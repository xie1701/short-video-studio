#!/usr/bin/env python3
"""把「用户核过的成稿」对到逐字稿的字级时间戳上，算出**只该保留哪几段声音**。

为什么需要它（2026-11-24 实测，用户原话：「还是混有大量导演/摄影师的声音，不应该
通过ASR就把这些声音片段剪掉吗？就按确认的口播文案，其它的就要毫不留情的剪掉！」）：

成稿的一句话是按「转写段」整段切的，而一个转写段里往往混着：

- 打板口令与现场指令（「一组」「三一组」「ok 嚟」）；
- 同一个意思说错重来的半句（「厨房啦几多嘅厨房两百平」）；
- 他自己的口癖与语气词（「诶」「咧」）；
- 别人的插话。

这些字都在被保留的那个时间窗里，所以光按句子切、片子照样把它们带进成片。
正确做法：把成稿的每个字对到逐字稿的字级时间戳上，**只保留对得上的那些字所在的时间段**，
对不上的整段砍掉（连视频一起砍，否则口型对不上）。

本模块只做对齐与切段，不碰音频、不碰渲染：

- `align_text_to_words()`：字符级 DP 对齐（支持同音字弱匹配，粤语字面与普通话字混排也能对）。
- `keep_intervals()`：把匹配到的字收成若干「保留区间」，并把每个区间该显示的文字切出来。

对不上的情况（成稿被改写过、ASR 丢字）会在 `coverage` 里如实报出，调用方据此决定要不要退回旧口径。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── 对齐代价（只在参数不同的实现里改这里）────────────────────────────────
COST_MATCH = 0.0          # 字面相同
COST_HOMOPHONE = 0.35     # 同音不同字（ASR 错字、繁简、粤普混写）
COST_MISS = 1.20          # 成稿有、逐字稿没有（成稿改写过，或 ASR 漏字）
COST_SKIP_AUDIO = 0.45    # 逐字稿有、成稿没有（口令、重来、口癖、插话）—— 该砍，但要付出代价

# ── 切段参数 ─────────────────────────────────────────────────────────────
# 判断「该不该在这里断开」只看一件事：两段保留内容之间**是不是夹着成稿之外的话**。
# 纯停顿（没字的地方）一律不切——那是他自己在喘气，切了反而像卡带。
SKIP_SPEECH_SEC = 1.00    # 夹在中间的「非成稿语音」超过这么久才切（更短的算口癖与绊嘴，留着自然）
MIN_RUN_SEC = 1.00        # 一刀下去剩下的碎段比这还短，就不切（段首/段尾的碎段直接丢）
MERGE_GAP_SEC = 0.16      # 两段保留内容之间只隔这么点，并成一段
LONG_CUT_SEC = 1.00       # 段首/段尾被整句重来的话隔开时，那半句废开头直接丢
LEAD_SEC = 0.05           # 每个保留区间前后垫一点，避免切到字的起音
TAIL_SEC = 0.16

# 成稿覆盖率低于这个值时只砍段首/段尾的非成稿语音，不动句中（成稿被改写过时不敢乱切）
MIN_COVERAGE = 0.72
# 覆盖率低到这个程度，就说明根本没对上（成稿换了内容），调用方应退回整段保留
MIN_OK_COVERAGE = 0.45

_PUNCT = set("，,。.、；;：:！!？?…「」『』\"'“”‘’（）()《》【】[]—～~· \t\n\r")


def strip_punct(text: str) -> List[str]:
    """去掉标点与空白，只留正文候选字（保留文字顺序）。"""
    return [ch for ch in str(text or "") if ch and ch not in _PUNCT]


def _pinyin(ch: str) -> str:
    try:
        from pypinyin import lazy_pinyin  # 延迟导入：没装也能跑

        py = lazy_pinyin(ch)
        return py[0] if py else ""
    except Exception:
        return ""


def _norm_ch(ch: str) -> str:
    """繁简、全半角统一的粗归一（只用于比较，不改输出）。"""
    ch = str(ch)
    try:
        from opencc import OpenCC  # type: ignore

        return OpenCC("t2s").convert(ch)
    except Exception:
        return ch


def _same_kind(a: str, b: str) -> Optional[float]:
    """返回这两个字的对齐代价；None 表示不算匹配。"""
    if a == b:
        return COST_MATCH
    na, nb = _norm_ch(a), _norm_ch(b)
    if na == nb:
        return COST_MATCH
    if not na or not nb:
        return None
    pa, pb = _pinyin(a), _pinyin(b)
    if pa and pb and pa == pb:
        return COST_HOMOPHONE
    return None


def char_tokens(words: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把逐字稿的「词」摊成「字」，每个字带自己的时间。

    SenseVoice 的粤语字面基本是一字一词；遇到多字词按字数均分时间，
    不会让一个字吞掉整段时长（那会让切点偏出去）。
    """
    out: List[Dict[str, Any]] = []
    for w in words or []:
        txt = str(w.get("word") or "")
        chars = [c for c in txt if c and c not in _PUNCT]
        if not chars:
            continue
        s = float(w.get("start_sec", 0.0))
        e = float(w.get("end_sec", s))
        if e < s:
            e = s
        step = (e - s) / float(len(chars)) if len(chars) > 1 else (e - s)
        for i, ch in enumerate(chars):
            cs = s + step * i
            ce = s + step * (i + 1) if len(chars) > 1 else e
            out.append({"ch": ch, "start_sec": round(cs, 4), "end_sec": round(ce, 4), "src": w})
    return out


def mark_repeated_takes(toks: List[Dict[str, Any]], max_gap_sec: float = 1.6) -> set:
    """标出「同一句话说了两遍」里**前一遍**的那些字。

    现场最常见的绊嘴是「厨房啦几多嘅厨房」：同一段话连着说两遍，前一遍是废的。
    直接按字面找「紧邻重复的 2~4 字片段」，把前一遍标掉，对齐时就不会去配它
    （DP 会把它当非成稿内容跳过去），字幕也就不会印出「厨房嘅厨房」。
    """
    n = len(toks)
    dup: set = set()
    for size in (4, 3, 2):
        for i in range(n - size):
            a = "".join(str(t["ch"]) for t in toks[i : i + size])
            if not a.strip():
                continue
            # 同一串字在附近（中间最多隔 size+4 个字、时间不超过 max_gap_sec）又出现一次，
            # 就把**前一遍**标掉——那是绊嘴，不是内容
            for j in range(i + 1, min(n - size + 1, i + size + 5)):
                b = "".join(str(t["ch"]) for t in toks[j : j + size])
                if a != b:
                    continue
                gap = float(toks[j]["start_sec"]) - float(toks[i + size - 1]["end_sec"])
                if gap > max_gap_sec:
                    continue
                dup.update(range(i, i + size))
                break
    return dup


def align_text_to_words(
    text: str,
    words: Sequence[Dict[str, Any]],
    dedupe_repeats: bool = False,
) -> Dict[str, Any]:
    """字符级 DP 对齐。返回 `{"pairs": [(text_idx, tok_idx)], "coverage": float, "n_text": int}`。

    coverage = 被匹配上的成稿字数 / 成稿总字数。低覆盖说明成稿被改写过，调用方要当心。
    """
    t_chars = strip_punct(text)
    toks = char_tokens(words)
    # 只在「成稿对不上」（覆盖率低）时才去认绊嘴：高覆盖率的行重复词通常是真内容
    # （「有啲灯笼、有啲造型」），误标会把好端端的话切碎（2026-11-24 实测栽过）
    dup_toks = mark_repeated_takes(toks) if dedupe_repeats else set()
    n, m = len(t_chars), len(toks)
    if n == 0 or m == 0:
        return {"pairs": [], "coverage": 0.0, "n_text": n}

    # dp[i][j]：成稿前 i 字 与 逐字稿前 j 字 的最小代价
    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    back: List[List[str]] = [[""] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            cur = dp[i][j]
            if cur == INF:
                continue
            if i < n and j < m and j not in dup_toks:
                c = _same_kind(t_chars[i], toks[j]["ch"])
                if c is not None and cur + c < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = cur + c
                    back[i + 1][j + 1] = "pair"
            if j < m and cur + COST_SKIP_AUDIO < dp[i][j + 1]:
                dp[i][j + 1] = cur + COST_SKIP_AUDIO
                back[i][j + 1] = "skip_audio"
            if i < n and cur + COST_MISS < dp[i + 1][j]:
                dp[i + 1][j] = cur + COST_MISS
                back[i + 1][j] = "skip_text"

    # 回溯
    pairs: List[Tuple[int, int]] = []
    i, j = n, m
    while i > 0 or j > 0:
        step = back[i][j]
        if step == "pair":
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif step == "skip_audio":
            j -= 1
        elif step == "skip_text":
            i -= 1
        else:  # 走到头（理论上不会发生）
            if i > 0:
                i -= 1
            else:
                j -= 1
    pairs.reverse()
    coverage = (len(pairs) / float(n)) if n else 0.0
    return {"pairs": pairs, "coverage": coverage, "n_text": n}


def keep_intervals(
    text: str,
    words: Sequence[Dict[str, Any]],
    lead_sec: float = LEAD_SEC,
    tail_sec: float = TAIL_SEC,
    min_run_sec: float = MIN_RUN_SEC,
    long_cut_sec: float = LONG_CUT_SEC,
) -> Dict[str, Any]:
    """把「只保留成稿里那些字」算成具体的时间区间。

    做法是先把匹配到的字按「中间有没有夹着成稿之外的话」切成若干连续跑段，
    再决定哪些刀真的下：

    - 夹在中间的非成稿语音 < `SKIP_SPEECH_SEC` —— 是口癖与语气词，留着他本来就在喘气，切了像卡带；
    - 一刀下去会剩下 < `MIN_RUN_SEC` 的碎段 —— 不切（中间碎段）或整段丢掉（段首/段尾的废开头）；
    - 夹着的是一整句重来的话（>= `LONG_CUT_SEC`）—— 一律切掉，这才是「混进来的别人的声音」与废话重拍。

    返回 `{"intervals": [...], "coverage", "dropped", "cut_notes", "head_trim_sec", "tail_trim_sec", "ok", "low_coverage", "reason"}`
    """
    al = align_text_to_words(text, words)
    if al["coverage"] < MIN_COVERAGE:
        al2 = align_text_to_words(text, words, dedupe_repeats=True)
        if al2["coverage"] >= al["coverage"]:
            al = al2
    pairs = al["pairs"]
    t_chars = strip_punct(text)
    toks = char_tokens(words)
    if not pairs:
        return {
            "intervals": [], "coverage": 0.0, "dropped": [], "cut_notes": [],
            "head_trim_sec": 0.0, "tail_trim_sec": 0.0,
            "ok": False, "low_coverage": True,
            "reason": "成稿的字一个都没对上逐字稿",
        }

    cov_raw = al["coverage"]
    # 成稿覆盖率太低 = 这一行是被压缩改写过的，句中不敢乱切，只砍段首段尾
    allow_inner_cut = cov_raw >= MIN_COVERAGE

    # ── 1. 相邻匹配字之间夹着的语音 ──
    # 注意：`runs[k]` 与 `runs[k+1]` 之间的 gap 才是 k，不能拿「第几个字对」当下标，
    # 因为连着好几对字可能都落在同一个跑段里（2026-11-24 第一版就是这样错位的）。
    runs: List[List[Tuple[int, int]]] = [[pairs[0]]]
    run_gaps: List[Dict[str, Any]] = []
    acc: List[Dict[str, Any]] = []
    for (ti_prev, tj_prev), (ti, tj) in zip(pairs, pairs[1:]):
        acc += [toks[k] for k in range(tj_prev + 1, tj)]
        span = sum(max(0.0, float(t["end_sec"]) - float(t["start_sec"])) for t in acc)
        if allow_inner_cut and span > SKIP_SPEECH_SEC:
            run_gaps.append({
                "speech": "".join(str(t["ch"]) for t in acc),
                "span_sec": round(span, 3),
                "start": round(float(acc[0]["start_sec"]), 3),
                "end": round(float(acc[-1]["end_sec"]), 3),
            })
            runs.append([(ti, tj)])
            acc = []
        else:
            runs[-1].append((ti, tj))

    # ── 2. 每段跑的首尾时间（取字级时间，不含垫边）──
    def _run_span(g: List[Tuple[int, int]]) -> Tuple[float, float]:
        a = float(toks[g[0][1]]["start_sec"])
        b = float(toks[g[-1][1]]["end_sec"])
        return a, b

    keep: List[int] = list(range(len(runs)))
    cut_notes: List[Dict[str, Any]] = []
    for k in range(len(runs) - 1):
        a, b = _run_span(runs[k])
        c, d = _run_span(runs[k + 1])
        gap = run_gaps[k]
        if gap["span_sec"] <= SKIP_SPEECH_SEC:
            continue  # 太小，算不上别人的话
        # 只有「一刀下去两边都还剩一整口气」才真下刀；
        # 会在段首/段尾留下 0.3 秒碎段的，不切——那点碎段是成稿里的字，宁可留着他那半句。
        left_dur, right_dur = b - a, d - c
        if left_dur >= min_run_sec and right_dur >= min_run_sec:
            cut_notes.append(dict(gap, action="cut"))
        else:
            cut_notes.append(dict(gap, action="keep_stumble"))

    # ── 3. 落成区间 ──
    intervals: List[Dict[str, Any]] = []
    for idx in keep:
        g = runs[idx]
        raw_start, raw_end = _run_span(g)
        intervals.append({
            "start": round(max(0.0, raw_start - lead_sec), 4),
            "end": round(raw_end + tail_sec, 4),
            # 文字取「本段第一个对上字 ~ 最后一个对上字」之间的成稿原文，
            # 不是只取对上的那些字：ASR 把「天」听成「轩」时，「四大天王」不能被印成「四大王」。
            "text": "".join(t_chars[g[0][0] : g[-1][0] + 1]),
            "words": _words_in_span(words, raw_start, raw_end),
        })

    # 相邻区间挨得极近（或因为垫边而重叠）就并成一段：那是同一口气，不该断
    merged_iv: List[Dict[str, Any]] = []
    for iv in intervals:
        if merged_iv and iv["start"] - merged_iv[-1]["end"] <= MERGE_GAP_SEC:
            prev_ = merged_iv[-1]
            prev_["end"] = round(max(prev_["end"], iv["end"]), 4)
            prev_["text"] = prev_["text"] + iv["text"]
            prev_["words"] = list(prev_["words"]) + list(iv["words"])
        else:
            merged_iv.append(dict(iv))
    intervals = merged_iv

    dropped: List[Dict[str, Any]] = []
    for a_iv, b_iv in zip(intervals, intervals[1:]):
        if b_iv["start"] > a_iv["end"] + 1e-6:
            dropped.append({
                "start": round(a_iv["end"], 4),
                "end": round(b_iv["start"], 4),
                "reason": "非成稿内容（口令/重来/插话/口癖）",
            })

    # 覆盖率低到「根本没对上」时不许凭 1 个字就开切：直接交空结果，调用方退回整段保留
    if cov_raw < MIN_OK_COVERAGE:
        intervals = []

    head_cut = round(max(0.0, _run_span(runs[keep[0]])[0] - float(toks[0]["start_sec"])), 3)
    tail_cut = round(max(0.0, float(toks[-1]["end_sec"]) - _run_span(runs[keep[-1]])[1]), 3)
    return {
        "intervals": intervals,
        "coverage": round(cov_raw, 4),
        "dropped": dropped,
        "cut_notes": cut_notes,
        "head_trim_sec": head_cut,
        "tail_trim_sec": tail_cut,
        "ok": bool(intervals) and cov_raw >= MIN_OK_COVERAGE,
        "low_coverage": cov_raw < MIN_COVERAGE,
        "reason": "" if cov_raw >= MIN_COVERAGE else f"成稿只对上 {cov_raw:.0%}（压缩改写过的行），只砍段首段尾",
    }


def _words_in_span(words: Sequence[Dict[str, Any]], start: float, end: float) -> List[Dict[str, Any]]:
    """取落在 [start,end] 里的原始词对象（保留 is_keyword / energy 等字段）。"""
    out: List[Dict[str, Any]] = []
    for w in words or []:
        ws = float(w.get("start_sec", 0.0))
        we = float(w.get("end_sec", ws))
        if we <= start + 1e-6 or ws >= end - 1e-6:
            continue
        out.append(dict(w))
    return out
