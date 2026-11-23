#!/usr/bin/env python3
"""从本机草稿库里**挑真实实拍素材**给复刻模版套用（合成 testsrc2/分形那一套不算验证）。

为什么要有这个工具：上一轮端到端用 ffmpeg 造的合成图（testsrc2/细胞自动机/分形）跑通了链路，
但成片完全看不出真实效果——没人脸、没有真场景、没有真声音。这个工具按「角色」从**真人草稿库**
里捞素材，并且只输出脱敏信息（id + 时长 + 分辨率 + 有没有人脸）：

  a_roll  有人脸、有声音的实拍段（口播）——Apple Vision 抽 3 帧检测，至少 2 帧有脸
  b_roll  没检出人脸的实拍段（产品或场景）
  photo   静图（jpg/png）：**复合片段里的照片拼贴层专用**。照片层换素材必须还是图片，
          拿视频顶就等于把「照片翻页」变成「视频墙」，结构没重建
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
# 静图：复合片段里的「照片翻页/拼贴底板」层要的就是这些（**不能拿视频顶**）
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
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


def template_need(tpl: dict) -> dict:
    """模版 → 素材池至少要凑多少条**互不相同**的素材。

    为什么不能只按「段数」算：主轨的 13 段是**顺序播放**的，同一条素材可以在不同时间复用；
    真正卡住的是「同一瞬间上下叠着几层」——那些层必须各自用不同素材，否则就是同源重影。
    所以需求 = 全片「同时可见的视频层数」峰值。多留 2 条余量，轮转时才不必原路复用。

    photo 单独算：复合片段里的照片拼贴层是**同时**铺在一条子时间线上的（原版 5 张/7 张
    错开入场、大半时间重叠），照片层只能用图片，所以 photo 池要按子层的最多同时层数算。
    """
    segs = [s for s in tpl.get("segments") or [] if s.get("kind") in ("video", "photo")]
    events: List[tuple] = []
    for s in segs:
        a = float(s.get("start_sec") or 0.0)
        b = a + float(s.get("duration_sec") or 0.0)
        # 半开区间：紧接的两段（上一段在 t 结束、下一段在 t 开始）不算同时存在，
        # 否则每次切点都会被多算一层（实测得到 5 层，实际叠层最多只有 3 层）。
        events.append((a + 1e-6, 1))
        events.append((b - 1e-6, -1))
    events.sort()
    cur = peak = 0
    for _t, d in events:
        cur += d
        peak = max(peak, cur)
    driver_idx = next((t["index"] for t in tpl.get("tracks") or [] if t.get("is_driver")), None)

    def _peak_of(child_segs: List[dict]) -> int:
        ev: List[tuple] = []
        for s in child_segs:
            a = float(s.get("start_sec") or 0.0)
            b = a + float(s.get("duration_sec") or 0.0)
            ev.append((a + 1e-6, 1))
            ev.append((b - 1e-6, -1))
        ev.sort()
        c = p = 0
        for _t, d in ev:
            c += d
            p = max(p, c)
        return p

    photo_peak = 0
    photo_fps: set = set()

    def _walk(child_segs: List[dict], depth: int = 0) -> None:
        nonlocal photo_peak
        for s in child_segs:
            comp = s.get("compound")
            if not comp:
                continue
            sub = comp.get("segments") or []
            photos = [x for x in sub if (x.get("asset_role") or x.get("kind")) == "photo"]
            photo_peak = max(photo_peak, _peak_of(photos))
            for x in photos:
                fp = x.get("material_fingerprint")
                photo_fps.add(fp or f"anon:{depth}:{x.get('key')}")
            _walk(sub, depth + 1)

    _walk(tpl.get("segments") or [])
    # 照片池要多大：**不能只看同时层数**。套用端对照片层施行「全片唯一」（同一张图在整条
    # 时间线上只能用一次，跨复合片段、跨层级）——所以需求是「原版一共用了几张**不同**的照片」，
    # 而不是「同一时刻最多几层」。原来按同时层数算（峰值 7 → 要 9 张），实采 14 张仍然不够
    # 16 个照片层，最后两层只能留空（photo_layer_no_image），拼贴就少了带子。
    photo_distinct = len(photo_fps) if photo_fps else photo_peak
    return {
        "video_slots": len(segs),
        "driver_segments": sum(1 for s in segs if s.get("track_index") == driver_idx),
        "max_concurrent_video_layers": peak,
        "distinct_assets_needed": max(2, peak),
        "a_roll_want": max(2, peak) + 2,
        "b_roll_want": max(2, peak) + 2,
        "max_concurrent_photo_layers": photo_peak,
        "photo_distinct_needed": photo_distinct,
        "photo_want": max(photo_distinct, max(0, photo_peak) + 2) if photo_peak else 0,
    }


def mine(style_id: str, *, want_a: int, want_b: int, want_sfx: int,
         min_mb: float, max_candidates: int, want_photo: int = 0) -> tuple:
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

    # ---- 静图：复合片段照片拼贴层专用（优先大图＋跨草稿多样） ----
    photo_cands: List[tuple] = []
    for d in dirs:
        for p in _media_in(d, "video", IMAGE_EXTS):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size < 300_000:      # 小图多是图标/装饰角标，不是「照片」
                continue
            photo_cands.append((size, d, p))
    photo_cands.sort(reverse=True, key=lambda x: x[0])
    photo_rows: List[dict] = []
    for size, d, p in photo_cands[: max(40, want_photo * 4)]:
        info = _probe(p)
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        if w < 480 or h < 480:      # 短边过小：拿去做拼贴底板会糊成一块
            continue
        photo_rows.append({"path": str(p), "draft_ref": _ref(d.name), "w": w, "h": h,
                           "size_mb": round(size / 1e6, 1)})
    photo_pick = _pick(photo_rows, want_photo) if want_photo else []
    notes.append(f"静图候选 {len(photo_rows)} 张（≥300KB 且短边≥480）→ 取 {len(photo_pick)} 张做复合片段照片层")

    assets = {
        "a_roll": [r["path"] for r in a_pick],
        "b_roll": [r["path"] for r in b_pick],
        "photo": [r["path"] for r in photo_pick],
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
        "photo": [{"id": f"ph-{i + 1:02d}", **{k: r[k] for k in ("draft_ref", "w", "h", "size_mb")}}
                  for i, r in enumerate(photo_pick)],
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
    ap.add_argument("--want-photo", type=int, default=14,
                    help="静图池张数（复合片段照片拼贴层用）；给 --template 时按子层同时层数上调")
    ap.add_argument("--min-mb", type=float, default=8.0)
    ap.add_argument("--max-candidates", type=int, default=30)
    ap.add_argument("--template", default=None,
                    help="模版 JSON：按其「同时叠层数」自动定池子大小（不给就用 want-* 默认值）")
    args = ap.parse_args()

    need = None
    want_a, want_b = args.want_aroll, args.want_broll
    want_photo = args.want_photo
    if args.template:
        need = template_need(json.loads(Path(args.template).read_text(encoding="utf-8")))
        want_a = max(want_a, int(need["a_roll_want"]))
        want_b = max(want_b, int(need["b_roll_want"]))
        want_photo = max(want_photo, int(need.get("photo_want") or 0))

    assets, pool, notes = mine(args.style_id, want_a=want_a, want_b=want_b, want_sfx=args.want_sfx,
                               min_mb=args.min_mb, max_candidates=args.max_candidates,
                               want_photo=want_photo)
    if need:
        short_a = max(0, int(need["distinct_assets_needed"]) - len(assets["a_roll"]))
        short_b = max(0, int(need["distinct_assets_needed"]) - len(assets["b_roll"]))
        pool["need"] = {**need, "want_a": want_a, "want_b": want_b, "want_photo": want_photo,
                       "got_a": len(assets["a_roll"]), "got_b": len(assets["b_roll"]),
                       "got_photo": len(assets.get("photo") or []),
                       "shortfall": {"a_roll": short_a, "b_roll": short_b,
                                     "photo": max(0, int(need.get("photo_distinct_needed")
                                           or need.get("max_concurrent_photo_layers") or 0)
                                                  - len(assets.get("photo") or []))}}
        notes.append(f"模版同时叠层峰值 {need['max_concurrent_video_layers']} 层 → 至少要 "
                     f"{need['distinct_assets_needed']} 条不同素材；实采 a_roll {len(assets['a_roll'])} 条 / "
                     f"b_roll {len(assets['b_roll'])} 条")
        if need.get("max_concurrent_photo_layers"):
            notes.append(f"复合片段照片层同时最多 {need['max_concurrent_photo_layers']} 层，"
                         f"但全片一共用了 {need.get('photo_distinct_needed')} 张不同原图（照片层全片唯一）→ "
                         f"photo 池要 {need.get('photo_want')} 张图；实采 {len(assets.get('photo') or [])} 张")
        if short_a or short_b:
            notes.append(f"素材不足：还差 a_roll {short_a} 条 / b_roll {short_b} 条，"
                         f"套用时会按硬约束留空并在 overlay_left_blank 里逐条记")
            print(f"[mine] 素材不足，{max(short_a, short_b)} 个位可能留空")
    if args.assets:
        Path(args.assets).parent.mkdir(parents=True, exist_ok=True)
        Path(args.assets).write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.pool:
        Path(args.pool).parent.mkdir(parents=True, exist_ok=True)
        Path(args.pool).write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")

    for note in notes:
        print(f"[mine] {note}")
    for role in ("a_roll", "b_roll", "photo", "bgm", "sfx"):
        for row in pool.get(role) or []:
            if role in ("a_roll", "b_roll"):
                print(f"[mine] {role:7s} {row['id']} {row.get('draft_ref')} {row.get('dur')}s "
                      f"{row.get('w')}x{row.get('h')} face={row.get('face')} area={row.get('face_area')}")
            elif role == "photo":
                print(f"[mine] {role:7s} {row['id']} {row.get('draft_ref')} "
                      f"{row.get('w')}x{row.get('h')} {row.get('size_mb')}MB")
            else:
                print(f"[mine] {role:7s} {row['id']} {row.get('draft_ref')} {row.get('dur')}s")
    return 0 if (assets.get("a_roll") and assets.get("b_roll")) else 1


if __name__ == "__main__":
    sys.exit(main())
