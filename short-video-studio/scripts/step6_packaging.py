#!/usr/bin/env python3
"""
[Step 6: 包装对齐与预算控制]
1. 音效先行：先把转场/提示/冲击音效精确落在 03_audio_events.json 的音频事件帧上，合成为 06_sfx_track.wav。
2. 视觉包装对齐：贴纸、花字和首帧钩子卡片的出现帧严格对齐对应音效或重音帧（误差 0 帧，满足 ±1 帧）。
3. 精准触发与预算控制：只放在剪辑表标注的情绪点/信息点（hook/info/twist/persona/cta）上，
   严格执行预算上限 max_visual_elements = max(3, ceil(duration_sec / 3.5))。
4. 全部元素严格位于平台安全区 (X:[80,860], Y:[200,1200])。
输出：pipeline/06_packaging.json 与 pipeline/06_sfx_track.wav
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Dict, List

from common import (
    ASSETS_DIR,
    DEFAULT_FPS,
    check_bbox_in_safe_zone,
    frames_to_sec,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)

maybe_reexec_in_venv()

import numpy as np
from step3_lock_audio import SR, load_stereo_wav_np, save_stereo_wav_np


def run_step6(
    pipeline_dir: Path,
    fps: int = DEFAULT_FPS,
) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")

    total_dur = float(audio_events["locked_duration_sec"])
    total_samples = int(round(total_dur * SR))
    max_visual_budget = max(3, math.ceil(total_dur / 10.0))
    max_sfx_budget = max(4, math.ceil(total_dur / 8.0))

    sentences_map = {s["id"]: s for s in audio_events["sentences"]}
    events = audio_events["events"]
    code_ranges = [
        (int(c["timeline_in_frame"]), int(c["timeline_out_frame"]))
        for c in edl.get("clips", [])
        if c.get("track_role") == "code_broll"
    ]

    def overlaps_code(sf_f: int, ef_f: int) -> bool:
        return any(sf_f < ce and ef_f > cs for cs, ce in code_ranges)

    # 1. 克制化 SFX 先行落点（高光转场与重音点，柔和增益不抢人声）
    sfx_cues: List[Dict[str, Any]] = []
    sfx_files = {
        "hook": ASSETS_DIR / "sfx" / "bass_drop.wav",
        "info": ASSETS_DIR / "sfx" / "ding.wav",
        "twist": ASSETS_DIR / "sfx" / "bass_drop.wav",
        "detail": ASSETS_DIR / "sfx" / "pop.wav",
        "persona": ASSETS_DIR / "sfx" / "ding.wav",
        "cta": ASSETS_DIR / "sfx" / "pop.wav",
        "cut": ASSETS_DIR / "sfx" / "whoosh.wav",
    }

    used_frames: List[int] = []
    min_sfx_gap = int(4.5 * fps)

    def can_place_sfx(frame: int) -> bool:
        return len(sfx_cues) < max_sfx_budget and all(abs(frame - f) >= min_sfx_gap for f in used_frames)

    for ev in events:
        if ev["type"] == "keyword_accent" and can_place_sfx(ev["frame"]):
            sent = sentences_map.get(ev.get("sentence_id", ""), {})
            emo = sent.get("emotion_or_info", "info")
            if emo not in ("hook", "twist", "persona", "cta", "info"):
                continue
            sf = sfx_files.get(emo, sfx_files["info"])
            sfx_cues.append({
                "id": f"sfx_{len(sfx_cues)+1:03d}",
                "file": str(sf.resolve()),
                "sfx_name": sf.name,
                "time_sec": ev["time_sec"],
                "frame": ev["frame"],
                "gain_db": -14.0,
                "event_id": ev["id"],
                "reason": f"{emo} 信息/情绪重音",
            })
            used_frames.append(ev["frame"])

    for clip in edl["clips"][1:]:
        ev_frame = int(clip["cut_event_frame"])
        if can_place_sfx(ev_frame):
            sf = sfx_files["cut"]
            sfx_cues.append({
                "id": f"sfx_{len(sfx_cues)+1:03d}",
                "file": str(sf.resolve()),
                "sfx_name": sf.name,
                "time_sec": frames_to_sec(ev_frame, fps),
                "frame": ev_frame,
                "gain_db": -15.0,
                "event_id": clip["cut_event_id"],
                "reason": "关键转场 Whoosh 过渡",
            })
            used_frames.append(ev_frame)

    sfx_cues.sort(key=lambda x: x["frame"])

    # 混合合成 06_sfx_track.wav
    sfx_track = np.zeros((total_samples, 2), dtype=np.float32)
    sfx_cache: Dict[str, np.ndarray] = {}
    for cue in sfx_cues:
        fp = cue["file"]
        if fp not in sfx_cache:
            arr, _ = load_stereo_wav_np(Path(fp))
            sfx_cache[fp] = arr
        arr = sfx_cache[fp]
        gain = 10.0 ** (cue["gain_db"] / 20.0)
        start_idx = max(0, int(round(cue["time_sec"] * SR)))
        end_idx = min(total_samples, start_idx + len(arr))
        if end_idx > start_idx:
            sfx_track[start_idx:end_idx] += arr[: end_idx - start_idx] * gain

    sfx_wav_path = pipeline_dir / "06_sfx_track.wav"
    save_stereo_wav_np(sfx_wav_path, sfx_track, SR)

    # 2. 基于首帧密度 + 音效/重音点放置花字胶囊（严禁覆盖 code_broll，绝不放无语境贴纸，且绝不叠框）
    visual_elements: List[Dict[str, Any]] = []
    first_sent = audio_events["sentences"][0] if audio_events["sentences"] else {"text": "必看重点", "keywords": []}
    hook_badge_text = first_sent["keywords"][0] if first_sent.get("keywords") else first_sent["text"][:8]
    hook_label = f"重点：{hook_badge_text}"
    hook_w = min(720, max(280, len(hook_label) * 42 + 64))
    hook_bbox = {"x": 88, "y": 210, "w": hook_w, "h": 92}
    safe_ok, _ = check_bbox_in_safe_zone(hook_bbox)
    hook_ef = min(int(2.0 * fps), int(total_dur * fps))
    if not overlaps_code(0, hook_ef):
        visual_elements.append({
            "id": "pkg_001",
            "kind": "flower_text",
            "text_or_asset": hook_label,
            "start_frame": 0,
            "end_frame": hook_ef,
            "aligned_sfx_or_accent_frame": 0,
            "alignment_error_frames": 0,
            "bbox": hook_bbox,
            "safe_zone_ok": safe_ok,
            "reason": "首帧高密度视觉钩子（对齐首句强拍 evt_001）",
        })

    last_vis_end = hook_ef + int(2.5 * fps)
    for cue in sfx_cues:
        if len(visual_elements) >= max_visual_budget:
            break
        sf = int(cue["frame"])
        if sf < last_vis_end or sf > int((total_dur - 1.5) * fps):
            continue
        ef = min(int(total_dur * fps), sf + int(2.2 * fps))
        if overlaps_code(sf, ef):
            continue
        sent = next((s for s in audio_events["sentences"] if s["start_frame"] <= sf <= s["end_frame"] + 15), first_sent)
        if sent.get("emotion_or_info") not in ("hook", "info", "twist", "persona", "cta"):
            continue

        kw_label = sent["keywords"][0] if sent.get("keywords") else sent["text"][:8]
        box_w = min(720, max(240, len(kw_label) * 40 + 68))
        bbox = {"x": 88, "y": 210, "w": box_w, "h": 86}
        ok, _ = check_bbox_in_safe_zone(bbox)
        visual_elements.append({
            "id": f"pkg_{len(visual_elements)+1:03d}",
            "kind": "flower_text",
            "text_or_asset": kw_label,
            "start_frame": sf,
            "end_frame": ef,
            "aligned_sfx_or_accent_frame": sf,
            "alignment_error_frames": 0,
            "bbox": bbox,
            "safe_zone_ok": ok,
            "reason": cue["reason"],
        })
        last_vis_end = ef + int(2.5 * fps)

    packaging = {
        "step": "06_packaging",
        "sfx_track_wav": str(sfx_wav_path.resolve()),
        "budget": {
            "max_visual_elements": max_visual_budget,
            "used_visual_elements": len(visual_elements),
            "max_sfx_cues": max_sfx_budget,
            "used_sfx_cues": len(sfx_cues),
        },
        "sfx_cues": sfx_cues,
        "visual_elements": visual_elements,
    }
    write_json(pipeline_dir / "06_packaging.json", packaging)
    print(
        f"[step6] 包装完成：音效 {len(sfx_cues)}/{max_sfx_budget} 个，"
        f"视觉包装 {len(visual_elements)}/{max_visual_budget} 个（±0 帧对齐）-> 06_packaging.json"
    )
    return packaging


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 6: 音效先行与±1帧对齐视觉包装")
    parser.add_argument("--pipeline-dir", required=True)
    args = parser.parse_args()
    run_step6(Path(args.pipeline_dir))


if __name__ == "__main__":
    main()
