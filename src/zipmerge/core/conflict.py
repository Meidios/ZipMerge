"""导出冲突检测与解决策略。

冲突来源有两类：

1. **同路径重复**：两个条目要写进同一个目标路径；
2. **文件与目录同名**：某个文件的目标路径同时也是另一条目的目录路径。

目录与目录同名不算冲突 —— 虚拟目录天然可以合并。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .plan import Mapping


class ResolveStrategy(str, Enum):
    RENAME = "rename"  # 自动改名 name_1
    OVERWRITE = "overwrite"  # 后到者覆盖先到者
    SKIP = "skip"  # 保留先到者，跳过后来者


class ConflictKind(str, Enum):
    DUPLICATE = "duplicate"  # 同一路径多个条目
    PATH_CLASH = "path_clash"  # 文件与目录路径冲突


@dataclass
class Conflict:
    target_path: str
    kind: ConflictKind
    first: Mapping | None
    incoming: Mapping
    resolved_path: str | None = None

    @property
    def description(self) -> str:
        if self.kind is ConflictKind.PATH_CLASH:
            return "文件与文件夹路径冲突"
        return "路径重复"


@dataclass
class Resolution:
    mappings: list[Mapping] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    skipped: int = 0
    overwritten: int = 0
    renamed: int = 0


def _split_name(path: str) -> tuple[str, str, str]:
    if "\\" in path:
        head, tail = path.rsplit("\\", 1)
        prefix = head + "\\"
    else:
        prefix, tail = "", path
    if "." in tail[1:]:
        stem, ext = tail.rsplit(".", 1)
        return prefix, stem, "." + ext
    return prefix, tail, ""


def _unique_name(path: str, taken: set[str]) -> str:
    prefix, stem, ext = _split_name(path)
    index = 1
    while True:
        candidate = f"{prefix}{stem}_{index}{ext}"
        if candidate.casefold() not in taken:
            return candidate
        index += 1


def resolve_mappings(
    mappings: list[Mapping],
    strategy: ResolveStrategy = ResolveStrategy.RENAME,
    overrides: dict[str, ResolveStrategy] | None = None,
) -> Resolution:
    """按策略生成最终映射，并返回全部冲突记录。

    ``overrides`` 以目标路径为键，对个别冲突单独指定策略。
    ``OVERWRITE`` 只对文件→文件生效；涉及目录时自动退回改名，避免把目录子树删掉。
    """
    overrides = overrides or {}
    result = Resolution()

    # 目录路径先全部登记，文件与目录同名即可识别为冲突。
    taken: set[str] = set()
    dir_paths: set[str] = set()
    for item in mappings:
        if item.is_dir:
            key = item.target_path.casefold()
            dir_paths.add(key)
            taken.add(key)

    file_index: dict[str, int] = {}  # casefold 路径 → result.mappings 下标

    for item in mappings:
        if item.is_dir:
            result.mappings.append(item)
            continue

        key = item.target_path.casefold()
        clash_kind: ConflictKind | None = None
        first: Mapping | None = None

        if key in dir_paths:
            clash_kind = ConflictKind.PATH_CLASH
        elif key in file_index:
            clash_kind = ConflictKind.DUPLICATE
            first = result.mappings[file_index[key]]

        if clash_kind is None:
            taken.add(key)
            file_index[key] = len(result.mappings)
            result.mappings.append(item)
            continue

        chosen = overrides.get(item.target_path, strategy)
        conflict = Conflict(
            target_path=item.target_path,
            kind=clash_kind,
            first=first,
            incoming=item,
        )

        if chosen is ResolveStrategy.OVERWRITE and clash_kind is ConflictKind.DUPLICATE:
            # 后到者覆盖先到者：移除先前映射，占用其位置。
            previous_index = file_index[key]
            displaced = result.mappings[previous_index]
            result.mappings[previous_index] = item
            result.overwritten += 1
            conflict.first = displaced
            conflict.resolved_path = item.target_path
        elif chosen is ResolveStrategy.SKIP:
            result.skipped += 1
            conflict.resolved_path = None
        else:
            renamed = _unique_name(item.target_path, taken)
            taken.add(renamed.casefold())
            renamed_item = item.with_path(renamed)
            file_index[renamed.casefold()] = len(result.mappings)
            result.mappings.append(renamed_item)
            result.renamed += 1
            conflict.resolved_path = renamed

        result.conflicts.append(conflict)

    return result


def detect_conflicts(mappings: list[Mapping]) -> list[Conflict]:
    """只做冲突检测。冲突集合与策略无关（改名不会制造新冲突）。"""
    return resolve_mappings(mappings, ResolveStrategy.RENAME).conflicts
