#!/usr/bin/env python3
"""
[素材首尾 NG 画面检测 & 切点干净度审计]

用户实测痛点：A-roll / B-roll 的开头常残留开机起拍晃动与对焦模糊，结尾常残留收机甩镜、
人物未站稳转身或暗闪；源窗口不够长时还会冻帧硬拖。本模块只做两件事：

1. `analyze_clip_safe_window()`：逐帧扫描素材首部/尾部（中段作参考系），给出可安全使用的
   in/out 点与命中原因，供选镜排期直接裁掉 NG 头尾。
2. `audit_edl_cut_boundaries()`：复核 04_edl.json 每个镜头的源窗口是否够长（冻帧拖尾），
   并在成片 MP4 的入点 +0.20s / 出点 -0.20s 内采样，抓黑闪与「糊帧 + 剧烈甩镜」。

口径与全仓一致：FFmpeg 解码 RGB -> `common.compute_bt709_luma` 求 BT.709 亮度、复用
`vision_inspector.compute_laplacian_sharpness` 求锐度，不引入任何新依赖。
"""

from __future__ import annotations

import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    compute_bt709_luma,
    ffprobe_media,
    find_bin,
    media_duration_sec,
    parse_video_stream_geometry,
)
from vision_inspector import compute_laplacian_sharpness

import numpy as np

# 采样口径：只抽首尾各 2s + 中段参考窗，不整段解码素材，避免长素材拖慢选镜。
DEFAULT_SAMPLE_FPS = 8.0
FRAME_HEIGHT = 160
HEAD_TAIL_FPS = 10.0

# 判定阈值（全部是「相对本镜中段参考系」的阈值，正常镜头不该被误伤）
DARK_LUMA_ABS = 14.0        # 与 QA 黑帧口径一致：均值 Y<14 视为黑闪
BLUR_RATIO = 0.45           # 锐度掉到中段参考的 45% 以下才算糊帧
MIN_REF_SHARP = 18.0        # 中段本身就没细节（纯色/极糊）时不做糊帧判定
MOTION_MAD_MIN = 0.10       # 相邻采样帧平均绝对差（0..1）超过它才算剧烈甩镜
FREEZE_SHORTFALL_TOL = 0.08  # 源窗口允许的最大短缺（秒），超过即为冻帧拖尾
SHORT_CLIP_SEC = 1.2        # 短素材不强行裁画面，只剪明显黑帧/纯色坏帧
MIN_USABLE_SEC = 0.8


def _frame_size(probe: Dict[str, Any]) -> Tuple[int, int]:
    """按素材真实展示比例取 160p 采样高度，避免竖屏被压扁影响锐度口径。"""
    v_stream = next((s for s in (probe.get("streams") or []) if s.get("codec_type") == "video"), {})
    geo = parse_video_stream_geometry(v_stream)
    w, h = int(geo.get("display_width") or 0), int(geo.get("display_height") or 0)
    if w <= 0 or h <= 0:
        return 90, FRAME_HEIGHT
    target_w = max(64, min(480, int(round(w * FRAME_HEIGHT / float(h) / 2.0) * 2)))
    return target_w, FRAME_HEIGHT


def _empty_scan() -> Dict[str, np.ndarray]:
    return {
        "times": np.zeros(0),
        "luma": np.zeros(0),
        "sharp": np.zeros(0),
        "std": np.zeros(0),
        "motion": np.zeros(0),
    }


def _scan_segment(
    media_path: Path,
    start_sec: float,
    span_sec: float,
    fps: float,
    size: Tuple[int, int],
) -> Dict[str, np.ndarray]:
    """抽一小段逐帧口径：BT.709 亮度、拉普拉斯锐度、画面标准差、相邻帧位移量。

    一次解码一次算完，不把整段素材留在内存里；解码不可用返回空数组，由上层决定中性兜底。
    """
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg or span_sec <= 0.0 or fps <= 0.0:
        return _empty_scan()
    w, h = size
    frame_bytes = w * h * 3
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, start_sec):.3f}", "-i", str(media_path),
        "-t", f"{span_sec:.3f}",
        "-vf", f"fps={fps},scale={w}:{h}",
        "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return _empty_scan()
    assert proc.stdout is not None
    lumas: List[float] = []
    sharps: List[float] = []
    stds: List[float] = []
    motions: List[float] = []
    prev: Optional[np.ndarray] = None
    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            rgb = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 3))
            gray = compute_bt709_luma(rgb.astype(np.float32))
            lumas.append(float(np.mean(gray)))
            stds.append(float(np.std(gray)))
            sharps.append(compute_laplacian_sharpness(gray))
            motions.append(float(np.mean(np.abs(gray - prev))) / 255.0 if prev is not None else 0.0)
            prev = gray
    finally:
        proc.stdout.close()
    proc.wait()
    count = len(lumas)
    return {
        "times": start_sec + np.arange(count, dtype=np.float64) / float(fps),
        "luma": np.asarray(lumas, dtype=np.float64),
        "sharp": np.asarray(sharps, dtype=np.float64),
        "std": np.asarray(stds, dtype=np.float64),
        "motion": np.asarray(motions, dtype=np.float64),
    }


def _combine_scans(*scans: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    usable = [s for s in scans if s["times"].size]
    if not usable:
        return _empty_scan()
    return {key: np.concatenate([s[key] for s in usable]) for key in usable[0]}


def _motion_threshold(mid: Dict[str, np.ndarray]) -> float:
    """甩镜阈值随本镜自身运动基线浮动：正常跟拍不会因为「一直在动」被误判。"""
    motions = mid["motion"][1:] if mid["motion"].size > 1 else np.zeros(0)
    baseline = float(statistics.median(motions)) if motions.size else 0.0
    return max(MOTION_MAD_MIN, baseline * 3.0)


def _first_clean_end(bad: np.ndarray, min_run: int = 3) -> int:
    """从首帧找第一段连续 min_run 帧都干净的位置；找不到就退一步只认单帧干净。"""
    n = int(bad.size)
    for run in (min_run, 1):
        for i in range(n):
            if not bool(bad[i:i + run].any()):
                return i
    return n


def _last_clean_index(bad: np.ndarray, min_run: int = 3) -> int:
    """从尾帧往前找最后一段连续 min_run 帧都干净的位置；找不到就退一步只认单帧干净。"""
    n = int(bad.size)
    for run in (min_run, 1):
        for i in range(n - 1, -1, -1):
            if not bool(bad[max(0, i - run + 1):i + 1].any()):
                return i
    return -1


def _edge_reasons(
    edge: str,
    dark: np.ndarray,
    blur: np.ndarray,
    shake: np.ndarray,
    solid: Optional[np.ndarray] = None,
) -> List[str]:
    reasons: List[str] = []
    if bool(dark.any()):
        reasons.append(f"{edge}_dark_flash")
    if solid is not None and bool(solid.any()):
        reasons.append(f"{edge}_pure_color_bad")
    if bool(blur.any()):
        reasons.append(f"{edge}_blur")
    if bool(shake.any()):
        reasons.append(f"{edge}_motion_shake")
    return reasons


def analyze_clip_safe_window(
    media_path: str,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    head_scan_sec: float = 2.0,
    tail_scan_sec: float = 2.0,
) -> Dict[str, Any]:
    """扫描视频素材首部与尾部的可用边界，切除开机甩镜、过暗黑闪与严重失焦糊帧。

    返回字段见任务契约：safe_in_sec / safe_out_sec / duration_sec / head_ng_sec /
    tail_ng_sec / usable_span_sec / ng_reasons。
    """
    path = Path(media_path)
    probe = ffprobe_media(path)
    duration = media_duration_sec(probe)
    fps = max(1.0, float(sample_fps))
    size = _frame_size(probe)

    head_span = max(0.0, min(float(head_scan_sec), duration))
    head = _scan_segment(path, 0.0, head_span, fps, size)
    tail_span = max(0.0, min(float(tail_scan_sec), duration))
    tail_start = max(0.0, duration - tail_span)
    tail = _scan_segment(path, tail_start, tail_span, fps, size)
    mid_span = max(0.4, min(2.0, duration * 0.3))
    mid_start = max(0.0, min(duration - mid_span, duration / 2.0 - mid_span / 2.0))
    mid = _scan_segment(path, mid_start, max(0.0, min(mid_span, duration - mid_start)), fps, size)

    everything = _combine_scans(head, tail, mid)
    reference = mid if mid["times"].size >= 2 else everything
    if not reference["times"].size:
        return {
            "safe_in_sec": 0.0,
            "safe_out_sec": round(duration, 3),
            "duration_sec": round(duration, 3),
            "head_ng_sec": 0.0,
            "tail_ng_sec": 0.0,
            "usable_span_sec": round(duration, 3),
            "ng_reasons": [],
        }

    all_luma = everything["luma"]
    all_sharp = everything["sharp"]
    mid_luma = float(statistics.median(mid["luma"])) if mid["times"].size else float(statistics.median(all_luma))
    mid_sharp = float(statistics.median(mid["sharp"])) if mid["times"].size else 0.0
    # 参考锐度：中段清晰就用中段；NG 太长把中段污染时，用全片最清晰的一档兜底，
    # 免得「一整片都糊」被当成参考系，反而把真正的糊帧判成正常（0.8 系数压住单帧锐度尖峰）。
    if all_sharp.size >= 2:
        top_n = max(2, int(round(all_sharp.size * 0.05)))
        top_sharp = float(statistics.median(np.sort(all_sharp)[-top_n:]))
    else:
        top_sharp = mid_sharp
    ref_sharp = max(mid_sharp, top_sharp * 0.8)
    clear_mask = all_sharp >= max(MIN_REF_SHARP, ref_sharp * 0.6)
    ref_luma = float(statistics.median(all_luma[clear_mask])) if bool(clear_mask.any()) else mid_luma
    motion_thresh = _motion_threshold(mid if mid["times"].size >= 2 else everything)
    dark_thresh = max(9.0, min(30.0, ref_luma * 0.28))
    short_clip = duration <= SHORT_CLIP_SEC

    def _flags(scan: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        luma, sharp, motion = scan["luma"], scan["sharp"], scan["motion"]
        dark = luma < (max(6.0, min(12.0, ref_luma * 0.2)) if short_clip else dark_thresh)
        if short_clip:
            # 短素材只认「明显黑」与「纯色坏帧」，不因轻微运动/略糊就裁掉画面
            solid = (scan["std"] < 2.0) & (np.abs(luma - ref_luma) > 20.0)
            return {"dark": dark, "solid": solid, "blur": np.zeros(luma.size, bool), "shake": np.zeros(luma.size, bool)}
        blur = (sharp < ref_sharp * BLUR_RATIO) if ref_sharp >= MIN_REF_SHARP else np.zeros(luma.size, bool)
        # 同一处 NG 只报主因：黑帧不再叠加糊帧；糊帧本身的帧间差不再算甩镜
        blur = blur & ~dark
        recovery = np.zeros(luma.size, bool)
        recovery[1:] = dark[:-1] & ~dark[1:]
        # 从黑闪切回正常画面的那一帧，帧间差天然接近 1.0，是曝光恢复不是甩镜
        shake = (motion >= motion_thresh) & ~dark & ~blur & ~recovery
        return {"dark": dark, "solid": np.zeros(luma.size, bool), "blur": blur, "shake": shake}

    head_flags = _flags(head) if head["times"].size else None
    tail_flags = _flags(tail) if tail["times"].size else None

    head_bad = None
    if head_flags is not None:
        bad = head_flags["dark"] | head_flags["solid"] | head_flags["blur"] | head_flags["shake"]
        head_bad = bad
        first_good = _first_clean_end(bad)
        safe_in = float(head["times"][first_good]) if first_good < head["times"].size else head_span
    else:
        first_good = 0
        safe_in = 0.0

    tail_bad = None
    if tail_flags is not None:
        bad = tail_flags["dark"] | tail_flags["solid"] | tail_flags["blur"] | tail_flags["shake"]
        tail_bad = bad
        last_good = _last_clean_index(bad)
        if last_good >= 0:
            safe_out = min(duration, float(tail["times"][last_good]) + 1.0 / fps)
        else:
            safe_out = tail_start
    else:
        last_good = len(tail["times"]) - 1
        safe_out = duration

    safe_in = max(0.0, min(safe_in, duration))
    safe_out = max(0.0, min(safe_out, duration))
    if safe_out < safe_in:
        safe_in, safe_out = 0.0, duration

    # 防误杀兜底 1：短素材不把画面裁没
    if short_clip and duration > 0 and safe_out - safe_in < duration * 0.6:
        head_trim = safe_in
        tail_trim = duration - safe_out
        keep_ratio = 0.6
        scale = (duration * keep_ratio) / max(1e-6, head_trim + tail_trim)
        safe_in = max(0.0, head_trim * scale)
        safe_out = min(duration, duration - tail_trim * scale)
    # 防误杀兜底 2：正常素材裁完太短就保守回退，至少留下 min(0.6d, d-0.2)。
    # 采样步长 1/fps 会让 usable 有 ±1 帧的量化误差，故 0.8s 判据放宽一个采样间隔再触发。
    elif duration >= SHORT_CLIP_SEC and safe_out - safe_in < MIN_USABLE_SEC - 1.0 / fps - 1e-6:
        target = min(duration * 0.6, max(0.0, duration - 0.2))
        head_trim = safe_in
        tail_trim = duration - safe_out
        trim_total = head_trim + tail_trim
        if target > safe_out - safe_in and trim_total > 1e-6:
            scale = max(0.0, (duration - target) / trim_total)
            safe_in = max(0.0, head_trim * scale)
            safe_out = min(duration, duration - tail_trim * scale)

    safe_in = round(safe_in, 3)
    safe_out = round(safe_out, 3)
    duration_out = round(duration, 3)
    head_ng = safe_in
    tail_ng = round(max(0.0, duration_out - safe_out), 3)
    reasons: List[str] = []
    if head_bad is not None and head_ng > 0.01:
        reasons.extend(_edge_reasons(
            "head",
            head_flags["dark"][:first_good],
            head_flags["blur"][:first_good],
            head_flags["shake"][:first_good],
            head_flags["solid"][:first_good],
        ))
    if tail_bad is not None and tail_ng > 0.01 and last_good + 1 < tail_bad.size:
        reasons.extend(_edge_reasons(
            "tail",
            tail_flags["dark"][last_good + 1:],
            tail_flags["blur"][last_good + 1:],
            tail_flags["shake"][last_good + 1:],
            tail_flags["solid"][last_good + 1:],
        ))
    return {
        "safe_in_sec": safe_in,
        "safe_out_sec": safe_out,
        "duration_sec": duration_out,
        "head_ng_sec": head_ng,
        "tail_ng_sec": tail_ng,
        "usable_span_sec": round(max(0.0, safe_out - safe_in), 3),
        "ng_reasons": list(dict.fromkeys(reasons)),
    }


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip_timing(clip: Dict[str, Any], fps: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """镜头在成片时间线上的 [in, out] 与时长；兼容只写帧号的旧 EDL。"""
    tin = _as_float(clip.get("timeline_in_sec"))
    tout = _as_float(clip.get("timeline_out_sec"))
    dur = _as_float(clip.get("duration_sec"))
    if tin is None and _as_float(clip.get("timeline_in_frame")) is not None:
        tin = float(clip["timeline_in_frame"]) / max(1.0, fps)
    if tout is None and _as_float(clip.get("timeline_out_frame")) is not None:
        tout = float(clip["timeline_out_frame"]) / max(1.0, fps)
    if dur is None and tin is not None and tout is not None:
        dur = tout - tin
    return tin, tout, dur


def _clip_id(clip: Dict[str, Any], index: int) -> str:
    return str(clip.get("clip_id") or clip.get("id") or f"clip_{index + 1:03d}")


def _median_at(scan: Dict[str, np.ndarray], indices: np.ndarray, key: str) -> float:
    if indices.size == 0:
        return 0.0
    return float(statistics.median(scan[key][indices]))


def _find_edge_ng(
    scan: Dict[str, np.ndarray],
    clips: List[Dict[str, Any]],
    fps: float,
    window_sec: float,
) -> List[Dict[str, Any]]:
    """逐镜在入点 +window / 出点 -window 内采样，抓黑闪与「糊帧 + 剧烈甩镜」。"""
    times = scan["times"]
    if times.size < 2:
        return []
    hits: List[Dict[str, Any]] = []
    for index, clip in enumerate(clips):
        tin, tout, dur = _clip_timing(clip, fps)
        if tin is None:
            continue
        if tout is None:
            tout = tin + (dur or 0.0)
        if dur is None or dur <= 0:
            dur = tout - tin
        if dur <= 0:
            continue
        if tin > float(times[-1]) + 0.1 or tout < float(times[0]) - 0.1:
            # EDL 里的镜头不在成片采样范围内（如成片被截短），不能拿别的镜头画面顶替判 NG
            continue
        clip_mask = (times >= tin - 0.05) & (times <= tout + 0.05)
        clip_idx = np.flatnonzero(clip_mask)
        mid_idx = np.flatnonzero((times >= tin + dur * 0.3) & (times <= tout - dur * 0.3))
        if mid_idx.size < 2:
            mid_idx = clip_idx
        if mid_idx.size == 0:
            continue
        mid_luma = _median_at(scan, mid_idx, "luma")
        mid_sharp = _median_at(scan, mid_idx, "sharp")
        for edge in ("head", "tail"):
            lo, hi = (tin, tin + window_sec) if edge == "head" else (tout - window_sec, tout)
            edge_idx = np.flatnonzero((times >= lo) & (times <= hi))
            if edge_idx.size == 0:
                target = (lo + hi) / 2.0
                nearest = int(np.argmin(np.abs(times - target)))
                if abs(float(times[nearest]) - target) > max(0.25, window_sec):
                    continue
                edge_idx = np.asarray([nearest], dtype=np.int64)
            luma = scan["luma"][edge_idx]
            sharp = scan["sharp"][edge_idx]
            motion = scan["motion"][edge_idx]
            dark_pos = np.flatnonzero((luma < DARK_LUMA_ABS) & (mid_luma > 35.0))
            blur_pos = np.flatnonzero((sharp < mid_sharp * 0.5) & (mid_sharp >= MIN_REF_SHARP))
            # 入点第一帧的帧间差来自「上一个镜头 -> 本镜」的硬切，不是本镜甩镜，不能算违例
            motion_scope = motion[1:] if edge == "head" and motion.size > 1 else motion
            whip = float(motion_scope.max()) >= MOTION_MAD_MIN if motion_scope.size else False
            if dark_pos.size:
                hit_at = float(times[edge_idx[int(dark_pos[0])]])
                hits.append({
                    "clip_id": _clip_id(clip, index),
                    "edge": edge,
                    "time_sec": round(hit_at, 3),
                    "reason": f"{edge}_dark_flash",
                })
            elif blur_pos.size and whip:
                worst = int(blur_pos[np.argmin(sharp[blur_pos])])
                hits.append({
                    "clip_id": _clip_id(clip, index),
                    "edge": edge,
                    "time_sec": round(float(times[edge_idx[worst]]), 3),
                    "reason": f"{edge}_blur_whip",
                })
    return hits


def audit_edl_cut_boundaries(
    video_path: str,
    edl: Dict[str, Any],
    window_sec: float = 0.20,
) -> Dict[str, Any]:
    """结合成片 MP4 与 04_edl.json 逐镜检查入出点干脆度。

    返回字段见任务契约：ok / checked_clips / freeze_tail_clips / ng_boundary_clips / summary。
    """
    edl = edl if isinstance(edl, dict) else {}
    clips = [c for c in (edl.get("clips") or []) if isinstance(c, dict)]
    fps = _as_float(edl.get("fps")) or 30.0

    freeze_tail_clips: List[Dict[str, Any]] = []
    for index, clip in enumerate(clips):
        dur = _as_float(clip.get("duration_sec"))
        src_in = _as_float(clip.get("source_in_sec"))
        src_out = _as_float(clip.get("source_out_sec"))
        if dur is None or src_in is None or src_out is None:
            continue
        shortfall = dur - (src_out - src_in)
        if shortfall > FREEZE_SHORTFALL_TOL + 1e-6:
            tin = _as_float(clip.get("timeline_in_sec"))
            if tin is None and _as_float(clip.get("timeline_in_frame")) is not None:
                tin = float(clip["timeline_in_frame"]) / max(1.0, fps)
            freeze_tail_clips.append({
                "clip_id": _clip_id(clip, index),
                "timeline_in_sec": round(float(tin or 0.0), 3),
                "shortfall_sec": round(shortfall, 3),
            })

    ng_boundary_clips: List[Dict[str, Any]] = []
    sampled = False
    path = Path(video_path)
    if clips and path.is_file():
        try:
            probe = ffprobe_media(path)
            duration = media_duration_sec(probe)
            size = _frame_size(probe)
            scan = _scan_segment(path, 0.0, duration, HEAD_TAIL_FPS, size)
            sampled = scan["times"].size >= 2
            if sampled:
                ng_boundary_clips = _find_edge_ng(scan, clips, fps, max(0.05, float(window_sec)))
        except (OSError, RuntimeError, ValueError):
            sampled = False

    parts: List[str] = [f"检查 {len(clips)} 个镜头"]
    if freeze_tail_clips:
        first = freeze_tail_clips[0]
        parts.append(
            f"结尾冻帧拖尾 {len(freeze_tail_clips)} 处（{first['clip_id']} 源窗口短缺 {first['shortfall_sec']:.2f}s）"
        )
    else:
        parts.append("结尾冻帧拖尾 0 处")
    if ng_boundary_clips:
        first_ng = ng_boundary_clips[0]
        edge_cn = "入点" if first_ng["edge"] == "head" else "出点"
        parts.append(f"切点NG帧 {len(ng_boundary_clips)} 处（{first_ng['clip_id']} {edge_cn} {first_ng['reason']}）")
    else:
        parts.append("切点NG帧 0 处")
    if clips and not sampled:
        parts.append("成片采样不可用")
    ok = not freeze_tail_clips and not ng_boundary_clips
    return {
        "ok": ok,
        "checked_clips": len(clips),
        "freeze_tail_clips": freeze_tail_clips,
        "ng_boundary_clips": ng_boundary_clips,
        "sampled": sampled,
        "summary": "：".join([parts[0], "、".join(parts[1:])]),
    }
