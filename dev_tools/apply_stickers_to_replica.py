#!/usr/bin/env python3
"""
把草稿里的贴纸轨叠到**已经渲好的复刻片**上（独立一步，不动主渲染链）。

贴纸是最后一道合成层：按画布大小 RGBA 图层输出，再按时间段 overlay 上去。
几何真值见 sticker_render.py：显示尺寸 = 原生 × clip.scale × 画布宽/720。

    python3 dev_tools/apply_stickers_to_replica.py --style-id style_21 \
        --video /tmp/blind_review/style_21_replica.mp4 --out /tmp/blind_review/style_21_stickered.mp4

草稿定位：forensic/style_sources.json 给 style→草稿名；草稿根默认 JY_DRAFT_ROOT 环境变量，
否则在 ~/Movies/*/User Data/Projects/* 下自动找。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "dev_tools"))


def default_draft_root() -> Path:
    env = os.environ.get("JY_DRAFT_ROOT")
    if env:
        return Path(env)
    for cand in sorted((Path.home() / "Movies").glob("*/User Data/Projects/*")):
        if cand.is_dir() and any(c.is_dir() for c in cand.iterdir()):
            return cand
    return Path.home() / "Movies" / "User Data" / "Projects" / "drafts"


def load_draft(draft_dir: Path) -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("jd", str(ROOT / "forensic" / "jy_draft_decrypt.py"))
    jd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jd)
    for name in ("draft_content.json",):
        p = draft_dir / name
        if p.is_file():
            raw = p.read_text(encoding="utf-8", errors="ignore")
            try:
                return json.loads(raw)
            except Exception:
                return json.loads(jd.decrypt_text(raw))
    raise SystemExit(f"草稿里没有 draft_content.json: {draft_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="把草稿贴纸轨叠到复刻片上")
    ap.add_argument("--style-id", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--draft-root", default=str(default_draft_root()))
    ap.add_argument("--work", default="/tmp/sticker_work")
    args = ap.parse_args()

    from sticker_render import plan_sticker_layers, overlay_stickers

    sources = json.loads((ROOT / "forensic" / "style_sources.json").read_text(encoding="utf-8"))["styles"]
    dname = sources.get(args.style_id)
    if not dname:
        raise SystemExit(f"未知风格: {args.style_id}")
    draft_dir = Path(args.draft_root) / dname
    draft = load_draft(draft_dir)
    canvas = draft.get("canvas_config") or {}
    W = int(canvas.get("width") or 1080)
    H = int(canvas.get("height") or 1920)
    fps = float(draft.get("fps") or 30.0)
    total = float(draft.get("duration") or 0) / 1_000_000
    work = Path(args.work) / args.style_id
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {"unrealized": {}, "sticker_notes": []}

    layers = plan_sticker_layers(draft, draft_dir, W, H, fps, total, work, report)
    print(f"[sticker] {args.style_id}: {len(layers)} 段 -> " +
          (", ".join(f"{l.name}@{l.t_start:.2f}s" for l in layers) or "无"))
    if report.get("sticker_notes"):
        print("[sticker] 注意:", "; ".join(report["sticker_notes"][:4]))
    if not layers:
        shutil.copy2(args.video, args.out)
        print("[sticker] 没有可叠的贴纸，原样复制")
        return
    overlay_stickers(Path(args.video), layers, Path(args.out), fps, total)
    print(f"[sticker] -> {args.out}")


if __name__ == "__main__":
    main()
