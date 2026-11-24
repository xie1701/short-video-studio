#!/usr/bin/env python3
"""花字策略：什么时候才允许上屏。

旧 step6 花字是倒挂的——拿音效 cue 当花字锚点，于是「有音效就有花字」。这里锁死：
  1. 文案必须有出处（数字/章节/关键词/整句回声），溯不到出处就不上屏；
  2. 半截子文案（"我哋嘅四大"、"…嘅"）不许上屏；
  3. 纯静音段不上屏、同屏不叠字、最小间隔与每分钟上限生效；
  4. 触发归类正确。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

import flower_policy as FP  # noqa: E402

RULES = ROOT / "forensic" / "style_trigger_rules_sfx_flower.json"


def _ctx(**kw):
    ctx = {
        "duration_sec": 40.0,
        "cut_times": [],
        "line_starts": [],
        "sentences": [{"start_sec": 0.0, "end_sec": 40.0, "text": "整段话"}],
    }
    ctx.update(kw)
    return ctx


class TriggerDetectTests(unittest.TestCase):
    """触发归类：优先级 数字 > 章节 > 整句回声 > 关键词 > 切点。"""

    def test_number(self):
        got = FP.detect_trigger("三百万", sentence_text="花咗三百万喺金湾开")
        self.assertEqual(got["trigger"], "number")
        self.assertEqual(got["evidence_text"], "三百万")

    def test_number_rejects_measure_word_trap(self):
        """「四大天王」不是数字：宽松量词表会误判成「四大天」，这里必须不认。"""
        got = FP.detect_trigger("四大天王", sentence_text="我哋嘅四大天王")
        self.assertNotEqual(got["trigger"], "number")

    def test_chapter_section_marker(self):
        self.assertEqual(FP.detect_trigger("二、低风险高收入", sentence_text="")["trigger"], "chapter")

    def test_chapter_long_turn(self):
        got = FP.detect_trigger("简单便捷", sentence_text="这一段说简单便捷", is_long_turn=True)
        self.assertEqual(got["trigger"], "chapter")

    def test_line_echo(self):
        got = FP.detect_trigger("绝对品质", sentence_text="绝对品质")
        self.assertEqual(got["trigger"], "line_echo")

    def test_keyword(self):
        got = FP.detect_trigger("招牌特色", sentence_text="招牌特色炒面食过嘅话")
        self.assertEqual(got["trigger"], "keyword")

    def test_no_trigger_when_no_source(self):
        got = FP.detect_trigger("胡说八道", sentence_text="今天天气很好")
        self.assertIsNone(got["trigger"])

    def test_cut_only_when_at_cut(self):
        got = FP.detect_trigger("完全对不上", sentence_text="今天天气很好", at_cut=True)
        self.assertEqual(got["trigger"], "cut")


class TextQualityTests(unittest.TestCase):
    def test_filler_head(self):
        self.assertTrue(FP.text_quality_issue("我哋嘅四大"))

    def test_dangling_tail(self):
        self.assertTrue(FP.text_quality_issue("两百平嘅"))

    def test_ok(self):
        self.assertEqual(FP.text_quality_issue("绝对品质"), "")


class FlowerGateTests(unittest.TestCase):
    def setUp(self):
        self.pol = FP.FlowerPolicy("style_21", RULES)
        self.assertTrue(self.pol.found)

    def _eval(self, cands, ctx):
        return self.pol.evaluate(cands, ctx)

    def test_no_source_is_dropped(self):
        """文案在同期人声里溯不到出处，也没有数字/章节 → 不许上屏。"""
        res = self._eval([{"text": "胡说八道", "start_sec": 5.0, "sentence_text": "今天天气很好"}],
                         _ctx(sentences=[{"start_sec": 4.0, "end_sec": 8.0, "text": "今天天气很好"}]))
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "no_trigger")

    def test_number_accepted(self):
        res = self._eval([{"text": "三百万", "start_sec": 5.0,
                           "sentence_text": "花咗三百万喺金湾开", "sentence_id": "s1"}],
                         _ctx(sentences=[{"start_sec": 4.0, "end_sec": 8.0, "text": "花咗三百万喺金湾开"}]))
        self.assertEqual(res["n_accepted"], 1)
        cue = res["accepted"][0]
        self.assertEqual(cue["trigger"], "number")
        self.assertEqual(cue["evidence_text"], "三百万")
        self.assertIn("数字", cue["why"])
        self.assertGreaterEqual(cue["hold_sec"], 0.8)

    def test_broken_phrase_dropped(self):
        res = self._eval([{"text": "我哋嘅四大", "start_sec": 5.0, "sentence_text": "我哋嘅四大天王"}],
                         _ctx(sentences=[{"start_sec": 4.0, "end_sec": 8.0, "text": "我哋嘅四大天王"}]))
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "broken_phrase")

    def test_too_short_dropped(self):
        res = self._eval([{"text": "𠮶", "start_sec": 5.0, "sentence_text": "𠮶边就系我哋"}],
                         _ctx(sentences=[{"start_sec": 4.0, "end_sec": 8.0, "text": "𠮶边就系我哋"}]))
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "text_too_short")

    def test_silence_ban(self):
        res = self._eval([{"text": "三百万", "start_sec": 20.0, "sentence_text": "花咗三百万"}],
                         _ctx(sentences=[{"start_sec": 0.0, "end_sec": 10.0, "text": "花咗三百万"},
                                         {"start_sec": 30.0, "end_sec": 40.0, "text": "收尾"}]))
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "silence_ban")

    def test_min_gap(self):
        gap = self.pol.min_gap
        c1 = {"text": "三百万", "start_sec": 5.0, "sentence_text": "三百万"}
        c2 = {"text": "六百平", "start_sec": 5.0 + gap * 0.5, "sentence_text": "六百平"}
        res = self._eval([c1, c2], _ctx())
        self.assertEqual(res["n_accepted"], 1)
        self.assertIn(res["dropped"][0]["dropped_because"], ("min_gap", "overlap_previous"))

    def test_overlap_previous(self):
        """上一张还没下屏就上下一张，同屏两张字会打架。"""
        c1 = {"text": "三百万", "start_sec": 5.0, "sentence_text": "三百万", "dur_sec": 3.5}
        c2 = {"text": "六百平", "start_sec": 6.0, "sentence_text": "六百平", "dur_sec": 3.5}
        res = self._eval([c1, c2], _ctx())
        self.assertEqual(res["n_accepted"], 1)

    def test_chars_too_long(self):
        long_text = "招牌特色炒面食过嘅话都会觉得非常之好食"   # 20 字，超过 style_21 的 5+6
        res = self._eval([{"text": long_text, "start_sec": 5.0, "sentence_text": long_text}], _ctx())
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "text_too_long")

    def test_trigger_not_in_style(self):
        """style_21 源草稿的花字没有整句回声，所以 line_echo 不上屏。"""
        self.assertNotIn("line_echo", self.pol.allowed_triggers)
        res = self._eval([{"text": "干炒蚝同埋", "start_sec": 5.0, "sentence_text": "干炒蚝同埋"}],
                         _ctx(sentences=[{"start_sec": 4.0, "end_sec": 8.0, "text": "干炒蚝同埋"}]))
        self.assertEqual(res["n_accepted"], 0)
        self.assertEqual(res["dropped"][0]["dropped_because"], "trigger_not_in_style")

    def test_number_always_allowed(self):
        """数字是内容自证触发：即使整套风格一次都没用过数字花字也允许。"""
        self.assertIn("number", self.pol.always_allowed)

    def test_table_is_readable(self):
        res = self._eval([{"text": "三百万", "start_sec": 5.0, "sentence_text": "三百万"},
                          {"text": "胡说八道", "start_sec": 30.0}], _ctx())
        text = FP.format_table(res)
        self.assertIn("花字依据表", text)
        self.assertIn("数字+量词", text)
        self.assertIn("no_trigger", text)


class FlowerRuleCoverageTests(unittest.TestCase):
    def test_all_styles_have_flower_rules(self):
        import json

        table = json.loads(RULES.read_text(encoding="utf-8"))
        self.assertEqual(len(table["styles"]), 24)
        for sid, node in table["styles"].items():
            f = node["flower"]
            for key in ("trigger_mix", "allowed_triggers", "max_per_min", "chars_min", "chars_max",
                        "hold_sec_min", "hold_sec_max", "scope", "evidence"):
                self.assertIn(key, f, f"{sid}.{key}")


if __name__ == "__main__":
    unittest.main()
