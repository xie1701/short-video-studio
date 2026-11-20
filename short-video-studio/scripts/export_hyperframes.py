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
    find_bin,
    find_commercial_font,
    frames_to_sec,
    get_wav_duration_sec,
    is_verified_commercial_font,
    link_or_copy,
    load_style_applied,
    load_subtitle_templates,
    maybe_reexec_in_venv,
    place_bbox_in_zone,
    read_json,
    resolve_audio_stems,
    sec_to_frames,
    write_json,
)

maybe_reexec_in_venv()

from PIL import Image
from export_jianying import _export_graded_segment
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


def _offline_subcomp_timeline_script(comp_id: str) -> str:
    return "\n".join([
        "      <script>",
        "        (function () {",
        "          window.__timelines = window.__timelines || {};",
        f"          var compId = '{comp_id}';",
        "          var state = { paused: true, time: 0 };",
        "          function syncNodes(t) {",
        f"            var root = document.getElementById('{comp_id}-root');",
        "            if (!root) return;",
        "            var clips = root.querySelectorAll('.clip[data-start]');",
        "            for (var i = 0; i < clips.length; i++) {",
        "              var el = clips[i];",
        "              var st = parseFloat(el.getAttribute('data-start') || '0');",
        "              var dur = parseFloat(el.getAttribute('data-duration') || '0');",
        "              var vis = (t >= st && t < st + dur);",
        "              el.style.visibility = vis ? 'visible' : 'hidden';",
        "            }",
        "          }",
        "          window.__timelines[compId] = {",
        "            pause: function (t) { state.paused = true; if (typeof t === 'number') { state.time = t; syncNodes(t); } return this; },",
        "            play: function () { state.paused = false; return this; },",
        "            seek: function (t) { state.time = Number(t) || 0; syncNodes(state.time); return this; },",
        "            time: function (t) { if (typeof t === 'number') { state.time = t; syncNodes(t); } return state.time; }",
        "          };",
        "        })();",
        "      </script>",
    ])


def _wrap_subcomposition(title: str, comp_id: str, style_css: str, body_html: str) -> str:
    """严格遵循 hyperframes-core <template> 传输契约：样式、唯一 ID 根与脚本全置于 <template> 内。"""
    root_id = f"{comp_id}-root"
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '  <meta charset="utf-8" />',
        f"  <title>{html.escape(title)}</title>",
        "</head>",
        "<body>",
        "  <template>",
        "    <style>",
        "      * { box-sizing: border-box; margin: 0; padding: 0; }",
        f"      #{root_id} {{ position: absolute; inset: 0; width: 100%; height: 100%; overflow: hidden; background: transparent; }}",
        "      .clip { position: absolute; left: 0; top: 0; width: 100%; height: 100%; }",
        style_css,
        "    </style>",
        f'    <div id="{html.escape(root_id)}" data-composition-id="{html.escape(comp_id)}">',
        body_html,
        "    </div>",
        _offline_subcomp_timeline_script(comp_id),
        "  </template>",
        "</body>",
        "</html>",
    ]) + "\n"


def _offline_main_engine(total_dur: float, fps: int) -> str:
    """完整可播放/可 seek 离线时间线驱动引擎（挂载 <template> 子合成并逐帧同步音画显隐）。"""
    return "\n".join([
        "  <script>",
        "    (function () {",
        "      window.__timelines = window.__timelines || {};",
        f"      var TOTAL_DUR = {total_dur:.4f};",
        f"      var FPS = {fps};",
        "      var state = { paused: true, time: 0, lastTs: 0, raf: 0 };",
        "      var hasExternalRuntime = !!window.__HYPERFRAMES_RUNTIME__;",
        "      var isAutomation = !!navigator.webdriver;",
        "      var mountedPromise = null;",
        "",
        "      function waitActiveVideosReady() {",
        "        var vids = Array.prototype.slice.call(document.querySelectorAll('video.clip'));",
        "        var pending = vids.filter(function (v) {",
        "          return v.style.visibility === 'visible' && v.readyState >= 1 && v.seeking;",
        "        });",
        "        if (!pending.length) return Promise.resolve();",
        "        return Promise.all(pending.map(function (v) {",
        "          return new Promise(function (resolve) {",
        "            var timer = setTimeout(resolve, 250);",
        "            v.addEventListener('seeked', function onS() {",
        "              clearTimeout(timer);",
        "              v.removeEventListener('seeked', onS);",
        "              resolve();",
        "            });",
        "          });",
        "        }));",
        "      }",
        "",
        "      function hydrateSubCompositions() {",
        "        if (mountedPromise) return mountedPromise;",
        "        var hosts = Array.prototype.slice.call(document.querySelectorAll('[data-composition-src]'));",
        "        mountedPromise = Promise.all(hosts.map(function (host) {",
        "          if (host.getAttribute('data-hf-hydrated') === '1') return Promise.resolve();",
        "          var src = host.getAttribute('data-composition-src');",
        "          if (!src) return Promise.resolve();",
        "          return fetch(src).then(function (r) { return r.ok ? r.text() : ''; }).then(function (txt) {",
        "            if (!txt) return;",
        "            var doc = new DOMParser().parseFromString(txt, 'text/html');",
        "            var tpl = doc.querySelector('template');",
        "            var frag = tpl ? document.importNode(tpl.content, true) : document.createDocumentFragment();",
        "            var mediaNodes = frag.querySelectorAll('[src]');",
        "            for (var i = 0; i < mediaNodes.length; i++) {",
        "              var s = mediaNodes[i].getAttribute('src') || '';",
        "              if (s.indexOf('../media/') === 0) mediaNodes[i].setAttribute('src', s.slice(3));",
        "            }",
        "            host.innerHTML = '';",
        "            host.appendChild(frag);",
        "            host.setAttribute('data-hf-hydrated', '1');",
        "          }).catch(function () {});",
        "        }));",
        "        return mountedPromise;",
        "      }",
        "",
        "      function applyAudioVolumes() {",
        "        var audios = document.querySelectorAll('audio[data-volume]');",
        "        for (var i = 0; i < audios.length; i++) {",
        "          var v = parseFloat(audios[i].getAttribute('data-volume') || '1');",
        "          if (!isNaN(v)) audios[i].volume = Math.max(0, Math.min(1, v));",
        "        }",
        "      }",
        "",
        "      function syncTime(t) {",
        "        var clamped = Math.max(0, Math.min(TOTAL_DUR, Number(t) || 0));",
        "        state.time = clamped;",
        "        var clips = document.querySelectorAll('.clip[data-start]');",
        "        for (var i = 0; i < clips.length; i++) {",
        "          var el = clips[i];",
        "          var st = parseFloat(el.getAttribute('data-start') || '0');",
        "          var dur = parseFloat(el.getAttribute('data-duration') || '0');",
        "          var active = (clamped >= st && clamped < st + dur);",
        "          el.style.visibility = active ? 'visible' : 'hidden';",
        "          if (el.tagName === 'VIDEO') {",
        "            var ms = parseFloat(el.getAttribute('data-media-start') || '0');",
        "            var targetVidT = ms + Math.max(0, clamped - st);",
        "            if (active && Math.abs((el.currentTime || 0) - targetVidT) > 0.12) {",
        "              try { el.currentTime = targetVidT; } catch (e) {}",
        "            }",
        "            if (active && !state.paused && el.paused) { el.play().catch(function(){}); }",
        "            else if ((!active || state.paused) && !el.paused) { el.pause(); }",
        "          }",
        "        }",
        "        var audios = document.querySelectorAll('audio[data-start]');",
        "        for (var j = 0; j < audios.length; j++) {",
        "          var au = audios[j];",
        "          var ast = parseFloat(au.getAttribute('data-start') || '0');",
        "          var adur = parseFloat(au.getAttribute('data-duration') || '0');",
        "          var aActive = (clamped >= ast && clamped < ast + adur);",
        "          var targetAuT = Math.max(0, clamped - ast);",
        "          if (aActive && Math.abs((au.currentTime || 0) - targetAuT) > 0.15) {",
        "            try { au.currentTime = targetAuT; } catch (e) {}",
        "          }",
        "          if (aActive && !state.paused && au.paused) { au.play().catch(function(){}); }",
        "          else if ((!aActive || state.paused) && !au.paused) { au.pause(); }",
        "        }",
        "        var rng = document.getElementById('hf-scrubber');",
        "        var tc = document.getElementById('hf-timecode');",
        "        if (rng && Math.abs(parseFloat(rng.value) - clamped) > 0.02) rng.value = clamped.toFixed(2);",
        "        if (tc) tc.textContent = clamped.toFixed(2) + 's / ' + TOTAL_DUR.toFixed(2) + 's';",
        "      }",
        "",
        "      function tick(ts) {",
        "        if (state.paused) return;",
        "        if (!state.lastTs) state.lastTs = ts;",
        "        var dt = Math.min(0.1, (ts - state.lastTs) / 1000.0);",
        "        state.lastTs = ts;",
        "        var nxt = state.time + dt;",
        "        if (nxt >= TOTAL_DUR) { syncTime(TOTAL_DUR); api.pause(); return; }",
        "        syncTime(nxt);",
        "        state.raf = requestAnimationFrame(tick);",
        "      }",
        "",
        "      var api = {",
        "        pause: function (t) {",
        "          state.paused = true; state.lastTs = 0;",
        "          if (state.raf) cancelAnimationFrame(state.raf);",
        "          if (typeof t === 'number') syncTime(t); else syncTime(state.time);",
        "          var btn = document.getElementById('hf-play-btn');",
        "          if (btn) btn.textContent = '播放';",
        "          return this;",
        "        },",
        "        play: function () {",
        "          applyAudioVolumes();",
        "          if (state.time >= TOTAL_DUR - 0.05) state.time = 0;",
        "          state.paused = false; state.lastTs = 0;",
        "          var btn = document.getElementById('hf-play-btn');",
        "          if (btn) btn.textContent = '暂停';",
        "          state.raf = requestAnimationFrame(tick);",
        "          return this;",
        "        },",
        "        seek: function (t) { syncTime(t); return this; },",
        "        time: function (t) { if (typeof t === 'number') syncTime(t); return state.time; }",
        "      };",
        "      window.__timelines['main'] = api;",
        "      window.DUR = TOTAL_DUR;",
        "      window.seek = function (t) {",
        "        api.seek(t);",
        "        return isAutomation ? waitActiveVideosReady() : Promise.resolve();",
        "      };",
        "",
        "      if (!hasExternalRuntime) {",
        "        hydrateSubCompositions().then(function () {",
        "          applyAudioVolumes();",
        "          syncTime(0);",
        "          var readyFonts = (document.fonts && document.fonts.ready) ? document.fonts.ready : Promise.resolve();",
        "          return readyFonts.then(function () {",
        "            return isAutomation ? waitActiveVideosReady() : Promise.resolve();",
        "          });",
        "        }).then(function () {",
        "          window.READY = true;",
        "          if (!isAutomation) {",
        "            var bar = document.createElement('div');",
        "            bar.id = 'hf-offline-controls';",
        "            bar.style.cssText = 'position:fixed;bottom:14px;left:16px;right:16px;z-index:9999;background:rgba(15,23,42,0.88);border:1px solid #334155;border-radius:10px;padding:8px 12px;display:flex;align-items:center;gap:10px;font-family:sans-serif;font-size:13px;color:#f8fafc;';",
        "            var btn = document.createElement('button');",
        "            btn.id = 'hf-play-btn'; btn.textContent = '播放';",
        "            btn.style.cssText = 'background:#facc15;color:#0f172a;border:none;border-radius:6px;padding:4px 12px;font-weight:700;cursor:pointer;';",
        "            btn.onclick = function () { state.paused ? api.play() : api.pause(); };",
        "            var rng = document.createElement('input');",
        "            rng.id = 'hf-scrubber'; rng.type = 'range'; rng.min = '0'; rng.max = String(TOTAL_DUR); rng.step = (1 / FPS).toFixed(4); rng.value = '0';",
        "            rng.style.cssText = 'flex:1;cursor:pointer;';",
        "            rng.oninput = function () { api.seek(parseFloat(rng.value)); };",
        "            var tc = document.createElement('span');",
        "            tc.id = 'hf-timecode'; tc.textContent = '0.00s / ' + TOTAL_DUR.toFixed(2) + 's';",
        "            bar.appendChild(btn); bar.appendChild(rng); bar.appendChild(tc);",
        "            document.body.appendChild(bar);",
        "          }",
        "        });",
        "      } else {",
        "        window.READY = true;",
        "      }",
        "    })();",
        "  </script>",
    ])


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
    return f"media/fonts/{src.name}", fmt


def _build_video_track_html(
    pipeline_dir: Path,
    edl: Dict[str, Any],
    segments_dir: Path,
    fps: int,
    total_dur: float,
) -> Tuple[str, int]:
    clips = edl.get("clips") or []
    color_p = pipeline_dir / "07_color_plan.json"
    color_plan = read_json(color_p) if color_p.exists() else {}
    man_p = pipeline_dir / "01_manifest.json"
    manifest = read_json(man_p) if man_p.exists() else {"items": []}

    eye_by_mp4: Dict[str, Dict[str, Any]] = {}
    for item in (manifest.get("items") or []):
        mp = str(item.get("normalized_mp4") or "")
        if mp and item.get("eye_anchor"):
            eye_by_mp4[mp] = item["eye_anchor"]
            eye_by_mp4[str(Path(mp).resolve())] = item["eye_anchor"]
    color_by_cid = {
        str(p.get("clip_id")): str(p.get("vf_filter") or "").strip()
        for p in (color_plan.get("clip_plans") or [])
        if p.get("apply_color_grade") and p.get("vf_filter")
    }

    ffmpeg_bin = find_bin("ffmpeg")
    render_work_dir = pipeline_dir / "_render_final"

    src_to_rel: Dict[str, str] = {}
    used_names: set[str] = set()
    keep_segs: set[str] = set()
    body_lines: List[str] = []

    for idx, c in enumerate(clips, start=1):
        cid = str(c.get("clip_id") or f"seg_{idx:03d}")
        mp = str(c.get("media_path") or "")
        src_p = Path(mp)
        if not mp or not src_p.is_file():
            raise FileNotFoundError(f"HyperFrames 导出缺失分镜文件: {mp}")

        c_idx = idx - 1
        color_vf = str(c.get("color_filter") or color_by_cid.get(cid) or "").strip() or None
        kb = c.get("ken_burns")
        has_kb = isinstance(kb, dict) and ("zoom_start" in kb or "zoom_end" in kb)
        has_vf_extra = bool(str(c.get("vf_extra") or "").strip())

        st_s, en_s = _clamp_window(float(c.get("timeline_in_sec", 0.0)), float(c.get("timeline_out_sec", 0.0)), total_dur)
        dur_s = round(max(0.04, en_s - st_s), 4)
        src_in = max(0.0, float(c.get("source_in_sec", 0.0)))
        src_avail = max(0.1, float(c.get("source_out_sec", src_in + dur_s)) - src_in)
        needs_bake = bool(color_vf or has_kb or has_vf_extra or (src_avail < dur_s - 0.01))

        prebaked_seg = render_work_dir / f"seg_{c_idx:03d}_f.mp4"
        jy_seg = pipeline_dir / "jianying_draft" / "media" / "segments" / f"seg_{c_idx:03d}.mp4"
        if needs_bake and ffmpeg_bin:
            seg_name = f"seg_{c_idx:03d}.mp4"
            dst_seg = segments_dir / seg_name
            if prebaked_seg.is_file() and prebaked_seg.stat().st_size > 1024:
                link_or_copy(prebaked_seg, dst_seg)
            elif jy_seg.is_file() and jy_seg.stat().st_size > 1024:
                link_or_copy(jy_seg, dst_seg)
            else:
                render_work_dir.mkdir(parents=True, exist_ok=True)
                _export_graded_segment(ffmpeg_bin, c, color_vf, eye_by_mp4, prebaked_seg, fps)
                link_or_copy(prebaked_seg, dst_seg)
            keep_segs.add(seg_name)
            rel_vid_src = f"../media/segments/{seg_name}"
            eff_media_start = 0.0
        else:
            if mp not in src_to_rel:
                name = src_p.name
                if name in used_names:
                    name = f"{len(used_names) + 1:02d}_{src_p.name}"
                used_names.add(name)
                link_or_copy(src_p, segments_dir / name)
                keep_segs.add(name)
                src_to_rel[mp] = f"../media/segments/{name}"
            rel_vid_src = src_to_rel[mp]
            eff_media_start = src_in

        vid_id = _safe_id(cid, "seg")
        body_lines.append(
            f'      <video class="clip" id="track-video-{vid_id}" src="{html.escape(rel_vid_src)}" '
            f'muted playsinline preload="metadata" '
            f'data-media-start="{eff_media_start:.4f}" data-start="{st_s:.4f}" data-duration="{dur_s:.4f}" data-track-index="0"></video>'
        )

    for stale_seg in segments_dir.glob("*"):
        if stale_seg.name not in keep_segs and stale_seg.is_file():
            stale_seg.unlink()

    css = "      video.clip { object-fit: cover; }"
    return _wrap_subcomposition("主视频分镜轨", "track-video", css, "\n".join(body_lines)), len(clips)


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
        px, py = place_bbox_in_zone(elem, pw, ph, SAFE_ZONE, (def_x, def_y))

        oid = _safe_id(str(elem.get("id") or f"ov_{idx:03d}"), "ov")
        lines.append(
            f'      <img class="clip ov-img" id="{comp_id}-{oid}" src="../media/overlays/{dst_name}" '
            f'style="left:{px}px;top:{py}px;width:{pw}px;height:{ph}px;" '
            f'data-start="{st_s:.4f}" data-duration="{dur_s:.4f}" data-track-index="{track_index}" />'
        )

    css = "      .ov-img { position: absolute; object-fit: contain; pointer-events: none; }"
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
    bg_hex = str(tpl.get("bg_box_color") or tpl.get("bg_box") or "none")
    bg_radius = int(tpl.get("bg_radius", 16))
    bg_css = f"background: {bg_hex}; padding: 12px 24px; border-radius: {bg_radius}px;" if _hex_has_alpha(bg_hex) else ""

    font_css = ""
    font_stack = '"AlimamaShuHeiTi", "Noto Sans CJK SC", sans-serif'
    if font_info:
        f_url, f_fmt = font_info
        font_css = (
            "      @font-face {\n"
            '        font-family: "CustomStudioFont";\n'
            f'        src: url("{f_url}") format("{f_fmt}");\n'
            "        font-display: swap;\n"
            "      }\n"
        )
        font_stack = f'"CustomStudioFont", {font_stack}'

    stroke_css = "0px" if orig_stroke_w <= 0 else f"{max(1, orig_stroke_w * 2)}px {c_stroke}"
    shadow_css = "text-shadow: 2px 3px 6px rgba(0,0,0,0.68);" if tpl.get("shadow", True) else ""

    css = "\n".join([
        font_css,
        "      .cap-clip { position: absolute; display: flex; align-items: center; justify-content: center; text-align: center; }",
        f"      .cap-box {{ display: inline-flex; flex-wrap: wrap; align-items: baseline; justify-content: center; "
        f"font-family: {font_stack}; font-size: {font_sz}px; font-weight: 800; line-height: 1.18; "
        f"color: {c_primary}; paint-order: stroke fill; -webkit-text-stroke: {stroke_css}; {shadow_css} {bg_css} }}",
        f"      .cap-kw {{ color: {c_kw}; font-size: {int(round(font_sz * kw_scale))}px; }}",
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

        kw_set = set(str(k) for k in (sub.get("keywords") or []))
        words = sub.get("words") or []
        if words:
            spans = "".join(
                f'<span class="cap-kw">{html.escape(str(w.get("word", "")))}</span>'
                if (w.get("is_keyword") or str(w.get("word", "")) in kw_set)
                else html.escape(str(w.get("word", "")))
                for w in words
                if str(w.get("word", "")).strip()
            )
        else:
            spans = ""
        if not spans:
            spans = html.escape(str(sub.get("text") or ""))

        sid = _safe_id(str(sub.get("id") or f"sub_{idx:03d}"), "sub")
        body_lines.append(
            f'      <div class="clip cap-clip" id="track-captions-{sid}" '
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
    font_info: Optional[Tuple[str, str]] = None,
) -> str:
    font_css = ""
    if font_info:
        f_url, f_fmt = font_info
        font_css = (
            "    @font-face {\n"
            '      font-family: "CustomStudioFont";\n'
            f'      src: url("{f_url}") format("{f_fmt}");\n'
            "      font-display: swap;\n"
            "    }\n"
        )
    track_divs = "\n".join(
        f'    <div id="host-{html.escape(tr["id"])}" class="track-layer" style="z-index:{tr["z"]};" '
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
        font_css,
        "    * { box-sizing: border-box; margin: 0; padding: 0; }",
        f"    html, body {{ width: {CANVAS_WIDTH}px; height: {CANVAS_HEIGHT}px; background: #000; overflow: hidden; }}",
        "    #main { position: relative; width: 100%; height: 100%; overflow: hidden; }",
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
        _offline_main_engine(total_dur, fps),
        "</body>",
        "</html>",
    ]) + "\n"


def export_hyperframes_draft(pipeline_dir: Path, simulate_failure: bool = False) -> Dict[str, Any]:
    """将流水线剪辑包装成果导出为符合 hyperframes-core 契约且可离线播放的工程目录。"""
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

        # 1. 视频分镜子组合（带调色/KB/防尾帧裁切独立片段）
        video_html, n_segs = _build_video_track_html(pipeline_dir, edl, segments_dir, fps, total_dur)
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
        if font_info:
            keep_font_name = Path(font_info[0]).name
            for old_f in fonts_dir.glob("*"):
                if old_f.name != keep_font_name and old_f.is_file():
                    old_f.unlink()
        cap_html, n_subs = _build_captions_track_html(subs, templates, next_track, font_info, total_dur)
        (comp_dir / "captions_track.html").write_text(cap_html, encoding="utf-8")
        tracks.append({
            "id": "track-captions",
            "src": "compositions/captions_track.html",
            "track_index": next_track,
            "kind": "captions",
            "z": 40,
        })

        # 4. 三轨同源独立 WAV 音频（通过 common.resolve_audio_stems 优先挂载 08_sfx_master.wav）
        stems = resolve_audio_stems(pipeline_dir, audio_events=audio_events, mix_metrics=mix_metrics)
        voice_src = stems.get("voice")
        bgm_src = stems.get("bgm")
        sfx_src = stems.get("sfx")
        bgm_ducked = bgm_src is not None and "08_bgm_ducked" in str(bgm_src.name)
        sfx_master = sfx_src is not None and "08_sfx_master" in str(sfx_src.name)
        master_gain_db = float(mix_metrics.get("master_gain_db", 0.0) or 0.0)
        sfx_vol = "1.0" if sfx_master else f"{min(1.0, max(0.05, 10.0 ** (master_gain_db / 20.0))):.3f}"

        audio_specs = [
            ("audio-voice", str(voice_src) if voice_src else None, "voice_master.wav", 10, "1.0"),
            ("audio-bgm", str(bgm_src) if bgm_src else None, "bgm_ducked.wav", 11, "1.0" if bgm_ducked else "0.2"),
            ("audio-sfx", str(sfx_src) if sfx_src else None, "sfx_master.wav", 12, sfx_vol),
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
            if old_au.name not in keep_audio and old_au.is_file():
                old_au.unlink()

        # 5. 生成根 index.html、hyperframes.json 与启动器
        root_html = _build_root_index_html(proj_slug, total_dur, fps, tracks, audio_items, font_info=font_info)
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
            "2. 所有子合成遵循 <template> 传输契约，内建可播放/可拖拽 Seek 时间线预览引擎（不依赖外部 CDN）：",
            f"   - compositions/video_track.html    : {n_segs} 段精剪调色视频分镜",
            f"   - compositions/flower_track.html   : {len(flower_elems)} 个视觉包装层",
            f"   - compositions/captions_track.html : {n_subs} 条权威安全区双色字幕 (y <= {SUBTITLE_SAFE_ZONE['y_max']})",
            f"   - media/audio/                     : {len(audio_items)} 条独立同源分轨 WAV (voice_master / bgm_ducked / sfx_master)",
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
