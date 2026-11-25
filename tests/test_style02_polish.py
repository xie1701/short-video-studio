"""style_02 定型收口回归单测：覆盖 15s 防叠字、28s/32s 清单不截字不叠压、弹幕位置与断词、以及 07s/39s 选镜过滤。"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

from common import write_json  # noqa: E402
import kinetic_choreographer as kc  # noqa: E402
import render_template_overlays as rto  # noqa: E402
import step4_build_edl as s4  # noqa: E402


class Style02PolishRegressionTests(unittest.TestCase):
    _INTRO = {"id": "s_intro", "text": "大家好我是店主牛哥", "start_sec": 0.0, "end_sec": 2.4, "start_frame": 0, "end_frame": 72}

    @staticmethod
    def _cta(start_sec: float):
        sf = int(round(start_sec * 30))
        return {"id": "s_cta", "text": "关注我评论区聊聊", "start_sec": start_sec, "end_sec": start_sec + 2.5, "start_frame": sf, "end_frame": sf + 75}

    def test_num_anchor_two_lines_have_clear_vertical_separation(self):
        ae = {
            "locked_duration_sec": 9.0,
            "sentences": [
                self._INTRO,
                {
                    "id": "s1",
                    "text": "两百平嘅厨房",
                    "start_sec": 2.8,
                    "end_sec": 5.8,
                    "start_frame": 84,
                    "end_frame": 174,
                    "words": [
                        {"text": "两", "start_sec": 2.9, "end_sec": 3.1},
                        {"text": "百", "start_sec": 3.1, "end_sec": 3.3},
                        {"text": "平", "start_sec": 3.3, "end_sec": 3.6},
                        {"text": "嘅", "start_sec": 3.7, "end_sec": 4.0},
                        {"text": "厨", "start_sec": 4.0, "end_sec": 4.5},
                        {"text": "房", "start_sec": 4.5, "end_sec": 5.1},
                    ],
                },
                self._cta(6.3),
            ],
            "events": [],
        }
        plan = kc.plan_kinetic_stage(ae, style_id="style_02")
        items = [it for it in plan["items"] if it.get("group_id") == "grp_01"]
        lime = next(it for it in items if it.get("variant") == "hero_lime")
        white = next(it for it in items if it.get("variant") == "hero_white")
        gap_px = white["center_px"][1] - lime["center_px"][1]
        min_need_px = (lime["font_size_px"] + white["font_size_px"]) * 0.5 + 24.0
        self.assertGreaterEqual(gap_px, min_need_px, (lime, white, gap_px, min_need_px))

    def test_list_items_retire_on_final_punch_and_static_items_do_not_barrel_scroll(self):
        ae = {
            "locked_duration_sec": 14.0,
            "sentences": [
                self._INTRO,
                {
                    "id": "s1",
                    "text": "核心招牌有四个1.虾饺 2.排骨 3.凤爪 4.叉烧包",
                    "start_sec": 2.8,
                    "end_sec": 7.0,
                    "start_frame": 84,
                    "end_frame": 210,
                },
                {
                    "id": "s2",
                    "text": "招牌特色炒面绝对食过嘅话都会觉得非常之好食",
                    "start_sec": 7.0,
                    "end_sec": 10.8,
                    "start_frame": 210,
                    "end_frame": 324,
                },
                self._cta(11.3),
            ],
            "events": [],
        }
        plan = kc.plan_kinetic_stage(ae, style_id="style_02")
        items = [it for it in plan["items"] if it.get("group_id") == "grp_01"]
        gather_items = [
            it for it in items
            if re.match(r"^\d+\.", str(it.get("text_or_asset") or "")) and float(it.get("x_norm") or 0.0) == 0.0
        ]
        punch = next(it for it in items if it.get("variant") == "hero_white" and it.get("jianying_anim_name") == "呐喊声波")
        self.assertTrue(gather_items, items)
        for it in gather_items:
            self.assertIsNotNone(it.get("dim_after_sec"), it)
            self.assertLessEqual(float(it["end_sec"]), float(punch["start_sec"]) + 0.12, (it, punch))
            self.assertEqual(it.get("anim_primitive"), "")
            self.assertEqual(it.get("motion_mode"), "")

        with tempfile.TemporaryDirectory() as td:
            out_mov = Path(td) / "gather.mov"
            base = rto.render_kinetic_stage_item_png(gather_items[0])
            res = rto.render_animated_overlay_mov(
                elem=gather_items[0],
                out_mov=out_mov,
                style_id="style_02",
                duration_sec=1.2,
                fps=30,
                fallback_static_img=base,
            )
            self.assertIsNotNone(res)
            _path, cw, ch = res
            # 仅静态显影 + 压暗时不再套 barrel_scroll (pad=120 -> width+240)，画布仅小留白且无倾斜裁切
            self.assertLess(cw, base.width + 64)
            self.assertLess(ch, base.height + 64)

    def test_danmaku_avoids_hairline_uses_slide_and_preserves_classifier(self):
        ae = {
            "locked_duration_sec": 11.5,
            "sentences": [
                self._INTRO,
                {
                    "id": "s1",
                    "text": "所以好多人都觉得我好傻一间六百平嘅店厨房你要花三分之一去制造",
                    "start_sec": 2.8,
                    "end_sec": 8.3,
                    "start_frame": 84,
                    "end_frame": 249,
                },
                self._cta(8.8),
            ],
            "events": [],
        }
        plan = kc.plan_kinetic_stage(ae, style_id="style_02")
        danmaku = [it for it in plan["items"] if it.get("z_layer") == "behind_person"]
        self.assertTrue(danmaku)
        for it in danmaku:
            self.assertLessEqual(float(it["y_norm"]), 0.40, it)
            self.assertNotEqual(it.get("motion_mode"), "barrel_scroll", it)
            self.assertEqual(it.get("motion_mode"), "slide_in_out", it)
            self.assertFalse(str(it.get("text_or_asset") or "").endswith("一间"), it)

    def test_signboard_wall_noise_and_deictic_stub_helpers(self):
        wall = {
            "description": "绿色宣传墙上的『凹造型ing』标语，特写到远景",
            "semantic_tags": ["店面", "宣传墙", "标语"],
        }
        hall = {
            "description": "茶楼挑高中空卡座大厅与灯笼装饰，远景",
            "semantic_tags": ["中空", "卡座", "灯笼", "造型"],
        }
        spoken = "卡座一个中空跟住有啲灯笼有啲造型岭南元素嘅"
        self.assertTrue(s4._is_signboard_wall_noise(wall, spoken))
        self.assertFalse(s4._is_signboard_wall_noise(hall, spoken))
        self.assertFalse(s4._is_signboard_wall_noise(wall, "门外宣传墙标语很醒目"))
        self.assertTrue(s4._is_deictic_pointing_stub("就系我哋嘅", 1.0))
        self.assertTrue(s4._is_deictic_pointing_stub("𠮶边就系我哋嘅", 1.1))
        self.assertFalse(s4._is_deictic_pointing_stub("鱼池到时就有好多新鲜海鲜", 1.0))
        self.assertFalse(s4._is_deictic_pointing_stub("就系我哋嘅", 1.5))

    def test_step2_rhetorical_negation_not_dropped_as_self_correction(self):
        import step2_transcribe as S2

        self.assertFalse(S2.is_self_correction_cue("不是被骗"))
        self.assertFalse(S2.is_self_correction_cue("不是人的问题"))
        self.assertFalse(S2.is_self_correction_cue("不对劲"))
        self.assertTrue(S2.is_self_correction_cue("不对，重来"))
        self.assertTrue(S2.is_self_correction_cue("不对"))

        segs = [
            {"id": "s1", "text": "公司破产那年我负债200多万", "start_sec": 0.12, "end_sec": 3.0},
            {"id": "s2", "text": "不是被骗", "start_sec": 3.48, "end_sec": 4.32},
            {"id": "s3", "text": "也不是行情差", "start_sec": 4.5, "end_sec": 5.7},
        ]
        stats = S2.filter_offcamera_and_retakes(segs)
        self.assertEqual(stats.get("self_correction_dropped", 0), 0)
        self.assertTrue(all(not s.get("is_filler") for s in segs))

    def test_step4_aroll_guards_across_breath_jump_cuts(self):
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            a_mp = str(pdir / "talk.mp4")
            sents = [
                {"id": "s1", "text": "公司破产那年我负债两百多万", "start_sec": 0.0, "end_sec": 2.40, "start_frame": 0, "end_frame": 72, "source_media": a_mp, "source_in_sec": 0.12, "source_out_sec": 2.52, "keywords": ["负债"]},
                {"id": "s2", "text": "删掉公司名改了几个数字", "start_sec": 2.57, "end_sec": 4.80, "start_frame": 77, "end_frame": 144, "source_media": a_mp, "source_in_sec": 3.40, "source_out_sec": 5.63, "keywords": ["公司"]},
                {"id": "s3", "text": "第三", "start_sec": 4.97, "end_sec": 5.45, "start_frame": 149, "end_frame": 164, "source_media": a_mp, "source_in_sec": 6.20, "source_out_sec": 6.68, "keywords": []},
                {"id": "s4", "text": "过了夫妻店那条线规矩只能自己长", "start_sec": 5.62, "end_sec": 8.40, "start_frame": 169, "end_frame": 252, "source_media": a_mp, "source_in_sec": 7.30, "source_out_sec": 10.08, "keywords": ["规矩"]},
            ]
            evts = [
                {"id": f"e{i}", "type": "sentence_start", "time_sec": s["start_sec"], "frame": s["start_frame"], "sentence_id": s["id"]}
                for i, s in enumerate(sents, 1)
            ]
            # 添加一个刚好贴在跳剪前 ~0.13s 的中间重音，过去会跨过 s3/s4 气口造成冻帧
            evts.append({"id": "e_mid", "type": "stress", "time_sec": 4.84, "frame": 145, "sentence_id": "s2"})
            evts.sort(key=lambda e: e["time_sec"])
            write_json(pdir / "01_manifest.json", {"items": [{
                "id": "a1", "role": "a_roll", "video_grade": "usable",
                "normalized_mp4": a_mp, "duration_sec": 12.0,
                "sharpness_score": 450.0, "stability_score": 0.92,
                "faces": [{"bbox": {"x": 0.3, "y": 0.15, "w": 0.35, "h": 0.35}}],
            }]})
            write_json(pdir / "03_audio_events.json", {
                "locked_duration_sec": 8.5, "locked_duration_frames": 255,
                "sentences": sents, "events": evts,
            })
            edl = s4.run_step4(pdir, track="creator", edit_profile="talking", style_id="style_02")
            for c in edl["clips"]:
                shortfall = float(c["duration_sec"]) - (float(c["source_out_sec"]) - float(c["source_in_sec"]))
                self.assertLessEqual(shortfall, 0.08, f"{c['clip_id']} 跨气口短缺冻帧: {c}")
                self.assertFalse(c.get("freeze_tail"), c)

    def test_ordinal_separation_no_aspect_split_and_problem_anchor(self):
        ph1 = kc.split_clean_phrases("删掉公司名改了几个数字", target_min=3, target_max=8)
        self.assertFalse(any(p.startswith("了") for p in ph1), ph1)
        ph2 = kc.split_clean_phrases("抄最快抄完还有种我在做事的踏实感", target_min=3, target_max=8)
        self.assertFalse(any(p.startswith("种") for p in ph2), ph2)

        ae = {
            "locked_duration_sec": 12.0,
            "sentences": [
                self._INTRO,
                {"id": "s1", "text": "钱从哪一环挣出来", "start_sec": 2.6, "end_sec": 4.4, "start_frame": 78, "end_frame": 132},
                {"id": "s2", "text": "第二", "start_sec": 4.57, "end_sec": 5.02, "start_frame": 137, "end_frame": 151},
                {"id": "s3", "text": "看到做法先找他的前提", "start_sec": 5.19, "end_sec": 7.3, "start_frame": 156, "end_frame": 219},
                {"id": "s4", "text": "包括我自己，不是人的问题，是我们想事情的方式被固化了", "start_sec": 7.5, "end_sec": 11.2, "start_frame": 225, "end_frame": 336},
                self._cta(11.7),
            ],
            "events": [],
        }
        plan = kc.plan_kinetic_stage(ae, style_id="style_02")
        all_texts = [str(it.get("text_or_asset") or "") for it in plan["items"]]
        self.assertFalse(any("挣出来第二" in t for t in all_texts), all_texts)
        red_slams = [str(it.get("text_or_asset") or "") for it in plan["items"] if it.get("variant") == "red_slam"]
        self.assertTrue(red_slams and red_slams[0] in ("不是人的问题", "人的问题"), red_slams)


if __name__ == "__main__":
    unittest.main()
