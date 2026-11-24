#!/usr/bin/env python3
"""风格指纹：复用后怎么印证「这就是这套风格」，以及套错风格能被当场识破。

用一份**合成但结构真实**的 06_packaging.json 做夹具（音效角色/触发配比、花字配色、
BGM BPM 都按 style_21 源草稿实测值给），验证：
  1. fingerprint() 每套都有完整可测项；
  2. 合规的成片 → PASS，且 style_guess 判回 style_21；
  3. 拿 style_15 的指纹去核对这份 style_21 成片 → FAIL（密度/配色/BPM/来源全对不上）；
  4. 跨风格音效串味、无理由音效会被抓出来。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

import style_fingerprint as SF  # noqa: E402

RULES = ROOT / "forensic" / "style_trigger_rules_sfx_flower.json"
SFX_DIR = ROOT / "short-video-studio" / "assets" / "sound_vibe" / "sfx"

# style_21 源草稿实测：12 条音效（cut7/flower4/line1；accent5/cut2/info4/hook1），3 个花字视觉块
STYLE21_CUES = [
    (0.0, "accent", "cut"),
    (1.667, "cut", "cut"),
    (3.783, "accent", "line"),
    (4.833, "cut", "cut"),
    (5.3, "info", "cut"),
    (6.8, "accent", "flower"),
    (7.517, "info", "cut"),
    (8.2, "hook", "flower"),
    (10.017, "accent", "cut"),
    (11.65, "info", "cut"),
    (12.9, "accent", "flower"),
    (14.867, "info", "flower"),
]
STYLE21_FLOWERS = [
    (0.85, 3.98, "keyword", "#FFF589"),
    (6.8, 2.85, "chapter", "#FFF589"),
    (11.65, 3.97, "keyword", "#B71C1C"),
]


def _write_fixture(tmp: Path, style_id: str = "style_21", *, cues=None, flowers=None,
                   bpm: float = 99.4, sfx_style_dir: str = "style_21", reasons=None) -> Path:
    pipe = tmp / "pipeline"
    pipe.mkdir(parents=True, exist_ok=True)
    cue_rows = []
    for i, (t, role, reason) in enumerate(cues if cues is not None else STYLE21_CUES):
        cue_rows.append({
            "id": f"sfx_{i+1:03d}",
            "file": str(SFX_DIR / sfx_style_dir / f"{role}_real.mp3"),
            "time_sec": t,
            "frame": int(round(t * 30)),
            "role": role,
            "reason": (reasons or {}).get(i, reason),
            "anchor": {"kind": reason, "time_sec": t, "offset_sec": 0.0},
            "gain_db": -7.0,
        })
    vis = []
    for i, (t, hold, trigger, fg) in enumerate(flowers if flowers is not None else STYLE21_FLOWERS):
        vis.append({
            "id": f"pkg_{i+1:03d}",
            "kind": "stamp_tag",
            "text_or_asset": "三百万",
            "start_frame": int(round(t * 30)),
            "end_frame": int(round((t + hold) * 30)),
            "read_hold_sec": hold,
            "trigger": trigger,
            "fg_hex": fg,
            "anchor": {"kind": trigger, "time_sec": t},
        })
    (pipe / "06_packaging.json").write_text(json.dumps({
        "step": "06_packaging",
        "style_id": style_id,
        "sfx_cues": cue_rows,
        "visual_elements": vis,
        "budget": {"max_sfx_cues": 49, "used_sfx_cues": len(cue_rows)},
    }, ensure_ascii=False), encoding="utf-8")
    (pipe / "03_audio_events.json").write_text(json.dumps({
        "locked_duration_sec": 15.617,
        "bgm": {"bpm": bpm, "beat_times_sec": []},
    }), encoding="utf-8")
    return pipe


class FingerprintShapeTests(unittest.TestCase):
    def test_all_24_fingerprints_complete(self):
        table = json.loads(RULES.read_text(encoding="utf-8"))
        self.assertEqual(len(table["styles"]), 24)
        for sid in table["styles"]:
            fp = SF.fingerprint(sid)
            self.assertTrue(fp["found"], sid)
            for key in ("per_min", "max_per_min", "min_gap_sec", "role_shares",
                        "trigger_shares", "allowed_triggers"):
                self.assertIn(key, fp["sfx"], f"{sid}.sfx.{key}")
            for key in ("per_min", "trigger_shares", "allowed_triggers", "chars", "colors"):
                self.assertIn(key, fp["flower"], f"{sid}.flower.{key}")

    def test_style21_fingerprint_values(self):
        fp = SF.fingerprint("style_21")
        self.assertAlmostEqual(fp["sfx"]["per_min"], 46.1, places=1)
        self.assertEqual(set(fp["sfx"]["allowed_triggers"]), {"cut", "flower", "line"})
        self.assertAlmostEqual(fp["sfx"]["min_gap_sec"], 0.467, places=3)
        self.assertEqual(fp["flower"]["colors"].get("center_burst_color"), "#FFF589")
        self.assertAlmostEqual(fp["beat"]["bpm"], 99.38, places=1)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pipe = _write_fixture(Path(self.tmp.name))

    def test_matching_style_passes(self):
        res = SF.verify("style_21", self.pipe)
        self.assertEqual(res["verdict"], "PASS", SF.format_report(res))
        self.assertEqual(res["style_guess"], "style_21")
        self.assertEqual(res["n_fail"], 0)

    def test_wrong_style_is_caught(self):
        """拿 style_15 的指纹核对这份 style_21 成片：必须 FAIL 并指出更像 style_21。"""
        res = SF.verify("style_15", self.pipe)
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(res["style_guess"], "style_21")
        failed = {c["name"] for c in res["checks"] if c["status"] == "FAIL"}
        self.assertIn("style_id_declared", failed)
        self.assertIn("sfx_source_dir", failed)

    def test_cross_style_sfx_is_caught(self):
        pipe = _write_fixture(Path(self.tmp.name) / "b", sfx_style_dir="style_15")
        res = SF.verify("style_21", pipe)
        names = {c["name"]: c for c in res["checks"]}
        self.assertEqual(names["sfx_source_dir"]["status"], "FAIL")

    def test_unreasoned_sfx_is_caught(self):
        """全是"无由"音效（reason=free）→ 触发配比与角色都偏，判 FAIL/WARN。"""
        reasons = {i: "free" for i in range(len(STYLE21_CUES))}
        pipe = _write_fixture(Path(self.tmp.name) / "c", reasons=reasons)
        res = SF.verify("style_21", pipe)
        names = {c["name"]: c for c in res["checks"]}
        self.assertIn(names["sfx_trigger_mix"]["status"], ("FAIL", "WARN"))
        self.assertEqual(res["verdict"], "FAIL")

    def test_density_too_high_is_caught(self):
        """41 条塞进 15.6s = 157/min，远超源草稿 46/min 的 110% 上限。"""
        cues = [(i * 0.38, "info", "cut") for i in range(41)]
        pipe = _write_fixture(Path(self.tmp.name) / "d", cues=cues)
        res = SF.verify("style_21", pipe)
        names = {c["name"]: c for c in res["checks"]}
        self.assertEqual(names["sfx_per_min"]["status"], "FAIL")

    def test_wrong_palette_is_caught(self):
        flowers = [(0.85, 3.0, "keyword", "#00FF00"), (6.8, 3.0, "chapter", "#0000FF")]
        pipe = _write_fixture(Path(self.tmp.name) / "e", flowers=flowers)
        res = SF.verify("style_21", pipe)
        names = {c["name"]: c for c in res["checks"]}
        self.assertEqual(names["flower_palette"]["status"], "FAIL")

    def test_wrong_bgm_is_caught(self):
        pipe = _write_fixture(Path(self.tmp.name) / "f", bpm=66.0)
        res = SF.verify("style_21", pipe)
        names = {c["name"]: c for c in res["checks"]}
        self.assertEqual(names["bgm_bpm"]["status"], "FAIL")

    def test_report_renders(self):
        text = SF.format_report(SF.verify("style_21", self.pipe))
        self.assertIn("风格指纹核对", text)
        self.assertIn("sfx_trigger_mix", text)


if __name__ == "__main__":
    unittest.main()
