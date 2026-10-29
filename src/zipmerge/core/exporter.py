"""导出链路：映射 → 冲突解决 → 分源提取 → 结构落地 → 打包 → 清理。

对应 PLAN 7.6 的七步流程。整个函数不依赖 Qt，可被单元测试直接调用；
UI 只需把它放进 QThread 并传入进度回调与取消事件。
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from ..engine import ArchiveCancelled, ArchiveError, create_archive, extract_entries
from ..util.paths import long_path
from .conflict import Conflict, ResolveStrategy, resolve_mappings
from .nodes import NodeKind, TargetNode
from .plan import Mapping, build_export_plan
from .session import SourceArchive

ProgressFn = Callable[[int, str], None]

# 阶段占用的进度区间（百分比）
_STAGE_PLAN = 2
_STAGE_EXTRACT_END = 60
_STAGE_LAYOUT_END = 82


class ExportCancelled(Exception):
    """用户取消导出。"""


@dataclass
class ExportOptions:
    output_path: Path
    fmt: str = "zip"
    level: int = 5
    password: str | None = None
    strategy: ResolveStrategy = ResolveStrategy.RENAME
    overrides: dict[str, ResolveStrategy] = field(default_factory=dict)
    stage_dir: Path | None = None
    clean_stage: bool = True


@dataclass
class ExportResult:
    output_path: Path
    file_count: int
    dir_count: int
    renamed: int
    skipped: int
    overwritten: int
    conflicts: list[Conflict]
    mapping: list[Mapping]


class ExportFailure(Exception):
    """导出失败，携带可读原因与原始 7-Zip 输出。"""

    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


class _Reporter:
    """把各阶段进度线性映射到 0-100。"""

    def __init__(
        self,
        callback: ProgressFn | None,
        cancel: threading.Event | None,
    ) -> None:
        self._callback = callback
        self._cancel = cancel
        self._last = -1

    def report(self, percent: int, message: str) -> None:
        percent = max(0, min(100, int(percent)))
        self._last = percent
        if self._callback is not None:
            self._callback(percent, message)

    def check_cancelled(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise ExportCancelled()

    def segment(self, start: int, end: int) -> Callable[[int, str], None]:
        span = max(1, end - start)

        def emit(inner_percent: int, message: str) -> None:
            self.check_cancelled()
            self.report(start + int(span * max(0, min(100, inner_percent)) / 100), message)

        return emit


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _copy_disk_file(source: Path, target: Path) -> None:
    _ensure_dir(target.parent)
    shutil.copy2(long_path(source), long_path(target))


def build_resolution(root: TargetNode, options: ExportOptions):
    """生成最终映射与冲突清单（导出前的预览 / 询问环节也用它）。"""
    mappings = build_export_plan(root, expand_disk_dirs=True)
    return mappings, resolve_mappings(mappings, options.strategy, options.overrides)


def run_export(
    root: TargetNode,
    sources: dict[str, SourceArchive],
    options: ExportOptions,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> ExportResult:
    """事务化导出：候选包成功后才原子替换最终文件。"""
    final_path = Path(options.output_path)
    stage = Path(options.stage_dir) if options.stage_dir else final_path.parent / ".zipmerge-stage"
    candidate = final_path.parent / f".{final_path.name}.zipmerge-{uuid4().hex}.tmp"
    inner_options = replace(
        options,
        output_path=candidate,
        stage_dir=stage,
        clean_stage=False,
    )
    try:
        result = _run_export_inner(root, sources, inner_options, on_progress, cancel)
        if cancel is not None and cancel.is_set():
            raise ExportCancelled()
        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(long_path(candidate), long_path(final_path))
        except OSError as exc:
            raise ExportFailure(f"无法替换目标压缩包：{exc}") from exc
        result.output_path = final_path
        return result
    finally:
        try:
            if candidate.exists():
                candidate.unlink()
        except OSError:
            pass
        if options.clean_stage:
            shutil.rmtree(long_path(stage), ignore_errors=True)


def _run_export_inner(
    root: TargetNode,
    sources: dict[str, SourceArchive],
    options: ExportOptions,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> ExportResult:
    """执行暂存和候选包构建；最终文件提交由 :func:`run_export` 负责。"""
    reporter = _Reporter(on_progress, cancel)
    reporter.check_cancelled()

    stage = Path(options.stage_dir) if options.stage_dir else options.output_path.parent / ".zipmerge-stage"
    raw_root = stage / "raw"
    final_root = stage / "final"

    # 1) 计划与冲突解决
    reporter.report(0, "正在生成导出计划…")
    try:
        mappings = build_export_plan(root, expand_disk_dirs=True)
    except OSError as exc:
        raise ExportFailure(str(exc)) from exc
    resolution = resolve_mappings(mappings, options.strategy, options.overrides)
    final_mappings = resolution.mappings
    reporter.report(_STAGE_PLAN, "导出计划完成")

    if not final_mappings:
        raise ExportFailure("目标结构是空的，没有可导出的内容")

    # 2) 清空并预建暂存区（含全部目录，保证空目录被保留）
    try:
        if stage.exists():
            shutil.rmtree(long_path(stage), ignore_errors=True)
        _ensure_dir(final_root)
        for item in final_mappings:
            if item.is_dir and item.kind is not NodeKind.DISK_DIR:
                _ensure_dir(final_root / item.target_path)
    except OSError as exc:
        raise ExportFailure(f"无法准备暂存区：{exc}") from exc

    file_mappings = [item for item in final_mappings if not item.is_dir]
    archive_items = [item for item in file_mappings if item.kind is NodeKind.ARCHIVE_ENTRY]
    disk_items = [item for item in file_mappings if item.kind is NodeKind.DISK_FILE]

    # 3) 分源提取（每个源一次 7-Zip 调用）
    by_source: dict[str, list[Mapping]] = {}
    for item in archive_items:
        source_id = item.node.source.source_id if item.node.source else None
        if source_id is None:
            continue
        by_source.setdefault(source_id, []).append(item)

    total_sources = max(1, len(by_source))
    span = _STAGE_EXTRACT_END - _STAGE_PLAN
    for position, (source_id, items) in enumerate(sorted(by_source.items())):
        source = sources.get(source_id)
        if source is None:
            raise ExportFailure(f"找不到源压缩包记录：{source_id}")
        segment_start = _STAGE_PLAN + int(span * position / total_sources)
        segment_end = _STAGE_PLAN + int(span * (position + 1) / total_sources)
        emit = reporter.segment(segment_start, segment_end)
        emit(0, f"正在提取 {source.archive_path.name}…")
        out_dir = raw_root / source_id
        try:
            extract_entries(
                source.archive_path,
                [item.node.source.entry_path for item in items if item.node.source],
                out_dir,
                password=source.password,
                on_progress=lambda pct, _text, e=emit, name=source.archive_path.name: e(
                    pct, f"正在提取 {name}…"
                ),
                cancel=cancel,
            )
        except ArchiveCancelled as exc:
            raise ExportCancelled() from exc
        except ArchiveError as exc:
            raise ExportFailure(
                f"提取 {source.archive_path.name} 失败：{exc.message}", detail=exc.output
            ) from exc
        reporter.check_cancelled()
        emit(100, f"已提取 {source.archive_path.name}")

    # 4) 结构落地：raw 中移动 / 改名到 final；磁盘内容直接复制
    layout_items = archive_items + disk_items
    total_layout = max(1, len(layout_items))
    emit = reporter.segment(_STAGE_EXTRACT_END, _STAGE_LAYOUT_END)
    for index, item in enumerate(layout_items):
        reporter.check_cancelled()
        target = final_root / item.target_path
        if item.kind is NodeKind.ARCHIVE_ENTRY:
            source_ref = item.node.source
            if source_ref is None or source_ref.source_id is None or source_ref.entry_path is None:
                continue
            raw_file = raw_root / source_ref.source_id / source_ref.entry_path
            if not raw_file.exists():
                raise ExportFailure(
                    f"源包中缺少条目：{source_ref.entry_path}（{item.kind.value}）"
                )
            _ensure_dir(target.parent)
            if target.exists():
                target.unlink()
            os.replace(long_path(raw_file), long_path(target))
        elif item.kind is NodeKind.DISK_FILE:
            source_path = Path(item.node.source.disk_path) if item.node.source else None
            if source_path is None or not source_path.exists():
                raise ExportFailure(f"磁盘文件不存在：{source_path}")
            _copy_disk_file(source_path, target)
        emit(int(100 * (index + 1) / total_layout), f"正在整理 {item.target_path}")

    # 5) 打包
    reporter.report(_STAGE_LAYOUT_END, "正在打包…")
    pack_emit = reporter.segment(_STAGE_LAYOUT_END, 99)
    output_path = Path(options.output_path)
    _ensure_dir(output_path.parent)
    try:
        create_archive(
            output_path,
            options.fmt,
            final_root,
            level=options.level,
            password=options.password,
            on_progress=lambda pct, text: pack_emit(pct, "正在打包…"),
            cancel=cancel,
        )
    except ArchiveCancelled as exc:
        raise ExportCancelled() from exc
    except ArchiveError as exc:
        raise ExportFailure(f"打包失败：{exc.message}", detail=exc.output) from exc

    reporter.check_cancelled()
    reporter.report(100, "导出完成")

    result = ExportResult(
        output_path=output_path,
        file_count=len(file_mappings),
        dir_count=sum(1 for item in final_mappings if item.is_dir),
        renamed=resolution.renamed,
        skipped=resolution.skipped,
        overwritten=resolution.overwritten,
        conflicts=resolution.conflicts,
        mapping=final_mappings,
    )

    return result


def estimate_export_summary(
    root: TargetNode, sources: dict[str, SourceArchive]
) -> tuple[int, int, int]:
    """返回展开磁盘文件夹后的 ``(文件数, 文件夹数, 字节数)``。"""
    total = 0
    try:
        mappings = build_export_plan(root, expand_disk_dirs=True)
    except OSError:
        mappings = build_export_plan(root)
    for item in mappings:
        if item.is_dir:
            continue
        node = item.node
        if node.kind is NodeKind.ARCHIVE_ENTRY and node.source and node.source.source_id:
            source = sources.get(node.source.source_id)
            if source is not None:
                entry = source.entry(node.source.entry_path or "")
                if entry is not None:
                    total += entry.size
        elif node.kind is NodeKind.DISK_FILE and node.source and node.source.disk_path:
            try:
                total += Path(node.source.disk_path).stat().st_size
            except OSError:
                continue
    return (
        sum(not item.is_dir for item in mappings),
        sum(item.is_dir for item in mappings),
        total,
    )


def estimate_export_size(root: TargetNode, sources: dict[str, SourceArchive]) -> int:
    """粗略估算导出素材体积（用于确认对话框）。"""
    return estimate_export_summary(root, sources)[2]
