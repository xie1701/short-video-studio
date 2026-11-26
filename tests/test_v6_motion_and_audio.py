import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_template_overlays as overlays
from common import detect_browser_renderer, find_bin, read_json, write_json
from export_jianying_native import export_native_draft
from step3_lock_audio import SR, save_stereo_wav_np
from step8_mix_audio import apply_music_cues_envelope, run_step8


VARIANTS = (
    {"variant": "negation_line", "text_or_asset": "✕ 唔係呃人"},
    {"variant": "pillar_badge", "text_or_asset": "01"},
    {"variant": "concept_card", "text_or_asset": "口碑見真章",
     "card_lines": ["口碑見真章", "✕ 喊師傅", "✓ 利潤分錢"], "card_has_headline": True},
)
BROWSER = detect_browser_renderer()


def probe_video(path):
    probe = subprocess.run(
        [find_bin("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,pix_fmt,color_space,width,height,nb_frames",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(probe.stdout)["streams"][0]


def alpha_frames(path):
    src = probe_video(path)
    raw = subprocess.run(
        [find_bin("ffmpeg"), "-v", "error", "-i", str(path), "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, src["height"], src["width"], 4)[..., 3]


class V6MotionAndAudioTests(unittest.TestCase):
    def test_both_themes_render_colored_readable_glyphs_and_rounded_alpha(self):
        for sample in VARIANTS:
            renders = []
            for theme in ("store_vibrant", "business_editorial"):
                with self.subTest(variant=sample["variant"], theme=theme):
                    image = overlays.render_kinetic_stage_item_png({**sample, "theme_profile": theme, "font_size_px": 72})
                    self.assertIsNotNone(image)
                    pixels = np.asarray(image)
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(pixels[0, 0, 3], 0)
                    solid = pixels[pixels[..., 3] == 255, :3]
                    self.assertGreater(len(solid), 20)
                    expected = (12, 15, 22) if theme == "business_editorial" else (28, 32, 30)
                    if sample["variant"] == "pillar_badge":
                        self.assertTrue(np.any(np.all(solid == expected, axis=1)))
                    else:
                        self.assertTrue(np.any(np.min(solid, axis=1) > 225))
                    renders.append(pixels)
            self.assertFalse(np.array_equal(*renders))
        elem = {"variant": "negation_line", "theme_profile": "business_editorial"}
        rare = overlays.render_kinetic_stage_item_png({**elem, "text_or_asset": "✕ 𠮶"})
        missing = overlays.render_kinetic_stage_item_png({**elem, "text_or_asset": "✕ \uffff"})
        self.assertFalse(np.array_equal(np.asarray(rare), np.asarray(missing)))

    def test_no_browser_mov_has_alpha_and_damped_motion(self):
        points = [overlays.spring_progress(n / 20) for n in range(21)]
        self.assertEqual((points[0], points[-1]), (0.0, 1.0))
        self.assertGreater(max(points), 1.1)
        self.assertGreater(points[4] - points[0], 5 * abs(points[-1] - points[-5]))

        elem = {**VARIANTS[0], "kind": "kinetic_stage_item", "theme_profile": "business_editorial",
                "render_backend": "web_spring", "direction": "right", "dim_after_sec": 0.48, "dim_alpha": 0.36}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            overlays, "detect_browser_renderer", return_value={"available": False},
        ):
            mov = Path(directory) / "spring.mov"
            result = overlays.render_animated_overlay_mov(elem, mov, duration_sec=0.95, fps=30)
            self.assertIsNotNone(result)
            self.assertEqual(probe_video(mov)["pix_fmt"], "argb")
            alpha = alpha_frames(mov)
            self.assertTrue(np.all(alpha[:, 0, 0] == 0))
            column = np.arange(alpha.shape[2])
            centers = (alpha.sum(axis=1) * column).sum(axis=1) / alpha.sum(axis=(1, 2))
            self.assertGreater(centers[6] - centers[0], 22)
            self.assertLess(abs(centers[-1] - centers[-4]), 0.1)
            self.assertLess(float(alpha[-1].max()), float(alpha[12].max()) * 0.45)

    @unittest.skipUnless(BROWSER.get("available"), "local Chrome and Playwright are unavailable")
    def test_browser_wrapper_and_batch_preserve_alpha_and_regular_h264(self):
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            mov = directory / "web.mov"
            elem = {**VARIANTS[2], "kind": "kinetic_stage_item", "theme_profile": "business_editorial"}
            self.assertIsNotNone(overlays.render_animated_overlay_mov(elem, mov, duration_sec=0.5, fps=24))
            # Only a successful transparent browser encode is cached.
            self.assertTrue(list(directory.glob(".web-spring-*.mov")))
            self.assertEqual(probe_video(mov)["pix_fmt"], "argb")
            alpha = alpha_frames(mov)
            self.assertTrue(np.all(alpha[:, 0, 0] == 0))
            self.assertGreater(alpha[5].max(), 0)

            (directory / "index.html").write_text(
                '<style>html,body{margin:0}</style><div style="background:red;width:24px;height:24px"></div>'
                '<script>window.seek=()=>{};window.READY=true</script>', encoding="utf-8",
            )
            batch_mov, batch_mp4 = directory / "batch.mov", directory / "batch.mp4"
            base = {"project_dir": str(directory), "size": "64x64", "frames": 2, "fps": 24}
            write_json(directory / "batch.json", [
                {**base, "alpha": True, "out": str(batch_mov)}, {**base, "out": str(batch_mp4)},
            ])
            env = {**os.environ, "SVS_CHROME_PATH": BROWSER["chrome_bin"], "FFMPEG_BIN": find_bin("ffmpeg"),
                   "SVS_PLAYWRIGHT_DIR": str(Path(BROWSER["playwright_core"]).parent.parent)}
            process = subprocess.run(
                [BROWSER["node_bin"], BROWSER["bridge_script"], "--batch-json", str(directory / "batch.json")],
                env=env, capture_output=True, text=True, timeout=25,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(probe_video(batch_mov)["pix_fmt"], "argb")
            self.assertTrue(np.all(alpha_frames(batch_mov)[:, -1, -1] == 0))
            mp4 = probe_video(batch_mp4)
            self.assertEqual((mp4["codec_name"], mp4["pix_fmt"], mp4["color_space"]), ("h264", "yuv420p", "bt709"))

    def test_stereo_music_cues_drop_rms_without_a_step_or_phase_change(self):
        time = np.arange(4 * SR) / SR
        channel = 0.22 * np.cos(2 * np.pi * 127 * time)
        bgm = np.column_stack((channel, channel * 0.7)).astype(np.float32)
        cues = [
            {"type": "delay_entry", "start_sec": 0.0, "end_sec": 0.6, "fade_ms": 120},
            {"type": "silence_drop", "start_sec": 1.5, "end_sec": 2.0, "depth_db": -26.0, "fade_ms": 65},
            {"type": "section_swell", "start_sec": 1.25, "end_sec": 2.85, "depth_db": 2.5, "fade_ms": 130},
        ]
        out, metrics = apply_music_cues_envelope(bgm, cues, return_metrics=True)
        self.assertEqual(metrics["applied_count"], 3)
        self.assertEqual(metrics["min_gain_db"], -36.0)

        def rms_gain(start, end):
            lo, hi = int(start * SR), int(end * SR)
            ratio = np.sqrt(np.mean(out[lo:hi] ** 2) / np.mean(bgm[lo:hi] ** 2))
            return float(20 * np.log10(ratio))

        self.assertAlmostEqual(rms_gain(1.5, 2.0), -26.0, places=2)
        self.assertAlmostEqual(rms_gain(0.0, 0.6), -36.0, places=2)
        self.assertAlmostEqual(rms_gain(2.3, 2.75), 2.5, places=2)
        self.assertLess(float(np.max(np.abs(np.diff(out, axis=0)))), 0.05)
        np.testing.assert_allclose(out[:, 1], out[:, 0] * 0.7, atol=1e-6)
        self.assertAlmostEqual(bgm[0, 0], 0.22)
        np.testing.assert_array_equal(apply_music_cues_envelope(bgm, []), bgm)
        _, silent = apply_music_cues_envelope(np.zeros_like(bgm), cues, return_metrics=True)
        self.assertEqual(silent["applied_count"], 0)

    def test_step8_uses_director_file_and_measures_master(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            time = np.arange(4 * SR) / SR
            voice = 0.16 * np.sin(2 * np.pi * 215 * time)
            music = 0.08 * np.sin(2 * np.pi * 145 * time)
            voice_wav = pipeline / "voice.wav"
            bgm_wav = pipeline / "bgm.wav"
            save_stereo_wav_np(voice_wav, np.column_stack((voice, voice)), SR)
            save_stereo_wav_np(bgm_wav, np.column_stack((music, music)), SR)
            write_json(pipeline / "03_audio_events.json", {
                "locked_voice_wav": str(voice_wav), "locked_bgm_wav": str(bgm_wav),
                "locked_duration_sec": 4.0, "sentences": [],
            })
            write_json(pipeline / "06_packaging.json", {"style_id": "style_02"})
            write_json(pipeline / "03b_director_plan.json", {"music_cues": [
                {"type": "silence_drop", "start_sec": 1.5, "end_sec": 2.0, "depth_db": -26.0},
            ]})
            mix = run_step8(pipeline)
            self.assertAlmostEqual(mix["measured_lufs"], -14.0, delta=0.8)
            self.assertGreater(mix["mono_phase_corr"], 0.95)
            self.assertEqual(mix["music_cue_metrics"]["applied_count"], 1)
            self.assertEqual(mix["music_cue_metrics"]["min_gain_db"], -26.0)

    def test_native_export_uses_six_editable_stage_lanes_and_card_text(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory) / "pipeline"
            pipeline.mkdir()
            write_json(pipeline / "04_edl.json", {"clips": [], "fps": 30, "style_id": "style_02", "total_duration_sec": 8.0})
            samples = list(VARIANTS) + [
                {"variant": "bridge_bar", "text_or_asset": "保留同期原话"},
                {"variant": "hero_white", "text_or_asset": "先看前提"},
                {"variant": "danmaku_line", "text_or_asset": "探店原句"},
            ]
            rows = [{**it, "start_sec": i * 0.75, "end_sec": (i + 1) * 0.75, "track_lane": i,
                     "theme_profile": "business_editorial", "font_size_units": 12}
                    for i, it in enumerate(samples)]
            write_json(pipeline / "06_packaging.json", {"kinetic_stage": {"enabled": True, "items": rows}})
            result = export_native_draft(pipeline)
            draft = read_json(Path(result["draft_dir"]) / "draft_content.json")
            self.assertEqual([track["name"] for track in draft["tracks"]], [f"kinetic_T{i}" for i in range(6)])
            texts = {json.loads(material["content"])["text"]: material for material in draft["materials"]["texts"]}
            self.assertIn("口碑見真章\n✕ 喊師傅\n✓ 利潤分錢", texts)
            for label in ("✕ 唔係呃人", "01", "口碑見真章\n✕ 喊師傅\n✓ 利潤分錢"):
                self.assertTrue(texts[label]["is_rich_text"])
                self.assertGreater(texts[label]["background_round_radius"], 0)
                self.assertTrue(texts[label]["background_color"])
            self.assertEqual(texts["01"]["background_color"], "#EAB308")
            self.assertEqual(texts["✕ 唔係呃人"]["text_color"], "#F8FAFC")


if __name__ == "__main__":
    unittest.main()
