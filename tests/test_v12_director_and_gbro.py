#!/usr/bin/env python3
"""V12 通用大模型导演规划协议、v11_director_planner 废除门禁、M1/M2 边界修复与 gbro-cover-design 10风格多图封面回归单测。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
DEV_TOOLS = ROOT / "dev_tools"
for _p in (str(SCRIPTS), str(DEV_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import director_plan  # noqa: E402
import run_pipeline  # noqa: E402
import step9_title_cover as s9  # noqa: E402
import v11_director_planner as v11dp  # noqa: E402
from common import detect_active_agent, find_bin, read_json, write_json  # noqa: E402


def _make_audio(rows, total=None):
    return {
        "locked_duration_sec": total if total is not None else rows[-1][1] + 0.5,
        "sentences": [
            {"id": f"u_{i:03d}", "text": txt, "start_sec": st, "end_sec": en, "keywords": kws}
            for i, (st, en, txt, kws) in enumerate(rows, 1)
        ],
    }


def _make_solid_mp4(path: Path, rgb: tuple[int, int, int], dur_sec: float = 1.6) -> Path:
    ffmpeg = find_bin("ffmpeg")
    assert ffmpeg, "ffmpeg binary required for tests"
    path.parent.mkdir(parents=True, exist_ok=True)
    color = f"0x{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=540x960:r=15:d={dur_sec}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path


class V12DirectorAndGbroTests(unittest.TestCase):
    def test_f13_abolition_zero_biz_sto_tables_and_frame8_markers(self) -> None:
        abolished = (
            "BIZ_" + "SEMANTIC_SUBTITLES",
            "STO_" + "SEMANTIC_SUBTITLES",
            "BIZ_" + "SEMANTIC_BLOCKS",
            "STO_" + "SEMANTIC_BLOCKS",
            "_style_" + "cover_copy",
        )
        frame8_markers = ("夫妻店到" + "雇人断层", "01/02" + "/03")
        targets = [
            SCRIPTS / "director_plan.py",
            SCRIPTS / "step9_title_cover.py",
            SCRIPTS / "run_pipeline.py",
            DEV_TOOLS / "v11_director_planner.py",
        ]
        for fp in targets:
            txt = fp.read_text(encoding="utf-8")
            for sym in abolished:
                self.assertIsNone(re.search(rf"\b{re.escape(sym)}\b", txt), f"{fp.name} 残留废除符号 {sym}")
            for marker in frame8_markers:
                self.assertNotIn(marker, txt, f"{fp.name} 含第8格硬编码标记 {marker}")

    def test_f13_v11_director_planner_cli_and_pipeline_wrapper(self) -> None:
        audio = _make_audio([
            (0.0, 2.8, "社区咖啡馆最怕选址只看人流量", ["社区咖啡馆", "选址"]),
            (3.0, 6.2, "不是位置越大越好而是复购半径要稳", ["复购半径"]),
            (6.5, 9.8, "我们用三个月把单杯损耗压到百分之五", ["百分之五"]),
        ])
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            write_json(pdir / "03_audio_events.json", audio)
            out_json = pdir / "custom_plan.json"
            res = subprocess.run(
                [
                    sys.executable, str(DEV_TOOLS / "v11_director_planner.py"),
                    "--pipeline-dir", str(pdir), "--style-id", "style_15", "--out", str(out_json),
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(out_json.is_file())
            doc = read_json(out_json)
            self.assertTrue(doc["native_style_mode"])
            self.assertEqual(doc["style_id"], "style_15")
            self.assertTrue(director_plan.validate_director_plan(doc, audio)["ok"])

            plan2 = v11dp.build_director_plan_for_pipeline(pdir, style_id="style_24", write_plan=True)
            self.assertTrue((pdir / "03b_director_plan.json").is_file())
            self.assertEqual(plan2["style_id"], "style_24")

    def test_f14_unseen_blind_24_styles_parity_and_determinism(self) -> None:
        blind_audio = _make_audio([
            (0.0, 3.2, "做旧房局改最怕客户被增项吓跑", ["旧房局改", "增项"]),
            (3.5, 6.8, "不是材料多贵而是工序没有提前锁死", ["工序"]),
            (7.1, 10.5, "第一，进场前把水电点位一次性确认", ["水电点位"]),
            (10.8, 14.2, "第二，基层空鼓必须在批腻子前铲净", ["基层空鼓"]),
            (14.5, 18.0, "第三，墙面做两遍底漆加净味面漆", ["净味面漆"]),
            (18.3, 22.0, "整套标准流程只要三天就能交付入住", ["三天", "交付"]),
            (22.4, 26.2, "所以业主才敢把老房子放心交给我们", ["老房子"]),
            (26.5, 30.0, "想看同款旧墙翻新清单可以评论区留言", ["评论区"]),
        ], total=30.5)
        blind_inv = {
            "clips": [
                {"filename": "wall_sand.mov", "suggested_role": "b_roll_candidate", "description": "师傅打磨老旧墙面基层", "tags": ["旧房局改", "基层"]},
                {"filename": "paint_roll.mov", "suggested_role": "b_roll_candidate", "description": "滚刷净味环保面漆上墙", "tags": ["净味面漆", "翻新"]},
            ]
        }

        for idx in range(1, 25):
            sid = f"style_{idx:02d}"
            p1 = director_plan.generate_deterministic_director_plan(blind_audio, style_id=sid, inventory=blind_inv)
            p2 = director_plan.generate_deterministic_director_plan(blind_audio, style_id=sid, inventory=blind_inv)
            self.assertEqual(p1, p2, f"{sid} 非确定性")
            self.assertEqual(p1["native_style_mode"], sid != "style_02")
            chk = director_plan.validate_director_plan(p1, blind_audio)
            self.assertTrue(chk["ok"], f"{sid} 校验失败: {chk['errors']}")
            self.assertIn(p1["gbro_cover_spec"]["gbro_style"], director_plan.GBRO_STYLES)
            self.assertTrue(p1["gbro_cover_spec"]["dish_or_subject"])
            self.assertGreaterEqual(len(p1["semantic_subtitles"]), 8, sid)

            beats = p1["style_scene_beats"]
            if sid in ("style_01", "style_02", "style_06", "style_08"):
                self.assertEqual(beats, [], f"{sid} 必须保持原生无面板")
            elif sid == "style_15":
                self.assertTrue(any(b["layout"]["mode"] == "paper_outline_pip" for b in beats))
            elif sid == "style_24":
                self.assertTrue(any(b["layout"]["mode"] == "review_whiteboard_pip" for b in beats))
            else:
                self.assertTrue(beats, f"{sid} 缺少 style_scene_beats")

            # callout 与 beat 零碰撞
            beat_wins = [(b["target_sec"], b["target_sec"] + b["target_dur"]) for b in beats]
            for co in p1["style_callouts"]:
                self.assertGreaterEqual(co["end_sec"] - co["start_sec"], 1.5, (sid, co))
                for bs, be in beat_wins:
                    self.assertLessEqual(min(co["end_sec"], be) - max(co["start_sec"], bs), 0.25, (sid, co))

    def test_f14_input_sensitivity_across_unseen_scripts(self) -> None:
        a1 = _make_audio([
            (0.0, 3.0, "做宠物烘焙最容易忽略鲜肉配比", ["宠物烘焙"]),
            (3.2, 6.5, "我们坚持每天现烤鸡胸肉冻干", ["鸡胸肉"]),
        ])
        a2 = _make_audio([
            (0.0, 3.0, "户外露营电源核心看磷酸铁锂电芯", ["磷酸铁锂"]),
            (3.2, 6.5, "两千瓦功率连带电磁炉都不跳闸", ["两千瓦"]),
        ])
        p1 = director_plan.generate_deterministic_director_plan(a1, style_id="style_09")
        p2 = director_plan.generate_deterministic_director_plan(a2, style_id="style_09")
        self.assertNotEqual(p1["cover_copy"]["headline_top"], p2["cover_copy"]["headline_top"])
        self.assertNotEqual(p1["gbro_cover_spec"]["dish_or_subject"], p2["gbro_cover_spec"]["dish_or_subject"])
        self.assertNotEqual(p1["semantic_subtitles"], p2["semantic_subtitles"])
        self.assertNotEqual(p1["style_scene_beats"][0]["layout"]["panel_title"], p2["style_scene_beats"][0]["layout"]["panel_title"])

    def test_f14_m1_phrase_boundary_regressions_and_validator(self) -> None:
        # 1) 2.4G 不得被拆成 "G低延迟传输"
        audio_rf = _make_audio([
            (0.0, 3.5, "这款无线麦采用2.4G低延迟传输技术", ["2.4G"]),
            (3.8, 7.5, "室外五十米距离依然稳定不断连", ["五十米"]),
            (7.8, 12.0, "第一，开机自动配对；第二，降噪芯片隔离风声；第三，续航十二小时不断电", ["续航"]),
            (12.4, 16.5, "其实节奏感是练出来的，多录几条自然顺畅", ["节奏感"]),
        ])
        plan = director_plan.generate_deterministic_director_plan(audio_rf, style_id="style_03")
        blob = json.dumps(plan, ensure_ascii=False)
        self.assertIsNone(re.search(r"(?<![0-9.])G低延迟", blob), blob)
        self.assertNotIn("24G", blob)
        self.assertIsNone(re.search(r"[\"']感是练出来的", blob), blob)
        self.assertNotIn("G低延迟", director_plan._short_conclusion("采用2.4G低延迟传输", []))
        self.assertEqual(director_plan._short_conclusion("其实节奏感是练出来的", []), "练出来的")

        # framework_pillar items 绝不能只输出裸 "03"
        pillars = [a["visual_copy"] for a in plan["acts"] if a["primitive"] == "framework_pillar"]
        for vc in pillars:
            for item in vc.get("items") or []:
                self.assertNotRegex(item.strip(), r"^\d{2}$", f"发现裸序数残片: {item!r}")

        # _validate_copy 拒绝断词 "G低延迟传输"
        bad_act_plan = {
            "theme_profile": "business_editorial",
            "acts": [
                {
                    "sentence_ids": ["u_001"],
                    "primitive": "num_anchor_dim_overlay",
                    "camera_intent": "steady_hold",
                    "visual_copy": {"headline": "G低延迟传输", "items": [], "accent_token": "", "footnote": ""},
                },
                {
                    "sentence_ids": ["u_002", "u_003", "u_004"],
                    "primitive": "plain_bridge",
                    "camera_intent": "steady_hold",
                    "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                },
            ],
            "music_cues": [],
        }
        res_bad = director_plan.validate_director_plan(bad_act_plan, audio_rf)
        self.assertFalse(res_bad["ok"])
        self.assertTrue(any("hallucinated_token" in e for e in res_bad["errors"]))

        # validate_director_plan 拦截 invalid gbro_style / Frame-8 / callout-beat overlap
        overlap_plan = dict(plan)
        overlap_plan["cover_copy"] = {**plan["cover_copy"], "gbro_style": "style-99"}
        overlap_plan["style_callouts"] = [
            {"text": "测试碰撞", "start_sec": plan["style_scene_beats"][0]["target_sec"], "end_sec": plan["style_scene_beats"][0]["target_sec"] + 1.8}
        ]
        res_ov = director_plan.validate_director_plan(overlap_plan, audio_rf)
        self.assertFalse(res_ov["ok"])
        self.assertTrue(any("gbro_style" in e for e in res_ov["errors"]))
        self.assertTrue(any("重叠" in e for e in res_ov["errors"]))

    def test_f14_build_director_tasks_and_run_pipeline_native_routing(self) -> None:
        audio = _make_audio([
            (0.0, 3.0, "开一家社区烘培店先算清楚房租占比", ["房租占比"]),
            (3.2, 6.4, "超过营业额两成就会压垮利润", ["利润"]),
        ])
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            write_json(pdir / "03_audio_events.json", audio)
            write_json(pdir / "00_inventory.json", {
                "clips": [{"filename": "oven.mov", "suggested_role": "b_roll_candidate", "description": "面包出炉", "tags": ["烘培"]}]
            })
            tasks_p = director_plan.build_director_tasks(pdir, style_id="style_15")
            tasks = read_json(tasks_p)
            self.assertTrue(tasks["native_style_mode"])
            self.assertEqual(len(tasks["gbro_styles"]), 10)
            self.assertEqual(tasks["style_visual_truth"]["family"], "F6_cinema_pinyin_interview")
            prompt_md = (pdir / "03b_director_prompt.md").read_text(encoding="utf-8")
            self.assertIn("style_scene_beats", prompt_md)
            self.assertIn("gbro_cover_spec", prompt_md)

            # 测试 _prepare_v6_director 透传 inventory/visual_index 与 native_style_mode
            ext_plan = director_plan.generate_deterministic_director_plan(audio, style_id="style_09")
            ext_p = pdir / "ext_plan.json"
            write_json(ext_p, ext_plan)
            info = run_pipeline._prepare_v6_director(pdir, "style_09", str(ext_p), False, False)
            self.assertTrue(info["native_style_mode"])

            # 验证 run_visual_to_delivery 在 native_style_mode=True + --director-plan 时不强开 enable_v6_director
            captured = {}
            with (
                mock.patch.object(run_pipeline, "run_step4", side_effect=lambda d, **kw: captured.setdefault("step4", kw)),
                mock.patch.object(run_pipeline, "run_step5", side_effect=lambda d, **kw: captured.setdefault("step5", kw)),
                mock.patch.object(run_pipeline, "run_step6", side_effect=lambda d, **kw: captured.setdefault("step6", kw)),
                mock.patch.object(run_pipeline, "write_source_review"),
                mock.patch.object(run_pipeline, "run_step7"),
                mock.patch.object(run_pipeline, "_enforce_style_lock"),
                mock.patch.object(run_pipeline, "run_step8"),
                mock.patch.object(run_pipeline, "run_step9"),
                mock.patch.object(run_pipeline, "render_mp4", return_value=pdir / "prev.mp4"),
            ):
                run_pipeline.run_visual_to_delivery(
                    pdir, track="store", j_cut_frames=2, subtitle_template=None, lut_name=None,
                    prefer_broll_only=False, benchmark=None, simulate_jy_fail=False,
                    preview_only=True, style_id="style_09", director_plan=str(ext_p),
                )
            self.assertNotIn("enable_v6_director", captured["step4"])
            self.assertFalse(captured["step5"]["enable_v6_director"])
            self.assertNotIn("enable_v6_director", captured["step6"])

    def test_f15_gbro_catalog_all_10_styles_prompt_contracts(self) -> None:
        self.assertEqual(sorted(s9._GBRO_STYLE_CATALOG.keys()), [f"style-{i:02d}" for i in range(1, 11)])
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll = _make_solid_mp4(pdir / "aroll.mp4", (40, 60, 90), 1.2)
            broll = _make_solid_mp4(pdir / "broll_dish.mp4", (210, 120, 40), 1.2)
            write_json(pdir / "01_manifest.json", {
                "items": [
                    {"id": "a1", "role": "a_roll", "normalized_mp4": str(aroll), "duration_sec": 1.2, "video_grade": "usable"},
                    {"id": "b1", "role": "b_roll", "normalized_mp4": str(broll), "duration_sec": 1.2, "video_grade": "usable", "semantic_tags": ["脆皮乳鸽"]},
                ]
            })
            for gstyle in [f"style-{i:02d}" for i in range(1, 11)]:
                hf = s9.build_gbro_cover_handoff(
                    pdir,
                    title_main="现烤脆皮乳鸽",
                    title_sub="外酥里嫩",
                    hook_text="每日现烤脆皮乳鸽",
                    track="store",
                    gbro_style=gstyle,
                    dish_or_subject="脆皮乳鸽",
                )
                self.assertIsNotNone(hf, gstyle)
                self.assertEqual(hf["aspect_ratio"], "3:4")
                self.assertEqual(hf["recommended_style_id"], gstyle)
                self.assertTrue(hf["figure_1_face_ref"])
                self.assertTrue(hf["figure_2_content_refs"])
                pt = hf["prompt_text"]
                lines = [ln.strip() for ln in pt.splitlines() if ln.strip()]
                self.assertTrue(lines[0].startswith("3:4 竖版构图"), gstyle)
                self.assertIn("10%", pt, gstyle)
                self.assertIn("图1", pt, gstyle)
                for banned in ("全息投影", "粒子效果", "发光特效"):
                    self.assertNotIn(banned, pt, gstyle)
                if gstyle == "style-03":
                    self.assertIn("图2", pt)
                if gstyle == "style-08":
                    self.assertTrue("背影" in pt or "背对镜头" in pt)
                    self.assertNotIn("正脸直视", pt)
                    self.assertNotIn("五官清晰正面", pt)
                if gstyle == "style-02":
                    self.assertNotIn("渐变", pt)
                if gstyle == "style-05":
                    self.assertIn("留白", pt)
                    self.assertNotIn("超粗", pt)
                if gstyle == "style-10":
                    self.assertIn("不遮挡眼睛和嘴巴", pt)

    def test_f15_extract_content_refs_subject_match_diversity_and_single_cam_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll = _make_solid_mp4(pdir / "aroll.mp4", (30, 30, 35), 1.0)
            goose = _make_solid_mp4(pdir / "norm_01_goose.mp4", (220, 110, 30), 1.0)
            shrimp = _make_solid_mp4(pdir / "norm_02_shrimp.mp4", (235, 215, 195), 1.0)
            steam = _make_solid_mp4(pdir / "norm_03_chicken.mp4", (90, 150, 80), 1.0)
            write_json(pdir / "01_manifest.json", {
                "items": [
                    {"id": "a1", "role": "a_roll", "normalized_mp4": str(aroll), "duration_sec": 1.0, "video_grade": "usable", "faces": [{"x": 0.3, "y": 0.2, "w": 0.3, "h": 0.3}]},
                    {"id": "b1", "role": "b_roll", "normalized_mp4": str(goose), "duration_sec": 1.0, "sharpness_score": 80, "video_grade": "usable"},
                    {"id": "b2", "role": "b_roll", "normalized_mp4": str(shrimp), "duration_sec": 1.0, "sharpness_score": 80, "video_grade": "usable"},
                    {"id": "b3", "role": "b_roll", "normalized_mp4": str(steam), "duration_sec": 1.0, "sharpness_score": 80, "video_grade": "usable"},
                ]
            })
            write_json(pdir / "00_visual_index.json", {
                "clips": [
                    {"filename": "goose.mp4", "description": "老广古法脆皮烧鹅切盘", "semantic_tags": ["烧鹅", "烤鹅"]},
                    {"filename": "shrimp.mp4", "description": "水晶虾饺热气出笼", "semantic_tags": ["虾饺", "早茶"]},
                    {"filename": "chicken.mp4", "description": "桑拿走地鸡原汁原味", "semantic_tags": ["桑拿鸡", "走地鸡"]},
                ]
            })
            write_json(pdir / "04_edl.json", {
                "clips": [
                    {"clip_id": "c1", "track_role": "a_roll", "media_path": str(aroll), "source_in_sec": 0.0, "source_out_sec": 1.0},
                    {"clip_id": "c2", "track_role": "b_roll", "media_path": str(goose), "source_in_sec": 0.0, "source_out_sec": 1.0},
                    {"clip_id": "c3", "track_role": "b_roll", "media_path": str(shrimp), "source_in_sec": 0.0, "source_out_sec": 1.0},
                    {"clip_id": "c4", "track_role": "b_roll", "media_path": str(steam), "source_in_sec": 0.0, "source_out_sec": 1.0},
                ]
            })

            r_goose = s9.extract_content_ref_frames(pdir, subject_hint="古法烧鹅")
            self.assertEqual(Path(r_goose[0]["source_media"]).name, "norm_01_goose.mp4")

            r_shrimp = s9.extract_content_ref_frames(pdir, subject_hint="水晶虾饺")
            self.assertEqual(Path(r_shrimp[0]["source_media"]).name, "norm_02_shrimp.mp4")

            # 跨 style_id 多样性验证
            picked_sources = set()
            for sid in ("style_01", "style_02", "style_03"):
                refs = s9.extract_content_ref_frames(pdir, subject_hint="", style_id=sid)
                self.assertTrue(refs)
                picked_sources.add(Path(refs[0]["source_media"]).name)
            self.assertGreaterEqual(len(picked_sources), 3)

            # 纯单机口播无 B-roll 优雅降级 + style-03 自动回退
            p_single = pdir / "single_cam"
            p_single.mkdir()
            write_json(p_single / "01_manifest.json", {
                "items": [{"id": "a1", "role": "a_roll", "normalized_mp4": str(aroll), "duration_sec": 1.0, "video_grade": "usable"}]
            })
            self.assertEqual(s9.extract_content_ref_frames(p_single), [])
            hf_fb = s9.build_gbro_cover_handoff(
                p_single, title_main="单机口播复盘", title_sub="核心真相", hook_text="口播复盘",
                track="creator", gbro_style="style-03",
            )
            self.assertEqual(hf_fb["style_fallback_from"], "style-03")
            self.assertEqual(hf_fb["recommended_style_id"], "style-10")
            self.assertEqual(hf_fb["figure_2_content_refs"], [])

    def test_f15_run_step9_handoff_and_i2i_passthrough_and_v30_compat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll = _make_solid_mp4(pdir / "aroll.mp4", (45, 55, 70), 1.5)
            broll = _make_solid_mp4(pdir / "broll_tea.mp4", (200, 140, 60), 1.5)
            audio = _make_audio([(0.0, 1.5, "招牌手工茶点每日现做", ["手工茶点"])], total=1.8)
            write_json(pdir / "03_audio_events.json", audio)
            write_json(pdir / "01_manifest.json", {
                "items": [
                    {"id": "a1", "role": "a_roll", "normalized_mp4": str(aroll), "duration_sec": 1.5, "video_grade": "usable"},
                    {"id": "b1", "role": "b_roll", "normalized_mp4": str(broll), "duration_sec": 1.5, "video_grade": "usable", "semantic_tags": ["手工茶点"]},
                ]
            })
            write_json(pdir / "04_edl.json", {
                "style_id": "style_03",
                "clips": [
                    {"clip_id": "c1", "track_role": "a_roll", "media_path": str(aroll), "source_in_sec": 0.0, "source_out_sec": 0.8, "timeline_in_sec": 0.0, "timeline_out_sec": 0.8},
                    {"clip_id": "c2", "track_role": "b_roll", "media_path": str(broll), "source_in_sec": 0.0, "source_out_sec": 0.7, "timeline_in_sec": 0.8, "timeline_out_sec": 1.5},
                ],
            })
            plan = director_plan.generate_deterministic_director_plan(audio, style_id="style_03")
            write_json(pdir / "03b_director_plan.json", plan)

            res1 = s9.run_step9(pdir, track="store", style_id="style_03")
            hf = res1.get("gbro_cover_handoff") or {}
            self.assertEqual(hf.get("aspect_ratio"), "3:4")
            self.assertTrue(hf.get("figure_1_face_ref"))
            self.assertTrue(hf.get("figure_2_content_refs"))
            self.assertTrue((pdir / "09_gbro_cover_handoff.json").is_file())
            self.assertNotIn("Cola 专属", (pdir / "09_gbro_cover_prompt.md").read_text(encoding="utf-8"))

            # 放入合成 09_gbro_i2i_cover_3x4.png 后重跑验证直出
            Image.new("RGB", (900, 1200), (28, 32, 42)).save(pdir / "09_gbro_i2i_cover_3x4.png")
            res2 = s9.run_step9(pdir, track="store", style_id="style_03")
            self.assertEqual(res2["cover_mode"], "gbro_i2i_generated")
            with Image.open(pdir / "09_cover_3x4.jpg") as im34:
                self.assertEqual(im34.size, (1080, 1440))
            with Image.open(pdir / "09_cover.jpg") as im916:
                self.assertEqual(im916.size, (1080, 1920))

            # 旧签名向后兼容
            compat_hf = s9.build_gbro_cover_handoff(
                pdir, title_main="测试标题", title_sub="副标题", hook_text="测试钩子",
                track="store", agent_info=detect_active_agent("antigravity"),
            )
            self.assertIsNotNone(compat_hf)

    def test_f14_challenger_m2_mixed_alnum_compound_boundaries_and_dirty_keywords(self) -> None:
        # 1) 混合字母数字/连字符/小数点原子词（Type-C / 2.4G低延迟传输 / USB3.2 / H.265 / 600平）不得被劈碎
        mixed_rows = [
            (0.0, 4.0, "很多人只看5G网络，其实2.4G低延迟传输才关键。", []),
            (4.2, 8.5, "我哋喺600平展厅实测过，成本省咗200万。", []),
            (8.7, 13.5, "记住，第一点先把Type-C快充协议对齐，第二点用H.265编码压缩码率，第三点标准化门店复购流程。", []),
            (13.8, 18.0, "呢台设备支持2.4G低延迟传输同埋Type-C接口，仲有USB3.2接口。", []),
            (18.2, 21.5, "其实节奏感是练出来的，颗粒度是关键，仪式感很重要。", []),
        ]
        audio_mixed = _make_audio(mixed_rows, total=22.0)
        for s in audio_mixed["sentences"]:
            t = s["text"]
            dur = (s["end_sec"] - s["start_sec"]) / max(1, len(t))
            s["words"] = [
                {"word": ch, "start_sec": round(s["start_sec"] + i * dur, 3), "end_sec": round(s["start_sec"] + (i + 1) * dur, 3)}
                for i, ch in enumerate(t)
            ]

        for sid in ("style_04", "style_15", "style_24"):
            plan = director_plan.generate_deterministic_director_plan(audio_mixed, style_id=sid)
            blob = json.dumps(plan, ensure_ascii=False)
            self.assertIsNone(re.search(r"Type-(?!C)", blob), (sid, blob))
            self.assertIsNone(re.search(r"Type(?![-\w])", blob), (sid, blob))
            self.assertIsNone(re.search(r"其实2\.4G低(?!延迟)", blob), (sid, blob))
            for sub in plan["semantic_subtitles"]:
                self.assertFalse(sub["text"].endswith("2.4G低"), (sid, sub))
                self.assertFalse(sub["text"].startswith("延迟"), (sid, sub))
            for co in plan["style_callouts"]:
                self.assertFalse(co["text"].endswith(("低", "Type", "Type-", "的", "从")), (sid, co))
            for b in plan["style_scene_beats"]:
                lay = b["layout"]
                for txt_val in (lay.get("panel_title"), lay.get("giant_back_text")):
                    if txt_val:
                        self.assertNotIn(txt_val, ("Type", "Type-", "记住", "第一", "实测过"), (sid, lay))
                for pl in (lay.get("panel_lines") or []):
                    self.assertFalse(str(pl).endswith(("Type", "Type-", "实", "2.4G低")), (sid, pl))
                for it in (lay.get("panel_items") or []):
                    it_txt = str(it.get("text") or it.get("label") or "")
                    self.assertFalse(it_txt.endswith(("Type", "Type-", "从", "实", "2.4G低")), (sid, it))
                    self.assertNotIn(it_txt, ("记住", "第一", "实测过"), (sid, it))

        # 2) 脏 keywords 清洗 + 复合词尾边界（管人抄制度）+ 非实体动词子句排除（喺𠮶度供应俾大家）
        dirty_audio = _make_audio([
            (0.0, 3.2, "去年公司破产那年我负债200多万", ["负债多万"]),
            (3.4, 6.6, "删掉公司名改了几个数字就发给门店", ["公司名改", "几个数字"]),
            (6.8, 10.2, "招人找能人管人抄制度，抄最快抄完", ["人抄制度", "快抄完"]),
            (10.5, 14.0, "招牌特色就系朝早做纯手工早茶，鱼池有海鲜喺𠮶度供应俾大家", ["一个", "组嗰边咧"]),
            (14.2, 18.0, "我哋桑拿鸡个鸡就系从山上捉翻嚟嘅，四大天王虾饺最受欢迎", ["鸡个鸡咧", "我哋嘅四大"]),
        ], total=18.5)
        for sid in ("style_04", "style_15", "style_24"):
            plan_d = director_plan.generate_deterministic_director_plan(dirty_audio, style_id=sid)
            short_tokens = [
                str(plan_d["cover_copy"].get(k) or "")
                for k in ("headline_top", "highlight_badge", "sub_line", "dish_or_subject")
            ] + [
                str(plan_d["gbro_cover_spec"].get(k) or "")
                for k in ("dish_or_subject", "headline_top", "highlight_badge", "sub_line")
            ] + [
                kw for sub in plan_d["semantic_subtitles"] for kw in (sub.get("keywords") or [])
            ] + [
                str(co.get("text") or "") for co in plan_d["style_callouts"]
            ] + [
                str(b["layout"].get(k) or "")
                for b in plan_d["style_scene_beats"]
                for k in ("panel_title", "giant_back_text")
            ]
            for act in plan_d["acts"]:
                vc = act.get("visual_copy") or {}
                short_tokens.append(str(vc.get("headline") or ""))
                short_tokens.extend(str(x) for x in (vc.get("items") or []))

            for dirty in ("负债多万", "公司名改", "几个数字", "人抄制度", "快抄完", "组嗰边咧", "鸡个鸡咧", "招人找能人管人抄", "喺𠮶度供应俾大家", "我哋嘅四大"):
                self.assertNotIn(dirty, short_tokens, (sid, dirty, short_tokens))
            for b in plan_d["style_scene_beats"]:
                for it in (b["layout"].get("panel_items") or []):
                    it_txt = str(it.get("text") or it.get("label") or "")
                    self.assertNotIn(it_txt, ("负债多万", "公司名改", "人抄制度", "快抄完", "组嗰边咧", "鸡个鸡咧", "招人找能人管人抄", "喺𠮶度供应俾大家"), (sid, it))
                    self.assertFalse(it_txt.endswith(("从", "抄")), (sid, it))
            dish = plan_d["cover_copy"]["dish_or_subject"]
            self.assertNotIn("供应", dish)
            self.assertNotIn("喺𠮶度", dish)
            self.assertNotIn("负债多万", dish)
            for b in plan_d["style_scene_beats"]:
                for it in (b["layout"].get("panel_items") or []):
                    it_txt = str(it.get("text") or it.get("label") or "")
                    self.assertFalse(it_txt.endswith("从"), (sid, it))
            for co in plan_d["style_callouts"]:
                self.assertFalse(co["text"].endswith("抄"), (sid, co))

        # 3) _snap_phrase_to_boundary 直接检验「招人找能人管人抄制度」不断在「抄」
        snapped = director_plan._snap_phrase_to_boundary("招人找能人管人抄制度", max_chars=8)
        self.assertFalse(snapped.endswith("抄"), snapped)
        self.assertIn("制度", snapped)

    def test_f14_challenger_m2_2_space_polarity_pre_de_and_showcase_boundaries(self) -> None:
        # 1) 'Wi-Fi 7' 空格与完整性、'800V高压平台' 复合词、否定极性保留、代词+动词降权、AA叠词保护
        ev_audio = _make_audio([
            (0.0, 3.8, "今天聊聊为什么800V高压平台这么稳。", ["一个", "800V高"]),
            (4.0, 8.0, "而且这台充电桩支持Wi-Fi 7联动和15分钟快充，成本降了30%。", ["Wi-Fi 7"]),
            (8.2, 12.2, "记住，稳定性是调出来的，不是靠参数表堆出来的。", ["稳定性是", "几个参数"]),
            (12.4, 16.5, "我们主打双锅炉萃取咖啡机，在门店现场免费给大家试喝。", ["大家试喝", "锅炉萃", "门店现"]),
            (16.7, 20.5, "配合9Bar恒压和PID温控，每杯萃取率都很稳。", ["9Bar恒压", "萃取率是"]),
            (20.7, 24.5, "重点不是堆料，而是把控温精度拉满。", ["控温精度"]),
            (24.7, 28.0, "评论区说说你最看重哪一项配置。", ["配置"]),
        ], total=28.0)
        for sid in ("style_02", "style_04", "style_07", "style_12", "style_15", "style_20", "style_24"):
            plan = director_plan.generate_deterministic_director_plan(ev_audio, style_id=sid)
            blob = json.dumps(plan, ensure_ascii=False)
            self.assertNotIn("Wi-Fi7", blob, (sid, blob))
            self.assertIn("Wi-Fi 7", blob, (sid, blob))
            self.assertNotIn("聊聊为什么800V", blob, (sid, blob))
            self.assertNotIn("为什么800V", blob, (sid, blob))
            for sub in plan["semantic_subtitles"]:
                self.assertFalse(sub["text"].endswith("说") and not sub["text"].endswith("说说"), (sid, sub))
                self.assertFalse(sub["text"].startswith("说"), (sid, sub))
            for co in plan["style_callouts"]:
                self.assertNotEqual(co["text"], "为什么800V", (sid, co))
                self.assertNotEqual(co["text"], "大家试喝", (sid, co))
            for b in plan["style_scene_beats"]:
                lay = b["layout"]
                self.assertNotEqual(lay.get("panel_title"), "靠参数表堆出来的", (sid, lay))
                self.assertNotEqual(lay.get("panel_title"), "大家试喝", (sid, lay))
                self.assertNotEqual(lay.get("giant_back_text"), "大家试喝", (sid, lay))
                for pl in (lay.get("panel_lines") or []):
                    self.assertNotIn("桩支持Wi-Fi", str(pl), (sid, pl))
                for it in (lay.get("panel_items") or []):
                    it_txt = str(it.get("text") or it.get("label") or "")
                    self.assertNotEqual(it_txt, "联动和15分钟快充", (sid, it))
                    self.assertNotEqual(it_txt, "靠参数表堆出来的", (sid, it))
            if sid == "style_24":
                beat0_lines = plan["style_scene_beats"][0]["layout"].get("panel_lines") or []
                self.assertTrue(any("Wi-Fi 7" in str(ln) for ln in beat0_lines), beat0_lines)

        # 2) _snap_phrase_to_boundary / _breaks_atomic_or_word_boundary：否定前缀、系词、中分句、'的/嘅' 前截断防护
        self.assertIn("不是", director_plan._snap_phrase_to_boundary("不是靠参数表堆出来的", max_chars=8, allow_plus_two=False))
        self.assertEqual(director_plan._snap_phrase_to_boundary("第一，颗粒度是关键", max_chars=8, allow_plus_two=False), "颗粒度是关键")
        self.assertEqual(director_plan._snap_phrase_to_boundary("还有种我在做事的踏实感", max_chars=10, allow_plus_two=True), "还有种我在做事的踏实感")
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("到时就有好多新鲜", "到时就有好多新鲜嘅海鲜"))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("𠮶边就系我哋", "𠮶边就系我哋嘅厨房"))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("打印出来发给店里", "打印出来发给店里的人"))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("还有种我在做事", "还有种我在做事的踏实感"))
        self.assertFalse(director_plan._breaks_atomic_or_word_boundary("练出来的", "其实节奏感是练出来的"))

        # 3) 逗号界定 _SHOWCASE 提取，避免跨逗号抓取方位词（'花园喺𠮶度'）
        canto_audio = _make_audio([
            (0.0, 4.0, "招牌特色就系每日现烤牛角可颂，门外有花园喺𠮶度欢迎各位街坊。", ["一个", "喺𠮶度"]),
            (4.2, 8.0, "配合800V商用变频烘烤线和Wi-Fi 7低延迟组网，出炉速度提升两倍。", ["组嗰边咧"]),
        ], total=8.5)
        canto_plan = director_plan.generate_deterministic_director_plan(canto_audio, style_id="style_15")
        dish = canto_plan["cover_copy"]["dish_or_subject"]
        self.assertNotIn("花园", dish)
        self.assertNotIn("喺𠮶度", dish)
        self.assertIn(dish, ("牛角", "牛角可颂", "可颂"))

    def test_f14_r4_asr_rebalance_pos_morphology_and_tech_unit_atomicity(self) -> None:
        # 1) 残片零兜底 + 禁止剥离句首「的/嘅/地/得」
        self.assertEqual(
            director_plan._snap_phrase_to_boundary("𠮶边就系我哋嘅", max_chars=11, allow_plus_two=True, prefer_content=True),
            "",
        )
        self.assertNotEqual(
            director_plan._snap_phrase_to_boundary("嘅店厨房你要花三分之一两百平去制造", max_chars=8, allow_plus_two=True, prefer_content=True),
            "店厨房",
        )

        # 2) 2字指示/疑问限定词 '哪一'/'这一'/'每一'、纯汉字量词 '一句'/'一根'、句中虚词/副词 '只顾' 拦截
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("钱从哪一", "钱从哪一环挣出来"))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("这一", "这一环最关键"))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("每一", "每一步都很重要"))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("不如先把一句", "与其把特效加满不如先把一句没有用的废话删干净"))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("这三件事要放在一起想只顾", "观众平台算法这三件事要放在一起想只顾一头就会失衡"))

        # 3) 动补保护（升级完）与非句首 <1字+的/嘅> 拦截（绝不产生 '完的播客录音棚'）
        snapped_studio = director_plan._snap_phrase_to_boundary("大家好，今天带你们看看我刚升级完的播客录音棚。", max_chars=9, allow_plus_two=False)
        self.assertNotEqual(snapped_studio, "完的播客录音棚")
        self.assertFalse(snapped_studio.startswith("完的"))

        # 4) 技术/媒体数字+单位原子跨度与 _numeric_focus 完整性 + 去空格截断关键词拦截
        self.assertEqual(director_plan._numeric_focus("机位我们直接上4K 60帧双机位录制，配一根Type-C供电线就能跑满全天。"), ("4K 60帧", "4K 60帧"))
        self.assertEqual(director_plan._numeric_focus("很多人只盯着麦克风价钱，其实120Hz以下的低频驻波处理才最关键。"), ("120Hz", "120Hz"))
        self.assertEqual(director_plan._numeric_focus("开降噪前后的底噪落差接近30分贝，而且续航能撑12小时。"), ("30分贝", "30分贝"))
        self.assertEqual(director_plan._numeric_focus("支持1080P 120fps慢动作"), ("", ""))
        self.assertEqual(director_plan._numeric_focus("2400万像素全画幅传感器，支持1080P 120fps慢动作和60毫米微距。"), ("2400万", "2400万"))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("USB3", "我们主打多协议扩展坞，支持USB 3.2高速传输。"))
        self.assertEqual(director_plan._clean_sent_keywords({"text": "我们主打多协议扩展坞，支持USB 3.2高速传输。", "keywords": ["USB 3"]}), [])
        s5_txt = "机位我们直接上4K 60帧双机位录制，配一根Type-C供电线就能跑满全天。"
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("4K60", s5_txt))
        self.assertTrue(director_plan._breaks_atomic_or_word_boundary("4K 60帧双", s5_txt))
        self.assertFalse(director_plan._breaks_atomic_or_word_boundary("4K 60帧", s5_txt))
        sn_120 = director_plan._snap_phrase_to_boundary("很多人只盯着麦克风价钱，其实120Hz以下的低频驻波处理才最关键。", max_chars=8, allow_plus_two=True, prefer_content=True)
        self.assertNotIn(sn_120, ("其实120Hz", "以下的低频驻波", "120", ""))

        # 5) 跨句 ASR 碎片重平衡进入封面/面板/花字，且无残片泄漏
        frag_audio = _make_audio([
            (0.0, 4.0, "佢饮茶嘅氛围，我觉得非常之舒服，𠮶边就系我哋嘅", []),
            (4.05, 5.2, "厨房", []),
            (5.5, 9.0, "所以好多人都觉得我好傻一间六百平", []),
            (9.05, 13.0, "嘅店厨房你要花三分之一两百平去制造", []),
            (13.5, 18.0, "𠮶边，就系我哋嘅，鱼池到时就有好多新鲜嘅海鲜喺𠮶度供应俾大家选择", []),
            (18.5, 23.5, "钱从哪一环挣出来，其实最核心系复购", ["环挣出来"]),
        ], total=24.0)
        for sid in ("style_04", "style_15", "style_24"):
            pl_f = director_plan.generate_deterministic_director_plan(frag_audio, style_id=sid)
            pl_chk = json.loads(json.dumps(pl_f, ensure_ascii=False))
            pl_chk.pop("semantic_subtitles", None)
            for b in pl_chk.get("style_scene_beats") or []:
                (b.get("layout") or {}).pop("quote_text", None)
            blob_f = json.dumps(pl_chk, ensure_ascii=False)
            for bad in ('"𠮶边就系我哋"', '"就系我哋"', '"嘅店厨房"', '"店厨房"', '"钱从哪一"'):
                self.assertNotIn(bad, blob_f, (sid, bad, blob_f))

        # 6) 自然未知声学播客全链路盲测（7 风格：封面/花字/面板/acts 零裸数字、零残片、零词缀劈碎）
        studio_audio = _make_audio([
            (0.0, 4.0, "大家好，今天带你们看看我刚升级完的播客录音棚。", ["一个", "录音棚"]),
            (4.2, 8.5, "我们主打全消声录音间和USB 3.0便携声卡，门口就有免费停车位喺𠮶度等大家。", ["USB 3.0", "停车位"]),
            (8.7, 13.0, "很多人只盯着麦克风价钱，其实120Hz以下的低频驻波处理才最关键。", ["120Hz以", "低频驻波"]),
            (13.2, 17.5, "记住，好人声是声学调校出来的，不是靠后期插件硬修出来的。", ["声学调校"]),
            (17.7, 22.0, "机位我们直接上4K 60帧双机位录制，配一根Type-C供电线就能跑满全天。", ["4K 60", "Type-C"]),
            (22.2, 26.2, "开降噪前后的底噪落差接近30分贝，而且续航能撑12小时。", ["30分贝"]),
            (26.4, 30.0, "评论区说说你平时录播客最头疼哪一项环境噪声。", ["说说"]),
        ], total=30.2)
        for sid in ("style_02", "style_04", "style_07", "style_12", "style_15", "style_20", "style_24"):
            pl_s = director_plan.generate_deterministic_director_plan(studio_audio, style_id=sid)
            for sub in pl_s["semantic_subtitles"]:
                self.assertFalse(sub["text"].startswith("完的"), (sid, sub))
                self.assertFalse(sub["text"].endswith("4K 60帧双"), (sid, sub))
                self.assertNotIn("4K60", sub["text"], (sid, sub))
            for k in ("headline_top", "highlight_badge", "sub_line", "dish_or_subject"):
                self.assertNotIn(pl_s["cover_copy"][k], ("60", "120", "30", "一根", "完的播客录音棚", "4K60", "4K 60帧双", "其实120Hz"), (sid, k))
            for co in pl_s["style_callouts"]:
                self.assertNotIn(co["text"], ("60", "120", "30", "一根", "完的播客录音棚", "4K60", "4K 60帧双", "其实120Hz"), (sid, co))
            for b in pl_s["style_scene_beats"]:
                lay = b["layout"]
                for fld in ("panel_title", "giant_back_text"):
                    self.assertNotIn(lay.get(fld), ("60", "120", "30", "一根", "完的播客录音棚", "4K60", "4K 60帧双", "其实120Hz"), (sid, fld))
                for it in (lay.get("panel_items") or []):
                    it_txt = str(it.get("text") or it.get("label") or "")
                    self.assertNotIn(it_txt, ("60", "120", "30", "一根", "完的播客录音棚", "4K60", "4K 60帧双", "其实120Hz"), (sid, it))
            for a in pl_s["acts"]:
                self.assertNotIn(a["visual_copy"]["headline"], ("60", "120", "30", "一根"), (sid, a))

        # 7) 句中 '一句' 与 '只顾' 多风格端到端不泄漏
        koubo_audio = _make_audio([
            (0.0, 6.5, "与其把特效加满不如先把一句没有用的废话删干净画面自然就清楚了", []),
            (7.0, 13.5, "观众平台算法这三件事要放在一起想只顾一头就会失衡", []),
        ], total=14.0)
        for sid in ("style_15", "style_24"):
            pl_k = director_plan.generate_deterministic_director_plan(koubo_audio, style_id=sid)
            pl_k_chk = json.loads(json.dumps(pl_k, ensure_ascii=False))
            pl_k_chk.pop("semantic_subtitles", None)
            for b in pl_k_chk.get("style_scene_beats") or []:
                (b.get("layout") or {}).pop("quote_text", None)
            blob_k = json.dumps(pl_k_chk, ensure_ascii=False)
            self.assertNotIn('"不如先把一句"', blob_k, (sid, blob_k))
            self.assertNotIn("这三件事要放在一起想只顾", blob_k, (sid, blob_k))

    def test_style02_auto_composition_profile_uses_unified_theme_while_bare_hint_preserved(self) -> None:
        audio = _make_audio([
            (0.0, 3.2, "很多人以为做门店只要位置好就能赚钱", ["位置"]),
            (3.4, 6.8, "其实复购率和动线效率才决定生死", ["复购率"]),
            (7.0, 10.5, "我们先拆开看人工与毛利这两本账", ["毛利"]),
        ], total=11.0)
        auto_biz_profile = {
            "step": "01b_composition_profile",
            "primary_stage_mode": "closeup_chest_stage",
            "allow_behind_person": False,
            "theme_hint": "business_editorial",
            "aroll_profiles": [{"media_path": "aroll.mp4", "allow_behind_person": False}],
        }
        plan_biz = director_plan.generate_deterministic_director_plan(
            audio, style_id="style_02", composition_profile=auto_biz_profile,
        )
        self.assertEqual(plan_biz["theme_profile"], "style_02_unified")
        self.assertFalse(plan_biz["native_style_mode"])

        auto_store_profile = {
            "step": "01b_composition_profile",
            "primary_stage_mode": "wide_behind_person",
            "allow_behind_person": True,
            "theme_hint": "store_vibrant",
            "aroll_profiles": [{"media_path": "aroll.mp4", "allow_behind_person": True}],
        }
        plan_sto = director_plan.generate_deterministic_director_plan(
            audio, style_id="style_02", composition_profile=auto_store_profile,
        )
        self.assertEqual(plan_sto["theme_profile"], "style_02_unified")

        # 裸 dict 提示仍保留对既有单测的向后兼容
        bare_store = director_plan.generate_deterministic_director_plan(
            audio, composition_profile={"theme_hint": "store_vibrant", "allow_behind_person": True},
        )
        self.assertEqual(bare_store["theme_profile"], "store_vibrant")

    def test_load_or_build_director_plan_invalidates_stale_style02_or_mismatched_style(self) -> None:
        audio = _make_audio([
            (0.0, 3.0, "不要再把预算全砸在无效曝光上", ["预算"]),
            (3.2, 6.5, "先把前十秒的真实问题讲清楚", ["真实问题"]),
        ], total=7.0)
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            write_json(pdir / "03_audio_events.json", audio)
            write_json(pdir / "01b_composition_profile.json", {
                "step": "01b_composition_profile",
                "primary_stage_mode": "closeup_chest_stage",
                "allow_behind_person": False,
                "theme_hint": "business_editorial",
                "aroll_profiles": [],
            })

            stale_s02 = director_plan.generate_deterministic_director_plan(audio, style_id="style_03")
            stale_s02["style_id"] = "style_02"
            stale_s02["native_style_mode"] = False
            stale_s02["theme_profile"] = "business_editorial"
            stale_s02["source"] = "fallback_deterministic"
            write_json(pdir / "03b_director_plan.json", stale_s02)

            self.assertIsNone(
                director_plan.load_or_build_director_plan(pdir, style_id="style_02", generate_fallback=False)
            )
            rebuilt = director_plan.load_or_build_director_plan(pdir, style_id="style_02", generate_fallback=True)
            self.assertIsNotNone(rebuilt)
            self.assertEqual(rebuilt["theme_profile"], "style_02_unified")
            self.assertEqual(rebuilt["style_id"], "style_02")

            # style_id 不匹配同样应失效重算
            other_style_plan = director_plan.generate_deterministic_director_plan(audio, style_id="style_09")
            write_json(pdir / "03b_director_plan.json", other_style_plan)
            self.assertIsNone(
                director_plan.load_or_build_director_plan(pdir, style_id="style_02", generate_fallback=False)
            )
            rebuilt_from_s09 = director_plan.load_or_build_director_plan(pdir, style_id="style_02", generate_fallback=True)
            self.assertEqual(rebuilt_from_s09["style_id"], "style_02")
            self.assertEqual(rebuilt_from_s09["theme_profile"], "style_02_unified")

    def test_reedit_visuals_auto_audits_missing_compliance_report_and_blocks_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p_ok = Path(td) / "ok_proj"
            p_ok.mkdir(parents=True)
            write_json(p_ok / "02_transcript.json", {
                "step": "02_transcribe",
                "source_type": "asr_whisper",
                "segments": [
                    {"id": "seg_001", "text": "今天来看看这家店的后厨备料流程", "start_sec": 0.0, "end_sec": 2.5},
                    {"id": "seg_002", "text": "每天现切现配，不囤隔夜半成品", "start_sec": 2.6, "end_sec": 5.2},
                ],
            })
            self.assertFalse((p_ok / "00_compliance_report.json").exists())
            run_pipeline._ensure_reedit_dependencies(p_ok, preserve_locked_audio=True)
            self.assertTrue((p_ok / "00_compliance_report.json").is_file())
            self.assertTrue((p_ok / "00_compliance_report.md").is_file())
            rep_ok = read_json(p_ok / "00_compliance_report.json")
            self.assertTrue(rep_ok["passed"])
            self.assertEqual(rep_ok["real_voice_total"], 2)

            p_bad = Path(td) / "bad_proj"
            p_bad.mkdir(parents=True)
            write_json(p_bad / "02_transcript.json", {
                "step": "02_transcribe",
                "source_type": "real_voice",
                "segments": [
                    {"id": "seg_001", "text": "我们这是全网第一品牌而且绝对有效", "start_sec": 0.0, "end_sec": 2.5},
                    {"id": "seg_002", "text": "全行业最强配方包治百病零风险", "start_sec": 2.6, "end_sec": 5.2},
                ],
            })
            with self.assertRaises(RuntimeError) as ctx:
                run_pipeline._ensure_reedit_dependencies(p_bad, preserve_locked_audio=True)
            self.assertIn("合规自动复审未通过", str(ctx.exception))
            self.assertTrue((p_bad / "00_compliance_report.json").is_file())
            self.assertFalse(read_json(p_bad / "00_compliance_report.json")["passed"])

            # tts 违规词不得在内存中被 auto_action=True 静默改写放行
            p_tts_bad = Path(td) / "tts_bad_proj"
            p_tts_bad.mkdir(parents=True)
            write_json(p_tts_bad / "02_transcript.json", {
                "step": "02_transcribe",
                "source_type": "tts",
                "segments": [
                    {"id": "seg_001", "text": "我们这是全网第一的门店做法", "start_sec": 0.0, "end_sec": 2.5},
                    {"id": "seg_002", "text": "每天现切现配保证新鲜", "start_sec": 2.6, "end_sec": 5.0},
                ],
            })
            with self.assertRaises(RuntimeError) as ctx_tts:
                run_pipeline._ensure_reedit_dependencies(p_tts_bad, preserve_locked_audio=True)
            self.assertIn("合规自动复审未通过", str(ctx_tts.exception))
            rep_tts_bad = read_json(p_tts_bad / "00_compliance_report.json")
            self.assertFalse(rep_tts_bad["passed"])
            self.assertGreaterEqual(rep_tts_bad["unresolved_must_count"], 1)
            self.assertEqual(rep_tts_bad["auto_rewritten_count"], 0)
            self.assertEqual(
                read_json(p_tts_bad / "02_transcript.json")["segments"][0]["text"],
                "我们这是全网第一的门店做法",
            )

            # real_voice <= 40% (1/4) 未标 is_filler=True 的违规句同样必须阻断，不可静默标废放行
            p_rv_quarter_bad = Path(td) / "rv_quarter_bad_proj"
            p_rv_quarter_bad.mkdir(parents=True)
            write_json(p_rv_quarter_bad / "02_transcript.json", {
                "step": "02_transcribe",
                "source_type": "real_voice",
                "segments": [
                    {"id": "seg_001", "text": "我们这是全网第一品牌而且绝对有效", "start_sec": 0.0, "end_sec": 2.5},
                    {"id": "seg_002", "text": "今天来看看后厨备料流程", "start_sec": 2.6, "end_sec": 5.0},
                    {"id": "seg_003", "text": "每天现切现配不囤隔夜食材", "start_sec": 5.1, "end_sec": 7.5},
                    {"id": "seg_004", "text": "客单价和出餐效率都稳定", "start_sec": 7.6, "end_sec": 10.0},
                ],
            })
            with self.assertRaises(RuntimeError) as ctx_rv:
                run_pipeline._ensure_reedit_dependencies(p_rv_quarter_bad, preserve_locked_audio=True)
            self.assertIn("合规自动复审未通过", str(ctx_rv.exception))
            rep_rv_bad = read_json(p_rv_quarter_bad / "00_compliance_report.json")
            self.assertFalse(rep_rv_bad["passed"])
            self.assertEqual(rep_rv_bad["unresolved_must_count"], 1)
            self.assertEqual(rep_rv_bad["dropped_real_voice_ids"], [])

            # 已在前置 step2 过滤标为 is_filler=True 的 real_voice 句子与干净 tts 口播应顺利通过
            p_rv_filtered_ok = Path(td) / "rv_filtered_ok_proj"
            p_rv_filtered_ok.mkdir(parents=True)
            write_json(p_rv_filtered_ok / "02_transcript.json", {
                "step": "02_transcribe",
                "source_type": "real_voice",
                "segments": [
                    {"id": "seg_001", "text": "我们这是全网第一品牌而且绝对有效", "start_sec": 0.0, "end_sec": 2.5, "is_filler": True},
                    {"id": "seg_002", "text": "今天来看看后厨备料流程", "start_sec": 2.6, "end_sec": 5.0},
                    {"id": "seg_003", "text": "每天现切现配不囤隔夜食材", "start_sec": 5.1, "end_sec": 7.5},
                    {"id": "seg_004", "text": "客单价和出餐效率都稳定", "start_sec": 7.6, "end_sec": 10.0},
                ],
            })
            run_pipeline._ensure_reedit_dependencies(p_rv_filtered_ok, preserve_locked_audio=True)
            rep_rv_ok = read_json(p_rv_filtered_ok / "00_compliance_report.json")
            self.assertTrue(rep_rv_ok["passed"])
            self.assertEqual(rep_rv_ok["unresolved_must_count"], 0)

            p_tts_ok = Path(td) / "tts_ok_proj"
            p_tts_ok.mkdir(parents=True)
            write_json(p_tts_ok / "02_transcript.json", {
                "step": "02_transcribe",
                "source_type": "tts",
                "segments": [
                    {"id": "seg_001", "text": "今天来看看后厨备料流程", "start_sec": 0.0, "end_sec": 2.5},
                    {"id": "seg_002", "text": "每天现切现配不囤隔夜食材", "start_sec": 2.6, "end_sec": 5.0},
                ],
            })
            run_pipeline._ensure_reedit_dependencies(p_tts_ok, preserve_locked_audio=True)
            rep_tts_ok = read_json(p_tts_ok / "00_compliance_report.json")
            self.assertTrue(rep_tts_ok["passed"])
            self.assertEqual(rep_tts_ok["unresolved_must_count"], 0)

    def test_side_shift_x_generation_validation_and_native_step4(self) -> None:
        import step4_build_edl  # noqa: E402
        from copy import deepcopy  # noqa: E402

        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll = _make_solid_mp4(pdir / "norm_01_speaker.mp4", (45, 55, 75), 11.0)
            audio = {
                "locked_duration_sec": 10.5,
                "locked_duration_frames": 315,
                "sentences": [
                    {
                        "id": "sent_001", "text": "很多人开店只看热闹", "start_sec": 0.0, "end_sec": 2.8,
                        "source_media": str(aroll), "source_in_sec": 0.0, "source_out_sec": 2.8, "keywords": [],
                    },
                    {
                        "id": "sent_002", "text": "核心不是规模而是复购", "start_sec": 3.0, "end_sec": 6.2,
                        "source_media": str(aroll), "source_in_sec": 3.0, "source_out_sec": 6.2, "keywords": [],
                    },
                    {
                        "id": "sent_003", "text": "回去按明细核对每天复购率", "start_sec": 6.4, "end_sec": 10.2,
                        "source_media": str(aroll), "source_in_sec": 6.4, "source_out_sec": 10.2, "keywords": [],
                    },
                ],
                "events": [
                    {"id": "evt_001", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "sent_001"},
                    {"id": "evt_002", "type": "sentence_start", "time_sec": 3.0, "frame": 90, "sentence_id": "sent_002"},
                    {"id": "evt_003", "type": "sentence_start", "time_sec": 6.4, "frame": 192, "sentence_id": "sent_003"},
                ],
            }
            comp = {
                "step": "01b_composition_profile",
                "primary_stage_mode": "closeup_chest_stage",
                "allow_behind_person": False,
                "aroll_profiles": [
                    {
                        "media_path": str(aroll),
                        "face_bbox": {"x": 0.20, "y": 0.25, "w": 0.35, "h": 0.25},
                        "safe_center_shift_x": 0.12,
                        "allow_behind_person": False,
                    }
                ],
            }
            plan = director_plan.generate_deterministic_director_plan(
                audio, style_id="style_03", composition_profile=comp, pipeline_dir=pdir,
            )
            ss_acts = [a for a in plan["acts"] if a.get("camera_intent") == "side_shift_card"]
            self.assertTrue(ss_acts, plan["acts"])
            self.assertEqual(ss_acts[0]["side_shift_x"], 0.12)

            # validate_director_plan: 缺省 side_shift_card 自动补齐且不原地修改入参；非法数值报错
            raw_plan = deepcopy(plan)
            for a in raw_plan["acts"]:
                a.pop("side_shift_x", None)
            snap_in = deepcopy(raw_plan)
            chk_ok = director_plan.validate_director_plan(raw_plan, audio, comp)
            self.assertEqual(raw_plan, snap_in)
            self.assertTrue(chk_ok["ok"], chk_ok["errors"])
            ss_clean = [a for a in chk_ok["sanitized_plan"]["acts"] if a.get("camera_intent") == "side_shift_card"]
            self.assertEqual(ss_clean[0]["side_shift_x"], 0.12)

            bad_plan = deepcopy(plan)
            bad_plan["acts"][0]["side_shift_x"] = float("nan")
            chk_bad = director_plan.validate_director_plan(bad_plan, audio, comp)
            self.assertFalse(chk_bad["ok"])
            self.assertTrue(any("side_shift_x" in e for e in chk_bad["errors"]))

            # 原生 style_03 run_step4 即使 03b_director_plan.json 缺失 side_shift_x 也不崩溃且准确生效
            write_json(pdir / "01_manifest.json", {
                "items": [{
                    "id": "a1", "role": "a_roll", "normalized_mp4": str(aroll),
                    "duration_sec": 11.0, "video_grade": "usable",
                    "faces": [{"x": 0.20, "y": 0.25, "w": 0.35, "h": 0.25}],
                }]
            })
            write_json(pdir / "03_audio_events.json", audio)
            write_json(pdir / "01b_composition_profile.json", comp)
            write_json(pdir / "03b_director_plan.json", raw_plan)
            edl = step4_build_edl.run_step4(pdir, track="creator", style_id="style_03")
            ss_clips = [c for c in edl["clips"] if c.get("camera_intent") == "side_shift_card"]
            self.assertTrue(ss_clips)
            self.assertTrue(any("-0.120000*ow" in str(c.get("vf_extra") or "") for c in ss_clips), ss_clips)

            # _apply_director_cameras 缺失字段安全回退
            mock_clips = [{"track_role": "a_roll", "media_path": str(aroll), "timeline_in_sec": 0.0, "timeline_out_sec": 2.0}]
            mock_acts = [{"start_sec": 0.0, "end_sec": 2.0, "camera_intent": "side_shift_card"}]
            step4_build_edl._apply_director_cameras(mock_clips, mock_acts, {}, True, composition_profile=comp)
            self.assertIn("-0.120000*ow", mock_clips[0]["vf_extra"])

    def test_director_plan_semantic_subtitles_glossary_font_and_no_yijian_glue(self) -> None:
        # 1) 通用量词头不得与后继数词短语粘连（例如绝不产生 '一间六百平'）
        txt_s = "我花咗三百万喺金湾开咗一间六百平嘅点赞茶楼"
        dyn = director_plan._dynamic_protected_terms(txt_s)
        self.assertNotIn("一间六百平", dyn)
        for head in ("一间", "一个", "一批", "一种", "一款", "一套", "一份", "一家", "一条", "一张", "一点"):
            self.assertNotIn(f"{head}六百平", director_plan._dynamic_protected_terms(f"{head}六百平的门店"))

        # 2) _build_semantic_subtitles 读取 pipeline_dir 术语表 + 00_raw_intel + 真实字体，且保留缺省签名兼容
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "terms.txt").write_text("点赞茶楼\n金湾\n桑拿鸡\n", encoding="utf-8")
            write_json(pdir / "00_raw_intel.json", {"pain_keywords": ["纯手工"]})
            ae = _make_audio([
                (0.0, 4.2, "我花咗三百万喺金湾开咗一间六百平嘅点赞茶楼", ["三百万", "点赞茶楼"]),
                (4.4, 9.0, "一早点赞茶楼呢一个特色就系朝早做纯手工嘅早茶晚上就系做桑拿鸡", ["点赞茶楼", "桑拿鸡"]),
            ], total=9.5)
            subs_default = director_plan._build_semantic_subtitles(ae, "style_03")
            self.assertTrue(subs_default)
            subs_pdir = director_plan._build_semantic_subtitles(ae, "style_03", pipeline_dir=pdir)
            texts = [x["text"] for x in subs_pdir]
            self.assertFalse(any(t.endswith(("金", "点赞", "开咗")) for t in texts), texts)
            self.assertFalse(any(t.startswith(("湾", "茶楼")) for t in texts), texts)

            # 3) _build_style_scene_beats 保护 panel_title 词界并清除 quote_text / panel_lines 悬空虚词尾
            ae_beats = _make_audio([
                (0.0, 3.0, "我花咗三百万喺金湾开咗一间六百平嘅点赞茶楼", ["点赞茶楼"]),
                (3.2, 7.0, "一早点赞茶楼呢一个特色就系朝早做纯手工嘅早茶晚上就系做桑拿鸡", []),
                (7.2, 11.0, "呢度环境舒服适合朋友聚餐聊天", ["环境"]),
                (11.2, 15.0, "我哋桑拿鸡个鸡就系从山上捉翻嚟嘅", ["桑拿鸡"]),
                (15.2, 19.0, "所以食嘅就系鸡𠮶种原汁原味", ["原汁原味"]),
                (19.2, 24.0, "最后带大家睇下大厅同后厨布局", ["后厨"]),
                (24.2, 28.0, "钟意食早茶记得评论区讲下", ["评论区"]),
            ], total=28.5)
            plan_b = director_plan.generate_deterministic_director_plan(ae_beats, style_id="style_03", pipeline_dir=pdir)
            for b in plan_b["style_scene_beats"]:
                lay = b["layout"]
                self.assertNotEqual(lay.get("panel_title"), "一早点赞", lay)
                qt = str(lay.get("quote_text") or "")
                if qt:
                    self.assertNotIn(qt[-1], "就的嘅咗了喺在把被给", lay)
                for pl in (lay.get("panel_lines") or []):
                    self.assertNotIn(str(pl)[-1], "就的嘅咗了喺在把被给", lay)

    def test_native_director_broll_semantic_filtering_and_cta_stopword_guard(self) -> None:
        import step4_build_edl  # noqa: E402

        # 1) _match_broll_keywords 过滤停用词 + 纯口播 CTA 不给 allow_verified_broll
        inv = {
            "clips": [
                {"filename": "tea.mov", "suggested_role": "b_roll_candidate", "description": "手工茶楼点心出笼", "tags": ["一个", "点心"]},
            ]
        }
        group = [{"id": "s1", "text": "一个普通门店评论区讲下", "keywords": ["一个", "呢个"]}]
        kws = director_plan._match_broll_keywords(group, director_plan._broll_pool(inv))
        self.assertNotIn("一个", kws)
        self.assertNotIn("呢个", kws)

        ae_cta = _make_audio([
            (0.0, 3.0, "今天带大家看呢间点赞茶楼", ["茶楼"]),
            (3.2, 6.2, "点心全部每日现做现蒸", ["点心"]),
            (6.4, 9.5, "唔知道大家中意食乜嘢可以喺评论区讲下", ["一个"]),
        ], total=9.8)
        plan_cta = director_plan.generate_deterministic_director_plan(ae_cta, style_id="style_06", inventory=inv)
        cta_acts = [a for a in plan_cta["acts"] if a.get("dramatic_role") == "cta"]
        self.assertTrue(cta_acts)
        for ca in cta_acts:
            self.assertEqual(ca["broll_policy"], "lock_aroll", ca)

        # 2) 原生 style_06 step4 当 allow_verified_broll 无匹配 B-roll 时回留 a_roll，绝不插入 tier='none'
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll = _make_solid_mp4(pdir / "norm_01_aroll.mp4", (40, 60, 90), 10.5)
            broll = _make_solid_mp4(pdir / "norm_02_dimsum.mp4", (180, 120, 60), 5.0)
            audio = {
                "locked_duration_sec": 9.6,
                "locked_duration_frames": 288,
                "sentences": [
                    {"id": "s1", "text": "今日同大家来看手工点心", "start_sec": 0.0, "end_sec": 3.0, "source_media": str(aroll), "source_in_sec": 0.0, "source_out_sec": 3.0, "keywords": ["点心"]},
                    {"id": "s2", "text": "水晶虾饺热气出笼", "start_sec": 3.2, "end_sec": 6.2, "source_media": str(aroll), "source_in_sec": 3.2, "source_out_sec": 6.2, "keywords": ["虾饺"]},
                    {"id": "s3", "text": "欢迎大家在评论区留言互动", "start_sec": 6.4, "end_sec": 9.4, "source_media": str(aroll), "source_in_sec": 6.4, "source_out_sec": 9.4, "keywords": []},
                ],
                "events": [
                    {"id": "e1", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "s1"},
                    {"id": "e2", "type": "sentence_start", "time_sec": 3.2, "frame": 96, "sentence_id": "s2"},
                    {"id": "e3", "type": "sentence_start", "time_sec": 6.4, "frame": 192, "sentence_id": "s3"},
                ],
            }
            write_json(pdir / "01_manifest.json", {
                "items": [
                    {"id": "a1", "role": "a_roll", "normalized_mp4": str(aroll), "duration_sec": 10.5, "video_grade": "usable"},
                    {"id": "b1", "role": "b_roll", "source_path": "dimsum.mp4", "normalized_mp4": str(broll), "duration_sec": 5.0, "video_grade": "usable", "semantic_tags": ["虾饺"]},
                ]
            })
            write_json(pdir / "00_visual_index.json", {
                "clips": [{"filename": "dimsum.mp4", "role": "b_roll", "description": "水晶虾饺热气出笼", "semantic_tags": ["虾饺"]}]
            })
            write_json(pdir / "03_audio_events.json", audio)
            # 故意在 s3 挂 allow_verified_broll 测试原生模式宁缺毋滥回退 a_roll
            dp_forced = director_plan.generate_deterministic_director_plan(audio, style_id="style_06", pipeline_dir=pdir)
            for a in dp_forced["acts"]:
                if "s3" in a["sentence_ids"]:
                    a["broll_policy"] = "allow_verified_broll"
            write_json(pdir / "03b_director_plan.json", dp_forced)
            edl = step4_build_edl.run_step4(pdir, track="store", style_id="style_06")
            self.assertEqual(edl["broll_semantic"]["none"], 0, edl["broll_semantic"])
            s3_clips = [c for c in edl["clips"] if c.get("sentence_id") == "s3"]
            self.assertTrue(s3_clips and all(c["track_role"] == "a_roll" for c in s3_clips), s3_clips)

    def test_step4_aroll_to_broll_eof_snap_and_whip_strategy_d(self) -> None:
        import numpy as np  # noqa: E402
        import clip_boundary_audit as cba  # noqa: E402
        import step4_build_edl  # noqa: E402

        # 1) a_roll -> b_roll 源尾冻帧吸附（shortfall > 0.08s 吸附到句末 pause_start/sentence_end）
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll1 = _make_solid_mp4(pdir / "norm_01_a1.mp4", (40, 60, 90), 5.0)
            broll1 = _make_solid_mp4(pdir / "norm_02_b1.mp4", (180, 120, 60), 5.0)
            audio = {
                "locked_duration_sec": 7.5,
                "locked_duration_frames": 225,
                "sentences": [
                    {"id": "s1", "text": "前面先讲清核心要点", "start_sec": 0.0, "end_sec": 3.0, "source_media": str(aroll1), "source_in_sec": 0.0, "source_out_sec": 3.0, "keywords": []},
                    {"id": "s2", "text": "呢个位置面积非常大", "start_sec": 3.1, "end_sec": 4.9, "source_media": str(aroll1), "source_in_sec": 3.1, "source_out_sec": 4.9, "keywords": []},
                    {"id": "s3", "text": "接着看看水晶虾饺出笼", "start_sec": 5.1, "end_sec": 7.4, "source_media": str(aroll1), "source_in_sec": 0.0, "source_out_sec": 2.3, "keywords": ["虾饺"]},
                ],
                "events": [
                    {"id": "e1", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "s1"},
                    {"id": "e2", "type": "sentence_start", "time_sec": 3.1, "frame": 93, "sentence_id": "s2"},
                    {"id": "e3", "type": "pause_start", "time_sec": 4.9, "frame": 147, "sentence_id": "s2"},
                    {"id": "e4", "type": "sentence_start", "time_sec": 5.1, "frame": 153, "sentence_id": "s3"},
                ],
            }
            write_json(pdir / "01_manifest.json", {
                "items": [
                    {"id": "a1", "role": "a_roll", "normalized_mp4": str(aroll1), "duration_sec": 5.0, "video_grade": "usable"},
                    {"id": "b1", "role": "b_roll", "normalized_mp4": str(broll1), "duration_sec": 5.0, "video_grade": "usable", "semantic_tags": ["虾饺"]},
                ]
            })
            write_json(pdir / "00_visual_index.json", {
                "clips": [{"filename": "norm_02_b1.mp4", "role": "b_roll", "description": "水晶虾饺特写", "semantic_tags": ["虾饺"]}]
            })
            write_json(pdir / "03_audio_events.json", audio)
            dp_eof = director_plan.generate_deterministic_director_plan(audio, style_id="style_03", pipeline_dir=pdir)
            for a in dp_eof["acts"]:
                a["broll_policy"] = "allow_verified_broll" if "s3" in a["sentence_ids"] else "lock_aroll"
            write_json(pdir / "03b_director_plan.json", dp_eof)
            edl_eof = step4_build_edl.run_step4(pdir, track="store", style_id="style_03")
            for c in edl_eof["clips"]:
                if c["track_role"] == "a_roll":
                    sf = float(c["duration_sec"]) - (float(c["source_out_sec"]) - float(c["source_in_sec"]))
                    self.assertLessEqual(sf, 0.08, c)

        # 2) Strategy D whip heal: a_roll 入点甩手糊帧且前镜为可延展 b_roll -> 推进到本镜内最早清晰音频事件
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll_pre = _make_solid_mp4(pdir / "norm_01_pre.mp4", (30, 50, 70), 6.0)
            aroll_whip = _make_solid_mp4(pdir / "norm_02_whip.mp4", (45, 65, 85), 16.0)
            broll_prev = _make_solid_mp4(pdir / "norm_03_bprev.mp4", (180, 120, 60), 6.0)
            audio = {
                "locked_duration_sec": 15.2,
                "locked_duration_frames": 456,
                "sentences": [
                    {"id": "s1", "text": "大家好我先介绍门店位置", "start_sec": 0.0, "end_sec": 2.9, "source_media": str(aroll_pre), "source_in_sec": 0.0, "source_out_sec": 2.9, "keywords": []},
                    {"id": "s2", "text": "门口水晶虾饺现蒸现卖", "start_sec": 3.0, "end_sec": 5.0, "source_media": str(aroll_pre), "source_in_sec": 3.0, "source_out_sec": 5.0, "keywords": ["虾饺"]},
                    {"id": "s3", "text": "中间上边空间很宽敞", "start_sec": 5.0, "end_sec": 8.0, "source_media": str(aroll_whip), "source_in_sec": 3.1, "source_out_sec": 6.1, "keywords": []},
                    {"id": "s4", "text": "后面卡座可以坐很多客人", "start_sec": 8.2, "end_sec": 12.0, "source_media": str(aroll_whip), "source_in_sec": 7.5, "source_out_sec": 11.3, "keywords": []},
                    {"id": "s5", "text": "欢迎大家来体验", "start_sec": 12.2, "end_sec": 15.0, "source_media": str(aroll_whip), "source_in_sec": 11.5, "source_out_sec": 14.3, "keywords": []},
                ],
                "events": [
                    {"id": "e1", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "s1"},
                    {"id": "e2", "type": "sentence_start", "time_sec": 3.0, "frame": 90, "sentence_id": "s2"},
                    {"id": "e3", "type": "sentence_start", "time_sec": 5.0, "frame": 150, "sentence_id": "s3"},
                    {"id": "e4", "type": "bgm_downbeat", "time_sec": 5.4, "frame": 162, "sentence_id": None},
                    {"id": "e5", "type": "sentence_start", "time_sec": 8.2, "frame": 246, "sentence_id": "s4"},
                    {"id": "e6", "type": "sentence_start", "time_sec": 12.2, "frame": 366, "sentence_id": "s5"},
                ],
            }
            write_json(pdir / "01_manifest.json", {
                "items": [
                    {"id": "a1", "role": "a_roll", "normalized_mp4": str(aroll_pre), "duration_sec": 6.0, "video_grade": "usable"},
                    {"id": "a2", "role": "a_roll", "normalized_mp4": str(aroll_whip), "duration_sec": 16.0, "video_grade": "usable"},
                    {"id": "b1", "role": "b_roll", "source_path": "bprev.mp4", "normalized_mp4": str(broll_prev), "duration_sec": 6.0, "video_grade": "usable", "safe_in_sec": 0.0, "safe_out_sec": 6.0, "semantic_tags": ["虾饺"]},
                ]
            })
            write_json(pdir / "00_visual_index.json", {
                "clips": [{"filename": "bprev.mp4", "role": "b_roll", "description": "水晶虾饺特写", "semantic_tags": ["虾饺"]}]
            })
            write_json(pdir / "03_audio_events.json", audio)
            dp_whip = director_plan.generate_deterministic_director_plan(audio, style_id="style_06", pipeline_dir=pdir)
            for a in dp_whip["acts"]:
                a["broll_policy"] = "allow_verified_broll" if "s2" in a["sentence_ids"] else "lock_aroll"
            write_json(pdir / "03b_director_plan.json", dp_whip)

            def _fake_scan(path, start_sec, dur_sec, sample_fps, size):
                times = np.arange(0.0, max(0.2, dur_sec), 0.1)
                sharp = np.full(times.size, 1900.0)
                motion = np.full(times.size, 0.04)
                luma = np.full(times.size, 120.0)
                std = np.full(times.size, 30.0)
                if "norm_02_whip" in str(path):
                    whip_mask = (times >= 3.0) & (times <= 3.45)
                    sharp[whip_mask] = 780.0
                    motion[whip_mask] = 0.16
                return {"times": times, "sharp": sharp, "motion": motion, "luma": luma, "std": std}

            with mock.patch.object(cba, "_scan_segment", side_effect=_fake_scan), mock.patch.object(cba, "_frame_size", return_value=(540, 960)):
                edl_whip = step4_build_edl.run_step4(pdir, track="store", style_id="style_06")
            s3_arolls = [c for c in edl_whip["clips"] if c["track_role"] == "a_roll" and "norm_02_whip" in str(c["media_path"]) and c["source_in_sec"] < 5.0]
            self.assertTrue(s3_arolls, edl_whip["clips"])
            self.assertGreaterEqual(s3_arolls[0]["source_in_sec"], 3.45, s3_arolls[0])
            self.assertGreaterEqual(s3_arolls[0]["timeline_in_sec"], 5.35, s3_arolls[0])

    def test_prepare_v6_director_and_v11_wrapper_forward_pipeline_dir_terms(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "terms.txt").write_text("金湾赞茶楼\n", encoding="utf-8")
            write_json(pdir / "00_raw_intel.json", {"pain_keywords": ["金湾赞茶楼"]})
            audio = _make_audio([
                (0.0, 4.5, "我花咗三百万喺金湾开咗一间六百平嘅金湾赞茶楼", ["三百万", "金湾赞茶楼"]),
                (4.7, 8.8, "每日朝早都有纯手工早茶点心同大家见面", ["纯手工"]),
            ], total=9.2)
            write_json(pdir / "03_audio_events.json", audio)

            info = run_pipeline._prepare_v6_director(pdir, "style_03", None, False, False)
            self.assertTrue(info["native_style_mode"])
            plan = read_json(pdir / "03b_director_plan.json")
            sub_texts = [str(s.get("text") or "") for s in (plan.get("semantic_subtitles") or [])]
            self.assertTrue(any("金湾赞茶楼" in t for t in sub_texts), sub_texts)

            plan_wrap = v11dp.build_director_plan_for_pipeline(pdir, style_id="style_03", write_plan=False)
            wrap_texts = [str(s.get("text") or "") for s in (plan_wrap.get("semantic_subtitles") or [])]
            self.assertTrue(any("金湾赞茶楼" in t for t in wrap_texts), wrap_texts)

    def test_prot_terms_and_entities_unsplit_in_cover_callouts_and_panels(self) -> None:
        import style_visual_truth as svt

        prot_terms = ["潮汕手打牛肉丸", "沙湾古镇姜埋奶", "荔枝木烧鹅王"]
        sent_rows = [
            (0.0, 3.5, "讲真，今日带大家去试潮汕手打牛肉丸", []),
            (3.6, 7.0, "九成游客专程去吃沙湾古镇姜埋奶", []),
            (7.1, 10.5, "所以大家来到都会先试试招牌荔枝木烧鹅王", []),
        ]
        full_blob = "，".join(r[2] for r in sent_rows)

        def _assert_no_term_split(snippets: list[str], context_label: str) -> None:
            for snip in snippets:
                s_clean = str(snip or "").strip()
                if not s_clean:
                    continue
                for src in [r[2] for r in sent_rows] + [full_blob]:
                    for term in prot_terms:
                        t_start = 0
                        while True:
                            ta = src.find(term, t_start)
                            if ta < 0:
                                break
                            tb = ta + len(term)
                            s_start = 0
                            while True:
                                sa = src.find(s_clean, s_start)
                                if sa < 0:
                                    break
                                sb = sa + len(s_clean)
                                if sa < tb and sb > ta and (sa > ta or sb < tb):
                                    self.fail(
                                        f"{context_label}: snippet {s_clean!r} splits protected term {term!r} in {src!r}"
                                    )
                                s_start = sa + 1
                            t_start = ta + 1

        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "terms.txt").write_text("潮汕手打牛肉丸\n沙湾古镇姜埋奶\n", encoding="utf-8")
            write_json(pdir / "00_raw_intel.json", {"entities": ["荔枝木烧鹅王"]})
            audio = _make_audio(sent_rows, total=11.0)
            write_json(pdir / "03_audio_events.json", audio)

            # 1) style_12: cover_copy, gbro_cover_spec, s12_pill_bubbles style_callouts
            run_pipeline._prepare_v6_director(pdir, "style_12", None, False, False)
            plan12 = read_json(pdir / "03b_director_plan.json")
            cov12 = plan12.get("cover_copy") or {}
            gbro12 = plan12.get("gbro_cover_spec") or {}
            cov_snippets = [
                cov12.get("headline_top", ""),
                cov12.get("highlight_badge", ""),
                cov12.get("sub_line", ""),
                cov12.get("dish_or_subject", ""),
                gbro12.get("headline_top", ""),
                gbro12.get("highlight_badge", ""),
                gbro12.get("sub_line", ""),
                gbro12.get("dish_or_subject", ""),
            ]
            _assert_no_term_split(cov_snippets, "style_12 cover/gbro")

            callouts12 = plan12.get("style_callouts") or []
            self.assertTrue(callouts12, plan12)
            co_snippets = []
            for co in callouts12:
                co_snippets.append(co.get("text", ""))
                co_snippets.extend(co.get("items") or [])
            _assert_no_term_split(co_snippets, "style_12 style_callouts")

            # Also verify s12_pill_bubbles directly on a long clause containing a protected term from entities
            s12_audio = _make_audio([(0.0, 4.0, "九成游客专程去吃沙湾古镇姜埋奶", [])], total=4.5)
            write_json(pdir / "03_audio_events.json", s12_audio)
            dp_s12 = director_plan.generate_deterministic_director_plan(s12_audio, style_id="style_12", pipeline_dir=pdir)
            s12_co_snips = []
            for co in dp_s12.get("style_callouts") or []:
                s12_co_snips.append(co.get("text", ""))
                s12_co_snips.extend(co.get("items") or [])
            _assert_no_term_split(s12_co_snips, "s12_pill_bubbles direct")

            # 2) style_24: review_whiteboard_pip
            write_json(pdir / "03_audio_events.json", audio)
            run_pipeline._prepare_v6_director(pdir, "style_24", None, False, False)
            plan24 = read_json(pdir / "03b_director_plan.json")
            wb_snippets = []
            for beat in plan24.get("style_scene_beats") or []:
                lay = beat.get("layout") or {}
                if lay.get("mode") == "review_whiteboard_pip":
                    wb_snippets.append(lay.get("panel_title", ""))
                    wb_snippets.extend(lay.get("panel_lines") or [])
                    wb_snippets.extend(str(it.get("text") or "") for it in (lay.get("panel_items") or []) if isinstance(it, dict))
            self.assertTrue(wb_snippets, plan24.get("style_scene_beats"))
            _assert_no_term_split(wb_snippets, "style_24 review_whiteboard_pip")

            # 3) concept_diagram_pip (chain & trio)
            with mock.patch.object(
                svt,
                "get_scene_beat_specs",
                return_value=[
                    {"frac": 0.25, "max_dur": 3.5, "layout": {"mode": "concept_diagram_pip"}},
                    {"frac": 0.75, "max_dur": 3.5, "layout": {"mode": "concept_diagram_pip"}},
                ],
            ):
                dp_cd = director_plan.generate_deterministic_director_plan(audio, style_id="style_03", pipeline_dir=pdir)
            cd_snippets = []
            for beat in dp_cd.get("style_scene_beats") or []:
                lay = beat.get("layout") or {}
                cd_snippets.append(lay.get("panel_title", ""))
                cd_snippets.extend(lay.get("panel_lines") or [])
                for it in lay.get("panel_items") or []:
                    if isinstance(it, dict):
                        cd_snippets.append(it.get("label", ""))
                        cd_snippets.append(it.get("text", ""))
            self.assertTrue(cd_snippets, dp_cd.get("style_scene_beats"))
            _assert_no_term_split(cd_snippets, "concept_diagram_pip")
            # Confirm entities term ("荔枝木烧鹅王") from 00_raw_intel.json is actually protected intact across plan outputs
            all_plan_text = json.dumps(plan24, ensure_ascii=False)
            self.assertIn("荔枝木烧鹅王", all_plan_text)

    def test_c1_c2_acts_headline_and_broll_tag_and_compare_protected_terms(self) -> None:
        import kinetic_choreographer as kc
        import style_visual_truth as svt

        prot_terms = ["古法柴火窑炉现烤面包", "古法窑炉之后出炉"]
        audio = _make_audio([
            (0.0, 4.5, "不是普通烤箱而是古法窑炉之后出炉的香", []),
            (4.7, 9.5, "不是流水线预制，而是古法柴火窑炉现烤面包", []),
        ], total=10.0)
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "terms.txt").write_text("\n".join(prot_terms) + "\n", encoding="utf-8")
            write_json(pdir / "00_raw_intel.json", {"entities": [{"name": "古法柴火窑炉现烤面包"}]})
            inv = {
                "clips": [{
                    "filename": "bread_close.mp4",
                    "suggested_role": "b_roll_candidate",
                    "tags": ["窑炉现烤"],
                    "description": "古法柴火窑炉现烤面包特写",
                }]
            }

            # C1: acts visual_copy.headline (and style_02 red_slam) never splits 8-10 char protected terms
            dp_s02 = director_plan.generate_deterministic_director_plan(
                audio, style_id="style_02", inventory=inv, pipeline_dir=pdir,
            )
            for act in dp_s02["acts"]:
                vc = act.get("visual_copy") or {}
                hl = str(vc.get("headline") or "")
                self.assertNotIn(hl, ("窑炉现烤面包", "出炉的香"), act)
                for it in (vc.get("items") or []):
                    self.assertNotIn(str(it).lstrip("✕✓ "), ("窑炉现烤面包", "出炉的香"), act)
            st_s02 = kc.plan_kinetic_stage(audio, style_id="style_02", director_plan=dp_s02, pipeline_dir=pdir)
            for it in st_s02["items"]:
                self.assertNotIn(str(it.get("text_or_asset") or ""), ("窑炉现烤面包", "出炉的香"), it)

            # C2 B-roll tag guard: strict truncated substring tag "窑炉现烤" (and stopword "特写") rejected
            dp_s09 = director_plan.generate_deterministic_director_plan(
                audio, style_id="style_09", inventory=inv, pipeline_dir=pdir,
            )
            self.assertNotEqual(dp_s09["cover_copy"]["dish_or_subject"], "窑炉现烤")
            self.assertNotEqual(dp_s09["cover_copy"]["dish_or_subject"], "特写")
            self.assertEqual(dp_s09["cover_copy"]["dish_or_subject"], "古法柴火窑炉现烤面包")
            self.assertEqual(dp_s09["gbro_cover_spec"]["dish_or_subject"], "古法柴火窑炉现烤面包")

            # C2 concept_diagram_pip compare/fallback guard
            with mock.patch.object(
                svt, "get_scene_beat_specs",
                return_value=[{"frac": 0.6, "max_dur": 3.5, "layout": {"mode": "concept_diagram_pip"}}],
            ):
                beats = director_plan._build_style_scene_beats(
                    "style_03",
                    audio["sentences"],
                    10.0,
                    [{
                        "sentence_ids": ["u_002"],
                        "dramatic_role": "twist",
                        "visual_copy": {"headline": "", "items": ["✕ 不是流水线预制", "✓ 窑炉现烤面包"]},
                    }],
                    {},
                    prot_terms=prot_terms,
                )
            self.assertTrue(beats)
            for b in beats:
                for it in (b["layout"].get("panel_items") or []):
                    lbl = str(it.get("label") or "")
                    self.assertNotIn("窑炉现烤面包", lbl.replace("古法柴火窑炉现烤面包", ""), b)


if __name__ == "__main__":
    unittest.main()
