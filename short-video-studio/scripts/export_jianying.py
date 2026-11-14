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
from pathlib import Path
from typing import Any, Dict, List

from common import maybe_reexec_in_venv, read_json, write_json


def sec_to_us(sec: float) -> int:
    return int(round(sec * 1_000_000))


def build_jianying_draft_payload(pipeline_dir: Path) -> Dict[str, Any]:
    edl = read_json(pipeline_dir / "04_edl.json")
    subs = read_json(pipeline_dir / "05_subtitles.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    mix_metrics = read_json(pipeline_dir / "08_mix_metrics.json")

    total_us = sec_to_us(float(edl["total_duration_sec"]))
    draft_id = str(uuid.uuid4()).upper()

    materials_videos: List[Dict[str, Any]] = []
    video_segments: List[Dict[str, Any]] = []
    for clip in edl["clips"]:
        mat_id = str(uuid.uuid4()).upper()
        src_dur_us = sec_to_us(max(0.1, float(clip["source_out_sec"]) - float(clip["source_in_sec"])))
        tgt_dur_us = sec_to_us(float(clip["duration_sec"]))
        materials_videos.append({
            "id": mat_id,
            "path": clip["media_path"],
            "type": "video",
            "width": 1080,
            "height": 1920,
            "duration": src_dur_us,
        })
        video_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": mat_id,
            "source_timerange": {"start": sec_to_us(float(clip["source_in_sec"])), "duration": src_dur_us},
            "target_timerange": {"start": sec_to_us(float(clip["timeline_in_sec"])), "duration": tgt_dur_us},
            "volume": 0.0,
        })

    # 锁定音轨与混音母带
    voice_mat_id = str(uuid.uuid4()).upper()
    bgm_mat_id = str(uuid.uuid4()).upper()
    materials_audios = [
        {"id": voice_mat_id, "path": mix_metrics["master_wav"], "name": "08_mixed_master.wav", "duration": total_us},
        {"id": bgm_mat_id, "path": audio_events["locked_bgm_wav"], "name": "03_locked_bgm.wav", "duration": total_us},
    ]
    audio_segments = [
        {
            "id": str(uuid.uuid4()).upper(),
            "material_id": voice_mat_id,
            "source_timerange": {"start": 0, "duration": total_us},
            "target_timerange": {"start": 0, "duration": total_us},
            "volume": 1.0,
        }
    ]

    # 字幕轨
    materials_texts: List[Dict[str, Any]] = []
    text_segments: List[Dict[str, Any]] = []
    for sub in subs.get("entries", []):
        tid = str(uuid.uuid4()).upper()
        dur_us = max(100_000, sec_to_us(float(sub["end_sec"]) - float(sub["start_sec"])))
        materials_texts.append({
            "id": tid,
            "type": "subtitle",
            "content": sub["text"],
            "font_path": subs.get("font_path") or "",
        })
        text_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": tid,
            "target_timerange": {"start": sec_to_us(float(sub["start_sec"])), "duration": dur_us},
        })

    draft_content = {
        "id": draft_id,
        "version": 360000,
        "fps": float(edl.get("fps", 30)),
        "duration": total_us,
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "materials": {
            "videos": materials_videos,
            "audios": materials_audios,
            "texts": materials_texts,
        },
        "tracks": [
            {"id": str(uuid.uuid4()).upper(), "type": "video", "segments": video_segments},
            {"id": str(uuid.uuid4()).upper(), "type": "audio", "segments": audio_segments},
            {"id": str(uuid.uuid4()).upper(), "type": "text", "segments": text_segments},
        ],
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
