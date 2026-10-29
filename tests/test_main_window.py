"""主窗口单元测试：标签页生命周期、布局切换、撤销联动与主题持久化。

Qt 走离屏平台；QSettings 使用独立的 `ZipMergeTest` 组织，避免污染真实 HKCU。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QByteArray,
    QItemSelectionModel,
    QMimeData,
    QPointF,
    Qt,
)
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.engine import create_archive  # noqa: E402
from zipmerge.ui.design import ThemeService  # noqa: E402
from zipmerge.ui.drag_export import LazyFileMimeData  # noqa: E402
from zipmerge.ui.main_window import (  # noqa: E402
    LAYOUT_BOTTOM,
    LAYOUT_COLUMNS,
    MainWindow,
    WORKSPACE_GAP,
    _format_sizes,
    _parse_sizes,
)
from zipmerge.ui.target_model import MIME_NODES  # noqa: E402
from zipmerge.util.settings import (  # noqa: E402
    KEY_ACCENT,
    KEY_LAYOUT,
    KEY_LAYOUT_SIZES,
    KEY_PREVIEW_PANEL_VISIBLE,
    KEY_SOURCE_PANEL_VISIBLE,
    KEY_TEMP_CLEAN_ON_CLOSE,
    KEY_TEMP_ROOT,
    KEY_THEME_MODE,
    KEY_WINDOW_GEOMETRY,
    Settings,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


class SizeCodecTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        sizes = {LAYOUT_COLUMNS: [300, 520, 380], LAYOUT_BOTTOM: [440, 240]}
        self.assertEqual(_parse_sizes(_format_sizes(sizes)), sizes)

    def test_parse_ignores_garbage(self) -> None:
        self.assertEqual(_parse_sizes("nonsense;columns:1,2,x"), {"columns": [1, 2]})
        self.assertEqual(_parse_sizes(None), {})


class MainWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-window-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = Settings("ZipMergeTest", "ZipMergeTest")
        self.settings.clear()
        self.addCleanup(self.settings.clear)
        self.settings.set_value(KEY_TEMP_ROOT, str(self.tmp / "temp"))
        self.theme = ThemeService()
        self.window = MainWindow(settings=self.settings, theme=self.theme)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def _archive(self, name: str = "包A", text: str = "内容\n") -> Path:
        src = self.tmp / f"{name}-src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "文件.txt").write_text(text, encoding="utf-8")
        archive = self.tmp / f"{name}.zip"
        create_archive(archive, "zip", src)
        return archive

    # ---- 标签页 -----------------------------------------------------
    def test_starts_with_one_tab(self) -> None:
        self.assertEqual(self.window._tabs.count(), 1)
        self.assertIsNotNone(self.window.current_tab())

    def test_workspace_cards_have_uniform_outer_and_inner_gaps(self) -> None:
        tab = self.window.current_tab()
        self.assertEqual(tab._layout.contentsMargins().left(), WORKSPACE_GAP)
        self.assertEqual(tab._layout.contentsMargins().top(), WORKSPACE_GAP)
        self.assertEqual(tab._layout.contentsMargins().right(), WORKSPACE_GAP)
        self.assertEqual(tab._layout.contentsMargins().bottom(), WORKSPACE_GAP)
        self.assertEqual(tab._layout.spacing(), WORKSPACE_GAP)

    def test_new_and_close_tabs(self) -> None:
        second = self.window.new_tab()
        self.assertEqual(self.window._tabs.count(), 2)
        self.assertIs(self.window.current_tab(), second)

        self.window._close_tab(self.window._tabs.indexOf(second))
        self.assertEqual(self.window._tabs.count(), 1)

        self.window._close_tab(0)
        # 关掉最后一个标签会自动补一个新会话
        self.assertEqual(self.window._tabs.count(), 1)
        self.assertIsNotNone(self.window.current_tab())

    def test_closing_tab_always_cleans_delivery_when_other_temp_files_are_kept(self) -> None:
        self.settings.set_value(KEY_TEMP_CLEAN_ON_CLOSE, False)
        tab = self.window.current_tab()
        session_dir = tab.session.temp_dir
        archive = tab.session.create_temporary_output("客户包.zip")
        archive.write_bytes(b"data")
        tab.session.commit_temporary_archive(archive)

        self.window._close_tab(0)

        self.assertTrue(session_dir.exists())
        self.assertFalse(archive.exists())
        self.assertFalse(tab.session.deliveries_dir.exists())

    def test_closing_window_cleans_deliveries_from_every_tab(self) -> None:
        self.settings.set_value(KEY_TEMP_CLEAN_ON_CLOSE, False)
        tabs = [self.window.current_tab(), self.window.new_tab()]
        archives: list[Path] = []
        for index, tab in enumerate(tabs):
            archive = tab.session.create_temporary_output(f"客户包{index}.zip")
            archive.write_bytes(b"data")
            tab.session.commit_temporary_archive(archive)
            archives.append(archive)

        self.window.close()

        self.assertTrue(all(not archive.exists() for archive in archives))
        self.assertTrue(all(tab.session.temp_dir.exists() for tab in tabs))

    # ---- 会话联动 ---------------------------------------------------
    def test_import_refreshes_views_and_enables_undo(self) -> None:
        tab = self.window.current_tab()
        tab.session.import_archive(self._archive())
        _app.processEvents()

        self.assertTrue(tab.session.has_content)
        self.assertGreater(tab.target_panel.model.rowCount(), 0)
        self.assertTrue(tab.export_bar.base_name())
        self.assertTrue(self.window.act_undo.isEnabled())
        self.assertFalse(self.window.act_redo.isEnabled())
        self.assertIn("1 个源包", self.window._status_label.text())

    def test_temporary_export_commits_result_without_success_dialog(self) -> None:
        tab = self.window.current_tab()
        tab.session.import_archive(self._archive())
        calls: list[tuple[Path, bool]] = []

        def fake_run(_parent, _session, options, *, show_success=True):
            options.output_path.parent.mkdir(parents=True, exist_ok=True)
            options.output_path.write_bytes(b"archive")
            calls.append((options.output_path, show_success))
            return SimpleNamespace(output_path=options.output_path)

        with mock.patch("zipmerge.ui.main_window.run_export_dialog", side_effect=fake_run):
            tab._run_temporary_export()

        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1])
        self.assertEqual(tab.session.temporary_archive, calls[0][0].resolve())
        self.assertEqual(tab.export_bar._temporary_card.archive_path, calls[0][0].resolve())
        self.assertTrue(tab.export_bar._temporary_card.is_draggable)

        tab.session.new_folder(tab.session.root, "新增内容")
        _app.processEvents()
        self.assertEqual(tab.export_bar._temporary_card.state, "stale")
        self.assertFalse(tab.export_bar._temporary_card.is_draggable)

    def test_cancelled_regeneration_keeps_previous_stale_archive(self) -> None:
        tab = self.window.current_tab()
        tab.session.import_archive(self._archive())
        previous = tab.session.create_temporary_output("旧包.zip")
        previous.write_bytes(b"old")
        tab.session.commit_temporary_archive(previous)
        tab.export_bar.set_temporary_archive(previous)
        tab.export_bar.mark_temporary_archive_stale()

        with mock.patch("zipmerge.ui.main_window.run_export_dialog", return_value=None):
            tab._run_temporary_export()

        self.assertEqual(tab.session.temporary_archive, previous.resolve())
        self.assertTrue(previous.exists())
        self.assertEqual(tab.export_bar._temporary_card.state, "stale")
        generations = [path for path in tab.session.deliveries_dir.iterdir() if path.is_dir()]
        self.assertEqual(generations, [previous.parent])

    def test_undo_redo_actions(self) -> None:
        tab = self.window.current_tab()
        tab.session.import_archive(self._archive())
        _app.processEvents()

        self.window._action_undo()
        _app.processEvents()
        self.assertFalse(tab.session.has_content)
        self.assertTrue(self.window.act_redo.isEnabled())

        self.window._action_redo()
        _app.processEvents()
        self.assertTrue(tab.session.has_content)
        self.assertTrue(self.window.act_undo.isEnabled())

    def test_menu_actions_disabled_without_tab(self) -> None:
        # 关闭唯一标签时会立刻补一个新标签，因此这里只验证有标签时动作可用
        self.assertTrue(self.window.act_export.isEnabled())

    def test_internal_tree_drop_on_window_never_requests_file_urls(self) -> None:
        internal = QMimeData()
        internal.setData(MIME_NODES, b"[1]")
        prepare = mock.Mock(return_value=(self.tmp / "不应物化.txt",))
        mime = LazyFileMimeData(internal, prepare)
        event = QDropEvent(
            QPointF(5, 5),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        self.window.dropEvent(event)

        prepare.assert_not_called()
        self.assertFalse(event.isAccepted())

    # ---- 主题下发 ---------------------------------------------------
    def test_first_tab_receives_theme_palette(self) -> None:
        # 回归：_apply_theme() 早于首个 new_tab()，标签页曾保留构造函数的默认强调色
        self.settings.set_value(KEY_ACCENT, "#123456")
        window = MainWindow(settings=self.settings, theme=ThemeService())
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        tab = window.current_tab()
        display_accent = window.theme.palette().accent
        self.assertNotEqual(display_accent, "#123456")
        self.assertEqual(tab.preview_panel._palette.accent, display_accent)
        self.assertEqual(tab.preview_panel._audio._palette.accent, display_accent)

        fresh = window.new_tab()
        self.assertEqual(fresh.source_panel._palette.accent, display_accent)
        self.assertEqual(fresh.target_panel._palette.accent, display_accent)
        self.assertEqual(fresh.preview_panel._palette.accent, display_accent)

    # ---- 布局 -------------------------------------------------------
    def test_layout_switch_rebuilds_splitter(self) -> None:
        tab = self.window.current_tab()
        self.assertEqual(tab._splitter.count(), 3)

        self.window._set_layout(LAYOUT_BOTTOM)
        self.assertEqual(tab.layout_mode, LAYOUT_BOTTOM)
        self.assertEqual(tab._splitter.count(), 2)
        self.assertEqual(self.settings.value(KEY_LAYOUT), LAYOUT_BOTTOM)

        self.window._set_layout(LAYOUT_COLUMNS)
        self.assertEqual(tab._splitter.count(), 3)

    def test_layout_applies_to_every_tab(self) -> None:
        first = self.window.current_tab()
        self.window._set_layout(LAYOUT_BOTTOM)
        second = self.window.new_tab()
        self.assertEqual(second.layout_mode, LAYOUT_BOTTOM)
        self.assertEqual(first._splitter.count(), 2)

    def test_toolbar_has_one_layout_toggle_instead_of_panel_switches(self) -> None:
        actions = self.window._toolbar.actions()
        self.assertIn(self.window.act_toggle_layout, actions)
        self.assertNotIn(self.window.act_show_source, actions)
        self.assertNotIn(self.window.act_show_preview, actions)

    def test_extract_action_is_shared_with_source_panel_and_tracks_selection(self) -> None:
        tab = self.window.current_tab()
        tab.session.import_archive(self._archive())
        tab.source_panel.refresh()
        self.window._sync_actions()
        self.assertFalse(self.window.act_extract.isEnabled())
        self.assertIn(self.window.act_extract, self.window._toolbar.actions())
        self.assertIs(tab.source_panel._extract_button.defaultAction(), self.window.act_extract)
        self.assertEqual(self.window.act_extract.shortcut().toString(), "Ctrl+Shift+E")

        root = tab.source_panel.model.index(0, 0)
        tab.source_panel.view.selectionModel().select(
            root,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        _app.processEvents()
        self.assertTrue(self.window.act_extract.isEnabled())

    def test_layout_toggle_action_cycles_between_the_two_presets(self) -> None:
        first = self.window.current_tab()
        second = self.window.new_tab()
        self.assertIn("当前：三栏并排", self.window.act_toggle_layout.toolTip())
        self.assertIn("两栏 + 底部预览", self.window.act_toggle_layout.toolTip())

        self.window.act_toggle_layout.trigger()
        self.assertEqual(first.layout_mode, LAYOUT_BOTTOM)
        self.assertEqual(second.layout_mode, LAYOUT_BOTTOM)
        self.assertEqual(self.settings.value(KEY_LAYOUT), LAYOUT_BOTTOM)
        self.assertTrue(self.window._layout_actions[LAYOUT_BOTTOM].isChecked())
        self.assertIn("当前：两栏 + 底部预览", self.window.act_toggle_layout.toolTip())

        self.window.act_toggle_layout.trigger()
        self.assertEqual(first.layout_mode, LAYOUT_COLUMNS)
        self.assertEqual(second.layout_mode, LAYOUT_COLUMNS)
        self.assertTrue(self.window._layout_actions[LAYOUT_COLUMNS].isChecked())

    def test_splitter_sizes_persist(self) -> None:
        tab = self.window.current_tab()
        tab._on_splitter_moved()
        stored = _parse_sizes(self.settings.value(KEY_LAYOUT_SIZES))
        self.assertEqual(stored.get(LAYOUT_COLUMNS), tab._splitter.sizes())

    def test_window_geometry_and_complete_layout_restore_after_restart(self) -> None:
        self.window.show()
        _app.processEvents()
        # 离屏 Qt 的虚拟屏幕约 800 px 宽；使用屏内尺寸，避免 restoreGeometry
        # 正常执行「把窗口压回可见区域」时改变断言值。
        self.window.resize(720, 641)
        self.window.move(1, 47)
        self.window._set_layout(LAYOUT_BOTTOM)
        _app.processEvents()
        tab = self.window.current_tab()
        tab._splitter.setSizes([430, 170])
        tab._inner_splitter.setSizes([260, 540])
        self.window._toggle_panel("preview", False)
        _app.processEvents()

        self.window.close()

        self.assertTrue(self.settings.value(KEY_WINDOW_GEOMETRY))
        self.assertFalse(self.settings.value(KEY_PREVIEW_PANEL_VISIBLE, True, type=bool))
        restored = MainWindow(settings=self.settings, theme=ThemeService())
        self.addCleanup(restored.deleteLater)
        self.addCleanup(restored.close)
        restored.show()
        _app.processEvents()
        restored_tab = restored.current_tab()
        stored_sizes = _parse_sizes(self.settings.value(KEY_LAYOUT_SIZES))

        self.assertEqual(restored.size(), self.window.size())
        self.assertEqual(restored.pos(), self.window.pos())
        self.assertEqual(restored_tab.layout_mode, LAYOUT_BOTTOM)
        self.assertEqual(restored_tab._inner_splitter.sizes(), stored_sizes["bottom_inner"])
        self.assertTrue(restored_tab.preview_panel.isHidden())
        self.assertFalse(restored.act_show_preview.isChecked())

        restored._toggle_panel("preview", True)
        _app.processEvents()
        self.assertEqual(restored_tab._splitter.sizes(), stored_sizes[LAYOUT_BOTTOM])
        self.assertFalse(restored_tab.preview_panel.isHidden())

    def test_invalid_window_geometry_falls_back_without_crashing(self) -> None:
        self.window.close()
        self.settings.set_value(KEY_WINDOW_GEOMETRY, QByteArray(b"not-a-qt-geometry"))
        self.settings.set_value(KEY_SOURCE_PANEL_VISIBLE, False)

        restored = MainWindow(settings=self.settings, theme=ThemeService())
        self.addCleanup(restored.deleteLater)
        self.addCleanup(restored.close)

        self.assertEqual(restored.size().width(), 1180)
        self.assertEqual(restored.size().height(), 720)
        self.assertTrue(restored.current_tab().source_panel.isHidden())
        self.assertFalse(restored.act_show_source.isChecked())

    def test_maximized_window_state_restores_after_restart(self) -> None:
        self.window.showMaximized()
        _app.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.window.close()

        restored = MainWindow(settings=self.settings, theme=ThemeService())
        self.addCleanup(restored.deleteLater)
        self.addCleanup(restored.close)
        restored.show()
        _app.processEvents()

        self.assertTrue(restored.isMaximized())

    def test_panel_toggle_tracks_action(self) -> None:
        tab = self.window.current_tab()
        self.window._toggle_panel("preview", False)
        self.assertTrue(tab.preview_panel.isHidden())
        self.window._toggle_panel("preview", True)
        self.assertFalse(tab.preview_panel.isHidden())

        # 新标签页继承当前的显隐偏好
        self.window._toggle_panel("source", False)
        fresh = self.window.new_tab()
        self.assertTrue(fresh.source_panel.isHidden())

    # ---- 主题 -------------------------------------------------------
    def test_theme_change_persists_and_styles(self) -> None:
        self.window.theme.set_mode("dark")
        self.assertEqual(self.settings.value(KEY_THEME_MODE), "dark")
        self.assertTrue(self.window.styleSheet())

    def test_stored_theme_restored_on_startup(self) -> None:
        self.settings.set_value(KEY_THEME_MODE, "light")
        other = MainWindow(settings=self.settings, theme=ThemeService())
        self.addCleanup(other.deleteLater)
        self.addCleanup(other.close)
        self.assertEqual(other.theme.mode_preference, "light")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
