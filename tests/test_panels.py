"""目标树 / 预览面板单元测试：右键选择语义、展开态保持、空格播放与外部打开。

Qt 走离屏平台；QSettings 使用独立的 ``ZipMergeTest`` 组织，避免污染真实 HKCU。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QAction, QDropEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import MergeSession, ResolveStrategy  # noqa: E402
from zipmerge.engine import create_archive  # noqa: E402
from zipmerge.ui import target_panel as target_panel_module  # noqa: E402
from zipmerge.ui import source_panel as source_panel_module  # noqa: E402
from zipmerge.ui.design import ThemeService  # noqa: E402
from zipmerge.ui.drag_export import LazyFileMimeData  # noqa: E402
from zipmerge.ui.main_window import WorkspaceTab  # noqa: E402
from zipmerge.ui.preview_panel import PreviewPanel  # noqa: E402
from zipmerge.ui.source_panel import ArchiveImportWorker, SourcePanel  # noqa: E402
from zipmerge.ui.target_panel import TargetPanel  # noqa: E402
from zipmerge.ui.target_model import MIME_SOURCE  # noqa: E402
from zipmerge.util.settings import Settings  # noqa: E402

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


def write_files(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class _DesktopSpy:
    """记录 ``QDesktopServices.openUrl`` 收到的本地路径。"""

    opened: list[str] = []

    @staticmethod
    def openUrl(url) -> bool:  # noqa: N802 - 与 Qt 同名
        _DesktopSpy.opened.append(url.toLocalFile())
        return True


class PanelTestCase(unittest.TestCase):
    """共用夹具：一个含 SFX / 深层 / readme 的源包。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-panel-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="面板", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)
        src = self.tmp / "src"
        write_files(src, {"SFX/boom.wav": b"A" * 8, "SFX/深层/inner.txt": b"T", "readme.txt": b"R"})
        archive = self.tmp / "包A.zip"
        create_archive(archive, "zip", src)
        self.session.import_archive(archive)
        self.wrapper = self.session.root.children[0]
        _DesktopSpy.opened = []

    def node(self, name: str):
        return next(n for n in self.session.root.walk() if n.name == name)


class TargetPanelTests(PanelTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.panel = TargetPanel(self.session)
        self.addCleanup(self.panel.deleteLater)
        self.panel.resize(480, 400)
        self.panel.show()
        self.panel.activateWindow()
        _app.processEvents()
        self.panel.view.expand(self.panel.model.index_for_node(self.wrapper))
        _app.processEvents()

    def test_tree_rows_do_not_use_alternating_backgrounds(self) -> None:
        self.assertFalse(self.panel.view.alternatingRowColors())

    def test_selected_nested_rows_have_no_vertical_ancestor_markers(self) -> None:
        theme = ThemeService()
        view = self.panel.view
        for mode in ("dark", "light"):
            theme.set_mode(mode)
            self.panel.setStyleSheet(theme.stylesheet())
            view.expandAll()
            for name in ("inner.txt", "深层"):
                with self.subTest(mode=mode, name=name):
                    index = self.panel.model.index_for_node(self.node(name))
                    view.setCurrentIndex(index)
                    view.setFocus()
                    _app.processEvents()
                    rect = view.visualRect(index)
                    image = view.viewport().grab().toImage()
                    ratio = image.devicePixelRatio()
                    # Exclude the current node's expansion arrow, but include
                    # every ancestor indentation cell on this selected row.
                    colors = {
                        image.pixelColor(int(x * ratio), int(rect.center().y() * ratio)).name()
                        for x in range(8, rect.left() - view.indentation())
                    }
                    self.assertEqual(colors, {theme.palette().selection_solid.lower()})

    def test_duplicate_prompt_shows_count_and_explicit_choices(self) -> None:
        shown: dict[str, object] = {}

        def choose_overwrite(box) -> int:
            shown["title"] = box.windowTitle()
            shown["text"] = box.text()
            shown["buttons"] = [button.text() for button in box.buttons()]
            next(button for button in box.buttons() if button.text() == "覆盖旧文件").click()
            return 0

        with mock.patch.object(target_panel_module.QMessageBox, "exec", choose_overwrite):
            strategy = self.panel._ask_file_conflicts(3)

        self.assertIs(strategy, ResolveStrategy.OVERWRITE)
        self.assertEqual(shown["title"], "目标结构中有同名文件")
        self.assertIn("3", shown["text"])
        self.assertIn("覆盖旧文件", shown["buttons"])
        self.assertIn("重命名新文件", shown["buttons"])
        self.assertIn("取消", shown["buttons"])

    def _select(self, *names: str) -> None:
        selection = self.panel.view.selectionModel()
        for name in names:
            index = self.panel.model.index_for_node(self.node(name))
            selection.select(
                index,
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )

    # ---- 右键语义（PLAN 7.7 问题 2 / 5）------------------------------
    def test_context_menu_on_selected_keeps_multi_selection(self) -> None:
        self._select("SFX", "readme.txt")
        self.assertEqual(len(self.panel.selected_nodes()), 2)

        sfx = self.node("SFX")
        point = self.panel.view.visualRect(self.panel.model.index_for_node(sfx)).center()
        self.assertIs(self.panel._pointed_node(point), sfx)
        # 点中已在选中集内的项不塌缩多选
        self.assertEqual(len(self.panel.selected_nodes()), 2)

        self.panel.delete_selected()
        self.assertEqual(self.session.root.total_file_count(), 0)

    def test_context_menu_on_unselected_item_selects_only_it(self) -> None:
        self._select("SFX", "readme.txt")
        # 「深层」在 SFX 之下，展开后才可见，visualRect 才能定位到它
        self.panel.view.expand(self.panel.model.index_for_node(self.node("SFX")))
        _app.processEvents()
        deep = self.node("深层")
        point = self.panel.view.visualRect(self.panel.model.index_for_node(deep)).center()
        self.assertIs(self.panel._pointed_node(point), deep)
        self.assertEqual([n.name for n in self.panel.selected_nodes()], ["深层"])

    def test_blank_context_menu_targets_root(self) -> None:
        self.panel.view.setCurrentIndex(
            self.panel.model.index_for_node(self.node("SFX"))
        )
        self.assertTrue(self.panel.selected_nodes())

        blank = QPoint(4, self.panel.view.viewport().height() - 4)
        self.assertIsNone(self.panel._pointed_node(blank))
        self.assertIsNone(self.panel.current_node())
        self.assertEqual(self.panel.selected_nodes(), [])
        self.assertIs(self.panel._folder_target(), self.session.root)

        self.panel.new_folder()
        self.assertIn("新建文件夹", [child.name for child in self.session.root.children])

    # ---- 展开态保持（PLAN 7.7 问题 7）--------------------------------
    def test_refresh_keeps_expansion_and_selection(self) -> None:
        sfx = self.node("SFX")
        sfx_index = self.panel.model.index_for_node(sfx)
        self.panel.view.expand(sfx_index)
        self.panel.view.setCurrentIndex(sfx_index)
        _app.processEvents()

        self.panel.model.refresh()
        _app.processEvents()

        self.assertTrue(
            self.panel.view.isExpanded(self.panel.model.index_for_node(self.wrapper))
        )
        self.assertTrue(
            self.panel.view.isExpanded(self.panel.model.index_for_node(sfx))
        )
        self.assertIs(self.panel.current_node(), sfx)
        self.assertEqual([n.name for n in self.panel.selected_nodes()], ["SFX"])

    def test_refresh_drops_deleted_nodes_from_view_state(self) -> None:
        sfx = self.node("SFX")
        self.panel.view.expand(self.panel.model.index_for_node(sfx))
        self.panel.view.setCurrentIndex(self.panel.model.index_for_node(sfx))
        self.session.delete([sfx])
        self.panel.reload_now()
        _app.processEvents()
        self.assertIsNone(self.panel.current_node())

    # ---- 空格播放（PLAN 7.7 问题 8）----------------------------------
    def test_space_shortcut_requests_playback(self) -> None:
        seen: list[int] = []
        self.panel.playback_toggle_requested.connect(lambda: seen.append(1))
        self.panel.view.setFocus()
        _app.processEvents()
        QTest.keyClick(self.panel.view, Qt.Key.Key_Space)
        _app.processEvents()
        self.assertEqual(seen, [1])

    # ---- 外部程序打开（PLAN 7.7 新增 1）------------------------------
    def test_open_disk_file_uses_default_app(self) -> None:
        loose = self.tmp / "散文件.txt"
        loose.write_text("x", encoding="utf-8")
        node = self.session.import_disk_paths([str(loose)])[0]
        with mock.patch.object(target_panel_module, "QDesktopServices", _DesktopSpy):
            self.panel.open_node(node)
        self.assertEqual([Path(opened) for opened in _DesktopSpy.opened], [loose])

    def test_open_archive_entry_extracts_then_opens(self) -> None:
        node = self.node("inner.txt")
        with mock.patch.object(target_panel_module, "QDesktopServices", _DesktopSpy):
            self.panel.open_node(node)
        self.assertEqual(len(_DesktopSpy.opened), 1)
        opened = Path(_DesktopSpy.opened[0])
        self.assertTrue(opened.is_file())
        self.assertEqual(opened.read_bytes(), b"T")
        self.assertTrue(opened.is_relative_to(self.session.temp_dir))

    def test_double_click_opens_file_but_not_directory(self) -> None:
        model = self.panel.model
        with mock.patch.object(target_panel_module, "QDesktopServices", _DesktopSpy):
            self.panel._on_double_clicked(model.index_for_node(self.node("SFX")))
            self.panel._on_double_clicked(model.index_for_node(self.node("inner.txt")))
        self.assertEqual(len(_DesktopSpy.opened), 1)
        self.assertTrue(_DesktopSpy.opened[0].endswith("inner.txt"))

    def test_open_virtual_folder_is_noop(self) -> None:
        node = self.session.new_folder(self.session.root)
        with mock.patch.object(target_panel_module, "QDesktopServices", _DesktopSpy):
            self.panel.open_node(node)
        self.assertEqual(_DesktopSpy.opened, [])


class PreviewPanelTests(PanelTestCase):
    def test_toggle_playback_only_on_audio_page(self) -> None:
        preview = PreviewPanel(self.session, Settings("ZipMergeTest", "ZipMergeTest"))
        self.addCleanup(preview.deleteLater)
        calls: list[int] = []
        preview._audio.toggle_playback = lambda: calls.append(1)
        preview.toggle_playback()
        self.assertEqual(calls, [])
        preview._stack.setCurrentIndex(preview._PAGE_AUDIO)
        preview.toggle_playback()
        self.assertEqual(calls, [1])


class WorkspaceWiringTests(PanelTestCase):
    def test_target_tree_space_reaches_audio_preview(self) -> None:
        settings = Settings("ZipMergeTest", "ZipMergeTest")
        settings.clear()
        self.addCleanup(settings.clear)
        tab = WorkspaceTab(self.session, settings)
        self.addCleanup(tab.deleteLater)

        calls: list[int] = []
        tab.preview_panel._audio.toggle_playback = lambda: calls.append(1)
        tab.target_panel.playback_toggle_requested.emit()
        self.assertEqual(calls, [])

        tab.preview_panel._stack.setCurrentIndex(tab.preview_panel._PAGE_AUDIO)
        tab.target_panel.playback_toggle_requested.emit()
        self.assertEqual(calls, [1])


class SourcePanelTests(PanelTestCase):
    """源包面板：拖入压缩包与「上次导入目录」记忆（PLAN 7.7 问题 1 / 11）。"""

    def _panel(self) -> SourcePanel:
        settings = Settings("ZipMergeTest", "ZipMergeTest")
        settings.clear()
        self.addCleanup(settings.clear)
        panel = SourcePanel(self.session, settings)
        self.addCleanup(panel.deleteLater)
        return panel

    def test_archive_listing_runs_off_the_ui_thread(self) -> None:
        archive = self.tmp / "包A.zip"
        listing = next(iter(self.session.sources.values())).listing
        caller_thread = threading.get_ident()
        worker_threads: list[int] = []

        def fake_listing(_path, password=None, *, cancel=None):
            worker_threads.append(threading.get_ident())
            return listing

        worker = ArchiveImportWorker([(str(archive), None)])
        with mock.patch.object(source_panel_module, "list_with_info", fake_listing):
            worker.start()
            worker.wait()
        self.assertEqual(len(worker.loaded), 1)
        self.assertNotEqual(worker_threads, [caller_thread])

    def test_refresh_keeps_archive_selection_without_exposing_entries(self) -> None:
        panel = self._panel()
        source_id = next(iter(self.session.sources))
        root = panel.model.find_entry_index(source_id, "")
        self.assertEqual(panel.model.rowCount(root), 0)
        self.assertFalse(panel.model.index(0, 0, root).isValid())
        self.assertFalse(panel.model.find_entry_index(source_id, "SFX").isValid())
        self.assertTrue(panel.view.isHeaderHidden())
        self.assertTrue(panel.view.isColumnHidden(1))
        self.assertTrue(panel.view.isColumnHidden(2))
        self.assertFalse(panel.view.itemsExpandable())
        self.assertFalse(panel.view.rootIsDecorated())
        panel.view.setCurrentIndex(root)
        _app.processEvents()
        panel.refresh()
        _app.processEvents()
        self.assertEqual(panel._node_key(panel.view.currentIndex()), (source_id, ""))
        self.assertEqual(panel.model.rowCount(panel.view.currentIndex()), 0)

    def test_locating_nested_target_selects_owning_archive(self) -> None:
        panel = self._panel()
        self.assertTrue(panel.focus_entry("包A", "SFX/boom.wav"))
        self.assertEqual(panel._node_key(panel.view.currentIndex()), ("包A", ""))
        self.assertFalse(panel.focus_entry("missing", "SFX/boom.wav"))

    def test_pending_scroll_restore_is_cancelled_with_panel_destruction(self) -> None:
        panel = self._panel()
        panel.refresh()
        panel.deleteLater()
        _app.processEvents()
        _app.processEvents()

    def test_empty_state_keeps_view_as_drop_target(self) -> None:
        empty = MergeSession(name="空", temp_root=self.tmp / "temp-empty")
        self.addCleanup(empty.close)
        settings = Settings("ZipMergeTest", "ZipMergeTest")
        settings.clear()
        self.addCleanup(settings.clear)
        panel = SourcePanel(empty, settings)
        self.addCleanup(panel.deleteLater)

        self.assertFalse(empty.sources)
        self.assertFalse(panel._empty_hint.isHidden())
        self.assertFalse(panel.view.isHidden())

    def test_add_archives_remembers_import_dir(self) -> None:
        settings = Settings("ZipMergeTest", "ZipMergeTest")
        settings.clear()
        self.addCleanup(settings.clear)
        panel = SourcePanel(self.session, settings)
        self.addCleanup(panel.deleteLater)

        archive = self.tmp / "包A.zip"
        self.assertEqual(panel.add_archives([str(archive)]), 1)
        self.assertEqual(settings.value("import/last_dir", ""), str(self.tmp))

    def test_prompt_add_starts_at_remembered_dir(self) -> None:
        settings = Settings("ZipMergeTest", "ZipMergeTest")
        settings.clear()
        self.addCleanup(settings.clear)
        settings.set_value("import/last_dir", str(self.tmp))
        panel = SourcePanel(self.session, settings)
        self.addCleanup(panel.deleteLater)

        seen: list[str] = []

        def fake(parent, title, directory, filters):
            seen.append(directory)
            return ([], "")

        with mock.patch.object(source_panel_module.QFileDialog, "getOpenFileNames", fake):
            panel.prompt_add()
        self.assertEqual(seen, [str(self.tmp)])

    def test_prompt_add_falls_back_when_dir_missing(self) -> None:
        settings = Settings("ZipMergeTest", "ZipMergeTest")
        settings.clear()
        self.addCleanup(settings.clear)
        settings.set_value("import/last_dir", str(self.tmp / "不存在"))
        panel = SourcePanel(self.session, settings)
        self.addCleanup(panel.deleteLater)

        seen: list[str] = []

        def fake(parent, title, directory, filters):
            seen.append(directory)
            return ([], "")

        with mock.patch.object(source_panel_module.QFileDialog, "getOpenFileNames", fake):
            panel.prompt_add()
        self.assertEqual(seen, [str(Path.home())])

    def test_drop_archives_on_panel_imports_them(self) -> None:
        panel = self._panel()
        archive = self.tmp / "包A.zip"
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(archive))])
        event = QDropEvent(
            QPointF(5, 5),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        panel.dropEvent(event)
        self.assertIn("包A", self.session.sources)

    def test_internal_tree_drop_on_source_panel_never_requests_file_urls(self) -> None:
        panel = self._panel()
        internal = QMimeData()
        internal.setData(MIME_SOURCE, b'{}')
        prepare = mock.Mock(return_value=(self.tmp / "不应生成.txt",))
        mime = LazyFileMimeData(internal, prepare)
        # 即使拖拽状态刚从外部切回窗口，内部接收区也必须按自有 MIME 拒绝，
        # 不能访问 text/uri-list。
        event = QDropEvent(
            QPointF(5, 5),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        panel.dropEvent(event)

        prepare.assert_not_called()
        self.assertFalse(event.isAccepted())

    def test_extract_action_uses_selected_archive_and_remembers_successful_directory(self) -> None:
        panel = self._panel()
        action = QAction("解压所选到…", panel)
        action.triggered.connect(panel.prompt_extract)
        panel.bind_extract_action(action)
        index = panel.model.index(0, 0)
        panel.view.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        destination = self.tmp / "解压位置"
        destination.mkdir()

        with (
            mock.patch.object(
                source_panel_module.QFileDialog,
                "getExistingDirectory",
                return_value=str(destination),
            ),
            mock.patch.object(
                source_panel_module,
                "run_extraction_dialog",
                return_value=SimpleNamespace(destination=destination),
            ) as runner,
        ):
            action.trigger()

        plan = runner.call_args.args[1]
        self.assertIsNone(plan.jobs[0].paths)
        self.assertEqual(plan.jobs[0].output_dir, destination / "包A")
        self.assertEqual(panel.settings.value("extract/last_dir"), str(destination))
        self.assertIs(panel._extract_button.defaultAction(), action)

    def test_archive_selection_retains_whole_archive_drag_and_extraction(self) -> None:
        panel = self._panel()
        archive = panel.model.find_entry_index("包A", "")
        panel.view.selectionModel().select(
            archive,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        paths = {item.entry_path for item in panel.selected_extraction_selections()}
        self.assertEqual(paths, {""})
        root = source_panel_module.build_source_drag_root(
            [panel.model.node(archive)], self.session.sources
        )
        self.assertTrue(root.children[0].children)
        self.assertTrue(panel.model.mimeData([archive]).hasFormat(MIME_SOURCE))

    def test_extract_context_menu_reuses_bound_action(self) -> None:
        panel = self._panel()
        action = QAction("解压所选到…", panel)
        panel.bind_extract_action(action)
        index = panel.model.index(0, 0)
        captured: list[QAction] = []

        class FakeMenu:
            def __init__(self, _parent):
                pass

            def addAction(self, item):  # noqa: N802
                captured.append(item)

            def exec(self, _point):
                pass

        with (
            mock.patch.object(panel.view, "indexAt", return_value=index),
            mock.patch.object(source_panel_module, "QMenu", FakeMenu),
        ):
            panel._show_context_menu(QPoint(1, 1))
        self.assertEqual(captured, [action])

    def test_source_cannot_be_removed_while_target_tree_references_it(self) -> None:
        panel = self._panel()
        index = panel.model.index(0, 0)
        panel.view.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        with mock.patch.object(source_panel_module.QMessageBox, "information") as message:
            panel.remove_selected()
        self.assertIn("包A", self.session.sources)
        message.assert_called_once()

    def test_source_can_be_removed_after_references_are_deleted(self) -> None:
        source_id = next(iter(self.session.sources))
        self.session.delete([self.session.root.children[0]])
        self.assertTrue(self.session.undo.can_undo)
        self.session.remove_source(source_id)
        self.assertNotIn(source_id, self.session.sources)
        self.assertFalse(self.session.undo.can_undo)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
