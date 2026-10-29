"""目标树的原子编辑命令，供撤销 / 重做栈使用。

命令直接持有节点对象引用（节点身份稳定），因此撤销成本与树规模无关。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .nodes import TargetNode


class Command(ABC):
    """可撤销的原子操作。"""

    label: str = "操作"

    @abstractmethod
    def apply(self) -> None: ...

    @abstractmethod
    def revert(self) -> None: ...


@dataclass
class AddNodes(Command):
    """插入节点（新建文件夹、粘贴、导入）。"""

    parent: TargetNode
    nodes: list[TargetNode]
    index: int | None = None
    label = "添加"

    def apply(self) -> None:
        self.parent.extend(self.nodes, self.index)

    def revert(self) -> None:
        for node in self.nodes:
            if node.parent is not None:
                node.parent.remove(node)


@dataclass
class RemoveNodes(Command):
    """删除节点，撤销时按原索引还原。"""

    nodes: list[TargetNode]
    label = "删除"
    # (原父节点, 原索引, 节点)
    _origins: list[tuple[TargetNode, int, TargetNode]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._origins = [
            (node.parent, node.parent.index_of(node), node)
            for node in self.nodes
            if node.parent is not None
        ]

    def apply(self) -> None:
        for node in self.nodes:
            if node.parent is not None:
                node.parent.remove(node)

    def revert(self) -> None:
        grouped: dict[int, tuple[TargetNode, list[tuple[int, TargetNode]]]] = {}
        for parent, index, node in self._origins:
            grouped.setdefault(id(parent), (parent, []))[1].append((index, node))
        for parent, items in grouped.values():
            for index, node in sorted(items, key=lambda item: item[0]):
                parent.add(node, index)


@dataclass
class RenameNode(Command):
    """重命名节点。"""

    node: TargetNode
    new_name: str
    label = "重命名"
    _old_name: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self._old_name = self.node.name

    def apply(self) -> None:
        self.node.name = self.new_name

    def revert(self) -> None:
        self.node.name = self._old_name


@dataclass
class MoveNodes(Command):
    """把若干节点移动 / 重排到目标位置。

    ``index`` 是**移出这些节点之后**目标列表中的插入位置。
    """

    moves: list[tuple[TargetNode, TargetNode, int]]
    target: TargetNode
    index: int
    label = "移动"

    def apply(self) -> None:
        for node, _old_parent, _old_index in self.moves:
            if node.parent is not None:
                node.parent.remove(node)
        self.target.extend([node for node, _op, _oi in self.moves], self.index)

    def revert(self) -> None:
        for node, _old_parent, _old_index in self.moves:
            if node.parent is not None:
                node.parent.remove(node)
        grouped: dict[int, tuple[TargetNode, list[tuple[int, TargetNode]]]] = {}
        for node, old_parent, old_index in self.moves:
            grouped.setdefault(id(old_parent), (old_parent, []))[1].append((old_index, node))
        for old_parent, items in grouped.values():
            for old_index, node in sorted(items, key=lambda item: item[0]):
                old_parent.add(node, old_index)


@dataclass
class ReplaceNode(Command):
    """用一个节点替换为若干节点（摊平操作）。"""

    node: TargetNode
    replacements: list[TargetNode]
    label = "摊平"
    _parent: TargetNode | None = field(default=None, init=False)
    _index: int = field(default=0, init=False)
    # (原父节点, 原索引, 节点)：替换节点多来自被摊平节点的子树，撤销时需回到原位
    _origins: list[tuple[TargetNode, int, TargetNode]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._parent = self.node.parent
        self._index = self.node.parent.index_of(self.node) if self.node.parent else 0
        self._origins = [
            (item.parent, item.parent.index_of(item), item)
            for item in self.replacements
            if item.parent is not None
        ]

    def apply(self) -> None:
        if self._parent is None:
            return
        self._parent.remove(self.node)
        # 替换节点仍是原节点的子 / 后代，先脱离原父节点，避免父子链与 children 不一致
        for item in self.replacements:
            if item.parent is not None:
                item.parent.remove(item)
        self._parent.extend(self.replacements, self._index)

    def revert(self) -> None:
        if self._parent is None:
            return
        for node in self.replacements:
            if node.parent is not None:
                node.parent.remove(node)
        self._parent.add(self.node, self._index)
        grouped: dict[int, tuple[TargetNode, list[tuple[int, TargetNode]]]] = {}
        for parent, index, item in self._origins:
            grouped.setdefault(id(parent), (parent, []))[1].append((index, item))
        for parent, items in grouped.values():
            for index, item in sorted(items, key=lambda entry: entry[0]):
                parent.add(item, index)


@dataclass
class Compound(Command):
    """按顺序执行多条命令，撤销时逆序回滚。"""

    commands: list[Command]
    label = "批量操作"

    def apply(self) -> None:
        for command in self.commands:
            command.apply()

    def revert(self) -> None:
        for command in reversed(self.commands):
            command.revert()
