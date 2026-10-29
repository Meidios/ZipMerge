"""字体注册：内置 Noto Sans SC 保证跨机字形一致，等宽回退 Cascadia Mono → Consolas。"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QFont, QFontDatabase

from ...util.paths import asset_path

# 内置字体缺失时的系统回退，仅用于极端情况。
UI_FAMILY_FALLBACK = "Microsoft YaHei UI"

MONO_CANDIDATES: tuple[str, ...] = (
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "Courier New",
)

_UI_SOURCES: tuple[tuple[str, str], ...] = (
    ("ui", "NotoSansSC-Regular.otf"),
    ("ui-medium", "NotoSansSC-Medium.otf"),
)

_registered: dict[str, str] = {}


def register_fonts() -> dict[str, str]:
    """把内置字体注册进 QFontDatabase，返回逻辑别名到实际 family 的映射。"""
    if _registered:
        return dict(_registered)
    for alias, filename in _UI_SOURCES:
        path = asset_path("fonts", filename)
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            _registered.setdefault(alias, families[0])
    return dict(_registered)


def ui_family() -> str:
    """界面正文字体族。"""
    register_fonts()
    return _registered.get("ui") or _registered.get("ui-medium") or UI_FAMILY_FALLBACK


@lru_cache(maxsize=1)
def mono_family() -> str:
    """文本 / 代码预览的等宽字体族。"""
    available = set(QFontDatabase.families())
    for name in MONO_CANDIDATES:
        if name in available:
            return name
    return MONO_CANDIDATES[-1]


def ui_font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(ui_family(), size)
    font.setWeight(weight)
    return font


def mono_font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(mono_family(), size)
    font.setWeight(weight)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font
