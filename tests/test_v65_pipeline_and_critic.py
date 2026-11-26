"""V6.5 一键 V6 闭环、审片精修与短句底字幕避重的可测量回归。"""
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_pipeline as rp
from aesthetic_critic import CRITIC_DIMENSIONS, REVIEW_RESULT_FILE, REVIEW_TASKS_FILE, score_aesthetic_review
from common import read_json, write_json
from director_plan import (
    PLAN_FILENAME,
    TASKS_FILENAME,
    generate_deterministic_director_plan,
    validate_director_plan,
)
from kinetic_choreographer import business_caption_items
from step4_build_edl import run_step4
from step5_subtitles import run_step5
from step6_packaging import run_step6

FPS = 30
DURATION = 24.0
LINES = (
    ("s0", "第一点先问生意前提", 0.0, 4.0),
    ("s1", "第二点核对回款", 4.2, 8.0),
    ("s2", "第三点看现金流", 8.2, 12.0),
    ("s3", "想知道怎么落地就评论区留言", 12.2, 16.0),
    ("s4", "搜索主页领取完整清单", 16.2, 20.0),
    ("s5", "最后记得关注", 20.2, 24.0),
)


def write_project(pipeline):
    sentences = [
        {"id": sid, "text": text, "start_sec": start, "end_sec": end}
        for sid, text, start, end in LINES
    ]
    write_json(pipeline / "03_audio_events.json", {"sentences": sentences, "locked_duration_sec": DURATION})
    write_json(pipeline / "01_manifest.json", {"items": []})
    return sentences


def overfull_plan():
    """一句一幕全高光（100% 覆盖）的合法总谱：能过校验，但审片必须要求修。"""
    headlines = ("先问生意前提", "核对回款", "看现金流", "评论区", "完整清单", "记得关注")
    return {
        "source": "agent", "theme_profile": "business_editorial", "music_cues": [],
        "acts": [
            {
                "act_id": f"act_{index:02d}", "sentence_ids": [sid],
                "primitive": "framework_pillar" if index < 3 else "hero_intro_pop",
                "visual_copy": {"headline": headlines[index], "items": [],
                                "accent_token": f"{index + 1:02d}" if index < 3 else "",
                                "footnote": ""},
                "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 1,
                "dramatic_role": "hook" if index == 0 else "setup", "emotion_intensity": 0.82,
            }
            for index, (sid, _text, _start, _end) in enumerate(LINES)
        ],
    }


class FakeStudio:
    """把 step4~step9 / 渲染 / 质检换成确定性轻产物，只保留 V6 新增闭环在真跑。"""

    def __init__(self):
        self.step4_runs = 0
        self.step6_kwargs = []
        self.style_tag = "style_02"

    def step4(self, pipeline_dir, **_kwargs):
        self.step4_runs += 1
        write_json(pipeline_dir / "04_edl.json", {
            "clips": [], "fps": FPS, "style_id": self.style_tag, "total_duration_sec": DURATION,
        })

    def step5(self, pipeline_dir, **_kwargs):
        write_json(pipeline_dir / "05_subtitles.json", {"entries": [
            {"text": text, "start_sec": start, "end_sec": end} for _sid, text, start, end in LINES
        ]})

    def step6(self, pipeline_dir, **kwargs):
        self.step6_kwargs.append(kwargs)
        plan_path = pipeline_dir / PLAN_FILENAME
        plan = read_json(plan_path) if plan_path.is_file() else {"acts": []}
        audio = read_json(pipeline_dir / "03_audio_events.json")
        by_id = {row["id"]: row for row in audio["sentences"]}
        items = []
        for act in plan.get("acts") or []:
            if act.get("primitive") in ("plain_bridge", "clean_breath"):
                continue
            spoken = [by_id[sid] for sid in act.get("sentence_ids") or [] if sid in by_id]
            if not spoken:
                continue
            visual = act.get("visual_copy") or {}
            variant = "pillar_badge" if act["primitive"] == "framework_pillar" else "hero_lime"
            item = {
                "id": f"ks_{len(items):03d}", "variant": variant,
                "text_or_asset": visual.get("accent_token") or visual.get("headline") or "",
                "start_sec": min(s["start_sec"] for s in spoken),
                "end_sec": max(s["end_sec"] for s in spoken),
                "theme_profile": "business_editorial", "motion_mode": "slide_in_out",
                "render_backend": "web_spring", "z_layer": "foreground", "track_lane": 1,
                "font_size_units": 11.5, "base_caption": False,
                "element_choreo": "pillar_progress" if variant == "pillar_badge" else "number_roll",
                "sfx_role": None,
            }
            items.append(item)
        cues = [
            {"time_sec": round(1.0 + index * 1.4, 3), "sfx_role": "punch", "event_id": item["id"]}
            for index, item in enumerate(items)
        ][:14]
        while len(cues) < 6:
            cues.append({"time_sec": round(6.0 + len(cues) * 1.4, 3), "sfx_role": "pop", "event_id": None})
        write_json(pipeline_dir / "06_packaging.json", {
            "style_id": self.style_tag,
            "visual_elements": [], "sfx_cues": cues,
            "kinetic_stage": {
                "enabled": True, "theme_profile": "business_editorial",
                "director_source": plan.get("source", "fallback_deterministic"),
                "director_acts": plan.get("acts") or [], "items": items,
                "stats": {"total_items": len(items)}, "music_cues": plan.get("music_cues") or [],
            },
        })

    def step7(self, pipeline_dir, **_kwargs):
        write_json(pipeline_dir / "07_color_grade.json", {"ok": True})

    def step8(self, pipeline_dir, **_kwargs):
        plan_path = pipeline_dir / PLAN_FILENAME
        plan = read_json(plan_path) if plan_path.is_file() else {}
        cues = plan.get("music_cues") or []
        write_json(pipeline_dir / "08_mix_metrics.json", {
            "measured_lufs": -14.0, "true_peak_dbtp": -1.2, "mono_phase_corr": 0.98,
            "music_cue_metrics": {"applied_count": len(cues), "applied_cues": cues},
        })

    def step9(self, pipeline_dir, **_kwargs):
        write_json(pipeline_dir / "09_cover.json", {"ok": True})

    def render(self, pipeline_dir, preview=False):
        target = pipeline_dir / ("output_preview.mp4" if preview else "output_final.mp4")
        target.write_bytes(b"")
        return target

    def inspect(self, pipeline_dir, **_kwargs):
        report = {"fail_count": 0, "warn_count": 0, "checks": [{"id": "sync", "status": "PASS"}]}
        write_json(pipeline_dir / "10_qa_report.json", report)
        return report


HARNESS = {
    "run_step4": "step4", "run_step5": "step5", "run_step6": "step6", "run_step7": "step7",
    "run_step8": "step8", "run_step9": "step9", "render_mp4": "render", "inspect_video": "inspect",
}


@contextmanager
def fake_pipeline(studio):
    with ExitStack() as stack:
        for name, attr in HARNESS.items():
            stack.enter_context(patch.object(rp, name, getattr(studio, attr)))
        stack.enter_context(patch.object(rp, "write_source_review", lambda pipeline_dir: Path("source.md")))
        stack.enter_context(patch.object(rp, "_enforce_style_lock", lambda *a, **k: None))
        stack.enter_context(patch.object(rp, "generate_action_strips", lambda *a, **k: Path("strips.jpg")))
        stack.enter_context(patch.object(rp, "export_selection", lambda mode: (["jianying"], [])))
        stack.enter_context(patch.object(rp, "export_jianying_draft",
                                         lambda pipeline_dir, simulate_failure=False: {"status": "ok"}))
        stack.enter_context(patch.object(rp, "export_hyperframes_draft",
                                         lambda *a, **k: {"status": "ok"}))
        stack.enter_context(patch.object(rp, "write_decisions_made",
                                         lambda pipeline_dir, track: pipeline_dir / "decisions.md"))
        stack.enter_context(patch.object(rp, "write_review_player_html",
                                         lambda pipeline_dir: pipeline_dir / "review.html"))
        stack.enter_context(patch.object(rp, "write_delivery_summary",
                                         lambda pipeline_dir, *_a, **_k: pipeline_dir / "delivery.md"))
        yield


def run_delivery(pipeline, studio, **kwargs):
    with fake_pipeline(studio):
        return rp.run_visual_to_delivery(
            pipeline_dir=pipeline, track="creator", j_cut_frames=3, subtitle_template=None,
            lut_name=None, prefer_broll_only=False, benchmark=None, simulate_jy_fail=False,
            style_id=kwargs.pop("style_id", "style_02"), edit_profile="talking",
            agent="t013-check", **kwargs,
        )


class V65PipelineAndCriticTests(unittest.TestCase):
    def test_style02_pipeline_writes_v6_artifacts_and_critic_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            result = run_delivery(pipeline, studio)

            self.assertTrue((pipeline / "01b_composition_profile.json").is_file())
            self.assertTrue((pipeline / TASKS_FILENAME).is_file())
            plan = read_json(pipeline / PLAN_FILENAME)
            self.assertTrue(validate_director_plan(
                plan, read_json(pipeline / "03_audio_events.json"),
                read_json(pipeline / "01b_composition_profile.json"),
            )["ok"])
            self.assertEqual(plan["source"], "fallback_deterministic")

            self.assertIn("jianying_native", result)
            native = result["jianying_native"]
            self.assertTrue((Path(native["draft_dir"]) / "draft_content.json").is_file())
            self.assertGreater(native["counts"]["kinetic_stage_segments"], 0)

            self.assertTrue((pipeline / REVIEW_TASKS_FILE).is_file())
            self.assertTrue((pipeline / REVIEW_RESULT_FILE).is_file())
            review = result["aesthetic_review"]
            self.assertEqual(set(review["scores"]), set(CRITIC_DIMENSIONS))
            self.assertIn("duplicate_short_captions", review["metrics"])
            self.assertIn("element_choreo_ratio", review["metrics"])
            self.assertEqual(review["metrics"]["duplicate_short_captions"], 0)
            self.assertGreater(review["metrics"]["element_choreo_ratio"], 0.0)
            self.assertEqual(result["qa_report"]["fail_count"], 0)

    def test_reruns_preserve_the_written_plan_instead_of_rebuilding_it(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            run_delivery(pipeline, studio)
            first = (pipeline / PLAN_FILENAME).read_bytes()
            run_delivery(pipeline, studio)
            self.assertEqual((pipeline / PLAN_FILENAME).read_bytes(), first)

    def test_non_v6_styles_and_no_director_keep_the_old_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            studio.style_tag = "style_01"
            result = run_delivery(pipeline, studio, style_id="style_01")
            for name in ("01b_composition_profile.json", TASKS_FILENAME, PLAN_FILENAME,
                         REVIEW_TASKS_FILE, REVIEW_RESULT_FILE):
                self.assertFalse((pipeline / name).exists(), name)
            self.assertNotIn("jianying_native", result)
            self.assertNotIn("aesthetic_review", result)

        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            result = run_delivery(pipeline, studio, no_v6_director=True)
            for name in ("01b_composition_profile.json", TASKS_FILENAME, PLAN_FILENAME):
                self.assertFalse((pipeline / name).exists(), name)
            self.assertNotIn("jianying_native", result)
            self.assertTrue(studio.step6_kwargs)
            self.assertTrue(studio.step6_kwargs[-1]["no_v6_director"])

    def test_external_plan_is_validated_and_review_pause_stops_before_step4(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            good = pipeline / "agent_plan.json"
            write_json(good, overfull_plan())
            result = run_delivery(pipeline, studio, director_plan=str(good))
            self.assertEqual(read_json(pipeline / PLAN_FILENAME)["acts"], read_json(good)["acts"])
            self.assertEqual(studio.step4_runs, 1)
            self.assertIn("jianying_native", result)

        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            bad = pipeline / "bad_plan.json"
            write_json(bad, {
                "source": "agent", "theme_profile": "business_editorial", "music_cues": [],
                "acts": [{
                    "act_id": "act_01", "sentence_ids": [row[0] for row in LINES],
                    "primitive": "hero_intro_pop", "camera_intent": "steady_hold",
                    "visual_copy": {"headline": "腾讯集团", "items": [], "accent_token": ""},
                    "sfx_budget": 1, "dramatic_role": "hook", "emotion_intensity": 0.9,
                }],
            })
            with self.assertRaises(RuntimeError) as caught:
                run_delivery(pipeline, studio, director_plan=str(bad))
            self.assertIn("hallucinated_token", str(caught.exception))
            self.assertEqual(studio.step4_runs, 0)
            self.assertFalse((pipeline / PLAN_FILENAME).exists())
            self.assertFalse((pipeline / "04_edl.json").exists())

        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            result = run_delivery(pipeline, studio, director_review_pause=True)
            self.assertTrue(result["paused_for_director"])
            self.assertEqual(Path(result["tasks_path"]), pipeline / TASKS_FILENAME)
            self.assertTrue(Path(result["tasks_path"]).is_file())
            self.assertEqual(studio.step4_runs, 0)
            self.assertFalse((pipeline / PLAN_FILENAME).exists())

    def test_auto_critic_revise_reruns_once_from_step4_and_then_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            agent_plan = pipeline / "agent_plan.json"
            write_json(agent_plan, overfull_plan())
            result = run_delivery(
                pipeline, studio, director_plan=str(agent_plan), auto_critic_revise=True,
            )
            revision = result["critic_revision"]
            self.assertTrue(revision["applied"])
            self.assertTrue(revision["critic_revised"])
            self.assertFalse(revision["passed"] is None)
            self.assertGreater(revision["after_score"], revision["before_score"])
            self.assertGreater(revision["score_delta"], 0.5)
            self.assertEqual(studio.step4_runs, 2)
            plan = read_json(pipeline / PLAN_FILENAME)
            self.assertTrue(plan["critic_revised"])
            self.assertGreaterEqual(plan["highlight_coverage_ratio"], 0.35)
            self.assertLessEqual(plan["highlight_coverage_ratio"], 0.55)
            self.assertTrue(plan["music_cues"])

            again = run_delivery(
                pipeline, studio, director_plan=None, auto_critic_revise=True,
            )
            self.assertEqual(again["critic_revision"]["reason"], "already_revised")
            self.assertEqual(studio.step4_runs, 3)

    def test_short_duplicate_caption_is_hidden_but_long_caption_survives(self):
        audio = {
            "sentences": [
                {"id": "s1", "text": "不是被骗", "start_sec": 3.1, "end_sec": 4.6},
                {"id": "s2", "text": "看到做法先找他的前提", "start_sec": 6.5, "end_sec": 10.0},
            ],
        }
        subtitles = [
            {"text": "不是被骗", "start_sec": 3.1, "end_sec": 4.6},
            {"text": "看到做法先找他的前提", "start_sec": 6.5, "end_sec": 10.0},
        ]
        highlights = [
            {"variant": "negation_line", "text_or_asset": "✕ 不是被骗", "start_sec": 3.1,
             "end_sec": 6.07, "motion_mode": "slide_in_out", "element_choreo": "strike_slash"},
            {"variant": "hero_white", "text_or_asset": "先找他的前提", "start_sec": 6.5,
             "end_sec": 10.0, "motion_mode": "slide_in_out"},
        ]
        stats = {}
        kept = business_caption_items(audio, subtitles, FPS, highlight_items=highlights, out_stats=stats)
        self.assertEqual([item["text_or_asset"] for item in kept], ["看到做法先找他的前提"])
        self.assertEqual(stats["suppressed_duplicate_captions"], 1)
        legacy = business_caption_items(audio, subtitles, FPS)
        self.assertEqual([item["text_or_asset"] for item in legacy], ["不是被骗", "看到做法先找他的前提"])

        thin = [dict(highlights[0], start_sec=5.0, end_sec=6.07)]
        capped = business_caption_items(audio, subtitles, FPS, highlight_items=thin)
        self.assertEqual([item["text_or_asset"] for item in capped], ["不是被骗", "看到做法先找他的前提"])

    def test_store_breath_skips_the_opening_reveal_and_lands_mid_film(self):
        rows = (
            ("今日同大家来探店", 0.0, 1.0),
            ("呢间茶楼有六百平", 1.1, 3.4),
            ("沿住大厅行过去就见到后厨", 3.8, 7.0),
            ("师傅每日现包三十笼点心", 10.4, 14.0),
            ("仲有桑拿鸡同埋老火汤", 14.5, 18.0),
            ("街坊早上过来饮一壶热茶", 18.5, 22.0),
            ("下次评论区见", 22.5, 26.0),
        )
        sentences = [
            {"id": f"sent_{index:03d}", "text": text, "start_sec": start, "end_sec": end}
            for index, (text, start, end) in enumerate(rows, 1)
        ]
        audio = {"sentences": sentences, "locked_duration_sec": 26.0}
        profile = {"theme_hint": "store_vibrant", "allow_behind_person": True}
        plan = generate_deterministic_director_plan(audio, composition_profile=profile)
        self.assertEqual(plan["theme_profile"], "store_vibrant")
        reveal_acts = [
            act for act in plan["acts"]
            if act["primitive"] in ("num_anchor_dim_overlay", "list_build_gather_dim_overlay")
        ]
        self.assertTrue(reveal_acts)
        opening = min(
            (act for act in reveal_acts if act["sentence_ids"] == ["sent_002"]),
            key=lambda act: act["act_id"], default=None,
        )
        self.assertIsNotNone(opening, "开场 1s 的数字揭晓应仍在总谱里")
        drops = [cue for cue in plan["music_cues"] if cue["type"] == "silence_drop"]
        self.assertEqual(len(drops), 1)
        drop = drops[0]
        self.assertGreaterEqual(drop["start_sec"], 4.0)
        self.assertAlmostEqual(drop["end_sec"] - drop["start_sec"], 0.35)
        later = [
            act for act in reveal_acts
            if min(
                row["start_sec"] for row in sentences if row["id"] in act["sentence_ids"]
            ) >= 4.0
        ]
        self.assertTrue(later, "中段必须还有揭晓幕")
        reveal_start = min(
            row["start_sec"] for row in sentences if row["id"] in later[0]["sentence_ids"]
        )
        self.assertAlmostEqual(drop["start_sec"], reveal_start - 0.08, delta=0.02)
        self.assertTrue(validate_director_plan(plan, audio, profile)["ok"])

    def test_negation_rows_cover_the_short_caption_after_a_leading_conjunction(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            source = pipeline / "voice.mp4"
            rows = (
                ("公司倒闭那年我负债两百多万", 0.0, 3.2),
                ("不是被骗", 3.3, 4.6),
                ("也不是行情差", 4.7, 7.0),
                ("我后来反复复盘", 7.2, 10.0),
                ("第一点先问生意前提", 10.2, 14.0),
                ("讲完这些再看过去", 14.2, 17.0),
            )
            sentences = [
                {"id": f"s{index}", "text": text, "start_sec": start, "end_sec": end,
                 "source_media": str(source), "source_in_sec": start, "source_out_sec": end,
                 "start_frame": round(start * FPS), "end_frame": round(end * FPS), "keywords": []}
                for index, (text, start, end) in enumerate(rows)
            ]
            # 真片词级时间戳从「也」后的「不」开始，大字原本会晚出一截。
            for index in (1, 2):
                sentences[index]["words"] = [{
                    "text": sentences[index]["text"],
                    "start_sec": sentences[index]["start_sec"],
                    "end_sec": sentences[index]["end_sec"],
                }]
            audio = {
                "sentences": sentences, "locked_duration_sec": 17.0,
                "locked_duration_frames": round(17.0 * FPS),
                "events": [{"id": f"e{index}", "time_sec": row["start_sec"],
                            "frame": row["start_frame"], "type": "sentence_start",
                            "sentence_id": row["id"]} for index, row in enumerate(sentences)],
            }
            write_json(pipeline / "03_audio_events.json", audio)
            write_json(pipeline / "01_manifest.json", {"items": [{
                "id": "a", "role": "a_roll", "normalized_mp4": str(source), "duration_sec": 30.0,
                "sharpness_score": 460, "stability_score": 0.93,
                "faces": [{"bbox": {"x": 0.29, "y": 0.22, "w": 0.42, "h": 0.26}}],
            }]})
            profile = {
                "primary_stage_mode": "closeup_chest_stage", "allow_behind_person": False,
                "aroll_profiles": [{
                    "media_path": str(source), "allow_behind_person": False,
                    "face_area_ratio": 0.11, "safe_fg_y_norm": -0.46, "safe_center_shift_x": 0.07,
                }],
            }
            write_json(pipeline / "01b_composition_profile.json", profile)
            acts = [
                {"act_id": "act_01", "sentence_ids": ["s0", "s1", "s2"],
                 "primitive": "negation_strike",
                 "visual_copy": {"headline": "负债200万+",
                                 "items": ["✕ 不是被骗", "✕ 不是行情差"],
                                 "accent_token": "", "footnote": ""},
                 "camera_intent": "slow_push_115", "broll_policy": "lock_aroll", "sfx_budget": 2,
                 "dramatic_role": "hook", "emotion_intensity": 0.92},
                {"act_id": "act_02", "sentence_ids": ["s3"], "primitive": "clean_breath",
                 "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                 "camera_intent": "slow_push_115", "broll_policy": "lock_aroll", "sfx_budget": 0,
                 "dramatic_role": "setup", "emotion_intensity": 0.18},
                {"act_id": "act_03", "sentence_ids": ["s4"], "primitive": "framework_pillar",
                 "visual_copy": {"headline": "问生意前提", "items": [], "accent_token": "01",
                                 "footnote": ""},
                 "camera_intent": "slow_push_115", "broll_policy": "lock_aroll", "sfx_budget": 1,
                 "dramatic_role": "framework", "emotion_intensity": 0.73},
                {"act_id": "act_04", "sentence_ids": ["s5"], "primitive": "plain_bridge",
                 "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                 "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 0,
                 "dramatic_role": "setup", "emotion_intensity": 0.31},
            ]
            checked = validate_director_plan(
                {"source": "agent", "theme_profile": "business_editorial", "acts": acts,
                 "music_cues": []}, audio, profile)
            self.assertTrue(checked["ok"], checked["errors"])
            write_json(pipeline / PLAN_FILENAME, checked["sanitized_plan"])

            run_step4(pipeline, track="creator", style_id="style_02")
            run_step5(pipeline, template_id="style_02")
            package = run_step6(pipeline, style_id="style_02")
            stage = package["kinetic_stage"]
            negations = [item for item in stage["items"] if item["variant"] == "negation_line"]
            aligned = next(item for item in negations if "行情差" in item["text_or_asset"])
            self.assertEqual(aligned["start_sec"], 4.7)
            captions = [item["text_or_asset"] for item in stage["items"] if item.get("base_caption")]
            self.assertNotIn("也不是行情差", captions)
            self.assertIn("我后来反复复盘", captions)
            self.assertEqual(stage["stats"]["suppressed_duplicate_captions"], 2)

    def test_review_file_is_only_accepted_by_the_apply_action(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            review = pipeline / "review.json"
            write_json(review, {"patch_suggestions": []})
            process = subprocess.run(
                [sys.executable, "-B", str(SCRIPTS / "aesthetic_critic.py"), "package",
                 "--pipeline-dir", str(pipeline), "--review-file", str(review)],
                capture_output=True, text=True,
            )
            self.assertEqual(process.returncode, 2)
            self.assertIn("--review-file 只支持 apply", process.stderr)

    def test_store_theme_is_not_punished_for_missing_commercial_choreo(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            sentences = [
                {"id": "s0", "text": "今日同大家来探店", "start_sec": 0.0, "end_sec": 3.0},
                {"id": "s1", "text": "呢间茶楼有六百平", "start_sec": 3.2, "end_sec": 6.0},
                {"id": "s2", "text": "招牌就系桑拿鸡", "start_sec": 6.4, "end_sec": 12.0},
            ]
            write_json(pipeline / "03_audio_events.json",
                       {"sentences": sentences, "locked_duration_sec": 12.0})
            write_json(pipeline / "01b_composition_profile.json", {"allow_behind_person": True})
            write_json(pipeline / PLAN_FILENAME, {
                "source": "agent", "theme_profile": "store_vibrant",
                "music_cues": [{"type": "delay_entry", "start_sec": 0.0, "end_sec": 1.2,
                                "depth_db": -28.0, "fade_ms": 80}],
                "acts": [
                    {"act_id": "act_01", "sentence_ids": ["s0"], "primitive": "hero_intro_pop",
                     "visual_copy": {"headline": "探店", "items": [], "accent_token": "",
                                     "footnote": ""}, "camera_intent": "steady_hold",
                     "broll_policy": "lock_aroll", "sfx_budget": 1, "dramatic_role": "hook",
                     "emotion_intensity": 0.9},
                    {"act_id": "act_02", "sentence_ids": ["s1"], "primitive": "clean_breath",
                     "visual_copy": {"headline": "", "items": [], "accent_token": "",
                                     "footnote": ""}, "camera_intent": "steady_hold",
                     "broll_policy": "lock_aroll", "sfx_budget": 0, "dramatic_role": "setup",
                     "emotion_intensity": 0.15},
                    {"act_id": "act_03", "sentence_ids": ["s2"], "primitive": "plain_bridge",
                     "visual_copy": {"headline": "", "items": [], "accent_token": "",
                                     "footnote": ""}, "camera_intent": "steady_hold",
                     "broll_policy": "lock_aroll", "sfx_budget": 0, "dramatic_role": "setup",
                     "emotion_intensity": 0.3},
                ],
            })
            write_json(pipeline / "06_packaging.json", {
                "sfx_cues": [{"time_sec": value, "sfx_role": "pop"}
                             for value in (0.4, 2.0, 4.0, 6.0, 8.0, 10.0)],
                "kinetic_stage": {"items": [{
                    "variant": "hero_lime", "text_or_asset": "六百平", "start_sec": 3.2,
                    "end_sec": 6.0, "theme_profile": "store_vibrant",
                    "motion_mode": "slide_in_out", "render_backend": "web_spring",
                    "z_layer": "foreground",
                }]},
            })
            write_json(pipeline / "08_mix_metrics.json", {
                "measured_lufs": -14.0,
                "music_cue_metrics": {"applied_cues": [{"type": "delay_entry"}]},
            })
            write_json(pipeline / "10_qa_report.json", {"fail_count": 0})
            review = score_aesthetic_review(pipeline)
            self.assertEqual(review["metrics"]["element_choreo_ratio"], 0.0)
            self.assertAlmostEqual(review["scores"]["motion_craft"], 9.4, places=2)

    def test_render_only_and_critic_revision_respect_no_v6_director(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            write_json(pipeline / "04_edl.json",
                       {"clips": [], "fps": FPS, "style_id": "style_02", "total_duration_sec": DURATION})
            write_json(pipeline / "06_packaging.json", {"kinetic_stage": {"enabled": True, "items": []}})
            write_json(pipeline / PLAN_FILENAME, overfull_plan())
            studio = FakeStudio()
            with fake_pipeline(studio):
                result = rp._render_and_verify_only(
                    pipeline_dir=pipeline, track="creator", benchmark=None, simulate_jy_fail=False,
                    simulate_hf_fail=False, dialect="mandarin", store_anchor=None, require_lock=False,
                    strict_asr=False, hook_variants=1, ws=None, style_id="style_02",
                    v6_director=True, v6_disabled=True,
                )
            self.assertNotIn("jianying_native", result)
            self.assertNotIn("aesthetic_review", result)

        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            write_project(pipeline)
            studio = FakeStudio()
            result = run_delivery(pipeline, studio, no_v6_director=True, auto_critic_revise=True)
            self.assertNotIn("critic_revision", result)
            self.assertEqual(studio.step4_runs, 1)

    def test_store_plan_breathes_once_and_contrast_gets_a_headline(self):
        sentences = [
            {"id": "s0", "text": "今日同大家来探店", "start_sec": 0.0, "end_sec": 2.0},
            {"id": "s1", "text": "呢间茶楼有六百平", "start_sec": 2.2, "end_sec": 4.6},
            {"id": "s2", "text": "我哋嘅四大天王虾饺排骨凤爪同埋叉烧包", "start_sec": 5.0, "end_sec": 8.7},
            {"id": "s3", "text": "招牌就系桑拿鸡", "start_sec": 9.0, "end_sec": 11.0},
            {"id": "s4", "text": "沿住大厅行过去就见到后厨", "start_sec": 11.2, "end_sec": 16.0},
            {"id": "s5", "text": "宜家带大家坐低等菜上桌", "start_sec": 16.5, "end_sec": 20.0},
        ]
        audio = {"sentences": sentences, "locked_duration_sec": 20.0}
        profile = {"theme_hint": "store_vibrant", "allow_behind_person": True}
        plan = generate_deterministic_director_plan(audio, composition_profile=profile)
        self.assertEqual(plan["theme_profile"], "store_vibrant")
        drops = [cue for cue in plan["music_cues"] if cue["type"] == "silence_drop"]
        self.assertEqual(len(drops), 1)
        self.assertAlmostEqual(drops[0]["end_sec"] - drops[0]["start_sec"], 0.35)
        self.assertEqual(drops[0]["depth_db"], -20.0)
        self.assertEqual(drops[0]["fade_ms"], 65)
        self.assertEqual(validate_director_plan(plan, audio, profile)["ok"], True)

        contrast = [
            {"id": "c0", "text": "不是人的问题，是我们想事情的方式被固化了", "start_sec": 0.0, "end_sec": 4.0},
            {"id": "c1", "text": "接下来把这一年的事情按顺序讲一遍", "start_sec": 4.5, "end_sec": 10.0},
        ]
        business = generate_deterministic_director_plan(
            {"sentences": contrast, "locked_duration_sec": 10.0},
            style_id="style_03",
        )
        opening = business["acts"][0]
        self.assertEqual(opening["primitive"], "negation_strike")
        self.assertEqual(opening["visual_copy"]["headline"], "方式被固化了")
        self.assertTrue(validate_director_plan(
            business, {"sentences": contrast, "locked_duration_sec": 10.0},
        )["ok"])

        unified_s02 = generate_deterministic_director_plan(
            {"sentences": contrast, "locked_duration_sec": 10.0},
            style_id="style_02",
        )
        self.assertEqual(unified_s02["theme_profile"], "style_02_unified")
        self.assertEqual(unified_s02["acts"][0]["primitive"], "pain_slam_danmaku")
        self.assertTrue(validate_director_plan(
            unified_s02, {"sentences": contrast, "locked_duration_sec": 10.0},
        )["ok"])


if __name__ == "__main__":
    unittest.main()
