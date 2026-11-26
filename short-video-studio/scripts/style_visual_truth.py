#!/usr/bin/env python3
"""
style_visual_truth.py · 24 套风格真母本视觉基因单一真值表（V10.0）

严格对照 24 个原版风格视频（style_01 ~ style_24）逐帧核验提炼，在不篡改
style_spectrum_library.json 既有字段契约（确保 verify_style_parity 528 项全绿）
的前提下，为 step4（镜面构图/圆窗/方窗/抠像压巨字/纯黑抠像/打字机/白板）、
step6（全片常驻真母本顶栏/人设卡/拼音字/高光花字）及 render_mp4 / render_template_overlays
提供精确、互不串味的风格视觉配方。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ── 24 套真母本完整视觉基因表 ──────────────────────────────────────────────────
# 每个条目包含：
# - family: 视觉家族代号 (F1..F7)
# - label: 一句话真母本画面特征
# - top_bar: 常驻顶栏规格（仅 style_03/06/08/12 开启，覆盖整片常规口播）
# - overlay_override: 覆盖老蓝图中与真母本不符的前景包装渲染模式
# - scene_beats: 按相对时长位置 (frac) 精准挂载到 A-roll/高潮镜的标志性构图列表
#   每条 beat 为 {"frac": float, "max_dur": float, "layout": dict}
# - subtitle_patch: 仅用于最终像素级渲染对齐的轻量覆盖（不改动 templates.json）

STYLE_VISUAL_TRUTH: Dict[str, Dict[str, Any]] = {
    # ── F1 · 清爽原位高亮口播 ───────────────────────────────────────────────
    "style_01": {
        "family": "F1_clean_inline_highlight",
        "label": "极简无黑框白字 + 同句关键词原位暖黄高亮 + 开场3拍斜标 + 实拍B-roll",
        "top_bar": None,
        "overlay_override": "s01_clean_store",
        "scene_beats": [],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.15,
            "stroke_width": 4,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F2 · 单层一体化动态花字舞台（style_02 独占）─────────────────────────
    "style_02": {
        "family": "F2_unified_kinetic_stage",
        "label": "单层无黑框通透白字 + 荧光绿#BCFF00大字 + 白底暗红胶囊 + 人后弹幕",
        "top_bar": None,
        "overlay_override": None,
        "scene_beats": [],
        "subtitle_patch": {},
    },
    # ── F3 · 顶栏常驻标题 + 深度口播 ────────────────────────────────────────
    "style_03": {
        "family": "F3_persistent_top_bar_deep",
        "label": "顶部常驻双行亮黄无框标题 + 底部白/大红/鎏金粗字 + 纯黑白描边剪纸与打字机引语",
        "top_bar": {
            "mode": "s03_dual_yellow_serif",
            "y": 168,
            "full_duration": True,
            "fg_top": "#FDE047",
            "fg_bot": "#FDE047",
            "stroke": "#0B0B0D",
        },
        "overlay_override": "s03_gold_crimson_callout",
        "scene_beats": [
            {
                "frac": 0.14,
                "max_dur": 2.8,
                "layout": {
                    "mode": "vision_portrait_cutout_pip",
                    "bg_mode": "dark_charcoal",
                    "bg_hex": "#16181F",
                    "outline_px": 12,
                    "outline_hex": "#FFFFFF",
                    "quote_top_left": True,
                    "fg_offset_x": 110,
                    "fg_offset_y": 140,
                    "fg_scale": 0.86,
                },
            },
            {
                "frac": 0.36,
                "max_dur": 2.6,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#16181F",
                    "accent_hex": "#FDE047",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 585,
                    "circle_r": 215,
                },
            },
            {
                "frac": 0.58,
                "max_dur": 2.6,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s03_black_typewriter",
                    "bg_hex": "#16181F",
                    "fg_hex": "#F8FAFC",
                    "accent_hex": "#FDE047",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FDE047",
            "stroke_width": 3,
            "stroke_color": "#0A0A0A",
            "dual_line_banner": False,
        },
    },
    # ── F4 · 黑屏方窗双拼/聚焦框 ────────────────────────────────────────────
    "style_04": {
        "family": "F4_dark_box_panel",
        "label": "明亮清透白字粗黑边 + 黑底圆角方窗顶部大黄字 + 聚焦方窗下大白字单字冲击",
        "top_bar": None,
        "overlay_override": "s04_clean_white_stroke",
        "scene_beats": [
            {
                "frac": 0.26,
                "max_dur": 2.8,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s04_dual_yellow_header",
                    "bg_hex": "#15171E",
                    "accent_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.72,
                "max_dur": 2.5,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s04_focus_giant_word",
                    "bg_hex": "#15171E",
                    "accent_hex": "#FFFFFF",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "stroke_width": 4,
            "stroke_color": "#111318",
        },
    },
    # ── F1/F5 · 清透知性柔红/暖金大字 + 人像压巨字 ──────────────────────────
    "style_05": {
        "family": "F5_person_over_giant",
        "label": "清透无框白字配柔红/暖金描边粗大字 + AI抠像人像前景压巨字",
        "top_bar": None,
        "overlay_override": "s05_warm_red_gold_callout",
        "scene_beats": [
            {
                "frac": 0.16,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#D9463E",
                    "stroke_hex": "#FFFDF7",
                    "bg_blur": 4,
                    "bg_darken": -0.06,
                    "y_center": 680,
                },
            },
            {
                "frac": 0.64,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#EAB308",
                    "stroke_hex": "#FFFDF7",
                    "bg_blur": 4,
                    "bg_darken": -0.06,
                    "y_center": 720,
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#E05A47",
            "keyword_scale": 1.18,
            "stroke_width": 3,
            "stroke_color": "#1E1A18",
        },
    },
    # ── F3 · 顶部半透暖金双行栏 + 人物名片 + 暖金放大金句 ───────────────────
    "style_06": {
        "family": "F3_persistent_top_bar_deep",
        "label": "顶部全宽灰半透双行暖金宋体大标 + 开场左置人设头衔 + 暖金放大重点行",
        "top_bar": {
            "mode": "s06_translucent_warm_gold",
            "y": 168,
            "full_duration": True,
            "fg_top": "#F4E2A8",
            "fg_bot": "#F4E2A8",
            "bg_rgba": (26, 28, 32, 148),
        },
        "opening_profile_card": True,
        "overlay_override": "s06_warm_gold_serif",
        "scene_beats": [],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FAF8F2",
            "keyword_color": "#F3D37A",
            "keyword_scale": 1.18,
            "stroke_width": 3,
            "stroke_color": "#1C1915",
        },
    },
    # ── F5/F4 · 黑屏圆窗红字痛点 + 人后超大亮黄字 ───────────────────────────
    "style_07": {
        "family": "F5_person_over_giant",
        "label": "黑屏顶置圆形主播小窗配大红双行痛点 + AI抠像人后顶天亮黄巨字",
        "top_bar": None,
        "overlay_override": "s07_yellow_outline_callout",
        "scene_beats": [
            {
                "frac": 0.25,
                "max_dur": 2.8,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_red_pain",
                    "bg_hex": "#14151B",
                    "accent_hex": "#C92A2A",
                    "sub_hex": "#E03131",
                    "circle_cx": 540,
                    "circle_cy": 500,
                    "circle_r": 215,
                },
            },
            {
                "frac": 0.68,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FFD426",
                    "stroke_hex": "#18181B",
                    "bg_blur": 5,
                    "bg_darken": -0.08,
                    "y_center": 480,
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFD426",
            "keyword_scale": 1.16,
            "stroke_width": 4,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F3 · 左上角常驻斜杠双色大标 + 黑底条字幕 + 霸屏白大字 ───────────────
    "style_08": {
        "family": "F3_persistent_top_bar_deep",
        "label": "左上角常驻/斜杠/白黄双行大标 + 精致黑底条字幕 + 霸屏粗黑边白大字金句",
        "top_bar": {
            "mode": "s08_slash_dual_top_left",
            "y": 172,
            "full_duration": True,
            "fg_top": "#FFFFFF",
            "fg_bot": "#FFE14D",
        },
        "overlay_override": "s08_giant_white_impact",
        "scene_beats": [],
        "subtitle_patch": {
            "bg_box_color": "#0F1116CC",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.12,
            "stroke_width": 0,
        },
    },
    # ── F4 · 律政纪实黑屏左下圆窗 + 上区图文/红引号法条 ─────────────────────
    "style_09": {
        "family": "F4_dark_box_panel",
        "label": "纯黑纪实面板左下正圆主播小窗 + 上半区图文对比与金句引号条文 + 尾段全屏亮黄",
        "top_bar": None,
        "overlay_override": "s09_doc_gold_caption",
        "scene_beats": [
            {
                "frac": 0.16,
                "max_dur": 2.9,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_doc",
                    "bg_hex": "#15161C",
                    "accent_hex": "#C92A2A",
                    "sub_hex": "#E2D5B5",
                },
            },
            {
                "frac": 0.56,
                "max_dur": 2.9,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_yellow_quote",
                    "bg_hex": "#15161C",
                    "accent_hex": "#FACC15",
                    "sub_hex": "#F8FAFC",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFD426",
            "keyword_scale": 1.14,
            "stroke_width": 3,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F5 · 极简人后顶天大红/亮黄巨字 + 单字大白冲击 ───────────────────────
    "style_10": {
        "family": "F5_person_over_giant",
        "label": "清爽无框口播 + AI抠像人后顶天大红/亮黄粗巨字 + 底部原位关键词标黄",
        "top_bar": None,
        "overlay_override": "s10_giant_white_punch",
        "scene_beats": [
            {
                "frac": 0.24,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#D62828",
                    "stroke_hex": "#121216",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 420,
                },
            },
            {
                "frac": 0.66,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FFD426",
                    "stroke_hex": "#121216",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 440,
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE135",
            "keyword_scale": 1.14,
            "stroke_width": 4,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F5 · 开场人后白黄大字 + 一键纯黑抠像背景配顶部大白字 ────────────────
    "style_11": {
        "family": "F5_person_over_giant",
        "label": "清爽知性口播 + 开场人后白黄大字 + 金句一键切纯黑背景AI抠像配顶部居中大白字",
        "top_bar": None,
        "overlay_override": "s11_clean_studio_callout",
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 2.7,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#F8FAFC",
                    "stroke_hex": "#14161D",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 430,
                },
            },
            {
                "frac": 0.68,
                "max_dur": 2.9,
                "layout": {
                    "mode": "pure_black_cutout_pip",
                    "bg_hex": "#14161C",
                    "title_hex": "#FDE68A",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.12,
            "stroke_width": 3,
            "stroke_color": "#111318",
        },
    },
    # ── F1/F3 · 顶部常驻白+亮黄粗黑边双行大标 + 重点整句亮黄 ────────────────
    "style_12": {
        "family": "F1_clean_inline_highlight",
        "label": "顶部常驻纯白+亮黄#FFE135粗黑边双行大标题 + 常规清爽白字 + 重点句亮黄粗字",
        "top_bar": {
            "mode": "s12_white_yellow_dual_top",
            "y": 168,
            "full_duration": True,
            "fg_top": "#FFFFFF",
            "fg_bot": "#FFE135",
        },
        "overlay_override": "s12_yellow_sentence_emphasis",
        "scene_beats": [],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE135",
            "keyword_scale": 1.18,
            "stroke_width": 4,
            "stroke_color": "#0B0B0D",
        },
    },
    # ── F4 · 反诈黑底红字方窗 + 虚化圆窗绕圈问号 ───────────────────────────
    "style_13": {
        "family": "F4_dark_box_panel",
        "label": "开场黑底白红警示大字配居中白边方窗 + 虚化暗底顶圆窗绕圈问号???? + 顶部亮黄标",
        "top_bar": None,
        "overlay_override": "s13_yellow_warning_header",
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 2.8,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s13_red_warn_box",
                    "bg_hex": "#15161C",
                    "accent_hex": "#E03131",
                },
            },
            {
                "frac": 0.54,
                "max_dur": 2.9,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_blur_qmark",
                    "bg_hex": "#181920",
                    "accent_hex": "#FFE14D",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 560,
                    "circle_r": 185,
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.14,
            "stroke_width": 3,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F4 · 黑底大圆窗设问 + 纯黑打字机引语 + 人后顶天亮黄大标 ─────────────
    "style_14": {
        "family": "F4_dark_box_panel",
        "label": "开场黑底大圆窗配白黄双行设问 + 纯黑屏白字引号打字机 + AI抠像人后亮黄大标",
        "top_bar": None,
        "overlay_override": "s14_red_yellow_emphasis",
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 2.8,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#14151B",
                    "accent_hex": "#FFE14D",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 540,
                    "circle_r": 255,
                },
            },
            {
                "frac": 0.48,
                "max_dur": 2.5,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s14_black_quote_top",
                    "bg_hex": "#15171E",
                    "fg_hex": "#F8FAFC",
                    "accent_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.78,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FFD426",
                    "stroke_hex": "#121216",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 420,
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.15,
            "stroke_width": 3,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F6 · 电影感拼音大字访谈 ──────────────────────────────────────────────
    "style_15": {
        "family": "F6_cinema_pinyin_interview",
        "label": "电影感宽字距访谈 + 开场红拼音注音大白字 + 暗红巨字叠加 + 居中超大纯白粗字",
        "top_bar": None,
        "overlay_override": "s15_pinyin_and_crimson_giant",
        "scene_beats": [
            {
                "frac": 0.10,
                "max_dur": 2.8,
                "layout": {
                    "mode": "pinyin_hero_pip",
                    "variant": "s15_pinyin_split",
                    "pinyin_hex": "#EF4444",
                    "fg_hex": "#FFFFFF",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.15,
            "stroke_width": 4,
            "stroke_color": "#0A0A0A",
            "font_size": 58,
            "max_chars_per_line": 10,
        },
    },
    # ── F5 · 暗调创业重虚化打字机 + 人后暗红巨字 ────────────────────────────
    "style_16": {
        "family": "F5_person_over_giant",
        "label": "暗调创业口播 + 重度虚化暗底白字打字机引语 + AI抠像人后顶格暗红巨字",
        "top_bar": None,
        "overlay_override": "s16_glow_white_callout",
        "scene_beats": [
            {
                "frac": 0.30,
                "max_dur": 2.6,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s16_blur_dark_typewriter",
                    "bg_hex": "#14161D",
                    "fg_hex": "#FFFFFF",
                    "accent_hex": "#FACC15",
                },
            },
            {
                "frac": 0.64,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#B91C1C",
                    "stroke_hex": "#180A0A",
                    "bg_blur": 5,
                    "bg_darken": -0.10,
                    "y_center": 420,
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FDE047",
            "keyword_scale": 1.14,
            "stroke_width": 3,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F4 · 明亮黑条字幕 + 黑底方窗/上下双图对比框 ─────────────────────────
    "style_17": {
        "family": "F4_dark_box_panel",
        "label": "明亮黑条小字幕 + 黑底上方人物方窗 / 上下双实拍对比图配居中白字标签",
        "top_bar": None,
        "overlay_override": None,  # 保留既有 style_17 暖金双格单测契约
        "scene_beats": [
            {
                "frac": 0.32,
                "max_dur": 2.8,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s17_black_top_square",
                    "bg_hex": "#15161B",
                    "accent_hex": "#FDE047",
                },
            },
            {
                "frac": 0.66,
                "max_dur": 2.8,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s17_dual_compare_cards",
                    "bg_hex": "#15161B",
                    "accent_hex": "#FFFFFF",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#0C0D12CC",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFDE00",
            "keyword_scale": 1.14,
            "stroke_width": 0,
        },
    },
    # ── F5 · 户外人后暗红粗巨字 + 虚化暗底序号白条引语 ──────────────────────
    "style_18": {
        "family": "F5_person_over_giant",
        "label": "户外街拍清爽字幕 + 开场AI抠像人后暗红粗巨字 + 虚化暗底白字序号与白底红黑字引号条",
        "top_bar": None,
        "overlay_override": "s18_crimson_quote_strip",
        "scene_beats": [
            {
                "frac": 0.10,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#B91C1C",
                    "stroke_hex": "#111114",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 420,
                },
            },
            {
                "frac": 0.56,
                "max_dur": 2.7,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s18_blur_white_strip",
                    "bg_hex": "#181920",
                    "fg_hex": "#111318",
                    "accent_hex": "#DC2626",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.14,
            "stroke_width": 4,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F4 · 开场前景金黄大字 + 黑屏左下圆窗红引号大段法条 ──────────────────
    "style_19": {
        "family": "F4_dark_box_panel",
        "label": "开场前景金黄黑边大字 + 纯黑屏左下正圆头像配右侧同步字与上区红引号大段白黄条文",
        "top_bar": None,
        "overlay_override": "s19_gold_headline",
        "scene_beats": [
            {
                "frac": 0.44,
                "max_dur": 2.9,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_red_quote",
                    "bg_hex": "#14151B",
                    "accent_hex": "#E03131",
                    "sub_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.76,
                "max_dur": 2.9,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_red_quote",
                    "bg_hex": "#14151B",
                    "accent_hex": "#E03131",
                    "sub_hex": "#F8FAFC",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFD426",
            "keyword_scale": 1.14,
            "stroke_width": 4,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F4 · 国风黑底顶大圆窗暖金双行 + 纯黑打字机 + 红底白字块 ─────────────
    "style_20": {
        "family": "F4_dark_box_panel",
        "label": "纯黑屏顶部大圆窗配古风暖金双行宋体 + 纯黑打字机引语 + 全屏底部朱红底白字块",
        "top_bar": None,
        "overlay_override": "s20_crimson_block_serif",
        "scene_beats": [
            {
                "frac": 0.26,
                "max_dur": 2.9,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_guofeng_gold",
                    "bg_hex": "#161412",
                    "accent_hex": "#F3D37A",
                    "sub_hex": "#F5E6BE",
                    "circle_cx": 540,
                    "circle_cy": 540,
                    "circle_r": 250,
                },
            },
            {
                "frac": 0.56,
                "max_dur": 2.5,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s20_black_quote_top",
                    "bg_hex": "#151412",
                    "fg_hex": "#F8F3E8",
                    "accent_hex": "#F3D37A",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFDF7",
            "keyword_color": "#F3D37A",
            "keyword_scale": 1.12,
            "stroke_width": 3,
            "stroke_color": "#1C1410",
            "dual_line_banner": False,
        },
    },
    # ── F5 · 开场白描边手绘人物贴纸 + 左上黄绿大字 + 黑框人后顶天白巨字 ─────
    "style_21": {
        "family": "F5_person_over_giant",
        "label": "开场AI抠像白描边手绘人物贴纸 + 左上黄绿大字标注 + 黑边相框人后顶天纯白巨字",
        "top_bar": None,
        "overlay_override": "s21_lime_yellow_annotation",
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 2.8,
                "layout": {
                    "mode": "vision_portrait_cutout_pip",
                    "bg_mode": "soft_blur",
                    "outline_px": 14,
                    "outline_hex": "#FFFFFF",
                    "torn_paper": True,
                },
            },
            {
                "frac": 0.58,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FFFFFF",
                    "stroke_hex": "#0E1015",
                    "bg_blur": 5,
                    "bg_darken": -0.14,
                    "y_center": 420,
                    "frame_black": True,
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#D9F94D",
            "keyword_scale": 1.15,
            "stroke_width": 4,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F4/F5 · 开场人后灰白巨字+红字 + 黑屏左下圆窗配上方亮黄引号警示 ──────
    "style_22": {
        "family": "F4_dark_box_panel",
        "label": "开场人后大灰白巨字配前景大红粗字 + 纯黑屏左下正圆头像配上方大段亮黄引号警示",
        "top_bar": None,
        "overlay_override": "s22_red_callout",
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 2.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#E5E7EB",
                    "stroke_hex": "#18181B",
                    "bg_blur": 3,
                    "bg_darken": -0.04,
                    "y_center": 430,
                },
            },
            {
                "frac": 0.38,
                "max_dur": 2.9,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_yellow_quote",
                    "bg_hex": "#14151B",
                    "accent_hex": "#FACC15",
                    "sub_hex": "#FDE047",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#DC2626",
            "keyword_scale": 1.15,
            "stroke_width": 3,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F4 · 虚化暗底暖黄诗句+右下白描边剪纸 + 暗化圆窗暖黄金句 ─────────────
    "style_23": {
        "family": "F4_dark_box_panel",
        "label": "虚化暗底左侧暖黄诗句配右下白描边人物剪纸 + 暗化背景顶部大圆窗配暖黄金句",
        "top_bar": None,
        "overlay_override": "s23_warm_serif_caption",
        "scene_beats": [
            {
                "frac": 0.28,
                "max_dur": 2.9,
                "layout": {
                    "mode": "vision_portrait_cutout_pip",
                    "bg_mode": "blur_poem_left",
                    "outline_px": 12,
                    "outline_hex": "#FFFFFF",
                    "fg_offset_x": 160,
                    "fg_offset_y": 240,
                    "fg_scale": 0.78,
                    "poem_hex": "#FDE68A",
                },
            },
            {
                "frac": 0.62,
                "max_dur": 2.9,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_blur_gold",
                    "bg_hex": "#1B1B20",
                    "accent_hex": "#FDE68A",
                    "sub_hex": "#FDE68A",
                    "circle_cx": 540,
                    "circle_cy": 540,
                    "circle_r": 230,
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FAF8F2",
            "keyword_color": "#FDE68A",
            "keyword_scale": 1.10,
            "stroke_width": 3,
            "stroke_color": "#1A1815",
        },
    },
    # ── F7 · 测评多图白板 + 黑底圆角冲击窗 + 竖排大黄字 ─────────────────────
    "style_24": {
        "family": "F7_review_whiteboard",
        "label": "黑底圆角实拍小窗黄蓝大字 + 浅灰白底多图痛点评测白板 + 竖排大黄字强调",
        "top_bar": None,
        "overlay_override": "s24_vertical_yellow_punch",
        "scene_beats": [
            {
                "frac": 0.14,
                "max_dur": 2.8,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s24_rounded_impact",
                    "bg_hex": "#15171E",
                    "accent_hex": "#FFE135",
                },
            },
            {
                "frac": 0.44,
                "max_dur": 2.9,
                "layout": {
                    "mode": "review_whiteboard_pip",
                    "variant": "s24_whiteboard_multi",
                    "bg_hex": "#EEF1F5",
                    "accent_hex": "#F59E0B",
                },
            },
        ],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE135",
            "keyword_scale": 1.18,
            "stroke_width": 4,
            "stroke_color": "#0B0B0D",
        },
    },
}


def get_visual_truth(style_id: str) -> Dict[str, Any]:
    """返回指定风格的真母本视觉基因（未登记时返回空字典，保持兼容）。"""
    return dict(STYLE_VISUAL_TRUTH.get(str(style_id or "").strip(), {}))


def get_persistent_top_bar_spec(style_id: str) -> Optional[Dict[str, Any]]:
    """仅返回母本真有全片常驻顶栏的风格规格（style_03/06/08/12）。"""
    tb = get_visual_truth(style_id).get("top_bar")
    return dict(tb) if isinstance(tb, dict) else None


def extract_top_bar_two_lines(
    sentences: List[Dict[str, Any]],
    director_plan: Optional[Dict[str, Any]] = None,
    fallback_hook: str = "核心实录",
) -> Tuple[str, str]:
    """提取适合顶置双行大标的两行短标题（优先复用导演总谱 cover_copy，绝不拼乱码）。"""
    if isinstance(director_plan, dict):
        cc = director_plan.get("cover_copy") or {}
        l1 = str(cc.get("headline_top") or "").strip()
        l2 = str(cc.get("highlight_badge") or "").strip()
        if l1 and l2:
            return l1[:11], l2[:11]
    texts = [
        re.sub(r"[\s，。！？、,.!?\"“”‘’()（）]+", "", str(s.get("text") or ""))
        for s in (sentences or [])[:6]
        if str(s.get("text") or "").strip()
    ]
    joined = "".join(texts)
    if len(joined) >= 10:
        return joined[:8], joined[8:16]
    clean = re.sub(r"[\s，。！？、,.!?]+", "", fallback_hook) or "深度实拍解析"
    if len(clean) >= 7:
        mid = len(clean) // 2
        return clean[:mid], clean[mid:]
    return clean, "一线真实复盘"


_PUNCH_STOP_WORDS = frozenset({
    "一个", "一下", "一些", "这种", "那个", "这个", "呢个", "𠮶个", "什么", "乜嘢",
    "所以", "因为", "但是", "可是", "不过", "然后", "而且", "如果", "其实", "就是",
    "觉得", "可以", "应该", "非常", "特别", "这么", "那么", "还有", "已经", "我们",
    "我哋", "你们", "他们", "大家", "自己", "开始", "之后", "之前", "时候", "地方",
    "两个", "一次", "下来", "出来", "上去", "过来", "过去", "还是", "不是", "没有",
    "二一组", "大轩王啦", "括我自己", "法找他", "干分", "么活", "边就", "就系", "什么活", "包括",
})
_PUNCH_BAD_HEAD = frozenset("的了呢吗吧啊呀么嘅啲喺唔是把被在从跟比和与括")
_PUNCH_BAD_TAIL = frozenset("的了呢吗吧啊呀么嘅啲喺在从跟比和与改")


def extract_sentence_punch(sent: Optional[Dict[str, Any]], max_chars: int = 4, fallback: str = "核心真相") -> str:
    """从单句提取 2~max_chars 字的完整高冲击实词（经自然词边界校验，彻底剔除虚词、停用词与跨词截断脏片）。"""
    if not isinstance(sent, dict):
        return fallback[:max_chars]

    txt_raw = str(sent.get("text") or "").strip()
    txt = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9+]+", "", txt_raw)

    # 先用自然分词切出合法词边界集合（含单词与相邻双词组合），杜绝上游脏 keywords 如「法找他/括我自己」混入大字
    nat_toks: List[str] = []
    valid_spans: Optional[set] = None
    if txt_raw:
        try:
            import jieba  # type: ignore
            jieba.setLogLevel(60)
            nat_toks = [
                re.sub(r"[^\u4e00-\u9fffA-Za-z0-9+]+", "", t)
                for t in jieba.lcut(txt_raw)
            ]
            nat_toks = [t for t in nat_toks if t]
            vs: set = set()
            for i_t in range(len(nat_toks)):
                vs.add(nat_toks[i_t])
                if i_t + 1 < len(nat_toks):
                    vs.add(nat_toks[i_t] + nat_toks[i_t + 1])
                if i_t + 2 < len(nat_toks):
                    vs.add(nat_toks[i_t] + nat_toks[i_t + 1] + nat_toks[i_t + 2])
            valid_spans = vs
        except Exception:
            nat_toks = []
            valid_spans = None

    def _clean_cand(raw_w: str, require_boundary: bool = False) -> str:
        k = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9+]+", "", str(raw_w or ""))
        if not k:
            return ""
        if k in _PUNCH_STOP_WORDS or k[0] in _PUNCH_BAD_HEAD or k[-1] in _PUNCH_BAD_TAIL:
            return ""
        if not (2 <= len(k) <= max_chars):
            return ""
        if require_boundary and valid_spans is not None and txt:
            # 若词来自原文，则必须对齐自然词边界（数字短语放行），彻底挡住「法找他/括我自己」
            if k in txt and (not re.search(r"\d", k)) and (k not in valid_spans):
                return ""
            # 若原词根本不在原文里（如上游误识别残片「二一组」），直接拒绝
            if k not in txt:
                return ""
        return k

    # 1) 优先使用已清洗且落在自然词边界内的关键词
    for field in ("display_keywords", "keywords"):
        for kw in (sent.get(field) or []):
            hit = _clean_cand(str(kw or ""), require_boundary=True)
            if hit:
                return hit

    if not txt:
        return fallback[:max_chars]

    # 2) 从自然分词中优先挑选 2~max_chars 字高信息量实词（优先 3~4 字完整专名，其次 2 字实词）
    for min_len in (3, 2):
        for tk in nat_toks:
            if len(tk) >= min_len:
                hit = _clean_cand(tk, require_boundary=False)
                if hit:
                    return hit
    # 尝试相邻双词合成 3~max_chars 字完整短语
    for i_t in range(len(nat_toks) - 1):
        comb = nat_toks[i_t] + nat_toks[i_t + 1]
        hit = _clean_cand(comb, require_boundary=False)
        if hit:
            return hit

    txt = re.sub(r"^(?:所以|但是|然后|就是|其实|我哋|我们|呢个|那个|这个|我系|我是|一个|还有|如果|不过)", "", txt) or txt
    while len(txt) > 2 and txt[0] in _PUNCH_BAD_HEAD:
        txt = txt[1:]
    out = txt[:max_chars]
    while len(out) > 2 and out[-1] in _PUNCH_BAD_TAIL:
        out = out[:-1]
    return out if len(out) >= 2 else fallback[:max_chars]


def extract_quote_excerpt(
    sentences: List[Dict[str, Any]],
    center_idx: int,
    max_chars: int = 44,
) -> str:
    """围绕当前句提取完整通顺的金句/条文段落（供黑屏引号/打字机/诗句面板使用）。"""
    if not sentences:
        return "先看清底层前提，再谈方法落地"
    buf: List[str] = []
    total = 0
    for i in range(max(0, center_idx), min(len(sentences), center_idx + 3)):
        t = str(sentences[i].get("text") or "").strip()
        if not t:
            continue
        if total + len(t) > max_chars and buf:
            break
        buf.append(t)
        total += len(t)
    out = "，".join(s.rstrip("，。！？,.!?") for s in buf)
    return out[:max_chars] if out else "先看清底层前提，再谈方法落地"
