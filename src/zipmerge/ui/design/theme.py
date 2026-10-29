"""主题服务：系统明暗与强调色读取、注册表实时监听、手动覆盖。

初始值读 ``HKCU``；实时切换用 ``RegNotifyChangeKeyValue`` 后台线程监听，
不依赖 darkdetect（它只读一次）。所有注册表访问只读，不写入。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, Qt, Signal

from . import qss as qss_module
from .tokens import (
    DARK,
    DEFAULT_FONT_SCALE,
    DEFAULT_ACCENT,
    LIGHT,
    Palette,
    clamp_font_scale,
    metrics_for_density,
    normalize_accent,
    palette_for,
    Metrics,
)

MODE_SYSTEM = "system"
THEME_MODES: tuple[str, ...] = (MODE_SYSTEM, DARK, LIGHT)

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
DWM_KEY = r"Software\Microsoft\Windows\DWM"

_KEY_NOTIFY = 0x0010
_REG_NOTIFY_CHANGE_LAST_SET = 0x00000004
_HKEY_CURRENT_USER = 0x80000001


def read_system_mode() -> str:
    """读系统应用主题；无法读取时按浅色处理。"""
    if sys.platform != "win32":
        return LIGHT
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except (OSError, ImportError):
        return LIGHT
    return LIGHT if int(value) else DARK


def read_system_accent() -> str:
    """读系统强调色。注册表 ``AccentColor`` 是 ABGR（0xAABBGGRR）。"""
    if sys.platform != "win32":
        return DEFAULT_ACCENT
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DWM_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "AccentColor")
    except (OSError, ImportError):
        return DEFAULT_ACCENT
    value = int(value) & 0xFFFFFFFF
    red = value & 0xFF
    green = (value >> 8) & 0xFF
    blue = (value >> 16) & 0xFF
    if red == green == blue == 0:
        return DEFAULT_ACCENT
    return f"#{red:02X}{green:02X}{blue:02X}"


class _RegistryWatcher(threading.Thread):
    """用 ``RegNotifyChangeKeyValue`` 监听单个键；无需轮询注册表本身。"""

    def __init__(self, key_path: str, callback) -> None:
        super().__init__(daemon=True, name=f"zipmerge-regwatch:{key_path}")
        self._key_path = key_path
        self._callback = callback
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if sys.platform != "win32":
            return
        try:
            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except OSError:
            return
        advapi32.RegOpenKeyExW.argtypes = [
            wintypes.HKEY,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HKEY),
        ]
        advapi32.RegOpenKeyExW.restype = ctypes.c_long
        advapi32.RegNotifyChangeKeyValue.argtypes = [
            wintypes.HKEY,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.HANDLE,
            wintypes.BOOL,
        ]
        advapi32.RegNotifyChangeKeyValue.restype = ctypes.c_long
        advapi32.RegCloseKey.argtypes = [wintypes.HKEY]
        kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        root = ctypes.c_void_p(_HKEY_CURRENT_USER)
        while not self._stop.is_set():
            hkey = wintypes.HKEY()
            result = advapi32.RegOpenKeyExW(root, self._key_path, 0, _KEY_NOTIFY, ctypes.byref(hkey))
            if result != 0:
                time.sleep(1.0)
                continue
            event = kernel32.CreateEventW(None, False, False, None)
            try:
                advapi32.RegNotifyChangeKeyValue(hkey, True, _REG_NOTIFY_CHANGE_LAST_SET, event, True)
                while not self._stop.is_set():
                    if kernel32.WaitForSingleObject(event, 300) == 0:
                        self._callback()
                        break
            finally:
                kernel32.CloseHandle(event)
                advapi32.RegCloseKey(hkey)


class ThemeService(QObject):
    """主题状态源：持有当前模式 / 强调色 / 密度，变化时发出信号。"""

    changed = Signal(object)
    metrics_changed = Signal(object)
    mode_changed = Signal(str)
    accent_changed = Signal(str)
    # 注册表监听线程用它把变化切回 Qt 主线程（跨线程自动排队投递）。
    _registry_signal = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mode_pref = MODE_SYSTEM
        self._accent_pref: str | None = None
        self._density = "comfortable"
        self._font_scale = DEFAULT_FONT_SCALE
        self._watchers: list[_RegistryWatcher] = []
        self._palette = self._compute_palette()
        self._metrics = metrics_for_density(self._density, self._font_scale)
        self._registry_signal.connect(self.refresh, Qt.ConnectionType.QueuedConnection)

    # -- 状态读取 -------------------------------------------------------
    @property
    def mode_preference(self) -> str:
        return self._mode_pref

    @property
    def effective_mode(self) -> str:
        return read_system_mode() if self._mode_pref == MODE_SYSTEM else self._mode_pref

    @property
    def accent(self) -> str:
        return self._accent_pref or read_system_accent()

    @property
    def accent_is_system(self) -> bool:
        return self._accent_pref is None

    @property
    def density(self) -> str:
        return self._density

    @property
    def font_scale(self) -> int:
        """界面字号缩放百分比（PLAN 7.8「常规」组）。"""
        return self._font_scale

    def palette(self) -> Palette:
        return self._palette

    def metrics(self) -> Metrics:
        return self._metrics

    # -- 状态修改 -------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode not in THEME_MODES or mode == self._mode_pref:
            return
        self._mode_pref = mode
        self._apply()
        self.mode_changed.emit(mode)

    def set_accent(self, accent: str | None) -> None:
        normalized = normalize_accent(accent) if accent else None
        if normalized == self._accent_pref:
            return
        self._accent_pref = normalized
        self._apply()
        self.accent_changed.emit(self.accent)

    def set_density(self, density: str) -> None:
        if density == self._density:
            return
        self._density = density
        self._metrics = metrics_for_density(self._density, self._font_scale)
        self.metrics_changed.emit(self._metrics)

    def set_font_scale(self, percent: object) -> None:
        """调整界面字号缩放；越界值由令牌层收敛。"""
        scale = clamp_font_scale(percent)
        if scale == self._font_scale:
            return
        self._font_scale = scale
        self._metrics = metrics_for_density(self._density, self._font_scale)
        self.metrics_changed.emit(self._metrics)

    def refresh(self) -> None:
        """重新读取系统状态；有实际变化时才发信号。"""
        self._apply()

    # -- 样式 -----------------------------------------------------------
    def stylesheet(self) -> str:
        from .fonts import mono_family, ui_family
        from .icons import icons, qss_glyph_urls

        glyph_urls = None
        try:
            if icons().loaded:
                glyph_urls = qss_glyph_urls(
                    text_color=self._palette.text_primary,
                    secondary_color=self._palette.text_secondary,
                    on_accent=self._palette.on_accent,
                    accent=self._palette.accent,
                )
        except Exception:  # 无 GUI 平台插件等极端情况下降级为纯 CSS
            glyph_urls = None
        return qss_module.build_qss(
            self._palette,
            self._metrics,
            ui_family=ui_family(),
            mono_family=mono_family(),
            icon_family=icons().family,
            glyph_urls=glyph_urls,
        )

    def apply_to(self, app) -> None:
        """把当前主题写入 QApplication。"""
        app.setStyleSheet(self.stylesheet())

    # -- 监听 -----------------------------------------------------------
    def start_watching(self) -> None:
        if self._watchers or sys.platform != "win32":
            return
        for key_path in (PERSONALIZE_KEY, DWM_KEY):
            watcher = _RegistryWatcher(key_path, self._registry_signal.emit)
            watcher.start()
            self._watchers.append(watcher)

    def stop_watching(self) -> None:
        for watcher in self._watchers:
            watcher.stop()
        self._watchers.clear()

    # -- 内部 -----------------------------------------------------------
    def _compute_palette(self) -> Palette:
        return palette_for(self.effective_mode, self.accent)

    def _apply(self) -> None:
        palette = self._compute_palette()
        if palette != self._palette:
            self._palette = palette
            self.changed.emit(palette)
