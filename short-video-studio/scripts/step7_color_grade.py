#!/usr/bin/env python3
"""
[Step 7: 逐镜三段曲线校色 + 真实肤色矢量掩膜 + 切段中点测光 + 库外特效 + 肤色回测闭环]
1. 按 EDL 实际切片区间 [source_in_sec, source_out_sec] 中点测光（不拿片头 3s 乱调第 20s 画面）。
2. 利用 Step 1 映射后的裁切人脸 ROI + BT.709 肤色像素掩膜剔除背景/眼镜/眉毛后测定真实 Cb/Cr。
3. 调色改为「逐风格三段关系」而不是「一个片源统一混合」：
   暗部(0.05)/中间调(0.5)/高光(0.95) 三段锚点 → ffmpeg curves 曲线；
   三段分色 → ffmpeg colorbalance 的 s/m/h 三组通道偏移；
   实测饱和度 → eq saturation；实测暖度 → 中间调白平衡。
   参数全部来自 assets/style_spectrum/color_effects.json（库外配置，读库不改库）。
4. 片源强度逐套给出（24 个显式值），不再用同一个 60–72% 系数。
5. 源里存在而此前缺失的视觉特效按包裝证据逐套开关：暗角、颗粒、怀旧相框、局部虚化、
   聚光圆窗、不规则底框、轻微颤抖；开关与参数同样落在 color_effects.json，不开即显式 false。
6. 闭环回测：生成 blended LUT 后对 Hero 帧抽样复核套色后肤色是否偏移 common.SKIN_YCBCR_RANGE，若偏移则自动降强度重编。
输出：pipeline/07_color_plan.json (+ pipeline/07_blended_lut.cube)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    COLOR_EFFECT_KEYS,
    SKIN_YCBCR_RANGE,
    ffmpeg_has_filter,
    find_bin,
    load_color_effects,
    load_style_applied,
    load_style_spectrum_entry,
    maybe_reexec_in_venv,
    parse_manifest_faces,
    read_json,
    resolve_lut_path,
    run_cmd,
    validate_color_effects,
    write_cube_lut,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

import numpy as np


# ── 逐风格调色配置（库外 color_effects.json）：三段曲线 + 三段分色 + 饱和度 + 特效 ──
# 库内 style_spectrum_library.json 只读：本文件只消费它的 color_and_lut / packaging_techniques 实测值。
_PLACEHOLDER_SHADOW_RGB = (32.0, 34.0, 38.0)
_PLACEHOLDER_HIGHLIGHT_RGB = (225.0, 224.0, 220.0)
_SAT_NEUTRAL_PCT = 20.0
_COLOR_EFFECTS_DOC: Optional[Dict[str, Any]] = None
_STYLE_COLOR_CACHE: Dict[str, Dict[str, Any]] = {}


def _clampf(value: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _f4(value: float) -> str:
    """滤镜参数格式化：四位小数、抹掉 -0.0。"""
    v = round(float(value), 4)
    if abs(v) < 1e-9:
        v = 0.0
    return f"{v:.4f}"


def _load_color_effects_doc() -> Dict[str, Any]:
    global _COLOR_EFFECTS_DOC
    if _COLOR_EFFECTS_DOC is None:
        doc = load_color_effects()
        issues = validate_color_effects(doc)
        if issues:
            print("[WARN] color_effects.json 校验异常：" + "；".join(issues[:6]), file=sys.stderr)
        _COLOR_EFFECTS_DOC = doc
    return _COLOR_EFFECTS_DOC


def _is_placeholder_triplets(cl: Dict[str, Any]) -> bool:
    """库内 shadow/midtone/highlight 是否为回退占位值（32/34/38 + 灰度中调 + 225/224/220）。"""
    sh = cl.get("shadow_rgb") or []
    md = cl.get("midtone_rgb") or []
    hl = cl.get("highlight_rgb") or []
    if len(sh) != 3 or len(md) != 3 or len(hl) != 3:
        return True
    return (
        all(abs(float(sh[i]) - _PLACEHOLDER_SHADOW_RGB[i]) < 1e-6 for i in range(3))
        and max(md) - min(md) < 1e-6
        and all(abs(float(hl[i]) - _PLACEHOLDER_HIGHLIGHT_RGB[i]) < 1e-6 for i in range(3))
    )


def _tint_from_rgb(triplet: List[Any], cap: float = 0.03) -> List[float]:
    """把实测三段 RGB 去亮度后转成纯色倾向，并按最大分量归一到 cap，防止单一风格调色过冲。"""
    vals = [_clampf(v, 0.0, 255.0) for v in (triplet or [0, 0, 0])][:3]
    if len(vals) != 3:
        return [0.0, 0.0, 0.0]
    mean = sum(vals) / 3.0
    if mean <= 1e-6:
        return [0.0, 0.0, 0.0]
    raw = [v / mean - 1.0 for v in vals]
    peak = max(abs(x) for x in raw)
    if peak <= 1e-6:
        return [0.0, 0.0, 0.0]
    factor = cap / peak
    return [round(_clampf(x * factor, -0.04, 0.04), 4) for x in raw]


def _derive_style_color(style_id: str) -> Dict[str, Any]:
    """color_effects.json 缺少该风格时的应变路径：从库内实测值现推同一套参数（公式与配置生成一致）。"""
    entry = load_style_spectrum_entry(style_id) or {}
    cl = entry.get("color_and_lut") or {}
    hsl = int((entry.get("packaging_techniques") or {}).get("hsl_materials") or 0)
    y = _clampf(cl.get("y_mean_255"), 0.0, 255.0, 128.0)
    sat = _clampf(cl.get("sat_mean_pct"), 0.0, 100.0, _SAT_NEUTRAL_PCT)
    warm = _clampf(cl.get("warmth_index"), -100.0, 100.0, 0.0)
    p05 = _clampf(cl.get("y_p05_255"), 0.0, 255.0, max(0.0, y - 45.0))
    p50 = _clampf(cl.get("y_p50_255"), 0.0, 255.0, y)
    p95 = _clampf(cl.get("y_p95_255"), 0.0, 255.0, min(255.0, y + 55.0))

    lut_strength = round(_clampf(0.50 + 0.0045 * sat + 0.0008 * abs(warm) - 0.0025 * hsl, 0.45, 0.76), 2)
    sat_scale = round(_clampf(0.94 + 0.006 * (sat - _SAT_NEUTRAL_PCT), 0.88, 1.14), 3)
    contrast = round(_clampf(cl.get("contrast_gain"), 1.0, 1.16, 1.06), 3)
    extreme = _clampf(abs(y - 128.0) / 128.0, 0.0, 1.0)
    curve_w = round(_clampf(0.42 + 0.10 * min(hsl / 32.0, 1.0) + 0.18 * extreme, 0.42, 0.78), 2)
    anchors = []
    for x, measured in ((0.05, p05), (0.5, p50), (0.95, p95)):
        target = measured / 255.0
        anchors.append({"input": x, "output": round(_clampf(x + curve_w * (target - x), 0.0, 1.0), 4), "measured": measured})

    w = _clampf(warm / 90.0, -1.0, 1.0)
    if not _is_placeholder_triplets(cl):
        sh_t = _tint_from_rgb(cl.get("shadow_rgb"))
        md_t = _tint_from_rgb(cl.get("midtone_rgb"))
        hi_t = _tint_from_rgb(cl.get("highlight_rgb"))
    else:
        sh_meas = [_clampf(v, -1.0, 1.0) for v in (cl.get("shadow_tint_rgb") or [0, 0, 0])][:3]
        hi_meas = [_clampf(v, -1.0, 1.0) for v in (cl.get("highlight_tint_rgb") or [0, 0, 0])][:3]
        sh_t = [round(_clampf(v * 1.6, -0.04, 0.04), 4) for v in sh_meas] if max(abs(v) for v in sh_meas) > 1e-6 else [
            round(_clampf(w * 0.010, -0.04, 0.04), 4), 0.0, round(_clampf(-w * 0.010, -0.04, 0.04), 4)]
        hi_t = [round(_clampf(v * 1.6, -0.04, 0.04), 4) for v in hi_meas] if max(abs(v) for v in hi_meas) > 1e-6 else [
            round(_clampf(w * 0.009, -0.04, 0.04), 4), 0.0, round(_clampf(-w * 0.007, -0.04, 0.04), 4)]
        md_t = [round(_clampf(w * 0.013, -0.04, 0.04), 4), 0.0, round(_clampf(-w * 0.013, -0.04, 0.04), 4)]

    return {
        "style_name_cn": entry.get("style_name_cn") or style_id,
        "lut_strength": lut_strength,
        "saturation_scale": sat_scale,
        "contrast": contrast,
        "warmth": round(w, 3),
        "tone_curve": {"curve_weight": curve_w, "anchors": anchors},
        "zone_tint": {"shadow": sh_t, "midtone": md_t, "highlight": hi_t},
        "effects": {k: False for k in COLOR_EFFECT_KEYS},
        "basis_cn": "库内实测现推：y {:.1f}/sat {:.1f}%/warmth {:+.1f}/HSL {} 处".format(y, sat, warm, hsl),
    }


def _resolve_style_color(style_id: Optional[str]) -> Dict[str, Any]:
    """取该风格的三段曲线、饱和度、白平衡与特效开关（优先库外配置，缺条目时按库内实测现推）。"""
    if not style_id:
        return {}
    if style_id in _STYLE_COLOR_CACHE:
        return _STYLE_COLOR_CACHE[style_id]
    node = (_load_color_effects_doc().get("styles") or {}).get(style_id)
    if not isinstance(node, dict):
        print(f"[WARN] color_effects.json 缺少 {style_id}，按库内实测现推参数", file=sys.stderr)
        node = _derive_style_color(style_id)
    _STYLE_COLOR_CACHE[style_id] = node
    return node


_LUMA_PCT_CACHE: Dict[Tuple[str, float, float], Optional[Tuple[float, float, float]]] = {}


def probe_luma_percentiles(path_str: str, start_sec: float = 0.0,
                           dur_sec: float = 0.0, pre_vf: str = "") -> Optional[Tuple[float, float, float]]:
    """量这一段素材的亮度 p05/p50/p95（0–1）。

    pre_vf 非空时先把该镜的前置链（eq + 三段分色）套上再量，得到「进曲线之前」的真实分位，
    曲线再用它做输入锚点，才能把本片真正推到目标分位（而不是只推一半）。
    """
    key = (str(path_str), round(float(start_sec), 2), round(float(dur_sec), 2), pre_vf)
    if key in _LUMA_PCT_CACHE:
        return _LUMA_PCT_CACHE[key]
    src = Path(str(path_str))
    ff = find_bin("ffmpeg")
    if not src.is_file() or not ff:
        _LUMA_PCT_CACHE[key] = None
        return None
    ss = max(0.0, float(start_sec))
    cmd = [str(ff), "-v", "error"]
    if ss > 0:
        cmd += ["-ss", f"{ss:.3f}"]
    if float(dur_sec) > 0:
        cmd += ["-t", f"{max(0.2, float(dur_sec)):.3f}"]
    chain = (pre_vf + "," if pre_vf else "") + "fps=2,scale=64:114"
    cmd += ["-i", str(src), "-vf", chain, "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout
    except Exception:
        _LUMA_PCT_CACHE[key] = None
        return None
    frame_bytes = 64 * 114 * 3
    n = len(raw) // frame_bytes
    if n <= 0:
        _LUMA_PCT_CACHE[key] = None
        return None
    arr = np.frombuffer(raw[: n * frame_bytes], dtype=np.uint8).reshape(n, 114, 64, 3).astype(np.float32) / 255.0
    luma = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
    p05, p50, p95 = (float(v) for v in np.percentile(luma, [5, 50, 95]))
    out = (_clampf(p05, 0.0, 1.0), _clampf(p50, 0.0, 1.0), _clampf(p95, 0.0, 1.0))
    _LUMA_PCT_CACHE[key] = out
    return out


def _tone_curve_filter(style_color: Dict[str, Any],
                       src_pct: Optional[Tuple[float, float, float]] = None,
                       targets: Optional[Tuple[float, float, float]] = None,
                       black_lift: float = 0.0) -> Optional[str]:
    """暗部/中间调/高光三段锚点 → ffmpeg curves（单调插值，不产生反转过冲）。

    src_pct 给定该镜自己的 p05/p50/p95 时，输入侧就用实测分位（真分位对齐），输出侧直接压到
    目标分位——不再用 curve_weight 反推（反推会在目标超出可达范围时把整条曲线顶到饱和）；
    curve_weight 的“收着改”由闭环校准的残差反馈体现。
    black_lift > 0 时把 0 点抬起来（片源有死黑、目标暗部却更亮时，只有抬黑位才能到）。
    """
    anchors = (style_color.get("tone_curve") or {}).get("anchors") or []
    if len(anchors) != 3 or not ffmpeg_has_filter("curves"):
        return None
    fallback = (0.05, 0.50, 0.95)
    weight = _clampf(float((style_color.get("tone_curve") or {}).get("curve_weight") or 0.0), 0.15, 1.0)
    lift = _clampf(float(black_lift), 0.0, 0.6)
    pts: List[Tuple[float, float]] = [(0.0, lift)]
    for i, a in enumerate(anchors):
        measured = (targets[i] * 255.0) if targets is not None else a.get("measured")
        if src_pct is not None and measured is not None:
            inx = _clampf(float(src_pct[i]), 0.005, 0.995)
            outy = _clampf(float(measured) / 255.0, lift, 1.0)
            pts.append((inx, outy))
            continue
        # 没有本镜分位时退回库里的输入/输出锚点，仍按 curve_weight 混一次（近似模式）
        inx = _clampf(float(a.get("input", fallback[i])), 0.005, 0.995)
        raw = float(a.get("output", 0.0))
        outy = _clampf(inx + weight * (raw - inx), lift, 1.0)
        pts.append((inx, outy))
    pts.append((1.0, 1.0))
    # 输入必须严格递增（贴合素材实际分布时三点可能挤在一起）
    for i in range(1, len(pts)):
        if pts[i][0] <= pts[i - 1][0]:
            pts[i] = (min(0.995, pts[i - 1][0] + 0.004), pts[i][1])
    out_mono: List[Tuple[float, float]] = [pts[0]]
    for x, y in pts[1:]:
        out_mono.append((x, max(y, out_mono[-1][1])))
    return "curves=all='{}':interp=pchip".format(" ".join(f"{_f4(x)}/{_f4(y)}" for x, y in out_mono))


def _zone_balance_filter(style_color: Dict[str, Any], corr: Dict[str, Any]) -> Optional[str]:
    """三段分色：暗部/中间调/高光各自的 R/G/B 偏移，肤色保护与 Hero 锚定叠加到中间调组。"""
    if not ffmpeg_has_filter("colorbalance"):
        return None
    tint = style_color.get("zone_tint") or {}

    def zone(name: str) -> List[float]:
        vals = [float(v) for v in (tint.get(name) or [0.0, 0.0, 0.0])][:3]
        if len(vals) != 3:
            return [0.0, 0.0, 0.0]
        # colorbalance 只接受 -1..1；配置里若误写 0–255 的三元组，这里按亮度差归一并夹住
        if max(abs(v) for v in vals) > 1.0:
            lum = 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]
            vals = [max(-0.35, min(0.35, (v - lum) / 255.0 * 1.6)) for v in vals]
        return [max(-0.4, min(0.4, v)) for v in vals]

    sh, md, hi = zone("shadow"), zone("midtone"), zone("highlight")
    md[0] = _clampf(md[0] + float(corr.get("rm", 0.0)), -0.1, 0.1)
    md[2] = _clampf(md[2] + float(corr.get("bm", 0.0)), -0.1, 0.1)
    return (
        f"colorbalance=rs={_f4(sh[0])}:gs={_f4(sh[1])}:bs={_f4(sh[2])}"
        f":rm={_f4(md[0])}:gm={_f4(md[1])}:bm={_f4(md[2])}"
        f":rh={_f4(hi[0])}:gh={_f4(hi[1])}:bh={_f4(hi[2])}"
    )


def _film_frame_boxes(spec: Dict[str, Any]) -> List[str]:
    """怀旧相框：四边压边，拍立得底边更宽（源 black_polaroid_frame 的宽底白/黑框特征）。"""
    base = min(CANVAS_WIDTH, CANVAS_HEIGHT)
    bp = max(2, int(round(base * _clampf(spec.get("border_frac"), 0.005, 0.08, 0.02))))
    color = str(spec.get("color") or "0x0A0A0A")
    frame_style = str(spec.get("style") or "")
    bottom = max(bp, int(round(bp * 1.7))) if frame_style == "black_polaroid" else bp
    boxes = [
        f"drawbox=x=0:y=0:w=iw:h={bp}:color={color}@1:t=fill",
        f"drawbox=x=0:y=ih-{bottom}:w=iw:h={bottom}:color={color}@1:t=fill",
        f"drawbox=x=0:y=0:w={bp}:h=ih:color={color}@1:t=fill",
        f"drawbox=x=iw-{bp}:y=0:w={bp}:h=ih:color={color}@1:t=fill",
    ]
    if frame_style == "white_paper":
        inset = bp + 3
        boxes.append(f"drawbox=x={inset}:y={inset}:w=iw-{2 * inset}:h=ih-{2 * inset}:color=0x000000@0.30:t=2")
    return boxes


def _irregular_frame_boxes(spec: Dict[str, Any]) -> List[str]:
    """不规则黑框：四边不等宽，再用两条错位窄边打散完美直线，近似源里手工贴的底框。"""
    edges = spec.get("edges") or {}
    color = str(spec.get("color") or "0x141618")
    top = max(1, int(edges.get("top", 14)))
    bottom = max(1, int(edges.get("bottom", 12)))
    left = max(1, int(edges.get("left", 10)))
    right = max(1, int(edges.get("right", 18)))
    return [
        f"drawbox=x=0:y=0:w=iw:h={top}:color={color}@1:t=fill",
        f"drawbox=x=0:y=ih-{bottom}:w=iw:h={bottom}:color={color}@1:t=fill",
        f"drawbox=x=0:y=0:w={left}:h=ih:color={color}@1:t=fill",
        f"drawbox=x=iw-{right}:y=0:w={right}:h=ih:color={color}@1:t=fill",
        f"drawbox=x=0:y=0:w=iw/3:h={top + 7}:color={color}@1:t=fill",
        f"drawbox=x=iw*2/3:y=ih-{bottom + 5}:w=iw/3:h={bottom + 5}:color={color}@1:t=fill",
    ]


def _window_hit(spec: Dict[str, Any], clip_window: Optional[Tuple[float, float]]) -> bool:
    """特效只在该时间窗内生效：与分镜时间轴的交叠不足较短一方 40% 就算没命中。"""
    win = spec.get("window")
    if not isinstance(win, dict) or clip_window is None:
        return True
    try:
        ws = float(win.get("start") or 0.0)
        wd = float(win.get("duration") or 0.0)
    except (TypeError, ValueError):
        return True
    if wd <= 0.0:
        return True
    we = ws + wd
    cs, ce = float(clip_window[0]), float(clip_window[1])
    overlap = min(ce, we) - max(cs, ws)
    if overlap <= 0.0:
        return False
    return overlap >= 0.4 * min(ce - cs, wd)


def _spotlight_geq(spec: Dict[str, Any]) -> str:
    """真圆形聚光：径向遮罩直接乘在 YUV 三个平面上（内圈全亮、羽化过渡、外圈压暗）。

    以前用 vignette 顶替，只能整圈渐变、做不出「中间一个亮圈」；geq 是逐像素径向函数，
    内半径内系数恒为 1、羽化带内线性过渡，等价于剪映「聚光灯」的蒙版半径 + 羽化。
    半径与圆心用 W/H 的相对量写，换分辨率也成立。
    """
    angle = _clampf(spec.get("angle"), 0.2, 1.2, 0.63)
    fx = _clampf(float(spec.get("x") or CANVAS_WIDTH // 2) / CANVAS_WIDTH, 0.0, 1.0)
    fy = _clampf(float(spec.get("y") or CANVAS_HEIGHT // 2) / CANVAS_HEIGHT, 0.0, 1.0)
    inner = _clampf(spec.get("inner_frac"), 0.10, 0.85, 0.34)
    feather = _clampf(spec.get("feather_frac"), 0.25, 1.20, 0.62)
    keep = _clampf(1.0 - 0.55 * angle, 0.22, 0.85)
    cx, cy = f"{fx:.4f}*W", f"{fy:.4f}*H"
    r_in = f"({inner:.3f}*min(W,H))"
    r_end = f"({feather:.3f}*0.5*hypot(W,H))"
    dist = f"hypot(X-{cx},Y-{cy})"
    m = f"({keep:.4f}+{1.0 - keep:.4f}*clip(({r_end}-{dist})/({r_end}-{r_in}),0,1))"
    return (f"geq=lum='lum(X,Y)*{m}':"
            f"cb='128+(cb(X,Y)-128)*{m}':"
            f"cr='128+(cr(X,Y)-128)*{m}'")


def _effect_filters(style_color: Dict[str, Any], is_broll: bool = False,
                    clip_window: Optional[Tuple[float, float]] = None) -> List[str]:
    """按风格特效开关生成滤镜：只做源草稿 effect 轨里真有的特效（color_effects.effects）。

    clip_window 给该分镜在成片时间轴上的 (起点, 终点)，特效只在源里的时间窗内生效。
    """
    eff = style_color.get("effects") or {}

    def spec(key: str) -> Optional[Dict[str, Any]]:
        val = eff.get(key)
        return val if isinstance(val, dict) and _window_hit(val, clip_window) else None

    out: List[str] = []

    # 轻微颤抖放最前：先抖画面再画框/压暗，画框保持不动，像手持机身在固定框内晃
    shake = spec("shake")
    if shake and ffmpeg_has_filter("crop"):
        ax = max(1, int(round(_clampf(shake.get("amp_x"), 1, 8, 2))))
        ay = max(1, int(round(_clampf(shake.get("amp_y"), 1, 8, 2))))
        speed = _clampf(shake.get("speed"), 0.2, 4.0, 1.0)
        out.append(
            f"crop=w=iw-{2 * ax}:h=ih-{2 * ay}"
            f":x='{ax}+{ax}*sin(n*{_f4(speed)})':y='{ay}+{ay}*cos(n*{_f4(speed * 0.8)})'"
        )
        out.append(f"scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}:flags=bicubic")

    spot = spec("spotlight")
    if spot and ffmpeg_has_filter("geq"):
        out.append(_spotlight_geq(spot))

    vig = spec("vignette")
    if vig and ffmpeg_has_filter("vignette"):
        angle = _clampf(vig.get("angle"), 0.2, 1.5, 0.9)
        x0 = int(_clampf(vig.get("x"), 0, CANVAS_WIDTH, CANVAS_WIDTH // 2))
        y0 = int(_clampf(vig.get("y"), 0, CANVAS_HEIGHT, CANVAS_HEIGHT // 2))
        out.append(f"vignette=angle={_f4(angle)}:x0={x0}:y0={y0}")

    # 剪映「模糊」是整画面特效（effect 轨挂一个 effects_adjust_blur 参数），不是空间局部虚化
    blur = spec("blur")
    if blur and ffmpeg_has_filter("gblur"):
        sigma = _clampf(blur.get("sigma"), 0.3, 16.0, 4.0)
        out.append(f"gblur=sigma={_f4(sigma)}:steps=2")

    grain = spec("grain")
    if grain and ffmpeg_has_filter("noise"):
        strength = int(round(_clampf(grain.get("strength"), 1, 20, 5)))
        flags = str(grain.get("flags") or "t+u")
        out.append(f"noise=alls={strength}:allf={flags}")

    frame = spec("film_frame")
    if frame and ffmpeg_has_filter("drawbox"):
        out.extend(_film_frame_boxes(frame))
    irregular = spec("irregular_black_frame")
    if irregular and ffmpeg_has_filter("drawbox"):
        out.extend(_irregular_frame_boxes(irregular))

    return out


def _effects_enabled(style_color: Dict[str, Any]) -> List[str]:
    eff = style_color.get("effects") or {}
    return [k for k in COLOR_EFFECT_KEYS if isinstance(eff.get(k), dict)]


def _applied_effects(style_color: Dict[str, Any], is_broll: bool = False,
                     clip_window: Optional[Tuple[float, float]] = None) -> List[str]:
    """本镜真实会执行的特效（与 _effect_filters 完全同一套判定），方便回查每个分镜到底开了什么。"""
    eff = style_color.get("effects") or {}
    names: List[str] = []
    for key in ("shake", "spotlight", "vignette", "blur", "grain", "film_frame", "irregular_black_frame"):
        val = eff.get(key)
        if not isinstance(val, dict) or not _window_hit(val, clip_window):
            continue
        names.append(str(val.get("source_effect") or key))
    return names


def _escape_lut_filter_path(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace("'", "\\'").replace(":", "\\:")


def probe_clip_color_stats(
    media_path: Path,
    start_sec: float = 0.0,
    dur_sec: float = 3.0,
) -> Dict[str, float]:
    """针对 EDL 实际切片区间 [start_sec, start_sec + dur_sec] 的中点窗口抽样测光。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg or not media_path.exists():
        return {"yavg": 125.0, "uavg": 128.0, "vavg": 128.0}
    c_dur = max(0.3, float(dur_sec))
    win_dur = min(4.0, c_dur)
    win_start = max(0.0, float(start_sec) + max(0.0, (c_dur - win_dur) * 0.5))
    cmd = [
        ffmpeg, "-v", "info",
        "-ss", f"{win_start:.3f}", "-t", f"{win_dur:.3f}",
        "-i", str(media_path),
        "-vf", "select='not(mod(n,12))',scale=in_color_matrix=bt709:out_color_matrix=bt709,format=yuv420p,signalstats,metadata=print",
        "-an", "-f", "null", "-",
    ]
    res = run_cmd(cmd, check=False)

    def avg_val(key: str, default: float = 128.0) -> float:
        vals = [float(m) for m in re.findall(rf"lavfi\.signalstats\.{key}=([0-9.]+)", res.stderr or "")]
        return round(sum(vals) / len(vals), 2) if vals else default

    return {
        "yavg": avg_val("YAVG", 125.0),
        "uavg": avg_val("UAVG", 128.0),
        "vavg": avg_val("VAVG", 128.0),
    }


def _compute_masked_skin_ycbcr_from_rgb(
    arr_rgb: np.ndarray,
    face_bbox: Dict[str, float],
) -> Optional[Dict[str, float]]:
    """使用 BT.709 矩阵 + 皮肤色度粗掩膜提取人脸真实肤色画素（剔除眼镜/眉毛/胡须/背景）。"""
    h_s, w_s, _ = arr_rgb.shape
    fx, fy, fw, fh = float(face_bbox["x"]), float(face_bbox["y"]), float(face_bbox["w"]), float(face_bbox["h"])
    if max(fx, fy, fw, fh) > 1.01:
        fx, fy, fw, fh = fx / 1080.0, fy / 1920.0, fw / 1080.0, fh / 1920.0
    x0 = max(0, int((fx + fw * 0.22) * w_s))
    x1 = min(w_s, max(x0 + 4, int((fx + fw * 0.78) * w_s)))
    y0 = max(0, int((fy + fh * 0.22) * h_s))
    y1 = min(h_s, max(y0 + 4, int((fy + fh * 0.76) * h_s)))
    roi = arr_rgb[y0:y1, x0:x1].astype(np.float32)
    if roi.size < 12:
        return None

    r, g, b = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    # BT.709 矩阵系数转换
    y_val = 16.0 + (0.2126 * r + 0.7152 * g + 0.0722 * b) * (219.0 / 255.0)
    cb = 128.0 + (-0.114572 * r - 0.385428 * g + 0.5 * b) * (224.0 / 255.0)
    cr = 128.0 + (0.5 * r - 0.454153 * g - 0.045847 * b) * (224.0 / 255.0)

    # 掩膜只用来「去掉背景/衣服/头发」，不能顺手把要检测的值裁掉：
    # 旧掩膜写着 cr <= 178，而 BT.709 健康扇区的上限是 174 —— 于是「肤色已经跑到 178」这件事
    # 在掩膜这一步就被夹回 167，守门永远看不见（2026-11-24 实测：同一帧窄掩膜 cr=167.6、
    # 宽掩膜 cr=178.1，成片实测 180）。掩膜范围一律放宽到扇区之外，判定仍用 SKIN_YCBCR_RANGE。
    skin_mask = (cb >= 70.0) & (cb <= 150.0) & (cr >= 110.0) & (cr <= 195.0) & (y_val >= 35.0) & (y_val <= 235.0)
    if int(np.sum(skin_mask)) >= 8:
        return {
            "skin_y": round(float(np.median(y_val[skin_mask])), 1),
            "skin_cb": round(float(np.median(cb[skin_mask])), 1),
            "skin_cr": round(float(np.median(cr[skin_mask])), 1),
            "mask_ratio": round(float(np.mean(skin_mask)), 3),
        }
    return {
        "skin_y": round(float(np.median(y_val)), 1),
        "skin_cb": round(float(np.median(cb)), 1),
        "skin_cr": round(float(np.median(cr)), 1),
        "mask_ratio": 0.0,
    }


def probe_face_skin_ycbcr(
    media_path: Path,
    face_bbox: Dict[str, float],
    at_sec: float = 0.2,
    extra_vf: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg or not media_path.exists():
        return None
    w_s, h_s = 270, 480
    scale_bt709 = f"scale={w_s}:{h_s}:in_color_matrix=bt709:out_color_matrix=bt709,format=rgb24"
    vf_arg = f"{extra_vf},{scale_bt709}" if extra_vf else scale_bt709
    res = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, at_sec):.2f}", "-i", str(media_path),
            "-frames:v", "1", "-vf", vf_arg,
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if len(res.stdout) < w_s * h_s * 3:
        return None
    arr = np.frombuffer(res.stdout[: w_s * h_s * 3], dtype=np.uint8).reshape((h_s, w_s, 3))
    return _compute_masked_skin_ycbcr_from_rgb(arr, face_bbox)


def compute_hero_and_shot_correction(
    stats: Dict[str, float],
    hero_stats: Dict[str, float],
    is_hero: bool,
    skin_ycbcr: Optional[Dict[str, float]],
    style_id: Optional[str] = None,
) -> Dict[str, Any]:
    """基于 Hero 锚定与中间调皮肤矢量保护计算校色参数（肤色纠偏与亮度锚定，三段分色与曲线另走配置）。"""
    style_color = _resolve_style_color(style_id)
    y, u, v = stats["yavg"], stats["uavg"], stats["vavg"]

    skin_bm, skin_rm = 0.0, 0.0
    if skin_ycbcr:
        cb, cr = skin_ycbcr["skin_cb"], skin_ycbcr["skin_cr"]
        if cb > SKIN_YCBCR_RANGE["cb_max"] - 6.0:
            skin_bm = -(cb - 118.0) / 360.0
        elif cb < SKIN_YCBCR_RANGE["cb_min"] + 6.0:
            skin_bm = (100.0 - cb) / 360.0
        if cr < SKIN_YCBCR_RANGE["cr_min"] + 6.0:
            skin_rm = (142.0 - cr) / 360.0
        elif cr > SKIN_YCBCR_RANGE["cr_max"] - 6.0:
            skin_rm = -(cr - 160.0) / 360.0

    if is_hero:
        y_corr = 0.0
        if y < 85.0:
            y_corr = (95.0 - y) / 480.0
        elif y > 175.0:
            y_corr = (165.0 - y) / 480.0
        brightness = round(max(-0.14, min(0.14, y_corr)), 3)
        bm_shift = round(max(-0.08, min(0.08, skin_bm)), 3)
        rm_shift = round(max(-0.08, min(0.08, skin_rm)), 3)
    else:
        y_diff = hero_stats["yavg"] - y
        u_diff = hero_stats["uavg"] - u
        v_diff = hero_stats["vavg"] - v
        brightness = round(max(-0.12, min(0.12, (y_diff * 0.45) / 380.0)), 3)
        bm_base = (u_diff * 0.30) / 420.0
        rm_base = (v_diff * 0.30) / 420.0
        if skin_ycbcr:
            bm_base = bm_base * 0.5 + skin_bm * 0.7
            rm_base = rm_base * 0.5 + skin_rm * 0.7
        bm_shift = round(max(-0.07, min(0.07, bm_base)), 3)
        rm_shift = round(max(-0.07, min(0.07, rm_base)), 3)

    return {
        "brightness": brightness,
        "contrast": round(_clampf(style_color.get("contrast"), 1.0, 1.16, 1.06), 3),
        "saturation": round(_clampf(style_color.get("saturation_scale"), 0.5, 1.6, 1.0), 3),
        "rm": rm_shift,
        "bm": bm_shift,
    }


# 闭环降强度的下限（相对 1.0）：低于这个值画面影调已经明显偏离风格真值
LUT_STRENGTH_FLOOR = 0.45


def build_blended_cube_lut(lut_path: Path, strength: float, out_path: Path) -> tuple[Path, float]:
    """将源 3D .cube LUT 与恒等映射线性混合；通过 common.write_cube_lut 写出并如实返回有效强度。"""
    strength = max(0.0, min(1.0, strength))
    if strength >= 0.99 or not lut_path.exists():
        return lut_path, 1.0 if lut_path.exists() else 0.0

    lines = lut_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    size = 33
    triplets: List[List[float]] = []
    title = f"Blended_{lut_path.stem}_{int(round(strength*100))}pct"

    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("LUT_3D_SIZE"):
            size = int(s.split()[1])
            continue
        if s.startswith(("TITLE", "DOMAIN_MIN", "DOMAIN_MAX")):
            continue
        parts = s.split()
        if len(parts) == 3:
            try:
                triplets.append([float(parts[0]), float(parts[1]), float(parts[2])])
            except ValueError:
                continue

    if len(triplets) != size ** 3:
        return lut_path, 1.0

    lut_arr = np.array(triplets, dtype=np.float32)
    grid = np.linspace(0.0, 1.0, size, dtype=np.float32)
    bb, gg, rr = np.meshgrid(grid, grid, grid, indexing="ij")
    identity = np.stack([rr, gg, bb], axis=-1).reshape(-1, 3)

    blended = np.clip(identity * (1.0 - strength) + lut_arr * strength, 0.0, 1.0)
    write_cube_lut(out_path, blended, size, title)
    return out_path, strength


def _build_vf_for_corr(
    corr: Dict[str, Any],
    lut_path: Optional[Path],
    lut_strength: float = 1.0,
    style_color: Optional[Dict[str, Any]] = None,
    is_broll: bool = False,
    src_pct: Optional[Tuple[float, float, float]] = None,
    targets: Optional[Tuple[float, float, float]] = None,
    black_lift: float = 0.0,
    include_effects: bool = True,
    clip_window: Optional[Tuple[float, float]] = None,
) -> str:
    """拼单分镜调色链：亮度/对比/饱和 → 三段分色 → 三段曲线 → 片源 → 特效 → BT.709 收口。

    include_effects=False 时不含特效栈：闭环校准要在「特效之前」对齐分位，否则暗角/聚光会把
    输出分布整体压下去，校准就永远追不上目标（源片实测分位本来就取自未加特效的画面）。
    """
    style_color = style_color if style_color is not None else {}
    parts: List[str] = [
        f"eq=brightness={corr['brightness']}:contrast={corr['contrast']}:saturation={corr['saturation']}"
    ]
    balance = _zone_balance_filter(style_color, corr)
    if balance:
        parts.append(balance)
    tone = _tone_curve_filter(style_color, src_pct, targets, black_lift=black_lift)
    if tone:
        parts.append(tone)

    if float(lut_strength) > 0.01 and lut_path is not None and Path(lut_path).is_file():
        lut_escaped = _escape_lut_filter_path(Path(lut_path))
        parts.append("scale=in_color_matrix=bt709:out_color_matrix=bt709,format=rgb24")
        parts.append(f"lut3d='{lut_escaped}'")

    if include_effects:
        parts.extend(_effect_filters(style_color, is_broll=is_broll, clip_window=clip_window))
    parts.append("scale=in_color_matrix=bt709:out_color_matrix=bt709,format=yuv420p")
    return ",".join(parts)


def _probe_with_vf(path_str: str, vf: str, start_sec: float, dur_sec: float) -> Optional[Tuple[float, float, float]]:
    """把整条链套到一小段素材上量输出分位（闭环校准用，代价很小：fps=2 + 64×114）。"""
    ff = find_bin("ffmpeg")
    src = Path(str(path_str))
    if not src.is_file() or not ff:
        return None
    cmd = [str(ff), "-v", "error"]
    if start_sec > 0:
        cmd += ["-ss", f"{start_sec:.3f}"]
    if dur_sec > 0:
        cmd += ["-t", f"{max(0.2, dur_sec):.3f}"]
    cmd += ["-i", str(src), "-vf", f"{vf},fps=2,scale=64:114",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout
    except Exception:
        return None
    frame_bytes = 64 * 114 * 3
    n = len(raw) // frame_bytes
    if n <= 0:
        return None
    arr = np.frombuffer(raw[: n * frame_bytes], dtype=np.uint8).reshape(n, 114, 64, 3).astype(np.float32) / 255.0
    luma = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
    return tuple(float(v) for v in np.percentile(luma, [5, 50, 95]))


def calibrate_clip_curve(
    mp: str,
    c_in: float,
    c_dur: float,
    corr: Dict[str, Any],
    style_color: Dict[str, Any],
    blended_lut: Optional[Path],
    eff_strength: float,
    is_broll: bool,
    clip_window: Optional[Tuple[float, float]] = None,
    max_iter: int = 5,
    tol: float = 0.018,
) -> Tuple[str, Optional[List[float]], Optional[List[float]]]:
    """闭环校准：先量前置链后分位，再反复「出片→量输出→回推目标」，直到输出分位贴住源片实测分位。

    返回 (最终 vf, 校准后目标(0–255), 最终残差(0–255))。校准失败（素材无亮度分布）时退回单轮。
    """
    pre_chain = f"eq=brightness={corr['brightness']}:contrast={corr['contrast']}:saturation={corr['saturation']}"
    pre_balance = _zone_balance_filter(style_color, corr)
    if pre_balance:
        pre_chain = f"{pre_chain},{pre_balance}"
    src_pct = probe_luma_percentiles(mp, c_in, c_dur, pre_vf=pre_chain)
    anchors = (style_color.get("tone_curve") or {}).get("anchors") or []
    base_targets = tuple(float(a.get("measured") or 0.0) / 255.0 for a in anchors) if len(anchors) == 3 else None

    def probe_vf(adj_targets, lift):
        # 校准只看「特效之前」的分布：源片实测分位取自未加暗角/聚光的画面
        return _build_vf_for_corr(corr, blended_lut, lut_strength=eff_strength, style_color=style_color,
                                  is_broll=is_broll, src_pct=src_pct, targets=adj_targets,
                                  black_lift=lift, include_effects=False)

    vf = _build_vf_for_corr(corr, blended_lut, lut_strength=eff_strength, style_color=style_color,
                            is_broll=is_broll, src_pct=src_pct, clip_window=clip_window)
    if src_pct is None or base_targets is None:
        return vf, None, None

    adj = list(base_targets)
    lift = 0.0
    last_got: Optional[Tuple[float, float, float]] = None
    for _ in range(max_iter):
        got = _probe_with_vf(mp, probe_vf(tuple(adj), lift), c_in, c_dur)
        if got is None:
            break
        last_got = got
        err = [base_targets[i] - got[i] for i in range(3)]
        if max(abs(e) for e in err) <= tol:
            break
        prev_adj = list(adj)
        adj = [max(0.0, min(1.0, adj[i] + err[i])) for i in range(3)]
        # 暗部卡在天花板还追不上 → 说明片源有死黑、目标暗部更亮，只能抬黑位
        if err[0] > tol and prev_adj[0] >= 0.985:
            lift = _clampf(lift + err[0] * 0.55, 0.0, 0.6)
    if last_got is None:
        return vf, None, None
    residual = [round((last_got[i] - base_targets[i]) * 255.0, 1) for i in range(3)]
    vf = _build_vf_for_corr(corr, blended_lut, lut_strength=eff_strength, style_color=style_color,
                            is_broll=is_broll, src_pct=src_pct, targets=tuple(adj),
                            black_lift=lift, clip_window=clip_window)
    return vf, [round(v * 255.0, 1) for v in adj], residual


def _retune_eq_saturation(vf: str, sat: float) -> str:
    """只改 eq 里的饱和度，其余链原样保留（饱和度不改变亮度，曲线标定结果依旧成立）。"""
    return re.sub(
        r"(eq=brightness=[-0-9.]+:contrast=[-0-9.]+:saturation=)[-0-9.]+",
        lambda m: f"{m.group(1)}{float(sat):.3f}",
        vf,
        count=1,
    )


def _skin_in_sector(skin: Optional[Dict[str, float]], margin: float = 0.0) -> bool:
    if not skin:
        return False
    return (
        SKIN_YCBCR_RANGE["cb_min"] + margin <= float(skin["skin_cb"]) <= SKIN_YCBCR_RANGE["cb_max"] - margin
        and SKIN_YCBCR_RANGE["cr_min"] + margin <= float(skin["skin_cr"]) <= SKIN_YCBCR_RANGE["cr_max"] - margin
    )


def _guard_probe_times(c_in: float, c_dur: float) -> List[float]:
    """肤色守门采样点：镜头的 25% / 50% / 75%，单点会漏掉镜内换光（实测 t=0~5s 就是这样漏的）。"""
    d = max(0.1, float(c_dur))
    return [round(float(c_in) + frac * d, 2) for frac in (0.25, 0.50, 0.75)]


def _probe_skin_series(media_path: Path, face_bbox: Dict[str, float], times: List[float], vf: str) -> List[Tuple[float, Dict[str, float]]]:
    out: List[Tuple[float, Dict[str, float]]] = []
    for at in times:
        s = probe_face_skin_ycbcr(media_path, face_bbox, at_sec=at, extra_vf=vf)
        if s:
            out.append((float(at), s))
    return out


def _skin_excess(skin: Dict[str, float]) -> float:
    """出扇区的「超限量」（0 = 健康），用于挑最差样本。"""
    cb, cr = float(skin["skin_cb"]), float(skin["skin_cr"])
    return max(
        0.0,
        SKIN_YCBCR_RANGE["cb_min"] - cb,
        cb - SKIN_YCBCR_RANGE["cb_max"],
        SKIN_YCBCR_RANGE["cr_min"] - cr,
        cr - SKIN_YCBCR_RANGE["cr_max"],
    )


def _all_in_sector(series: List[Tuple[float, Dict[str, float]]], margin: float = 0.0) -> bool:
    return bool(series) and all(_skin_in_sector(s, margin=margin) for _t, s in series)


def _worst_skin(series: List[Tuple[float, Dict[str, float]]]) -> Dict[str, Any]:
    if not series:
        return {}
    t_w, s_w = max(series, key=lambda ts: _skin_excess(ts[1]))
    return {
        "t": round(float(t_w), 2),
        "cb": s_w["skin_cb"],
        "cr": s_w["skin_cr"],
        "y": s_w["skin_y"],
        "mask_ratio": s_w.get("mask_ratio"),
        "excess": round(_skin_excess(s_w), 1),
    }


def run_step7(
    pipeline_dir: Path,
    track: str = "store",
    lut_name: Optional[str] = None,
    style_id: Optional[str] = None,
    lut_strength: Optional[float] = None,
) -> Dict[str, Any]:
    edl = read_json(pipeline_dir / "04_edl.json")
    manifest = read_json(pipeline_dir / "01_manifest.json") if (pipeline_dir / "01_manifest.json").exists() else {"items": []}

    if not style_id:
        style_id = edl.get("style_id") or load_style_applied(pipeline_dir)
    if not lut_name:
        lut_name = f"{style_id}.cube" if style_id else ("warm_store.cube" if track == "store" else "clean_creator.cube")
    lut_path = resolve_lut_path(lut_name, style_id=style_id)

    style_entry = load_style_spectrum_entry(style_id) if style_id else None
    style_color = _resolve_style_color(style_id)
    configured_strength = float(style_color.get("lut_strength") or 0.0)
    if lut_strength is None:
        if configured_strength > 0.0:
            lut_strength = max(0.0, min(1.0, configured_strength))
        else:
            cfg_str = (style_entry or {}).get("color_and_lut", {}).get("lut_strength")
            if cfg_str is not None:
                lut_strength = max(0.0, min(1.0, float(cfg_str)))
            else:
                lut_strength = 0.60

    blended_out_path = pipeline_dir / "07_blended_lut.cube"
    blended_lut, eff_strength = build_blended_cube_lut(lut_path, lut_strength, blended_out_path)

    manifest_by_mp4 = {it.get("normalized_mp4"): it for it in manifest.get("items", [])}
    clip_stats_cache: Dict[str, Dict[str, float]] = {}
    clip_skin_cache: Dict[str, Optional[Dict[str, float]]] = {}

    def _get_clip_stats(mp_s: str, s_in: float, s_dur: float) -> Dict[str, float]:
        ck = f"{mp_s}@{s_in:.2f}@{s_dur:.2f}"
        if ck not in clip_stats_cache:
            clip_stats_cache[ck] = probe_clip_color_stats(Path(mp_s), s_in, s_dur)
        return clip_stats_cache[ck]

    def _get_clip_skin(mp_s: str, fb_dict: Dict[str, float], at_s: float) -> Optional[Dict[str, float]]:
        sk = f"{mp_s}@{at_s:.2f}"
        if sk not in clip_skin_cache:
            clip_skin_cache[sk] = probe_face_skin_ycbcr(Path(mp_s), fb_dict, at_sec=at_s)
        return clip_skin_cache[sk]

    hero_clip = next((c for c in edl["clips"] if c["track_role"] == "a_roll"), None)
    if not hero_clip:
        hero_clip = next(
            (
                c for c in edl["clips"]
                if c["track_role"] != "code_broll"
                and not (manifest_by_mp4.get(c.get("media_path")) or {}).get("source_is_code_scene")
            ),
            None,
        )

    hero_mp = hero_clip["media_path"] if hero_clip else ""
    hero_in = float(hero_clip.get("source_in_sec", 0.0)) if hero_clip else 0.0
    hero_dur = float(hero_clip.get("duration_sec", 2.0)) if hero_clip else 2.0
    hero_stats = _get_clip_stats(hero_mp, hero_in, hero_dur) if hero_mp else {"yavg": 125.0, "uavg": 128.0, "vavg": 128.0}

    skin_ycbcr: Optional[Dict[str, float]] = None
    hero_face_bbox: Optional[Dict[str, float]] = None
    hero_at_sec = round(hero_in + min(1.5, hero_dur * 0.5), 2)
    if hero_mp and hero_mp in manifest_by_mp4:
        faces_px = parse_manifest_faces(manifest_by_mp4[hero_mp])
        if faces_px:
            hero_face_bbox = faces_px[0]
            skin_ycbcr = _get_clip_skin(hero_mp, hero_face_bbox, hero_at_sec)

    # 收集所有含人脸镜头的抽样检验点（不仅 Hero 1 帧，副镜头人脸一并纳入 LUT 肤色保护回测）
    face_verify_targets: List[Tuple[Path, Dict[str, float], float, Dict[str, float], bool]] = []
    if hero_mp and hero_face_bbox and skin_ycbcr:
        face_verify_targets.append((Path(hero_mp), hero_face_bbox, hero_at_sec, hero_stats, True))
    seen_verify_mps = {hero_mp}
    for c_tmp in edl["clips"]:
        mp_t = str(c_tmp.get("media_path") or "")
        if not mp_t or mp_t in seen_verify_mps or mp_t not in manifest_by_mp4:
            continue
        f_list = parse_manifest_faces(manifest_by_mp4[mp_t])
        if not f_list:
            continue
        seen_verify_mps.add(mp_t)
        c_in_t = float(c_tmp.get("source_in_sec", 0.0))
        c_dur_t = float(c_tmp.get("duration_sec", 2.0))
        at_t = round(c_in_t + min(1.5, c_dur_t * 0.5), 2)
        st_t = _get_clip_stats(mp_t, c_in_t, c_dur_t)
        face_verify_targets.append((Path(mp_t), f_list[0], at_t, st_t, False))
        if len(face_verify_targets) >= 4:
            break

    # 闭环片源强度回测：套用完整调色链（含特效）回测 Hero 及副镜头人脸肤色是否跌出健康扇区，仅沿严格不增强度阶梯下探
    skin_post_grade: Optional[Dict[str, float]] = None
    if face_verify_targets and eff_strength > 0.01:
        # 降强度阶梯必须设下限。2026-11-24 实测：style_21 真值 lut_strength=0.6，
        # 旧阶梯一路探到 0.15 只为把肤色拉回扇区 → 全片主亮 yavg 105（真值 140），
        # 整整暗 35 级、画面发灰，人眼看着「闷」；而真值本来就是「均衡自然高通透」。
        # 影调是这套风格的第一性，肤色偏移应该用中间调纠回来，不能靠把强度降到 1/4 来回避。
        raw_cands = [eff_strength, min(eff_strength, 0.55), min(eff_strength, LUT_STRENGTH_FLOOR)]
        cand_strengths: List[float] = []
        for cs in raw_cands:
            cs_r = round(float(cs), 2)
            if cs_r <= eff_strength + 1e-6 and cs_r not in cand_strengths:
                cand_strengths.append(cs_r)
        min_cand = min(cand_strengths) if cand_strengths else 0.15
        for cand_str in cand_strengths:
            if abs(cand_str - eff_strength) < 1e-6 and blended_lut.is_file():
                cand_lut, cand_eff = blended_lut, eff_strength
            else:
                cand_lut, cand_eff = build_blended_cube_lut(lut_path, cand_str, blended_out_path)
            blended_lut, eff_strength = cand_lut, cand_eff
            all_in_sector = True
            for vp, v_fb, v_at, v_st, v_is_hero in face_verify_targets:
                v_skin = skin_ycbcr if v_is_hero else _get_clip_skin(str(vp), v_fb, v_at)
                v_corr = compute_hero_and_shot_correction(v_st, hero_stats, v_is_hero, v_skin, style_id=style_id)
                test_vf = _build_vf_for_corr(v_corr, cand_lut, lut_strength=cand_eff, style_color=style_color, is_broll=False)
                pg = probe_face_skin_ycbcr(vp, v_fb, at_sec=v_at, extra_vf=test_vf)
                if v_is_hero:
                    skin_post_grade = pg
                if pg and not (
                    SKIN_YCBCR_RANGE["cb_min"] <= pg["skin_cb"] <= SKIN_YCBCR_RANGE["cb_max"]
                    and SKIN_YCBCR_RANGE["cr_min"] <= pg["skin_cr"] <= SKIN_YCBCR_RANGE["cr_max"]
                ):
                    all_in_sector = False
                    break
            if all_in_sector or cand_str <= min_cand:
                break

    clip_plans: List[Dict[str, Any]] = []

    for clip in edl["clips"]:
        mp = clip["media_path"]
        is_code = (clip["track_role"] == "code_broll") or bool((manifest_by_mp4.get(mp) or {}).get("source_is_code_scene"))
        if is_code:
            clip_plans.append({
                "clip_id": clip["clip_id"],
                "track_role": clip["track_role"],
                "apply_color_grade": False,
                "reason": "代码生成UI图表保留原色",
                "vf_filter": None,
            })
            continue

        c_in = float(clip.get("source_in_sec", 0.0))
        c_dur = float(clip.get("duration_sec", 2.0))
        stats = _get_clip_stats(mp, c_in, c_dur)
        is_hero = (mp == hero_mp and abs(c_in - hero_in) < 0.5)

        shot_skin: Optional[Dict[str, float]] = skin_ycbcr if is_hero else None
        if not is_hero and mp in manifest_by_mp4:
            clip_faces = parse_manifest_faces(manifest_by_mp4[mp])
            if clip_faces:
                c_at = round(c_in + min(1.5, c_dur * 0.5), 2)
                shot_skin = _get_clip_skin(mp, clip_faces[0], c_at)

        corr = compute_hero_and_shot_correction(
            stats,
            hero_stats=hero_stats,
            is_hero=is_hero,
            skin_ycbcr=shot_skin,
            style_id=style_id,
        )
        is_broll = str(clip.get("track_role") or "") == "b_roll"
        clip_win = (float(clip.get("timeline_in_sec") or 0.0),
                    float(clip.get("timeline_in_sec") or 0.0) + c_dur)
        vf_str, cal_targets, cal_residual = calibrate_clip_curve(
            mp, c_in, c_dur, corr, style_color, blended_lut, eff_strength, is_broll,
            clip_window=clip_win,
        )
        # 肤色扇区闭环守门（用户实测 2026-11-24）：本片 cr 从素材 167 被调色链推到 176~180，
        # 根因是 tone_curve 的提亮（逐通道凸曲线把暖肤的 R−B 差距同步放大），colorbalance 几乎拉不动，
        # 能真正回拉的只有色彩饱和度。因此逐镜量一次「上了整条链之后的真实肤色」，出扇区就沿
        # 1.0/0.95/0.90/0.85 阶梯降本镜饱和度（只降不升，不碰亮度与曲线），每个镜头实际用了哪一档写进 07_color_plan.json。
        skin_guard: Optional[Dict[str, Any]] = None
        guard_faces = parse_manifest_faces(manifest_by_mp4[mp]) if mp in manifest_by_mp4 else []
        if guard_faces:
            g_times = _guard_probe_times(c_in, c_dur)
            g_pre = _probe_skin_series(Path(mp), guard_faces[0], g_times, vf_str)
            if g_pre:
                applied_scale, applied_sat = 1.0, float(corr["saturation"])
                chosen = g_pre
                best_effort = False
                if not _all_in_sector(g_pre):
                    last: Optional[Tuple[float, float, str, List[Tuple[float, Dict[str, float]]]]] = None
                    for g_scale in (0.95, 0.90, 0.85, 0.80):
                        g_sat = round(float(corr["saturation"]) * g_scale, 3)
                        g_vf = _retune_eq_saturation(vf_str, g_sat)
                        g_post = _probe_skin_series(Path(mp), guard_faces[0], g_times, g_vf)
                        if not g_post:
                            continue
                        chosen = g_post
                        last = (g_scale, g_sat, g_vf, g_post)
                        if _all_in_sector(g_post):
                            corr = dict(corr)
                            corr["saturation"] = g_sat
                            vf_str = g_vf
                            applied_scale, applied_sat = g_scale, g_sat
                            break
                    else:
                        # 阶梯走完还出不来的（实测 178 → 最低档 172.7），也要用上最低那档：
                        # 旧写法在这一步什么都不改，于是「明明能改善 5 个单位」的白等一场，
                        # 成片照样 178 出扇区。这里如实记 best_effort。
                        if last is not None:
                            g_scale, g_sat, g_vf, g_post = last
                            corr = dict(corr)
                            corr["saturation"] = g_sat
                            vf_str = g_vf
                            applied_scale, applied_sat = g_scale, g_sat
                            chosen = g_post
                            best_effort = True
                worst = _worst_skin(chosen)
                skin_guard = {
                    "probe_sec": g_times,
                    "pre_worst": _worst_skin(g_pre),
                    "post_worst": worst,
                    "samples": [
                        {"t": t, "cb": s["skin_cb"], "cr": s["skin_cr"], "y": s["skin_y"], "mask_ratio": s["mask_ratio"]}
                        for t, s in chosen
                    ],
                    "saturation_scale": applied_scale,
                    "saturation_applied": applied_sat,
                    "applied": bool(applied_scale < 1.0),
                    "in_sector_after": _all_in_sector(chosen),
                    "best_effort": best_effort,
                    "source_limited": bool(float(worst.get("y") or 0.0) < 45.0),
                }
        clip_plans.append({
            "clip_id": clip["clip_id"],
            "track_role": clip["track_role"],
            "is_hero_ref": is_hero,
            "apply_color_grade": True,
            "measured_stats": stats,
            "base_correction": corr,
            "grade_strength": {
                "lut": round(float(eff_strength), 2),
                "contrast": corr["contrast"],
                "saturation": corr["saturation"],
            },
            "skin_guard": skin_guard,
            "tone_curve_weight": float((style_color.get("tone_curve") or {}).get("curve_weight") or 0.0),
            "curve_calibration": {
                "targets_255": cal_targets,
                "residual_255": cal_residual,
                "converged": bool(cal_residual and max(abs(v) for v in cal_residual) <= 6.0),
                "method": "闭环：出片→量特效前 p05/p50/p95→按残差回推曲线目标（暗部卡顶时抬黑位），最多 5 轮",
            } if cal_targets else None,
            "effects_applied": _applied_effects(style_color, is_broll, clip_win),
            "lut_file": str(blended_lut.resolve()) if eff_strength > 0.01 else None,
            "vf_filter": vf_str,
        })

    plan = {
        "step": "07_color_grade",
        "style_id": style_id,
        "lut_name": lut_path.name,
        "source_lut_path": str(lut_path.resolve()),
        "lut_strength": round(eff_strength, 2),
        "lut_strength_configured": round(float(lut_strength), 2),
        "lut_path": str(blended_lut.resolve()),
        "hero_media": hero_mp,
        "hero_stats": hero_stats,
        "skin_tone_measured": skin_ycbcr,
        "skin_tone_post_grade": skin_post_grade,
        "color_effects": {
            "config": "assets/style_spectrum/color_effects.json",
            "saturation_scale": style_color.get("saturation_scale"),
            "contrast": style_color.get("contrast"),
            "warmth": style_color.get("warmth"),
            "tone_curve": style_color.get("tone_curve"),
            "zone_tint": style_color.get("zone_tint"),
            "effects_enabled": _effects_enabled(style_color),
            "effects_evidence_cn": (style_color.get("effects") or {}).get("evidence_cn"),
            "basis_cn": style_color.get("basis_cn"),
        },
        "clip_plans": clip_plans,
    }
    write_json(pipeline_dir / "07_color_plan.json", plan)
    graded_cnt = sum(1 for c in clip_plans if c["apply_color_grade"])
    effects_cn = "、".join(_effects_enabled(style_color)) or "无"
    print(
        f"[step7] 肤色保护调色方案生成完成：{graded_cnt} 个实拍镜头（Hero锚定 + 强度{eff_strength:.0%}混合 `{lut_path.name}` "
        f"+ 三段曲线/分色 + 特效：{effects_cn}）-> 07_color_plan.json"
    )
    return plan


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 7: 逐镜三段曲线校色 + 肤色保护 + 库外特效 + 逐套片源强度规划")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--style-id", help="绑定风格谱 ID")
    parser.add_argument("--lut", help="LUT 文件名")
    parser.add_argument("--lut-strength", type=float, default=None, help="手动覆盖片源强度 0.0~1.0（默认取 color_effects.json 的逐套值）")
    args = parser.parse_args()
    run_step7(
        Path(args.pipeline_dir),
        track=args.track,
        lut_name=args.lut,
        style_id=args.style_id,
        lut_strength=args.lut_strength,
    )


if __name__ == "__main__":
    main()
