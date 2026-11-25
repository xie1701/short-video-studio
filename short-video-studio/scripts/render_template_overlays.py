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

import json
import math
import platform
import subprocess
import sys
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
        rows = []
        for line, size, color in (
            (l1, 42 if quote else main_fs, layout["main_hex"]),
            (l2, 28 if quote else layout["font_size_px"], layout["sub_hex"]),
        ):
            sprite = _draw_kinetic_line_sprite(
                " ".join(line.splitlines()), render_font, size, hex_to_rgba(color), max_w=text_w,
                stroke_w=layout["stroke_px"], **layout["shadow"],
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
    if raw_prim:
        anim_mode = resolve_anim_mode(raw_prim, anim_mode)
    elif prim_list and anim_mode not in SIGNATURE_ANIM_MODES:
        slot = int(elem.get("anim_slot") or 0)
        anim_mode = resolve_anim_mode(prim_list[slot % len(prim_list)], anim_mode)

    out_mov = Path(out_mov)
    out_mov.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.35, float(duration_sec))
    n_frames = max(10, int(round(dur * fps)))

    # 1. style_01 专属三拍分步砸屏（仅开场海报/花字触发，片尾卡/贴纸走各自专用组件）
    _ek = str(elem.get("kind") or elem.get("type") or elem.get("element_type") or "")
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
        if motion_fn is not None:
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
        for fi in range(n_frames):
            if proc.stdin:
                proc.stdin.write(frame_fn(fi))
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()

    if proc.returncode != 0 or not out_mov.is_file():
        return None
    return out_mov, cw, ch
