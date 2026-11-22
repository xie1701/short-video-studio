#!/usr/bin/env python3
"""每套风格「多特点帧」并排特性图：一屏看清一套风格的构图、字幕、花字、特效、调色。

每行一套风格，5 格依次是：
  ① 源素材原样      ② 真草稿构图（主轨缩放/位移 + 叠加窗 + 抠像描边）
  ③ 库字幕          （字号 / 描边 / 底框 / 关键词色 / 安全区）
  ④ 蓝图花字        （配色 / 版式 / 巨字，全部读 template_blueprints.overlay_style）
  ⑤ 源真值特效      （按 effect 轨时间窗，源里没有就明写「无特效」）

构图格完全按 `forensic/pip_usage.json` 的真几何摆放：主轨 (x, y, scale)、叠加窗 (x, y, scale)、
抠像描边的颜色与粗细；不按风格名字猜。调色格与其它帧一律过一遍该套的调色链（闭环校准后）。

用法：
    JY_DRAFT_ROOT=/path/to/drafts python3 dev_tools/render_style_feature_sheet.py --out reports/style_features_2026-11-23
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from common import find_bin, read_json  # noqa: E402
from step7_color_grade import _build_vf_for_corr, _resolve_style_color  # noqa: E402
from render_template_overlays import render_template_packaging_png  # noqa: E402
from render_mp4 import render_subtitle_png  # noqa: E402

ASSETS = ROOT / "short-video-studio" / "assets"
FORENSIC = ROOT / "forensic"
W, H = 1080, 1920
CELL_W, CELL_H = 396, 704
FULL_W = 396          # 整幅格子的宽度
WAIST_W = 620         # 字幕/花字裁片的宽度（接近原尺寸，字才看得清）
CROP_W = 396          # 裁片格子的宽度（字幕带 / 花字区，字放大到能看清）
SAMPLE_TEXT = "老板最该想清楚的一件事"
SAMPLE_SUB = "这条街三个月关了一半的店"
SAMPLE_KW = "三个月"

DRAFT_ROOT = Path(os.environ.get("JY_DRAFT_ROOT") or "")
CACHE = Path("/tmp/style_feature_frames")


def _ff() -> Optional[str]:
    return find_bin("ffmpeg")


def _extract_frame(video: Path, out: Path) -> Optional[Path]:
    ff = _ff()
    if not ff or not video.is_file():
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [ff, "-v", "error", "-ss", "2", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", "-y", str(out)],
        capture_output=True)
    return out if r.returncode == 0 and out.is_file() else None


def _draft(sid: str, sources: Dict[str, str]) -> Optional[dict]:
    for name in ("draft_content.json", "__latest_close.json", "template-2.tmp"):
        p = FORENSIC / "decrypted" / sources[sid] / name
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def _usage() -> Dict[str, Any]:
    return read_json(FORENSIC / "pip_usage.json")


def _source_frames(sid: str, sources: Dict[str, str], usage: Dict[str, Any]) -> Tuple[Optional[Path], List[Path]]:
    """主轨一段 + 叠加轨若干段的真实画面（缓存到 /tmp）。

    三级回退：草稿素材 → 先前接触表用过的同源帧 → 按该套实测三段色合成的渐变；
    绝不返回 None 让整行变黑。
    """
    cached = CACHE / sid
    main_c = cached / "main.jpg"
    if main_c.is_file():
        return main_c, sorted(cached.glob("ov_*.jpg"))

    contact = Path("/tmp/style_contact_frames") / f"{sid}.jpg"
    fb_main = contact if contact.is_file() else _synth_frame(sid)
    draft = _draft(sid, sources)
    if draft is None or not DRAFT_ROOT.is_dir():
        return fb_main, []
    videos = {m["id"]: m for m in (draft.get("materials", {}).get("videos") or [])}
    tracks = [t for t in (draft.get("tracks") or []) if t.get("type") == "video"]
    picks: List[Path] = []
    for track in tracks:
        best: Optional[Tuple[float, Path]] = None
        for seg in track.get("segments") or []:
            mat = videos.get(seg.get("material_id")) or {}
            path = str(mat.get("path") or "")
            if not path:
                continue
            base = Path(path.replace("\\", "/")).name
            if not base:
                continue
            cand = DRAFT_ROOT / sources[sid] / "materials" / "video" / base
            if cand.is_file():
                dur = float((seg.get("target_timerange") or {}).get("duration") or 0)
                if best is None or dur > best[0]:
                    best = (dur, cand)
        if best:
            picks.append(best[1])
    if not picks:
        return fb_main, []
    main_p = _extract_frame(picks[0], main_c) or fb_main
    extras: List[Path] = []
    for k, vid in enumerate(picks[1:4]):
        got = _extract_frame(vid, cached / f"ov_{k}.jpg")
        if got:
            extras.append(got)
    return main_p, extras


def _fit(img: Image.Image) -> Image.Image:
    return img if img.size == (W, H) else img.resize((W, H), Image.LANCZOS)


def _place(canvas: Image.Image, img: Image.Image, x: float, y: float, scale: float,
           border: Optional[Tuple[Tuple[int, int, int], int]] = None) -> None:
    """把 img 按草稿几何贴到画布：中心 = (W(1+x)/2, H(1+y)/2)，尺寸 = (W·scale, H·scale)。"""
    w = max(8, int(round(W * scale)))
    h = max(8, int(round(H * scale)))
    im = img.resize((w, h), Image.LANCZOS)
    cx = int(round(W * (1.0 + x) / 2.0))
    cy = int(round(H * (1.0 + y) / 2.0))
    px, py = cx - w // 2, cy - h // 2
    if border:
        color, width = border
        layer = Image.new("RGB", (w + width * 2, h + width * 2), color)
        layer.paste(im, (width, width))
        im = layer
        px -= width
        py -= width
    canvas.paste(im, (px, py))


def _compose(sid: str, usage: Dict[str, Any], main_p: Optional[Path], extras: List[Path]) -> Image.Image:
    node = usage["styles"][sid]
    canvas = Image.new("RGB", (W, H), (6, 6, 8))
    if main_p is None:
        d = ImageDraw.Draw(canvas)
        d.text((40, 40), f"{sid} 无本机源帧", fill=(230, 230, 230))
        return canvas
    main_img = Image.open(main_p).convert("RGB")
    m = node["main"]
    scale = float(m.get("scale") or 1.0)
    if scale > 1.0:
        # 推近：先整体放大再中心裁回画布，等价于剪映里 scale>1 的推镜
        big = main_img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
        ox = (big.width - W) // 2
        oy = (big.height - H) // 2
        main_img = big.crop((ox, oy, ox + W, oy + H))
        _place(canvas, main_img, 0.0, 0.0, 1.0)
    else:
        _place(canvas, main_img, float(m.get("x") or 0.0), float(m.get("y") or 0.0), scale)

    # 叠加窗取「所有出现过的非满屏几何」按出现段数排序——只看主导几何会把只在少数段出现的小窗漏掉
    seen: Dict[Tuple[float, float, float], Dict[str, Any]] = {}
    for ov in node["overlays"]:
        for g in ov.get("geometries") or []:
            key = (g["x"], g["y"], g["scale"])
            full = g["scale"] >= 0.999 and abs(g["x"]) < 0.02 and abs(g["y"]) < 0.02
            if full:
                continue
            if key not in seen:
                seen[key] = {"geom": g, "count": 0, "ov": ov}
            seen[key]["count"] += 1
    windows = sorted(seen.values(), key=lambda w: -w["count"])
    for k, win in enumerate(windows[:3]):
        ov, dom = win["ov"], win["geom"]
        src = extras[k] if k < len(extras) else (extras[0] if extras else None)
        if src is None:
            continue
        border = None
        stroke = ov.get("matting_stroke") or {}
        color = stroke.get("color")
        if isinstance(color, list) and len(color) >= 3:
            rgb = tuple(int(round(float(c) * 255)) for c in color[:3])
            params = stroke.get("params") or {}
            px = 4 + int(round(40 * float(params.get("effects_adjust_size") or 0.35)))
            border = (rgb, max(3, px // 2))
        _place(canvas, Image.open(src).convert("RGB"), float(dom.get("x") or 0.0),
               float(dom.get("y") or 0.0), max(0.18, float(dom.get("scale") or 1.0)), border)
    return canvas


def _grade(img_path: Path, sid: str, out_path: Path) -> Path:
    """把该套调色链套到静态帧上（闭环校准用同一套参数；静态图没有时间轴，取整段均值）。"""
    ff = _ff()
    style_color = _resolve_style_color(sid)
    corr = {"brightness": 0.0, "contrast": 1.0, "saturation": float(style_color.get("saturation_scale") or 1.0)}
    vf = _build_vf_for_corr(corr, None, lut_strength=0.0, style_color=style_color, is_broll=False)
    if not ff:
        return img_path
    r = subprocess.run([ff, "-v", "error", "-i", str(img_path), "-vf", vf, "-frames:v", "1", "-y", str(out_path)],
                       capture_output=True)
    return out_path if r.returncode == 0 else img_path


def _subtitle_overlay(sid: str, tpl: Dict[str, Any]) -> Optional[Image.Image]:
    try:
        p = Path("/tmp") / f"sheet_sub_{sid}.png"
        render_subtitle_png({"text": SAMPLE_SUB, "keywords": [SAMPLE_KW]}, tpl, None, p)
        return Image.open(p).convert("RGBA")
    except Exception:
        return None


def _flower_overlay(sid: str, ov_style: Dict[str, Any]) -> Optional[Image.Image]:
    try:
        elem = {"kind": "flower_text", "text": SAMPLE_TEXT, "text_or_asset": SAMPLE_TEXT,
                "big_text": True, "style_id": sid}
        img = render_template_packaging_png(elem, style_id=sid)
        return img.convert("RGBA")
    except Exception:
        return None


def _effects_frame(img: Image.Image, sid: str, color_effects: Dict[str, Any]) -> Tuple[Image.Image, str]:
    eff = (color_effects.get(sid) or {}).get("effects") or {}
    active = {k: v for k, v in eff.items() if isinstance(v, dict)}
    if not active:
        return img, "源草稿无画面特效"
    from step7_color_grade import _effect_filters
    vf = ",".join(_effect_filters({"effects": eff}))
    if not vf:
        return img, "源草稿无画面特效"
    ff = _ff()
    src = Path("/tmp") / f"sheet_fx_in_{sid}.png"
    dst = Path("/tmp") / f"sheet_fx_out_{sid}.png"
    img.save(src)
    r = subprocess.run([ff, "-v", "error", "-i", str(src), "-vf", vf, "-frames:v", "1", "-y", str(dst)],
                       capture_output=True)
    names = "、".join(str(v.get("source_effect") or k) for k, v in active.items())
    return (Image.open(dst).convert("RGB") if r.returncode == 0 else img), names


def _cell(img: Image.Image, title: str, sub: str, width: int = FULL_W) -> Image.Image:
    """一格 = 标题 + 图；宽度可指定（整幅格 396，字幕/花字裁片给到原尺寸附近，字才看得清）。"""
    w = max(120, width)
    h = max(40, int(round(img.height * w / max(1, img.width))))
    cell = Image.new("RGB", (w, h + 46), (14, 14, 16))
    cell.paste(img.resize((w, h), Image.LANCZOS), (0, 46))
    d = ImageDraw.Draw(cell)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Songti.ttc", 19)
    except Exception:
        font = ImageFont.load_default()
    d.text((8, 5), title, fill=(240, 240, 240), font=font)
    if sub:
        d.text((8, 25), sub, fill=(148, 152, 158), font=font)
    return cell


def _band(img: Image.Image, box: Tuple[int, int, int, int], pad: int = 56) -> Image.Image:
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(img.width, x1 + pad)
    y1 = min(img.height, y1 + pad)
    if x1 - x0 < 40 or y1 - y0 < 40:
        return img
    return img.crop((x0, y0, x1, y1))


def _sheet(rows: List[Tuple[str, List[Tuple[Image.Image, str, str, int]]]], out: Path) -> None:
    """逐行拼：每格宽度可不同（整幅 396 / 字幕花字裁片更宽），行高按最高格，底部标签不被截。"""
    pad = 10
    cells_all = [[_cell(img, title, sub, width) for (img, title, sub, width) in cells] for _sid, cells in rows]
    widths = [sum(c.width for c in cells) + pad * (len(cells) - 1) for cells in cells_all]
    row_hs = [max(c.height for c in cells) for cells in cells_all]
    sheet = Image.new("RGB", (max(widths) + pad * 2, sum(row_hs) + pad * (len(rows) + 1)), (20, 20, 22))
    y = pad
    for cells, rh in zip(cells_all, row_hs):
        x = pad
        for cell in cells:
            sheet.paste(cell, (x, y))
            x += cell.width + pad
        y += rh + pad
    sheet.save(out, quality=94, subsampling=1)
    print(f"wrote {out} {sheet.size}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "reports" / "style_features_2026-11-23"))
    ap.add_argument("--per-sheet", type=int, default=6)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    sources = read_json(FORENSIC / "style_sources.json")["styles"]
    usage = _usage()
    tpl_doc = read_json(ASSETS / "subtitle_templates" / "templates.json")
    templates = tpl_doc.get("templates") or tpl_doc
    bp = read_json(ASSETS / "style_spectrum" / "template_blueprints.json")["blueprints"]
    color_effects = read_json(ASSETS / "style_spectrum" / "color_effects.json")["styles"]

    sids = [f"style_{i:02d}" for i in range(1, 25)]
    if args.only:
        sids = [s for s in sids if s == args.only]

    work = Path("/tmp/style_feature_work")
    work.mkdir(parents=True, exist_ok=True)
    rows: List[Tuple[str, List[Image.Image]]] = []
    for sid in sids:
        main_p, extras = _source_frames(sid, sources, usage)
        node = usage["styles"][sid]
        m = node["main"]
        comp = _compose(sid, usage, main_p, extras)
        comp_p = work / f"{sid}_comp.png"
        comp.save(comp_p)
        graded = _grade(comp_p, sid, work / f"{sid}_grade.png")
        base = Image.open(graded).convert("RGB")

        # ③ 字幕
        sub_img = _subtitle_overlay(sid, templates.get(sid) or {})
        sub_frame = base.copy()
        if sub_img is not None:
            tpl = templates.get(sid) or {}
            sy = int(tpl.get("safe_y_center") or 1400)
            sub_frame.paste(sub_img, ((W - sub_img.width) // 2, sy - sub_img.height // 2), sub_img)

        # ④ 花字
        fl_img = _flower_overlay(sid, (bp.get(sid) or {}).get("overlay_style") or {})
        fl_frame = base.copy()
        if fl_img is not None:
            ov_style = (bp.get(sid) or {}).get("overlay_style") or {}
            layout = str(ov_style.get("flower_layout") or "")
            top_frac = 0.055 if "top" in layout or "bar" in layout else 0.14
            fl_w = min(int(W * 0.88), fl_img.width)
            fl_h = int(round(fl_img.height * fl_w / max(1, fl_img.width)))
            fl_img = fl_img.resize((fl_w, fl_h), Image.LANCZOS)
            fl_frame.paste(fl_img, ((W - fl_w) // 2, int(H * top_frac)), fl_img)

        # ⑤ 特效
        fx_frame, fx_names = _effects_frame(base.copy(), sid, color_effects)

        raw = Image.open(main_p).convert("RGB") if main_p else base
        import numpy as np
        d_comp = float(np.abs(np.asarray(comp, dtype=float) - np.asarray(_fit(raw), dtype=float)).mean()) if main_p else 0.0
        # 字幕 / 花字格改成裁片：整幅缩到 396 宽只有 0.37 倍，字看不清；裁片按墨迹包围盒留白，字放到能读
        sub_box = (0, 0, 0, 0)
        if sub_img is not None:
            sy = int((templates.get(sid) or {}).get("safe_y_center") or 1400)
            sub_box = ((W - sub_img.width) // 2, sy - sub_img.height // 2,
                       (W + sub_img.width) // 2, sy + sub_img.height // 2)
        fl_box = (0, 0, 0, 0)
        if fl_img is not None:
            ov_style_d = (bp.get(sid) or {}).get("overlay_style") or {}
            layout_d = str(ov_style_d.get("flower_layout") or "")
            top_d = int(H * (0.055 if "top" in layout_d or "bar" in layout_d else 0.14))
            fl_box = ((W - fl_img.width) // 2, top_d, (W + fl_img.width) // 2, top_d + fl_img.height)
        cells = [
            (raw, f"{sid} ① 源素材", "剪映草稿里的原始素材帧"),
            (comp, "② 真草稿构图",
             f"主轨 scale={m.get('scale')} x={m.get('x')} y={m.get('y')} ｜ 叠加轨 {len(node['overlays'])} 条 ｜ 与①差 {d_comp:.1f}"),
            (_band(sub_frame, sub_box) if sub_img is not None else sub_frame, "③ 库字幕",
             f"字号 {templates.get(sid, {}).get('font_size')} · 字数上限 {templates.get(sid, {}).get('max_chars_per_line')} · 底框 {templates.get(sid, {}).get('bg_box_color')}"),
            (_band(fl_frame, fl_box) if fl_img is not None else fl_frame, "④ 蓝图花字",
             "、".join((bp.get(sid, {}).get("overlay_style", {}).get("flower_colors") or [])[:3])),
            (fx_frame, "⑤ 源真值特效", fx_names),
        ]
        widths = [FULL_W, FULL_W, WAIST_W, WAIST_W, FULL_W]
        rows.append((sid, [(c[0], c[1], c[2], widths[i]) for i, c in enumerate(cells)]))
        print(f"{sid} ok  fx={fx_names} windows={len(node['overlays'])}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    per = max(1, args.per_sheet)
    for i in range(0, len(rows), per):
        chunk = rows[i:i + per]
        tag = chr(ord("A") + i // per)
        _sheet(chunk, Path(f"{out}_{tag}.jpg"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
