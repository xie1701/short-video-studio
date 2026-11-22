#!/usr/bin/env python3
"""
[知识讲解动画引擎：工程初始化 / 单源时间轴编译 / 运动静态化审片包 / 像素死区与亮度指纹体检]
支持两种声音策略：
1. 无配音模式（字幕即旁白）：按 base + 字数/cps 自动编排句间节奏，前 5 秒大字金句抓人 + 发光片名卡 + 底部单行无句末标点字幕。
2. 配音同步模式：直接读取 pipeline/03_audio_events.json 锁定音轨的逐句真实时间戳，与画面共享同一条时间线。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from common import (
    ASSETS_DIR,
    detect_browser_renderer,
    find_bin,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

import numpy as np

CANVAS_RUNTIME_DIR = ASSETS_DIR / "canvas_runtime"
_TRAILING_PUNCT_RE = re.compile(r"[。，、；：！？…—～.,;:!?]+$")
_SELF_REVIEW_SECTION_RE = re.compile(
    r"(?ms)^##\s*(?:自评|自我评分|打分|修改记录|审片记录|复盘打分)[^\n]*\n.*?(?=^##\s|\Z)"
)


def strip_trailing_subtitle_punct(text: str) -> str:
    """剥离句末冗余标点，但保留成对闭合引号/书名号及关键词高亮方括号 []。"""
    s = str(text or "").strip()
    if not s:
        return ""
    if s.endswith("]"):
        inner_pos = s.rfind("[")
        if inner_pos >= 0:
            head = s[:inner_pos]
            body = _TRAILING_PUNCT_RE.sub("", s[inner_pos + 1 : -1])
            return f"{head}[{body}]"
    return _TRAILING_PUNCT_RE.sub("", s).strip()


def estimate_single_line_width_px(text: str, font_size: int) -> float:
    plain = re.sub(r"[\[\]]", "", str(text or ""))
    w = 0.0
    for ch in plain:
        eaw = unicodedata.east_asian_width(ch)
        w += font_size * (1.0 if eaw in ("W", "F") else 0.58) + font_size * 0.04
    return w


def compile_animation_timeline(
    lines_cfg: Dict[str, Any],
    out_timing_js: Optional[Path] = None,
    audio_events_path: Optional[Path] = None,
    ratio: str = "16x9",
) -> Dict[str, Any]:
    """将 lines.json（或绑定 03_audio_events.json）编译为确定性 timing.js 并执行前 5 秒金句与单行宽校验。"""
    start_t = float(lines_cfg.get("start", 0.30))
    tail_sec = float(lines_cfg.get("tail", 2.5))
    cps = max(2.5, float(lines_cfg.get("cps", 6.0)))
    base_hold = max(0.4, float(lines_cfg.get("base", 0.9)))
    gap_sec = max(0.26, float(lines_cfg.get("gap", 0.30)))
    font_mode = str(lines_cfg.get("font_mode") or lines_cfg.get("font") or "sans")
    raw_lines = list(lines_cfg.get("lines") or [])

    audio_map: List[Dict[str, Any]] = []
    if audio_events_path and Path(audio_events_path).is_file():
        ae = read_json(Path(audio_events_path))
        audio_map = list(ae.get("sentences") or [])

    canvas_w = 1080 if ratio == "9x16" else 1920
    compiled: List[Dict[str, Any]] = []
    warnings: List[str] = []
    cursor = start_t
    spoken_idx = 0

    for idx, item in enumerate(raw_lines):
        raw_txt = str(item.get("text") or "").strip()
        is_card = bool(item.get("card"))
        is_hook = bool(item.get("hook"))
        clean_txt = raw_txt if is_card else strip_trailing_subtitle_punct(raw_txt)
        if not is_card and clean_txt != raw_txt:
            warnings.append(f"句 {item.get('id', idx+1)} 已自动去除句末标点: {raw_txt!r} -> {clean_txt!r}")

        plain_len = len(re.sub(r"[\[\]\s]", "", clean_txt))
        if not is_card and spoken_idx < len(audio_map):
            st = round(float(audio_map[spoken_idx]["start_sec"]), 3)
            en = round(float(audio_map[spoken_idx]["end_sec"]), 3)
            spoken_idx += 1
        else:
            at_val = item.get("at")
            if at_val is not None:
                cursor = max(cursor, float(at_val))
            if item.get("hold") is not None:
                hold = float(item["hold"])
            elif is_card:
                hold = 3.2
            elif is_hook:
                hold = round(min(2.0, 0.55 + plain_len / 8.0), 3)
            else:
                hold = round(base_hold + plain_len / cps, 3)
            st = round(cursor, 3)
            en = round(st + max(0.8, hold), 3)
            cursor = en + gap_sec + float(item.get("pause", 0.0))

        # 单行像素宽预警
        if not is_card:
            px_size = (76 if ratio == "9x16" else 98) if is_hook else (44 if ratio == "9x16" else 52)
            max_lim = canvas_w * (0.84 if ratio == "9x16" else 0.86)
            est_w = estimate_single_line_width_px(clean_txt, px_size)
            if est_w > max_lim:
                warnings.append(
                    f"单行过长预警 [{item.get('id', idx+1)}]: 约 {est_w:.0f}px > {max_lim:.0f}px，建议拆成两句 ({clean_txt})"
                )

        compiled.append({
            "id": str(item.get("id") or f"l{idx+1:02d}"),
            "text": clean_txt,
            "plain_len": plain_len,
            "start": st,
            "end": en,
            "hook": is_hook,
            "card": is_card,
            "scene": bool(item.get("scene") or is_card or item.get("at") is not None),
            "font": str(item.get("font") or font_mode),
        })

    # 前 5 秒金句硬校验
    hooks = [ln for ln in compiled if ln["hook"]]
    hook_within_5s = True
    if not hooks:
        hook_within_5s = False
        warnings.append("缺少开篇金句 (hook: true)：前 5 秒应先用大字抛反常识结论或悬念")
    else:
        first_s = hooks[0]["start"]
        last_finish = hooks[-1]["start"] + hooks[-1]["plain_len"] / 18.0 + 0.5
        if first_s > 1.0 or last_finish > 5.0:
            hook_within_5s = False
            warnings.append(
                f"开篇金句节奏偏慢：首句 {first_s:.2f}s、末句出完约 {last_finish:.2f}s（目标 <= 5.0s）"
            )

    total_sec = round((compiled[-1]["end"] if compiled else 4.0) + tail_sec, 3)
    payload = {
        "title": str(lines_cfg.get("title") or "知识讲解动画"),
        "subtitle_en": str(lines_cfg.get("subtitle_en") or "VISUAL EXPLAINER"),
        "question": str(lines_cfg.get("question") or ""),
        "font_mode": font_mode,
        "ratio": ratio,
        "voice_mode": "voice_synced" if audio_map else "subtitle_narration",
        "palette": lines_cfg.get("palette") or {
            "bg": "#05070d",
            "ink": "#f1f5f9",
            "sub": "#94a3b8",
            "accent": "#5ce1e6",
            "accent2": "#f59e0b",
            "alert": "#fb7185",
        },
        "total_sec": total_sec,
        "lines": compiled,
        "hook_within_5s": hook_within_5s,
        "warnings": warnings,
    }

    if out_timing_js is not None:
        out_timing_js.parent.mkdir(parents=True, exist_ok=True)
        js_body = (
            "window.SVS_TIMING = "
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + ";\n"
            + "window.SVS_LINE_MAP = Object.fromEntries((window.SVS_TIMING.lines || []).map((l) => [l.id, l]));\n"
        )
        out_timing_js.write_text(js_body, encoding="utf-8")
    return payload


def init_animation_project(
    project_dir: Path,
    title: str,
    one_idea: str,
    ratio: str = "16x9",
    font_mode: str = "sans",
    lines_override: Optional[List[Dict[str, Any]]] = None,
    palette: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """初始化独立知识动画工程目录（舞台运行时 + 双比例 4K 封面模板 + 初始字幕稿与时间轴）。"""
    proj = Path(project_dir).resolve()
    stage_dir = proj / "stage"
    work_dir = proj / "work"
    out_dir = proj / "out"
    for d in (stage_dir, work_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)

    for fname in ("stage_clock.js", "visual_kit.js", "anim_starter.html", "cover_4k.html"):
        src = CANVAS_RUNTIME_DIR / fname
        if src.is_file():
            shutil.copy2(src, stage_dir / fname)

    lines_list = lines_override or [
        {"id": "h1", "text": f"{title[:12]}背后有个[反常识真相]", "hook": True},
        {"id": "h2", "text": f"{one_idea[:14]}", "hook": True, "pause": 0.15},
        {"id": "title_card", "text": title, "card": True, "hold": 3.0},
        {"id": "s1", "text": f"先看最直观的[日常现象]", "scene": True},
        {"id": "s2", "text": f"拆开底层规律后，答案其实很清楚", "scene": False},
        {"id": "s3", "text": f"回到开头再看一遍，[感受已经完全不同]", "scene": True},
    ]

    lines_cfg: Dict[str, Any] = {
        "title": title,
        "subtitle_en": "VISUAL EXPLAINER",
        "question": one_idea,
        "font_mode": font_mode,
        "start": 0.3,
        "tail": 2.2,
        "cps": 6.0,
        "base": 0.9,
        "gap": 0.3,
        "palette": palette or {
            "bg": "#05070d",
            "ink": "#f1f5f9",
            "sub": "#94a3b8",
            "accent": "#5ce1e6",
            "accent2": "#f59e0b",
            "alert": "#fb7185",
        },
        "lines": lines_list,
    }
    lines_json_p = work_dir / "lines.json"
    write_json(lines_json_p, lines_cfg)
    compiled = compile_animation_timeline(lines_cfg, out_timing_js=stage_dir / "timing.js", ratio=ratio)

    # 写入封面数据文件供 cover_4k.html 读取
    clean_hero = re.sub(r"[\[\]]", "", lines_list[0]["text"])[:8]
    mid = max(2, len(clean_hero) // 2)
    cover_data = {
        "kicker": one_idea[:14],
        "hero_lines": [clean_hero[:mid], clean_hero[mid:]] if len(clean_hero) >= 5 else [clean_hero],
        "title": title[:12],
        "font_mode": font_mode,
        "palette": lines_cfg["palette"],
    }
    (stage_dir / "cover_data.js").write_text(
        "window.SVS_COVER_DATA = " + json.dumps(cover_data, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )

    brief_md = work_dir / "storyboard.md"
    if not brief_md.exists():
        brief_md.write_text(
            f"# 《{title}》动画分镜与核验记录\n\n"
            f"- **核心命题**：{one_idea}\n"
            f"- **画幅**：`{ratio}` | **字体气质**：`{font_mode}` | **预计时长**：`{compiled['total_sec']}s`\n\n"
            "## 分句时间表\n"
            + "\n".join(
                f"- `{ln['id']}` [{ln['start']:.2f}s - {ln['end']:.2f}s] "
                f"{'[片名卡]' if ln['card'] else ('[金句]' if ln['hook'] else '')} {ln['text']}"
                for ln in compiled["lines"]
            )
            + "\n",
            encoding="utf-8",
        )

    return {
        "project_dir": str(proj),
        "stage_dir": str(stage_dir),
        "lines_json": str(lines_json_p),
        "timing_js": str(stage_dir / "timing.js"),
        "cover_html": str(stage_dir / "cover_4k.html"),
        "total_sec": compiled["total_sec"],
        "hook_within_5s": compiled["hook_within_5s"],
        "warnings": compiled["warnings"],
    }


def _decode_gray_frames(video_path: Path, width: int = 192) -> Tuple[np.ndarray, float, float, int, int]:
    ffmpeg = find_bin("ffmpeg")
    ffprobe = find_bin("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("需要 ffmpeg 与 ffprobe 执行成片像素体检。")
    pr = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video_path),
        ],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(pr.stdout or "{}")
    vs = next(s for s in info.get("streams", []) if s.get("codec_type") == "video")
    num, den = map(int, str(vs.get("r_frame_rate") or "30/1").split("/"))
    fps = float(num) / max(1.0, float(den))
    vw, vh = int(vs["width"]), int(vs["height"])
    dur = float(info.get("format", {}).get("duration") or vs.get("duration") or 0.0)
    sh = max(12, int(round(vh * width / max(1, vw) / 2.0) * 2))
    raw = subprocess.run(
        [
            ffmpeg, "-v", "error", "-i", str(video_path),
            "-vf", f"scale={width}:{sh},format=gray", "-f", "rawvideo", "-",
        ],
        capture_output=True, check=True,
    ).stdout
    arr = np.frombuffer(raw, dtype=np.uint8)
    n_frames = len(arr) // (width * sh)
    frames = arr[: n_frames * width * sh].reshape(n_frames, sh, width).astype(np.float32)
    return frames, fps, dur, vw, vh


def audit_animation_video(
    video_path: Path,
    dead_sec: float = 2.5,
    holds: Optional[List[Tuple[float, float]]] = None,
    cut_ranges: Optional[List[Tuple[float, float]]] = None,
    dark_style: bool = True,
) -> Dict[str, Any]:
    """
    动画成片硬伤与视觉秩序体检：
    1. 6x6 分区 15fps 活跃度与死区检测（精准适配暗底细线动画）；
    2. 单帧跳变（3 倍邻帧中位数）与黑白闪帧检测；
    3. 暗底亮度指纹四指标（去底部 15% 字幕带：p99 / 高亮>110占比 / 画面均值 / 近黑帧占比）。
    """
    vp = Path(video_path).resolve()
    g, fps, dur, vw, vh = _decode_gray_frames(vp, width=192)
    if len(g) < 3:
        return {"video": str(vp), "error": "too_few_frames", "passed": False}

    diff_full = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
    t_axis = (np.arange(len(diff_full)) + 1) / fps
    holds = holds or []
    cut_ranges = cut_ranges or []

    def _in_range(x: float, rs: List[Tuple[float, float]], pad: float = 0.0) -> bool:
        return any((a - pad) <= x <= (b + pad) for a, b in rs)

    # 1) 6x6 分区 15fps 活跃度与死区
    step = max(1, int(round(fps / 15.0)))
    gs = g[::step]
    sfps = fps / step
    hh = (gs.shape[1] // 6) * 6
    ww = (gs.shape[2] // 6) * 6
    if len(gs) >= 2 and hh >= 6 and ww >= 6:
        zone_diff = np.abs(np.diff(gs[:, :hh, :ww], axis=0)).reshape(
            len(gs) - 1, 6, hh // 6, 6, ww // 6
        ).mean(axis=(2, 4))
        act = zone_diff.max(axis=(1, 2))
    else:
        act = diff_full
    ts = (np.arange(len(act)) + 1) / max(1e-3, sfps)

    still = act < 0.6
    for idx_k, x in enumerate(ts):
        if _in_range(float(x), holds):
            still[idx_k] = False
    dead_zones: List[Tuple[float, float]] = []
    i = 0
    while i < len(still):
        if still[i]:
            j = i
            while j < len(still) and still[j]:
                j += 1
            if (j - i) / sfps >= dead_sec:
                dead_zones.append((round(float(ts[i] - 1.0 / sfps), 2), round(float(ts[j - 1]), 2)))
            i = j
        else:
            i += 1

    # 2) 单帧跳变与黑白闪帧
    spikes: List[Dict[str, float]] = []
    for k in range(2, len(diff_full) - 2):
        nb = float(np.median(np.r_[diff_full[k - 2 : k], diff_full[k + 1 : k + 3]]))
        if float(diff_full[k]) > 6.0 and float(diff_full[k]) > 3.0 * max(nb, 0.8):
            if not _in_range(float(t_axis[k]), cut_ranges, pad=1.5 / fps):
                spikes.append({"t_sec": round(float(t_axis[k]), 3), "delta": round(float(diff_full[k]), 2), "neighbor": round(nb, 2)})

    frame_means = g.mean(axis=(1, 2))
    flashes = [
        round(k / fps, 3)
        for k in range(1, len(frame_means) - 1)
        if (frame_means[k] < 6.0 or frame_means[k] > 249.0) and abs(frame_means[k] - frame_means[k - 1]) > 40.0
    ]

    # 3) 暗底亮度指纹（每 2 秒采样、去底部 15% 字幕带）
    stride2 = max(1, int(round(fps * 2.0)))
    gl = g[::stride2][:, : max(1, int(0.85 * g.shape[1]))]
    p99 = round(float(np.median(np.percentile(gl.reshape(len(gl), -1), 99, axis=1))), 1)
    bright_pct = round(float(np.median((gl > 110).mean(axis=(1, 2)))) * 100.0, 2)
    luma_mean = round(float(np.median(gl.mean(axis=(1, 2)))), 1)
    near_black_ratio = round(float(((gl < 18).mean(axis=(1, 2)) > 0.85).mean()), 3)

    luma_ok = True
    luma_notes: List[str] = []
    if dark_style:
        if p99 < 130.0:
            luma_ok = False
            luma_notes.append(f"亮部峰值 p99={p99} 偏暗（目标 >=130），建议增强主体高光/亮核")
        if bright_pct < 1.0:
            luma_notes.append(f"高亮像素占比 {bright_pct}% 偏少（推荐 1.5–2.4%）")
        elif bright_pct > 4.5:
            luma_ok = False
            luma_notes.append(f"高亮像素占比 {bright_pct}% 偏亮（>4.5% 易过曝）")
        if luma_mean > 32.0:
            luma_notes.append(f"整体均值 {luma_mean} 偏亮（暗底推荐 17–24）")

    moving_ratio = round(float((act > 1.5).mean()), 3)
    sec_bins = [
        float(act[int(k * sfps) : int((k + 1) * sfps)].mean())
        for k in range(int(len(act) / max(1e-3, sfps)))
    ]
    mx_bin = max(sec_bins) if sec_bins else 1.0
    sparkline = "".join(" ▁▂▃▄▅▆▇█"[min(8, int(8 * v / max(1e-4, mx_bin) + 0.5))] for v in sec_bins)

    return {
        "video": str(vp),
        "width": vw,
        "height": vh,
        "fps": round(fps, 3),
        "duration_sec": round(dur, 3),
        "dead_zones": dead_zones,
        "single_frame_spikes": spikes[:12],
        "flash_frames": flashes[:10],
        "moving_ratio": moving_ratio,
        "activity_sparkline": sparkline,
        "luminance_fingerprint": {
            "p99": p99,
            "bright_pixel_pct": bright_pct,
            "mean_luma": luma_mean,
            "near_black_frame_ratio": near_black_ratio,
            "ok": luma_ok,
            "notes": luma_notes,
        },
        "passed": (len(dead_zones) == 0 and len(flashes) == 0),
    }


def build_review_pack(
    video_path: Path,
    out_dir: Path,
    timing_js: Optional[Path] = None,
    storyboard_md: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    生成将“运动静态化”的双盲独立审片包：
    1. 01_contact_2s.png：每 2 秒一格全片节奏联系表
    2. 02_keyframes.png：场景中点关键帧拼图
    3. transitions/：每个场景切点前后 0.6s、每 0.1s 一格横条带（查残影与硬跳）
    4. motion/：每个场景中段 1.0s、每 0.1s 一格横条带（查缓动重量与死画面）
    5. blind_review_guide.md：剥离自评/打分段落后的独立审片指令
    """
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 生成审片包。")

    vp = Path(video_path).resolve()
    od = Path(out_dir).resolve()
    tr_dir = od / "transitions"
    mo_dir = od / "motion"
    for d in (od, tr_dir, mo_dir):
        d.mkdir(parents=True, exist_ok=True)

    _, _, dur, _, _ = _decode_gray_frames(vp, width=96)
    lines: List[Dict[str, Any]] = []
    if timing_js and Path(timing_js).is_file():
        txt = Path(timing_js).read_text(encoding="utf-8")
        m = re.search(r"window\.SVS_TIMING\s*=\s*(\{.*?\});", txt, re.S)
        if m:
            lines = list(json.loads(m.group(1)).get("lines") or [])

    # 1) 每 2 秒联系表
    rows = max(1, int(math.ceil(max(1.0, dur) / 2.0 / 6.0)))
    contact_p = od / "01_contact_2s.png"
    subprocess.run(
        [
            ffmpeg, "-y", "-v", "error", "-i", str(vp),
            "-vf", f"fps=0.5,scale=320:-2,tile=6x{rows}:padding=4:color=0x111111",
            "-frames:v", "1", str(contact_p),
        ],
        check=False,
    )

    # 2) 确定场景边界与中点
    cuts = [float(l["start"]) for l in lines if (l.get("scene") or l.get("card")) and float(l["start"]) > 0.6]
    if not cuts and dur > 4.0:
        cuts = [round(dur * k / 3.0, 2) for k in (1, 2)]
    seg_bounds = [0.0] + [c for c in cuts if 0.6 < c < dur - 0.6] + [dur]

    def _make_strip(t0: float, t1: float, step: float, dest: Path, w: int = 240) -> bool:
        span = max(0.2, t1 - t0)
        n_tiles = max(2, min(14, int(round(span / step))))
        r = subprocess.run(
            [
                ffmpeg, "-y", "-v", "error",
                "-ss", f"{max(0.0, t0):.3f}", "-t", f"{span:.3f}", "-i", str(vp),
                "-vf", f"fps={1.0 / step:.4f},scale={w}:-2,tile={n_tiles}x1:padding=2:color=0x222222",
                "-frames:v", "1", str(dest),
            ],
            check=False,
        )
        return r.returncode == 0 and dest.is_file()

    transition_files: List[str] = []
    for idx, ct in enumerate(cuts[:10], start=1):
        out_t = tr_dir / f"trans_{idx:02d}_{ct:.2f}s.png"
        if _make_strip(max(0.0, ct - 0.5), min(dur, ct + 0.5), 0.1, out_t):
            transition_files.append(str(out_t))

    motion_files: List[str] = []
    for idx in range(len(seg_bounds) - 1):
        mid = (seg_bounds[idx] + seg_bounds[idx + 1]) * 0.5
        out_m = mo_dir / f"motion_{idx+1:02d}_{mid:.2f}s.png"
        if _make_strip(max(0.0, mid - 0.45), min(dur, mid + 0.45), 0.1, out_m):
            motion_files.append(str(out_m))

    # 3) 脱敏分镜说明（剥离自评/打分段落防盲审污染）
    scrubbed_story = ""
    if storyboard_md and Path(storyboard_md).is_file():
        raw_story = Path(storyboard_md).read_text(encoding="utf-8")
        scrubbed_story = _SELF_REVIEW_SECTION_RE.sub("", raw_story).strip()
        (od / "storyboard_blind.md").write_text(scrubbed_story + "\n", encoding="utf-8")

    guide_p = od / "blind_review_guide.md"
    guide_p.write_text(
        "# 独立盲审检查指南\n\n"
        f"- **全片节奏联系表**：`01_contact_2s.png`\n"
        f"- **转场条带（每格 0.1s）**：共 {len(transition_files)} 条，位于 `transitions/`\n"
        f"- **运动条带（每格 0.1s）**：共 {len(motion_files)} 条，位于 `motion/`\n\n"
        "## 必查 6 项硬指标\n"
        "1. 开篇 5 秒内大字金句是否出完、画面是否足够干净抓人；\n"
        "2. 每帧是否只留 1 个主角 + ≤2 处小标注，没有信息堆砌；\n"
        "3. `transitions/` 中是否有双层叠影残影或突兀闪帧；\n"
        "4. `motion/` 中 10 格是否持续有重量感运动，而非死画面；\n"
        "5. 字幕是否始终单行、居中、无多余句末标点且不压主体；\n"
        "6. 联系表连续 3 格是否雷同（雷同则需切景别或插特写）。\n",
        encoding="utf-8",
    )

    return {
        "out_dir": str(od),
        "contact_sheet": str(contact_p) if contact_p.is_file() else None,
        "transition_strips": transition_files,
        "motion_strips": motion_files,
        "guide_md": str(guide_p),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="零实拍知识讲解动画引擎 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="初始化知识动画工程")
    p_init.add_argument("--project-dir", required=True)
    p_init.add_argument("--title", required=True)
    p_init.add_argument("--one-idea", required=True)
    p_init.add_argument("--ratio", choices=["16x9", "9x16", "1x1", "3x4"], default="16x9")
    p_init.add_argument("--font-mode", choices=["sans", "serif"], default="sans")

    p_tl = sub.add_parser("timeline", help="编译 lines.json 为 timing.js")
    p_tl.add_argument("--lines-json", required=True)
    p_tl.add_argument("--out-js", required=True)
    p_tl.add_argument("--audio-events", help="可选：绑定 03_audio_events.json 进入配音同步模式")
    p_tl.add_argument("--ratio", choices=["16x9", "9x16"], default="16x9")

    p_aud = sub.add_parser("audit", help="对成片 MP4 执行 6x6 死区与亮度指纹体检")
    p_aud.add_argument("--video", required=True)
    p_aud.add_argument("--dead-sec", type=float, default=2.5)
    p_aud.add_argument("--out-json")

    p_rp = sub.add_parser("review-pack", help="生成 0.1s 转场/运动条带双盲审片包")
    p_rp.add_argument("--video", required=True)
    p_rp.add_argument("--out-dir", required=True)
    p_rp.add_argument("--timing-js")
    p_rp.add_argument("--storyboard-md")

    args = ap.parse_args()
    if args.cmd == "init":
        res = init_animation_project(
            Path(args.project_dir), args.title, args.one_idea, ratio=args.ratio, font_mode=args.font_mode
        )
    elif args.cmd == "timeline":
        res = compile_animation_timeline(
            read_json(Path(args.lines_json)),
            out_timing_js=Path(args.out_js),
            audio_events_path=Path(args.audio_events) if args.audio_events else None,
            ratio=args.ratio,
        )
    elif args.cmd == "audit":
        res = audit_animation_video(Path(args.video), dead_sec=args.dead_sec)
        if args.out_json:
            write_json(Path(args.out_json), res)
    elif args.cmd == "review-pack":
        res = build_review_pack(
            Path(args.video),
            Path(args.out_dir),
            timing_js=Path(args.timing_js) if args.timing_js else None,
            storyboard_md=Path(args.storyboard_md) if args.storyboard_md else None,
        )
    else:
        raise SystemExit(2)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
