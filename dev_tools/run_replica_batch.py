#!/usr/bin/env python3
"""
24 套复刻批处理：逐套 渲染 → 并排片 → 六项指标 → 汇总 CSV。

顺序按**时长从短到长**（短的最先出结果，你最早能看）。
可断点续跑：已经渲过的复刻片、已经算过的指标都跳过；`--force` 才重做。

    python3 dev_tools/run_replica_batch.py                 # 全量
    python3 dev_tools/run_replica_batch.py --styles style_21,style_17
    python3 dev_tools/run_replica_batch.py --metrics-only   # 只算指标，不重渲

产物：
    <out>/<style_id>_replica.mp4        复刻全片
    <out>/sidebyside/<style>_sidebyside_diff.mp4 / _index_grid.jpg / _metrics.json
    <out>/sidebyside/sidebyside_key.csv
    reports/replica_batch.csv           汇总（每套一行）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(ROOT / "dev_tools"))

PY = os.environ.get("SVS_PY") or sys.executable
ORIG_DIR = Path(os.environ.get("JY_STYLEFRAME_DIR") or (Path.home() / "Downloads" / "styleframe"))
TMP_OUT = Path("/tmp/blind_review")
SUMMARY = ROOT / "reports" / "replica_batch.csv"

FIELDS = [
    "style_id", "draft", "dur_orig", "verdict", "ssim_mean", "subtitle_band_rel_max",
    "grade_max_level_delta", "cut_max_delta_s", "lufs_delta", "duration_delta_s",
    "flags", "render_sec", "unrealized", "status",
]


def probe(path: Path) -> Dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    j = json.loads(r.stdout or "{}")
    st = (j.get("streams") or [{}])[0]
    fps = 0.0
    v = st.get("r_frame_rate") or ""
    if "/" in v:
        a, b = v.split("/")[:2]
        try:
            fps = float(a) / float(b or 1)
        except ZeroDivisionError:
            fps = 0.0
    return {"dur": float((j.get("format") or {}).get("duration") or 0.0), "fps": fps}


def load_styles() -> Dict[str, str]:
    p = ROOT / "forensic" / "style_sources.json"
    return json.loads(p.read_text(encoding="utf-8"))["styles"]


def render_one(style_id: str, out: Path) -> tuple:
    t0 = time.time()
    cmd = [PY, str(ROOT / "dev_tools" / "render_replica_from_draft.py"),
           "--style-id", style_id, "--out", str(out)]
    # 让一让：别抢满 CPU
    if sys.platform == "darwin":
        cmd = ["nice", "-n", "5"] + cmd
    r = subprocess.run(cmd, capture_output=True, text=True)
    sec = round(time.time() - t0, 1)
    if r.returncode != 0:
        return None, sec, r.stdout[-800:] + r.stderr[-1500:]
    unreal = ""
    for line in (r.stdout or "").splitlines():
        if "unrealized=" in line:
            unreal = line.split("unrealized=", 1)[1].strip()
    return unreal, sec, ""


def metrics_one(style_id: str, orig: Path, rep: Path, out: Path) -> Optional[Dict]:
    cmd = [PY, str(ROOT / "dev_tools" / "make_sidebyside.py"),
           "--style-id", style_id, "--orig", str(orig), "--rep", str(rep),
           "--out", str(out / "sidebyside"), "--diff"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    mf = out / "sidebyside" / f"{style_id}_metrics.json"
    if r.returncode != 0 or not mf.is_file():
        return None
    return json.loads(mf.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="24 套复刻批处理")
    ap.add_argument("--styles", default="", help="逗号分隔；默认全部 24 套")
    ap.add_argument("--out", default=str(TMP_OUT))
    ap.add_argument("--metrics-only", action="store_true")
    ap.add_argument("--force", action="store_true", help="已存在也重做")
    ap.add_argument("--no-video", action="store_true", help="只算指标不出并排片")
    args = ap.parse_args()

    out = Path(args.out)
    (out / "sidebyside").mkdir(parents=True, exist_ok=True)
    sources = load_styles()
    ids = [s.strip() for s in args.styles.split(",") if s.strip()] or sorted(sources)
    rows: List[Dict] = []
    for sid in ids:
        orig = ORIG_DIR / f"{sid}.mov"
        if not orig.is_file():
            rows.append({"style_id": sid, "draft": sources.get(sid, ""),
                         "status": "no_orig", "flags": f"缺原版 {orig}"})
            continue
        info = probe(orig)
        rep = out / f"{sid}_replica.mp4"
        unreal, sec, err = "", 0.0, ""
        need_render = args.force or not rep.is_file() or rep.stat().st_size < 10_000
        if not args.metrics_only and need_render:
            print(f"[batch] {sid} 渲染中（{info.get('dur', 0):.1f}s 原片）…", flush=True)
            unreal, sec, err = render_one(sid, out)
        m = metrics_one(sid, orig, rep, out)
        row = {"style_id": sid, "draft": sources.get(sid, ""),
               "dur_orig": round(info.get("dur", 0.0), 2),
               "render_sec": sec, "unrealized": unreal or "",
               "status": "ok" if m else (err or "metrics_failed")}
        if m:
            for k in ("verdict", "ssim_mean", "subtitle_band_rel_max", "grade_max_level_delta",
                      "cut_max_delta_s", "lufs_delta", "duration_delta_s"):
                row[k] = m.get(k)
            row["flags"] = " | ".join(m.get("flags") or [])
        rows.append(row)
        print(f"[batch] {sid} -> {row.get('verdict', row['status'])}  "
              f"ssim={row.get('ssim_mean')}  grade={row.get('grade_max_level_delta')}", flush=True)

    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    with SUMMARY.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    ok = sum(1 for r in rows if r.get("verdict") == "PASS")
    print(f"\n汇总 -> {SUMMARY}   PASS {ok}/{len(rows)}")


if __name__ == "__main__":
    main()
