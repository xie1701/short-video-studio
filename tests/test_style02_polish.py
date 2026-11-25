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


if __name__ == "__main__":
    unittest.main()
