#!/usr/bin/env python3
"""从 24 套剪映源草稿里抽出**真正用过**的音效，替换掉旧的合成音效包。

背景（实测事故）：`assets/sound_vibe/sfx/style_XX_ui.wav` 这类 0.05s、频谱重心 12kHz 的
"叮"其实是 `build_sound_packs.py` 用 `synthesize_sfx_waveform()` 合成出来的电子音；成片里
用户听到的高频串味就是这个。真音效一直躺在源草稿里（`materials.audios[type=sound]`）。

本工具做的事：
1. 读 `forensic/style_sources.json` 拿到 风格 → 本机剪映草稿名；
2. 优先读 `forensic/decrypted/<草稿名>/draft_content.json`（解密明文），没有则退到
   `__latest_close.json` / `template-2.tmp`；
3. 取 `materials.audios` 里 `type == "sound"` 的音频段（music 是 BGM，不算音效），
   按音效名归到 cut/hook/info/accent/receipt/misc 六个角色；
4. 用 ffmpeg 从真草稿 `materials/audio/*.mp3` 里截出草稿实际用到的那一小段，落盘成
   `assets/sound_vibe/sfx/<style_id>/<角色>_<原音效名>.mp3`；
5. 写 `assets/sound_vibe/sfx_truth/<style_id>.json`：每条音效的名称/角色/时长/音量(dB)/
   在源草稿里的落点与「距最近切点的偏移」，以及 `sfx_per_min` 真值与 `gain_db_median`。

源草稿里确实一条音效都没有的风格 → truth 写 `sfx_count: 0`，不产出任何文件。

    python dev_tools/extract_style_sfx.py          # 用仓库专用 venv 的 python
    ... extract_style_sfx.py --style style_21 --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "short-video-studio/assets"
SFX_ROOT = ASSETS / "sound_vibe/sfx"
BGM_ROOT = ASSETS / "sound_vibe/bgm"
TRUTH_ROOT = ASSETS / "sound_vibe/sfx_truth"
STYLE_SOURCES = ROOT / "forensic/style_sources.json"
DECRYPTED = ROOT / "forensic/decrypted"
_JY_APP = "Jianying" + "Pro"          # 剪映桌面版
_JY_DRAFT_DIR = "com." + "lveditor" + ".draft"
DRAFT_ROOT = Path.home() / "Movies" / _JY_APP / "User Data" / "Projects" / _JY_DRAFT_DIR

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")

# 剪映音效名 → 管线角色。顺序即优先级：先判专指（账单/小票）、再判「风铃」这类
# 同时含「铃」的复合词，最后才是「呼/转场」这种宽泛词，避免误归。
ROLE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("receipt", ("账单", "小票")),
    ("accent", ("开瓶塞", "发条", "哇呜", "风铃")),
    ("hook", ("响铃", "铃")),
    ("info", ("翻页", "键盘", "空格")),
    ("cut", ("转场", "呼")),
)
ROLES = ("cut", "hook", "info", "accent", "receipt", "misc")

# 单条提取上限：源草稿里一声音效通常 0.15–1.8s，超过 2s 只会让成片糊成一团
MAX_CLIP_SEC = 1.80
MIN_CLIP_SEC = 0.08
FADE_OUT_SEC = 0.03

PH_RE = re.compile(r"##_draftpath_placeholder_[^#]+_##")
_BAD_CHARS = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')


def classify_role(name: str) -> str:
    """按剪映音效名归类角色（归不了类的进 misc）。"""
    low = str(name)
    for role, keys in ROLE_RULES:
        if any(k in low for k in keys):
            return role
    return "misc"


def sanitize_name(name: str) -> str:
    """音效名 → 可用文件名：去引号/制表符/斜杠等，保留中文与可读性。"""
    s = _BAD_CHARS.sub("_", str(name).strip())
    s = s.replace("“", "").replace("”", "").replace('"', "")
    s = re.sub(r"[\s_]+", "", s)
    s = s.strip("._")
    return s[:48] or "unnamed"


def _find_ffmpeg() -> Optional[str]:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        sys.path.insert(0, str(ROOT / "short-video-studio/scripts"))
        from common import find_bin  # type: ignore

        return find_bin("ffmpeg")
    except Exception:
        return None


def _probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe") or (
        str(Path(_find_ffmpeg()).with_name("ffprobe")) if _find_ffmpeg() else None
    )
    if not ffprobe or not Path(ffprobe).is_file():
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return round(float(out), 3)
    except Exception:
        return 0.0


def load_style_sources() -> Dict[str, str]:
    doc = json.loads(STYLE_SOURCES.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in (doc.get("styles") or {}).items()}


def draft_dir_for(style_id: str) -> Optional[Path]:
    name = load_style_sources().get(style_id)
    if not name:
        return None
    p = DRAFT_ROOT / name
    return p if p.is_dir() else None


def find_draft_json(style_id: str) -> Optional[Path]:
    """解密后的明文草稿：draft_content.json 优先，其次 __latest_close.json / template-2.tmp。"""
    name = load_style_sources().get(style_id)
    if not name:
        return None
    base = DECRYPTED / name
    for fn in ("draft_content.json", "__latest_close.json", "template-2.tmp"):
        cand = base / fn
        if cand.is_file() and cand.stat().st_size > 1024:
            try:
                json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                continue
            return cand
    return None


def resolve_media_path(raw: str, draft_dir: Path) -> Optional[Path]:
    """把草稿里的 `##_draftpath_placeholder_..._##/materials/audio/x.mp3` 还原成实盘路径。"""
    if not raw:
        return None
    s = PH_RE.sub(str(draft_dir), str(raw))
    p = Path(s)
    if not p.is_absolute():
        p = draft_dir / p
    if p.is_file():
        return p
    alt = draft_dir / "materials" / "audio" / Path(str(raw)).name
    return alt if alt.is_file() else None


def materials_index(doc: Dict[str, Any]) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    idx: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for kind, rows in (doc.get("materials") or {}).items():
        if not isinstance(rows, list):
            continue
        for m in rows:
            if isinstance(m, dict) and m.get("id"):
                idx[str(m["id"])] = (kind, m)
    return idx


def cut_points_sec(doc: Dict[str, Any]) -> List[float]:
    """源草稿的切点：所有视频段落的入/出点（音效卡的就是这些缝）。"""
    pts = {0.0}
    for track in doc.get("tracks") or []:
        if str(track.get("type")) != "video":
            continue
        for seg in track.get("segments") or []:
            tr = seg.get("target_timerange") or {}
            try:
                start = float(tr.get("start") or 0.0) / 1e6
                dur = float(tr.get("duration") or 0.0) / 1e6
            except (TypeError, ValueError):
                continue
            if dur <= 0:
                continue
            pts.add(round(start, 4))
            pts.add(round(start + dur, 4))
    try:
        pts.add(round(float(doc.get("duration") or 0) / 1e6, 4))
    except (TypeError, ValueError):
        pass
    return sorted(p for p in pts if p >= 0)


def collect_music_layers(doc: Dict[str, Any], draft_dir: Optional[Path]) -> List[Dict[str, Any]]:
    """草稿里所有 BGM 层（type=music / extract_music，后者是剪映「提取音乐」）。"""
    idx = materials_index(doc)
    layers: List[Dict[str, Any]] = []
    for track in doc.get("tracks") or []:
        if str(track.get("type")) != "audio":
            continue
        for seg in track.get("segments") or []:
            kind, mat = idx.get(str(seg.get("material_id")), ("", {}))
            if kind != "audios" or str(mat.get("type")) not in ("music", "extract_music"):
                continue
            tr = seg.get("target_timerange") or {}
            try:
                t0 = float(tr.get("start") or 0.0) / 1e6
                t_dur = float(tr.get("duration") or 0.0) / 1e6
            except (TypeError, ValueError):
                continue
            if t_dur <= 0:
                continue
            vol = seg.get("volume")
            try:
                vol_v = float(vol) if vol is not None else 1.0
            except (TypeError, ValueError):
                vol_v = 1.0
            layers.append({
                "name": str(mat.get("name") or "").strip(),
                "kind": str(mat.get("type")),
                "start_sec": round(t0, 3),
                "end_sec": round(t0 + t_dur, 3),
                "dur_sec": round(t_dur, 3),
                "volume": round(vol_v, 4),
                "gain_db": round(20.0 * math.log10(max(1e-4, vol_v)), 2),
                "file": resolve_media_path(str(mat.get("path") or ""), draft_dir) if draft_dir else None,
            })
    layers.sort(key=lambda x: (x["start_sec"], x["name"]))
    return layers


def summarize_bgm(layers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把 BGM 层归纳成：主曲 / 双段切换点 / 音量中位数。

    * `bgm_med_vol_db`：所有 BGM 段的音量（dB）中位数，与 forensic 真值一致
      （style_21 = -5.0 dB：主曲 0.318 + 复合片段空音量 1.0 的中位数）。
    * `dual_bgm`：时间线上出现两段以上不同曲子/层（哪怕第二层是无法抽文件的复合片段）。
    * `switch_sec`：**实测**切点，不再拍脑袋写成 3.0s：
        - 顺序交接 → 后一首的入点；
        - 弓子式叠入（短层盖在主曲上）→ 该短层的出点（之后只剩主曲）；
        - 两层都铺到片尾（真正的平行双轨）→ null。
    """
    if not layers:
        return {"status": "none", "reason": "源草稿时间线上没有任何 BGM（music/extract_music）"}

    gains = sorted(float(x["gain_db"]) for x in layers)
    mid = len(gains) // 2
    med = round(gains[mid], 1) if len(gains) % 2 else round((gains[mid - 1] + gains[mid]) / 2.0, 1)

    pieces: Dict[str, Dict[str, Any]] = {}
    for lay in layers:
        key = str(lay["file"]) if lay["file"] else f"no-file::{lay['name']}"
        node = pieces.setdefault(key, {
            "name": lay["name"], "kind": lay["kind"], "file": lay["file"],
            "start_sec": lay["start_sec"], "end_sec": lay["end_sec"], "coverage_sec": 0.0,
        })
        node["start_sec"] = min(float(node["start_sec"]), float(lay["start_sec"]))
        node["end_sec"] = max(float(node["end_sec"]), float(lay["end_sec"]))
        node["coverage_sec"] = round(float(node["coverage_sec"]) + float(lay["dur_sec"]), 3)

    ordered = sorted(pieces.values(), key=lambda p: (p["start_sec"], -p["coverage_sec"]))
    primary = max(ordered, key=lambda p: (p["coverage_sec"], -p["start_sec"]))
    # 覆盖到片尾的那一层才是“主曲”，切点要看它，不是看覆盖面最广的那段
    tail = max(ordered, key=lambda p: (p["end_sec"], p["start_sec"]))
    dual = len(ordered) > 1

    switch: Optional[float] = None
    switch_kind = "none"
    if dual:
        others_end = max(float(p["end_sec"]) for p in ordered if p is not tail)
        if others_end >= float(tail["end_sec"]) - 0.05:
            switch_kind = "parallel_layers"          # 两轨平行铺到片尾，没有切点
        elif others_end <= float(tail["start_sec"]) + 0.05:
            switch = round(float(tail["start_sec"]), 3)
            switch_kind = "sequential_handoff"       # 前一首放完再进主曲
        else:
            switch = round(others_end, 3)
            switch_kind = "intro_overlay_exit"       # 短层退出，之后只剩主曲

    return {
        "status": "source_extracted",
        "name": primary["name"],
        "source_file": primary["file"],
        "med_vol_db": med,
        "dual_bgm": dual,
        "switch_sec": switch,
        "switch_kind": switch_kind,
        "tail_piece": tail["name"],
        "pieces": [
            {"name": p["name"], "kind": p["kind"], "start_sec": p["start_sec"],
             "end_sec": p["end_sec"], "coverage_sec": p["coverage_sec"],
             "extractable": bool(p["file"])}
            for p in ordered
        ],
        "layers": [
            {"name": lay["name"], "start_sec": lay["start_sec"], "dur_sec": lay["dur_sec"],
             "gain_db": lay["gain_db"], "extractable": bool(lay["file"])}
            for lay in layers
        ],
    }


def collect_sound_cues(doc: Dict[str, Any], draft_dir: Optional[Path]) -> List[Dict[str, Any]]:
    """草稿里所有 type=sound 的音频段（按时间排好），带落点/音量/源窗口/源文件。"""
    idx = materials_index(doc)
    cues: List[Dict[str, Any]] = []
    for track in doc.get("tracks") or []:
        if str(track.get("type")) != "audio":
            continue
        for seg in track.get("segments") or []:
            kind, mat = idx.get(str(seg.get("material_id")), ("", {}))
            if kind != "audios" or str(mat.get("type")) != "sound":
                continue
            tr = seg.get("target_timerange") or {}
            sr_ = seg.get("source_timerange") or {}
            try:
                t0 = float(tr.get("start") or 0.0) / 1e6
                t_dur = float(tr.get("duration") or 0.0) / 1e6
                s0 = float(sr_.get("start") or 0.0) / 1e6
                s_dur = float(sr_.get("duration") or 0.0) / 1e6
            except (TypeError, ValueError):
                continue
            if t_dur <= 0:
                continue
            vol = seg.get("volume")
            try:
                vol_v = float(vol) if vol is not None else 1.0
            except (TypeError, ValueError):
                vol_v = 1.0
            raw_path = str(mat.get("path") or "")
            cues.append({
                "name": str(mat.get("name") or "").strip(),
                "role": classify_role(str(mat.get("name") or "")),
                "time_sec": round(t0, 3),
                "target_dur_sec": round(t_dur, 3),
                "source_in_sec": round(s0, 3),
                "source_dur_sec": round(s_dur or t_dur, 3),
                "volume": round(vol_v, 4),
                "gain_db": round(20.0 * math.log10(max(1e-4, vol_v)), 2),
                "material_dur_sec": round(float(mat.get("duration") or 0) / 1e6, 3),
                "src_audio": resolve_media_path(raw_path, draft_dir) if draft_dir else None,
                "raw_path": raw_path,
            })
    cues.sort(key=lambda c: (c["time_sec"], c["name"]))
    return cues


def _extract_clip(src: Path, out: Path, start_sec: float, dur_sec: float, ffmpeg: Optional[str]) -> bool:
    """截取源 mp3 的一段并转成 48k 立体声 mp3；没有 ffmpeg 时退化为整文件拷贝。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    dur_sec = max(MIN_CLIP_SEC, min(MAX_CLIP_SEC, dur_sec))
    if not ffmpeg:
        shutil.copy2(src, out)
        return out.is_file()
    fade_st = max(0.0, dur_sec - FADE_OUT_SEC)
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{max(0.0, start_sec):.3f}", "-t", f"{dur_sec:.3f}",
        "-i", str(src),
        "-af", f"afade=t=out:st={fade_st:.3f}:d={FADE_OUT_SEC:.3f}",
        "-ar", "48000", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except Exception:
        return False
    return out.is_file() and out.stat().st_size > 512


def _sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


_SHA_CACHE: Dict[str, str] = {}
_BGMA_BY_SOURCE: Dict[str, Path] = {}


def install_bgm_file(src: Path, out: Path, force: bool = False) -> Path:
    """把真 BGM 落盘到 sound_vibe/bgm/<style_id>_bgm.mp3（不转调、不载短）。

    为什么要重编一次：源草稿里的“mp3”很多其实是 AAC-in-MP4（容器 mp4、扩展名 mp3），
    librosa/mpg123 读不了，step3 的节拍检测会默默回落到 120 BPM 网格，踩点就全错。
    转成真 mp3 后 librosa 能正常唱出 BPM（例：style_21 → 99.4 BPM）。

    两套风格用同一首源曲时（例：6 套都用 Push Up）不重复占盘，直接硬链接。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file() and not force:
        return out
    sha = _SHA_CACHE.get(str(src)) or _sha1_of(src)
    _SHA_CACHE[str(src)] = sha
    prev = _BGMA_BY_SOURCE.get(sha)
    if prev is not None and prev.is_file():
        try:
            out.unlink(missing_ok=True)
            os.link(prev, out)
            return out
        except OSError:
            pass

    ffmpeg = _find_ffmpeg()
    out.unlink(missing_ok=True)
    ok = False
    if ffmpeg:
        try:
            subprocess.run([
                ffmpeg, "-y", "-v", "error", "-i", str(src), "-vn",
                "-ar", "48000", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k",
                str(out),
            ], check=True, capture_output=True)
            ok = out.is_file() and out.stat().st_size > 4096
        except Exception:
            ok = False
    if not ok:
        suffix = src.suffix.lower() or ".mp3"
        fallback = out.with_suffix(suffix)
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.unlink(missing_ok=True)
        shutil.copy2(src, fallback)
        _BGMA_BY_SOURCE[sha] = fallback
        return fallback
    _BGMA_BY_SOURCE[sha] = out
    return out


def extract_style(style_id: str, force: bool = False) -> Dict[str, Any]:
    """单套风格：抽真音效 + 返回 truth 文档。"""
    draft_name = load_style_sources().get(style_id) or ""
    truth: Dict[str, Any] = {
        "style_id": style_id,
        "source_ref": None,
        "draft_duration_sec": 0.0,
        "sfx_count": 0,
        "sfx_per_min": 0.0,
        "unique_sound_count": 0,
        "gain_db_median": 0.0,
        "roles": {},
        "files": [],
        "cues": [],
        "missing_files": [],
        "bgm": {"status": "none"},
        "bgm_disabled_reason": None,
    }
    jp = find_draft_json(style_id)
    ddir = draft_dir_for(style_id)
    if jp is None or ddir is None:
        truth["error"] = "找不到源草稿（decrypted 明文或本机草稿目录缺失）"
        return truth
    doc = json.loads(jp.read_text(encoding="utf-8"))
    # 仓内产物不能带本机路径/私人草稿名：只留风格 ID 与私有映射表的指针，真名在 forensic/ 里
    truth["source_ref"] = f"forensic/style_sources.json#styles.{style_id}"
    try:
        truth["draft_duration_sec"] = round(float(doc.get("duration") or 0) / 1e6, 3)
    except (TypeError, ValueError):
        truth["draft_duration_sec"] = 0.0

    # ── BGM：源草稿里真正在用的那几首（含剪映「提取音乐」）──
    bgm = summarize_bgm(collect_music_layers(doc, ddir))
    primary_src = bgm.pop("source_file", None)
    if bgm.get("status") == "source_extracted" and primary_src is not None:
        # 统一落成真 mp3（源文件可能是 AAC 容器，librosa 读不了会拖坏节拍检测）
        out_bgm = install_bgm_file(Path(primary_src), BGM_ROOT / f"{style_id}_bgm.mp3", force=force)
        bgm["file"] = f"sound_vibe/bgm/{out_bgm.name}"
        bgm["duration_sec"] = _probe_duration(out_bgm)
        bgm["license"] = "source_draft_local_only"  # 剪映曲库原曲：仅限本机/本人项目使用，不随包分发
    else:
        bgm["file"] = None
        bgm.setdefault("status", "none")
        truth["bgm_disabled_reason"] = bgm.get("reason") or f"{style_id} 源草稿里没有可用的 BGM 文件"
    truth["bgm"] = bgm

    cues = collect_sound_cues(doc, ddir)
    cut_pts = cut_points_sec(doc)
    usable = []
    for c in cues:
        if c["src_audio"] is None:
            truth["missing_files"].append({"name": c["name"], "raw_path": c["raw_path"][:200]})
            continue
        nearest = min(cut_pts, key=lambda p: abs(p - c["time_sec"])) if cut_pts else c["time_sec"]
        c_out = {k: v for k, v in c.items() if k not in ("src_audio", "raw_path")}
        c_out["nearest_cut_sec"] = round(nearest, 3)
        c_out["offset_from_cut_sec"] = round(c["time_sec"] - nearest, 3)
        c_out["src_audio"] = c["src_audio"].name
        usable.append((c, c_out))

    dur_sec = truth["draft_duration_sec"] or 0.0
    if dur_sec > 0:
        truth["sfx_per_min"] = round(len(usable) * 60.0 / dur_sec, 1)
    truth["sfx_count"] = len(usable)
    truth["cues"] = [c_out for _c, c_out in usable]
    if usable:
        gains = sorted(c["gain_db"] for c, _ in usable)
        mid = len(gains) // 2
        truth["gain_db_median"] = (
            round(gains[mid], 2) if len(gains) % 2 else round((gains[mid - 1] + gains[mid]) / 2.0, 2)
        )

    if not usable:
        return truth

    ffmpeg = _find_ffmpeg()
    out_dir = SFX_ROOT / style_id
    seen: Dict[str, str] = {}
    files: List[Dict[str, Any]] = []
    for c, _c_out in usable:
        base = sanitize_name(c["name"])
        fname = f"{c['role']}_{base}.mp3"
        if fname in seen:
            # 同名音效只落一份文件；cue 表里仍然逐次记录实际落点
            continue
        out = out_dir / fname
        clip_dur = c["target_dur_sec"] or c["source_dur_sec"]
        clip_start = c["source_in_sec"]
        ok = True
        if force or not out.is_file():
            ok = _extract_clip(c["src_audio"], out, clip_start, clip_dur, ffmpeg)
        if not ok:
            continue
        seen[fname] = c["role"]
        files.append({
            "file": f"sound_vibe/sfx/{style_id}/{fname}",
            "name": c["name"],
            "role": c["role"],
            "duration_sec": _probe_duration(out),
            "source_in_sec": c["source_in_sec"],
            "trim_dur_sec": round(max(MIN_CLIP_SEC, min(MAX_CLIP_SEC, clip_dur)), 3),
            "gain_db": c["gain_db"],
            "src_audio": c["src_audio"].name,
        })

    truth["files"] = files
    truth["unique_sound_count"] = len(files)
    role_counts: Dict[str, int] = {}
    for f in files:
        role_counts[f["role"]] = role_counts.get(f["role"], 0) + 1
    truth["roles"] = dict(sorted(role_counts.items()))
    if not files:
        truth["sfx_count"] = 0
        truth["error"] = "源草稿有音效段，但音频文件提取失败"
    return truth


def extract_all(force: bool = False, only: Optional[str] = None) -> Dict[str, Any]:
    styles = load_style_sources()
    TRUTH_ROOT.mkdir(parents=True, exist_ok=True)
    SFX_ROOT.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {}
    for sid in sorted(styles):
        if only and sid != only:
            continue
        truth = extract_style(sid, force=force)
        (TRUTH_ROOT / f"{sid}.json").write_text(
            json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        out[sid] = truth
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="从剪映源草稿抽真音效，替换合成音效包")
    ap.add_argument("--style", help="只处理某套风格，如 style_21")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的抽取文件")
    args = ap.parse_args()
    res = extract_all(force=args.force, only=args.style)
    srcs = load_style_sources()
    total_seg = sum(t["sfx_count"] for t in res.values())
    total_files = sum(t["unique_sound_count"] for t in res.values())
    print(f"{'style':9s} {'draft':16s} {'cues':>5s} {'files':>5s} {'per_min':>7s} {'gain_med':>8s}  roles")
    for sid, t in res.items():
        print(
            f"{sid:9s} {srcs.get(sid, '')[:16]:16s} {t['sfx_count']:5d} {t['unique_sound_count']:5d} "
            f"{t['sfx_per_min']:7.1f} {t['gain_db_median']:8.2f}  {t['roles']}"
            + (f"  !{t.get('error')}" if t.get("error") else "")
        )
    empty = [sid for sid, t in res.items() if t["sfx_count"] == 0]
    print(f"\n共 {len(res)} 套，{total_seg} 条源音效段，{total_files} 个去重文件；无音效：{empty or '无'}")
    print(f"truth -> {TRUTH_ROOT}")
    print(f"files -> {SFX_ROOT}/<style_id>/")


if __name__ == "__main__":
    main()
