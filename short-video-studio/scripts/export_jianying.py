#!/usr/bin/env python3
"""
[出片 2：剪映专业版草稿独立导出封装]
将流水线中间产物 (04_edl.json, 05_subtitles.json, 08_mixed_master.wav) 独立封装导出为剪映草稿：
- jianying_draft/draft_content.json
- jianying_draft/draft_meta_info.json
容错降级原则（BRIEF.md 硬性要求）：
草稿导出独立封装；如遇版本兼容异常或写入失效（或测试 --simulate-failure），
立即捕获并降级为「只出 MP4」，明确输出状态报告，绝不阻断整体出片流程。
"""

from __future__ import annotations

import argparse
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Dict, List

from common import CANVAS_HEIGHT, CANVAS_WIDTH, maybe_reexec_in_venv, read_json, write_json


def sec_to_us(sec: float) -> int:
    return int(round(sec * 1_000_000))


def _measure_wav_us(path_str: str, fallback_us: int = 500_000) -> int:
    p = Path(path_str)
    if p.exists() and p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as wf:
                return max(50_000, int(round(wf.getnframes() / float(wf.getframerate()) * 1_000_000)))
        except Exception:
            pass
    return fallback_us


def _bbox_to_jy_transform(bbox: Dict[str, Any], canvas_w: int = CANVAS_WIDTH, canvas_h: int = CANVAS_HEIGHT) -> Dict[str, float]:
    bx = float(bbox.get("x", 88))
    by = float(bbox.get("y", 1312))
    bw = float(bbox.get("w", 400))
    bh = float(bbox.get("h", 84))
    cx = (bx + bw / 2.0 - canvas_w / 2.0) / (canvas_w / 2.0)
    cy = (canvas_h / 2.0 - (by + bh / 2.0)) / (canvas_h / 2.0)
    return {"x": round(cx, 4), "y": round(cy, 4)}


def build_jianying_draft_payload(pipeline_dir: Path) -> Dict[str, Any]:
    edl = read_json(pipeline_dir / "04_edl.json")
    subs = read_json(pipeline_dir / "05_subtitles.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    mix_metrics = read_json(pipeline_dir / "08_mix_metrics.json")
    pkg_path = pipeline_dir / "06_packaging.json"
    pkg = read_json(pkg_path) if pkg_path.exists() else {}
    color_path = pipeline_dir / "07_color_plan.json"
    color_plan = read_json(color_path) if color_path.exists() else {}

    fps = float(edl.get("fps", 30))
    total_us = sec_to_us(float(edl["total_duration_sec"]))
    draft_id = str(uuid.uuid4()).upper()

    shared_speed_id = str(uuid.uuid4()).upper()
    shared_canvas_id = str(uuid.uuid4()).upper()
    shared_scm_id = str(uuid.uuid4()).upper()
    materials_speeds = [{"id": shared_speed_id, "mode": 0, "speed": 1.0, "type": "speed"}]
    materials_canvases = [{"id": shared_canvas_id, "type": "canvas_color", "color": "", "album_image": ""}]
    materials_scm = [{"id": shared_scm_id, "type": "none", "audio_channel_mapping": 0, "is_config_open": False}]

    materials_videos: List[Dict[str, Any]] = []
    video_segments: List[Dict[str, Any]] = []
    for clip in edl["clips"]:
        mat_id = str(uuid.uuid4()).upper()
        src_dur_us = sec_to_us(max(0.1, float(clip["source_out_sec"]) - float(clip["source_in_sec"])))
        tgt_dur_us = sec_to_us(float(clip["duration_sec"]))
        materials_videos.append({
            "id": mat_id,
            "path": clip["media_path"],
            "material_name": Path(clip["media_path"]).name,
            "type": "video",
            "width": CANVAS_WIDTH,
            "height": CANVAS_HEIGHT,
            "duration": src_dur_us,
        })
        video_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": mat_id,
            "extra_material_refs": [shared_speed_id, shared_canvas_id, shared_scm_id],
            "source_timerange": {"start": sec_to_us(float(clip["source_in_sec"])), "duration": src_dur_us},
            "target_timerange": {"start": sec_to_us(float(clip["timeline_in_sec"])), "duration": tgt_dur_us},
            "render_index": 0,
            "volume": 0.0,
        })

    # 锁定音轨与混音母带 + SFX 独立音效轨（按实测 WAV 时长写入，严防超出 source_timerange）
    voice_mat_id = str(uuid.uuid4()).upper()
    bgm_mat_id = str(uuid.uuid4()).upper()
    materials_audios: List[Dict[str, Any]] = [
        {"id": voice_mat_id, "path": mix_metrics["master_wav"], "name": "08_mixed_master.wav", "type": "extract_music", "duration": total_us},
        {"id": bgm_mat_id, "path": audio_events["locked_bgm_wav"], "name": "03_locked_bgm.wav", "type": "music", "duration": total_us},
    ]
    audio_segments = [
        {
            "id": str(uuid.uuid4()).upper(),
            "material_id": voice_mat_id,
            "extra_material_refs": [shared_speed_id, shared_scm_id],
            "source_timerange": {"start": 0, "duration": total_us},
            "target_timerange": {"start": 0, "duration": total_us},
            "render_index": 0,
            "volume": 1.0,
        }
    ]
    sfx_segments: List[Dict[str, Any]] = []
    sfx_dur_cache: Dict[str, int] = {}
    for cue in pkg.get("sfx_cues", []):
        sfx_id = str(uuid.uuid4()).upper()
        fpath = cue.get("file", "")
        if fpath not in sfx_dur_cache:
            sfx_dur_cache[fpath] = _measure_wav_us(fpath)
        cue_dur_us = sfx_dur_cache[fpath]
        gain_db = float(cue.get("gain_db", -14.0))
        lin_vol = round(min(1.0, max(0.05, 10.0 ** (gain_db / 20.0))), 3)
        materials_audios.append({
            "id": sfx_id,
            "path": fpath,
            "name": cue.get("sfx_name", cue.get("id", "sfx")),
            "type": "sound",
            "duration": cue_dur_us,
        })
        sfx_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": sfx_id,
            "extra_material_refs": [shared_speed_id, shared_scm_id],
            "source_timerange": {"start": 0, "duration": cue_dur_us},
            "target_timerange": {"start": sec_to_us(float(cue.get("time_sec", 0.0))), "duration": cue_dur_us},
            "render_index": 0,
            "volume": lin_vol,
        })

    # LUT 调色滤镜材质与特效轨 (读取 07_color_plan.json)
    materials_effects: List[Dict[str, Any]] = []
    effect_segments: List[Dict[str, Any]] = []
    if color_plan.get("lut_path") or color_plan.get("lut_name"):
        lut_mat_id = str(uuid.uuid4()).upper()
        materials_effects.append({
            "id": lut_mat_id,
            "type": "filter",
            "name": str(color_plan.get("lut_name", "custom_lut")),
            "path": str(color_plan.get("lut_path", "")),
            "value": 0.85,
            "clip_grades_count": len(color_plan.get("clip_plans", [])),
        })
        effect_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": lut_mat_id,
            "target_timerange": {"start": 0, "duration": total_us},
            "render_index": 10000,
        })

    # 主字幕轨 + 独立花字胶囊轨 (精确换算剪映 [-1, 1] 归一化坐标与分层 render_index)
    tcfg = subs.get("template_config", {})
    materials_texts: List[Dict[str, Any]] = []
    text_segments: List[Dict[str, Any]] = []
    for sub in subs.get("entries", []):
        tid = str(uuid.uuid4()).upper()
        dur_us = max(100_000, sec_to_us(float(sub["end_sec"]) - float(sub["start_sec"])))
        tf = _bbox_to_jy_transform(sub.get("bbox", {"x": 140, "y": 1312, "w": 680, "h": 96}))
        materials_texts.append({
            "id": tid,
            "type": "subtitle",
            "content": sub["text"],
            "font_path": subs.get("font_path") or "",
            "font_size": tcfg.get("font_size", 54),
            "text_color": tcfg.get("primary_color", "#FFFFFF"),
            "keyword_color": tcfg.get("keyword_color", "#FFD54F"),
        })
        text_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": tid,
            "target_timerange": {"start": sec_to_us(float(sub["start_sec"])), "duration": dur_us},
            "clip": {"transform": tf, "scale": {"x": 1.0, "y": 1.0}},
            "render_index": 14000,
        })

    sticker_text_segments: List[Dict[str, Any]] = []
    for elem in pkg.get("visual_elements", []):
        stid = str(uuid.uuid4()).upper()
        sf = int(elem.get("start_frame", 0))
        ef = int(elem.get("end_frame", sf + int(fps)))
        start_sec = sf / fps
        dur_us = max(200_000, sec_to_us((ef - sf) / fps))
        tf = _bbox_to_jy_transform(elem.get("bbox", {"x": 88, "y": 205, "w": 420, "h": 84}))
        materials_texts.append({
            "id": stid,
            "type": "sticker_text",
            "style_id": elem.get("style_id", tcfg.get("style_id", "style_14")),
            "kind": elem.get("kind", "flower_text"),
            "content": elem.get("text_or_asset", ""),
            "sub_text": elem.get("sub_text", ""),
            "bbox": elem.get("bbox", {}),
        })
        sticker_text_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": stid,
            "target_timerange": {"start": sec_to_us(start_sec), "duration": dur_us},
            "clip": {"transform": tf, "scale": {"x": 1.0, "y": 1.0}},
            "render_index": 15000,
        })

    tracks: List[Dict[str, Any]] = [
        {"id": str(uuid.uuid4()).upper(), "type": "video", "name": "main_video", "attribute": 0, "flag": 0, "segments": video_segments},
        {"id": str(uuid.uuid4()).upper(), "type": "audio", "name": "mixed_master", "attribute": 0, "flag": 0, "segments": audio_segments},
        {"id": str(uuid.uuid4()).upper(), "type": "text", "name": "main_subtitles", "attribute": 0, "flag": 0, "segments": text_segments},
    ]
    if sfx_segments:
        tracks.append({"id": str(uuid.uuid4()).upper(), "type": "audio", "name": "sfx_audio", "attribute": 0, "flag": 0, "segments": sfx_segments})
    if effect_segments:
        tracks.append({"id": str(uuid.uuid4()).upper(), "type": "effect", "name": "color_lut_grade", "attribute": 0, "flag": 0, "segments": effect_segments})
    if sticker_text_segments:
        tracks.append({"id": str(uuid.uuid4()).upper(), "type": "text", "name": "sticker_text", "attribute": 0, "flag": 0, "segments": sticker_text_segments})

    draft_content = {
        "id": draft_id,
        "version": 360000,
        "fps": fps,
        "duration": total_us,
        "canvas_config": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT, "ratio": "9:16"},
        "materials": {
            "videos": materials_videos,
            "audios": materials_audios,
            "effects": materials_effects,
            "texts": materials_texts,
            "speeds": materials_speeds,
            "canvases": materials_canvases,
            "sound_channel_mappings": materials_scm,
        },
        "tracks": tracks,
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
    try:
        if simulate_failure:
            raise RuntimeError("模拟剪映草稿格式版本不匹配或目标目录不可写。")

        payload = build_jianying_draft_payload(pipeline_dir)
        draft_dir.mkdir(parents=True, exist_ok=True)
        write_json(draft_dir / "draft_content.json", payload["draft_content"])
        write_json(draft_dir / "draft_meta_info.json", payload["draft_meta_info"])
        status = {
            "status": "success",
            "degraded_to_mp4_only": False,
            "draft_dir": str(draft_dir.resolve()),
            "message": "剪映草稿导出成功，可直接导入剪映专业版精修。",
        }
        write_json(pipeline_dir / "jianying_export_status.json", status)
        print(f"[jianying] 剪映草稿导出成功 -> {draft_dir}")
        return status
    except Exception as exc:
        # 失效时优雅降级为只出 MP4 并明确提示用户
        status = {
            "status": "degraded_to_mp4_only",
            "degraded_to_mp4_only": True,
            "draft_dir": None,
            "error": str(exc),
            "message": f"提示：剪映草稿导出失败（{exc}），已自动降级为仅交付本地渲染 MP4，成片质量不受影响。",
        }
        write_json(pipeline_dir / "jianying_export_status.json", status)
        print(f"[jianying][降级提示] {status['message']}")
        return status


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="出片2: 剪映草稿独立导出封装（支持失效降级）")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--simulate-failure", action="store_true", help="模拟导出失败验证降级逻辑")
    args = parser.parse_args()
    export_jianying_draft(Path(args.pipeline_dir), simulate_failure=args.simulate_failure)


if __name__ == "__main__":
    main()
