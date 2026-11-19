#!/usr/bin/env python3
"""
[入口 2：带素材进来] 深度素材盘点、分镜日志 (00_shot_log) 与多模态视觉索引融合脚本
功能：
1. 扫描输入目录的视音频素材，解析 side_data/rotation 真实展示尺寸、HDR/10-bit、VFR 状态。
2. 2D FFT 相位相关二阶位移加速度稳度 + 拉普拉斯锐度，锁定精华入点 best_in_sec（免除管道死锁）。
3. 抽取精华帧批量调用 vision_inspector（Apple Vision / 跨平台降级），并支持融合多模态视觉索引 00_visual_index.json。
4. 声画分离双评级 (audio_grade × video_grade)、禁忌词标注、生成等比接触表 00_contact_sheet.jpg。
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    MEDIA_EXTS,
    ffprobe_media,
    find_bin,
    find_commercial_font,
    maybe_reexec_in_venv,
    parse_fps,
    parse_video_stream_geometry,
    read_json,
    run_cmd,
    write_json,
)

maybe_reexec_in_venv()

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from vision_inspector import compute_laplacian_sharpness, inspect_images

TABOO_KEYWORD_MAP: Dict[str, Tuple[str, ...]] = {
    "raw_meat": ("生肉", "血水", "宰杀", "内脏", "冻肉", "解冻", "raw_meat", "rawmeat", "blood"),
    "kitchen_trash": ("垃圾", "泔水", "厨余", "脏乱", "油污", "苍蝇", "kitchen_trash", "trash", "dirty"),
    "construction_trash": ("废料", "拆迁", "瓦砾", "工地垃圾", "施工废料", "construction_trash", "debris"),
    "crew_leak": ("穿帮", "场记", "打板", "摄影师", "工作人员", "三脚架", "反光板", "补光灯", "crew_leak", "crew"),
}


def estimate_phase_shift(prev_gray: np.ndarray, cur_gray: np.ndarray) -> Tuple[float, float, bool]:
    """用 2D FFT 相位相关估算两帧全局像素位移 (dx, dy)；纹理缺失或硬切时跳过。"""
    if float(np.std(prev_gray)) < 2.5 or float(np.std(cur_gray)) < 2.5:
        return 0.0, 0.0, True
    mad = float(np.mean(np.abs(cur_gray - prev_gray))) / 255.0
    if mad > 0.28:
        return 0.0, 0.0, True
    h, w = prev_gray.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    a = (prev_gray - float(np.mean(prev_gray))) * win
    b = (cur_gray - float(np.mean(cur_gray))) * win
    fa = np.fft.fft2(a)
    fb = np.fft.fft2(b)
    r = fa * np.conj(fb)
    r /= np.maximum(np.abs(r), 1e-8)
    cc = np.fft.fftshift(np.abs(np.fft.ifft2(r)))
    py, px = np.unravel_index(int(np.argmax(cc)), cc.shape)
    sub_y, sub_x = 0.0, 0.0
    if 0 < py < h - 1:
        denom_y = float(cc[py - 1, px] - 2.0 * cc[py, px] + cc[py + 1, px])
        if abs(denom_y) > 1e-8:
            sub_y = max(-0.5, min(0.5, 0.5 * float(cc[py - 1, px] - cc[py + 1, px]) / denom_y))
    if 0 < px < w - 1:
        denom_x = float(cc[py, px - 1] - 2.0 * cc[py, px] + cc[py, px + 1])
        if abs(denom_x) > 1e-8:
            sub_x = max(-0.5, min(0.5, 0.5 * float(cc[py, px - 1] - cc[py, px + 1]) / denom_x))
    return float(px + sub_x - (w // 2)), float(py + sub_y - (h // 2)), False


def analyze_video_quality(path: Path, duration: float, display_w: int, display_h: int) -> Dict[str, Any]:
    """检测亮度曝光、静音比例、拉普拉斯锐度与 FFT 二阶加速度稳度（基于真实旋转后展示宽高）。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg or duration <= 0.1:
        return {
            "exposure": "unknown",
            "avg_luma": None,
            "silence_ratio": 0.0,
            "sharpness_score": 0.0,
            "stability_score": 1.0,
            "motion_speed_px": 0.0,
            "accel_jitter_px": 0.0,
            "best_in_sec": 0.0,
            "issues": [],
        }

    issues: List[str] = []
    # 1. 亮度采样
    res = run_cmd([
        ffmpeg, "-v", "info", "-t", str(min(duration, 15.0)), "-i", str(path),
        "-vf", "select='not(mod(n,10))',format=yuv420p,signalstats,metadata=print",
        "-an", "-f", "null", "-",
    ], check=False)
    yavg_vals = [float(m) for m in re.findall(r"lavfi\.signalstats\.YAVG=([0-9.]+)", res.stderr or "")]
    avg_luma = round(sum(yavg_vals) / len(yavg_vals), 1) if yavg_vals else 128.0
    if avg_luma < 55:
        exposure = "underexposed"
        issues.append(f"画面偏暗 (YAVG={avg_luma})，建议第7步适当提亮曝光")
    elif avg_luma > 205:
        exposure = "overexposed"
        issues.append(f"画面过曝 (YAVG={avg_luma})，建议第7步压低高光")
    else:
        exposure = "normal"

    # 2. 静音检测
    res_sil = run_cmd([
        ffmpeg, "-v", "info", "-i", str(path),
        "-vn", "-af", "silencedetect=noise=-42dB:d=0.6",
        "-f", "null", "-",
    ], check=False)
    sil_durs = [float(m) for m in re.findall(r"silence_duration:\s*([0-9.]+)", res_sil.stderr or "")]
    sil_ratio = round(min(1.0, sum(sil_durs) / max(duration, 0.1)), 2)
    if sil_ratio > 0.6:
        issues.append(f"有效人声较少(静音占比 {int(sil_ratio*100)}%)，适合作为 B-roll 或补拍/配音")

    # 3. 真实展示尺寸等比缩放下采样测锐度与二阶加速度 (stderr=DEVNULL 杜绝 64KB 管道死锁)
    w_s = 240
    h_s = max(2, int(round(display_h * w_s / max(1, display_w))) // 2 * 2) if display_w > 0 and display_h > 0 else 426
    frame_bytes = w_s * h_s * 3
    sample_fps = 4.0
    max_scan = min(duration, 30.0)
    proc = subprocess.Popen(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-t", f"{max_scan:.2f}", "-i", str(path),
            "-vf", f"fps={sample_fps},scale={w_s}:{h_s}",
            "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    sharps: List[float] = []
    shifts: List[Tuple[float, float, bool]] = []
    prev_gray: Optional[np.ndarray] = None
    while True:
        raw = proc.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            break
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((h_s, w_s, 3))
        gray = (
            0.299 * arr[:, :, 0].astype(np.float32)
            + 0.587 * arr[:, :, 1].astype(np.float32)
            + 0.114 * arr[:, :, 2].astype(np.float32)
        )
        # 复用已计算的 gray，避免 compute_laplacian_sharpness 重复算灰度
        sharps.append(compute_laplacian_sharpness(gray))
        if prev_gray is not None:
            shifts.append(estimate_phase_shift(prev_gray, gray))
        else:
            shifts.append((0.0, 0.0, True))
        prev_gray = gray
    proc.wait()

    speeds: List[float] = [0.0] * len(sharps)
    accels: List[float] = [0.0] * len(sharps)
    valid_accels: List[float] = []
    for i in range(1, len(shifts)):
        dx1, dy1, skip1 = shifts[i]
        dx0, dy0, skip0 = shifts[i - 1]
        speeds[i] = math.hypot(dx1, dy1)
        if not skip1 and not skip0 and i >= 2:
            acc = math.hypot(dx1 - dx0, dy1 - dy0)
            accels[i] = acc
            valid_accels.append(acc)

    sharpness_score = round(float(np.mean(sharps)), 1) if sharps else 0.0
    med_speed = round(float(np.median([s for i, s in enumerate(speeds) if i > 0])) if len(speeds) > 1 else 0.0, 2)
    med_accel = round(float(np.median(valid_accels)) if valid_accels else 0.0, 2)
    stability_score = round(max(0.0, min(1.0, 1.0 - (max(0.0, med_accel - 0.6) / 10.0))), 3)

    best_in_sec = 0.0
    if len(sharps) >= 6 and duration > 2.2:
        s_np = np.array(sharps, dtype=np.float32)
        a_np = np.array(accels, dtype=np.float32)
        s_norm = (s_np - np.min(s_np)) / max(1e-5, float(np.max(s_np) - np.min(s_np)))
        a_norm = a_np / max(2.0, float(np.max(a_np)))
        skip_idx = min(len(sharps) - 4, max(1, int(round(0.40 * sample_fps))))
        max_idx = max(skip_idx + 1, len(sharps) - 4)
        best_score = -1e9
        best_idx = skip_idx
        for idx in range(skip_idx, max_idx):
            win_sharp = float(np.mean(s_norm[idx : idx + 4]))
            win_acc = float(np.mean(a_norm[idx : idx + 4]))
            sc = win_sharp - 0.75 * win_acc
            if sc > best_score:
                best_score = sc
                best_idx = idx
        best_in_sec = round(best_idx / sample_fps, 2)

    if sharpness_score > 0 and sharpness_score < 22.0:
        issues.append(f"画面整体偏糊 (锐度={sharpness_score})，建议核查对焦或优先选用高分片段")
    if stability_score < 0.55:
        issues.append(f"高频随机手抖明显 (二阶加速度={med_accel}px, 稳定度={stability_score})，已锁定稳妥入点 {best_in_sec}s")

    return {
        "exposure": exposure,
        "avg_luma": avg_luma,
        "silence_ratio": sil_ratio,
        "sharpness_score": sharpness_score,
        "stability_score": stability_score,
        "motion_speed_px": med_speed,
        "accel_jitter_px": med_accel,
        "best_in_sec": best_in_sec,
        "issues": issues,
    }


def classify_clip(
    filename: str,
    a_stream: Optional[Dict[str, Any]],
    quality: Dict[str, Any],
    duration: float,
    v_idx_entry: Optional[Dict[str, Any]] = None,
) -> str:
    low = filename.lower()
    if v_idx_entry and v_idx_entry.get("role") in ("a_roll_candidate", "b_roll_candidate"):
        return str(v_idx_entry["role"])
    if "broll" in low or "b_roll" in low:
        return "b_roll_candidate"
    if "aroll" in low or "a_roll" in low or "talk" in low:
        return "a_roll_candidate"
    if a_stream and quality["silence_ratio"] < 0.55 and duration >= 2.0:
        return "a_roll_candidate"
    return "b_roll_candidate"


def _infer_semantic_tags(
    filename: str,
    vision_res: Dict[str, Any],
    avg_luma: Optional[float],
    v_idx_entry: Optional[Dict[str, Any]] = None,
) -> List[str]:
    tags: List[str] = []
    if v_idx_entry:
        for tg in v_idx_entry.get("semantic_tags", []) or []:
            if str(tg).strip():
                tags.append(str(tg).strip())
        if v_idx_entry.get("shot_type"):
            tags.append(str(v_idx_entry["shot_type"]).strip())
        if v_idx_entry.get("action"):
            tags.append(str(v_idx_entry["action"]).strip())

    stem = Path(filename).stem
    for tok in re.findall(r"[\u4e00-\u9fff]{2,6}|[a-zA-Z]{3,12}", stem):
        low = tok.lower()
        if low not in ("norm", "aroll", "broll", "roll", "clip", "vid", "img", "mov", "mp4"):
            tags.append(tok)
    if vision_res.get("faces"):
        tags.append("真人出镜")
    else:
        tags.append("空镜特写")
    for t in vision_res.get("texts", []):
        txt = str(t.get("text", "")).strip()
        if len(txt) >= 2:
            tags.append(txt[:10])
    if avg_luma is not None:
        if avg_luma < 85:
            tags.append("暗调质感")
        elif avg_luma > 165:
            tags.append("明亮环境")
    seen = set()
    uniq: List[str] = []
    for tg in tags:
        if tg and tg not in seen:
            seen.add(tg)
            uniq.append(tg)
    return uniq[:16]


def _infer_taboo_tags(
    filename: str,
    ocr_texts: List[str],
    v_idx_entry: Optional[Dict[str, Any]] = None,
) -> List[str]:
    tags: List[str] = []
    if v_idx_entry:
        tags.extend(str(x).strip() for x in (v_idx_entry.get("taboo_tags") or []) if str(x).strip())
    blob = (filename + " " + " ".join(ocr_texts)).lower()
    for tag, kws in TABOO_KEYWORD_MAP.items():
        if any(k.lower() in blob for k in kws) and tag not in tags:
            tags.append(tag)
    return tags


def _has_foreground_teleprompter(faces: List[Dict[str, Any]], text_boxes: List[Dict[str, Any]]) -> bool:
    """
    区分“手持大面积提词稿/A4纸”与“门店背景远景招牌/菜单”：
    仅当画面中心/中下部 (y in [0.25, 0.88], x in [0.15, 0.85]) 存在密集多段文本（>=4 行或总字符 >=28 且覆盖宽度较宽）时才判为提词稿。
    """
    if not faces or not text_boxes:
        return False
    fg_boxes = [
        b for b in text_boxes
        if 0.25 <= float(b.get("y", 0.0)) <= 0.88
        and 0.12 <= float(b.get("x", 0.0)) <= 0.85
        and len(str(b.get("text", "")).strip()) >= 3
    ]
    fg_chars = sum(len(str(b.get("text", "")).strip()) for b in fg_boxes)
    return (len(fg_boxes) >= 4 and fg_chars >= 24) or fg_chars >= 36


def _assess_av_grades(
    filename: str,
    has_audio: bool,
    quality: Dict[str, Any],
    faces: List[Dict[str, Any]],
    text_boxes: List[Dict[str, Any]],
    role: str,
    v_idx_entry: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, List[str]]:
    """A2 声画分离双评级 (audio_grade × video_grade: usable | audio_only | ng)。"""
    reasons: List[str] = []
    sil = float(quality.get("silence_ratio", 0.0))
    sharp = float(quality.get("sharpness_score", 0.0))
    luma = float(quality.get("avg_luma") or 128.0)

    if not has_audio or sil >= 0.85:
        audio_grade = "silent"
    elif sil >= 0.72:
        audio_grade = "noisy"
    else:
        audio_grade = "usable"

    if v_idx_entry and v_idx_entry.get("video_grade") in ("usable", "audio_only", "ng"):
        vg = str(v_idx_entry["video_grade"])
        if v_idx_entry.get("grade_reason"):
            reasons.append(str(v_idx_entry["grade_reason"]))
        return audio_grade, vg, reasons

    fname_low = filename.lower()
    face_severed = any(
        float((f.get("bbox") or {}).get("y", 0.2)) < 0.01
        or float((f.get("bbox") or {}).get("y", 0.2)) + float((f.get("bbox") or {}).get("h", 0.2)) > 0.99
        for f in faces
    )

    if sharp < 8.0 and audio_grade != "usable" and luma < 12.0:
        video_grade = "ng"
        reasons.append("画面黑废且无可用人声 (ng)")
    elif "audio_only" in fname_low:
        video_grade = "audio_only"
        reasons.append("文件名标记仅取人声遮脸 (audio_only)")
    elif audio_grade == "usable" and role == "a_roll_candidate":
        bad = []
        if 0 < sharp < 16.0:
            bad.append(f"极糊(sharp={sharp:.1f})")
        if face_severed:
            bad.append("人物切顶/构图严重残缺")
        if _has_foreground_teleprompter(faces, text_boxes):
            bad.append("镜头前大面积提词稿/A4纸")
        if (faces and luma < 32.0) or luma > 242.0:
            bad.append(f"曝光严重失常(YAVG={luma:.1f})")
        if bad:
            video_grade = "audio_only"
            reasons.append("声好画废降级保声遮脸: " + "、".join(bad))
        else:
            video_grade = "usable"
    else:
        video_grade = "usable"

    return audio_grade, video_grade, reasons


def recommend_angle(
    clips: List[Dict[str, Any]],
    track: str,
    raw_intel: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """结合素材盘点与一手客户原材 00_raw_intel.json，反推最稳妥的选题结构建议。"""
    has_aroll = any(c["suggested_role"] == "a_roll_candidate" for c in clips)
    total_dur = round(sum(c["duration_sec"] for c in clips), 2)
    broll_count = sum(1 for c in clips if c["suggested_role"] == "b_roll_candidate")

    pain_kws = (raw_intel or {}).get("pain_keywords", [])
    questions = (raw_intel or {}).get("customer_questions", [])
    proof_cnt = len((raw_intel or {}).get("proof_assets", []))

    if track == "store":
        if has_aroll:
            formula = "水桶流视频公式：客户原话痛点钩子 + 产品/物证细节展示 + 老板人格化表达(原则/经历) + 价值说明 + 转化指令"
            hook = f"直接回应真实客户顾虑「{questions[0][:22]}」" if questions else "颠覆切入 / 痛点切入（例如：做这行这么多年，只坚持一条底线…）"
            a_level = "A2 人设信任型 / A3 垂直说服型"
        else:
            formula = "漏斗流视频公式：同城/行业高关注话题 + 细节B-roll视觉展示 + 原声/TTS配音专业植入 + 人设结尾"
            hook = "信息密度开篇 + 好奇切入（用高质感特写画面配首句反常识事实）"
            a_level = "A1 泛流量型"
    else:
        if has_aroll:
            formula = "漏斗流视频公式：话题钩子(社会/认知/内幕) + 观点+原因+案例 + 人设露出 + 互动结尾"
            hook = f"从评论区高频追问「{questions[0][:22]}」切入颠覆认知" if questions else "对立面开篇 / 不字开头 / 认知颠覆切入"
            a_level = "A1 泛流量型 / A2 人设信任型"
        else:
            formula = "纯视觉+代码补镜流：信息缺口开篇 + 配音节奏 + B-roll与数据图表混剪 + 金句结尾"
            hook = "数字开篇 / 信息缺口开篇"
            a_level = "A1 泛流量型"

    missing_alert = []
    if broll_count < 2:
        missing_alert.append("可用 B-roll 画面较少，建议 Step 4 自动使用风格化代码叙事补镜补齐前 3 秒多切点")
    if not has_aroll:
        missing_alert.append("缺少清晰同期声口播素材，Step 2/3 将自动引导使用文案配音或补录原声")
    if proof_cnt == 0 and track == "store":
        missing_alert.append("未关联门店物证目录 (--proof-dir)，建议补充原料/账单/实测对比照提升信任度")

    return {
        "track": track,
        "total_raw_duration_sec": total_dur,
        "has_usable_aroll": has_aroll,
        "recommended_5a_level": a_level,
        "recommended_formula": formula,
        "recommended_hook": hook,
        "raw_pain_keywords_used": pain_kws[:6],
        "proof_assets_count": proof_cnt,
        "gaps_and_remedy": missing_alert,
    }


def _build_contact_sheet(
    clips: List[Dict[str, Any]],
    frame_paths: Dict[int, Path],
    out_jpg: Path,
) -> Optional[Path]:
    """生成 4 列等比保持宽高比的素材接触表 00_contact_sheet.jpg，方便人/VLM 一眼审片。"""
    if not clips or not frame_paths:
        return None
    cols = min(4, max(1, len(clips)))
    rows = math.ceil(len(clips) / cols)
    tw, th = 270, 480
    hdr_h = 48
    cell_h = th + hdr_h
    pad = 12
    canvas = Image.new("RGB", (cols * (tw + pad) + pad, rows * (cell_h + pad) + pad), (18, 20, 26))
    draw = ImageDraw.Draw(canvas)
    f_path = find_commercial_font()
    try:
        fnt = ImageFont.truetype(f_path, 14) if f_path else ImageFont.load_default()
    except Exception:
        fnt = ImageFont.load_default()

    for idx, c in enumerate(clips):
        r, col = divmod(idx, cols)
        x0 = pad + col * (tw + pad)
        y0 = pad + r * (cell_h + pad)
        draw.rectangle([x0, y0, x0 + tw, y0 + cell_h], fill=(28, 32, 42), outline=(65, 74, 92), width=1)
        fp = frame_paths.get(idx)
        if fp and fp.is_file():
            try:
                with Image.open(fp) as im:
                    im_rgb = ImageOps.exif_transpose(im).convert("RGB")
                    thumb = ImageOps.pad(im_rgb, (tw, th), method=Image.Resampling.LANCZOS, color=(14, 16, 22))
                canvas.paste(thumb, (x0, y0 + hdr_h))
            except Exception:
                pass
        vg = c.get("video_grade", "usable")
        tab = ",".join(c.get("taboo_tags") or [])
        line1 = f"#{idx+1} {c['filename'][:15]} ({c['duration_sec']:.1f}s)"
        line2 = f"S:{c['sharpness_score']:.0f} St:{c['stability_score']:.2f} {vg}" + (f" [{tab}]" if tab else "")
        badge_col = (250, 204, 21) if vg == "audio_only" else ((248, 113, 113) if (vg == "ng" or tab) else (226, 232, 240))
        draw.text((x0 + 6, y0 + 4), line1, font=fnt, fill=(248, 250, 252))
        draw.text((x0 + 6, y0 + 24), line2, font=fnt, fill=badge_col)

    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_jpg, quality=88)
    return out_jpg


def _load_visual_index_map(visual_index_path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if not visual_index_path or not visual_index_path.exists():
        return {}
    raw = read_json(visual_index_path)
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, dict):
        items = raw.get("clips") or raw.get("shots") or raw
        if isinstance(items, dict):
            for k, v in items.items():
                if isinstance(v, dict):
                    out[str(k)] = v
        elif isinstance(items, list):
            for v in items:
                if isinstance(v, dict) and v.get("filename"):
                    out[str(v["filename"])] = v
    elif isinstance(raw, list):
        for v in raw:
            if isinstance(v, dict) and v.get("filename"):
                out[str(v["filename"])] = v
    return out


def build_shot_log_dict(inv: Dict[str, Any]) -> Dict[str, Any]:
    """统一构造 00_shot_log.json 数据结构（消除 inventory_media 与 run_pipeline 重复代码）。"""
    return {
        "step": "00_shot_log",
        "input_dir": inv["input_dir"],
        "shots": [
            {
                "filename": c["filename"],
                "path": c["path"],
                "role": c["suggested_role"],
                "duration_sec": c["duration_sec"],
                "width": c["width"],
                "height": c["height"],
                "rotation_deg": c.get("rotation_deg", 0),
                "orientation": c.get("orientation", "portrait"),
                "best_in_sec": c["best_in_sec"],
                "sharpness_score": c["sharpness_score"],
                "stability_score": c["stability_score"],
                "motion_speed_px": c.get("motion_speed_px", 0.0),
                "accel_jitter_px": c.get("accel_jitter_px", 0.0),
                "audio_grade": c.get("audio_grade", "usable"),
                "video_grade": c.get("video_grade", "usable"),
                "taboo_tags": c.get("taboo_tags", []),
                "color_meta": c["color_meta"],
                "faces": c["faces"],
                "eye_anchor": c["eye_anchor"],
                "saliency_bbox": c["saliency_bbox"],
                "semantic_tags": c["semantic_tags"],
                "manual_tags": c["manual_tags"],
                "scene_group": c["scene_group"],
            }
            for c in inv["clips"]
        ],
    }


def inventory_directory(
    input_dir: Path,
    track: str,
    raw_intel: Optional[Dict[str, Any]] = None,
    contact_sheet_path: Optional[Path] = None,
    visual_index_path: Optional[Path] = None,
) -> Dict[str, Any]:
    files = sorted([
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS and "benchmark" not in p.name.lower()
    ])
    v_index = _load_visual_index_map(visual_index_path)
    clips: List[Dict[str, Any]] = []
    ffmpeg = find_bin("ffmpeg")

    with tempfile.TemporaryDirectory(prefix="svs_inv_vis_") as td:
        td_path = Path(td)
        rep_frames: List[Path] = []
        frame_to_idx: Dict[str, int] = {}
        idx_to_frame: Dict[int, Path] = {}
        idx_to_boxes: Dict[int, List[Dict[str, Any]]] = {}

        for f in files:
            meta = ffprobe_media(f)
            streams = meta.get("streams", [])
            v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
            a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
            if not v_stream:
                continue

            dur = float(meta.get("format", {}).get("duration") or v_stream.get("duration") or 0.0)
            geom = parse_video_stream_geometry(v_stream)
            dw, dh = geom["display_width"], geom["display_height"]
            r_fps = parse_fps(v_stream.get("r_frame_rate", "0/1"))
            avg_fps = parse_fps(v_stream.get("avg_frame_rate", "0/1"))
            is_vfr = abs(r_fps - avg_fps) > 0.5 or avg_fps == 0.0

            color_transfer = str(v_stream.get("color_transfer") or "unknown")
            color_primaries = str(v_stream.get("color_primaries") or "unknown")
            color_space = str(v_stream.get("color_space") or "unknown")
            pix_fmt = str(v_stream.get("pix_fmt") or "unknown")
            is_hdr = color_transfer in ("arib-std-b67", "smpte2084") or "bt2020" in color_primaries or "bt2020" in color_space
            is_10bit = any(k in pix_fmt for k in ("10le", "10be", "p010"))

            v_idx_entry = v_index.get(f.name) or v_index.get(f.stem)
            quality = analyze_video_quality(f, dur, dw, dh)
            if v_idx_entry and isinstance(v_idx_entry.get("best_in_sec"), (int, float)):
                quality["best_in_sec"] = round(max(0.0, min(max(0.0, dur - 0.5), float(v_idx_entry["best_in_sec"]))), 2)

            issues = list(quality["issues"])
            if geom["rotation_deg"] != 0:
                issues.append(f"容器旋转 {geom['rotation_deg']}°，已按展示尺寸 {dw}x{dh} 解算几何")
            if is_hdr:
                issues.append(f"检测到 HDR/广色域素材 ({color_transfer}/{color_primaries}), Step 1 将执行 Hable 色调映射")
            if is_vfr:
                issues.append(f"检测到可变帧率 VFR (r_fps={r_fps:.2f}, avg_fps={avg_fps:.2f})，Step 1 将强制转 30fps CFR")
            if dw > dh:
                issues.append(f"横屏素材 ({dw}x{dh})，Step 1 将裁切适配 1080x1920 并同步映射人脸坐标")
            if dw < 720 or dh < 1080:
                issues.append(f"分辨率偏低 ({dw}x{dh})，建议仅作局部插画或谨慎使用")

            role = classify_clip(f.name, a_stream, quality, dur, v_idx_entry=v_idx_entry)
            tags_meta = meta.get("format", {}).get("tags", {}) or v_stream.get("tags", {}) or {}
            ctime = str(tags_meta.get("creation_time", ""))[:10]
            scene_group = f"shoot_{ctime}" if ctime else "shoot_default"

            clip_idx = len(clips)
            clips.append({
                "filename": f.name,
                "path": str(f.resolve()),
                "duration_sec": round(dur, 2),
                "width": dw,
                "height": dh,
                "coded_width": geom["coded_width"],
                "coded_height": geom["coded_height"],
                "rotation_deg": geom["rotation_deg"],
                "orientation": geom["orientation"],
                "r_fps": round(r_fps, 2),
                "avg_fps": round(avg_fps, 2),
                "is_vfr": is_vfr,
                "has_audio": a_stream is not None,
                "color_meta": {
                    "color_transfer": color_transfer,
                    "color_primaries": color_primaries,
                    "color_space": color_space,
                    "pix_fmt": pix_fmt,
                    "is_hdr": is_hdr,
                    "is_10bit": is_10bit,
                },
                "quality": quality,
                "best_in_sec": quality["best_in_sec"],
                "sharpness_score": quality["sharpness_score"],
                "stability_score": quality["stability_score"],
                "motion_speed_px": quality.get("motion_speed_px", 0.0),
                "accel_jitter_px": quality.get("accel_jitter_px", 0.0),
                "issues": issues,
                "suggested_role": role,
                "usable": dur >= 0.8,
                "audio_grade": "usable" if (a_stream is not None and quality["silence_ratio"] < 0.85) else "silent",
                "video_grade": "usable",
                "taboo_tags": [],
                "scene_group": scene_group,
                "faces": [],
                "eye_anchor": None,
                "saliency_bbox": None,
                "ocr_texts": [],
                "semantic_tags": [],
                "manual_tags": list((v_idx_entry or {}).get("manual_tags", [])),
                "visual_indexed": bool(v_idx_entry),
                "_v_idx_entry": v_idx_entry,
            })

            if ffmpeg and dur > 0.1:
                rep_jpg = td_path / f"rep_{clip_idx:03d}.jpg"
                run_cmd([
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{quality['best_in_sec']:.2f}", "-i", str(f),
                    "-frames:v", "1", "-q:v", "3", str(rep_jpg),
                ], check=False)
                if rep_jpg.exists():
                    rep_frames.append(rep_jpg)
                    frame_to_idx[str(rep_jpg)] = clip_idx
                    idx_to_frame[clip_idx] = rep_jpg

        vis_map = inspect_images(rep_frames)
        for fp_str, c_idx in frame_to_idx.items():
            vr = vis_map.get(fp_str, {})
            faces = vr.get("faces", [])
            clips[c_idx]["faces"] = faces
            if faces:
                f0 = faces[0]
                clips[c_idx]["eye_anchor"] = {
                    "cx": round(float(f0.get("eye_cx", 0.5)), 4),
                    "cy": round(float(f0.get("eye_cy", 0.36)), 4),
                    "eye_open_ratio": round(float(f0.get("eye_open_ratio", 0.25)), 4),
                }
            clips[c_idx]["saliency_bbox"] = vr.get("saliency")
            boxes = vr.get("texts", []) or []
            idx_to_boxes[c_idx] = boxes
            ocrs = [str(t.get("text", "")) for t in boxes if t.get("text")]
            clips[c_idx]["ocr_texts"] = ocrs
            ve = clips[c_idx].get("_v_idx_entry")
            clips[c_idx]["semantic_tags"] = _infer_semantic_tags(
                clips[c_idx]["filename"], vr, clips[c_idx]["quality"].get("avg_luma"), v_idx_entry=ve
            )
            clips[c_idx]["taboo_tags"] = _infer_taboo_tags(clips[c_idx]["filename"], ocrs, v_idx_entry=ve)

        for c_idx, c in enumerate(clips):
            ve = c.pop("_v_idx_entry", None)
            ag, vg, grade_reasons = _assess_av_grades(
                c["filename"],
                c["has_audio"],
                c["quality"],
                c["faces"],
                idx_to_boxes.get(c_idx, []),
                c["suggested_role"],
                v_idx_entry=ve,
            )
            c["audio_grade"] = ag
            c["video_grade"] = vg
            c["issues"].extend(grade_reasons)
            if vg == "ng":
                c["usable"] = False

        if contact_sheet_path is not None:
            _build_contact_sheet(clips, idx_to_frame, contact_sheet_path)

    recommendation = recommend_angle(clips, track, raw_intel=raw_intel)
    return {
        "input_dir": str(input_dir.resolve()),
        "clip_count": len(clips),
        "clips": clips,
        "contact_sheet": str(contact_sheet_path.resolve()) if contact_sheet_path and contact_sheet_path.exists() else None,
        "visual_index_used": bool(v_index),
        "recommendation": recommendation,
    }


def render_markdown_summary(data: Dict[str, Any]) -> str:
    rec = data["recommendation"]
    lines = [
        "# 素材盘点与分镜日志反推报告",
        "",
        f"- **盘点素材总数**：{data['clip_count']} 条（累计原始时长 {rec['total_raw_duration_sec']} 秒）",
        f"- **适用轨道**：{'门店轨' if rec['track']=='store' else '博主轨'}",
        f"- **推荐 5A 层级**：{rec['recommended_5a_level']}",
        f"- **反推视频公式**：{rec['recommended_formula']}",
        f"- **建议开篇钩子**：{rec['recommended_hook']}",
        "",
        "## 素材明细与视觉感知表",
        "",
        "| 文件名 | 尺寸/旋转 | 时长 | HDR/VFR | 精华入点 | 锐度/稳度 | 人脸/标签 | 建议用途 | 诊断 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :--- | :--- | :--- |",
    ]
    for c in data["clips"]:
        diag = "；".join(c["issues"]) if c["issues"] else "正常可用"
        hdr_tag = "HDR" if c["color_meta"]["is_hdr"] else "SDR"
        vfr_tag = "VFR" if c["is_vfr"] else "CFR"
        tags_str = ",".join(c["semantic_tags"][:4]) or "-"
        face_str = f"{len(c['faces'])}脸" if c["faces"] else "无脸"
        dim_str = f"{c['width']}x{c['height']}" + (f"({c.get('rotation_deg',0)}°)" if c.get("rotation_deg") else "")
        lines.append(
            f"| `{c['filename']}` | {dim_str} | {c['duration_sec']}s | {hdr_tag}/{vfr_tag} | "
            f"{c['best_in_sec']:.2f}s | {c['sharpness_score']:.0f}/{c['stability_score']:.2f} | "
            f"{face_str}({tags_str}) | "
            f"{'同期声口播(A-roll)' if c['suggested_role']=='a_roll_candidate' else '空镜/特写(B-roll)'} | {diag} |"
        )
    if rec["gaps_and_remedy"]:
        lines += ["", "## 缺口与自动补救策略"] + [f"- {g}" for g in rec["gaps_and_remedy"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="深度素材盘点、分镜日志与多模态视觉索引融合")
    parser.add_argument("--input-dir", required=True, help="原始素材文件夹路径")
    parser.add_argument("--output", required=True, help="输出 00_inventory.json 路径")
    parser.add_argument("--track", choices=["store", "creator"], default="store", help="门店轨或博主轨")
    parser.add_argument("--raw-intel", help="可选：00_raw_intel.json 路径")
    parser.add_argument("--visual-index", help="可选：00_visual_index.json 路径（多模态看图真实语义/禁忌标注）")
    args = parser.parse_args()

    out_json = Path(args.output)
    raw_intel = None
    intel_path = Path(args.raw_intel) if args.raw_intel else (out_json.parent / "00_raw_intel.json")
    if intel_path.exists():
        raw_intel = read_json(intel_path)

    vis_idx_path = Path(args.visual_index) if args.visual_index else (out_json.parent / "00_visual_index.json")
    contact_sheet = out_json.parent / "00_contact_sheet.jpg"
    inv = inventory_directory(
        Path(args.input_dir),
        args.track,
        raw_intel=raw_intel,
        contact_sheet_path=contact_sheet,
        visual_index_path=vis_idx_path if vis_idx_path.exists() else None,
    )
    write_json(out_json, inv)

    shot_log = build_shot_log_dict(inv)
    shot_log_path = out_json.parent / "00_shot_log.json"
    write_json(shot_log_path, shot_log)

    out_md = out_json.with_suffix(".md")
    out_md.write_text(render_markdown_summary(inv), encoding="utf-8")
    print(f"[inventory] 已盘点 {inv['clip_count']} 条素材 -> {out_json} & {shot_log_path}")


if __name__ == "__main__":
    main()
