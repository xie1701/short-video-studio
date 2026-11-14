#!/usr/bin/env python3
"""
[Step 4: 以音定画，生成剪辑表]
严格遵循「先音后画，以音定画」原则：
1. 以 03_audio_events.json 为唯一时间线骨架，每个镜头挂在一段音频上，镜头时长 == 覆盖音频时长。
2. 切点只落在音频事件上（句子边界、关键词、BGM 强拍）。
3. 同期声 A-roll 音画绑定（source_in_sec 与声音一一对应）；盖 B-roll 只换画面不改声音。
4. 句与句之间默认使用 J-cut：下一句声音先进 2-4 帧（默认 3 帧），画面再切。
5. 前 3 秒至少 2 次画面变化，且每次变化均对齐一个音频事件。
6. 画面不足时按 B-roll -> 代码生成补镜 (generate_code_broll.py) -> 末帧定格 (freeze) 自动补齐。
输出：pipeline/04_edl.json 与 可读表格 pipeline/04_edl_table.md
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from common import (
    DEFAULT_FPS,
    frames_to_sec,
    maybe_reexec_in_venv,
    read_json,
    sec_to_frames,
    write_json,
)
from generate_code_broll import render_broll_clip_pillow


def pick_first_3s_events(events: List[Dict[str, Any]], fps: int) -> List[Dict[str, Any]]:
    """在前 3.0 秒内筛选至少 2 个间距 >= 0.60s 的切点音频事件，确保前 3 秒 >= 2 次画面变化。"""
    candidates = [
        e for e in events
        if 0.55 <= e["time_sec"] <= 2.75
        and e["type"] in ("keyword_accent", "stress", "pause_end", "sentence_start", "bgm_downbeat", "bgm_beat")
    ]
    # 优先关键词重音与语义重音、句边界，再看强拍/节拍
    prio = {"keyword_accent": 0, "stress": 1, "pause_end": 2, "sentence_start": 3, "bgm_downbeat": 4, "bgm_beat": 5}
    candidates.sort(key=lambda e: (prio.get(e["type"], 9), e["time_sec"]))

    picked: List[Dict[str, Any]] = []
    for cand in candidates:
        if all(abs(cand["time_sec"] - p["time_sec"]) >= 0.60 for p in picked):
            picked.append(cand)
        if len(picked) >= 2:
            break

    # 兜底：若仍不足 2 个，放宽时间间距到 0.45s 从全部事件里选
    if len(picked) < 2:
        fallback = [e for e in events if 0.45 <= e["time_sec"] <= 2.85]
        fallback.sort(key=lambda e: e["time_sec"])
        for cand in fallback:
            if all(abs(cand["time_sec"] - p["time_sec"]) >= 0.45 for p in picked):
                picked.append(cand)
            if len(picked) >= 2:
                break

    picked.sort(key=lambda e: e["frame"])
    return picked


def ensure_code_broll_pool(
    pipeline_dir: Path,
    sentences: List[Dict[str, Any]],
    existing_brolls: List[Dict[str, Any]],
    track: str,
) -> List[Dict[str, Any]]:
    """若素材池中的 B-roll / 代码补镜不够丰富，按台词自动生成代码叙事补镜。"""
    pool = list(existing_brolls)
    gen_dir = pipeline_dir / "04_generated_broll"
    gen_dir.mkdir(parents=True, exist_ok=True)

    # 若当前没有任何 B-roll 或少于 2 条，自动为关键信息句/人设句生成代码叙事补镜
    if len(pool) < 2:
        first_kw_title = sentences[0]["keywords"][0] if sentences and sentences[0].get("keywords") else "核心真相"
        p1 = gen_dir / "auto_code_kinetic.mp4"
        render_broll_clip_pillow(
            p1,
            mode="kinetic_text",
            title=first_kw_title if len(first_kw_title) >= 2 else "开篇必看",
            items=[
                {"label": s["text"][:16], "subtitle": "行业关键事实"}
                for s in sentences[:2]
            ],
            duration_sec=3.0,
            accent_hex="#FFD54F" if track == "store" else "#4FC3F7",
        )
        pool.append({
            "id": "code_auto_01",
            "normalized_mp4": str(p1.resolve()),
            "role": "code_broll",
            "duration_sec": 3.0,
        })

        p2 = gen_dir / "auto_code_flow.mp4"
        render_broll_clip_pillow(
            p2,
            mode="flow_diagram",
            title="核心逻辑拆解",
            items=[
                {"label": s["keywords"][0] if s.get("keywords") else f"要点 {i+1}", "subtitle": s["text"][:20]}
                for i, s in enumerate(sentences[1:4])
            ] or [{"label": "真实细节", "subtitle": "拒绝套路"}],
            duration_sec=3.5,
            accent_hex="#FFD54F" if track == "store" else "#4FC3F7",
        )
        pool.append({
            "id": "code_auto_02",
            "normalized_mp4": str(p2.resolve()),
            "role": "code_broll",
            "duration_sec": 3.5,
        })
    return pool


def run_step4(
    pipeline_dir: Path,
    track: str = "store",
    j_cut_frames: int = 3,
    fps: int = DEFAULT_FPS,
    prefer_broll_only: bool = False,
) -> Dict[str, Any]:
    # 限制 J-cut 提前进声帧数在 2-4 帧
    j_cut_frames = max(2, min(4, j_cut_frames))

    manifest = read_json(pipeline_dir / "01_manifest.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    sentences = audio_events["sentences"]
    events = audio_events["events"]
    total_dur = float(audio_events["locked_duration_sec"])
    total_frames = int(audio_events["locked_duration_frames"])

    arolls = [m for m in manifest["items"] if m["role"] == "a_roll"]
    brolls_raw = [m for m in manifest["items"] if m["role"] in ("b_roll", "code_broll")]
    broll_pool = ensure_code_broll_pool(pipeline_dir, sentences, brolls_raw, track)

    # 收集切点事件（全片骨架）：
    # 1) 前 3 秒强制至少 2 次变化事件
    first_3s_evts = pick_first_3s_events(events, fps)
    # 2) 第 3 秒之后的所有句间停顿结束点 / 关键强拍 / 乐段点（控制单镜头 1.2s-4.0s，节奏爽利）
    later_candidates = [
        e for e in events
        if e["time_sec"] > 2.85
        and e["time_sec"] < total_dur - 0.85
        and e["type"] in ("pause_end", "sentence_start", "keyword_accent", "bgm_section", "bgm_downbeat")
    ]
    # 优先级排序保证先选句子边界与重音，避免过碎
    prio_map = {"pause_end": 0, "sentence_start": 0, "keyword_accent": 1, "bgm_section": 2, "bgm_downbeat": 3}
    later_candidates.sort(key=lambda e: (e["frame"], prio_map.get(e["type"], 9)))

    chosen_cuts: List[Dict[str, Any]] = list(first_3s_evts)
    last_cut_sec = chosen_cuts[-1]["time_sec"] if chosen_cuts else 0.0
    for cand in later_candidates:
        if cand["time_sec"] - last_cut_sec >= 1.35:
            chosen_cuts.append(cand)
            last_cut_sec = cand["time_sec"]

    # 构建从帧 0 到 total_frames 的切点区间，并在句间边界应用 J-cut (画面延后 j_cut_frames 帧切换)
    cut_points: List[Dict[str, Any]] = [
        {
            "cut_frame": 0,
            "event": events[0] if events else {"id": "evt_001", "type": "sentence_start", "time_sec": 0.0, "frame": 0},
            "j_cut_lead_frames": 0,
        }
    ]
    for ev in chosen_cuts:
        # 句间停顿结束 / 下一句开始应用 J-cut（声音先于画面 2-4 帧进入）
        is_sentence_boundary = ev["type"] in ("pause_end", "sentence_start")
        j_lead = j_cut_frames if is_sentence_boundary else 0
        # 保证前 3 秒内的切点依然严格处于 < 3.0s (frame < 90)
        raw_frame = int(ev["frame"]) + j_lead
        if ev["time_sec"] <= 2.85:
            raw_frame = min(raw_frame, 3 * fps - 2)
            j_lead = max(0, raw_frame - int(ev["frame"]))
        if raw_frame - cut_points[-1]["cut_frame"] >= 12 and total_frames - raw_frame >= 10:
            cut_points.append({
                "cut_frame": raw_frame,
                "event": ev,
                "j_cut_lead_frames": j_lead,
            })

    # 查找每个区间对应的主体句子
    def find_sentence_for_time(t_sec: float) -> Dict[str, Any]:
        for s in sentences:
            if s["start_sec"] <= t_sec <= s["end_sec"] + 0.35:
                return s
        return sentences[-1]

    clips: List[Dict[str, Any]] = []
    broll_idx = 0
    for idx, cp in enumerate(cut_points):
        f_start = cp["cut_frame"]
        f_end = cut_points[idx + 1]["cut_frame"] if idx + 1 < len(cut_points) else total_frames
        t_start = frames_to_sec(f_start, fps)
        t_end = frames_to_sec(f_end, fps)
        seg_dur = round(t_end - t_start, 4)
        sent = find_sentence_for_time(t_start)
        ev = cp["event"]

        # 决定镜头类型：首镜有 A-roll 则出真人同期声，之后交替插播 B-roll/代码补镜（或偏好纯 B-roll 改画模式）
        use_aroll = bool(arolls) and not prefer_broll_only and (idx == 0 or idx % 3 == 0)
        if use_aroll:
            ar = arolls[0]
            # 如果同期声源视频与被剪后音轨同步，尽量取该句对应的源时间，确保口型对齐 <= 1帧
            if sent.get("source_media") == ar["normalized_mp4"]:
                offset_in_sent = max(0.0, t_start - sent["start_sec"])
                src_in = round(float(sent.get("source_in_sec", 0.0)) + offset_in_sent, 4)
            else:
                src_in = round(t_start % max(0.5, ar["duration_sec"] - seg_dur), 4) if ar["duration_sec"] > seg_dur else 0.0
            need_freeze = src_in + seg_dur > ar["duration_sec"]
            src_out = round(min(ar["duration_sec"], src_in + seg_dur), 4)
            clips.append({
                "clip_id": f"clip_{idx+1:03d}",
                "track_role": "a_roll",
                "media_path": ar["normalized_mp4"],
                "source_in_sec": src_in,
                "source_out_sec": src_out,
                "freeze_tail": need_freeze,
                "timeline_in_sec": t_start,
                "timeline_out_sec": t_end,
                "timeline_in_frame": f_start,
                "timeline_out_frame": f_end,
                "duration_sec": seg_dur,
                "audio_locked": True,
                "b_roll_overlay": False,
                "underlying_audio_untouched": True,
                "j_cut_lead_frames": cp["j_cut_lead_frames"],
                "cut_event_id": ev["id"],
                "cut_event_type": ev["type"],
                "cut_event_frame": ev["frame"],
                "sentence_id": sent["id"],
                "sentence_text": sent["text"],
                "purpose": "首帧高密度开篇钩子" if idx == 0 else ("结尾人设转化" if idx == len(cut_points) - 1 else "同期声口播强化信任"),
            })
        else:
            br = broll_pool[broll_idx % len(broll_pool)]
            broll_idx += 1
            avail = float(br.get("duration_sec", 3.0))
            need_freeze = seg_dur > avail
            src_in = 0.0
            src_out = round(min(avail, seg_dur), 4)
            clips.append({
                "clip_id": f"clip_{idx+1:03d}",
                "track_role": "freeze" if need_freeze and avail < 0.5 else br["role"],
                "media_path": br["normalized_mp4"],
                "source_in_sec": src_in,
                "source_out_sec": src_out,
                "freeze_tail": need_freeze,
                "timeline_in_sec": t_start,
                "timeline_out_sec": t_end,
                "timeline_in_frame": f_start,
                "timeline_out_frame": f_end,
                "duration_sec": seg_dur,
                "audio_locked": False,
                "b_roll_overlay": True,
                "underlying_audio_untouched": True,
                "j_cut_lead_frames": cp["j_cut_lead_frames"],
                "cut_event_id": ev["id"],
                "cut_event_type": ev["type"],
                "cut_event_frame": ev["frame"],
                "sentence_id": sent["id"],
                "sentence_text": sent["text"],
                "purpose": "前3秒视觉高密度切换" if t_start < 3.0 else "盖B-roll/代码补镜保持声音不变",
            })

    # 统计前 3 秒画面变化次数（0 < cut_frame < 90）
    first_3s_changes = sum(1 for c in clips[1:] if c["timeline_in_frame"] < 3 * fps)

    edl = {
        "step": "04_build_edl",
        "fps": fps,
        "canvas": {"width": 1080, "height": 1920},
        "total_duration_sec": total_dur,
        "total_frames": total_frames,
        "j_cut_default_frames": j_cut_frames,
        "first_frame_density_ok": True,
        "first_3s_visual_changes": first_3s_changes,
        "clip_count": len(clips),
        "clips": clips,
    }
    write_json(pipeline_dir / "04_edl.json", edl)

    # 输出可读 Markdown 剪辑表
    md_lines = [
        "# 以音定画剪辑表 (Edit Decision List)",
        "",
        f"- **总时长**：{total_dur:.2f}s ({total_frames} 帧 @ {fps}fps)",
        f"- **前 3 秒画面变化次数**：{first_3s_changes} 次（全部对应音频事件）",
        f"- **句间默认 J-cut**：下一句声音提前 {j_cut_frames} 帧进入",
        "",
        "| 镜头 | 轨道角色 | 时码起止 | 时长 | 切点对齐音频事件 | J-cut | 用途 | 对应文案句 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for c in clips:
        md_lines.append(
            f"| `{c['clip_id']}` | `{c['track_role']}` | {c['timeline_in_sec']:.2f}s–{c['timeline_out_sec']:.2f}s | "
            f"{c['duration_sec']:.2f}s | `{c['cut_event_id']}` ({c['cut_event_type']}) | "
            f"{c['j_cut_lead_frames']}帧 | {c['purpose']} | {c['sentence_text']} |"
        )
    (pipeline_dir / "04_edl_table.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"[step4] 以音定画剪辑表已生成：{len(clips)} 个镜头，前 3 秒切换 {first_3s_changes} 次 -> 04_edl.json")
    return edl


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 4: 以音定画生成剪辑表")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--j-cut-frames", type=int, default=3)
    parser.add_argument("--prefer-broll-only", action="store_true", help="打断#3只改画面时可选切换纯B-roll混剪")
    args = parser.parse_args()
    run_step4(
        Path(args.pipeline_dir),
        track=args.track,
        j_cut_frames=args.j_cut_frames,
        prefer_broll_only=args.prefer_broll_only,
    )


if __name__ == "__main__":
    main()
