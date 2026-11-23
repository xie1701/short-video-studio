#!/usr/bin/env python3
"""
复刻 vs 原版：并排盲审片 + 每 5 秒索引网格 + 六项客观指标。

为什么盲
────────
自己看自己复刻的片子会往好里看。所以左右按固定种子随机，只标 `A` / `B`；
哪边是原版写进 `sidebyside_key.csv`——先看完再对列。

它做四件事
──────────
1. `style_XX_sidebyside.mp4`：左右并排，两侧都烧时间码 + 帧号，可选第三栏是放大差分。
2. `style_XX_index_grid.jpg`：每 5 秒一格，一眼看全片差异分布。
3. `style_XX_metrics.json`：六项指标（SSIM / 字幕带边缘密度 / 调色分位 / 切点 / LUFS / 时长）。
4. `sidebyside_key.csv`：A/B 对应关系（含数值）。

用法
────
    python3 dev_tools/make_sidebyside.py --style-id style_21 \
        --orig ~/Downloads/styleframe/style_21.mov \
        --rep  /tmp/blind_review/style_21_replica.mp4 \
        --out  reports/sidebyside

指标阈值（PLAN_v4.3 §1.4）
──────────────────────────
SSIM < 0.90 / 字幕带**带内绝对差** > 5 级 / 调色 p05·p50·p95 差 > 6 级 /
切点错位 > 0.15s / LUFS 差 > 1.5 LU / 时长差 > 0.1s —— 任一项超了记 `flag`。

字幕带为什么不用「相对边缘密度差」判
──────────────────────────────────────
旧口径 = 带内 Sobel 边缘密度之比 |b-a|/max(a,b)，取全片最差的帧。比例的分母可以是近零：
实测该片 5.0/5.5s 带内亮度只有 0.12 级（纯黑），边缘密度 1.9e-4 对 1.6e-4 —— 绝对差只有
0.03 级（肉眼不可见），比例却是 14.7%；4.5s 是 33%，6.0s 是 37%。反过来，真有差字的帧
（3.5s 带内 6.8% 像素差 >5 级、6.3% 差 >20 级）比例只有 1.7%。即旧口径的排序与可见差
不相关，它在给「全黑的带」记差账。所以闸门改成带内**亮度绝对差（0-255 级）**，同一指标
族里跟调色分位差一样用「级」说话；旧比例仍然写进 JSON（`subtitle_band_rel_max`）留档对照。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

THRESH = {
    "ssim_min": 0.90,
    # 字幕带：带内亮度平均绝对差（0-255 级）。5 级 ≈ 屏幕 2% 亮度差 —— 暗部刚可辨的上沿。
    "sub_band_abs_lv": 5.0,
    "sub_band_rel": 0.15,   # 旧闸门（相对边缘密度），只留作对照，不再参与判定
    "grade_levels": 6.0,
    "cut_delta_s": 0.15,
    "lufs_delta": 1.5,
    "dur_delta_s": 0.1,
}

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font() -> Optional[str]:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def run(cmd: List[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败({r.returncode}): {' '.join(cmd[:8])}...\n{r.stderr[-2500:]}")
    return r


def probe(path: Path) -> Dict:
    r = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    j = json.loads(r.stdout)
    st = (j.get("streams") or [{}])[0]
    dur = float((j.get("format") or {}).get("duration") or 0.0)
    fps = 0.0
    for key in ("r_frame_rate", "avg_frame_rate"):
        v = st.get(key) or ""
        if "/" in v:
            a, b = v.split("/")[:2]
            try:
                f = float(a) / float(b or 1)
            except ZeroDivisionError:
                f = 0.0
            if f > 0:
                fps = f
                break
    return {
        "path": str(path),
        "width": int(st.get("width") or 0),
        "height": int(st.get("height") or 0),
        "fps": round(fps, 4),
        "duration": round(dur, 4),
    }


# ── 六项指标 ────────────────────────────────────────────────────────────────

def _box_mean(a: np.ndarray, r: int = 4) -> np.ndarray:
    """可分离滑动平均（积分图实现，不依赖 scipy）。"""
    k = 2 * r + 1
    pad = np.pad(a, ((r, r), (r, r)), mode="edge")
    c = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)), mode="constant")
    H, W = a.shape
    out = np.empty((H, W), dtype=np.float64)
    for y in range(H):
        for x in range(W):
            out[y, x] = (
                c[y + k, x + k] - c[y, x + k] - c[y + k, x] + c[y, x]
            ) / (k * k)
    return out


def ssim_gray(a: np.ndarray, b: np.ndarray) -> float:
    """全局 SSIM（8×8 块 → 均值），a/b 为 float64、0..255。"""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    block = 8
    H, W = a.shape
    H8, W8 = (H // block) * block, (W // block) * block
    if not H8 or not W8:
        return 0.0
    a = a[:H8, :W8].reshape(H8 // block, block, W8 // block, block).mean(axis=(1, 3))
    b = b[:H8, :W8].reshape(H8 // block, block, W8 // block, block).mean(axis=(1, 3))
    mu_a, mu_b = a.mean(), b.mean()
    var_a, var_b = a.var(), b.var()
    cov = ((a - mu_a) * (b - mu_b)).mean()
    return float(((2 * mu_a * mu_b + C1) * (2 * cov + C2)) /
                 ((mu_a ** 2 + mu_b ** 2 + C1) * (var_a + var_b + C2)))


def edge_density(gray: np.ndarray) -> float:
    """Sobel 差分绝对值均值 / 255。"""
    g = gray.astype(np.float64)
    gx = np.abs(np.diff(g, axis=1))
    gy = np.abs(np.diff(g, axis=0))
    return float((gx.mean() + gy.mean()) / 2.0 / 255.0)


def extract_frames(path: Path, fps: float, width: int, height: int, outdir: Path) -> List[np.ndarray]:
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.png"):
        old.unlink()
    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(path),
        "-vf", f"fps={fps},scale={width}:{height}:flags=area",
        "-start_number", "0", str(outdir / "%05d.png"),
    ])
    frames = []
    from PIL import Image
    for p in sorted(outdir.glob("*.png")):
        frames.append(np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8))
    return frames


def lufs(path: Path) -> Optional[float]:
    r = subprocess.run(
        ["ffmpeg", "-nostats", "-v", "info", "-i", str(path), "-af", "ebur128=peak=true",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    vals = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", r.stderr)
    return float(vals[-1]) if vals else None


def cut_times(frames: List[np.ndarray], fps: float) -> List[float]:
    if len(frames) < 3:
        return []
    diffs = []
    for i in range(1, len(frames)):
        d = np.abs(frames[i].astype(np.int16) - frames[i - 1].astype(np.int16)).mean()
        diffs.append(float(d))
    arr = np.array(diffs)
    thr = max(float(arr.mean() + 2.0 * arr.std()), 12.0)
    cand = [(i + 1) / fps for i, v in enumerate(diffs) if v >= thr]
    # 合并相邻候选（间隔 < 0.3s 只留最强）
    merged: List[float] = []
    for t in cand:
        if merged and t - merged[-1] < 0.3:
            continue
        merged.append(t)
    return merged


def compute_metrics(orig: Path, rep: Path, samp_fps: float = 2.0) -> Dict:
    po, pr = probe(orig), probe(rep)
    w, h = 270, 480
    with tempfile.TemporaryDirectory(prefix="ssim_") as td:
        fo = extract_frames(orig, samp_fps, w, h, Path(td) / "o")
        fr = extract_frames(rep, samp_fps, w, h, Path(td) / "r")
        n = min(len(fo), len(fr))
        if n == 0:
            raise RuntimeError("抽帧为空，无法比对")
        ssims, band_bot, band_top = [], [], []
        band_abs: List[Tuple[float, float, float]] = []   # (平均绝对差级, >20级占比, 带内亮度)
        pix_o, pix_r = [], []
        for i in range(n):
            a, b = fo[i], fr[i]
            ga = a.mean(axis=2)
            gb = b.mean(axis=2)
            ssims.append(ssim_gray(ga, gb))
            y0b, y1b = int(0.70 * h), int(0.97 * h)
            band_bot.append((edge_density(ga[y0b:y1b]), edge_density(gb[y0b:y1b])))
            y0t, y1t = int(0.02 * h), int(0.30 * h)
            band_top.append((edge_density(ga[y0t:y1t]), edge_density(gb[y0t:y1t])))
            dab = np.abs(ga[y0b:y1b] - gb[y0b:y1b])
            band_abs.append((float(dab.mean()), float((dab > 20).mean()),
                             float(ga[y0b:y1b].mean())))
            pix_o.append(a.reshape(-1, 3)[::7])
            pix_r.append(b.reshape(-1, 3)[::7])
    # 字幕带 / 顶部标题带：相对差取全片最大（任一处字幕没画对都算账）
    def worst_rel(pairs):
        """对称相对差；两侧都接近空白（基底 < 0.004）时跳过，避免百分比炸到天文数字。"""
        worst, at = 0.0, 0.0
        for i, (x, y) in enumerate(pairs):
            if max(x, y) < 0.004:
                continue
            rel = abs(y - x) / max(x, y)
            if rel > worst:
                worst, at = rel, i / samp_fps
        return worst, at
    sub_rel, sub_at = worst_rel(band_bot)
    top_rel, top_at = worst_rel(band_top)
    # 字幕带绝对口径：全片最差那一帧的「带内亮度平均绝对差」（级）
    sub_abs_lv, sub_abs_i = 0.0, 0
    for i, (v, _, _) in enumerate(band_abs):
        if v > sub_abs_lv:
            sub_abs_lv, sub_abs_i = v, i
    sub_vis = max((v[1] for v in band_abs), default=0.0)
    sub_edge_abs_lv = max((abs(x - y) for x, y in band_bot), default=0.0) * 255.0
    # 调色分位
    o = np.concatenate(pix_o).astype(np.float32)
    r = np.concatenate(pix_r).astype(np.float32)
    qs = [5, 50, 95]
    qo = np.percentile(o, qs, axis=0)
    qr = np.percentile(r, qs, axis=0)
    grade = float(np.abs(qo - qr).max())
    # 切点
    co = cut_times(fo, samp_fps)
    cr = cut_times(fr, samp_fps)
    cut_delta = 0.0
    if co and cr:
        cut_delta = max(min(abs(t - u) for u in cr) for t in co)
    dur_delta = abs(po["duration"] - pr["duration"])
    res = {
        "style_frames_compared": n,
        "ssim_mean": round(float(np.mean(ssims)), 4),
        "ssim_min": round(float(np.min(ssims)), 4),
        "subtitle_band_abs_lv_max": round(sub_abs_lv, 2),
        "subtitle_band_abs_at_s": round(sub_abs_i / samp_fps, 2),
        "subtitle_band_abs_band_luma": round(band_abs[sub_abs_i][2], 2) if band_abs else 0.0,
        "subtitle_band_visible_frac_max": round(sub_vis, 4),
        "subtitle_band_edge_abs_lv_max": round(sub_edge_abs_lv, 2),
        "subtitle_band_rel_max": round(sub_rel, 4),      # 旧口径，仅留档对照
        "subtitle_band_at_s": round(sub_at, 2),          # 旧口径最差帧
        "title_band_rel_max": round(top_rel, 4),
        "title_band_at_s": round(top_at, 2),
        "grade_max_level_delta": round(grade, 2),
        "grade_quantiles_orig": [[round(float(x), 1) for x in row] for row in qo],
        "grade_quantiles_rep": [[round(float(x), 1) for x in row] for row in qr],
        "cuts_orig": [round(t, 2) for t in co],
        "cuts_rep": [round(t, 2) for t in cr],
        "cuts_count_orig": len(co),
        "cuts_count_rep": len(cr),
        "cut_max_delta_s": round(cut_delta, 3),
        "lufs_orig": lufs(orig),
        "lufs_rep": lufs(rep),
        "duration_orig": po["duration"],
        "duration_rep": pr["duration"],
        "duration_delta_s": round(dur_delta, 3),
        "spec_orig": po,
        "spec_rep": pr,
    }
    res["lufs_delta"] = (
        round(abs(res["lufs_orig"] - res["lufs_rep"]), 2)
        if res["lufs_orig"] is not None and res["lufs_rep"] is not None else None
    )
    flags = []
    if res["ssim_mean"] < THRESH["ssim_min"]:
        flags.append(f"SSIM {res['ssim_mean']} < {THRESH['ssim_min']}")
    if res["subtitle_band_abs_lv_max"] > THRESH["sub_band_abs_lv"]:
        flags.append(
            f"字幕带绝对差 {res['subtitle_band_abs_lv_max']} 级 > {THRESH['sub_band_abs_lv']} 级"
            f"（{res['subtitle_band_abs_at_s']}s，带内亮度 {res['subtitle_band_abs_band_luma']} 级，"
            f">20 级像素占 {res['subtitle_band_visible_frac_max']:.1%}；"
            f"旧相对口径 {res['subtitle_band_rel_max']:.0%}@{res['subtitle_band_at_s']}s 不计入判定）"
        )
    if res["grade_max_level_delta"] > THRESH["grade_levels"]:
        flags.append(f"调色分位差 {res['grade_max_level_delta']} > 6 级")
    if res["cut_max_delta_s"] > THRESH["cut_delta_s"]:
        flags.append(f"切点错位 {res['cut_max_delta_s']}s")
    if res["lufs_delta"] is not None and res["lufs_delta"] > THRESH["lufs_delta"]:
        flags.append(f"响度差 {res['lufs_delta']} LU")
    if res["duration_delta_s"] > THRESH["dur_delta_s"]:
        flags.append(f"时长差 {res['duration_delta_s']}s")
    res["flags"] = flags
    res["verdict"] = "PASS" if not flags else "FLAG"
    return res


# ── 并排片 ──────────────────────────────────────────────────────────────────

def _tc(t: float) -> str:
    m, sec = divmod(max(t, 0.0), 60)
    return f"{int(m):02d}:{sec:06.3f}"


def make_overlay_strip(side_label: str, dur: float, w: int, h: int, video_fps: float,
                       out: Path, step: float = 0.1, note: str = "") -> Path:
    """PIL 生成「标签 + 时间码 + 帧号」透明叠加条（qtrle mov 带 alpha）。

    这台 ffmpeg 没编 freetype，`drawtext` 不可用，所以文字只能自己画。
    """
    from PIL import Image, ImageDraw, ImageFont
    tmp = out.parent / (out.stem + "_png")
    tmp.mkdir(parents=True, exist_ok=True)
    for old in tmp.glob("*.png"):
        old.unlink()
    big = max(18, w // 16)
    small = max(11, w // 40)
    fp = find_font()
    try:
        f_big = ImageFont.truetype(fp or "/System/Library/Fonts/Helvetica.ttc", big)
        f_small = ImageFont.truetype(fp or "/System/Library/Fonts/Helvetica.ttc", small)
    except Exception:
        f_big = f_small = ImageFont.load_default()
    n = max(1, int(math.ceil(dur / step)))
    for i in range(n):
        t = i * step
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        label = f"{side_label}"
        if note:
            label += f"  {note}"
        d.rectangle([12, 12, 12 + int(len(label) * big * 0.62) + 12, 12 + big + 10],
                    fill=(0, 0, 0, 115))
        d.text((18, 16), label, fill=(255, 255, 255, 255), font=f_big)
        tc = f"{_tc(t)}  f{int(round(t * video_fps))}"
        tw = int(len(tc) * small * 0.60) + 12
        d.rectangle([12, h - small - 26, 12 + tw, h - 8], fill=(0, 0, 0, 115))
        d.text((18, h - small - 22), tc, fill=(255, 255, 255, 255), font=f_small)
        img.save(tmp / f"{i:05d}.png")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", f"{1/step:.6f}",
         "-i", str(tmp / "%05d.png"), "-c:v", "qtrle", "-pix_fmt", "argb",
         "-t", f"{dur:.3f}", str(out)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"叠加条编码失败: {r.stderr[-1200:]}")
    return out


def build_sbs(orig: Path, rep: Path, out: Path, width: int, with_diff: bool,
              left_label: str, right_label: str) -> None:
    po, pr = probe(orig), probe(rep)
    aspect = (po["height"] / max(po["width"], 1)) or (16 / 9)
    ph = int(round(width * aspect)) // 2 * 2
    fps = max(po["fps"], pr["fps"]) or 30.0
    dur = max(po["duration"], pr["duration"])
    tmpdir = out.parent
    ov_a = make_overlay_strip(left_label, dur, width, ph, fps, tmpdir / "_ov_left.mov")
    ov_b = make_overlay_strip(right_label, dur, width, ph, fps, tmpdir / "_ov_right.mov")
    parts = [
        f"[0:v]fps={fps},scale={width}:{ph}:flags=lanczos,setsar=1,format=rgba,"
        f"tpad=stop_mode=clone:stop_duration={dur:.3f}[b0]",
        f"[1:v]fps={fps},scale={width}:{ph}:flags=lanczos,setsar=1,format=rgba,"
        f"tpad=stop_mode=clone:stop_duration={dur:.3f}[b1]",
        "[2:v]setpts=PTS-STARTPTS,format=rgba[o0]",
        "[3:v]setpts=PTS-STARTPTS,format=rgba[o1]",
        "[b0][o0]overlay=0:0:eof_action=repeat[s0]",
        "[b1][o1]overlay=0:0:eof_action=repeat[s1]",
    ]
    inputs = ["-i", str(orig), "-i", str(rep), "-i", str(ov_a), "-i", str(ov_b)]
    if with_diff:
        ov_d = tmpdir / "_ov_diff.mov"
        make_overlay_strip("", dur, width, ph, fps, ov_d, note="DIFF x4")
        inputs += ["-i", str(ov_d)]
        parts += [
            f"[0:v]fps={fps},scale={width}:{ph}:flags=area,setsar=1,format=yuv444p[oa]",
            f"[1:v]fps={fps},scale={width}:{ph}:flags=area,setsar=1,format=yuv444p[ob]",
            "[oa][ob]blend=all_mode=difference,format=gray,"
            "lutyuv=y='clip(val*4,0,255)',setsar=1,format=rgba[d0]",
            "[4:v]setpts=PTS-STARTPTS,format=rgba[o2]",
            "[d0][o2]overlay=0:0:eof_action=repeat[d]",
            "[s0][s1][d]hstack=inputs=3,format=yuv420p[v]",
        ]
    else:
        parts.append("[s0][s1]hstack=inputs=2,format=yuv420p[v]")

    graph = ";".join(parts)
    cmd = ["ffmpeg", "-y", "-v", "error"] + inputs + [
        "-filter_complex", graph, "-map", "[v]",
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-r", f"{fps}", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"并排片渲染失败({r.returncode}): {r.stderr[-2500:]}")
    for f in (ov_a, ov_b):
        try:
            f.unlink()
        except OSError:
            pass
    return None


# ── 索引网格 ────────────────────────────────────────────────────────────────

def build_grid(orig: Path, rep: Path, out: Path, left_label: str, right_label: str,
               panel_w: int = 480) -> Tuple[int, List[float]]:
    from PIL import Image, ImageDraw, ImageFont
    po, pr = probe(orig), probe(rep)
    dur = max(po["duration"], pr["duration"])
    aspect = (po["height"] / max(po["width"], 1)) or (16 / 9)
    pw = panel_w
    phh = int(round(pw * aspect)) // 2 * 2
    times = list(np.arange(0.0, max(dur - 0.05, 0.0), 5.0))
    if not times or times[-1] < dur - 0.5:
        times.append(round(max(dur - 0.1, 0.0), 2))

    def grab(path: Path, t: float) -> Image.Image:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
             "-vf", f"scale={pw}:{phh}:flags=lanczos", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True,
        )
        if not r.stdout:
            return Image.new("RGB", (pw, phh), (20, 20, 20))
        import io
        return Image.open(io.BytesIO(r.stdout)).convert("RGB")

    head = 30
    cols = 2
    rows = math.ceil(len(times) / cols)
    W = pw * cols + 8 * (cols + 1)
    H = head + rows * (phh + 34) + 8
    sheet = Image.new("RGB", (W, H), (16, 16, 16))
    d = ImageDraw.Draw(sheet)
    try:
        f_head = ImageFont.truetype(find_font() or "/System/Library/Fonts/Helvetica.ttc", 17)
        f_tag = ImageFont.truetype(find_font() or "/System/Library/Fonts/Helvetica.ttc", 13)
    except Exception:
        f_head = f_tag = ImageFont.load_default()
    d.text((10, 8), f"左 {left_label}   右 {right_label}   每 5 秒一格（先判再看 key）",
           fill=(235, 235, 235), font=f_head)
    for i, t in enumerate(times):
        cx = (i % cols) * (pw + 8) + 8
        cy = head + (i // cols) * (phh + 34)
        sheet.paste(grab(orig, t), (cx, cy))
        sheet.paste(grab(rep, t), (cx, cy))
        # 左右并排而不是上下：单格宽 = 2*pw，改画法
    # 重画：每格内部左右并排
    sheet = Image.new("RGB", (W, H), (16, 16, 16))
    d = ImageDraw.Draw(sheet)
    d.text((10, 8), f"每 5 秒一格 · 格内左 {left_label} / 右 {right_label}（先判再看 key）",
           fill=(235, 235, 235), font=f_head)
    half = pw // 2
    halfh = int(round(half * aspect)) // 2 * 2
    for i, t in enumerate(times):
        cx = (i % cols) * (pw + 8) + 8
        cy = head + (i // cols) * (halfh + 34)
        a = grab(orig, t).resize((half, halfh), Image.LANCZOS)
        b = grab(rep, t).resize((half, halfh), Image.LANCZOS)
        sheet.paste(a, (cx, cy))
        sheet.paste(b, (cx + half, cy))
        d.rectangle([cx, cy, cx + pw, cy + halfh], outline=(70, 70, 70))
        d.text((cx + 2, cy + halfh + 4), f"{t:.1f}s", fill=(200, 200, 200), font=f_tag)
    sheet.save(out, quality=92)
    return len(times), [round(float(t), 2) for t in times]


def main() -> None:
    ap = argparse.ArgumentParser(description="复刻 vs 原版并排盲审 + 六项指标")
    ap.add_argument("--style-id", required=True)
    ap.add_argument("--orig", required=True)
    ap.add_argument("--rep", required=True)
    ap.add_argument("--out", default=str(ROOT / "reports" / "sidebyside"))
    ap.add_argument("--seed", type=int, default=None, help="左右随机种子，默认取风格号")
    ap.add_argument("--width", type=int, default=720, help="并排片每侧宽度")
    ap.add_argument("--diff", action="store_true", help="加第三栏放大 4 倍差分")
    ap.add_argument("--metrics-only", action="store_true")
    ap.add_argument("--no-grid", action="store_true")
    args = ap.parse_args()

    orig, rep = Path(args.orig).expanduser(), Path(args.rep).expanduser()
    for p in (orig, rep):
        if not p.exists():
            raise SystemExit(f"找不到文件: {p}")
    outdir = Path(args.out).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    sid = args.style_id
    m = re.search(r"(\d{1,2})", sid)
    seed = args.seed if args.seed is not None else int(m.group(1)) if m else 0
    flip = random.Random(seed).random() < 0.5
    left_is_orig = not flip
    left_label = "A" if left_is_orig else "B"
    right_label = "B" if left_is_orig else "A"
    p_left, p_right = (orig, rep) if left_is_orig else (rep, orig)

    print(f"[sbs] {sid}: 左={left_label}  右={right_label}（A=原版: {left_is_orig}）")
    metrics = compute_metrics(orig, rep)
    (outdir / f"{sid}_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    row = {
        "style_id": sid,
        "A_side": "orig" if left_is_orig else "replica",
        "B_side": "replica" if left_is_orig else "orig",
        "verdict": metrics["verdict"],
        "ssim_mean": metrics["ssim_mean"],
        "ssim_min": metrics["ssim_min"],
        "subtitle_band_rel_max": metrics["subtitle_band_rel_max"],
        "grade_max_level_delta": metrics["grade_max_level_delta"],
        "cut_max_delta_s": metrics["cut_max_delta_s"],
        "lufs_delta": metrics["lufs_delta"],
        "duration_delta_s": metrics["duration_delta_s"],
        "flags": " | ".join(metrics["flags"]),
    }
    key_csv = outdir / "sidebyside_key.csv"
    write_head = not key_csv.exists()
    with key_csv.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_head:
            w.writeheader()
        w.writerow(row)

    print(json.dumps({k: metrics[k] for k in (
        "verdict", "ssim_mean", "subtitle_band_abs_lv_max", "subtitle_band_abs_at_s",
        "subtitle_band_rel_max", "grade_max_level_delta",
        "cut_max_delta_s", "lufs_delta", "duration_delta_s", "flags")},
        ensure_ascii=False, indent=2))
    if args.metrics_only:
        return

    vid = outdir / f"{sid}_sidebyside{'_diff' if args.diff else ''}.mp4"
    build_sbs(p_left, p_right, vid, args.width, args.diff, left_label, right_label)
    print(f"[sbs] 并排片 -> {vid}")
    if not args.no_grid:
        grid = outdir / f"{sid}_index_grid.jpg"
        n, times = build_grid(p_left, p_right, grid, left_label, right_label)
        print(f"[sbs] 索引网格 -> {grid}（{n} 格，{times[0]}–{times[-1]}s）")


if __name__ == "__main__":
    main()
