#!/usr/bin/env python3
"""
[style_01 / style_21 节奏编舞器 · 纯函数]

把风格库里实测出来的剪辑节拍，落成可被选镜/包装步骤直接消费的确定性计划：

- `style_01`：3 拍错落斜标开场 + 中位 1.87s 的口播硬切紧拼 + 约 57% 语义画面覆盖；
- `style_21`：前 3 秒至少 4 个画面切片的高能快切 + 约 76% 工艺特写覆盖 + 构图推镜关键帧；
- 两套风格都锁死同一件事：**口播人声段落倍速只能是 1.0x**，不靠加速口播凑快剪感。

本模块只读 `common.ASSETS_DIR` 下的既有 JSON 真值，不写文件、不引入随机数，
同样的输入永远得到同样的输出，供总控在后续冲刺接入 step4 / step6。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from common import ASSETS_DIR  # noqa: E402

LIB_PATH = ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json"
BP_PATH = ASSETS_DIR / "style_spectrum" / "template_blueprints.json"

# 兜底真值只在资产包被搬走时兜底；平时永远以库/蓝图为唯一真源。
_FALLBACK_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "style_01": {
        "style_name_cn": "门店原生爆款风 · 综艺分拍斜标开场（默认）",
        "broll_target_ratio": 0.572,
        "broll_ratio_band": (0.45, 0.68),
        "opening_cuts_3s": 3,
        "median_shot_sec": 1.87,
        "opening_mode": "store_poster_burst",
        "anim_mode": "store_stagger_burst",
        "tilt_deg": -5.0,
        "punch_in_scale": 1.0,
        "fast_cuts_under_1s_ratio": 0.167,
        "keyframe_count": 0,
        "pip_mode": "geometry_pip_window",
        "opening_beats": 3,
    },
    "style_21": {
        "style_name_cn": "门店工艺快切 · 76% 实拍特写暖黄大字风",
        "broll_target_ratio": 0.756,
        "broll_ratio_band": (0.60, 0.906),
        "opening_cuts_3s": 4,
        "median_shot_sec": 1.15,
        "opening_mode": "store_detail_badge",
        "anim_mode": "scale_in",
        "tilt_deg": 0.0,
        "punch_in_scale": 1.2,
        "fast_cuts_under_1s_ratio": 0.462,
        "keyframe_count": 24,
        "pip_mode": "geometry_pip_main_punch_in",
        "opening_beats": 0,
    },
}


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _build_contracts() -> Dict[str, Dict[str, Any]]:
    lib = _read_json(LIB_PATH)
    bps = (_read_json(BP_PATH).get("blueprints") or {})
    entries = {str(s.get("style_id")): s for s in (lib.get("styles") or []) if isinstance(s, dict)}
    contracts: Dict[str, Dict[str, Any]] = {}
    for style_id, fallback in _FALLBACK_CONTRACTS.items():
        entry = entries.get(style_id) or {}
        bp = bps.get(style_id) or {}
        overlay = bp.get("overlay_style") or {}
        shot = entry.get("shot_distribution") or {}
        opening = entry.get("opening_structure") or {}
        pip = entry.get("pip_composition_spec") or {}
        contract = dict(fallback)
        contract.update({
            "style_id": style_id,
            "style_name_cn": str(entry.get("style_name_cn") or fallback["style_name_cn"]),
            "broll_target_ratio": float(entry.get("broll_overlay_ratio_target", fallback["broll_target_ratio"])),
            "opening_cuts_3s": int(opening.get("first_3s_cuts_count", fallback["opening_cuts_3s"])),
            "median_shot_sec": float(shot.get("median_sec", fallback["median_shot_sec"])),
            "opening_mode": str(overlay.get("opening_mode") or fallback["opening_mode"]),
            "anim_mode": str(overlay.get("anim_mode") or fallback["anim_mode"]),
            "tilt_deg": float(overlay.get("tilt_deg", fallback["tilt_deg"])),
            "punch_in_scale": float((pip.get("main") or {}).get("scale", fallback["punch_in_scale"])),
            "aroll_speed_lock": 1.0,
            "fast_cuts_under_1s_ratio": float(shot.get("fast_cuts_under_1s_ratio", fallback["fast_cuts_under_1s_ratio"])),
            "keyframe_count": int(shot.get("keyframe_count", fallback["keyframe_count"])),
            "pip_mode": str(pip.get("mode") or fallback["pip_mode"]),
            "hard_cut_only": True,
            "broll_ratio_band": tuple(fallback["broll_ratio_band"]),
        })
        contracts[style_id] = contract
    return contracts


STYLE_RHYTHM_CONTRACTS: Dict[str, Dict[str, Any]] = _build_contracts()

OPENING_WINDOW_SEC = 3.2
MIN_SLICE_SEC = 0.45
FAST_CUT_SEC = 1.05
_PUNCT_RE = re.compile(r"[\s，。！？；：、,.!?;:\"'“”‘’()（）\[\]【】—\-]+")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _short_text(text: Any, limit: int = 8) -> str:
    cleaned = _PUNCT_RE.sub("", str(text or "").strip())
    return cleaned[:limit]


def _normalize_sentences(sentences: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    if isinstance(sentences, dict):
        sentences = sentences.get("sentences") or sentences.get("items") or []
    rows: List[Dict[str, Any]] = []
    for index, raw in enumerate(sentences or []):
        if not isinstance(raw, dict):
            continue
        start = _num(raw.get("start_sec"), _num(raw.get("start"), -1.0))
        end = _num(raw.get("end_sec"), _num(raw.get("end"), -1.0))
        if start < 0 or end <= start:
            continue
        rows.append({
            "id": str(raw.get("id") or raw.get("sentence_id") or f"s{index + 1:03d}"),
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "text": str(raw.get("text") or ""),
        })
    rows.sort(key=lambda r: (r["start_sec"], r["end_sec"], r["id"]))
    return rows


def _normalize_candidates(candidates: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    if isinstance(candidates, dict):
        candidates = candidates.get("candidates") or candidates.get("items") or []
    rows: List[Dict[str, Any]] = []
    for index, raw in enumerate(candidates or []):
        if not isinstance(raw, dict):
            continue
        tags = raw.get("semantic_tags") or raw.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        rows.append({
            "id": str(raw.get("id") or raw.get("clip_id") or f"broll_{index + 1:03d}"),
            "duration_sec": max(0.0, _num(raw.get("duration_sec"), 0.0)),
            "tags": [str(t).strip() for t in tags if str(t).strip()],
            "description": str(raw.get("description") or ""),
        })
    rows.sort(key=lambda r: r["id"])
    return rows


def _covering_sentence(sentences: List[Dict[str, Any]], start: float, end: float) -> Optional[Dict[str, Any]]:
    best = None
    best_overlap = 0.0
    for sent in sentences:
        overlap = min(end, sent["end_sec"]) - max(start, sent["start_sec"])
        if overlap > best_overlap + 1e-9:
            best, best_overlap = sent, overlap
    if best is not None:
        return best
    # 句间有静音空档时，落点最近的句子仍可作为这刀的画面台词参考
    mid = (start + end) / 2.0
    return min(sentences, key=lambda s: abs((s["start_sec"] + s["end_sec"]) / 2.0 - mid)) if sentences else None


def _slice_boundaries(
    total: float,
    preferred_steps: Sequence[float],
    min_slice: float = MIN_SLICE_SEC,
    cycle_from: int = 0,
) -> List[float]:
    """按节奏步长切画面，保证最后一刀不小于 min_slice，避免出现无法成片的碎屑镜头。

    `cycle_from` 之后只循环尾部的快切步长：开场固定的错落节拍不会被反复套用。
    """
    if total <= 0:
        return []
    bounds = [0.0]
    cursor = 0.0
    index = 0
    tail_steps = list(preferred_steps[cycle_from:]) or list(preferred_steps)
    while total - cursor > min_slice + 1e-9:
        if index < cycle_from:
            step = float(preferred_steps[index])
        else:
            step = float(tail_steps[(index - cycle_from) % len(tail_steps)])
        step = max(min_slice, step)
        nxt = cursor + step
        if total - nxt < min_slice:
            nxt = total
        nxt = round(nxt, 3)
        bounds.append(nxt)
        if nxt >= total - 1e-9:
            break
        cursor = nxt
        index += 1
    if bounds[-1] < round(total, 3) - 1e-9:
        bounds.append(round(total, 3))
    return bounds


def _match_score(text: str, candidate: Dict[str, Any]) -> float:
    haystack = str(text or "")
    score = 0.0
    for tag in candidate.get("tags") or []:
        if tag and tag in haystack:
            score += 2.0
    description = str(candidate.get("description") or "")
    if description and len(description) >= 2 and description[:4] in haystack:
        score += 1.0
    return score


def _best_candidate(text: str, pool: List[Dict[str, Any]], slice_duration: float) -> Optional[Dict[str, Any]]:
    best = None
    best_key: Optional[Tuple[float, str]] = None
    for cand in pool:
        if cand["duration_sec"] + 1e-9 < slice_duration:
            continue
        score = _match_score(text, cand)
        if score <= 0:
            continue
        key = (-score, cand["id"])
        if best_key is None or key < best_key:
            best_key, best = key, cand
    return best


def _assign_broll(
    segments: List[Dict[str, Any]],
    matched_pool: List[Dict[str, Any]],
    total: float,
    target_ratio: float,
    band: Tuple[float, float],
    opening_cover_sec: float,
) -> Tuple[float, Optional[str]]:
    if not segments or total <= 0:
        return 0.0, "没有可用的口播句子，无法规划节奏"
    target_sec = target_ratio * total
    current = 0.0
    for seg in segments:
        if seg["start_sec"] < opening_cover_sec - 1e-9:
            continue
        if current >= target_sec - 1e-9:
            break
        cand = _best_candidate(seg["text"], matched_pool, seg["duration_sec"])
        if cand is None:
            continue
        seg["track_role"] = "b_roll"
        seg["broll_id"] = cand["id"]
        seg["broll_tags"] = list(cand["tags"])
        current += seg["duration_sec"]
    ratio = current / total
    # 候选够但还差一点时，继续找能压满区间的镜头，不硬塞同一条无关画面
    if ratio < band[0]:
        for seg in segments:
            if seg["track_role"] == "b_roll" or seg["start_sec"] < opening_cover_sec - 1e-9:
                continue
            if current / total >= band[0] - 1e-9:
                break
            cand = _best_candidate(seg["text"], matched_pool, seg["duration_sec"])
            if cand is None:
                continue
            seg["track_role"] = "b_roll"
            seg["broll_id"] = cand["id"]
            seg["broll_tags"] = list(cand["tags"])
            current += seg["duration_sec"]
    ratio = current / total
    # 尾刀偏长可能把占比顶过上限：撤掉最后压上去的画面，守住风格库区间
    if ratio > band[1]:
        for seg in reversed(segments):
            if current / total <= band[1] + 1e-9:
                break
            if seg["track_role"] != "b_roll":
                continue
            current -= seg["duration_sec"]
            seg["track_role"] = "a_roll"
            seg["broll_id"] = ""
            seg["broll_tags"] = []
    ratio = round(current / total, 4)
    if ratio < band[0] - 1e-9:
        if not matched_pool:
            reason = "没有找到与台词对得上的工艺/特写画面，保持口播镜头可剪，不硬塞无关画面"
        else:
            reason = f"对得上的画面总时长不够（计划覆盖 {ratio:.1%}），保持可用镜头，不靠加速口播凑节奏"
        return ratio, reason
    return ratio, None


def _segments_aroll_locked(segments: List[Dict[str, Any]]) -> bool:
    return all(
        abs(_num(seg.get("playback_speed"), 0.0) - 1.0) <= 1e-9
        for seg in segments
        if seg.get("track_role") == "a_roll"
    )


def _build_segments(
    bounds: List[float],
    sentences: List[Dict[str, Any]],
    fps: int,
) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    for index in range(len(bounds) - 1):
        start, end = bounds[index], bounds[index + 1]
        if end - start <= 1e-9:
            continue
        sent = _covering_sentence(sentences, start, end)
        segments.append({
            "segment_index": len(segments) + 1,
            "segment_id": f"seg_{len(segments) + 1:03d}",
            "track_role": "a_roll",
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "start_frame": int(round(start * fps)),
            "end_frame": int(round(end * fps)),
            "duration_sec": round(end - start, 3),
            "playback_speed": 1.0,
            "sentence_id": sent["id"] if sent else "",
            "text": sent["text"] if sent else "",
            "broll_id": "",
            "broll_tags": [],
            "punch_in": False,
        })
    return segments


def _opening_beats(sentences: List[Dict[str, Any]], contract: Dict[str, Any], fps: int) -> List[Dict[str, Any]]:
    specs = ((0.0, 1.10), (0.55, 1.16), (1.15, 1.22))
    beats: List[Dict[str, Any]] = []
    used: set = set()
    for index, (start, duration) in enumerate(specs):
        end = min(OPENING_WINDOW_SEC, start + duration)
        sent = _pick_beat_sentence(sentences, start, end, used)
        if sent is not None:
            used.add(sent["id"])
        text = _short_text(sent["text"]) if sent else ""
        beats.append({
            "beat_index": index + 1,
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "start_frame": int(round(start * fps)),
            "end_frame": int(round(end * fps)),
            "tilt_deg": contract.get("tilt_deg", -5.0),
            "anim_mode": contract.get("anim_mode", "store_stagger_burst"),
            "opening_mode": contract.get("opening_mode", "store_poster_burst"),
            "text": text or f"开场第{index + 1}拍",
        })
    return beats


def _pick_beat_sentence(
    sentences: List[Dict[str, Any]], start: float, end: float, used: set
) -> Optional[Dict[str, Any]]:
    """三拍错落用三条不同的话，不把同一句文案重复贴三遍。"""
    if not sentences:
        return None
    mid = (start + end) / 2.0
    ordered = sorted(
        sentences,
        key=lambda s: (
            0 if min(end, s["end_sec"]) - max(start, s["start_sec"]) > 1e-9 else 1,
            abs((s["start_sec"] + s["end_sec"]) / 2.0 - mid),
            s["id"],
        ),
    )
    for sent in ordered:
        if sent["id"] not in used:
            return sent
    return ordered[0]


def _apply_punch_in(segments: List[Dict[str, Any]], scale: float) -> int:
    """给推镜触发镜生成纯构图缩放轨迹：1.0 -> 1.18~1.22，声音倍速一动不动。"""
    zoom = min(1.22, max(1.18, round(float(scale), 3)))
    triggers = [
        seg for index, seg in enumerate(segments)
        if index > 0 and (seg["track_role"] == "b_roll" or index % 2 == 0)
    ]
    if not triggers and len(segments) > 1:
        triggers = [segments[1]]
    for seg in triggers:
        seg["punch_in"] = True
        seg["punch_in_zoom"] = zoom
        seg["keyframes"] = [
            {"at_sec": 0.0, "scale": 1.0},
            {"at_sec": round(seg["duration_sec"] * 0.55, 3), "scale": zoom},
        ]
        seg["punch_in_audio_locked"] = True
    return len(triggers)


def plan_style_01_rhythm(
    sentences: Optional[Sequence[Dict[str, Any]]],
    broll_candidates: Optional[Sequence[Dict[str, Any]]] = None,
    fps: int = 30,
) -> Dict[str, Any]:
    """`style_01` 门店爆款节奏：3 拍斜标开场 + 57% 语义画面覆盖 + 口播 1.0x 紧拼。"""
    contract = STYLE_RHYTHM_CONTRACTS["style_01"]
    fps = max(1, int(_num(fps, 30)))
    sents = _normalize_sentences(sentences)
    cands = _normalize_candidates(broll_candidates)
    total = sents[-1]["end_sec"] if sents else 0.0

    if total <= 0:
        return {
            "style_id": "style_01",
            "style_name_cn": contract["style_name_cn"],
            "fps": fps,
            "total_duration_sec": 0.0,
            "opening_beats": _opening_beats(sents, contract, fps),
            "segments": [],
            "broll_target_ratio": contract["broll_target_ratio"],
            "broll_ratio_band": list(contract["broll_ratio_band"]),
            "broll_ratio_planned": 0.0,
            "aroll_speed_locked": True,
            "degrade_reason": "没有可用的口播句子，无法规划节奏",
        }

    bounds = _slice_boundaries(total, (contract["median_shot_sec"],))
    segments = _build_segments(bounds, sents, fps)
    texts = " ".join(s["text"] for s in sents)
    pool = [c for c in cands if _match_score(texts, c) > 0]
    ratio, degrade = _assign_broll(
        segments,
        pool,
        total,
        contract["broll_target_ratio"],
        tuple(contract["broll_ratio_band"]),
        min(OPENING_WINDOW_SEC, total),
    )
    return {
        "style_id": "style_01",
        "style_name_cn": contract["style_name_cn"],
        "fps": fps,
        "total_duration_sec": round(total, 3),
        "opening_beats": _opening_beats(sents, contract, fps),
        "opening_mode": contract["opening_mode"],
        "segments": segments,
        "broll_target_ratio": contract["broll_target_ratio"],
        "broll_ratio_band": list(contract["broll_ratio_band"]),
        "broll_ratio_planned": ratio,
        "aroll_speed_locked": _segments_aroll_locked(segments),
        "degrade_reason": degrade,
    }


def plan_style_21_rhythm(
    sentences: Optional[Sequence[Dict[str, Any]]],
    broll_candidates: Optional[Sequence[Dict[str, Any]]] = None,
    fps: int = 30,
) -> Dict[str, Any]:
    """`style_21` 门店工艺快切：首 3 秒 >=4 切片 + 76% 工艺特写 + 推镜关键帧 + 口播 1.0x。"""
    contract = STYLE_RHYTHM_CONTRACTS["style_21"]
    fps = max(1, int(_num(fps, 30)))
    sents = _normalize_sentences(sentences)
    cands = _normalize_candidates(broll_candidates)
    total = sents[-1]["end_sec"] if sents else 0.0

    if total <= 0:
        return {
            "style_id": "style_21",
            "style_name_cn": contract["style_name_cn"],
            "fps": fps,
            "total_duration_sec": 0.0,
            "opening_3s_cuts": [],
            "opening_3s_slice_count": 0,
            "segments": [],
            "broll_target_ratio": contract["broll_target_ratio"],
            "broll_ratio_band": list(contract["broll_ratio_band"]),
            "broll_ratio_planned": 0.0,
            "fast_cuts_under_1s_ratio": 0.0,
            "punch_in_count": 0,
            "keyframe_count": 0,
            "aroll_speed_locked": True,
            "degrade_reason": "没有可用的口播句子，无法规划节奏",
        }

    # 开场 3 秒用 0.45/0.50/0.55/0.60/0.90 的错落步长 -> 5 个切片、4 次硬切；
    # 之后只循环 0.9~1.4 秒的工艺快切步长，保证中位镜长贴近库里的 1.15s。
    steps = (0.45, 0.50, 0.55, 0.60, 0.90, 0.95, 1.35, 1.05, 1.20, 0.90, 1.40)
    bounds = _slice_boundaries(total, steps, cycle_from=5)
    segments = _build_segments(bounds, sents, fps)
    texts = " ".join(s["text"] for s in sents)
    pool = [c for c in cands if _match_score(texts, c) > 0]
    ratio, degrade = _assign_broll(
        segments,
        pool,
        total,
        contract["broll_target_ratio"],
        tuple(contract["broll_ratio_band"]),
        min(3.0, total),
    )
    punch_count = _apply_punch_in(segments, contract["punch_in_scale"])
    fast_ratio = sum(1 for seg in segments if seg["duration_sec"] < FAST_CUT_SEC) / len(segments)
    opening_bounds = [b for b in bounds if b <= min(3.0, total) + 1e-9]
    return {
        "style_id": "style_21",
        "style_name_cn": contract["style_name_cn"],
        "fps": fps,
        "total_duration_sec": round(total, 3),
        "opening_3s_cuts": [round(b, 3) for b in opening_bounds],
        "opening_3s_slice_count": max(0, len(opening_bounds) - 1),
        "opening_mode": contract["opening_mode"],
        "segments": segments,
        "broll_target_ratio": contract["broll_target_ratio"],
        "broll_ratio_band": list(contract["broll_ratio_band"]),
        "broll_ratio_planned": ratio,
        "fast_cuts_under_1s_ratio": round(fast_ratio, 4),
        "punch_in_count": punch_count,
        "keyframe_count": sum(len(seg.get("keyframes") or []) for seg in segments),
        "aroll_speed_locked": _segments_aroll_locked(segments),
        "degrade_reason": degrade,
    }
