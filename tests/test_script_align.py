#!/usr/bin/env python3
"""逐字对齐切段（`script_align`）的回归测试。

这些用例全部来自 2026-11-24 的真实事故：成片里混进了打板口令与重来的半句，
用户原话「就按确认的口播文案，其它的就要毫不留情的剪掉」。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "short-video-studio" / "scripts"))

import script_align as SA  # noqa: E402


def _words(spec):
    """`[(字, 起, 止), ...]` → step2 口径的词表。"""
    return [
        {"word": ch, "start_sec": s, "end_sec": e, "is_keyword": False, "is_accent": False, "energy": 0.7}
        for ch, s, e in spec
    ]


def _grid(text, start=0.0, step=0.20, gap_before=None, gap_after=None):
    """把一串字按固定节拍铺成 (字, 起, 止)。"""
    out = []
    t = start
    for i, ch in enumerate(text):
        if gap_before and i in gap_before:
            t += gap_before[i]
        out.append((ch, round(t, 3), round(t + step, 3)))
        t += step
        if gap_after and i in gap_after:
            t += gap_after[i]
    return out


class AlignTests(unittest.TestCase):
    def test_leading_cue_is_trimmed(self):
        """段首的打板口令（「一组」）必须被砍掉。"""
        spec = _grid("一组") + _grid("我系牛哥", start=1.2)
        r = SA.keep_intervals("我系牛哥", _words(spec))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["intervals"]), 1)
        self.assertGreaterEqual(r["intervals"][0]["start"], 1.0, "口令仍在保留区间里")
        self.assertEqual(r["intervals"][0]["text"], "我系牛哥")
        self.assertGreater(r["head_trim_sec"], 0.8)

    def test_long_foreign_speech_in_middle_is_cut(self):
        """句中夹着整句重来的话（>1s）要切掉，成稿文字不能缺字。"""
        spec = _grid("厨房") + _grid("或者系咩呢再讲一次", start=2.0, step=0.25) + _grid("两百平", start=6.0)
        r = SA.keep_intervals("厨房两百平", _words(spec))
        self.assertTrue(r["ok"])
        kept = "".join(iv["text"] for iv in r["intervals"])
        self.assertEqual(kept, "厨房两百平")
        self.assertEqual(len(r["intervals"]), 2, "中间那句重来的话没切开")
        # 切掉的那段就是中间那句
        self.assertTrue(any("再讲" in (c.get("speech") or "") for c in r["cut_notes"]))

    def test_short_filler_is_kept(self):
        """0.4 秒的「诶」是他自己在喘气，切了像卡带，不能切。"""
        spec = _grid("厨房") + _grid("诶", start=2.0, step=0.4) + _grid("两百平", start=2.6)
        r = SA.keep_intervals("厨房两百平", _words(spec))
        self.assertEqual(len(r["intervals"]), 1, "口癖不该把一句话切碎")

    def test_natural_pause_is_not_a_cut(self):
        """中间只是停顿（没有别的字），一律不断开。"""
        spec = _grid("点赞茶楼", start=0.0, step=0.2, gap_after={3: 1.4})
        r = SA.keep_intervals("点赞茶楼", _words(spec))
        self.assertEqual(len(r["intervals"]), 1)

    def test_low_coverage_only_trims_head_and_tail(self):
        """成稿是压缩改写过的（覆盖率低）时不敢动句中，只砍段首段尾。"""
        # 成稿比原声多了半句（压缩时不改字、只影响覆盖率），覆盖不上「都系纯手工」
        spec = _grid("一组") + _grid("厨房啦几多嘅厨房两百平", start=1.5)
        r = SA.keep_intervals("厨房两百平都系纯手工", _words(spec))
        self.assertTrue(r["low_coverage"])
        self.assertEqual(len(r["intervals"]), 1, "低覆盖时不该动句中")
        self.assertGreaterEqual(r["intervals"][0]["start"], 1.3)


    def test_stutter_repeat_is_dropped_on_low_coverage(self):
        """低覆盖行里的绊嘴（「厨房啦几多嘅厨房」）只留后一遍，字幕不许印「厨房嘅厨房」。"""
        spec = _grid("厨房") + _grid("啦几多嘅", start=1.0, step=0.2) + _grid("厨房两百平", start=2.0)
        # 成稿比原声多出半句，覆盖率天然偏低（这就是压缩改写过的行）
        r = SA.keep_intervals("厨房两百平都系手工", _words(spec))
        kept = "".join(iv["text"] for iv in r["intervals"])
        self.assertEqual(kept.count("厨房"), 1, f"绊嘴没去掉：{kept}")

    def test_legit_repeat_kept_on_high_coverage(self):
        """高覆盖行里「有啲灯笼、有啲造型」是真内容，不许当成绊嘴删掉。"""
        spec = _grid("有啲灯笼") + _grid("有啲造型", start=1.0)
        r = SA.keep_intervals("有啲灯笼有啲造型", _words(spec))
        kept = "".join(iv["text"] for iv in r["intervals"])
        self.assertEqual(kept, "有啲灯笼有啲造型")

    def test_alignment_failure_is_reported(self):
        """一个都对不上时必须明确报 ok=False，不能假装切好了。"""
        r = SA.keep_intervals("完全无关的一句话", _words(_grid("另一回事")))
        self.assertFalse(r["ok"])
        self.assertEqual(r["intervals"], [])

    def test_homophone_tolerated(self):
        """同音字（ASR 写错）要能对上，否则整句会被判成对不上。"""
        spec = _grid("干炒壕")
        r = SA.keep_intervals("干炒蚝", _words(spec))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["intervals"]), 1)

    def test_intervals_never_overlap(self):
        """切出来的区间不许互相压住，否则音频会重复一段。"""
        spec = _grid("甲乙丙丁", step=0.3)
        r = SA.keep_intervals("甲乙丙丁", _words(spec))
        for a, b in zip(r["intervals"], r["intervals"][1:]):
            self.assertLessEqual(a["end"], b["start"] + 1e-9)


class ForeignSpeechCutTests(unittest.TestCase):
    """别人插话的切法（2026-11-24 第二次实测：16–19 秒、35–39 秒还有导演/摄影师的声音）。

    旧口径两条都太宽容：句中夹着的碎话要 1.0 秒以上才切；成稿覆盖率低的行（被压缩改写过的）
    干脆「只砍段首段尾」，于是整段别人的话原样留在片子里。
    """

    def test_short_foreign_speech_is_cut(self):
        """0.4 秒的实义插话（不是语气词）也要切掉——导演提词经常只有两三个字。"""
        spec = _grid("甲乙") + _grid("丙丁", start=0.4 + 0.45, step=0.15) + _grid("戊己", start=1.5)
        r = SA.keep_intervals("甲乙戊己", _words(spec))
        kept = "".join(iv["text"] for iv in r["intervals"])
        self.assertEqual(kept, "甲乙戊己")
        self.assertGreaterEqual(len(r["intervals"]), 2)


    def test_pure_filler_inside_is_kept(self):
        """夹在中间的纯语气词（「咧」「啊」）留着更像人说话，不许切。"""
        spec = _grid("甲乙") + _grid("咧", start=0.37, step=0.30) + _grid("戊己", start=0.55 + 0.45)
        r = SA.keep_intervals("甲乙戊己", _words(spec))
        self.assertEqual(len(r["intervals"]), 1)


    def test_long_filler_inside_is_cut(self):
        """一秒钟以上的语气词也是碎话，留在当中一定抢戏。"""
        spec = _grid("甲乙") + _grid("诶", start=0.37, step=1.40) + _grid("戊己", start=1.77 + 0.60)
        r = SA.keep_intervals("甲乙戊己", _words(spec))
        self.assertGreaterEqual(len(r["intervals"]), 2)


    def test_low_coverage_line_still_cuts_inner_junk(self):
        """成稿只对上 60% 的行，句中的别人的话照样要切（旧口径在这里整段保留）。"""
        spec = _grid("厨房") + _grid("啦几多嘅", start=0.45) + _grid("厨房两百平", start=1.90)
        # 成稿改写过的行：句尾「手工现做」素材里根本没有，覆盖率必然低
        r = SA.keep_intervals("厨房厨房两百平手工现做", _words(spec))
        self.assertTrue(r["low_coverage"])
        for iv in r["intervals"]:
            self.assertNotIn("啦几多", iv["text"])
        self.assertGreaterEqual(len(r["intervals"]), 2)


class DeadAirTests(unittest.TestCase):
    """一句话中间的长静音（冷场）要剪掉（2026-11-24 实测：32.0–33.0s 全场 0.90s 没声）。"""

    def test_inner_silence_is_split(self):
        # 「甲乙丙丁」四个字之间空了 0.9 秒：没人说话，不是别人的插话
        spec = _grid("甲乙") + _grid("丙丁", start=1.30)
        r = SA.keep_intervals("甲乙丙丁", _words(spec))
        self.assertGreaterEqual(len(r["intervals"]), 2)
        cut = [n for n in r["cut_notes"] if n.get("kind") == "silence"]
        self.assertTrue(cut, "应记一条 silence 切点")
        self.assertEqual(cut[0]["action"], "cut")

    def test_short_inner_silence_is_kept(self):
        # 0.35 秒的呼吸停顿是说话本身的节奏，不许切
        spec = _grid("甲乙") + _grid("丙丁", start=0.75)
        r = SA.keep_intervals("甲乙丙丁", _words(spec))
        self.assertEqual(len(r["intervals"]), 1)


class Step3WiringTests(unittest.TestCase):
    """源码级防回退：step3 必须真的调用逐字对齐，而不是又退回整段保留。"""

    def test_step3_calls_script_align(self):
        src = (Path(__file__).resolve().parent.parent / "short-video-studio" / "scripts" / "step3_lock_audio.py").read_text("utf-8")
        self.assertIn("import script_align", src)
        self.assertIn("keep_intervals", src)
        self.assertIn("plan_split", src)


if __name__ == "__main__":
    unittest.main()
