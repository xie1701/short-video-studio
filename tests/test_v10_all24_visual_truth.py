#!/usr/bin/env python3
"""V10.0 全 24 套风格真母本视觉配方、常驻顶栏、7 大镜面原语及原生解耦回归单测。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import render_mp4  # noqa: E402
import step5_subtitles  # noqa: E402
import style_truth_renderer  # noqa: E402
import style_visual_truth  # noqa: E402


class V10All24VisualTruthTests(unittest.TestCase):
    def test_all_24_styles_registered_with_accurate_top_bars(self) -> None:
        self.assertEqual(len(style_visual_truth.STYLE_VISUAL_TRUTH), 24)
        top_bar_styles = {
            sid for sid in style_visual_truth.STYLE_VISUAL_TRUTH
            if style_visual_truth.get_persistent_top_bar_spec(sid) is not None
        }
        # 严格对齐 24 套真母本：仅 style_03/06/08/12 拥有全片常驻顶栏
        self.assertEqual(top_bar_styles, {"style_03", "style_06", "style_08", "style_12"})

    def test_truth_top_bars_and_overrides_render_clean_rgba(self) -> None:
        for sid in ("style_03", "style_06", "style_08", "style_12"):
            tb = style_visual_truth.get_persistent_top_bar_spec(sid)
            im = style_truth_renderer.render_truth_overlay_png(
                {
                    "kind": "persistent_top_bar",
                    "top_bar_mode": tb["mode"],
                    "text_or_asset": "破产负债200万+",
                    "subtext": "照抄制度全垮了",
                },
                style_id=sid,
            )
            self.assertIsNotNone(im, sid)
            self.assertGreater(im.width, 180)
            self.assertGreater(im.height, 60)

        for sid in ("style_06", "style_08", "style_10", "style_12", "style_15", "style_18", "style_20", "style_21", "style_24"):
            im = style_truth_renderer.render_truth_overlay_png(
                {"kind": "center_burst_title", "text_or_asset": "核心方法"},
                style_id=sid,
            )
            self.assertIsNotNone(im, sid)
            self.assertGreater(im.width, 60)

    def test_truth_scene_panel_commands_and_ai_cutout_assets_build(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            dummy_in = str(p / "in.mp4")
            for mode, variant in (
                ("dark_circle_panel_pip", "top_red_pain"),
                ("dark_circle_panel_pip", "top_blur_qmark"),
                ("dark_circle_panel_pip", "bottom_left_red_quote"),
                ("dark_typewriter_card_pip", "s16_blur_dark_typewriter"),
                ("dark_typewriter_card_pip", "s18_blur_white_strip"),
                ("framed_box_pip", "s04_dual_yellow_header"),
                ("framed_box_pip", "s17_dual_compare_cards"),
                ("framed_box_pip", "s24_rounded_impact"),
                ("review_whiteboard_pip", "s24_whiteboard_multi"),
                ("pinyin_hero_pip", "s15_pinyin_split"),
            ):
                cmd = style_truth_renderer.build_truth_scene_panel_cmd(
                    ffmpeg_bin="ffmpeg",
                    media_path=dummy_in,
                    src_in=0.0,
                    src_avail=3.0,
                    base_vf_chain="scale=1080:1920",
                    pip_layout={
                        "mode": mode,
                        "variant": variant,
                        "panel_title": "核心真相",
                        "quote_text": "先看清底层前提再谈方法落地",
                    },
                    clip_frames=75,
                    fps=30,
                    out_mp4=p / f"{mode}_{variant}.mp4",
                )
                self.assertIsNotNone(cmd, (mode, variant))
                self.assertIn("-filter_complex", cmd)

            # AI 抠像背景图与人后巨字图
            g_png = style_truth_renderer.render_person_behind_giant_v10_png("核心真相", p / "giant.png", accent_hex="#D62828", frame_black=True)
            self.assertTrue(g_png.is_file())
            b_png = style_truth_renderer.render_cutout_scene_bg_png({"mode": "pure_black_cutout_pip", "quote_text": "先找前提再落地"}, p / "blk.png")
            self.assertTrue(b_png.is_file())

    def test_native_style_mode_director_plan_does_not_suppress_step5_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "03_audio_events.json").write_text(json.dumps({
                "locked_duration_sec": 3.0,
                "sentences": [
                    {"id": "s1", "text": "今日带大家食呢间点赞茶楼", "start_sec": 0.1, "end_sec": 2.8, "keywords": ["点赞茶楼"]},
                ],
            }), encoding="utf-8")
            (pdir / "03b_director_plan.json").write_text(json.dumps({
                "style_id": "style_01",
                "native_style_mode": True,
                "acts": [],
            }), encoding="utf-8")
            res = step5_subtitles.run_step5(pdir, track="store", template_id="style_01")
            self.assertEqual(res.get("suppressed_count", 0), 0)
            self.assertTrue(res.get("entries"))
            self.assertFalse(any(e.get("suppressed") for e in res["entries"]))

    def test_extract_sentence_punch_rejects_cross_word_fragments(self) -> None:
        # 严防上游脏 keywords 碎片（如「法找他」「二一组」「括我自己」）上屏
        sent_bad = {
            "text": "看到做法先找他的前提，海底捞的师徒制核心",
            "keywords": ["法找他", "前提"],
        }
        w1 = style_visual_truth.extract_sentence_punch(sent_bad, max_chars=4)
        self.assertNotIn(w1, ("法找他", "一个"))
        self.assertEqual(w1, "前提")

        sent_ghost = {
            "text": "卡座一个中空跟住有啲灯笼有啲造型岭南元素嘅",
            "keywords": ["二一组"],
        }
        w2 = style_visual_truth.extract_sentence_punch(sent_ghost, max_chars=4)
        self.assertNotEqual(w2, "二一组")
        self.assertGreaterEqual(len(w2), 2)

    def test_merge_short_chunks_strips_leading_punct_and_counts_clean_chars(self) -> None:
        chunks = [
            {"text": "我哋嘅四大天王", "start_sec": 0.0, "end_sec": 1.2, "words": [], "keywords": []},
            {"text": "：虾饺排", "start_sec": 1.2, "end_sec": 1.8, "words": [], "keywords": []},
            {"text": "骨凤爪同埋叉烧包", "start_sec": 1.8, "end_sec": 3.0, "words": [], "keywords": []},
        ]
        merged = step5_subtitles._merge_short_chunks(chunks, min_chars=4, max_px=720, font_size=58)
        self.assertTrue(all(not str(c["text"]).startswith("：") for c in merged))
        self.assertTrue(all(len(str(c["text"]).replace("：", "")) >= 4 for c in merged))


if __name__ == "__main__":
    unittest.main()
