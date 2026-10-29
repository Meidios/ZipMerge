"""目标树面板：整理虚拟目标结构的视图与操作入口。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core import MergeSession, NodeKind, ResolveStrategy, TargetNode, build_target_drag_root
from ..engine import ArchiveError, extract_entries
from ..util.paths import safe_relative_path
from .design import icon as design_icon
from .design.tokens import Palette, palette_for
from .target_model import TargetTreeModel
from .drag_export import DragExportController


class TargetTreeView(QTreeView):
    """目标树：内部移动与准备真实文件后的外部拖出共用一次拖拽。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.drag_starter = None

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt 命名
        if self.drag_starter is not None:
            self.drag_starter(supported_actions)
            return
        super().startDrag(supported_actions)


class TargetPanel(QWidget):
    """目标树视图 + 整理操作。"""

    current_node_changed = Signal(object)
    locate_requested = Signal(object)
    #: 请求预览面板播放 / 暂停当前音频（空格快捷键）
    playback_toggle_requested = Signal()

    def __init__(self, session: MergeSession, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("targetPanel")
        self.setProperty("role", "panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._palette: Palette = palette_for("dark")
        self._clipboard: list[TargetNode] = []
        self._clipboard_cut = False
        #: 模型 reset 前记录、reset 后还原的视图状态（展开 / 选中 / 当前项）
        self._expanded_uids: list[int] = []
        self._selected_uids: list[int] = []
        self._current_uid: int | None = None

        self.model = TargetTreeModel(session, self)
        self.model.conflict_resolver = self._ask_file_conflicts
        self.model.rename_rejected.connect(self._on_rename_rejected)

        title = QLabel("目标结构")
        title.setProperty("variant", "subtitle")
        self._stats = QLabel("")
        self._stats.setProperty("variant", "secondary")

        self.view = TargetTreeView(self)
        self.view.setModel(self.model)
        self._drag_export = DragExportController(self.view, session)
        self.view.drag_starter = self._start_drag
        # 必须晚于 setModel：视图自身的 reset 处理先清空展开/选中，之后我们再按 uid 还原
        self.model.modelAboutToBeReset.connect(self._snapshot_view_state)
        self.model.modelReset.connect(self._restore_view_state)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.view.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 单选即改名会挡住双击打开，改名只保留 F2 / 右键菜单
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(False)
        self.view.setDropIndicatorShown(True)
        self.view.setAcceptDrops(True)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._on_context_menu)
        self.view.setColumnWidth(0, 320)
        self.view.header().setStretchLastSection(True)
        self.view.doubleClicked.connect(self._on_double_clicked)

        self._empty_hint = QLabel("把源包里的文件拖到这里，开始整理目标结构")
        self._empty_hint.setProperty("variant", "disabled")
        self._empty_hint.setProperty("role", "empty")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setWordWrap(True)
        # 空态提示浮在视图之上，但不能吃掉拖放事件，否则空的时候就没法往里拖东西
        self._empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(0)
        header.addWidget(title)
        header.addWidget(self._stats)
        layout.addLayout(header)
        body = QGridLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.view, 0, 0)
        body.addWidget(self._empty_hint, 0, 0)
        layout.addLayout(body, 1)

        self.view.selectionModel().currentChanged.connect(self._on_current_changed)

        self._shortcut_rename = QShortcut(QKeySequence(Qt.Key.Key_F2), self)
        self._shortcut_rename.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_rename.activated.connect(self.rename_current)
        self._shortcut_delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        self._shortcut_delete.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_delete.activated.connect(self.delete_selected)
        self._shortcut_play = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._shortcut_play.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_play.activated.connect(self.playback_toggle_requested.emit)

        self.refresh()

    # ---- 对外 -------------------------------------------------------
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.model.set_palette(palette)
        self.view.viewport().update()

    def refresh(self) -> None:
        # 统计与空态用当前树直接算，模型重建延后一个事件循环（见 TargetTreeModel.request_refresh）
        self._update_stats()
        self._update_empty_state()
        self.model.request_refresh()

    def reload_now(self) -> None:
        self.model.refresh()
        self._update_stats()
        self._update_empty_state()

    def selected_nodes(self) -> list[TargetNode]:
        return self.model.nodes_for_indexes(
            [index for index in self.view.selectionModel().selectedRows(0)]
        )

    def current_node(self) -> TargetNode | None:
        index = self.view.currentIndex()
        return self.model.node(index) if index.isValid() else None

    def new_folder(self) -> None:
        parent = self._folder_target()
        node = self.session.new_folder(parent)
        self.reload_now()
        index = self.model.index_for_node(node)
        if index.isValid():
            self.view.setCurrentIndex(index)
            self.view.edit(index)

    def import_disk_paths(self, paths: list[str]) -> list[TargetNode]:
        """把外部文件写入目标树根目录，并统一处理同名文件。"""
        return self.session.import_disk_paths(
            paths,
            parent=self.session.root,
            conflict_resolver=self._ask_file_conflicts,
        )

    def rename_current(self) -> None:
        index = self.view.currentIndex()
        if index.isValid() and index.column() == 0:
            self.view.edit(index)

    def delete_selected(self) -> None:
        nodes = self.selected_nodes()
        if nodes:
            self.session.delete(nodes)

    def copy_selected(self) -> None:
        nodes = self.selected_nodes()
        if nodes:
            self._clipboard = list(nodes)
            self._clipboard_cut = False

    def cut_selected(self) -> None:
        nodes = self.selected_nodes()
        if nodes:
            self._clipboard = list(nodes)
            self._clipboard_cut = True

    def paste(self) -> None:
        if not self._clipboard:
            return
        target = self._folder_target()
        if self._clipboard_cut:
            if self.session.move(
                self._clipboard,
                target,
                len(target.children),
                conflict_resolver=self._ask_file_conflicts,
            ):
                self._clipboard = []
                self._clipboard_cut = False
        else:
            self.session.paste(
                self._clipboard,
                target,
                len(target.children),
                conflict_resolver=self._ask_file_conflicts,
            )

    def flatten_current(self, levels: int | None = 0) -> None:
        node = self.current_node()
        if node is None or not node.is_dir:
            return
        if self.session.flatten(
            node,
            levels,
            conflict_resolver=self._ask_file_conflicts,
        ):
            self.reload_now()

    def prompt_flatten_depth(self) -> None:
        node = self.current_node()
        if node is None or not node.is_dir:
            return
        depth, ok = QInputDialog.getInt(
            self, "摊平到指定层数", "保留的目录层数：", 2, 1, 20, 1
        )
        if ok:
            self.flatten_current(depth - 1)

    def locate_current(self) -> None:
        node = self.current_node()
        if node is not None:
            self.locate_requested.emit(node)

    def open_node(self, node: TargetNode | None) -> None:
        """用系统默认程序打开节点对应的文件；包内条目先取到本地临时目录。"""
        if node is None:
            return
        if node.is_dir:
            # 只有真实磁盘文件夹有实体；虚拟目录 / 包内目录交给系统没有意义
            path = Path(node.source.disk_path) if node.source and node.source.disk_path else None
            if path is not None and path.is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        path, error = self._openable_path(node)
        if path is None:
            if error:
                QMessageBox.warning(self, "无法打开", error)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ---- 内部 -------------------------------------------------------
    def _folder_target(self) -> TargetNode:
        """当前操作应落到的目录：选中的目录，或选中项所在目录，否则根。"""
        current = self.current_node()
        if current is None:
            return self.session.root
        if current.is_dir:
            return current
        return current.parent or self.session.root

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self.current_node_changed.emit(self.model.node(current) if current.isValid() else None)

    def _start_drag(self, supported_actions) -> None:
        indexes = list(self.view.selectionModel().selectedRows(0))
        root = build_target_drag_root(self.model.nodes_for_indexes(indexes))
        mime = self.model.mimeData(indexes)
        self._drag_export.start(
            root,
            mime,
            supported_actions | Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
            Qt.DropAction.MoveAction,
        )

    def _on_rename_rejected(self, reason: str) -> None:
        self._stats.setText(reason)
        self._stats.setProperty("variant", "danger")
        self._repolish(self._stats)

    def _ask_file_conflicts(self, count: int) -> ResolveStrategy | None:
        """一次询问处理本次操作中的全部同名文件；关闭或取消不修改目标树。"""
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            "目标结构中有同名文件",
            f"发现 {count} 个新文件与目标结构中的文件同名。",
            parent=self,
        )
        box.setInformativeText("请选择覆盖旧文件，或保留两份并自动重命名新文件。")
        overwrite = box.addButton("覆盖旧文件", QMessageBox.ButtonRole.DestructiveRole)
        rename = box.addButton("重命名新文件", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        cancel.setText("取消")
        box.setDefaultButton(rename)
        box.exec()
        clicked = box.clickedButton()
        if clicked is overwrite:
            return ResolveStrategy.OVERWRITE
        if clicked is rename:
            return ResolveStrategy.RENAME
        return None

    def _pointed_node(self, point: QPoint) -> TargetNode | None:
        """右键落点：点中未选中的项时改为选中它，点在空白处则清空选择。

        点中已在选中集内的项时只移动当前项，保留多选，右键菜单的批量操作才有意义。
        """
        index = self.view.indexAt(point)
        selection = self.view.selectionModel()
        if index.isValid():
            if selection.isRowSelected(index.row(), index.parent()):
                selection.setCurrentIndex(
                    index, QItemSelectionModel.SelectionFlag.NoUpdate
                )
            else:
                self.view.setCurrentIndex(index)
            return self.model.node(index)
        # 空白处右键：清掉选择与当前项，让「新建文件夹」落到根而不是上一次选中的目录
        selection.clearSelection()
        self.view.setCurrentIndex(QModelIndex())
        return None

    def _on_double_clicked(self, index: QModelIndex) -> None:
        node = self.model.node(index) if index.isValid() else None
        if node is not None and not node.is_dir:
            self.open_node(node)

    def _openable_path(self, node: TargetNode) -> tuple[Path | None, str]:
        """返回节点可交给系统打开的本地文件路径与失败原因。"""
        ref = node.source
        if ref is None:
            return None, "该节点没有可打开的文件"
        if node.kind is NodeKind.DISK_FILE and ref.disk_path:
            path = Path(ref.disk_path)
            return (path, "") if path.is_file() else (None, f"文件不存在：{path}")
        if ref.source_id and ref.entry_path:
            source = self.session.sources.get(ref.source_id)
            if source is None:
                return None, "源包已卸载，无法打开该文件"
            # 解压根即落地目录，7-Zip 会在其下重建包内相对结构
            root = self.session.stage_path(
                "open", *safe_relative_path(ref.source_id).parts
            )
            destination = root / safe_relative_path(ref.entry_path)
            try:
                extract_entries(
                    source.archive_path,
                    [ref.entry_path],
                    root,
                    password=source.password,
                )
            except ArchiveError as exc:
                return None, exc.message
            if destination.is_file():
                return destination, ""
            return None, "无法从源包中取出该文件"
        return None, "该节点没有可打开的文件"

    def _on_context_menu(self, point: QPoint) -> None:
        node = self._pointed_node(point)
        nodes = self.selected_nodes()
        has_selection = bool(nodes)
        is_dir = node is not None and node.is_dir
        can_open = node is not None and (
            not node.is_dir or bool(node.source and node.source.disk_path)
        )

        menu = QMenu(self)
        actions: list[tuple[str, str, object, bool]] = [
            ("打开", "open", lambda _checked=False, n=node: self.open_node(n), can_open),
            ("", "", None, False),
            ("新建文件夹", "folder_add", self.new_folder, node is None or is_dir),
            ("重命名", "rename", self.rename_current, bool(nodes) and len(nodes) == 1),
            ("删除", "delete", self.delete_selected, has_selection),
            ("", "", None, False),
            ("剪切", "cut", self.cut_selected, has_selection),
            ("复制", "copy", self.copy_selected, has_selection),
            ("粘贴", "clipboard_paste", self.paste, bool(self._clipboard) and (node is None or is_dir)),
            ("", "", None, False),
            ("摊平到当前目录", "arrow_expand", lambda: self.flatten_current(0), is_dir),
            ("摊平到指定层数…", "arrow_expand", self.prompt_flatten_depth, is_dir),
            ("", "", None, False),
            ("在源包中定位", "search", self.locate_current, node is not None),
        ]
        for label, icon_name, slot, enabled in actions:
            if not label:
                menu.addSeparator()
                continue
            action = QAction(label, menu)
            action.setIcon(design_icon(icon_name, 16, self._palette.text_secondary))
            action.setEnabled(enabled)
            action.triggered.connect(slot)  # type: ignore[arg-type]
            menu.addAction(action)
        menu.exec(self.view.viewport().mapToGlobal(point))

    def _snapshot_view_state(self) -> None:
        """模型 reset 前记录展开 / 选中 / 当前项，供 reset 后还原。"""
        self._expanded_uids = self._expanded_uids_from(QModelIndex())
        self._selected_uids = [
            self.model.node(index).uid
            for index in self.view.selectionModel().selectedRows(0)
        ]
        current = self.view.currentIndex()
        self._current_uid = self.model.node(current).uid if current.isValid() else None

    def _restore_view_state(self) -> None:
        """模型 reset 后按 uid 还原展开集与选中集；已删除的节点自动跳过。"""
        root = self.session.root
        selection = self.view.selectionModel()
        for uid in self._expanded_uids:
            node = root.find_by_uid(uid)
            if node is None:
                continue
            index = self.model.index_for_node(node)
            if index.isValid():
                self.view.expand(index)
        rows = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for uid in self._selected_uids:
            node = root.find_by_uid(uid)
            if node is None:
                continue
            index = self.model.index_for_node(node)
            if index.isValid():
                selection.select(index, rows)
        if self._current_uid is not None:
            node = root.find_by_uid(self._current_uid)
            if node is not None:
                index = self.model.index_for_node(node)
                if index.isValid():
                    selection.setCurrentIndex(
                        index, QItemSelectionModel.SelectionFlag.NoUpdate
                    )

    def _expanded_uids_from(self, parent: QModelIndex) -> list[int]:
        uids: list[int] = []
        for row in range(self.model.rowCount(parent)):
            index = self.model.index(row, 0, parent)
            if self.view.isExpanded(index):
                uids.append(self.model.node(index).uid)
                uids.extend(self._expanded_uids_from(index))
        return uids

    def _update_stats(self) -> None:
        files = self.session.root.total_file_count()
        dirs = sum(1 for node in self.session.root.walk(include_self=False) if node.is_dir)
        if not self.session.root.children:
            self._stats.setText("")
        else:
            self._stats.setText(f"{dirs} 个文件夹 · {files} 个文件")
        self._stats.setProperty("variant", "secondary")
        self._repolish(self._stats)

    def _update_empty_state(self) -> None:
        empty = not self.session.root.children
        self._empty_hint.setVisible(empty)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
