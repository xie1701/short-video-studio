#!/usr/bin/env python3
"""
[24 套剪映模板高保真透明叠层渲染引擎]
依据 assets/style_spectrum/template_blueprints.json，为 24 套风格分别渲染各自专属的
开场海报大字、花字、红底条、暗灰斜体底框、3D 金属渐变立体大字、左上角斜杠大标、
拍立得手写金句+Daily Vlog、纪实面板标题、国风黑条粗宋体等透明 RGBA 叠层，
彻底告别旧版“24 套千篇一律黑底黄字红角标”的粗糙感。
"""

from __future__ import annotations

import json
import math
import platform
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
    find_commercial_font,
    get_cached_font,
    hex_to_rgba,
)

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BLUEPRINTS_PATH = ASSETS_DIR / "style_spectrum" / "template_blueprints.json"


@lru_cache(maxsize=4)
def load_template_blueprints() -> Dict[str, Any]:
    if BLUEPRINTS_PATH.is_file():
        return json.loads(BLUEPRINTS_PATH.read_text(encoding="utf-8")).get("blueprints", {})
    return {}


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
    for fs in range(max_fs, min_fs - 1, -2):
        fnt = get_cached_font(font_path, fs)
        bb = draw.textbbox((0, 0), t, font=fnt, stroke_width=stroke_w)
        if (bb[2] - bb[0]) <= max_w:
            return fnt, t, fs
    fnt = get_cached_font(font_path, min_fs)
    while len(t) > 2:
        bb = draw.textbbox((0, 0), t + "…", font=fnt, stroke_width=stroke_w)
        if (bb[2] - bb[0]) <= max_w:
            return fnt, t + "…", min_fs
        t = t[:-1]
    return fnt, t, min_fs


def _split_two_lines(text: str, max_first: int = 8) -> Tuple[str, str]:
    t = str(text or "").strip()
    if not t:
        return "", ""
    for sep in ("，", "。", "！", "？", "：", " ", "/", "|", "·"):
        if sep in t:
            parts = [p.strip() for p in t.split(sep, 1) if p.strip()]
            if len(parts) == 2:
                return parts[0][:10], parts[1][:12]
    if len(t) <= max_first:
        return t, ""
    mid = min(max_first, max(3, len(t) // 2))
    return t[:mid], t[mid : mid + 10]


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
) -> Image.Image:
    """渲染高质感 3D 金属渐变立体字（复刻 style_18 户外探盘等爆款立体字）。"""
    probe = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    pd = ImageDraw.Draw(probe)
    fnt, fitted, fs = _fit_text(pd, text, font_path, max_w - 60, font_size, 44, stroke_w=8)
    bb = pd.textbbox((0, 0), fitted, font=fnt, stroke_width=8)
    tw = max(80, bb[2] - bb[0])
    th = max(60, bb[3] - bb[1])
    pad = 28
    w = tw + pad * 2 + depth_px + 8
    h = th + pad * 2 + depth_px + 8

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out)
    ox = pad - bb[0]
    oy = pad - bb[1]

    # 1. 深色立体挤出厚底 + 柔和外阴影
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.text((ox + depth_px + 4, oy + depth_px + 6), fitted, font=fnt, fill=(0, 0, 0, 185), stroke_width=10, stroke_fill=(0, 0, 0, 185))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
    out.alpha_composite(shadow)

    for d in range(depth_px, 0, -1):
        shade = 0.65 + 0.35 * (1.0 - d / max(1, depth_px))
        ec = (int(extrude_rgb[0] * shade), int(extrude_rgb[1] * shade), int(extrude_rgb[2] * shade), 255)
        draw.text((ox + int(d * 0.45), oy + d), fitted, font=fnt, fill=ec, stroke_width=8, stroke_fill=ec)

    # 2. 外描边底
    draw.text((ox, oy), fitted, font=fnt, fill=(*stroke_rgb, 255), stroke_width=8, stroke_fill=(*stroke_rgb, 255))

    # 3. 金属纵向三段渐变填充
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.text((ox, oy), fitted, font=fnt, fill=255)

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

    # 顶部微高光条
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
    probe = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    pd = ImageDraw.Draw(probe)
    fnt, fitted, _ = _fit_text(pd, text, font_path, max_w - 40, font_size, 34, stroke_w=stroke_w)
    bb = pd.textbbox((0, 0), fitted, font=fnt, stroke_width=stroke_w)
    tw, th = max(40, bb[2] - bb[0]), max(40, bb[3] - bb[1])
    pad = 22
    img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ox, oy = pad - bb[0], pad - bb[1]
    d.text((ox + 5, oy + 6), fitted, font=fnt, fill=(0, 0, 0, 195), stroke_width=stroke_w, stroke_fill=(0, 0, 0, 195))
    d.text((ox, oy), fitted, font=fnt, fill=fill_rgba, stroke_width=stroke_w, stroke_fill=stroke_rgba)
    if abs(tilt_deg) > 0.2:
        img = img.rotate(-tilt_deg, resample=Image.Resampling.BICUBIC, expand=True)
    ink = img.getbbox()
    return img.crop(ink) if ink else img


def render_template_packaging_png(
    elem: Dict[str, Any],
    style_id: str = "style_01",
    font_candidates: Optional[List[str]] = None,
) -> Optional[Image.Image]:
    """
    依据 24 套剪映模板蓝图渲染高还原度透明叠层：
    返回 RGBA Image（若无需特化则回退给 render_mp4 默认渲染）。
    """
    bp = get_style_blueprint(style_id)
    family = str(bp.get("template_family") or "")
    ov_style = bp.get("overlay_style") or {}
    if not family:
        return None

    elem_kind = str(elem.get("type") or elem.get("element_type") or "flower_text")
    main_text = str(elem.get("text_or_asset") or elem.get("text") or "").strip()
    sub_text = str(elem.get("subtext") or elem.get("tag_label") or "").strip()
    if not main_text:
        return None

    font_path = find_commercial_font(font_candidates)
    serif_font = find_commercial_font(["SourceHanSerifCN-Heavy.otf", "SourceHanSerifCN-Bold.otf"]) or font_path
    sans_font = find_commercial_font(["AlimamaShuHeiTi-Bold.otf", "SmileySans-Oblique.ttf"]) or font_path
    oblique_font = find_commercial_font(["SmileySans-Oblique.ttf", "AlimamaShuHeiTi-Bold.otf"]) or font_path

    # 1. style_01（默认门店爆款风）：复刻剪映门店爆款——倾斜红底角标 + 亮黄绿粗描边大字 + 红金撞色副标
    if family == "store_poster_burst":
        l1, l2 = _split_two_lines(main_text, max_first=6)
        tag = sub_text[:8] if sub_text and sub_text != "核心重点" else ""
        canvas = Image.new("RGBA", (860, 320), (0, 0, 0, 0))
        y_cur = 12
        if tag or elem_kind == "center_burst_title":
            badge_txt = tag or l1[:5]
            if not tag and l2:
                pass
            elif tag:
                # 顶部红底微斜小标
                b_img = Image.new("RGBA", (420, 66), (0, 0, 0, 0))
                bd = ImageDraw.Draw(b_img)
                bf, bt, _ = _fit_text(bd, badge_txt, sans_font, 360, 34, 22)
                bb = bd.textbbox((0, 0), bt, font=bf)
                bw = (bb[2] - bb[0]) + 40
                bh = (bb[3] - bb[1]) + 22
                bx0 = (420 - bw) // 2
                bd.rounded_rectangle([(bx0, 6), (bx0 + bw, 6 + bh)], radius=10, fill=(224, 34, 34, 245), outline=(255, 255, 255, 235), width=3)
                bd.text((210, 6 + bh // 2), bt, font=bf, fill=(255, 255, 255, 255), anchor="mm", stroke_width=2, stroke_fill=(90, 8, 8, 255))
                b_rot = b_img.rotate(5, resample=Image.Resampling.BICUBIC, expand=True)
                b_ink = b_rot.getbbox()
                if b_ink:
                    b_rot = b_rot.crop(b_ink)
                    canvas.alpha_composite(b_rot, dest=((860 - b_rot.width) // 2 - 40, y_cur))
                    y_cur += b_rot.height - 6

        # 主爆发大字（亮黄绿/暖金强描边 + 微倾角）
        if elem_kind == "center_burst_title":
            main_img = _draw_tilted_stroked_text(
                l1, sans_font, 760, 96, (255, 228, 64, 255), (12, 12, 14, 255), stroke_w=10, tilt_deg=-4.5
            )
        else:
            main_img = _draw_tilted_stroked_text(
                l1, oblique_font, 740, 74, (132, 255, 74, 255), (12, 26, 10, 255), stroke_w=8, tilt_deg=-4.0
            )
        canvas.alpha_composite(main_img, dest=((860 - main_img.width) // 2, y_cur))
        y_cur += main_img.height - 4

        if l2:
            sub_img = _draw_gradient_3d_text(
                l2, sans_font, 760, 78,
                top_rgb=(255, 110, 92), mid_rgb=(238, 36, 42), bot_rgb=(176, 12, 22),
                extrude_rgb=(45, 8, 12), stroke_rgb=(255, 255, 255), depth_px=6,
            )
            canvas.alpha_composite(sub_img, dest=((860 - sub_img.width) // 2 + 12, min(318 - sub_img.height, y_cur)))

        ink = canvas.getbbox()
        return canvas.crop(ink) if ink else None

    # 2. style_02：极速跳剪暗灰圆角底框 + 白/荧光黄绿双行斜粗体
    if family == "dark_gray_neon_slab":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 760, (176 if l2 else 112)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=20, fill=(28, 32, 30, 224))
        f1, t1, _ = _fit_text(d, l1, oblique_font, w - 56, 56, 30, stroke_w=3)
        if l2:
            f2, t2, _ = _fit_text(d, l2, oblique_font, w - 56, 60, 32, stroke_w=3)
            d.text((w // 2, 54), t1, font=f1, fill=(255, 255, 255, 255), anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0, 240))
            d.text((w // 2, 124), t2, font=f2, fill=(188, 255, 0, 255), anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0, 240))
        else:
            d.text((w // 2, h // 2), t1, font=f1, fill=(188, 255, 0, 255), anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0, 240))
        return img

    # 3. style_03：暗调深度深红实心底条双行大字
    if family == "red_bar_dual_line":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 780, (174 if l2 else 104)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=8, fill=(182, 26, 26, 238))
        f1, t1, _ = _fit_text(d, l1, sans_font, w - 48, 56, 30, stroke_w=2)
        if l2:
            f2, t2, _ = _fit_text(d, l2, sans_font, w - 48, 56, 30, stroke_w=2)
            d.text((w // 2, 52), t1, font=f1, fill=(255, 255, 255, 255), anchor="mm", stroke_width=2, stroke_fill=(40, 4, 4, 240))
            d.text((w // 2, 122), t2, font=f2, fill=(255, 244, 150, 255), anchor="mm", stroke_width=2, stroke_fill=(40, 4, 4, 240))
        else:
            d.text((w // 2, h // 2), t1, font=f1, fill=(255, 255, 255, 255), anchor="mm", stroke_width=2, stroke_fill=(40, 4, 4, 240))
        return img

    # 4. style_18：户外探盘 3D 金属渐变立体大字
    if family == "metallic_3d_gold_title":
        return _draw_gradient_3d_text(
            main_text[:10], sans_font, 820,
            font_size=108 if elem_kind == "center_burst_title" else 82,
            top_rgb=(255, 248, 186), mid_rgb=(255, 194, 34), bot_rgb=(218, 120, 14),
            extrude_rgb=(78, 36, 8), stroke_rgb=(48, 22, 4), depth_px=10,
        )

    # 5. style_08：专家长镜左上角斜杠双色大标
    if family == "top_left_slash_badge":
        l1, l2 = _split_two_lines(main_text, max_first=7)
        top_line = f"/{l1}/"
        bot_line = l2 or sub_text[:8] or "真实干货"
        w, h = 680, 170
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f1, t1, _ = _fit_text(d, top_line, sans_font, w - 30, 48, 28, stroke_w=5)
        f2, t2, _ = _fit_text(d, bot_line, sans_font, w - 30, 64, 34, stroke_w=7)
        d.text((22, 36), t1, font=f1, fill=(255, 255, 255, 255), anchor="lm", stroke_width=5, stroke_fill=(15, 23, 42, 245))
        d.text((22, 114), t2, font=f2, fill=(255, 228, 72, 255), anchor="lm", stroke_width=7, stroke_fill=(15, 23, 42, 255))
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 6. style_06：黑底拍立得顶部两行优雅白字金句
    if family == "black_polaroid_vlog":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 760, 190
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f1, t1, _ = _fit_text(d, l1, serif_font, w - 40, 62, 34)
        if l2:
            f2, t2, _ = _fit_text(d, l2, serif_font, w - 40, 62, 34)
            d.text((w // 2, 54), t1, font=f1, fill=(255, 255, 255, 250), anchor="mm")
            d.text((w // 2, 132), t2, font=f2, fill=(255, 255, 255, 250), anchor="mm")
        else:
            d.text((w // 2, 95), t1, font=f1, fill=(255, 255, 255, 250), anchor="mm")
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 7. style_20：国风养生下半屏黑条双行米白粗宋体 + 朱红印
    if family == "guofeng_black_band_serif":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 840, (176 if l2 else 112)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([(0, 0), (w - 1, h - 1)], fill=(18, 14, 11, 196))
        d.line([(24, 6), (w - 24, 6)], fill=(200, 155, 84, 150), width=2)
        d.line([(24, h - 7), (w - 24, h - 7)], fill=(200, 155, 84, 150), width=2)
        f1, t1, _ = _fit_text(d, l1, serif_font, w - 120, 56, 30)
        if l2:
            f2, t2, _ = _fit_text(d, l2, serif_font, w - 120, 56, 30)
            d.text((w // 2 - 20, 54), t1, font=f1, fill=(247, 239, 225, 255), anchor="mm")
            d.text((w // 2 - 20, 124), t2, font=f2, fill=(242, 201, 125, 255), anchor="mm")
        else:
            d.text((w // 2 - 20, h // 2), t1, font=f1, fill=(247, 239, 225, 255), anchor="mm")
        # 右侧迷你朱红方印
        d.rounded_rectangle([(w - 76, h // 2 - 30), (w - 24, h // 2 + 30)], radius=6, fill=(166, 26, 28, 235), outline=(242, 201, 125, 230), width=2)
        sf = get_cached_font(serif_font, 22)
        d.text((w - 50, h // 2 - 12), "真", font=sf, fill=(255, 248, 232, 255), anchor="mm")
        d.text((w - 50, h // 2 + 12), "言", font=sf, fill=(255, 248, 232, 255), anchor="mm")
        return img

    # 8. style_04 / style_13 / style_23：清冷极简轻阴影思源宋体（零多余花哨大红框）
    if family in ("cream_minimal_serif", "cinema_serif_quiet", "micro_doc_film_serif"):
        w, h = 760, 110
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f1, t1, _ = _fit_text(d, main_text[:12], serif_font, w - 40, 54, 30, stroke_w=2)
        d.text((w // 2 + 2, h // 2 + 3), t1, font=f1, fill=(0, 0, 0, 160), anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0, 160))
        d.text((w // 2, h // 2), t1, font=f1, fill=(255, 252, 245, 255), anchor="mm", stroke_width=2, stroke_fill=(32, 28, 24, 210))
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 9. style_09：纯黑纪实面板（案情大标题 + 金线分隔）
    if family == "dark_doc_dual_gold_panel":
        w, h = 580, 124
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f1, t1, _ = _fit_text(d, main_text[:12], sans_font, w - 24, 52, 28)
        d.text((12, 46), t1, font=f1, fill=(248, 246, 240, 255), anchor="lm")
        d.line([(12, 92), (min(w - 20, 360), 92)], fill=(204, 169, 102, 240), width=4)
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 10. style_14：深色控制台步骤胶囊条
    if family == "dark_console_stepper":
        w, h = 760, 86
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=20, fill=(14, 17, 23, 232), outline=(245, 158, 11, 210), width=2)
        d.rounded_rectangle([(14, 16), (92, h - 16)], radius=12, fill=(245, 158, 11, 245))
        bf = get_cached_font(sans_font, 28)
        d.text((53, h // 2), "STEP", font=bf, fill=(12, 14, 18, 255), anchor="mm")
        f1, t1, _ = _fit_text(d, main_text[:14], sans_font, w - 130, 42, 24)
        d.text((114, h // 2), t1, font=f1, fill=(248, 250, 252, 255), anchor="lm")
        return img

    # 11. style_19：冷峻数据新闻白底黑字精悍条
    if family == "cool_data_news_cutout":
        probe = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        pd = ImageDraw.Draw(probe)
        f1, t1, _ = _fit_text(pd, main_text[:12], sans_font, 680, 48, 26)
        bb = pd.textbbox((0, 0), t1, font=f1)
        bw = (bb[2] - bb[0]) + 56
        bh = (bb[3] - bb[1]) + 28
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (bw - 1, bh - 1)], radius=6, fill=(255, 255, 255, 238))
        d.rectangle([(0, 0), (10, bh - 1)], fill=(3, 105, 161, 255))
        d.text((bw // 2 + 4, bh // 2), t1, font=f1, fill=(15, 23, 42, 255), anchor="mm")
        return img

    # 12. style_05：牛皮纸工艺红线尺标卡
    if family == "paper_craft_diagram":
        w, h = 720, 108
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=10, fill=(246, 239, 224, 244), outline=(198, 40, 40, 235), width=3)
        d.line([(20, h - 14), (w - 20, h - 14)], fill=(198, 40, 40, 200), width=2)
        for tx in range(28, w - 24, 28):
            d.line([(tx, h - 22), (tx, h - 10)], fill=(198, 40, 40, 200), width=2)
        f1, t1, _ = _fit_text(d, main_text[:12], sans_font, w - 60, 46, 26)
        d.text((w // 2, h // 2 - 6), t1, font=f1, fill=(42, 33, 24, 255), anchor="mm")
        return img

    # 13. style_12：极简白底笔记下划线卡
    if family == "white_note_card":
        w, h = 720, 100
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=12, fill=(255, 255, 255, 244), outline=(24, 24, 27, 235), width=3)
        f1, t1, _ = _fit_text(d, main_text[:14], sans_font, w - 64, 44, 24)
        d.text((w // 2, h // 2 - 4), t1, font=f1, fill=(17, 24, 39, 255), anchor="mm")
        d.rectangle([(48, h - 16), (w - 48, h - 11)], fill=(37, 99, 235, 220))
        return img

    # 14. style_16：暗黑羽化书摘巨字
    if family == "dark_feather_book_quote":
        l1, l2 = _split_two_lines(main_text, max_first=8)
        w, h = 780, (170 if l2 else 110)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        f1, t1, _ = _fit_text(d, l1, serif_font, w - 40, 68, 36)
        if l2:
            f2, t2, _ = _fit_text(d, l2, serif_font, w - 40, 68, 36)
            d.text((w // 2, 50), t1, font=f1, fill=(255, 255, 255, 255), anchor="mm")
            d.text((w // 2, 124), t2, font=f2, fill=(250, 204, 21, 255), anchor="mm")
        else:
            d.text((w // 2, h // 2), t1, font=f1, fill=(250, 204, 21, 255), anchor="mm")
        ink = img.getbbox()
        return img.crop(ink) if ink else img

    # 15. style_22：顶栏常驻醒目横幅
    if family == "top_banner_case_study":
        w, h = 920, 120
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=14, fill=(11, 13, 18, 238), outline=(250, 204, 21, 235), width=3)
        f1, t1, _ = _fit_text(d, main_text[:14], sans_font, w - 60, 56, 30)
        d.text((w // 2, h // 2), t1, font=f1, fill=(250, 204, 21, 255), anchor="mm")
        return img

    # 16. 其余风格（style_07/10/11/15/17/21/24）：按蓝图 accent_colors 渲染无廉价红标的干净高质感透明字
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


def render_vision_person_cutout_frame(
    fg_image: Image.Image,
    bg_image: Image.Image,
    outline_px: int = 10,
    outline_rgb: Tuple[int, int, int] = (255, 255, 255),
) -> Optional[Image.Image]:
    """
    macOS 原生 Apple Vision 真人前景白边剪纸抠像合成器（用于 style_07 / style_19）。
    输入同尺寸前景人像图与背景实景图，返回合成后的 RGBA 图像；非 macOS 或 Vision 不可用时返回 None。
    """
    if platform.system() != "Darwin" or not shutil.which("swift"):
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="svs_vis_mat_") as td:
            in_p = Path(td) / "fg.png"
            mask_p = Path(td) / "mask.png"
            fg_rgb = fg_image.convert("RGB")
            fg_rgb.save(in_p)
            swift_code = f'''
import Foundation
import Vision
import CoreImage
import AppKit

let url = URL(fileURLWithPath: "{in_p.as_posix()}")
guard let ciImg = CIImage(contentsOf: url) else {{ exit(2) }}
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .balanced
req.outputPixelFormat = kCVPixelFormatType_OneComponent8
let handler = VNImageRequestHandler(ciImage: ciImg, options: [:])
try handler.perform([req])
guard let obs = req.results?.first as? VNPixelBufferObservation else {{ exit(3) }}
let maskCI = CIImage(cvPixelBuffer: obs.pixelBuffer)
let sx = ciImg.extent.width / maskCI.extent.width
let sy = ciImg.extent.height / maskCI.extent.height
let scaled = maskCI.transformed(by: CGAffineTransform(scaleX: sx, y: sy))
let ctx = CIContext()
guard let cg = ctx.createCGImage(scaled, from: ciImg.extent) else {{ exit(4) }}
let rep = NSBitmapImageRep(cgImage: cg)
guard let png = rep.representation(using: .png, properties: [:]) else {{ exit(5) }}
try png.write(to: URL(fileURLWithPath: "{mask_p.as_posix()}"))
'''
            proc = subprocess.run(["swift", "-e", swift_code], capture_output=True, text=True, timeout=20)
            if proc.returncode != 0 or not mask_p.is_file():
                return None
            mask_l = Image.open(mask_p).convert("L").resize(fg_rgb.size, Image.Resampling.BILINEAR)
            bg_rgba = bg_image.convert("RGBA").resize(fg_rgb.size, Image.Resampling.LANCZOS)
            fg_rgba = fg_rgb.convert("RGBA")
            if outline_px > 0:
                dil = mask_l.filter(ImageFilter.MaxFilter(size=max(3, (outline_px * 2) | 1)))
                dil = dil.point(lambda v: 255 if v >= 120 else 0)
                outline_layer = Image.new("RGBA", fg_rgb.size, (*outline_rgb, 255))
                bg_rgba.paste(outline_layer, (0, 0), dil)
            soft_mask = mask_l.point(lambda v: 255 if v >= 135 else (0 if v <= 60 else int((v - 60) * 255 / 75)))
            bg_rgba.paste(fg_rgba, (0, 0), soft_mask)
            return bg_rgba
    except Exception:
        return None
