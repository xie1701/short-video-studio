#!/usr/bin/env python3
"""音效策略：每条音效都必须挂到可观测锚点上，挂不上就不出。

锁住的是用户 2026-11-24 的实测反馈：「音效过于密集，感觉是没有任何缘由就出现」。
旧 step6 只按密度把音效铺在每一个切点上（style_21 41 条），这里用测试钉死五件事：
  1. 挂不上锚点的 cue 一律被拒（no_anchor）；
  2. `stress` 这类口播中段微切不算切点理由（non_structural_cut / no_anchor）；
  3. 纯静音段不响（silence_ban）；
  4. 最小间隔生效（global_min_gap / role_min_gap）；
  5. 锚点归类正确（cut / flower / line / beat / free）。
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

import sfx_policy as SP  # noqa: E402
from trigger_policy_common import classify_sfx_anchor  # noqa: E402

RULES = ROOT / "forensic" / "style_trigger_rules_sfx_flower.json"


def _ctx(**kw):
    ctx = {
        "duration_sec": 20.0,
        "cut_events": [],
        "flower_starts": [],
        "flower_ends": [],
        "line_starts": [],
        "beats": [],
        "sentences": [{"start_sec": 0.0, "end_sec": 20.0, "text": "整段话"}],
    }
    ctx.update(kw)
    return ctx


class AnchorClassifyTests(unittest.TestCase):
    """锚点归类：一条 cue 只能有一个理由，且按 cut > flower > line > beat 取优先。"""

    def setUp(self):
        self.anchors = {"cut": [1.0, 5.0], "flower": [2.0], "line": [3.0], "beat": [4.0]}

    def test_cut(self):
        got = classify_sfx_anchor(1.03, self.anchors)
        self.assertEqual(got["reason"], "cut")
        self.assertAlmostEqual(got["anchor_time"], 1.0, places=3)

    def test_flower(self):
        self.assertEqual(classify_sfx_anchor(2.05, self.anchors)["reason"], "flower")

    def test_line(self):
        self.assertEqual(classify_sfx_anchor(3.05, self.anchors)["reason"], "line")

    def test_beat(self):
        self.assertEqual(classify_sfx_anchor(4.03, self.anchors)["reason"], "beat")

    def test_free(self):
        self.assertIsNone(classify_sfx_anchor(7.5, self.anchors)["reason"])

    def test_tolerance_rejects_far(self):
        # 偏移 0.30s 已经超过 0.12s 容差：不许硬说它贴切点
        self.assertIsNone(classify_sfx_anchor(1.30, self.anchors)["reason"])

    def test_cut_wins_over_flower_at_same_time(self):
        anchors = {"cut": [1.0], "flower": [1.01], "line": [], "beat": []}
        self.assertEqual(classify_sfx_anchor(1.0, anchors)["reason"], "cut")


class SfxGateTests(unittest.TestCase):
    def setUp(self):
        self.pol = SP.SfxPolicy("style_21", RULES)
        self.assertTrue(self.pol.found, "规则表必须覆盖 style_21")
        self.assertIn("cut", self.pol.allowed_triggers)

    def _evaluate(self, cands, ctx):
        return self.pol.evaluate(cands, ctx)

    def test_no_anchor_is_dropped(self):
        res = self._evaluate([{"time_sec": 7.77}], _ctx())
        self.assertEqual(res["accepted"], [])
        self.assertEqual(res["dropped"][0]["dropped_because"], "no_anchor")

    def test_structural_cut_is_kept(self):
        ctx = _ctx(cut_events=[{"time_sec": 1.0, "type": "sentence_start", "shot_type": "a_roll"}])
        res = self._evaluate([{"time_sec": 1.0, "origin": "cut"}], ctx)
        self.assertEqual(res["n_accepted"], 1)
        cue = res["accepted"][0]
        self.assertEqual(cue["reason"], "cut")
        self.assertIn("源草稿触发=cut", cue["policy"])
        self.assertAlmostEqual(cue["anchor_time"], 1.0, places=3)

    def test_stress_micro_cut_has_no_reason(self):
        """口播中段的重音微切不构成"切点理由"——这是用户听到的无理由一声。"""
        ctx = _ctx(cut_events=[{"time_sec": 1.0, "type": "stress", "shot_type": "a_roll"}],
                   sentences=[{"start_sec": 0.0, "end_sec": 2.0, "text": "口播中段"}])
        res = self._evaluate([{"time_sec": 1.0, "event_type": "stress", "origin": "cut"}], ctx)
        self.assertEqual(res["n_accepted"], 0)
        self.assertIn(res["dropped"][0]["dropped_because"], ("no_anchor", "non_structural_cut"))

    def test_bgm_beat_micro_cut_has_no_reason(self):
        ctx = _ctx(cut_events=[{"time_sec": 1.0, "type": "bgm_beat", "shot_type": "a_roll"}],
                   sentences=[{"start_sec": 0.0, "end_sec": 2.0, "text": "口播中段"}])
        res = self._evaluate([{"time_sec": 1.0, "event_type": "bgm_beat"}], ctx)
        self.assertEqual(res["n_accepted"], 0)

    def test_silence_window_banned(self):
        """纯静音段（人声之间 2.0~6.0s 的空白）里即使有切点也不许响。"""
        ctx = _ctx(cut_events=[{"time_sec": 4.0, "type": "sentence_start"}],
                   sentences=[{"start_sec": 0.0, "end_sec": 2.0, "text": "前半句"},
                              {"start_sec": 6.0, "end_sec": 10.0, "text": "后半句"}])
        res = self._evaluate([{"time_sec": 4.0}], ctx)
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "silence_ban")

    def test_midline_banned(self):
        """句中人声中间（离句首 1.5s、不贴任何锚点）不许插一声。"""
        ctx = _ctx(cut_events=[{"time_sec": 0.5, "type": "sentence_start"}],
                   sentences=[{"start_sec": 0.0, "end_sec": 3.0, "text": "一整句话"}])
        res = self._evaluate([{"time_sec": 1.5}], ctx)
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "no_anchor")

    def test_min_gap_enforced(self):
        ctx = _ctx(cut_events=[{"time_sec": 1.0, "type": "sentence_start"},
                               {"time_sec": 1.2, "type": "sentence_start"}])
        res = self._evaluate([{"time_sec": 1.0}, {"time_sec": 1.2}], ctx)
        self.assertEqual(res["n_accepted"], 1)
        self.assertEqual(res["dropped"][0]["dropped_because"], "global_min_gap")

    def test_role_min_gap_enforced(self):
        """同角色不许连击：间隔小于角色最小间隔就拒（即使过了全局间隔）。"""
        role_gap = SP._role_gap_floor(self.pol.rules, "info")
        self.assertGreater(role_gap, self.pol.min_gap)
        t1, t2 = 1.0, 1.0 + self.pol.min_gap + 0.05
        self.assertLess(t2 - t1, role_gap)
        ctx = _ctx(cut_events=[{"time_sec": t1, "type": "sentence_start"},
                               {"time_sec": t2, "type": "sentence_start"}])
        res = self._evaluate([{"time_sec": t1, "role": "info"}, {"time_sec": t2, "role": "info"}], ctx)
        self.assertEqual(res["n_accepted"], 1)
        self.assertEqual(res["dropped"][0]["dropped_because"], "role_min_gap")

    def test_trigger_not_in_style(self):
        """style_21 源草稿没有拍点音效：beat 候选一律拒（哪怕确实踩在拍上）。"""
        self.assertNotIn("beat", self.pol.allowed_triggers)
        ctx = _ctx(beats=[1.0, 2.0, 3.0], cut_events=[])
        res = self._evaluate([{"time_sec": 2.0}], ctx)
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "trigger_not_in_style")

    def test_shot_type_scope(self):
        ctx = _ctx(cut_events=[{"time_sec": 1.0, "type": "sentence_start", "shot_type": "code_broll"}])
        res = self._evaluate([{"time_sec": 1.0, "shot_type": "screen_recording"}], ctx)
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "shot_type_scope")

    def test_flower_anchor_kept(self):
        ctx = _ctx(flower_starts=[2.0], flower_ends=[4.0],
                   sentences=[{"start_sec": 1.5, "end_sec": 5.0, "text": "口播"}])
        res = self._evaluate([{"time_sec": 2.0}], ctx)
        self.assertEqual(res["n_accepted"], 1)
        self.assertEqual(res["accepted"][0]["reason"], "flower")

    def test_reason_and_anchor_fields_present(self):
        ctx = _ctx(cut_events=[{"time_sec": 1.0, "type": "sentence_start", "event_id": "evt_1"}])
        res = self._evaluate([{"time_sec": 1.0, "event_id": "evt_1"}], ctx)
        cue = res["accepted"][0]
        for key in ("reason", "anchor_time", "policy", "role"):
            self.assertIn(key, cue)
        self.assertEqual(cue["event_id"], "evt_1")

    def test_table_is_readable(self):
        ctx = _ctx(cut_events=[{"time_sec": 1.0, "type": "sentence_start"}])
        res = self._evaluate([{"time_sec": 1.0}, {"time_sec": 9.9}], ctx)
        text = SP.format_table(res)
        self.assertIn("音效依据表", text)
        self.assertIn("贴结构性切点", text)
        self.assertIn("no_anchor", text)


class RuleTableCoverageTests(unittest.TestCase):
    """24 套都要有规则，不许只在 style_21 上成立。"""

    def test_all_24_styles_present(self):
        table = json.loads(RULES.read_text(encoding="utf-8"))
        styles = table["styles"]
        self.assertEqual(len(styles), 24)
        for sid, node in styles.items():
            self.assertIn("sfx", node, sid)
            self.assertIn("flower", node, sid)
            self.assertIn("trigger_mix", node["sfx"], sid)
            self.assertIn("allowed_triggers", node["sfx"], sid)
            self.assertIn("min_gap_sec", node["sfx"], sid)
            self.assertIn("scope", node["sfx"], sid)
            self.assertIn("evidence", node["sfx"], sid)
            self.assertIn("scope", node["flower"], sid)

    def test_sfx_hit_rate_high_across_styles(self):
        """规则必须能解释源草稿：24 套音效可归因率中位数应 ≥0.95。"""
        table = json.loads(RULES.read_text(encoding="utf-8"))
        rates = [v["sfx"]["evidence"]["hit_rate"] for v in table["styles"].values()]
        rates.sort()
        median = rates[len(rates) // 2]
        self.assertGreaterEqual(median, 0.95)
        self.assertGreaterEqual(min(rates), 0.80)


if __name__ == "__main__":
    unittest.main()
