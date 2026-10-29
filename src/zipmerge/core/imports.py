"""导入语义：压缩包 → 包名文件夹子树；磁盘文件 / 文件夹 → 散节点。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..engine import ArchiveEntry
from ..util.paths import sanitize_filename
from .nodes import NodeKind, SourceRef, TargetNode


def archive_display_name(archive_path: str | Path) -> str:
    """压缩包在目标树中的包名文件夹名（去掉扩展名）。"""
    stem = Path(archive_path).name
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    else:
        stem = Path(stem).stem or stem
    return sanitize_filename(stem) or "压缩包"


def _relative(entry_path: str, prefix: str) -> str | None:
    if not prefix:
        return entry_path
    prefix = prefix.strip("\\")
    if entry_path == prefix:
        return None
    marker = prefix + "\\"
    if entry_path.startswith(marker):
        return entry_path[len(marker) :]
    return None


def build_entry_subtree(
    entries: list[ArchiveEntry],
    *,
    source_id: str,
    archive_path: str,
    prefix: str = "",
) -> list[TargetNode]:
    """把包内条目（可选地只取某个子目录）还原成一层层的目标树子树。

    目录条目缺失时按路径补齐中间目录，保证树结构连续。
    """
    container = TargetNode(name="", is_dir=True, kind=NodeKind.VIRTUAL_DIR)
    index: dict[str, TargetNode] = {}

    ordered = sorted(
        entries,
        key=lambda item: (item.path.count("\\"), item.path),
    )
    for entry in ordered:
        relative = _relative(entry.path, prefix)
        if not relative:
            continue
        parts = relative.split("\\")
        parent = container
        for depth in range(len(parts)):
            key = "\\".join(parts[: depth + 1])
            existing = index.get(key)
            if existing is not None:
                parent = existing
                continue
            is_last = depth == len(parts) - 1
            entry_is_dir = entry.is_dir if is_last else True
            archive_entry_path = entry.path if is_last else "\\".join(
                [prefix, key] if prefix else [key]
            )
            node = TargetNode(
                name=parts[depth],
                is_dir=entry_is_dir,
                kind=NodeKind.ARCHIVE_ENTRY,
                source=SourceRef(
                    kind=NodeKind.ARCHIVE_ENTRY,
                    source_id=source_id,
                    archive_path=archive_path,
                    entry_path=archive_entry_path,
                ),
            )
            parent.add(node)
            index[key] = node
            parent = node

    return container.children


def flatten_levels(node: TargetNode, levels: int | None = 0) -> list[TargetNode]:
    """把 node 替换为它的后代。

    ``levels=0`` 去掉 node 本身；每 +1 多去掉一层目录；``None`` 表示完全摊平。
    """
    result: list[TargetNode] = []

    def collect(current: TargetNode, depth: int | None) -> None:
        for child in current.children:
            if child.is_dir and child.children and (depth is None or depth > 0):
                collect(child, None if depth is None else depth - 1)
            else:
                result.append(child)

    collect(node, levels)
    return result


@dataclass
class DiskImport:
    """磁盘导入结果。"""

    nodes: list[TargetNode]
    skipped: list[str]


def build_disk_nodes(paths: list[str | Path]) -> DiskImport:
    """把磁盘散文件 / 文件夹构造成目标树节点。"""
    nodes: list[TargetNode] = []
    skipped: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            skipped.append(str(path))
            continue
        if path.is_dir():
            nodes.append(
                TargetNode(
                    name=sanitize_filename(path.name) or path.name,
                    is_dir=True,
                    kind=NodeKind.DISK_DIR,
                    source=SourceRef(kind=NodeKind.DISK_DIR, disk_path=str(path)),
                )
            )
        else:
            nodes.append(
                TargetNode(
                    name=sanitize_filename(path.name) or path.name,
                    is_dir=False,
                    kind=NodeKind.DISK_FILE,
                    source=SourceRef(kind=NodeKind.DISK_FILE, disk_path=str(path)),
                )
            )
    return DiskImport(nodes=nodes, skipped=skipped)

