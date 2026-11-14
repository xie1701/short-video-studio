#!/usr/bin/env python3
"""
[Step 1: 素材规整]
将输入的原始手机素材（可能为 VFR 可变帧率、横屏/竖屏混杂、不同采样率）统一规整为：
- 恒定帧率 CFR 30fps (-r 30 -fps_mode cfr)
- 标准竖屏 1080x1920（自动等比放大裁剪居中或安全黑边消除）
- 保留 48kHz 16-bit PCM 原始无损 WAV 音轨（供后续字级精准剪辑与响度分析）
输出：pipeline/01_manifest.json 与 pipeline/01_normalized/*.mp4/*.wav
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    ffprobe_media,
    find_bin,
    maybe_reexec_in_venv,
    parse_fps,
    run_cmd,
    write_json,
)

MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def normalize_single_media(src: Path, out_dir: Path, idx: int, role_hint: str = "auto") -> Dict[str, Any]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg。")

    meta = ffprobe_media(src)
    streams = meta.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v_stream:
        raise ValueError(f"文件不含视频流: {src}")

    src_w = int(v_stream.get("width", 1080))
    src_h = int(v_stream.get("height", 1920))
    r_fps = parse_fps(v_stream.get("r_frame_rate", "30/1"))
    avg_fps = parse_fps(v_stream.get("avg_frame_rate", "30/1"))
    is_vfr = abs(r_fps - avg_fps) > 0.5 or avg_fps == 0.0

    stem = f"norm_{idx:02d}_{src.stem}"
    out_mp4 = out_dir / f"{stem}.mp4"
    out_wav = out_dir / f"{stem}.wav"

    # 竖屏 1080x1920 居中裁切（不留黑边避免黑帧检测误报）
    vf_chain = (
        f"scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_WIDTH}:{CANVAS_HEIGHT},fps={DEFAULT_FPS},format=yuv420p"
    )

    cmd_video = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", vf_chain,
        "-fps_mode", "cfr",
        "-r", str(DEFAULT_FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
    ]
    if a_stream:
        cmd_video += ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k"]
    else:
        cmd_video += ["-an"]
    cmd_video.append(str(out_mp4))
    run_cmd(cmd_video)

    # 提取或生成对应的 48kHz 16-bit WAV
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

    # 判定素材角色
    name_low = src.name.lower()
    if role_hint != "auto":
        role = role_hint
    elif "code_broll" in name_low:
        role = "code_broll"
    elif "broll" in name_low or "b_roll" in name_low or not has_audio:
        role = "b_roll"
    elif "aroll" in name_low or "a_roll" in name_low or "talk" in name_low or "main" in name_low:
        role = "a_roll"
    else:
        role = "a_roll" if has_audio else "b_roll"

    return {
        "id": f"media_{idx:02d}",
        "source_path": str(src.resolve()),
        "normalized_mp4": str(out_mp4.resolve()),
        "normalized_wav": str(out_wav.resolve()) if has_audio else None,
        "role": role,
        "original_size": f"{src_w}x{src_h}",
        "was_vfr": is_vfr,
        "original_r_fps": round(r_fps, 2),
        "normalized_fps": DEFAULT_FPS,
        "duration_sec": round(norm_dur, 4),
        "has_audio": has_audio,
    }


def run_step1(input_paths: List[Path], pipeline_dir: Path) -> Dict[str, Any]:
    norm_dir = pipeline_dir / "01_normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)

    media_files: List[Path] = []
    for p in input_paths:
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in MEDIA_EXTS and "benchmark" not in child.name.lower():
                    media_files.append(child)
        elif p.is_file() and p.suffix.lower() in MEDIA_EXTS:
            media_files.append(p)

    if not media_files:
        raise FileNotFoundError("未找到任何可用的原始视频素材文件。")

    items: List[Dict[str, Any]] = []
    for idx, f in enumerate(media_files, start=1):
        items.append(normalize_single_media(f, norm_dir, idx))

    manifest = {
        "step": "01_normalize",
        "fps": DEFAULT_FPS,
        "resolution": f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
        "sample_rate": 48000,
        "media_count": len(items),
        "items": items,
    }
    write_json(pipeline_dir / "01_manifest.json", manifest)
    print(f"[step1] 素材规整完成：{len(items)} 条素材已统一为 {DEFAULT_FPS}fps CFR -> 01_manifest.json")
    return manifest


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 1: 素材规整 (VFR转CFR + 提取48kHz WAV)")
    parser.add_argument("--input", required=True, nargs="+", help="原始素材目录或视频文件路径列表")
    parser.add_argument("--pipeline-dir", required=True, help="pipeline 输出目录")
    args = parser.parse_args()
    run_step1([Path(x) for x in args.input], Path(args.pipeline_dir))


if __name__ == "__main__":
    main()
