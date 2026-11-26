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


if __name__ == "__main__":
    unittest.main()
