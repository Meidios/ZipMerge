import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zipmerge.engine import create_archive
from zipmerge.ui.main_window import MainWindow
from zipmerge.util.settings import KEY_TEMP_ROOT, Settings


class TreeShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.d = Path(temp.name)
        settings = Settings("ZipMergeTest", "TreeClipboard")
        settings._store = QSettings(str(self.d/"prefs.ini"), QSettings.Format.IniFormat)
        settings.set_value(KEY_TEMP_ROOT, str(self.d/"sessions"))
        self.w = MainWindow(settings)
        self.addCleanup(self.w.deleteLater)
        self.addCleanup(self.w.close)
        self.addCleanup(self.app.clipboard().clear)
        self.w.show()
        self.tab = self.w.current_tab()
        self.p = self.tab.target_panel
        src = self.d/"输入"
        (src/"对白"/"子目录").mkdir(parents=True)
        (src/"对白"/"子目录"/"深层.txt").write_bytes(b"nested")
        (src/"对白"/"音频.wav").write_bytes(b"sound")
        (src/"其他.txt").write_bytes(b"other")
        self.archive = self.d/"源包.zip"
        create_archive(self.archive, "zip", src)
        self.wrapper = self.tab.session.import_archive(self.archive)
        self.tab.source_panel.refresh()
        self.folder = next(n for n in self.wrapper.children if n.is_dir)
        self.file = next(n for n in self.folder.children if not n.is_dir)
        self.app.processEvents()
        self.p.view.expandAll()
        self.p.view.setFocus()
        self.w.activateWindow()
        self.app.processEvents()

    def select(self, node):
        self.p.view.setCurrentIndex(self.p.model.index_for_node(node))

    def chord(self, key, view=None):
        QTest.keyClick(view or self.p.view, key, Qt.KeyboardModifier.ControlModifier)

    def test_scope_including_collapsed_descendants_and_reset(self):
        self.select(self.file)
        self.p.view.collapse(self.p.model.index_for_node(self.folder.children[0]))
        self.chord(Qt.Key.Key_A)
        self.assertEqual(set(self.p.selected_nodes()), set(self.folder.children))
        self.chord(Qt.Key.Key_A)
        self.assertEqual(set(self.p.selected_nodes()), set(self.wrapper.walk()))
        self.select(self.folder)
        self.chord(Qt.Key.Key_A)
        self.assertEqual(set(self.p.selected_nodes()), set(self.folder.children))

    def test_copy_external_and_internal_and_failed_replacement(self):
        with patch("zipmerge.ui.drag_export.QStandardPaths.writableLocation", return_value=str(self.d/"cache")):
            self.select(self.folder)
            self.chord(Qt.Key.Key_C)
            urls = self.app.clipboard().mimeData().urls()
            self.assertEqual(len(urls), 1)
            path = Path(urls[0].toLocalFile())
            self.assertEqual((path/"音频.wav").read_bytes(), b"sound")
            self.assertEqual((path/"子目录"/"深层.txt").read_bytes(), b"nested")
            with patch.object(self.p._drag_export, "_run", return_value=None):
                self.select(self.file)
                self.chord(Qt.Key.Key_C)
                self.assertEqual(self.app.clipboard().mimeData().urls(), urls)
            dest = self.tab.session.new_folder(self.tab.session.root, "放这里")
            self.app.processEvents()
            self.select(dest)
            self.chord(Qt.Key.Key_V)
            self.assertEqual([n.name for n in dest.children], ["对白"])
            self.assertIsNot(dest.children[0], self.folder)
            self.w._close_tab(0)
            self.assertTrue(path.is_dir())

    def test_flat_source_list_copies_original_archive(self):
        panel = self.tab.source_panel
        panel.view.setFocus()
        self.chord(Qt.Key.Key_A, panel.view)
        self.chord(Qt.Key.Key_C, panel.view)
        urls = self.app.clipboard().mimeData().urls()
        self.assertEqual(len(urls), 1)
        self.assertTrue(Path(urls[0].toLocalFile()).samefile(self.archive))
