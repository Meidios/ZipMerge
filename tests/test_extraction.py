"""解压计划、冲突预检与后台执行测试。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import (  # noqa: E402
    ExtractionSelection,
    MergeSession,
    SourceArchive,
    build_extraction_plan,
    inspect_extraction_conflicts,
)
from zipmerge.engine import ArchiveEntry, ArchiveListing, create_archive  # noqa: E402
from zipmerge.ui.extraction_runner import ExtractionWorker  # noqa: E402

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802
    global _app
    _app = QApplication.instance() or QApplication([])


class ExtractionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="zipmerge-extract-plan-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.session = MergeSession(name="解压", temp_root=self.root / "temp")
        self.addCleanup(self.session.close)

    def _archive(
        self,
        directory: str,
        name: str,
        files: dict[str, bytes],
        *,
        fmt: str = "zip",
        password: str | None = None,
    ) -> str:
        base = self.root / directory
        src = base / "src"
        for relative, content in files.items():
            path = src / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        archive = base / f"{name}.{fmt}"
        create_archive(archive, fmt, src, password=password)
        wrapper = self.session.import_archive(archive, password=password)
        assert wrapper.source is not None and wrapper.source.source_id is not None
        return wrapper.source.source_id

    def test_single_whole_archive_gets_named_folder(self) -> None:
        source_id = self._archive("a", "中文包", {"目录/文件.txt": b"x"})
        plan = build_extraction_plan(
            self.session.sources, [ExtractionSelection(source_id)], self.root / "out"
        )
        self.assertIsNone(plan.jobs[0].paths)
        self.assertEqual(plan.jobs[0].output_dir, (self.root / "out" / "中文包").resolve())
        self.assertEqual(plan.file_count, 1)

    def test_extract_here_whole_archive_uses_destination_directly(self) -> None:
        source_id = self._archive("a", "中文包", {"目录/文件.txt": b"x"})
        destination = self.root / "out"
        plan = build_extraction_plan(
            self.session.sources,
            [ExtractionSelection(source_id)],
            destination,
            wrap_whole_archives=False,
        )
        self.assertIsNone(plan.jobs[0].paths)
        self.assertEqual(plan.jobs[0].output_dir, destination.resolve())
        worker = ExtractionWorker(plan, "overwrite")
        worker.run()
        self.assertEqual((destination / "目录" / "文件.txt").read_bytes(), b"x")

    def test_parent_selection_removes_selected_descendant(self) -> None:
        source_id = self._archive("a", "包", {"SFX/深层/文件.wav": b"x"})
        plan = build_extraction_plan(
            self.session.sources,
            [
                ExtractionSelection(source_id, "SFX"),
                ExtractionSelection(source_id, "SFX\\深层\\文件.wav"),
            ],
            self.root / "out",
        )
        self.assertEqual(plan.jobs[0].paths, ("SFX",))
        self.assertEqual(plan.jobs[0].output_dir, (self.root / "out").resolve())

    def test_multiple_same_named_sources_get_stable_separate_folders(self) -> None:
        first = self._archive("a", "同名", {"a.txt": b"a"})
        second = self._archive("b", "同名", {"b.txt": b"b"})
        plan = build_extraction_plan(
            self.session.sources,
            [ExtractionSelection(first, "a.txt"), ExtractionSelection(second, "b.txt")],
            self.root / "out",
        )
        self.assertEqual([job.output_dir.name for job in plan.jobs], ["同名", "同名_2"])

    def test_existing_file_is_promptable_but_type_conflict_blocks(self) -> None:
        source_id = self._archive("a", "包", {"目录/文件.txt": b"new"})
        destination = self.root / "out"
        existing = destination / "目录" / "文件.txt"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"old")
        plan = build_extraction_plan(
            self.session.sources,
            [ExtractionSelection(source_id, "目录\\文件.txt")],
            destination,
        )
        report = inspect_extraction_conflicts(plan)
        self.assertEqual(report.existing_files, (existing.resolve(),))
        self.assertFalse(report.blocking)

        existing.unlink()
        existing.mkdir()
        report = inspect_extraction_conflicts(plan)
        self.assertTrue(report.blocking)
        self.assertIn("已有文件夹", report.structural[0])

    def test_case_only_archive_collision_is_blocking(self) -> None:
        source_id = "case-source"
        self.session.sources[source_id] = SourceArchive(
            source_id=source_id,
            archive_path=self.root / "case.zip",
            display_name="包",
            listing=ArchiveListing(
                entries=[ArchiveEntry("A.txt", size=1), ArchiveEntry("a.txt", size=1)],
                archive_type="zip",
            ),
        )
        plan = build_extraction_plan(
            self.session.sources, [ExtractionSelection(source_id)], self.root / "out"
        )
        self.assertTrue(inspect_extraction_conflicts(plan).internal)


class ExtractionWorkerTests(ExtractionPlanTests):
    def test_worker_extracts_chinese_subset(self) -> None:
        source_id = self._archive(
            "a", "包", {"中文目录/有 空格.txt": b"data", "other.txt": b"x"}
        )
        plan = build_extraction_plan(
            self.session.sources,
            [ExtractionSelection(source_id, "中文目录")],
            self.root / "out",
        )
        results: list[object] = []
        worker = ExtractionWorker(plan, "overwrite")
        worker.completed.connect(results.append)
        worker.run()
        self.assertEqual((self.root / "out/中文目录/有 空格.txt").read_bytes(), b"data")
        self.assertEqual(len(results), 1)

    def test_worker_cancel_before_start(self) -> None:
        source_id = self._archive("a", "包", {"文件.txt": b"x"})
        plan = build_extraction_plan(
            self.session.sources, [ExtractionSelection(source_id)], self.root / "out"
        )
        cancelled: list[bool] = []
        worker = ExtractionWorker(plan, "overwrite")
        worker.cancelled.connect(cancelled.append)
        worker.cancel()
        worker.run()
        self.assertEqual(cancelled, [False])

    def test_multi_archive_zip_and_password_7z_extract_to_long_chinese_path(self) -> None:
        first = self._archive("a", "普通包", {"中文/a.txt": b"zip"})
        second = self._archive(
            "b",
            "加密包",
            {"音频/有 空格.wav": b"7z"},
            fmt="7z",
            password="测试密码",
        )
        destination = self.root.joinpath(*(["很长的中文目标目录"] * 18))
        destination.mkdir(parents=True)
        plan = build_extraction_plan(
            self.session.sources,
            [ExtractionSelection(first), ExtractionSelection(second)],
            destination,
        )
        results: list[object] = []
        failures: list[str] = []
        worker = ExtractionWorker(plan, "overwrite")
        worker.completed.connect(results.append)
        worker.failed.connect(lambda message, _detail, _partial: failures.append(message))
        worker.run()

        self.assertFalse(failures)
        self.assertEqual((destination / "普通包/中文/a.txt").read_bytes(), b"zip")
        self.assertEqual((destination / "加密包/音频/有 空格.wav").read_bytes(), b"7z")
        self.assertEqual(len(results), 1)
