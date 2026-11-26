"""V8.0 双源风格圣经、style_02 统一视觉血统与总导演闭环测试。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

from common import write_json  # noqa: E402
import director_plan as dp  # noqa: E402
import independent_verifier as iv  # noqa: E402
import kinetic_choreographer as kc  # noqa: E402
import qa_inspector as qa  # noqa: E402
import render_template_overlays as rto  # noqa: E402
import step4_build_edl as s4  # noqa: E402


def _make_audio_store_and_biz():
    return {
        "locked_duration_sec": 18.0,
        "sentences": [
            {
                "id": "sent_001",
                "text": "2024年实体工厂老板要把短视频做起来",
                "start_sec": 0.0,
                "end_sec": 3.4,
                "start_frame": 0,
                "end_frame": 102,
                "source_media": "aroll_a.mp4",
                "words": [
                    {"text": "2024", "start_sec": 0.05, "end_sec": 0.45},
                    {"text": "年", "start_sec": 0.45, "end_sec": 0.65},
                    {"text": "实", "start_sec": 0.70, "end_sec": 0.90},
                    {"text": "体", "start_sec": 0.90, "end_sec": 1.10},
                    {"text": "老", "start_sec": 1.25, "end_sec": 1.50},
                    {"text": "板", "start_sec": 1.50, "end_sec": 1.75},
                    {"text": "做", "start_sec": 2.30, "end_sec": 2.60},
                    {"text": "起", "start_sec": 2.60, "end_sec": 2.90},
                    {"text": "来", "start_sec": 2.90, "end_sec": 3.30},
                ],
            },
            {
                "id": "sent_002",
                "text": "顺便看看这条走廊怎么走过去",
                "start_sec": 3.6,
                "end_sec": 6.4,
                "start_frame": 108,
                "end_frame": 192,
                "source_media": "aroll_a.mp4",
            },
            {
                "id": "sent_003",
                "text": "不是产品不好而是太慢了",
                "start_sec": 6.6,
                "end_sec": 9.8,
                "start_frame": 198,
                "end_frame": 294,
                "source_media": "aroll_b.mp4",
                "words": [
                    {"text": "不", "start_sec": 6.7, "end_sec": 6.9},
                    {"text": "是", "start_sec": 6.9, "end_sec": 7.1},
                    {"text": "产", "start_sec": 7.1, "end_sec": 7.3},
                    {"text": "品", "start_sec": 7.3, "end_sec": 7.5},
                    {"text": "不", "start_sec": 7.5, "end_sec": 7.7},
                    {"text": "好", "start_sec": 7.7, "end_sec": 7.9},
                    {"text": "而", "start_sec": 8.0, "end_sec": 8.2},
                    {"text": "是", "start_sec": 8.2, "end_sec": 8.4},
                    {"text": "太", "start_sec": 8.5, "end_sec": 8.8},
                    {"text": "慢", "start_sec": 8.8, "end_sec": 9.3},
                ],
            },
            {
                "id": "sent_004",
                "text": "三大招1.虾饺 2.排骨 3.凤爪",
                "start_sec": 10.0,
                "end_sec": 14.5,
                "start_frame": 300,
                "end_frame": 435,
                "source_media": "aroll_a.mp4",
                "words": [
                    {"text": "1.", "start_sec": 10.8, "end_sec": 11.0},
                    {"text": "虾", "start_sec": 11.0, "end_sec": 11.3},
                    {"text": "饺", "start_sec": 11.3, "end_sec": 11.6},
                    {"text": "2.", "start_sec": 12.0, "end_sec": 12.2},
                    {"text": "排", "start_sec": 12.2, "end_sec": 12.5},
                    {"text": "骨", "start_sec": 12.5, "end_sec": 12.8},
                    {"text": "3.", "start_sec": 13.1, "end_sec": 13.3},
                    {"text": "凤", "start_sec": 13.3, "end_sec": 13.6},
                    {"text": "爪", "start_sec": 13.6, "end_sec": 14.0},
                ],
            },
            {
                "id": "sent_005",
                "text": "欢迎大家来点赞茶楼",
                "start_sec": 14.8,
                "end_sec": 18.0,
                "start_frame": 444,
                "end_frame": 540,
                "source_media": "aroll_a.mp4",
            },
        ],
        "events": [],
    }


class V8PlaybooksAndUnifiedStyle02Tests(unittest.TestCase):
    def test_24_playbooks_and_common_grammar_exist_and_zero_privacy_leaks(self):
        spec = ROOT / "short-video-studio" / "assets" / "style_spectrum"
        pb_json_p = spec / "style_playbooks.json"
        cg_json_p = spec / "common_editing_grammar.json"
        cg_md_p = spec / "common_editing_grammar.md"
        self.assertTrue(pb_json_p.is_file())
        self.assertTrue(cg_json_p.is_file())
        self.assertTrue(cg_md_p.is_file())

        pb_doc = json.loads(pb_json_p.read_text(encoding="utf-8"))
        self.assertEqual(pb_doc["n_styles"], 24)
        self.assertEqual(pb_doc["n_rea_backed"], 24)
        self.assertEqual(pb_doc["styles"]["style_02"]["subtitle_stage_architecture"]["recommended_theme_profile"], "style_02_unified")

        forbidden = [str(Path.home()), f"/Users/{Path.home().name}"]
        sources_p = ROOT / "forensic" / "style_sources.json"
        if sources_p.is_file():
            sources = json.loads(sources_p.read_text(encoding="utf-8")).get("styles") or {}
            forbidden.extend(str(v) for v in sources.values() if v)

        files_to_scan = [pb_json_p, cg_json_p, cg_md_p]
        for idx in range(1, 25):
            md = spec / "playbooks" / f"style_{idx:02d}.md"
            self.assertTrue(md.is_file(), f"缺少圣经: {md}")
            files_to_scan.append(md)

        for fp in files_to_scan:
            txt = fp.read_text(encoding="utf-8")
            for bad in forbidden:
                if len(bad) >= 2:
                    self.assertNotIn(bad, txt, f"{fp.name} 存在隐私或参考草稿名泄漏: {bad}")

    def test_unified_eight_primitives_word_timing_and_union_face_avoidance(self):
        ae = {
            "locked_duration_sec": 26.0,
            "sentences": [
                *_make_audio_store_and_biz()["sentences"],
                {"id": "sent_006", "text": "单店利润提升到500万+", "start_sec": 18.2, "end_sec": 21.5, "start_frame": 546, "end_frame": 645, "source_media": "aroll_a.mp4"},
                {"id": "sent_007", "text": "本质是把成本算清再扩张", "start_sec": 21.7, "end_sec": 26.0, "start_frame": 651, "end_frame": 780, "source_media": "aroll_a.mp4"},
            ],
            "events": [],
        }
        profile = {
            "primary_stage_mode": "wide_behind_person",
            "allow_behind_person": True,
            "aroll_profiles": [
                {"media_path": "aroll_a.mp4", "allow_behind_person": True, "face_bbox": {"x": 0.35, "y": 0.30, "w": 0.28, "h": 0.20}},
                {"media_path": "aroll_b.mp4", "allow_behind_person": True, "face_bbox": {"x": 0.25, "y": 0.18, "w": 0.42, "h": 0.30}},
            ],
        }
        plan = {
            "step": "03b_director_plan",
            "source": "agent",
            "style_id": "style_02",
            "theme_profile": "style_02_unified",
            "acts": [
                {"act_id": "act_01", "sentence_ids": ["sent_001"], "dramatic_role": "hook", "emotion_intensity": 0.9,
                 "primitive": "opening_triad", "visual_copy": {"headline": "2024年", "items": ["实体工厂老板", "做起来"]},
                 "camera_intent": "slow_push_115", "broll_policy": "lock_aroll", "sfx_budget": 2},
                {"act_id": "act_02", "sentence_ids": ["sent_002"], "dramatic_role": "setup", "emotion_intensity": 0.2,
                 "primitive": "plain_bridge", "visual_copy": {},
                 "camera_intent": "steady_hold", "broll_policy": "allow_verified_broll", "broll_keywords": ["走廊"], "sfx_budget": 0},
                {"act_id": "act_03", "sentence_ids": ["sent_003"], "dramatic_role": "twist", "emotion_intensity": 0.95,
                 "primitive": "pain_slam_danmaku", "visual_copy": {"headline": "不是产品", "items": ["太慢了"]},
                 "camera_intent": "jump_closeup", "broll_policy": "lock_aroll", "sfx_budget": 2},
                {"act_id": "act_04", "sentence_ids": ["sent_004"], "dramatic_role": "proof", "emotion_intensity": 0.8,
                 "primitive": "list_gather_depth", "visual_copy": {"headline": "三大招", "items": ["虾饺", "排骨", "凤爪"]},
                 "camera_intent": "slow_pull_115", "broll_policy": "allow_verified_broll", "broll_keywords": ["虾饺"], "sfx_budget": 2},
                {"act_id": "act_05", "sentence_ids": ["sent_005"], "dramatic_role": "cta", "emotion_intensity": 0.75,
                 "primitive": "inline_highlight_cta", "visual_copy": {"headline": "点赞茶楼", "items": ["欢迎大家来"]},
                 "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 1},
                {"act_id": "act_06", "sentence_ids": ["sent_006"], "dramatic_role": "proof", "emotion_intensity": 0.85,
                 "primitive": "number_burst_pair", "visual_copy": {"headline": "500万+", "items": ["单店利润"]},
                 "camera_intent": "jump_closeup", "broll_policy": "lock_aroll", "sfx_budget": 1},
                {"act_id": "act_07", "sentence_ids": ["sent_007"], "dramatic_role": "framework", "emotion_intensity": 0.7,
                 "primitive": "stacked_dual_color", "visual_copy": {"headline": "把成本算清", "items": ["再扩张"]},
                 "camera_intent": "slow_pull_115", "broll_policy": "lock_aroll", "sfx_budget": 1},
            ],
            "music_cues": [{"type": "silence_drop", "start_sec": 6.45, "end_sec": 6.85, "depth_db": -22.0, "fade_ms": 60, "reason": "pain drop"}],
        }
        val = dp.validate_director_plan(plan, ae, profile)
        self.assertTrue(val["ok"], val["errors"])
        self.assertEqual(val["sanitized_plan"]["acts"][1]["broll_keywords"], ["走廊"])

        stage = kc.plan_kinetic_stage(ae, style_id="style_02", director_plan=val["sanitized_plan"], composition_profile=profile)
        self.assertEqual(stage["theme_profile"], "style_02_unified")
        items = stage["items"]
        self.assertTrue(all("serif" not in str((it.get("font_candidates") or [""])[0]).lower() for it in items))
        # opening_triad 后两阶滞后发声时间防剧透（过滤自动补全下方未呈现短句 bridge_bar）
        open_items = [it for it in items if it.get("group_id") == "v6_01" and it.get("variant") != "bridge_bar"]
        self.assertEqual([it["variant"] for it in open_items], ["hero_white", "hero_lime", "red_pill"])
        self.assertGreater(open_items[1]["start_sec"], open_items[0]["start_sec"])
        self.assertGreater(open_items[2]["start_sec"], open_items[1]["start_sec"])
        # aroll_b 的人脸框 y=0.18 (<0.27)，联合包络检测到头顶不足以放人后弹幕 → 安全降级前景而非切耳朵
        act3_items = [it for it in items if it.get("group_id") == "v6_03"]
        self.assertFalse(any(it.get("z_layer") == "behind_person" for it in act3_items), act3_items)
        # list_gather_depth 前两项带压暗退隐且全部在同一幕 end_sec 清场
        act4_rows = [it for it in items if it.get("group_id") == "v6_04" and it.get("track_lane", 0) >= 2]
        self.assertTrue(act4_rows[0].get("dim_after_sec") is not None)
        self.assertTrue(all(abs(float(it["end_sec"]) - 14.5) < 0.05 for it in act4_rows))
        # bridge_bar L1 字号为 ~69px 且 PNG 渲染也保持 L1 >= 60px、inline_rich 无截断完整保留末字
        bridge_it = next(it for it in items if it.get("variant") == "bridge_bar")
        self.assertEqual(bridge_it["font_size_units"], 11.5)
        b_im = rto.render_kinetic_stage_item_png(bridge_it, font_candidates=bridge_it["font_candidates"])
        self.assertIsNotNone(b_im)
        self.assertGreaterEqual(b_im.height, 70)
        inline_it = next(it for it in items if it.get("variant") == "inline_rich")
        i_im = rto.render_kinetic_stage_item_png(inline_it, font_candidates=inline_it["font_candidates"])
        self.assertIsNotNone(i_im)
        self.assertGreater(i_im.width, 300)

    def test_step4_store_director_cameras_jump_closeup_and_broll_keywords(self):
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            a_mp = str(pdir / "talk.mp4")
            b1_mp = str(pdir / "xiajiao.mp4")
            b2_mp = str(pdir / "paigu.mp4")
            sents = [
                {"id": "s1", "text": "今天带大家来看一间招牌茶楼", "start_sec": 0.0, "end_sec": 3.0, "start_frame": 0, "end_frame": 90,
                 "source_media": a_mp, "source_in_sec": 0.0, "source_out_sec": 3.0},
                {"id": "s2", "text": "呢个点心非常地道好食", "start_sec": 3.1, "end_sec": 6.2, "start_frame": 93, "end_frame": 186,
                 "source_media": a_mp, "source_in_sec": 3.1, "source_out_sec": 6.2},
                {"id": "s3", "text": "真系试过就会返寻味", "start_sec": 6.3, "end_sec": 9.5, "start_frame": 189, "end_frame": 285,
                 "source_media": a_mp, "source_in_sec": 6.3, "source_out_sec": 9.5},
            ]
            evts = [
                {"id": f"e{i}", "type": "sentence_start", "time_sec": s["start_sec"], "frame": s["start_frame"], "sentence_id": s["id"]}
                for i, s in enumerate(sents, 1)
            ]
            write_json(pdir / "03_audio_events.json", {"locked_duration_sec": 9.5, "locked_duration_frames": 285, "sentences": sents, "events": evts})
            write_json(pdir / "01_manifest.json", {"items": [
                {"id": "a1", "role": "a_roll", "video_grade": "usable", "normalized_mp4": a_mp, "duration_sec": 12.0,
                 "sharpness_score": 450.0, "stability_score": 0.92, "faces": [{"bbox": {"x": 0.3, "y": 0.2, "w": 0.35, "h": 0.32}}]},
                {"id": "b1", "role": "b_roll", "video_grade": "usable", "normalized_mp4": b1_mp, "duration_sec": 6.0,
                 "description": "手工现包晶莹剔透鲜虾饺皇", "semantic_tags": ["虾饺", "点心"], "sharpness_score": 400.0, "stability_score": 0.9},
                {"id": "b2", "role": "b_roll", "video_grade": "usable", "normalized_mp4": b2_mp, "duration_sec": 6.0,
                 "description": "空茶杯特写", "semantic_tags": ["茶杯"], "sharpness_score": 400.0, "stability_score": 0.9},
            ]})
            write_json(pdir / "01b_composition_profile.json", {"primary_stage_mode": "wide_behind_person", "allow_behind_person": True})
            write_json(pdir / "03b_director_plan.json", {
                "step": "03b_director_plan", "source": "agent", "style_id": "style_02", "theme_profile": "style_02_unified",
                "acts": [
                    {"act_id": "act_01", "sentence_ids": ["s1"], "dramatic_role": "hook", "emotion_intensity": 0.9,
                     "primitive": "opening_triad", "visual_copy": {"headline": "招牌茶楼"},
                     "camera_intent": "jump_closeup", "broll_policy": "lock_aroll", "sfx_budget": 1},
                    {"act_id": "act_02", "sentence_ids": ["s2"], "dramatic_role": "proof", "emotion_intensity": 0.7,
                     "primitive": "plain_bridge", "visual_copy": {},
                     "camera_intent": "steady_hold", "broll_policy": "allow_verified_broll", "broll_keywords": ["虾饺"], "sfx_budget": 0},
                    {"act_id": "act_03", "sentence_ids": ["s3"], "dramatic_role": "cta", "emotion_intensity": 0.8,
                     "primitive": "inline_highlight_cta", "visual_copy": {"headline": "返寻味"},
                     "camera_intent": "slow_push_115", "broll_policy": "lock_aroll", "sfx_budget": 1},
                ],
                "music_cues": [],
            })
            edl = s4.run_step4(pdir, track="store", edit_profile="talking", style_id="style_02")
            clips = edl["clips"]
            a_clips = [c for c in clips if c["track_role"] == "a_roll"]
            b_clips = [c for c in clips if c["track_role"] == "b_roll"]
            self.assertTrue(any(c.get("camera_intent") == "jump_closeup" and abs(float(c.get("punch_in_zoom", 1.0)) - 1.22) < 0.01 and c.get("vf_extra") for c in a_clips), a_clips)
            self.assertTrue(any(b["media_path"] == b1_mp for b in b_clips), b_clips)

    def test_build_director_tasks_creates_director_prompt_md(self):
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            ae = _make_audio_store_and_biz()
            write_json(pdir / "03_audio_events.json", ae)
            write_json(pdir / "00_visual_index.json", {"clips": [
                {"filename": "shrimp.mov", "role": "b_roll", "duration_sec": 4.2, "description": "手工虾饺特写", "tags": ["虾饺"]},
            ]})
            dp.build_director_tasks(pdir, style_id="style_02")
            md = (pdir / "03b_director_prompt.md").read_text(encoding="utf-8")
            self.assertIn("总导演编排任务包", md)
            self.assertIn("style_02_unified", md)
            self.assertIn("shrimp.mov", md)
            self.assertIn("2024年实体工厂老板", md)

    def test_qa_inspector_double_caption_typography_and_camera_realized_guards(self):
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vpath = pdir / "out.mp4"
            vpath.write_bytes(b"fake")
            ae = {
                "locked_duration_sec": 6.0,
                "sentences": [
                    {"id": "s1", "text": "2024年实体老板做起来", "start_sec": 0.0, "end_sec": 3.0},
                    {"id": "s2", "text": "顺便看看走廊环境", "start_sec": 3.0, "end_sec": 6.0},
                ],
                "events": [
                    {"id": "e1", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "s1"},
                    {"id": "e2", "type": "sentence_start", "time_sec": 3.0, "frame": 90, "sentence_id": "s2"},
                ],
            }
            edl = {
                "fps": 30, "total_duration_sec": 6.0,
                "clips": [
                    {"clip_id": "c1", "track_role": "a_roll", "duration_sec": 3.0, "source_in_sec": 0.0, "source_out_sec": 3.0,
                     "timeline_in_sec": 0.0, "timeline_out_sec": 3.0, "timeline_in_frame": 0, "timeline_out_frame": 90,
                     "cut_event_id": "e1", "vf_extra": "scale=iw*1.22:-1"},
                    {"clip_id": "c2", "track_role": "a_roll", "duration_sec": 3.0, "source_in_sec": 3.0, "source_out_sec": 6.0,
                     "timeline_in_sec": 3.0, "timeline_out_sec": 6.0, "timeline_in_frame": 90, "timeline_out_frame": 180,
                     "cut_event_id": "e2", "vf_extra": ""},
                ],
            }
            subs = {"template_id": "style_02", "suppressed_count": 1, "entries": [
                {"id": "sub_1", "text": "2024年实体老板做起来", "start_sec": 0.0, "end_sec": 3.0, "suppressed": True, "suppress_reason": "kinetic_typography_stage"},
            ]}
            pkg_good = {
                "style_id": "style_02",
                "sfx_cues": [],
                "kinetic_stage": {
                    "enabled": True, "theme_profile": "style_02_unified",
                    "director_acts": [{"act_id": "a1", "start_sec": 0.0, "end_sec": 3.0, "camera_intent": "jump_closeup"}],
                    "items": [
                        {"id": "k1", "start_sec": 0.0, "end_sec": 3.0, "variant": "hero_lime", "z_layer": "foreground", "font_size_px": 135, "font_candidates": ["SourceHanSansCN-Bold.otf"]},
                        {"id": "k2", "start_sec": 3.0, "end_sec": 6.0, "variant": "bridge_bar", "z_layer": "foreground", "font_size_px": 69, "font_candidates": ["SourceHanSansCN-Bold.otf"]},
                    ],
                },
            }
            write_json(pdir / "03_audio_events.json", ae)
            write_json(pdir / "04_edl.json", edl)
            write_json(pdir / "05_subtitles.json", subs)
            write_json(pdir / "06_packaging.json", pkg_good)

            with mock.patch.object(
                qa, "ffprobe_media",
                return_value={
                    "format": {"duration": "6.0"},
                    "streams": [
                        {"codec_type": "video", "width": 1080, "height": 1920, "r_frame_rate": "30/1", "duration": "6.0"},
                        {"codec_type": "audio", "duration": "6.0"},
                    ],
                },
            ), \
                 mock.patch.object(qa, "detect_black_intervals", return_value=("PASS", [])), \
                 mock.patch.object(qa, "evaluate_cut_boundary_cleanliness", return_value=qa._mk_check("cut_boundary_clean", "切点干净", "PASS", "ok", "ok")), \
                 mock.patch.object(iv, "extract_audio_context_once", return_value={"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}), \
                 mock.patch.object(iv, "run_blackbox_verification", return_value={"checks": []}):
                rep = qa.inspect_video(pdir, vpath)
                by_id = {c["id"]: c for c in rep["checks"]}
                self.assertEqual(by_id["double_caption_overlap"]["status"], "PASS", by_id["double_caption_overlap"])
                self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", by_id["style_typography_unity"])
                self.assertEqual(by_id["camera_intent_realized"]["status"], "PASS", by_id["camera_intent_realized"])

                # 破坏：加回底字幕 + 混入暗金小卡 + 清除 vf_extra → 应全数 FAIL 拦截
                pkg_bad = json.loads(json.dumps(pkg_good))
                pkg_bad["kinetic_stage"]["items"].append({"id": "k_cap", "start_sec": 0.0, "end_sec": 3.0, "variant": "bridge_bar", "base_caption": True, "font_size_px": 51})
                pkg_bad["kinetic_stage"]["items"].append({"id": "k_card", "start_sec": 0.5, "end_sec": 2.0, "variant": "concept_card", "z_layer": "foreground", "font_size_px": 90})
                edl_bad = json.loads(json.dumps(edl))
                edl_bad["clips"][0]["vf_extra"] = ""
                write_json(pdir / "06_packaging.json", pkg_bad)
                write_json(pdir / "04_edl.json", edl_bad)
                rep_bad = qa.inspect_video(pdir, vpath)
                bad_id = {c["id"]: c for c in rep_bad["checks"]}
                self.assertEqual(bad_id["double_caption_overlap"]["status"], "FAIL", bad_id["double_caption_overlap"])
                self.assertEqual(bad_id["style_typography_unity"]["status"], "FAIL", bad_id["style_typography_unity"])
                self.assertEqual(bad_id["camera_intent_realized"]["status"], "FAIL", bad_id["camera_intent_realized"])


if __name__ == "__main__":
    unittest.main()
