#!/usr/bin/env python3
"""
[Step 10: 全套白盒硬红线自检 + 独立黑盒外部裁判 + 独立盲审取证包]
自动核查（白盒 #1–#11 + 黑盒 #12–#22 + 可选同步/来源门禁，统一三态 PASS | FAIL | SKIPPED_UNAVAILABLE）：
1–11. 响度(-14 LUFS ±1.5)、黑帧、静音、音画差(<=1帧)、字幕与03_audio_events真实偏差、切点对齐、
      平台UI安全区/540px水平居中/[220,860]对称带/<=3字残句/包装预算、镜头分布/零倒带/A-Roll占比、前3秒切换、句界严锁、禁词防露馅。
12–22. 调用 independent_verifier.py 执行单遍解码黑盒校验。
输出：pipeline/10_qa_report.json、pipeline/10_qa_report.md 与 pipeline/10_blind_review_pack.md
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    DEFAULT_FPS,
    EDIT_PROFILES,
    EXPORT_CHOICES,
    export_selection,
    LEAK_REGEX_PATTERNS,
    LOUDNESS_TOLERANCE_LUFS,
    SAFE_ZONE,
    SUBTITLE_CENTER_X,
    SUBTITLE_SAFE_ZONE,
    SUBTITLE_SYMMETRIC_ZONE,
    TARGET_LUFS,
    check_bbox_in_safe_zone,
    ffprobe_media,
    find_bin,
    maybe_reexec_in_venv,
    read_json,
    review_checks,
    run_cmd,
    sha256_file,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()


META_LEAK_RE = re.compile("|".join(LEAK_REGEX_PATTERNS), re.IGNORECASE)


def _mk_check(
    cid: str,
    name: str,
    status: str,
    measured: str,
    threshold: str,
) -> Dict[str, Any]:
    st = status if status in ("PASS", "WARN", "FAIL", "SKIPPED_UNAVAILABLE", "NOT_REQUESTED") else "FAIL"
    return {
        "id": cid,
        "name": name,
        "status": st,
        # WARN = 提醒但不阻断交付；只有 FAIL 会把片子拦下来
        "passed": (st in ("PASS", "WARN")),
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


def detect_black_intervals(video_path: Path) -> tuple[str, List[str]]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        return "SKIPPED_UNAVAILABLE", []
    result = run_cmd([
        ffmpeg, "-hide_banner", "-nostats", "-i", str(video_path),
        "-vf", "blackdetect=d=0:pix_th=0.07", "-an", "-f", "null", "-",
    ], check=False)
    if result.returncode:
        return "FAIL", []
    hits = re.findall(r"black_start:([0-9.]+)", result.stderr or "")
    return ("FAIL" if hits else "PASS"), hits


def evaluate_cut_boundary_cleanliness(video_path: Path, edl: Dict[str, Any]) -> Dict[str, Any]:
    """片段首尾 NG 帧与切点冻帧拖尾（白盒 EDL 源窗口 + 黑盒成片切点采样）。"""
    from clip_boundary_audit import audit_edl_cut_boundaries

    result = audit_edl_cut_boundaries(str(video_path), edl)
    freeze = result.get("freeze_tail_clips") or []
    ng_edges = result.get("ng_boundary_clips") or []
    if not result.get("ok"):
        status = "FAIL"
    elif result.get("checked_clips") and not result.get("sampled", True):
        status = "SKIPPED_UNAVAILABLE"
    else:
        status = "PASS"
    detail = f"{result.get('checked_clips', 0)} 镜 / 冻帧拖尾 {len(freeze)} 处 / 首尾NG帧 {len(ng_edges)} 处"
    if freeze:
        detail += f" | {freeze[0]['clip_id']} 结尾源窗口缺 {freeze[0]['shortfall_sec']:.2f}s"
    if ng_edges:
        edge_cn = "入点" if ng_edges[0]["edge"] == "head" else "出点"
        detail += f" | {ng_edges[0]['clip_id']} {edge_cn} {ng_edges[0]['reason']} @{ng_edges[0]['time_sec']:.2f}s"
    return _mk_check(
        "cut_boundary_cleanliness",
        "片段首尾无NG帧与切点无冻帧拖尾",
        status,
        detail,
        "0 处源窗口短缺>0.08s；切点±0.20s 无黑闪/糊帧甩镜",
    )


def measure_sync_marker(video_path: Path, start: float, end: float, fps: int) -> Dict[str, Any]:
    """Measure the onset offset of a bright slate flash and a paired test tone."""
    import numpy as np

    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 FFmpeg 测量同步测试信号")
    if not (0 <= start < end and end - start <= 10):
        raise ValueError("同步测试窗须位于成片内且长度不超过 10 秒")
    seek = [ffmpeg, "-v", "error", "-ss", str(start), "-i", str(video_path), "-t", str(end - start)]
    visual = subprocess.check_output([
        *seek, "-an", "-vf", f"fps={fps},scale=16:16,format=gray", "-f", "rawvideo", "-",
    ])
    audio = subprocess.check_output([*seek, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"])
    image_levels = np.frombuffer(visual, dtype=np.uint8).reshape(-1, 256).mean(axis=1)
    samples = np.frombuffer(audio, dtype="<i2").astype(float)
    levels = np.sqrt(np.mean(samples[: len(samples) // 80 * 80].reshape(-1, 80) ** 2, axis=1))
    if len(image_levels) < 3 or len(levels) < 3:
        raise ValueError("同步测试窗内的信号不足")
    background = float(np.percentile(image_levels, 20))
    flash = np.flatnonzero(image_levels > background + 40)
    beep = np.flatnonzero(levels > max(500, np.max(levels) * 0.35))
    if flash.size == 0 or beep.size == 0:
        raise ValueError("未同时测到测试闪帧与声音")
    video_on = start + int(flash[0]) / fps
    audio_on = start + int(beep[0]) / 100
    return {"video_sec": video_on, "audio_sec": audio_on, "offset_sec": audio_on - video_on}


def _effective_dialect(pipeline_dir: Path, dialect: str) -> str:
    """语种以转写实测为准，不以默认值为准。

    2026-11-24 实测：这批口播是粤语，但 `--dialect` 默认 mandarin，于是盲听复核拿只会普通话的
    Whisper 去听粤语，CER 0.366 全部来自「金湾→今晚」这类同音误听 —— 判的是方言差异，不是播控。
    SenseVoice 已经把每句的实际语种记在 asr_lang 里，这里直接采信。
    """
    dl = str(dialect or "").lower()
    if dl in ("cantonese", "yue", "dialect"):
        return dialect
    tr_p = Path(pipeline_dir) / "02_transcript.json"
    if not tr_p.is_file():
        return dialect
    try:
        tr = read_json(tr_p)
        segs = [x for x in (tr.get("segments") or []) if not x.get("is_filler")]
        langs = [str(x.get("asr_lang") or "") for x in segs]
        n_all = len([x for x in langs if x])
        n_yue = len([x for x in langs if x in ("yue", "cantonese")])
        if n_all and n_yue / n_all >= 0.6:
            print(f"[qa] 语种实测：{n_yue}/{n_all} 句是粤语 -> 盲听复核按粤语判（不套普通话 CER）")
            return "cantonese"
    except Exception:
        pass
    return dialect


def inspect_video(
    pipeline_dir: Path,
    video_path: Optional[Path] = None,
    benchmark_path: Optional[Path] = None,
    dialect: str = "mandarin",
    store_anchor: Optional[str] = None,
    strict_asr: bool = False,
    export_mode: str = "auto",
    sync_window: Optional[List[float]] = None,
) -> Dict[str, Any]:
    from workflow_guard import compute_plan_digest, package_digest
    from source_review import source_review_status

    if video_path is None:
        video_path = pipeline_dir / "output_final.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"待检成片不存在: {video_path}")

    plan_sha256 = compute_plan_digest(pipeline_dir)
    video_sha256 = sha256_file(video_path)
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    subs = read_json(pipeline_dir / "05_subtitles.json")
    pkg = read_json(pipeline_dir / "06_packaging.json")

    fps = int(edl.get("fps", DEFAULT_FPS))
    pacing = EDIT_PROFILES.get(edl.get("edit_profile"), EDIT_PROFILES["talking"])
    frame_dur = 1.0 / fps
    checks: List[Dict[str, Any]] = []

    from independent_verifier import extract_audio_context_once, run_blackbox_verification

    with tempfile.TemporaryDirectory(prefix="svs_qa_audio_") as td:
        audio_ctx = extract_audio_context_once(video_path, Path(td))
        r128 = audio_ctx["r128"]
        long_sil = audio_ctx["long_silences"]

        # 1. 响度检查 (-14 LUFS ± LOUDNESS_TOLERANCE_LUFS，与黑盒 #12 同源)
        lufs = float(r128["integrated_lufs"])
        lufs_ok = abs(lufs - TARGET_LUFS) <= LOUDNESS_TOLERANCE_LUFS
        checks.append(_mk_check(
            "loudness",
            f"综合响度 ({TARGET_LUFS:.0f} LUFS)",
            "PASS" if lufs_ok else "FAIL",
            f"{lufs:.2f} LUFS (TP={r128['true_peak_dbtp']} dBTP)",
            f"{TARGET_LUFS - LOUDNESS_TOLERANCE_LUFS:.1f} ~ {TARGET_LUFS + LOUDNESS_TOLERANCE_LUFS:.1f} LUFS",
        ))

        # 2. 黑帧检测
        black_status, black_hits = detect_black_intervals(video_path)
        checks.append(_mk_check(
            "black_frames", "黑帧检测", black_status,
            ", ".join(black_hits[:5]) if black_hits else "未检出" if black_status == "PASS" else "解码失败",
            "0 处非预期黑帧",
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
        start_delta = abs(float((v_s or {}).get("start_time") or 0) - float((a_s or {}).get("start_time") or 0))
        sync_ok = (v_s is not None and a_s is not None and max(dur_diff_sec, start_delta) <= frame_dur + 1e-6 and aroll_sync_ok)
        checks.append(_mk_check(
            "av_sync",
            "音视频流起点与时长 (≤1帧)",
            "PASS" if sync_ok else "FAIL",
            f"起点差 {start_delta*1000:.1f}ms，时长差 {dur_diff_sec*1000:.1f}ms ({dur_diff_frames:.2f} 帧), 同期声锁定={'是' if aroll_sync_ok else '否'}",
            f"≤ 1.0 帧 ({frame_dur*1000:.1f}ms)",
        ))
        if sync_window:
            try:
                marker = measure_sync_marker(video_path, *sync_window, fps)
                offset = marker["offset_sec"]
                status = "PASS" if abs(offset) <= frame_dur + 0.005 else "FAIL"
                detail = f"声音相对闪帧偏移 {offset * 1000:+.1f}ms"
            except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
                status, detail = "FAIL", str(exc)
            checks.append(_mk_check("sync_marker", "闪帧与测试音同步", status, detail, "≤1 帧，计入 5ms 测量误差"))

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
            merged_ids = sub.get("merged_sentence_ids")
            if isinstance(merged_ids, list) and len(merged_ids) > 1 and all(mid in sents_by_id for mid in merged_ids):
                ref_group = [sents_by_id[mid] for mid in merged_ids]
                s_st = min(float(rs.get("start_sec", 0.0)) for rs in ref_group)
                s_en = max(float(rs.get("end_sec", 0.0)) for rs in ref_group)
                ref_words = [w for rs in ref_group for w in (rs.get("words") or [])]
            else:
                ref_sent = sents_by_id.get(sid)
                if not ref_sent:
                    sub_sync_violations.append(f"{sub.get('id')}: 未关联有效 03_audio_events 句子 {sid}")
                    continue
                s_st = float(ref_sent.get("start_sec", 0.0))
                s_en = float(ref_sent.get("end_sec", 0.0))
                ref_words = ref_sent.get("words") or []
            if st_sub < s_st - 0.08 or en_sub > s_en + 0.12:
                overrun = max(s_st - st_sub, en_sub - s_en, 0.0)
                max_sub_err_sec = max(max_sub_err_sec, overrun)
                sub_sync_violations.append(f"{sub.get('id')}: 超出所属锁定句窗 [{s_st:.2f},{s_en:.2f}]")

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
        all_subs_entries = subs.get("entries", [])
        all_suppressed_b9 = (
            len(all_subs_entries) > 0
            and len(active_subs) == 0
            and int(subs.get("suppressed_count", 0)) >= len(all_subs_entries)
        )
        sub_sync_ok = (len(active_subs) > 0 or len(sents_all) == 0 or all_suppressed_b9) and len(sub_sync_violations) == 0 and sub_err_frames <= 1.5
        checks.append(_mk_check(
            "subtitle_sync",
            "字幕与 03_audio_events 语音时间戳交叉对齐",
            "PASS" if sub_sync_ok else "FAIL",
            f"最大交叉偏差 {max_sub_err_sec*1000:.1f}ms ({sub_err_frames:.2f} 帧), 越窗 {len(sub_sync_violations)} 处",
            "≤ 1.5 帧且 0 越出锁定句时间窗",
        ))

        # 5b. 动态花字舞台检验（当风格由 kinetic_stage 接管口播表达时）
        ks_block = pkg.get("kinetic_stage") or {}
        if ks_block.get("enabled"):
            ks_items = list(ks_block.get("items") or [])
            ks_by_id = {str(it.get("id")): it for it in ks_items if it.get("id")}
            sfx_errs_ms = []
            for c_s in (pkg.get("sfx_cues") or []):
                eid = str(c_s.get("event_id") or "")
                if eid in ks_by_id:
                    sfx_errs_ms.append(abs(float(c_s.get("time_sec") or 0.0) - float(ks_by_id[eid].get("start_sec") or 0.0)) * 1000.0)
            max_ks_sfx_ms = max(sfx_errs_ms) if sfx_errs_ms else 0.0
            # 计算动态花字对全片口播时段的覆盖率
            ks_intervals = sorted((float(it.get("start_sec") or 0.0), float(it.get("end_sec") or 0.0)) for it in ks_items if float(it.get("end_sec") or 0.0) > float(it.get("start_sec") or 0.0))
            merged_iv: List[Tuple[float, float]] = []
            for a_k, b_k in ks_intervals:
                if merged_iv and a_k <= merged_iv[-1][1] + 0.05:
                    merged_iv[-1] = (merged_iv[-1][0], max(merged_iv[-1][1], b_k))
                else:
                    merged_iv.append((a_k, b_k))
            speech_cov_sec = 0.0
            speech_total_sec = sum(max(0.0, float(s["end_sec"]) - float(s["start_sec"])) for s in sents_all)
            for s in sents_all:
                ss_k, se_k = float(s["start_sec"]), float(s["end_sec"])
                speech_cov_sec += sum(max(0.0, min(se_k, b_k) - max(ss_k, a_k)) for a_k, b_k in merged_iv)
            ks_cov_ratio = speech_cov_sec / max(0.1, speech_total_sec)
            ks_ok = bool(ks_items) and ks_cov_ratio >= 0.80 and max_ks_sfx_ms <= (frame_dur * 1000.0 + 1.0)
            checks.append(_mk_check(
                "kinetic_stage_sync",
                "动态花字舞台口播覆盖与 0ms 音效咬合",
                "PASS" if ks_ok else "FAIL",
                f"舞台={len(ks_items)} 项, 口播覆盖={ks_cov_ratio*100:.1f}%, 音效最大误差={max_ks_sfx_ms:.1f}ms ({len(sfx_errs_ms)} 条)",
                "覆盖≥80%, 音效偏差≤1帧",
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

        # 6b. 素材头尾 NG 帧与切点干净度（EDL 源窗口冻帧 + 成片切点 ±0.20s 采样）
        checks.append(evaluate_cut_boundary_cleanliness(video_path, edl))

        # 7. UI 安全区 / 540px 真居中对称带 / <=3 字残句 / B9 互斥 / 包装预算
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
            elif isinstance(bbox, dict):
                bx = float(bbox["x"])
                bw = float(bbox["w"])
                cx = bx + bw / 2.0
                if abs(cx - float(SUBTITLE_CENTER_X)) > 4.0:
                    ui_violations.append(
                        f"{sub['id']}: 字幕未水平居中 (中心 X={cx:.1f}px，目标 {SUBTITLE_CENTER_X}±4px)"
                    )
                if bx < SUBTITLE_SYMMETRIC_ZONE["x_min"] or (bx + bw) > SUBTITLE_SYMMETRIC_ZONE["x_max"]:
                    ui_violations.append(
                        f"{sub['id']}: 字幕横越居中对称带 [{SUBTITLE_SYMMETRIC_ZONE['x_min']},{SUBTITLE_SYMMETRIC_ZONE['x_max']}] (实测 [{bx:.0f},{bx+bw:.0f}])"
                    )
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

        ks_block_ui = pkg.get("kinetic_stage") if isinstance(pkg, dict) else {}
        if isinstance(ks_block_ui, dict):
            from common import find_commercial_font as _qa_find_comm_font, find_source_font as _qa_find_src_font
            from step5_subtitles import _measure_text_px as _qa_bb_measure_px
            from step5_subtitles import dp_segment_short_lines as _qa_bb_dp_seg
            _bb_font_cands = ["SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf", "AlimamaShuHeiTi-Bold.otf"]
            _bb_fp = _qa_find_src_font(None, "style_02", _bb_font_cands) or _qa_find_comm_font(_bb_font_cands, verified_only=True) or _qa_find_comm_font()
            ks_items_ui = list(ks_block_ui.get("items") or [])
            bb_items_ui = sorted(
                [
                    it for it in ks_items_ui
                    if it.get("variant") == "bridge_bar"
                    and it.get("z_layer", "foreground") == "foreground"
                    and str(it.get("text_or_asset") or "").strip()
                ],
                key=lambda x: float(x.get("start_sec") or 0.0),
            )
            fg_hi_ui = [
                it for it in ks_items_ui
                if not it.get("base_caption")
                and it.get("variant") not in {"bridge_bar", "editorial_ticker", "pillar_badge"}
                and it.get("z_layer", "foreground") == "foreground"
                and str(it.get("text_or_asset") or "").strip()
            ]
            for bb_idx, bb_it in enumerate(bb_items_ui):
                bb_txt = str(bb_it.get("text_or_asset") or "").strip()
                clean_bb = _strip_punct_for_len(bb_txt)
                if len(clean_bb) > 3:
                    continue
                if re.match(r"^第[一二三四五六七八九十0-9]+[条点步个件]?$", clean_bb):
                    continue
                bb_st = float(bb_it.get("start_sec") or 0.0)
                bb_en = float(bb_it.get("end_sec") or bb_st)
                bb_mid = 0.5 * (bb_st + bb_en)
                ov_sents_bb = [
                    s_i for s_i in sents_all
                    if (
                        min(bb_en, float(s_i.get("end_sec") or 0.0)) - max(bb_st, float(s_i.get("start_sec") or 0.0)) > 0.05
                        or float(s_i.get("start_sec") or 0.0) - 0.05 <= bb_mid <= float(s_i.get("end_sec") or 0.0) + 0.05
                    )
                ]
                bb_sid = str(bb_it.get("sentence_id") or "")
                if not ov_sents_bb and bb_sid and bb_sid in sents_by_id:
                    ov_sents_bb = [sents_by_id[bb_sid]]
                if ov_sents_bb:
                    has_fg_carve = any(
                        min(float(fg_i.get("end_sec") or 0.0), float(s_i.get("end_sec") or 0.0))
                        - max(float(fg_i.get("start_sec") or 0.0), float(s_i.get("start_sec") or 0.0)) > 0.05
                        for s_i in ov_sents_bb
                        for fg_i in fg_hi_ui
                    )
                    if has_fg_carve:
                        continue
                    ref_sent_txt_bb = _strip_punct_for_len("".join(str(s_i.get("text") or "") for s_i in ov_sents_bb))
                else:
                    ref_sent_txt_bb = ""
                if len(ref_sent_txt_bb) >= 5:
                    # 若受 60px / 640px 物理宽度刚性约束（同句前后桥接句合并及重切均超宽或仍留残句），豁免物理不可避免之尾块
                    _bb_avoidable = False
                    _adj_cands: List[str] = []
                    if bb_idx > 0:
                        _p_bb = bb_items_ui[bb_idx - 1]
                        if abs(float(_p_bb.get("end_sec") or 0.0) - bb_st) <= 0.15:
                            _pt = str(_p_bb.get("text_or_asset") or "").strip()
                            if _pt and (_strip_punct_for_len(_pt) + clean_bb) in ref_sent_txt_bb:
                                _adj_cands.append(_pt + bb_txt)
                    if bb_idx + 1 < len(bb_items_ui):
                        _n_bb = bb_items_ui[bb_idx + 1]
                        if abs(float(_n_bb.get("start_sec") or 0.0) - bb_en) <= 0.15:
                            _nt = str(_n_bb.get("text_or_asset") or "").strip()
                            if _nt and (clean_bb + _strip_punct_for_len(_nt)) in ref_sent_txt_bb:
                                _adj_cands.append(bb_txt + _nt)
                    if not _adj_cands:
                        _bb_avoidable = True
                    else:
                        for _cand_adj in _adj_cands:
                            if _qa_bb_measure_px(_cand_adj, _bb_fp, 60)[0] <= 640:
                                _bb_avoidable = True
                                break
                            _dp_c = [t for _, _, t in _qa_bb_dp_seg(_cand_adj, 640, font_path=_bb_fp, font_size=60, max_chars=10, strip_punct=True, strict_width=True)]
                            if _dp_c and all(len(_strip_punct_for_len(t)) > 3 for t in _dp_c):
                                _bb_avoidable = True
                                break
                    if _bb_avoidable:
                        short_subs += 1
                        ui_violations.append(f"{bb_it.get('id', 'bb')}: <=3字残句 '{bb_txt}'")

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
        pkg_sync_err = max(
            [abs(int(e["start_frame"]) - int(e.get("aligned_sfx_or_accent_frame", e["start_frame"]))) for e in vis_elems]
            or [0]
        )
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
            else 0.0
        )
        broll_tgt = float(edl.get("broll_ratio_target") if edl.get("broll_ratio_target") is not None else 0.53)
        if edl.get("style_id") and edl.get("broll_ratio_target") is None:
            from common import load_style_spectrum_entry
            st_ent = load_style_spectrum_entry(str(edl["style_id"]))
            if st_ent and st_ent.get("broll_overlay_ratio_target") is not None:
                broll_tgt = float(st_ent["broll_overlay_ratio_target"])
        if broll_tgt >= 0.995:
            aroll_ok = True
        elif broll_tgt >= 0.85:
            min_aroll_floor = max(0.0, (1.0 - broll_tgt) * 0.40)
            aroll_ok = (aroll_ratio >= min_aroll_floor) if aroll_clips else True
        else:
            aroll_tail_ok = True if not aroll_clips else any(float(c["timeline_out_sec"]) >= total_edl_dur - 10.0 for c in aroll_clips)
            aroll_ok = (aroll_ratio >= 0.18 and aroll_tail_ok) if aroll_clips else True
        dist_ok = (min_d >= 0.30 and max_d <= pacing["max_shot_sec"] and rewind_count == 0 and aroll_ok)
        checks.append(_mk_check(
            "shot_duration_dist",
            "镜头分布 / 零同源倒带 / A-Roll 占比",
            "PASS" if dist_ok else "FAIL",
            f"共{len(durs)}镜 | min={min_d:.2f}s, med={med_d:.2f}s, max={max_d:.2f}s | 倒带={rewind_count}, A-roll={aroll_ratio*100:.1f}%",
            f"0.30s≤单镜≤{pacing['max_shot_sec']:.1f}s, 0非预期倒带",
        ))

        # 8b. 外来画面 ↔ 口播文案：只查"有没有说实话"，不替创作者背锅
        broll_clips = [
            c for c in edl["clips"]
            if str(c.get("track_role")) in ("b_roll", "code_broll") or bool(c.get("b_roll_overlay"))
        ]
        sem_bad = []
        for c in broll_clips:
            rel = c.get("sem_match")
            if not isinstance(rel, dict):
                sem_bad.append(f"{c.get('clip_id')}:未记录语义依据")
                continue
            v = str(rel.get("verdict") or "none")
            if v not in ("hit", "weak", "none"):
                sem_bad.append(f"{c.get('clip_id')}:判定非法({v})")
            elif v != "none" and not (rel.get("basis") or []):
                sem_bad.append(f"{c.get('clip_id')}:声称对得上却没有任何依据")
        stat_sem = edl.get("broll_semantic") or {}
        sem_counts_ok = bool(stat_sem) and int(stat_sem.get("clips", -1)) == len(broll_clips)
        if broll_clips and not stat_sem:
            sem_bad.append("EDL 未写 broll_semantic 汇总")
        checks.append(_mk_check(
            "broll_semantic_fit",
            "外来画面与台词对位（有依据才算对得上）",
            "FAIL" if sem_bad else "PASS",
            f"{len(broll_clips)} 个外来画面 | 贴合 {stat_sem.get('hit', 0)} / 同场景 {stat_sem.get('weak', 0)} / "
            f"无依据 {stat_sem.get('none', 0)} | 命中率 {float(stat_sem.get('hit_ratio', 0.0))*100:.0f}%"
            + (f" | 问题: {sem_bad[:3]}" if sem_bad else ""),
            "每个外来画面都要写清凭什么对得上；无依据必须标 none，不许编",
        ))

        # 8c. 画中画小窗人脸构图：会裁切的构图必须交代到底有没有按人脸取景
        pip_cut_modes = ("geometry_pip_window", "geometry_pip_main_shrunk", "geometry_pip_main_punch_in",
                         "blur_bg_center_card_pip", "smooth_orbit_pip", "black_polaroid_frame_pip",
                         "layered_cutout_over_broll")
        pip_clips = [c for c in edl["clips"] if isinstance(c.get("pip_layout"), dict)]
        cut_clips = [c for c in pip_clips if str((c.get("pip_layout") or {}).get("mode") or "") in pip_cut_modes]
        unaccounted = [
            str(c.get("clip_id")) for c in cut_clips
            if str((c.get("pip_layout") or {}).get("face_framing") or "") not in ("face_center", "no_face_center_crop")
        ]
        face_centered = sum(1 for c in cut_clips if (c.get("pip_layout") or {}).get("face_framing") == "face_center")
        checks.append(_mk_check(
            "pip_face_framing",
            "画中画小窗人脸构图（不切脸）",
            "FAIL" if unaccounted else "PASS",
            f"会裁切的构图 {len(cut_clips)} 个 | 按人脸取景 {face_centered} | 未交代 {len(unaccounted)}"
            + (f" | 缺: {unaccounted[:3]}" if unaccounted else ""),
            "每个小窗/缩窗/推近都要标明是按人脸取景还是素材里根本没脸",
        ))

        # 9. 前 3 秒画面变化次数
        first_3s_cuts = [c for c in edl["clips"][1:] if int(c["timeline_in_frame"]) < 3 * fps]
        all_on_events = all(c.get("cut_event_id") in events_by_id for c in first_3s_cuts)
        first3_ok = len(first_3s_cuts) >= pacing["opening_cuts"] and all_on_events
        checks.append(_mk_check(
            "first_3s_changes",
            "前 3 秒画面变化次数",
            "PASS" if first3_ok else "FAIL",
            f"{len(first_3s_cuts)} 次变化 (对齐事件: {'全部' if all_on_events else '部分缺失'})",
            f"≥ {pacing['opening_cuts']} 次且切点对齐音频事件",
        ))

        # 10. A-Roll 句界严锁（兼容正常 J/L-cut 声画错位延续）
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
            base_end = float(last_s["end_sec"]) + 0.08
            pause_after = max(0.0, float(last_s.get("pause_after_sec", 0.08)))
            src_locked_ok = float(c.get("source_out_sec", 0.0)) <= float(last_s.get("source_out_sec", 99999.0)) + max(0.06, pause_after + 0.06)
            dyn_end = float(last_s["end_sec"]) + pause_after + 0.04 if src_locked_ok else base_end
            # 允许相邻 B-roll L-cut 跨界延申 (<= 0.25s)
            l_lead_allowance = 0.0
            clip_pos = clips_list.index(c)
            if clip_pos + 1 < len(clips_list):
                nxt_j = int(clips_list[clip_pos + 1].get("j_cut_lead_frames", 0))
                if nxt_j < 0:
                    l_lead_allowance = abs(nxt_j) / float(fps) + 0.04
            cut_end_limit = max(base_end, dyn_end) + l_lead_allowance
            if last_s["id"] == sents_all[-1]["id"] and c is clips_list[-1]:
                cut_end_limit = max(cut_end_limit, total_edl_dur)
            overrun = c_out - cut_end_limit
            max_overrun_sec = max(max_overrun_sec, max(0.0, overrun))
            if overrun > 0.04:
                aroll_boundary_violations.append(f"{c['clip_id']} ({last_s['id']}): +{overrun:.2f}s")
            late_in = c_in - float(first_s["start_sec"])
            max_late_in_sec = max(max_late_in_sec, max(0.0, late_in))
            if late_in > 1.20:
                # 句中插入真人镜是风格刀法（style_21 的源草稿本身就是 22.8% A-roll），
                # 只要这一镜的源内位置跟句子源内位置对得上，就是合法的句中小切，不是错位。
                # 判据：时间线偏移 ≈ 源内偏移（≤0.30s），说明它拍的就是这句话的那一段。
                tl_off = c_in - float(first_s["start_sec"])
                src_off = float(c.get("source_in_sec", 0.0)) - float(first_s.get("source_in_sec", 0.0))
                if abs(tl_off - src_off) > 0.30:
                    aroll_boundary_violations.append(
                        f"{c['clip_id']} ({first_s['id']}): 晚入 +{late_in:.2f}s 且源内错位 "
                        f"{abs(tl_off - src_off):.2f}s"
                    )
        checks.append(_mk_check(
            "aroll_sentence_lock",
            "同期声句界契约",
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
        copy_pack_p = pipeline_dir / "09_publish_copy_pack.json"
        if copy_pack_p.exists():
            pj = read_json(copy_pack_p)
            for plat_k, plat_v in (pj.get("platforms") or {}).items():
                if not isinstance(plat_v, dict):
                    continue
                for ti, tline in enumerate(plat_v.get("titles") or []):
                    _scan_text(f"09_copy.{plat_k}.titles[{ti}]", tline)
                _scan_text(f"09_copy.{plat_k}.body", plat_v.get("body"))

        checks.append(_mk_check(
            "forbidden_meta_text",
            "零开发词与风格名称露馅审计",
            "PASS" if len(meta_leaks) == 0 else "FAIL",
            f"检出 {len(meta_leaks)} 处" + (f" ({meta_leaks[0]})" if meta_leaks else ""),
            "0 处元数据/风格代号露馅",
        ))

        # 11b. 10 秒滑窗残留重说与半截起句复查（正式接入 scan_residual_repeats）
        sent_list = audio_events.get("sentences") or []
        rep_scan = scan_residual_repeats(sent_list, window_sec=10.0)
        rep_ok = bool(rep_scan.get("clean", True))
        dup_hits = rep_scan.get("duplicate_phrases") or []
        orph_hits = rep_scan.get("orphan_starts") or []
        checks.append(_mk_check(
            "residual_repeat_scan",
            "成片口播残留重说与半截起句复查",
            "PASS" if rep_ok else "FAIL",
            f"残留重复 {len(dup_hits)} 处 / 孤立半截起句 {len(orph_hits)} 处",
            "10 秒内 0 处意外重复短语、0 处半截起句残留",
        ))

        # 11c. 风格锁：必须 100% 套用所选风格的库标准（用户实测过 Agent 会擅自改风格标准）
        try:
            from style_lock import audit_pipeline as _audit_style_lock
            _sl = _audit_style_lock(pipeline_dir)
            _sl_ok = bool(_sl.get("ok"))
            _sl_detail = (
                f"{_sl.get('style_id') or '未记录'} 完全套用"
                if _sl_ok else
                f"{len(_sl.get('deviations') or [])} 处未经用户同意的偏离："
                + "；".join(f"{d['cn']} 库={d['expected']} 实际={d['actual']}" for d in (_sl.get("deviations") or [])[:3])
            )
            if _sl.get("waived"):
                _sl_detail += f"（已同意偏离：{', '.join(_sl['waived'])}）"
            checks.append(_mk_check(
                "style_lock_intact",
                "风格严格套用（未经同意不得改动）",
                "PASS" if _sl_ok else "FAIL",
                _sl_detail,
                "所选风格的库参数逐项一致，或偏离项都有用户同意的豁免记录",
            ))
        except Exception as exc:  # 风格锁自身出错不能掩盖其它检查
            checks.append(_mk_check(
                "style_lock_intact",
                "风格严格套用（未经同意不得改动）",
                "FAIL",
                f"风格锁校验未跑通：{exc}",
                "所选风格的库参数逐项一致，或偏离项都有用户同意的豁免记录",
            ))

        # 11d. 元素因果规则契合度：核对所选风格该出/不该出元素与数量上限
        try:
            _pk_er = pkg if isinstance(pkg, dict) else {}
            _style_er = str(
                _pk_er.get("style_id")
                or ((edl.get("style_spectrum_applied") or {}).get("style_id") if isinstance(edl, dict) else "")
                or ""
            ).strip()
            if _style_er:
                import element_rules as _er
                if _er.load_rules().get(_style_er):
                    _er_res = _er.audit(pipeline_dir)
                    _er_fail_rows = list(_er_res.get("fail_rows") or [])
                    _er_raw_fails = _er_res.get("fails")
                    _er_fail_cnt = int(_er_raw_fails) if isinstance(_er_raw_fails, int) else len(_er_raw_fails or _er_fail_rows)
                    _er_ok = bool(_er_res.get("passed")) and _er_fail_cnt == 0
                    def _fmt_er(item: Any) -> str:
                        if isinstance(item, dict):
                            el = item.get("element") or ""
                            msg = item.get("metric") or item.get("reason") or item.get("detail") or item.get("rule") or ""
                            act = item.get("actual")
                            if msg and act is not None:
                                return f"{el}.{msg}={act}"
                            if msg:
                                return f"{el}:{msg}"
                            return str(item)
                        return str(item)
                    _er_detail = (
                        f"{_style_er} 全部元素符合因果规则（{len(_er_res.get('rows') or [])} 项）"
                        if _er_ok else
                        f"{_er_fail_cnt} 项偏离：" + "；".join(_fmt_er(x) for x in _er_fail_rows[:3])
                    )
                    checks.append(_mk_check(
                        "element_causality_fit",
                        "元素因果规则契合度",
                        "PASS" if _er_ok else "FAIL",
                        _er_detail,
                        "该出的核心元素不缺席、不该出的克制项不泛滥、密度不越上限",
                    ))
        except Exception as exc:
            checks.append(_mk_check(
                "element_causality_fit",
                "元素因果规则契合度",
                "FAIL",
                f"元素因果规则审计异常：{exc}",
                "该出的核心元素不缺席、不该出的克制项不泛滥、密度不越上限",
            ))

        # 片尾收没收住：话没说完 / 没有行动指引 = 观众划走
        try:
            _pk = json.loads((pipeline_dir / "06_packaging.json").read_text(encoding="utf-8")) \
                if (pipeline_dir / "06_packaging.json").is_file() else {}
            _ep = (_pk.get("ending_plan") or {})
            _verdict = str(_ep.get("verdict") or "unknown")
            if not _ep:
                checks.append(_mk_check(
                    "ending_complete", "片尾收住了，观众知道下一步做什么", "WARN",
                    "这一版包装里没有片尾体检（旧流程跑出来的），重跑 step6 就有了",
                    "末句收住或已挂收尾卡",
                ))
                raise StopIteration
            _card = any(str(e.get("kind")) == "end_card_cta" for e in (_pk.get("visual_elements") or []))
            _fm = _ep.get("framework_map") or {}
            if _verdict == "closed" or _card:
                _st = "PASS"
                _detail = "末句已收住" + ("，并挂了片尾收尾卡" if _card and _verdict != "closed" else "")
            elif _verdict == "soft":
                _st = "WARN"
                _detail = str(_ep.get("reason") or "收尾偏软")
            else:
                _st = "FAIL"
                _detail = str(_ep.get("reason") or "片尾没有收住，也没告诉观众下一步做什么")
            if _fm.get("missing"):
                _detail += f"；台词五格缺：{'、'.join(_fm['missing'])}"
            checks.append(_mk_check(
                "ending_complete",
                "片尾收住了，观众知道下一步做什么",
                _st,
                _detail,
                "末句是转化指令 / 互动提问 / 金句或人设收尾，或已在片尾挂上收尾卡",
            ))
        except StopIteration:
            pass
        except Exception as exc:
            checks.append(_mk_check(
                "ending_complete", "片尾收住了，观众知道下一步做什么", "FAIL",
                f"片尾检查没跑通：{exc}", "末句收住或已挂收尾卡",
            ))

        # 11f. 花字不打架叠字与同屏行数上限检查（跨幕 + 同幕同Y + bridge_bar碰撞 + <=5行）
        ks_items_all = list((ks_block.get("items") or []) if isinstance(ks_block, dict) else [])
        cur_theme = str((ks_block.get("theme_profile") if isinstance(ks_block, dict) else "") or "")
        fg_highlights = [
            it for it in ks_items_all
            if not it.get("base_caption")
            and it.get("variant") not in {"bridge_bar", "editorial_ticker", "pillar_badge"}
            and it.get("z_layer", "foreground") == "foreground"
            and str(it.get("text_or_asset") or "").strip()
        ]
        bb_items = [
            it for it in ks_items_all
            if it.get("variant") == "bridge_bar"
            and it.get("z_layer", "foreground") == "foreground"
            and str(it.get("text_or_asset") or "").strip()
        ]
        cross_overlaps: List[str] = []
        for idx_a, ia in enumerate(fg_highlights):
            ga = str(ia.get("group_id") or ia.get("act_id") or "")
            sa, ea = float(ia.get("start_sec") or 0.0), float(ia.get("end_sec") or 0.0)
            xa, ya = float(ia.get("x_norm") or 0.0), float(ia.get("y_norm") or 0.0)
            if ea <= sa:
                continue
            for ib in fg_highlights[idx_a + 1 :]:
                gb = str(ib.get("group_id") or ib.get("act_id") or "")
                sb, eb = float(ib.get("start_sec") or 0.0), float(ib.get("end_sec") or 0.0)
                ov_sec = min(ea, eb) - max(sa, sb)
                xb, yb = float(ib.get("x_norm") or 0.0), float(ib.get("y_norm") or 0.0)
                if ga and gb and gb != ga and ov_sec > 0.15 and abs(ya - yb) < 0.14:
                    cross_overlaps.append(
                        f"{ia.get('id')}「{ia.get('text_or_asset')}」与{ib.get('id')}「{ib.get('text_or_asset')}」"
                        f"跨幕在 {max(sa, sb):.2f}s 重叠 {ov_sec:.2f}s"
                    )
                elif ov_sec > 0.05 and abs(ya - yb) < 0.06 and abs(xa - xb) < 0.45:
                    cross_overlaps.append(
                        f"{ia.get('id')}「{ia.get('text_or_asset')}」与{ib.get('id')}「{ib.get('text_or_asset')}」"
                        f"同位({ya:+.2f}/{yb:+.2f})在 {max(sa, sb):.2f}s 重叠 {ov_sec:.2f}s"
                    )
            for ib in bb_items:
                sb, eb = float(ib.get("start_sec") or 0.0), float(ib.get("end_sec") or 0.0)
                ov_sec = min(ea, eb) - max(sa, sb)
                yb = float(ib.get("y_norm") or 0.0)
                if ov_sec > 0.05 and abs(ya - yb) < 0.06:
                    cross_overlaps.append(
                        f"{ia.get('id')}「{ia.get('text_or_asset')}」与bridge_bar「{ib.get('text_or_asset')}」"
                        f"在 {max(sa, sb):.2f}s 重叠 {ov_sec:.2f}s"
                    )
        for idx_a, ia in enumerate(bb_items):
            sa, ea = float(ia.get("start_sec") or 0.0), float(ia.get("end_sec") or 0.0)
            xa, ya = float(ia.get("x_norm") or 0.0), float(ia.get("y_norm") or 0.0)
            if ea <= sa:
                continue
            for ib in bb_items[idx_a + 1 :]:
                sb, eb = float(ib.get("start_sec") or 0.0), float(ib.get("end_sec") or 0.0)
                if eb <= sb:
                    continue
                ov_sec = min(ea, eb) - max(sa, sb)
                xb, yb = float(ib.get("x_norm") or 0.0), float(ib.get("y_norm") or 0.0)
                if ov_sec > 0.05 and abs(ya - yb) < 0.06 and abs(xa - xb) < 0.45:
                    cross_overlaps.append(
                        f"bridge_bar「{ia.get('text_or_asset')}」与bridge_bar「{ib.get('text_or_asset')}」"
                        f"同位({ya:+.2f}/{yb:+.2f})在 {max(sa, sb):.2f}s 重叠 {ov_sec:.2f}s"
                    )
        if cur_theme == "style_02_unified" and fg_highlights:
            edge_pts = sorted(
                {float(it.get("start_sec") or 0.0) for it in fg_highlights}
                | {float(it.get("end_sec") or 0.0) for it in fg_highlights}
            )
            for t0, t1 in zip(edge_pts, edge_pts[1:]):
                if t1 - t0 <= 0.04:
                    continue
                mid_t = 0.5 * (t0 + t1)
                sim_fg = [it for it in fg_highlights if float(it.get("start_sec") or 0.0) <= mid_t <= float(it.get("end_sec") or 0.0)]
                if len(sim_fg) > 5:
                    cross_overlaps.append(f"{t0:.2f}..{t1:.2f}s 同时在屏前景花字 {len(sim_fg)} 行 (>5行)")
                    break
        checks.append(_mk_check(
            "overlay_cross_act_overlap",
            "花字不跨幕打架叠字",
            "PASS" if not cross_overlaps else "FAIL",
            "0 处前景花字叠压/超行" if not cross_overlaps else f"{len(cross_overlaps)} 处：" + "；".join(cross_overlaps[:2]),
            "前景花字不跨幕/同位叠压，style_02 同时在屏 <= 5 行",
        ))

        # 11g. 通用字幕与花字桥接句分词自然度检查（序数吞并、助词行首、句中助词/介词/指示代词挂尾、词边界腰斩）
        from step5_subtitles import (
            _ALNUM_MIXED_TOKEN_RE,
            _BAD_SUB_HEAD_AUX,
            _CLAUSE_BOUND_SPLIT_RE,
            _DP_NO_TAIL_CHARS,
            _HYPHEN_ASCII_TOKEN_RE,
            _NUM_UNIT_PRESPLIT_RE,
            _PERSONAL_CLAUSE_PRONOUNS,
            _PRON_TAIL_KEEP_PREV,
            _SUFFIX_DERIV_STEM_POS,
            _SUFFIX_MOD_CHARS,
            _UNIVERSAL_PUBLIC_PLATFORMS,
            _UNIVERSAL_SPOKEN_LEXICON,
            _is_cantonese_ne_demo_head,
            _is_completed_le_clause,
            _is_pronoun_led_clause,
            _is_v_noun_deg_predicate,
            _normalize_pos_tag,
            _pseg_cut_clean,
            _terms_file_for,
            is_compound_np_body,
            is_degree_complement_haoduo,
            is_independent_svo_clause,
            is_predicative_after_headless_de,
            iter_num_unit_spans,
            load_protected_terms,
            tokenize_semantic_words,
        )

        _sub_ordinal_tail_re = re.compile(r"(第[一二三四五六七八九十两\d]+)$")
        _head_aux_allow_pfx = (
            "之前", "之后", "之内", "之外", "之间", "之上", "之下", "之中", "之一", "之类",
            "了解", "了不起", "得到", "着急", "着想", "过了", "过去", "过来", "过年", "过关", "过头", "过分",
            "呢度", "呢批", "呢个", "呢间", "呢啲", "呢种", "呢边", "呢排", "呢次", "呢条", "呢位", "呢家",
            "呢套", "呢份", "呢款", "呢张", "呢只",
        )
        _strict_mid_tails = frozenset("地得之把被从向给俾在喺这那嗰𠮶某每各")
        _strict_mid_prep_chars = frozenset("把被从向给俾在喺")
        _multi_mod_tails = (
            "有啲", "好多", "呢啲", "𠮶啲", "嗰啲", "呢个", "嗰个", "𠮶个",
            "这个", "那个", "哪个", "每个", "某个",
            "𠮶种", "嗰种", "这种", "那种", "呢种", "哪种", "每种", "某种",
        )
        _dir_aspect_allow = ("出来了", "起来了", "下去了", "回来了", "过去了", "进来了", "过来了")

        _QA_NON_CORE_PUNCT_RE = re.compile(
            r"(?<![A-Za-z0-9])\.|(?<=[A-Za-z0-9])\.(?!\d)"
            r"|(?<!\d)[:：](?!\d)|(?<!\d)[:：](?=\d)|(?<=\d)[:：](?!\d)"
            r"|(?<!\d),(?!\d)|(?<!\d),(?=\d)|(?<=\d),(?!\d{3}(?!\d))"
            r"|[\s，。！？、；!?;]+"
        )

        def _qa_plain_text(val: Any) -> str:
            s_val = re.sub(r"(?<=\d)：(?=\d)", ":", str(val or ""))
            return _QA_NON_CORE_PUNCT_RE.sub("", s_val)

        _sent_word_pauses: List[Tuple[str, set[int]]] = []
        for _s_wp in sents_all:
            _ws_wp = _s_wp.get("words") or []
            if not _ws_wp:
                continue
            _buf_wp: List[str] = []
            _p_idxs: set[int] = set()
            _cur_len_wp = 0
            _prev_en_wp: Optional[float] = None
            for _w_wp in _ws_wp:
                _wt_wp = _qa_plain_text(_w_wp.get("word") or "")
                if not _wt_wp:
                    continue
                _wst_wp = float(_w_wp.get("start_sec") or 0.0)
                _wen_wp = float(_w_wp.get("end_sec") or _wst_wp)
                if _prev_en_wp is not None and (_wst_wp - _prev_en_wp) >= 0.20:
                    _p_idxs.add(_cur_len_wp)
                _buf_wp.append(_wt_wp)
                _cur_len_wp += len(_wt_wp)
                _prev_en_wp = _wen_wp
            if _p_idxs:
                _sent_word_pauses.append(("".join(_buf_wp), _p_idxs))

        qa_prot_terms: List[str] = []
        tf_qa = _terms_file_for(pipeline_dir)
        if tf_qa is not None:
            qa_prot_terms.extend(load_protected_terms(tf_qa))
        intel_qa_p = pipeline_dir / "00_raw_intel.json"
        if intel_qa_p.is_file():
            try:
                intel_qa = read_json(intel_qa_p)
                for kt in ("pain_keywords", "terms", "protected_terms", "entities"):
                    for x in (intel_qa.get(kt) or []):
                        xs = str(
                            x.get("name") or x.get("text") or x.get("term") or ""
                            if isinstance(x, dict) else (x or "")
                        ).strip()
                        if "=>" in xs:
                            xs = xs.split("=>", 1)[1].strip()
                        if len(xs) >= 2:
                            qa_prot_terms.append(xs)
            except Exception:
                pass
        qa_prot_all = sorted(
            {t for t in qa_prot_terms if len(t) >= 2} | set(_UNIVERSAL_PUBLIC_PLATFORMS) | _UNIVERSAL_SPOKEN_LEXICON,
            key=len,
            reverse=True,
        )

        def _check_seg_rows(
            rows_to_check: List[Dict[str, Any]],
            label_pfx: str,
            *,
            max_split_gap: float = 0.85,
            token_only: bool = False,
        ) -> List[str]:
            issues: List[str] = []
            for idx_e, cur_e in enumerate(rows_to_check):
                raw_cur = str(cur_e.get("text") or cur_e.get("text_or_asset") or "").strip()
                plain_cur = _qa_plain_text(raw_cur)
                if not plain_cur:
                    continue
                eid = cur_e.get("id") or f"{label_pfx}{idx_e + 1}"
                if not token_only:
                    if re.search(r"(?:[嘅的]\s+|[嘅的][，、；：,;:]\s*|[，、；：,;:]\s*)[佢他她我你]$", raw_cur):
                        issues.append(f"{eid} 句尾跨顿粘连下句主语代词：「{raw_cur}」")
                    if len(plain_cur) >= 6:
                        _m_ord = _sub_ordinal_tail_re.search(plain_cur)
                        if _m_ord:
                            _ord_tok = _m_ord.group(1)
                            _ord_pre = plain_cur[: -len(_ord_tok)]
                            _ord_ends_sent = raw_cur.endswith(("。", "？", "！", "?", "!"))
                            _ord_has_nxt = idx_e + 1 < len(rows_to_check)
                            _ord_pre_tail = _ord_pre[-min(4, len(_ord_pre)) :]
                            _ord_at_sent_end = any(
                                _qa_plain_text(_s_o.get("text") or "").endswith(plain_cur[-min(6, len(plain_cur)) :])
                                and str(_s_o.get("text") or "").strip().endswith(("。", "？", "！", "?", "!"))
                                for _s_o in sents_all
                            )
                            _ord_has_punct_after = any(
                                re.search(
                                    re.escape(_ord_pre_tail) + r"\s*" + re.escape(_ord_tok) + r"\s*[，。！？、；：,;:]",
                                    str(_s_o.get("text") or ""),
                                )
                                for _s_o in sents_all
                            )
                            _ord_cross_glue = any(
                                k_s + 1 < len(sents_all)
                                and _qa_plain_text(sents_all[k_s].get("text") or "").endswith(_ord_pre_tail)
                                and _qa_plain_text(sents_all[k_s + 1].get("text") or "").startswith(_ord_tok)
                                for k_s in range(len(sents_all))
                            )
                            if _ord_cross_glue or (
                                not _ord_ends_sent
                                and not _ord_at_sent_end
                                and _ord_has_nxt
                                and not (_ord_has_punct_after and len(_ord_tok) >= 2)
                            ):
                                issues.append(f"{eid} 末尾跨句粘连序数：「{plain_cur}」")
                    if (
                        plain_cur[0] in _BAD_SUB_HEAD_AUX
                        and not plain_cur.startswith(_head_aux_allow_pfx)
                        and not (plain_cur[0] == "呢" and _is_cantonese_ne_demo_head(plain_cur))
                    ):
                        issues.append(f"{eid} 行首误挂助词「{plain_cur[0]}」：「{plain_cur}」")
                if idx_e + 1 < len(rows_to_check):
                    nxt_e = rows_to_check[idx_e + 1]
                    plain_nxt = _qa_plain_text(nxt_e.get("text") or nxt_e.get("text_or_asset") or "")
                    if not plain_nxt:
                        continue
                    cur_en = float(cur_e.get("end_sec") or 0.0)
                    nxt_st = float(nxt_e.get("start_sec") or 0.0)
                    gap_next = (nxt_st - cur_en) if (cur_en > 0.0 or nxt_st > 0.0) else 0.0
                    sid_cur = str(cur_e.get("sentence_id") or "")
                    sid_nxt = str(nxt_e.get("sentence_id") or "")
                    cur_sids = [str(x) for x in (cur_e.get("merged_sentence_ids") or ([sid_cur] if sid_cur else [])) if x]
                    nxt_sids = [str(x) for x in (nxt_e.get("merged_sentence_ids") or ([sid_nxt] if sid_nxt else [])) if x]
                    def _infer_sent_id(item_d: Dict[str, Any]) -> str:
                        st_i = float(item_d.get("start_sec") or 0.0)
                        en_i = float(item_d.get("end_sec") or st_i)
                        if en_i <= st_i:
                            return ""
                        mid_i = 0.5 * (st_i + en_i)
                        for s_i in sents_all:
                            s_st = float(s_i.get("start_sec") or 0.0)
                            s_en = float(s_i.get("end_sec") or s_st)
                            if s_st - 0.05 <= mid_i <= s_en + 0.05:
                                return str(s_i.get("id") or "")
                        return ""

                    def _infer_sent_sids(item_d: Dict[str, Any]) -> List[str]:
                        st_i = float(item_d.get("start_sec") or 0.0)
                        en_i = float(item_d.get("end_sec") or st_i)
                        if en_i > st_i:
                            hits = [
                                str(s_i.get("id") or "")
                                for s_i in sents_all
                                if s_i.get("id")
                                and min(en_i, float(s_i.get("end_sec") or 0.0)) - max(st_i, float(s_i.get("start_sec") or 0.0)) > 0.05
                            ]
                            if hits:
                                return hits
                        sid_fb = _infer_sent_id(item_d)
                        return [sid_fb] if sid_fb else []

                    eff_cur_sids = cur_sids or _infer_sent_sids(cur_e)
                    eff_nxt_sids = nxt_sids or _infer_sent_sids(nxt_e)
                    eff_sid_cur = sid_cur or (eff_cur_sids[0] if eff_cur_sids else "")
                    eff_sid_nxt = sid_nxt or (eff_nxt_sids[0] if eff_nxt_sids else "")
                    same_sent = (not eff_cur_sids) or (not eff_nxt_sids) or bool(set(eff_cur_sids) & set(eff_nxt_sids))
                    same_sent_strict = bool(sid_cur and sid_nxt and sid_cur == sid_nxt)
                    diff_sent_strict = bool(
                        eff_cur_sids
                        and eff_nxt_sids
                        and eff_cur_sids[-1] != eff_nxt_sids[0]
                        and not (set(eff_cur_sids) & set(eff_nxt_sids))
                    )
                    raw_sent_ends_punct = False
                    if diff_sent_strict:
                        ref_cur_sent = sents_by_id.get(eff_cur_sids[-1]) or {}
                        raw_sent_ends_punct = str(ref_cur_sent.get("text") or "").strip().endswith(("。", "？", "！", "?", "!"))
                    def _cur_has_transcript_punct() -> bool:
                        if re.search(r"\d[，,:：]$", raw_cur):
                            return False
                        has_trailing_punct = raw_cur.endswith(("、", "；", ";", "。", "？", "！", "?", "!", "，", ",", "：", ":"))
                        if not sents_all:
                            return has_trailing_punct
                        tail_chars = plain_cur[-min(4, len(plain_cur)):]
                        head_chars = plain_nxt[:min(3, len(plain_nxt))]
                        tail_p = r"\s*".join(re.escape(ch) for ch in tail_chars)
                        head_n = r"\s*".join(re.escape(ch) for ch in head_chars)
                        if same_sent_strict or eff_cur_sids:
                            ref_cur_sent = sents_by_id.get(eff_cur_sids[-1]) or {} if eff_cur_sids else {}
                            ref_sent_text = re.sub(r"(?<=\d)：(?=\d)", ":", str(ref_cur_sent.get("text") or ""))
                            if ref_sent_text and head_n:
                                if re.search(
                                    tail_p + r"\s*(?:[，。！？、；;?!]|[:：,](?!\d))\s*" + head_n,
                                    ref_sent_text,
                                ):
                                    return True
                                if diff_sent_strict and eff_nxt_sids:
                                    ref_nxt_sent = sents_by_id.get(eff_nxt_sids[0]) or {}
                                    ref_nxt_text = re.sub(r"(?<=\d)：(?=\d)", ":", str(ref_nxt_sent.get("text") or ""))
                                    if (
                                        ref_nxt_text
                                        and re.search(tail_p + r"\s*(?:[，。！？、；;?!]|[:：,](?!\d))\s*$", ref_sent_text.strip())
                                        and re.match(r"^\s*" + head_n, ref_nxt_text)
                                    ):
                                        return True
                        elif head_n:
                            pat_join = re.compile(tail_p + r"\s*(?:[，。！？、；;?!]|[:：,](?!\d))\s*" + head_n)
                            for _s_ref in sents_all:
                                _t_ref = re.sub(r"(?<=\d)：(?=\d)", ":", str(_s_ref.get("text") or ""))
                                if _t_ref and pat_join.search(_t_ref):
                                    return True
                        return False

                    cur_has_transcript_punct = _cur_has_transcript_punct()
                    cur_ends_sent_punct = raw_sent_ends_punct or (
                        raw_cur.endswith(("。", "？", "！", "?", "!")) and (not sents_all or cur_has_transcript_punct)
                    )

                    def _is_split_ne_na_tail() -> bool:
                        if cur_ends_sent_punct:
                            return False
                        tail_ch = plain_cur[-1]
                        if tail_ch not in {"呢", "哪"}:
                            return False
                        if tail_ch == "呢":
                            if any(q in plain_cur for q in ("为什么", "怎么", "点解", "凭什么", "难道", "什么", "乜嘢", "吗", "为啥", "为何")):
                                return False
                            if cur_has_transcript_punct:
                                return False
                            if (
                                (same_sent_strict or gap_next <= 0.15)
                                and (
                                    plain_nxt[0] in "度批个间啲种边排次条位家套份款张只"
                                    or plain_nxt.startswith(("一个", "一间", "一批", "一种", "一套", "一份", "一次", "一条"))
                                )
                                and _is_cantonese_ne_demo_head("呢" + plain_nxt)
                            ):
                                return True
                            return False
                        # tail_ch == "哪"
                        if same_sent_strict:
                            return True
                        if gap_next <= 0.15:
                            if plain_nxt[0] in "个间批啲种边排次条位家套份款张只度里些样":
                                return True
                            jn_na = _pseg_cut_clean("哪" + plain_nxt[:3], prot_terms=qa_prot_all)
                            if jn_na and jn_na[0][0].startswith("哪") and len(jn_na[0][0]) >= 2:
                                return True
                        return False

                    def _extract_post_punct_clause() -> str:
                        if not sents_all:
                            return ""
                        tail_chars = plain_cur[-min(4, len(plain_cur)):]
                        head_chars = plain_nxt[:min(3, len(plain_nxt))]
                        if not tail_chars or not head_chars:
                            return ""
                        tail_p = r"\s*".join(re.escape(ch) for ch in tail_chars)
                        head_n = r"\s*".join(re.escape(ch) for ch in head_chars)
                        pat_post = re.compile(
                            tail_p + r"\s*(?:[，。！？、；;?!]|[:：,](?!\d))\s*(" + head_n + r"[^，。！？；;!?]*)"
                        )
                        ref_texts: List[str] = []
                        if eff_cur_sids and eff_cur_sids[-1] in sents_by_id:
                            ref_texts.append(str((sents_by_id[eff_cur_sids[-1]] or {}).get("text") or ""))
                        for _s_ref in sents_all:
                            _t_ref = str(_s_ref.get("text") or "")
                            if _t_ref and _t_ref not in ref_texts:
                                ref_texts.append(_t_ref)
                        for _rt in ref_texts:
                            _rt_norm = re.sub(r"(?<=\d)：(?=\d)", ":", _rt)
                            _m_post = pat_post.search(_rt_norm)
                            if _m_post:
                                _cl = _qa_plain_text(_m_post.group(1))
                                if _cl.startswith(plain_nxt):
                                    return _cl
                        return ""

                    _cand_refs: List[str] = []
                    if eff_cur_sids and eff_cur_sids[-1] in sents_by_id:
                        _cand_refs.append(str((sents_by_id[eff_cur_sids[-1]] or {}).get("text") or ""))
                    if eff_nxt_sids and eff_nxt_sids[0] in sents_by_id:
                        _nxt_ref_t = str((sents_by_id[eff_nxt_sids[0]] or {}).get("text") or "")
                        if _nxt_ref_t not in _cand_refs:
                            _cand_refs.append(_nxt_ref_t)
                    if not _cand_refs and sents_all:
                        _cand_refs = [str(_s_r.get("text") or "") for _s_r in sents_all if _s_r.get("text")]

                    if not token_only and gap_next <= max_split_gap:
                        _is_strict_mid_tail_hit = False
                        if plain_cur[-1] in _strict_mid_tails and not cur_ends_sent_punct:
                            _cur_pairs_t = _pseg_cut_clean(plain_cur, prot_terms=qa_prot_all)
                            _lw, _lf = _cur_pairs_t[-1] if _cur_pairs_t else ("", "")
                            if plain_cur[-1] in _strict_mid_prep_chars:
                                _is_strict_mid_tail_hit = (_lf == "p") or (len(_lw) == 1 and _lf != "n")
                            else:
                                _is_strict_mid_tail_hit = (len(_lw) == 1) or (_lf in {"u", "ud", "uv", "uz", "r"})
                        if _is_strict_mid_tail_hit:
                            issues.append(f"{eid} 句中虚词/介词/指示代词挂尾：「{plain_cur}」")
                        elif _is_split_ne_na_tail():
                            issues.append(f"{eid} 句中虚词/介词/指示代词挂尾：「{plain_cur}」")
                        elif plain_cur.endswith(_multi_mod_tails) and not cur_ends_sent_punct:
                            _is_haoduo_deg = plain_cur.endswith(("好多", "有啲")) and is_degree_complement_haoduo(
                                plain_cur, plain_nxt, prot_terms=qa_prot_all
                            )
                            _haoduo_v_le = bool(re.search(r"[了咗得]好多$", plain_cur) and len(plain_cur) >= 4)
                            _post_clause_ok = False
                            if cur_has_transcript_punct and _haoduo_v_le and not _is_haoduo_deg:
                                if _is_pronoun_led_clause(plain_nxt, prot_terms=qa_prot_all):
                                    _post_clause_ok = True
                                else:
                                    _full_post_cl = _extract_post_punct_clause()
                                    if _full_post_cl and (
                                        is_independent_svo_clause(_full_post_cl, prot_terms=qa_prot_all, prev_plain=plain_cur)
                                        or _is_pronoun_led_clause(_full_post_cl, prot_terms=qa_prot_all)
                                    ):
                                        _post_clause_ok = True
                            _is_clause_punct_ok = cur_has_transcript_punct and (
                                not plain_cur.endswith("好多")
                                or _is_haoduo_deg
                                or _post_clause_ok
                                or is_independent_svo_clause(plain_nxt, prot_terms=qa_prot_all, prev_plain=plain_cur)
                            )
                            if not _is_haoduo_deg and not _is_clause_punct_ok:
                                issues.append(f"{eid} 句中修饰语挂尾：「{plain_cur}」")
                        elif (
                            gap_next <= 0.25
                            and len(plain_cur) >= 4
                            and plain_cur[-1] in {"我", "你", "他", "她", "佢"}
                            and not cur_ends_sent_punct
                            and not cur_has_transcript_punct
                            and not any(plain_cur[:-1].endswith(k) for k in _PRON_TAIL_KEEP_PREV)
                            and not plain_nxt.startswith(("的", "嘅", "哋", "们", "自己", "也", "都"))
                        ):
                            issues.append(f"{eid} 句尾误挂下句主语代词「{plain_cur[-1]}」：「{plain_cur}｜{plain_nxt}」")
                        elif (
                            gap_next <= 0.25
                            and len(plain_cur) >= 4
                            and not cur_ends_sent_punct
                            and not cur_has_transcript_punct
                            and not plain_nxt.startswith(("的", "嘅", "哋", "们"))
                            and any(
                                plain_cur.endswith(("好多" + _pr, "有啲" + _pr))
                                and (
                                    is_degree_complement_haoduo(plain_cur[: -len(_pr)], _pr + plain_nxt, prot_terms=qa_prot_all)
                                    or _is_pronoun_led_clause(_pr + plain_nxt, prot_terms=qa_prot_all)
                                )
                                for _pr in sorted(_PERSONAL_CLAUSE_PRONOUNS, key=len, reverse=True)
                            )
                        ):
                            _sw_pr = next(
                                _pr for _pr in sorted(_PERSONAL_CLAUSE_PRONOUNS, key=len, reverse=True)
                                if plain_cur.endswith(("好多" + _pr, "有啲" + _pr))
                            )
                            issues.append(f"{eid} 句尾误挂下句主语代词「{_sw_pr}」：「{plain_cur}｜{plain_nxt}」")
                        elif (
                            (same_sent and gap_next <= 0.35 or gap_next <= 0.12)
                            and plain_cur[-1] in {"的", "嘅"}
                            and not cur_ends_sent_punct
                            and not plain_cur.endswith(("出来的", "起来的", "下去的", "过来的", "过去的"))
                        ):
                            pairs_jn = _pseg_cut_clean(plain_cur + plain_nxt, prot_terms=qa_prot_all)
                            cur_pos_p = 0
                            pa_j = ""
                            for wp, fp in pairs_jn:
                                if cur_pos_p == len(plain_cur):
                                    pa_j = _normalize_pos_tag(wp, fp)
                                    break
                                cur_pos_p += len(wp)
                            _ref_nxt_pl = ""
                            if diff_sent_strict and eff_nxt_sids:
                                _ref_nxt_pl = _qa_plain_text((sents_by_id.get(eff_nxt_sids[0]) or {}).get("text") or "")
                            _nxt_is_clause_de = bool(
                                is_independent_svo_clause(plain_nxt, prot_terms=qa_prot_all, prev_plain=plain_cur)
                                or is_predicative_after_headless_de(plain_cur, plain_nxt, prot_terms=qa_prot_all)
                                or (
                                    diff_sent_strict
                                    and _ref_nxt_pl
                                    and _ref_nxt_pl.startswith(plain_nxt)
                                    and (
                                        is_independent_svo_clause(_ref_nxt_pl, prot_terms=qa_prot_all, prev_plain=plain_cur)
                                        or is_predicative_after_headless_de(plain_cur, _ref_nxt_pl, prot_terms=qa_prot_all)
                                    )
                                )
                            )
                            _cur_de_has_pred = any(
                                fp in {"v", "vg", "vd", "l"}
                                for _wp, fp in _pseg_cut_clean(plain_cur[:-1], prot_terms=qa_prot_all)
                            )
                            _v_noun_or_unpred_de = bool(
                                pa_j == "v"
                                and (
                                    _is_v_noun_deg_predicate(plain_nxt, prot_terms=qa_prot_all)
                                    or (
                                        not _cur_de_has_pred
                                        and not plain_nxt.startswith(("是", "系", "有", "会", "能", "要", "可以", "应该", "变成", "成为", "值得", "交给"))
                                        and not any(wp in ("得", "了", "咗", "着", "过", "起来", "下去") for wp, _ in _pseg_cut_clean(plain_nxt, prot_terms=qa_prot_all)[1:3])
                                    )
                                )
                            )
                            if (
                                (pa_j in {"n", "nr", "ns", "nt", "nz", "vn", "s", "f", "m", "mq", "q", "a", "i", "l"} or _v_noun_or_unpred_de)
                                and not _nxt_is_clause_de
                            ):
                                issues.append(f"{eid} 定语「{plain_cur[-1]}」后接名词被腰斩：「{plain_cur}｜{plain_nxt}」")
                        elif (
                            (same_sent and gap_next <= 0.35 or gap_next <= 0.12)
                            and plain_cur[-1] in {"了", "咗", "着", "过"}
                            and not cur_ends_sent_punct
                            and not cur_has_transcript_punct
                            and not plain_cur.endswith(_dir_aspect_allow)
                        ):
                            pairs_jn = _pseg_cut_clean(plain_cur + plain_nxt, prot_terms=qa_prot_all)
                            cur_pos_p = 0
                            pa_j = ""
                            for wp, fp in pairs_jn:
                                if cur_pos_p == len(plain_cur):
                                    pa_j = _normalize_pos_tag(wp, fp)
                                    break
                                cur_pos_p += len(wp)
                            _ref_nxt_pl = ""
                            if diff_sent_strict and eff_nxt_sids:
                                _ref_nxt_pl = _qa_plain_text((sents_by_id.get(eff_nxt_sids[0]) or {}).get("text") or "")
                            _nxt_is_svo = bool(
                                is_independent_svo_clause(plain_nxt, prot_terms=qa_prot_all, prev_plain=plain_cur)
                                or (
                                    diff_sent_strict
                                    and _ref_nxt_pl
                                    and _ref_nxt_pl.startswith(plain_nxt)
                                    and is_independent_svo_clause(_ref_nxt_pl, prot_terms=qa_prot_all, prev_plain=plain_cur)
                                )
                            )
                            _nxt_head_cl = _CLAUSE_BOUND_SPLIT_RE.split(plain_nxt, 1)[0]
                            _b_a_np_obj = bool(
                                pa_j in {"b", "a"}
                                and not _is_completed_le_clause(plain_cur, prot_terms=qa_prot_all)
                                and (
                                    is_compound_np_body(_nxt_head_cl, prot_terms=qa_prot_all, prev_plain=plain_cur)
                                    or (pa_j == "b" and not _is_v_noun_deg_predicate(_nxt_head_cl, prot_terms=qa_prot_all))
                                )
                            )
                            if (
                                (pa_j in {"m", "mq", "q", "n", "nr", "ns", "nt", "nz", "vn"} or _b_a_np_obj)
                                and not _nxt_is_svo
                            ):
                                nxt_pairs_j = _pseg_cut_clean(plain_nxt, prot_terms=qa_prot_all)
                                first_np_len = len(nxt_pairs_j[0][0]) if nxt_pairs_j else 0
                                first_np_pos = _normalize_pos_tag(nxt_pairs_j[0][0], nxt_pairs_j[0][1]) if nxt_pairs_j else ""
                                is_num_head_np_j = first_np_pos in {"m", "mq", "q"}
                                is_heavy_mod_np = (
                                    len(plain_nxt) >= 8
                                    and ("的" in plain_nxt[2:] or "嘅" in plain_nxt[2:])
                                    and not plain_nxt.endswith(("的", "嘅"))
                                    and (
                                        (
                                            is_num_head_np_j
                                            and len(nxt_pairs_j) >= 2
                                            and nxt_pairs_j[1][0] in {"的", "嘅"}
                                            and ((len(plain_cur) >= 3 and first_np_len >= 4) or (len(plain_cur) >= 5 and first_np_len >= 3))
                                        )
                                        or (not is_num_head_np_j and len(plain_cur) >= 3 and first_np_len >= 4)
                                    )
                                )
                                if not is_heavy_mod_np:
                                    issues.append(f"{eid} 动貌「{plain_cur[-1]}」与宾语数量断开：「{plain_cur}｜{plain_nxt}」")
                        if not cur_ends_sent_punct and _cand_refs and len(plain_cur) >= 3:
                            for _k_sw in range(max(1, len(plain_cur) - 4), len(plain_cur)):
                                _pre_c = plain_cur[:_k_sw]
                                _post_hd = plain_cur[_k_sw:]
                                _pre_pat = r"\s*".join(re.escape(_ch_x) for _ch_x in _pre_c[-min(4, len(_pre_c)):])
                                _hd_pat = r"\s*".join(re.escape(_ch_x) for _ch_x in (_post_hd + plain_nxt[:min(2, len(plain_nxt))]))
                                _pat_sw = re.compile(_pre_pat + r"\s*[，、；：,;:]\s*(" + _hd_pat + r"[^，。！？；;!?]*)")
                                _post_cl_sw = ""
                                for _rt_sw in _cand_refs:
                                    _m_sw = _pat_sw.search(re.sub(r"(?<=\d)：(?=\d)", ":", _rt_sw))
                                    if _m_sw:
                                        _post_cl_sw = _qa_plain_text(_m_sw.group(1))
                                        break
                                if not _post_cl_sw:
                                    continue
                                _is_pron_sw = _post_hd in _PERSONAL_CLAUSE_PRONOUNS and _is_pronoun_led_clause(_post_cl_sw, prot_terms=qa_prot_all)
                                _is_haoduo_or_le_sw = bool(
                                    (
                                        _pre_c.endswith(("好多", "有啲"))
                                        and (
                                            is_degree_complement_haoduo(_pre_c, _post_cl_sw, prot_terms=qa_prot_all)
                                            or _is_pronoun_led_clause(_post_cl_sw, prot_terms=qa_prot_all)
                                            or is_independent_svo_clause(_post_cl_sw, prot_terms=qa_prot_all, prev_plain=_pre_c)
                                        )
                                    )
                                    or (
                                        _is_completed_le_clause(_pre_c, prot_terms=qa_prot_all)
                                        and (
                                            _is_pronoun_led_clause(_post_cl_sw, prot_terms=qa_prot_all)
                                            or is_independent_svo_clause(_post_cl_sw, prot_terms=qa_prot_all, prev_plain=_pre_c)
                                        )
                                    )
                                )
                                _is_svo_non_np_sw = bool(
                                    is_independent_svo_clause(_post_cl_sw, prot_terms=qa_prot_all, prev_plain=_pre_c)
                                    and not is_compound_np_body(_post_hd, prot_terms=qa_prot_all, prev_plain=_pre_c)
                                )
                                if _is_pron_sw or _is_haoduo_or_le_sw or _is_svo_non_np_sw:
                                    issues.append(f"{eid} 跨逗号吞并下句主语「{_post_hd}」：「{plain_cur}｜{plain_nxt}」")
                                    break

                    _ascii_space_separated = False
                    _ascii_joined_tok = ""
                    _ascii_has_joined_ref = False
                    _dot_split_tok = ""
                    _colon_split_tok = ""
                    _hyphen_split_tok = ""
                    _hyphen_space_separated = False
                    _hyphen_has_joined_ref = False

                    _raw_nxt = str(nxt_e.get("text") or nxt_e.get("text_or_asset") or "").strip()
                    _rc_no_zh = re.sub(r"[，。！？、；!?;,]+$", "", raw_cur)
                    _rn_no_zh = re.sub(r"^[，。！？、；!?;,]+", "", _raw_nxt)
                    _m_dl = re.search(r"([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)(\.?)$", _rc_no_zh)
                    _m_dr = re.match(r"^(\.?)([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)", _rn_no_zh)
                    if _m_dl and _m_dr and (_m_dl.group(2) == "." or _m_dr.group(1) == ".") and not cur_ends_sent_punct:
                        _cand_dot = f"{_m_dl.group(1)}.{_m_dr.group(2)}"
                        _has_dot_sp = any(
                            re.search(re.escape(_m_dl.group(1)) + r"\.\s+" + re.escape(_m_dr.group(2)), _rt)
                            for _rt in _cand_refs
                        )
                        if not _has_dot_sp and (
                            _ALNUM_MIXED_TOKEN_RE.fullmatch(_cand_dot)
                            or any(_cand_dot in _rt for _rt in _cand_refs)
                        ):
                            _dot_split_tok = _cand_dot
                    _rc_col = re.sub(r"(?<=\d)：$", ":", re.sub(r"[，。！？、；!?;,\s]+$", "", raw_cur))
                    _rn_col = re.sub(r"^：(?=\d)", ":", re.sub(r"^[，。！？、；!?;,\s]+", "", _raw_nxt))
                    _m_cl = re.search(r"(\d+)(:?)$", _rc_col)
                    _m_cr = re.match(r"^(:?)(\d+)", _rn_col)
                    if _m_cl and _m_cr and (_m_cl.group(2) == ":" or _m_cr.group(1) == ":") and not cur_ends_sent_punct:
                        _cand_colon = f"{_m_cl.group(1)}:{_m_cr.group(2)}"
                        _has_col_sp = any(
                            re.search(re.escape(_m_cl.group(1)) + r"[:：]\s+" + re.escape(_m_cr.group(2)), _rt)
                            for _rt in _cand_refs
                        )
                        if not _has_col_sp and any(_cand_colon in re.sub(r"(?<=\d)：(?=\d)", ":", _rt) for _rt in _cand_refs):
                            _colon_split_tok = _cand_colon
                    if (
                        not _dot_split_tok
                        and not _colon_split_tok
                        and plain_cur[-1].isascii()
                        and plain_cur[-1].isalnum()
                        and plain_nxt[0].isascii()
                        and plain_nxt[0].isalnum()
                    ):
                        _raw_cur_trim = re.sub(r"[，。！？、；!?;,.]+$", "", raw_cur)
                        _raw_nxt_trim = re.sub(r"^[，。！？、；!?;,.]+", "", _raw_nxt)
                        _m_ta = re.search(r"[A-Za-z0-9]+(?:\.[0-9]+)*$", _raw_cur_trim)
                        _m_ha = re.match(r"^[A-Za-z0-9]+(?:\.[0-9]+)*", _raw_nxt_trim)
                        if _m_ta and _m_ha:
                            _ta, _ha = _m_ta.group(0), _m_ha.group(0)
                            _ascii_joined_tok = _ta + _ha
                            _has_sp_ref = any(re.search(re.escape(_ta) + r"\s+" + re.escape(_ha), _rt) for _rt in _cand_refs)
                            _has_joined_ref = any(re.search(re.escape(_ta + _ha), _rt) for _rt in _cand_refs)
                            _ascii_has_joined_ref = _has_joined_ref
                            if _has_sp_ref and not _has_joined_ref:
                                _ascii_space_separated = True
                    else:
                        _m_hl = re.search(r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-$", plain_cur)
                        _m_hr = re.match(r"^([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)", plain_nxt)
                        if not (_m_hl and _m_hr):
                            _m_hl = re.search(r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)$", plain_cur)
                            _m_hr = re.match(r"^-([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)", plain_nxt)
                        if _m_hl and _m_hr:
                            _hl, _hr = _m_hl.group(1), _m_hr.group(1)
                            _hyphen_split_tok = f"{_hl}-{_hr}"
                            _has_sp_hyph = any(
                                re.search(re.escape(_hl) + r"(?:\s+-\s*|-\s+)" + re.escape(_hr), _rt)
                                for _rt in _cand_refs
                            )
                            _has_jn_hyph = any(re.search(re.escape(_hyphen_split_tok), _rt) for _rt in _cand_refs)
                            _hyphen_has_joined_ref = _has_jn_hyph
                            if _has_sp_hyph and not _has_jn_hyph:
                                _hyphen_space_separated = True

                    _any_ascii_sep = _ascii_space_separated or _hyphen_space_separated
                    _tail_q = plain_cur[-min(12, len(plain_cur)):]
                    _head_q = plain_nxt[:min(12, len(plain_nxt))]
                    joint_s = (_tail_q + " " + _head_q) if _any_ascii_sep else (_tail_q + _head_q)
                    cut_k = len(_tail_q)
                    _full_joint = (plain_cur + " " + plain_nxt) if _any_ascii_sep else (plain_cur + plain_nxt)
                    _full_cut_k = len(plain_cur)
                    if gap_next <= max_split_gap and not cur_has_transcript_punct:
                        split_hit: Optional[str] = None
                        for term_p in qa_prot_all:
                            for m_p in re.finditer(re.escape(term_p), _full_joint):
                                if m_p.start() < _full_cut_k < m_p.end():
                                    split_hit = term_p
                                    break
                            if split_hit:
                                break
                        if (
                            not split_hit
                            and _dot_split_tok
                            and (same_sent or gap_next <= 0.15 or any(_dot_split_tok in _rt for _rt in _cand_refs))
                        ):
                            split_hit = _dot_split_tok
                        if (
                            not split_hit
                            and _colon_split_tok
                            and (same_sent or gap_next <= 0.15 or any(_colon_split_tok in re.sub(r"(?<=\d)：(?=\d)", ":", _rt) for _rt in _cand_refs))
                        ):
                            split_hit = _colon_split_tok
                        if not split_hit:
                            for ms_nu, me_nu in iter_num_unit_spans(_full_joint):
                                if ms_nu < _full_cut_k < me_nu:
                                    split_hit = _full_joint[ms_nu:me_nu]
                                    break
                        if not split_hit:
                            for m_alnum in _ALNUM_MIXED_TOKEN_RE.finditer(_full_joint):
                                if m_alnum.start() < _full_cut_k < m_alnum.end():
                                    split_hit = m_alnum.group(0)
                                    break
                        if (
                            not split_hit
                            and plain_cur[-1].isascii()
                            and plain_cur[-1].isalnum()
                            and plain_nxt[0].isascii()
                            and plain_nxt[0].isalnum()
                            and not _ascii_space_separated
                            and (same_sent or gap_next <= 0.15 or _ascii_has_joined_ref)
                        ):
                            split_hit = _ascii_joined_tok or (plain_cur[-1] + plain_nxt[0])
                        if (
                            not split_hit
                            and _hyphen_split_tok
                            and not _hyphen_space_separated
                            and (same_sent or gap_next <= 0.15 or _hyphen_has_joined_ref)
                        ):
                            split_hit = _hyphen_split_tok
                        if not split_hit and not _any_ascii_sep and (same_sent or gap_next <= 0.15):
                            _has_audio_pause_at_cut = gap_next >= 0.20
                            _pause_probe = plain_cur[-min(4, len(plain_cur)):] + plain_nxt[:min(4, len(plain_nxt))]
                            _pause_cut_k = min(4, len(plain_cur))
                            if not _has_audio_pause_at_cut and len(_pause_probe) >= 2:
                                for _sp_plain, _sp_pauses in _sent_word_pauses:
                                    _idx_sp = _sp_plain.find(_pause_probe)
                                    if _idx_sp >= 0 and (_idx_sp + _pause_cut_k) in _sp_pauses:
                                        _has_audio_pause_at_cut = True
                                        break
                            pos_c = 0
                            for tk in tokenize_semantic_words(
                                joint_s,
                                terms=qa_prot_all,
                                pause_breaks=[cut_k] if _has_audio_pause_at_cut else None,
                            ):
                                nxt_c = pos_c + len(tk)
                                if (
                                    len(tk) >= 2
                                    and pos_c < cut_k < nxt_c
                                    and tk[0] not in _BAD_SUB_HEAD_AUX
                                    and tk[-1] not in _BAD_SUB_HEAD_AUX
                                ):
                                    if (
                                        len(tk) >= 3
                                        and nxt_c == cut_k + 1
                                        and plain_nxt.startswith(("我", "你", "他", "她", "佢"))
                                        and tk.endswith(plain_nxt[0])
                                        and tk not in qa_prot_all
                                        and (
                                            diff_sent_strict
                                            or (
                                                len(_pseg_cut_clean(tk[:-1], prot_terms=qa_prot_all)) == 1
                                                and _pseg_cut_clean(tk[:-1], prot_terms=qa_prot_all)[0][1] in {"nr", "n", "nz"}
                                                and (
                                                    (
                                                        len(_pseg_cut_clean(tk, prot_terms=qa_prot_all)) == 1
                                                        and _pseg_cut_clean(tk, prot_terms=qa_prot_all)[0][1] == "nr"
                                                    )
                                                    or (
                                                        len(_pseg_cut_clean(tk, prot_terms=qa_prot_all)) == 2
                                                        and _pseg_cut_clean(tk, prot_terms=qa_prot_all)[1][0] == plain_nxt[0]
                                                        and _pseg_cut_clean(tk, prot_terms=qa_prot_all)[1][1] == "r"
                                                    )
                                                )
                                            )
                                        )
                                    ):
                                        pos_c = nxt_c
                                        continue
                                    if (
                                        len(tk) == 4
                                        and pos_c + 2 == cut_k
                                        and tk not in qa_prot_all
                                        and len(plain_cur) >= 8
                                        and plain_cur[-3] in {"的", "嘅"}
                                        and bool(re.search(
                                            r"(?:[零〇一二两三四五六七八九十百千万亿廿\d%％平间套家斤克元米层项吨箱台]{3,}|[零〇一二两三四五六七八九十百千万亿廿]+分之[零〇一二两三四五六七八九十百千万亿两廿\d]+)[的嘅]$",
                                            plain_cur[:-2],
                                        ))
                                    ):
                                        _tk_ps = _pseg_cut_clean(tk, prot_terms=qa_prot_all)
                                        _tk_l = _pseg_cut_clean(tk[:2], prot_terms=qa_prot_all)
                                        _tk_r = _pseg_cut_clean(tk[2:], prot_terms=qa_prot_all)
                                        if (
                                            len(_tk_ps) == 1
                                            and _tk_ps[0][1] in {"n", "vn"}
                                            and len(_tk_l) == 1
                                            and len(_tk_r) == 1
                                            and _tk_l[0][1] in {"n", "ns", "vn", "nz", "a", "b"}
                                            and _tk_r[0][1] in {"n", "ns", "vn", "nz"}
                                        ):
                                            pos_c = nxt_c
                                            continue
                                    split_hit = tk
                                    break
                                pos_c = nxt_c
                            if not split_hit:
                                _pairs_morph = _pseg_cut_clean(plain_cur + plain_nxt, prot_terms=qa_prot_all)
                                _pos_m = 0
                                for _idx_m, (_wm, _fm) in enumerate(_pairs_morph):
                                    if _pos_m == len(plain_cur) and _idx_m > 0:
                                        _wb_m, _pb_m = _pairs_morph[_idx_m - 1]
                                        _pa_m = _normalize_pos_tag(_wm, _fm)
                                        _pb_m = _normalize_pos_tag(_wb_m, _pb_m)
                                        if (
                                            (_pa_m == "k" and len(_wm) == 1 and _pb_m not in {"", "x", "u", "uj", "ug", "ud", "ul", "uz", "uv", "c", "p"} and _wb_m not in _DP_NO_TAIL_CHARS)
                                            or (_pb_m == "j" and _pa_m in {"g", "ng", "k"} and len(_wb_m) == 1 and len(_wm) == 1)
                                            or (
                                                len(_wm) == 1
                                                and _wm in _SUFFIX_MOD_CHARS
                                                and _pa_m in {"k", "ng", "n", "q", "m"}
                                                and _pb_m in _SUFFIX_DERIV_STEM_POS
                                                and len(_wb_m) >= 2
                                                and _wb_m not in _DP_NO_TAIL_CHARS
                                            )
                                        ):
                                            split_hit = f"{_wb_m}{_wm}"
                                        break
                                    _pos_m += len(_wm)
                        if split_hit:
                            nid = nxt_e.get("id") or f"{label_pfx}{idx_e + 2}"
                            issues.append(f"{eid}/{nid} 腰斩「{split_hit}」")
            return issues

        def _check_fg_highlights(fg_rows: List[Dict[str, Any]]) -> List[str]:
            fg_issues: List[str] = []
            full_plain = _qa_plain_text("".join(str(s_i.get("text") or "") for s_i in sents_all))
            _fg_tail_chars = frozenset("的嘅地得之把被从向给俾在喺和与及跟或这那呢嗰𠮶某每各哪")
            _fg_strict_mid = frozenset("地得之把被从向给俾在喺和与及跟或")
            for idx_fg, fg in enumerate(fg_rows):
                raw_fg = str(fg.get("text_or_asset") or "")
                fid = fg.get("id") or f"fg{idx_fg + 1}"
                if raw_fg != raw_fg.strip():
                    fg_issues.append(f"{fid} 前景花字首尾含多余空格：「{raw_fg}」")
                plain_fg = _qa_plain_text(raw_fg)
                if not plain_fg:
                    continue
                if (
                    plain_fg[0] in _BAD_SUB_HEAD_AUX
                    and not plain_fg.startswith(_head_aux_allow_pfx)
                    and not (plain_fg[0] == "呢" and _is_cantonese_ne_demo_head(plain_fg))
                ):
                    fg_issues.append(f"{fid} 前景花字行首误挂助词「{plain_fg[0]}」：「{plain_fg}」")
                if (
                    plain_fg[-1] in _fg_tail_chars
                    and not plain_fg.endswith(_dir_aspect_allow)
                    and not plain_fg.endswith(("出来的", "起来的", "下去的", "过来的", "过去的"))
                ):
                    _fg_ps = _pseg_cut_clean(plain_fg, prot_terms=qa_prot_all)
                    _lw_f, _lf_f = _fg_ps[-1] if _fg_ps else ("", "")
                    _is_particle_pos = (len(_lw_f) == 1) or (_lf_f in {"u", "uj", "ug", "ud", "ul", "uz", "uv", "p", "c", "r"})
                    if _is_particle_pos:
                        fg_st = float(fg.get("start_sec") or 0.0)
                        fg_en = float(fg.get("end_sec") or fg_st)
                        ov_sents_f = [
                            s_i for s_i in sents_all
                            if min(fg_en + 0.15, float(s_i.get("end_sec") or 0.0)) - max(fg_st - 0.15, float(s_i.get("start_sec") or 0.0)) > 0.0
                        ]
                        at_sent_end = any(_qa_plain_text(s_i.get("text") or "").endswith(plain_fg) for s_i in ov_sents_f)
                        after_clause = ""
                        for s_i in ov_sents_f:
                            s_pl = _qa_plain_text(s_i.get("text") or "")
                            idx_fg_s = s_pl.find(plain_fg)
                            if idx_fg_s >= 0 and idx_fg_s + len(plain_fg) < len(s_pl):
                                after_clause = s_pl[idx_fg_s + len(plain_fg) :]
                                break
                        contains_nonfinal = bool(after_clause)
                        if not at_sent_end and plain_fg[-1] in {"的", "嘅"} and after_clause:
                            pairs_fg_after = _pseg_cut_clean(plain_fg + after_clause, prot_terms=qa_prot_all)
                            pa_after = ""
                            acc_fg_p = 0
                            for wp_a, fp_a in pairs_fg_after:
                                if acc_fg_p == len(plain_fg):
                                    pa_after = _normalize_pos_tag(wp_a, fp_a)
                                    break
                                acc_fg_p += len(wp_a)
                            _fg_de_has_pred = any(
                                fp_a in {"v", "vg", "vd", "l"}
                                for _wp_a, fp_a in _pseg_cut_clean(plain_fg[:-1], prot_terms=qa_prot_all)
                            )
                            _fg_v_noun_or_unpred = bool(
                                pa_after == "v"
                                and (
                                    _is_v_noun_deg_predicate(after_clause, prot_terms=qa_prot_all)
                                    or (
                                        not _fg_de_has_pred
                                        and not after_clause.startswith(("是", "系", "有", "会", "能", "要", "可以", "应该", "变成", "成为", "值得", "交给"))
                                        and not any(wp_a in ("得", "了", "咗", "着", "过", "起来", "下去") for wp_a, _ in _pseg_cut_clean(after_clause, prot_terms=qa_prot_all)[1:3])
                                    )
                                )
                            )
                            _after_is_clause_de = bool(
                                is_independent_svo_clause(after_clause, prot_terms=qa_prot_all, prev_plain=plain_fg)
                                or is_predicative_after_headless_de(plain_fg, after_clause, prot_terms=qa_prot_all)
                            )
                            if (
                                pa_after in {"n", "nr", "ns", "nt", "nz", "vn", "s", "f", "m", "mq", "q", "a", "i", "l"}
                                or _fg_v_noun_or_unpred
                            ) and not _after_is_clause_de:
                                fg_issues.append(f"{fid} 前景花字定语「{plain_fg[-1]}」后接名词腰斩：「{plain_fg}｜{after_clause[:8]}」")
                        elif not at_sent_end and (
                            contains_nonfinal
                            or (plain_fg[-1] in _fg_strict_mid and len(_lw_f) == 1 and _lf_f in {"p", "c", "u", "ud", "uv"})
                        ):
                            fg_issues.append(f"{fid} 前景花字末尾助词/连词挂尾：「{plain_fg}」")
                elif plain_fg.endswith(_multi_mod_tails):
                    fg_st_mm = float(fg.get("start_sec") or 0.0)
                    fg_en_mm = float(fg.get("end_sec") or fg_st_mm)
                    _ov_strict_mm = [
                        s_i for s_i in sents_all
                        if min(fg_en_mm, float(s_i.get("end_sec") or 0.0)) - max(fg_st_mm, float(s_i.get("start_sec") or 0.0)) > 0.05
                    ]
                    ov_sents_mm = _ov_strict_mm if _ov_strict_mm else [
                        s_i for s_i in sents_all
                        if min(fg_en_mm + 0.15, float(s_i.get("end_sec") or 0.0)) - max(fg_st_mm - 0.15, float(s_i.get("start_sec") or 0.0)) > 0.0
                    ]
                    for s_i in ov_sents_mm:
                        _raw_mm = str(s_i.get("text") or "")
                        s_pl_mm = _qa_plain_text(_raw_mm)
                        idx_fg_mm = s_pl_mm.find(plain_fg)
                        if idx_fg_mm < 0:
                            continue
                        left_ctx_mm = s_pl_mm[: idx_fg_mm + len(plain_fg)]
                        after_mm = s_pl_mm[idx_fg_mm + len(plain_fg) :]
                        if not after_mm:
                            continue
                        _after_cl_mm = _CLAUSE_BOUND_SPLIT_RE.split(after_mm, 1)[0]
                        _is_haoduo_deg_mm = plain_fg.endswith(("好多", "有啲")) and is_degree_complement_haoduo(
                            left_ctx_mm, _after_cl_mm, prot_terms=qa_prot_all
                        )
                        _tail_p_mm = r"\s*".join(re.escape(ch) for ch in plain_fg[-min(4, len(plain_fg)):])
                        _head_n_mm = r"\s*".join(re.escape(ch) for ch in after_mm[:min(3, len(after_mm))])
                        _has_punct_mm = bool(_head_n_mm and re.search(
                            _tail_p_mm + r"\s*(?:[，。！？、；;?!]|[:：,](?!\d))\s*" + _head_n_mm,
                            re.sub(r"(?<=\d)：(?=\d)", ":", _raw_mm),
                        ))
                        _post_clause_ok_mm = False
                        if _has_punct_mm and bool(re.search(r"[了咗得]好多$", left_ctx_mm)) and not _is_haoduo_deg_mm:
                            _raw_m_mm = re.search(
                                _tail_p_mm + r"\s*(?:[，。！？、；;?!]|[:：,](?!\d))\s*(" + _head_n_mm + r"[^，。！？；;!?]*)",
                                re.sub(r"(?<=\d)：(?=\d)", ":", _raw_mm),
                            )
                            _full_post_mm = _qa_plain_text(_raw_m_mm.group(1)) if _raw_m_mm else _after_cl_mm
                            if _full_post_mm and (
                                is_independent_svo_clause(_full_post_mm, prot_terms=qa_prot_all, prev_plain=left_ctx_mm)
                                or _is_pronoun_led_clause(_full_post_mm, prot_terms=qa_prot_all)
                            ):
                                _post_clause_ok_mm = True
                        _is_clause_punct_ok_mm = _has_punct_mm and (
                            not plain_fg.endswith("好多")
                            or _is_haoduo_deg_mm
                            or _post_clause_ok_mm
                            or is_independent_svo_clause(_after_cl_mm, prot_terms=qa_prot_all, prev_plain=left_ctx_mm)
                        )
                        if not _is_haoduo_deg_mm and not _is_clause_punct_ok_mm:
                            fg_issues.append(f"{fid} 前景花字句中修饰语挂尾：「{plain_fg}」")
                            break
                fg_st_m = float(fg.get("start_sec") or 0.0)
                fg_en_m = float(fg.get("end_sec") or fg_st_m)
                _ov_strict_m = [
                    s_im for s_im in sents_all
                    if min(fg_en_m, float(s_im.get("end_sec") or 0.0)) - max(fg_st_m, float(s_im.get("start_sec") or 0.0)) > 0.05
                ]
                _ov_cands_m = _ov_strict_m if _ov_strict_m else [
                    s_im for s_im in sents_all
                    if min(fg_en_m + 0.15, float(s_im.get("end_sec") or 0.0)) - max(fg_st_m - 0.15, float(s_im.get("start_sec") or 0.0)) > 0.0
                ]
                for s_im in _ov_cands_m:
                    _raw_sm = re.sub(r"(?<=\d)：(?=\d)", ":", str(s_im.get("text") or ""))
                    s_plm = _qa_plain_text(_raw_sm)
                    _occ_matches = list(re.finditer(re.escape(plain_fg), s_plm))
                    if not _occ_matches:
                        continue
                    _del_spans_m = [m_d.span() for m_d in _QA_NON_CORE_PUNCT_RE.finditer(_raw_sm)]
                    _kept_idx_m: List[int] = []
                    _di_m = 0
                    for _ri_m in range(len(_raw_sm)):
                        while _di_m < len(_del_spans_m) and _ri_m >= _del_spans_m[_di_m][1]:
                            _di_m += 1
                        if _di_m < len(_del_spans_m) and _del_spans_m[_di_m][0] <= _ri_m < _del_spans_m[_di_m][1]:
                            continue
                        _kept_idx_m.append(_ri_m)
                    _nu_spans_m = [(ms_x, me_x, s_plm[ms_x:me_x]) for ms_x, me_x in iter_num_unit_spans(s_plm)]
                    _al_spans_m = (
                        [(m_x.start(), m_x.end(), m_x.group(0)) for m_x in _ALNUM_MIXED_TOKEN_RE.finditer(s_plm)]
                        + [(m_x.start(), m_x.end(), m_x.group(0)) for m_x in _HYPHEN_ASCII_TOKEN_RE.finditer(s_plm)]
                        + [(m_x.start(), m_x.end(), m_x.group(0)) for m_x in re.finditer(r"\d+[:：]\d+", s_plm)]
                    )
                    _morph_cuts_m: Dict[int, str] = {}
                    _fg_m_pairs = _pseg_cut_clean(s_plm, prot_terms=qa_prot_all)
                    _pos_fg_m = 0
                    for _idx_fm, (_tk_fm, _fl_fm) in enumerate(_fg_m_pairs):
                        _norm_fm = _normalize_pos_tag(_tk_fm, _fl_fm)
                        if len(_tk_fm) >= 2 and _norm_fm in {"n", "nr", "ns", "nt", "nz", "vn", "a", "b", "r", "m", "j", "ng"}:
                            for _in_i in range(1, len(_tk_fm)):
                                _l_sub = _tk_fm[:_in_i]
                                _r_sub = _tk_fm[_in_i:]
                                if _l_sub not in _DP_NO_TAIL_CHARS and (
                                    _r_sub in {"们", "哋"}
                                    or _r_sub in _SUFFIX_MOD_CHARS
                                ):
                                    _morph_cuts_m[_pos_fg_m + _in_i] = _tk_fm
                        if _idx_fm > 0:
                            _wb_fm, _fb_fm = _fg_m_pairs[_idx_fm - 1]
                            _pa_fm = _normalize_pos_tag(_tk_fm, _fl_fm)
                            _pb_fm = _normalize_pos_tag(_wb_fm, _fb_fm)
                            if (
                                (_pa_fm == "k" and len(_tk_fm) == 1 and _pb_fm not in {"", "x", "u", "uj", "ug", "ud", "ul", "uz", "uv", "c", "p"} and _wb_fm not in _DP_NO_TAIL_CHARS)
                                or (_pb_fm == "j" and _pa_fm in {"g", "ng", "k"} and len(_wb_fm) == 1 and len(_tk_fm) == 1)
                                or (
                                    len(_tk_fm) == 1
                                    and _tk_fm in _SUFFIX_MOD_CHARS
                                    and _pa_fm in {"k", "ng", "n", "q", "m"}
                                    and _pb_fm in _SUFFIX_DERIV_STEM_POS
                                    and len(_wb_fm) >= 2
                                    and _wb_fm not in _DP_NO_TAIL_CHARS
                                )
                            ):
                                _morph_cuts_m[_pos_fg_m] = f"{_wb_fm}{_tk_fm}"
                        _pos_fg_m += len(_tk_fm)
                    _first_hit_m: Optional[str] = None
                    _has_clean_occ = False
                    for _occ in _occ_matches:
                        _occ_hit: Optional[str] = None
                        for _cut_b in (_occ.start(), _occ.end()):
                            if not (0 < _cut_b < len(s_plm)):
                                continue
                            _between_raw = _raw_sm[_kept_idx_m[_cut_b - 1] + 1 : _kept_idx_m[_cut_b]]
                            if re.search(r"[\s，。！？、；,;!?]", _between_raw):
                                continue
                            for _sp_s, _sp_e, _sp_t in _nu_spans_m + _al_spans_m:
                                if _sp_s < _cut_b < _sp_e:
                                    _occ_hit = _sp_t
                                    break
                            if _occ_hit:
                                break
                            if _cut_b in _morph_cuts_m:
                                _occ_hit = _morph_cuts_m[_cut_b]
                                break
                        if _occ_hit is None:
                            _has_clean_occ = True
                            break
                        if _first_hit_m is None:
                            _first_hit_m = _occ_hit
                    if not _has_clean_occ and _first_hit_m:
                        fg_issues.append(f"{fid} 前景花字词边界腰斩「{_first_hit_m}」：「{plain_fg}」")
                        break

            fg_sorted = sorted(
                fg_rows,
                key=lambda x: (float(x.get("start_sec") or 0.0), int(x.get("track_lane") or 0)),
            )
            for idx_f in range(len(fg_sorted) - 1):
                ia, ib = fg_sorted[idx_f], fg_sorted[idx_f + 1]
                ga = str(ia.get("group_id") or ia.get("act_id") or "")
                gb = str(ib.get("group_id") or ib.get("act_id") or "")
                if not (ga and ga == gb):
                    continue
                pa_f = _qa_plain_text(ia.get("text_or_asset") or "")
                pb_f = _qa_plain_text(ib.get("text_or_asset") or "")
                _raw_af = re.sub(r"[\s，。！？、；!?;]+$", "", str(ia.get("text_or_asset") or "").strip())
                _raw_bf = re.sub(r"^[\s，。！？、；!?;]+", "", str(ib.get("text_or_asset") or "").strip())
                _joined_ab_f = _qa_plain_text(_raw_af + _raw_bf) if (_raw_af and _raw_bf) else ""
                if pa_f and pb_f and pa_f != pb_f and ((pa_f + pb_f) in full_plain or (_joined_ab_f and _joined_ab_f in full_plain)):
                    fg_issues.extend(_check_seg_rows([ia, ib], "fg", max_split_gap=999.0))
            if bb_items and fg_rows:
                stage_sorted = sorted(
                    list(bb_items) + list(fg_rows),
                    key=lambda x: (float(x.get("start_sec") or 0.0), int(x.get("track_lane") or 0)),
                )
                for idx_f in range(len(stage_sorted) - 1):
                    ia, ib = stage_sorted[idx_f], stage_sorted[idx_f + 1]
                    if (ia.get("variant") == "bridge_bar") == (ib.get("variant") == "bridge_bar"):
                        continue
                    ga = str(ia.get("group_id") or ia.get("act_id") or "")
                    gb = str(ib.get("group_id") or ib.get("act_id") or "")
                    if not (ga and ga == gb):
                        continue
                    pa_f = _qa_plain_text(ia.get("text_or_asset") or "")
                    pb_f = _qa_plain_text(ib.get("text_or_asset") or "")
                    _raw_af = re.sub(r"[\s，。！？、；!?;]+$", "", str(ia.get("text_or_asset") or "").strip())
                    _raw_bf = re.sub(r"^[\s，。！？、；!?;]+", "", str(ib.get("text_or_asset") or "").strip())
                    _joined_ab_f = _qa_plain_text(_raw_af + _raw_bf) if (_raw_af and _raw_bf) else ""
                    if pa_f and pb_f and pa_f != pb_f and ((pa_f + pb_f) in full_plain or (_joined_ab_f and _joined_ab_f in full_plain)):
                        _is_leg_num_anchor = bool(
                            ia.get("variant") == "bridge_bar"
                            and ib.get("variant") == "hero_lime"
                            and pa_f[-1] in {"了", "咗", "着", "过"}
                            and pb_f[-1] not in {"把", "的", "嘅", "了", "咗"}
                            and bool(_NUM_UNIT_PRESPLIT_RE.fullmatch(pb_f))
                        )
                        fg_issues.extend(_check_seg_rows([ia, ib], "fg", max_split_gap=999.0, token_only=_is_leg_num_anchor))
            return fg_issues

        sub_check_rows = [
            e for e in (subs.get("entries") or [])
            if str(e.get("text") or "").strip() and (not e.get("suppressed") or e.get("suppress_reason") == "kinetic_typography_stage")
        ]
        sub_seg_issues = _check_seg_rows(sub_check_rows, "sub", max_split_gap=0.85)
        if bb_items:
            bb_sorted = sorted(bb_items, key=lambda x: float(x.get("start_sec") or 0.0))
            sub_seg_issues.extend(_check_seg_rows(bb_sorted, "bb", max_split_gap=0.45))
        if fg_highlights:
            sub_seg_issues.extend(_check_fg_highlights(fg_highlights))
        checks.append(_mk_check(
            "subtitle_segmentation_clean",
            "字幕分词自然不破词",
            "PASS" if not sub_seg_issues else "FAIL",
            "0 处助词挂尾/破词/序数吞并" if not sub_seg_issues else f"{len(sub_seg_issues)} 处：" + "；".join(sub_seg_issues[:3]),
            "0 处跨句序数粘连、0 处助词挂尾/行首、0 处词边界腰斩",
        ))

        # 11h. 双层复读叠字率检测（高光大字在屏时，下面不得叠着同义/第二层底字幕）
        def _merge_spans(spans: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
            out_s: List[Tuple[float, float]] = []
            for a_s, b_s in sorted(spans):
                if b_s <= a_s:
                    continue
                if out_s and a_s <= out_s[-1][1] + 1e-4:
                    out_s[-1] = (out_s[-1][0], max(out_s[-1][1], b_s))
                else:
                    out_s.append((a_s, b_s))
            return out_s

        def _spans_len(spans: List[Tuple[float, float]]) -> float:
            return sum(b - a for a, b in spans)

        fg_hi_spans = _merge_spans([
            (float(it.get("start_sec") or 0.0), float(it.get("end_sec") or 0.0))
            for it in ks_items_all
            if not it.get("base_caption")
            and it.get("variant") not in {"bridge_bar", "editorial_ticker", "pillar_badge"}
            and it.get("z_layer", "foreground") == "foreground"
        ])
        base_cap_spans = _merge_spans(
            [
                (float(it.get("start_sec") or 0.0), float(it.get("end_sec") or 0.0))
                for it in ks_items_all
                if it.get("base_caption") or it.get("variant") == "bridge_bar"
            ]
            + [
                (float(e.get("start_sec") or 0.0), float(e.get("end_sec") or 0.0))
                for e in (subs.get("entries") or [])
                if str(e.get("text") or "").strip() and not e.get("suppressed")
            ]
        )
        fg_total_sec = _spans_len(fg_hi_spans)
        ov_cap_sec = 0.0
        for fa, fb in fg_hi_spans:
            for ba, bb in base_cap_spans:
                ov_cap_sec += max(0.0, min(fb, bb) - max(fa, ba))
        ov_cap_ratio = (ov_cap_sec / fg_total_sec) if fg_total_sec > 0.05 else 0.0
        cur_theme = str(ks_block.get("theme_profile") or "")
        dup_limit = 0.01 if cur_theme == "style_02_unified" else 0.05
        if ks_block.get("enabled") and fg_total_sec > 0.05:
            checks.append(_mk_check(
                "double_caption_overlap",
                "高光与底字幕双层复读重叠率",
                "PASS" if ov_cap_ratio <= dup_limit else "FAIL",
                f"重叠 {ov_cap_sec:.2f}s / {fg_total_sec:.2f}s ({ov_cap_ratio:.1%})",
                f"<= {dup_limit:.0%}（单层一体化舞台禁止高光下叠字幕）",
            ))

        # 11i. 统一字体/色阶血统检查（style_02_unified 绝不混入暗金小盒或宋体）
        if ks_block.get("enabled") and cur_theme == "style_02_unified":
            typo_issues: List[str] = []
            bridge_pxs = []
            hero_pxs = []
            for it in ks_items_all:
                var = str(it.get("variant") or "")
                if var in {"concept_card", "semantic_diagram_card", "editorial_ticker"}:
                    typo_issues.append(f"混入旧暗金卡片变体 {var}")
                cands = list(it.get("font_candidates") or [])
                if cands and "serif" in str(cands[0]).lower():
                    typo_issues.append(f"首选字体混用衬线宋体 {cands[0]}")
                px_v = float(it.get("font_size_px") or 0.0)
                if var == "bridge_bar" and px_v > 0:
                    bridge_pxs.append(px_v)
                elif var in {"hero_lime", "hero_white", "red_slam", "red_pill"} and px_v > 0:
                    hero_pxs.append(px_v)
            if bridge_pxs and min(bridge_pxs) < 60.0:
                typo_issues.append(f"桥接字号过小 ({min(bridge_pxs):.0f}px < 60px)")
            if bridge_pxs and hero_pxs and max(hero_pxs) < max(bridge_pxs) * 1.25:
                typo_issues.append("大字与桥接句字阶对比不足 1.25×")
            checks.append(_mk_check(
                "style_typography_unity",
                "统一粗黑体与三级字号血统一致性",
                "PASS" if not typo_issues else "FAIL",
                "0 处血统污染（统一无衬线粗黑体 + L1/L2/L3 标尺）" if not typo_issues else "；".join(typo_issues[:3]),
                "0 旧暗金卡/宋体混入，L1>=60px 且阶梯比>=1.25×",
            ))

        # 11j. 导演运镜真实落地校验（非 steady_hold A-roll 必须实际挂载 vf_extra）
        d_acts = list(ks_block.get("director_acts") or [])
        non_steady_acts = [a for a in d_acts if str(a.get("camera_intent") or "steady_hold") != "steady_hold"]
        if non_steady_acts:
            expected_aroll = 0
            realized_aroll = 0
            for c_row in edl.get("clips", []):
                if c_row.get("track_role") != "a_roll" or c_row.get("pip_layout"):
                    continue
                cs_t, ce_t = float(c_row.get("timeline_in_sec") or 0.0), float(c_row.get("timeline_out_sec") or 0.0)
                if any(min(ce_t, float(a["end_sec"])) - max(cs_t, float(a["start_sec"])) > 0.1 for a in non_steady_acts):
                    expected_aroll += 1
                    if str(c_row.get("vf_extra") or "").strip():
                        realized_aroll += 1
            if expected_aroll > 0:
                checks.append(_mk_check(
                    "camera_intent_realized",
                    "导演运镜在口播镜头真实落地",
                    "PASS" if realized_aroll > 0 else "FAIL",
                    f"{realized_aroll}/{expected_aroll} 个口播镜头真实挂载运镜 vf_extra",
                    "存在非 steady_hold 口播幕时 >=1 镜实际挂载运镜",
                ))

        # 12–22. 独立黑盒外部验收（复用 audio_ctx，严格 Fail-Closed 三态）
        dialect = _effective_dialect(pipeline_dir, dialect)
        bb_res = run_blackbox_verification(
            video_path,
            pipeline_dir=pipeline_dir,
            dialect=dialect,
            store_anchor=store_anchor,
            strict_asr=strict_asr,
            precomputed_audio=audio_ctx,
            export_mode=export_mode,
        )

    for bchk in bb_res["checks"]:
        checks.append(_mk_check(
            bchk["id"],
            bchk["name"],
            bchk["status"],
            bchk["measured"],
            bchk["threshold"],
        ))

    provenance_path = pipeline_dir / "04_source_review.json"
    provenance = source_review_status(read_json(provenance_path)) if provenance_path.is_file() else "NOT_REQUESTED"
    if provenance == "FAIL":
        checks.append(_mk_check("source_claims", "主张来源", "FAIL", "存在缺失或变更的来源或台词", "逐条可回查"))
    requested_exports, export_required = export_selection(export_mode)
    export_check = next((c for c in bb_res["checks"] if c["id"] == "bb_export_contracts"), {})
    export_results = export_check.get("detail", {}).get("targets", {})
    required_package_hashes = {
        name: package_digest(pipeline_dir / f"{name}_draft") for name in requested_exports
        if export_required and export_results.get(name, {}).get("status") == "PASS"
    }
    manual_checks = []
    if export_required and "jianying" in requested_exports:
        manual_checks.append("jianying_import")
    if provenance == "PENDING_REVIEW":
        manual_checks.append("source_claims")
    if (pipeline_dir / "00_code_spec.json").is_file():
        manual_checks.append("code_visual_review")
    commercial = next((c["detail"] for c in bb_res["checks"]
                       if c["id"] == "bb_compliance_and_license"), {})
    if commercial.get("unacked_warn_count"):
        manual_checks.append("compliance_context")
    if commercial.get("external_documents"):
        manual_checks.append("authorization_documents")
    blocking, unavailable = review_checks(checks, export_mode, strict_asr)
    manual_checks.extend(unavailable)
    all_passed = not blocking

    if plan_sha256 != compute_plan_digest(pipeline_dir) or video_sha256 != sha256_file(video_path):
        raise RuntimeError("质检期间方案或成片已变更，请重新质检。")

    approval_status = "REJECTED"
    if all_passed:
        approval_status = "REVIEW_REQUIRED" if manual_checks else "MACHINE_PASS_PENDING_HUMAN"
        lock_p = pipeline_dir / "approval_lock.json"
        if lock_p.is_file():
            try:
                lock = read_json(lock_p)
                if (
                    lock.get("human_approved") is True
                    and lock.get("plan_sha256") == plan_sha256
                    and lock.get("output_final_sha256") == video_sha256
                    and lock.get("export_mode", "auto") == export_mode
                    and lock.get("required_package_hashes", {}) == required_package_hashes
                    and set(manual_checks) <= set(lock.get("reviewed_checks", []))
                ):
                    approval_status = "APPROVED"
            except Exception:
                pass

    pass_cnt = sum(1 for c in checks if c["status"] == "PASS")
    fail_cnt = sum(1 for c in checks if c["status"] == "FAIL")
    warn_cnt = sum(1 for c in checks if c["status"] == "WARN")
    skip_cnt = sum(1 for c in checks if c["status"] == "SKIPPED_UNAVAILABLE")

    report: Dict[str, Any] = {
        "step": "10_qa_inspector",
        "video_path": str(video_path.resolve()),
        "plan_sha256": plan_sha256,
        "output_final_sha256": video_sha256,
        "all_passed": all_passed,
        "machine_passed": all_passed and not manual_checks,
        "blocking_checks": blocking,
        "pending_manual_checks": manual_checks if approval_status != "APPROVED" else [],
        "required_manual_checks": manual_checks,
        "export_mode": export_mode,
        "required_package_hashes": required_package_hashes,
        "sync_window": sync_window,
        "deliverables": {"mp4": {"status": "FAIL" if any(c != "bb_export_contracts" for c in blocking) else
                                  "PENDING_REVIEW" if any(c != "jianying_import" for c in manual_checks) else "PASS"},
                         **export_results},
        "approval_status": approval_status,
        "strict_asr": strict_asr,
        "pass_count": pass_cnt,
        "fail_count": fail_cnt,
        "warn_count": warn_cnt,
        "warning_checks": [c["id"] for c in checks if c["status"] == "WARN"],
        "skipped_count": skip_cnt,
        "total_checks": len(checks),
        "checks": checks,
        "blackbox_details": bb_res,
    }
    if benchmark_path and benchmark_path.exists():
        report["benchmark_comparison"] = compare_with_benchmark(video_path, benchmark_path, edl)

    write_json(pipeline_dir / "10_qa_report.json", report)

    status_banner = (
        "✅ **全部自动检查与人工终审已通过**"
        if approval_status == "APPROVED"
        else (
            "🟡 **自动检查已过，待您最后看一眼确认**"
            if all_passed
            else "❌ **有硬性问题未通过，请先修改再发**"
        )
    )
    plain_verdict = (
        "成片声音清晰、字幕居中没被按钮遮挡、画面切换干脆，所有硬性检查都过了，您确认一眼就能发。"
        if all_passed
        else f"还有 {fail_cnt} 项硬性问题没过（见下表标有「未通过」的行），建议先修好再发，避免发出后被限流或观感打折。"
    )
    _status_cn = {
        "PASS": "✅ 通过",
        "FAIL": "❌ 未通过",
        "SKIPPED_UNAVAILABLE": "⏭️ 环境未装跳过",
        "NOT_REQUESTED": "➖ 本次未要求",
    }
    _manual_cn = {
        "jianying_import": "剪映工程导入抽查",
        "source_claims": "原始报价或证明材料核对",
        "code_visual_review": "动态图解画面人眼核查",
        "compliance_context": "需凭证表述核验确认",
        "authorization_documents": "外置素材授权原件核对",
        "bb_ocr_av_loop": "画面字幕居中与同步人工抽查",
        "bb_pixel_readability": "字幕与人像遮挡及清晰度抽查",
        "bb_meta_leak_ocr": "画面零异常水印/开发词抽查",
        "bb_color_scope": "人物肤色与曝光自然度抽查",
        "bb_blind_asr_proxy": "整片盲听复核",
        "bb_cut_spot_verify": "镜头切换顺畅度抽查",
    }
    _check_plain_cn = {
        "loudness": "整片声音大小适中、不破音",
        "black_frames": "全程没有突然黑屏",
        "long_silence": "说话之间没有冷场大空白",
        "av_sync": "画面和声音严格对齐不脱节",
        "subtitle_sync": "字幕出现时间紧贴说话节奏",
        "cut_alignment": "切画面踩在说话和音乐节拍上",
        "ui_safe_zone": "字幕居中好看、不撞点赞按钮、不留碎字",
        "shot_rhythm": "镜头长短有起伏、不重复倒播",
        "first_3s_hook": "开头前 3 秒画面节奏抓人",
        "sentence_integrity": "没有把话说一半硬切断",
        "forbidden_meta_text": "画面和文案没有多余乱码或模板字样",
        "residual_repeat_scan": "没有漏剪的口误重说和半截话",
        "style_lock_intact": "画面样式严格按你选的那套来，没被偷偷改",
        "element_causality_fit": "花字、抠像、大字、结尾卡与音效密度按所选风格习惯控制",
        "ending_complete": "片尾收住了，观众知道下一步做什么",
        "overlay_cross_act_overlap": "花字不跨幕打架叠字",
        "subtitle_segmentation_clean": "字幕分词自然不破词",
        "double_caption_overlap": "重点大字下面不叠第二层复读小字幕",
        "style_typography_unity": "全片字体家族、字号阶梯与色彩高度统一",
        "camera_intent_realized": "导演规划的推拉与特写运镜真实落地到口播镜头",
        "bb_audio_master": "成品声音饱满清晰、无爆音无穿帮",
        "bb_ocr_av_loop": "成片字幕实测正中对齐、字句准确",
        "bb_pixel_readability": "字幕不挡脸且对比清晰好认",
        "bb_color_scope": "人物肤色自然、不过曝不发灰",
        "bb_export_contracts": "可编辑工程包完整不缺素材",
        "bb_meta_leak_ocr": "成片画面干干净净无异常水印",
        "bb_freeze_frame": "没有卡顿定格死画面",
        "bb_cut_spot_verify": "每次切镜画面都有真实变化",
        "bb_sfx_onset_align": "提示音和画面动作严丝合缝",
        "bb_blind_asr_proxy": "脱离字幕盲听还原台词清晰准确",
        "bb_compliance_and_license": "台词文案合规、字体音乐全部可商用",
    }
    md_lines = [
        f"# 成片与交付工程体检报告（共 {len(checks)} 项）",
        "",
        f"- **一句话大白话结论**：{plain_verdict}",
        f"- **成片文件**：`{video_path.name}`",
        f"- **质量状态**：{status_banner}",
        f"- **统计**：共 {len(checks)} 项 · 通过 `{pass_cnt}` 项 · 未通过 `{fail_cnt}` 项 · 跳过 `{skip_cnt}` 项",
        "",
        "## 一、 大白话体验体检表",
        "",
        "| 序号 | 体检项目 | 结果 |",
        "| :---: | :--- | :---: |",
    ]
    for i, c in enumerate(checks, start=1):
        st_label = _status_cn.get(c["status"], c["status"])
        plain_title = _check_plain_cn.get(c["id"], c["name"])
        md_lines.append(f"| {i} | {plain_title} | {st_label} |")
    if manual_checks:
        manual_readable = "、".join(_manual_cn.get(m, m) for m in manual_checks)
        md_lines.extend(["", f"- **建议您最后一眼确认**：{manual_readable}"])
    for target, result in export_results.items():
        md_lines.extend(f"- {target}：{error}" for error in result.get("errors", []))

    md_lines.extend([
        "",
        "<details>",
        "<summary>展开查看专业工程实测明细（供技术复核）</summary>",
        "",
        "| # | 检查项 | 状态 | 实测结果 | 门槛 |",
        "|:-:|:---|:---:|:---|:---|",
    ])
    for i, c in enumerate(checks, start=1):
        measured = c["measured"].replace("|", "｜")
        st_label = _status_cn.get(c["status"], c["status"])
        md_lines.append(f"| {i} | {c['name']} | {st_label} | {measured} | {c['threshold']} |")
    md_lines.append("</details>")

    md_lines.extend(_per_frame_detail_section(report))

    if "benchmark_comparison" in report:
        bc = report["benchmark_comparison"]
        md_lines.extend([
            "",
            "## 对标参考节奏对比",
            f"- 参考片 `{Path(bc['benchmark_file']).name}` 平均单镜时长：`{bc['benchmark_estimated_asl_sec']}s`；本片平均单镜时长：`{bc['our_asl_sec']}s`（差值 `{bc['rhythm_delta_sec']:+.2f}s`）",
        ])

    (pipeline_dir / "10_qa_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    _write_blind_review_pack(pipeline_dir, video_path, report, edl, subs, audio_events)
    print(f"[qa] {len(checks)} 项质检：{pass_cnt} PASS / {fail_cnt} FAIL / {skip_cnt} SKIP -> 10_qa_report.md")
    return report


def _per_frame_detail_section(report: Dict[str, Any]) -> List[str]:
    """把三项字幕/肤色黑盒检查的**逐帧实测**落成可读小节（用户要求：具体哪几帧、什么原因）。"""
    bb = {c["id"]: c for c in (report.get("blackbox_details", {}) or {}).get("checks") or []}
    lines: List[str] = ["", "## 二、 逐帧实测明细（字幕层 / 可读性 / 肤色）"]

    sub = (bb.get("bb_ocr_av_loop") or {}).get("detail") or {}
    if sub:
        lines.extend([
            "",
            "### 2.1 字幕层（只计我们自己渲染的字幕层）",
            f"- 判据范围：`{sub.get('scope_cn', '05_subtitles.final_bbox')}`",
            f"- 字幕条数：`{sub.get('entries_total')}` 条，实测样本 `{sub.get('entries_sampled')}` 条，裁切 OCR `{sub.get('crop_ocr_calls')}` 次",
            f"- 最大居中偏差：`{sub.get('max_subtitle_center_dev_px')}`px；语音帧字幕覆盖：`{round(float(sub.get('speech_sub_ocr_ratio') or 0)*100, 1)}%`；内容匹配：`{round(float(sub.get('sub_ocr_content_recall') or 0)*100, 1)}%`",
            f"- 画面自带文字（招牌/美术字）与其他非字幕层文字：`{sub.get('excluded_texts_count', 0)}` 处，**不计入本项判定**",
        ])
        rows = sub.get("frames") or []
        if rows:
            lines.extend(["", "<details>", f"<summary>展开逐帧字幕实测表（{len(rows)} 帧）</summary>", "",
                          "| 帧 | 时间 | 期望字幕 | 裁切OCR | bbox | 居中偏差 | 安全区 | 来源 |",
                          "|:--:|:--:|:---|:---|:---|:--:|:--:|:--|"])
            for row in rows:
                subs_row = row.get("subs") or []
                if not subs_row:
                    lines.append(f"| {row.get('frame')} | {row.get('t_sec')}s | （无字幕） | - | - | - | - | - |")
                    continue
                for s in subs_row:
                    box = s.get("text_bbox") or [0, 0, 0, 0]
                    lines.append(
                        f"| {row.get('frame')} | {row.get('t_sec')}s | {s.get('expected_text', '')} | "
                        f"{s.get('ocr_text') or '（未识出）'} | {box[0]},{box[1]}+{box[2]}x{box[3]} | "
                        f"{s.get('center_dev_px')}px | {'OK' if s.get('in_safe_zone') else '✗越界'} | our_subtitle |"
                    )
            lines.append("</details>")

    read = (bb.get("bb_pixel_readability") or {}).get("detail") or {}
    if read:
        lines.extend([
            "",
            "### 2.2 字幕可读性（WCAG 前景 vs 紧邻背景）",
            f"- 判据范围：`{read.get('scope_cn', '只判字幕层')}`",
            f"- 均值对比度 `{read.get('wcag_mean_contrast')}:1`（样本 {read.get('wcag_samples')}），需 ≥4.5；单帧底线 ≥3.8",
            f"- 人字最大 IoU `{read.get('max_face_text_iou')}`（阈值 ≤0.02）",
        ])
        pkg_hits = read.get("packaging_face_hits") or []
        if pkg_hits:
            lines.append(f"- 包装花字压脸（不是字幕层，单列不计入判定）：" + "；".join(pkg_hits))
        rows = [r for r in (read.get("frames") or []) if r.get("contrast_ratio") is not None]
        if rows:
            low = [r for r in rows if float(r["contrast_ratio"]) < 3.8]
            lines.extend(["", "<details>", f"<summary>展开逐帧可读性表（{len(rows)} 帧，低于 3.8 的 {len(low)} 帧）</summary>", "",
                          "| 帧 | 时间 | 字幕 | 字幕框 | 人脸框 | IoU | 对比度 | 背景亮度 |",
                          "|:--:|:--:|:---|:---|:---|:--:|:--:|:--:|"])
            for r in rows:
                tb = r.get("text_bbox") or [0, 0, 0, 0]
                fb = r.get("face_bbox")
                lines.append(
                    f"| {r.get('frame')} | {r.get('t_sec')}s | {r.get('text')} | {tb[0]},{tb[1]}+{tb[2]}x{tb[3]} | "
                    f"{('%d,%d+%dx%d' % tuple(fb)) if fb else '无'} | {r.get('iou')} | {r.get('contrast_ratio')} | {r.get('bg_luma')} |"
                )
            lines.append("</details>")

    color = (bb.get("bb_color_scope") or {}).get("detail") or {}
    if color:
        rng = color.get("skin_sector_range") or {}
        lines.extend([
            "",
            "### 2.3 肤色与曝光逐镜像素实测",
            f"- 判据：BT.709 矩阵 + `common.SKIN_YCBCR_RANGE`（cb {rng.get('cb_min')}~{rng.get('cb_max')}，cr {rng.get('cr_min')}~{rng.get('cr_max')}），只对 yy≥40 的有效肤色像素判定",
            f"- 实测 {color.get('skin_samples_checked')} 个含脸帧：违规 {color.get('skin_sector_violations')}，素材限制标 `source_limited` {color.get('skin_source_limited')}",
            f"- `{color.get('scope_cn', '')}`",
        ])
        rows = color.get("frames") or []
        if rows:
            lines.extend(["", "<details>", f"<summary>展开逐帧肤色表（{len(rows)} 帧）</summary>", "",
                          "| 帧 | 时间 | cb | cr | 亮度p50 | 肤色像素 | 判定 | 原因 |",
                          "|:--:|:--:|:--:|:--:|:--:|:--:|:--|:--|"])
            for r in rows:
                if r.get("source_limited"):
                    verdict = "source_limited"
                    why = r.get("source_limited_reason") or "素材限制"
                elif r.get("in_sector"):
                    verdict = "OK"
                    why = "-"
                else:
                    verdict = "✗违规"
                    why = "调色把肤色推出健康扇区"
                lines.append(
                    f"| {r.get('frame')} | {r.get('t_sec')}s | {r.get('cb')} | {r.get('cr')} | {r.get('y_median')} | "
                    f"{r.get('skin_px_count')} | {verdict} | {why} |"
                )
            lines.append("</details>")
    return lines


def _write_blind_review_pack(
    pipeline_dir: Path,
    video_path: Path,
    report: Dict[str, Any],
    edl: Dict[str, Any],
    subs: Dict[str, Any],
    audio_events: Dict[str, Any],
) -> Path:
    """生成《独立盲审取证包》(10_blind_review_pack.md)：零内部代号，30 秒人眼/外审一页通关。"""
    bb_checks = {c["id"]: c for c in (report.get("blackbox_details", {}).get("checks") or [])}
    asr_detail = bb_checks.get("bb_blind_asr_proxy", {}).get("detail", {}) or {}
    asr_text = str(asr_detail.get("recognized_text") or "").strip()
    ocr_detail = bb_checks.get("bb_ocr_av_loop", {}).get("detail", {}) or {}
    cover_p = pipeline_dir / "09_title_cover.json"
    cover_meta = read_json(cover_p) if cover_p.is_file() else {}

    strip_p = pipeline_dir / "10_action_strips.jpg"
    cover_3x4 = pipeline_dir / "09_cover_3x4.jpg"
    cover_full = pipeline_dir / "09_cover.jpg"
    ui_prev = pipeline_dir / "09_ui_safe_preview.jpg"
    feed_prev = pipeline_dir / "09_cover_360px_feed.jpg"

    cover_files_list = [f"`{p.name}`" for p in (cover_full, cover_3x4, ui_prev, feed_prev) if p.is_file()]
    lines = [
        "# 成片出厂盲审取证包（不看内部制作过程，直接当普通观众验片）",
        "",
        "这份清单把成片的开头、关键切镜、手机小图封面和盲听还原的台词摊平在一页里，方便你或朋友 30 秒一眼验收：",
        "",
        "## 1. 取证材料一览",
        f"- **最终成片**：`{video_path.name}`（总时长 {float(edl.get('total_duration_sec', 0.0)):.1f} 秒，共 {len(edl.get('clips') or [])} 个镜头）",
        f"- **开头 3 秒与关键切点连拍长图**：`{strip_p.name}`" + ("（已生成）" if strip_p.is_file() else "（本次未生成，请直接播放成片前 3 秒核对）"),
        f"- **封面与手机信息流模拟预览**：{' / '.join(cover_files_list) if cover_files_list else '（本次未生成封面图）'}",
    ]
    if cover_meta:
        lines.append(
            f"- **封面实测清晰度（缩小到手机双列小图尺寸）**：对比度 `{cover_meta.get('thumb_180_contrast_ratio', '-')}:1`（越高越醒目，最低底线 3.8:1，推荐 ≥ 4.5:1），"
            f"人物前景压字：`{'已启用' if cover_meta.get('person_cutout_applied') else '标准排版'}`"
        )

    lines.extend([
        "",
        "## 2. 脱离字幕直接盲听还原的台词",
        (f"> {asr_text}" if asr_text else "> （本次未启用独立听写或视频无口播，请直接打开成片外放试听。）"),
        "",
        "## 3. 镜头画面与当下台词对照抽样（检查有没有文不对图）",
        "",
        "| 时间段 | 画面内容 | 这一刻嘴里正在说什么 |",
        "| :---: | :--- | :--- |",
    ])
    sents = audio_events.get("sentences") or []
    clips = edl.get("clips") or []
    step_stride = max(1, len(clips) // 8)
    for c in clips[::step_stride][:8]:
        c_in = float(c.get("timeline_in_sec", 0.0))
        c_out = float(c.get("timeline_out_sec", 0.0))
        spoken = [
            str(s.get("text") or "").strip()
            for s in sents
            if min(c_out, float(s.get("end_sec", 0.0))) - max(c_in, float(s.get("start_sec", 0.0))) > 0.15
        ]
        spoken_str = " / ".join(spoken[:2]) or "（画面过渡/停顿）"
        role_zh = (
            "真人出镜说话" if c.get("track_role") == "a_roll"
            else ("图解动画卡片" if c.get("track_role") == "code_broll" else "实拍特写/空镜")
        )
        purp = str(c.get("purpose") or role_zh).replace("|", "｜")
        lines.append(f"| {c_in:.1f}s–{c_out:.1f}s | {role_zh}：{purp} | {spoken_str.replace('|', '｜')} |")

    center_dev = ocr_detail.get("max_subtitle_center_dev_px")
    hook_check_src = "看连拍长图前 3 秒" if strip_p.is_file() else "静音播放成片前 3 秒"
    lines.extend([
        "",
        "## 4. 盲审人眼 5 问（全勾即可直接发布）",
        f"- [ ] **静音盲看前 3 秒**：{hook_check_src}，关掉声音的情况下，能不能一眼看懂在讲什么并想停下来？",
        "- [ ] **闭眼盲听顺不顺**：不看画面听一遍，有没有吞字、重说残句或音乐盖过说话声？",
        "- [ ] **画面对不对嘴**：看上面对照表，每段特写和字幕说的事是不是一回事？",
        f"- [ ] **字幕和按钮打不打架**：字幕都在正中间靠下清晰可读吗？（自动实测居中偏差：`{center_dev if center_dev is not None else '未测得'}px`）",
        "- [ ] **手机小图清不清楚**：看 `09_cover_360px_feed.jpg`（缩小成手机信息流真实宽度），主标题能不能 1 秒看清、没被裁切？",
    ])
    out_p = pipeline_dir / "10_blind_review_pack.md"
    out_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_p


def scan_residual_repeats(
    segments: List[Dict[str, Any]],
    reference_text: Optional[str] = None,
    window_sec: float = 10.0,
) -> Dict[str, Any]:
    """
    成片转写残留重说扫描：
    1. 10 秒滑窗内同一 5 字片段重复出现（排除参考稿中本就有意重复 >=2 次的排比）；
    2. 1–6 字短孤立段且其前 2 字正是下一段开头（典型漏剪残留起句，如“地图……地图上其实”）。
    """
    clean_segs: List[Tuple[float, str]] = []
    for seg in (segments or []):
        t0 = float(seg.get("start_sec", seg.get("start", 0.0)))
        txt = _strip_punct_for_len(str(seg.get("text") or ""))
        if txt:
            clean_segs.append((t0, txt))

    ref_clean = _strip_punct_for_len(reference_text or "")
    dup_phrases: set[str] = set()
    for i, (t0, s0) in enumerate(clean_segs):
        for j in range(i, len(clean_segs)):
            t1, s1 = clean_segs[j]
            if (t1 - t0) > window_sec:
                break
            for k in range(max(0, len(s0) - 4)):
                gram = s0[k : k + 5]
                if len(gram) < 5:
                    continue
                pos = s1.find(gram, k + 5 if i == j else 0)
                if pos >= 0:
                    if not ref_clean or ref_clean.count(gram) < 2:
                        dup_phrases.add(gram)

    orphan_starts: List[str] = []
    for (t0, s0), (t1, s1) in zip(clean_segs, clean_segs[1:]):
        if 1 <= len(s0) <= 6 and len(s1) >= 2 and s1.startswith(s0[:2]):
            if (t1 - t0) <= 4.0:
                orphan_starts.append(f"{t0:.2f}s 「{s0}」→「{s1[:10]}」")

    return {
        "duplicate_phrases": sorted(dup_phrases),
        "orphan_starts": orphan_starts,
        "clean": (len(dup_phrases) == 0 and len(orphan_starts) == 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 10: 全套白盒硬红线与独立黑盒质检报告生成")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--video", help="指定待检 MP4（默认 output_final.mp4）")
    parser.add_argument("--benchmark", help="可选：对标视频路径")
    parser.add_argument("--dialect", default="mandarin", help="语言模式 (mandarin / cantonese / dialect)")
    parser.add_argument("--store-anchor", help="门店名/核心听觉锚点")
    parser.add_argument("--exports", choices=EXPORT_CHOICES, default=None)
    parser.add_argument("--sync-window", type=float, nargs=2, metavar=("START", "END"))
    parser.add_argument("--strict-asr", action=argparse.BooleanOptionalAction, default=None, help="要求可用的独立 ASR")
    args = parser.parse_args()
    previous = Path(args.pipeline_dir) / "10_qa_report.json"
    saved_report = read_json(previous) if previous.is_file() else {}

    res = inspect_video(
        Path(args.pipeline_dir),
        video_path=Path(args.video) if args.video else None,
        benchmark_path=Path(args.benchmark) if args.benchmark else None,
        dialect=args.dialect,
        store_anchor=args.store_anchor,
        strict_asr=args.strict_asr if args.strict_asr is not None else bool(saved_report.get("strict_asr")),
        export_mode=args.exports or saved_report.get("export_mode", "auto"),
        sync_window=args.sync_window or saved_report.get("sync_window"),
    )
    if not res["all_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
