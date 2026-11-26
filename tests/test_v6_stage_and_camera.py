import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import find_bin, write_json
from director_plan import validate_director_plan
from kinetic_choreographer import plan_kinetic_stage
from step4_build_edl import run_step4
from step5_subtitles import run_step5
from step6_packaging import run_step6


FPS = 30


def act(index, ids, primitive, headline="", *, items=(), accent="", camera="steady_hold",
        budget=0, role="setup", broll="lock_aroll"):
    return {
        "act_id": f"act_{index:02d}", "sentence_ids": ids,
        "primitive": primitive, "visual_copy": {"headline": headline, "items": list(items), "accent_token": accent},
        "camera_intent": camera, "broll_policy": broll, "sfx_budget": budget,
        "dramatic_role": role, "emotion_intensity": 0.85 if budget else 0.15,
    }


def make_sentences(path, texts, seconds=2.8):
    return [
        {"id": f"s{idx}", "start_sec": round(idx * seconds, 4), "end_sec": round((idx + 1) * seconds, 4),
         "text": text, "source_media": str(path), "source_in_sec": round(idx * seconds, 4),
         "source_out_sec": round((idx + 1) * seconds, 4), "start_frame": round(idx * seconds * FPS),
         "end_frame": round((idx + 1) * seconds * FPS), "keywords": []}
        for idx, text in enumerate(texts)
    ]


def write_project(directory, sentences, manifest):
    duration = sentences[-1]["end_sec"]
    events = [
        {"id": f"e_{s['id']}", "time_sec": s["start_sec"], "frame": s["start_frame"],
         "type": "sentence_start", "sentence_id": s["id"]}
        for s in sentences
    ]
    events.insert(1, {"id": "stress_hook", "time_sec": 1.2, "frame": 36, "type": "stress", "sentence_id": "s0"})
    audio = {"sentences": sentences, "locked_duration_sec": duration,
             "locked_duration_frames": round(duration * FPS), "events": events}
    write_json(directory / "03_audio_events.json", audio)
    write_json(directory / "01_manifest.json", {"items": manifest})
    return audio


def aroll(path, media_id="a"):
    return {
        "id": media_id, "role": "a_roll", "normalized_mp4": str(path),
        "duration_sec": 40.0, "sharpness_score": 460, "stability_score": 0.93,
        "faces": [{"bbox": {"x": 0.29, "y": 0.22, "w": 0.42, "h": 0.26}}],
        "eye_anchor": {"cx": 0.5, "cy": 0.32},
    }


class V6StageAndCameraTests(unittest.TestCase):
    def test_business_full_pipeline_preserves_captions_and_locks_camera_to_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            source = pipeline / "voice.mp4"
            sentences = make_sentences(source, [
                "公司倒闭那年我负债两百多万", "不是被骗", "也不是行情差", "我后来反复复盘",
                "第一问生意前提", "接下来聊其中缘由", "第二看真实需求", "这是当时的情况",
                "第三看现金流", "下面说一个例子", "师徒制不是喊师傅核心是利润分钱", "讲完这些再看过去",
            ])
            sentences[1]["words"] = [{"text": "不是被骗", "start_sec": 3.10, "end_sec": 3.85}]
            audio = write_project(pipeline, sentences, [aroll(source)])
            profile = {
                "primary_stage_mode": "closeup_chest_stage", "allow_behind_person": False,
                "aroll_profiles": [{"media_path": str(source), "allow_behind_person": False,
                                    "face_area_ratio": 0.1092, "safe_fg_y_norm": -0.46,
                                    "safe_center_shift_x": 0.07}],
            }
            write_json(pipeline / "01b_composition_profile.json", profile)
            acts = [
                act(1, ["s0", "s1", "s2"], "negation_strike", "负债200万+", items=("✕ 不是被骗", "✕ 不是行情差"),
                    camera="slow_push_115", budget=3, role="hook"),
                act(2, ["s3"], "clean_breath"),
                act(3, ["s4"], "framework_pillar", "问生意前提", accent="01", budget=1, role="framework"),
                act(4, ["s5"], "plain_bridge"),
                act(5, ["s6"], "framework_pillar", "看真实需求", accent="02", budget=1, role="framework"),
                act(6, ["s7"], "clean_breath"),
                act(7, ["s8"], "framework_pillar", "看现金流", accent="03", budget=1, role="framework"),
                act(8, ["s9"], "plain_bridge"),
                act(9, ["s10"], "concept_split_card", "师徒制", items=("✕ 喊师傅", "✓ 利润分钱"),
                    camera="side_shift_card", budget=1, role="twist"),
                act(10, ["s11"], "clean_breath"),
            ]
            document = {"source": "agent", "theme_profile": "business_editorial", "acts": acts, "music_cues": []}
            result = validate_director_plan(document, audio, profile)
            self.assertTrue(result["ok"], result["errors"])
            write_json(pipeline / "03b_director_plan.json", result["sanitized_plan"])

            edl = run_step4(pipeline, track="creator", style_id="style_02")
            self.assertGreaterEqual(edl["first_3s_visual_changes"], 2)
            self.assertEqual(edl["code_broll_ratio"], 0.0)
            self.assertFalse((pipeline / "04_generated_broll").exists())
            for clip in edl["clips"]:
                self.assertEqual(clip["track_role"], "a_roll")
                self.assertEqual(clip["speed"], 1.0)
                self.assertFalse(clip["freeze_tail"])
            cameras = {clip["camera_intent"]: clip for clip in edl["clips"]}
            self.assertEqual(cameras["steady_hold"]["punch_in_zoom"], 1.0)
            self.assertEqual(cameras["steady_hold"]["vf_extra"], "")
            for intent in ("slow_push_115", "side_shift_card"):
                self.assertIn("eval=frame", cameras[intent]["vf_extra"])
                self.assertIn("crop=1080:1920", cameras[intent]["vf_extra"])

            ffmpeg = find_bin("ffmpeg")
            self.assertIsNotNone(ffmpeg)
            for intent in ("slow_push_115", "side_shift_card"):
                source_filter = "color=black:s=1080x1920:r=30,drawbox=x=360:y=580:w=125:h=125:c=white:t=fill"
                cmd = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", source_filter,
                       "-vf", cameras[intent]["vf_extra"], "-frames:v", "12", "-f", "framemd5", "-"]
                process = subprocess.run(cmd, capture_output=True, text=True, check=True)
                self.assertIn("#dimensions 0: 1080x1920", process.stdout)
                frames = [row for row in process.stdout.splitlines() if not row.startswith("#")]
                self.assertEqual(len(frames), 12)
                self.assertGreater(len({row.split(",")[-1] for row in frames}), 1)

            subtitles = run_step5(pipeline, template_id="style_02")
            package = run_step6(pipeline, style_id="style_02")
            self.assertEqual(package["visual_elements"], [])
            stage = package["kinetic_stage"]
            self.assertEqual(package["director_plan_applied"]["composition_stage_mode"], "closeup_chest_stage")
            items = stage["items"]
            highlights = [it for it in items if not it.get("base_caption")]
            self.assertTrue(all(it["z_layer"] == "foreground" for it in items))
            self.assertEqual([it["text_or_asset"] for it in items if it.get("base_caption")],
                             [line["text"] for line in subtitles["entries"]])
            self.assertEqual([it["text_or_asset"] for it in items if it["variant"] == "pillar_badge"],
                             ["01", "02", "03"])
            negations = [it for it in highlights if it["variant"] == "negation_line"]
            self.assertEqual([it["text_or_asset"] for it in negations], ["✕ 不是被骗", "✕ 不是行情差"])
            self.assertAlmostEqual(negations[0]["start_sec"], 3.1, delta=1 / FPS)
            card = next(it for it in highlights if it["variant"] == "concept_card")
            self.assertLess(card["x_norm"], 0.0)
            self.assertEqual(card["card_lines"], ["师徒制", "✕ 喊师傅", "✓ 利润分钱"])
            self.assertTrue(all(len(it["text_or_asset"].translate(str.maketrans("", "", "✕✓ ·"))) <= 7
                                for it in highlights))
            quiet = {row["act_id"] for row in acts if row["primitive"] in {"plain_bridge", "clean_breath"}}
            self.assertFalse(any(it["act_id"] in quiet for it in highlights))
            cues = package["sfx_cues"]
            self.assertLessEqual(6, len(cues))
            self.assertLessEqual(len(cues), 14)
            self.assertTrue(all(b["frame"] - a["frame"] >= 36 for a, b in zip(cues, cues[1:])))
            by_id = {it["id"]: it for it in highlights}
            for cue in cues:
                self.assertNotIn(cue["sfx_role"], {"ding", "chime"})
                self.assertNotIn(by_id[cue["event_id"]]["act_id"], quiet)
                self.assertEqual(cue["frame"], by_id[cue["event_id"]]["start_frame"])

    def test_store_preserves_legacy_layers_and_checks_the_actual_take(self):
        first, wide, close = Path("norm_0001_open.mp4"), Path("norm_0002_wide.mp4"), Path("norm_0003_close.mp4")
        sentences = make_sentences(first, [
            "今日带大家行下呢间茶楼", "这里就是热闹的大厅",
            "好多人都觉得餐饮生意难做到想放弃", "好多人觉得装修实在太贵好难撑住",
            "推荐大家过来试试", "下次评论区见",
        ], seconds=4.0)
        sentences[2]["source_media"], sentences[3]["source_media"] = str(wide), str(close)
        audio = {"sentences": sentences, "locked_duration_sec": 24}
        baseline = plan_kinetic_stage(audio)
        profile = {
            "allow_behind_person": False, "primary_stage_mode": "closeup_chest_stage",
            "aroll_profiles": [
                {"media_path": "wide.mov", "allow_behind_person": True, "face_area_ratio": 0.06},
                {"media_path": "close.mov", "allow_behind_person": False, "face_area_ratio": 0.085,
                 "safe_fg_y_norm": -0.46},
            ],
        }
        acts = [act(i + 1, [s["id"]], "clean_breath") for i, s in enumerate(sentences)]
        stage = plan_kinetic_stage(
            audio, director_plan={"theme_profile": "store_vibrant", "source": "agent", "acts": acts},
            composition_profile=profile,
        )
        self.assertEqual([it["text_or_asset"] for it in stage["items"]],
                         [it["text_or_asset"] for it in baseline["items"]])
        danmaku = [it for it in stage["items"] if it["variant"] == "danmaku_line"]
        self.assertTrue(any(it["z_layer"] == "behind_person" and it["start_sec"] < 12 for it in danmaku))
        close_items = [it for it in danmaku if 12 <= it["start_sec"] < 16]
        self.assertTrue(close_items)
        self.assertTrue(all(it["z_layer"] == "foreground" and it["y_norm"] < -0.49 for it in close_items))
        self.assertEqual(len(stage["matting_windows"]), 1)
        self.assertFalse(any(it["sfx_role"] for it in stage["items"]))


    def test_store_keeps_verified_cutaway_only_in_an_allowed_act(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            first, second, fish, wall = (pipeline / f"{name}.mp4" for name in ("open", "wide", "fish", "wall"))
            sentences = make_sentences(first, [
                "今日带大家行下呢间茶楼", "一路行入去看热闹", "鱼池的龙虾全部新鲜生猛",
                "这里还是鱼池里面的生猛龙虾", "接着介绍一下用餐体验", "我们下次在评论区再见",
            ], seconds=4)
            for sentence in sentences[2:]:
                sentence["source_media"] = str(second)
            for sentence in sentences[2:4]:
                sentence["keywords"] = ["鱼池", "龙虾"]

            def cutaway(path, tags, description):
                return {
                    "id": path.stem, "role": "b_roll", "normalized_mp4": str(path), "duration_sec": 10,
                    "sharpness_score": 495, "stability_score": 0.94, "semantic_tags": tags,
                    "scene_group": path.stem, "description": description, "best_in_sec": 0.2,
                }

            audio = write_project(pipeline, sentences, [
                aroll(first), aroll(second, "a2"),
                cutaway(fish, ["鱼池", "龙虾"], "鱼池里有生猛龙虾，水产特写。"),
                cutaway(wall, ["海报", "宣传墙"], "围挡广告牌和宣传墙。"),
            ])
            audio["events"].insert(2, {"id": "stress_before_cut", "type": "stress", "time_sec": 2.4,
                                       "frame": 72, "sentence_id": "s0"})
            write_json(pipeline / "03_audio_events.json", audio)
            plan = {
                "source": "agent", "theme_profile": "store_vibrant",
                "acts": [act(i + 1, [s["id"]], "plain_bridge",
                             broll="allow_verified_broll" if i == 2 else "lock_aroll")
                         for i, s in enumerate(sentences)],
            }
            checked = validate_director_plan(plan, audio)
            self.assertTrue(checked["ok"], checked["errors"])
            write_json(pipeline / "03b_director_plan.json", checked["sanitized_plan"])

            edl = run_step4(pipeline, track="store", style_id="style_02")
            self.assertEqual(edl["code_broll_ratio"], 0.0)
            self.assertGreaterEqual(edl["first_3s_visual_changes"], 2)
            cutaways = [clip for clip in edl["clips"] if clip["track_role"] == "b_roll"]
            self.assertEqual(len(cutaways), 1)
            chosen = cutaways[0]
            self.assertEqual(chosen["media_path"], str(fish))
            self.assertEqual(chosen["sem_match"]["tier"], "hit")
            self.assertEqual((chosen["timeline_in_sec"], chosen["timeline_out_sec"]), (8.0, 12.0))

    def test_headline_optional_and_offline_plan_available_for_profiled_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Path(directory)
            sentences = make_sentences(pipeline / "talk.mp4", ["不是人的问题", "也不是钱的问题", "这里先停一停"])
            audio = write_project(pipeline, sentences, [aroll(pipeline / "talk.mp4")])
            profile = {"allow_behind_person": False, "primary_stage_mode": "closeup_chest_stage"}
            write_json(pipeline / "01b_composition_profile.json", profile)
            fallback = plan_kinetic_stage(audio, pipeline_dir=pipeline)
            self.assertEqual(fallback["director_source"], "fallback_deterministic")
            self.assertFalse((pipeline / "03b_director_plan.json").exists())
            explicit = {
                "source": "agent", "theme_profile": "business_editorial",
                "acts": [act(1, ["s0", "s1"], "negation_strike", items=("✕ 不是人的问题", "✕ 不是钱的问题"), budget=2),
                         act(2, ["s2"], "clean_breath")],
            }
            stage = plan_kinetic_stage(audio, director_plan=explicit, composition_profile=profile)
            self.assertEqual([row["variant"] for row in stage["items"]], ["negation_line", "negation_line"])
            self.assertEqual(len([it for it in stage["items"] if it["sfx_role"]]), 2)


if __name__ == "__main__":
    unittest.main()
