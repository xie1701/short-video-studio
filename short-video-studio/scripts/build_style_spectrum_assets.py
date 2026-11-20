#!/usr/bin/env python3
"""
[Step 3 资产构建器]
读取 analyze_style_spectrum.py 产出的原始逐帧量化文件 style_spectrum_raw.json，
融合人工视觉图谱复核元数据（字体家族、精确单行字号、描边/底框、动效曲线、0-5s开头结构、封面版式、流派聚类），
确定性生成全套跨平台风格谱资产：
1. assets/style_spectrum/style_spectrum_library.json
2. assets/subtitle_templates/templates.json (保留原4套基础模板 + 新增 style_01~style_20)
3. assets/style_spectrum/hyperframes/style-spectrum-presets.css
4. assets/style_spectrum/STYLE_SPECTRUM_REPORT.md
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import ASSETS_DIR, maybe_reexec_in_venv, read_json, write_json

maybe_reexec_in_venv()

STYLE_DIR = ASSETS_DIR / "style_spectrum"

ARCHETYPES: List[Dict[str, Any]] = [
    {
        "archetype_id": "arch_01_tech_ui_console",
        "name": "暗黑科技 / AI 工作流控制台风",
        "members": ["style_01", "style_09", "style_10", "style_15", "style_17"],
        "best_for": "AI 工具测评、Agent 工作流演示、SaaS 产品教程、极客硬核拆解",
        "color_signature": "暗黑低亮度基底 (Y=36~84) + 琥珀金/科技蓝强调色 + 高对比卡片边框",
        "rhythm_signature": "顶部章节步骤条常驻 + 画面内部 UI/卡片高频切换 + 强节拍/事件对齐 (60%~90%)",
        "subtitle_signature": "克制小号居中底字 (36~46px)，重在让渡主视觉区给界面与卡片演示",
    },
    {
        "archetype_id": "arch_02_high_contrast_story",
        "name": "强反差财经 / 深度人物叙事拼贴风",
        "members": ["style_03", "style_16", "style_19"],
        "best_for": "商业案例揭秘、创始人深度访谈、剪辑思维干货、高信息量长视频",
        "color_signature": "中性电影对比调或暖人物肤色 + 亮黄/翠绿/报刊米白强视觉冲击块",
        "rhythm_signature": "高速切镜 (med 1.25s~2.20s，全片 118~290 镜) + 高频音效推动 (63~90 SFX/min)",
        "subtitle_signature": "常规底部小字兜底 + 情绪/数字爆点处全屏或半屏巨型花字/色块炸裂",
    },
    {
        "archetype_id": "arch_03_editorial_matte_depth",
        "name": "高级感杂志排版 / 人物前景遮罩风",
        "members": ["style_11", "style_12", "style_18", "style_20"],
        "best_for": "审美/设计评论、个人IP方法论教学、Notion/极简白底知识拆解",
        "color_signature": "极净极简白底或深红/书墙反差色 + 少而精的无衬线黑体层级",
        "rhythm_signature": "前 3 秒用人物遮罩大字或实物定格瞬间抓眼，中段结构化清单与快切案例交替",
        "subtitle_signature": "干净双语或粗黑体无底框排版，字距行距克制，严控花哨字体数量 ≤2 种",
    },
    {
        "archetype_id": "arch_04_punch_talking_head",
        "name": "高密口播卡点 / 干货大字冲击风",
        "members": ["style_02", "style_04", "style_07", "style_14"],
        "best_for": "创始人认知口播、实体商家/建材家居老板IP、认知颠覆与痛点科普",
        "color_signature": "暖肤色高透或暖琥珀书桌调 + 亮黄(#FFD54F)/珊瑚红强反差重点高亮",
        "rhythm_signature": "口播句间紧剪 + 景别推拉跳剪 + 卡片弹窗音效卡点 (对齐率 79%~92%)",
        "subtitle_signature": "大号粗黑体 (52~76px) + 整句/半句亮黄变色放大，首帧高信息密度大字封面",
    },
    {
        "archetype_id": "arch_05_cinema_slow_space",
        "name": "电影感空间漫游 / 人文宋体金句风",
        "members": ["style_06", "style_08", "style_13"],
        "best_for": "高端室内设计/建筑展厅实拍、心理学/哲学金句、深度真实生活随拍访谈",
        "color_signature": "暖木质感色温或冷青电影灰调，拒绝廉价高饱和滤镜",
        "rhythm_signature": "尊重呼吸感与空间尺度（空镜缓推或随拍微跳剪），少音效或零花字干扰",
        "subtitle_signature": "思源宋体 (SourceHanSerifCN) 或极简细黑投影，呈现杂志书卷气与高级克制感",
    },
    {
        "archetype_id": "arch_06_paper_stopmotion",
        "name": "纸张拼贴定格 / 工程图解科普风",
        "members": ["style_05"],
        "best_for": "收纳改造、家居尺寸科普、产品结构拆解、强动手感实物教程",
        "color_signature": "低饱和褶皱白纸底纹 (sat=9.1%) + 暖木纹理 + 红线尺寸标尺",
        "rhythm_signature": "前 3 秒手部模型定格 3 连切，全片依靠纸片元件位移与定格帧节奏驱动",
        "subtitle_signature": "底部极简白字黑描边，画面主体留给纸雕模型与红色尺寸标注线",
    },
]

# 基于 20 张 contact sheet 逐一视觉精校后的规格表
CURATED_VISUAL_SPECS: Dict[str, Dict[str, Any]] = {
    "style_01": {
        "style_name_cn": "复古胶片控制台 · AI 模板展示风",
        "archetype_id": "arch_01_tech_ui_console",
        "domain_tags": ["AI工具", "开源展示", "多案例合集"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Medium.otf", "NotoSansSC-Medium.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Medium",
            "bilingual": True,
            "font_size_1080p_px": 40,
            "english_font_size_px": 22,
            "keyword_scale": 1.10,
            "primary_color": "#FBFAEE",
            "keyword_color": "#D2BB8C",
            "stroke_color": "#0D0C0D",
            "stroke_width": 2,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.80,
            "animation": "fade_slide_up",
            "punct_stripped": False,
        },
        "opening_structure": {
            "0_to_3s_pattern": "胶片齿孔暗窗 0.2s 渐亮开场 → 0.65s 推至半身口播并顶部浮现白字「行业开源」→ 1.55s 展开金黄金属质感双行标题「行业开源模板 / 即将上线」→ 2.65s 切入横屏画中画代码对照",
            "hook_formula": "利益诱饵 + 免费开源合集钩子",
            "first_frame_density": "中等偏高（拟物胶片框+顶部时间码+品牌抬头+人物）",
        },
        "title_cover_layout": {
            "layout_id": "film_reel_console",
            "layout_name": "复古胶片齿孔放映控制台",
            "grid_desc": "深黑底色嵌双侧胶片齿孔，顶部常驻 REEL 进度与黄黑胶囊场次条，左侧场次巨数字 `01`，右侧速度表盘 `SPEED 1x`，片尾弹白底评分意见卡",
            "main_sub_ratio": 1.85,
            "highlight_style": "金黄金属渐变大字 + 胶片黄黑胶囊状态标签",
        },
        "packaging_notes": {
            "ui_cadence": "每 15-20s 切换一个行业放映场次（01 房产→03 电商→06 数码→08 情感），内部画中画以高倍速快进演示",
            "transitions": ["暗场淡入", "胶片滚动切场", "弹窗意见卡滑入"],
            "elements": ["胶片齿孔边框", "胶囊场次进度条", "圆环倒计时速度表", "白底映后意见卡"],
            "sfx_per_min_target": 60,
            "visual_per_min_target": 12,
        },
    },
    "style_02": {
        "style_name_cn": "极简硬核口播 · 书法炸裂封面风",
        "archetype_id": "arch_04_punch_talking_head",
        "domain_tags": ["个人成长", "认知观点", "读书金句"],
        "subtitle_spec": {
            "font_family": "Alimama ShuHeiTi",
            "font_candidates": ["AlimamaShuHeiTi-Bold.otf", "SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 74,
            "keyword_scale": 1.24,
            "primary_color": "#FFFFFF",
            "keyword_color": "#EFD45F",
            "stroke_color": "#111111",
            "stroke_width": 5,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.78,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 人物近景微微笑 + 下半屏毛笔手写体白黄撞色封面闪帧 → 0.3s 秒切紧凑直视口播，前 3 秒通过景别小推拉与关键词全句变黄连切 3 刀",
            "hook_formula": "痛点反差 + 颠覆认知句",
            "first_frame_density": "极高（大头特写直视 + 巨大毛笔双行反差金句）",
        },
        "title_cover_layout": {
            "layout_id": "calligraphy_contrast_bottom",
            "layout_name": "下方双行毛笔撞色金句封面",
            "grid_desc": "人物占上 60% 画面，下 40% 压黑色半透渐变衬底，两行毛笔手写大字（首行纯白、次行亮金黄#EFD45F）形成强冲突",
            "main_sub_ratio": 1.05,
            "highlight_style": "全句/半句整段变亮黄并放大 1.24x + 硬切零拖沓",
        },
        "packaging_notes": {
            "ui_cadence": "全程纯口播零多余贴纸，完全依靠去气口硬切与推拉跳剪（35镜/56s，平均1.6s一刀）抓牢节奏",
            "transitions": ["硬切", "同机位推拉跳剪"],
            "elements": ["毛笔手写封面大字", "全句变黄重音高亮"],
            "sfx_per_min_target": 70,
            "visual_per_min_target": 6,
        },
    },
    "style_03": {
        "style_name_cn": "十亿访谈长片 · 顶部时间节点进度条风",
        "archetype_id": "arch_02_high_contrast_story",
        "domain_tags": ["人物访谈", "女性创业", "长视频留存"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 46,
            "keyword_scale": 1.35,
            "primary_color": "#FFFFFF",
            "keyword_color": "#F8E731",
            "stroke_color": "#141414",
            "stroke_width": 4,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.89,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 双人16:9中景直接抛数字反差金句「从月薪4000」白字 → 1.55s 弹出超大亮黄黑描边「到年薪10亿」→ 2.65s 秒切嘉宾单人特写+左侧大白字「她是如何」悬念拉满",
            "hook_formula": "巨额数字反差对比 + 悬念提问",
            "first_frame_density": "高（双人高颜值主体 + 居中大字反差钩子）",
        },
        "title_cover_layout": {
            "layout_id": "top_milestone_bar_interview",
            "layout_name": "顶部金线里程碑进度条 + 双机位花字冲击",
            "grid_desc": "正片顶部横贯金线时间轴（4000→300万→3亿→10亿→显化心法）并随播放推进头像游标；重音时居中弹出占屏 1/3 的巨型白黄黑描边花字",
            "main_sub_ratio": 2.1,
            "highlight_style": "巨型亮黄#F8E731重黑描边花字 + 顶部里程碑节点亮起",
        },
        "packaging_notes": {
            "ui_cadence": "552秒长访谈靠290个双机位正反打与单人特写快切维持 1.58s 中位镜长",
            "transitions": ["双机位正反打硬切", "单人特写切入"],
            "elements": ["顶部里程碑进度条与头像游标", "全屏大白字重音花字", "语气词悬浮花字(嗯~)"],
            "sfx_per_min_target": 80,
            "visual_per_min_target": 14,
        },
    },
    "style_04": {
        "style_name_cn": "质感拟物纸片 · AI 框架科普风",
        "archetype_id": "arch_04_punch_talking_head",
        "domain_tags": ["AI提示词", "方法论框架", "效率工具"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Medium.otf", "NotoSansSC-Medium.ttf", "PingFang.ttc"],
            "font_weight": "Medium",
            "bilingual": False,
            "font_size_1080p_px": 48,
            "keyword_scale": 1.12,
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFFA87",
            "stroke_color": "#262B24",
            "stroke_width": 3,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.76,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 微笑直视镜头 + 居中双行红黄圆角描边大字封面「私人定制版 / 人生进化框架」→ 0.65s 切纯口播近景 → 1.55s 左上弹出微倾斜黄标签「想多赚点钱?」→ 2.65s 胸前飞入折角米白纸片「HUMAN 3.0」",
            "hook_formula": "权威背书(1.5亿浏览/马斯克转载) + 痛点发问",
            "first_frame_density": "高（清透暖调人物 + 撞色圆角双行大字 + 闪烁星点贴纸）",
        },
        "title_cover_layout": {
            "layout_id": "center_dual_stroke_badge",
            "layout_name": "居中红黄撞色圆角双行封面 + 拟物纸片卡",
            "grid_desc": "首帧居中红底白描边大字配下排金黄黑描边标题；正片频繁使用倾斜便签纸片、折角卡片、全屏米白模拟微信/AI对话窗+左上角圆形人物PIP",
            "main_sub_ratio": 1.25,
            "highlight_style": "红底黄字胶囊 + 米白纸张折角卡片 + 打勾清单贴纸",
        },
        "packaging_notes": {
            "ui_cadence": "虽镜头切换 12 次，但纸片卡片/清单/对话 UI 每 2-4 秒交替弹出一次",
            "transitions": ["硬切", "纸片弹出切入", "全屏对话UI切屏"],
            "elements": ["倾斜手绘黄标签", "折角纸片引文卡", "AI聊天模拟手机框", "方框勾选清单"],
            "sfx_per_min_target": 75,
            "visual_per_min_target": 18,
        },
    },
    "style_05": {
        "style_name_cn": "皱纹纸拼贴定格 · 工程图解科普风",
        "archetype_id": "arch_06_paper_stopmotion",
        "domain_tags": ["室内设计", "收纳改造", "尺寸科普"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 48,
            "keyword_scale": 1.15,
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFD54F",
            "stroke_color": "#1E1E1E",
            "stroke_width": 4,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.88,
            "animation": "fade_slide_up",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 皱纹白纸背景+立体鞋柜拼贴+红白大字「巨能装的 加密 鞋柜!!」+手绘尺寸标注 → 0.65s~1.55s 实拍人手推拉纸雕木柜门模型 → 2.65s 手撒一把迷你鞋子模型制造混乱反差",
            "hook_formula": "视觉强奇观模型 + 痛点具象化(满墙鞋柜仍塞不下)",
            "first_frame_density": "极高（皱纹纸背景 + 立面鞋柜 + 红色尺寸线 + 卡通表情贴纸）",
        },
        "title_cover_layout": {
            "layout_id": "paper_cutout_hero",
            "layout_name": "褶皱纸面立体模型 + 红色工程标注线",
            "grid_desc": "浅灰褶皱纸纹贯穿全片，顶部粗黑/红底白描边大字标题，左侧标尺红字标注尺寸(11/15/18/24cm)，底部极简白字字幕",
            "main_sub_ratio": 1.45,
            "highlight_style": "红字工程尺寸标注线 + 立体纸模抽拉动效",
        },
        "packaging_notes": {
            "ui_cadence": "前 3 秒定格手势连切 3 次；中段依靠木纹柜体拆解与红色尺寸线动态延展支撑 170s 科普",
            "transitions": ["实物手势定格切", "纸片滑动位移"],
            "elements": ["皱纹纸全局底纹", "红色CAD尺寸标尺", "3D等轴测柜体图", "卡通小人交互"],
            "sfx_per_min_target": 78,
            "visual_per_min_target": 15,
        },
    },
    "style_06": {
        "style_name_cn": "10秒情感金句 · 衬线宋体剪影大片风",
        "archetype_id": "arch_05_cinema_slow_space",
        "domain_tags": ["心理学", "人生金句", "高转发情绪短片"],
        "subtitle_spec": {
            "font_family": "Source Han Serif CN",
            "font_candidates": ["SourceHanSerifCN-Bold.otf", "NotoSerifSC-Bold.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 78,
            "keyword_scale": 1.38,
            "primary_color": "#FFFFFF",
            "keyword_color": "#F7CE77",
            "stroke_color": "#1A1A1A",
            "stroke_width": 4,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.69,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 心理专家中景正视 + 居中超大白金双行宋体封面「人这一生 有3次成长」→ 0.65s 标题缩小置顶常驻并切柔光特写+名牌 → 2.65s 全屏转为黑白逆光一家三口剪影动画配合「第1次 / 第2次 / 第3次」红字递增",
            "hook_formula": "高度凝练金句 + 序列递进(3次成长)",
            "first_frame_density": "高（暖色书房人物 + 居中双行强阴影白金宋体大字）",
        },
        "title_cover_layout": {
            "layout_id": "minimal_serif_title_top",
            "layout_name": "置顶红白衬线主题 + 剪影高光句中大小字错落",
            "grid_desc": "首帧居中大字后，0.6s 起顶部常驻双行衬线红字/白字主题；正文金句采用同一行内大小字混排（核心名词放大 1.38x 如「父母」「普通」「真实」）",
            "main_sub_ratio": 1.38,
            "highlight_style": "行内关键词字号突增 1.38x + 朱红渐变「第N次」角标",
        },
        "packaging_notes": {
            "ui_cadence": "10秒极短结构：前 2s 人物背书，后 8s 纯剪影渐消意境动画 + 强情绪 BGM",
            "transitions": ["叠化转黑白剪影", "人物渐隐消散特效"],
            "elements": ["置顶常驻双行宋体标题", "名牌小标签", "逆光家庭剪影", "行内大小字混排金句"],
            "sfx_per_min_target": 72,
            "visual_per_min_target": 18,
        },
    },
    "style_07": {
        "style_name_cn": "商业导师书桌 · 暗金波点全屏花字风",
        "archetype_id": "arch_04_punch_talking_head",
        "domain_tags": ["商业思维", "创业管理", "干货拆解"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Heavy.otf", "AlimamaShuHeiTi-Bold.otf", "NotoSansSC-Black.ttf"],
            "font_weight": "Heavy",
            "bilingual": False,
            "font_size_1080p_px": 64,
            "keyword_scale": 1.32,
            "primary_color": "#FFFFFF",
            "keyword_color": "#FBDE51",
            "stroke_color": "#1F1B18",
            "stroke_width": 5,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.83,
            "animation": "pop_spring",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 满桌亮黄书籍前景 + 导师直视口播 + 居中超大白黄三行封面「创业者 / 如何1年顶3年? / 苦练基本功」→ 0.65s 左下浮现透明导师履历名牌 → 1.55s~2.65s 右上角切入红白大字「?创业 死的快」强悬念",
            "hook_formula": "高回报反常识提问 + 权威数字背书",
            "first_frame_density": "极高（黄书前景视觉引导 + 超大白黄标题 + 人物）",
        },
        "title_cover_layout": {
            "layout_id": "warm_desk_keynote_pop",
            "layout_name": "暖琥珀书桌中景 + 全屏波点黑板关键词卡",
            "grid_desc": "主体固定在书堆后方（强烈橙黄品牌识别色），重点金句直接以 110px 双行巨字砸在画面中央，并频繁切全屏暗金波点剪影插画与白底工具箱表格",
            "main_sub_ratio": 1.7,
            "highlight_style": "屏中巨型双行花字 + 暗金波点剪影全屏转场 + 结构化白底表格",
        },
        "packaging_notes": {
            "ui_cadence": "57.7% 切点 1 帧精确踩在重音/音效上！穿插表格图与插画卡打破纯口播疲惫",
            "transitions": ["硬切", "全屏暗金波点图形切", "表格弹窗切入"],
            "elements": ["透明导师履历名牌", "屏中巨型双行重音字", "波点剪影隐喻插画", "4x4方法论工具表格"],
            "sfx_per_min_target": 76,
            "visual_per_min_target": 16,
        },
    },
    "style_08": {
        "style_name_cn": "真实生活随拍 · 文艺双语宋体微跳剪风",
        "archetype_id": "arch_05_cinema_slow_space",
        "domain_tags": ["超级个体", "IP价值观", "真实对话Vlog"],
        "subtitle_spec": {
            "font_family": "Source Han Serif CN",
            "font_candidates": ["SourceHanSerifCN-Medium.otf", "SourceHanSerifCN-Bold.otf", "NotoSerifSC-Medium.ttf"],
            "font_weight": "Medium",
            "bilingual": True,
            "font_size_1080p_px": 50,
            "english_font_size_px": 24,
            "keyword_scale": 1.08,
            "primary_color": "#F6F9F7",
            "keyword_color": "#FFD54F",
            "stroke_color": "#1E2221",
            "stroke_width": 2,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.67,
            "animation": "fade_slide_up",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 无任何封面贴片，直接切入餐桌旁真实边吃边聊的侧颜场景 + 直白硬核暴论「你不出干货 / 老子永远不给你流量」→ 靠手势与 2.6s 停顿微跳剪留人",
            "hook_formula": "强情绪暴论 + 沉浸式偷窥真实对话感",
            "first_frame_density": "中（真实餐饮场景细节 + 冷青电影调 + 双语宋体金句）",
        },
        "title_cover_layout": {
            "layout_id": "candid_serif_no_cover",
            "layout_name": "无包装封面直入 · 双语宋体电影感版式",
            "grid_desc": "零花字贴纸、零彩色底框，仅在极少数核心拆解处用左中偏上竖排纯白宋体双语词条（如「人群 crowd / 生活方式 Lifestyle」）",
            "main_sub_ratio": 1.1,
            "highlight_style": "中英双语思源宋体 + 零特效音克制呼吸感",
        },
        "packaging_notes": {
            "ui_cadence": "4分38秒共21次微跳剪（72%靠去气口Jump-cut推进），切点对齐语音/事件率高达 85.7%",
            "transitions": ["去气口硬跳剪 (Jump-cut)"],
            "elements": ["双语思源宋体字幕", "左中竖排无边框宋体词条"],
            "sfx_per_min_target": 15,
            "visual_per_min_target": 4,
        },
    },
    "style_09": {
        "style_name_cn": "电影剪辑工作站 · 3D 胶片墙极客风",
        "archetype_id": "arch_01_tech_ui_console",
        "domain_tags": ["AI剪辑", "影视工作流", "工具发布"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Regular.otf", "NotoSansSC-Regular.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Regular",
            "bilingual": True,
            "font_size_1080p_px": 36,
            "english_font_size_px": 20,
            "keyword_scale": 1.05,
            "primary_color": "#FCFCF6",
            "keyword_color": "#FFD54F",
            "stroke_color": "#050505",
            "stroke_width": 2,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.90,
            "animation": "hard_cut",
            "punct_stripped": False,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 3D 空间透视胶片卷轴墙高速推近（四角带取景器十字线与 `REC / FILES / SUBS` 计数动态跳变）→ 2.65s 中央定格电影感衬线大标题「D · Legend 剪辑 Skill」配英文斜体副标",
            "hook_formula": "电影级视效冲击 + 120 BPM 强鼓点踩拍",
            "first_frame_density": "极高（多画面胶片卷轴墙 + 摄像机取景器 HUD）",
        },
        "title_cover_layout": {
            "layout_id": "cyber_film_workstation",
            "layout_name": "暗黑极客剪辑室 · 2.39:1 宽幅画中画版式",
            "grid_desc": "深黑高反差基底，穿插时间线音轨波形放大镜、右侧 5 步任务清单面板、2.39:1 电影遮幅暗场画中画与黑底绿字终端 CHANGELOG 卡片",
            "main_sub_ratio": 2.2,
            "highlight_style": "取景器 HUD 细线角标 + 等宽数字计时 + 衬线主标题",
        },
        "packaging_notes": {
            "ui_cadence": "BGM 120.0 BPM 节拍强度全库第一 (0.225)，硬切与特效卡片紧密呼应鼓点",
            "transitions": ["3D镜头穿梭", "暗场UI界面切屏", "VHS复古录像带叠加"],
            "elements": ["3D胶片墙", "取景器REC角标", "2.39:1遮幅画中画", "终端CHANGELOG卡片"],
            "sfx_per_min_target": 69,
            "visual_per_min_target": 16,
        },
    },
    "style_10": {
        "style_name_cn": "暗黑幕后工作室 · 右下固定画中画教程风",
        "archetype_id": "arch_01_tech_ui_console",
        "domain_tags": ["后期教程", "口播动效", "Agent工作流"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 44,
            "keyword_scale": 1.12,
            "primary_color": "#FFFEFB",
            "keyword_color": "#FFD54F",
            "stroke_color": "#080406",
            "stroke_width": 3,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.91,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 16:9 暗黑历史作品背景墙 + 右下角常驻圆角矩形主播讲解画中画（顶部点缀黑胶唱片旋转图标）→ 1.55s~2.65s 中央垂直样片推大并在背后炸出巨型纯白粗字「幕后」",
            "hook_formula": "高呼声宠粉交付 + 幕后揭秘钩子",
            "first_frame_density": "高（暗调视频墙 + 竖屏样片框 + 右下主播画中画）",
        },
        "title_cover_layout": {
            "layout_id": "dark_studio_pip_bottom_right",
            "layout_name": "暗黑工作台 + 右下角常驻主播画中画",
            "grid_desc": "主画面区（左/中 80%）专门用来展示音频波形、三栏字体对照卡、深色对话气泡窗口，右下角锚定主播陪伴感",
            "main_sub_ratio": 2.4,
            "highlight_style": "背后巨型白字「幕后」+ 三栏蓝色卡片对照",
        },
        "packaging_notes": {
            "ui_cadence": "机位大结构仅切换 11 次，但中央 UI 演示框每 3-5 秒切换波形/对比图/聊天窗",
            "transitions": ["中心样片放大推入", "暗黑工作台UI交叉叠化"],
            "elements": ["右下圆角主播PIP", "黑胶旋转角标", "声波时间轴图示", "三栏方案对比卡"],
            "sfx_per_min_target": 78,
            "visual_per_min_target": 12,
        },
    },
    "style_11": {
        "style_name_cn": "人物前景遮罩巨字 · 高级感杂志教程风",
        "archetype_id": "arch_03_editorial_matte_depth",
        "domain_tags": ["审美分享", "剪辑包装", "高级感设计"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Medium.otf", "AlimamaShuHeiTi-Bold.otf", "NotoSansSC-Medium.ttf"],
            "font_weight": "Medium",
            "bilingual": False,
            "font_size_1080p_px": 38,
            "keyword_scale": 1.12,
            "primary_color": "#FFFFFF",
            "keyword_color": "#F4AB09",
            "stroke_color": "#1E1E1E",
            "stroke_width": 2,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.90,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s~2.65s 人物抠像作为前景，背后上下铺满横跨全屏的珊瑚红「视频包装」+ 琥珀黄「审美有多重要」超巨型粗黑体，直接以空间纵深感震慑开场",
            "hook_formula": "痛点灵魂发问 + 视觉纵深大字压迫感",
            "first_frame_density": "极高（人物前后遮罩纵深层次 + 珊瑚红/琥珀黄巨字）",
        },
        "title_cover_layout": {
            "layout_id": "depth_matte_behind_subject",
            "layout_name": "人物前景遮罩穿插巨字 + 极简高反差全屏卡",
            "grid_desc": "利用人物抠像遮罩将巨字压在人物背后；中段穿插深红渐变留白大字卡（左上「到底」+ 底部极小字罗列对比）、9宫格网页墙、2.39:1电影片断",
            "main_sub_ratio": 3.0,
            "highlight_style": "珊瑚红#E57373与琥珀黄#F4AB09巨型无描边净字 + 极简负空间",
        },
        "packaging_notes": {
            "ui_cadence": "全片 90 镜，中位镜长仅 1.35s，<1s 快切占 35.6%，J-cut 占 21.3%，快节奏但字效极少克制",
            "transitions": ["硬切", "J-cut声音先行切案例", "全屏渐变色卡切入"],
            "elements": ["人物前景抠像遮罩字", "深红渐变留白大字卡", "电影2.39:1拉片画框"],
            "sfx_per_min_target": 70,
            "visual_per_min_target": 15,
        },
    },
    "style_12": {
        "style_name_cn": "Apple/Notion 极简白底 · 人像抠像卡片风",
        "archetype_id": "arch_03_editorial_matte_depth",
        "domain_tags": ["AI工具教学", "极简PPT风", "效率办公"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 54,
            "keyword_scale": 1.12,
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFD54F",
            "stroke_color": "#2A2A2A",
            "stroke_width": 4,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.77,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 全局浅灰白背景（亮度224.7全库最亮），人像抠像位于中下，上方留白区左对齐红竖线标签「| 教程」+ 黑粗大字「用 WorkBuddy + AI」→ 2.65s 弹出第二行红黑撞色「全自动 剪视频」",
            "hook_formula": "直给方法 + 零废话高效工具组合",
            "first_frame_density": "极高信息整洁度（白底抠像 + Notion级对齐标题 + 图标）",
        },
        "title_cover_layout": {
            "layout_id": "apple_notion_light_top",
            "layout_name": "极净浅灰白底 · 上方结构化 Notion 卡片 + 下方人像抠像",
            "grid_desc": "画幅上下严密分区：Y:[0.10, 0.48] 为动态卡片舞台（红竖线分类眉标 + 黑粗标题 + 圆点要点表 + 左右对比卡`过去vs现在`），Y:[0.48, 1.0] 为半身抠像口播",
            "main_sub_ratio": 1.6,
            "highlight_style": "朱红竖线眉标(#D9383A) + 红色胶囊状态徽章 + 白底微阴影方卡",
        },
        "packaging_notes": {
            "ui_cadence": "背景/机位稳定（仅4次全局景别变更），全靠上半屏白底卡片内逐条动态列表保持高留存",
            "transitions": ["卡片内容平滑替换", "人像微缩放"],
            "elements": ["红竖线眉标", "Notion风白底要点列表", "过去vs现在左右对比卡", "流程图箭头"],
            "sfx_per_min_target": 40,
            "visual_per_min_target": 16,
        },
    },
    "style_13": {
        "style_name_cn": "建筑空间漫游 · 电影感慢调奢享风",
        "archetype_id": "arch_05_cinema_slow_space",
        "domain_tags": ["高端室内设计", "展厅探店", "奢华家居"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Regular.otf", "PingFang.ttc", "NotoSansSC-Regular.ttf"],
            "font_weight": "Regular",
            "bilingual": False,
            "font_size_1080p_px": 46,
            "keyword_scale": 1.08,
            "primary_color": "#FAFFFA",
            "keyword_color": "#FFD54F",
            "stroke_color": "#26251C",
            "stroke_width": 2,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.72,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s~2.65s 零封面大字、零花字贴纸，直接以超广角对称水景步道+RARA立体雕塑双人远景缓慢推近开篇，纯粹靠建筑空间高级感建立信任",
            "hook_formula": "第一眼高端实景奇观 + 大师名号背书",
            "first_frame_density": "高实景质感（建筑水景倒影 + 极低文字干扰）",
        },
        "title_cover_layout": {
            "layout_id": "architectural_no_overlay",
            "layout_name": "零遮挡实景长镜 · 电影字幕极简版式",
            "grid_desc": "全片无封面大字板、无弹幕花字，所有视觉空间留给暖木石材光影与慢推运镜；字幕小号细字投影居下，不挡空间结构",
            "main_sub_ratio": 1.0,
            "highlight_style": "暖木温润调色(warmth=26.5) + 极简无框细投影字幕",
        },
        "packaging_notes": {
            "ui_cadence": "7分53秒深度漫游（34个大场切换，包含70次平滑叠化/空间缓推运镜），95.2 BPM 舒缓氛围乐",
            "transitions": ["缓慢推拉长镜", "柔和叠化"],
            "elements": ["极简细字投影字幕"],
            "sfx_per_min_target": 20,
            "visual_per_min_target": 2,
        },
    },
    "style_14": {
        "style_name_cn": "剪映门店原生爆款风 · 居中双字号暖黄高亮（默认）",
        "archetype_id": "arch_04_punch_talking_head",
        "domain_tags": ["门店老板", "同城获客", "实拍口播", "默认旗舰风格"],
        "subtitle_spec": {
            "font_family": "Alimama ShuHeiTi",
            "font_candidates": ["AlimamaShuHeiTi-Bold.otf", "SourceHanSansCN-Bold.otf", "NotoSansCJKsc-Bold.otf"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 62,
            "keyword_scale": 1.133,
            "primary_color": "#FFFFFF",
            "keyword_color": "#FFE28A",
            "stroke_color": "#000000",
            "stroke_width": 4,
            "shadow": True,
            "shadow_color": "#000000C8",
            "shadow_offset": [2, 3],
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.682,
            "animation": "none_crisp_cut",
            "punct_stripped": True,
            "max_chars_per_line": 10,
            "symmetry_center_x": 540,
            "symmetry_max_width_px": 640,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.00–1.37s 老板在门店实景前正脸开讲第一句 → 1.37–2.83s 切强动作/态度的关键定格帧 + 1.43s 锣声/重音起音 + 1.53–2.83s 同步弹出五层多方位无底框花字（头顶居中巨型红字 + 左右双翼金黄字 + 底部暗红自省印章标签）",
            "hook_formula": "门店真事冲突 + 定格强态度 + 多方位花字连环炸 + 引子BGM切主BGM",
            "first_frame_density": "极高（门店真实背景 + 老板主体 + 无底框高反差红黄大字 + 利益引导）",
        },
        "title_cover_layout": {
            "layout_id": "jianying_poster_4zone",
            "layout_name": "门店老板白边人像 + 四区高低错落爆款封面",
            "grid_desc": "门店门头实景铺底 + 右下老板半身人像白边轮廓 + 左上『小白字引子 + 正红黑边巨字主标题』 + 中左微倾斜明黄黑边状态签 + 右下『白字福利引子 + 红白双色毛笔大字行动钩子』",
            "main_sub_ratio": 1.75,
            "highlight_style": "正红黑边主爆字(#FF2222) + 明黄黑边倾斜贴纸(#FFE234) + 红白双色福利大字",
        },
        "packaging_notes": {
            "ui_cadence": "主轨口播物理切净气口零缝紧拼（中位1.87s），句群级B-roll覆盖率53.3%（中位1.67s），动作指代句保留-10dB现场动作真声；开头2.8s引子BGM切正文轻快主BGM；片尾2.7s独立金黄大字+评论区引导尾卡",
            "transitions": ["零缝紧剪硬切", "定格爆点切入", "句群B-roll覆盖切回"],
            "elements": ["无底框头顶红字爆点", "左右双翼金黄花字", "底部暗红印章小字", "片尾金黄大字尾卡"],
            "sfx_per_min_target": 24,
            "visual_per_min_target": 8,
            "broll_overlay_ratio_target": 0.53,
            "broll_ambient_sound_policy": {"enabled": True, "gain_db": -10.0, "trigger_action_words": ["敲", "响", "滋啦", "切", "倒", "沸", "烤", "炒", "砸", "摔"]},
            "dual_bgm_structure": {"enabled": True, "intro_duration_sec": 2.83, "intro_fade_out_sec": 0.57, "main_bgm_gain_db": -11.0, "main_fade_in_sec": 0.57, "main_fade_out_sec": 1.0},
            "multi_zone_flower_spec": {
                "center_burst_color": "#FF4C2C",
                "flank_color": "#FFE28A",
                "stamp_color": "#B71C1C",
                "end_card_color": "#FFD637",
                "stroke_color": "#000000",
                "borderless": True,
            },
        },
    },
    "style_15": {
        "style_name_cn": "AI Agent 流程导航 · 工牌/账单小票风",
        "archetype_id": "arch_01_tech_ui_console",
        "domain_tags": ["数字人", "AI全自动", "工作流复盘"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 40,
            "keyword_scale": 1.15,
            "primary_color": "#FBFFFE",
            "keyword_color": "#FBE088",
            "stroke_color": "#1E221D",
            "stroke_width": 3,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.90,
            "animation": "pop_spring",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 16:9 顶部常驻步骤条导航(`①写稿 ②配音 ③数字人 ④动效`) + 右上`● REC`计时器 → 1.55s 人物背后穿插巨型黄字「不是 枝鸥」+ 左下红叉名牌 → 2.65s 右侧挂出晃动黄色工牌吊绳「数字人」",
            "hook_formula": "身份反转钩子(不是真人而是数字人) + 透明算账悬念",
            "first_frame_density": "极高（顶部流程条 + 左下名牌 + 人物前后遮罩大字）",
        },
        "title_cover_layout": {
            "layout_id": "agent_workflow_step_bar",
            "layout_name": "顶部步骤条进度导航 + 拟物工牌与制片小票 UI",
            "grid_desc": "顶部全时展示工作流当前进度胶囊，中段左下方固定方框口播画中画，主区展示深色终端卡片、高亮流程表格与结尾白底`制片小票`账单清单",
            "main_sub_ratio": 2.6,
            "highlight_style": "亮黄#FBE088粗字黑描边 + 挂绳工牌卡 + 制片账单小票",
        },
        "packaging_notes": {
            "ui_cadence": "音频事件 1 帧精确对齐率 80.0%（全库最高！），3 帧对齐率 90.0%，完美践行以音定画",
            "transitions": ["工作流步骤高亮跳转", "人物居中缩入左下PIP"],
            "elements": ["顶部步骤条导航", "黄色工牌吊绳", "深色终端清单", "制片小票账单UI"],
            "sfx_per_min_target": 72,
            "visual_per_min_target": 18,
        },
    },
    "style_16": {
        "style_name_cn": "商业悬疑纪实 · 撕纸拍立得拼贴风",
        "archetype_id": "arch_02_high_contrast_story",
        "domain_tags": ["财经深度", "企业兴衰", "商业人物"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 48,
            "keyword_scale": 1.25,
            "primary_color": "#FFF8F3",
            "keyword_color": "#E1A187",
            "stroke_color": "#26150E",
            "stroke_width": 3,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.84,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 冷峻灰暗高楼群航拍俯冲镜头 → 0.65s 底部旋入复古红棕拍立得人物口播画中画 → 1.55s~2.65s 航拍画面上弹出巨型数字「360亿 / 最惨的大佬之一了」",
            "hook_formula": "震撼数字钩子(360亿) + 极端处境悬念(遭全网悬赏)",
            "first_frame_density": "高（航拍实景压迫感 + 拍立得画中画 + 巨型数字）",
        },
        "title_cover_layout": {
            "layout_id": "polaroid_torn_paper_finance",
            "layout_name": "拍立得画中画 + 报刊撕纸拼贴",
            "grid_desc": "混合使用复古棕色拍立得边框、白边锯齿撕纸上下分拼贴、米白报刊衬线章节卡（`四面楚歌 UNDER SIEGE`）与红色数据柱条",
            "main_sub_ratio": 2.4,
            "highlight_style": "超大白色数字`360亿` + 朱红报刊衬线标题 + 锯齿撕纸分割线",
        },
        "packaging_notes": {
            "ui_cadence": "5分13秒共 118 镜（med=2.20s），音效密度高达 89.7/min，全程营造调查纪录片紧张感",
            "transitions": ["航拍转拍立得画中画", "锯齿撕纸分屏", "报刊全屏章节卡"],
            "elements": ["复古拍立得边框", "锯齿撕纸上下分屏", "报刊衬线章节卡", "红条数据标"],
            "sfx_per_min_target": 88,
            "visual_per_min_target": 20,
        },
    },
    "style_17": {
        "style_name_cn": "深蓝 SaaS 演示 · 右下圆角画中画风",
        "archetype_id": "arch_01_tech_ui_console",
        "domain_tags": ["AI助手", "房产/垂直Demo", "产品讲解"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Medium.otf", "NotoSansSC-Medium.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Medium",
            "bilingual": False,
            "font_size_1080p_px": 46,
            "keyword_scale": 1.15,
            "primary_color": "#F9FFFF",
            "keyword_color": "#F1E1B3",
            "stroke_color": "#0E172B",
            "stroke_width": 2,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.70,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 1帧黑场渐入 → 0.65s~1.55s 深蓝科技面板上半区直接嵌横屏探房成片播放窗口（附带亮黄胶囊亮点条），右下角固定圆角真人画中画同步口播 → 2.65s 上半区切换竖屏成片演示对比",
            "hook_formula": "成片效果直接展示 + 解决复杂剪辑痛点",
            "first_frame_density": "高（深蓝科技底板 + 成片实时小窗 + 真人圆角PIP）",
        },
        "title_cover_layout": {
            "layout_id": "dark_navy_saas_pip",
            "layout_name": "深海蓝科技面板 · 上2/3演示区 + 右下真人PIP",
            "grid_desc": "深蓝(#0E172B -> #17264B)渐变面板贯穿全片；上半区切换横/竖成片窗、机器人架构流程图、蓝色微信气泡对话；左下角呈现 `STEP 01` 衬线+黑体混排标卡",
            "main_sub_ratio": 1.8,
            "highlight_style": "深蓝微边框卡片 + 暖金胶囊标(`#F1E1B3`) + 衬线混排步骤卡",
        },
        "packaging_notes": {
            "ui_cadence": "J-cut 比例全库最高（28.6%），音频先进后切换上方演示卡片，极具现代 SaaS 发布会质感",
            "transitions": ["演示卡片无缝切换", "流程图节点点亮"],
            "elements": ["深蓝科技边框卡片", "右下圆角真人PIP", "机器人节点架构图", "STEP混排标卡"],
            "sfx_per_min_target": 75,
            "visual_per_min_target": 16,
        },
    },
    "style_18": {
        "style_name_cn": "白板方法论 · 手写字纸片反差条风",
        "archetype_id": "arch_03_editorial_matte_depth",
        "domain_tags": ["编导教学", "短视频运营", "实战复盘"],
        "subtitle_spec": {
            "font_family": "QingSongShouXieTi / Source Han Sans CN",
            "font_candidates": ["QingSongShouXieTi1.ttf", "AlimamaShuHeiTi-Bold.otf", "SourceHanSansCN-Bold.otf"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 64,
            "keyword_scale": 1.18,
            "primary_color": "#1E1E1E",
            "keyword_color": "#FFD54F",
            "stroke_color": "#FFFFFF",
            "stroke_width": 0,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.75,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 人物抠像置前站立白板前，背后透出白底黑红混排手写大字「编导八大问 怎么 运用 / 一条视频 教会你」→ 0.65s 切至浅灰背景 + 半透明水印大字「欧阳」+ 上方实拍花絮方窗 → 2.65s 弹出纯黑底白字矩形标签「编导八大问」",
            "hook_formula": "干货工具名直接亮剑 + 实拍花絮场景代入",
            "first_frame_density": "极高（白板前景抠像 + 黑红手写斜标 + 实景白板手写字）",
        },
        "title_cover_layout": {
            "layout_id": "whiteboard_matte_sticky",
            "layout_name": "浅灰手写水印底板 + 黑白反差条 + 暗调白板清单",
            "grid_desc": "三段式质感切换：白板抠像开场 → 浅灰底(上方实拍花絮小窗 + 黑色反白条 + 手写黑字字幕) → 暗调白板透明黑底压出白色/亮黄竖排结构清单",
            "main_sub_ratio": 1.5,
            "highlight_style": "纯黑底白字矩形反差块 + 手写字体亲和力 + 暗调亮黄关键句",
        },
        "packaging_notes": {
            "ui_cadence": "39镜稳健教学节奏（med=2.71s），在浅灰高对比与暗调清单间切换保持高注意力",
            "transitions": ["硬切进花絮窗", "暗调蒙版清单展开"],
            "elements": ["前景抠像白板透字", "背景水印手写字", "纯黑反差矩形条", "暗调结构化清单"],
            "sfx_per_min_target": 50,
            "visual_per_min_target": 12,
        },
    },
    "style_19": {
        "style_name_cn": "专业剪辑大师课 · 顶部细条章节 + 绿块冲击风",
        "archetype_id": "arch_02_high_contrast_story",
        "domain_tags": ["影视剪辑", "调色音效", "专业教程"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Bold",
            "bilingual": False,
            "font_size_1080p_px": 46,
            "keyword_scale": 1.20,
            "primary_color": "#FFFFFF",
            "keyword_color": "#2ECC71",
            "stroke_color": "#111111",
            "stroke_width": 3,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.90,
            "animation": "hard_cut",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 16:9 顶部常驻极细4栏章节条(`剪辑技巧|音效设计|调色思维|调色设备`) + 女主自然口播 → 2.65s 胸前猛然弹出 3 个亮绿渐变方框白字「流 水 账」直击痛点",
            "hook_formula": "共鸣痛点反问 + 3大法宝结构预告",
            "first_frame_density": "高（顶部章节条 + 背景浮世绘/双屏工作室质感 + 清晰人物）",
        },
        "title_cover_layout": {
            "layout_id": "pro_editor_chapter_nav",
            "layout_name": "顶部极细章节导航条 + 亮绿圆角方块与左右竖排巨字",
            "grid_desc": "顶部 32px 高灰黑导航条全片高亮当前章节；关键重音用高饱和翠绿方框单字排列或黑底双侧竖排超大白字(`音 效 / 设 计`)夹击中间工程软件画面",
            "main_sub_ratio": 2.5,
            "highlight_style": "高饱和亮绿渐变方框单字块(#10B981) + 双侧竖排巨型白字",
        },
        "packaging_notes": {
            "ui_cadence": "全片 137 镜，med=1.25s（<1s快切 35.8%），146.3 BPM 强动感，82.4% 切点精准对齐事件",
            "transitions": ["高速B-roll素材硬切", "双侧竖排巨字黑底切"],
            "elements": ["顶部4栏极细章节导航", "绿底方框单字花字", "双侧竖排白字夹心画中画", "达芬奇示波器录屏"],
            "sfx_per_min_target": 64,
            "visual_per_min_target": 18,
        },
    },
    "style_20": {
        "style_name_cn": "模型盒悬念开篇 · 书墙双语毛玻璃金句风",
        "archetype_id": "arch_03_editorial_matte_depth",
        "domain_tags": ["设计行业洞察", "商业思辨", "知识女性IP"],
        "subtitle_spec": {
            "font_family": "Source Han Sans CN",
            "font_candidates": ["SourceHanSansCN-Medium.otf", "NotoSansSC-Medium.ttf", "STHeiti Medium.ttc"],
            "font_weight": "Medium",
            "bilingual": True,
            "font_size_1080p_px": 42,
            "english_font_size_px": 24,
            "keyword_scale": 1.10,
            "primary_color": "#FFFFFF",
            "keyword_color": "#E6D267",
            "stroke_color": "#120B01",
            "stroke_width": 3,
            "bg_box_color": "#00000000",
            "bg_radius": 0,
            "safe_y_ratio": 0.87,
            "animation": "fade_slide_up",
            "punct_stripped": True,
        },
        "opening_structure": {
            "0_to_3s_pattern": "0.0s 先以一个实拍手工立体纸盒模型（写着「室内设计的行业真相 / 真 相 THE TRUTH」）强悬念定格 0.5s → 0.65s 秒切满墙堆书背景人物口播 → 1.55s 左上弹出粉白描边贴纸「AI」",
            "hook_formula": "实物悬念视觉奇观 + 行业尖锐二选一发问",
            "first_frame_density": "极高（实拍微缩模型盒+红色立体符号+衬线大字）",
        },
        "title_cover_layout": {
            "layout_id": "physical_prop_bookshelf_highlight",
            "layout_name": "实物道具开篇 + 毛玻璃编号清单 + 荧光黄绿底块金句",
            "grid_desc": "高密度书墙暖调背景，中段用半透明暗色毛玻璃左对齐罗列 1~5.5 条思考清单，高潮金句用荧光黄绿底块(`#D9E35B`)衬托加粗黑字横向铺满画面",
            "main_sub_ratio": 1.8,
            "highlight_style": "中英同步关键词高亮 + 荧光黄绿底块黑字金句条",
        },
        "packaging_notes": {
            "ui_cadence": "88镜，med=1.27s（<1s快切 38.6%），音效密度 89.2/min，J-cut 16.1%，极佳的思辨口播节奏",
            "transitions": ["实物盒子跳切人物", "背景虚化+毛玻璃清单浮现"],
            "elements": ["实物微缩开篇模型", "卡通描边贴纸(AI)", "暗色毛玻璃编号清单", "荧光黄绿金句底块"],
            "sfx_per_min_target": 88,
            "visual_per_min_target": 16,
        },
    },
}


def hex_to_ass_bgr(hex_str: str) -> str:
    h = hex_str.strip().lstrip("#")
    if len(h) >= 6:
        return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}"
    return "&H00FFFFFF"


def hex_to_ass_abgr(hex_str: str) -> str:
    h = hex_str.strip().lstrip("#")
    if len(h) == 8:
        # RGBA -> ASS AABBGGRR (alpha inverted in ASS: 00 opaque, FF transparent)
        a = 255 - int(h[6:8], 16)
        return f"&H{a:02X}{h[4:6]}{h[2:4]}{h[0:2]}"
    if len(h) == 6:
        return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}"
    return "&HAA000000"


def build_all_assets() -> None:
    raw = read_json(STYLE_DIR / "style_spectrum_raw.json")
    merged_styles: List[Dict[str, Any]] = []

    # 读取现有 templates.json，保留原 4 套基础模板
    tpl_path = ASSETS_DIR / "subtitle_templates" / "templates.json"
    existing_tpl_data = read_json(tpl_path)
    base_templates = {
        k: v for k, v in existing_tpl_data.get("templates", {}).items()
        if not k.startswith("style_")
    }

    css_blocks: List[str] = [
        "/* 自动生成的 20 套参考视频「风格谱」HyperFrames CSS 变量预设 */",
        ":root {",
        "  --hf-font-sans: 'Alimama ShuHeiTi', 'Source Han Sans CN', 'Noto Sans SC', 'PingFang SC', sans-serif;",
        "  --hf-font-serif: 'Source Han Serif CN', 'Noto Serif SC', 'Songti SC', serif;",
        "  --hf-font-hand: 'QingSongShouXieTi1', 'Ma Shan Zheng', 'Kaiti SC', cursive;",
        "}",
        "",
    ]

    for s in raw["styles"]:
        sid = s["style_id"]
        cur = CURATED_VISUAL_SPECS[sid]
        meta = s["meta"]
        shots = s["shot_distribution"]
        color = s["color_and_lut"]
        audio = s["audio_and_rhythm"]
        raw_sub = s["subtitle_and_layout"]
        sub = cur["subtitle_spec"]

        # 计算换算至标准竖屏 1080x1920 下的字号与安全区坐标
        is_portrait = meta["orientation"] == "portrait_9_16"
        safe_y_px = int(round(sub["safe_y_ratio"] * 1920))
        safe_y_px = max(240, min(1440, safe_y_px)) if is_portrait else int(round(sub["safe_y_ratio"] * 1080))

        # 判断默认推荐轨道
        rec_track = "store" if sid in ("style_05", "style_13", "style_14", "style_18") else "creator"

        # 合并完整的风格谱单视频档案
        merged = {
            "style_id": sid,
            "index": s["index"],
            "style_name_cn": cur["style_name_cn"],
            "archetype_id": cur["archetype_id"],
            "recommended_track": rec_track,
            "domain_tags": cur["domain_tags"],
            "source_filename": s["filename"],
            "meta": meta,
            "shot_distribution": {
                **{k: v for k, v in shots.items() if k not in ("cut_frames",)},
                "cut_times_sample_first15": shots.get("cut_times_sec", [])[:15],
            },
            "beat_alignment": {
                "estimated_bpm": audio.get("estimated_bpm", 110.0),
                "beat_strength_score": audio.get("beat_strength_score", 0.05),
                "bgm_presence": audio.get("bgm_presence", "soft_ambient_bgm"),
                "cut_beat_align_2f_pct": audio.get("cut_beat_align_2f_pct", 0.0),
                "cut_event_align_1f_pct": audio.get("cut_event_align_1f_pct", 0.0),
                "cut_event_align_3f_pct": audio.get("cut_event_align_3f_pct", 0.0),
                "j_cut_detected_pct": audio.get("j_cut_detected_pct", 0.0),
                "j_cut_median_lead_frames": audio.get("j_cut_median_lead_frames", 0.0),
                "recommended_j_cut_frames": 3 if audio.get("j_cut_detected_pct", 0) >= 8.0 else 0,
            },
            "audio_and_sfx": {
                "integrated_lufs": audio.get("integrated_lufs", -14.0),
                "loudness_range_lu": audio.get("loudness_range_lu", 6.0),
                "true_peak_dbtp": audio.get("true_peak_dbtp", -1.0),
                "silence_count": audio.get("silence_count", 0),
                "mean_pause_sec": audio.get("mean_pause_sec", 0.2),
                "sfx_transient_count": audio.get("sfx_transient_count", 0),
                "sfx_per_min_measured": audio.get("sfx_per_min", 60.0),
                "sfx_per_min_target": cur["packaging_notes"]["sfx_per_min_target"],
            },
            "subtitle_spec": {
                **sub,
                "measured_y_center_ratio": raw_sub.get("y_center_ratio"),
                "measured_animation_raw": raw_sub.get("animation_mode"),
                "safe_y_px_1080x1920": int(round(sub["safe_y_ratio"] * 1920)),
                "safe_y_px_1920x1080": int(round(sub["safe_y_ratio"] * 1080)),
            },
            "color_and_lut": {
                **color,
                "lut_relative_path": f"assets/style_spectrum/luts/{sid}.cube",
            },
            "opening_structure": cur["opening_structure"],
            "title_cover_layout": cur["title_cover_layout"],
            "transition_and_packaging": cur["packaging_notes"],
            "assets": {
                "lut_cube": f"luts/{sid}.cube",
            },
        }
        merged_styles.append(merged)

        # 1) 写入 subtitle_templates/templates.json 兼容格式
        ass_pri = hex_to_ass_bgr(sub["primary_color"])
        ass_sec = hex_to_ass_bgr(sub["keyword_color"])
        ass_out = hex_to_ass_bgr(sub["stroke_color"])
        ass_bak = hex_to_ass_abgr(sub["bg_box_color"])
        base_templates[sid] = {
            "name": f"[{sid}] {cur['style_name_cn']}",
            "recommended_track": rec_track,
            "archetype_id": cur["archetype_id"],
            "font_candidates": sub["font_candidates"],
            "license": "SIL OFL 1.1 / 免费可商用字体链",
            "font_size": sub["font_size_1080p_px"],
            "keyword_scale": sub["keyword_scale"],
            "primary_color": sub["primary_color"],
            "keyword_color": sub["keyword_color"],
            "stroke_color": sub["stroke_color"],
            "stroke_width": sub["stroke_width"],
            "bg_box_color": sub["bg_box_color"],
            "bg_radius": sub["bg_radius"],
            "safe_y_center": min(1420, max(1180, int(round(sub["safe_y_ratio"] * 1920)))),
            "safe_x_min": 220,
            "safe_x_max": 860,
            "symmetry_center_x": 540,
            "symmetry_max_width_px": 640,
            "bilingual": sub["bilingual"],
            "animation": sub["animation"],
            "punct_stripped": sub["punct_stripped"],
            "lut_cube": f"assets/style_spectrum/luts/{sid}.cube",
            "ass_style": (
                f"Style: Default,{sub['font_family']},{sub['font_size_1080p_px']},"
                f"{ass_pri},{ass_sec},{ass_out},{ass_bak},-1,0,0,0,100,100,1,0,1,"
                f"{sub['stroke_width']},2,2,220,220,460,1"
            ),
        }

        # 2) HyperFrames CSS 主题块
        accent_palette = color.get("top5_palette_hex", ["#111111", "#FFFFFF", "#FFD54F"])
        font_var = (
            "var(--hf-font-serif)" if "Serif" in sub["font_family"]
            else "var(--hf-font-hand)" if "QingSong" in sub["font_family"]
            else "var(--hf-font-sans)"
        )
        bg_primary = accent_palette[0] if accent_palette else "#0F172A"
        css_blocks.extend([
            f".{sid.replace('_', '-')} {{",
            f"  --sp-font-family: {font_var};",
            f"  --sp-sub-size: {sub['font_size_1080p_px']}px;",
            f"  --sp-sub-y: {round(sub['safe_y_ratio'] * 100, 1)}%;",
            f"  --sp-sub-color: {sub['primary_color']};",
            f"  --sp-kw-color: {sub['keyword_color']};",
            f"  --sp-kw-scale: {sub['keyword_scale']};",
            f"  --sp-stroke-color: {sub['stroke_color']};",
            f"  --sp-stroke-width: {sub['stroke_width']}px;",
            f"  --sp-bg-box: {sub['bg_box_color']};",
            f"  --sp-bg-canvas: {bg_primary};",
            f"  --sp-color-filter: {color['css_filter_approx']};",
            f"  --sp-bpm: {audio.get('estimated_bpm', 110.0)};",
            "}",
            "",
        ])

    # 写出 1. style_spectrum_library.json
    lib = {
        "version": "1.1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": raw["source_dir"],
        "total_videos": len(merged_styles),
        "archetypes": ARCHETYPES,
        "styles": merged_styles,
    }
    write_json(STYLE_DIR / "style_spectrum_library.json", lib)

    # 写出 2. subtitle_templates/templates.json（确保零不可商用系统字体残留）
    for _tname, _tpl in base_templates.items():
        if isinstance(_tpl, dict) and "font_candidates" in _tpl:
            cleaned_fc = [
                f for f in (_tpl["font_candidates"] or [])
                if "stheiti" not in str(f).lower() and "pingfang" not in str(f).lower() and "msyh" not in str(f).lower()
            ]
            if "AlimamaShuHeiTi-Bold.otf" not in cleaned_fc:
                cleaned_fc.append("AlimamaShuHeiTi-Bold.otf")
            _tpl["font_candidates"] = cleaned_fc
    write_json(tpl_path, {"templates": base_templates})

    # 写出 3. STYLE_SPECTRUM_REPORT.md 深度拆解手册（废除已删冗余 assets/style_spectrum/hyperframes）
    report_path = STYLE_DIR / "STYLE_SPECTRUM_REPORT.md"
    generate_markdown_report(lib, report_path)

    print(
        f"[build-assets] 成功构建风格谱资产库：{len(merged_styles)} 套风格 + {len(ARCHETYPES)} 大母版 ->\n"
        f"  - {STYLE_DIR / 'style_spectrum_library.json'}\n"
        f"  - {tpl_path}\n"
        f"  - {report_path}"
    )


def generate_markdown_report(lib: Dict[str, Any], out_md: Path) -> None:
    """从 style_spectrum_library.json 确定性生成完整《参考视频逐帧分析与风格谱手册》。"""
    styles = lib["styles"]
    arches = lib["archetypes"]

    total_sec = sum(s["meta"]["duration_sec"] for s in styles)
    total_frames = sum(s["shot_distribution"]["total_frames_analyzed"] for s in styles)
    n_portrait = sum(1 for s in styles if s.get("meta", {}).get("orientation") == "portrait_9_16")
    n_landscape = len(styles) - n_portrait

    lines: List[str] = [
        f"# {len(styles)}条参考包装视频逐帧拆解与「风格谱（Style Spectrum）」全量手册",
        "",
        f"> **样本规模**：共 **{len(styles)} 条**参考视频（竖屏 9:16 {n_portrait}条 / 横屏 16:9 {n_landscape}条），累计时长 **{total_sec:.1f} 秒（{total_sec/60:.1f} 分钟）**，全帧扫描 **{total_frames:,} 帧**。",
        f"> **生成时间**：`{lib['generated_at']}` | **数据主库**：[style_spectrum_library.json](style_spectrum_library.json)",
        "",
        "---",
        "",
        "## 一、 全局 20 条「风格谱」量化总览表",
        "",
        "| ID | 风格名称 | 画幅 / 帧率 | 时长 | 总镜数 (硬切/跳剪) | 中位镜长 (`<1s`快切) | 切点事件对齐 (`≤1f` / `≤3f`) | BPM (响度) | 音效密度 | 字幕字号/位置/动效 | LUT 与调性 |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |",
    ]

    for s in styles:
        m = s["meta"]
        sh = s["shot_distribution"]
        ba = s["beat_alignment"]
        ax = s["audio_and_sfx"]
        sb = s["subtitle_spec"]
        cl = s["color_and_lut"]
        lines.append(
            f"| `{s['style_id']}` | {s['style_name_cn']} | {m['aspect_ratio']} / {m['fps']:.0f}p | {m['duration_sec']:.1f}s | "
            f"{sh['total_shots']} ({sh['hard_cuts']}/{sh['jump_or_punch_cuts']}) | "
            f"{sh['median_sec']:.2f}s ({sh['pct_under_1s']:.1f}%) | "
            f"{ba['cut_event_align_1f_pct']:.1f}% / {ba['cut_event_align_3f_pct']:.1f}% | "
            f"{ba['estimated_bpm']:.1f} ({ax['integrated_lufs']:.1f} LUFS) | "
            f"{ax['sfx_per_min_measured']:.1f}/min | "
            f"{sb['font_size_1080p_px']}px · Y={sb['safe_y_ratio']:.2f} · `{sb['animation']}` | "
            f"`{cl['lut_file']}` ({cl['tone_mood']}) |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 二、 提炼六大包装流派母版 (Archetypes)",
        "",
        "| 流派 ID | 流派名称 | 包含样本 | 适用场景 | 色彩与字幕特征 | 节奏与对齐公式 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    for a in arches:
        lines.append(
            f"| `{a['archetype_id']}` | **{a['name']}** | {', '.join(f'`{x}`' for x in a['members'])} | "
            f"{a['best_for']} | {a['color_signature']}；{a['subtitle_signature']} | {a['rhythm_signature']} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 三、 20 条参考视频逐一 9 维深度拆解档案",
        "",
    ])

    for s in styles:
        sid = s["style_id"]
        m = s["meta"]
        sh = s["shot_distribution"]
        ba = s["beat_alignment"]
        ax = s["audio_and_sfx"]
        sb = s["subtitle_spec"]
        cl = s["color_and_lut"]
        op = s["opening_structure"]
        cv = s["title_cover_layout"]
        pk = s["transition_and_packaging"]

        lines.extend([
            f"### {sid.upper()} · {s['style_name_cn']}",
            f"- **原片文件**：`{s['source_filename']}`（`{m['width']}×{m['height']}` · `{m['fps']} fps` · `{m['duration_sec']:.1f}s` · `{sh['total_frames_analyzed']} 帧`）",
            f"- **所属流派**：`{s['archetype_id']}` | **推荐轨道**：`{s['recommended_track']}` | **适用标签**：{' · '.join(s['domain_tags'])}",
            "",
            "| 风格谱维度 | 逐帧实测与视觉精校参数 |",
            "| :--- | :--- |",
            f"| **1. 镜头时长分布** | 共 **{sh['total_shots']}** 镜（硬切 {sh['hard_cuts']} / 微跳剪·推拉 {sh['jump_or_punch_cuts']} / 叠化擦除 {sh['dissolve_or_wipe_transitions']}），切镜率 **{sh['cuts_per_min']:.1f}/min**；前 3 秒切换 **{sh['first_3s_cuts_count']}** 次；`min={sh['min_sec']:.2f}s` / `P25={sh['p25_sec']:.2f}s` / `median={sh['median_sec']:.2f}s` / `mean={sh['mean_sec']:.2f}s` / `P90={sh['p90_sec']:.2f}s` / `max={sh['max_sec']:.2f}s`；分桶占比：`<1s` 占 **{sh['pct_under_1s']}%**，`1–2.5s` 占 **{sh['pct_1s_to_2_5s']}%**，`>2.5s` 占 **{sh['pct_over_2_5s']}%** |",
            f"| **2. 切点与节拍对齐率** | 切点对齐音频事件（节拍/气口句首/瞬态音效）`≤1帧` 率 **{ba['cut_event_align_1f_pct']}%**，`≤3帧` 率 **{ba['cut_event_align_3f_pct']}%**；纯踩 BGM 节拍（`≤2帧`）率 **{ba['cut_beat_align_2f_pct']}%**；检出 J-cut 比例 **{ba['j_cut_detected_pct']}%**（中位提前 `{ba['j_cut_median_lead_frames']} 帧`，建议 J-cut `{ba['recommended_j_cut_frames']} 帧`） |",
            f"| **3. 字幕样式与动效** | 字体族 **`{sb['font_family']}`**（候选：`{', '.join(sb['font_candidates'])}`），字重 `{sb['font_weight']}`，双语={'是' if sb['bilingual'] else '否'}；1080p 基准字号 **`{sb['font_size_1080p_px']}px`**，垂直位置 `Y={sb['safe_y_ratio']:.2f}`（竖屏 `{sb['safe_y_px_1080x1920']}px`）；主色 `{sb['primary_color']}`，关键词色 `{sb['keyword_color']}`（放大 `{sb['keyword_scale']}x`）；描边 `{sb['stroke_width']}px {sb['stroke_color']}`，底框 `{sb['bg_box_color']}`；动效 **`{sb['animation']}`**；{'去掉句末标点' if sb['punct_stripped'] else '保留标点'} |",
            f"| **4. 色彩统计与 LUT** | 调性：**{cl['tone_mood']}**；平均亮度 `Y={cl['y_mean_255']}/255`（动态范围 `{cl['dynamic_range_255']}`，暗部占比 `{cl['dark_ratio_pct']}%`，高光占比 `{cl['bright_ratio_pct']}%`）；饱和度 `{cl['sat_mean_pct']}%`，色温偏移 `{cl['warmth_index']}`；Top-5 主色板 `{' '.join(cl['top5_palette_hex'])}`；逆向 3D LUT：[{cl['lut_file']}](luts/{cl['lut_file']})（CSS 近似：`{cl['css_filter_approx']}`） |",
            f"| **5. 转场和音效密度** | 综合转场密度 **{sh['transitions_per_min']:.1f}/min**，常用手法：{'、'.join(pk['transitions'])}；高频瞬态音效实测 **{ax['sfx_per_min_measured']:.1f}/min**（总计 {ax['sfx_transient_count']} 次，建议预算 `{pk['sfx_per_min_target']}/min`）；核心包装组件：{'、'.join(pk['elements'])}；内部 UI/卡片节奏：{pk['ui_cadence']} |",
            f"| **6. BGM 速度与混音** | 估算 BGM 速度 **{ba['estimated_bpm']} BPM**（节拍显著度 `{ba['beat_strength_score']}`，类型 `{ba['bgm_presence']}`）；综合响度 **{ax['integrated_lufs']} LUFS**（LRA `{ax['loudness_range_lu']} LU`，真峰值 `{ax['true_peak_dbtp']} dBTP`）；检出气口停顿 {ax['silence_count']} 次（平均 `{ax['mean_pause_sec']}s`） |",
            f"| **7. 开头黄金 3–5 秒** | **时序拆解**：{op['0_to_3s_pattern']}<br>**钩子公式**：{op['hook_formula']} | **首帧信息密度**：{op['first_frame_density']} |",
            f"| **8. 标题封面版式** | **版式类型**：`{cv['layout_id']}`（{cv['layout_name']}）<br>**构图网格**：{cv['grid_desc']}<br>**主副标题字号比**：`{cv['main_sub_ratio']}x` | **高亮手法**：{cv['highlight_style']} |",
            "",
            "```bash",
            f"# 一键应用 {sid} 并导出 HyperFrames 样板 HTML：",
            f"python3 short-video-studio/scripts/apply_style_spectrum.py --style-id {sid} \\",
            f"  --export-hyperframes ./out_{sid}/hyperframes_comp.html",
            "```",
            "",
        ])

    lines.extend([
        "---",
        "",
        "## 四、 复刻接入指南",
        "",
        "1. **HyperFrames 复刻**：使用 `apply_style_spectrum.py --style-id style_XX --export-hyperframes <out.html>` 导出零外链样板预览。",
        "2. **`short-video-studio` 流水线一键应用**：运行 `apply_style_spectrum.py --style-id style_XX --apply-pipeline <project>/pipeline`，自动同步 `.cube` LUT 与字幕模板至当前项目。",
        "",
    ])

    out_md.write_text("\n".join(lines), encoding="utf-8")



if __name__ == "__main__":
    build_all_assets()
