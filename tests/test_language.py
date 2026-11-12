"""Locale selection, catalogue parity, settings persistence and display boundaries."""
import ast
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QTranslator
from PySide6.QtWidgets import QApplication

from zipmerge.core.template import DEFAULT_TEMPLATE
from zipmerge.i18n import set_lookup, tr
from zipmerge.ui.language import load_language, resolve_language
from zipmerge.ui.main_window import MainWindow
from zipmerge.ui.settings_dialog import SettingsDialog
from zipmerge.util.paths import asset_path
from zipmerge.util.settings import KEY_LANGUAGE, KEY_TEMP_ROOT, Settings


class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_system_script_region_preference_and_fallback(self):
        for tag, expected in (("zh-Hant", "zh_TW"), ("zh-HK", "zh_TW"), ("zh-MO", "zh_TW"),
                              ("zh-Hant-CN", "zh_TW"), ("zh-SG", "zh_CN"), ("zh", "zh_CN"),
                              ("zh-Hans-TW", "zh_CN"), ("en-GB", "en"), ("fr-FR", "en")):
            with self.subTest(tag=tag):
                self.assertEqual(resolve_language("system", [tag]), expected)
                self.assertEqual(resolve_language("en", [tag]), "en")
        self.assertEqual(resolve_language("invalid", ["de-DE", "zh-TW"]), "zh_TW")

    def test_catalogues_match_sources_placeholders_and_compiled_messages(self):
        root = Path(__file__).resolve().parents[1]
        marked = set()
        for file in [root / "main.py", *(root / "src/zipmerge").rglob("*.py")]:
            for n in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr"
                        and n.args and isinstance(n.args[0], ast.Constant)):
                    marked.add(n.args[0].value)
        for lang in ("en", "zh_TW"):
            records = ET.parse(root / f"assets/i18n/zipmerge_{lang}.ts")
            catalog = QTranslator()
            self.assertTrue(catalog.load(str(asset_path("i18n", f"zipmerge_{lang}.qm"))))
            seen = set()
            for msg in records.iter("message"):
                source, translated = msg.findtext("source"), msg.findtext("translation")
                seen.add(source)
                with self.subTest(lang=lang, message=source):
                    self.assertTrue(translated)
                    self.assertEqual(sorted(re.findall(r"\{\d+\}", source)),
                                     sorted(re.findall(r"\{\d+\}", translated)))
                    self.assertEqual(catalog.translate("ZipMerge", source), translated)
            self.assertFalse(marked - seen)

    def test_settings_then_new_window_translates_only_app_owned_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings("ZipMergeTest", "Language")
            settings._store = QSettings(str(Path(tmp) / "test.ini"), QSettings.Format.IniFormat)
            settings.set_value(KEY_TEMP_ROOT, tmp)
            window = MainWindow(settings=settings)
            self.addCleanup(window.deleteLater)
            dialog = SettingsDialog(settings, window.theme, temp_root=Path(tmp), parent=window)
            self.assertEqual(dialog._language_combo.currentData(), "system")
            self.assertEqual([dialog._language_combo.itemText(i) for i in range(1,4)],
                             ["简体中文", "繁體中文", "English"])
            dialog._language_combo.setCurrentIndex(dialog._language_combo.findData("en"))
            dialog.reject()
            self.assertFalse(settings.contains(KEY_LANGUAGE))
            # Do not write actual Explorer integrations in a UI test.
            with patch.object(dialog, "_apply_integration"):
                dialog.apply()
            self.assertEqual(settings.value(KEY_LANGUAGE), "en")
            window.close()
            self.addCleanup(set_lookup, None)
            for code, file_menu, header, settings_title in (
                    ("en", "&File", "Name", "Settings"), ("zh_TW", "檔案(&F)", "名稱", "設定"),
                    ("zh_CN", "文件(&F)", "名称", "设置")):
                settings.set_value(KEY_LANGUAGE, code)
                settings.sync()
                reopened = Settings("ZipMergeTest", "Language")
                reopened._store = QSettings(str(Path(tmp) / "test.ini"), QSettings.Format.IniFormat)
                load_language(reopened)
                with self.subTest(code=code):
                    new = MainWindow(settings=reopened)
                    try:
                        self.assertEqual(new.menuBar().actions()[0].text(), file_menu)
                        dlg = SettingsDialog(reopened, new.theme, temp_root=Path(tmp), parent=new)
                        self.assertEqual(dlg.windowTitle(), settings_title)
                        self.assertEqual(dlg._language_combo.currentData(), code)
                        tab = new.current_tab()
                        node = tab.session.new_folder(tab.session.root, "导出")
                        model = tab.target_panel.model
                        self.assertEqual(model.headerData(0, Qt.Orientation.Horizontal), header)
                        self.assertEqual(model.index_for_node(node).data(), "导出")
                        self.assertEqual(tab.export_bar._template, DEFAULT_TEMPLATE)
                        self.assertIn("对白.wav", tr("文件不存在：{0}", "对白.wav"))
                    finally:
                        new.close()
                        new.deleteLater()
