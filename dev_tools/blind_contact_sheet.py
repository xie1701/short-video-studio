#!/usr/bin/env python3
"""
盲审接触表：把你从剪映里导的**原版首帧**和我们复刻的首帧并排，左右随机、不告诉你哪边是哪边。

为什么要盲
──────────
自己人看自己的片子，会不由自主地"往好里看"。标了哪边是原版，你就会盯着差异找理由。
所以这里左右按固定种子随机打乱，只标 `style_XX · A` / `style_XX · B`；
哪边是原版写在 `blind_key.csv` 里——**先判完再去看那一列**，不然白盲。

你只需要做一件事
────────────────
在剪映里把 24 套草稿的首帧导成图片，文件名带上风格号（`style_01.png`，或放进 `style_01/` 子目录），
放到一个目录里，然后：

    python3 dev_tools/blind_contact_sheet.py --orig ~/Desktop/原版首帧 --ours <我们的首帧目录>

不给我们那一侧也能跑（`--ours` 可省），先出一张「原版首帧总览」也能核对构图/字幕位置。

判定怎么写
──────────
看完在 `blind_key.csv` 里补一列 `verdict`：`same`（看不出差别）/ `minor`（小差）/ `wrong`（明显不是一回事），
再把 `wrong` 的挑出来对着看。**别按我的自评指标判，按你眼睛。**
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

STYLE_RE = re.compile(r"style[_-]?(\d{1,2})", re.I)
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".heic"}


def style_key(path: Path) -> Optional[str]:
    """从文件名或所在目录里认出风格号；认不出返回 None（不许瞎配）。"""
    for text in (path.stem, path.parent.name):
        m = STYLE_RE.search(str(text))
        if m:
            return f"style_{int(m.group(1)):02d}"
    return None


def collect(dir_path: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    if not dir_path.is_dir():
        return out
    for p in sorted(dir_path.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMG_EXTS:
            continue
        key = style_key(p)
        if key and key not in out:
            out[key] = p
    return out


def _load_png(path: Path, box: Tuple[int, int]):
    from PIL import Image, ImageDraw

    try:
        from common import ensure_image_decodable
        fixed = ensure_image_decodable(path)
        src = Path(fixed) if fixed else path
    except Exception:
        src = path
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = box
        scale = max(w / im.width, h / im.height)
        im2 = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        left = (im2.width - w) // 2
        top = (im2.height - h) // 2
        return im2.crop((left, top, left + w, top + h))


def build_sheet(
    orig: Dict[str, Path],
    ours: Dict[str, Path],
    out_png: Path,
    key_csv: Path,
    seed: int = 20261123,
    cell: Tuple[int, int] = (300, 534),
    cols: int = 6,
) -> Dict[str, object]:
    from PIL import Image, ImageDraw, ImageFont

    try:
        from common import find_commercial_font
        font_path = find_commercial_font(None)
    except Exception:
        font_path = None

    def font(sz: int):
        if font_path:
            try:
                return ImageFont.truetype(str(font_path), sz)
            except Exception:
                pass
        return ImageFont.load_default()

    styles = sorted(set(orig) | set(ours))
    if not styles:
        raise SystemExit("两侧都没认出风格号（文件名里要有 style_01 这样的字样）")

    rng = random.Random(seed)
    pad, head = 8, 34
    rows = (len(styles) + cols - 1) // cols
    cw, ch = cell
    sheet = Image.new("RGB", (cols * (cw * 2 + pad) + pad, rows * (ch + head + pad) + pad), (24, 26, 30))
    d = ImageDraw.Draw(sheet)
    f = font(20)
    keys: List[Dict[str, object]] = []

    for i, sid in enumerate(styles):
        r, c = divmod(i, cols)
        x0 = pad + c * (cw * 2 + pad)
        y0 = pad + r * (ch + head + pad)
        flip = rng.random() < 0.5
        sides = [("our" if flip else "orig"), ("orig" if flip else "our")]
        for j, which in enumerate(sides):
            box_x = x0 + j * (cw + 4)
            label = "A" if j == 0 else "B"
            d.text((box_x + 4, y0 + 6), f"{sid}  {label}", font=f, fill=(232, 234, 238))
            src = (orig if which == "orig" else ours).get(sid)
            if src is None:
                d.rectangle([box_x, y0 + head, box_x + cw, y0 + head + ch], outline=(70, 74, 82), width=2)
                d.text((box_x + cw // 2 - 34, y0 + head + ch // 2), "缺", font=font(28), fill=(120, 124, 132))
                continue
            try:
                img = _load_png(src, (cw, ch))
            except Exception:
                d.rectangle([box_x, y0 + head, box_x + cw, y0 + head + ch], outline=(150, 60, 60), width=2)
                d.text((box_x + 8, y0 + head + ch // 2), "读取失败", font=f, fill=(200, 120, 120))
                continue
            sheet.paste(img, (box_x, y0 + head))
        keys.append({
            "style_id": sid,
            "A": "our" if sides[0] == "our" else "orig",
            "B": "our" if sides[1] == "our" else "orig",
            "verdict": "",
            "note": "",
        })

    out_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png, quality=92)
    with key_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["style_id", "A", "B", "verdict", "note"])
        w.writeheader()
        for k in keys:
            w.writerow(k)
    return {
        "styles": len(styles),
        "orig_only": sorted(set(orig) - set(ours)),
        "ours_only": sorted(set(ours) - set(orig)),
        "sheet": str(out_png),
        "key": str(key_csv),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="原版首帧 vs 复刻首帧盲审接触表")
    ap.add_argument("--orig", required=True, help="你从剪映导出的原版首帧目录（文件名含 style_XX）")
    ap.add_argument("--ours", default="", help="我们复刻的首帧目录（可省）")
    ap.add_argument("--out", default="", help="输出图片（默认 reports/blind_sheet_<日期>.jpg）")
    ap.add_argument("--cols", type=int, default=6, help="每行几套（默认 6）")
    ap.add_argument("--seed", type=int, default=20261123, help="左右随机种子（固定可复现）")
    args = ap.parse_args()

    orig = collect(Path(args.orig).expanduser())
    ours = collect(Path(args.ours).expanduser()) if args.ours else {}
    if not orig:
        raise SystemExit(f"在 {args.orig} 里没认出任何 style_XX 图片")
    out_png = Path(args.out).expanduser() if args.out else (
        ROOT / "reports" / "blind_sheet_2026-11-23.jpg")
    key_csv = out_png.with_name(out_png.stem + "_key.csv")
    rep = build_sheet(orig, ours, out_png, key_csv, seed=args.seed, cols=args.cols)
    print(f"[blind] 凑到 {rep['styles']} 套（原版 {len(orig)} / 复刻 {len(ours)}）")
    if rep["orig_only"]:
        print(f"[blind] 只有原版、还没复刻帧的：{'、'.join(rep['orig_only'])}")
    if rep["ours_only"]:
        print(f"[blind] 只有复刻帧、没导原版的：{'、'.join(rep['ours_only'])}")
    print(f"[blind] 接触表 -> {rep['sheet']}")
    print(f"[blind] 左右对照表 -> {rep['key']}（先判完再看这一列）")


if __name__ == "__main__":
    main()
