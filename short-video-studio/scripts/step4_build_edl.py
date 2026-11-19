#!/usr/bin/env python3
"""
[Step 4: 以音定画，生成专业剪辑表 (EDL)]
1. 语义互文 + 多模态标签 + 连戏 + 去重选镜，并落实 0.40s Handles 掐头去尾 (A1) 与保声遮脸 (A2)。
2. 修复 PROP_SHOW_CUES 排序置顶吞噬前序切点 Bug：严格按时间线性推进切点，仅在局部 ±0.85s 窗内加权展示句。
3. 真·J-cut 支持 0 帧慢镜直切与 2–4 帧声先画后；支持 account_profile.sample_sufficient 时消费 target_aroll_ratio。
4. 精确计算 first_frame_density_ok，写入 style_id，并按 1080x1920 映射后的 eye_anchor 施加 1.09x 锁眼推拉。
输出：pipeline/04_edl.json 与 pipeline/04_edl_table.md
"""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    frames_to_sec,
    load_style_applied,
    maybe_reexec_in_venv,
    read_json,
    write_json,
)
from generate_code_broll import render_broll_clip_pillow, resolve_style_palette

PROP_SHOW_CUES = ("睇下", "看下", "看一下", "给大家看", "给你看", "看我手上", "看我手里", "效果图", "实物", "清单", "明细")
DEICTIC_CUES = ("这张图", "四张图", "两张图", "三张图", "效果图", "平面图", "明细表", "账单", "发票", "检测单", "看这里", "看这个", "你看这", "对比一下")
POSITIVE_PRAISE_CUES = ("招牌", "正宗", "干净", "舒服", "鲜嫩", "多汁", "放心", "透明", "美味", "好吃", "靓", "靠谱", "新鲜", "卫生", "环境好")


def pick_first_3s_events(events: List[Dict[str, Any]], fps: int) -> List[Dict[str, Any]]:
    """在前 3.0 秒内筛选至少 2 个间距 >= 0.55s 的切点音频事件，确保前 3 秒 >= 2 次画面变化。"""
    candidates = [
        e for e in events
        if 0.50 <= e["time_sec"] <= 2.75
        and e["type"] in ("keyword_accent", "stress", "pause_end", "sentence_start", "bgm_downbeat", "bgm_beat")
    ]
    prio = {"sentence_start": 0, "pause_end": 1, "keyword_accent": 2, "stress": 3, "bgm_downbeat": 4, "bgm_beat": 5}
    candidates.sort(key=lambda e: (prio.get(e["type"], 9), e["time_sec"]))

    picked: List[Dict[str, Any]] = []
    for cand in candidates:
        if all(abs(cand["time_sec"] - p["time_sec"]) >= 0.55 for p in picked):
            picked.append(cand)
        if len(picked) >= 2:
            break

    if len(picked) < 2:
        fallback = [e for e in events if 0.40 <= e["time_sec"] <= 2.85]
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
    style_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """若素材池中的 B-roll 少于 2 条，按当前风格谱与台词自动生成风格化叙事补镜。"""
    pool = list(existing_brolls)
    gen_dir = pipeline_dir / "04_generated_broll"
    gen_dir.mkdir(parents=True, exist_ok=True)
    pal = resolve_style_palette(style_id, track=track)

    if len(pool) < 2:
        first_kw_title = sentences[0]["keywords"][0] if sentences and sentences[0].get("keywords") else "核心真相"
        p1 = gen_dir / "auto_code_kinetic.mp4"
        render_broll_clip_pillow(
            p1,
            mode="kinetic_text",
            title=first_kw_title if len(first_kw_title) >= 2 else "开篇必看",
            items=[
                {"label": s["text"][:16], "subtitle": "真实数据与现场拆解"}
                for s in sentences[:2]
            ],
            duration_sec=3.0,
            accent_hex=pal["accent_hex"],
            bg_hex=pal["bg_hex"],
            style_id=style_id,
            track=track,
        )
        pool.append({
            "id": "code_auto_01",
            "normalized_mp4": str(p1.resolve()),
            "role": "code_broll",
            "duration_sec": 3.0,
            "best_in_sec": 0.0,
            "semantic_tags": ["观点", "关键事实", *(sentences[0].get("keywords", []) if sentences else [])],
            "scene_group": "code_gfx",
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
            accent_hex=pal["accent_hex"],
            bg_hex=pal["bg_hex"],
            style_id=style_id,
            track=track,
        )
        pool.append({
            "id": "code_auto_02",
            "normalized_mp4": str(p2.resolve()),
            "role": "code_broll",
            "duration_sec": 3.5,
            "best_in_sec": 0.0,
            "semantic_tags": ["流程", "拆解", "细节"],
            "scene_group": "code_gfx",
        })
    return pool


def _build_eye_locked_vf(eye_anchor: Optional[Dict[str, Any]], zoom: float = 1.09) -> str:
    cx = float((eye_anchor or {}).get("cx", (eye_anchor or {}).get("eye_cx", 0.50)))
    cy = float((eye_anchor or {}).get("cy", (eye_anchor or {}).get("eye_cy", 0.36)))
    zw = (int(round(CANVAS_WIDTH * zoom)) // 2) * 2
    zh = (int(round(CANVAS_HEIGHT * zoom)) // 2) * 2
    crop_x = max(0, min(zw - CANVAS_WIDTH, int(round(cx * (zw - CANVAS_WIDTH)))))
    crop_y = max(0, min(zh - CANVAS_HEIGHT, int(round(cy * (zh - CANVAS_HEIGHT)))))
    return f"scale={zw}:{zh}:flags=lanczos,crop={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{crop_x}:{crop_y}"


def run_step4(
    pipeline_dir: Path,
    track: str = "store",
    j_cut_frames: int = 3,
    fps: int = DEFAULT_FPS,
    prefer_broll_only: bool = False,
    style_id: Optional[str] = None,
    account_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw_jc = int(j_cut_frames)
    j_cut_frames = 0 if raw_jc <= 0 else max(2, min(4, raw_jc))
    if not style_id:
        style_id = load_style_applied(pipeline_dir)

    manifest = read_json(pipeline_dir / "01_manifest.json")
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    sentences = audio_events["sentences"]
    events = audio_events["events"]
    total_dur = float(audio_events["locked_duration_sec"])
    total_frames = int(audio_events["locked_duration_frames"])

    arolls = [m for m in manifest["items"] if m["role"] == "a_roll" and m.get("video_grade", "usable") != "ng"]
    usable_arolls = [m for m in arolls if m.get("video_grade", "usable") == "usable"]
    vgrade_by_mp4 = {str(m.get("normalized_mp4")): str(m.get("video_grade", "usable")) for m in manifest["items"]}
    brolls_raw = [m for m in manifest["items"] if m["role"] in ("b_roll", "code_broll") and m.get("video_grade", "usable") != "ng"]
    broll_pool = ensure_code_broll_pool(pipeline_dir, sentences, brolls_raw, track, style_id=style_id)

    # A3 视觉指代词守卫
    deictic_gaps: List[Dict[str, Any]] = []
    for sent in sentences:
        s_txt = str(sent.get("text", ""))
        matched_cue = next((c for c in DEICTIC_CUES if c in s_txt), None)
        if not matched_cue:
            continue
        cue_tokens = [matched_cue]
        for tk in ("图", "效果", "平面", "账单", "明细", "发票", "检测", "对比"):
            if tk in matched_cue:
                cue_tokens.append(tk)
        has_visual_match = False
        for m in broll_pool:
            tags = " ".join(str(t) for t in (m.get("semantic_tags", []) + m.get("manual_tags", []))) + " " + Path(str(m.get("normalized_mp4", ""))).name
            if any(tk in tags for tk in cue_tokens):
                has_visual_match = True
                break
        if not has_visual_match:
            deictic_gaps.append({
                "sentence_id": sent["id"],
                "matched_cue": matched_cue,
                "text": s_txt,
                "suggestion": f"台词含视觉指代「{matched_cue}」，但 B-roll 标签库未见匹配素材，建议补拍对应特写或通过 00_visual_index.json 标注",
            })

    eff_prof = account_profile if (account_profile and account_profile.get("sample_sufficient") is True) else {}
    min_shot_gap = max(0.95, min(2.4, float(eff_prof.get("target_median_shot_sec", 1.55)) * 0.85))
    micro_hook_max_sec = float(eff_prof.get("micro_hook_max_sec", 6.2))
    target_aroll_ratio = max(0.20, min(0.85, float(eff_prof.get("target_aroll_ratio", 0.48))))

    first_3s_evts = pick_first_3s_events(events, fps)
    sent_by_id = {s["id"]: s for s in sentences}
    prop_sent_ids = {
        s["id"] for s in sentences if any(cue in s.get("text", "") for cue in PROP_SHOW_CUES)
    }

    later_candidates = [
        e for e in events
        if e["time_sec"] > 2.85
        and e["time_sec"] < total_dur - 0.80
        and e["type"] in ("pause_start", "sentence_start", "pause_end", "keyword_accent", "stress", "bgm_section", "bgm_downbeat")
    ]
    prio_map = {
        "sentence_start": 0,
        "pause_end": 1,
        "pause_start": 2,
        "keyword_accent": 3,
        "stress": 4,
        "bgm_section": 5,
        "bgm_downbeat": 6,
    }
    # 严格按时间 frame 升序排序（同帧优先展示句与高级事件），严禁跨整条片全局置顶后面切点
    later_candidates.sort(key=lambda e: (e["frame"], 0 if (e.get("type") == "sentence_start" and e.get("sentence_id") in prop_sent_ids) else 1, prio_map.get(e["type"], 9)))

    chosen_cuts: List[Dict[str, Any]] = list(first_3s_evts)
    last_cut_sec = chosen_cuts[-1]["time_sec"] if chosen_cuts else 0.0
    n_cand = len(later_candidates)
    skip_until_idx = -1
    for ci, cand in enumerate(later_candidates):
        if ci < skip_until_idx:
            continue
        # 若 0.90s 局部窗口内存在 sentence_start（优先展示句），提前让位避免被停顿/重音事件抢占吞噬
        eff_cand = cand
        if cand.get("type") != "sentence_start":
            best_nxt: Optional[Dict[str, Any]] = None
            best_idx = -1
            for look_j in range(ci + 1, n_cand):
                nxt = later_candidates[look_j]
                if nxt["time_sec"] - cand["time_sec"] > 0.90:
                    break
                if nxt.get("type") != "sentence_start":
                    continue
                if nxt.get("sentence_id") in prop_sent_ids:
                    best_nxt = nxt
                    best_idx = look_j
                    break
                if best_nxt is None and (nxt["time_sec"] - last_cut_sec <= min(micro_hook_max_sec, 4.5)):
                    best_nxt = nxt
                    best_idx = look_j
            if best_nxt is not None:
                eff_cand = best_nxt
                skip_until_idx = best_idx + 1

        is_sent_start = eff_cand.get("type") == "sentence_start"
        gap_needed = 0.85 if is_sent_start else min_shot_gap
        if eff_cand["time_sec"] - last_cut_sec >= gap_needed:
            prev_ev = chosen_cuts[-1] if chosen_cuts else None
            if prev_ev and prev_ev.get("sentence_id") in prop_sent_ids and eff_cand.get("sentence_id") == prev_ev.get("sentence_id"):
                continue
            chosen_cuts.append(eff_cand)
            last_cut_sec = eff_cand["time_sec"]

    # 微观留存模式中断守护
    if chosen_cuts:
        guarded_cuts: List[Dict[str, Any]] = []
        prev_t = 0.0
        for ev in chosen_cuts + [{"time_sec": total_dur, "frame": total_frames, "type": "_end"}]:
            while ev["time_sec"] - prev_t > micro_hook_max_sec:
                mid_ev = next(
                    (
                        c for c in events
                        if prev_t + 1.5 <= c["time_sec"] <= min(ev["time_sec"] - 1.2, prev_t + micro_hook_max_sec)
                        and c["type"] in ("keyword_accent", "stress", "pause_start", "sentence_start", "bgm_downbeat", "bgm_beat")
                    ),
                    None,
                )
                if mid_ev is None:
                    break
                guarded_cuts.append(mid_ev)
                prev_t = mid_ev["time_sec"]
            if ev["type"] != "_end":
                guarded_cuts.append(ev)
                prev_t = ev["time_sec"]
        chosen_cuts = guarded_cuts

    cut_points: List[Dict[str, Any]] = [
        {
            "cut_frame": 0,
            "event": events[0] if events else {"id": "evt_001", "type": "sentence_start", "time_sec": 0.0, "frame": 0},
            "raw_frame": 0,
        }
    ]
    for ev in chosen_cuts:
        raw_frame = int(ev["frame"])
        if ev["time_sec"] <= 2.85:
            raw_frame = min(raw_frame, 3 * fps - 2)
        if raw_frame - cut_points[-1]["cut_frame"] >= 12 and total_frames - raw_frame >= 10:
            cut_points.append({
                "cut_frame": raw_frame,
                "raw_frame": raw_frame,
                "event": ev,
            })

    def find_sentence_for_time(t_sec: float, ev: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if ev and ev.get("sentence_id") in sent_by_id and ev.get("type") in ("sentence_start", "pause_end", "keyword_accent", "stress"):
            return sent_by_id[ev["sentence_id"]]
        for s in sentences:
            if s["start_sec"] <= t_sec <= s["end_sec"]:
                return s
        for s in sentences:
            if s["start_sec"] <= t_sec <= s["end_sec"] + 0.40:
                return s
        return sentences[-1]

    usage_counts: Dict[str, int] = {m["id"]: 0 for m in broll_pool}
    used_cursors: Dict[str, float] = {
        m["id"]: (
            max(0.40, float(m.get("best_in_sec", 0.40)))
            if (m.get("role") == "b_roll" and float(m.get("duration_sec", 0.0)) > 2.0)
            else float(m.get("best_in_sec", 0.0))
        )
        for m in broll_pool
    }
    taboo_blocked_count = 0

    def pick_best_broll(
        sent: Dict[str, Any],
        seg_dur: float,
        last_group: str,
        t_start: float,
        forbid_code: bool = False,
    ) -> Dict[str, Any]:
        nonlocal taboo_blocked_count
        s_txt = str(sent.get("text", ""))
        s_kws = [str(k) for k in sent.get("keywords", [])]
        is_praise = any(cue in s_txt for cue in POSITIVE_PRAISE_CUES)
        candidates = [m for m in broll_pool if not (forbid_code and m.get("role") == "code_broll")] or broll_pool
        best_m = candidates[0]
        best_score = -1e9
        blocked_here = False
        for m in candidates:
            tags = [str(t) for t in (m.get("semantic_tags", []) + m.get("manual_tags", []))]
            sem_hits = sum(1 for tg in tags if tg and (tg in s_txt or any(tg in k or k in tg for k in s_kws)))
            cont_bonus = 1.5 if m.get("scene_group") == last_group else 0.0
            qual_bonus = float(m.get("stability_score", 0.8)) * 1.2
            reuse_pen = usage_counts.get(m["id"], 0) * 12.0
            avail = float(m.get("duration_sec", 3.0))
            rem = avail - used_cursors.get(m["id"], 0.0)
            dur_bonus = 2.0 if rem >= seg_dur else (0.5 if avail >= seg_dur else -2.0)
            live_bonus = 3.5 if (t_start < 3.0 and m.get("role") == "b_roll") else 0.0
            taboo_pen = 0.0
            if is_praise and m.get("taboo_tags"):
                taboo_pen = 1000.0
                blocked_here = True
            sc = sem_hits * 5.0 + cont_bonus + qual_bonus + dur_bonus + live_bonus - reuse_pen - taboo_pen
            if sc > best_score:
                best_score = sc
                best_m = m
        if blocked_here and not best_m.get("taboo_tags"):
            taboo_blocked_count += 1
        return best_m

    # 按 A2 声画分离守卫与 target_aroll_ratio 分配轨道角色
    n_segs = len(cut_points)
    roles: List[str] = []
    audio_only_flags: List[bool] = []
    aroll_step = max(2, int(round(1.0 / max(0.15, target_aroll_ratio))))
    for idx, cp in enumerate(cut_points):
        ev = cp["event"]
        t_start = frames_to_sec(cp["cut_frame"], fps)
        sent = find_sentence_for_time(t_start, ev)
        sent_vgrade = str(sent.get("source_video_grade") or vgrade_by_mp4.get(str(sent.get("source_media", "")), "usable"))
        forced_mask = sent_vgrade in ("audio_only", "ng")
        audio_only_flags.append(forced_mask)

    def _can_be_safe_aroll(seg_idx: int) -> bool:
        if audio_only_flags[seg_idx] or not usable_arolls or prefer_broll_only:
            return False
        t_in = frames_to_sec(cut_points[seg_idx]["raw_frame"], fps)
        t_out = frames_to_sec(
            cut_points[seg_idx + 1]["raw_frame"] if seg_idx + 1 < n_segs else total_frames,
            fps,
        )
        overlapping = [
            s for s in sentences
            if min(t_out, float(s["end_sec"])) - max(t_in, float(s["start_sec"])) > 0.04
        ]
        if not overlapping:
            overlapping = [find_sentence_for_time(t_in, cut_points[seg_idx]["event"])]
        if len(overlapping) != 1:
            return False
        if t_in - float(overlapping[0]["start_sec"]) > 1.10:
            return False
        if seg_idx < n_segs - 1:
            last_s = overlapping[-1]
            if t_out > float(last_s["end_sec"]) + float(last_s.get("pause_after_sec", 0.08)) + 0.04:
                return False
        return True

    for idx, cp in enumerate(cut_points):
        ev = cp["event"]
        t_start = frames_to_sec(cp["cut_frame"], fps)
        sent = find_sentence_for_time(t_start, ev)
        offset_in_sent = t_start - float(sent["start_sec"])
        near_sent_start = offset_in_sent <= 0.85
        has_prop_show = any(cue in sent.get("text", "") for cue in PROP_SHOW_CUES)
        is_tail = (idx == n_segs - 1 and near_sent_start) or (t_start >= max(0.0, total_dur - 8.5) and near_sent_start)

        if audio_only_flags[idx] or not usable_arolls or prefer_broll_only:
            roles.append("b_roll")
        elif idx == 0:
            roles.append("a_roll")
        elif _can_be_safe_aroll(idx) and near_sent_start and (
            has_prop_show or is_tail or (idx % aroll_step == 1 if aroll_step > 2 else idx % 2 == 1)
        ):
            roles.append("a_roll")
        else:
            roles.append("b_roll")

    # 兜底确保非纯 B-roll 模式下，仅从符合句界严锁的安全段提拔，保证结尾 10s 内出镜且 A-Roll 总占比达标
    if usable_arolls and not prefer_broll_only:
        seg_ends_est = [
            frames_to_sec(cut_points[i + 1]["raw_frame"] if i + 1 < n_segs else total_frames, fps)
            for i in range(n_segs)
        ]
        seg_durs_est = [
            frames_to_sec(
                (cut_points[i + 1]["raw_frame"] if i + 1 < n_segs else total_frames) - cut_points[i]["raw_frame"],
                fps,
            )
            for i in range(n_segs)
        ]
        cur_aroll_sec = sum(d for r, d in zip(roles, seg_durs_est) if r == "a_roll")
        has_tail_aroll = any(
            roles[i] == "a_roll" and seg_ends_est[i] >= total_dur - 10.0
            for i in range(n_segs)
        )
        if not has_tail_aroll:
            tail_cands = []
            for i in range(n_segs):
                if roles[i] != "b_roll" or not _can_be_safe_aroll(i) or seg_ends_est[i] < total_dur - 10.0:
                    continue
                t_s = frames_to_sec(cut_points[i]["raw_frame"], fps)
                s_obj = find_sentence_for_time(t_s, cut_points[i]["event"])
                off = abs(t_s - float(s_obj["start_sec"]))
                tail_cands.append((-i, off, -seg_durs_est[i], i))
            if tail_cands:
                _, _, _, best_tail_i = sorted(tail_cands)[0]
                roles[best_tail_i] = "a_roll"
                cur_aroll_sec += seg_durs_est[best_tail_i]

        min_aroll_sec = total_dur * max(0.24, min(0.35, target_aroll_ratio * 0.78))
        if cur_aroll_sec < min_aroll_sec:
            cands = []
            for i in range(n_segs):
                if roles[i] != "b_roll" or not _can_be_safe_aroll(i):
                    continue
                t_s = frames_to_sec(cut_points[i]["raw_frame"], fps)
                s_obj = find_sentence_for_time(t_s, cut_points[i]["event"])
                off = abs(t_s - float(s_obj["start_sec"]))
                cands.append((off, -seg_durs_est[i], i))
            for _, _, i_cand in sorted(cands):
                roles[i_cand] = "a_roll"
                cur_aroll_sec += seg_durs_est[i_cand]
                if cur_aroll_sec >= min_aroll_sec:
                    break

    j_leads = [0] * n_segs
    if j_cut_frames > 0:
        for idx in range(1, n_segs):
            ev = cut_points[idx]["event"]
            if roles[idx - 1] != "a_roll" and ev["type"] in ("sentence_start", "pause_end", "keyword_accent"):
                cand_f = cut_points[idx]["raw_frame"] + j_cut_frames
                next_f = cut_points[idx + 1]["raw_frame"] if idx + 1 < n_segs else total_frames
                if (idx == 1 or cut_points[idx]["raw_frame"] >= 90 or cand_f < 90) and (next_f - cand_f >= 12):
                    cut_points[idx]["cut_frame"] = cand_f
                    j_leads[idx] = j_cut_frames

        for idx in range(1, n_segs):
            if cut_points[idx]["cut_frame"] - cut_points[idx - 1]["cut_frame"] < 10:
                cut_points[idx]["cut_frame"] = cut_points[idx]["raw_frame"]
                j_leads[idx] = 0

        for idx in range(1, n_segs):
            if roles[idx] == "a_roll" and j_leads[idx] > 0:
                t_jc = frames_to_sec(cut_points[idx]["cut_frame"], fps)
                s_jc = find_sentence_for_time(t_jc, cut_points[idx]["event"])
                if t_jc - float(s_jc["start_sec"]) > 1.15:
                    cut_points[idx]["cut_frame"] = cut_points[idx]["raw_frame"]
                    j_leads[idx] = 0

    clips: List[Dict[str, Any]] = []
    last_group = "shoot_default"
    last_aroll_zoom = 1.0
    punch_in_count = 0

    for idx, cp in enumerate(cut_points):
        f_start = cp["cut_frame"]
        f_end = cut_points[idx + 1]["cut_frame"] if idx + 1 < n_segs else total_frames
        t_start = frames_to_sec(f_start, fps)
        t_end = frames_to_sec(f_end, fps)
        seg_dur = round(t_end - t_start, 4)
        ev = cp["event"]
        sent = find_sentence_for_time(t_start, ev)
        has_prop_show = any(cue in sent.get("text", "") for cue in PROP_SHOW_CUES)

        if roles[idx] == "a_roll":
            ar = next((a for a in usable_arolls if a["normalized_mp4"] == sent.get("source_media")), usable_arolls[0])
            if sent.get("source_media") == ar["normalized_mp4"]:
                offset_in_sent = max(0.0, t_start - sent["start_sec"])
                src_in = round(float(sent.get("source_in_sec", 0.0)) + offset_in_sent, 4)
                sent_src_end = float(sent.get("source_out_sec", ar["duration_sec"]))
                max_src_out = min(ar["duration_sec"], sent_src_end)
            else:
                src_in = round(t_start % max(0.5, ar["duration_sec"] - seg_dur), 4) if ar["duration_sec"] > seg_dur else 0.0
                max_src_out = ar["duration_sec"]

            src_in = min(src_in, max(0.0, max_src_out - 0.05))
            need_freeze = src_in + seg_dur > max_src_out + 0.01
            src_out = round(min(max_src_out, src_in + seg_dur), 4)

            vf_extra = ""
            cur_zoom = 1.0
            if idx > 0 and roles[idx - 1] == "a_roll" and not has_prop_show:
                cur_zoom = 1.09 if last_aroll_zoom == 1.0 else 1.0
                if cur_zoom > 1.01:
                    vf_extra = _build_eye_locked_vf(ar.get("eye_anchor"), zoom=cur_zoom)
                    punch_in_count += 1
            last_aroll_zoom = cur_zoom

            kb_cfg = {"zoom_start": 1.0, "zoom_end": 1.06, "reason": "防>3s静止微缓推"} if (seg_dur > 3.0 and not vf_extra) else None
            purpose = (
                "首帧高密度开篇钩子" if idx == 0
                else ("实拍实物/图纸展示保护" if has_prop_show
                      else ("锁眼微推拉强调(1.09x)" if cur_zoom > 1.01
                            else ("结尾人设转化" if idx == n_segs - 1 else "同期声口播强化信任")))
            )
            clips.append({
                "clip_id": f"clip_{idx+1:03d}",
                "track_role": "a_roll",
                "media_path": ar["normalized_mp4"],
                "source_in_sec": src_in,
                "source_out_sec": src_out,
                "tight": True,
                "freeze_tail": need_freeze,
                "timeline_in_sec": t_start,
                "timeline_out_sec": t_end,
                "timeline_in_frame": f_start,
                "timeline_out_frame": f_end,
                "duration_sec": seg_dur,
                "audio_locked": True,
                "b_roll_overlay": False,
                "underlying_audio_untouched": True,
                "j_cut_lead_frames": j_leads[idx],
                "punch_in_zoom": cur_zoom,
                "ken_burns": kb_cfg,
                "vf_extra": vf_extra,
                "cut_event_id": ev["id"],
                "cut_event_type": ev["type"],
                "cut_event_frame": ev["frame"],
                "sentence_id": sent["id"],
                "sentence_text": sent["text"],
                "purpose": purpose,
            })
        else:
            has_live_broll = any(m.get("role") == "b_roll" for m in broll_pool)
            cur_code_dur = sum(float(c["duration_sec"]) for c in clips if c.get("track_role") == "code_broll")
            forbid_code = has_live_broll and ((cur_code_dur + seg_dur) / max(0.1, total_dur) > 0.50)

            br = pick_best_broll(sent, seg_dur, last_group, t_start, forbid_code=forbid_code)
            bid = br["id"]
            avail = float(br.get("duration_sec", 3.0))
            is_live_long = br.get("role") == "b_roll" and avail > 2.0
            handle_s = 0.40 if (is_live_long and avail >= seg_dur + 0.80) else 0.0
            max_usable_out = avail - handle_s
            cur_pos = float(used_cursors.get(bid, max(handle_s, float(br.get("best_in_sec", handle_s)))))

            if br["role"] == "code_broll":
                src_in = 0.0
            else:
                cur_pos = max(handle_s, cur_pos)
                if cur_pos + seg_dur <= max_usable_out:
                    src_in = round(cur_pos, 4)
                elif max_usable_out - handle_s >= seg_dur:
                    src_in = round(max(handle_s, max_usable_out - seg_dur), 4)
                elif avail > seg_dur + 0.45:
                    src_in = round(max(0.40, avail - seg_dur - 0.05), 4)
                else:
                    src_in = round(min(max(handle_s, float(br.get("best_in_sec", handle_s))), max(0.0, avail - 0.3)), 4)

            used_cursors[bid] = src_in + seg_dur + 0.20
            usage_counts[bid] = usage_counts.get(bid, 0) + 1
            last_group = str(br.get("scene_group", last_group))
            need_freeze = src_in + seg_dur > avail + 0.01
            src_out = round(min(avail, src_in + seg_dur), 4)
            eff_role = "freeze" if need_freeze and avail < 0.5 else br["role"]
            kb_cfg = {"zoom_start": 1.0, "zoom_end": 1.06, "reason": "防>3s静止微缓推"} if (seg_dur > 3.0 and eff_role == "b_roll") else None

            if audio_only_flags[idx]:
                b_purpose = "声画分离保声遮脸(audio_only)"
            elif t_start < 3.0:
                b_purpose = "前3秒视觉高密度切换"
            elif j_leads[idx] > 0:
                b_purpose = f"真J-cut({j_leads[idx]}帧)语义B-roll"
            else:
                b_purpose = "语义互文B-roll保持声音不变"

            clips.append({
                "clip_id": f"clip_{idx+1:03d}",
                "track_role": eff_role,
                "media_path": br["normalized_mp4"],
                "source_in_sec": src_in,
                "source_out_sec": src_out,
                "tight": True,
                "handles_guarded": bool(br["role"] == "code_broll" or (src_in >= 0.39 and (avail - src_out) >= 0.39)),
                "audio_only_masked": audio_only_flags[idx],
                "freeze_tail": need_freeze,
                "timeline_in_sec": t_start,
                "timeline_out_sec": t_end,
                "timeline_in_frame": f_start,
                "timeline_out_frame": f_end,
                "duration_sec": seg_dur,
                "audio_locked": False,
                "b_roll_overlay": True,
                "underlying_audio_untouched": True,
                "j_cut_lead_frames": j_leads[idx],
                "punch_in_zoom": 1.0,
                "ken_burns": kb_cfg,
                "vf_extra": "",
                "cut_event_id": ev["id"],
                "cut_event_type": ev["type"],
                "cut_event_frame": ev["frame"],
                "sentence_id": sent["id"],
                "sentence_text": sent["text"],
                "purpose": b_purpose,
            })

    first_3s_changes = sum(1 for c in clips[1:] if c["timeline_in_frame"] < 3 * fps)
    jcut_real_count = sum(1 for c in clips if c.get("j_cut_lead_frames", 0) > 0)
    durs = [float(c["duration_sec"]) for c in clips]
    shot_cv = round(statistics.stdev(durs) / max(1e-5, statistics.mean(durs)), 3) if len(durs) >= 2 else 0.0
    code_dur = sum(float(c["duration_sec"]) for c in clips if c.get("track_role") == "code_broll")
    code_ratio = round(code_dur / max(0.1, total_dur), 3)
    first_clip_ok = bool(clips and Path(str(clips[0].get("media_path", ""))).exists())

    edl = {
        "step": "04_build_edl",
        "fps": fps,
        "tight": True,
        "style_id": style_id,
        "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
        "total_duration_sec": total_dur,
        "total_frames": total_frames,
        "j_cut_default_frames": j_cut_frames,
        "j_cut_applied_count": jcut_real_count,
        "punch_in_count": punch_in_count,
        "shot_duration_cv": shot_cv,
        "code_broll_ratio": code_ratio,
        "ppt_warning": code_ratio > 0.30,
        "audio_only_masked_count": sum(1 for c in clips if c.get("audio_only_masked")),
        "taboo_blocked_count": taboo_blocked_count,
        "deictic_gaps": deictic_gaps,
        "first_frame_density_ok": first_clip_ok,
        "first_3s_visual_changes": first_3s_changes,
        "clip_count": len(clips),
        "clips": clips,
    }
    write_json(pipeline_dir / "04_edl.json", edl)

    md_lines = [
        "# 以音定画专业剪辑表 (Edit Decision List)",
        "",
        f"- **总时长**：{total_dur:.2f}s ({total_frames} 帧 @ {fps}fps)",
        f"- **前 3 秒画面变化次数**：{first_3s_changes} 次（全部对应音频事件）",
        f"- **真 J-cut 落实**：{jcut_real_count} 处（声先画后 {j_cut_frames} 帧）| **锁眼推拉**：{punch_in_count} 处",
        "",
        "| 镜头 | 轨道角色 | 源入点 | 时码起止 | 时长 | 切点事件 | J-cut/推拉 | 用途 | 对应文案句 |",
        "| :--- | :--- | :---: | :--- | :---: | :--- | :---: | :--- | :--- |",
    ]
    for c in clips:
        j_p = f"J{c['j_cut_lead_frames']}f" if c["j_cut_lead_frames"] else ("1.09x" if c["punch_in_zoom"] > 1.01 else "-")
        md_lines.append(
            f"| `{c['clip_id']}` | `{c['track_role']}` | {c['source_in_sec']:.2f}s | "
            f"{c['timeline_in_sec']:.2f}s–{c['timeline_out_sec']:.2f}s | {c['duration_sec']:.2f}s | "
            f"`{c['cut_event_id']}` ({c['cut_event_type']}) | {j_p} | {c['purpose']} | {c['sentence_text']} |"
        )
    (pipeline_dir / "04_edl_table.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(
        f"[step4] 专业剪辑表生成完成：{len(clips)} 个镜头，前3秒切换 {first_3s_changes} 次，"
        f"真J-cut {jcut_real_count} 处，锁眼推拉 {punch_in_count} 处 -> 04_edl.json"
    )
    return edl


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 4: 以音定画生成专业剪辑表")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--j-cut-frames", type=int, default=3)
    parser.add_argument("--style-id", help="风格谱ID")
    parser.add_argument("--account-profile", help="account_profile.json 路径")
    parser.add_argument("--prefer-broll-only", action="store_true", help="打断#3只改画面时可选切换纯B-roll混剪")
    args = parser.parse_args()
    prof = read_json(Path(args.account_profile)) if args.account_profile and Path(args.account_profile).exists() else None
    run_step4(
        Path(args.pipeline_dir),
        track=args.track,
        j_cut_frames=args.j_cut_frames,
        prefer_broll_only=args.prefer_broll_only,
        style_id=args.style_id,
        account_profile=prof,
    )


if __name__ == "__main__":
    main()
