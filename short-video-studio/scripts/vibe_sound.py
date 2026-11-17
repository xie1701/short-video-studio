#!/usr/bin/env python3
"""
[Vibe Sound CC0 声学资产集成与清洗工具]
基于 vibe-motion/sound (https://github.com/vibe-motion/sound) 规范集成 Freesound CC0 检索与下载，
并对下载的原始声学样本执行静音裁切、瞬态对准、尾部平滑淡出与 LUFS/Peak 归一化清洗，
为《风格谱》各流派提供高质感、不抢人声的商业级真实声学 SFX 与差异化 BGM 底乐。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import ASSETS_DIR, find_bin, maybe_reexec_in_venv, write_json

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) short-video-studio-vibe-sound"}
CACHE_DIR = Path.home() / ".cache" / "sfx-sources"
INDEX_PATH = CACHE_DIR / "index.json"
SOUND_VIBE_DIR = ASSETS_DIR / "sound_vibe"
FS_LICENSE = {"cc0": '"Creative Commons 0"', "by": '("Attribution" OR "Creative Commons 0")', "any": None}
FS_LIC_SHORT = {
    "Creative Commons 0": "CC0",
    "Attribution": "BY",
    "Attribution NonCommercial": "BY-NC",
    "Sampling+": "Sampling+",
}
FS_SORT = {
    "downloads": "Downloads (most first)",
    "rating": "Rating (highest first)",
    "relevance": "Automatic by relevance",
    "short": "Duration (shortest first)",
}

# 7 套展示与通用流派对应的 CC0 真实声源蓝图（全数经 Freesound CC0 人工筛选：高评分+真录音+无版权风险）
# 每个流派均配置：1首差异化 BGM（含有效循环窗口），以及 2~3 种柔和真实物理材质 SFX（严格区分音色手感）
STYLE_SOUND_MAP: Dict[str, Dict[str, Any]] = {
    "style_14": {
        "bgm": {"src": "fs_680113_Pop-Guitar-Loop-Thing.mp3", "start": 0.60, "end": 65.50, "bpm": 120.0},
        "sfx": {
            "transition": ("fs_585257_swoosh-1-mp3.mp3", 0.10, 0.36, -1.5),
            "accent": ("fs_704935_Wooden-Tap.mp3", 0.82, 1.05, 0.0),
            "ui": ("fs_411462_Bubble-Pop.mp3", 0.14, 0.34, -2.0),
        },
        "sfx_min_gap_sec": 7.5,
        "sfx_gain_db": -10.0,
        "bgm_base_db": -12.0,
        "duck_depth_db": -10.0,
    },
    "style_02": {
        "bgm": {"src": "fs_69195_Mix05_90BPM-mp3.mp3", "start": 0.02, "end": 21.30, "bpm": 90.0},
        "sfx": {
            "transition": ("fs_585257_swoosh-1-mp3.mp3", 0.12, 0.34, 0.0),
            "accent": ("fs_512389_Bass-Impact-Ki_01-mp3.mp3", 0.04, 0.48, -2.0),
            "ui": ("fs_878248_UI-Hover-Micro-Swish.mp3", 0.01, 0.18, -1.5),
        },
        "sfx_min_gap_sec": 6.0,
        "sfx_gain_db": -9.0,
        "bgm_base_db": -11.5,
        "duck_depth_db": -10.5,
    },
    "style_03": {
        "bgm": {"src": "fs_713517_Perspire-Loop-Corporate-style-background.mp3", "start": 0.02, "end": 59.80, "bpm": 110.0},
        "sfx": {
            "transition": ("fs_623709_soft_swoosh_with_noisy_ambience-wav.mp3", 0.04, 0.36, -2.0),
            "accent": ("fs_369710_Wood-Knock.mp3", 0.00, 0.22, -1.0),
            "ui": ("fs_704935_Wooden-Tap.mp3", 0.82, 1.06, -1.0),
        },
        "sfx_min_gap_sec": 6.5,
        "sfx_gain_db": -10.0,
        "bgm_base_db": -12.5,
        "duck_depth_db": -10.0,
    },
    "style_12": {
        "bgm": {"src": "fs_629153_Dusty-Lofi-Melody-Loop-75-BPM-wav.mp3", "start": 0.02, "end": 49.50, "bpm": 75.0},
        "sfx": {
            "transition": ("fs_878248_UI-Hover-Micro-Swish.mp3", 0.01, 0.18, -2.0),
            "accent": ("fs_411462_Bubble-Pop.mp3", 0.15, 0.32, -2.0),
            "ui": ("fs_528561_Soft-UI-Button-Click.mp3", 0.00, 0.14, -1.0),
        },
        "sfx_min_gap_sec": 7.5,
        "sfx_gain_db": -11.0,
        "bgm_base_db": -13.0,
        "duck_depth_db": -9.5,
    },
    "style_06": {
        "bgm": {"src": "fs_668907_Free-Cinematic-Emotional-Piano-92bpm-D-M.mp3", "start": 0.02, "end": 41.40, "bpm": 92.0},
        "sfx": {
            "transition": ("fs_623709_soft_swoosh_with_noisy_ambience-wav.mp3", 0.04, 0.42, -3.0),
            "accent": ("fs_676375_Film-Camera-Click-Shutter.mp3", 0.96, 1.28, -1.0),
            "ui": ("fs_676375_Film-Camera-Click-Shutter.mp3", 0.20, 0.48, -2.0),
        },
        "sfx_min_gap_sec": 11.0,
        "sfx_gain_db": -13.5,
        "bgm_base_db": -11.5,
        "duck_depth_db": -11.0,
    },
    "style_05": {
        "bgm": {"src": "fs_347815_Warm-Breeze-mp3.mp3", "start": 0.88, "end": 22.30, "bpm": 104.0},
        "sfx": {
            "transition": ("fs_82379_paper-flip-3-wav.mp3", 0.05, 0.34, 0.0),
            "accent": ("fs_82379_paper-flip-3-wav.mp3", 0.05, 0.28, 0.0),
            "ui": ("fs_369710_Wood-Knock.mp3", 0.00, 0.20, -1.5),
        },
        "sfx_min_gap_sec": 6.5,
        "sfx_gain_db": -9.5,
        "bgm_base_db": -12.5,
        "duck_depth_db": -10.0,
    },
    "style_15": {
        "bgm": {"src": "fs_531463_Waterfall-synth-loop-wav.mp3", "start": 0.02, "end": 34.10, "bpm": 120.0},
        "sfx": {
            "transition": ("fs_878248_UI-Hover-Micro-Swish.mp3", 0.01, 0.19, -1.0),
            "accent": ("fs_378085_mechanical-key-hard-wav.mp3", 0.08, 0.25, 0.0),
            "ui": ("fs_528561_Soft-UI-Button-Click.mp3", 0.00, 0.15, -1.0),
            "receipt": ("fs_217181_Old-Till-Printer-Feeding-Receipt-Paper-w.mp3", 4.95, 5.55, -1.0),
        },
        "sfx_min_gap_sec": 6.5,
        "sfx_gain_db": -10.0,
        "bgm_base_db": -12.5,
        "duck_depth_db": -10.0,
    },
}


def _fetch(url: str, timeout: int = 60, tries: int = 3) -> bytes:
    for n in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if n == tries - 1:
                raise e if isinstance(e, OSError) else OSError(repr(e))
            time.sleep(1 + n)
    return b""


def search_freesound(query: str, license_mode: str = "cc0", limit: int = 8, pages: int = 2) -> List[Dict[str, Any]]:
    q = {"q": query, "s": FS_SORT["relevance"]}
    if FS_LICENSE[license_mode]:
        q["f"] = "license:" + FS_LICENSE[license_mode]
    with ThreadPoolExecutor(max(1, pages)) as ex:
        htmls = list(ex.map(lambda n: _fetch("https://freesound.org/search/?" + urllib.parse.urlencode({**q, "page": n})).decode(), range(1, pages + 1)))
    rows: List[Dict[str, Any]] = []
    for blk in (b for h in htmls for b in h.split('class="bw-search__result"')[1:]):
        g = lambda k: (re.search(rf'data-{k}="([^"]*)"', blk) or [None, ""])[1]
        lic = re.search(r'title="License: ([^"]+)"', blk)
        rt = re.search(r'aria-label="Average rating of ([\d.]+)"', blk)
        rn = re.search(r'bw-rating__count[^>]*>\((\d+)\)', blk)
        desc = re.search(r'<div class="max-h-60px[^"]*"\s*>\s*(.*?)\s*</div>', blk, re.S)
        sid, user, dl = g("sound-id"), g("username"), int(g("num-downloads") or 0)
        rows.append({
            "key": f"fs:{sid}",
            "title": html.unescape(g("title")),
            "dur": f"{float(g('duration') or 0):.1f}s",
            "author": user,
            "license": FS_LIC_SHORT.get(lic.group(1), lic.group(1)) if lic else "?",
            "preview": g("mp3").replace("-lq.mp3", "-hq.mp3"),
            "url": g("mp3").replace("-lq.mp3", "-hq.mp3"),
            "downloads": dl,
            "stats": f"↓{dl} ★{rt[1]}({rn[1]})" if rt and rn else f"↓{dl} ★-",
            "desc": html.unescape(re.sub(r"<[^>]+>|\s+", " ", desc[1])).strip()[:90] if desc else "",
        })
    rows.sort(key=lambda r: -r["downloads"])
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    idx = json.loads(INDEX_PATH.read_text()) if INDEX_PATH.exists() else {}
    idx.update({r["key"]: r for r in rows})
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False))
    return rows[:limit]


def clean_all_vibe_assets(raw_dir: Path | None = None, out_dir: Path | None = None) -> Dict[str, Any]:
    """将 raw 目录中的 CC0 Freesound 录音清洗为 48kHz stereo 标准化 BGM (-20 LUFS) 与 SFX (-18 dBFS peak 限幅)。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 执行声学清洗。")
    raw_dir = raw_dir or (SOUND_VIBE_DIR / "raw")
    out_dir = out_dir or SOUND_VIBE_DIR
    bgm_dir = out_dir / "bgm"
    sfx_dir = out_dir / "sfx"
    bgm_dir.mkdir(parents=True, exist_ok=True)
    sfx_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Any] = {"styles": {}}
    for sid, cfg in STYLE_SOUND_MAP.items():
        b = cfg["bgm"]
        src_b = raw_dir / b["src"]
        dst_b = bgm_dir / f"{sid}_bgm.wav"
        dur_b = max(2.0, float(b["end"]) - float(b["start"]))
        fade_out_t = max(0.0, dur_b - 0.18)
        subprocess.run([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{float(b['start']):.3f}", "-t", f"{dur_b:.3f}",
            "-i", str(src_b),
            "-af", f"afade=t=in:st=0:d=0.12,afade=t=out:st={fade_out_t:.3f}:d=0.18,loudnorm=I=-20:TP=-2.0:LRA=8",
            "-ar", "48000", "-ac", "2", "-acodec", "pcm_s16le",
            str(dst_b),
        ], check=True)
        write_json(bgm_dir / f"{sid}_bgm.json", {"style_id": sid, "bpm": b["bpm"], "source": b["src"], "license": "CC0"})

        sfx_entry: Dict[str, str] = {}
        for role, (fn, s0, s1, trim_gain_db) in cfg["sfx"].items():
            src_s = raw_dir / fn
            dst_s = sfx_dir / f"{sid}_{role}.wav"
            d_s = max(0.08, s1 - s0)
            f_out_d = min(0.06, d_s * 0.35)
            f_out_st = max(0.0, d_s - f_out_d)
            # 先高通去除低频喷麦杂质，裁取目标瞬态区间，加起止淡化并压限避免刺耳
            subprocess.run([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{s0:.3f}", "-t", f"{d_s:.3f}",
                "-i", str(src_s),
                "-af", (
                    f"highpass=f=60,afade=t=in:st=0:d=0.005,afade=t=out:st={f_out_st:.3f}:d={f_out_d:.3f},"
                    f"loudnorm=I=-22:TP=-6.0:LRA=6,volume={trim_gain_db:.1f}dB"
                ),
                "-ar", "48000", "-ac", "2", "-acodec", "pcm_s16le",
                str(dst_s),
            ], check=True)
            sfx_entry[role] = str(dst_s.resolve())

        manifest["styles"][sid] = {
            "bgm_wav": str(dst_b.resolve()),
            "bpm": b["bpm"],
            "sfx": sfx_entry,
            "sfx_min_gap_sec": cfg["sfx_min_gap_sec"],
            "sfx_gain_db": cfg["sfx_gain_db"],
            "bgm_base_db": cfg["bgm_base_db"],
            "duck_depth_db": cfg["duck_depth_db"],
        }

    write_json(out_dir / "vibe_sound_manifest.json", manifest)
    print(f"[vibe_sound] 已清洗生成 {len(manifest['styles'])} 套风格专属 CC0 BGM 与真实物理 SFX -> {out_dir / 'vibe_sound_manifest.json'}")
    return manifest


def get_style_sound_profile(style_id: str) -> Optional[Dict[str, Any]]:
    mf_path = SOUND_VIBE_DIR / "vibe_sound_manifest.json"
    if not mf_path.exists():
        return None
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    return mf.get("styles", {}).get(style_id)


def main() -> None:
    maybe_reexec_in_venv()
    ap = argparse.ArgumentParser(description="Vibe Sound CC0 真实音效检索与清洗")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p_s = sp.add_parser("search")
    p_s.add_argument("query")
    p_s.add_argument("--license", default="cc0", choices=list(FS_LICENSE))
    p_s.add_argument("-n", type=int, default=8)
    p_c = sp.add_parser("clean")
    a = ap.parse_args()
    if a.cmd == "search":
        for r in search_freesound(a.query, license_mode=a.license, limit=a.n):
            print(f"{r['key']}\t{r['title']}\t{r['dur']}\t{r['stats']}\t{r['desc']}")
    elif a.cmd == "clean":
        clean_all_vibe_assets()


if __name__ == "__main__":
    main()
