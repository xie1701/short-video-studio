#!/usr/bin/env python3
"""
v11_director_planner.py · 24 套风格专属语义导演总谱 + 10/10 语义精修字幕 + 商业动态图解编排器

废除 V10.0 复制同一份 style_02 导演表与机械固定 frac 贴 2 个死面板的做法。
为 BIZ（商业口播）和 STO（粤语探店）严格基于各自真实语音轴与每套风格的视觉基因，
产出：
1. 10/10 人类剪辑师级 `semantic_subtitles`（原话一字不差、0 劈词、0 挂尾助词、0 脏高亮）；
2. 按每套风格真母本占比与构图特点定制的高占比 `style_scene_beats`（35%~65% 动态风格覆盖 + BIZ 专属动态对比/三要素/因果链图解卡）；
3. 每套风格专属的 30fps 透明前景花字 `style_callouts`（胸口暗红立体字/发光暖金宋体/竖排诗句/满屏白大字/胶囊气泡/悬浮白卡/竖排大黄字）；
4. 差异化的顶栏与封面文案 `cover_copy`。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import style_visual_truth as svt


# ── 1) BIZ（破产负债 200 万商业口播）10/10 人类剪辑师精修语义字幕 ─────────────
# 严格 100% 忠于 03_audio_events.json 口播原话字符流（一字不增删），每条 4~9 字自然呼吸意群
BIZ_SEMANTIC_SUBTITLES: List[Dict[str, Any]] = [
    {"text": "公司破产那年", "keywords": ["破产"]},
    {"text": "我负债200多万", "keywords": ["200多万"]},
    {"text": "不是被骗", "keywords": ["被骗"]},
    {"text": "也不是行情差", "keywords": ["行情差"]},
    {"text": "是我从百度文库", "keywords": ["百度文库"]},
    {"text": "下载了一份", "keywords": []},
    {"text": "装修公司管理制度", "keywords": ["管理制度"]},
    {"text": "删掉公司名", "keywords": ["公司名"]},
    {"text": "改了几个数字", "keywords": ["几个数字"]},
    {"text": "打印出来", "keywords": []},
    {"text": "发给店里的人", "keywords": []},
    {"text": "那张纸贴上去之后", "keywords": ["贴上去"]},
    {"text": "没有人真拿他管过人", "keywords": ["管过人"]},
    {"text": "包括我自己", "keywords": ["我自己"]},
    {"text": "不是人的问题", "keywords": ["问题"]},
    {"text": "是我们想事情的方式", "keywords": ["方式"]},
    {"text": "被固化了", "keywords": ["固化"]},
    {"text": "小老板遇到事", "keywords": ["小老板"]},
    {"text": "第一反应", "keywords": ["第一反应"]},
    {"text": "是去别人那找答案", "keywords": ["找答案"]},
    {"text": "招人找能人", "keywords": ["找能人"]},
    {"text": "管人抄制度抄最快", "keywords": ["抄制度"]},
    {"text": "抄完还有种", "keywords": []},
    {"text": "我在做事的踏实感", "keywords": ["踏实感"]},
    {"text": "可他在别人那成立", "keywords": ["成立"]},
    {"text": "是因为长在", "keywords": []},
    {"text": "别人的前提上", "keywords": ["前提"]},
    {"text": "前提搬不过来", "keywords": ["搬不过来"]},
    {"text": "第一先答清楚", "keywords": ["先答清楚"]},
    {"text": "你自己的生意", "keywords": ["生意"]},
    {"text": "靠什么活下来", "keywords": ["活下来"]},
    {"text": "钱从哪一环挣出来", "keywords": ["挣出来"]},
    {"text": "第二看到做法", "keywords": ["做法"]},
    {"text": "先找他的前提", "keywords": ["前提"]},
    {"text": "海底捞的师徒制", "keywords": ["海底捞", "师徒制"]},
    {"text": "核心不是师徒两个字", "keywords": ["师徒"]},
    {"text": "是师傅能从徒弟的店", "keywords": ["徒弟的店"]},
    {"text": "利润里分到钱", "keywords": ["分到钱"]},
    {"text": "没这个前提", "keywords": ["前提"]},
    {"text": "他就是一句口号", "keywords": ["口号"]},
    {"text": "加一次拜师仪式", "keywords": ["拜师仪式"]},
    {"text": "第三过了夫妻店那条线", "keywords": ["夫妻店"]},
    {"text": "规矩只能自己长", "keywords": ["规矩"]},
    {"text": "两个人干", "keywords": ["两个人"]},
    {"text": "分工计酬监督", "keywords": ["分工计酬监督"]},
    {"text": "天然就有", "keywords": ["天然就有"]},
    {"text": "一雇人三样全碎了", "keywords": ["三样全碎"]},
    {"text": "我不给模板", "keywords": ["模板"]},
    {"text": "也不替你写", "keywords": []},
    {"text": "就陪你一条条答一遍", "keywords": ["一条条"]},
    {"text": "你店里哪条", "keywords": []},
    {"text": "答不上来私信我", "keywords": ["私信我"]},
]


# ── 2) STO（牛哥 300 万 600 平粤语点赞茶楼探店）10/10 人类剪辑师精修语义字幕 ─
# 展示层自动将 𠮶 渲染为高清标准粤字 嗰；原文本严丝合缝对齐 03_audio_events.json
STO_SEMANTIC_SUBTITLES: List[Dict[str, Any]] = [
    {"text": "我系牛哥", "keywords": ["牛哥"]},
    {"text": "我花咗三百万", "keywords": ["三百万"]},
    {"text": "喺金湾开咗一间", "keywords": ["金湾"]},
    {"text": "六百平嘅点赞茶楼", "keywords": ["点赞茶楼"]},
    {"text": "中间上边", "keywords": []},
    {"text": "摆好多卡座", "keywords": ["卡座"]},
    {"text": "一个中空", "keywords": ["中空"]},
    {"text": "跟住有啲灯笼", "keywords": ["灯笼"]},
    {"text": "有啲造型", "keywords": ["造型"]},
    {"text": "岭南元素嘅", "keywords": ["岭南元素"]},
    {"text": "佢饮茶嘅氛围", "keywords": ["氛围"]},
    {"text": "我觉得非常之舒服", "keywords": ["非常之舒服"]},
    {"text": "𠮶边就系我哋嘅厨房", "keywords": ["厨房"]},
    {"text": "两百平嘅厨房", "keywords": ["两百平"]},
    {"text": "所以好多人", "keywords": ["好多人"]},
    {"text": "都觉得我好傻", "keywords": ["好傻"]},
    {"text": "一间六百平嘅店", "keywords": ["六百平"]},
    {"text": "厨房你要花三分之一", "keywords": ["三分之一"]},
    {"text": "两百平去制造", "keywords": ["两百平"]},
    {"text": "我哋嘅四大天王", "keywords": ["四大天王"]},
    {"text": "虾饺排骨", "keywords": ["虾饺", "排骨"]},
    {"text": "凤爪同埋叉烧包", "keywords": ["叉烧包"]},
    {"text": "招牌特色炒面", "keywords": ["特色炒面"]},
    {"text": "绝对食过嘅话", "keywords": []},
    {"text": "都会觉得非常之好食", "keywords": ["非常之好食"]},
    {"text": "一早点赞茶楼", "keywords": ["点赞茶楼"]},
    {"text": "呢一个特色", "keywords": ["特色"]},
    {"text": "就系朝早", "keywords": ["朝早"]},
    {"text": "做纯手工嘅早茶", "keywords": ["纯手工", "早茶"]},
    {"text": "晚上就系做桑拿鸡", "keywords": ["桑拿鸡"]},
    {"text": "𠮶边就系我哋嘅鱼池", "keywords": ["鱼池"]},
    {"text": "到时就有好多", "keywords": []},
    {"text": "新鲜嘅海鲜", "keywords": ["海鲜"]},
    {"text": "喺𠮶度供应", "keywords": ["供应"]},
    {"text": "俾大家选择", "keywords": ["选择"]},
    {"text": "我哋桑拿鸡个鸡", "keywords": ["桑拿鸡"]},
    {"text": "就系从山上捉翻嚟嘅", "keywords": ["山上"]},
    {"text": "绝对品质OK嘅", "keywords": ["绝对品质"]},
    {"text": "所以食嘅就系鸡", "keywords": []},
    {"text": "𠮶种原汁原味", "keywords": ["原汁原味"]},
    {"text": "最后俾大家睇下", "keywords": []},
    {"text": "我哋依家", "keywords": []},
    {"text": "装修嘅进度如何", "keywords": ["装修"]},
    {"text": "唔知道大家", "keywords": []},
    {"text": "中意食乜嘢", "keywords": ["食乜嘢"]},
    {"text": "可以喺评论区讲下", "keywords": ["评论区"]},
]


# ── 3) BIZ 与 STO 的深度语义卡片物料库（供 24 套风格换肤消费，彻底告别空洞短语）─
BIZ_SEMANTIC_BLOCKS: List[Dict[str, Any]] = [
    {
        "target_sec": 7.5,
        "title": "照抄制度的坑",
        "punch": "抄制度坑",
        "subtitle": "真实血泪复盘",
        "quote": "从百度文库下载装修公司管理制度，删掉公司名改几个数字贴墙上，没人真用",
        "lines": ["百度文库直接下载制度", "删公司名改几个数字", "贴上墙没人真管过人"],
        "diagram_mode": "compare",
        "items": [
            {"label": "盲目抄模板", "points": ["百度文库直接下载", "删公司名改数字", "贴墙上没人管用"]},
            {"label": "先问底层前提", "points": ["生意靠什么活下来", "钱从哪一环挣出来", "规矩自己长出来"]},
        ],
        "outline_title": "抄制度必碎三步",
        "outline_items": [
            {"ord": "一、", "text": "下载大厂管理制度改名字"},
            {"ord": "二、", "text": "打印发店里贴墙当规矩"},
            {"ord": "三、", "text": "包括自己都没人真拿来用"},
        ],
        "citation": "—— 破产200万复盘",
    },
    {
        "target_sec": 24.0,
        "title": "思维固化陷阱",
        "punch": "思维固化",
        "subtitle": "小老板第一反应",
        "quote": "小老板遇到事第一反应去别人那找答案，招人找能人管人抄制度，抄完还有踏实感",
        "lines": ["遇到事就去别人那找答案", "招人找能人 · 管人抄制度", "抄完还有种做事的踏实感"],
        "diagram_mode": "compare",
        "items": [
            {"label": "虚假踏实感", "points": ["招人只想着找能人", "管人只想着抄制度", "抄最快但全浮表面"]},
            {"label": "真正的破局", "points": ["打破固化想事方式", "看清别人成立前提", "前提搬不来要自建"]},
        ],
        "outline_title": "思维固化三大表现",
        "outline_items": [
            {"ord": "一、", "text": "遇到问题就去别人那找答案"},
            {"ord": "二、", "text": "招人找能人、管人抄制度"},
            {"ord": "三、", "text": "忽略制度背后长出来的前提"},
        ],
        "citation": "—— 经营本质拆解",
    },
    {
        "target_sec": 41.5,
        "title": "先答清生意本质",
        "punch": "底层前提",
        "subtitle": "第一步核心自检",
        "quote": "第一先答清楚你自己的生意靠什么活下来，钱从哪一环挣出来",
        "lines": ["前提搬不过来", "先答清楚你自己的生意", "钱到底从哪一环挣出来"],
        "diagram_mode": "trio",
        "items": [
            {"ord": "01", "label": "生意靠什么活下来", "desc": "先看清自家生存底牌"},
            {"ord": "02", "label": "钱从哪一环挣出来", "desc": "锁死真正的利润引擎"},
            {"ord": "03", "label": "看到做法先找前提", "desc": "绝不照搬无根之木"},
        ],
        "outline_title": "自建规矩三步法",
        "outline_items": [
            {"ord": "一、", "text": "答清生意靠什么活下来"},
            {"ord": "二、", "text": "看到做法先找他的底层前提"},
            {"ord": "三、", "text": "过了夫妻店线规矩自己长"},
        ],
        "citation": "—— 实战自检清单",
    },
    {
        "target_sec": 52.5,
        "title": "海底捞师徒制真相",
        "punch": "利润分钱",
        "subtitle": "经典案例拆解",
        "quote": "海底捞的师徒制核心不是师徒两个字，是师傅能从徒弟的店利润里分到钱，没这个前提就是一句口号",
        "lines": ["核心不是师徒两个字", "是师傅能从徒弟的店", "利润里分到钱", "没这个前提就是一句口号"],
        "diagram_mode": "chain",
        "items": [
            {"ord": "01", "label": "师傅亲手带出徒弟开新店", "desc": "真教真带不藏私"},
            {"ord": "02", "label": "从徒弟店利润里直接分钱", "desc": "真金白银利益绑定"},
            {"ord": "03", "label": "没分钱前提＝口号＋拜师仪式", "desc": "只学皮毛必流于形式"},
        ],
        "outline_title": "海底捞师徒制底层",
        "outline_items": [
            {"ord": "一、", "text": "核心绝非师徒两个字"},
            {"ord": "二、", "text": "师傅能从徒弟店利润分钱"},
            {"ord": "三、", "text": "缺这前提就剩拜师仪式"},
        ],
        "citation": "—— 组织激励拆解",
    },
    {
        "target_sec": 66.5,
        "title": "夫妻店到雇人断层",
        "punch": "三样全碎",
        "subtitle": "第三步组织跃迁",
        "quote": "过了夫妻店那条线，两个人干分工计酬监督天然就有，一雇人三样全碎了",
        "lines": ["过了夫妻店那条线", "两个人干分工计酬监督天然就有", "一雇人三样全碎了"],
        "diagram_mode": "trio",
        "items": [
            {"ord": "01", "label": "分工 · 谁干什么清清楚楚", "desc": "雇人后权责边界立刻模糊"},
            {"ord": "02", "label": "计酬 · 干好怎么分到钱", "desc": "没机制只能靠老板盯人"},
            {"ord": "03", "label": "监督 · 不在场也照常运转", "desc": "三样重长才能走出夫妻店"},
        ],
        "outline_title": "雇人必重长三要素",
        "outline_items": [
            {"ord": "一、", "text": "分工：拆清每个岗位权责"},
            {"ord": "二、", "text": "计酬：让员工和利润挂钩"},
            {"ord": "三、", "text": "监督：告别纯靠口号管人"},
        ],
        "citation": "—— 实体老板必修课",
    },
]

STO_SEMANTIC_BLOCKS: List[Dict[str, Any]] = [
    {
        "target_sec": 3.0,
        "title": "金湾600平点赞茶楼",
        "punch": "花三百万",
        "subtitle": "牛哥实拍探店",
        "quote": "花咗三百万喺金湾开咗一间六百平嘅点赞茶楼，岭南元素饮茶氛围非常舒服",
        "lines": ["花咗三百万喺金湾开店", "六百平岭南元素茶楼", "中空灯笼氛围非常舒服"],
        "diagram_mode": "trio",
        "items": [
            {"ord": "01", "label": "300万重金打造600平大空间", "desc": "珠海金湾全新点赞茶楼"},
            {"ord": "02", "label": "挑高中空＋岭南红灯笼卡座", "desc": "叹茶聊天氛围非常舒服"},
            {"ord": "03", "label": "200平超大现做明档厨房", "desc": "占足全场三分之一面积"},
        ],
        "outline_title": "点赞茶楼三大亮点",
        "outline_items": [
            {"ord": "一、", "text": "三百万打造六百平岭南空间"},
            {"ord": "二、", "text": "三分之一面积做两百平厨房"},
            {"ord": "三、", "text": "早茶纯手工 · 晚市桑拿鸡"},
        ],
        "citation": "—— 牛哥探店实录",
    },
    {
        "target_sec": 18.5,
        "title": "三分之一做大厨房",
        "punch": "大厨房",
        "subtitle": "重投入保品质",
        "quote": "好多人都觉得我好傻，六百平嘅店厨房你要花三分之一两百平去制造四大天王",
        "lines": ["好多人都觉得我好傻", "六百平嘅店花三分之一", "两百平厨房纯手工制造"],
        "diagram_mode": "compare",
        "items": [
            {"label": "普通茶楼做法", "points": ["压缩厨房多摆台", "预制点心加热上桌", "出品千篇一律"]},
            {"label": "牛哥重金做法", "points": ["200平超大现做厨房", "虾饺排骨凤爪叉烧包", "坚持纯手工原汁原味"]},
        ],
        "outline_title": "重金造大厨房",
        "outline_items": [
            {"ord": "一、", "text": "六百平店拿出三分之一面积"},
            {"ord": "二、", "text": "现点现蒸广式四大天王"},
            {"ord": "三、", "text": "食过绝对觉得非常之好食"},
        ],
        "citation": "—— 广式早茶匠心",
    },
    {
        "target_sec": 34.5,
        "title": "早茶晚市双招牌",
        "punch": "双招牌",
        "subtitle": "全时段美味攻略",
        "quote": "点赞茶楼呢一个特色，就系朝早做纯手工嘅早茶，晚上就系做桑拿鸡加新鲜海鲜",
        "lines": ["朝早做纯手工广式早茶", "晚上主打鲜嫩桑拿鸡", "仲有大型鱼池现点海鲜"],
        "diagram_mode": "trio",
        "items": [
            {"ord": "早", "label": "朝早：纯手工广式早茶", "desc": "四大天王＋招牌特色炒面"},
            {"ord": "晚", "label": "晚上：山上走地桑拿鸡", "desc": "现蒸锁鲜 · 绝对品质OK"},
            {"ord": "鲜", "label": "活鲜：超大鱼池任君挑选", "desc": "新鲜海鲜现场供应"},
        ],
        "outline_title": "早晚双绝必点攻略",
        "outline_items": [
            {"ord": "一、", "text": "朝早叹纯手工广式四大天王"},
            {"ord": "二、", "text": "晚上食山上捉翻嚟桑拿鸡"},
            {"ord": "三、", "text": "鱼池现捞新鲜海鲜任拣"},
        ],
        "citation": "—— 珠海金湾必打卡",
    },
    {
        "target_sec": 48.0,
        "title": "山上走地原汁原味",
        "punch": "原汁原味",
        "subtitle": "食材品质承诺",
        "quote": "我哋桑拿鸡个鸡就系从山上捉翻嚟嘅，绝对品质OK，食嘅就系鸡嗰种原汁原味",
        "lines": ["桑拿鸡从山上捉翻嚟", "绝对品质OK", "食嘅就系鸡嗰种原汁原味"],
        "diagram_mode": "chain",
        "items": [
            {"ord": "01", "label": "山上散养走地靓鸡直供", "desc": "源头严选绝不凑合"},
            {"ord": "02", "label": "桑拿现蒸锁住鲜甜肉汁", "desc": "原汁原味不靠重料盖"},
            {"ord": "03", "label": "装修倒计时 · 评论区点菜", "desc": "留言你最中意食乜嘢"},
        ],
        "outline_title": "点赞茶楼品质承诺",
        "outline_items": [
            {"ord": "一、", "text": "山上走地鸡现蒸原汁原味"},
            {"ord": "二、", "text": "实拍装修进度公开透明"},
            {"ord": "三、", "text": "评论区讲下你最中意食乜"},
        ],
        "citation": "—— 欢迎评论区留言",
    },
]


def _style_cover_copy(style_id: str, track_tag: str) -> Dict[str, str]:
    """为 24 套风格按各自语调定制专属顶栏与封面主副标，绝不 24 套复制同一句。"""
    if track_tag == "BIZ":
        table = {
            "style_01": ("负债200万复盘", "抄制度全垮了"),
            "style_02": ("公司破产负债200万", "不是被骗是抄错制度"),
            "style_03": ("小老板别再瞎抄制度", "负债200万买来的教训"),
            "style_04": ("为什么一雇人就全碎", "抄来的制度根本管不住人"),
            "style_05": ("贴上墙的制度没人用", "小老板思维固化真相"),
            "style_06": ("让你少走三年弯路", "先找前提再谈制度落地"),
            "style_07": ("照抄管理制度三大坑", "别再花冤枉钱试错了"),
            "style_08": ("破解小店管人死局", "先答清钱从哪一环挣"),
            "style_09": ("破产200万深度复盘", "拆解海底捞师徒制真相"),
            "style_10": ("为什么照抄大厂全废", "真正的核心差异在前提"),
            "style_11": ("怎么才算规矩真落地", "过了夫妻店必须自己长"),
            "style_12": ("夫妻店和雇人管店", "核心差距到底在哪里"),
            "style_13": ("小老板一定要警惕", "盲目抄制度的虚假踏实"),
            "style_14": ("找能人还是抄制度", "其实前提根本搬不过来"),
            "style_15": ("制度落地核心三要素", "先找前提再建分工计酬"),
            "style_16": ("破产200万顿悟真相", "没有分钱前提全成口号"),
            "style_17": ("实体店雇人必看建议", "别从文库下载管理模板"),
            "style_18": ("揭秘管人最扎心真相", "一雇人分工计酬全碎了"),
            "style_19": ("深度拆解海底捞制度", "师傅能从徒弟店分到钱"),
            "style_20": ("管人靠抄不如靠长", "先明生意前提再定规矩"),
            "style_21": ("为什么你贴制度没用", "三样机制必须自己长出来"),
            "style_22": ("千万别盲目照搬大厂", "没有前提全是一句口号"),
            "style_23": ("经营里最贵的弯路", "把别人的做法当自己答案"),
            "style_24": ("实测照搬模板有多坑", "小老板自建规矩三步法"),
        }
    else:
        table = {
            "style_01": ("300万开600平茶楼", "200平拿来做厨房"),
            "style_02": ("花300万金湾开茶楼", "200平厨房纯手工早茶"),
            "style_03": ("牛哥300万金湾开店", "六百平岭南点赞茶楼实拍"),
            "style_04": ("六百平店200平厨房", "别人都说牛哥太傻了"),
            "style_05": ("重金打造岭南茶楼", "朝早叹早茶晚上桑拿鸡"),
            "style_06": ("坚持纯手工才够味", "金湾六百平点赞茶楼实录"),
            "style_07": ("斥资300万开广式茶楼", "凭什么三分之一建厨房"),
            "style_08": ("金湾新开岭南大茶楼", "纯手工早茶配山上桑拿鸡"),
            "style_09": ("300万茶楼装修实录", "揭秘200平厨房四大天王"),
            "style_10": ("六百平大店硬核配置", "三分之一面积只做出品"),
            "style_11": ("怎么才算地道广式茶楼", "岭南中空灯笼配手工点心"),
            "style_12": ("普通茶楼和点赞茶楼", "出品差距到底有多大"),
            "style_13": ("爱叹早茶一定要看", "200平明档手工四大天王"),
            "style_14": ("早茶还是晚市桑拿鸡", "金湾点赞茶楼全都要"),
            "style_15": ("点赞茶楼必打卡三绝", "手工早茶·桑拿鸡·活海鲜"),
            "style_16": ("牛哥砸300万的底气", "只做山上走地鸡原汁原味"),
            "style_17": ("珠海金湾叹茶新推荐", "六百平岭南氛围太舒服"),
            "style_18": ("别人笑我厨房建太大", "食过四大天王就知真功夫"),
            "style_19": ("实地直击点赞茶楼", "300万打造全时段岭南食府"),
            "style_20": ("匠心守住广式原味", "朝早纯手工晚市桑拿鸡"),
            "style_21": ("为什么敢用200平做厨房", "现做四大天王加活海鲜池"),
            "style_22": ("千万别错过金湾新地标", "600平岭南点赞茶楼抢先看"),
            "style_23": ("一盅两件的岭南烟火", "牛哥300万筑梦点赞茶楼"),
            "style_24": ("实拍金湾点赞茶楼", "300万装修进度全公开"),
        }
    l1, l2 = table.get(style_id, ("核心实战复盘", "看清底层真相"))
    return {"headline_top": l1, "highlight_badge": l2}


def _enrich_beat_layout(base_lay: Dict[str, Any], blk: Dict[str, Any], theme: Dict[str, Any]) -> Dict[str, Any]:
    """把语义物料库精准填进对应风格的面板布局里，确保零乱码、零空话、100% 同风格换肤。"""
    lay = copy.deepcopy(base_lay)
    mode = str(lay.get("mode") or "")
    lay["panel_title"] = blk["title"] if mode in ("paper_outline_pip", "concept_diagram_pip", "review_whiteboard_pip") else blk["punch"]
    lay["giant_back_text"] = blk["punch"]
    lay["panel_subtitle"] = blk["subtitle"]
    lay["quote_text"] = blk["quote"]
    lay["panel_lines"] = list(blk["lines"])
    lay["citation"] = blk.get("citation", "")

    if mode == "paper_outline_pip":
        lay["panel_title"] = blk["outline_title"]
        lay["panel_items"] = copy.deepcopy(blk["outline_items"])
    elif mode == "review_whiteboard_pip":
        lay["panel_title"] = blk["punch"]
        lay["panel_items"] = [{"text": x} for x in blk["lines"][:2]]
    elif mode == "concept_diagram_pip":
        lay["diagram_mode"] = blk["diagram_mode"]
        lay["panel_title"] = blk["title"]
        lay["panel_items"] = copy.deepcopy(blk["items"])
        lay["light_theme"] = bool(theme.get("light_theme", False))
        lay["accent_hex"] = str(theme.get("accent") or "#FFE14D")
        lay["accent2_hex"] = str(theme.get("accent2") or "#DC2626")
        lay["font_role"] = str(theme.get("font_role") or "sans")
    return lay


def build_director_plan_for_style(
    style_id: str,
    track_tag: str,
    base_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    为指定风格 + 赛道生成 V11.0 专属 `03b_director_plan.json`：
    - style_02 保持原有顶尖单层动态花字舞台（并注入 10/10 精修语义字幕）；
    - 其余 23 套全部按亲眼核验的原母本基因 + 语义物料库产出专属高占比 `style_scene_beats`、`style_callouts`、`cover_copy` 与 `semantic_subtitles`。
    """
    is_biz = (track_tag == "BIZ")
    plan = copy.deepcopy(base_plan) if isinstance(base_plan, dict) else {}
    plan["style_id"] = style_id
    plan["v11_director_version"] = "11.0"
    plan["cover_copy"] = _style_cover_copy(style_id, track_tag)
    plan["semantic_subtitles"] = copy.deepcopy(BIZ_SEMANTIC_SUBTITLES if is_biz else STO_SEMANTIC_SUBTITLES)

    if style_id == "style_02":
        plan["native_style_mode"] = False
        plan.pop("style_scene_beats", None)
        plan.pop("style_callouts", None)
        return plan

    plan["native_style_mode"] = True
    truth = svt.get_visual_truth(style_id)
    theme = truth.get("diagram_theme") or {"light_theme": False, "accent": "#FFE14D", "accent2": "#DC2626", "font_role": "sans"}
    blocks = BIZ_SEMANTIC_BLOCKS if is_biz else STO_SEMANTIC_BLOCKS
    raw_beats = list(truth.get("scene_beats") or [])

    style_beats: List[Dict[str, Any]] = []
    if raw_beats:
        for i, rb in enumerate(raw_beats):
            blk = blocks[i % len(blocks)]
            base_lay = dict(rb.get("layout") or {})
            # 商业口播 BIZ：在论证拆解高峰（第 2、4 段）对适合图解的面板无缝升级为同风格换肤动态概念图解卡（彻底终结单机死盯光头！）
            # 同时保护 style_09 纪实双区、style_15 纸底大纲、style_16 连续打字机、style_24 杂志白板等自身标志性招牌不被冲淡
            if is_biz and base_lay.get("variant") == "s17_dual_compare_cards":
                # 纯单机商业口播无异源副镜时，用同风格双列概念对比图解卡替代双人头堆叠，避免下框人像贴近字幕带
                base_lay = {"mode": "concept_diagram_pip", "with_anchor_circle": True}
            if (
                is_biz
                and i == 1
                and style_id not in ("style_09", "style_15", "style_16", "style_24", "style_20", "style_23")
                and base_lay.get("mode") not in ("paper_outline_pip", "review_whiteboard_pip", "dark_typewriter_card_pip")
            ):
                base_lay = {
                    "mode": "concept_diagram_pip",
                    "with_anchor_circle": True,
                }
            enriched = _enrich_beat_layout(base_lay, blk, theme)
            _high_density = style_id in ("style_09", "style_15", "style_16", "style_24", "style_19", "style_22")
            _tdur = 6.8 if (_high_density and is_biz) else (5.8 if is_biz else (5.2 if _high_density else 4.2))
            style_beats.append({
                "target_sec": blk["target_sec"] if i < len(blocks) else None,
                "frac": rb.get("frac", 0.2 + 0.16 * i),
                "max_dur": float(rb.get("max_dur") or 4.6),
                "target_dur": _tdur,
                "layout": enriched,
            })
        # 商业口播 BIZ：如果风格本身有 4 段，额外在第 5 个方法论段落（66.5s 三要素）追加 1 张同色系动态概念拆解卡（保证全片 40%~60% 视觉密度）
        if is_biz and len(style_beats) < 5 and style_id not in ("style_01", "style_06", "style_08", "style_12"):
            blk_end = blocks[4]
            extra_lay = _enrich_beat_layout({"mode": "concept_diagram_pip", "with_anchor_circle": True}, blk_end, theme)
            style_beats.append({
                "target_sec": blk_end["target_sec"],
                "frac": 0.84,
                "max_dur": 5.0,
                "target_dur": 6.0,
                "layout": extra_lay,
            })
    elif is_biz and style_id in ("style_01", "style_06", "style_08"):
        # 轻面板顶栏/极简派在商业口播纯单机赛道下：适度安排 2 张与本风格 100% 同色系的动态结构图解卡，既保顶栏清爽气质又告别 77 秒死盯光头
        for blk_idx in (1, 3):
            blk = blocks[blk_idx]
            extra_lay = _enrich_beat_layout({"mode": "concept_diagram_pip", "with_anchor_circle": True}, blk, theme)
            style_beats.append({
                "target_sec": blk["target_sec"],
                "frac": 0.34 if blk_idx == 1 else 0.68,
                "max_dur": 4.8,
                "target_dur": 5.8,
                "layout": extra_lay,
            })

    plan["style_scene_beats"] = style_beats

    # ── 专属 30fps 透明 HTML 动态前景花字（精准复刻原母本标志性动效，避开面板时段）──
    callouts: List[Dict[str, Any]] = []
    if style_id == "style_05":
        callouts = [
            {"text": "负债200多万" if is_biz else "花咗三百万", "start_sec": 1.2, "end_sec": 3.8, "variant": "s05_crimson_3d", "callout_y": 930, "font_role": "serif"},
            {"text": "前提搬不过来" if is_biz else "两百平厨房", "start_sec": 37.5 if is_biz else 20.5, "end_sec": 40.5 if is_biz else 23.2, "variant": "s08_giant_white", "callout_y": 930, "font_role": "sans"},
        ]
    elif style_id == "style_06":
        callouts = [
            {"text": "先答清生意前提" if is_biz else "纯手工广式早茶", "start_sec": 38.0 if is_biz else 35.0, "end_sec": 41.2 if is_biz else 38.2, "variant": "s06_warm_gold", "callout_y": 960, "font_role": "serif_bold"},
            {"text": "规矩只能自己长" if is_biz else "一盅两件守原味", "start_sec": 70.0 if is_biz else 51.5, "end_sec": 75.0 if is_biz else 56.2, "variant": "s06_vertical_poem", "callout_y": 360, "font_role": "serif_bold"},
        ]
    elif style_id == "style_08":
        callouts = [
            {"text": "不是人的问题" if is_biz else "三百万六百平", "start_sec": 18.8 if is_biz else 1.4, "end_sec": 21.6 if is_biz else 4.2, "variant": "s08_giant_white", "callout_y": 880, "font_role": "sans"},
            {"text": "一雇人三样全碎" if is_biz else "两百平造四大天王", "start_sec": 67.8 if is_biz else 21.5, "end_sec": 70.8 if is_biz else 24.5, "variant": "s08_giant_white", "callout_y": 880, "font_role": "sans"},
        ]
    elif style_id == "style_12":
        callouts = [
            {"text": "招人找能人" if is_biz else "朝早纯手工早茶", "items": ["招人找能人", "管人抄制度"] if is_biz else ["纯手工早茶", "晚上桑拿鸡"], "start_sec": 26.8 if is_biz else 35.0, "end_sec": 30.2 if is_biz else 38.5, "variant": "s12_pill_bubbles", "callout_y": 900},
            {"text": "分工计酬监督" if is_biz else "鱼池活海鲜", "items": ["分工计酬", "监督全碎"] if is_biz else ["山上走地鸡", "原汁原味"], "start_sec": 65.0 if is_biz else 46.5, "end_sec": 68.5 if is_biz else 50.0, "variant": "s12_pill_bubbles", "callout_y": 900},
        ]
    elif style_id == "style_15":
        callouts = [
            {"text": "抄制度" if is_biz else "点赞茶楼", "start_sec": 1.2, "end_sec": 3.8, "variant": "s15_glow_white", "callout_y": 950, "font_role": "sans"},
            {"text": "分工计酬监督" if is_biz else "原汁原味", "start_sec": 65.0 if is_biz else 49.5, "end_sec": 68.2 if is_biz else 52.5, "variant": "s15_glow_white", "callout_y": 950, "font_role": "sans"},
        ]
    elif style_id == "style_20":
        callouts = [
            {"text": "不是被骗是抄错" if is_biz else "三百万开点赞茶楼", "start_sec": 1.4, "end_sec": 4.4, "variant": "s20_crimson_plate", "callout_y": 1020, "font_role": "serif"},
            {"text": "规矩只能自己长" if is_biz else "食嘅就系原汁原味", "start_sec": 62.2 if is_biz else 49.5, "end_sec": 65.2 if is_biz else 52.5, "variant": "s20_crimson_plate", "callout_y": 1020, "font_role": "serif"},
        ]
    elif style_id == "style_21":
        callouts = [
            {"text": "思维被固化了" if is_biz else "三分之一做厨房", "start_sec": 20.2 if is_biz else 20.0, "end_sec": 23.0 if is_biz else 22.8, "variant": "s21_lime_arrow", "callout_y": 240, "font_role": "sans"},
            {"text": "一雇人全碎了" if is_biz else "纯手工四大天王", "start_sec": 67.8 if is_biz else 23.2, "end_sec": 70.6 if is_biz else 26.0, "variant": "s21_lime_arrow", "callout_y": 240, "font_role": "sans"},
        ]
    elif style_id == "style_22":
        callouts = [
            {"text": "三要素" if is_biz else "三大招牌", "items": ["分工", "计酬", "监督"] if is_biz else ["手工早茶", "桑拿鸡", "活海鲜"], "start_sec": 64.5 if is_biz else 35.5, "end_sec": 68.5 if is_biz else 39.2, "variant": "s22_floating_tags", "callout_y": 780},
        ]
    elif style_id == "style_24":
        callouts = [
            {"text": "全碎了" if is_biz else "好抵食", "start_sec": 67.5 if is_biz else 49.5, "end_sec": 70.8 if is_biz else 52.6, "variant": "s24_vertical_yellow", "callout_y": 310, "font_role": "oblique"},
        ]

    plan["style_callouts"] = callouts
    return plan
