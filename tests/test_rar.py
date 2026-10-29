"""RAR4 / RAR5 只读支持的真实归档回归测试。

RAR 是专有格式，随包 7-Zip 仅负责解码；ZipMerge 的输出仍只允许 zip / 7z。
测试样本来自 libarchive 公开测试套件，来源与许可证见 fixtures/libarchive/README.md。
"""

from __future__ import annotations

import binascii
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import (  # noqa: E402
    ExtractionSelection,
    MaterializeRequest,
    MergeSession,
    build_extraction_plan,
    build_source_drag_root,
    materialize_directory,
)
from zipmerge.core.exporter import ExportOptions, run_export  # noqa: E402
from zipmerge.engine import ArchiveError, extract_entries, list_with_info, test_archive  # noqa: E402
from zipmerge.ui.extraction_runner import ExtractionResult, ExtractionWorker  # noqa: E402
from zipmerge.ui.source_panel import ARCHIVE_FILTER  # noqa: E402
from zipmerge.util.paths import ARCHIVE_SUFFIXES, is_archive_path  # noqa: E402


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "libarchive"
FIXTURES = {
    "rar4": (
        "rar4-basic.rar.uu",
        "d421b86f6290aefad61b2a36737253b2b30fe27c156bd95abfc230f24fe0307e",
    ),
    "rar5": (
        "rar5-stored.rar.uu",
        "35d75e315d164d2e329afc28f7d844f013271b4fcffd4ddd78efcdd114a383a7",
    ),
}

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


def _decode_fixture(kind: str, destination: Path) -> Path:
    filename, expected_sha256 = FIXTURES[kind]
    lines = (FIXTURE_ROOT / filename).read_text(encoding="ascii").splitlines()
    if not lines or not lines[0].startswith("begin ") or lines[-1] != "end":
        raise AssertionError(f"RAR 测试样本不是完整的 uuencode 文件：{filename}")
    payload = b"".join(
        binascii.a2b_uu(line.encode("ascii")) for line in lines[1:-1]
    )
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise AssertionError(
            f"RAR 测试样本哈希不匹配：{filename}，得到 {actual_sha256}"
        )
    destination.write_bytes(payload)
    return destination


class RarSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-rar-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _fixture(self, kind: str) -> Path:
        return _decode_fixture(kind, self.tmp / f"{kind}-来源包.rar")

    def test_rar_is_exposed_as_a_supported_input_format(self) -> None:
        self.assertIn(".rar", ARCHIVE_SUFFIXES)
        self.assertTrue(is_archive_path("D:/音频交付/中文素材.RAR"))
        self.assertIn("*.rar", ARCHIVE_FILTER)

    def test_rar4_lists_and_extracts_files_directories_and_empty_directory(self) -> None:
        archive = self._fixture("rar4")
        listing = list_with_info(archive)

        self.assertEqual(listing.archive_type.casefold(), "rar")
        self.assertTrue(test_archive(archive))
        entries = {entry.path: entry for entry in listing.entries}
        self.assertEqual(
            set(entries),
            {"test.txt", "testlink", "testdir", "testdir\\test.txt", "testemptydir"},
        )
        self.assertTrue(entries["testdir"].is_dir)
        self.assertTrue(entries["testemptydir"].is_dir)

        out = self.tmp / "rar4-out"
        extract_entries(archive, None, out)
        self.assertEqual((out / "test.txt").read_bytes(), b"test text document\r\n")
        self.assertEqual((out / "testdir" / "test.txt").read_bytes(), b"test text document\r\n")
        self.assertEqual((out / "testlink").read_bytes(), b"test.txt")
        self.assertTrue((out / "testemptydir").is_dir())

    def test_rar5_lists_tests_and_extracts_known_content(self) -> None:
        archive = self._fixture("rar5")
        listing = list_with_info(archive)

        self.assertEqual(listing.archive_type.casefold(), "rar5")
        self.assertTrue(test_archive(archive))
        self.assertEqual(
            [(entry.path, entry.size, entry.is_dir) for entry in listing.entries],
            [("helloworld.txt", 29, False)],
        )
        out = self.tmp / "rar5-out"
        extract_entries(archive, None, out)
        self.assertEqual(
            (out / "helloworld.txt").read_bytes(),
            b"hello libarchive test suite!\n",
        )

    def test_broken_rar_reports_archive_error_instead_of_crashing(self) -> None:
        archive = self._fixture("rar5")
        archive.write_bytes(archive.read_bytes()[:24])

        self.assertFalse(test_archive(archive))
        with self.assertRaises(ArchiveError):
            list_with_info(archive)

    def test_rar_can_be_imported_and_merged_into_a_new_zip(self) -> None:
        archive = self._fixture("rar5")
        session = MergeSession(name="RAR 合并", temp_root=self.tmp / "temp")
        self.addCleanup(session.close)
        wrapper = session.import_archive(archive)
        self.assertEqual(wrapper.name, "rar5-来源包")
        self.assertEqual(session.flatten(wrapper, 0), 1)

        output = self.tmp / "RAR合并结果.zip"
        result = run_export(
            session.root,
            session.sources,
            ExportOptions(output_path=output, stage_dir=self.tmp / "stage"),
        )
        self.assertEqual(result.file_count, 1)
        verify = self.tmp / "verify"
        extract_entries(output, None, verify)
        self.assertEqual(
            (verify / "helloworld.txt").read_bytes(),
            b"hello libarchive test suite!\n",
        )

    def test_rar_works_through_user_extraction_and_drag_materialization(self) -> None:
        archive = self._fixture("rar4")
        session = MergeSession(name="RAR 解压与拖出", temp_root=self.tmp / "temp")
        self.addCleanup(session.close)
        wrapper = session.import_archive(archive)
        source_id = next(iter(session.sources))

        plan = build_extraction_plan(
            session.sources,
            [ExtractionSelection(source_id, "testdir")],
            self.tmp / "user-extract",
        )
        completed: list[ExtractionResult] = []
        failures: list[str] = []
        worker = ExtractionWorker(plan, "overwrite")
        worker.completed.connect(completed.append)
        worker.failed.connect(lambda message, _detail, _partial: failures.append(message))
        worker.run()
        self.assertFalse(failures)
        self.assertEqual(len(completed), 1)
        self.assertEqual(
            (self.tmp / "user-extract" / "testdir" / "test.txt").read_bytes(),
            b"test text document\r\n",
        )

        testdir = next(node for node in wrapper.walk() if node.name == "testdir")
        drag_root = build_source_drag_root([testdir], session.sources)
        materialized = materialize_directory(
            MaterializeRequest(drag_root, session.sources, self.tmp / "drag-out")
        )
        self.assertEqual(
            (materialized.destination / "testdir" / "test.txt").read_bytes(),
            b"test text document\r\n",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
