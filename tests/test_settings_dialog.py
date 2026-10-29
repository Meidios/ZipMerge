r"""设置对话框单元测试：五组设置的读取、校验与落盘（PLAN 7.8 / 8.5）。

注册表用例全部落在临时 HKCU 子树（Software\ZipMergeTest-<uuid>），QSettings 使用
独立的 ZipMergeTest 组织；不触碰真实 HKCU 与真实设置。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from zipmerge.platform import registry  # noqa: E402
from zipmerge.platform import shell_integration as si  # noqa: E402
from zipmerge.platform.shell_integration import PROGID, ShellIntegration  # noqa: E402
from zipmerge.ui import settings_dialog as sd  # noqa: E402
from zipmerge.ui.design import ThemeService  # noqa: E402
from zipmerge.ui.settings_dialog import SettingsDialog  # noqa: E402
from zipmerge.util.paths import ARCHIVE_SUFFIXES, set_extra_archive_suffixes  # noqa: E402
from zipmerge.util.settings import (  # noqa: E402
    KEY_DENSITY,
    KEY_FONT_SCALE,
    KEY_LAYOUT,
    KEY_OUTPUT_DIR,
    KEY_OUTPUT_FORMAT,
    KEY_PREVIEW_TEXT_LIMIT,
    KEY_PREVIEW_VIDEO_AUTO_LIMIT,
    KEY_TEMP_CLEAN_ON_CLOSE,
    Settings,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


@unittest.skipUnless(registry.available(), "需要 Windows 注册表")
class SettingsDialogTests(unittest.TestCase):
    """对话框行为：状态回显、输入校验、apply 一次性落盘。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-settings-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # 自定义后缀会写进进程内识别表，测试结束复位，避免污染后续用例
        self.addCleanup(set_extra_archive_suffixes, [])
        self.root = registry.hkey_current_user()
        self.prefix = rf"Software\ZipMergeTest-{uuid.uuid4().hex[:8]}"
        self.addCleanup(registry.delete_tree, self.root, self.prefix)
        self.settings = Settings("ZipMergeTest", "ZipMergeTest")
        self.settings.clear()
        self.addCleanup(self.settings.clear)
        self.theme = ThemeService()
        self.integration = ShellIntegration(
            self.settings, prefix=self.prefix, executable=r"C:\App\ZipMerge.exe", script=None
        )

    # ---- 工具 -------------------------------------------------------
    def _dialog(self) -> SettingsDialog:
        dialog = SettingsDialog(
            self.settings, self.theme, integration=self.integration, temp_root=self.tmp
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    @staticmethod
    def _set_combo(combo, data) -> None:
        combo.setCurrentIndex(combo.findData(data))

    # ---- 结构 -------------------------------------------------------
    def test_builds_five_tabs(self) -> None:
        dialog = self._dialog()
        self.assertEqual(dialog._tabs.count(), 5)
        titles = [dialog._tabs.tabText(i) for i in range(dialog._tabs.count())]
        self.assertEqual(titles, ["系统集成", "临时目录", "常规", "预览", "密码"])

    def test_format_checks_cover_builtin_and_custom(self) -> None:
        self.integration.set_custom_suffixes([".psarc"])
        dialog = self._dialog()

        expected = set(ARCHIVE_SUFFIXES) | {".psarc"}
        self.assertEqual(set(dialog._checks), expected)
        self.assertEqual(dialog._candidate_extensions(), sorted(expected))
        self.assertEqual(dialog._checked_exts(), set())

    def test_state_mirrors_existing_registration(self) -> None:
        self.integration.register([".zip"], add_to_session=False, open_with=True)
        dialog = self._dialog()

        self.assertEqual(dialog._checked_exts(), {".zip"})
        self.assertFalse(dialog._add_session_check.isChecked())
        self.assertTrue(dialog._open_with_check.isChecked())
        self.assertTrue(dialog._extract_here_check.isChecked())
        self.assertIn("已关联 .zip", dialog._status_label.text())
        self.assertIn("已关闭", dialog._status_label.text())

    def test_status_text_when_unregistered(self) -> None:
        dialog = self._dialog()
        self.assertEqual(dialog._status_label.text(), "当前尚未注册系统集成。")

    # ---- 落盘 -------------------------------------------------------
    def test_apply_registers_checked_and_persists_settings(self) -> None:
        dialog = self._dialog()
        dialog._checks[".zip"].setChecked(True)
        dialog._checks[".7z"].setChecked(True)
        dialog._add_session_check.setChecked(True)
        dialog._open_with_check.setChecked(False)

        self._set_combo(dialog._density_combo, "compact")
        self._set_combo(dialog._layout_combo, "bottom")
        self._set_combo(dialog._format_combo, "7z")
        dialog._font_spin.setValue(120)
        dialog._text_spin.setValue(8)
        dialog._video_limit_spin.setValue(768)
        dialog._output_edit.setText(str(self.tmp / "out"))
        dialog._clean_check.setChecked(False)
        dialog.apply()

        self.assertEqual(self.integration.registered_extensions(), {".7z", ".zip"})
        self.assertTrue(registry.key_exists(self.root, si.add_to_session_key(self.prefix)))
        self.assertFalse(
            registry.key_exists(self.root, si.open_with_shell_key(".zip", self.prefix))
        )
        self.assertEqual(self.settings.value(KEY_FONT_SCALE, 0, int), 120)
        self.assertEqual(self.settings.value(KEY_DENSITY), "compact")
        self.assertEqual(self.settings.value(KEY_LAYOUT), "bottom")
        self.assertEqual(self.settings.value(KEY_OUTPUT_FORMAT), "7z")
        self.assertEqual(self.settings.value(KEY_OUTPUT_DIR), str(self.tmp / "out"))
        self.assertEqual(self.settings.value(KEY_PREVIEW_TEXT_LIMIT, 0, int), 8 * 1024 * 1024)
        self.assertEqual(
            self.settings.value(KEY_PREVIEW_VIDEO_AUTO_LIMIT, 0, int),
            768 * 1024 * 1024,
        )
        self.assertFalse(self.settings.value(KEY_TEMP_CLEAN_ON_CLOSE, True, bool))
        self.assertEqual(self.theme.font_scale, 120)
        self.assertEqual(self.theme.density, "compact")

    def test_apply_unregisters_unchecked_extension(self) -> None:
        self.integration.register([".zip", ".7z"])
        dialog = self._dialog()
        self.assertEqual(dialog._checked_exts(), {".7z", ".zip"})

        dialog._checks[".7z"].setChecked(False)
        dialog.apply()

        self.assertEqual(self.integration.registered_extensions(), {".zip"})
        self.assertIsNone(
            registry.read_value(self.root, si.open_with_progids_key(".7z", self.prefix), PROGID)
        )
        self.assertTrue(
            registry.key_exists(self.root, si.open_with_shell_key(".7z", self.prefix))
        )

    # ---- 自定义后缀 -------------------------------------------------
    def test_custom_suffix_add_validates_and_removes(self) -> None:
        dialog = self._dialog()

        with mock.patch.object(sd.QMessageBox, "warning") as warning:
            dialog._custom_edit.setText("??")
            dialog._add_custom()
            warning.assert_called_once()
        self.assertEqual(dialog._custom_exts, [])

        dialog._custom_edit.setText("PSARC")
        dialog._add_custom()
        self.assertEqual(dialog._custom_exts, [".psarc"])
        self.assertEqual(dialog._custom_list.count(), 1)
        self.assertIn(".psarc", dialog._checks)

        dialog._custom_edit.setText(".zip")
        dialog._add_custom()
        self.assertEqual(dialog._custom_exts, [".psarc"])
        self.assertIn("内置格式", dialog._status_label.text())

        dialog._custom_list.setCurrentRow(0)
        dialog._remove_custom()
        self.assertEqual(dialog._custom_exts, [])
        self.assertEqual(dialog._custom_list.count(), 0)
        self.assertNotIn(".psarc", dialog._checks)

    def test_removed_custom_suffix_is_unregistered_on_apply(self) -> None:
        self.integration.set_custom_suffixes([".psarc"])
        self.integration.register([".psarc"])
        dialog = self._dialog()
        self.assertEqual(dialog._custom_exts, [".psarc"])

        dialog._custom_list.setCurrentRow(0)
        dialog._remove_custom()
        dialog.apply()

        self.assertEqual(self.integration.custom_suffixes(), [])
        self.assertFalse(
            registry.has_value(
                self.root, si.open_with_progids_key(".psarc", self.prefix), PROGID
            )
        )
        self.assertEqual(self.integration.registered_extensions(), set())

    # ---- 一键撤销 ---------------------------------------------------
    def test_unregister_all_button_clears_registry(self) -> None:
        self.integration.register([".zip", ".7z"], add_to_session=True, open_with=True)
        dialog = self._dialog()

        with mock.patch.object(
            sd.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            dialog._unregister_all()

        status = self.integration.status()
        self.assertFalse(status.registered)
        self.assertEqual(status.extensions, ())
        self.assertEqual(dialog._checked_exts(), set())
        self.assertFalse(dialog._add_session_check.isChecked())
        self.assertFalse(dialog._open_with_check.isChecked())

    # ---- 临时目录 ---------------------------------------------------
    def test_temp_page_reports_usage_and_clears(self) -> None:
        (self.tmp / "session-a").mkdir()
        (self.tmp / "session-a" / "blob.bin").write_bytes(b"x" * 4096)
        dialog = self._dialog()

        self.assertIn("4.0 KB", dialog._usage_label.text())
        with mock.patch.object(
            sd.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            dialog._clear_temp()

        self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertIn("0 B", dialog._usage_label.text())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
