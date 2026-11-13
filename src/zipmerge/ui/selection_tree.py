"""Two-stage keyboard selection for the workspace trees."""

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QTreeView


class DirectorySelectionTree(QTreeView):
    copy_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_next = False

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self._all_next = False
            event.accept()
            if not event.isAutoRepeat():
                self.copy_requested.emit()
            return
        if not event.matches(QKeySequence.StandardKey.SelectAll):
            if event.key() != Qt.Key.Key_Control:
                self._all_next = False
            return super().keyPressEvent(event)
        event.accept()
        if event.isAutoRepeat():
            return
        model = self.model()
        current = self.currentIndex().siblingAtColumn(0)
        whole = self._all_next
        parent = current.parent() if current.isValid() else QModelIndex()
        if not whole and current.isValid() and self.is_directory_index(current):
            parent = current
            self.expand(current)
        parents = [QModelIndex() if whole else parent]
        ranges = QItemSelection()
        while parents:
            p = parents.pop()
            rows = model.rowCount(p)
            if rows:
                ranges.select(model.index(0, 0, p), model.index(rows - 1, model.columnCount(p) - 1, p))
                if whole:
                    parents.extend(i for r in range(rows) if model.rowCount(i := model.index(r, 0, p)))
        self.selectionModel().select(ranges, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self._all_next = True

    def is_directory_index(self, index):
        return self.model().node(index).is_dir

    def selectionChanged(self, selected, deselected):
        self._all_next = False
        super().selectionChanged(selected, deselected)

    def currentChanged(self, current, previous):
        self._all_next = False
        super().currentChanged(current, previous)

    def mousePressEvent(self, event):
        self._all_next = False
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        self._all_next = False
        super().focusOutEvent(event)

    def reset(self):
        self._all_next = False
        super().reset()
