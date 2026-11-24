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
from typing import Any, Dict, List, Optional, Tuple

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

# 仅保留夹在数字间的小数点/冒号(如 3.5 / 18:30)以及数字后的%，彻底清掉紧贴数字的中文逗号句号！
_NON_CORE_PUNCT_RE = re.compile(r"(?<!\d)[.:：](?!\d)|(?<!\d)[.:：](?=\d)|(?<=\d)[.:：](?!\d)|[，。、；！？,!?;“”‘’\"'（）()]+")

_DP_NO_LEAD_CHARS = set("的了呢吗吧啊着过么嘛上里中下后前")
_DP_NO_TAIL_CHARS = set("的把被从跟比在对向给让和与及是将用为")
_DP_UNIT_AFTER_NUM = set("倍度年多万亿%％岁个次米元块斤克天月时分秒件家店条步项位层种")
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
    "还是", "就是", "只是", "于是", "要是", "或是", "总是", "老是", "已经", "一定",
    "可能", "应该", "现在", "最后", "关键", "基本", "差不多", "下来", "起来", "出来",
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
            if t and not t.startswith("#") and t not in terms:
                terms.append(t)
    for t in (extra_terms or []):
        ts = str(t or "").strip()
        if len(ts) >= 2 and ts not in terms:
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


def tokenize_semantic_words(sentence: str, terms: Optional[List[str]] = None) -> List[str]:
    """
    词边界切分器：优先调用 jieba（若已安装），否则使用零依赖中文语义边界与保护词/数字量词合并分词器。
    严格保护 terms 中的专有名词、成语与“数字+单位”不被切碎。
    """
    s = str(sentence or "")
    if not s:
        return []
    prot = sorted([t for t in (terms or []) if t], key=len, reverse=True)
    raw_toks: List[str] = []

    try:
        import jieba  # type: ignore
        jieba.setLogLevel(60)
        for tm in prot:
            jieba.add_word(tm, freq=2_000_000)
        raw_toks = _merge_numeral_units([t for t in jieba.cut(s, HMM=False) if t])
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
    return merged


def dp_segment_short_lines(
    sentence: str,
    max_width_px: int,
    font_path: Optional[str] = None,
    font_size: int = 62,
    terms: Optional[List[str]] = None,
    max_chars: int = 12,
    strip_punct: bool = True,
) -> List[Tuple[int, int, str]]:
    """
    词边界动态规划（DP）最优短句切分：
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

    # 预计算保护词区间，并自动保护“中文连续数字+量词”（如 二十五张 / 八间 / 三百块），防止断句切词
    prot_spans: List[Tuple[int, int]] = []
    for tm in prot:
        for m in re.finditer(re.escape(tm), raw):
            prot_spans.append((m.start(), m.end()))
    for m in re.finditer(
        r"[零一二两三四五六七八九十百千万亿几多]+(?:[点余多来])?[零一二两三四五六七八九十百千万亿]*[个张间块元平米斤克秒分天年月人位套条次倍成折%]?",
        raw,
    ):
        if m.end() - m.start() >= 2:
            prot_spans.append((m.start(), m.end()))

    def _break_cost(pos: int) -> float:
        if pos <= 0 or pos >= len(raw):
            return 0.0
        left = raw[:pos]
        right = raw[pos:]
        # 句末标点与软标点一样是天然断点：此前只认软标点，导致「贵。其」跨句拼进同一行
        if left and (left[-1] in _DP_SOFT_PUNCT or left[-1] in "。！？!?"):
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
        for ps, pe in prot_spans:
            if ps < pos < pe:
                cost += 1000.0
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
            short_pen = max(0, 5 - compact_len) * 2.5 if j < nb - 1 or i > 0 else 0.0
            if compact_len <= 3 and (i > 0 or j < nb - 1):
                short_pen += 12.0
            cand = best[i] + _break_cost(bounds[j]) + short_pen + 1.0
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


def _compute_authoritative_subtitle_bbox(
    text: str,
    tpl: Dict[str, Any],
    font_path: Optional[str],
    face_boxes_px: List[Dict[str, Any]],
) -> Dict[str, int]:
    """按画布绝对正中 x=540 对称安全带 [220, 860] 计算尺寸并仅在垂直方向避开人脸五官，产出四端统一 final_bbox。"""
    base_fs = int(tpl.get("font_size", 62))
    kw_scale = float(tpl.get("keyword_scale", 1.133))
    eff_fs = int(round(base_fs * min(1.20, max(1.0, (1.0 + kw_scale) * 0.5))))
    safe_w_cap = SUBTITLE_SYMMETRIC_MAX_W  # 640px ([220, 860]，中心严格锁定 540px)
    center_x = SUBTITLE_CENTER_X  # 540px

    est_w, est_h = _measure_text_px(text, font_path, eff_fs)
    box_w = min(safe_w_cap, max(160, est_w + 32))
    box_h = min(220, max(84, est_h + 16))
    # 水平中心 100% 锁定画布正中央 540px，且 box_w <= 640 保证永不触发右侧 860px 单侧推偏
    box_x = center_x - box_w // 2

    # 钳位 safe_y_center，默认对齐黄金水位 SUBTITLE_DEFAULT_Y_CENTER (1310px)
    safe_yc = int(tpl.get("safe_y_center", SUBTITLE_DEFAULT_Y_CENTER))
    box_y = max(SUBTITLE_SAFE_ZONE["y_min"], min(SUBTITLE_SAFE_ZONE["y_max"] - box_h, safe_yc - box_h // 2))
    cand = {"x": int(box_x), "y": int(box_y), "w": int(box_w), "h": int(box_h)}

    if face_boxes_px:
        cur_iou = max(bbox_iou(cand, fb) for fb in face_boxes_px)
        if cur_iou > 0.01:
            y_lo = SUBTITLE_SAFE_ZONE["y_min"]
            y_hi = SUBTITLE_SAFE_ZONE["y_max"] - box_h
            try_ys = [
                y_hi,
                y_lo,
                max(y_lo, box_y - 110),
                min(y_hi, box_y + 110),
                max(y_lo, y_lo + 60),
                min(y_hi, y_hi - 60),
                (y_lo + y_hi) // 2,
            ]
            best_cand = cand
            best_iou = cur_iou
            for ty in try_ys:
                ty_c = max(y_lo, min(y_hi, int(ty)))
                alt = dict(cand, y=ty_c)
                iou_v = max(bbox_iou(alt, fb) for fb in face_boxes_px)
                if iou_v <= 0.01:
                    best_cand = alt
                    break
                if iou_v < best_iou:
                    best_iou = iou_v
                    best_cand = alt
            cand = best_cand
    return cand


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

def _merge_short_chunks(chunks: List[Dict[str, Any]], min_chars: int = MIN_CHUNK_CHARS) -> List[Dict[str, Any]]:
    """把 2~3 个字的残句并进相邻块。

    为什么：字号修到真值（75px）后一行只放得下 8 个字，句尾常被切出一个「仲有」「去制造」，
    单占一屏既难看也被平台安全区体检判成「残句」。
    """
    out: List[Dict[str, Any]] = []
    for ch in chunks:
        txt = str(ch.get("text") or "").replace(" ", "")
        if out and len(txt) < min_chars:
            prev = out[-1]
            gap = float(ch["start_sec"]) - float(prev["end_sec"])
            if gap <= 0.50:
                prev["text"] = str(prev.get("text") or "") + str(ch.get("text") or "")
                prev["end_sec"] = max(float(prev["end_sec"]), float(ch["end_sec"]))
                prev["words"] = list(prev.get("words") or []) + list(ch.get("words") or [])
                prev["keywords"] = list(dict.fromkeys(list(prev.get("keywords") or []) + list(ch.get("keywords") or [])))
                continue
        out.append(dict(ch))
    # 并不到一起的残块（中间隔了停顿）如果这一句还有别的块，就别单独占一屏
    if len(out) >= 2:
        out = [c for c in out if len(str(c.get("text") or "").replace(" ", "")) >= min_chars] or out
    # 头一块太短（第一块就只剩两个字）并到后一块
    if len(out) >= 2 and len(str(out[0].get("text") or "")) < min_chars:
        first, second = out[0], out[1]
        if float(second["start_sec"]) - float(first["end_sec"]) <= 0.50:
            second["text"] = str(first.get("text") or "") + str(second.get("text") or "")
            second["start_sec"] = min(float(first["start_sec"]), float(second["start_sec"]))
            second["words"] = list(first.get("words") or []) + list(second.get("words") or [])
            out.pop(0)
    return out


def _confirm_chunk_text(chunk: Dict[str, Any], sent_text: str, cursor: int = 0) -> Tuple[Dict[str, Any], int]:
    """把 ASR 听错的字换成用户核过的成稿用字（只换字，不动时间轴）。

    例：ASR 把「四大天王」听成「四大轩王」，词表上屏就会印错。成稿是用户核过的，
    同一长度窗口里模糊匹配上就采信成稿的字形；匹配不上就不动（怕是另一句话）。
    """
    txt = str(chunk.get("text") or "")
    base = str(sent_text or "")
    n = len(txt.replace(" ", ""))
    if not base or n == 0 or n > len(base):
        return chunk
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
    words = _clean_word_tokens(list(sent.get("words") or []), strip_punct=strip_punct)
    # 字形按成稿订正（只换字，不增删词），避免「四大轩王」这种同音误听印上屏
    words = _retarget_words_to_text(words, str(sent.get("text") or ""))
    sent_kws = list(sent.get("keywords") or [])
    # 字幕文字一律跟**嘴上说的**走（逐词时间戳来自 ASR 词表）。
    # 2026-11-24 实测教训：成稿那一行是「整理后」的文字（去口癖、合并半句），跟原始录音并不逐字一致；
    # 拿它去覆盖字幕，屏幕上就会出现「𠮶边就系我哋嘅厨房」而观众听到的是「同大家介绍下啦…」，
    # 而且按字对齐会把时间戳拉到句子另一端，出现 6 秒的空档。整理只用于决定**剪哪几句**，
    # 上屏文字由词表清洗得到（口癖词单独剔掉，不动时间轴）。
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
        return out_no_words

    breath_groups: List[List[Dict[str, Any]]] = []
    cur_grp: List[Dict[str, Any]] = [words[0]]
    for prev_w, cur_w in zip(words, words[1:]):
        gap = float(cur_w["start_sec"]) - float(prev_w["end_sec"])
        # 句末强制断组：一屏之内不跨句
        if prev_w.get("_sent_end"):
            breath_groups.append(cur_grp)
            cur_grp = [cur_w]
            continue
        if gap >= 0.30 and len(cur_grp) >= 4:
            breath_groups.append(cur_grp)
            cur_grp = [cur_w]
        else:
            cur_grp.append(cur_w)
    if cur_grp:
        if breath_groups and len(cur_grp) <= 3:
            breath_groups[-1].extend(cur_grp)
        else:
            breath_groups.append(cur_grp)

    chunks: List[Dict[str, Any]] = []
    for grp in breath_groups:
        txt = "".join(str(x["word"]) for x in grp)
        px_w, _ = _measure_text_px(txt, font_path, font_size)
        if len(txt) <= max_chars + 1 and px_w <= max_safe_px:
            chunks.append({
                "text": txt,
                "start_sec": grp[0]["start_sec"],
                "end_sec": grp[-1]["end_sec"],
                "words": list(grp),
                "keywords": sent_kws,
            })
            continue

        # 构造字符偏移到 word token 索引的精确映射，保证 DP 按语义词边界切片不丢逐字时间戳
        char_to_wi: List[int] = []
        for wi, w_item in enumerate(grp):
            for _ in str(w_item["word"]):
                char_to_wi.append(wi)

        dp_segs = dp_segment_short_lines(
            txt,
            max_width_px=max_safe_px,
            font_path=font_path,
            font_size=font_size,
            terms=eff_terms,
            max_chars=max_chars,
            strip_punct=strip_punct,
        )
        used_end_wi = 0
        for s_ch, e_ch, _ in dp_segs:
            if s_ch >= len(char_to_wi):
                continue
            w_start = max(used_end_wi, char_to_wi[s_ch])
            w_end = char_to_wi[min(len(char_to_wi) - 1, max(s_ch, e_ch - 1))] + 1
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
    return chunks


def _build_safe_ass_style(raw_ass_style: str, font_path: Optional[str]) -> str:
    """动态对齐 ASS Style 中的商用字体名、对称左右边距 (MarginL=MarginR=220 保正中 540px) 与 MarginV >= 452。"""
    parts = [p.strip() for p in str(raw_ass_style or "").split(",")]
    if len(parts) < 23:
        return str(raw_ass_style or "")
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

    idx = 1
    suppressed_count = 0
    # 每套风格的字数上限来自源草稿实测（7–96 字），不再统一压到 14 字
    tpl_char_cap = max(6, int(tpl.get("max_chars_hard", 96)))
    base_fs = int(tpl.get("font_size", 62))
    # 每行字数必须由**字号**反推，而不是先让渲染器把字号砍小：
    # 原版 style_21 一行最多 8 字、行宽约 516px；字号 75px 时 8 字 = 600px 正好落在 640 安全带内。
    # 以前这里直接吃库里写死的 10，字号一修到真值就会被 render_subtitle_png 的溢出兜底
    # 默默砍回 60 左右（用户看到的「字还是小」），这里把源头卡住，字号才能保住。
    _line_px = SUBTITLE_SYMMETRIC_MAX_W - 32  # 与 split_sentence_into_chunks 的 max_safe_px 同口径
    _chars_by_font = max(4, int(_line_px // max(1, base_fs)))
    tpl_max_chars = max(4, min(tpl_char_cap, int(tpl.get("max_chars_per_line", 10)), _chars_by_font))
    kw_scale_f = float(tpl.get("keyword_scale", 1.133))
    eff_measure_fs = int(round(base_fs * max(1.0, (1.0 + kw_scale_f) * 0.5)))

    # 实测痛点 #12 根治：合并相邻极短无强标点碎句（<0.85s 且间隔 <0.35s），避免“大约有二十”+“五张左右”被强行硬拆
    merged_sentences: List[Dict[str, Any]] = []
    for raw_s in audio_events["sentences"]:
        s_copy = dict(raw_s)
        s_copy["words"] = [dict(w) for w in (raw_s.get("words") or [])]
        if merged_sentences:
            prev_s = merged_sentences[-1]
            p_txt = str(prev_s.get("text") or "").strip()
            c_txt = str(s_copy.get("text") or "").strip()
            p_dur = float(prev_s.get("end_sec", 0.0)) - float(prev_s.get("start_sec", 0.0))
            c_dur = float(s_copy.get("end_sec", 0.0)) - float(s_copy.get("start_sec", 0.0))
            gap = float(s_copy.get("start_sec", 0.0)) - float(prev_s.get("end_sec", 0.0))
            if (
                (p_dur < 0.85 or c_dur < 0.85)
                and 0.0 <= gap <= 0.35
                and p_txt
                and not p_txt.endswith(("。", "？", "！", "?", "!"))
                and len(p_txt) + len(c_txt) <= tpl_max_chars * 2 + 4
            ):
                prev_s["text"] = p_txt + c_txt
                prev_s["end_sec"] = max(float(prev_s.get("end_sec", 0.0)), float(s_copy.get("end_sec", 0.0)))
                prev_s["words"] = list(prev_s.get("words") or []) + list(s_copy.get("words") or [])
                prev_s["keywords"] = list(dict.fromkeys(list(prev_s.get("keywords") or []) + list(s_copy.get("keywords") or [])))
                continue
        merged_sentences.append(s_copy)

    for sent in merged_sentences:
        sent_eff = dict(sent)
        if raw_pain_kws:
            s_kws = list(sent_eff.get("keywords") or [])
            s_txt_full = str(sent_eff.get("text") or "")
            for pk in raw_pain_kws:
                if pk in s_txt_full and pk not in s_kws:
                    s_kws.append(pk)
            sent_eff["keywords"] = s_kws
            if sent_eff.get("words"):
                matched_pks = [pk for pk in raw_pain_kws if pk and pk in s_txt_full]
                if matched_pks:
                    words_src = sent_eff["words"]
                    new_ws = [dict(w) for w in words_src]
                    # 精确拼接连续字词定位完整关键短语区间，杜绝单字包含误染
                    buf_tokens: List[Tuple[int, int, int]] = []
                    cur_pos = 0
                    joined_w = []
                    for wi, w_item in enumerate(new_ws):
                        wt = re.sub(r"\s+", "", str(w_item.get("word") or ""))
                        if not wt:
                            continue
                        joined_w.append(wt)
                        buf_tokens.append((cur_pos, cur_pos + len(wt), wi))
                        cur_pos += len(wt)
                    joined_str = "".join(joined_w)
                    for pk in matched_pks:
                        pk_clean = re.sub(r"\s+", "", pk)
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
                    sent_eff["words"] = new_ws
        sent_prot = load_protected_terms(None, extra_terms=[*prot_terms, *(sent_eff.get("keywords") or [])])
        _chunks = _merge_short_chunks(list(split_sentence_into_chunks(
            sent_eff,
            max_chars=tpl_max_chars,
            font_path=font_path,
            font_size=eff_measure_fs,
            strip_punct=strip_punct,
            protected_terms=sent_prot,
        )))
        for ch in _chunks:
            st_sec = round(float(ch["start_sec"]), 4)
            en_sec = round(float(ch["end_sec"]), 4)
            sub_dur = max(0.05, en_sec - st_sec)

            # 收集该字幕在时间线上重叠镜头的精确人脸框以执行源头避脸
            active_faces: List[Dict[str, Any]] = []
            for c in edl.get("clips", []):
                if float(c["timeline_in_sec"]) < en_sec and float(c["timeline_out_sec"]) > st_sec:
                    active_faces.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))

            final_bbox = _compute_authoritative_subtitle_bbox(ch["text"], tpl, font_path, active_faces)
            safe_ok, safe_msg = check_bbox_in_safe_zone(final_bbox)

            suppressed = False
            suppressed_by = None
            for cs, ce, cid in code_ranges:
                ov = max(0.0, min(en_sec, ce) - max(st_sec, cs))
                if ov / sub_dur >= 0.60:
                    suppressed = True
                    suppressed_by = cid
                    suppressed_count += 1
                    break

            entry_obj: Dict[str, Any] = {
                "id": f"sub_{idx:03d}",
                "sentence_id": sent["id"],
                "text": ch["text"],
                "start_sec": st_sec,
                "end_sec": en_sec,
                "keywords": ch.get("keywords", sent.get("keywords", [])),
                "words": ch["words"],
                "bbox": final_bbox,
                "final_bbox": final_bbox,
                "safe_zone_ok": safe_ok,
                "safe_msg": safe_msg,
                "suppressed": suppressed,
            }
            if suppressed:
                entry_obj["suppressed_by"] = suppressed_by
                entry_obj["suppress_reason"] = "B9_code_broll_dual_channel_dedup"

            entries.append(entry_obj)

            if not suppressed:
                ass_text = ""
                for w in ch["words"]:
                    w_txt = _escape_ass_text(str(w["word"]))
                    if not w_txt:
                        continue
                    if w.get("is_keyword"):
                        ass_text += f"{{\\c{ass_kw_color}\\b1}}{w_txt}{{\\r}}"
                    else:
                        ass_text += w_txt
                if not ass_text:
                    ass_text = _escape_ass_text(ch["text"])
                plain_ass = _escape_ass_text(ch["text"])
                # 双层柔和模糊阴影（Layer 0 柔黑阴影 + Layer 1 清晰主层，禁用自动换行 \q2）
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
            idx += 1

    safe_ass_style = _build_safe_ass_style(tpl["ass_style"], font_path)
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
        "srt_path": str((pipeline_dir / "05_subtitles.srt").resolve()),
        "check_report_path": str((pipeline_dir / "05_subtitle_check.txt").resolve()),
        "broken_protected_terms": broken_terms,
        "entries": entries,
    }
    write_json(pipeline_dir / "05_subtitles.json", out_json)
    print(
        f"[step5] 字幕生成完成：{len(entries)} 条（图解画面自动让位 {suppressed_count} 条，"
        f"模板 `{template_id}`, 字体 `{Path(font_path).name if font_path else 'default'}`）-> 05_subtitles.json"
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
