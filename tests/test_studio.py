import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

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
        self.assertEqual(lib.get("version"), "3.2.0")
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
            self.assertGreaterEqual(float(sub["safe_y_ratio"]), 0.63)
            self.assertLessEqual(float(sub["safe_y_ratio"]), 0.78)
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
            {"style_21": 0.85, "style_22": 0.90, "style_23": 0.95, "style_24": 1.00},
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

        # style_21 (85% B-roll)
        p21 = self.root / "p21"
        p21.mkdir()
        write_json(p21 / "01_manifest.json", {"items": manifest_items})
        write_json(p21 / "03_audio_events.json", {"locked_duration_sec": 9.6, "locked_duration_frames": 288, "sentences": sents, "events": evts})
        edl21 = run_step4(p21, track="store", style_id="style_21", edit_profile="documentary")
        self.assertGreaterEqual(edl21["broll_ratio_target"], 0.85)
        self.assertGreaterEqual(edl21["broll_ratio_actual"], 0.70)
        self.assertTrue(all(float(c["duration_sec"]) >= 0.30 for c in edl21["clips"]))

        # style_24 (100% B-roll, zero A-roll) + TTS source mismatch must block a_roll binding
        p24 = self.root / "p24"
        p24.mkdir()
        tts_sents = [{**s, "source_media": str(self.root / "tts_fake.wav")} for s in sents]
        write_json(p24 / "01_manifest.json", {"items": manifest_items})
        write_json(p24 / "03_audio_events.json", {"locked_duration_sec": 9.6, "locked_duration_frames": 288, "sentences": tts_sents, "events": evts})
        edl24 = run_step4(p24, track="creator", style_id="style_24", edit_profile="documentary")
        self.assertEqual(edl24["broll_ratio_actual"], 1.0)
        self.assertFalse(any(c["track_role"] == "a_roll" for c in edl24["clips"]))
        self.assertTrue(all(float(c["duration_sec"]) >= 0.30 for c in edl24["clips"]))

        # QA #8 must pass on 100% high-broll style_24
        write_json(p24 / "05_subtitles.json", {"template_id": "style_24", "font_path": str(ASSETS_DIR / "fonts" / "SourceHanSerifCN-Heavy.otf"), "entries": []})
        write_json(p24 / "06_packaging.json", {"visual_elements": []})
        qa_rep = inspect_video(p24, out_mp4, export_mode="mp4")
        c8 = next(c for c in qa_rep["checks"] if c["id"] == "shot_duration_dist")
        self.assertEqual(c8["status"], "PASS", f"QA #8 failed on style_24: {c8}")

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
        none_cnt = sum(1 for s in lib["styles"] if (s.get("pip_composition_spec") or {}).get("mode") == "none")
        self.assertGreaterEqual(none_cnt, 13)
        self.assertEqual((lib["styles"][0].get("pip_composition_spec") or {}).get("mode"), "none")

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
        self.assertEqual(by_id["style_05"]["pip_composition_spec"]["mode"], "person_behind_giant_text_pip")
        self.assertEqual(by_id["style_12"]["pip_composition_spec"]["mode"], "none")
        self.assertEqual(by_id["style_13"]["pip_composition_spec"]["mode"], "top_white_rounded_frame_pip")
        self.assertEqual(by_id["style_14"]["pip_composition_spec"]["mode"], "black_spotlight_circle_pip")
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
        for sid in ("style_02", "style_03", "style_20"):
            tpl = tpls[sid]
            self.assertTrue(tpl.get("full_width_bar"), f"{sid} must enable full_width_bar")
            sub_p = self.root / f"sub_fw_{sid}.png"
            w, h = render_subtitle_png(
                {"text": "这一句一定要看清底条通栏", "keywords": ["底条通栏"]},
                tpl,
                None,
                sub_p,
            )
            self.assertEqual(w, CANVAS_WIDTH, f"{sid} subtitle bar must span full 1080px")
            px, _py = compute_subtitle_place_xy(w, h, tpl)
            self.assertEqual(px, 0)
            with Image.open(sub_p) as im:
                self.assertEqual(im.width, CANVAS_WIDTH)
                # 最左与最右边缘均应有不透明通栏底条
                self.assertGreater(im.getpixel((4, h // 2))[3], 100)
                self.assertGreater(im.getpixel((CANVAS_WIDTH - 5, h // 2))[3], 100)

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
        self.assertEqual(len(res_s03["visual_elements"]), 0)

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
        self.assertEqual(prof.get("xfade_applied"), 1)
        self.assertEqual(prof.get("xfade_transition"), "zoomin")
        info = ffprobe_media(base_mp4)
        dur = float(info.get("format", {}).get("duration", 0.0))
        self.assertAlmostEqual(dur, 3.0, delta=0.04, msg="xfade 转场后总时长必须 0 帧漂移")

    def test_v32_ab_roll_switch_grammar_frequency_and_keyframes(self):
        from common import ASSETS_DIR, read_json
        from step4_build_edl import run_step4

        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        self.assertEqual(lib.get("version"), "3.2.0")
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
        self.assertEqual(edl10["keyframe_moves_applied"], edl10["clip_count"])

        # 3) style_05 (depth_hook_then_framed_broll): A-roll 挂 person_behind_giant_text_pip，B-roll 切 blur_bg_center_card_pip
        p05 = self.root / "edl_s05"
        p05.mkdir()
        write_json(p05 / "01_manifest.json", {"items": man_items})
        write_json(p05 / "03_audio_events.json", ae_doc)
        edl05 = run_step4(p05, track="store", style_id="style_05")
        a_modes_05 = {(c.get("pip_layout") or {}).get("mode") for c in edl05["clips"] if c["track_role"] == "a_roll"}
        b_modes_05 = {(c.get("pip_layout") or {}).get("mode") for c in edl05["clips"] if c["track_role"] == "b_roll"}
        self.assertIn("person_behind_giant_text_pip", a_modes_05)
        self.assertIn("blur_bg_center_card_pip", b_modes_05)

        # 4) style_07 (cutout_broll_alternating): A-roll 挂 vision_portrait_cutout_pip，纯 B-roll 镜保持全屏无 pip_layout
        p07 = self.root / "edl_s07"
        p07.mkdir()
        write_json(p07 / "01_manifest.json", {"items": man_items})
        write_json(p07 / "03_audio_events.json", ae_doc)
        edl07 = run_step4(p07, track="store", style_id="style_07")
        for c in edl07["clips"]:
            if c["track_role"] == "a_roll":
                self.assertEqual((c.get("pip_layout") or {}).get("mode"), "vision_portrait_cutout_pip")
            elif c["track_role"] == "b_roll":
                self.assertIsNone(c.get("pip_layout"), "cutout_broll_alternating 下纯 B-roll 镜应全屏呼吸展示")

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

        # 1) style_21 已补齐 vision_portrait_cutout_pip (outline_px=8)
        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        s21 = next(s for s in lib["styles"] if s["style_id"] == "style_21")
        self.assertEqual(s21["pip_composition_spec"]["mode"], "vision_portrait_cutout_pip")
        self.assertEqual(s21["pip_composition_spec"]["outline_px"], 8)

        # 2) style_02 / 03 / 20 启用 dual_line_banner 后，长句自动排为上下双行高厚度大字横幅 (h >= 120px)
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
            self.assertEqual(w, 1080)
            self.assertGreaterEqual(h, 128, f"{sid} dual-line banner should be thick two-line banner, got h={h}")

        # 3) 同屏花字与底字幕防重互斥
        subs = [{"text": "钱背后藏着定价真相", "start_sec": 0.0, "end_sec": 2.0}]
        self.assertTrue(_flower_duplicates_subtitle("两块钱背后藏", 0.0, 1.8, subs))
        self.assertFalse(_flower_duplicates_subtitle("门店实拍核验", 0.0, 1.8, subs))


if __name__ == "__main__":
    unittest.main()
