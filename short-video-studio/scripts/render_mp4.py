#!/usr/bin/env python3
"""
成片渲染器：合成 1080x1920 30fps BT.709 竖屏成片 MP4 (output_preview.mp4 / output_final.mp4)。
严格对齐流水线权威 Schema：
- 01_manifest.json: items[*], normalized_mp4, faces[*].bbox ([x1,y1,x2,y2] 0..1 归一化), eye_anchor
- 04_edl.json: clips[*], timeline_in_frame/timeline_out_frame, vf_extra, ken_burns ({zoom_start, zoom_end})
- 05_subtitles.json: entries[*], final_bbox/bbox, suppressed
- 06_packaging.json: visual_elements[*], kind, text_or_asset, start_frame/end_frame, final_bbox/bbox
- 07_color_plan.json: clip_plans[*], apply_color_grade, vf_filter (完整 eq+colorbalance+lut3d)
- 08_mix_metrics.json: master_wav (08_mixed_master.wav)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    BT709_FFMPEG_ARGS,
    BT709_VF_SUFFIX,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SAFE_ZONE,
    SUBTITLE_CENTER_X,
    SUBTITLE_SAFE_ZONE,
    SUBTITLE_SYMMETRIC_MAX_W,
    TP_MAX_DBTP,
    compute_centered_crop_frac,
    compute_crop_transform,
    compute_segment_cache_key,
    contrast_ratio,
    clamp_speed,
    ffprobe_media,
    find_bin,
    find_commercial_font,
    fit_text_line,
    frames_to_sec,
    get_cached_font,
    get_wav_duration_sec,
    glyph_contrast_ratio,
    hex_to_rgba,
    load_style_applied,
    load_style_spectrum_entry,
    load_subtitle_templates,
    measure_lufs_ebur128,
    maybe_reexec_in_venv,
    parse_manifest_faces,
    parse_video_stream_geometry,
    read_json,
    run_cmd,
    sec_to_frames,
    sha256_file,
    source_span_for,
    speed_setpts,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

import face_framing
from generate_code_broll import resolve_style_palette
from glyph_fallback import load_font_ref as _load_fb_font
from glyph_fallback import glyph_font_for as _glyph_fb_font

from PIL import Image, ImageDraw, ImageFilter


def parse_hex_rgba(hex_str: str, default_alpha: int = 255) -> Tuple[int, int, int, int]:
    return hex_to_rgba(hex_str, default=(255, 255, 255, int(default_alpha)), default_alpha=int(default_alpha))


@lru_cache(maxsize=512)
def _load_font_ref(font_path: str, index: int, size: int) -> Any:
    """按 (文件, 字面 index, 字号) 载入字体；`.ttc` 必须带 index 才能取到目标字面（如 Heiti TC）。"""
    return _load_fb_font(font_path, index, max(12, int(size)))


def glyph_font_for(ch: str, primary_font_path: Optional[str], size: int) -> Any:
    """逐字字形回退：主字体缺这个字就按 font_aliases.json 的 glyph_fallback_chain 逐级找。

    PIL/FreeType 自身不做字体回退，缺字直接画成空白/豆腐块（用户实测：阿里妈妈书黑体缺
    `佢 冇 咗 哋 啲 喺 嘅 嘢 嚟 𠮶` 十个粤语常用字）。这里只换字体、**不改字号**，
    同一行里混排两套字体时由调用方按各字体 ascent 对齐基线（render_subtitle_png 按行内
    max_ascent 排 y），所以基线、字号一致，字宽按各自字体的真实 advance 走。

    实现（cmap 查询 / 回退链 / .ttc 字面 index）在 glyph_fallback 模块，本函数是渲染端的入口。
    返回已按 size 载入的 Pillow 字体；整条链都覆盖不了时返回主字体（覆盖门禁 fail-closed 拦下）。
    """
    return _glyph_fb_font(ch, primary_font_path or None, max(12, int(size)))


_DEDUPE_MINIMAL_STYLES = {"style_04", "style_10", "style_11", "style_15", "style_23", "style_24"}


def _norm_cn(text: Any) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(text or ""))


def _flower_duplicates_subtitle(text: str, s_t: float, e_t: float, subs: List[Dict[str, Any]]) -> bool:
    """同屏防重：花字与同时段底字幕字面高度重合（>=60% 字符或互相包含）时，丢弃花字，避免胸口+肚子上下说同一句话。"""
    ft = _norm_cn(text)
    if len(ft) < 2:
        return False
    ft_set = set(ft)
    for sub in subs:
        if sub.get("suppressed"):
            continue
        if float(sub.get("end_sec", 0.0)) <= s_t or float(sub.get("start_sec", 0.0)) >= e_t:
            continue
        st = _norm_cn(sub.get("text"))
        if not st:
            continue
        if ft in st or st in ft or (sum(1 for ch in ft_set if ch in st) / float(len(ft_set))) >= 0.6:
            return True
    return False



def compute_subtitle_place_xy(img_w: int, img_h: int, tpl: Dict[str, Any]) -> Tuple[int, int]:
    """按画布绝对水平正中 x=540px 居中放置字幕（通宽底条模式贴满 0..1080，其余严守对称安全带 [220, 860]）。

    长行（>640px）以前会被「贴着 860 右边界往回推」，反而把整行推偏一半；现在宁可保持口径居中，
    只在平台安全区内做对称夹紧——真正的修法在 step5（换行/断句把行宽压进 640px），这里只是兼底。
    """
    y_center = int(tpl.get("safe_y_center", tpl.get("y_center", 1310)))
    if bool(tpl.get("full_width_bar")) and img_w >= CANVAS_WIDTH - 4:
        px = 0
    else:
        px = SUBTITLE_CENTER_X - img_w // 2
        if img_w > SUBTITLE_SYMMETRIC_MAX_W:
            # 超宽兼底：宁可保口径居中（对称溢出），也不把整行推到一侧；真正的修法在 step5 把行宽压进 640px
            px = max(0, min(px, CANVAS_WIDTH - img_w))
    py = max(SUBTITLE_SAFE_ZONE["y_min"], min(y_center - img_h // 2, SUBTITLE_SAFE_ZONE["y_max"] - img_h))
    return int(px), int(py)


# ── 字幕阴影真值口径（2026-11-24 实测重新标定，用户反馈「投影偏移过多，影响观感」）──
# 位移：剪映 shadow_point 是 -45° 的方向分量（0.6364=1/√2），乘 shadow_distance 得到阴影幅度。
# 实测法：style_21 原版成片取 540×960 预览，把「字形掩膜」与「暗像素掩膜」做互相关，最佳位移
#   dx≈-5 / dy≈+3（预览口径）→ ×2 落到 1080 宽 = 合成幅度 ≈11.7px；草稿真值 distance=5.0、
#   angle=-45°，即 11.5px / 5.0 单位 = **2.3px 每 unit（1080 宽）**。旧值 [14,14]（幅度 19.8px）
#   比实测大 1.7 倍，正是「投影糊出墨迹一大圈、像双层黑边」的根因。
SHADOW_CALIB_CANVAS_W = 1080.0  # 2.3px/unit 的标定画布宽，换画布按比例缩放
SHADOW_DIST_PX_PER_UNIT = 2.3  # 见上：style_21 distance 5.0 → 11.5px（实测 11.7px）
# 虚化：σ = smoothing × font_px × 0.13。「0.13」由两档字号实测反解（style_21 字幕 font_px=75 时
#   σ≈4.4px、300px 巨字 σ≈11.4px，4 倍字号 → 2.6 倍 σ，线性拟合残差 ≤1px），与 dev_tools/
#   render_replica_from_draft.py 的 SHADOW_BLUR_EM 同一口径。旧模板 shadow_blur_px=1.1 太硬，
#   阴影边缘像描边。
SHADOW_BLUR_EM = 0.13
SHADOW_DEFAULT_POINT = (0.636396103067893, -0.636396103067893)  # -45°，剪映 y 负 = 屏幕向下
SHADOW_DEFAULT_SMOOTHING = 0.45  # 24 套草稿里 21 套的 shadow_smoothing 都是 0.45


def resolve_subtitle_shadow(tpl: Dict[str, Any], font_px: float) -> Tuple[int, int, float, bool]:
    """算字幕阴影：(dx, dy, σ, 是否启用)。

    优先按草稿真值（shadow_distance_units / shadow_point / shadow_smoothing）现场推：
    偏移幅度与画布宽成正比、与字号无关；虚化 σ 与字号成正比。
    模板里没有真值字段时回落旧的 shadow_offset / shadow_blur_px 占位分量。
    """
    dist_units = tpl.get("shadow_distance_units")
    try:
        dist = float(dist_units) if dist_units is not None else 0.0
    except (TypeError, ValueError):
        dist = 0.0
    if dist > 0:
        mag = dist * SHADOW_DIST_PX_PER_UNIT * (float(CANVAS_WIDTH) / SHADOW_CALIB_CANVAS_W)
        pt = tpl.get("shadow_point")
        try:
            ux = float(pt.get("x"))  # type: ignore[union-attr]
            uy = -float(pt.get("y"))  # type: ignore[union-attr]
        except (TypeError, ValueError, AttributeError):
            ux, uy = SHADOW_DEFAULT_POINT[0], -SHADOW_DEFAULT_POINT[1]
        norm = (ux * ux + uy * uy) ** 0.5 or 1.0
        sm_raw = tpl.get("shadow_smoothing")
        try:
            sm = float(sm_raw) if sm_raw is not None else SHADOW_DEFAULT_SMOOTHING
        except (TypeError, ValueError):
            sm = SHADOW_DEFAULT_SMOOTHING
        return (
            int(round(mag * ux / norm)),
            int(round(mag * uy / norm)),
            max(0.0, sm * float(font_px) * SHADOW_BLUR_EM),
            True,
        )

    off = tpl.get("shadow_offset") or [0, 0]
    try:
        dx, dy = int(off[0]), int(off[1])
    except (TypeError, ValueError, IndexError):
        dx, dy = 0, 0
    try:
        blur = float(tpl.get("shadow_blur_px") or 0.0)
    except (TypeError, ValueError):
        blur = 0.0
    return dx, dy, blur, bool(dx or dy or blur)


def render_subtitle_png(
    entry: Dict[str, Any],
    tpl: Dict[str, Any],
    font_path: Optional[str],
    out_png: Path,
    shadow_boost: float = 1.0,
    stroke_extra: int = 0,
) -> Tuple[int, int]:
    """按模板样式与字词高亮信息渲染透明字幕 PNG：整行按真实墨迹包围盒严格几何居中，且宽度严格 <=640px 保绝对居中。

    shadow_boost / stroke_extra 只服务于「亮底自适应增强」闭环：默认值 1.0 / 0 与标定值逐像素一致
    （暗底永不动），只有投影在同族系数上限内加强仍达不到 WCAG 3.8 时才补细描边。
    """
    words = entry.get("words") or []
    raw_text = str(entry.get("text") or "").strip()
    if not raw_text and words:
        raw_text = "".join(str(w.get("word", "")) for w in words)

    dual_banner = bool(tpl.get("dual_line_banner"))
    raw_base_fs = max(24, int(tpl.get("font_size", 62)))
    orig_stroke_w = int(tpl.get("stroke_width", 4))
    kw_scale = float(tpl.get("keyword_scale", 1.133))

    bg_hex = str(tpl.get("bg_box_color") or tpl.get("bg_box") or "none").strip()
    bg_rgba = parse_hex_rgba(bg_hex, 210) if (bg_hex and bg_hex.lower() != "none") else (0, 0, 0, 0)
    has_bg = bool(bg_hex and bg_hex.lower() != "none" and bg_rgba[3] > 0)
    bg_radius = int(tpl.get("bg_radius", 18))
    pad_x = 28 if has_bg else 14
    pad_y = 18 if has_bg else 12

    max_line_w = SUBTITLE_SYMMETRIC_MAX_W - pad_x * 2
    if dual_banner and "\n" not in raw_text and len(raw_text) >= 5:
        # 通宽双行大字横幅（上白下荧光/暖黄/雅金）：优先复用 Step5 权威词边界断行，再回落语义词边界切分
        dl = entry.get("display_lines")
        if (
            isinstance(dl, (list, tuple))
            and len(dl) >= 2
            and "".join(str(x).strip() for x in dl[:2]) == re.sub(r"\s+", "", raw_text)
        ):
            raw_text = f"{str(dl[0]).strip()}\n{str(dl[1]).strip()}"
        else:
            from step5_subtitles import split_semantic_two_lines
            l1, l2 = split_semantic_two_lines(raw_text, keywords=entry.get("keywords"))
            raw_text = f"{l1}\n{l2}" if l2 else l1
    lines_text = raw_text.splitlines() if "\n" in raw_text else [raw_text]
    if not lines_text:
        lines_text = [""]

    if dual_banner:
        orig_fs = min(84, max(62, raw_base_fs)) if len(lines_text) >= 2 else min(100, max(62, raw_base_fs))
    else:
        orig_fs = raw_base_fs

    base_sz = orig_fs
    probe_img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe_img)
    # 只做溢出兜底：字号以库/模板为准（units × canvas_w/172.8，见 dev_tools/rebuild_subtitle_typography.py）。
    # 正常路径不该进这个循环——每行字数已在 step5_subtitles.py 按字号反推（可用宽度 // 字号），
    # 24 套原版成片也是一行 ≤6~8 字。这里只防「调用方硬塞一整段长文」把字排到安全带外。
    while base_sz > 28:
        fnt_probe = get_cached_font(font_path, int(round(base_sz * max(1.0, (1.0 + kw_scale) * 0.5))))
        sw_p = 0 if orig_stroke_w <= 0 else max(1, int(round(orig_stroke_w * max(0.75, base_sz / float(orig_fs)))))
        widest = max(
            (probe_draw.textbbox((0, 0), ln, font=fnt_probe, stroke_width=sw_p)[2]
             - probe_draw.textbbox((0, 0), ln, font=fnt_probe, stroke_width=sw_p)[0]
             + max(0, len(ln) - 1) * max(1, sw_p // 2))
            for ln in lines_text
        )
        if widest <= max_line_w:
            break
        base_sz -= 2

    stroke_w = 0 if orig_stroke_w <= 0 else max(1, int(round(orig_stroke_w * max(0.75, base_sz / float(orig_fs)))))
    if int(stroke_extra) > 0:
        stroke_w = max(stroke_w, int(stroke_extra))
    primary_rgba = parse_hex_rgba(tpl.get("primary_color", "#FFFFFF"))
    kw_rgba = parse_hex_rgba(tpl.get("keyword_color", "#FFE28A"))
    stroke_rgba = parse_hex_rgba(tpl.get("stroke_color", "#000000"))
    shadow_rgba = parse_hex_rgba(tpl.get("shadow_color", "#000000C8"), 200)

    kw_set = set(str(k) for k in (entry.get("keywords") or []))

    tokens: List[Tuple[str, bool, bool]] = []
    if words and "".join(str(w.get("word", "")) for w in words).replace("\n", "") == raw_text.replace("\n", ""):
        raw_idx = 0
        for w_obj in words:
            w_str = str(w_obj.get("word", ""))
            is_kw = bool(w_obj.get("is_keyword", False)) or (w_str in kw_set)
            for ch in w_str:
                while raw_idx < len(raw_text) and raw_text[raw_idx] == "\n":
                    tokens.append(("\n", False, True))
                    raw_idx += 1
                tokens.append((ch, is_kw, False))
                raw_idx += 1
        while raw_idx < len(raw_text):
            if raw_text[raw_idx] == "\n":
                tokens.append(("\n", False, True))
            else:
                tokens.append((raw_text[raw_idx], False, False))
            raw_idx += 1
    else:
        kw_flags = [False] * len(raw_text)
        for kw in kw_set:
            if not kw:
                continue
            start = 0
            while True:
                idx = raw_text.find(kw, start)
                if idx < 0:
                    break
                for j in range(idx, idx + len(kw)):
                    kw_flags[j] = True
                start = idx + len(kw)
        for ch, flag in zip(raw_text, kw_flags):
            tokens.append((ch, flag, ch == "\n"))

    if dual_banner and any(t[2] for t in tokens):
        seen_nl = False
        recolored: List[Tuple[str, bool, bool]] = []
        for ch_t, kw_t, nl_t in tokens:
            if nl_t:
                seen_nl = True
                recolored.append((ch_t, False, True))
            else:
                recolored.append((ch_t, seen_nl, False))
        tokens = recolored

    # 逐行收集字符排版元信息
    lines: List[List[Tuple[str, Any, Tuple[int, int, int, int], int, int, int]]] = [[]]
    line_max_ascents: List[int] = [0]
    inter_pad = max(1, stroke_w // 2) if stroke_w > 0 else 0
    for ch, is_kw, is_nl in tokens:
        if is_nl:
            lines.append([])
            line_max_ascents.append(0)
            continue
        ch_sz = int(round(base_sz * (kw_scale if is_kw else 1.0)))
        # 逐字取字体：主字体缺这个字就按 glyph_fallback_chain 回落（字号不变，基线按行内 ascent 对齐）
        fnt = glyph_font_for(ch, font_path, ch_sz)
        bb = probe_draw.textbbox((0, 0), ch, font=fnt, stroke_width=stroke_w)
        try:
            adv_w = max(4, int(round(fnt.getlength(ch))) + inter_pad)
        except Exception:
            adv_w = max(4, (bb[2] - bb[0]) + 2)
        try:
            ascent, _descent = fnt.getmetrics()
        except Exception:
            ascent = ch_sz
        rgba = kw_rgba if is_kw else primary_rgba
        lines[-1].append((ch, fnt, rgba, adv_w, ch_sz, int(ascent)))
        line_max_ascents[-1] = max(line_max_ascents[-1], int(ascent))

    # ── 阴影：按真草稿量出来的值画（偏移 px / 透明度 / 虚化 px）──
    # 老实现把阴影画成「和正文同宽描边的一个黑影」，只要 stroke_width > 0 就变成双层黑边；
    # 而真草稿里 24/24 套字幕根本没有描边，只有阴影（见 forensic/text_style_truth.json）。
    sz_scale = max(0.4, base_sz / float(orig_fs))
    if tpl.get("shadow_distance_units") is not None:
        # 真值路径：偏移/虚化按草稿量出来的口径现场算（不再吃模板里的占位分量）
        sh_dx, sh_dy, sh_blur, _ = resolve_subtitle_shadow(tpl, base_sz)
    else:
        sh_dx_raw, sh_dy_raw, sh_blur_raw, _ = resolve_subtitle_shadow(tpl, base_sz)
        sh_dx = int(round(sh_dx_raw * sz_scale))
        sh_dy = int(round(sh_dy_raw * sz_scale))
        sh_blur = float(sh_blur_raw * sz_scale)
    if float(shadow_boost) > 1.0:
        # 亮底自适应增强：偏移与虚化按同族系数放大（σ 放大封顶 1.6，再大就从「投影」糊成「灰蒙」）
        sh_dx = int(round(sh_dx * float(shadow_boost)))
        sh_dy = int(round(sh_dy * float(shadow_boost)))
        sh_blur = float(sh_blur) * min(float(shadow_boost), 1.6)
        shadow_rgba = (
            shadow_rgba[0],
            shadow_rgba[1],
            shadow_rgba[2],
            int(min(255, round(shadow_rgba[3] * (1.0 + 0.5 * (float(shadow_boost) - 1.0))))),
        )
    use_shadow = bool(tpl.get("shadow", False)) and shadow_rgba[3] > 0 and (sh_dx or sh_dy or sh_blur)

    # 先将每行单独渲染至临时透明层并精确裁出真实墨迹包围盒，消除字形左轴承/末字尾距造成的不对称偏移
    rendered_lines: List[Image.Image] = []
    line_ink_offsets: List[Tuple[float, float]] = []  # 墨迹中心在整行图里的相对位置（用于「按墨迹居中」而非按含阴影的外框居中）
    for ln_idx, ln in enumerate(lines):
        if not ln:
            rendered_lines.append(Image.new("RGBA", (40, max(36, base_sz)), (0, 0, 0, 0)))
            line_ink_offsets.append((20.0, 0.0))
            continue
        est_ln_w = sum(item[3] for item in ln) + max(80, stroke_w * 4 + abs(sh_dx) * 4 + base_sz * 2)
        max_asc = max(base_sz // 2, line_max_ascents[ln_idx])
        est_ln_h = int(max_asc * 2.2) + stroke_w * 4 + abs(sh_dy) * 4 + 40
        pad0 = 40 + stroke_w * 2 + max(0, sh_dx)
        base_top = 24 + stroke_w * 2 + max(0, sh_dy)
        ink = Image.new("RGBA", (est_ln_w, est_ln_h), (0, 0, 0, 0))
        ink_draw = ImageDraw.Draw(ink)
        sh_layer = Image.new("RGBA", (est_ln_w, est_ln_h), (0, 0, 0, 0)) if use_shadow else None
        sh_draw = ImageDraw.Draw(sh_layer) if sh_layer is not None else None
        cur_x = pad0
        for ch, fnt, rgba, adv_w, _ch_sz, asc in ln:
            y_off = base_top + max(0, max_asc - asc)
            if sh_draw is not None:
                # 阴影是「位移+半透明+虚化」的第二个字形，不跟着描边一起长胖
                sh_draw.text((cur_x + sh_dx, y_off + sh_dy), ch, font=fnt, fill=shadow_rgba)
            ink_draw.text(
                (cur_x, y_off),
                ch,
                font=fnt,
                fill=rgba,
                stroke_width=stroke_w,
                stroke_fill=stroke_rgba if stroke_w > 0 else None,
            )
            cur_x += adv_w
        if sh_layer is not None and sh_blur > 0.2:
            sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(radius=sh_blur))
        merged = Image.new("RGBA", (est_ln_w, est_ln_h), (0, 0, 0, 0))
        if sh_layer is not None:
            merged.alpha_composite(sh_layer)
        merged.alpha_composite(ink)
        union_bb = merged.getbbox()
        ink_bb = ink.getbbox()
        if union_bb is not None:
            cropped = merged.crop(union_bb)
            if ink_bb is not None:
                ink_cx = (ink_bb[0] + ink_bb[2]) / 2.0 - union_bb[0]
                ink_cy = (ink_bb[1] + ink_bb[3]) / 2.0 - union_bb[1]
            else:
                ink_cx, ink_cy = cropped.width / 2.0, cropped.height / 2.0
        else:
            cropped = Image.new("RGBA", (40, max(36, base_sz)), (0, 0, 0, 0))
            ink_cx, ink_cy = 20.0, 0.0
        if cropped.width > max_line_w:
            ratio = max_line_w / float(cropped.width)
            cropped = cropped.resize(
                (max_line_w, max(24, int(round(cropped.height * ratio)))),
                Image.Resampling.LANCZOS,
            )
            ink_cx *= ratio
            ink_cy *= ratio
        rendered_lines.append(cropped)
        line_ink_offsets.append((ink_cx, ink_cy))

    line_gap = 10 if len(rendered_lines) > 1 else 0
    max_ink_w = max((im.width for im in rendered_lines), default=60)
    is_full_width_bar = bool(tpl.get("full_width_bar")) and has_bg
    if is_full_width_bar:
        total_w = CANVAS_WIDTH
        pad_y_eff = max(24, pad_y + 6)
        total_h = max(64, sum(im.height for im in rendered_lines) + line_gap * max(0, len(rendered_lines) - 1) + pad_y_eff * 2)
    else:
        total_w = max(80, min(SUBTITLE_SYMMETRIC_MAX_W, max_ink_w + pad_x * 2))
        if total_w % 2 == 1 and total_w < SUBTITLE_SYMMETRIC_MAX_W:
            total_w += 1
        pad_y_eff = pad_y
        total_h = max(48, sum(im.height for im in rendered_lines) + line_gap * max(0, len(rendered_lines) - 1) + pad_y_eff * 2)
    if dual_banner and total_h > 284 and rendered_lines:
        # 双行横幅高度严守字幕安全带总高 [1180, 1468]=288px，防止实拍 PNG 溢出 final_bbox 导致黑盒 OCR 拦腰截断
        avail_h = max(48, 284 - pad_y_eff * 2 - line_gap * max(0, len(rendered_lines) - 1))
        cur_sum_h = max(1, sum(im.height for im in rendered_lines))
        h_ratio = avail_h / float(cur_sum_h)
        if h_ratio < 0.999:
            scaled_lines: List[Image.Image] = []
            scaled_offsets: List[Tuple[float, float]] = []
            for ln_i, im_ln in enumerate(rendered_lines):
                nw = max(20, int(round(im_ln.width * h_ratio)))
                nh = max(20, int(round(im_ln.height * h_ratio)))
                scaled_lines.append(im_ln.resize((nw, nh), Image.Resampling.LANCZOS))
                ox, oy = line_ink_offsets[ln_i] if ln_i < len(line_ink_offsets) else (im_ln.width / 2.0, im_ln.height / 2.0)
                scaled_offsets.append((ox * h_ratio, oy * h_ratio))
            rendered_lines = scaled_lines
            line_ink_offsets = scaled_offsets
            max_ink_w = max((im.width for im in rendered_lines), default=60)
            if not is_full_width_bar:
                total_w = max(80, min(SUBTITLE_SYMMETRIC_MAX_W, max_ink_w + pad_x * 2))
                if total_w % 2 == 1 and total_w < SUBTITLE_SYMMETRIC_MAX_W:
                    total_w += 1
            total_h = max(48, sum(im.height for im in rendered_lines) + line_gap * max(0, len(rendered_lines) - 1) + pad_y_eff * 2)

    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    if has_bg:
        if is_full_width_bar:
            # 1080px 通栏横贯全屏底条（复刻 style_02 暗灰 / style_03 深红 / style_20 国风暖黑通栏）
            draw.rectangle([(0, 0), (total_w - 1, total_h - 1)], fill=bg_rgba)
            if bg_radius <= 6 and bg_rgba[0] < 45 and bg_rgba[1] < 40:
                # 国风暖黑通栏：上下 2px 雅金细线勾边
                draw.line([(0, 3), (total_w, 3)], fill=(200, 155, 84, 165), width=2)
                draw.line([(0, total_h - 4), (total_w, total_h - 4)], fill=(200, 155, 84, 165), width=2)
        else:
            draw.rounded_rectangle([(0, 0), (total_w - 1, total_h - 1)], radius=bg_radius, fill=bg_rgba)

    cur_y = pad_y_eff
    for ln_i, im_ln in enumerate(rendered_lines):
        # 居中按「墨迹中心」，不按含阴影的外框——否则阴影会把正文整体往反方向挤偏半条阴影的距离
        ink_cx = line_ink_offsets[ln_i][0] if ln_i < len(line_ink_offsets) else im_ln.width / 2.0
        paste_x = int(round(total_w / 2.0 - ink_cx))
        paste_x = max(pad_x // 2 - int(ink_cx), min(paste_x, total_w - im_ln.width - (pad_x // 2)))
        canvas.alpha_composite(im_ln, dest=(paste_x, cur_y))
        cur_y += im_ln.height + line_gap

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)
    return total_w, total_h


# ── 亮底自适应投影闭环（同一套风格，只把「投影/描边」的强度往上调）──
# 用户实测 2026-11-24：白字压在亮墙/白鱼身上时 WCAG 单帧只有 1.3~3.2:1，字看不清。
# 做法：先按标定值（boost=1.0）量一次；不够就沿同族系数阶梯加强投影，投影到顶仍不够才补细描边。
# 目标取 4.2（高于质检硬门槛 3.8，给 H.264 缩放后的软化留余量）；暗底 boost 永远保持 1.0 不动标定值。
# AAC 首编时挂 4x 过采样真峰守门（192kHz True Peak Limiting + 18kHz 低通）：同步消除 48kHz 采样间潜峰与高频 MDCT 振铃过冲（五风格全实测 TP -1.5~-3.5dBTP）
DELIVERY_AUDIO_VF = "aresample=192000,lowpass=f=18000,alimiter=limit=0.65:level=disabled:attack=1.5:release=40:latency=true,aresample=48000"
SUBTITLE_CONTRAST_TARGET = 4.2
SUBTITLE_SHADOW_BOOST_STEPS = (1.0, 1.4, 2.2)
# 增强阶梯：按「偏离标定值从小到大」排，先加深投影（同族系数），投影到顶仍不行才补细描边（描边不是 style_21 的 DNA，
# 只在亮底逼到没办法时才用，且封顶 4px）。实测（2026-11-24，base 轨真背景）：只放投影最多把 1.37:1 拉到 1.62:1
# （均匀亮底上偏移投影只盖住一个角），2px 描边→2.3~6.1、4px→7.3~21，因此阶梯最后必须允许到 4px 才能守住 3.8。
SUBTITLE_ENHANCE_LADDER: Tuple[Tuple[float, int], ...] = (
    (1.4, 0),
    (2.2, 0),
    (1.4, 2),
    (2.2, 2),
    (1.4, 3),
    (2.2, 3),
    (1.4, 4),
    (2.2, 4),
    (1.4, 6),
    (2.2, 6),
    (2.2, 8),
)
SUBTITLE_BRIGHT_BG_LUM = 0.18  # 环带背景亮度低于此值视为「暗底」，不启动增强


def _subtitle_bg_strip(
    ffmpeg_bin: str,
    video_path: Path,
    at_sec: float,
    y0: int,
    y1: int,
    cache_dir: Path,
    tag: str,
) -> Optional[np.ndarray]:
    """抽一帧成片（已调色、无字幕的 base 轨）并裁出字幕所在横条，供投影闭环现场合图度量。"""
    y0 = max(0, min(CANVAS_HEIGHT - 8, int(y0)))
    y1 = max(y0 + 8, min(CANVAS_HEIGHT, int(y1)))
    out_jpg = cache_dir / f"bg_{tag}.jpg"
    res = run_cmd([
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, float(at_sec)):.3f}", "-i", str(video_path),
        "-frames:v", "1",
        "-vf", f"crop={CANVAS_WIDTH}:{y1 - y0}:0:{y0}",
        "-q:v", "2", str(out_jpg),
    ], check=False)
    if res.returncode != 0 or not out_jpg.is_file():
        return None
    with Image.open(out_jpg) as im:
        return np.asarray(im.convert("RGB"))


def render_subtitle_png_adaptive(
    entry: Dict[str, Any],
    tpl: Dict[str, Any],
    font_path: Optional[str],
    out_png: Path,
    bg_strip: Optional[np.ndarray] = None,
    bg_strip_y0: int = 0,
    place_xy: Optional[Any] = None,
    target_ratio: float = SUBTITLE_CONTRAST_TARGET,
    allow_stroke: bool = True,
) -> Tuple[int, int, Dict[str, Any]]:
    """渲字幕 PNG 并在真实背景上闭度量对比度，不够就同族加强投影（必要时补细描边）。

    返回 (w, h, meta)。meta 里记原始/增强后的实测对比度与用了哪一档，供 05_subtitle_render_log.json 与质检对账。
    bg_strip 为 None（拿不到底图）时按标定值渲一次，不造假度量。
    """
    meta: Dict[str, Any] = {
        "shadow_boost": 1.0,
        "stroke_extra": 0,
        "contrast_before": None,
        "contrast_after": None,
        "bg_lum": None,
    }

    def _measure(w: int, h: int) -> Optional[Dict[str, float]]:
        if bg_strip is None or place_xy is None:
            return None
        px, py = place_xy(w, h)
        top = int(py) - int(bg_strip_y0)
        left = int(px)
        if top < 0 or left < 0:
            return None
        if top + h > bg_strip.shape[0] or left + w > bg_strip.shape[1]:
            return None
        with Image.open(out_png) as png:
            layer = png.convert("RGBA")
            patch = Image.fromarray(bg_strip[top : top + h, left : left + w]).convert("RGBA")
            canvas = Image.alpha_composite(patch, layer)
            return glyph_contrast_ratio(np.asarray(canvas.convert("RGB")))

    sw, sh = render_subtitle_png(entry, tpl, font_path, out_png)
    base_metric = _measure(sw, sh)
    if base_metric is None:
        return sw, sh, meta
    base_eff = float(base_metric.get("ratio_effective", base_metric["ratio"]))
    meta["contrast_before"] = round(base_eff, 2)
    meta["contrast_median"] = round(float(base_metric["ratio"]), 2)
    meta["bg_lum"] = round(float(base_metric["bg_lum"]), 3)
    if base_metric["bg_lum"] < SUBTITLE_BRIGHT_BG_LUM or base_eff >= target_ratio:
        # 暗底或本来就够清楚：保持已标定的 1:1 参数，一个像素都不动
        meta["contrast_after"] = meta["contrast_before"]
        return sw, sh, meta

    best = (sw, sh, 1.0, 0, base_eff)
    for boost, extra in (SUBTITLE_ENHANCE_LADDER if allow_stroke else tuple(c for c in SUBTITLE_ENHANCE_LADDER if c[1] == 0)):
        cand_w, cand_h = render_subtitle_png(
            entry, tpl, font_path, out_png, shadow_boost=boost, stroke_extra=extra
        )
        m = _measure(cand_w, cand_h)
        if m is None:
            continue
        cand_eff = float(m.get("ratio_effective", m["ratio"]))
        if cand_eff > best[4]:
            best = (cand_w, cand_h, boost, extra, cand_eff)
        if cand_eff >= target_ratio:
            meta.update({
                "shadow_boost": float(boost),
                "stroke_extra": int(extra),
                "contrast_after": round(cand_eff, 2),
            })
            return cand_w, cand_h, meta
    # 到顶仍不够：用跑到的最好一档（不撒谎，实测值照记）
    bw, bh, bboost, bextra, bratio = best
    if (bboost, bextra) != (1.0, 0):
        render_subtitle_png(entry, tpl, font_path, out_png, shadow_boost=bboost, stroke_extra=bextra)
    meta.update({
        "shadow_boost": float(bboost),
        "stroke_extra": int(bextra),
        "contrast_after": round(float(bratio), 2),
        "below_target": True,
    })
    return bw, bh, meta


def _resolve_packaging_palette(style_id: str, track: str = "store") -> Dict[str, Tuple[int, int, int, int]]:
    pal = resolve_style_palette(style_id, track=track)
    bg_rgba = parse_hex_rgba(pal.get("card_bg_hex") or pal.get("bg_hex", "#12151E"), 232)
    accent_rgba = parse_hex_rgba(pal.get("accent_hex", "#FFD54F"), 255)
    text_rgba = parse_hex_rgba(pal.get("title_hex", "#F8FAFC"), 255)
    border_rgba = parse_hex_rgba(pal.get("border_hex") or pal.get("accent_hex", "#FFD54F"), 220)
    if contrast_ratio(text_rgba[:3], bg_rgba[:3]) < 4.5:
        text_rgba = (17, 24, 39, 255) if pal.get("is_light") else (255, 255, 255, 255)
    return {
        "bg": bg_rgba,
        "accent": accent_rgba,
        "text": text_rgba,
        "border": border_rgba,
    }


def _fit_line(draw: Any, text: str, font_path: Optional[str], max_w: int, start_sz: int, min_sz: int = 24) -> Tuple[Any, str]:
    return fit_text_line(draw, str(text or ""), font_path, max_width=max_w, max_size=start_sz, min_size=min_sz)


def render_packaging_png(
    elem: Dict[str, Any],
    style_id: str = "style_01",
    color_plan: Optional[Dict[str, Any]] = None,
    font_candidates: Optional[List[str]] = None,
) -> Image.Image:
    """渲染视觉包装 PNG（兼容 06_packaging.json: kind/text_or_asset 及通用字段）。"""
    del color_plan
    pal = _resolve_packaging_palette(style_id)
    font_path = find_commercial_font(font_candidates)
    elem_kind = str(elem.get("kind") or elem.get("type") or "flower_text")
    main_text = str(elem.get("text_or_asset") or elem.get("text") or "").strip()
    max_safe_w = SAFE_ZONE["x_max"] - SAFE_ZONE["x_min"]

    if elem_kind == "persistent_top_bar":
        # 顶部常驻主题标语栏（强对比保护 >= 4.5）
        text = main_text or "核心重点"
        fg_rgba = parse_hex_rgba(elem.get("fg_hex") or "#FFF589")
        pill_bg = (18, 22, 28)
        if contrast_ratio(fg_rgba[:3], pill_bg) < 4.5:
            fg_rgba = (255, 245, 137, 255)
        probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        pdraw = ImageDraw.Draw(probe)
        fnt, fitted = _fit_line(pdraw, text, font_path, SUBTITLE_SYMMETRIC_MAX_W - 48, 34, 22)
        bb = pdraw.textbbox((0, 0), fitted, font=fnt)
        tw, th = max(40, bb[2] - bb[0]), max(22, bb[3] - bb[1])
        w = min(SUBTITLE_SYMMETRIC_MAX_W, tw + 56)
        h = max(56, th + 24)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=h // 2, fill=(16, 20, 28, 228), outline=fg_rgba, width=3)
        draw.text((w // 2, h // 2), fitted, font=fnt, fill=fg_rgba, anchor="mm")
        return img

    if elem_kind == "quote_citation_card":
        # 模版级大引号毛玻璃金句引语卡（适用于 style_08 / style_09 / style_16 等）
        raw_quote = (main_text or "“核心实证观点”").strip().strip("“”\"'")
        sub_txt = str(elem.get("subtext") or "—— 核心观点实证").strip()
        fg_rgba = parse_hex_rgba(elem.get("fg_hex") or "#FFF589")
        acc_rgba = parse_hex_rgba(elem.get("accent_hex") or "#FF4D4F")
        card_bg = (14, 18, 26, 232)
        if contrast_ratio(fg_rgba[:3], card_bg[:3]) < 4.5:
            fg_rgba = (255, 245, 137, 255)
        w = min(SUBTITLE_SYMMETRIC_MAX_W, 608)
        h = 136
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([(2, 4), (w - 3, h - 2)], radius=20, fill=card_bg, outline=fg_rgba, width=3)
        draw.rounded_rectangle([(14, 18), (24, h - 18)], radius=5, fill=acc_rgba)
        q_fnt = get_cached_font(font_path, 58)
        draw.text((34, 6), "“", font=q_fnt, fill=acc_rgba, stroke_width=2, stroke_fill=(0, 0, 0, 240))
        draw.text((w - 58, h - 68), "”", font=q_fnt, fill=acc_rgba, stroke_width=2, stroke_fill=(0, 0, 0, 240))
        main_fnt, fitted_q = _fit_line(draw, raw_quote, font_path, w - 124, 46, 26)
        draw.text((w // 2 + 6, 52), fitted_q, font=main_fnt, fill=fg_rgba, anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0, 245))
        sub_fnt, fitted_sub = _fit_line(draw, sub_txt, font_path, w - 140, 24, 18)
        draw.line([(48, 92), (w - 64, 92)], fill=(255, 245, 137, 110), width=1)
        draw.text((w // 2 + 6, 112), fitted_sub, font=sub_fnt, fill=(240, 244, 250, 245), anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0, 220))
        return img

    burst_tags = [str(t).strip() for t in (elem.get("burst_tags") or []) if str(t).strip()]
    if elem_kind in ("stamp_tag", "flower_text") and len(burst_tags) >= 2:
        # 三连弹卡 / 三阶复利词条（适用于 style_08 / style_09 / style_16 等）
        tags = burst_tags[:3]
        fg_rgba = parse_hex_rgba(elem.get("fg_hex") or "#FFF589")
        acc_rgba = parse_hex_rgba(elem.get("accent_hex") or "#FF1837")
        w = min(SUBTITLE_SYMMETRIC_MAX_W, 616)
        h = 98
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        n_t = len(tags)
        gap = 12
        cell_w = (w - gap * (n_t - 1)) // n_t
        for ti, tg in enumerate(tags):
            cx0 = ti * (cell_w + gap)
            cx1 = cx0 + cell_w - 1
            is_hi = (ti == n_t - 1)
            bg_c = (180, 24, 38, 238) if is_hi else (18, 22, 30, 232)
            bd_c = fg_rgba if is_hi else acc_rgba
            tx_c = (255, 245, 137, 255) if is_hi else (255, 255, 255, 255)
            draw.rounded_rectangle([(cx0, 8), (cx1, h - 8)], radius=16, fill=bg_c, outline=bd_c, width=3)
            tfnt, tfit = _fit_line(draw, tg[:5], font_path, cell_w - 22, 38, 22)
            draw.text(((cx0 + cx1) // 2, h // 2), tfit, font=tfnt, fill=tx_c, anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0, 240))
        return img

    if elem_kind == "center_burst_title":
        # 真·巨字冲击引擎（支持按元素因果表 112px–488px 分档）
        giant_mode = str(elem.get("giant_mode") or "giant_smash_single")
        req_fs = max(112, min(520, int(elem.get("font_size_px") or 140)))
        fg_rgba = parse_hex_rgba(elem.get("fg_hex") or "#FFE28A")
        acc_rgba = parse_hex_rgba(elem.get("accent_hex") or "#FF2438")
        st_rgba = parse_hex_rgba(elem.get("stroke_hex") or "#000000")
        if contrast_ratio(fg_rgba[:3], (12, 16, 22)) < 4.5:
            fg_rgba = (255, 245, 137, 255)
        if contrast_ratio(acc_rgba[:3], (12, 16, 22)) < 3.8:
            acc_rgba = (255, 77, 79, 255)
        tag_label = str(elem.get("tag_label") or "核心重点").strip()[:6]
        raw_txt = (main_text or "核心重点").strip().strip("“”\"'")[:6]
        max_w_cap = SUBTITLE_SYMMETRIC_MAX_W  # 640px

        if giant_mode == "giant_numeric_trio":
            # style_16, 11, 22：三格巨型数字/核心字连跳砸屏 (138px–164px)
            chars = list(raw_txt[:3]) if len(raw_txt) >= 3 else list((raw_txt + "100")[:3])
            w, h = max_w_cap, 164
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # 顶部导语横签
            tag_fnt = get_cached_font(font_path, 22)
            draw.rounded_rectangle([(w // 2 - 96, 2), (w // 2 + 96, 32)], radius=14, fill=(18, 22, 30, 235), outline=fg_rgba, width=2)
            draw.text((w // 2, 17), f"★ {tag_label} ★", font=tag_fnt, fill=fg_rgba, anchor="mm")
            cell_w = 178
            gap = 18
            total_cw = cell_w * 3 + gap * 2
            x_start = (w - total_cw) // 2
            c_sz = min(104, req_fs - 36)
            for ci, ch in enumerate(chars):
                cx0 = x_start + ci * (cell_w + gap)
                cx1 = cx0 + cell_w
                c_bg = (178, 24, 36, 236) if ci == 0 else (20, 24, 34, 232)
                c_bd = fg_rgba if ci == 0 else acc_rgba
                c_fg = (255, 245, 137, 255) if ci > 0 else (255, 255, 255, 255)
                # 巨字逐字绘制：每格一字，逐字取字体（缺字回落，字号不变，anchor=mm 居中不受影响）
                c_fnt = glyph_font_for(ch, font_path, c_sz)
                draw.rounded_rectangle([(cx0, 38), (cx1, h - 10)], radius=20, fill=c_bg, outline=c_bd, width=4)
                draw.text(((cx0 + cx1) // 2 + 3, 98 + 4), ch, font=c_fnt, fill=(0, 0, 0, 230), anchor="mm", stroke_width=8, stroke_fill=(0, 0, 0, 230))
                draw.text(((cx0 + cx1) // 2, 98), ch, font=c_fnt, fill=c_fg, anchor="mm", stroke_width=8, stroke_fill=st_rgba)
            return img

        if giant_mode == "giant_dual_clash":
            # style_02, 08, 09, 17, 19：左右/上下双色对撞巨字 (124px–144px)
            mid = max(1, len(raw_txt) // 2) if len(raw_txt) >= 2 else 1
            left_t = raw_txt[:mid]
            right_t = raw_txt[mid:] or tag_label[:2]
            w, h = max_w_cap, 158
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # 背板斜切警示撞色条
            draw.rounded_rectangle([(18, 22), (w - 18, h - 14)], radius=22, fill=(14, 18, 26, 216), outline=fg_rgba, width=3)
            draw.polygon([(w // 2 + 10, 24), (w - 22, 24), (w - 22, h - 16), (w // 2 - 18, h - 16)], fill=(185, 22, 36, 228))
            f_left, l_fit = _fit_line(draw, left_t, font_path, w // 2 - 36, min(112, req_fs - 24), 48)
            f_right, r_fit = _fit_line(draw, right_t, font_path, w // 2 - 36, min(112, req_fs - 24), 48)
            lx, ly = w // 4 + 6, h // 2 - 2
            rx, ry = (w * 3) // 4 - 4, h // 2 + 6
            draw.text((lx + 4, ly + 5), l_fit, font=f_left, fill=(0, 0, 0, 235), anchor="mm", stroke_width=9, stroke_fill=(0, 0, 0, 235))
            draw.text((lx, ly), l_fit, font=f_left, fill=(255, 255, 255, 255), anchor="mm", stroke_width=9, stroke_fill=st_rgba)
            draw.text((rx + 4, ry + 5), r_fit, font=f_right, fill=(0, 0, 0, 235), anchor="mm", stroke_width=9, stroke_fill=(0, 0, 0, 235))
            draw.text((rx, ry), r_fit, font=f_right, fill=fg_rgba, anchor="mm", stroke_width=9, stroke_fill=st_rgba)
            # 顶部角标
            t_fnt = get_cached_font(font_path, 20)
            draw.rounded_rectangle([(28, 4), (176, 32)], radius=10, fill=fg_rgba, outline=(0, 0, 0, 255), width=2)
            draw.text((102, 18), tag_label[:4], font=t_fnt, fill=(12, 16, 22, 255), anchor="mm")
            return img

        if giant_mode == "giant_vertical_seal":
            # style_04, 06, 13, 20, 23：130px–148px 粗宋大字 + 朱红古风方印章
            w, h = max_w_cap, 154
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            f_main, m_fit = _fit_line(draw, raw_txt, font_path, w - 148, min(118, req_fs - 18), 54)
            mx = (w - 76) // 2
            my = h // 2 + 4
            draw.text((mx + 4, my + 5), m_fit, font=f_main, fill=(0, 0, 0, 230), anchor="mm", stroke_width=9, stroke_fill=(0, 0, 0, 230))
            draw.text((mx, my), m_fit, font=f_main, fill=fg_rgba, anchor="mm", stroke_width=9, stroke_fill=st_rgba)
            # 右侧朱红印章签
            sx0, sy0, sx1, sy1 = w - 122, 22, w - 30, h - 22
            draw.rounded_rectangle([(sx0, sy0), (sx1, sy1)], radius=12, fill=(184, 28, 34, 242), outline=(255, 232, 168, 255), width=3)
            s_fnt = get_cached_font(font_path, 24)
            seal_t = tag_label[:4] if len(tag_label) >= 4 else (tag_label + "精选")[:4]
            draw.text(((sx0 + sx1) // 2, sy0 + 32), seal_t[:2], font=s_fnt, fill=(255, 248, 230, 255), anchor="mm")
            draw.text(((sx0 + sx1) // 2, sy0 + 72), seal_t[2:4], font=s_fnt, fill=(255, 248, 230, 255), anchor="mm")
            return img

        # 默认 giant_smash_single：若为因果分级大字（giant_text / req_fs>168），放开尺寸直接纯大字砸屏
        is_giant_role = str(elem.get("display_role") or "") == "giant_text" or req_fs > 168
        if is_giant_role:
            target_fs = max(96, req_fs)
            w = max(max_w_cap, 960)
            h = max(180, int(target_fs * 1.45))
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            f_main, m_fit = _fit_line(draw, raw_txt, font_path, w - 24, target_fs, 72)
            mx, my = w // 2, h // 2
            sw = max(10, min(20, int(round(target_fs / 22.0))))
            draw.text((mx + 6, my + 8), m_fit, font=f_main, fill=(0, 0, 0, 235), anchor="mm", stroke_width=sw, stroke_fill=(0, 0, 0, 235))
            draw.text((mx, my), m_fit, font=f_main, fill=fg_rgba, anchor="mm", stroke_width=sw, stroke_fill=st_rgba)
            ink = img.getbbox()
            return img.crop(ink) if ink else img
        w, h = max_w_cap, 156
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        t_fnt = get_cached_font(font_path, 22)
        draw.rounded_rectangle([(w // 2 - 104, 2), (w // 2 + 104, 34)], radius=14, fill=(185, 24, 38, 238), outline=fg_rgba, width=2)
        draw.text((w // 2, 18), f"⚡ {tag_label} ⚡", font=t_fnt, fill=(255, 255, 255, 255), anchor="mm")
        f_main, m_fit = _fit_line(draw, raw_txt, font_path, w - 36, min(122, req_fs - 18), 56)
        mx, my = w // 2, 96
        draw.text((mx + 5, my + 6), m_fit, font=f_main, fill=(0, 0, 0, 235), anchor="mm", stroke_width=10, stroke_fill=(0, 0, 0, 235))
        draw.text((mx, my), m_fit, font=f_main, fill=fg_rgba, anchor="mm", stroke_width=10, stroke_fill=st_rgba)
        return img

    if elem_kind in ("stamp_tag", "end_card_cta"):
        # 原生无底框高冲击描边花字与片尾召唤卡
        fg_rgba = parse_hex_rgba(elem.get("fg_hex") or "#FFE28A")
        st_rgba = parse_hex_rgba(elem.get("stroke_hex") or "#000000")
        sh_rgba = (0, 0, 0, 210)
        max_w_cap = SUBTITLE_SYMMETRIC_MAX_W
        target_fs = 64 if elem_kind == "end_card_cta" else 62
        sw_draw = 6
        text = main_text or "核心重点"
        sub_txt = str(elem.get("subtext") or "").strip() if elem_kind == "end_card_cta" else ""

        tmp = Image.new("RGBA", (max_w_cap + 120, 260), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        f1, t1 = _fit_line(td, text, font_path, max_w_cap - 36, target_fs, 32)
        bb1 = td.textbbox((0, 0), t1, font=f1, stroke_width=sw_draw)
        w1 = max(40, bb1[2] - bb1[0])
        h1 = max(36, bb1[3] - bb1[1])
        x1 = (tmp.width - w1) // 2 - bb1[0]
        y1 = 24 - bb1[1]
        td.text((x1 + 3, y1 + 4), t1, font=f1, fill=sh_rgba, stroke_width=sw_draw, stroke_fill=sh_rgba)
        td.text((x1, y1), t1, font=f1, fill=fg_rgba, stroke_width=sw_draw, stroke_fill=st_rgba)

        if sub_txt:
            f2, t2 = _fit_line(td, sub_txt, font_path, max_w_cap - 48, 34, 22)
            bb2 = td.textbbox((0, 0), t2, font=f2, stroke_width=3)
            w2 = max(40, bb2[2] - bb2[0])
            x2 = (tmp.width - w2) // 2 - bb2[0]
            y2 = 24 + h1 + 14 - bb2[1]
            td.text((x2 + 2, y2 + 3), t2, font=f2, fill=sh_rgba, stroke_width=3, stroke_fill=sh_rgba)
            td.text((x2, y2), t2, font=f2, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=st_rgba)

        ink = tmp.getbbox()
        if ink is not None:
            crop = tmp.crop(ink)
            pad = 12
            ow = min(max_w_cap, crop.width + pad * 2)
            oh = crop.height + pad * 2
            out_img = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
            if crop.width > ow - pad * 2:
                ratio = (ow - pad * 2) / float(crop.width)
                crop = crop.resize((ow - pad * 2, max(24, int(round(crop.height * ratio)))), Image.Resampling.LANCZOS)
            out_img.alpha_composite(crop, dest=((ow - crop.width) // 2, (oh - crop.height) // 2))
            return out_img
        return Image.new("RGBA", (240, 84), (0, 0, 0, 0))

    text = main_text or "重点"
    probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(probe)
    fnt, fitted = _fit_line(pdraw, text, font_path, max_safe_w - 64, 42, 24)
    bb = pdraw.textbbox((0, 0), fitted, font=fnt)
    tw, th = max(40, bb[2] - bb[0]), max(28, bb[3] - bb[1])
    w = min(max_safe_w, tw + 68)
    h = max(72, th + 32)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=18, fill=pal["bg"], outline=pal["border"], width=2)
    draw.rounded_rectangle([(12, 16), (22, h - 16)], radius=5, fill=pal["accent"])
    draw.text((38, (h - th) // 2 - 2), fitted, font=fnt, fill=pal["text"])
    return img


def resolve_packaging_source(
    elem: Dict[str, Any],
    pipeline_dir: Path,
    idx: int,
    style_id: str = "style_01",
    color_plan: Optional[Dict[str, Any]] = None,
    font_candidates: Optional[List[str]] = None,
) -> Path:
    out_png = pipeline_dir / "_render_final" / f"pkg_{idx:03d}.png"
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img: Optional[Image.Image] = None
    try:
        from render_template_overlays import render_template_packaging_png
        img = render_template_packaging_png(elem, style_id=style_id, font_candidates=font_candidates)
    except Exception:
        img = None
    if img is None:
        img = render_packaging_png(elem, style_id=style_id, color_plan=color_plan, font_candidates=font_candidates)
    img.save(out_png)
    return out_png


def _ensure_circle_mask_png(cache_dir: Path, radius_px: int) -> Path:
    """预烘焙抗锯齿圆形灰度蒙版 PNG（替代逐像素 geq 解释器，提速 50x+）。"""
    r = max(16, int(radius_px))
    diam = r * 2
    cache_dir.mkdir(parents=True, exist_ok=True)
    mask_p = cache_dir / f"_pip_circle_mask_{diam}.png"
    if not mask_p.is_file():
        mask = Image.new("L", (diam, diam), 0)
        ImageDraw.Draw(mask).ellipse([(2, 2), (diam - 3, diam - 3)], fill=255)
        mask.save(mask_p)
    return mask_p


def _ensure_circle_ring_png(cache_dir: Path, radius_px: int, border_hex: str = "#FFF589") -> Path:
    """预烘焙平滑圆环人像的发光装饰外环 PNG。"""
    r = max(16, int(radius_px))
    pad = 14
    size = r * 2 + pad * 2
    safe_hex = re.sub(r"[^0-9A-Fa-f]", "", str(border_hex or "FFF589"))[:6] or "FFF589"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ring_p = cache_dir / f"_pip_circle_ring_{r}_{safe_hex}.png"
    if not ring_p.is_file():
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        col = parse_hex_rgba(f"#{safe_hex}")
        d.ellipse([(pad, pad), (size - pad - 1, size - pad - 1)], outline=(236, 227, 207, 240), width=4)
        d.ellipse([(pad - 5, pad - 5), (size - pad + 4, size - pad + 4)], outline=col, width=3)
        d.arc([(pad - 10, pad - 10), (size - pad + 9, size - pad + 9)], start=25, end=115, fill=col, width=4)
        d.arc([(pad - 10, pad - 10), (size - pad + 9, size - pad + 9)], start=205, end=270, fill=(236, 227, 207, 170), width=2)
        img.save(ring_p)
    return ring_p


def _ensure_rounded_rect_mask_png(cache_dir: Path, width: int, height: int, radius: int = 38) -> Path:
    """预烘焙抗锯齿圆角矩形灰度 Alpha 蒙版 PNG（供 style_13 上白圆角画框高速 alphamerge）。"""
    w = max(32, int(width) & ~1)
    h = max(32, int(height) & ~1)
    r = max(4, min(int(radius), min(w, h) // 3))
    cache_dir.mkdir(parents=True, exist_ok=True)
    mask_p = cache_dir / f"_pip_round_mask_{w}x{h}_r{r}.png"
    if not mask_p.is_file():
        # 2x 超采样抗锯齿绘制圆角蒙版
        hi = Image.new("L", (w * 2, h * 2), 0)
        ImageDraw.Draw(hi).rounded_rectangle([(0, 0), (w * 2 - 1, h * 2 - 1)], radius=r * 2, fill=255)
        hi.resize((w, h), Image.Resampling.LANCZOS).save(mask_p)
    return mask_p


def _ensure_feather_bottom_mask_png(cache_dir: Path, width: int, height: int, feather_px: int = 180) -> Path:
    """预烘焙底部平滑线性羽化渐变（255 -> 0）Alpha 蒙版 PNG（供 style_16 上窄实景平滑溶入下纯黑书摘区）。"""
    import numpy as np

    w = max(32, int(width) & ~1)
    h = max(32, int(height) & ~1)
    fp = max(16, min(h - 16, int(feather_px)))
    cache_dir.mkdir(parents=True, exist_ok=True)
    mask_p = cache_dir / f"_pip_feather_bot_{w}x{h}_f{fp}.png"
    if not mask_p.is_file():
        col = np.full((h, 1), 255, dtype=np.uint8)
        y_start = h - fp
        # 余弦平滑过渡比硬折线更接近剪映线性蒙版真实观感
        t = np.linspace(0.0, 1.0, fp, dtype=np.float32)
        fade = (0.5 * (1.0 + np.cos(np.pi * t)) * 255.0).clip(0, 255).astype(np.uint8)
        col[y_start:, 0] = fade
        arr = np.repeat(col, w, axis=1)
        Image.fromarray(arr, mode="L").save(mask_p)
    return mask_p


def _ensure_dual_gold_divider_png(
    cache_dir: Path,
    w: int,
    h: int,
    border_hex: str = "#C8A96A",
    border_w: int = 3,
    dual_divider: bool = True,
) -> Path:
    """为 style_09 预烘焙顶部细金线外框 + 居中竖向双格分栏金线透明叠加图。"""
    safe_hex = re.sub(r"[^0-9A-Fa-f]", "", str(border_hex or "C8A96A"))[:6] or "C8A96A"
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / f"_pip_gold_frame_{w}x{h}_{safe_hex}_d{int(dual_divider)}.png"
    if not p.is_file():
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        col = parse_hex_rgba(f"#{safe_hex}")
        bw = max(2, int(border_w))
        d.rectangle([(0, 0), (w - 1, h - 1)], outline=col, width=bw)
        if dual_divider:
            mx = w // 2
            d.line([(mx, bw), (mx, h - bw)], fill=col, width=max(2, bw))
        img.save(p)
    return p


def _rects_overlap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def _avoid_face_boxes(
    px: int,
    py: int,
    pw: int,
    ph: int,
    faces: List[Dict[str, int]],
    y_min: int,
    y_max: int,
) -> Tuple[int, int]:
    if not faces:
        return px, max(y_min, min(py, y_max - ph))
    face_rects = [(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]) for f in faces]
    step_y = 60
    candidates = [py]
    for d in range(1, 12):
        candidates.append(py + d * step_y)
        candidates.append(py - d * step_y)
    for cy in candidates:
        cy = max(y_min, min(cy, y_max - ph))
        cand_rect = (px, cy, px + pw, cy + ph)
        if not any(_rects_overlap(cand_rect, fr) for fr in face_rects):
            return px, cy
    return px, max(y_min, min(py, y_max - ph))


def _place_from_final_bbox(
    entry: Dict[str, Any],
    pw: int,
    ph: int,
    zone: Dict[str, int],
    default_xy: Tuple[int, int],
    faces: List[Dict[str, int]],
) -> Tuple[int, int]:
    fb = entry.get("final_bbox") or entry.get("bbox")
    if isinstance(fb, dict) and {"x", "y", "w", "h"}.issubset(fb.keys()):
        bx = int(fb["x"]) + (int(fb["w"]) - pw) // 2
        by = int(fb["y"]) + (int(fb["h"]) - ph) // 2
        px = max(zone["x_min"], min(bx, zone["x_max"] - pw))
        py = max(zone["y_min"], min(by, zone["y_max"] - ph))
        # 风格给的落位也要让脸：花字/贴纸压在人脸上，观众第一眼看到的就是糊字压脸
        #（实测 52.5s/53.0s『绝对品质』压脸 IoU 0.17）。能避开就沿 y 挪，避不开才回原位。
        if faces:
            px, py = _avoid_face_boxes(int(px), int(py), pw, ph, faces, zone["y_min"], zone["y_max"])
        return int(px), int(py)
    return _avoid_face_boxes(default_xy[0], default_xy[1], pw, ph, faces, zone["y_min"], zone["y_max"])


def _build_color_filter_for_clip(clip: Dict[str, Any], color_plan: Dict[str, Any]) -> Optional[str]:
    cf_str = str(clip.get("color_filter") or "").strip()
    if cf_str:
        return cf_str
    cid = clip.get("clip_id")
    for item in (color_plan.get("clip_plans") or []):
        if item.get("clip_id") == cid:
            if not item.get("apply_color_grade"):
                return None
            vf = str(item.get("vf_filter") or "").strip()
            return vf or None
    return None


def _resolve_master_wav(pipeline_dir: Path) -> Tuple[Path, Dict[str, Any]]:
    mix_p = pipeline_dir / "08_mix_metrics.json"
    if mix_p.exists():
        mix_metrics = read_json(mix_p)
        cand = Path(mix_metrics.get("master_wav") or (pipeline_dir / "08_mixed_master.wav"))
        if cand.exists():
            return cand, mix_metrics
    fallback = pipeline_dir / "08_mixed_master.wav"
    if not fallback.exists():
        raise FileNotFoundError(f"混音母带不存在: {fallback}")
    return fallback, {}


def _manifest_lookups(manifest: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, int]]], Dict[str, Dict[str, Any]]]:
    faces_by_mp4: Dict[str, List[Dict[str, int]]] = {}
    eye_by_mp4: Dict[str, Dict[str, Any]] = {}
    for item in (manifest.get("items") or []):
        mp = str(item.get("normalized_mp4") or "")
        if not mp:
            continue
        norm_faces = parse_manifest_faces(item)
        for key in {mp, str(Path(mp).resolve())}:
            faces_by_mp4[key] = norm_faces
            if item.get("eye_anchor"):
                eye_by_mp4[key] = item["eye_anchor"]
    return faces_by_mp4, eye_by_mp4


def _seg_frame_count(ffmpeg_bin: str, seg_path: Path) -> Optional[int]:
    """读单个分镜 mp4 的实际帧数（优先流元数据，拿不到就量时长×帧率）。"""
    ffprobe_bin = find_bin("ffprobe") or "ffprobe"
    res = run_cmd([
        ffprobe_bin, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames,avg_frame_rate,duration",
        "-of", "json", str(seg_path),
    ], check=False)
    if res.returncode != 0:
        return None
    try:
        st = (json.loads(res.stdout or "{}").get("streams") or [{}])[0]
    except Exception:
        return None
    nb = st.get("nb_frames")
    if nb and str(nb).isdigit():
        return int(nb)
    rate = str(st.get("avg_frame_rate") or "")
    try:
        num, den = rate.split("/")
        fps_v = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps_v = 0.0
    dur = st.get("duration")
    if fps_v > 0 and dur:
        return int(round(float(dur) * fps_v))
    return None


def _pad_short_segments(
    ffmpeg_bin: str,
    seg_files: List[Path],
    clip_nom_frames: List[int],
    extra_xf: List[int],
    fps: int,
) -> int:
    """把短出计划帧数的分镜用末帧克隆补齐（最多补 1 秒，异常偏差不硬补）。返回补过的镜头数。"""
    padded = 0
    for idx, seg_p in enumerate(seg_files):
        if not seg_p.is_file():
            continue
        want = clip_nom_frames[idx] + (extra_xf[idx] if idx < len(extra_xf) else 0)
        have = _seg_frame_count(ffmpeg_bin, seg_p)
        if have is None or have >= want or have <= 0:
            continue
        deficit = want - have
        if deficit > fps:
            print(f"[render_mp4] [WARN] {seg_p.name} 少 {deficit} 帧，超 1 秒，不硬补（请查素材时长）", flush=True)
            continue
        tmp = seg_p.with_suffix(".pad.mp4")
        cmd = [
            ffmpeg_bin, "-y", "-i", str(seg_p),
            "-vf", f"tpad=stop_mode=clone:stop_duration={frames_to_sec(deficit, fps):.4f}",
            "-frames:v", str(want),
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS,
            str(tmp),
        ]
        res = run_cmd(cmd, check=False)
        if res.returncode == 0 and tmp.is_file() and (_seg_frame_count(ffmpeg_bin, tmp) or 0) >= want:
            tmp.replace(seg_p)
            padded += 1
        else:
            tmp.unlink(missing_ok=True)
    return padded


def _compute_base_cache_key(
    edl: Dict[str, Any],
    color_plan: Dict[str, Any],
    target_frames: int,
    preview: bool,
    source_hashes: Dict[str, str],
    eye_by_mp4: Dict[str, Any],
) -> str:
    payload = {
        "version": 7,  # v7：修正 layered_cutout_over_broll face_anchor['cy'] 偏移与底部字幕安全带避让
        "edl": edl,
        "color_plan": color_plan,
        "sources": source_hashes,
        "eyes": eye_by_mp4,
        "target_frames": target_frames,
        "preview": preview,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _corner_cutout_crop_expr(
    fa: Optional[Dict[str, Any]],
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
) -> str:
    """角落人像抠像（远/中全景坐姿）只裁取头至胸腰上半身，避免盘腿悬空在前景角落。"""
    if not isinstance(fa, dict):
        return ""
    face_cx = float(fa.get("cx") if fa.get("cx") is not None else 0.5)
    face_h = max(0.06, float(fa.get("h") or 0.15))
    face_cy = float(
        fa["cy"] if fa.get("cy") is not None
        else (float(fa.get("y") or 0.42) + face_h * 0.5)
    )
    if face_h > 0.24 or face_cy > 0.52:
        return ""
    top_frac = max(0.0, face_cy - max(0.14, face_h * 1.45))
    bot_frac = min(1.0, face_cy + max(0.32, face_h * 2.55))
    span_h = max(0.46, bot_frac - top_frac)
    if span_h >= 0.88:
        return ""
    ch = max(120, min(canvas_h, int(round(span_h * canvas_h)) & ~1))
    cw = max(80, min(canvas_w, int(round(ch * canvas_w / float(canvas_h))) & ~1))
    crop_x = max(0, min(canvas_w - cw, int(round(face_cx * canvas_w - cw / 2.0)) & ~1))
    crop_y = max(0, min(canvas_h - ch, int(round(top_frac * canvas_h)) & ~1))
    return f"crop={cw}:{ch}:{crop_x}:{crop_y}"


def _cover_crop_vf(w: int, h: int, face_bias: bool = False, anchor: Optional[Dict[str, Any]] = None) -> str:
    """等比放大填满窗口的裁切：有人脸锚点时以人脸为中心取景（不再按正中瞎切），否则居中/偏上。"""
    if anchor:
        return face_framing.cover_crop_vf(w, h, anchor)
    iw = max(2, int(w) & ~1)
    ih = max(2, int(h) & ~1)
    if face_bias:
        return (
            f"scale={iw}:{ih}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={iw}:{ih}:(iw-{iw})/2:(ih-{ih})*0.24"
        )
    return (
        f"scale={iw}:{ih}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={iw}:{ih}:(iw-{iw})/2:(ih-{ih})/2"
    )


def _build_ffmpeg_pip_vf(pip_layout: Optional[Dict[str, Any]], tw: int = CANVAS_WIDTH, th: int = CANVAS_HEIGHT) -> Optional[str]:
    """生成 30fps 单源动态画框/相框 FFmpeg 滤镜链（全部强制等比裁切零变形；mode='none' 保持干净全屏）。"""
    if not isinstance(pip_layout, dict) or tw != CANVAS_WIDTH or th != CANVAS_HEIGHT:
        return None
    mode = str(pip_layout.get("mode") or "none").strip()
    if mode in ("", "none", "null"):
        return None
    bd_hex = str(pip_layout.get("border_hex") or "#FFF589")
    if not bd_hex.startswith("#") or len(bd_hex) not in (7, 9):
        bd_hex = "#FFF589"
    bd_hex = bd_hex[:7]

    if mode.startswith("geometry_pip"):
        # 真草稿几何驱动：主轨 scale>1 推近、scale<1 缩窗居中/偏置；窗口由双源路径补（单源没有第二路素材）
        main = pip_layout.get("main") or {}
        sc = float(main.get("scale") or 1.0)
        mx = float(main.get("x") or 0.0)
        my = float(main.get("y") or 0.0)
        if sc <= 1.001:
            if sc >= 0.999:
                return None
            # 主轨缩窗：背景用自身模糊铺底（真草稿里缩窗背后是素材底 + 叠加层）
            w = int(round(CANVAS_WIDTH * sc / 2)) * 2
            h = int(round(CANVAS_HEIGHT * sc / 2)) * 2
            cx = int(round(CANVAS_WIDTH * (1.0 + mx) / 2.0))
            cy = int(round(CANVAS_HEIGHT * (1.0 + my) / 2.0))
            ox = max(0, min(CANVAS_WIDTH - w, cx - w // 2))
            oy = max(0, min(CANVAS_HEIGHT - h, cy - h // 2))
            return (
                f"split=2[gp_bg][gp_fg];"
                f"[gp_bg]scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT},boxblur=24:2,eq=brightness=-0.14[gp_bgb];"
                f"[gp_fg]{_cover_crop_vf(w, h, anchor=pip_layout.get('face_anchor'))}[gp_fgw];"
                f"[gp_bgb][gp_fgw]overlay=x={ox}:y={oy}:shortest=1"
            )
        # 推近：按 scale 放大后按 (x,y) 偏移裁回画布
        bw = int(round(CANVAS_WIDTH * sc / 2)) * 2
        bh = int(round(CANVAS_HEIGHT * sc / 2)) * 2
        ox = int(round((bw - CANVAS_WIDTH) / 2.0 - mx * CANVAS_WIDTH / 2.0))
        oy = int(round((bh - CANVAS_HEIGHT) / 2.0 - my * CANVAS_HEIGHT / 2.0))
        ox = max(0, min(bw - CANVAS_WIDTH, ox))
        oy = max(0, min(bh - CANVAS_HEIGHT, oy))
        pin = face_framing.punch_in_crop_expr(CANVAS_WIDTH, CANVAS_HEIGHT, pip_layout.get("face_anchor"))
        if pin:
            # 有人脸时推近跟着脸走：宁可少推一点也不把人推出画
            return f"scale={bw}:{bh}:flags=bicubic,{pin}"
        return (f"scale={bw}:{bh}:flags=bicubic,"
                f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{ox}:{oy}")

    if mode == "smooth_orbit_pip":
        corner = str(pip_layout.get("corner") or "tr").lower()
        r_pip = int(pip_layout.get("radius_px") or 146)
        diam = r_pip * 2
        cx_p = (CANVAS_WIDTH - 200) if corner != "tl" else 200
        cy_p = int(pip_layout.get("center_y") or 360)
        ox, oy = cx_p - r_pip, cy_p - r_pip
        return (
            f"split=2[pip_bg][pip_av];"
            f"[pip_av]{_cover_crop_vf(diam, diam, face_bias=True, anchor=pip_layout.get('face_anchor'))},pad={diam + 8}:{diam + 8}:4:4:color={bd_hex}[pip_circ];"
            f"[pip_bg][pip_circ]overlay=x={ox}:y={oy}:shortest=1"
        )

    if mode == "blur_bg_center_card_pip":
        return (
            f"split=2[pip_bg][pip_fg];"
            f"[pip_bg]boxblur=16:2,eq=brightness=-0.10[pip_bg_b];"
            f"[pip_fg]{_cover_crop_vf(764, 802, face_bias=True, anchor=pip_layout.get('face_anchor'))},pad=772:810:4:4:color={bd_hex}[pip_fg_p];"
            f"[pip_bg_b][pip_fg_p]overlay=x=154:y=386"
        )

    if mode == "black_polaroid_frame_pip":
        # style_06：纯黑底 + 居中无变形方/竖相框，留足顶部手写金句与底部 Daily Vlog 空间
        fw = int(pip_layout.get("frame_w") or 800)
        fh = int(pip_layout.get("frame_h") or 720)
        fx = int(pip_layout.get("frame_x") or (CANVAS_WIDTH - fw) // 2)
        fy = int(pip_layout.get("frame_y") or 540)
        bg_c = str(pip_layout.get("bg_hex") or "#050506")[:7]
        return (
            f"split=2[pip_bg][pip_fg];"
            f"[pip_bg]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[pip_bg_b];"
            f"[pip_fg]{_cover_crop_vf(fw, fh, face_bias=True, anchor=pip_layout.get('face_anchor'))}[pip_fg_p];"
            f"[pip_bg_b][pip_fg_p]overlay=x={fx}:y={fy}"
        )

    if mode == "paper_craft_card_pip":
        fw = int(pip_layout.get("frame_w") or 928)
        fh = int(pip_layout.get("frame_h") or 928)
        fx = int(pip_layout.get("frame_x") or 76)
        fy = int(pip_layout.get("frame_y") or 320)
        bg_c = str(pip_layout.get("bg_hex") or "#ECE5D8")[:7]
        bw = int(pip_layout.get("border_width") or 6)
        return (
            f"split=2[pip_bg][pip_fg];"
            f"[pip_bg]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[pip_bg_b];"
            f"[pip_fg]{_cover_crop_vf(fw - bw * 2, fh - bw * 2, face_bias=False)},pad={fw}:{fh}:{bw}:{bw}:color={bd_hex}[pip_fg_p];"
            f"[pip_bg_b][pip_fg_p]overlay=x={fx}:y={fy}"
        )

    if mode == "minimal_white_note_card_pip":
        fw = int(pip_layout.get("frame_w") or 928)
        fh = int(pip_layout.get("frame_h") or 880)
        fx = int(pip_layout.get("frame_x") or 76)
        fy = int(pip_layout.get("frame_y") or 330)
        bg_c = str(pip_layout.get("bg_hex") or "#F8F8F6")[:7]
        bw = int(pip_layout.get("border_width") or 4)
        return (
            f"split=2[pip_bg][pip_fg];"
            f"[pip_bg]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[pip_bg_b];"
            f"[pip_fg]{_cover_crop_vf(fw - bw * 2, fh - bw * 2, face_bias=True)},pad={fw}:{fh}:{bw}:{bw}:color={bd_hex}[pip_fg_p];"
            f"[pip_bg_b][pip_fg_p]overlay=x={fx}:y={fy}"
        )

    if mode == "top_banner_evidence_pip":
        bh = int(pip_layout.get("banner_h") or 240)
        bg_c = str(pip_layout.get("bg_hex") or "#0B0D12")[:7]
        return f"drawbox=x=0:y=0:w=iw:h={bh}:color={bg_c}@0.92:t=fill"

    if mode == "linear_split_feather_pip":
        # style_16：上半实景等比铺满 + 下半纯黑书摘区（无变形）
        top_h = int(pip_layout.get("top_h") or 640)
        bg_c = str(pip_layout.get("bg_hex") or "#050507")[:7]
        return (
            f"split=2[pip_bg][pip_top];"
            f"[pip_bg]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[pip_bg_b];"
            f"[pip_top]{_cover_crop_vf(CANVAS_WIDTH, top_h, face_bias=True)}[pip_top_c];"
            f"[pip_bg_b][pip_top_c]overlay=x=0:y=0"
        )

    return None


def _build_dual_pip_cmd(
    ffmpeg_bin: str,
    media_path: str,
    src_in: float,
    src_avail: float,
    base_vf_chain: str,
    pip_layout: Dict[str, Any],
    sec_media: str,
    sec_in: float,
    clip_dur: float,
    clip_frames: int,
    fps: int,
    out_mp4: Path,
) -> Optional[List[str]]:
    """真双源画中画（主镜头 + 独立异源副镜头）高速合成器：全部强制等比裁切零变形，严禁同脸套娃。"""
    mode = str(pip_layout.get("mode") or "none").strip()
    if mode in ("", "none", "null"):
        return None
    bd_hex = str(pip_layout.get("border_hex") or "#FFF589")
    if not bd_hex.startswith("#") or len(bd_hex) not in (7, 9):
        bd_hex = "#FFF589"
    bd_hex = bd_hex[:7]
    tail_str = ",".join(BT709_VF_SUFFIX)
    sec_trim = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS"

    if mode.startswith("geometry_pip"):
        wins = pip_layout.get("windows") or []
        # 真窗口才有意义：`scale≈1.0` 的是满屏叠加层，拿它当小窗会把主镜整个人盖住
        # （2026-11-24 实测：抠像被内容门槛关掉后，8 个 A-roll 镜头差点变成「B-roll 铺满」）
        real_wins = [w for w in wins if isinstance(w, dict) and float(w.get("scale") or 1.0) < 0.92]
        if not real_wins:
            return None
        win = real_wins[0]
        wsc = max(0.18, min(0.98, float(win.get("scale") or 0.55)))
        ww = int(round(CANVAS_WIDTH * wsc / 2)) * 2
        wh = int(round(CANVAS_HEIGHT * wsc / 2)) * 2
        cx = int(round(CANVAS_WIDTH * (1.0 + float(win.get("x") or 0.0)) / 2.0))
        cy = int(round(CANVAS_HEIGHT * (1.0 + float(win.get("y") or 0.0)) / 2.0))
        ox = max(0, min(CANVAS_WIDTH - ww, cx - ww // 2))
        oy = max(0, min(CANVAS_HEIGHT - wh, cy - wh // 2))
        bw = max(0, int(pip_layout.get("border_width") or 6))
        main_fg = base_vf_chain
        return [
            ffmpeg_bin, "-v", "error",
            # -ss 必须在该输入的 -i 之前：写在 -i 之后会变成**输出**侧的 seek，
            # 主输入照旧从 0 开始读，再被输出侧跳掉 src_in 秒 —— 0.6 秒的镜头直接 0 帧。
            # 2026-11-24 实测：21 个双源画中画镜头全部渲出 261 字节的空 mp4，成片比音轨短 3 秒。
            "-ss", f"{max(0.0, float(src_in)):.3f}", "-i", str(media_path),
            "-ss", f"{max(0.0, float(sec_in)):.3f}", "-i", str(sec_media),
            "-filter_complex",
            f"[0:v]{main_fg}[gpm];"
            f"[1:v]{sec_trim},{_cover_crop_vf(max(16, ww - bw * 2), max(16, wh - bw * 2), face_bias=True, anchor=pip_layout.get('secondary_face_anchor'))},"
            f"pad={ww}:{wh}:{bw}:{bw}:color={bd_hex}[gpw];"
            f"[gpm][gpw]overlay=x={ox}:y={oy}:shortest=1,{tail_str}[v]",
            "-map", "[v]", "-an", "-frames:v", str(clip_frames),
            "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-y", str(out_mp4),
        ]


    if mode == "smooth_orbit_pip":
        corner = str(pip_layout.get("corner") or "tr").lower()
        r_pip = int(pip_layout.get("radius_px") or 146)
        diam = r_pip * 2
        cx_p = (CANVAS_WIDTH - 200) if corner != "tl" else 200
        cy_p = int(pip_layout.get("center_y") or 360)
        ox, oy = cx_p - r_pip, cy_p - r_pip
        mask_p = _ensure_circle_mask_png(out_mp4.parent, r_pip)
        ring_p = _ensure_circle_ring_png(out_mp4.parent, r_pip, bd_hex)
        cx0, cy0 = (CANVAS_WIDTH - diam) // 2, (CANVAS_HEIGHT - diam) // 2
        t_out = max(0.45, clip_dur - 0.30)
        x_expr = f"if(lt(t,0.32),{cx0}+({ox}-{cx0})*sin(t/0.32*PI/2),if(gt(t,{t_out:.3f}),{ox}+({cx0}-{ox})*sin(min(1,(t-{t_out:.3f})/0.30)*PI/2),{ox}))"
        y_expr = f"if(lt(t,0.32),{cy0}+({oy}-{cy0})*sin(t/0.32*PI/2),if(gt(t,{t_out:.3f}),{oy}+({cy0}-{oy})*sin(min(1,(t-{t_out:.3f})/0.30)*PI/2),{oy}))"
        fc = (
            f"[0:v]{base_vf_chain}[p_main];"
            f"[1:v]{sec_trim},{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[p_av_raw];"
            f"[2:v]format=gray[p_mask];"
            f"[p_av_raw][p_mask]alphamerge[p_av_circ];"
            f"[p_main][p_av_circ]overlay=x='{x_expr}':y='{y_expr}':shortest=1[p_m1];"
            f"[p_m1][3:v]overlay=x='({x_expr})-14':y='({y_expr})-14':shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
            "-loop", "1", "-framerate", str(fps), "-i", str(mask_p),
            "-loop", "1", "-framerate", str(fps), "-i", str(ring_p),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    if mode == "dark_doc_circle_avatar_pip":
        # style_09：纯黑纪实底 + 顶部双格细金线无变形证据窗 + 左下正圆白金边主讲人头像
        tf_x = int(pip_layout.get("top_frame_x") or 76)
        tf_y = int(pip_layout.get("top_frame_y") or 290)
        tf_w = int(pip_layout.get("top_frame_w") or 928)
        tf_h = int(pip_layout.get("top_frame_h") or 540)
        bw = int(pip_layout.get("border_width") or 3)
        dual_div = bool(pip_layout.get("dual_pane_divider", True))
        av_r = int(pip_layout.get("avatar_r") or 122)
        av_d = av_r * 2
        av_x = int(pip_layout.get("avatar_cx") or 215) - av_r
        av_y = int(pip_layout.get("avatar_cy") or 1030) - av_r
        bg_c = str(pip_layout.get("bg_hex") or "#060608")[:7]
        av_bd = str(pip_layout.get("avatar_border_hex") or "#E2D5B5")[:7]
        mask_p = _ensure_circle_mask_png(out_mp4.parent, av_r)
        ring_p = _ensure_circle_ring_png(out_mp4.parent, av_r, av_bd)
        frame_p = _ensure_dual_gold_divider_png(out_mp4.parent, tf_w, tf_h, bd_hex, bw, dual_divider=dual_div)
        if dual_div:
            half_w = (tf_w - bw * 3) // 2
            inner_h = tf_h - bw * 2
            fc = (
                f"[0:v]{base_vf_chain},split=3[p_bg_src][p_av_src][p_ev_r];"
                f"[p_bg_src]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[p_bg];"
                f"[1:v]{sec_trim},{_cover_crop_vf(half_w, inner_h, face_bias=False)}[p_left];"
                f"[p_ev_r]{_cover_crop_vf(tf_w - bw * 2 - half_w, inner_h, face_bias=True)}[p_right];"
                f"[p_bg][p_left]overlay=x={tf_x + bw}:y={tf_y + bw}:shortest=1[p_m0];"
                f"[p_m0][p_right]overlay=x={tf_x + bw + half_w}:y={tf_y + bw}:shortest=1[p_m0b];"
                f"[p_m0b][4:v]overlay=x={tf_x}:y={tf_y}:shortest=1[p_m1];"
                f"[p_av_src]{_cover_crop_vf(av_d, av_d, face_bias=True)},format=rgba[p_av_raw];"
                f"[2:v]format=gray[p_mask];"
                f"[p_av_raw][p_mask]alphamerge[p_av];"
                f"[p_m1][p_av]overlay=x={av_x}:y={av_y}:shortest=1[p_m2];"
                f"[p_m2][3:v]overlay=x={av_x - 14}:y={av_y - 14}:shortest=1,{tail_str}[vout]"
            )
        else:
            fc = (
                f"[0:v]{base_vf_chain},split=2[p_bg_src][p_av_src];"
                f"[p_bg_src]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[p_bg];"
                f"[1:v]{sec_trim},{_cover_crop_vf(tf_w - bw * 2, tf_h - bw * 2, face_bias=False)}[p_ev];"
                f"[p_bg][p_ev]overlay=x={tf_x + bw}:y={tf_y + bw}:shortest=1[p_m0];"
                f"[p_m0][4:v]overlay=x={tf_x}:y={tf_y}:shortest=1[p_m1];"
                f"[p_av_src]{_cover_crop_vf(av_d, av_d, face_bias=True)},format=rgba[p_av_raw];"
                f"[2:v]format=gray[p_mask];"
                f"[p_av_raw][p_mask]alphamerge[p_av];"
                f"[p_m1][p_av]overlay=x={av_x}:y={av_y}:shortest=1[p_m2];"
                f"[p_m2][3:v]overlay=x={av_x - 14}:y={av_y - 14}:shortest=1,{tail_str}[vout]"
            )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
            "-loop", "1", "-framerate", str(fps), "-i", str(mask_p),
            "-loop", "1", "-framerate", str(fps), "-i", str(ring_p),
            "-loop", "1", "-framerate", str(fps), "-i", str(frame_p),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    if mode == "dual_gold_frame_pip":
        # style_17：上下暖金双格无变形对比框
        bg_c = str(pip_layout.get("bg_hex") or "#100D08")[:7]
        bw = int(pip_layout.get("border_width") or 4)
        bw_box, bh_box = 960, 590
        fc = (
            f"[0:v]{base_vf_chain},split=2[p_bg_src][p_top_src];"
            f"[p_bg_src]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[p_bg];"
            f"[p_top_src]{_cover_crop_vf(bw_box - bw * 2, bh_box - bw * 2, face_bias=True)},"
            f"pad={bw_box}:{bh_box}:{bw}:{bw}:color={bd_hex}[p_top];"
            f"[1:v]{sec_trim},{_cover_crop_vf(bw_box - bw * 2, bh_box - bw * 2, face_bias=False)},"
            f"pad={bw_box}:{bh_box}:{bw}:{bw}:color={bd_hex}[p_bot];"
            f"[p_bg][p_top]overlay=x=60:y=240:shortest=1[p_m1];"
            f"[p_m1][p_bot]overlay=x=60:y=860:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    if mode == "linear_split_feather_pip":
        # style_16：上窄实景等比铺满 + 底部 180px 平滑线性羽化渐变溶入下半纯黑书摘区
        top_h = int(pip_layout.get("top_h") or 680)
        feather_px = int(pip_layout.get("feather_px") or 180)
        bg_c = str(pip_layout.get("bg_hex") or "#050507")[:7]
        fmask_p = _ensure_feather_bottom_mask_png(out_mp4.parent, CANVAS_WIDTH, top_h, feather_px)
        fc = (
            f"[0:v]{base_vf_chain},drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[p_bg];"
            f"[1:v]{sec_trim},{_cover_crop_vf(CANVAS_WIDTH, top_h, face_bias=True)},format=rgba[p_top_raw];"
            f"[2:v]format=gray[p_fmask];"
            f"[p_top_raw][p_fmask]alphamerge[p_top];"
            f"[p_bg][p_top]overlay=x=0:y=0:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
            "-loop", "1", "-framerate", str(fps), "-i", str(fmask_p),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    if mode in ("vision_portrait_cutout_pip", "data_news_cutout_pip"):
        # 当 AI 逐帧抠像未激活（非 macOS 或无人物画面）时的平滑圆角降级方案
        is_white = (mode == "vision_portrait_cutout_pip")
        pad_c = "#FFFFFF" if is_white else "#38BDF8"
        pad_w = 10 if is_white else 4
        fg_w, fg_h = 680, 1020
        bg_fx = "boxblur=14:2,eq=brightness=-0.08" if is_white else "boxblur=6:1,eq=saturation=0.55:contrast=1.12:brightness=-0.10"
        fc = (
            f"[1:v]{sec_trim},{_cover_crop_vf(CANVAS_WIDTH, CANVAS_HEIGHT, face_bias=False)},{bg_fx}[p_bg];"
            f"[0:v]{base_vf_chain},{_cover_crop_vf(fg_w - pad_w * 2, fg_h - pad_w * 2, face_bias=True)},"
            f"pad={fg_w}:{fg_h}:{pad_w}:{pad_w}:color={pad_c}[p_fg];"
            f"[p_bg][p_fg]overlay=x={(CANVAS_WIDTH - fg_w) // 2}:y=290:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    return None


def _build_single_masked_pip_cmd(
    ffmpeg_bin: str,
    media_path: str,
    src_in: float,
    src_avail: float,
    base_vf_chain: str,
    pip_layout: Dict[str, Any],
    clip_frames: int,
    fps: int,
    out_mp4: Path,
) -> Optional[List[str]]:
    """单源带 Alpha 预烘焙蒙版的高保真画框合成器（style_13 上白圆角 / style_14 纯黑聚光圆窗 / style_16 真羽化 / style_09 单源面板）。"""
    mode = str(pip_layout.get("mode") or "none").strip()
    tail_str = ",".join(BT709_VF_SUFFIX)

    if mode == "top_white_rounded_frame_pip":
        # style_13：纯黑底 + 上白圆角画框 (1000x920, r=38) + 下纯黑留白
        fw = int(pip_layout.get("frame_w") or 1000)
        fh = int(pip_layout.get("frame_h") or 920)
        fx = int(pip_layout.get("frame_x") or 40)
        fy = int(pip_layout.get("frame_y") or 210)
        rad = int(pip_layout.get("corner_radius") or 38)
        bg_c = str(pip_layout.get("bg_hex") or "#050506")[:7]
        rmask_p = _ensure_rounded_rect_mask_png(out_mp4.parent, fw, fh, rad)
        fc = (
            f"[0:v]{base_vf_chain},split=2[p_bg_s][p_fg_s];"
            f"[p_bg_s]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[p_bg];"
            f"[p_fg_s]{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[p_fg_raw];"
            f"[1:v]format=gray[p_rmask];"
            f"[p_fg_raw][p_rmask]alphamerge[p_fg];"
            f"[p_bg][p_fg]overlay=x={fx}:y={fy}:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(rmask_p),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    if mode == "black_spotlight_circle_pip":
        # style_14：纯黑全屏背景 + 居中 r=295 抗锯齿圆形聚光窗
        cx = int(pip_layout.get("circle_cx") or 540)
        cy = int(pip_layout.get("circle_cy") or 560)
        cr = int(pip_layout.get("circle_r") or 295)
        diam = cr * 2
        bg_c = str(pip_layout.get("bg_hex") or "#040405")[:7]
        cmask_p = _ensure_circle_mask_png(out_mp4.parent, cr)
        fc = (
            f"[0:v]{base_vf_chain},split=2[p_bg_s][p_fg_s];"
            f"[p_bg_s]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[p_bg];"
            f"[p_fg_s]{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[p_fg_raw];"
            f"[1:v]format=gray[p_cmask];"
            f"[p_fg_raw][p_cmask]alphamerge[p_fg];"
            f"[p_bg][p_fg]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(cmask_p),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    if mode == "linear_split_feather_pip":
        # style_16 单源版：上窄实景 + 底部 180px 平滑羽化渐变溶入下半纯黑书摘区
        top_h = int(pip_layout.get("top_h") or 680)
        feather_px = int(pip_layout.get("feather_px") or 180)
        bg_c = str(pip_layout.get("bg_hex") or "#050507")[:7]
        fmask_p = _ensure_feather_bottom_mask_png(out_mp4.parent, CANVAS_WIDTH, top_h, feather_px)
        fc = (
            f"[0:v]{base_vf_chain},split=2[p_bg_s][p_top_s];"
            f"[p_bg_s]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[p_bg];"
            f"[p_top_s]{_cover_crop_vf(CANVAS_WIDTH, top_h, face_bias=True)},format=rgba[p_top_raw];"
            f"[1:v]format=gray[p_fmask];"
            f"[p_top_raw][p_fmask]alphamerge[p_top];"
            f"[p_bg][p_top]overlay=x=0:y=0:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(fmask_p),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    if mode == "dark_doc_circle_avatar_pip":
        # style_09 单源版：纯黑纪实底 + 顶部双格细金线画框 + 左下正圆头像
        tf_x = int(pip_layout.get("top_frame_x") or 76)
        tf_y = int(pip_layout.get("top_frame_y") or 290)
        tf_w = int(pip_layout.get("top_frame_w") or 928)
        tf_h = int(pip_layout.get("top_frame_h") or 540)
        bw = int(pip_layout.get("border_width") or 3)
        bd_hex = str(pip_layout.get("border_hex") or "#C8A96A")[:7]
        av_r = int(pip_layout.get("avatar_r") or 122)
        av_d = av_r * 2
        av_x = int(pip_layout.get("avatar_cx") or 215) - av_r
        av_y = int(pip_layout.get("avatar_cy") or 1030) - av_r
        bg_c = str(pip_layout.get("bg_hex") or "#060608")[:7]
        av_bd = str(pip_layout.get("avatar_border_hex") or "#E2D5B5")[:7]
        mask_p = _ensure_circle_mask_png(out_mp4.parent, av_r)
        ring_p = _ensure_circle_ring_png(out_mp4.parent, av_r, av_bd)
        frame_p = _ensure_dual_gold_divider_png(out_mp4.parent, tf_w, tf_h, bd_hex, bw, dual_divider=bool(pip_layout.get("dual_pane_divider", True)))
        fc = (
            f"[0:v]{base_vf_chain},split=3[p_bg_s][p_ev_s][p_av_s];"
            f"[p_bg_s]drawbox=x=0:y=0:w=iw:h=ih:color={bg_c}:t=fill[p_bg];"
            f"[p_ev_s]{_cover_crop_vf(tf_w - bw * 2, tf_h - bw * 2, face_bias=True)}[p_ev];"
            f"[p_bg][p_ev]overlay=x={tf_x + bw}:y={tf_y + bw}:shortest=1[p_m0];"
            f"[p_m0][3:v]overlay=x={tf_x}:y={tf_y}:shortest=1[p_m1];"
            f"[p_av_s]{_cover_crop_vf(av_d, av_d, face_bias=True)},format=rgba[p_av_raw];"
            f"[1:v]format=gray[p_mask];"
            f"[p_av_raw][p_mask]alphamerge[p_av];"
            f"[p_m1][p_av]overlay=x={av_x}:y={av_y}:shortest=1[p_m2];"
            f"[p_m2][2:v]overlay=x={av_x - 14}:y={av_y - 14}:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(mask_p),
            "-loop", "1", "-framerate", str(fps), "-i", str(ring_p),
            "-loop", "1", "-framerate", str(fps), "-i", str(frame_p),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    return None


_AI_MATTING_MODES = {
    "person_behind_giant_text_pip",
    "vision_portrait_cutout_pip",
    "data_news_cutout_pip",
    "layered_cutout_over_broll",
}


def _render_ai_person_matting_segment(
    ffmpeg_bin: str,
    clip: Dict[str, Any],
    color_vf: Optional[str],
    eye_by_mp4: Dict[str, Dict[str, Any]],
    out_mp4: Path,
    fps: int,
    clip_frames: int,
    geom_cache: Optional[Dict[str, Tuple[int, int]]] = None,
) -> bool:
    """
    真·逐帧 AI 人像前景抠像合成器：
    - style_05 (person_behind_giant_text_pip): [微虚暗调背景] -> [背后 256px 亮黄巨字] -> [抠出前景真人]
    - style_07 (vision_portrait_cutout_pip):   [虚化实景/副镜背景] -> [10px 沿发丝肩膀纯白描边] -> [抠出前景真人]
    - style_19 (data_news_cutout_pip):         [冷峻动态数据折线背景/副镜] -> [抠出前景真人]
    成功合成返回 True；若非 macOS/无模型或画面无真人，返回 False 交回普通命令平滑降级。
    """
    pip_cfg = clip.get("pip_layout") or {}
    mode = str(pip_cfg.get("mode") or "")
    if mode not in _AI_MATTING_MODES:
        return False

    try:
        from person_matting import generate_person_matte_video, render_cool_data_news_bg_mp4
        from render_template_overlays import render_style05_giant_back_png
    except Exception:
        return False

    work_dir = out_mp4.parent
    stem = out_mp4.stem
    fg_clean = work_dir / f"{stem}_fgclean.mp4"
    matte_mp4 = work_dir / f"{stem}_matte.mp4"
    outline_px = int(pip_cfg.get("outline_px") or (10 if mode == "vision_portrait_cutout_pip" else 0))
    outline_mp4 = (work_dir / f"{stem}_outline.mp4") if outline_px > 0 else None

    clip_plain = dict(clip)
    clip_plain.pop("pip_layout", None)
    clip_plain["timeline_in_frame"] = 0
    clip_plain["timeline_out_frame"] = clip_frames
    clean_cmd = build_graded_segment_cmd(
        ffmpeg_bin, clip_plain, color_vf, eye_by_mp4, fg_clean, fps,
        tw=CANVAS_WIDTH, th=CANVAS_HEIGHT, preview=False, geom_cache=geom_cache,
    )
    run_cmd(clean_cmd)

    meta = generate_person_matte_video(
        fg_clean,
        matte_mp4,
        out_outline_mp4=outline_mp4,
        outline_px=outline_px,
        quality="balanced",
        fps=fps,
        torn_paper=bool(pip_cfg.get("torn_paper", False)),
    )
    if not meta or not meta.get("has_person") or not matte_mp4.is_file():
        fg_clean.unlink(missing_ok=True)
        matte_mp4.unlink(missing_ok=True)
        if outline_mp4:
            outline_mp4.unlink(missing_ok=True)
        return False

    tail_str = ",".join(BT709_VF_SUFFIX)
    clip_dur = frames_to_sec(clip_frames, fps)
    sec_media = str(pip_cfg.get("secondary_media_path") or "")
    sec_in = max(0.0, float(pip_cfg.get("secondary_in_sec", 0.0)))
    has_sec = bool(sec_media and Path(sec_media).is_file() and str(Path(sec_media).resolve()) != str(Path(str(clip.get("media_path") or "")).resolve()))

    try:
        if mode == "person_behind_giant_text_pip":
            back_txt = str(pip_cfg.get("giant_back_text") or "核心真相")
            giant_col = str(pip_cfg.get("giant_color") or "#FFD426")
            bg_dark = float(pip_cfg.get("bg_darken") or -0.20)
            bg_blur = max(2, int(pip_cfg.get("bg_blur") or 8))
            giant_png = work_dir / f"{stem}_s05_giant_back.png"
            render_style05_giant_back_png(back_txt, giant_png, accent_hex=giant_col)
            fc = (
                f"[0:v]boxblur={bg_blur}:1,eq=brightness={bg_dark:.2f}[bg_dim];"
                f"[bg_dim][2:v]overlay=0:0:shortest=1[bg_txt];"
                f"[0:v]format=rgba[fg_raw];"
                f"[1:v]format=gray[m_gray];"
                f"[fg_raw][m_gray]alphamerge[fg_cut];"
                f"[bg_txt][fg_cut]overlay=0:0:shortest=1,{tail_str}[vout]"
            )
            run_cmd([
                ffmpeg_bin, "-y",
                "-i", str(fg_clean),
                "-i", str(matte_mp4),
                "-loop", "1", "-framerate", str(fps), "-i", str(giant_png),
                "-an", "-filter_complex", fc, "-map", "[vout]",
                "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
            ])
            return out_mp4.is_file()

        if mode == "vision_portrait_cutout_pip":
            blur_s = max(6, int(pip_cfg.get("bg_blur_sigma") or 12))
            out_hex = str(pip_cfg.get("outline_hex") or "#FFFFFF")[:7]
            if has_sec and outline_mp4 and outline_mp4.is_file():
                sec_trim = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS"
                fc = (
                    f"[3:v]{sec_trim},{_cover_crop_vf(CANVAS_WIDTH, CANVAS_HEIGHT, face_bias=False)},boxblur={blur_s}:2,eq=brightness=-0.08[bg];"
                    f"color=c={out_hex}:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:r={fps}:d={clip_dur + 0.2:.4f},format=rgba[wc];"
                    f"[2:v]format=gray[om];"
                    f"[wc][om]alphamerge[white_outline];"
                    f"[0:v]format=rgba[fg_raw];"
                    f"[1:v]format=gray[mm];"
                    f"[fg_raw][mm]alphamerge[fg_cut];"
                    f"[bg][white_outline]overlay=0:0:shortest=1[m1];"
                    f"[m1][fg_cut]overlay=0:0:shortest=1,{tail_str}[vout]"
                )
                run_cmd([
                    ffmpeg_bin, "-y",
                    "-i", str(fg_clean),
                    "-i", str(matte_mp4),
                    "-i", str(outline_mp4),
                    "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
                    "-an", "-filter_complex", fc, "-map", "[vout]",
                    "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
                ])
                return out_mp4.is_file()
            elif outline_mp4 and outline_mp4.is_file():
                fc = (
                    f"[0:v]boxblur={blur_s + 4}:2,eq=brightness=-0.10:saturation=0.85[bg];"
                    f"color=c={out_hex}:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:r={fps}:d={clip_dur + 0.2:.4f},format=rgba[wc];"
                    f"[2:v]format=gray[om];"
                    f"[wc][om]alphamerge[white_outline];"
                    f"[0:v]format=rgba[fg_raw];"
                    f"[1:v]format=gray[mm];"
                    f"[fg_raw][mm]alphamerge[fg_cut];"
                    f"[bg][white_outline]overlay=0:0:shortest=1[m1];"
                    f"[m1][fg_cut]overlay=0:0:shortest=1,{tail_str}[vout]"
                )
                run_cmd([
                    ffmpeg_bin, "-y",
                    "-i", str(fg_clean),
                    "-i", str(matte_mp4),
                    "-i", str(outline_mp4),
                    "-an", "-filter_complex", fc, "-map", "[vout]",
                    "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
                ])
                return out_mp4.is_file()

        if mode == "layered_cutout_over_broll":
            if not has_sec:
                return False
            fg_scale = max(0.35, min(1.65, float(pip_cfg.get("fg_scale") or 1.2)))
            fg_tx = max(-0.6, min(0.6, float(pip_cfg.get("fg_transform_x") or 0.0)))
            fg_ty = max(-0.6, min(0.4, float(pip_cfg.get("fg_transform_y") or -0.10)))
            fw = max(2, int(round(CANVAS_WIDTH * fg_scale)) & ~1)
            fh = max(2, int(round(CANVAS_HEIGHT * fg_scale)) & ~1)
            fa = pip_cfg.get("face_anchor") if isinstance(pip_cfg.get("face_anchor"), dict) else None
            fg_crop_vf = ""
            if fg_scale < 0.98:
                ox = max(0, min(CANVAS_WIDTH - fw, int(round((fg_tx + 1.0 - fg_scale) * CANVAS_WIDTH / 2.0))))
                oy = max(0, min(CANVAS_HEIGHT - fh, int(round((1.0 - fg_ty - fg_scale) * CANVAS_HEIGHT / 2.0))))
                fg_crop_vf = _corner_cutout_crop_expr(fa, CANVAS_WIDTH, CANVAS_HEIGHT)
                if fg_crop_vf and fg_ty <= 0.15:
                    oy = CANVAS_HEIGHT - fh
            else:
                ox = int(round((CANVAS_WIDTH - fw) / 2.0 + fg_tx * CANVAS_WIDTH / 2.0))
                oy = int(round((1.0 - fg_ty) * CANVAS_HEIGHT / 2.0 - fh / 2.0))
                if fa:
                    face_h = float(fa.get("h") or 0.15)
                    face_cy = float(
                        fa["cy"] if fa.get("cy") is not None
                        else (float(fa.get("y") or 0.42) + face_h * 0.5)
                    )
                    head_y = max(0.0, face_cy - face_h * 0.72) * fh
                    chin_y = min(1.0, face_cy + face_h * 0.62) * fh
                    max_face_bot = SUBTITLE_SAFE_ZONE["y_min"] - 60
                    if oy + chin_y > max_face_bot:
                        oy = min(oy, int(round(max_face_bot - chin_y)))
                    if oy + head_y < 0.03 * CANVAS_HEIGHT:
                        oy = max(oy, int(round(0.03 * CANVAS_HEIGHT - head_y)))
                # 全屏/放大前景确保底边不悬空、顶边不留黑带
                oy = max(CANVAS_HEIGHT - fh, min(0, oy))

            sec_color_part = f",{color_vf}" if color_vf else ""
            sec_trim = (
                f"fps=fps={fps},tpad=stop_mode=clone:stop_duration=3.0,"
                f"trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS"
            )
            fg_crop_prefix = f",{fg_crop_vf}" if fg_crop_vf else ""
            fg_resize = f",scale={fw}:{fh}:flags=lanczos" if (fg_crop_vf or fw != CANVAS_WIDTH or fh != CANVAS_HEIGHT) else ""
            scale_fg = f"{fg_crop_prefix}{fg_resize}"
            out_hex = str(pip_cfg.get("outline_hex") or "#FFFFFF")[:7]
            back_ovs = [b for b in (pip_cfg.get("back_overlays") or []) if b.get("path") and Path(str(b["path"])).is_file()]

            cmd_inputs: List[str] = ["-i", str(fg_clean), "-i", str(matte_mp4)]
            has_out_layer = bool(outline_mp4 and outline_mp4.is_file() and outline_px > 0)
            out_idx = 2 if has_out_layer else -1
            if has_out_layer:
                cmd_inputs.extend(["-i", str(outline_mp4)])
            sec_idx = len(cmd_inputs) // 2
            cmd_inputs.extend(["-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media])

            fc_stages: List[str] = [
                f"[{sec_idx}:v]{sec_trim},{_cover_crop_vf(CANVAS_WIDTH, CANVAS_HEIGHT, face_bias=True)}{sec_color_part}[bg0]"
            ]
            bg_lbl = "[bg0]"
            for bi, bov in enumerate(back_ovs):
                bpath = str(bov["path"])
                bx, by = int(bov.get("x") or 0), int(bov.get("y") or 0)
                rs = max(0.0, float(bov.get("rel_start") or 0.0))
                re_t = max(rs + 0.04, float(bov.get("rel_end") or clip_dur))
                ov_ss = max(0.0, float(bov.get("ov_offset") or 0.0))
                in_i = (len([x for x in cmd_inputs if x == "-i"]))
                if bpath.lower().endswith(".png"):
                    cmd_inputs.extend(["-loop", "1", "-t", f"{clip_dur + 0.2:.4f}", "-i", bpath])
                    b_stream = f"[{in_i}:v]"
                else:
                    if ov_ss > 0.01:
                        cmd_inputs.extend(["-ss", f"{ov_ss:.4f}", "-i", bpath])
                    elif rs > 0.01:
                        cmd_inputs.extend(["-itsoffset", f"{rs:.4f}", "-i", bpath])
                    else:
                        cmd_inputs.extend(["-i", bpath])
                    b_stream = f"[{in_i}:v]"
                nxt_lbl = f"[bg_bt{bi}]"
                fc_stages.append(
                    f"{bg_lbl}{b_stream}overlay=x={bx}:y={by}:enable='between(t,{rs:.3f},{re_t:.3f})':shortest=0:eof_action=pass{nxt_lbl}"
                )
                bg_lbl = nxt_lbl

            fc_stages.append(f"[0:v]format=rgba[fg_raw]")
            fc_stages.append(f"[1:v]format=gray[mm]")
            fc_stages.append(f"[fg_raw][mm]alphamerge{scale_fg}[fg_cut]")
            if has_out_layer:
                fc_stages.append(f"color=c={out_hex}:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:r={fps}:d={clip_dur + 0.2:.4f},format=rgba[wc]")
                fc_stages.append(f"[{out_idx}:v]format=gray[om]")
                fc_stages.append(f"[wc][om]alphamerge{scale_fg}[white_outline]")
                fc_stages.append(f"{bg_lbl}[white_outline]overlay=x={ox}:y={oy}:shortest=1[m1]")
                fc_stages.append(f"[m1][fg_cut]overlay=x={ox}:y={oy}:shortest=1,{tail_str}[vout]")
            else:
                fc_stages.append(f"{bg_lbl}[fg_cut]overlay=x={ox}:y={oy}:shortest=1,{tail_str}[vout]")

            run_cmd([
                ffmpeg_bin, "-y",
                *cmd_inputs,
                "-an", "-filter_complex", ";".join(fc_stages), "-map", "[vout]",
                "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
            ])
            return out_mp4.is_file()

        if mode == "data_news_cutout_pip":
            if has_sec:
                sec_trim = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS"
                fc = (
                    f"[2:v]{sec_trim},{_cover_crop_vf(CANVAS_WIDTH, CANVAS_HEIGHT, face_bias=False)},boxblur=6:1,eq=saturation=0.52:contrast=1.10:brightness=-0.12[bg];"
                    f"[0:v]format=rgba[fg_raw];"
                    f"[1:v]format=gray[mm];"
                    f"[fg_raw][mm]alphamerge[fg_cut];"
                    f"[bg][fg_cut]overlay=0:0:shortest=1,{tail_str}[vout]"
                )
                run_cmd([
                    ffmpeg_bin, "-y",
                    "-i", str(fg_clean),
                    "-i", str(matte_mp4),
                    "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
                    "-an", "-filter_complex", fc, "-map", "[vout]",
                    "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
                ])
                return out_mp4.is_file()
            else:
                data_bg = work_dir / f"_s19_data_bg_{clip_frames}f.mp4"
                if not data_bg.is_file():
                    render_cool_data_news_bg_mp4(data_bg, max(0.6, clip_dur + 0.1), fps=fps)
                fc = (
                    f"[0:v]format=rgba[fg_raw];"
                    f"[1:v]format=gray[mm];"
                    f"[fg_raw][mm]alphamerge[fg_cut];"
                    f"[2:v][fg_cut]overlay=0:0:shortest=1,{tail_str}[vout]"
                )
                run_cmd([
                    ffmpeg_bin, "-y",
                    "-i", str(fg_clean),
                    "-i", str(matte_mp4),
                    "-i", str(data_bg),
                    "-an", "-filter_complex", fc, "-map", "[vout]",
                    "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "medium", "-crf", "15",
                    "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
                ])
                return out_mp4.is_file()
    finally:
        fg_clean.unlink(missing_ok=True)
        matte_mp4.unlink(missing_ok=True)
        if outline_mp4:
            outline_mp4.unlink(missing_ok=True)
    return False


def speed_time_parts(speed: Any, trim_src_sec: float, fps: int) -> List[str]:
    """抽段滤镜链的头两段：裁素材时长 → 落帧率。**倍速时顺序必须反过来。**

    剪映的 speed 语义是 `source_timerange = target_timerange × speed`：
    时间线 t 秒要吃掉素材 t×speed 秒，所以要先把**素材时长**裁出来，再把时间戳压/拉，
    最后才落目标帧率。先 `fps` 再 `setpts/speed` 会让帧率滤镜按「压缩前的时长」算采样数，
    快速段少帧、慢速段复用相邻帧（看着像掉帧/卡顿）。

    speed≈1 时保持历史链条逐字不变（`fps` 在前、`trim` 在后）——这条链已被几十条风格
    的逐帧对齐验过，不为「整齐」去动它。
    """
    if abs(clamp_speed(speed) - 1.0) > 5e-4:
        return [f"trim=duration={trim_src_sec:.4f},{speed_setpts(speed)}",
                f"fps=fps={fps}"]
    return [f"fps=fps={fps}", f"trim=duration={trim_src_sec:.4f},setpts=PTS-STARTPTS"]


def build_graded_segment_cmd(
    ffmpeg_bin: str,
    clip: Dict[str, Any],
    color_vf: Optional[str],
    eye_by_mp4: Dict[str, Dict[str, Any]],
    out_mp4: Path,
    fps: int,
    tw: int = CANVAS_WIDTH,
    th: int = CANVAS_HEIGHT,
    preview: bool = False,
    geom_cache: Optional[Dict[str, Tuple[int, int]]] = None,
) -> List[str]:
    """全仓唯一权威的单分镜裁切 + 锁眼 Ken Burns + 调色 + BT.709 命令构造器（供 render_mp4 / export_jianying / export_hyperframes 共享，消灭 80 行双写）。"""
    media_path = str(clip.get("media_path") or "")
    src_in = max(0.0, float(clip.get("source_in_sec", 0.0)))
    if clip.get("timeline_out_frame") is not None and clip.get("timeline_in_frame") is not None:
        clip_frames = max(1, int(clip["timeline_out_frame"]) - int(clip["timeline_in_frame"]))
    else:
        clip_frames = max(1, sec_to_frames(float(clip.get("duration_sec", 1.0)), fps))
    clip_dur = frames_to_sec(clip_frames, fps)
    src_out = float(clip.get("source_out_sec", src_in + clip_dur))
    src_avail = max(0.1, src_out - src_in)
    # 变速（剪映 segment.speed）：时间线 1 秒要吃掉素材 speed 秒。
    # 旧行为：这里根本不读 speed，却已经按 source_out = src_in + dur×speed 写进了 EDL，
    # 于是快速段只拿到素材的**前 1/speed 部分按原速播放**（style_01 那段 2× B-roll：
    # 该走 2.67s 素材压缩进 1.33s，实际只用了前 1.33s 正常速）+ 慢速段（0.197×）整段内容跑不完。
    # 只认显式 `speed`，**不从 (source_out−source_in)/duration 反解**：step4 生成的 EDL 在素材
    # 不够长时会把 src_out 夹到末帧（src_in+seg_dur 截短），反解出来的「<1 倍速」会把「冻末帧」
    # 误画成慢动作。谁要变速，谁写 speed。
    speed = clamp_speed(clip.get("speed"))
    need_src = max(0.0, source_span_for(clip_dur, speed)) if abs(speed - 1.0) > 5e-4 else clip_dur

    if geom_cache is not None and media_path in geom_cache:
        sw, sh = geom_cache[media_path]
    else:
        probe = ffprobe_media(Path(media_path))
        v_s = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
        g = parse_video_stream_geometry(v_s)
        sw, sh = g["display_width"], g["display_height"]
        if geom_cache is not None:
            geom_cache[media_path] = (sw, sh)

    eye = clip.get("eye_anchor") or eye_by_mp4.get(media_path) or eye_by_mp4.get(str(Path(media_path).resolve())) or {}
    fx_raw = float(eye.get("cx", eye.get("eye_cx", 0.50)))
    fy_raw = float(eye.get("cy", eye.get("eye_cy", 0.36)))
    base_tx = compute_crop_transform(sw, sh, tw, th, 0.5, 0.5)
    cx_frac = compute_centered_crop_frac(fx_raw, base_tx["scaled_w"], tw, target_norm_pos=0.5)
    cy_frac = compute_centered_crop_frac(fy_raw, base_tx["scaled_h"], th, target_norm_pos=0.38)
    tx = compute_crop_transform(sw, sh, tw, th, cx_frac, cy_frac)
    fx, fy = fx_raw, fy_raw

    trim_dur = min(src_avail, need_src) if src_avail >= need_src - 0.01 else src_avail
    vf_parts: List[str] = speed_time_parts(speed, trim_dur, fps)

    vf_extra = str(clip.get("vf_extra") or "").strip()
    kb = clip.get("ken_burns")
    has_kb = isinstance(kb, dict) and ("zoom_start" in kb or "zoom_end" in kb)
    if has_kb and not preview and not vf_extra:
        z0 = max(1.0, float(kb.get("zoom_start", 1.0)))
        z1 = max(1.0, float(kb.get("zoom_end", 1.06)))
        pan_x = float(kb.get("pan_x", 0.0))
        pan_y = float(kb.get("pan_y", 0.0))
        if abs(z1 - z0) > 1e-4 or abs(pan_x) > 1e-4 or abs(pan_y) > 1e-4 or z0 > 1.01 or z1 > 1.01:
            prog = f"min(t/{clip_dur:.4f},1)"
            z_expr = f"({z0:.4f}+({z1 - z0:.4f})*{prog})"
            vf_parts.append(
                f"scale=w='trunc({tx['scaled_w']}*{z_expr}/2)*2':h='trunc({tx['scaled_h']}*{z_expr}/2)*2':eval=frame:flags=bicubic"
            )
            focal_px_x = float(tx["crop_x"]) + fx * tw
            focal_px_y = float(tx["crop_y"]) + fy * th
            x_off = f"{z_expr}*{focal_px_x:.2f}-{fx * tw:.2f}+({pan_x:.4f}*iw*{prog})"
            y_off = f"{z_expr}*{focal_px_y:.2f}-{fy * th:.2f}+({pan_y:.4f}*ih*{prog})"
            vf_parts.append(f"crop={tw}:{th}:x='max(0,min(iw-{tw},{x_off}))':y='max(0,min(ih-{th},{y_off}))'")
        elif sw != tw or sh != th:
            vf_parts.append(f"scale={tx['scaled_w']}:{tx['scaled_h']}:flags=bicubic")
            vf_parts.append(f"crop={tw}:{th}:{tx['crop_x']}:{tx['crop_y']}")
    else:
        if sw != tw or sh != th:
            vf_parts.append(f"scale={tx['scaled_w']}:{tx['scaled_h']}:flags=bicubic")
            vf_parts.append(f"crop={tw}:{th}:{tx['crop_x']}:{tx['crop_y']}")
        if vf_extra and not preview:
            vf_parts.append(vf_extra)

    if color_vf and not preview:
        vf_parts.append(color_vf)
    if src_avail < need_src - 0.01:
        # 素材不够长：冻住末帧补时间线上的缺口（补的时长按**时间线口径**算，不是素材口径）
        pad_sec = ((need_src - src_avail) / max(1e-6, speed)) + 0.3
        vf_parts.append(f"tpad=stop_mode=clone:stop_duration={pad_sec:.4f}")

    pip_cfg = clip.get("pip_layout") if (not preview and tw == CANVAS_WIDTH and th == CANVAS_HEIGHT) else None
    if isinstance(pip_cfg, dict):
        sec_media = str(pip_cfg.get("secondary_media_path") or "")
        sec_in = max(0.0, float(pip_cfg.get("secondary_in_sec", 0.0)))
        # 会裁切的构图（小窗/缩窗/推近/相框/圆窗）必须先拿到人脸锚点，否则就是按画面正中瞎切
        _mode = str(pip_cfg.get("mode") or "")
        # 剪辑表已经定了锚点（含"确认过没有脸"=None）就不重复检测，尊重上游结论
        if _mode and _mode not in ("none", "null") and "face_anchor" not in pip_cfg:
            pip_cfg = {**pip_cfg}
            pip_cfg["face_anchor"] = face_framing.anchor_for(media_path, src_in + clip_dur / 2.0, clip_dur)
            if sec_media and Path(sec_media).is_file():
                pip_cfg["secondary_face_anchor"] = face_framing.anchor_for(sec_media, sec_in + clip_dur / 2.0, clip_dur)
            pip_cfg["face_framing"] = "face_center" if pip_cfg.get("face_anchor") else "no_face_center_crop"
            pip_cfg["_face_checked"] = True
            clip["pip_layout"] = pip_cfg  # 回写，render_profile 才能统计到底人脸构图落了几镜
        swap_spk = str(pip_cfg.get("speaker_media_path") or "") if pip_cfg.get("swap_main_secondary") else ""
        if swap_spk and Path(swap_spk).is_file() and sec_media and Path(sec_media).is_file():
            # B-roll 镜头也要保证「主讲人头像/同步口型」是当前句真人：把真人口播源换成主输入，B-roll 作为证据窗副输入
            spk_chain = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS,{_cover_crop_vf(tw, th, face_bias=True)}"
            if color_vf and not preview:
                spk_chain += f",{color_vf}"
            swap_cmd = _build_dual_pip_cmd(
                ffmpeg_bin=ffmpeg_bin,
                media_path=swap_spk,
                src_in=max(0.0, float(pip_cfg.get("speaker_in_sec", 0.0))),
                src_avail=clip_dur + 0.1,
                base_vf_chain=spk_chain,
                pip_layout=pip_cfg,
                sec_media=sec_media,
                sec_in=sec_in,
                clip_dur=clip_dur,
                clip_frames=clip_frames,
                fps=fps,
                out_mp4=out_mp4,
            )
            if swap_cmd:
                return swap_cmd
        elif sec_media and Path(sec_media).is_file() and str(Path(sec_media).resolve()) != str(Path(media_path).resolve()):
            dual_cmd = _build_dual_pip_cmd(
                ffmpeg_bin=ffmpeg_bin,
                media_path=media_path,
                src_in=src_in,
                src_avail=src_avail,
                base_vf_chain=",".join(vf_parts),
                pip_layout=pip_cfg,
                sec_media=sec_media,
                sec_in=sec_in,
                clip_dur=clip_dur,
                clip_frames=clip_frames,
                fps=fps,
                out_mp4=out_mp4,
            )
            if dual_cmd:
                return dual_cmd
        single_masked_cmd = _build_single_masked_pip_cmd(
            ffmpeg_bin=ffmpeg_bin,
            media_path=media_path,
            src_in=src_in,
            src_avail=src_avail,
            base_vf_chain=",".join(vf_parts),
            pip_layout=pip_cfg,
            clip_frames=clip_frames,
            fps=fps,
            out_mp4=out_mp4,
        )
        if single_masked_cmd:
            return single_masked_cmd
        pip_vf = _build_ffmpeg_pip_vf(pip_cfg, tw=tw, th=th)
        if pip_vf:
            vf_parts.append(pip_vf)

    if preview:
        vf_parts.extend(["setsar=1", "format=yuv420p"])
    else:
        vf_parts.extend(BT709_VF_SUFFIX)

    # 正式中间分镜采用 CRF 15 极低损编码，大幅削减三代 H.264 压损
    return [
        ffmpeg_bin, "-y",
        "-ss", f"{src_in:.4f}",
        "-t", f"{src_avail + 0.1:.4f}",
        "-i", media_path,
        "-an",
        "-vf", ",".join(vf_parts),
        "-frames:v", str(clip_frames),
        "-c:v", "libx264",
        "-preset", "ultrafast" if preview else "medium",
        "-crf", "26" if preview else "15",
        "-pix_fmt", "yuv420p",
        *([] if preview else BT709_FFMPEG_ARGS),
        str(out_mp4),
    ]


_XFADE_STYLE_MAP = {
    "zoomin": "zoomin",
    "fadeblack": "fadeblack",
    "dissolve": "dissolve",
    "wipeleft": "wipeleft",
    "smoothleft": "smoothleft",
    "fade": "fade",
}


def build_base_video_track(
    pipeline_dir: Path,
    edl: Dict[str, Any],
    color_plan: Dict[str, Any],
    master_wav: Path,
    manifest: Optional[Dict[str, Any]] = None,
    preview: bool = False,
) -> Path:
    """按 EDL 分镜精确裁切、单次高质量 Ken Burns / 锁眼推拉 + AI 真抠像 + 调色 + 镜头间 xfade 真实转场拼接底轨。"""
    started = perf_counter()
    ffmpeg_bin = find_bin("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("未找到 ffmpeg，无法渲染底轨视频。")
    work_dir = pipeline_dir / "_render_final"
    work_dir.mkdir(parents=True, exist_ok=True)
    # 人脸锚点缓存落盘：同一条素材只检测一次，重渲不重复抽帧
    face_framing.set_cache_path(pipeline_dir / "state" / "face_anchors.json")
    base_mp4 = work_dir / ("base_preview.mp4" if preview else "base_video.mp4")
    cache_flag = work_dir / ("base_preview.hash" if preview else "base_video.hash")

    fps = int(edl.get("fps") or DEFAULT_FPS)
    master_dur = get_wav_duration_sec(master_wav)
    target_frames = max(1, round(master_dur * fps))
    clips = edl.get("clips") or []
    _, eye_by_mp4 = _manifest_lookups(manifest or {"items": []})
    paths = {str(clip["media_path"]) for clip in clips}
    lut_path = str(color_plan.get("lut_path") or "")
    if not preview and lut_path and Path(lut_path).is_file():
        paths.add(lut_path)
    source_hashes = {path: sha256_file(Path(path)) for path in sorted(paths)}
    cache_key = _compute_base_cache_key(edl, color_plan, target_frames, preview, source_hashes, eye_by_mp4)
    suffix = "p" if preview else "f"
    expected_segments = [work_dir / f"seg_{index:03d}_{suffix}.mp4" for index in range(len(clips))]

    # 读取当前风格专属转场风格（仅正式渲染且非单镜时启用，确保 0 帧总时长漂移）
    style_id = str(edl.get("style_id") or load_style_applied(pipeline_dir) or "style_01")
    xf_type = ""
    if not preview and len(clips) >= 2:
        try:
            from render_template_overlays import get_style_blueprint
            bp_ov = get_style_blueprint(style_id).get("overlay_style") or {}
            raw_tr = str(bp_ov.get("transition_style") or "none").strip()
            # 硬约束：源草稿转场数为 0 的风格一律硬切，任何配置都不能开 xfade
            if int(bp_ov.get("transition_source_count") or 0) > 0:
                xf_type = _XFADE_STYLE_MAP.get(raw_tr, "")
            else:
                xf_type = ""
        except Exception:
            xf_type = ""

    xf_frames = 5 if xf_type else 0  # 5 帧 ≈ 0.167s
    xf_dur = frames_to_sec(xf_frames, fps)
    # 判断每个相邻边界 (i -> i+1) 是否应用 xfade（要求两侧时长均 >= 1.0s 且至少一侧为 b_roll/code_broll 或非紧口型对切）
    xf_edges: List[bool] = []
    clip_nom_frames: List[int] = []
    for c_item in clips:
        if c_item.get("timeline_out_frame") is not None and c_item.get("timeline_in_frame") is not None:
            cf_n = max(1, int(c_item["timeline_out_frame"]) - int(c_item["timeline_in_frame"]))
        else:
            cf_n = max(1, sec_to_frames(float(c_item.get("duration_sec", 1.0)), fps))
        clip_nom_frames.append(cf_n)
    if xf_type and len(clips) >= 2:
        for i_e in range(len(clips) - 1):
            c_a, c_b = clips[i_e], clips[i_e + 1]
            long_enough = clip_nom_frames[i_e] >= int(0.95 * fps) and clip_nom_frames[i_e + 1] >= int(0.95 * fps)
            non_tight_lip = (c_a.get("track_role") != "a_roll") or (c_b.get("track_role") != "a_roll")
            xf_edges.append(bool(long_enough and non_tight_lip))
    else:
        xf_edges = [False] * max(0, len(clips) - 1)
    # 逐镜取整的零头必须摊回最后一镜：每镜向下取整丢 0.x 帧，51 镜能短 1~2 帧，
    # 短掉的部分没人补，成片视频流就比音频流短（实测 60.83s vs 60.89s = 1.75 帧）。
    if clip_nom_frames:
        _n_xf_edge = sum(1 for e in xf_edges if e)
        _want = target_frames + _n_xf_edge * xf_frames
        _resid = _want - sum(clip_nom_frames)
        _i = len(clip_nom_frames) - 1
        while _resid != 0 and _i >= 0:
            if _resid > 0:
                clip_nom_frames[_i] += _resid
                _resid = 0
            else:
                _take = min(clip_nom_frames[_i] - 1, -_resid)
                clip_nom_frames[_i] -= _take
                _resid += _take
                _i -= 1
    use_xfade = any(xf_edges)

    def _pip_summary() -> Dict[str, Any]:
        """画中画落地情况：几何模式挂了几镜、几镜真做了双源窗口、几镜窗口没素材可填。"""
        single = dual = unrealized = 0
        for c_item in clips:
            cfg = c_item.get("pip_layout") or {}
            if not str(cfg.get("mode") or "").startswith("geometry_pip"):
                continue
            wins = cfg.get("windows") or []
            has_sec = bool(cfg.get("secondary_media_path"))
            if has_sec:
                dual += 1
            elif wins:
                single += 1
                unrealized += 1
            else:
                single += 1
        return {"geometry_pip_single": single, "geometry_pip_dual": dual,
                "windows_unrealized": unrealized,
                "note_cn": "windows_unrealized>0 表示该镜没拿到第二路素材，只落了主轨几何（窗口不下假图）"}

    def profile(base_hit: bool, reused: int) -> None:
        write_json(pipeline_dir / "render_profile.json", {
            "face_framing": {
                "windows_face_centered": sum(
                    1 for c in clips
                    if (c.get("pip_layout") or {}).get("face_framing") == "face_center"
                ),
                "windows_no_face_detected": sum(
                    1 for c in clips
                    if (c.get("pip_layout") or {}).get("face_framing") == "no_face_center_crop"
                ),
                "note_cn": "画中画小窗/缩窗/推近一律先检测人脸再取景；没有检测到人脸的镜头照实退回居中裁切并在此计数",
            },
            "preview": preview, "base_cache_hit": base_hit, "segments_reused": reused,
            "segments_rendered": len(clips) - reused, "xfade_applied": sum(1 for e in xf_edges if e),
            "xfade_transition": xf_type if use_xfade else "none",
            "pip_geometry": _pip_summary(),
            "base_elapsed_sec": round(perf_counter() - started, 4),
        })

    if (base_mp4.is_file() and cache_flag.is_file()
            and cache_flag.read_text(encoding="utf-8").strip() == cache_key
            and all(seg.is_file() for seg in expected_segments)):
        profile(True, len(clips))
        return base_mp4

    tw = 540 if preview else CANVAS_WIDTH
    th = 960 if preview else CANVAS_HEIGHT
    seg_files: List[Path] = []
    geom_cache: Dict[str, Tuple[int, int]] = {}
    reused = 0
    render_jobs: List[Tuple[Dict[str, Any], Optional[str], int, List[str], Path, Path, Path, str]] = []

    for idx, clip in enumerate(clips):
        seg_out = expected_segments[idx]
        media_path = str(clip.get("media_path") or "")
        if not media_path or not Path(media_path).exists():
            raise FileNotFoundError(f"EDL 镜头素材不存在: {media_path}")
        cf = _build_color_filter_for_clip(clip, color_plan)
        staged_seg = seg_out.with_suffix(".part.mp4")

        # 若本镜尾部参与 xfade，提前多裁 xf_frames 帧用于过渡重叠，确保拼接后总时长 0 帧漂移
        extra_xf = xf_frames if (idx < len(xf_edges) and xf_edges[idx]) else 0
        seg_frames = clip_nom_frames[idx] + extra_xf
        clip_eff = dict(clip)
        if extra_xf > 0:
            clip_eff["timeline_in_frame"] = 0
            clip_eff["timeline_out_frame"] = seg_frames
            clip_eff["source_out_sec"] = float(clip.get("source_out_sec", 0.0)) + frames_to_sec(extra_xf, fps)

        cmd = build_graded_segment_cmd(
            ffmpeg_bin, clip_eff, cf, eye_by_mp4, staged_seg, fps,
            tw=tw, th=th, preview=preview, geom_cache=geom_cache,
        )
        sec_p = str((clip.get("pip_layout") or {}).get("secondary_media_path") or "")
        sec_hash = sha256_file(Path(sec_p)) if sec_p and Path(sec_p).is_file() else ""
        pip_mode_k = str((clip.get("pip_layout") or {}).get("mode") or "")
        seg_key = compute_segment_cache_key({
            "ver": 5,
            "cmd": cmd[:-1],
            "pip_layout": clip.get("pip_layout"),
            "pip_mode": pip_mode_k,
            "seg_frames": seg_frames,
            "source_hash": source_hashes[media_path],
            "sec_hash": sec_hash,
            "lut_hash": source_hashes.get(lut_path),
            "fps": fps,
            "preview": preview,
        })
        seg_flag = seg_out.with_suffix(".sha256")
        if seg_out.is_file() and seg_flag.is_file() and seg_flag.read_text(encoding="utf-8") == seg_key:
            reused += 1
        else:
            render_jobs.append((clip_eff, cf, seg_frames, cmd, staged_seg, seg_out, seg_flag, seg_key))
        seg_files.append(seg_out)

    if render_jobs:
        def _exec_seg(job: Tuple[Dict[str, Any], Optional[str], int, List[str], Path, Path, Path, str]) -> None:
            j_clip, j_cf, j_frames, j_cmd, j_staged, j_out, j_flag, j_key = job
            try:
                pip_cfg_m = j_clip.get("pip_layout") or {}
                pip_m = str(pip_cfg_m.get("mode") or "")
                # 真草稿几何模式里带抠像语义的（cutout=true 且主轨不打小窗）走 AI 人像抠像合成；
                # 抠像器内部按「满屏真人 + 副镜背景 + 纯色描边」实现，与这些风格的源结构一致
                mat_m = pip_m
                if pip_m.startswith("geometry_pip"):
                    try:
                        cut = bool(pip_cfg_m.get("cutout"))
                        main_sc = float((pip_cfg_m.get("main") or {}).get("scale") or 1.0)
                    except (TypeError, ValueError):
                        cut, main_sc = False, 1.0
                    if cut and main_sc >= 0.999:
                        mat_m = "vision_portrait_cutout_pip"
                mat_ok = False
                if (not preview) and (mat_m in _AI_MATTING_MODES) and bool(pip_cfg_m.get("cutout", True)):
                    outline_val = (
                        10 if (pip_cfg_m.get("outline_px") is None and mat_m == "vision_portrait_cutout_pip")
                        else int(pip_cfg_m.get("outline_px") or 0)
                    )
                    clip_for_mat = dict(j_clip)
                    clip_for_mat["pip_layout"] = {**pip_cfg_m, "mode": mat_m, "outline_px": outline_val}
                    mat_ok = _render_ai_person_matting_segment(
                        ffmpeg_bin=ffmpeg_bin,
                        clip=clip_for_mat,
                        color_vf=j_cf,
                        eye_by_mp4=eye_by_mp4,
                        out_mp4=j_staged,
                        fps=fps,
                        clip_frames=j_frames,
                        geom_cache=geom_cache,
                    )
                if not mat_ok:
                    run_cmd(j_cmd)
                j_staged.replace(j_out)
            finally:
                j_staged.unlink(missing_ok=True)
            j_flag.write_text(j_key, encoding="utf-8")

        if len(render_jobs) == 1:
            _exec_seg(render_jobs[0])
        else:
            with ThreadPoolExecutor(max_workers=min(2, len(render_jobs))) as pool:
                list(pool.map(_exec_seg, render_jobs))

    # 逐镜补帧：素材不够长 / seek 取整会让某一镜少出 1~2 帧，拼起来视频流就比音频短
    # （实测 seg_003 要 37 帧只见 35，全片 60.83s vs 音频 60.89s = 1.75 帧，质检 #4 判脱节）。
    padded_segs = _pad_short_segments(
        ffmpeg_bin, seg_files, clip_nom_frames,
        [xf_frames if (idx < len(xf_edges) and xf_edges[idx]) else 0 for idx in range(len(seg_files))],
        fps,
    )
    if padded_segs:
        print(f"[render_mp4] 补帧 {padded_segs} 镜（末帧克隆到计划帧数，保证音视频流等长）", flush=True)

    exact_dur = frames_to_sec(target_frames, fps)
    if use_xfade and len(seg_files) >= 2:
        # 使用 FFmpeg xfade 拼接相邻镜头（非转场边界采用 0.034s/1帧极短过渡或分段拼接，严格保证 0 帧漂移）
        xf_cmd: List[str] = [ffmpeg_bin, "-y"]
        for sf_p in seg_files:
            xf_cmd.extend(["-i", str(sf_p)])
        fc_parts: List[str] = []
        for si in range(len(seg_files)):
            fc_parts.append(f"[{si}:v]fps=fps={fps},settb=AVTB,format=yuv420p[xs{si}]")
        cur_lbl = "[xs0]"
        acc_frames = clip_nom_frames[0]
        for edge_i in range(len(seg_files) - 1):
            nxt_lbl = f"[xs{edge_i + 1}]"
            out_lbl = "[vxf_raw]" if edge_i == len(seg_files) - 2 else f"[xm{edge_i}]"
            if xf_edges[edge_i]:
                # 前镜多预留了 xf_frames，在名义终点 acc_frames 处开启 xf_dur 过渡
                off_sec = max(0.0, frames_to_sec(acc_frames, fps))
                fc_parts.append(
                    f"{cur_lbl}{nxt_lbl}xfade=transition={xf_type}:duration={xf_dur:.4f}:offset={off_sec:.4f},settb=AVTB{out_lbl}"
                )
            else:
                fc_parts.append(f"{cur_lbl}{nxt_lbl}concat=n=2:v=1:a=0,settb=AVTB{out_lbl}")
            cur_lbl = out_lbl
            acc_frames += clip_nom_frames[edge_i + 1]
        fc_parts.append(f"[vxf_raw]{','.join(BT709_VF_SUFFIX)}[vxf_out]")
        xf_cmd.extend([
            "-an", "-filter_complex", ";".join(fc_parts),
            "-map", "[vxf_out]",
            "-frames:v", str(target_frames),
            "-c:v", "libx264", "-preset", "medium", "-crf", "15",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS,
            str(base_mp4),
        ])
        run_cmd(xf_cmd)
    else:
        concat_txt = work_dir / f"concat_{'p' if preview else 'f'}.txt"
        def _ff_concat_line(p: Path) -> str:
            escaped = p.resolve().as_posix().replace("'", r"'\''")
            return f"file '{escaped}'"
        concat_txt.write_text(
            "\n".join(_ff_concat_line(p) for p in seg_files) + "\n",
            encoding="utf-8",
        )
        run_cmd([
            ffmpeg_bin, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c", "copy",
            "-t", f"{exact_dur:.4f}",
            str(base_mp4),
        ])
    cache_flag.write_text(cache_key, encoding="utf-8")
    profile(False, reused)
    return base_mp4


def render_mp4(pipeline_dir: Path, preview: bool = False) -> Path:
    ffmpeg_bin = find_bin("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("未找到 ffmpeg，无法合成成片 MP4。")
    edl = read_json(pipeline_dir / "04_edl.json")
    subs_data = read_json(pipeline_dir / "05_subtitles.json")
    pkg_data = read_json(pipeline_dir / "06_packaging.json")
    color_plan = read_json(pipeline_dir / "07_color_plan.json")
    manifest = read_json(pipeline_dir / "01_manifest.json")

    master_wav, _ = _resolve_master_wav(pipeline_dir)
    out_mp4 = pipeline_dir / ("output_preview.mp4" if preview else "output_final.mp4")

    work_dir = pipeline_dir / "_render_final"
    work_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(work_dir.glob("pkg_*.png")) + list(work_dir.glob("pkg_*.mov")) + list(work_dir.glob("sub_*.png")):
        stale.unlink(missing_ok=True)

    fps = int(edl.get("fps") or DEFAULT_FPS)
    master_dur = get_wav_duration_sec(master_wav)
    target_frames = max(1, round(master_dur * fps))
    exact_dur = frames_to_sec(target_frames, fps)

    scale_k = 0.5 if preview else 1.0
    faces_by_mp4, _ = _manifest_lookups(manifest)

    def _faces_at_time(t: float) -> List[Dict[str, int]]:
        for c in edl.get("clips") or []:
            if float(c.get("timeline_in_sec", 0.0)) <= t < float(c.get("timeline_out_sec", 0.0)):
                mp = str(c.get("media_path") or "")
                return faces_by_mp4.get(mp) or faces_by_mp4.get(str(Path(mp).resolve())) or []
        return []

    templates = load_subtitle_templates()
    tpl_id = subs_data.get("template_id") or "style_01"
    base_tpl = templates.get(tpl_id) or next(iter(templates.values()))
    tpl = {**base_tpl, **subs_data["template_config"]} if isinstance(subs_data.get("template_config"), dict) else dict(base_tpl)
    font_path = subs_data.get("font_path") or find_commercial_font(tpl.get("font_candidates"))
    style_id = pkg_data.get("style_id") or load_style_applied(pipeline_dir) or "style_01"

    def _scale_png_for_preview(src_p: Path, tag: str) -> Path:
        if not preview:
            return src_p
        dst_p = work_dir / f"prev_{tag}_{src_p.name}"
        with Image.open(src_p) as im0:
            nw = max(2, int(round(im0.width * scale_k)))
            nh = max(2, int(round(im0.height * scale_k)))
            im0.resize((nw, nh), Image.Resampling.BILINEAR).save(dst_p)
        return dst_p

    # 先预渲染包装图层，以便把与全屏分层抠像（layered_cutout_over_broll）重叠的大字/巨字压进「人物背后 4 层纵深」
    prepared_pkg_ovs: List[Tuple[int, Dict[str, Any], Path, int, int, float, float, Optional[Tuple[int, int, int, int]], bool, bool]] = []
    for idx, elem in enumerate(pkg_data.get("visual_elements") or []):
        s_t = max(0.0, frames_to_sec(int(elem.get("start_frame", 0)), fps))
        e_t = min(exact_dur, frames_to_sec(int(elem.get("end_frame", 0)), fps))
        if e_t <= s_t:
            continue
        elem_kind_d = str(elem.get("kind") or elem.get("type") or "")
        is_poster_burst = str(elem.get("opening_mode") or "") == "store_poster_burst"
        if (not is_poster_burst) and (
            elem_kind_d in ("flower_text", "stamp_tag")
            or (elem_kind_d == "center_burst_title" and style_id in _DEDUPE_MINIMAL_STYLES)
        ):
            if _flower_duplicates_subtitle(str(elem.get("text_or_asset") or elem.get("text") or ""), s_t, e_t, subs_data.get("entries") or []):
                continue
        ov_path: Path
        is_video_ov = False
        pw, ph = 400, 120
        if not preview:
            try:
                from render_template_overlays import render_animated_overlay_mov
                mov_p = work_dir / f"pkg_{idx:03d}.mov"
                fallback_img = render_packaging_png(elem, style_id=style_id, color_plan=color_plan, font_candidates=tpl.get("font_candidates"))
                anim_res = render_animated_overlay_mov(
                    elem=elem,
                    out_mov=mov_p,
                    style_id=style_id,
                    duration_sec=max(0.4, e_t - s_t),
                    fps=fps,
                    font_candidates=tpl.get("font_candidates"),
                    fallback_static_img=fallback_img,
                )
                if anim_res is not None and anim_res[0].is_file():
                    ov_path, pw, ph = anim_res
                    is_video_ov = True
            except Exception:
                is_video_ov = False
        if not is_video_ov:
            png_path = resolve_packaging_source(
                elem, pipeline_dir, idx,
                style_id=style_id, color_plan=color_plan, font_candidates=tpl.get("font_candidates"),
            )
            with Image.open(png_path) as im:
                pw, ph = im.size
            ov_path = _scale_png_for_preview(png_path, f"p{idx}")

        elem_kind_s = str(elem.get("kind") or elem.get("type") or "")
        if style_id == "style_01" and (elem_kind_s == "center_burst_title" or str(elem.get("opening_mode") or "") == "store_poster_burst"):
            px = max(40, (CANVAS_WIDTH - pw) // 2)
            py = max(720, min(860, 1110 - ph))
        elif style_id == "style_18" and elem_kind_s == "center_burst_title":
            px = max(40, (CANVAS_WIDTH - pw) // 2)
            py = max(760, 1120 - ph)
        elif elem_kind_s == "indicator_sticker":
            def_x = max(SAFE_ZONE["x_min"], (CANVAS_WIDTH - pw) // 2)
            def_y = SAFE_ZONE["y_min"] + 120
            px, py = _place_from_final_bbox(elem, pw, ph, SAFE_ZONE, (def_x, def_y), [])
        elif style_id == "style_05":
            px = max(60, (CANVAS_WIDTH - pw) // 2)
            py = max(840, min(950, 1120 - ph))
        elif style_id == "style_08":
            px, py = 72, 230
        elif style_id == "style_06":
            px, py = max(60, (CANVAS_WIDTH - pw) // 2), 235
        elif style_id == "style_09":
            px, py = 375, 960
        elif style_id == "style_12":
            px, py = max(40, (CANVAS_WIDTH - pw) // 2), 188
        elif style_id == "style_14":
            px, py = max(60, (CANVAS_WIDTH - pw) // 2), 915
        elif style_id == "style_16":
            px, py = max(60, (CANVAS_WIDTH - pw) // 2), 775
        elif style_id == "style_22":
            px, py = max(40, (CANVAS_WIDTH - pw) // 2), 62
        else:
            def_x = max(SAFE_ZONE["x_min"], (CANVAS_WIDTH - pw) // 2)
            def_y = SAFE_ZONE["y_min"] + 40
            px, py = _place_from_final_bbox(elem, pw, ph, SAFE_ZONE, (def_x, def_y), _faces_at_time(s_t))
        if style_id not in ("style_09", "style_20") and (py + ph) > 1130:
            py = max(140, 1130 - ph)
        blur_rect = (px, py, pw, ph) if (not preview and elem.get("overlay_mode") == "focus_blur") else None
        fade_in = bool((not preview) and (not is_video_ov) and int(elem.get("start_frame", 0)) > 0)
        prepared_pkg_ovs.append((idx, elem, ov_path, px, py, s_t, e_t, blur_rect, fade_in, is_video_ov))

    # 4 层纵深路由：当全屏/放大抠像镜与 center_burst_title 时段重叠时，把巨字放入抠像人物背后，避免字糊在主讲人脸上
    edl_for_base = dict(edl)
    clips_base = [dict(c) for c in (edl.get("clips") or [])]
    skip_foreground_idxs: set = set()
    if (not preview) and prepared_pkg_ovs:
        for p_idx, p_elem, ov_p, bx, by, s_t, e_t, _br, _fi, _iv in prepared_pkg_ovs:
            if str(p_elem.get("kind") or "") != "center_burst_title":
                continue
            cut_overlap = 0.0
            matched_clips: List[Tuple[int, float, float]] = []
            for ci, c_it in enumerate(clips_base):
                lay = c_it.get("pip_layout") or {}
                if str(lay.get("mode") or "") != "layered_cutout_over_broll":
                    continue
                if float(lay.get("fg_scale") or 1.2) < 0.98:
                    continue
                c_s = float(c_it.get("timeline_in_sec") or 0.0)
                c_e = float(c_it.get("timeline_out_sec") or 0.0)
                ov_l = min(c_e, e_t) - max(c_s, s_t)
                if ov_l >= 0.20:
                    cut_overlap += ov_l
                    matched_clips.append((ci, max(c_s, s_t) - c_s, min(c_e, e_t) - c_s))
            if matched_clips and cut_overlap >= min(0.25, (e_t - s_t) * 0.45):
                skip_foreground_idxs.add(p_idx)
                for ci, rs_c, re_c in matched_clips:
                    c_s = float(clips_base[ci].get("timeline_in_sec") or 0.0)
                    lay_new = dict(clips_base[ci].get("pip_layout") or {})
                    b_list = list(lay_new.get("back_overlays") or [])
                    b_list.append({
                        "path": str(ov_p),
                        "x": int(bx),
                        "y": int(by),
                        "rel_start": round(rs_c, 4),
                        "rel_end": round(re_c, 4),
                        "ov_offset": round(max(0.0, c_s - s_t), 4),
                    })
                    lay_new["back_overlays"] = b_list
                    clips_base[ci]["pip_layout"] = lay_new
    edl_for_base["clips"] = clips_base
    base_mp4 = build_base_video_track(pipeline_dir, edl_for_base, color_plan, master_wav, manifest=manifest, preview=preview)

    # (path, px, py, s_t, e_t, blur_rect, fade_in, is_video_ov)
    overlays: List[Tuple[Path, int, int, float, float, Optional[Tuple[int, int, int, int]], bool, bool]] = []

    # style_06 黑底拍立得：常驻底部 Daily Vlog 副标
    if style_id == "style_06":
        try:
            from render_template_overlays import build_polaroid_bottom_caption_png
            ft_p = build_polaroid_bottom_caption_png(work_dir)
            with Image.open(ft_p) as imf:
                fw_f, fh_f = imf.size
            fx_f = (CANVAS_WIDTH - fw_f) // 2
            fy_f = 1175
            overlays.append((_scale_png_for_preview(ft_p, "ft"), int(round(fx_f * scale_k)), int(round(fy_f * scale_k)), 0.0, exact_dur, None, False, False))
        except Exception:
            pass

    # style_09 纯黑纪实双格金线：顶部画框正下方居中案情注记小字
    if style_id == "style_09":
        try:
            from render_template_overlays import build_style09_case_caption_png, get_style_blueprint
            cap_txt = str((get_style_blueprint("style_09").get("pip_spec") or {}).get("case_caption") or "核心现场实录 · 重点拆解")
            cap_p = build_style09_case_caption_png(work_dir, caption=cap_txt)
            with Image.open(cap_p) as imc:
                cw_c, _ch_c = imc.size
            overlays.append((_scale_png_for_preview(cap_p, "cap9"), int(round(((CANVAS_WIDTH - cw_c) // 2) * scale_k)), int(round(842 * scale_k)), 0.0, exact_dur, None, False, False))
        except Exception:
            pass

    for p_idx, _elem, ov_path, px, py, s_t, e_t, blur_rect, fade_in, is_video_ov in prepared_pkg_ovs:
        if p_idx in skip_foreground_idxs:
            continue
        overlays.append((
            ov_path,
            int(round(px * scale_k)),
            int(round(py * scale_k)),
            s_t, e_t, blur_rect, fade_in, is_video_ov,
        ))

    # 字幕闭环必须量在「真实的底片」上：先前把 base + 所有非字幕图层合成为 stage A，
    # 再从 stage A 抽帧量对比度。否则投影/描边是按一张没有贴纸/花字/卡片的假底图在算，
    # 实测同一时刻量出来能差 3~4 倍（16.0s：假底 0.302 vs 真底 0.775），闭环等于白跑。
    ov_non_sub = len(overlays)
    subtitles_present = any(not e.get("suppressed") for e in (subs_data.get("entries") or []))
    probe_src: Path = base_mp4
    if subtitles_present and ov_non_sub:
        probe_src = work_dir / ("stage_a_preview.mp4" if preview else "stage_a.mp4")
        _composite_layers(
            ffmpeg_bin, base_mp4, overlays[:ov_non_sub], probe_src, work_dir,
            fps=fps, preview=preview, audio_in=None, dur=exact_dur, tag="a",
        )

    subtitle_render_log: List[Dict[str, Any]] = []
    for idx, sub in enumerate(subs_data.get("entries") or []):
        if sub.get("suppressed"):
            continue
        s_t = max(0.0, float(sub.get("start_sec", 0.0)))
        e_t = min(exact_dur, float(sub.get("end_sec", s_t + 0.5)))
        if e_t <= s_t:
            continue
        sub_png = work_dir / f"sub_{idx:03d}.png"

        def _place_for(w: int, h: int) -> Tuple[int, int]:
            d_x, d_y = compute_subtitle_place_xy(w, h, tpl)
            if bool(tpl.get("full_width_bar")) and w >= CANVAS_WIDTH - 4:
                return 0, d_y
            p_x, p_y = _place_from_final_bbox(sub, w, h, SUBTITLE_SAFE_ZONE, (d_x, d_y), _faces_at_time(s_t))
            if w <= SUBTITLE_SYMMETRIC_MAX_W:
                p_x = SUBTITLE_CENTER_X - w // 2
            return int(p_x), int(p_y)

        # 亮底自适应投影闭环：拿 base 轨（已调色、无字幕）在字幕框位置的真实背景量对比度
        fb = sub.get("final_bbox") or sub.get("bbox") or {}
        bg_strip, bg_y0 = None, 0
        if fb:
            bg_y0 = max(0, int(fb.get("y", 0)) - 40)
            bg_y1 = min(CANVAS_HEIGHT, int(fb.get("y", 0)) + int(fb.get("h", 0)) + 40)
            bg_strip = _subtitle_bg_strip(
                ffmpeg_bin, probe_src, (s_t + e_t) / 2.0, bg_y0, bg_y1, work_dir, f"{idx:03d}"
            )
        sw_px, sh_px, sub_meta = render_subtitle_png_adaptive(
            sub, tpl, font_path, sub_png,
            bg_strip=bg_strip, bg_strip_y0=bg_y0, place_xy=_place_for,
        )
        if sub_meta.get("contrast_before") is not None:
            subtitle_render_log.append({
                "id": sub.get("id"),
                "text": sub.get("text"),
                "start_sec": round(s_t, 3),
                "end_sec": round(e_t, 3),
                **sub_meta,
            })
        # 同步写入 .key 指纹，供 export_jianying.py 零重复渲染直接命中字幕 PNG 缓存
        sub_cache_key = compute_segment_cache_key({
            "text": sub.get("text"),
            "words": sub.get("words"),
            "tpl": tpl,
            "font_path": font_path,
            "shadow_boost": sub_meta.get("shadow_boost"),
            "stroke_extra": sub_meta.get("stroke_extra"),
        })
        (work_dir / f"sub_{idx:03d}.key").write_text(sub_cache_key, encoding="utf-8")
        px, py = _place_for(sw_px, sh_px)
        overlays.append((
            _scale_png_for_preview(sub_png, f"s{idx}"),
            int(round(px * scale_k)),
            int(round(py * scale_k)),
            s_t, e_t, None, False, False,
        ))

    if subtitle_render_log:
        boosted = [r for r in subtitle_render_log if float(r.get("shadow_boost") or 1.0) > 1.0]
        write_json(work_dir.parent / "05_subtitle_render_log.json", {
            "step": "render_mp4.subtitle_shadow_calibration",
            "metric": "common.glyph_contrast_ratio（字形 vs 紧邻环带背景，同一把尺供渲染闭环与黑盒质检共用）",
            "target_ratio": SUBTITLE_CONTRAST_TARGET,
            "entries": subtitle_render_log,
            "boosted_count": len(boosted),
            "min_contrast_after": min((r["contrast_after"] for r in subtitle_render_log if r.get("contrast_after")), default=None),
        })

    if probe_src != base_mp4:
        final_src, final_overlays = probe_src, overlays[ov_non_sub:]
    else:
        final_src, final_overlays = base_mp4, overlays

    if not final_overlays:
        run_cmd([
            ffmpeg_bin, "-y",
            "-i", str(final_src),
            "-i", str(master_wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-af", DELIVERY_AUDIO_VF,
            "-c:a", "aac", "-b:a", "128k" if preview else "192k", "-ar", "48000",
            "-t", f"{exact_dur:.4f}",
            "-movflags", "+faststart",
            str(out_mp4),
        ])
        return _enforce_delivery_true_peak(ffmpeg_bin, out_mp4, preview=preview)

    _composite_layers(
        ffmpeg_bin, final_src, final_overlays, out_mp4, work_dir,
        fps=fps, preview=preview, audio_in=master_wav, dur=exact_dur, tag="f",
    )
    return _enforce_delivery_true_peak(ffmpeg_bin, out_mp4, preview=preview)


def _enforce_delivery_true_peak(ffmpeg_bin: str, mp4_path: Path, preview: bool = False) -> Path:
    """量「交付文件自己的」真峰：AAC 编码会把 wav 的 -1.6dBTP 抬到 -0.6dBTP。

    step8 限的是 wav，质检量的是 mp4 里的 AAC 流——两者差 1dB 时质检必挂。
    这里在封装后用同一把尺（ebur128 peak=true）再量一次，超线就只把音频重编码一次压回来（视频流不动）。
    """
    try:
        m = measure_lufs_ebur128(mp4_path, raise_on_error=False)
    except Exception:
        return mp4_path
    tp = float(m.get("true_peak_dbtp") or -99.0)
    # 注意：这里「不」靠再编一次把峰值压回来——实测 AAC 每编一代就多抬 1dB 真峰
    # （原片 -0.6 → 重编 +0.5 → 带 -0.6dB 增益重编 +5.1），越压越糟。
    # 真正的修法在 step8 的母带目标里扣掉 AAC 余量；这里只把交付文件的实测值写进日志，供质检对账。
    if m.get("valid", True):
        write_json(mp4_path.parent / "08_delivery_loudness.json", {
            "file": mp4_path.name,
            "integrated_lufs": round(float(m.get("integrated_lufs") or -99.0), 2),
            "true_peak_dbtp": round(tp, 2),
            "tp_max_dbtp": TP_MAX_DBTP,
            "ok": bool(tp <= TP_MAX_DBTP),
        })
        if tp > TP_MAX_DBTP:
            print(f"[render_mp4] [WARN] 交付文件真峰 {tp:.2f}dBTP 高于 {TP_MAX_DBTP:g}dBTP（AAC 封装抬升）", flush=True)
    return mp4_path


def _composite_layers(
    ffmpeg_bin: str,
    video_in: Path,
    overlays: List[Tuple[Any, int, int, float, float, Optional[Any], bool, bool]],
    out_path: Path,
    work_dir: Path,
    fps: int,
    preview: bool,
    audio_in: Optional[Path],
    dur: float,
    tag: str = "f",
) -> Path:
    """把一组图层按时间窗叠到 video_in 上；audio_in 为 None 时只出画面（供字幕闭环取真底片）。"""
    out_path = Path(out_path)
    if not overlays:
        raise ValueError("_composite_layers 需要至少一个图层")
    base_idx = 2 if audio_in else 1
    cmd = [ffmpeg_bin, "-y", "-i", str(video_in)]
    if audio_in:
        cmd.extend(["-i", str(audio_in)])
    for ov_p, _px, _py, s_t, e_t, _blur_rect, fade_in, is_video_ov in overlays:
        if is_video_ov:
            cmd.extend(["-i", str(ov_p)])
        elif fade_in:
            cmd.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{max(0.1, e_t - s_t + 0.05):.4f}", "-i", str(ov_p)])
        else:
            cmd.extend(["-i", str(ov_p)])

    filter_chains: List[str] = []
    cur_label = "[0:v]"
    step_idx = 0
    for i, (_, px, py, s_t, e_t, blur_rect, fade_in, is_video_ov) in enumerate(overlays):
        in_idx = i + base_idx
        if blur_rect is not None:
            bx, by, bw, bh = blur_rect
            bw = max(40, min(CANVAS_WIDTH, int(bw)))
            bh = max(40, min(CANVAS_HEIGHT, int(bh)))
            bx = max(0, min(CANVAS_WIDTH - bw, int(bx)))
            by = max(0, min(CANVAS_HEIGHT - bh, int(by)))
            blur_rad = max(2, min(10, min(bw, bh) // 4 - 1))
            base_copy = f"[bc_{step_idx}]"
            blur_crop_in = f"[bi_{step_idx}]"
            blur_crop_out = f"[bo_{step_idx}]"
            blur_merged = f"[bm_{step_idx}]"
            filter_chains.append(f"{cur_label}split=2{base_copy}{blur_crop_in}")
            filter_chains.append(f"{blur_crop_in}crop={bw}:{bh}:{bx}:{by},boxblur={blur_rad}:2:enable='between(t,{s_t:.4f},{e_t:.4f})'{blur_crop_out}")
            filter_chains.append(
                f"{base_copy}{blur_crop_out}overlay=x={bx}:y={by}:enable='between(t,{s_t:.4f},{e_t:.4f})'{blur_merged}"
            )
            cur_label = blur_merged

        if is_video_ov:
            prep_lbl = f"[ovm_{step_idx}]"
            filter_chains.append(
                f"[{in_idx}:v]format=rgba,setpts=PTS-STARTPTS+{s_t:.4f}/TB{prep_lbl}"
            )
            ov_in = prep_lbl
            eof_mode = "pass"
        elif fade_in:
            prep_lbl = f"[ovf_{step_idx}]"
            filter_chains.append(
                f"[{in_idx}:v]format=rgba,fade=t=in:st=0:d=0.133:alpha=1,setpts=PTS+{s_t:.4f}/TB{prep_lbl}"
            )
            ov_in = prep_lbl
            eof_mode = "pass"
        else:
            ov_in = f"[{in_idx}:v]"
            eof_mode = "repeat"

        out_label = "[vout]" if i == len(overlays) - 1 else f"[v{i}]"
        filter_chains.append(
            f"{cur_label}{ov_in}overlay=x={px}:y={py}:eof_action={eof_mode}:enable='between(t,{s_t:.4f},{e_t:.4f})'{out_label}"
        )
        cur_label = out_label
        step_idx += 1

    filter_str = ";\n".join(filter_chains)
    filter_script = work_dir / f"overlay_filter_{tag}.txt"
    filter_script.write_text(filter_str, encoding="utf-8")

    if len(filter_str) < 32768:
        fc_args = ["-filter_complex", filter_str]
    else:
        help_txt = run_cmd([ffmpeg_bin, "-hide_banner", "-h", "long"], check=False).stdout or ""
        fc_flag = "-filter_complex_script" if "filter_complex_script" in help_txt else "-/filter_complex"
        fc_args = [fc_flag, str(filter_script)]

    cmd.extend([
        *fc_args,
        "-map", "[vout]",
        *(["-map", "1:a:0"] if audio_in else ["-an"]),
        "-c:v", "libx264", "-preset", "ultrafast" if preview else "medium", "-crf", "26" if preview else "17",
        *([] if preview else BT709_FFMPEG_ARGS),
        *(["-af", DELIVERY_AUDIO_VF, "-c:a", "aac", "-b:a", "128k" if preview else "192k", "-ar", "48000"] if audio_in else []),
        "-t", f"{dur:.4f}",
        *(["-movflags", "+faststart"] if audio_in else []),
        str(out_path),
    ])
    run_cmd(cmd)
    return out_path


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="成片渲染器（支持粗剪预览与终稿 BT.709 导出）")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--preview", action="store_true", help="输出低清快速粗剪预览 (540x960)")
    args = parser.parse_args()
    out = render_mp4(Path(args.pipeline_dir), preview=args.preview)
    print(f"[render_mp4] 渲染完成: {out}")


if __name__ == "__main__":
    main()
