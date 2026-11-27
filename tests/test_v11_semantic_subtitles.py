#!/usr/bin/env python3
"""V11.0 语义分词引擎、大模型语义字幕直通、粤语展示字形治理回归单测。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "short-video-studio" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import normalize_display_glyphs
import step5_subtitles as s5
import style_truth_renderer as str_ren


class V11SemanticSubtitlesTests(unittest.TestCase):
    def test_biz_and_sto_dp_no_broken_words_or_hanging_particles(self):
        cases = [
            ("海底捞的师徒制核心不是师徒两个字是师傅能从徒弟的店利润里分到钱", ["师徒两个字", "徒弟的店", "利润里"]),
            ("我不给模板也不替你写就陪你一条条答一遍", ["一条条", "也不替你写", "就陪你"]),
            ("是我们想事情的方式被固化了", ["想事情"]),
            ("一个中空跟住有啲灯笼有啲造型岭南元素嘅", ["有啲灯笼", "有啲造型", "岭南元素"]),
            ("到时就有好多新鲜嘅海鲜喺𠮶度供应俾大家选择", ["新鲜嘅海鲜", "俾大家"]),
        ]
        for sentence, must_keep in cases:
            segs = s5.dp_segment_short_lines(sentence, max_width_px=640, font_size=60, max_chars=9)
            lines = [t for _, _, t in segs]
            joined = "".join(lines)
            self.assertEqual(joined, sentence)
            for phrase in must_keep:
                self.assertTrue(
                    any(phrase in ln for ln in lines),
                    f"短语「{phrase}」被劈碎: {lines}",
                )
            for ln in lines[:-1]:
                self.assertFalse(
                    ln.endswith(("有啲", "跟住", "好多", "也不替")),
                    f"禁止挂尾词出现在行尾: {lines}",
                )

    def test_split_semantic_two_lines_never_splits_numeral_units(self):
        l1, l2 = s5.split_semantic_two_lines("一间六百平嘅店")
        self.assertIn("六百平", l1 + "|" + l2)
        self.assertNotIn("六百|平", l1 + "|" + l2)
        self.assertFalse(l2.startswith("嘅"))

        a1, a2 = s5.split_semantic_two_lines("两百平嘅厨房")
        self.assertIn("两百平", a1 + "|" + a2)
        self.assertFalse(a2.startswith("嘅"))

        b1, b2 = s5.split_semantic_two_lines("是去别人那找答案")
        self.assertIn("别人那", b1 + "|" + b2)
        self.assertFalse(b2.startswith("那"))

    def test_tail_highlight_never_slices_words_into_fragments(self):
        entries = [
            {
                "id": "sub_001",
                "text": "我负债200多万",
                "words": [{"word": c, "start_sec": i * 0.1, "end_sec": (i + 1) * 0.1} for i, c in enumerate("我负债200多万")],
                "keywords": [],
                "suppressed": False,
            },
            {
                "id": "sub_002",
                "text": "也不是行情差",
                "words": [{"word": c, "start_sec": i * 0.1, "end_sec": (i + 1) * 0.1} for i, c in enumerate("也不是行情差")],
                "keywords": [],
                "suppressed": False,
            },
            {
                "id": "sub_003",
                "text": "我觉得非常之舒服",
                "words": [{"word": c, "start_sec": i * 0.1, "end_sec": (i + 1) * 0.1} for i, c in enumerate("我觉得非常之舒服")],
                "keywords": [],
                "suppressed": False,
            },
        ]
        s5._apply_phrase_end_highlights(entries, 1.0)
        for e in entries:
            for kw in e["keywords"]:
                self.assertNotIn(kw, {"0多万", "情差", "之舒服", "百平", "司名"})
        self.assertIn("200多万", entries[0]["keywords"])
        self.assertIn("行情差", entries[1]["keywords"])

    def test_semantic_subtitles_override_aligns_verbatim_timestamps(self):
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            raw_text = "核心不是师徒两个字是师傅能从徒弟的店利润里分到钱"
            words = [
                {"word": ch, "start_sec": round(i * 0.12, 4), "end_sec": round((i + 1) * 0.12, 4)}
                for i, ch in enumerate(raw_text)
            ]
            (pdir / "03_audio_events.json").write_text(
                json.dumps({
                    "sentences": [{
                        "id": "s001",
                        "text": raw_text,
                        "start_sec": 0.0,
                        "end_sec": round(len(raw_text) * 0.12, 4),
                        "words": words,
                    }]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (pdir / "05a_semantic_subtitles.json").write_text(
                json.dumps({
                    "segments": [
                        {"text": "核心不是师徒两个字", "keywords": ["师徒两个字"]},
                        {"text": "是师傅能从徒弟的店", "keywords": ["徒弟的店"]},
                        {"text": "利润里分到钱", "keywords": ["分到钱"]},
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            out = s5.run_step5(pdir, track="creator", template_id="style_01", enable_v6_director=False)
            ents = out["entries"]
            self.assertEqual([e["text"] for e in ents], ["核心不是师徒两个字", "是师傅能从徒弟的店", "利润里分到钱"])
            self.assertTrue(all(e.get("semantic_curated") for e in ents))
            self.assertLess(ents[0]["end_sec"], ents[1]["end_sec"])
            self.assertLess(ents[1]["end_sec"], ents[2]["end_sec"])

    def test_cantonese_display_glyph_normalization_and_panel_wrap(self):
        self.assertEqual(normalize_display_glyphs("𠮶边就系我哋嘅厨房"), "嗰边就系我哋嘅厨房")
        wrapped = str_ren._wrap_lines("一间六百平嘅店厨房你要花三分之一两百平去制造", max_chars_per_line=10, max_lines=4)
        self.assertTrue(any("三分之一" in ln for ln in wrapped), f"面板折行劈开了三分之一: {wrapped}")
        self.assertTrue(any("两百平" in ln for ln in wrapped), f"面板折行劈开了两百平: {wrapped}")

    def test_semantic_override_tracks_merged_sentence_ids_across_asr_fragments(self):
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            s1_txt = "一间六百平"
            s2_txt = "嘅店厨房"
            (pdir / "03_audio_events.json").write_text(
                json.dumps({
                    "sentences": [
                        {
                            "id": "sent_001", "text": s1_txt, "start_sec": 0.0, "end_sec": 0.6,
                            "words": [{"word": ch, "start_sec": round(i * 0.12, 4), "end_sec": round((i + 1) * 0.12, 4)} for i, ch in enumerate(s1_txt)],
                        },
                        {
                            "id": "sent_002", "text": s2_txt, "start_sec": 0.6, "end_sec": 1.08,
                            "words": [{"word": ch, "start_sec": round(0.6 + i * 0.12, 4), "end_sec": round(0.6 + (i + 1) * 0.12, 4)} for i, ch in enumerate(s2_txt)],
                        },
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (pdir / "05a_semantic_subtitles.json").write_text(
                json.dumps({"segments": [{"text": "一间六百平嘅店厨房", "keywords": ["六百平"]}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            out = s5.run_step5(pdir, track="store", template_id="style_09", enable_v6_director=False)
            ent = out["entries"][0]
            self.assertEqual(ent.get("merged_sentence_ids"), ["sent_001", "sent_002"])


if __name__ == "__main__":
    unittest.main()
