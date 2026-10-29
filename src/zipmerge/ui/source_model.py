"""源包面板的 Qt 模型：已挂载压缩包及其内部条目的只读树，提供拖拽载荷。"""

from __future__ import annotations

import json

from PySide6.QtCore import (
    QAbstractItemModel,
    QMimeData,
    QModelIndex,
    QTimer,
    Qt,
)
from PySide6.QtGui import QIcon

from ..core import MergeSession, NodeKind, SourceRef, TargetNode, top_level_nodes
from ..core.imports import build_entry_subtree
from . import fmt
from .design import icon as design_icon
from .design.tokens import Palette, palette_for
from .target_model import MIME_SOURCE, icon_name_for


class SourceTreeModel(QAbstractItemModel):
    """只读源包树。拖拽内部条目到目标树时提供 :data:`MIME_SOURCE` 载荷。"""

    HEADERS: tuple[str, str, str] = ("源包 / 条目", "类型", "大小")

    def __init__(self, session: MergeSession, parent=None, *, archive_only: bool = False) -> None:
        super().__init__(parent)
        self._archive_only = archive_only
        self._session = session
        self._palette: Palette = palette_for("dark")
        self._icon_cache: dict[tuple[str, int, str], QIcon] = {}
        self._roots: list[TargetNode] = []
        self._sizes: dict[tuple[str, str], int] = {}
        self._types: dict[str, str] = {}
        self._nodes_by_path: dict[tuple[str, str], TargetNode] = {}
        self._refresh_pending = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._flush_refresh)
        self.reload()

    # ---- 刷新 -------------------------------------------------------
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._icon_cache.clear()

    def reload(self) -> None:
        """同步重建（构造与测试直接调用）。"""
        self.beginResetModel()
        self._roots = []
        self._sizes = {}
        self._types = {}
        self._nodes_by_path = {}
        for source_id, source in sorted(self._session.sources.items()):
            root = self._build_root(source_id, source)
            self._roots.append(root)
            for node in root.walk():
                entry_path = node.source.entry_path if node.source else ""
                self._nodes_by_path[(source_id, entry_path or "")] = node
            self._sizes[(source_id, "")] = source.total_size
            self._types[source_id] = source.archive_type.upper()
            for entry in source.listing.entries:
                if not entry.is_dir:
                    self._sizes[(source_id, entry.path)] = entry.size
        self.endResetModel()

    def request_refresh(self) -> None:
        """把一次事件循环内的多次变更合并成一次重建。"""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self._refresh_timer.start(0)

    def _flush_refresh(self) -> None:
        self._refresh_pending = False
        self.reload()

    def _build_root(self, source_id, source) -> TargetNode:
        node = TargetNode(
            name=source.display_name,
            is_dir=True,
            kind=NodeKind.ARCHIVE_ENTRY,
            source=SourceRef(
                kind=NodeKind.ARCHIVE_ENTRY,
                source_id=source_id,
                archive_path=str(source.archive_path),
                entry_path="",
            ),
        )
        for child in build_entry_subtree(
            source.listing.entries,
            source_id=source_id,
            archive_path=str(source.archive_path),
        ):
            node.add(child)
        return node

    # ---- 映射 -------------------------------------------------------
    def node(self, index: QModelIndex) -> TargetNode | None:
        if not index.isValid():
            return None
        return index.internalPointer()  # type: ignore[return-value]

    def index_for_node(self, node: TargetNode) -> QModelIndex:
        parent = node.parent
        if parent is None:
            try:
                row = self._roots.index(node)
            except ValueError:
                return QModelIndex()
            return self.createIndex(row, 0, node)
        return self.createIndex(parent.children.index(node), 0, node)

    def source_id_for_node(self, node: TargetNode | None) -> str | None:
        return node.source.source_id if node is not None and node.source else None

    def find_entry_index(self, source_id: str, entry_path: str) -> QModelIndex:
        """按源包 ID 与包内路径定位条目（用于「在源包中定位」）。"""
        from ..util.paths import normalize_entry_path

        wanted = normalize_entry_path(entry_path)
        if self._archive_only and wanted:
            return QModelIndex()
        node = self._nodes_by_path.get((source_id, wanted))
        return self.index_for_node(node) if node is not None else QModelIndex()

    # ---- QAbstractItemModel ----------------------------------------
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if self._archive_only and parent.isValid():
            return QModelIndex()
        parent_node = self.node(parent)
        children = self._roots if parent_node is None else parent_node.children
        if row < 0 or column < 0 or row >= len(children) or column >= len(self.HEADERS):
            return QModelIndex()
        return self.createIndex(row, column, children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        node = self.node(index)
        if node is None or node.parent is None:
            return QModelIndex()
        return self.index_for_node(node.parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if self._archive_only and parent.isValid():
            return 0
        if parent.column() > 0:
            return 0
        node = self.node(parent)
        return len(self._roots if node is None else node.children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return self.HEADERS[section] if 0 <= section < len(self.HEADERS) else None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
        )

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        node = self.node(index)
        if node is None:
            return None
        source_id = self.source_id_for_node(node)
        entry_path = node.source.entry_path or "" if node.source else ""
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return node.name
            if column == 1:
                return self._type_label(node, source_id)
            return self._size_label(node, source_id, entry_path)
        if role == Qt.ItemDataRole.DecorationRole and column == 0:
            return self._icon(node)
        if role == Qt.ItemDataRole.TextAlignmentRole and column >= 1:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            if entry_path:
                return f"包内路径：{entry_path}"
            return node.name
        return None

    # ---- 拖拽 -------------------------------------------------------
    def mimeTypes(self) -> list[str]:
        return [MIME_SOURCE]

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.CopyAction

    def mimeData(self, indexes) -> QMimeData:  # type: ignore[override]
        data = QMimeData()
        nodes: list[TargetNode] = []
        for index in indexes:
            if not index.isValid() or index.column() != 0:
                continue
            node = self.node(index)
            source_id = self.source_id_for_node(node)
            if node is None or source_id is None or node.source is None:
                continue
            nodes.append(node)
        items: list[dict[str, object]] = []
        for node in top_level_nodes(nodes):
            source_id = self.source_id_for_node(node)
            if source_id is None or node.source is None:
                continue
            entry_path = node.source.entry_path or ""
            items.append({
                "source_id": source_id,
                "entry_path": entry_path,
                "whole": not entry_path,
            })
        if items:
            payload = dict(items=items, **items[0])
            data.setData(MIME_SOURCE, json.dumps(payload).encode("utf-8"))
            data.setText("zipmerge-source")
        return data

    # ---- 内部 -------------------------------------------------------
    def _type_label(self, node: TargetNode, source_id: str | None) -> str:
        if node.parent is None:
            return self._types.get(source_id or "", "压缩包")
        if node.is_dir:
            return "文件夹"
        suffix = node.name.rsplit(".", 1)[-1].upper() if "." in node.name else ""
        return suffix or "文件"

    def _size_label(self, node: TargetNode, source_id: str | None, entry_path: str) -> str:
        if node.parent is None or not node.is_dir:
            size = self._sizes.get((source_id or "", entry_path))
            return fmt.human_size(size) if size is not None else ""
        return ""

    def _icon(self, node: TargetNode) -> QIcon:
        name = "folder_zip" if node.parent is None else icon_name_for(node)
        color = self._palette.text_primary
        key = (name, 16, color)
        cached = self._icon_cache.get(key)
        if cached is None:
            cached = design_icon(name, 16, color)
            self._icon_cache[key] = cached
        return cached
