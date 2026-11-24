#!/usr/bin/env python3
"""视觉类处理的**触发条件**回归测试（转场 / 抠像 / 变速 / 贴纸 / 特效）。

背景（用户 2026-11-24 原话）：
「不仅仅是音效和花字，还有转场、抠像、变速等等模版里包含的那些针对具体素材元素的处理，
都是要提炼出真正的风格条件才是。」

这里锁的是「什么时候**不**套」——比「能套」更要紧：
  · 拿着效果图讲解的镜头不抠像（真值 72 段抠像里只有 2 段与「指认画面」的台词同框）
  · 画面里没脸 / 脸太小不推近不抠像
  · A-roll 口播永远不许变速（口型必须与录音对齐）
  · 转场默认禁用（24 套源草稿转场数 = 0）
  · 真值没有的手法（这套风格没贴纸/没特效/没变速）一律不加

规则来源：`forensic/style_trigger_rules_visual.json`（dev_tools/build_trigger_rules_visual.py 生成）。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

import treatment_policy as TP  # noqa: E402

RULES = ROOT / "forensic" / "style_trigger_rules_visual.json"


def _face(h=0.20, w=0.35, faces=1):
    return {"h": h, "w": w, "faces": faces, "cx": 0.5, "cy": 0.35}


def _cutout_ctx(**kw):
    ctx = {
        "style_id": "style_21",
        "track_role": "a_roll",
        "duration_sec": 1.5,
        "timeline_in_sec": 0.0,
        "total_duration_sec": 60.0,
        "person_sec": 14.0,
        "used_sec": 0.0,
        "runs_started": 0,
        "continues_run": False,
        "face_anchor": _face(),
        "visual_text": "真人出镜 中景 口播",
        "sentence_text": "为什么同样是美业主理人",
        "main_media": "/p/01_normalized/norm_02_a.mp4",
        "secondary_media_path": "/p/01_normalized/norm_20_b.mp4",
    }
    ctx.update(kw)
    return ctx


class RulesFileTests(unittest.TestCase):
    def test_24_styles_all_have_five_sections(self):
        doc = json.loads(RULES.read_text(encoding="utf-8"))
        styles = doc["styles"]
        self.assertEqual(len(styles), 24)
        for sid, node in styles.items():
            for name in ("transition", "cutout", "speed", "overlay", "effect"):
                sec = node[name]
                self.assertIn("conditions", sec, f"{sid}.{name}")
                self.assertIn("scope", sec, f"{sid}.{name}")
                self.assertIn("limits", sec, f"{sid}.{name}")
                self.assertIn("evidence", sec, f"{sid}.{name}")

    def test_transition_truth_is_zero_for_all_24(self):
        doc = json.loads(RULES.read_text(encoding="utf-8"))
        for sid, node in doc["styles"].items():
            self.assertEqual(node["transition"]["evidence"]["truth_transition_count"], 0,
                             f"{sid} 转场真值应为 0")
            self.assertEqual(node["transition"]["default"], "hard_cut", sid)

    def test_speed_truth_only_two_styles(self):
        doc = json.loads(RULES.read_text(encoding="utf-8"))
        with_speed = [s for s, n in doc["styles"].items() if n["speed"]["enabled"]]
        self.assertEqual(sorted(with_speed), ["style_01", "style_24"])
        for sid in with_speed:
            ev = doc["styles"][sid]["speed"]["evidence"]
            self.assertEqual(ev["roles"], ["overlay"], f"{sid} 变速只应落在叠加/B-roll 层")
            self.assertEqual(ev["on_main_track"], 0, f"{sid} 主轨不许变速")

    def test_cutout_disabled_styles_match_truth(self):
        doc = json.loads(RULES.read_text(encoding="utf-8"))
        off = sorted(s for s, n in doc["styles"].items() if not n["cutout"]["enabled"])
        # 真值 0 段抠像的 6 套（style_04/06/08/15/17/20）
        self.assertEqual(off, ["style_04", "style_06", "style_08", "style_15", "style_17", "style_20"])


class TransitionTests(unittest.TestCase):
    def test_default_is_hard_cut(self):
        ok, reason, scope = TP.allow_transition({"style_id": "style_21"})
        self.assertFalse(ok)
        self.assertIn("硬切", reason)
        self.assertIn("mid_sentence", scope["forbid"])

    def test_user_explicit_allows_one(self):
        ok, reason, _sc = TP.allow_transition(
            {"style_id": "style_21", "user_explicit": True, "transition_whitelist": "客户要求叠化"})
        self.assertTrue(ok, reason)

    def test_chapter_break_needs_silence(self):
        ok, reason, _ = TP.allow_transition({"style_id": "style_21", "chapter_break": True, "silence_sec": 0.5})
        self.assertTrue(ok, reason)
        ok2, _, _ = TP.allow_transition({"style_id": "style_21", "chapter_break": True, "silence_sec": 0.05})
        self.assertFalse(ok2)

    def test_mid_sentence_never_allows(self):
        ok, reason, _ = TP.allow_transition(
            {"style_id": "style_21", "user_explicit": True, "cut_kind": "mid_sentence"})
        self.assertFalse(ok)
        self.assertIn("节奏点", reason)

    def test_hook_first_3s_blocked(self):
        ok, _, _ = TP.allow_transition(
            {"style_id": "style_21", "user_explicit": True, "in_hook": True})
        self.assertFalse(ok)


class CutoutTests(unittest.TestCase):
    def test_clean_talking_head_allows(self):
        ok, reason, scope = TP.allow_cutout(_cutout_ctx())
        self.assertTrue(ok, reason)
        self.assertEqual(scope["allow"], ["a_roll"])
        self.assertEqual(scope["layer"], "person_over_broll")

    def test_holding_a_design_sheet_blocks_cutout(self):
        """用户实测事故：举着效果图讲解的镜头被抠掉了图，只剩带白边的手。"""
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(visual_text="真人出镜 手上举着效果图 讲解"))
        self.assertFalse(ok)
        self.assertIn("效果图", reason)

    def test_deictic_line_blocks_cutout(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(sentence_text="你看这张图，是这个颜色"))
        self.assertFalse(ok)
        self.assertIn("指认画面", reason)

    def test_no_face_blocks_cutout(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(face_anchor=None))
        self.assertFalse(ok)
        self.assertIn("没检出人脸", reason)

    def test_tiny_face_blocks_cutout(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(face_anchor=_face(0.05, 0.03)))
        self.assertFalse(ok)
        self.assertIn("人脸太小", reason)

    def test_non_aroll_layer_blocks_cutout(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(track_role="b_roll"))
        self.assertFalse(ok)
        self.assertIn("b_roll", reason)

    def test_same_source_secondary_blocks_cutout(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(secondary_media_path="/p/01_normalized/norm_02_a.mp4"))
        self.assertFalse(ok)
        self.assertIn("副镜", reason)

    def test_style_without_cutout_truth_is_refused(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(style_id="style_17"))
        self.assertFalse(ok)
        self.assertIn("真值", reason)

    def test_segment_too_long_is_refused(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(duration_sec=9.0))
        self.assertFalse(ok)
        self.assertIn("上限", reason)

    def test_run_budget_stops_second_run(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(runs_started=1, continues_run=False))
        self.assertFalse(ok)
        self.assertIn("成段", reason)

    def test_continuing_a_run_is_fine(self):
        ok, reason, _ = TP.allow_cutout(_cutout_ctx(runs_started=1, continues_run=True))
        self.assertTrue(ok, reason)


class SpeedTests(unittest.TestCase):
    def test_aroll_never_speeds(self):
        ok, reason, _ = TP.allow_speed({"style_id": "style_24", "track_role": "a_roll",
                                        "duration_sec": 2.0, "speed": 2.0,
                                        "source_headroom_sec": 10.0, "total_duration_sec": 40.0})
        self.assertFalse(ok)
        self.assertIn("口型", reason)

    def test_broll_speed_allowed_when_truth_has_it(self):
        ok, reason, scope = TP.allow_speed({"style_id": "style_24", "track_role": "b_roll",
                                            "duration_sec": 2.0, "speed": 2.0,
                                            "source_headroom_sec": 10.0, "total_duration_sec": 40.0})
        self.assertTrue(ok, reason)
        self.assertIn("b_roll", scope["allow"])
        self.assertIn("a_roll", scope["forbid"])

    def test_style_without_speed_truth_stays_original(self):
        ok, reason, _ = TP.allow_speed({"style_id": "style_21", "track_role": "b_roll",
                                        "duration_sec": 2.0, "speed": 2.0,
                                        "source_headroom_sec": 10.0, "total_duration_sec": 40.0})
        self.assertFalse(ok)
        self.assertIn("原速", reason)

    def test_invented_speed_is_refused(self):
        ok, reason, _ = TP.allow_speed({"style_id": "style_24", "track_role": "b_roll",
                                        "duration_sec": 2.0, "speed": 3.0,
                                        "source_headroom_sec": 10.0, "total_duration_sec": 40.0})
        self.assertFalse(ok)
        self.assertIn("倍速", reason)

    def test_short_headroom_refused_by_pipeline(self):
        clips = [{"clip_id": "c1", "track_role": "b_roll", "duration_sec": 2.0,
                  "source_in_sec": 9.0, "source_out_sec": 11.0,
                  "media_path": "/p/a.mp4"}]
        stats = TP.apply_speed_policy(clips, None, Path("/tmp"), "style_24",
                                      {"items": [{"normalized_mp4": "/p/a.mp4", "duration_sec": 10.0}]})
        self.assertEqual(stats["applied"], 0)
        self.assertAlmostEqual(float(clips[0].get("speed") or 1.0), 1.0)


class OverlayAndEffectTests(unittest.TestCase):
    def test_sticker_at_cut_with_text_allowed(self):
        ok, reason, _ = TP.allow_overlay({"style_id": "style_21", "kind": "sticker",
                                          "track_role": "sticker", "duration_sec": 1.2,
                                          "timeline_in_sec": 3.0, "at_cut_dist": 0.0,
                                          "has_text": True, "total_duration_sec": 15.6})
        self.assertTrue(ok, reason)

    def test_sticker_floating_mid_shot_refused_when_style_pins_to_cut(self):
        """style_02 真值贴纸 100% 落在切点上 → 飘在镜中间就要拦。"""
        ok, reason, _ = TP.allow_overlay({"style_id": "style_02", "kind": "sticker",
                                          "track_role": "sticker", "duration_sec": 1.2,
                                          "timeline_in_sec": 3.0, "at_cut_dist": 1.4,
                                          "has_text": True, "total_duration_sec": 29.0})
        self.assertFalse(ok)
        self.assertIn("切点", reason)

    def test_sticker_mid_shot_allowed_when_style_floats(self):
        """style_21 真值贴纸本来就落在切点后 ~1s（0/2 贴切点）→ 不能拿别人的规则硬套。"""
        ok, reason, _ = TP.allow_overlay({"style_id": "style_21", "kind": "sticker",
                                          "track_role": "sticker", "duration_sec": 1.05,
                                          "timeline_in_sec": 3.78, "at_cut_dist": 0.97,
                                          "has_text": True, "total_duration_sec": 15.6})
        self.assertTrue(ok, reason)

    def test_sticker_without_flower_refused(self):
        ok, reason, _ = TP.allow_overlay({"style_id": "style_21", "kind": "sticker",
                                          "track_role": "sticker", "duration_sec": 1.2,
                                          "timeline_in_sec": 3.0, "at_cut_dist": 0.0,
                                          "has_text": False, "total_duration_sec": 15.6})
        self.assertFalse(ok)
        self.assertIn("花字", reason)

    def test_style_without_stickers_refuses(self):
        ok, reason, _ = TP.allow_overlay({"style_id": "style_01", "kind": "sticker",
                                          "track_role": "sticker", "duration_sec": 1.0,
                                          "at_cut_dist": 0.0, "has_text": True,
                                          "total_duration_sec": 30.0})
        self.assertFalse(ok)
        self.assertIn("贴纸", reason)

    def test_instant_effect_must_sit_on_cut(self):
        base = {"style_id": "style_21", "duration_sec": 0.167, "timeline_in_sec": 1.667,
                "total_duration_sec": 15.6, "track_role": "b_roll", "used_count": 0}
        ok, reason, _ = TP.allow_effect({**base, "at_cut_dist": 0.0})
        self.assertTrue(ok, reason)
        ok2, reason2, _ = TP.allow_effect({**base, "at_cut_dist": 0.8})
        self.assertFalse(ok2)
        self.assertIn("闪帧", reason2)

    def test_style_without_effects_refuses(self):
        ok, reason, _ = TP.allow_effect({"style_id": "style_01", "duration_sec": 2.0,
                                         "at_cut_dist": 0.0, "total_duration_sec": 30.0,
                                         "track_role": "b_roll"})
        self.assertFalse(ok)
        self.assertIn("画面特效", reason)

    def test_effect_longer_than_truth_cap_refused(self):
        ok, reason, _ = TP.allow_effect({"style_id": "style_21", "duration_sec": 8.0,
                                         "at_cut_dist": 0.0, "total_duration_sec": 15.6,
                                         "track_role": "b_roll"})
        self.assertFalse(ok)
        self.assertIn("上限", reason)


class PipelineTests(unittest.TestCase):
    def _pipe(self, td, visual_text="真人出镜 中景 口播"):
        p = Path(td)
        (p / "01_normalized").mkdir(parents=True, exist_ok=True)
        (p / "00_visual_index.json").write_text(json.dumps({
            "clips": [{"filename": "2026-10-22 151125.mov", "description": visual_text}]},
            ensure_ascii=False), encoding="utf-8")
        return p

    def _clip(self, tin, media="01_normalized/norm_02_2026-10-22 151125.mp4", face=True,
              cutout=True, sec="01_normalized/norm_20_other.mp4"):
        lay = {"mode": "geometry_pip_main_punch_in", "cutout": cutout,
               "main": {"x": 0.0, "y": 0.0, "scale": 1.2},
               "windows": [{"x": 0.0, "y": -0.199, "scale": 1.6, "matting": True,
                            "stroke_hex": "#FFFFFF"}],
               "face_anchor": _face() if face else None}
        if sec:
            lay["secondary_media_path"] = sec
            lay["secondary_in_sec"] = 0.0
        return {"clip_id": f"c{int(tin * 100)}", "track_role": "a_roll",
                "timeline_in_sec": tin, "duration_sec": 1.0,
                "media_path": media, "source_in_sec": tin, "source_out_sec": tin + 1.0,
                "sentence_text": "为什么同样是美业主理人", "pip_layout": lay}

    def test_cutout_gate_downgrades_design_sheet_shot(self):
        with tempfile.TemporaryDirectory() as td:
            pipe = self._pipe(td, visual_text="真人出镜 手上举着效果图 讲解")
            clips = [self._clip(0.0), self._clip(2.0)]
            stats = TP.apply_cutout_policy(clips, {"pip_composition_spec": {}}, pipe, "style_21")
            self.assertEqual(stats["candidates"], 2)
            self.assertEqual(stats["allowed"], 0)
            self.assertEqual(stats["dropped"], 2)
            for c in clips:
                self.assertFalse(c["pip_layout"]["cutout"])
                self.assertIn("效果图", c["pip_layout"]["cutout_gated_reason"])
            # 逐镜理由要能读给人听，并且带作用域
            self.assertTrue(all(r.get("reason") for r in stats["rows"]))
            self.assertTrue(all("scope" in r for r in stats["rows"]))

    def test_cutout_gate_allows_clean_face_shot(self):
        with tempfile.TemporaryDirectory() as td:
            pipe = self._pipe(td)
            clips = [self._clip(0.0)]
            stats = TP.apply_cutout_policy(clips, {"pip_composition_spec": {}}, pipe, "style_21")
            self.assertEqual(stats["allowed"], 1)
            self.assertTrue(clips[0]["pip_layout"]["cutout"])

    def test_speed_policy_only_touches_broll(self):
        clips = [
            {"clip_id": "a1", "track_role": "a_roll", "duration_sec": 2.0,
             "source_in_sec": 0.0, "source_out_sec": 2.0, "media_path": "/p/a.mp4"},
            {"clip_id": "b1", "track_role": "b_roll", "duration_sec": 2.0,
             "source_in_sec": 0.0, "source_out_sec": 2.0, "media_path": "/p/b.mp4"},
        ]
        stats = TP.apply_speed_policy(clips, None, Path("/tmp"), "style_24",
                                      {"items": [{"normalized_mp4": "/p/a.mp4", "duration_sec": 30.0},
                                                 {"normalized_mp4": "/p/b.mp4", "duration_sec": 30.0}]})
        self.assertEqual([c.get("speed") for c in clips], [None, 2.0])
        self.assertAlmostEqual(clips[1]["source_out_sec"], 4.0)
        self.assertEqual(stats["applied"], 1)
        self.assertTrue(all(r.get("reason") for r in stats["rows"]))

    def test_unknown_style_never_crashes(self):
        with tempfile.TemporaryDirectory() as td:
            pipe = self._pipe(td)
            clips = [self._clip(0.0)]
            stats = TP.apply_cutout_policy(clips, None, pipe, "style_99")
            self.assertEqual(stats["allowed"], 0)
            self.assertFalse(clips[0]["pip_layout"]["cutout"])


class ReplicaWiringTests(unittest.TestCase):
    """套用端（apply_replica_template）必须问同一套规则，不许自己写死阈值。"""

    def setUp(self):
        import apply_replica_template as ART
        self.ART = ART

    def test_speed_gate_uses_style_rules(self):
        ok, _r, _s = self.ART._tp_allow("speed", "style_21", {
            "track_role": "b_roll", "duration_sec": 2.0, "speed": 2.0,
            "source_headroom_sec": 8.0, "total_duration_sec": 15.6})
        self.assertFalse(ok)          # style_21 真值 0 段变速
        ok2, _r2, _s2 = self.ART._tp_allow("speed", "style_24", {
            "track_role": "b_roll", "duration_sec": 2.0, "speed": 2.0,
            "source_headroom_sec": 8.0, "total_duration_sec": 36.0})
        self.assertTrue(ok2)          # style_24 真值有 2×

    def test_effect_gate_uses_style_rules(self):
        ok, reason, _ = self.ART._tp_allow("effect", "style_01", {
            "track_role": "b_roll", "duration_sec": 2.0, "at_cut_dist": 0.0,
            "total_duration_sec": 30.0})
        self.assertFalse(ok)
        self.assertIn("画面特效", reason)
        ok2, _r2, _s2 = self.ART._tp_allow("effect", "style_21", {
            "track_role": "b_roll", "duration_sec": 0.167, "at_cut_dist": 0.0,
            "total_duration_sec": 15.6})
        self.assertTrue(ok2)

    def test_overlay_gate_uses_style_rules(self):
        ok, _r, _ = self.ART._tp_allow("overlay", "style_01", {
            "kind": "sticker", "track_role": "sticker", "duration_sec": 1.0,
            "at_cut_dist": 0.0, "has_text": True, "total_duration_sec": 30.0})
        self.assertFalse(ok)

    def test_transition_gate_defaults_to_hard_cut(self):
        ok, reason, _ = self.ART._tp_allow("transition", "style_21", {})
        self.assertFalse(ok)
        self.assertIn("硬切", reason)

    def test_unknown_style_is_marked_unverified(self):
        """规则里没有的 style_id：按模版真值执行，但必须标注「未做风格校验」。"""
        ok, reason, _ = self.ART._tp_allow("speed", "style_99", {
            "track_role": "b_roll", "duration_sec": 2.0, "speed": 2.0})
        self.assertTrue(ok)
        self.assertIn("未做风格校验", reason)

    def test_replica_source_actually_calls_the_gates(self):
        src = (ROOT / "short-video-studio" / "scripts" / "apply_replica_template.py").read_text(encoding="utf-8")
        for token in ('_tp_allow("speed"', '_tp_allow("overlay"', '_tp_allow("effect"',
                      '_tp_allow("transition"', '_cutout_layer_allowed'):
            self.assertIn(token, src)
        # 不许自己写死「某套风格的阈值」
        self.assertNotIn("款待", src)


if __name__ == "__main__":
    unittest.main()
