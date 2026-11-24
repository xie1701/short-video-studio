"""离麦插话（别人在几米外说话）判定与「只砍离麦且对不上成稿」口径的回归测试。

2026-11-24 用户实测：成片里「仲有好多人中意我哋嘅干炒蚝，同埋」是导演/摄影师的话，要去掉。
根因不是 ASR 听错，而是声源离麦太远（电平差 32dB）却被 ASR 听成了通顺的一句。
这些用例钉住三件事：
1. 只按电平能分出「谁在说话」（同一文件统一基准，别被插话拉低）；
2. 落差中等的（主讲人自己转头/弯腰）判成软档，不硬砍；
3. 真正下刀还要「够长 + 成稿里对不上」，不把主讲人的词切成两半。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import numpy as np  # noqa: E402

import off_mic  # noqa: E402
import script_align  # noqa: E402


def _words(chars, t0=0.0, step=0.18):
    out = []
    t = t0
    for ch in chars:
        out.append({"word": ch, "start_sec": round(t, 3), "end_sec": round(t + step, 3)})
        t += step
    return out


class OffMicDetectionTests(unittest.TestCase):
    def test_hard_vs_soft_by_drop(self):
        words = _words("一二三四五六七八")
        levels = [-18.0, -18.0, -40.0, -41.0, -18.0, -18.0, -27.0, -27.0]
        spans = off_mic.detect_off_mic(words, levels, ref_db=-18.0)
        hard = [s for s in spans if s["severity"] == "hard"]
        soft = [s for s in spans if s["severity"] == "soft"]
        self.assertEqual(len(hard), 1, spans)
        self.assertEqual(hard[0]["text"], "三四")
        self.assertEqual(len(soft), 1, spans)
        self.assertEqual(soft[0]["text"], "七八")

    def test_short_blip_is_not_a_span(self):
        """一两声碰撞（< MIN_RUN_SEC）不该被判成插话。"""
        words = _words("一二三四")
        levels = [-18.0, -40.0, -18.0, -18.0]
        spans = off_mic.detect_off_mic(words, levels, ref_db=-18.0)
        self.assertEqual(spans, [])

    def test_ref_not_dragged_down_by_interjection(self):
        """基准取 75 分位：插话占少数时不能把基准拉到它自己身上。"""
        levels = [-17.0, -18.0, -17.5, -18.5, -17.2, -40.0, -42.0]
        ref = off_mic.host_ref_db(levels)
        self.assertLess(ref, -16.5)
        self.assertGreater(ref, -20.0)

    def test_word_levels_reads_quiet_region(self):
        sr = 8000
        loud = 0.2 * np.ones(int(0.4 * sr), dtype="float32")
        quiet = 0.002 * np.ones(int(0.4 * sr), dtype="float32")
        audio = np.concatenate([loud, quiet])
        words = [
            {"word": "大", "start_sec": 0.0, "end_sec": 0.4},
            {"word": "声", "start_sec": 0.4, "end_sec": 0.8},
        ]
        levels = off_mic.word_levels(audio, sr, words)
        self.assertGreater(levels[0] - levels[1], 25.0)

    def test_annotate_segments_uses_per_segment_words(self):
        """同一源文件被切成多段时，每段都要拿到自己的离麦段（第一版按文件缓存整份结论，全空）。"""
        sr = 8000
        audio = np.concatenate([
            0.2 * np.ones(int(0.3 * sr), dtype="float32"),   # 段 1：主讲人，0.0–0.3
            0.002 * np.ones(int(1.2 * sr), dtype="float32"),  # 段 2：插话，0.3–1.5
        ])
        wav = Path("/tmp/_off_mic_test.wav")
        import wave

        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((audio * 32767).astype(np.int16).tobytes())

        seg1 = {"source_audio": str(wav), "start_sec": 0.0, "end_sec": 0.3,
                "words": [{"word": "我", "start_sec": 0.02, "end_sec": 0.28}]}
        seg2 = {"source_audio": str(wav), "start_sec": 0.4, "end_sec": 1.4,
                "words": [{"word": c, "start_sec": 0.4 + i * 0.2, "end_sec": 0.6 + i * 0.2}
                          for i, c in enumerate("干炒蚝同埋")]}
        # 现场真实形状：主讲人在这一个文件里说了很多字，插话只有一小段
        seg1["words"] = [
            {"word": c, "start_sec": i * 0.05, "end_sec": (i + 1) * 0.05}
            for i, c in enumerate("我系牛哥我花")
        ]
        res = off_mic.annotate_segments([seg1, seg2])
        self.assertFalse(seg1.get("off_mic_spans"), "主讲人那段不该被标")
        self.assertTrue(seg2.get("off_mic_spans"), "插话那段必须被标")
        self.assertGreaterEqual(res["sec"], 0.8)
        wav.unlink(missing_ok=True)


class OffMicAlignmentTests(unittest.TestCase):
    def test_blocks_off_mic_chars_not_in_script(self):
        """成稿里没有的离麦话：必须被砍，且砍出来的两段都要保住。"""
        text = "四大天王叉烧包招牌炒面好食"
        words = _words("四大天王叉烧包干炒蚝我同或系咩招牌炒面好食")
        span = {
            "start_sec": 1.08, "end_sec": 2.52, "dur_sec": 1.44, "drop_db": 32.4,
            "severity": "hard", "words": ["干", "炒", "蚝", "我", "同", "或", "系", "咩"],
            "text": "干炒蚝我同或系咩",
        }
        r = script_align.keep_intervals(text, words, off_mic_spans=[span])
        joined = "".join(iv["text"] for iv in r["intervals"])
        self.assertNotIn("干炒蚝", joined)
        self.assertIn("四大天王叉烧包", joined)
        self.assertIn("招牌炒面好食", joined)
        self.assertGreater(r.get("off_mic_sec") or 0.0, 1.0)

    def test_matching_off_mic_is_not_cut(self):
        """电平掉 20dB 但说的就是成稿里的字（主讲人转头/离麦远一步）——不许砍。"""
        text = "四大天王叉烧包招牌炒面好食"
        words = _words("四大天王叉烧包招牌炒面好食")
        span = {
            "start_sec": 0.9, "end_sec": 2.5, "dur_sec": 1.6, "drop_db": 22.0,
            "severity": "hard", "words": ["招", "牌", "炒", "面", "好", "食"],
            "text": "招牌炒面好食",
        }
        r = script_align.keep_intervals(text, words, off_mic_spans=[span])
        joined = "".join(iv["text"] for iv in r["intervals"])
        self.assertIn("招牌炒面好食", joined)
        self.assertEqual(r.get("off_mic_sec"), 0.0)

    def test_short_off_mic_run_is_left_to_normal_logic(self):
        """不到下刀长度（0.8s）的离麦段不参与硬砍。"""
        text = "四大天王叉烧包招牌炒面"
        words = _words("四大天王叉烧包诶招牌炒面")
        span = {"start_sec": 0.54, "end_sec": 0.9, "dur_sec": 0.36, "drop_db": 24.0,
                "severity": "hard", "words": ["诶"], "text": "诶"}
        r = script_align.keep_intervals(text, words, off_mic_spans=[span])
        self.assertEqual(r.get("off_mic_sec"), 0.0)


class RealCaseShapeTests(unittest.TestCase):
    """用现场那一行的真实形状复现：这段判定必须能砍掉「干炒蚝」那一串。"""

    def test_ouyang_line5_shape(self):
        plan = "我哋嘅四大天王：虾饺、排骨、凤爪同埋叉烧包。招牌特色炒面，绝对食过嘅话都会觉得非常之好食。"
        words = _words(
            "我哋嘅四大天王虾饺排骨凤爪同埋叉烧包仲有好多人中意嘅干炒蚝我同或系咩还有好多人中意我哋嘅招牌特色炒面绝对食过嘅话都会觉得非常之好食"
        )
        # 真实落点（相对本段起点）：23.28–24.96 与 26.16–27.54
        spans = [
            {"start_sec": 23.28, "end_sec": 24.96, "dur_sec": 1.68, "drop_db": 26.8,
             "severity": "hard", "words": list("仲有你仲有好人中意嘅"), "text": "仲有你仲有好人中意嘅"},
            {"start_sec": 26.16, "end_sec": 27.54, "dur_sec": 1.38, "drop_db": 32.4,
             "severity": "hard", "words": ["干", "炒", "蚝", "我", "同", "或", "系", "咩"],
             "text": "干炒蚝我同或系咩"},
        ]
        r = script_align.keep_intervals(plan, words, off_mic_spans=spans)
        joined = "".join(iv["text"] for iv in r["intervals"])
        self.assertNotIn("干炒蚝", joined)
        self.assertIn("四大天王", joined)
        self.assertIn("招牌特色炒面", joined)


if __name__ == "__main__":
    unittest.main()
