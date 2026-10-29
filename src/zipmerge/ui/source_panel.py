"""源包面板：挂载压缩包、浏览内部条目、把条目拖到目标树。"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    ExtractionSelection,
    MergeSession,
    build_extraction_plan,
    build_source_drag_root,
)
from ..engine import (
    ArchiveCancelled,
    ArchiveError,
    ArchiveListing,
    is_password_failure,
    list_with_info,
)
from ..util.paths import is_archive_path
from ..util.settings import (
    KEY_EXTRACT_LAST_DIR,
    KEY_IMPORT_LAST_DIR,
    KEY_REMEMBER_PASSWORD,
    Settings,
)
from .design import icon as design_icon
from .design.tokens import Palette, palette_for
from .source_model import SourceTreeModel
from .extraction_runner import run_extraction_dialog
from .drag_export import DragExportController, is_internal_drag

#: 「添加压缩包」对话框的过滤器
ARCHIVE_FILTER = "压缩包 (*.zip *.7z *.rar *.tar *.gz *.bz2 *.xz *.tgz *.cab *.iso);;所有文件 (*)"


class SourceTreeView(QTreeView):
    """只读条目树；接受从资源管理器拖入的压缩包。"""

    dropped_paths = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setExpandsOnDoubleClick(True)
        self.drag_starter = None

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt 命名
        if self.drag_starter is not None:
            self.drag_starter(supported_actions)
            return
        super().startDrag(supported_actions)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.dropped_paths.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class ArchiveImportWorker(QThread):
    """后台读取压缩包清单，避免 7-Zip 与建表期间阻塞主事件循环。"""

    progress = Signal(int, str)
    completed = Signal(object, object, bool)

    def __init__(self, requests: list[tuple[str, str | None]], parent=None) -> None:
        super().__init__(parent)
        self._requests = requests
        self._cancel = threading.Event()
        self.loaded: list[tuple[str, ArchiveListing, str | None]] = []
        self.failures: list[tuple[str, ArchiveError]] = []
        self.was_cancelled = False

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        total = max(1, len(self._requests))
        for index, (path, password) in enumerate(self._requests):
            if self._cancel.is_set():
                break
            self.progress.emit(int(index * 100 / total), f"正在读取 {Path(path).name}…")
            try:
                listing = list_with_info(path, password=password, cancel=self._cancel)
            except ArchiveCancelled:
                break
            except ArchiveError as exc:
                self.failures.append((path, exc))
            else:
                self.loaded.append((path, listing, password))
        self.was_cancelled = self._cancel.is_set()
        if not self.was_cancelled:
            self.progress.emit(100, "压缩包读取完成")
        self.completed.emit(self.loaded, self.failures, self.was_cancelled)


class ArchiveImportProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正在添加压缩包")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._cancelling = False
        self._label = QLabel("正在读取压缩包…")
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.clicked.connect(self.request_cancel)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._cancel_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addLayout(row)

    def set_progress(self, percent: int, text: str) -> None:
        self._bar.setValue(percent)
        if text:
            self._label.setText(text)

    def request_cancel(self) -> None:
        if self._cancelling:
            return
        self._cancelling = True
        self._cancel_button.setEnabled(False)
        self._label.setText("正在取消…")
        self.cancel_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.request_cancel()
        event.ignore()


class SourcePanel(QWidget):
    """一个会话的源包列表。"""

    selection_changed = Signal()

    def __init__(self, session: MergeSession, settings: Settings | None = None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.settings = settings
        self._password = ""
        self._extract_action: QAction | None = None
        self.setObjectName("sourcePanel")
        self.setProperty("role", "panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 面板整体也接受拖入，拖到标题栏 / 空白边距同样能加包（PLAN 7.7 问题 1）
        self.setAcceptDrops(True)
        self._palette: Palette = palette_for("dark")

        self.model = SourceTreeModel(session, self, archive_only=True)

        title = QLabel("源包")
        title.setProperty("variant", "subtitle")

        self._add_button = self._tool_button("添加压缩包")
        self._extract_button = self._tool_button("解压所选到…")
        self._remove_button = self._tool_button("移除所选源包")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._add_button)
        header.addWidget(self._extract_button)
        header.addWidget(self._remove_button)

        self.view = SourceTreeView(self)
        self.view.setModel(self.model)
        self._scroll_restore_timer = QTimer(self.view)
        self._scroll_restore_timer.setSingleShot(True)
        self._scroll_restore_timer.timeout.connect(self._restore_scroll_value)
        self._drag_export = DragExportController(self.view, session)
        self.view.drag_starter = self._start_drag
        self.model.modelAboutToBeReset.connect(self._snapshot_view_state)
        self.model.modelReset.connect(self._restore_view_state)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.view.setHeaderHidden(True)
        self.view.setRootIsDecorated(False)
        self.view.setItemsExpandable(False)
        self.view.setExpandsOnDoubleClick(False)
        self.view.setColumnHidden(1, True)
        self.view.setColumnHidden(2, True)
        self.view.header().setStretchLastSection(True)
        self.view.setColumnWidth(0, 200)
        self.view.setColumnWidth(1, 64)
        self.view.dropped_paths.connect(self._on_dropped)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())
        self._expanded_keys: list[tuple[str, str]] = []
        self._selected_keys: list[tuple[str, str]] = []
        self._current_key: tuple[str, str] | None = None
        self._scroll_value = 0

        self._empty_hint = QLabel("把压缩包拖到这里，或点右上角添加")
        self._empty_hint.setProperty("variant", "disabled")
        self._empty_hint.setProperty("role", "empty")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setWordWrap(True)
        # 空态提示浮在视图之上，但不能吃掉拖放事件，否则空的时候就没法拖包进来
        self._empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        body = QGridLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.view, 0, 0)
        body.addWidget(self._empty_hint, 0, 0)
        layout.addLayout(body, 1)

        self._add_button.clicked.connect(self.prompt_add)
        self._remove_button.clicked.connect(self.remove_selected)

        self.set_palette(self._palette)
        self.refresh()

    # ---- 对外 -------------------------------------------------------
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.model.set_palette(palette)
        self._add_button.setIcon(QIcon(design_icon("folder_open", 16, palette.text_secondary)))
        self._remove_button.setIcon(QIcon(design_icon("delete", 16, palette.text_secondary)))
        self.view.viewport().update()

    def refresh(self) -> None:
        # 源包集合变化频率很低，且都发生在按钮 / 回调里，同步重建更可预期
        self.model.reload()
        self._update_empty_state()

    def bind_extract_action(self, action: QAction) -> None:
        """让面板按钮、右键菜单和主窗口共用同一个 QAction。"""
        self._extract_action = action
        self._extract_button.setDefaultAction(action)

    def selected_extraction_selections(self) -> list[ExtractionSelection]:
        selections: list[ExtractionSelection] = []
        for index in self.view.selectionModel().selectedRows(0):
            node = self.model.node(index)
            source_id = self.model.source_id_for_node(node)
            if node is None or source_id is None or node.source is None:
                continue
            selections.append(ExtractionSelection(source_id, node.source.entry_path or ""))
        return selections

    def prompt_extract(self) -> None:
        selections = self.selected_extraction_selections()
        if not selections:
            return
        start = self._last_extract_dir()
        if start is None:
            source = self.session.sources.get(selections[0].source_id)
            start = str(source.archive_path.parent) if source is not None else str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择解压目标文件夹", start)
        if not directory:
            return
        try:
            plan = build_extraction_plan(self.session.sources, selections, directory)
        except ValueError as exc:
            QMessageBox.warning(self, "无法解压", str(exc))
            return
        if run_extraction_dialog(self.window(), plan) is not None and self.settings is not None:
            self.settings.set_value(KEY_EXTRACT_LAST_DIR, directory)

    def prompt_add(self) -> None:
        start = self._last_import_dir() or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加压缩包", start, ARCHIVE_FILTER
        )
        if paths:
            self.add_archives(paths)

    def add_archives(self, paths) -> int:
        """挂载若干压缩包，返回成功数量；失败的包逐个提示。"""
        added = 0
        failures: list[str] = []
        last_dir: str | None = None
        candidates: list[str] = []
        mounted = {
            str(source.archive_path.resolve()).casefold()
            for source in self.session.sources.values()
        }
        for path in paths:
            if not is_archive_path(path):
                failures.append(f"{Path(path).name}：不是压缩包")
                continue
            resolved = str(Path(path).resolve())
            if resolved.casefold() in mounted:
                # 重复添加沿用旧行为：视为成功，但不重复运行 7-Zip 或重建树。
                added += 1
                if last_dir is None:
                    last_dir = str(Path(resolved).parent)
                continue
            candidates.append(str(path))

        loaded, load_failures, _cancelled = self._load_archive_listings(
            [(path, None) for path in candidates]
        )
        for path, listing, password in loaded:
            self.session.mount_archive(path, listing, password=password)
            added += 1
            if last_dir is None:
                last_dir = str(Path(path).parent)
        for path, exc in load_failures:
            if self._retry_with_password(path, exc):
                added += 1
                if last_dir is None:
                    last_dir = str(Path(path).parent)
            else:
                failures.append(f"{Path(path).name}：{exc.message}")
        self.refresh()
        if last_dir is not None:
            self._remember_import_dir(last_dir)
        if failures:
            QMessageBox.warning(self, "部分压缩包未能打开", "\n".join(failures))
        return added

    def _load_archive_listings(self, requests: list[tuple[str, str | None]]):
        if not requests:
            return [], [], False
        dialog = ArchiveImportProgressDialog(self)
        worker = ArchiveImportWorker(requests, dialog)
        worker.progress.connect(dialog.set_progress)
        dialog.cancel_requested.connect(worker.cancel)
        worker.completed.connect(lambda _loaded, _failures, _cancelled: dialog.accept())
        worker.start()
        dialog.exec()
        worker.wait()
        # 结果直接从 worker 读取；不要依赖 completed 排队信号一定在嵌套
        # 事件循环退出前送达（测试和应用退出阶段都可能提前结束 dialog）。
        return worker.loaded, worker.failures, worker.was_cancelled

    # ---- 拖入 -----------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if is_internal_drag(event.mimeData()):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.add_archives(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def remembered_password(self) -> str:
        """本会话内记住的密码；未开启记忆时始终为空。"""
        if self.settings is None:
            return ""
        if not bool(self.settings.value(KEY_REMEMBER_PASSWORD, False, bool)):
            return ""
        return self._password

    def _retry_with_password(self, path, error: ArchiveError) -> bool:
        """加密包：提示输入密码后重试；成功且开启记忆时留在本会话内（PLAN 12.2）。"""
        if not is_password_failure(error.output):
            return False
        password, ok = QInputDialog.getText(
            self,
            "需要密码",
            f"{Path(path).name} 已加密，请输入密码：",
            QLineEdit.EchoMode.Password,
            self.remembered_password(),
        )
        if not ok or not password:
            return False
        loaded, failures, cancelled = self._load_archive_listings([(str(path), password)])
        if cancelled:
            return False
        if failures or not loaded:
            exc = failures[0][1] if failures else error
            QMessageBox.warning(self, "无法用该密码打开", f"{Path(path).name}：{exc.message}")
            return False
        loaded_path, listing, loaded_password = loaded[0]
        self.session.mount_archive(loaded_path, listing, password=loaded_password)
        if self.settings is not None and bool(
            self.settings.value(KEY_REMEMBER_PASSWORD, False, bool)
        ):
            self._password = password
        return True

    def remove_selected(self) -> None:
        source_ids = self.selected_source_ids()
        if not source_ids:
            return
        failures: list[str] = []
        for source_id in source_ids:
            try:
                self.session.remove_source(source_id)
            except ValueError as exc:
                failures.append(f"{source_id}：{exc}")
        self.session.notify_change()
        self.refresh()
        if failures:
            QMessageBox.information(self, "无法移除源包", "\n".join(failures))

    def selected_source_ids(self) -> list[str]:
        seen: list[str] = []
        for index in self.view.selectionModel().selectedRows(0):
            source_id = self.model.source_id_for_node(self.model.node(index))
            if source_id and source_id not in seen:
                seen.append(source_id)
        return seen

    def focus_entry(self, source_id: str, entry_path: str) -> bool:
        """Select the owning archive without exposing its internal entries."""
        index = self.model.find_entry_index(source_id, "")
        if not index.isValid():
            return False
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.view.setFocus()
        return True

    # ---- 内部 -------------------------------------------------------
    def _on_dropped(self, paths: list[str]) -> None:
        self.add_archives(paths)

    def _selection_changed(self) -> None:
        self.selection_changed.emit()

    def _start_drag(self, supported_actions) -> None:
        indexes = list(self.view.selectionModel().selectedRows(0))
        nodes = [self.model.node(index) for index in indexes]
        root = build_source_drag_root(
            [node for node in nodes if node is not None], self.session.sources
        )
        mime = self.model.mimeData(indexes)
        self._drag_export.start(
            root, mime, supported_actions | Qt.DropAction.CopyAction, Qt.DropAction.CopyAction
        )

    def _node_key(self, index: QModelIndex) -> tuple[str, str] | None:
        node = self.model.node(index)
        source_id = self.model.source_id_for_node(node)
        if node is None or source_id is None or node.source is None:
            return None
        return source_id, node.source.entry_path or ""

    def _snapshot_view_state(self) -> None:
        self._expanded_keys = self._expanded_keys_from(QModelIndex())
        self._selected_keys = [
            key for index in self.view.selectionModel().selectedRows(0)
            if (key := self._node_key(index)) is not None
        ]
        self._current_key = self._node_key(self.view.currentIndex())
        self._scroll_value = self.view.verticalScrollBar().value()

    def _restore_view_state(self) -> None:
        for source_id, entry_path in sorted(self._expanded_keys, key=lambda item: item[1].count("\\")):
            index = self.model.find_entry_index(source_id, entry_path)
            if index.isValid():
                self.view.expand(index)
        selection = self.view.selectionModel()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for source_id, entry_path in self._selected_keys:
            index = self.model.find_entry_index(source_id, entry_path)
            if index.isValid():
                selection.select(index, flags)
        if self._current_key is not None:
            index = self.model.find_entry_index(*self._current_key)
            if index.isValid():
                selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.view.verticalScrollBar().setValue(self._scroll_value)
        # 布局完成后滚动条范围才最终稳定，再补一次恢复；定时器跟随 view 销毁。
        self._scroll_restore_timer.start(0)

    def _restore_scroll_value(self) -> None:
        self.view.verticalScrollBar().setValue(self._scroll_value)

    def _expanded_keys_from(self, parent: QModelIndex) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for row in range(self.model.rowCount(parent)):
            index = self.model.index(row, 0, parent)
            if self.view.isExpanded(index):
                key = self._node_key(index)
                if key is not None:
                    result.append(key)
                result.extend(self._expanded_keys_from(index))
        return result

    def _show_context_menu(self, pos: QPoint) -> None:
        index = self.view.indexAt(pos)
        selection = self.view.selectionModel()
        if index.isValid():
            if selection.isRowSelected(index.row(), index.parent()):
                selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
            else:
                self.view.setCurrentIndex(index)
        if self._extract_action is None or not self.selected_extraction_selections():
            return
        menu = QMenu(self)
        menu.addAction(self._extract_action)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _last_extract_dir(self) -> str | None:
        if self.settings is None:
            return None
        raw = str(self.settings.value(KEY_EXTRACT_LAST_DIR, "") or "")
        path = Path(raw)
        return str(path) if raw and path.is_dir() else None

    def _last_import_dir(self) -> str | None:
        """上次成功导入压缩包所在目录；设置缺失或路径已失效时返回 ``None``。"""
        if self.settings is None:
            return None
        raw = str(self.settings.value(KEY_IMPORT_LAST_DIR, "") or "")
        if not raw:
            return None
        path = Path(raw)
        return str(path) if path.is_dir() else None

    def _remember_import_dir(self, directory: str) -> None:
        if self.settings is not None:
            self.settings.set_value(KEY_IMPORT_LAST_DIR, directory)

    def _update_empty_state(self) -> None:
        empty = not self.session.sources
        self._empty_hint.setVisible(empty)
        # 视图始终可见：空态只是叠一层提示文字，隐藏视图会让它失去拖放目标的作用
        self._remove_button.setEnabled(bool(self.session.sources))
        if self._extract_action is None:
            self._extract_button.setEnabled(bool(self.selected_extraction_selections()))

    def _tool_button(self, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setProperty("variant", "ghost")
        button.setAutoRaise(True)
        button.setToolTip(tooltip)
        return button
