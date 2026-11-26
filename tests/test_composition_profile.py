"""Offline and synthetic-frame checks for A-roll composition profiles."""

import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import composition_profile as composition
from common import read_json, write_json

CLOSE_FACE = {"x": 0.33, "y": 0.14, "w": 0.34, "h": 0.30}
WIDE_FACE = {"x": 0.42, "y": 0.32, "w": 0.16, "h": 0.16}


def checkerboard(width, height, tile=8, dark=20, light=236):
    y, x = np.ogrid[:height, :width]
    pixels = np.where((y // tile + x // tile) % 2, light, dark)
    return Image.fromarray(pixels.astype(np.uint8))


def aroll(path, bbox, duration=25.0, **metadata):
    return {
        "role": "a_roll",
        "normalized_mp4": path,
        "duration_sec": duration,
        "faces": [{"bbox": bbox}],
        **metadata,
    }


class FrameCompositionTests(unittest.TestCase):
    def test_closeup_uses_dark_chest_stage(self):
        frame = Image.new("L", (300, 520), 205)
        frame.paste(checkerboard(300, 280), (0, 0))
        frame.paste(180, (99, 72, 201, 229))
        frame.paste(0, (66, 301, 234, 427))

        profile = composition.analyze_frame_composition(frame, CLOSE_FACE)

        self.assertEqual(profile["recommended_stage_mode"], "closeup_chest_stage")
        self.assertIs(profile["allow_behind_person"], False)
        self.assertAlmostEqual(profile["face_area_ratio"], 0.102)
        self.assertAlmostEqual(profile["headroom_ratio"], 0.14)
        self.assertGreaterEqual(profile["chest_dark_ratio"], 0.70)
        self.assertGreaterEqual(profile["bg_complexity"], 0.35)
        self.assertTrue(-0.58 <= profile["safe_fg_y_norm"] <= -0.32)
        self.assertGreater((1 - profile["safe_fg_y_norm"]) / 2, CLOSE_FACE["y"] + CLOSE_FACE["h"])

    def test_wide_with_clean_background_ignores_texture_inside_face(self):
        frame = Image.new("L", (300, 500), 225)
        frame.paste(checkerboard(48, 80, tile=2), (126, 160))

        profile = composition.analyze_frame_composition(frame, WIDE_FACE)

        self.assertEqual(profile["recommended_stage_mode"], "wide_behind_person")
        self.assertIs(profile["allow_behind_person"], True)
        self.assertAlmostEqual(profile["face_area_ratio"], 0.0256)
        self.assertAlmostEqual(profile["headroom_ratio"], 0.32)
        self.assertEqual(profile["chest_dark_ratio"], 0.0)
        self.assertLess(profile["bg_complexity"], 0.05)

    def test_stage_thresholds_independently(self):
        plain = Image.new("L", (180, 320), 210)
        cluttered = checkerboard(180, 320)
        cases = [
            (plain, {**CLOSE_FACE, "y": 0.30, "h": 0.25}, False),  # Area is exactly 0.085.
            (plain, {**WIDE_FACE, "y": 0.0}, True),
            (plain, {**WIDE_FACE, "y": 0.21}, False),
            (plain, {**WIDE_FACE, "y": 0.22}, True),
            (cluttered, {**WIDE_FACE, "y": 0.24}, False),
            (cluttered, {**WIDE_FACE, "y": 0.28}, True),
        ]
        for frame, bbox, allow_behind in cases:
            with self.subTest(bbox=bbox, allow_behind=allow_behind):
                profile = composition.analyze_frame_composition(frame, bbox)
                self.assertIs(profile["allow_behind_person"], allow_behind)

    def test_no_face_uses_fixed_chest_band_and_checks_local_texture(self):
        frame = Image.new("L", (200, 300), 230)
        frame.paste(30, (44, 174, 156, 246))
        plain = composition.analyze_frame_composition(frame)
        frame.paste(checkerboard(112, 72, tile=2, dark=20, light=90), (44, 174))
        textured = composition.analyze_frame_composition(frame)

        self.assertEqual(plain["face_area_ratio"], 0.0)
        self.assertEqual(plain["headroom_ratio"], 0.30)
        self.assertEqual(plain["safe_center_shift_x"], 0.0)
        self.assertTrue(plain["allow_behind_person"])
        self.assertGreaterEqual(plain["chest_dark_ratio"], 0.95)
        self.assertLess(textured["chest_dark_ratio"], 0.1)

    def test_low_face_and_horizontal_shift_margin(self):
        frame = Image.new("L", (180, 320), 195)
        face_left = {"x": 0.02, "y": 0.42, "w": 0.23, "h": 0.34}
        left = composition.analyze_frame_composition(frame, face_left)
        right = composition.analyze_frame_composition(frame, {**face_left, "x": 0.75})

        self.assertGreater((1 - left["safe_fg_y_norm"]) / 2, face_left["y"] + face_left["h"])
        self.assertGreaterEqual(left["safe_fg_y_norm"], -0.56)
        self.assertGreater(left["safe_center_shift_x"], 0.0)
        self.assertAlmostEqual(left["safe_center_shift_x"], -right["safe_center_shift_x"])


class ArollCompositionTests(unittest.TestCase):
    def test_samples_at_twenty_and_sixty_percent_and_uses_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "take.mp4"
            media.touch()
            responses = []
            for luma in (0, 200):
                with BytesIO() as buffer:
                    Image.new("L", (180, 320), luma).save(buffer, format="PNG")
                    responses.append(subprocess.CompletedProcess([], 0, buffer.getvalue()))
            with patch.object(composition.subprocess, "run", side_effect=responses) as run, \
                 patch.object(composition.face_framing, "anchor_for") as anchor:
                profile = composition.analyze_aroll_item(aroll(media, CLOSE_FACE, duration=10.0), "ffmpeg-test")

        self.assertEqual(profile["media_path"], str(media))
        self.assertEqual(profile["sampled_frames"], 2)
        self.assertAlmostEqual(profile["chest_dark_ratio"], 0.5)
        self.assertEqual([call.args[0][:6] for call in run.call_args_list], [
            ["ffmpeg-test", "-nostdin", "-v", "error", "-ss", "2.000"],
            ["ffmpeg-test", "-nostdin", "-v", "error", "-ss", "6.000"],
        ])
        anchor.assert_not_called()

    def test_source_only_one_sample_with_optional_face_detector(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "source.mov"
            media.touch()
            item = {"source_path": media, "duration_sec": 4.0}
            image = Image.new("L", (180, 320), 180)
            anchor_data = {"cx": 0.5, "cy": 0.29, "w": 0.34, "h": 0.30}
            with patch.object(composition, "_sample_frame", side_effect=[None, image]), \
                 patch.object(composition.face_framing, "anchor_for", return_value=anchor_data) as anchor:
                profile = composition.analyze_aroll_item(item)
            anchor.assert_called_once_with(str(media), 2.0, 4.0, cache_scope=str(media.resolve()))
            self.assertEqual(profile["sampled_frames"], 1)
            self.assertAlmostEqual(profile["headroom_ratio"], 0.14)
            self.assertIs(profile["allow_behind_person"], False)

            with patch.object(composition, "_sample_frame", side_effect=[image, None]), \
                 patch.object(composition.face_framing, "anchor_for", side_effect=ImportError):
                without_vision = composition.analyze_aroll_item(item)
            self.assertEqual(without_vision["sampled_frames"], 1)
            self.assertEqual(without_vision["face_area_ratio"], 0.0)

    def test_missing_media_and_decoder_timeout_use_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "offline.mp4"
            item = aroll(media, CLOSE_FACE)
            missing = composition.analyze_aroll_item(item)
            media.touch()
            with patch.object(composition.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 10)):
                timed_out = composition.analyze_aroll_item(item)

        self.assertEqual(timed_out, missing)
        self.assertEqual(missing["sampled_frames"], 0)
        self.assertIs(missing["allow_behind_person"], False)
        self.assertAlmostEqual(missing["face_area_ratio"], 0.102)
        self.assertEqual(missing["chest_dark_ratio"], 0.0)
        self.assertEqual(missing["bg_complexity"], 0.0)


class PipelineCompositionTests(unittest.TestCase):
    def test_offline_manifest_writes_single_talker_and_multi_camera_profiles(self):
        cases = [
            ([aroll("host.mp4", CLOSE_FACE)], "closeup_chest_stage", "business_editorial"),
            ([aroll("wide_host.mp4", WIDE_FACE)], "wide_behind_person", "business_editorial"),
            ([aroll(f"store_{i}.mp4", WIDE_FACE) for i in range(3)], "wide_behind_person", "store_vibrant"),
        ]
        for items, stage, theme in cases:
            with self.subTest(theme=theme), tempfile.TemporaryDirectory() as tmp:
                pipeline_dir = Path(tmp)
                write_json(pipeline_dir / "01_manifest.json", {"items": items})

                profile = composition.build_composition_profile(pipeline_dir)

                self.assertEqual(profile, read_json(pipeline_dir / composition.PROFILE_FILENAME))
                self.assertEqual(profile["step"], "01b_composition_profile")
                self.assertEqual(profile["primary_stage_mode"], stage)
                self.assertEqual(profile["theme_hint"], theme)
                self.assertIs(profile["allow_behind_person"], stage == "wide_behind_person")
                self.assertEqual([row["media_path"] for row in profile["aroll_profiles"]],
                                 [item["normalized_mp4"] for item in items])
                self.assertTrue(all(row["sampled_frames"] == 0 for row in profile["aroll_profiles"]))

    def test_primary_stage_uses_aroll_duration_majority_or_count_fallback(self):
        cases = (
            ("wide majority", (30, 30, 25), (15,), True),
            ("closeup duration majority", (5, 5, 5), (85,), False),
            ("equal duration", (10,), (10,), False),
            ("fractional duration tie", (0.1, 0.2), (0.3,), False),
            ("missing duration", (5, None, 5), (85,), True),
            ("no aroll", (), (), False),
        )
        for case, wide_durations, closeup_durations, allow_behind in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                pipeline_dir = Path(tmp)
                items = [
                    aroll(f"wide_{i}.mp4", WIDE_FACE, duration=duration)
                    for i, duration in enumerate(wide_durations)
                ]
                items.extend(
                    aroll(f"closeup_{i}.mp4", CLOSE_FACE, duration=duration)
                    for i, duration in enumerate(closeup_durations)
                )
                write_json(pipeline_dir / "01_manifest.json", {"items": items})

                profile = composition.build_composition_profile(pipeline_dir)

                self.assertIs(profile["allow_behind_person"], allow_behind)
                stage = "wide_behind_person" if allow_behind else "closeup_chest_stage"
                self.assertEqual(profile["primary_stage_mode"], stage)
                self.assertEqual(
                    [row["allow_behind_person"] for row in profile["aroll_profiles"]],
                    [True] * len(wide_durations) + [False] * len(closeup_durations),
                )

    def test_prefers_usable_aroll_then_falls_back_to_all_video_takes(self):
        items = [
            aroll("clean.mp4", WIDE_FACE, usable=True),
            aroll("cropped.mp4", CLOSE_FACE, usable=False),
            {"role": "a_roll", "source_path": "photo.png", "usable": True},
            {"role": "b_roll", "normalized_mp4": "cutaway.mp4", "usable": True},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pipeline_dir = Path(tmp)
            manifest = pipeline_dir / "01_manifest.json"
            write_json(manifest, {"items": items})
            profile = composition.build_composition_profile(pipeline_dir)
            self.assertEqual([row["media_path"] for row in profile["aroll_profiles"]], ["clean.mp4"])
            self.assertTrue(profile["allow_behind_person"])

            items[0]["usable"] = False
            write_json(manifest, {"items": items})
            self.assertEqual(composition.build_composition_profile(pipeline_dir, overwrite=False), profile)
            rebuilt = composition.build_composition_profile(pipeline_dir)
            self.assertEqual([row["media_path"] for row in rebuilt["aroll_profiles"]], ["clean.mp4", "cropped.mp4"])
            self.assertFalse(rebuilt["allow_behind_person"])
            self.assertEqual(rebuilt["theme_hint"], "store_vibrant")

    def test_two_long_closeups_use_business_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline_dir = Path(tmp)
            write_json(pipeline_dir / "01_manifest.json", {"items": [
                aroll("one.mp4", CLOSE_FACE),
                aroll("two.mp4", CLOSE_FACE),
            ]})

            profile = composition.build_composition_profile(pipeline_dir)

        self.assertEqual(profile["theme_hint"], "business_editorial")

    def test_inventory_supplies_source_face_geometry_and_cutaway_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline_dir = Path(tmp)
            item = aroll("vertical.mp4", None, source_path="original.mov", crop_transform={
                "scaled_w": 200, "scaled_h": 200, "crop_x": 50, "crop_y": 0, "dst_w": 100, "dst_h": 200,
            })
            write_json(pipeline_dir / "01_manifest.json", {"items": [item]})
            write_json(pipeline_dir / "00_inventory.json", {"clips": [
                {"path": "original.mov", "faces": [{"bbox": {"x": 0.4, "y": 0.32, "w": 0.18, "h": 0.25}}]},
                {"path": "cutaway.mov", "suggested_role": "b_roll_candidate", "usable": True},
            ]})

            profile = composition.build_composition_profile(pipeline_dir)

        self.assertAlmostEqual(profile["aroll_profiles"][0]["face_area_ratio"], 0.09)
        self.assertEqual(profile["primary_stage_mode"], "closeup_chest_stage")
        self.assertEqual(profile["theme_hint"], "store_vibrant")


    def test_cropped_out_inventory_face_is_not_used_for_normalized_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline_dir = Path(tmp)
            item = {
                "role": "a_roll", "normalized_mp4": "vertical.mp4", "source_path": "original.mov",
                "crop_transform": {
                    "scaled_w": 200, "scaled_h": 200, "crop_x": 50, "crop_y": 0, "dst_w": 100, "dst_h": 200,
                },
            }
            write_json(pipeline_dir / "01_manifest.json", {"items": [item]})
            write_json(pipeline_dir / "00_inventory.json", {"clips": [
                {"path": "original.mov", "faces": [{"bbox": {"x": 0.02, "y": 0.17, "w": 0.20, "h": 0.40}}]},
            ]})

            profile = composition.build_composition_profile(pipeline_dir)

        self.assertEqual(profile["aroll_profiles"][0]["face_area_ratio"], 0.0)
        self.assertEqual(profile["primary_stage_mode"], "wide_behind_person")


if __name__ == "__main__":
    unittest.main()
