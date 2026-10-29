"""由设计令牌生成全局 QSS。

颜色 / 圆角 / 间距 / 字号全部来自 :mod:`tokens`，本模块不硬编码任何具体值；
业务代码只给控件打 ``variant`` / ``role`` 动态属性，样式集中在这里维护。
"""

from __future__ import annotations

from string import Template
from typing import Mapping

from .tokens import Metrics, Palette

# QSS 里需要字形图片的子控件（QSS 不支持 data URI，也不认字体字形，只能用 PNG 路径）。
GLYPH_TOKENS: tuple[str, ...] = (
    "check",
    "chevron_down",
    "chevron_up",
    "chevron_right",
    "branch_right",
    "branch_down",
    "radio",
    "close",
)


def token_map(
    palette: Palette,
    metrics: Metrics,
    *,
    ui_family: str = "Segoe UI",
    mono_family: str = "Consolas",
    icon_family: str = "FluentSystemIcons-Regular",
    glyph_urls: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """把令牌对象摊平成 QSS 模板变量。"""
    tokens: dict[str, str] = {
        "ui_family": ui_family,
        "mono_family": mono_family,
        "icon_family": icon_family,
        # 颜色
        "base": palette.base,
        "chrome": palette.chrome,
        "layer": palette.layer,
        "card": palette.card,
        "canvas": palette.canvas,
        "hover": palette.hover,
        "pressed": palette.pressed,
        "border_subtle": palette.border_subtle,
        "border_strong": palette.border_strong,
        "text_primary": palette.text_primary,
        "text_secondary": palette.text_secondary,
        "text_disabled": palette.text_disabled,
        "accent": palette.accent,
        "accent_hover": palette.accent_hover,
        "accent_pressed": palette.accent_pressed,
        "on_accent": palette.on_accent,
        "danger": palette.danger,
        "danger_bg": palette.danger_bg,
        "warning": palette.warning,
        "success": palette.success,
        "source_badge": palette.source_badge,
        "selection": palette.selection,
        "selection_solid": palette.selection_solid,
        "scrollbar": palette.scrollbar,
        "tooltip": palette.tooltip,
        # 度量
        "radius_control": f"{metrics.radius_control}px",
        "radius_panel": f"{metrics.radius_panel}px",
        "space_xs": f"{metrics.space_xs}px",
        "space_sm": f"{metrics.space_sm}px",
        "space_md": f"{metrics.space_md}px",
        "space_lg": f"{metrics.space_lg}px",
        "space_xl": f"{metrics.space_xl}px",
        "font_small": f"{metrics.font_small}px",
        "font_body": f"{metrics.font_body}px",
        "font_subtitle": f"{metrics.font_subtitle}px",
        "font_title": f"{metrics.font_title}px",
        "row_height": f"{metrics.row_height}px",
        "control_height": f"{metrics.control_height}px",
        "icon_sm": f"{metrics.icon_sm}px",
        "icon_md": f"{metrics.icon_md}px",
        "icon_lg": f"{metrics.icon_lg}px",
    }
    provided = dict(glyph_urls or {})
    for name in GLYPH_TOKENS:
        value = provided.get(name)
        if not value:
            value = provided.get(f"glyph_{name}")
        tokens[f"glyph_{name}"] = value or "none"
    return tokens


QSS_TEMPLATE = Template(
    """
* { outline: none; }

QWidget {
    font-family: "$ui_family";
    font-size: $font_body;
    color: $text_primary;
}

QMainWindow, QDialog, QWidget[role="workspace"] { background-color: $base; }
QWidget[role="panel"] {
    background-color: $layer;
    border: 1px solid $border_subtle;
    border-radius: $radius_panel;
}
/* 普通预览与空态直接使用面板表面，避免形成大面积近黑内框。 */
[role="preview"] {
    background-color: transparent;
    border: none;
    border-radius: $radius_control;
}
[role="empty"] {
    background-color: transparent;
    border: none;
    border-radius: $radius_control;
    color: $text_secondary;
}
[role="canvas"] {
    background-color: $canvas;
    border: 1px solid $border_subtle;
    border-radius: $radius_control;
}
QWidget[role="card"], QFrame[role="card"] {
    background-color: $card;
    border: 1px solid $border_subtle;
    border-radius: $radius_panel;
}
QWidget#temporaryArchiveCard {
    background-color: $card;
    border: 1px solid $border_subtle;
    border-radius: $radius_control;
}
QWidget#temporaryArchiveCard[state="ready"] { border-color: $accent; }
QWidget#temporaryArchiveCard[state="stale"] { border-color: $warning; }
QWidget#temporaryArchiveCard[state="missing"] { border-color: $danger; }
QFrame[role="separator"] { background-color: $border_subtle; border: none; }

/* 文本 */
QLabel { background: transparent; }
QLabel[variant="secondary"] { color: $text_secondary; }
QLabel[variant="disabled"] { color: $text_disabled; }
QLabel[variant="subtitle"] { color: $accent; font-size: $font_subtitle; font-weight: 600; }
QLabel[variant="title"] { font-size: $font_title; font-weight: 600; }
QLabel[variant="danger"] { color: $danger; }
QLabel[variant="warning"] { color: $warning; }
QLabel[variant="success"] { color: $success; }
QLabel[variant="glyph"] { font-family: "$icon_family"; color: $text_secondary; }
QLabel[variant="glyph"][on="accent"] { color: $accent; }
QLabel[role="badge"] {
    background-color: $source_badge;
    color: #FFFFFF;
    border-radius: 4px;
    padding: 1px 6px;
    font-size: $font_small;
}
QLabel[role="mono"] { font-family: "$mono_family"; }

/* 按钮 */
QPushButton {
    background-color: $card;
    color: $text_primary;
    border: 1px solid $border_strong;
    border-radius: $radius_control;
    min-height: $control_height;
    padding: 0 14px;
}
QPushButton:hover { background-color: $hover; border-color: $accent; }
QPushButton:pressed { background-color: $pressed; border-color: $accent; }
QPushButton:disabled { color: $text_disabled; border-color: $border_subtle; background-color: $base; }
QPushButton[variant="primary"] { background-color: $accent; color: $on_accent; border-color: transparent; }
QPushButton[variant="primary"]:hover { background-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background-color: $accent_pressed; }
QPushButton[variant="primary"]:disabled { background-color: $border_subtle; color: $text_disabled; }
QPushButton[variant="danger"] { background-color: $danger; color: #FFFFFF; border-color: transparent; }
QPushButton[variant="ghost"] { background-color: transparent; border-color: transparent; }
QPushButton[variant="ghost"]:hover { background-color: $hover; }
QPushButton[variant="ghost"]:pressed { background-color: $pressed; }

QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: $radius_control;
    padding: 4px;
}
QToolButton:hover { background-color: $hover; border-color: $accent; }
QToolButton:pressed { background-color: $pressed; border-color: $accent; }
QToolButton:checked { background-color: $selection; border-color: $accent; }
QToolButton[role="glyph"] { font-family: "$icon_family"; }

/* 输入 */
QLineEdit, QPlainTextEdit, QTextEdit {
    background-color: $card;
    border: 1px solid $border_strong;
    border-radius: $radius_control;
    padding: 4px 8px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit { min-height: $control_height; padding: 0 8px; }
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover { border-color: $accent; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: $accent; }
QLineEdit:disabled, QPlainTextEdit:disabled { color: $text_disabled; background-color: $base; }
QPlainTextEdit[role="mono"], QTextEdit[role="mono"] { font-family: "$mono_family"; }

QComboBox {
    background-color: $card;
    border: 1px solid $border_strong;
    border-radius: $radius_control;
    min-height: $control_height;
    padding: 0 8px;
}
QComboBox:hover { background-color: $hover; border-color: $accent; }
QComboBox:focus { border-color: $accent; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; background: transparent; }
QComboBox::down-arrow { image: $glyph_chevron_down; width: 16px; height: 16px; }
QToolButton#templateMenuButton::menu-indicator { image: none; width: 0px; height: 0px; }
QComboBox QAbstractItemView {
    background-color: $card;
    border: 1px solid $border_strong;
    border-radius: $radius_control;
    padding: 4px;
    selection-background-color: $selection;
    selection-color: $text_primary;
    outline: none;
}

QSpinBox, QDoubleSpinBox {
    background-color: $card;
    border: 1px solid $border_strong;
    border-radius: $radius_control;
    min-height: $control_height;
    padding: 0 6px;
}
QSpinBox:hover, QDoubleSpinBox:hover { border-color: $accent; }
QSpinBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 20px; border: none; background: transparent; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 20px; border: none; background: transparent; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: $glyph_chevron_up; width: 12px; height: 12px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: $glyph_chevron_down; width: 12px; height: 12px; }

QCheckBox, QRadioButton { spacing: $space_sm; background: transparent; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $border_strong;
    background-color: $card;
}
QCheckBox::indicator { border-radius: 4px; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background-color: $accent; border-color: $accent; image: $glyph_check; }
QCheckBox::indicator:disabled { background-color: $base; border-color: $border_subtle; }
QRadioButton::indicator { border-radius: 8px; }
QRadioButton::indicator:hover { border-color: $accent; }
QRadioButton::indicator:checked { border-color: $accent; background-color: $card; image: $glyph_radio; }

/* 列表与树 */
QAbstractItemView {
    /* 列表 / 树直接落在面板表面；层级由面板填充而不是永久亮描边建立。 */
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: $radius_control;
    outline: none;
    selection-background-color: $selection;
    selection-color: $text_primary;
    /* 不使用斑马纹；即使个别视图误开交替行，也保持与普通行同色 */
    alternate-background-color: $layer;
}
QAbstractItemView:focus { border-color: $accent; }
QTreeView::item, QListView::item, QTableView::item { padding: 3px 4px; border-radius: 4px; }
/* 行分隔线：长列表里靠它分辨行，不依赖 hover */
QTreeView::item { border-bottom: 1px solid $border_subtle; }
QTreeView::item:hover, QListView::item:hover { background-color: $hover; }
QTreeView::item:selected, QListView::item:selected {
    background-color: $selection;
    color: $text_primary;
}
/* Keep the accent marker on flat lists, away from target ancestor cells. */
QWidget#sourcePanel QTreeView::item:selected, QListView::item:selected {
    border-left: 2px solid $accent;
}
QTreeView::branch { background: transparent; }
/* An opaque fill prevents native selection markers in ancestor branch cells. */
QTreeView::branch:selected { background-color: $selection_solid; border: none; }
QTreeView::branch:closed:has-children { image: $glyph_branch_right; }
QTreeView::branch:open:has-children { image: $glyph_branch_down; }

QHeaderView { background-color: $layer; border: none; }
QHeaderView::section {
    background-color: $layer;
    color: $text_secondary;
    padding: 4px $space_sm;
    border: none;
    border-bottom: 1px solid $border_subtle;
    border-right: 1px solid $border_subtle;
}

/* 标签页：分隔线画在标签栏底部，避免横线贴着工具栏造成「压住标签」的观感 */
QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: transparent; border-bottom: 1px solid $border_subtle; }
QTabBar::tab {
    background: transparent;
    color: $text_secondary;
    padding: 7px 14px;
    margin-top: 6px;
    margin-right: 2px;
    border: none;
    border-top-left-radius: $radius_control;
    border-top-right-radius: $radius_control;
}
QTabBar::tab:hover { background-color: $hover; color: $text_primary; }
QTabBar::tab:selected {
    background-color: $selection;
    color: $text_primary;
    border-bottom: 2px solid $accent;
}
QTabBar::close-button { image: $glyph_close; subcontrol-position: right; }
QTabBar::close-button:hover { background-color: $hover; border-radius: 3px; }

/* 滚动条 */
QScrollBar:vertical { background: transparent; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background-color: $scrollbar; border-radius: 3px; min-height: 32px; margin: 2px; }
QScrollBar::handle:vertical:hover { background-color: $border_strong; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background-color: $scrollbar; border-radius: 3px; min-width: 32px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background-color: $border_strong; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QScrollArea { background: transparent; border: none; }

/* 容器 */
QSplitter::handle { background-color: transparent; }
QSplitter::handle:horizontal { width: $space_sm; }
QSplitter::handle:vertical { height: $space_sm; }
QSplitter::handle:hover { background-color: $selection; }

QGroupBox {
    background-color: $layer;
    border: 1px solid $border_subtle;
    border-radius: $radius_panel;
    margin-top: $space_md;
    padding: $space_md;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: $space_md;
    padding: 0 $space_xs;
    color: $text_secondary;
}

QStatusBar { background-color: $chrome; color: $text_secondary; border-top: 1px solid $border_subtle; }
QStatusBar::item { border: none; }

QToolBar {
    background-color: $chrome;
    border: none;
    border-bottom: 1px solid $border_subtle;
    spacing: $space_xs;
    padding: $space_xs;
}
QToolBar::separator { width: 1px; background-color: $border_subtle; margin: $space_xs; }

QProgressBar {
    background-color: $canvas;
    border: none;
    border-radius: 3px;
    color: $text_secondary;
    text-align: center;
}
QProgressBar::chunk { background-color: $accent; border-radius: 3px; }

QSlider::groove:horizontal { height: 4px; background: $canvas; border: 1px solid $border_subtle; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { background: $accent; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::handle:horizontal:hover { background: $accent_hover; }

/* 菜单与提示 */
QMenu {
    background-color: $card;
    border: 1px solid $border_strong;
    border-radius: $radius_panel;
    padding: $space_xs;
}
QMenu::item { padding: 6px 24px 6px $space_md; border-radius: 4px; }
QMenu::item:selected { background-color: $selection; }
QMenu::item:disabled { color: $text_disabled; }
QMenu::separator { height: 1px; background-color: $border_subtle; margin: $space_xs $space_sm; }
QMenuBar { background-color: $chrome; border-bottom: 1px solid $border_subtle; }
QMenuBar::item { padding: 4px 10px; border-radius: 4px; background: transparent; }
QMenuBar::item:selected { background-color: $hover; }

QToolTip {
    background-color: $tooltip;
    color: $text_primary;
    border: 1px solid $border_strong;
    border-radius: $radius_control;
    padding: 4px $space_sm;
}
"""
)


def build_qss(
    palette: Palette,
    metrics: Metrics,
    *,
    ui_family: str = "Segoe UI",
    mono_family: str = "Consolas",
    icon_family: str = "FluentSystemIcons-Regular",
    glyph_urls: Mapping[str, str] | None = None,
) -> str:
    """生成完整 QSS；令牌缺失会抛 ``KeyError``，便于测试卡住不完整映射。"""
    return QSS_TEMPLATE.substitute(
        token_map(
            palette,
            metrics,
            ui_family=ui_family,
            mono_family=mono_family,
            icon_family=icon_family,
            glyph_urls=glyph_urls,
        )
    )
