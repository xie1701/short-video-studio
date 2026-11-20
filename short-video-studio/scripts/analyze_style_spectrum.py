#!/usr/bin/env python3
"""
[20条参考包装视频逐帧特征提取与「风格谱」构建引擎]
对指定目录中的所有参考视频执行逐帧级多维分析：
1. 镜头时长分布（全帧像素差分 + 直方图突变 + 微跳剪/推拉检测）
2. BGM 速度（BPM）、节拍网格、综合响度（LUFS）、静音气口、高频瞬态音效密度
3. 画面切点与节拍/音频事件对齐率（±1帧 / ±3帧 / J-cut 提前量）
4. 逐帧色彩统计（YAVG、对比度、饱和度、冷暖色温、暗部/中间调/高光偏色、Top-5主色）与逆向 33x33x33 .cube LUT
5. 字幕位置、字号、主色/高亮色、描边宽度与颜色、底框与出场动效分析（macOS Vision OCR + 像素形态学）
6. 导出每条视频的 3x3 高清关键帧图谱（keyframes/style_XX_contact.jpg）及原始汇总 JSON

输出目录默认：short-video-studio/assets/style_spectrum/
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    ASSETS_DIR,
    ffprobe_media,
    find_bin,
    find_commercial_font,
    get_cached_font,
    maybe_reexec_in_venv,
    measure_lufs_ebur128,
    parse_fps,
    parse_video_stream_geometry,
    run_cmd,
    write_cube_lut,
    write_json,
)
from vision_inspector import inspect_images

maybe_reexec_in_venv()

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DEFAULT_INPUT_DIR = ASSETS_DIR / "style_spectrum" / "samples"
DEFAULT_OUT_DIR = ASSETS_DIR / "style_spectrum"


def rgb_to_hex(rgb: Tuple[float, float, float]) -> str:
    r = max(0, min(255, int(round(rgb[0]))))
    g = max(0, min(255, int(round(rgb[1]))))
    b = max(0, min(255, int(round(rgb[2]))))
    return f"#{r:02X}{g:02X}{b:02X}"


def probe_video_meta(video_path: Path) -> Dict[str, Any]:
    """复用 common.ffprobe_media 与 parse_video_stream_geometry 获取视频物理规格。"""
    info = ffprobe_media(video_path)
    v_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    geom = parse_video_stream_geometry(v_stream)
    w = int(geom["display_width"])
    h = int(geom["display_height"])
    fps = parse_fps(v_stream.get("r_frame_rate") or v_stream.get("avg_frame_rate") or "24/1")
    if fps <= 0:
        fps = 24.0
    duration = float(info.get("format", {}).get("duration") or v_stream.get("duration") or 0.0)
    nb_frames = int(v_stream.get("nb_frames") or round(duration * fps))
    orientation = "portrait_9_16" if h > w else "landscape_16_9"
    return {
        "width": w,
        "height": h,
        "orientation": orientation,
        "aspect_ratio": "9:16" if h > w else "16:9",
        "fps": round(fps, 3),
        "nb_frames": nb_frames,
        "duration_sec": round(duration, 3),
        "file_size_mb": round(video_path.stat().st_size / (1024 * 1024), 2),
        "has_audio": bool(a_stream),
        "audio_sample_rate": int(a_stream.get("sample_rate") or 48000) if a_stream else 0,
    }


def analyze_frames_stream(video_path: Path, meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    流式解码视频的每一个单帧（缩放至宽度 180px），同步完成：
    1. 逐帧像素 L1 差分与直方图差分（精准区分硬切、微跳剪/推拉、叠化转场）
    2. 全片色彩统计（亮度、对比度、饱和度、色温、三段偏色、Top-5主色）
    """
    ffmpeg = find_bin("ffmpeg")
    w_src, h_src = meta["width"], meta["height"]
    w_s = 180
    h_s = max(2, int(round(h_src * w_s / max(1, w_src))) // 2 * 2)
    frame_bytes = w_s * h_s * 3

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"scale={w_s}:{h_s}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None

    prev_frame: Optional[np.ndarray] = None
    prev_hist: Optional[np.ndarray] = None

    diff_l1: List[float] = []
    diff_hist: List[float] = []

    color_sample_stride = max(1, int(round(meta["fps"] * 0.5)))  # 每 0.5 秒采样一帧用于色彩聚类
    sampled_pixels_list: List[np.ndarray] = []
    y_vals_all: List[float] = []
    y_p05_list: List[float] = []
    y_p50_list: List[float] = []
    y_p95_list: List[float] = []
    sat_vals_all: List[float] = []
    warm_vals_all: List[float] = []
    dark_ratios: List[float] = []
    bright_ratios: List[float] = []

    # 阴影/中间调/高光 RGB 累积
    zone_rgb_sums = {"shadow": np.zeros(3), "mid": np.zeros(3), "high": np.zeros(3)}
    zone_counts = {"shadow": 0, "mid": 0, "high": 0}

    frame_idx = 0
    while True:
        raw = proc.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            break
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((h_s, w_s, 3)).astype(np.float32) / 255.0

        # 计算亮度直方图（16 bins）
        luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
        hist, _ = np.histogram(luma, bins=16, range=(0.0, 1.0))
        hist = hist.astype(np.float32) / float(luma.size)

        if prev_frame is not None and prev_hist is not None:
            d_l1 = float(np.mean(np.abs(arr - prev_frame)))
            d_h = float(np.sum(np.abs(hist - prev_hist)))
            diff_l1.append(d_l1)
            diff_hist.append(d_h)
        else:
            diff_l1.append(0.0)
            diff_hist.append(0.0)

        prev_frame = arr
        prev_hist = hist

        if frame_idx % color_sample_stride == 0:
            y_vals_all.append(float(np.mean(luma)))
            p05, p50, p95 = np.percentile(luma, [5, 50, 95])
            y_p05_list.append(float(p05))
            y_p50_list.append(float(p50))
            y_p95_list.append(float(p95))
            dark_ratios.append(float(np.mean(luma < (50.0 / 255.0))))
            bright_ratios.append(float(np.mean(luma > (205.0 / 255.0))))

            cmax = np.max(arr, axis=2)
            cmin = np.min(arr, axis=2)
            sat = (cmax - cmin) / np.maximum(cmax, 1e-5)
            sat_vals_all.append(float(np.mean(sat)))
            warm_vals_all.append(float(np.mean(arr[:, :, 0] - arr[:, :, 2])))

            flat_rgb = arr.reshape(-1, 3)
            flat_y = luma.reshape(-1)
            for zname, mask in [
                ("shadow", flat_y < 0.28),
                ("mid", (flat_y >= 0.28) & (flat_y <= 0.72)),
                ("high", flat_y > 0.72),
            ]:
                cnt = int(np.sum(mask))
                if cnt > 0:
                    zone_rgb_sums[zname] += np.sum(flat_rgb[mask], axis=0)
                    zone_counts[zname] += cnt

            # 取步长像素做主色聚类
            sampled_pixels_list.append((flat_rgb[::8] * 255.0).astype(np.uint8))

        frame_idx += 1

    proc.wait()
    n_frames = max(1, frame_idx)
    fps = meta["fps"]

    d_l1_np = np.array(diff_l1, dtype=np.float32)
    d_h_np = np.array(diff_hist, dtype=np.float32)

    # 向量化局部滑窗中位数背景差分（排除单帧运动干扰）
    try:
        from scipy.ndimage import median_filter
        local_med = median_filter(d_l1_np, size=13, mode="nearest")
    except Exception:
        local_med = np.zeros_like(d_l1_np)
        for idx in range(len(d_l1_np)):
            s0 = max(0, idx - 6)
            s1 = min(len(d_l1_np), idx + 7)
            win = np.concatenate([d_l1_np[s0:idx], d_l1_np[idx + 1 : s1]])
            local_med[idx] = float(np.median(win)) if len(win) > 0 else 0.0

    min_gap_frames = max(4, int(round(0.25 * fps)))
    hard_cuts: List[int] = []
    jump_cuts: List[int] = []
    last_cut_f = -min_gap_frames

    for idx in range(1, len(d_l1_np)):
        if idx - last_cut_f < min_gap_frames:
            continue
        dl = float(d_l1_np[idx])
        dh = float(d_h_np[idx])
        lm = float(local_med[idx])
        nbr_max = max(float(d_l1_np[idx - 1]), float(d_l1_np[idx + 1]) if idx + 1 < len(d_l1_np) else 0.0)

        is_hard = (
            (dl >= 0.145 and dl >= 1.8 * (lm + 0.005))
            or (dh >= 0.28 and dl >= 0.085 and dl >= 2.0 * (lm + 0.005))
            or (dl >= 0.095 and dl >= 3.2 * (lm + 0.005) and dl >= 1.7 * (nbr_max + 0.005))
        )
        if is_hard:
            hard_cuts.append(idx)
            last_cut_f = idx
            continue

        # 微跳剪 / 推拉缩放 / 动效卡片切出（孤立尖峰）
        is_jump = (
            dl >= 0.032
            and dl >= 3.3 * (lm + 0.004)
            and dl >= 2.0 * (nbr_max + 0.004)
        )
        if is_jump:
            jump_cuts.append(idx)
            last_cut_f = idx

    # 检测渐变叠化/擦除转场（连续 4-24 帧高变化平台且无尖峰硬切）
    cut_frame_set = set(hard_cuts) | set(jump_cuts)
    transition_events = 0
    in_run = False
    run_len = 0
    for idx in range(1, len(d_l1_np)):
        if idx in cut_frame_set:
            in_run = False
            run_len = 0
            continue
        if d_l1_np[idx] > 0.030 and d_h_np[idx] > 0.025:
            if not in_run:
                in_run = True
                run_len = 1
            else:
                run_len += 1
        else:
            if in_run and 4 <= run_len <= 24:
                transition_events += 1
            in_run = False
            run_len = 0

    all_cuts = sorted(hard_cuts + jump_cuts)
    boundaries = [0] + all_cuts + [n_frames]
    shot_durs = [
        (boundaries[i + 1] - boundaries[i]) / fps
        for i in range(len(boundaries) - 1)
        if (boundaries[i + 1] - boundaries[i]) > 0
    ]
    if not shot_durs:
        shot_durs = [meta["duration_sec"]]

    shot_arr = np.array(shot_durs, dtype=np.float32)
    first_3s_cuts = [f for f in all_cuts if 0 < f <= int(round(3.0 * fps))]
    dur_min = max(1e-3, meta["duration_sec"] / 60.0)

    shot_stats = {
        "total_frames_analyzed": n_frames,
        "total_shots": len(shot_durs),
        "hard_cuts": len(hard_cuts),
        "jump_or_punch_cuts": len(jump_cuts),
        "dissolve_or_wipe_transitions": transition_events,
        "cuts_per_min": round(len(all_cuts) / dur_min, 2),
        "transitions_per_min": round((len(all_cuts) + transition_events) / dur_min, 2),
        "first_3s_cuts_count": len(first_3s_cuts),
        "first_3s_cut_times_sec": [round(f / fps, 3) for f in first_3s_cuts],
        "min_sec": round(float(np.min(shot_arr)), 3),
        "p25_sec": round(float(np.percentile(shot_arr, 25)), 3),
        "median_sec": round(float(np.median(shot_arr)), 3),
        "mean_sec": round(float(np.mean(shot_arr)), 3),
        "p75_sec": round(float(np.percentile(shot_arr, 75)), 3),
        "p90_sec": round(float(np.percentile(shot_arr, 90)), 3),
        "max_sec": round(float(np.max(shot_arr)), 3),
        "pct_under_1s": round(float(np.mean(shot_arr < 1.0) * 100.0), 1),
        "pct_1s_to_2_5s": round(float(np.mean((shot_arr >= 1.0) & (shot_arr <= 2.5)) * 100.0), 1),
        "pct_over_2_5s": round(float(np.mean(shot_arr > 2.5) * 100.0), 1),
        "cut_times_sec": [round(f / fps, 4) for f in all_cuts],
        "cut_frames": all_cuts,
    }

    # 汇总色彩统计与 Top-5 主色
    y_mean = float(np.mean(y_vals_all)) if y_vals_all else 0.5
    y_p05 = float(np.mean(y_p05_list)) if y_p05_list else 0.08
    y_p50 = float(np.mean(y_p50_list)) if y_p50_list else 0.48
    y_p95 = float(np.mean(y_p95_list)) if y_p95_list else 0.90
    sat_mean = float(np.mean(sat_vals_all)) if sat_vals_all else 0.25
    warmth = float(np.mean(warm_vals_all)) if warm_vals_all else 0.0

    zone_avg = {}
    for zname in ("shadow", "mid", "high"):
        if zone_counts[zname] > 0:
            avg_rgb = zone_rgb_sums[zname] / float(zone_counts[zname])
            zone_avg[zname] = [round(float(x * 255.0), 1) for x in avg_rgb]
        else:
            zone_avg[zname] = [128.0, 128.0, 128.0]

    # Top-5 主色提取（32级量化直方图合并）
    top_colors_hex: List[str] = []
    if sampled_pixels_list:
        all_px = np.concatenate(sampled_pixels_list, axis=0)
        q = (all_px // 32).astype(np.int32)
        keys = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
        uniq, counts = np.unique(keys, return_counts=True)
        order = np.argsort(-counts)
        picked_rgbs: List[np.ndarray] = []
        for k_idx in order:
            k_val = int(uniq[k_idx])
            mask = keys == k_val
            c_rgb = np.mean(all_px[mask], axis=0)
            if all(np.linalg.norm(c_rgb - p) >= 34.0 for p in picked_rgbs):
                picked_rgbs.append(c_rgb)
                top_colors_hex.append(rgb_to_hex(tuple(c_rgb)))
            if len(top_colors_hex) >= 5:
                break

    color_stats = {
        "y_mean_255": round(y_mean * 255.0, 1),
        "y_p05_255": round(y_p05 * 255.0, 1),
        "y_p50_255": round(y_p50 * 255.0, 1),
        "y_p95_255": round(y_p95 * 255.0, 1),
        "dynamic_range_255": round((y_p95 - y_p05) * 255.0, 1),
        "dark_ratio_pct": round(float(np.mean(dark_ratios) * 100.0), 1),
        "bright_ratio_pct": round(float(np.mean(bright_ratios) * 100.0), 1),
        "sat_mean_pct": round(sat_mean * 100.0, 1),
        "warmth_index": round(warmth * 255.0, 2),
        "tone_mood": (
            "暗黑高对比科技调" if y_mean < 0.34 and (y_p95 - y_p05) > 0.65
            else "深沉低饱和质感调" if y_mean < 0.42
            else "暖色高饱和门店/人物调" if warmth > 0.045
            else "清透冷中性/极简白调" if warmth < -0.015
            else "均衡自然高通透调"
        ),
        "shadow_rgb": zone_avg["shadow"],
        "midtone_rgb": zone_avg["mid"],
        "highlight_rgb": zone_avg["high"],
        "top5_palette_hex": top_colors_hex,
    }

    return {"shot_stats": shot_stats, "color_stats": color_stats}


def generate_reverse_cube_lut(color_stats: Dict[str, Any], out_lut_path: Path, title: str) -> Dict[str, Any]:
    """
    根据视频实测的暗/中/高三段偏色、动态范围对比度与饱和度，
    逆向拟合生成轻量精简的 17x17x17 3D .cube LUT 文件（可直接被 FFmpeg lut3d / 剪映加载）。
    """
    # 以中性 Rec.709 基准为参照计算相对调色偏移
    y_p05 = color_stats["y_p05_255"] / 255.0
    y_p50 = color_stats["y_p50_255"] / 255.0
    y_p95 = color_stats["y_p95_255"] / 255.0
    dyn = max(0.25, y_p95 - y_p05)
    contrast_gain = max(0.88, min(1.24, 0.80 / dyn if dyn < 0.72 else 0.96 + (dyn - 0.72) * 0.65))
    # 若本身偏暗且高对比，压实暗部；若高亮通透，轻微提亮中灰
    lift_offset = max(-0.045, min(0.045, (y_p05 - 0.06) * 0.35))
    mid_gamma_shift = max(-0.06, min(0.06, (y_p50 - 0.46) * 0.30))

    sat_scale = max(0.76, min(1.28, (color_stats["sat_mean_pct"] / 100.0) / 0.28))

    def zone_tint(rgb: List[float], strength: float) -> np.ndarray:
        arr = np.array(rgb, dtype=np.float32) / 255.0
        luma = float(0.2126 * arr[0] + 0.7152 * arr[1] + 0.0722 * arr[2])
        delta = (arr - luma) * strength
        return np.clip(delta, -0.065, 0.065)

    sh_tint = zone_tint(color_stats["shadow_rgb"], 0.65)
    md_tint = zone_tint(color_stats["midtone_rgb"], 0.55)
    hi_tint = zone_tint(color_stats["highlight_rgb"], 0.55)

    size = 17
    grid = np.linspace(0.0, 1.0, size, dtype=np.float32)
    # 注意 .cube 规范顺序：B 最外层，G 中间层，R 最内层
    bb, gg, rr = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([rr, gg, bb], axis=-1)  # (17, 17, 17, 3)

    # 1. 亮度对比与 Lift/Mid/Gain 曲线
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    # 柔滑 S 曲线
    rgb_c = 0.5 + (rgb - 0.5) * contrast_gain + lift_offset * (1.0 - luma[..., None]) + mid_gamma_shift * (4.0 * luma * (1.0 - luma))[..., None]
    rgb_c = np.clip(rgb_c, 0.0, 1.0)

    # 2. 饱和度调整
    luma_c = (0.2126 * rgb_c[..., 0] + 0.7152 * rgb_c[..., 1] + 0.0722 * rgb_c[..., 2])[..., None]
    rgb_s = luma_c + (rgb_c - luma_c) * sat_scale

    # 3. 阴影/中间调/高光三区色彩分离
    w_sh = np.clip(1.0 - luma / 0.42, 0.0, 1.0) ** 1.5
    w_hi = np.clip((luma - 0.55) / 0.45, 0.0, 1.0) ** 1.5
    w_md = np.clip(1.0 - w_sh - w_hi, 0.0, 1.0)
    tint_map = (
        w_sh[..., None] * sh_tint[None, None, None, :]
        + w_md[..., None] * md_tint[None, None, None, :]
        + w_hi[..., None] * hi_tint[None, None, None, :]
    )
    rgb_out = np.clip(rgb_s + tint_map, 0.0, 1.0)

    write_cube_lut(out_lut_path, rgb_out, size, title)

    # 返回近似 CSS Filter（用于 HyperFrames 浏览器端无 LUT 实时预览）
    css_brightness = round(1.0 + mid_gamma_shift * 1.1, 3)
    css_contrast = round(contrast_gain, 3)
    css_saturate = round(sat_scale, 3)
    css_sepia = round(max(0.0, min(0.25, color_stats["warmth_index"] / 120.0)), 3)
    return {
        "lut_file": out_lut_path.name,
        "lut_size": size,
        "contrast_gain": round(contrast_gain, 3),
        "sat_scale": round(sat_scale, 3),
        "shadow_tint_rgb": [round(float(x), 4) for x in sh_tint],
        "highlight_tint_rgb": [round(float(x), 4) for x in hi_tint],
        "css_filter_approx": f"brightness({css_brightness}) contrast({css_contrast}) saturate({css_saturate}) sepia({css_sepia})",
    }


def analyze_audio_and_alignment(
    video_path: Path,
    meta: Dict[str, Any],
    shot_stats: Dict[str, Any],
) -> Dict[str, Any]:
    """
    提取 16kHz 单声道波形与 EBU R128 响度，测定：
    - 综合响度 (LUFS) 与响度范围 (LRA)
    - 静音气口分布与语音句首 Onset 时间点
    - BGM 速度 (BPM) 与 4/4 节拍/强拍网格
    - 高频瞬态音效密度 (SFX/min)
    - 画面切点与节拍/音频事件对齐率 + J-cut 提前量
    """
    ffmpeg = find_bin("ffmpeg")
    if not meta["has_audio"]:
        return {"has_audio": False}

    # 1. 复用 common.measure_lufs_ebur128
    r128 = measure_lufs_ebur128(video_path, raise_on_error=False)
    integrated_lufs = float(r128.get("integrated_lufs", -23.0))
    lra_lu = float(r128.get("lra_lu", 6.0))
    true_peak = float(r128.get("true_peak_dbtp", -1.0))

    sil_res = run_cmd([
        ffmpeg, "-hide_banner", "-i", str(video_path),
        "-af", "silencedetect=noise=-38dB:d=0.16", "-f", "null", "-",
    ], check=False)
    sil_starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", sil_res.stderr)]
    sil_ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", sil_res.stderr)]
    sil_durs = [float(x) for x in re.findall(r"silence_duration:\s*([0-9.]+)", sil_res.stderr)]

    # 2. 提取 16kHz PCM 浮点波形
    pcm_res = subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sr = 16000
    audio = np.frombuffer(pcm_res.stdout, dtype=np.float32)
    if len(audio) < sr:
        return {"has_audio": False}

    # 3. 10ms 分帧短时频谱通量（低频 Beat 通量 + 高频 SFX 瞬态通量）
    n_fft = 512
    hop = 160  # 10ms = 100 Hz
    n_frames = (len(audio) - n_fft) // hop + 1
    if n_frames < 50:
        return {"has_audio": False}

    # 使用 stride 构造矩阵 (n_frames, n_fft)
    shape = (n_frames, n_fft)
    strides = (audio.strides[0] * hop, audio.strides[0])
    frames = np.lib.stride_tricks.as_strided(audio, shape=shape, strides=strides)
    win = np.hanning(n_fft).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * win, axis=1))  # bins: 0..256 (0..8000Hz, ~31.25Hz/bin)

    # 低频能量通量 (30-250 Hz -> bins 1..8) 与中低频 (250-2000Hz -> bins 8..64) 与高频瞬态 (2500-7500Hz -> bins 80..240)
    log_spec = np.log1p(spec * 10.0)
    diff_spec = np.maximum(0.0, np.diff(log_spec, axis=0, prepend=log_spec[:1]))
    flux_low = np.mean(diff_spec[:, 1:9], axis=1)
    flux_full = np.mean(diff_spec[:, 1:96], axis=1)
    flux_high = np.mean(diff_spec[:, 80:240], axis=1)

    # 4. BGM 速度 (BPM) 自相关测定（范围 72 ~ 164 BPM）
    # 混合低频鼓点通量与全频包络突变
    beat_env = 0.65 * flux_low + 0.35 * flux_full
    beat_env_c = beat_env - np.mean(beat_env)
    env_rate = 100.0  # 100 Hz

    # 计算自相关（最多取前 120s 保持速度且足够稳健）
    max_env_len = min(len(beat_env_c), 12000)
    sub_env = beat_env_c[:max_env_len]
    ac_full = np.correlate(sub_env, sub_env, mode="full")[max_env_len - 1 :]
    if ac_full[0] > 1e-8:
        ac_norm = ac_full / ac_full[0]
    else:
        ac_norm = ac_full

    lag_min = int(round(env_rate * 60.0 / 164.0))  # 164 BPM -> ~37 frames
    lag_max = int(round(env_rate * 60.0 / 72.0))   # 72 BPM -> ~83 frames
    cand_lags = np.arange(lag_min, min(lag_max + 1, len(ac_norm)))
    if len(cand_lags) > 0:
        # 加入温和的节奏先验偏好（100-130 BPM 权重微增，避免选到半拍/倍拍误判）
        cand_bpms = 60.0 * env_rate / cand_lags
        prior = np.exp(-0.5 * ((cand_bpms - 116.0) / 38.0) ** 2)
        scores = ac_norm[cand_lags] * (0.80 + 0.20 * prior)
        best_idx = int(np.argmax(scores))
        best_lag = int(cand_lags[best_idx])
        best_bpm = round(60.0 * env_rate / best_lag, 1)
        beat_strength = float(max(0.0, ac_norm[best_lag]))
    else:
        best_bpm = 110.0
        beat_strength = 0.0

    # 5. 相位搜索生成全片节拍与强拍时间轴
    beat_period = 60.0 / best_bpm
    phase_steps = max(12, int(round(beat_period * env_rate)))
    best_phase = 0.0
    best_phase_score = -1.0
    for p_idx in range(phase_steps):
        ph = (p_idx / phase_steps) * beat_period
        grid_idx = (np.arange(ph, min(meta["duration_sec"], 120.0), beat_period) * env_rate).astype(int)
        grid_idx = grid_idx[grid_idx < len(beat_env)]
        sc = float(np.mean(beat_env[grid_idx])) if len(grid_idx) > 0 else 0.0
        if sc > best_phase_score:
            best_phase_score = sc
            best_phase = ph

    beat_times = np.arange(best_phase, meta["duration_sec"], beat_period)
    downbeat_times = beat_times[::4]

    # 6. 检测语音句首 Onsets（结合静音结束时刻 + 能量突增点）
    rms_10ms = np.sqrt(np.mean(frames ** 2, axis=1))
    rms_thresh = max(0.012, float(np.percentile(rms_10ms, 25)) * 2.2)
    speech_onsets: List[float] = list(sil_ends)
    # 补充能量低谷转上升的点
    for k in range(8, len(rms_10ms) - 4):
        if rms_10ms[k - 1] < rms_thresh and np.mean(rms_10ms[k : k + 4]) > rms_thresh * 1.8:
            t_on = k * 0.01
            if not speech_onsets or min(abs(t_on - s) for s in speech_onsets) > 0.25:
                speech_onsets.append(round(t_on, 3))

    # 7. 高频瞬态音效检测（SFX Cue：如 Whoosh / Pop / Ding / Bass Hit）
    # 比较 30ms 高频通量与其前后 400ms 局部中位数的尖峰比
    med_high = np.zeros_like(flux_high)
    for k in range(len(flux_high)):
        s0, s1 = max(0, k - 20), min(len(flux_high), k + 21)
        med_high[k] = float(np.median(flux_high[s0:s1]))
    sfx_ratio = flux_high / (med_high + 1e-4)
    high_energy = np.mean(spec[:, 80:240], axis=1)
    high_thresh = float(np.percentile(flux_high, 88))
    sfx_times: List[float] = []
    last_sfx = -0.40
    for k in range(2, len(flux_high) - 2):
        t = k * 0.01
        if t - last_sfx < 0.38:
            continue
        if flux_high[k] > max(high_thresh, 0.08) and sfx_ratio[k] > 3.2 and high_energy[k] > np.median(high_energy) * 1.8:
            sfx_times.append(round(t, 3))
            last_sfx = t

    # 结合静音区间的底层能量判断 BGM 存在强度
    bgm_presence = "minimal_or_pure_voice"
    if len(sil_durs) > 0:
        # 无静音段说明要么语速极密要么全程铺了较高电平 BGM
        if len(sil_durs) <= 2 and meta["duration_sec"] > 30:
            bgm_presence = "continuous_bgm_bed"
        elif beat_strength > 0.14:
            bgm_presence = "rhythmic_bgm_clear"
        else:
            bgm_presence = "soft_ambient_bgm"
    elif meta["duration_sec"] > 20:
        bgm_presence = "continuous_bgm_bed"

    # 8. 计算画面切点与节拍/音频事件对齐率
    fps = meta["fps"]
    cut_times = np.array(shot_stats["cut_times_sec"], dtype=np.float32)
    all_audio_events = np.sort(np.unique(np.concatenate([
        beat_times,
        np.array(speech_onsets, dtype=np.float32) if speech_onsets else np.array([], dtype=np.float32),
        np.array(sfx_times, dtype=np.float32) if sfx_times else np.array([], dtype=np.float32),
    ])))

    if len(cut_times) > 0:
        # 到最近节拍的帧距
        beat_errs_f = [float(np.min(np.abs(beat_times - tc)) * fps) for tc in cut_times]
        beat_align_2f = float(np.mean(np.array(beat_errs_f) <= 2.0) * 100.0)

        # 到最近任意音频事件的帧距
        ev_errs_f = [float(np.min(np.abs(all_audio_events - tc)) * fps) for tc in cut_times]
        ev_align_1f = float(np.mean(np.array(ev_errs_f) <= 1.2) * 100.0)
        ev_align_3f = float(np.mean(np.array(ev_errs_f) <= 3.2) * 100.0)

        # J-cut 检验：切点比最近 speech_onset 提前 1~5 帧（0.03s ~ 0.22s）或音频先进
        jcut_hits = 0
        jcut_leads: List[float] = []
        if speech_onsets:
            on_arr = np.array(speech_onsets, dtype=np.float32)
            for tc in cut_times:
                diffs_f = (on_arr - tc) * fps  # >0 表示画面先切或声音稍后；<0 表示声音先进画面后切(J-cut)
                # 找最近的 onset
                nearest_df = float(diffs_f[int(np.argmin(np.abs(diffs_f)))])
                if -5.5 <= nearest_df <= -1.0:
                    jcut_hits += 1
                    jcut_leads.append(abs(nearest_df))
        jcut_ratio = float(jcut_hits / max(1, len(cut_times)) * 100.0)
        jcut_med_frames = round(float(np.median(jcut_leads)), 1) if jcut_leads else 0.0
    else:
        beat_align_2f = 0.0
        ev_align_1f = 0.0
        ev_align_3f = 0.0
        jcut_ratio = 0.0
        jcut_med_frames = 0.0

    dur_min = max(1e-3, meta["duration_sec"] / 60.0)
    return {
        "has_audio": True,
        "integrated_lufs": round(integrated_lufs, 2),
        "loudness_range_lu": round(lra_lu, 2),
        "true_peak_dbtp": round(true_peak, 2),
        "silence_count": len(sil_durs),
        "mean_pause_sec": round(float(np.mean(sil_durs)), 3) if sil_durs else 0.0,
        "speech_onset_count": len(speech_onsets),
        "estimated_bpm": best_bpm,
        "beat_strength_score": round(beat_strength, 3),
        "bgm_presence": bgm_presence,
        "sfx_transient_count": len(sfx_times),
        "sfx_per_min": round(len(sfx_times) / dur_min, 2),
        "cut_beat_align_2f_pct": round(beat_align_2f, 1),
        "cut_event_align_1f_pct": round(ev_align_1f, 1),
        "cut_event_align_3f_pct": round(ev_align_3f, 1),
        "j_cut_detected_pct": round(jcut_ratio, 1),
        "j_cut_median_lead_frames": jcut_med_frames,
    }


def extract_frames_and_analyze_subtitles(
    video_path: Path,
    meta: Dict[str, Any],
    contact_out: Optional[Path],
    video_id: str,
) -> Dict[str, Any]:
    """
    抽取 9 张代表性高清关键帧拼合为 3x3 Contact Sheet 图谱，同时抓取连续 10 帧字幕过渡段，
    利用 Vision OCR + 像素连通域提取：
    - 字幕字号、垂直位置、主色 HEX、高亮色 HEX、描边颜色与像素宽度、底框存在性
    - 字幕切入动效（hard_cut / pop_spring / karaoke_highlight / fade_slide）
    - 首帧/开头 OCR 标题文本与分布版式特征
    """
    ffmpeg = find_bin("ffmpeg")
    dur = meta["duration_sec"]
    fps = meta["fps"]

    # 9个关键采样时刻：前3秒4帧 + 全片5帧
    sample_times = [
        min(0.04, max(0.0, dur * 0.01)),
        min(0.65, max(0.0, dur * 0.08)),
        min(1.55, max(0.0, dur * 0.16)),
        min(2.65, max(0.0, dur * 0.25)),
        dur * 0.18,
        dur * 0.36,
        dur * 0.54,
        dur * 0.74,
        max(0.0, dur * 0.90),
    ]
    sample_labels = [
        "01 首帧/封面", "02 0.65s", "03 1.55s",
        "04 2.65s", "05 18%处", "06 36%处",
        "07 54%处", "08 74%处", "09 90%处",
    ]

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        frame_paths: List[Path] = []
        for idx, t in enumerate(sample_times):
            out_p = td_path / f"kf_{idx:02d}.jpg"
            run_cmd([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{t:.3f}", "-i", str(video_path),
                "-frames:v", "1", "-q:v", "2", str(out_p),
            ], check=False)
            if out_p.exists():
                frame_paths.append(out_p)

        # 同时在 35% 进度附近抓取连续 14 帧做字幕切换动效检测
        anim_dir = td_path / "anim"
        anim_dir.mkdir(exist_ok=True)
        t_anim = max(1.0, dur * 0.35)
        run_cmd([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{t_anim:.3f}", "-i", str(video_path),
            "-frames:v", "14", "-q:v", "3", str(anim_dir / "f_%02d.jpg"),
        ], check=False)
        anim_frames = sorted(anim_dir.glob("f_*.jpg"))

        vis_raw = inspect_images(frame_paths)
        ocr_map: Dict[str, List[Dict[str, Any]]] = {}
        for k, v in vis_raw.items():
            texts = v.get("texts") or []
            ocr_map[str(k)] = texts
            ocr_map[str(Path(k).resolve())] = texts

        # 可选构建 3x3 Contact Sheet
        if frame_paths and contact_out is not None:
            first_im = Image.open(frame_paths[0])
            fw, fh = first_im.size
            is_portrait = fh > fw
            tile_w = 540 if is_portrait else 640
            tile_h = int(round(tile_w * fh / max(1, fw)))
            cols, rows = 3, 3
            header_h = 64
            canvas = Image.new("RGB", (tile_w * cols, tile_h * rows + header_h), (18, 22, 30))
            draw = ImageDraw.Draw(canvas)
            fp_comm = find_commercial_font(verified_only=True)
            fnt = get_cached_font(fp_comm, 22) or ImageFont.load_default()
            fnt_sm = get_cached_font(fp_comm, 18) or ImageFont.load_default()

            title_text = f"{video_id} | {video_path.stem[:36]} | {meta['width']}x{meta['height']} @ {meta['fps']}fps | {dur:.1f}s"
            draw.text((16, 18), title_text, fill=(248, 250, 252), font=fnt)

            for i, fp in enumerate(frame_paths[:9]):
                r, c = divmod(i, 3)
                im = Image.open(fp).convert("RGB").resize((tile_w, tile_h), Image.Resampling.LANCZOS)
                x0, y0 = c * tile_w, header_h + r * tile_h
                canvas.paste(im, (x0, y0))
                draw.rectangle([x0 + 8, y0 + 8, x0 + 165, y0 + 36], fill=(15, 23, 42))
                draw.text((x0 + 14, y0 + 11), f"{sample_labels[i]} ({sample_times[i]:.1f}s)", fill=(255, 213, 79), font=fnt_sm)

            contact_out.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(contact_out, quality=90)

        # 解析 OCR 结果统计字幕和标题版式
        sub_boxes: List[Tuple[Path, Dict[str, Any]]] = []
        cover_boxes: List[Dict[str, Any]] = []
        for idx, fp in enumerate(frame_paths):
            boxes = ocr_map.get(str(fp.resolve()), [])
            if idx == 0:
                cover_boxes = boxes
            # 通常主字幕位于画面下半部分 Y ∈ [0.58, 0.94]（去掉极边缘水印）
            for b in boxes:
                cy = b["y"] + b["h"] * 0.5
                if 0.58 <= cy <= 0.94 and len(b["text"].strip()) >= 2:
                    sub_boxes.append((fp, b))

        # 采样字幕像素特征（颜色、描边、底框）
        y_centers: List[float] = []
        h_ratios: List[float] = []
        fg_colors: List[np.ndarray] = []
        stroke_colors: List[np.ndarray] = []
        stroke_ratios: List[float] = []
        bg_box_votes = 0

        img_cache: Dict[str, np.ndarray] = {}
        for fp, b in sub_boxes[:12]:
            key = str(fp)
            if key not in img_cache:
                img_cache[key] = np.array(Image.open(fp).convert("RGB"), dtype=np.float32)
            im_arr = img_cache[key]
            ih, iw, _ = im_arr.shape
            x1 = max(0, int(b["x"] * iw) - 4)
            y1 = max(0, int(b["y"] * ih) - 4)
            x2 = min(iw, int((b["x"] + b["w"]) * iw) + 4)
            y2 = min(ih, int((b["y"] + b["h"]) * ih) + 4)
            if x2 - x1 < 8 or y2 - y1 < 6:
                continue
            y_centers.append(b["y"] + b["h"] * 0.5)
            h_ratios.append(b["h"])

            patch = im_arr[y1:y2, x1:x2]
            luma_p = 0.2126 * patch[:, :, 0] + 0.7152 * patch[:, :, 1] + 0.0722 * patch[:, :, 2]
            p_hi = np.percentile(luma_p, 85)
            p_lo = np.percentile(luma_p, 15)
            fg_mask = luma_p >= max(165.0, p_hi)
            if np.sum(fg_mask) >= 5:
                fg_colors.append(np.median(patch[fg_mask], axis=0))

            # 检测紧邻前景外的描边像素（1px-2px 扩张环）
            dilated = np.zeros_like(fg_mask)
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    if dx == 0 and dy == 0:
                        continue
                    shifted = np.roll(np.roll(fg_mask, dy, axis=0), dx, axis=1)
                    dilated |= shifted
            ring_mask = dilated & (~fg_mask)
            if np.sum(ring_mask) >= 5:
                ring_luma = luma_p[ring_mask]
                dark_ring = float(np.mean(ring_luma < 75.0))
                stroke_ratios.append(dark_ring)
                if dark_ring > 0.35:
                    stroke_colors.append(np.median(patch[ring_mask], axis=0))

            # 检查四周边框区域是否暗色一致（半透明底框）
            border_luma = np.concatenate([luma_p[:2, :].ravel(), luma_p[-2:, :].ravel()])
            if float(np.mean(border_luma)) < 65.0 and float(np.std(border_luma)) < 25.0:
                bg_box_votes += 1

        # 从连续 14 帧评估字幕切换动效
        sub_anim_mode = "hard_cut"
        if len(anim_frames) >= 6:
            strip_diffs: List[float] = []
            prev_strip: Optional[np.ndarray] = None
            for af in anim_frames:
                im_a = np.array(Image.open(af).convert("RGB"), dtype=np.float32) / 255.0
                ah = im_a.shape[0]
                strip = im_a[int(ah * 0.65) : int(ah * 0.90), :]
                if prev_strip is not None:
                    strip_diffs.append(float(np.mean(np.abs(strip - prev_strip))))
                prev_strip = strip
            if strip_diffs:
                sd_arr = np.array(strip_diffs)
                max_sd = float(np.max(sd_arr))
                # 统计连续出现变化的帧数来判别动效类型
                active_run = int(np.sum(sd_arr > max(0.015, max_sd * 0.35)))
                if active_run >= 4 and max_sd > 0.025:
                    sub_anim_mode = "smooth_pop_or_fade"
                elif 2 <= active_run <= 3 and max_sd > 0.03:
                    sub_anim_mode = "fast_spring_pop"
                else:
                    sub_anim_mode = "hard_cut"

        # 换算为标准 1080x1920（竖屏）或 1920x1080（横屏）参考参数
        ref_h = 1920.0 if meta["orientation"] == "portrait_9_16" else 1080.0
        med_y_ratio = float(np.median(y_centers)) if y_centers else 0.74
        med_h_ratio = float(np.median(h_ratios)) if h_ratios else 0.032
        est_font_px = int(round(med_h_ratio * ref_h * 0.92))
        est_font_px = max(34, min(92, est_font_px))

        # 分类主色（白色 vs 黄/橙 vs 彩色高亮）
        primary_hex = "#FFFFFF"
        keyword_hex = "#FFD54F"
        if fg_colors:
            # 寻找饱和度低的偏白/灰像素作为主色，饱和度高作为高亮色
            low_sat_cols = [c for c in fg_colors if (np.max(c) - np.min(c)) < 45]
            hi_sat_cols = [c for c in fg_colors if (np.max(c) - np.min(c)) >= 45]
            if low_sat_cols:
                primary_hex = rgb_to_hex(tuple(np.median(low_sat_cols, axis=0)))
            else:
                primary_hex = rgb_to_hex(tuple(np.median(fg_colors, axis=0)))
            if hi_sat_cols:
                keyword_hex = rgb_to_hex(tuple(np.median(hi_sat_cols, axis=0)))

        has_stroke = bool(stroke_ratios and np.mean(stroke_ratios) > 0.30)
        stroke_hex = rgb_to_hex(tuple(np.median(stroke_colors, axis=0))) if stroke_colors else "#111111"
        has_bg_box = bg_box_votes >= max(2, len(sub_boxes) // 3)

        return {
            "subtitle_detected_count": len(sub_boxes),
            "y_center_ratio": round(med_y_ratio, 3),
            "safe_y_px": int(round(med_y_ratio * ref_h)),
            "font_height_ratio": round(med_h_ratio, 4),
            "estimated_font_size_px": est_font_px,
            "primary_color_hex": primary_hex,
            "keyword_color_hex": keyword_hex,
            "has_stroke": has_stroke,
            "stroke_color_hex": stroke_hex,
            "estimated_stroke_px": 4 if has_stroke else 1,
            "has_bg_box": has_bg_box,
            "animation_mode": sub_anim_mode,
            "cover_ocr_texts": [
                {"text": b["text"], "y_ratio": round(b["y"], 3), "h_ratio": round(b["h"], 3)}
                for b in cover_boxes[:10]
            ],
            "sample_subtitle_texts": [b["text"] for _, b in sub_boxes[:6]],
            "contact_sheet": str(contact_out) if contact_out else None,
        }


def analyze_single_video(args_tuple: Tuple[int, Path, Path, bool]) -> Dict[str, Any]:
    idx, video_path, out_dir, save_contacts = args_tuple
    style_id = f"style_{idx:02d}"
    t0 = time.time()
    meta = probe_video_meta(video_path)
    vis_stats = analyze_frames_stream(video_path, meta)
    lut_path = out_dir / "luts" / f"{style_id}.cube"
    lut_info = generate_reverse_cube_lut(vis_stats["color_stats"], lut_path, title=f"Style {idx:02d}")
    audio_stats = analyze_audio_and_alignment(video_path, meta, vis_stats["shot_stats"])
    contact_path = (out_dir / "keyframes" / f"{style_id}_contact.jpg") if save_contacts else None
    sub_cover_stats = extract_frames_and_analyze_subtitles(video_path, meta, contact_path, style_id)

    elapsed = round(time.time() - t0, 2)
    print(
        f"[{style_id}] {video_path.name[:32]:32s} | {meta['duration_sec']:5.1f}s | "
        f"{vis_stats['shot_stats']['total_shots']:3d}镜(med={vis_stats['shot_stats']['median_sec']:.2f}s) | "
        f"BPM={audio_stats.get('estimated_bpm', 0):5.1f} | "
        f"对齐={audio_stats.get('cut_event_align_3f_pct', 0):4.1f}% | {elapsed:.1f}s"
    )
    return {
        "style_id": style_id,
        "index": idx,
        "filename": video_path.name,
        "video_path": str(video_path),
        "meta": meta,
        "shot_distribution": vis_stats["shot_stats"],
        "color_and_lut": {**vis_stats["color_stats"], **lut_info},
        "audio_and_rhythm": audio_stats,
        "subtitle_and_layout": sub_cover_stats,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="批量逐帧分析参考包装视频并构建原始风格谱数据")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--save-contact-sheets", action="store_true", help="可选：保存 3x3 关键帧采样图谱至 keyframes/")
    args = parser.parse_args()

    in_dir = Path(args.input_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "luts").mkdir(parents=True, exist_ok=True)
    if args.save_contact_sheets:
        (out_dir / "keyframes").mkdir(parents=True, exist_ok=True)

    videos = sorted(in_dir.glob("*.mp4")) if in_dir.exists() else []
    if not videos:
        raise FileNotFoundError(f"未在 {in_dir} 找到 mp4 参考样本，请通过 --input-dir 指定目录")

    print(f"[style-analyzer] 共发现 {len(videos)} 条参考视频，开始并行逐帧分析...")

    tasks = [(i + 1, vp, out_dir, bool(args.save_contact_sheets)) for i, vp in enumerate(videos)]
    results: List[Dict[str, Any]] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for res in ex.map(analyze_single_video, tasks):
            results.append(res)

    results.sort(key=lambda x: x["index"])
    raw_lib = {
        "version": "1.0.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": str(in_dir),
        "total_videos": len(results),
        "styles": results,
    }
    raw_json_path = out_dir / "style_spectrum_raw.json"
    write_json(raw_json_path, raw_lib)
    print(f"[style-analyzer] 完成全量逐帧分析 -> {raw_json_path}")


if __name__ == "__main__":
    main()
