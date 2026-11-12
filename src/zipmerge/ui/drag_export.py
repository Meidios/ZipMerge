"""用延迟本地 URL 保持一次完成的 Windows 外部拖拽。"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEventLoop, QMimeData, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from zipmerge.i18n import tr
from ..core import (
    ExportCancelled,
    ExportFailure,
    MaterializeRequest,
    MergeSession,
    TargetNode,
    materialize_directory,
)


def is_internal_drag(data: QMimeData) -> bool:
    """是否为 ZipMerge 自己发起的树拖拽；检查格式不会触发延迟 URL 读取。"""
    return any(str(mime_type).startswith("application/x-zipmerge-") for mime_type in data.formats())


class LazyFileMimeData(QMimeData):
    """Qt fallback: resolve complete local URLs only when data is requested."""

    def __init__(
        self,
        source: QMimeData,
        prepare: Callable[[], tuple[Path, ...]] | None,
        initial_paths: tuple[Path, ...] = (),
    ) -> None:
        super().__init__()
        for mime_type in source.formats():
            self.setData(mime_type, source.data(mime_type))
        self._prepare = prepare
        self._paths = initial_paths
        self._urls_resolved = prepare is None
        self._resolving_urls = False
        self.setUrls([QUrl.fromLocalFile(str(path)) for path in initial_paths])

    def retrieveData(self, mime_type, preferred_type):
        if mime_type == "text/uri-list" and not self._urls_resolved and not self._resolving_urls:
            self.resolve_urls()
        return super().retrieveData(mime_type, preferred_type)

    @property
    def urls_resolved(self) -> bool:
        return self._urls_resolved

    def resolve_urls(self) -> tuple[Path, ...]:
        """Resolve complete content at most once per drag."""
        if self._urls_resolved or self._resolving_urls or self._prepare is None:
            return self._paths
        self._resolving_urls = True
        try:
            paths = self._prepare()
            self._paths = paths
            if paths:
                self.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
            else:
                self.setUrls([])
            self._urls_resolved = True
            return paths
        finally:
            self._resolving_urls = False


class DragMaterializeWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()

    def __init__(self, request: MaterializeRequest, parent=None) -> None:
        super().__init__(parent)
        self.request = request
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result = materialize_directory(
                self.request,
                on_progress=lambda percent, text: self.progress.emit(percent, text),
                cancel=self._cancel,
            )
        except ExportCancelled:
            self.cancelled.emit()
        except ExportFailure as exc:
            self.failed.emit(exc.message, exc.detail)
        except Exception as exc:
            self.failed.emit(tr("准备拖出内容时发生未预期错误：{0}", exc), "")
        else:
            self.completed.emit(result)


class DragProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("正在准备拖出内容"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._cancelling = False
        self._label = QLabel(tr("正在准备本地文件…"))
        self._label.setWordWrap(True)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._cancel = QPushButton(tr("取消"))
        self._cancel.clicked.connect(self.request_cancel)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._cancel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
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
        self._cancel.setEnabled(False)
        self._label.setText(tr("正在取消…"))
        self.cancel_requested.emit()

    def closeEvent(self, event) -> None:
        self.request_cancel()
        event.ignore()


class DragExportController:
    """每个树视图持有一个控制器，并缓存最近一次相同选择的物化结果。"""

    def __init__(self, view, session: MergeSession) -> None:
        self.view = view
        self.session = session
        self._cache: dict[tuple, tuple[Path, ...]] = {}

    def start(self, root: TargetNode, mime: QMimeData, supported: Qt.DropAction, default: Qt.DropAction) -> None:
        if not root.children:
            return
        if os.name == "nt" and QApplication.platformName() == "windows":
            from ..platform.native_drag import drag_files

            signature = self._signature(root)
            private = {str(name): bytes(mime.data(name)) for name in mime.formats()
                       if str(name).startswith("application/x-zipmerge-")}
            try:
                drag_files(private, lambda: self._prepare_paths(root, signature), QApplication.processEvents)
            except Exception as exc:
                QMessageBox.critical(self.view.window(), tr("无法拖出"), str(exc))
            return
        signature = self._signature(root)
        cached = self._cache.get(signature)
        if cached is not None and all(path.exists() for path in cached):
            lazy_mime = LazyFileMimeData(mime, None, cached)
        else:
            lazy_mime = LazyFileMimeData(mime, lambda: self._prepare_paths(root, signature))
        drag = QDrag(self.view)
        drag.setMimeData(lazy_mime)
        drag.exec(supported, default)

    def _prepare_paths(
        self, root: TargetNode, signature: tuple, destination: Path | None = None
    ) -> tuple[Path, ...]:
        paths = self._cache.get(signature)
        if paths is None or not all(path.exists() for path in paths):
            destination = destination or self.session.create_drag_output()
            request = MaterializeRequest(root, self.session.sources, destination)
            result = self._run(request)
            if result is None:
                self.session.discard_drag_output(destination)
                return ()
            paths = result.paths
            self._cache[signature] = paths
        return paths

    def _run(self, request: MaterializeRequest):
        dialog = DragProgressDialog(self.view.window())
        # Rendering may occur inside an OLE GetData call. A modal dialog can
        # change the active drag target and consume a release. Keep it visible
        # without activation, and return data only after the worker finishes.
        dialog.setModal(False)
        dialog.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        worker = DragMaterializeWorker(request, dialog)
        loop = QEventLoop()
        holder: dict[str, object] = {}
        worker.progress.connect(dialog.set_progress)
        dialog.cancel_requested.connect(worker.cancel)
        worker.completed.connect(lambda result: (holder.__setitem__("result", result), dialog.accept()))
        worker.failed.connect(lambda message, detail: (holder.__setitem__("error", (message, detail)), dialog.accept()))
        worker.cancelled.connect(lambda: (holder.__setitem__("cancelled", True), dialog.accept()))
        worker.finished.connect(loop.quit)
        owner = self.view.window()
        was_enabled = owner.isEnabled()
        owner.setEnabled(False)
        dialog.setEnabled(True)
        try:
            worker.start()
            dialog.show()
            loop.exec()
            worker.wait()
        finally:
            dialog.hide()
            owner.setEnabled(was_enabled)
            dialog.deleteLater()
        if "error" in holder:
            message, detail = holder["error"]
            box = QMessageBox(QMessageBox.Icon.Critical, tr("无法拖出"), str(message), parent=self.view.window())
            if detail:
                box.setDetailedText(str(detail))
            box.exec()
        return holder.get("result")

    def _signature(self, root: TargetNode) -> tuple:
        nodes = tuple(
            (
                node.name, node.is_dir, node.kind.value,
                node.source.source_id if node.source else None,
                node.source.entry_path if node.source else None,
                node.source.disk_path if node.source else None,
            )
            for node in root.walk()
        )
        archives = []
        for source_id in sorted(self.session.sources):
            path = self.session.sources[source_id].archive_path
            try:
                stat = path.stat()
                archives.append((source_id, stat.st_size, stat.st_mtime_ns))
            except OSError:
                archives.append((source_id, -1, -1))
        disk_paths = []
        for node in root.walk():
            raw = node.source.disk_path if node.source else None
            if not raw:
                continue
            path = Path(raw)
            try:
                stat = path.stat()
                disk_paths.append((str(path), stat.st_size, stat.st_mtime_ns))
            except OSError:
                disk_paths.append((str(path), -1, -1))
        return nodes, tuple(archives), tuple(disk_paths)
