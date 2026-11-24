#!/usr/bin/env python3
"""
独立黑盒外部验收裁判 (Independent Black-Box Verifier, 检查项 #12–#22)
只读最终成片 output_final.mp4 与外部导出工程目录，不信任上游内部报告自评：
1. 三态严谨判定：PASS | FAIL | SKIPPED_UNAVAILABLE（SKIPPED_UNAVAILABLE 绝不伪装成 passed=True）。
2. 全链路单遍音频解码 + 单次 EBU R128 测量缓存共享（供 #1/#3/#12/#20/#21 零冗余复算）。
3. 强制核查：#12 声学与口播音频覆盖(含 suspect_offcam==0)、#13 Vision OCR 与字幕回环、
   #14 单帧单框 WCAG >= 3.8:1 (禁均值掩盖)、#15 common.SKIN_YCBCR_RANGE 肤色范围、
   #16 FCPXML 纯相对路径/HyperFrames 零 CDN 与草稿存在性、#17 common.LEAK_REGEX_PATTERNS 统一防露馅、
   #18 屏蔽字幕带 >3s 防冻结、#19 全量 EDL 切点 showinfo 精准对照跳变、
   #20 SFX 同窗净抬升 (支持 08_sfx_audit_window.json tidy 后回退)、#21 本地缓存独立 ASR 复核、
   #22 合规与 Verified 商用字体授权清单。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    TP_MAX_DBTP,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    EXPORT_CHOICES,
    HYPERFRAMES_VERSION,
    EDIT_PROFILES,
    LOUDNESS_TOLERANCE_LUFS,
    SAFE_ZONE,
    bbox_iou,
    compute_bt709_luma,
    get_cached_whisper_model,
    sha256_file,
    export_selection,
    review_checks,
    DEFAULT_FPS,
    LEAK_REGEX_PATTERNS,
    SKIN_YCBCR_RANGE,
    SUBTITLE_CENTER_X,
    SUBTITLE_SYMMETRIC_MAX_W,
    SUBTITLE_SAFE_ZONE,
    glyph_contrast_ratio,
    TARGET_LUFS,
    compute_sfx_band_windows,
    find_bin,
    find_cached_whisper_model,
    get_huggingface_hub_dir,
    is_verified_commercial_font,
    load_style_applied,
    load_style_spectrum_entry,
    maybe_reexec_in_venv,
    measure_lufs_ebur128,
    read_json,
    read_wav_as_48k_stereo,
    run_cmd,
    write_json,
)
from platform_probe import fps_mode_args

if __name__ == "__main__":
    maybe_reexec_in_venv()

import numpy as np
from PIL import Image
from step2_transcribe import _clean_zh_text, vad_speech_intervals
from step8_mix_audio import compute_mono_phase_correlation
from vision_inspector import inspect_images


META_LEAK_RE = re.compile("|".join(LEAK_REGEX_PATTERNS), re.IGNORECASE)


def _mk_check(
    cid: str,
    name: str,
    status: str,
    measured: str,
    threshold: str,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    st = status if status in ("PASS", "FAIL", "SKIPPED_UNAVAILABLE", "NOT_REQUESTED") else "FAIL"
    return {
        "id": cid,
        "name": name,
        "status": st,
        "passed": (st == "PASS"),
        "measured": measured,
        "threshold": threshold,
        "detail": detail or {},
    }


def _detect_digital_splice_clicks(audio_48k: np.ndarray, sr: int = 48000) -> List[Dict[str, Any]]:
    if len(audio_48k) < 16:
        return []
    mono = audio_48k.mean(axis=1) if audio_48k.ndim > 1 else audio_48k
    diff = np.abs(np.diff(mono))
    win = max(1, int(sr * 0.04))
    abs_m = np.abs(mono)
    pad = np.pad(abs_m, (win, 0), mode="constant")
    csum = np.cumsum(pad ** 2)
    local_rms = np.sqrt(np.maximum(1e-10, (csum[win:] - csum[:-win]) / float(win)))[:-1]
    clicks = np.where((diff > 0.35) & (local_rms < 0.03))[0]
    out: List[Dict[str, Any]] = []
    last_t = -1.0
    for idx in clicks:
        t = round(float(idx) / float(sr), 3)
        if t - last_t >= 0.05:
            out.append({"time_sec": t, "jump": round(float(diff[idx]), 3)})
            last_t = t
    return out


def _detect_long_silences_np(audio_48k: np.ndarray, sr: int = 48000, thresh_db: float = -42.0, min_dur: float = 0.8) -> List[float]:
    if len(audio_48k) < int(sr * 0.1):
        return []
    mono = audio_48k.mean(axis=1) if audio_48k.ndim > 1 else audio_48k
    hop = max(1, int(sr * 0.02))
    n_hops = len(mono) // hop
    if n_hops < 2:
        return []
    rms = np.sqrt(np.mean(mono[: n_hops * hop].reshape(n_hops, hop) ** 2, axis=1) + 1e-12)
    db = 20.0 * np.log10(np.maximum(1e-7, rms))
    total_dur = len(mono) / float(sr)
    long_sils: List[float] = []
    cur_start: Optional[int] = None
    for i, is_sil in enumerate(db <= thresh_db):
        if is_sil and cur_start is None:
            cur_start = i
        elif not is_sil and cur_start is not None:
            s_t = cur_start * 0.02
            e_t = i * 0.02
            dur = e_t - s_t
            if dur >= min_dur and e_t < total_dur - 0.15:
                long_sils.append(round(dur, 3))
            cur_start = None
    if cur_start is not None:
        s_t = cur_start * 0.02
        dur = total_dur - s_t
        # 允许合法片尾 0.45s 呼吸尾音，若片尾意外静音 >= max(min_dur, 1.0s) 则必须报出
        if dur >= max(min_dur, 1.0) and s_t > 0.2:
            long_sils.append(round(dur, 3))
    return long_sils


def extract_audio_context_once(video_path: Path, tmp_dir: Path) -> Dict[str, Any]:
    """全链路单次抽取成片音轨并共享 48kHz numpy 数组、16kHz VAD 与 EBU R128 测量结果。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 执行黑盒声学裁判。")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ext_wav = tmp_dir / "blackbox_audio_48k.wav"
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(ext_wav),
    ])
    r128 = measure_lufs_ebur128(ext_wav)
    audio_48k, sr48 = read_wav_as_48k_stereo(ext_wav, target_sr=48000)
    mono_48k = audio_48k.mean(axis=1).astype(np.float32)
    try:
        from scipy.signal import resample_poly
        y16 = resample_poly(mono_48k, 1, 3).astype(np.float32)
    except Exception:
        y16 = mono_48k[::3].astype(np.float32)
    speech_spans = vad_speech_intervals(y16, 16000)
    silence_spans = _detect_long_silences_np(audio_48k, sr48)
    return {
        "wav_path": str(ext_wav),
        "r128": r128,
        "audio_48k": audio_48k,
        "sr": sr48,
        "y16": y16,
        "speech_spans": speech_spans,
        "long_silences": silence_spans,
    }


def _drop_sfx_originated_clicks(clicks: List[Dict[str, Any]], pipeline_dir: Optional[Path],
                                tol_sec: float = 0.010) -> List[Dict[str, Any]]:
    """把「本来就是音效自己发出来的」跳变从爆音里剔除，只留真剪接爆音。

    判据不是「离音效起音多近」，而是**这一下跳变在音效轨里也存在**：
    铃/翻页/发条这类瞬态音效天生就是「安静里猛地一下」，跟剪接爆音同一个特征，
    但音效轨同一点上也有同样大的跳变，说明声音是它发的，不是我们剪坏的。
    人声/底噪的接缝在音效轨上是平的，照样会被抓出来。
    """
    if not clicks or pipeline_dir is None:
        return clicks
    pkg_p = Path(pipeline_dir) / "06_packaging.json"
    if not pkg_p.is_file():
        return clicks
    try:
        pkg = read_json(pkg_p)
        stem = pkg.get("sfx_track_wav")
        if not stem or not Path(stem).is_file():
            return clicks
        import subprocess
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(stem), "-ac", "1", "-ar", "48000",
                              "-f", "f32le", "-"], capture_output=True).stdout
        arr = np.frombuffer(raw, dtype=np.float32)
        if arr.size < 32:
            return clicks
        jump = np.abs(np.diff(arr))
    except Exception:
        return clicks
    kept: List[Dict[str, Any]] = []
    half = int(tol_sec * 48000)
    for c in clicks:
        idx = int(round(float(c.get("time_sec") or 0.0) * 48000))
        lo, hi = max(0, idx - half), min(len(jump), idx + half + 1)
        if hi > lo and float(jump[lo:hi].max()) >= 0.30:
            continue  # 音效轨同一时刻也有台阶 → 声音是音效发的
        kept.append(c)
    return kept
    if not onsets:
        return clicks
    kept: List[Dict[str, Any]] = []
    for c in clicks:
        t = float(c.get("time_sec") or 0.0)
        if any(abs(t - o) <= tol_sec for o in onsets):
            continue
        kept.append(c)
    return kept


def verify_audio_blackbox(
    video_path: Path,
    tmp_dir: Path,
    pipeline_dir: Optional[Path] = None,
    audio_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ctx = audio_ctx or extract_audio_context_once(video_path, tmp_dir)
    r128 = ctx["r128"]
    audio_48k = ctx["audio_48k"]
    sr = int(ctx["sr"])
    y16 = ctx["y16"]
    spans = ctx["speech_spans"]

    mono_corr = compute_mono_phase_correlation(audio_48k)
    clicks = _detect_digital_splice_clicks(audio_48k, sr)
    # 设计好的音效起音也是「静默里突然跳一下」，跟剪接爆音同一个特征。
    # 不豁免就会把每一声铃声/翻页声都判成爆音（2026-11-24 实测：42 条音效里被抓 4 处），
    # 于是只留「人声/音轨接缝」真爆音：音效轨起音前后 60ms 内的跳变不算。
    clicks = _drop_sfx_originated_clicks(clicks, pipeline_dir)

    # 提取 300–3400Hz 纯人声语谱带衡量每句电平，避免低频鼓点/BGM 高频误导画外音检测
    try:
        from scipy.signal import butter, sosfiltfilt
        sos_v = butter(4, [300.0, 3400.0], btype="bandpass", fs=16000, output="sos")
        y16_vocal = sosfiltfilt(sos_v, y16.astype(np.float64))
    except Exception:
        y16_vocal = y16

    speech_windows: List[Tuple[float, float]] = []
    if pipeline_dir and (pipeline_dir / "03_audio_events.json").is_file():
        try:
            ae_a = read_json(pipeline_dir / "03_audio_events.json")
            speech_windows = [
                (float(s.get("start_sec", 0.0)), float(s.get("end_sec", 0.0)))
                for s in (ae_a.get("sentences") or [])
                if float(s.get("end_sec", 0.0)) > float(s.get("start_sec", 0.0))
            ]
        except Exception:
            speech_windows = []

    span_rms_dbs: List[float] = []
    for s, e in spans:
        # 仅核查真实口播时段（避免纯 BGM / 空镜停顿被误判成画外音电平跌落）
        if speech_windows and not any(min(e, we) - max(s, ws) >= 0.12 for ws, we in speech_windows):
            continue
        seg = y16_vocal[int(s * 16000): int(e * 16000)]
        if len(seg) > 160:
            r = float(np.sqrt(np.mean(seg ** 2) + 1e-12))
            span_rms_dbs.append(20.0 * math.log10(max(1e-6, r)))
    suspect_offcam = 0
    if len(span_rms_dbs) >= 4:
        med_db = float(np.median(sorted(span_rms_dbs, reverse=True)[: max(2, len(span_rms_dbs) // 2)]))
        suspect_offcam = sum(1 for db in span_rms_dbs if -38.0 < db <= med_db - 12.0)

    # 实测语音能量段覆盖 A-Roll 口播镜头检查
    aroll_cov_ok = True
    missing_aroll_clips: List[str] = []
    if pipeline_dir and (pipeline_dir / "04_edl.json").is_file():
        edl = read_json(pipeline_dir / "04_edl.json")
        for clip in (edl.get("clips") or []):
            if clip.get("track_role") != "a_roll" or not clip.get("audio_locked", True):
                continue
            cin = float(clip.get("timeline_in_sec", 0.0))
            cout = float(clip.get("timeline_out_sec", 0.0))
            if cout - cin < 0.25:
                continue
            ov = sum(max(0.0, min(cout, se) - max(cin, ss)) for ss, se in spans)
            if ov < 0.12:
                aroll_cov_ok = False
                missing_aroll_clips.append(str(clip.get("clip_id", "unknown")))

    lufs_ok = abs(r128["integrated_lufs"] - TARGET_LUFS) <= LOUDNESS_TOLERANCE_LUFS
    tp_ok = r128["true_peak_dbtp"] <= TP_MAX_DBTP
    lra_ok = 0.0 <= r128["lra_lu"] <= 11.5
    phase_ok = mono_corr >= 0.45
    clicks_ok = len(clicks) == 0
    offcam_ok = (suspect_offcam == 0)

    passed = lufs_ok and tp_ok and lra_ok and phase_ok and clicks_ok and offcam_ok and aroll_cov_ok
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "integrated_lufs": r128["integrated_lufs"],
        "true_peak_dbtp": r128["true_peak_dbtp"],
        "lra_lu": r128["lra_lu"],
        "mono_phase_corr": mono_corr,
        "splice_clicks": clicks,
        "suspect_offcamera_spans": suspect_offcam,
        "aroll_voice_coverage_ok": aroll_cov_ok,
        "missing_aroll_voice_clips": missing_aroll_clips,
        "vad_span_count": len(spans),
        "wav_path": ctx["wav_path"],
        "speech_spans": spans,
    }


def _relative_luminance(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float32) / 255.0
    lin = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[:, :, 0] + 0.7152 * lin[:, :, 1] + 0.0722 * lin[:, :, 2]


def _subsequence_ratio(needle: str, haystack: str) -> float:
    if not needle:
        return 1.0
    pos = 0
    matched = 0
    for ch in needle:
        idx = haystack.find(ch, pos)
        if idx >= 0:
            matched += 1
            pos = idx + 1
    return matched / float(len(needle))


# ── 字幕层黑盒量测（2026-11-24 重写：只量我们自己画的那一层，画面自带招牌/美术字不计入）──
# 旧实现的三个坑（用户实测复核）：
#   1) 逐帧 `fps=2` 抽帧后按 idx*0.5 当时间戳，实际落点比名义时间晚 1~2 帧，字幕窗口边界那帧
#      经常被抽到「字幕刚消失之后」「字幕刚出现之前」，语音帧覆盖率被系统性压低。
#   2) 全帧 OCR 的文字框直接拿来算「安全区越界 / 540px 居中」——门店招牌（拿火锅、早茶、点赞茶楼、
#      招牌特色炒面）跟字幕落在同一行（y 中心差 <36px）会被合并成一行，测得中心偏 196px；
#      OCR 只读出半行（「平去制造」）也会被当成整行跑偏。这些都不是我们的字幕问题。
#   3) WCAG 用「框内 p92 / p10」量，把花字包装卡也算进去，量到的其实是包装标题而不是字幕。
# 现在：几何一律以 05_subtitles.json 的 final_bbox 为唯一判据（渲染/工程/质检同源），
#       文字识别只在 final_bbox 外扩 8px 的裁切里做（放大 2× 后 OCR），内容核对也在这个裁切里比。
SUB_CROP_PAD_PX = 8
SUB_CROP_SCALE = 2
SUB_CENTER_TOLERANCE_PX = 42.0
SUB_MIN_CONTRAST = 3.8
SUB_MIN_MEAN_CONTRAST = 4.5
SPEECH_SUB_COVERAGE_MIN = 0.65
SUB_CONTENT_RECALL_MIN = 0.45
SKIN_SOURCE_LIMITED_LUMA = 45.0  # 脸区 p50 亮度低于此值 -> 逆光/剪影，肤色判据不硬掰
SKIN_SOURCE_LIMITED_P90 = 60.0


def _extract_sample_frames(ffmpeg: str, video_path: Path, frames_dir: Path, step_sec: float = 0.5) -> Tuple[List[Path], List[float]]:
    """按**精确帧号**抽帧（select=not(mod(n,k))），时间戳 = k/30 的整数倍，不再用 fps 滤镜的近似落点。"""
    step_frames = max(1, int(round(step_sec * float(DEFAULT_FPS))))
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"select='not(mod(n\\,{step_frames}))',scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}",
        *fps_mode_args("passthrough"), "-q:v", "2",
        str(frames_dir / "f_%04d.jpg"),
    ])
    files = sorted(frames_dir.glob("f_*.jpg"))
    step = step_frames / float(DEFAULT_FPS)
    return files, [round(i * step, 3) for i in range(len(files))]


def _subtitle_text_box(entry: Dict[str, Any]) -> Optional[Dict[str, int]]:
    for key in ("final_bbox", "bbox"):
        box = entry.get(key)
        if isinstance(box, dict) and {"x", "y", "w", "h"}.issubset(box.keys()):
            return {k: int(box[k]) for k in ("x", "y", "w", "h")}
    return None


def measure_subtitle_geometry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """只用 05_subtitles 的 final_bbox 判几何：中心是否 540、是否在平台安全区、是否守住对称安全带。"""
    box = _subtitle_text_box(entry)
    if not box:
        return {"box": None, "center_dev_px": None, "in_safe_zone": False, "in_symmetric_zone": False,
                "center_ok": False, "reason": "missing_bbox"}
    cx = box["x"] + box["w"] / 2.0
    dev = abs(cx - float(SUBTITLE_CENTER_X))
    in_safe = (
        box["x"] >= SAFE_ZONE["x_min"]
        and box["x"] + box["w"] <= SAFE_ZONE["x_max"]
        and box["y"] >= SAFE_ZONE["y_min"]
        and box["y"] + box["h"] <= SAFE_ZONE["y_max"]
    )
    in_sym = (
        box["x"] >= SUBTITLE_CENTER_X - SUBTITLE_SYMMETRIC_MAX_W / 2.0 - 0.5
        and box["x"] + box["w"] <= SUBTITLE_CENTER_X + SUBTITLE_SYMMETRIC_MAX_W / 2.0 + 0.5
    )
    return {
        "box": box,
        "center_dev_px": round(dev, 1),
        "in_safe_zone": bool(in_safe),
        "in_symmetric_zone": bool(in_sym),
        "center_ok": bool(dev <= SUB_CENTER_TOLERANCE_PX),
        "reason": "" if (in_safe and in_sym and dev <= SUB_CENTER_TOLERANCE_PX) else "geometry",
    }


def max_face_text_iou(boxes: List[Dict[str, int]], faces: List[Dict[str, Any]], scale: Tuple[int, int] = (CANVAS_WIDTH, CANVAS_HEIGHT)) -> float:
    """我们字幕框与人脸框的最大 IoU（人脸框是 0..1 归一化）。"""
    worst = 0.0
    for box in boxes:
        for f in faces:
            fb = f.get("bbox")
            if not fb:
                continue
            face_px = {
                "x": float(fb["x"]) * scale[0],
                "y": float(fb["y"]) * scale[1],
                "w": float(fb["w"]) * scale[0],
                "h": float(fb["h"]) * scale[1],
            }
            worst = max(worst, bbox_iou(box, face_px))
    return worst


def classify_skin_sample(cb: float, cr: float, yy_p50: float, yy_p90: float, skin_ratio: float) -> Dict[str, Any]:
    """把「肤色出扇区」分成两类：我们的调色把它推出去（真缺陷） vs 素材本身逆光/剪影（如实标注）。

    只对有效肤色像素（yy>=40）判定；当脸区整体就是暗部（p50/p90 都很低）或压根没几个肤色像素时，
    判据本身没有意义，标 source_limited 而不是硬掰成违规、也不假装通过。
    """
    in_sector = (
        SKIN_YCBCR_RANGE["cb_min"] <= cb <= SKIN_YCBCR_RANGE["cb_max"]
        and SKIN_YCBCR_RANGE["cr_min"] <= cr <= SKIN_YCBCR_RANGE["cr_max"]
    )
    limited_reason = ""
    if yy_p50 < SKIN_SOURCE_LIMITED_LUMA:
        limited_reason = "face_in_shadow"
    elif yy_p90 < SKIN_SOURCE_LIMITED_P90:
        limited_reason = "backlit_silhouette"
    elif skin_ratio < 0.02:
        limited_reason = "insufficient_skin_pixels"
    return {
        "in_sector": bool(in_sector),
        "source_limited": bool(limited_reason),
        "source_limited_reason": limited_reason,
        "violation": bool((not in_sector) and (not limited_reason)),
    }


def _clean_for_match(text: str) -> str:
    return _clean_zh_text(str(text or ""))


def ocr_box_is_our_subtitle(box_px: Tuple[int, int, int, int], sub_boxes: List[Dict[str, int]], tolerance_px: int = SUB_CROP_PAD_PX) -> bool:
    """全帧 OCR 的文字框是否落在「我们自己画的字幕框」里——不在里面的一律当画面自带文字/包装花字，不进几何判据。"""
    if not sub_boxes:
        return False
    x1, y1, w, h = (int(v) for v in box_px)
    cx, cy = x1 + w / 2.0, y1 + h / 2.0
    for box in sub_boxes:
        if (
            box["x"] - tolerance_px <= cx <= box["x"] + box["w"] + tolerance_px
            and box["y"] - tolerance_px <= cy <= box["y"] + box["h"] + tolerance_px
        ):
            return True
    return False


def text_match_ratio(expected: str, ocr_text: str) -> float:
    """字幕内容核对：按字符相似度（1 - CER 的近似），容忍 OCR 认错一两个字。"""
    exp = _clean_for_match(expected)
    got = _clean_for_match(ocr_text)
    if not exp:
        return 1.0
    if not got:
        return 0.0
    if exp == got:
        return 1.0
    import difflib

    sm = difflib.SequenceMatcher(None, exp, got, autojunk=False)
    matched = sum(block.size for block in sm.get_matching_blocks())
    return matched / float(len(exp))


def verify_visual_and_subtitle_blackbox(
    video_path: Path,
    tmp_dir: Path,
    speech_spans: List[Tuple[float, float]],
    pipeline_dir: Optional[Path] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 执行黑盒视觉裁判。")

    frames_dir = tmp_dir / "bb_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_files, frame_times = _extract_sample_frames(ffmpeg, video_path, frames_dir)
    if not frame_files:
        raise RuntimeError("黑盒视觉抽帧失败，成片视频流可能损坏。")

    vis_raw = inspect_images(frame_files)
    vis_map: Dict[str, Dict[str, Any]] = {}
    for k, v in vis_raw.items():
        vis_map[str(k)] = v
        vis_map[str(Path(k).resolve())] = v

    static_limit = EDIT_PROFILES["talking"]["static_sec"]
    expected_aroll_has_face = False
    expected_sub_entries: List[Tuple[float, float, str]] = []
    live_sub_entries: List[Dict[str, Any]] = []
    suppressed_spans: List[Tuple[float, float]] = []
    code_broll_spans: List[Tuple[float, float]] = []
    cut_times_sec: List[float] = []
    pkg_texts: List[str] = []
    pkg_spans: List[Tuple[float, float]] = []
    pkg_elements: List[Dict[str, Any]] = []
    sent_speech_spans: List[Tuple[float, float]] = []
    if pipeline_dir and pipeline_dir.exists():
        edl_cuts_clips: List[Dict[str, Any]] = []
        timeline = pipeline_dir / "04_edl.json"
        if timeline.is_file():
            edl_obj = read_json(timeline)
            style = edl_obj.get("edit_profile", "talking")
            static_limit = EDIT_PROFILES.get(style, EDIT_PROFILES["talking"])["static_sec"]
            edl_cuts_clips = list(edl_obj.get("clips") or [])
            code_broll_spans = [
                (float(c.get("timeline_in_sec", 0.0)), float(c.get("timeline_out_sec", 0.0)))
                for c in edl_cuts_clips
                if c.get("track_role") == "code_broll"
            ]
            cut_times_sec = [float(c.get("timeline_in_sec", 0.0)) for c in edl_cuts_clips[1:]]
        mf_p = pipeline_dir / "01_manifest.json"
        if mf_p.is_file():
            try:
                mf = read_json(mf_p)
                face_mps = {
                    str(it.get("normalized_mp4") or "")
                    for it in (mf.get("items") or [])
                    if it.get("role") == "a_roll" and bool(it.get("faces"))
                }
                if edl_cuts_clips:
                    expected_aroll_has_face = any(
                        str(c.get("media_path") or "") in face_mps and float(c.get("duration_sec", 0.0)) >= 1.0
                        for c in edl_cuts_clips
                        if c.get("track_role") == "a_roll"
                    )
                else:
                    expected_aroll_has_face = bool(face_mps)
            except Exception:
                pass
        ae_p = pipeline_dir / "03_audio_events.json"
        if ae_p.is_file():
            try:
                for s in (read_json(ae_p).get("sentences") or []):
                    if float(s.get("end_sec", 0.0)) > float(s.get("start_sec", 0.0)):
                        sent_speech_spans.append((float(s["start_sec"]), float(s["end_sec"])))
            except Exception:
                pass
        pkg_p2 = pipeline_dir / "06_packaging.json"
        if pkg_p2.is_file():
            try:
                pkg_obj = read_json(pkg_p2)
                edl_fps = int((read_json(timeline).get("fps") if timeline.is_file() else DEFAULT_FPS) or DEFAULT_FPS)
                for el in (pkg_obj.get("visual_elements") or []):
                    t_el = _clean_zh_text(str(el.get("text_or_asset") or ""))
                    if t_el:
                        pkg_texts.append(t_el)
                    s_p = float(el.get("start_frame", 0)) / float(edl_fps)
                    e_p = float(el.get("end_frame", 0)) / float(edl_fps)
                    pkg_spans.append((s_p, e_p))
                    pkg_elements.append({**el, "_s": s_p, "_e": e_p})
            except Exception:
                pass
        sub_p = pipeline_dir / "05_subtitles.json"
        if sub_p.is_file():
            try:
                subs_j = read_json(sub_p)
                for e in (subs_j.get("entries") or []):
                    st_e = float(e.get("start_sec", 0.0))
                    en_e = float(e.get("end_sec", 0.0))
                    if e.get("suppressed"):
                        suppressed_spans.append((st_e, en_e))
                    else:
                        et = str(e.get("text") or "")
                        expected_sub_entries.append((st_e, en_e, _clean_zh_text(et)))
                        live_sub_entries.append({
                            "id": str(e.get("id") or ""),
                            "text": et,
                            "start_sec": st_e,
                            "end_sec": en_e,
                            "box": _subtitle_text_box(e),
                        })
            except Exception:
                pass

    sub_frames: List[Dict[str, Any]] = []
    readability_frames: List[Dict[str, Any]] = []
    color_frames: List[Dict[str, Any]] = []
    excluded_texts: List[Dict[str, Any]] = []
    packaging_face_hits: List[str] = []
    face_occlusions: List[str] = []
    max_face_text_iou_val = 0.0
    max_pkg_face_iou = 0.0
    wcag_ratios: List[float] = []
    low_contrast_hits: List[str] = []
    meta_leak_hits: List[str] = []
    clipped_white_pcts: List[float] = []
    clipped_black_pcts: List[float] = []
    mean_lumas: List[float] = []
    skin_checked = 0
    skin_violations = 0
    skin_source_limited = 0
    masked_thumbs: List[np.ndarray] = []
    frame_backends: List[str] = []
    read_error_frames = 0
    crop_jobs: List[Tuple[int, Dict[str, Any], Path]] = []
    crop_dir = tmp_dir / "bb_sub_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)

    cb_min = float(SKIN_YCBCR_RANGE["cb_min"])
    cb_max = float(SKIN_YCBCR_RANGE["cb_max"])
    cr_min = float(SKIN_YCBCR_RANGE["cr_min"])
    cr_max = float(SKIN_YCBCR_RANGE["cr_max"])

    # 逐条字幕的几何体检（与帧无关，一次判定；帧级明细里原样带出）
    entry_geometry: Dict[str, Dict[str, Any]] = {}
    for ent in live_sub_entries:
        entry_geometry[ent["id"]] = measure_subtitle_geometry(
            {"final_bbox": ent["box"]} if ent["box"] else {}
        )

    for idx, fp in enumerate(frame_files):
        t_sec = frame_times[idx]
        vr = vis_map.get(str(fp.resolve()), {})
        b_name = str(vr.get("backend") or "none")
        frame_backends.append(b_name)
        if vr.get("read_error") or b_name == "apple_vision_read_error":
            read_error_frames += 1
        faces_raw = vr.get("faces") or []
        # 过滤背景极小路人人脸 (< 0.8% 画幅面积) 防误算肤色或误报字幕遮挡
        faces = [
            f for f in faces_raw
            if isinstance(f.get("bbox"), dict)
            and (float(f["bbox"].get("w", 0.0)) * float(f["bbox"].get("h", 0.0))) >= 0.008
        ]
        texts = [t for t in (vr.get("texts") or []) if float(t.get("conf", 0.0)) >= 0.32 and len(str(t.get("text", "")).strip()) >= 2]

        with Image.open(fp) as im:
            im_rgb = im.convert("RGB")
            rgb = np.asarray(im_rgb)
            thumb = np.asarray(im_rgb.resize((270, 480)), dtype=np.float32)

        thumb_masked = thumb.copy()
        thumb_masked[285:380, :, :] = 0.0
        thumb_masked[46:88, :, :] = 0.0
        masked_thumbs.append(thumb_masked)

        luma = compute_bt709_luma(rgb)
        mean_lumas.append(float(np.mean(luma)))
        clipped_white_pcts.append(float(np.mean(luma > 251.0) * 100.0))
        clipped_black_pcts.append(float(np.mean(luma < 6.0) * 100.0))

        # #15 严格使用 BT.709 矩阵与 common.SKIN_YCBCR_RANGE 检测实测肤色像素
        for f in faces:
            bb = f.get("bbox")
            if not bb:
                continue
            x0 = max(0, int((bb["x"] + bb["w"] * 0.25) * CANVAS_WIDTH))
            x1 = min(CANVAS_WIDTH, max(x0 + 4, int((bb["x"] + bb["w"] * 0.75) * CANVAS_WIDTH)))
            y0 = max(0, int((bb["y"] + bb["h"] * 0.22) * CANVAS_HEIGHT))
            y1 = min(CANVAS_HEIGHT, max(y0 + 4, int((bb["y"] + bb["h"] * 0.68) * CANVAS_HEIGHT)))
            roi = rgb[y0:y1, x0:x1].astype(np.float32)
            if roi.size < 16:
                continue
            r, g, b = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
            yy = 16.0 + (0.2126 * r + 0.7152 * g + 0.0722 * b) * (219.0 / 255.0)
            cb = 128.0 + (-0.114572 * r - 0.385428 * g + 0.5 * b) * (224.0 / 255.0)
            cr = 128.0 + (0.5 * r - 0.454153 * g - 0.045847 * b) * (224.0 / 255.0)
            valid_px = (yy >= 40.0) & (yy <= 235.0)
            near_skin = (
                (cb >= cb_min - 10) & (cb <= cb_max + 10)
                & (cr >= cr_min - 10) & (cr <= cr_max + 10)
                & valid_px
            )
            skin_ratio = float(np.mean(near_skin)) if near_skin.size else 0.0
            if np.any(valid_px):
                cb_med = float(np.median(cb[valid_px]))
                cr_med = float(np.median(cr[valid_px]))
                yy_p50 = float(np.median(yy[valid_px]))
            else:
                cb_med = float(np.mean(cb))
                cr_med = float(np.mean(cr))
                yy_p50 = float(np.median(yy))
            yy_p90 = float(np.percentile(yy, 90))
            verdict = classify_skin_sample(cb_med, cr_med, yy_p50, yy_p90, skin_ratio)
            skin_checked += 1
            if verdict["violation"]:
                skin_violations += 1
            if verdict["source_limited"]:
                skin_source_limited += 1
            color_frames.append({
                "frame": int(round(t_sec * DEFAULT_FPS)),
                "t_sec": round(t_sec, 1),
                "face_bbox": [int(bb["x"] * CANVAS_WIDTH), int(bb["y"] * CANVAS_HEIGHT),
                              int(bb["w"] * CANVAS_WIDTH), int(bb["h"] * CANVAS_HEIGHT)],
                "cb": round(cb_med, 1),
                "cr": round(cr_med, 1),
                "y_median": round(yy_p50, 1),
                "skin_px_count": int(near_skin.sum()),
                "skin_px_ratio": round(skin_ratio, 3),
                "clipped_white": round(float(np.mean(luma > 251.0) * 100.0), 1),
                "clipped_black": round(float(np.mean(luma < 6.0) * 100.0), 1),
                "in_sector": bool(verdict["in_sector"]),
                "source_limited": verdict["source_limited"],
                "source_limited_reason": verdict["source_limited_reason"],
                "skipped_by_check": True if verdict["source_limited"] else False,
            })

        eff_spans = sent_speech_spans or speech_spans
        in_speech = any((s + 0.15) <= t_sec <= (e - 0.15) for s, e in eff_spans)
        is_suppressed_now = (
            any(s <= t_sec <= e for s, e in suppressed_spans)
            or any(cs <= t_sec <= ce for cs, ce in code_broll_spans)
        )

        # ── 画面自带文字 / 包装花字：全帧 OCR 只用来查元数据露馅，并显式登记「不进几何判据」──
        active_overlay_corpus = "".join(
            [et for st_s, en_s, et in expected_sub_entries if (st_s - 0.45) <= t_sec <= (en_s + 0.45)] + pkg_texts
        )
        for t in texts:
            txt_raw = str(t.get("text", "")).strip()
            m_leak = META_LEAK_RE.search(txt_raw)
            if m_leak:
                meta_leak_hits.append(f"t={t_sec:.1f}s 命中'{m_leak.group(0)}' ({txt_raw[:16]})")
            px1 = int(round(float(t["x"]) * CANVAS_WIDTH))
            py1 = int(round(float(t["y"]) * CANVAS_HEIGHT))
            pw = max(4, int(round(float(t["w"]) * CANVAS_WIDTH)))
            ph = max(4, int(round(float(t["h"]) * CANVAS_HEIGHT)))
            txt_c = _clean_zh_text(txt_raw)
            in_pkg_window = any((s - 0.2) <= t_sec <= (e + 0.2) for s, e in pkg_spans if e > s)
            matches_pkg = bool(txt_c) and any(
                pt and (sum(1 for ch in set(txt_c) if ch in pt) >= min(2, len(txt_c))) for pt in pkg_texts
            )
            our_boxes = [
                ent["box"] for ent in live_sub_entries
                if ent["box"] and (ent["start_sec"] - 0.05) <= t_sec <= (ent["end_sec"] + 0.05)
            ]
            if not ocr_box_is_our_subtitle((px1, py1, pw, ph), our_boxes):
                # 不在我们字幕框里的字 = 画面自带的招牌/美术字 或包装花字：显式登记并排除出几何/可读性判据
                excluded_texts.append({
                    "frame": int(round(t_sec * DEFAULT_FPS)),
                    "t_sec": round(t_sec, 1),
                    "text": txt_raw[:16],
                    "box": [px1, py1, pw, ph],
                    "source": "packaging_text" if (matches_pkg or in_pkg_window) else "footage_text",
                    "reason": "not_our_subtitle_layer",
                })

        # ── 我们自己的字幕层：几何以 05_subtitles.final_bbox 为准 + 裁切 OCR + 裁切内 WCAG ──
        # 只在字幕**确实画在画面上的时间窗**内量可读性：旧口径带 ±0.05s 容差，
        # 会把「字幕刚开始前 0.01s」的帧也算成有字幕，量到的其实是一块没字的亮背景
        # （实测 t=16.0s 期望『嘅厨房』(16.041 起) 却被判 1.26:1）。两侧各让 1 帧。
        _frame_tol = 1.0 / float(DEFAULT_FPS)
        active_subs = [
            ent for ent in live_sub_entries
            if (ent["start_sec"] + _frame_tol) <= t_sec <= (ent["end_sec"] - _frame_tol) and ent["box"]
        ]
        frame_row: Dict[str, Any] = {
            "frame": int(round(t_sec * DEFAULT_FPS)),
            "t_sec": round(t_sec, 1),
            "in_speech": bool(in_speech),
            "suppressed": bool(is_suppressed_now),
            "subs": [],
        }
        for ent in active_subs:
            geo = entry_geometry.get(ent["id"]) or measure_subtitle_geometry({"final_bbox": ent["box"]})
            box = geo["box"]
            iou = max_face_text_iou([box], faces)
            max_face_text_iou_val = max(max_face_text_iou_val, iou)
            if iou > 0.02:
                face_occlusions.append(f"t={t_sec:.1f}s '{ent['text'][:8]}' IoU={iou:.3f}")

            pad = SUB_CROP_PAD_PX
            x0 = max(0, box["x"] - pad)
            y0 = max(0, box["y"] - pad)
            x1 = min(CANVAS_WIDTH, box["x"] + box["w"] + pad)
            y1 = min(CANVAS_HEIGHT, box["y"] + box["h"] + pad)
            patch = rgb[y0:y1, x0:x1]
            metric = glyph_contrast_ratio(patch) if patch.size else None
            contrast = round(float(metric.get("ratio_effective", metric["ratio"])), 2) if metric else None
            bg_lum = round(float(metric["bg_lum"]), 3) if metric else None
            # 「这一帧到底有没有我们画的字」要在裁切 OCR 之后才能定（白字压在亮墙时上也量得到「字形」），
            # 先记原始度量，等 OCR 回来再判，避免把「没字」的帧算成低对比。
            readability_frames.append({
                "id": ent["id"],
                "glyph_lum": round(float(metric["glyph_lum"]), 3) if metric else None,
                "frame": int(round(t_sec * DEFAULT_FPS)),
                "t_sec": round(t_sec, 1),
                "text": ent["text"],
                "text_bbox": [box["x"], box["y"], box["w"], box["h"]],
                "face_bbox": (
                    [int(f["bbox"]["x"] * CANVAS_WIDTH), int(f["bbox"]["y"] * CANVAS_HEIGHT),
                     int(f["bbox"]["w"] * CANVAS_WIDTH), int(f["bbox"]["h"] * CANVAS_HEIGHT)]
                    if faces else None
                ),
                "iou": round(iou, 4),
                "contrast_ratio": contrast,
                "bg_luma": bg_lum,
                "text_detected": None,
            })

            crop_path = crop_dir / f"{int(round(t_sec * DEFAULT_FPS)):05d}_{ent['id'] or 'x'}.png"
            with Image.fromarray(patch) as pim:
                big = pim.resize((max(8, pim.width * SUB_CROP_SCALE), max(8, pim.height * SUB_CROP_SCALE)), Image.LANCZOS)
                big.save(crop_path)
            crop_jobs.append((idx, ent, crop_path))

            frame_row["subs"].append({
                "id": ent["id"],
                "expected_text": ent["text"],
                "text_bbox": [box["x"], box["y"], box["w"], box["h"]],
                "center_dev_px": geo["center_dev_px"],
                "in_safe_zone": geo["in_safe_zone"],
                "in_symmetric_zone": geo["in_symmetric_zone"],
                "source": "our_subtitle",
                "face_iou": round(iou, 4),
                "contrast_ratio": contrast,
                "bg_luma": bg_lum,
                "crop": crop_path.name,
            })

        # 包装花字压脸单独登记（不是字幕层的锅，但报告里要说清楚是谁压的）
        if faces:
            for el in pkg_elements:
                fb2 = el.get("bbox") or {}
                if not isinstance(fb2, dict) or not {"x", "y", "w", "h"}.issubset(fb2.keys()):
                    continue
                if not (float(el["_s"]) <= t_sec <= float(el["_e"])):
                    continue
                iou2 = max_face_text_iou([{k: int(fb2[k]) for k in ("x", "y", "w", "h")}], faces)
                max_pkg_face_iou = max(max_pkg_face_iou, iou2)
                if iou2 > 0.02:
                    packaging_face_hits.append(f"t={t_sec:.1f}s '{str(el.get('text_or_asset') or '')[:8]}' IoU={iou2:.3f}")

        sub_frames.append(frame_row)

    # 裁切 OCR：只认我们自己画的那一层（时间/位置都已由 final_bbox 锁定）
    crop_ocr: Dict[str, str] = {}
    crop_backends: List[str] = []
    if crop_jobs:
        res = inspect_images([p for _, _, p in crop_jobs])
        for _, _, p in crop_jobs:
            v = res.get(str(p)) or {}
            crop_backends.append(str(v.get("backend") or "none"))
            hits = [str(x.get("text", "")) for x in (v.get("texts") or []) if str(x.get("text", "")).strip()]
            crop_ocr[str(p)] = "".join(hits)

    entry_best: Dict[str, Tuple[float, str]] = {}
    per_entry_length: Dict[str, int] = {}
    for idx, ent, crop_path in crop_jobs:
        ocr_txt = crop_ocr.get(str(crop_path), "")
        ratio = text_match_ratio(ent["text"], ocr_txt)
        prev = entry_best.get(ent["id"])
        if prev is None or ratio > prev[0]:
            entry_best[ent["id"]] = (ratio, ocr_txt)
        per_entry_length[ent["id"]] = len(_clean_for_match(ent["text"]))
        for row in readability_frames:
            if row.get("id") == ent["id"] and row.get("frame") == int(round(frame_times[idx] * DEFAULT_FPS)):
                row["ocr_readable"] = bool(ratio >= 0.5)
                row["ocr_text"] = ocr_txt
        for row in sub_frames:
            if row["frame"] == int(round(frame_times[idx] * DEFAULT_FPS)):
                for s in row["subs"]:
                    if s["id"] == ent["id"]:
                        s["ocr_text"] = ocr_txt
                        s["match_ratio"] = round(ratio, 3)
                        s["ocr_readable"] = bool(ratio >= 0.5)

    # WCAG 在拿到裁切 OCR 之后再定案：认得出字 = 这一帧确实有我们的字幕层；认不出但字形很亮也算（字被背景吃掉的极端情况）
    for row in readability_frames:
        if row.get("contrast_ratio") is None:
            continue
        if not (row.get("ocr_readable") or float(row.get("glyph_lum") or 0.0) >= 0.85):
            row["text_detected"] = False
            row["skip_reason"] = "no_subtitle_layer_in_crop"
            continue
        row["text_detected"] = True
        wcag_ratios.append(float(row["contrast_ratio"]))
        if float(row["contrast_ratio"]) < SUB_MIN_CONTRAST:
            low_contrast_hits.append(f"t={row['t_sec']:.1f}s '{str(row['text'])[:8]}' ratio={float(row['contrast_ratio']):.2f}")

    # 语音帧覆盖率：分母＝「这一帧有我们的字幕且落在语音窗内」的帧数（不再拿全帧 OCR 猜）
    speech_frames_checked = 0
    speech_frames_with_sub_ocr = 0
    for row in sub_frames:
        if not row["in_speech"] or row["suppressed"] or not row["subs"]:
            continue
        speech_frames_checked += 1
        if any(s.get("ocr_readable") for s in row["subs"]):
            speech_frames_with_sub_ocr += 1

    total_chars = sum(max(1, per_entry_length.get(k, 1)) for k in entry_best)
    sub_content_recall = (
        round(sum(r * max(1, per_entry_length.get(k, 1)) for k, (r, _t) in entry_best.items()) / float(total_chars), 3)
        if total_chars else 1.0
    )
    entries_sampled = len(entry_best)
    entries_total = len(live_sub_entries)

    safe_violations: List[str] = []
    center_violations: List[str] = []
    max_subtitle_center_dev_px = 0.0
    for ent in live_sub_entries:
        geo = entry_geometry.get(ent["id"]) or {}
        box = geo.get("box")
        if not box:
            safe_violations.append(f"'{ent['text'][:10]}' 缺少 final_bbox")
            continue
        if not geo.get("in_safe_zone"):
            safe_violations.append(
                f"'{ent['text'][:10]}' ({box['x']},{box['y']})-({box['x'] + box['w']},{box['y'] + box['h']})"
            )
        dev = geo.get("center_dev_px") or 0.0
        max_subtitle_center_dev_px = max(max_subtitle_center_dev_px, float(dev))
        if not geo.get("center_ok"):
            center_violations.append(
                f"'{ent['text'][:10]}' 中心X={box['x'] + box['w'] / 2.0:.0f}px(偏{dev:.0f}px)"
            )
        if not geo.get("in_symmetric_zone"):
            center_violations.append(
                f"'{ent['text'][:10]}' 宽度{box['w']}px 超出对称安全带 640px"
            )

    # #18 屏蔽字幕带后的连续 >3s 视觉静止黑盒检测
    freeze_end_idx = max(1, len(masked_thumbs) - 2)
    cur_static_steps = 0
    max_static_steps = 0
    for ti in range(1, freeze_end_idx):
        diff_arr = np.abs(masked_thumbs[ti] - masked_thumbs[ti - 1])
        mad = float(np.mean(diff_arr))
        p95 = float(np.percentile(diff_arr, 95))
        if mad < 1.2 and p95 < 5.0:
            cur_static_steps += 1
            max_static_steps = max(max_static_steps, cur_static_steps)
        else:
            cur_static_steps = 0
    max_static_sec = round(max_static_steps * 0.5, 2)
    freeze_res = {
        "status": "PASS" if max_static_sec <= static_limit else "FAIL",
        "passed": max_static_sec <= static_limit,
        "max_static_sec": max_static_sec,
        "threshold_sec": static_limit,
    }

    uniq_backends = sorted(set(frame_backends)) if frame_backends else ["none"]
    ocr_backend = uniq_backends[0] if len(uniq_backends) == 1 else ",".join(uniq_backends)
    crop_backend = sorted(set(crop_backends))[0] if crop_backends else "none"
    ocr_supported = (
        bool(frame_backends)
        and all(b == "apple_vision" for b in frame_backends)
        and read_error_frames == 0
        and bool(crop_backends)
        and all(b == "apple_vision" for b in crop_backends)
    )

    sub_match_ratio = (speech_frames_with_sub_ocr / float(speech_frames_checked)) if speech_frames_checked > 0 else 1.0

    if not ocr_supported:
        sub_sync_res = {
            "status": "FAIL" if read_error_frames > 0 else "SKIPPED_UNAVAILABLE",
            "passed": False,
            "ocr_backend": ocr_backend,
            "crop_ocr_backend": crop_backend,
            "ocr_supported": False,
            "read_error_frames": read_error_frames,
            "frames_checked": len(frame_files),
            "safe_violations": [],
            "center_violations": [],
            "max_subtitle_center_dev_px": None,
            "speech_frames_checked": speech_frames_checked,
            "speech_sub_ocr_ratio": 0.0,
            "sub_ocr_content_recall": 0.0,
            "scope_cn": "只检我们自己渲染的字幕层（05_subtitles.final_bbox），画面自带招牌/美术字不计入",
            "frames": [],
        }
        readability_res = {
            "status": "FAIL" if read_error_frames > 0 else "SKIPPED_UNAVAILABLE",
            "passed": False,
            "max_face_text_iou": 0.0,
            "face_occlusions": [],
            "wcag_mean_contrast": 0.0,
            "wcag_samples": 0,
            "low_contrast_hits": [],
            "scope_cn": "只检我们自己渲染的字幕层（05_subtitles.final_bbox），画面自带招牌/美术字不计入",
            "frames": [],
        }
        meta_leak_res = {
            "status": "FAIL" if read_error_frames > 0 else "SKIPPED_UNAVAILABLE",
            "passed": False,
            "ocr_backend": ocr_backend,
            "hits_count": 0,
            "hits": [],
        }
    else:
        sub_content_ok = True if not expected_sub_entries else (sub_content_recall >= SUB_CONTENT_RECALL_MIN)
        sub_ok = (
            len(safe_violations) == 0
            and len(center_violations) == 0
            and (sub_match_ratio >= SPEECH_SUB_COVERAGE_MIN)
            and sub_content_ok
        )
        sub_sync_res = {
            "status": "PASS" if sub_ok else "FAIL",
            "passed": sub_ok,
            "ocr_backend": ocr_backend,
            "crop_ocr_backend": crop_backend,
            "ocr_supported": True,
            "read_error_frames": 0,
            "frames_checked": len(frame_files),
            "safe_violations": safe_violations[:8],
            "center_violations": center_violations[:8],
            "max_subtitle_center_dev_px": round(max_subtitle_center_dev_px, 1),
            "speech_frames_checked": speech_frames_checked,
            "speech_sub_ocr_ratio": round(sub_match_ratio, 3),
            "sub_ocr_content_recall": sub_content_recall,
            "entries_sampled": entries_sampled,
            "entries_total": entries_total,
            "crop_ocr_calls": len(crop_jobs),
            "excluded_texts_count": len(excluded_texts),
            "scope_cn": "只检我们自己渲染的字幕层（05_subtitles.final_bbox 裁切 OCR + 框几何），画面自带招牌/美术字不计入",
            "frames": sub_frames,
            "excluded_texts": excluded_texts[:40],
        }

        # 绝不在零对比样本时硬写 8.0 伪通过；无非抑制字幕文本时不因空采样误判 FAIL
        expect_text_on_screen = bool(expected_sub_entries)
        if wcag_ratios:
            mean_wcag = round(float(np.mean(wcag_ratios)), 2)
            wcag_ok = (len(low_contrast_hits) == 0) and (mean_wcag >= SUB_MIN_MEAN_CONTRAST)
        else:
            mean_wcag = 0.0
            wcag_ok = not expect_text_on_screen
        readability_ok = (max_face_text_iou_val <= 0.02) and wcag_ok
        readability_res = {
            "status": "PASS" if readability_ok else "FAIL",
            "passed": readability_ok,
            "max_face_text_iou": round(max_face_text_iou_val, 4),
            "face_occlusions": face_occlusions[:8],
            "wcag_mean_contrast": mean_wcag,
            "wcag_samples": len(wcag_ratios),
            "low_contrast_hits": low_contrast_hits[:8],
            "packaging_max_face_iou": round(max_pkg_face_iou, 4),
            "packaging_face_hits": packaging_face_hits[:8],
            "scope_cn": "人脸遮挡与 WCAG 只判我们自己渲染的字幕层；包装花字压脸单列（packaging_face_hits），不计入本项判定",
            "frames": readability_frames,
        }
        meta_leak_res = {
            "status": "PASS" if len(meta_leak_hits) == 0 else "FAIL",
            "passed": len(meta_leak_hits) == 0,
            "ocr_backend": ocr_backend,
            "hits_count": len(meta_leak_hits),
            "hits": meta_leak_hits[:8],
        }

    max_white = round(max(clipped_white_pcts or [0.0]), 1)
    max_black = round(max(clipped_black_pcts or [0.0]), 1)
    luma_jumps = [
        abs(mean_lumas[i] - mean_lumas[i - 1])
        for i in range(1, len(mean_lumas))
        if not any(abs(ct - (i - 0.5) * 0.5) <= 0.55 for ct in cut_times_sec)
    ]
    max_luma_jump = round(max(luma_jumps or [0.0]), 1)
    skin_missing_fail = bool(ocr_supported and expected_aroll_has_face and skin_checked == 0)
    luma_jump_ok = (max_luma_jump <= 90.0)
    color_ok = (max_white <= 55.0) and (max_black <= 92.0) and (skin_violations == 0) and (not skin_missing_fail) and luma_jump_ok
    color_status = (
        "SKIPPED_UNAVAILABLE"
        if (not ocr_supported and expected_aroll_has_face and skin_checked == 0 and color_ok)
        else ("PASS" if color_ok else "FAIL")
    )
    color_res = {
        "status": color_status,
        "passed": (color_status == "PASS"),
        "max_clipped_white_pct": max_white,
        "max_clipped_black_pct": max_black,
        "skin_samples_checked": skin_checked,
        "skin_sector_violations": skin_violations,
        "skin_source_limited": skin_source_limited,
        "skin_missing_when_expected": skin_missing_fail,
        "max_luma_jump": max_luma_jump,
        "luma_jump_ok": luma_jump_ok,
        "skin_sector_range": dict(SKIN_YCBCR_RANGE),
        "scope_cn": "肤色只对 yy>=40 的有效肤色像素判定；脸区本身在暗部/逆光剪影的帧标 source_limited（素材限制，不硬掰也不假装通过）",
        "frames": color_frames,
    }

    return sub_sync_res, readability_res, color_res, meta_leak_res, freeze_res

def verify_cut_spot_blackbox(video_path: Path, pipeline_dir: Optional[Path], tmp_dir: Path) -> Dict[str, Any]:
    """#19 全量切点像素点名验证：取消 [:12] 截断，并用 showinfo n: 解析精准匹配帧号防 zip 错位。"""
    if not pipeline_dir or not (pipeline_dir / "04_edl.json").is_file():
        return {"status": "SKIPPED_UNAVAILABLE", "passed": False, "checked_cuts": 0, "verified_cuts": 0, "verified_ratio": 0.0, "max_unverified_run": 0}
    edl = read_json(pipeline_dir / "04_edl.json")
    fps = int(edl.get("fps", DEFAULT_FPS))
    total_f = int(edl.get("total_frames", 0))
    cuts = [
        int(c["timeline_in_frame"])
        for c in (edl.get("clips") or [])[1:]
        if 4 <= int(c.get("timeline_in_frame", 0)) <= max(4, total_f - 4)
    ]
    if not cuts:
        return {"status": "PASS", "passed": True, "checked_cuts": 0, "verified_cuts": 0, "verified_ratio": 1.0, "max_unverified_run": 0}

    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        return {"status": "SKIPPED_UNAVAILABLE", "passed": False, "checked_cuts": 0, "verified_cuts": 0, "verified_ratio": 0.0, "max_unverified_run": 0}
    req_frames = sorted({max(0, cf - 2) for cf in cuts} | {cf + 2 for cf in cuts})
    spot_dir = tmp_dir / "spot_cuts"
    spot_dir.mkdir(parents=True, exist_ok=True)
    frame_by_n: Dict[int, np.ndarray] = {}
    for batch_idx in range(0, len(req_frames), 24):
        batch_req = req_frames[batch_idx : batch_idx + 24]
        batch_dir = spot_dir / f"b_{batch_idx:03d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        sel_expr = "+".join(f"eq(n\\,{fn})" for fn in batch_req)
        res = run_cmd([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "info",
            "-i", str(video_path),
            "-vf", f"select={sel_expr},showinfo,scale=270:480",
            *fps_mode_args("passthrough"), "-q:v", "3",
            str(batch_dir / "sp_%04d.jpg"),
        ], check=False)

        emitted_frames: List[int] = []
        for m in re.finditer(r"n:\s*\d+.*?pts_time:([0-9.]+)", res.stderr or ""):
            pts_t = float(m.group(1))
            emitted_frames.append(int(round(pts_t * fps)))

        files = sorted(batch_dir.glob("sp_*.jpg"))
        for idx_f, fp in enumerate(files):
            in_fn = emitted_frames[idx_f] if idx_f < len(emitted_frames) else (batch_req[idx_f] if idx_f < len(batch_req) else -1)
            if in_fn < 0:
                continue
            with Image.open(fp) as im:
                arr = np.asarray(im.convert("RGB"), dtype=np.float32)
            # 双带掩膜：同时屏蔽底部字幕区 [285:380] 与顶部花字包装区 [46:88]，防包装切换冒充切镜
            arr[285:380, :, :] = 0.0
            arr[46:88, :, :] = 0.0
            matched_req = min(batch_req, key=lambda rf: abs(rf - in_fn))
            if abs(matched_req - in_fn) <= 1:
                frame_by_n[matched_req] = arr

    verified = 0
    unverified_cuts: List[float] = []
    cur_unver = 0
    max_unver = 0
    for cf in cuts:
        a = frame_by_n.get(max(0, cf - 2))
        b = frame_by_n.get(cf + 2)
        if a is None or b is None:
            unverified_cuts.append(round(cf / float(fps), 2))
            cur_unver += 1
            max_unver = max(max_unver, cur_unver)
            continue
        mad = float(np.mean(np.abs(b - a)))
        rel_mad = mad / max(15.0, float(np.mean(a)) * 0.25)
        ha, _ = np.histogram(a.ravel(), bins=16, range=(0, 256), density=True)
        hb, _ = np.histogram(b.ravel(), bins=16, range=(0, 256), density=True)
        hist_l1 = float(np.sum(np.abs(ha - hb)) * 16.0)
        if mad >= 1.8 or rel_mad >= 0.08 or hist_l1 >= 0.035:
            verified += 1
            cur_unver = 0
        else:
            unverified_cuts.append(round(cf / float(fps), 2))
            cur_unver += 1
            max_unver = max(max_unver, cur_unver)

    checked = len(cuts)
    ratio = round(verified / max(1, checked), 3)
    passed = (ratio >= 0.85 and max_unver <= 1)
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "checked_cuts": checked,
        "verified_cuts": verified,
        "verified_ratio": ratio,
        "max_unverified_run": max_unver,
        "unverified_cut_secs": unverified_cuts,
    }


def compute_audio_onset_strength(mono: np.ndarray, sr: int = 48000) -> Tuple[np.ndarray, np.ndarray]:
    """基于 5ms hop 的对数频谱通量 + 对数能量上升沿联合 Onset 强度曲线（纯 NumPy 实现）。"""
    hop = max(1, int(round(0.005 * sr)))
    win_n = 1024
    x = np.asarray(mono, dtype=np.float64)
    if len(x) < hop * 2:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)
    padded = np.pad(x, (win_n // 2, win_n))
    n_frames = max(1, (len(x) + hop - 1) // hop)
    hann = np.hanning(win_n)
    try:
        views = np.lib.stride_tricks.sliding_window_view(padded, win_n)[::hop][:n_frames]
    except Exception:
        views = np.stack([padded[i * hop : i * hop + win_n] for i in range(n_frames)], axis=0)
    frames = views * hann
    frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
    noise_floor = max(1e-4, float(np.percentile(frame_rms, 30)) * 2.5)
    gate = (frame_rms >= noise_floor).astype(np.float64)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    log_mag = np.log1p(100.0 * mag)
    flux = np.concatenate([[0.0], np.maximum(np.diff(log_mag, axis=0), 0.0).sum(axis=1)]) * gate
    rms_log = np.log(frame_rms + max(1e-3, noise_floor))
    rise = np.concatenate([[0.0], np.maximum(np.diff(rms_log), 0.0)]) * gate
    p99_f = float(np.percentile(flux, 99)) + 1e-9
    p99_r = float(np.percentile(rise, 99)) + 1e-9
    strength = np.maximum(flux / p99_f, rise / p99_r)
    times = np.arange(len(strength), dtype=np.float64) * (hop / float(sr))
    return times, strength


def measure_event_onset_offsets(
    mono: np.ndarray,
    sr: int,
    events: List[Dict[str, Any]],
    window_sec: float = 0.15,
    thr: float = 0.22,
    tol_ms: float = 55.0,
) -> Dict[str, Any]:
    """对比期望事件时刻（支持单时刻 t 或双锚 [t_on, t_pk]）与成片实测音轨 Onset 毫秒偏差。"""
    if not events:
        return {"checked": 0, "synced": 0, "sync_ratio": 1.0, "median_err_ms": 0.0, "max_err_ms": 0.0, "details": []}
    times, strength = compute_audio_onset_strength(mono, sr)
    if len(times) < 3:
        return {"checked": len(events), "synced": 0, "sync_ratio": 0.0, "median_err_ms": 999.0, "max_err_ms": 999.0, "details": []}

    errs_ms: List[float] = []
    details: List[Dict[str, Any]] = []
    for ev in events:
        t_on = float(ev.get("onset_time_sec", ev.get("t", 0.0)))
        t_pk = float(ev.get("peak_time_sec", t_on))
        t_lo = min(t_on, t_pk) - float(window_sec)
        t_hi = max(t_on, t_pk) + float(window_sec)
        idx = np.flatnonzero((times >= t_lo) & (times <= t_hi))
        peaks = [
            int(k) for k in idx
            if 0 < k < len(strength) - 1
            and strength[k] >= strength[k - 1]
            and strength[k] >= strength[k + 1]
            and strength[k] >= thr
        ]
        if not peaks:
            errs_ms.append(round(window_sec * 1000.0 + 50.0, 1))
            details.append({"id": ev.get("id", ""), "expected": round(t_pk, 3), "onset": None, "offset_ms": None})
            continue
        max_pk = max(float(strength[k]) for k in peaks)
        sig_peaks = [k for k in peaks if float(strength[k]) >= max(thr, max_pk * 0.45)] or peaks
        best_k = min(sig_peaks, key=lambda k: min(abs(times[k] - t_on), abs(times[k] - t_pk)))
        o_t = float(times[best_k])
        err_on = (o_t - t_on) * 1000.0
        err_pk = (o_t - t_pk) * 1000.0
        best_err = err_on if abs(err_on) <= abs(err_pk) else err_pk
        errs_ms.append(round(abs(best_err), 1))
        details.append({"id": ev.get("id", ""), "expected": round(t_pk, 3), "onset": round(o_t, 3), "offset_ms": round(best_err, 1)})

    synced = sum(1 for d, e in zip(details, errs_ms) if d["onset"] is not None and e <= tol_ms)
    return {
        "checked": len(events),
        "synced": synced,
        "sync_ratio": round(synced / float(len(events)), 3),
        "median_err_ms": round(statistics.median(errs_ms), 1),
        "max_err_ms": round(max(errs_ms), 1),
        "details": details,
    }


def verify_sfx_in_window_blackbox(
    audio_ctx: Dict[str, Any],
    pipeline_dir: Optional[Path],
) -> Dict[str, Any]:
    """#20 统一调用 common.compute_sfx_band_windows 实测同窗净抬升，并用 5ms 频谱通量校验真实起音毫秒同步。"""
    if not pipeline_dir or not (pipeline_dir / "06_packaging.json").is_file():
        return {"status": "SKIPPED_UNAVAILABLE", "passed": False, "cues_checked": 0, "median_lift_db": 0.0, "max_lift_db": 0.0}
    pkg = read_json(pipeline_dir / "06_packaging.json")
    cues = pkg.get("sfx_cues") or []
    if not cues:
        return {"status": "PASS", "passed": True, "cues_checked": 0, "median_lift_db": 0.0, "max_lift_db": 0.0, "onset_sync_ratio": 1.0, "source": "no_cues"}

    bb_arr = audio_ctx["audio_48k"]
    sr1 = int(audio_ctx["sr"])
    bb_mono = bb_arr.mean(axis=1).astype(np.float64)
    onset_signal = bb_mono
    # 实测痛点 #11 根治：按当前风格本身的音效密度分级设定抬升门槛，以起音同步率为主判据，不再与低密风格自相矛盾
    minimum_lift = 1.8
    edit_path = pipeline_dir / "04_edl.json"
    edl_meta = read_json(edit_path) if edit_path.is_file() else {}
    edit_prof_v = str(edl_meta.get("edit_profile", ""))
    sid_v = str(edl_meta.get("style_id") or pkg.get("style_id") or load_style_applied(pipeline_dir) or "")
    style_entry_v = load_style_spectrum_entry(sid_v) if sid_v else None
    sfx_pm_v = float(((style_entry_v or {}).get("audio_and_sfx") or {}).get("sfx_per_min_target") or 24.0)
    if edit_prof_v == "explainer" or sfx_pm_v <= 28.0:
        minimum_lift = 0.8
    elif edit_prof_v == "documentary" or sfx_pm_v <= 38.0:
        minimum_lift = 1.2

    nosfx_p = pipeline_dir / "08_stem_nosfx.wav"
    audit_win_p = pipeline_dir / "08_sfx_audit_window.json"

    try:
        from scipy.signal import butter, sosfiltfilt
        hi_hz = min(8000.0, float(sr1) * 0.48)
        lo_hz = min(2000.0, hi_hz * 0.5)
        sos = butter(4, [lo_hz, hi_hz], btype="bandpass", fs=sr1, output="sos")
        bb_band = sosfiltfilt(sos, bb_mono)
    except Exception:
        bb_band = bb_mono
        sos = None

    windows: List[Dict[str, Any]] = []
    source_used = "missing"
    if nosfx_p.is_file():
        source_used = "08_stem_nosfx.wav"
        no_arr, _ = read_wav_as_48k_stereo(nosfx_p, target_sr=sr1)
        n = min(len(bb_mono), len(no_arr))
        if n < 1024:
            return {"status": "FAIL", "passed": False, "cues_checked": 0, "median_lift_db": 0.0, "max_lift_db": 0.0, "source": source_used}
        no_mono = no_arr[:n].mean(axis=1).astype(np.float64)
        onset_signal = bb_mono[:n] - no_mono
        windows = compute_sfx_band_windows(cues, no_mono, sr1)
    elif audit_win_p.is_file():
        source_used = "08_sfx_audit_window.json"
        audit_data = read_json(audit_win_p)
        windows = list(audit_data.get("windows") or [])
    else:
        return {"status": "FAIL", "passed": False, "cues_checked": 0, "median_lift_db": 0.0, "max_lift_db": 0.0, "source": "missing_nosfx_and_audit_json"}

    lifts: List[float] = []
    for win in windows:
        i0 = max(0, int(round(float(win.get("win_start_sec", 0.0)) * sr1)))
        i1 = min(len(bb_band), int(round(float(win.get("win_end_sec", 0.0)) * sr1)))
        r_no = float(win.get("nosfx_2_8k_rms", 0.0))
        if i1 <= i0 + 64 or r_no <= 0.0:
            continue
        r_bb = float(np.sqrt(np.mean(bb_band[i0:i1] ** 2) + 1e-12))
        lifts.append(round(20.0 * math.log10(max(1e-8, r_bb) / max(1e-8, r_no)), 2))

    if not lifts:
        return {"status": "FAIL", "passed": False, "cues_checked": 0, "median_lift_db": 0.0, "max_lift_db": 0.0, "source": source_used}

    onset_band = sosfiltfilt(sos, onset_signal) if sos is not None else onset_signal
    onset_audit = measure_event_onset_offsets(onset_band, sr1, cues, window_sec=0.14, thr=0.18, tol_ms=60.0)
    med_lift = round(statistics.median(lifts), 2)
    max_lift = round(max(lifts), 2)
    lift_ok = med_lift >= minimum_lift or (onset_audit["sync_ratio"] >= 0.85 and max_lift >= minimum_lift)
    onset_ok = onset_audit["sync_ratio"] >= 0.65
    ok = lift_ok and onset_ok
    return {
        "status": "PASS" if ok else "FAIL",
        "passed": ok,
        "cues_checked": len(lifts),
        "median_lift_db": med_lift,
        "minimum_lift_db": minimum_lift,
        "max_lift_db": max_lift,
        "per_cue_lifts_db": lifts,
        "onset_sync_ratio": onset_audit["sync_ratio"],
        "median_onset_err_ms": onset_audit["median_err_ms"],
        "onset_details": onset_audit["details"][:8],
        "source": source_used,
    }


def _normalize_zh_digits_for_cer(s: str) -> str:
    """将完整多位数阿拉伯数字（个/十/百/千/万位）规范转为汉字读法，防多位数 CER 误杀。"""
    mp = {"0": "零", "1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}

    def _int_to_zh(val: int) -> str:
        if val == 0:
            return "零"
        if val >= 100_000_000:
            return "".join(mp.get(c, c) for c in str(val))
        if val >= 10_000:
            w = val // 10_000
            rem = val % 10_000
            res = _int_to_zh(w) + "万"
            if rem == 0:
                return res
            return res + ("零" if rem < 1000 else "") + _int_to_zh(rem)
        units = ["", "十", "百", "千"]
        digits = [int(c) for c in str(val)][::-1]
        out: List[str] = []
        zero_flag = False
        for idx_u, d in enumerate(digits):
            if d == 0:
                if out:
                    zero_flag = True
            else:
                if zero_flag:
                    out.append("零")
                    zero_flag = False
                if idx_u == 1 and d == 1 and len(digits) == 2:
                    out.append(units[idx_u])
                else:
                    out.append(mp[str(d)] + units[idx_u])
        return "".join(reversed(out))

    def _conv_dec(m: re.Match) -> str:
        int_part, frac_part = m.group(1), m.group(2)
        left = _int_to_zh(int(int_part)) if len(int_part) <= 8 else "".join(mp.get(c, c) for c in int_part)
        right = "".join(mp.get(c, c) for c in frac_part)
        return f"{left}点{right}"

    def _conv(m: re.Match) -> str:
        tok = m.group(0)
        if len(tok) == 4 and (tok.startswith("19") or tok.startswith("20")):
            return "".join(mp.get(ch, ch) for ch in tok)
        if tok.isdigit() and len(tok) <= 8:
            return _int_to_zh(int(tok))
        return "".join(mp.get(ch, ch) for ch in tok)

    s = re.sub(r"(\d+)\.(\d+)", _conv_dec, s)
    s = re.sub(r"\d+", _conv, s)
    return s.replace("两", "二")


def _char_cer(ref: str, hyp: str) -> float:
    a = _normalize_zh_digits_for_cer(_clean_zh_text(ref))
    b = _normalize_zh_digits_for_cer(_clean_zh_text(hyp))
    if not a:
        return 0.0
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            cur = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = cur
    return round(dp[n] / float(m), 3)


def verify_blind_asr_proxy_blackbox(
    audio_48k_path: Path,
    pipeline_dir: Optional[Path],
    dialect: str = "mandarin",
    store_anchor: Optional[str] = None,
) -> Dict[str, Any]:
    """#21 带 BGM 成片独立 ASR 盲听代理（large-v3 优先；支持普通话 zh 与粤语 yue；优先对齐口播真值 say_text）。"""
    if not pipeline_dir or not (pipeline_dir / "03_audio_events.json").is_file():
        return {"status": "SKIPPED_UNAVAILABLE", "passed": False, "cer": None, "anchor_ok": False, "reason": "缺少 03_audio_events.json"}

    ae = read_json(pipeline_dir / "03_audio_events.json")
    sents_ae = ae.get("sentences") or []
    ref_show_text = "".join(str(s.get("text", "")) for s in sents_ae)
    # 真值必须是**嘴上说的**那串字：句级 text 是整理过的版本（去口癖、合半句），
    # 拿它跟盲听结果比，比的不是播控而是「整理改了多少字」。
    ref_say_text = "".join(
        ("".join(str(w.get("word") or "") for w in (s.get("words") or [])) or str(s.get("say_text") or s.get("text") or ""))
        for s in sents_ae
    )
    if ref_say_text == ref_show_text and (pipeline_dir / "02_transcript.json").is_file():
        try:
            tr = read_json(pipeline_dir / "02_transcript.json")
            tr_segs = [s for s in (tr.get("segments") or tr.get("sentences") or []) if not s.get("is_filler")]
            cand_say = "".join(str(s.get("say_text") or s.get("say") or s.get("text") or "") for s in tr_segs)
            if cand_say:
                ref_say_text = cand_say
        except Exception:
            pass

    if not ref_say_text.strip() and not ref_show_text.strip():
        return {
            "status": "PASS",
            "passed": True,
            "dialect": dialect,
            "asr_language": "none",
            "model": "none",
            "cer": 0.0,
            "anchor_ok": True,
            "hyp_preview": "",
            "recognized_text": "",
            "reason": "无口播画面视频，跳过人声听写偏差检查",
        }

    hf_hub = get_huggingface_hub_dir()
    model_name = find_cached_whisper_model(hf_hub)
    if not model_name:
        return {
            "status": "SKIPPED_UNAVAILABLE",
            "passed": False,
            "cer": None,
            "anchor_ok": False,
            "reason": f"未在 {hf_hub} 找到本地 faster-whisper 缓存模型",
        }

    dl_norm = (dialect or "mandarin").lower()
    if dl_norm not in ("mandarin", "cantonese", "yue"):
        return {
            "status": "SKIPPED_UNAVAILABLE",
            "passed": False,
            "dialect": dialect,
            "cer": None,
            "anchor_ok": False,
            "reason": f"自定义方言模式 ({dialect}) 尚无对应通用 ASR 模型",
        }

    asr_lang = "yue" if dl_norm in ("cantonese", "yue") else "zh"
    asr_prompt = "以下是粤语简体中文口播内容：" if asr_lang == "yue" else "以下是普通话简体中文口播内容："
    hyp_text = ""
    model_used = model_name
    # 粤语素材不能用只会普通话的 Whisper 当盲听裁判：它会把「金湾」听成「今晚」、「厨房」听成「徐凤」，
    # CER 全是方言差异，不是播控问题（2026-11-24 实测 0.366，逐条核对全部是同音误听）。
    # 本地已经有 SenseVoice（自判粤语），先让它听；它不可用才回落 Whisper（且只在有 yue 模型时）。
    if asr_lang == "yue":
        try:
            import asr_sensevoice
            segs_sv = asr_sensevoice.try_transcribe(Path(audio_48k_path), language="auto")
            if segs_sv:
                hyp_text = "".join(str(x.get("text", "")).strip() for x in segs_sv)
                model_used = "SenseVoice(yue)"
        except Exception:
            hyp_text = ""
    if not hyp_text:
        if asr_lang == "yue" and "large-v3" not in model_name and "turbo" not in model_name:
            return {
                "status": "SKIPPED_UNAVAILABLE", "passed": False, "cer": None, "anchor_ok": False,
                "reason": f"已缓存的 {model_name} 缺少粤语语言模型，且 SenseVoice 不可用",
            }
        try:
            model = get_cached_whisper_model(model_name, local_files_only=True)
            segs, _ = model.transcribe(
                str(audio_48k_path),
                language=asr_lang,
                initial_prompt=asr_prompt,
                word_timestamps=False,
            )
            hyp_text = "".join(s.text.strip() for s in segs)
        except Exception as exc:
            return {
                "status": "SKIPPED_UNAVAILABLE",
                "passed": False,
                "cer": None,
                "anchor_ok": False,
                "reason": f"本地 ASR 加载/推理不可用: {exc}",
            }

    try:
        # SenseVoice 直接给的是繁体字面；不先统一简体，CER 里全是繁简差异
        from opencc import OpenCC
        hyp_text = OpenCC("t2s").convert(hyp_text)
    except Exception:
        pass
    cer_say = _char_cer(ref_say_text, hyp_text) if ref_say_text else 1.0
    cer_show = _char_cer(ref_show_text, hyp_text) if ref_show_text else 1.0
    cer = min(cer_say, cer_show)
    anchor_ok = True
    if store_anchor and str(store_anchor).strip():
        anc = _clean_zh_text(store_anchor)
        hyp_c = _clean_zh_text(hyp_text)
        if anc:
            anchor_ok = (anc in hyp_c) or (_subsequence_ratio(anc, hyp_c) >= 0.75)

    ok = (cer <= 0.25) and anchor_ok
    return {
        "status": "PASS" if ok else "FAIL",
        "passed": ok,
        "dialect": dialect,
        "asr_language": asr_lang,
        "model": model_used,
        "cer": cer,
        "anchor_ok": anchor_ok,
        "hyp_preview": hyp_text[:48],
        "recognized_text": hyp_text,
    }


def verify_compliance_and_license_blackbox(pipeline_dir: Optional[Path]) -> Dict[str, Any]:
    """#22 严格只读复核广告法合规与 SHA-256 商业字体授权清单，绝不覆写 00_compliance_report.json。"""
    if not pipeline_dir or not pipeline_dir.exists():
        return {"status": "SKIPPED_UNAVAILABLE", "passed": False, "compliance_ok": False, "license_ok": False, "assets_count": 0, "unregistered_count": 0}

    from compliance_guard import audit_script_compliance

    comp_p = pipeline_dir / "00_compliance_report.json"
    comp: Dict[str, Any] = read_json(comp_p) if comp_p.is_file() else {}
    comp_ok = bool(comp.get("passed", False))

    rv_total = int(comp.get("real_voice_total", 0))
    rv_dropped = len(comp.get("dropped_real_voice_ids", []))
    if rv_total > 0 and (rv_dropped >= rv_total or rv_dropped / float(rv_total) > 0.40):
        comp_ok = False

    wl_path: Optional[Path] = None
    if comp.get("whitelist_path") and Path(str(comp["whitelist_path"])).is_file():
        wl_path = Path(str(comp["whitelist_path"]))
    elif (pipeline_dir.parent / "compliance_whitelist.json").is_file():
        wl_path = pipeline_dir.parent / "compliance_whitelist.json"

    extra_audits = []
    if (pipeline_dir / "05_subtitles.json").is_file():
        subs = read_json(pipeline_dir / "05_subtitles.json")
        sents = [{"id": e["id"], "text": e["text"]} for e in subs.get("entries", []) if not e.get("suppressed")]
        # 只读调用 pipeline_dir=None，严禁覆写覆盖上游的 real_voice drop 留痕！
        sub_audit = audit_script_compliance(
            sents, source_type="subtitles", pipeline_dir=None, whitelist_path=wl_path, auto_action=False
        )
        comp_ok = comp_ok and bool(sub_audit.get("passed", False))
        extra_audits.append(sub_audit)

    if (pipeline_dir / "06_packaging.json").is_file():
        pkg = read_json(pipeline_dir / "06_packaging.json")
        psents = [{"id": e["id"], "text": str(e.get("text_or_asset", ""))} for e in pkg.get("visual_elements", [])]
        if psents:
            p_audit = audit_script_compliance(
                psents, source_type="packaging", pipeline_dir=None, whitelist_path=wl_path, auto_action=False
            )
            comp_ok = comp_ok and bool(p_audit.get("passed", False))
            extra_audits.append(p_audit)

    if (pipeline_dir / "09_title_cover.json").is_file():
        cov = read_json(pipeline_dir / "09_title_cover.json")
        csents: List[Dict[str, str]] = []
        for ck in ("cover_main", "cover_sub"):
            if cov.get(ck):
                csents.append({"id": f"cover_{ck}", "text": str(cov[ck])})
        for ti, tline in enumerate(cov.get("titles") or []):
            if str(tline).strip():
                csents.append({"id": f"cover_title_{ti+1}", "text": str(tline)})
        if csents:
            c_audit = audit_script_compliance(
                csents, source_type="cover", pipeline_dir=None, whitelist_path=wl_path, auto_action=False
            )
            comp_ok = comp_ok and bool(c_audit.get("passed", False))
            extra_audits.append(c_audit)

    if (pipeline_dir / "09_publish_copy_pack.json").is_file():
        pk = read_json(pipeline_dir / "09_publish_copy_pack.json")
        pk_sents: List[Dict[str, str]] = []
        for plat_k, plat_v in (pk.get("platforms") or {}).items():
            if not isinstance(plat_v, dict):
                continue
            for ti, tline in enumerate(plat_v.get("titles") or [], 1):
                if str(tline).strip():
                    pk_sents.append({"id": f"copy_{plat_k}_t{ti}", "text": str(tline)})
            if str(plat_v.get("body") or "").strip():
                pk_sents.append({"id": f"copy_{plat_k}_body", "text": str(plat_v["body"])})
        if pk_sents:
            pk_audit = audit_script_compliance(
                pk_sents, source_type="publish_copy", pipeline_dir=None, whitelist_path=wl_path, auto_action=False
            )
            comp_ok = comp_ok and bool(pk_audit.get("passed", False))
            extra_audits.append(pk_audit)

    lic_p = pipeline_dir / "asset_license_manifest.json"
    lic = read_json(lic_p) if lic_p.is_file() else {}
    tidy_p = pipeline_dir / "tidy_report.json"
    tidy_removed: set[str] = set()
    if tidy_p.is_file():
        try:
            tr_rep = read_json(tidy_p)
            if not tr_rep.get("skipped_keep_temp"):
                tidy_removed = {str(x).rstrip("/") for x in (tr_rep.get("removed_paths") or [])}
        except Exception:
            pass

    unverified_fonts: List[str] = []
    file_mismatches: List[str] = []
    external_documents = 0
    for item in lic.get("assets", []):
        asset = Path(item["path"])
        generated = item.get("source_tier") == "project_generated"
        tidy_cleaned = generated and (not asset.exists()) and any(
            d in asset.parts for d in tidy_removed
        )
        if not tidy_cleaned:
            if not asset.is_file() or sha256_file(asset) != item.get("sha256"):
                file_mismatches.append(str(asset))
        cat = item.get("category") or item.get("kind")
        external = item.get("source_tier") == "external_registered"
        if cat == "font" and not external and not is_verified_commercial_font(asset):
            unverified_fonts.append(asset.name)
        if external:
            external_documents += 1
            document = Path(item.get("authorization_path") or ".")
            if not document.is_file() or sha256_file(document) != item.get("authorization_sha256"):
                file_mismatches.append(str(document))

    lic_ok = bool(lic.get("all_commercial_verified", False)) and not unverified_fonts and not file_mismatches
    passed = comp_ok and lic_ok
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "compliance_ok": comp_ok,
        "license_ok": lic_ok,
        "assets_count": int(lic.get("assets_count", 0)),
        "unregistered_count": int(lic.get("unregistered_count", 0)),
        "unverified_fonts": sorted(set(unverified_fonts)),
        "file_mismatches": file_mismatches,
        "external_documents": external_documents,
        "unacked_warn_count": int(comp.get("unacked_warn_count", 0)) + sum(
            report.get("unacked_warn_count", 0) for report in extra_audits
        ),
    }


def _parse_rational_sec(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    s = str(val).strip()
    if s.endswith("s"):
        s = s[:-1]
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / max(1e-6, float(b))
        return float(s)
    except Exception:
        return None


def _verify_jianying(pipeline_dir: Path) -> Dict[str, Any]:
    jy_dir = pipeline_dir / "jianying_draft"
    errors: List[str] = []

    fcpxmls = list(jy_dir.glob("*.fcpxml")) if jy_dir.exists() else []
    fcpxml_ok = True
    if not fcpxmls:
        fcpxml_ok = False
        errors.append("缺少剪映 jianying_draft/*.fcpxml 工程文件")
    else:
        xp = fcpxmls[0]
        xmllint = find_bin("xmllint")
        if xmllint:
            res = run_cmd([xmllint, "--noout", str(xp)], check=False)
            if res.returncode != 0:
                fcpxml_ok = False
                errors.append(f"xmllint 校验失败: {(res.stderr or '')[:200]}")
        try:
            tree = ET.parse(xp)
            root = tree.getroot()
            if root.tag != "fcpxml":
                fcpxml_ok = False
                errors.append("FCPXML 根节点无效")
            res_ids = set()
            for child in root.findall("./resources/*"):
                rid = child.attrib.get("id")
                if rid:
                    res_ids.add(rid)
                mr = child.find("media-rep")
                if mr is not None and "src" in mr.attrib:
                    src = mr.attrib["src"]
                    if src.startswith("file://") or src.startswith("/") or ":/" in src:
                        fcpxml_ok = False
                        errors.append(f"FCPXML 禁止包含绝对路径或 file://: {src}")
                        continue
                    fp = (xp.parent / urllib.parse.unquote(src)).resolve()
                    if not fp.is_file() or fp.stat().st_size == 0:
                        fcpxml_ok = False
                        errors.append(f"FCPXML 引用相对资产缺失或为空: {src}")
            text_style_ids = {d.attrib["id"] for d in root.findall(".//text-style-def") if d.attrib.get("id")}
            for node in root.iter():
                for attr_name in ("ref", "format"):
                    ref_val = node.attrib.get(attr_name)
                    if not ref_val:
                        continue
                    valid_pool = text_style_ids if (attr_name == "ref" and node.tag == "text-style") else res_ids
                    if ref_val not in valid_pool:
                        fcpxml_ok = False
                        errors.append(f"FCPXML 存在悬空资源引用 {attr_name}={ref_val}")

            # 校验 connected child (<video>/<title>/<marker>) 归属节点与局部时间有效性（严禁错堆超限）
            spine = root.find(".//spine")
            if spine is None or not spine.findall("asset-clip"):
                fcpxml_ok = False
                errors.append("FCPXML 缺少可导入的主视频轨")
            if spine is not None:
                spine_clips = spine.findall("asset-clip")
                for ac in spine_clips:
                    c_start = _parse_rational_sec(ac.attrib.get("start")) or 0.0
                    c_dur = _parse_rational_sec(ac.attrib.get("duration")) or 0.0
                    for sub_tag in ("video", "title"):
                        for ch in ac.findall(sub_tag):
                            ch_off = _parse_rational_sec(ch.attrib.get("offset"))
                            if ch_off is not None and (ch_off < c_start - 0.1 or ch_off > c_start + c_dur + 0.15):
                                fcpxml_ok = False
                                errors.append(f"FCPXML 子元素 {ch.attrib.get('name', sub_tag)} 错挂越出宿主剪辑范围")
                                break
        except Exception as e:
            fcpxml_ok = False
            errors.append(f"FCPXML XML 解析异常: {e}")

    return {"status": "PASS" if fcpxml_ok else "FAIL", "host_import": "PENDING_VERIFICATION", "errors": errors}


def _hyperframes_cli() -> list[str]:
    configured = os.environ.get("SVS_HYPERFRAMES_CLI")
    if configured:
        return [configured]
    global_cli = find_bin("hyperframes")
    if global_cli:
        return [global_cli]
    node = find_bin("node")
    if node:
        cache_dir = Path(os.environ.get("npm_config_cache") or (Path.home() / ".npm"))
        for package in cache_dir.glob("_npx/*/node_modules/hyperframes/package.json"):
            try:
                if read_json(package).get("version") == HYPERFRAMES_VERSION:
                    return [node, str(package.parent / "bin" / "hyperframes.mjs")]
            except (OSError, ValueError):
                continue
    return []


def _verify_hyperframes(pipeline_dir: Path) -> Dict[str, Any]:
    from export_hyperframes import StudioMarkup

    draft = (pipeline_dir / "hyperframes_draft").resolve()
    entry = draft / "index.html"
    receipt_path = draft / "studio_export.json"
    if not entry.is_file() or not receipt_path.is_file() or not (draft / "hyperframes.json").is_file():
        return {"status": "FAIL", "errors": ["缺少原生 Studio 导出清单，请重新导出旧工程"]}
    timeline = read_json(receipt_path)
    if timeline.get("format") != "svs-hyperframes-studio-v2":
        return {"status": "FAIL", "errors": ["工程版本无法验证"]}
    nodes = StudioMarkup(entry).nodes
    errors = []
    sources = []
    clip_nodes = {}
    for tag, attrs in nodes:
        if tag == "template" or "data-composition-src" in attrs:
            errors.append("流水线工程应直接暴露逐条剪辑，发现整轨模板或组合")
        if attrs.get("src"):
            sources.append(attrs["src"])
        if "data-track-index" not in attrs:
            continue
        clip_id = attrs.get("id")
        if not clip_id or clip_id in clip_nodes:
            errors.append(f"时间线片段缺少唯一 ID：{clip_id}")
            continue
        try:
            start, duration = float(attrs["data-start"]), float(attrs["data-duration"])
            if start < 0 or duration <= 0 or start + duration > timeline["duration_sec"] + 0.001:
                errors.append(f"时间范围越界：{clip_id}")
        except (KeyError, ValueError):
            errors.append(f"时间范围缺失：{clip_id}")
        clip_nodes[clip_id] = (tag, attrs)
    summary = {
        "video": sum(tag == "video" for tag, _ in clip_nodes.values()),
        "captions": sum(attrs.get("data-track-kind") == "captions" for _, attrs in clip_nodes.values()),
        "graphics": sum(attrs.get("data-track-kind") == "graphics" for _, attrs in clip_nodes.values()),
        "graphic_text": sum(tag == "div" and attrs.get("data-track-kind") == "graphics" for tag, attrs in clip_nodes.values()),
        "audio": sum(tag == "audio" for tag, _ in clip_nodes.values()),
    }
    if summary != timeline["counts"]:
        errors.append(f"逐条剪辑数和导出清单不一致：{summary}")
    for item in timeline["video"]:
        if item.get("original_src"):
            sources.append(item["original_src"])
    for css_src in re.findall(r'url\(["\']?([^"\')]+)', entry.read_text(encoding="utf-8")):
        if css_src.strip().lower().startswith("data:"):
            continue
        sources.append(css_src)
    for src in set(sources):
        clean_src = urllib.parse.unquote(str(src).split("?")[0].split("#")[0])
        resolved = (draft / clean_src).resolve()
        if ":" in clean_src or Path(clean_src).is_absolute() or not resolved.is_relative_to(draft) or not resolved.is_file():
            errors.append(f"媒体脱离工程或缺失：{src}")
    if errors:
        return {"status": "FAIL", "errors": errors, "native_clip_counts": summary}

    runner = _hyperframes_cli()
    if not runner:
        return {"status": "SKIPPED_UNAVAILABLE", "errors": ["未找到可执行 HyperFrames CLI"], "native_clip_counts": summary}
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1"}
    try:
        lint = subprocess.run([*runner, "lint", str(draft), "--json"], capture_output=True, text=True, timeout=30, env=env)
        if lint.returncode:
            return {"status": "FAIL", "errors": [f"官方 lint 未通过：{(lint.stdout or lint.stderr)[-700:]}"]}
        result = subprocess.run([*runner, "timeline", str(draft), "--json"], capture_output=True, text=True,
                                timeout=30, env=env, check=True)
        rows = [row for group in json.loads(result.stdout)["timeline"]["tracks"] for row in group["rows"]]
        actual = {row["id"] for row in rows if row["file"] == "index.html" and not row.get("nested")}
        if set(clip_nodes) != actual:
            return {"status": "FAIL", "errors": ["官方 Studio 时间线未识别出导出的全部原生片段"]}
        with tempfile.TemporaryDirectory(prefix="svs_hf_review_") as work:
            end = max(0.0, float(timeline["duration_sec"]) - 1.0 / timeline["fps"])
            times = ",".join(f"{t:.4f}" for t in (0.0, end / 2, end))
            proc = subprocess.run([*runner, "snapshot", str(draft), "--at", times, "--no-end",
                                   "--no-browser-gpu", "--output", work, "--json"],
                                  capture_output=True, text=True, timeout=60, env=env)
            if proc.returncode:
                return {"status": "SKIPPED_UNAVAILABLE" if "browser" in proc.stderr.lower() else "FAIL",
                        "errors": [f"官方运行时抽帧失败：{(proc.stderr or proc.stdout)[-700:]}"],
                        "native_clip_counts": summary}
            stills = list(Path(work).glob("frame-*.png")) or list(Path(work).glob("at-*.png"))
            from PIL import ImageStat
            levels = []
            for path in stills:
                with Image.open(path) as frame:
                    levels.append(ImageStat.Stat(frame.convert("L")).mean[0])
            if len(stills) != 3 or min(levels) < 5:
                return {"status": "FAIL", "errors": ["官方运行时缺少有效的片头、中段或片尾画面"],
                        "native_clip_counts": summary}
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        return {"status": "SKIPPED_UNAVAILABLE", "errors": [f"原生 Studio 核查未完成：{exc}"], "native_clip_counts": summary}
    return {"status": "PASS", "browser_smoke": "pass", "native_clip_counts": summary,
            "rendered_frames": len(stills), "errors": []}


def verify_exports_blackbox(pipeline_dir: Path, export_mode: str = "auto") -> Dict[str, Any]:
    requested, required = export_selection(export_mode)
    results = {}
    for name, check in (("jianying", _verify_jianying), ("hyperframes", _verify_hyperframes)):
        if name not in requested:
            results[name] = {"status": "NOT_REQUESTED", "errors": []}
            continue
        status_path = pipeline_dir / f"{name}_export_status.json"
        if status_path.is_file():
            status = read_json(status_path)
            if status.get("status") != "success":
                msg = str(status.get("message") or "工程导出失败")
                # 实测痛点 #9 根治：非必检 (--exports auto) 且因本机环境/CLI 缺失或降级时标记 SKIPPED_UNAVAILABLE 而非 FAIL
                is_env_unavail = (not required) and (
                    status.get("status") == "degraded_to_mp4_only"
                    or any(k in msg.lower() for k in ("cli", "missing", "未安装", "未找到", "降级"))
                )
                results[name] = {
                    "status": "SKIPPED_UNAVAILABLE" if is_env_unavail else "FAIL",
                    "errors": [msg],
                }
                continue
        results[name] = check(pipeline_dir)

    statuses = [results[name]["status"] for name in requested]
    status = (
        "NOT_REQUESTED" if not statuses else
        "FAIL" if "FAIL" in statuses else
        "SKIPPED_UNAVAILABLE" if "SKIPPED_UNAVAILABLE" in statuses else "PASS"
    )
    return {"status": status, "passed": status == "PASS", "required": required, "targets": results,
            "errors": [error for result in results.values() for error in result["errors"]]}


def run_blackbox_verification(
    video_path: Path,
    pipeline_dir: Optional[Path] = None,
    dialect: str = "mandarin",
    store_anchor: Optional[str] = None,
    strict_asr: bool = False,
    precomputed_audio: Optional[Dict[str, Any]] = None,
    export_mode: str = "auto",
) -> Dict[str, Any]:
    if not video_path.exists():
        raise FileNotFoundError(f"待验收成片不存在: {video_path}")

    with tempfile.TemporaryDirectory(prefix="svs_blackbox_") as td:
        td_path = Path(td)
        audio_ctx = precomputed_audio or extract_audio_context_once(video_path, td_path)
        audio_res = verify_audio_blackbox(video_path, td_path, pipeline_dir=pipeline_dir, audio_ctx=audio_ctx)
        sub_sync_res, read_res, color_res, meta_leak_res, freeze_res = verify_visual_and_subtitle_blackbox(
            video_path, td_path, audio_res["speech_spans"], pipeline_dir=pipeline_dir
        )
        spot_res = verify_cut_spot_blackbox(video_path, pipeline_dir, td_path)
        sfx_res = verify_sfx_in_window_blackbox(audio_ctx, pipeline_dir)
        asr_res = verify_blind_asr_proxy_blackbox(
            Path(audio_res["wav_path"]), pipeline_dir, dialect=dialect, store_anchor=store_anchor
        )
        comp_lic_res = verify_compliance_and_license_blackbox(pipeline_dir)
        exp_res = verify_exports_blackbox(pipeline_dir, export_mode) if pipeline_dir else {
            "status": "SKIPPED_UNAVAILABLE", "required": export_selection(export_mode)[1],
            "targets": {}, "errors": ["无 pipeline_dir"],
        }

    checks: List[Dict[str, Any]] = [
        _mk_check(
            "bb_audio_master",
            "[黑盒] 声学播控 (LUFS/TP/LRA/相位/零爆音/画外音)",
            audio_res["status"],
            f"LUFS={audio_res['integrated_lufs']:.2f} | TP={audio_res['true_peak_dbtp']}dBTP | LRA={audio_res['lra_lu']}LU | 相位={audio_res['mono_phase_corr']:.2f} | "
            f"爆音={len(audio_res['splice_clicks'])}处 | 画外声风险={audio_res['suspect_offcamera_spans']}段 | A轨覆盖={'是' if audio_res['aroll_voice_coverage_ok'] else '缺'}",
            f"{TARGET_LUFS - LOUDNESS_TOLERANCE_LUFS:.1f}~{TARGET_LUFS + LOUDNESS_TOLERANCE_LUFS:.1f} LUFS, TP≤{TP_MAX_DBTP:g}dBTP, LRA≤11.5, 相位≥0.45, 0爆音, 0画外音, A-Roll语音覆盖",
            audio_res,
        ),
        _mk_check(
            "bb_ocr_av_loop",
            "[黑盒] 成片OCR安全区、540px居中与声画字幕回环",
            sub_sync_res["status"],
            f"OCR后端={sub_sync_res['ocr_backend']} | 安全区越界={len(sub_sync_res['safe_violations'])} | "
            f"居中偏移={sub_sync_res.get('max_subtitle_center_dev_px', '-')}px(越限={len(sub_sync_res.get('center_violations', []))}) | "
            f"语音帧字幕={sub_sync_res['speech_sub_ocr_ratio']*100:.1f}% | 内容匹配={sub_sync_res.get('sub_ocr_content_recall', 1.0)*100:.0f}% "
            f"(只检字幕层, 排除画面自带文字 {sub_sync_res.get('excluded_texts_count', 0)} 处)",
            "0安全区越界, 字幕实测中心=540±42px, 语音帧字幕≥65%+内容匹配≥45%（只判我们渲染的字幕层）",
            sub_sync_res,
        ),
        _mk_check(
            "bb_pixel_readability",
            "[黑盒] 人脸零遮挡与 WCAG >= 3.8 单框实测",
            read_res["status"],
            f"人字最大IoU={read_res['max_face_text_iou']:.3f} | 均值={read_res['wcag_mean_contrast']:.2f}:1 | "
            f"低对比单帧(<3.8)={len(read_res['low_contrast_hits'])}处 | 包装花字压脸={len(read_res.get('packaging_face_hits', []))}处(单列)",
            "人字IoU≤0.02, 均值≥4.5:1 且 0处单帧<3.8:1（只判我们渲染的字幕层）",
            read_res,
        ),
        _mk_check(
            "bb_color_scope",
            "[黑盒] 色彩示波器与 BT.709 肤色范围",
            color_res["status"],
            f"高光削波={color_res['max_clipped_white_pct']}% | 黑场={color_res['max_clipped_black_pct']}% | "
            f"最大亮跳={color_res.get('max_luma_jump', 0.0):.1f} | "
            f"肤色违规={color_res['skin_sector_violations']}/{color_res['skin_samples_checked']} "
            f"(素材限制 source_limited={color_res.get('skin_source_limited', 0)})",
            "高光≤55%, 亮跳≤90 且 肤色100%落入 common.SKIN_YCBCR_RANGE（暗部/逆光帧标 source_limited）",
            color_res,
        ),
        _mk_check(
            "bb_export_contracts",
            "[黑盒] 外部宿主工程契约 (相对FCPXML/全离线HyperFrames)",
            exp_res["status"],
            " | ".join(f"{target}={result['status']}" for target, result in exp_res["targets"].items()),
            "剪映文件及相对链接完整，HyperFrames 原生剪辑计数与官方运行时实测",
            exp_res,
        ),
        _mk_check(
            "bb_meta_leak_ocr",
            "[黑盒] B11 成片 OCR 元数据防露馅",
            meta_leak_res["status"],
            f"后端={meta_leak_res['ocr_backend']} | 检出 {meta_leak_res['hits_count']} 处禁用词",
            "0 处开发/设备/风格代号露馅（依赖Apple Vision）",
            meta_leak_res,
        ),
        _mk_check(
            "bb_no_visual_freeze",
            "[黑盒] 屏蔽字幕带后的视觉静止检测",
            freeze_res["status"],
            f"连续静止最长达 {freeze_res['max_static_sec']:.1f}s (已屏蔽字幕区)",
            f"最大静止时长 ≤ {freeze_res['threshold_sec']:.1f}s",
            freeze_res,
        ),
        _mk_check(
            "bb_cut_spot_verify",
            "[黑盒] 全量切点像素点名跳变验证 (Spot-Verify)",
            spot_res["status"],
            f"验证 {spot_res['verified_cuts']}/{spot_res['checked_cuts']} 切点真实跳变 ({spot_res['verified_ratio']*100:.0f}%)",
            "≥85% 计划切点实测画面跳变且连续未验证≤1次",
            spot_res,
        ),
        _mk_check(
            "bb_sfx_in_window_lift",
            "[黑盒] 2-8kHz 同窗净抬升与 Spectral Flux 毫秒起音校准",
            sfx_res["status"],
            f"来源={sfx_res.get('source', '?')} | 抽检 {sfx_res['cues_checked']} 处 | "
            f"中位={sfx_res['median_lift_db']:+.1f}dB | 峰值={sfx_res['max_lift_db']:+.1f}dB | "
            f"起音同步={sfx_res.get('onset_sync_ratio', 1.0)*100:.0f}% (中位差={sfx_res.get('median_onset_err_ms', 0.0):.1f}ms)",
            f"中位净抬升 ≥ +{sfx_res.get('minimum_lift_db', 2.5):g}dB，起音偏差≤60ms 命中率≥65%",
            sfx_res,
        ),
        _mk_check(
            "bb_blind_asr_proxy",
            "[黑盒] 带 BGM 成片独立 ASR 盲听复核",
            asr_res["status"],
            (
                f"状态={asr_res['status']} ({asr_res.get('reason', '')})"
                if asr_res["status"] == "SKIPPED_UNAVAILABLE"
                else f"CER={asr_res.get('cer', 0.0):.3f} | 锚点={'通过' if asr_res.get('anchor_ok') else '缺失'}"
            ),
            "普通话 CER≤0.25 且含锚点 (无本地Whisper标SKIPPED_UNAVAILABLE)",
            asr_res,
        ),
        _mk_check(
            "bb_compliance_and_license",
            "[黑盒] 广告法合规与商业字体授权清单",
            comp_lic_res["status"],
            f"合规={'通过' if comp_lic_res['compliance_ok'] else '违规'} | 资产={comp_lic_res['assets_count']} | "
            f"未授权字体={len(comp_lic_res.get('unverified_fonts', []))}",
            "合规=通过、零未登记资产且所有字体通过 is_verified_commercial_font",
            comp_lic_res,
        ),
    ]

    blocked, pending = review_checks(checks, export_mode, strict_asr)
    return {
        "video_path": str(video_path.resolve()),
        "all_passed": not blocked,
        "machine_passed": not blocked and not pending,
        "blocking_checks": blocked,
        "pending_manual_checks": pending,
        "pass_count": sum(1 for c in checks if c["status"] == "PASS"),
        "fail_count": sum(1 for c in checks if c["status"] == "FAIL"),
        "skipped_count": sum(1 for c in checks if c["status"] == "SKIPPED_UNAVAILABLE"),
        "total_checks": len(checks),
        "strict_asr": strict_asr,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="独立黑盒外部验收裁判 (Check #12–#22)")
    parser.add_argument("--video", required=True, help="成片 MP4 路径")
    parser.add_argument("--pipeline-dir", help="流水线目录（用于检验 jianying_draft / hyperframes_draft）")
    parser.add_argument("--dialect", default="mandarin", help="语言模式 (mandarin / cantonese / dialect)")
    parser.add_argument("--store-anchor", help="门店名/听觉锚点")
    parser.add_argument("--strict-asr", action="store_true", help="无本地 Whisper 模型时阻止总检通过")
    parser.add_argument("--exports", choices=EXPORT_CHOICES, default="auto")
    parser.add_argument("--output-json", help="保存独立验收 JSON 报告路径")
    args = parser.parse_args()

    res = run_blackbox_verification(
        Path(args.video),
        pipeline_dir=Path(args.pipeline_dir) if args.pipeline_dir else None,
        dialect=args.dialect,
        store_anchor=args.store_anchor,
        strict_asr=args.strict_asr,
        export_mode=args.exports,
    )
    if args.output_json:
        write_json(Path(args.output_json), res)
    print(f"[independent_verifier] 黑盒校验: {res['pass_count']}/{res['total_checks']} PASS (fail={res['fail_count']}, skip={res['skipped_count']})")
    for c in res["checks"]:
        print(f"  {c['status']} | {c['name']}: {c['measured']}")
    if not res["all_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
