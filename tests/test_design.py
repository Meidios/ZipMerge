"""设计系统单元测试：令牌、QSS 生成、图标字形、主题服务。"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zipmerge.ui.design import (  # noqa: E402
    COMPACT,
    COMFORTABLE,
    DARK,
    DEFAULT_ACCENT,
    DENSITIES,
    LIGHT,
    MODES,
    ThemeService,
    apply_window_effects,
    build_qss,
    glyph_png,
    icons,
    metrics_for_density,
    palette_for,
    qss_glyph_urls,
    qcolor,
    read_system_accent,
    read_system_mode,
    token_map,
)
from zipmerge.ui.design.qss import GLYPH_TOKENS  # noqa: E402
from zipmerge.ui.design.tokens import (  # noqa: E402
    Metrics,
    Palette,
    darken,
    lighten,
    luminance,
    mix,
    normalize_accent,
    on_color,
    rgba,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802 - unittest 约定
    global _app
    _app = QApplication.instance() or QApplication([])


class ColorMathTests(unittest.TestCase):
    @staticmethod
    def contrast_ratio(foreground: str, background: str) -> float:
        lighter, darker = sorted(
            (luminance(foreground), luminance(background)), reverse=True
        )
        return (lighter + 0.05) / (darker + 0.05)

    def test_mix_endpoints_and_middle(self) -> None:
        self.assertEqual(mix("#000000", "#FFFFFF", 0.0), "#000000")
        self.assertEqual(mix("#000000", "#FFFFFF", 1.0), "#FFFFFF")
        self.assertEqual(mix("#000000", "#FFFFFF", 0.5), "#808080")

    def test_lighten_darken(self) -> None:
        self.assertEqual(darken("#FFFFFF", 1.0), "#000000")
        self.assertEqual(lighten("#000000", 1.0), "#FFFFFF")

    def test_rgba_and_on_color(self) -> None:
        self.assertEqual(rgba("#0078D4", 0.5), "rgba(0, 120, 212, 0.50)")
        self.assertEqual(on_color("#FFFFFF"), "#000000")
        self.assertEqual(on_color("#101010"), "#FFFFFF")
        self.assertEqual(on_color("#0078D4"), "#FFFFFF")
        self.assertEqual(on_color("#4EA1FF"), "#000000")
        self.assertEqual(on_color("#F5C54A"), "#000000")

    def test_on_color_always_meets_wcag_aa_for_representative_accents(self) -> None:
        for accent in ("#0078D4", "#4EA1FF", "#F5C54A", "#123456"):
            with self.subTest(accent=accent):
                self.assertGreaterEqual(self.contrast_ratio(on_color(accent), accent), 4.5)

    def test_normalize_accent_falls_back(self) -> None:
        self.assertEqual(normalize_accent(None), DEFAULT_ACCENT)
        self.assertEqual(normalize_accent("zzz"), DEFAULT_ACCENT)
        self.assertEqual(normalize_accent("#0078d4"), "#0078D4")
        self.assertEqual(normalize_accent("0078D4"), "#0078D4")


class TokenTests(unittest.TestCase):
    @staticmethod
    def contrast_ratio(foreground: str, background: str) -> float:
        lighter, darker = sorted(
            (luminance(foreground), luminance(background)), reverse=True
        )
        return (lighter + 0.05) / (darker + 0.05)

    def test_modes_and_densities_registered(self) -> None:
        self.assertEqual(set(MODES), {DARK, LIGHT})
        self.assertEqual(set(DENSITIES), {"comfortable", "compact"})
        self.assertEqual(metrics_for_density("nope"), COMFORTABLE)

    def test_palette_fields_filled_for_both_modes(self) -> None:
        for mode in MODES:
            palette = palette_for(mode, "#0078D4")
            for field in fields(Palette):
                value = getattr(palette, field.name)
                self.assertIsInstance(value, str, field.name)
                self.assertTrue(value, f"{mode}.{field.name} 为空")

    def test_light_and_dark_differ(self) -> None:
        dark = palette_for(DARK)
        light = palette_for(LIGHT)
        self.assertNotEqual(dark.base, light.base)
        self.assertNotEqual(dark.text_primary, light.text_primary)

    def test_refreshed_palette_values(self) -> None:
        dark = palette_for(DARK, "#4EA1FF")
        light = palette_for(LIGHT, "#4EA1FF")
        self.assertEqual(
            (dark.base, dark.chrome, dark.layer, dark.card, dark.canvas),
            ("#141416", "#1B1B1D", "#202022", "#252628", "#18181A"),
        )
        self.assertEqual(
            (dark.text_primary, dark.text_secondary, dark.text_disabled, dark.success),
            ("#ECEEF1", "#A5AAB2", "#6E747C", "#3CCE76"),
        )
        self.assertEqual(dark.border_subtle, "#34373C")
        self.assertEqual(dark.border_strong, "#44484E")
        self.assertEqual(
            (light.base, light.chrome, light.layer, light.card, light.canvas),
            ("#E5E9EE", "#EDF0F4", "#F4F6F9", "#F8FAFC", "#EEF2F6"),
        )
        self.assertEqual(
            (light.text_primary, light.text_secondary, light.text_disabled),
            ("#1C232A", "#495766", "#7B8794"),
        )
        self.assertEqual(dark.accent, "#4EA1FF")
        self.assertEqual(light.accent, "#4793E9")
        self.assertEqual(dark.selection, "rgba(78, 161, 255, 0.18)")
        self.assertEqual(light.selection, "rgba(71, 147, 233, 0.14)")

    def test_text_contrast_across_primary_surfaces(self) -> None:
        for mode in MODES:
            palette = palette_for(mode)
            for surface in (palette.base, palette.layer, palette.card):
                with self.subTest(mode=mode, surface=surface, role="primary"):
                    self.assertGreaterEqual(
                        self.contrast_ratio(palette.text_primary, surface), 7.0
                    )
                with self.subTest(mode=mode, surface=surface, role="secondary"):
                    self.assertGreaterEqual(
                        self.contrast_ratio(palette.text_secondary, surface), 4.5
                    )
            # 禁用态不是正文信息，刻意保持更低层级；仍需与表面清晰区分。
            with self.subTest(mode=mode, role="disabled"):
                self.assertGreaterEqual(
                    self.contrast_ratio(palette.text_disabled, palette.layer), 3.0
                )

    def test_display_accent_is_visible_on_brightest_theme_surface(self) -> None:
        for mode in MODES:
            for raw in ("#4C4A48", "#0078D4", "#000000", "#FFFFFF", "#4EA1FF"):
                with self.subTest(mode=mode, raw=raw):
                    palette = palette_for(mode, raw)
                    self.assertGreaterEqual(
                        self.contrast_ratio(palette.accent, palette.card), 3.0
                    )
        self.assertNotEqual(palette_for(DARK, "#4C4A48").accent, "#4C4A48")
        self.assertEqual(palette_for(LIGHT, "#4C4A48").accent, "#4C4A48")

    def test_metrics_are_positive(self) -> None:
        for metrics in (COMFORTABLE, COMPACT):
            for field in fields(Metrics):
                self.assertGreater(getattr(metrics, field.name), 0, field.name)


class QssTests(unittest.TestCase):
    def test_token_map_covers_palette_fields(self) -> None:
        palette = palette_for(DARK)
        tokens = token_map(palette, COMFORTABLE)
        for field in fields(Palette):
            if field.name == "mode":
                continue
            self.assertIn(field.name, tokens, field.name)
            self.assertEqual(tokens[field.name], getattr(palette, field.name))

    def test_token_map_covers_metrics(self) -> None:
        tokens = token_map(palette_for(LIGHT), COMPACT)
        required = {
            "radius_control", "radius_panel", "space_xs", "space_sm", "space_md",
            "space_lg", "space_xl", "font_small", "font_body", "font_subtitle",
            "font_title", "row_height", "control_height", "icon_sm", "icon_md", "icon_lg",
        }
        self.assertTrue(required.issubset(tokens))
        self.assertEqual(tokens["row_height"], f"{COMPACT.row_height}px")

    def test_glyph_tokens_always_present(self) -> None:
        tokens = token_map(palette_for(DARK), COMFORTABLE)
        for name in GLYPH_TOKENS:
            self.assertIn(f"glyph_{name}", tokens)
            self.assertTrue(tokens[f"glyph_{name}"])

    def test_qss_fully_substituted(self) -> None:
        for mode in MODES:
            for density in DENSITIES:
                palette = palette_for(mode, "#0F7B0F")
                metrics = metrics_for_density(density)
                stylesheet = build_qss(palette, metrics, ui_family="Test UI", mono_family="Test Mono")
                self.assertNotIn("$", stylesheet, f"{mode}/{density} 仍有未替换占位符")
                self.assertIn(palette.accent, stylesheet)
                self.assertIn("Test UI", stylesheet)
                self.assertGreater(len(stylesheet), 5000)

    def test_visual_hierarchy_and_state_rules_are_present(self) -> None:
        palette = palette_for(DARK)
        stylesheet = build_qss(palette, COMFORTABLE)
        self.assertIn("QAbstractItemView:focus", stylesheet)
        self.assertIn("background-color: transparent", stylesheet)
        self.assertIn("border: 1px solid transparent", stylesheet)
        self.assertIn("alternate-background-color: #202022", stylesheet)
        self.assertIn("QHeaderView { background-color: #202022", stylesheet)
        self.assertIn("background-color: #18181A", stylesheet)
        self.assertIn("border-left: 2px solid", stylesheet)
        self.assertIn(f"border-bottom: 2px solid {palette.accent}", stylesheet)
        self.assertIn('QWidget#temporaryArchiveCard[state="ready"]', stylesheet)
        self.assertIn('QWidget#temporaryArchiveCard[state="stale"]', stylesheet)
        self.assertIn('QWidget#temporaryArchiveCard[state="missing"]', stylesheet)
        self.assertIn("QToolBar {", stylesheet)
        self.assertIn("background-color: #1B1B1D", stylesheet)
        self.assertIn("QMenuBar { background-color: #1B1B1D", stylesheet)
        self.assertIn("QStatusBar { background-color: #1B1B1D", stylesheet)
        self.assertIn("QGroupBox {\n    background-color: #202022", stylesheet)

    def test_missing_token_raises(self) -> None:
        with self.assertRaises(KeyError):
            # 故意剥掉一个颜色令牌，substitute 必须报错而不是静默留空
            from string import Template

            from zipmerge.ui.design.qss import QSS_TEMPLATE

            tokens = token_map(palette_for(DARK), COMFORTABLE)
            del tokens["accent"]
            Template.substitute(QSS_TEMPLATE, tokens)


class IconTests(unittest.TestCase):
    #: PLAN 6.2 明确列出的图标位，必须都能解析出字形。
    PLAN_ICONS: tuple[str, ...] = (
        "folder_zip", "archive_multiple", "music_note", "image", "document",
        "add", "delete", "save", "export", "import", "filter", "search",
        "arrow_sync", "checkmark", "warning",
    )

    def test_plan_icons_resolve(self) -> None:
        icon_set = icons()
        self.assertTrue(icon_set.loaded)
        for name in self.PLAN_ICONS:
            code = icon_set.code(name)
            self.assertIsNotNone(code, f"图标缺失：{name}")
            self.assertGreater(code or 0, 0)

    def test_unknown_icon_returns_none(self) -> None:
        self.assertIsNone(icons().code("definitely_not_an_icon"))

    def test_glyph_png_is_written_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = glyph_png("checkmark", 16, "#FFFFFF", root)
            self.assertTrue(first)
            self.assertTrue(Path(first).exists())
            second = glyph_png("checkmark", 16, "#FFFFFF", root)
            self.assertEqual(first, second)

    def test_qss_glyph_urls_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            urls = qss_glyph_urls(
                text_color="#FFFFFF",
                secondary_color="#AAAAAA",
                on_accent="#FFFFFF",
                accent="#0078D4",
                root=Path(tmp),
            )
        self.assertEqual(set(urls), set(GLYPH_TOKENS))
        self.assertTrue(all(url.startswith("url(") for url in urls.values()))

    def test_qcolor_parses_token_colors(self) -> None:
        # 设计令牌的 text_primary 等是 rgba() 字符串，QColor 本身不认
        color = qcolor(rgba("#FFFFFF", 0.89))
        self.assertTrue(color.isValid())
        self.assertEqual(color.name(), "#ffffff")
        self.assertEqual(color.alpha(), round(0.89 * 255))
        self.assertEqual(qcolor("#0078D4").name(), "#0078d4")

    def test_glyph_png_uses_rgba_token_color(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = glyph_png("chevron_down", 16, rgba("#FFFFFF", 0.60), Path(tmp))
            self.assertTrue(path)
            self.assertTrue(Path(path).exists())


class ThemeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ThemeService()

    def test_default_follows_system(self) -> None:
        self.assertEqual(self.service.effective_mode, read_system_mode())
        self.assertTrue(self.service.accent_is_system)

    def test_mode_switch_emits_and_updates_palette(self) -> None:
        seen: list[Palette] = []
        self.service.changed.connect(seen.append)
        initial = self.service.palette().mode
        target = LIGHT if initial == DARK else DARK
        self.service.set_mode(target)
        self.assertEqual(self.service.effective_mode, target)
        self.assertEqual(self.service.palette().mode, target)
        self.assertEqual(len(seen), 1)
        self.service.set_mode(target)  # 重复设置不重复发信号
        self.assertEqual(len(seen), 1)

    def test_density_switch_updates_metrics(self) -> None:
        seen: list[Metrics] = []
        self.service.metrics_changed.connect(seen.append)
        self.service.set_density("compact")
        self.assertEqual(self.service.metrics().row_height, COMPACT.row_height)
        self.assertEqual(len(seen), 1)

    def test_accent_override_and_reset(self) -> None:
        self.service.set_accent("#ff0000")
        self.assertFalse(self.service.accent_is_system)
        self.assertEqual(self.service.palette().accent, "#FF0000")
        self.service.set_accent(None)
        self.assertTrue(self.service.accent_is_system)

    def test_stylesheet_usable(self) -> None:
        stylesheet = self.service.stylesheet()
        self.assertNotIn("$", stylesheet)
        self.assertIn(self.service.palette().accent, stylesheet)


class PlatformTests(unittest.TestCase):
    def test_system_mode_is_known_value(self) -> None:
        self.assertIn(read_system_mode(), MODES)

    def test_system_accent_is_hex(self) -> None:
        accent = read_system_accent()
        self.assertRegex(accent, r"^#[0-9A-F]{6}$")

    def test_dwm_effects_degrade_silently(self) -> None:
        result = apply_window_effects(0, dark=True)
        self.assertEqual(set(result), {"dark_titlebar", "rounded", "backdrop"})
        self.assertFalse(any(result.values()))


if __name__ == "__main__":
    unittest.main()
