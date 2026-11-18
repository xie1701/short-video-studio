#!/usr/bin/env python3
"""
[出片 3：HyperFrames 可编辑工程包导出封装]
将流水线已完成的调色精剪分镜、风格谱画框/卡片/花字、原生 HTML 双色高亮字幕
与三轨独立 WAV 音频导出为标准模块化 HyperFrames Studio 工程 (hyperframes_draft/)，
并附带跨平台双击启动器，双击即可打开浏览器 Studio 实时预览、二次编辑或重新渲染。
"""

from __future__ import annotations

import argparse
import html
import os
import shutil
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SKILL_ROOT,
    find_commercial_font,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)
from render_mp4 import render_subtitle_png, resolve_packaging_source


def _link_or_copy(src: Path, dst: Path) -> None:
    """同卷优先硬链接零空间复用文件，跨文件系统自动降级为复制。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_real = src.resolve()
    if dst.exists() or dst.is_symlink():
        try:
            st_s, st_d = src_real.stat(), dst.stat()
            if st_s.st_ino == st_d.st_ino and st_s.st_dev == st_d.st_dev:
                return
        except OSError:
            pass
        dst.unlink()
    try:
        os.link(src_real, dst)
    except OSError:
        shutil.copy2(src_real, dst)


def _measure_wav_sec(path_str: Optional[str]) -> float:
    if not path_str:
        return 0.0
    p = Path(path_str)
    if p.is_file() and p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except Exception:
            pass
    return 0.0


def _clamp_window(start_sec: float, end_sec: float, max_sec: float) -> Tuple[float, float]:
    st = round(max(0.0, min(max_sec - 0.0333, start_sec)), 4)
    en = min(max_sec, max(st + 0.0333, round(end_sec, 4)))
    dur = round(max(0.0333, en - st), 4)
    if st + dur > max_sec:
        dur = round(max(0.01, max_sec - st), 4)
    return st, dur


def _safe_id(raw: str, prefix: str = "item") -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(raw)).strip("-")
    return cleaned or prefix


def attach_hyperframes_launchers(out_dir: Path) -> None:
    """将 macOS/Windows 双击启动脚本写入 HyperFrames 工程目录，开箱即双击运行。"""
    repo_root = SKILL_ROOT.parent
    cmd_name = "启动HyperFrames工程.command"
    bat_name = "启动HyperFrames工程.bat"

    src_cmd = repo_root / cmd_name
    dst_cmd = out_dir / cmd_name
    if src_cmd.is_file():
        shutil.copy2(src_cmd, dst_cmd)
    else:
        dst_cmd.write_text(
            "#!/usr/bin/env bash\n"
            'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"\n'
            'cd "$(dirname "$0")" && npx --yes hyperframes preview "$@"\n',
            encoding="utf-8",
        )
    os.chmod(dst_cmd, 0o755)

    src_bat = repo_root / bat_name
    if src_bat.is_file():
        shutil.copy2(src_bat, out_dir / bat_name)


def _wrap_subcomposition(title: str, comp_id: str, style_css: str, body_html: str) -> str:
    """封装符合 hyperframes-core 规范的 <template> 子组合文件。"""
    return "\n".join([
        "<!doctype html>",
        '<html lang="zh-CN">',
        "  <head>",
        '    <meta charset="UTF-8" />',
        f"    <title>{html.escape(title)}</title>",
        "  </head>",
        "  <body>",
        "    <template>",
        "      <style>",
        style_css.rstrip(),
        "      </style>",
        f'      <div id="root" data-composition-id="{comp_id}" data-width="{CANVAS_WIDTH}" data-height="{CANVAS_HEIGHT}">',
        body_html.rstrip(),
        "      </div>",
        "      <script>",
        "        (function () {",
        "          const tl = gsap.timeline({ paused: true });",
        '          tl.to("#root", { duration: 0.01 }, 0);',
        f'          window.__timelines["{comp_id}"] = tl;',
        "        })();",
        "      </script>",
        "    </template>",
        "  </body>",
        "</html>",
        "",
    ])


def _stage_font(font_path: Optional[str], fonts_dir: Path) -> Optional[Tuple[str, str]]:
    """将字体文件硬链接至 media/fonts/，返回 (相对URL, CSS format)（.ttc 不适用于网页 font-face）。"""
    if not font_path:
        return None
    fp = Path(font_path)
    if not fp.is_file():
        return None
    ext = fp.suffix.lower()
    fmt_map = {".otf": "opentype", ".ttf": "truetype", ".woff2": "woff2", ".woff": "woff"}
    css_fmt = fmt_map.get(ext)
    if not css_fmt:
        return None
    dst = fonts_dir / f"sub_main{ext}"
    _link_or_copy(fp, dst)
    return f"media/fonts/{dst.name}", css_fmt


def _build_video_track_html(
    pipeline_dir: Path,
    edl: Dict[str, Any],
    segments_dir: Path,
    fps: int,
    total_frames: int,
    total_dur: float,
) -> Tuple[str, int]:
    render_final = pipeline_dir / "_render_final"
    clips = edl.get("clips", [])
    accum_frames = 0
    lines: List[str] = []
    keep_names: set[str] = set()
    raw_alias_map: Dict[Path, str] = {}

    for idx, clip in enumerate(clips):
        if idx == len(clips) - 1:
            clip_frames = max(1, total_frames - accum_frames)
        else:
            clip_frames = max(1, int(clip["timeline_out_frame"]) - int(clip["timeline_in_frame"]))
        st_f = accum_frames
        en_f = accum_frames + clip_frames
        accum_frames = en_f

        seg_file = render_final / f"seg_{idx+1:03d}.mp4"
        use_baked = seg_file.is_file() and seg_file.stat().st_size > 1024
        if use_baked:
            dst_name = seg_file.name
            _link_or_copy(seg_file, segments_dir / dst_name)
            media_start_attr = ""
        else:
            src_raw = Path(clip["media_path"]).resolve()
            if src_raw not in raw_alias_map:
                raw_alias_map[src_raw] = f"raw_{len(raw_alias_map)+1:02d}{src_raw.suffix}"
                _link_or_copy(src_raw, segments_dir / raw_alias_map[src_raw])
            dst_name = raw_alias_map[src_raw]
            src_in = max(0.0, float(clip.get("source_in_sec", 0.0)))
            media_start_attr = f' data-media-start="{src_in:.4f}"'

        keep_names.add(dst_name)
        st_s, dur_s = _clamp_window(st_f / float(fps), en_f / float(fps), total_dur)
        cid = f"seg-{idx+1:03d}"
        lines.append(
            f'        <video id="{cid}" class="clip seg-clip" src="media/segments/{dst_name}" '
            f'playsinline muted{media_start_attr} '
            f'data-start="{st_s:.4f}" data-duration="{dur_s:.4f}" data-track-index="0"></video>'
        )

    for old in segments_dir.glob("*"):
        if old.is_file() and old.name not in keep_names:
            old.unlink()

    css = "\n".join([
        "        #root { position: absolute; inset: 0; width: 100%; height: 100%; background: #000000; overflow: hidden; }",
        "        .seg-clip { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }",
    ])
    return _wrap_subcomposition("主分镜视频轨", "track-video", css, "\n".join(lines)), len(lines)


def _build_overlay_track_html(
    comp_id: str,
    title: str,
    elems: List[Dict[str, Any]],
    track_index: int,
    pipeline_dir: Path,
    overlays_dir: Path,
    font_path: Optional[str],
    cur_style_id: str,
    fps: int,
    total_dur: float,
    keep_overlays: set[str],
) -> str:
    render_final = pipeline_dir / "_render_final"
    stage_assets = pipeline_dir / "_stage_assets"
    lines: List[str] = []

    for idx, elem in enumerate(elems, start=1):
        eid = str(elem.get("id", f"elem_{idx:03d}"))
        src_png = resolve_packaging_source(elem, eid, render_final, stage_assets, font_path, cur_style_id)
        if not src_png:
            continue
        with Image.open(src_png) as im_probe:
            img_w, img_h = im_probe.size
        bbox = elem.get("bbox", {})
        if img_w == CANVAS_WIDTH and img_h == CANVAS_HEIGHT:
            px, py = 0, 0
        else:
            px = int(bbox.get("x", max(0, (CANVAS_WIDTH - img_w) // 2)))
            py = int(bbox.get("y", max(0, (CANVAS_HEIGHT - img_h) // 2)))

        png_name = f"{_safe_id(eid)}.png"
        _link_or_copy(src_png, overlays_dir / png_name)
        keep_overlays.add(png_name)

        sf = int(elem.get("start_frame", 0))
        ef = max(sf + 1, int(elem.get("end_frame", sf + fps)))
        st_s, dur_s = _clamp_window(sf / float(fps), ef / float(fps), total_dur)
        alt_txt = html.escape(str(elem.get("text_or_asset") or eid)[:24])
        lines.append(
            f'        <img id="ov-{_safe_id(eid)}" class="clip ov-img" src="media/overlays/{png_name}" alt="{alt_txt}" '
            f'data-start="{st_s:.4f}" data-duration="{dur_s:.4f}" data-track-index="{track_index}" '
            f'style="left:{px}px; top:{py}px; width:{img_w}px; height:{img_h}px;" />'
        )

    css = "\n".join([
        "        #root { position: absolute; inset: 0; width: 100%; height: 100%; overflow: hidden; pointer-events: none; background: transparent; }",
        "        .ov-img { position: absolute; display: block; pointer-events: auto; }",
    ])
    return _wrap_subcomposition(title, comp_id, css, "\n".join(lines))


def _hex_has_alpha(hex_str: str) -> bool:
    s = hex_str.strip().lstrip("#")
    if len(s) == 8:
        return int(s[6:8], 16) > 0
    return len(s) == 6


def _estimate_sub_sizes(words: List[Dict[str, Any]], base_sz: int, kw_scale: float) -> Tuple[int, int]:
    """估算单行字符像素宽度并按 [80, 860] 安全区自适应缩放字号，避免换行溢出。"""
    max_w = 708.0
    n_sz = float(base_sz)
    k_sz = round(n_sz * kw_scale)

    def _est_width(fn: float, fk: float) -> float:
        tot = 0.0
        for w in words:
            ch = str(w.get("word", ""))
            sz = fk if w.get("is_keyword") else fn
            for c in ch:
                tot += sz * (1.03 if ord(c) > 127 else 0.58)
        return tot

    est = _est_width(n_sz, k_sz)
    if est > max_w and est > 0:
        ratio = (max_w / est) * 0.96
        min_f = min(28, max(24, base_sz))
        n_sz = max(min_f, int(n_sz * ratio))
        k_sz = max(min_f + 4, int(round(k_sz * ratio)))
    return int(round(n_sz)), int(round(k_sz))


def _group_word_spans(words: List[Dict[str, Any]], fallback_text: str) -> str:
    if not words:
        return f'<span class="pt">{html.escape(fallback_text)}</span>'
    parts: List[str] = []
    cur_kw: Optional[bool] = None
    buf: List[str] = []

    def _flush() -> None:
        if not buf or cur_kw is None:
            return
        cls = "kw" if cur_kw else "pt"
        parts.append(f'<span class="{cls}">{html.escape("".join(buf))}</span>')
        buf.clear()

    for w in words:
        ch = str(w.get("word", ""))
        kw = bool(w.get("is_keyword", False))
        if cur_kw is None:
            cur_kw = kw
        elif kw != cur_kw:
            _flush()
            cur_kw = kw
        buf.append(ch)
    _flush()
    return "".join(parts)


def _build_captions_track_html(
    subs: Dict[str, Any],
    track_index: int,
    font_info: Optional[Tuple[str, str]],
    total_dur: float,
) -> Tuple[str, int]:
    tpl = subs.get("template_config", {})
    base_sz = int(tpl.get("font_size", 54))
    kw_scale = float(tpl.get("keyword_scale", 1.15))
    c_primary = str(tpl.get("primary_color", "#FFFFFF"))
    c_kw = str(tpl.get("keyword_color", "#FFD54F"))
    c_stroke = str(tpl.get("stroke_color", "#111111"))
    stroke_w = int(tpl.get("stroke_width", 4))
    bg_raw = str(tpl.get("bg_box_color", "#00000000"))
    bg_radius = int(tpl.get("bg_radius", 16))
    safe_y = int(tpl.get("safe_y_center") if tpl.get("safe_y_center") is not None else 1340)
    top_y = max(1120, min(1400, safe_y - 90))

    has_bg = _hex_has_alpha(bg_raw)
    bg_css = (
        f"background: {bg_raw}; padding: 14px 26px; border-radius: {bg_radius}px;"
        if has_bg
        else "background: transparent; padding: 4px 10px;"
    )

    font_face_css = ""
    font_stack = "sans-serif"
    if font_info:
        f_url, f_fmt = font_info
        font_face_css = (
            "        @font-face {\n"
            '          font-family: "SubMain";\n'
            f'          src: url("{f_url}") format("{f_fmt}");\n'
            "          font-display: block;\n"
            "        }\n"
        )
        font_stack = '"SubMain", sans-serif'

    css = "\n".join([
        font_face_css.rstrip("\n"),
        "        #root { position: absolute; inset: 0; width: 100%; height: 100%; overflow: hidden; pointer-events: none; background: transparent; }",
        f"        .cap-clip {{ position: absolute; left: 80px; width: 780px; top: {top_y}px; height: 180px; display: flex; align-items: center; justify-content: center; text-align: center; pointer-events: none; }}",
        f"        .cap-box {{ display: inline-flex; align-items: baseline; justify-content: center; white-space: nowrap; font-family: {font_stack}; font-weight: 800; line-height: 1.15; letter-spacing: 1px; paint-order: stroke fill; -webkit-text-stroke: {max(1, stroke_w * 2)}px {c_stroke}; {bg_css} }}",
        f"        .cap-box .pt {{ color: {c_primary}; font-size: var(--fs, {base_sz}px); }}",
        f"        .cap-box .kw {{ color: {c_kw}; font-size: var(--ks, {int(round(base_sz * kw_scale))}px); font-weight: 900; }}",
    ]).strip("\n")

    lines: List[str] = []
    entries = subs.get("entries", [])
    for idx, sub in enumerate(entries, start=1):
        txt = str(sub.get("text", "")).strip()
        if not txt:
            continue
        words = sub.get("words") or [{"word": ch, "is_keyword": False} for ch in txt]
        fs, ks = _estimate_sub_sizes(words, base_sz, kw_scale)
        st_s, dur_s = _clamp_window(float(sub.get("start_sec", 0.0)), float(sub.get("end_sec", 1.0)), total_dur)
        sid = _safe_id(str(sub.get("id", f"sub_{idx:03d}")))
        spans_html = _group_word_spans(words, txt)
        lines.append(
            f'        <div class="clip cap-clip" id="{sid}" data-start="{st_s:.4f}" data-duration="{dur_s:.4f}" data-track-index="{track_index}">'
            f'<span class="cap-box" style="--fs:{fs}px;--ks:{ks}px;">{spans_html}</span>'
            f"</div>"
        )

    return _wrap_subcomposition("双色安全区可编辑字幕轨", "track-captions", css, "\n".join(lines)), len(lines)


def _build_root_index_html(
    project_title: str,
    total_dur: float,
    fps: int,
    tracks: List[Dict[str, Any]],
    audio_items: List[Dict[str, Any]],
) -> str:
    slot_css: List[str] = []
    slot_divs: List[str] = []
    for tr in tracks:
        slot_css.append(
            f"      #{tr['id']} {{ position: absolute; inset: 0; width: 100%; height: 100%; z-index: {tr['z']};"
            + (" pointer-events: none;" if tr["kind"] == "captions" else "")
            + " }"
        )
        slot_divs.append(
            f'      <div id="{tr["id"]}" '
            f'data-composition-id="{tr["id"]}" '
            f'data-composition-src="{tr["src"]}" '
            f'data-start="0" data-duration="{total_dur:.4f}" '
            f'data-track-index="{tr["track_index"]}" '
            f'data-track-kind="{tr["kind"]}" '
            f'data-width="{CANVAS_WIDTH}" data-height="{CANVAS_HEIGHT}"></div>'
        )

    audio_tags: List[str] = []
    for au in audio_items:
        audio_tags.append(
            f'      <audio id="{au["id"]}" src="{au["src"]}" '
            f'data-start="0" data-duration="{au["duration"]:.4f}" '
            f'data-track-index="{au["track_index"]}" data-volume="{au["volume"]}"></audio>'
        )

    return "\n".join([
        "<!doctype html>",
        '<html lang="zh-CN">',
        "  <head>",
        '    <meta charset="UTF-8" />',
        f'    <meta name="viewport" content="width={CANVAS_WIDTH}, height={CANVAS_HEIGHT}" />',
        f"    <title>{html.escape(project_title)} · HyperFrames 可编辑工程</title>",
        '    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>',
        "    <style>",
        "      * { margin: 0; padding: 0; box-sizing: border-box; }",
        "      html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; background: #000000; }",
        "      #root { position: relative; width: 100%; height: 100%; overflow: hidden; background: #000000; }",
        *slot_css,
        "    </style>",
        "  </head>",
        "  <body>",
        "    <div",
        '      id="root"',
        '      data-composition-id="main"',
        '      data-start="0"',
        f'      data-duration="{total_dur:.4f}"',
        f'      data-fps="{fps}"',
        f'      data-width="{CANVAS_WIDTH}"',
        f'      data-height="{CANVAS_HEIGHT}"',
        "    >",
        *slot_divs,
        *audio_tags,
        "    </div>",
        "    <script>",
        "      (function () {",
        "        const tl = gsap.timeline({ paused: true });",
        '        tl.to("#root", { duration: 0.01 }, 0);',
        '        window.__timelines["main"] = tl;',
        "      })();",
        "    </script>",
        "  </body>",
        "</html>",
        "",
    ])


def export_hyperframes_draft(pipeline_dir: Path, simulate_failure: bool = False) -> Dict[str, Any]:
    """将流水线剪辑包装成果导出为可双击打开的 HyperFrames Studio 工程目录。"""
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

        subs = read_json(subs_p) if subs_p.exists() else {"entries": [], "template_config": {}}
        pkg = read_json(pkg_p) if pkg_p.exists() else {}
        audio_events = read_json(audio_p) if audio_p.exists() else {}
        mix_metrics = read_json(mix_p) if mix_p.exists() else {}

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

        # 1. 视频分镜子组合
        video_html, n_segs = _build_video_track_html(pipeline_dir, edl, segments_dir, fps, total_frames, total_dur)
        (comp_dir / "video_track.html").write_text(video_html, encoding="utf-8")

        tracks: List[Dict[str, Any]] = [{
            "id": "track-video",
            "src": "compositions/video_track.html",
            "track_index": 0,
            "kind": "video",
            "z": 1,
        }]
        next_track = 1

        # 2. 风格谱画框 / 阶段卡 / 语义花字子组合（无该类元素时自动略过，保持时间线简洁）
        font_path = subs.get("font_path") or find_commercial_font()
        cur_style_id = str(subs.get("template_id") or pkg.get("style_id") or "style_14")
        stage_all = list(pkg.get("stage_overlays", []))
        stage_wins = [
            e for e in stage_all
            if ("_win_" in str(e.get("id", ""))) or (
                int(e.get("bbox", {}).get("w", 0)) == CANVAS_WIDTH and int(e.get("bbox", {}).get("h", 0)) == CANVAS_HEIGHT
            )
        ]
        stage_cards = [e for e in stage_all if e not in stage_wins]
        flower_elems = list(pkg.get("visual_elements", []))
        keep_overlays: set[str] = set()

        def _maybe_add_overlay_sub(comp_id: str, title: str, fname: str, elems: List[Dict[str, Any]], z_idx: int) -> None:
            nonlocal next_track
            comp_file = comp_dir / fname
            if not elems:
                if comp_file.exists():
                    comp_file.unlink()
                return
            html_str = _build_overlay_track_html(
                comp_id, title, elems, next_track,
                pipeline_dir, overlays_dir, font_path, cur_style_id, fps, total_dur, keep_overlays,
            )
            comp_file.write_text(html_str, encoding="utf-8")
            tracks.append({
                "id": comp_id,
                "src": f"compositions/{fname}",
                "track_index": next_track,
                "kind": "graphics",
                "z": z_idx,
            })
            next_track += 1

        _maybe_add_overlay_sub("track-stage-win", "风格画框背景窗轨", "stage_win_track.html", stage_wins, 10)
        _maybe_add_overlay_sub("track-stage-card", "阶段知识卡与进度条轨", "stage_card_track.html", stage_cards, 20)
        _maybe_add_overlay_sub("track-flower", "语义花字贴纸轨", "flower_track.html", flower_elems, 30)

        for old_ov in overlays_dir.glob("*.png"):
            if old_ov.name not in keep_overlays:
                old_ov.unlink()

        # 3. 原生 HTML/CSS 双色高亮可编辑字幕子组合
        font_info = _stage_font(font_path, fonts_dir)
        cap_html, n_subs = _build_captions_track_html(subs, next_track, font_info, total_dur)
        (comp_dir / "captions_track.html").write_text(cap_html, encoding="utf-8")
        tracks.append({
            "id": "track-captions",
            "src": "compositions/captions_track.html",
            "track_index": next_track,
            "kind": "captions",
            "z": 40,
        })

        # 4. 三轨独立 WAV 音频
        voice_src = next(
            (cand for cand in [
                str(pipeline_dir / "08_voice_denoised.wav"),
                str(pipeline_dir / "03_locked_voice.wav"),
                mix_metrics.get("master_wav"),
            ] if cand and Path(cand).is_file()),
            None,
        )
        audio_specs = [
            ("audio-voice", voice_src, "voice.wav", 10, "1.0"),
            ("audio-bgm", audio_events.get("locked_bgm_wav"), "bgm.wav", 11, "0.2"),
            ("audio-sfx", str(pipeline_dir / "06_sfx_track.wav"), "sfx.wav", 12, "1.0"),
        ]
        audio_items: List[Dict[str, Any]] = []
        keep_audio: set[str] = set()
        for aid, src_str, dst_name, t_idx, vol in audio_specs:
            if not src_str or not Path(src_str).is_file():
                continue
            src_p = Path(src_str)
            _link_or_copy(src_p, audio_dir / dst_name)
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

        # 5. 生成根 index.html、hyperframes.json、使用说明与双击启动器
        root_html = _build_root_index_html(proj_slug, total_dur, fps, tracks, audio_items)
        (draft_dir / "index.html").write_text(root_html, encoding="utf-8")
        write_json(draft_dir / "hyperframes.json", {
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "media": {"autoProxy": True},
        })
        attach_hyperframes_launchers(draft_dir)

        readme = "\n".join([
            f"【{proj_slug} HyperFrames Studio 可编辑多轨工程使用指南】",
            "",
            "1. 一键打开可视化时间线编辑器：",
            "   直接双击本目录中的「启动HyperFrames工程.command」（macOS）或「启动HyperFrames工程.bat」（Windows）。",
            "   前置要求：已安装 Node.js >= 22 与 FFmpeg（macOS 可运行 brew install node ffmpeg）。",
            "",
            "2. 工程内包含以下独立可编辑轨道：",
            f"   - compositions/video_track.html      : {n_segs} 段精剪调色视频分镜",
            f"   - compositions/stage_* / flower.html : {len(stage_wins) + len(stage_cards)} 个画框/阶段卡 + {len(flower_elems)} 个花字",
            f"   - compositions/captions_track.html   : {n_subs} 句原生 HTML 双色高亮可编辑字幕（可直接改文案、颜色、字号与坐标）",
            f"   - media/audio/                       : {len(audio_items)} 条独立音轨（人声干音 / BGM / SFX）",
            "",
            "3. 渲染最终 MP4（如需在 Studio 修改后重新出片）：",
            "   可在打开的浏览器 Studio 中点击渲染，或在本目录终端运行：",
            "   npx --yes hyperframes render --quality looks --output output_edited.mp4",
            "",
        ])
        (draft_dir / "README_HyperFrames使用说明.txt").write_text(readme, encoding="utf-8")

        status = {
            "status": "success",
            "degraded_to_mp4_only": False,
            "draft_dir": str(draft_dir.resolve()),
            "index_html": str((draft_dir / "index.html").resolve()),
            "launcher_command": str((draft_dir / "启动HyperFrames工程.command").resolve()),
            "segments_count": n_segs,
            "stage_overlays_count": len(stage_wins) + len(stage_cards),
            "flower_texts_count": len(flower_elems),
            "captions_count": n_subs,
            "audio_tracks_count": len(audio_items),
            "message": f"HyperFrames 可编辑工程导出成功（含 {n_segs} 段分镜 + {len(stage_wins)+len(stage_cards)+len(flower_elems)} 个包装层 + {n_subs} 条原生双色字幕 + 双击启动器）。",
        }
        write_json(pipeline_dir / "hyperframes_export_status.json", status)
        print(f"[hyperframes] 可编辑工程 + 双击启动器导出成功 -> {draft_dir}")
        return status
    except Exception as exc:
        status = {
            "status": "degraded_to_mp4_only",
            "degraded_to_mp4_only": True,
            "draft_dir": None,
            "error": str(exc),
            "message": f"提示：HyperFrames 工程导出失败（{exc}），已自动降级，不影响 MP4 与剪映工程交付。",
        }
        write_json(pipeline_dir / "hyperframes_export_status.json", status)
        print(f"[hyperframes][降级提示] {status['message']}")
        return status


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="出片3: HyperFrames Studio 全多轨可编辑工程 + 双击启动器导出")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--simulate-failure", action="store_true", help="模拟导出失败验证降级逻辑")
    args = parser.parse_args()
    export_hyperframes_draft(Path(args.pipeline_dir), simulate_failure=args.simulate_failure)


if __name__ == "__main__":
    main()
