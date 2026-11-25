"""T004：style_01 / style_21 节奏编舞器与风格锁 DNA 校验回归。

四组验收：
1. style_01：3 拍错落斜标开场、~57% 语义画面覆盖、口播倍速全锁 1.0；
2. style_21：前 3 秒 >=4 个画面切片、~76% 工艺特写覆盖、推镜关键帧、口播倍速全锁 1.0；
3. style_lock：CLI 向后兼容 + `--check-dna` 真库全绿，异常字典（越界占比 / 非 1.0 倍速锁 /
   偷偷加粗描边）能精准检出；
4. 跨风格隔离与零隐私路径自查。
"""

import copy
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

from common import maybe_reexec_in_venv

maybe_reexec_in_venv()

import style_01_21_choreographer as choreo  # noqa: E402
import style_lock  # noqa: E402

STYLE_LOCK_SCRIPT = ROOT / "short-video-studio" / "scripts" / "style_lock.py"

SENTENCE_TEXTS = (
    "现切鲜羊排看得见才放心",
    "火候全靠老师傅盯着",
    "手工切片厚薄一个样",
    "后厨干净到反光",
    "刀工利落不拖泥带水",
    "现烤出炉香气扑鼻",
    "今天到店就能尝到",
    "评论区告诉我你想吃啥",
)
BROLL_TAGS = ("现切", "火候", "手工", "后厨", "刀工", "现烤", "到店", "评论区")


def _sentences():
    rows = []
    for index, text in enumerate(SENTENCE_TEXTS):
        start = round(index * 2.4, 3)
        rows.append({"id": f"s{index + 1}", "text": text, "start_sec": start, "end_sec": round(start + 2.2, 3)})
    return rows


def _candidates():
    return [
        {"id": f"b{index + 1}", "duration_sec": 3.5, "semantic_tags": [tag],
         "description": f"{tag}现场实拍"}
        for index, tag in enumerate(BROLL_TAGS)
    ]


def _assert_contiguous(testcase, segments, total):
    testcase.assertTrue(segments, "分镜不能为空")
    testcase.assertAlmostEqual(segments[0]["start_sec"], 0.0, places=3)
    testcase.assertAlmostEqual(segments[-1]["end_sec"], total, places=3)
    for left, right in zip(segments, segments[1:]):
        testcase.assertAlmostEqual(left["end_sec"], right["start_sec"], places=3)


class StyleRhythmContractTests(unittest.TestCase):
    def test_contract_comes_from_library_truth(self):
        expected = {
            "style_01": {"ratio": 0.572, "opening": 3, "median": 1.87, "mode": "store_poster_burst",
                         "anim": "store_stagger_burst", "tilt": -5.0, "punch": 1.0},
            "style_21": {"ratio": 0.756, "opening": 4, "median": 1.15, "mode": "store_detail_badge",
                         "anim": "scale_in", "tilt": 0.0, "punch": 1.2},
        }
        for style_id, truth in expected.items():
            contract = choreo.STYLE_RHYTHM_CONTRACTS[style_id]
            self.assertAlmostEqual(contract["broll_target_ratio"], truth["ratio"], places=3)
            self.assertEqual(contract["opening_cuts_3s"], truth["opening"])
            self.assertAlmostEqual(contract["median_shot_sec"], truth["median"], places=3)
            self.assertEqual(contract["opening_mode"], truth["mode"])
            self.assertEqual(contract["anim_mode"], truth["anim"])
            self.assertAlmostEqual(contract["tilt_deg"], truth["tilt"], places=3)
            self.assertAlmostEqual(contract["punch_in_scale"], truth["punch"], places=3)
            self.assertEqual(contract["aroll_speed_lock"], 1.0)


class Style01RhythmTests(unittest.TestCase):
    def test_three_tilted_beats_and_semantic_broll(self):
        plan = choreo.plan_style_01_rhythm(_sentences(), _candidates())

        self.assertEqual(plan["style_id"], "style_01")
        beats = plan["opening_beats"]
        self.assertEqual(len(beats), 3, beats)
        self.assertEqual([b["beat_index"] for b in beats], [1, 2, 3])
        for beat in beats:
            self.assertAlmostEqual(beat["tilt_deg"], -5.0, places=3)
            self.assertEqual(beat["anim_mode"], "store_stagger_burst")
            self.assertGreaterEqual(beat["start_sec"], 0.0)
            self.assertLess(beat["start_sec"], 3.2)
            self.assertLessEqual(beat["end_sec"], 3.2)
            self.assertTrue(beat["text"])
        self.assertEqual(len({b["text"] for b in beats}), 3, "三拍要用三句不同的话，不能同一句贴三遍")

        self.assertEqual(plan["broll_ratio_band"], [0.45, 0.68])
        self.assertGreaterEqual(plan["broll_ratio_planned"], 0.45)
        self.assertLessEqual(plan["broll_ratio_planned"], 0.68)
        self.assertLessEqual(abs(plan["broll_ratio_planned"] - 0.572), 0.06, plan["broll_ratio_planned"])
        self.assertIsNone(plan["degrade_reason"])

        segments = plan["segments"]
        _assert_contiguous(self, segments, plan["total_duration_sec"])
        self.assertTrue(all(seg["track_role"] in ("a_roll", "b_roll") for seg in segments))
        self.assertTrue(all(seg["playback_speed"] == 1.0 for seg in segments))
        self.assertTrue(plan["aroll_speed_locked"])
        broll_sec = sum(seg["duration_sec"] for seg in segments if seg["track_role"] == "b_roll")
        self.assertAlmostEqual(plan["broll_ratio_planned"], broll_sec / plan["total_duration_sec"], places=3)

    def test_no_candidates_degrades_to_aroll_without_speed_cheat(self):
        plan = choreo.plan_style_01_rhythm(_sentences(), [])

        self.assertEqual(plan["broll_ratio_planned"], 0.0)
        self.assertTrue(plan["degrade_reason"])
        self.assertTrue(plan["aroll_speed_locked"])
        self.assertTrue(plan["segments"])
        self.assertTrue(all(seg["track_role"] == "a_roll" for seg in plan["segments"]))
        self.assertTrue(all(seg["playback_speed"] == 1.0 for seg in plan["segments"]))


class Style21RhythmTests(unittest.TestCase):
    def test_fast_opening_craft_broll_and_punch_in(self):
        plan = choreo.plan_style_21_rhythm(_sentences(), _candidates())

        self.assertEqual(plan["style_id"], "style_21")
        cuts = plan["opening_3s_cuts"]
        self.assertGreaterEqual(len(cuts), 4, cuts)
        self.assertGreaterEqual(plan["opening_3s_slice_count"], 4, plan)
        self.assertAlmostEqual(cuts[0], 0.0, places=3)
        self.assertAlmostEqual(cuts[-1], 3.0, places=3)
        self.assertTrue(all(cut <= 3.0 + 1e-6 for cut in cuts), cuts)
        for left, right in zip(cuts, cuts[1:]):
            self.assertGreaterEqual(right - left, 0.45 - 1e-6, f"单切点间隔过密: {left}->{right}")

        self.assertGreaterEqual(plan["broll_ratio_planned"], 0.60)
        self.assertLessEqual(abs(plan["broll_ratio_planned"] - 0.756), 0.05, plan["broll_ratio_planned"])
        self.assertIsNone(plan["degrade_reason"])
        self.assertGreater(plan["fast_cuts_under_1s_ratio"], 0.25, plan["fast_cuts_under_1s_ratio"])

        segments = plan["segments"]
        _assert_contiguous(self, segments, plan["total_duration_sec"])
        self.assertTrue(all(seg["playback_speed"] == 1.0 for seg in segments), "口播/推镜都不许改声音倍速")
        self.assertTrue(plan["aroll_speed_locked"])
        self.assertTrue(any(seg["track_role"] == "a_roll" for seg in segments))
        self.assertTrue(any(seg["track_role"] == "b_roll" for seg in segments))

        punch_segments = [seg for seg in segments if seg.get("punch_in")]
        self.assertTrue(punch_segments, "style_21 必须给推镜触发镜生成关键帧轨迹")
        for seg in punch_segments:
            self.assertGreaterEqual(seg["punch_in_zoom"], 1.18)
            self.assertLessEqual(seg["punch_in_zoom"], 1.22)
            keys = seg["keyframes"]
            self.assertEqual(keys[0]["scale"], 1.0)
            self.assertGreaterEqual(keys[-1]["scale"], 1.18)
            self.assertLessEqual(keys[-1]["scale"], 1.22)
            self.assertEqual(seg["playback_speed"], 1.0)
            self.assertTrue(seg["punch_in_audio_locked"])

    def test_no_craft_candidates_degrades_but_keeps_usable_shots(self):
        plan = choreo.plan_style_21_rhythm(_sentences(), [])

        self.assertEqual(plan["broll_ratio_planned"], 0.0)
        self.assertTrue(plan["degrade_reason"])
        self.assertTrue(plan["segments"])
        self.assertTrue(plan["aroll_speed_locked"])
        self.assertTrue(all(seg["playback_speed"] == 1.0 for seg in plan["segments"]))
        self.assertTrue(any(seg.get("punch_in") for seg in plan["segments"]), "降级也要保留纯构图推镜能力")


class StyleLockDnaTests(unittest.TestCase):
    def test_real_styles_pass_dna_self_check(self):
        for style_id in ("style_01", "style_21", "style_02", "style_03", "style_17"):
            result = style_lock.check_style_dna_integrity(style_id)
            self.assertTrue(result["ok"], f"{style_id} DNA 自检失败: {result}")
            self.assertEqual(result["style_id"], style_id)
            self.assertTrue(result["checks"])
            self.assertEqual(result["issues"], [])

    def test_cli_backward_compatible_and_check_dna(self):
        plain = subprocess.run(
            [sys.executable, str(STYLE_LOCK_SCRIPT), "--style-id", "style_02", "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
        plain_doc = json.loads(plain.stdout)
        self.assertTrue(plain_doc["ok"])
        self.assertNotIn("dna", plain_doc, "没传 --check-dna 时 JSON 结构必须保持原样")
        for key in ("ok", "style_id", "deviations", "waived", "notes"):
            self.assertIn(key, plain_doc)

        for style_id in ("style_01", "style_21", "style_02", "style_03", "style_17"):
            run = subprocess.run(
                [sys.executable, str(STYLE_LOCK_SCRIPT), "--style-id", style_id, "--check-dna", "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(run.returncode, 0, f"{style_id}: {run.stdout}{run.stderr}")
            doc = json.loads(run.stdout)
            self.assertTrue(doc["ok"], doc)
            self.assertTrue(doc["dna"]["ok"], doc["dna"])
            self.assertEqual(doc["dna"]["style_id"], style_id)

        text_run = subprocess.run(
            [sys.executable, str(STYLE_LOCK_SCRIPT), "--style-id", "style_01", "--check-dna"],
            capture_output=True, text=True,
        )
        self.assertEqual(text_run.returncode, 0, text_run.stdout + text_run.stderr)
        self.assertIn("[风格DNA] PASS", text_run.stdout)

    def test_out_of_range_broll_target_is_caught(self):
        bad_lib = copy.deepcopy(style_lock._load_lib())
        for entry in bad_lib["styles"]:
            if entry["style_id"] == "style_01":
                entry["broll_overlay_ratio_target"] = 1.6
        with mock.patch.object(style_lock, "_load_lib", return_value=bad_lib):
            result = style_lock.check_style_dna_integrity("style_01")
        self.assertFalse(result["ok"])
        self.assertTrue(any("B-roll 占比目标在 0..1" in issue for issue in result["issues"]), result["issues"])

    def test_non_locked_aroll_speed_is_caught(self):
        patched = dict(choreo.STYLE_RHYTHM_CONTRACTS["style_21"])
        patched["aroll_speed_lock"] = 1.25
        with mock.patch.dict(choreo.STYLE_RHYTHM_CONTRACTS, {"style_21": patched}):
            result = style_lock.check_style_dna_integrity("style_21")
        self.assertFalse(result["ok"])
        self.assertTrue(any("编舞契约" in issue for issue in result["issues"]), result["issues"])

    def test_stroke_drift_is_caught(self):
        bad_bps = copy.deepcopy(style_lock._blueprints())
        bad_bps["style_01"]["subtitle_override"]["stroke_mode"] = "heavy_black"
        bad_bps["style_01"]["subtitle_override"]["stroke_width"] = 6
        with mock.patch.object(style_lock, "_blueprints", return_value=bad_bps):
            result = style_lock.check_style_dna_integrity("style_01")
        self.assertFalse(result["ok"])
        self.assertTrue(any("描边" in issue for issue in result["issues"]), result["issues"])

    def test_unknown_style_fails_without_crashing(self):
        result = style_lock.check_style_dna_integrity("style_99")
        self.assertFalse(result["ok"])
        self.assertTrue(result["issues"])


class CrossStyleIsolationAndPrivacyTests(unittest.TestCase):
    def test_plans_do_not_leak_into_each_other(self):
        plan_01 = choreo.plan_style_01_rhythm(_sentences(), _candidates())
        plan_21 = choreo.plan_style_21_rhythm(_sentences(), _candidates())

        self.assertEqual(plan_01["style_id"], "style_01")
        self.assertNotIn("opening_3s_cuts", plan_01)
        self.assertEqual(plan_21["style_id"], "style_21")
        self.assertNotIn("opening_beats", plan_21)
        self.assertTrue(all(seg["playback_speed"] == 1.0 for seg in plan_01["segments"] + plan_21["segments"]))

    def test_sources_have_no_local_paths_or_private_drafts(self):
        privacy_re = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+|C:\\\\Users\\\\[A-Za-z0-9._-]+")
        for path in (
            ROOT / "short-video-studio" / "scripts" / "style_01_21_choreographer.py",
            ROOT / "short-video-studio" / "scripts" / "style_lock.py",
            Path(__file__).resolve(),
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(privacy_re.search(text), f"本机路径泄漏: {path}")
        for reason in (
            choreo.plan_style_21_rhythm(_sentences(), [])["degrade_reason"],
            choreo.plan_style_01_rhythm(_sentences(), [])["degrade_reason"],
        ):
            self.assertTrue(reason)
            self.assertNotIn("B-roll", reason, "面向人的提示要用大白话")
            self.assertNotIn("EDL", reason)


if __name__ == "__main__":
    unittest.main()
