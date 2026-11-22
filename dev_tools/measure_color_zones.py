#!/usr/bin/env python3
"""从原草稿的真实素材量出 24 套风格的色彩分区实测值。

背景：库里的 `shadow_rgb / midtone_rgb / highlight_rgb` 有 18 套是历史占位值
（暗部恒 32/34/38、中调等于均值、高光恒 225/224/220），不足以支撑逐套调色。
本脚本直接解码剪映草稿目录里的原始视频，按与 `analyze_style_spectrum.py`
完全相同的口径重算：

  - luma 用 BT.709 权重，取每帧 p05/p50/p95 后跨帧取均值
  - 暗部/中间调/高光 = 亮度 <0.28 / 0.28–0.72 / >0.72 像素的 RGB 均值
  - 饱和 = (max-min)/max 的逐帧均值；暖度 = mean(R-B)×255
  - 主色 = 32 级量化直方图合并后的前 5 色

产物 forensic/color_zones.json（只在本地取证用，不随技能包分发）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
# 草稿根目录不写进仓库：运行前用环境变量指到本机剪映草稿根目录
DRAFT_ROOT = Path(os.environ.get("JY_DRAFT_ROOT") or "")
SRC_MAP = ROOT / "forensic" / "style_sources.json"
OUT = ROOT / "forensic" / "color_zones.json"

W_S, H_S = 180, 320
SAMPLE_FPS = 1.0
MAX_FILES = 4
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".MP4", ".MOV"}


def _resolve_dir(style_id: str, name: str) -> Optional[Path]:
    for cand in (DRAFT_ROOT / name, DRAFT_ROOT / name.replace(" ", "")):
        if cand.is_dir():
            return cand
    return None


def _draft_doc(style_id: str, name: str) -> Optional[Dict[str, Any]]:
    for f in (ROOT / "forensic" / "decrypted" / name / "draft_content.json",
              ROOT / "forensic" / "decrypted" / name / "__latest_close.json"):
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    return None


def _v1_usage(doc: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """按主视频轨实际占用时长排序素材：片头彩卡/贴图不该主导整片色调。"""
    use: Dict[str, int] = {}
    if not doc:
        return use
    for t in doc.get("tracks") or []:
        if t.get("type") != "video":
            continue
        for seg in t.get("segments") or []:
            mid = str(seg.get("material_id") or "")
            use[mid] = use.get(mid, 0) + int((seg.get("target_timerange") or {}).get("duration") or 0)
    return use


def _pick_files(draft_dir: Path, doc: Optional[Dict[str, Any]]) -> List[tuple]:
    vdir = draft_dir / "materials" / "video"
    if not vdir.is_dir():
        return []
    use = _v1_usage(doc)
    mats = list((doc.get("materials") or {}).get("videos") or []) if doc else []
    mats.sort(key=lambda v: -use.get(str(v.get("id")), 0))
    picked: List[tuple] = []
    seen: set = set()
    for v in mats:
        nm = str(v.get("material_name") or "")
        for cand in (vdir / nm, vdir / (nm + ".mp4"), vdir / nm.replace(".mp4", ".MP4")):
            if cand.is_file() and cand.suffix in VIDEO_EXT and cand.stat().st_size > 200_000 and cand not in seen:
                picked.append((cand, max(1, use.get(str(v.get("id")), 0) // 1000)))
                seen.add(cand)
                break
        if len(picked) >= MAX_FILES:
            break
    if not picked:
        pool = sorted(
            (p for p in vdir.iterdir() if p.is_file() and p.suffix in VIDEO_EXT and p.stat().st_size > 200_000),
            key=lambda p: -p.stat().st_size,
        )
        picked = [(pool[0], 1)] if pool else []
    return picked


def _sample(path: Path) -> Optional[Dict[str, Any]]:
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-vf", f"fps={SAMPLE_FPS},scale={W_S}:{H_S}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    frame_bytes = W_S * H_S * 3
    n = len(proc.stdout) // frame_bytes
    if n <= 0:
        return None
    y_vals: List[float] = []
    p05: List[float] = []
    p50: List[float] = []
    p95: List[float] = []
    sats: List[float] = []
    warms: List[float] = []
    dark: List[float] = []
    bright: List[float] = []
    zone_sums = {"shadow": np.zeros(3), "mid": np.zeros(3), "high": np.zeros(3)}
    zone_cnt = {"shadow": 0, "mid": 0, "high": 0}
    pixels: List[np.ndarray] = []
    for i in range(n):
        arr = np.frombuffer(proc.stdout[i * frame_bytes:(i + 1) * frame_bytes], dtype=np.uint8)
        arr = arr.reshape((H_S, W_S, 3)).astype(np.float32) / 255.0
        luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
        y_vals.append(float(np.mean(luma)))
        a5, a50, a95 = np.percentile(luma, [5, 50, 95])
        p05.append(float(a5)); p50.append(float(a50)); p95.append(float(a95))
        dark.append(float(np.mean(luma < 50.0 / 255.0)))
        bright.append(float(np.mean(luma > 205.0 / 255.0)))
        cmax, cmin = np.max(arr, axis=2), np.min(arr, axis=2)
        sats.append(float(np.mean((cmax - cmin) / np.maximum(cmax, 1e-5))))
        warms.append(float(np.mean(arr[:, :, 0] - arr[:, :, 2])))
        flat_rgb, flat_y = arr.reshape(-1, 3), luma.reshape(-1)
        for zname, mask in (("shadow", flat_y < 0.28),
                            ("mid", (flat_y >= 0.28) & (flat_y <= 0.72)),
                            ("high", flat_y > 0.72)):
            c = int(np.sum(mask))
            if c:
                zone_sums[zname] += np.sum(flat_rgb[mask], axis=0)
                zone_cnt[zname] += c
        pixels.append((flat_rgb[::8] * 255.0).astype(np.uint8))
    zones = {
        z: ([round(float(x * 255.0), 1) for x in zone_sums[z] / zone_cnt[z]] if zone_cnt[z] else None)
        for z in ("shadow", "mid", "high")
    }
    return {
        "file": path.name,
        "frames": n,
        "y_mean_255": round(float(np.mean(y_vals)) * 255.0, 1),
        "y_p05_255": round(float(np.mean(p05)) * 255.0, 1),
        "y_p50_255": round(float(np.mean(p50)) * 255.0, 1),
        "y_p95_255": round(float(np.mean(p95)) * 255.0, 1),
        "dark_ratio_pct": round(float(np.mean(dark)) * 100.0, 1),
        "bright_ratio_pct": round(float(np.mean(bright)) * 100.0, 1),
        "sat_mean_pct": round(float(np.mean(sats)) * 100.0, 1),
        "warmth_index": round(float(np.mean(warms)) * 255.0, 2),
        "zone_rgb": zones,
        "_pixels": np.concatenate(pixels, axis=0) if pixels else None,
    }


def _top5(px: np.ndarray) -> List[str]:
    if px is None or not len(px):
        return []
    q = (px // 32).astype(np.int32)
    keys = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    out: List[str] = []
    picked: List[np.ndarray] = []
    for idx in np.argsort(-counts):
        mask = keys == int(uniq[idx])
        c = np.mean(px[mask], axis=0)
        if all(float(np.linalg.norm(c - p)) >= 34.0 for p in picked):
            picked.append(c)
            out.append("#%02X%02X%02X" % tuple(int(round(v)) for v in c))
        if len(out) >= 5:
            break
    return out


def _merge(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    w = np.array([float(s.get("weight") or 1.0) for s in samples], dtype=np.float64)
    w = w / w.sum() if w.sum() > 0 else np.ones_like(w) / len(w)

    def avg(key: str) -> float:
        pairs = [(float(s[key]), wi) for s, wi in zip(samples, w) if s.get(key) is not None]
        return round(float(np.average([v for v, _ in pairs], weights=[x for _, x in pairs])), 1) if pairs else 0.0

    zones = {}
    for z in ("shadow", "mid", "high"):
        pairs = [(s["zone_rgb"][z], wi) for s, wi in zip(samples, w) if s.get("zone_rgb", {}).get(z)]
        zones[z] = ([round(float(np.average([t[i] for t, _ in pairs], weights=[x for _, x in pairs])), 1)
                     for i in range(3)] if pairs else None)
    y_mean = avg("y_mean_255"); y05 = avg("y_p05_255"); y50 = avg("y_p50_255"); y95 = avg("y_p95_255")
    warmth = round(float(np.average([s["warmth_index"] for s in samples], weights=w)), 2)
    tone = (
        "暗黑高对比科技调" if y_mean < 87 and (y95 - y05) > 166
        else "深沉低饱和质感调" if y_mean < 107
        else "暖色高饱和门店/人物调" if warmth > 11.5
        else "清透冷中性/极简白调" if warmth < -3.8
        else "均衡自然高通透调"
    )
    return {
        "y_mean_255": y_mean,
        "y_p05_255": y05,
        "y_p50_255": y50,
        "y_p95_255": y95,
        "dynamic_range_255": round(y95 - y05, 1),
        "dark_ratio_pct": avg("dark_ratio_pct"),
        "bright_ratio_pct": avg("bright_ratio_pct"),
        "sat_mean_pct": avg("sat_mean_pct"),
        "warmth_index": warmth,
        "tone_mood": tone,
        "shadow_rgb": zones["shadow"],
        "midtone_rgb": zones["mid"],
        "highlight_rgb": zones["high"],
        "top5_palette_hex": _top5(np.concatenate([s["_pixels"] for s in samples if s.get("_pixels") is not None], axis=0)
                                  if any(s.get("_pixels") is not None for s in samples) else None),
        "source_files": [s["file"] for s in samples],
        "frames_sampled": sum(int(s["frames"]) for s in samples),
    }


def main() -> None:
    if not DRAFT_ROOT.is_dir():
        raise SystemExit("请先设置 JY_DRAFT_ROOT 指向本机剪映草稿根目录（内含各草稿子目录）")
    mapping = json.loads(SRC_MAP.read_text(encoding="utf-8"))["styles"]
    out: Dict[str, Any] = {}
    for sid in sorted(mapping):
        name = mapping[sid]
        draft_dir = _resolve_dir(sid, name)
        if not draft_dir:
            print(f"[skip] {sid} {name}: 草稿目录不存在")
            continue
        files = _pick_files(draft_dir, _draft_doc(sid, name))
        if not files:
            print(f"[skip] {sid} {name}: 无可用视频素材")
            continue
        samples = []
        for f, weight in files:
            one = _sample(f)
            if one:
                one["weight"] = weight
                samples.append(one)
        if not samples:
            print(f"[skip] {sid} {name}: 采样失败")
            continue
        merged = _merge(samples)
        merged["project_dir"] = name
        out[sid] = merged
        print(f"[ok] {sid} y={merged['y_mean_255']} p05/50/95={merged['y_p05_255']}/{merged['y_p50_255']}/{merged['y_p95_255']}"
              f" sat={merged['sat_mean_pct']} warm={merged['warmth_index']}"
              f" zones={merged['shadow_rgb']}|{merged['midtone_rgb']}|{merged['highlight_rgb']}"
              f" files={len(samples)} frames={merged['frames_sampled']}")
    OUT.write_text(json.dumps({"note": "本机实测：24 套风格的源片色彩分区（BT.709 亮度，口径同 analyze_style_spectrum.py）",
                               "styles": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}  ({len(out)}/24 套)")


if __name__ == "__main__":
    main()
