r"""入口脚本单元测试：参数解析、单实例请求分发与卸载短路（PLAN 8.4 / 8.7）。"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

import main as entry  # noqa: E402
from zipmerge import __version__  # noqa: E402
from zipmerge.engine import create_archive  # noqa: E402
from zipmerge.ipc import ACTION_ADD_TO_SESSION, ACTION_OPEN, Request  # noqa: E402
from zipmerge.ui.design import ThemeService  # noqa: E402
from zipmerge.ui.main_window import MainWindow  # noqa: E402
from zipmerge.util.settings import KEY_TEMP_ROOT, Settings  # noqa: E402

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


class ParseArgsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        args = entry._parse_args([])
        self.assertEqual(args.paths, [])
        self.assertFalse(args.add_to_session)
        self.assertFalse(args.unregister_all)
        self.assertFalse(args.extract_here)
        self.assertFalse(args.install_context_menus)
        self.assertFalse(args.version)

    def test_collects_paths_and_flags(self) -> None:
        args = entry._parse_args(["--add-to-session", "a.txt", "b.7z"])
        self.assertEqual(args.paths, ["a.txt", "b.7z"])
        self.assertTrue(args.add_to_session)
        self.assertFalse(args.unregister_all)

        self.assertTrue(entry._parse_args(["--unregister-all"]).unregister_all)
        self.assertTrue(
            entry._parse_args(["--install-context-menus"]).install_context_menus
        )
        extracted = entry._parse_args(["--extract-here", "a.zip"])
        self.assertTrue(extracted.extract_here)
        self.assertEqual(extracted.paths, ["a.zip"])

    def test_extract_here_branch_bypasses_single_instance(self) -> None:
        with (
            mock.patch.object(entry, "QApplication", return_value=mock.Mock()),
            mock.patch.object(entry, "_configure_app"),
            mock.patch.object(entry, "_run_extract_here", return_value=0) as run,
            mock.patch.object(entry, "is_running", side_effect=AssertionError("不应检查单实例")),
            mock.patch.object(entry.ShellIntegration, "sync_suffixes"),
        ):
            self.assertEqual(entry.main(["--extract-here", "a.zip"]), 0)
        run.assert_called_once()

    def test_compress_here_bypasses_gui_and_single_instance(self) -> None:
        with (
            mock.patch("zipmerge.core.compress_here.compress_here", return_value=Path("file.zip")) as pack,
            mock.patch.object(entry, "is_running", side_effect=AssertionError("must bypass IPC")),
            mock.patch.object(entry, "QApplication", side_effect=AssertionError("no confirmation window")),
            mock.patch.object(entry, "_emit"),
        ):
            self.assertEqual(entry.main(["--compress-here", "file.txt"]), 0)
        pack.assert_called_once_with("file.txt")


class DispatchRequestTests(unittest.TestCase):
    """右键 / 文件关联进来的请求要落到当前标签页的正确目标。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-main-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        settings = Settings("ZipMergeTest", "ZipMergeTest")
        settings.clear()
        self.addCleanup(settings.clear)
        settings.set_value(KEY_TEMP_ROOT, str(self.tmp / "temp"))
        self.window = MainWindow(settings=settings, theme=ThemeService())
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def _archive(self, name: str = "包A") -> Path:
        src = self.tmp / f"{name}-src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "内容.txt").write_text("hello\n", encoding="utf-8")
        archive = self.tmp / f"{name}.zip"
        create_archive(archive, "zip", src)
        return archive

    def test_open_request_attaches_archive(self) -> None:
        archive = self._archive()
        entry._dispatch_request(self.window, Request(action=ACTION_OPEN, paths=(str(archive),)))
        _app.processEvents()

        tab = self.window.current_tab()
        self.assertEqual(tab.source_panel.model.rowCount(), 1)
        self.assertTrue(tab.session.has_content)

    def test_add_to_session_request_imports_disk_file(self) -> None:
        loose = self.tmp / "散文件.txt"
        loose.write_text("hi\n", encoding="utf-8")
        entry._dispatch_request(
            self.window, Request(action=ACTION_ADD_TO_SESSION, paths=(str(loose),))
        )
        _app.processEvents()

        tab = self.window.current_tab()
        self.assertTrue(tab.session.has_content)
        self.assertEqual(tab.source_panel.model.rowCount(), 0)

    def test_add_to_session_request_imports_chinese_directory(self) -> None:
        folder = self.tmp / "中文音效目录"
        folder.mkdir()
        (folder / "音效.wav").write_bytes(b"audio")
        entry._dispatch_request(
            self.window, Request(action=ACTION_ADD_TO_SESSION, paths=(str(folder),))
        )
        _app.processEvents()
        tab = self.window.current_tab()
        node = tab.session.root.children[0]
        self.assertTrue(node.is_dir)
        self.assertEqual(node.name, folder.name)
        self.assertEqual(Path(node.source.disk_path), folder)
        self.assertEqual(tab.source_panel.model.rowCount(), 0)

    def test_dispatch_creates_tab_when_missing(self) -> None:
        archive = self._archive()
        with mock.patch.object(self.window, "current_tab", return_value=None):
            entry._dispatch_request(
                self.window, Request(action=ACTION_OPEN, paths=(str(archive),))
            )
        _app.processEvents()
        self.assertGreaterEqual(self.window._tabs.count(), 2)


class UnregisterAllTests(unittest.TestCase):
    def test_short_circuits_before_qt_and_calls_unregister(self) -> None:
        calls: list[int] = []

        class FakeIntegration:
            def __init__(self, settings) -> None:
                self.settings = settings

            def unregister(self) -> list[str]:
                calls.append(1)
                return ["k1", "k2"]

        with mock.patch.object(entry, "ShellIntegration", FakeIntegration):
            code = entry.main(["--unregister-all"])

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)

    def test_install_context_menus_short_circuits_before_gui(self) -> None:
        calls: list[int] = []

        class FakeIntegration:
            def __init__(self, settings) -> None:
                self.settings = settings

            def initialize_install_defaults(self) -> bool:
                calls.append(1)
                return True

        with (
            mock.patch.object(entry, "ShellIntegration", FakeIntegration),
            mock.patch.object(entry, "is_running", side_effect=AssertionError("不应检查单实例")),
        ):
            code = entry.main(["--install-context-menus"])

        self.assertEqual(code, 0)
        self.assertEqual(calls, [1])


class ConsoleOutputTests(unittest.TestCase):
    """console=False 打包后没有标准输出，命令行分支仍必须正常结束（MEMORY 3.8）。"""

    def test_prints_version_and_exits(self) -> None:
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            code = entry.main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn(__version__, buffer.getvalue())

    def test_version_without_any_output_channel(self) -> None:
        with (
            mock.patch.object(sys, "stdout", None),
            mock.patch.object(entry, "_attach_parent_console", return_value=None),
        ):
            self.assertEqual(entry.main(["--version"]), 0)

    def test_unregister_all_survives_broken_stream(self) -> None:
        class FakeIntegration:
            def __init__(self, settings) -> None:
                self.settings = settings

            def unregister(self) -> list[str]:
                return ["k1"]

        class BrokenStream:
            def write(self, _text: str) -> int:
                raise OSError("no console")

            def flush(self) -> None:
                pass

        with (
            mock.patch.object(entry, "ShellIntegration", FakeIntegration),
            mock.patch.object(sys, "stdout", BrokenStream()),
        ):
            self.assertEqual(entry.main(["--unregister-all"]), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
