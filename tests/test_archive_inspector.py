"""Inspect real generated archives without extraction or session mutation."""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView

from zipmerge.core import MergeSession
from zipmerge.engine import create_archive
from zipmerge.ui.archive_inspector import ArchiveInspector
from zipmerge.ui.design.tokens import palette_for


class ArchiveInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-inspect-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        source = self.tmp / "来源"
        (source / "中文目录" / "空目录").mkdir(parents=True)
        (source / "中文目录" / "检查.exe").write_bytes(b"never execute")
        self.source = source

    def _wait(self, dialog):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not dialog._start_timer.isActive() and not dialog._worker.isRunning():
                QTest.qWait(10)
                return
            QTest.qWait(5)
        dialog._worker.cancel()
        dialog._worker.wait(5000)
        self.fail("archive listing did not finish")

    def test_zip_and_encrypted_7z_list_actual_contents_without_execution_or_import(self):
        for fmt, password in (("zip", None), ("7z", "中文密码")):
            with self.subTest(fmt=fmt):
                archive = self.tmp / f"实际交付.{fmt}"
                create_archive(archive, fmt, self.source, password=password)
                before = archive.read_bytes()
                dialog = ArchiveInspector(archive, palette_for("dark"), password=password)
                self.addCleanup(dialog.deleteLater)
                with (
                    mock.patch("PySide6.QtGui.QDesktopServices.openUrl") as external,
                    mock.patch.object(MergeSession, "import_archive") as imported,
                    mock.patch("zipmerge.engine.extract_entries") as extracted,
                ):
                    dialog.show()
                    self._wait(dialog)
                    root = dialog.model.item(0)
                    self.assertEqual(root.text(), "中文目录")
                    names = {root.child(i).text() for i in range(root.rowCount())}
                    self.assertEqual(names, {"空目录", "检查.exe"})
                    index = root.child(next(i for i in range(root.rowCount()) if root.child(i).text() == "检查.exe")).index()
                    self.assertFalse(dialog.model.flags(index) & Qt.ItemFlag.ItemIsEditable)
                    self.assertFalse(dialog.model.flags(index) & Qt.ItemFlag.ItemIsDragEnabled)
                    self.assertEqual(dialog.view.dragDropMode(), QAbstractItemView.DragDropMode.NoDragDrop)
                    dialog.view.expandAll()
                    self.app.processEvents()
                    QTest.mouseDClick(dialog.view.viewport(), Qt.MouseButton.LeftButton, pos=dialog.view.visualRect(index).center())
                    external.assert_not_called()
                    imported.assert_not_called()
                    extracted.assert_not_called()
                dialog.reject()
                self.assertEqual(archive.read_bytes(), before)
                self.assertIn("1 个文件", dialog._summary.text())

    def test_invalid_archive_reports_error_in_its_own_window(self):
        archive = self.tmp / "损坏.zip"
        archive.write_bytes(b"invalid archive")
        dialog = ArchiveInspector(archive, palette_for("light"))
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        self._wait(dialog)
        self.assertIn("无法读取", dialog._summary.text())
        self.assertEqual(dialog.model.rowCount(), 0)
        dialog.reject()

    def test_close_before_worker_start_does_not_start_listing(self):
        dialog = ArchiveInspector(self.tmp / "unused.zip", palette_for("dark"))
        dialog.reject()
        self.app.processEvents()
        self.assertFalse(dialog._worker.isRunning())
        self.assertFalse(dialog._start_timer.isActive())
        dialog.deleteLater()

    def test_close_during_listing_cancels_worker_before_dialog_finishes(self):
        def slow_listing(_path, password=None, *, cancel=None):
            from zipmerge.engine import ArchiveCancelled

            cancel.wait(5)
            raise ArchiveCancelled("cancelled")

        with mock.patch("zipmerge.ui.source_panel.list_with_info", slow_listing):
            dialog = ArchiveInspector(self.tmp / "slow.zip", palette_for("dark"))
            dialog.show()
            deadline = time.monotonic() + 2
            while not dialog._worker.isRunning() and time.monotonic() < deadline:
                QTest.qWait(5)
            self.assertTrue(dialog._worker.isRunning())
            dialog.reject()
            self._wait(dialog)
            self.assertFalse(dialog.isVisible())
            dialog.deleteLater()
