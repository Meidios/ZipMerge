r"""系统集成单元测试：注册表键生成与撤销（PLAN 12.1）。

注册表用例全部在临时 HKCU 子树（`Software\ZipMergeTest-<uuid>`）里构造与清理，
不污染真实 HKCU；QSettings 使用独立的 ZipMergeTest 组织。
"""

from __future__ import annotations

import unittest
import uuid

from zipmerge.platform import registry
from zipmerge.platform import shell_integration as si
from zipmerge.platform.shell_integration import APP_KEY_NAME, PROGID, ShellIntegration
from zipmerge.util.paths import asset_path
from zipmerge.util.settings import Settings
from zipmerge.util.settings import (
    KEY_INSTALL_DEFAULTS_INITIALIZED,
    KEY_POST_INSTALL_PROMPT_PENDING,
)


class KeyPathTests(unittest.TestCase):
    """键路径必须与 PLAN 8.5 的布局逐字一致。"""

    def test_paths_without_prefix_match_plan(self) -> None:
        self.assertEqual(si.progid_key(), r"Software\Classes\ZipMerge.Archive")
        self.assertEqual(
            si.progid_command_key(),
            r"Software\Classes\ZipMerge.Archive\shell\open\command",
        )
        self.assertEqual(si.default_icon_key(), r"Software\Classes\ZipMerge.Archive\DefaultIcon")
        self.assertEqual(si.open_with_progids_key(".zip"), r"Software\Classes\.zip\OpenWithProgids")
        self.assertEqual(
            si.add_to_session_key(), r"Software\Classes\*\shell\ZipMerge.AddToSession"
        )
        self.assertEqual(
            si.open_with_shell_key(".7z"),
            r"Software\Classes\SystemFileAssociations\.7z\shell\ZipMerge.Open",
        )
        self.assertEqual(
            si.extract_here_shell_key(".7z"),
            r"Software\Classes\SystemFileAssociations\.7z\shell\ZipMerge.ExtractHere",
        )
        self.assertEqual(si.capabilities_key(), r"Software\ZipMerge\Capabilities")
        self.assertEqual(
            si.file_associations_key(), r"Software\ZipMerge\Capabilities\FileAssociations"
        )
        self.assertEqual(si.registered_applications_key(), r"Software\RegisteredApplications")

    def test_prefix_nests_every_path(self) -> None:
        prefix = r"Software\ZipMergeTest-x"
        for path in (
            si.progid_key(prefix),
            si.progid_command_key(prefix),
            si.default_icon_key(prefix),
            si.open_with_progids_key(".zip", prefix),
            si.add_to_session_key(prefix),
            si.open_with_shell_key(".zip", prefix),
            si.extract_here_shell_key(".zip", prefix),
            si.capabilities_key(prefix),
            si.file_associations_key(prefix),
            si.registered_applications_key(prefix),
        ):
            self.assertTrue(path.startswith(prefix + "\\"), path)

    def test_build_command_quotes_arguments(self) -> None:
        self.assertEqual(si.build_command("C:/App/a.exe", None, '"%1"'), '"C:/App/a.exe" "%1"')
        self.assertEqual(
            si.build_command("py.exe", "C:/App/main.py", "--add-to-session", '"%1"'),
            '"py.exe" "C:/App/main.py" --add-to-session "%1"',
        )

    def test_clean_extensions_normalizes_and_sorts(self) -> None:
        self.assertEqual(si._clean_extensions(["ZIP", ".7z", "zip", "bad!!", "", ".bz2"]),
                         [".7z", ".bz2", ".zip"])


@unittest.skipUnless(registry.available(), "需要 Windows 注册表")
class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = registry.hkey_current_user()
        self.prefix = rf"Software\ZipMergeTest-{uuid.uuid4().hex[:8]}"
        self.addCleanup(registry.delete_tree, self.root, self.prefix)
        self.settings = Settings("ZipMergeTest", "ZipMergeTest")

    def _key(self, name: str) -> str:
        return rf"{self.prefix}\{name}"

    def test_write_read_delete_roundtrip(self) -> None:
        key = self._key("roundtrip")
        self.assertTrue(registry.write_value(self.root, key, "text", "值"))
        self.assertTrue(registry.write_value(self.root, key, "none", b"", registry.REG_NONE))
        self.assertTrue(registry.key_exists(self.root, key))
        self.assertEqual(registry.read_value(self.root, key, "text"), "值")
        self.assertIs(registry.value_type(self.root, key, "none"), registry.REG_NONE)
        self.assertEqual(
            sorted(registry.list_values(self.root, key)), ["none", "text"]
        )

        self.assertTrue(registry.delete_value(self.root, key, "text"))
        self.assertIsNone(registry.read_value(self.root, key, "text"))
        self.assertFalse(registry.delete_value(self.root, key, "text"))

    def test_delete_tree_removes_subkeys(self) -> None:
        parent = self._key("tree")
        child = parent + r"\child\grand"
        registry.write_value(self.root, parent, "", "root")
        registry.write_value(self.root, child, "leaf", "1")
        self.assertEqual(registry.list_subkeys(self.root, parent), ["child"])
        self.assertTrue(registry.delete_tree(self.root, parent))
        self.assertFalse(registry.key_exists(self.root, parent))
        self.assertFalse(registry.key_exists(self.root, child))
        # 幂等：再删一次仍返回成功
        self.assertTrue(registry.delete_tree(self.root, parent))


@unittest.skipUnless(registry.available(), "需要 Windows 注册表")
class ShellIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = registry.hkey_current_user()
        self.prefix = rf"Software\ZipMergeTest-{uuid.uuid4().hex[:8]}"
        self.addCleanup(registry.delete_tree, self.root, self.prefix)
        self.settings = Settings("ZipMergeTest", "ZipMergeTest")
        self.settings.clear()
        self.addCleanup(self.settings.clear)
        self.integration = ShellIntegration(
            self.settings, prefix=self.prefix, executable=r"C:\App\ZipMerge.exe", script=None
        )

    def _read(self, path: str, name: str = ""):
        return registry.read_value(self.root, path, name)

    def test_add_to_session_registers_files_and_folders_and_shared_toggle_cleans_both(self):
        self.integration.install_context_menus()
        for key in si.add_to_session_keys(self.prefix):
            self.assertEqual(self._read(key, "MUIVerb"), si.MENU_ADD_TO_SESSION)
            self.assertEqual(self._read(si.command_key(key)), r'"C:\App\ZipMerge.exe" --add-to-session "%1"')
        self.integration.install_context_menus(add_to_session=False)
        for key in si.add_to_session_keys(self.prefix):
            self.assertFalse(registry.key_exists(self.root, key))
        for key in si.compress_here_keys(self.prefix):
            self.assertTrue(registry.key_exists(self.root, key))
        self.integration.install_context_menus(add_to_session=True)
        self.integration.unregister()
        for key in si.add_to_session_keys(self.prefix):
            self.assertFalse(registry.key_exists(self.root, key))

    def test_upgrade_adds_missing_directory_verb_without_reenabling_disabled_preferences(self):
        self.integration.initialize_install_defaults()
        directory_key = si.add_to_session_keys(self.prefix)[1]
        registry.delete_tree(self.root, directory_key)
        self.assertFalse(self.integration.initialize_install_defaults())
        self.assertTrue(registry.key_exists(self.root, directory_key))
        self.integration.install_context_menus(add_to_session=False)
        self.integration.initialize_install_defaults()
        self.assertFalse(registry.key_exists(self.root, directory_key))

    def test_register_writes_matching_values(self) -> None:
        written = self.integration.register([".zip", ".7z"])

        for key in (
            si.progid_key(self.prefix),
            si.progid_command_key(self.prefix),
            si.default_icon_key(self.prefix),
            si.open_with_progids_key(".zip", self.prefix),
            si.add_to_session_key(self.prefix),
            si.open_with_shell_key(".zip", self.prefix),
            si.extract_here_shell_key(".zip", self.prefix),
            si.capabilities_key(self.prefix),
            si.file_associations_key(self.prefix),
            si.registered_applications_key(self.prefix),
        ):
            self.assertIn(key, written, key)

        for key in si.compress_here_keys(self.prefix):
            self.assertEqual(self._read(key, "MUIVerb"), si.MENU_COMPRESS_HERE)
            self.assertEqual(self._read(key, "MultiSelectModel"), "Single")
            self.assertEqual(self._read(si.command_key(key)), r'"C:\App\ZipMerge.exe" --compress-here "%1"')

        self.assertEqual(self._read(si.progid_key(self.prefix)), PROGID)
        self.assertEqual(
            self._read(si.progid_command_key(self.prefix)), r'"C:\App\ZipMerge.exe" "%1"'
        )
        # 文件类型用独立文档轮廓；右键操作保留应用图标，资源管理器中不会混淆。
        archive_icon = self._read(si.default_icon_key(self.prefix))
        app_icon = f'{asset_path("icons", "ZipMerge.ico")},0'
        self.assertEqual(archive_icon, f'{asset_path("icons", "ZipMergeArchive.ico")},0')
        self.assertEqual(self._read(si.add_to_session_key(self.prefix), "Icon"), app_icon)
        self.assertEqual(self._read(si.open_with_shell_key(".zip", self.prefix), "Icon"), app_icon)
        self.assertEqual(self._read(si.extract_here_shell_key(".zip", self.prefix), "Icon"), app_icon)
        self.assertIs(
            registry.value_type(self.root, si.open_with_progids_key(".zip", self.prefix), PROGID),
            registry.REG_NONE,
        )
        self.assertEqual(
            self._read(si.file_associations_key(self.prefix), ".7z"), PROGID
        )
        self.assertEqual(
            self._read(si.registered_applications_key(self.prefix), APP_KEY_NAME),
            si.capabilities_key(self.prefix),
        )
        self.assertEqual(
            self._read(si.add_to_session_key(self.prefix), "MUIVerb"), si.MENU_ADD_TO_SESSION
        )
        self.assertEqual(
            self._read(si.command_key(si.add_to_session_key(self.prefix))),
            r'"C:\App\ZipMerge.exe" --add-to-session "%1"',
        )
        self.assertEqual(
            self._read(si.open_with_shell_key(".zip", self.prefix), "MUIVerb"), si.MENU_OPEN_WITH
        )
        self.assertEqual(
            self._read(si.extract_here_shell_key(".zip", self.prefix), "MUIVerb"),
            si.MENU_EXTRACT_HERE,
        )
        self.assertEqual(
            self._read(si.command_key(si.extract_here_shell_key(".zip", self.prefix))),
            r'"C:\App\ZipMerge.exe" --extract-here "%1"',
        )

    def test_register_does_not_write_extension_default_value(self) -> None:
        # 回归：绝不能改写 .zip 的 (Default)，否则会抢占 Bandizip 等现有默认程序
        classes = rf"{self.prefix}\Software\Classes"
        registry.write_value(self.root, rf"{classes}\.zip", "", "Bandizip.zip")
        self.integration.register([".zip"], add_to_session=False, open_with=False)
        self.assertEqual(self._read(rf"{classes}\.zip"), "Bandizip.zip")
        self.integration.unregister()
        self.assertEqual(self._read(rf"{classes}\.zip"), "Bandizip.zip")

    def test_status_reflects_registration(self) -> None:
        self.assertFalse(self.integration.status().registered)
        self.integration.register([".zip", ".7z"], add_to_session=True, open_with=True)
        status = self.integration.status()
        self.assertTrue(status.registered)
        self.assertEqual(status.extensions, (".7z", ".zip"))
        self.assertTrue(status.add_to_session)
        self.assertTrue(status.open_with)
        self.assertTrue(status.extract_here)
        self.assertTrue(status.capabilities)

    def test_install_context_menus_does_not_create_file_associations(self) -> None:
        written = self.integration.install_context_menus()
        status = self.integration.status()

        self.assertTrue(status.registered)
        self.assertEqual(status.extensions, ())
        self.assertTrue(status.add_to_session)
        self.assertTrue(status.open_with)
        self.assertTrue(status.extract_here)
        self.assertFalse(status.capabilities)
        self.assertFalse(registry.key_exists(self.root, si.progid_key(self.prefix)))
        self.assertIsNone(
            self._read(si.open_with_progids_key(".zip", self.prefix), PROGID)
        )
        for key in (
            si.add_to_session_key(self.prefix),
            si.open_with_shell_key(".zip", self.prefix),
            si.extract_here_shell_key(".rar", self.prefix),
        ):
            self.assertIn(key, written)
            self.assertTrue(registry.key_exists(self.root, key))

    def test_install_defaults_enable_three_menus_once(self) -> None:
        self.assertTrue(self.integration.initialize_install_defaults())
        status = self.integration.status()
        self.assertTrue(status.add_to_session)
        self.assertTrue(status.open_with)
        self.assertTrue(status.extract_here)
        self.assertEqual(status.extensions, ())
        self.assertTrue(
            self.settings.value(KEY_INSTALL_DEFAULTS_INITIALIZED, False, bool)
        )
        self.assertTrue(
            self.settings.value(KEY_POST_INSTALL_PROMPT_PENDING, False, bool)
        )

        self.integration.install_context_menus(open_with=False)
        self.assertFalse(self.integration.initialize_install_defaults())
        self.assertFalse(self.integration.status().open_with)

    def test_upgrade_rewrites_file_icon_without_changing_associations(self) -> None:
        self.integration.register([".zip", ".7z"], add_to_session=False, open_with=True)
        self.settings.set_value(KEY_INSTALL_DEFAULTS_INITIALIZED, True)
        registry.write_value(
            self.root,
            si.default_icon_key(self.prefix),
            "",
            "C:\\Old\\ZipMerge.ico,0",
        )

        self.assertFalse(self.integration.initialize_install_defaults())

        self.assertEqual(self.integration.registered_extensions(), {".zip", ".7z"})
        self.assertFalse(self.integration.status().add_to_session)
        self.assertTrue(self.integration.status().open_with)
        self.assertEqual(
            self._read(si.default_icon_key(self.prefix)),
            f'{asset_path("icons", "ZipMergeArchive.ico")},0',
        )

    def test_upgrade_without_file_association_does_not_create_progid(self) -> None:
        self.assertTrue(self.integration.initialize_install_defaults())
        self.assertFalse(self.integration.initialize_install_defaults())
        self.assertFalse(registry.key_exists(self.root, si.progid_key(self.prefix)))

    def test_menu_toggles_are_independent(self) -> None:
        self.integration.register([".zip"], add_to_session=False, open_with=True)
        self.assertFalse(registry.key_exists(self.root, si.add_to_session_key(self.prefix)))
        self.assertTrue(registry.key_exists(self.root, si.open_with_shell_key(".zip", self.prefix)))

        self.integration.register([".zip"], add_to_session=True, open_with=False)
        self.assertTrue(registry.key_exists(self.root, si.add_to_session_key(self.prefix)))
        self.assertFalse(registry.key_exists(self.root, si.open_with_shell_key(".zip", self.prefix)))

    def test_apply_removes_unchecked_extensions(self) -> None:
        self.integration.register([".zip", ".7z", ".rar"])
        self.integration.apply([".zip"])

        self.assertEqual(self.integration.registered_extensions(), {".zip"})
        self.assertIsNone(self._read(si.open_with_progids_key(".7z", self.prefix), PROGID))
        # 关联取消后仍保留受支持格式的右键菜单；两者是独立设置。
        self.assertTrue(registry.key_exists(self.root, si.open_with_shell_key(".7z", self.prefix)))
        self.assertIsNone(self._read(si.file_associations_key(self.prefix), ".7z"))
        self.assertTrue(
            registry.has_value(self.root, si.open_with_progids_key(".zip", self.prefix), PROGID)
        )

    def test_unregister_extensions_keeps_others(self) -> None:
        self.integration.register([".zip", ".7z"])
        self.integration.unregister_extensions([".7z"])
        self.assertEqual(self.integration.registered_extensions(), {".zip"})
        self.assertTrue(registry.key_exists(self.root, si.add_to_session_key(self.prefix)))

    def test_unregister_clears_every_known_key(self) -> None:
        self.integration.register([".zip", ".7z"], add_to_session=True, open_with=True)
        self.integration.unregister()

        status = self.integration.status()
        self.assertFalse(status.registered)
        self.assertEqual(status.extensions, ())
        self.assertFalse(status.add_to_session)
        self.assertFalse(status.open_with)
        self.assertFalse(status.extract_here)
        self.assertFalse(status.capabilities)
        self.assertFalse(registry.key_exists(self.root, si.progid_key(self.prefix)))
        self.assertIsNone(
            self._read(si.registered_applications_key(self.prefix), APP_KEY_NAME)
        )
        self.assertIsNone(self._read(si.open_with_progids_key(".zip", self.prefix), PROGID))
        self.assertFalse(self.settings.value(si.KEY_INTEGRATION_EXTENSIONS))

    def test_register_persists_settings(self) -> None:
        self.integration.register([".zip", ".7z"], add_to_session=False, open_with=True)
        self.assertEqual(self.settings.value(si.KEY_INTEGRATION_EXTENSIONS), [".7z", ".zip"])
        self.assertFalse(self.settings.value(si.KEY_INTEGRATION_ADD_TO_SESSION, True, bool))
        self.assertTrue(self.settings.value(si.KEY_INTEGRATION_OPEN_WITH, False, bool))
        self.assertTrue(self.settings.value(si.KEY_INTEGRATION_EXTRACT_HERE, False, bool))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
