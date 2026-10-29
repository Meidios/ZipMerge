"""解压选择归一化、输出规划与落地前冲突检查。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Iterable, Literal

from ..util.paths import normalize_entry_path, safe_relative_path
from .session import SourceArchive

OverwriteMode = Literal["overwrite", "skip"]


@dataclass(frozen=True)
class ExtractionSelection:
    """源包树中的一个选择；空条目路径表示整个压缩包。"""

    source_id: str
    entry_path: str = ""


@dataclass(frozen=True)
class ExtractionJob:
    """对一个源压缩包执行的一次 7-Zip 解压。"""

    source_id: str
    archive_path: Path
    archive_name: str
    paths: tuple[str, ...] | None
    output_dir: Path
    password: str | None
    file_targets: tuple[Path, ...]
    dir_targets: tuple[Path, ...]


@dataclass(frozen=True)
class ExtractionPlan:
    destination: Path
    jobs: tuple[ExtractionJob, ...]

    @property
    def file_count(self) -> int:
        return sum(len(job.file_targets) for job in self.jobs)

    @property
    def dir_count(self) -> int:
        keys = {str(path).casefold() for job in self.jobs for path in job.dir_targets}
        return len(keys)


@dataclass(frozen=True)
class ExtractionConflicts:
    """解压前可处理的已有文件，以及必须中止的结构冲突。"""

    existing_files: tuple[Path, ...] = ()
    structural: tuple[str, ...] = ()
    internal: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return bool(self.structural or self.internal)


def _canonical_paths(paths: Iterable[str]) -> tuple[str, ...] | None:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = normalize_entry_path(raw)
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    if any(not path for path in normalized):
        return None

    result: list[str] = []
    for path in normalized:
        key = path.casefold()
        if any(key.startswith(parent.casefold() + "\\") for parent in normalized if parent != path):
            continue
        result.append(path)
    return tuple(result)


def _selected_entries(source: SourceArchive, paths: tuple[str, ...] | None):
    if paths is None:
        return list(source.listing.entries)
    prefixes = tuple(path.casefold() for path in paths)
    return [
        entry
        for entry in source.listing.entries
        if any(
            entry.path.casefold() == prefix
            or entry.path.casefold().startswith(prefix + "\\")
            for prefix in prefixes
        )
    ]


def _unique_folder_name(name: str, used: set[str]) -> str:
    base = name or "压缩包"
    candidate = base
    index = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def build_extraction_plan(
    sources: dict[str, SourceArchive],
    selections: Iterable[ExtractionSelection],
    destination: str | Path,
    *,
    wrap_whole_archives: bool = True,
) -> ExtractionPlan:
    """把源包树选择转换为稳定、可预检的逐源解压任务。"""
    grouped: dict[str, list[str]] = {}
    for selection in selections:
        if selection.source_id not in sources:
            continue
        grouped.setdefault(selection.source_id, []).append(selection.entry_path)
    if not grouped:
        raise ValueError("没有可解压的源包或条目")

    root = Path(destination).resolve()
    multiple_sources = len(grouped) > 1
    used_folders: set[str] = set()
    jobs: list[ExtractionJob] = []
    for source_id, raw_paths in grouped.items():
        source = sources[source_id]
        paths = _canonical_paths(raw_paths)
        wrapped = multiple_sources or (paths is None and wrap_whole_archives)
        output_dir = root
        if wrapped:
            output_dir /= _unique_folder_name(source.display_name, used_folders)

        entries = _selected_entries(source, paths)
        files: list[Path] = []
        dirs: set[Path] = set()
        if wrapped:
            dirs.add(output_dir)
        for entry in entries:
            target = output_dir / safe_relative_path(entry.path)
            if entry.is_dir:
                dirs.add(target)
            else:
                files.append(target)
                parent = target.parent
                while parent != output_dir and output_dir in parent.parents:
                    dirs.add(parent)
                    parent = parent.parent
        jobs.append(
            ExtractionJob(
                source_id=source_id,
                archive_path=source.archive_path,
                archive_name=source.archive_path.name,
                paths=paths,
                output_dir=output_dir,
                password=source.password,
                file_targets=tuple(files),
                dir_targets=tuple(sorted(dirs, key=lambda value: str(value).casefold())),
            )
        )
    return ExtractionPlan(root, tuple(jobs))


def inspect_extraction_conflicts(plan: ExtractionPlan) -> ExtractionConflicts:
    """按 Windows 大小写不敏感语义检查计划内部与磁盘现状。"""
    expected: dict[str, tuple[Path, bool]] = {}
    internal: list[str] = []
    for job in plan.jobs:
        targets = chain(
            ((path, True) for path in job.dir_targets),
            ((path, False) for path in job.file_targets),
        )
        for path, is_dir in targets:
            key = str(path).casefold()
            previous = expected.get(key)
            if previous is not None and (previous[0] != path or previous[1] != is_dir or not is_dir):
                internal.append(str(path))
            else:
                expected[key] = (path, is_dir)

    existing: list[Path] = []
    structural: list[str] = []
    if plan.destination.exists() and not plan.destination.is_dir():
        structural.append(f"目标路径不是文件夹：{plan.destination}")
    elif plan.destination.exists() and not os.access(plan.destination, os.W_OK):
        structural.append(f"目标文件夹不可写：{plan.destination}")
    for path, is_dir in expected.values():
        if path.exists():
            if is_dir and not path.is_dir():
                structural.append(f"需要文件夹，但已有文件：{path}")
            elif not is_dir and path.is_dir():
                structural.append(f"需要文件，但已有文件夹：{path}")
            elif not is_dir:
                existing.append(path)
        parent = path.parent
        while parent != plan.destination and plan.destination in parent.parents:
            if parent.exists() and not parent.is_dir():
                structural.append(f"父路径不是文件夹：{parent}")
                break
            parent = parent.parent

    return ExtractionConflicts(
        existing_files=tuple(sorted(set(existing), key=lambda value: str(value).casefold())),
        structural=tuple(dict.fromkeys(structural)),
        internal=tuple(dict.fromkeys(internal)),
    )
