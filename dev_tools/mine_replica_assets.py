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

24 套批量跑的时候还有第二种用法（推荐）：先把整个草稿库探一遍存成**共用的素材目录**，
之后每套风格只是从目录里挑子集——人脸检测（每条素材 Apple Vision 抽 3 帧）是这个工具里
唯一的重活，24 套各挖一遍等于把同一批检测重复做 24 次。

    python3 dev_tools/mine_replica_assets.py --build-catalog /tmp/pool_catalog.json
    python3 dev_tools/mine_replica_assets.py --catalog /tmp/pool_catalog.json \
        --style-id style_03 --assets /tmp/assets_03.json --pool /tmp/pool_03.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

    # ---- 复合片段的子层也要算进「同一瞬间叠几层」----
    # 只看顶层会把 style_23 这类算成「只要 4 条素材」：它有 12 个复合片段，每个子时间线 25 段，
    # 子层按「父段时长/子时间线时长」压进父段窗口后大量重叠，实测 45 个子层拿不到素材，
    # 子时间线渲成空片（concat 没有流）→ 整片渲染失败。
    def _expand(child_segs: List[dict], base: float, k: float, depth: int = 0) -> List[tuple]:
        out: List[tuple] = []
        for s in child_segs:
            st = base + float(s.get("start_sec") or 0.0) * k
            du = float(s.get("duration_sec") or 0.0) * k
            kind = s.get("kind") or ""
            if kind != "compound":  # 复合片段父段只是容器（像素在子层里）
                out.append((st, du, kind, s.get("asset_role") or s.get("role") or ""))
            comp = s.get("compound")
            if comp and depth < 3:
                sub_total = float(comp.get("duration_sec") or 0.0)
                kk = (float(s.get("duration_sec") or 0.0) / sub_total) if sub_total > 1e-6 else 1.0
                out.extend(_expand(comp.get("segments") or [], st, k * kk, depth + 1))
        return out

    layers = _expand(tpl.get("segments") or [], 0.0, 1.0)

    def _peak_of_items(items: List[tuple]) -> int:
        ev: List[tuple] = []
        for a, du, _k, _r in items:
            ev.append((a + 1e-6, 1))
            ev.append((a + du - 1e-6, -1))
        ev.sort()
        c = p = 0
        for _t, d in ev:
            c += d
            p = max(p, c)
        return p

    video_layers = [x for x in layers if x[2] == "video"]
    photo_layers_all = [x for x in layers if x[2] == "photo"]
    peak_video = max(peak, _peak_of_items(video_layers))
    peak_photo_all = max(photo_peak, _peak_of_items(photo_layers_all))
    need_assets = max(2, peak_video)

    # 单个复合片段里最多有几层视频子层。套用端给子层用的 avoid 是「父段整个时间窗内已用的素材」，
    # 所以一个复合片段最多只能填满「池子条数」那么多子层；池子小了就会出现
    # `compound_skipped: sub_video_layer_no_asset` —— 子片拼不成片，整部片渲染失败
    # （实测 style_23：12 个复合片段 × 25 个子层，池子 4 条 → 45 层拿不到素材）。
    def _sub_layers_of(comp: dict, depth: int = 0) -> int:
        n = 0
        for s in comp.get("segments") or []:
            if (s.get("kind") or "") in ("video", "photo"):
                n += 1
            sub = s.get("compound")
            if sub and depth < 3:
                n += _sub_layers_of(sub, depth + 1)
        return n

    max_sub = 0
    for s in tpl.get("segments") or []:
        comp = s.get("compound")
        if comp:
            max_sub = max(max_sub, _sub_layers_of(comp))

    return {
        "video_slots": len(segs),
        "driver_segments": sum(1 for s in segs if s.get("track_index") == driver_idx),
        "max_concurrent_video_layers": peak,
        "max_concurrent_video_layers_with_compounds": peak_video,
        "compound_sub_video_layers": len(video_layers) - len(segs),
        "compound_sub_photo_layers": len(photo_layers_all),
        "max_sub_video_layers_per_compound": max_sub,
        "distinct_assets_needed": need_assets,
        "a_roll_want": max(need_assets + 2, max_sub + 2),
        "b_roll_want": max(need_assets + 2, max_sub + 2),
        "max_concurrent_photo_layers": peak_photo_all,
        "photo_distinct_needed": photo_distinct,
        "photo_want": max(photo_distinct, peak_photo_all + 2) if peak_photo_all else 0,
        # 配音效/配乐池子用：短片一个音效反复响无所谓，57 个音效位的长片拿 4 条音效凑会很假。
        # 同时给模板时长：BGM 池要能盖住最长那条片（126.9s），否则一段一条地回卷。
        "template_duration_sec": float(tpl.get("duration_sec") or 0.0),
        # 音效/配乐位按**段**数：音频段的 role 统一是 audio，真角色在 asset_role（bgm/sfx）
        "sfx_segments": sum(1 for s in tpl.get("segments") or [] if s.get("asset_role") == "sfx"),
        "bgm_segments": sum(1 for s in tpl.get("segments") or [] if s.get("asset_role") == "bgm"),
        "a_roll_segments": sum(1 for s in tpl.get("segments") or [] if s.get("role") == "a_roll"),
        "b_roll_segments": sum(1 for s in tpl.get("segments") or [] if s.get("role") == "b_roll"),
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


# ------------------------------------------------------------ 共用素材目录（24 套共用一份）
#
# 为什么要有目录：24 套逐套挖素材时，同一批候选会被重复探 24 遍，而人脸检测（每条素材
# Apple Vision 抽 3 帧）是这里最贵的一步。把「草稿库有哪些素材、各是什么货」探一次存成目录，
# 之后每套风格只是从目录里挑子集（纯计算，毫秒级）。

CATALOG_VERSION = 1
PHOTO_MIN_BYTES = 300_000     # 小图多是图标/装饰角标，不是「照片」
PHOTO_MIN_SHORT = 480         # 短边过小：拿去做拼贴底板会糊成一块


def _sig(row: dict) -> str:
    """同源指纹：同一段源文件常被复制进多套草稿（字节数 + 时长 + 分辨率都一样）。

    素材池要求「互不同源」，所以同一段源在不同草稿里的拷贝只能留一条。
    """
    return f"{int(row.get('bytes') or 0)}|{round(float(row.get('dur') or 0.0), 2)}|{row.get('w')}x{row.get('h')}"


def build_catalog(*, min_video_bytes: int = 1_000_000, min_video_dur: float = 1.0,
                  log=None) -> dict:
    """把草稿库整扫一遍：每条视频/音频/静图探时长、分辨率、音轨、人脸，存成目录。

    不排除任何草稿（排除的是「模版源草稿」，那是**逐套**的事，在挑素材时按 draft_ref 过滤）。
    """
    from replica_template import _replica_module
    root = Path(_replica_module()._discover_root("drafts"))
    if not root.is_dir():
        raise SystemExit(f"找不到草稿库根目录（{root.name}），用 JY_DRAFT_ROOT 指定")
    dirs = _draft_dirs(root, exclude="\x00")  # 不排除任何一套
    say = log or (lambda msg: print(f"[mine] {msg}"))
    say(f"草稿库 {len(dirs)} 套 → 开始探素材")

    videos: List[dict] = []
    t0 = time.time()
    n_v = 0
    for d in dirs:
        for p in _media_in(d, "video", VIDEO_EXTS):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size < min_video_bytes:
                continue
            info = _probe(p)
            if info["duration"] < min_video_dur or info["width"] <= 0:
                continue
            anchor = _face_anchor(p, info["duration"])
            row = {
                "path": str(p), "draft_ref": _ref(d.name),
                "dur": round(info["duration"], 2), "w": int(info["width"]),
                "h": int(info["height"]), "fps": info["fps"],
                "rotation": int(info.get("rotation") or 0),
                "audio": bool(info["audio"]), "bytes": size,
                "size_mb": round(size / 1e6, 1),
                "face": bool(anchor),
                "face_area": round(float(anchor["w"]) * float(anchor["h"]), 4) if anchor else 0.0,
                "face_frames": int(anchor.get("faces") or 0) if anchor else 0,
                "face_cx": anchor.get("cx") if anchor else None,
                "face_cy": anchor.get("cy") if anchor else None,
            }
            if anchor and info["audio"]:
                row["audio_db"] = _mean_volume_db(p)
            videos.append(row)
            n_v += 1
            if n_v % 20 == 0:
                say(f"  视频 {n_v} 条…（{time.time() - t0:.0f}s）")
    videos.sort(key=lambda r: -r["dur"])
    say(f"视频 {len(videos)} 条（{time.time() - t0:.0f}s）")

    audios: List[dict] = []
    for d in dirs:
        for p in _media_in(d, "audio", AUDIO_EXTS):
            info = _probe(p)
            # 只要两种：20s 以上的音乐床、3s 以内的短音效；中间那种（口播原声）不进池
            if info["duration"] >= 20.0:
                kind = "music"
            elif 0.15 <= info["duration"] <= 3.0:
                kind = "sfx"
            else:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            audios.append({"path": str(p), "draft_ref": _ref(d.name),
                           "name_ref": _ref(p.name), "dur": round(info["duration"], 2),
                           "kind": kind, "channels": None, "bytes": size})
    audios.sort(key=lambda r: -r["dur"])
    say(f"音频 {len(audios)} 条（music {sum(1 for r in audios if r['kind'] == 'music')} / "
        f"sfx {sum(1 for r in audios if r['kind'] == 'sfx')}）")

    images: List[dict] = []
    for d in dirs:
        for p in _media_in(d, "video", IMAGE_EXTS):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size < PHOTO_MIN_BYTES:
                continue
            info = _probe(p)
            w, h = int(info.get("width") or 0), int(info.get("height") or 0)
            if w < PHOTO_MIN_SHORT or h < PHOTO_MIN_SHORT:
                continue
            images.append({"path": str(p), "draft_ref": _ref(d.name), "w": w, "h": h,
                           "bytes": size, "size_mb": round(size / 1e6, 1)})
    images.sort(key=lambda r: -r["bytes"])
    say(f"静图 {len(images)} 张（≥{PHOTO_MIN_BYTES // 1000}KB 且短边≥{PHOTO_MIN_SHORT}）")

    return {
        "version": CATALOG_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "drafts_scanned": len(dirs),
        "videos": videos, "audios": audios, "images": images,
        "dedup": _catalog_dedup_stats(videos),
    }


def _catalog_dedup_stats(videos: List[dict]) -> dict:
    """目录里「同源拷贝」有多少（同一段源被多套草稿各存一份）。"""
    sigs: Dict[str, int] = {}
    for r in videos:
        sigs[_sig(r)] = sigs.get(_sig(r), 0) + 1
    dup = sum(n - 1 for n in sigs.values() if n > 1)
    return {"videos_distinct_signature": len(sigs), "videos_copies_of_same_source": dup}


# ------------------------------------------------------------ 从目录里挑一套风格的素材

def _dedupe_same_source(rows: List[dict]) -> Tuple[List[dict], int]:
    """同源拷贝只留一条（留字节数最大的那份，质量最可靠）。返回 (去重后, 丢掉几条)。"""
    best: Dict[str, dict] = {}
    for r in rows:
        s = _sig(r)
        cur = best.get(s)
        if cur is None or int(r.get("bytes") or 0) > int(cur.get("bytes") or 0):
            best[s] = r
    out = list(best.values())
    return out, len(rows) - len(out)


def _pick_diverse(pool: List[dict], want_n: int, *, min_total_sec: float = 0.0,
                  max_n: int = 40) -> List[dict]:
    """先一条草稿一条素材（互补背景），条数与总时长都够了就停；不够再放开同草稿。"""
    picked: List[dict] = []
    used_paths: set = set()
    seen_drafts: set = set()
    total = 0.0

    def _done() -> bool:
        return len(picked) >= want_n and total >= min_total_sec

    for r in pool:
        if len(picked) >= max_n or _done():
            break
        if r["draft_ref"] in seen_drafts or r["path"] in used_paths:
            continue
        picked.append(r)
        used_paths.add(r["path"])
        seen_drafts.add(r["draft_ref"])
        total += float(r.get("dur") or 0.0)
    for r in pool:
        if len(picked) >= max_n or _done():
            break
        if r["path"] in used_paths:
            continue
        picked.append(r)
        used_paths.add(r["path"])
        total += float(r.get("dur") or 0.0)
    return picked


def _role_totals(rows: List[dict]) -> dict:
    durs = [float(r.get("dur") or 0.0) for r in rows]
    return {
        "count": len(rows),
        "total_sec": round(sum(durs), 2),
        "min_dur": round(min(durs), 2) if durs else 0.0,
        "max_dur": round(max(durs), 2) if durs else 0.0,
        "distinct_drafts": len({r.get("draft_ref") for r in rows}),
        "distinct_sources": len({_sig(r) for r in rows}),
    }


def select_from_catalog(catalog: dict, style_id: str, need: Optional[dict], *,
                        src_draft_ref: Optional[str] = None,
                        want_a: int = 0, want_b: int = 0, want_photo: int = 0,
                        want_sfx: int = 4, min_a_sec: float = 200.0, min_b_sec: float = 200.0,
                        min_bgm_sec: float = 300.0, min_photo: int = 20,
                        max_a: int = 48, max_b: int = 48, max_photo: int = 30,
                        max_bgm: int = 4) -> Tuple[dict, dict, List[str]]:
    """目录 → 一套风格的 {角色: [路径]} + 脱敏素材清单。

    池子大小不是拍脑袋：`need`（template_need）算了「同一瞬间最多几层叠在一起」「照片全片
    要用几张不同图」，池子至少要那么多**互不同源**的素材，否则套用时就得留空或同源重影。
    另外按「总时长」兜一层：A-roll 池 < 200s 时，长片（style_03 是 126.9s）里同一段素材会被
    反复回卷（asset_wrapped），所以要求池子总时长也够。
    """
    notes: List[str] = []
    need = need or {}
    videos = [r for r in catalog.get("videos") or [] if r.get("draft_ref") != src_draft_ref]
    videos, dropped = _dedupe_same_source(videos)
    notes.append(f"候选视频 {len(videos)} 条互不同源（另有 {dropped} 条是同源拷贝，已去重）")

    aroll = [r for r in videos if r.get("face") and int(r.get("face_frames") or 0) >= 2
             and r.get("audio")
             and (r.get("audio_db") is None or float(r.get("audio_db")) > -32.0)
             and float(r.get("dur") or 0) >= 1.5]
    # 排序：脸够大又不过大 → 时长长 → 优先能撑长片的
    aroll.sort(key=lambda r: (-(0.01 <= float(r.get("face_area") or 0) <= 0.20),
                              -float(r.get("face_area") or 0), -float(r.get("dur") or 0)))
    broll = [r for r in videos if not r.get("face") and float(r.get("dur") or 0) >= 4.0]
    broll.sort(key=lambda r: -float(r.get("dur") or 0))

    tgt_a = max(want_a, int(need.get("a_roll_want") or 0), 2)
    tgt_b = max(want_b, int(need.get("b_roll_want") or 0), 2)
    a_pick = _pick_diverse(aroll, tgt_a, min_total_sec=min_a_sec, max_n=max_a)
    b_pick = _pick_diverse(broll, tgt_b, min_total_sec=min_b_sec, max_n=max_b)

    photos_all = [r for r in catalog.get("images") or [] if r.get("draft_ref") != src_draft_ref]
    tgt_photo = max(want_photo, int(need.get("photo_want") or 0), 0)
    if tgt_photo:
        tgt_photo = max(tgt_photo, min_photo)
    # 照片层的要求是「全片唯一」：池子里有几张，就有几层能落地，所以按需要张数取，别多取
    p_pick = _pick_diverse(photos_all, tgt_photo, min_total_sec=0.0, max_n=min(max_photo, max(tgt_photo, min_photo)))

    audios = [r for r in catalog.get("audios") or [] if r.get("draft_ref") != src_draft_ref]
    music = [r for r in audios if r.get("kind") == "music"]
    music.sort(key=lambda r: -float(r.get("dur") or 0))
    # BGM 池：先按「一条草稿一首、时长长的先上」凑到 min_bgm_sec；凑不够再放开同草稿。
    # （模版里 bgm 段通常只有 1~3 段，池子里多备几首不会让它中途换歌：一段一次 fill。）
    bgm_pick: List[dict] = []
    bgm_total = 0.0
    seen_md: set = set()
    for r in music:
        if len(bgm_pick) >= max_bgm or bgm_total >= min_bgm_sec:
            break
        if r["draft_ref"] in seen_md:
            continue
        bgm_pick.append(r)
        seen_md.add(r["draft_ref"])
        bgm_total += float(r.get("dur") or 0)
    if bgm_total < min_bgm_sec:
        for r in music:
            if len(bgm_pick) >= max_bgm or bgm_total >= min_bgm_sec:
                break
            if any(p["path"] == r["path"] for p in bgm_pick):
                continue
            bgm_pick.append(r)
            bgm_total += float(r.get("dur") or 0)
    sfx_all = [r for r in audios if r.get("kind") == "sfx"]
    sfx_all.sort(key=lambda r: -float(r.get("dur") or 0))
    # 音效条数跟「模板里有多少个音效位」走（57 个音效位拿 4 条会一直重复同一个“叮”）
    sfx_need = max(want_sfx, 4)
    if need.get("sfx_segments"):
        sfx_need = max(sfx_need, min(12, int(need["sfx_segments"]) // 3))
    sfx_pick: List[dict] = []
    seen_sfx: set = set()
    for r in sfx_all:
        if len(sfx_pick) >= sfx_need:
            break
        if r["name_ref"] in seen_sfx:
            continue
        seen_sfx.add(r["name_ref"])
        sfx_pick.append(r)

    assets = {
        "a_roll": [r["path"] for r in a_pick],
        "b_roll": [r["path"] for r in b_pick],
        "photo": [r["path"] for r in p_pick],
        "bgm": [r["path"] for r in bgm_pick],
        "sfx": [r["path"] for r in sfx_pick],
    }

    totals = {"a_roll": _role_totals(a_pick), "b_roll": _role_totals(b_pick),
              "photo": _role_totals(p_pick), "bgm": _role_totals(bgm_pick),
              "sfx": _role_totals(sfx_pick)}
    shortfall = {
        "a_roll": max(0, int(need.get("distinct_assets_needed") or 0) - len(a_pick)),
        "b_roll": max(0, int(need.get("distinct_assets_needed") or 0) - len(b_pick)),
        "a_roll_sec": round(max(0.0, min_a_sec - totals["a_roll"]["total_sec"]), 1),
        "b_roll_sec": round(max(0.0, min_b_sec - totals["b_roll"]["total_sec"]), 1),
        "photo": max(0, tgt_photo - len(p_pick)),
        "bgm_sec": round(max(0.0, min_bgm_sec - totals["bgm"]["total_sec"]), 1),
    }
    if shortfall["a_roll"]:
        notes.append(f"A-roll 池只有 {len(a_pick)} 条，比叠层峰值要求少 {shortfall['a_roll']} 条")
    if shortfall["a_roll_sec"]:
        notes.append(f"A-roll 池总时长 {totals['a_roll']['total_sec']}s，没到 {min_a_sec}s（长片会回卷）")
    if shortfall["photo"]:
        notes.append(f"照片池 {len(p_pick)} 张，比需要少 {shortfall['photo']} 张（照片层会留空）")
    notes.append(f"池子：A {len(a_pick)} 条/{totals['a_roll']['total_sec']}s · "
                 f"B {len(b_pick)} 条/{totals['b_roll']['total_sec']}s · 照片 {len(p_pick)} 张 · "
                 f"BGM {len(bgm_pick)} 首/{totals['bgm']['total_sec']}s · 音效 {len(sfx_pick)} 条")

    pool = {
        "style_id": style_id,
        "catalog_built_at": catalog.get("built_at"),
        "catalog_version": catalog.get("version"),
        "candidates_considered": len(videos),
        "candidates_dropped_same_source": dropped,
        "need": need,
        "totals": totals,
        "shortfall": shortfall,
        "limits": {"min_a_sec": min_a_sec, "min_b_sec": min_b_sec,
                   "min_bgm_sec": min_bgm_sec, "min_photo": min_photo},
        "a_roll": [{"id": f"ar-{i + 1:02d}", **{k: r.get(k) for k in
                     ("draft_ref", "dur", "w", "h", "fps", "rotation", "audio", "face",
                      "face_area", "face_frames", "face_cx", "face_cy", "size_mb", "audio_db")}}
                   for i, r in enumerate(a_pick)],
        "b_roll": [{"id": f"br-{i + 1:02d}", **{k: r.get(k) for k in
                     ("draft_ref", "dur", "w", "h", "fps", "rotation", "audio", "face",
                      "face_area", "size_mb")}}
                   for i, r in enumerate(b_pick)],
        "photo": [{"id": f"ph-{i + 1:02d}", **{k: r.get(k) for k in
                    ("draft_ref", "w", "h", "size_mb")}} for i, r in enumerate(p_pick)],
        "bgm": [{"id": f"bgm-{i + 1:02d}", **{k: r.get(k) for k in ("draft_ref", "dur")}}
                for i, r in enumerate(bgm_pick)],
        "sfx": [{"id": f"sfx-{i + 1:02d}", **{k: r.get(k) for k in ("draft_ref", "dur")}}
                for i, r in enumerate(sfx_pick)],
        "rejected": {"aroll_candidates": len(aroll), "broll_candidates": len(broll),
                     "photo_candidates": len(photos_all), "music_candidates": len(music),
                     "sfx_candidates": len(sfx_all)},
        "notes": notes,
    }
    return assets, pool, notes


def _need_of_style(style_id: str) -> Tuple[Optional[dict], Optional[str]]:
    """按风格 id 现提一份模版算需求（模板源草稿名 → 哈希 ref，用来排除自家素材）。

    提不出来不抛（那条错误该由下游 verify 报），退成「不给需求、不排除草稿」。
    """
    from replica_template import resolve_draft_by_style, extract_template
    try:
        draft = resolve_draft_by_style(style_id)
        tpl = extract_template(draft)
        return template_need(tpl), _ref(draft.parent.name)
    except BaseException as exc:
        print(f"[mine] {style_id} 提模版失败，素材池按默认需求挑：{type(exc).__name__}: {exc}")
        return None, None


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
    # ---- 共用素材目录（24 套共用一份，见文件头第二种用法）----
    ap.add_argument("--catalog", default=None, help="已有的共用素材目录 JSON（给了就走目录挑素材）")
    ap.add_argument("--build-catalog", default=None, metavar="OUT",
                    help="把草稿库整扫一遍写成目录 JSON（只建目录，不挑素材）")
    ap.add_argument("--min-aroll-sec", type=float, default=200.0,
                    help="A-roll 池总时长下限（秒）：短片够了，长片才会大量回卷）")
    ap.add_argument("--min-broll-sec", type=float, default=200.0)
    ap.add_argument("--min-bgm-sec", type=float, default=300.0, help="BGM 池总时长下限（秒）")
    ap.add_argument("--min-photo", type=int, default=20, help="照片池张数下限")
    ap.add_argument("--max-pool", type=int, default=48,
                    help="A/B-roll 池条数上限（复合片段子层多时要调大：套用端对子层的 avoid 是父段整个窗口）")
    args = ap.parse_args()

    if args.build_catalog:
        catalog = build_catalog()
        out_p = Path(args.build_catalog)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
        print(f"[mine] 素材目录 -> {out_p.name} · 视频 {len(catalog['videos'])} / "
              f"音频 {len(catalog['audios'])} / 静图 {len(catalog['images'])} · "
              f"同源拷贝去重 {catalog['dedup']['videos_copies_of_same_source']} 条")
        return 0

    need = None
    want_a, want_b = args.want_aroll, args.want_broll
    want_photo = args.want_photo
    if args.template:
        need = template_need(json.loads(Path(args.template).read_text(encoding="utf-8")))
        want_a = max(want_a, int(need["a_roll_want"]))
        want_b = max(want_b, int(need["b_roll_want"]))
        want_photo = max(want_photo, int(need.get("photo_want") or 0))

    if args.catalog:
        # 目录模式：需求从风格自己的模版现算（不依赖外面先跑一步提模版），源草稿按哈希排除
        catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        if catalog.get("version") != CATALOG_VERSION:
            print(f"[mine] 警告：目录版本 {catalog.get('version')} != {CATALOG_VERSION}，建议 --build-catalog 重建")
        src_ref = None
        if not args.template:
            need, src_ref = _need_of_style(args.style_id)
        else:
            try:
                from replica_template import resolve_draft_by_style
                src_ref = _ref(resolve_draft_by_style(args.style_id).parent.name)
            except SystemExit:
                src_ref = None
        assets, pool, notes = select_from_catalog(
            catalog, args.style_id, need, src_draft_ref=src_ref,
            want_a=args.want_aroll, want_b=args.want_broll, want_photo=args.want_photo,
            want_sfx=args.want_sfx, min_a_sec=args.min_aroll_sec, min_b_sec=args.min_broll_sec,
            min_bgm_sec=args.min_bgm_sec, min_photo=args.min_photo,
            max_a=args.max_pool, max_b=args.max_pool)
    else:
        assets, pool, notes = mine(args.style_id, want_a=want_a, want_b=want_b,
                                   want_sfx=args.want_sfx, min_mb=args.min_mb,
                                   max_candidates=args.max_candidates, want_photo=want_photo)
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
    if args.catalog:
        t = pool.get("totals") or {}
        print(f"[mine] 池子总计: A {(t.get('a_roll') or {}).get('total_sec')}s / "
              f"B {(t.get('b_roll') or {}).get('total_sec')}s / 照片 {(t.get('photo') or {}).get('count')} 张 / "
              f"BGM {(t.get('bgm') or {}).get('total_sec')}s · 差量 {pool.get('shortfall')}")
    return 0 if (assets.get("a_roll") and assets.get("b_roll")) else 1


if __name__ == "__main__":
    sys.exit(main())
