#!/usr/bin/env python3
"""
[Step 1: 素材规整 + HDR 色彩空间还原 + 物理几何坐标仿射映射]
将原始素材统一规整为：
- 解析 rotation 真实展示宽高，按显式 scale + crop 将裁切前 faces / eye_anchor / saliency 精确仿射映射至 1080x1920
- HDR/BT.2020 自动 Hable 色调映射至标准 Rec.709 (tv)
- 恒定帧率 CFR 30fps (-r 30 -fps_mode cfr)，规范视频去冗长 AAC (-an)
- 对含音频流素材提取 48kHz 16-bit PCM WAV，并严格尊重 00_shot_log.json 的 role 与 audio_grade
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (
    BT709_FFMPEG_ARGS,
    BT709_VF_SUFFIX,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    MEDIA_EXTS,
    compute_crop_transform,
    ffmpeg_has_filter,
    ffprobe_media,
    find_bin,
    map_eye_anchor_to_crop,
    map_norm_bbox_to_crop,
    maybe_reexec_in_venv,
    parse_fps,
    parse_video_stream_geometry,
    read_json,
    run_cmd,
    write_json,
)


def _build_hdr_prefix() -> str:
    if ffmpeg_has_filter("zscale"):
        return "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
    return "format=gbrpf32le,tonemap=hable:desat=0,colorspace=all=bt709:iall=bt2020:fast=1,"


def normalize_single_media(
    src: Path,
    out_dir: Path,
    idx: int,
    role_hint: str = "auto",
    shot_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg。")

    meta = ffprobe_media(src)
    streams = meta.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v_stream:
        raise ValueError(f"文件不含视频流: {src}")

    geom = parse_video_stream_geometry(v_stream)
    disp_w = geom["display_width"] or CANVAS_WIDTH
    disp_h = geom["display_height"] or CANVAS_HEIGHT
    crop_tx = compute_crop_transform(disp_w, disp_h, CANVAS_WIDTH, CANVAS_HEIGHT)

    r_fps = parse_fps(v_stream.get("r_frame_rate", "30/1"))
    avg_fps = parse_fps(v_stream.get("avg_frame_rate", "30/1"))
    is_vfr = abs(r_fps - avg_fps) > 0.5 or avg_fps == 0.0

    color_transfer = str(v_stream.get("color_transfer") or "unknown")
    color_primaries = str(v_stream.get("color_primaries") or "unknown")
    color_space = str(v_stream.get("color_space") or "unknown")
    was_hdr = color_transfer in ("arib-std-b67", "smpte2084") or "bt2020" in color_primaries or "bt2020" in color_space

    stem = f"norm_{idx:02d}_{src.stem}"
    out_mp4 = out_dir / f"{stem}.mp4"
    out_wav = out_dir / f"{stem}.wav"

    hdr_prefix = _build_hdr_prefix() if was_hdr else ""
    bt709_tail = ",".join(BT709_VF_SUFFIX)
    vf_chain = (
        f"{hdr_prefix}"
        f"scale={crop_tx['scaled_w']}:{crop_tx['scaled_h']}:flags=lanczos,"
        f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{crop_tx['crop_x']}:{crop_tx['crop_y']},"
        f"fps={DEFAULT_FPS},{bt709_tail}"
    )

    cmd_video = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", vf_chain,
        "-an",
        "-fps_mode", "cfr",
        "-r", str(DEFAULT_FPS),
        "-c:v", "libx264", *BT709_FFMPEG_ARGS, "-preset", "fast", "-crf", "18",
        str(out_mp4),
    ]

    hdr_tonemapped = was_hdr
    res = run_cmd(cmd_video, check=False)
    if res.returncode != 0 and was_hdr:
        fallback_vf = (
            f"colorspace=all=bt709:iall=bt2020:fast=1,"
            f"scale={crop_tx['scaled_w']}:{crop_tx['scaled_h']}:flags=lanczos,"
            f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{crop_tx['crop_x']}:{crop_tx['crop_y']},"
            f"fps={DEFAULT_FPS},{bt709_tail}"
        )
        cmd_fb = list(cmd_video)
        cmd_fb[cmd_fb.index("-vf") + 1] = fallback_vf
        fb_res = run_cmd(cmd_fb, check=False)
        if fb_res.returncode != 0:
            hdr_tonemapped = False
            plain_vf = (
                f"scale={crop_tx['scaled_w']}:{crop_tx['scaled_h']}:flags=lanczos,"
                f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{crop_tx['crop_x']}:{crop_tx['crop_y']},"
                f"fps={DEFAULT_FPS},{bt709_tail}"
            )
            cmd_fb[cmd_fb.index("-vf") + 1] = plain_vf
            run_cmd(cmd_fb, check=True)
    elif res.returncode != 0:
        raise RuntimeError(f"素材规整失败 ({src.name}): {(res.stderr or '')[-800:]}")

    has_audio = a_stream is not None
    if has_audio:
        run_cmd([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
            str(out_wav),
        ])

    norm_meta = ffprobe_media(out_mp4)
    nv = next(s for s in norm_meta["streams"] if s["codec_type"] == "video")
    norm_dur = float(norm_meta.get("format", {}).get("duration") or nv.get("duration") or 0.0)

    # 判定素材角色（优先显式标识，其次尊重 inventory_media 的 role 与 audio_grade）
    name_low = src.name.lower()
    shot_role = (shot_meta or {}).get("role")
    shot_ag = (shot_meta or {}).get("audio_grade")
    if role_hint != "auto":
        role = role_hint
    elif "code_broll" in name_low:
        role = "code_broll"
    elif "broll" in name_low or "b_roll" in name_low or not has_audio:
        role = "b_roll"
    elif "aroll" in name_low or "a_roll" in name_low or "talk" in name_low or "main" in name_low:
        role = "a_roll"
    elif shot_role == "b_roll_candidate" or shot_ag == "silent":
        role = "b_roll"
    elif shot_role == "a_roll_candidate":
        role = "a_roll"
    else:
        role = "a_roll" if has_audio else "b_roll"

    item: Dict[str, Any] = {
        "id": f"media_{idx:02d}",
        "source_path": str(src.resolve()),
        "normalized_mp4": str(out_mp4.resolve()),
        "normalized_wav": str(out_wav.resolve()) if has_audio else None,
        "role": role,
        "original_size": f"{disp_w}x{disp_h}",
        "coded_size": f"{geom['coded_width']}x{geom['coded_height']}",
        "rotation_deg": geom["rotation_deg"],
        "crop_transform": crop_tx,
        "was_vfr": is_vfr,
        "was_hdr": was_hdr,
        "hdr_tonemapped": hdr_tonemapped,
        "source_color_transfer": color_transfer,
        "source_color_primaries": color_primaries,
        "original_r_fps": round(r_fps, 2),
        "normalized_fps": DEFAULT_FPS,
        "duration_sec": round(norm_dur, 4),
        "has_audio": has_audio,
    }
    if shot_meta:
        for k in (
            "best_in_sec", "sharpness_score", "stability_score",
            "motion_speed_px", "accel_jitter_px",
            "audio_grade", "video_grade", "taboo_tags",
            "semantic_tags", "manual_tags", "scene_group",
        ):
            if k in shot_meta:
                item[k] = shot_meta[k]

        # 精确仿射映射 faces, eye_anchor, saliency_bbox 至 1080x1920 裁切后画布坐标系
        mapped_faces: List[Dict[str, Any]] = []
        for f_obj in shot_meta.get("faces") or []:
            nb = map_norm_bbox_to_crop(f_obj.get("bbox"), crop_tx)
            if nb is None:
                continue
            nf = dict(f_obj)
            nf["bbox"] = nb
            ea_tmp = map_eye_anchor_to_crop({"eye_cx": f_obj.get("eye_cx", 0.5), "eye_cy": f_obj.get("eye_cy", 0.35)}, crop_tx)
            if ea_tmp:
                nf["eye_cx"] = ea_tmp["eye_cx"]
                nf["eye_cy"] = ea_tmp["eye_cy"]
            mapped_faces.append(nf)
        item["faces"] = mapped_faces

        ea = shot_meta.get("eye_anchor")
        if isinstance(ea, dict):
            mapped_ea = map_eye_anchor_to_crop(
                {"eye_cx": ea.get("cx", ea.get("eye_cx", 0.5)), "eye_cy": ea.get("cy", ea.get("eye_cy", 0.36)), **{k: v for k, v in ea.items()}},
                crop_tx,
            )
            if mapped_ea:
                mapped_ea["cx"] = mapped_ea["eye_cx"]
                mapped_ea["cy"] = mapped_ea["eye_cy"]
            item["eye_anchor"] = mapped_ea
        else:
            item["eye_anchor"] = None

        item["saliency_bbox"] = map_norm_bbox_to_crop(shot_meta.get("saliency_bbox"), crop_tx)
    return item


def run_step1(input_paths: List[Path], pipeline_dir: Path) -> Dict[str, Any]:
    norm_dir = pipeline_dir / "01_normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)

    shot_by_path: Dict[str, Dict[str, Any]] = {}
    shot_by_name: Dict[str, Dict[str, Any]] = {}
    shot_log_p = pipeline_dir / "00_shot_log.json"
    if shot_log_p.exists():
        sl = read_json(shot_log_p)
        for s in sl.get("shots", []):
            if s.get("path"):
                shot_by_path[str(Path(s["path"]).resolve())] = s
            if s.get("filename"):
                shot_by_name[str(s["filename"])] = s

    media_files: List[Path] = []
    for p in input_paths:
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in MEDIA_EXTS and "benchmark" not in child.name.lower():
                    media_files.append(child)
        elif p.is_file() and p.suffix.lower() in MEDIA_EXTS and "benchmark" not in p.name.lower():
            media_files.append(p)

    if not media_files:
        raise FileNotFoundError("未找到任何可用的原始视频素材文件。")

    items: List[Dict[str, Any]] = []
    for idx, f in enumerate(media_files, start=1):
        sm = shot_by_path.get(str(f.resolve())) or shot_by_name.get(f.name)
        items.append(normalize_single_media(f, norm_dir, idx, shot_meta=sm))

    hdr_cnt = sum(1 for it in items if it.get("was_hdr"))
    manifest = {
        "step": "01_normalize",
        "fps": DEFAULT_FPS,
        "resolution": f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
        "sample_rate": 48000,
        "media_count": len(items),
        "hdr_tonemapped_count": hdr_cnt,
        "items": items,
    }
    write_json(pipeline_dir / "01_manifest.json", manifest)
    print(f"[step1] 素材规整完成：{len(items)} 条素材已统一为 {DEFAULT_FPS}fps CFR (HDR还原 {hdr_cnt} 条) -> 01_manifest.json")
    return manifest


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 1: 素材规整 (旋转解算 + 仿射坐标映射 + HDR映射 + CFR)")
    parser.add_argument("--input", required=True, nargs="+", help="原始素材目录或视频文件路径列表")
    parser.add_argument("--pipeline-dir", required=True, help="pipeline 输出目录")
    args = parser.parse_args()
    run_step1([Path(x) for x in args.input], Path(args.pipeline_dir))


if __name__ == "__main__":
    main()
