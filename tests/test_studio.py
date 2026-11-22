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
        self.assertEqual(lib.get("version"), "2.1.0")
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
        self.assertEqual(whitebox_cnt, 20)
        self.assertEqual(
            high_broll_targets,
            {"style_21": 0.85, "style_22": 0.90, "style_23": 0.95, "style_24": 1.00},
        )

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
            "33-" + "全屋定制",
            "24-" + "美业",
            "58-" + "汽车销售",
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
        ]
        scan_roots = [
            ROOT / "short-video-studio",
            ROOT / "dev_tools",
            ROOT / "README.md",
            ROOT / "BRIEF.md",
            ROOT / "tests" / "test_studio.py",
        ]
        text_exts = {".py", ".md", ".json", ".cube", ".mjs", ".command", ".bat", ".sh", ".ps1"}
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


if __name__ == "__main__":
    unittest.main()
