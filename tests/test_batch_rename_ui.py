import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from zipmerge.engine import create_archive
from zipmerge.ui.batch_rename import BatchRenameDialog, NameEditor
from zipmerge.ui.main_window import MainWindow
from zipmerge.util.settings import KEY_TEMP_ROOT, Settings


class BatchRenameUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        settings = Settings("ZipMergeTest", "BatchRename")
        settings._store = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        settings.set_value(KEY_TEMP_ROOT, str(root / "work"))
        self.window = MainWindow(settings=settings)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.session = self.window.current_tab().session
        self.panel = self.window.current_tab().target_panel
        # Offscreen QtTest can leave its synthetic Ctrl modifier active after a
        # chord closes an editor or opens a menu. Release it before the next test.
        self.addCleanup(QTest.keyRelease, self.panel.view, Qt.Key.Key_Control)
        src = root / "src"
        src.mkdir()
        for name in ("皮革 脚步 01.wav", "衣物 脚步 02.wav", "金属 脚步 03.wav"):
            (src / name).write_bytes(name.encode())
        archive = root / "脚步素材.zip"
        create_archive(archive, "zip", src)
        self.wrapper = self.session.import_archive(archive)
        self.nodes = self.wrapper.children.copy()
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        self.panel.view.expandAll()
        self.panel.view.setFocus()
        self.app.processEvents()

    def dialog(self):
        dialog = BatchRenameDialog(self.session, self.nodes, self.window)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        return dialog

    def test_rule_surface_tracks_app_theme_with_opposite_system_palette(self):
        from PySide6.QtGui import QColor, QPalette
        from PySide6.QtWidgets import QScrollArea
        old = self.app.palette()
        sheet = self.app.styleSheet()
        try:
            dialog = self.dialog()
            content = dialog.findChild(QScrollArea).widget()
            for mode, opposite in (("dark", "#efefef"), ("light", "#353535")):
                self.app.setStyleSheet("")
                palette = QPalette(old)
                palette.setColor(QPalette.ColorRole.Window, QColor(opposite))
                self.app.setPalette(palette)
                self.window.theme.set_mode(mode)
                self.window.theme.apply_to(self.app)
                self.app.processEvents()
                image = content.grab().toImage()
                self.assertEqual(image.pixelColor(image.width()//2, image.height()-4).name(),
                                 self.window.theme.palette().base.lower())
        finally:
            self.app.setPalette(old)
            self.app.setStyleSheet(sheet)

    def test_shortcuts_button_and_context_share_selection(self):
        view = self.panel.view
        self.assertFalse(self.panel.batch_button.isEnabled())
        index = self.panel.model.index_for_node(self.nodes[0])
        view.setCurrentIndex(index)
        QTest.keyClick(view, Qt.Key.Key_F2)
        self.assertIsInstance(self.app.focusWidget(), QLineEdit)
        QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Escape)
        selection = view.selectionModel()
        selection.select(self.panel.model.index_for_node(self.nodes[1]),
                         QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.assertTrue(self.panel.batch_button.isEnabled())
        seen = []

        def record(dialog):
            seen.append(dialog.original_order)
            return 0

        menu_actions = []
        def choose_batch_action():
            menu = self.app.activePopupWidget()
            menu_actions.extend(menu.actions())
            menu.close()
            for action in menu_actions:
                if action.text() == "批量重命名":
                    action.trigger()

        with patch.object(BatchRenameDialog, "exec", record):
            QTest.keyClick(view, Qt.Key.Key_F2, Qt.KeyboardModifier.ControlModifier)
            self.panel.batch_button.click()
            QTimer.singleShot(0, choose_batch_action)
            self.panel._on_context_menu(view.visualRect(index).center())
        self.assertIn(self.panel.batch_action, menu_actions)
        self.assertEqual(seen, [tuple(self.nodes[:2])] * 3)

    def test_preview_rules_numbering_exclusion_and_apply(self):
        d = self.dialog()
        d.rules[0].find.setText("脚步")
        d.rules[0].replacement.setText("Step")
        d._add_rule()
        d.rules[1].find.setText("Step")
        d.rules[1].replacement.setText("Foot")
        self.assertIn("Foot", d.model.names[self.nodes[0]])
        d.rules[1].remove.click()
        self.assertIn("Step", d.model.names[self.nodes[0]])
        d.tabs.setCurrentIndex(1)
        d.position.setCurrentIndex(2)
        d.kind.setCurrentIndex(1)
        d.prefix.setText("_")
        d.width.setValue(2)
        d.sort.setCurrentIndex(2)
        first, excluded, last = d.model.rows
        d.model.setData(d.model.index(1, 0), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        self.assertTrue(d.model.names[first].endswith("_01.wav"))
        self.assertTrue(d.model.names[last].endswith("_02.wav"))
        self.assertNotIn(excluded, d.model.names)
        d.kind.setCurrentIndex(2)
        d.info.setCurrentIndex(1)
        self.assertTrue(d.model.names[first].endswith("脚步素材.wav"))
        d.kind.setCurrentIndex(1)
        original = [n.name for n in self.nodes]
        d.apply_button.click()
        self.assertTrue(first.name.endswith("_01.wav"))
        self.assertEqual(self.session.undo.undo_label, "批量重命名")
        self.session.undo.undo()
        self.assertEqual([n.name for n in self.nodes], original)

    def test_manual_edit_paste_conflict_and_cancel(self):
        d = self.dialog()
        d.tabs.setCurrentIndex(2)
        index = d.model.index(0, 1)
        d.table.setCurrentIndex(index)
        d.table.edit(index)
        editor = d.table.findChild(NameEditor)
        QTest.keyClicks(editor, "Demo.wav")
        self.assertEqual(d.model.names[self.nodes[0]], "Demo.wav")
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(d.apply_button.isEnabled())
        d.table.edit(index)
        editor = self.app.focusWidget()
        old_clip = self.app.clipboard().text()
        self.addCleanup(self.app.clipboard().setText, old_clip)
        self.app.clipboard().setText("鼓.wav\r\n鼓.wav\r\n尾音.wav")
        QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertEqual(list(d.model.names.values()), ["鼓.wav", "鼓.wav", "尾音.wav"])
        self.assertFalse(d.apply_button.isEnabled())
        self.assertEqual(len(d.model.errors), 2)
        d.table.setCurrentIndex(d.model.index(1, 1))
        self.app.clipboard().setText("鼓二.wav")
        QTest.keyClick(d.table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(d.apply_button.isEnabled())
        d.reject()
        self.assertTrue(all("脚步" in n.name for n in self.nodes))
