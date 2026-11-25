"""T006：step6 接入 style_03/17 视觉 DNA 与 style_01 三拍开场的端到端回归。

四组验收：
1. style_03：引语卡顶部锚点、通宽深红底条安全居中，并真实渲染出深墨顶栏卡与深红底条；
2. style_17：对比语义写入 compare_mode / dual_gold_hairline_grid 并渲染暖金细线，单镜保持极净无框；
3. style_01：开场 store_poster_burst 注入 3 拍斜标节拍（文案不重样、-5°），原有字段一个不少；
4. 跨风格隔离：style_02 动能舞台不沾 compare_mode / opening_beats，音效无 ding/chime，源码零本机路径。
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

from common import SAFE_ZONE, check_bbox_in_safe_zone, write_json  # noqa: E402
import render_template_overlays as overlays  # noqa: E402
import step6_packaging as S6  # noqa: E402

FPS = 30
GOLD_LINE = (255, 222, 0, 255)
CRIMSON = (185, 28, 28, 238)
QUOTE_BG = (17, 12, 10, 238)


def _make_pipeline(pdir: Path, style_id: str, sentences, total_sec: float) -> Path:
    pdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, raw in enumerate(sentences):
        start = float(raw["start_sec"])
        end = float(raw["end_sec"])
        rows.append({
            **raw,
            "start_frame": int(round(start * FPS)),
            "end_frame": int(round(end * FPS)),
        })
    events = [
        {"id": f"e{index}", "type": "sentence_start", "time_sec": row["start_sec"],
         "frame": row["start_frame"], "sentence_id": row["id"]}
        for index, row in enumerate(rows, start=1)
    ]
    clips = [
        {"clip_id": f"c{index}", "track_role": ("a_roll" if index % 2 == 1 else "b_roll"),
         "media_path": "", "timeline_in_sec": row["start_sec"], "timeline_out_sec": row["end_sec"],
         "timeline_in_frame": row["start_frame"], "timeline_out_frame": row["end_frame"],
         "cut_event_frame": row["start_frame"], "cut_event_id": f"e{index}"}
        for index, row in enumerate(rows, start=1)
    ]
    write_json(pdir / "03_audio_events.json", {
        "locked_duration_sec": float(total_sec), "sentences": rows, "events": events,
    })
    write_json(pdir / "04_edl.json", {"style_id": style_id, "clips": clips})
    return pdir


def _color_mask(image, rgba):
    return np.all(np.asarray(image) == rgba, axis=2)


def _by_kind(elements, kind):
    return [elem for elem in elements if str(elem.get("kind") or "") == kind]


class Style03DnaPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdir = _make_pipeline(self.root / "p03", "style_03", [
            {"id": "s1", "text": "花咗三百万喺金湾", "keywords": ["三百万"],
             "start_sec": 0.0, "end_sec": 2.2, "emotion_or_info": "hook"},
            {"id": "s2", "text": "开咗一间六百平点赞茶楼", "keywords": ["六百平"],
             "start_sec": 2.2, "end_sec": 5.0, "emotion_or_info": "info"},
            {"id": "s3", "text": "呢个两百平嘅透明厨房", "keywords": ["两百平"],
             "start_sec": 5.0, "end_sec": 9.5, "emotion_or_info": "twist"},
            {"id": "s4", "text": "呢间铺两百平月租三十万", "keywords": ["两百平", "三十万"],
             "start_sec": 9.5, "end_sec": 12.5, "emotion_or_info": "info"},
            {"id": "s5", "text": "你觉得值唔值得点赞", "keywords": ["点赞"],
             "start_sec": 12.5, "end_sec": 13.0, "emotion_or_info": "ending"},
        ], 13.0)
        self.result = S6.run_step6(self.pdir, style_id="style_03")
        self.elements = self.result["visual_elements"]

    def test_quote_card_anchors_to_top_band(self):
        quote = _by_kind(self.elements, "quote_citation_card")
        self.assertTrue(quote, "style_03 应生成深墨顶栏引语卡")
        card = quote[0]
        self.assertEqual(card["bar_mode"], "quote_citation_card")
        self.assertEqual(card["anchor"], "top")
        self.assertEqual(card["bar_width_px"], 608)
        fb = card["final_bbox"]
        self.assertGreaterEqual(fb["y"], SAFE_ZONE["y_min"])
        self.assertLess(fb["y"], 450, f"引语卡不许落回中下屏: {fb}")
        self.assertGreaterEqual(fb["y"] + fb["h"], SAFE_ZONE["y_min"] + 16)
        self.assertLessEqual(fb["y"] + fb["h"], 340, f"引语卡必须压在顶部带: {fb}")
        self.assertTrue(card["safe_zone_ok"])
        self.assertTrue(check_bbox_in_safe_zone(fb)[0], fb)
        self.assertGreaterEqual(card["read_hold_sec"], 1.35)

        image = overlays.render_template_packaging_png(card, style_id="style_03")
        self.assertIsNotNone(image)
        self.assertLess(image.width, 960)
        self.assertEqual(image.getpixel((image.width - 9, image.height // 2)), QUOTE_BG)
        self.assertGreater(_color_mask(image, CRIMSON).sum(), 50)

    def test_full_width_crimson_bar_stays_centered_and_safe(self):
        bars = [e for e in self.elements if e.get("bar_mode") == "full_width_crimson_bar"]
        self.assertTrue(bars, "style_03 应有通宽深红底条元素")
        bar = next(e for e in bars if e.get("kind") == "stamp_tag")
        self.assertEqual(bar["anchor"], "center")
        self.assertGreaterEqual(bar["bar_width_px"], 900)
        fb = bar["final_bbox"]
        self.assertEqual(fb["x"] + fb["w"] // 2, 540)
        self.assertGreaterEqual(fb["x"], SAFE_ZONE["x_min"])
        self.assertLessEqual(fb["x"] + fb["w"], SAFE_ZONE["x_max"])
        self.assertTrue(bar["safe_zone_ok"])
        self.assertTrue(check_bbox_in_safe_zone(fb)[0], fb)
        self.assertGreaterEqual(bar["read_hold_sec"], 1.35)

        image = overlays.render_template_packaging_png(bar, style_id="style_03")
        self.assertIsNotNone(image)
        self.assertGreaterEqual(image.width, 960)
        row = image.height - 6  # 避开字身/投影，取底条实色区
        for x in (8, image.width // 2, image.width - 9):
            self.assertEqual(image.getpixel((x, row)), CRIMSON)

        opening = _by_kind(self.elements, "center_burst_title")[0]
        self.assertEqual(opening["bar_mode"], "full_width_crimson_bar")
        self.assertGreaterEqual(opening["bar_width_px"], 900)


class Style17DnaPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_comparison_sentence_gets_dual_gold_grid(self):
        pdir = _make_pipeline(self.root / "p17_cmp", "style_17", [
            {"id": "s1", "text": "老店改造前 VS 升级后采光好太多", "keywords": ["改造前", "升级后"],
             "start_sec": 0.0, "end_sec": 3.0, "emotion_or_info": "hook"},
            {"id": "s2", "text": "现在客人一进门就说敞亮", "keywords": ["敞亮"],
             "start_sec": 3.0, "end_sec": 6.0, "emotion_or_info": "info"},
        ], 6.0)
        result = S6.run_step6(pdir, style_id="style_17")
        comparing = [e for e in result["visual_elements"] if e.get("compare_mode") is True]
        self.assertTrue(comparing, "对比句必须激活暖金双格对比框")
        elem = comparing[0]
        self.assertEqual(elem["kind"], "center_burst_title")
        self.assertEqual(elem["layout_mode"], "dual_gold_hairline_grid")
        self.assertTrue(elem["text_or_asset"])
        self.assertTrue(elem["subtext"])
        self.assertNotEqual(elem["text_or_asset"], elem["subtext"])
        self.assertIn("改造前", elem["text_or_asset"])
        self.assertIn("升级后", elem["subtext"])
        fb = elem["final_bbox"]
        self.assertGreaterEqual(fb["h"], 200)
        self.assertTrue(elem["safe_zone_ok"])
        self.assertTrue(check_bbox_in_safe_zone(fb)[0], fb)

        image = overlays.render_template_packaging_png(elem, style_id="style_17")
        self.assertIsNotNone(image)
        wide_rows = np.flatnonzero(_color_mask(image, GOLD_LINE).sum(axis=1) > image.width * 0.85)
        groups = np.split(wide_rows, np.flatnonzero(np.diff(wide_rows) != 1) + 1)
        self.assertEqual(len(groups), 3, "双格对比框应有上/中/下三条暖金细线")
        self.assertTrue(all(1 <= len(group) <= 3 for group in groups))

    def test_plain_sentence_keeps_clean_single_frame(self):
        pdir = _make_pipeline(self.root / "p17_clean", "style_17", [
            {"id": "s1", "text": "现炒锅气实测", "keywords": ["锅气"],
             "start_sec": 0.0, "end_sec": 3.0, "emotion_or_info": "hook"},
            {"id": "s2", "text": "这一口真的很下饭", "keywords": ["下饭"],
             "start_sec": 3.0, "end_sec": 6.0, "emotion_or_info": "info"},
        ], 6.0)
        result = S6.run_step6(pdir, style_id="style_17")
        opening = _by_kind(result["visual_elements"], "center_burst_title")[0]
        self.assertIs(opening["compare_mode"], False)
        self.assertEqual(opening["layout_mode"], "clean_fullscreen_gold")
        self.assertTrue(check_bbox_in_safe_zone(opening["final_bbox"])[0], opening["final_bbox"])

        image = overlays.render_template_packaging_png(opening, style_id="style_17")
        self.assertIsNotNone(image)
        self.assertEqual(int(_color_mask(image, GOLD_LINE).sum()), 0, "单镜不许画对比金线")
        left, top, right, bottom = image.getbbox()
        for point in ((left, top), (right - 1, top), (left, bottom - 1), (right - 1, bottom - 1)):
            self.assertEqual(image.getpixel(point), (0, 0, 0, 178))


class Style01OpeningBeatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_store_poster_burst_carries_three_distinct_beats(self):
        pdir = _make_pipeline(self.root / "p01", "style_01", [
            {"id": "s1", "text": "现切鲜羊排看得见才放心", "keywords": ["现切"],
             "start_sec": 0.0, "end_sec": 2.2, "emotion_or_info": "hook"},
            {"id": "s2", "text": "火候全靠老师傅盯着", "keywords": ["火候"],
             "start_sec": 2.2, "end_sec": 4.4, "emotion_or_info": "info"},
            {"id": "s3", "text": "手工切片厚薄一个样", "keywords": ["手工"],
             "start_sec": 4.4, "end_sec": 6.6, "emotion_or_info": "detail"},
        ], 6.6)
        result = S6.run_step6(pdir, style_id="style_01")
        opening = next(e for e in result["visual_elements"] if e.get("opening_mode") == "store_poster_burst")
        self.assertEqual(opening["kind"], "flower_text")
        self.assertAlmostEqual(opening["tilt_deg"], -5.0, places=3)
        beats = opening["opening_beats"]
        self.assertEqual(len(beats), 3, beats)
        self.assertEqual(len({b["text"] for b in beats}), 3, "三拍文案不许重样")
        for beat in beats:
            self.assertAlmostEqual(beat["tilt_deg"], -5.0, places=3)
            self.assertEqual(beat["anim_mode"], "store_stagger_burst")
            self.assertGreaterEqual(beat["start_sec"], 0.0)
            self.assertLess(beat["start_sec"], 3.2)
            self.assertLessEqual(beat["end_sec"], 3.2)
        for key in ("id", "kind", "bbox", "final_bbox", "safe_zone_ok",
                    "aligned_sfx_or_accent_frame", "read_hold_sec", "text_or_asset"):
            self.assertIn(key, opening, f"开场元素原有必选字段缺失: {key}")
        self.assertTrue(opening["safe_zone_ok"])
        self.assertTrue(check_bbox_in_safe_zone(opening["final_bbox"])[0])
        self.assertGreaterEqual(opening["read_hold_sec"], 1.35)


class CrossStyleIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _make_pipeline(self.root / "p02", "style_02", [
            {"id": "s1", "text": "花咗三百万喺金湾", "keywords": ["三百万"],
             "start_sec": 0.0, "end_sec": 2.2, "emotion_or_info": "hook"},
            {"id": "s2", "text": "开咗一间六百平点赞茶楼", "keywords": ["六百平"],
             "start_sec": 2.2, "end_sec": 5.0, "emotion_or_info": "info"},
            {"id": "s3", "text": "呢个两百平嘅透明厨房", "keywords": ["两百平"],
             "start_sec": 5.0, "end_sec": 9.5, "emotion_or_info": "twist"},
            {"id": "s4", "text": "呢间铺两百平月租三十万", "keywords": ["两百平", "三十万"],
             "start_sec": 9.5, "end_sec": 12.5, "emotion_or_info": "info"},
            {"id": "s5", "text": "你觉得值唔值得点赞", "keywords": ["点赞"],
             "start_sec": 12.5, "end_sec": 13.0, "emotion_or_info": "ending"},
        ], 13.0)

    def test_kinetic_stage_does_not_get_dna_fields(self):
        result = S6.run_step6(self.root / "p02", style_id="style_02")
        self.assertTrue((result.get("kinetic_stage") or {}).get("enabled"))
        for elem in result["visual_elements"]:
            self.assertNotIn("compare_mode", elem, f"style_02 被对比 DNA 污染: {elem}")
            self.assertNotIn("opening_beats", elem, f"style_02 被三拍 DNA 污染: {elem}")
            self.assertNotIn("bar_mode", elem, f"style_02 被红底条 DNA 污染: {elem}")
        for cue in result["sfx_cues"]:
            self.assertNotIn(str(cue.get("sfx_role") or ""), {"ding", "chime"}, cue)

    def test_sources_have_no_local_paths_or_dev_jargon(self):
        privacy_re = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+|C:\\\\Users\\\\[A-Za-z0-9._-]+")
        jargon_re = re.compile(r"\b(?:EDL|LUT|ASR|OCR|kinetic|compare_mode|layout_mode)\b", re.IGNORECASE)
        for path in (SCRIPTS / "step6_packaging.py", Path(__file__).resolve()):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(privacy_re.search(text), f"本机路径泄漏: {path}")
        result = S6.run_step6(self.root / "p02", style_id="style_02")
        for elem in result["visual_elements"]:
            for field in ("text_or_asset", "subtext", "policy", "reason"):
                self.assertIsNone(jargon_re.search(str(elem.get(field) or "")), f"开发黑话上屏: {elem}")


if __name__ == "__main__":
    unittest.main()
