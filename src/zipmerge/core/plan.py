"""导出计划：把目标树展开为「来源 → 目标相对路径」的映射列表。"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from .nodes import NodeKind, SourceRef, TargetNode


@dataclass(frozen=True)
class Mapping:
    """一条导出映射。目录映射用于保证空目录被创建。"""

    node: TargetNode
    target_path: str
    is_dir: bool

    @property
    def kind(self) -> NodeKind:
        return self.node.kind

    @property
    def source_id(self) -> str | None:
        return self.node.source.source_id if self.node.source else None

    def with_path(self, target_path: str) -> Mapping:
        return replace(self, target_path=target_path)


def _disk_directory_mappings(node: TargetNode, target_path: str) -> list[Mapping]:
    """把磁盘文件夹展开为可参与计数、冲突检测和复制的普通映射。"""
    raw = node.source.disk_path if node.source is not None else None
    source = Path(raw) if raw else None
    if source is None or not source.is_dir():
        raise OSError(f"磁盘文件夹不存在：{source}")

    root_node = TargetNode(name=node.name, is_dir=True, kind=NodeKind.VIRTUAL_DIR)
    result = [Mapping(root_node, target_path, True)]

    def fail(error: OSError) -> None:
        raise error

    for current_raw, dirnames, filenames in os.walk(source, onerror=fail, followlinks=False):
        current = Path(current_raw)
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        relative = current.relative_to(source)
        prefix_parts = () if relative == Path(".") else relative.parts

        for name in dirnames:
            disk_path = current / name
            if disk_path.is_symlink():
                continue
            mapped = "\\".join((target_path, *prefix_parts, name))
            directory = TargetNode(name=name, is_dir=True, kind=NodeKind.VIRTUAL_DIR)
            result.append(Mapping(directory, mapped, True))

        for name in filenames:
            disk_path = current / name
            if disk_path.is_symlink():
                continue
            mapped = "\\".join((target_path, *prefix_parts, name))
            file_node = TargetNode(
                name=name,
                is_dir=False,
                kind=NodeKind.DISK_FILE,
                source=SourceRef(kind=NodeKind.DISK_FILE, disk_path=str(disk_path)),
            )
            result.append(Mapping(file_node, mapped, False))
    return result


def build_export_plan(root: TargetNode, *, expand_disk_dirs: bool = False) -> list[Mapping]:
    """按树的先后顺序生成映射；导出预检可展开磁盘文件夹内容。"""
    mappings: list[Mapping] = []

    def visit(node: TargetNode) -> None:
        for child in node.children:
            target_path = child.path()
            if expand_disk_dirs and child.kind is NodeKind.DISK_DIR:
                mappings.extend(_disk_directory_mappings(child, target_path))
            else:
                mappings.append(Mapping(node=child, target_path=target_path, is_dir=child.is_dir))
            if child.children:
                visit(child)

    visit(root)
    return mappings


def plan_file_count(mappings: list[Mapping]) -> int:
    return sum(1 for item in mappings if not item.is_dir)
