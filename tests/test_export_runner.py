"""导出执行链路测试：worker 信号、冲突对话框选项、导出前询问。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from zipmerge.core import (  # noqa: E402
    ExportOptions,
    MergeSession,
    ResolveStrategy,
    build_export_plan,
    detect_conflicts,
)
from zipmerge.engine import create_archive, list_entries  # noqa: E402
from zipmerge.ui.export_runner import (  # noqa: E402
    ConflictDialog,
    ExportWorker,
    prepare_options,
    run_export_dialog,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


def _write(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class ExportWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-runner-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="跑", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

        src = self.tmp / "src"
        _write(src, {"SFX/boom.wav": b"A" * 32, "readme.txt": b"R"})
        archive = self.tmp / "包A.zip"
        create_archive(archive, "zip", src)
        self.session.import_archive(archive)

    def _options(self) -> ExportOptions:
        return ExportOptions(
            output_path=self.tmp / "out" / "结果.zip",
            stage_dir=self.session.stage_dir,
        )

    def test_worker_emits_progress_and_completion(self) -> None:
        seen: list[tuple[int, str]] = []
        results: list[object] = []
        failures: list[tuple[str, str]] = []
        cancelled: list[bool] = []
        worker = ExportWorker(self.session, self._options())
        worker.progress.connect(lambda pct, text: seen.append((pct, text)))
        worker.completed.connect(results.append)
        worker.failed.connect(lambda message, detail: failures.append((message, detail)))
        worker.cancelled.connect(lambda: cancelled.append(True))

        worker.run()  # 直接同步执行，等价于线程体

        self.assertFalse(failures)
        self.assertFalse(cancelled)
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertTrue(result.output_path.exists())
        self.assertEqual(seen[-1][0], 100)
        self.assertTrue(all(0 <= pct <= 100 for pct, _ in seen))
        entries = {entry.path for entry in list_entries(result.output_path)}
        self.assertIn("包A\\readme.txt", entries)

    def test_worker_reports_failure_on_empty_tree(self) -> None:
        empty = MergeSession(name="空", temp_root=self.tmp / "temp")
        self.addCleanup(empty.close)
        failures: list[tuple[str, str]] = []
        worker = ExportWorker(empty, ExportOptions(output_path=self.tmp / "x.zip"))
        worker.failed.connect(lambda message, detail: failures.append((message, detail)))
        worker.run()
        self.assertEqual(len(failures), 1)
        self.assertIn("空", failures[0][0])

    def test_worker_cancel_before_start(self) -> None:
        cancelled: list[bool] = []
        results: list[object] = []
        worker = ExportWorker(self.session, self._options())
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.completed.connect(results.append)
        worker.cancel()
        worker.run()
        self.assertTrue(cancelled)
        self.assertFalse(results)

    def test_dialog_default_keeps_success_message_and_temporary_mode_suppresses_it(self) -> None:
        with mock.patch("zipmerge.ui.export_runner._show_success") as show_success:
            normal = run_export_dialog(None, self.session, self._options())
        self.assertIsNotNone(normal)
        show_success.assert_called_once()

        temporary_options = ExportOptions(
            output_path=self.tmp / "temporary" / "临时包.zip",
            stage_dir=self.session.stage_dir,
        )
        with mock.patch("zipmerge.ui.export_runner._show_success") as show_success:
            temporary = run_export_dialog(
                None,
                self.session,
                temporary_options,
                show_success=False,
            )
        self.assertIsNotNone(temporary)
        show_success.assert_not_called()


class ConflictDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-conflict-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="冲突", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

        for name, payload in (("d1", b"1"), ("d2", b"2")):
            src = self.tmp / name
            _write(src, {"same.txt": payload})
            archive = src / "包.zip"
            create_archive(archive, "zip", src)
            wrapper = self.session.import_archive(archive)
            self.session.flatten(wrapper, 0)

        self.conflicts = detect_conflicts(build_export_plan(self.session.root))

    def test_detects_duplicate_conflict(self) -> None:
        self.assertEqual(len(self.conflicts), 1)
        self.assertEqual(self.conflicts[0].target_path, "same.txt")

    def test_defaults_to_follow_overall(self) -> None:
        dialog = ConflictDialog(self.conflicts)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.strategy(), ResolveStrategy.RENAME)
        self.assertEqual(dialog.overrides(), {})

    def test_per_row_override(self) -> None:
        dialog = ConflictDialog(self.conflicts)
        self.addCleanup(dialog.deleteLater)
        combo = dialog._row_combos[0]
        combo.setCurrentIndex(combo.findData(ResolveStrategy.SKIP.value))
        self.assertEqual(dialog.overrides(), {"same.txt": ResolveStrategy.SKIP})

    def test_path_clash_offers_no_overwrite(self) -> None:
        # 单文件与同名目录冲突：下拉不应提供「覆盖」
        self.session.new_folder(self.session.root, name="clash")
        src = self.tmp / "d3"
        _write(src, {"clash": b"f"})
        archive = src / "包3.zip"
        create_archive(archive, "zip", src)
        wrapper = self.session.import_archive(archive)
        self.session.flatten(wrapper, 0)
        conflicts = detect_conflicts(build_export_plan(self.session.root))
        path_clash = next(c for c in conflicts if c.kind.value == "path_clash")
        dialog = ConflictDialog([path_clash])
        self.addCleanup(dialog.deleteLater)
        combo = dialog._row_combos[0]
        options = [combo.itemData(i) for i in range(combo.count())]
        self.assertNotIn(ResolveStrategy.OVERWRITE.value, options)


class PrepareOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-prepare-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="准备", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)
        src = self.tmp / "src"
        _write(src, {"a.txt": b"a"})
        archive = self.tmp / "包A.zip"
        create_archive(archive, "zip", src)
        self.first_wrapper = self.session.import_archive(archive)

    def test_no_dialog_when_clean(self) -> None:
        options = ExportOptions(output_path=self.tmp / "ok.zip")
        with mock.patch.object(ConflictDialog, "exec") as exec_mock:
            prepared = prepare_options(None, self.session, options)
        exec_mock.assert_not_called()
        self.assertIs(prepared, options)

    def test_conflict_dialog_updates_strategy_and_overrides(self) -> None:
        # 两个包各含 a.txt，摊平到根后制造重名冲突
        self.session.flatten(self.first_wrapper, 0)
        src = self.tmp / "d2"
        _write(src, {"a.txt": b"b"})
        archive = src / "包B.zip"
        create_archive(archive, "zip", src)
        self.session.flatten(self.session.import_archive(archive), 0)
        self.assertEqual(len(detect_conflicts(build_export_plan(self.session.root))), 1)

        options = ExportOptions(output_path=self.tmp / "ok.zip")
        with mock.patch.object(ConflictDialog, "exec", return_value=QDialog.DialogCode.Accepted), \
                mock.patch.object(ConflictDialog, "strategy", return_value=ResolveStrategy.SKIP), \
                mock.patch.object(ConflictDialog, "overrides", return_value={"a.txt": ResolveStrategy.RENAME}):
            prepared = prepare_options(None, self.session, options)
        self.assertIs(prepared, options)
        self.assertEqual(options.strategy, ResolveStrategy.SKIP)
        self.assertEqual(options.overrides, {"a.txt": ResolveStrategy.RENAME})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
