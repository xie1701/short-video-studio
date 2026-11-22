#!/usr/bin/env python3
"""
[画中画小窗人脸构图 实测验收]

做法（不靠自述，靠输出帧）：
  1. 从真素材里抽一段带人的片段；
  2. 用旧逻辑（正中裁切）和新逻辑（人脸锚点）各渲一次同一个窗口；
  3. 在**输出帧**上再跑一次人脸检测，量：还找得到脸吗 / 脸是不是贴着窗边（=被裁掉一半）/ 脸偏了多少；
  4. 出一张左右对照图，人眼也能核。

用法：
  JY_DRAFT_ROOT=<草稿库> python dev_tools/verify_face_framing.py            # 真素材
  python dev_tools/verify_face_framing.py --self-test                       # 无素材时用合成片段
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "short-video-studio" / "scripts"
for p in (str(SCRIPTS), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

from common import maybe_reexec_in_venv  # noqa: E402

if __name__ == "__main__":
    maybe_reexec_in_venv()

import face_framing  # noqa: E402
from vision_inspector import inspect_images  # noqa: E402

# 真实在用的几种窗口尺寸（场景取自 24 套真草稿几何 + 现有相框模式）
WINDOWS: List[Tuple[str, int, int]] = [
    ("全宽取证窗 0.55", 594, 1056),
    ("方窗 0.4", 432, 432),
    ("圆窗 Ø292", 292, 292),
    ("相框 764×802", 764, 802),
]
OUT_DIR = Path("/tmp/face_framing_proof")


def _ff(*args: str) -> int:
    return subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True).returncode


def _pick_real_clips(limit: int) -> List[Path]:
    """从真草稿素材里挑带人的片段（隐私：路径只在环境变量里，不落仓库）。"""
    root = Path(os.environ.get("JY_DRAFT_ROOT") or "")
    if not root.is_dir():
        return []
    picks: List[Path] = []
    for proj in sorted(root.iterdir()):
        if len(picks) >= limit:
            break
        vdir = proj / "materials" / "video"
        if not vdir.is_dir():
            continue
        for f in sorted(vdir.iterdir()):
            if len(picks) >= limit:
                break
            if f.suffix.lower() not in (".mp4", ".mov") or f.stat().st_size < 2_000_000:
                continue
            picks.append(f)
            break
    return picks


def _make_clip(src: Path, out: Path, dur: float = 3.0) -> bool:
    # 保持原始画幅（横屏素材进竖屏窗口才是最容易切脸的情形，不许先转成竖屏掩盖问题）
    return _ff("-ss", "1.2", "-t", f"{dur}", "-i", str(src), "-an",
               "-vf", "fps=30", "-c:v", "libx264", "-crf", "20",
               "-pix_fmt", "yuv420p", str(out)) == 0


def _synthetic_clip(out: Path) -> bool:
    """没有真素材时的兜底：白底 + 偏左 1/4 的人脸图（用 PIL 画的简笔脸，只验证几何）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1080, 1920), (238, 238, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 120, 1000, 1700], fill=(52, 78, 120))
    d.ellipse([250, 620, 470, 860], fill=(232, 190, 160))   # 脸
    d.ellipse([300, 700, 330, 730], fill=(40, 40, 40))
    d.ellipse([390, 700, 420, 730], fill=(40, 40, 40))
    d.arc([300, 770, 420, 830], 20, 160, fill=(150, 60, 60), width=6)
    frame = OUT_DIR / "synthetic_src.jpg"
    img.save(frame)
    return _ff("-loop", "1", "-t", "3", "-i", str(frame), "-vf", "fps=30",
               "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out)) == 0


def _size(p: Path) -> Tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def _face_of(img: Path) -> Optional[Dict[str, float]]:
    res = inspect_images([img])
    obj = res.get(str(img)) or {}
    faces = obj.get("faces") or []
    if not faces:
        return None
    best = max(faces, key=lambda f: float((f.get("bbox") or {}).get("w", 0)) * float((f.get("bbox") or {}).get("h", 0)))
    return {k: float((best.get("bbox") or {}).get(k, 0.0)) for k in ("x", "y", "w", "h")}


def _border_touch(fb: Dict[str, float], tol: float = 0.02) -> bool:
    return fb["x"] <= tol or fb["y"] <= tol or fb["x"] + fb["w"] >= 1 - tol or fb["y"] + fb["h"] >= 1 - tol


def _render_window(clip: Path, out: Path, w: int, h: int, anchor: Optional[Dict[str, Any]]) -> bool:
    vf = face_framing.cover_crop_vf(w, h, anchor)
    return _ff("-ss", "0.5", "-i", str(clip), "-frames:v", "1", "-vf", vf, str(out)) == 0


def main() -> None:
    ap = argparse.ArgumentParser(description="画中画小窗人脸构图实测")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--sheet", default=str(REPO / "reports" / "face_framing_proof.jpg"))
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    clips: List[Path] = []
    if not args.self_test:
        with tempfile.TemporaryDirectory(prefix="svs_ff_") as td:
            for i, src in enumerate(_pick_real_clips(args.limit)):
                dst = Path(td) / f"clip_{i:02d}.mp4"
                if _make_clip(src, dst):
                    clips.append(dst)
            return _run(clips, args.sheet)
    if not clips:
        syn = OUT_DIR / "synthetic.mp4"
        if _synthetic_clip(syn):
            clips = [syn]
    _run(clips, args.sheet)


def _run(clips: List[Path], sheet_out: str) -> None:
    if not clips:
        print("[face] 没拿到任何素材（真素材路径需 JY_DRAFT_ROOT，或加 --self-test）")
        raise SystemExit(2)
    rows: List[Dict[str, Any]] = []
    cells: List[Tuple[str, Path, Path, Path, str]] = []
    ok_legacy_bad = 0
    fixed = 0
    for ci, clip in enumerate(clips):
        anchor = face_framing.anchor_for(str(clip), 1.4, 3.0)
        if not anchor:
            print(f"[face] {clip.name}: 没检测到人脸，跳过（会照实退回居中裁切）")
            continue
        for name, w, h in WINDOWS:
            src_img = OUT_DIR / f"c{ci}_{w}x{h}_src.jpg"
            leg = OUT_DIR / f"c{ci}_{w}x{h}_legacy.jpg"
            fac = OUT_DIR / f"c{ci}_{w}x{h}_face.jpg"
            if not _ff("-ss", "0.5", "-i", str(clip), "-frames:v", "1", str(src_img)):
                pass
            _render_window(clip, leg, w, h, None)
            _render_window(clip, fac, w, h, anchor)
            f_leg = _face_of(leg) if leg.is_file() else None
            f_fac = _face_of(fac) if fac.is_file() else None
            bad_leg = (f_leg is None) or _border_touch(f_leg)
            bad_fac = (f_fac is None) or _border_touch(f_fac)
            ok_legacy_bad += 1 if bad_leg else 0
            fixed += 1 if (bad_leg and not bad_fac) else 0
            off = abs((f_fac["x"] + f_fac["w"] / 2) - 0.5) if f_fac else 1.0
            _w, _h = _size(clip)
            rows.append({
                "clip": f"{clip.name}{f'({_w}x{_h})' if _w else ''}", "window": name,
                "legacy_face": "无脸" if f_leg is None else ("贴边" if _border_touch(f_leg) else "完整"),
                "face_face": "无脸" if f_fac is None else ("贴边" if _border_touch(f_fac) else "完整"),
                "face_center_offset": round(off, 3),
            })
            cells.append((f"{name} · 旧:{rows[-1]['legacy_face']} 新:{rows[-1]['face_face']}", src_img, leg, fac, name))

    total = len(rows)
    print(f"\n[face] 窗口样本 {total} 个")
    for r in rows:
        print(f"  {r['clip'][:22]:<24}{r['window']:<16}旧 {r['legacy_face']:<5}→ 新 {r['face_face']:<5}"
              f"（脸中心偏离 {r['face_center_offset']*100:.1f}%）")
    if total:
        print(f"[face] 旧逻辑出问题（找不到脸或脸贴边）{ok_legacy_bad}/{total}；本次修复 {fixed} 个")
    if cells:
        _sheet(cells, Path(sheet_out))
        print(f"[face] 对照图 -> {sheet_out}")


def _sheet(cells: List[Tuple[str, Path, Path, Path, str]], out: Path, cols: int = 3) -> None:
    from PIL import Image, ImageDraw

    rows = (len(cells) + cols - 1) // cols
    cell_w, cell_h = 320, 420
    sheet = Image.new("RGB", (cols * cell_w, rows * (cell_h + 26)), (18, 18, 20))
    d = ImageDraw.Draw(sheet)
    for i, (label, src, leg, fac, _ ) in enumerate(cells):
        r, c = divmod(i, cols)
        x0 = c * cell_w
        y0 = r * (cell_h + 26)
        d.text((x0 + 6, y0 + 5), label[:34], fill=(240, 240, 240))
        for j, p in enumerate((leg, fac)):
            if not p.is_file():
                continue
            try:
                im = Image.open(p).convert("RGB")
            except Exception:
                continue
            im.thumbnail((cell_w // 2 - 8, cell_h - 12))
            sheet.paste(im, (x0 + 6 + j * (cell_w // 2), y0 + 22))
        d.text((x0 + 6, y0 + cell_h + 8), "左=旧居中裁切   右=人脸中心", fill=(160, 200, 160))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)


if __name__ == "__main__":
    main()
