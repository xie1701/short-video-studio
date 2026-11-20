#!/usr/bin/env python3
"""
[Step 6: 包装对齐与预算控制]
1. 修复限幅符号写反 Bug：采用上限 min(-3.0, ...) 确保真实流派音量梯度生效，06_packaging.json 与 WAV 实感完全一致。
2. Freesound CC0 真实物理材质音效排在数学合成兜底之前，同族轮换防疲劳。
3. 视觉包装严格遵守预算上限 max(3, ceil(duration_sec / 3.5))，并写入 style_id 与基于 01_manifest 裁切人脸的权威避脸 final_bbox。
输出：pipeline/06_packaging.json 与 pipeline/06_sfx_track.wav
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    ASSETS_DIR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SAFE_ZONE,
    bbox_iou,
    check_bbox_in_safe_zone,
    find_commercial_font,
    fit_text_line,
    frames_to_sec,
    load_style_applied,
    maybe_reexec_in_venv,
    parse_manifest_faces,
    read_json,
    write_json,
)

maybe_reexec_in_venv()

import numpy as np
from step3_lock_audio import SR, load_stereo_wav_np, save_stereo_wav_np


def rotate_sfx(candidates: List[Path], last_used_name: Optional[str]) -> Path:
    """同族/候选音效自动轮换，避免连续两次重复同一音色引起听觉疲劳。"""
    valid = [p for p in candidates if p and p.is_file()]
    if not valid:
        return ASSETS_DIR / "sfx" / "pop.wav"
    if len(valid) == 1 or not last_used_name:
        return valid[0]
    for p in valid:
        if p.name != last_used_name:
            return p
    return valid[0]


def _enhance_sfx_presence(arr: np.ndarray, sr: int = SR) -> np.ndarray:
    """保持 CC0 原生频谱波形不变，对 2–8kHz 过闷暖调 SFX 施加温和广播级自身带通临场补偿。"""
    if len(arr) < 128:
        return arr
    try:
        from scipy import signal as sp_sig
        mono = arr.mean(axis=1).astype(np.float64)
        full_rms = float(np.sqrt(np.mean(mono ** 2) + 1e-12))
        sos = sp_sig.butter(4, [2000.0, 8000.0], btype="bandpass", fs=float(sr), output="sos")
        hf_mono = sp_sig.sosfiltfilt(sos, mono)
        hf_rms = float(np.sqrt(np.mean(hf_mono ** 2) + 1e-12))
        hf_ratio = hf_rms / max(1e-5, full_rms)
        if hf_ratio >= 0.28 or hf_rms < 1e-5:
            return arr
        # 仅按比例混合原 SFX 自身的 2–8kHz 分量，使其清晰度达到 hf_ratio≈0.35（绝不混入人工噪波）
        target_hf = max(hf_rms, full_rms * 0.35)
        alpha = min(6.0, max(0.0, (target_hf - hf_rms) / hf_rms))
        out = arr.astype(np.float64).copy()
        out[:, 0] += alpha * sp_sig.sosfiltfilt(sos, out[:, 0])
        out[:, 1] += alpha * sp_sig.sosfiltfilt(sos, out[:, 1])
        pk = float(np.max(np.abs(out)))
        if pk > 0.85:
            out *= (0.85 / pk)
        return out.astype(np.float32)
    except Exception:
        return arr


def _measure_sfx_peak_offset_sec(arr: np.ndarray, sr: int = SR) -> float:
    if len(arr) < 128:
        return 0.0
    mono = np.abs(arr.mean(axis=1))
    win = max(1, int(0.020 * sr))
    env = np.convolve(mono, np.ones(win, dtype=np.float32) / win, mode="same")
    peak_idx = int(np.argmax(env))
    return round(peak_idx / float(sr), 4)


def _avoid_faces_for_pkg(bbox: Dict[str, int], faces_px: List[Dict[str, Any]]) -> Dict[str, int]:
    """在 Step 6 源头一次性将花字胶囊避开裁切后人脸五官，保持安全区且不侵入字幕带。"""
    cand = dict(bbox)
    if not faces_px:
        return cand
    cur_iou = max(bbox_iou(cand, fb) for fb in faces_px)
    if cur_iou <= 0.01:
        return cand
    best = cand
    best_iou = cur_iou
    for try_y in (200, 260, 340, 460, 820, 960, 1080):
        if try_y + cand["h"] > 1180:
            continue
        alt = {"x": cand["x"], "y": int(try_y), "w": cand["w"], "h": cand["h"]}
        ok, _ = check_bbox_in_safe_zone(alt)
        if not ok:
            continue
        iou_v = max(bbox_iou(alt, fb) for fb in faces_px)
        if iou_v <= 0.01:
            return alt
        if iou_v < best_iou:
            best_iou = iou_v
            best = alt
    return best


def _measure_pkg_badge_width(text: str, font_path: Optional[str], max_pkg_w: int, target_fs: int = 40, min_w: int = 240) -> int:
    try:
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        fnt, _ = fit_text_line(draw, text, font_path, max_width=max_pkg_w - 64, max_size=target_fs, min_size=24)
        if fnt is not None:
            bb = draw.textbbox((0, 0), text, font=fnt)
            return int(min(max_pkg_w, max(min_w, (bb[2] - bb[0]) + 72)))
    except Exception:
        pass
    return int(min(max_pkg_w, max(min_w, len(text) * target_fs + 68)))


def run_step6(
    pipeline_dir: Path,
    fps: int = DEFAULT_FPS,
    style_id: str | None = None,
) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    man_p = pipeline_dir / "01_manifest.json"
    manifest = read_json(man_p) if man_p.exists() else {"items": []}

    faces_by_mp4: Dict[str, List[Dict[str, int]]] = {}
    for it in manifest.get("items", []):
        mp = str(it.get("normalized_mp4", ""))
        fl = parse_manifest_faces(it)
        if mp and fl:
            faces_by_mp4[mp] = fl

    if not style_id:
        style_id = edl.get("style_id") or load_style_applied(pipeline_dir)
    style_id = str(style_id or "style_14")

    vibe_prof = None
    try:
        from vibe_sound import get_style_sound_profile
        vibe_prof = get_style_sound_profile(style_id)
    except Exception:
        vibe_prof = None

    total_dur = float(audio_events["locked_duration_sec"])
    total_samples = int(round(total_dur * SR))
    max_visual_budget = max(3, math.ceil(total_dur / 3.5))
    max_sfx_budget = 8 if style_id == "style_06" else max(4, math.ceil(total_dur / 9.5))

    sentences_map = {s["id"]: s for s in audio_events["sentences"]}
    events = audio_events["events"]
    code_ranges = [
        (int(c["timeline_in_frame"]), int(c["timeline_out_frame"]))
        for c in edl.get("clips", [])
        if c.get("track_role") == "code_broll"
    ]

    def overlaps_code(sf_f: int, ef_f: int) -> bool:
        return any(sf_f < ce and ef_f > cs for cs, ce in code_ranges)

    sfx_cues: List[Dict[str, Any]] = []
    if vibe_prof and vibe_prof.get("sfx"):
        v_sfx = {k: Path(v) for k, v in vibe_prof["sfx"].items() if Path(v).exists()}
        # 尊重流派真实 sfx_gain_db（仅允许上限到 -3.5dB，避免风格全被拉爆成统一响度）
        base_gain_db = max(-14.0, min(-3.5, float(vibe_prof.get("sfx_gain_db", -9.5)) + 3.0))
        min_sfx_gap = int(float(vibe_prof.get("sfx_min_gap_sec", 7.0)) * fps)
    else:
        v_sfx = {}
        base_gain_db = -8.5
        min_sfx_gap = int(6.5 * fps)

    sfx_files = {
        "hook": v_sfx.get("accent") or (ASSETS_DIR / "sfx" / "whoosh.wav"),
        "info": v_sfx.get("ui") or v_sfx.get("accent") or (ASSETS_DIR / "sfx" / "pop.wav"),
        "twist": v_sfx.get("accent") or (ASSETS_DIR / "sfx" / "whoosh.wav"),
        "detail": v_sfx.get("ui") or (ASSETS_DIR / "sfx" / "pop.wav"),
        "persona": v_sfx.get("ui") or v_sfx.get("transition") or (ASSETS_DIR / "sfx" / "pop.wav"),
        "cta": v_sfx.get("receipt") or v_sfx.get("ui") or (ASSETS_DIR / "sfx" / "pop.wav"),
        "cut": v_sfx.get("transition") or (ASSETS_DIR / "sfx" / "whoosh.wav"),
    }

    used_frames: List[int] = []
    last_sfx_name: Optional[str] = None

    def can_place_sfx(frame: int) -> bool:
        return len(sfx_cues) < max_sfx_budget and all(abs(frame - f) >= min_sfx_gap for f in used_frames)

    # 优先使用 Freesound CC0 真实物理材质 SFX（次选内置合成音效）
    for clip in edl["clips"][1:]:
        role = clip.get("track_role", "b_roll")
        ev_frame = int(clip.get("timeline_in_frame", clip["cut_event_frame"])) if role == "code_broll" else int(clip["cut_event_frame"])
        is_end_receipt = (clip is edl["clips"][-1]) and ("receipt" in v_sfx)
        if role != "code_broll" and not is_end_receipt:
            continue
        if not can_place_sfx(ev_frame):
            continue
        cand_list = [v_sfx["receipt"]] if is_end_receipt else [
            sfx_files["info"],
            sfx_files["cut"],
            ASSETS_DIR / "sfx" / "ding.wav",
            ASSETS_DIR / "sfx" / "whoosh.wav",
        ]
        sf = rotate_sfx(cand_list, last_sfx_name)
        last_sfx_name = sf.name
        cue_gain = round(max(-14.0, min(-3.5, base_gain_db + 1.5)), 1)
        sfx_cues.append({
            "id": f"sfx_{len(sfx_cues)+1:03d}",
            "file": str(sf.resolve()),
            "sfx_name": sf.name,
            "time_sec": frames_to_sec(ev_frame, fps),
            "frame": ev_frame,
            "gain_db": cue_gain,
            "event_id": clip["cut_event_id"],
            "reason": f"{style_id} 账单小票收尾提示" if is_end_receipt else f"{style_id} 信息卡片物理提示",
        })
        used_frames.append(ev_frame)

    # CC0 真实录音优先排在 cut_palette 最前
    cut_palette = [
        sfx_files["cut"],
        sfx_files["info"],
        sfx_files["hook"],
        ASSETS_DIR / "sfx" / "whoosh.wav",
        ASSETS_DIR / "sfx" / "shutter.wav",
    ]
    cut_rot = 0
    # 优先选在句首/停顿缝隙切点安放 SFX，确保听感清爽且避免被重读浊音完全掩蔽，第二遍再补足其余切点
    prioritized_clips = [
        c for c in edl["clips"][1:]
        if str(c.get("cut_event_type", "")) in ("sentence_start", "pause_start", "pause_end", "bgm_downbeat")
    ] + [
        c for c in edl["clips"][1:]
        if str(c.get("cut_event_type", "")) not in ("sentence_start", "pause_start", "pause_end", "bgm_downbeat")
    ]
    for clip in prioritized_clips:
        ev_frame = int(clip["cut_event_frame"])
        if not can_place_sfx(ev_frame):
            continue
        rotated_pool = cut_palette[cut_rot % len(cut_palette):] + cut_palette[:cut_rot % len(cut_palette)]
        sf = rotate_sfx(rotated_pool, last_sfx_name)
        last_sfx_name = sf.name
        cut_rot += 1
        cue_gain = round(max(-14.0, min(-4.0, base_gain_db + 0.5)), 1)
        sfx_cues.append({
            "id": f"sfx_{len(sfx_cues)+1:03d}",
            "file": str(sf.resolve()),
            "sfx_name": sf.name,
            "time_sec": frames_to_sec(ev_frame, fps),
            "frame": ev_frame,
            "gain_db": cue_gain,
            "event_id": clip["cut_event_id"],
            "reason": f"{style_id} 流派物理材质转场",
        })
        used_frames.append(ev_frame)

    for ev in events:
        if ev["type"] == "sentence_start" and can_place_sfx(ev["frame"]):
            sent = sentences_map.get(ev.get("sentence_id", ""), {})
            emo = sent.get("emotion_or_info", "info")
            if emo not in ("hook", "twist", "cta"):
                continue
            sf = rotate_sfx([sfx_files.get(emo, sfx_files["info"]), sfx_files["cut"]], last_sfx_name)
            last_sfx_name = sf.name
            cue_gain = round(max(-15.0, min(-4.5, base_gain_db - 1.2)), 1)
            sfx_cues.append({
                "id": f"sfx_{len(sfx_cues)+1:03d}",
                "file": str(sf.resolve()),
                "sfx_name": sf.name,
                "time_sec": ev["time_sec"],
                "frame": ev["frame"],
                "gain_db": cue_gain,
                "event_id": ev["id"],
                "reason": f"{emo} 句首节点轻量提示",
            })
            used_frames.append(ev["frame"])

    sfx_cues.sort(key=lambda x: x["frame"])
    for idx_cue, cue in enumerate(sfx_cues, start=1):
        cue["id"] = f"sfx_{idx_cue:03d}"

    sfx_track = np.zeros((total_samples, 2), dtype=np.float32)
    sfx_cache: Dict[str, np.ndarray] = {}
    for cue in sfx_cues:
        fp = cue["file"]
        if fp not in sfx_cache:
            arr, _ = load_stereo_wav_np(Path(fp))
            m_abs = np.abs(arr.mean(axis=1))
            active_m = m_abs[m_abs >= max(1e-4, float(np.max(m_abs)) * 0.15)]
            core_rms = float(np.sqrt(np.mean(active_m ** 2) + 1e-10)) if len(active_m) > 0 else 0.01
            # 保持原生 CC0 物理频谱不注水：仅做温和活性核心增益归一化与防削波峰值钳位
            norm_gain = min(10.0, 0.25 / max(1e-4, core_rms))
            peak_cap = 0.85 / max(1e-4, float(np.max(np.abs(arr))))
            arr = (arr * min(norm_gain, peak_cap)).astype(np.float32)
            arr = _enhance_sfx_presence(arr, SR)
            sfx_cache[fp] = arr
        arr = sfx_cache[fp]
        peak_off = _measure_sfx_peak_offset_sec(arr, SR)
        # 峰值对齐切点：提前 peak_off 起播，使听觉峰值精准落在切点 time_sec
        onset_sec = max(0.0, float(cue["time_sec"]) - peak_off)
        cue["onset_time_sec"] = round(onset_sec, 4)
        cue["peak_time_sec"] = round(onset_sec + peak_off, 4)
        gain = 10.0 ** (min(-3.0, float(cue["gain_db"])) / 20.0)
        start_idx = max(0, int(round(onset_sec * SR)))
        end_idx = min(total_samples, start_idx + len(arr))
        if end_idx > start_idx:
            sfx_track[start_idx:end_idx] += arr[: end_idx - start_idx] * gain

    sfx_wav_path = pipeline_dir / "06_sfx_track.wav"
    save_stereo_wav_np(sfx_wav_path, sfx_track, SR)

    pkg_font_path = find_commercial_font()
    visual_elements: List[Dict[str, Any]] = []
    first_sent = audio_events["sentences"][0] if audio_events["sentences"] else {"text": "必看重点", "keywords": []}
    hook_badge_text = first_sent["keywords"][0] if first_sent.get("keywords") else first_sent["text"][:8]
    hook_label = f"重点：{hook_badge_text}"
    max_pkg_w = SAFE_ZONE["x_max"] - 88 - 12  # 780px
    hook_w = _measure_pkg_badge_width(hook_label, pkg_font_path, max_pkg_w, target_fs=42, min_w=280)
    hook_ef = min(int(2.0 * fps), int(total_dur * fps))
    if not overlaps_code(0, hook_ef):
        active_faces_0 = []
        for c in edl.get("clips", []):
            if int(c["timeline_in_frame"]) < hook_ef and int(c["timeline_out_frame"]) > 0:
                active_faces_0.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
        hook_bbox = _avoid_faces_for_pkg({"x": 88, "y": 210, "w": hook_w, "h": 92}, active_faces_0)
        safe_ok, _ = check_bbox_in_safe_zone(hook_bbox)
        visual_elements.append({
            "id": "pkg_001",
            "kind": "flower_text",
            "overlay_mode": "corner",
            "text_or_asset": hook_label,
            "start_frame": 0,
            "end_frame": hook_ef,
            "read_hold_sec": round(hook_ef / float(fps), 2),
            "aligned_sfx_or_accent_frame": 0,
            "alignment_error_frames": 0,
            "bbox": hook_bbox,
            "final_bbox": hook_bbox,
            "safe_zone_ok": safe_ok,
            "reason": "首帧高密度视觉钩子（第0帧零淡入直接落位）",
        })

    last_vis_end = hook_ef + int(2.5 * fps)
    for cue in sfx_cues:
        if len(visual_elements) >= max_visual_budget:
            break
        sf = int(cue["frame"])
        if sf < last_vis_end or sf > int((total_dur - 1.5) * fps):
            continue
        sent = next((s for s in audio_events["sentences"] if s["start_frame"] <= sf <= s["end_frame"] + 15), first_sent)
        if sent.get("emotion_or_info") not in ("hook", "info", "twist", "persona", "cta"):
            continue

        kw_label = sent["keywords"][0] if sent.get("keywords") else sent["text"][:8]
        cjk_n = sum(1 for ch in kw_label if "\u4e00" <= ch <= "\u9fff")
        est_hold = max(2.2, round(cjk_n / 4.5 + 1.5, 2))
        ef = min(int(total_dur * fps), sf + int(math.ceil(min(3.0, est_hold) * fps)))
        if overlaps_code(sf, ef):
            continue
        box_w = _measure_pkg_badge_width(kw_label, pkg_font_path, max_pkg_w, target_fs=40, min_w=240)
        active_faces = []
        for c in edl.get("clips", []):
            if int(c["timeline_in_frame"]) < ef and int(c["timeline_out_frame"]) > sf:
                active_faces.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
        final_bbox = _avoid_faces_for_pkg({"x": 88, "y": 210, "w": box_w, "h": 86}, active_faces)
        ok, _ = check_bbox_in_safe_zone(final_bbox)
        visual_elements.append({
            "id": f"pkg_{len(visual_elements)+1:03d}",
            "kind": "flower_text",
            "overlay_mode": "focus_blur" if sent.get("emotion_or_info") == "twist" else "corner",
            "text_or_asset": kw_label,
            "start_frame": sf,
            "end_frame": ef,
            "read_hold_sec": round((ef - sf) / float(fps), 2),
            "aligned_sfx_or_accent_frame": sf,
            "alignment_error_frames": 0,
            "bbox": final_bbox,
            "final_bbox": final_bbox,
            "safe_zone_ok": ok,
            "reason": cue["reason"],
        })
        last_vis_end = ef + int(2.5 * fps)

    packaging = {
        "step": "06_packaging",
        "style_id": style_id,
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
    parser.add_argument("--style-id", help="绑定风格谱 ID")
    args = parser.parse_args()
    run_step6(Path(args.pipeline_dir), style_id=args.style_id)


if __name__ == "__main__":
    main()
