"""设计令牌：语义化颜色与度量，深浅两套值。

业务代码只引用语义令牌，不硬编码颜色 / 圆角 / 间距（见 PLAN 6.1）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

DARK = "dark"
LIGHT = "light"
MODES: tuple[str, ...] = (DARK, LIGHT)

# 读取系统强调色失败时的回退值（Windows 默认蓝）
DEFAULT_ACCENT = "#0078D4"


def _clamp(value: float) -> int:
    return max(0, min(255, int(round(value))))


def parse_hex(color: str) -> tuple[int, int, int]:
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"无法解析颜色：{color!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def to_hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(_clamp(value) for value in rgb))


def mix(color_a: str, color_b: str, ratio: float) -> str:
    """按 ratio 从 color_a 向 color_b 混合（0 → a，1 → b）。"""
    first = parse_hex(color_a)
    second = parse_hex(color_b)
    return to_hex(tuple(first[i] + (second[i] - first[i]) * ratio for i in range(3)))


def lighten(color: str, ratio: float) -> str:
    return mix(color, "#FFFFFF", ratio)


def darken(color: str, ratio: float) -> str:
    return mix(color, "#000000", ratio)


def rgba(color: str, alpha: float) -> str:
    r, g, b = parse_hex(color)
    return f"rgba({r}, {g}, {b}, {max(0.0, min(1.0, alpha)):.2f})"


def luminance(color: str) -> float:
    """返回 WCAG 定义的 sRGB 相对亮度。"""

    def linear(channel: int) -> float:
        value = channel / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in parse_hex(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def on_color(background: str) -> str:
    """返回叠加在给定背景上至少达到 WCAG AA 的黑 / 白文本色。"""
    background_luminance = luminance(background)
    white_contrast = 1.05 / (background_luminance + 0.05)
    return "#FFFFFF" if white_contrast >= 4.5 else "#000000"


def _contrast_ratio(color_a: str, color_b: str) -> float:
    lighter, darker = sorted((luminance(color_a), luminance(color_b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _visible_accent(accent: str, background: str, mode: str, minimum: float = 3.0) -> str:
    """让系统强调色在当前主题表面上保持可辨识，同时尽量保留原色。"""
    if _contrast_ratio(accent, background) >= minimum:
        return accent
    target = "#FFFFFF" if mode == DARK else "#000000"
    # 以 8-bit 色阶逐步靠近高对比端点；结果确定，且不会无谓改动已合格颜色。
    for step in range(1, 256):
        candidate = mix(accent, target, step / 255)
        if _contrast_ratio(candidate, background) >= minimum:
            return candidate
    return target


def normalize_accent(accent: str | None) -> str:
    if not accent:
        return DEFAULT_ACCENT
    try:
        parse_hex(accent)
    except ValueError:
        return DEFAULT_ACCENT
    return "#" + accent.strip().lstrip("#").upper()


@dataclass(frozen=True)
class Palette:
    """一套语义颜色。"""

    mode: str
    base: str
    chrome: str
    layer: str
    card: str
    canvas: str
    hover: str
    pressed: str
    border_subtle: str
    border_strong: str
    text_primary: str
    text_secondary: str
    text_disabled: str
    accent: str
    accent_hover: str
    accent_pressed: str
    on_accent: str
    danger: str
    danger_bg: str
    warning: str
    success: str
    source_badge: str
    selection: str
    selection_solid: str
    scrollbar: str
    tooltip: str


@dataclass(frozen=True)
class Metrics:
    """间距 / 圆角 / 字号 / 行高 / 动效时长。"""

    radius_control: int = 5
    radius_panel: int = 6
    space_xs: int = 4
    space_sm: int = 8
    space_md: int = 12
    space_lg: int = 16
    space_xl: int = 24
    font_small: int = 12
    font_body: int = 13
    font_subtitle: int = 15
    font_title: int = 20
    row_height: int = 32
    control_height: int = 32
    icon_sm: int = 14
    icon_md: int = 18
    icon_lg: int = 22
    duration_panel: int = 200
    duration_tab: int = 150
    duration_drop: int = 120
    duration_toast: int = 200


COMFORTABLE = Metrics()
COMPACT = replace(COMFORTABLE, row_height=28, control_height=28, icon_sm=12, icon_md=16, icon_lg=20)

DENSITIES: dict[str, Metrics] = {"comfortable": COMFORTABLE, "compact": COMPACT}


#: 界面字号缩放（百分数）的可用范围与默认值；设置页与主题服务共用同一套边界
FONT_SCALE_MIN = 80
FONT_SCALE_MAX = 150
FONT_SCALE_STEP = 5
DEFAULT_FONT_SCALE = 100


def clamp_font_scale(percent: object) -> int:
    """把任意输入收敛到合法缩放百分比，非法值回落到默认值。"""
    try:
        value = int(round(float(percent)))
    except (TypeError, ValueError):
        return DEFAULT_FONT_SCALE
    return max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, value))


def _scale_font(size: int, percent: int) -> int:
    return max(1, int(round(size * percent / 100)))


def metrics_for_density(density: str, font_scale: int = DEFAULT_FONT_SCALE) -> Metrics:
    """按密度取度量，再按 ``font_scale`` 缩放字号阶梯。"""
    base = DENSITIES.get(density, COMFORTABLE)
    scale = clamp_font_scale(font_scale)
    if scale == DEFAULT_FONT_SCALE:
        return base
    return replace(
        base,
        font_small=_scale_font(base.font_small, scale),
        font_body=_scale_font(base.font_body, scale),
        font_subtitle=_scale_font(base.font_subtitle, scale),
        font_title=_scale_font(base.font_title, scale),
    )


def palette_for(mode: str, accent: str | None = DEFAULT_ACCENT) -> Palette:
    """生成指定模式的语义颜色；强调色由系统或用户设置提供。"""
    raw_accent = normalize_accent(accent)
    selection_alpha = 0.14 if mode == LIGHT else 0.18
    layer = "#F4F6F9" if mode == LIGHT else "#202022"
    if mode == LIGHT:
        acc = _visible_accent(raw_accent, "#F8FAFC", LIGHT)
        return Palette(
            mode=LIGHT,
            base="#E5E9EE",
            chrome="#EDF0F4",
            layer=layer,
            card="#F8FAFC",
            canvas="#EEF2F6",
            hover="#EBEFF4",
            pressed="#E2E7ED",
            border_subtle="#AEB7C2",
            border_strong="#929EAB",
            text_primary="#1C232A",
            text_secondary="#495766",
            text_disabled="#7B8794",
            accent=acc,
            accent_hover=darken(acc, 0.08),
            accent_pressed=darken(acc, 0.18),
            on_accent=on_color(acc),
            danger="#C23D43",
            danger_bg=rgba("#C23D43", 0.10),
            warning="#B17719",
            success="#1F9952",
            source_badge="#3F719D",
            selection=rgba(acc, selection_alpha),
            selection_solid=mix(layer, acc, selection_alpha),
            scrollbar=rgba("#495766", 0.30),
            tooltip="#F8FAFC",
        )
    acc = _visible_accent(raw_accent, "#252628", DARK)
    return Palette(
        mode=DARK,
        base="#141416",
        chrome="#1B1B1D",
        layer=layer,
        card="#252628",
        canvas="#18181A",
        hover="#2A2B2E",
        pressed="#18181A",
        border_subtle="#34373C",
        border_strong="#44484E",
        text_primary="#ECEEF1",
        text_secondary="#A5AAB2",
        text_disabled="#6E747C",
        accent=acc,
        accent_hover=lighten(acc, 0.14),
        accent_pressed=darken(acc, 0.12),
        on_accent=on_color(acc),
        danger="#E04F4F",
        danger_bg=rgba("#E04F4F", 0.16),
        warning="#E0AE4A",
        success="#3CCE76",
        source_badge="#477FA8",
        selection=rgba(acc, selection_alpha),
        selection_solid=mix(layer, acc, selection_alpha),
        scrollbar="#4E5258",
        tooltip="#242527",
    )
