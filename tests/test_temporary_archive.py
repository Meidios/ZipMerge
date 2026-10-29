"""临时交付包的会话生命周期与跨应用拖拽卡片。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import ExportOptions, MergeSession, run_export  # noqa: E402
from zipmerge.engine import create_archive, extract_entries  # noqa: E402
from zipmerge.ui.temporary_archive import (  # noqa: E402
    STATE_MISSING,
    STATE_READY,
    STATE_STALE,
    TemporaryArchiveCard,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


class TemporaryArchiveSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-delivery-session-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="客户交付", temp_root=self.tmp)
        self.addCleanup(self.session.close)

    def _candidate(self, name: str = "项目_中文交付.zip", data: bytes = b"archive") -> Path:
        path = self.session.create_temporary_output(name)
        path.write_bytes(data)
        return path

    def test_generation_paths_are_unique_but_keep_customer_filename(self) -> None:
        first = self._candidate()
        second = self._candidate()
        self.assertNotEqual(first.parent, second.parent)
        self.assertEqual(first.name, second.name)
        self.assertEqual(first.parent.parent, self.session.deliveries_dir)

    def test_commit_replaces_current_and_cleans_older_generation(self) -> None:
        first = self._candidate(data=b"one")
        self.session.commit_temporary_archive(first)
        second = self._candidate(data=b"two")
        self.session.commit_temporary_archive(second)

        self.assertEqual(self.session.temporary_archive, second.resolve())
        self.assertTrue(second.exists())
        self.assertFalse(first.parent.exists())

    @unittest.skipUnless(os.name == "nt", "Windows 8.3 aliases are platform-specific")
    def test_short_path_alias_keeps_real_chinese_zip_and_7z_deliveries(self) -> None:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
        get_short.restype = ctypes.c_uint
        length = get_short(str(self.tmp), buffer, len(buffer))
        if not length or length >= len(buffer):
            self.skipTest("The test volume does not provide an 8.3 path alias")
        alias = Path(buffer.value)
        if alias == self.tmp.resolve():
            self.skipTest("The test volume does not provide a distinct 8.3 path alias")
        session = MergeSession(name="短路径交付", temp_root=alias)
        self.addCleanup(session.close)
        source = self.tmp / "中文音效.wav"
        source.write_bytes(b"unchanged-audio-source")
        session.import_disk_paths([source])
        previous = None
        for archive_format in ("zip", "7z"):
            with self.subTest(archive_format=archive_format):
                output = session.create_temporary_output(f"中文客户交付.{archive_format}")
                result = run_export(session.root, session.sources, ExportOptions(
                    output_path=output, fmt=archive_format, stage_dir=session.stage_dir))
                session.commit_temporary_archive(result.output_path)
                self.assertTrue(output.is_file(), "Committing an aliased generation must retain its archive")
                self.assertEqual(session.temporary_archive, output.resolve())
                self.assertEqual(len(list(session.deliveries_dir.iterdir())), 1)
                if previous is not None:
                    self.assertFalse(previous.parent.exists())
                extracted = self.tmp / f"短路径解包-{archive_format}"
                extract_entries(output, None, extracted)
                self.assertEqual((extracted / source.name).read_bytes(), source.read_bytes())
                session.discard_temporary_output(output)
                self.assertTrue(output.exists(), "Discarding the committed output must preserve it")
                previous = output

    def test_discard_keeps_already_committed_archive(self) -> None:
        current = self._candidate(data=b"current")
        self.session.commit_temporary_archive(current)
        failed = self._candidate(data=b"partial")
        self.session.discard_temporary_output(failed)

        self.assertTrue(current.exists())
        self.assertFalse(failed.parent.exists())
        self.assertEqual(self.session.temporary_archive, current.resolve())

    def test_clear_removes_deliveries_without_removing_session(self) -> None:
        current = self._candidate()
        self.session.commit_temporary_archive(current)
        self.session.clear_temporary_archives()
        self.assertIsNone(self.session.temporary_archive)
        self.assertFalse(self.session.deliveries_dir.exists())
        self.assertTrue(self.session.temp_dir.exists())

    def test_rejects_unsafe_or_external_paths(self) -> None:
        with self.assertRaises(ValueError):
            self.session.create_temporary_output("../escape.zip")
        outside = self.tmp / "outside.zip"
        outside.write_bytes(b"x")
        with self.assertRaises(ValueError):
            self.session.commit_temporary_archive(outside)

    def test_real_export_inside_generation_keeps_chinese_name_and_content(self) -> None:
        source_dir = self.tmp / "来源"
        (source_dir / "音效").mkdir(parents=True)
        (source_dir / "音效" / "爆炸.wav").write_bytes(b"wave-data")
        source_archive = self.tmp / "来源包.zip"
        create_archive(source_archive, "zip", source_dir)
        self.session.import_archive(source_archive)

        for archive_format in ("zip", "7z"):
            with self.subTest(archive_format=archive_format):
                output = self.session.create_temporary_output(f"客户交付.{archive_format}")
                result = run_export(
                    self.session.root,
                    self.session.sources,
                    ExportOptions(
                        output_path=output,
                        fmt=archive_format,
                        stage_dir=self.session.stage_dir,
                    ),
                )
                self.session.commit_temporary_archive(result.output_path)
                extracted = self.tmp / f"verify-{archive_format}"
                extract_entries(result.output_path, None, extracted)
                files = [path for path in extracted.rglob("爆炸.wav") if path.is_file()]
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].read_bytes(), b"wave-data")


class TemporaryArchiveCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-delivery-card-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "客户包.zip"
        self.path.write_bytes(b"123456")
        self.card = TemporaryArchiveCard()
        self.addCleanup(self.card.deleteLater)

    def test_preview_button_inspects_actual_archive_without_external_open_or_drag(self) -> None:
        self.card.set_archive(self.path, password="session-only")
        self.card.resize(600, 64)
        self.card.show()
        _app.processEvents()
        with (
            mock.patch("zipmerge.ui.temporary_archive.ArchiveInspector") as inspector,
            mock.patch("PySide6.QtGui.QDesktopServices.openUrl") as external,
            mock.patch.object(self.card, "start_drag") as dragged,
        ):
            QTest.mouseClick(self.card._preview_button, Qt.MouseButton.LeftButton)
        inspector.assert_called_once_with(
            self.path.resolve(), self.card._palette, password="session-only", parent=self.card
        )
        inspector.return_value.exec.assert_called_once()
        inspector.return_value.deleteLater.assert_called_once()
        external.assert_not_called()
        dragged.assert_not_called()

    def test_preview_rejects_stale_output_and_uses_regenerated_archive(self) -> None:
        self.card.set_archive(self.path)
        self.card.mark_stale()
        self.assertFalse(self.card._preview_button.isEnabled())
        with mock.patch("zipmerge.ui.temporary_archive.ArchiveInspector") as inspector:
            self.card.preview_archive()
            inspector.assert_not_called()
            replacement = self.tmp / "客户包_v2.7z"
            replacement.write_bytes(b"new archive")
            self.card.set_archive(replacement)
            self.assertTrue(self.card._preview_button.isEnabled())
            self.card._preview_button.click()
        self.assertEqual(inspector.call_args.args[0], replacement.resolve())
        self.assertTrue(self.path.exists())

    def test_preview_detects_deleted_file_and_disables_button(self) -> None:
        self.card.set_archive(self.path)
        self.path.unlink()
        with mock.patch("zipmerge.ui.temporary_archive.ArchiveInspector") as inspector:
            self.card._preview_button.click()
        inspector.assert_not_called()
        self.assertEqual(self.card.state, STATE_MISSING)
        self.assertFalse(self.card._preview_button.isEnabled())

    def test_preview_is_disabled_before_generation_and_after_clear(self) -> None:
        self.assertFalse(self.card._preview_button.isEnabled())
        self.card.set_archive(self.path, password="session-only")
        self.card.clear_archive()
        self.assertFalse(self.card._preview_button.isEnabled())
        self.assertIsNone(self.card._password)
        with mock.patch("zipmerge.ui.temporary_archive.ArchiveInspector") as inspector:
            self.card.preview_archive()
        inspector.assert_not_called()
        self.assertTrue(self.path.exists())

    def test_ready_card_exposes_one_local_file_url(self) -> None:
        self.card.set_archive(self.path)
        mime = self.card.mime_data()
        self.assertEqual(self.card.state, STATE_READY)
        self.assertTrue(self.card.is_draggable)
        self.assertIsNotNone(mime)
        urls = mime.urls()
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].isLocalFile())
        self.assertEqual(Path(urls[0].toLocalFile()), self.path.resolve())
        self.assertIn("6 B", self.card._detail.text())

    def test_stale_card_is_visible_but_not_draggable(self) -> None:
        self.card.set_archive(self.path)
        self.card.mark_stale()
        self.assertEqual(self.card.state, STATE_STALE)
        self.assertFalse(self.card.is_draggable)
        self.assertIsNone(self.card.mime_data())
        self.assertIn("重新生成", self.card._detail.text())

    def test_missing_file_is_detected_before_drag(self) -> None:
        self.card.set_archive(self.path)
        self.path.unlink()
        self.assertIsNone(self.card.mime_data())
        self.assertEqual(self.card.state, STATE_MISSING)
        self.assertFalse(self.card.is_draggable)

    def test_drag_only_offers_copy_action(self) -> None:
        calls: dict[str, object] = {}

        class FakeDrag:
            def __init__(self, source) -> None:
                calls["source"] = source

            def setMimeData(self, mime) -> None:  # noqa: N802
                calls["mime"] = mime

            def setPixmap(self, pixmap) -> None:  # noqa: N802
                calls["pixmap"] = pixmap

            def setHotSpot(self, point) -> None:  # noqa: N802
                calls["hotspot"] = point

            def exec(self, supported, default):
                calls["actions"] = (supported, default)
                return default

        self.card.set_archive(self.path)
        with mock.patch("zipmerge.ui.temporary_archive.QDrag", FakeDrag):
            result = self.card.start_drag()

        self.assertEqual(
            calls["actions"],
            (Qt.DropAction.CopyAction, Qt.DropAction.CopyAction),
        )
        self.assertEqual(result, Qt.DropAction.CopyAction)
        self.assertEqual(Path(calls["mime"].urls()[0].toLocalFile()), self.path.resolve())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
