#!/usr/bin/env python3
"""只读验收：真渲染一句字幕，量「墨高 / 字号」与「阴影位移 / 模糊」。

用法：
  python3 dev_tools/verify_subtitle_typography.py --style-id style_21
  python3 dev_tools/verify_subtitle_typography.py --style-id style_21 --text "锅底用到今天"
  python3 dev_tools/verify_subtitle_typography.py --pipeline-dir <项目>/pipeline   # 用管线里真字幕条目

量法（与人工实测同一口径）：
  墨高  = 白字掩膜（α>200 且三通道>200）的 bbox 高度
  阴影  = 位移后用「字形掩膜 × 暗像素掩膜」的互相关找最佳 (dx,dy)，取幅度与角度
对照值（用户实测原版成片，1080×1920）：style_21 墨高 70px、阴影幅度 11.7px(-45°)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "short-video-studio"
sys.path.insert(0, str(SKILL / "scripts"))

from common import load_subtitle_templates  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import render_mp4 as R  # noqa: E402


def masks(png: Path) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    img = np.asarray(Image.open(png).convert("RGBA")).astype(np.int16)
    r, g, b, a = img[:, :, 0], img[:, :, 1], img[:, :, 2], img[:, :, 3]
    ink = (a > 200) & (r > 200) & (g > 200) & (b > 200)
    dark = (a > 40) & (np.maximum(np.maximum(r, g), b) < 110) & ~ink
    return ink, dark, img


def shadow_weight(img: np.ndarray) -> np.ndarray:
    """阴影层的 alpha 权重（淡出边缘权重小），用于加权互相关，避开虚化尾巴的噪声。"""
    r, g, b, a = img[:, :, 0], img[:, :, 1], img[:, :, 2], img[:, :, 3]
    ink = (a > 200) & (r > 200) & (g > 200) & (b > 200)
    darkish = (np.maximum(np.maximum(r, g), b) < 110) & ~ink
    return np.where(darkish, np.clip(a - 40, 0, 255).astype(np.float64), 0.0)


def bbox_h(m: np.ndarray) -> int:
    ys = np.where(m.any(axis=1))[0]
    return int(ys[-1] - ys[0] + 1) if ys.size else 0


def best_shift(ink: np.ndarray, weight: np.ndarray, rng: int = 30) -> Tuple[int, int, float]:
    """把墨迹掩膜按 (dx,dy) 平移后与阴影 alpha 层求加权重叠，取重叠最大者。"""
    ys, xs = np.nonzero(ink)
    if ys.size == 0:
        return 0, 0, 0.0
    h, w = weight.shape
    best = (0, 0, -1.0)
    for dy in range(-rng, rng + 1):
        yy = ys + dy
        ok_y = (yy >= 0) & (yy < h)
        if not ok_y.any():
            continue
        for dx in range(-rng, rng + 1):
            xx = xs + dx
            ok = ok_y & (xx >= 0) & (xx < w)
            if not ok.any():
                continue
            score = float(weight[yy[ok], xx[ok]].sum())
            if score > best[2]:
                best = (dx, dy, score)
    return best


def render(text: str, style_id: str) -> Tuple[Path, Dict[str, Any]]:
    tpls = load_subtitle_templates()
    tpl = dict(tpls[style_id])
    font_path = None
    try:
        from common import find_commercial_font, find_source_font

        font_path = find_source_font(tpl.get("source_font_family"), style_id, tpl.get("font_candidates")) \
            or find_commercial_font(tpl.get("font_candidates"), verified_only=True)
    except Exception:
        font_path = None
    # 量阴影时把底框关掉：深色底框会跟阴影啊成一团，暗像素掩膜就不再只含阴影。
    # （只影响本次探针，字号/阴影参数一概不动。）
    probe = dict(tpl)
    probe["bg_box_color"] = "#00000000"
    probe["bg_box"] = "none"
    out = Path(tempfile.mkdtemp()) / f"{style_id}.png"
    R.render_subtitle_png(
        {"text": text, "keywords": [], "words": []},
        probe,
        str(font_path) if font_path else None,
        out,
    )
    tpl["_font_path"] = font_path
    tpl["_bg_box"] = str(tpls[style_id].get("bg_box_color") or "none")
    return out, tpl


def effective_font_px(tpl: Dict[str, Any]) -> int:
    """渲染器实际用的字号（通宽双行横幅会 ×1.32）。"""
    fs = max(24, int(tpl.get("font_size", 62)))
    if bool(tpl.get("dual_line_banner")):
        fs = max(76, int(round(fs * 1.32)))
    return fs


def report(style_id: str, text: str, png: Path, tpl: Dict[str, Any]) -> Dict[str, Any]:
    ink, dark, img = masks(png)
    ink_h = bbox_h(ink)
    font_px = int(tpl.get("font_size") or 0)
    eff_px = effective_font_px(tpl)
    shadow = R.resolve_subtitle_shadow(tpl, eff_px)
    if bool(tpl.get("shadow")) and (shadow[0] or shadow[1]):
        dx, dy, _score = best_shift(ink, shadow_weight(img))
    else:
        dx, dy = 0, 0
    mag = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(dy, dx)) if mag else 0.0
    return {
        "style_id": style_id,
        "text": text,
        "font_size_px": font_px,
        "rendered_font_px": eff_px,
        "ink_height_px": ink_h,
        "ink_over_font": round(ink_h / eff_px, 3) if eff_px else 0.0,
        "shadow_shift_px": [dx, dy],
        "shadow_magnitude_px": round(mag, 2),
        "shadow_angle_deg": round(angle, 1),
        "shadow_expected_dxdy": [shadow[0], shadow[1]],
        "shadow_expected_mag": round(math.hypot(shadow[0], shadow[1]), 2),
        "shadow_blur_sigma_px": round(shadow[2], 2),
        "bg_box": tpl.get("_bg_box"),
        "chars": len(str(text or "").replace(" ", "")),
        "line_cap": max(4, int((640 - 28) // max(1, eff_px))),
        "overflow_fallback": len(str(text or "").replace(" ", "")) > max(4, int((640 - 28) // max(1, eff_px))),
        "font_file": Path(str(tpl.get("_font_path"))).name if tpl.get("_font_path") else None,
        "png": str(png),
    }


def pipeline_text(pdir: Path) -> Optional[Tuple[str, str]]:
    """从管线里取一条真字幕（最长那条），返回 (style_id, text)。"""
    subs_p = pdir / "05_subtitles.json"
    if not subs_p.is_file():
        return None
    subs = json.loads(subs_p.read_text(encoding="utf-8"))
    sid = str(subs.get("template_id") or "")
    best = ""
    for e in subs.get("entries") or []:
        if e.get("suppressed"):
            continue
        t = str(e.get("text") or "")
        if len(t) > len(best):
            best = t
    return (sid, best) if sid and best else None


def main() -> None:
    ap = argparse.ArgumentParser(description="只读验收字幕排版（墨高/阴影位移）")
    ap.add_argument("--style-id", default="style_21")
    ap.add_argument("--text", default="锅底用到今天")
    ap.add_argument("--pipeline-dir", type=Path, default=None)
    ap.add_argument("--all", action="store_true", help="24 套全跑一遍（每套用同一句）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    jobs = []
    if args.pipeline_dir:
        got = pipeline_text(Path(args.pipeline_dir))
        if not got:
            raise SystemExit(f"{args.pipeline_dir} 里没有可用的 05_subtitles.json 条目")
        jobs.append((got[0], got[1]))
    elif args.all:
        jobs = [(f"style_{i:02d}", args.text) for i in range(1, 25)]
    else:
        jobs.append((args.style_id, args.text))

    rows = []
    for sid, text in jobs:
        png, tpl = render(text, sid)
        rows.append(report(sid, text, png, tpl))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    print(f"{'style':9} {'字号':>4} {'墨高':>4} {'墨高/字号':>9} {'阴影dx,dy':>10} {'幅度':>6} {'角度':>7} {'σ':>5}  字体")
    for r in rows:
        print(
            f"{r['style_id']:9} {r['rendered_font_px']:>4} {r['ink_height_px']:>4} {r['ink_over_font']:>9} "
            f"{str(tuple(r['shadow_shift_px'])):>10} {r['shadow_magnitude_px']:>6} "
            f"{r['shadow_angle_deg']:>7} {r['shadow_blur_sigma_px']:>5}  {r['font_file']}"
        )
        if r.get("overflow_fallback"):
            print(
                f"  ! {r['style_id']} 这句 {r['chars']} 字超过每行 {r['line_cap']} 字（字号 {r['rendered_font_px']}px），"
                "渲染器走了溢出兜底把字号砍小了，墨高不能拿来对原版"
            )


if __name__ == "__main__":
    main()
