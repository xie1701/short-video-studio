#!/usr/bin/env python3
"""24 套风格 → 音效 / 花字的「出现条件」真值（只读取证草稿，纯反推）。

要回答的问题（用户原话）
────────────────────────
「这一版的音效过于密集，感觉是没有任何缘由就出现……应该好好抽象下这些音效、花字之类的是要在
什么情况下才会出现，调用的原则是怎样，作用域又是怎样，而不是无脑的硬植入。24 个风格模版也都要反思。」

做法
────
对每套风格的源草稿，把**每一条音效 cue** 和**每一条花字 cue** 拿去找锚点：
  · 主轨切点（`style_edit_grammar.json` 的 `main_segments`）
  · 花字出现 / 退出点（`draft_content.json` 里非字幕文字轨的段起止）
  · 字幕换行点（`text_beats.entries`）
  · BGM 节拍网格（源草稿 beats 材料不带时间，从本风格 BGM 音频自相关估 BPM 再铺网格）
算它到每类锚点的偏移，取最近的那个作为「它凭什么响」。都挂不上就是 `free`。

然后按触发类型 × 角色交叉统计，落出：
  · 这套风格的音效在什么情况下才会响（`trigger_mix` + `allowed_triggers`）
  · 最短间隔 / 每分钟上限 / 每个角色的每分钟上限（`min_gap_sec` / `max_per_min` / `roles`）
  · 作用域（允许哪些镜头类型、静音段能不能响、口播中段能不能响）
  · 证据（样本量、命中率、偏移分布）

⚠ 不许凭空补值：量不到的字段写 null，并在 `unmeasured` 里写清原因（例如本风格无 BGM 就没有节拍网格）。

用法：
    python3 dev_tools/build_trigger_rules_sfx_flower.py            # 全 24 套
    python3 dev_tools/build_trigger_rules_sfx_flower.py --style style_21 --print
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as stats
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
ASSETS = ROOT / "short-video-studio" / "assets"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from flower_policy import NUM_MEASURE_RE  # noqa: E402

from trigger_policy_common import (  # noqa: E402
    TOL_BEAT_SEC,
    TOL_CUT_SEC,
    TOL_FLOWER_SEC,
    TOL_LINE_SEC,
    beat_grid,
    classify_sfx_anchor,
    estimate_bpm,
    silence_windows,
    sorted_float,
)

FORENSIC = ROOT / "forensic"
STYLE_SOURCES = FORENSIC / "style_sources.json"
GRAMMAR = FORENSIC / "style_edit_grammar.json"
SFX_TRUTH_DIR = ASSETS / "sound_vibe" / "sfx_truth"
BGM_DIR = ASSETS / "sound_vibe" / "bgm"
LIBRARY = ASSETS / "style_spectrum" / "style_spectrum_library.json"
OUT_FORENSIC = FORENSIC / "style_trigger_rules_sfx_flower.json"
OUT_ASSET = ASSETS / "style_spectrum" / "style_trigger_rules_sfx_flower.json"

US = 1_000_000.0
# pipeline 能执行的触发类型（文字面板/逐字碎片要做版式渲染，当前 pipeline 不产出，故不冒用）
EXECUTABLE_FLOWER_TRIGGERS = ("number", "chapter", "keyword", "line_echo", "cut")
SECTION_MARKERS = ("一、", "二、", "三、", "四、", "五、", "六、", "1.", "2.", "3.", "4.", "5.",
                   "①", "②", "③", "第一步", "第二步", "第一点", "第二点",
                   "第一", "第二", "第三", "第四", "第五", "首先", "其次", "再次", "最后", "另外")


def read_json(p: Path) -> Dict[str, Any]:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def load_draft(draft_name: str) -> Optional[Dict[str, Any]]:
    """明文草稿：常规是 draft_content.json，style_01 只有解密后的 __latest_close.json。"""
    for fn in ("draft_content.json", "__latest_close.json"):
        p = FORENSIC / "decrypted" / draft_name / fn
        if p.is_file():
            try:
                return read_json(p)
            except Exception:
                continue
    return None


def seg_start(seg: Dict[str, Any]) -> float:
    return round(float((seg.get("target_timerange") or {}).get("start", 0)) / US, 3)


def seg_dur(seg: Dict[str, Any]) -> float:
    return round(float((seg.get("target_timerange") or {}).get("duration", 0)) / US, 3)


def seg_text(draft: Dict[str, Any], seg: Dict[str, Any]) -> str:
    mid = seg.get("material_id")
    for m in (draft.get("materials") or {}).get("texts") or []:
        if m.get("id") == mid:
            try:
                c = json.loads(m.get("content") or "{}")
            except Exception:
                return ""
            return str(c.get("text") or "").strip()
    return ""


def text_tracks(draft: Dict[str, Any]) -> List[Tuple[int, Dict[str, Any]]]:
    return [(i, t) for i, t in enumerate(draft.get("tracks") or []) if t.get("type") == "text"]


def flower_segments(draft: Dict[str, Any], sub_track_index: Optional[int]) -> List[Dict[str, Any]]:
    """花字 = 非字幕文字轨上的所有文字段（字幕轨由 grammar 的 subtitle_track_index 指定）。"""
    out: List[Dict[str, Any]] = []
    for i, tr in text_tracks(draft):
        if sub_track_index is not None and i == sub_track_index:
            continue
        for seg in tr.get("segments") or []:
            txt = seg_text(draft, seg)
            y = None
            try:
                y = float(((seg.get("clip") or {}).get("transform") or {}).get("y"))
            except (TypeError, ValueError):
                y = None
            fs = None
            mid = seg.get("material_id")
            for m in (draft.get("materials") or {}).get("texts") or []:
                if m.get("id") == mid:
                    try:
                        fs = float(((m.get("style") or {}).get("size")))
                    except (TypeError, ValueError):
                        fs = None
                    break
            out.append({
                "track": i,
                "start": seg_start(seg),
                "dur": seg_dur(seg),
                "end": round(seg_start(seg) + seg_dur(seg), 3),
                "text": txt,
                "y": y,
                "font_size": fs,
            })
    out.sort(key=lambda x: (x["start"], x["track"]))
    return out


def merged_spans(segs: Sequence[Dict[str, Any]], gap: float = 0.08) -> List[Tuple[float, float]]:
    """同一时刻同一条文案可能被拆成多条轨道（描边层/阴影层），合并成"花字出现"的时段。"""
    spans: List[List[float]] = []
    for s in segs:
        a, b = float(s["start"]), float(s["end"])
        if spans and a - spans[-1][1] <= gap:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in spans]


def num_measure_text(s: str) -> bool:
    """数字触发判据与运行期共用同一份正则（`flower_policy.NUM_MEASURE_RE`），避免两处漂移。"""
    from flower_policy import NUM_MEASURE_RE

    return bool(NUM_MEASURE_RE.search(s or ""))


def zone_of_y(y: Optional[float]) -> str:
    if y is None:
        return "unknown"
    if y >= 0.30:
        return "top"
    if y <= -0.30:
        return "bottom"
    return "middle"


def shot_types_from_share(broll_share: Optional[float]) -> List[str]:
    """源草稿里音效/花字压在 B-roll 上的占比 → 允许的镜头类型。

    不是「二选一」而是「两都能用但各有侧重」：只要有一成以上落在 B-roll 就允许 B-roll，
    只要有一成以上落在主轨就允许主轨。只有压倒性的一种（<10%）才真禁。
    """
    if broll_share is None:
        return ["a_roll", "b_roll"]
    out = []
    if broll_share >= 0.10:
        out.append("b_roll")
    if (1.0 - broll_share) >= 0.10:
        out.append("a_roll")
    return out or ["a_roll", "b_roll"]


def norm_text(s: str) -> str:
    import re

    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")


def sub_at(t: float, subs: Sequence[Tuple[float, float, str]]) -> str:
    for a, b, txt in subs:
        if a - 0.05 <= t <= b + 0.05:
            return txt
    return ""


def sub_near(t: float, subs: Sequence[Tuple[float, float, str]], pad: float = 2.0) -> str:
    """t 附近（±pad 秒）的字幕文案拼接：花字多半是把前后一句里的词提前/延后喊出来。"""
    return "".join(txt for a, b, txt in subs if a - pad <= t <= b + pad)


def best_line_match(t: float, nt: str, subs: Sequence[Tuple[float, float, str]],
                    pad: float = 3.0) -> Tuple[float, str]:
    """附近（±pad）里匹配得最好的一句：返回 (字面重合率, 那句文案)。

    花字常见三种上屏时机：整句回声、把下一句的关键词提前喊出来、语义改写。
    前两种都能在同期人声里溯到出处，第三种不能——所以这里取"最好的一句"而不是"当前那句"。
    """
    best, best_txt = 0.0, ""
    for a, b, txt in subs:
        if not (a - pad <= t <= b + pad):
            continue
        nt_line = norm_text(txt)
        if not nt_line:
            continue
        if nt == nt_line:
            return 1.0, txt
        ov = char_overlap(nt, nt_line)
        if nt in nt_line or (len(nt_line) >= 2 and nt_line in nt):
            ov = max(ov, 0.95)
        if ov > best:
            best, best_txt = ov, txt
    return best, best_txt


def char_overlap(a: str, b: str) -> float:
    """花字文案有多少字能在同期人声里找到出处（0~1）。用于"文案必须有出处"的判据。"""
    sa, sb = set(a or ""), set(b or "")
    if not sa:
        return 0.0
    return len(sa & sb) / float(len(sa))


def _span_layer_count(t: float, flowers: Sequence[Dict[str, Any]], tol: float = 0.12) -> int:
    """同一时刻同时上屏的文字层数（描边层/阴影层/多行面板都会叠层）。"""
    texts = set()
    for f in flowers:
        if abs(float(f["start"]) - t) <= tol:
            nt = norm_text(f.get("text") or "")
            if nt:
                texts.add(nt)
    return len(texts)


def shot_len_of(t: float, main_segs: Sequence[Dict[str, Any]], dur: float) -> Optional[float]:
    """t 所在主轨镜头的时长。"""
    if not main_segs:
        return None
    best = None
    for i, m in enumerate(main_segs):
        a = float(m.get("start", 0.0))
        b = float(main_segs[i + 1]["start"]) if i + 1 < len(main_segs) else float(dur)
        if a - 1e-6 <= t < b + 1e-6:
            best = round(b - a, 3)
            break
    if best is None and t >= float(main_segs[-1].get("start", 0.0)):
        best = round(float(dur) - float(main_segs[-1].get("start", 0.0)), 3)
    return best


def pct(vals: Sequence[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return round(s[0], 3)
    k = (len(s) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return round(s[lo], 3)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


# ── 单套分析 ──────────────────────────────────────────────────────────────────

def analyze_style(style_id: str, draft_name: str, grammar: Dict[str, Any],
                  lib_entry: Dict[str, Any]) -> Dict[str, Any]:
    draft = load_draft(draft_name)
    dur = float(grammar.get("duration_sec") or 0.0)
    unmeasured: List[str] = []
    if draft is None:
        return {"style_id": style_id, "error": "draft_not_found", "source_draft": draft_name}

    # ── 锚点 1：主轨切点 ──
    main_segs = grammar.get("main_segments") or []
    cut_times = sorted_float([m.get("start") for m in main_segs])
    shot_len_by_start = {}
    for i, m in enumerate(main_segs):
        nxt = main_segs[i + 1]["start"] if i + 1 < len(main_segs) else dur
        shot_len_by_start[round(float(m["start"]), 3)] = round(float(nxt) - float(m["start"]), 3)

    # ── 锚点 2：字幕换行（时间取 grammar.text_beats，文案取字幕轨自身，grammar 不带文案）──
    tb = grammar.get("text_beats") or {}
    line_starts = sorted_float([e.get("t") for e in (tb.get("entries") or [])])
    sti = tb.get("subtitle_track_index")
    subs: List[Tuple[float, float, str]] = []
    for i, tr in text_tracks(draft):
        if sti is not None and i != sti:
            continue
        for seg in tr.get("segments") or []:
            a = seg_start(seg)
            subs.append((a, round(a + seg_dur(seg), 3), seg_text(draft, seg)))
    subs.sort()
    if not line_starts:
        line_starts = sorted_float([s[0] for s in subs])

    # ── 锚点 3：花字出现 / 退出（逐段取，保留被叠加字幕层掩盖的真实起止点）──
    flowers = flower_segments(draft, sti)
    flower_in = sorted_float({s["start"] for s in flowers})
    flower_out = sorted_float({s["end"] for s in flowers})
    fspans = merged_spans(flowers)

    # ── 锚点 4：BGM 节拍网格 ──
    bgm_node = {}
    try:
        mf = read_json(ASSETS / "sound_vibe" / "vibe_sound_manifest.json")
        bgm_node = (mf.get("styles") or {}).get(style_id) or {}
    except Exception:
        bgm_node = {}
    bgm_rel = bgm_node.get("bgm_wav")
    bpm_info: Dict[str, Any] = {"bpm": None, "confidence": 0.0, "source": "none"}
    if bgm_rel and (ASSETS / bgm_rel).is_file() and not bgm_node.get("bgm_is_silence_placeholder"):
        bpm_info = estimate_bpm(ASSETS / bgm_rel)
    else:
        unmeasured.append("本风格 BGM 缺失/静音占位，无节拍网格")
    beats = beat_grid(bpm_info.get("bpm"), dur) if bpm_info.get("bpm") else []

    anchors = {"cut": cut_times, "flower": flower_in, "flower_exit": flower_out,
               "line": line_starts, "beat": beats}

    # ── 音效 cue 归属 ──
    sfx_truth = {}
    tp = SFX_TRUTH_DIR / f"{style_id}.json"
    if tp.is_file():
        sfx_truth = read_json(tp)
    cues = sfx_truth.get("cues") or []
    silences = silence_windows([{"start_sec": a, "end_sec": b} for a, b, _ in subs], dur)
    cue_rows: List[Dict[str, Any]] = []
    for c in cues:
        t = float(c.get("time_sec") or 0.0)
        cls = classify_sfx_anchor(t, {k: v for k, v in anchors.items() if k != "flower_exit"},
                                  {"cut": TOL_CUT_SEC, "flower": TOL_FLOWER_SEC,
                                   "line": TOL_LINE_SEC, "beat": TOL_BEAT_SEC})
        role = str(c.get("role") or "misc")
        in_sil = any(a <= t <= b for a, b in silences)
        cut_len = shot_len_by_start.get(round(cls["anchor_time"], 3)) if cls["reason"] == "cut" and cls["anchor_time"] is not None else None
        cue_rows.append({
            "time_sec": round(t, 3),
            "role": role,
            "name": c.get("name"),
            "reason": cls["reason"],
            "anchor_time": cls["anchor_time"],
            "offset_sec": cls["offset_sec"],
            "in_silence": in_sil,
            "inside_line": bool(sub_at(t, subs)),
            "shot_len_at_cut": cut_len,
        })

    n = len(cue_rows)
    counts = {k: 0 for k in ("cut", "flower", "line", "beat", "free")}
    for r in cue_rows:
        counts[r["reason"] or "free"] += 1
    hit = n - counts["free"]

    # 偏移分布（只统计挂上的）
    offs = [r["offset_sec"] for r in cue_rows if r["offset_sec"] is not None and r["reason"]]
    times = [r["time_sec"] for r in cue_rows]
    gaps = [round(times[i + 1] - times[i], 3) for i in range(len(times) - 1)]

    # 角色 × 触发
    roles: Dict[str, Any] = {}
    for r in cue_rows:
        node = roles.setdefault(r["role"], {"n": 0, "triggers": {}, "offsets": []})
        node["n"] += 1
        node["triggers"][r["reason"] or "free"] = node["triggers"].get(r["reason"] or "free", 0) + 1
        if r["offset_sec"] is not None:
            node["offsets"].append(r["offset_sec"])
    for rname, node in roles.items():
        trs = node["triggers"]
        offs_role = node.pop("offsets")
        node["primary_trigger"] = max(trs.items(), key=lambda kv: kv[1])[0]
        node["offset_median_sec"] = round(stats.median(offs_role), 3) if offs_role else None
        seq = [row["time_sec"] for row in cue_rows if row["role"] == rname]
        rgaps = [round(seq[i + 1] - seq[i], 3) for i in range(len(seq) - 1)]
        node["min_gap_sec"] = min(rgaps) if rgaps else None
        node["gap_median_sec"] = round(stats.median(rgaps), 3) if rgaps else None
        node["per_min"] = round(len(seq) / dur * 60.0, 2) if dur > 0 else None

    # 角色 ← 触发 的主映射（每个触发类型最常挂在哪个角色上）
    trig_role: Dict[str, Dict[str, int]] = {}
    for r in cue_rows:
        if not r["reason"]:
            continue
        d = trig_role.setdefault(r["reason"], {})
        d[r["role"]] = d.get(r["role"], 0) + 1
    role_for_trigger = {k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in trig_role.items()}

    # 作用域：镜头类型（主轨 = A-roll，叠加视频轨 = B-roll）
    def on_broll(t: float) -> bool:
        for ov in grammar.get("overlay_segments") or []:
            a = float(ov.get("start", 0.0))
            b = float(ov.get("end", a + float(ov.get("dur", 0.0))))
            if a - 1e-6 <= t < b + 1e-6:
                return True
        return False

    broll_share = round(sum(1 for r in cue_rows if on_broll(r["time_sec"])) / n, 3) if n else None
    silence_share = round(sum(1 for r in cue_rows if r["in_silence"]) / n, 3) if n else None

    # 结构性切点证据：命中 cut 的 cue，其所在镜头时长 vs 全部镜头时长
    cut_shot_lens = [r["shot_len_at_cut"] for r in cue_rows if r["reason"] == "cut" and r["shot_len_at_cut"]]
    all_shot_lens = list(shot_len_by_start.values())

    allowed = [k for k in ("cut", "flower", "line", "beat")
               if counts[k] > 0 and counts[k] / max(1, hit) >= 0.05]
    if not allowed and hit:
        allowed = [max(((k, counts[k]) for k in ("cut", "flower", "line", "beat")), key=lambda kv: kv[1])[0]]

    sfx_rules = {
        "n_cues": n,
        "per_min": round(n / dur * 60.0, 1) if dur > 0 else None,
        "trigger_mix": counts,
        "trigger_share": {k: round(v / n, 3) for k, v in counts.items()} if n else {},
        "tol_sec": TOL_CUT_SEC,
        "allowed_triggers": allowed,
        "max_per_min": int(math.ceil((n / dur * 60.0) if dur > 0 else 0)),
        "max_per_sec": round(n / dur, 3) if dur > 0 else None,
        "max_free_per_min": round(counts["free"] / dur * 60.0, 2) if dur > 0 else 0.0,
        "min_gap_sec": min(gaps) if gaps else None,
        "gap_median_sec": round(stats.median(gaps), 3) if gaps else None,
        "gap_p90_sec": pct(gaps, 0.9),
        "max_per_min_by_role": {k: (v.get("per_min") or 0.0) for k, v in roles.items()},
        "roles": roles,
        "role_for_trigger": role_for_trigger,
        "scope": {
            "allowed_shot_types": shot_types_from_share(broll_share),
            "broll_share": broll_share,
            "silence_ban": not (silence_share is not None and silence_share >= 0.15),
            "silence_share": silence_share,
            "midline_ban": True,
            "structural_cut_only": True,
            "cut_shot_len_p25_sec": pct(cut_shot_lens, 0.25),
            "all_shot_len_p25_sec": pct(all_shot_lens, 0.25),
        },
        "evidence": {
            "n_cues": n,
            "attributable": hit,
            "hit_rate": round(hit / n, 3) if n else None,
            "offset_median_sec": round(stats.median(offs), 3) if offs else None,
            "offset_p90_sec": pct(offs, 0.9),
            "role_trigger_crosstab": trig_role,
        },
    }

    # ── 花字归属 ──
    median_shot = stats.median(all_shot_lens) if all_shot_lens else 0.0
    fl_rows: List[Dict[str, Any]] = []
    for s in flowers:
        t = float(s["start"])
        txt = s.get("text") or ""
        nt = norm_text(txt)
        line = sub_at(t, subs)
        nl = norm_text(line)
        d_cut, _ = _nearest(t, cut_times)
        d_line, _ = _nearest(t, line_starts)
        shot_len = shot_len_of(t, main_segs, dur)
        marker = any(txt.strip().startswith(mk) for mk in SECTION_MARKERS)
        # 章节转折也可能由"人声里的条目播报"引起（"第一/第二/一、/第一步"），花字跟着这条条目上屏
        narr_marker = any(mk in sub_near(t, subs, pad=2.0) for mk in SECTION_MARKERS)
        # 章节转折：标题式序号，或者本镜明显长于中位镜长（≥1.8×）= 段落落地后的"标题镜"
        long_turn = bool(shot_len and median_shot and shot_len >= 1.8 * median_shot)
        near = norm_text(sub_near(t, subs, pad=2.5))
        overlap, match_line = best_line_match(t, nt, subs, pad=3.0) if nt else (0.0, "")
        if not nl:
            nl = norm_text(match_line)
        layers = _span_layer_count(t, flowers)
        if not nt:
            reason = "free"
        elif num_measure_text(txt) and not marker:
            reason = "number"
        elif marker or narr_marker:
            reason = "chapter"
        elif nl and nt == nl:
            reason = "line_echo"
        elif nt and len(nt) >= 1 and (nt in nl or (len(nl) >= 2 and nl in nt)):
            reason = "keyword"
        elif nt and len(nt) >= 2 and overlap >= 0.6:
            reason = "keyword"
        elif long_turn:
            reason = "chapter"
        elif len(nt) <= 1:
            reason = "fragment"
        elif layers >= 3:
            reason = "card"
        elif d_cut is not None and d_cut <= TOL_CUT_SEC:
            reason = "cut"
        else:
            reason = "free"
        fl_rows.append({
            "start": t, "end": s["end"], "dur": round(float(s["end"]) - t, 3), "text": txt,
            "chars": len(nt), "reason": reason, "line": line,
            "offset_cut_sec": d_cut, "offset_line_sec": d_line, "overlap": round(overlap, 3),
            "match_line": match_line,
            "layers": layers,
            "zone": zone_of_y(s.get("y")), "y": s.get("y"),
            "shot_len": shot_len, "on_broll": on_broll(t),
            "section_marker": marker, "narr_marker": narr_marker, "long_turn": long_turn,
        })

    used = [r for r in fl_rows if r["chars"] > 0]
    fn = len(used)
    FKEYS = ("number", "chapter", "keyword", "line_echo", "cut", "card", "fragment", "free")
    fcounts = {k: 0 for k in FKEYS}
    for r in used:
        fcounts[r["reason"]] += 1
    fhit = fn - fcounts["free"]
    # 密度以「合并后的视觉块」为准：同一时刻叠三层的描边/阴影算一次上屏。
    # 但有些风格是"常驻标签"（整片一条文字轨不换），合并后只剩 1 块 —— 那种要用
    # 「文案更换次数/分」的一半来估，否则会得出 2 次/分这种荒谬的上限。
    fspans_used = [(a, b) for a, b in fspans if b - a > 0.2]
    n_visuals = len(fspans_used)
    vstarts = [a for a, _ in fspans_used]
    fgaps = [round(vstarts[i + 1] - vstarts[i], 3) for i in range(len(vstarts) - 1)]
    _ppm_seg = round(fn / dur * 60.0, 1) if dur > 0 else 0.0
    _ppm_vis = round(n_visuals / dur * 60.0, 1) if dur > 0 else 0.0
    if n_visuals >= 2:
        f_ppm = float(_ppm_vis)
    elif fn >= 2:
        f_ppm = round(_ppm_seg / 2.0, 1)
    else:
        f_ppm = float(_ppm_vis)
    fchars = [r["chars"] for r in used]
    zones: Dict[str, int] = {}
    for r in used:
        zones[r["zone"]] = zones.get(r["zone"], 0) + 1
    fbroll = round(sum(1 for r in used if r["on_broll"]) / fn, 3) if fn else None
    f_allowed = [k for k in EXECUTABLE_FLOWER_TRIGGERS if fcounts[k] > 0]
    if not f_allowed and fn:
        f_allowed = [max(((k, fcounts[k]) for k in EXECUTABLE_FLOWER_TRIGGERS), key=lambda kv: kv[1])[0]]
    unexecutable = fcounts["card"] + fcounts["fragment"] + fcounts["free"]

    # 停留时长：优先用"文案块"的真实停留（多张叠层的风格也按一次上屏算），
    # 常驻标签那种（整条拉满）退回单条文字层的时长分位
    if n_visuals >= 2:
        _spans_dur = [round(b - a, 3) for a, b in fspans_used]
    else:
        _spans_dur = [r["dur"] for r in used] or [1.5]
    _hold = (pct(_spans_dur, 0.2) or 1.5, pct(_spans_dur, 0.8) or 3.0,
             round(stats.median(_spans_dur), 2))

    mzf = lib_entry.get("multi_zone_flower_spec") or {}
    dts = lib_entry.get("display_text_spec") or {}

    flower_rules = {
        "n_cues": fn,
        "n_visuals": n_visuals,
        "per_min": f_ppm,
        "per_min_segments": _ppm_seg,
        "trigger_mix": fcounts,
        "trigger_share": {k: round(v / fn, 3) for k, v in fcounts.items()} if fn else {},
        "allowed_triggers": f_allowed,
        # 数字是内容自证触发：这句人声真说了数字，压上屏不需要风格许可（仍受密度/字数/作用域约束）
        "always_allowed_triggers": ["number"],
        "max_per_min": max(1, min(24, int(math.ceil(f_ppm or 0.0)))),
        "max_per_min_note": ("整片一条常驻文字轨，按文案更换率的一半估" if n_visuals < 2 and fn >= 2 else ""),
        "fallback_hold_sec": [round(min(5.0, max(1.0, pct([r["dur"] for r in used], 0.2) or 1.5)), 2),
                              round(min(5.0, max(1.2, pct([r["dur"] for r in used], 0.8) or 3.0)), 2)],
        "min_gap_sec": min(fgaps) if fgaps else round(60.0 / max(2.0, f_ppm) * 0.5, 2),
        "gap_median_sec": round(stats.median(fgaps), 3) if fgaps else None,
        "chars_min": min(fchars) if fchars else None,
        "chars_max": max(fchars) if fchars else None,
        "chars_median": round(stats.median(fchars), 1) if fchars else None,
        "chars_mean": round(sum(fchars) / len(fchars), 1) if fchars else None,
        "hold_sec_min": round(min(6.0, max(1.0, _hold[0])), 2) if used else None,
        "hold_sec_max": round(min(6.0, max(1.2, _hold[1])), 2) if used else None,
        "hold_sec_median": round(min(6.0, max(1.2, _hold[2])), 2) if used else None,
        "layout_mix": zones,
        "colors": {k: mzf.get(k) for k in ("center_burst_color", "flank_color", "stamp_color",
                                           "end_card_color", "stroke_color") if mzf.get(k)},
        "fonts": dts.get("fonts") or [],
        "anims_top": dts.get("anims_top") or [],
        "scope": {
            "allowed_shot_types": shot_types_from_share(fbroll),
            "broll_share": fbroll,
            "zones": [z for z, c in sorted(zones.items(), key=lambda kv: -kv[1]) if z != "unknown"][:2],
        },
        "evidence": {
            "n_cues": fn,
            "n_visuals": n_visuals,
            "attributable": fhit,
            "unexecutable_share": round(unexecutable / fn, 3) if fn else None,
            "unexecutable_kinds": "card=文字面板/清单, fragment=逐字弹字的单字碎片, free=既无出处也不贴锚点",
            "note": ("本套花字多为编辑自行改写/总结，或与字幕轨不同源，字面不可溯；pipeline 只产出可溯源的"
                     "花字，故不复制这部分" if (fn and fhit / fn < 0.6) else ""),
            "hit_rate": round(fhit / fn, 3) if fn else None,
            "number_share": round(fcounts["number"] / max(1, fn), 3),
            "chapter_share": round(fcounts["chapter"] / max(1, fn), 3),
            "card_share": round(fcounts["card"] / max(1, fn), 3),
            "fragment_share": round(fcounts["fragment"] / max(1, fn), 3),
            "keyword_share": round(fcounts["keyword"] / max(1, fn), 3),
            "line_echo_share": round(fcounts["line_echo"] / max(1, fn), 3),
            "cut_share": round(fcounts["cut"] / max(1, fn), 3),
            "free_share": round(fcounts["free"] / max(1, fn), 3),
        },
    }

    return {
        "style_id": style_id,
        "source_ref": grammar.get("source_ref"),
        "source_draft": draft_name,
        "duration_sec": dur,
        "beat": {"bpm": bpm_info.get("bpm"), "confidence": bpm_info.get("confidence"),
                 "source": bpm_info.get("source"), "n_beats": len(beats)},
        "anchors": {"n_cuts": len(cut_times), "n_flower_spans": len(fspans),
                    "n_lines": len(line_starts), "n_beats": len(beats)},
        "sfx": sfx_rules,
        "flower": flower_rules,
        "cue_table": cue_rows,
        "flower_table": fl_rows,
        "unmeasured": unmeasured,
    }


def _nearest(t: float, arr: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    if not arr:
        return None, None
    best = min(arr, key=lambda x: abs(x - t))
    return round(abs(best - t), 4), best


def print_style(node: Dict[str, Any]) -> None:
    if node.get("error"):
        print(f"{node['style_id']}: ERROR {node['error']}")
        return
    s = node["sfx"]
    f = node["flower"]
    print(f"\n===== {node['style_id']}  dur={node['duration_sec']:.2f}s  bpm={node['beat'].get('bpm')} "
          f"conf={node['beat'].get('confidence')}")
    print(f"  锚点: {node['anchors']}")
    print(f"  音效 {s['n_cues']} 条 ({s['per_min']}/min)  mix={s['trigger_mix']}  "
          f"hit={s['evidence']['hit_rate']}  min_gap={s['min_gap_sec']}  med={s['gap_median_sec']}")
    print(f"        roles={ {k: (v['n'], v['primary_trigger']) for k, v in s['roles'].items()} }")
    print(f"        blood={s['scope']}  role_for_trigger={s['role_for_trigger']}")
    print(f"  花字 {f['n_cues']} 条 ({f['per_min']}/min)  mix={f['trigger_mix']}  hit={f['evidence']['hit_rate']}  "
          f"chars={f['chars_min']}~{f['chars_max']} med={f['chars_median']}  gap={f['min_gap_sec']}")
    print(f"        zones={f['layout_mix']} colors={list(f['colors'].values())}")
    print(f"        allowed={f['allowed_triggers']} scope={f['scope']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="反推 24 套风格的音效/花字触发条件")
    ap.add_argument("--style", help="只跑一套（调试用）")
    ap.add_argument("--print", action="store_true", help="打印每套结论")
    ap.add_argument("--out", default=str(OUT_FORENSIC))
    ap.add_argument("--no-asset-copy", action="store_true", help="不写技能包内副本")
    args = ap.parse_args()

    style_sources = read_json(STYLE_SOURCES).get("styles") or {}
    grammar = read_json(GRAMMAR).get("styles") or {}
    library = {e["style_id"]: e for e in (read_json(LIBRARY).get("styles") or [])}

    out_styles: Dict[str, Any] = {}
    shipped_styles: Dict[str, Any] = {}
    for sid in sorted(style_sources):
        if args.style and sid != args.style:
            continue
        node = analyze_style(sid, style_sources[sid], grammar.get(sid) or {}, library.get(sid) or {})
        out_styles[sid] = node
        shipped = {k: v for k, v in node.items() if k not in ("cue_table", "flower_table", "source_draft")}
        shipped_styles[sid] = shipped
        if args.print:
            print_style(node)

    payload = {
        "version": 1,
        "generated_at": date.today().isoformat(),
        "what": "24 套风格的音效/花字「出现条件」真值：触发类型配比、最小间隔、密度上限、角色、作用域、证据",
        "method": {
            "anchors": ["主轨切点(main_segments)", "花字出现/退出(非字幕文字轨段)", "字幕换行(text_beats)",
                        "BGM 节拍网格(自相关 BPM 估计)"],
            "tolerance_sec": {"cut": TOL_CUT_SEC, "flower": TOL_FLOWER_SEC, "line": TOL_LINE_SEC,
                              "beat": TOL_BEAT_SEC},
            "reason_priority": ["cut", "flower", "line", "beat"],
            "free_policy": "挂不上任何锚点的 cue 记为 free；free 比例是风格允许的「无理由」额度上限",
        },
        "n_styles": len(out_styles),
        "styles": out_styles,
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[build_trigger_rules] -> {outp} ({len(out_styles)} 套)")
    if not args.no_asset_copy and not args.style:
        # 技能包内副本不带逐条文案/本机目录名（隐私门禁；运行期只需要统计规则）
        shipped_payload = dict(payload)
        shipped_payload["styles"] = shipped_styles
        shipped_payload["note"] = ("本文件是运行期用的规则副本，不含逐条 cue 明细与本机草稿目录名；"
                                  "全量取证（含逐条依据）在仓库 forensic/style_trigger_rules_sfx_flower.json")
        OUT_ASSET.write_text(json.dumps(shipped_payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[build_trigger_rules] -> {OUT_ASSET}")


if __name__ == "__main__":
    main()
