#!/usr/bin/env python3
"""从本机草稿库里**挑真实实拍素材**给复刻模版套用（合成 testsrc2/分形那一套不算验证）。

为什么要有这个工具：上一轮端到端用 ffmpeg 造的合成图（testsrc2/细胞自动机/分形）跑通了链路，
但成片完全看不出真实效果——没人脸、没有真场景、没有真声音。这个工具按「角色」从**真人草稿库**
里捞素材，并且只输出脱敏信息（id + 时长 + 分辨率 + 有没有人脸）：

  a_roll  有人脸、有声音的实拍段（口播）——Apple Vision 抽 3 帧检测，至少 2 帧有脸
  b_roll  没检出人脸的实拍段（产品或场景）
  bgm     草稿库里 20s 以上的音乐
  sfx     3s 以内的短音效

反解/去敏做法：
  · 草稿根不写死：走 `render_replica_from_draft._discover_root("drafts")`（环境变量优先，
    否则按结构在 ~/Movies 下自动发现）——Windows/Linux 布局不同也能跑；
  · 模版**源草稿**按哈希定位后取目录名排除，代码里不留草稿名；
  · 隐藏目录（回收站/云缓存）整个跳过；
  · 输出只给 `draft_ref`（目录名哈希前 8 位）与文件 id，不写草稿名与绝对路径到 pool 里
    （`--assets` 那份 JSON 是给套用器吃的，必然带路径，属于运行时输入、不进仓库）。

用法：
    python3 dev_tools/mine_replica_assets.py --style-id style_21 \
        --assets /tmp/assets.json --pool /tmp/asset_pool.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "dev_tools"))

from common import maybe_reexec_in_venv  # noqa: E402

maybe_reexec_in_venv()

VIDEO_EXTS = (".mp4", ".mov", ".m4v")
AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".aac", ".flac")
SKIP_DIRS = ("matting", "audioAlg", "videoAlg", "subquad", "subdraft", "combination",
             "qr_upload", "digitalHuman", "common_attachment", "Resources")


def _probe(path: Path) -> dict:
    """时长 + 显示分辨率 + 帧率 + 有没有音轨 + 旋转（与套用端 probe_media 同一口径）。"""
    try:
        from apply_replica_template import probe_media
        info = probe_media(path)
        out = {"duration": info["duration"], "width": info["width"], "height": info["height"],
               "rotation": int(info.get("rotation") or 0), "fps": "", "audio": False}
    except Exception:
        out = {"duration": 0.0, "width": 0, "height": 0, "rotation": 0, "fps": "", "audio": False}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height,r_frame_rate:format=duration",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=120)
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        v = next((s for s in streams if s.get("codec_type") == "video"), {})
        out["fps"] = str(v.get("r_frame_rate") or "")
        out["audio"] = any(s.get("codec_type") == "audio" for s in streams)
        if not out["duration"]:
            out["duration"] = float((data.get("format") or {}).get("duration") or 0.0)
    except Exception:
        pass
    return out


def _mean_volume_db(path: Path) -> Optional[float]:
    """平均音压（dB）：用来判断「这条素材里真的有人在说话」而不是静帧空镜。"""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180)
        for line in (proc.stderr or "").splitlines():
            if "mean_volume:" in line:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    except Exception:
        return None
    return None


def _face_anchor(path: Path, dur: float):
    """人脸锚点（归一化）：face_framing 抽 3 帧 + Apple Vision，取面积最大那张脸的中位位置。"""
    try:
        from face_framing import anchor_for
        at = min(max(0.5, dur * 0.5), max(0.5, dur - 0.5))
        return anchor_for(str(path), at, min(3.0, max(1.0, dur * 0.5)), cache_scope="mine")
    except Exception:
        return None


def _draft_dirs(root: Path, exclude: str) -> List[Path]:
    out = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return out
    for d in children:
        if not d.is_dir() or d.name.startswith(".") or d.name == exclude:
            continue
        out.append(d)
    return out


def _media_in(draft_dir: Path, sub: str, exts: tuple) -> List[Path]:
    base = draft_dir / "materials" / sub
    if not base.is_dir():
        return []
    try:
        return [p for p in sorted(base.iterdir())
                if p.is_file() and p.suffix.casefold() in exts]
    except OSError:
        return []


def _ref(name: str) -> str:
    """草稿名 → 脱敏引用（报告里只出现这个）。"""
    return "d" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:7]


def mine(style_id: str, *, want_a: int, want_b: int, want_sfx: int,
         min_mb: float, max_candidates: int) -> tuple:
    """→ (assets{role:[path]}, pool{reach 素材清单（脱敏）}, notes[])"""
    from replica_template import resolve_draft_by_style, _replica_module

    src_draft_dir = resolve_draft_by_style(style_id).parent
    root = Path(_replica_module()._discover_root("drafts"))
    notes: List[str] = []
    if not root.is_dir():
        raise SystemExit(f"找不到草稿库根目录（{root.name}），用 JY_DRAFT_ROOT 指定")

    dirs = _draft_dirs(root, src_draft_dir.name)
    notes.append(f"草稿库 {len(dirs)} 套可用草稿（已排除模版源草稿 {_ref(src_draft_dir.name)}）")

    # ---- 视频候选 ----
    cands: List[tuple] = []
    for d in dirs:
        for p in _media_in(d, "video", VIDEO_EXTS):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size >= min_mb * 1_000_000:
                cands.append((size, d, p))
    cands.sort(reverse=True, key=lambda x: x[0])
    cands = cands[:max_candidates]
    notes.append(f"视频候选 {len(cands)} 条（≥{min_mb:g}MB，按体积取前 {max_candidates}）")

    rows: List[dict] = []
    for size, d, p in cands:
        info = _probe(p)
        if info["duration"] < 5.0 or info["width"] <= 0:
            continue
        anchor = _face_anchor(p, info["duration"])
        row = {
            "path": str(p), "draft_ref": _ref(d.name), "draft_dir": str(d),
            "dur": round(info["duration"], 2), "w": info["width"], "h": info["height"],
            "fps": info["fps"], "rotation": info.get("rotation") or 0,
            "audio": info["audio"], "size_mb": round(size / 1e6, 1),
            "face": bool(anchor),
            "face_area": round(float(anchor["w"]) * float(anchor["h"]), 4) if anchor else 0.0,
            "face_frames": int(anchor.get("faces") or 0) if anchor else 0,
            "face_cx": anchor.get("cx") if anchor else None,
            "face_cy": anchor.get("cy") if anchor else None,
        }
        if anchor and info["audio"]:
            row["audio_db"] = _mean_volume_db(p)
        rows.append(row)

    # a_roll：有脸（≥2/3 帧）+ 有音轨 + 说话音压不是静音；按「脸够大又不过大」排序
    aroll = [r for r in rows
             if r["face"] and r["face_frames"] >= 2 and r["audio"]
             and (r.get("audio_db") is None or float(r["audio_db"]) > -32.0)]
    aroll.sort(key=lambda r: (-(0.01 <= r["face_area"] <= 0.20), -r["face_area"], -r["dur"]))
    # b_roll：0/3 帧有脸（产品或场景），时长 ≥5s
    broll = [r for r in rows if not r["face"] and r["dur"] >= 5.0]
    broll.sort(key=lambda r: -r["dur"])

    def _pick(pool: List[dict], n: int) -> List[dict]:
        """先保证草稿多样性（同一条草稿最多取一条），不够再放开。"""
        picked, seen = [], set()
        for r in pool:
            if len(picked) >= n:
                break
            if r["draft_ref"] in seen:
                continue
            picked.append(r)
            seen.add(r["draft_ref"])
        for r in pool:
            if len(picked) >= n:
                break
            if r not in picked:
                picked.append(r)
        return picked[:n]

    a_pick = _pick(aroll, want_a)
    b_pick = _pick(broll, want_b)

    # ---- 音频：BGM（≥20s，取最长）+ 短音效（≤3s） ----
    music: List[tuple] = []
    sfx: List[tuple] = []
    for d in dirs:
        for p in _media_in(d, "audio", AUDIO_EXTS):
            info = _probe(p)
            if info["duration"] >= 20.0:
                music.append((info["duration"], d, p))
            elif 0.15 <= info["duration"] <= 3.0:
                sfx.append((info["duration"], d, p))
    music.sort(reverse=True, key=lambda x: x[0])
    sfx.sort(reverse=True, key=lambda x: x[0])
    # 去重：同一首音效常被多条草稿重复引用，别把 repeats 当多个素材
    seen_sfx = set()
    sfx_unique = []
    for dur, d, p in sfx:
        if p.name in seen_sfx:
            continue
        seen_sfx.add(p.name)
        sfx_unique.append((dur, d, p))
    sfx = sfx_unique
    bgm_pick = music[:1]
    sfx_pick = _pick([{"path": str(p), "draft_ref": _ref(d.name), "dur": round(dur, 2), "draft_dir": str(d)}
                      for dur, d, p in sfx], want_sfx)

    assets = {
        "a_roll": [r["path"] for r in a_pick],
        "b_roll": [r["path"] for r in b_pick],
        "bgm": [str(p) for _, _, p in bgm_pick],
        "sfx": [r["path"] for r in sfx_pick],
    }

    pool = {
        "style_id": style_id,
        "drafts_root_found": True,
        "candidates_considered": len(rows),
        "a_roll": [{"id": f"ar-{i + 1:02d}", **{k: r[k] for k in
                     ("draft_ref", "dur", "w", "h", "fps", "rotation", "audio", "face", "face_area",
                      "face_frames", "face_cx", "face_cy", "size_mb")},
                    **({"audio_db": r["audio_db"]} if "audio_db" in r else {})}
                   for i, r in enumerate(a_pick)],
        "b_roll": [{"id": f"br-{i + 1:02d}", **{k: r[k] for k in
                     ("draft_ref", "dur", "w", "h", "fps", "rotation", "audio", "face", "face_area",
                      "size_mb")}}
                   for i, r in enumerate(b_pick)],
        "bgm": [{"id": f"bgm-{i + 1:02d}", "draft_ref": _ref(d.name), "dur": round(dur, 2)}
                for i, (dur, d, _p) in enumerate(bgm_pick)],
        "sfx": [{"id": f"sfx-{i + 1:02d}", **{k: r[k] for k in ("draft_ref", "dur")}}
                for i, r in enumerate(sfx_pick)],
        "rejected": {"no_face_broll_candidates": len(broll), "aroll_candidates": len(aroll)},
        "notes": notes,
    }
    return assets, pool, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="从本机草稿库挑真实实拍素材（脱敏清单 + 套用素材表）")
    ap.add_argument("--style-id", default="style_21")
    ap.add_argument("--assets", default=None, help="写出套用器吃的 {角色: [路径...]} JSON")
    ap.add_argument("--pool", default=None, help="写出脱敏素材清单 JSON（报告用）")
    ap.add_argument("--want-aroll", type=int, default=3)
    ap.add_argument("--want-broll", type=int, default=3)
    ap.add_argument("--want-sfx", type=int, default=4)
    ap.add_argument("--min-mb", type=float, default=8.0)
    ap.add_argument("--max-candidates", type=int, default=30)
    args = ap.parse_args()

    assets, pool, notes = mine(args.style_id, want_a=args.want_aroll, want_b=args.want_broll,
                               want_sfx=args.want_sfx, min_mb=args.min_mb,
                               max_candidates=args.max_candidates)
    if args.assets:
        Path(args.assets).parent.mkdir(parents=True, exist_ok=True)
        Path(args.assets).write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.pool:
        Path(args.pool).parent.mkdir(parents=True, exist_ok=True)
        Path(args.pool).write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")

    for note in notes:
        print(f"[mine] {note}")
    for role in ("a_roll", "b_roll", "bgm", "sfx"):
        for row in pool.get(role) or []:
            if role in ("a_roll", "b_roll"):
                print(f"[mine] {role:7s} {row['id']} {row.get('draft_ref')} {row.get('dur')}s "
                      f"{row.get('w')}x{row.get('h')} face={row.get('face')} area={row.get('face_area')}")
            else:
                print(f"[mine] {role:7s} {row['id']} {row.get('draft_ref')} {row.get('dur')}s")
    return 0 if (assets.get("a_roll") and assets.get("b_roll")) else 1


if __name__ == "__main__":
    sys.exit(main())
