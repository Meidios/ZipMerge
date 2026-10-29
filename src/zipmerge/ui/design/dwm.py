"""通过 DWM 给原生窗口加圆角、Mica 背景与暗色标题栏。

全部走 ctypes 调用 ``dwmapi``，失败或系统不支持时静默返回 ``False``，
由调用方降级为纯色背景（见 PLAN 6.5、14）。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Mapping

# DWMWINDOWATTRIBUTE
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

# DWM_WINDOW_CORNER_PREFERENCE
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3

# DWM_SYSTEMBACKDROP_TYPE
DWMSBT_AUTO = 0
DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2  # Mica
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic
DWMSBT_TABBEDWINDOW = 4  # Mica Alt


class _Margins(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _dwmapi():
    if sys.platform != "win32":
        return None
    try:
        api = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        return None
    api.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    api.DwmSetWindowAttribute.restype = ctypes.c_long
    api.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(_Margins)]
    api.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    return api


def _set_attribute(hwnd: int, attribute: int, value: int) -> bool:
    api = _dwmapi()
    if api is None or not hwnd:
        return False
    data = ctypes.c_int(value)
    try:
        result = api.DwmSetWindowAttribute(wintypes.HWND(hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data))
    except OSError:
        return False
    return result == 0


def set_dark_titlebar(hwnd: int, dark: bool) -> bool:
    return _set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)


def set_rounded_corners(hwnd: int, enabled: bool = True) -> bool:
    preference = DWMWCP_ROUND if enabled else DWMWCP_DONOTROUND
    return _set_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, preference)


def set_backdrop(hwnd: int, backdrop: int = DWMSBT_MAINWINDOW) -> bool:
    return _set_attribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, backdrop)


def extend_frame(hwnd: int) -> bool:
    """把边框延伸进客户区，Mica 才会透出到整个窗口。"""
    api = _dwmapi()
    if api is None or not hwnd:
        return False
    margins = _Margins(-1, -1, -1, -1)
    try:
        return api.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins)) == 0
    except OSError:
        return False


def apply_window_effects(
    hwnd: int,
    *,
    dark: bool,
    backdrop: int | None = DWMSBT_MAINWINDOW,
    rounded: bool = True,
) -> Mapping[str, bool]:
    """一次性应用全部 DWM 效果，返回每项是否成功（失败即可静默降级）。"""
    result = {
        "dark_titlebar": set_dark_titlebar(hwnd, dark),
        "rounded": set_rounded_corners(hwnd, rounded),
        "backdrop": False,
    }
    if backdrop is not None:
        extend_frame(hwnd)
        result["backdrop"] = set_backdrop(hwnd, backdrop)
    return result
