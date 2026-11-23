#!/usr/bin/env python3
"""24 套复刻画面接触表：把「字幕 + 花字」按库实测参数渲成真实 1080×1920 画面，供人眼核对。

用途：不是出片，是让人一眼看出每套的字号/字重/描边/底框/位置/花字版式是否与原版一致。
底图用该套源的实测色调（暗/中/高分区色）合成渐变，避免纯色底板误导观感。

输出：reports/style_contact_sheet_<date>_A.jpg / _B.jpg（每张 12 套，2×6 竖排在 2 行）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

from common import ASSETS_DIR, CANVAS_HEIGHT, CANVAS_WIDTH, find_commercial_font, read_json  # noqa: E402
from render_mp4 import parse_hex_rgba  # noqa: E402
from render_template_overlays import render_template_packaging_png  # noqa: E402

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

STYLE_DIR = ASSETS_DIR / "style_spectrum"
OUT_DIR = ROOT / "reports"
CARD_W = 560
TOP_BAND = (0.06, 0.30)      # 花字落区
SUB_BAND_HALF = 0.14         # 字幕带半高（以 safe_y 为中心）
PAD = 26
COLS = 2


def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if path and Path(path).is_file():
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    return ImageFont.load_default()


TARGET_LUMA = 104.0
LUMA_FLOOR, LUMA_CEIL = 72.0, 128.0


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".MP4", ".MOV"}
CACHE = Path("/tmp/style_contact_frames")


def _draft_root() -> Path | None:
    root = Path(os.environ.get("JY_DRAFT_ROOT") or "")
    return root if root.is_dir() else None


def _source_frame(sid: str) -> Image.Image | None:
    """取该套源草稿真实主镜一帧，作为核对底图（有就用真画面，没有才退回渐变）。"""
    root = _draft_root()
    if root is None:
        return None
    cache = CACHE / f"{sid}.jpg"
    if cache.is_file():
        try:
            return Image.open(cache).convert("RGBA")
        except OSError:
            pass
    mapping = json.loads((ROOT / "forensic" / "style_sources.json").read_text(encoding="utf-8"))["styles"]
    name = mapping.get(sid)
    vdir = root / str(name) / "materials" / "video"
    if not vdir.is_dir():
        return None
    pool = sorted((p for p in vdir.iterdir() if p.is_file() and p.suffix in VIDEO_EXT and p.stat().st_size > 200_000),
                  key=lambda p: -p.stat().st_size)
    if not pool:
        return None
    src = pool[0]
    CACHE.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-ss", "2", "-i", str(src), "-frames:v", "1",
           "-vf", f"scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}:force_original_aspect_ratio=increase,"
                  f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}", "-y", str(cache)]
    if subprocess.run(cmd, check=False).returncode != 0 or not cache.is_file():
        return None
    return Image.open(cache).convert("RGBA")


def _bg_from_measured(cal: Dict[str, Any]) -> Image.Image:
    """用源片实测的三段色做竖向渐变，并把整体亮度归一到 ~104。

    归一化是为了让人眼比较「字」本身：白字在 145 亮度的浅底上会糊、彩字在 40 的暗底上会闷，
    统一亮度后各套的字号/字重/描边/底框差异才可比。色相与相对光比保留。
    """
    shadow = list(cal.get("shadow_rgb") or [24, 26, 30])
    mid = list(cal.get("midtone_rgb") or [76, 78, 82])
    high = list(cal.get("highlight_rgb") or [180, 182, 186])
    luma = 0.2126 * mid[0] + 0.7152 * mid[1] + 0.0722 * mid[2]
    if luma > 8:
        # 只把极端亮度往中间夹（暗调风格仍暗、奶油风仍亮），避免统一灰度抹掉风格差异
        target = max(LUMA_FLOOR, min(LUMA_CEIL, luma))
        k = target / luma
        shadow = [min(255.0, v * k) for v in shadow]
        mid = [min(255.0, v * k) for v in mid]
        high = [min(255.0, v * k) for v in high]
    img = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), tuple(int(c) for c in mid))
    draw = ImageDraw.Draw(img)
    for y in range(CANVAS_HEIGHT):
        t = y / (CANVAS_HEIGHT - 1)
        a, b = (shadow, mid) if t < 0.5 else (mid, high)
        k = t * 2 if t < 0.5 else (t - 0.5) * 2
        col = tuple(int(round(a[i] + (b[i] - a[i]) * k)) for i in range(3))
        draw.line([(0, y), (CANVAS_WIDTH, y)], fill=col)
    # 轻噪声，避免纯渐变显得比真画面干净
    return img


def _draw_subtitle(img: Image.Image, text: str, entry: Dict[str, Any], tpl: Dict[str, Any]) -> None:
    sub = entry.get("subtitle_spec") or {}
    px = int(sub.get("font_size_1080p_px") or 58)
    y = int(float(sub.get("safe_y_ratio") or 0.76) * CANVAS_HEIGHT)
    font = _font(find_commercial_font([(sub.get("font_substitution") or {}).get("delivered_file") or "AlimamaShuHeiTi-Bold.otf"]), px)
    # 描边只有草稿真有的才画（24/24 套实测都没有描边，全部是右下偏移的半透明阴影）
    stroke_w = 0 if str(sub.get("stroke_mode") or "none") == "none" else max(1, int(round(int(sub.get("stroke_width") or 0) * 0.5)))
    stroke = str(sub.get("stroke_color") or "#000000")
    sh_on = bool(sub.get("shadow"))
    sh_off = [int(v) for v in (sub.get("shadow_offset") or [0, 0])][:2] or [0, 0]
    sh_blur = float(sub.get("shadow_blur_px") or 0.0)
    sh_rgba = parse_hex_rgba(sub.get("shadow_color") or "#000000C8", 200) if sh_on else (0, 0, 0, 0)
    pri = str(tpl.get("primary_color") or "#FFFFFF")
    kw = str(tpl.get("keyword_color") or "#FFBF17")
    bg_ratio = float(sub.get("bg_ratio_measured") or 0.0)
    bg_color = str(sub.get("bg_box_color") or "#000000")

    layer = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bbox = d.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if bg_ratio > 0.01:
        pad_x, pad_y = 26, 12
        radius = int(min(28, float(sub.get("bg_radius") or 0) * 4))
        d.rounded_rectangle(
            [(CANVAS_WIDTH - tw) // 2 - pad_x, y - th // 2 - pad_y,
             (CANVAS_WIDTH + tw) // 2 + pad_x, y + th // 2 + pad_y],
            radius=radius, fill=bg_color, outline=None,
        )
    # 阴影先画（与真草稿同参数：偏移 / 半透明 / 虚化），正文后画在上面
    tx = (CANVAS_WIDTH - tw) // 2 - bbox[0]
    ty = y - th // 2 - bbox[1]
    if sh_on and (sh_off[0] or sh_off[1] or sh_blur > 0.2):
        sh_layer = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        ImageDraw.Draw(sh_layer).text((tx + sh_off[0], ty + sh_off[1]), text, font=font, fill=sh_rgba)
        if sh_blur > 0.2:
            sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(radius=sh_blur))
        layer.alpha_composite(sh_layer)
        d = ImageDraw.Draw(layer)
    # 单次绘制：底图只用于看字号/字重/描边/阴影/底框，不做关键词二次叠字（避免描边重影）
    d.text((tx, ty), text, font=font, fill=pri, stroke_width=stroke_w, stroke_fill=stroke if stroke_w > 0 else None)
    if img.mode == "RGBA":
        img.alpha_composite(layer)
    else:
        img.paste(layer, (0, 0), layer)


def render_one(sid: str, entry: Dict[str, Any], tpl: Dict[str, Any]) -> Image.Image:
    real = _source_frame(sid)
    base = real if real is not None else _bg_from_measured(entry.get("color_and_lut") or {}).convert("RGBA")
    _draw_subtitle(base, "关键信息一句话说清重点", entry, tpl)
    flower = render_template_packaging_png(
        {"kind": "flower_text", "text_or_asset": "重点一句", "subtext": "标注"}, style_id=sid
    )
    if flower is not None:
        fw = min(flower.width, int(CANVAS_WIDTH * 0.86))
        scale = fw / flower.width
        flower = flower.resize((fw, max(1, int(flower.height * scale))), Image.LANCZOS)
        base.alpha_composite(flower, ((CANVAS_WIDTH - flower.width) // 2, int(CANVAS_HEIGHT * 0.16)))
    return base.convert("RGB")


def card(img: Image.Image, entry: Dict[str, Any]) -> Image.Image:
    """把整幅画面拆成「花字区 + 字幕带」两张裁片上下拼，放大到能看清字形与描边。"""
    y_ratio = float((entry.get("subtitle_spec") or {}).get("safe_y_ratio") or 0.76)
    top = img.crop((0, int(CANVAS_HEIGHT * TOP_BAND[0]), CANVAS_WIDTH, int(CANVAS_HEIGHT * TOP_BAND[1])))
    y0 = max(0, int(CANVAS_HEIGHT * (y_ratio - SUB_BAND_HALF)))
    y1 = min(CANVAS_HEIGHT, int(CANVAS_HEIGHT * (y_ratio + SUB_BAND_HALF)))
    sub = img.crop((0, y0, CANVAS_WIDTH, y1))
    scale = CARD_W / CANVAS_WIDTH
    top = top.resize((CARD_W, max(1, int(top.height * scale))), Image.LANCZOS)
    sub = sub.resize((CARD_W, max(1, int(sub.height * scale))), Image.LANCZOS)
    out = Image.new("RGB", (CARD_W, top.height + sub.height + 6), (30, 31, 34))
    out.paste(top, (0, 0))
    out.paste(sub, (0, top.height + 6))
    return out


def sheet(cells: List[Tuple[str, str, Image.Image]], title: str) -> Image.Image:
    prepared = [(sid, label, card(img, entry)) for sid, label, img, entry in cells]
    cols = COLS
    rows = max(1, (len(prepared) + cols - 1) // cols)
    cell_h = max(c.height for _, _, c in prepared)
    W = cols * CARD_W + (cols + 1) * PAD
    H = rows * (cell_h + 34) + (rows + 1) * PAD + 44
    out = Image.new("RGB", (W, H), (14, 15, 17))
    d = ImageDraw.Draw(out)
    cap_font = _font(str(next(iter((ASSETS_DIR / "fonts").glob("*.otf")), "")) or None, 18)
    title_font = _font(str(next(iter((ASSETS_DIR / "fonts").glob("*.otf")), "")) or None, 26)
    d.text((PAD, 14), title, font=title_font, fill=(240, 240, 240))
    for i, (sid, label, tile) in enumerate(prepared):
        r, c = divmod(i, cols)
        x = PAD + c * (CARD_W + PAD)
        y = 44 + PAD + r * (cell_h + 34)
        out.paste(tile, (x, y))
        d.text((x, y + tile.height + 8), f"{sid} · {label[:26]}", font=cap_font, fill=(200, 200, 205))
    return out


def main() -> None:
    lib = read_json(STYLE_DIR / "style_spectrum_library.json")
    tpl_doc = read_json(ASSETS_DIR / "subtitle_templates" / "templates.json")
    tpls = tpl_doc.get("templates") or tpl_doc
    cells = []
    for entry in lib["styles"]:
        sid = entry["style_id"]
        img = render_one(sid, entry, tpls.get(sid) or {})
        cells.append((sid, str(entry.get("style_name_cn") or ""), img, entry))
    stamp = datetime.now().strftime("%Y-%m-%d")
    half = (len(cells) + 1) // 2
    outs = []
    for idx, chunk in enumerate((cells[:half], cells[half:])):
        s = sheet(chunk, f"24 套复刻画面核对表 · 第 {'A' if idx == 0 else 'B'} 部分（上：花字区 / 下：字幕带，按库实测参数）")
        p = OUT_DIR / f"style_contact_sheet_{stamp}_{'A' if idx == 0 else 'B'}.jpg"
        s.save(p, quality=92)
        outs.append(p)
        print(f"[contact-sheet] {p}  ({s.width}×{s.height})")


if __name__ == "__main__":
    main()
