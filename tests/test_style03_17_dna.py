import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_template_overlays as overlays
from common import CANVAS_WIDTH
from style_dna_profiles import (
    STYLE_DNA_PROFILES,
    get_style_dna_profile,
    resolve_style03_bar_layout,
    resolve_style17_frame_layout,
)


def _color_mask(image, rgba):
    return np.all(np.asarray(image) == rgba, axis=2)


class StyleDnaProfileTests(unittest.TestCase):
    def test_profiles_read_measured_assets_and_expose_read_only_views(self):
        self.assertEqual(set(STYLE_DNA_PROFILES), {"style_03", "style_17"})
        crimson = get_style_dna_profile("style_03")
        gold = get_style_dna_profile("style_17")
        self.assertEqual(crimson["color_baseline"]["y_mean_255"], 39.5)
        self.assertEqual(crimson["color_baseline"]["dark_ratio_pct"], 70.8)
        self.assertEqual(crimson["broll_overlay_ratio_target"], 0.097)
        self.assertTrue(crimson["persistent_top_bar"])
        self.assertTrue(crimson["quote_citation_card"])
        self.assertEqual(gold["color_baseline"]["y_mean_255"], 96.2)
        self.assertEqual(gold["color_baseline"]["dynamic_range_255"], 209.7)
        self.assertEqual(gold["broll_overlay_ratio_target"], 0.158)
        self.assertEqual(gold["giant_mode"], "giant_dual_clash")
        self.assertEqual(gold["flower_layout"], "gold_hairline_plus_square_plate")
        for profile in (crimson, gold):
            with self.subTest(style_id=profile["style_id"]):
                self.assertEqual(profile["font_candidates"][0], "SourceHanSerifCN-Heavy.otf")
                self.assertEqual(profile["stroke_mode"], "none")
                self.assertEqual(profile["stroke_px"], 0)
                self.assertEqual(profile["shadow_color"], "#000000E6")
                self.assertEqual(profile["shadow"]["shadow_rgba"], (0, 0, 0, 230))
                with self.assertRaises(TypeError):
                    profile["stroke_px"] = 4
                with self.assertRaises(TypeError):
                    profile["shadow"]["shadow_dx"] = 0
        safe = get_style_dna_profile("unknown_style")
        self.assertEqual(safe["stroke_px"], 0)
        self.assertEqual(safe["shadow"]["shadow_rgba"][3], 0)
        self.assertIsNone(safe["color_baseline"]["y_mean_255"])
        self.assertFalse(safe["quote_citation_card"])

    def test_crimson_layout_recognizes_quote_kinds_and_roles(self):
        bar = resolve_style03_bar_layout("保持耐心", "给自己时间")
        self.assertEqual(bar["bar_mode"], "full_width_crimson_bar")
        self.assertGreaterEqual(bar["width_px"], 960)
        self.assertEqual(bar["bg_hex"], "#B91C1C")
        self.assertEqual(bar["bg_rgba"], (185, 28, 28, 238))
        self.assertEqual((bar["main_hex"], bar["sub_hex"]), ("#FFFFFF", "#FFF589"))
        for kind, role in (("quote_citation", ""), ("persistent_top_bar", ""), ("flower_text", "quote_citation_card")):
            with self.subTest(kind=kind, role=role):
                card = resolve_style03_bar_layout("给变化留时间", elem_kind=kind, display_role=role)
                self.assertEqual(card["bar_mode"], "quote_citation_card")
                self.assertEqual(card["anchor"], "top")
                self.assertLess(card["width_px"], bar["width_px"])
                self.assertEqual(card["stroke_px"], 0)

    def test_gold_grid_requires_comparison_metadata_or_a_title_separator(self):
        for delim in (" vs ", "VS", "对比", "｜", "/"):
            with self.subTest(delim=delim):
                layout = resolve_style17_frame_layout(f"采光{delim}隔音", elem_kind="center_burst_title")
                self.assertEqual(layout["layout_mode"], "dual_gold_hairline_grid")
                self.assertEqual((layout["main_text"], layout["sub_text"]), ("采光", "隔音"))
                self.assertIn(layout["line_hex"], ("#FFBF17", "#FFDE00"))
                self.assertIn(layout["line_width_px"], (1, 2, 3))
                self.assertEqual(layout["bg_rgba"], (0, 0, 0, 178))
                self.assertEqual(layout["radius_px"], 0)
        forced = resolve_style17_frame_layout("采光", "隔音", elem_kind="flower_text", compare_mode=True)
        self.assertEqual(forced["layout_mode"], "dual_gold_hairline_grid")
        for text, kind in (("现场实测", "center_burst_title"), ("Invest", "center_burst_title"),
                           ("采光 vs 隔音", "flower_text")):
            with self.subTest(text=text, kind=kind):
                clean = resolve_style17_frame_layout(text, elem_kind=kind)
                self.assertEqual(clean["layout_mode"], "clean_fullscreen_gold")
                self.assertEqual(clean["line_width_px"], 0)
                self.assertEqual(clean["stroke_px"], 0)


class StyleDnaRenderTests(unittest.TestCase):
    def test_style03_full_width_bar_and_compact_quote_render_distinct_pixels(self):
        bar_elem = {"kind": "flower_text", "text_or_asset": "保持耐心", "subtext": "给自己时间"}
        with patch.object(overlays, "_draw_kinetic_line_sprite", wraps=overlays._draw_kinetic_line_sprite) as text:
            bar = overlays.render_template_packaging_png(bar_elem, style_id="style_03")
        self.assertIsNotNone(bar)
        self.assertEqual(bar.mode, "RGBA")
        self.assertGreaterEqual(bar.width, 960)
        self.assertLessEqual(bar.width, CANVAS_WIDTH)
        self.assertLess(bar.getpixel((0, 0))[3], 238)
        row = bar.height // 2
        for x in (8, bar.width // 2, bar.width - 9):
            self.assertEqual(bar.getpixel((x, row)), (185, 28, 28, 238))
        self.assertGreater(_color_mask(bar, (255, 255, 255, 255))[:row].sum(), 100)
        self.assertGreater(_color_mask(bar, (255, 245, 137, 255))[row:].sum(), 100)
        self.assertEqual(text.call_count, 2)
        for call in text.call_args_list:
            self.assertEqual(Path(call.args[1]).name, "SourceHanSerifCN-Heavy.otf")
            self.assertEqual(call.kwargs["stroke_w"], 0)
            self.assertGreater(call.kwargs["shadow_rgba"][3], 0)

        for routing in ({"kind": "quote_citation_card"}, {"kind": "persistent_top_bar"},
                        {"kind": "flower_text", "display_role": "quote_citation"}):
            with self.subTest(routing=routing):
                card = overlays.render_template_packaging_png({**bar_elem, **routing}, style_id="style_03")
                self.assertIsNotNone(card)
                self.assertEqual(card.mode, "RGBA")
                self.assertLess(card.width, bar.width * 0.75)
                self.assertLess(card.height, bar.height)
                self.assertEqual(card.getpixel((card.width - 9, card.height // 2)), (17, 12, 10, 238))
                self.assertGreater(_color_mask(card, (185, 28, 28, 238)).sum(), 50)

    def test_style03_respects_a_caller_font(self):
        elem = {"kind": "flower_text", "text_or_asset": "看清细节"}
        with patch.object(overlays, "_draw_kinetic_line_sprite", wraps=overlays._draw_kinetic_line_sprite) as draw:
            image = overlays.render_template_packaging_png(
                elem, style_id="style_03", font_candidates=["AlimamaShuHeiTi-Bold.otf"],
            )
        self.assertIsNotNone(image)
        self.assertEqual(Path(draw.call_args_list[0].args[1]).name, "AlimamaShuHeiTi-Bold.otf")

    def test_style17_renders_hairlines_only_for_comparisons_and_no_text_stroke(self):
        with patch.object(overlays, "_text_sprite", wraps=overlays._text_sprite) as text:
            compare = overlays.render_template_packaging_png(
                {"kind": "center_burst_title", "text_or_asset": "采光 VS 隔音"}, style_id="style_17",
            )
            clean = overlays.render_template_packaging_png(
                {"kind": "center_burst_title", "text_or_asset": "现场实测"}, style_id="style_17",
            )
        self.assertIsNotNone(compare)
        self.assertIsNotNone(clean)
        line = (255, 222, 0, 255)
        wide_rows = np.flatnonzero(_color_mask(compare, line).sum(axis=1) > compare.width * 0.85)
        groups = np.split(wide_rows, np.flatnonzero(np.diff(wide_rows) != 1) + 1)
        self.assertEqual(len(groups), 3)
        self.assertTrue(all(1 <= len(group) <= 3 for group in groups))
        self.assertIn(compare.height // 2, groups[1])
        self.assertGreater(_color_mask(compare, (255, 191, 23, 255))[:compare.height // 2].sum(), 100)
        self.assertGreater(_color_mask(compare, (255, 255, 255, 255))[compare.height // 2:].sum(), 100)
        self.assertEqual(_color_mask(clean, line).sum(), 0)
        self.assertGreater(_color_mask(clean, (255, 191, 23, 255)).sum(), 100)
        left, top, right, bottom = clean.getbbox()
        for pt in ((left, top), (right - 1, top), (left, bottom - 1), (right - 1, bottom - 1)):
            self.assertEqual(clean.getpixel(pt), (0, 0, 0, 178))
        for image in (compare, clean):
            self.assertEqual(image.mode, "RGBA")
            alpha = np.asarray(image)[:, :, 3]
            self.assertFalse(alpha[[0, -1], :].any())
            self.assertFalse(alpha[:, [0, -1]].any())
        self.assertTrue(text.call_args_list)
        self.assertTrue(all(call.kwargs["stroke_w"] == 0 for call in text.call_args_list))

    def test_style01_and_style02_pixels_are_isolated_from_the_new_layouts(self):
        elem = {"kind": "center_burst_title", "text_or_asset": "明亮而安静", "subtext": "光线变化"}
        controls = ("style_01", "style_02")
        before = {sid: overlays.render_template_packaging_png(elem, style_id=sid) for sid in controls}
        for sid in ("style_03", "style_17"):
            self.assertIsNotNone(overlays.render_template_packaging_png(elem, style_id=sid))
        with (
            patch("style_dna_profiles.resolve_style03_bar_layout", side_effect=AssertionError),
            patch("style_dna_profiles.resolve_style17_frame_layout", side_effect=AssertionError),
        ):
            for sid in controls:
                with self.subTest(style_id=sid):
                    actual = overlays.render_template_packaging_png(elem, style_id=sid)
                    self.assertEqual(actual.size, before[sid].size)
                    self.assertEqual(actual.tobytes(), before[sid].tobytes())

    def test_style_lock_passes_for_the_two_styles(self):
        for sid in ("style_03", "style_17"):
            with self.subTest(style_id=sid):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "style_lock.py"), "--style-id", sid],
                    cwd=ROOT, capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
