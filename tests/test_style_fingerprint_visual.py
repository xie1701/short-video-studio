#!/usr/bin/env python3
"""视觉处理指纹的回归测试（转场 / 抠像 / 变速 / 贴纸 / 特效）。

复用后怎么印证「这就是这套风格」——音效花字对得上只说明声音像，画面处理还得单独核：
  · 这套风格真值 0 段抠像（style_17），成片却抠了 → 抓出来
  · 抠像挂在 B-roll 层（没有可抠的人）→ 抓出来
  · A-roll 口播被变速（口型与字音会拆开）→ 抓出来
  · 插了转场（24 套源草稿转场数都 = 0）→ 抓出来

用合成的 `04_edl.json` 做夹具，不依赖真素材、不依赖渲染。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

import style_fingerprint as SF  # noqa: E402

RULES_VISUAL = ROOT / "forensic" / "style_trigger_rules_visual.json"


def _clip(cid, role, t, dur, *, cutout=False, speed=1.0):
    c = {"clip_id": cid, "track_role": role, "timeline_in_sec": t, "duration_sec": dur,
         "media_path": f"/p/01_normalized/norm_{cid}.mp4",
         "source_in_sec": t, "source_out_sec": t + dur, "speed": speed}
    if cutout:
        c["pip_layout"] = {"mode": "geometry_pip_main_punch_in", "cutout": True}
    return c


def _write_edl(pipe: Path, style_id="style_21", clips=None, *, transitions_applied=0):
    clips = clips if clips is not None else [
        _clip("c1", "a_roll", 0.0, 1.0, cutout=True),
        _clip("c2", "b_roll", 1.0, 2.0),
        _clip("c3", "a_roll", 3.0, 1.0),
    ]
    edl = {
        "step": "04_build_edl",
        "style_id": style_id,
        "total_duration_sec": sum(float(c["duration_sec"]) for c in clips),
        "clips": clips,
        "cutout_gate": {"candidates": sum(1 for c in clips if (c.get("pip_layout") or {}).get("cutout")),
                        "allowed": sum(1 for c in clips if (c.get("pip_layout") or {}).get("cutout")),
                        "dropped": 0, "rows": []},
        "treatment_policy": {"transition": {"applied": transitions_applied, "dropped": 2}},
    }
    pipe.mkdir(parents=True, exist_ok=True)
    (pipe / "04_edl.json").write_text(json.dumps(edl, ensure_ascii=False), encoding="utf-8")
    return pipe


class VisualFingerprintShapeTests(unittest.TestCase):
    def test_24_styles_have_visual_fingerprint(self):
        for sid in (json.loads(RULES_VISUAL.read_text(encoding="utf-8"))["styles"]):
            fp = SF.visual_fingerprint(sid)
            self.assertTrue(fp["found"], sid)
            for key in ("cutout", "speed", "overlay", "effect", "transition"):
                self.assertIn(key, fp, sid)
            self.assertEqual(fp["transition"]["truth_count"], 0, sid)

    def test_style_21_expectations(self):
        fp = SF.visual_fingerprint("style_21")
        self.assertTrue(fp["cutout"]["enabled"])
        self.assertEqual(fp["cutout"]["truth_segments"], 7)
        self.assertFalse(fp["speed"]["enabled"])
        self.assertEqual(fp["effect"]["names"], ["模糊"])

    def test_style_17_has_no_cutout_truth(self):
        fp = SF.visual_fingerprint("style_17")
        self.assertFalse(fp["cutout"]["enabled"])
        self.assertEqual(fp["cutout"]["truth_segments"], 0)


class VisualChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pipe = Path(self.tmp.name) / "pipeline"

    def _status(self, style_id, kind):
        rows = SF.visual_checks(style_id, self.pipe)
        return {r["name"]: r for r in rows}

    def test_clean_edl_passes(self):
        _write_edl(self.pipe)
        names = self._status("style_21", "clean")
        for key in ("cutout_scope", "cutout_share", "speed_aroll_lock",
                    "speed_usage", "transition_hard_cut"):
            self.assertEqual(names[key]["status"], "PASS", f"{key}: {names[key]}")

    def test_cutout_on_broll_is_caught(self):
        _write_edl(self.pipe, clips=[_clip("c1", "b_roll", 0.0, 2.0, cutout=True),
                                     _clip("c2", "a_roll", 2.0, 2.0)])
        names = self._status("style_21", "scope")
        self.assertEqual(names["cutout_scope"]["status"], "FAIL")

    def test_style_without_cutout_truth_but_cutout_used_fails(self):
        _write_edl(self.pipe, style_id="style_17",
                   clips=[_clip("c1", "a_roll", 0.0, 2.0, cutout=True),
                          _clip("c2", "b_roll", 2.0, 2.0)])
        names = self._status("style_17", "nocut")
        self.assertEqual(names["cutout_share"]["status"], "FAIL")

    def test_aroll_speed_is_caught(self):
        _write_edl(self.pipe, style_id="style_24",
                   clips=[_clip("c1", "a_roll", 0.0, 2.0, speed=2.0),
                          _clip("c2", "b_roll", 2.0, 2.0, speed=2.0)])
        names = self._status("style_24", "spd")
        self.assertEqual(names["speed_aroll_lock"]["status"], "FAIL")

    def test_invented_speed_value_is_caught(self):
        _write_edl(self.pipe, style_id="style_24",
                   clips=[_clip("c1", "b_roll", 0.0, 2.0, speed=3.0)])
        names = self._status("style_24", "spd2")
        self.assertEqual(names["speed_values"]["status"], "FAIL")

    def test_transition_is_caught(self):
        _write_edl(self.pipe, transitions_applied=2)
        names = self._status("style_21", "tr")
        self.assertEqual(names["transition_hard_cut"]["status"], "FAIL")
        self.assertEqual(names["transition_hard_cut"]["measured"], 2)

    def test_no_edl_is_warn_not_pass(self):
        self.pipe.mkdir(parents=True)
        rows = SF.visual_checks("style_21", self.pipe)
        self.assertEqual(rows[0]["status"], "WARN")
        self.assertIn("04_edl.json", rows[0]["note"])

    def test_report_renders(self):
        _write_edl(self.pipe)
        text = SF.format_visual_report("style_21", self.pipe)
        self.assertIn("视觉处理指纹", text)
        self.assertIn("cutout_share", text)

    def test_verify_includes_visual_checks(self):
        _write_edl(self.pipe)
        res = SF.verify("style_21", self.pipe)
        names = {c["name"] for c in res["checks"]}
        self.assertIn("cutout_share", names)
        self.assertIn("transition_hard_cut", names)


if __name__ == "__main__":
    unittest.main()
