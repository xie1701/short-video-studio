#!/usr/bin/env python3
"""V9.0 全 24 套风格统一闭环、实体互斥防穿帮、分词护栏、gbro 封面与黑盒 OCR 实检回归单测。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import broll_match  # noqa: E402
import director_plan  # noqa: E402
import independent_verifier  # noqa: E402
import kinetic_choreographer  # noqa: E402
import render_template_overlays  # noqa: E402
import step5_subtitles  # noqa: E402


class V9All24DirectorAndQualityTests(unittest.TestCase):
    def test_all_24_styles_director_pipeline_and_dna_isolation(self) -> None:
        audio = {
            "sentences": [
                {"id": "s0", "text": "开业砸了三百万，足足六百平。", "start_sec": 0.0, "end_sec": 3.0},
                {"id": "s1", "text": "大厅卡座设计非常实用。", "start_sec": 3.1, "end_sec": 5.8},
                {"id": "s2", "text": "招牌手工早茶每天现包。", "start_sec": 6.0, "end_sec": 9.2},
            ],
            "locked_duration_sec": 9.5,
        }
        seen_fonts = set()
        for idx in range(1, 25):
            sid = f"style_{idx:02d}"
            self.assertTrue(kinetic_choreographer.is_director_plan_supported(sid))
            self.assertTrue(kinetic_choreographer.is_kinetic_stage_style(sid, enable_v6_director=True))
            dna = kinetic_choreographer.get_style_dna(sid)
            self.assertIsNotNone(dna, f"{sid} DNA 不得为空")
            self.assertEqual(dna["style_id"], sid)
            seen_fonts.update(dna.get("font_candidates") or [])

            plan = director_plan.generate_deterministic_director_plan(audio, style_id=sid)
            self.assertIn("cover_copy", plan)
            self.assertTrue(plan["cover_copy"]["headline_top"])
            self.assertTrue(plan["cover_copy"]["highlight_badge"])
            chk = director_plan.validate_director_plan(plan, audio)
            self.assertTrue(chk["ok"], f"{sid} 总谱校验失败: {chk['errors']}")
        self.assertGreaterEqual(len(seen_fonts), 2)

    def test_broll_entity_conflict_blocks_fish_for_chicken_and_dumpling_for_noodle(self) -> None:
        fish_clip = {
            "id": "fish_1",
            "role": "b_roll",
            "description": "揭开锅盖，露出蒸熟的鱼块与配菜",
            "scene_tags_zh": ["海鲜", "蒸鱼", "锅盖"],
            "semantic_tags": ["出餐", "蒸汽", "鱼"],
        }
        sc_chicken = broll_match.score_clip("所以食嘅就系鸡𠮶种原汁原味", fish_clip)
        self.assertEqual(sc_chicken["tier"], broll_match.TIER_NONE)
        self.assertEqual(sc_chicken["score"], 0.0)

        ext_clip = {
            "id": "ext_1",
            "role": "b_roll",
            "description": "仰视餐厅建筑外部大楼外观，街边电动车",
            "scene_tags_zh": ["建筑外墙", "大楼", "门头"],
            "semantic_tags": ["门店", "建筑"],
        }
        dumpling_clip = {
            "id": "dump_1",
            "role": "b_roll",
            "description": "灶台上把饺子一个个摆进煎锅煎制，近景",
            "scene_tags_zh": ["煎饺", "饺子"],
            "semantic_tags": ["手工", "饺子"],
        }
        self.assertEqual(
            broll_match.score_clip("招牌特色炒面绝对食过嘅话都会觉得非常之好食", ext_clip)["tier"],
            broll_match.TIER_NONE,
        )
        self.assertEqual(
            broll_match.score_clip("招牌特色炒面绝对食过嘅话都会觉得非常之好食", dumpling_clip)["tier"],
            broll_match.TIER_NONE,
        )

    def test_director_broll_clips_whitelist_validation(self) -> None:
        audio = {
            "sentences": [
                {"id": "s0", "text": "看看我们的点心手工制作。", "start_sec": 0.0, "end_sec": 3.0},
            ],
            "locked_duration_sec": 3.2,
        }
        bad_plan = {
            "step": "03b_director_plan",
            "source": "agent",
            "style_id": "style_02",
            "theme_profile": "style_02_unified",
            "acts": [{
                "act_id": "act_01",
                "sentence_ids": ["s0"],
                "dramatic_role": "proof",
                "emotion_intensity": 0.8,
                "primitive": "giant_anchor_sub",
                "visual_copy": {"headline": "手工制作", "items": [], "accent_token": "", "footnote": ""},
                "camera_intent": "steady_hold",
                "broll_policy": "allow_verified_broll",
                "broll_clips": ["non_existent_clip.mp4"],
                "sfx_budget": 1,
            }],
            "music_cues": [],
        }
        res_bad = director_plan.validate_director_plan(bad_plan, audio, valid_broll_names={"real_dimsum.mp4"})
        self.assertFalse(res_bad["ok"])
        self.assertTrue(any("broll_clips" in e for e in res_bad["errors"]))

        bad_plan["acts"][0]["broll_clips"] = ["real_dimsum.mp4"]
        res_ok = director_plan.validate_director_plan(bad_plan, audio, valid_broll_names={"real_dimsum.mp4"})
        self.assertTrue(res_ok["ok"], res_ok["errors"])

    def test_phrase_splitter_blocks_auxiliary_leading_lines_across_styles(self) -> None:
        chunks = kinetic_choreographer.split_clean_phrases("抄最快抄完还有种的踏实感", target_min=4, target_max=8)
        self.assertFalse(any(c.startswith(("的", "地", "得", "了", "着", "过", "嘅")) for c in chunks), chunks)

        canton = kinetic_choreographer.split_clean_phrases("所以食嘅就系鸡𠮶种原汁原味", target_min=4, target_max=7)
        self.assertFalse(any(c.startswith(("的", "嘅", "喺")) for c in canton), canton)

        sub_chunks = step5_subtitles._guard_no_aux_head([
            {"text": "抄最快抄完还有种", "start_sec": 0.0, "end_sec": 1.0, "words": []},
            {"text": "的踏实感", "start_sec": 1.0, "end_sec": 1.8, "words": []},
        ], max_chars=9)
        self.assertFalse(any(c["text"].startswith(("的", "了", "嘅")) for c in sub_chunks), sub_chunks)

    def test_unified_stage_supplements_uncovered_spoken_phrases_and_blackbox_picks_stage(self) -> None:
        audio = {
            "sentences": [
                {
                    "id": "s0",
                    "text": "可他在别人那成立是因为长在别人的前提上",
                    "start_sec": 0.0,
                    "end_sec": 4.2,
                },
            ],
            "locked_duration_sec": 4.5,
        }
        plan = {
            "step": "03b_director_plan",
            "source": "agent",
            "style_id": "style_02",
            "theme_profile": "style_02_unified",
            "acts": [{
                "act_id": "act_01",
                "sentence_ids": ["s0"],
                "dramatic_role": "twist",
                "emotion_intensity": 0.85,
                "primitive": "giant_anchor_sub",
                "visual_copy": {"headline": "前提", "items": [], "accent_token": "前提", "footnote": ""},
                "camera_intent": "jump_closeup",
                "broll_policy": "lock_aroll",
                "sfx_budget": 1,
            }],
            "music_cues": [],
        }
        stage = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
        texts = "".join(it["text_or_asset"] for it in stage["items"])
        self.assertIn("前提", texts)
        self.assertIn("在别人", texts)
        self.assertTrue(any(it.get("variant") == "bridge_bar" for it in stage["items"]))

        # 单层绝对零重叠 + 前景全部落入安全纵坐标带
        fg = [it for it in stage["items"] if it.get("z_layer", "foreground") == "foreground" and it.get("variant") != "bridge_bar"]
        bb = [it for it in stage["items"] if it.get("variant") == "bridge_bar"]
        for it in fg + bb:
            self.assertGreaterEqual(float(it["y_norm"]), -0.47, it)
        for b in bb:
            for f in fg:
                ov = min(float(b["end_sec"]), float(f["end_sec"])) - max(float(b["start_sec"]), float(f["start_sec"]))
                self.assertLessEqual(ov, 0.02, f"发现双层叠字重叠 {b} vs {f}")

    def test_extract_char_timeline_falls_back_evenly_when_words_lack_timestamps(self) -> None:
        sent = {
            "id": "s_none",
            "text": "两个人干分工计酬监督天然就有",
            "start_sec": 64.0,
            "end_sec": 70.0,
            "words": [{"word": ch, "start": None, "end": None} for ch in "两个人干分工计酬监督天然就有"],
        }
        spans = kinetic_choreographer._extract_char_timeline(sent)
        self.assertEqual(len(spans), len("两个人干分工计酬监督天然就有"))
        self.assertGreater(spans[-1][1], 68.5)

    def test_blackbox_takes_over_kinetic_stage_items_even_when_visual_elements_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            pkg = {
                "step": "06_packaging",
                "visual_elements": [],
                "kinetic_stage": {
                    "enabled": True,
                    "theme_profile": "style_02_unified",
                    "items": [
                        {
                            "id": "ks_u2_01_01",
                            "kind": "kinetic_stage_item",
                            "theme_profile": "style_02_unified",
                            "variant": "bridge_bar",
                            "z_layer": "foreground",
                            "text_or_asset": "小老板第一反应",
                            "start_sec": 0.2,
                            "end_sec": 2.4,
                            "center_px": [540, 1392],
                            "font_size_px": 69,
                            "x_norm": 0.0,
                        }
                    ],
                },
            }
            sub = {"entries": [{"id": "sub_001", "text": "小老板第一反应", "start_sec": 0.2, "end_sec": 2.4, "suppressed": True}]}
            (pdir / "06_packaging.json").write_text(json.dumps(pkg), encoding="utf-8")
            (pdir / "05_subtitles.json").write_text(json.dumps(sub), encoding="utf-8")
            (pdir / "04_edl.json").write_text(json.dumps({"fps": 30, "edit_profile": "talking", "clips": []}), encoding="utf-8")

            from PIL import Image, ImageDraw
            frame = pdir / "f.jpg"
            im = Image.new("RGB", (1080, 1920), (20, 20, 20))
            ImageDraw.Draw(im).text((320, 1370), "ABC DEF", fill=(250, 250, 250))
            ImageDraw.Draw(im).rectangle([(320, 1365), (760, 1410)], fill=(250, 250, 250))
            ImageDraw.Draw(im).rectangle([(330, 1372), (750, 1402)], fill=(20, 20, 20))
            im.save(frame)
            with (
                mock.patch.object(independent_verifier, "_extract_sample_frames", return_value=([frame], [1.0])),
                mock.patch.object(
                    independent_verifier,
                    "inspect_images",
                    side_effect=lambda paths: {
                        str(p): {
                            "backend": "apple_vision",
                            "faces": [],
                            "texts": [{"text": "小老板第一反应", "conf": 0.99, "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}],
                        }
                        for p in paths
                    },
                ),
            ):
                sub_res, read_res, _col, _fr, leak_res = independent_verifier.verify_visual_and_subtitle_blackbox(
                    frame, pdir, [(0.2, 2.4)], pipeline_dir=pdir,
                )
            self.assertEqual(sub_res["status"], "PASS", sub_res)
            self.assertEqual(read_res["status"], "PASS", read_res)
            self.assertEqual(leak_res["status"], "PASS", leak_res)

    def test_non_style02_director_enables_single_layer_stage_and_safe_y(self) -> None:
        audio = {
            "sentences": [
                {"id": "s0", "text": "不是人的问题，而是方式被固化了", "start_sec": 0.0, "end_sec": 3.2},
                {"id": "s1", "text": "今日带大家食呢只招牌手工鸡", "start_sec": 3.4, "end_sec": 6.6},
            ],
            "locked_duration_sec": 7.0,
        }
        profile = {"primary_stage_mode": "closeup_chest_stage", "allow_behind_person": False}
        plan_01 = director_plan.generate_deterministic_director_plan(
            audio, style_id="style_01", composition_profile={"theme_hint": "store_vibrant", "allow_behind_person": False},
        )
        stage_01 = kinetic_choreographer.plan_kinetic_stage(
            audio, style_id="style_01", fps=30, director_plan=plan_01, composition_profile=profile, enable_v6_director=True,
        )
        self.assertTrue(stage_01.get("single_layer_stage"))
        fg_01 = [it for it in stage_01["items"] if it.get("z_layer", "foreground") == "foreground"]
        self.assertTrue(fg_01)
        self.assertTrue(all(float(it["y_norm"]) >= -0.45 for it in fg_01), fg_01)
        bridges = [it for it in fg_01 if it.get("variant") == "bridge_bar"]
        highlights = [
            it for it in fg_01
            if it.get("variant") not in {"bridge_bar", "editorial_ticker", "pillar_badge"} and not it.get("base_caption")
        ]
        for b in bridges:
            for h in highlights:
                ov = min(float(b["end_sec"]), float(h["end_sec"])) - max(float(b["start_sec"]), float(h["start_sec"]))
                self.assertLessEqual(ov, 0.04, f"style_01 存在同窗叠字 {b} vs {h}")

        plan_03 = director_plan.generate_deterministic_director_plan(audio, style_id="style_03", composition_profile=profile)
        stage_03 = kinetic_choreographer.plan_kinetic_stage(
            audio, style_id="style_03", fps=30, director_plan=plan_03, composition_profile=profile, enable_v6_director=True,
        )
        self.assertTrue(stage_03.get("single_layer_stage"))
        raw_caps = kinetic_choreographer.business_caption_items(
            audio,
            [{"id": "sub_1", "text": "方式被固化了", "start_sec": 0.0, "end_sec": 3.2}],
            fps=30,
            highlight_items=[],
        )
        clipped = kinetic_choreographer.clip_bridge_items_against_foreground(
            raw_caps, foreground_pool=stage_03["items"], fps=30, min_slice_sec=0.45,
        )
        self.assertEqual(clipped, [])

        # V6 向后兼容检查：style_02 无 single_layer_stage 走 store_vibrant 时保持关闭
        stage_legacy_v6 = kinetic_choreographer.plan_kinetic_stage(
            audio,
            style_id="style_02",
            fps=30,
            director_plan={"theme_profile": "store_vibrant", "source": "agent", "acts": []},
            composition_profile=profile,
        )
        self.assertFalse(stage_legacy_v6.get("single_layer_stage"))

    def test_render_kinetic_stage_item_caps_centered_sprite_width_across_styles(self) -> None:
        long_text = "方式被彻底固化了还要再看一遍"
        for sid, theme, variant in (
            ("style_01", "store_vibrant", "bridge_bar"),
            ("style_03", "business_editorial", "hero_white"),
            ("style_03", "business_editorial", "inline_rich"),
        ):
            elem = {
                "id": f"test_{sid}_{variant}",
                "style_id": sid,
                "theme_profile": theme,
                "variant": variant,
                "z_layer": "foreground",
                "text_or_asset": long_text,
                "start_sec": 0.0,
                "end_sec": 2.0,
                "center_px": [540, 1392],
                "x_norm": 0.0,
                "font_size_px": 74,
            }
            if variant == "inline_rich":
                elem["inline_parts"] = [
                    {"text": "方式被彻底固化了", "color": "#FFFFFF", "size_units": 12.0},
                    {"text": "还要看一遍", "color": "#F59E0B", "size_units": 14.0},
                ]
            im = render_template_overlays.render_kinetic_stage_item_png(elem)
            self.assertIsNotNone(im)
            self.assertLessEqual(im.width, 640, f"{sid}/{variant} 宽度 {im.width}px 超出 640px")


if __name__ == "__main__":
    unittest.main()
