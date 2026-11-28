#!/usr/bin/env python3
"""
style_truth_renderer.py · 24 套风格真母本高保真渲染原语（V10.0）

统一封装 7 大视觉家族的前景叠层与镜面合成原语：
- 全片常驻真母本顶栏（style_03 / style_06 / style_08 / style_12）与开场人设卡（style_06）
- 纠偏后的各风格标志性前景花字/拼音大字/红底块/引号白条
- 黑屏/虚化圆窗看板（style_07/09/13/14/19/20/22/23）
- 炭墨/重虚化打字机与白条引语卡（style_03/14/16/18/20）
- 圆角方窗与双图对比（style_04/13/17/24）
- 浅灰白底产品多图测评白板（style_24）
- 电影感红色拼音注音大字（style_15）
- AI 抠像前景压巨字 / 纯黑抠像 / 剪纸白描边底图（style_03/05/07/10/11/14/16/18/21/22/23）
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from common import (
    BT709_FFMPEG_ARGS,
    BT709_VF_SUFFIX,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    find_commercial_font,
    get_cached_font,
    hex_to_rgba,
    normalize_display_glyphs,
)
from glyph_fallback import glyph_font_for
from style_visual_truth import get_visual_truth


def _load_font_role(role: str = "sans", extra: Optional[List[str]] = None) -> Optional[str]:
    role_map = {
        "sans": ["AlimamaShuHeiTi-Bold.otf", "SourceHanSansCN-Bold.otf", "SmileySans-Oblique.ttf"],
        "serif": ["SourceHanSerifCN-Heavy.otf", "SourceHanSerifCN-Bold.otf", "AlimamaShuHeiTi-Bold.otf"],
        "serif_bold": ["SourceHanSerifCN-Bold.otf", "SourceHanSerifCN-Heavy.otf", "AlimamaShuHeiTi-Bold.otf"],
        "oblique": ["SmileySans-Oblique.ttf", "AlimamaShuHeiTi-Bold.otf"],
        "hand": ["MaShanZheng-Regular.ttf", "SourceHanSerifCN-Bold.otf"],
    }
    cands = [*(extra or []), *role_map.get(role, role_map["sans"])]
    return find_commercial_font(cands) or find_commercial_font()


def _text_line_rgba(
    text: str,
    font_path: Optional[str],
    max_w: int,
    font_size: int,
    fill_rgba: Tuple[int, int, int, int],
    *,
    stroke_rgba: Optional[Tuple[int, int, int, int]] = None,
    stroke_w: int = 0,
    shadow_rgba: Optional[Tuple[int, int, int, int]] = None,
    shadow_dy: int = 4,
    shadow_blur: float = 2.5,
    min_fs: int = 24,
    spacing_px: float = 0.0,
) -> Optional[Image.Image]:
    """逐字回退字体的高精度单行 RGBA 文字精灵（按墨迹紧裁）。"""
    t = normalize_display_glyphs(str(text or "").strip())
    if not t:
        return None
    sw = max(0, int(stroke_w))
    probe = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    pd = ImageDraw.Draw(probe)
    fs = max(min_fs, int(font_size))
    limit = max(60, int(max_w) - sw * 2)

    def _w(sz: int) -> float:
        return sum(pd.textlength(c, font=glyph_font_for(c, font_path, sz)) for c in t) + spacing_px * max(0, len(t) - 1)

    while fs > min_fs and _w(fs) > limit:
        fs -= 2

    pad = sw + max(10, abs(shadow_dy) + int(shadow_blur * 2) + 8)
    cw = max(32, int(math.ceil(_w(fs))) + pad * 2)
    ch = max(32, int(fs * 1.55) + pad * 2)
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 先按基线收集每字位置
    cur_x = float(pad)
    base_y = float(pad + int(fs * 0.15))
    glyphs: List[Tuple[str, Any, float]] = []
    for char in t:
        f = glyph_font_for(char, font_path, fs)
        glyphs.append((char, f, cur_x))
        cur_x += pd.textlength(char, font=f) + spacing_px

    if shadow_rgba and shadow_rgba[3] > 0:
        sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        for char, f, gx in glyphs:
            sd.text((gx, base_y + shadow_dy), char, font=f, fill=shadow_rgba, stroke_width=sw, stroke_fill=shadow_rgba)
        if shadow_blur > 0.2:
            sh = sh.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        img.alpha_composite(sh)

    for char, f, gx in glyphs:
        if sw > 0 and stroke_rgba:
            draw.text((gx, base_y), char, font=f, fill=fill_rgba, stroke_width=sw, stroke_fill=stroke_rgba)
        else:
            draw.text((gx, base_y), char, font=f, fill=fill_rgba)

    ink = img.getbbox()
    return img.crop(ink) if ink else img


def _wrap_lines(text: str, max_chars_per_line: int, max_lines: int = 4) -> List[str]:
    clean = normalize_display_glyphs(re.sub(r"\s+", "", str(text or "").strip()))
    if not clean:
        return []
    try:
        from step5_subtitles import dp_segment_short_lines
        segs = dp_segment_short_lines(
            clean,
            max_width_px=max(260, int(max_chars_per_line) * 68),
            font_path=None,
            font_size=56,
            max_chars=max(5, int(max_chars_per_line)),
            strip_punct=False,
        )
        lines = [t.strip("，、；：,;: ") for _, _, t in segs if t.strip("，、；：,;: ")]
        if lines:
            return lines[:max_lines]
    except Exception:
        pass
    parts = [p for p in re.split(r"[，。！？、,.!?；;]+", clean) if p]
    lines = []
    buf = ""
    for p in parts:
        if not buf:
            while len(p) > max_chars_per_line:
                lines.append(p[:max_chars_per_line])
                p = p[max_chars_per_line:]
            buf = p
        elif len(buf) + len(p) + 1 <= max_chars_per_line:
            buf = f"{buf}，{p}"
        else:
            lines.append(buf)
            while len(p) > max_chars_per_line:
                lines.append(p[:max_chars_per_line])
                p = p[max_chars_per_line:]
            buf = p
    if buf:
        lines.append(buf)
    return lines[:max_lines]


def _split_two(text: str, subtext: str = "", max_first: int = 8) -> Tuple[str, str]:
    t = normalize_display_glyphs(str(text or "").strip())
    s = normalize_display_glyphs(str(subtext or "").strip())
    if s and s != t:
        return t[:11], s[:11]
    if "\n" in t:
        ps = [x.strip() for x in t.splitlines() if x.strip()]
        return (ps[0][:11], ps[1][:11]) if len(ps) >= 2 else (ps[0][:11], "")
    if len(t) <= max_first:
        return t, ""
    try:
        from step5_subtitles import split_semantic_two_lines
        l1, l2 = split_semantic_two_lines(t, max_first=max_first)
        if l1:
            return l1[:11], l2[:11]
    except Exception:
        pass
    mid = min(max_first, max(3, (len(t) + 1) // 2))
    return t[:mid], t[mid:mid + 11]


def _pinyin_for_chars(chars: str) -> List[str]:
    try:
        from pypinyin import Style, pinyin  # type: ignore
        res = pinyin(chars, style=Style.NORMAL, errors="default")
        return [str(x[0] if x else "").strip() for x in res]
    except Exception:
        return [""] * len(chars)


# ══════════════════════════════════════════════════════════════════════════════
# 组 A · 前景叠加 PNG 渲染（全片常驻真母本顶栏 + 纠偏前景花字/人设卡）
# ══════════════════════════════════════════════════════════════════════════════

def render_truth_overlay_png(
    elem: Dict[str, Any],
    style_id: str,
    font_candidates: Optional[List[str]] = None,
) -> Optional[Image.Image]:
    """按 24 套真母本视觉配方渲染顶栏与纠偏后的标志性前景叠加层；未命中则返回 None 交回旧渲染器。"""
    elem_kind = str(elem.get("kind") or elem.get("type") or "")
    main_text = str(elem.get("text_or_asset") or elem.get("text") or "").strip()
    # 带 burst_tags 的多标签爆点贴纸保留既有 98px 紧凑渲染契约
    if elem_kind == "stamp_tag" and elem.get("burst_tags"):
        return None
    sub_text = str(elem.get("subtext") or "").strip()

    # 1) 全片常驻真母本顶栏（仅 style_03 / 06 / 08 / 12）
    if elem_kind == "persistent_top_bar":
        tb_mode = str(elem.get("top_bar_mode") or (get_visual_truth(style_id).get("top_bar") or {}).get("mode") or "")
        if not tb_mode:
            return None
        l1, l2 = _split_two(main_text, sub_text, max_first=9)
        if not l1:
            return None

        if tb_mode == "s03_dual_yellow_serif":
            fnt = _load_font_role("serif", font_candidates)
            sp1 = _text_line_rgba(l1, fnt, 820, 50, (253, 224, 71, 255), stroke_rgba=(10, 10, 14, 255), stroke_w=4, shadow_rgba=(0, 0, 0, 185), shadow_dy=3)
            sp2 = _text_line_rgba(l2, fnt, 820, 50, (253, 224, 71, 255), stroke_rgba=(10, 10, 14, 255), stroke_w=4, shadow_rgba=(0, 0, 0, 185), shadow_dy=3) if l2 else None
            sprites = [s for s in (sp1, sp2) if s is not None]
            if not sprites:
                return None
            w = max(s.width for s in sprites) + 24
            h = sum(s.height for s in sprites) + (4 if len(sprites) > 1 else 0) + 8
            cv = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            y = 4
            for sp in sprites:
                cv.alpha_composite(sp, dest=((w - sp.width) // 2, y))
                y += sp.height + 4
            return cv

        if tb_mode == "s06_translucent_warm_gold":
            fnt = _load_font_role("serif_bold", font_candidates)
            sp1 = _text_line_rgba(l1, fnt, 800, 46, (244, 226, 168, 255), stroke_rgba=(20, 18, 14, 210), stroke_w=2, shadow_rgba=(0, 0, 0, 150), shadow_dy=2)
            sp2 = _text_line_rgba(l2, fnt, 800, 46, (244, 226, 168, 255), stroke_rgba=(20, 18, 14, 210), stroke_w=2, shadow_rgba=(0, 0, 0, 150), shadow_dy=2) if l2 else None
            sprites = [s for s in (sp1, sp2) if s is not None]
            if not sprites:
                return None
            w = min(900, max(760, max(s.width for s in sprites) + 112))
            h = sum(s.height for s in sprites) + (6 if len(sprites) > 1 else 0) + 20
            cv = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(cv)
            d.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=16, fill=(26, 28, 32, 162))
            y = 10
            for sp in sprites:
                cv.alpha_composite(sp, dest=((w - sp.width) // 2, y))
                y += sp.height + 6
            return cv

        if tb_mode == "s08_slash_dual_top_left":
            fnt = _load_font_role("oblique", font_candidates)
            top_l = f"/{l1.strip('/')}/"
            bot_l = l2 or "核心实战拆解"
            sp1 = _text_line_rgba(top_l, fnt, 700, 40, (255, 255, 255, 255), stroke_rgba=(12, 16, 26, 255), stroke_w=4, shadow_rgba=(0, 0, 0, 175), shadow_dy=3)
            sp2 = _text_line_rgba(bot_l, fnt, 700, 50, (255, 225, 77, 255), stroke_rgba=(12, 16, 26, 255), stroke_w=5, shadow_rgba=(0, 0, 0, 180), shadow_dy=3)
            sprites = [s for s in (sp1, sp2) if s is not None]
            if not sprites:
                return None
            w = max(s.width for s in sprites) + 24
            h = sum(s.height for s in sprites) + (4 if len(sprites) > 1 else 0) + 10
            cv = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            y = 5
            for sp in sprites:
                cv.alpha_composite(sp, dest=(12, y))
                y += sp.height + 4
            return cv

        if tb_mode == "s12_white_yellow_dual_top":
            fnt = _load_font_role("sans", font_candidates)
            sp1 = _text_line_rgba(l1, fnt, 780, 46, (255, 255, 255, 255), stroke_rgba=(10, 10, 14, 255), stroke_w=4, shadow_rgba=(0, 0, 0, 190), shadow_dy=3)
            sp2 = _text_line_rgba(l2, fnt, 780, 52, (255, 225, 53, 255), stroke_rgba=(10, 10, 14, 255), stroke_w=5, shadow_rgba=(0, 0, 0, 190), shadow_dy=3) if l2 else None
            sprites = [s for s in (sp1, sp2) if s is not None]
            if not sprites:
                return None
            w = max(s.width for s in sprites) + 24
            h = sum(s.height for s in sprites) + (4 if len(sprites) > 1 else 0) + 8
            cv = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            y = 4
            for sp in sprites:
                cv.alpha_composite(sp, dest=((w - sp.width) // 2, y))
                y += sp.height + 4
            return cv

    # 2) style_06 开场左侧人设头衔名片
    if elem_kind == "opening_profile_card":
        fnt = _load_font_role("serif_bold", font_candidates)
        l1, l2 = _split_two(main_text or "主讲实录", sub_text or "一线真实复盘", max_first=8)
        sp1 = _text_line_rgba(l1, fnt, 440, 38, (255, 253, 245, 255), stroke_rgba=(16, 14, 12, 210), stroke_w=2, shadow_rgba=(0, 0, 0, 165), shadow_dy=2)
        sp2 = _text_line_rgba(f"· {l2}", fnt, 440, 28, (244, 226, 168, 255), stroke_rgba=(16, 14, 12, 200), stroke_w=2, shadow_rgba=(0, 0, 0, 160), shadow_dy=2) if l2 else None
        sprites = [s for s in (sp1, sp2) if s is not None]
        if not sprites:
            return None
        w = max(s.width for s in sprites) + 34
        h = sum(s.height for s in sprites) + 22
        cv = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(cv)
        d.rectangle([(8, 10), (12, h - 10)], fill=(244, 226, 168, 235))
        y = 8
        for sp in sprites:
            cv.alpha_composite(sp, dest=(22, y))
            y += sp.height + 8
        return cv

    # 3) 各风格纠偏后的标志性前景花字/巨字（不触碰 style_01/02/03/17 既有单测契约）
    if elem_kind not in ("center_burst_title", "flower_text", "stamp_tag"):
        return None
    if not main_text:
        return None
    ov_key = str(get_visual_truth(style_id).get("overlay_override") or "")
    if not ov_key or ov_key in ("s01_clean_store", "s03_gold_crimson_callout"):
        return None

    sans_f = _load_font_role("sans", font_candidates)
    serif_f = _load_font_role("serif", font_candidates)
    oblique_f = _load_font_role("oblique", font_candidates)
    is_giant = elem_kind == "center_burst_title"

    if ov_key == "s06_warm_gold_serif":
        sp = _text_line_rgba(main_text[:10], serif_f, 700, 74 if is_giant else 60, (244, 226, 168, 255), stroke_rgba=(20, 18, 14, 220), stroke_w=3, shadow_rgba=(0, 0, 0, 170), shadow_dy=4)
        return sp

    if ov_key == "s08_giant_white_impact":
        sp = _text_line_rgba(main_text[:8], sans_f, 800, 118 if is_giant else 94, (255, 255, 255, 255), stroke_rgba=(10, 12, 16, 255), stroke_w=9, shadow_rgba=(0, 0, 0, 195), shadow_dy=6)
        return sp

    if ov_key == "s10_giant_white_punch":
        sp = _text_line_rgba(main_text[:6], sans_f, 760, 124 if is_giant else 92, (255, 255, 255, 255), stroke_rgba=(12, 14, 18, 255), stroke_w=6, shadow_rgba=(0, 0, 0, 190), shadow_dy=6)
        return sp

    if ov_key == "s12_yellow_sentence_emphasis":
        sp = _text_line_rgba(main_text[:10], sans_f, 760, 82 if is_giant else 68, (255, 225, 53, 255), stroke_rgba=(10, 10, 14, 255), stroke_w=7, shadow_rgba=(0, 0, 0, 195), shadow_dy=5)
        return sp

    if ov_key == "s13_yellow_warning_header":
        sp = _text_line_rgba(main_text[:8], serif_f, 780, 96 if is_giant else 74, (255, 212, 38, 255), stroke_rgba=(12, 12, 16, 255), stroke_w=6, shadow_rgba=(0, 0, 0, 195), shadow_dy=6)
        return sp

    if ov_key == "s14_red_yellow_emphasis":
        sp = _text_line_rgba(main_text[:8], sans_f, 760, 86 if is_giant else 70, (255, 212, 38, 255), stroke_rgba=(175, 28, 28, 255), stroke_w=7, shadow_rgba=(0, 0, 0, 195), shadow_dy=5)
        return sp

    if ov_key == "s15_pinyin_and_crimson_giant":
        if is_giant:
            # 暗红居中巨字（复刻「低收入」）或纯白粗巨字（复刻「根本没有」）
            clean = main_text[:6]
            return _text_line_rgba(clean, sans_f, 800, 118, (255, 255, 255, 255), stroke_rgba=(14, 14, 18, 240), stroke_w=6, shadow_rgba=(0, 0, 0, 200), shadow_dy=6, spacing_px=6.0)
        return _text_line_rgba(main_text[:6], sans_f, 760, 104, (195, 34, 34, 255), stroke_rgba=(20, 8, 8, 220), stroke_w=5, shadow_rgba=(0, 0, 0, 175), shadow_dy=5)

    if ov_key == "s16_glow_white_callout":
        return _text_line_rgba(main_text[:8], sans_f, 760, 94 if is_giant else 74, (255, 255, 255, 255), stroke_rgba=(24, 20, 12, 235), stroke_w=4, shadow_rgba=(250, 204, 21, 180), shadow_dy=0, shadow_blur=9.0)

    if ov_key == "s18_crimson_quote_strip":
        sp = _text_line_rgba(f"“{main_text[:10]}”", sans_f, 620, 48, (185, 28, 28, 255), stroke_rgba=(255, 255, 255, 220), stroke_w=1)
        if sp is None:
            return None
        bw, bh = sp.width + 44, sp.height + 24
        cv = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(cv)
        d.rounded_rectangle([(0, 0), (bw - 1, bh - 1)], radius=10, fill=(252, 252, 252, 242), outline=(220, 38, 38, 220), width=3)
        cv.alpha_composite(sp, dest=((bw - sp.width) // 2, (bh - sp.height) // 2))
        return cv

    if ov_key == "s19_gold_headline":
        return _text_line_rgba(main_text[:8], sans_f, 800, 96 if is_giant else 78, (255, 212, 38, 255), stroke_rgba=(14, 16, 22, 255), stroke_w=7, shadow_rgba=(0, 0, 0, 195), shadow_dy=5)

    if ov_key == "s20_crimson_block_serif":
        sp = _text_line_rgba(main_text[:8], serif_f, 640, 68 if is_giant else 58, (255, 253, 246, 255), shadow_rgba=(0, 0, 0, 160), shadow_dy=3)
        if sp is None:
            return None
        bw, bh = sp.width + 48, sp.height + 26
        cv = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(cv)
        d.rounded_rectangle([(0, 0), (bw - 1, bh - 1)], radius=8, fill=(166, 28, 28, 242))
        cv.alpha_composite(sp, dest=((bw - sp.width) // 2, (bh - sp.height) // 2))
        return cv

    if ov_key == "s21_lime_yellow_annotation":
        sp = _text_line_rgba(main_text[:8], sans_f, 760, 86 if is_giant else 72, (217, 249, 77, 255), stroke_rgba=(14, 18, 12, 255), stroke_w=7, shadow_rgba=(0, 0, 0, 190), shadow_dy=5)
        if sp is None:
            return None
        cv = Image.new("RGBA", (sp.width + 20, sp.height + 18), (0, 0, 0, 0))
        cv.alpha_composite(sp, dest=(10, 4))
        ImageDraw.Draw(cv).line([(14, sp.height + 10), (sp.width + 6, sp.height + 10)], fill=(255, 255, 255, 230), width=4)
        return cv

    if ov_key == "s22_red_callout":
        return _text_line_rgba(main_text[:8], sans_f, 780, 96 if is_giant else 76, (220, 38, 38, 255), stroke_rgba=(255, 255, 255, 250), stroke_w=6, shadow_rgba=(0, 0, 0, 195), shadow_dy=5)

    if ov_key == "s23_warm_serif_caption":
        return _text_line_rgba(main_text[:10], serif_f, 720, 68 if is_giant else 54, (253, 230, 138, 255), stroke_rgba=(24, 20, 16, 210), stroke_w=2, shadow_rgba=(0, 0, 0, 165), shadow_dy=3, spacing_px=4.0)

    if ov_key == "s24_vertical_yellow_punch":
        # 复刻 style_24 醒目竖排大黄字（「耗油」）或双字粗黑边亮黄字
        chars = main_text[:4]
        if len(chars) <= 3:
            sp_list = [
                _text_line_rgba(ch, oblique_f, 220, 108, (255, 225, 53, 255), stroke_rgba=(12, 14, 20, 255), stroke_w=8, shadow_rgba=(0, 0, 0, 200), shadow_dy=5)
                for ch in chars
            ]
            sp_list = [s for s in sp_list if s is not None]
            if not sp_list:
                return None
            w = max(s.width for s in sp_list) + 20
            h = sum(s.height for s in sp_list) + 12 * len(sp_list)
            cv = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            y = 6
            for s in sp_list:
                cv.alpha_composite(s, dest=((w - s.width) // 2, y))
                y += s.height + 8
            return cv
        return _text_line_rgba(main_text[:7], oblique_f, 780, 94, (255, 225, 53, 255), stroke_rgba=(12, 14, 20, 255), stroke_w=8, shadow_rgba=(0, 0, 0, 200), shadow_dy=5)

    return None


# ══════════════════════════════════════════════════════════════════════════════
# 组 B · 镜面合成原语（PIL 高精度画布 + 单条 FFmpeg 实时合成）
# ══════════════════════════════════════════════════════════════════════════════

def _cover_crop_vf(w: int, h: int, face_bias: bool = True) -> str:
    w_e = max(2, int(w) & ~1)
    h_e = max(2, int(h) & ~1)
    y_expr = "(ih-oh)*0.22" if face_bias else "(ih-oh)/2"
    return f"scale={w_e}:{h_e}:force_original_aspect_ratio=increase:flags=lanczos,crop={w_e}:{h_e}:(iw-ow)/2:{y_expr},setsar=1"


def _circle_mask_png(cache_dir: Path, radius: int) -> Path:
    r = max(24, int(radius))
    p = cache_dir / f"_v10_cmask_{r}.png"
    if p.is_file():
        return p
    d = r * 2
    hi = Image.new("L", (d * 2, d * 2), 0)
    ImageDraw.Draw(hi).ellipse([(2, 2), (d * 2 - 3, d * 2 - 3)], fill=255)
    hi.resize((d, d), Image.Resampling.LANCZOS).save(p)
    return p


def _rounded_mask_png(cache_dir: Path, w: int, h: int, radius: int) -> Path:
    w, h, r = max(32, int(w) & ~1), max(32, int(h) & ~1), max(8, int(radius))
    p = cache_dir / f"_v10_rmask_{w}x{h}_r{r}.png"
    if p.is_file():
        return p
    hi = Image.new("L", (w * 2, h * 2), 0)
    ImageDraw.Draw(hi).rounded_rectangle([(2, 2), (w * 2 - 3, h * 2 - 3)], radius=r * 2, fill=255)
    hi.resize((w, h), Image.Resampling.LANCZOS).save(p)
    return p


def _make_charcoal_bg(bg_hex: str = "#16181F") -> Image.Image:
    """深炭墨细腻径向暗晕底图（平均亮度 > 0.085，绝不触发 blackdetect 黑屏误报）。"""
    base = hex_to_rgba(bg_hex, 255)[:3]
    # 保证基础亮度脱离 0.07 黑屏门槛（通道均值至少 >= 24）
    base = tuple(max(22, c) for c in base)
    im = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (*base, 255))
    glow = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([(120, 260), (960, 1340)], fill=(min(255, base[0] + 22), min(255, base[1] + 24), min(255, base[2] + 30), 115))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=140))
    im.alpha_composite(glow)
    return im


def _paste_centered_lines(
    canvas: Image.Image,
    lines: List[str],
    y_start: int,
    font_path: Optional[str],
    font_size: int,
    fill_rgba: Tuple[int, int, int, int],
    *,
    max_w: int = 860,
    line_gap: int = 18,
    stroke_rgba: Optional[Tuple[int, int, int, int]] = None,
    stroke_w: int = 0,
    left_x: Optional[int] = None,
) -> int:
    y = y_start
    for ln in lines:
        sp = _text_line_rgba(
            ln, font_path, max_w, font_size, fill_rgba,
            stroke_rgba=stroke_rgba, stroke_w=stroke_w,
            shadow_rgba=(0, 0, 0, 175), shadow_dy=4,
        )
        if sp is None:
            continue
        x = left_x if left_x is not None else (CANVAS_WIDTH - sp.width) // 2
        canvas.alpha_composite(sp, dest=(max(36, x), y))
        y += sp.height + line_gap
    return y


def render_person_behind_giant_v10_png(
    text: str,
    out_png: Path,
    *,
    accent_hex: str = "#FFD426",
    stroke_hex: str = "#141418",
    y_center: int = 445,
    frame_black: bool = False,
    font_candidates: Optional[List[str]] = None,
) -> Path:
    """升级版人后巨字透明图（1080x1920）：支持红/黄/白/灰巨字、可控高度、无截断自适应换行及 style_21 黑边相框。"""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    clean_t = normalize_display_glyphs(str(text or "核心真相").strip()) or "核心真相"
    fnt = _load_font_role("sans", font_candidates)
    fg_rgba = hex_to_rgba(accent_hex, 255)
    st_rgba = hex_to_rgba(stroke_hex, 230)
    cv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    if frame_black:
        d = ImageDraw.Draw(cv)
        d.rectangle([(0, 0), (CANVAS_WIDTH - 1, 210)], fill=(18, 20, 26, 230))
        d.rectangle([(0, 1520), (CANVAS_WIDTH - 1, CANVAS_HEIGHT - 1)], fill=(18, 20, 26, 230))
    if len(clean_t) <= 6:
        req_fs = 250 if len(clean_t) <= 4 else 184
        sp = _text_line_rgba(
            clean_t, fnt, CANVAS_WIDTH - 56, req_fs, fg_rgba,
            stroke_rgba=st_rgba, stroke_w=6, shadow_rgba=(0, 0, 0, 185), shadow_dy=10, min_fs=112,
        )
        if sp is not None:
            y_pos = max(160, min(920 - sp.height, y_center - sp.height // 2))
            cv.alpha_composite(sp, dest=((CANVAS_WIDTH - sp.width) // 2, y_pos))
    else:
        max_line_chars = max(5, (len(clean_t) + 1) // 2)
        wrapped = _wrap_lines(clean_t, max_chars_per_line=max_line_chars, max_lines=2) or [clean_t]
        sprites = [
            _text_line_rgba(
                ln, fnt, CANVAS_WIDTH - 64, 156, fg_rgba,
                stroke_rgba=st_rgba, stroke_w=6, shadow_rgba=(0, 0, 0, 185), shadow_dy=8, min_fs=92,
            )
            for ln in wrapped[:2]
        ]
        sprites = [s for s in sprites if s is not None]
        if sprites:
            gap = 18
            tot_h = sum(s.height for s in sprites) + gap * (len(sprites) - 1)
            cur_y = max(170, min(980 - tot_h, y_center - tot_h // 2))
            for sp in sprites:
                cv.alpha_composite(sp, dest=((CANVAS_WIDTH - sp.width) // 2, cur_y))
                cur_y += sp.height + gap
    cv.save(out_png)
    return out_png


def render_cutout_scene_bg_png(
    pip_cfg: Dict[str, Any],
    out_png: Path,
) -> Path:
    """为纯黑抠像（style_11）或剪纸白边人像（style_03/style_23）生成 1080x1920 背景/文字层 PNG。"""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    mode = str(pip_cfg.get("mode") or "")
    bg_mode = str(pip_cfg.get("bg_mode") or "")
    quote_txt = str(pip_cfg.get("quote_text") or pip_cfg.get("giant_back_text") or "核心要点拆解").strip()
    sans_f = _load_font_role("sans")
    serif_f = _load_font_role("serif")

    if mode == "pure_black_cutout_pip":
        cv = _make_charcoal_bg(str(pip_cfg.get("bg_hex") or "#15171E"))
        lines = _wrap_lines(quote_txt, max_chars_per_line=10, max_lines=2)
        t_col = hex_to_rgba(str(pip_cfg.get("title_hex") or "#FDE68A"), 255)
        _paste_centered_lines(cv, lines, 210, sans_f, 64, t_col, stroke_rgba=(10, 10, 14, 240), stroke_w=3)
        cv.save(out_png)
        return out_png

    if bg_mode == "dark_charcoal":
        # style_03：深炭墨底 + 左上白色打字机引语（避开顶部常驻黄标底边 y=380，留右侧给白描边剪纸人物）
        cv = _make_charcoal_bg(str(pip_cfg.get("bg_hex") or "#16181F"))
        lines = _wrap_lines(quote_txt, max_chars_per_line=11, max_lines=3)
        _paste_centered_lines(cv, lines, 430, serif_f, 44, (248, 250, 252, 250), max_w=640, left_x=74, line_gap=18)
        cv.save(out_png)
        return out_png

    if bg_mode == "blur_poem_left":
        # style_23：透明文字层（叠在虚化暗底视频上），左侧暖黄诗句排版
        cv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        lines = _wrap_lines(quote_txt, max_chars_per_line=10, max_lines=5)
        _paste_centered_lines(
            cv, lines, 380, serif_f, 48, hex_to_rgba(str(pip_cfg.get("poem_hex") or "#FDE68A"), 255),
            max_w=620, left_x=86, line_gap=24, stroke_rgba=(20, 16, 12, 180), stroke_w=2,
        )
        cv.save(out_png)
        return out_png

    cv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    cv.save(out_png)
    return out_png


def build_truth_scene_panel_cmd(
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
    """
    渲染 V10.0 真母本五大镜面面板原语：
    - dark_circle_panel_pip (style_07/09/13/14/19/20/22/23)
    - dark_typewriter_card_pip (style_03/14/16/18/20)
    - framed_box_pip (style_04/13/17/24)
    - review_whiteboard_pip (style_24)
    - pinyin_hero_pip (style_15)
    """
    mode = str(pip_layout.get("mode") or "").strip()
    if mode not in {
        "dark_circle_panel_pip",
        "dark_typewriter_card_pip",
        "framed_box_pip",
        "review_whiteboard_pip",
        "pinyin_hero_pip",
        "paper_outline_pip",
        "concept_diagram_pip",
    }:
        return None

    work = out_mp4.parent
    stem = out_mp4.stem
    tail_str = ",".join(BT709_VF_SUFFIX)
    variant = str(pip_layout.get("variant") or "")
    bg_hex = str(pip_layout.get("bg_hex") or "#15161C")
    accent_hex = str(pip_layout.get("accent_hex") or "#FFE14D")
    sub_hex = str(pip_layout.get("sub_hex") or "#FFFFFF")
    title_txt = str(pip_layout.get("panel_title") or pip_layout.get("giant_back_text") or "核心要点").strip()
    quote_txt = str(pip_layout.get("quote_text") or title_txt).strip()
    sans_f = _load_font_role("sans")
    serif_f = _load_font_role("serif")
    oblique_f = _load_font_role("oblique")
    sec_media = str(pip_layout.get("secondary_media_path") or "")
    sec_in = max(0.0, float(pip_layout.get("secondary_in_sec") or 0.0))
    has_sec = bool(sec_media and Path(sec_media).is_file() and str(Path(sec_media).resolve()) != str(Path(media_path).resolve()))
    clip_dur = max(0.2, clip_frames / float(max(1, fps)))

    # ── 1) 圆窗黑屏/虚化看板（8 套母本风格共用一套干净管线） ─────────────────
    if mode == "dark_circle_panel_pip":
        is_bottom_left = variant.startswith("bottom_left")
        is_blur_bg = variant in ("top_blur_qmark", "top_blur_gold")
        if is_bottom_left:
            cx, cy, cr = 205, 1085, 112
        else:
            cx = int(pip_layout.get("circle_cx") or 540)
            cy = int(pip_layout.get("circle_cy") or 520)
            cr = int(pip_layout.get("circle_r") or 225)
        diam = cr * 2
        cmask_p = _circle_mask_png(work, cr)

        ov_png = work / f"{stem}_v10_circle_ov.png"
        cv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0)) if is_blur_bg else _make_charcoal_bg(bg_hex)
        d = ImageDraw.Draw(cv)
        # 圆窗外细环
        ring_col = hex_to_rgba(accent_hex if variant != "top_red_pain" else "#E5E7EB", 215)
        d.ellipse([(cx - cr - 6, cy - cr - 6), (cx + cr + 6, cy + cr + 6)], outline=ring_col, width=5)

        if variant == "top_blur_qmark":
            # style_13：绕圆窗上方散布白色问号 ? ? ? ? + 下方白黄双行提示
            qf = get_cached_font(sans_f, 74)
            for ang_deg, q_rot in [(-140, -28), (-112, -12), (-68, 12), (-40, 28)]:
                rad = math.radians(ang_deg)
                qx = int(cx + (cr + 78) * math.cos(rad))
                qy = int(cy + (cr + 78) * math.sin(rad))
                qsp = _text_line_rgba("?", sans_f, 120, 76, (255, 255, 255, 250), stroke_rgba=(16, 18, 24, 240), stroke_w=4)
                if qsp is not None:
                    qsp = qsp.rotate(-q_rot, resample=Image.Resampling.BICUBIC, expand=True)
                    cv.alpha_composite(qsp, dest=(qx - qsp.width // 2, qy - qsp.height // 2))
            l1, l2 = _split_two(quote_txt, "", max_first=9)
            _paste_centered_lines(cv, [l1], cy + cr + 56, serif_f, 54, (255, 255, 255, 255), stroke_rgba=(12, 12, 16, 220), stroke_w=3)
            if l2:
                _paste_centered_lines(cv, [l2], cy + cr + 132, serif_f, 60, hex_to_rgba(accent_hex, 255), stroke_rgba=(12, 12, 16, 220), stroke_w=3)
        elif variant in ("top_red_pain", "top_large_gold", "top_guofeng_gold", "top_blur_gold"):
            use_f = serif_f if variant in ("top_guofeng_gold", "top_blur_gold", "top_red_pain") else sans_f
            l1, l2 = _split_two(quote_txt, "", max_first=8)
            eff_acc = "#FF6B6B" if (variant == "top_red_pain" and accent_hex.upper() in ("#C92A2A", "#E03131")) else accent_hex
            eff_sub = "#FF8787" if (variant == "top_red_pain" and sub_hex.upper() in ("#C92A2A", "#E03131")) else sub_hex
            c1 = hex_to_rgba(eff_acc, 255) if variant in ("top_red_pain", "top_guofeng_gold", "top_blur_gold") else (255, 255, 255, 255)
            c2 = hex_to_rgba(eff_sub, 255) if variant == "top_red_pain" else hex_to_rgba(eff_acc, 255)
            _paste_centered_lines(cv, [l1], cy + cr + 62, use_f, 68, c1, stroke_rgba=(12, 10, 10, 220), stroke_w=3)
            if l2:
                _paste_centered_lines(cv, [l2], cy + cr + 150, use_f, 70, c2, stroke_rgba=(12, 10, 10, 220), stroke_w=3)
        else:
            # 左下圆窗系列：style_09 (bottom_left_doc), style_19 (bottom_left_red_quote), style_22 (bottom_left_yellow_quote)
            if variant == "bottom_left_red_quote":
                # style_19：红底引号角标 + 上半区大段白黄法条/观点
                d.rounded_rectangle([(84, 240), (146, 294)], radius=8, fill=(220, 38, 38, 245))
                q1 = _text_line_rgba("“", sans_f, 80, 62, (255, 255, 255, 255))
                if q1:
                    cv.alpha_composite(q1, dest=(96, 242))
                lines = _wrap_lines(quote_txt, max_chars_per_line=15, max_lines=4)
                y_end = _paste_centered_lines(cv, lines, 326, sans_f, 44, (248, 250, 252, 255), max_w=860, left_x=104, line_gap=20)
                d.rounded_rectangle([(910, max(540, y_end + 8)), (972, max(540, y_end + 8) + 54)], radius=8, fill=(220, 38, 38, 245))
            elif variant == "bottom_left_yellow_quote":
                # style_22 / style_09 金句版：大白双引号 + 上半区亮黄粗体警示段
                q1 = _text_line_rgba("“", serif_f, 100, 92, (245, 245, 248, 240))
                if q1:
                    cv.alpha_composite(q1, dest=(92, 248))
                lines = _wrap_lines(quote_txt, max_chars_per_line=14, max_lines=4)
                y_end = _paste_centered_lines(cv, lines, 344, serif_f, 48, hex_to_rgba(accent_hex, 255), max_w=840, left_x=128, line_gap=22)
                q2 = _text_line_rgba("”", serif_f, 100, 92, (245, 245, 248, 240))
                if q2:
                    cv.alpha_composite(q2, dest=(888, max(540, y_end - 12)))
            else:
                # style_09 纪实面板：顶部双格细金线框（有副镜时嵌实拍，无副镜时放纪实要点档案卡）
                d.rectangle([(76, 250), (1004, 780)], outline=(204, 169, 102, 235), width=4)
                if not has_sec:
                    d.rectangle([(80, 254), (1000, 776)], fill=(24, 26, 34, 240))
                    lines = _wrap_lines(quote_txt, max_chars_per_line=14, max_lines=4)
                    _paste_centered_lines(cv, lines, 340, serif_f, 50, (248, 244, 234, 255), max_w=820, line_gap=24)
            # 圆窗右侧核心短标（不与底部字幕重叠）
            tag_sp = _text_line_rgba(title_txt[:10], sans_f, 580, 44, (248, 248, 250, 245))
            if tag_sp:
                cv.alpha_composite(tag_sp, dest=(cx + cr + 42, cy - tag_sp.height // 2))
            d.line([(cx + cr + 42, cy + 44), (min(960, cx + cr + 380), cy + 44)], fill=ring_col, width=3)

        cv.save(ov_png)

        if is_blur_bg:
            fc = (
                f"[0:v]{base_vf_chain},split=2[b_src][c_src];"
                f"[b_src]boxblur=24:2,eq=brightness=-0.22:saturation=0.72[bg_blur];"
                f"[c_src]{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[c_raw];"
                f"[1:v]format=gray[cm];[c_raw][cm]alphamerge[c_ok];"
                f"[bg_blur][2:v]overlay=0:0:shortest=1[m1];"
                f"[m1][c_ok]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(cmask_p),
                "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
            ]
        elif variant == "bottom_left_doc" and has_sec:
            sec_trim = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS"
            fc = (
                f"[0:v]{base_vf_chain},{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[c_raw];"
                f"[1:v]format=gray[cm];[c_raw][cm]alphamerge[c_ok];"
                f"[3:v]{sec_trim},{_cover_crop_vf(920, 522, face_bias=False)}[ev];"
                f"[2:v][ev]overlay=x=80:y=254:shortest=1[m1];"
                f"[m1][c_ok]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(cmask_p),
                "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
                "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
            ]
        else:
            fc = (
                f"[0:v]{base_vf_chain},{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[c_raw];"
                f"[1:v]format=gray[cm];[c_raw][cm]alphamerge[c_ok];"
                f"[2:v][c_ok]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(cmask_p),
                "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
            ]
        return [
            ffmpeg_bin, "-y", *inputs, "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # ── 2) 炭墨/重虚化打字机与白条引语卡（style_03/14/16/18/20） ─────────────
    if mode == "dark_typewriter_card_pip":
        ov_png = work / f"{stem}_v10_typewriter.png"
        cv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        d = ImageDraw.Draw(cv)
        is_blur = variant in ("s16_blur_dark_typewriter", "s18_blur_white_strip")

        if variant == "s18_blur_white_strip":
            # style_18：重虚化暗底 + 左侧大白字序号 + 白色圆角引号条（暗红/深墨字）
            sp_ord = _text_line_rgba(title_txt[:4], sans_f, 400, 74, (255, 255, 255, 255), stroke_rgba=(12, 12, 16, 240), stroke_w=4, shadow_rgba=(0, 0, 0, 180), shadow_dy=4)
            if sp_ord:
                cv.alpha_composite(sp_ord, dest=(118, 520))
            sp_q = _text_line_rgba(f"“{quote_txt[:14]}”", sans_f, 760, 50, (185, 28, 28, 255))
            if sp_q:
                bw, bh = sp_q.width + 52, sp_q.height + 30
                bx, by = 112, 626
                d.rounded_rectangle([(bx, by), (bx + bw, by + bh)], radius=12, fill=(250, 250, 252, 246))
                cv.alpha_composite(sp_q, dest=(bx + 26, by + 15))
        else:
            # 打字机引语卡（带亮白光标方块，绝不与底部字幕重叠）
            q_display = f"“{quote_txt[:18]}”"
            use_f = serif_f if variant in ("s03_black_typewriter", "s20_black_quote_top") else sans_f
            y_pos = 460 if variant in ("s14_black_quote_top", "s20_black_quote_top") else 560
            sp = _text_line_rgba(q_display, use_f, 820, 56, (248, 250, 252, 255), shadow_rgba=(0, 0, 0, 180), shadow_dy=4)
            if sp:
                tx = (CANVAS_WIDTH - sp.width) // 2 if variant == "s16_blur_dark_typewriter" else 112
                cv.alpha_composite(sp, dest=(tx, y_pos))
                # 复古打字机亮白光标块
                cx0 = min(CANVAS_WIDTH - 90, tx + sp.width + 14)
                d.rectangle([(cx0, y_pos + 6), (cx0 + 26, y_pos + max(42, sp.height - 6))], fill=(255, 255, 255, 245))
        cv.save(ov_png)

        if is_blur:
            bg_fx = "boxblur=28:3,eq=brightness=-0.26:saturation=0.68"
        else:
            # 保留极轻微原视频呼吸纹理 + 炭墨提底（杜绝 blackdetect 与静止检测）
            bg_fx = "boxblur=30:3,eq=brightness=-0.42:contrast=0.55:saturation=0.25,drawbox=x=0:y=0:w=iw:h=ih:color=#16181F@0.86:t=fill"
        fc = f"[0:v]{base_vf_chain},{bg_fx}[bg];[bg][1:v]overlay=0:0:shortest=1,{tail_str}[vout]"
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # ── 3) 黑底圆角方窗 / 双图对比（style_04/13/17/24） ──────────────────────
    if mode == "framed_box_pip":
        ov_png = work / f"{stem}_v10_fbox_bg.png"
        cv = _make_charcoal_bg(bg_hex)
        d = ImageDraw.Draw(cv)

        if variant == "s17_dual_compare_cards":
            # style_17：上下两张圆角实拍对比卡片 + 居中粗黑边白字标签
            fw, fh, rad = 880, 440, 24
            rmask = _rounded_mask_png(work, fw, fh, rad)
            l1, l2 = _split_two(title_txt, quote_txt[:8], max_first=5)
            l2 = l2 or "实拍细节"
            lbl_png = work / f"{stem}_v10_s17_lbl.png"
            lcv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            _paste_centered_lines(lcv, [l1], 410, sans_f, 70, (255, 255, 255, 255), stroke_rgba=(12, 12, 16, 255), stroke_w=6)
            _paste_centered_lines(lcv, [l2], 890, sans_f, 70, (255, 255, 255, 255), stroke_rgba=(12, 12, 16, 255), stroke_w=6)
            lcv.save(lbl_png)
            cv.save(ov_png)
            if has_sec:
                sec_trim = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS"
                fc = (
                    f"[0:v]{base_vf_chain},{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[t_raw];"
                    f"[4:v]{sec_trim},{_cover_crop_vf(fw, fh, face_bias=False)},format=rgba[b_raw];"
                    f"[1:v]format=gray,split=2[m1][m2];"
                    f"[t_raw][m1]alphamerge[t_ok];[b_raw][m2]alphamerge[b_ok];"
                    f"[2:v][t_ok]overlay=x=100:y=220:shortest=1[s1];"
                    f"[s1][b_ok]overlay=x=100:y=700:shortest=1[s2];"
                    f"[s2][3:v]overlay=0:0:shortest=1,{tail_str}[vout]"
                )
                inputs = [
                    "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                    "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
                    "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
                    "-loop", "1", "-framerate", str(fps), "-i", str(lbl_png),
                    "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
                ]
            else:
                # 纯口播源时：上为标准景、下为微推近局部，依然呈现高级双卡对比构图
                fc = (
                    f"[0:v]{base_vf_chain},split=2[s_a][s_b];"
                    f"[s_a]{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[t_raw];"
                    f"[s_b]scale=iw*1.18:ih*1.18,crop=iw/1.18:ih/1.18,{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[b_raw];"
                    f"[1:v]format=gray,split=2[m1][m2];"
                    f"[t_raw][m1]alphamerge[t_ok];[b_raw][m2]alphamerge[b_ok];"
                    f"[2:v][t_ok]overlay=x=100:y=220:shortest=1[s1];"
                    f"[s1][b_ok]overlay=x=100:y=700:shortest=1[s2];"
                    f"[s2][3:v]overlay=0:0:shortest=1,{tail_str}[vout]"
                )
                inputs = [
                    "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                    "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
                    "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
                    "-loop", "1", "-framerate", str(fps), "-i", str(lbl_png),
                ]
            return [
                ffmpeg_bin, "-y", *inputs, "-an", "-filter_complex", fc, "-map", "[vout]",
                "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
            ]

        # 单圆角方窗变体（s04_dual_yellow_header / s04_focus_giant_word / s13_red_warn_box / s17_black_top_square / s24_rounded_impact）
        if variant == "s04_dual_yellow_header":
            fw, fh, fx, fy, rad = 820, 820, 130, 410, 38
            hdr_lines = _wrap_lines(title_txt, max_chars_per_line=7, max_lines=2) if len(title_txt) > 7 else [title_txt]
            hdr_fs = 102 if len(hdr_lines) == 1 and len(title_txt) <= 6 else 74
            _paste_centered_lines(cv, hdr_lines, 200 if len(hdr_lines) > 1 else 235, serif_f, hdr_fs, hex_to_rgba(accent_hex, 255), stroke_rgba=(12, 12, 16, 255), stroke_w=5, line_gap=10)
        elif variant == "s04_focus_giant_word":
            fw, fh, fx, fy, rad = 680, 680, 200, 210, 36
            clean_title = normalize_display_glyphs(re.sub(r"\s+", "", title_txt)) or "核心真相"
            if len(clean_title) <= 6:
                f_lines = [clean_title]
            else:
                max_c = max(6, (len(clean_title) + 1) // 2)
                f_lines = _wrap_lines(clean_title, max_chars_per_line=max_c, max_lines=2)
                if len("".join(f_lines)) < len(clean_title):
                    mid = (len(clean_title) + 1) // 2
                    f_lines = [clean_title[:mid], clean_title[mid:]]
            max_line_c = max((len(ln) for ln in f_lines), default=len(clean_title))
            if len(f_lines) == 1:
                _fs4 = 112 if max_line_c <= 4 else (98 if max_line_c <= 6 else 78)
            else:
                _fs4 = 62 if max_line_c <= 7 else (56 if max_line_c <= 9 else 48)
            frame_bot = fy + fh + 6  # 896px：严禁与上方圆角方窗底部重叠
            max_band_h = 1055 - frame_bot  # 159px：严格落在 y < 1056 安全带内
            gap = 8
            line_sprites: List[Image.Image] = []
            while _fs4 >= 32:
                cand = [
                    sp for sp in (
                        _text_line_rgba(
                            ln, sans_f, 920, _fs4, (255, 255, 255, 255),
                            shadow_rgba=(0, 0, 0, 175), shadow_dy=4,
                        )
                        for ln in f_lines[:2]
                    )
                    if sp is not None
                ]
                if not cand:
                    break
                tot_h = sum(sp.height for sp in cand) + gap * (len(cand) - 1)
                if tot_h <= max_band_h or _fs4 <= 32:
                    line_sprites = cand
                    break
                _fs4 -= 4
            if line_sprites:
                tot_h = sum(sp.height for sp in line_sprites) + gap * (len(line_sprites) - 1)
                cur_y = max(frame_bot, min(1055 - tot_h, 974 - tot_h // 2))
                for sp in line_sprites:
                    cv.alpha_composite(sp, dest=((CANVAS_WIDTH - sp.width) // 2, cur_y))
                    cur_y += sp.height + gap
        elif variant == "s13_red_warn_box":
            fw, fh, fx, fy, rad = 820, 740, 130, 440, 24
            _paste_centered_lines(cv, ["一定要警惕"], 205, serif_f, 44, (255, 255, 255, 250), left_x=134)
            w_lines = _wrap_lines(title_txt, max_chars_per_line=6, max_lines=2) if len(title_txt) > 7 else [title_txt]
            _paste_centered_lines(cv, w_lines, 268, serif_f, 108 if len(w_lines) == 1 else 74, (224, 49, 49, 255), stroke_rgba=(20, 6, 6, 240), stroke_w=4, left_x=130, line_gap=8)
            d.rounded_rectangle([(fx - 4, fy - 4), (fx + fw + 4, fy + fh + 4)], radius=rad + 4, outline=(245, 245, 248, 225), width=4)
        elif variant == "s24_rounded_impact":
            fw, fh, fx, fy, rad = 920, 640, 80, 340, 36
        else:
            # s17_black_top_square
            fw, fh, fx, fy, rad = 860, 760, 110, 260, 20
            d.rounded_rectangle([(fx - 3, fy - 3), (fx + fw + 3, fy + fh + 3)], radius=rad + 3, outline=(245, 198, 72, 220), width=3)

        cv.save(ov_png)
        rmask = _rounded_mask_png(work, fw, fh, rad)
        if variant == "s24_rounded_impact":
            fg_png = work / f"{stem}_v10_s24_fg.png"
            fcv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            s24_lines = _wrap_lines(title_txt, max_chars_per_line=7, max_lines=2) if len(title_txt) > 7 else [title_txt]
            _paste_centered_lines(fcv, s24_lines, 610, oblique_f, 118 if len(s24_lines) == 1 else 84, (255, 225, 53, 255), stroke_rgba=(28, 78, 166, 255), stroke_w=10, line_gap=12)
            fcv.save(fg_png)
            fc = (
                f"[0:v]{base_vf_chain},{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[f_raw];"
                f"[1:v]format=gray[rm];[f_raw][rm]alphamerge[f_ok];"
                f"[2:v][f_ok]overlay=x={fx}:y={fy}:shortest=1[m1];"
                f"[m1][3:v]overlay=0:0:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
                "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
                "-loop", "1", "-framerate", str(fps), "-i", str(fg_png),
            ]
        else:
            fc = (
                f"[0:v]{base_vf_chain},{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[f_raw];"
                f"[1:v]format=gray[rm];[f_raw][rm]alphamerge[f_ok];"
                f"[2:v][f_ok]overlay=x={fx}:y={fy}:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
                "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
            ]
        return [
            ffmpeg_bin, "-y", *inputs, "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # ── 4) style_24 浅灰白底产品多图痛点评测白板 ─────────────────────────────
    if mode == "review_whiteboard_pip":
        wb_png = work / f"{stem}_v10_whiteboard.png"
        cv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (236, 240, 245, 255))
        d = ImageDraw.Draw(cv)
        # 顶部黄蓝大标
        _paste_centered_lines(cv, [title_txt[:8]], 180, oblique_f, 108, (255, 200, 36, 255), stroke_rgba=(24, 52, 118, 255), stroke_w=8)
        # 下方评测要点卡片条
        pts = _wrap_lines(quote_txt, max_chars_per_line=10, max_lines=2)
        y_pt = 900
        for pt in pts:
            d.rounded_rectangle([(120, y_pt), (960, y_pt + 110)], radius=20, fill=(255, 255, 255, 250), outline=(210, 216, 226, 255), width=3)
            d.ellipse([(148, y_pt + 32), (194, y_pt + 78)], fill=(245, 158, 11, 255))
            sp_pt = _text_line_rgba(pt, oblique_f, 700, 52, (28, 34, 46, 255))
            if sp_pt:
                cv.alpha_composite(sp_pt, dest=(224, y_pt + (110 - sp_pt.height) // 2))
            y_pt += 132
        cv.save(wb_png)

        fw, fh, rad = 860, 520, 28
        rmask = _rounded_mask_png(work, fw, fh, rad)
        fc = (
            f"[0:v]{base_vf_chain},{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[p_raw];"
            f"[1:v]format=gray[rm];[p_raw][rm]alphamerge[p_ok];"
            f"[2:v][p_ok]overlay=x=110:y=330:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
            "-loop", "1", "-framerate", str(fps), "-i", str(wb_png),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # ── 5) style_15 电影感开场竖条切屏 + 红色拼音注音大白字 ──────────────────
    if mode == "pinyin_hero_pip":
        py_png = work / f"{stem}_v10_pinyin.png"
        cv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        d = ImageDraw.Draw(cv)
        # 复刻 style_15 开场宽影院微暗水平聚焦带（置于胸口区 y=900..1150，绝不遮挡主播脸部与下巴）
        d.rectangle([(0, 900), (CANVAS_WIDTH - 1, 1150)], fill=(12, 14, 18, 136))
        chars = re.sub(r"[^\u4e00-\u9fff]", "", title_txt)[:4] or "核心真相"
        pys = _pinyin_for_chars(chars)
        n = max(1, len(chars))
        slot_w = min(210, 860 // n)
        x0 = (CANVAS_WIDTH - slot_w * n) // 2
        for i, ch in enumerate(chars):
            cx_i = x0 + i * slot_w + slot_w // 2
            py_str = pys[i] if i < len(pys) else ""
            if py_str:
                sp_py = _text_line_rgba(py_str, sans_f, slot_w - 8, 44, (239, 68, 68, 255), stroke_rgba=(16, 8, 8, 240), stroke_w=3)
                if sp_py:
                    cv.alpha_composite(sp_py, dest=(cx_i - sp_py.width // 2, 922))
            sp_ch = _text_line_rgba(ch, sans_f, slot_w - 8, 124, (255, 255, 255, 255), stroke_rgba=(10, 12, 16, 250), stroke_w=5, shadow_rgba=(0, 0, 0, 190), shadow_dy=6)
            if sp_ch:
                cv.alpha_composite(sp_ch, dest=(cx_i - sp_ch.width // 2, 988))
        cv.save(py_png)
        fc = f"[0:v]{base_vf_chain}[v0];[v0][1:v]overlay=0:0:shortest=1,{tail_str}[vout]"
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(py_png),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # ── 6) style_15 纸底大纲清单白板 & 商业概念图解卡（无 Chromium 时轻量 PIL 兜底）──
    if mode in ("paper_outline_pip", "concept_diagram_pip"):
        ov_png = work / f"{stem}_v11_{mode}.png"
        is_light = mode == "paper_outline_pip" or bool(pip_layout.get("light_theme"))
        cv = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (244, 242, 236, 255)) if is_light else _make_charcoal_bg(bg_hex)
        fg_c = (24, 26, 32, 255) if is_light else (248, 250, 252, 255)
        acc_c = hex_to_rgba(accent_hex, 255)
        _paste_centered_lines(cv, [title_txt[:10]], 350 if mode == "paper_outline_pip" else 280, sans_f, 76, fg_c if is_light else acc_c)
        pts = _wrap_lines(quote_txt, max_chars_per_line=12, max_lines=3)
        _paste_centered_lines(cv, pts, 560, sans_f, 48, fg_c, line_gap=26)
        cv.save(ov_png)
        fc = f"[0:v]fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS,{tail_str}[vout]"
        return [
            ffmpeg_bin, "-y",
            "-loop", "1", "-framerate", str(fps), "-i", str(ov_png),
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    return None
