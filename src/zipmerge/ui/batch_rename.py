"""Preview and edit a set of names before applying one virtual tree command."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox, QStackedWidget, QStyledItemDelegate, QTabWidget,
    QTableView, QVBoxLayout, QWidget,
)

from zipmerge.i18n import tr
from ..core.batch_rename import ReplaceRule, insert_name, name_parts, rename_errors, replace_name, sequence_number
from ..core.nodes import TargetNode
from ..core.session import MergeSession


def _combo(options):
    box = QComboBox()
    for key, label in options:
        box.addItem(label, key)
    return box


def _spin(value=1, maximum=999999):
    field = QSpinBox()
    field.setRange(1, maximum)
    field.setValue(value)
    return field


class RuleRow(QWidget):
    changed = Signal()
    removed = Signal()

    def __init__(self):
        super().__init__()
        self.mode = _combo([
            ("literal", tr("指定文本")), ("first", tr("前 N 位")), ("last", tr("后 N 位")),
            ("range", tr("从第 X 位开始的 N 位")), ("after", tr("某字符之后的内容")),
            ("before", tr("某字符之前的内容")), ("after_n", tr("某字符之后的 N 位")),
            ("before_n", tr("某字符之前的 N 位")),
        ])
        self.find = QLineEdit()
        self.replacement = QLineEdit()
        self.replacement.setPlaceholderText(tr("替换成（留空则删除）"))
        self.start = _spin()
        self.start.setPrefix(tr("第 "))
        self.start.setSuffix(tr(" 位"))
        self.count = _spin()
        self.count.setSuffix(tr(" 位"))
        self.remove = QPushButton(tr("移除"))
        self.remove.clicked.connect(lambda: self.removed.emit())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in (self.mode, self.find, self.start, self.count, self.replacement, self.remove):
            layout.addWidget(widget)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        for signal in (self.find.textChanged, self.replacement.textChanged,
                       self.start.valueChanged, self.count.valueChanged):
            signal.connect(lambda: self.changed.emit())
        self._mode_changed()

    def _mode_changed(self):
        mode = self.mode.currentData()
        self.find.setVisible(mode not in ("first", "last", "range"))
        self.find.setPlaceholderText(tr("查找文本") if mode == "literal" else tr("定位字符（首次出现）"))
        self.start.setVisible(mode == "range")
        self.count.setVisible(mode in ("first", "last", "range", "before_n", "after_n"))
        self.changed.emit()

    def rule(self):
        return ReplaceRule(self.mode.currentData(), self.find.text(), self.replacement.text(),
                           self.start.value(), self.count.value())


class PreviewModel(QAbstractTableModel):
    edited = Signal()

    def __init__(self, nodes, parent):
        super().__init__(parent)
        self.rows = list(nodes)
        self.included = set(nodes)
        self.names = {n: n.name for n in nodes}
        self.errors = {}
        self.manual = {}
        self.editable = False

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return (tr("原名称"), tr("新名称"), tr("状态"))[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if node in self.included else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.errors.get(node) or node.path()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == 0:
                return node.name
            if col == 1:
                return self.names.get(node, node.name)
            if node not in self.included:
                return tr("已排除")
            return self.errors.get(node) or (tr("将改名") if self.names[node] != node.name else tr("不变"))
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.column() == 0:
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        if index.column() == 1 and self.editable and self.rows[index.row()] in self.included:
            return flags | Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        node = self.rows[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            if Qt.CheckState(value) == Qt.CheckState.Checked:
                self.included.add(node)
            else:
                self.included.discard(node)
        elif role == Qt.ItemDataRole.EditRole and index.column() == 1 and self.editable:
            if self.manual.get(node, node.name) == value:
                return False
            self.manual[node] = value
        else:
            return False
        self.edited.emit()
        return True

    def paste_lines(self, row, lines):
        targets = (n for n in self.rows[row:] if n in self.included)
        self.manual.update(zip(targets, lines))
        self.edited.emit()


class NameEditor(QLineEdit):
    pasted = Signal(list)
    reverted = Signal()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            text = QApplication.clipboard().text()
            if "\n" in text or "\r" in text:
                self.pasted.emit(text.splitlines())
                return
        super().keyPressEvent(event)


class NameDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = NameEditor(parent)
        model = index.model()
        original = index.data(Qt.ItemDataRole.EditRole)

        def paste(lines):
            model.paste_lines(index.row(), lines)
            self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)

        editor.pasted.connect(paste)
        editor.textEdited.connect(lambda value: model.setData(index, value))
        editor.reverted.connect(lambda: model.setData(index, original))
        editor.setText(original)
        editor.selectAll()
        return editor

    def setEditorData(self, editor, index):
        # Preview changes can repaint the active cell. Keep the user's cursor intact.
        pass

    def eventFilter(self, editor, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            editor.reverted.emit()
        return super().eventFilter(editor, event)


class PreviewTable(QTableView):
    def keyPressEvent(self, event):
        if self.model().editable and event.matches(QKeySequence.StandardKey.Paste):
            row = max(self.currentIndex().row(), 0)
            self.model().paste_lines(row, QApplication.clipboard().text().splitlines())
            return
        super().keyPressEvent(event)


class BatchRenameDialog(QDialog):
    def __init__(self, session: MergeSession, nodes: list[TargetNode], parent=None):
        super().__init__(parent)
        self.session = session
        self.original_order = tuple(nodes)
        self.model = PreviewModel(nodes, self)
        self.setWindowTitle(tr("批量重命名"))
        self.resize(920, 580)
        self.tabs = QTabWidget()
        self.rules = []
        self.tabs.addTab(self._replace_page(), tr("查找替换"))
        self.tabs.addTab(self._insert_page(), tr("插入内容"))
        manual = QWidget()
        manual_layout = QVBoxLayout(manual)
        manual_layout.addWidget(QLabel(tr("双击新名称可编辑完整文件名。粘贴多行文本（如 Excel 的一列）会从当前行向下填写。")))
        manual_layout.addStretch()
        self.tabs.addTab(manual, tr("手动编辑"))

        self.table = PreviewTable()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(1, NameDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.sort = _combo([("tree", tr("目标树顺序")), ("asc", tr("按名称升序")), ("desc", tr("按名称降序"))])
        self.summary = QLabel()
        self.apply_button = QPushButton(tr("执行重命名"))
        self.apply_button.setProperty("variant", "primary")
        cancel = QPushButton(tr("取消"))
        self.apply_button.clicked.connect(self._apply)
        cancel.clicked.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(self.sort)
        footer.addWidget(self.summary)
        footer.addStretch()
        footer.addWidget(cancel)
        footer.addWidget(self.apply_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(self.table, 1)
        layout.addLayout(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.tabs.currentChanged.connect(self.refresh)
        self.model.edited.connect(self.refresh)
        self.sort.currentIndexChanged.connect(self._sort_rows)
        self._add_rule()

    def _replace_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setMinimumHeight(self.fontMetrics().height() * 5)
        contents = QWidget()
        self.rule_layout = QVBoxLayout(contents)
        self.rule_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        area.setWidget(contents)
        layout.addWidget(area)
        bar = QHBoxLayout()
        self.extension = QCheckBox(tr("替换文件扩展名"))
        self.extension.toggled.connect(self.refresh)
        add = QPushButton(tr("添加替换规则"))
        add.clicked.connect(self._add_rule)
        bar.addWidget(self.extension)
        bar.addStretch()
        bar.addWidget(add)
        layout.addLayout(bar)
        return page

    def _add_rule(self):
        row = RuleRow()
        row.remove.setAutoDefault(False)
        row.changed.connect(self.refresh)
        row.removed.connect(lambda: self._remove_rule(row))
        self.rule_layout.addWidget(row)
        self.rules.append(row)
        self.rules[0].remove.setEnabled(len(self.rules) > 1)
        self.refresh()

    def _remove_rule(self, row):
        self.rules.remove(row)
        self.rule_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.rules[0].remove.setEnabled(len(self.rules) > 1)
        self.refresh()

    def _insert_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.kind = _combo([("text", tr("文本")), ("seq", tr("序号")), ("info", tr("文件信息"))])
        self.position = _combo([("start", tr("开头")), ("position", tr("指定位置")), ("end", tr("末尾"))])
        self.pos = _spin()
        self.pos.setPrefix(tr("第 "))
        self.pos.setSuffix(tr(" 位之前"))
        bar.addWidget(QLabel(tr("内容")))
        bar.addWidget(self.kind)
        bar.addStretch()
        bar.addWidget(QLabel(tr("位置")))
        bar.addWidget(self.position)
        bar.addWidget(self.pos)
        layout.addLayout(bar)
        stack = QStackedWidget()
        text_page = QWidget()
        form = QFormLayout(text_page)
        self.fixed = QLineEdit()
        self.fixed.setPlaceholderText(tr("例如：MEI_ 或 _Loop"))
        form.addRow(tr("固定文本"), self.fixed)
        stack.addWidget(text_page)
        seq_page = QWidget()
        form = QFormLayout(seq_page)
        self.prefix, self.suffix = QLineEdit(), QLineEdit()
        self.prefix.setPlaceholderText(tr("序号前的字符"))
        self.suffix.setPlaceholderText(tr("序号后的字符"))
        affixes = QHBoxLayout()
        affixes.addWidget(self.prefix)
        affixes.addWidget(self.suffix)
        form.addRow(tr("修饰字符"), affixes)
        seq = QHBoxLayout()
        self.style_box = _combo([("decimal", tr("阿拉伯数字")), ("chinese", tr("中文小写数字")),
                                 ("financial", tr("中文大写数字")), ("lower", tr("英文小写字母")),
                                 ("upper", tr("英文大写字母"))])
        self.start = _spin()
        self.width = _spin(1, 12)
        self.width.setSuffix(tr(" 位"))
        for w in (self.style_box, QLabel(tr("开始序号")), self.start, QLabel(tr("补零至")), self.width):
            seq.addWidget(w)
        form.addRow(tr("序号类型"), seq)
        stack.addWidget(seq_page)
        info_page = QWidget()
        form = QFormLayout(info_page)
        self.info = _combo([("parent", tr("所在目标文件夹名")), ("source", tr("来源压缩包名")),
                            ("extension", tr("扩展名（不含点）"))])
        form.addRow(tr("文件信息"), self.info)
        stack.addWidget(info_page)
        layout.addWidget(stack)
        self.kind.currentIndexChanged.connect(stack.setCurrentIndex)
        self.pos.hide()
        self.position.currentIndexChanged.connect(lambda: self.pos.setVisible(self.position.currentData() == "position"))
        self.style_box.currentIndexChanged.connect(lambda: self.width.setEnabled(self.style_box.currentData() == "decimal"))
        for box in (self.kind, self.position, self.info, self.style_box):
            box.currentIndexChanged.connect(self.refresh)
        for spin in (self.pos, self.start, self.width):
            spin.valueChanged.connect(self.refresh)
        for field in (self.fixed, self.prefix, self.suffix):
            field.textChanged.connect(self.refresh)
        return page

    def _inserted_content(self, node, index):
        kind = self.kind.currentData()
        if kind == "text":
            return self.fixed.text()
        if kind == "seq":
            number = sequence_number(self.start.value() + index, self.style_box.currentData(), self.width.value())
            return self.prefix.text() + number + self.suffix.text()
        if self.info.currentData() == "parent":
            return node.parent.name if node.parent else ""
        if self.info.currentData() == "extension":
            return name_parts(node)[1].lstrip(".")
        if node.source and node.source.archive_path:
            return Path(node.source.archive_path).stem
        return ""

    def _sort_rows(self):
        mode = self.sort.currentData()
        self.model.beginResetModel()
        self.model.rows = list(self.original_order)
        if mode != "tree":
            self.model.rows.sort(key=lambda n: n.name.casefold(), reverse=mode == "desc")
        self.model.endResetModel()
        self.refresh()

    def refresh(self):
        model = self.model
        mode = self.tabs.currentIndex()
        model.editable = mode == 2
        rules = [row.rule() for row in self.rules]
        names = {}
        for node in model.rows:
            if node not in model.included:
                continue
            if mode == 0:
                name = replace_name(node, rules, self.extension.isChecked())
            elif mode == 1:
                content = self._inserted_content(node, len(names))
                name = insert_name(node, content, self.position.currentData(), self.pos.value())
            else:
                name = model.manual.get(node, node.name)
            names[node] = name
        model.names = names
        model.errors = rename_errors(self.session.root, names)
        if model.rows:
            model.dataChanged.emit(model.index(0, 0), model.index(len(model.rows) - 1, 2))
        changed = sum(n.name != name for n, name in names.items())
        summary = tr("已选 {0} 项 · 将改名 {1} 项", len(names), changed)
        if model.errors:
            summary += tr(" · {0} 项需修正", len(model.errors))
        self.summary.setText(summary)
        self.apply_button.setEnabled(bool(changed and not model.errors))

    def _apply(self):
        # Recheck against the live tree before entering undo history.
        self.refresh()
        if self.apply_button.isEnabled() and self.session.batch_rename(self.model.names):
            self.accept()
