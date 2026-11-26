#!/usr/bin/env python3
"""
[24 套剪映模板高保真静态 + 30fps 动态透明叠层渲染引擎 (v3.1)]
1. 依据 assets/style_spectrum/template_blueprints.json 为全部 24 套剪映白盒风格渲染专属视觉叠层：
   - style_01 门店爆款：3 拍错落砸屏综艺斜标（红底角标 + 亮黄绿粗描边主字 + 红白立体副标）
   - style_05 人像前景压巨字：背后 240px 巨型亮黄大字层 + 胸前高对比白字提问层
   - style_06 黑底拍立得：随仓商用毛笔手写体 MaShanZheng-Regular.ttf 顶部双行金句 + Daily Vlog 底栏
   - style_09 纪实双格金线：顶部双格金线案情注记 + 左圆头像右侧金线标题
   - style_12 顶置双色悬念标：跨幅双行纯白+荧光黄绿高对比悬念标题
   - style_13 上白圆角画框 / style_14 纯黑聚光圆窗：极简克制大字标题
   - style_16 暗黑羽化书摘 / style_18 3D 金属扫光立体字 / style_20 国风黑条朱红印等全部 24 套。
2. 30fps 动态透明 .mov 渲染器 (render_animated_overlay_mov)：
   原生支持 8 种剪映核心动态文字动效（store_stagger_burst / bounce_smash / wipe_reveal /
   typewriter_char / shimmer_3d_gold / ink_bloom / depth_behind_person / number_flip_trio），
   并按 overlay_style.anim_primitives 叠加 6 类入场原语 + 缩小/放大子型（overlay_motion.py：
   弹幕滚动 / 镜像翻转 / 横向模糊 / 快速翻页 / 折叠展开 / 滑动出入），彻底告别静态整图生硬淡入。
3. 每套风格的花字叠层均为专属版式（无「通用斜体兜底」）：宋体极简 / 白描边抠像 / 极净全屏 /
   通透大字 / 圆角黑幕 / 双色花字流 / 暖金勾边 / 门店小签 / 克制微纪录 / 高燃混剪。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    ASSETS_DIR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    detect_browser_renderer,
    find_bin,
    find_commercial_font,
    get_cached_font,
    hex_to_rgba,
    maybe_reexec_in_venv,
    read_json,
)

if __name__ == "__main__" or str(sys.argv[0]).endswith(".py"):
    maybe_reexec_in_venv()

from overlay_motion import (
    MOTION_MODES,
    MOTION_PAD,
    make_frame_fn as make_motion_frame_fn,
    resolve_mode as resolve_anim_mode,
)
from glyph_fallback import glyph_font_for

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BLUEPRINTS_PATH = ASSETS_DIR / "style_spectrum" / "template_blueprints.json"

# 不参与 anim_primitives 轮换的签名动效（style_01 三拍砸屏 / style_18 3D 金属扫光）
SIGNATURE_ANIM_MODES = frozenset({"store_stagger_burst", "shimmer_3d_gold"})


@lru_cache(maxsize=4)
def load_template_blueprints() -> Dict[str, Any]:
    if BLUEPRINTS_PATH.is_file():
        return json.loads(BLUEPRINTS_PATH.read_text(encoding="utf-8")).get("blueprints", {})
    return {}


_PAINT = {"style_id": "", "stroke_px": 0, "shadow_rgba": (0, 0, 0, 0), "shadow_dx": 0, "shadow_dy": 0,
          "shadow_blur": 0.0}


def _load_paint(style_id: str) -> Dict[str, Any]:
    """读该风格「花字」的描边/阴影实测值（与字幕同源：解密草稿 styles[].strokes/shadows）。

    v4.0 及以前这里每条花字分支都写死 stroke_width=_sw(7)~11 的黑描边，加上 shadow_dy=3 的假阴影；
    真草稿 24/24 套花字**一条描边都没有**，全是右下偏移的半透明阴影。写死的描边让复刻看起来
    又脏又土，也是用户实测「变成描黑边」的另一半来源。
    """
    try:
        lib = read_json(ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json")
        entry = next((x for x in lib.get("styles", []) if x.get("style_id") == style_id), None)
        spec = (entry or {}).get("flower_text_spec") or {}
    except Exception:
        spec = {}
    # flower_text_spec 里 shadow 是 bool + 平铺的 shadow_color/offset/blur 字段
    sh = {
        "enabled": bool(spec.get("shadow")),
        "color": spec.get("shadow_color") or "#00000000",
        "alpha": spec.get("shadow_alpha") if spec.get("shadow_alpha") is not None else 0.0,
        "offset_px": spec.get("shadow_offset") or [0, 0],
        "blur_px": spec.get("shadow_blur_px") or 0.0,
    }
    if spec.get("stroke_mode") == "stroke":
        stroke_px = int(round(float(spec.get("stroke_width") or 0)))
    else:
        stroke_px = 0
    if sh.get("enabled"):
        col = str(sh.get("color") or "#000000").lstrip("#")
        alpha = int(round(max(0.0, min(1.0, float(sh.get("alpha") or 0.0))) * 255))
        off = list(sh.get("offset_px") or [0, 0])
        sh_rgba = (int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16), alpha) if len(col) == 6 else (0, 0, 0, alpha)
        dx, dy = int(off[0]), int(off[1])
        blur = float(sh.get("blur_px") or 0.0)
    else:
        sh_rgba, dx, dy, blur = (0, 0, 0, 0), 0, 0, 0.0
    return {"style_id": style_id, "stroke_px": stroke_px, "shadow_rgba": sh_rgba,
            "shadow_dx": dx, "shadow_dy": dy, "shadow_blur": blur}


def _sw(legacy: int) -> int:
    """描边宽度：该风格实测有描边才画，否则一律 0（以前每个分支写死 7~11px 黑边）。

    真草稿里 24 套花字 1636 个文字样式**一条真描边都没有**，只有右下偏移的半透明阴影；
    写死描边会让复刻出来的花字又脏又土，也是用户实测「变成描黑边」的另一半来源。
    """
    if not _PAINT.get("style_id"):
        return int(legacy)
    if int(_PAINT.get("stroke_px") or 0) <= 0:
        return 0
    return max(1, int(round(int(legacy) * min(1.0, int(_PAINT["stroke_px"]) / 8.0))))


def flower_paint(style_id: str) -> Dict[str, Any]:
    """当前花字的实测画法（描边 px / 阴影色 / 偏移 / 虚化）。"""
    if _PAINT.get("style_id") != style_id:
        _PAINT.update(_load_paint(style_id))
    return _PAINT


def get_style_blueprint(style_id: str) -> Dict[str, Any]:
    bps = load_template_blueprints()
    return bps.get(style_id) or bps.get("style_01") or {}


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Optional[str],
    max_w: int,
    max_fs: int,
    min_fs: int = 24,
    stroke_w: int = 0,
) -> Tuple[Any, str, int]:
    t = str(text or "").strip()
    if not t:
        return get_cached_font(font_path, min_fs), "", min_fs

    def _w_px(txt: str, size: int) -> float:
        base_f = get_cached_font(font_path, size)
        c_fonts = [glyph_font_for(ch, font_path, size) for ch in txt]
        if all(cf == base_f for cf in c_fonts):
            bb = draw.textbbox((0, 0), txt, font=base_f, stroke_width=stroke_w)
            return float(bb[2] - bb[0])
        return sum(draw.textlength(ch, font=cf) for ch, cf in zip(txt, c_fonts)) + stroke_w * 2

    for fs in range(max_fs, min_fs - 1, -2):
        if _w_px(t, fs) <= max_w:
            return get_cached_font(font_path, fs), t, fs
    fnt = get_cached_font(font_path, min_fs)
    while len(t) > 2:
        if _w_px(t + "…", min_fs) <= max_w:
            return fnt, t + "…", min_fs
        t = t[:-1]
    return fnt, t, min_fs


def _hex_rgb(hex_str: Any, default: Tuple[int, int, int] = (255, 255, 255)) -> Tuple[int, int, int]:
    """把 #RRGGBB / #RRGGBBAA 转成 RGB。"""
    s = str(hex_str or "").strip().lstrip("#")
    if len(s) not in (6, 8):
        return default
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return default


def _mix(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, float(t)))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def _light(c: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return _mix(c, (255, 255, 255), t)


def _shade(c: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return _mix(c, (0, 0, 0), t)


def style_flower_palette(ov_style: Dict[str, Any], style_id: str = "",
                         preferred_hex: str = "") -> Dict[str, Any]:
    """花字配色一律从蓝图 overlay_style 读：flower_colors 是源草稿实测色。

    返回 {text, accent, accent2, deep, light, stroke, plate}，其中 light/deep
    是 accent 的明暗派生，保证与源同色系。蓝图为空时按最保守的白字回退。
    """
    raw = [c for c in (ov_style.get("flower_colors") or []) if isinstance(c, str)]
    rgbs = [_hex_rgb(c) for c in raw]
    neutral = [c for c in rgbs if max(c) - min(c) <= 12 or min(c) >= 235]
    accents = [c for c in rgbs if c not in neutral]
    pref = _hex_rgb(preferred_hex) if preferred_hex else None
    if pref is not None and not (max(pref) - min(pref) <= 12 or min(pref) >= 235):
        # 字幕关键词色是源草稿里最可靠的主强调色，优先用它当花字主色
        accents = [pref] + [c for c in accents if c != pref]
    accent = accents[0] if accents else (rgbs[0] if rgbs else (255, 191, 23))
    accent2 = accents[1] if len(accents) > 1 else _shade(accent, 0.25)
    deepest = accents[2] if len(accents) > 2 else _shade(accent, 0.62)
    stroke_hex = str(ov_style.get("stroke_color") or "")
    stroke = _hex_rgb(stroke_hex) if stroke_hex else _shade(accent, 0.80)
    if min(stroke) >= 235:
        # 白字 + 白描边会糊成一团（源里「白边」指的是抠像的人像白轮廓，不是字描边）：改用深色描边
        stroke = _shade(accent, 0.86)
    return {
        "text": (255, 255, 255),
        "accent": accent,
        "accent2": accent2,
        "deep": deepest,
        "light": _light(accent, 0.58),
        "mist": _light(accent, 0.84),
        "stroke": stroke,
        "plate": (0, 0, 0),
        "source_hex": [c.upper() for c in raw],
    }


def _split_two_lines(text: str, max_first: int = 8) -> Tuple[str, str]:
    t = str(text or "").strip()
    if not t:
        return "", ""
    from step5_subtitles import split_semantic_two_lines
    l1, l2 = split_semantic_two_lines(t, max_first=max_first)
    return l1[:max(max_first, 10)], l2[:12]


def _draw_glyph_run(
    draw: ImageDraw.ImageDraw,
    ox: int,
    oy: int,
    text: str,
    font_path: Optional[str],
    fs: int,
    main_fnt: Any,
    fill: Any,
    stroke_width: int = 0,
    stroke_fill: Any = None,
    has_fallback: bool = False,
    base_offset_y: int = 0,
) -> None:
    """绘制一行文字：无缺字时走高速单次 draw.text，缺字（如粤语喺/嘅/咗）自动按基线逐字回退。"""
    if not has_fallback:
        draw.text((ox, oy), text, font=main_fnt, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        return
    x = float(ox)
    by = float(oy + base_offset_y)
    for ch in text:
        cf = glyph_font_for(ch, font_path, fs)
        draw.text((x, by), ch, font=cf, fill=fill, anchor="ls", stroke_width=stroke_width, stroke_fill=stroke_fill)
        x += draw.textlength(ch, font=cf)


def _paste_glyph_line(
    canvas: Image.Image,
    pos: Tuple[int, int],
    text: str,
    font_path: Optional[str],
    max_w: int,
    font_size: int,
    min_fs: int,
    fill_rgba: Tuple[int, int, int, int],
    stroke_rgba: Optional[Tuple[int, int, int, int]] = None,
    stroke_w: int = 0,
    shadow_rgba: Optional[Tuple[int, int, int, int]] = None,
    shadow_dy: int = 4,
    anchor: str = "mm",
) -> None:
    """复用 _text_sprite（自带粤语逐字字形回退）向已有画布居中或左对齐贴入一行字，彻底消除豆腐块。"""
    if not str(text or "").strip():
        return
    sp = _text_sprite(
        text, font_path, max_w, font_size, fill_rgba,
        stroke_rgba=stroke_rgba, stroke_w=stroke_w,
        shadow_rgba=shadow_rgba, shadow_dy=shadow_dy, shadow_blur=1.0, min_fs=min_fs,
    )
    if sp is None:
        return
    cx, cy = int(pos[0]), int(pos[1])
    px = cx if anchor == "lm" else (cx - sp.width // 2)
    py = cy - sp.height // 2
    canvas.alpha_composite(sp, dest=(max(0, px), max(0, py)))


def _draw_gradient_3d_text(
    text: str,
    font_path: Optional[str],
    max_w: int,
    font_size: int = 104,
    top_rgb: Tuple[int, int, int] = (255, 246, 178),
    mid_rgb: Tuple[int, int, int] = (255, 196, 36),
    bot_rgb: Tuple[int, int, int] = (224, 114, 12),
    extrude_rgb: Tuple[int, int, int] = (68, 28, 6),
    stroke_rgb: Tuple[int, int, int] = (42, 16, 4),
    depth_px: int = 10,
    min_hairline_w: int = 0,
    shadow_alpha: int = 185,
) -> Image.Image:
    """渲染高质感 3D 金属渐变立体字（复刻 style_18 / style_21 等爆款大字，含缺字逐字回退）。"""
    probe = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    pd = ImageDraw.Draw(probe)
    fnt, fitted, fs = _fit_text(pd, text, font_path, max_w - 60, font_size, 44, stroke_w=8)
    char_fonts = [glyph_font_for(ch, font_path, fs) for ch in fitted]
    has_fb = any(cf != fnt for cf in char_fonts)
    pad = 28
    if has_fb:
        metrics = [cf.getmetrics() for cf in char_fonts] or [fnt.getmetrics()]
        ascent = max(m[0] for m in metrics)
        descent = max(m[1] for m in metrics)
        tw = max(80, int(math.ceil(sum(pd.textlength(ch, font=cf) for ch, cf in zip(fitted, char_fonts)))) + 16)
        th = max(60, ascent + descent + 16)
        ox, oy = pad + 8, pad + 8
        base_off = ascent
    else:
        bb = pd.textbbox((0, 0), fitted, font=fnt, stroke_width=8)
        tw = max(80, bb[2] - bb[0])
        th = max(60, bb[3] - bb[1])
        ox, oy = pad - bb[0], pad - bb[1]
        base_off = 0
    w = tw + pad * 2 + depth_px + 8
    h = th + pad * 2 + depth_px + 8

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out)
    eff_sw = max(int(min_hairline_w), _sw(8))
    sh_a = max(120, min(255, int(shadow_alpha)))

    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    _draw_glyph_run(sd, ox + depth_px + 4, oy + depth_px + 6, fitted, font_path, fs, fnt, (0, 0, 0, sh_a),
                    stroke_width=max(eff_sw, _sw(10)), stroke_fill=(0, 0, 0, sh_a), has_fallback=has_fb, base_offset_y=base_off)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
    out.alpha_composite(shadow)

    for d in range(depth_px, 0, -1):
        shade = 0.65 + 0.35 * (1.0 - d / max(1, depth_px))
        ec = (int(extrude_rgb[0] * shade), int(extrude_rgb[1] * shade), int(extrude_rgb[2] * shade), 255)
        _draw_glyph_run(draw, ox + int(d * 0.45), oy + d, fitted, font_path, fs, fnt, ec,
                        stroke_width=eff_sw, stroke_fill=ec, has_fallback=has_fb, base_offset_y=base_off)

    if eff_sw > 0:
        _draw_glyph_run(draw, ox, oy, fitted, font_path, fs, fnt, (*stroke_rgb, 255),
                        stroke_width=eff_sw, stroke_fill=(*stroke_rgb, 255), has_fallback=has_fb, base_offset_y=base_off)

    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    _draw_glyph_run(md, ox, oy, fitted, font_path, fs, fnt, 255, has_fallback=has_fb, base_offset_y=base_off)

    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    y0 = max(0, pad - 6)
    y1 = min(h - 1, pad + th + 6)
    span = max(1, y1 - y0)
    for yy in range(y0, y1 + 1):
        t = (yy - y0) / float(span)
        if t < 0.48:
            k = t / 0.48
            r = int(top_rgb[0] + (mid_rgb[0] - top_rgb[0]) * k)
            g = int(top_rgb[1] + (mid_rgb[1] - top_rgb[1]) * k)
            b = int(top_rgb[2] + (mid_rgb[2] - top_rgb[2]) * k)
        else:
            k = (t - 0.48) / 0.52
            r = int(mid_rgb[0] + (bot_rgb[0] - mid_rgb[0]) * k)
            g = int(mid_rgb[1] + (bot_rgb[1] - mid_rgb[1]) * k)
            b = int(mid_rgb[2] + (bot_rgb[2] - mid_rgb[2]) * k)
        gd.line([(0, yy), (w, yy)], fill=(r, g, b, 255))

    glint_y = y0 + int(span * 0.16)
    gd.line([(0, glint_y), (w, glint_y)], fill=(255, 255, 240, 235), width=3)

    filled = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    filled.paste(grad, (0, 0), mask)
    out.alpha_composite(filled)
    ink = out.getbbox()
    return out.crop(ink) if ink else out


def _draw_tilted_stroked_text(
    text: str,
    font_path: Optional[str],
    max_w: int,
    font_size: int,
    fill_rgba: Tuple[int, int, int, int],
    stroke_rgba: Tuple[int, int, int, int] = (10, 10, 12, 255),
    stroke_w: int = 8,
    tilt_deg: float = -5.0,
) -> Image.Image:
    stroke_w = _sw(stroke_w)
    img = _text_sprite(
        str(text or "").strip(), font_path, max_w - 40, font_size, fill_rgba,
        stroke_rgba=stroke_rgba, stroke_w=stroke_w,
        shadow_rgba=(0, 0, 0, 195), shadow_dy=6, shadow_blur=1.5, min_fs=34,
    )
    if img is None:
        return Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    if abs(tilt_deg) > 0.2:
        img = img.rotate(-tilt_deg, resample=Image.Resampling.BICUBIC, expand=True)
    ink = img.getbbox()
    return img.crop(ink) if ink else img


def _text_sprite(
    text: str,
    font_path: Optional[str],
    max_w: int,
    font_size: int,
    fill_rgba: Tuple[int, int, int, int],
    stroke_rgba: Optional[Tuple[int, int, int, int]] = None,
    stroke_w: int = 0,
    shadow_rgba: Optional[Tuple[int, int, int, int]] = None,
    shadow_dy: int = 4,
    shadow_blur: float = 3.0,
    spacing_px: float = 0.0,
    min_fs: int = 24,
) -> Optional[Image.Image]:
    """
    单行文字精灵：按 max_w 自动压字号 / 截断防溢出，可选字距、描边与柔和投影。
    返回已按实际墨迹裁边的 RGBA 图（透明底）。
    """
    t = str(text or "").strip()
    if not t:
        return None
    # 描边/阴影只认该风格的实测值（花字 24/24 套真草稿都没有描边）
    pt = _PAINT
    if pt.get("style_id"):
        sw = max(0, int(pt.get("stroke_px") or 0))
        if pt.get("shadow_rgba", (0, 0, 0, 0))[3] > 0:
            shadow_rgba = tuple(pt["shadow_rgba"])
            shadow_dy = int(pt.get("shadow_dy") or 0)
            shadow_blur = float(pt.get("shadow_blur") or 0.0)
        else:
            shadow_rgba = None
    else:
        sw = max(0, int(stroke_w))
    limit = max(48, int(max_w) - sw * 2)
    probe = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    pd = ImageDraw.Draw(probe)
    fs = max(int(min_fs), int(font_size))
    fnt = get_cached_font(font_path, fs)
    gap_px = max(0.0, float(spacing_px))

    def _span(txt: str, size: int, gap: float) -> float:
        # 逐字量宽：缺字用回退字体量，否则豆腐块的宽度会把行宽算错
        return sum(pd.textlength(ch, font=glyph_font_for(ch, font_path, size)) for ch in txt) + gap * max(0, len(txt) - 1)

    while fs > min_fs and _span(t, fs, gap_px) > limit:
        fs -= 2
        fnt = get_cached_font(font_path, fs)
    while len(t) > 2 and _span(t, fs, gap_px) > limit:
        t = t[:-1]
    if gap_px > 0 and _span(t, fs, gap_px) > limit:
        gap_px = 0.0

    # 逐字字形回退：花字里常出现粤语字（嘅/喺/嗰…），而阿里妈妈书黑体的 cmap 没有这些字，
    # PIL 又不会自动换字体——不回落就是空白。只换字体、不换字号；基线用 anchor="ls" 对齐，
    # 画布高度按行内最大 ascent/descent 留白，混排也不会上下错位。
    char_fonts = [glyph_font_for(ch, font_path, fs) for ch in t]
    widths = [pd.textlength(ch, font=cf) for ch, cf in zip(t, char_fonts)]
    total = sum(widths) + gap_px * max(0, len(t) - 1)
    metrics = [cf.getmetrics() for cf in char_fonts] or [fnt.getmetrics()]
    ascent = max(m[0] for m in metrics)
    descent = max(m[1] for m in metrics)
    pad = sw + 8
    w = int(math.ceil(total)) + pad * 2 + 4
    h = ascent + descent + pad * 2 + 4

    ink = Image.new("RGBA", (max(8, w), max(8, h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(ink)
    x = float(pad)
    base_y = pad + ascent
    stroke_fill = stroke_rgba if (sw > 0 and stroke_rgba) else None
    for ch, cw_px, cf in zip(t, widths, char_fonts):
        d.text((x, base_y), ch, font=cf, fill=fill_rgba, anchor="ls", stroke_width=sw, stroke_fill=stroke_fill)
        x += cw_px + gap_px

    if shadow_rgba is not None and shadow_rgba[3] > 0:
        alpha = ink.split()[3].point(lambda v: int(v * shadow_rgba[3] / 255.0))
        shade = Image.new("RGBA", ink.size, (shadow_rgba[0], shadow_rgba[1], shadow_rgba[2], 255))
        shadow = Image.new("RGBA", ink.size, (0, 0, 0, 0))
        shadow.paste(shade, (0, 0), alpha)
        if shadow_blur > 0.2:
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        out = Image.new("RGBA", ink.size, (0, 0, 0, 0))
        out.alpha_composite(shadow, dest=(0, max(0, int(shadow_dy))))
        out.alpha_composite(ink)
        ink = out

    box = ink.getbbox()
    return ink.crop(box) if box else None


def _stack_sprites(
    layers: List[Optional[Image.Image]],
    gap: int = 10,
    pad: int = 8,
    max_w: int = 940,
) -> Optional[Image.Image]:
    """把若干单行精灵按行距垂直居中堆叠为一块透明叠层，超宽自动等比压回 1080 内。"""
    rows = [l for l in layers if l is not None]
    if not rows:
        return None
    w = max(r.width for r in rows) + pad * 2
    h = sum(r.height for r in rows) + gap * max(0, len(rows) - 1) + pad * 2
    img = Image.new("RGBA", (max(8, w), max(8, h)), (0, 0, 0, 0))
    y = pad
    for r in rows:
        img.alpha_composite(r, dest=((w - r.width) // 2, y))
        y += r.height + gap
    box = img.getbbox()
    img = img.crop(box) if box else img
    if img.width > max_w:
        k = max_w / float(img.width)
        img = img.resize((max_w, max(1, int(round(img.height * k)))), Image.Resampling.LANCZOS)
    return img


def _plate_sprite(
    layer: Image.Image,
    fill_rgba: Tuple[int, int, int, int],
    pad_x: int = 26,
    pad_y: int = 12,
    radius: int = 0,
) -> Image.Image:
    """给一行文字加方角/圆角色块底框（style_17 方角 #000000B2、style_21 门店小签）。"""
    w, h = layer.width + pad_x * 2, layer.height + pad_y * 2
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    if radius > 0:
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=fill_rgba)
    else:
        d.rectangle([(0, 0), (w - 1, h - 1)], fill=fill_rgba)
    out.alpha_composite(layer, dest=(pad_x, pad_y))
    return out


def _build_style01_layers(
    elem: Dict[str, Any],
    sans_font: Optional[str],
    oblique_font: Optional[str],
) -> Tuple[Tuple[int, int], List[Tuple[Image.Image, Tuple[int, int], float]]]:
    """
    拆解 style_01 门店爆款综艺斜标为 3 个可独立按节拍砸入的子层：
    [(layer_img, (dest_x, dest_y), delay_sec), ...]
    """
    elem_kind = str(elem.get("kind") or elem.get("type") or elem.get("element_type") or "flower_text")
    main_text = str(elem.get("text_or_asset") or elem.get("text") or "").strip()
    sub_text = str(elem.get("subtext") or "").strip()
    tag_raw = str(elem.get("tag_label") or "").strip()
    l1, l2 = _split_two_lines(main_text, max_first=4 if len(main_text) >= 6 else 6)
    if not l2 and sub_text and sub_text != l1:
        l2 = sub_text[:8]
        tag = tag_raw[:8] if (tag_raw and tag_raw not in (l1, l2)) else "门店实拍"
    else:
        tag = (sub_text or tag_raw)[:8] if (sub_text or tag_raw) else ("门店实拍" if elem_kind == "center_burst_title" else "")

    cw = 980
    layers: List[Tuple[Image.Image, Tuple[int, int], float]] = []
    y_cur = 18

    if tag:
        badge_txt = tag
        # 2x 超采样抗锯齿绘制与轻微倾斜，杜绝 34px 小签旋转后边缘拉丝发糊
        b_hi = Image.new("RGBA", (880, 148), (0, 0, 0, 0))
        bd = ImageDraw.Draw(b_hi)
        bf, bt, _ = _fit_text(bd, badge_txt, sans_font, 700, 68, 42)
        bb = bd.textbbox((0, 0), bt, font=bf)
        bw = (bb[2] - bb[0]) + 88
        bh = (bb[3] - bb[1]) + 46
        bx0 = (880 - bw) // 2
        bd.rounded_rectangle([(bx0, 16), (bx0 + bw, 16 + bh)], radius=20, fill=(226, 32, 32, 248), outline=(255, 255, 255, 245), width=6)
        _paste_glyph_line(b_hi, (440, 16 + bh // 2), bt, sans_font, 700, 68, 42,
                          (255, 255, 255, 255), stroke_rgba=(90, 8, 8, 255), stroke_w=max(2, _sw(4)), shadow_dy=0)
        b_rot = b_hi.rotate(4, resample=Image.Resampling.BICUBIC, expand=True)
        b_ink = b_rot.getbbox()
        if b_ink:
            b_rot = b_rot.crop(b_ink)
            b_rot = b_rot.resize((max(40, b_rot.width // 2), max(20, b_rot.height // 2)), Image.Resampling.LANCZOS)
            layers.append((b_rot, (6, y_cur), 0.0))
            y_cur += b_rot.height - 4

    is_burst_mode = (elem_kind == "center_burst_title") or (str(elem.get("opening_mode") or "") == "store_poster_burst")
    if is_burst_mode:
        main_img = _draw_tilted_stroked_text(
            l1 or main_text[:6], sans_font, 900, 122, (132, 255, 58, 255), (10, 12, 10, 255), stroke_w=11, tilt_deg=-4.5
        )
    else:
        main_img = _draw_tilted_stroked_text(
            l1 or main_text[:6], oblique_font, 860, 96, (148, 255, 62, 255), (12, 22, 10, 255), stroke_w=9, tilt_deg=-4.0
        )
    layers.append((main_img, (14, y_cur), 0.22 if layers else 0.0))
    y_cur += main_img.height - 8

    if l2:
        sub_img = _draw_gradient_3d_text(
            l2, sans_font, 900, 108,
            top_rgb=(255, 118, 96), mid_rgb=(238, 36, 42), bot_rgb=(176, 12, 22),
            extrude_rgb=(45, 8, 12), stroke_rgb=(255, 255, 255), depth_px=6,
        )
        layers.append((sub_img, (max(0, cw - sub_img.width - 6), y_cur - 14), 0.44 if len(layers) >= 2 else 0.22))
        y_cur += sub_img.height + 16
    else:
        y_cur += 16

    ch = max(160, min(560, y_cur + 14))
    # 保证偶数尺寸
    cw = cw & ~1
    ch = (ch + 1) & ~1
    return (cw, ch), layers


def render_style05_giant_back_png(
    text: str,
    out_png: Path,
    accent_hex: str = "#FFD426",
    font_candidates: Optional[List[str]] = None,
) -> Path:
    """
    为 style_05（人像前景压巨字）生成 1080x1920 透明图：
    在人物头肩背后 (y=240..660) 铺满 220px–260px 超大号亮黄粗体字，
    供 FFmpeg 合成 4 层夹心景深：[微虚背景] -> [背后巨字] -> [抠像前景真人] -> [胸前白字提问]。
    """
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    clean_t = str(text or "核心干货").strip()[:4]
    if len(clean_t) < 2:
        clean_t = "核心真相"

    sans_font = find_commercial_font(font_candidates or ["AlimamaShuHeiTi-Bold.otf", "SourceHanSerifCN-Heavy.otf"])
    fg_rgba = hex_to_rgba(accent_hex, default=(255, 212, 38, 255))

    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    _paste_glyph_line(
        canvas, (CANVAS_WIDTH // 2, 445), clean_t, sans_font, CANVAS_WIDTH - 48, 256, 140,
        fg_rgba, stroke_rgba=(38, 26, 4, 210), stroke_w=_sw(6), shadow_rgba=(0, 0, 0, 175), shadow_dy=12,
    )
    canvas.save(out_png)
    return out_png


def letter_spacing(ov_style: Dict[str, Any], default: float) -> float:
    """蓝图里实测的字距（letter_spacing_px）优先，缺省才用分支的默认值。"""
    raw = ov_style.get("letter_spacing_px")
    try:
        v = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        v = None
    return float(default) if v is None else max(0.0, v)


def _render_indicator_sticker_png(elem: Dict[str, Any]) -> Image.Image:
    """程序化渲染无版权指示性贴纸（点赞手势 / 手绘红箭头），支持左/右朝向自适应，画布保持 180x180。"""
    st_kind = str(elem.get("sticker_kind") or "thumbs_up").strip()
    point_dir = str(elem.get("point_dir") or "right").strip().lower()
    w, h = 180, 180
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if st_kind == "red_arrow":
        # 醒目弧形红色指引箭头 + 白色内高光 + 深色厚轮廓；根据 point_dir 指向紧邻文字/主体一侧
        pts = [(28, 34), (56, 22), (118, 92), (140, 74), (154, 146), (82, 134), (102, 114)]
        d.polygon([(x + 4, y + 5) for x, y in pts], fill=(16, 16, 20, 205))
        d.polygon(pts, fill=(244, 52, 52, 255), outline=(255, 255, 255, 248))
        d.line([pts[0], pts[2], pts[4]], fill=(255, 196, 180, 225), width=4)
        if point_dir == "left":
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        return img
    # 默认手绘风点赞手势 + 辐射高光星芒
    rays = [
        ((32, 42), (48, 56)), ((82, 16), (84, 38)), ((132, 34), (118, 52)), ((148, 80), (128, 84)),
    ]
    for p0, p1 in rays:
        d.line([p0, p1], fill=(255, 228, 92, 245), width=6)
    # 拇指 + 拳头主体（暖白填色 + 黑粗漫画描边 + 金黄袖口）
    d.rounded_rectangle([(32, 84), (62, 148)], radius=8, fill=(252, 211, 77, 255), outline=(20, 20, 24, 255), width=5)
    thumb = [(66, 86), (84, 44), (104, 46), (100, 82), (138, 84), (144, 102), (138, 144), (66, 144)]
    d.polygon(thumb, fill=(255, 255, 255, 255), outline=(20, 20, 24, 255))
    d.line([(96, 102), (136, 102)], fill=(20, 20, 24, 220), width=4)
    d.line([(96, 120), (134, 120)], fill=(20, 20, 24, 220), width=4)
    if point_dir == "left":
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return img


def _render_end_card_slate_png(
    elem: Dict[str, Any],
    style_id: str,
    font_candidates: Optional[List[str]] = None,
) -> Optional[Image.Image]:
    """统一渲染片尾收束互动卡：自带半透明深黑磨砂底板 + 细金/强调边线，避免直接漂在亮杂背景下看不清。"""
    main_text = str(elem.get("text_or_asset") or elem.get("text") or "评论区聊聊").strip()
    sub_text = str(elem.get("subtext") or "").strip()
    if sub_text == main_text:
        sub_text = ""
    fnt_p = find_commercial_font(font_candidates) or find_commercial_font()
    accent = _hex_rgb(elem.get("fg_hex") or "#FFE28A", (255, 226, 138))
    cw, ch = 640, (176 if sub_text else 128)
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(2, 2), (cw - 3, ch - 3)], radius=18, fill=(14, 16, 22, 224), outline=(*accent, 210), width=3)
    d.rounded_rectangle([(20, 14), (cw - 20, 19)], radius=3, fill=(*accent, 235))
    y_main = 72 if sub_text else (ch // 2 + 4)
    _paste_glyph_line(img, (cw // 2, y_main), main_text, fnt_p, cw - 64, 54, 30, (*accent, 255),
                      stroke_rgba=(20, 18, 12, 245), stroke_w=3, shadow_rgba=(0, 0, 0, 220), shadow_dy=3)
    if sub_text:
        _paste_glyph_line(img, (cw // 2, 132), sub_text, fnt_p, cw - 72, 34, 22, (248, 248, 250, 255),
                          stroke_rgba=(0, 0, 0, 220), stroke_w=2, shadow_dy=0)
    return img


def _draw_kinetic_line_sprite(
    text: str,
    font_path: Optional[str],
    font_size: int,
    fill_rgba: Tuple[int, int, int, int],
    stroke_rgba: Optional[Tuple[int, int, int, int]] = None,
    stroke_w: int = 0,
    shadow_rgba: Optional[Tuple[int, int, int, int]] = None,
    shadow_dx: int = 3,
    shadow_dy: int = 4,
    shadow_blur: float = 2.0,
    max_w: int = 920,
    min_fs: int = 28,
) -> Optional[Image.Image]:
    """不受全局 _PAINT 限制的高精单行字幕/花字精灵（全量支持粤语字逐字回退，基线严格对齐）。"""
    t = str(text or "").strip()
    if not t:
        return None
    probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    pd = ImageDraw.Draw(probe)
    fs = max(min_fs, int(font_size))
    sw = max(0, int(stroke_w))
    limit = max(60, int(max_w) - sw * 2 - 16)
    while fs > min_fs and sum(pd.textlength(ch, font=glyph_font_for(ch, font_path, fs)) for ch in t) > limit:
        fs -= 2
    char_fonts = [glyph_font_for(ch, font_path, fs) for ch in t]
    widths = [pd.textlength(ch, font=cf) for ch, cf in zip(t, char_fonts)]
    total_w = sum(widths)
    metrics = [cf.getmetrics() for cf in char_fonts]
    ascent = max(m[0] for m in metrics)
    descent = max(m[1] for m in metrics)
    pad = sw + max(10, int(math.ceil(shadow_blur * 2 + max(abs(shadow_dx), abs(shadow_dy)) + 2)))
    w = int(math.ceil(total_w)) + pad * 2 + 4
    h = ascent + descent + pad * 2 + 4

    ink = Image.new("RGBA", (max(8, w), max(8, h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(ink)
    x = float(pad)
    base_y = pad + ascent
    s_fill = stroke_rgba if (sw > 0 and stroke_rgba) else None
    for ch, cw_px, cf in zip(t, widths, char_fonts):
        d.text((x, base_y), ch, font=cf, fill=fill_rgba, anchor="ls", stroke_width=sw, stroke_fill=s_fill)
        x += cw_px

    if shadow_rgba is not None and shadow_rgba[3] > 0:
        alpha = ink.split()[3].point(lambda v: int(v * shadow_rgba[3] / 255.0))
        shade = Image.new("RGBA", ink.size, (shadow_rgba[0], shadow_rgba[1], shadow_rgba[2], 255))
        shadow = Image.new("RGBA", ink.size, (0, 0, 0, 0))
        shadow.paste(shade, (0, 0), alpha)
        if shadow_blur > 0.2:
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        out = Image.new("RGBA", ink.size, (0, 0, 0, 0))
        out.alpha_composite(shadow, dest=(max(0, int(shadow_dx)), max(0, int(shadow_dy))))
        out.alpha_composite(ink)
        ink = out
    box = ink.getbbox()
    return ink.crop(box) if box else None


V6_CARD_VARIANTS = frozenset({"negation_line", "pillar_badge", "concept_card", "semantic_diagram_card"})
TICKER_VARIANT = "editorial_ticker"


_SKIN_PALETTE_DEFAULTS: Dict[str, Dict[str, str]] = {
    "style_01": {"white": "#FFFDF7", "lime": "#D4F542", "red": "#DC2626", "pill_bg": "#18181B"},
    "style_03": {"white": "#F8FAFC", "lime": "#F59E0B", "red": "#EF4444", "pill_bg": "#0F172A"},
    "style_17": {"white": "#FAF8F5", "lime": "#CA8A04", "red": "#B91C1C", "pill_bg": "#1C1917"},
}


def kinetic_item_paint(elem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Shared paint for V6 sprites and editable native text."""
    variant = elem.get("variant")
    editorial = elem.get("theme_profile") == "business_editorial"
    if not editorial and variant not in V6_CARD_VARIANTS and variant != TICKER_VARIANT:
        return None
    skin_id = str(elem.get("style_id") or "").strip()
    skin_pal = elem.get("style_palette") if isinstance(elem.get("style_palette"), dict) else _SKIN_PALETTE_DEFAULTS.get(skin_id)
    if skin_id == "style_02":
        skin_pal = None
    accent = str(skin_pal.get("lime")) if (skin_pal and skin_pal.get("lime")) else ("#EAB308" if editorial else "#BCFF00")
    ink = str(skin_pal.get("white")) if (skin_pal and skin_pal.get("white")) else ("#F8FAFC" if editorial else "#FFFFFF")
    dark = "#0C0F16" if editorial else "#1C201E"
    if skin_pal and str(skin_pal.get("pill_bg") or "").upper() not in ("#FFFFFF", "#FFFDF7"):
        dark = str(skin_pal["pill_bg"])
    marker = str(skin_pal.get("red")) if (skin_pal and skin_pal.get("red")) else ("#DC2626" if editorial else "#B71C1C")
    if variant == TICKER_VARIANT:
        return {
            "fg_hex": "#E2E8F0", "bg_hex": dark, "bg_alpha": 0.74, "accent_hex": accent,
            "marker_hex": marker, "radius_px": 11, "outline_px": 1,
        }
    badge = variant == "pillar_badge"
    badge_fg = "#0C0F16" if skin_pal else dark
    return {
        "fg_hex": badge_fg if badge else (accent if variant == "hero_lime" else ink),
        "bg_hex": accent if badge else dark,
        "bg_alpha": 0.97 if badge else 0.80,
        "accent_hex": accent,
        "marker_hex": marker,
        "radius_px": 19 if badge else (15 if editorial else 10),
        "outline_px": 1 if editorial else 2,
    }


def _pillar_progress(elem: Dict[str, Any]) -> Optional[tuple[int, int]]:
    """结构进度格：(当前第几格, 总格数)；不足 2 格时不画。"""
    if str(elem.get("variant") or "") != "pillar_badge":
        return None
    total, index = elem.get("pillar_total"), elem.get("pillar_index")
    if type(total) is not int or type(index) is not int or total < 2:
        return None
    return max(1, min(total, index)), total


_PILLAR_BAR_GAP_PX = 4
_PILLAR_BAR_THICK_PX = 4


def _pillar_progress_layer(
    width: int, index: int, total: int, accent: Tuple[int, int, int],
) -> Image.Image:
    """在胶囊下方留 4px 间隙，再铺 total 个 4px 细格：已走完的格用赛道主色。"""
    inset, gap = 6, 3
    bar_h = _PILLAR_BAR_THICK_PX
    layer = Image.new("RGBA", (width, _PILLAR_BAR_GAP_PX + bar_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    usable = max(6.0, float(width - inset * 2))
    cell_w = max(2.0, (usable - gap * (total - 1)) / float(total))
    top = _PILLAR_BAR_GAP_PX
    for cell in range(total):
        x0 = inset + cell * (cell_w + gap)
        x1 = min(float(width - inset), x0 + cell_w)
        if x1 <= x0:
            continue
        fill = (*accent, 255) if cell < index else (255, 255, 255, 72)
        draw.rounded_rectangle((x0, top, x1, top + bar_h), radius=1, fill=fill)
    return layer


def _kinetic_marked_row(
    label: str, font: Optional[str], size: int, color: Tuple[int, int, int], limit: int,
    marker: Tuple[int, int, int], accent: Tuple[int, int, int], force_mark: bool = False,
) -> Optional[Image.Image]:
    """单行带 ✕/✓ 记号的花字精灵；字身与记号共用同一套几何，动效层也复用。"""
    prefix = label[:1] if label[:1] in {"✕", "✓"} else ""
    if not prefix and force_mark:
        prefix = "✕"
    words = label[1:].strip() if label[:1] in {"✕", "✓"} else label
    mark = max(16, round(size * 0.55)) if prefix else 0
    gap = mark // 2
    sprite = _draw_kinetic_line_sprite(words, font, size, (*color, 255),
                                       max_w=limit - mark - gap, min_fs=20)
    if not prefix or sprite is None:
        return sprite
    row = Image.new("RGBA", (mark + gap + sprite.width, max(mark, sprite.height)))
    row.alpha_composite(sprite, dest=(mark + gap, (row.height - sprite.height) // 2))
    center_y = row.height // 2
    draw = ImageDraw.Draw(row)
    pen = max(3, round(mark * 0.17))
    if prefix == "✕":
        draw.line(((2, center_y - mark // 3), (mark - 2, center_y + mark // 3)), fill=(*marker, 255), width=pen)
        draw.line(((2, center_y + mark // 3), (mark - 2, center_y - mark // 3)), fill=(*marker, 255), width=pen)
    else:
        draw.line(((2, center_y), (mark // 3, center_y + mark // 4), (mark - 2, center_y - mark // 3)),
                  fill=(*accent, 255), width=pen, joint="curve")
    return row


def _render_v6_kinetic_sprite(
    elem: Dict[str, Any], text: str, font: Optional[str], font_px: int, paint: Dict[str, Any],
) -> Optional[Image.Image]:
    variant = elem.get("variant")
    if variant == "semantic_diagram_card":
        layout = _semantic_diagram_layout(elem, font, paint, 2.0)
        if layout is None:
            return None
        cw, ch = layout["canvas"]
        state = {
            "layers": {ly["name"]: {"alpha": 1.0, "scale": 1.0, "dx": 0.0, "dy": 0.0} for ly in layout["layers"]},
            "vectors": {v["key"]: 1.0 for v in layout["vectors"]},
            "vector_alpha": 1.0,
        }
        raw_bytes = _element_frame_bytes(layout, state)
        im = Image.frombytes("RGBA", (cw, ch), raw_bytes)
        ink = im.getbbox()
        return im.crop(ink) if ink else im
    is_card = variant == "concept_card"
    is_ticker = variant == TICKER_VARIANT
    card_lines = [str(line).strip() for line in elem.get("card_lines") or [] if str(line).strip()]
    heading = bool(is_card and elem.get("card_has_headline") and card_lines)
    lines = card_lines if is_card and card_lines else text.splitlines()
    columns = is_card and len(lines) - int(heading) == 2
    fg = _hex_rgb(paint["fg_hex"])
    accent = _hex_rgb(paint["accent_hex"])
    marker = _hex_rgb(paint["marker_hex"])

    def draw_line(label: str, size: int, color: Tuple[int, int, int], limit: int) -> Optional[Image.Image]:
        return _kinetic_marked_row(
            label, font, size, color, limit, marker, accent, force_mark=variant == "negation_line",
        )

    rendered = []
    for idx, line in enumerate(lines):
        header_line = heading and idx == 0
        if is_card:
            size = min(font_px, 49 if header_line else (36 if columns else 44))
            limit = 182 if columns and not header_line else 398
        elif is_ticker:
            size = min(font_px, 30)
            limit = 300
        elif variant in {"bridge_bar", "pillar_badge", "negation_line"}:
            size = min(font_px, 36 if elem.get("base_caption") else 54)
            limit = 600
        else:
            size = min(font_px, 112)
            limit = 680
        sprite = draw_line(line, size, accent if header_line else fg, limit)
        if sprite:
            rendered.append(sprite)
    if not rendered:
        return None

    pad_x, pad_y, gap_y = (15, 10, 10) if is_card else ((12, 6, 4) if is_ticker else (16, 8, 5))
    progress = _pillar_progress(elem)
    bar_area = (_PILLAR_BAR_GAP_PX + _PILLAR_BAR_THICK_PX) if progress else 0
    detail_gap = 22
    preceding = rendered[:1] if heading else []
    details = rendered[len(preceding):]
    use_columns = columns and len(details) == 2
    if use_columns:
        left, right = details
        body_w = left.width + right.width + detail_gap
        body_h = max(left.height, right.height)
    else:
        body_w = max((sprite.width for sprite in details), default=0)
        body_h = sum(sprite.height for sprite in details) + gap_y * max(0, len(details) - 1)
    title_h = preceding[0].height if preceding else 0
    gap_title = gap_y if title_h and body_h else 0
    width = max(body_w, preceding[0].width if preceding else 0) + 2 * pad_x
    body_height = title_h + gap_title + body_h + 2 * pad_y
    card = Image.new("RGBA", (width, body_height + bar_area))
    draw = ImageDraw.Draw(card)
    radius = paint["radius_px"]
    draw.rounded_rectangle(
        (0, 0, width - 1, body_height - 1), radius=radius,
        fill=(*_hex_rgb(paint["bg_hex"]), round(255 * paint["bg_alpha"])),
        outline=(255, 255, 255, 48 if not is_ticker else 78), width=paint["outline_px"],
    )
    if preceding:
        card.alpha_composite(preceding[0], dest=((width - preceding[0].width) // 2, pad_y))
    y = pad_y + title_h + gap_title
    if use_columns:
        x = (width - body_w) // 2
        divider = x + left.width + detail_gap // 2
        draw.line((divider, y + 1, divider, y + body_h - 1), fill=(255, 255, 255, 48))
        card.alpha_composite(left, dest=(x, y + (body_h - left.height) // 2))
        card.alpha_composite(right, dest=(x + left.width + detail_gap, y + (body_h - right.height) // 2))
    else:
        for sprite in details:
            card.alpha_composite(sprite, dest=((width - sprite.width) // 2, y))
            y += sprite.height + gap_y
    if progress is not None:
        index, total = progress
        card.alpha_composite(_pillar_progress_layer(width, index, total, accent),
                             dest=(0, body_height))
    return card


def render_kinetic_stage_item_png(
    elem: Dict[str, Any],
    font_candidates: Optional[List[str]] = None,
) -> Optional[Image.Image]:
    """
    渲染 `style_02` 动态花字舞台组件（严格还原原版 7 类视觉质感）：
    - hero_white: 纯白粗字 + 轻微暗影描边（如 `2024年` / `全面解放老板`）
    - hero_lime: 荧光黄绿 #BCFF00 + 轻微暗影描边（如 `实体工厂老板` / `6000+`）
    - red_pill: 白色圆角微底条 + 暗红 #B71C1C 粗字（如 `要把短视频做起来` / `都在下场`）
    - red_slam: 暗红 #B71C1C 巨字 + 清晰白描边外加浓阴影（如 `IP难做` / `好傻`）
    - bridge_bar: 半透明黑底 rgba(0,0,0,0.70) + 紧凑圆角 + 白字（呼吸过渡短句）
    - inline_rich: 单行内双色双字号混排强调（如 `想知道`白 + `如何落地`荧光绿）
    - danmaku_line: 无底框清爽白字 + 精细描边（人后滚动弹幕）
    """
    text = str(elem.get("text_or_asset") or elem.get("text") or "").strip()
    if not text:
        return None
    variant = str(elem.get("variant") or "hero_white")
    font_px = max(30, int(elem.get("font_size_px") or 88))
    cands = font_candidates or ["SourceHanSerifCN-Bold.otf", "AlimamaShuHeiTi-Bold.otf", "SourceHanSansCN-Bold.otf"]
    serif_f = find_commercial_font(cands)
    sans_f = find_commercial_font(["SourceHanSansCN-Bold.otf", "AlimamaShuHeiTi-Bold.otf"]) or serif_f
    paint = kinetic_item_paint(elem)
    if paint is not None:
        return _render_v6_kinetic_sprite(elem, text, sans_f, font_px, paint)

    if variant == "inline_rich" and isinstance(elem.get("inline_parts"), list) and elem["inline_parts"]:
        parts_imgs: List[Image.Image] = []
        for part in elem["inline_parts"]:
            pt = str(part.get("text") or "").strip()
            if not pt:
                continue
            p_rgb = _hex_rgb(part.get("color") or "#FFFFFF")
            p_fs = max(36, int(round(float(part.get("size_units") or 16.0) * 5.4)))
            sp = _draw_kinetic_line_sprite(
                pt, serif_f, p_fs, (*p_rgb, 255),
                stroke_rgba=(0, 0, 0, 235), stroke_w=3,
                shadow_rgba=(0, 0, 0, 175), shadow_dx=3, shadow_dy=4, shadow_blur=2.0,
                max_w=540,
            )
            if sp is not None:
                parts_imgs.append(sp)
        if not parts_imgs:
            return None
        gap = 8
        tw = sum(im.width for im in parts_imgs) + gap * max(0, len(parts_imgs) - 1)
        th = max(im.height for im in parts_imgs)
        if tw > 940:
            sc = 940.0 / float(tw)
            parts_imgs = [
                im.resize((max(4, int(im.width * sc)), max(4, int(im.height * sc))), Image.Resampling.LANCZOS)
                for im in parts_imgs
            ]
            tw = sum(im.width for im in parts_imgs) + gap * max(0, len(parts_imgs) - 1)
            th = max(im.height for im in parts_imgs)
        cv = Image.new("RGBA", (tw + 4, th + 4), (0, 0, 0, 0))
        x_cur = 2
        for im in parts_imgs:
            cv.alpha_composite(im, dest=(x_cur, th - im.height + 2))
            x_cur += im.width + gap
        box = cv.getbbox()
        return cv.crop(box) if box else cv

    if variant == "red_pill":
        sp = _draw_kinetic_line_sprite(
            text, serif_f, min(72, max(44, int(font_px * 0.72))),
            (183, 28, 28, 255),
            stroke_rgba=None, stroke_w=0, shadow_rgba=None,
            max_w=820,
        )
        if sp is None:
            return None
        px_pad, py_pad = 24, 12
        pw, ph = sp.width + px_pad * 2, sp.height + py_pad * 2
        card = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        d.rounded_rectangle([(0, 0), (pw - 1, ph - 1)], radius=10, fill=(255, 255, 255, 248))
        card.alpha_composite(sp, dest=(px_pad, py_pad))
        return card

    if variant == "bridge_bar":
        sp = _draw_kinetic_line_sprite(
            text, serif_f, min(68, max(42, int(font_px * 0.64))),
            (255, 255, 255, 255),
            stroke_rgba=(0, 0, 0, 180), stroke_w=1,
            shadow_rgba=(0, 0, 0, 110), shadow_dx=2, shadow_dy=2, shadow_blur=1.0,
            max_w=800,
        )
        if sp is None:
            return None
        px_pad, py_pad = 22, 11
        pw, ph = sp.width + px_pad * 2, sp.height + py_pad * 2
        card = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        d.rounded_rectangle([(0, 0), (pw - 1, ph - 1)], radius=8, fill=(0, 0, 0, 178))
        card.alpha_composite(sp, dest=(px_pad, py_pad))
        return card

    if variant == "red_slam":
        return _draw_kinetic_line_sprite(
            text, serif_f, min(144, max(82, int(font_px * 0.90))),
            (196, 26, 26, 255),
            stroke_rgba=(255, 255, 255, 245), stroke_w=6,
            shadow_rgba=(0, 0, 0, 210), shadow_dx=4, shadow_dy=6, shadow_blur=3.0,
            max_w=880,
        )

    if variant == "danmaku_line":
        return _draw_kinetic_line_sprite(
            text, sans_f, min(72, max(46, int(font_px * 0.66))),
            (255, 255, 255, 250),
            stroke_rgba=(0, 0, 0, 240), stroke_w=3,
            shadow_rgba=(0, 0, 0, 180), shadow_dx=2, shadow_dy=3, shadow_blur=1.5,
            max_w=780,
        )

    # hero_white / hero_lime
    fg_rgb = _hex_rgb(elem.get("fg_hex") or ("#BCFF00" if variant == "hero_lime" else "#FFFFFF"))
    eff_fs = min(140, max(54, int(font_px * 0.86)))
    return _draw_kinetic_line_sprite(
        text, serif_f, eff_fs,
        (*fg_rgb, 255),
        stroke_rgba=(0, 0, 0, 235), stroke_w=4,
        shadow_rgba=(0, 0, 0, 195), shadow_dx=4, shadow_dy=5, shadow_blur=2.4,
        max_w=880,
    )


def render_template_packaging_png(
    elem: Dict[str, Any],
    style_id: str = "style_01",
    font_candidates: Optional[List[str]] = None,
) -> Optional[Image.Image]:
    """
    依据 24 套剪映模板蓝图渲染高还原度透明叠层：
    返回 RGBA Image（若无需特化则回退给 render_mp4 默认渲染）。
    """
    elem_kind = str(elem.get("kind") or elem.get("type") or elem.get("element_type") or "flower_text")
    if elem_kind == "indicator_sticker":
        return _render_indicator_sticker_png(elem)
    if elem_kind == "end_card_cta":
        return _render_end_card_slate_png(elem, style_id=style_id, font_candidates=font_candidates)
    if elem_kind == "kinetic_stage_item":
        return render_kinetic_stage_item_png(elem, font_candidates=font_candidates)
    flower_paint(style_id)  # 先按该风格实测值设定本次花字的描边/阴影画法
    bp = get_style_blueprint(style_id)
    family = str(bp.get("template_family") or "")
    ov_style = bp.get("overlay_style") or {}
    if not family:
        return None
    kw_hex = str((bp.get("subtitle_override") or {}).get("keyword_color") or "")
    pal = style_flower_palette(ov_style, style_id, kw_hex)

    elem_kind = str(elem.get("kind") or elem.get("type") or elem.get("element_type") or "flower_text")
    main_text = str(elem.get("text_or_asset") or elem.get("text") or "").strip()
    sub_text = str(elem.get("subtext") or elem.get("tag_label") or "").strip()
    if not main_text:
        return None

    font_path = find_commercial_font(font_candidates)
    serif_font = find_commercial_font(["SourceHanSerifCN-Heavy.otf", "SourceHanSerifCN-Bold.otf"]) or font_path
    sans_font = find_commercial_font(["AlimamaShuHeiTi-Bold.otf", "SmileySans-Oblique.ttf"]) or font_path
    oblique_font = find_commercial_font(["SmileySans-Oblique.ttf", "AlimamaShuHeiTi-Bold.otf"]) or font_path
    hand_font = find_commercial_font([ov_style.get("handwriting_font") or "MaShanZheng-Regular.ttf", "SourceHanSerifCN-Heavy.otf"]) or serif_font
    # 各风格实测源字幕字体（不在随仓白名单内时 find_commercial_font 自动回退到最接近的商用字体）
    douyin_font = find_commercial_font(["DouyinSansBold.otf", "AlimamaShuHeiTi-Bold.otf"]) or sans_font
    zhixin_font = find_commercial_font(["知新体.ttf", "AlimamaShuHeiTi-Bold.otf"]) or sans_font
    deyi_font = find_commercial_font(["得意黑.ttf", "SmileySans-Oblique.ttf"]) or sans_font
    jy_serif_font = find_commercial_font(["造字工房俊雅体.ttf", "SourceHanSerifCN-Bold.otf"]) or serif_font
    impact_font = find_commercial_font(["FZZJ-JHTJW.ttf", "SmileySans-Oblique.ttf"]) or oblique_font

    # 1. style_01（默认门店爆款风）：3 拍错落综艺贴纸
    if family == "store_poster_burst":
        (cw, ch), layers = _build_style01_layers(elem, sans_font, oblique_font)
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        for img_l, (dx, dy), _delay in layers:
            canvas.alpha_composite(img_l, dest=(dx, dy))
        ink = canvas.getbbox()
        return canvas.crop(ink) if ink else None

    # 2. style_02：极速跳剪暗灰圆角底框 + 白/荧光黄绿双行斜粗体
    if family == "dark_gray_neon_slab":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 760, (176 if l2 else 112)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=20, fill=(28, 32, 30, 224))
        if l2:
            _paste_glyph_line(img, (w // 2, 54), l1, oblique_font, w - 56, 56, 30, (255, 255, 255, 255), (0, 0, 0, 240), _sw(3), shadow_dy=0)
            _paste_glyph_line(img, (w // 2, 124), l2, oblique_font, w - 56, 60, 32, (188, 255, 0, 255), (0, 0, 0, 240), _sw(3), shadow_dy=0)
        else:
            _paste_glyph_line(img, (w // 2, h // 2), l1, oblique_font, w - 56, 56, 30, (188, 255, 0, 255), (0, 0, 0, 240), _sw(3), shadow_dy=0)
        return img

    # 3. style_03：暗调深度深红实心底条双行大字
    if family == "red_bar_dual_line":
        from style_dna_profiles import resolve_style03_bar_layout

        layout = resolve_style03_bar_layout(
            main_text, str(elem.get("subtext") or ""), elem_kind, str(elem.get("display_role") or ""),
        )
        quote = layout["bar_mode"] == "quote_citation_card"
        l1, l2 = layout["main_text"], layout["sub_text"]
        if not quote and not l2:
            l1, l2 = _split_two_lines(l1, max_first=8)
        render_font = find_commercial_font([*(font_candidates or ()), *layout["font_candidates"]]) or serif_font
        w = layout["width_px"]
        text_w = min(w - 64, layout["text_width_px"])
        main_fs = layout["giant_font_size_px"] if elem_kind == "center_burst_title" else layout["font_size_px"]
        sh = layout["shadow"]
        rows = []
        for line, size, color in (
            (l1, 42 if quote else main_fs, layout["main_hex"]),
            (l2, 28 if quote else layout["font_size_px"], layout["sub_hex"]),
        ):
            if sh["shadow_rgba"][3] > 0:
                s_ratio = min(1.0, max(0.22, float(size) / max(1.0, float(layout["giant_font_size_px"]))))
                sh_kwargs = {
                    "shadow_rgba": (*sh["shadow_rgba"][:3], min(sh["shadow_rgba"][3], 150 if not quote else 135)),
                    "shadow_dx": min(4 if not quote else 2, max(1, round(int(sh["shadow_dx"]) * s_ratio * 0.45))),
                    "shadow_dy": min(5 if not quote else 3, max(2, round(int(sh["shadow_dy"]) * s_ratio * 0.50))),
                    "shadow_blur": max(float(sh["shadow_blur"]), 3.0 if not quote else 2.2),
                }
            else:
                sh_kwargs = dict(sh)
            sprite = _draw_kinetic_line_sprite(
                " ".join(line.splitlines()), render_font, size, hex_to_rgba(color), max_w=text_w,
                stroke_w=layout["stroke_px"], **sh_kwargs,
            )
            if sprite is not None:
                if sprite.width > text_w:
                    sprite = sprite.resize(
                        (text_w, max(1, round(sprite.height * text_w / sprite.width))), Image.Resampling.LANCZOS,
                    )
                rows.append(sprite)
        if not rows:
            return None
        row_heights = (
            [row.height + 12 for row in rows] if quote
            else [max(row.height for row in rows) + 26] * len(rows)
        )
        h = sum(row_heights)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=layout["radius_px"], fill=layout["bg_rgba"])
        if quote:
            d.rectangle((14, 10, 18, h - 11), fill=hex_to_rgba(layout["bar_hex"], 238))
        y = 0
        for row, row_h in zip(rows, row_heights):
            img.alpha_composite(row, dest=((w - row.width) // 2, y + (row_h - row.height) // 2))
            y += row_h
        return img

    # 4. style_05：人像前景压巨字 · 胸前高对比白字提问层（第 4 层）
    if family == "person_depth_giant_title":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 860, (176 if l2 else 112)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if l2:
            _paste_glyph_line(img, (w // 2, 52), l1, sans_font, w - 48, 66, 34, (255, 255, 255, 255), (10, 10, 14, 255), _sw(7), (0, 0, 0, 200), 4)
            _paste_glyph_line(img, (w // 2, 124), l2, sans_font, w - 48, 66, 34, (255, 212, 38, 255), (10, 10, 14, 255), _sw(7), (0, 0, 0, 200), 4)
        else:
            _paste_glyph_line(img, (w // 2, h // 2), l1, sans_font, w - 48, 66, 34, (255, 255, 255, 255), (10, 10, 14, 255), _sw(7), (0, 0, 0, 200), 4)
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 5. style_18：户外探盘 3D 金属渐变立体大字
    if family == "metallic_3d_gold_title":
        return _draw_gradient_3d_text(
            main_text[:10], sans_font, 820,
            font_size=108 if elem_kind == "center_burst_title" else 82,
            top_rgb=(255, 248, 186), mid_rgb=(255, 194, 34), bot_rgb=(218, 120, 14),
            extrude_rgb=(78, 36, 8), stroke_rgb=(48, 22, 4), depth_px=10,
        )

    # 6. style_08：专家长镜左上角斜杠双色大标
    if family == "top_left_slash_badge":
        l1, l2 = _split_two_lines(main_text, max_first=7)
        top_line = f"/{l1}/"
        bot_line = l2 or sub_text[:8] or "真实干货"
        w, h = 680, 170
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        _paste_glyph_line(img, (22, 36), top_line, sans_font, w - 30, 48, 28, (255, 255, 255, 255), (15, 23, 42, 245), _sw(5), shadow_dy=0, anchor="lm")
        _paste_glyph_line(img, (22, 114), bot_line, sans_font, w - 30, 64, 34, (255, 228, 72, 255), (15, 23, 42, 255), _sw(7), shadow_dy=0, anchor="lm")
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 7. style_06：黑底拍立得顶部两行真实毛笔手写体金句 (MaShanZheng-Regular.ttf)
    if family == "black_polaroid_vlog":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 860, 228
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if l2:
            _paste_glyph_line(img, (w // 2, 62), l1, hand_font, w - 30, 90, 44, (255, 255, 255, 252), shadow_dy=0)
            _paste_glyph_line(img, (w // 2, 158), l2, hand_font, w - 30, 90, 44, (255, 255, 255, 252), shadow_dy=0)
        else:
            _paste_glyph_line(img, (w // 2, 114), l1, hand_font, w - 30, 90, 44, (255, 255, 255, 252), shadow_dy=0)
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 8. style_12：顶置双色悬念标 · 干货对比口播风（纯白+荧光黄绿跨幅双行大标题）
    if family == "top_dual_color_hook_header":
        l1, l2 = _split_two_lines(main_text, max_first=7)
        if not l2 and sub_text and sub_text != "核心重点":
            l2 = sub_text[:9]
        w, h = 920, (192 if l2 else 118)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if l2:
            _paste_glyph_line(img, (w // 2, 56), l1, sans_font, w - 48, 68, 36, (255, 255, 255, 255), (12, 12, 14, 255), _sw(7), (0, 0, 0, 210), 4)
            _paste_glyph_line(img, (w // 2, 138), l2, sans_font, w - 48, 74, 38, (198, 255, 0, 255), (12, 12, 14, 255), _sw(7), (0, 0, 0, 210), 4)
        else:
            _paste_glyph_line(img, (w // 2, h // 2), l1, sans_font, w - 48, 68, 36, (198, 255, 0, 255), (12, 12, 14, 255), _sw(7), (0, 0, 0, 210), 4)
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 9. style_14：纯黑聚光圆窗 · 极简聚焦大字（圆窗正下方干净白/金主标）
    if family == "black_spotlight_focus":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 780, (162 if l2 else 108)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if l2:
            _paste_glyph_line(img, (w // 2, 46), l1, sans_font, w - 40, 58, 32, (255, 255, 255, 252), shadow_dy=0)
            _paste_glyph_line(img, (w // 2, 114), l2, sans_font, w - 40, 62, 34, (255, 191, 23, 255), shadow_dy=0)
        else:
            _paste_glyph_line(img, (w // 2, h // 2 - 6), l1, sans_font, w - 40, 58, 32, (255, 255, 255, 252), shadow_dy=0)
        d.line([(w // 2 - 110, h - 8), (w // 2 + 110, h - 8)], fill=(255, 191, 23, 180), width=3)
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 10. style_20：国风养生下半屏黑条双行米白粗宋体 + 朱红印
    if family == "guofeng_black_band_serif":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 840, (176 if l2 else 112)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([(0, 0), (w - 1, h - 1)], fill=(18, 14, 11, 196))
        d.line([(24, 6), (w - 24, 6)], fill=(200, 155, 84, 150), width=2)
        d.line([(24, h - 7), (w - 24, h - 7)], fill=(200, 155, 84, 150), width=2)
        if l2:
            _paste_glyph_line(img, (w // 2 - 20, 54), l1, serif_font, w - 120, 56, 30, (247, 239, 225, 255), shadow_dy=0)
            _paste_glyph_line(img, (w // 2 - 20, 124), l2, serif_font, w - 120, 56, 30, (242, 201, 125, 255), shadow_dy=0)
        else:
            _paste_glyph_line(img, (w // 2 - 20, h // 2), l1, serif_font, w - 120, 56, 30, (247, 239, 225, 255), shadow_dy=0)
        d.rounded_rectangle([(w - 76, h // 2 - 30), (w - 24, h // 2 + 30)], radius=6, fill=(166, 26, 28, 235), outline=(242, 201, 125, 230), width=2)
        sf = get_cached_font(serif_font, 22)
        d.text((w - 50, h // 2 - 12), "真", font=sf, fill=(255, 248, 232, 255), anchor="mm")
        d.text((w - 50, h // 2 + 12), "言", font=sf, fill=(255, 248, 232, 255), anchor="mm")
        return img

    # 11. style_04 / style_13 / style_23 以外的通用轻阴影宋体兜底
    if family == "cinema_serif_quiet":
        w, h = 760, 110
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        _paste_glyph_line(img, (w // 2, h // 2), main_text[:12], serif_font, w - 40, 54, 30,
                          (255, 252, 245, 255), (32, 28, 24, 210), _sw(2), (0, 0, 0, 160), 3)
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 12. style_09：纯黑纪实面板（左圆头像右侧大标题 + 细金线）
    if family == "dark_doc_dual_gold_panel":
        w, h = 580, 124
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        _paste_glyph_line(img, (12, 46), main_text[:12], sans_font, w - 24, 52, 28, (248, 246, 240, 255), shadow_dy=0, anchor="lm")
        d.line([(12, 92), (min(w - 20, 380), 92)], fill=(204, 169, 102, 240), width=4)
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 13. style_19：冷峻数据新闻白底黑字精悍条
    if family == "cool_data_news_cutout":
        sp = _text_sprite(main_text[:12], sans_font, 680, 48, (15, 23, 42, 255), min_fs=26)
        bw = (sp.width if sp else 120) + 56
        bh = (sp.height if sp else 48) + 28
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (bw - 1, bh - 1)], radius=6, fill=(255, 255, 255, 238))
        d.rectangle([(0, 0), (10, bh - 1)], fill=(3, 105, 161, 255))
        if sp is not None:
            img.alpha_composite(sp, dest=(max(14, (bw - sp.width) // 2 + 4), max(0, (bh - sp.height) // 2)))
        return img

    # 14. style_16：暗黑羽化书摘巨字（支持三格巨字与书摘双行金句）
    if family == "dark_feather_book_quote":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 780, (176 if l2 else 116)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if l2:
            _paste_glyph_line(img, (w // 2, 52), l1, serif_font, w - 40, 70, 38, (255, 255, 255, 255), shadow_dy=0)
            _paste_glyph_line(img, (w // 2, 128), l2, serif_font, w - 40, 70, 38, (250, 204, 21, 255), shadow_dy=0)
        else:
            _paste_glyph_line(img, (w // 2, h // 2), l1, serif_font, w - 40, 70, 38, (250, 204, 21, 255), shadow_dy=0)
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 15. style_22：顶栏常驻醒目横幅
    if family == "top_banner_case_study":
        w, h = 920, 120
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=14, fill=(11, 13, 18, 238), outline=(250, 204, 21, 235), width=3)
        _paste_glyph_line(img, (w // 2, h // 2), main_text[:14], sans_font, w - 60, 56, 30, (250, 204, 21, 255), shadow_dy=0)
        return img

    # 16. style_04：清冷奶油高光 · 极简轻描边宋体（留白多、无底框、无贴纸）
    if family == "cream_minimal_serif":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        layers: List[Optional[Image.Image]] = []
        if elem_kind == "center_burst_title":
            layers.append(
                _draw_gradient_3d_text(
                    l1 or main_text[:8], serif_font, 860, font_size=96,
                    top_rgb=pal["text"], mid_rgb=pal["light"], bot_rgb=pal["accent"],
                    extrude_rgb=_mix(pal["accent"], pal["stroke"], 0.52),
                    stroke_rgb=pal["stroke"], depth_px=3,
                )
            )
        else:
            layers.append(
                _text_sprite(
                    l1 or main_text[:10], serif_font, 820, 78, (*pal["text"], 250),
                    stroke_rgba=(*pal["stroke"], 160), stroke_w=2,
                    shadow_rgba=(0, 0, 0, 105), shadow_dy=3, spacing_px=letter_spacing(ov_style, 5.0),
                )
            )
        if l2:
            layers.append(
                _text_sprite(
                    l2, serif_font, 780, 54, (*pal["accent"], 252),
                    stroke_rgba=(*pal["stroke"], 140), stroke_w=2,
                    shadow_rgba=(0, 0, 0, 95), shadow_dy=2, spacing_px=letter_spacing(ov_style, 3.0),
                )
            )
        return _stack_sprites(layers, gap=14)

    # 17. style_07：人像真轮廓白边抠像 · 白描边字压虚化实景（字距紧）
    if family == "white_outline_cutout":
        l1, l2 = _split_two_lines(main_text, max_first=7)
        layers = []
        if elem_kind == "center_burst_title":
            layers.append(
                _draw_gradient_3d_text(
                    l1 or main_text[:6], douyin_font, 860, font_size=104,
                    top_rgb=pal["text"], mid_rgb=pal["text"], bot_rgb=_light(pal["accent"], 0.45),
                    extrude_rgb=_mix(pal["accent"], (170, 170, 176), 0.55),
                    stroke_rgb=pal["text"], depth_px=5,
                )
            )
        else:
            layers.append(
                _text_sprite(
                    l1 or main_text[:8], douyin_font, 800, 84, (*pal["text"], 255),
                    stroke_rgba=(*pal["stroke"], 255), stroke_w=9,
                    shadow_rgba=(0, 0, 0, 135), shadow_dy=6, spacing_px=letter_spacing(ov_style, 0.0),
                )
            )
        if l2:
            layers.append(
                _text_sprite(
                    l2, douyin_font, 760, 66, (*pal["accent"], 255),
                    stroke_rgba=(*pal["stroke"], 255), stroke_w=8,
                    shadow_rgba=(0, 0, 0, 130), shadow_dy=5, spacing_px=letter_spacing(ov_style, 0.0),
                )
            )
        return _stack_sprites(layers, gap=6)

    # 18. style_10：高频推拉微距 · 极净全屏（无描边无底框，重点词压亮黄）
    if family == "clean_punch_minimal":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        layers = []
        if elem_kind == "center_burst_title":
            layers.append(
                _draw_gradient_3d_text(
                    l1 or main_text[:8], zhixin_font, 880, font_size=104,
                    top_rgb=pal["text"], mid_rgb=_light(pal["accent"], 0.45), bot_rgb=pal["accent"],
                    extrude_rgb=_shade(pal["accent"], 0.66), stroke_rgb=pal["stroke"], depth_px=5,
                )
            )
        else:
            layers.append(
                _text_sprite(
                    l1 or main_text[:11], zhixin_font, 860, 96, (255, 255, 255, 255),
                    shadow_rgba=(0, 0, 0, 125), shadow_dy=4, spacing_px=letter_spacing(ov_style, 2.0),
                )
            )
        if l2:
            layers.append(
                _text_sprite(
                    l2, zhixin_font, 820, 74, (*pal["accent"], 255),
                    shadow_rgba=(0, 0, 0, 120), shadow_dy=3, spacing_px=letter_spacing(ov_style, 2.0),
                )
            )
        return _stack_sprites(layers, gap=4)

    # 19. style_11：清亮知性干货 · 无底框通透大字（靠字重与字号分级，重点词压深红）
    if family == "clean_creator_studio":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        layers = []
        if elem_kind == "center_burst_title":
            layers.append(
                _draw_gradient_3d_text(
                    l1 or main_text[:8], sans_font, 880, font_size=100,
                    top_rgb=pal["text"], mid_rgb=_light(pal["accent"], 0.90), bot_rgb=_light(pal["accent"], 0.80),
                    extrude_rgb=_shade(pal["accent"], 0.42), stroke_rgb=_shade(pal["accent"], 0.62), depth_px=4,
                )
            )
        else:
            layers.append(
                _text_sprite(
                    l1 or main_text[:11], sans_font, 860, 92, (255, 255, 255, 255),
                    shadow_rgba=(0, 0, 0, 110), shadow_dy=3, spacing_px=letter_spacing(ov_style, 1.0),
                )
            )
        if l2:
            layers.append(
                _text_sprite(
                    l2, sans_font, 700, 56, (*pal["accent"], 255),
                    shadow_rgba=(255, 255, 255, 150), shadow_dy=0, shadow_blur=4.0,
                    spacing_px=letter_spacing(ov_style, 1.0),
                )
            )
        return _stack_sprites(layers, gap=6)

    # 20. style_13：上白圆角画框 / 下纯黑宽幕 · 下半黑幕只放居中精致白字
    if family == "top_rounded_black_cinema":
        is_giant = elem_kind == "center_burst_title"
        txt = (main_text or "")[:12]
        line = (
            _draw_gradient_3d_text(
                txt, deyi_font, 820, font_size=84,
                top_rgb=pal["text"], mid_rgb=pal["text"], bot_rgb=_light(pal["accent"], 0.62),
                extrude_rgb=_mix(pal["accent"], (196, 192, 182), 0.62), stroke_rgb=pal["stroke"], depth_px=3,
            )
            if is_giant
            else _text_sprite(
                txt, deyi_font, 800, 62, (*pal["text"], 246),
                shadow_rgba=(0, 0, 0, 120), shadow_dy=3, spacing_px=letter_spacing(ov_style, 6.0),
            )
        )
        if line is None:
            return None
        w = max(line.width, 260) + 16
        h = line.height + 40
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        img.alpha_composite(line, dest=((w - line.width) // 2, 6))
        d = ImageDraw.Draw(img)
        rule_w = min(w - 24, max(120, line.width // 2))
        rx = (w - rule_w) // 2
        ry = min(h - 12, 6 + line.height + 12)
        d.line([(rx, ry), (rx + rule_w, ry)], fill=(*pal["accent"], 190), width=2)
        box = img.getbbox()
        return img.crop(box) if box else img

    # 21. style_15：实景访谈切片 · 弹性双色花字流（白 + 亮黄 + 黑底小签，位置散布）
    if family == "interview_flower_flow":
        l1, l2 = _split_two_lines(main_text, max_first=6)
        if not l2 and sub_text and sub_text != main_text:
            l2 = sub_text[:6]
        a = _text_sprite(
            l1 or main_text[:6], serif_font, 520, 76, (*pal["text"], 255),
            stroke_rgba=(0, 0, 0, 255), stroke_w=7, shadow_rgba=(0, 0, 0, 150), shadow_dy=4,
        )
        if a is None:
            return None
        a = a.rotate(-3.2, resample=Image.Resampling.BICUBIC, expand=True)
        pieces: List[Tuple[Image.Image, int, int]] = []
        b = None
        if l2:
            b = _text_sprite(
                l2, serif_font, 520, 68, (*pal["accent"], 255),
                stroke_rgba=(0, 0, 0, 255), stroke_w=7, shadow_rgba=(0, 0, 0, 150), shadow_dy=4,
            )
            if b is not None:
                b = b.rotate(2.4, resample=Image.Resampling.BICUBIC, expand=True)
        tag_txt = (sub_text or "实拍实录")[:6]
        chip_line = _text_sprite(tag_txt, sans_font, 260, 26, (*pal["text"], 250))
        chip = _plate_sprite(chip_line, (0, 0, 0, 225), pad_x=14, pad_y=6) if chip_line is not None else None

        w = max(a.width, b.width if b else 0, chip.width if chip else 0) + 96
        h = a.height + (b.height if b else 0) + (chip.height if chip else 0) + 40
        img = Image.new("RGBA", (max(80, w), max(60, h)), (0, 0, 0, 0))
        y = 4
        if chip is not None:
            img.alpha_composite(chip, dest=(10, y))
            y += chip.height + 4
        img.alpha_composite(a, dest=(34, y))
        y += a.height - 6
        if b is not None:
            img.alpha_composite(b, dest=(76, y))
        box = img.getbbox()
        return img.crop(box) if box else img

    # 22. style_17：暖金强切探店 · 暖金细线勾边 + 方角深色字幕带（#000000B2）
    if family == "warm_gold_highlight":
        from style_dna_profiles import resolve_style17_frame_layout

        layout = resolve_style17_frame_layout(
            main_text, str(elem.get("subtext") or ""), elem_kind, elem.get("compare_mode"),
        )
        comparing = layout["layout_mode"] == "dual_gold_hairline_grid"
        l1, l2 = layout["main_text"], layout["sub_text"]
        if not l2 and (comparing or "\n" in l1):
            l1, l2 = _split_two_lines(l1, max_first=8)
        render_font = find_commercial_font([*(font_candidates or ()), *layout["font_candidates"]]) or serif_font
        w = layout["width_px"]
        main_fs = layout["giant_font_size_px"] if elem_kind == "center_burst_title" else layout["font_size_px"]
        rows = [
            _text_sprite(
                " ".join(line.splitlines()), render_font, w - 64, size, hex_to_rgba(color),
                stroke_w=_sw(layout["stroke_px"]), spacing_px=letter_spacing(ov_style, 2.0),
            )
            for line, size, color in (
                (l1, main_fs, layout["main_hex"]),
                (l2, layout["font_size_px"], layout["sub_hex"]),
            )
        ]
        if comparing:
            row_h = max((row.height for row in rows if row is not None), default=0) + 28
            plate = Image.new("RGBA", (w, 2 * row_h), layout["bg_rgba"])
            d = ImageDraw.Draw(plate)
            line = hex_to_rgba(layout["line_hex"])
            line_w = layout["line_width_px"]
            d.rectangle((0, 0, w - 1, plate.height - 1), outline=line, width=line_w)
            d.rectangle((0, row_h, w - 1, row_h + line_w - 1), fill=line)
            for i, row in enumerate(rows):
                if row is not None:
                    plate.alpha_composite(row, dest=((w - row.width) // 2, i * row_h + (row_h - row.height) // 2))
        else:
            ink = _stack_sprites(rows, gap=8, pad=0, max_w=w - 44)
            if ink is None:
                return None
            plate = _plate_sprite(ink, layout["bg_rgba"], pad_x=22, pad_y=10, radius=layout["radius_px"])
        margin = 10
        img = Image.new("RGBA", (plate.width + 2 * margin, plate.height + 2 * margin), (0, 0, 0, 0))
        img.alpha_composite(plate, dest=(margin, margin))
        return img

    # 23. style_21：门店工艺快切 · 门店标签式小签 + 暖黄大字（底部安全区内）
    if family == "high_broll_store_tag":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        tag_txt = (sub_text or l2 or "门店实拍")[:8]
        tag_line = _text_sprite(tag_txt, serif_font, 320, 30, (*pal["text"], 250), spacing_px=letter_spacing(ov_style, 1.5))
        layers = []
        if tag_line is not None:
            tag = _plate_sprite(tag_line, (24, 24, 27, 230), pad_x=34, pad_y=7, radius=6)
            d = ImageDraw.Draw(tag)
            d.rounded_rectangle([(8, 7), (16, tag.height - 8)], radius=3, fill=(*pal["accent"], 255))
            layers.append(tag)
        chosen_font = font_path or serif_font
        try:
            req_fs = int(float(elem.get("font_size_px") or 0) or 0)
        except (TypeError, ValueError):
            req_fs = 0
        is_giant_role = str(elem.get("display_role") or "") == "giant_text"
        if elem_kind == "center_burst_title" or is_giant_role:
            if is_giant_role:
                layers.clear()  # 因果分级大字直接独立砸屏，不强制叠门店小签
            g_fs = max(104, min(520, req_fs)) if req_fs > 0 else 104
            g_max_w = 960 if is_giant_role else 860
            g_depth = max(6, min(14, int(round(g_fs / 28.0))))
            layers.append(
                _draw_gradient_3d_text(
                    l1 or main_text[:8], chosen_font, g_max_w, font_size=g_fs,
                    top_rgb=pal["text"], mid_rgb=pal["light"], bot_rgb=pal["accent"],
                    extrude_rgb=(44, 34, 10), stroke_rgb=(26, 20, 6), depth_px=g_depth,
                    min_hairline_w=3, shadow_alpha=230,
                )
            )
        else:
            if elem_kind == "end_card_cta" and (not sub_text or sub_text == main_text):
                layers.clear()  # 片尾卡无独立副标时不叠冗余默认小签
            e_fs = max(72, min(160, req_fs)) if req_fs > 0 else 88
            layers.append(
                _text_sprite(
                    l1 or main_text[:10], chosen_font, 860, e_fs, (*pal["accent"], 255),
                    stroke_rgba=(*_shade(pal["accent"], 0.90), 210), stroke_w=4,
                    shadow_rgba=(0, 0, 0, 145), shadow_dy=4, spacing_px=letter_spacing(ov_style, 2.0),
                )
            )
        return _stack_sprites(layers, gap=8)

    # 24. style_23：品牌微纪录 · 克制宋体（字号小、无描边、几乎不动）
    if family == "micro_doc_film_serif":
        txt = (main_text or "")[:12]
        line = (
            _draw_gradient_3d_text(
                txt, jy_serif_font, 760, font_size=64,
                top_rgb=pal["text"], mid_rgb=pal["mist"], bot_rgb=pal["accent"],
                extrude_rgb=_mix(pal["accent"], (188, 184, 172), 0.58), stroke_rgb=pal["stroke"], depth_px=2,
            )
            if elem_kind == "center_burst_title"
            else _text_sprite(
                txt, jy_serif_font, 720, 46, (*pal["text"], 238), spacing_px=letter_spacing(ov_style, 5.0),
            )
        )
        if line is None:
            return None
        w = max(line.width, 220) + 16
        h = line.height + 34
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        rule_w = min(w - 24, max(110, line.width // 3))
        rx = (w - rule_w) // 2
        d.line([(rx, 4), (rx + rule_w, 4)], fill=(*pal["accent"], 150), width=2)
        img.alpha_composite(line, dest=((w - line.width) // 2, 16))
        box = img.getbbox()
        return img.crop(box) if box else img

    # 25. style_24：不露脸混剪 · 炸裂金句（高燃大字、斜切、黑粗描边、14 字上限）
    if family == "faceless_kinetic_montage":
        l1, l2 = _split_two_lines(main_text[:14], max_first=7)
        layers = []
        if elem_kind == "center_burst_title":
            layers.append(
                _draw_gradient_3d_text(
                    l1 or main_text[:7], impact_font, 880, font_size=124,
                    top_rgb=pal["text"], mid_rgb=_light(pal["accent"], 0.36), bot_rgb=pal["accent"],
                    extrude_rgb=_shade(pal["accent"], 0.80), stroke_rgb=(0, 0, 0), depth_px=9,
                )
            )
        else:
            layers.append(
                _text_sprite(
                    l1 or main_text[:7], impact_font, 860, 110, (*pal["text"], 255),
                    stroke_rgba=(0, 0, 0, 255), stroke_w=9,
                    shadow_rgba=(0, 0, 0, 165), shadow_dy=6,
                )
            )
        if l2:
            layers.append(
                _text_sprite(
                    l2, impact_font, 820, 96, (*pal["accent"], 255),
                    stroke_rgba=(0, 0, 0, 255), stroke_w=8,
                    shadow_rgba=(0, 0, 0, 160), shadow_dy=5,
                )
            )
        stack = _stack_sprites(layers, gap=2)
        if stack is None:
            return None
        return stack.rotate(-3.0, resample=Image.Resampling.BICUBIC, expand=True)

    # 26. 其余风格（逐层兜底斜体描边字）
    acc = ov_style.get("accent_colors") or ["#FFFFFF", "#FFE14D"]
    fg_c = hex_to_rgba(acc[1] if len(acc) > 1 else acc[0])
    st_c = hex_to_rgba(ov_style.get("stroke_color") or "#0B0B0C")
    sw = max(3, int(ov_style.get("stroke_width") or 5))
    fs = 86 if elem_kind == "center_burst_title" else 62
    return _draw_tilted_stroked_text(main_text[:10], sans_font, 740, fs, fg_c, st_c, stroke_w=sw, tilt_deg=0.0)


def build_polaroid_bottom_caption_png(
    cache_dir: Path,
    bottom_tag: str = "Daily Vlog",
    bottom_quote: str = "Every ordinary moment carries quiet strength.",
) -> Path:
    """为 style_06 黑底拍立得生成底部 Daily Vlog + 英文格言透明贴图。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_p = cache_dir / "_style06_polaroid_footer.png"
    if out_p.is_file():
        return out_p
    w, h = 760, 150
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f_tag = get_cached_font(find_commercial_font(["SourceHanSerifCN-Bold.otf"]), 40)
    f_sub = get_cached_font(find_commercial_font(["SourceHanSerifCN-Bold.otf"]), 24)
    d.text((w // 2, 40), bottom_tag, font=f_tag, fill=(255, 255, 255, 245), anchor="mm")
    d.text((w // 2, 98), bottom_quote, font=f_sub, fill=(212, 212, 216, 220), anchor="mm")
    img.save(out_p)
    return out_p


def build_style09_case_caption_png(
    cache_dir: Path,
    caption: str = "核心现场实录 · 重点拆解",
) -> Path:
    """为 style_09 纯黑纪实双格金线面板生成顶部画框下方居中案情注记小字。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_p = cache_dir / "_style09_case_caption.png"
    if out_p.is_file():
        return out_p
    w, h = 760, 56
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fnt = get_cached_font(find_commercial_font(["SourceHanSerifCN-Bold.otf", "AlimamaShuHeiTi-Bold.otf"]), 28)
    d.text((w // 2, h // 2), caption, font=fnt, fill=(214, 198, 165, 232), anchor="mm")
    img.save(out_p)
    return out_p


def _ease_out_back(u: float) -> float:
    """弹性过冲缓动曲线（砸屏爆点专用）。"""
    u = max(0.0, min(1.0, u))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * ((u - 1.0) ** 3) + c1 * ((u - 1.0) ** 2)


def _scale_layer_centered(layer: Image.Image, scale: float, alpha_mul: float = 1.0) -> Image.Image:
    nw = max(2, int(round(layer.width * scale)))
    nh = max(2, int(round(layer.height * scale)))
    im = layer.resize((nw, nh), Image.Resampling.BILINEAR)
    if alpha_mul < 0.995:
        r, g, b, a = im.split()
        a = a.point(lambda v: int(v * max(0.0, min(1.0, alpha_mul))))
        im = Image.merge("RGBA", (r, g, b, a))
    return im


def _apply_diagonal_shimmer(base_rgba: Image.Image, progress: float) -> Image.Image:
    """为 3D 金属字施加 45° 斜向高光扫光（严格限定在原文字不透明区域内）。"""
    arr = np.array(base_rgba, dtype=np.float32)
    h, w, _ = arr.shape
    alpha_mask = arr[:, :, 3] > 140.0
    if not np.any(alpha_mask):
        return base_rgba
    yy, xx = np.mgrid[0:h, 0:w]
    diag = (xx + yy * 0.55) / float(max(1, w + h * 0.55))
    center = -0.25 + progress * 1.50
    band = np.exp(-((diag - center) ** 2) / (2.0 * (0.065 ** 2)))
    boost = (band * 155.0) * alpha_mask
    arr[:, :, 0] = np.clip(arr[:, :, 0] + boost, 0, 255)
    arr[:, :, 1] = np.clip(arr[:, :, 1] + boost * 0.96, 0, 255)
    arr[:, :, 2] = np.clip(arr[:, :, 2] + boost * 0.72, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), mode="RGBA")


def spring_progress(progress: float) -> float:
    """Unit step response of a damped spring, with a small overshoot."""
    u = max(0.0, min(1.0, progress))
    damping, frequency = 6.0, 12.0
    residual = math.exp(-damping * u) * (math.cos(frequency * u) + damping / frequency * math.sin(frequency * u))
    return 1.0 - residual * (1.0 - u ** 4)


def _smooth_step(progress: float) -> float:
    u = max(0.0, min(1.0, progress))
    return u * u * (3.0 - 2.0 * u)


def _spring_frames(elem: Dict[str, Any], fps: int, n_frames: int) -> List[Tuple[float, float, float, float]]:
    duration = n_frames / fps
    enter_sec = min(duration * 0.48, max(0.23, float(elem.get("anim_duration_ms") or 380) / 1000))
    out_sec = min(0.23, duration * 0.28)
    direction = elem.get("direction")
    dx = 38 if direction == "left" else (-38 if direction == "right" else 0)
    dy = -17 if direction == "down" else 17
    dim_after = elem.get("dim_after_sec")
    dim_alpha = float(elem.get("dim_alpha") if elem.get("dim_alpha") is not None else 1.0)
    frames = []
    for frame in range(n_frames):
        at = frame / fps
        settle = spring_progress(at / enter_sec)
        alpha = 0.34 + 0.66 * _smooth_step(at / (0.55 * enter_sec))
        if dim_after is not None:
            alpha *= 1.0 + (dim_alpha - 1.0) * _smooth_step((at - float(dim_after)) / 0.18)
        if elem.get("out_anim"):
            alpha *= 1.0 - _smooth_step((at - (duration - 1 / fps - out_sec)) / out_sec)
        frames.append((dx * (1.0 - settle), dy * (1.0 - settle), 0.94 + 0.06 * settle, alpha))
    return frames


def _encode_overlay_mov(ffmpeg_bin: str, out_mov: Path, cw: int, ch: int, fps: int, n_frames: int, frame_fn: Any) -> bool:
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "rgba", "-s", f"{cw}x{ch}", "-r", str(fps),
        "-i", "pipe:0",
        "-an", "-c:v", "qtrle", "-pix_fmt", "argb",
        str(out_mov),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with proc.stdin as stream:
            for fi in range(n_frames):
                stream.write(frame_fn(fi))
    except BrokenPipeError:
        pass
    finally:
        proc.wait()
    return proc.returncode == 0 and out_mov.is_file()


def _capture_spring_alpha(
    renderer: Dict[str, Any], ffmpeg_bin: str, html: str, temporary: Path,
    out_mov: Path, cw: int, ch: int, fps: int, n_frames: int,
) -> bool:
    (temporary / "index.html").write_text(html, encoding="utf-8")
    env = {**os.environ, "FFMPEG_BIN": ffmpeg_bin, "SVS_CHROME_PATH": renderer["chrome_bin"],
           "SVS_PLAYWRIGHT_DIR": str(Path(renderer["playwright_core"]).parent.parent)}
    try:
        result = subprocess.run(
            [renderer["node_bin"], renderer["bridge_script"], str(temporary), "--alpha",
             "--out", str(out_mov), "--size", f"{cw}x{ch}", "--fps", str(fps), "--frames", str(n_frames)],
            env=env, capture_output=True, timeout=min(45, max(10, 5 + n_frames / 7)),
        )
        return result.returncode == 0 and out_mov.is_file() and out_mov.stat().st_size > 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _render_spring_overlay(
    elem: Dict[str, Any], base: Image.Image, out_mov: Path,
    ffmpeg_bin: str, fps: int, n_frames: int,
) -> Optional[Tuple[Path, int, int]]:
    base = base.convert("RGBA")
    pad_x = max(4, min(50, (CANVAS_WIDTH - base.width) // 2))
    cw = (base.width + 2 * pad_x + 1) & ~1
    ch = (base.height + 57) & ~1
    transforms = _spring_frames(elem, fps, n_frames)
    renderer = detect_browser_renderer()
    with tempfile.TemporaryDirectory(prefix="spring-", dir=out_mov.parent) as directory:
        temporary = Path(directory).resolve()
        rendered = temporary / "overlay.mov"
        if renderer.get("available"):
            image_buffer = io.BytesIO()
            base.save(image_buffer, format="PNG")
            encoded = base64.b64encode(image_buffer.getvalue()).decode("ascii")
            # Use the existing glyph fallback to shape the local sprite, then transform it in the SVG.
            html = f"""<!doctype html><meta charset="utf-8">
<style>html,body{{margin:0;background:transparent;overflow:hidden}}</style>
<svg width="{cw}" height="{ch}" xmlns="http://www.w3.org/2000/svg">
<image id="sprite" x="{-base.width / 2}" y="{-base.height / 2}"
 width="{base.width}" height="{base.height}" href="data:image/png;base64,{encoded}"/>
</svg>
<script>
const sprite = document.getElementById("sprite");
const frames = {json.dumps(transforms, separators=(",", ":"))};
window.seek = function(t) {{
  const [x, y, scale, alpha] = frames[Math.min(frames.length - 1, Math.max(0, Math.round(t * {fps})))];
  sprite.setAttribute("transform", "translate(" + ({cw / 2} + x) + " " + ({ch / 2} + y) + ") scale(" + scale + ")");
  sprite.setAttribute("opacity", alpha);
}};
window.READY = true;
</script>"""
            key = hashlib.sha256(html.encode("utf-8")).hexdigest()
            cache = out_mov.parent / f".web-spring-{key}.mov"
            if cache.is_file():
                shutil.copyfile(cache, rendered)
                rendered.replace(out_mov)
                return out_mov, cw, ch
            if _capture_spring_alpha(renderer, ffmpeg_bin, html, temporary, rendered, cw, ch, fps, n_frames):
                rendered.replace(cache)
                shutil.copyfile(cache, rendered)
                rendered.replace(out_mov)
                return out_mov, cw, ch

        def pillow_frame(frame: int) -> bytes:
            dx, dy, scale, alpha = transforms[frame]
            sprite = _scale_layer_centered(base, scale, alpha_mul=alpha)
            composed = Image.new("RGBA", (cw, ch))
            composed.alpha_composite(sprite, (round((cw - sprite.width) / 2 + dx), round((ch - sprite.height) / 2 + dy)))
            return composed.tobytes()

        if _encode_overlay_mov(ffmpeg_bin, rendered, cw, ch, fps, n_frames, pillow_frame):
            rendered.replace(out_mov)
            return out_mov, cw, ch
    return None


ELEMENT_CHOREO_MODES = frozenset({"strike_slash", "stagger_card", "number_roll", "ticker_fade", "semantic_diagram_card"})
_ROLL_STEPS = (0.15, 0.45, 0.72, 0.91, 1.0)
_ELEMENT_OUT_MAX_SEC = 0.22
_ELEMENT_OUT_RATIO = 0.25


def _element_out_span(elem: Dict[str, Any], duration: float) -> Optional[Tuple[float, float]]:
    """元素级动效的尾帧淡出窗口：只有显式声明 out_anim 的元素才淡出。"""
    if not str(elem.get("out_anim") or "").strip():
        return None
    out_sec = min(_ELEMENT_OUT_MAX_SEC, duration * _ELEMENT_OUT_RATIO)
    if out_sec <= 0.0:
        return None
    return (max(0.0, duration - out_sec), duration)


def _element_beats(choreo: str, duration: float, elem: Optional[Dict[str, Any]] = None) -> Dict[str, Tuple[float, float]]:
    """元素级动效的分拍时间表：优先读取 03_audio_events 绑定的逐字 beat_offsets，未传则退回固定默认节拍。"""
    bo = (elem or {}).get("beat_offsets")
    has_bo = isinstance(bo, dict) and bool(bo)
    if choreo == "strike_slash":
        if has_bo and "slash_start" in bo:
            ss = max(0.06, min(max(0.10, duration - 0.28), float(bo["slash_start"])))
            return {"card": (0.0, min(ss, 0.16)), "mark_a": (ss, min(duration - 0.14, ss + 0.09)),
                    "mark_b": (min(duration - 0.12, ss + 0.07), min(duration - 0.06, ss + 0.16)),
                    "slash": (ss, min(duration - 0.06, ss + 0.18))}
        return {"card": (0.0, 0.16), "mark_a": (0.16, 0.25), "mark_b": (0.25, 0.34),
                "slash": (0.16, 0.34)}
    if choreo == "stagger_card":
        if has_bo and ("left" in bo or "right" in bo):
            tc = max(0.0, min(max(0.08, duration - 0.60), float(bo.get("card", 0.0))))
            lc = max(tc + 0.10, min(max(0.20, duration - 0.45), float(bo.get("left", 0.16))))
            rc = max(lc + 0.14, min(max(0.35, duration - 0.28), float(bo.get("right", lc + 0.26))))
            return {
                "card": (tc, min(lc, tc + 0.18)),
                "left": (lc, min(duration - 0.35, lc + 0.26)),
                "divider": (max(lc, rc - 0.06), min(duration - 0.24, rc + 0.06)),
                "right": (rc, min(duration - 0.16, rc + 0.20)),
            }
        return {"card": (0.0, 0.16), "left": (0.16, 0.42), "divider": (0.42, 0.52),
                "right": (0.42, 0.58)}
    if choreo == "number_roll":
        if has_bo and "roll_start" in bo:
            rs = max(0.0, min(max(0.10, duration - 0.32), float(bo["roll_start"])))
            re_t = max(rs + 0.16, min(max(0.26, duration - 0.12), float(bo.get("roll_end", rs + 0.26))))
            return {"roll": (rs, re_t), "settle": (re_t, min(duration - 0.02, re_t + 0.10))}
        return {"roll": (0.0, 0.26), "settle": (0.26, 0.36)}
    if choreo == "semantic_diagram_card":
        beats: Dict[str, Tuple[float, float]] = {"card": (0.0, min(0.18, max(0.08, duration * 0.12)))}
        nodes_off = list(bo.get("nodes") or []) if has_bo else []
        outs_off = list(bo.get("outs") or []) if has_bo else []
        for idx, st_off in enumerate(nodes_off):
            s0 = max(0.02, min(max(0.12, duration - 0.45), float(st_off)))
            beats[f"node_{idx}"] = (s0, min(duration - 0.30, s0 + 0.22))
        for idx, st_off in enumerate(outs_off):
            s0 = max(0.04, min(max(0.14, duration - 0.26), float(st_off)))
            beats[f"out_{idx}"] = (s0, min(duration - 0.14, s0 + 0.22))
        div_t = beats["out_0"][0] - 0.06 if "out_0" in beats else (beats["card"][1])
        beats["hdiv"] = (max(0.05, div_t), min(duration - 0.20, max(0.05, div_t) + 0.16))
        return beats
    return {"in": (0.0, 0.28), "out": (max(0.28, duration - 0.30), duration)}


def _beat_progress(t: float, span: Tuple[float, float]) -> float:
    start, end = span
    if end <= start:
        return 1.0 if t >= end else 0.0
    return max(0.0, min(1.0, (t - start) / (end - start)))


def _element_layer(
    name: str, img: Image.Image, pos: Tuple[int, int],
) -> Dict[str, Any]:
    return {"name": name, "img": img, "pos": (int(pos[0]), int(pos[1]))}


def _layout_canvas(width: int, height: int, pad: int = 6) -> Tuple[int, int, int, int]:
    cw = (width + pad * 2 + 1) & ~1
    ch = (height + pad * 2 + 1) & ~1
    return cw, ch, (cw - width) // 2, (ch - height) // 2


def _capsule_image(
    width: int, height: int, paint: Dict[str, Any], outline_alpha: int = 48,
) -> Image.Image:
    capsule = Image.new("RGBA", (max(2, width), max(2, height)), (0, 0, 0, 0))
    ImageDraw.Draw(capsule).rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=paint["radius_px"],
        fill=(*_hex_rgb(paint["bg_hex"]), round(255 * paint["bg_alpha"])),
        outline=(255, 255, 255, outline_alpha), width=paint["outline_px"],
    )
    return capsule


def _strike_slash_layout(
    elem: Dict[str, Any], font: Optional[str], paint: Dict[str, Any], duration: float,
) -> Optional[Dict[str, Any]]:
    label = str(elem.get("text_or_asset") or "").strip()
    words = label[1:].strip() if label[:1] in {"✕", "✓"} else label
    size = min(max(30, int(elem.get("font_size_px") or 69)), 54)
    mark = max(16, round(size * 0.55))
    gap = mark // 2
    word_sprite = _draw_kinetic_line_sprite(words, font, size, (*_hex_rgb(paint["fg_hex"]), 255),
                                            max_w=600 - mark - gap, min_fs=20)
    if word_sprite is None:
        return None
    pad_x, pad_y = 16, 8
    width = pad_x * 2 + mark + gap + word_sprite.width
    height = pad_y * 2 + max(mark, word_sprite.height)
    body = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    body.alpha_composite(_capsule_image(width, height, paint))
    body.alpha_composite(word_sprite, dest=(pad_x + mark + gap,
                                            (height - word_sprite.height) // 2))
    center_y = height // 2
    pen = max(3, round(mark * 0.17))
    slash_pen = max(4, round(size * 0.085))
    marker = _hex_rgb(paint["marker_hex"])
    left = pad_x + 2
    right = pad_x + mark - 2
    cw, ch, ox, oy = _layout_canvas(width, height)
    word_x0 = pad_x + mark + gap
    return {
        "choreo": "strike_slash",
        "canvas": (cw, ch),
        "layers": [_element_layer("card", body, (ox, oy))],
        "vectors": [
            {"kind": "line", "key": "mark_a", "p1": (ox + left, oy + center_y - mark // 3),
             "p2": (ox + right, oy + center_y + mark // 3), "width": pen, "rgba": (*marker, 255)},
            {"kind": "line", "key": "mark_b", "p1": (ox + left, oy + center_y + mark // 3),
             "p2": (ox + right, oy + center_y - mark // 3), "width": pen, "rgba": (*marker, 255)},
            {"kind": "line", "key": "slash", "p1": (ox + word_x0, oy + center_y),
             "p2": (ox + word_x0 + word_sprite.width, oy + center_y),
             "width": slash_pen, "rgba": (*marker, 235)},
        ],
        "beats": _element_beats("strike_slash", duration, elem),
        "out": _element_out_span(elem, duration),
    }


def _stagger_card_layout(
    elem: Dict[str, Any], font: Optional[str], paint: Dict[str, Any], duration: float,
) -> Optional[Dict[str, Any]]:
    card_lines = [str(line).strip() for line in elem.get("card_lines") or [] if str(line).strip()]
    if not card_lines:
        return None
    font_px = max(30, int(elem.get("font_size_px") or 72))
    heading = bool(elem.get("card_has_headline"))
    accent, fg = _hex_rgb(paint["accent_hex"]), _hex_rgb(paint["fg_hex"])
    if heading:
        title_label, details = card_lines[0], card_lines[1:]
        title = _kinetic_marked_row(title_label, font, min(font_px, 49), accent, 398,
                                    _hex_rgb(paint["marker_hex"]), accent)
    else:
        title, details = None, card_lines
    if len(details) != 2:
        return None
    columns = [
        _kinetic_marked_row(label, font, min(font_px, 36), fg, 182,
                            _hex_rgb(paint["marker_hex"]), accent)
        for label in details
    ]
    if any(sprite is None for sprite in columns) or (heading and title is None):
        return None
    left, right = columns
    pad_x, pad_y, gap_y, gap_detail = 15, 10, 10, 22
    body_w = left.width + right.width + gap_detail
    body_h = max(left.height, right.height)
    title_h = title.height if title is not None else 0
    width = max(body_w, title.width if title is not None else 0) + pad_x * 2
    height = title_h + (gap_y if title_h else 0) + body_h + pad_y * 2
    divider = ((width - body_w) // 2 + left.width + gap_detail // 2,
               pad_y + title_h + (gap_y if title_h else 0))
    cw, ch, ox, oy = _layout_canvas(width, height)
    layers = [_element_layer("card", _capsule_image(width, height, paint), (ox, oy))]
    if title is not None:
        layers.append(_element_layer("title", title, (ox + (width - title.width) // 2, oy + pad_y)))
    layers.append(_element_layer("left", left, (ox + (width - body_w) // 2,
                                                oy + divider[1] + (body_h - left.height) // 2)))
    layers.append(_element_layer("right", right, (ox + (width - body_w) // 2 + left.width + gap_detail,
                                                  oy + divider[1] + (body_h - right.height) // 2)))
    return {
        "choreo": "stagger_card",
        "canvas": (cw, ch),
        "layers": layers,
        "vectors": [{"kind": "vline", "key": "divider", "x": ox + divider[0], "y0": oy + divider[1],
                     "height": body_h, "width": 1, "rgba": (255, 255, 255, 96)}],
        "beats": _element_beats("stagger_card", duration, elem),
        "out": _element_out_span(elem, duration),
    }


def _number_roll_layout(
    elem: Dict[str, Any], font: Optional[str], paint: Dict[str, Any], duration: float,
) -> Optional[Dict[str, Any]]:
    text = str(elem.get("text_or_asset") or "").strip()
    match = re.search(r"(\d{2,})", text)
    if not match:
        return None
    prefix, digits, suffix = text[:match.start()], match.group(1), text[match.end():]
    value = int(digits)
    size = min(max(30, int(elem.get("font_size_px") or 100)), 112)
    color = _hex_rgb(paint["accent_hex"] if elem.get("variant") == "hero_lime" else paint["fg_hex"])
    sprites, shown_values = [], []
    for step in _ROLL_STEPS:
        shown = int(round(value * step))
        shown_values.append(shown)
        sprite = _draw_kinetic_line_sprite(f"{prefix}{shown}{suffix}", font, size, (*color, 255),
                                           max_w=680, min_fs=20)
        if sprite is None:
            return None
        sprites.append(sprite)
    width = max(sprite.width for sprite in sprites)
    height = max(sprite.height for sprite in sprites)
    cw, ch, ox, oy = _layout_canvas(width, height, pad=8)
    layers = [
        _element_layer(f"roll_{idx}", sprite,
                       (ox + (width - sprite.width) // 2, oy + height - sprite.height))
        for idx, sprite in enumerate(sprites)
    ]
    return {
        "choreo": "number_roll",
        "canvas": (cw, ch),
        "layers": layers,
        "vectors": [],
        "roll_values": shown_values,
        "beats": _element_beats("number_roll", duration, elem),
        "out": _element_out_span(elem, duration),
    }


def _ticker_layout(elem: Dict[str, Any], font: Optional[str], paint: Dict[str, Any],
                   duration: float) -> Optional[Dict[str, Any]]:
    text = str(elem.get("text_or_asset") or "").strip()
    sprite = _draw_kinetic_line_sprite(text, font, min(max(20, int(elem.get("font_size_px") or 31)), 30),
                                       (*_hex_rgb(paint["fg_hex"]), 255), max_w=300, min_fs=18)
    if sprite is None:
        return None
    pad_x, pad_y = 12, 6
    width, height = sprite.width + pad_x * 2, sprite.height + pad_y * 2
    body = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    body.alpha_composite(_capsule_image(width, height, paint, outline_alpha=78))
    body.alpha_composite(sprite, dest=(pad_x, pad_y))
    cw, ch, ox, oy = _layout_canvas(width, height, pad=4)
    return {
        "choreo": "ticker_fade",
        "canvas": (cw, ch),
        "layers": [_element_layer("ticker", body, (ox, oy))],
        "vectors": [],
        "beats": _element_beats("ticker_fade", max(0.4, duration), elem),
        "duration": max(0.4, duration),
        "out": _element_out_span(elem, max(0.4, duration)),
    }


def _semantic_diagram_layout(
    elem: Dict[str, Any], font: Optional[str], paint: Dict[str, Any], duration: float,
) -> Optional[Dict[str, Any]]:
    """黑金杂志风极简动态语义图卡：支持「三元节点 + 对比结果」及「对称金句双条」。"""
    nodes = [str(x).strip() for x in (elem.get("diagram_nodes") or []) if str(x).strip()]
    rows = [str(x).strip() for x in (elem.get("diagram_conclusion") or []) if str(x).strip()]
    if not nodes and not rows:
        card_lines = [str(x).strip() for x in (elem.get("card_lines") or []) if str(x).strip()]
        if not card_lines:
            raw_t = str(elem.get("text_or_asset") or "").strip()
            if not raw_t:
                return None
            card_lines = [raw_t]
        if "·" in card_lines[0]:
            nodes = [n.strip() for n in card_lines[0].split("·") if n.strip()]
            rows = card_lines[1:]
        else:
            rows = card_lines

    accent = _hex_rgb(paint["accent_hex"])
    fg = _hex_rgb(paint["fg_hex"])
    marker = _hex_rgb(paint["marker_hex"])
    node_imgs: List[Image.Image] = []
    for nd in nodes:
        sp = _draw_kinetic_line_sprite(nd, font, 32, (*accent, 255), max_w=170, min_fs=20)
        if sp is None:
            continue
        pw, ph = sp.width + 26, sp.height + 14
        pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        ImageDraw.Draw(pill).rounded_rectangle(
            (0, 0, pw - 1, ph - 1), radius=10,
            fill=(24, 29, 40, 235), outline=(*accent, 200), width=1,
        )
        pill.alpha_composite(sp, dest=(13, 7))
        node_imgs.append(pill)

    out_imgs: List[Image.Image] = []
    for rw in rows:
        sp = _kinetic_marked_row(rw, font, 35 if node_imgs else 38, fg, 260 if node_imgs else 420, marker, accent)
        if sp is None:
            continue
        if not node_imgs:
            # 对仗金句条目左侧配 4px 黑金杂志竖线
            bw = sp.width + 18
            bh = max(sp.height + 6, 36)
            bar_im = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            ImageDraw.Draw(bar_im).rounded_rectangle((0, 4, 5, bh - 5), radius=2, fill=(*accent, 245))
            bar_im.alpha_composite(sp, dest=(14, (bh - sp.height) // 2))
            out_imgs.append(bar_im)
        else:
            out_imgs.append(sp)

    if not node_imgs and not out_imgs:
        return None

    pad_x, pad_y = 22, 14
    node_gap, out_gap, sec_gap = 18, 24, 14
    top_w = sum(im.width for im in node_imgs) + node_gap * max(0, len(node_imgs) - 1)
    top_h = max((im.height for im in node_imgs), default=0)
    if node_imgs and len(out_imgs) == 2:
        bot_w = out_imgs[0].width + out_imgs[1].width + out_gap
        bot_h = max(out_imgs[0].height, out_imgs[1].height)
        bot_side_by_side = True
    else:
        bot_w = max((im.width for im in out_imgs), default=0)
        bot_h = sum(im.height for im in out_imgs) + 10 * max(0, len(out_imgs) - 1)
        bot_side_by_side = False

    content_w = max(top_w, bot_w, 220)
    width = content_w + pad_x * 2
    div_space = sec_gap if (node_imgs and out_imgs) else 0
    height = pad_y * 2 + top_h + div_space + bot_h

    card_bg = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d_bg = ImageDraw.Draw(card_bg)
    d_bg.rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=14,
        fill=(12, 15, 22, 222), outline=(*accent, 140), width=1,
    )
    d_bg.rounded_rectangle((18, 0, width - 19, 3), radius=1, fill=(*accent, 235))

    cw, ch, ox, oy = _layout_canvas(width, height, pad=6)
    layers = [_element_layer("card", card_bg, (ox, oy))]
    cur_y = pad_y
    if node_imgs:
        cur_x = (width - top_w) // 2
        for idx, nim in enumerate(node_imgs):
            layers.append(_element_layer(f"node_{idx}", nim, (ox + cur_x, oy + cur_y + (top_h - nim.height) // 2)))
            cur_x += nim.width + node_gap
        cur_y += top_h + sec_gap // 2

    vectors: List[Dict[str, Any]] = []
    if node_imgs and out_imgs:
        vectors.append({
            "kind": "line", "key": "hdiv",
            "p1": (ox + pad_x, oy + cur_y), "p2": (ox + width - pad_x, oy + cur_y),
            "width": 1, "rgba": (*accent, 145),
        })
        cur_y += sec_gap - sec_gap // 2

    if out_imgs:
        if bot_side_by_side:
            cx_b = (width - bot_w) // 2
            layers.append(_element_layer("out_0", out_imgs[0], (ox + cx_b, oy + cur_y + (bot_h - out_imgs[0].height) // 2)))
            layers.append(_element_layer("out_1", out_imgs[1], (ox + cx_b + out_imgs[0].width + out_gap, oy + cur_y + (bot_h - out_imgs[1].height) // 2)))
        else:
            for idx, oim in enumerate(out_imgs):
                layers.append(_element_layer(f"out_{idx}", oim, (ox + (width - oim.width) // 2, oy + cur_y)))
                cur_y += oim.height + 10

    elem_copy = dict(elem)
    bo = dict(elem_copy.get("beat_offsets") or {}) if isinstance(elem_copy.get("beat_offsets"), dict) else {}
    if not bo.get("nodes") and node_imgs:
        bo["nodes"] = [round(0.08 + i * 0.18, 3) for i in range(len(node_imgs))]
    if not bo.get("outs") and out_imgs:
        base_o = (bo["nodes"][-1] + 0.22) if bo.get("nodes") else 0.08
        bo["outs"] = [round(base_o + i * 0.20, 3) for i in range(len(out_imgs))]
    elem_copy["beat_offsets"] = bo

    return {
        "choreo": "semantic_diagram_card",
        "canvas": (cw, ch),
        "layers": layers,
        "vectors": vectors,
        "beats": _element_beats("semantic_diagram_card", duration, elem_copy),
        "out": _element_out_span(elem_copy, duration),
    }


def _element_layout(
    elem: Dict[str, Any], choreo: str, font_candidates: Optional[List[str]], duration: float,
) -> Optional[Dict[str, Any]]:
    paint = kinetic_item_paint(elem)
    if paint is None:
        return None
    font = find_commercial_font(
        font_candidates or ["SourceHanSansCN-Bold.otf", "AlimamaShuHeiTi-Bold.otf"],
    ) or find_commercial_font()
    builders = {
        "strike_slash": lambda: _strike_slash_layout(elem, font, paint, duration),
        "stagger_card": lambda: _stagger_card_layout(elem, font, paint, duration),
        "number_roll": lambda: _number_roll_layout(elem, font, paint, duration),
        "ticker_fade": lambda: _ticker_layout(elem, font, paint, duration),
        "semantic_diagram_card": lambda: _semantic_diagram_layout(elem, font, paint, duration),
    }
    return builders[choreo]()


def _element_frame_state(layout: Dict[str, Any], t: float) -> Dict[str, Any]:
    """某一时刻每个图层的位移/缩放/透明度与矢量绘制进度。"""
    choreo = layout["choreo"]
    beats = layout["beats"]
    names = [layer["name"] for layer in layout["layers"]]
    state: Dict[str, Any] = {name: {"alpha": 1.0, "scale": 1.0, "dx": 0.0, "dy": 0.0} for name in names}
    vectors: Dict[str, float] = {}

    if choreo == "strike_slash":
        card = spring_progress(_beat_progress(t, beats["card"]))
        state["card"].update(alpha=0.34 + 0.66 * _smooth_step(_beat_progress(t, beats["card"])),
                             scale=0.94 + 0.06 * card)
        for key in ("mark_a", "mark_b", "slash"):
            vectors[key] = _smooth_step(_beat_progress(t, beats[key]))
    elif choreo == "stagger_card":
        card = spring_progress(_beat_progress(t, beats["card"]))
        card_alpha = 0.34 + 0.66 * _smooth_step(_beat_progress(t, beats["card"]))
        state["card"].update(alpha=card_alpha, scale=0.94 + 0.06 * card)
        if "title" in state:
            state["title"].update(alpha=card_alpha, scale=0.90 + 0.10 * card)
        u_left = _beat_progress(t, beats["left"])
        state["left"].update(alpha=_smooth_step(min(1.0, u_left * 2.4)), dx=-26.0 * (1.0 - u_left))
        u_right = _beat_progress(t, beats["right"])
        state["right"].update(alpha=_smooth_step(min(1.0, u_right * 2.4)),
                              scale=0.72 + 0.28 * _ease_out_back(u_right))
        vectors["divider"] = _smooth_step(_beat_progress(t, beats["divider"]))
    elif choreo == "number_roll":
        if t < beats["roll"][0]:
            for name in names:
                state[name]["alpha"] = 0.0
        else:
            u_roll = _beat_progress(t, beats["roll"])
            active = min(len(_ROLL_STEPS) - 1, int(u_roll * len(_ROLL_STEPS)))
            for idx, name in enumerate(names):
                state[name]["alpha"] = 1.0 if idx == active else 0.0
            settle = _beat_progress(t, beats["settle"])
            state[names[-1]]["scale"] = 1.0 + 0.05 * (1.0 - settle) if t >= beats["roll"][1] else 1.0
            state[names[-1]]["alpha"] = 1.0 if t >= beats["roll"][1] else state[names[-1]]["alpha"]
    elif choreo == "semantic_diagram_card":
        card_p = spring_progress(_beat_progress(t, beats["card"]))
        state["card"].update(alpha=_smooth_step(_beat_progress(t, beats["card"])),
                             scale=0.95 + 0.05 * card_p)
        for name in names:
            if name == "card":
                continue
            sp_beat = beats.get(name)
            if sp_beat is None:
                continue
            u_l = _beat_progress(t, sp_beat)
            state[name].update(
                alpha=_smooth_step(min(1.0, u_l * 2.2)),
                scale=0.86 + 0.14 * _ease_out_back(u_l) if u_l > 0.0 else 0.86,
                dy=8.0 * (1.0 - _smooth_step(u_l)),
            )
        if "hdiv" in beats:
            vectors["hdiv"] = _smooth_step(_beat_progress(t, beats["hdiv"]))
    else:  # ticker_fade：柔和淡入淡出，无过冲弹跳
        state["ticker"].update(alpha=_smooth_step(_beat_progress(t, beats["in"])))
        if t > beats["out"][0]:
            state["ticker"]["alpha"] *= 1.0 - _smooth_step(_beat_progress(t, beats["out"]))

    # 尾帧统一淡出：图层 alpha 与矢量（红叉/红线/竖分割线）不透明度同步归零。
    vector_alpha = 1.0
    out_span = layout.get("out")
    if out_span:
        vector_alpha = 1.0 - _smooth_step(_beat_progress(t, out_span))
        if vector_alpha < 0.999:
            for name in names:
                state[name]["alpha"] *= vector_alpha
    return {"layers": state, "vectors": vectors, "vector_alpha": vector_alpha}


def _element_frames(layout: Dict[str, Any], fps: int, n_frames: int) -> List[Dict[str, Any]]:
    return [_element_frame_state(layout, frame / float(fps)) for frame in range(n_frames)]


def _draw_vector(draw: Any, vector: Dict[str, Any], progress: float, alpha_mul: float = 1.0) -> None:
    if progress <= 0.001 or alpha_mul <= 0.004:
        return
    rgba = vector["rgba"]
    fill = (*rgba[:3], int(round(rgba[3] * alpha_mul)))
    if vector["kind"] == "line":
        (x0, y0), (x1, y1) = vector["p1"], vector["p2"]
        draw.line(((x0, y0), (x0 + (x1 - x0) * progress, y0 + (y1 - y0) * progress)),
                  fill=fill, width=vector["width"])
    else:
        draw.rectangle((vector["x"], vector["y0"], vector["x"] + vector["width"],
                        vector["y0"] + max(1.0, vector["height"] * progress)), fill=fill)


def _element_frame_bytes(layout: Dict[str, Any], state: Dict[str, Any]) -> bytes:
    cw, ch = layout["canvas"]
    frame = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    for layer in layout["layers"]:
        transform = state["layers"].get(layer["name"]) or {}
        alpha = float(transform.get("alpha", 1.0))
        if alpha <= 0.004:
            continue
        scale = float(transform.get("scale", 1.0))
        img = layer["img"] if abs(scale - 1.0) < 0.002 else _scale_layer_centered(layer["img"], scale, alpha_mul=1.0)
        if alpha < 0.996:
            img = _scale_layer_centered(img, 1.0, alpha_mul=alpha)
        x = int(round(layer["pos"][0] + float(transform.get("dx", 0.0)) + (layer["img"].width - img.width) / 2))
        y = int(round(layer["pos"][1] + float(transform.get("dy", 0.0)) + (layer["img"].height - img.height) / 2))
        box = (max(0, x), max(0, y),
               min(cw, x + img.width), min(ch, y + img.height))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        frame.alpha_composite(img.crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y)),
                              dest=(box[0], box[1]))
    draw = ImageDraw.Draw(frame)
    vector_alpha = float(state.get("vector_alpha", 1.0))
    for vector in layout["vectors"]:
        _draw_vector(draw, vector, float(state["vectors"].get(vector["key"], 0.0)), vector_alpha)
    return frame.tobytes()


def _element_html(layout: Dict[str, Any], frames: List[Dict[str, Any]], fps: int) -> str:
    cw, ch = layout["canvas"]
    parts = [f'<!doctype html><meta charset="utf-8">'
             f'<style>html,body{{margin:0;background:transparent;overflow:hidden}}</style>',
             f'<svg width="{cw}" height="{ch}" xmlns="http://www.w3.org/2000/svg">']
    for idx, layer in enumerate(layout["layers"]):
        buffer = io.BytesIO()
        layer["img"].save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        x, y = layer["pos"]
        parts.append(
            f'<image id="L{idx}" x="{x}" y="{y}" width="{layer["img"].width}" '
            f'height="{layer["img"].height}" href="data:image/png;base64,{encoded}"/>'
        )
    for idx, vector in enumerate(layout["vectors"]):
        rgba = vector["rgba"]
        stroke = f'rgba({rgba[0]},{rgba[1]},{rgba[2]},{rgba[3] / 255.0:.3f})'
        if vector["kind"] == "line":
            parts.append(
                f'<line id="V{idx}" x1="{vector["p1"][0]}" y1="{vector["p1"][1]}" '
                f'x2="{vector["p2"][0]}" y2="{vector["p2"][1]}" stroke="{stroke}" '
                f'stroke-width="{vector["width"]}" stroke-linecap="round"/>'
            )
        else:
            parts.append(
                f'<rect id="V{idx}" x="{vector["x"]}" y="{vector["y0"]}" width="{vector["width"]}" '
                f'height="0" fill="{stroke}"/>'
            )
    parts.append("</svg><script>")
    parts.append(f"const FPS={fps};")
    parts.append(f"const FRAMES={json.dumps(frames, separators=(',', ':'))};")
    parts.append(
        "const META=" + json.dumps(
            [{"name": layer["name"], "x": layer["pos"][0], "y": layer["pos"][1],
              "w": layer["img"].width, "h": layer["img"].height} for layer in layout["layers"]],
            separators=(",", ":"),
        ) + ";"
    )
    parts.append(
        "const VEC=" + json.dumps(
            [{key: vector.get(key) for key in ("kind", "height")} for vector in layout["vectors"]],
            separators=(",", ":"),
        ) + ";"
    )
    parts.append(f"const VKEYS={json.dumps([vector['key'] for vector in layout['vectors']])};")
    parts.append("""
window.seek = function(t) {
  const state = FRAMES[Math.min(FRAMES.length - 1, Math.max(0, Math.round(t * FPS)))];
  META.forEach(function(meta, idx) {
    const el = document.getElementById("L" + idx);
    const tr = state.layers[meta.name] || {};
    const scale = tr.scale === undefined ? 1 : tr.scale;
    const dx = tr.dx === undefined ? 0 : tr.dx;
    const dy = tr.dy === undefined ? 0 : tr.dy;
    const alpha = tr.alpha === undefined ? 1 : tr.alpha;
    const cx = meta.x + meta.w / 2, cy = meta.y + meta.h / 2;
    el.setAttribute("transform", "translate(" + dx + " " + dy + ") translate(" + cx + " " + cy + ") scale(" + scale + ") translate(" + (-cx) + " " + (-cy) + ")");
    el.setAttribute("opacity", alpha);
  });
  VKEYS.forEach(function(key, idx) {
    const el = document.getElementById("V" + idx);
    if (!el) return;
    const progress = (state.vectors[key] === undefined) ? 0 : state.vectors[key];
    const vectorAlpha = (state.vector_alpha === undefined) ? 1 : state.vector_alpha;
    el.style.opacity = progress > 0.001 ? vectorAlpha : 0;
    if (VEC[idx].kind === "line") {
      const len = el.getTotalLength();
      el.setAttribute("stroke-dasharray", len);
      el.setAttribute("stroke-dashoffset", len * (1 - progress));
    } else {
      el.setAttribute("height", VEC[idx].height * progress);
    }
  });
};
window.READY = true;
""")
    parts.append("</script>")
    return "".join(parts)


def _render_element_choreo(
    elem: Dict[str, Any], choreo: str, out_mov: Path, ffmpeg_bin: str, fps: int,
    n_frames: int, duration: float, font_candidates: Optional[List[str]],
) -> Optional[Tuple[Path, int, int]]:
    layout = _element_layout(elem, choreo, font_candidates, duration)
    if layout is None:
        return None
    if layout.get("out"):
        # 尾帧淡出终止在最后一帧的采样时刻，保证 mov 末帧确实全透明。
        start, _ = layout["out"]
        layout["out"] = (start, max(start + 1.0 / fps, (n_frames - 1) / float(fps)))
    cw, ch = layout["canvas"]
    frames = _element_frames(layout, fps, n_frames)
    out_mov = Path(out_mov)
    out_mov.parent.mkdir(parents=True, exist_ok=True)
    renderer = detect_browser_renderer()
    with tempfile.TemporaryDirectory(prefix="choreo-", dir=out_mov.parent) as directory:
        temporary = Path(directory).resolve()
        rendered = temporary / "overlay.mov"
        if renderer.get("available"):
            html = _element_html(layout, frames, fps)
            key = hashlib.sha256(html.encode("utf-8")).hexdigest()
            cache = out_mov.parent / f".web-element-{key}.mov"
            if cache.is_file():
                shutil.copyfile(cache, rendered)
                rendered.replace(out_mov)
                return out_mov, cw, ch
            if _capture_spring_alpha(renderer, ffmpeg_bin, html, temporary, rendered, cw, ch, fps, n_frames):
                shutil.copyfile(rendered, cache)
                rendered.replace(out_mov)
                return out_mov, cw, ch

        def pillow_frame(frame: int) -> bytes:
            return _element_frame_bytes(layout, frames[frame])

        if _encode_overlay_mov(ffmpeg_bin, rendered, cw, ch, fps, n_frames, pillow_frame):
            rendered.replace(out_mov)
            return out_mov, cw, ch
    return None


def render_animated_overlay_mov(
    elem: Dict[str, Any],
    out_mov: Path,
    style_id: str = "style_01",
    duration_sec: float = 2.0,
    fps: int = DEFAULT_FPS,
    font_candidates: Optional[List[str]] = None,
    fallback_static_img: Optional[Image.Image] = None,
) -> Optional[Tuple[Path, int, int]]:
    """
    30fps 动态透明文字动效渲染器（输出带完整 Alpha 通道的无损 qtrle .mov）：
    支持 8 种剪映核心动态文字动效：
    - store_stagger_burst (style_01 3 拍分步砸屏)
    - bounce_smash (弹性过冲砸屏)
    - wipe_reveal (左到右平滑擦除揭幕)
    - typewriter_char (逐字展开显影)
    - shimmer_3d_gold (砸屏 + 3D 金属斜向高光扫光)
    - ink_bloom (水墨由雾渐清轻扩)
    - depth_behind_person (轻缓上浮淡入)
    - number_flip_trio (三格依次纵向翻转弹入)
    返回 (out_mov, canvas_w, canvas_h)。
    """
    ffmpeg_bin = find_bin("ffmpeg")
    if not ffmpeg_bin:
        return None
    bp = get_style_blueprint(style_id)
    ov_style = bp.get("overlay_style") or {}
    anim_mode = str(ov_style.get("anim_mode") or "bounce_smash")
    family = str(bp.get("template_family") or "")

    # 按 overlay_style.anim_primitives（该风格真实动效前 3 名 → 原语名）顺位轮换；
    # elem["anim_slot"] 由调用方递增即可让同风格的花字轮着换动效，
    # elem["anim_primitive"] 可强制指定某一个原语。
    # 三拍砸屏（style_01）与 3D 金属扫光（style_18）是该风格的签名动效，不参与轮换。
    prim_list = [str(p) for p in (ov_style.get("anim_primitives") or []) if str(p or "").strip()]
    raw_prim = str(elem.get("anim_primitive") or "").strip()
    _ek = str(elem.get("kind") or elem.get("type") or elem.get("element_type") or "")
    if raw_prim:
        anim_mode = resolve_anim_mode(raw_prim, anim_mode)
    elif _ek == "kinetic_stage_item" and not str(elem.get("motion_mode") or "").strip():
        # 动态舞台中仅做静置/压暗让位的元素（anim_key=None），绝不套用风格默认弹幕滚动以免横向裁切
        anim_mode = "static"
    elif prim_list and anim_mode not in SIGNATURE_ANIM_MODES:
        slot = int(elem.get("anim_slot") or 0)
        anim_mode = resolve_anim_mode(prim_list[slot % len(prim_list)], anim_mode)

    out_mov = Path(out_mov)
    out_mov.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.35, float(duration_sec))
    n_frames = max(10, int(round(dur * fps)))

    # 元素级物理编舞：同一份分拍与几何同时供 Chrome SVG 与 Pillow 兜底使用。
    choreo = str(elem.get("element_choreo") or "").strip()
    if choreo in ELEMENT_CHOREO_MODES:
        built = _render_element_choreo(
            elem, choreo, out_mov, ffmpeg_bin, fps, n_frames, dur, font_candidates,
        )
        if built is not None:
            return built

    if elem.get("variant") in V6_CARD_VARIANTS or (
        elem.get("render_backend") == "web_spring" and elem.get("variant") != "bridge_bar"
    ):
        base = fallback_static_img
        if base is None:
            base = render_kinetic_stage_item_png(elem, font_candidates=font_candidates)
        if base is not None:
            return _render_spring_overlay(elem, base, out_mov, ffmpeg_bin, fps, n_frames)
        return None

    # 1. style_01 专属三拍分步砸屏（仅开场海报/花字触发，片尾卡/贴纸走各自专用组件）
    if anim_mode == "store_stagger_burst" and family == "store_poster_burst" and _ek not in ("end_card_cta", "indicator_sticker"):
        sans_font = find_commercial_font(["AlimamaShuHeiTi-Bold.otf", "SmileySans-Oblique.ttf"])
        oblique_font = find_commercial_font(["SmileySans-Oblique.ttf", "AlimamaShuHeiTi-Bold.otf"])
        (raw_w, raw_h), layers = _build_style01_layers(elem, sans_font, oblique_font)
        if not layers:
            return None
        # 留出放大过冲安全边距并保证偶数尺寸
        pad = 36
        cw = (raw_w + pad * 2 + 1) & ~1
        ch = (raw_h + pad * 2 + 1) & ~1
        final_frame = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        for img_l, (dx, dy), _delay in layers:
            final_frame.alpha_composite(img_l, dest=(dx + pad, dy + pad))
        final_bytes = final_frame.tobytes()
        max_active_sec = max(d for _, _, d in layers) + 0.34
        active_frames = min(n_frames, int(math.ceil(max_active_sec * fps)) + 1)

        def _render_frame_s01(fi: int) -> bytes:
            if fi >= active_frames:
                return final_bytes
            t = fi / float(fps)
            frame = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            for img_l, (dx, dy), delay in layers:
                if t < delay:
                    continue
                u = min(1.0, (t - delay) / 0.28)
                if u >= 1.0:
                    frame.alpha_composite(img_l, dest=(dx + pad, dy + pad))
                else:
                    # 从 1.52 倍快速过冲弹落至 1.00
                    sc = 1.52 - 0.52 * _ease_out_back(u)
                    sc = max(0.86, min(1.55, sc))
                    al = min(1.0, u / 0.35)
                    scaled = _scale_layer_centered(img_l, sc, alpha_mul=al)
                    cx = dx + pad + img_l.width // 2
                    cy = dy + pad + img_l.height // 2
                    px = cx - scaled.width // 2
                    py = cy - scaled.height // 2
                    # 安全贴合边界
                    ox0 = max(0, px)
                    oy0 = max(0, py)
                    sx0 = max(0, -px)
                    sy0 = max(0, -py)
                    sw = min(scaled.width - sx0, cw - ox0)
                    sh = min(scaled.height - sy0, ch - oy0)
                    if sw > 0 and sh > 0:
                        frame.alpha_composite(scaled.crop((sx0, sy0, sx0 + sw, sy0 + sh)), dest=(ox0, oy0))
            return frame.tobytes()

        frame_fn = _render_frame_s01
    else:
        base_img = render_template_packaging_png(elem, style_id=style_id, font_candidates=font_candidates)
        if base_img is None:
            base_img = fallback_static_img
        if base_img is None:
            return None
        base_img = base_img.convert("RGBA")
        pad = 24 if anim_mode in ("bounce_smash", "shimmer_3d_gold", "depth_behind_person", "ink_bloom") else 8
        pad = max(pad, MOTION_PAD.get(anim_mode, 0))
        # 叠层画布不得超 1080 宽（对称安全区 220-860 居中落位）
        pad = max(4, min(pad, (CANVAS_WIDTH - base_img.width) // 2))
        cw = (base_img.width + pad * 2 + 1) & ~1
        ch = (base_img.height + pad * 2 + 1) & ~1
        ox = (cw - base_img.width) // 2
        oy = (ch - base_img.height) // 2
        settled = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        settled.alpha_composite(base_img, dest=(ox, oy))
        settled_bytes = settled.tobytes()

        # 6 类新增入场原语 + 缩小/放大细分子型（见 overlay_motion）
        motion_dir = str(elem.get("direction") or "").strip() or None
        motion_fn = make_motion_frame_fn(anim_mode, base_img, cw, ch, ox, oy, fps, dur, direction=motion_dir) if anim_mode in MOTION_MODES else None
        if anim_mode == "static":
            frame_fn = lambda _fi: settled_bytes
        elif motion_fn is not None:
            frame_fn = motion_fn

        elif anim_mode == "shimmer_3d_gold":
            def _render_shimmer(fi: int) -> bytes:
                t = fi / float(fps)
                if t < 0.28:
                    u = t / 0.28
                    sc = max(0.88, min(1.45, 1.45 - 0.45 * _ease_out_back(u)))
                    al = min(1.0, u / 0.35)
                    scaled = _scale_layer_centered(base_img, sc, alpha_mul=al)
                    fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                    px = (cw - scaled.width) // 2
                    py = (ch - scaled.height) // 2
                    ox0, oy0 = max(0, px), max(0, py)
                    sx0, sy0 = max(0, -px), max(0, -py)
                    sw = min(scaled.width - sx0, cw - ox0)
                    sh = min(scaled.height - sy0, ch - oy0)
                    if sw > 0 and sh > 0:
                        fr.alpha_composite(scaled.crop((sx0, sy0, sx0 + sw, sy0 + sh)), dest=(ox0, oy0))
                    return fr.tobytes()
                # 每 1.35 秒执行一次 0.55 秒斜向金属扫光
                cyc = (t - 0.28) % 1.35
                if cyc <= 0.55:
                    lit = _apply_diagonal_shimmer(settled, cyc / 0.55)
                    return lit.tobytes()
                return settled_bytes
            frame_fn = _render_shimmer

        elif anim_mode == "bounce_smash":
            act_f = min(n_frames, int(math.ceil(0.36 * fps)) + 1)
            def _render_bounce(fi: int) -> bytes:
                if fi >= act_f:
                    return settled_bytes
                u = min(1.0, (fi / float(fps)) / 0.32)
                sc = max(0.88, min(1.46, 1.46 - 0.46 * _ease_out_back(u)))
                al = min(1.0, u / 0.32)
                scaled = _scale_layer_centered(base_img, sc, alpha_mul=al)
                fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                px = (cw - scaled.width) // 2
                py = (ch - scaled.height) // 2
                ox0, oy0 = max(0, px), max(0, py)
                sx0, sy0 = max(0, -px), max(0, -py)
                sw = min(scaled.width - sx0, cw - ox0)
                sh = min(scaled.height - sy0, ch - oy0)
                if sw > 0 and sh > 0:
                    fr.alpha_composite(scaled.crop((sx0, sy0, sx0 + sw, sy0 + sh)), dest=(ox0, oy0))
                return fr.tobytes()
            frame_fn = _render_bounce

        elif anim_mode in ("wipe_reveal", "typewriter_char"):
            dur_anim = 0.44 if anim_mode == "typewriter_char" else 0.32
            act_f = min(n_frames, int(math.ceil(dur_anim * fps)) + 1)
            def _render_wipe(fi: int) -> bytes:
                if fi >= act_f:
                    return settled_bytes
                u = min(1.0, (fi / float(fps)) / dur_anim)
                ease_u = 1.0 - (1.0 - u) ** 2
                if anim_mode == "typewriter_char":
                    # 阶梯感逐字揭示
                    steps = 8
                    reveal_frac = min(1.0, math.ceil(ease_u * steps) / float(steps))
                else:
                    reveal_frac = ease_u
                vis_w = max(2, int(round(base_img.width * reveal_frac)))
                dx = int(round((1.0 - ease_u) * (-18 if anim_mode == "wipe_reveal" else 0)))
                fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                cropped = base_img.crop((0, 0, vis_w, base_img.height))
                fr.alpha_composite(cropped, dest=(max(0, ox + dx), oy))
                return fr.tobytes()
            frame_fn = _render_wipe

        elif anim_mode == "ink_bloom":
            act_f = min(n_frames, int(math.ceil(0.44 * fps)) + 1)
            def _render_ink(fi: int) -> bytes:
                if fi >= act_f:
                    return settled_bytes
                u = min(1.0, (fi / float(fps)) / 0.42)
                ease_u = 1.0 - (1.0 - u) ** 2
                rad = max(0.0, (1.0 - ease_u) * 7.0)
                im = base_img.filter(ImageFilter.GaussianBlur(radius=rad)) if rad > 0.4 else base_img
                im = _scale_layer_centered(im, 0.94 + 0.06 * ease_u, alpha_mul=ease_u)
                fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                fr.alpha_composite(im, dest=((cw - im.width) // 2, (ch - im.height) // 2))
                return fr.tobytes()
            frame_fn = _render_ink

        elif anim_mode == "number_flip_trio":
            act_f = min(n_frames, int(math.ceil(0.62 * fps)) + 1)
            seg_w = max(4, base_img.width // 3)
            slices = [
                (base_img.crop((0, 0, seg_w, base_img.height)), 0, 0.0),
                (base_img.crop((seg_w, 0, seg_w * 2, base_img.height)), seg_w, 0.14),
                (base_img.crop((seg_w * 2, 0, base_img.width, base_img.height)), seg_w * 2, 0.28),
            ]
            def _render_flip(fi: int) -> bytes:
                if fi >= act_f:
                    return settled_bytes
                t = fi / float(fps)
                fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                for sl_img, sx, delay in slices:
                    if t < delay:
                        continue
                    u = min(1.0, (t - delay) / 0.28)
                    sy_sc = max(0.08, min(1.06, _ease_out_back(u)))
                    nh = max(2, int(round(sl_img.height * sy_sc)))
                    res = sl_img.resize((sl_img.width, nh), Image.Resampling.BILINEAR)
                    py = oy + (sl_img.height - nh) // 2
                    oy0 = max(0, py)
                    ry0 = max(0, -py)
                    rh = min(nh - ry0, ch - oy0)
                    if rh > 0:
                        fr.alpha_composite(res.crop((0, ry0, res.width, ry0 + rh)), dest=(ox + sx, oy0))
                return fr.tobytes()
            frame_fn = _render_flip

        else:  # depth_behind_person 或通用轻缓上浮淡入
            act_f = min(n_frames, int(math.ceil(0.38 * fps)) + 1)
            def _render_float_up(fi: int) -> bytes:
                if fi >= act_f:
                    return settled_bytes
                u = min(1.0, (fi / float(fps)) / 0.35)
                ease_u = 1.0 - (1.0 - u) ** 3
                dy = int(round((1.0 - ease_u) * 24))
                im = _scale_layer_centered(base_img, 1.0, alpha_mul=ease_u)
                fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                py = max(0, min(ch - im.height, oy + dy))
                fr.alpha_composite(im, dest=(ox, py))
                return fr.tobytes()
            frame_fn = _render_float_up

    dim_after = elem.get("dim_after_sec")
    dim_alpha = float(elem.get("dim_alpha") if elem.get("dim_alpha") is not None else 1.0)
    if dim_after is not None and 0.0 < dim_alpha < 0.99:
        try:
            dim_start_t = float(dim_after)
            orig_pre_dim = frame_fn

            def _with_dim_alpha(fi: int) -> bytes:
                raw = orig_pre_dim(fi)
                t_cur = fi / float(fps)
                if t_cur < dim_start_t:
                    return raw
                ramp = min(1.0, (t_cur - dim_start_t) / 0.18)
                cur_mul = 1.0 - (1.0 - dim_alpha) * ramp
                im = Image.frombytes("RGBA", (cw, ch), raw)
                r, g, b, a = im.split()
                a = a.point(lambda px: int(px * cur_mul))
                return Image.merge("RGBA", (r, g, b, a)).tobytes()

            frame_fn = _with_dim_alpha
        except (TypeError, ValueError):
            pass

    out_anim = str(elem.get("out_anim") or "").strip().lower()
    if out_anim in ("fade_out", "渐隐", "slide_down", "向下滑动") and n_frames >= 14:
        out_dur = max(0.24, min(0.55, float(elem.get("out_anim_sec") or 0.45)))
        out_frames = max(4, min(n_frames // 2, int(round(out_dur * fps))))
        orig_fn = frame_fn
        slide = out_anim in ("slide_down", "向下滑动")

        def _with_out_anim(fi: int) -> bytes:
            raw = orig_fn(fi)
            if fi < n_frames - out_frames:
                return raw
            v = min(1.0, (fi - (n_frames - out_frames) + 1) / float(out_frames))
            alpha_k = max(0.0, 1.0 - (v ** 1.6))
            im = Image.frombytes("RGBA", (cw, ch), raw)
            r, g, b, a = im.split()
            a = a.point(lambda px: int(px * alpha_k))
            im = Image.merge("RGBA", (r, g, b, a))
            if slide:
                dy = int(round(36.0 * (v ** 1.5)))
                if dy > 0:
                    shifted = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                    if ch > dy:
                        shifted.alpha_composite(im.crop((0, 0, cw, ch - dy)), dest=(0, dy))
                    im = shifted
            return im.tobytes()

        frame_fn = _with_out_anim

    if not _encode_overlay_mov(ffmpeg_bin, out_mov, cw, ch, fps, n_frames, frame_fn):
        return None
    return out_mov, cw, ch
