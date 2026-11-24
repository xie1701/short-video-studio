#!/usr/bin/env python3
"""音效 / 花字的「触发条件」公共底座（规则表读取 + 锚点归属 + 节拍网格 + 密度闸门）。

为什么要它
──────────
用户实测原话：「这一版的音效过于密集，感觉是没有任何缘由就出现……应该好好抽象下这些音效、
花字之类的是要在什么情况下才会出现，调用的原则是怎样，作用域又是怎样，而不是无脑的硬植入。」

旧 `step6_packaging.py` 的判定只有「密度 + 最小间隔」：它按 sfx_per_min 把音效铺满每一个切点，
从不问这条 cue 凭什么响；花字更糟——它直接拿音效 cue 当花字锚点，等于"有音效就有花字"。
本模块把「凭什么响」变成可计算的东西：

  1. 每条候选必须挂到一个**可观测锚点**上（切点 / 花字出现或退出 / 字幕换行 / BGM 节拍），
     挂不上就一律不出（`reason=None`）。
  2. 锚点本身要过**结构门槛**：贴在 `stress`（口播中段的重音微切）上的音效不算"切点理由"，
     因为那正是用户听到的"没有任何缘由就响一声"。
  3. 密度不是拍脑袋的常数，而是从 24 套源草稿反推出来的（`forensic/style_trigger_rules_sfx_flower.json`）。

规则表只放**统计真值**，不含任何一版成片的审美偏好；阈值判定全部走这里，便于测试锁死。
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_SCRIPTS_DIR))

# 仓库根（short-video-studio/scripts → short-video-studio → 仓库根）
SKILL_DIR = _SCRIPTS_DIR.parent
REPO_ROOT = SKILL_DIR.parent

RULES_ASSET = SKILL_DIR / "assets" / "style_spectrum" / "style_trigger_rules_sfx_flower.json"
RULES_FORENSIC = REPO_ROOT / "forensic" / "style_trigger_rules_sfx_flower.json"

# 归属容差：源草稿里音效/花字贴锚点的实测中位偏移是 0.000s，p90 ≤ 0.083s。
# 取 120ms 作为"贴住"的判据（30fps 下约 3.6 帧），比人耳可分辨的错位（~80ms）略宽，
# 但足以把"落在锚点附近"和"落在段落中间"分开。
TOL_CUT_SEC = 0.12
TOL_FLOWER_SEC = 0.12
TOL_LINE_SEC = 0.12
TOL_BEAT_SEC = 0.08

SFX_REASONS = ("cut", "flower", "line", "beat")
FLOWER_REASONS = ("number", "chapter", "keyword", "line_echo", "cut", "free")

# 切点类型里，只有这些算"结构性切点"（段落/句子/重拍边界）。
# `stress` / `bgm_beat` 是口播中段的重音微切与等分节拍微切——音效贴上去就是"无理由的一声"，
# 这是 2026-11-24 用户投诉的直接来源。
STRUCTURAL_CUT_TYPES = frozenset({
    "sentence_start", "sentence_end", "pause_start", "pause_end",
    "bgm_downbeat", "keyword_accent", "shot_change", "structural_cut", "start",
})
NON_STRUCTURAL_CUT_TYPES = frozenset({"stress", "bgm_beat", "beat", "midline", "micro_cut"})

# 切点本身也是"换行/换句"时，优先记成 cut（切点强于换行）；花字出现强于换行。
REASON_PRIORITY = {"cut": 0, "flower": 1, "line": 2, "beat": 3}

DEFAULTS = {
    "sfx": {
        "tol_sec": TOL_CUT_SEC,
        "allowed_triggers": ["cut", "flower", "line"],
        "max_per_min": 30.0,
        "max_free_per_min": 0.0,
        "min_gap_sec": 0.9,
        "min_gap_by_role": {},
        "max_per_min_by_role": {},
        "role_for_trigger": {"cut": "cut", "flower": "accent", "line": "info", "beat": "hook"},
        "scope": {
            "silence_ban": True,
            "midline_ban": True,
            "structural_cut_only": True,
            "allowed_shot_types": ["a_roll", "b_roll"],
        },
    },
    "flower": {
        "tol_sec": TOL_FLOWER_SEC,
        "allowed_triggers": ["number", "chapter", "keyword", "line_echo"],
        "max_per_min": 12.0,
        "min_gap_sec": 2.0,
        "chars_min": 2,
        "chars_max": 10,
        "hold_sec_min": 1.2,
        "hold_sec_max": 4.0,
        "scope": {"allowed_shot_types": ["a_roll", "b_roll"], "zones": []},
    },
}


# ── 规则表 ────────────────────────────────────────────────────────────────────

_CACHE: Dict[str, Any] = {}


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_rule_table(rules_path: Optional[Path] = None) -> Dict[str, Any]:
    """读规则表。优先技能包内副本（随包分发），再退到取证目录（本机真值）。"""
    key = str(rules_path or "")
    if key in _CACHE:
        return _CACHE[key]
    table: Dict[str, Any] = {}
    for cand in ([Path(rules_path)] if rules_path else [RULES_ASSET, RULES_FORENSIC]):
        if cand and cand.is_file():
            table = _read_json(cand)
            if table.get("styles"):
                break
    _CACHE[key] = table
    return table


def style_rules(style_id: str, rules_path: Optional[Path] = None) -> Dict[str, Any]:
    """取某套风格的 sfx/flower 规则，缺字段用安全默认值补齐（缺规则不等于放开）。"""
    table = load_rule_table(rules_path)
    node = (table.get("styles") or {}).get(str(style_id)) or {}
    out: Dict[str, Any] = {"style_id": style_id, "found": bool(node)}
    for kind in ("sfx", "flower"):
        merged = json.loads(json.dumps(DEFAULTS[kind]))
        got = node.get(kind) or {}
        for k, v in got.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        out[kind] = merged
    out["beat"] = node.get("beat") or {}
    out["duration_sec"] = node.get("duration_sec")
    return out


# ── 锚点工具 ──────────────────────────────────────────────────────────────────

def sorted_float(vals: Iterable[Any]) -> List[float]:
    out: List[float] = []
    for v in vals or []:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def nearest_offset(t: float, points: Sequence[float]) -> Tuple[Optional[float], float]:
    """返回 (最近点的偏移绝对值, 最近点时间)。空表返回 (None, nan)。"""
    if not points:
        return None, float("nan")
    lo, hi = 0, len(points) - 1
    t = float(t)
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    best = points[lo]
    if lo > 0 and abs(points[lo - 1] - t) <= abs(best - t):
        best = points[lo - 1]
    return abs(best - t), best


def is_structural_cut_type(event_type: Optional[str]) -> bool:
    """没有类型信息时按"结构性"处理（不能让缺失字段把整片音效全禁掉）。"""
    et = str(event_type or "").strip().lower()
    if not et:
        return True
    if et in NON_STRUCTURAL_CUT_TYPES:
        return False
    return True


def classify_sfx_anchor(
    t: float,
    anchors: Dict[str, Sequence[float]],
    tol: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """把一条音效候选取归属到最近的锚点类型；都挂不上就 reason=None（该 cue 不许出）。

    anchors 支持键：cut / flower / flower_exit / line / beat。
    """
    tol_map = {"cut": TOL_CUT_SEC, "flower": TOL_FLOWER_SEC, "line": TOL_LINE_SEC, "beat": TOL_BEAT_SEC}
    tol_map.update({k: float(v) for k, v in (tol or {}).items() if v is not None})
    cands: List[Dict[str, Any]] = []
    for reason in SFX_REASONS:
        off, at = nearest_offset(t, sorted_float(anchors.get(reason) or []))
        if off is None:
            continue
        cands.append({"reason": reason, "offset_sec": round(off, 4), "anchor_time": round(at, 4),
                      "tol_sec": tol_map[reason], "hit": off <= tol_map[reason]})
    hits = [c for c in cands if c["hit"]]
    if not hits:
        return {"reason": None, "anchor_time": None, "offset_sec": None, "candidates": cands}
    hits.sort(key=lambda c: (c["offset_sec"] - c["tol_sec"], REASON_PRIORITY[c["reason"]]))
    best = hits[0]
    return {"reason": best["reason"], "anchor_time": best["anchor_time"],
            "offset_sec": best["offset_sec"], "candidates": cands}


def silence_windows(
    sentences: Sequence[Dict[str, Any]],
    duration_sec: float,
    min_len_sec: float = 0.35,
) -> List[Tuple[float, float]]:
    """纯静音段 = 没有人声的空白区间（人声之间 ≥ min_len_sec 的缝）。"""
    spans: List[Tuple[float, float]] = []
    for s in sentences or []:
        try:
            a = float(s.get("start_sec", s.get("start_sec", 0.0)))
            b = float(s.get("end_sec", 0.0))
        except (TypeError, ValueError):
            continue
        if b > a:
            spans.append((a, b))
    spans.sort()
    gaps: List[Tuple[float, float]] = []
    cur = 0.0
    for a, b in spans:
        if a - cur >= min_len_sec:
            gaps.append((round(cur, 3), round(a, 3)))
        cur = max(cur, b)
    if float(duration_sec) - cur >= min_len_sec:
        gaps.append((round(cur, 3), round(float(duration_sec), 3)))
    return gaps


def in_windows(t: float, windows: Sequence[Tuple[float, float]], pad: float = 0.0) -> bool:
    for a, b in windows or []:
        if a - pad <= t <= b + pad:
            return True
    return False


def sliding_count(times: Sequence[float], t: float, window_sec: float = 60.0) -> int:
    lo = t - window_sec
    return sum(1 for x in times if lo < x <= t + 1e-6)


# ── BGM 节拍网格（源草稿的 beats 材料不带时间，只能从 BGM 音频反推）─────────────

_BPM_CACHE: Dict[str, Dict[str, Any]] = {}
_SR = 22050


def _decode_mono(path: Path, start: float, dur: float) -> Any:
    import numpy as np

    cmd = ["ffmpeg", "-v", "quiet", "-ss", str(start), "-t", str(dur), "-i", str(path),
           "-f", "f32le", "-ac", "1", "-ar", str(_SR), "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=90).stdout
    except Exception:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.float32)


def estimate_bpm(path: Path, windows: Sequence[float] = (0.0, 10.0, 24.0, 40.0)) -> Dict[str, Any]:
    """长窗互相印证的自相关 BPM 估计；各窗不一致就降置信度（beat 归属只在置信度高时启用）。"""
    import numpy as np

    key = str(path)
    if key in _BPM_CACHE:
        return _BPM_CACHE[key]
    path = Path(path)
    out: Dict[str, Any] = {"bpm": None, "confidence": 0.0, "source": "bgm_autocorr"}
    if not path.is_file():
        _BPM_CACHE[key] = out
        return out
    hop, nfft = 512, 1024
    found: List[float] = []
    best_conf = 0.0
    for st in windows:
        a = _decode_mono(path, float(st), 16.0)
        if len(a) < _SR * 6:
            continue
        frames = 1 + (len(a) - nfft) // hop
        if frames < 32:
            continue
        win = np.hanning(nfft).astype(np.float32)
        S = np.empty((frames, nfft // 2 + 1), dtype=np.float32)
        for i in range(frames):
            S[i] = np.abs(np.fft.rfft(a[i * hop:i * hop + nfft] * win))
        L = np.log1p(S * 10.0)
        flux = np.maximum(0.0, np.diff(L, axis=0)).sum(axis=1)
        flux = flux - flux.mean()
        ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
        ac = ac / (ac[0] + 1e-9)
        fps = _SR / hop
        lo, hi = int(fps * 60 / 180), int(fps * 60 / 55)
        if hi <= lo + 1:
            continue
        seg = ac[lo:hi]
        k = int(np.argmax(seg)) + lo
        found.append(round(60.0 * fps / k, 2))
        best_conf = max(best_conf, float(seg.max()))
    if not found:
        _BPM_CACHE[key] = out
        return out
    # 取出现最多的 BPM（±1.5 视为同一个）
    clusters: List[List[float]] = []
    for b in found:
        for c in clusters:
            if abs(c[0] - b) <= 1.5:
                c.append(b)
                break
        else:
            clusters.append([b])
    clusters.sort(key=len, reverse=True)
    top = clusters[0]
    agree = len(top) / float(len(found))
    out = {"bpm": round(sum(top) / len(top), 2),
           "confidence": round(min(1.0, agree * 0.6 + best_conf * 0.4), 3),
           "source": "bgm_autocorr"}
    _BPM_CACHE[key] = out
    return out


def beat_grid(bpm: Optional[float], duration_sec: float, phase_sec: float = 0.0,
              max_beats: int = 4000) -> List[float]:
    if not bpm or bpm <= 0 or duration_sec <= 0:
        return []
    period = 60.0 / float(bpm)
    n = min(max_beats, int(math.ceil(float(duration_sec) / period)) + 2)
    return [round(phase_sec + i * period, 4) for i in range(n)]


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
