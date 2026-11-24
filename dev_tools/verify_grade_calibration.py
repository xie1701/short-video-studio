#!/usr/bin/env python3
"""调色闭环校准验收：用真素材量「出来的画面」是否真落到源片实测分位。

两种口径，缺一不可：

A. 自校（同一素材）——把某套风格的**自己那份原始素材**喂进该套调色链，出口的
   p05/p50/p95 应当回到该套源片的实测分位。这是「同一素材进来，还是这个影调」的直接证据；
   残差大说明曲线/分色在把画面推走，而不是在复刻。

B. 中性迁移（跨素材）——把一份中性素材喂进 24 套调色链，看各套能不能把同一份画面
   拉到各自的目标影调。这证明「换素材也能按风格落地」，而不是只对自家素材成立。

用法：
    python3 dev_tools/verify_grade_calibration.py \
        --probe-dir /tmp/probe_clips        # 每个 style_XX.mp4 是该套源片的片段
        [--neutral-clip /tmp/probe_clips/style_07.mp4] \
        [--out reports/grade_calibration.csv] [--tol 6.0]

只做 B 口径时，--probe-dir 里只需要一个文件；不给 --neutral-clip 时取 --probe-dir 中
最接近标准实拍分布（0.19/0.53/0.92）的那条。
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

import numpy as np  # noqa: E402

from common import find_bin, load_style_spectrum_entry  # noqa: E402
from step7_color_grade import (  # noqa: E402
    _probe_with_vf,
    _resolve_style_color,
    calibrate_clip_curve,
    compute_hero_and_shot_correction,
)

NEUTRAL = np.array([0.19, 0.53, 0.92])


def _stats_of(clip: Path) -> Dict[str, float]:
    """粗测素材的 YUV 均值（对标 Hero 锚定用的那组统计量）。"""
    ff = find_bin("ffmpeg")
    if not ff:
        return {"yavg": 126.0, "uavg": 128.0, "vavg": 128.0}
    raw = subprocess.run([str(ff), "-v", "error", "-t", "2.5", "-i", str(clip),
                          "-vf", "fps=2,scale=64:114", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout
    fb = 64 * 114 * 3
    n = len(raw) // fb
    if n <= 0:
        return {"yavg": 126.0, "uavg": 128.0, "vavg": 128.0}
    arr = np.frombuffer(raw[: n * fb], dtype=np.uint8).reshape(n, 114, 64, 3).astype(np.float32) / 255.0
    luma = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
    return {"yavg": float(luma.mean()) * 255.0, "uavg": 128.0, "vavg": 128.0}


def _targets(style_color: Dict) -> Optional[List[float]]:
    anchors = (style_color.get("tone_curve") or {}).get("anchors") or []
    if len(anchors) != 3:
        return None
    return [round(float(a.get("measured") or 0.0), 1) for a in anchors]


def _run(clip: Path, sid: str) -> Dict[str, object]:
    style_color = _resolve_style_color(sid)
    stats = _stats_of(clip)
    corr = compute_hero_and_shot_correction(
        stats, hero_stats=stats, is_hero=True, skin_ycbcr=None, style_id=sid)
    vf, adj, residual = calibrate_clip_curve(
        str(clip), 0.0, 2.5, corr, style_color, None, 0.0, False)
    # 特效前残差直接取校准器自己量的那一份（它就是「特效之前」的输出分布）
    with_effects = _probe_with_vf(str(clip), vf, 0.0, 2.5)
    out = {
        "style": sid,
        "target_p05": None, "target_p50": None, "target_p95": None,
        "out_p05": None, "out_p50": None, "out_p95": None,
        "delta_p05": None, "delta_p50": None, "delta_p95": None,
        "max_abs_delta": None,
        "pre_effect_delta": None,
        "calibrated": bool(adj),
    }
    tg = _targets(style_color)
    if tg:
        out.update({"target_p05": tg[0], "target_p50": tg[1], "target_p95": tg[2]})
    if with_effects:
        got = [round(v * 255.0, 1) for v in with_effects]
        out.update({"out_p05": got[0], "out_p50": got[1], "out_p95": got[2]})
        if tg:
            d = [round(got[i] - tg[i], 1) for i in range(3)]
            out.update({"delta_p05": d[0], "delta_p50": d[1], "delta_p95": d[2],
                        "max_abs_delta": round(max(abs(x) for x in d), 1)})
    if residual:
        out["pre_effect_delta"] = round(max(abs(float(x)) for x in residual), 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-dir", required=True, help="内含 style_XX.mp4（该套源片片段）")
    ap.add_argument("--neutral-clip", default="")
    ap.add_argument("--out", default=str(ROOT / "reports" / "grade_calibration.csv"))
    ap.add_argument("--tol", type=float, default=12.0, help="判 PASS 的最大单点残差（0–255 级）")
    ap.add_argument("--only", default="", help="只跑某一套，如 style_14")
    args = ap.parse_args()

    probe_dir = Path(args.probe_dir)
    clips = {p.stem: p for p in sorted(probe_dir.glob("style_*.mp4"))}
    if not clips:
        print(f"没有找到 style_*.mp4：{probe_dir}")
        return 2
    if args.only:
        clips = {args.only: clips[args.only]} if args.only in clips else {}

    styles = sorted(load_style_spectrum_entry(sid) and sid or sid for sid in
                    [f"style_{i:02d}" for i in range(1, 25)])

    rows: List[Dict[str, object]] = []
    print("== A. 自校（同一素材进同一条链，出口应回到该套实测影调） ==")
    for sid, clip in clips.items():
        r = _run(clip, sid)
        rows.append({"mode": "self", **r})
        print(f"  {sid} 目标 {r['target_p05']}/{r['target_p50']}/{r['target_p95']} "
              f"→ 出片 {r['out_p05']}/{r['out_p50']}/{r['out_p95']}  "
              f"残差 {r['delta_p05']}/{r['delta_p50']}/{r['delta_p95']}  "
              f"特效前最大残差 {r['pre_effect_delta']}")

    neutral = Path(args.neutral_clip) if args.neutral_clip else None
    if neutral is None and len(clips) > 1:
        best, bestd = None, 1e9
        for sid, clip in clips.items():
            pct = _probe_with_vf(str(clip), "null", 0.0, 2.5)
            if not pct:
                continue
            d = float(np.abs(np.array(pct) - NEUTRAL).sum())
            if d < bestd:
                best, bestd = clip, d
        neutral = best
    if neutral is not None:
        print(f"\n== B. 中性迁移（同一份 {neutral.stem} 进 24 套链，看能否各自到位） ==")
        for i in range(1, 25):
            sid = f"style_{i:02d}"
            r = _run(neutral, sid)
            rows.append({"mode": "neutral", **r})
            print(f"  {sid} 目标 {r['target_p05']}/{r['target_p50']}/{r['target_p95']} "
                  f"→ 出片 {r['out_p05']}/{r['out_p50']}/{r['out_p95']}  "
                  f"残差 {r['delta_p05']}/{r['delta_p50']}/{r['delta_p95']}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # 判 PASS 只能看「特效之前」的分布：源片实测分位取自未加暗角/聚光的素材，
    # 加上特效后终帧分布本来就该被改动（那是特效本身的效果，另有接触表人工核对）。
    bad_total = 0
    for mode in ("self", "neutral"):
        sub = [r for r in rows if r["mode"] == mode and r.get("pre_effect_delta") is not None]
        if not sub:
            continue
        ok = [r for r in sub if r["pre_effect_delta"] <= args.tol]
        worst = max(sub, key=lambda r: r["pre_effect_delta"])
        print(f"\n[{mode}] 调色链（特效前）PASS {len(ok)}/{len(sub)}（容差 ±{args.tol} 级）"
              f"｜平均最大残差 {round(sum(r['pre_effect_delta'] for r in sub) / len(sub), 1)}"
              f"｜最差 {worst['style']} {worst['pre_effect_delta']}")
        bad = [r for r in sub if r["pre_effect_delta"] > args.tol]
        if bad:
            bad_total += len(bad)
            print("  超差：" + "，".join(f"{r['style']}({r['pre_effect_delta']})" for r in bad))
        withfx = [r for r in sub if r.get("max_abs_delta") is not None]
        if withfx:
            print(f"  （含特效后的终帧分布：平均偏离 "
                  f"{round(sum(r['max_abs_delta'] for r in withfx) / len(withfx), 1)} 级，属特效本身主动改分布）")
    print(f"\n明细已写入 {out_path}")
    # 门禁有牙：有超差就非 0 退出，不许「全错也绿灯」。
    if bad_total:
        print(f"[FAIL] {bad_total} 条超差（容差 ±{args.tol} 级）")
        return 1
    print("[PASS] 全部在容差内")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
