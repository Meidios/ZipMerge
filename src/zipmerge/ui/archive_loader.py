"""Cancellable archive listing and verified password acquisition shared by UI entry points."""
from __future__ import annotations

import threading
from pathlib import Path
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                              QMessageBox, QProgressBar, QPushButton, QVBoxLayout)
from ..engine import (ArchiveCancelled, ArchiveError, ArchiveListing, is_password_failure,
                      list_with_info, verify_archive_password)

class ArchiveImportWorker(QThread):
    """后台读取压缩包清单，避免 7-Zip 与建表期间阻塞主事件循环。"""

    progress = Signal(int, str)
    completed = Signal(object, object, bool)

    def __init__(self, requests: list[tuple[str, str | None]], parent=None, *, verify_password=True) -> None:
        super().__init__(parent)
        self._requests = requests
        self._verify_password = verify_password
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
                if self._verify_password and password is not None and listing.has_encrypted_entries:
                    verify_archive_password(path, listing, password, cancel=self._cancel)
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


def load_archive_listings(parent, requests: list[tuple[str, str | None]]):
    if not requests:
        return [], [], False
    dialog = ArchiveImportProgressDialog(parent)
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


def unlock_archive(parent, path, *, initial_password="", loader=None):
    """Ask until actual decryption succeeds, or return None on cancellation."""
    loader = loader or (lambda requests: load_archive_listings(parent, requests))
    while True:
        password, ok = QInputDialog.getText(
            parent, "需要密码", f"{Path(path).name} 已加密，请输入密码：",
            QLineEdit.EchoMode.Password, initial_password,
        )
        if not ok:
            return None
        if not password:
            continue
        loaded, failures, cancelled = loader([(str(path), password)])
        if cancelled:
            return None
        if loaded:
            return loaded[0]
        if not failures:
            return None
        error = failures[0][1]
        message = error.hint or error.message
        QMessageBox.warning(parent, "无法用该密码打开", f"{Path(path).name}：{message}")
        if not is_password_failure(error.output):
            return None
        initial_password = ""
