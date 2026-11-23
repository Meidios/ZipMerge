"""Qt Widgets views for read-only Office content and paginated Windows PDF rendering."""

import base64
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QRunnable,
    QThreadPool, Qt, Signal, Slot,
)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QSpinBox,
    QStackedWidget, QTableView, QVBoxLayout, QWidget,
)

from ..core.document_preview import (
    EXCEL_EXT, WORD_EXT, Sheet, column_label, read_sheet, read_workbook, word_text,
)
from ..i18n import tr
from ..platform.office_filter import legacy_office_text
from ..util.paths import asset_path


class _ReadDocument(QRunnable):
    def __init__(self, generation, result, read):
        super().__init__()
        self.generation = generation
        self.result = result
        self.read = read

    def run(self):
        try:
            content = self.read()
        except (OSError, ValueError, RuntimeError) as exc:
            self.result.emit(self.generation, None, str(exc))
        else:
            self.result.emit(self.generation, content, "")


class _Cells(QAbstractTableModel):
    def __init__(self, sheet: Sheet, parent):
        super().__init__(parent)
        self.sheet = sheet

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.sheet.rows

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.sheet.columns

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            return self.sheet.cells.get((index.row(), index.column()), "")

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return column_label(section + 1) if orientation == Qt.Horizontal else str(section + 1)


class _PdfPage(QScrollArea):
    def __init__(self):
        super().__init__()
        self.original = QPixmap()
        self.page_width = 0
        self.zoom = 0
        self.label = QLabel()
        self.setWidget(self.label)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)

    def set_page(self, image, width=0):
        self.original = image
        self.page_width = width
        self._scale()
        self.verticalScrollBar().setValue(0)

    def set_zoom(self, percent):
        self.zoom = percent
        self._scale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale()

    def _scale(self):
        if self.original.isNull():
            self.label.clear()
            return
        target = self.page_width * self.zoom // 100 if self.zoom else self.viewport().width()
        width = max(1, min(self.original.width(), target))
        scaled = self.original.scaledToWidth(width,
                                             Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(scaled)
        self.label.resize(scaled.size())


class DocumentPreview(QWidget):
    failed = Signal(str)
    _result_ready = Signal(int, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._generation = 0
        self._pdf = None
        self._path = None
        self._book = None
        self._sheet_names = QComboBox()
        self._sheet_names.setAccessibleName(tr("工作表"))
        self._sheet_names.currentIndexChanged.connect(self._select_sheet)
        self._number = QSpinBox()
        self._number.setAccessibleName(tr("页码"))
        self._number.setKeyboardTracking(False)
        self._number.valueChanged.connect(self._select_page)
        self._count = QLabel()
        self._zoom = QComboBox()
        self._zoom.addItem(tr("适合宽度"), 0)
        for percent in (100, 150, 200):
            self._zoom.addItem(f"{percent}%", percent)
        self._zoom.setAccessibleName(tr("缩放"))
        self._notice = QLabel()
        self._notice.setWordWrap(True)
        controls = QHBoxLayout()
        controls.addWidget(self._sheet_names)
        controls.addWidget(self._number)
        controls.addWidget(self._count)
        controls.addWidget(self._zoom)
        controls.addStretch()
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._table = QTableView()
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._page = _PdfPage()
        self._zoom.currentIndexChanged.connect(lambda: self._page.set_zoom(self._zoom.currentData()))
        self._stack = QStackedWidget()
        for view in (self._notice, self._text, self._table, self._page):
            self._stack.addWidget(view)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self._stack, 1)
        self._result_ready.connect(self._on_result_ready)
        self.clear()

    def clear(self):
        self._generation += 1
        self._pool.clear()
        process, self._pdf = self._pdf, None
        if process is not None:
            process.kill()
            process.wait()
            process.stdin.close()
            process.stdout.close()
        self._book = self._path = None
        self._sheet_names.clear()
        for control in (self._sheet_names, self._number, self._count, self._zoom):
            control.hide()
        self._text.clear()
        self._set_table(None)
        self._page.set_page(QPixmap())
        self._zoom.setCurrentIndex(0)
        self._notice.setText(tr("正在读取文档…"))
        self._stack.setCurrentWidget(self._notice)

    def shutdown(self):
        self.clear()
        self._pool.waitForDone()

    def show_file(self, path: Path, suffix: str):
        self._path = path
        if suffix == ".pdf":
            self._open_pdf(path)
        elif suffix in EXCEL_EXT:
            self._submit(lambda: read_workbook(path))
        elif suffix in WORD_EXT:
            self._submit(lambda: word_text(path))
        else:
            self._submit(lambda: legacy_office_text(path))

    def _submit(self, read):
        self._generation += 1
        self._pool.clear()
        self._pool.start(_ReadDocument(self._generation, self._result_ready, read))

    @Slot(int, object, str)
    def _on_result_ready(self, generation, content, error):
        if generation != self._generation:
            return
        if error:
            self.failed.emit(error)
        elif isinstance(content, tuple):
            number, total, width, data = content
            image = QPixmap()
            if not image.loadFromData(data, "PNG"):
                self.failed.emit(tr("Windows 无法渲染此 PDF，请检查文件是否已加密或损坏"))
                return
            self._number.blockSignals(True)
            self._number.setRange(1, total)
            self._number.setValue(number)
            self._number.blockSignals(False)
            self._count.setText(f"/ {total}")
            self._count.show()
            self._number.show()
            self._zoom.show()
            self._page.set_page(image, width)
            self._stack.setCurrentWidget(self._page)
        elif isinstance(content, str):
            self._text.setPlainText(content)
            self._stack.setCurrentWidget(self._text)
        elif isinstance(content, Sheet):
            self._set_table(_Cells(content, self._table))
            self._stack.setCurrentWidget(self._table)
            self._count.setText(tr("已达到预览上限") if content.truncated else "")
            self._count.setVisible(content.truncated)
        else:
            self._book = content
            self._sheet_names.show()
            self._sheet_names.addItems([name for name, _ in content.sheets])

    def _set_table(self, model):
        previous = self._table.model()
        self._table.setModel(model)
        if previous is not None:
            previous.deleteLater()

    def _select_sheet(self, index):
        if self._book is None or index < 0:
            return
        path, book = self._path, self._book
        self._stack.setCurrentWidget(self._notice)
        self._count.hide()
        self._submit(lambda: read_sheet(path, book, index))

    def _open_pdf(self, path):
        try:
            script = asset_path("preview", "render_pdf.ps1").read_text(encoding="utf-8")
        except OSError as exc:
            self.failed.emit(str(exc))
            return
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        try:
            self._pdf = subprocess.Popen(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={**os.environ, "ZIPMERGE_PDF_PATH": str(path.resolve())},
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self.failed.emit(str(exc))
            return
        self._select_page(1)

    def _select_page(self, number):
        process = self._pdf
        if process is None:
            return

        def render():
            try:
                process.stdin.write(f"{number - 1}\n".encode("ascii"))
                process.stdin.flush()
                index, total, width, data = process.stdout.readline().split(maxsplit=3)
                if int(index) != number - 1:
                    raise ValueError
                return number, int(total), int(width), base64.b64decode(data)
            except (OSError, ValueError) as exc:
                raise ValueError(tr("Windows 无法渲染此 PDF，请检查文件是否已加密或损坏")) from exc

        self._submit(render)
