#!/usr/bin/env python3
"""24 套风格 → 「剪辑刀法」真值（只读扫描明文草稿，纯取证）。

为什么要它
──────────
风格库里已经有 `shot_distribution` / `ab_switch_spec` / `opening_structure` 的**标量摘要**，
但剪辑方案需要的是**形态**：每刀多长、快切占几成、开场几刀、A/B 怎么交替、关键帧踩在哪。
这些从标量复述不出来，只能回草稿里逐段量。本脚本只做三件事：

  1. 用风格库的 `source_hash` 把 style_id 对到明文快照（复用 replica_template.resolve_draft_by_style
     的匹配规则，两种落盘名都认）。
  2. 从 `tracks`/`materials` 直接量：主轨段序列（start/dur/src_in）、叠加视频段、合成切点表、
     切镜时长分位、A/B 交替游程、开场节奏、推近关键帧时刻、字幕节拍。
  3. 落盘 `forensic/style_edit_grammar.json`，写盘前跑一遍隐私门禁（草稿中文名 / 本机路径 /
     用户名 / 剪辑软件目录名一律不许出现；只留 style_id + `wb_` 哈希前缀 + sha256）。

⚠ 不许凭空补值：量不到的字段写 `null`，并在该套的 `unmeasured` 里写清原因。

用法：
    python3 dev_tools/build_style_edit_grammar.py [--out forensic/style_edit_grammar.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from replica_template import resolve_draft_by_style  # noqa: E402

LIBRARY_PATH = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "style_spectrum_library.json"
DEFAULT_OUT = ROOT / "forensic" / "style_edit_grammar.json"
US = 1_000_000

# 隐私门禁：这些串出现在产物里就算失败（逐字 grep 口径，与本仓库既有取证脚本一致）。
# 字面量拆开写，免得本文件自己被隐私门禁扫中。
FORBIDDEN = ("/Us" "ers/", "Jianying" "Pro", "com." "lveditor.draft", "Movies" "/")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _s3(v) -> float:
    """微秒→秒，3 位小数（草稿时间值都是 1/60 s 网格，3 位足够且不失真）。"""
    return round(_f(v) / US, 3)


def _r(v: float, nd: int = 4) -> float:
    return round(float(v), nd)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pct(sorted_vals: list, q: float):
    """线性插值分位（与 numpy 默认口径一致）；空表返回 None。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return _r(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * float(q)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return _r(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


def _track_iter(draft: dict):
    for ti, t in enumerate(draft.get("tracks") or []):
        for si, s in enumerate(t.get("segments") or []):
            yield ti, t, si, s


def _tr(seg: dict):
    tr = seg.get("target_timerange") or {}
    return _f(tr.get("start")), _f(tr.get("duration"))


# --------------------------------------------------------------------------- #
# 主轨 / 叠加轨
# --------------------------------------------------------------------------- #
def video_tracks(draft: dict) -> list:
    return [(ti, t) for ti, t in enumerate(draft.get("tracks") or [])
            if str(t.get("type")) == "video" and (t.get("segments") or [])]


def pick_main_track(draft: dict) -> tuple:
    """主轨 = 第一个有段的视频轨；同时算出「覆盖时长最长」的那条做交叉校验。

    风格库的 `main_track_clips` 就是这个口径（24 套实测与「覆盖最长」也一致），
    两者若不等必须记进 `unmeasured`，不能让口径偷偷漂。
    """
    vts = video_tracks(draft)
    if not vts:
        return -1, -1
    first_i = vts[0][0]
    cover = {ti: sum(_tr(s)[1] for s in t.get("segments") or []) for ti, t in vts}
    max_i = max(cover, key=lambda k: cover[k])
    return first_i, max_i


def measure_main_segments(draft: dict, main_i: int) -> list:
    mats = (draft.get("materials") or {}).get("videos") or []
    by_id = {m.get("id"): m for m in mats}
    out = []
    for ti, t, si, s in _track_iter(draft):
        if ti != main_i:
            continue
        st, du = _tr(s)
        src = s.get("source_timerange") or {}
        mat = by_id.get(s.get("material_id")) or {}
        out.append({
            "i": si,
            "start": _s3(st),
            "dur": _s3(du),
            "src_in": _s3(src.get("start")),
            "src_dur": _s3(src.get("duration")) if src.get("duration") is not None else None,
            "material_type": str(mat.get("type") or "video"),
            "speed": _f(s.get("speed"), 1.0),
        })
    out.sort(key=lambda r: (r["start"], r["i"]))
    return out


def measure_overlay_segments(draft: dict, main_i: int) -> list:
    mats = (draft.get("materials") or {}).get("videos") or []
    by_id = {m.get("id"): m for m in mats}
    out = []
    for ti, t, si, s in _track_iter(draft):
        if ti == main_i or str(t.get("type")) != "video":
            continue
        st, du = _tr(s)
        clip = s.get("clip") or {}
        mat = by_id.get(s.get("material_id")) or {}
        out.append({
            "track_index": ti,
            "i": si,
            "start": _s3(st),
            "dur": _s3(du),
            "end": _s3(st + du),
            "alpha": _r(_f(clip.get("alpha"), 1.0), 3),
            "material_type": str(mat.get("type") or "video"),
        })
    out.sort(key=lambda r: (r["start"], r["track_index"], r["i"]))
    return out


# --------------------------------------------------------------------------- #
# 切点表 / 时长分布 / A-B 游程
# --------------------------------------------------------------------------- #
def build_cut_sheet(main: list, overlays: list, total_sec: float) -> dict:
    """全片切点表：主轨段边界（硬切）+ 叠加视频进出点（叠层切开，但不切主轨）。"""
    points = []
    for k, seg in enumerate(main):
        points.append({"t": seg["start"], "kind": "main_start" if k == 0 else "main_cut",
                       "track": "main", "i": seg["i"]})
    for k, ov in enumerate(overlays):
        points.append({"t": ov["start"], "kind": "overlay_in", "track": ov["track_index"], "i": ov["i"]})
        points.append({"t": ov["end"], "kind": "overlay_out", "track": ov["track_index"], "i": ov["i"]})
    points.sort(key=lambda p: (p["t"], 0 if p["kind"].startswith("main") else 1))
    hard = [p for p in points if p["kind"] in ("main_start", "main_cut")]
    return {
        "total_sec": round(float(total_sec), 3),
        "n_main_boundaries": len(hard),
        "n_hard_cuts": max(0, len([p for p in hard if p["kind"] == "main_cut"])),
        "n_overlay_edges": len(points) - len(hard),
        "points": points,
    }


def build_ab_runs(main: list, overlays: list, total_sec: float) -> dict:
    """按时间顺序把「有叠加视频覆盖」标 B、「只有主轨」标 A，输出游程序列与各自分布。

    覆盖口径与 forensic/jy_extract_ground_truth.py 的 `broll_overlay_ratio` 一致
    （所有非主轨视频段的区间并集），另给一版只算 alpha>0.01 的可见覆盖做对照。
    """
    if total_sec <= 0:
        return {"sequence": [], "unmeasured": "总时长为 0"}
    bounds = {0.0, round(total_sec, 3)}
    for seg in main:
        bounds.add(seg["start"])
        bounds.add(round(seg["start"] + seg["dur"], 3))
    for ov in overlays:
        bounds.add(ov["start"])
        bounds.add(ov["end"])
    cuts = sorted(b for b in bounds if 0.0 <= b <= round(total_sec, 3) + 1e-6)
    labels = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a <= 1e-4:
            continue
        # 用区间中点判 A/B：微秒取整会在段边界留 1ms 级的缝，按「覆盖长度 ≥0.05s」判会把
        # 这些缝误判成 A 段，把一条长 B 段切成一串 A1/B1 的假交替。
        mid = (a + b) / 2.0
        hit = any(ov["start"] - 1e-6 <= mid <= ov["end"] + 1e-6 for ov in overlays)
        labels.append(("B" if hit else "A", round(b - a, 3)))
    runs = []
    for lab, du in labels:
        if runs and runs[-1]["label"] == lab:
            runs[-1]["dur"] = round(runs[-1]["dur"] + du, 3)
            runs[-1]["n"] += 1
        else:
            runs.append({"label": lab, "dur": du, "n": 1})
    seq = [f"{r['label']}{r['n']}" for r in runs]
    a_durs = sorted(r["dur"] for r in runs if r["label"] == "A")
    b_durs = sorted(r["dur"] for r in runs if r["label"] == "B")
    # 游程「几个区间」= 连续几段同标签；秒数是它的时长。两者都要，规划端用的是区间数
    a_lens = sorted(r["n"] for r in runs if r["label"] == "A")
    b_lens = sorted(r["n"] for r in runs if r["label"] == "B")
    covered = round(sum(r["dur"] for r in runs if r["label"] == "B"), 3)
    vis = [ov for ov in overlays if float(ov.get("alpha") or 0.0) > 0.01]
    vis_covered = 0.0
    for ov in vis_covered_intervals(vis):
        vis_covered += ov[1] - ov[0]
    return {
        "sequence": seq,
        "n_runs": len(runs),
        "n_a_runs": len(a_durs),
        "n_b_runs": len(b_durs),
        "a_runs": {"n": len(a_durs), "median_sec": pct(a_durs, 0.5),
                   "median_intervals": pct(a_lens, 0.5), "max_intervals": (a_lens[-1] if a_lens else None),
                   "max_sec": (a_durs[-1] if a_durs else None),
                   "min_sec": (a_durs[0] if a_durs else None), "total_sec": round(sum(a_durs), 3)},
        "b_runs": {"n": len(b_durs), "median_sec": pct(b_durs, 0.5),
                   "median_intervals": pct(b_lens, 0.5), "max_intervals": (b_lens[-1] if b_lens else None),
                   "max_sec": (b_durs[-1] if b_durs else None),
                   "min_sec": (b_durs[0] if b_durs else None), "total_sec": round(sum(b_durs), 3)},
        "b_coverage_ratio": round(covered / float(total_sec), 4),
        "b_coverage_ratio_visible": round(min(1.0, vis_covered / float(total_sec)), 4),
        "n_hidden_overlay_segments": len(overlays) - len(vis),
    }


def vis_covered_intervals(overlays: list) -> list:
    iv = sorted((ov["start"], ov["end"]) for ov in overlays)
    merged = []
    for s, e in iv:
        if merged and s <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def measure_shot_distribution(main: list) -> dict:
    durs = sorted(round(seg["dur"], 3) for seg in main if seg["dur"] > 0)
    if not durs:
        return {"n": 0, "p10": None, "p25": None, "p50": None, "p75": None, "p90": None,
                "max": None, "min": None, "mean": None, "fast_cuts_under_1s_ratio": None}
    fast = [d for d in durs if d < 1.0]
    return {
        "n": len(durs),
        "min": durs[0],
        "p10": pct(durs, 0.10),
        "p25": pct(durs, 0.25),
        "p50": pct(durs, 0.50),
        "p75": pct(durs, 0.75),
        "p90": pct(durs, 0.90),
        "max": durs[-1],
        "mean": round(sum(durs) / len(durs), 4),
        "fast_cuts_under_1s_ratio": round(len(fast) / len(durs), 3),
        "fast_cuts_n": len(fast),
        # 分位是汇总，但剪辑要的是**节奏型**：按时间顺序的原样时长序列（切点排布用得上）
        "durations_sec": durs,
        "durations_chrono": [round(seg["dur"], 3) for seg in main if seg["dur"] > 0],
    }


def measure_opening(main: list, total_sec: float) -> dict:
    """开场：前 3s 内有几个镜头（含首镜本身）/ 几个切点 / 首镜多长 / hook 收口时刻。

    口径与风格库生成器一致（dev_tools/build_style_library_v4.py::opening_stats）：
    `first_3s_cuts_count` = 主轨里 start<3.0 的段数；`hook_intro_sec` = 第 3 个段起点
    （不足 3 段就取首镜时长），两者都夹在 0..3。这里把「切点数 = 段数-1」也单独写清楚，
    因为 step4 的 `first_3s_visual_changes` 数的是切点，不是段数。
    """
    starts = [seg["start"] for seg in main]
    if not starts:
        return {"shots_in_first_3s": None, "cuts_in_first_3s": None,
                "first_shot_sec": None, "hook_intro_sec": None}
    in3 = [t for t in starts if t < 3.0]
    first_clip = round(main[0]["dur"], 3)
    hook = starts[2] if len(starts) > 2 else first_clip
    return {
        "shots_in_first_3s": len(in3),
        "cuts_in_first_3s": max(0, len(in3) - 1),
        "first_shot_sec": first_clip,
        "hook_intro_sec": round(min(hook, 3.0), 3),
        "total_sec": round(float(total_sec), 3),
    }


def measure_keyframes(draft: dict, main_i: int, main: list, total_sec: float) -> dict:
    """推近/缩放关键帧：`time_offset` 在草稿里是**源素材绝对时间**，要减段 src_in 再叠段起点。"""
    per_shot, times, n_all = [], [], 0
    scale_groups = 0
    for ti, t, si, s in _track_iter(draft):
        if ti != main_i:
            continue
        st, du = _tr(s)
        src = s.get("source_timerange") or {}
        s_in = _f(src.get("start"))
        seg_scale_kfs = []
        for kfo in s.get("common_keyframes") or []:
            prop = str(kfo.get("property_type") or "")
            pts = []
            for k in kfo.get("keyframe_list") or []:
                vals = k.get("values") or []
                if not vals:
                    continue
                t_sec = _s3(st + (_f(k.get("time_offset")) - s_in))
                pts.append({"t": t_sec, "v": _r(_f(vals[0]), 4)})
            if not pts:
                continue
            n_all += len(pts)
            pts.sort(key=lambda p: p["t"])
            if prop.startswith("KFTypeScale"):
                scale_groups += 1
                times.extend(p["t"] for p in pts)
                seg_scale_kfs.append({"property": prop, "points": pts})
        push = None
        if seg_scale_kfs:
            vs = [p["v"] for g in seg_scale_kfs for p in g["points"]]
            push = bool(vs and abs(vs[-1] - vs[0]) >= 0.02)
        per_shot.append({"i": si, "start": _s3(st), "dur": _s3(du),
                         "n_groups": len(seg_scale_kfs), "push_in": push,
                         "scale_kfs": seg_scale_kfs})
    times.sort()
    shots_total = len([r for r in per_shot if r["dur"] > 0])
    return {
        "total_keyframe_points": n_all,
        "scale_keyframe_groups": scale_groups,
        "scale_keyframe_points": len(times),
        "times": times,
        "kf_per_min": (round(len(times) / max(total_sec / 60.0, 0.1), 2) if total_sec > 0 else None),
        "shots_with_push_in": sum(1 for r in per_shot if r["push_in"]),
        "shots_total": shots_total,
        "per_shot": per_shot,
    }


def measure_text_beats(draft: dict) -> dict:
    """字幕节拍：每条字幕的出现时刻 + 时长 + 字数（正文不落盘，只记字数）。"""
    mats = (draft.get("materials") or {}).get("texts") or []
    tmap = {m.get("id"): m for m in mats}
    tracks = [(ti, t) for ti, t in enumerate(draft.get("tracks") or [])
              if str(t.get("type")) == "text" and (t.get("segments") or [])]
    if not tracks:
        return {"n": 0, "entries": [], "median_dur": None, "median_chars": None,
                "subtitle_track_index": None}
    # 字幕轨 = 文字段最多的那条轨（花字轨通常只有几条）
    sub_i, sub_t = max(tracks, key=lambda kv: len(kv[1].get("segments") or []))
    entries = []
    for si, s in enumerate(sub_t.get("segments") or []):
        st, du = _tr(s)
        raw = (tmap.get(s.get("material_id")) or {}).get("content")
        try:
            content = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            content = {}
        txt = str((content or {}).get("text") or "").replace("\n", "").strip()
        entries.append({"t": _s3(st), "dur": _s3(du), "chars": len(txt)})
    entries.sort(key=lambda e: e["t"])
    durs = sorted(e["dur"] for e in entries if e["dur"] > 0)
    chars = sorted(e["chars"] for e in entries if e["chars"] > 0)
    return {
        "n": len(entries),
        "subtitle_track_index": sub_i,
        "entries": entries,
        "median_dur": pct(durs, 0.5),
        "median_chars": pct(chars, 0.5),
        "all_text_tracks": [{"track_index": ti, "n": len(t.get("segments") or [])} for ti, t in tracks],
    }


def cut_text_alignment(main: list, text_beats: dict, tol_sec: float = 0.10) -> dict:
    """刀法与字幕同步度：主轨切点里有多少落在某条字幕出现时刻 ±tol 内。"""
    onsets = [e["t"] for e in (text_beats.get("entries") or []) if e.get("t") is not None and e["t"] > 0.01]
    cuts = [seg["start"] for seg in main if seg["start"] > 0.01]
    if not cuts or not onsets:
        return {"n_cuts": len(cuts), "n_onsets": len(onsets), "aligned_ratio": None}
    hit = sum(1 for c in cuts if min(abs(c - o) for o in onsets) <= tol_sec)
    return {"n_cuts": len(cuts), "n_onsets": len(onsets), "tol_sec": tol_sec,
            "aligned_ratio": round(hit / len(cuts), 3)}


# --------------------------------------------------------------------------- #
# 单套装配
# --------------------------------------------------------------------------- #
def measure_style(style_id: str, entry: dict) -> dict:
    path = resolve_draft_by_style(style_id)
    digest = sha256_of(path)
    draft = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    total_sec = _f(draft.get("duration")) / US
    main_i, cover_i = pick_main_track(draft)
    main = measure_main_segments(draft, main_i)
    overlays = measure_overlay_segments(draft, main_i)
    if total_sec <= 0 and main:
        total_sec = round(max(seg["start"] + seg["dur"] for seg in main), 3)

    unmeasured = []
    if main_i < 0:
        unmeasured.append("草稿里没有任何有段的视频轨，主轨刀法无从量")
    if cover_i >= 0 and coverage_diff(draft, cover_i):
        unmeasured.append("主轨口径冲突：首条视频轨与覆盖最长轨不是同一条，本文件按首条记")
    shots = measure_shot_distribution(main)
    runs = build_ab_runs(main, overlays, total_sec)
    opening = measure_opening(main, total_sec)
    kfs = measure_keyframes(draft, main_i, main, total_sec)
    texts = measure_text_beats(draft)
    align = cut_text_alignment(main, texts)
    if not overlays:
        unmeasured.append("无叠加视频段：A/B 交替游程退化为单条 A 游程（该风格本来就是零插入）")
    if texts.get("n") in (0, None):
        unmeasured.append("草稿无文字段，字幕节拍为空")
    if kfs["scale_keyframe_points"] == 0:
        unmeasured.append("主轨无缩放关键帧（该风格机位不动）")

    # 交叉校验：和风格库里的标量摘要比对，证明本文件是独立量的，不是复述
    sd = entry.get("shot_distribution") or {}
    ab = entry.get("ab_switch_spec") or {}
    op = entry.get("opening_structure") or {}
    parity = {
        "median_sec": {"measured": shots["p50"], "library": sd.get("median_sec"),
                       "delta": _delta(shots["p50"], sd.get("median_sec"))},
        "main_track_clips": {"measured": shots["n"], "library": sd.get("main_track_clips"),
                             "delta": _delta(shots["n"], sd.get("main_track_clips"))},
        "fast_cuts_under_1s_ratio": {"measured": shots["fast_cuts_under_1s_ratio"],
                                     "library": sd.get("fast_cuts_under_1s_ratio"),
                                     "delta": _delta(shots["fast_cuts_under_1s_ratio"],
                                                     sd.get("fast_cuts_under_1s_ratio"))},
        "broll_coverage_ratio": {"measured": runs["b_coverage_ratio"],
                                 "library_present": sd.get("broll_coverage_ratio"),
                                 "delta": _delta(runs["b_coverage_ratio"], sd.get("broll_coverage_ratio"))},
        "shots_in_first_3s": {"measured": opening["shots_in_first_3s"],
                              "library_first_3s_cuts_count": op.get("first_3s_cuts_count"),
                              "delta": _delta(opening["shots_in_first_3s"], op.get("first_3s_cuts_count"))},
        "opening_cuts_ab_spec": {"library": ab.get("opening_cuts"),
                                 "measured_cuts_in_first_3s": opening["cuts_in_first_3s"],
                                 "delta": _delta(opening["cuts_in_first_3s"], ab.get("opening_cuts"))},
    }
    return {
        "style_id": style_id,
        "source_ref": "wb_" + digest[:12],
        "draft_sha256": digest,
        "archetype_id": str(entry.get("archetype_id") or ""),
        "duration_sec": round(float(total_sec), 3),
        "main_track_index": main_i,
        "main_segments": main,
        "overlay_segments": overlays,
        "cut_sheet": build_cut_sheet(main, overlays, total_sec),
        "shot_distribution": shots,
        "ab_runs": runs,
        "opening": opening,
        "keyframes": kfs,
        "text_beats": texts,
        "cut_text_alignment": align,
        "parity": parity,
        "unmeasured": unmeasured,
    }


def coverage_diff(draft: dict, cover_i: int) -> bool:
    vts = video_tracks(draft)
    first_i = vts[0][0] if vts else -1
    return cover_i != first_i


def _delta(a, b):
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 4)


def privacy_gate(payload: str) -> list:
    return [w for w in FORBIDDEN if w in payload]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    lib = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    out = {
        "generated_at": "2026-11-24",
        "what": "24 套风格各自的「剪辑刀法」真值：主轨段序列 / 合成切点表 / 切镜时长分位 / "
                "A-B 交替游程 / 开场节奏 / 推近关键帧 / 字幕节拍",
        "source": "style_spectrum_library.json::source_hash → forensic/decrypted/*/{draft_content.json,__latest_close.json}",
        "measurement_policy": "全部从明文草稿的 tracks[].segments[].target_timerange/source_timerange 直接量；"
                              "量不到写 null 并记 unmeasured；标量摘要只用于 parity 交叉校验，不作为真值来源",
        "time_unit": "秒，保留 3 位小数（草稿值为微秒，1/60s 网格）",
        "n_styles": 0,
        "styles": {},
    }
    for entry in lib.get("styles") or []:
        sid = entry["style_id"]
        out["styles"][sid] = measure_style(sid, entry)
    out["n_styles"] = len(out["styles"])

    payload = json.dumps(out, ensure_ascii=False, indent=1)
    hits = privacy_gate(payload)
    if hits:
        print(f"隐私门禁失败：产物里出现 {hits}")
        return 1
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(payload + "\n", encoding="utf-8")

    print(f"edit grammar: {out['n_styles']} 套 -> {outp.relative_to(ROOT)}")
    for sid, g in out["styles"].items():
        sd = g["shot_distribution"]
        runs = g["ab_runs"]
        op = g["opening"]
        print(f"  {sid}: {sd['n']} 镜 / p50 {sd['p50']}s / 快切 {sd['fast_cuts_under_1s_ratio']} / "
              f"B {runs['b_coverage_ratio']} / 开场 {op['shots_in_first_3s']} 镜 / "
              f"游程 {len(runs['sequence'])} 段 / KF {g['keyframes']['scale_keyframe_points']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
