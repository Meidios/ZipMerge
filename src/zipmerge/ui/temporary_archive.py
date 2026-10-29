"""临时压缩包文件卡片：状态展示与跨应用文件拖放。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from . import fmt
from .design import icon as design_icon
from .design.tokens import Palette, palette_for
from .archive_inspector import ArchiveInspector

STATE_READY = "ready"
STATE_STALE = "stale"
STATE_MISSING = "missing"


class TemporaryArchiveCard(QWidget):
    """展示一份会话临时包；仅 ready 状态允许以文件 URL 拖出。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("temporaryArchiveCard")
        self.setProperty("state", STATE_READY)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("临时压缩包，可拖拽发送")

        self._path: Path | None = None
        self._password: str | None = None
        self._state = STATE_READY
        self._press_position: QPoint | None = None
        self._palette: Palette = palette_for("dark")

        self._icon = QLabel()
        self._icon.setFixedSize(24, 24)
        self._name = QLabel()
        self._name.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._detail = QLabel("拖拽到聊天或邮件发送")
        self._detail.setProperty("variant", "secondary")
        self._preview_button = QPushButton("预览")
        self._preview_button.setCursor(Qt.CursorShape.ArrowCursor)
        self._preview_button.setToolTip("在独立只读窗口查看临时包的目录和文件结构")
        self._preview_button.setEnabled(False)
        self._preview_button.clicked.connect(self.preview_archive)

        for label in (self._icon, self._name, self._detail):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        text_layout.addWidget(self._name)
        text_layout.addWidget(self._detail)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)
        layout.addWidget(self._icon)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self._preview_button)

        self.set_palette(self._palette)
        self.hide()

    @property
    def archive_path(self) -> Path | None:
        return self._path

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_draggable(self) -> bool:
        return self._state == STATE_READY and self._path is not None and self._path.is_file()

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._icon.setPixmap(design_icon("archive", 20, palette.text_secondary).pixmap(20, 20))

    def set_archive(self, path: str | Path, *, password: str | None = None) -> None:
        self._path = Path(path).resolve()
        self._password = password
        self._name.setText(self._path.name)
        self._name.setToolTip(str(self._path))
        self.setToolTip(f"拖拽发送：{self._path}")
        self._set_state(STATE_READY)
        self.show()

    def mark_stale(self) -> None:
        if self._path is not None:
            self._set_state(STATE_STALE)

    def clear_archive(self) -> None:
        self._path = None
        self._password = None
        self._press_position = None
        self._preview_button.setEnabled(False)
        self.setToolTip("")
        self.hide()

    def preview_archive(self) -> None:
        """Inspect actual output without external opening or merge-session import."""
        if not self._ensure_available():
            return
        dialog = ArchiveInspector(self._path, self._palette, password=self._password, parent=self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def mime_data(self) -> QMimeData | None:
        """构造单文件 URL MIME；文件丢失时更新卡片并拒绝拖出。"""
        if not self._ensure_available():
            return None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self._path))])
        return mime

    def start_drag(self):
        mime = self.mime_data()
        if mime is None:
            return Qt.DropAction.IgnoreAction
        drag = QDrag(self)
        drag.setMimeData(mime)
        preview = self.grab()
        drag.setPixmap(preview)
        drag.setHotSpot(QPoint(min(24, preview.width() // 2), preview.height() // 2))
        return drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton and self._ensure_available():
            self._press_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        self._press_position = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 命名
        if self._press_position is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._press_position).manhattanLength()
        if distance < QApplication.startDragDistance():
            return
        self._press_position = None
        self.start_drag()
        self._update_cursor()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 命名
        self._press_position = None
        self._update_cursor()
        super().mouseReleaseEvent(event)

    def _ensure_available(self) -> bool:
        if self._state != STATE_READY or self._path is None:
            return False
        if not self._path.is_file():
            self._set_state(STATE_MISSING)
            return False
        return True

    def _set_state(self, state: str) -> None:
        self._state = state
        self.setProperty("state", state)
        self._preview_button.setEnabled(self.is_draggable)
        if state == STATE_READY:
            size = self._path.stat().st_size if self._path is not None and self._path.is_file() else -1
            detail = "拖拽到聊天或邮件发送"
            if size >= 0:
                detail = f"{fmt.human_size(size)} · {detail}"
            self._detail.setText(detail)
            self._detail.setProperty("variant", "success")
        elif state == STATE_STALE:
            self._detail.setText("内容已变更，请重新生成")
            self._detail.setProperty("variant", "warning")
        else:
            self._detail.setText("文件不存在，请重新生成")
            self._detail.setProperty("variant", "danger")
        self._repolish(self)
        self._repolish(self._detail)
        self._update_cursor()

    def _update_cursor(self) -> None:
        cursor = Qt.CursorShape.OpenHandCursor if self.is_draggable else Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
