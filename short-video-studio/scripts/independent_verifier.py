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
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    EXPORT_CHOICES,
    HYPERFRAMES_VERSION,
    EDIT_PROFILES,
    sha256_file,
    export_selection,
    review_checks,
    DEFAULT_FPS,
    LEAK_REGEX_PATTERNS,
    SKIN_YCBCR_RANGE,
    SUBTITLE_SAFE_ZONE,
    TARGET_LUFS,
    compute_sfx_band_windows,
    find_bin,
    find_cached_whisper_model,
    get_huggingface_hub_dir,
    is_verified_commercial_font,
    maybe_reexec_in_venv,
    measure_lufs_ebur128,
    read_json,
    read_wav_as_48k_stereo,
    run_cmd,
    write_json,
)

maybe_reexec_in_venv()

import numpy as np
from PIL import Image
from step2_transcribe import _clean_zh_text, vad_speech_intervals
from step8_mix_audio import compute_mono_phase_correlation
from vision_inspector import check_bbox_iou, inspect_images


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
    mono = audio_48k.mean(axis=1)
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
    mono = audio_48k.mean(axis=1)
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
        dur = (n_hops * 0.02) - s_t
        if dur >= min_dur and s_t > 0.2 and (n_hops * 0.02) < total_dur - 0.5:
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
    y16_st, _ = read_wav_as_48k_stereo(ext_wav, target_sr=16000)
    y16 = y16_st.mean(axis=1)
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

    # 提取 300–3400Hz 纯人声语谱带衡量每句电平，避免低频鼓点/BGM 高频误导画外音检测
    try:
        from scipy.signal import butter, sosfiltfilt
        sos_v = butter(4, [300.0, 3400.0], btype="bandpass", fs=16000, output="sos")
        y16_vocal = sosfiltfilt(sos_v, y16.astype(np.float64))
    except Exception:
        y16_vocal = y16

    span_rms_dbs: List[float] = []
    for s, e in spans:
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

    lufs_ok = abs(r128["integrated_lufs"] - TARGET_LUFS) <= 1.2
    tp_ok = r128["true_peak_dbtp"] <= -0.5
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
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"fps=2,scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}",
        "-q:v", "2",
        str(frames_dir / "f_%04d.jpg"),
    ])
    frame_files = sorted(frames_dir.glob("f_*.jpg"))
    if not frame_files:
        raise RuntimeError("黑盒视觉抽帧失败，成片视频流可能损坏。")

    vis_raw = inspect_images(frame_files)
    vis_map: Dict[str, Dict[str, Any]] = {}
    for k, v in vis_raw.items():
        vis_map[str(k)] = v
        vis_map[str(Path(k).resolve())] = v

    static_limit = EDIT_PROFILES["talking"]["static_sec"]
    expected_aroll_has_face = False
    expected_sub_text = ""
    if pipeline_dir and pipeline_dir.exists():
        timeline = pipeline_dir / "04_edl.json"
        if timeline.is_file():
            style = read_json(timeline).get("edit_profile", "talking")
            static_limit = EDIT_PROFILES.get(style, EDIT_PROFILES["talking"])["static_sec"]
        mf_p = pipeline_dir / "01_manifest.json"
        if mf_p.is_file():
            try:
                mf = read_json(mf_p)
                expected_aroll_has_face = any(
                    it.get("role") == "a_roll" and bool(it.get("faces"))
                    for it in (mf.get("items") or [])
                )
            except Exception:
                pass
        sub_p = pipeline_dir / "05_subtitles.json"
        if sub_p.is_file():
            try:
                subs_j = read_json(sub_p)
                expected_sub_text = "".join(
                    str(e.get("text") or "") for e in (subs_j.get("entries") or []) if not e.get("suppressed")
                )
            except Exception:
                pass

    safe_violations: List[str] = []
    face_occlusions: List[str] = []
    max_face_text_iou = 0.0
    wcag_ratios: List[float] = []
    low_contrast_hits: List[str] = []
    meta_leak_hits: List[str] = []
    clipped_white_pcts: List[float] = []
    clipped_black_pcts: List[float] = []
    mean_lumas: List[float] = []
    skin_checked = 0
    skin_violations = 0
    speech_frames_checked = 0
    speech_frames_with_sub_ocr = 0
    masked_thumbs: List[np.ndarray] = []
    sub_ocr_segments: List[str] = []

    cb_min = float(SKIN_YCBCR_RANGE["cb_min"])
    cb_max = float(SKIN_YCBCR_RANGE["cb_max"])
    cr_min = float(SKIN_YCBCR_RANGE["cr_min"])
    cr_max = float(SKIN_YCBCR_RANGE["cr_max"])

    for idx, fp in enumerate(frame_files):
        t_sec = idx * 0.5
        vr = vis_map.get(str(fp.resolve()), {})
        faces = vr.get("faces") or []
        texts = [t for t in (vr.get("texts") or []) if float(t.get("conf", 0.0)) >= 0.32 and len(str(t.get("text", "")).strip()) >= 2]

        with Image.open(fp) as im:
            rgb = np.asarray(im.convert("RGB"))
            thumb = np.asarray(im.convert("RGB").resize((270, 480)), dtype=np.float32)

        thumb_masked = thumb.copy()
        thumb_masked[260:370, :, :] = 0.0
        masked_thumbs.append(thumb_masked)

        luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
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
            if roi.size >= 16:
                r, g, b = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
                yy = 16.0 + (0.2126 * r + 0.7152 * g + 0.0722 * b) * (219.0 / 255.0)
                cb = 128.0 + (-0.114572 * r - 0.385428 * g + 0.5 * b) * (224.0 / 255.0)
                cr = 128.0 + (0.5 * r - 0.454153 * g - 0.045847 * b) * (224.0 / 255.0)
                valid_px = (yy >= 40.0) & (yy <= 235.0)
                if np.any(valid_px):
                    cb_med = float(np.median(cb[valid_px]))
                    cr_med = float(np.median(cr[valid_px]))
                else:
                    cb_med = float(np.mean(cb))
                    cr_med = float(np.mean(cr))
                skin_checked += 1
                if not (cb_min <= cb_med <= cb_max and cr_min <= cr_med <= cr_max):
                    skin_violations += 1

        in_speech = any((s + 0.15) <= t_sec <= (e - 0.15) for s, e in speech_spans)
        if in_speech:
            speech_frames_checked += 1
            if any(float(t["y"]) >= 0.50 or len(str(t["text"]).strip()) >= 4 for t in texts):
                speech_frames_with_sub_ocr += 1

        rel_lum = _relative_luminance(rgb)
        for t in texts:
            txt_raw = str(t.get("text", "")).strip()
            m_leak = META_LEAK_RE.search(txt_raw)
            if m_leak:
                meta_leak_hits.append(f"t={t_sec:.1f}s 命中'{m_leak.group(0)}' ({txt_raw[:16]})")

            px1 = int(round(float(t["x"]) * CANVAS_WIDTH))
            py1 = int(round(float(t["y"]) * CANVAS_HEIGHT))
            pw = max(4, int(round(float(t["w"]) * CANVAS_WIDTH)))
            ph = max(4, int(round(float(t["h"]) * CANVAS_HEIGHT)))
            px2, py2 = px1 + pw, py1 + ph

            if py1 >= SUBTITLE_SAFE_ZONE["y_min"] - 40 and py2 <= SUBTITLE_SAFE_ZONE["y_max"] + 25:
                cleaned_o = _clean_zh_text(txt_raw)
                if cleaned_o and (not sub_ocr_segments or cleaned_o != sub_ocr_segments[-1]):
                    sub_ocr_segments.append(cleaned_o)

            if py1 < 145 or py2 > 1495 or px1 < 45 or (px2 > 915 and py2 > 660):
                safe_violations.append(f"t={t_sec:.1f}s '{txt_raw[:10]}' ({px1},{py1})-({px2},{py2})")

            t_norm = {"x": float(t["x"]), "y": float(t["y"]), "w": float(t["w"]), "h": float(t["h"])}
            for f in faces:
                fb = f.get("bbox")
                if not fb:
                    continue
                iou = check_bbox_iou(t_norm, fb)
                if iou > 0.02:
                    face_occlusions.append(f"t={t_sec:.1f}s '{txt_raw[:8]}' IoU={iou:.3f}")
                max_face_text_iou = max(max_face_text_iou, iou)

            in_sub_or_pkg = py1 >= 150 and py2 <= SUBTITLE_SAFE_ZONE["y_max"] + 25
            if in_sub_or_pkg and ph >= 24:
                # 纳入文字外圈 6px Halo/描边带联合采样前后景极端相对亮度比
                bx0 = max(0, px1 - 6)
                by0 = max(0, py1 - 6)
                bx1 = min(CANVAS_WIDTH, px2 + 6)
                by1 = min(CANVAS_HEIGHT, py2 + 6)
                patch = rel_lum[by0:by1, bx0:bx1]
                if patch.size >= 36:
                    l_max = float(np.percentile(patch, 92))
                    l_min = float(np.percentile(patch, 10))
                    ratio = (l_max + 0.05) / (l_min + 0.05)
                    wcag_ratios.append(ratio)
                    if ratio < 3.8:
                        low_contrast_hits.append(f"t={t_sec:.1f}s '{txt_raw[:8]}' ratio={ratio:.2f}")

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

    p_res = run_cmd([
        ffmpeg, "-hide_banner", "-t", "3.0", "-i", str(video_path),
        "-vf", "select='gt(scene,0.08)',showinfo", "-f", "null", "-",
    ], check=False)
    scene_cuts_3s = re.findall(r"pts_time:([0-9.]+)", p_res.stderr)

    ocr_backend = next(iter(vis_map.values()), {}).get("backend", "none") if vis_map else "none"
    ocr_supported = (ocr_backend == "apple_vision")

    sub_match_ratio = (speech_frames_with_sub_ocr / float(speech_frames_checked)) if speech_frames_checked > 0 else 1.0
    exp_clean = _clean_zh_text(expected_sub_text)
    ocr_sub_joined = "".join(sub_ocr_segments)
    sub_content_recall = round(_subsequence_ratio(exp_clean, ocr_sub_joined), 3) if exp_clean else 1.0

    if not ocr_supported:
        sub_sync_res = {
            "status": "SKIPPED_UNAVAILABLE",
            "passed": False,
            "ocr_backend": ocr_backend,
            "ocr_supported": False,
            "frames_checked": len(frame_files),
            "safe_violations": [],
            "speech_frames_checked": speech_frames_checked,
            "speech_sub_ocr_ratio": 0.0,
            "sub_ocr_content_recall": 0.0,
            "first_3s_scene_cuts": len(scene_cuts_3s),
        }
        readability_res = {
            "status": "SKIPPED_UNAVAILABLE",
            "passed": False,
            "max_face_text_iou": 0.0,
            "face_occlusions": [],
            "wcag_mean_contrast": 0.0,
            "wcag_samples": 0,
            "low_contrast_hits": [],
        }
        meta_leak_res = {
            "status": "SKIPPED_UNAVAILABLE",
            "passed": False,
            "ocr_backend": ocr_backend,
            "hits_count": 0,
            "hits": [],
        }
    else:
        sub_content_ok = True if not exp_clean else (sub_content_recall >= 0.45)
        sub_ok = (len(safe_violations) == 0) and (sub_match_ratio >= 0.65) and sub_content_ok
        sub_sync_res = {
            "status": "PASS" if sub_ok else "FAIL",
            "passed": sub_ok,
            "ocr_backend": ocr_backend,
            "ocr_supported": True,
            "frames_checked": len(frame_files),
            "safe_violations": safe_violations[:8],
            "speech_frames_checked": speech_frames_checked,
            "speech_sub_ocr_ratio": round(sub_match_ratio, 3),
            "sub_ocr_content_recall": sub_content_recall,
            "first_3s_scene_cuts": len(scene_cuts_3s),
        }

        # 绝不在零对比样本时硬写 8.0 伪通过；无期望文本时仅检查零人脸遮挡
        expect_text_on_screen = bool(exp_clean or speech_frames_checked > 0)
        if wcag_ratios:
            mean_wcag = round(float(np.mean(wcag_ratios)), 2)
            wcag_ok = (len(low_contrast_hits) == 0) and (mean_wcag >= 4.5)
        else:
            mean_wcag = 0.0
            wcag_ok = not expect_text_on_screen
        readability_ok = (max_face_text_iou <= 0.02) and wcag_ok
        readability_res = {
            "status": "PASS" if readability_ok else "FAIL",
            "passed": readability_ok,
            "max_face_text_iou": round(max_face_text_iou, 4),
            "face_occlusions": face_occlusions,
            "wcag_mean_contrast": mean_wcag,
            "wcag_samples": len(wcag_ratios),
            "low_contrast_hits": low_contrast_hits[:5],
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
    luma_jumps = [abs(mean_lumas[i] - mean_lumas[i - 1]) for i in range(1, len(mean_lumas))]
    max_luma_jump = round(max(luma_jumps or [0.0]), 1)
    skin_missing_fail = bool(expected_aroll_has_face and skin_checked == 0)
    luma_jump_ok = (max_luma_jump <= 90.0)
    color_ok = (max_white <= 55.0) and (max_black <= 92.0) and (skin_violations == 0) and (not skin_missing_fail) and luma_jump_ok
    color_res = {
        "status": "PASS" if color_ok else "FAIL",
        "passed": color_ok,
        "max_clipped_white_pct": max_white,
        "max_clipped_black_pct": max_black,
        "skin_samples_checked": skin_checked,
        "skin_sector_violations": skin_violations,
        "skin_missing_when_expected": skin_missing_fail,
        "max_luma_jump": max_luma_jump,
        "luma_jump_ok": luma_jump_ok,
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
    req_frames = sorted({max(0, cf - 2) for cf in cuts} | {cf + 2 for cf in cuts})
    spot_dir = tmp_dir / "spot_cuts"
    spot_dir.mkdir(parents=True, exist_ok=True)
    sel_expr = "+".join(f"eq(n\\,{fn})" for fn in req_frames)
    res = run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "info",
        "-i", str(video_path),
        "-vf", f"select={sel_expr},showinfo,scale=270:480",
        "-fps_mode", "passthrough", "-q:v", "3",
        str(spot_dir / "sp_%04d.jpg"),
    ], check=False)

    # 通过 showinfo 中的 pts_time 反算原视频帧号（比 zip(req_frames, files) 绝对防错位）
    emitted_frames: List[int] = []
    for m in re.finditer(r"n:\s*\d+.*?pts_time:([0-9.]+)", res.stderr or ""):
        pts_t = float(m.group(1))
        emitted_frames.append(int(round(pts_t * fps)))

    files = sorted(spot_dir.glob("sp_*.jpg"))
    frame_by_n: Dict[int, np.ndarray] = {}
    for idx_f, fp in enumerate(files):
        in_fn = emitted_frames[idx_f] if idx_f < len(emitted_frames) else (req_frames[idx_f] if idx_f < len(req_frames) else -1)
        if in_fn < 0:
            continue
        with Image.open(fp) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.float32)
        # 双带掩膜：同时屏蔽底部字幕区 [285:380] 与顶部花字包装区 [46:88]，防包装切换冒充切镜
        arr[285:380, :, :] = 0.0
        arr[46:88, :, :] = 0.0
        matched_req = min(req_frames, key=lambda rf: abs(rf - in_fn))
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
    minimum_lift = 2.5
    edit_path = pipeline_dir / "04_edl.json"
    if edit_path.is_file() and read_json(edit_path).get("edit_profile") == "explainer":
        minimum_lift = 1.0

    nosfx_p = pipeline_dir / "08_stem_nosfx.wav"
    audit_win_p = pipeline_dir / "08_sfx_audit_window.json"

    try:
        from scipy.signal import butter, sosfiltfilt
        sos = butter(4, [2000.0, 8000.0], btype="bandpass", fs=sr1, output="sos")
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
    lift_ok = med_lift >= minimum_lift
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

    def _conv(m: re.Match) -> str:
        tok = m.group(0)
        if len(tok) == 4 and (tok.startswith("19") or tok.startswith("20")):
            return "".join(mp.get(ch, ch) for ch in tok)
        if tok.isdigit() and len(tok) <= 8:
            return _int_to_zh(int(tok))
        return "".join(mp.get(ch, ch) for ch in tok)

    return re.sub(r"\d+", _conv, s)


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
    ref_say_text = "".join(str(s.get("say_text") or s.get("text") or "") for s in sents_ae)
    if ref_say_text == ref_show_text and (pipeline_dir / "02_transcript.json").is_file():
        try:
            tr = read_json(pipeline_dir / "02_transcript.json")
            tr_segs = [s for s in (tr.get("segments") or tr.get("sentences") or []) if not s.get("is_filler")]
            cand_say = "".join(str(s.get("say_text") or s.get("say") or s.get("text") or "") for s in tr_segs)
            if cand_say:
                ref_say_text = cand_say
        except Exception:
            pass

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
    if asr_lang == "yue" and "large-v3" not in model_name and "turbo" not in model_name:
        return {
            "status": "SKIPPED_UNAVAILABLE", "passed": False, "cer": None, "anchor_ok": False,
            "reason": f"已缓存的 {model_name} 缺少粤语 yue 语言模型",
        }
    asr_prompt = "以下是粤语简体中文口播内容：" if asr_lang == "yue" else "以下是普通话简体中文口播内容："

    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="cpu", compute_type="int8", local_files_only=True)
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
        "model": model_name,
        "cer": cer,
        "anchor_ok": anchor_ok,
        "hyp_preview": hyp_text[:48],
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

    extra_audits = []
    if (pipeline_dir / "05_subtitles.json").is_file():
        subs = read_json(pipeline_dir / "05_subtitles.json")
        sents = [{"id": e["id"], "text": e["text"]} for e in subs.get("entries", []) if not e.get("suppressed")]
        # 只读调用 pipeline_dir=None，严禁覆写覆盖上游的 real_voice drop 留痕！
        sub_audit = audit_script_compliance(sents, source_type="subtitles", pipeline_dir=None, auto_action=False)
        comp_ok = comp_ok and bool(sub_audit.get("passed", False))
        extra_audits.append(sub_audit)

    if (pipeline_dir / "06_packaging.json").is_file():
        pkg = read_json(pipeline_dir / "06_packaging.json")
        psents = [{"id": e["id"], "text": str(e.get("text_or_asset", ""))} for e in pkg.get("visual_elements", [])]
        if psents:
            p_audit = audit_script_compliance(psents, source_type="packaging", pipeline_dir=None, auto_action=False)
            comp_ok = comp_ok and bool(p_audit.get("passed", False))
            extra_audits.append(p_audit)

    lic_p = next(
        (p for p in (pipeline_dir / "asset_license_manifest.json", pipeline_dir / "00_asset_licenses.json") if p.is_file()),
        None,
    )
    if lic_p:
        lic = read_json(lic_p)
    else:
        lic = {}

    unverified_fonts: List[str] = []
    file_mismatches: List[str] = []
    external_documents = 0
    for item in lic.get("assets", []):
        asset = Path(item["path"])
        generated = item.get("source_tier") == "project_generated"
        if not (generated and not asset.exists()):
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
                ref = node.attrib.get("ref") or node.attrib.get("format")
                if not ref:
                    continue
                valid_pool = text_style_ids if node.tag == "text-style" else res_ids
                if ref not in valid_pool:
                    fcpxml_ok = False
                    errors.append(f"FCPXML 存在悬空资源引用 ref={ref}")

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
    sources.extend(re.findall(r'url\(["\']?([^"\')]+)', entry.read_text(encoding="utf-8")))
    for src in set(sources):
        resolved = (draft / urllib.parse.unquote(src)).resolve()
        if ":" in src or Path(src).is_absolute() or not resolved.is_relative_to(draft) or not resolved.is_file():
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
                results[name] = {"status": "FAIL", "errors": [status.get("message") or "工程导出失败"]}
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
            "[黑盒] 声学播控 (TP/LRA/相位/零爆音/画外音)",
            audio_res["status"],
            f"TP={audio_res['true_peak_dbtp']}dBTP | LRA={audio_res['lra_lu']}LU | 相位={audio_res['mono_phase_corr']:.2f} | "
            f"爆音={len(audio_res['splice_clicks'])}处 | 画外声风险={audio_res['suspect_offcamera_spans']}段 | A轨覆盖={'是' if audio_res['aroll_voice_coverage_ok'] else '缺'}",
            "TP≤-0.5, LRA≤11.5, 相位≥0.45, 0爆音, 0画外音, A-Roll语音覆盖",
            audio_res,
        ),
        _mk_check(
            "bb_ocr_av_loop",
            "[黑盒] 成片OCR安全区与声画字幕回环",
            sub_sync_res["status"],
            f"OCR后端={sub_sync_res['ocr_backend']} | 安全区越界={len(sub_sync_res['safe_violations'])} | "
            f"语音帧字幕={sub_sync_res['speech_sub_ocr_ratio']*100:.1f}% | 内容匹配={sub_sync_res.get('sub_ocr_content_recall', 1.0)*100:.0f}%",
            "0安全区越界且语音帧字幕≥65%+内容匹配≥45%（依赖Apple Vision）",
            sub_sync_res,
        ),
        _mk_check(
            "bb_pixel_readability",
            "[黑盒] 人脸零遮挡与 WCAG >= 3.8 单框实测",
            read_res["status"],
            f"人字最大IoU={read_res['max_face_text_iou']:.3f} | 均值={read_res['wcag_mean_contrast']:.2f}:1 | "
            f"低对比单帧(<3.8)={len(read_res['low_contrast_hits'])}处",
            "人字IoU≤0.02, 均值≥4.5:1 且 0处单帧<3.8:1",
            read_res,
        ),
        _mk_check(
            "bb_color_scope",
            "[黑盒] 色彩示波器与 BT.709 肤色范围",
            color_res["status"],
            f"高光削波={color_res['max_clipped_white_pct']}% | 黑场={color_res['max_clipped_black_pct']}% | "
            f"最大亮跳={color_res.get('max_luma_jump', 0.0):.1f} | "
            f"肤色违规={color_res['skin_sector_violations']}/{color_res['skin_samples_checked']}",
            "高光≤55%, 亮跳≤90 且 肤色100%落入 common.SKIN_YCBCR_RANGE",
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
