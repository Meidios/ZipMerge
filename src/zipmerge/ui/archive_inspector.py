"""Read-only archive structure inspection, isolated from merge sessions."""

from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QTreeView, QVBoxLayout,
)

from ..core.imports import build_entry_subtree
from . import fmt
from .design import icon as design_icon
from .design.tokens import Palette
from .source_panel import ArchiveImportWorker


class ArchiveInspector(QDialog):
    """List the actual archive without importing, extracting, or opening entries."""

    def __init__(self, path: Path, palette: Palette, *, password: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"临时包内容预览 — {path.name}")
        self.resize(760, 540)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._palette = palette
        self._closing_result: int | None = None
        self._summary = QLabel("正在读取压缩包目录…")
        self._path_label = QLabel(str(path))
        self._path_label.setWordWrap(True)
        self._path_label.setProperty("variant", "secondary")
        self.view = QTreeView()
        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels(["名称", "类型", "大小"])
        self.view.setModel(self.model)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.setUniformRowHeights(True)
        self.view.setColumnWidth(0, 460)
        self.view.setColumnWidth(1, 90)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._path_label)
        layout.addWidget(self._summary)
        layout.addWidget(self.view, 1)
        layout.addWidget(buttons)
        self._worker = ArchiveImportWorker([(str(path), password)], self)
        self._worker.completed.connect(self._loaded)
        self._worker.finished.connect(self._worker_finished)
        # The dialog owns the timer; closing before the next event turn is safe.
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._worker.start)
        self._start_timer.start(0)

    def _loaded(self, loaded, failures, cancelled):
        if cancelled or self._closing_result is not None:
            return
        if failures:
            self._summary.setText(f"无法读取临时包：{failures[0][1].message}")
            return
        if not loaded:
            self._summary.setText("未读取到压缩包内容")
            return
        path, listing, _password = loaded[0]
        roots = build_entry_subtree(listing.entries, source_id="inspection", archive_path=path)
        sizes = {entry.path: entry.size for entry in listing.entries if not entry.is_dir}
        stack = [(self.model.invisibleRootItem(), roots)]
        files = directories = 0
        while stack:
            parent, children = stack.pop()
            for node in children:
                name = QStandardItem(node.name)
                name.setIcon(design_icon("folder" if node.is_dir else "document", 16, self._palette.text_primary))
                kind = QStandardItem("文件夹" if node.is_dir else "文件")
                size = QStandardItem("" if node.is_dir else fmt.human_size(sizes.get(node.source.entry_path, 0)))
                for item in (name, kind, size):
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                parent.appendRow([name, kind, size])
                files += not node.is_dir
                directories += node.is_dir
                if node.children:
                    stack.append((name, node.children))
        self._summary.setText(f"{directories} 个文件夹 · {files} 个文件 · 只读结构预览")
        self.view.expandToDepth(0)

    def done(self, result):
        self._start_timer.stop()
        if self._worker.isRunning():
            self._closing_result = result
            self._worker.cancel()
            self._summary.setText("正在关闭预览…")
            return
        super().done(result)

    def _worker_finished(self):
        if self._closing_result is not None:
            super().done(self._closing_result)
