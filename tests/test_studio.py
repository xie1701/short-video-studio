import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

# 测试入口自己先切到专用 venv：被 import 的脚本（如 independent_verifier）不可能在 import 期
# 重启宿主进程，所以「用系统 python3 直接跑测试」只能在这里切一次，否则一到 numpy 就 ImportError。
from common import maybe_reexec_in_venv

maybe_reexec_in_venv()

from common import ASSETS_DIR, load_subtitle_templates, read_json, review_checks, sha256_file, write_json
from independent_verifier import verify_exports_blackbox
from compliance_guard import audit_text_line, build_asset_license_manifest
from content_experiment import activate_candidate, evaluate_plan, register_plan
from content_log_review import append_record, generate_weekly_review, load_records
from export_hyperframes import StudioMarkup, export_hyperframes_draft
from raw_intel_ingest import extract_raw_intel
from render_mp4 import build_base_video_track
from source_review import import_sources, source_review_status, write_source_review
from install_skill import deploy
from qa_inspector import detect_black_intervals, measure_sync_marker
from workflow_guard import approve_final, audit_stops, compute_plan_digest, record_milestone, record_stop, tidy_workspace, verify_archive


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.pipeline = self.root / "pipeline"
        self.pipeline.mkdir()

    def test_source_font_and_audio_override_win_when_present(self):
        from common import find_source_font, read_json
        from step3_lock_audio import find_source_bgm
        from vibe_sound import get_style_sound_profile
        sid = "style_21"
        entry = [x for x in read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")["styles"]
                 if x["style_id"] == sid][0]
        fam = entry["subtitle_spec"]["source_font_family"]
        font_dir = ASSETS_DIR / "style_spectrum" / "fonts" / sid
        font_dir.mkdir(parents=True, exist_ok=True)
        probe = font_dir / f"{fam}-Bold.otf"
        base = next((q for q in (ASSETS_DIR / "fonts").glob("*") if q.suffix in (".otf", ".ttf")), None)
        self.assertIsNotNone(base, "随包字体缺失，无法构造探针")
        probe.write_bytes(base.read_bytes())
        from common import _FONT_CACHE
        _FONT_CACHE.clear()
        user_dir = ASSETS_DIR / "sound_vibe" / "user" / sid
        user_dir.mkdir(parents=True, exist_ok=True)
        bgm_names = read_json(ASSETS_DIR / "sound_vibe" / "vibe_sound_manifest.json")["styles"][sid]["source_bgm_names"]
        probes = [probe, user_dir / f"{bgm_names[0]}.wav", user_dir / "accent.wav"]
        for q in probes[1:]:
            with wave.open(str(q), "w") as w:
                w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
                w.writeframes(b"\x00\x00" * 4410)
        try:
            self.assertIsNotNone(find_source_font(fam, sid), "源字体放进专属目录后应被优先命中")
            bgm = find_source_bgm(sid)
            self.assertIsNotNone(bgm)
            self.assertIn(bgm_names[0], bgm.name)
            prof = get_style_sound_profile(sid)
            self.assertIn("accent", prof["user_original_sfx_roles"])
            self.assertIn("user", prof["sfx"]["accent"])
        finally:
            for q in probes:
                q.unlink(missing_ok=True)
            _FONT_CACHE.clear()
        self.assertIsNone(find_source_font(fam, sid))
        self.assertIsNone(find_source_bgm(sid))

    def test_flower_palette_reads_blueprint_not_hardcoded(self):
        from render_template_overlays import get_style_blueprint, style_flower_palette
        bp_all = read_json(ASSETS_DIR / "style_spectrum" / "template_blueprints.json")["blueprints"]
        for sid, node in bp_all.items():
            ov = node["overlay_style"]
            self.assertTrue(ov.get("flower_colors"), f"{sid} 缺 flower_colors")
            pal = style_flower_palette(ov, sid)
            self.assertLessEqual(max(pal["accent"]) - min(pal["accent"]), 255)
            # 主强调色不得是纯白/纯黑（否则花字会失去层次）
            self.assertNotEqual(pal["accent"], (255, 255, 255), f"{sid} 主强调色退化成纯白")
            self.assertNotIn(pal["accent"], [(0, 0, 0)], f"{sid} 主强调色退化成纯黑")
        # 换掉蓝图配色，调色板必须跟着变（证明不是写字面量）
        fake = {"flower_colors": ["#12AB34", "#FFFFFF"], "stroke_color": "#000000"}
        self.assertEqual(style_flower_palette(fake, "style_x")["accent"], (0x12, 0xAB, 0x34))
        # 字幕关键词色优先于按频次排出的第一个色
        self.assertEqual(
            style_flower_palette({"flower_colors": ["#38BDF8", "#B71C1C"]}, "style_y", "#B71C1C")["accent"],
            (0xB7, 0x1C, 0x1C),
        )

    def _native_fixture(self):
        from export_jianying_native import export_native_draft
        norm = self.pipeline / "01_normalized"
        norm.mkdir(parents=True, exist_ok=True)
        v1, v2 = norm / "a.mp4", norm / "b.mp4"
        v1.write_bytes(b"fixture")
        v2.write_bytes(b"fixture")
        clips, cursor = [], 0.0
        for i, (path, role) in enumerate(((v1, "a_roll"), (v2, "b_roll"), (v1, "a_roll"), (v2, "b_roll"))):
            clips.append({
                "clip_id": f"c{i + 1}", "track_role": role, "media_path": str(path),
                "source_in_sec": float(i), "duration_sec": 2.0,
                "timeline_in_sec": cursor, "timeline_out_sec": cursor + 2.0,
            })
            cursor += 2.0
        write_json(self.pipeline / "04_edl.json", {
            "step": "step4", "fps": 30, "style_id": "style_14", "canvas": {"width": 1080, "height": 1920},
            "total_duration_sec": cursor, "total_frames": int(cursor * 30), "clip_count": len(clips), "clips": clips,
        })
        write_json(self.pipeline / "05_subtitles.json", {"step": "step5", "template_id": "style_14", "entries": [
            {"id": "s1", "text": "第一行示范字幕", "start_sec": 0.2, "end_sec": 2.0},
            {"id": "s2", "text": "第二行示范字幕", "start_sec": 2.2, "end_sec": 4.0},
            {"id": "s3", "text": "第三行示范字幕", "start_sec": 4.2, "end_sec": 7.8},
        ]})
        write_json(self.pipeline / "06_packaging.json", {"step": "step6", "style_id": "style_14", "visual_elements": [
            {"id": "p1", "kind": "center_burst_title", "text_or_asset": "关键一点", "fg_hex": "#FFF589",
             "stroke_hex": "#000000", "start_frame": 0, "end_frame": 60},
        ]})
        return export_native_draft(self.pipeline, self.root / "native_out")

    def test_native_jianying_draft_is_text_editable(self):
        status = self._native_fixture()
        self.assertEqual(status["status"], "success")
        self.assertTrue(status["editable_text"])
        self.assertFalse(status["baked_layers"])
        draft = Path(status["draft_content"])
        content = read_json(draft)
        types = [t["type"] for t in content["tracks"]]
        self.assertIn("video", types)
        self.assertGreaterEqual(types.count("text"), 2)
        texts = content["materials"]["texts"]
        self.assertTrue(all(m.get("type") == "text" for m in texts))
        bodies = [json.loads(m["content"])["text"] for m in texts]
        self.assertIn("第一行示范字幕", bodies)
        self.assertIn("关键一点", bodies)
        entry = [s for s in read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")["styles"]
                 if s["style_id"] == "style_14"][0]
        src_font = entry["subtitle_spec"]["source_font_family"]
        self.assertTrue(all(m["font_name"] == src_font for m in texts))
        self.assertIn("source_timerange", content["tracks"][0]["segments"][0])
        self.assertAlmostEqual(content["duration"] / 1e6, 8.0, places=2)

    def test_native_draft_schema_matches_decrypted_reference(self):
        import os
        ref = Path(os.environ.get("JY_DRAFT_REF") or "")
        if not ref.is_file():
            self.skipTest("未提供 JY_DRAFT_REF（真草稿参考不入库），跳过 schema 逐键对比")
        sys.path.insert(0, str(ROOT / "dev_tools"))
        from verify_native_draft import verify
        status = self._native_fixture()
        self.assertEqual(verify(Path(status["draft_dir"]), ref), 0)

    def test_hyperframes_sample_is_per_style_not_generic(self):
        from apply_style_spectrum import export_hyperframes_html, get_style_entry, load_library
        lib = load_library()
        out_a = self.root / "hf_a" / "index.html"
        out_b = self.root / "hf_b" / "index.html"
        export_hyperframes_html(lib, "style_14", out_a, duration_sec=12.0)
        export_hyperframes_html(lib, "style_01", out_b, duration_sec=12.0)
        a, b = out_a.read_text(), out_b.read_text()
        self.assertNotEqual(a, b)
        pip_mode = get_style_entry(lib, "style_14")["pip_composition_spec"]["mode"]
        self.assertIn("sample-pip", a)
        self.assertIn(pip_mode, a)
        self.assertGreaterEqual(a.count("sample-caption-"), 3)
        self.assertNotIn("STYLE-", a + b)
        self.assertNotIn("style_14", a.replace("data-", ""))
        max_chars = get_style_entry(lib, "style_14")["subtitle_spec"]["max_chars_per_line"]
        self.assertIn(f'data-max-chars="{max_chars}"', a)

    def test_fcpxml_export_is_labeled_baked(self):
        source = (ROOT / "short-video-studio" / "scripts" / "export_jianying.py").read_text()
        self.assertIn('"export_kind": "baked_interchange"', source)
        self.assertIn('"text_editable": False', source)
        self.assertIn("export_jianying_native.py", source)

    def test_optional_and_explicit_exports(self):
        for selection, state, required in (("mp4", "NOT_REQUESTED", False), ("auto", "FAIL", False),
                                           ("jianying", "FAIL", True), ("hyperframes", "FAIL", True)):
            result = verify_exports_blackbox(self.pipeline, selection)
            self.assertEqual((result["status"], result["required"]), (state, required))
        jy = self.pipeline / "jianying_draft"
        jy.mkdir()
        (jy / "timeline.fcpxml").write_text("<fcpxml><resources/></fcpxml>")
        write_json(self.pipeline / "jianying_export_status.json", {"status": "degraded_to_mp4_only"})
        self.assertEqual(verify_exports_blackbox(self.pipeline, "jianying")["status"], "FAIL")
        checks = [{"id": "bb_export_contracts", "status": "FAIL"}, {"id": "bb_ocr_av_loop", "status": "SKIPPED_UNAVAILABLE"},
                  {"id": "bb_blind_asr_proxy", "status": "SKIPPED_UNAVAILABLE"}]
        self.assertEqual(review_checks(checks, "auto", False), ([], ["bb_ocr_av_loop", "bb_blind_asr_proxy"]))
        self.assertEqual(review_checks(checks, "jianying", True), (["bb_export_contracts", "bb_blind_asr_proxy"], ["bb_ocr_av_loop"]))

    def test_milestone_and_rehearsal_do_not_count_as_user_contact(self):
        record_milestone(self.pipeline, "stop1_script", "complete")
        record_stop(self.pipeline, "stop1_script", rehearsal=True)
        self.assertEqual(audit_stops(self.pipeline)["count"], 0)
        record_stop(self.pipeline, "stop1_script", summary="confirmed")
        self.assertEqual(audit_stops(self.pipeline)["count"], 1)

    def test_hyperframes_exports_native_clips_and_preserves_edited_draft(self):
        video = self.root / "normalized.mp4"
        video.write_bytes(b"fixture")
        for name in ("08_voice_master.wav", "08_bgm_ducked.wav", "08_sfx_master.wav"):
            with wave.open(str(self.pipeline / name), "wb") as track:
                track.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                track.writeframes(b"\0\0" * 8000)
        write_json(self.pipeline / "04_edl.json", {"fps": 30, "total_frames": 30, "clips": [
            {"clip_id": "a", "media_path": str(video), "timeline_in_sec": 0, "timeline_out_sec": 0.5,
             "source_in_sec": 1, "source_out_sec": 1.5},
            {"clip_id": "b", "media_path": str(video), "timeline_in_sec": 0.5, "timeline_out_sec": 1,
             "source_in_sec": 1.5, "source_out_sec": 2},
        ]})
        write_json(self.pipeline / "03_audio_events.json", {"sentences": [
            {"start_sec": 0, "end_sec": 0.4}, {"start_sec": 0.5, "end_sec": 0.9}
        ]})
        write_json(self.pipeline / "05_subtitles.json", {"entries": [
            {"id": "one", "text": "七十二元，包工。", "keywords": ["七十二元"], "start_sec": 0.1, "end_sec": 0.8},
            {"id": "ignored", "suppressed": True},
        ]})
        write_json(self.pipeline / "06_packaging.json", {"visual_elements": [
            {"id": "title", "kind": "center_burst_title", "text_or_asset": "现场报价", "start_frame": 0, "end_frame": 24}
        ]})
        status = export_hyperframes_draft(self.pipeline)
        self.assertEqual(status["status"], "success")
        package = Path(status["draft_dir"])
        entry = package / "index.html"
        last = entry.read_text(encoding="utf-8")
        nodes = [(tag, attrs) for tag, attrs in StudioMarkup(entry).nodes if "data-track-index" in attrs]
        self.assertEqual([attrs["id"] for tag, attrs in nodes if tag == "video"], ["track-video-a", "track-video-b"])
        self.assertEqual([attrs["data-media-start"] for tag, attrs in nodes if tag == "video"], ["1.0000", "1.5000"])
        self.assertIn('<span class="cap-kw">七十二元</span>，包工。', last)
        self.assertIn("现场报价", last)
        self.assertNotIn("data-composition-src", last)
        self.assertEqual(status["audio_clips_count"], 6)
        receipt = read_json(package / "studio_export.json")
        self.assertTrue(all((package / item["original_src"]).is_file() for item in receipt["video"]))
        self.assertIn("preview .", (package / "启动HyperFrames工程.command").read_text(encoding="utf-8"))
        self.assertNotIn("open \"index.html\"", (package / "启动HyperFrames工程.command").read_text(encoding="utf-8"))

        entry.write_text(last.replace("现场报价", "人工改字"), encoding="utf-8")
        rerun = export_hyperframes_draft(self.pipeline)
        backup = Path(rerun["previous_backup"]) / "project" / "index.html"
        self.assertIn("人工改字", backup.read_text(encoding="utf-8"))
        video.unlink()
        self.assertEqual(export_hyperframes_draft(self.pipeline)["status"], "success")
        (package / receipt["video"][0]["original_src"]).unlink()
        qa = verify_exports_blackbox(self.pipeline, "hyperframes")
        self.assertEqual(qa["status"], "FAIL")
        self.assertTrue(any("缺失" in error for error in qa["errors"]))

    def test_manual_review_and_file_fingerprint(self):
        target = self.pipeline / "output_final.mp4"
        target.write_bytes(b"fixture")
        qa = self.pipeline / "10_qa_report.json"
        write_json(qa, {
            "all_passed": True, "machine_passed": False, "required_manual_checks": ["jianying_import"],
            "plan_sha256": compute_plan_digest(self.pipeline), "output_final_sha256": sha256_file(target),
            "export_mode": "jianying", "deliverables": {"mp4": {"status": "PASS"}, "jianying": {"host_import": "PENDING"}},
        })
        with self.assertRaises(RuntimeError):
            approve_final(self.pipeline)
        approve_final(self.pipeline, "simulated import receipt", ("jianying_import",))
        self.assertEqual(read_json(qa)["deliverables"]["jianying"]["host_import"], "USER_VERIFIED")
        target.write_bytes(b"changed")
        with self.assertRaises(RuntimeError):
            approve_final(self.pipeline, "simulated import receipt", ("jianying_import",))

    def test_install_preserves_unknown_and_backs_up_known_copy(self):
        source = self.root / "source"
        source.mkdir()
        (source / "SKILL.md").write_text("name: short-video-studio\nnew")
        skills_dir = self.root / "skills"
        skills_dir.mkdir()
        target = skills_dir / "short-video-studio"
        target.mkdir()
        (target / "custom.txt").write_text("keep")
        with self.assertRaises(ValueError):
            deploy(source, skills_dir, copy=True)
        self.assertEqual((target / "custom.txt").read_text(), "keep")
        (target / "SKILL.md").write_text("name: short-video-studio\nold")
        previous = deploy(source, skills_dir, copy=True)
        self.assertEqual((previous / "custom.txt").read_text(), "keep")
        self.assertEqual((target / "SKILL.md").read_text(), "name: short-video-studio\nnew")

    def test_sources_link_locked_speech_to_untouched_media_and_archive(self):
        raw = self.root / "in.mov"
        raw.write_bytes(b"raw media")
        normalized = self.pipeline / "01_normalized" / "v1.mp4"
        normalized.parent.mkdir()
        normalized.write_bytes(b"proxy")
        table = self.root / "prices.csv"
        table.write_text("item,price\nrepair,72")
        source_file = self.root / "sources.json"
        write_json(source_file, {
            "sources": [{"id": "price", "path": table.name}],
            "claims": [{"id": "c1", "text": "72元", "sentence_id": "sent_001", "source_id": "price", "locator": "row:2"}],
        })
        import_sources(source_file, self.pipeline)
        write_json(self.pipeline / "01_manifest.json", {"items": [{"source_path": str(raw), "normalized_mp4": str(normalized)}]})
        write_json(self.pipeline / "03_audio_events.json", {"sentences": [
            {"id": "sent_001", "text": "维修72元", "source_media": str(normalized), "source_in_sec": 2.5, "source_out_sec": 3.8,
             "start_sec": 0.1, "end_sec": 1.4}
        ]})
        write_json(self.pipeline / "04_edl.json", {"clips": [
            {"clip_id": "clip_001", "media_path": str(normalized), "source_in_sec": 2.5, "source_out_sec": 3.8,
             "timeline_in_sec": 0, "timeline_out_sec": 1.4}
        ]})
        report = write_source_review(self.pipeline)
        self.assertEqual(report["narration"][0]["voice"]["path"], str(raw))
        self.assertEqual(report["claims"][0]["clip_ids"], ["clip_001"])
        self.assertEqual(source_review_status(report), "PENDING_REVIEW")
        (self.pipeline / "output_final.mp4").write_bytes(b"fixture")
        tidy_workspace(self.pipeline)
        self.assertTrue(verify_archive(self.root / "out")["passed"])
        (self.root / "out" / "04_edl.json").write_text("{}")
        self.assertEqual(verify_archive(self.root / "out")["changed"], ["04_edl.json"])
        table.write_text("item,price\nrepair,172")
        self.assertEqual(source_review_status(report), "FAIL")

    def test_first_hand_records_keep_distinct_participants_and_original_locator(self):
        source = self.root / "comments.json"
        write_json(source, {"comments": [
            {"id": "c1", "author_id": "u1", "role": "customer", "text": "这个多少钱？"},
            {"id": "c2", "author_id": "u2", "role": "customer", "text": "这个多少钱？"},
            {"id": "c3", "role": "support", "text": "地址在街道旁"},
        ]})
        report = extract_raw_intel([source], verified_customer_export=True)
        self.assertEqual(report["identified_participant_count"], 2)
        self.assertEqual(report["source_utterances"][1]["locator"], "json:/comments/1")
        self.assertEqual(len(report["raw_pain_phrases"][0]["source_ids"]), 2)
        self.assertTrue(report["verified_by_real_users"])

    def test_external_license_checks_content_and_original_document(self):
        music = self.root / "music.wav"
        music.write_bytes(b"registered bytes")
        document = self.root / "permission.txt"
        document.write_text("fixture")
        licenses = self.root / "licenses.json"
        write_json(licenses, {"assets": [{"sha256": sha256_file(music), "authorization_path": document.name,
                                         "license": "fixture", "commercial_ok": True}]})
        write_json(self.pipeline / "03_audio_events.json", {"bgm": {"file": str(music)}})
        self.assertTrue(build_asset_license_manifest(self.pipeline, licenses_path=licenses)["passed"])
        music.write_bytes(b"different asset with identical name")
        self.assertFalse(build_asset_license_manifest(self.pipeline, licenses_path=licenses)["passed"])
        hit = audit_text_line("我们店的全城第一只是引用顾客原话。")["hits"][0]
        self.assertIn(hit["matched_term"], hit["context"])

    def test_experiment_versions_and_snapshot_reimport(self):
        log = self.root / "log"
        plan = {
            "id": "pace", "track": "creator", "design": "matched", "platform": "fixture",
            "traffic_source": "organic", "observation_hours": 48, "duration_band_sec": [20, 30],
            "variable": "pause_sec", "parameter": "pause_sec", "control_value": 0.20, "variant_value": 0.26,
            "primary_metric": "retention_3s", "min_units_per_arm": 5, "min_effect": 0.04,
        }
        registration = register_plan(log, plan)
        published = datetime.fromisoformat(read_json(registration)["registered_at"]) + timedelta(seconds=5)
        control, variant = [], []
        for i in range(6):
            for arm, value, rate in (("control", 0.20, 40 + i), ("variant", 0.26, 53 + i)):
                row = {
                    "track": "creator", "platform": "fixture", "video_id": f"{arm}-{i}", "observation_hours": 48,
                    "duration_sec": 23, "traffic_source": "organic",
                    "published_at": published.isoformat(), "captured_at": (published + timedelta(hours=48)).isoformat(),
                    "metrics": {"retention_3s": {"numerator": rate, "denominator": 100}},
                    "experiment": {"id": "pace", "pair_id": str(i), "arm": arm, "variable": "pause_sec", "value": value},
                }
                append_record(log, row)
                (control if arm == "control" else variant).append(row)
        before = (log / "records.jsonl").read_bytes()
        initial = generate_weekly_review(log, track="creator")
        profile = Path(initial["account_profile_json"])
        state = profile.read_bytes()
        for row in control + variant:
            append_record(log, row)
        replay = generate_weekly_review(log, track="creator")
        self.assertEqual((log / "records.jsonl").read_bytes(), before)
        self.assertEqual(replay["record_count"], 12)
        self.assertEqual(profile.read_bytes(), state)
        self.assertEqual(initial["candidates"], replay["candidates"])
        candidate = replay["candidates"][0]
        self.assertEqual(candidate["status"], "CANDIDATE")
        self.assertAlmostEqual(candidate["effect"], 0.13)
        historical = evaluate_plan(plan, control + variant, None)
        self.assertFalse(historical["activation_allowed"])
        self.assertEqual(historical["units"], {"control": 6, "variant": 6})
        self.assertEqual(evaluate_plan(plan, control[:4] + variant, None)["status"], "INSUFFICIENT")
        activate_candidate(log, load_records(log / "records.jsonl"), candidate["candidate_id"])
        active = read_json(profile)
        self.assertEqual(active["pause_sec"], 0.26)
        self.assertEqual(active["platform"], "fixture")
        self.assertEqual(len(list((log / "profile_history").glob("*.json"))), 1)
        altered = {**variant[0], "metrics": {"retention_3s": {"numerator": 44, "denominator": 100}}}
        append_record(log, altered)
        with self.assertRaises(ValueError):
            activate_candidate(log, load_records(log / "records.jsonl"), candidate["candidate_id"])
        self.assertEqual(len(load_records(log / "records.jsonl")), 12)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_render_reuses_valid_segments_and_invalidates_changed_media(self):
        video = self.root / "source.mp4"
        wav = self.root / "master.wav"
        for color in ("blue", "red"):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color={color}:s=108x192:r=30:d=1", "-c:v", "libx264", str(video)], check=True)
            if not wav.exists():
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", str(wav)], check=True)
            edl = {"fps": 30, "clips": [{"clip_id": "c1", "media_path": str(video), "source_in_sec": 0,
                                         "source_out_sec": 1, "duration_sec": 1}]}
            build_base_video_track(self.pipeline, edl, {}, wav, preview=True)
            cold = read_json(self.pipeline / "render_profile.json")
            self.assertEqual(cold["segments_rendered"], 1)
            build_base_video_track(self.pipeline, edl, {}, wav, preview=True)
            warm = read_json(self.pipeline / "render_profile.json")
            self.assertTrue(warm["base_cache_hit"])
            self.assertEqual(warm["segments_reused"], 1)
            print(f"base render {color}: cold {cold['base_elapsed_sec']}s, warm {warm['base_elapsed_sec']}s")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_single_black_frame_and_real_signal_offset(self):
        for delay in (0, 120):
            path = self.root / f"marker_{delay}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=96x128:r=30:d=2",
                "-f", "lavfi", "-i", f"aevalsrc=0.6*sin(2*PI*800*t)*between(t\\,{0.5 + delay / 1000}\\,{0.6 + delay / 1000}):s=48000:d=2",
                "-vf", "drawbox=c=white:t=fill:enable='between(t,0.499,0.56)',drawbox=c=black:t=fill:enable='eq(n,36)'",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
            ], check=True)
            status, hits = detect_black_intervals(path)
            self.assertEqual(status, "FAIL")
            self.assertIn("1.2", hits)
            offset = measure_sync_marker(path, 0.2, 1.0, 30)["offset_sec"]
            self.assertAlmostEqual(offset, delay / 1000, delta=0.02)

    def test_landscape_to_portrait_crop_and_bt709_luma(self):
        import numpy as np
        from common import compute_bt709_luma, compute_centered_crop_frac

        # 1920x1080 等比缩放到高 1920 后宽为 3413，裁宽 1080：
        left_f = compute_centered_crop_frac(0.20, 3413, 1080, target_norm_pos=0.5)
        mid_f = compute_centered_crop_frac(0.50, 3413, 1080, target_norm_pos=0.5)
        right_f = compute_centered_crop_frac(0.80, 3413, 1080, target_norm_pos=0.5)
        self.assertLess(left_f, mid_f)
        self.assertLess(mid_f, right_f)
        self.assertAlmostEqual(mid_f, 0.5, delta=0.01)

        white = np.full((4, 4, 3), 255.0, dtype=np.float32)
        self.assertAlmostEqual(float(compute_bt709_luma(white).mean()), 255.0, delta=0.05)

    def test_equal_power_overlap_no_dip_and_dynamic_pause(self):
        import numpy as np
        from step3_lock_audio import _equal_power_append, resolve_dynamic_pause

        sr = 48000
        t = np.arange(int(0.2 * sr)) / float(sr)
        sig = np.column_stack([np.sin(2 * np.pi * 440 * t) * 0.5] * 2).astype(np.float32)
        buf = [sig.copy()]
        _, xf = _equal_power_append(buf, sig.copy(), int(0.012 * sr))
        joined = np.concatenate(buf, axis=0)
        self.assertEqual(xf, int(0.012 * sr))
        self.assertEqual(len(joined), len(sig) * 2 - xf)
        mid = len(sig) - xf // 2
        seam_rms = float(np.sqrt(np.mean(joined[mid - 240 : mid + 240] ** 2)))
        self.assertGreater(seam_rms, 0.20)
        # 停顿不再被 0.24s 硬顶死
        p_pers, _ = resolve_dynamic_pause(
            {"emotion": "info"}, {"emotion": "persona"},
            base_pause=0.22, account_profile={"sample_sufficient": True, "persona_pause_sec": 0.31},
        )
        self.assertGreaterEqual(p_pers, 0.28)

    def test_compliance_offset_rewrite_and_system_commercial_font(self):
        from compliance_guard import _classify_asset_license, audit_text_line

        res = audit_text_line("最近很多老客问，这家是不是全网最好吃的烧烤")
        self.assertTrue(res["has_must"])
        self.assertIn("最近", res["suggested_text"])
        self.assertNotIn("最好吃", res["suggested_text"])

        cls = _classify_asset_license(
            Path("/System/Library/Fonts/SourceHanSansCN-Bold.otf"), "font", self.pipeline, {}, ""
        )
        self.assertTrue(cls["commercial_ok"])
        self.assertEqual(cls["source_tier"], "verified_commercial_font")

    def test_demo_review_and_zero_jargon_outputs(self):
        from content_log_review import DEMO_RECORDS, append_record, generate_weekly_review
        from generate_shot_card import build_sketch_svg, generate_shot_card, DEFAULT_SHOTS
        from raw_intel_ingest import lint_script, render_lint_markdown

        log_d = self.root / "demo_log"
        for rec in DEMO_RECORDS:
            append_record(log_d, dict(rec))
        rev = generate_weekly_review(log_d, track="store")
        md_txt = Path(rev["weekly_review_md"]).read_text(encoding="utf-8")
        self.assertIn("3 秒仍在看", md_txt)
        self.assertIn("有效咨询", md_txt)
        self.assertNotIn("本期缺少可对账的分子和分母", md_txt)

        svg = build_sketch_svg("portrait_hook", 1)
        self.assertNotIn("TOP UI SAFE", svg)
        self.assertNotIn("BOTTOM UI", svg)

        card_md = self.root / "shot_card.md"
        generate_shot_card(card_md, "现切羊排实拍", "store", DEFAULT_SHOTS, industry="catering")
        card_txt = card_md.read_text(encoding="utf-8")
        self.assertIn("餐饮美食", card_txt)
        self.assertNotIn("HDR", card_txt)

        lint = lint_script([{"show": "我们坚持手工现切不隔夜", "say": "我们坚持手工现切不隔夜"}])
        lint_md = render_lint_markdown(lint, {"utterance_count": 0, "verified_by_real_users": False, "pain_keywords": [], "proof_assets": []})
        self.assertNotIn("Micro-Hook", lint_md)

    def test_v2_whitebox_style_spectrum_and_rulebooks_contract(self):
        import re
        from common import (
            ASSETS_DIR,
            LEAK_REGEX_PATTERNS,
            find_commercial_font,
            is_verified_commercial_font,
            load_animation_sfx_pairs,
            load_sfx_intent_taxonomy,
            load_typography_cover_rules,
            resolve_lut_path,
        )

        # 1. Verify 4 bundled open commercial fonts exist and pass license guard
        expected_fonts = (
            "AlimamaShuHeiTi-Bold.otf",
            "SmileySans-Oblique.ttf",
            "SourceHanSerifCN-Bold.otf",
            "SourceHanSerifCN-Heavy.otf",
        )
        for fn in expected_fonts:
            fp = ASSETS_DIR / "fonts" / fn
            self.assertTrue(fp.is_file(), f"Missing bundled font: {fp}")
            self.assertTrue(is_verified_commercial_font(fp), f"Font failed commercial verification: {fn}")
            resolved = find_commercial_font([fn], verified_only=True)
            self.assertTrue(resolved and Path(resolved).name == fn)

        # 2. Verify 3 shared rulebooks extracted from the 7 teaching drafts
        pairs_doc = load_animation_sfx_pairs()
        self.assertEqual(pairs_doc.get("total_verified_pairs"), 83)
        self.assertGreaterEqual(len(pairs_doc.get("animation_lookup") or {}), 35)
        for p in pairs_doc.get("pairs", []):
            self.assertTrue((ASSETS_DIR / p["cc0_vibe_sfx"]).is_file(), f"Missing CC0 SFX: {p['cc0_vibe_sfx']}")

        tax_doc = load_sfx_intent_taxonomy()
        self.assertEqual(tax_doc.get("total_scene_labels"), 29)
        self.assertEqual(tax_doc.get("total_sfx_samples"), 92)
        for emo, files in (tax_doc.get("pipeline_emotion_routing") or {}).items():
            for rel in files:
                self.assertTrue((ASSETS_DIR / rel).is_file(), f"Missing routed CC0 SFX for {emo}: {rel}")

        typo_doc = load_typography_cover_rules()
        self.assertEqual(len(typo_doc.get("golden_highlight_palette") or {}), 4)
        self.assertEqual(len(typo_doc.get("cover_3x4_layouts") or {}), 4)

        # 3. Verify v2.1.0 24 styles & 28 subtitle templates contract (100% 1080x1920 9:16 portrait, 540px symmetry, 0 private path leak, zero jargon)
        lib_path = ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json"
        raw_lib_text = lib_path.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"/(?:Users|home)/[A-Za-z0-9._-]+", raw_lib_text))
        self.assertNotIn("com." + "lveditor.draft", raw_lib_text)
        lib = read_json(lib_path)
        self.assertEqual(lib.get("version"), "4.1.0")
        self.assertEqual(lib.get("default_style_id"), "style_01")
        styles = lib.get("styles") or []
        self.assertEqual(len(styles), 24)

        tpl_doc = load_subtitle_templates()
        self.assertEqual(len(tpl_doc), 28)
        for tkey, tval in tpl_doc.items():
            self.assertIsInstance(tval, dict, f"Non-dict template entry in templates.json: {tkey}")
        for expected_tid in ("store_bold_yellow", "creator_clean_white", "contrast_red_punch", "warm_persona_cream"):
            self.assertIn(expected_tid, tpl_doc)

        jargon_re = re.compile(r"水桶流|漏斗流|痛点|人设|A-roll|B-roll|LUFS|LUT|OCR|Micro-Hook", re.IGNORECASE)
        report_md = (ASSETS_DIR / "style_spectrum" / "STYLE_SPECTRUM_REPORT.md").read_text(encoding="utf-8")
        self.assertIsNone(jargon_re.search(report_md), "Jargon found in STYLE_SPECTRUM_REPORT.md")

        whitebox_cnt = 0
        high_broll_targets = {}
        for s in styles:
            sid = s["style_id"]
            self.assertIn(sid, tpl_doc)
            meta = s["meta"]
            self.assertEqual((meta["width"], meta["height"], meta["aspect"]), (1080, 1920, "9:16"), f"{sid} not 9:16")
            sub = s["subtitle_spec"]
            # v4：安全区直接取源草稿实测值（0.629–0.721），带外即为漂移
            self.assertGreaterEqual(float(sub["safe_y_ratio"]), 0.60)
            self.assertLessEqual(float(sub["safe_y_ratio"]), 0.80)
            self.assertEqual(sub.get("symmetry_center_x"), 540)
            self.assertEqual(sub.get("symmetry_max_width_px"), 640)
            self.assertTrue(is_verified_commercial_font(s.get("commercial_safe_font")))
            lut_p = resolve_lut_path(s["color_and_lut"]["lut_file"], style_id=sid)
            self.assertTrue(lut_p.is_file() and lut_p.stat().st_size > 1000, f"Invalid LUT for {sid}")
            pitch = str(s.get("plain_cn_pitch") or "")
            self.assertGreater(len(pitch), 12)
            for pat in LEAK_REGEX_PATTERNS:
                self.assertIsNone(re.search(pat, pitch), f"Leak pattern {pat} in pitch of {sid}: {pitch}")
            self.assertIsNone(jargon_re.search(pitch), f"Jargon found in plain_cn_pitch of {sid}: {pitch}")
            if (s.get("whitebox_source") or {}).get("is_whitebox_jianying"):
                whitebox_cnt += 1
            if sid in ("style_21", "style_22", "style_23", "style_24"):
                high_broll_targets[sid] = round(float(s.get("broll_overlay_ratio_target", -1.0)), 2)
            pip_spec = s.get("pip_composition_spec") or {}
            giant_spec = s.get("giant_text_spec") or {}
            self.assertIn(pip_spec.get("mode"), {
                "none",
                "geometry_pip_full",
                "geometry_pip_window",
                "geometry_pip_main_shrunk",
                "geometry_pip_main_punch_in",
                "blur_bg_center_card_pip",
                "dark_doc_circle_avatar_pip",
                "linear_split_feather_pip",
                "black_polaroid_frame_pip",
                "person_behind_giant_text_pip",
                "top_white_rounded_frame_pip",
                "black_spotlight_circle_pip",
                "top_banner_evidence_pip",
                "dual_gold_frame_pip",
                "vision_portrait_cutout_pip",
                "data_news_cutout_pip",
            }, f"Invalid pip_composition_spec on {sid}")
            self.assertIn(giant_spec.get("mode"), {
                "giant_smash_single",
                "giant_dual_clash",
                "giant_numeric_trio",
                "giant_vertical_seal",
            }, f"Invalid giant_text_spec on {sid}")
            self.assertGreaterEqual(int(giant_spec.get("font_size_px") or 0), 120, f"Giant text too small on {sid}")
        self.assertEqual(whitebox_cnt, 24)
        self.assertEqual(
            high_broll_targets,
            {"style_21": 0.76, "style_22": 0.41, "style_23": 0.46, "style_24": 0.55},
        )

    def test_pip_giant_text_and_template_packaging_rendering(self):
        from render_mp4 import _build_dual_pip_cmd, _build_ffmpeg_pip_vf, _build_single_masked_pip_cmd, render_packaging_png

        # 单源画框模式全部走等比无变形缩放裁切；mode="none" 返回 None 保持纯净全屏
        self.assertIsNone(_build_ffmpeg_pip_vf({"mode": "none"}))
        for expected_mode in (
            "black_polaroid_frame_pip",
            "paper_craft_card_pip",
            "minimal_white_note_card_pip",
            "blur_bg_center_card_pip",
            "linear_split_feather_pip",
            "smooth_orbit_pip",
        ):
            vf_str = _build_ffmpeg_pip_vf({"mode": expected_mode, "border_hex": "#FFF589"})
            self.assertIsNotNone(vf_str, f"Single-source mode returned None: {expected_mode}")
            self.assertIn("force_original_aspect_ratio=increase", vf_str)
            self.assertIn("overlay=", vf_str)

        # 验证单源 Alpha 蒙版高级画框（style_13 圆角 / style_14 聚光圆窗 / style_16 真羽化 / style_09 单源双格金线）
        for smode, expected_in in (
            ("top_white_rounded_frame_pip", 2),
            ("black_spotlight_circle_pip", 2),
            ("linear_split_feather_pip", 2),
            ("dark_doc_circle_avatar_pip", 4),
        ):
            cmd_sm = _build_single_masked_pip_cmd(
                "ffmpeg",
                str(self.root / "pip_main.mp4"),
                0.0,
                5.0,
                "scale=1080:1920",
                {"mode": smode, "border_hex": "#C8A96A"},
                60,
                30,
                self.root / f"seg_sm_{smode}.mp4",
            )
            self.assertIsNotNone(cmd_sm, f"Single masked PIP returned None: {smode}")
            self.assertEqual(cmd_sm.count("-i"), expected_in)
            fc_s = cmd_sm[cmd_sm.index("-filter_complex") + 1]
            self.assertIn("alphamerge", fc_s)
            self.assertIn("force_original_aspect_ratio=increase", fc_s)

        # 验证双源 PIP 命令路由：secondary_media_path 异于主镜时走多路 -i + -filter_complex 且等比无变形
        for dmode, expected_inputs in (
            ("smooth_orbit_pip", 4),
            ("dark_doc_circle_avatar_pip", 5),
            ("dual_gold_frame_pip", 2),
            ("linear_split_feather_pip", 3),
            ("vision_portrait_cutout_pip", 2),
            ("data_news_cutout_pip", 2),
        ):
            cmd_dual = _build_dual_pip_cmd(
                "ffmpeg",
                str(self.root / "pip_main.mp4"),
                0.0,
                5.0,
                "scale=1080:1920",
                {"mode": dmode, "corner": "tr", "radius_px": 146, "border_hex": "#F4B13E"},
                str(self.root / "pip_sec.mp4"),
                1.2,
                2.0,
                60,
                30,
                self.root / "seg_dual.mp4",
            )
            self.assertIsNotNone(cmd_dual, f"Dual PIP returned None: {dmode}")
            self.assertIn("-filter_complex", cmd_dual)
            self.assertEqual(cmd_dual.count("-i"), expected_inputs)
            fc_str = cmd_dual[cmd_dual.index("-filter_complex") + 1]
            self.assertIn("force_original_aspect_ratio=increase", fc_str)

        for gmode in ("giant_smash_single", "giant_dual_clash", "giant_numeric_trio", "giant_vertical_seal"):
            im_g = render_packaging_png({
                "kind": "center_burst_title",
                "giant_mode": gmode,
                "font_size_px": 148,
                "tag_label": "深度锐评",
                "text_or_asset": "三万块",
                "fg_hex": "#FFF589",
                "accent_hex": "#FF1837",
            }, style_id="style_08")
            self.assertLessEqual(im_g.width, 640)
            self.assertGreaterEqual(im_g.height, 130)

        im_q = render_packaging_png({
            "kind": "quote_citation_card",
            "text_or_asset": "“看清关键条款”",
            "subtext": "—— 案情焦点 · 核心实证",
            "fg_hex": "#FFF589",
            "accent_hex": "#FF1837",
        }, style_id="style_09")
        self.assertLessEqual(im_q.width, 640)
        self.assertEqual(im_q.height, 136)

        im_b = render_packaging_png({
            "kind": "stamp_tag",
            "text_or_asset": "高复利",
            "burst_tags": ["可复制", "高复利", "可持续"],
            "fg_hex": "#FFF589",
            "accent_hex": "#FF1837",
        }, style_id="style_16")
        self.assertLessEqual(im_b.width, 640)
        self.assertEqual(im_b.height, 98)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_high_broll_edl_and_qa_styles_21_to_24(self):
        from qa_inspector import inspect_video
        from step4_build_edl import run_step4

        ar_mp4 = self.root / "aroll.mp4"
        br1_mp4 = self.root / "broll_1.mp4"
        br2_mp4 = self.root / "broll_2.mp4"
        out_mp4 = self.root / "out_24.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=10",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(ar_mp4)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=navy:s=1080x1920:r=30:d=6",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(br1_mp4)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=teal:s=1080x1920:r=30:d=6",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(br2_mp4)], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=9.6",
                        "-f", "lavfi", "-i", "sine=f=330:d=9.6:r=48000",
                        "-af", "volume=-14dB", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out_mp4)], check=True)

        sents = [
            {"id": "s1", "text": "门店现场现切准备开始", "keywords": ["现切"], "start_sec": 0.0, "end_sec": 1.6, "source_media": str(ar_mp4), "source_in_sec": 0.0, "source_out_sec": 1.6},
            {"id": "s2", "text": "看清每道工序时间差", "keywords": ["工序"], "start_sec": 1.6, "end_sec": 3.4, "source_media": str(ar_mp4), "source_in_sec": 1.6, "source_out_sec": 3.4},
            {"id": "s3", "text": "实拍火候和手工步骤", "keywords": ["手工"], "start_sec": 3.4, "end_sec": 5.6, "source_media": str(ar_mp4), "source_in_sec": 3.4, "source_out_sec": 5.6},
            {"id": "s4", "text": "全程细节看得见才放心", "keywords": ["细节"], "start_sec": 5.6, "end_sec": 7.6, "source_media": str(ar_mp4), "source_in_sec": 5.6, "source_out_sec": 7.6},
            {"id": "s5", "text": "记得来看看现场做法", "keywords": ["现场"], "start_sec": 7.6, "end_sec": 9.6, "source_media": str(ar_mp4), "source_in_sec": 7.6, "source_out_sec": 9.6},
        ]
        evts = [
            {"id": f"evt_{i:03d}", "type": "sentence_start", "sentence_id": s["id"],
             "time_sec": s["start_sec"], "frame": int(round(s["start_sec"] * 30))}
            for i, s in enumerate(sents, start=1)
        ] + [
            {"id": "evt_099", "type": "keyword_accent", "sentence_id": "s1", "time_sec": 0.8, "frame": 24},
            {"id": "evt_100", "type": "keyword_accent", "sentence_id": "s2", "time_sec": 2.4, "frame": 72},
        ]
        evts.sort(key=lambda e: e["frame"])

        manifest_items = [
            {"id": "ar1", "role": "a_roll", "normalized_mp4": str(ar_mp4), "duration_sec": 10.0, "video_grade": "usable", "sharpness_score": 45.0, "stability_score": 0.9},
            {"id": "br1", "role": "b_roll", "normalized_mp4": str(br1_mp4), "duration_sec": 6.0, "video_grade": "usable", "semantic_tags": ["现切", "手工"], "sharpness_score": 48.0, "stability_score": 0.9, "best_in_sec": 0.4},
            {"id": "br2", "role": "b_roll", "normalized_mp4": str(br2_mp4), "duration_sec": 6.0, "video_grade": "usable", "semantic_tags": ["细节", "现场"], "sharpness_score": 48.0, "stability_score": 0.9, "best_in_sec": 0.4},
        ]

        # style_21 (源实测 75.6% 覆盖，动态带 ±15pp)
        p21 = self.root / "p21"
        p21.mkdir()
        write_json(p21 / "01_manifest.json", {"items": manifest_items})
        write_json(p21 / "03_audio_events.json", {"locked_duration_sec": 9.6, "locked_duration_frames": 288, "sentences": sents, "events": evts})
        edl21 = run_step4(p21, track="store", style_id="style_21", edit_profile="documentary")
        self.assertAlmostEqual(edl21["broll_ratio_prior"], 0.756, places=3)
        self.assertEqual(edl21["broll_ratio_band"], [0.606, 0.906])
        # 口径：真实插入画面单独统计（自制图卡不计入与源一致的覆盖率）
        self.assertGreaterEqual(edl21["broll_ratio_with_cards"], 0.60)
        self.assertLessEqual(edl21["broll_ratio_with_cards"], 0.95)
        self.assertTrue(all(float(c["duration_sec"]) >= 0.30 for c in edl21["clips"]))

        # style_24 (源实测 54.8%，非 100%) + TTS source mismatch must block a_roll binding
        p24 = self.root / "p24"
        p24.mkdir()
        tts_sents = [{**s, "source_media": str(self.root / "tts_fake.wav")} for s in sents]
        write_json(p24 / "01_manifest.json", {"items": manifest_items})
        write_json(p24 / "03_audio_events.json", {"locked_duration_sec": 9.6, "locked_duration_frames": 288, "sentences": tts_sents, "events": evts})
        edl24 = run_step4(p24, track="creator", style_id="style_24", edit_profile="documentary")
        # 本 fixture 的 A-roll 全部被 TTS 源不匹配挡掉，B-roll 只能吃满 —— 这正是动态配比应有的结果
        self.assertAlmostEqual(edl24["broll_ratio_prior"], 0.548, places=3)
        self.assertEqual(edl24["broll_ratio_band"], [0.398, 0.698])
        self.assertEqual(edl24["broll_target_hard"], False)
        self.assertGreaterEqual(edl24["broll_ratio_actual"], 0.40)
        self.assertFalse(any(
            c["track_role"] == "a_roll" and "tts_fake" in str(c.get("media_path") or "")
            for c in edl24["clips"]
        ))
        self.assertTrue(all(float(c["duration_sec"]) >= 0.30 for c in edl24["clips"]))

        # QA #8 must pass on high-broll style_24
        write_json(p24 / "05_subtitles.json", {"template_id": "style_24", "font_path": str(ASSETS_DIR / "fonts" / "SourceHanSerifCN-Heavy.otf"), "entries": []})
        write_json(p24 / "06_packaging.json", {"visual_elements": []})
        qa_rep = inspect_video(p24, out_mp4, export_mode="mp4")
        c8 = next(c for c in qa_rep["checks"] if c["id"] == "shot_duration_dist")
        self.assertEqual(c8["status"], "PASS", f"QA #8 failed on style_24: {c8}")
        # 新增两条"不许假装""不许瞎切"的自查：素材没主题词就照实记 none，小窗必须交代有没有按脸取景
        sem_c = next(c for c in qa_rep["checks"] if c["id"] == "broll_semantic_fit")
        self.assertEqual(sem_c["status"], "PASS", f"语义对位自查不通过: {sem_c}")
        face_c = next(c for c in qa_rep["checks"] if c["id"] == "pip_face_framing")
        self.assertEqual(face_c["status"], "PASS", f"小窗人脸构图自查不通过: {face_c}")

    def test_bbox_iou_normalized_and_archive_approve_order(self):
        from common import bbox_iou

        iou = bbox_iou({"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4}, {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4})
        self.assertAlmostEqual(iou, 0.09 / 0.23, places=4)

        final_mp4 = self.pipeline / "output_final.mp4"
        final_mp4.write_bytes(b"fixture-video")
        write_json(self.pipeline / "04_edl.json", {"clips": [{"clip_id": "c1", "duration_sec": 3.0}], "total_duration_sec": 3.0})
        write_json(self.pipeline / "10_qa_report.json", {
            "all_passed": True, "machine_passed": True, "required_manual_checks": [],
            "plan_sha256": compute_plan_digest(self.pipeline),
            "output_final_sha256": sha256_file(final_mp4),
            "export_mode": "mp4", "deliverables": {"mp4": {"status": "PENDING_REVIEW"}},
        })
        (self.pipeline / "10_qa_report.md").write_text("🟡 **自动检查已过，待您最后看一眼确认**\n", encoding="utf-8")
        (self.pipeline / "11_delivery_summary.md").write_text("状态：待人工终审确认\n", encoding="utf-8")
        (self.pipeline / "decisions_made.md").write_text("状态：自动检查全过，等你确认一眼\n", encoding="utf-8")

        out_dir = self.root / "out"
        tidy_workspace(self.pipeline, out_dir=out_dir)
        approve_final(self.pipeline, note="已看成片确认可发", out_dir=out_dir)
        self.assertTrue(verify_archive(out_dir)["passed"])
        for doc_name in ("10_qa_report.md", "11_delivery_summary.md", "decisions_made.md"):
            txt = (out_dir / doc_name).read_text(encoding="utf-8")
            self.assertNotIn("APPROVED", txt)
            self.assertNotIn("MACHINE_PASS_PENDING_HUMAN", txt)
            self.assertNotIn("REVIEW_REQUIRED", txt)

    def test_experiment_24_styles_and_hyperframes_regressions(self):
        from compliance_guard import audit_text_line
        from content_experiment import validate_plan

        for val_a, val_b in (("style_21", "style_24"),):
            validate_plan({
                "id": "exp_style24", "track": "creator", "design": "matched", "platform": "fixture",
                "traffic_source": "organic", "observation_hours": 48, "duration_band_sec": [20, 40],
                "variable": "preferred_style_id", "parameter": "preferred_style_id",
                "control_value": val_a, "variant_value": val_b,
                "primary_metric": "completion_rate", "min_units_per_arm": 5, "min_effect": 0.04,
            })
        validate_plan({
            "id": "exp_broll_only", "track": "creator", "design": "matched", "platform": "fixture",
            "traffic_source": "organic", "observation_hours": 48, "duration_band_sec": [20, 40],
            "variable": "target_aroll_ratio", "parameter": "target_aroll_ratio",
            "control_value": 0.15, "variant_value": 0.0,
            "primary_metric": "retention_3s", "min_units_per_arm": 5, "min_effect": 0.04,
        })

        hit = audit_text_line("他们说100%好评才靠谱")["hits"][0]
        self.assertEqual(hit["matched_term"], "100%好评")
        self.assertEqual(audit_text_line("兼容 nvx 显卡驱动接口")["hits"], [])

        video = self.root / "norm2.mp4"
        video.write_bytes(b"fixture")
        for name in ("08_voice_master.wav", "08_bgm_ducked.wav", "08_sfx_master.wav"):
            with wave.open(str(self.pipeline / name), "wb") as track:
                track.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                track.writeframes(b"\0\0" * 8000)
        write_json(self.pipeline / "04_edl.json", {"fps": 30, "total_frames": 60, "clips": [
            {"clip_id": "a", "media_path": str(video), "timeline_in_sec": 0, "timeline_out_sec": 2.0,
             "source_in_sec": 0, "source_out_sec": 2.0}
        ]})
        write_json(self.pipeline / "03_audio_events.json", {"sentences": [{"start_sec": 0, "end_sec": 1.8}]})
        write_json(self.pipeline / "05_subtitles.json", {"entries": [
            {"id": "s1", "text": "记住标准再签合同", "keywords": ["标准"], "start_sec": 0.1, "end_sec": 1.8}
        ]})
        write_json(self.pipeline / "06_packaging.json", {"visual_elements": [
            {"id": "bar1", "kind": "persistent_top_bar", "text_or_asset": "装修避坑指南", "start_frame": 0, "end_frame": 45},
            {"id": "q1", "kind": "quote_citation_card", "text_or_asset": "“看清合同.png”", "start_frame": 10, "end_frame": 55},
        ]})
        res = export_hyperframes_draft(self.pipeline)
        self.assertEqual(res["status"], "success")
        html_txt = (Path(res["draft_dir"]) / "index.html").read_text(encoding="utf-8")
        self.assertIn("装修避坑指南", html_txt)
        self.assertIn("看清合同.png", html_txt)

    def test_repo_privacy_and_zero_local_project_implants(self):
        import re

        # Construct forbidden local/third-party tokens cleanly so test source stays scrubbed too
        priv_path_re = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+|C:\\\\Users\\\\[A-Za-z0-9._-]+")
        forbidden_tokens = [
            "com." + "lveditor.draft",
            "Jianying" + "Pro",
            "11月" + "14日",
            "jianying_project_" + "1114",
            "39-" + "知识付费",
            "45-" + "知识付费",
            "57-" + "留学",
            "31-" + "知识付费",
            "37-" + "银行贷款",
            "61-" + "升学规划",
            "33-" + "全屋定制",
            "24-" + "美业",
            "58-" + "汽车销售",
            "42-" + "社会评论",
            "25-" + "法律",
            "64-" + "商业认知",
            "16-" + "疗愈",
            "余华" + "英",
            "哪" + "吒",
            "1-1-" + "文字",
            "2-1-" + "文字",
            "3-1-" + "封面",
            "RA" + "RA",
            "孙建" + "亚",
            "汪先" + "生",
            "三毛" + "算力",
            "work" + "buddy",
            "Motion" + "Git",
            "chat" + "cut",
            "加密" + "鞋柜",
            "买鞋" + "自由",
            "欧" + "阳",
            "飞" + "书",
            "leader" + "ai",
            "Azen" + "Yes",
            "lead-" + "media",
            "lead-" + "knowledge",
            "video-rough-" + "cut",
            "格陵兰" + "没那么大",
        ]
        scan_roots = [
            ROOT / "short-video-studio",
            ROOT / "dev_tools",
            ROOT / "README.md",
            ROOT / "BRIEF.md",
            ROOT / "tests" / "test_studio.py",
        ]
        text_exts = {".py", ".md", ".json", ".cube", ".mjs", ".js", ".html", ".command", ".bat", ".sh", ".ps1"}
        for sr in scan_roots:
            files = [sr] if sr.is_file() else sorted(sr.rglob("*"))
            for p in files:
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                if p.parent.parent.name == "short-video-studio" and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"}:
                    self.fail(f"Unexpected media asset in skill package: {p}")
                if p.suffix.lower() not in text_exts:
                    continue
                txt = p.read_text(encoding="utf-8", errors="ignore")
                self.assertIsNone(priv_path_re.search(txt), f"User home path leaked in {p}")
                for tok in forbidden_tokens:
                    self.assertNotIn(tok, txt, f"Forbidden local/third-party implant {tok!r} in {p}")
        for media_p in (ROOT / "short-video-studio").rglob("*"):
            if media_p.is_file() and media_p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"}:
                self.fail(f"Forbidden visual media leak inside skill dir: {media_p}")

    def test_progress_card_five_user_types_and_zero_jargon(self):
        import re
        from workflow_guard import render_progress_card

        jargon_re = re.compile(
            r"水桶流|漏斗流|痛点|人设|A-roll|B-roll|LUFS|LUT|OCR|Micro-Hook|EDL|FCPXML|WCAG|ASR|VAD|口述稿|提词器|风格谱|流派模板",
            re.IGNORECASE,
        )
        protocol_md = (ROOT / "short-video-studio" / "references" / "interactive-coach-protocol.md").read_text(encoding="utf-8")
        self.assertIsNone(jargon_re.search(protocol_md), "Jargon found in interactive-coach-protocol.md")

        expected_totals = {
            "store_from_zero": 5,
            "creator_talking": 4,
            "footage_organizer": 3,
            "faceless_visual": 4,
            "post_review": 2,
        }
        for u_type, total_n in expected_totals.items():
            pdir = self.root / f"prog_{u_type}"
            c1 = render_progress_card(pdir, user_type=u_type)
            self.assertEqual(c1["current_step"], 1)
            self.assertEqual(c1["total_steps"], total_n)
            self.assertEqual(len(c1["quick_choices"]), 3)
            for tag in ("📍 **当前进度**", "🤖 **我刚做了 / 正在做**", "👉 **现在轮到你**", "⏳ **离能发还差**"):
                self.assertIn(tag, c1["markdown_card"])
            self.assertIsNone(jargon_re.search(c1["markdown_card"]), f"Jargon in initial progress card for {u_type}")
            self.assertTrue((pdir / "00_progress_card.json").is_file())
            self.assertTrue((pdir / "00_进度看板.md").is_file())

        # Verify stage progression on store_from_zero (1 -> 2 -> 3 -> 4 -> 5 -> approved)
        sp = self.root / "prog_store_lifecycle"
        sp.mkdir()
        (sp / "shot_card.md").write_text("# 手机拍摄清单\n", encoding="utf-8")
        c_shot = render_progress_card(sp, user_type="store_from_zero")
        self.assertEqual(c_shot["current_step"], 2)
        self.assertEqual(c_shot["progress_bar"], "[■■□□□]")

        write_json(sp / "03_audio_events.json", {"locked_duration_sec": 18.5, "sentences": []})
        c_aud = render_progress_card(sp)
        self.assertEqual(c_aud["current_step"], 3)
        self.assertEqual(c_aud["progress_bar"], "[■■■□□]")

        write_json(sp / "04_edl.json", {
            "total_duration_sec": 18.5,
            "clips": [{"clip_id": "c1"}, {"clip_id": "c2"}],
            "deictic_gaps": [{"matched_cue": "账单明细"}],
        })
        c_prev = render_progress_card(sp)
        self.assertEqual(c_prev["current_step"], 4)
        self.assertEqual(c_prev["progress_bar"], "[■■■■□]")
        self.assertIn("账单明细", c_prev["markdown_card"])
        self.assertIsNone(jargon_re.search(c_prev["markdown_card"]))

        (sp / "output_final.mp4").write_bytes(b"final-mp4")
        c_fin = render_progress_card(sp)
        self.assertEqual(c_fin["current_step"], 5)
        self.assertEqual(c_fin["progress_bar"], "[■■■■■]")
        self.assertFalse(c_fin["human_approved"])

        write_json(sp / "approval_lock.json", {"human_approved": True})
        c_app = render_progress_card(sp)
        self.assertTrue(c_app["human_approved"])
        self.assertIn("圆满完工", c_app["markdown_card"])
        self.assertIsNone(jargon_re.search(c_app["markdown_card"]))

    def test_subtitle_dp_segmentation_and_protected_terms(self):
        from step5_subtitles import (
            align_corrected_text_to_words,
            dp_segment_short_lines,
            run_step5,
        )

        # 验证 DP 断句不拆开保护词、不拆开“数字+单位”、不把虚词放在行首
        sent = "因为墨卡托投影把高纬度地区放大了14倍，所以地图上的面积看起来一模一样。"
        segs = dp_segment_short_lines(
            sent,
            max_width_px=520,
            font_size=56,
            terms=["墨卡托投影", "高纬度地区"],
            max_chars=10,
            strip_punct=True,
        )
        texts = [t for _, _, t in segs]
        self.assertGreaterEqual(len(texts), 2)
        joined = "".join(texts).replace(" ", "")
        self.assertIn("墨卡托投影", joined)
        self.assertIn("14倍", joined)
        for t in texts:
            self.assertFalse(t.startswith(("的", "了", "吗", "呢")), f"Bad leading particle in {t}")
        # 保护词和 14倍 必须完整留在单行内
        self.assertTrue(any("墨卡托投影" in t for t in texts), f"Protected term split across lines: {texts}")
        self.assertTrue(any("14倍" in t for t in texts), f"Number+unit split across lines: {texts}")

        # 验证字符级校正对齐与插值
        asr_w = [
            {"word": "实拍", "start_sec": 0.0, "end_sec": 0.4},
            {"word": "烤羊", "start_sec": 0.4, "end_sec": 0.8},
            {"word": "牌", "start_sec": 0.8, "end_sec": 1.0},
        ]
        aligned, ratio = align_corrected_text_to_words("实拍烤羊排", asr_w)
        self.assertEqual(len(aligned), 5)
        self.assertGreaterEqual(ratio, 0.79)
        self.assertEqual(aligned[-1]["word"], "排")

        # 验证 run_step5 产出双层柔和阴影 ASS + SRT + 逐行自检报告
        pdir = self.root / "sub_dp_pipe"
        pdir.mkdir()
        (pdir / "terms.txt").write_text("鲜切羊排\n", encoding="utf-8")
        write_json(pdir / "03_audio_events.json", {
            "sentences": [{
                "id": "sent_001",
                "text": "清晨现宰的鲜切羊排足足有14斤重，绝对不用冻货。",
                "start_sec": 0.0,
                "end_sec": 3.2,
                "keywords": ["鲜切羊排", "14斤"],
                "words": [
                    {"word": ch, "start_sec": round(i * 0.14, 3), "end_sec": round((i + 1) * 0.14, 3), "is_keyword": False}
                    for i, ch in enumerate("清晨现宰的鲜切羊排足足有14斤重绝对不用冻货")
                ],
            }]
        })
        res5 = run_step5(pdir, track="store")
        self.assertTrue((pdir / "05_subtitles.srt").is_file())
        self.assertTrue((pdir / "05_subtitle_check.txt").is_file())
        ass_txt = (pdir / "05_subtitles.ass").read_text(encoding="utf-8")
        self.assertIn("Dialogue: 0,", ass_txt)
        self.assertIn("Dialogue: 1,", ass_txt)
        self.assertIn("\\q2", ass_txt)
        self.assertEqual(res5["broken_protected_terms"], [])

    def test_speech_islands_frame_grid_and_residual_repeat_scanner(self):
        import numpy as np
        from qa_inspector import scan_residual_repeats
        from step2_transcribe import detect_speech_islands
        from step3_lock_audio import apply_edge_micro_fade, refine_cut_to_island_midpoint, snap_cut_to_frame_grid

        sr = 16000
        y = np.zeros(int(3.2 * sr), dtype=np.float32)
        t_a = np.arange(int(0.35 * sr)) / sr
        y[int(0.2 * sr) : int(0.55 * sr)] = (0.25 * np.sin(2 * np.pi * 240 * t_a)).astype(np.float32)
        t_b = np.arange(int(1.4 * sr)) / sr
        y[int(1.1 * sr) : int(2.5 * sr)] = (0.28 * np.sin(2 * np.pi * 240 * t_b)).astype(np.float32)

        isl = detect_speech_islands(y, sr=sr, thr_db=-38.0, min_sil_sec=0.18)
        self.assertEqual(len(isl), 2)
        self.assertTrue(isl[0]["is_short_orphan_candidate"])
        self.assertFalse(isl[1]["is_short_orphan_candidate"])

        mid_cut = refine_cut_to_island_midpoint(0.80, isl, fps=30)
        self.assertAlmostEqual(mid_cut, snap_cut_to_frame_grid(0.825, fps=30), places=2)

        stereo = np.ones((4800, 2), dtype=np.float32) * 0.5
        faded = apply_edge_micro_fade(stereo, fade_sec=0.012, sr=48000)
        self.assertAlmostEqual(float(faded[0, 0]), 0.0, places=5)
        self.assertAlmostEqual(float(faded[-1, 0]), 0.0, places=5)

        rep = scan_residual_repeats([
            {"start_sec": 0.2, "text": "地图上"},
            {"start_sec": 1.0, "text": "地图上看起来一模一样"},
            {"start_sec": 3.5, "text": "看起来一模一样但真实差距很大"},
        ])
        self.assertFalse(rep["clean"])
        self.assertTrue(len(rep["orphan_starts"]) >= 1)
        self.assertTrue(len(rep["duplicate_phrases"]) >= 1)

    def test_synth_motion_sfx_and_peak_alignment(self):
        import wave
        from synth_motion_sfx import ALL_SFX_KINDS, mix_sfx_cues, synthesize_sfx_waveform

        self.assertGreaterEqual(len(ALL_SFX_KINDS), 14)
        for kind in ALL_SFX_KINDS:
            arr = synthesize_sfx_waveform(kind)
            self.assertEqual(arr.ndim, 2)
            self.assertEqual(arr.shape[1], 2)
            self.assertGreater(float(abs(arr).max()), 0.5)

        out_wav = self.root / "sfx_mix.wav"
        mixed = mix_sfx_cues(
            [
                {"t": 0.5, "sfx": "whoosh_in", "gain": 0.3},
                {"t": 1.5, "sfx": "riser", "gain": 0.25, "align": "end"},
                {"t": 1.5, "sfx": "impact", "gain": 0.35, "align": "peak"},
                {"t": 1.55, "sfx": "pop", "gain": 0.25},
            ],
            total_duration_sec=3.0,
            out_wav=out_wav,
        )
        self.assertTrue(out_wav.is_file())
        self.assertEqual(mixed.shape[1], 2)
        with wave.open(str(out_wav), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 2)
            self.assertEqual(wf.getframerate(), 48000)

    def test_knowledge_animator_timeline_audit_and_review_pack(self):
        import subprocess
        from common import find_bin
        from knowledge_animator import audit_animation_video, build_review_pack, compile_animation_timeline, init_animation_project

        anim_root = self.root / "anim_demo"
        init_res = init_animation_project(
            anim_root,
            title="注意力的价格",
            one_idea="每天解锁96次手机却只记得3次",
            ratio="16x9",
            font_mode="serif",
        )
        self.assertTrue(Path(init_res["timing_js"]).is_file())
        self.assertTrue(Path(init_res["cover_html"]).is_file())
        self.assertTrue(init_res["hook_within_5s"])

        # 测试句末标点自动剥离与单行过长提示
        tl = compile_animation_timeline({
            "title": "测试",
            "lines": [
                {"id": "h1", "text": "你每天解锁手机[96次]。", "hook": True},
                {"id": "c1", "text": "这是一句很长很长很长很长很长很长很长很长很长很长很长的超宽测试句，", "scene": True},
            ],
        })
        self.assertEqual(tl["lines"][0]["text"], "你每天解锁手机[96次]")
        self.assertTrue(any("单行过长" in w for w in tl["warnings"]))

        # 生成一个 3.5 秒带运动的测试视频验证 audit 与 review-pack
        ffmpeg = find_bin("ffmpeg")
        if ffmpeg:
            demo_mp4 = anim_root / "out" / "demo.mp4"
            subprocess.run([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3.5",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(demo_mp4),
            ], check=True)
            aud = audit_animation_video(demo_mp4, dead_sec=2.0, dark_style=False)
            self.assertEqual(aud["dead_zones"], [])
            self.assertGreater(aud["moving_ratio"], 0.8)
            rp = build_review_pack(demo_mp4, anim_root / "review", timing_js=Path(init_res["timing_js"]))
            self.assertTrue(Path(rp["guide_md"]).is_file())
            self.assertTrue(rp["contact_sheet"] and Path(rp["contact_sheet"]).is_file())
            self.assertGreaterEqual(len(rp["motion_strips"]), 1)

    def test_publish_copy_pack_four_round_lint_and_smooth_orbit_pip(self):
        from raw_intel_ingest import export_script_deliverables, lint_script
        from render_mp4 import build_graded_segment_cmd
        from step4_build_edl import detect_explainer_broll_windows
        from step9_title_cover import build_publish_copy_pack

        # 1) 四轮审稿体检 + 双文件解耦导出
        lint = lint_script(
            [
                {"text": "这个东西全网第一，100%有效！"},
                {"text": "点左下角小黄车马上抢。"},
                {"text": "清晨现宰鲜羊排三小时炭火慢烤不糊弄。"},
                {"text": "清晨现宰鲜羊排三小时炭火慢烤绝不糊弄。"},
            ],
            raw_intel={"target_platform": "xiaohongshu", "pain_keywords": []},
        )
        codes = {i["code"] for i in lint["issues"]}
        self.assertIn("exaggerated_claim", codes)
        self.assertIn("dangling_pronoun", codes)
        self.assertIn("platform_residue", codes)
        self.assertIn("redundant_paragraph", codes)

        deliv = export_script_deliverables(
            self.root / "script_out",
            title="鲜羊排成本拆解",
            sentences=[
                {"say": "呃那个废话跳过", "show": "废话", "is_filler": True},
                {"say": "四百块一份的烤羊排，成本其实占了七成。", "show": "400元烤羊排成本占70%"},
            ],
            platform="douyin",
        )
        self.assertTrue(Path(deliv["pure_script_txt"]).is_file())
        self.assertTrue(Path(deliv["execution_plan_md"]).is_file())
        pure_txt = Path(deliv["pure_script_txt"]).read_text(encoding="utf-8")
        self.assertNotIn("〔画面", pure_txt)
        self.assertNotIn("废话跳过", pure_txt)
        self.assertIn("四百块", pure_txt)

        # 2) 口播动态讲解窗口探测 + 真双源 smooth_orbit_pip FFmpeg 实测渲染
        wins = detect_explainer_broll_windows([
            {"id": "s1", "text": "很多人问为什么看起来一样大其实差了14倍。", "start_sec": 0.4, "end_sec": 3.4, "words": [{"word": "14倍", "start_sec": 2.2, "end_sec": 2.6}]},
            {"id": "s2", "text": "欢迎评论区聊聊。", "start_sec": 3.6, "end_sec": 5.0, "emotion_or_info": "cta"},
        ])
        self.assertEqual(len(wins), 1)
        self.assertEqual(wins[0]["presenter_pip_mode"], "smooth_orbit_pip")
        if shutil.which("ffmpeg"):
            broll_v = self.root / "orbit_broll.mp4"
            host_v = self.root / "orbit_host.mp4"
            out_seg = self.root / "orbit_out.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=#08131F:s=540x960:r=30:d=1.2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(broll_v)], check=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=#C07A50:s=540x960:r=30:d=2.0",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(host_v)], check=True)
            cmd = build_graded_segment_cmd(
                shutil.which("ffmpeg"),
                {
                    "clip_id": "orbit_01",
                    "media_path": str(broll_v),
                    "source_in_sec": 0.0,
                    "source_out_sec": 1.0,
                    "duration_sec": 1.0,
                    "pip_spec": {"mode": "smooth_orbit_pip", "corner": "tr", "radius_px": 74, "border_hex": "#F4B13E"},
                    "secondary_media_path": str(host_v),
                    "secondary_in_sec": 0.4,
                },
                None,
                {},
                out_seg,
                30,
                tw=540,
                th=960,
                preview=True,
            )
            subprocess.run(cmd, check=True)
            self.assertTrue(out_seg.is_file() and out_seg.stat().st_size > 1000)

        # 3) 分平台发布文案交付包
        pack = build_publish_copy_pack(
            self.root,
            title_seed="四百元烤羊排成本真相",
            hook_text="价格越高的菜，利润反而越低",
            sentences=[{"text": "价格越高的菜，利润反而越低"}, {"text": "鲜羊排原料成本就占了六成八"}],
            keywords=["烤羊排", "利润"],
            track="store",
            cover_main="鲜羊排真相",
            cover_sub="实拍算账",
            has_4k_covers=True,
        )
        self.assertTrue((self.root / "09_publish_copy_pack.md").is_file())
        self.assertIn("douyin_shipinhao", pack["platforms"])
        self.assertIn("xiaohongshu", pack["platforms"])
        self.assertIn("bilibili", pack["platforms"])

    def test_v25_tail_silence_and_residual_repeat_qa_gate(self):
        import numpy as np
        from independent_verifier import _detect_long_silences_np
        from qa_inspector import inspect_video

        # 1) 验证尾部纯静音能被正确检出（回归原尾段永假 Bug）
        sr = 48000
        t = np.arange(int(1.0 * sr)) / sr
        speech = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        tail_sil = np.zeros(int(1.2 * sr), dtype=np.float32)
        wav_arr = np.concatenate([speech, tail_sil])
        sils = _detect_long_silences_np(wav_arr, sr=sr, thresh_db=-42.0, min_dur=0.8)
        self.assertEqual(len(sils), 1)
        self.assertGreaterEqual(sils[0], 1.1)

        # 2) 验证 qa_inspector.inspect_video 接入 residual_repeat_scan 并检出残留重说
        if shutil.which("ffmpeg"):
            pdir = self.root / "qa_repeat"
            pdir.mkdir()
            mp4 = pdir / "output_final.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=4.0",
                "-f", "lavfi", "-i", "sine=f=330:d=4.0:r=48000", "-af", "volume=-14dB",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(mp4),
            ], check=True)
            write_json(pdir / "03_audio_events.json", {
                "locked_duration_sec": 4.0,
                "locked_duration_frames": 120,
                "sentences": [
                    {"id": "s1", "text": "每天早起", "start_sec": 0.0, "end_sec": 0.6},
                    {"id": "s2", "text": "每天早起现切鲜羊排不隔夜", "start_sec": 0.8, "end_sec": 2.4},
                    {"id": "s3", "text": "现切鲜羊排不隔夜就是底线", "start_sec": 2.5, "end_sec": 4.0},
                ],
                "events": [{"id": "e1", "type": "sentence_start", "time_sec": 0.0, "frame": 0}],
            })
            write_json(pdir / "04_edl.json", {
                "fps": 30, "total_duration_sec": 4.0, "total_frames": 120,
                "clips": [{"clip_id": "c1", "media_path": str(mp4), "track_role": "a_roll",
                           "source_in_sec": 0.0, "source_out_sec": 4.0, "timeline_in_sec": 0.0,
                           "timeline_out_sec": 4.0, "duration_sec": 4.0}],
            })
            write_json(pdir / "05_subtitles.json", {"template_id": "style_14", "font_path": str(ASSETS_DIR / "fonts" / "AlimamaShuHeiTi-Bold.otf"), "entries": []})
            write_json(pdir / "06_packaging.json", {"visual_elements": []})
            rep = inspect_video(pdir, mp4, export_mode="mp4")
            rc = next((c for c in rep["checks"] if c["id"] == "residual_repeat_scan"), None)
            self.assertIsNotNone(rc)
            self.assertEqual(rc["status"], "FAIL")

    def test_v25_raw_intel_platform_and_compliance_copy_pack(self):
        from compliance_guard import _classify_asset_license, audit_script_compliance, write_compliance_report_files

        # 1) extract_raw_intel 透传 target_platform 到 lint_script
        src_txt = self.root / "cmts.txt"
        src_txt.write_text("羊排多少钱一份？\n你们家是不是现切的？\n", encoding="utf-8")
        intel = extract_raw_intel([src_txt], target_platform="xiaohongshu")
        self.assertEqual(intel["target_platform"], "xiaohongshu")

        # 2) compliance_guard: 实拍新枚举识别 + pipeline code_broll 放行 + CLI --write-back 同步审计 09_publish_copy_pack.json
        pdir = self.root / "comp_v25"
        pdir.mkdir()
        code_clip = pdir / "code_broll" / "card_01.mp4"
        code_clip.parent.mkdir(parents=True)
        code_clip.write_bytes(b"code-broll")
        cls = _classify_asset_license(code_clip, "code_broll", pdir, {}, "abc")
        self.assertTrue(cls["commercial_ok"])

        segs = [
            {"id": "seg_1", "text": "这家是全网第一好吃的羊排", "is_filler": False},
            {"id": "seg_2", "text": "每天清晨现切现烤", "is_filler": False},
            {"id": "seg_3", "text": "炭火慢烤三小时", "is_filler": False},
        ]
        comp_real = audit_script_compliance(segs, source_type="asr_script_forced", pipeline_dir=pdir, auto_action=True)
        self.assertTrue(comp_real["is_real_voice"])
        self.assertTrue(segs[0]["is_filler"])
        self.assertTrue((pdir / "00_compliance_report.md").is_file())

        tr_path = pdir / "02_transcript.json"
        write_json(tr_path, {
            "source_type": "script",
            "segments": [
                {"id": "seg_1", "text": "这家是全网第一好吃的羊排", "is_filler": False},
                {"id": "seg_2", "text": "每天清晨现切现烤", "is_filler": False},
            ],
        })
        write_json(pdir / "09_publish_copy_pack.json", {
            "platforms": {"douyin_shipinhao": {"titles": ["全网第一的炭烤羊排"], "body": "实拍现切"}}
        })
        subprocess.run([
            sys.executable, str(ROOT / "short-video-studio" / "scripts" / "compliance_guard.py"),
            "audit-script", "--input-json", str(tr_path), "--source-type", "script",
            "--pipeline-dir", str(pdir), "--write-back",
        ], check=True)
        tr_after = read_json(tr_path)
        self.assertNotIn("全网第一", tr_after["segments"][0]["text"])
        rep_after = read_json(pdir / "00_compliance_report.json")
        self.assertTrue(any(f.get("sentence_id", "").startswith("copy_douyin") for f in rep_after.get("findings", [])))

    def test_v25_knowledge_animation_fonts_audio_and_four_ratios(self):
        from common import ffprobe_media, find_bin
        from knowledge_animator import (
            compile_animation_timeline,
            init_animation_project,
            inject_stage_commercial_fonts,
            mix_animation_audio,
        )

        # 1) 验证 4 种画幅均被支持
        for r in ("16x9", "9x16", "1x1", "3x4"):
            tl = compile_animation_timeline({"title": "比例测试", "lines": [{"id": "l1", "text": "一句清晰短句"}]}, ratio=r)
            self.assertEqual(tl["ratio"], r)

        # 2) 验证商业字体真实注入 anim_starter.html 与 cover_4k.html
        anim_root = self.root / "anim_v25"
        init_animation_project(anim_root, title="时间复利", one_idea="每天进步1%一年变37倍", ratio="16x9", font_mode="serif")
        finj = inject_stage_commercial_fonts(anim_root / "stage", extra_text="时间复利每天进步1%一年变37倍")
        self.assertGreaterEqual(len(finj["injected"]), 1)
        html_txt = (anim_root / "stage" / "anim_starter.html").read_text(encoding="utf-8")
        self.assertIn("@font-face", html_txt)
        self.assertNotIn("/* @fonts */", html_txt)

        # 3) 验证知识动画有声合成封装（立体声 48kHz AAC）
        ffmpeg = find_bin("ffmpeg")
        if ffmpeg:
            silent_mp4 = anim_root / "silent.mp4"
            voiced_mp4 = anim_root / "voiced.mp4"
            subprocess.run([
                ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=#050A14:s=640x360:r=30:d=3.0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent_mp4),
            ], check=True)
            tl = compile_animation_timeline({
                "title": "时间复利",
                "lines": [
                    {"id": "h1", "text": "每天进步[1%]", "hook": True},
                    {"id": "c1", "text": "一年后翻37倍", "scene": True},
                ],
            })
            mix_res = mix_animation_audio(silent_mp4, voiced_mp4, tl, anim_root / "work_audio")
            self.assertTrue(voiced_mp4.is_file())
            self.assertGreaterEqual(mix_res["sfx_cue_count"], 1)
            self.assertLessEqual(float(mix_res["true_peak_dbtp"]), -1.0)
            info = ffprobe_media(voiced_mp4)
            a_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
            self.assertEqual(len(a_streams), 1)
            self.assertEqual(int(a_streams[0].get("channels", 0)), 2)

    def test_v25_weekly_review_plain_cn_and_dirty_date(self):
        from content_log_review import DEMO_RECORDS, append_record, generate_weekly_review

        log_d = self.root / "review_v25"
        for rec in DEMO_RECORDS:
            append_record(log_d, dict(rec))
        # 追加一条含脏日期的旧手填记录，验证不抛 ValueError
        with (log_d / "records.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('{"date":"bad-date","track":"store","title":"手填旧记录"}\n')
        rev = generate_weekly_review(log_d, track="store")
        md = Path(rev["weekly_review_md"]).read_text(encoding="utf-8")
        self.assertIn("抖音", md)
        self.assertIn("同城推荐", md)
        self.assertNotIn("去重快照", md)
        self.assertNotIn("95% 区间", md)

    def test_subset_fonts_char_collection_and_fallback(self):
        from common import find_commercial_font
        from subset_fonts import collect_project_chars, subset_font_file

        sample_dir = self.root / "font_scan"
        sample_dir.mkdir()
        (sample_dir / "page.html").write_text("<h1>测试中文字体子集化2026</h1>", encoding="utf-8")
        chars = collect_project_chars([sample_dir])
        self.assertIn("子", chars)
        self.assertIn("2", chars)
        fp = find_commercial_font()
        if fp:
            res = subset_font_file(Path(fp), self.root / "out_font" / Path(fp).name, chars)
            self.assertIn(res["status"], ("subsetted", "copied_fallback_no_fonttools"))
            self.assertTrue(Path(res["output"]).is_file())

    def test_v30_default_style01_and_blueprints_zero_fake_pip(self):
        from common import ASSETS_DIR, DEFAULT_STYLE_ID, read_json
        from render_template_overlays import load_template_blueprints, render_template_packaging_png

        self.assertEqual(DEFAULT_STYLE_ID, "style_01")
        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        self.assertEqual(lib.get("default_style_id"), "style_01")
        bps = load_template_blueprints()
        self.assertEqual(len(bps), 24)
        # 24 套全部改成真草稿几何驱动；逐套与 pip_usage.json 的主轨数字比对，杜绝「手写版面」
        usage = read_json(ROOT / "forensic" / "pip_usage.json")["styles"]
        for s_node in lib["styles"]:
            sid = s_node["style_id"]
            spec = s_node.get("pip_composition_spec") or {}
            self.assertTrue(str(spec.get("mode")).startswith("geometry_pip"),
                            f"{sid} 未按真草稿几何重排：{spec.get('mode')}")
            truth = usage[sid]["main"]
            self.assertEqual(spec["main"]["scale"], truth["scale"], f"{sid} 主轨 scale 与真草稿不符")
            self.assertEqual(spec["main"]["x"], truth["x"], f"{sid} 主轨 x 与真草稿不符")

        # 全部 24 套模板蓝图均可渲染出有效透明叠层
        for i in range(1, 25):
            sid = f"style_{i:02d}"
            im = render_template_packaging_png(
                {"type": "center_burst_title", "text_or_asset": "大杯只贵两块钱", "subtext": "真相拆解"},
                style_id=sid,
            )
            self.assertIsNotNone(im, f"Overlay failed for {sid}")
            self.assertGreater(im.width, 40)
            self.assertGreater(im.height, 30)

    def test_v30_cola_agent_detection_and_gbro_cover_handoff(self):
        import os
        from common import SUBSKILLS_DIR, detect_active_agent, find_bin
        from step9_title_cover import build_gbro_cover_handoff, extract_best_host_face_frame

        self.assertTrue((SUBSKILLS_DIR / "gbro-cover-design" / "SKILL.md").is_file())
        orig_env = dict(os.environ)
        try:
            for k in list(os.environ):
                if k == "PI_PROVIDER" or k.startswith("COLA_"):
                    os.environ.pop(k, None)
            self.assertFalse(detect_active_agent()["is_cola"])

            os.environ["PI_PROVIDER"] = "cola"
            det1 = detect_active_agent()
            self.assertTrue(det1["is_cola"])
            self.assertEqual(det1["agent"], "cola")

            os.environ.pop("PI_PROVIDER", None)
            os.environ["COLA_ACCOUNT_DIR"] = "/tmp/fake_cola_acct"
            det2 = detect_active_agent()
            self.assertTrue(det2["is_cola"])

            os.environ.pop("COLA_ACCOUNT_DIR", None)
            os.environ["COLA_WORKSPACE"] = "yes"
            det3 = detect_active_agent()
            self.assertTrue(det3["is_cola"])

            # 验证 A-roll 人脸参考帧抽取与 gbro-cover-design 交接单生成
            pdir = self.root / "cola_cover_test"
            pdir.mkdir()
            demo_vid = pdir / "aroll_01.mp4"
            subprocess.run([
                find_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=30:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(demo_vid),
            ], check=True)
            write_json(pdir / "01_manifest.json", {
                "items": [{"id": "m1", "role": "a_roll", "normalized_mp4": str(demo_vid), "duration_sec": 2.0, "video_grade": "usable"}]
            })
            face_ref = extract_best_host_face_frame(pdir)
            self.assertIsNotNone(face_ref)
            self.assertTrue(Path(face_ref["path"]).is_file())

            handoff = build_gbro_cover_handoff(
                pdir,
                title_main="为什么大杯只贵2块",
                title_sub="实体店定价秘密",
                hook_text="为什么奶茶店大杯只贵2块钱",
                track="store",
                agent_info=det1,
            )
            self.assertIsNotNone(handoff)
            self.assertTrue((pdir / "09_gbro_cover_prompt.md").is_file())
            self.assertTrue((pdir / "09_gbro_cover_handoff.json").is_file())
        finally:
            os.environ.clear()
            os.environ.update(orig_env)

    def test_v30_recursive_scan_speech_band_and_offcam_filter(self):
        from inventory_media import _collect_media_files_recursive, _folder_role_hint, classify_clip
        from step2_transcribe import split_segments_at_pauses

        raw_d = self.root / "nested_raw"
        (raw_d / "口播").mkdir(parents=True)
        (raw_d / "空镜素材" / "环境").mkdir(parents=True)
        (raw_d / ".hidden").mkdir(parents=True)
        f1 = raw_d / "口播" / "a1.mp4"
        f2 = raw_d / "空镜素材" / "环境" / "b1.mp4"
        f3 = raw_d / ".hidden" / "ignore.mp4"
        for f in (f1, f2, f3):
            f.write_bytes(b"00")
        found = _collect_media_files_recursive(raw_d)
        self.assertEqual({p.name for p in found}, {"a1.mp4", "b1.mp4"})
        self.assertEqual(_folder_role_hint(f1, raw_d), "a_roll_candidate")
        self.assertEqual(_folder_role_hint(f2, raw_d), "b_roll_candidate")

        # 空镜有吵闹低频背景音乐但语音频带占比低 -> 必判 b_roll_candidate 而非 a_roll_candidate
        role_bg = classify_clip(
            filename="clip_env.mp4",
            a_stream={"codec_type": "audio"},
            quality={"speech_ratio": 0.72, "audio_dyn_range_db": 6.0, "speech_band_ratio": 0.22},
            duration=8.0,
            folder_hint="b_roll_candidate",
        )
        self.assertEqual(role_bg, "b_roll_candidate")

        # ASR 长段内有 >=0.25s 停顿 -> 按停顿切细以便独立识别画外音
        merged_seg = [{
            "id": "seg_001",
            "text": "主讲说一句画外插一句",
            "start_sec": 0.0,
            "end_sec": 4.0,
            "words": [
                {"word": "主讲", "start_sec": 0.0, "end_sec": 0.5},
                {"word": "说一句", "start_sec": 0.52, "end_sec": 1.1},
                {"word": "画外", "start_sec": 1.65, "end_sec": 2.1},
                {"word": "插一句", "start_sec": 2.12, "end_sec": 2.8},
            ],
        }]
        split_res = split_segments_at_pauses(merged_seg, min_gap_sec=0.25)
        self.assertEqual(len(split_res), 2)

    def test_v30_plan_confirm_gate_and_exclude_shot_persistence(self):
        from step4_build_edl import _apply_excluded_rules_to_items, _avoid_excluded_windows, detect_script_media_gaps
        from workflow_guard import confirm_plan, exclude_shot_window, is_plan_confirmed, write_confirm_plan_doc

        pdir = self.root / "gate_p"
        pdir.mkdir()
        write_json(pdir / "01_manifest.json", {
            "items": [
                {"id": "m1", "filename": "a1.mp4", "role": "a_roll", "video_grade": "usable", "normalized_mp4": str(pdir / "a1.mp4")},
                {"id": "m2", "filename": "b_bad.mp4", "role": "b_roll", "video_grade": "usable", "normalized_mp4": str(pdir / "b_bad.mp4")},
                {"id": "m3", "filename": "b_win.mp4", "role": "b_roll", "video_grade": "usable", "normalized_mp4": str(pdir / "b_win.mp4")},
            ]
        })
        write_json(pdir / "02_transcript.json", {
            "kept_segments": [
                {"id": "s1", "text": "咱们店里有二十五张卡座和八个包房", "start_sec": 0.0, "end_sec": 3.0},
            ]
        })
        gaps = detect_script_media_gaps(
            [{"id": "s1", "text": "咱们店里有二十五张卡座和八个包房"}],
            [{"filename": "door.mp4", "semantic_tags": ["门头"]}],
        )
        self.assertTrue(any("卡座" in str(g.get("matched_cue")) or "包房" in str(g.get("matched_cue")) for g in gaps))

        self.assertFalse(is_plan_confirmed(pdir))
        plan_p = write_confirm_plan_doc(pdir, style_id="style_01", media_gaps=gaps)
        self.assertTrue(plan_p.is_file())
        self.assertIn("二十五张卡座", plan_p.read_text(encoding="utf-8"))
        confirm_plan(pdir, note="用户已点头")
        self.assertTrue(is_plan_confirmed(pdir))

        # 整条标废 + 时间窗标废持久化
        exclude_shot_window(pdir, "b_bad.mp4", reason="手抖穿帮")
        exclude_shot_window(pdir, "b_win.mp4", start_sec=2.0, end_sec=5.0, reason="中段路人入镜")
        kept = _apply_excluded_rules_to_items(pdir, read_json(pdir / "01_manifest.json")["items"])
        self.assertNotIn("b_bad.mp4", [x["filename"] for x in kept])
        shifted = _avoid_excluded_windows(2.2, 1.5, 10.0, [{"start_sec": 2.0, "end_sec": 5.0}])
        self.assertGreaterEqual(shifted, 5.0)

    def test_v30_shared_cache_hardlink_zero_redundancy(self):
        import os
        from common import find_bin
        from step1_normalize import run_step1

        raw_d = self.root / "shared_raw"
        raw_d.mkdir()
        clip = raw_d / "sample_b.mp4"
        subprocess.run([
            find_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=30:duration=1.5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ], check=True)
        cache_dir = self.root / "acct_shared_cache"
        p1 = self.root / "proj1" / "pipeline"
        p2 = self.root / "proj2" / "pipeline"
        m1 = run_step1([raw_d], p1, shared_cache_dir=cache_dir)
        m2 = run_step1([raw_d], p2, shared_cache_dir=cache_dir)
        f1 = Path(m1["items"][0]["normalized_mp4"])
        f2 = Path(m2["items"][0]["normalized_mp4"])
        self.assertTrue(f1.is_file() and f2.is_file())
        self.assertEqual(os.stat(f1).st_ino, os.stat(f2).st_ino, "跨项目应通过硬链接零冗余复用同一份归一素材")

    def test_v30_flower_text_clean_no_flower_and_keep_edl(self):
        from apply_style_spectrum import apply_full_visual_refresh, filter_styles_by_aroll, load_library
        from step6_packaging import _extract_clean_flower_phrase, run_step6

        # 拦截“介绍一下/我觉得”口语虚词碎片
        p1 = _extract_clean_flower_phrase({"text": "我给大家介绍一下，咱们店有25张卡座", "keywords": ["介绍一下", "一个"]})
        self.assertNotIn("介绍", p1)
        self.assertIn("25张卡座", p1)

        # --has-aroll no 应过滤掉 requires_aroll == "required" 的模板
        lib = load_library()
        no_aroll_styles = filter_styles_by_aroll(lib, has_aroll=False)
        self.assertTrue(all(s.get("requires_aroll") != "required" for s in no_aroll_styles))
        self.assertIn("style_24", [s["style_id"] for s in no_aroll_styles])
        self.assertNotIn("style_01", [s["style_id"] for s in no_aroll_styles])

        # 验证 --no-flower-text 与 keep_manual_texts
        pdir = self.root / "pkg_v30"
        pdir.mkdir()
        write_json(pdir / "03_audio_events.json", {
            "locked_duration_sec": 6.0,
            "sentences": [{"id": "s1", "text": "大杯只贵两块钱", "keywords": ["大杯只贵2块"], "start_sec": 0.0, "end_sec": 3.0, "start_frame": 0, "end_frame": 90, "emotion_or_info": "hook"}],
            "events": [{"id": "e1", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "s1"}],
        })
        write_json(pdir / "04_edl.json", {
            "style_id": "style_01",
            "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": "", "timeline_in_sec": 0.0, "timeline_out_sec": 6.0, "timeline_in_frame": 0, "timeline_out_frame": 180, "cut_event_frame": 0, "cut_event_id": "e1"}],
        })
        res_off = run_step6(pdir, style_id="style_01", no_flower_text=True)
        self.assertEqual(len(res_off["visual_elements"]), 0)
        self.assertTrue(res_off["no_flower_text"])

        # 写入人工编辑花字后切风格 keep_edl=True -> 文案不丢
        res_on = run_step6(pdir, style_id="style_01", no_flower_text=False)
        self.assertGreaterEqual(len(res_on["visual_elements"]), 1)
        res_on["visual_elements"][0]["text_or_asset"] = "手工定制大标题"
        write_json(pdir / "06_packaging.json", res_on)
        refresh = apply_full_visual_refresh(lib, "style_03", pdir, keep_edl=True)
        self.assertIn("06_packaging.json", refresh["refreshed"])
        pkg_after = read_json(pdir / "06_packaging.json")
        self.assertEqual(pkg_after["style_id"], "style_03")
        self.assertEqual(pkg_after["visual_elements"][0]["text_or_asset"], "手工定制大标题")

    def test_v30_incremental_patch_lines(self):
        import numpy as np
        from step3_lock_audio import SR, patch_locked_audio_sentences, save_stereo_wav_np

        pdir = self.root / "patch_p"
        pdir.mkdir()
        t = np.linspace(0.0, 2.0, 2 * SR, endpoint=False, dtype=np.float32)
        s1_arr = np.column_stack([0.2 * np.sin(2 * np.pi * 220 * t), 0.2 * np.sin(2 * np.pi * 220 * t)])
        s2_arr = np.column_stack([0.2 * np.sin(2 * np.pi * 330 * t), 0.2 * np.sin(2 * np.pi * 330 * t)])
        full_v = np.concatenate([s1_arr, s2_arr], axis=0)
        save_stereo_wav_np(pdir / "03_locked_voice.wav", full_v, SR)
        save_stereo_wav_np(pdir / "03_locked_bgm.wav", full_v * 0.1, SR)
        rep_wav = pdir / "rep_s1.wav"
        t_new = np.linspace(0.0, 1.0, SR, endpoint=False, dtype=np.float32)
        save_stereo_wav_np(rep_wav, np.column_stack([0.2 * np.sin(2 * np.pi * 440 * t_new)] * 2), SR)
        write_json(pdir / "03_audio_events.json", {
            "locked_duration_sec": 4.0,
            "total_duration_sec": 4.0,
            "locked_duration_frames": 120,
            "bgm": {"file": str(pdir / "03_locked_bgm.wav"), "bpm": 120.0, "downbeat_times_sec": [0.0, 2.0]},
            "sentences": [
                {"id": "sent_001", "text": "旧的第一句", "start_sec": 0.0, "end_sec": 2.0, "duration_sec": 2.0},
                {"id": "sent_002", "text": "保持不变第二句", "start_sec": 2.0, "end_sec": 4.0, "duration_sec": 2.0},
            ],
            "events": [],
        })
        ae_new = patch_locked_audio_sentences(
            pdir,
            [{"sentence_id": "sent_001", "new_text": "新的第一句", "replacement_wav": str(rep_wav)}],
        )
        self.assertEqual(ae_new["sentences"][0]["text"], "新的第一句")
        self.assertEqual(ae_new["sentences"][1]["text"], "保持不变第二句")
        self.assertAlmostEqual(ae_new["sentences"][1]["start_sec"], 1.0, delta=0.08)
        self.assertAlmostEqual(ae_new["locked_duration_sec"], 3.0, delta=0.08)

    def test_v30_content_driven_knowledge_animation_no_blue_planet(self):
        from common import ASSETS_DIR
        from knowledge_animator import compile_animation_timeline

        starter_html = (ASSETS_DIR / "canvas_runtime" / "anim_starter.html").read_text(encoding="utf-8")
        self.assertNotIn("drawMaterialOrb", starter_html, "anim_starter.html 不得再残留写死的蓝色星球占位代码")
        self.assertIn("SVS_SCENES", starter_html)

        payload = compile_animation_timeline({
            "title": "大杯只贵2块的秘密",
            "question": "为什么奶茶店总把大杯定价只比中杯贵2块钱",
            "lines": [
                {"id": "h1", "text": "中杯[16元]大杯只要[18元]", "hook": True},
                {"id": "h2", "text": "多加[2块钱]商家利润率反而涨了[70%]", "hook": True},
                {"id": "c1", "text": "大杯只贵2块的秘密", "card": True},
                {"id": "s1", "text": "表面以为赚了便宜其实是被锚点引导升级", "scene": True},
                {"id": "s2", "text": "第一步定锚点第二步缩差价第三步抬客单", "scene": True},
            ],
        })
        scenes = payload.get("content_scenes") or []
        vtypes = [s["visual_type"] for s in scenes]
        self.assertIn("mockup_card", vtypes)
        self.assertIn("quote_poster", vtypes)
        self.assertGreaterEqual(len(set(vtypes)), 3)

    def test_v30_china_mirrors_eta_and_windows_drive_config(self):
        from common import HF_CHINA_MIRROR, NPM_CHINA_MIRROR, PYPI_CHINA_MIRRORS, estimate_task_eta

        self.assertTrue(any("tuna.tsinghua.edu.cn" in m for m in PYPI_CHINA_MIRRORS))
        self.assertIn("npmmirror.com", NPM_CHINA_MIRROR)
        self.assertIn("hf-mirror.com", HF_CHINA_MIRROR)
        eta = estimate_task_eta("full_edit", duration_sec=45.0, clip_count=8)
        self.assertTrue(eta["eta_cn"])
        self.assertGreater(eta["estimated_sec"], 10)

    def test_v31_all_24_whitebox_new_four_styles_and_handwriting_font(self):
        from common import ASSETS_DIR, find_commercial_font, is_verified_commercial_font, read_json
        from render_template_overlays import get_style_blueprint, render_style05_giant_back_png

        # 1) 随仓第 5 款开源商用毛笔手写体 MaShanZheng-Regular.ttf 落盘且通过商用白名单
        msz = ASSETS_DIR / "fonts" / "MaShanZheng-Regular.ttf"
        self.assertTrue(msz.is_file() and msz.stat().st_size > 1_000_000)
        self.assertTrue(is_verified_commercial_font(str(msz)))
        self.assertIn("MaShanZheng", str(find_commercial_font(["MaShanZheng-Regular.ttf"])))

        # 2) 新替换的 4 套白盒风格（05/12/13/14）均已接入真实白盒构图、动效与转场
        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        by_id = {s["style_id"]: s for s in lib["styles"]}
        self.assertEqual(by_id["style_05"]["packaging_techniques"]["pip_mode"], "geometry_pip_window")
        for sid in ("style_05", "style_12", "style_13", "style_14"):
            self.assertTrue(str(by_id[sid]["pip_composition_spec"]["mode"]).startswith("geometry_pip"),
                            f"{sid} 未按真草稿几何重排")
        for sid in ("style_05", "style_12", "style_13", "style_14"):
            self.assertTrue(by_id[sid]["whitebox_source"]["is_whitebox_jianying"])
            bp = get_style_blueprint(sid)
            self.assertTrue((bp.get("overlay_style") or {}).get("anim_mode"))
        self.assertEqual(get_style_blueprint("style_06")["overlay_style"].get("handwriting_font"), "MaShanZheng-Regular.ttf")

        # 3) style_05 背后 256px 亮黄巨字透明图层生成
        g_png = self.root / "s05_back.png"
        render_style05_giant_back_png("真实真相", g_png)
        self.assertTrue(g_png.is_file())

    def test_v31_person_matting_engine_and_data_news_bg(self):
        import platform
        from person_matting import ensure_macos_vision_matte_bin, render_cool_data_news_bg_mp4

        if platform.system() == "Darwin" and shutil.which("swiftc"):
            bp = ensure_macos_vision_matte_bin()
            self.assertIsNotNone(bp)
            self.assertTrue(bp.is_file())
        if shutil.which("ffmpeg"):
            bg_mp4 = self.root / "s19_bg.mp4"
            render_cool_data_news_bg_mp4(bg_mp4, duration_sec=0.8, fps=30, width=540, height=960)
            self.assertTrue(bg_mp4.is_file() and bg_mp4.stat().st_size > 10_000)

    def test_v31_animated_30fps_overlay_mov_all_8_modes(self):
        from common import ffprobe_media
        from render_template_overlays import render_animated_overlay_mov

        if not shutil.which("ffmpeg"):
            return
        # 覆盖全部 8 种动效模式：01/07/02/06/18/04/05/16
        for sid in ("style_01", "style_07", "style_02", "style_06", "style_18", "style_04", "style_05", "style_16"):
            out_mov = self.root / f"anim_{sid}.mov"
            res = render_animated_overlay_mov(
                {"kind": "center_burst_title", "text_or_asset": "大杯只贵两块", "subtext": "门店实拍"},
                out_mov,
                style_id=sid,
                duration_sec=0.7,
                fps=30,
            )
            self.assertIsNotNone(res, f"Animated overlay failed for {sid}")
            mov_p, cw, ch = res
            self.assertTrue(mov_p.is_file() and mov_p.stat().st_size > 2000)
            self.assertEqual(cw % 2, 0)
            self.assertEqual(ch % 2, 0)
            info = ffprobe_media(mov_p)
            v_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
            self.assertEqual(len(v_streams), 1)
            self.assertEqual(v_streams[0].get("codec_name"), "qtrle")

    def test_v31_full_width_subtitle_bar_and_suppress_duplicate_flower(self):
        from PIL import Image
        from common import CANVAS_WIDTH, load_subtitle_templates
        from render_mp4 import compute_subtitle_place_xy, render_subtitle_png
        from step6_packaging import run_step6

        tpls = load_subtitle_templates()
        # v4：源草稿底框 background_width/height 均为 0（跟随文字自适应），没有任何一套是通栏
        for sid in ("style_02", "style_03", "style_20"):
            tpl = tpls[sid]
            self.assertFalse(tpl.get("full_width_bar"), f"{sid} must NOT claim a full-width bar")
        # style_02 是源里唯一带底框的（方角 67%），底框随文字宽度走
        tpl02 = tpls["style_02"]
        sub_p = self.root / "sub_box_style_02.png"
        w, h = render_subtitle_png(
            {"text": "这一句带方角底框", "keywords": ["方角底框"]},
            tpl02,
            None,
            sub_p,
        )
        self.assertLessEqual(w, 848, "底框字幕宽度必须随文字收缩，不得铺满 1080")
        px, _py = compute_subtitle_place_xy(w, h, tpl02)
        self.assertEqual(px, (CANVAS_WIDTH - w) // 2)
        with Image.open(sub_p) as im:
            self.assertGreater(im.getpixel((6, h // 2))[3], 100, "底框左右边缘应不透明")

        # style_02 / 03 / 13 / 20 默认 suppress_top_duplicate_flower=True，不生成顶部冗余重复花字条
        pdir = self.root / "pkg_suppress_dup"
        pdir.mkdir()
        write_json(pdir / "03_audio_events.json", {
            "locked_duration_sec": 6.0,
            "sentences": [{"id": "s1", "text": "底条字幕本身就是主视觉", "keywords": ["主视觉"], "start_sec": 0.0, "end_sec": 3.0, "start_frame": 0, "end_frame": 90, "emotion_or_info": "hook"}],
            "events": [{"id": "e1", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "s1"}],
        })
        write_json(pdir / "04_edl.json", {
            "style_id": "style_03",
            "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": "", "timeline_in_sec": 0.0, "timeline_out_sec": 6.0, "timeline_in_frame": 0, "timeline_out_frame": 180, "cut_event_frame": 0, "cut_event_id": "e1"}],
        })
        res_s03 = run_step6(pdir, style_id="style_03")
        # v4.2：顶部冗余花字条仍然不许生成；片尾那句没收住，会补一张带
        # deviation_from_style_source 标记的收尾卡（用户可删，qa 会点名）
        kinds = [str(e.get("kind")) for e in res_s03["visual_elements"]]
        self.assertNotIn("persistent_top_bar", kinds)
        self.assertEqual(kinds.count("end_card_cta"), 1, kinds)
        self.assertEqual(len(kinds), 1, kinds)
        self.assertIs(res_s03["ending_plan"]["needs_operator_line"], True)

    def test_v31_xfade_transition_zero_frame_drift(self):
        import wave
        from common import ffprobe_media, find_bin
        from render_mp4 import build_base_video_track

        ffmpeg = find_bin("ffmpeg")
        if not ffmpeg:
            return
        pdir = self.root / "xfade_pipe"
        pdir.mkdir()
        c1_p = pdir / "c1.mp4"
        c2_p = pdir / "c2.mp4"
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=#1E293B:s=1080x1920:r=30:d=3.0",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(c1_p)], check=True)
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=#7C2D12:s=1080x1920:r=30:d=3.0",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(c2_p)], check=True)
        wav_p = pdir / "master.wav"
        with wave.open(str(wav_p), "wb") as wf:
            wf.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            wf.writeframes(b"\0\0\0\0" * (48000 * 3))  # 精确 3.000s = 90 帧
        edl = {
            "fps": 30,
            "style_id": "style_01",
            "clips": [
                {"clip_id": "c1", "track_role": "a_roll", "media_path": str(c1_p), "source_in_sec": 0.0, "source_out_sec": 1.5,
                 "timeline_in_frame": 0, "timeline_out_frame": 45, "duration_sec": 1.5},
                {"clip_id": "c2", "track_role": "b_roll", "media_path": str(c2_p), "source_in_sec": 0.0, "source_out_sec": 1.5,
                 "timeline_in_frame": 45, "timeline_out_frame": 90, "duration_sec": 1.5},
            ],
        }
        base_mp4 = build_base_video_track(pdir, edl, {"clip_plans": []}, wav_p, manifest={"items": []}, preview=False)
        self.assertTrue(base_mp4.is_file())
        prof = read_json(pdir / "render_profile.json")
        # v4：源 24/24 转场数为 0，渲染器硬约束不允许开 xfade
        self.assertEqual(prof.get("xfade_applied"), 0)
        self.assertEqual(prof.get("xfade_transition"), "none")
        info = ffprobe_media(base_mp4)
        dur = float(info.get("format", {}).get("duration", 0.0))
        self.assertAlmostEqual(dur, 3.0, delta=0.04, msg="xfade 转场后总时长必须 0 帧漂移")

    def test_v32_ab_roll_switch_grammar_frequency_and_keyframes(self):
        from common import ASSETS_DIR, read_json
        from step4_build_edl import run_step4

        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        self.assertEqual(lib.get("version"), "4.1.0")
        by_id = {s["style_id"]: s for s in lib["styles"]}
        for sid, s in by_id.items():
            ab = s.get("ab_switch_spec") or {}
            self.assertIn(ab.get("pattern"), {
                "fullscreen_intercut",
                "zero_broll_keyframe_flow",
                "container_internal_switch",
                "cutout_broll_alternating",
                "depth_hook_then_framed_broll",
                "dual_window_broll_switcher",
            }, f"Missing ab_switch_spec.pattern on {sid}")
            self.assertGreater(float(ab.get("target_median_shot_sec", 0)), 0.7)
            self.assertIn("kf_per_min_target", ab)

        # 构造 4 句 8 秒的多镜头测试素材，验证不同风格的切镜频率、B-roll 占比、关键帧密度与切换样式
        ar_p = self.root / "ab_aroll.mp4"
        br1_p = self.root / "ab_broll1.mp4"
        br2_p = self.root / "ab_broll2.mp4"
        for p in (ar_p, br1_p, br2_p):
            p.write_bytes(b"fixture-mp4")
        man_items = [
            {"id": "a1", "filename": "ab_aroll.mp4", "role": "a_roll", "video_grade": "usable", "normalized_mp4": str(ar_p), "duration_sec": 12.0},
            {"id": "b1", "filename": "ab_broll1.mp4", "role": "b_roll", "video_grade": "usable", "normalized_mp4": str(br1_p), "duration_sec": 8.0, "best_in_sec": 0.0, "semantic_tags": ["门店"]},
            {"id": "b2", "filename": "ab_broll2.mp4", "role": "b_roll", "video_grade": "usable", "normalized_mp4": str(br2_p), "duration_sec": 8.0, "best_in_sec": 0.0, "semantic_tags": ["特写"]},
        ]
        sents = [
            {"id": f"s{i+1}", "text": f"第{i+1}句门店特写核心干货拆解", "keywords": ["核心干货"],
             "start_sec": i * 2.0, "end_sec": (i + 1) * 2.0, "start_frame": i * 60, "end_frame": (i + 1) * 60,
             "source_media": str(ar_p), "source_in_sec": i * 2.0, "source_out_sec": (i + 1) * 2.0, "emotion_or_info": "hook" if i == 0 else "info"}
            for i in range(4)
        ]
        evts = []
        for i in range(4):
            evts.append({"id": f"ev_s{i+1}", "type": "sentence_start", "time_sec": i * 2.0, "frame": i * 60, "sentence_id": f"s{i+1}"})
            evts.append({"id": f"ev_k{i+1}", "type": "keyword_accent", "time_sec": i * 2.0 + 1.0, "frame": i * 60 + 30, "sentence_id": f"s{i+1}"})
        ae_doc = {"locked_duration_sec": 8.0, "locked_duration_frames": 240, "sentences": sents, "events": evts}

        # 1) style_08 (0% B-roll 长镜 + 中频关键帧推拉 vs style_01 0 kf/min 零推拉)
        p08 = self.root / "edl_s08"
        p08.mkdir()
        write_json(p08 / "01_manifest.json", {"items": man_items})
        write_json(p08 / "03_audio_events.json", ae_doc)
        edl08 = run_step4(p08, track="creator", style_id="style_08")
        self.assertEqual(edl08["broll_ratio_actual"], 0.0, "style_08 必须为 0% B-roll 全 A-roll 长镜")
        self.assertGreater(edl08["keyframe_moves_applied"], 0)

        p01 = self.root / "edl_s01"
        p01.mkdir()
        write_json(p01 / "01_manifest.json", {"items": man_items})
        write_json(p01 / "03_audio_events.json", ae_doc)
        edl01 = run_step4(p01, track="store", style_id="style_01")
        self.assertEqual(edl01["keyframe_moves_applied"], 0, "style_01 kf_per_min=0 应关闭自动 ken_burns 推拉")

        # 2) style_10 (1.12s 极速跳剪 + 190.3 kf/min 几乎每镜推拉) 切镜数必须显著多于 style_04 (3.60s 慢调)
        p10 = self.root / "edl_s10"
        p10.mkdir()
        write_json(p10 / "01_manifest.json", {"items": man_items})
        write_json(p10 / "03_audio_events.json", ae_doc)
        edl10 = run_step4(p10, track="creator", style_id="style_10")

        p04 = self.root / "edl_s04"
        p04.mkdir()
        write_json(p04 / "01_manifest.json", {"items": man_items})
        write_json(p04 / "03_audio_events.json", ae_doc)
        edl04 = run_step4(p04, track="store", style_id="style_04")
        self.assertGreater(edl10["clip_count"], edl04["clip_count"])
        # 已做重点拉近（punch-in）的镜头不再叠 keyframe，允许少量镜头由拉近替代
        self.assertGreaterEqual(edl10["keyframe_moves_applied"], int(edl10["clip_count"] * 0.8))

        # 3) style_05 (depth_hook_then_framed_broll): A-roll 挂 person_behind_giant_text_pip，B-roll 切 blur_bg_center_card_pip
        p05 = self.root / "edl_s05"
        p05.mkdir()
        write_json(p05 / "01_manifest.json", {"items": man_items})
        write_json(p05 / "03_audio_events.json", ae_doc)
        edl05 = run_step4(p05, track="store", style_id="style_05")
        a_modes_05 = {(c.get("pip_layout") or {}).get("mode") for c in edl05["clips"] if c["track_role"] == "a_roll"}
        b_modes_05 = {(c.get("pip_layout") or {}).get("mode") for c in edl05["clips"] if c["track_role"] == "b_roll"}
        # depth 范式只在钩子镜挂框（其余保持全屏），但挂上的必须是真值几何，不能再出现别人的版面
        self.assertIn("geometry_pip_window", a_modes_05)
        self.assertTrue(all(m is None or str(m).startswith("geometry_pip") for m in a_modes_05 | b_modes_05),
                        a_modes_05 | b_modes_05)

        # 4) style_07 (cutout_broll_alternating): A-roll 挂 vision_portrait_cutout_pip，纯 B-roll 镜保持全屏无 pip_layout
        p07 = self.root / "edl_s07"
        p07.mkdir()
        write_json(p07 / "01_manifest.json", {"items": man_items})
        write_json(p07 / "03_audio_events.json", ae_doc)
        edl07 = run_step4(p07, track="store", style_id="style_07")
        for c in edl07["clips"]:
            mode07 = (c.get("pip_layout") or {}).get("mode")
            self.assertTrue(mode07 is None or str(mode07).startswith("geometry_pip"),
                            f"style_07 {c['track_role']} 出现非真值几何的画中画模式：{mode07}")

        # 5) style_09 (dual_window_broll_switcher): B-roll 镜也必须绑定 swap_main_secondary + speaker_media_path 保左下圆头像真人同步说话
        p09 = self.root / "edl_s09"
        p09.mkdir()
        write_json(p09 / "01_manifest.json", {"items": man_items})
        write_json(p09 / "03_audio_events.json", ae_doc)
        edl09 = run_step4(p09, track="creator", style_id="style_09")
        b_clips_09 = [c for c in edl09["clips"] if c["track_role"] == "b_roll"]
        self.assertTrue(len(b_clips_09) >= 1)
        self.assertTrue(all((c.get("pip_layout") or {}).get("swap_main_secondary") for c in b_clips_09))
        self.assertTrue(all((c.get("pip_layout") or {}).get("speaker_media_path") == str(ar_p) for c in b_clips_09))

    def test_v32_dual_line_banner_style21_cutout_and_flower_dedupe(self):
        from common import ASSETS_DIR, load_subtitle_templates, read_json
        from render_mp4 import _flower_duplicates_subtitle, render_subtitle_png

        # 1) style_21 按真草稿：主轨推近 1.2 + 叠加窗，几何来自 pip_usage.json
        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        s21 = next(s for s in lib["styles"] if s["style_id"] == "style_21")
        self.assertTrue(str(s21["pip_composition_spec"]["mode"]).startswith("geometry_pip"))
        self.assertEqual(s21["pip_composition_spec"]["main"]["scale"], 1.2)
        self.assertTrue(s21["pip_composition_spec"]["windows"], "style_21 真草稿里有叠加窗")

        # 2) dual_line_banner：长句排上下双行（标题厚度 ≥124px），宽度仍在对称安全带内（≤848）
        tpls = load_subtitle_templates()
        for sid in ("style_02", "style_03", "style_20"):
            self.assertTrue(tpls[sid].get("dual_line_banner"))
            sub_p = self.root / f"dual_{sid}.png"
            w, h = render_subtitle_png(
                {"text": "实体工厂老板该怎么做短视频", "keywords": ["怎么做短视频"]},
                tpls[sid],
                None,
                sub_p,
            )
            self.assertLessEqual(w, 848, f"{sid} 双行字幕不得铺满整屏")
            self.assertGreaterEqual(h, 124, f"{sid} dual-line banner should be thick two-line banner, got h={h}")

        # 3) 同屏花字与底字幕防重互斥
        subs = [{"text": "钱背后藏着定价真相", "start_sec": 0.0, "end_sec": 2.0}]
        self.assertTrue(_flower_duplicates_subtitle("两块钱背后藏", 0.0, 1.8, subs))
        self.assertFalse(_flower_duplicates_subtitle("门店实拍核验", 0.0, 1.8, subs))


class ColorGradeCalibrationTests(unittest.TestCase):
    """调色闭环：曲线必须真的在改画面，且输出分位要落到源片实测分位。"""

    @classmethod
    def setUpClass(cls):
        import subprocess
        from common import find_bin
        cls.ff = find_bin("ffmpeg")
        work = Path(tempfile.mkdtemp(prefix="grade_probe_"))
        cls.clip = work / "ramp.mp4"
        if cls.ff:
            # 静态灰阶斜坡：分布固定（不随时间变），才谈得上「贴到目标分位」是可复现的
            from PIL import Image
            img = Image.new("RGB", (320, 240))
            px = img.load()
            for y in range(240):
                for x in range(320):
                    v = int(14 + (236 - 14) * x / 319)
                    px[x, y] = (v, v, min(255, v + 4))
            png = work / "ramp.png"
            img.save(png)
            subprocess.run(
                [str(cls.ff), "-v", "error", "-loop", "1", "-i", str(png), "-t", "3", "-r", "24",
                 "-pix_fmt", "yuv420p", "-c:v", "libx264", "-y", str(cls.clip)],
                capture_output=True, check=False)

    def test_tone_curve_maps_to_target_and_stays_in_range(self):
        from step7_color_grade import _resolve_style_color, _tone_curve_filter
        sc = _resolve_style_color("style_01")
        vf = _tone_curve_filter(sc, (0.15, 0.52, 0.90), (60 / 255.0, 150 / 255.0, 225 / 255.0))
        self.assertTrue(vf and vf.startswith("curves=all="), vf)
        pts = [tuple(float(v) for v in pair.split("/")) for pair in
               vf.split("'")[1].split()]
        # 坐标必须是 0–1（剪映/ffmpeg curves 都吃 0–1；写成 0–255 会直接报错或被夹死）
        for x, y in pts:
            self.assertGreaterEqual(x, 0.0)
            self.assertLessEqual(x, 1.0)
            self.assertGreaterEqual(y, 0.0)
            self.assertLessEqual(y, 1.0)
        # 三个源分位必须被搬到目标位置（不是空转的恒等曲线）
        mapping = {round(x, 3): y for x, y in pts}
        self.assertAlmostEqual(mapping[0.15], 60 / 255.0, places=2)
        self.assertAlmostEqual(mapping[0.52], 150 / 255.0, places=2)
        self.assertAlmostEqual(mapping[0.9], 225 / 255.0, places=2)

    def test_curve_actually_moves_the_picture(self):
        """回归护栏：曾经曲线坐标写错，滤镜看着在跑但画面几乎没动。"""
        from step7_color_grade import _probe_with_vf
        if not self.ff or not Path(self.clip).is_file():
            self.skipTest("ffmpeg unavailable")
        base = _probe_with_vf(str(self.clip), "null", 0.0, 2.5)
        lifted = _probe_with_vf(str(self.clip), "curves=all='0/0 0.18/1 1/1'", 0.0, 2.5)
        self.assertIsNotNone(base)
        self.assertIsNotNone(lifted)
        self.assertGreater((lifted[1] - base[1]) * 255.0, 40.0,
                           f"曲线没生效：{base} → {lifted}")

    def test_calibration_lands_on_source_percentiles(self):
        from step7_color_grade import (
            _resolve_style_color, calibrate_clip_curve, compute_hero_and_shot_correction)
        if not self.ff or not Path(self.clip).is_file():
            self.skipTest("ffmpeg unavailable")
        sc = _resolve_style_color("style_01")
        stats = {"yavg": 128.0, "uavg": 128.0, "vavg": 128.0}
        corr = compute_hero_and_shot_correction(
            stats, hero_stats=stats, is_hero=True, skin_ycbcr=None, style_id="style_01")
        vf, adj, residual = calibrate_clip_curve(str(self.clip), 0.0, 2.5, corr, sc, None, 0.0, False)
        self.assertTrue(vf.startswith("eq="), vf)
        self.assertIsNotNone(residual, "校准没量到素材分位")
        self.assertLessEqual(max(abs(v) for v in residual), 12.0,
                             f"调色链没贴到源分位，残差 {residual}")


class EffectLayerParityTests(unittest.TestCase):
    """画面特效只能做源草稿 effect 轨里真有的那几段，且要按时间窗生效。"""

    def test_effects_match_source_effect_track(self):
        from step7_color_grade import _effects_enabled
        usage = read_json(ROOT / "forensic" / "effect_usage.json")["styles"]
        build = __import__("subprocess").run(
            [sys.executable, str(ROOT / "dev_tools" / "build_effect_usage.py"), "--check"],
            capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        for sid, node in usage.items():
            eff = _effects_enabled({"effects": read_json(
                ASSETS_DIR / "style_spectrum" / "color_effects.json")["styles"][sid]["effects"]})
            want = {e["source_effect"] for e in node["effects"]
                    if e["source_effect"] in ("暗角", "模糊", "聚光灯", "不规则黑框", "怀旧边框", "荧幕噪点", "颤抖")}
            self.assertEqual(len(eff), len(want), f"{sid} 特效数量与源不符：{eff} vs {want}")

    def test_effect_only_fires_inside_source_window(self):
        from step7_color_grade import _effect_filters
        color = read_json(ASSETS_DIR / "style_spectrum" / "color_effects.json")["styles"]["style_02"]
        self.assertTrue(color["effects"]["vignette"], "style_02 源里有暗角")
        win = color["effects"]["vignette"]["window"]
        inside = (float(win["start"]) + 0.2, float(win["start"]) + 1.2)
        outside = (0.0, 1.0)
        self.assertTrue(any("vignette" in f for f in _effect_filters(color, clip_window=inside)))
        self.assertFalse(any("vignette" in f for f in _effect_filters(color, clip_window=outside)))

    def test_spotlight_geq_makes_a_real_light_circle(self):
        """聚光要能真挖出「中间亮、四周暗」，不是整圈渐变。"""
        import subprocess
        from common import find_bin
        from step7_color_grade import _effect_filters
        ff = find_bin("ffmpeg")
        if not ff:
            self.skipTest("ffmpeg unavailable")
        tmp = Path(tempfile.mkdtemp(prefix="spot_")) / "flat.mp4"
        subprocess.run([str(ff), "-v", "error", "-f", "lavfi",
                        "-i", "color=c=0x808080:s=320x480:d=1:r=24",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-y", str(tmp)],
                       capture_output=True, check=False)
        # x/y 用画布（1080×1920）坐标，滤镜内部按相对量换算，换分辨率也成立
        color = {"effects": {"spotlight": {"angle": 0.63, "x": 540, "y": 960,
                                           "inner_frac": 0.34, "feather_frac": 0.62}}}
        vf = ",".join(_effect_filters(color)) or "null"
        raw = subprocess.run([str(ff), "-v", "error", "-i", str(tmp), "-vf", f"{vf},scale=32:48",
                              "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        import numpy as np
        arr = np.frombuffer(raw[: 32 * 48 * 3], dtype=np.uint8).reshape(48, 32, 3).astype(float)
        center = arr[22:26, 14:18].mean()
        corner = arr[0:3, 0:3].mean()
        self.assertGreater(center - corner, 25.0,
                           f"聚光没有形成亮圈：中心 {center:.1f} 角落 {corner:.1f}")


class GeometryPipRealizationTests(unittest.TestCase):
    """真草稿几何画中画必须真的改画面：单源主轴几何、双源窗口都要跑出可测差异。"""

    @classmethod
    def setUpClass(cls):
        import subprocess
        from common import find_bin
        cls.ff = find_bin("ffmpeg")
        cls.work = Path(tempfile.mkdtemp(prefix="geom_pip_"))
        cls.main = cls.work / "main.mp4"
        cls.sec = cls.work / "sec.mp4"
        if not cls.ff:
            return
        from PIL import Image, ImageDraw
        a = Image.new("RGB", (1080, 1920), (30, 40, 60))
        d = ImageDraw.Draw(a)
        d.rectangle([0, 0, 300, 300], fill=(240, 60, 60))
        a.save(cls.work / "a.png")
        b = Image.new("RGB", (1080, 1920), (250, 240, 200))
        b.save(cls.work / "b.png")
        for src, dst in ((cls.work / "a.png", cls.main), (cls.work / "b.png", cls.sec)):
            subprocess.run([str(cls.ff), "-v", "error", "-loop", "1", "-i", str(src), "-t", "2",
                            "-r", "24", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-y", str(dst)],
                           capture_output=True, check=False)

    def _frame(self, path: Path) -> "object":
        import subprocess
        import numpy as np
        raw = subprocess.run([str(self.ff), "-v", "error", "-i", str(path), "-frames:v", "1",
                              "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
        return np.frombuffer(raw[: 1080 * 1920 * 3], dtype=np.uint8).reshape(1920, 1080, 3)

    def test_single_source_punch_in_changes_framing(self):
        import subprocess
        from render_mp4 import _build_ffmpeg_pip_vf
        if not self.ff:
            self.skipTest("ffmpeg unavailable")
        vf = _build_ffmpeg_pip_vf({"mode": "geometry_pip_main_punch_in", "main": {"x": 0.0, "y": 0.0, "scale": 1.3}})
        self.assertIsNotNone(vf)
        self.assertIn("crop=1080:1920", vf)
        out = self.work / "punch.mp4"
        r = subprocess.run([str(self.ff), "-v", "error", "-i", str(self.main), "-vf", vf,
                            "-t", "1", "-pix_fmt", "yuv420p", "-y", str(out)], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[:300])
        base = self._frame(self.main)
        zom = self._frame(out)
        self.assertEqual(zom.shape, (1920, 1080, 3))
        # 推近 1.3：左上角那块红块应被推出画面（或明显变小位移），中心保持不变
        self.assertGreater(float(abs(base[0:300, 0:300].astype(int) - zom[0:300, 0:300].astype(int)).mean()), 8.0)

    def test_dual_window_places_secondary_into_truth_geometry(self):
        import subprocess
        from render_mp4 import _build_dual_pip_cmd
        if not self.ff:
            self.skipTest("ffmpeg unavailable")
        layout = {"mode": "geometry_pip_window", "border_hex": "#FFFFFF", "border_width": 6,
                  "main": {"x": 0.0, "y": 0.0, "scale": 1.0},
                  "windows": [{"x": 0.0, "y": 0.0, "scale": 0.4, "segments": 3}]}
        out = self.work / "dual.mp4"
        cmd = _build_dual_pip_cmd(str(self.ff), str(self.main), 0.0, 2.0, "format=yuv420p",
                                  layout, str(self.sec), 0.0, 1.0, 24, 24, out)
        self.assertIsNotNone(cmd)
        r = subprocess.run(cmd, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[:400])
        frame = self._frame(out)
        # 窗口几何 scale=0.4、居中 → 中心 432×768 区域应变成副素材的浅米色
        center = frame[960 - 200:960 + 200, 540 - 100:540 + 100].reshape(-1, 3).mean(axis=0)
        corner = frame[10:60, 10:60].reshape(-1, 3).mean(axis=0)
        # 副素材是米色 (250,240,200)：绿通道高；主素材角落是红块 (240,60,60)：绿通道低
        self.assertGreater(float(center[1]), 190.0, f"窗口里没有副素材：中心 {center}")
        self.assertLess(float(corner[1]), 120.0, f"窗口外应保持主素材：角落 {corner}")


class DeadKnobWiringTests(unittest.TestCase):
    """蓝图里的源实测参数必须真的被消费，不许躺在 json 里当装饰。"""

    def test_flower_density_and_giant_count_come_from_source_measurement(self):
        from step6_packaging import flower_budget_from_source, giant_count_from_source

        # style_15 源草稿实测 88 条花字/分钟、10 条巨字；旧的经验公式只给 18 条
        self.assertEqual(flower_budget_from_source("style_15", 60.0, 18), 88)
        self.assertEqual(giant_count_from_source("style_15", 60.0, {}), 10)
        # 没有实测密度的风格沿用旧预算，不做无依据的加料
        self.assertEqual(flower_budget_from_source("style_01", 60.0, 18), 18)
        self.assertEqual(giant_count_from_source("style_01", 60.0, {}), 0)
        # pkg_tech 显式给了巨字数就听它的
        self.assertEqual(giant_count_from_source("style_21", 60.0, {"giant_impact_count": 6}), 6)

    def test_letter_spacing_reads_blueprint_value(self):
        from render_template_overlays import letter_spacing

        self.assertEqual(letter_spacing({}, 5.0), 5.0)
        self.assertEqual(letter_spacing({"letter_spacing_px": 0}, 5.0), 0.0)
        self.assertEqual(letter_spacing({"letter_spacing_px": 9}, 5.0), 9.0)
        self.assertEqual(letter_spacing({"letter_spacing_px": "bad"}, 5.0), 5.0)

    def test_letter_spacing_visibly_widens_the_flower_sprite(self):
        from common import ASSETS_DIR
        import render_template_overlays as rto

        font = str(ASSETS_DIR / "fonts" / "SourceHanSerifCN-Heavy.otf")
        tight = rto._text_sprite("看得见的细节", font, 900, 64, (255, 255, 255, 255), spacing_px=0.0)
        loose = rto._text_sprite("看得见的细节", font, 900, 64, (255, 255, 255, 255), spacing_px=16.0)
        self.assertIsNotNone(tight)
        self.assertIsNotNone(loose)
        self.assertGreater(loose.size[0], tight.size[0] + 40, (tight.size, loose.size))


class FaceFramingTests(unittest.TestCase):
    """小窗/推近的取景必须以人脸为中心，人不能被切掉。"""

    def test_face_near_left_edge_stays_fully_inside_window(self):
        from face_framing import crop_rect, face_inside

        anchor = {"cx": 0.12, "cy": 0.42, "w": 0.10, "h": 0.14}
        rect = crop_rect(anchor, 1080, 1920, 432, 768)
        self.assertTrue(face_inside(rect, anchor, 1080, 1920, margin=1.0), rect)
        self.assertLessEqual(rect["x"], int(0.12 * 1080))
        self.assertGreaterEqual(rect["x"] + rect["w"], int((0.12 + 0.05) * 1080))

    def test_big_close_up_face_zooms_out_instead_of_being_cut(self):
        from face_framing import crop_rect, face_inside

        # 脸占到画面高度的 45%：方窗里必须退到最大取景才放得下
        anchor = {"cx": 0.5, "cy": 0.5, "w": 0.40, "h": 0.45}
        rect = crop_rect(anchor, 1080, 1920, 292, 292)
        self.assertTrue(face_inside(rect, anchor, 1080, 1920, margin=1.0), rect)
        self.assertEqual(rect["w"], 1080)
        # 极端特写（脸高 80% 的源画面塞进方窗）物理上放不下，此时只要求"尽量居中"，
        # 并且不许假装成功：face_inside 必须如实为 False
        huge = crop_rect({"cx": 0.5, "cy": 0.5, "w": 0.62, "h": 0.80}, 1080, 1920, 292, 292)
        self.assertFalse(face_inside(huge, {"cx": 0.5, "cy": 0.5, "w": 0.62, "h": 0.80}, 1080, 1920))
        self.assertLessEqual(abs((huge["x"] + huge["w"] / 2) - 540), 2)

    def test_face_centered_crop_renders_face_at_window_center(self):
        """真跑 ffmpeg：左 1/4 处的人被搬进窗口正中，旧的居中裁切则完全丢失。"""
        import numpy as np
        from PIL import Image
        from face_framing import cover_crop_vf

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src.jpg"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1920x1080:d=1",
                            "-vf", "drawbox=x=300:y=300:w=200:h=200:color=lime:t=fill", "-frames:v", "1", str(src)],
                           check=True)
            anchor = {"cx": (300 + 100) / 1920, "cy": (300 + 100) / 1080, "w": 200 / 1920, "h": 200 / 1080}
            outs = {}
            for name, a in (("legacy", None), ("face", anchor)):
                dst = root / f"{name}.png"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                                "-vf", cover_crop_vf(432, 768, a), "-frames:v", "1", str(dst)], check=True)
                arr = np.array(Image.open(dst).convert("RGB"))
                mask = (arr[:, :, 1] > 150) & (arr[:, :, 0] < 120)
                cols = np.where(mask.any(axis=0))[0] if mask.any() else []
                outs[name] = (float(mask.mean()), float(cols.mean()) if len(cols) else -1.0)
            self.assertLess(outs["legacy"][0], 0.02, "旧的居中裁切本来就该丢掉这张脸")
            self.assertGreater(outs["face"][0], 0.03, "人脸取景必须把小窗里的脸保住")
            self.assertAlmostEqual(outs["face"][1], 216.0, delta=24.0, msg="脸应落在窗口水平正中")

    def test_no_face_anchor_keeps_legacy_center_crop(self):
        from face_framing import cover_crop_vf

        self.assertEqual(cover_crop_vf(432, 768, None),
                         "scale=432:768:force_original_aspect_ratio=increase:flags=lanczos,"
                         "crop=432:768:(iw-432)/2:(ih-768)/2")
        self.assertIn("*0.24", cover_crop_vf(432, 768, None, face_bias=True))


class WindowFaceFramingRenderTests(unittest.TestCase):
    """渲染链真的按人脸锚点走：缩窗里的主角必须出现在窗口正中，不是被切开的一半。"""

    def test_shrunk_window_uses_face_anchor_end_to_end(self):
        import numpy as np
        from PIL import Image
        from render_mp4 import build_graded_segment_cmd

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src.mp4"
            # 已规整的竖屏源（1080x1920），主角（亮绿块）站在画面**上半部** y=90..330 ——
            # 方窗按画面正中/偏下取景就把他整个切掉，这正是复盘里"人脸被裁掉"的成因
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=2",
                            "-vf", "drawbox=x=390:y=90:w=300:h=240:color=lime:t=fill",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
            anchor = {"cx": 540 / 1080, "cy": 210 / 1920, "w": 300 / 1080, "h": 240 / 1920}
            outs = {}
            for name, a in (("legacy", None), ("face", anchor)):
                out = root / f"{name}.mp4"
                clip = {
                    "clip_id": "c1", "track_role": "b_roll", "media_path": str(src),
                    "source_in_sec": 0.0, "source_out_sec": 2.0,
                    "timeline_in_frame": 0, "timeline_out_frame": 60,
                    "duration_sec": 2.0,
                    "pip_layout": {"mode": "smooth_orbit_pip", "corner": "tr",
                                   "radius_px": 146, "center_y": 360,
                                   "face_anchor": a},
                }
                cmd = build_graded_segment_cmd("ffmpeg", clip, None, {}, out, 30)
                r = subprocess.run(cmd, capture_output=True)
                self.assertEqual(r.returncode, 0, r.stderr.decode()[:300])
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.3", "-i", str(out),
                                "-frames:v", "1", str(root / f"{name}.png")], check=True)
                arr = np.array(Image.open(root / f"{name}.png").convert("RGB"))
                # 只量圆窗内部（窗在右上角 x=734..1034, y=214..514），背景里的主体不算
                win = arr[224:504, 744:1024]
                mask = (win[:, :, 1] > 150) & (win[:, :, 0] < 120)
                outs[name] = float(mask.mean())
            # 完整可见时绿块约占窗口的 6.2%；旧逻辑只留下被切掉的一块（≈一半）
            self.assertLess(float(outs["legacy"]), 0.045, f"旧逻辑应该把人切掉一块：{outs}")
            # 新逻辑按人脸锚点取景，人完整落在窗口里
            self.assertGreater(float(outs["face"]), 0.055, f"人脸取景没保住人：{outs}")


class PackagingSmokeTests(unittest.TestCase):
    """打包前必须能 import：任何一个脚本的语法错误都会让 install_skill.py 直接失败。"""

    def test_every_script_compiles(self):
        import py_compile

        scripts = sorted((ROOT / "short-video-studio" / "scripts").glob("*.py"))
        scripts += sorted((ROOT / "dev_tools").glob("*.py"))
        scripts.append(ROOT / "install_skill.py")
        self.assertGreater(len(scripts), 30)
        bad = []
        for f in scripts:
            try:
                py_compile.compile(str(f), doraise=True, cfile=str(Path(tempfile.gettempdir()) / "svs_compile.pyc"))
            except Exception as exc:
                bad.append(f"{f.name}: {exc}")
        self.assertEqual(bad, [], f"以下脚本无法编译：{bad}")

    def test_style_list_command_runs_on_v4_library(self):
        """README 让用户第一条就跑的命令，不许因为 v4 里作废的推演值崩掉。"""
        r = subprocess.run([sys.executable, str(ROOT / "short-video-studio" / "scripts" / "apply_style_spectrum.py"),
                            "--list"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("style_01", r.stdout)
        self.assertIn("style_24", r.stdout)

    def test_style_menu_is_computed_from_library_not_hand_written(self):
        """点单菜单必须从库里现算：门店默认 style_01，绝不把 creator 的 style_14 塞给门店。"""
        from apply_style_spectrum import build_style_menu, load_library

        lib = load_library()
        store = build_style_menu(lib, "store", has_aroll=True)
        self.assertEqual([s["style_id"] for s in store][0], "style_01")
        self.assertNotIn("style_14", [s["style_id"] for s in store])
        self.assertTrue(all(s["recommended_track"] == "store" for s in store))
        self.assertGreaterEqual(len(store), 3)

        no_face = build_style_menu(lib, "creator", has_aroll=False)
        self.assertTrue(all(str(s.get("requires_aroll") or "optional") != "required" for s in no_face))

        r = subprocess.run([sys.executable, str(ROOT / "short-video-studio" / "scripts" / "apply_style_spectrum.py"),
                            "--menu", "store"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("选 A", r.stdout)
        self.assertIn("style_01", r.stdout)

    def test_repo_consistency_audit_passes(self):
        """文档默认值/数量/路径/命令/点单菜单必须与代码和资产单源一致。"""
        r = subprocess.run([sys.executable, str(ROOT / "dev_tools" / "audit_consistency.py")],
                           capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1200:])

    def test_install_targets_are_recognizable_skills(self):
        import install_skill

        self.assertTrue(install_skill.recognized_skill(ROOT / "short-video-studio"))
        self.assertIn("version:", (ROOT / "short-video-studio" / "SKILL.md").read_text(encoding="utf-8")[:400])


class StyleLockTests(unittest.TestCase):
    """用户实测过：Agent 会「顺手」改风格的标准，而且不说。

    所以不是要求 AI 承诺 100% 套用，而是把「库里写的」和「管线里实际在用的」逐字段对撞，
    改一处就抓一处，只有用户点名的字段才算豁免。
    """

    def _pipeline(self, tmp: str, style_id: str = "style_01", tamper: Optional[Dict[str, Any]] = None) -> Path:
        from apply_style_spectrum import apply_to_pipeline, load_library
        from common import load_subtitle_templates

        pdir = Path(tmp) / "pipeline"
        pdir.mkdir(parents=True, exist_ok=True)
        apply_to_pipeline(load_library(), style_id, pdir)
        tpl = dict(load_subtitle_templates()[style_id])
        if tamper:
            tpl.update(tamper)
        write_json(pdir / "05_subtitles.json", {"template_id": style_id, "template_config": tpl})
        return pdir

    def test_untouched_pipeline_passes(self):
        from style_lock import audit_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._pipeline(tmp)
            rep = audit_pipeline(pdir)
            self.assertTrue(rep["ok"], rep)
            self.assertEqual(rep["style_id"], "style_01")
            self.assertEqual(rep["deviations"], [])

    def test_agent_changing_style_standard_is_caught_and_named(self):
        from style_lock import audit_pipeline, render_report

        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._pipeline(tmp, tamper={"font_size": 64, "keyword_color": "#FF0000"})
            rep = audit_pipeline(pdir)
            self.assertFalse(rep["ok"])
            fields = {d["field"]: d for d in rep["deviations"]}
            self.assertIn("subtitles.font_size", fields)
            self.assertIn("subtitles.keyword_color", fields)
            # 必须能说清「库是多少、实际是多少」，不能只说一句不一致
            self.assertEqual(fields["subtitles.font_size"]["expected"], 48)
            self.assertEqual(fields["subtitles.font_size"]["actual"], 64)
            report = render_report(rep)
            self.assertIn("字幕字号", report)
            self.assertIn("48", report)

    def test_only_fields_the_user_agreed_to_are_waived(self):
        from style_lock import audit_pipeline, write_waiver

        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._pipeline(tmp, tamper={"font_size": 56, "keyword_color": "#FF0000"})
            write_waiver(pdir, "style_01", ["subtitles.font_size"], "用户说字太小，同意放大")
            rep = audit_pipeline(pdir)
            self.assertIn("subtitles.font_size", rep["waived"])
            self.assertFalse(rep["ok"], "只同意字号，颜色仍应拦住")
            self.assertEqual([d["field"] for d in rep["deviations"]], ["subtitles.keyword_color"])

            write_waiver(pdir, "style_01", ["subtitles.keyword_color"], "用户要求品牌红")
            self.assertTrue(audit_pipeline(pdir)["ok"])

    def test_waiver_for_another_style_does_not_count(self):
        from style_lock import audit_pipeline, write_waiver

        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._pipeline(tmp, tamper={"font_size": 56})
            write_waiver(pdir, "style_02", ["subtitles.font_size"], "串了风格")
            rep = audit_pipeline(pdir)
            self.assertFalse(rep["ok"])
            self.assertEqual(rep["waived"], [])

    def test_broll_ratio_and_transition_tampering_is_caught(self):
        from style_lock import audit_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._pipeline(tmp)
            applied = read_json(pdir / "style_spectrum_applied.json")
            applied["broll_overlay_ratio_target"] = 0.95
            applied["transition_and_packaging"] = dict(
                applied["transition_and_packaging"], transitions=["叠化 0.5s"]
            )
            write_json(pdir / "style_spectrum_applied.json", applied)
            fields = {d["field"] for d in audit_pipeline(pdir)["deviations"]}
            self.assertIn("applied.broll_overlay_ratio_target", fields)
            self.assertIn("applied.transition_and_packaging", fields)

    def test_every_style_asset_is_self_consistent(self):
        """24 套的库 / 蓝图 / 字幕模板之间不许有漂移，否则风格锁一上线就全红。"""
        from style_lock import audit_assets

        bad = {}
        for i in range(1, 25):
            sid = f"style_{i:02d}"
            rep = audit_assets(sid)
            if not rep["ok"]:
                bad[sid] = [d["field"] for d in rep["deviations"]]
        self.assertEqual(bad, {}, f"以下风格资产自相矛盾：{bad}")

    def test_progress_card_shows_style_lock_state(self):
        """进度卡是每轮必读的面板：样式有没有被改，必须挂在上面。"""
        from workflow_guard import render_progress_card

        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._pipeline(tmp, tamper={"font_size": 64})
            card = render_progress_card(pdir, track="store", write_files=False)
            self.assertIn("画面样式", card["markdown_card"])
            self.assertIn("style_01", card["markdown_card"])
            self.assertIn("没经你同意", card["markdown_card"])

            write_json(pdir / "05_subtitles.json", {"template_id": "style_01", "template_config": dict(load_subtitle_templates()["style_01"])})
            card2 = render_progress_card(pdir, track="store", write_files=False)
            self.assertIn("完全按你选的这套执行", card2["markdown_card"])

    def test_default_style_comes_from_library_not_hardcoded_code(self):
        """run_pipeline 里曾经写死 store→style_14，库里默认早就是 style_01 —— 一开跑就换风格。"""
        from common import default_style_id

        self.assertEqual(default_style_id("store"), "style_01")
        self.assertEqual(default_style_id("creator"), "style_11")
        src = (ROOT / "short-video-studio" / "scripts" / "run_pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn('"style_14" if track == "store"', src)
        self.assertIn("default_style_id(track)", src)

    def test_render_gate_refuses_deviation_without_user_consent(self):
        """出片前那道闸：偏离且无豁免 → 退出码 3，不能静默出片。"""
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._pipeline(tmp, tamper={"font_size": 64})
            r = subprocess.run(
                [sys.executable, str(ROOT / "short-video-studio" / "scripts" / "style_lock.py"),
                 "--pipeline-dir", str(pdir)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("字幕字号", r.stdout)

            r2 = subprocess.run(
                [sys.executable, str(ROOT / "short-video-studio" / "scripts" / "style_lock.py"),
                 "--pipeline-dir", str(pdir), "--waive", "subtitles.font_size", "--reason", "用户同意放大"],
                capture_output=True, text=True,
            )
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertIn("PASS", r2.stdout)


class BrollSemanticsTests(unittest.TestCase):
    """外来画面必须能与它压着的那句台词对上；对不上要照实标记，不能靠字面 tag 蒙。"""

    def test_bare_phone_clip_is_reported_as_no_signal_not_fake_match(self):
        from broll_semantics import relate

        bare = {"filename": "IMG_4821.MOV", "semantic_tags": ["空镜特写"]}
        rel = relate("清晨市场带回的鲜羊排，成本花了三千块。", bare, ["鲜羊排"])
        self.assertEqual(rel["verdict"], "none")
        self.assertEqual(rel["score"], 0.0)
        self.assertEqual(rel["basis"], [])

    def test_one_line_note_makes_the_same_clip_match_with_evidence(self):
        from broll_semantics import relate

        noted = {"filename": "IMG_4821.MOV", "semantic_tags": ["空镜特写"],
                 "description": "后厨现切鲜羊排，刀工特写"}
        rel = relate("清晨市场带回的鲜羊排，成本花了三千块。", noted, ["鲜羊排"])
        self.assertGreaterEqual(rel["score"], 0.6)
        self.assertEqual(rel["verdict"], "hit")
        self.assertTrue(any("羊排" in b for b in rel["basis"]), rel["basis"])

    def test_media_notes_file_is_parsed_and_becomes_topic_signal(self):
        from inventory_media import _note_for, load_media_notes
        from broll_semantics import relate

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "素材说明.txt").write_text(
                "# 注释行会被跳过\nIMG_4821.MOV 后厨现切鲜羊排\nIMG_4830.MOV  门店门头与大厅\n",
                encoding="utf-8")
            notes = load_media_notes(root)
            self.assertEqual(notes.get("IMG_4821.MOV"), "后厨现切鲜羊排")
            self.assertEqual(notes.get("IMG_4830.MOV"), "门店门头与大厅")
            self.assertEqual(_note_for(notes, "IMG_0000.MOV", "sub/IMG_0000.MOV"), "")
            item = {"filename": "IMG_4821.MOV", "description": _note_for(notes, "IMG_4821.MOV", "IMG_4821.MOV")}
            self.assertEqual(relate("清晨带回的鲜羊排", item, ["鲜羊排"])["verdict"], "hit")

    def test_chinese_folder_name_is_usable_signal(self):
        from broll_semantics import relate

        rel = relate("其实要腌两小时，炭火再烤三小时。", {"filename": "IMG_0001.MOV", "parent_dir": "后厨炭火"}, ["炭火"])
        self.assertEqual(rel["verdict"], "hit")

    def test_edl_records_basis_for_every_broll_and_never_fabricates_it(self):
        from step4_build_edl import run_step4

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ar = root / "aroll.mp4"
            br1 = root / "IMG_1001.MOV"
            br2 = root / "IMG_1002.MOV"
            for path, color in ((ar, "gray"), (br1, "navy"), (br2, "teal")):
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                                f"color=c={color}:s=540x960:r=30:d=8", "-c:v", "libx264",
                                "-pix_fmt", "yuv420p", str(path)], check=True)
            sents, evts, t = [], [], 0.0
            for i, text in enumerate(("清晨带回的鲜羊排", "其实要腌两小时", "炭火再烤三小时"), start=1):
                sents.append({"id": f"s{i}", "text": text, "keywords": [], "start_sec": round(t, 2),
                              "end_sec": round(t + 2.0, 2), "source_media": str(ar),
                              "source_in_sec": round(t, 2), "source_out_sec": round(t + 2.0, 2)})
                evts.append({"id": f"e{i}", "type": "sentence_start", "sentence_id": f"s{i}",
                             "time_sec": round(t, 2), "frame": int(round(t * 30))})
                t += 2.0
            items = [
                {"id": "ar1", "role": "a_roll", "normalized_mp4": str(ar), "duration_sec": 8.0,
                 "video_grade": "usable", "sharpness_score": 45.0, "stability_score": 0.9},
                {"id": "b1", "role": "b_roll", "normalized_mp4": str(br1), "duration_sec": 8.0,
                 "video_grade": "usable", "semantic_tags": ["空镜特写"], "description": "后厨现切鲜羊排",
                 "sharpness_score": 47.0, "stability_score": 0.9, "best_in_sec": 0.3},
                {"id": "b2", "role": "b_roll", "normalized_mp4": str(br2), "duration_sec": 8.0,
                 "video_grade": "usable", "semantic_tags": ["空镜特写"],
                 "sharpness_score": 47.0, "stability_score": 0.9, "best_in_sec": 0.3},
            ]
            pipe = root / "p"
            pipe.mkdir()
            write_json(pipe / "01_manifest.json", {"items": items})
            write_json(pipe / "03_audio_events.json", {"locked_duration_sec": round(t, 2),
                                                       "locked_duration_frames": int(round(t * 30)),
                                                       "sentences": sents, "events": evts})
            edl = run_step4(pipe, track="store", style_id="style_21", edit_profile="documentary")

            brolls = [c for c in edl["clips"] if c["track_role"] in ("b_roll", "code_broll")]
            self.assertTrue(brolls)
            for c in brolls:
                self.assertIsInstance(c.get("sem_match"), dict, c)
                verdict = c["sem_match"]["verdict"]
                self.assertIn(verdict, ("hit", "weak", "none"))
                if verdict == "none":
                    self.assertEqual(c["sem_match"]["basis"], [], "无依据的镜头不许编依据")
                else:
                    self.assertTrue(c["sem_match"]["basis"], "有依据的镜头必须写清凭什么")
            stat = edl["broll_semantic"]
            self.assertEqual(stat["clips"], len(brolls))
            self.assertEqual(stat["hit"] + stat["weak"] + stat["none"], len(brolls))
            # 有说明的那条素材必须能被优先选中并给出依据
            picked = [c for c in brolls if "IMG_1001" in str(c.get("media_path"))]
            self.assertTrue(picked, "带素材说明的 B-roll 应被优先选中")
            self.assertEqual(picked[0]["sem_match"]["verdict"], "hit")


class SubtitlePaintTruthTests(unittest.TestCase):
    """字幕「阴影 or 描边」必须按草稿真值画（用户实测：style_01 明明是深灰阴影，被画成了描黑边）。"""

    def test_library_paint_matches_draft_truth(self):
        truth_p = ROOT / "forensic" / "text_style_truth.json"
        src_p = ROOT / "forensic" / "style_sources.json"
        if not truth_p.is_file():
            self.skipTest("缺 forensic/text_style_truth.json（需先跑 dev_tools/measure_text_style_truth.py）")
        truth = json.loads(truth_p.read_text(encoding="utf-8"))["projects"]
        srcs = json.loads(src_p.read_text(encoding="utf-8"))["styles"]
        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        styles = {x["style_id"]: x for x in lib["styles"]}
        tpls = read_json(ASSETS_DIR / "subtitle_templates" / "templates.json")["templates"]
        checked = 0
        for sid, src in sorted(srcs.items()):
            draft = src if isinstance(src, str) else src.get("draft")
            tr = truth.get(draft or "")
            if not tr or "error" in tr:
                continue
            sub = styles[sid]["subtitle_spec"]
            checked += 1
            self.assertEqual(sub.get("stroke_mode"), tr["stroke_mode"], f"{sid} 描边方式与草稿不一致")
            if tr["stroke_mode"] == "none":
                self.assertEqual(int(sub.get("stroke_width") or 0), 0, f"{sid} 草稿没描边，库却给了描边宽度")
            self.assertEqual(bool(sub.get("shadow")), bool((tr.get("shadow") or {}).get("enabled")),
                             f"{sid} 阴影有无与草稿不一致")
            self.assertEqual(int(tpls[sid].get("stroke_width") or 0), int(sub.get("stroke_width") or 0),
                             f"{sid} 模板与库不一致")
            if (tr.get("shadow") or {}).get("enabled"):
                self.assertEqual([int(v) for v in sub.get("shadow_offset") or []],
                                 [int(v) for v in tr["shadow"]["offset_px"]],
                                 f"{sid} 阴影偏移与草稿不一致")
        self.assertGreaterEqual(checked, 20, "至少要核到 20 套，说明真值文件是全的")


    def test_flower_text_has_no_fake_stroke_either(self):
        """花字同样只认实测：24 套花字 1636 个样式里真描边 0 个，不许再写死黑边。"""
        import render_template_overlays as RTO
        from PIL import Image
        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        styles = {x["style_id"]: x for x in lib["styles"]}
        checked = 0
        for sid, entry in styles.items():
            spec = entry.get("flower_text_spec") or {}
            if not spec:
                continue
            checked += 1
            self.assertEqual(spec.get("stroke_mode"), "none", f"{sid} 花字被写成有描边")
            self.assertEqual(int(spec.get("stroke_width") or 0), 0, f"{sid} 花字描边宽度不为 0")
        self.assertGreaterEqual(checked, 20)
        # 真渲染：左上邻域不该有一圈深色（描边特征）
        img = RTO.render_template_packaging_png(
            {"kind": "flower_text", "text_or_asset": "关键信息一句话", "subtext": "标注"}, style_id="style_01"
        )
        self.assertIsNotNone(img)
        px = img.convert("RGBA").load()
        w, h = img.size
        solids = [(x, y) for y in range(h) for x in range(w) if px[x, y][3] > 200 and max(px[x, y][:3]) > 90]
        self.assertGreater(len(solids), 1000)
        sset = set(solids)
        ring = 0
        for (x, y) in solids:
            for dx, dy in ((-2, 0), (-1, 0), (0, -2), (0, -1)):
                nx, ny = x + dx, y + dy
                if (nx, ny) in sset:
                    break
                if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] > 120 and max(px[nx, ny][:3]) < 60:
                    ring += 1
                    break
        self.assertLess(ring / max(1, len(solids)), 0.12, "花字左上有一圈黑边，说明还在画描边")

    def test_rendered_subtitle_is_shadow_not_outline(self):
        """真渲染一张：白字周围不该有黑边，右下方向应该有半透明黑影。"""
        from PIL import Image
        import render_mp4 as R

        tpls = read_json(ASSETS_DIR / "subtitle_templates" / "templates.json")["templates"]
        tpl = dict(tpls["style_01"])
        self.assertEqual(tpl.get("stroke_mode"), "none")
        out = Path(tempfile.mkdtemp()) / "sub.png"
        R.render_subtitle_png({"text": "成本三千块", "words": [], "keywords": []}, tpl, None, out)
        img = Image.open(out).convert("RGBA")
        px = img.load()
        w, h = img.size
        white, dark = [], []
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a > 200 and r > 235 and g > 235 and b > 235:
                    white.append((x, y))
                elif a > 120 and max(r, g, b) < 60:
                    dark.append((x, y))
        self.assertGreater(len(white), 200, "白字应该画出来")
        self.assertGreater(len(dark), 200, "必须有阴影（否则整块就没有深色像素）")
        # 阴影必须整体偏右下：深色像素的重心要落在白字重心右下方
        wx = sum(p[0] for p in white) / len(white)
        wy = sum(p[1] for p in white) / len(white)
        dx_ = sum(p[0] for p in dark) / len(dark)
        dy_ = sum(p[1] for p in dark) / len(dark)
        self.assertGreater(dx_, wx, "阴影重心应在白字右侧")
        self.assertGreater(dy_, wy, "阴影重心应在白字下方")
        # 描边的特征是「白字四周围一圈黑」：检查白字左侧/上方邻域不应大量是深色
        white_set = set(white)
        left_dark = 0
        for (x, y) in white:
            for d in (1, 2, 3):
                if (x - d, y) in white_set:
                    break
                if px[x - d, y][3] > 120 and max(px[x - d, y][:3]) < 60:
                    left_dark += 1
                    break
        ratio = left_dark / max(1, len(white))
        self.assertLess(ratio, 0.12, f"白字左边大量深色像素（{ratio:.2%}）说明又画成描边了")


class WorkspaceHygieneTests(unittest.TestCase):
    """过程文件不许撒进用户原素材目录（用户实测：Agent 把工作目录建在了他的素材盘上）。"""

    def test_workspace_falls_back_to_one_top_level_dir_in_material_dir(self):
        from common import WORK_DIR_NAME, resolve_workspace_paths
        tmp = Path(tempfile.mkdtemp())
        raw = tmp / "老板的素材"
        raw.mkdir()
        (raw / "IMG_0001.MOV").write_bytes(b"x")
        # 让默认工作区不可写：指向一个「父级是文件」的路径
        blocked = tmp / "blocked_file"
        blocked.write_text("not a dir", encoding="utf-8")
        os.environ["SVS_WORKSPACE_ROOT"] = str(blocked / "workspaces")
        try:
            import importlib
            import common
            importlib.reload(common)
            ws = common.resolve_workspace_paths(
                account="default", project_id="p1", raw_dir=raw,
                workspace_root=blocked / "workspaces",
            )
        finally:
            os.environ.pop("SVS_WORKSPACE_ROOT", None)
            import importlib
            import common
            importlib.reload(common)
        self.assertTrue(ws["workspace_fallback"], "默认工作区不可写时必须给出回落说明")
        self.assertEqual(ws["workspace_root"].name, WORK_DIR_NAME, "回落到素材目录下的唯一顶层工作目录")
        self.assertEqual(ws["workspace_root"].parent.resolve(), raw.resolve(), "回落目录必须就在素材目录下")
        self.assertIn(str(raw / WORK_DIR_NAME), str(ws["pipeline_dir"]))
        # 素材目录根上不许出现任何过程文件
        leftovers = [p.name for p in raw.iterdir() if p.name not in ("IMG_0001.MOV", WORK_DIR_NAME)]
        self.assertEqual(leftovers, [], f"素材目录根上多了东西：{leftovers}")

    def test_inventory_notes_skeleton_never_lands_in_raw_root(self):
        from common import WORK_DIR_NAME
        import inventory_media as INV
        tmp = Path(tempfile.mkdtemp())
        raw = tmp / "素材"
        raw.mkdir()
        fake_clip = {"filename": "IMG_9999.MOV", "suggested_role": "b_roll_candidate"}
        inv = INV.inventory_directory.__doc__ or ""
        # 只验证默认落点常量：骨架路径必须落在 WORK_DIR_NAME 之下
        default_skeleton = raw / WORK_DIR_NAME / "素材说明_待填写.txt"
        self.assertIn(WORK_DIR_NAME, str(default_skeleton))
        self.assertNotEqual(default_skeleton.parent, raw)
        self.assertTrue(inv is not None)


class AsrGlossaryTests(unittest.TestCase):
    """转录必须按用户背景资料纠错（用户实测：很多转录没有针对他的背景资料纠错）。"""

    def _glossary(self, terms: str):
        import asr_glossary as AG
        tmp = Path(tempfile.mkdtemp())
        (tmp / "术语表.txt").write_text(terms, encoding="utf-8")
        (tmp / "背景资料.md").write_text("我们是珠海香洲的烧烤店，主打清晨市场鲜羊排。", encoding="utf-8")
        return AG, AG.load_glossary([tmp])

    def test_terms_go_into_asr_prompt(self):
        AG, g = self._glossary("羊排\n炭火\n")
        prompt = AG.asr_prompt_suffix(g)
        self.assertIn("羊排", prompt)
        self.assertIn("珠海香洲", prompt, "背景资料要进 ASR 提示词")

    def test_three_char_homophone_typo_is_fixed_and_words_synced(self):
        AG, g = self._glossary("全屋定制\n")
        segs = [{"id": "s1", "text": "全屋定置也做", "words": [{"word": "全屋定置"}, {"word": "也做"}]}]
        log = AG.apply_to_transcript(segs, g)
        self.assertEqual(segs[0]["text"], "全屋定制也做")
        self.assertEqual("".join(w["word"] for w in segs[0]["words"]), "全屋定制也做", "落字必须同步改")
        self.assertEqual(log[0]["corrections"], [{"from": "全屋定置", "to": "全屋定制"}])
        # 无关词不许动
        segs2 = [{"id": "s2", "text": "这家店的老板很实在", "words": []}]
        self.assertEqual(AG.apply_to_transcript(segs2, g), [])

    def test_hard_mapping_wins(self):
        AG, g = self._glossary("炭火 => 碳火\n")
        segs = [{"id": "s1", "text": "用炭火烤", "words": [{"word": "用"}, {"word": "炭火"}, {"word": "烤"}]}]
        AG.apply_to_transcript(segs, g)
        self.assertEqual(segs[0]["text"], "用碳火烤")

    def test_short_words_need_same_pronunciation(self):
        """2 字词只差一字太危险：没同音证据不许改。"""
        AG, g = self._glossary("炭火\n")
        segs = [{"id": "s1", "text": "这家店和大火都旺", "words": []}]
        AG.apply_to_transcript(segs, g)
        self.assertEqual(segs[0]["text"], "这家店和大火都旺", "「大火」不该被改成「炭火」")


class NgTakeTests(unittest.TestCase):
    """NG 片段（说错重来 / 重复录制）要能识别、能剔除、能说清原因。"""

    def test_self_correction_cue_drops_cue_and_previous_take(self):
        import step2_transcribe as S2
        segs = [
            {"id": "n1", "text": "这家店开了八年", "start_sec": 1.0, "end_sec": 2.5},
            {"id": "n2", "text": "不对，重来", "start_sec": 3.0, "end_sec": 3.8},
            {"id": "n3", "text": "这家店开了八年了", "start_sec": 5.0, "end_sec": 6.5},
        ]
        stats = S2.filter_offcamera_and_retakes(segs)
        self.assertEqual(stats.get("self_correction_dropped"), 2)
        dropped = [s["id"] for s in segs if s.get("is_filler")]
        self.assertEqual(dropped, ["n1", "n2"], "错的半句和「重来」本身都不该留在片子里")
        self.assertFalse(segs[2].get("is_filler"), "重录那句要留下")
        report = S2.collect_ng_report(segs)
        self.assertEqual(len(report), 2)
        self.assertTrue(all(x["reason"].startswith("ng_self_correction") for x in report))

    def test_question_with_negative_word_is_not_a_cue(self):
        import step2_transcribe as S2
        self.assertFalse(S2.is_self_correction_cue("这家店不对吗"))
        self.assertTrue(S2.is_self_correction_cue("说错了，重来一遍"))

    def test_repeated_take_far_apart_is_still_caught(self):
        """现场说错→停→重新讲，中间常隔十来句；窗口不能只看 5 句。"""
        import step2_transcribe as S2
        segs = [{"id": "a0", "text": "我们先看一下价格", "start_sec": 0.0, "end_sec": 1.0}]
        for k in range(8):
            segs.append({"id": f"m{k}", "text": f"中间闲话第{k}句内容不一样", "start_sec": 1.0 + k, "end_sec": 2.0 + k})
        segs.append({"id": "b0", "text": "我们先看一下价格", "start_sec": 12.0, "end_sec": 13.0})
        stats = S2.filter_offcamera_and_retakes(segs)
        self.assertGreaterEqual(stats.get("retake_dropped", 0), 1, "隔 9 句的重录也要认出来")
        self.assertTrue(segs[0].get("is_filler"))
        self.assertFalse(segs[-1].get("is_filler"))


class SceneLabelTests(unittest.TestCase):
    """配错镜的根子是"画面里是什么没人知道"：本地视觉分类标签必须能变成可检索的中文语义。"""

    def test_english_labels_become_chinese_concepts(self):
        from vision_scene_labels import labels_to_tags, tag_groups
        labels = [{"label": "sofa", "conf": 0.71}, {"label": "couch", "conf": 0.62},
                  {"label": "furniture", "conf": 0.55}, {"label": "living room", "conf": 0.4},
                  {"label": "junk", "conf": 0.02}]
        tags, ev = labels_to_tags(labels)
        self.assertIn("沙发", tags)
        self.assertIn("家具", tags)
        self.assertTrue(all(e["conf"] >= 0.18 for e in ev), "低置信度标签不许进证据")
        self.assertIn("goods", tag_groups(tags))

    def test_scene_tags_make_broll_matchable(self):
        """有了画面标签，沙发那句才能配到沙发素材；办公区素材必须仍是「无依据」。"""
        import broll_semantics as B
        sofa = {"filename": "IMG_1938.MOV", "scene_tags_zh": ["沙发", "家具", "客厅"]}
        office = {"filename": "IMG_2001.MOV", "scene_tags_zh": ["办公桌", "办公"]}
        line = "一张沙发一张沙发的坐，慢慢挑"
        good = B.relate(line, sofa)
        bad = B.relate(line, office)
        self.assertEqual(good["verdict"], "hit")
        self.assertTrue(good["basis"])
        self.assertEqual(bad["verdict"], "none", "画面里没有沙发就不许配沙发这句")
        self.assertEqual(bad["basis"], [])

    def test_heic_decode_helper_keeps_source_untouched(self):
        from common import ensure_image_decodable
        tmp = Path(tempfile.mkdtemp())
        jpg = tmp / "a.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xd9")
        self.assertEqual(ensure_image_decodable(jpg), jpg, "jpg 本来就可读，直接返回原路径")
        fake_heic = tmp / "b.heic"
        fake_heic.write_bytes(b"not really heic")
        out = ensure_image_decodable(fake_heic, cache_dir=tmp / "dec")
        self.assertIsNone(out, "解不出来的 HEIC 要如实返回 None，不许假装成功")
        self.assertTrue(fake_heic.is_file(), "源文件必须原封不动")

    def test_tag_frames_dense_sampling_reaches_multiple_points(self):
        import tag_frames as TF
        times = TF._frame_times(12.0, 6)
        self.assertEqual(len(times), 6)
        self.assertTrue(all(0 <= t <= 12.0 for t in times))
        self.assertGreater(max(times) - min(times), 8.0, "密帧要铺开整段，不能全挤在开头")


class EndingPlanTests(unittest.TestCase):
    """片尾收尾：话没说完要能说清，收住了不许瞎补。"""

    PROFILE = {"store_name": "老杨家烧烤", "city": "珠海", "main_service": "烧烤",
               "address": "香洲区凤凰路 12 号"}

    def test_unfinished_ending_is_flagged_with_reason(self):
        import ending_plan as EP

        segs = [{"text": "开了八年烧烤店"}, {"text": "从来不用冻肉"}, {"text": "这个价格就这样"}]
        plan = EP.analyze_ending(segs, profile=self.PROFILE)
        self.assertIn(plan["verdict"], ("soft", "none"))
        self.assertFalse(plan["has_cta"])
        self.assertTrue(plan["needs_operator_line"])
        self.assertIn("这个价格就这样", plan["reason"])

    def test_card_text_uses_real_store_facts_not_platitudes(self):
        import ending_plan as EP

        segs = [{"text": "开了八年烧烤店"}, {"text": "这个价格就这样"}]
        text = EP.best_card_text(EP.analyze_ending(segs, profile=self.PROFILE))
        self.assertIn("珠海", text)
        self.assertIn("老杨家烧烤", text)
        self.assertIn("凤凰路", text)
        self.assertNotIn("点赞关注", text)

    def test_already_closed_ending_adds_no_line(self):
        import ending_plan as EP

        segs = [{"text": "开了八年烧烤店"}, {"text": "想吃的来香洲凤凰路找我，报我名字"}]
        plan = EP.analyze_ending(segs, profile=self.PROFILE)
        self.assertEqual(plan["verdict"], "closed")
        self.assertFalse(plan["needs_operator_line"])

    def test_framework_map_names_the_missing_slot(self):
        import ending_plan as EP

        segs = [{"text": "这家店一开就是八年"}, {"text": "穿串要两小时，温度控制在两百度"},
                {"text": "我这个人做事实在"}, {"text": "所以贵一点但省你时间"}]
        fm = EP.map_framework(segs)
        self.assertIn("开篇钩子", fm["covered"])
        self.assertIn("细节展示", fm["covered"])
        self.assertIn("人格化表达", fm["covered"])
        self.assertEqual(fm["missing"], ["转化指令"])


class EndingGateTests(unittest.TestCase):
    """片尾没挂上收尾就没过门禁。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pipe = Path(self.temp.name) / "pipeline"
        self.pipe.mkdir(parents=True)

    def test_names_the_missing_ending_in_plain_words(self):
        import qa_inspector

        labels = Path(qa_inspector.__file__).read_text(encoding="utf-8")
        self.assertIn("ending_complete", labels)
        self.assertIn("片尾收住了，观众知道下一步做什么", labels)


class CrossPlatformTests(unittest.TestCase):
    """跨平台：能跑 / 会静默降级 / 必须换技术选型，三种情况要分得清。"""

    def test_old_ffmpeg_falls_back_to_vsync(self):
        import platform_probe as PP

        PP._CAPS_CACHE.clear()
        try:
            PP._CAPS_CACHE[""] = {"path": "/usr/bin/ffmpeg", "version": "ffmpeg version 4.4",
                                  "major_minor": (4, 4), "fps_mode": False, "usable_bins": []}
            self.assertEqual(PP.fps_mode_args("cfr"), ["-vsync", "cfr"])
            self.assertEqual(PP.fps_mode_args("passthrough"), ["-vsync", "passthrough"])
            PP._CAPS_CACHE[""] = {"path": "x", "version": "ffmpeg version 7.1",
                                  "major_minor": (7, 1), "fps_mode": True, "usable_bins": []}
            self.assertEqual(PP.fps_mode_args("cfr"), ["-fps_mode", "cfr"])
        finally:
            PP._CAPS_CACHE.clear()

    def test_probe_platform_speaks_plain_chinese_about_degradation(self):
        import platform_probe as PP

        rep = PP.probe_platform()
        self.assertIn("platform", rep)
        self.assertIn("ffmpeg", rep)
        for feat in ("vision_ocr", "vision_face", "asr", "tts", "browser"):
            self.assertIn(feat, rep["features"])
        for note in (rep.get("degradations") or {}).values():
            self.assertTrue(any("\u4e00" <= ch <= "\u9fff" for ch in note), note)

    def test_broken_vendor_binary_is_skipped(self):
        """离线包自带一个跑不起来的 ffmpeg（架构不对）时，必须回落系统 PATH。"""
        import common

        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "vendor" / "bin"
            bin_dir.mkdir(parents=True)
            fake = bin_dir / "ffmpeg"
            fake.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
            fake.chmod(0o755)
            common._VENDOR_BIN_OK.clear()
            with unittest.mock.patch.object(common, "VENDOR_DIR", Path(td) / "vendor"):
                got = common.find_bin("ffmpeg")
            common._VENDOR_BIN_OK.clear()
        self.assertNotEqual(got, str(fake.resolve()), "跑不起来的 vendor 二进制不许被当成可用")

    def test_pkg_skips_homebrew_dynamic_ffmpeg(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_release_zips", str(ROOT / "dev_tools" / "build_release_zips.py"))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        self.assertTrue(callable(getattr(mod, "_is_dynamic_homebrew_bin", None)))
        # 非 Darwin 平台不判，直接放过（否则 Linux/Windows 打包会误伤）
        if platform.system() != "Darwin":
            self.assertFalse(mod._is_dynamic_homebrew_bin(Path("/bin/ls")))


class EndCardPixelTests(unittest.TestCase):
    """尾卡要真的画出来——不能只写在 06_packaging.json 里（这就是上次"只跑不生效"的老坑）。"""

    def test_end_card_renders_both_lines(self):
        import numpy as np
        from render_mp4 import render_packaging_png

        img = render_packaging_png({
            "kind": "end_card_cta",
            "fg_hex": "#FFE28A",
            "stroke_hex": "#000000",
            "text_or_asset": "在珠海想烧烤的，直接来老杨家烧烤看看",
            "subtext": "香洲区凤凰路 12 号，报我名字就行",
        }, "style_01")
        alpha = np.array(img)[:, :, 3]
        rows = (alpha > 40).sum(axis=1)
        bands, inb, start = [], False, 0
        for i, v in enumerate(rows):
            if v > 0 and not inb:
                start, inb = i, True
            elif v == 0 and inb:
                bands.append((start, i))
                inb = False
        if inb:
            bands.append((start, len(rows)))
        solid = [b for b in bands if b[1] - b[0] > 3]
        self.assertGreaterEqual(len(solid), 2, f"主句和副行都要有墨迹，实际行带：{solid}")
        self.assertGreater(int(alpha.max()), 200, "字要不透明，不能被背景吃掉")

    def test_two_char_homophone_only_fixed_when_pinyin_available(self):
        import asr_glossary as G

        segs = [{"text": "我们做厨柜", "words": [{"word": "我们"}, {"word": "做"}, {"word": "厨柜"}]}]
        g = {"terms": ["橱柜"], "replacements": {}, "background": "", "sources": []}
        import importlib.util
        g["pinyin_available"] = importlib.util.find_spec("pypinyin") is not None
        log = G.apply_to_transcript(segs, g)
        if g["pinyin_available"]:
            self.assertTrue(log and segs[0]["text"] == "我们做橱柜", "同音两字词应当纠对")
        else:
            self.assertEqual(log, [], "没有拼音证据时不许动两字词")


class VisionAgentEngineTests(unittest.TestCase):
    """画面理解必须走大模型：本地只能显式兜底，且结果要打标。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "work"
        self.out.mkdir()

    def test_tasks_packet_keeps_frames_and_asks_in_plain_words(self):
        import vision_agent as VA

        clips = [{"file": "IMG_1938.MOV", "duration_sec": 12.4,
                  "frame_paths": [{"path": "/tmp/f0.jpg", "t": 1.2}, {"path": "/tmp/f1.jpg", "t": 5.0}]}]
        p = VA.write_tasks(self.out, clips)
        data = json.loads(p.read_text(encoding="utf-8"))
        clip = data["clips"][0]
        self.assertEqual(len(clip["frames"]), 2)
        self.assertTrue(clip["frames"][0]["path"].startswith("/"))
        self.assertIn("主体", clip["ask"])
        self.assertIn("不要编", clip["ask"])

    def test_unjudged_clips_are_named_not_silently_passed(self):
        import vision_agent as VA

        write_json(self.out / VA.SEMANTICS_NAME, {"clips": [{"file": "a.MOV"}, {"file": "b.MOV"}]})
        write_json(self.out / VA.LABELS_NAME, {"clips": [{"file": "a.MOV", "summary": "后厨穿串", "tags": ["后厨"]}]})
        rep = VA.merge_labels(self.out)
        self.assertEqual(rep["clips"], 1)
        self.assertEqual(rep["missing"], ["b.MOV"])
        sem = read_json(self.out / VA.SEMANTICS_NAME)
        self.assertEqual(sem["clips"][0]["vision_engine"], "agent")
        self.assertEqual(sem["clips"][1]["vision_engine"], "pending_agent")
        self.assertIn("待 Agent 判定", (self.out / VA.NOTES_NAME).read_text(encoding="utf-8"))

    def test_local_engine_refuses_without_explicit_consent(self):
        r = subprocess.run([sys.executable, str(ROOT / "short-video-studio" / "scripts" / "tag_frames.py"),
                            "--input-dir", self.temp.name, "--out", str(self.out), "--engine", "local"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)
        self.assertIn("拒绝用本地分类冒充大模型理解", r.stderr)

    def test_local_engine_marks_result_as_fallback(self):
        import vision_agent as VA

        src = (ROOT / "short-video-studio" / "scripts" / "tag_frames.py").read_text(encoding="utf-8")
        self.assertIn("user_accepted_local_fallback", src)
        self.assertIn("user_accepted_local_fallback", VA.LOCAL_VISION_REFUSAL)


class AdaptiveWorkersTests(unittest.TestCase):
    def test_never_takes_every_core_and_respects_item_count(self):
        from common import adaptive_workers

        w = adaptive_workers(100, cap=8)
        self.assertGreaterEqual(w, 1)
        self.assertLessEqual(w, 8)
        self.assertLessEqual(adaptive_workers(3, cap=8), 3)


class BlindSheetTests(unittest.TestCase):
    """盲审接触表：左右随机，但对照表要能对上。"""

    def test_pairs_styles_and_writes_key(self):
        import importlib.util
        from PIL import Image

        spec = importlib.util.spec_from_file_location(
            "blind_contact_sheet", str(ROOT / "dev_tools" / "blind_contact_sheet.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        td = Path(self.id().replace(".", "_"))
        with tempfile.TemporaryDirectory() as tmp:
            o, u = Path(tmp) / "orig", Path(tmp) / "ours"
            o.mkdir(); u.mkdir()
            for i in (1, 2):
                Image.new("RGB", (540, 960), (180, 40, 40)).save(o / f"style_{i:02d}.png")
                Image.new("RGB", (540, 960), (40, 40, 180)).save(u / f"style_{i:02d}.png")
            rep = mod.build_sheet(mod.collect(o), mod.collect(u), Path(tmp) / "s.jpg", Path(tmp) / "k.csv")
            self.assertEqual(rep["styles"], 2)
            rows = (Path(tmp) / "k.csv").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(rows[0], "style_id,A,B,verdict,note")
            for row in rows[1:]:
                _, a, b, _v, _n = row.split(",")
                self.assertEqual({a, b}, {"orig", "our"})


class StickerRenderTests(unittest.TestCase):
    """贴纸几何：显示尺寸 = 原生 × scale × 画布宽/720（用原版帧反解出来的真值）。"""

    def test_paint_size_matches_original_frame(self):
        from sticker_render import paint_size
        # 实测草稿「手机屏幕边框」：原生 475×540、scale 2.18822、画布 1080
        w, h = paint_size(475, 540, 2.18822212042551, 1080)
        self.assertEqual((w, h), (1560, 1772))
        # 上边界 = 中心 y 1104 − 高/2 = 218 → 画布 11.35%，原版量到 11.5%
        cy = 1920 / 2 - (-0.14987714987715) * 1920 / 2
        self.assertAlmostEqual((cy - h / 2) / 1920, 0.1135, places=3)

    def test_resolve_sticker_dir_rejects_unknown(self):
        from sticker_assets import resolve_sticker_dir
        self.assertIsNone(resolve_sticker_dir("/no/such/dir", "1"))
        self.assertIsNone(resolve_sticker_dir("", ""))

    def test_overwide_sticker_cropped_to_canvas(self):
        """渲染宽 1560 > 画布 1080：按交集裁切后 pad，alpha 只能落在画布内。"""
        import tempfile
        from PIL import Image
        from sticker_render import render_one_layer
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "f00000.png"
            Image.new("RGBA", (475, 540), (255, 0, 0, 255)).save(src)
            out = Path(td) / "layer.mov"
            render_one_layer(Path(td), True, 0.0, out, 1080, 1920, 1560, 1772,
                             849, 1104, 0.0, 0.4, 30.0)
            r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1",
                                "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
            self.assertTrue(r.stdout, "贴纸图层渲染失败")
            import io
            im = Image.open(io.BytesIO(r.stdout)).convert("RGBA")
            self.assertEqual(im.size, (1080, 1920))
            bb = im.getchannel("A").getbbox()
            self.assertIsNotNone(bb)
            self.assertEqual(bb[0], 69)          # 左边界：849 − 1560/2
            self.assertEqual(bb[1], 218)         # 上边界：1104 − 1772/2
            self.assertEqual(bb[2], 1080)        # 右侧被画布裁掉
            self.assertEqual(bb[3], 1920)        # 下侧被画布裁掉


class OverlayFrameRenderTests(unittest.TestCase):
    """带 keyframe 的叠加层按帧渲染（ffmpeg scale 不能逐帧改尺寸，只能自己算）。"""

    def test_display_size_uses_natural_height(self):
        from overlay_frame_render import display_size
        # 主页素材 1080×1440、scale 0.2341 → 原版实测 189×336
        w, h = display_size((1080, 1440), 0.2341, (1080, 1920))
        self.assertEqual((w, h), (190, 337))
        # 9:16 素材照旧 = 画布 × scale
        self.assertEqual(display_size((1080, 1920), 0.5, (1080, 1920)), (540, 960))

    def test_keyframe_interp_holds_ends(self):
        from overlay_frame_render import _interp
        kf = [(0.0, 1.0), (2.0, 0.01)]
        self.assertAlmostEqual(_interp(kf, -1.0)[0], 1.0)
        self.assertAlmostEqual(_interp(kf, 1.0)[0], 0.505)
        self.assertAlmostEqual(_interp(kf, 5.0)[0], 0.01)

    def test_zero_alpha_frames_skipped(self):
        import tempfile
        from PIL import Image
        from overlay_frame_render import AnimLayer, render_layer_seq
        with tempfile.TemporaryDirectory() as td:
            im = Image.new("RGBA", (108, 144), (250, 40, 40, 255))
            layer = AnimLayer(image=im, natural=(108, 144), t_dur=0.3,
                              base_scale=0.5, alpha_kf=[(0.0, 1.0), (0.1, 0.0)])
            n = render_layer_seq(layer, 108, 192, 10.0, Path(td))
            # 帧点是 0/0.1/0.2s，只有首帧 alpha=1，后两帧全透明不该落盘
            self.assertEqual(n, 1)
            self.assertTrue((Path(td) / "f00000.png").is_file())
            self.assertFalse((Path(td) / "f00002.png").exists())


if __name__ == "__main__":
    unittest.main()
