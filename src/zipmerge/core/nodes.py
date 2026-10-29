"""目标树节点模型。

目标树是**纯虚拟结构**：节点只记录来源引用，不复制任何文件数据。
因此改名、删除、拖拽都是零成本操作，可无限次调整。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

_UID = itertools.count(1)

# Windows 文件名非法字符（与 util.paths.sanitize_filename 保持一致）
ILLEGAL_NAME_CHARS = '\\/:*?"<>|'
MAX_NAME_LENGTH = 255


def next_uid() -> int:
    return next(_UID)


class NodeKind(str, Enum):
    """节点的来源类型（四态）。"""

    VIRTUAL_DIR = "virtual_dir"  # 用户新建的虚拟文件夹
    ARCHIVE_ENTRY = "archive_entry"  # 来自源压缩包内部条目
    DISK_FILE = "disk_file"  # 来自磁盘散文件
    DISK_DIR = "disk_dir"  # 来自磁盘文件夹，导出时整目录原样复制


@dataclass(frozen=True)
class SourceRef:
    """节点的来源引用。"""

    kind: NodeKind
    source_id: str | None = None  # 源包 ID（同一会话内唯一）
    archive_path: str | None = None  # 源包在磁盘上的路径
    entry_path: str | None = None  # 包内路径（反斜杠）
    disk_path: str | None = None  # 磁盘路径（散文件或文件夹）


@dataclass(eq=False)
class TargetNode:
    """目标树节点。相等性按身份判定，便于在视图与撤销栈中安全引用。"""

    name: str
    is_dir: bool
    kind: NodeKind = NodeKind.VIRTUAL_DIR
    source: SourceRef | None = None
    children: list[TargetNode] = field(default_factory=list)
    uid: int = field(default_factory=next_uid)
    parent: TargetNode | None = field(default=None, repr=False, compare=False)

    # ---- 树结构操作 -------------------------------------------------

    def add(self, node: TargetNode, index: int | None = None) -> None:
        if index is None or index < 0 or index > len(self.children):
            index = len(self.children)
        self.children.insert(index, node)
        node.parent = self

    def extend(self, nodes: list[TargetNode], index: int | None = None) -> None:
        if index is None:
            index = len(self.children)
        for offset, node in enumerate(nodes):
            self.add(node, index + offset)

    def remove(self, node: TargetNode) -> None:
        try:
            self.children.remove(node)
        except ValueError:
            return
        node.parent = None

    def index_of(self, node: TargetNode) -> int:
        return self.children.index(node)

    def is_ancestor_of(self, node: TargetNode) -> bool:
        current = node.parent
        while current is not None:
            if current is self:
                return True
            current = current.parent
        return False

    # ---- 查询 -------------------------------------------------------

    def path(self) -> str:
        """相对根节点的目标路径；根节点自身返回空串。"""
        parts: list[str] = []
        current: TargetNode | None = self
        while current is not None and current.parent is not None:
            parts.append(current.name)
            current = current.parent
        return "\\".join(reversed(parts))

    def walk(self, include_self: bool = True) -> Iterator[TargetNode]:
        if include_self:
            yield self
        for child in list(self.children):
            yield from child.walk(include_self=True)

    def find_by_uid(self, uid: int) -> TargetNode | None:
        for node in self.walk(include_self=True):
            if node.uid == uid:
                return node
        return None

    def iter_files(self) -> Iterator[TargetNode]:
        for node in self.walk(include_self=True):
            if not node.is_dir:
                yield node

    def descendant_count(self) -> int:
        return sum(1 for _ in self.walk(include_self=False))

    def total_file_count(self) -> int:
        return sum(1 for _ in self.iter_files())

    # ---- 复制 -------------------------------------------------------

    def clone(self) -> TargetNode:
        """深拷贝（含子节点），所有节点获得新的 uid 与独立的 parent 链。"""
        copy = TargetNode(
            name=self.name,
            is_dir=self.is_dir,
            kind=self.kind,
            source=self.source,
        )
        for child in self.children:
            copy.add(child.clone())
        return copy

    # ---- 展示辅助 ---------------------------------------------------

    @property
    def source_label(self) -> str:
        if self.source is None or self.source.source_id is None:
            return "本地"
        return self.source.source_id

    @property
    def is_from_archive(self) -> bool:
        return self.kind is NodeKind.ARCHIVE_ENTRY


def new_root() -> TargetNode:
    """根节点：不可见、不可改名，承载一次合并会话的目标结构。"""
    return TargetNode(name="", is_dir=True, kind=NodeKind.VIRTUAL_DIR)


def make_virtual_dir(name: str) -> TargetNode:
    return TargetNode(name=name, is_dir=True, kind=NodeKind.VIRTUAL_DIR)


def validate_name(name: str) -> str | None:
    """校验节点名，合法返回 None，否则返回中文原因。"""
    if not name or not name.strip():
        return "名称不能为空"
    stripped = name.strip()
    if stripped in (".", ".."):
        return "名称不能是 . 或 .."
    bad = sorted({ch for ch in stripped if ch in ILLEGAL_NAME_CHARS or ord(ch) < 32})
    if bad:
        return "名称不能包含 " + " ".join(bad)
    if stripped.endswith((".", " ")):
        return "名称不能以空格或点结尾"
    if len(stripped) > MAX_NAME_LENGTH:
        return f"名称过长（上限 {MAX_NAME_LENGTH} 字符）"
    return None


def unique_child_name(parent: TargetNode, desired: str) -> str:
    """在父节点下生成不重复的虚拟目录名（用于「新建文件夹」）。"""
    existing = {child.name for child in parent.children}
    if desired not in existing:
        return desired
    base = desired
    # 已有「新建文件夹」时递增后缀
    while base and base[-1].isdigit():
        base = base[:-1]
    base = base or desired
    index = 2
    while f"{base}{index}" in existing:
        index += 1
    return f"{base}{index}"

