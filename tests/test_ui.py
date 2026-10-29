"""界面层单元测试：展示格式化、源包树模型、导出栏选项与命名。

Qt 部分统一走离屏平台，QSettings 使用独立的 ``ZipMergeTest`` 组织，
避免污染真实 HKCU 中的用户设置。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.core import MergeSession, NamingFields, render_filename  # noqa: E402
from zipmerge.engine import create_archive  # noqa: E402
from zipmerge.ui import fmt  # noqa: E402
from zipmerge.ui import export_bar as export_bar_module  # noqa: E402
from zipmerge.ui.export_bar import DEFAULT_LEVEL, ExportBar  # noqa: E402
from zipmerge.ui.source_model import SourceTreeModel  # noqa: E402
from zipmerge.ui.target_model import MIME_SOURCE  # noqa: E402
from zipmerge.ui.temporary_archive import STATE_READY, STATE_STALE  # noqa: E402
from zipmerge.util.settings import (  # noqa: E402
    KEY_ACTIVE_TEMPLATE,
    KEY_NAMING_CONTENT,
    KEY_NAMING_TEMPLATE,
    KEY_OUTPUT_DIR,
    KEY_OUTPUT_DIR_AUTO,
    KEY_OUTPUT_FORMAT,
    KEY_OUTPUT_LEVEL,
    KEY_TEMPLATES,
    Settings,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class FmtTests(unittest.TestCase):
    def test_human_size_steps(self) -> None:
        self.assertEqual(fmt.human_size(0), "0 B")
        self.assertEqual(fmt.human_size(1023), "1023 B")
        self.assertEqual(fmt.human_size(1024), "1.0 KB")
        self.assertEqual(fmt.human_size(1536), "1.5 KB")
        self.assertEqual(fmt.human_size(1024 * 1024), "1.0 MB")
        self.assertEqual(fmt.human_size(-1), "—")

    def test_count_label(self) -> None:
        self.assertEqual(fmt.count_label(3, "个文件"), "3 个文件")
        self.assertEqual(fmt.count_label(0), "0 项")


class SourceTreeModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-srcmodel-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="源", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

        src = self.tmp / "src"
        _write_files(src, {"SFX/boom.wav": b"A" * 64, "readme.txt": b"R"})
        archive = self.tmp / "包A.zip"
        create_archive(archive, "zip", src)
        self.session.import_archive(archive)
        self.source_id = next(iter(self.session.sources))
        self.model = SourceTreeModel(self.session)

    def test_headers_and_rows(self) -> None:
        self.assertEqual(self.model.columnCount(), 3)
        self.assertEqual(
            self.model.headerData(0, Qt.Orientation.Horizontal), "源包 / 条目"
        )
        self.assertEqual(self.model.headerData(1, Qt.Orientation.Horizontal), "类型")
        self.assertEqual(self.model.rowCount(QModelIndex()), 1)

    def test_find_entry_index_three_states(self) -> None:
        self.assertTrue(self.model.find_entry_index(self.source_id, "").isValid())
        self.assertTrue(self.model.find_entry_index(self.source_id, "SFX\\boom.wav").isValid())
        self.assertFalse(self.model.find_entry_index(self.source_id, "不存在").isValid())
        self.assertFalse(self.model.find_entry_index("别家", "").isValid())

    def test_size_and_type_columns(self) -> None:
        self.assertEqual(self.model.data(self.model.index(0, 1, QModelIndex())), "ZIP")
        # 根节点大小列为总大小
        size_text = self.model.data(self.model.index(0, 2, QModelIndex()))
        self.assertTrue(size_text.endswith("B"), size_text)

    def test_mime_payload_single_file_and_root(self) -> None:
        entry = self.model.find_entry_index(self.source_id, "SFX\\boom.wav")
        payload = json.loads(bytes(self.model.mimeData([entry]).data(MIME_SOURCE)).decode("utf-8"))
        self.assertEqual(payload["source_id"], self.source_id)
        self.assertEqual(payload["entry_path"], "SFX\\boom.wav")
        self.assertFalse(payload["whole"])

        root_index = self.model.index(0, 0, QModelIndex())
        whole = json.loads(bytes(self.model.mimeData([root_index]).data(MIME_SOURCE)).decode("utf-8"))
        self.assertEqual(whole["entry_path"], "")
        self.assertTrue(whole["whole"])

    def test_mime_payload_deduplicates_selected_parent_and_child(self) -> None:
        folder = self.model.find_entry_index(self.source_id, "SFX")
        child = self.model.find_entry_index(self.source_id, "SFX\\boom.wav")
        payload = json.loads(
            bytes(self.model.mimeData([folder, child]).data(MIME_SOURCE)).decode("utf-8")
        )
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["entry_path"], "SFX")

    def test_reload_matches_session_sources(self) -> None:
        second = self.tmp / "包B.7z"
        create_archive(second, "7z", self.tmp / "src")
        self.session.import_archive(str(second))
        self.model.reload()
        self.assertEqual(self.model.rowCount(QModelIndex()), 2)


class ExportBarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-exportbar-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = Settings("ZipMergeTest", "ZipMergeTest")
        self.settings.clear()
        self.addCleanup(self.settings.clear)

        self.session = MergeSession(name="导出", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)
        src = self.tmp / "src"
        _write_files(src, {"SFX/boom.wav": b"A" * 32})
        archive = self.tmp / "项目_音效SFX_20260910.zip"
        create_archive(archive, "zip", src)
        self.session.import_archive(archive)

        self.bar = ExportBar(self.session, self.settings)
        self.addCleanup(self.bar.deleteLater)

    def test_defaults_render_clean_base_name(self) -> None:
        today = NamingFields.today().date
        # 源包名含 SFX → 内容标识被建议填入，连续分隔符收敛
        self.assertEqual(self.bar.base_name(), f"SFX_{today}_V1")
        self.assertEqual(self.bar.target_path().suffix, ".zip")
        self.assertEqual(self.bar.build_options().fmt, "zip")
        self.assertEqual(self.bar.build_options().level, DEFAULT_LEVEL)

    def test_custom_template_label_has_reserved_width_and_single_menu_indicator(self) -> None:
        self.assertGreaterEqual(
            self.bar._template_combo.minimumContentsLength(), len("（自定义）")
        )
        self.assertEqual(self.bar._template_menu_button.objectName(), "templateMenuButton")

    def test_content_tag_suggested_from_source_names(self) -> None:
        self.assertEqual(self.bar.fields().content, "SFX")

    def test_user_edit_guard_keeps_cleared_value(self) -> None:
        self.bar._content_edit.clear()
        self.bar._content_edit.textEdited.emit("")
        self.bar.refresh_from_session()
        self.assertEqual(self.bar.fields().content, "")

    def test_format_switch_updates_suffix_and_options(self) -> None:
        index = self.bar._format_combo.findData("7z")
        self.bar._format_combo.setCurrentIndex(index)
        self.assertEqual(self.bar.target_path().suffix, ".7z")
        self.assertEqual(self.bar.build_options().fmt, "7z")
        self.assertTrue(self.bar._preview_edit.text().endswith(".7z"))

    def test_level_selected_persists(self) -> None:
        self.bar._level_combo.setCurrentIndex(self.bar._level_combo.findData(9))
        self.assertEqual(self.bar.build_options().level, 9)
        self.settings.sync()
        self.assertEqual(Settings("ZipMergeTest", "ZipMergeTest").value(KEY_OUTPUT_LEVEL, 5, int), 9)

    def test_naming_fields_persist_across_instances(self) -> None:
        self.bar._project_edit.setText("项目A")
        self.settings.sync()
        reloaded = Settings("ZipMergeTest", "ZipMergeTest")
        self.assertEqual(reloaded.value("naming/project"), "项目A")
        self.assertEqual(reloaded.value(KEY_NAMING_CONTENT), "SFX")

    def test_output_directory_override(self) -> None:
        self.bar._dir_edit.setText(str(self.tmp / "out"))
        self.bar._on_dir_changed()
        self.assertEqual(self.bar.output_directory(), self.tmp / "out")
        self.assertEqual(self.bar.target_path().parent, self.tmp / "out")

    def test_existing_file_marks_preview_warning(self) -> None:
        out_dir = self.tmp / "out"
        out_dir.mkdir()
        self.bar._dir_edit.setText(str(out_dir))
        self.bar._on_dir_changed()
        self.bar.target_path().write_bytes(b"x")
        self.bar._update_preview()
        self.assertEqual(self.bar._preview_edit.property("variant"), "warning")

    def test_build_options_uses_session_stage_dir(self) -> None:
        self.assertIsNotNone(self.bar.build_options().stage_dir)

    def test_output_password_is_confirmed_and_kept_in_session_only(self) -> None:
        with mock.patch.object(
            export_bar_module.QInputDialog,
            "getText",
            side_effect=[("交付密码", True), ("交付密码", True)],
        ):
            self.bar._prompt_output_password()
        self.assertEqual(self.bar.build_options().password, "交付密码")
        self.assertEqual(self.bar._password_button.text(), "已加密")
        self.assertIsNone(self.settings.value("export/password"))

    # ---- 临时交付包 -------------------------------------------------
    def _temporary_file(self) -> Path:
        path = self.session.create_temporary_output(self.bar.output_filename())
        path.write_bytes(b"temporary-archive")
        return path

    def test_temporary_request_does_not_use_or_create_output_directory(self) -> None:
        unavailable = self.tmp / "does-not-exist" / "nested"
        self.bar._dir_edit.setText(str(unavailable))
        self.bar._on_dir_changed()
        requested: list[bool] = []
        self.bar.temporary_export_requested.connect(lambda: requested.append(True))

        self.bar.request_temporary_export()

        self.assertEqual(requested, [True])
        self.assertFalse(unavailable.exists())
        candidate = self.session.create_temporary_output(self.bar.output_filename())
        options = self.bar.build_options(candidate)
        self.assertEqual(options.output_path, candidate)
        self.assertEqual(options.fmt, self.bar._format_combo.currentData())
        self.assertEqual(options.level, self.bar._level_combo.currentData())

    def test_temporary_card_appears_and_directory_change_does_not_expire_it(self) -> None:
        path = self._temporary_file()
        self.bar.set_temporary_archive(path)
        self.assertFalse(self.bar._temporary_card.isHidden())
        self.assertEqual(self.bar._temporary_card.state, STATE_READY)
        self.assertEqual(self.bar._temporary_button.text(), "重新生成临时包")

        self.bar._dir_edit.setText(str(self.tmp / "another-output"))
        self.bar._on_dir_changed()
        self.bar.refresh_from_session()
        self.assertEqual(self.bar._temporary_card.state, STATE_READY)

    def test_temporary_card_expires_on_content_or_export_option_change(self) -> None:
        path = self._temporary_file()
        self.bar.set_temporary_archive(path)
        self.bar._project_edit.setText("项目已修改")
        self.assertEqual(self.bar._temporary_card.state, STATE_STALE)

        self.bar.set_temporary_archive(path)
        self.bar._format_combo.setCurrentIndex(self.bar._format_combo.findData("7z"))
        self.assertEqual(self.bar._temporary_card.state, STATE_STALE)

        self.bar.set_temporary_archive(path)
        self.bar._level_combo.setCurrentIndex(self.bar._level_combo.findData(9))
        self.assertEqual(self.bar._temporary_card.state, STATE_STALE)

        self.bar.set_temporary_archive(path)
        self.bar.refresh_from_session(invalidate_temporary=True)
        self.assertEqual(self.bar._temporary_card.state, STATE_STALE)

    def test_applying_template_expires_temporary_card(self) -> None:
        self.assertTrue(self.bar.save_template("客户模板"))
        self.bar._project_edit.setText("另一个项目")
        path = self._temporary_file()
        self.bar.set_temporary_archive(path)
        self.assertTrue(self.bar.apply_template("客户模板"))
        self.assertEqual(self.bar._temporary_card.state, STATE_STALE)

    # ---- 输出目录自动跟随（PLAN 6.6 问题 4）---------------------------
    def test_output_dir_follows_first_source_by_default(self) -> None:
        self.assertEqual(self.bar.output_directory(), self.tmp)
        second = self.tmp / "子目录"
        second.mkdir()
        other = second / "包B.zip"
        create_archive(other, "zip", self.tmp / "src")
        self.session.import_archive(other)
        self.bar.refresh_from_session()
        # 自动模式只跟随「首个」源包
        self.assertEqual(self.bar.output_directory(), self.tmp)

    def test_manual_dir_edit_stops_auto_follow(self) -> None:
        chosen = self.tmp / "out"
        self.bar._dir_edit.setText(str(chosen))
        self.bar._dir_edit.textEdited.emit(str(chosen))
        self.bar._on_dir_changed()
        self.bar.refresh_from_session()
        self.assertEqual(self.bar.output_directory(), chosen)
        self.assertFalse(self.settings.value(KEY_OUTPUT_DIR_AUTO, True, bool))

    def test_auto_follow_disabled_after_explicit_setting(self) -> None:
        self.settings.set_value(KEY_OUTPUT_DIR_AUTO, False)
        self.settings.set_value(KEY_OUTPUT_DIR, str(self.tmp / "fixed"))
        self.bar.reload_settings()
        self.assertEqual(self.bar.output_directory(), self.tmp / "fixed")

    # ---- 设置页改动后重读（PLAN 6.6 问题 3）---------------------------
    def test_reload_settings_refreshes_open_bar(self) -> None:
        self.settings.set_value(KEY_OUTPUT_DIR_AUTO, False)
        self.settings.set_value(KEY_OUTPUT_DIR, str(self.tmp / "newout"))
        self.settings.set_value(KEY_OUTPUT_FORMAT, "7z")
        self.settings.set_value(KEY_OUTPUT_LEVEL, 9)
        self.settings.set_value("naming/project", "改过的项目")
        self.bar.reload_settings()
        self.assertEqual(self.bar.output_directory(), self.tmp / "newout")
        self.assertEqual(self.bar.build_options().fmt, "7z")
        self.assertEqual(self.bar.build_options().level, 9)
        self.assertEqual(self.bar.fields().project, "改过的项目")

    # ---- 模板库（PLAN 6.6 新增 3）------------------------------------
    def test_template_save_apply_rename_delete(self) -> None:
        self.bar._project_edit.setText("项目甲")
        self.bar._version_edit.setText("V9")
        self.assertTrue(self.bar.save_template("模板甲"))
        expected = render_filename(self.bar._template, self.bar.fields())
        self.assertIn("模板甲", [self.bar._template_combo.itemText(i)
                                  for i in range(self.bar._template_combo.count())])

        # 改动字段 → 退回（自定义）；重新套用模板回到快照值
        self.bar._project_edit.setText("项目乙")
        self.assertIsNone(self.bar._template_combo.currentData())
        self.assertTrue(self.bar.apply_template("模板甲"))
        self.assertEqual(self.bar.fields().project, "项目甲")
        self.assertEqual(render_filename(self.bar._template, self.bar.fields()), expected)

        self.assertTrue(self.bar.rename_template("模板甲", "模板乙"))
        self.assertEqual(self.settings.value(KEY_ACTIVE_TEMPLATE), "模板乙")
        self.assertTrue(self.bar.delete_template("模板乙"))
        persisted = json.loads(str(self.settings.value(KEY_TEMPLATES)))
        self.assertEqual([item["name"] for item in persisted], ["默认"])
        self.assertEqual([item["name"] for item in self.bar._templates], ["默认"])

    def test_template_roundtrips_through_settings(self) -> None:
        self.bar._project_edit.setText("项目甲")
        self.bar._requirement_edit.setText("需求甲")
        self.bar.save_template("甲方案")
        self.settings.sync()
        reloaded = ExportBar(self.session, Settings("ZipMergeTest", "ZipMergeTest"))
        self.addCleanup(reloaded.deleteLater)
        self.assertEqual(reloaded._template_combo.currentData(), "甲方案")
        self.assertEqual(reloaded.fields().project, "项目甲")

    def test_empty_library_migrates_legacy_naming_keys(self) -> None:
        self.settings.set_value(KEY_TEMPLATES, "")
        self.settings.set_value("naming/project", "旧项目")
        self.settings.set_value(KEY_NAMING_TEMPLATE, "{项目名}-{版本}")
        bar = ExportBar(self.session, self.settings)
        self.addCleanup(bar.deleteLater)
        self.assertEqual([item["name"] for item in bar._templates], ["默认"])
        bar._project_edit.setText("临时")
        self.assertTrue(bar.apply_template("默认"))
        self.assertEqual(bar.fields().project, "旧项目")
        self.assertEqual(bar._template, "{项目名}-{版本}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
