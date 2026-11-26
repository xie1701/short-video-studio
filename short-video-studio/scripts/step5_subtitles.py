#!/usr/bin/env python3
"""
[Step 5: 字幕生成与统一权威布局]
1. 读取锁定音轨 03_audio_events.json 的逐字时间戳，结合 terms.txt 保护词与词边界动态规划（DP）切分清爽单行短句。
2. 修复数字旁中文标点残留（仅保住夹在数字间的小数点 . 与时间冒号 : 以及数字后 %），尊重模板 punct_stripped 配置。
3. 基于唯一权威 SUBTITLE_SAFE_ZONE 与 01_manifest.json 裁切后精确人脸框一次性完成像素测宽与避脸位移，
   写出权威 final_bbox（同步写入 bbox），供 render / jianying / hyperframes / qa 四端同源消费。
4. 双通道防重复：code_broll 在场覆盖 >=60% 时标记 suppressed=True 并跳过上屏（仅普通无画中画图解）。
输出：pipeline/05_subtitles.json、05_subtitles.ass、05_subtitles.srt 与 05_subtitle_check.txt
"""

from __future__ import annotations

import argparse
import difflib
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    BT709_FFMPEG_ARGS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SUBTITLE_CENTER_X,
    SUBTITLE_DEFAULT_Y_CENTER,
    SUBTITLE_SAFE_ZONE,
    SUBTITLE_SYMMETRIC_MAX_W,
    bbox_iou,
    check_bbox_in_safe_zone,
    find_bin,
    find_commercial_font,
    find_source_font,
    get_cached_font,
    load_subtitle_templates,
    maybe_reexec_in_venv,
    parse_manifest_faces,
    read_json,
    write_json,
)
import element_rules
import glyph_fallback as glyph_gate

# 仅保留夹在数字间的小数点/冒号(如 3.5 / 18:30)以及数字后的%，彻底清掉紧贴数字的中文逗号句号！
_NON_CORE_PUNCT_RE = re.compile(r"(?<!\d)[.:：](?!\d)|(?<!\d)[.:：](?=\d)|(?<=\d)[.:：](?!\d)|[，。、；！？,!?;“”‘’\"'（）()]+")

_DP_NO_LEAD_CHARS = set("的了呢吗吧啊着过么嘛上里中下后前嘅咗喺啲哋咧啰")
_DP_NO_TAIL_CHARS = set("的嘅把被从跟比在对向给让和与及是将用为喺")
_DP_UNIT_AFTER_NUM = set("倍度年多万亿%％岁个次米元块斤克天月时分秒件家店条步项位层种平张台间人只杯碗盘之")
_DP_HARD_SPLIT_RE = re.compile(r"([。！？!?]+)")
_DP_SOFT_PUNCT = set("，、；：,;: ")
_COMMON_TWO_CHAR_WORDS = {
    "因为", "所以", "但是", "其实", "如果", "而且", "虽然", "不过", "然后", "或者",
    "为什么", "怎么样", "多少钱", "不会", "不能", "不是", "没有", "已经", "可以", "需要",
    "自己", "大家", "我们", "你们", "他们", "这个", "那个", "这样", "那样", "这里", "那里",
    "放大", "缩小", "面积", "真实", "成本", "利润", "价格", "时间", "方法", "步骤", "结果",
    "对比", "发现", "问题", "答案", "原因", "标准", "细节", "效果", "数据", "视频", "画面",
    "字幕", "镜头", "声音", "节奏", "烧烤", "羊排", "门店", "顾客", "老板", "新人", "完播",
    # 高频连接/副词：不断开会读得更顺（此前「最后还|是删掉了」这类会被切开）
    "还是", "就是", "只是", "于是", "要是", "或是", "总是", "老是", "一定",
    "可能", "应该", "现在", "最后", "关键", "基本", "差不多", "下来", "起来", "出来",
    # 粤语/口播高频粘连词（禁止断成「我|哋」「好|傻」「氛|围」「早|茶」）
    "我哋", "佢哋", "你哋", "就系", "唔系", "唔知", "唔好", "唔同", "唔够", "唔错",
    "依家", "点解", "点样", "跟住", "呢度", "𠮶度", "𠮶边", "呢边", "上边", "下边", "中间",
    "非常之", "饮茶", "早茶", "午市", "晚市", "茶楼", "厨房", "氛围", "好傻", "好食",
    "好多", "好似", "新鲜", "海鲜", "鱼池", "灯笼", "造型", "岭南", "元素", "三分之一",
    "四大天王", "纯手工", "装修", "进度", "评论区", "乜嘢", "同埋", "食过", "绝对", "品质",
    "原汁原味", "卡座", "中空", "制造业", "制造", "觉得", "可以", "中意", "评论", "供应",
    "牛哥", "金湾", "餐饮", "特色", "炒面", "招牌", "桑拿鸡", "桑拿", "叉烧包", "排骨",
    "凤爪", "虾饺", "点赞", "环境", "位置", "大概", "其实", "平时", "夜晚", "晚上",
    "早上", "朝早", "手工", "本地", "传统", "广式",
    # V7 新增：商战口播/粤语高频专名、动补结构与易腰斩固定搭配
    "百度文库", "管理制度", "装修公司", "海底捞", "师徒制", "小老板", "公司名",
    "第一反应", "哪一环", "两个人", "其他人", "别人", "私信我", "还有种", "踏实感",
    "发给", "打印出来", "下载了", "搬不过来", "答不上来", "分到钱", "挣出来",
    "一雇人", "三样", "三样全碎", "分工计酬", "计酬监督", "分工计酬监督", "去别人",
    "喺𠮶度", "俾大家", "我哋嘅", "食过嘅话", "新鲜嘅海鲜",
    "好多新鲜嘅", "一间六百平", "六百平嘅店", "一间六百平嘅店", "嘅店", "唔会", "好抵食",
    "两百多万", "两百万", "红灯笼", "广式点心", "古风灯笼", "手工制作", "长在", "前提上", "答清楚",
}
_SPOKEN_CANTONESE_COHESIVE = {
    "我哋", "佢哋", "你哋", "就系", "唔系", "唔知", "唔好", "唔同", "依家", "点解", "跟住",
    "非常之", "饮茶", "早茶", "茶楼", "厨房", "氛围", "好傻", "好食", "好多", "好似",
    "纯手工", "三分之一", "四大天王", "原汁原味", "评论区", "牛哥", "桑拿鸡", "叉烧包",
    "喺𠮶度", "俾大家", "我哋嘅", "食过嘅话", "好抵食", "一间六百平", "六百平嘅店", "一间六百平嘅店", "嘅店", "新鲜嘅海鲜",
}
_COHESIVE_PROTECT_PHRASES = (
    "百度文库", "管理制度", "装修公司", "海底捞", "师徒制", "小老板", "公司名",
    "第一反应", "哪一环", "两个人", "其他人", "别人", "去别人", "私信我", "还有种", "踏实感",
    "发给", "打印出来", "下载了", "搬不过来", "答不上来", "分到钱", "挣出来",
    "一雇人", "三样全碎", "分工计酬", "计酬监督", "分工计酬监督",
    "喺𠮶度", "俾大家", "我哋嘅", "食过嘅话", "好抵食", "新鲜嘅海鲜",
    "一间六百平", "六百平嘅店", "一间六百平嘅店", "两百多万", "两百万", "红灯笼", "广式点心", "手工制作",
)
_CLAUSE_START_PROTECT_RE = re.compile(
    r"^(?:第[一二三四五六七八九十两\d]+|首先|其次|再次|最后|总结|"
    r"不是|但是|可是|不过|然而|其实|所以|因此|因为|如果|只要|一旦|或者|另外|还有|我好傻|真系好)"
)
_STOP_KEYWORDS = {
    "一个", "一些", "呢个", "𠮶个", "这种", "那个", "这个", "什么", "所以",
    "因为", "觉得", "如果", "但是", "现在", "时候", "跟住", "大家",
}


def _terms_file_for(pipeline_dir: Path) -> Optional[Path]:
    """术语表可能在三处：pipeline/terms.txt、素材侧 `_SVS工作区/术语表.txt`、项目根。"""
    cands = [
        Path(pipeline_dir) / "terms.txt",
        Path(pipeline_dir).parent / "terms.txt",
        Path(pipeline_dir).parent / "_SVS工作区" / "术语表.txt",
        Path(pipeline_dir).parent / "术语表.txt",
    ]
    inv_p = Path(pipeline_dir) / "00_inventory.json"
    if inv_p.is_file():
        try:
            inv = read_json(inv_p)
            for key in ("raw_dir", "source_dir", "input_dir"):
                v = str(inv.get(key) or "").strip()
                if v:
                    cands.append(Path(v) / "_SVS工作区" / "术语表.txt")
                    cands.append(Path(v) / "术语表.txt")
        except Exception:
            pass
    for c in cands:
        if c.is_file():
            return c
    return None


def load_protected_terms(terms_path: Optional[Path] = None, extra_terms: Optional[List[str]] = None) -> List[str]:
    terms: List[str] = []
    if terms_path and Path(terms_path).is_file():
        for ln in Path(terms_path).read_text(encoding="utf-8").splitlines():
            t = ln.strip()
            if not t or t.startswith("#"):
                continue
            # 支持「错词 => 正词」硬映射格式：提取右侧正词进保护词典（不再把整行带符号放进去）
            if "=>" in t:
                t = t.split("=>", 1)[1].strip()
            if len(t) >= 2 and t not in _STOP_KEYWORDS and t not in terms:
                terms.append(t)
    for t in (extra_terms or []):
        ts = str(t or "").strip()
        if "=>" in ts:
            ts = ts.split("=>", 1)[1].strip()
        if len(ts) >= 2 and ts not in _STOP_KEYWORDS and ts not in terms:
            terms.append(ts)
    terms.sort(key=len, reverse=True)
    return terms


_NUM_ONLY_RE = re.compile(r"^(?:[0-9]+(?:[.:][0-9]+)?|[零〇一二三四五六七八九十百千万亿两廿]+)$")
_UNIT_ONLY_RE = re.compile(r"^(?:[%％倍度年万亿岁个次米元块斤克天月时分秒件家店条步项位层种平张台间人只杯碗盘]|平方|平米|公分)$")


def _merge_numeral_units(toks: List[str]) -> List[str]:
    """把「六百」+「平」、「三」+「百万」、「廿」+「五」这类相邻碎片拼回一整块。

    jieba 会把中文数字和量词切开，于是字幕在「六|百平」这种地方断行 —— 一眼就是机器切的。
    """
    out: List[str] = []
    for tk in toks:
        if out and _NUM_ONLY_RE.match(out[-1]):
            if _UNIT_ONLY_RE.match(tk) or (_NUM_ONLY_RE.match(tk) and len(out[-1]) <= 3):
                out[-1] = out[-1] + tk
                continue
        out.append(tk)
    return out


def _merge_cohesive_phrases(toks: List[str], prot_set: set[str]) -> List[str]:
    """合并被分词器拆开的专名、动补搭子与粤语粘连块，避免「百度|文库」「发|给」「喺𠮶|度」。"""
    if len(toks) < 2:
        return toks
    cohesive = set(_COHESIVE_PROTECT_PHRASES) | _SPOKEN_CANTONESE_COHESIVE | prot_set
    out = list(toks)
    changed = True
    while changed and len(out) >= 2:
        changed = False
        nxt: List[str] = []
        i = 0
        while i < len(out):
            merged_hit = None
            for span in (3, 2):
                if i + span <= len(out):
                    cand = "".join(out[i : i + span])
                    if cand in cohesive:
                        merged_hit = (cand, span)
                        break
            if merged_hit:
                nxt.append(merged_hit[0])
                i += merged_hit[1]
                changed = True
            else:
                nxt.append(out[i])
                i += 1
        out = nxt
    return out


def tokenize_semantic_words(sentence: str, terms: Optional[List[str]] = None) -> List[str]:
    """
    词边界切分器：优先调用 jieba（启用 HMM 并注入保护词），否则使用零依赖中文语义边界与保护词/数字量词合并分词器。
    严格保护 terms 中的专有名词、成语与“数字+单位”不被切碎。
    """
    s = str(sentence or "")
    if not s:
        return []
    prot = sorted([t for t in (terms or []) if t], key=len, reverse=True)
    prot_set = set(prot)
    raw_toks: List[str] = []

    try:
        import jieba  # type: ignore
        jieba.setLogLevel(60)
        for tm in sorted(prot_set | _COMMON_TWO_CHAR_WORDS | set(_COHESIVE_PROTECT_PHRASES), key=len, reverse=True):
            jieba.add_word(tm, freq=2_000_000)
        raw_toks = _merge_numeral_units([t for t in jieba.cut(s, HMM=True) if t])
    except Exception:
        # 零依赖高质量分词兜底：先整块匹配保护词/数字+单位/英文词，再按虚词与常见词切分
        i, n = 0, len(s)
        # 中文数字 + 量词也要当一整块：「六百平」被切成「六|百平」是外行错
        num_unit_re = re.compile(
            r"(?:[0-9]+(?:[.:][0-9]+)?|[零〇一二三四五六七八九十百千万亿两廿]+)"
            r"(?:[%％倍度年多万亿岁个次米元块斤克天月时分秒件家店条步项位层种平张台间位人只杯碗盘位]*\s*)"
        )
        ascii_re = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")
        while i < n:
            matched_prot = None
            for tm in prot:
                if s.startswith(tm, i):
                    matched_prot = tm
                    break
            if matched_prot:
                raw_toks.append(matched_prot)
                i += len(matched_prot)
                continue
            m_num = num_unit_re.match(s, i)
            if m_num:
                raw_toks.append(m_num.group(0))
                i = m_num.end()
                continue
            m_asc = ascii_re.match(s, i)
            if m_asc:
                raw_toks.append(m_asc.group(0))
                i = m_asc.end()
                continue
            ch = s[i]
            if ch in _DP_SOFT_PUNCT or ch in "。！？!?":
                raw_toks.append(ch)
                i += 1
                continue
            if i + 3 <= n and s[i : i + 3] in _COMMON_TWO_CHAR_WORDS:
                raw_toks.append(s[i : i + 3])
                i += 3
                continue
            if i + 2 <= n and s[i : i + 2] in _COMMON_TWO_CHAR_WORDS:
                raw_toks.append(s[i : i + 2])
                i += 2
                continue
            if ch in _DP_NO_LEAD_CHARS or ch in _DP_NO_TAIL_CHARS:
                raw_toks.append(ch)
                i += 1
                continue
            # 其余普通汉字尽量组成 2 字自然语义节拍，遇到标点/数字/虚词提前停
            if i + 1 < n and ("\u4e00" <= s[i + 1] <= "\u9fff") and s[i + 1] not in _DP_NO_LEAD_CHARS and s[i + 1] not in _DP_NO_TAIL_CHARS:
                if not any(s.startswith(tm, i + 1) for tm in prot):
                    raw_toks.append(s[i : i + 2])
                    i += 2
                    continue
            raw_toks.append(ch)
            i += 1

    # 后处理：强制合并跨 token 的“数字 + 单位/小数”
    merged: List[str] = []
    for tok in raw_toks:
        if (
            merged
            and re.search(r"[0-9]$", merged[-1])
            and tok
            and (tok[0] in _DP_UNIT_AFTER_NUM or tok[0] in "0123456789.:")
        ):
            merged[-1] += tok
        else:
            merged.append(tok)
    return _merge_cohesive_phrases(merged, prot_set)


def split_semantic_two_lines(
    text: str,
    keywords: Optional[Sequence[str]] = None,
    terms: Optional[Sequence[str]] = None,
    max_first: Optional[int] = None,
) -> Tuple[str, str]:
    """按语义词边界平衡切分为双行，绝不在专有名词、保护词、数字量词或粤语粘连词中间腰斩。"""
    t = str(text or "").strip()
    if not t:
        return "", ""
    if "\n" in t:
        parts = [p.strip() for p in t.splitlines() if p.strip()]
        return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0] if parts else "", "")
    for sep in ("，", "。", "！", "？", "：", " ", "/", "|", "·"):
        if sep in t:
            parts = [p.strip() for p in t.split(sep, 1) if p.strip()]
            if len(parts) == 2:
                return (parts[0][:max_first] if max_first else parts[0]), parts[1]
    if len(t) < 5 or (max_first is not None and len(t) <= max_first and len(t) < 6):
        return t, ""

    kw_list = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    prot = sorted(
        set([str(x).strip() for x in (terms or []) if len(str(x).strip()) >= 2])
        | set(k for k in kw_list if len(k) >= 2)
        | _SPOKEN_CANTONESE_COHESIVE,
        key=len,
        reverse=True,
    )
    toks = tokenize_semantic_words(t, terms=prot)
    bounds: List[int] = [0]
    for tk in toks:
        bounds.append(bounds[-1] + len(tk))

    prot_spans: List[Tuple[int, int]] = []
    for tm in prot:
        for m in re.finditer(re.escape(tm), t):
            prot_spans.append((m.start(), m.end()))
    for m in re.finditer(
        r"(?:[零〇一二两三四五六七八九十百千万亿廿]+分之[零〇一二两三四五六七八九十百千万亿]+|"
        r"[0-9]+(?:[.:][0-9]+)?[%％倍度年多万亿岁个次米元块斤克天月时分秒件家店条步项位层种平张台间人只杯碗盘]*|"
        r"[零一二两三四五六七八九十百千万亿几多]+(?:[点余多来])?[零一二两三四五六七八九十百千万亿]*[个张间块元平米斤克秒分天年月人位套条次倍成折%]?)",
        t,
    ):
        if m.end() - m.start() >= 2:
            prot_spans.append((m.start(), m.end()))

    cands = [b for b in bounds if 2 <= b <= len(t) - 2 and not any(s < b < e for s, e in prot_spans)]
    if not cands:
        cands = [b for b in bounds if 1 <= b <= len(t) - 1 and not any(s < b < e for s, e in prot_spans)]
    if not cands:
        cut_fallback = min(max_first or len(t), (len(t) + 1) // 2)
        return t[:cut_fallback], t[cut_fallback:]

    kw_starts = set()
    for kw in kw_list:
        idx_k = t.find(kw)
        if 2 <= idx_k <= len(t) - 2:
            kw_starts.add(idx_k)

    mid = len(t) / 2.0
    best_pos = cands[0]
    best_cost = float("inf")
    for pos in cands:
        cost = abs(pos - mid) * 1.4
        if max_first is not None and pos > max_first:
            cost += (pos - max_first) * 25.0
        if pos in kw_starts:
            cost -= 6.0
        left_last = t[pos - 1]
        right_first = t[pos]
        if left_last in _DP_NO_TAIL_CHARS:
            cost += 8.0
        if right_first in _DP_NO_LEAD_CHARS:
            cost += 12.0
        if cost < best_cost:
            best_cost = cost
            best_pos = pos
    return t[:best_pos], t[best_pos:]


def dp_segment_short_lines(
    sentence: str,
    max_width_px: int,
    font_path: Optional[str] = None,
    font_size: int = 62,
    terms: Optional[List[str]] = None,
    max_chars: int = 12,
    strip_punct: bool = True,
    word_gaps: Optional[Dict[int, float]] = None,
) -> List[Tuple[int, int, str]]:
    """
    词边界 + 声学气口动态规划（DP）最优短句切分：
    返回 [(start_char_idx, end_char_idx, display_text), ...]。
    """
    raw = str(sentence or "")
    if not raw.strip():
        return []
    prot = [t for t in (terms or []) if len(t) >= 2]
    toks = tokenize_semantic_words(raw, terms=prot)
    bounds = [0]
    for tk in toks:
        bounds.append(bounds[-1] + len(tk))
    nb = len(bounds)
    if nb <= 1:
        return []

    def _disp(a: int, b: int) -> str:
        sub = raw[a:b].strip()
        if strip_punct:
            sub = re.sub(r"[，、；：,;:]+", " ", sub)
            sub = strip_display_punctuation(sub)
            sub = re.sub(r"\s+", " ", sub).strip()
        return sub

    # 预计算保护词区间，并自动保护粤语粘连高频词、商业高危短语、分数词与“中文连续数字+量词”
    prot_spans: List[Tuple[int, int]] = []
    for tm in set(prot) | _SPOKEN_CANTONESE_COHESIVE | set(_COHESIVE_PROTECT_PHRASES):
        if len(tm) < 2:
            continue
        for m in re.finditer(re.escape(tm), raw):
            prot_spans.append((m.start(), m.end()))
    for m in re.finditer(
        r"(?:[零〇一二两三四五六七八九十百千万亿廿]+分之[零〇一二两三四五六七八九十百千万亿]+|"
        r"[零一二两三四五六七八九十百千万亿几多]+(?:[点余多来])?[零一二两三四五六七八九十百千万亿]*[个张间块元平米斤克秒分天年月人位套条次倍成折%]?)",
        raw,
    ):
        if m.end() - m.start() >= 2:
            prot_spans.append((m.start(), m.end()))

    gaps_map = word_gaps or {}
    if gaps_map:
        bound_set = set(bounds)
        for gpos, gsec in gaps_map.items():
            if gsec >= 0.12 and 0 < gpos < len(raw) and not any(ps < gpos < pe for ps, pe in prot_spans):
                bound_set.add(gpos)
        if len(bound_set) > len(bounds):
            bounds = sorted(bound_set)
            nb = len(bounds)

    def _break_cost(pos: int) -> float:
        if pos <= 0 or pos >= len(raw):
            return 0.0
        in_prot = any(ps < pos < pe for ps, pe in prot_spans)
        left = raw[:pos]
        right = raw[pos:]
        # 句末标点与软标点一样是天然断点；但若被护词包含仍重罚
        left_punct = bool(left and (left[-1] in _DP_SOFT_PUNCT or left[-1] in "。！？!?"))
        if left_punct and not in_prot:
            return 0.0
        cost = 4.0
        r_trim = right.lstrip("，、；：,;: 。！？!?")
        l_trim = left.rstrip("，、；：,;: 。！？!?")
        r0 = r_trim[:1]
        l_last = l_trim[-1:]
        if r0 in _DP_NO_LEAD_CHARS:
            cost += 30.0
        if l_last in _DP_NO_TAIL_CHARS:
            cost += 15.0
        if r0.isdigit():
            cost += 25.0
        if in_prot:
            cost += 1000.0
        elif gaps_map:
            gap_v = gaps_map.get(pos)
            if gap_v is None and left and left[-1] in "，、；：,;:":
                gap_v = gaps_map.get(pos - 1)
            if gap_v is not None:
                if gap_v >= 0.22:
                    cost -= 16.0
                elif gap_v >= 0.10:
                    cost -= 8.0
                elif gap_v < 0.04 and not left_punct:
                    cost += 10.0
        return cost

    inf = float("inf")
    best = [inf] * nb
    prev = [-1] * nb
    best[0] = 0.0
    max_len_hard = max(6, int(max_chars) + 1)

    for j in range(1, nb):
        for i in range(j):
            if best[i] == inf:
                continue
            txt = _disp(bounds[i], bounds[j])
            if not txt:
                if best[i] < best[j]:
                    best[j] = best[i]
                    prev[j] = i
                continue
            compact_len = len(txt.replace(" ", ""))
            pw, _ = _measure_text_px(txt, font_path, font_size)
            if pw > max_width_px or compact_len > max_len_hard:
                continue
            # 若本段内部跨越 >=0.16s 的明显声学气口且气口两侧均 >=4 字，加罚以促成在气口自然换行
            mid_gap_pen = 0.0
            if gaps_map and compact_len >= 8:
                for gpos, gsec in gaps_map.items():
                    if bounds[i] + 3 <= gpos <= bounds[j] - 3 and gsec >= 0.16:
                        if not any(ps < gpos < pe for ps, pe in prot_spans):
                            mid_gap_pen += 14.0
            short_pen = max(0, 5 - compact_len) * 2.5 if j < nb - 1 or i > 0 else 0.0
            if compact_len <= 3 and (i > 0 or j < nb - 1):
                short_pen += 12.0
            cand = best[i] + _break_cost(bounds[j]) + short_pen + mid_gap_pen + 1.0
            # 一屏之内不允许跨句：span 中段（非末尾）夹着句末标点 = 先把两句话粘成一行，重罚
            mid = raw[bounds[i]:bounds[j]]
            if _DP_HARD_SPLIT_RE.search(mid.rstrip("。！？!? ")):
                cand += 40.0
            if cand < best[j]:
                best[j] = cand
                prev[j] = i

    if best[nb - 1] == inf:
        # 极端长保护词或超窄画布兜底：按原样返回整段
        return [(0, len(raw), _disp(0, len(raw)) or raw.strip())]

    out: List[Tuple[int, int, str]] = []
    cur = nb - 1
    while cur > 0:
        p = prev[cur]
        disp_t = _disp(bounds[p], bounds[cur])
        if disp_t:
            out.append((bounds[p], bounds[cur], disp_t))
        cur = p
    out.reverse()
    return out


def align_corrected_text_to_words(
    corrected_text: str,
    asr_words: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], float]:
    """用 difflib.SequenceMatcher 将人工校对后的纯文本字符级对齐到逐字时间戳，保留数字间小数点/冒号，未匹配字平滑插值。"""
    norm_corr = strip_display_punctuation(re.sub(r"\s+", "", str(corrected_text or "")))
    clean_chars = [ch for ch in norm_corr if ch.strip()]
    asr_flat: List[Tuple[str, float, float]] = []
    for w in asr_words:
        wt = strip_display_punctuation(re.sub(r"\s+", "", str(w.get("word") or "")))
        if not wt:
            continue
        ws = float(w.get("start_sec", 0.0))
        we = float(w.get("end_sec", ws + 0.08))
        n = len(wt)
        for k, ch in enumerate(wt):
            asr_flat.append((ch, ws + (we - ws) * k / n, ws + (we - ws) * (k + 1) / n))

    if not clean_chars or not asr_flat:
        return [], 0.0
    sm = difflib.SequenceMatcher(None, clean_chars, [c for c, _, _ in asr_flat], autojunk=False)
    times: List[Optional[Tuple[float, float]]] = [None] * len(clean_chars)
    matched = 0
    for blk in sm.get_matching_blocks():
        matched += blk.size
        for k in range(blk.size):
            _, s, e = asr_flat[blk.b + k]
            times[blk.a + k] = (s, e)

    first_asr_t = float(asr_flat[0][1])
    n = len(times)
    i = 0
    while i < n:
        if times[i] is None:
            j = i
            while j < n and times[j] is None:
                j += 1
            gap_n = max(1, j - i)
            if i == 0 and j < n and times[j] is not None:
                # 句首未匹配字：严格落在第一个已匹配字 times[j][0] 之前，彻底避免时间轴倒挂重叠
                t1 = float(times[j][0])
                t0 = max(0.0, min(first_asr_t, t1 - 0.12 * gap_n))
                if t1 <= t0 + 0.01:
                    t0 = max(0.0, t1 - 0.04 * gap_n)
                span = max(0.015, (t1 - t0) / gap_n)
                for k in range(i, j):
                    s_k = max(0.0, t1 - span * (j - k))
                    e_k = max(s_k + 0.01, t1 - span * (j - k - 1))
                    times[k] = (s_k, e_k)
            else:
                t0 = float(times[i - 1][1]) if i > 0 and times[i - 1] else first_asr_t
                t1 = float(times[j][0]) if j < n and times[j] else (t0 + 0.16 * gap_n)
                span = max(0.02 * gap_n, t1 - t0) / gap_n
                for k in range(i, j):
                    times[k] = (t0 + span * (k - i), t0 + span * (k - i + 1))
            i = j
        else:
            i += 1

    aligned = [
        {"word": ch, "start_sec": round(float(t[0]), 4), "end_sec": round(float(t[1]), 4)}
        for ch, t in zip(clean_chars, times)
        if t is not None
    ]
    return aligned, round(matched / max(1, len(clean_chars)), 4)


def fmt_srt_time(sec: float) -> str:
    ms = int(round(max(0.0, float(sec)) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_r = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_r:03d}"


def strip_display_punctuation(text: str) -> str:
    raw = str(text or "")
    # 先将夹在数字间的全角冒号归一化为半角冒号
    raw = re.sub(r"(?<=\d)：(?=\d)", ":", raw)
    return _NON_CORE_PUNCT_RE.sub("", raw)


_FILLER_WORDS = {"诶", "欸", "呃", "嗯", "啊", "呀", "哇", "唉", "哦", "噢", "喔", "咧"}


def _drop_filler_tokens(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """剔掉纯语气词与紧邻重复词：字幕要跟嘴上说的一致，但「诶」「啊」这种不该占一屏字。"""
    out: List[Dict[str, Any]] = []
    for w in words:
        t = str(w.get("word") or "").strip()
        if not t:
            continue
        if t in _FILLER_WORDS:
            continue
        if out and str(out[-1].get("word") or "") == t:
            # 紧邻重复（结巴 / NG 残留）只留第一次的时长，不重复占字
            out[-1]["end_sec"] = max(float(out[-1].get("end_sec", 0.0)), float(w.get("end_sec", 0.0)))
            continue
        out.append(w)
    return out


def _clean_word_tokens(raw_words: List[Dict[str, Any]], strip_punct: bool) -> List[Dict[str, Any]]:
    """逐词清洗标点，并保护跨 token 拆分的数字小数点/时间冒号(如 '18:' + '30')不被误杀。"""
    if not strip_punct:
        out: List[Dict[str, Any]] = []
        for w in raw_words:
            t = str(w.get("word", "")).strip()
            if t:
                cw = dict(w)
                cw["word"] = t
                out.append(cw)
        return _drop_filler_tokens(out)

    out = []
    n = len(raw_words)
    for idx, w in enumerate(raw_words):
        orig = str(w.get("word", ""))
        prev_txt = str(raw_words[idx - 1].get("word", "")) if idx > 0 else ""
        next_txt = str(raw_words[idx + 1].get("word", "")) if idx + 1 < n else ""
        # 如果当前词以 .:： 结尾且下一词以数字开头，或以 .:： 开头且上一词以数字结尾，暂时用占位符保护
        lead_protected = bool(orig and orig[0] in ".:：" and prev_txt and prev_txt[-1].isdigit())
        trail_protected = bool(orig and orig[-1] in ".:：" and next_txt and next_txt[0].isdigit())
        tmp = orig
        if lead_protected:
            tmp = "9" + tmp
        if trail_protected:
            tmp = tmp + "9"
        cleaned = strip_display_punctuation(tmp)
        if lead_protected and cleaned.startswith("9"):
            cleaned = cleaned[1:]
        if trail_protected and cleaned.endswith("9"):
            cleaned = cleaned[:-1]
        cleaned = cleaned.strip()
        # 句末标点会在这一步被剥掉。句界信息一旦丢了，后面的呼吸组与 DP 就再也分不清
        # 「为什么这么贵。其实…」和「为什么这么贵其实…」，会把两句话粘进同一行。
        # 所以在这里先打一个只在内部流转、落盘前会摘掉的标记。
        sent_end = bool(re.search(r"[。！？!?]", orig))
        if not cleaned:
            if sent_end and out:
                out[-1]["_sent_end"] = True
            continue
        cw = dict(w)
        cw["word"] = cleaned
        if sent_end:
            cw["_sent_end"] = True
        out.append(cw)
    return _drop_filler_tokens(out)


def fmt_ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass_text(txt: str) -> str:
    return str(txt or "").replace("\r\n", r"\N").replace("\n", r"\N").replace("{", r"\{").replace("}", r"\}")


def _measure_text_px(text: str, font_path: Optional[str], font_size: int) -> Tuple[int, int]:
    fnt = get_cached_font(font_path, font_size)
    if fnt is not None:
        try:
            bb = fnt.getbbox(text)
            return int(max(40, (bb[2] - bb[0]) + max(8, len(text) * 3))), int(max(font_size + 16, (bb[3] - bb[1]) + 28))
        except Exception:
            pass
    return int(len(text) * font_size * 1.04), int(font_size * 1.35 + 24)


# 避脸微移：只允许在 SUBTITLE_SAFE_ZONE 内上下挪，且上限 ±60px（用户实测 2026-11-24：
# 再大的位移会把字幕甩到胸口/腰部，观感比轻微压脸更差）。优先级：原地不动 > 近处不重叠 > IoU 最小。
FACE_AVOID_MAX_SHIFT_PX = 60
FACE_AVOID_STEP_PX = 15
FACE_AVOID_IOU_LIMIT = 0.01


def _compute_authoritative_subtitle_bbox(
    text: str,
    tpl: Dict[str, Any],
    font_path: Optional[str],
    face_boxes_px: List[Dict[str, Any]],
    keywords: Optional[Sequence[str]] = None,
    terms: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, int], int]:
    """按画布绝对正中 x=540 对称安全带 [220, 860] 计算尺寸并仅在垂直方向避开人脸五官，产出四端统一 final_bbox。

    返回 (final_bbox, face_avoid_dy)。水平中心永远锁在 540px（位移只走垂直方向，居中不会被破坏），
    face_avoid_dy 会写回 05_subtitles.json，供渲染/工程/质检三端对账。
    """
    base_fs = int(tpl.get("font_size", 62))
    kw_scale = float(tpl.get("keyword_scale", 1.133))
    dual_banner = bool(tpl.get("dual_line_banner"))
    safe_w_cap = SUBTITLE_SYMMETRIC_MAX_W  # 640px ([220, 860]，中心严格锁定 540px)
    center_x = SUBTITLE_CENTER_X  # 540px

    clean_len = len(str(text or "").replace("\n", "").strip())
    if dual_banner and ("\n" in str(text or "") or clean_len >= 5):
        raw_t = str(text or "").strip()
        if "\n" not in raw_t:
            l1, l2 = split_semantic_two_lines(raw_t, keywords=keywords, terms=terms)
        else:
            parts = raw_t.splitlines()
            l1, l2 = parts[0], (parts[1] if len(parts) > 1 else "")
        eff_fs = min(84, max(62, int(round(base_fs * min(1.15, max(1.0, (1.0 + kw_scale) * 0.5))))))
        w1, h1 = _measure_text_px(l1, font_path, eff_fs)
        w2, h2 = _measure_text_px(l2 or l1, font_path, eff_fs)
        est_w = max(w1, w2)
        est_h_2 = max(h1, h2) * 2 + 12
        box_w = min(safe_w_cap, max(180, est_w + 56))
        box_h = min(284, max(168, est_h_2 + 24))
    else:
        eff_fs = int(round(base_fs * min(1.20, max(1.0, (1.0 + kw_scale) * 0.5))))
        if dual_banner:
            eff_fs = min(100, eff_fs)
        est_w, est_h = _measure_text_px(text, font_path, eff_fs)
        box_w = min(safe_w_cap, max(160, est_w + 32))
        box_h = min(220, max(84, est_h + 16))
    # 水平中心 100% 锁定画布正中央 540px，且 box_w <= 640 保证永不触发右侧 860px 单侧推偏
    box_x = center_x - box_w // 2

    # 钳位 safe_y_center，默认对齐黄金水位 SUBTITLE_DEFAULT_Y_CENTER (1310px)
    safe_yc = int(tpl.get("safe_y_center", SUBTITLE_DEFAULT_Y_CENTER))
    box_y = max(SUBTITLE_SAFE_ZONE["y_min"], min(SUBTITLE_SAFE_ZONE["y_max"] - box_h, safe_yc - box_h // 2))
    cand = {"x": int(box_x), "y": int(box_y), "w": int(box_w), "h": int(box_h)}

    face_avoid_dy = 0
    if face_boxes_px:
        cur_iou = max(bbox_iou(cand, fb) for fb in face_boxes_px)
        if cur_iou > FACE_AVOID_IOU_LIMIT:
            y_lo = SUBTITLE_SAFE_ZONE["y_min"]
            y_hi = max(y_lo, SUBTITLE_SAFE_ZONE["y_max"] - box_h)
            offsets: List[int] = [0]
            d = FACE_AVOID_STEP_PX
            while d <= FACE_AVOID_MAX_SHIFT_PX:
                offsets.extend([d, -d])
                d += FACE_AVOID_STEP_PX
            best_cand, best_iou, best_dy = cand, cur_iou, 0
            for dy in offsets:
                ty = max(y_lo, min(y_hi, box_y + dy))
                alt = dict(cand, y=int(ty))
                iou_v = max(bbox_iou(alt, fb) for fb in face_boxes_px)
                if iou_v <= FACE_AVOID_IOU_LIMIT:
                    best_cand, best_iou, best_dy = alt, iou_v, int(ty) - int(box_y)
                    break
                if iou_v < best_iou:
                    best_cand, best_iou, best_dy = alt, iou_v, int(ty) - int(box_y)
            cand = best_cand
            face_avoid_dy = int(best_dy)
    return cand, face_avoid_dy


def _norm_tokens(s: Any) -> str:
    return strip_display_punctuation(re.sub(r"\s+", "", str(s or "")))


def align_words_to_sentence_text(text: str, asr_words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把**整理后**的句子文字重新对回逐字时间戳。

    为什么会不一致：ASR 给的是原话（`然后唔知道大家` / `诶唔知道大家中意食` / 句尾多个 `听`），
    上屏前 Agent 整理过（去口癖、补标点、合半句），字数和字面都变了。
    旧实现直接把 ASR 词表拼起来当字幕，于是屏幕上印的还是那堆口癖 —— 整理白整理了。
    """
    norm = _norm_tokens(text)
    if not norm or not asr_words:
        return []
    # align_corrected_text_to_words 返回的是逐字 dict 列表（word/start_sec/end_sec）
    char_times, _cov = align_corrected_text_to_words(text, asr_words)
    if len(char_times) != len(norm):
        return []
    out: List[Dict[str, Any]] = []
    i = 0
    for tok in tokenize_semantic_words(text):
        n = len(_norm_tokens(tok))
        if n <= 0:
            continue
        span = char_times[i:i + n]
        if len(span) < n:
            return []
        out.append({"word": tok,
                    "start_sec": float(span[0]["start_sec"]),
                    "end_sec": float(span[-1]["end_sec"])})
        i += n
    return out if i == len(norm) else []


# ── 字幕上屏文字的两处收口（2026-11-24 用户实测：字号放大后冒出「四大轩王啦」这种行，
#    以及只剩 2~3 个字的残句）────────────────────────────────────────────────
MIN_CHUNK_CHARS = 4      # 少于这个字数的字幕块不单独占一屏，并进相邻块
# 与成稿文字的相似下限：只改「听错一两个字」的情况（四大轩王→四大天王），改写过的整句不动。
# 配合「窗口单调向前」（cursor）使用：每块只在上一块之后的成稿文字里找，避免两块文字重叠。
CONFIRM_MATCH_RATIO = 0.75


def _retarget_words_to_text(words: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """把逐词表里的**字形**按成稿订正，词数与时间轴一概不动。

    2026-11-24：ASR 把「四大天王」听成「四大轩王」，字幕就印错。成稿是用户核过的，
    整句做一次字符对齐，凡是「一对一替换」的位置就换成成稿的字（同音误听、专名听错都在这里修好）；
    增删块（口癖、重复词）保持原样，因此不会改动块边界，也不会把时间戳拉偏。
    """
    base = str(text or "")
    if not words or not base:
        return words
    joined = "".join(str(w.get("word") or "") for w in words)
    if not joined or joined == base:
        return words
    sm = difflib.SequenceMatcher(None, joined, base, autojunk=False)
    chars = list(joined)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                chars[i1 + k] = base[j1 + k]
    fixed = "".join(chars)
    if fixed == joined:
        return words
    out: List[Dict[str, Any]] = []
    pos = 0
    for w in words:
        wt = str(w.get("word") or "")
        n = len(wt)
        nw = dict(w)
        nw["word"] = fixed[pos : pos + n] or wt
        pos += n
        out.append(nw)
    return out

def _clean_display_keywords(raw_kws: Sequence[Any], sentence_text: str) -> List[str]:
    """清理句级伪关键词（「一个」等通用数量代词）并将截断前缀数词（「六百」/「三分」）还原为完整实词（「六百平」/「三分之一」）。"""
    txt = strip_display_punctuation(re.sub(r"\s+", "", str(sentence_text or "")))
    out: List[str] = []
    for k in (raw_kws or []):
        ks = re.sub(r"\s+", "", str(k or ""))
        if not ks or ks in _STOP_KEYWORDS or len(ks) < 2:
            continue
        if ks in txt:
            # 仅针对「纯数字或分数截断」（六百→六百平，两百→两百平，三分→三分之一）向上扩全词，避免误伤「三百万」等完整词
            m_frac = re.search(re.escape(ks) + r"(?:分之[一二两三四五六七八九十]+(?![百千万亿])|之[一二两三四五六七八九十]+(?![百千万亿])|[平平米个间张条只杯碗盘倍年天块元斤克]?)", txt)
            if m_frac and re.fullmatch(r"[零〇一二两三四五六七八九十百千万亿0-9]+分?", ks):
                cand = m_frac.group(0)
                if len(cand) > len(ks):
                    ks = cand
        if len(ks) >= 2 and ks not in _STOP_KEYWORDS and ks in txt and ks not in out:
            out.append(ks)
    return out


def _paint_keyword_flags(words: List[Dict[str, Any]], keywords: Sequence[str]) -> List[Dict[str, Any]]:
    """先重置再按完整关键词短语重染 word['is_keyword']，彻底消除残词/虚词连带高亮。"""
    if not words:
        return words
    new_ws = [dict(w) for w in words]
    for w_item in new_ws:
        w_item["is_keyword"] = False
    if not keywords:
        return new_ws
    buf_tokens: List[Tuple[int, int, int]] = []
    cur_pos = 0
    joined_w: List[str] = []
    for wi, w_item in enumerate(new_ws):
        wt = strip_display_punctuation(re.sub(r"\s+", "", str(w_item.get("word") or "")))
        if not wt:
            continue
        joined_w.append(wt)
        buf_tokens.append((cur_pos, cur_pos + len(wt), wi))
        cur_pos += len(wt)
    joined_str = "".join(joined_w)
    for pk in keywords:
        pk_clean = strip_display_punctuation(re.sub(r"\s+", "", str(pk or "")))
        if not pk_clean:
            continue
        search_i = 0
        while True:
            hit_at = joined_str.find(pk_clean, search_i)
            if hit_at < 0:
                break
            hit_end = hit_at + len(pk_clean)
            for cs, ce, wi in buf_tokens:
                if cs < hit_end and ce > hit_at:
                    new_ws[wi]["is_keyword"] = True
            search_i = hit_end
    return new_ws


def _repair_words_from_script(words: List[Dict[str, Any]], script_text: str) -> List[Dict[str, Any]]:
    """当成稿文字与口播逐字高度吻合时，通过字符级 difflib 对齐补回 ASR 漏字（「氛→氛围」「四大王→四大天王」）。"""
    if not words or not str(script_text or "").strip():
        return words
    asr_plain = "".join(_norm_tokens(w.get("word")) for w in words)
    scr_plain = _norm_tokens(script_text)
    if not asr_plain or not scr_plain:
        return words
    if asr_plain == scr_plain:
        return words
    ratio = difflib.SequenceMatcher(None, asr_plain, scr_plain, autojunk=False).ratio()
    if ratio < 0.78 or abs(len(scr_plain) - len(asr_plain)) > max(3, int(len(asr_plain) * 0.20)):
        return _retarget_words_to_text(words, scr_plain)
    aligned, _ = align_corrected_text_to_words(scr_plain, words)
    if len(aligned) != len(scr_plain):
        return _retarget_words_to_text(words, scr_plain)
    # 沿旧词表继承 is_keyword / _sent_end 元信息
    old_kw_flags = []
    for w in words:
        nt = _norm_tokens(w.get("word"))
        for _ in nt:
            old_kw_flags.append(bool(w.get("is_keyword", False)))
    sm = difflib.SequenceMatcher(None, asr_plain, scr_plain, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "replace"):
            for k in range(min(i2 - i1, j2 - j1)):
                if i1 + k < len(old_kw_flags) and old_kw_flags[i1 + k]:
                    aligned[j1 + k]["is_keyword"] = True
    # 严格锁回原参考词首尾时间包络 [ref_t0, ref_t1]，防止句首/句尾补回漏字后外扩 ±0.16s 触发字幕交叉同步偏差 FAIL
    ref_t0 = float(words[0]["start_sec"])
    ref_t1 = float(words[-1]["end_sec"])
    if aligned and float(aligned[0]["start_sec"]) < ref_t0 - 1e-4:
        first_e = float(words[0]["end_sec"])
        span_end = max(ref_t0 + 0.02, first_e)
        head_k = 0
        while head_k + 1 < len(aligned) and float(aligned[head_k + 1]["start_sec"]) < ref_t0 - 1e-4:
            head_k += 1
        head_cnt = head_k + 1
        if float(aligned[head_k]["end_sec"]) > span_end:
            span_end = max(span_end, float(aligned[head_k]["end_sec"]))
        step_h = (span_end - ref_t0) / float(max(1, head_cnt))
        for idx_h in range(head_cnt):
            aligned[idx_h]["start_sec"] = round(ref_t0 + idx_h * step_h, 4)
            aligned[idx_h]["end_sec"] = round(ref_t0 + (idx_h + 1) * step_h, 4)
    if aligned and float(aligned[-1]["end_sec"]) > ref_t1 + 1e-4:
        tail_anchor = min(float(words[-1]["start_sec"]), max(ref_t0, ref_t1 - 0.04))
        tail_start_idx = len(aligned) - 1
        while tail_start_idx > 0 and float(aligned[tail_start_idx - 1]["end_sec"]) > ref_t1 + 1e-4:
            tail_start_idx -= 1
        while tail_start_idx > 0 and float(aligned[tail_start_idx]["start_sec"]) >= tail_anchor - 1e-4:
            if float(aligned[tail_start_idx - 1]["start_sec"]) >= tail_anchor - 1e-4:
                tail_start_idx -= 1
            else:
                break
        tail_anchor = min(tail_anchor, float(aligned[tail_start_idx]["start_sec"]))
        if tail_start_idx > 0:
            tail_anchor = max(tail_anchor, float(aligned[tail_start_idx - 1]["end_sec"]))
        tail_cnt = len(aligned) - tail_start_idx
        step_t = max(0.01, (ref_t1 - tail_anchor) / float(max(1, tail_cnt)))
        for idx_o in range(tail_cnt):
            ci = tail_start_idx + idx_o
            aligned[ci]["start_sec"] = round(tail_anchor + idx_o * step_t, 4)
            aligned[ci]["end_sec"] = round(ref_t1 if idx_o == tail_cnt - 1 else (tail_anchor + (idx_o + 1) * step_t), 4)
    # 如果任一原词带 _sent_end，保留在最末字符
    if words[-1].get("_sent_end") and aligned:
        aligned[-1]["_sent_end"] = True
    return aligned


def _restore_script_punctuation(sent_text: str, script_lines: Sequence[str]) -> str:
    """将 02c_script_final.json 中的柔和标点（，、；：。！？）安全回贴到口播段落文字中，供 DP 在自然停顿点 zero-cost 断开。"""
    s_raw = str(sent_text or "").strip()
    if not s_raw or not script_lines or any(ch in s_raw for ch in "，、；：,;:"):
        return s_raw
    s_plain = _norm_tokens(s_raw)
    if len(s_plain) < 6:
        return s_raw
    for ln in script_lines:
        ln_str = str(ln or "").strip()
        if not ln_str:
            continue
        ln_plain = _norm_tokens(ln_str)
        if not ln_plain:
            continue
        # 建立 ln_plain 下标 -> 其后紧随的原始标点 映射
        punct_after: Dict[int, str] = {}
        pi = -1
        for ch in ln_str:
            if ch in "，、；：,;:" or ch in "。！？!?":
                if pi >= 0:
                    punct_after[pi] = punct_after.get(pi, "") + ch
            elif _norm_tokens(ch):
                pi += len(_norm_tokens(ch))
        if not punct_after:
            continue
        sm = difflib.SequenceMatcher(None, s_plain, ln_plain, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size >= 4]
        if not blocks or sum(b.size for b in blocks) < min(6, int(len(s_plain) * 0.45)):
            continue
        insert_after: Dict[int, str] = {}
        for b in blocks:
            for k in range(b.size):
                lp = b.b + k
                sp = b.a + k
                if lp in punct_after and sp < len(s_plain) - 1:
                    insert_after[sp] = punct_after[lp][0]
        if insert_after:
            out_chars: List[str] = []
            for idx_c, ch in enumerate(s_plain):
                out_chars.append(ch)
                if idx_c in insert_after:
                    out_chars.append(insert_after[idx_c])
            return "".join(out_chars)
    return s_raw


def _merge_short_chunks(
    chunks: List[Dict[str, Any]],
    min_chars: int = MIN_CHUNK_CHARS,
    max_px: Optional[int] = None,
    font_path: Optional[str] = None,
    font_size: int = 62,
    protected_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """把 2~3 个字的残句并进相邻块（**并完必须仍在对称安全带内**）。

    为什么：字号修到真值（75px）后一行只放得下 8 个字，句尾常被切出一个「仲有」「去制造」，
    单占一屏既难看也被平台安全区体检判成「残句」。

    2026-11-24 实测补充：这里以前只管字数不管宽度，「三分之一」+「两百平去制造」被并成
    10 字 640px 的满宽行，渲染器只能靠溢出兑底把整行缩字，字号和居中都被带偏。
    现在合并前先量宽，超过对称安全带（max_px）就不并。
    """
    def _too_wide(txt: str) -> bool:
        if not max_px:
            return False
        w, _ = _measure_text_px(str(txt or ""), font_path, font_size)
        return w > int(max_px)

    out: List[Dict[str, Any]] = []
    for ch in chunks:
        txt = str(ch.get("text") or "").replace(" ", "")
        if out and len(txt) < min_chars:
            prev = out[-1]
            gap = float(ch["start_sec"]) - float(prev["end_sec"])
            merged_txt = str(prev.get("text") or "") + str(ch.get("text") or "")
            if gap <= 0.50 and not _too_wide(merged_txt):
                prev["text"] = merged_txt
                prev["end_sec"] = max(float(prev["end_sec"]), float(ch["end_sec"]))
                prev["words"] = list(prev.get("words") or []) + list(ch.get("words") or [])
                prev["keywords"] = list(dict.fromkeys(list(prev.get("keywords") or []) + list(ch.get("keywords") or [])))
                continue
        out.append(dict(ch))
    # 并不到一起的残块：按**完整语义词元**从邻块借词，绝不按单字劈开双字词或保护词（「好|傻」「两百|平」「桑拿|鸡」）
    if len(out) >= 2:
        out = _rebalance_short_chunks(out, min_chars, max_px, font_path, font_size, protected_terms=protected_terms)
    # 头一块太短（第一块就只剩两个字）并到后一块
    if len(out) >= 2 and len(str(out[0].get("text") or "")) < min_chars:
        first, second = out[0], out[1]
        merged_txt = str(first.get("text") or "") + str(second.get("text") or "")
        if float(second["start_sec"]) - float(first["end_sec"]) <= 0.50 and not _too_wide(merged_txt):
            second["text"] = merged_txt
            second["start_sec"] = min(float(first["start_sec"]), float(second["start_sec"]))
            second["words"] = list(first.get("words") or []) + list(second.get("words") or [])
            second["keywords"] = list(dict.fromkeys(list(first.get("keywords") or []) + list(second.get("keywords") or [])))
            out.pop(0)
    # 句内扫尾兜底：若仍留有 <=3 字残块（如句内呼吸间隔 >0.50s 跳过上面分支），优先并入更短的相邻块（放宽到句内 gap<=1.20s，绝不留单挂碎字）
    changed = True
    while changed and len(out) >= 2:
        changed = False
        for k in range(len(out)):
            cur_t = str(out[k].get("text") or "").replace(" ", "")
            if len(cur_t) >= min_chars:
                continue
            cand_neighbors: List[Tuple[int, int]] = []
            if k - 1 >= 0:
                gap_prev = float(out[k]["start_sec"]) - float(out[k - 1]["end_sec"])
                if gap_prev <= 1.20:
                    cand_neighbors.append((len(str(out[k - 1].get("text") or "").replace(" ", "")), k - 1))
            if k + 1 < len(out):
                gap_next = float(out[k + 1]["start_sec"]) - float(out[k]["end_sec"])
                if gap_next <= 1.20:
                    cand_neighbors.append((len(str(out[k + 1].get("text") or "").replace(" ", "")), k + 1))
            for _, nb_i in sorted(cand_neighbors):
                lo, hi = (nb_i, k) if nb_i < k else (k, nb_i)
                m_txt = str(out[lo].get("text") or "") + str(out[hi].get("text") or "")
                if len(m_txt.replace(" ", "")) <= 12 and not _too_wide(m_txt):
                    out[lo]["text"] = m_txt
                    out[lo]["end_sec"] = max(float(out[lo]["end_sec"]), float(out[hi]["end_sec"]))
                    out[lo]["words"] = list(out[lo].get("words") or []) + list(out[hi].get("words") or [])
                    out[lo]["keywords"] = list(dict.fromkeys(list(out[lo].get("keywords") or []) + list(out[hi].get("keywords") or [])))
                    out.pop(hi)
                    changed = True
                    break
            if changed:
                break
    return out


def _rebalance_short_chunks(
    chunks: List[Dict[str, Any]],
    min_chars: int,
    max_px: Optional[int],
    font_path: Optional[str],
    font_size: int,
    protected_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """不足 min_chars 的残句：按**完整语义词/保护词**向邻块借词，严禁按单字从词中间劈半。"""
    def _chars(txt: str) -> int:
        return len(str(txt or "").replace(" ", ""))

    def _too_wide(txt: str) -> bool:
        if not max_px:
            return False
        w, _ = _measure_text_px(str(txt or ""), font_path, font_size)
        return w > int(max_px)

    eff_terms = list(dict.fromkeys(list(protected_terms or []) + list(_SPOKEN_CANTONESE_COHESIVE)))

    def _word_spans(nb_words: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
        """把 nb_words 按语义分词划分成不可再拆的 token 索引区间 [(s_i, e_i), ...]。"""
        joined = "".join(str(w.get("word") or "") for w in nb_words)
        if not joined or len(joined) != sum(len(str(w.get("word") or "")) for w in nb_words):
            return [(i, i + 1) for i in range(len(nb_words))]
        char_to_wi: List[int] = []
        for wi, w in enumerate(nb_words):
            for _ in str(w.get("word") or ""):
                char_to_wi.append(wi)
        toks = tokenize_semantic_words(joined, terms=eff_terms)
        spans: List[Tuple[int, int]] = []
        pos = 0
        for tk in toks:
            n_c = len(tk)
            if n_c <= 0 or pos >= len(char_to_wi):
                continue
            w0 = char_to_wi[pos]
            w1 = char_to_wi[min(len(char_to_wi) - 1, pos + n_c - 1)] + 1
            if spans and w0 < spans[-1][1]:
                w0 = spans[-1][1]
            if w1 > w0:
                spans.append((w0, w1))
            pos += n_c
        if not spans:
            return [(i, i + 1) for i in range(len(nb_words))]
        return spans

    for i in range(len(chunks)):
        cur = chunks[i]
        while _chars(cur.get("text")) < min_chars:
            moved = False
            for j, take_from_head in ((i - 1, False), (i + 1, True)):
                if not (0 <= j < len(chunks)):
                    continue
                nb = chunks[j]
                nb_words = list(nb.get("words") or [])
                cur_words = list(cur.get("words") or [])
                if not nb_words or not cur_words:
                    continue
                w_spans = _word_spans(nb_words)
                if len(w_spans) <= 1:
                    continue
                s_idx, e_idx = w_spans[0] if take_from_head else w_spans[-1]
                donor_slice = nb_words[s_idx:e_idx]
                cand_nb_words = nb_words[e_idx:] if take_from_head else nb_words[:s_idx]
                cand_cur_words = cur_words + donor_slice if take_from_head else donor_slice + cur_words
                cand_nb_text = "".join(str(w.get("word") or "") for w in cand_nb_words)
                cand_cur_text = "".join(str(w.get("word") or "") for w in cand_cur_words)
                if _chars(cand_nb_text) < min_chars:
                    continue
                if _too_wide(cand_nb_text) or _too_wide(cand_cur_text):
                    continue
                nb["words"] = cand_nb_words
                nb["text"] = cand_nb_text
                cur["words"] = cand_cur_words
                cur["text"] = cand_cur_text
                if take_from_head:
                    nb["start_sec"] = float(cand_nb_words[0]["start_sec"])
                    cur["end_sec"] = float(donor_slice[-1]["end_sec"])
                else:
                    nb["end_sec"] = float(cand_nb_words[-1]["end_sec"])
                    cur["start_sec"] = float(donor_slice[0]["start_sec"])
                moved = True
                break
            if not moved:
                # 借整词会导致邻块变碎或超宽时：若「当前残块+相邻整块」合起来仍在行宽安全带内且字数 <= 9，直接合并成一句
                for j, on_left in ((i - 1, True), (i + 1, False)):
                    if not (0 <= j < len(chunks)):
                        continue
                    nb = chunks[j]
                    nb_words = list(nb.get("words") or [])
                    cur_words = list(cur.get("words") or [])
                    if not nb_words or not cur_words:
                        continue
                    merged_words = (nb_words + cur_words) if on_left else (cur_words + nb_words)
                    merged_txt = "".join(str(w.get("word") or "") for w in merged_words)
                    if _chars(merged_txt) <= 9 and not _too_wide(merged_txt):
                        nb["words"] = merged_words
                        nb["text"] = merged_txt
                        nb["start_sec"] = float(merged_words[0]["start_sec"])
                        nb["end_sec"] = float(merged_words[-1]["end_sec"])
                        cur["words"] = []
                        cur["text"] = ""
                        moved = True
                        break
                break
    return [c for c in chunks if str(c.get("text") or "").strip()]


def _confirm_chunk_text(chunk: Dict[str, Any], sent_text: str, cursor: int = 0) -> Tuple[Dict[str, Any], int]:
    """把 ASR 听错的字换成用户核过的成稿用字（只换字，不动时间轴）。

    例：ASR 把「四大天王」听成「四大轩王」，词表上屏就会印错。成稿是用户核过的，
    同一长度窗口里模糊匹配上就采信成稿的字形；匹配不上就不动（怕是另一句话）。
    """
    txt = str(chunk.get("text") or "")
    base = str(sent_text or "")
    n = len(txt.replace(" ", ""))
    if not base or n == 0 or n > len(base):
        return chunk, cursor
    import difflib

    best_ratio, best, best_at = 0.0, "", cursor
    for i in range(max(0, cursor), len(base) - n + 1):
        win = base[i : i + n]
        if len(win) != n:
            continue
        r = difflib.SequenceMatcher(None, win, txt).ratio()
        if r > best_ratio:
            best_ratio, best, best_at = r, win, i
    if best_ratio >= CONFIRM_MATCH_RATIO and best and best != txt:
        chunk = dict(chunk)
        chunk["text"] = best
    return chunk, best_at + (n if best else 0)

def split_sentence_into_chunks(
    sent: Dict[str, Any],
    max_chars: int = 10,
    font_path: Optional[str] = None,
    font_size: int = 62,
    max_safe_px: int = SUBTITLE_SYMMETRIC_MAX_W - 32,
    strip_punct: bool = True,
    protected_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    把长句按真静音 (>=0.30s) 呼吸组 + 词边界动态规划（DP）拆分为无残句、不破词、不拆保护词的短句字幕块。
    """
    clean_fn = strip_display_punctuation if strip_punct else (lambda s: str(s or "").strip())
    raw_sent_text = str(sent.get("text") or "").strip()
    words = _clean_word_tokens(list(sent.get("words") or []), strip_punct=strip_punct)
    # 字符级对齐修复 ASR 漏听/同音误听（「氛→氛围」「四大王→四大天王」「四大轩王→四大天王」）
    if strip_punct and raw_sent_text:
        words = _repair_words_from_script(words, raw_sent_text)
    elif raw_sent_text:
        words = _retarget_words_to_text(words, raw_sent_text)
    # 关键词过滤虚词碎片并向上扩全截断词（「一个」剔除，「六百」→「六百平」「三分」→「三分之一」）
    _disp_kws = sent.get("display_keywords")
    if isinstance(_disp_kws, list):
        raw_kws = [str(k) for k in _disp_kws if str(k).strip()]
    else:
        raw_kws = [str(k) for k in (sent.get("keywords") or []) if str(k).strip()]
    sent_kws = _clean_display_keywords(raw_kws, raw_sent_text)
    if words and sent_kws:
        # 若上游塞入跨越真实声学气口 (>=0.10s) 的碎词（如「公司名|改」中间停顿 0.18s），绝不让其充当保护词锁死断句
        clean_sent_str = "".join(str(w.get("word") or "") for w in words)
        word_end_gaps: List[Tuple[int, float]] = []
        acc_c = 0
        for wa, wb in zip(words, words[1:]):
            acc_c += len(str(wa.get("word") or ""))
            g_w = float(wb.get("start_sec", 0.0)) - float(wa.get("end_sec", 0.0))
            if g_w >= 0.10:
                word_end_gaps.append((acc_c, g_w))
        if word_end_gaps and clean_sent_str:
            filtered_kws: List[str] = []
            for kw in sent_kws:
                kw_pos = clean_sent_str.find(kw)
                if kw_pos >= 0 and any(kw_pos < gp < kw_pos + len(kw) for gp, _ in word_end_gaps):
                    continue
                filtered_kws.append(kw)
            sent_kws = filtered_kws
    if words:
        if any(w.get("is_keyword") for w in words) and sent_kws == raw_kws:
            pass
        elif any(w.get("is_keyword") for w in words) or sent_kws:
            # 只要做过关键词清洗，同步按完整清洗结果重染（含 cleaned==[] 时清掉上游脏标签，同时不误伤单元测试构造的 words 标色）
            words = _paint_keyword_flags(words, sent_kws)
    eff_terms = load_protected_terms(extra_terms=list(protected_terms or []) + [str(k) for k in sent_kws])

    if not words:
        raw_t = str(sent.get("text", "")).strip()
        dp_segs = dp_segment_short_lines(
            raw_t,
            max_width_px=max_safe_px,
            font_path=font_path,
            font_size=font_size,
            terms=eff_terms,
            max_chars=max_chars,
            strip_punct=strip_punct,
        )
        if not dp_segs:
            clean_txt = clean_fn(raw_t).strip()
            return [{
                "text": clean_txt or raw_t,
                "start_sec": sent["start_sec"],
                "end_sec": sent["end_sec"],
                "words": [],
                "keywords": sent_kws,
            }]
        total_chars = max(1, sum(len(t.replace(" ", "")) for _, _, t in dp_segs))
        t0, t1 = float(sent["start_sec"]), float(sent["end_sec"])
        span = max(0.2, t1 - t0)
        cursor_t = t0
        out_no_words: List[Dict[str, Any]] = []
        for _, _, seg_t in dp_segs:
            seg_dur = span * (max(1, len(seg_t.replace(" ", ""))) / total_chars)
            out_no_words.append({
                "text": seg_t,
                "start_sec": round(cursor_t, 4),
                "end_sec": round(min(t1, cursor_t + seg_dur), 4),
                "words": [],
                "keywords": sent_kws,
            })
            cursor_t += seg_dur
        return _guard_no_aux_head(out_no_words, max_chars=max_chars)

    breath_groups: List[List[Dict[str, Any]]] = []
    cur_grp: List[Dict[str, Any]] = [words[0]]
    for prev_w, cur_w in zip(words, words[1:]):
        gap = float(cur_w["start_sec"]) - float(prev_w["end_sec"])
        # 句末强制断组：一屏之内不跨句；仅当真实大停顿 (>=0.38s) 时直接拆呼吸组，
        # 中小微停顿 (0.10..0.38s) 交由 DP 结合 word_gaps 与词边界做全局最优断句。
        if prev_w.get("_sent_end"):
            breath_groups.append(cur_grp)
            cur_grp = [cur_w]
            continue
        if gap >= 0.38 and len(cur_grp) >= 4:
            breath_groups.append(cur_grp)
            cur_grp = [cur_w]
        else:
            cur_grp.append(cur_w)
    if cur_grp:
        last_gap = (
            float(cur_grp[0]["start_sec"]) - float(breath_groups[-1][-1]["end_sec"])
            if breath_groups else 0.0
        )
        if breath_groups and len(cur_grp) <= 3 and not breath_groups[-1][-1].get("_sent_end") and last_gap < 0.35:
            breath_groups[-1].extend(cur_grp)
        else:
            breath_groups.append(cur_grp)

    chunks: List[Dict[str, Any]] = []
    for grp in breath_groups:
        txt = "".join(str(x["word"]) for x in grp)
        px_w, _ = _measure_text_px(txt, font_path, font_size)
        has_clear_gap = any(
            (float(b["start_sec"]) - float(a["end_sec"]) >= 0.18)
            and min(i + 1, len(grp) - i - 1) >= 4
            for i, (a, b) in enumerate(zip(grp, grp[1:]))
        )
        if len(txt) <= max_chars and px_w <= max_safe_px and not has_clear_gap:
            chunks.append({
                "text": txt,
                "start_sec": grp[0]["start_sec"],
                "end_sec": grp[-1]["end_sec"],
                "words": list(grp),
                "keywords": sent_kws,
            })
            continue

        # 按「先保留标点成稿分词 DP，后剥标点回填逐字时间轴」流程：从成稿把软标点（，、；：。！？）回贴至 grp，
        # 同时把 words 之间真实声学气口映射为字符级 word_gaps 喂给 DP，让断点主动落在真实换气处。
        dp_input = _restore_script_punctuation(txt, [raw_sent_text]) if (strip_punct and raw_sent_text) else txt
        char_to_wi: List[int] = []
        for wi, w_item in enumerate(grp):
            for _ in str(w_item["word"]):
                char_to_wi.append(wi)
        dp_to_norm: List[Optional[int]] = []
        norm_i = 0
        for ch in dp_input:
            if ch in _DP_SOFT_PUNCT or ch in "。！？!?":
                dp_to_norm.append(None)
            else:
                dp_to_norm.append(norm_i if norm_i < len(char_to_wi) else None)
                norm_i += 1
        word_gaps: Dict[int, float] = {}
        for pos in range(1, len(dp_input)):
            p_norm = next((dp_to_norm[k] for k in range(pos - 1, -1, -1) if dp_to_norm[k] is not None), None)
            n_norm = next((dp_to_norm[k] for k in range(pos, len(dp_to_norm)) if dp_to_norm[k] is not None), None)
            if p_norm is None or n_norm is None:
                continue
            p_wi, n_wi = char_to_wi[p_norm], char_to_wi[n_norm]
            if p_wi != n_wi:
                word_gaps[pos] = max(0.0, float(grp[n_wi]["start_sec"]) - float(grp[p_wi]["end_sec"]))

        dp_segs = dp_segment_short_lines(
            dp_input,
            max_width_px=max_safe_px,
            font_path=font_path,
            font_size=font_size,
            terms=eff_terms,
            max_chars=max_chars,
            strip_punct=strip_punct,
            word_gaps=word_gaps,
        )
        used_end_wi = 0
        for s_ch, e_ch, _ in dp_segs:
            mapped = [dp_to_norm[k] for k in range(max(0, s_ch), min(len(dp_to_norm), e_ch)) if dp_to_norm[k] is not None]
            if not mapped:
                continue
            w_start = max(used_end_wi, char_to_wi[mapped[0]])
            w_end = char_to_wi[min(len(char_to_wi) - 1, mapped[-1])] + 1
            if w_end <= w_start:
                continue
            sl = grp[w_start:w_end]
            used_end_wi = w_end
            if sl:
                chunks.append({
                    "text": "".join(str(x["word"]) for x in sl),
                    "start_sec": sl[0]["start_sec"],
                    "end_sec": sl[-1]["end_sec"],
                    "words": list(sl),
                    "keywords": sent_kws,
                })
        if used_end_wi < len(grp):
            tail_sl = grp[used_end_wi:]
            if chunks and len(tail_sl) <= 3:
                chunks[-1]["words"].extend(tail_sl)
                chunks[-1]["text"] += "".join(str(x["word"]) for x in tail_sl)
                chunks[-1]["end_sec"] = tail_sl[-1]["end_sec"]
            elif tail_sl:
                chunks.append({
                    "text": "".join(str(x["word"]) for x in tail_sl),
                    "start_sec": tail_sl[0]["start_sec"],
                    "end_sec": tail_sl[-1]["end_sec"],
                    "words": list(tail_sl),
                    "keywords": sent_kws,
                })
    # 时间必须落在本句窗口内、且前后不重叠：成稿文字与 ASR 词表对不齐时，
    # 插值会算到句界之外（2026-11-24 实测最大越窗 1480ms，1 处）
    t_lo = float(sent["start_sec"])
    t_hi = float(sent["end_sec"])
    prev_end = t_lo
    for _c in chunks:
        st = min(max(float(_c["start_sec"]), prev_end), t_hi)
        en = min(max(float(_c["end_sec"]), st + 0.08), t_hi)
        _c["start_sec"] = round(st, 4)
        _c["end_sec"] = round(en, 4)
        prev_end = en
    for _c in chunks:
        for _w in _c.get("words") or []:
            _w.pop("_sent_end", None)
    return _guard_no_aux_head(chunks, max_chars=max_chars)


_BAD_SUB_HEAD_AUX = frozenset("的地得之了着过嘅啲嘛呢吧啊呀")


def _guard_no_aux_head(chunks: List[Dict[str, Any]], max_chars: int = 12) -> List[Dict[str, Any]]:
    """全风格通用字幕行首护栏：绝不允许以「的/地/得/了/着/过/嘅」等助词作下一条字幕的第一个字。"""
    if len(chunks) < 2:
        return chunks
    out = [dict(c) for c in chunks]
    changed = True
    while changed:
        changed = False
        for k in range(len(out) - 1):
            cur_t = str(out[k].get("text") or "")
            nxt_t = str(out[k + 1].get("text") or "")
            if not cur_t or not nxt_t or nxt_t[0] not in _BAD_SUB_HEAD_AUX:
                continue
            cur_w = list(out[k].get("words") or [])
            nxt_w = list(out[k + 1].get("words") or [])
            if len(cur_t) + len(nxt_t) <= max(12, max_chars + 2):
                out[k]["text"] = cur_t + nxt_t
                out[k]["end_sec"] = max(float(out[k]["end_sec"]), float(out[k + 1]["end_sec"]))
                out[k]["words"] = cur_w + nxt_w
                out[k]["keywords"] = list(dict.fromkeys(list(out[k].get("keywords") or []) + list(out[k + 1].get("keywords") or [])))
                out.pop(k + 1)
                changed = True
                break
            if len(nxt_t) >= 2 and len(cur_t) < 13:
                ch_mv = nxt_t[0]
                out[k]["text"] = cur_t + ch_mv
                out[k + 1]["text"] = nxt_t[1:]
                if nxt_w and len(str(nxt_w[0].get("word") or "")) == 1:
                    w_mv = nxt_w.pop(0)
                    cur_w.append(w_mv)
                    out[k]["words"] = cur_w
                    out[k + 1]["words"] = nxt_w
                    out[k]["end_sec"] = float(w_mv.get("end_sec", out[k]["end_sec"]))
                    if nxt_w:
                        out[k + 1]["start_sec"] = float(nxt_w[0].get("start_sec", out[k + 1]["start_sec"]))
                changed = True
                break
    return out


_TAIL_FILLER_CHARS = set("嘅啲啦嘛的了呢吧啊呀哦喂么咯噃囉㗎")
_BAD_TAIL_AUX_CHARS = _TAIL_FILLER_CHARS | {"有", "系", "在", "着", "去", "来", "住", "到", "个", "就", "得", "把", "被", "从", "跟", "比", "和", "与"}


def _subtitle_tail_highlight_target(style_id: str) -> float:
    """从因果规则表读取正文句尾重音覆盖率（仅 style_01 54% / style_06 67% 等显式启用字幕高亮的风格生效）。"""
    kh = element_rules.get_element_rule(style_id, "keyword_highlight")
    if not isinstance(kh, dict) or not kh.get("present"):
        return 0.0
    ev = str((kh.get("why_appears") or {}).get("evidence") or "")
    m = re.search(r"占字幕句\s*(\d+(?:\.\d+)?)%", ev)
    if not m:
        return 0.0
    pct = float(m.group(1))
    return min(0.68, pct / 100.0) if pct > 0.0 else 0.0


def _apply_phrase_end_highlights(
    entries: List[Dict[str, Any]],
    target_ratio: float,
    protected_terms: Optional[List[str]] = None,
) -> int:
    """对尚未含关键词高亮且字数 >= 4 的非屏蔽短句，沿分词边界在句末实词（2–3 字）落下重音高亮，杜绝「住有/啦有/得系」等跨词伪高亮。"""
    active = [e for e in entries if not e.get("suppressed")]
    if not active or target_ratio <= 0.0:
        return 0
    target_n = int(round(len(active) * target_ratio))
    already = sum(1 for e in active if any(w.get("is_keyword") for w in (e.get("words") or [])))
    if already >= target_n:
        return 0
    added = 0
    for e in active:
        if already >= target_n:
            break
        words = e.get("words") or []
        plain = re.sub(r"\s+", "", str(e.get("text") or ""))
        if len(plain) < 4 or not words or any(w.get("is_keyword") for w in words):
            continue
        new_ws = [dict(w) for w in words]
        # 构造 words 文字字符映射，并结合 tokenize_semantic_words 按整词锁定句尾实词
        tok_src = "".join(re.sub(r"\s+", "", str(w.get("word") or "")) for w in new_ws)
        if not tok_src:
            continue
        sem_toks = tokenize_semantic_words(tok_src, terms=protected_terms)
        tok_spans: List[Tuple[int, int, str]] = []
        pos = 0
        for tk in sem_toks:
            ntk = re.sub(r"\s+", "", tk)
            if not ntk:
                continue
            tok_spans.append((pos, pos + len(ntk), ntk))
            pos += len(ntk)
        # 从句尾剥除纯语气助词、虚词搭子（「啲/啦/嘅/得系/住有」）后取最后一个实义词块
        picked_span: Optional[Tuple[int, int, str]] = None
        for cs, ce, ntk in reversed(tok_spans):
            if all(ch in _BAD_TAIL_AUX_CHARS for ch in ntk) or ntk in _STOP_KEYWORDS:
                continue
            if len(ntk) < 2 or ce - cs >= len(plain):
                continue
            picked_span = (cs, ce, ntk)
            break
        if picked_span is None:
            continue
        cs_pick, ce_pick, tail_phrase = picked_span
        max_hl = 2 if len(plain) <= 6 else (4 if len(tail_phrase) == 4 and ce_pick - cs_pick < len(plain) else 3)
        if len(tail_phrase) > max_hl:
            tail_phrase = tail_phrase[-max_hl:]
            cs_pick = ce_pick - len(tail_phrase)
        picked_indices: List[int] = []
        w_pos = 0
        for wi, w_item in enumerate(new_ws):
            wt = re.sub(r"\s+", "", str(w_item.get("word") or ""))
            if not wt:
                continue
            ws_i, we_i = w_pos, w_pos + len(wt)
            w_pos = we_i
            if ws_i < ce_pick and we_i > cs_pick:
                picked_indices.append(wi)
        if not picked_indices or (ce_pick - cs_pick) >= len(plain):
            continue
        for wi in picked_indices:
            new_ws[wi]["is_keyword"] = True
        e["words"] = new_ws
        kws = _clean_display_keywords(list(e.get("keywords") or []) + [tail_phrase], plain)
        if tail_phrase and tail_phrase not in kws:
            kws.append(tail_phrase)
        e["keywords"] = kws
        already += 1
        added += 1
    return added


def _build_safe_ass_style(raw_ass_style: str, font_path: Optional[str], font_size: Optional[int] = None) -> str:
    """动态对齐 ASS Style 中的商用字体名、字号、对称左右边距 (MarginL=MarginR=220 保正中 540px) 与 MarginV >= 452。"""
    parts = [p.strip() for p in str(raw_ass_style or "").split(",")]
    if len(parts) < 23:
        return str(raw_ass_style or "")
    if font_size and int(font_size) > 0:
        parts[2] = str(int(font_size))
    if font_path:
        stem = Path(font_path).stem
        low_stem = stem.lower()
        if "alimama" in low_stem:
            parts[1] = "Alimama ShuHeiTi"
        elif "smileysans" in low_stem:
            parts[1] = "Smiley Sans"
        elif "sourcehanserifcn-heavy" in low_stem:
            parts[1] = "Source Han Serif CN Heavy"
        elif "sourcehanserif" in low_stem:
            parts[1] = "Source Han Serif CN"
        elif "sourcehansans" in low_stem:
            parts[1] = "Source Han Sans CN"
        elif "notoserifsc" in low_stem:
            parts[1] = "Noto Serif CJK SC"
        elif "notosans" in low_stem:
            parts[1] = "Noto Sans CJK SC"
        elif "pingfang" not in low_stem and "stheiti" not in low_stem:
            parts[1] = stem
    sym_margin_x = str(CANVAS_WIDTH - SUBTITLE_SAFE_ZONE["x_max"])  # 220 -> [220, 860] 轴心 540px
    parts[19] = sym_margin_x
    parts[20] = sym_margin_x
    min_margin_v = CANVAS_HEIGHT - SUBTITLE_SAFE_ZONE["y_max"]  # 452
    try:
        cur_mv = int(float(parts[21]))
    except Exception:
        cur_mv = min_margin_v
    if cur_mv < min_margin_v:
        parts[21] = str(min_margin_v)
    return ",".join(parts)


def run_step5(
    pipeline_dir: Path,
    track: str = "store",
    template_id: Optional[str] = None,
) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl_p = pipeline_dir / "04_edl.json"
    edl = read_json(edl_p) if edl_p.exists() else {"clips": []}
    man_p = pipeline_dir / "01_manifest.json"
    manifest = read_json(man_p) if man_p.exists() else {"items": []}

    # 获取每段规范素材在 1080x1920 裁切后画布的精确像素人脸框（兼容归一化与像素坐标系）
    faces_by_mp4: Dict[str, List[Dict[str, int]]] = {}
    for it in manifest.get("items", []):
        mp = str(it.get("normalized_mp4", ""))
        f_list = parse_manifest_faces(it)
        if mp and f_list:
            faces_by_mp4[mp] = f_list

    code_ranges: List[Tuple[float, float, str]] = [
        (float(c["timeline_in_sec"]), float(c["timeline_out_sec"]), str(c["clip_id"]))
        for c in edl.get("clips", [])
        if c.get("track_role") == "code_broll"
    ]
    templates = load_subtitle_templates()
    if not template_id or template_id not in templates:
        template_id = "store_bold_yellow" if track == "store" else "creator_clean_white"
    tpl = templates[template_id]
    # 字体优先级：源模版原版字体（放 assets/style_spectrum/fonts/<style_id>/）> 可商用替代
    source_font_family = tpl.get("source_font_family") or (tpl.get("font_substitution") or {}).get("source_font")
    source_font = find_source_font(source_font_family, template_id, tpl.get("font_candidates"))
    exact_font = bool(source_font)
    font_path = source_font or find_commercial_font(tpl.get("font_candidates"), verified_only=True) or find_commercial_font()
    strip_punct = bool(tpl.get("punct_stripped", True))

    entries: List[Dict[str, Any]] = []
    ass_dialogues: List[str] = []
    kw_hex_bgr = tpl["keyword_color"].lstrip("#")
    ass_kw_color = f"&H00{kw_hex_bgr[4:6]}{kw_hex_bgr[2:4]}{kw_hex_bgr[0:2]}&"

    raw_intel_p = pipeline_dir / "00_raw_intel.json"
    raw_pain_kws = set(str(k) for k in ((read_json(raw_intel_p).get("pain_keywords") or []) if raw_intel_p.is_file() else []) if str(k).strip())

    # 切分前即加载 terms.txt 与原材关键词作为 DP 保护词，从源头禁止把专有名词/品牌词切碎
    # 2026-11-24 实测：术语表在素材侧 `_SVS工作区/术语表.txt`，而这里只找 pipeline/terms.txt，
    # 于是「金湾」「点赞茶楼」「卡座」「中空」全被切碎，字幕在词中间断行。
    prot_terms = load_protected_terms(_terms_file_for(pipeline_dir), extra_terms=list(raw_pain_kws))

    _kinetic_stage_suppressed = False
    try:
        from kinetic_choreographer import is_kinetic_stage_style
        _has_dir_plan = (Path(pipeline_dir) / "03b_director_plan.json").is_file()
        _kinetic_stage_suppressed = bool(is_kinetic_stage_style(template_id, enable_v6_director=_has_dir_plan))
    except Exception:
        _kinetic_stage_suppressed = False

    idx = 1
    suppressed_count = 0
    # 每套风格的字数上限来自源草稿实测（7–96 字），不再统一压到 14 字
    tpl_char_cap = max(6, int(tpl.get("max_chars_hard", 96)))
    base_fs = int(tpl.get("font_size", 62))
    _line_px = SUBTITLE_SYMMETRIC_MAX_W - 8
    _chars_by_font = max(4, int(SUBTITLE_SYMMETRIC_MAX_W // max(1, base_fs)))
    tpl_max_chars = max(4, min(tpl_char_cap, int(tpl.get("max_chars_per_line", 10)), _chars_by_font))
    kw_scale_f = float(tpl.get("keyword_scale", 1.133))
    eff_measure_fs = base_fs
    dual_banner = bool(tpl.get("dual_line_banner"))
    if _kinetic_stage_suppressed:
        # 动态舞台模式下由 kinetic_stage 承担画面渲染（如 style_02_unified L1 ~69px@max_w=920），
        # ASS 断词尺度保持 54px/11 字以内，既保证分词整洁又不因窄带宽度切碎长短语。
        chunk_fs = min(eff_measure_fs, 54)
        chunk_px = _line_px
        chunk_max_chars = min(tpl_char_cap, max(9, min(11 if template_id == "style_02" else 10, tpl_char_cap)))
    else:
        chunk_fs = min(84, max(62, eff_measure_fs)) if dual_banner else eff_measure_fs
        chunk_px = int(_line_px * 1.85) if dual_banner else _line_px
        chunk_max_chars = max(tpl_max_chars, min(tpl_char_cap, max(8, int(tpl.get("max_chars_per_line", 10))))) if dual_banner else tpl_max_chars

    # 合并相邻极短无强标点碎句（<0.85s 且间隔 <0.16s），但严禁跨越序数/转折起句界或较大换气停顿
    merged_sentences: List[Dict[str, Any]] = []
    for raw_s in audio_events["sentences"]:
        s_copy = dict(raw_s)
        s_copy["words"] = [dict(w) for w in (raw_s.get("words") or [])]
        if merged_sentences:
            prev_s = merged_sentences[-1]
            p_txt = str(prev_s.get("text") or "").strip()
            c_txt = str(s_copy.get("text") or "").strip()
            c_plain = strip_display_punctuation(c_txt).replace(" ", "")
            p_dur = float(prev_s.get("end_sec", 0.0)) - float(prev_s.get("start_sec", 0.0))
            c_dur = float(s_copy.get("end_sec", 0.0)) - float(s_copy.get("start_sec", 0.0))
            gap = float(s_copy.get("start_sec", 0.0)) - float(prev_s.get("end_sec", 0.0))
            same_source = str(prev_s.get("source_media") or "") == str(s_copy.get("source_media") or "")
            if (
                same_source
                and (p_dur < 0.85 or c_dur < 0.85)
                and -0.08 <= gap < 0.16
                and p_txt
                and c_plain
                and not _CLAUSE_START_PROTECT_RE.match(c_plain)
                and not p_txt.endswith(("。", "？", "！", "?", "!"))
                and len(p_txt) + len(c_txt) <= tpl_max_chars * 2 + 4
            ):
                mids = list(prev_s.get("merged_sentence_ids") or ([prev_s["id"]] if prev_s.get("id") else []))
                if s_copy.get("id") and s_copy["id"] not in mids:
                    mids.append(s_copy["id"])
                prev_s["merged_sentence_ids"] = mids
                prev_s["text"] = p_txt + c_txt
                prev_s["end_sec"] = max(float(prev_s.get("end_sec", 0.0)), float(s_copy.get("end_sec", 0.0)))
                prev_s["words"] = list(prev_s.get("words") or []) + list(s_copy.get("words") or [])
                prev_s["keywords"] = list(dict.fromkeys(list(prev_s.get("keywords") or []) + list(s_copy.get("keywords") or [])))
                continue
        merged_sentences.append(s_copy)

    # 加载 02c_script_final.json 成稿软标点，供无标点口播段回贴自然逗号断点
    script_lines_punct: List[str] = []
    sc_p = pipeline_dir / "02c_script_final.json"
    if sc_p.is_file():
        try:
            sc_doc = read_json(sc_p)
            script_lines_punct = [
                str(ln.get("text") or "").strip()
                for ln in (sc_doc.get("lines") or [])
                if isinstance(ln, dict) and str(ln.get("text") or "").strip()
            ]
        except Exception:
            script_lines_punct = []

    for sent in merged_sentences:
        sent_eff = dict(sent)
        if script_lines_punct:
            sent_eff["text"] = _restore_script_punctuation(str(sent_eff.get("text") or ""), script_lines_punct)
        s_txt_full = str(sent_eff.get("text") or "")
        raw_s_kws = list(sent_eff.get("keywords") or [])
        if raw_pain_kws:
            for pk in raw_pain_kws:
                if pk in s_txt_full and pk not in raw_s_kws:
                    raw_s_kws.append(pk)
        cleaned_kws = _clean_display_keywords(raw_s_kws, s_txt_full)
        sent_eff["keywords"] = cleaned_kws
        if isinstance(sent_eff.get("display_keywords"), list):
            sent_eff["display_keywords"] = _clean_display_keywords(sent_eff["display_keywords"], s_txt_full) or cleaned_kws
        if sent_eff.get("words"):
            sent_eff["words"] = _paint_keyword_flags(sent_eff["words"], cleaned_kws)
        sent_prot = load_protected_terms(None, extra_terms=[*prot_terms, *(sent_eff.get("keywords") or [])])
        _chunks = _merge_short_chunks(
            list(split_sentence_into_chunks(
                sent_eff,
                max_chars=chunk_max_chars,
                font_path=font_path,
                font_size=chunk_fs,
                max_safe_px=chunk_px,
                strip_punct=strip_punct,
                protected_terms=sent_prot,
            )),
            max_px=chunk_px,
            font_path=font_path,
            font_size=chunk_fs,
            protected_terms=sent_prot,
        )
        for ch in _chunks:
            st_sec = round(float(ch["start_sec"]), 4)
            en_sec = round(float(ch["end_sec"]), 4)
            sub_dur = max(0.05, en_sec - st_sec)

            # 收集该字幕在时间线上重叠镜头的精确人脸框以执行源头避脸
            active_faces: List[Dict[str, Any]] = []
            for c in edl.get("clips", []):
                if float(c["timeline_in_sec"]) < en_sec and float(c["timeline_out_sec"]) > st_sec:
                    active_faces.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))

            ch_kws = ch.get("keywords", sent.get("keywords", []))
            final_bbox, face_avoid_dy = _compute_authoritative_subtitle_bbox(
                ch["text"], tpl, font_path, active_faces, keywords=ch_kws, terms=sent_prot,
            )
            safe_ok, safe_msg = check_bbox_in_safe_zone(final_bbox)

            suppressed = False
            suppressed_by = None
            suppress_reason = None
            for cs, ce, cid in code_ranges:
                ov = max(0.0, min(en_sec, ce) - max(st_sec, cs))
                if ov / sub_dur >= 0.60:
                    suppressed = True
                    suppressed_by = cid
                    suppress_reason = "B9_code_broll_dual_channel_dedup"
                    suppressed_count += 1
                    break
            if _kinetic_stage_suppressed and not suppressed:
                suppressed = True
                suppressed_by = "kinetic_typography_stage"
                suppress_reason = "kinetic_typography_stage"
                suppressed_count += 1

            entry_obj: Dict[str, Any] = {
                "id": f"sub_{idx:03d}",
                "sentence_id": sent["id"],
                "text": ch["text"],
                "start_sec": st_sec,
                "end_sec": en_sec,
                "keywords": ch_kws,
                "words": ch["words"],
                "bbox": final_bbox,
                "final_bbox": final_bbox,
                "face_avoid_dy": int(face_avoid_dy),
                "safe_zone_ok": safe_ok,
                "safe_msg": safe_msg,
                "suppressed": suppressed,
            }
            if dual_banner and "\n" not in str(ch["text"]) and len(str(ch["text"]).strip()) >= 5:
                dl1, dl2 = split_semantic_two_lines(str(ch["text"]), keywords=ch_kws, terms=sent_prot)
                if dl2:
                    entry_obj["display_lines"] = [dl1, dl2]
            if sent.get("merged_sentence_ids"):
                entry_obj["merged_sentence_ids"] = list(sent["merged_sentence_ids"])
            if suppressed:
                entry_obj["suppressed_by"] = suppressed_by
                entry_obj["suppress_reason"] = suppress_reason or "B9_code_broll_dual_channel_dedup"

            entries.append(entry_obj)
            idx += 1

    # 跨句连贯助词护栏：消除句末切分留下的下一句首个「的/地/得/了/着/过/嘅」孤字开头（如「嘅店厨房」）
    k_cross = 0
    while k_cross < len(entries) - 1:
        cur_e = entries[k_cross]
        nxt_e = entries[k_cross + 1]
        if not cur_e.get("suppressed") and not nxt_e.get("suppressed"):
            t0 = str(cur_e.get("text") or "")
            t1 = str(nxt_e.get("text") or "")
            if (
                t0 and t1 and t1[0] in _BAD_SUB_HEAD_AUX
                and float(nxt_e["start_sec"]) - float(cur_e["end_sec"]) <= 0.35
                and len(t0) <= 15
            ):
                ch_mv = t1[0]
                cur_e["text"] = t0 + ch_mv
                nxt_e["text"] = t1[1:]
                cw = list(cur_e.get("words") or [])
                nw = list(nxt_e.get("words") or [])
                if nw and str(nw[0].get("word") or "")[:1] == ch_mv:
                    mv_w = nw.pop(0)
                    cw.append(mv_w)
                    cur_e["words"] = cw
                    nxt_e["words"] = nw
                    if mv_w.get("end_sec") is not None:
                        cur_e["end_sec"] = round(float(mv_w["end_sec"]), 4)
                    if nw and nw[0].get("start_sec") is not None:
                        nxt_e["start_sec"] = round(float(nw[0]["start_sec"]), 4)
                bb0, dy0 = _compute_authoritative_subtitle_bbox(cur_e["text"], tpl, font_path, [], keywords=cur_e.get("keywords"), terms=prot_terms)
                cur_e["bbox"] = cur_e["final_bbox"] = bb0
                cur_e["face_avoid_dy"] = int(dy0)
                cur_e["safe_zone_ok"], cur_e["safe_msg"] = check_bbox_in_safe_zone(bb0)
                if not nxt_e["text"].strip():
                    cur_e["end_sec"] = max(float(cur_e["end_sec"]), float(nxt_e["end_sec"]))
                    entries.pop(k_cross + 1)
                    continue
                bb1, dy1 = _compute_authoritative_subtitle_bbox(nxt_e["text"], tpl, font_path, [], keywords=nxt_e.get("keywords"), terms=prot_terms)
                nxt_e["bbox"] = nxt_e["final_bbox"] = bb1
                nxt_e["face_avoid_dy"] = int(dy1)
                nxt_e["safe_zone_ok"], nxt_e["safe_msg"] = check_bbox_in_safe_zone(bb1)
                continue
        k_cross += 1

    if not dual_banner:
        tail_hl_target = _subtitle_tail_highlight_target(template_id)
        if tail_hl_target > 0.0:
            _apply_phrase_end_highlights(entries, tail_hl_target, protected_terms=prot_terms)

    for entry_obj in entries:
        if entry_obj.get("suppressed"):
            continue
        st_sec = float(entry_obj["start_sec"])
        en_sec = float(entry_obj["end_sec"])
        ass_text = ""
        for w in entry_obj.get("words") or []:
            w_txt = _escape_ass_text(str(w["word"]))
            if not w_txt:
                continue
            if w.get("is_keyword"):
                ass_text += f"{{\\c{ass_kw_color}\\b1}}{w_txt}{{\\r}}"
            else:
                ass_text += w_txt
        if not ass_text:
            ass_text = _escape_ass_text(entry_obj["text"])
        plain_ass = _escape_ass_text(entry_obj["text"])
        # 双层柔和模糊阴影（Layer 0 柔黑阴影 + Layer 1 清晰主层，禁用自动换行 \q2）
        final_bbox = entry_obj["final_bbox"]
        ass_cx = SUBTITLE_CENTER_X
        ass_cy = int(final_bbox["y"]) + int(final_bbox["h"]) // 2
        ass_dialogues.append(
            f"Dialogue: 0,{fmt_ass_time(st_sec)},{fmt_ass_time(en_sec)},Default,,0,0,0,,"
            f"{{\\q2\\an5\\pos({ass_cx + 4},{ass_cy + 5})\\1c&H000000&\\1a&HB0&\\3c&H000000&\\3a&HB0&\\shad0\\blur4}}{plain_ass}"
        )
        ass_dialogues.append(
            f"Dialogue: 1,{fmt_ass_time(st_sec)},{fmt_ass_time(en_sec)},Default,,0,0,0,,"
            f"{{\\q2\\an5\\pos({ass_cx},{ass_cy})}}{ass_text}"
        )

    safe_ass_style = _build_safe_ass_style(tpl["ass_style"], font_path, font_size=base_fs)
    ass_content = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {CANVAS_WIDTH}",
        f"PlayResY: {CANVAS_HEIGHT}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        safe_ass_style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *ass_dialogues,
    ]) + "\n"

    (pipeline_dir / "05_subtitles.ass").write_text(ass_content, encoding="utf-8")

    # 同步导出纯净单行 SRT 与逐行断句检查报告（含字/秒节奏与保护词防拆核验）
    srt_lines: List[str] = []
    check_lines: List[str] = [f"字幕模板：{template_id} | 总条数：{len(entries)} | 让位挂起：{suppressed_count}", ""]
    active_entries = [e for e in entries if not e.get("suppressed")]
    for s_idx, ent in enumerate(active_entries, start=1):
        srt_lines.append(f"{s_idx}\n{fmt_srt_time(ent['start_sec'])} --> {fmt_srt_time(ent['end_sec'])}\n{ent['text']}\n")
        dur_e = max(0.05, float(ent["end_sec"]) - float(ent["start_sec"]))
        cps_e = len(str(ent["text"]).replace(" ", "")) / dur_e
        flag = "  <- 偏快 (>9字/秒)" if cps_e > 9.0 else ""
        check_lines.append(f"{s_idx:02d} [{ent['start_sec']:6.2f}-{ent['end_sec']:6.2f}] {dur_e:4.2f}s {cps_e:4.1f}字/秒 | {ent['text']}{flag}")

    broken_terms: List[str] = []
    for a, b in zip(active_entries, active_entries[1:]):
        ta = str(a["text"]).replace(" ", "")
        tb = str(b["text"]).replace(" ", "")
        joint = ta[-4:] + "|" + tb[:4]
        cut_i = joint.index("|")
        joined_plain = joint.replace("|", "")
        for tm in prot_terms:
            for m in re.finditer(re.escape(tm), joined_plain):
                if m.start() < cut_i < m.end():
                    broken_terms.append(f"{a['id']}/{b['id']} 拆开了「{tm}」")
    check_lines.append("\n保护词防拆检查：" + ("通过" if not broken_terms else "；".join(broken_terms)))
    (pipeline_dir / "05_subtitles.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    (pipeline_dir / "05_subtitle_check.txt").write_text("\n".join(check_lines) + "\n", encoding="utf-8")

    out_json = {
        "step": "05_subtitles",
        "template_id": template_id,
        "template_name": tpl["name"],
        "font_path": font_path,
        "font_license": tpl["license"],
        "font_source_family": source_font_family,
        "font_exact_match": exact_font,
        "exact_1to1_hint": (
            "字体已是源模版原版，字形可 1:1"
            if exact_font else
            "当前为可商用替代字体；把源模版字体放进 assets/style_spectrum/fonts/"
            f"{template_id}/ 即可按原版字形渲染"
        ),
        "template_config": tpl,
        "count": len(entries),
        "suppressed_count": suppressed_count,
        "kinetic_stage_suppressed": _kinetic_stage_suppressed,
        "srt_path": str((pipeline_dir / "05_subtitles.srt").resolve()),
        "check_report_path": str((pipeline_dir / "05_subtitle_check.txt").resolve()),
        "broken_protected_terms": broken_terms,
        "entries": entries,
    }
    # ── 字形覆盖门禁（fail-closed）──
    # PIL 不做字体回退，缺字就是空白/豆腐块；用户实测「阿里妈妈字体…很多字都显示不出来」。
    # 这里对将要上屏的全部字幕文本逐字核对「主字体 + glyph_fallback_chain」并落库 glyph_coverage，
    # 缺一个字就终止（exit 非 0），绝不带着豆腐块往下渲染。
    try:
        coverage = glyph_gate.coverage_report(
            [(str(e.get("id") or i), str(e.get("text") or "")) for i, e in enumerate(entries, start=1)],
            font_path,
            strict_chain=True,
        )
    except glyph_gate.GlyphCoverageError as exc:
        out_json["glyph_coverage"] = {"chars": 0, "missing": ["<配置错误>"], "fallback_used": {}, "error": str(exc)}
        write_json(pipeline_dir / "05_subtitles.json", out_json)
        print(f"[step5] 字形覆盖配置错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
    out_json["glyph_coverage"] = {
        "chars": coverage["chars"],
        "missing": coverage["missing"],
        "fallback_used": coverage["fallback_used"],
        "total_chars": coverage["total_chars"],
        "primary": coverage["primary"],
        "chain": coverage["chain"],
        "fallback_chars": coverage["fallback_chars"],
        "missing_lines": coverage["missing_lines"],
    }
    write_json(pipeline_dir / "05_subtitles.json", out_json)
    if coverage["missing"]:
        print(
            f"[step5] 字形覆盖 FAIL：{len(coverage['missing'])} 个字无字体可画"
            f"（{''.join(coverage['missing'])}），上屏会变成空白/豆腐块；"
            "请在 assets/fonts/font_aliases.json → glyph_fallback_chain 补一套覆盖的字体",
            file=sys.stderr,
        )
        for ln in coverage["missing_lines"]:
            print(f"  ✗ {ln['id']}「{ln['text']}」缺 {''.join(ln['chars'])}", file=sys.stderr)
        raise SystemExit(1)
    print(
        f"[step5] 字幕生成完成：{len(entries)} 条（图解画面自动让位 {suppressed_count} 条，"
        f"模板 `{template_id}`, 字体 `{Path(font_path).name if font_path else 'default'}`"
        f"｜字形覆盖 {coverage['chars']} 字全中，回退 {sum(coverage['fallback_used'].values())} 字次）"
        "-> 05_subtitles.json"
    )
    return out_json


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 5: 逐字时间戳字幕与统一避脸布局生成")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--template", help="字幕模板ID")
    parser.add_argument("--corrected-text", help="可选：人工校对后的纯文本文件，字符级回填逐字时间戳后重排字幕")
    args = parser.parse_args()
    pdir = Path(args.pipeline_dir)
    if args.corrected_text and Path(args.corrected_text).is_file():
        ae_p = pdir / "03_audio_events.json"
        if ae_p.is_file():
            ae = read_json(ae_p)
            corr_lines = [ln.strip() for ln in Path(args.corrected_text).read_text(encoding="utf-8").splitlines() if ln.strip()]
            for sent, corr in zip(ae.get("sentences") or [], corr_lines):
                aligned, ratio = align_corrected_text_to_words(corr, sent.get("words") or [])
                if aligned and ratio >= 0.35:
                    sent["text"] = corr
                    sent["words"] = aligned
            write_json(ae_p, ae)
    run_step5(pdir, track=args.track, template_id=args.template)


if __name__ == "__main__":
    main()
