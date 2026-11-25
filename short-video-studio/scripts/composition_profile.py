#!/usr/bin/env python3
"""Measure A-roll composition and recommend where to place on-screen text."""

from __future__ import annotations

import argparse
import math
import subprocess
from io import BytesIO
from pathlib import Path
from statistics import median
from typing import Any, Dict, Optional

import numpy as np
from PIL import Image

import face_framing
from common import MEDIA_EXTS, find_bin, map_norm_bbox_to_crop, read_json, write_json

PROFILE_FILENAME = "01b_composition_profile.json"
_CLOSEUP_STAGE = "closeup_chest_stage"
_WIDE_STAGE = "wide_behind_person"
_LONG_TAKE_SEC = 12.0


def _clip_bbox(bbox: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not isinstance(bbox, dict):
        return None
    try:
        x, y, w, h = (float(bbox[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x, y, w, h)):
        return None

    left, top = max(0.0, x), max(0.0, y)
    width = min(w + min(0.0, x), 1.0 - left)
    height = min(h + min(0.0, y), 1.0 - top)
    if width <= 0.0 or height <= 0.0:
        return None
    return {"x": left, "y": top, "w": width, "h": height}


def _pixel_slice(start: float, end: float, size: int) -> slice:
    return slice(int(start * size), math.ceil(end * size))


def _mean_gradient(regions: tuple[np.ndarray, ...], step: int) -> float:
    total, count = 0.0, 0
    for region in regions:
        dx = np.abs(region[:, step:] - region[:, :-step])
        dy = np.abs(region[step:, :] - region[:-step, :])
        total += float(dx.sum()) + float(dy.sum())
        count += dx.size + dy.size
    return total / max(1, count)


def _background_complexity(luma: np.ndarray, bbox: Optional[Dict[str, float]]) -> float:
    height, width = luma.shape
    if bbox is None:
        regions = (luma[:math.ceil(0.30 * height)],)
    else:
        rows = _pixel_slice(bbox["y"], bbox["y"] + bbox["h"], height)
        cols = _pixel_slice(bbox["x"], bbox["x"] + bbox["w"], width)
        regions = (luma[:rows.start], luma[rows, :cols.start], luma[rows, cols.stop:])

    # Adjacent pixels catch fine texture. A wider baseline catches edges on signs and shelves.
    step = max(1, round(min(height, width) * 0.0125))
    gradient = _mean_gradient(regions, 1)
    if step > 1:
        gradient = max(gradient, _mean_gradient(regions, step))
    return min(1.0, gradient / 40.0)


def _dark_chest_ratio(chest: np.ndarray) -> float:
    if not chest.size:
        return 0.0
    windows = np.lib.stride_tricks.sliding_window_view(np.pad(chest, 1, mode="edge"), (3, 3))
    std = windows.std(axis=(-2, -1))
    return float(np.mean((chest <= 95) & (std <= 12.0)))


def _measure_frame(frame_img: Optional[Image.Image], bbox: Optional[Dict[str, float]]) -> Dict[str, float]:
    face_area, headroom, shift_x = 0.0, 0.30, 0.0
    chest_left, chest_top, chest_right, chest_bottom = 0.22, 0.58, 0.78, 0.82
    if bbox is not None:
        x, y, w, h = (bbox[key] for key in ("x", "y", "w", "h"))
        face_area, headroom = w * h, y
        face_right, face_bottom = x + w, y + h
        chest_left, chest_right = max(0.0, x - 0.25 * w), min(1.0, face_right + 0.25 * w)
        chest_top = min(1.0, face_bottom + 0.45 * h)
        chest_bottom = min(1.0, face_bottom + 1.25 * h)

        # Signed fraction of frame width. Keep a 6% margin around the face and limit the move.
        room_left = max(0.0, x - 0.06)
        room_right = max(0.0, 1.0 - face_right - 0.06)
        shift_x = min(0.12, room_right) if room_right >= room_left else -min(0.12, room_left)

    # The chest midpoint is below the face. Keep the center within the subtitle-safe band.
    safe_y = max(-0.56, min(-0.34, 1.0 - chest_top - chest_bottom))
    chest_dark_ratio, bg_complexity = 0.0, 0.0
    if frame_img is not None:
        luma = np.asarray(frame_img if frame_img.mode == "L" else frame_img.convert("L"), dtype=np.float32)
        height, width = luma.shape
        chest_rows = _pixel_slice(chest_top, chest_bottom, height)
        chest_cols = _pixel_slice(chest_left, chest_right, width)
        chest_dark_ratio = _dark_chest_ratio(luma[chest_rows, chest_cols])
        bg_complexity = _background_complexity(luma, bbox)

    return {
        "face_area_ratio": face_area,
        "headroom_ratio": headroom,
        "chest_dark_ratio": chest_dark_ratio,
        "bg_complexity": bg_complexity,
        "safe_fg_y_norm": safe_y,
        "safe_center_shift_x": shift_x,
    }


def _with_stage(metrics: Dict[str, float]) -> Dict[str, Any]:
    headroom = metrics["headroom_ratio"]
    closeup = (
        metrics["face_area_ratio"] >= 0.085
        or 0.0 < headroom < 0.22
        or (metrics["bg_complexity"] >= 0.42 and headroom < 0.28)
    )
    return {
        **metrics,
        "allow_behind_person": not closeup,
        "recommended_stage_mode": _CLOSEUP_STAGE if closeup else _WIDE_STAGE,
    }


def analyze_frame_composition(
    frame_img: Image.Image, face_bbox: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Measure composition given a top-left-origin, normalized face box."""
    return _with_stage(_measure_frame(frame_img, _clip_bbox(face_bbox)))


def _first_face_bbox(faces: Any) -> Optional[Dict[str, float]]:
    for face in faces or []:
        if isinstance(face, dict):
            bbox = _clip_bbox(face.get("bbox"))
            if bbox is not None:
                return bbox
    return None


def _duration_sec(item: Dict[str, Any]) -> float:
    try:
        duration = float(item.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return duration if math.isfinite(duration) and duration > 0.0 else 0.0


def _sample_frame(path: Path, at_sec: float, ffmpeg_bin: str) -> Optional[Image.Image]:
    cmd = [
        ffmpeg_bin, "-nostdin", "-v", "error", "-ss", f"{at_sec:.3f}", "-i", str(path),
        "-map", "0:v:0", "-vf", "scale=w='min(iw,540)':h='min(ih,960)':force_original_aspect_ratio=decrease",
        "-frames:v", "1", "-c:v", "png", "-f", "image2pipe", "pipe:1",
    ]
    try:
        # The shared text-mode command runner cannot carry PNG bytes.
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        if result.returncode != 0 or not result.stdout:
            return None
        with Image.open(BytesIO(result.stdout)) as frame:
            return frame.convert("L")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _detect_face(path: Path, duration: float) -> Optional[Dict[str, float]]:
    try:
        anchor = face_framing.anchor_for(
            str(path), duration / 2.0, duration or 3.0, cache_scope=str(path.resolve()),
        )
        if anchor:
            w, h = float(anchor["w"]), float(anchor["h"])
            return _clip_bbox({"x": float(anchor["cx"]) - w / 2.0, "y": float(anchor["cy"]) - h / 2.0,
                               "w": w, "h": h})
    except Exception:
        # The local vision backend is optional.
        return None
    return None


def analyze_aroll_item(item: Dict[str, Any], ffmpeg_bin: str = "ffmpeg") -> Dict[str, Any]:
    """Analyze up to two decoded frames, or use face metadata when decoding fails."""
    media_path = str(item.get("normalized_mp4") or item.get("source_path") or "")
    duration = _duration_sec(item)
    bbox = _first_face_bbox(item.get("faces"))
    frames = []
    path = Path(media_path)
    try:
        has_video = path.is_file()
    except OSError:
        has_video = False
    if has_video:
        timestamps = (0.20 * duration, 0.60 * duration) if duration else (0.0,)
        for at_sec in timestamps:
            frame = _sample_frame(path, at_sec, ffmpeg_bin)
            if frame is not None:
                frames.append(frame)
        if frames and bbox is None:
            bbox = _detect_face(path, duration)

    samples = [_measure_frame(frame, bbox) for frame in frames] or [_measure_frame(None, bbox)]
    metrics = {key: median(sample[key] for sample in samples) for key in samples[0]}
    return {"media_path": media_path, "sampled_frames": len(frames), **_with_stage(metrics)}


def _merge_inventory(item: Dict[str, Any], clip: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**clip, **item}
    if not _first_face_bbox(item.get("faces")):
        bbox = _first_face_bbox(clip.get("faces"))
        if bbox and item.get("normalized_mp4"):
            bbox = map_norm_bbox_to_crop(bbox, item.get("crop_transform") or {})
        merged["faces"] = [{"bbox": bbox}] if bbox else []
    return merged


def _usable(item: Dict[str, Any]) -> bool:
    return item.get("usable", True) is not False and item.get("video_grade") not in ("ng", "audio_only")


def build_composition_profile(pipeline_dir: Path, overwrite: bool = True) -> Dict[str, Any]:
    """Write the project profile using the manifest and optional source inventory."""
    pipeline_dir = pipeline_dir.resolve()
    output = pipeline_dir / PROFILE_FILENAME
    if not overwrite and output.is_file():
        return read_json(output)

    manifest = read_json(pipeline_dir / "01_manifest.json")
    inventory_path = pipeline_dir / "00_inventory.json"
    inventory = read_json(inventory_path) if inventory_path.is_file() else {}
    inventory_by_source = {pipeline_dir / clip["path"]: clip for clip in inventory.get("clips", []) if clip.get("path")}

    arolls, manifest_sources = [], set()
    has_cutaways = False
    for item in manifest.get("items", []):
        normalized = item.get("normalized_mp4")
        media_path = normalized or item.get("source_path")
        if not media_path or (not normalized and Path(media_path).suffix.lower() not in MEDIA_EXTS):
            continue
        source = pipeline_dir / (item.get("source_path") or media_path)
        manifest_sources.add(source)
        merged = _merge_inventory(item, inventory_by_source.get(source, {}))
        if item.get("role") == "a_roll":
            arolls.append(merged)
        elif item.get("role") == "b_roll" and _usable(merged) and not item.get("source_is_code_scene"):
            has_cutaways = True
    has_cutaways = has_cutaways or any(
        source not in manifest_sources and source.suffix.lower() in MEDIA_EXTS
        and clip.get("suggested_role") == "b_roll_candidate" and _usable(clip)
        for source, clip in inventory_by_source.items()
    )

    arolls = [item for item in arolls if _usable(item)] or arolls
    ffmpeg = find_bin("ffmpeg") or "ffmpeg"
    profiles = []
    for item in arolls:
        path_key = "normalized_mp4" if item.get("normalized_mp4") else "source_path"
        profile = analyze_aroll_item({**item, path_key: pipeline_dir / item[path_key]}, ffmpeg)
        profile["media_path"] = str(item[path_key])
        profiles.append(profile)

    allow_behind = all(profile["allow_behind_person"] for profile in profiles)
    two_long_closeups = len(arolls) == 2 and all(
        not profile["allow_behind_person"] and _duration_sec(item) >= _LONG_TAKE_SEC
        for item, profile in zip(arolls, profiles)
    )
    business = not has_cutaways and (len(arolls) == 1 or two_long_closeups)
    result = {
        "step": "01b_composition_profile",
        "primary_stage_mode": _WIDE_STAGE if allow_behind else _CLOSEUP_STAGE,
        "allow_behind_person": allow_behind,
        "theme_hint": "business_editorial" if business else "store_vibrant",
        "aroll_profiles": profiles,
    }
    write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="分析口播画面构图，推荐花字位置")
    parser.add_argument("--pipeline-dir", type=Path, required=True, help="流水线输出目录")
    parser.add_argument("--no-overwrite", action="store_true", help="复用已有构图画像")
    args = parser.parse_args()
    profile = build_composition_profile(args.pipeline_dir, overwrite=not args.no_overwrite)
    print(f"[composition_profile] {len(profile['aroll_profiles'])} 条口播素材 -> {PROFILE_FILENAME}")


if __name__ == "__main__":
    main()
