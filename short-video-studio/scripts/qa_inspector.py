#!/usr/bin/env python3
"""
[Step 10: 22 项硬红线自检 + 独立黑盒外部裁判 + Markdown/JSON 报告]
自动核查（白盒 #1–#11 + 黑盒 #12–#22，统一三态 PASS | FAIL | SKIPPED_UNAVAILABLE）：
1–11. 响度(-14 LUFS)、黑帧、静音、音画差(<=1帧)、字幕与03_audio_events真实偏差、切点对齐、
      平台UI安全区/<=3字残句/包装预算、镜头分布/零倒带/A-Roll占比、前3秒切换、句界严锁、common禁用词防露馅。
12–22. 调用 independent_verifier.py 执行单遍解码黑盒校验（声学/OCR/WCAG>=3.8单框/SKIN_YCBCR/
       相对FCPXML与全离线HyperFrames/防静止/全量切点Spot/SFX同窗或tidy基准/本地ASR/合规与商用字体）。
输出：pipeline/10_qa_report.json 与 pipeline/10_qa_report.md
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    DEFAULT_FPS,
    LEAK_REGEX_PATTERNS,
    SAFE_ZONE,
    SUBTITLE_SAFE_ZONE,
    TARGET_LUFS,
    check_bbox_in_safe_zone,
    ffprobe_media,
    find_bin,
    maybe_reexec_in_venv,
    read_json,
    run_cmd,
    write_json,
)

maybe_reexec_in_venv()


META_LEAK_RE = re.compile("|".join(LEAK_REGEX_PATTERNS), re.IGNORECASE)


def _mk_check(
    cid: str,
    name: str,
    status: str,
    measured: str,
    threshold: str,
) -> Dict[str, Any]:
    st = status if status in ("PASS", "FAIL", "SKIPPED_UNAVAILABLE") else "FAIL"
    return {
        "id": cid,
        "name": name,
        "status": st,
        "passed": (st == "PASS"),
        "measured": measured,
        "threshold": threshold,
    }


def _strip_punct_for_len(text: str) -> str:
    return re.sub(r"[\s，。！？；：、,.!?;:\"'“”‘’()（）\[\]【】—\-]+", "", str(text or ""))


def _check_bbox_in_bounds(bbox: Any, bounds: Dict[str, int], label: str) -> tuple[bool, str]:
    if not isinstance(bbox, dict) or not {"x", "y", "w", "h"}.issubset(bbox.keys()):
        return False, f"{label}: 缺少完整 bbox"
    x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
    if x < bounds["x_min"] or (x + w) > bounds["x_max"] or y < bounds["y_min"] or (y + h) > bounds["y_max"]:
        return False, f"{label} ({x},{y},{x+w},{y+h}) 越出 ({bounds['x_min']},{bounds['y_min']},{bounds['x_max']},{bounds['y_max']})"
    return True, "ok"


def compare_with_benchmark(video_path: Path, benchmark_path: Path, edl: Dict[str, Any]) -> Dict[str, Any]:
    """与用户提供的对标参考视频做结构/节奏对比（只读对比）。"""
    ffmpeg = find_bin("ffmpeg")
    b_probe = ffprobe_media(benchmark_path)
    b_dur = float(b_probe.get("format", {}).get("duration") or 10.0)
    b_cuts = 1
    if ffmpeg:
        res = run_cmd([
            ffmpeg, "-hide_banner", "-i", str(benchmark_path),
            "-vf", "select='gt(scene,0.28)',showinfo", "-f", "null", "-",
        ], check=False)
        b_cuts = max(1, len(re.findall(r"pts_time:([0-9.]+)", res.stderr or "")))

    v_dur = float(edl.get("total_duration_sec") or 1.0)
    v_cuts = max(1, len(edl.get("clips") or []))
    b_asl = round(b_dur / b_cuts, 2)
    v_asl = round(v_dur / v_cuts, 2)
    return {
        "benchmark_file": str(benchmark_path.resolve()),
        "benchmark_duration_sec": round(b_dur, 2),
        "benchmark_estimated_asl_sec": b_asl,
        "our_duration_sec": round(v_dur, 2),
        "our_asl_sec": v_asl,
        "rhythm_delta_sec": round(v_asl - b_asl, 2),
        "our_video_path": str(video_path.resolve()),
    }


def inspect_video(
    pipeline_dir: Path,
    video_path: Optional[Path] = None,
    benchmark_path: Optional[Path] = None,
    dialect: str = "mandarin",
    store_anchor: Optional[str] = None,
    strict_asr: bool = False,
) -> Dict[str, Any]:
    if video_path is None:
        video_path = pipeline_dir / "output_final.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"待检成片不存在: {video_path}")

    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    subs = read_json(pipeline_dir / "05_subtitles.json")
    pkg = read_json(pipeline_dir / "06_packaging.json")

    fps = int(edl.get("fps", DEFAULT_FPS))
    frame_dur = 1.0 / fps
    checks: List[Dict[str, Any]] = []
    ffmpeg = find_bin("ffmpeg")

    from independent_verifier import extract_audio_context_once, run_blackbox_verification

    with tempfile.TemporaryDirectory(prefix="svs_qa_audio_") as td:
        audio_ctx = extract_audio_context_once(video_path, Path(td))
        r128 = audio_ctx["r128"]
        long_sil = audio_ctx["long_silences"]

        # 1. 响度检查 (-14 LUFS ±2 LU)
        lufs = float(r128["integrated_lufs"])
        lufs_ok = abs(lufs - TARGET_LUFS) <= 2.0
        checks.append(_mk_check(
            "loudness",
            "综合响度 (-14 LUFS)",
            "PASS" if lufs_ok else "FAIL",
            f"{lufs:.2f} LUFS (TP={r128['true_peak_dbtp']} dBTP)",
            f"{TARGET_LUFS - 2.0:.1f} ~ {TARGET_LUFS + 2.0:.1f} LUFS",
        ))

        # 2. 黑帧检测
        black_hits: List[str] = []
        if ffmpeg:
            b_res = run_cmd([
                ffmpeg, "-hide_banner", "-i", str(video_path),
                "-vf", "blackdetect=d=0.2:pix_th=0.10", "-an", "-f", "null", "-",
            ], check=False)
            black_hits = re.findall(r"black_duration:([0-9.]+)", b_res.stderr or "")
        checks.append(_mk_check(
            "black_frames",
            "黑帧检测",
            "PASS" if len(black_hits) == 0 else ("FAIL" if ffmpeg else "SKIPPED_UNAVAILABLE"),
            f"{len(black_hits)} 处连续黑帧",
            "0 处黑帧 (>=0.2s)",
        ))

        # 3. 超过 0.8s 静音段检测（复用单次音频抽取结果）
        checks.append(_mk_check(
            "long_silence",
            "超过 0.8s 的静音段",
            "PASS" if len(long_sil) == 0 else "FAIL",
            f"{len(long_sil)} 段" + (f" (最长 {max(long_sil):.2f}s)" if long_sil else ""),
            "0 段 > 0.8s",
        ))

        # 4. 音画同步与总时长差 <= 1 帧
        probe = ffprobe_media(video_path)
        streams = probe.get("streams", [])
        v_s = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_s = next((s for s in streams if s.get("codec_type") == "audio"), None)
        v_dur = float(v_s.get("duration") or probe.get("format", {}).get("duration") or 0.0) if v_s else 0.0
        a_dur = float(a_s.get("duration") or probe.get("format", {}).get("duration") or 0.0) if a_s else 0.0
        dur_diff_sec = abs(v_dur - a_dur)
        dur_diff_frames = dur_diff_sec / frame_dur
        aroll_sync_ok = all(bool(c.get("audio_locked", True)) for c in edl["clips"] if c.get("track_role") == "a_roll")
        sync_ok = (v_s is not None and a_s is not None and dur_diff_frames <= 1.05 and aroll_sync_ok)
        checks.append(_mk_check(
            "av_sync",
            "音画同步与总时长差 (≤1帧)",
            "PASS" if sync_ok else "FAIL",
            f"时长差 {dur_diff_sec*1000:.1f}ms ({dur_diff_frames:.2f} 帧), 同期声锁定={'是' if aroll_sync_ok else '否'}",
            f"≤ 1.0 帧 ({frame_dur*1000:.1f}ms)",
        ))

        # 5. 字幕与 03_audio_events 真实锁定语音时间戳交叉偏差（禁止与自身 words 相减作假）
        sents_all = audio_events.get("sentences", [])
        sents_by_id = {s["id"]: s for s in sents_all if s.get("id")}
        max_sub_err_sec = 0.0
        sub_sync_violations: List[str] = []
        active_subs = [e for e in subs.get("entries", []) if not e.get("suppressed")]
        for sub in active_subs:
            sid = sub.get("sentence_id")
            st_sub = float(sub.get("start_sec", 0.0))
            en_sub = float(sub.get("end_sec", 0.0))
            ref_sent = sents_by_id.get(sid)
            if not ref_sent:
                sub_sync_violations.append(f"{sub.get('id')}: 未关联有效 03_audio_events 句子 {sid}")
                continue
            s_st = float(ref_sent.get("start_sec", 0.0))
            s_en = float(ref_sent.get("end_sec", 0.0))
            if st_sub < s_st - 0.08 or en_sub > s_en + 0.12:
                overrun = max(s_st - st_sub, en_sub - s_en, 0.0)
                max_sub_err_sec = max(max_sub_err_sec, overrun)
                sub_sync_violations.append(f"{sub.get('id')}: 超出所属锁定句窗 [{s_st:.2f},{s_en:.2f}]")

            ref_words = ref_sent.get("words") or []
            sub_words = sub.get("words") or []
            if ref_words and sub_words:
                rw_starts = [float(w["start_sec"]) for w in ref_words if "start_sec" in w]
                rw_ends = [float(w["end_sec"]) for w in ref_words if "end_sec" in w]
                if rw_starts and "start_sec" in sub_words[0]:
                    first_w_t = float(sub_words[0]["start_sec"])
                    nearest_s = min(abs(first_w_t - ts) for ts in rw_starts)
                    max_sub_err_sec = max(max_sub_err_sec, nearest_s)
                    err_self = abs(st_sub - first_w_t)
                    max_sub_err_sec = max(max_sub_err_sec, err_self)
                if rw_ends and "end_sec" in sub_words[-1]:
                    last_w_t = float(sub_words[-1]["end_sec"])
                    nearest_e = min(abs(last_w_t - te) for te in rw_ends)
                    max_sub_err_sec = max(max_sub_err_sec, nearest_e)
                    err_self_e = abs(en_sub - last_w_t)
                    max_sub_err_sec = max(max_sub_err_sec, err_self_e)

        sub_err_frames = max_sub_err_sec / frame_dur
        sub_sync_ok = (len(active_subs) > 0 or len(sents_all) == 0) and len(sub_sync_violations) == 0 and sub_err_frames <= 1.5
        checks.append(_mk_check(
            "subtitle_sync",
            "字幕与 03_audio_events 语音时间戳交叉对齐",
            "PASS" if sub_sync_ok else "FAIL",
            f"最大交叉偏差 {max_sub_err_sec*1000:.1f}ms ({sub_err_frames:.2f} 帧), 越窗 {len(sub_sync_violations)} 处",
            "≤ 1.5 帧且 0 越出锁定句时间窗",
        ))

        # 6. 切点对齐音频事件 (含 J-cut)
        events_by_id = {e["id"]: e for e in audio_events.get("events", [])}
        max_cut_err_frames = 0
        jcut_valid = True
        for clip in edl.get("clips", [])[1:]:
            ev = events_by_id.get(clip.get("cut_event_id", ""))
            j_lead = int(clip.get("j_cut_lead_frames", 0))
            if not (-6 <= j_lead <= 4):
                jcut_valid = False
            if not ev:
                max_cut_err_frames = 999
            else:
                err = abs((int(clip["timeline_in_frame"]) - j_lead) - int(ev["frame"]))
                max_cut_err_frames = max(max_cut_err_frames, err)
        cut_ok = jcut_valid and max_cut_err_frames <= 1
        checks.append(_mk_check(
            "cut_on_audio_event",
            "切点对齐音频事件 (含J-cut)",
            "PASS" if cut_ok else "FAIL",
            f"最大对齐偏差 {max_cut_err_frames} 帧, J-cut合规={'是' if jcut_valid else '否'}",
            "≤ 1 帧",
        ))

        # 7. UI 安全区 / <=3 字残句 / B9 互斥 / 包装预算
        ui_violations: List[str] = []
        short_subs = 0
        code_ranges = [
            (int(c["timeline_in_frame"]), int(c["timeline_out_frame"]))
            for c in edl.get("clips", [])
            if c.get("track_role") == "code_broll"
        ]
        code_collisions = 0
        for sub in subs.get("entries", []):
            if sub.get("suppressed"):
                continue
            bbox = sub.get("final_bbox") or sub.get("bbox")
            ok1, msg1 = check_bbox_in_safe_zone(bbox) if bbox else (False, "缺少bbox")
            ok2, msg2 = _check_bbox_in_bounds(bbox, SUBTITLE_SAFE_ZONE, sub.get("id", "sub"))
            if not ok1 or not ok2:
                ui_violations.append(f"{sub['id']}: {msg1 if not ok1 else msg2}")
            clean_txt = _strip_punct_for_len(sub.get("text", ""))
            ref_sent_txt = _strip_punct_for_len(sents_by_id.get(sub.get("sentence_id", ""), {}).get("text", ""))
            if len(clean_txt) <= 3 and len(ref_sent_txt) >= 5:
                short_subs += 1
                ui_violations.append(f"{sub['id']}: <=3字残句 '{sub.get('text', '')}'")
            sf_sub = int(round(float(sub.get("start_sec", 0.0)) * fps))
            ef_sub = int(round(float(sub.get("end_sec", 0.0)) * fps))
            for cs, ce in code_ranges:
                ov = max(0, min(ef_sub, ce) - max(sf_sub, cs))
                if ef_sub > sf_sub and ov / float(ef_sub - sf_sub) >= 0.60:
                    code_collisions += 1
                    ui_violations.append(f"{sub['id']}: 未触发 B9 让位与代码卡重叠")

        total_edl_dur = max(0.1, float(edl.get("total_duration_sec", 1.0)))
        max_visual_budget = max(3, math.ceil(total_edl_dur / 3.5))
        vis_elems = pkg.get("visual_elements", [])
        if len(vis_elems) > max_visual_budget:
            ui_violations.append(f"视觉包装超预算 {len(vis_elems)} > {max_visual_budget}")
        short_holds = 0
        for elem in vis_elems:
            ebbox = elem.get("final_bbox") or elem.get("bbox")
            ok, msg = _check_bbox_in_bounds(ebbox, SAFE_ZONE, elem.get("id", "pkg"))
            if not ok:
                ui_violations.append(f"{elem['id']}: {msg}")
            sf_e, ef_e = int(elem["start_frame"]), int(elem["end_frame"])
            elem_txt = str(elem.get("text_or_asset") or "")
            hold_sec = (ef_e - sf_e) / float(fps)
            if elem_txt and hold_sec < 1.35:
                short_holds += 1
                ui_violations.append(f"{elem['id']}: 卡片停留过短 {hold_sec:.2f}s (<1.35s)")
            if any(sf_e < ce and ef_e > cs for cs, ce in code_ranges):
                code_collisions += 1
                ui_violations.append(f"{elem['id']}: 与代码补镜卡重叠")
        pkg_sync_err = max([abs(int(e["start_frame"]) - int(e["aligned_sfx_or_accent_frame"])) for e in vis_elems] or [0])
        ui_ok = len(ui_violations) == 0 and pkg_sync_err <= 1
        checks.append(_mk_check(
            "ui_safe_zone",
            "平台 UI 安全区 / 代码卡互斥 / 零 <=3 残句 / 阅读停留",
            "PASS" if ui_ok else "FAIL",
            f"违规 {len(ui_violations)} 处 (碰撞={code_collisions}, <=3残句={short_subs}, 短停留={short_holds}, 包装={len(vis_elems)}/{max_visual_budget}), 误差 {pkg_sync_err} 帧",
            "0违规/0碰撞/0残句, 卡片停留≥1.35s, 包装<=ceil(dur/3.5), 误差<=1帧",
        ))

        # 8. 镜头时长分布 + 零同源倒带 + A-Roll 占比
        durs = [float(c["duration_sec"]) for c in edl["clips"]]
        min_d, max_d = min(durs), max(durs)
        med_d = statistics.median(durs)
        rewind_count = 0
        clips_list = edl.get("clips", [])
        last_src_out_by_mp: Dict[str, float] = {}
        for c_cur in clips_list:
            mp = str(c_cur.get("media_path") or "")
            if not mp:
                continue
            if c_cur.get("track_role") == "code_broll":
                continue
            cin_s = float(c_cur.get("source_in_sec", 0.0))
            cout_s = float(c_cur.get("source_out_sec", cin_s))
            if mp in last_src_out_by_mp and cin_s < last_src_out_by_mp[mp] - 0.08:
                rewind_count += 1
            last_src_out_by_mp[mp] = max(last_src_out_by_mp.get(mp, 0.0), cout_s)
        aroll_clips = [c for c in clips_list if c.get("track_role") == "a_roll"]
        masked_voice_clips = [c for c in clips_list if c.get("audio_only_masked")]
        aroll_ratio = (
            (sum(float(c["duration_sec"]) for c in aroll_clips) + sum(float(c["duration_sec"]) for c in masked_voice_clips))
            / total_edl_dur
            if (aroll_clips or masked_voice_clips)
            else 1.0
        )
        aroll_tail_ok = True if not aroll_clips else any(float(c["timeline_out_sec"]) >= total_edl_dur - 10.0 for c in aroll_clips)
        aroll_ok = (aroll_ratio >= 0.18 and aroll_tail_ok) if aroll_clips else True
        dist_ok = (min_d >= 0.30 and max_d <= 8.0 and rewind_count == 0 and aroll_ok)
        checks.append(_mk_check(
            "shot_duration_dist",
            "镜头分布 / 零同源倒带 / A-Roll 占比",
            "PASS" if dist_ok else "FAIL",
            f"共{len(durs)}镜 | min={min_d:.2f}s, med={med_d:.2f}s, max={max_d:.2f}s | 倒带={rewind_count}, A-roll={aroll_ratio*100:.1f}%",
            "0.30s≤单镜≤8.0s, 0倒带, A-roll≥18%且结尾出镜",
        ))

        # 9. 前 3 秒画面变化次数
        first_3s_cuts = [c for c in edl["clips"][1:] if int(c["timeline_in_frame"]) < 3 * fps]
        all_on_events = all(c.get("cut_event_id") in events_by_id for c in first_3s_cuts)
        first3_ok = len(first_3s_cuts) >= 2 and all_on_events
        checks.append(_mk_check(
            "first_3s_changes",
            "前 3 秒画面变化次数",
            "PASS" if first3_ok else "FAIL",
            f"{len(first_3s_cuts)} 次变化 (对齐事件: {'全部' if all_on_events else '部分缺失'})",
            "≥ 2 次且均对齐音频事件",
        ))

        # 10. A-Roll 句界严锁（兼容正常 J/L-cut 声画错位延续）
        accepted_path = next(
            (p for p in (pipeline_dir / "02_Accepted_Cuts.json", pipeline_dir.parent / "02_Accepted_Cuts.json") if p.exists()),
            None,
        )
        accepted_by_id: Dict[str, Dict[str, Any]] = {}
        if accepted_path:
            acc_raw = read_json(accepted_path)
            acc_list = acc_raw.get("sentences", acc_raw) if isinstance(acc_raw, dict) else acc_raw
            if isinstance(acc_list, list):
                for it in acc_list:
                    if isinstance(it, dict) and it.get("id"):
                        accepted_by_id[str(it["id"])] = it
        aroll_boundary_violations: List[str] = []
        max_overrun_sec = 0.0
        max_late_in_sec = 0.0
        for ci, c in enumerate(aroll_clips):
            c_in, c_out = float(c["timeline_in_sec"]), float(c["timeline_out_sec"])
            overlapping = [s for s in sents_all if min(c_out, float(s["end_sec"])) - max(c_in, float(s["start_sec"])) > 0.04]
            if not overlapping and c.get("sentence_id") in sents_by_id:
                overlapping = [sents_by_id[c["sentence_id"]]]
            if not overlapping:
                continue
            first_s, last_s = overlapping[0], overlapping[-1]
            acc_last = accepted_by_id.get(last_s["id"], {})
            base_end = float(last_s["end_sec"]) + 0.08
            src_locked_ok = float(c.get("source_out_sec", 0.0)) <= float(last_s.get("source_out_sec", 99999.0)) + 0.06
            dyn_end = float(last_s["end_sec"]) + float(last_s.get("pause_after_sec", 0.08)) + 0.04 if src_locked_ok else base_end
            # 允许相邻 B-roll L-cut 跨界延申 (<= 0.25s)
            l_lead_allowance = 0.0
            clip_pos = clips_list.index(c)
            if clip_pos + 1 < len(clips_list):
                nxt_j = int(clips_list[clip_pos + 1].get("j_cut_lead_frames", 0))
                if nxt_j < 0:
                    l_lead_allowance = abs(nxt_j) / float(fps) + 0.04
            cut_end_limit = float(acc_last.get("cut_end_sec") or max(base_end, dyn_end)) + l_lead_allowance
            if last_s["id"] == sents_all[-1]["id"] and c is clips_list[-1]:
                cut_end_limit = max(cut_end_limit, float(acc_last.get("cut_buffer_end_sec") or total_edl_dur))
            overrun = c_out - cut_end_limit
            max_overrun_sec = max(max_overrun_sec, max(0.0, overrun))
            if overrun > 0.04:
                aroll_boundary_violations.append(f"{c['clip_id']} ({last_s['id']}): +{overrun:.2f}s")
            late_in = c_in - float(first_s["start_sec"])
            max_late_in_sec = max(max_late_in_sec, max(0.0, late_in))
            if late_in > 1.20:
                aroll_boundary_violations.append(f"{c['clip_id']} ({first_s['id']}): 晚入 +{late_in:.2f}s")
        checks.append(_mk_check(
            "aroll_sentence_lock",
            "A-Roll 句界严锁与口型同步",
            "PASS" if len(aroll_boundary_violations) == 0 else "FAIL",
            f"违规 {len(aroll_boundary_violations)} 处 (超界 {max_overrun_sec:.2f}s, 晚入 {max_late_in_sec:.2f}s)",
            "0 违规 (超界≤0.04s, 晚入≤1.20s)",
        ))

        # 11. 统一使用 common.LEAK_REGEX_PATTERNS 的零开发词与风格名称露馅审计
        meta_leaks: List[str] = []

        def _scan_text(src_tag: str, txt: Any) -> None:
            s = str(txt or "").strip()
            if not s:
                return
            m = META_LEAK_RE.search(s)
            if m:
                meta_leaks.append(f"{src_tag}: 命中 '{m.group(0)}' ({s[:24]})")

        for sub in subs.get("entries", []):
            _scan_text(sub.get("id", "sub"), sub.get("text"))
        for elem in pkg.get("visual_elements", []):
            _scan_text(elem.get("id", "pkg"), elem.get("text_or_asset"))
        cover_json_p = pipeline_dir / "09_title_cover.json"
        if cover_json_p.exists():
            cj = read_json(cover_json_p)
            for k in ("cover_main", "cover_sub", "hashtags"):
                _scan_text(f"09_cover.{k}", cj.get(k))
            for ti, tline in enumerate(cj.get("titles") or []):
                _scan_text(f"09_cover.titles[{ti}]", tline)

        checks.append(_mk_check(
            "forbidden_meta_text",
            "零开发词与风格名称露馅审计 (common.LEAK_REGEX_PATTERNS)",
            "PASS" if len(meta_leaks) == 0 else "FAIL",
            f"检出 {len(meta_leaks)} 处" + (f" ({meta_leaks[0]})" if meta_leaks else ""),
            "0 处元数据/风格代号露馅",
        ))

        # 12–22. 独立黑盒外部验收（复用 audio_ctx，严格 Fail-Closed 三态）
        bb_res = run_blackbox_verification(
            video_path,
            pipeline_dir=pipeline_dir,
            dialect=dialect,
            store_anchor=store_anchor,
            strict_asr=strict_asr,
            precomputed_audio=audio_ctx,
        )

    for bchk in bb_res["checks"]:
        checks.append(_mk_check(
            bchk["id"],
            bchk["name"],
            bchk["status"],
            bchk["measured"],
            bchk["threshold"],
        ))

    mandatory_ok = all(c["status"] == "PASS" for c in checks if c["id"] != "bb_blind_asr_proxy")
    asr_chk = next((c for c in checks if c["id"] == "bb_blind_asr_proxy"), {"status": "FAIL"})
    asr_ok = (asr_chk["status"] == "PASS") if strict_asr else (asr_chk["status"] != "FAIL")
    all_passed = mandatory_ok and asr_ok

    approval_status = "REJECTED"
    if all_passed:
        approval_status = "MACHINE_PASS_PENDING_HUMAN"
        lock_p = pipeline_dir / "approval_lock.json"
        if lock_p.is_file():
            try:
                if read_json(lock_p).get("human_approved"):
                    approval_status = "APPROVED"
            except Exception:
                pass

    pass_cnt = sum(1 for c in checks if c["status"] == "PASS")
    fail_cnt = sum(1 for c in checks if c["status"] == "FAIL")
    skip_cnt = sum(1 for c in checks if c["status"] == "SKIPPED_UNAVAILABLE")

    report: Dict[str, Any] = {
        "step": "10_qa_inspector",
        "video_path": str(video_path.resolve()),
        "all_passed": all_passed,
        "machine_passed": all_passed,
        "approval_status": approval_status,
        "strict_asr": strict_asr,
        "pass_count": pass_cnt,
        "fail_count": fail_cnt,
        "skipped_count": skip_cnt,
        "total_checks": len(checks),
        "checks": checks,
        "blackbox_details": bb_res,
    }
    if benchmark_path and benchmark_path.exists():
        report["benchmark_comparison"] = compare_with_benchmark(video_path, benchmark_path, edl)

    write_json(pipeline_dir / "10_qa_report.json", report)

    status_banner = (
        "✅ **机检与人工终审全部通过 (APPROVED)**"
        if approval_status == "APPROVED"
        else (
            "🟡 **全部必检通过，待人工终审确认 (MACHINE_PASS_PENDING_HUMAN)**"
            if all_passed
            else "❌ **未通过质检 (REJECTED - 禁止交付)**"
        )
    )
    md_lines = [
        "# 22 项硬红线与独立黑盒质检报告",
        "",
        f"- **成片文件**：`{video_path.name}`",
        f"- **质量状态**：{status_banner}",
        f"- **统计**：`{pass_cnt}/{len(checks)}` PASS | FAIL `{fail_cnt}` | SKIPPED_UNAVAILABLE `{skip_cnt}`",
        "",
        "| # | 检查项 | 状态 | 实测结果 | 门槛 |",
        "|:-:|:---|:---:|:---|:---|",
    ]
    for i, c in enumerate(checks, start=1):
        st_icon = "✅ PASS" if c["status"] == "PASS" else ("⚠️ SKIP" if c["status"] == "SKIPPED_UNAVAILABLE" else "❌ FAIL")
        md_lines.append(f"| {i} | {c['name']} | {st_icon} | {c['measured']} | {c['threshold']} |")

    if "benchmark_comparison" in report:
        bc = report["benchmark_comparison"]
        md_lines.extend([
            "",
            "## 对标参考节奏对比",
            f"- 参考片 `{Path(bc['benchmark_file']).name}` 估算 ASL：`{bc['benchmark_estimated_asl_sec']}s`；本片 ASL：`{bc['our_asl_sec']}s`（差值 `{bc['rhythm_delta_sec']:+.2f}s`）",
        ])

    (pipeline_dir / "10_qa_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"[qa] 22 项质检完成：{pass_cnt}/{len(checks)} PASS (fail={fail_cnt}, skip={skip_cnt}) -> 10_qa_report.md")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 10: 22 项硬红线与独立黑盒质检报告生成")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--video", help="指定待检 MP4（默认 output_final.mp4）")
    parser.add_argument("--benchmark", help="可选：对标视频路径")
    parser.add_argument("--dialect", default="mandarin", help="语言模式 (mandarin / cantonese / dialect)")
    parser.add_argument("--store-anchor", help="门店名/核心听觉锚点")
    parser.add_argument("--strict-asr", action="store_true", help="无本地 Whisper 模型时阻止通过")
    args = parser.parse_args()

    res = inspect_video(
        Path(args.pipeline_dir),
        video_path=Path(args.video) if args.video else None,
        benchmark_path=Path(args.benchmark) if args.benchmark else None,
        dialect=args.dialect,
        store_anchor=args.store_anchor,
        strict_asr=args.strict_asr,
    )
    if not res["all_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
