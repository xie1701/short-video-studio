#!/usr/bin/env python3
"""
[出片 2：剪映专业版可编辑工程导出封装]
由于剪映专业版 6.0+（含 macOS 11.7.0）对内部 draft_info.json 强制启用私有加密，
直接复制第三方明文 draft_content.json 会被判定为草稿损坏无法打开。
本模块遵循剪映官方原生开放导入规范，导出完整多轨可编辑工程包 (jianying_draft/)：
- <project_name>.fcpxml : Apple FCPXML 1.10 多轨工程（主视频分镜 + 风格谱画框/卡片/花字 Alpha 透明轨 + 双色安全区字幕 Alpha 透明轨 + 三轨独立 WAV）
- alpha_layers/         : 1080x1920 带透明通道的视频轨素材（macOS 优先 HEVC+Alpha `hvc1`，跨平台自动回退 ProRes4444/QTRLE）
- overlays/             : 1080x1920 透明全画幅 PNG 底图备份
- <project_name>.srt    : 可选文字替换备选字幕（仅当需在剪映内改错别字或重排文本时拖入）
- <project_name>.ass    : 高级样式字幕备份（若步骤 5 已生成则同步附带）
- <lut_name>.cube       : 风格专属 3D LUT（若存在则同步附带，可在剪映中导入调色）
- README_剪映导入指南.txt
- legacy_v5_json/       : 留存旧版 5.9 协议精简 JSON 骨架（仅作归档，勿直接导入剪映 6.0+）
"""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import hashlib
import platform
import shutil
import subprocess
import time
import uuid
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.dom import minidom

from PIL import Image

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    ffprobe_media,
    find_bin,
    find_commercial_font,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)
from render_mp4 import compute_subtitle_place_xy, render_packaging_png, render_subtitle_png


def sec_to_us(sec: float) -> int:
    return int(round(sec * 1_000_000))


def _frames_to_rational(frames: int, fps: int) -> str:
    """将整型帧数转为 FCPXML 标准有理数时间（严格帧对齐，0 帧返回 '0s'）。"""
    if frames <= 0:
        return "0s"
    return f"{frames * 100}/{fps * 100}s"


def _measure_wav_frames(path_str: Optional[str], fps: int) -> Tuple[int, float]:
    """实测 WAV 真实采样时长与对应整数帧数。"""
    if not path_str:
        return 0, 0.0
    p = Path(path_str)
    if p.exists() and p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as wf:
                dur = wf.getnframes() / float(wf.getframerate())
                return max(1, int(dur * fps)), dur
        except Exception:
            pass
    return 0, 0.0


def _format_srt_ts(sec: float) -> str:
    ms_total = max(0, int(round(sec * 1000.0)))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt_content(subs: Dict[str, Any]) -> str:
    lines: List[str] = []
    entries = sorted(subs.get("entries", []), key=lambda x: float(x.get("start_sec", 0.0)))
    prev_end = 0.0
    for idx, entry in enumerate(entries, start=1):
        txt = str(entry.get("text", "")).strip()
        if not txt:
            continue
        st = max(prev_end, float(entry.get("start_sec", 0.0)))
        en = max(st + 0.05, float(entry.get("end_sec", st + 0.5)))
        prev_end = en
        lines.extend([
            str(idx),
            f"{_format_srt_ts(st)} --> {_format_srt_ts(en)}",
            txt,
            "",
        ])
    return "\n".join(lines).strip() + "\n"


def _detect_alpha_codec(ffmpeg_bin: str) -> str:
    """选择当前平台最优透明通道编码器（macOS 优先硬件 HEVC+Alpha）。"""
    try:
        res = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        encoders = res.stdout or ""
    except Exception:
        encoders = ""
    if platform.system() == "Darwin" and "hevc_videotoolbox" in encoders:
        return "hevc_videotoolbox"
    if "prores_ks" in encoders:
        return "prores_ks"
    return "qtrle"


def _build_alpha_encode_cmd(
    ffmpeg_bin: str,
    codec: str,
    full_png: Path,
    out_mov: Path,
    frames: int,
    fps: int,
) -> List[str]:
    base = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", str(fps), "-i", str(full_png),
        "-frames:v", str(frames), "-r", str(fps),
    ]
    if codec == "hevc_videotoolbox":
        return base + [
            "-c:v", "hevc_videotoolbox",
            "-alpha_quality", "1.0",
            "-b:v", "1200k",
            "-g", "300",
            "-vtag", "hvc1",
            "-pix_fmt", "bgra",
            str(out_mov),
        ]
    if codec == "prores_ks":
        return base + [
            "-c:v", "prores_ks",
            "-profile:v", "4444",
            "-pix_fmt", "yuva444p10le",
            str(out_mov),
        ]
    return base + [
        "-c:v", "qtrle",
        "-pix_fmt", "argb",
        str(out_mov),
    ]


def _encode_single_alpha_mov(task: Dict[str, Any]) -> None:
    """将单张 1080x1920 透明全画幅 PNG 编码为精确帧数的 Alpha 通道 MOV（失败自动回退 qtrle）。"""
    ffmpeg_bin: str = task["ffmpeg"]
    codec: str = task["codec"]
    full_png: Path = task["full_png"]
    out_mov: Path = task["mov_path"]
    frames: int = task["dur_frames"]
    fps: int = task["fps"]

    out_mov.parent.mkdir(parents=True, exist_ok=True)
    tmp_mov = out_mov.with_suffix(".tmp.mov")
    cmd = _build_alpha_encode_cmd(ffmpeg_bin, codec, full_png, tmp_mov, frames, fps)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 and codec != "qtrle":
        # 若宿主机硬件 VideoToolbox 受限，自动回退跨平台 QTRLE Alpha
        fallback_cmd = _build_alpha_encode_cmd(ffmpeg_bin, "qtrle", full_png, tmp_mov, frames, fps)
        res = subprocess.run(fallback_cmd, capture_output=True, text=True)
    if res.returncode != 0 or not tmp_mov.is_file() or tmp_mov.stat().st_size == 0:
        if tmp_mov.exists():
            tmp_mov.unlink()
        raise RuntimeError(f"透明视频轨编码失败 ({out_mov.name}): {res.stderr.strip()}")
    tmp_mov.replace(out_mov)


def _compose_full_canvas_png(src_png: Path, out_full_png: Path, px: int, py: int) -> str:
    """将任意尺寸局部 RGBA PNG 贴合至 1080x1920 透明全画幅画布并返回内容签名用哈希。"""
    with Image.open(src_png).convert("RGBA") as src_im:
        if src_im.size == (CANVAS_WIDTH, CANVAS_HEIGHT) and px == 0 and py == 0:
            src_im.save(out_full_png)
        else:
            canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            canvas.paste(src_im, (px, py), src_im)
            canvas.save(out_full_png)
    return hashlib.sha256(out_full_png.read_bytes()).hexdigest()[:16]


def _resolve_packaging_source(
    elem: Dict[str, Any],
    eid: str,
    render_final: Path,
    stage_assets: Path,
    font_path: Optional[str],
    cur_style_id: str,
) -> Optional[Path]:
    candidates = [
        render_final / f"{eid}.png",
        Path(str(elem.get("custom_png", ""))) if elem.get("custom_png") else Path("__none__"),
        stage_assets / f"{eid}.png",
    ]
    src_png = next((c for c in candidates if c.is_file()), None)
    if src_png is not None:
        return src_png
    generated = render_final / f"{eid}.png"
    render_packaging_png(elem, font_path, generated, default_style_id=cur_style_id)
    return generated if generated.is_file() else None


def _export_alpha_layer_tracks(
    pipeline_dir: Path,
    overlays_dir: Path,
    alpha_dir: Path,
    fps: int,
    total_frames: int,
) -> Tuple[List[Dict[str, Any]], List[Path]]:
    """
    渲染并导出全套风格谱上层透明视频轨（画框/阶段卡/花字/双色字幕）：
    - overlays/     : 1080x1920 透明全画幅 PNG
    - alpha_layers/ : 30fps 精确帧数透明通道 MOV（带哈希缓存避免重复转码）
    """
    ffmpeg_bin = find_bin("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("需要 ffmpeg 才能导出带透明通道的剪映风格包装与字幕视频轨。")
    codec = _detect_alpha_codec(ffmpeg_bin)

    pkg_path = pipeline_dir / "06_packaging.json"
    subs_path = pipeline_dir / "05_subtitles.json"
    pkg = read_json(pkg_path) if pkg_path.exists() else {}
    subs = read_json(subs_path) if subs_path.exists() else {}

    render_final = pipeline_dir / "_render_final"
    stage_assets = pipeline_dir / "_stage_assets"
    font_path = subs.get("font_path") or find_commercial_font()
    tpl = subs.get("template_config", {})
    cur_style_id = str(subs.get("template_id") or pkg.get("style_id") or "style_14")

    overlays_dir.mkdir(parents=True, exist_ok=True)
    alpha_dir.mkdir(parents=True, exist_ok=True)

    raw_items: List[Dict[str, Any]] = []
    seq_no = 0

    def _add_packaging_list(elems: List[Dict[str, Any]], default_group: str) -> None:
        nonlocal seq_no
        for elem in elems:
            seq_no += 1
            eid = str(elem.get("id", f"elem_{seq_no:03d}"))
            src_png = _resolve_packaging_source(elem, eid, render_final, stage_assets, font_path, cur_style_id)
            if not src_png:
                continue
            bbox = elem.get("bbox", {})
            with Image.open(src_png) as im_probe:
                w, h = im_probe.size
            if w == CANVAS_WIDTH and h == CANVAS_HEIGHT:
                px, py = 0, 0
            else:
                px = int(bbox.get("x", max(0, (CANVAS_WIDTH - w) // 2)))
                py = int(bbox.get("y", max(0, (CANVAS_HEIGHT - h) // 2)))

            is_win = ("_win_" in eid) or (int(bbox.get("w", 0)) == CANVAS_WIDTH and int(bbox.get("h", 0)) == CANVAS_HEIGHT)
            group = "stage_win" if (default_group == "stage" and is_win) else ("stage_card" if default_group == "stage" else default_group)
            sf = max(0, min(total_frames - 1, int(elem.get("start_frame", 0))))
            ef = min(total_frames, max(sf + 1, int(elem.get("end_frame", sf + fps))))
            raw_hint = str(elem.get("text_or_asset") or "").strip()
            txt_hint = eid if (not raw_hint or "/" in raw_hint or "\\" in raw_hint or raw_hint.endswith(".png")) else raw_hint
            prefix = "画框" if group == "stage_win" else ("阶段卡" if group == "stage_card" else "花字")
            raw_items.append({
                "seq": seq_no,
                "group": group,
                "id": eid,
                "label": f"[{prefix}] {txt_hint[:14]}",
                "src_png": src_png,
                "px": px,
                "py": py,
                "start_frame": sf,
                "end_frame": ef,
                "dur_frames": ef - sf,
            })

    _add_packaging_list(list(pkg.get("stage_overlays", [])), "stage")
    _add_packaging_list(list(pkg.get("visual_elements", [])), "flower_text")

    for s_idx, sub in enumerate(subs.get("entries", []), start=1):
        seq_no += 1
        sub_id = str(sub.get("id", f"sub_{s_idx:03d}"))
        src_png = render_final / f"{sub_id}.png"
        if not src_png.is_file():
            px, py = render_subtitle_png(sub, tpl, font_path, src_png)
        else:
            with Image.open(src_png) as im_probe:
                w, h = im_probe.size
            px, py = (0, 0) if (w == CANVAS_WIDTH and h == CANVAS_HEIGHT) else compute_subtitle_place_xy(w, h, tpl)
        sf = max(0, min(total_frames - 1, int(round(float(sub.get("start_sec", 0.0)) * fps))))
        ef = min(total_frames, max(sf + 1, int(round(float(sub.get("end_sec", 0.0)) * fps))))
        raw_items.append({
            "seq": seq_no,
            "group": "subtitle",
            "id": sub_id,
            "label": f"[字幕] {str(sub.get('text', sub_id)).strip()[:16]}",
            "src_png": src_png,
            "px": px,
            "py": py,
            "start_frame": sf,
            "end_frame": ef,
            "dur_frames": ef - sf,
        })

    cache_path = alpha_dir / ".alpha_cache.json"
    old_cache: Dict[str, str] = read_json(cache_path) if cache_path.exists() else {}
    new_cache: Dict[str, str] = {}
    keep_png_names: set[str] = set()
    keep_mov_names: set[str] = set()
    exported_pngs: List[Path] = []
    encode_tasks: List[Dict[str, Any]] = []

    for item in raw_items:
        stem = f"{item['seq']:03d}_{item['start_frame'] / fps:06.2f}s-{item['end_frame'] / fps:06.2f}s_{item['id']}"
        full_png = overlays_dir / f"{stem}.png"
        mov_path = alpha_dir / f"{stem}.mov"
        png_hash = _compose_full_canvas_png(item["src_png"], full_png, item["px"], item["py"])
        sig = f"{png_hash}:{item['dur_frames']}:{fps}:{codec}"

        item["full_png"] = full_png
        item["mov_path"] = mov_path
        keep_png_names.add(full_png.name)
        keep_mov_names.add(mov_path.name)
        exported_pngs.append(full_png)
        new_cache[mov_path.name] = sig

        cached_ok = (
            old_cache.get(mov_path.name) == sig
            and mov_path.is_file()
            and mov_path.stat().st_size > 1024
        )
        if not cached_ok:
            encode_tasks.append({
                "ffmpeg": ffmpeg_bin,
                "codec": codec,
                "full_png": full_png,
                "mov_path": mov_path,
                "dur_frames": item["dur_frames"],
                "fps": fps,
            })

    # 清理失效残留文件，确保目录始终整洁
    for old_png in overlays_dir.glob("*.png"):
        if old_png.name not in keep_png_names:
            old_png.unlink()
    for old_mov in alpha_dir.glob("*.mov"):
        if old_mov.name not in keep_mov_names:
            old_mov.unlink()

    if encode_tasks:
        workers = min(4, len(encode_tasks))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_encode_single_alpha_mov, t) for t in encode_tasks]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

    write_json(cache_path, new_cache)
    return raw_items, exported_pngs


def _assign_non_overlapping_lanes(layers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    按 Painter 叠层顺序分组并执行贪心无重叠区间排轨 (lane >= 1)：
    stage_win (画框) < stage_card (卡片/进度条) < flower_text (花字) < subtitle (双色字幕，最顶层)
    同轨严格满足前段 end_frame <= 后段 start_frame，撞帧自动升至相邻上轨。
    """
    lane_base = 1
    scheduled: List[Dict[str, Any]] = []
    for group in ("stage_win", "stage_card", "flower_text", "subtitle"):
        grp_items = sorted(
            (it for it in layers if it["group"] == group),
            key=lambda x: (x["start_frame"], x["end_frame"], x["seq"]),
        )
        if not grp_items:
            continue
        lane_ends: List[int] = []
        for it in grp_items:
            sf, ef = it["start_frame"], it["end_frame"]
            placed_sub = None
            for idx, end_f in enumerate(lane_ends):
                if end_f <= sf:
                    placed_sub = idx
                    lane_ends[idx] = ef
                    break
            if placed_sub is None:
                placed_sub = len(lane_ends)
                lane_ends.append(ef)
            placed = dict(it)
            placed["lane"] = lane_base + placed_sub
            scheduled.append(placed)
        lane_base += len(lane_ends)
    return scheduled


def build_fcpxml_tree(
    pipeline_dir: Path,
    project_name: str,
    overlay_layers: List[Dict[str, Any]],
) -> str:
    """
    构建严格符合 Apple FCPXML 1.10 DTD 标准的剪映可导入多轨工程：
    - Primary Storyline (<spine>): 全段精剪/调色分镜 (<asset-clip>)
    - Connected Upper Video Lanes (lane 1..N): 风格谱透明画框/卡片/花字 & 顶层双色字幕 (.mov)
    - Connected Audio Lanes (-1 / -2 / -3): 独立纯净人声、风格 BGM、精准 SFX 音轨
    """
    edl = read_json(pipeline_dir / "04_edl.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    mix_metrics = read_json(pipeline_dir / "08_mix_metrics.json")

    fps = int(edl.get("fps", DEFAULT_FPS))
    total_frames = int(edl.get("total_frames") or round(float(edl["total_duration_sec"]) * fps))
    render_final_dir = pipeline_dir / "_render_final"

    fcpxml = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(fcpxml, "resources")

    fmt_id = "r_fmt_9x16"
    ET.SubElement(
        resources,
        "format",
        id=fmt_id,
        name="FFVideoFormatRateUndefined",
        frameDuration=_frames_to_rational(1, fps),
        width=str(CANVAS_WIDTH),
        height=str(CANVAS_HEIGHT),
        colorSpace="1-1-1 (Rec. 709)",
    )

    asset_cache: Dict[str, Tuple[str, int, bool]] = {}
    res_counter = 1

    def ensure_video_asset(media_path: Path, need_frames: int) -> Tuple[str, int, bool]:
        nonlocal res_counter
        key = str(media_path.resolve())
        if key in asset_cache:
            return asset_cache[key]
        res_counter += 1
        rid = f"r_v_{res_counter}"
        meta = ffprobe_media(media_path)
        has_audio = any(s.get("codec_type") == "audio" for s in meta.get("streams", []))
        fmt_dur = float(meta.get("format", {}).get("duration") or 0.0)
        avail_frames = max(need_frames, int(fmt_dur * fps + 1e-4)) if fmt_dur > 0.05 else need_frames
        attrs = {
            "id": rid,
            "name": media_path.name,
            "start": "0s",
            "duration": _frames_to_rational(avail_frames, fps),
            "hasVideo": "1",
            "hasAudio": "1" if has_audio else "0",
            "format": fmt_id,
        }
        if has_audio:
            attrs.update({"audioSources": "1", "audioChannels": "2", "audioRate": "48000"})
        asset_el = ET.SubElement(resources, "asset", **attrs)
        ET.SubElement(asset_el, "media-rep", kind="original-media", src=media_path.resolve().as_uri())
        asset_cache[key] = (rid, avail_frames, has_audio)
        return asset_cache[key]

    def register_audio_asset(wav_path: Optional[str], label: str) -> Optional[Tuple[str, int]]:
        nonlocal res_counter
        if not wav_path:
            return None
        p = Path(wav_path)
        if not p.is_file():
            return None
        n_frames, _ = _measure_wav_frames(str(p), fps)
        if n_frames <= 0:
            return None
        res_counter += 1
        rid = f"r_a_{res_counter}"
        asset_el = ET.SubElement(
            resources,
            "asset",
            id=rid,
            name=label,
            start="0s",
            duration=_frames_to_rational(n_frames, fps),
            hasVideo="0",
            hasAudio="1",
            audioSources="1",
            audioChannels="2",
            audioRate="48000",
        )
        ET.SubElement(asset_el, "media-rep", kind="original-media", src=p.resolve().as_uri())
        return rid, n_frames

    voice_path = next(
        (cand for cand in [
            str(pipeline_dir / "08_voice_denoised.wav"),
            str(pipeline_dir / "03_locked_voice.wav"),
            mix_metrics.get("master_wav"),
        ] if cand and Path(cand).is_file()),
        None,
    )
    voice_res = register_audio_asset(voice_path, "01_纯净人声干音")
    bgm_res = register_audio_asset(audio_events.get("locked_bgm_wav"), "02_背景配乐BGM")
    sfx_res = register_audio_asset(str(pipeline_dir / "06_sfx_track.wav"), "03_切镜音效SFX")

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name=project_name)
    project = ET.SubElement(event, "project", name=project_name)
    sequence = ET.SubElement(
        project,
        "sequence",
        format=fmt_id,
        duration=_frames_to_rational(total_frames, fps),
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")

    clips = edl.get("clips", [])
    accum_frames = 0
    # 记录 (in_f, out_f, src_start_f, clip_el)，确保每个 Connected Clip 锚定在覆盖其起点的主轨片段内
    spine_spans: List[Tuple[int, int, int, ET.Element]] = []
    spine_starts: List[int] = []

    for idx, clip in enumerate(clips):
        seg_file = render_final_dir / f"seg_{idx+1:03d}.mp4"
        use_baked_seg = seg_file.is_file() and seg_file.stat().st_size > 1024
        if idx == len(clips) - 1:
            clip_frames = max(1, total_frames - accum_frames)
        else:
            clip_frames = max(1, int(clip["timeline_out_frame"]) - int(clip["timeline_in_frame"]))
        offset_frames = accum_frames
        accum_frames += clip_frames

        if use_baked_seg:
            media_p = seg_file
            src_start_frames = 0
            rid, avail_frames, has_audio = ensure_video_asset(media_p, clip_frames)
            dur_frames = min(clip_frames, avail_frames)
        else:
            media_p = Path(clip["media_path"])
            src_start_frames = max(0, int(round(float(clip.get("source_in_sec", 0.0)) * fps)))
            rid, avail_frames, has_audio = ensure_video_asset(media_p, src_start_frames + clip_frames)
            dur_frames = min(clip_frames, max(1, avail_frames - src_start_frames))

        clip_name = f"{idx+1:02d}_{clip.get('track_role', 'clip')}_{str(clip.get('sentence_text', ''))[:10]}"
        clip_el = ET.SubElement(
            spine,
            "asset-clip",
            name=clip_name,
            ref=rid,
            offset=_frames_to_rational(offset_frames, fps),
            start=_frames_to_rational(src_start_frames, fps),
            duration=_frames_to_rational(dur_frames, fps),
            format=fmt_id,
            tcFormat="NDF",
        )
        if has_audio:
            ET.SubElement(clip_el, "adjust-volume", amount="-infinity")
        spine_spans.append((offset_frames, offset_frames + dur_frames, src_start_frames, clip_el))
        spine_starts.append(offset_frames)

    def _find_anchor(sf: int) -> Tuple[ET.Element, int]:
        pos = max(0, bisect.bisect_right(spine_starts, sf) - 1)
        in_f, _, src_start_f, el = spine_spans[pos]
        return el, src_start_f + max(0, sf - in_f)

    if spine_spans:
        # 1) 挂载三条全片连续独立 WAV 音轨 (lane -1 / -2 / -3)
        first_el = spine_spans[0][3]
        first_anchor = _frames_to_rational(spine_spans[0][2], fps)
        audio_lanes = [
            (voice_res, -1, "dialogue", "0dB", "纯净人声干音"),
            (bgm_res, -2, "music", "-14dB", "专属BGM配乐"),
            (sfx_res, -3, "effects", "0dB", "切镜包装音效"),
        ]
        for res_info, lane_idx, role_name, vol_db, lane_title in audio_lanes:
            if not res_info:
                continue
            rid, a_frames = res_info
            use_frames = min(total_frames, a_frames)
            aud_el = ET.SubElement(
                first_el,
                "asset-clip",
                name=lane_title,
                ref=rid,
                lane=str(lane_idx),
                offset=first_anchor,
                start="0s",
                duration=_frames_to_rational(use_frames, fps),
                role=role_name,
            )
            if vol_db != "0dB":
                ET.SubElement(aud_el, "adjust-volume", amount=vol_db)

        # 2) 挂载全套风格谱透明包装轨 & 最顶层双色安全区字幕轨 (lane 1..N)
        for layer in _assign_non_overlapping_lanes(overlay_layers):
            mov_p: Path = layer["mov_path"]
            rid, avail_frames, _ = ensure_video_asset(mov_p, layer["dur_frames"])
            dur_frames = min(layer["dur_frames"], avail_frames)
            parent_el, anchor_off = _find_anchor(layer["start_frame"])
            ET.SubElement(
                parent_el,
                "asset-clip",
                name=layer["label"],
                ref=rid,
                lane=str(layer["lane"]),
                offset=_frames_to_rational(anchor_off, fps),
                start="0s",
                duration=_frames_to_rational(dur_frames, fps),
                format=fmt_id,
                tcFormat="NDF",
            )

    rough = ET.tostring(fcpxml, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    header = '<?xml version="1.0" encoding="utf-8"?>\n'
    body = pretty.split("?>", 1)[1].lstrip("\n") if pretty.startswith("<?xml") else pretty
    return header + "<!DOCTYPE fcpxml>\n" + body


def build_jianying_draft_payload(pipeline_dir: Path) -> Dict[str, Any]:
    """构建向后兼容的旧版 5.9 精简 JSON 归档骨架。"""
    edl = read_json(pipeline_dir / "04_edl.json")
    subs = read_json(pipeline_dir / "05_subtitles.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    mix_metrics = read_json(pipeline_dir / "08_mix_metrics.json")

    fps = float(edl.get("fps", DEFAULT_FPS))
    total_us = sec_to_us(float(edl["total_duration_sec"]))
    draft_id = str(uuid.uuid4()).upper()
    voice_wav = str(pipeline_dir / "08_voice_denoised.wav") if (pipeline_dir / "08_voice_denoised.wav").exists() else mix_metrics["master_wav"]

    draft_content = {
        "id": draft_id,
        "version": 360000,
        "fps": fps,
        "duration": total_us,
        "canvas_config": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT, "ratio": "9:16"},
        "clips_count": len(edl.get("clips", [])),
        "subtitles_count": len(subs.get("entries", [])),
        "audio_stems": {
            "voice": voice_wav,
            "bgm": audio_events.get("locked_bgm_wav"),
            "sfx": str(pipeline_dir / "06_sfx_track.wav"),
        },
    }
    now_us = int(time.time() * 1_000_000)
    draft_meta = {
        "draft_id": draft_id,
        "draft_name": f"short_video_studio_{pipeline_dir.parent.name}",
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": total_us,
    }
    return {"draft_content": draft_content, "draft_meta_info": draft_meta}


def export_jianying_draft(pipeline_dir: Path, simulate_failure: bool = False) -> Dict[str, Any]:
    draft_dir = pipeline_dir / "jianying_draft"
    proj_slug = pipeline_dir.parent.name if pipeline_dir.name == "pipeline" else pipeline_dir.name
    safe_name = f"short_video_studio_{proj_slug}"
    try:
        if simulate_failure:
            raise RuntimeError("模拟剪映工程导出版本不匹配或目标目录不可写。")

        draft_dir.mkdir(parents=True, exist_ok=True)
        legacy_dir = draft_dir / "legacy_v5_json"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        for old_f in ("draft_content.json", "draft_meta_info.json"):
            old_p = draft_dir / old_f
            if old_p.exists():
                old_p.unlink()

        edl = read_json(pipeline_dir / "04_edl.json")
        subs = read_json(pipeline_dir / "05_subtitles.json")
        color_p = pipeline_dir / "07_color_plan.json"
        color_plan = read_json(color_p) if color_p.exists() else {}
        fps = int(edl.get("fps", DEFAULT_FPS))
        total_frames = int(edl.get("total_frames") or round(float(edl["total_duration_sec"]) * fps))

        # 1. 先渲染并编码全部风格谱透明视频层（画框/阶段卡/花字 + 双色精调字幕）
        overlay_layers, overlays_exported = _export_alpha_layer_tracks(
            pipeline_dir,
            draft_dir / "overlays",
            draft_dir / "alpha_layers",
            fps,
            total_frames,
        )

        # 2. 生成全轨道 FCPXML 1.10 工程文件（主分镜 + 风格包装透明轨 + 双色字幕透明轨 + 三轨音频）
        fcpxml_text = build_fcpxml_tree(pipeline_dir, safe_name, overlay_layers)
        fcpxml_path = draft_dir / f"{safe_name}.fcpxml"
        fcpxml_path.write_text(fcpxml_text, encoding="utf-8")

        # 3. 同步附带 SRT/ASS 字幕备选与 3D LUT
        srt_path = draft_dir / f"{safe_name}.srt"
        srt_path.write_text(_build_srt_content(subs), encoding="utf-8")
        ass_src = pipeline_dir / "05_subtitles.ass"
        if ass_src.is_file():
            shutil.copy2(ass_src, draft_dir / f"{safe_name}.ass")

        lut_exported: Optional[Path] = None
        if color_plan.get("lut_path") and Path(color_plan["lut_path"]).is_file():
            lut_src = Path(color_plan["lut_path"])
            lut_exported = draft_dir / lut_src.name
            shutil.copy2(lut_src, lut_exported)

        n_stage = sum(1 for x in overlay_layers if x["group"] in ("stage_win", "stage_card"))
        n_flower = sum(1 for x in overlay_layers if x["group"] == "flower_text")
        n_sub = sum(1 for x in overlay_layers if x["group"] == "subtitle")

        readme = "\n".join([
            f"【{safe_name} 剪映专业版（含 11.7.0+）全风格完整工程导入指南】",
            "",
            "本工程包已升级为「多轨透明视频层 + 三轨独立音频」一键导入结构：",
            "导入一次 FCPXML 即可直接还原风格谱的画框、卡片、花字与双色安全区字幕，无需逐张手拖 PNG！",
            "",
            "第 1 步（一键导入完整多轨时间线）：",
            "  1) 打开剪映专业版 -> 右上角齿轮「设置」->「全局设置」-> 确认开启「导入工程」。",
            f"  2) 在首页「本地草稿」右上角点击「...」->「导入工程」，选中本目录下的：{fcpxml_path.name}",
            "  导入后您将直接获得以下可自由编辑轨道：",
            f"    - 主视频轨 V1：全部 {len(edl.get('clips', []))} 段调色精剪分镜",
            f"    - 风格包装层（透明轨道）：{n_stage} 个画框/阶段卡片/进度条 + {n_flower} 个语义花字胶囊（可单块移动/隐藏/删除）",
            f"    - 双色精调字幕轨（最顶层）：{n_sub} 句风格谱原版双色高亮字幕（严格对齐安全区与商业字体）",
            "    - 三轨独立音频：纯净人声干音 + 专属 BGM + 切镜音效 SFX（可任意单独静音/裁剪/调音量）",
            "",
            "第 2 步（仅需手动改某个错别字时使用）：",
            "  双色字幕轨道默认已呈现与成片完全一致的精美排版样式；如仅需修改某一句文案内容，",
            f"  可在时间线最顶层删掉对应句子的字幕块，并拖入本目录附带的 {srt_path.name}（或 {safe_name}.ass）做文字替换。",
            "",
            f"第 3 步（可选 3D LUT 调节）：",
            f"  - overlays/ 与 alpha_layers/ 已保留全画幅透明素材备份；",
            f"  - 如需调整成片滤镜风格，可在「调节 -> 滤镜 -> 导入 LUT」加载 {lut_exported.name if lut_exported else '对应 .cube 文件'}。",
            "",
        ])
        (draft_dir / "README_剪映导入指南.txt").write_text(readme, encoding="utf-8")

        payload = build_jianying_draft_payload(pipeline_dir)
        write_json(legacy_dir / "draft_summary.json", payload["draft_content"])
        write_json(legacy_dir / "draft_meta_info.json", payload["draft_meta_info"])

        status = {
            "status": "success",
            "degraded_to_mp4_only": False,
            "export_format": "fcpxml_1.10+alpha_mov+srt",
            "draft_dir": str(draft_dir.resolve()),
            "fcpxml_path": str(fcpxml_path.resolve()),
            "srt_path": str(srt_path.resolve()),
            "alpha_layers_count": len(overlay_layers),
            "stage_overlays_count": n_stage,
            "flower_texts_count": n_flower,
            "subtitles_rendered_count": n_sub,
            "overlays_count": len(overlays_exported),
            "message": (
                f"剪映全风格可编辑工程导出成功 ({fcpxml_path.name}，"
                f"含 {n_stage} 张阶段卡/画框 + {n_flower} 个花字 + {n_sub} 条双色透明字幕轨)。"
            ),
        }
        write_json(pipeline_dir / "jianying_export_status.json", status)
        print(f"[jianying] 全风格可编辑工程包 (FCPXML + Alpha MOV) 导出成功 -> {draft_dir}")
        return status
    except Exception as exc:
        status = {
            "status": "degraded_to_mp4_only",
            "degraded_to_mp4_only": True,
            "draft_dir": None,
            "error": str(exc),
            "message": f"提示：剪映工程导出失败（{exc}），已自动降级为仅交付本地渲染 MP4，成片质量不受影响。",
        }
        write_json(pipeline_dir / "jianying_export_status.json", status)
        print(f"[jianying][降级提示] {status['message']}")
        return status


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="出片2: 剪映原生全风格可编辑工程包 (FCPXML + Alpha MOV + SRT) 独立导出")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--simulate-failure", action="store_true", help="模拟导出失败验证降级逻辑")
    args = parser.parse_args()
    export_jianying_draft(Path(args.pipeline_dir), simulate_failure=args.simulate_failure)


if __name__ == "__main__":
    main()
