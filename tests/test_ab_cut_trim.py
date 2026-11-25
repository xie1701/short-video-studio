import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

from common import write_json
from kinetic_choreographer import plan_kinetic_stage
from step4_build_edl import run_step4

FPS = 30


class AbCutTrimTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        render_card = patch("step4_build_edl.render_broll_clip_pillow")
        render_card.start()
        self.addCleanup(render_card.stop)

    def _media(self, name, role, duration, **fields):
        mp4 = self.root / f"{name}.mp4"
        mp4.touch()
        return {"id": name, "role": role, "normalized_mp4": str(mp4),
                "duration_sec": duration, **fields}

    def _broll(self, **fields):
        return self._media("booths", "b_roll", fields.pop("duration_sec", 6.0),
                           description="大厅的卡座近景与座位布置", semantic_tags=["卡座"], **fields)

    def _sent(self, sid, media, start, end, text="镜头转向大厅的卡座区域", **fields):
        return {"id": sid, "text": text, "keywords": [], "source_media": media["normalized_mp4"],
                "start_sec": start, "end_sec": end, "start_frame": round(start * FPS),
                "end_frame": round(end * FPS), "source_in_sec": start, "source_out_sec": end, **fields}

    def _pipeline(self, name, sentences, items):
        pipe = self.root / name
        pipe.mkdir()
        events = [
            {"id": f"begin_{s['id']}", "type": "sentence_start", "sentence_id": s["id"],
             "time_sec": s["start_sec"], "frame": s["start_frame"]}
            for s in sentences
        ]
        audio = {
            "locked_duration_sec": sentences[-1]["end_sec"],
            "locked_duration_frames": sentences[-1]["end_frame"],
            "sentences": sentences,
            "events": events,
        }
        write_json(pipe / "01_manifest.json", {"items": items})
        write_json(pipe / "03_audio_events.json", audio)
        return pipe, audio

    def _assert_cut_event_alignment(self, edl, audio):
        events_by_id = {e["id"]: e for e in audio["events"]}
        self.assertGreater(len(edl["clips"]), 1)
        self.assertEqual(edl["clips"][0]["timeline_in_frame"], 0)
        self.assertEqual(edl["clips"][-1]["timeline_out_frame"], audio["locked_duration_frames"])
        for previous, clip in zip(edl["clips"], edl["clips"][1:]):
            frame = events_by_id[clip["cut_event_id"]]["frame"]
            self.assertEqual(previous["timeline_out_frame"], clip["timeline_in_frame"], clip)
            self.assertEqual(clip["cut_event_frame"], frame, clip)
            self.assertLessEqual(abs(clip["timeline_in_frame"] - clip["j_cut_lead_frames"] - frame), 1, clip)

    def test_manifest_and_optional_audit_keep_reused_broll_inside_safe_window(self):
        for use_manifest in (True, False):
            with self.subTest(use_manifest=use_manifest):
                bounds = {"safe_in_sec": 0.8, "safe_out_sec": 5.2}
                broll = self._broll(**bounds if use_manifest else {})
                code = self._media("card", "code_broll", 8.0, semantic_tags=["统计图"])
                voice = self._media("voice", "a_roll", 12.0)
                sentences = [self._sent(f"s{i}", voice, 3.0 * i, 3.0 * (i + 1)) for i in range(3)]
                pipe, audio = self._pipeline(f"safe_{use_manifest}", sentences, [voice, broll, code])
                audit_module = ModuleType("clip_boundary_audit")
                audit = Mock(return_value=bounds)
                audit_module.analyze_clip_safe_window = audit
                with patch.dict(sys.modules, {"clip_boundary_audit": audit_module}):
                    edl = run_step4(pipe, style_id="style_01", grammar="dynamic", prefer_broll_only=True)

                if use_manifest:
                    audit.assert_not_called()
                else:
                    audit.assert_called_once_with(broll["normalized_mp4"])
                shots = [c for c in edl["clips"] if c["media_path"] == broll["normalized_mp4"]]
                self.assertGreaterEqual(len(shots), 2, edl["clips"])
                for clip in shots:
                    self.assertEqual(clip["boundary_trim_reason"], "manifest" if use_manifest else "audit_safe_window")
                    self.assertGreaterEqual(clip["source_in_sec"], 0.8, clip)
                    self.assertGreater(clip["source_out_sec"], clip["source_in_sec"], clip)
                    self.assertLessEqual(clip["source_out_sec"], 5.2, clip)
                    self.assertFalse(clip["freeze_tail"], clip)
                self._assert_cut_event_alignment(edl, audio)
                self.assertTrue(any(c["j_cut_lead_frames"] > 0 for c in edl["clips"][1:]))

    def test_default_handles_skip_shaky_heads_and_center_short_footage(self):
        voice = self._media("voice", "a_roll", 4.0)
        sentence = self._sent("s1", voice, 0.0, 1.5)
        for name, duration, stability, head, tail in (
            ("steady", 6.0, 0.95, 0.65, 0.50),
            ("shaky", 6.0, 0.65, 0.90, 0.50),
            ("short", 2.1, 0.95, 0.30, 0.30),
        ):
            with self.subTest(name=name):
                broll = self._broll(duration_sec=duration, stability_score=stability)
                pipe, _ = self._pipeline(name, [sentence], [voice, broll])
                with patch.dict(sys.modules, {"clip_boundary_audit": None}):
                    edl = run_step4(pipe, style_id="style_01", grammar="dynamic", prefer_broll_only=True)
                clip, = edl["clips"]
                self.assertEqual(clip["media_path"], broll["normalized_mp4"])
                self.assertEqual(clip["boundary_trim_reason"], "default_handle_margin")
                self.assertGreaterEqual(clip["source_in_sec"], head - 0.0001)
                self.assertLessEqual(clip["source_out_sec"], duration - tail + 0.0001)
                self.assertFalse(clip["freeze_tail"])

    def test_short_aroll_uses_matching_broll_and_preserves_a_full_take(self):
        voice = self._media("voice", "a_roll", 6.0)
        short_voice = {**voice, "excluded_windows": [{"start_sec": 0.60, "end_sec": 2.0}]}
        broll = self._broll(safe_in_sec=0.8, safe_out_sec=5.2)
        for name, source, source_out, role in (
            ("short_take", short_voice, 0.45, "b_roll"),
            ("full_take", voice, 1.50, "a_roll"),
        ):
            with self.subTest(name=name):
                sentence = self._sent("s1", source, 0.0, 1.5, source_out_sec=source_out)
                pipe, _ = self._pipeline(name, [sentence], [source, broll])
                clip, = run_step4(pipe, style_id="style_01", grammar="dynamic")["clips"]
                self.assertEqual(clip["track_role"], role, clip)
                self.assertTrue(clip["underlying_audio_untouched"], clip)
                self.assertFalse(clip["freeze_tail"], clip)
                available = (clip["source_out_sec"] - clip["source_in_sec"]) / clip.get("speed", 1.0)
                self.assertGreaterEqual(available, 1.5 - 1.0 / FPS, clip)
                if role == "b_roll":
                    self.assertEqual(clip["media_path"], broll["normalized_mp4"])
                    self.assertIn(clip["sem_match"]["tier"], ("hit", "weak"))
                else:
                    self.assertEqual(clip.get("speed", 1.0), 1.0, clip)

    def test_unfilled_aroll_stays_at_original_speed_and_warns(self):
        voice = self._media("voice", "a_roll", 6.0)
        short_voice = {**voice, "excluded_windows": [{"start_sec": 0.60, "end_sec": 2.0}]}
        broll = self._broll(safe_in_sec=0.8, safe_out_sec=5.2)
        for name, source, locked, items, expected_out in (
            ("locked_take", short_voice, True, [short_voice, broll], 0.60),
            ("without_broll", voice, False, [voice], 1.45),
        ):
            with self.subTest(name=name):
                sentence = self._sent("s1", source, 0.0, 1.5, source_out_sec=0.45, a_roll_locked=locked)
                pipe, _ = self._pipeline(name, [sentence], items)
                output = StringIO()
                with redirect_stdout(output):
                    clip, = run_step4(pipe, style_id="style_01", grammar="dynamic")["clips"]
                self.assertEqual(clip["track_role"], "a_roll", clip)
                self.assertTrue(clip["underlying_audio_untouched"], clip)
                self.assertEqual(clip.get("speed", 1.0), 1.0, clip)
                self.assertAlmostEqual(clip["source_in_sec"], 0.0)
                self.assertAlmostEqual(clip["source_out_sec"], expected_out)
                self.assertTrue(clip["freeze_tail"], clip)
                self.assertIn(f"[WARN] clip_001 的可用画面仍短 {1.5 - expected_out:.2f}s", output.getvalue())

    def test_aroll_uses_natural_tail_without_overlapping_next_sentence(self):
        voice = self._media("voice", "a_roll", 8.0)
        for name, speech_end, next_source_in, freeze_tail in (
            ("short_pause", 1.3, 3.5, False),
            ("long_pause", 1.0, 3.5, False),
            ("reserved_source", 1.0, 3.2, True),
        ):
            with self.subTest(name=name):
                sentences = [
                    self._sent("s1", voice, 0.0, speech_end, "今天说说我们怎么选座位",
                               source_in_sec=2.0, source_out_sec=2.0 + speech_end),
                    self._sent("s2", voice, 1.5, 3.0, "这里坐下来能看清走道",
                               source_in_sec=next_source_in, source_out_sec=next_source_in + 1.5),
                ]
                pipe, audio = self._pipeline(name, sentences, [voice])
                edl = run_step4(pipe, style_id="style_02", grammar="dynamic")
                first, second = edl["clips"]
                self.assertEqual([first["track_role"], second["track_role"]], ["a_roll", "a_roll"])
                self.assertAlmostEqual(first["source_in_sec"], 2.0)
                self.assertAlmostEqual(first["source_out_sec"], next_source_in)
                self.assertGreaterEqual(second["source_in_sec"], first["source_out_sec"])
                self.assertEqual(first["freeze_tail"], freeze_tail, first)
                self.assertEqual([c.get("speed", 1.0) for c in edl["clips"]], [1.0, 1.0])
                if not freeze_tail:
                    self.assertAlmostEqual(first["source_out_sec"] - first["source_in_sec"], first["duration_sec"])
                self._assert_cut_event_alignment(edl, audio)

    def test_style02_switches_onto_and_off_plain_bridge_on_the_event_frame(self):
        voice = self._media("voice", "a_roll", 15.0)
        broll = self._broll(safe_in_sec=0.8, safe_out_sec=5.2)
        sentences = [
            self._sent("intro", voice, 0.0, 3.2, "今天和大家聊聊我们是怎么布置店里的"),
            self._sent("bridge", voice, 3.2, 5.2, keywords=["卡座"]),
            self._sent("anchor", voice, 5.2, 8.4, "很多人觉得开店太难太贵赚不到钱", keywords=["开店"]),
            self._sent("close", voice, 8.8, 11.8, "欢迎大家在评论区聊聊你怎么想"),
        ]
        pipe, audio = self._pipeline("kinetic", sentences, [voice, broll])
        plan = plan_kinetic_stage(audio, style_id="style_02")
        groups = {sid: g for g in plan["groups"] for sid in g["sentence_ids"]}
        self.assertTrue(groups["intro"]["a_roll_locked"])
        self.assertEqual(groups["bridge"]["pattern"], "plain_bridge")
        self.assertEqual(groups["anchor"]["pattern"], "hero_anchor_plus_danmaku")
        edl = run_step4(pipe, style_id="style_02", grammar="dynamic")
        switches = [
            (p["track_role"], c["track_role"], c)
            for p, c in zip(edl["clips"], edl["clips"][1:]) if p["track_role"] != c["track_role"]
        ]
        self.assertEqual([(a, b) for a, b, _ in switches], [("a_roll", "b_roll"), ("b_roll", "a_roll")], edl["clips"])
        for _, _, clip in switches:
            self.assertEqual(clip["j_cut_lead_frames"], 0, clip)
            self.assertEqual(clip["timeline_in_frame"], clip["cut_event_frame"], clip)
        self.assertEqual([c["sentence_id"] for _, _, c in switches], ["bridge", "anchor"])
        self._assert_cut_event_alignment(edl, audio)

    def test_non_kinetic_offsets_stay_available_outside_group_boundaries(self):
        voice = self._media("voice", "a_roll", 25.0)
        broll = self._broll(duration_sec=18.0, safe_in_sec=0.8, safe_out_sec=17.2)
        for with_groups, lead in ((False, -3), (True, 0)):
            with self.subTest(with_groups=with_groups):
                sentences = [
                    self._sent(f"s{i}", voice, i * 3.2, (i + 1) * 3.2,
                               keywords=["卡座"], beat_group=f"group{i}" if with_groups else None)
                    for i in range(4)
                ]
                pipe, audio = self._pipeline(f"intercut_{with_groups}", sentences, [voice, broll])
                edl = run_step4(pipe, style_id="style_04", grammar="dynamic")
                clips = edl["clips"]
                self.assertEqual([c["track_role"] for c in clips], ["a_roll", "a_roll", "b_roll", "a_roll"])
                self.assertEqual([c["j_cut_lead_frames"] for c in clips], [0, 0, lead, 0])
                self._assert_cut_event_alignment(edl, audio)


if __name__ == "__main__":
    unittest.main()
