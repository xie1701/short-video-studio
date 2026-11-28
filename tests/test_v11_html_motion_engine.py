#!/usr/bin/env python3
"""V11.0 30fps HTML 真动效引擎与 24 套原母本纠偏基因回归单测。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import motion_html_renderer as mhr  # noqa: E402
import style_visual_truth as svt  # noqa: E402


class V11HtmlMotionEngineTests(unittest.TestCase):
    def test_corrected_style_truth_recipes_match_reference_videos(self) -> None:
        # style_15 纠偏为纸底大纲清单白板（paper_outline_pip）
        s15_modes = [b["layout"]["mode"] for b in svt.get_visual_truth("style_15")["scene_beats"]]
        self.assertIn("paper_outline_pip", s15_modes)
        self.assertGreaterEqual(s15_modes.count("paper_outline_pip"), 3)

        # style_05 纠偏：绝不误用人后巨字，改用居中黑屏画中画 + 胸口立体花字
        s05_modes = [b["layout"]["mode"] for b in svt.get_visual_truth("style_05")["scene_beats"]]
        self.assertNotIn("person_behind_giant_text_pip", s05_modes)
        self.assertIn("framed_box_pip", s05_modes)

        # style_09 高占比纪实双区：至少 5 个贯穿全片的双区面板
        s09_beats = svt.get_visual_truth("style_09")["scene_beats"]
        self.assertGreaterEqual(len(s09_beats), 5)
        self.assertIn("bottom_left_dossier", [b["layout"]["variant"] for b in s09_beats])

        # 全部 24 套均具备 diagram_theme 以支撑商业口播风格自适应图解
        for i in range(1, 25):
            sid = f"style_{i:02d}"
            cfg = svt.get_visual_truth(sid)
            self.assertIn("diagram_theme", cfg, sid)

    def test_panel_and_callout_specs_and_soft_feather_mask(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            mask_p = mhr.soft_circle_mask_png(p, 120, feather_px=14)
            self.assertTrue(mask_p.is_file())
            with Image.open(mask_p) as im:
                self.assertEqual(im.size, (240, 240))
                # 中心纯白，外角纯黑，边缘存在半透明柔和羽化过渡像素
                self.assertGreater(im.getpixel((120, 120)), 245)
                self.assertEqual(im.getpixel((0, 0)), 0)
                hist = im.histogram()
                mid_vals = sum(hist[20:235])
                self.assertGreater(mid_vals, 500, "圆窗遮罩必须含柔和羽化过渡而非锯齿硬边")

            dummy_mov = p / "dummy.mov"
            dummy_mov.write_bytes(b"0" * 4096)
            for mode, variant in (
                ("dark_typewriter_card_pip", "s16_blur_dark_typewriter"),
                ("dark_typewriter_card_pip", "s03_black_typewriter"),
                ("paper_outline_pip", "s15_paper_outline"),
                ("review_whiteboard_pip", "s24_whiteboard_multi"),
                ("concept_diagram_pip", "compare"),
                ("dark_circle_panel_pip", "bottom_left_red_quote"),
                ("dark_circle_panel_pip", "top_blur_qmark"),
                ("framed_box_pip", "s04_dual_yellow_header"),
            ):
                lay = {"mode": mode, "variant": variant, "panel_title": "核心前提", "quote_text": "先找前提再落地"}
                spec = mhr.build_panel_job_spec(lay, 3.0, style_id="style_09")
                self.assertIsNotNone(spec, (mode, variant))
                cmd = mhr.build_html_motion_panel_cmd(
                    ffmpeg_bin="ffmpeg",
                    media_path=str(p / "in.mp4"),
                    src_in=0.0,
                    src_avail=3.0,
                    base_vf_chain="scale=1080:1920",
                    pip_layout=lay,
                    rendered_bg_or_ov=dummy_mov,
                    clip_frames=90,
                    fps=30,
                    out_mp4=p / f"{mode}.mp4",
                )
                self.assertIsNotNone(cmd, (mode, variant))
                self.assertIn("-filter_complex", cmd)

            for sid in ("style_05", "style_06", "style_08", "style_12", "style_15", "style_20", "style_21", "style_22", "style_24"):
                co = mhr.build_callout_job_spec({"kind": "center_burst_title", "text_or_asset": "核心突破"}, sid, 2.2)
                self.assertIsNotNone(co, sid)
                self.assertTrue(co["alpha"], sid)

    def test_step9_gbro_i2i_cover_direct_passthrough(self) -> None:
        import json
        import step9_title_cover as s9

        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            cov_src = p / "gbro_sample_3x4.jpg"
            # 造一张高对比度 3:4 模拟图生图封面
            im = Image.new("RGB", (900, 1200), (14, 22, 38))
            for y in range(180, 420):
                for x in range(120, 780):
                    if (x + y) % 3 == 0:
                        im.putpixel((x, y), (255, 224, 64))
            im.save(cov_src, quality=92)

            (p / "03_audio_events.json").write_text(json.dumps({
                "sentences": [{"id": 1, "start_sec": 0.0, "end_sec": 3.0, "text": "先定前提再谈制度落地", "keywords": ["前提", "制度"]}],
                "total_duration_sec": 3.0,
            }, ensure_ascii=False), encoding="utf-8")
            (p / "04_edl.json").write_text(json.dumps({"style_id": "style_09", "clips": []}, ensure_ascii=False), encoding="utf-8")
            (p / "03b_director_plan.json").write_text(json.dumps({
                "style_id": "style_09",
                "gbro_cover_3x4_path": str(cov_src),
                "cover_copy": {"headline_top": "先定前提", "highlight_badge": "再谈落地"},
            }, ensure_ascii=False), encoding="utf-8")

            res = s9.run_step9(p, track="creator", style_id="style_09")
            self.assertEqual(res.get("cover_mode"), "gbro_i2i_generated")
            self.assertTrue((p / "09_cover_3x4.jpg").is_file())
            self.assertTrue((p / "09_cover.jpg").is_file())
            with Image.open(p / "09_cover_3x4.jpg") as c34:
                self.assertEqual(c34.size, (1080, 1440))
            with Image.open(p / "09_cover.jpg") as c916:
                self.assertEqual(c916.size, (1080, 1920))

    def test_v11_director_planner_covers_all_24_styles_dual_tracks(self) -> None:
        import director_plan  # noqa: E402
        dev_dir = str(ROOT / "dev_tools")
        if dev_dir not in sys.path:
            sys.path.insert(0, dev_dir)
        import v11_director_planner as v11dp  # noqa: E402

        biz_audio = {
            "locked_duration_sec": 56.0,
            "sentences": [
                {"id": f"b_{i+1:02d}", "text": txt, "start_sec": i * 2.8, "end_sec": i * 2.8 + 2.5, "keywords": kws}
                for i, (txt, kws) in enumerate([
                    ("很多人创业第一年就把现金流耗光了", ["现金流"]),
                    ("不是因为产品不好，也不是行情太差", ["产品", "行情"]),
                    ("而是照搬大公司的制度贴在墙上", ["制度"]),
                    ("发给团队之后根本没人真正执行", ["执行"]),
                    ("第一，先算清楚单店到底靠什么活下来", ["单店"]),
                    ("第二，看到别人的做法先找背后的前提", ["前提"]),
                    ("第三，把利润分配和岗位职责写明白", ["利润分配"]),
                    ("没有利益绑定前提，所有规矩都是口号", ["利益绑定"]),
                    ("两人合伙时沟通成本几乎为零", ["沟通成本"]),
                    ("一旦开始雇人，分工和监督就会失灵", ["分工", "监督"]),
                    ("我们复盘过十几个真实门店案例", ["复盘"]),
                    ("真正活下来的团队都有自建机制", ["自建机制"]),
                    ("先看清成本结构，再决定要不要扩店", ["成本结构"]),
                    ("毛利和复购才是实体生意的护城河", ["毛利", "复购"]),
                    ("不要盲目迷信别人现成的管理模板", ["管理模板"]),
                    ("把每一笔账算透比什么口号都管用", ["算透"]),
                    ("今天把这三条自检清单分享给大家", ["自检清单"]),
                    ("有疑问可以在评论区留言我们一起拆解", ["评论区"]),
                ])
            ],
        }
        sto_audio = {
            "locked_duration_sec": 56.0,
            "sentences": [
                {"id": f"s_{i+1:02d}", "text": txt, "start_sec": i * 2.8, "end_sec": i * 2.8 + 2.5, "keywords": kws}
                for i, (txt, kws) in enumerate([
                    ("今日带大家来探访一家现做老火靓汤馆", ["老火靓汤"]),
                    ("老板直接花了两百万打造六百平空间", ["两百万", "六百平"]),
                    ("其中两百平全部用来做开放式明档厨房", ["明档厨房"]),
                    ("食客一进门就能看到师傅现场现切", ["现切"]),
                    ("店里四大招牌包括脆皮烧鹅、桑拿鸡、鲜虾饺和叉烧包", ["脆皮烧鹅", "桑拿鸡"]),
                    ("烧鹅每天凌晨现烤出炉皮脆肉嫩", ["烧鹅"]),
                    ("桑拿鸡选用山上放养走地鸡原汁原味", ["走地鸡"]),
                    ("还有现捞活海鲜池每天新鲜直供", ["活海鲜池"]),
                    ("早上主打纯手工广式早茶点心", ["早茶点心"]),
                    ("中午和晚上专门做明火现蒸桑拿鸡", ["现蒸"]),
                    ("大厅卡座宽敞而且灯光氛围很舒服", ["卡座"]),
                    ("带家人朋友聚餐完全不会觉得拥挤", ["聚餐"]),
                    ("虽然很多人觉得厨房占三分之一太浪费", ["三分之一"]),
                    ("但只有后厨够大才能保证每道菜现点现做", ["现点现做"]),
                    ("拒绝预制菜还原街坊记忆里的老味道", ["老味道"]),
                    ("整体性价比在周边商圈非常有竞争力", ["性价比"]),
                    ("想试招牌烧鹅和桑拿鸡的朋友别错过", ["招牌烧鹅"]),
                    ("评论区告诉我你最想先尝哪一道菜", ["评论区"]),
                ])
            ],
        }
        sto_inv = {
            "clips": [
                {"filename": "goose.mov", "suggested_role": "b_roll_candidate", "description": "金黄脆皮烧鹅切盘特写", "tags": ["烧鹅", "脆皮"]},
                {"filename": "chicken.mov", "suggested_role": "b_roll_candidate", "description": "热气腾腾现蒸桑拿鸡", "tags": ["桑拿鸡", "蒸汽"]},
                {"filename": "shrimp.mov", "suggested_role": "b_roll_candidate", "description": "水晶虾饺竹笼特写", "tags": ["虾饺", "早茶"]},
            ]
        }

        biz_titles = set()
        sto_titles = set()
        biz_gbro = set()
        sto_gbro = set()
        for i in range(1, 25):
            sid = f"style_{i:02d}"
            for audio, inv, title_bucket, gbro_bucket in (
                (biz_audio, None, biz_titles, biz_gbro),
                (sto_audio, sto_inv, sto_titles, sto_gbro),
            ):
                plan = v11dp.build_director_plan_for_audio(audio, sid, inventory=inv)
                chk = director_plan.validate_director_plan(plan, audio)
                self.assertTrue(chk["ok"], (sid, chk["errors"]))
                cov = plan.get("cover_copy") or {}
                pair = (cov.get("headline_top"), cov.get("highlight_badge"))
                self.assertTrue(all(pair), (sid, cov))
                title_bucket.add(pair)
                gbro_bucket.add(cov.get("gbro_style"))
                subs = plan.get("semantic_subtitles") or []
                self.assertGreaterEqual(len(subs), 18, (sid, len(subs)))
                self.assertTrue(all(len(str(x.get("text") or "")) >= 4 for x in subs), sid)
                self.assertFalse(
                    any(str(x.get("text") or "").rstrip("，。！？")[-1:] in "的嘅地得之把被从向给和与在喺" for x in subs),
                    (sid, subs),
                )
                beats = plan.get("style_scene_beats") or []
                if sid in ("style_01", "style_02", "style_06", "style_08"):
                    self.assertEqual(beats, [], sid)
                else:
                    self.assertTrue(beats, sid)
                for co in (plan.get("style_callouts") or []):
                    self.assertGreaterEqual(float(co["end_sec"]) - float(co["start_sec"]), 1.5, (sid, co))
        self.assertTrue(biz_titles.isdisjoint(sto_titles), "BIZ 与 STO 封面标题必须贴合各自语料互不串味")
        self.assertGreaterEqual(len(biz_gbro), 2)
        self.assertGreaterEqual(len(sto_gbro), 2)


if __name__ == "__main__":
    unittest.main()
