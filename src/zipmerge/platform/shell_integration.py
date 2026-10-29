"""Windows 系统集成：ProgID 关联、打开方式候选、右键菜单与卸载清理。

设计见 PLAN 8。全部只写 `HKCU`，免管理员；只把 ZipMerge 写进「打开方式候选」
与系统「默认应用」列表，绝不改写扩展名的 `(Default)` 值，因此不会抢占 Bandizip 等
现有默认程序。

所有键路径由模块级纯函数拼装（`prefix` 参数让测试指向临时 HKCU 子树），注册与撤销
共用同一套路径，避免两边漂移；设置页与实际注册项也因此不会不一致（PLAN 10）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from ..util.paths import app_root, archive_suffixes, asset_path, is_frozen, normalize_suffix
from ..util.paths import set_extra_archive_suffixes
from ..util.settings import (
    KEY_INTEGRATION_ADD_TO_SESSION,
    KEY_INTEGRATION_CUSTOM_SUFFIXES,
    KEY_INTEGRATION_EXTENSIONS,
    KEY_INTEGRATION_EXTRACT_HERE,
    KEY_INTEGRATION_OPEN_WITH,
    KEY_INSTALL_DEFAULTS_INITIALIZED,
    KEY_POST_INSTALL_PROMPT_PENDING,
    Settings,
)
from . import registry

#: 「默认应用」页里显示的名称，也是 RegisteredApplications 的值名
APP_KEY_NAME = "ZipMerge"

#: 文件关联 ProgID
PROGID = "ZipMerge.Archive"

#: 右键项的子键名
ADD_TO_SESSION_KEY = "ZipMerge.AddToSession"
OPEN_WITH_KEY = "ZipMerge.Open"
EXTRACT_HERE_KEY = "ZipMerge.ExtractHere"
COMPRESS_HERE_KEY = "ZipMerge.CompressHere"

#: 右键菜单显示名（PLAN 8.3）
MENU_ADD_TO_SESSION = "添加到当前合并会话"
MENU_OPEN_WITH = "用 ZipMerge 打开"
MENU_EXTRACT_HERE = "解压到此处"
MENU_COMPRESS_HERE = "用 ZipMerge 打包为 ZIP"

APP_DESCRIPTION = "多压缩包合并工作台"

#: 区分「构造时未传 script」（自动判定）与「显式传 None」（不拼脚本）
_AUTO = object()

_CLASSES = r"Software\Classes"
_SYSTEM_FILE_ASSOCIATIONS = _CLASSES + r"\SystemFileAssociations"
_CAPABILITIES = r"Software\ZipMerge\Capabilities"
_FILE_ASSOCIATIONS = _CAPABILITIES + r"\FileAssociations"
_REGISTERED_APPLICATIONS = r"Software\RegisteredApplications"

# SHChangeNotify：文件关联变化。必须在覆盖安装重写图标路径后通知 Explorer，
# 否则相同 ProgID / ico 路径可能继续命中旧的进程内缓存。
_SHCNE_ASSOCCHANGED = 0x08000000
_SHCNF_IDLIST = 0x0000
_SHCNF_FLUSH = 0x1000


def notify_shell_associations_changed() -> bool:
    """同步通知 Windows Shell 重新读取文件关联与图标；失败时静默降级。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(
            _SHCNE_ASSOCCHANGED,
            _SHCNF_IDLIST | _SHCNF_FLUSH,
            None,
            None,
        )
        return True
    except (AttributeError, OSError):
        return False


# ---- 键路径（纯函数，供注册 / 撤销 / 单测共用） ----------------------


def _under(prefix: str, path: str) -> str:
    base = prefix.strip("\\")
    return f"{base}\\{path}" if base else path


def progid_key(prefix: str = "") -> str:
    return _under(prefix, f"{_CLASSES}\\{PROGID}")


def progid_command_key(prefix: str = "") -> str:
    return f"{progid_key(prefix)}\\shell\\open\\command"


def default_icon_key(prefix: str = "") -> str:
    return f"{progid_key(prefix)}\\DefaultIcon"


def open_with_progids_key(ext: str, prefix: str = "") -> str:
    return _under(prefix, f"{_CLASSES}\\{ext}\\OpenWithProgids")


def add_to_session_key(prefix: str = "") -> str:
    return _under(prefix, f"{_CLASSES}\\*\\shell\\{ADD_TO_SESSION_KEY}")


def add_to_session_keys(prefix: str = "") -> tuple[str, str]:
    return (
        add_to_session_key(prefix),
        _under(prefix, f"{_CLASSES}\\Directory\\shell\\{ADD_TO_SESSION_KEY}"),
    )


def open_with_shell_key(ext: str, prefix: str = "") -> str:
    return _under(prefix, f"{_SYSTEM_FILE_ASSOCIATIONS}\\{ext}\\shell\\{OPEN_WITH_KEY}")


def extract_here_shell_key(ext: str, prefix: str = "") -> str:
    return _under(prefix, f"{_SYSTEM_FILE_ASSOCIATIONS}\\{ext}\\shell\\{EXTRACT_HERE_KEY}")


def command_key(parent: str) -> str:
    return f"{parent}\\command"


def compress_here_keys(prefix: str = "") -> tuple[str, str]:
    return tuple(
        _under(prefix, f"{_CLASSES}\\{kind}\\shell\\{COMPRESS_HERE_KEY}")
        for kind in ("*", "Directory")
    )


def capabilities_key(prefix: str = "") -> str:
    return _under(prefix, _CAPABILITIES)


def file_associations_key(prefix: str = "") -> str:
    return _under(prefix, _FILE_ASSOCIATIONS)


def registered_applications_key(prefix: str = "") -> str:
    return _under(prefix, _REGISTERED_APPLICATIONS)


def build_command(executable, script=None, *args: str) -> str:
    """拼出注册表 `command` 值：可执行文件 + 可选脚本 + 附加参数（含引号）。"""
    parts = [f'"{executable}"']
    if script:
        parts.append(f'"{script}"')
    parts.extend(args)
    return " ".join(parts)


def _clean_extensions(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        suffix = normalize_suffix(str(value))
        if suffix and suffix not in seen:
            seen.append(suffix)
    return sorted(seen)


@dataclass(frozen=True)
class ShellStatus:
    """系统集成的当前注册状态快照（设置页据此回显）。"""

    registered: bool
    extensions: tuple[str, ...]
    add_to_session: bool
    open_with: bool
    extract_here: bool
    capabilities: bool


class ShellIntegration:
    """注册 / 撤销 / 状态查询的唯一入口（PLAN 10）。"""

    def __init__(
        self,
        settings: Settings,
        *,
        prefix: str = "",
        executable=None,
        script=_AUTO,
        root=None,
    ) -> None:
        self.settings = settings
        self.prefix = prefix.strip("\\")
        self.root = registry.hkey_current_user() if root is None else root
        self._executable = Path(executable) if executable else Path(sys.executable)
        if script is _AUTO:
            # 未显式传入：源码运行时 command 要带上 main.py；打包后 exe 本身就是入口
            self._script = None if is_frozen() else app_root() / "main.py"
        elif script:
            self._script = Path(script)
        else:
            self._script = None

    # ---- 路径与命令 -------------------------------------------------
    @property
    def executable(self) -> Path:
        return self._executable

    def command(self, *args: str) -> str:
        return build_command(self._executable, self._script, *args)

    def refresh_shell(self) -> bool:
        """刷新真实用户 Shell；临时注册表前缀测试不触碰 Explorer。"""
        if self.prefix:
            return False
        return notify_shell_associations_changed()

    def _icon(self, *, archive: bool = False) -> str:
        """返回应用操作或压缩包文件类型的显示图标。

        优先用随包分发的 ico：exe 在源码运行时是 python.exe，直接指向它只会显示
        Python 的图标（PLAN 8.3 问题 10）；ico 缺失时回退到 exe 自身。文件关联
        使用独立的文档轮廓，右键操作仍使用应用图标，避免资源管理器中混淆。
        """
        filename = "ZipMergeArchive.ico" if archive else "ZipMerge.ico"
        ico = asset_path("icons", filename)
        if ico.is_file():
            return f'{ico},0'
        return f"{self._executable},0"

    # ---- 状态查询 ---------------------------------------------------
    def custom_suffixes(self) -> list[str]:
        """用户在设置页新增的识别后缀（PLAN 8.2），只含内置表以外的条目。"""
        raw = self.settings.value(KEY_INTEGRATION_CUSTOM_SUFFIXES, []) or []
        if isinstance(raw, str):
            raw = [raw]
        return _clean_extensions(raw)

    def set_custom_suffixes(self, values) -> list[str]:
        """保存自定义后缀并立即刷新进程内的格式识别表。"""
        cleaned = _clean_extensions(values)
        self.settings.set_value(KEY_INTEGRATION_CUSTOM_SUFFIXES, cleaned)
        self.settings.sync()
        set_extra_archive_suffixes(cleaned)
        return cleaned

    def sync_suffixes(self) -> tuple[str, ...]:
        """把设置里的自定义后缀灌进识别表，返回生效的完整后缀表。"""
        set_extra_archive_suffixes(self.custom_suffixes())
        return archive_suffixes()

    def candidate_extensions(self) -> tuple[str, ...]:
        """可作为关联候选的扩展名：内置 + 用户自定义（PLAN 8.2）。"""
        return self.sync_suffixes()

    def registered_extensions(self) -> set[str]:
        found: set[str] = set()
        associations = file_associations_key(self.prefix)
        for ext in self.candidate_extensions():
            if registry.has_value(self.root, open_with_progids_key(ext, self.prefix), PROGID):
                found.add(ext)
            elif registry.read_value(self.root, associations, ext) == PROGID:
                found.add(ext)
        return found

    def status(self) -> ShellStatus:
        extensions = sorted(self.registered_extensions())
        menu_extensions = sorted(set(self.candidate_extensions()) | set(extensions))
        open_with = any(
            registry.key_exists(self.root, open_with_shell_key(ext, self.prefix))
            for ext in menu_extensions
        )
        extract_here = any(
            registry.key_exists(self.root, extract_here_shell_key(ext, self.prefix))
            for ext in menu_extensions
        )
        return ShellStatus(
            registered=(
                registry.key_exists(self.root, progid_key(self.prefix))
                or bool(extensions)
                or any(registry.key_exists(self.root, key) for key in add_to_session_keys(self.prefix))
                or open_with
                or extract_here
                or any(registry.key_exists(self.root, key) for key in compress_here_keys(self.prefix))
            ),
            extensions=tuple(extensions),
            add_to_session=any(registry.key_exists(self.root, key) for key in add_to_session_keys(self.prefix)),
            open_with=open_with,
            extract_here=extract_here,
            capabilities=registry.key_exists(self.root, capabilities_key(self.prefix)),
        )

    # ---- 注册 -------------------------------------------------------
    def register(
        self,
        extensions,
        *,
        add_to_session: bool = True,
        open_with: bool = True,
        extract_here: bool = True,
    ) -> list[str]:
        """写入全部注册项，返回实际写入的键路径（便于断言与诊断）。"""
        exts = _clean_extensions(extensions)
        written: list[str] = []

        base = progid_key(self.prefix)
        self._write(base, "", PROGID, written)
        self._write(base, "FriendlyAppName", APP_KEY_NAME, written)
        self._write(default_icon_key(self.prefix), "", self._icon(archive=True), written)
        self._write(progid_command_key(self.prefix), "", self.command('"%1"'), written)

        # 打开方式候选：值名是 ProgID，数据为空（REG_NONE）——只进候选，不改扩展名默认值
        for ext in exts:
            self._write(
                open_with_progids_key(ext, self.prefix), PROGID, b"", written, kind=registry.REG_NONE
            )

        if exts:
            # 默认应用页可见性：RegisteredApplications → Capabilities（缺此步系统设置页找不到本工具）
            capabilities = capabilities_key(self.prefix)
            self._write(capabilities, "ApplicationName", APP_KEY_NAME, written)
            self._write(capabilities, "ApplicationDescription", APP_DESCRIPTION, written)
            associations = file_associations_key(self.prefix)
            for ext in exts:
                self._write(associations, ext, PROGID, written)
            self._write(registered_applications_key(self.prefix), APP_KEY_NAME, capabilities, written)

        menu_extensions = _clean_extensions([*self.candidate_extensions(), *exts])
        self._apply_context_menus(
            menu_extensions,
            add_to_session=add_to_session,
            open_with=open_with,
            extract_here=extract_here,
            written=written,
        )

        self._persist(exts, add_to_session, open_with, extract_here)
        self.refresh_shell()
        return written

    def install_context_menus(
        self,
        *,
        add_to_session: bool = True,
        open_with: bool = True,
        extract_here: bool = True,
    ) -> list[str]:
        """Install context menus without changing file associations or defaults."""
        written: list[str] = []
        self._apply_context_menus(
            list(self.candidate_extensions()),
            add_to_session=add_to_session,
            open_with=open_with,
            extract_here=extract_here,
            written=written,
        )
        self.settings.set_value(KEY_INTEGRATION_ADD_TO_SESSION, bool(add_to_session))
        self.settings.set_value(KEY_INTEGRATION_OPEN_WITH, bool(open_with))
        self.settings.set_value(KEY_INTEGRATION_EXTRACT_HERE, bool(extract_here))
        self.settings.sync()
        self.refresh_shell()
        return written

    def initialize_install_defaults(self) -> bool:
        """首次安装写默认菜单；覆盖安装按原选择重写路径并刷新 Shell。"""
        if self.settings.value(KEY_INSTALL_DEFAULTS_INITIALIZED, False, type=bool):
            status = self.status()
            add_to_session = self.settings.value(
                KEY_INTEGRATION_ADD_TO_SESSION, status.add_to_session, type=bool
            )
            open_with = self.settings.value(
                KEY_INTEGRATION_OPEN_WITH, status.open_with, type=bool
            )
            extract_here = self.settings.value(
                KEY_INTEGRATION_EXTRACT_HERE, status.extract_here, type=bool
            )
            has_file_registration = (
                registry.key_exists(self.root, progid_key(self.prefix))
                or status.capabilities
                or bool(status.extensions)
            )
            if has_file_registration:
                self.register(
                    status.extensions,
                    add_to_session=add_to_session,
                    open_with=open_with,
                    extract_here=extract_here,
                )
            else:
                self.install_context_menus(
                    add_to_session=add_to_session,
                    open_with=open_with,
                    extract_here=extract_here,
                )
            return False

        preference_keys = (
            KEY_INTEGRATION_ADD_TO_SESSION,
            KEY_INTEGRATION_OPEN_WITH,
            KEY_INTEGRATION_EXTRACT_HERE,
        )
        has_existing_preferences = any(self.settings.contains(key) for key in preference_keys)
        if has_existing_preferences:
            add_to_session = self.settings.value(
                KEY_INTEGRATION_ADD_TO_SESSION, True, type=bool
            )
            open_with = self.settings.value(KEY_INTEGRATION_OPEN_WITH, True, type=bool)
            extract_here = self.settings.value(
                KEY_INTEGRATION_EXTRACT_HERE, True, type=bool
            )
        else:
            add_to_session = open_with = extract_here = True

        self.install_context_menus(
            add_to_session=add_to_session,
            open_with=open_with,
            extract_here=extract_here,
        )
        self.settings.set_value(KEY_INSTALL_DEFAULTS_INITIALIZED, True)
        self.settings.set_value(KEY_POST_INSTALL_PROMPT_PENDING, True)
        self.settings.sync()
        return True

    def _apply_context_menus(
        self,
        extensions: list[str],
        *,
        add_to_session: bool,
        open_with: bool,
        extract_here: bool,
        written: list[str],
    ) -> None:
        """同步右键菜单；文件型菜单覆盖受支持格式，与关联勾选集合解耦。"""
        for key in compress_here_keys(self.prefix):
            self._write(key, "MUIVerb", MENU_COMPRESS_HERE, written)
            self._write(key, "Icon", self._icon(), written)
            self._write(key, "MultiSelectModel", "Single", written)
            self._write(command_key(key), "", self.command("--compress-here", '"%1"'), written)
        for key in add_to_session_keys(self.prefix):
            if add_to_session:
                self._write(key, "MUIVerb", MENU_ADD_TO_SESSION, written)
                self._write(key, "Icon", self._icon(), written)
                self._write(command_key(key), "", self.command("--add-to-session", '"%1"'), written)
            elif registry.key_exists(self.root, key):
                registry.delete_tree(self.root, key)

        if open_with:
            for ext in extensions:
                key = open_with_shell_key(ext, self.prefix)
                self._write(key, "MUIVerb", MENU_OPEN_WITH, written)
                self._write(key, "Icon", self._icon(), written)
                self._write(command_key(key), "", self.command('"%1"'), written)
        else:
            for ext in extensions:
                key = open_with_shell_key(ext, self.prefix)
                if registry.key_exists(self.root, key):
                    registry.delete_tree(self.root, key)

        if extract_here:
            for ext in extensions:
                key = extract_here_shell_key(ext, self.prefix)
                self._write(key, "MUIVerb", MENU_EXTRACT_HERE, written)
                self._write(key, "Icon", self._icon(), written)
                self._write(command_key(key), "", self.command("--extract-here", '"%1"'), written)
        else:
            for ext in extensions:
                key = extract_here_shell_key(ext, self.prefix)
                if registry.key_exists(self.root, key):
                    registry.delete_tree(self.root, key)

    def apply(
        self,
        extensions,
        *,
        add_to_session: bool = True,
        open_with: bool = True,
        extract_here: bool = True,
    ) -> list[str]:
        """把「勾选后的格式集合」同步到注册表：新增写入，取消的逐个清除。"""
        desired = _clean_extensions(extensions)
        stale = self.registered_extensions() - set(desired)
        if stale:
            self.unregister_extensions(sorted(stale))
        return self.register(
            desired,
            add_to_session=add_to_session,
            open_with=open_with,
            extract_here=extract_here,
        )

    # ---- 撤销 -------------------------------------------------------
    def unregister_extensions(self, extensions) -> list[str]:
        """只撤销指定扩展名的关联项，保留其它格式与右键菜单。"""
        removed: list[str] = []
        associations = file_associations_key(self.prefix)
        for ext in _clean_extensions(extensions):
            key = open_with_progids_key(ext, self.prefix)
            if registry.delete_value(self.root, key, PROGID):
                removed.append(key)
            menu = open_with_shell_key(ext, self.prefix)
            if registry.key_exists(self.root, menu):
                registry.delete_tree(self.root, menu)
                removed.append(menu)
            extract_menu = extract_here_shell_key(ext, self.prefix)
            if registry.key_exists(self.root, extract_menu):
                registry.delete_tree(self.root, extract_menu)
                removed.append(extract_menu)
            if registry.read_value(self.root, associations, ext) == PROGID:
                registry.delete_value(self.root, associations, ext)
                removed.append(associations)
        return removed

    def unregister(self) -> list[str]:
        """清除全部 ZipMerge 注册项（PLAN 8.7，卸载与「一键撤销」共用）。"""
        removed: list[str] = []
        for ext in sorted(set(self.candidate_extensions()) | self.registered_extensions()):
            removed.extend(self.unregister_extensions([ext]))

        for key in (*add_to_session_keys(self.prefix), capabilities_key(self.prefix), *compress_here_keys(self.prefix)):
            if registry.key_exists(self.root, key):
                registry.delete_tree(self.root, key)
                removed.append(key)

        if registry.delete_value(self.root, registered_applications_key(self.prefix), APP_KEY_NAME):
            removed.append(registered_applications_key(self.prefix))

        base = progid_key(self.prefix)
        if registry.key_exists(self.root, base):
            registry.delete_tree(self.root, base)
            removed.append(base)

        self._clear_persisted()
        self.refresh_shell()
        return removed

    # ---- 内部 -------------------------------------------------------
    def _write(self, path: str, name: str, value, written: list[str], kind: int = registry.REG_SZ) -> None:
        if registry.write_value(self.root, path, name, value, kind):
            written.append(path)

    def _persist(
        self,
        extensions: list[str],
        add_to_session: bool,
        open_with: bool,
        extract_here: bool,
    ) -> None:
        self.settings.set_value(KEY_INTEGRATION_EXTENSIONS, extensions)
        self.settings.set_value(KEY_INTEGRATION_ADD_TO_SESSION, bool(add_to_session))
        self.settings.set_value(KEY_INTEGRATION_OPEN_WITH, bool(open_with))
        self.settings.set_value(KEY_INTEGRATION_EXTRACT_HERE, bool(extract_here))
        self.settings.sync()

    def _clear_persisted(self) -> None:
        self.settings.set_value(KEY_INTEGRATION_EXTENSIONS, [])
        self.settings.set_value(KEY_INTEGRATION_ADD_TO_SESSION, False)
        self.settings.set_value(KEY_INTEGRATION_OPEN_WITH, False)
        self.settings.set_value(KEY_INTEGRATION_EXTRACT_HERE, False)
        self.settings.sync()
