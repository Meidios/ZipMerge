"""设计系统：令牌、QSS、图标字形、字体、主题服务、DWM 效果与动效。"""

from __future__ import annotations

from .dwm import (
    DWMSBT_MAINWINDOW,
    DWMSBT_NONE,
    apply_window_effects,
    set_backdrop,
    set_dark_titlebar,
    set_rounded_corners,
)
from .fonts import mono_family, mono_font, register_fonts, ui_family, ui_font
from .icons import glyph, glyph_font, glyph_png, icon, icons, qcolor, qss_glyph_urls
from .motion import animate, animate_height, animations_enabled, fade_in, set_animations_enabled
from .qss import build_qss, token_map
from .theme import MODE_SYSTEM, THEME_MODES, ThemeService, read_system_accent, read_system_mode
from .tokens import (
    COMFORTABLE,
    COMPACT,
    DARK,
    DEFAULT_ACCENT,
    DEFAULT_FONT_SCALE,
    DENSITIES,
    FONT_SCALE_MAX,
    FONT_SCALE_MIN,
    FONT_SCALE_STEP,
    LIGHT,
    MODES,
    Metrics,
    Palette,
    clamp_font_scale,
    metrics_for_density,
    palette_for,
)

__all__ = [
    "COMFORTABLE",
    "COMPACT",
    "DARK",
    "DEFAULT_ACCENT",
    "DEFAULT_FONT_SCALE",
    "DENSITIES",
    "DWMSBT_MAINWINDOW",
    "DWMSBT_NONE",
    "FONT_SCALE_MAX",
    "FONT_SCALE_MIN",
    "FONT_SCALE_STEP",
    "LIGHT",
    "MODE_SYSTEM",
    "MODES",
    "Metrics",
    "Palette",
    "THEME_MODES",
    "ThemeService",
    "clamp_font_scale",
    "animate",
    "animate_height",
    "animations_enabled",
    "apply_window_effects",
    "build_qss",
    "fade_in",
    "glyph",
    "glyph_font",
    "glyph_png",
    "icon",
    "icons",
    "metrics_for_density",
    "mono_family",
    "mono_font",
    "palette_for",
    "qcolor",
    "qss_glyph_urls",
    "read_system_accent",
    "read_system_mode",
    "register_fonts",
    "set_animations_enabled",
    "set_backdrop",
    "set_dark_titlebar",
    "set_rounded_corners",
    "token_map",
    "ui_family",
    "ui_font",
]
