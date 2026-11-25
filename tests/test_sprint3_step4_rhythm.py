import statistics
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import find_bin, read_json, run_cmd, write_json
from step4_build_edl import run_step4

FPS = 30
BOUNDARIES = (0, 96, 162, 222, 300, 378, 459, 540)
OPENING_EVENTS = (15, 28, 41)


class Step4RhythmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        media = tempfile.TemporaryDirectory()
        cls.addClassCleanup(media.cleanup)
        cls.media = Path(media.name) / "normalized.mp4"
        run_cmd([
            find_bin("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
            "color=c=gray:s=64x112:r=30:d=22", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(cls.media),
        ])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.pipeline = Path(temp.name)
        self.log = StringIO()

    def _pipeline(self, *, matching=True, opening_events=OPENING_EVENTS, word_across_first_cut=False):
        voice = self.pipeline / "voice.mp4"
        voice.hardlink_to(self.media)
        items = [{
            "id": "voice",
            "role": "a_roll",
            "normalized_mp4": str(voice),
            "duration_sec": 22.0,
            "video_grade": "usable",
            "sharpness_score": 450.0,
            "stability_score": 0.95,
        }]
        for i, tag in enumerate(("鲜羊排", "炭火", "后厨") if matching else ("地铁闸机", "雪山缆车", "证券交易")):
            path = self.pipeline / f"detail_{i}.mp4"
            path.hardlink_to(self.media)
            items.append({
                "id": f"detail_{i}",
                "role": "b_roll",
                "normalized_mp4": str(path),
                "duration_sec": 22.0,
                "video_grade": "usable",
                "description": f"{tag}实拍近景",
                "semantic_tags": [tag],
                "safe_in_sec": 0.75,
                "safe_out_sec": 20.0,
                "sharpness_score": 480.0,
                "stability_score": 0.92,
            })

        sentences, events = [], []
        for i, (start, end) in enumerate(zip(BOUNDARIES, BOUNDARIES[1:])):
            sid = f"sent_{i}"
            src_start, src_end = start / FPS, end / FPS
            accents = opening_events if i == 0 else (start + 23, end - 21)
            words = [{"word": "鲜羊排", "start_sec": frame / FPS, "end_sec": (frame + 10) / FPS}
                     for frame in accents]
            if i == 0 and word_across_first_cut:
                words[0] = {"word": "鲜羊排", "start_sec": 10 / FPS, "end_sec": 22 / FPS}
            sentences.append({
                "id": sid,
                "text": "后厨用炭火慢烤鲜羊排，熟透后再切片",
                "keywords": ["炭火", "鲜羊排"],
                "words": words,
                "start_frame": start,
                "end_frame": end,
                "start_sec": src_start,
                "end_sec": src_end,
                "source_media": str(voice),
                "source_in_sec": src_start,
                "source_out_sec": src_end,
                "a_roll_locked": i == 4,
            })
            for frame, kind in [(start, "sentence_start"), *((frame, "keyword_accent") for frame in accents)]:
                events.append({
                    "id": f"event_{frame}",
                    "type": kind,
                    "sentence_id": sid,
                    "frame": frame,
                    "time_sec": frame / FPS,
                })
        self.audio = {
            "locked_duration_sec": BOUNDARIES[-1] / FPS,
            "locked_duration_frames": BOUNDARIES[-1],
            "sentences": sentences,
            "events": events,
        }
        write_json(self.pipeline / "01_manifest.json", {"items": items})
        write_json(self.pipeline / "03_audio_events.json", self.audio)
        self.live_media = {m["normalized_mp4"]: m for m in items if m["role"] == "b_roll"}

    def _run(self, style_id, **kwargs):
        with redirect_stdout(self.log), patch("face_framing.anchor_for", return_value=None):
            edl = run_step4(self.pipeline, style_id=style_id, fps=FPS, **kwargs)
        stored = read_json(self.pipeline / "04_edl.json")
        self.assertEqual(edl["choreography_applied"], stored["choreography_applied"])
        self.assertEqual(self.audio, read_json(self.pipeline / "03_audio_events.json"))
        return stored

    def _assert_event_aligned(self, edl):
        clips = edl["clips"]
        self.assertGreater(len(clips), 1)
        self.assertEqual(clips[0]["timeline_in_frame"], 0)
        self.assertEqual(clips[-1]["timeline_out_frame"], self.audio["locked_duration_frames"])
        events = {e["id"]: e for e in self.audio["events"]}
        for previous, clip in zip(clips, clips[1:]):
            frame = events[clip["cut_event_id"]]["frame"]
            self.assertEqual(clip["cut_event_frame"], frame, clip)
            self.assertEqual(previous["timeline_out_frame"], clip["timeline_in_frame"], clip)
            self.assertLessEqual(abs(clip["timeline_in_frame"] - clip["j_cut_lead_frames"] - frame), 1, clip)

    def _assert_original_speed(self, edl):
        voice_clips = [c for c in edl["clips"] if c["track_role"] == "a_roll"]
        self.assertTrue(voice_clips)
        for clip in voice_clips:
            self.assertEqual(clip["speed"], 1.0, clip)
            self.assertTrue(clip["underlying_audio_untouched"], clip)
            self.assertFalse(clip["freeze_tail"], clip)

    def _live_path(self, clip):
        if clip["track_role"] == "b_roll":
            return clip["media_path"]
        return (clip.get("pip_layout") or {}).get("secondary_media_path")

    def test_style21_applies_fast_cuts_matched_footage_and_punch_in(self):
        self._pipeline()
        edl = self._run("style_21")
        self.assertEqual(edl["choreography_applied"], {
            "style_id": "style_21",
            "enabled": True,
            "broll_target_ratio": 0.756,
            "punch_in_scale": 1.2,
            "opening_cuts_3s_target": 4,
            "aroll_speed_locked": True,
            "planned_degrade_reason": None,
        })
        opening = [c for c in edl["clips"] if c["timeline_in_frame"] < 3 * FPS]
        self.assertGreaterEqual(len(opening), 4)
        matched = [c for c in opening if self._live_path(c) in self.live_media]
        self.assertTrue(matched, opening)
        for clip in matched:
            medium = self.live_media[self._live_path(clip)]
            self.assertTrue(any(tag in clip["sentence_text"] for tag in medium["semantic_tags"]), clip)
            layout = clip.get("pip_layout") or {}
            self.assertGreaterEqual(layout.get("secondary_in_sec", clip["source_in_sec"]), medium["safe_in_sec"])
        inserted_sec = sum(c["duration_sec"] for c in edl["clips"] if self._live_path(c) in self.live_media)
        self.assertAlmostEqual(edl["broll_ratio_actual"], inserted_sec / edl["total_duration_sec"], places=3)
        self.assertGreaterEqual(edl["broll_ratio_actual"], 0.55)
        self.assertLessEqual(abs(edl["broll_ratio_actual"] - 0.756), 0.15)
        median = statistics.median(c["duration_sec"] for c in edl["clips"])
        self.assertAlmostEqual(median, 1.15, delta=0.35)
        punched = [c for c in edl["clips"] if c["punch_in_zoom"] > 1.0]
        self.assertTrue(punched)
        for clip in punched:
            self.assertGreaterEqual(clip["punch_in_zoom"], 1.18)
            self.assertLessEqual(clip["punch_in_zoom"], 1.22)
            self.assertTrue(clip["vf_extra"])
        self._assert_original_speed(edl)
        self._assert_event_aligned(edl)

    def test_style01_keeps_the_presenter_in_the_opening_and_uses_its_own_quota(self):
        self._pipeline()
        edl = self._run("style_01")
        self.assertEqual(edl["choreography_applied"], {
            "style_id": "style_01",
            "enabled": True,
            "broll_target_ratio": 0.572,
            "punch_in_scale": 1.0,
            "opening_cuts_3s_target": 3,
            "aroll_speed_locked": True,
            "planned_degrade_reason": None,
        })
        opening = [c for c in edl["clips"] if c["timeline_in_frame"] < round(3.2 * FPS)]
        self.assertGreaterEqual(len(opening), 3)
        self.assertTrue(all(c["track_role"] == "a_roll" and self._live_path(c) is None for c in opening))
        self.assertGreaterEqual(edl["broll_ratio_actual"], 0.45)
        self.assertLessEqual(edl["broll_ratio_actual"], 0.68)
        self.assertTrue(all(c["punch_in_zoom"] == 1.0 for c in edl["clips"]))
        self._assert_original_speed(edl)
        self._assert_event_aligned(edl)

    def test_mismatch_waiver_keeps_usable_voice_and_no_unrelated_footage(self):
        self._pipeline(matching=False)
        edl = self._run("style_21", allow_broll_mismatch=True, enforce_broll_gate=True)
        self.assertTrue(edl["choreography_applied"]["planned_degrade_reason"])
        self.assertTrue(edl["broll_semantic"]["gate"]["waived"])
        self.assertFalse(edl["broll_semantic"]["gate"]["raw_pass"])
        self.assertEqual(edl["broll_ratio_actual"], 0.0)
        for clip in edl["clips"]:
            self.assertEqual(clip["track_role"], "a_roll", clip)
            self.assertNotIn(self._live_path(clip), self.live_media, clip)
        self._assert_event_aligned(edl)
        self._assert_original_speed(edl)

    def test_opening_broll_waits_for_a_word_boundary(self):
        self._pipeline(word_across_first_cut=True)
        edl = self._run("style_21")
        before_word_ends = [c for c in edl["clips"] if c["timeline_in_frame"] < 22]
        self.assertTrue(all(c["track_role"] == "a_roll" and self._live_path(c) is None for c in before_word_ends))
        self._assert_event_aligned(edl)

    def test_sparse_opening_uses_only_available_events(self):
        self._pipeline(opening_events=(28,))
        edl = self._run("style_21")
        self.assertEqual(edl["first_3s_visual_changes"], 1)
        self._assert_event_aligned(edl)

    def test_style02_keeps_kinetic_stage_and_does_not_import_choreography(self):
        self._pipeline()
        with patch.dict(sys.modules, {"style_01_21_choreographer": None}):
            edl = self._run("style_02", track="creator")
        self.assertIsNone(edl["choreography_applied"])
        self.assertTrue(edl["kinetic_stage_enabled"])
        self.assertTrue(edl["kinetic_stage_stats"])
        self.assertNotIn("风格节奏契约加载失败", self.log.getvalue())
        self.assertLessEqual(edl["broll_ratio_actual"], 0.30)
        self.assertTrue(all(c["track_role"] == "a_roll" for c in edl["clips"] if c["timeline_in_frame"] < 3 * FPS))

    def test_unavailable_choreography_explicitly_falls_back(self):
        self._pipeline()
        with patch.dict(sys.modules, {"style_01_21_choreographer": None}):
            edl = self._run("style_21")
        meta = edl["choreography_applied"]
        self.assertEqual(meta["style_id"], "style_21")
        self.assertIs(meta["enabled"], False)
        self.assertIn(meta["planned_degrade_reason"], self.log.getvalue())
        self._assert_event_aligned(edl)


if __name__ == "__main__":
    unittest.main()
