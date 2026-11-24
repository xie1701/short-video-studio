#!/usr/bin/env python3
"""抠像适用性门槛（`pip_policy`）的回归测试。

事故来源（2026-11-24 用户实测原话）：「这个片子其实很多地方不适合抠像（比如 5-10 秒中，
讲解效果图的地方，都把讲解图给扣没了）」。

根因：风格模版里「这个风格会用抠像」被套用端理解成「每一镜都必须抠像」，于是主持人举着
效果图讲解的镜头也被做了人像抠像，图被抠掉，只剩一双带白边的手。这里的用例锁住三件事：
没人脸不抠、正给人看东西不抠、抠像有密度上限，并且降级后不能留下「满屏叠加层」。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "short-video-studio" / "scripts"))

import pip_policy as PP  # noqa: E402


def _clip(tin=0.0, media="01_normalized/norm_16_2026-10-22 152722.mp4", face=None,
          cutout=True, windows=None, scale=1.2, sec="01_normalized/norm_20_x.mp4"):
    lay = {
        "mode": "geometry_pip_main_punch_in",
        "cutout": cutout,
        "main": {"x": 0.0, "y": 0.0, "scale": scale},
        "windows": windows if windows is not None else [
            {"x": 0.0, "y": 0.107, "scale": 0.998, "matting": False},
            {"x": 0.0, "y": -0.199, "scale": 1.6, "matting": True, "stroke_hex": "#FFFFFF"},
        ],
        "face_anchor": face,
    }
    if sec:
        lay["secondary_media_path"] = sec
        lay["secondary_in_sec"] = 1.0
    return {
        "clip_id": f"c{int(tin * 100)}",
        "track_role": "a_roll",
        "timeline_in_sec": tin,
        "media_path": media,
        "pip_layout": lay,
    }


def _face(h=0.30, w=0.20):
    return {"cx": 0.5, "cy": 0.4, "h": h, "w": w, "faces": 1, "frames_sampled": 3, "source": "test"}


class CutoutGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pipe = Path(self.tmp.name)
        self.write_index({"2026-10-22 152722": "真人出镜，双手空着，正常口播"})

    def tearDown(self):
        self.tmp.cleanup()

    def write_index(self, mapping):
        import json
        payload = {"step": "visual_index", "clips": [
            {"filename": f"{k}.mov", "description": v, "semantic_tags": []} for k, v in mapping.items()
        ]}
        (self.pipe / "00_visual_index.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_no_face_is_never_cut_out(self):
        """没有检出人脸（手部特写/图纸特写/空镜）→ 不抠像，也不再推近裁切。"""
        clips = [_clip(face=None)]
        stats = PP.apply_cutout_gate(clips, {"pip_composition_spec": {}}, self.pipe)
        lay = clips[0]["pip_layout"]
        self.assertFalse(lay["cutout"])
        self.assertEqual(lay["main"]["scale"], 1.0)
        self.assertEqual(stats["downgraded"], 1)
        self.assertEqual(stats["rows"][0]["basis"], "face")

    def test_holding_document_blocks_cutout(self):
        """画面里正在给人看效果图 → 抠像会把图抠没，必须降级。"""
        self.write_index({"2026-10-22 152722": "真人出镜，手上举着装修效果图讲解"})
        clips = [_clip(face=_face())]
        stats = PP.apply_cutout_gate(clips, {"pip_composition_spec": {}}, self.pipe)
        self.assertFalse(clips[0]["pip_layout"]["cutout"])
        self.assertIn("效果图", stats["rows"][0]["reason"])

    def test_face_too_small_blocks_cutout(self):
        clips = [_clip(face=_face(h=0.05, w=0.03))]
        PP.apply_cutout_gate(clips, {"pip_composition_spec": {}}, self.pipe)
        self.assertFalse(clips[0]["pip_layout"]["cutout"])

    def test_clean_presenter_keeps_cutout(self):
        clips = [_clip(face=_face())]
        stats = PP.apply_cutout_gate(clips, {"pip_composition_spec": {}}, self.pipe)
        self.assertTrue(clips[0]["pip_layout"]["cutout"])
        self.assertEqual(stats["allowed"], 1)

    def test_downgrade_drops_full_frame_overlay(self):
        """降级后不能留下 scale≈1 的「满屏叠加层」，否则真人会被 B-roll 整条盖住。"""
        clips = [_clip(face=None)]
        PP.apply_cutout_gate(clips, {"pip_composition_spec": {}}, self.pipe)
        lay = clips[0]["pip_layout"]
        self.assertEqual(lay.get("windows") or [], [])
        self.assertNotIn("secondary_media_path", lay)

    def test_downgrade_keeps_real_window(self):
        """降级但风格里有真画中画窗口（scale<0.92）→ 窗口留着，只是不抠像。"""
        clips = [_clip(face=None, windows=[{"x": 0.5, "y": 0.4, "scale": 0.4, "matting": True}])]
        PP.apply_cutout_gate(clips, {"pip_composition_spec": {}}, self.pipe)
        lay = clips[0]["pip_layout"]
        self.assertEqual(len(lay["windows"]), 1)
        self.assertFalse(lay["windows"][0]["matting"])
        self.assertIn("secondary_media_path", lay)

    def test_density_cap_and_gap(self):
        """抠像密度按风格上限：超过 max_ratio / 间隔太近的降级成「只推近」。"""
        clips = [_clip(tin=float(i) * 1.0, face=_face()) for i in range(8)]
        policy = {"cutout_policy": {"max_ratio_of_aroll": 0.5, "min_gap_sec": 4.0}}
        stats = PP.apply_cutout_gate(clips, {"pip_composition_spec": policy}, self.pipe)
        self.assertEqual(stats["allowed"], 2)  # 8 镜 × 0.5 = 4，但 4s 间隔只放得下 2 镜
        allowed_tins = [r["timeline_in_sec"] for r in stats["rows"] if r["allowed"]]
        self.assertEqual(allowed_tins, [0.0, 4.0])

    def test_normalized_prefix_matches_index(self):
        """剪辑表用 `norm_16_xxx.mp4`，判读索引用原始 `xxx.mov`：必须能对上，否则门槛等于没开。"""
        texts = PP.load_visual_texts(self.pipe)
        self.assertIn("2026-10-22 152722", texts)

    def test_policy_disabled(self):
        clips = [_clip(face=_face())]
        PP.apply_cutout_gate(clips, {"pip_composition_spec": {"cutout_policy": {"enabled": False}}}, self.pipe)
        self.assertFalse(clips[0]["pip_layout"]["cutout"])


class CutoutGateWiringTests(unittest.TestCase):
    """源码级防回退：门槛必须真的挂在 step4 上，渲染端也不许拿满屏叠加层当小窗。"""

    SCRIPTS = Path(__file__).resolve().parent.parent / "short-video-studio" / "scripts"

    def test_step4_calls_cutout_gate(self):
        src = (self.SCRIPTS / "step4_build_edl.py").read_text("utf-8")
        self.assertIn("pip_policy.apply_cutout_gate", src)
        self.assertIn("cutout_gate", src)

    def test_render_rejects_full_frame_overlay_as_window(self):
        src = (self.SCRIPTS / "render_mp4.py").read_text("utf-8")
        self.assertIn("real_wins", src)

    def test_pipeline_audits_voice_cleanliness(self):
        src = (self.SCRIPTS / "run_pipeline.py").read_text("utf-8")
        self.assertIn("audio_cleanliness", src)


if __name__ == "__main__":
    unittest.main()
