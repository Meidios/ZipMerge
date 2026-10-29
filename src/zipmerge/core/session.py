"""合并会话：源包集合 + 目标树 + 临时目录 + 撤销栈。"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..engine import ArchiveEntry, ArchiveListing, list_with_info
from ..util.paths import normalize_entry_path, sanitize_filename
from ..util.tempdir import create_session_dir, default_root, remove_dir
from .commands import AddNodes, Compound, MoveNodes, RemoveNodes, RenameNode, ReplaceNode
from .conflict import ResolveStrategy
from .imports import (
    archive_display_name,
    build_disk_nodes,
    build_entry_subtree,
    flatten_levels,
)
from .nodes import NodeKind, SourceRef, TargetNode, new_root, validate_name
from .undo import UndoStack


@dataclass
class SourceArchive:
    """一个已挂载的源压缩包。"""

    source_id: str
    archive_path: Path
    display_name: str
    listing: ArchiveListing
    password: str | None = None
    _entries_by_path: dict[str, ArchiveEntry] = field(init=False, repr=False)
    _entry_count: int = field(init=False, repr=False)
    _total_size: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._entries_by_path = {
            normalize_entry_path(entry.path): entry for entry in self.listing.entries
        }
        files = [entry for entry in self.listing.entries if not entry.is_dir]
        self._entry_count = len(files)
        self._total_size = sum(entry.size for entry in files)

    def entry(self, path: str) -> ArchiveEntry | None:
        """按规范化包内路径 O(1) 查找条目。"""
        return self._entries_by_path.get(normalize_entry_path(path))

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def total_size(self) -> int:
        return self._total_size

    @property
    def archive_type(self) -> str:
        return self.listing.archive_type

    @property
    def has_encrypted_entries(self) -> bool:
        return self.listing.has_encrypted_entries


class MergeSession:
    """一个标签页对应一个会话；所有目标树改动都经撤销栈。"""

    def __init__(
        self,
        name: str = "新会话",
        *,
        temp_root: Path | None = None,
        undo_limit: int = 50,
    ) -> None:
        self.name = name
        self.root = new_root()
        self.sources: dict[str, SourceArchive] = {}
        self.undo = UndoStack(limit=undo_limit, on_change=self._on_undo_change)
        self.on_change: Callable[[], None] | None = None
        self.on_undo_change: Callable[[], None] | None = None

        self.temp_root = Path(temp_root) if temp_root else default_root()
        self.temp_dir = create_session_dir(self.temp_root, token=sanitize_filename(name)[:12] or "s")
        self.stage_dir = self.temp_dir / "stage"
        self.deliveries_dir = self.temp_dir / "deliveries"
        self.drag_exports_dir = self.temp_dir / "drag-exports"
        self._temporary_archive: Path | None = None

        self._wrappers: dict[str, TargetNode] = {}
        self._source_seq = 0

    # ---- 生命周期 ---------------------------------------------------

    def close(self) -> None:
        """清理本会话的临时目录。"""
        remove_dir(self.temp_dir)

    @property
    def temporary_archive(self) -> Path | None:
        """当前可供拖拽发送的临时压缩包。"""
        return self._temporary_archive

    def create_temporary_output(self, filename: str) -> Path:
        """为一次临时导出创建独立目录，返回保留客户可见名称的目标路径。"""
        if not filename or Path(filename).name != filename or filename in {".", ".."}:
            raise ValueError("临时压缩包必须使用安全的文件名")
        self.deliveries_dir.mkdir(parents=True, exist_ok=True)
        generation = Path(
            tempfile.mkdtemp(prefix="generation-", dir=str(self.deliveries_dir))
        )
        return generation / filename

    def commit_temporary_archive(self, path: str | Path) -> None:
        """确认一次临时导出成功，并尽力清理更早的 generation。"""
        candidate = Path(path).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(self.deliveries_dir.resolve()):
            raise ValueError("临时压缩包不在当前会话的交付目录中")
        self._temporary_archive = candidate
        for generation in self.deliveries_dir.iterdir():
            # Resolve both sides: Windows TEMP may contain an 8.3 alias while
            # candidate already uses its canonical long path.
            if generation.resolve() != candidate.parent:
                remove_dir(generation)

    def discard_temporary_output(self, path: str | Path) -> None:
        """放弃失败或取消的 generation，不影响已经确认的临时包。"""
        candidate = Path(path).resolve()
        root = self.deliveries_dir.resolve()
        if candidate.is_relative_to(root) and candidate.parent != root:
            if self._temporary_archive is None or candidate.parent != self._temporary_archive.parent:
                remove_dir(candidate.parent)

    def clear_temporary_archives(self) -> None:
        """无条件清理本会话生成的临时交付包。"""
        remove_dir(self.deliveries_dir)
        self._temporary_archive = None

    def create_drag_output(self) -> Path:
        """为一次外部拖出建立独立候选目录；成功内容保留到会话关闭。"""
        self.drag_exports_dir.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="generation-", dir=str(self.drag_exports_dir)))

    def discard_drag_output(self, path: str | Path) -> None:
        candidate = Path(path).resolve()
        root = self.drag_exports_dir.resolve()
        if candidate != root and candidate.is_relative_to(root):
            remove_dir(candidate)

    def _on_undo_change(self) -> None:
        if self.on_undo_change is not None:
            self.on_undo_change()

    def notify_change(self) -> None:
        if self.on_change is not None:
            self.on_change()

    # ---- 查询 -------------------------------------------------------

    @property
    def archive_paths(self) -> list[Path]:
        return [source.archive_path for source in self.sources.values()]

    @property
    def source_names(self) -> list[str]:
        return [source.archive_path.name for source in self.sources.values()]

    @property
    def has_content(self) -> bool:
        return bool(self.root.children)

    def source_of(self, node: TargetNode) -> SourceArchive | None:
        if node.source is None or node.source.source_id is None:
            return None
        return self.sources.get(node.source.source_id)

    def is_attached(self, node: TargetNode) -> bool:
        current: TargetNode | None = node
        while current is not None:
            if current is self.root:
                return True
            current = current.parent
        return False

    # ---- 导入 -------------------------------------------------------

    def import_archive(self, path: str | Path, password: str | None = None) -> TargetNode:
        """导入压缩包，在目标树中生成「包名文件夹」层。重复导入直接返回已有项。"""
        resolved = Path(path).resolve()
        key = str(resolved).casefold()
        existing = self._wrappers.get(key)
        if existing is not None:
            if not self.is_attached(existing):
                self.undo.do(AddNodes(parent=self.root, nodes=[existing]))
            return existing

        listing = list_with_info(resolved, password=password)
        return self.mount_archive(resolved, listing, password=password)

    def mount_archive(
        self,
        path: str | Path,
        listing: ArchiveListing,
        *,
        password: str | None = None,
    ) -> TargetNode:
        """提交已经在后台读取完成的压缩包清单。"""
        resolved = Path(path).resolve()
        key = str(resolved).casefold()
        existing = self._wrappers.get(key)
        if existing is not None:
            if not self.is_attached(existing):
                self.undo.do(AddNodes(parent=self.root, nodes=[existing]))
            return existing

        display = archive_display_name(resolved)
        source_id = self._unique_source_id(display)
        source = SourceArchive(
            source_id=source_id,
            archive_path=resolved,
            display_name=display,
            listing=listing,
            password=password,
        )
        self.sources[source_id] = source

        wrapper = self._entry_dir_node(
            source,
            display,
            "",
            build_entry_subtree(
                listing.entries, source_id=source_id, archive_path=str(resolved)
            ),
        )

        self._wrappers[key] = wrapper
        self.undo.do(AddNodes(parent=self.root, nodes=[wrapper]))
        return wrapper

    def import_disk_paths(
        self,
        paths: list[str | Path],
        parent: TargetNode | None = None,
        *,
        conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None,
    ) -> list[TargetNode]:
        """导入磁盘散文件 / 文件夹；``parent`` 缺省挂到根。"""
        imported = build_disk_nodes(paths)
        return self.add_nodes(
            parent or self.root,
            imported.nodes,
            conflict_resolver=conflict_resolver,
        )

    def build_entry_nodes(
        self,
        source_id: str,
        entry_path: str = "",
        *,
        wrapper_name: str | None = None,
    ) -> list[TargetNode]:
        """构造源包条目的目标节点，但暂不挂入目标树。"""
        source = self.sources.get(source_id)
        if source is None:
            raise KeyError(f"未挂载的源包：{source_id}")

        prefix = normalize_entry_path(entry_path)
        if not prefix:
            nodes = build_entry_subtree(
                source.listing.entries,
                source_id=source_id,
                archive_path=str(source.archive_path),
                prefix="",
            )
            if wrapper_name is not None:
                nodes = [self._entry_dir_node(source, wrapper_name, "", nodes)]
            return nodes

        entry = source.entry(prefix)
        name = prefix.rsplit("\\", 1)[-1]
        if entry is not None and not entry.is_dir:
            return [self._entry_file_node(source, name, prefix)]
        children = build_entry_subtree(
            source.listing.entries,
            source_id=source_id,
            archive_path=str(source.archive_path),
            prefix=prefix,
        )
        return [self._entry_dir_node(source, name, prefix, children)]

    def import_entry_subtree(
        self,
        source_id: str,
        entry_path: str = "",
        parent: TargetNode | None = None,
        *,
        wrapper_name: str | None = None,
        conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None,
    ) -> list[TargetNode]:
        """把已挂载源包中的**某个条目本身**（或其整包）导入目标树的指定目录。

        - ``entry_path`` 为空：导入整包内容；``wrapper_name`` 非空时再包一层
          「包名文件夹」（源包面板拖入整包用）。
        - ``entry_path`` 指向文件夹：保留该文件夹名，其内容原样挂在下面。
        - ``entry_path`` 指向文件：导入单个文件节点。
        """
        nodes = self.build_entry_nodes(
            source_id,
            entry_path,
            wrapper_name=wrapper_name,
        )
        return self.add_nodes(
            parent or self.root,
            nodes,
            conflict_resolver=conflict_resolver,
        )

    @staticmethod
    def _entry_dir_node(
        source: SourceArchive, name: str, entry_path: str, children: list[TargetNode]
    ) -> TargetNode:
        node = TargetNode(
            name=name,
            is_dir=True,
            kind=NodeKind.ARCHIVE_ENTRY,
            source=SourceRef(
                kind=NodeKind.ARCHIVE_ENTRY,
                source_id=source.source_id,
                archive_path=str(source.archive_path),
                entry_path=entry_path,
            ),
        )
        for child in children:
            node.add(child)
        return node

    @staticmethod
    def _entry_file_node(source: SourceArchive, name: str, entry_path: str) -> TargetNode:
        return TargetNode(
            name=name,
            is_dir=False,
            kind=NodeKind.ARCHIVE_ENTRY,
            source=SourceRef(
                kind=NodeKind.ARCHIVE_ENTRY,
                source_id=source.source_id,
                archive_path=str(source.archive_path),
                entry_path=entry_path,
            ),
        )

    def source_reference_count(self, source_id: str) -> int:
        """返回目标树中仍依赖某个源包的节点数。"""
        return sum(
            1
            for node in self.root.walk(include_self=False)
            if node.source is not None and node.source.source_id == source_id
        )

    def remove_source(self, source_id: str) -> None:
        """安全卸载源包；仍被目标结构引用时拒绝破坏会话。"""
        references = self.source_reference_count(source_id)
        if references:
            raise ValueError(f"目标结构仍有 {references} 个节点使用该源包，请先删除这些内容")
        self.sources.pop(source_id, None)
        self._wrappers = {key: node for key, node in self._wrappers.items()
                          if (node.source is None or node.source.source_id != source_id)}
        # 防止撤销旧的目标删除后重新引入已经卸载的 source_id。
        self.undo.clear()

    def _unique_source_id(self, base: str) -> str:
        candidate = base
        while candidate in self.sources:
            self._source_seq += 1
            candidate = f"{base}_{self._source_seq}"
        return candidate

    # ---- 编辑操作 ---------------------------------------------------

    def new_folder(self, parent: TargetNode, name: str = "新建文件夹") -> TargetNode:
        from .nodes import make_virtual_dir, unique_child_name

        node = make_virtual_dir(unique_child_name(parent, name))
        self.undo.do(AddNodes(parent=parent, nodes=[node]))
        return node

    def add_nodes(
        self,
        parent: TargetNode,
        nodes: list[TargetNode],
        index: int | None = None,
        *,
        conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None,
    ) -> list[TargetNode]:
        """把新节点放入目录；UI 可提供一次性的同名文件处理选择。"""
        if not nodes or not parent.is_dir:
            return []
        plan = self._file_placement_plan(
            parent,
            nodes,
            conflict_resolver=conflict_resolver,
        )
        if plan is None:
            return []
        placed, removals, renames = plan
        commands = [RenameNode(node=node, new_name=name) for node, name in renames]
        if removals:
            commands.append(RemoveNodes(nodes=removals))
        commands.append(AddNodes(parent=parent, nodes=placed, index=index))
        self.undo.do(commands[0] if len(commands) == 1 else Compound(commands))
        return placed

    def rename(
        self,
        node: TargetNode,
        new_name: str,
        *,
        conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None,
    ) -> bool:
        if node is self.root or node.parent is None:
            raise ValueError("根节点不能重命名")
        reason = validate_name(new_name)
        if reason:
            raise ValueError(reason)
        cleaned = new_name.strip()
        if cleaned == node.name:
            return False

        collisions = []
        if not node.is_dir:
            collisions = [
                child
                for child in node.parent.children
                if child is not node
                and not child.is_dir
                and child.name.casefold() == cleaned.casefold()
            ]
        if collisions and conflict_resolver is not None:
            strategy = conflict_resolver(1)
            if strategy is None:
                return False
            if strategy is not ResolveStrategy.OVERWRITE:
                cleaned = self._unique_file_name(
                    cleaned,
                    {child.name.casefold() for child in node.parent.children if child is not node},
                )
                collisions = []

        commands = []
        if collisions and conflict_resolver is not None:
            commands.append(RemoveNodes(nodes=collisions))
        commands.append(RenameNode(node=node, new_name=cleaned))
        self.undo.do(commands[0] if len(commands) == 1 else Compound(commands))
        return True

    def delete(self, nodes: list[TargetNode]) -> None:
        candidates = [node for node in nodes if node.parent is not None and node is not self.root]
        if not candidates:
            return
        self.undo.do(RemoveNodes(nodes=candidates))

    def move(
        self,
        nodes: list[TargetNode],
        target: TargetNode,
        index: int,
        *,
        conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None,
    ) -> bool:
        """把节点移动到 target 下 index 位置。index 按移出后的列表计算。"""
        if not nodes or not target.is_dir:
            return False
        moves: list[tuple[TargetNode, TargetNode, int]] = []
        for node in nodes:
            if node is self.root or node.parent is None:
                continue
            if node is target or node.is_ancestor_of(target):
                raise ValueError("不能把文件夹移动到它自己的子目录中")
            moves.append((node, node.parent, node.parent.index_of(node)))
        if not moves:
            return False
        movable = [node for node, _parent, _index in moves]
        plan = self._file_placement_plan(
            target,
            movable,
            exclude=set(movable),
            conflict_resolver=conflict_resolver,
        )
        if plan is None:
            return False
        placed, removals, renames = plan
        placed_set = set(placed)
        final_moves = [move for move in moves if move[0] in placed_set]
        commands = [RenameNode(node=node, new_name=name) for node, name in renames]
        if removals:
            commands.append(RemoveNodes(nodes=removals))
        commands.append(MoveNodes(moves=final_moves, target=target, index=index))
        self.undo.do(commands[0] if len(commands) == 1 else Compound(commands))
        return True

    def paste(
        self,
        nodes: list[TargetNode],
        parent: TargetNode,
        index: int | None = None,
        *,
        conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None,
    ) -> list[TargetNode]:
        """复制粘贴：深拷贝节点后插入。"""
        clones = [node.clone() for node in nodes]
        if not clones:
            return []
        return self.add_nodes(
            parent,
            clones,
            index,
            conflict_resolver=conflict_resolver,
        )

    def cut(self, nodes: list[TargetNode]) -> list[TargetNode]:
        """剪切在当前实现中等价于「记住待移动节点」，由 UI 调用 move 完成。"""
        return list(nodes)

    def flatten(
        self,
        node: TargetNode,
        levels: int | None = 0,
        *,
        conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None,
    ) -> int:
        """摊平节点：去掉包装目录层。返回被提升的节点数量。"""
        if node.parent is None or not node.is_dir:
            return 0
        replacements = flatten_levels(node, levels)
        plan = self._file_placement_plan(
            node.parent,
            replacements,
            conflict_resolver=conflict_resolver,
        )
        if plan is None:
            return 0
        placed, removals, renames = plan
        commands = [RenameNode(node=item, new_name=name) for item, name in renames]
        if removals:
            commands.append(RemoveNodes(nodes=removals))
        commands.append(ReplaceNode(node=node, replacements=placed))
        self.undo.do(commands[0] if len(commands) == 1 else Compound(commands))
        return len(placed)

    def _file_placement_plan(
        self,
        parent: TargetNode,
        nodes: list[TargetNode],
        *,
        exclude: set[TargetNode] | None = None,
        conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None,
    ) -> tuple[list[TargetNode], list[TargetNode], list[tuple[TargetNode, str]]] | None:
        """规划同一目录内的文件冲突；无 resolver 时保留核心层旧行为。"""
        exclude = exclude or set()
        occupied = {
            child.name.casefold()
            for child in parent.children
            if child not in exclude and not child.is_dir
        }
        conflicts = 0
        for node in nodes:
            if node.is_dir:
                continue
            key = node.name.casefold()
            if key in occupied:
                conflicts += 1
            occupied.add(key)

        if not conflicts or conflict_resolver is None:
            return list(nodes), [], []
        strategy = conflict_resolver(conflicts)
        if strategy is None:
            return None

        if strategy is not ResolveStrategy.OVERWRITE:
            file_names = {
                child.name.casefold()
                for child in parent.children
                if child not in exclude and not child.is_dir
            }
            all_names = {
                child.name.casefold()
                for child in parent.children
                if child not in exclude
            }
            renames: list[tuple[TargetNode, str]] = []
            for node in nodes:
                if node.is_dir:
                    all_names.add(node.name.casefold())
                    continue
                key = node.name.casefold()
                if key in file_names:
                    renamed = self._unique_file_name(node.name, all_names)
                    renames.append((node, renamed))
                    key = renamed.casefold()
                file_names.add(key)
                all_names.add(key)
            return list(nodes), [], renames

        # 覆盖：同名批次以后到者为准，并移除目标目录中的全部旧文件。
        existing_by_name: dict[str, list[TargetNode]] = {}
        for child in parent.children:
            if child not in exclude and not child.is_dir:
                existing_by_name.setdefault(child.name.casefold(), []).append(child)
        placed: list[TargetNode] = []
        latest: dict[str, TargetNode] = {}
        removals: list[TargetNode] = []
        for node in nodes:
            if node.is_dir:
                placed.append(node)
                continue
            key = node.name.casefold()
            for old in existing_by_name.pop(key, []):
                if old not in removals:
                    removals.append(old)
            previous = latest.get(key)
            if previous is not None:
                if previous in placed:
                    placed.remove(previous)
                if previous.parent is not None and previous not in removals:
                    removals.append(previous)
            latest[key] = node
            placed.append(node)
        return placed, removals, []

    @staticmethod
    def _unique_file_name(desired: str, taken: set[str]) -> str:
        """按 ``name_1.ext`` 生成 Windows 大小写不敏感的唯一文件名。"""
        path = Path(desired)
        suffix = path.suffix
        stem = desired[: -len(suffix)] if suffix else desired
        index = 1
        while True:
            candidate = f"{stem}_{index}{suffix}"
            if candidate.casefold() not in taken:
                return candidate
            index += 1

    # ---- 导出准备 ---------------------------------------------------

    def stage_path(self, *parts: str) -> Path:
        return self.stage_dir.joinpath(*parts)
