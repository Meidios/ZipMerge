"""目标树的 Qt 模型：把纯虚拟目标树映射到 QTreeView，并承载拖放语义。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon

from ..core import MergeSession, NodeKind, ResolveStrategy, TargetNode, top_level_nodes
from .design import icon as design_icon
from .design.tokens import Palette, palette_for

MIME_NODES = "application/x-zipmerge-nodes"
MIME_SOURCE = "application/x-zipmerge-source"

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aif", ".aiff", ".wma", ".aac", ".opus"}
VIDEO_EXT = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".wmv", ".mpg", ".mpeg",
    ".m2ts", ".mts", ".webm",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".psd", ".svg"}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".cab", ".iso"}
TEXT_EXT = {
    ".txt", ".md", ".json", ".xml", ".csv", ".py", ".js", ".ts", ".html", ".css",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".ini", ".yml", ".yaml", ".log", ".bat", ".sh",
}

# 来源角标配色（与主题无关的固定三色循环，保证多源可区分）
_SOURCE_COLORS: tuple[str, ...] = ("#3B6EA5", "#7A5AA8", "#2F8F6B", "#B06A2C", "#A34D6B")


def icon_name_for(node: TargetNode) -> str:
    if node.is_dir:
        return "folder" if node.kind is not NodeKind.DISK_DIR else "folder_open"
    suffix = Path(node.name).suffix.lower()
    if suffix in AUDIO_EXT:
        return "music_note"
    if suffix in VIDEO_EXT:
        return "video"
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in ARCHIVE_EXT:
        return "folder_zip"
    if suffix in TEXT_EXT:
        return "document"
    return "document"


class TargetTreeModel(QAbstractItemModel):
    """目标树模型。所有结构变更都经 :class:`MergeSession`，模型只做展示与翻译。"""

    HEADERS: tuple[str, str] = ("名称", "来源")

    #: 重命名被拒绝（非法名称）时抛出，由视图显示原因。
    rename_rejected = Signal(str)

    def __init__(self, session: MergeSession, parent=None) -> None:
        super().__init__(parent)
        self._session = session
        self._palette: Palette = palette_for("dark")
        self._icon_cache: dict[tuple[str, int, str], QIcon] = {}
        self._source_colors: dict[str, str] = {}
        self.conflict_resolver: Callable[[int], ResolveStrategy | None] | None = None
        self._refresh_pending = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._flush_refresh)
        self._rebuild_source_colors()

    # ---- 基本信息 ---------------------------------------------------
    @property
    def session(self) -> MergeSession:
        return self._session

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._icon_cache.clear()
        top = self.index(0, 0)
        bottom = self.index(self.rowCount() - 1, self.columnCount() - 1)
        if top.isValid() and bottom.isValid():
            self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.DecorationRole])

    def refresh(self) -> None:
        self._rebuild_source_colors()
        self.beginResetModel()
        self.endResetModel()

    def request_refresh(self) -> None:
        """延迟合并刷新。

        拖放等操作是在视图处理事件的栈里改树的，此刻直接 reset 模型会让视图状态
        错乱；统一下一个事件循环再重建。多次请求只重建一次。
        """
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self._refresh_timer.start(0)

    def _flush_refresh(self) -> None:
        self._refresh_pending = False
        self.refresh()

    # ---- 映射 -------------------------------------------------------
    def node(self, index: QModelIndex) -> TargetNode:
        if not index.isValid():
            return self._session.root
        return index.internalPointer()  # type: ignore[return-value]

    def index_for_node(self, node: TargetNode) -> QModelIndex:
        parent = node.parent
        if parent is None:
            return QModelIndex()
        return self.createIndex(parent.children.index(node), 0, node)

    def nodes_for_indexes(self, indexes) -> list[TargetNode]:
        seen: set[int] = set()
        result: list[TargetNode] = []
        for index in indexes:
            node = self.node(index)
            if node.uid in seen:
                continue
            seen.add(node.uid)
            result.append(node)
        return result

    # ---- QAbstractItemModel ----------------------------------------
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        parent_node = self.node(parent)
        if row < 0 or column < 0 or row >= len(parent_node.children) or column >= len(self.HEADERS):
            return QModelIndex()
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        node = self.node(index)
        parent_node = node.parent
        if parent_node is None or parent_node is self._session.root:
            return QModelIndex()
        grandparent = parent_node.parent
        if grandparent is None:
            return QModelIndex()
        return self.createIndex(grandparent.children.index(parent_node), 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        return len(self.node(parent).children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return self.HEADERS[section] if 0 <= section < len(self.HEADERS) else None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled
        node = self.node(index)
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if node.is_dir:
            base |= Qt.ItemFlag.ItemIsDropEnabled
        if index.column() == 0:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = self.node(index)
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return node.name if column == 0 else self._source_label(node)
        if role == Qt.ItemDataRole.EditRole and column == 0:
            return node.name
        if role == Qt.ItemDataRole.DecorationRole and column == 0:
            return self._icon(node)
        if role == Qt.ItemDataRole.ForegroundRole and column == 1:
            source_id = node.source.source_id if node.source is not None else None
            color = self._source_colors.get(source_id or "")
            if color:
                return QColor(color)
            return QColor(self._palette.text_secondary)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(node)
        if role == Qt.ItemDataRole.TextAlignmentRole and column == 1:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or index.column() != 0:
            return False
        node = self.node(index)
        new_name = str(value).strip()
        if new_name == node.name:
            return False
        try:
            changed = self._session.rename(
                node,
                new_name,
                conflict_resolver=self.conflict_resolver,
            )
        except ValueError as exc:
            self.rename_rejected.emit(str(exc))
            return False
        return changed

    # ---- 拖放 -------------------------------------------------------
    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    def mimeTypes(self) -> list[str]:
        return [MIME_NODES, MIME_SOURCE, "text/uri-list"]

    def mimeData(self, indexes) -> QMimeData:  # type: ignore[override]
        nodes = [self.node(index) for index in indexes if index.column() == 0 and index.isValid()]
        payload = [node.uid for node in top_level_nodes(nodes)]
        data = QMimeData()
        data.setData(MIME_NODES, json.dumps(sorted(set(payload))).encode("utf-8"))
        data.setText("zipmerge-nodes")
        return data

    def canDropMimeData(self, data: QMimeData, action, row: int, column: int, parent: QModelIndex) -> bool:
        if data.hasFormat(MIME_NODES):
            uids = self._decode_nodes(data)
            if not uids:
                return False
            target, _index = self._drop_position(parent, row)
            for uid in uids:
                node = self._session.root.find_by_uid(uid)
                if node is None or node.parent is None:
                    return False
                if node is target or node.is_ancestor_of(target):
                    return False
            return True
        if data.hasFormat(MIME_SOURCE) or data.hasUrls():
            target, _index = self._drop_position(parent, row)
            return target.is_dir
        return False

    def dropMimeData(self, data: QMimeData, action, row: int, column: int, parent: QModelIndex) -> bool:
        target, index = self._drop_position(parent, row)
        if not target.is_dir:
            return False

        if data.hasFormat(MIME_NODES):
            nodes = [self._session.root.find_by_uid(uid) for uid in self._decode_nodes(data)]
            movable = [node for node in nodes if node is not None and node.parent is not None]
            if not movable:
                return False
            try:
                changed = self._session.move(
                    movable,  # type: ignore[arg-type]
                    target,
                    index,
                    conflict_resolver=self.conflict_resolver,
                )
            except ValueError:
                return False
            return changed

        if data.hasFormat(MIME_SOURCE):
            payload = json.loads(bytes(data.data(MIME_SOURCE)).decode("utf-8"))
            items = payload.get("items")
            if not isinstance(items, list):
                items = [payload]
            nodes: list[TargetNode] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                source_id = item.get("source_id")
                entry_path = item.get("entry_path") or ""
                if not source_id:
                    continue
                try:
                    if item.get("whole"):
                        source = self._session.sources.get(source_id)
                        name = source.display_name if source else source_id
                        nodes.extend(
                            self._session.build_entry_nodes(
                                source_id, "", wrapper_name=name
                            )
                        )
                    else:
                        nodes.extend(
                            self._session.build_entry_nodes(source_id, entry_path)
                        )
                except KeyError:
                    continue
            return bool(
                self._session.add_nodes(
                    target,
                    nodes,
                    index,
                    conflict_resolver=self.conflict_resolver,
                )
            )

        if data.hasUrls():
            paths = [url.toLocalFile() for url in data.urls() if url.isLocalFile()]
            if not paths:
                return False
            return bool(
                self._session.import_disk_paths(
                    paths,
                    parent=target,
                    conflict_resolver=self.conflict_resolver,
                )
            )

        return False

    # ---- 内部 -------------------------------------------------------
    def _drop_position(self, parent: QModelIndex, row: int) -> tuple[TargetNode, int]:
        node = self.node(parent)
        if row is None or row < 0:
            if node.is_dir:
                return node, len(node.children)
            owner = node.parent or self._session.root
            return owner, owner.index_of(node) + 1
        return node, row

    @staticmethod
    def _decode_nodes(data: QMimeData) -> list[int]:
        try:
            payload = json.loads(bytes(data.data(MIME_NODES)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return []
        return [int(uid) for uid in payload if isinstance(uid, int)]

    def _source_label(self, node: TargetNode) -> str:
        if node.source is None or node.source.source_id is None:
            return "本地"
        return node.source.source_id

    def _tooltip(self, node: TargetNode) -> str:
        lines = [node.path() or node.name]
        source = self._session.source_of(node)
        if source is not None:
            lines.append(f"来源：{source.archive_path.name}")
            if node.source and node.source.entry_path:
                lines.append(f"包内路径：{node.source.entry_path}")
            elif node.kind is NodeKind.ARCHIVE_ENTRY:
                lines.append("来源包整包")
        elif node.source is not None and node.source.disk_path:
            lines.append(f"磁盘路径：{node.source.disk_path}")
        lines.append(f"类型：{'文件夹' if node.is_dir else '文件'}")
        return "\n".join(lines)

    def _rebuild_source_colors(self) -> None:
        self._source_colors = {
            source_id: _SOURCE_COLORS[index % len(_SOURCE_COLORS)]
            for index, source_id in enumerate(sorted(self._session.sources))
        }

    def _icon(self, node: TargetNode) -> QIcon:
        name = icon_name_for(node)
        color = self._palette.text_primary
        key = (name, 16, color)
        cached = self._icon_cache.get(key)
        if cached is None:
            cached = design_icon(name, 16, color)
            self._icon_cache[key] = cached
        return cached
