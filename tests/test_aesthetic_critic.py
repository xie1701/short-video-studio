import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from aesthetic_critic import (
    CRITIC_DIMENSIONS,
    REVIEW_RESULT_FILE,
    REVIEW_TASKS_FILE,
    apply_critic_patches_to_director_plan,
    build_aesthetic_review_package,
    evaluate_review_heuristics,
)
from common import find_bin, read_json, write_json
from director_plan import PLAN_FILENAME, validate_director_plan


def make_edit(pipeline, weak=False):
    lines = [
        "从这次创业复盘看真实需求", "我们重新算一笔", "师徒制看利润",
        "这时先停一停", "分清真实需求", "下面是原话", "算清楚现金流", "讲完就到这里",
    ]
    sentences = [{"id": f"s{i}", "text": text, "start_sec": i * 4, "end_sec": (i + 1) * 4}
                 for i, text in enumerate(lines)]
    short = ["看真实需求", "重新算一笔", "师徒制", "先停一停", "真实需求", "下面是原话", "现金流", "讲完"]
    acts = []
    for i, sentence in enumerate(sentences):
        active = i < 7 if weak else i % 2 == 0
        headline = lines[i] if weak and i == 0 else short[i]
        acts.append({
            "act_id": f"act_{i}", "sentence_ids": [sentence["id"]],
            "primitive": "hero_anchor_plus_danmaku" if weak and i == 0 else ("hero_intro_pop" if active else "clean_breath"),
            "visual_copy": {"headline": headline} if active else {},
            "emotion_intensity": (0.95 if i == 0 else 0.82 if i % 2 == 0 else 0.45) if active else 0.12,
            "sfx_budget": (4 if weak else 2) if active else 0,
            "dramatic_role": "hook" if i == 0 else "setup", "camera_intent": "steady_hold",
            "broll_policy": "lock_aroll",
        })
    music = [] if weak else [{"type": "delay_entry", "start_sec": 0.0, "end_sec": 2.5, "depth_db": -36.0}]
    data = {
        "03_audio_events.json": {"sentences": sentences, "locked_duration_sec": 32.0},
        "01b_composition_profile.json": {"primary_stage_mode": "closeup_chest_stage", "allow_behind_person": False},
        PLAN_FILENAME: {"acts": acts, "music_cues": music, "source": "agent", "theme_profile": "business_editorial"},
        "06_packaging.json": {
            "sfx_cues": [{"time_sec": t, "sfx_role": "punch"} for t in
                         ([idx * 0.8 for idx in range(28)] if weak else (0.0, 1.4, 8.0, 9.4, 16.0, 24.0))],
            "kinetic_stage": {"items": [
                {"variant": "hero_lime", "text_or_asset": short[i], "act_id": acts[i]["act_id"],
                 "start_sec": i * 4, "end_sec": (i + 1) * 4, "theme_profile": "business_editorial",
                 "z_layer": "behind_person" if weak and i == 0 else "foreground", "render_backend": "web_spring"}
                for i in (0, 2, 4, 6)]},
        },
        "08_mix_metrics.json": {"measured_lufs": -14.0, "music_cue_metrics": {
            "applied_count": len(music), "applied_cues": music}},
        "10_qa_report.json": {"fail_count": 1 if weak else 0, "checks": [{"id": "sync", "status": "PASS"}]},
    }
    for name, content in data.items():
        write_json(pipeline / name, content)
    return data


class AestheticCriticTests(unittest.TestCase):
    def test_package_includes_sampling_rubric_and_metrics_without_video(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            make_edit(pipeline)
            tasks = build_aesthetic_review_package(pipeline)
            self.assertEqual(tasks, read_json(pipeline / REVIEW_TASKS_FILE))
            self.assertIsNone(tasks["contact_sheet"])
            self.assertEqual(tuple(tasks["rubric"]), CRITIC_DIMENSIONS)
            opening = [s["time_sec"] for s in tasks["samples"] if s["reason"] == "opening"]
            self.assertEqual(opening, [0.5, 1.5, 2.5])
            self.assertEqual(len([s for s in tasks["samples"] if s["reason"] == "highlight"]), 4)
            self.assertEqual(len([s for s in tasks["samples"] if s["reason"] == "breath"]), 3)
            self.assertEqual(tasks["packaging"]["sfx_count"], 6)
            self.assertEqual(tasks["mix"]["measured_lufs"], -14.0)
            self.assertNotIn(str(pipeline), json.dumps(tasks))

    def test_package_extracts_real_contact_sheet_from_final_video(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            subprocess.run([
                find_bin("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=red:s=100x180:r=12:d=3",
                "-an", "-y", str(pipeline / "output_final.mp4"),
            ], check=True, capture_output=True)
            tasks = build_aesthetic_review_package(pipeline, out_image_name="sheet.jpg")
            self.assertEqual(tasks["contact_sheet"], "sheet.jpg")
            self.assertTrue(all(s["extracted"] for s in tasks["samples"]))
            with Image.open(pipeline / "sheet.jpg") as result:
                self.assertEqual(result.size, (750, 432))
                red, green, blue = result.getpixel((125, 180))
                self.assertGreater(red, 200)
                self.assertLess(max(green, blue), 40)

    def test_offline_scores_separate_good_and_overloaded_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good, bad = root / "good", root / "bad"
            make_edit(good)
            make_edit(bad, weak=True)
            clean, crowded = evaluate_review_heuristics(good), evaluate_review_heuristics(bad)
            self.assertEqual(clean, read_json(good / REVIEW_RESULT_FILE))
            self.assertGreaterEqual(clean["mean_score"], 8.5)
            self.assertTrue(clean["passed"])
            self.assertLess(crowded["mean_score"], clean["mean_score"])
            self.assertFalse(crowded["passed"])
            self.assertGreater(crowded["metrics"]["max_headline_chars"], 10)
            self.assertEqual(crowded["metrics"]["sfx_count"], 28)
            self.assertEqual(crowded["metrics"]["behind_person_violations"], 1)
            self.assertEqual({patch["type"] for patch in crowded["patch_suggestions"]}, {
                "shorten_visual_copy", "convert_to_clean_breath", "change_primitive",
                "reduce_sfx_budget", "add_silence_drop",
            })

    def test_validated_revision_is_one_shot_and_improves_next_review(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            data = make_edit(pipeline, weak=True)
            before = evaluate_review_heuristics(pipeline)
            revised = apply_critic_patches_to_director_plan(pipeline)
            self.assertTrue(revised["applied"], revised)
            plan = read_json(pipeline / PLAN_FILENAME)
            checked = validate_director_plan(plan, data["03_audio_events.json"], data["01b_composition_profile.json"])
            self.assertTrue(checked["ok"], checked["errors"])
            self.assertTrue(plan["critic_revised"])
            self.assertLessEqual(plan["highlight_coverage_ratio"], 0.55)
            self.assertGreaterEqual(plan["highlight_coverage_ratio"], 0.35)
            self.assertEqual(plan["music_cues"][0]["type"], "silence_drop")
            self.assertEqual(apply_critic_patches_to_director_plan(pipeline)["reason"], "already_revised")
            self.assertEqual(plan, read_json(pipeline / PLAN_FILENAME))
            after = evaluate_review_heuristics(pipeline)
            self.assertGreater(after["mean_score"], before["mean_score"])

    def test_unsupported_text_cannot_partially_modify_the_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            data = make_edit(pipeline)
            attempt = {"patch_suggestions": [
                {"type": "add_silence_drop", "act_id": "act_2"},
                {"type": "shorten_visual_copy", "act_id": "act_0", "visual_copy": {"headline": "腾讯集团"}},
            ]}
            result = apply_critic_patches_to_director_plan(pipeline, attempt)
            self.assertFalse(result["applied"])
            self.assertEqual(result["reason"], "validation_failed")
            self.assertEqual(read_json(pipeline / PLAN_FILENAME), data[PLAN_FILENAME])

    def test_benchmark_smoke_outputs_comparable_json_and_markdown_without_paths(self):
        script = ROOT / "dev_tools" / "bench_talking_head.py"
        result = subprocess.run([sys.executable, str(script), "--smoke", "--format", "json"],
                                capture_output=True, text=True, check=True)
        rows = json.loads(result.stdout)["projects"]
        self.assertEqual(len({row["project"] for row in rows}), 3)
        self.assertTrue(all(row["passed"] and row["qa_fail_count"] == 0 for row in rows))
        self.assertNotIn(str(tempfile.gettempdir()), result.stdout)
        markdown = subprocess.run([sys.executable, str(script), "--smoke"],
                                  capture_output=True, text=True, check=True).stdout
        self.assertIn("| project | qa_fail_count |", markdown)
        self.assertIn("粤语探店", markdown)


if __name__ == "__main__":
    unittest.main()
