#!/usr/bin/env python3
"""原生剪映草稿导出（可编辑工程路线）。

与 `export_jianying.py`（FCPXML + 烘焙 PNG）不同：这里直接生成剪映自己的
`draft_content.json`，文字是**真文字素材**（沿用源字体族名与字号/描边/底框），
打开剪映就能改字、改样式、改时长，不是烧进画面的图层。

生成结构严格照解密后的真草稿 schema（见 forensic/jianying_ground_truth_full.json，不随包分发）：

  draft_content.json   canvas_config / tracks(video,text,audio) / materials(videos,texts,speeds,audios,..)
  draft_meta_info.json 素材清单与草稿夹路径
  materials/           相对路径留存的媒体副本（##_draftpath_placeholder_## 协议）
  README_原生草稿使用说明.txt

用法:
  python3 scripts/export_jianying_native.py --pipeline-dir <项目>/pipeline [--out <目录>]
"""

from __future__ import annotations

import platform


def _jy_os_value() -> str:
    """剪映草稿里的 `os` 字段：mac 上写 mac，Windows 上写 windows。

    以前写死 "mac"——导到 Windows 版剪映里认不认没人验过，至少别把平台写错。
    """
    return "windows" if platform.system() == "Windows" else "mac"

import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ASSETS_DIR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    maybe_reexec_in_venv,
    read_json,
    resolve_audio_stems,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

LIB_PATH = ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json"
US = 1_000_000  # 剪映时间单位：微秒


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _us(sec: float) -> int:
    return int(round(float(sec) * US))


def _hex_to_rgb01(hex_str: str) -> List[float]:
    s = str(hex_str or "#FFFFFF").lstrip("#")
    if len(s) == 8:
        s = s[:6]
    if len(s) != 6:
        return [1.0, 1.0, 1.0]
    return [round(int(s[i : i + 2], 16) / 255.0, 6) for i in (0, 2, 4)]


def _safe_name(raw: str) -> str:
    keep = [c for c in str(raw) if c.isalnum() or c in "._-·（）()【】 "]
    return ("".join(keep).strip() or "draft")[:60]


def load_style_entry(style_id: str) -> Dict[str, Any]:
    if not LIB_PATH.is_file():
        return {}
    lib = read_json(LIB_PATH)
    for s in lib.get("styles") or []:
        if s.get("style_id") == style_id:
            return s
    return {}


# ---------------------------------------------------------------- 素材：文字


def build_text_material(
    text: str,
    sub: Dict[str, Any],
    *,
    start_sec: float,
    end_sec: float,
    size_units: Optional[float] = None,
    is_flower: bool = False,
    fill_hex: Optional[str] = None,
    stroke_hex: Optional[str] = None,
    stroke_units: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """返回 (text 素材, text segment)。文字是真文字，剪映里可直接编辑。"""
    mat_id, seg_id = _uid(), _uid()
    src_font = str(sub.get("source_font_family") or sub.get("font_family") or "")
    size = float(size_units if size_units is not None else sub.get("font_size_jianying_units") or 15.0)
    fill = str(fill_hex or sub.get("primary_color") or "#FFFFFF")
    kw = str(sub.get("keyword_color") or fill)
    stroke_c = str(stroke_hex or sub.get("stroke_color") or "#000000")
    stroke_w = float(stroke_units if stroke_units is not None else (sub.get("stroke_width") or 0) / 4.0)
    bg_ratio = float(sub.get("bg_ratio_measured") or 0.0)
    bg_alpha = float(sub.get("bg_box_alpha") or 0.0)
    radius = float(sub.get("bg_radius") or 0.0)

    styles = [{
        "fill": {"content": {"solid": {"color": _hex_to_rgb01(fill)}}},
        "font": {"path": "", "id": ""},
        "size": size,
        "useLetterColor": True,
        "range": [0, len(text)],
    }]
    if kw and kw.lower() != fill.lower():
        styles[0]["useLetterColor"] = False
    content = json.dumps({"text": text, "styles": styles}, ensure_ascii=False)
    need_bg = (bg_ratio > 0.01 or bg_alpha > 0.01) and not is_flower

    mat: Dict[str, Any] = {
        "add_type": 0,
        "alignment": 1,
        "background_alpha": 1.0 if need_bg else 0.0,
        "background_color": str(sub.get("bg_box_color") or "#000000")[:7] if need_bg else "",
        "background_height": 0.14 if need_bg else 0.0,
        "background_horizontal_offset": 0.0,
        "background_round_radius": round(radius * 4 / max(1.0, 1920.0), 4) if radius else 0.0,
        "background_style": 1 if need_bg else 0,
        "background_vertical_offset": 0.0,
        "background_width": 0.82 if need_bg else 0.0,
        "base_content": "",
        "beats": [],
        "bold": True,
        "border_alpha": 1.0 if stroke_w > 0 else 0.0,
        "border_color": stroke_c,
        "border_width": round(min(0.2, stroke_w / 30.0), 4) if stroke_w > 0 else 0.0,
        "caption_template_info": {"path": "", "resource_id": ""},
        "check_flag": 39,
        "combo_info": {},
        "content": content,
        "current_words": {},
        "fixed_height": -1,
        "fixed_width": -1,
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": src_font,
        "font_path": "",
        "font_resource_id": "",
        "font_size": size,
        "font_source_platform": "",
        "font_team_id": "",
        "font_title": "",
        "font_url": "",
        "fonts": [{"path": "", "id": ""}] if src_font else [],
        "force_apply_line_max_width": False,
        "global_alpha": 1.0,
        "group_id": "",
        "has_shadow": bool(sub.get("shadow")),
        "id": mat_id,
        "initial_scale": 1.0,
        "inner_padding": -1.0,
        "is_rich_text": True,
        "italic_degree": 0,
        "language": "",
        "layer_weight": 1,
        "letter_spacing": 0.0,
        "line_feed": 1,
        "line_max_width": round(min(1.0, max(0.5, float(sub.get("symmetry_max_width_px") or 640) / CANVAS_WIDTH)), 3),
        "line_spacing": 0.02,
        "lyrics_template": {"lyrics_template_id": "", "lyrics_template_name": "", "path": "", "resource_id": ""},
        "multi_language_current": "none",
        "name": "",
        "original_size": [],
        "preset_category": "",
        "preset_category_id": "",
        "preset_has_set_alignment": False,
        "preset_id": "",
        "preset_index": 0,
        "preset_name": "",
        "recognize_type": 0,
        "relevance_segment": [],
        "shadow_alpha": 0.44 if sub.get("shadow") else 0.0,
        "shadow_angle": -45,
        "shadow_color": str(sub.get("shadow_color") or "#000000")[:7],
        "shadow_distance": 8.0,
        "shadow_point": {"x": 0.0, "y": 0.0},
        "shadow_smoothing": 0.45,
        "shape_clip_x": False,
        "shape_clip_y": False,
        "style_name": "",
        "sub_type": 0,
        "text_alpha": 1.0,
        "text_color": fill,
        "text_effect_style": "",
        "text_effect_strength": 0.5,
        "text_size": size,
        "text_to_audio_ids": [],
        "typesetting": 0,
        "underline": False,
        "underline_offset": 0.22,
        "underline_width": 0.05,
        "type": "text",
        "use_effect_default_color": True,
        "words": {},
    }
    y_ratio = float(sub.get("safe_y_ratio") or 0.76)
    seg: Dict[str, Any] = {
        "caption_info": None,
        "cartoon": False,
        "clip": {
            "alpha": 1.0,
            "flip": {},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": round((y_ratio - 0.5) * 2.0, 4)},
        },
        "common_keyframes": [],
        "enable_adjust": False,
        "enable_adjust_mask": False,
        "enable_color_adjust": False,
        "enable_color_correct_adjust": False,
        "enable_color_curves": False,
        "enable_color_match_adjust": False,
        "enable_color_wheels": False,
        "enable_hsl": False,
        "enable_lut": False,
        "enable_smart_color_adjust": False,
        "extra_material_refs": [],
        "group_id": "",
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "id": seg_id,
        "intensifies_audio": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "material_id": mat_id,
        "render_index": 0,
        "render_timerange": {},
        "responsive_layout": {"enable": False, "horizontal_pos_layout": 0, "target_follow": False, "vertical_pos_layout": 0},
        "reverse": False,
        "source": "segmentsourcenormal",
        "source_timerange": {"duration": _us(end_sec - start_sec), "start": 0},
        "speed": 1.0,
        "target_timerange": {"duration": _us(end_sec - start_sec), "start": _us(start_sec)},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 0,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
        "volume": 1.0,
    }
    return mat, seg


# ---------------------------------------------------------------- 素材：视频


def build_video_material(path: Path, dur_sec: float, root_uuid: str, draft_name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    mat_id, speed_id = _uid(), _uid()
    rel = f"materials/video/{path.name}"
    mat: Dict[str, Any] = {
        "aigc_type": "none",
        "audio_fade": None,
        "beauty_face_auto_preset": {},
        "category_name": "local",
        "check_flag": 62978047,
        "crop": {
            "lower_left_x": 0.0,
            "lower_left_y": 1.0,
            "lower_right_x": 1.0,
            "lower_right_y": 1.0,
            "upper_left_x": 0.0,
            "upper_left_y": 0.0,
            "upper_right_x": 1.0,
            "upper_right_y": 0.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": _us(dur_sec),
        "height": CANVAS_HEIGHT,
        "id": mat_id,
        "local_material_id": _uid(),
        "material_id": "",
        "material_name": path.name,
        "matting": {"path": "", "strokes": []},
        "stable": {"time_range": {}},
        "media_path": "",
        "path": f"##_draftpath_placeholder_{root_uuid}_##/{rel}",
        "type": "video",
        "video_algorithm": {"path": "", "story_video_modify_video_config": {}},
        "width": CANVAS_WIDTH,
        "has_audio": True,
    }
    speed = {"curve_speed": None, "id": speed_id, "mode": 0, "speed": 1.0, "type": "speed"}
    return mat, speed


def build_video_segment(mat_id: str, speed_id: str, src_in: float, dur: float, tl_start: float) -> Dict[str, Any]:
    return {
        "cartoon": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        },
        "common_keyframes": [],
        "enable_adjust": True,
        "enable_adjust_mask": False,
        "enable_color_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_hsl": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "extra_material_refs": [speed_id],
        "group_id": "",
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "id": _uid(),
        "intensifies_audio": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "material_id": mat_id,
        "render_index": 0,
        "responsive_layout": {"enable": False, "horizontal_pos_layout": 0, "target_follow": False, "vertical_pos_layout": 0},
        "render_timerange": {},
        "reverse": False,
        "source": "segmentsourcenormal",
        "source_timerange": {"duration": _us(dur), "start": _us(src_in)},
        "speed": 1.0,
        "target_timerange": {"duration": _us(dur), "start": _us(tl_start)},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 0,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
        "volume": 1.0,
    }


def build_audio_material(path: Path, dur_sec: float, root_uuid: str, name: str, category: str) -> Dict[str, Any]:
    return {
        "app_id": 0,
        "category_id": category,
        "category_name": category,
        "check_flag": 1,
        "duration": _us(dur_sec),
        "effect_id": "",
        "id": _uid(),
        "intensifies_path": "",
        "local_material_id": _uid(),
        "music_id": _uid(),
        "name": name,
        "path": f"##_draftpath_placeholder_{root_uuid}_##/materials/audio/{path.name}",
        "request_id": "",
        "resource_id": "",
        "source_platform": 0,
        "team_id": "",
        "type": "extract_music",
    }


def build_audio_segment(mat_id: str, dur: float, tl_start: float, volume: float = 1.0) -> Dict[str, Any]:
    return {
        "clip": None,
        "common_keyframes": [],
        "extra_material_refs": [],
        "hdr_settings": None,
        "id": _uid(),
        "keyframe_refs": [],
        "last_nonzero_volume": volume,
        "material_id": mat_id,
        "render_index": 0,
        "reverse": False,
        "source_timerange": {"duration": _us(dur), "start": 0},
        "speed": 1.0,
        "target_timerange": {"duration": _us(dur), "start": _us(tl_start)},
        "track_attribute": 0,
        "track_render_index": 0,
        "visible": True,
        "volume": volume,
    }


# ---------------------------------------------------------------- 主流程


def export_native_draft(pipeline_dir: Path, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    pipeline_dir = Path(pipeline_dir).resolve()
    edl_path = pipeline_dir / "04_edl.json"
    subs_path = pipeline_dir / "05_subtitles.json"
    pkg_path = pipeline_dir / "06_packaging.json"
    if not edl_path.is_file():
        raise FileNotFoundError(f"缺少 04_edl.json: {edl_path}")

    edl = read_json(edl_path)
    subs = read_json(subs_path) if subs_path.is_file() else {}
    pkg = read_json(pkg_path) if pkg_path.is_file() else {}

    style_id = str(edl.get("style_id") or pkg.get("style_id") or "style_01")
    entry = load_style_entry(style_id)
    sub_spec = entry.get("subtitle_spec") or (subs.get("template_config") or {})

    proj_name = _safe_name(pipeline_dir.parent.name + "·" + style_id)
    root = Path(out_dir).resolve() if out_dir else pipeline_dir / "jianying_native"
    draft_dir = root / proj_name
    (draft_dir / "materials" / "video").mkdir(parents=True, exist_ok=True)
    (draft_dir / "materials" / "audio").mkdir(parents=True, exist_ok=True)

    root_uuid = _uid()
    total_sec = float(edl.get("total_duration_sec") or 0.0)

    # 视频轨
    video_mats: List[Dict[str, Any]] = []
    speeds: List[Dict[str, Any]] = []
    video_segs: List[Dict[str, Any]] = []
    media_seen: Dict[str, str] = {}
    for clip in edl.get("clips") or []:
        src = Path(str(clip.get("media_path") or ""))
        if not src.is_file():
            continue
        if str(src) not in media_seen:
            dst = draft_dir / "materials" / "video" / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
            media_seen[str(src)] = dst.name
        dur = float(clip.get("duration_sec") or 0.0)
        mat, spd = build_video_material(draft_dir / "materials" / "video" / media_seen[str(src)], dur, root_uuid, proj_name)
        video_mats.append(mat)
        speeds.append(spd)
        video_segs.append(
            build_video_segment(
                mat["id"], spd["id"],
                float(clip.get("source_in_sec") or 0.0), dur, float(clip.get("timeline_in_sec") or 0.0),
            )
        )

    # 字幕轨（真文字，可编辑）
    texts: List[Dict[str, Any]] = []
    sub_segs: List[Dict[str, Any]] = []
    for row in subs.get("entries") or []:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        st, en = float(row.get("start_sec") or 0.0), float(row.get("end_sec") or 0.0)
        if en <= st:
            continue
        mat, seg = build_text_material(text, sub_spec, start_sec=st, end_sec=en)
        texts.append(mat)
        sub_segs.append(seg)

    # 花字轨（同样保留为文字素材）
    flower_texts: List[Dict[str, Any]] = []
    flower_segs: List[Dict[str, Any]] = []
    fps = int(edl.get("fps") or DEFAULT_FPS)
    for el in pkg.get("visual_elements") or []:
        label = str(el.get("text_or_asset") or "").strip()
        if not label or not any("\u4e00" <= ch <= "\u9fff" for ch in label):
            continue
        st = float(el.get("start_frame") or 0) / fps
        en = float(el.get("end_frame") or 0) / fps
        if en <= st:
            continue
        mat, seg = build_text_material(
            label, sub_spec, start_sec=st, end_sec=en,
            size_units=float(sub_spec.get("font_size_jianying_units") or 15.0) * 2.2,
            is_flower=True,
            fill_hex=str(el.get("fg_hex") or "") or None,
            stroke_hex=str(el.get("stroke_hex") or "") or None,
        )
        flower_texts.append(mat)
        flower_segs.append(seg)

    # 音频轨：人声母带 / BGM / 音效（存在才挂）
    # 三端同源：走 common.resolve_audio_stems()，优先 08_voice_master / 08_bgm_ducked / 08_sfx_master，
    # 缺 08 时才回退 03 锁定音轨与 06 音效轨（旧代码只找从未被创建的 media/audio/，导出物永远无声音）。
    def _rj(p: Path) -> Dict[str, Any]:
        try:
            return read_json(p) if p.is_file() else {}
        except Exception:
            return {}

    _ae = _rj(pipeline_dir / "03_audio_events.json")
    _mix = _rj(pipeline_dir / "08_mix_metrics.json")
    stems = resolve_audio_stems(pipeline_dir, _ae, _mix)
    audios: List[Dict[str, Any]] = []
    audio_tracks: List[List[Dict[str, Any]]] = []
    for cand, cat in (
        (stems.get("voice"), "voice"),
        (stems.get("bgm"), "bgm"),
        (stems.get("sfx") or Path(str(pkg.get("sfx_track_wav") or "")), "sfx"),
    ):
        p = Path(str(cand))
        if not p.is_file():
            continue
        dst = draft_dir / "materials" / "audio" / p.name
        if not dst.exists():
            shutil.copy2(p, dst)
        dur = max(0.1, total_sec)
        mat = build_audio_material(dst, dur, root_uuid, p.stem, cat)
        audios.append(mat)
        audio_tracks.append([build_audio_segment(mat["id"], dur, 0.0, 1.0)])

    tracks: List[Dict[str, Any]] = []
    if video_segs:
        tracks.append({"attribute": 0, "flag": 0, "id": _uid(), "is_default_name": True, "name": "", "segments": video_segs, "type": "video"})
    for segs in (sub_segs, flower_segs):
        if segs:
            tracks.append({"attribute": 0, "flag": 0, "id": _uid(), "is_default_name": True, "name": "", "segments": segs, "type": "text"})
    for segs in audio_tracks:
        tracks.append({"attribute": 0, "flag": 0, "id": _uid(), "is_default_name": True, "name": "", "segments": segs, "type": "audio"})

    now_us = int(time.time() * US)
    content = {
        "canvas_config": {"height": CANVAS_HEIGHT, "ratio": "original", "width": CANVAS_WIDTH},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": len(audios),
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "duration": _us(max(total_sec, max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US for t in tracks for s in t["segments"]))),
        "extra_info": None,
        "fps": float(fps),
        "function_assistant_info": {"fps": {}},
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": _uid(),
        "is_minimal_draft": True,
        "keyframe_graph_list": [],
        "keyframes": {
            "adjusts": [], "audios": [], "effects": [], "filters": [],
            "handwrites": [], "stickers": [], "texts": [], "videos": [],
        },
        "last_modified_platform": {"app_id": 3704, "app_source": "lv", "app_version": "11.8.0", "os": _jy_os_value()},
        "materials": {
            "audios": audios,
            "beats": [],
            "canvases": [],
            "material_animations": [],
            "material_colors": [],
            "placeholder_infos": [],
            "sound_channel_mappings": [],
            "speeds": speeds,
            "texts": texts + flower_texts,
            "transitions": [],
            "videos": video_mats,
            "vocal_separations": [],
        },
        "mutable_config": None,
        "name": proj_name,
        "new_version": "125.0.0",
        "path": "",
        "platform": {"app_id": 3704, "app_source": "lv", "app_version": "11.8.0", "os": _jy_os_value()},
        "relationships": [],
        "render_index_track_mode_on": True,
        "retouch_cover": None,
        "smart_ads_info": {},
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": tracks,
        "uneven_animation_template_info": {"composition": "none", "content": "", "order": "none", "sub_template_id": ""},
        "update_time": now_us,
        "version": 360000,
    }

    write_json(draft_dir / "draft_content.json", content)

    meta = {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False,
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_fold_path": str(draft_dir).replace("\\", "/"),
        "draft_id": _uid(),
        "draft_is_ae_produce": False,
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_article_video_draft": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_invisible": False,
        "draft_is_web_article_video": False,
        "draft_materials": [
            {
                "type": 0,
                "value": [
                    {
                        "duration": m["duration"],
                        "extra_info": m["material_name"],
                        "file_Path": "./materials/video/" + m["material_name"],
                        "height": m["height"],
                        "id": m["local_material_id"],
                        "metetype": "video",
                        "roughcut_time_range": {"duration": -1, "start": -1},
                        "sub_time_range": {"duration": -1, "start": -1},
                        "type": 0,
                        "width": m["width"],
                    }
                    for m in video_mats
                ],
            },
            {"type": 1, "value": []},
            {"type": 2, "value": []},
            {"type": 3, "value": []},
            {"type": 6, "value": []},
            {"type": 7, "value": []},
            {"type": 8, "value": []},
        ],
        "draft_name": proj_name,
        "draft_removable_storage_device": "",
        "draft_root_path": str(draft_dir.parent).replace("\\", "/"),
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": int(content["duration"]),
    }
    write_json(draft_dir / "draft_meta_info.json", meta)

    readme = "\n".join([
        f"【{proj_name} 原生剪映草稿（可编辑工程）】",
        "",
        "1. 把整个目录放进剪映的草稿根目录（剪映「全局设置 → 草稿位置」里能看到该路径）。",
        "2. 打开剪映专业版即可在草稿列表看到它；文字、花字、画面、音频分轨独立，可直接改。",
        "3. 这里没有烘焙的透明 PNG 图层，字幕与花字都是**真文字素材**，",
        f"   字体族沿用源模版的「{sub_spec.get('source_font_family') or sub_spec.get('font_family') or '未知'}」，",
        "   本机没装该字体会回落到剪映默认字体（样式参数不变）。",
        "4. 版式参数（字号/描边/底框/位置）按源草稿实测写入，改字不会跑版。",
        "",
        f"风格：{style_id}  {entry.get('style_name_cn', '')}",
        f"时长：{total_sec:.2f}s  画面段：{len(video_segs)}  字幕条：{len(sub_segs)}  花字条：{len(flower_segs)}  音轨：{len(audios)}",
    ]) + "\n"
    (draft_dir / "README_原生草稿使用说明.txt").write_text(readme, encoding="utf-8")

    status = {
        "status": "success",
        "export_kind": "jianying_native_draft",
        "editable_text": True,
        "baked_layers": False,
        "draft_dir": str(draft_dir),
        "draft_content": str(draft_dir / "draft_content.json"),
        "style_id": style_id,
        "counts": {
            "video_segments": len(video_segs),
            "subtitle_segments": len(sub_segs),
            "flower_segments": len(flower_segs),
            "audio_tracks": len(audios),
        },
        "source_font_family": sub_spec.get("source_font_family") or sub_spec.get("font_family"),
        "message": f"原生剪映草稿导出成功：{len(video_segs)} 段画面 / {len(sub_segs)} 条字幕 / {len(flower_segs)} 条花字，文字可编辑。",
    }
    write_json(pipeline_dir / "jianying_native_status.json", status)
    print(f"[jianying-native] {status['message']} -> {draft_dir}")
    return status


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="原生剪映草稿导出（文字可编辑，非烘焙 PNG）")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--out", default=None, help="输出父目录（默认 <pipeline>/jianying_native）")
    args = parser.parse_args()
    export_native_draft(Path(args.pipeline_dir), Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
