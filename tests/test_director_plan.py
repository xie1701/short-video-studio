from __future__ import annotations

from contextlib import redirect_stderr
from copy import deepcopy
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import read_json, write_json
from director_plan import (
    DIRECTOR_PRIMITIVES,
    PLAN_FILENAME,
    TASKS_FILENAME,
    THEME_PROFILES,
    _amount,
    _numeric_focus,
    _short_conclusion,
    build_director_tasks,
    generate_deterministic_director_plan,
    load_or_build_director_plan,
    validate_director_plan,
)


def audio(*rows, total=None):
    sentences = [
        {"id": f"sent_{i:03d}", "text": text, "start_sec": start, "end_sec": end, "keywords": []}
        for i, (start, end, text) in enumerate(rows, 1)
    ]
    return {"locked_duration_sec": total if total is not None else rows[-1][1], "sentences": sentences}


def act(*ids, primitive="plain_bridge", headline="", items=(), accent="", camera="steady_hold"):
    return {
        "sentence_ids": [f"sent_{i:03d}" for i in ids],
        "primitive": primitive,
        "camera_intent": camera,
        "visual_copy": {"headline": headline, "items": list(items), "accent_token": accent, "footnote": ""},
    }


def music(kind, start, end, depth=-28.0):
    return {"type": kind, "start_sec": start, "end_sec": end, "depth_db": depth, "fade_ms": 80}


class DirectorPlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.pipeline = Path(temp.name)
        self.audio = audio(
            (0.0, 3.0, "公司破产那年我负债两百万"),
            (3.25, 4.5, "不是被骗，也不是行情差"),
            (4.9, 10.0, "回去和团队核对现金流和利润"),
        )
        self.plan = {
            "theme_profile": "business_editorial",
            "acts": [
                act(1, 2, primitive="negation_strike", headline="负债 200万",
                    items=("✕ 不是被骗", "✕ 不是行情差"), accent="200万", camera="slow_push_115"),
                act(3),
            ],
            "music_cues": [
                music("delay_entry", 0, 2.9),
                music("silence_drop", 3.15, 3.6),
                music("section_swell", 6.0, 8.0, 3.0),
            ],
        }

    def write_audio(self, source=None):
        return write_json(self.pipeline / "03_audio_events.json", self.audio if source is None else source)

    def validate(self, plan=None, source=None, profile=None):
        return validate_director_plan(
            self.plan if plan is None else plan, self.audio if source is None else source, profile,
        )

    def assert_error(self, result, message):
        self.assertFalse(result["ok"])
        self.assertTrue(any(message in error for error in result["errors"]), result["errors"])

    def test_task_packet_preserves_locked_lines_and_pending_composition(self):
        source = deepcopy(self.audio)
        source["sentences"][0]["keywords"] = ["负债", "两百万"]
        source["sentences"].reverse()
        audio_path = self.write_audio(source)
        path = build_director_tasks(self.pipeline)
        self.assertEqual(path, self.pipeline / TASKS_FILENAME)
        packet = read_json(path)
        self.assertEqual(packet["step"], "03b_director_tasks")
        self.assertEqual(packet["style_id"], "style_02")
        self.assertEqual(packet["composition_hint"], "pending")
        self.assertEqual(packet["locked_duration_sec"], 10.0)
        self.assertEqual([row["id"] for row in packet["sentences"]], ["sent_001", "sent_002", "sent_003"])
        self.assertEqual(packet["sentences"][0], {
            "id": "sent_001", "text": "公司破产那年我负债两百万",
            "start_sec": 0.0, "end_sec": 3.0, "duration_sec": 3.0, "keywords": ["负债", "两百万"],
        })
        self.assertEqual(packet["broll_summary"], {"has_real_broll": False, "count": 0, "clips": [], "tags_preview": []})
        self.assertEqual(packet["director_primitives"], list(DIRECTOR_PRIMITIVES))
        self.assertEqual(packet["theme_profiles"], list(THEME_PROFILES))
        rules = " ".join(packet["rules"])
        for term in ("7 字", "0.35..0.55", "silence_drop", "delay_entry"):
            self.assertIn(term, rules)
        self.assertEqual(read_json(audio_path), source)
        self.assertFalse((self.pipeline / PLAN_FILENAME).exists())

    def test_task_packet_adds_visual_evidence_with_inventory_roles(self):
        self.write_audio()
        write_json(self.pipeline / "00_inventory.json", {"clips": [
            {"filename": "detail.mov", "suggested_role": "b_roll_candidate", "semantic_tags": ["蒸笼"]},
            {"filename": "speaker.mov", "suggested_role": "a_roll_candidate"},
            {"filename": "discard.mov", "suggested_role": "b_roll_candidate", "usable": False},
            {"filename": "synthetic.mov", "role": "b_roll", "generated": True},
        ]})
        write_json(self.pipeline / "00_visual_index.json", {"clips": [
            {"filename": "detail.mov", "description": "蒸好后掀盖展示点心", "scene_tags_zh": ["虾饺"], "entities": ["蒸笼"]},
            {"filename": "speaker.mov", "role": "b_roll", "description": "主讲人镜头"},
            {"filename": "unlabeled.mov", "tags": ["门店"]},
            {"filename": "room.mov", "role": "b_roll", "tags": ["大厅"]},
        ]})
        write_json(self.pipeline / "01b_composition_profile.json", {
            "primary_stage_mode": "closeup_chest_stage", "allow_behind_person": False,
            "theme_hint": "business_editorial",
        })
        custom = self.pipeline / "tasks.json"
        self.assertEqual(build_director_tasks(self.pipeline, out_path=custom), custom)
        result = read_json(custom)
        self.assertEqual(result["composition_hint"], {
            "primary_stage_mode": "closeup_chest_stage", "allow_behind_person": False,
            "theme_hint": "business_editorial",
        })
        summary = result["broll_summary"]
        self.assertTrue(summary["has_real_broll"])
        self.assertEqual(summary["count"], 2)
        self.assertCountEqual(summary["tags_preview"], ["虾饺", "蒸笼", "大厅"])
        self.assertEqual(summary["clips"][0], {
            "filename": "detail.mov", "description": "蒸好后掀盖展示点心",
            "tags": ["虾饺", "蒸笼"], "entities": ["蒸笼"],
        })

    def test_validation_recomputes_coverage_and_accepts_grounded_copy(self):
        self.plan["highlight_coverage_ratio"] = 0.99
        self.plan["acts"][1]["visual_copy"]["headline"] = "现金流利润"
        original = deepcopy(self.plan)
        result = self.validate()
        self.assertEqual(self.plan, original)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["warnings"], [])
        cleaned = result["sanitized_plan"]
        self.assertEqual(cleaned["highlight_coverage_ratio"], 0.45)
        self.assertEqual((cleaned["step"], cleaned["source"]), ("03b_director_plan", "agent"))

    def test_validation_rejects_headlines_and_list_lines_over_seven_chars(self):
        source = deepcopy(self.audio)
        source["sentences"][0]["text"] = "公司破产那年之后重新对账"
        visual = self.plan["acts"][0]["visual_copy"]
        visual.update(headline="→ 公司破产那年之后", items=["✓ 公司破产那年之后"], accent_token="")
        result = self.validate(source=source)
        self.assert_error(result, "visual_copy.headline 长度 8")
        self.assert_error(result, "visual_copy.items[0] 长度 8")
        self.assertEqual(result["sanitized_plan"]["acts"][0]["visual_copy"]["headline"], visual["headline"])

    def test_equivalent_numbers_preserve_amount_unit_sign_and_ordinal(self):
        source = audio(
            (0.0, 4.0, "负债两百万，账款两百多万，增幅百分之五，产量三点五万，茶楼六百平，第一点先查账"),
            (4.0, 10.0, "回去按明细检查"),
        )
        plan = {
            "theme_profile": "business_editorial",
            "acts": [
                act(1, primitive="framework_pillar", headline="负债200万",
                    items=("200万+", "5%", "3.5万", "600平米"), accent="01"),
                act(2),
            ],
        }
        self.assertTrue(self.validate(plan, source)["ok"], self.validate(plan, source)["errors"])
        for token in ("五千万", "20万", "600人", "6%", "-5%", "02"):
            with self.subTest(token=token):
                plan["acts"][0]["visual_copy"]["accent_token"] = token
                self.assert_error(self.validate(plan, source), "hallucinated_token")

    def test_grounding_stays_in_the_act_and_rejects_invented_names(self):
        self.audio["sentences"][1]["text"] += "，晴岚很远，到了资本市场才懂原因"
        self.audio["sentences"][2]["text"] += "，下一步要准备五千万"
        for field, value in (
            ("headline", "负债五千万"),
            ("items", ["营收冠军"]),
            ("items", ["晴岚资本"]),
            ("accent_token", "200万+"),
        ):
            with self.subTest(field=field, value=value):
                plan = deepcopy(self.plan)
                plan["acts"][0]["visual_copy"][field] = value
                self.assert_error(self.validate(plan), "hallucinated_token")

    def test_figures_of_speech_do_not_license_numeric_claims(self):
        self.audio["sentences"][0]["text"] = "千万不要漏算，万一出问题再核对"
        self.plan["acts"][0]["visual_copy"] = {"headline": "1000万", "accent_token": "10001"}
        result = self.validate()
        self.assert_error(result, "'1000万' 无法")
        self.assert_error(result, "'10001' 无法")

    def test_sentence_ids_must_cover_the_locked_timeline_in_order(self):
        for ids, expected in (
            (("sent_001", "sent_404"), "unknown_sentence_id"),
            (("sent_002", "sent_001"), "sentence_order"),
            (("sent_001", "sent_001", "sent_002"), "duplicate_sentence_id"),
            (("sent_001",), "missing_sentence_ids"),
        ):
            with self.subTest(ids=ids):
                plan = deepcopy(self.plan)
                plan["acts"][0]["sentence_ids"] = list(ids)
                self.assert_error(self.validate(plan), expected)

    def test_invalid_enums_and_malformed_input_are_rejected(self):
        plan = deepcopy(self.plan)
        plan["theme_profile"] = "unknown"
        plan["acts"][0].update(primitive="rocket", camera_intent="zoom_300")
        plan["acts"][1]["visual_copy"]["items"] = "应当使用列表"
        result = self.validate(plan)
        for key in ("theme_profile", "primitive", "camera_intent", "visual_copy.items"):
            self.assert_error(result, key)
        self.assert_error(self.validate(plan=[]), "JSON 对象")
        self.assert_error(self.validate(source={"sentences": [None]}), "必须是对象")

    def test_composition_profile_blocks_behind_person_primitive(self):
        self.plan["acts"][0]["primitive"] = "hero_anchor_plus_danmaku"
        self.assertTrue(self.validate(profile={"allow_behind_person": True})["ok"])
        self.assert_error(self.validate(profile={"allow_behind_person": False}), "behind_person_not_allowed")
        self.audio["sentences"][0]["source_media"] = "wide.mov"
        self.audio["sentences"][1]["source_media"] = "close.mov"
        profile = {"aroll_profiles": [{"media_path": "norm_02_close.mp4", "allow_behind_person": False}]}
        self.assert_error(self.validate(profile=profile), "behind_person_not_allowed")
        self.audio["sentences"][1]["source_media"] = "wide.mov"
        self.assertTrue(self.validate(profile=profile)["ok"])

    def test_music_cue_bounds_depth_and_soft_duration(self):
        for cue, expected in (
            (music("silence_drop", 0.0, 3.51), "时长超过 3.5"),
            (music("silence_drop", 9.9, 10.1), "时间段必须"),
            (music("delay_entry", -0.1, 1.0), "时间段必须"),
            (music("section_swell", 2.0, 2.0), "时间段必须"),
            (music("section_swell", float("nan"), 2.0), "时间段必须"),
            (music("silence_drop", 1.0, 1.45, -11.0), "depth_db"),
            (music("unknown", 1.0, 1.45), ".type"),
        ):
            with self.subTest(cue=cue):
                plan = dict(self.plan, music_cues=[cue])
                self.assert_error(self.validate(plan), expected)
        plan = dict(self.plan, music_cues=[music("silence_drop", 0.5, 1.5)])
        result = self.validate(plan)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(any("0.25..0.90" in warning for warning in result["warnings"]))

    def test_coverage_uses_union_of_highlight_acts_and_warns_on_deviation(self):
        source = audio((0.0, 3.0, "清点订单"), (2.5, 4.25, "按时回款"), (4.5, 10.0, "今天做一遍复核"))
        plan = dict(self.plan, acts=[act(1, primitive="hero_intro_pop"), act(2, primitive="entity_stack_plus_top"), act(3)])
        result = self.validate(plan, source)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["sanitized_plan"]["highlight_coverage_ratio"], 0.425)
        plan["acts"][2]["primitive"] = "hero_intro_pop"
        result = self.validate(plan, source)
        self.assertTrue(any("25%..70%" in warning for warning in result["warnings"]))

    def assert_fallback_grounded(self, plan, source, profile=None):
        result = self.validate(plan, source, profile)
        self.assertTrue(result["ok"], result["errors"])
        self.assertGreaterEqual(result["sanitized_plan"]["highlight_coverage_ratio"], 0.35)
        self.assertLessEqual(result["sanitized_plan"]["highlight_coverage_ratio"], 0.55)
        self.assertEqual(
            [sid for a in plan["acts"] for sid in a["sentence_ids"]],
            [s["id"] for s in source["sentences"]],
        )
        for planned in plan["acts"]:
            visual = planned["visual_copy"]
            for line in [visual["headline"], *visual["items"]]:
                compact = "".join(ch for ch in line if not ch.isspace() and ch not in "✕✓≠→:：·")
                self.assertLessEqual(len(compact), 7, line)

    def test_offline_business_plan_keeps_negation_framework_contrast_and_breath(self):
        source = audio(
            (0.0, 3.0, "公司破产那年我负债200多万"),
            (3.1, 4.1, "不是被骗"),
            (4.25, 5.6, "也不是行情差"),
            (6.0, 9.0, "回头看了好几遍订单，才知道从哪查起"),
            (9.5, 10.85, "第一点，先问生意前提"),
            (11.05, 12.55, "第二，检查回款"),
            (13.0, 14.5, "第三，核对现金流"),
            (15.0, 21.0, "把账本放在桌上，一笔一笔跟负责人核对"),
            (21.5, 25.5, "师徒制核心不是师徒两个字而是分利润"),
            (26.0, 28.0, "关键是兑现承诺"),
            (29.0, 36.0, "最后由大家都来复核结果"),
        )
        profile = {"primary_stage_mode": "closeup_chest_stage", "allow_behind_person": False}
        plan = generate_deterministic_director_plan(source, composition_profile=profile)
        self.assertEqual(plan, generate_deterministic_director_plan(source, composition_profile=profile))
        self.assertEqual(plan["source"], "fallback_deterministic")
        self.assertEqual(plan["theme_profile"], "business_editorial")
        self.assert_fallback_grounded(plan, source, profile)

        opening = plan["acts"][0]
        self.assertEqual(opening["primitive"], "negation_strike")
        self.assertEqual(opening["visual_copy"]["headline"], "负债200万+")
        self.assertEqual(opening["visual_copy"]["items"], ["✕ 不是被骗", "✕ 不是行情差"])
        self.assertEqual(opening["sentence_ids"], ["sent_001", "sent_002", "sent_003"])
        pillars = [planned["visual_copy"] for planned in plan["acts"] if planned["primitive"] == "framework_pillar"]
        self.assertEqual([visual["accent_token"] for visual in pillars], ["01", "02", "03"])
        self.assertEqual([visual["headline"] for visual in pillars], ["先问生意前提", "检查回款", "核对现金流"])
        contrast = next(planned for planned in plan["acts"] if planned["primitive"] == "concept_split_card")
        self.assertEqual(contrast["camera_intent"], "side_shift_card")
        self.assertEqual(contrast["visual_copy"]["items"], ["✕ 师徒两个字", "✓ 分利润"])
        self.assertTrue(any(planned["primitive"] == "clean_breath" for planned in plan["acts"]))
        self.assertTrue(all(planned["broll_policy"] == "lock_aroll" for planned in plan["acts"]))
        self.assertEqual(plan["music_cues"][0]["type"], "delay_entry")
        drop = next(cue for cue in plan["music_cues"] if cue["type"] == "silence_drop")
        self.assertAlmostEqual(drop["end_sec"] - drop["start_sec"], 0.45)
        self.assertEqual(drop["depth_db"], -28.0)

    def test_offline_store_plan_keeps_numeric_and_menu_primitives(self):
        source = audio(
            (0.0, 2.0, "今日同大家来探店"),
            (2.2, 4.6, "呢间茶楼有六百平"),
            (5.0, 8.7, "我哋嘅四大天王虾饺排骨凤爪同埋叉烧包"),
            (9.0, 11.0, "招牌就系桑拿鸡"),
            (11.2, 16.0, "沿住大厅行过去就见到后厨"),
            (16.5, 20.0, "宜家带大家坐低等菜上桌"),
        )
        inventory = {"clips": [
            {"filename": "room.mov", "suggested_role": "b_roll_candidate", "description": "宽厅"},
            {"filename": "dimsum.mov", "suggested_role": "b_roll_candidate", "description": "虾饺排骨"},
            {"filename": "steam.mov", "suggested_role": "b_roll_candidate", "description": "桑拿鸡"},
        ]}
        profile = {"primary_stage_mode": "wide_behind_person", "allow_behind_person": True}
        plan = generate_deterministic_director_plan(source, inventory=inventory, composition_profile=profile)
        self.assertEqual(plan["theme_profile"], "store_vibrant")
        self.assert_fallback_grounded(plan, source, profile)
        selected = {planned["primitive"]: planned for planned in plan["acts"]}
        self.assertIn("hero_anchor_plus_danmaku", selected)
        self.assertEqual(selected["num_anchor_dim_overlay"]["visual_copy"]["headline"], "六百平")
        self.assertEqual(
            selected["list_build_gather_dim_overlay"]["visual_copy"]["items"],
            ["虾饺", "排骨", "凤爪", "叉烧包"],
        )
        self.assertEqual(selected["entity_stack_plus_top"]["visual_copy"]["headline"], "桑拿鸡")
        self.assertEqual(selected["list_build_gather_dim_overlay"]["broll_policy"], "allow_verified_broll")
        close_profile = {"allow_behind_person": False}
        close = generate_deterministic_director_plan(source, inventory=inventory, composition_profile=close_profile)
        self.assertNotIn("hero_anchor_plus_danmaku", [planned["primitive"] for planned in close["acts"]])
        self.assert_fallback_grounded(close, source, close_profile)

    def test_split_contrast_stays_in_one_act_and_triggers_music(self):
        source = audio(
            (0.0, 2.0, "核心不是规模。"),
            (2.2, 4.2, "而是复购。"),
            (4.5, 10.0, "用每次回款的凭证一起复核"),
        )
        plan = generate_deterministic_director_plan(source)
        self.assert_fallback_grounded(plan, source)
        contrast = plan["acts"][0]
        self.assertEqual(contrast["sentence_ids"], ["sent_001", "sent_002"])
        self.assertEqual(contrast["primitive"], "concept_split_card")
        self.assertEqual(contrast["visual_copy"]["items"], ["✕ 规模", "✓ 复购"])
        self.assertEqual(plan["music_cues"][0]["type"], "silence_drop")

    def test_continuous_contrast_with_no_punctuation(self):
        source = audio(
            (0.0, 4.0, "不是货不好是位置偏"),
            (4.5, 10.0, "我们复核了连续几天的进店数量"),
        )
        plan = generate_deterministic_director_plan(source)
        self.assert_fallback_grounded(plan, source)
        opening = plan["acts"][0]
        self.assertEqual(opening["primitive"], "negation_strike")
        self.assertEqual(opening["visual_copy"]["items"], ["✕ 不是货不好"])
        self.assertEqual(opening["visual_copy"]["headline"], "位置偏")

    def test_single_indivisible_sentence_warns_without_forging_timestamps(self):
        source = audio((0.0, 8.0, "第一点，先检查回款"))
        plan = generate_deterministic_director_plan(source)
        self.assertEqual(plan["acts"][0]["sentence_ids"], ["sent_001"])
        result = self.validate(plan, source)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(any("highlight_coverage_ratio" in warning for warning in result["warnings"]))

    def test_narration_does_not_fill_highlight_time_with_cut_off_keywords(self):
        source = audio(
            (0.0, 2.0, "先把合同印出来发给合作方"),
            (2.1, 4.1, "以前我们想事情总要多问一遍"),
            (4.2, 6.2, "把公司名改好再寄回原来的地址"),
            (6.3, 8.3, "样品摆上去之后就把桌面清理一遍"),
            (8.4, 10.4, "他管过人也帮其他团队安排过工作"),
            (10.5, 13.0, "后来大家到资料室那找答案和记录"),
        )
        for sentence, fragment in zip(source["sentences"], (
            "印出来发", "们想事情", "公司名改", "上去之后", "他管过人", "那找答案",
        )):
            sentence.update(keywords=[fragment], display_keywords=[fragment])
        plan = generate_deterministic_director_plan(source)
        self.assertTrue(all(
            planned["primitive"] in ("plain_bridge", "clean_breath")
            and not any(planned["visual_copy"].values())
            for planned in plan["acts"]
        ))
        result = self.validate(plan, source)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(plan["highlight_coverage_ratio"], 0.0)
        self.assertTrue(any("highlight_coverage_ratio" in warning for warning in result["warnings"]))

        store = audio(
            (0.0, 4.0, "这间门店一直由店长收好每天的排班记录"),
            (4.5, 10.0, "我把以前写好的笔记重新放回去"),
        )
        plan = generate_deterministic_director_plan(store, composition_profile={"theme_hint": "store_vibrant"})
        self.assertEqual(plan["theme_profile"], "store_vibrant")
        self.assertEqual(plan["highlight_coverage_ratio"], 0.0)

    def test_dense_opening_reserves_real_breathing_time(self):
        source = audio(
            (0.0, 2.0, "第一，核对订单"),
            (2.05, 4.0, "第二，检查回款"),
            (4.05, 6.0, "第三，登记往来"),
            (6.05, 8.0, "第四，汇总票据"),
            (8.05, 10.0, "第五，整理预算"),
            (10.05, 12.0, "第六，复核合同"),
            (12.2, 20.0, "这次复核由不同岗位一起在工作台完成"),
        )
        plan = generate_deterministic_director_plan(source)
        self.assert_fallback_grounded(plan, source)
        highlights = [
            i for i, planned in enumerate(plan["acts"]) if planned["primitive"] == "framework_pillar"
        ]
        self.assertGreaterEqual(len(highlights), 3)
        self.assertTrue(any(
            0 < i < highlights[-1] and plan["acts"][i]["primitive"] == "clean_breath"
            for i in range(len(plan["acts"]))
        ))
        active = 0
        previous_end = -1.0
        for i in highlights:
            sentence = source["sentences"][i]
            if sentence["start_sec"] - previous_end >= 0.35:
                active = 0
            active += 1
            self.assertLessEqual(active, 2)
            previous_end = sentence["end_sec"]

    def test_standalone_ordinals_join_only_a_nearby_conclusion(self):
        for heading, badge in (("第一", "01"), ("第二点", "02"), ("第三条", "03"), ("第四步", "04")):
            with self.subTest(heading=heading):
                source = audio(
                    (0.0, 0.8, heading), (2.0, 4.2, "点单先付款"),
                    (4.5, 10.0, "后台会用统一的表格核对完整的交易流程"),
                )
                plan = generate_deterministic_director_plan(source)
                self.assert_fallback_grounded(plan, source)
                combined = plan["acts"][0]
                self.assertEqual(combined["sentence_ids"], ["sent_001", "sent_002"])
                self.assertEqual(combined["primitive"], "framework_pillar")
                self.assertEqual(combined["visual_copy"]["headline"], "点单先付款")
                self.assertEqual(combined["visual_copy"]["accent_token"], badge)

                source["sentences"][1]["start_sec"] = 2.01
                plan = generate_deterministic_director_plan(source)
                self.assertEqual(plan["acts"][0]["sentence_ids"], ["sent_001"])
                self.assertNotIn("framework_pillar", [planned["primitive"] for planned in plan["acts"]])

    def test_standalone_ordinals_keep_long_conclusions_along_word_boundaries(self):
        source = audio(
            (0.0, 0.6, "第一"), (0.8, 2.6, "先答清楚你自己的生意"),
            (3.2, 3.8, "第二"), (4.0, 5.6, "看到做法先找他的前提"),
            (6.2, 6.8, "第三"), (7.0, 9.0, "过了夫妻店那条线规矩只能自己长"),
            (9.6, 10.2, "第四"), (10.4, 12.5, "我们想事情的方式被固化了"),
            (13.0, 25.5, "接下来我再把这一年发生的那些事按时间顺序讲一遍"),
        )
        plan = generate_deterministic_director_plan(source)
        self.assert_fallback_grounded(plan, source)
        pillars = [planned for planned in plan["acts"] if planned["primitive"] == "framework_pillar"]
        self.assertEqual(
            [
                (planned["sentence_ids"], planned["visual_copy"]["accent_token"], planned["visual_copy"]["headline"])
                for planned in pillars
            ],
            [
                (["sent_001", "sent_002"], "01", "自己的生意"),
                (["sent_003", "sent_004"], "02", "先找他的前提"),
                (["sent_005", "sent_006"], "03", "只能自己长"),
                (["sent_007", "sent_008"], "04", "方式被固化了"),
            ],
        )

    def test_conclusion_tail_rejects_cut_amount_before_selecting_the_next_suffix(self):
        text = "今年预付一百二十万开展业务"
        words = ["今年", "预付", "一百二十", "万", "开展", "业务"]
        sentences = [{"text": text, "words": [{"word": word} for word in words]}]
        self.assertEqual(_short_conclusion(text, sentences, limit=6), "开展业务")

    def test_ordinal_collocations_do_not_produce_pillars(self):
        for phrase in (
            "第一反应是复核", "第一时间看通知", "第一眼先看价格", "第一次来柜台",
            "第一名已经登记", "第一位已经签到", "第一批今天到货", "第一个月刚开张",
        ):
            with self.subTest(phrase=phrase):
                source = audio((0.0, 4.0, phrase), (4.5, 10.0, "这些信息留在记录里方便同事翻查"))
                plan = generate_deterministic_director_plan(source)
                self.assertNotIn("framework_pillar", [planned["primitive"] for planned in plan["acts"]])

    def test_long_contrast_extracts_grounded_short_positive_clause(self):
        for location in ("店利润里", "店的利润里面"):
            with self.subTest(location=location):
                source = audio(
                    (0.0, 4.0, f"师徒制核心不是师徒两个字是师傅能从徒弟的{location}分到钱"),
                    (4.5, 10.0, "接下来要按照之前定好的方法一起核对"),
                )
                source["sentences"][0].update(keywords=["师傅能从"], display_keywords=["徒弟的店"])
                plan = generate_deterministic_director_plan(source)
                self.assert_fallback_grounded(plan, source)
                self.assertEqual(plan["acts"][0]["primitive"], "concept_split_card")
                self.assertEqual(plan["acts"][0]["visual_copy"]["items"], ["✕ 师徒两个字", "✓ 分到钱"])

    def test_fails_closed_if_a_contrast_side_cannot_be_safely_shortened(self):
        for phrase in ("核心不是规模而是供应商付款排班业务复盘", "核心不是供应商付款排班业务复盘而是规模"):
            with self.subTest(phrase=phrase):
                source = audio((0.0, 4.0, phrase), (4.5, 10.0, "大家一致把这项工作放到下周复核"))
                plan = generate_deterministic_director_plan(source)
                self.assertNotIn("concept_split_card", [planned["primitive"] for planned in plan["acts"]])
                self.assertTrue(self.validate(plan, source)["ok"])

    def test_fraction_followed_by_area_does_not_ground_a_fabricated_amount(self):
        source = audio(
            (0.0, 4.0, "展厅占三分之一两百平，份额是3分之12"),
            (4.5, 10.0, "前后都有通道，工作人员可以在那里核对"),
        )
        plan = dict(self.plan, acts=[
            act(1, primitive="num_anchor_dim_overlay", headline="200平", items=("三分之一", "4")),
            act(2),
        ])
        result = self.validate(plan, source)
        self.assertTrue(result["ok"], result["errors"])
        plan["acts"][0]["visual_copy"]["headline"] = "1200平"
        self.assert_error(self.validate(plan, source), "hallucinated_token")
        self.assertEqual(_numeric_focus("展厅三分之一两百平"), ("两百平", "两百平"))
        self.assertIsNone(_amount("一两百"))
        self.assertIsNone(_amount("两三万"))
        self.assertEqual(_numeric_focus("展厅一两百平"), ("", ""))

    def test_numeric_focus_prefers_scale_and_ignores_single_classifiers(self):
        for spoken, expected in (
            ("一间六百平", "六百平"),
            ("五十个人里百分之五", "百分之五"),
            ("先说六百平还要投入两百万", "两百万"),
            ("先说一句，去一次，看一间，挑一个", ""),
            ("一平也是实测面积", "一平"),
        ):
            with self.subTest(spoken=spoken):
                self.assertEqual(_numeric_focus(spoken), (expected, expected))

    def test_store_lists_use_general_word_boundaries(self):
        for items, has_words in (
            (["手链", "耳饰", "发夹", "胸针"], False),
            (["护目镜", "隔音塞", "扭力扳手", "工装指套"], True),
        ):
            with self.subTest(items=items):
                source = audio(
                    (0.0, 0.8, "门店四大单品"), (0.9, 4.4, "".join(items)),
                    (4.8, 10.0, "沿走廊走下去就可以看到完整的陈列"),
                )
                if has_words:
                    source["sentences"][1]["words"] = [{"word": item} for item in items]
                profile = {"theme_hint": "store_vibrant"}
                plan = generate_deterministic_director_plan(source, composition_profile=profile)
                self.assert_fallback_grounded(plan, source, profile)
                first = plan["acts"][0]
                self.assertEqual(first["primitive"], "list_build_gather_dim_overlay")
                self.assertEqual(first["sentence_ids"], ["sent_001", "sent_002"])
                self.assertEqual(first["visual_copy"]["items"], items)

    def test_cta_detects_new_action_words(self):
        for action in ("私信", "主页", "预约", "领取", "点击"):
            with self.subTest(action=action):
                source = audio(
                    (0.0, 4.0, f"{action}取得这次活动的详细资料"),
                    (4.5, 10.0, "这份资料由现场工作人员统一整理"),
                )
                plan = generate_deterministic_director_plan(source)
                self.assert_fallback_grounded(plan, source)
                self.assertEqual(plan["acts"][0]["primitive"], "inline_emphasis_cta")
                self.assertEqual(plan["acts"][0]["visual_copy"]["headline"], action)

    def test_likes_in_store_names_do_not_trigger_cta(self):
        profile = {"theme_hint": "store_vibrant"}
        for name in ("点赞茶楼", "點讚茶樓", "點讚 餐廳", "点赞门店", "点赞店"):
            with self.subTest(name=name):
                source = audio(
                    (0.0, 4.0, f"{name}的入口就在这条街的转角"),
                    (4.5, 10.0, "这家门店靠窗的位置会提前安排"),
                )
                plan = generate_deterministic_director_plan(source, composition_profile=profile)
                self.assertEqual(plan["theme_profile"], "store_vibrant")
                self.assertNotIn("inline_emphasis_cta", [planned["primitive"] for planned in plan["acts"]])

        for action in ("点赞", "點讚"):
            with self.subTest(action=action):
                source = audio(
                    (0.0, 4.0, f"这间茶楼今天有探店记录，记得{action}支持"),
                    (4.5, 10.0, "这段内容由现场同事统一整理"),
                )
                plan = generate_deterministic_director_plan(source, composition_profile=profile)
                self.assert_fallback_grounded(plan, source, profile)
                self.assertEqual(plan["acts"][0]["primitive"], "inline_emphasis_cta")
                self.assertEqual(plan["acts"][0]["visual_copy"]["headline"], action)

    def test_per_shot_composition_precedes_project_default(self):
        self.plan["acts"][0]["primitive"] = "hero_anchor_plus_danmaku"
        self.audio["sentences"][0]["source_media"] = "wide/scene.mov"
        self.audio["sentences"][1]["source_media"] = "wide/scene.mov"
        profile = {
            "allow_behind_person": False,
            "primary_stage_mode": "closeup_chest_stage",
            "aroll_profiles": [
                {"media_path": "wide/scene.mov", "allow_behind_person": True},
                {"media_path": "close/scene.mov", "allow_behind_person": False},
            ],
        }
        self.assertTrue(self.validate(profile=profile)["ok"])
        self.audio["sentences"][1]["source_media"] = "unmatched.mov"
        self.assert_error(self.validate(profile=profile), "behind_person_not_allowed")
        profile["allow_behind_person"] = True
        self.assertTrue(self.validate(profile=profile)["ok"])
        self.audio["sentences"][1]["source_media"] = "close/scene.mov"
        self.assert_error(self.validate(profile=profile), "behind_person_not_allowed")

    def test_load_prefers_valid_agent_plan_and_falls_back_read_only(self):
        audio_path = self.write_audio()
        original = audio_path.read_bytes()
        plan_path = self.pipeline / PLAN_FILENAME
        self.assertIsNone(load_or_build_director_plan(self.pipeline, generate_fallback=False))
        generated = load_or_build_director_plan(self.pipeline)
        self.assertEqual(generated["source"], "fallback_deterministic")
        self.assertFalse(plan_path.exists())

        write_json(plan_path, self.plan)
        before = plan_path.read_bytes()
        self.assertEqual(load_or_build_director_plan(self.pipeline)["source"], "agent")
        self.assertEqual(plan_path.read_bytes(), before)

        rejected = deepcopy(self.plan)
        rejected["acts"][0]["visual_copy"]["accent_token"] = "9999万"
        write_json(plan_path, rejected)
        stderr = StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(load_or_build_director_plan(self.pipeline), generated)
        self.assertIn("[director_plan] [WARN]", stderr.getvalue())
        self.assertIn("hallucinated_token", stderr.getvalue())
        self.assertEqual(read_json(plan_path), rejected)

        plan_path.write_text("{broken", encoding="utf-8")
        with redirect_stderr(stderr):
            self.assertIsNone(load_or_build_director_plan(self.pipeline, generate_fallback=False))
            self.assertEqual(load_or_build_director_plan(self.pipeline), generated)
        self.assertIn(f"{PLAN_FILENAME} 读取失败", stderr.getvalue())
        self.assertEqual(plan_path.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(audio_path.read_bytes(), original)

    def test_cli_builds_applies_and_falls_back(self):
        self.write_audio()

        def cli(command, *args):
            return subprocess.run(
                [sys.executable, "-B", str(SCRIPTS / "director_plan.py"), command,
                 "--pipeline-dir", str(self.pipeline), *map(str, args)],
                capture_output=True, text=True,
            )

        process = cli("build-tasks")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue((self.pipeline / TASKS_FILENAME).exists())
        input_path = write_json(self.pipeline / "agent_plan.json", self.plan)
        process = cli("apply-plan", "--plan", input_path)
        self.assertEqual(process.returncode, 0, process.stderr)
        plan_path = self.pipeline / PLAN_FILENAME
        self.assertEqual(read_json(plan_path)["highlight_coverage_ratio"], 0.45)

        bad_plan = deepcopy(self.plan)
        bad_plan["acts"][0]["visual_copy"]["headline"] = "五千万"
        write_json(input_path, bad_plan)
        previous = plan_path.read_bytes()
        process = cli("apply-plan", "--plan", input_path)
        self.assertEqual(process.returncode, 1)
        self.assertIn("hallucinated_token", process.stderr)
        self.assertEqual(plan_path.read_bytes(), previous)

        process = cli("fallback")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(read_json(plan_path)["source"], "fallback_deterministic")


if __name__ == "__main__":
    unittest.main()
