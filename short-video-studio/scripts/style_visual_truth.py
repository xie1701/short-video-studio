#!/usr/bin/env python3
"""
style_visual_truth.py · 24 套风格真母本视觉基因单一真值表（V11.0 逐帧核验纠偏版）

严格对照 24 个原版风格视频（style_01 ~ style_24）全片等距 8 帧亲眼核验重编，
在不篡改 style_spectrum_library.json 既有字段契约（确保 verify_style_parity 528 项全绿）
的前提下，为 step4（30fps HTML 真动效面板/纸底大纲/杂志白板/柔和羽化圆窗/打字机/方窗/抠像压巨字）、
step6（全片常驻真母本顶栏/人设卡/30fps 透明动态花字）及 render_mp4 / motion_html_renderer
提供高保真、高占比、互不串味的风格视觉配方与商业图解换肤主题。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


STYLE_VISUAL_TRUTH: Dict[str, Dict[str, Any]] = {
    # ── F1 · 清爽原位高亮口播（style_01） ────────────────────────────────────
    "style_01": {
        "family": "F1_clean_inline_highlight",
        "label": "极简无黑框白字 + 同句关键词原位暖黄高亮 + 开场3拍斜标 + 实拍B-roll",
        "top_bar": None,
        "overlay_override": "s01_clean_store",
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#DC2626", "font_role": "sans"},
        "scene_beats": [],
        "subtitle_patch": {
            "bg_box_color": "#00000000",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.16,
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
        "diagram_theme": {"light_theme": False, "accent": "#BCFF00", "accent2": "#DC2626", "font_role": "serif"},
        "scene_beats": [],
        "subtitle_patch": {},
    },
    # ── F3 · 顶部常驻纤细黄标 + 暗红大字 + 顶圆窗金句 + 真打字机（style_03）─
    "style_03": {
        "family": "F3_persistent_top_bar_deep",
        "label": "顶部常驻双行亮黄宋体标题 + 暗底顶圆窗金黄大字 + 炭墨真打字机引语 + 人像剪纸白边",
        "top_bar": {
            "mode": "s03_dual_yellow_serif",
            "y": 168,
            "full_duration": True,
            "fg_top": "#FDE047",
            "fg_bot": "#FDE047",
            "stroke": "#0B0B0D",
        },
        "overlay_override": "s03_gold_crimson_callout",
        "diagram_theme": {"light_theme": False, "accent": "#FDE047", "accent2": "#DC2626", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.14,
                "max_dur": 3.8,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#15171E",
                    "accent_hex": "#FDE047",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 585,
                    "circle_r": 215,
                },
            },
            {
                "frac": 0.34,
                "max_dur": 3.8,
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
                "frac": 0.56,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s03_black_typewriter",
                    "bg_hex": "#16181F",
                    "fg_hex": "#F8FAFC",
                    "accent_hex": "#FDE047",
                },
            },
            {
                "frac": 0.78,
                "max_dur": 3.8,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#15171E",
                    "accent_hex": "#FDE047",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 585,
                    "circle_r": 215,
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
    # ── F4 · 高频黑屏圆角白边方窗 + 顶大黄字 + 人后巨字 + 聚焦方窗（style_04）
    "style_04": {
        "family": "F4_dark_box_panel",
        "label": "高频黑底圆角方窗顶大黄字 + 人后亮黄粗巨字 + 聚焦小方窗下超大白字冲击",
        "top_bar": None,
        "overlay_override": "s04_clean_white_stroke",
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#E03131", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.14,
                "max_dur": 4.0,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s04_dual_yellow_header",
                    "bg_hex": "#15171E",
                    "accent_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.36,
                "max_dur": 4.0,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s04_dual_yellow_header",
                    "bg_hex": "#15171E",
                    "accent_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.58,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FFE14D",
                    "stroke_hex": "#14161D",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 420,
                },
            },
            {
                "frac": 0.80,
                "max_dur": 4.0,
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
    # ── F1/F4 · 纠偏！胸口暗红描边立体大字 + 纯黑中间画中画 + 胸口大白字（style_05）
    "style_05": {
        "family": "F4_dark_box_panel",
        "label": "清透口播胸口暗红白边立体大字 + 纯黑屏居中画中画配精致白字 + 粗黑边大白字冲击",
        "top_bar": None,
        "overlay_override": "s05_warm_red_gold_callout",
        "diagram_theme": {"light_theme": False, "accent": "#E05A47", "accent2": "#FDE047", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.18,
                "max_dur": 4.0,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s05_center_pip",
                    "bg_hex": "#14151A",
                    "accent_hex": "#FFFFFF",
                },
            },
            {
                "frac": 0.52,
                "max_dur": 4.0,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s05_center_pip",
                    "bg_hex": "#14151A",
                    "accent_hex": "#FDE047",
                },
            },
            {
                "frac": 0.78,
                "max_dur": 3.8,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s03_black_typewriter",
                    "bg_hex": "#14151A",
                    "fg_hex": "#F8FAFC",
                    "accent_hex": "#E05A47",
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
    # ── F3 · 顶部半透暖金双行宋体 + 人设名片 + 暖金发光金句 + 尾段竖排诗句（style_06）
    "style_06": {
        "family": "F3_persistent_top_bar_deep",
        "label": "顶部全宽灰半透双行暖金宋体大标 + 开场左置人设头衔 + 暖金发光放大金句 + 右侧竖排诗意字",
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
        "diagram_theme": {"light_theme": False, "accent": "#F4E2A8", "accent2": "#C88A16", "font_role": "serif_bold"},
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
    # ── F5/F4 · 黑屏柔圆窗大红痛点 + 实拍方窗金数 + 人后顶天亮黄巨字（style_07）
    "style_07": {
        "family": "F5_person_over_giant",
        "label": "黑屏顶置羽化圆形主播小窗配大红双行痛点 + 方窗展示金字数字 + AI抠像人后顶天亮黄巨字",
        "top_bar": None,
        "overlay_override": "s07_yellow_outline_callout",
        "diagram_theme": {"light_theme": False, "accent": "#FFD426", "accent2": "#DC2626", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.18,
                "max_dur": 4.0,
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
                "frac": 0.44,
                "max_dur": 4.0,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s04_dual_yellow_header",
                    "bg_hex": "#14151B",
                    "accent_hex": "#FFD426",
                },
            },
            {
                "frac": 0.68,
                "max_dur": 4.0,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FFD426",
                    "stroke_hex": "#18181B",
                    "bg_blur": 5,
                    "bg_darken": -0.08,
                    "y_center": 440,
                },
            },
            {
                "frac": 0.86,
                "max_dur": 3.6,
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
    # ── F3 · 左上角常驻斜杠双色大标 + 黑底条字幕 + 满屏粗黑边白大字冲击（style_08）
    "style_08": {
        "family": "F3_persistent_top_bar_deep",
        "label": "左上角常驻/斜杠/白黄双行斜体大标 + 精致黑底条字幕 + 霸屏粗黑边白大字金句",
        "top_bar": {
            "mode": "s08_slash_dual_top_left",
            "y": 172,
            "full_duration": True,
            "fg_top": "#FFFFFF",
            "fg_bot": "#FFE14D",
        },
        "overlay_override": "s08_giant_white_impact",
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#2563EB", "font_role": "oblique"},
        "scene_beats": [],
        "subtitle_patch": {
            "bg_box_color": "#0F1116CC",
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE14D",
            "keyword_scale": 1.12,
            "stroke_width": 0,
        },
    },
    # ── F4 · 高占比纯黑律政纪实双区（左下柔圆窗 + 上区证据/卷宗/红金法条）（style_09）
    "style_09": {
        "family": "F4_dark_box_panel",
        "label": "全片60%纯黑纪实双区左下正圆主播窗 + 上区实景证据/白纸卷宗卡/引号红金法条 + 尾段全屏亮黄",
        "top_bar": None,
        "overlay_override": "s09_doc_gold_caption",
        "diagram_theme": {"light_theme": False, "accent": "#FACC15", "accent2": "#C92A2A", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 5.0,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_doc",
                    "bg_hex": "#15161C",
                    "accent_hex": "#CCA966",
                    "sub_hex": "#E2D5B5",
                },
            },
            {
                "frac": 0.24,
                "max_dur": 5.0,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_dossier",
                    "bg_hex": "#15161C",
                    "accent_hex": "#C92A2A",
                    "sub_hex": "#F8FAFC",
                },
            },
            {
                "frac": 0.42,
                "max_dur": 5.2,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_red_quote",
                    "bg_hex": "#15161C",
                    "accent_hex": "#DC2626",
                    "sub_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.60,
                "max_dur": 5.2,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_yellow_quote",
                    "bg_hex": "#15161C",
                    "accent_hex": "#FACC15",
                    "sub_hex": "#F8FAFC",
                },
            },
            {
                "frac": 0.76,
                "max_dur": 4.5,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_dossier",
                    "bg_hex": "#15161C",
                    "accent_hex": "#CCA966",
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
    # ── F5 · 高频人后顶天大红/纯白巨字 + 黑屏绿标签方窗（style_10）─────────
    "style_10": {
        "family": "F5_person_over_giant",
        "label": "高频AI抠像人后顶天大红/纯白粗巨字 + 黑屏方窗荧光绿标签 + 胸口单字白冲击",
        "top_bar": None,
        "overlay_override": "s10_giant_white_punch",
        "diagram_theme": {"light_theme": False, "accent": "#FFE135", "accent2": "#D62828", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.14,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FFFFFF",
                    "stroke_hex": "#121216",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 410,
                },
            },
            {
                "frac": 0.34,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#D62828",
                    "stroke_hex": "#121216",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 410,
                },
            },
            {
                "frac": 0.56,
                "max_dur": 4.0,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s10_green_tag_box",
                    "bg_hex": "#14151B",
                    "accent_hex": "#16A34A",
                },
            },
            {
                "frac": 0.78,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#D62828",
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
            "keyword_color": "#FFE135",
            "keyword_scale": 1.14,
            "stroke_width": 4,
            "stroke_color": "#0A0A0A",
        },
    },
    # ── F5 · 开场人后满屏大白字 + 黑屏圆窗提问 + 纯黑抠像大红/白字（style_11）
    "style_11": {
        "family": "F5_person_over_giant",
        "label": "开场人后大白字 + 纯黑圆窗设问 + 金句一键切纯黑背景AI抠像配大红/大白粗字",
        "top_bar": None,
        "overlay_override": "s11_clean_studio_callout",
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#DC2626", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#F8FAFC",
                    "stroke_hex": "#14161D",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 410,
                },
            },
            {
                "frac": 0.28,
                "max_dur": 3.8,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#14161C",
                    "accent_hex": "#FFE14D",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 500,
                    "circle_r": 215,
                },
            },
            {
                "frac": 0.62,
                "max_dur": 4.0,
                "layout": {
                    "mode": "pure_black_cutout_pip",
                    "bg_hex": "#14161C",
                    "title_hex": "#DC2626",
                },
            },
            {
                "frac": 0.82,
                "max_dur": 3.8,
                "layout": {
                    "mode": "pure_black_cutout_pip",
                    "bg_hex": "#14161C",
                    "title_hex": "#F8FAFC",
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
    # ── F1/F3 · 顶部常驻白+亮黄双行标 + 纯黑顶圆窗 + 胶囊气泡（style_12）───
    "style_12": {
        "family": "F1_clean_inline_highlight",
        "label": "顶部常驻纯白+亮黄#FFE135粗黑边双行大标题 + 纯黑顶置圆窗切镜 + 黑白胶囊小气泡",
        "top_bar": {
            "mode": "s12_white_yellow_dual_top",
            "y": 168,
            "full_duration": True,
            "fg_top": "#FFFFFF",
            "fg_bot": "#FFE135",
        },
        "overlay_override": "s12_yellow_sentence_emphasis",
        "diagram_theme": {"light_theme": False, "accent": "#FFE135", "accent2": "#18181B", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.42,
                "max_dur": 3.8,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#14151B",
                    "accent_hex": "#FFE135",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 560,
                    "circle_r": 210,
                },
            },
            {
                "frac": 0.74,
                "max_dur": 3.8,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#14151B",
                    "accent_hex": "#FFE135",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 560,
                    "circle_r": 210,
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
    # ── F4 · 反诈黑底红字方窗 + 纯黑头顶大黄标 + 虚化圆窗绕圈问号（style_13）
    "style_13": {
        "family": "F4_dark_box_panel",
        "label": "开场黑底白红警示方窗 + 纯黑抠像顶大黄宋体 + 虚化暗底圆窗绕圈动态问号???? + 法条档案",
        "top_bar": None,
        "overlay_override": "s13_yellow_warning_header",
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#E03131", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 4.0,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s13_red_warn_box",
                    "bg_hex": "#15161C",
                    "accent_hex": "#E03131",
                },
            },
            {
                "frac": 0.30,
                "max_dur": 3.8,
                "layout": {
                    "mode": "pure_black_cutout_pip",
                    "bg_hex": "#14151B",
                    "title_hex": "#FFD426",
                },
            },
            {
                "frac": 0.54,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_blur_qmark",
                    "bg_hex": "#181920",
                    "accent_hex": "#FFE14D",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 540,
                    "circle_r": 190,
                },
            },
            {
                "frac": 0.78,
                "max_dur": 4.0,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_dossier",
                    "bg_hex": "#16171E",
                    "accent_hex": "#DC2626",
                    "sub_hex": "#FFE14D",
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
    # ── F4 · 黑底大圆窗设问 + 真打字机引语 + 人后顶天亮黄大标（style_14）─────
    "style_14": {
        "family": "F4_dark_box_panel",
        "label": "开场黑底大圆窗白黄双行设问 + 纯黑屏白字引号真打字机 + AI抠像人后亮黄大标",
        "top_bar": None,
        "overlay_override": "s14_red_yellow_emphasis",
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#EA580C", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 4.0,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#14151B",
                    "accent_hex": "#FFE14D",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 530,
                    "circle_r": 245,
                },
            },
            {
                "frac": 0.36,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s14_black_quote_top",
                    "bg_hex": "#15171E",
                    "fg_hex": "#F8FAFC",
                    "accent_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.60,
                "max_dur": 4.0,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s14_black_quote_top",
                    "bg_hex": "#15171E",
                    "fg_hex": "#F8FAFC",
                    "accent_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.80,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FFD426",
                    "stroke_hex": "#121216",
                    "bg_blur": 3,
                    "bg_darken": -0.05,
                    "y_center": 410,
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
    # ── F6 · 重大纠偏！style_15 原母本真魂：纸质感白色大纲清单白板 + 发光大白字
    "style_15": {
        "family": "F6_cinema_pinyin_interview",
        "label": "高占比米白纸底大纲清单白板（左上圆窗+大写拼音副标+逐条黑体）+ 胸口发光白大字 + 黑屏黄字",
        "top_bar": None,
        "overlay_override": "s15_pinyin_and_crimson_giant",
        "diagram_theme": {"light_theme": True, "accent": "#C88A16", "accent2": "#181A20", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.16,
                "max_dur": 5.0,
                "layout": {
                    "mode": "paper_outline_pip",
                    "variant": "s15_paper_outline",
                    "bg_hex": "#F4F2EC",
                    "accent_hex": "#C88A16",
                    "circle_cx": 184,
                    "circle_cy": 216,
                    "circle_r": 106,
                },
            },
            {
                "frac": 0.40,
                "max_dur": 5.0,
                "layout": {
                    "mode": "paper_outline_pip",
                    "variant": "s15_paper_outline",
                    "bg_hex": "#F4F2EC",
                    "accent_hex": "#C88A16",
                    "circle_cx": 184,
                    "circle_cy": 216,
                    "circle_r": 106,
                },
            },
            {
                "frac": 0.64,
                "max_dur": 4.8,
                "layout": {
                    "mode": "paper_outline_pip",
                    "variant": "s15_paper_outline",
                    "bg_hex": "#F4F2EC",
                    "accent_hex": "#C88A16",
                    "circle_cx": 184,
                    "circle_cy": 216,
                    "circle_r": 106,
                },
            },
            {
                "frac": 0.82,
                "max_dur": 3.8,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s03_black_typewriter",
                    "bg_hex": "#131419",
                    "fg_hex": "#FFE14D",
                    "accent_hex": "#FFE14D",
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
    # ── F5 · 暗调创业重虚化连续真打字机 + 人后暗红巨字（style_16）───────────
    "style_16": {
        "family": "F5_person_over_giant",
        "label": "暗调创业口播 + 长段重虚化暗底真逐字打字机与右对齐出处 + AI抠像人后顶格暗红巨字",
        "top_bar": None,
        "overlay_override": "s16_glow_white_callout",
        "diagram_theme": {"light_theme": False, "accent": "#FACC15", "accent2": "#B91C1C", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.16,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#B91C1C",
                    "stroke_hex": "#180A0A",
                    "bg_blur": 5,
                    "bg_darken": -0.10,
                    "y_center": 410,
                },
            },
            {
                "frac": 0.34,
                "max_dur": 4.8,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s16_blur_dark_typewriter",
                    "bg_hex": "#14161D",
                    "fg_hex": "#FFFFFF",
                    "accent_hex": "#FACC15",
                },
            },
            {
                "frac": 0.54,
                "max_dur": 4.8,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s16_blur_dark_typewriter",
                    "bg_hex": "#14161D",
                    "fg_hex": "#FFFFFF",
                    "accent_hex": "#FACC15",
                },
            },
            {
                "frac": 0.78,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#B91C1C",
                    "stroke_hex": "#180A0A",
                    "bg_blur": 5,
                    "bg_darken": -0.10,
                    "y_center": 410,
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
    # ── F4 · 纯黑柔和羽化圆窗金句 + 双圆角对比卡 + 黑条小字幕（style_17）─────
    "style_17": {
        "family": "F4_dark_box_panel",
        "label": "纯黑底柔和羽化顶置主播窗配下方黄白建议金句 + 双实拍对比圆角卡 + 精致黑条字幕",
        "top_bar": None,
        "overlay_override": None,  # 保留既有 style_17 暖金双格单测契约
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#F59E0B", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.16,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#121318",
                    "accent_hex": "#FFE14D",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 500,
                    "circle_r": 240,
                },
            },
            {
                "frac": 0.38,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_large_gold",
                    "bg_hex": "#121318",
                    "accent_hex": "#FFE14D",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 500,
                    "circle_r": 240,
                },
            },
            {
                "frac": 0.62,
                "max_dur": 4.0,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s17_dual_compare_cards",
                    "bg_hex": "#15161B",
                    "accent_hex": "#FFFFFF",
                },
            },
            {
                "frac": 0.82,
                "max_dur": 3.8,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s17_black_top_square",
                    "bg_hex": "#15161B",
                    "accent_hex": "#FDE047",
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
    # ── F5 · 户外顶圆窗红引号 + 重虚化白底红字引号条 + 人后暗红巨字（style_18）
    "style_18": {
        "family": "F5_person_over_giant",
        "label": "户外口播 + 纯黑顶置圆窗配红引号金句 + 重虚化暗底大白序号与白底暗红引号条",
        "top_bar": None,
        "overlay_override": "s18_crimson_quote_strip",
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#DC2626", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.16,
                "max_dur": 3.8,
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
                "frac": 0.42,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "s18_circle_red_quote",
                    "bg_hex": "#14151A",
                    "accent_hex": "#FFE14D",
                    "sub_hex": "#FFFFFF",
                    "circle_cx": 540,
                    "circle_cy": 520,
                    "circle_r": 215,
                },
            },
            {
                "frac": 0.66,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s18_blur_white_strip",
                    "bg_hex": "#181920",
                    "fg_hex": "#111318",
                    "accent_hex": "#DC2626",
                },
            },
            {
                "frac": 0.84,
                "max_dur": 3.8,
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
    # ── F4 · 律政商业红黑双区 + 卷宗典籍卡 + 红引号逐句高亮法条（style_19）──
    "style_19": {
        "family": "F4_dark_box_panel",
        "label": "开场前景金黄大字 + 档案卷宗卡 + 纯黑屏左下正圆头像配红底引号逐句高亮白黄条文",
        "top_bar": None,
        "overlay_override": "s19_gold_headline",
        "diagram_theme": {"light_theme": False, "accent": "#FFE14D", "accent2": "#DC2626", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.22,
                "max_dur": 4.4,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_dossier",
                    "bg_hex": "#14151B",
                    "accent_hex": "#DC2626",
                    "sub_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.44,
                "max_dur": 4.6,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_red_quote",
                    "bg_hex": "#14151B",
                    "accent_hex": "#E03131",
                    "sub_hex": "#FFE14D",
                },
            },
            {
                "frac": 0.66,
                "max_dur": 4.6,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_red_quote",
                    "bg_hex": "#14151B",
                    "accent_hex": "#E03131",
                    "sub_hex": "#F8FAFC",
                },
            },
            {
                "frac": 0.84,
                "max_dur": 4.0,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_yellow_quote",
                    "bg_hex": "#14151B",
                    "accent_hex": "#FFD426",
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
    # ── F4 · 国风黑底顶大圆窗暖金双行 + 纯黑打字机 + 朱红底白字块（style_20）
    "style_20": {
        "family": "F4_dark_box_panel",
        "label": "纯黑屏顶部大圆窗配古风暖金双行宋体 + 纯黑真打字机引语 + 常规朱红发光底块白字重点",
        "top_bar": None,
        "overlay_override": "s20_crimson_block_serif",
        "diagram_theme": {"light_theme": False, "accent": "#F3D37A", "accent2": "#A61C1C", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.22,
                "max_dur": 4.4,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_guofeng_gold",
                    "bg_hex": "#161412",
                    "accent_hex": "#F3D37A",
                    "sub_hex": "#F5E6BE",
                    "circle_cx": 540,
                    "circle_cy": 520,
                    "circle_r": 245,
                },
            },
            {
                "frac": 0.50,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s20_black_quote_top",
                    "bg_hex": "#151412",
                    "fg_hex": "#F8F3E8",
                    "accent_hex": "#F3D37A",
                },
            },
            {
                "frac": 0.76,
                "max_dur": 4.2,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_guofeng_gold",
                    "bg_hex": "#161412",
                    "accent_hex": "#F3D37A",
                    "sub_hex": "#F5E6BE",
                    "circle_cx": 540,
                    "circle_cy": 520,
                    "circle_r": 245,
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
    # ── F5 · 剪纸粗白边贴纸 + 荧光黄绿箭头大字 + 黑框人后顶天白巨字（style_21）
    "style_21": {
        "family": "F5_person_over_giant",
        "label": "开场AI抠像白描边手绘人物贴纸 + 荧光黄绿大字手绘红线 + 黑边相框人后顶天纯白巨字",
        "top_bar": None,
        "overlay_override": "s21_lime_yellow_annotation",
        "diagram_theme": {"light_theme": False, "accent": "#D9F94D", "accent2": "#EF4444", "font_role": "sans"},
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 4.0,
                "layout": {
                    "mode": "vision_portrait_cutout_pip",
                    "bg_mode": "soft_blur",
                    "outline_px": 14,
                    "outline_hex": "#FFFFFF",
                    "torn_paper": True,
                },
            },
            {
                "frac": 0.36,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#D9F94D",
                    "stroke_hex": "#0E1015",
                    "bg_blur": 4,
                    "bg_darken": -0.08,
                    "y_center": 410,
                },
            },
            {
                "frac": 0.62,
                "max_dur": 4.0,
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
            {
                "frac": 0.82,
                "max_dur": 3.8,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s04_focus_giant_word",
                    "bg_hex": "#13151B",
                    "accent_hex": "#D9F94D",
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
    # ── F4/F5 · 人后灰白/暗红巨字 + 左下圆窗亮黄引号警示 + 悬浮概念白卡（style_22）
    "style_22": {
        "family": "F4_dark_box_panel",
        "label": "开场人后大灰白巨字配红字 + 纯黑屏左下正圆头像亮黄引号警示 + 纯黑抠像大红字 + 悬浮概念白卡",
        "top_bar": None,
        "overlay_override": "s22_red_callout",
        "diagram_theme": {"light_theme": False, "accent": "#FACC15", "accent2": "#DC2626", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#E5E7EB",
                    "stroke_hex": "#18181B",
                    "bg_blur": 3,
                    "bg_darken": -0.04,
                    "y_center": 420,
                },
            },
            {
                "frac": 0.30,
                "max_dur": 4.4,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "bottom_left_yellow_quote",
                    "bg_hex": "#14151B",
                    "accent_hex": "#FACC15",
                    "sub_hex": "#FDE047",
                },
            },
            {
                "frac": 0.58,
                "max_dur": 4.0,
                "layout": {
                    "mode": "pure_black_cutout_pip",
                    "bg_hex": "#13141A",
                    "title_hex": "#DC2626",
                },
            },
            {
                "frac": 0.80,
                "max_dur": 4.2,
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
    # ── F4 · 人后巨大暖金单字 + 虚化暗底暖黄诗句剪纸 + 暗化圆窗暖黄金句（style_23）
    "style_23": {
        "family": "F4_dark_box_panel",
        "label": "开场人后暖金大字 + 虚化暗底左侧逐行暖黄诗句配右下白描边剪纸 + 暗化背景顶部柔圆窗暖黄金句",
        "top_bar": None,
        "overlay_override": "s23_warm_serif_caption",
        "diagram_theme": {"light_theme": False, "accent": "#FDE68A", "accent2": "#D97706", "font_role": "serif"},
        "scene_beats": [
            {
                "frac": 0.08,
                "max_dur": 3.8,
                "layout": {
                    "mode": "person_behind_giant_text_pip",
                    "giant_color": "#FDE68A",
                    "stroke_hex": "#292218",
                    "bg_blur": 4,
                    "bg_darken": -0.06,
                    "y_center": 420,
                },
            },
            {
                "frac": 0.28,
                "max_dur": 4.4,
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
                "frac": 0.56,
                "max_dur": 4.4,
                "layout": {
                    "mode": "dark_circle_panel_pip",
                    "variant": "top_blur_gold",
                    "bg_hex": "#1B1B20",
                    "accent_hex": "#FDE68A",
                    "sub_hex": "#FDE68A",
                    "circle_cx": 540,
                    "circle_cy": 530,
                    "circle_r": 225,
                },
            },
            {
                "frac": 0.80,
                "max_dur": 4.0,
                "layout": {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s16_blur_dark_typewriter",
                    "bg_hex": "#1B1B20",
                    "fg_hex": "#FDE68A",
                    "accent_hex": "#FDE68A",
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
    # ── F7 · 高占比浅灰杂志纸底多图测评白板 + 黑底圆角冲击窗 + 竖排大黄字（style_24）
    "style_24": {
        "family": "F7_review_whiteboard",
        "label": "高占比浅灰杂志纸底多图痛点评测白板 + 黑底圆角实拍小窗蓝边大黄字 + 左侧竖排大黄手写字",
        "top_bar": None,
        "overlay_override": "s24_vertical_yellow_punch",
        "diagram_theme": {"light_theme": True, "accent": "#F59E0B", "accent2": "#183A88", "font_role": "oblique"},
        "scene_beats": [
            {
                "frac": 0.14,
                "max_dur": 4.4,
                "layout": {
                    "mode": "review_whiteboard_pip",
                    "variant": "s24_whiteboard_multi",
                    "bg_hex": "#EEF1F5",
                    "accent_hex": "#F59E0B",
                },
            },
            {
                "frac": 0.36,
                "max_dur": 4.6,
                "layout": {
                    "mode": "review_whiteboard_pip",
                    "variant": "s24_whiteboard_multi",
                    "bg_hex": "#EEF1F5",
                    "accent_hex": "#F59E0B",
                },
            },
            {
                "frac": 0.58,
                "max_dur": 4.2,
                "layout": {
                    "mode": "framed_box_pip",
                    "variant": "s24_rounded_impact",
                    "bg_hex": "#15171E",
                    "accent_hex": "#FFE135",
                },
            },
            {
                "frac": 0.78,
                "max_dur": 4.4,
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
            if k in txt and (not re.search(r"\d", k)) and (k not in valid_spans):
                return ""
            if k not in txt:
                return ""
        return k

    for field in ("display_keywords", "keywords"):
        for kw in (sent.get(field) or []):
            hit = _clean_cand(str(kw or ""), require_boundary=True)
            if hit:
                return hit

    if not txt:
        return fallback[:max_chars]

    for min_len in (3, 2):
        for tk in nat_toks:
            if len(tk) >= min_len:
                hit = _clean_cand(tk, require_boundary=False)
                if hit:
                    return hit
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
