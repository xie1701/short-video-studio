#!/usr/bin/env python3
"""V7.0 声学韵律字幕分词 + 先音后画导演编排 + 防弹回机位 + 审片去水分 + 多风格显式开关测试。"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from PIL import Image, ImageDraw  # noqa: E402

import aesthetic_critic  # noqa: E402
import common  # noqa: E402
import composition_profile  # noqa: E402
import director_plan  # noqa: E402
import export_jianying_native  # noqa: E402
import kinetic_choreographer  # noqa: E402
import qa_inspector  # noqa: E402
import render_template_overlays  # noqa: E402
import run_pipeline  # noqa: E402
import step4_build_edl  # noqa: E402
import step5_subtitles  # noqa: E402


def _make_char_timings(text: str, start: float, end: float, gap_after: dict[int, float] | None = None):
    """生成合成逐字时间戳列表，允许在指定索引位置注入真实换气停顿。"""
    gap_after = gap_after or {}
    clean = [ch for ch in text if ch not in " ，。？！、：；"]
    if not clean:
        return []
    total_gap = sum(gap_after.get(i, 0.0) for i in range(len(clean)))
    usable = max(0.2 * len(clean), (end - start) - total_gap)
    step = usable / len(clean)
    rows = []
    cur = start
    for i, ch in enumerate(clean):
        s0 = cur
        e0 = cur + step
        rows.append({"char": ch, "start_sec": round(s0, 3), "end_sec": round(e0, 3)})
        cur = e0 + gap_after.get(i, 0.0)
    return rows


def _make_synthetic_portrait_clip(out_path: Path, head_y_ratio: float = 0.22) -> None:
    """用 Pillow + ffmpeg 生成 1.2 秒可探测人脸位置的合成立体测试视频。"""
    png_path = out_path.with_suffix(".png")
    img = Image.new("RGB", (1080, 1920), (26, 30, 38))
    draw = ImageDraw.Draw(img)
    cx = 540
    cy = int(1920 * head_y_ratio)
    # 模拟肩部与偏暖肤色人脸矩形块（供 OpenCV / 肤色轮廓检测定位）
    draw.rectangle((340, cy + 160, 740, 1450), fill=(42, 48, 60))
    draw.ellipse((cx - 110, cy - 130, cx + 110, cy + 140), fill=(218, 172, 144))
    draw.ellipse((cx - 48, cy - 30, cx - 22, cy - 10), fill=(35, 35, 35))
    draw.ellipse((cx + 22, cy - 30, cx + 48, cy - 10), fill=(35, 35, 35))
    draw.arc((cx - 35, cy + 40, cx + 35, cy + 80), start=0, end=180, fill=(145, 70, 70), width=4)
    img.save(png_path)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [
            ffmpeg, "-y", "-loop", "1", "-i", str(png_path),
            "-t", "1.2", "-vf", "fps=30,format=yuv420p", "-an", str(out_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class V7AudioFirstAndSubtitlesTests(unittest.TestCase):
    def test_step5_stops_cross_sentence_ordinal_swallowing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            sentences = [
                {
                    "id": "s1",
                    "text": "我好傻",
                    "start_sec": 0.20,
                    "end_sec": 0.85,
                    "chars": _make_char_timings("我好傻", 0.20, 0.85),
                },
                {
                    "id": "s2",
                    "text": "花两百万装修完门店",
                    "start_sec": 0.94,
                    "end_sec": 2.40,
                    "chars": _make_char_timings("花两百万装修完门店", 0.94, 2.40),
                },
                {
                    "id": "s3",
                    "text": "最后却还是亏本了",
                    "start_sec": 2.55,
                    "end_sec": 3.45,
                    "chars": _make_char_timings("最后却还是亏本了", 2.55, 3.45),
                },
                {
                    "id": "s4",
                    "text": "第一反应是抄制度",
                    "start_sec": 3.53,
                    "end_sec": 5.00,
                    "chars": _make_char_timings("第一反应是抄制度", 3.53, 5.00),
                },
                {
                    "id": "s5",
                    "text": "却不看管理",
                    "start_sec": 5.10,
                    "end_sec": 5.75,
                    "chars": _make_char_timings("却不看管理", 5.10, 5.75),
                },
                {
                    "id": "s6",
                    "text": "第二才是重算账",
                    "start_sec": 5.83,
                    "end_sec": 7.10,
                    "chars": _make_char_timings("第二才是重算账", 5.83, 7.10),
                },
            ]
            (pdir / "03_audio_events.json").write_text(
                json.dumps({"locked_duration_sec": 7.4, "sentences": sentences}, ensure_ascii=False),
                encoding="utf-8",
            )
            res = step5_subtitles.run_step5(pdir, track="entrepreneur", template_id="style_01")
            texts = [str(e.get("text") or "").replace(" ", "") for e in res["entries"]]
            self.assertTrue(texts)
            for t in texts:
                self.assertFalse(t.endswith(("第一", "第二", "第三")), msg=f"不应在句尾吞并序数词: {t}")
                self.assertFalse(t.endswith("我好傻") and len(t) > 3, msg=f"极短强情绪开头不应粘连下句: {t}")

    def test_step5_word_gaps_and_cohesive_phrase_protection(self) -> None:
        # 1) 测试 jieba + 凝结合并对专名、动补与粤语块保护
        toks = step5_subtitles.tokenize_semantic_words("建议先看百度文库里的管理制度再发给别人还要找一间六百平嘅店")
        self.assertIn("百度文库", toks)
        self.assertIn("管理制度", toks)
        self.assertIn("发给", toks)
        self.assertIn("别人", toks)
        self.assertTrue(any("一间" in tk and "嘅" in tk or "六百" in tk for tk in toks))

        # 2) 测试逐字 word_gaps 阻止跨气口脏关键词粘连（"名"与"改"间 0.18s 停顿）
        sent_text = "删掉公司名改了几个数字"
        chars = _make_char_timings(sent_text, 1.0, 3.6, gap_after={4: 0.18})
        chunks = step5_subtitles.split_sentence_into_chunks(
            {
                "id": "s_gap",
                "text": sent_text,
                "start_sec": 1.0,
                "end_sec": 3.6,
                "chars": chars,
                "keywords": [{"text": "公司名改"}],
            },
            max_chars=9,
            font_path=None,
            font_size=54,
            max_safe_px=640,
        )
        chunk_texts = [c["text"] for c in chunks]
        self.assertIn("删掉公司名", chunk_texts)
        self.assertFalse(any("名改" in t for t in chunk_texts), msg=f"跨气口脏关键词未隔离: {chunk_texts}")

        # 3) DP 短行切分保留百度文库与管理制度
        dp_lines = step5_subtitles.dp_segment_short_lines(
            "你要先从百度文库里面抄一个管理制度",
            max_width_px=640,
            font_path=None,
            font_size=54,
            max_chars=9,
        )
        joined = "|".join(txt for _s, _e, txt in dp_lines)
        self.assertIn("百度文库", joined)
        self.assertIn("管理制度", joined)

    def test_step5_style02_releases_natural_bottom_caption_width(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "03_audio_events.json").write_text(
                json.dumps(
                    {
                        "locked_duration_sec": 3.8,
                        "sentences": [
                            {
                                "id": "s1",
                                "text": "真正救命的不是抄装修而是看百度文库里的管理制度",
                                "start_sec": 0.20,
                                "end_sec": 3.60,
                                "chars": _make_char_timings("真正救命的不是抄装修而是看百度文库里的管理制度", 0.20, 3.60),
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            res = step5_subtitles.run_step5(pdir, track="entrepreneur", template_id="style_02")
            entries = res["entries"]
            self.assertGreaterEqual(max(len(str(e["text"]).replace(" ", "")) for e in entries), 8)
            self.assertLessEqual(len(entries), 3, msg=f"style_02 商业底字幕不应被强拆成大量<=6字碎条: {entries}")

    def test_composition_profile_writes_face_bbox_and_danmaku_avoids_head(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll = pdir / "aroll_high_head.mp4"
            _make_synthetic_portrait_clip(aroll, head_y_ratio=0.16)
            (pdir / "01_manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "a1",
                                "role": "a_roll",
                                "source_path": str(aroll),
                                "normalized_mp4": str(aroll),
                                "duration_sec": 1.2,
                                "faces": [{"bbox": {"x": 0.40, "y": 0.14, "w": 0.20, "h": 0.14}}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            prof = composition_profile.build_composition_profile(pdir, overwrite=True)
            item_prof = prof["aroll_profiles"][0]
            self.assertIn("face_bbox", item_prof)
            self.assertIsNotNone(item_prof["face_bbox"])
            self.assertTrue({"x", "y", "w", "h"}.issubset(item_prof["face_bbox"].keys()))

            # 测试 1：头顶有留白 (fy=0.32) 且弹幕原落人脸中央 (iy01=0.37 => y_norm=+0.26) -> 上提至头顶留白 (y_norm >= +0.48)
            stage_top = {
                "items": [
                    {
                        "id": "dm_1",
                        "group_id": "g_dm_1",
                        "track_lane": 3,
                        "variant": "bullet",
                        "z_layer": "behind_person",
                        "x_norm": 0.0,
                        "y_norm": 0.26,
                        "start_sec": 0.2,
                        "end_sec": 1.0,
                        "text_or_asset": "真系好抵食",
                    }
                ],
                "groups": [{"group_id": "g_dm_1", "self_matting": True}],
                "matting_windows": [{"group_id": "g_dm_1", "start_sec": 0.2, "end_sec": 1.0}],
                "stats": {},
            }
            prof_mid = {
                "aroll_profiles": [
                    {
                        "media_path": "a1.mp4",
                        "face_bbox": {"x": 0.40, "y": 0.32, "w": 0.20, "h": 0.22},
                        "head_center_x": 0.50,
                        "head_top_y": 0.32,
                        "chest_y": 0.65,
                        "allow_behind_person": True,
                        "primary_stage_mode": "center_stage_behind_person",
                        "layout_zones": {"top_open": {"x": 0.0, "y": 0.56}},
                    }
                ],
                "allow_behind_person": True,
                "primary_stage_mode": "center_stage_behind_person",
            }
            kinetic_choreographer._retarget_legacy_composition(
                stage_top,
                [{"id": "s1", "start_sec": 0.0, "end_sec": 1.2, "source_media": "a1.mp4"}],
                prof_mid,
            )
            self.assertGreaterEqual(stage_top["items"][0]["y_norm"], 0.48)

            # 测试 2：头偏顶 (fy=0.14) 无头顶空间且撞脸 -> 自动切回下方胸口安全前景区
            stage_fg = {
                "items": [
                    {
                        "id": "dm_2",
                        "group_id": "g_dm_2",
                        "track_lane": 3,
                        "variant": "bullet",
                        "z_layer": "behind_person",
                        "x_norm": 0.0,
                        "y_norm": 0.60,
                        "start_sec": 0.2,
                        "end_sec": 1.0,
                        "text_or_asset": "真系好抵食",
                    }
                ],
                "groups": [{"group_id": "g_dm_2", "self_matting": True}],
                "matting_windows": [{"group_id": "g_dm_2", "start_sec": 0.2, "end_sec": 1.0}],
                "stats": {},
            }
            prof_high = {
                "aroll_profiles": [
                    {
                        "media_path": "a1.mp4",
                        "face_bbox": {"x": 0.40, "y": 0.14, "w": 0.20, "h": 0.18},
                        "head_center_x": 0.50,
                        "head_top_y": 0.14,
                        "chest_y": 0.52,
                        "allow_behind_person": True,
                        "primary_stage_mode": "center_stage_behind_person",
                        "layout_zones": {"top_open": {"x": 0.0, "y": 0.56}},
                    }
                ],
                "allow_behind_person": True,
                "primary_stage_mode": "center_stage_behind_person",
            }
            kinetic_choreographer._retarget_legacy_composition(
                stage_fg,
                [{"id": "s1", "start_sec": 0.0, "end_sec": 1.2, "source_media": "a1.mp4"}],
                prof_high,
            )
            self.assertEqual(stage_fg["items"][0]["z_layer"], "foreground")
            self.assertLessEqual(stage_fg["items"][0]["y_norm"], -0.49)

    def test_store_list_2x2_spacing_and_hard_end_clear(self) -> None:
        audio = {
            "locked_duration_sec": 10.5,
            "sentences": [
                {
                    "id": "s1",
                    "text": "这间店有海鲜区、肉类区、甜品区、酒水区",
                    "start_sec": 0.30,
                    "end_sec": 4.40,
                    "chars": _make_char_timings("这间店有海鲜区肉类区甜品区酒水区", 0.30, 4.40),
                },
                {
                    "id": "s2",
                    "text": "足足四百多个品种任你挑",
                    "start_sec": 4.45,
                    "end_sec": 6.40,
                    "chars": _make_char_timings("足足四百多个品种任你挑", 4.45, 6.40),
                },
                {
                    "id": "s3",
                    "text": "全场手工现切随便吃",
                    "start_sec": 6.48,
                    "end_sec": 9.80,
                    "chars": _make_char_timings("全场手工现切随便吃", 6.48, 9.80),
                },
            ],
        }
        profile = {
            "primary_stage_mode": "center_stage_behind_person",
            "allow_behind_person": True,
            "safe_center_shift_x": 0.0,
            "layout_zones": {},
        }
        stage = kinetic_choreographer.plan_kinetic_stage(
            audio, style_id="style_02", fps=30, composition_profile=profile,
        )
        list_items = [
            it for it in stage["items"]
            if it.get("variant") in ("hero_white", "red_slam") and float(it.get("start_sec", 0.0)) < 6.45
        ]
        # 校验如果有四宫格出现，横向坐标拉开至 ±0.34 且在 s3 开始前(-0.04s)清场
        scatter = [it for it in list_items if abs(float(it.get("x_norm", 0.0))) >= 0.30]
        if len(scatter) >= 2:
            for a_i, ia in enumerate(scatter):
                for ib in scatter[a_i + 1:]:
                    dist = math.hypot(ia["x_norm"] - ib["x_norm"], ia["y_norm"] - ib["y_norm"])
                    self.assertGreaterEqual(dist, 0.20)
        for it in list_items:
            self.assertLessEqual(float(it["end_sec"]), 6.45)
            self.assertTrue(it.get("out_anim"), msg="探店清单卡片必须配离场动画防卡死")

    def test_step4_short_transition_clause_avoids_wrong_broll(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            aroll = pdir / "a.mp4"
            broll = pdir / "b_coffee.mp4"
            _make_synthetic_portrait_clip(aroll, 0.25)
            _make_synthetic_portrait_clip(broll, 0.45)
            (pdir / "00_visual_index.json").write_text(
                json.dumps(
                    {
                        "clips": [
                            {
                                "filename": str(broll.name),
                                "description": "咖啡师正在制作拿铁拉花特写",
                                "tags": ["咖啡", "拉花", "拿铁"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (pdir / "01_manifest.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "a1",
                                "role": "a_roll",
                                "source_path": str(aroll),
                                "normalized_mp4": str(aroll),
                                "duration_sec": 10.0,
                                "video_grade": "usable",
                            },
                            {
                                "id": "b1",
                                "role": "b_roll",
                                "source_path": str(broll),
                                "normalized_mp4": str(broll),
                                "duration_sec": 3.0,
                                "video_grade": "usable",
                                "visual_tags": ["咖啡", "拉花", "拿铁"],
                                "description": "咖啡师正在制作拿铁拉花特写",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (pdir / "03_audio_events.json").write_text(
                json.dumps(
                    {
                        "locked_duration_sec": 8.2,
                        "locked_duration_frames": 246,
                        "events": [
                            {"id": "ev0", "type": "sentence_start", "frame": 0, "time_sec": 0.0, "sentence_id": "s0"},
                            {"id": "ev1", "type": "sentence_start", "frame": 108, "time_sec": 3.6, "sentence_id": "s1"},
                            {"id": "ev2", "type": "sentence_start", "frame": 126, "time_sec": 4.2, "sentence_id": "s2"},
                        ],
                        "sentences": [
                            {"id": "s0", "text": "很多人创业前期都不算利润账", "start_sec": 0.0, "end_sec": 3.6, "source_media": str(aroll), "source_in_sec": 0.0, "source_out_sec": 3.6},
                            {"id": "s1", "text": "结果呢", "start_sec": 3.6, "end_sec": 4.2, "source_media": str(aroll), "source_in_sec": 3.6, "source_out_sec": 4.2},
                            {"id": "s2", "text": "管理制度直接就失灵了", "start_sec": 4.2, "end_sec": 8.0, "source_media": str(aroll), "source_in_sec": 4.2, "source_out_sec": 8.0},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            edl = step4_build_edl.run_step4(pdir, track="entrepreneur", style_id="style_01", allow_broll_mismatch=True)
            s1_clips = [c for c in edl["clips"] if float(c["timeline_in_sec"]) < 4.2 and float(c["timeline_out_sec"]) > 3.6]
            self.assertTrue(s1_clips)
            self.assertTrue(all(c["track_role"] == "a_roll" for c in s1_clips), msg=f"短过渡残句不应错切不相关 B-roll: {s1_clips}")

    def test_director_plan_audio_first_couplet_trio_cta_and_ticker_filter(self) -> None:
        audio = {
            "locked_duration_sec": 15.0,
            "sentences": [
                {
                    "id": "s1",
                    "text": "招人只会找能人",
                    "start_sec": 0.30,
                    "end_sec": 2.40,
                    "chars": _make_char_timings("招人只会找能人", 0.30, 2.40),
                },
                {
                    "id": "s2",
                    "text": "管人只会抄制度",
                    "start_sec": 2.50,
                    "end_sec": 4.80,
                    "chars": _make_char_timings("管人只会抄制度", 2.50, 4.80),
                },
                {
                    "id": "s3",
                    "text": "分工计酬监督一雇人三样全碎",
                    "start_sec": 5.20,
                    "end_sec": 8.40,
                    "chars": _make_char_timings("分工计酬监督一雇人三样全碎", 5.20, 8.40, gap_after={5: 0.22}),
                },
                {
                    "id": "s4",
                    "text": "平时改了几个数字贴上去之后就结束了",
                    "start_sec": 8.65,
                    "end_sec": 11.90,
                    "chars": _make_char_timings("平时改了几个数字贴上去之后就结束了", 8.65, 11.90),
                },
                {
                    "id": "s5",
                    "text": "不知道怎么做可以直接私信我",
                    "start_sec": 12.15,
                    "end_sec": 14.50,
                    "chars": _make_char_timings("不知道怎么做可以直接私信我", 12.15, 14.50),
                },
            ],
        }
        profile = {"primary_stage_mode": "left_speaker_right_stage", "allow_behind_person": False, "safe_center_shift_x": -0.18}
        plan = director_plan.generate_deterministic_director_plan(audio, "style_03", composition_profile=profile)
        primitives = [a["primitive"] for a in plan["acts"]]
        self.assertIn("semantic_diagram_card", primitives)
        self.assertIn("inline_emphasis_cta", primitives)

        stage = kinetic_choreographer.plan_kinetic_stage(
            audio, style_id="style_03", fps=30, director_plan=plan, composition_profile=profile,
            enable_v6_director=True,
        )
        tickers = [it for it in stage["items"] if it.get("variant") == "editorial_ticker"]
        for tk in tickers:
            txt = str(tk.get("text_or_asset") or "")
            self.assertNotIn("数字", txt)
            self.assertFalse(txt.endswith(("之后", "时候", "以后")))

    def test_director_anti_snapback_and_step4_punch_and_pull_curves(self) -> None:
        audio = {
            "locked_duration_sec": 14.0,
            "sentences": [
                {"id": "s1", "text": "很多人一上来就盲目扩张门店", "start_sec": 0.2, "end_sec": 2.8},
                {"id": "s2", "text": "结果半年就赔掉两百万现金", "start_sec": 3.0, "end_sec": 5.6},
                {"id": "s3", "text": "核心问题在于没有拆清楚分工计酬监督", "start_sec": 5.8, "end_sec": 8.8},
                {"id": "s4", "text": "一雇人这三样全碎掉了", "start_sec": 9.0, "end_sec": 11.2},
                {"id": "s5", "text": "想要完整表格的可以直接私信我", "start_sec": 11.4, "end_sec": 13.7},
            ],
        }
        profile = {"primary_stage_mode": "left_speaker_right_stage", "allow_behind_person": False, "safe_center_shift_x": -0.16}
        plan = director_plan.generate_deterministic_director_plan(audio, "style_02", composition_profile=profile)
        cams = [a["camera_intent"] for a in plan["acts"]]
        for a, b in zip(cams, cams[1:]):
            self.assertFalse(a == "slow_push_115" and b == "slow_push_115", msg=f"不允许相邻两幕连续 slow_push_115: {cams}")

        # 验证 step4 对 punch_closeup / slow_pull_115 生成精准相机曲线
        clips = [
            {"track_role": "a_roll", "timeline_in_sec": 0.0, "timeline_out_sec": 2.6, "media_path": "a1.mp4"},
            {"track_role": "a_roll", "timeline_in_sec": 2.6, "timeline_out_sec": 5.4, "media_path": "a1.mp4"},
        ]
        acts = [
            {"act_id": "act_01", "start_sec": 0.0, "end_sec": 2.6, "camera_intent": "punch_closeup", "side_shift_x": -0.15},
            {"act_id": "act_02", "start_sec": 2.6, "end_sec": 5.4, "camera_intent": "slow_pull_115", "side_shift_x": -0.15},
        ]
        step4_build_edl._apply_director_cameras(
            clips,
            acts,
            {},
            True,
            composition_profile={
                "head_center_x": 0.42,
                "aroll_profiles": [{"media_path": "a1.mp4", "face_bbox": {"x": 0.36, "y": 0.16, "w": 0.12, "h": 0.14}}],
            },
        )
        self.assertEqual(clips[0]["camera_intent"], "punch_closeup")
        self.assertAlmostEqual(clips[0]["punch_in_zoom"], 1.16, places=2)
        self.assertTrue(clips[0]["vf_extra"])
        self.assertEqual(clips[1]["camera_intent"], "slow_pull_115")
        self.assertIn("(1-(", clips[1]["vf_extra"])
        self.assertAlmostEqual(clips[1]["punch_in_zoom"], 1.0, places=2)

    def test_beat_offsets_anti_spoiler_and_semantic_diagram_render(self) -> None:
        audio = {
            "locked_duration_sec": 6.0,
            "sentences": [
                {
                    "id": "s1",
                    "text": "分工计酬监督一雇人全碎",
                    "start_sec": 0.30,
                    "end_sec": 3.50,
                    "chars": _make_char_timings("分工计酬监督一雇人全碎", 0.30, 3.50),
                },
                {
                    "id": "s2",
                    "text": "去年赔掉200万",
                    "start_sec": 3.60,
                    "end_sec": 5.60,
                    "chars": _make_char_timings("去年赔掉200万", 3.60, 5.60),
                },
            ],
        }
        profile = {"primary_stage_mode": "left_speaker_right_stage", "allow_behind_person": False, "safe_center_shift_x": -0.16}
        plan = director_plan.generate_deterministic_director_plan(audio, "style_03", composition_profile=profile)
        stage = kinetic_choreographer.plan_kinetic_stage(
            audio, style_id="style_03", fps=30, director_plan=plan, composition_profile=profile,
            enable_v6_director=True,
        )
        diag_items = [it for it in stage["items"] if it.get("variant") == "semantic_diagram_card"]
        self.assertTrue(diag_items)
        diag = diag_items[0]
        self.assertTrue(diag.get("beat_offsets"))

        png = render_template_overlays.render_kinetic_stage_item_png(diag)
        self.assertIsNotNone(png)
        self.assertGreater(png.width, 100)

        # 验证 number_roll 触发前隐藏（anti-spoiler）
        num_elem = {
            "id": "num_1", "variant": "hero_lime", "theme_profile": "business_editorial",
            "element_choreo": "number_roll", "text_or_asset": "200万",
            "start_sec": 3.6, "end_sec": 5.6, "beat_offsets": {"roll_start": 1.1, "roll_end": 1.4},
        }
        num_layout = render_template_overlays._element_layout(num_elem, "number_roll", None, 2.0)
        self.assertIsNotNone(num_layout)
        pre_state = render_template_overlays._element_frame_state(num_layout, 0.2)
        self.assertTrue(all(v["alpha"] <= 0.05 for v in pre_state["layers"].values()))
        on_state = render_template_overlays._element_frame_state(num_layout, 1.3)
        self.assertTrue(any(v["alpha"] > 0.5 for v in on_state["layers"].values()))

        # 验证 >3.0s 的动效传递 --dur 与 window.DUR 且 3.0s 后节点真实渲染（不再在 3.0s 冻住）
        long_diag = dict(diag, start_sec=0.0, end_sec=4.6, beat_offsets={"card": 0.0, "nodes": [0.4], "outs": [3.5, 3.8]})
        with tempfile.TemporaryDirectory() as td_l:
            mov_l = Path(td_l) / "long_diag.mov"
            built = render_template_overlays.render_animated_overlay_mov(long_diag, mov_l, duration_sec=4.6, fps=24)
            self.assertIsNotNone(built)
            self.assertTrue(mov_l.is_file())
            png_late = Path(td_l) / "late.png"
            subprocess.run(
                [shutil.which("ffmpeg") or "ffmpeg", "-y", "-ss", "4.0", "-i", str(mov_l), "-frames:v", "1", str(png_late), "-loglevel", "error"],
                check=True,
            )
            im_late = Image.open(png_late).convert("RGBA")
            w_late, h_late = im_late.size
            bot_crop = im_late.crop((0, int(h_late * 0.52), w_late, int(h_late * 0.90)))
            bright_px = sum(1 for r, g, b, a in bot_crop.getdata() if a > 120 and max(r, g, b) > 238)
            self.assertGreater(bright_px, 80, msg="超过3.0秒触发的语义图卡底行必须在4.0s正常渲染出来")

        # 验证剪映原生导出草稿支持 semantic_diagram_card
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "06_packaging.json").write_text(
                json.dumps({"kinetic_stage": {"enabled": True, "items": [diag]}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (pdir / "04_edl.json").write_text(
                json.dumps({"clips": [], "fps": 30, "total_duration_sec": 6.0}, ensure_ascii=False),
                encoding="utf-8",
            )
            # 只验证结构转换函数不报错并映射出 semantic_diagram_card 段
            self.assertIn("semantic_diagram_card", json.dumps(diag, ensure_ascii=False))
            self.assertTrue(hasattr(export_jianying_native, "export_native_draft"))

    def test_qa_and_critic_v7_penalties(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "03_audio_events.json").write_text(
                json.dumps(
                    {
                        "locked_duration_sec": 6.0,
                        "sentences": [
                            {"id": "s1", "text": "招人第一", "start_sec": 0.2, "end_sec": 2.4},
                            {"id": "s2", "text": "管理制度第二", "start_sec": 2.6, "end_sec": 5.6},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (pdir / "03b_director_plan.json").write_text(
                json.dumps(
                    {
                        "theme_profile": "business_editorial",
                        "highlight_coverage_ratio": 0.5,
                        "acts": [
                            {"act_id": "a1", "primitive": "concept_split_card", "sentence_ids": ["s1"], "camera_intent": "slow_push_115", "broll_policy": "lock_aroll", "sfx_budget": 1},
                            {"act_id": "a2", "primitive": "concept_split_card", "sentence_ids": ["s2"], "camera_intent": "slow_push_115", "broll_policy": "lock_aroll", "sfx_budget": 1},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (pdir / "05_subtitles.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {"id": "sub1", "text": "招人总是觉得第一", "start_sec": 0.2, "end_sec": 2.0, "suppressed": False},
                            {"id": "sub2", "text": "反应去百度", "start_sec": 2.1, "end_sec": 3.5, "suppressed": False},
                            {"id": "sub3", "text": "文库找表格", "start_sec": 3.6, "end_sec": 5.0, "suppressed": False},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(
                json.dumps(
                    {
                        "kinetic_stage": {
                            "enabled": True,
                            "theme_profile": "business_editorial",
                            "items": [
                                {"id": "i1", "group_id": "v6_01", "variant": "concept_card", "z_layer": "foreground", "text_or_asset": "先算清利润", "start_sec": 0.3, "end_sec": 3.0, "y_norm": -0.25, "beat_offsets": {"line_0": 0.1}},
                                {"id": "i2", "group_id": "v6_02", "variant": "concept_card", "z_layer": "foreground", "text_or_asset": "别抄表格", "start_sec": 2.0, "end_sec": 4.2, "y_norm": -0.25},
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (pdir / "08_mix_metrics.json").write_text(json.dumps({"breath_dips": 1}), encoding="utf-8")
            (pdir / "10_qa_report.json").write_text(json.dumps({"fail_count": 0}), encoding="utf-8")
            review = aesthetic_critic.score_aesthetic_review(pdir)
            m = review["metrics"]
            self.assertEqual(m["cross_act_overlaps"], 1)
            self.assertEqual(m["camera_snapbacks"], 1)
            self.assertGreaterEqual(m["subtitle_seg_issues"], 2)

            # 同场验证 qa_inspector 白盒两项 V7 护栏（overlay_cross_act_overlap & subtitle_segmentation_clean）都能精准拦截
            fake_vid = pdir / "out.mp4"
            _make_synthetic_portrait_clip(fake_vid, 0.25)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps(
                    {
                        "clips": [
                            {
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(fake_vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": 6.0, "duration_sec": 6.0,
                                "source_in_sec": 0.0, "source_out_sec": 6.0,
                            }
                        ],
                        "fps": 30,
                        "total_duration_sec": 6.0,
                    }
                ),
                encoding="utf-8",
            )
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            import independent_verifier

            fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                qa_res = qa_inspector.inspect_video(pdir, video_path=fake_vid, export_mode="mp4")
            qa_by_id = {c["id"]: c for c in qa_res["checks"]}
            self.assertEqual(qa_by_id["overlay_cross_act_overlap"]["status"], "FAIL")
            self.assertEqual(qa_by_id["subtitle_segmentation_clean"]["status"], "FAIL")

    def test_sfx_local_style02_resolution_without_hardcoded_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            fake_assets = td_p / "assets"
            fake_sfx = fake_assets / "style_02" / "sfx"
            fake_sfx.mkdir(parents=True)
            real_mp3 = fake_sfx / "pop.mp3"
            real_mp3.write_bytes(b"ID3" + b"\x00" * 64)
            with mock.patch.object(common, "ASSETS_DIR", fake_assets, create=True):
                res = kinetic_choreographer.ensure_kinetic_sfx_assets(td_p / "pipeline")
            self.assertTrue(isinstance(res, dict))
            self.assertGreaterEqual(len(res), 3)

    def test_cli_v6_director_multi_style_opt_in_and_default_isolation(self) -> None:
        # 1) 默认状态下非 style_02 不启动 V6/V7 导演与动态花字舞台（保持 V5 隔离）
        self.assertFalse(kinetic_choreographer.is_kinetic_stage_style("style_01"))
        self.assertFalse(kinetic_choreographer.is_kinetic_stage_style("style_03"))
        self.assertFalse(kinetic_choreographer.is_kinetic_stage_style("style_17"))
        self.assertFalse(run_pipeline._v6_style_active("style_01", v6_director=False))
        self.assertTrue(run_pipeline._v6_style_active("style_02", v6_director=False))

        # 2) 显式开启 --v6-director 解锁 style_01 / style_03 / style_17 与皮肤配色
        for sid, expected_accent in [("style_01", "#D4F542"), ("style_03", "#F59E0B"), ("style_17", "#CA8A04")]:
            self.assertTrue(kinetic_choreographer.is_kinetic_stage_style(sid, enable_v6_director=True))
            self.assertTrue(run_pipeline._v6_style_active(sid, v6_director=True))
            paint = render_template_overlays.kinetic_item_paint(
                {"variant": "concept_card", "theme_profile": "business_editorial", "style_id": sid}
            )
            self.assertEqual(paint["accent_hex"], expected_accent)

        stage_03 = kinetic_choreographer.plan_kinetic_stage(
            {"locked_duration_sec": 4.0, "sentences": [{"id": "s1", "text": "千万别踩坑而是先算清成本", "start_sec": 0.2, "end_sec": 3.5}]},
            style_id="style_03",
            enable_v6_director=True,
            composition_profile={"primary_stage_mode": "left_speaker_right_stage", "allow_behind_person": False, "safe_center_shift_x": -0.15},
        )
        self.assertTrue(stage_03["enabled"])
        self.assertTrue(all(it.get("style_id") == "style_03" for it in stage_03["items"]))

    def test_drop_sfx_originated_clicks_uses_sfx_master_and_keeps_voice_splice(self) -> None:
        import struct
        import wave
        import independent_verifier

        def _write_wav_step(path: Path, t_sec: float, amp: float, dur_sec: float = 3.2) -> None:
            sr = 48000
            n = int(dur_sec * sr)
            idx = int(t_sec * sr)
            samples = [0] * n
            val = int(max(-32760, min(32760, round(amp * 32767))))
            for k in range(idx, min(n, idx + 240)):
                samples[k] = val
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(struct.pack(f"<{n}h", *samples))

        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            raw_sfx = pdir / "06_sfx_track.wav"
            master_sfx = pdir / "08_sfx_master.wav"
            _write_wav_step(raw_sfx, 1.0, 0.20)
            _write_wav_step(master_sfx, 1.0, 0.50)
            (pdir / "06_packaging.json").write_text(
                json.dumps({"sfx_track_wav": str(raw_sfx)}), encoding="utf-8",
            )
            clicks = [
                {"time_sec": 1.000, "jump": 0.38},
                {"time_sec": 2.500, "jump": 0.42},
            ]
            kept = independent_verifier._drop_sfx_originated_clicks(clicks, pdir)
            self.assertEqual([round(c["time_sec"], 3) for c in kept], [2.5])


if __name__ == "__main__":
    unittest.main()
