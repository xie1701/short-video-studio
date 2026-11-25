"""T001：素材头尾 NG 画面检测与 QA 切点干净度审计回归。

四个真实痛点场景：
1. 首部黑闪 + 尾部强糊：首尾都必须被裁掉，并给出命中原因；
2. 全程清晰稳定（含适度正常运动前景）：绝不误伤正常镜头；
3. 伪 EDL 源窗口短缺：必须精准点出冻帧拖尾的 clip_id 与短缺时长；
4. qa_inspector 接入：正常合成视频 cut_boundary_cleanliness=PASS，违例 EDL=FAIL。
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

from common import maybe_reexec_in_venv, write_json

maybe_reexec_in_venv()

import clip_boundary_audit as CBA  # noqa: E402
import qa_inspector as QA  # noqa: E402

FRAME_W, FRAME_H, FPS = 360, 640, 30
NG_CLIP_SEC = 2.0
HEAD_NG_SEC = 0.6
TAIL_NG_SEC = 0.6


def _scene(t: float) -> Image.Image:
    """高频细节 + 斜线 + 适度运动前景：清晰稳定镜头的代表画面。"""
    im = Image.new("RGB", (FRAME_W, FRAME_H), (28, 32, 40))
    draw = ImageDraw.Draw(im)
    for y in range(0, FRAME_H, 40):
        for x in range(0, FRAME_W, 40):
            if (x // 40 + y // 40) % 2 == 0:
                draw.rectangle([x, y, x + 39, y + 39], fill=(230, 220, 200))
    for x in range(0, FRAME_W, 60):
        draw.line([(x, 0), (x + 120, FRAME_H)], fill=(20, 140, 220), width=6)
    cx = FRAME_W // 2 + int(28 * np.sin(t * 3.0))
    draw.ellipse([cx - 70, FRAME_H // 2 - 90, cx + 10, FRAME_H // 2 + 70], fill=(210, 60, 70))
    return im


def _ng_frame(t: float) -> Image.Image:
    """前 0.6s 黑闪（开机没拍上），中段清晰，末 0.6s 强失焦糊帧。"""
    if t < HEAD_NG_SEC:
        return Image.new("RGB", (FRAME_W, FRAME_H), (0, 0, 0))
    frame = _scene(t)
    if t >= NG_CLIP_SEC - TAIL_NG_SEC:
        frame = frame.filter(ImageFilter.GaussianBlur(radius=10))
    return frame


def _clean_frame(t: float) -> Image.Image:
    """全程清晰稳定，只让前景小方块正常走动（不是甩镜）。"""
    frame = _scene(t)
    draw = ImageDraw.Draw(frame)
    cx = int(40 + (FRAME_W - 80) * ((t * 2.0) % 1.0))
    draw.rectangle([cx - 24, FRAME_H - 160, cx + 24, FRAME_H - 100], fill=(250, 250, 250))
    return frame


def _render_video(out: Path, duration: float, maker, with_audio: bool = False) -> None:
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{FRAME_W}x{FRAME_H}", "-r", str(FPS),
        "-i", "-",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=330:d={duration:.2f}:r=48000", "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(out))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for index in range(int(round(duration * FPS))):
            frame = maker(index / FPS)
            proc.stdin.write(np.asarray(frame.convert("RGB"), dtype=np.uint8).tobytes())
    finally:
        proc.stdin.close()
    stderr = (proc.stderr.read() if proc.stderr is not None else b"").decode("utf-8", "replace")
    if proc.stderr is not None:
        proc.stderr.close()
    if proc.wait() != 0:
        raise RuntimeError(f"生成测试视频失败: {stderr[-800:]}")


def _clip(clip_id: str, timeline_in: float, timeline_out: float, src_in: float, src_out: float) -> dict:
    return {
        "clip_id": clip_id,
        "track_role": "a_roll",
        "source_in_sec": src_in,
        "source_out_sec": src_out,
        "timeline_in_sec": timeline_in,
        "timeline_out_sec": timeline_out,
        "timeline_in_frame": int(round(timeline_in * FPS)),
        "timeline_out_frame": int(round(timeline_out * FPS)),
        "duration_sec": round(timeline_out - timeline_in, 4),
        "audio_locked": True,
    }


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
class ClipBoundaryNgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="svs_t001_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.ng_clip = cls.root / "ng_head_tail.mp4"
        cls.clean_clip = cls.root / "clean_stable.mp4"
        _render_video(cls.ng_clip, NG_CLIP_SEC, _ng_frame)
        _render_video(cls.clean_clip, NG_CLIP_SEC, _clean_frame, with_audio=True)

    def test_1_head_black_flash_and_tail_blur_are_trimmed(self):
        result = CBA.analyze_clip_safe_window(str(self.ng_clip))

        self.assertGreaterEqual(result["safe_in_sec"], 0.45, result)
        self.assertLessEqual(
            result["safe_out_sec"], result["duration_sec"] - 0.45, result
        )
        self.assertTrue(result["ng_reasons"], f"命中原因不能为空: {result}")
        self.assertTrue(any(r.startswith("head_") for r in result["ng_reasons"]), result)
        self.assertTrue(any(r.startswith("tail_") for r in result["ng_reasons"]), result)
        self.assertAlmostEqual(result["head_ng_sec"], result["safe_in_sec"], places=3)
        self.assertAlmostEqual(
            result["tail_ng_sec"], result["duration_sec"] - result["safe_out_sec"], places=3
        )
        self.assertAlmostEqual(
            result["usable_span_sec"], result["safe_out_sec"] - result["safe_in_sec"], places=3
        )

    def test_2_clean_stable_clip_is_not_over_trimmed(self):
        result = CBA.analyze_clip_safe_window(str(self.clean_clip))

        self.assertLessEqual(result["head_ng_sec"], 0.15, f"正常镜头不许误裁头: {result}")
        self.assertLessEqual(result["tail_ng_sec"], 0.15, f"正常镜头不许误裁尾: {result}")
        self.assertEqual(result["ng_reasons"], [], f"清晰稳定镜头不该报 NG: {result}")
        self.assertGreaterEqual(result["usable_span_sec"], result["duration_sec"] - 0.1, result)

    def test_3_freeze_tail_shortfall_names_the_clip(self):
        edl = {
            "fps": FPS,
            "total_duration_sec": NG_CLIP_SEC,
            "clips": [_clip("clip_freeze", 0.0, 2.0, 0.0, 1.1)],
        }
        result = CBA.audit_edl_cut_boundaries(str(self.clean_clip), edl)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["checked_clips"], 1)
        freeze = {item["clip_id"]: item for item in result["freeze_tail_clips"]}
        self.assertIn("clip_freeze", freeze, f"必须精准点出该 clip_id: {result}")
        self.assertAlmostEqual(freeze["clip_freeze"]["shortfall_sec"], 0.9, places=2)
        self.assertEqual(result["ng_boundary_clips"], [], "首尾干净时不许乱报 NG 帧")

    def test_4_qa_inspector_passes_clean_video_and_blocks_freeze_edl(self):
        pipeline = self.root / "qa_pipeline"
        pipeline.mkdir()
        video = pipeline / "output_final.mp4"
        shutil.copyfile(self.clean_clip, video)
        write_json(pipeline / "03_audio_events.json", {
            "locked_duration_sec": NG_CLIP_SEC,
            "locked_duration_frames": int(NG_CLIP_SEC * FPS),
            "sentences": [
                {"id": "s1", "text": "门店现场现切", "start_sec": 0.0, "end_sec": 1.0},
                {"id": "s2", "text": "看得见才放心", "start_sec": 1.0, "end_sec": 2.0},
            ],
            "events": [
                {"id": "e1", "type": "sentence_start", "sentence_id": "s1", "time_sec": 0.0, "frame": 0},
                {"id": "e2", "type": "sentence_start", "sentence_id": "s2", "time_sec": 1.0, "frame": 30},
            ],
        })
        normal_edl = {
            "fps": FPS,
            "total_duration_sec": NG_CLIP_SEC,
            "total_frames": int(NG_CLIP_SEC * FPS),
            "clips": [
                dict(_clip("clip_1", 0.0, 1.0, 0.0, 1.0), cut_event_id="e1"),
                dict(_clip("clip_2", 1.0, 2.0, 1.0, 2.0), cut_event_id="e2"),
            ],
        }
        write_json(pipeline / "04_edl.json", normal_edl)
        write_json(pipeline / "05_subtitles.json", {"template_id": "style_14", "entries": []})
        write_json(pipeline / "06_packaging.json", {"visual_elements": []})

        report = QA.inspect_video(pipeline, video, export_mode="mp4")
        passed = next(c for c in report["checks"] if c["id"] == "cut_boundary_cleanliness")
        self.assertEqual(passed["status"], "PASS", passed)
        self.assertEqual(passed["name"], "片段首尾无NG帧与切点无冻帧拖尾")

        freeze_edl = {
            "fps": FPS,
            "total_duration_sec": NG_CLIP_SEC,
            "clips": [_clip("clip_freeze", 0.0, 2.0, 0.0, 1.1)],
        }
        write_json(pipeline / "04_edl.json", freeze_edl)
        report2 = QA.inspect_video(pipeline, video, export_mode="mp4")
        blocked = next(c for c in report2["checks"] if c["id"] == "cut_boundary_cleanliness")
        self.assertEqual(blocked["status"], "FAIL", blocked)
        self.assertFalse(blocked["passed"])
        self.assertIn("clip_freeze", blocked["measured"])

    def test_5_adjacent_high_contrast_hard_cut_does_not_false_trigger_tail_whip(self):
        # 0..1.0s 亮图纹理清晰 -> 1.0s 硬切至纯暗色深底：不得把尾边界跨镜跳变误报为 tail_blur_whip
        joined = self.root / "joined_hard_cut.mp4"
        _render_video(
            joined,
            2.0,
            lambda t: _clean_frame(t) if t < 1.0 else Image.new("RGB", (FRAME_W, FRAME_H), (22, 26, 30)),
        )
        edl = {
            "fps": FPS,
            "total_duration_sec": 2.0,
            "clips": [
                _clip("c1", 0.0, 1.0, 0.0, 1.0),
                _clip("c2", 1.0, 2.0, 0.0, 1.0),
            ],
        }
        res = CBA.audit_edl_cut_boundaries(str(joined), edl)
        self.assertEqual(res["ng_boundary_clips"], [], res)


if __name__ == "__main__":
    unittest.main()
