"""Fluent UI System Icons 字形加载、QIcon 渲染与 QSS 用 PNG 缓存。

图标走字体字形渲染：同一套码点可以随主题改色，无需准备多套位图。
QSS 不支持 data URI 与字体字形，因此需要图片的子控件（勾选框、下拉箭头等）
会在临时目录生成 PNG 并由 :func:`qss_glyph_urls` 返回路径。
"""

from __future__ import annotations

import json
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap

from ...util.paths import asset_path

ICON_JSON = "FluentSystemIcons-Regular.json"
ICON_TTF = "FluentSystemIcons-Regular.ttf"

#: 设计令牌里的颜色可能带 alpha（``rgba(r, g, b, a)``），而 ``QColor`` 不认 CSS 的
#: ``rgba()`` 写法，直接构造会得到无效颜色。这里统一转换。
_CSS_RGBA = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)",
    re.IGNORECASE,
)


def qcolor(spec: str | QColor) -> QColor:
    """把设计令牌颜色（``#RRGGBB`` 或 ``rgba(...)``）转成 ``QColor``。"""
    if isinstance(spec, QColor):
        return QColor(spec)
    match = _CSS_RGBA.fullmatch(spec.strip())
    if match:
        red, green, blue = (int(match.group(index)) for index in (1, 2, 3))
        color = QColor(red, green, blue)
        alpha = match.group(4)
        if alpha is not None:
            color.setAlphaF(max(0.0, min(1.0, float(alpha))))
        return color
    return QColor(spec)

# 字形按 size 分档，取不到时依次回退。
SIZE_FALLBACKS: tuple[int, ...] = (24, 20, 16, 28, 32, 48)
STYLES: tuple[str, ...] = ("regular", "filled")

# PLAN 6.2 用的语义短名到 Fluent 实际名的映射（Fluent 的原始名带序号或 arrow_ 前缀）。
ALIASES: Mapping[str, str] = {
    "music_note": "music_note_2",
    "export": "arrow_export",
    "import": "arrow_import",
    "sync": "arrow_sync",
    "undo": "arrow_undo",
    "redo": "arrow_redo",
    "open": "folder_open",
    "archive": "box_multiple",
}


class IconSet:
    """Fluent 图标字体：码点表 + 注册后的字体族。"""

    def __init__(self) -> None:
        self._codes: dict[str, int] = {}
        self._family: str | None = None

    def load(self) -> bool:
        if self._codes:
            return True
        json_path = asset_path("fonts", ICON_JSON)
        ttf_path = asset_path("fonts", ICON_TTF)
        if not json_path.exists() or not ttf_path.exists():
            return False
        self._codes = json.loads(json_path.read_text(encoding="utf-8"))
        font_id = QFontDatabase.addApplicationFont(str(ttf_path))
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                self._family = families[0]
        return self._family is not None and bool(self._codes)

    @property
    def family(self) -> str:
        self.load()
        return self._family or "FluentSystemIcons-Regular"

    @property
    def loaded(self) -> bool:
        return self.load()

    def code(self, name: str, size: int | None = None) -> int | None:
        """解析图标码点。接受短名（``folder_zip``）或完整名（``ic_fluent_*``）。"""
        if not self.load():
            return None
        candidates: list[str] = []
        if name.startswith("ic_fluent_"):
            candidates.append(name)
            short = re.sub(r"_\d+_(regular|filled)$", "", name[len("ic_fluent_"):])
        else:
            short = name
        short = ALIASES.get(short, short)
        sizes: Iterable[int] = (size,) + SIZE_FALLBACKS if size else SIZE_FALLBACKS
        for style in STYLES:
            for candidate_size in sizes:
                candidates.append(f"ic_fluent_{short}_{candidate_size}_{style}")
        for candidate in candidates:
            value = self._codes.get(candidate)
            if value is not None:
                return int(value)
        return None

    def char(self, name: str, size: int | None = None) -> str | None:
        value = self.code(name, size)
        return chr(value) if value else None

    def font(self, size: int) -> QFont:
        font = QFont(self.family, size)
        font.setPixelSize(size)
        return font

    def pixmap(self, name: str, size: int, color: str | QColor) -> QPixmap | None:
        glyph = self.char(name, size)
        if glyph is None:
            return None
        ratio = 1.0
        app = QGuiApplication.instance()
        if app is not None and app.primaryScreen() is not None:
            ratio = float(app.primaryScreen().devicePixelRatio())
        physical = max(1, int(round(size * ratio)))
        pixmap = QPixmap(physical, physical)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(ratio)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setFont(self.font(physical))
            painter.setPen(qcolor(color))
            painter.drawText(QRect(0, 0, physical, physical), Qt.AlignmentFlag.AlignCenter, glyph)
        finally:
            painter.end()
        return pixmap

    def icon(self, name: str, size: int, color: str | QColor) -> QIcon:
        pixmap = self.pixmap(name, size, color)
        return QIcon(pixmap) if pixmap is not None else QIcon()

    def glyph_font(self, size: int) -> QFont:
        """供 QLabel 显示字形并由 QSS ``color`` 染色。"""
        return self.font(size)


_icons = IconSet()


def icons() -> IconSet:
    return _icons


def icon(name: str, size: int = 20, color: str = "#FFFFFF") -> QIcon:
    return _icons.icon(name, size, color)


def glyph(name: str, size: int = 16) -> str:
    """返回图标字形文本；配合 :func:`glyph_font` 使用。"""
    return _icons.char(name, size) or ""


def glyph_font(size: int = 16) -> QFont:
    return _icons.glyph_font(size)


def glyph_cache_root() -> Path:
    return Path(tempfile.gettempdir()) / "ZipMerge" / "ui" / "glyphs"


def glyph_png(name: str, size: int, color: str, root: Path | None = None) -> str:
    """把字形渲染成 PNG 并返回路径（供 QSS ``image: url(...)`` 使用）。"""
    safe_color = color.lstrip("#").lower()
    directory = Path(root) if root else glyph_cache_root()
    target = directory / f"{name.replace('_', '-')}-{size}-{safe_color}.png"
    if target.exists():
        return target.as_posix()
    pixmap = _icons.pixmap(name, size, color)
    if pixmap is None:
        return ""
    directory.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(target), "PNG")
    return target.as_posix()


def qss_url(path: str) -> str:
    return f'url("{path}")' if path else "none"


def qss_glyph_urls(
    *,
    text_color: str,
    secondary_color: str,
    on_accent: str,
    accent: str,
    root: Path | None = None,
) -> Mapping[str, str]:
    """生成 QSS 模板需要的字形图片 URL；缺失时返回 ``none``。"""
    specs = {
        "check": ("checkmark", 14, on_accent),
        "chevron_down": ("chevron_down", 16, secondary_color),
        "chevron_up": ("chevron_up", 16, secondary_color),
        "chevron_right": ("chevron_right", 16, secondary_color),
        "branch_right": ("chevron_right", 16, secondary_color),
        "branch_down": ("chevron_down", 16, secondary_color),
        "radio": ("circle", 10, accent),
        "close": ("dismiss", 12, text_color),
    }
    urls: dict[str, str] = {}
    for token, (name, size, color) in specs.items():
        urls[token] = qss_url(glyph_png(name, size, color, root))
    return urls
