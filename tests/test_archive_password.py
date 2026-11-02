"""Encrypted public directories must not bypass actual password verification."""
from __future__ import annotations

import os
from dataclasses import replace
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from zipmerge.core import (MergeSession, ExtractionSelection, build_extraction_plan,
                           MaterializeRequest, build_target_drag_root, materialize_directory)
from zipmerge.engine import ArchiveError, list_with_info, extract_entries, verify_archive_password
from zipmerge.engine.sevenzip import _run
from zipmerge.ui import archive_loader
from zipmerge.ui.source_panel import SourcePanel
from zipmerge.util.settings import Settings, KEY_REMEMBER_PASSWORD
from tests.support import sha256_file, snapshot


class ArchivePasswordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zipmerge-password-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "中文素材"
        (self.data / "目录" / "空目录").mkdir(parents=True)
        (self.data / "中文文件.txt").write_text("encrypted content\n", encoding="utf-8")
        (self.data / "目录" / "larger.bin").write_bytes(bytes(range(256)))
        self.password = "password-for-fixture"
        self.settings = Settings.__new__(Settings)
        self.settings._store = QSettings(str(self.root / "test.ini"), QSettings.Format.IniFormat)
        self.session = MergeSession(temp_root=self.root / "sessions")
        self.addCleanup(self.session.close)
        self.panel = SourcePanel(self.session, self.settings)
        self.addCleanup(self.panel.deleteLater)

    def archive(self, fmt="zip", method="ZipCrypto", headers=False):
        path = self.root / f"中文包-{fmt}-{method}-{headers}.{fmt}"
        _run(["a", f"-t{fmt}", "-y", "-sccUTF-8", "-p" + self.password,
              *(["-mem=" + method, "-mcu"] if fmt == "zip" else ["-mhe=on" if headers else "-mhe=off"]),
              "--", str(path), ".\\*"], cwd=self.data)
        return path

    def test_public_listing_does_not_validate_password_but_member_test_does(self):
        for fmt, method in (("zip", "ZipCrypto"), ("zip", "AES256"), ("7z", "LZMA2")):
            with self.subTest(fmt=fmt, method=method):
                archive = self.archive(fmt, method)
                before = sha256_file(archive)
                listing = list_with_info(archive, password="wrong")
                self.assertTrue(listing.has_encrypted_entries)
                with self.assertRaises(ArchiveError):
                    verify_archive_password(archive, listing, "wrong")
                verify_archive_password(archive, listing, self.password)
                self.assertEqual(sha256_file(archive), before)

    def test_all_supported_encryption_variants_prompt_and_store_verified_password(self):
        for fmt, method, headers in (("zip", "ZipCrypto", False), ("zip", "AES256", False),
                                     ("7z", "LZMA2", False), ("7z", "LZMA2", True)):
            with self.subTest(fmt=fmt, method=method, headers=headers):
                archive = self.archive(fmt, method, headers)
                before = sha256_file(archive)
                with mock.patch.object(archive_loader.QInputDialog, "getText", return_value=(self.password, True)) as ask:
                    self.assertEqual(self.panel.add_archives([str(archive)]), 1)
                ask.assert_called_once()
                source = next(s for s in self.session.sources.values() if s.archive_path == archive.resolve())
                self.assertEqual(source.password, self.password)
                plan = build_extraction_plan(self.session.sources, [ExtractionSelection(source.source_id)], self.root / "out")
                job = plan.jobs[0]
                extract_entries(job.archive_path, job.paths, job.output_dir, password=job.password)
                self.assertEqual(snapshot(job.output_dir), snapshot(self.data))
                self.assertEqual(sha256_file(archive), before)
        self.assertFalse(self.settings.contains("password/value"))
        self.assertEqual(self.panel.remembered_password(), "")
        self.settings.sync()
        self.assertNotIn(self.password, (self.root / "test.ini").read_text(encoding="utf-8"))

    def test_missing_encrypted_member_cannot_count_as_verified(self):
        archive = self.archive()
        listing = list_with_info(archive)
        listing.entries = [replace(entry, path="missing.txt") for entry in listing.entries if entry.encrypted]
        with self.assertRaises(ArchiveError):
            verify_archive_password(archive, listing, self.password)

    def test_wrong_password_retries_instead_of_accepting_public_directory(self):
        archive = self.archive()
        self.settings.set_value(KEY_REMEMBER_PASSWORD, True)
        with mock.patch.object(archive_loader.QInputDialog, "getText", side_effect=[("wrong", True), (self.password, True)]) as ask, \
             mock.patch.object(archive_loader.QMessageBox, "warning") as warning:
            self.assertEqual(self.panel.add_archives([str(archive)]), 1)
        self.assertEqual(ask.call_count, 2)
        warning.assert_called_once()
        self.assertEqual(self.panel.remembered_password(), self.password)
        self.settings.set_value(KEY_REMEMBER_PASSWORD, False)
        self.assertEqual(self.panel.remembered_password(), "")

    def test_cancel_is_silent_and_other_archives_still_mount(self):
        encrypted = self.archive()
        plain = self.root / "普通.zip"
        _run(["a", "-tzip", "--", str(plain), ".\\*"], cwd=self.data)
        with mock.patch.object(archive_loader.QInputDialog, "getText", return_value=("", False)), \
             mock.patch.object(archive_loader.QMessageBox, "warning") as warning:
            self.assertEqual(self.panel.add_archives([str(encrypted), str(plain)]), 1)
        warning.assert_not_called()
        self.assertEqual(len(self.session.sources), 1)
        self.assertEqual(next(iter(self.session.sources.values())).archive_path, plain.resolve())

    def test_worker_verifies_in_background_and_handles_cancel(self):
        archive = self.archive()
        calls = []
        original = archive_loader.verify_archive_password
        def verify(*args, **kwargs):
            calls.append(threading.get_ident())
            return original(*args, **kwargs)
        worker = archive_loader.ArchiveImportWorker([(str(archive), self.password)])
        with mock.patch.object(archive_loader, "verify_archive_password", side_effect=verify):
            worker.start()
            worker.wait()
        self.assertEqual(len(worker.loaded), 1)
        self.assertNotEqual(calls, [threading.get_ident()])
        cancelled = archive_loader.ArchiveImportWorker([(str(archive), self.password)])
        cancelled.cancel()
        cancelled.run()
        self.assertTrue(cancelled.was_cancelled)
        self.assertFalse(cancelled.loaded)

    def test_verified_password_reaches_preview_drag_and_export(self):
        from zipmerge.ui.preview_panel import PreviewCache
        from zipmerge.core import ExportOptions, run_export
        archive = self.archive()
        with mock.patch.object(archive_loader.QInputDialog, "getText", return_value=(self.password, True)):
            self.panel.add_archives([str(archive)])
        wrapper = self.session.root.children[0]
        file = next(n for n in wrapper.walk() if n.name == "中文文件.txt")
        cache = PreviewCache(self.root / "preview")
        path = cache.local_path(file, self.session)
        self.assertEqual(path.read_bytes(), (self.data / file.name).read_bytes())
        drag_root = build_target_drag_root([wrapper])
        result = materialize_directory(MaterializeRequest(drag_root, self.session.sources, self.root / "drag"))
        self.assertEqual(snapshot(result.paths[0]), snapshot(self.data))
        output = self.root / "交付.zip"
        run_export(self.session.root, self.session.sources, ExportOptions(output))
        extract_entries(output, None, self.root / "delivery")
        self.assertEqual(snapshot(self.root / "delivery" / wrapper.name), snapshot(self.data))

    def test_extract_here_prompts_for_public_and_private_directories(self):
        import main
        for fmt, headers in (("zip", False), ("7z", True)):
            archive = self.archive(fmt, headers=headers)
            def extract(_parent, plan):
                job = plan.jobs[0]
                self.assertEqual(job.password, self.password)
                extract_entries(job.archive_path, job.paths, job.output_dir, password=job.password)
                return object()
            with mock.patch.object(archive_loader.QInputDialog, "getText", return_value=(self.password, True)), \
                 mock.patch.object(main, "run_extraction_dialog", side_effect=extract):
                self.assertEqual(main._run_extract_here([str(archive)], self.settings), 0)
            self.assertEqual((self.root / "中文文件.txt").read_bytes(), (self.data / "中文文件.txt").read_bytes())
