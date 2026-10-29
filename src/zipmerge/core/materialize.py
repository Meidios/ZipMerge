"""把虚拟目标树物化为普通目录，供资源管理器拖出与导出暂存复用。"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from ..engine import ArchiveCancelled, ArchiveError, extract_entries
from ..util.paths import long_path
from .conflict import detect_conflicts
from .exporter import ExportCancelled, ExportFailure, ProgressFn, _Reporter
from .nodes import NodeKind, TargetNode
from .plan import Mapping, build_export_plan
from .session import SourceArchive


@dataclass(frozen=True)
class MaterializeRequest:
    """一次目录物化请求；``destination`` 必须是本次操作的独立目录。"""

    root: TargetNode
    sources: dict[str, SourceArchive]
    destination: Path


@dataclass(frozen=True)
class MaterializeResult:
    destination: Path
    paths: tuple[Path, ...]
    file_count: int
    dir_count: int


def top_level_nodes(nodes: list[TargetNode]) -> list[TargetNode]:
    """去重并移除已被另一选中目录覆盖的后代节点。"""
    unique: list[TargetNode] = []
    seen: set[int] = set()
    for node in nodes:
        if node.uid not in seen:
            seen.add(node.uid)
            unique.append(node)
    selected = set(unique)
    return [
        node for node in unique
        if not any(parent in selected for parent in _parents(node))
    ]


def build_target_drag_root(nodes: list[TargetNode]) -> TargetNode:
    root = TargetNode(name="", is_dir=True, kind=NodeKind.VIRTUAL_DIR)
    for node in top_level_nodes(nodes):
        root.add(node.clone())
    return root


def build_source_drag_root(
    nodes: list[TargetNode], sources: dict[str, SourceArchive]
) -> TargetNode:
    """按整包/单包部分/跨包规则构建源树拖出结构。"""
    chosen = top_level_nodes(nodes)
    root = TargetNode(name="", is_dir=True, kind=NodeKind.VIRTUAL_DIR)
    grouped: dict[str, list[TargetNode]] = {}
    for node in chosen:
        ref = node.source
        if ref is not None and ref.source_id:
            grouped.setdefault(ref.source_id, []).append(node)
    if len(grouped) <= 1:
        for node in chosen:
            root.add(node.clone())
        return root

    used: set[str] = set()
    for source_id, selected in grouped.items():
        source = sources.get(source_id)
        base = source.display_name if source is not None else source_id
        name = _unique_top_name(base, used)
        wrapper = TargetNode(name=name, is_dir=True, kind=NodeKind.VIRTUAL_DIR)
        # 整包根已自带同名包装，跨源时只取它的内容，避免双层包名。
        if any(node.parent is None for node in selected):
            whole = next(node for node in selected if node.parent is None)
            for child in whole.children:
                wrapper.add(child.clone())
        else:
            for node in selected:
                wrapper.add(node.clone())
        root.add(wrapper)
    return root


def _parents(node: TargetNode):
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def _unique_top_name(base: str, used: set[str]) -> str:
    candidate = base
    index = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def materialize_directory(
    request: MaterializeRequest,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> MaterializeResult:
    """将请求树写到目录；失败或取消时移除本次候选目录。"""
    reporter = _Reporter(on_progress, cancel)
    destination = Path(request.destination)
    raw_root = destination / ".zipmerge-raw"
    reporter.check_cancelled()
    try:
        mappings = build_export_plan(request.root, expand_disk_dirs=True)
    except OSError as exc:
        raise ExportFailure(str(exc)) from exc
    if not mappings:
        raise ExportFailure("没有可拖出的内容")
    folded: dict[str, Mapping] = {}
    path_collisions: list[str] = []
    for item in mappings:
        key = item.target_path.casefold()
        previous = folded.get(key)
        if previous is not None:
            path_collisions.append(item.target_path)
        else:
            folded[key] = item
    conflicts = detect_conflicts(mappings)
    if conflicts or path_collisions:
        unsafe_paths = path_collisions + [item.target_path for item in conflicts]
        unsafe_paths = list(dict.fromkeys(unsafe_paths))
        paths = "\n".join(f"• {path}" for path in unsafe_paths[:12])
        more = f"\n另有 {len(unsafe_paths) - 12} 项" if len(unsafe_paths) > 12 else ""
        raise ExportFailure(f"所选内容存在不安全的同名或文件/目录冲突：\n{paths}{more}")

    try:
        if destination.exists():
            shutil.rmtree(long_path(destination))
        destination.mkdir(parents=True)
        for item in mappings:
            if item.is_dir:
                (destination / item.target_path).mkdir(parents=True, exist_ok=True)

        archive_items = [
            item for item in mappings
            if not item.is_dir and item.kind is NodeKind.ARCHIVE_ENTRY
        ]
        by_source: dict[str, list[Mapping]] = {}
        for item in archive_items:
            ref = item.node.source
            if ref is not None and ref.source_id:
                by_source.setdefault(ref.source_id, []).append(item)

        source_count = max(1, len(by_source))
        for position, (source_id, items) in enumerate(sorted(by_source.items())):
            reporter.check_cancelled()
            source = request.sources.get(source_id)
            if source is None:
                raise ExportFailure(f"找不到源压缩包记录：{source_id}")
            start = int(60 * position / source_count)
            end = int(60 * (position + 1) / source_count)
            emit = reporter.segment(start, end)
            output = raw_root / source_id
            try:
                extract_entries(
                    source.archive_path,
                    [item.node.source.entry_path for item in items if item.node.source],
                    output,
                    password=source.password,
                    on_progress=lambda pct, _text, e=emit, name=source.archive_path.name: e(
                        pct, f"正在准备 {name}…"
                    ),
                    cancel=cancel,
                )
            except ArchiveCancelled as exc:
                raise ExportCancelled() from exc
            except ArchiveError as exc:
                raise ExportFailure(
                    f"提取 {source.archive_path.name} 失败：{exc.message}", detail=exc.output
                ) from exc

        layout_items = [
            item for item in mappings
            if item.kind in {NodeKind.ARCHIVE_ENTRY, NodeKind.DISK_FILE}
            and not item.is_dir
        ]
        total = max(1, len(layout_items))
        emit = reporter.segment(60, 99)
        for index, item in enumerate(layout_items):
            reporter.check_cancelled()
            target = destination / item.target_path
            ref = item.node.source
            if item.kind is NodeKind.ARCHIVE_ENTRY:
                if ref is None or not ref.source_id or not ref.entry_path:
                    continue
                raw = raw_root / ref.source_id / ref.entry_path
                if not raw.is_file():
                    raise ExportFailure(f"源包中缺少条目：{ref.entry_path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(long_path(raw), long_path(target))
            elif item.kind is NodeKind.DISK_FILE:
                source_path = Path(ref.disk_path) if ref and ref.disk_path else None
                if source_path is None or not source_path.is_file():
                    raise ExportFailure(f"磁盘文件不存在：{source_path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(long_path(source_path), long_path(target))
            emit(int(100 * (index + 1) / total), f"正在整理 {item.target_path}")

        shutil.rmtree(long_path(raw_root), ignore_errors=True)
        reporter.report(100, "拖出内容已准备完成")
        paths = tuple(destination / child.name for child in request.root.children)
        return MaterializeResult(
            destination=destination,
            paths=paths,
            file_count=sum(not item.is_dir for item in mappings),
            dir_count=sum(item.is_dir for item in mappings),
        )
    except (ExportCancelled, ExportFailure):
        shutil.rmtree(long_path(destination), ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(long_path(destination), ignore_errors=True)
        raise ExportFailure(f"无法准备拖出内容：{exc}") from exc
