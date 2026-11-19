#!/usr/bin/env python3
"""
出片3: HyperFrames Studio 全离线相对路径多轨 HTML 工程导出
P0/P1/P2 优化项：
1. 彻底移除外部 CDN 依赖，改用零网络内联轻量 timeline 控制脚本。
2. 字幕定位消费权威 final_bbox，严格钳制在 SUBTITLE_SAFE_ZONE (y_max <= 1468)，消灭 1510px 破安全区与 stroke_width=0 强加描边 Bug。
3. 字体 staging 强制校验 common.is_verified_commercial_font，不可用或 .ttc 自动回退随仓免费商用字体。
4. 三轨独立 WAV 复用 08_voice_master.wav / 08_bgm_ducked.wav / 06_sfx_track.wav (data-volume="1.0")。
5. 清除 stage_overlays 死分支与冗余本地辅助函数，复用 common.py。
"""

from __future__ import annotations

import argparse
import html
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SAFE_ZONE,
    SUBTITLE_SAFE_ZONE,
    find_commercial_font,
    frames_to_sec,
    get_wav_duration_sec,
    is_verified_commercial_font,
    link_or_copy,
    load_style_applied,
    load_subtitle_templates,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)

maybe_reexec_in_venv()

from PIL import Image
from render_mp4 import compute_subtitle_place_xy, resolve_packaging_source


def _clamp_window(start_sec: float, end_sec: float, max_sec: float) -> Tuple[float, float]:
    s = max(0.0, round(float(start_sec), 4))
    e = min(max_sec, round(float(end_sec), 4))
    if e <= s:
        e = min(max_sec, round(s + 0.04, 4))
    return s, e


def _safe_id(raw: str, prefix: str = "item") -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in str(raw or ""))
    return f"{prefix}_{cleaned}" if not cleaned or cleaned[0].isdigit() else cleaned


def _measure_wav_sec(path_str: Optional[str]) -> float:
    if not path_str or not Path(path_str).is_file():
        return 0.0
    return get_wav_duration_sec(Path(path_str))


def attach_hyperframes_launchers(out_dir: Path) -> None:
    """生成双击即可用本地服务器/HyperFrames Studio 打开工程的 .command 与 .bat 启动器。"""
    cmd_script = "\n".join([
        "#!/bin/bash",
        'DIR="$(cd "$(dirname "$0")" && pwd)"',
        'cd "$DIR" || exit 1',
        'echo "============================================="',
        'echo "  HyperFrames 可编辑工程正在启动..."',
        'echo "  工程目录: $DIR"',
        'echo "============================================="',
        'if command -v npx >/dev/null 2>&1; then',
        '  npx --yes hyperframes studio --port 4321 || open "index.html"',
        'else',
        '  python3 -m http.server 4321 >/dev/null 2>&1 &',
        '  sleep 1',
        '  open "http://127.0.0.1:4321/index.html" 2>/dev/null || open "index.html"',
        'fi',
    ]) + "\n"
    cmd_path = out_dir / "启动HyperFrames工程.command"
    cmd_path.write_text(cmd_script, encoding="utf-8")
    try:
        cmd_path.chmod(0o755)
    except Exception:
        pass

    bat_script = "\r\n".join([
        "@echo off",
        "cd /d \"%~dp0\"",
        "where npx >nul 2>nul",
        "if %ERRORLEVEL%==0 (",
        "  npx --yes hyperframes studio --port 4321",
        ") else (",
        "  start \"\" \"index.html\"",
        ")",
    ]) + "\r\n"
    (out_dir / "启动HyperFrames工程.bat").write_text(bat_script, encoding="utf-8")


def _offline_timeline_shim(comp_id: str) -> str:
    return "\n".join([
        "  <script>",
        "    (function () {",
        "      window.__timelines = window.__timelines || {};",
        "      var state = { paused: true, time: 0 };",
        "      window.__timelines['" + comp_id + "'] = {",
        "        pause: function (t) { state.paused = true; if (typeof t === 'number') state.time = t; return this; },",
        "        play: function () { state.paused = false; return this; },",
        "        seek: function (t) { state.time = Number(t) || 0; return this; },",
        "        time: function (t) { if (typeof t === 'number') state.time = t; return state.time; }",
        "      };",
        "    })();",
        "  </script>",
    ])


def _wrap_subcomposition(title: str, comp_id: str, style_css: str, body_html: str) -> str:
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '  <meta charset="utf-8" />',
        f"  <title>{html.escape(title)}</title>",
        "  <style>",
        "    * { box-sizing: border-box; margin: 0; padding: 0; }",
        f"    html, body {{ width: {CANVAS_WIDTH}px; height: {CANVAS_HEIGHT}px; overflow: hidden; background: transparent; }}",
        f"    #root {{ position: relative; width: {CANVAS_WIDTH}px; height: {CANVAS_HEIGHT}px; overflow: hidden; }}",
        "    .clip { position: absolute; left: 0; top: 0; width: 100%; height: 100%; }",
        style_css,
        "  </style>",
        "</head>",
        "<body>",
        f'  <div id="root" data-composition-id="{html.escape(comp_id)}" data-width="{CANVAS_WIDTH}" data-height="{CANVAS_HEIGHT}">',
        body_html,
        "  </div>",
        _offline_timeline_shim(comp_id),
        "</body>",
        "</html>",
    ]) + "\n"


def _stage_font(font_path: Optional[str], fonts_dir: Path) -> Optional[Tuple[str, str]]:
    """仅 staging 经 common.is_verified_commercial_font 校验通过的网页可用字体 (.otf/.ttf/.woff2)。"""
    cand = font_path
    if not cand or not Path(cand).is_file() or Path(cand).suffix.lower() not in (".otf", ".ttf", ".woff2") or not is_verified_commercial_font(cand):
        cand = find_commercial_font(verified_only=True)
    if not cand or not Path(cand).is_file():
        return None
    src = Path(cand)
    ext = src.suffix.lower()
    if ext not in (".otf", ".ttf", ".woff2") or not is_verified_commercial_font(src):
        return None
    dst = fonts_dir / src.name
    link_or_copy(src, dst)
    fmt = "opentype" if ext == ".otf" else ("woff2" if ext == ".woff2" else "truetype")
    return f"../media/fonts/{src.name}", fmt


def _build_video_track_html(
    edl: Dict[str, Any],
    segments_dir: Path,
    total_dur: float,
) -> Tuple[str, int]:
    clips = edl.get("clips") or []
    src_to_rel: Dict[str, str] = {}
    used_names: set[str] = set()
    body_lines: List[str] = []

    for idx, c in enumerate(clips, start=1):
        mp = str(c.get("media_path") or "")
        src_p = Path(mp)
        if not mp or not src_p.is_file():
            raise FileNotFoundError(f"HyperFrames 导出缺失分镜文件: {mp}")
        if mp not in src_to_rel:
            name = src_p.name
            if name in used_names:
                name = f"{len(used_names) + 1:02d}_{src_p.name}"
            used_names.add(name)
            link_or_copy(src_p, segments_dir / name)
            src_to_rel[mp] = f"../media/segments/{name}"

        st_s, en_s = _clamp_window(float(c.get("timeline_in_sec", 0.0)), float(c.get("timeline_out_sec", 0.0)), total_dur)
        dur_s = round(max(0.04, en_s - st_s), 4)
        src_in = max(0.0, float(c.get("source_in_sec", 0.0)))
        vid_id = _safe_id(str(c.get("clip_id") or f"seg_{idx:03d}"), "seg")
        body_lines.append(
            f'    <video class="clip" id="{vid_id}" src="{html.escape(src_to_rel[mp])}" '
            f'muted playsinline preload="metadata" '
            f'data-media-start="{src_in:.4f}" data-start="{st_s:.4f}" data-duration="{dur_s:.4f}" data-track-index="0"></video>'
        )

    css = "    video.clip { object-fit: cover; }"
    return _wrap_subcomposition("主视频分镜轨", "track-video", css, "\n".join(body_lines)), len(clips)


def _place_in_zone(entry: Dict[str, Any], pw: int, ph: int, zone: Dict[str, int], default_xy: Tuple[int, int]) -> Tuple[int, int]:
    fb = entry.get("final_bbox") or entry.get("bbox")
    if isinstance(fb, dict) and {"x", "y", "w", "h"}.issubset(fb.keys()):
        bx = int(fb["x"]) + (int(fb["w"]) - pw) // 2
        by = int(fb["y"]) + (int(fb["h"]) - ph) // 2
        px = max(zone["x_min"], min(bx, zone["x_max"] - pw))
        py = max(zone["y_min"], min(by, zone["y_max"] - ph))
        return int(px), int(py)
    px = max(zone["x_min"], min(default_xy[0], zone["x_max"] - pw))
    py = max(zone["y_min"], min(default_xy[1], zone["y_max"] - ph))
    return int(px), int(py)


def _build_overlay_track_html(
    comp_id: str,
    title: str,
    elems: List[Dict[str, Any]],
    track_index: int,
    pipeline_dir: Path,
    overlays_dir: Path,
    font_candidates: Optional[List[str]],
    style_id: str,
    fps: int,
    total_dur: float,
    keep_overlays: set[str],
) -> str:
    color_plan = read_json(pipeline_dir / "07_color_plan.json") if (pipeline_dir / "07_color_plan.json").exists() else {}
    lines: List[str] = []

    for idx, elem in enumerate(elems, start=1):
        sf = max(0, int(elem.get("start_frame", 0)))
        ef = int(elem.get("end_frame", sf + fps))
        st_s, en_s = _clamp_window(frames_to_sec(sf, fps), frames_to_sec(ef, fps), total_dur)
        dur_s = round(max(0.04, en_s - st_s), 4)

        raw_png = resolve_packaging_source(
            elem, pipeline_dir, idx - 1, style_id=style_id, color_plan=color_plan, font_candidates=font_candidates,
        )
        dst_name = f"pkg_{idx:03d}.png"
        dst_png = overlays_dir / dst_name
        shutil.copy2(raw_png, dst_png)
        keep_overlays.add(dst_name)

        with Image.open(dst_png) as im:
            pw, ph = im.size
        def_x = max(SAFE_ZONE["x_min"], (CANVAS_WIDTH - pw) // 2)
        def_y = SAFE_ZONE["y_min"] + 40
        px, py = _place_in_zone(elem, pw, ph, SAFE_ZONE, (def_x, def_y))

        oid = _safe_id(str(elem.get("id") or f"ov_{idx:03d}"), "ov")
        lines.append(
            f'    <img class="clip ov-img" id="{oid}" src="../media/overlays/{dst_name}" '
            f'style="left:{px}px;top:{py}px;width:{pw}px;height:{ph}px;" '
            f'data-start="{st_s:.4f}" data-duration="{dur_s:.4f}" data-track-index="{track_index}" />'
        )

    css = "    .ov-img { position: absolute; object-fit: contain; pointer-events: none; }"
    return _wrap_subcomposition(title, comp_id, css, "\n".join(lines))


def _hex_has_alpha(hex_str: str) -> bool:
    s = str(hex_str or "").strip().lower()
    return bool(s and s != "none" and not (len(s.lstrip("#")) == 8 and s.lstrip("#").endswith("00")))


def _build_captions_track_html(
    subs: Dict[str, Any],
    templates: Dict[str, Any],
    track_index: int,
    font_info: Optional[Tuple[str, str]],
    total_dur: float,
) -> Tuple[str, int]:
    tpl_id = subs.get("template_id") or "style_01"
    tpl = templates.get(tpl_id) or next(iter(templates.values()))

    c_primary = str(tpl.get("primary_color", "#FFFFFF"))
    c_kw = str(tpl.get("keyword_color", "#FFE066"))
    c_stroke = str(tpl.get("stroke_color", "#0D1117"))
    orig_stroke_w = int(tpl.get("stroke_width", 4))
    font_sz = max(24, int(tpl.get("font_size", 58)))
    kw_scale = float(tpl.get("keyword_scale", 1.15))
    bg_hex = str(tpl.get("bg_box", "none"))
    bg_css = f"background: {bg_hex}; padding: 12px 24px; border-radius: 16px;" if _hex_has_alpha(bg_hex) else ""

    font_css = ""
    font_stack = '"AlimamaShuHeiTi", "PingFang SC", "Noto Sans CJK SC", sans-serif'
    if font_info:
        f_url, f_fmt = font_info
        font_css = (
            "    @font-face {\n"
            '      font-family: "CustomStudioFont";\n'
            f'      src: url("{f_url}") format("{f_fmt}");\n'
            "      font-display: swap;\n"
            "    }\n"
        )
        font_stack = f'"CustomStudioFont", {font_stack}'

    stroke_css = "0px" if orig_stroke_w <= 0 else f"{max(1, orig_stroke_w * 2)}px {c_stroke}"
    shadow_css = "text-shadow: 2px 3px 6px rgba(0,0,0,0.68);" if tpl.get("shadow", True) else ""

    css = "\n".join([
        font_css,
        "    .cap-clip { position: absolute; display: flex; align-items: center; justify-content: center; text-align: center; }",
        f"    .cap-box {{ display: inline-flex; flex-wrap: wrap; align-items: baseline; justify-content: center; "
        f"font-family: {font_stack}; font-size: {font_sz}px; font-weight: 800; line-height: 1.18; "
        f"color: {c_primary}; paint-order: stroke fill; -webkit-text-stroke: {stroke_css}; {shadow_css} {bg_css} }}",
        f"    .cap-kw {{ color: {c_kw}; font-size: {int(round(font_sz * kw_scale))}px; }}",
    ])

    body_lines: List[str] = []
    rendered_count = 0
    max_sub_w = SUBTITLE_SAFE_ZONE["x_max"] - SUBTITLE_SAFE_ZONE["x_min"]

    for idx, sub in enumerate(subs.get("entries") or [], start=1):
        if sub.get("suppressed"):
            continue
        st_s, en_s = _clamp_window(float(sub.get("start_sec", 0.0)), float(sub.get("end_sec", 0.0)), total_dur)
        dur_s = round(max(0.04, en_s - st_s), 4)

        fb = sub.get("final_bbox") or sub.get("bbox")
        if isinstance(fb, dict) and {"x", "y", "w", "h"}.issubset(fb.keys()):
            bx = max(SUBTITLE_SAFE_ZONE["x_min"], min(int(fb["x"]), SUBTITLE_SAFE_ZONE["x_max"] - 80))
            bw = min(max_sub_w, max(80, int(fb["w"])))
            bh = min(SUBTITLE_SAFE_ZONE["y_max"] - SUBTITLE_SAFE_ZONE["y_min"], max(48, int(fb["h"])))
            by = max(SUBTITLE_SAFE_ZONE["y_min"], min(int(fb["y"]), SUBTITLE_SAFE_ZONE["y_max"] - bh))
        else:
            bw, bh = max_sub_w, 84
            bx, by = compute_subtitle_place_xy(bw, bh, tpl)
            by = max(SUBTITLE_SAFE_ZONE["y_min"], min(by, SUBTITLE_SAFE_ZONE["y_max"] - bh))

        words = sub.get("words") or []
        if words:
            spans = "".join(
                f'<span class="cap-kw">{html.escape(str(w.get("word", "")))}</span>'
                if w.get("is_keyword")
                else html.escape(str(w.get("word", "")))
                for w in words
            )
        else:
            spans = html.escape(str(sub.get("text") or ""))

        sid = _safe_id(str(sub.get("id") or f"sub_{idx:03d}"), "sub")
        body_lines.append(
            f'    <div class="clip cap-clip" id="{sid}" '
            f'style="left:{bx}px;top:{by}px;width:{bw}px;height:{bh}px;" '
            f'data-start="{st_s:.4f}" data-duration="{dur_s:.4f}" data-track-index="{track_index}">'
            f'<div class="cap-box">{spans}</div></div>'
        )
        rendered_count += 1

    return _wrap_subcomposition("双色高亮字幕轨", "track-captions", css, "\n".join(body_lines)), rendered_count


def _build_root_index_html(
    proj_slug: str,
    total_dur: float,
    fps: int,
    tracks: List[Dict[str, Any]],
    audio_items: List[Dict[str, Any]],
) -> str:
    track_divs = "\n".join(
        f'    <div class="track-layer" style="z-index:{tr["z"]};" '
        f'data-composition-id="{html.escape(tr["id"])}" '
        f'data-composition-src="{html.escape(tr["src"])}" '
        f'data-start="0" data-duration="{total_dur:.4f}" '
        f'data-track-index="{tr["track_index"]}" '
        f'data-track-kind="{html.escape(tr["kind"])}" '
        f'data-width="{CANVAS_WIDTH}" data-height="{CANVAS_HEIGHT}"></div>'
        for tr in tracks
    )
    audio_tags = "\n".join(
        f'    <audio id="{html.escape(au["id"])}" src="{html.escape(au["src"])}" '
        f'data-start="0" data-duration="{au["duration"]:.4f}" '
        f'data-track-index="{au["track_index"]}" data-volume="{au["volume"]}"></audio>'
        for au in audio_items
    )
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '  <meta charset="utf-8" />',
        f"  <title>{html.escape(proj_slug)} - HyperFrames Studio</title>",
        "  <style>",
        "    * { box-sizing: border-box; margin: 0; padding: 0; }",
        f"    html, body {{ width: {CANVAS_WIDTH}px; height: {CANVAS_HEIGHT}px; background: #000; overflow: hidden; }}",
        f"    #main {{ position: relative; width: {CANVAS_WIDTH}px; height: {CANVAS_HEIGHT}px; overflow: hidden; }}",
        "    .track-layer { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }",
        "  </style>",
        "</head>",
        "<body>",
        '  <div id="main"',
        '       data-composition-id="main"',
        '       data-start="0"',
        f'       data-duration="{total_dur:.4f}"',
        f'       data-fps="{fps}"',
        f'       data-width="{CANVAS_WIDTH}"',
        f'       data-height="{CANVAS_HEIGHT}">',
        track_divs,
        audio_tags,
        "  </div>",
        _offline_timeline_shim("main"),
        "</body>",
        "</html>",
    ]) + "\n"


def export_hyperframes_draft(pipeline_dir: Path, simulate_failure: bool = False) -> Dict[str, Any]:
    """将流水线剪辑包装成果导出为全离线可打开的 HyperFrames Studio 工程目录。"""
    draft_dir = pipeline_dir / "hyperframes_draft"
    proj_slug = pipeline_dir.parent.name if pipeline_dir.name == "pipeline" else pipeline_dir.name
    try:
        if simulate_failure:
            raise RuntimeError("模拟 HyperFrames 工程导出异常或目录不可写。")

        edl = read_json(pipeline_dir / "04_edl.json")
        subs_p = pipeline_dir / "05_subtitles.json"
        pkg_p = pipeline_dir / "06_packaging.json"
        audio_p = pipeline_dir / "03_audio_events.json"
        mix_p = pipeline_dir / "08_mix_metrics.json"

        subs = read_json(subs_p) if subs_p.exists() else {"entries": [], "template_id": "style_01"}
        pkg = read_json(pkg_p) if pkg_p.exists() else {"visual_elements": []}
        audio_events = read_json(audio_p) if audio_p.exists() else {}
        mix_metrics = read_json(mix_p) if mix_p.exists() else {}
        templates = load_subtitle_templates()

        fps = int(edl.get("fps", DEFAULT_FPS))
        total_frames = int(edl.get("total_frames") or round(float(edl["total_duration_sec"]) * fps))
        total_dur = round(total_frames / float(fps), 4)

        comp_dir = draft_dir / "compositions"
        media_dir = draft_dir / "media"
        segments_dir = media_dir / "segments"
        overlays_dir = media_dir / "overlays"
        audio_dir = media_dir / "audio"
        fonts_dir = media_dir / "fonts"
        for d in (comp_dir, segments_dir, overlays_dir, audio_dir, fonts_dir):
            d.mkdir(parents=True, exist_ok=True)
        for stale_comp in ("stage_win_track.html", "stage_card_track.html"):
            (comp_dir / stale_comp).unlink(missing_ok=True)

        # 1. 视频分镜子组合
        video_html, n_segs = _build_video_track_html(edl, segments_dir, total_dur)
        (comp_dir / "video_track.html").write_text(video_html, encoding="utf-8")

        tracks: List[Dict[str, Any]] = [{
            "id": "track-video",
            "src": "compositions/video_track.html",
            "track_index": 0,
            "kind": "video",
            "z": 1,
        }]
        next_track = 1

        # 2. 视觉包装花字轨
        tpl_id = subs.get("template_id") or "style_01"
        tpl = templates.get(tpl_id) or next(iter(templates.values()))
        cur_style_id = str(pkg.get("style_id") or load_style_applied(pipeline_dir) or "style_01")
        flower_elems = list(pkg.get("visual_elements") or [])
        keep_overlays: set[str] = set()

        flower_file = comp_dir / "flower_track.html"
        if flower_elems:
            flower_html = _build_overlay_track_html(
                "track-flower", "视觉包装花字轨", flower_elems, next_track,
                pipeline_dir, overlays_dir, tpl.get("font_candidates"), cur_style_id, fps, total_dur, keep_overlays,
            )
            flower_file.write_text(flower_html, encoding="utf-8")
            tracks.append({
                "id": "track-flower",
                "src": "compositions/flower_track.html",
                "track_index": next_track,
                "kind": "graphics",
                "z": 30,
            })
            next_track += 1
        elif flower_file.exists():
            flower_file.unlink()

        for old_ov in overlays_dir.glob("*.png"):
            if old_ov.name not in keep_overlays:
                old_ov.unlink()

        # 3. 原生离线双色安全区字幕子组合
        font_path = subs.get("font_path") or find_commercial_font(tpl.get("font_candidates"))
        font_info = _stage_font(font_path, fonts_dir)
        cap_html, n_subs = _build_captions_track_html(subs, templates, next_track, font_info, total_dur)
        (comp_dir / "captions_track.html").write_text(cap_html, encoding="utf-8")
        tracks.append({
            "id": "track-captions",
            "src": "compositions/captions_track.html",
            "track_index": next_track,
            "kind": "captions",
            "z": 40,
        })

        # 4. 三轨同源独立 WAV 音频
        voice_src = next(
            (c for c in (
                mix_metrics.get("voice_master_wav"),
                str(pipeline_dir / "08_voice_master.wav"),
                str(pipeline_dir / "03_locked_voice.wav"),
            ) if c and Path(c).is_file()),
            None,
        )
        bgm_ducked = next(
            (c for c in (
                mix_metrics.get("bgm_ducked_wav"),
                str(pipeline_dir / "08_bgm_ducked.wav"),
            ) if c and Path(c).is_file()),
            None,
        )
        bgm_src = bgm_ducked or audio_events.get("locked_bgm_wav")
        sfx_src = str(pipeline_dir / "06_sfx_track.wav")

        audio_specs = [
            ("audio-voice", voice_src, "voice_master.wav", 10, "1.0"),
            ("audio-bgm", bgm_src, "bgm_ducked.wav", 11, "1.0" if bgm_ducked else "0.2"),
            ("audio-sfx", sfx_src, "sfx_track.wav", 12, "1.0"),
        ]
        audio_items: List[Dict[str, Any]] = []
        keep_audio: set[str] = set()
        for aid, src_str, dst_name, t_idx, vol in audio_specs:
            if not src_str or not Path(src_str).is_file():
                continue
            link_or_copy(Path(src_str), audio_dir / dst_name)
            keep_audio.add(dst_name)
            wav_sec = _measure_wav_sec(src_str)
            use_dur = round(min(total_dur, wav_sec if wav_sec > 0.05 else total_dur), 4)
            audio_items.append({
                "id": aid,
                "src": f"media/audio/{dst_name}",
                "duration": use_dur,
                "track_index": t_idx,
                "volume": vol,
            })
        for old_au in audio_dir.glob("*"):
            if old_au.name not in keep_audio:
                old_au.unlink()

        # 5. 生成根 index.html、hyperframes.json 与启动器
        root_html = _build_root_index_html(proj_slug, total_dur, fps, tracks, audio_items)
        (draft_dir / "index.html").write_text(root_html, encoding="utf-8")
        write_json(draft_dir / "hyperframes.json", {
            "schema": "hyperframes-local-v1",
            "offline": True,
            "media": {"autoProxy": True},
        })
        attach_hyperframes_launchers(draft_dir)

        readme = "\n".join([
            f"【{proj_slug} HyperFrames 全离线相对路径工程使用指南】",
            "",
            "1. 双击「启动HyperFrames工程.command」（macOS）或「启动HyperFrames工程.bat」（Windows）即可开启预览与编辑。",
            "2. 所有视频、音频、字体、包装 PNG 均使用相对路径并内置离线 timeline 控制，不依赖外部 CDN：",
            f"   - compositions/video_track.html    : {n_segs} 段精剪调色视频分镜",
            f"   - compositions/flower_track.html   : {len(flower_elems)} 个视觉包装层",
            f"   - compositions/captions_track.html : {n_subs} 条权威安全区双色字幕 (y <= {SUBTITLE_SAFE_ZONE['y_max']})",
            f"   - media/audio/                     : {len(audio_items)} 条独立同源分轨 WAV",
        ]) + "\n"
        (draft_dir / "README_HyperFrames使用说明.txt").write_text(readme, encoding="utf-8")

        status = {
            "status": "success",
            "degraded_to_mp4_only": False,
            "offline_zero_cdn": True,
            "draft_dir": str(draft_dir.resolve()),
            "index_html": str((draft_dir / "index.html").resolve()),
            "launcher_command": str((draft_dir / "启动HyperFrames工程.command").resolve()),
            "segments_count": n_segs,
            "flower_texts_count": len(flower_elems),
            "captions_count": n_subs,
            "audio_tracks_count": len(audio_items),
            "message": f"HyperFrames 全离线工程导出成功（{n_segs} 分镜 + {len(flower_elems)} 包装 + {n_subs} 字幕 + {len(audio_items)} 音轨）。",
        }
        write_json(pipeline_dir / "hyperframes_export_status.json", status)
        print(f"[hyperframes] 全离线相对路径工程导出成功 -> {draft_dir}")
        return status
    except Exception as exc:
        status = {
            "status": "degraded_to_mp4_only",
            "degraded_to_mp4_only": True,
            "draft_dir": None,
            "error": str(exc),
            "message": f"提示：HyperFrames 工程导出失败（{exc}），已自动降级。",
        }
        write_json(pipeline_dir / "hyperframes_export_status.json", status)
        print(f"[hyperframes][降级提示] {status['message']}")
        return status


def main() -> None:
    parser = argparse.ArgumentParser(description="出片3: HyperFrames Studio 全离线相对路径可编辑工程导出")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--simulate-failure", action="store_true", help="模拟导出失败验证降级逻辑")
    args = parser.parse_args()
    export_hyperframes_draft(Path(args.pipeline_dir), simulate_failure=args.simulate_failure)


if __name__ == "__main__":
    main()
