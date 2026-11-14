#!/usr/bin/env python3
"""
[自动质检与对标逐秒结构对比]
对每条成片执行 9 项硬性检查，任何一项未通过则判定为 FAIL 并退出码非 0（未通过绝不交付）：
1. 响度 (-14.0 ± 1.0 LUFS)
2. 黑帧 (0 个黑帧区间)
3. 超过 0.8s 的静音段 (0 段)
4. 音画同步 (同期声源对齐 ≤ 1帧 & 成片音视频流时长差 ≤ 1帧)
5. 字幕和语音的时间偏差 (≤ 1帧)
6. 切点是否落在音频事件上 (去除合法 2-4帧 J-cut 后偏差 ≤ 1帧)
7. 字幕和贴纸是否进入平台 UI 遮挡区 (0 次越界)
8. 镜头时长分布 (统计分布并校验无 <0.3s 碎镜头或 >8s 死镜头)
9. 前 3 秒画面变化次数 (≥ 2 次且均对应音频事件)

可选：传入 --benchmark <对标视频>，额外输出逐秒结构对比报告 10_benchmark_comparison.md。
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (
    DEFAULT_FPS,
    TARGET_LUFS,
    check_bbox_in_safe_zone,
    ffprobe_media,
    find_bin,
    maybe_reexec_in_venv,
    read_json,
    run_cmd,
    write_json,
)


def inspect_video(
    pipeline_dir: Path,
    video_path: Optional[Path] = None,
    benchmark_path: Optional[Path] = None,
) -> Dict[str, Any]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 执行自动质检。")

    if video_path is None:
        video_path = pipeline_dir / "output_final.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"待质检成片不存在: {video_path}")

    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    subs = read_json(pipeline_dir / "05_subtitles.json")
    pkg = read_json(pipeline_dir / "06_packaging.json")

    fps = int(edl.get("fps", DEFAULT_FPS))
    frame_dur = 1.0 / float(fps)
    checks: List[Dict[str, Any]] = []

    # 1. 响度检查 (对成片实测 ebur128)
    ebur_res = run_cmd([
        ffmpeg, "-hide_banner", "-nostats", "-i", str(video_path),
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-",
    ], check=False)
    i_vals = re.findall(r"I:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*LUFS", ebur_res.stderr)
    measured_lufs = float(i_vals[-1]) if i_vals else -99.0
    lufs_ok = abs(measured_lufs - TARGET_LUFS) <= 1.0
    checks.append({
        "id": "loudness",
        "name": "综合响度 (-14 LUFS)",
        "passed": lufs_ok,
        "measured": f"{measured_lufs:.2f} LUFS",
        "threshold": f"{TARGET_LUFS} ± 1.0 LUFS",
    })

    # 2. 黑帧检查
    blk_res = run_cmd([
        ffmpeg, "-hide_banner", "-i", str(video_path),
        "-vf", "blackdetect=d=0.03:pix_th=0.10", "-f", "null", "-",
    ], check=False)
    black_hits = re.findall(r"black_duration:([0-9.]+)", blk_res.stderr)
    checks.append({
        "id": "black_frames",
        "name": "黑帧检测",
        "passed": len(black_hits) == 0,
        "measured": f"检出 {len(black_hits)} 处黑帧",
        "threshold": "0 处黑帧",
    })

    # 3. 超过 0.8s 静音段检查
    sil_res = run_cmd([
        ffmpeg, "-hide_banner", "-i", str(video_path),
        "-af", "silencedetect=noise=-48dB:d=0.80", "-f", "null", "-",
    ], check=False)
    sil_hits = [float(x) for x in re.findall(r"silence_duration:\s*([0-9.]+)", sil_res.stderr)]
    long_sil = [x for x in sil_hits if x > 0.80]
    checks.append({
        "id": "long_silence",
        "name": "超过 0.8s 的静音段",
        "passed": len(long_sil) == 0,
        "measured": f"{len(long_sil)} 段" + (f" (最长 {max(long_sil):.2f}s)" if long_sil else ""),
        "threshold": "0 段 > 0.8s",
    })

    # 4. 音画同步与成片总时长差 <= 1帧
    probe = ffprobe_media(video_path)
    streams = probe.get("streams", [])
    v_s = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_s = next((s for s in streams if s.get("codec_type") == "audio"), None)
    v_dur = float(v_s.get("duration") or probe.get("format", {}).get("duration") or 0.0) if v_s else 0.0
    a_dur = float(a_s.get("duration") or probe.get("format", {}).get("duration") or 0.0) if a_s else 0.0
    dur_diff_sec = abs(v_dur - a_dur)
    dur_diff_frames = dur_diff_sec / frame_dur
    aroll_sync_ok = all(bool(c.get("audio_locked", True)) for c in edl["clips"] if c["track_role"] == "a_roll")
    sync_ok = (v_s is not None and a_s is not None and dur_diff_frames <= 1.05 and aroll_sync_ok)
    checks.append({
        "id": "av_sync",
        "name": "音画同步与总时长差 (≤1帧)",
        "passed": sync_ok,
        "measured": f"时长差 {dur_diff_sec*1000:.1f}ms ({dur_diff_frames:.2f} 帧), 同期声锁定={'是' if aroll_sync_ok else '否'}",
        "threshold": f"≤ 1.0 帧 ({frame_dur*1000:.1f}ms)",
    })

    # 5. 字幕和语音的时间偏差 <= 1帧
    word_spans: Dict[str, tuple[float, float]] = {}
    for sent in audio_events["sentences"]:
        for w in sent.get("words", []):
            key = f"{sent['id']}_{w['word']}_{w['start_sec']}"
            word_spans[key] = (float(w["start_sec"]), float(w["end_sec"]))
    max_sub_err_sec = 0.0
    for sub in subs.get("entries", []):
        ws = sub.get("words") or []
        if ws:
            err_s = abs(float(sub["start_sec"]) - float(ws[0]["start_sec"]))
            err_e = abs(float(sub["end_sec"]) - float(ws[-1]["end_sec"]))
            max_sub_err_sec = max(max_sub_err_sec, err_s, err_e)
    sub_err_frames = max_sub_err_sec / frame_dur
    checks.append({
        "id": "subtitle_sync",
        "name": "字幕与锁定语音时间偏差",
        "passed": sub_err_frames <= 1.05,
        "measured": f"最大偏差 {max_sub_err_sec*1000:.1f}ms ({sub_err_frames:.2f} 帧)",
        "threshold": "≤ 1.0 帧",
    })

    # 6. 切点是否落在音频事件上 (允许句边界 2-4 帧 J-cut)
    events_by_id = {e["id"]: e for e in audio_events["events"]}
    max_cut_err_frames = 0
    jcut_valid = True
    for clip in edl["clips"][1:]:
        ev = events_by_id.get(clip.get("cut_event_id", ""))
        j_lead = int(clip.get("j_cut_lead_frames", 0))
        if j_lead not in (0, 2, 3, 4):
            jcut_valid = False
        if not ev:
            max_cut_err_frames = 999
        else:
            err = abs((int(clip["timeline_in_frame"]) - j_lead) - int(ev["frame"]))
            max_cut_err_frames = max(max_cut_err_frames, err)
    checks.append({
        "id": "cut_on_audio_event",
        "name": "切点对齐音频事件 (含J-cut)",
        "passed": jcut_valid and max_cut_err_frames <= 1,
        "measured": f"最大对齐偏差 {max_cut_err_frames} 帧, J-cut合规={'是' if jcut_valid else '否'}",
        "threshold": "≤ 1 帧",
    })

    # 7. 字幕和贴纸是否进入平台 UI 遮挡区
    ui_violations: List[str] = []
    for sub in subs.get("entries", []):
        ok, msg = check_bbox_in_safe_zone(sub["bbox"])
        if not ok:
            ui_violations.append(f"{sub['id']}: {msg}")
    for elem in pkg.get("visual_elements", []):
        ok, msg = check_bbox_in_safe_zone(elem["bbox"])
        if not ok:
            ui_violations.append(f"{elem['id']}: {msg}")
    # 同时核验包装元素 ±1 帧对齐
    pkg_sync_err = max([abs(int(e["start_frame"]) - int(e["aligned_sfx_or_accent_frame"])) for e in pkg.get("visual_elements", [])] or [0])
    checks.append({
        "id": "ui_safe_zone",
        "name": "平台 UI 遮挡区 & 包装帧对齐",
        "passed": len(ui_violations) == 0 and pkg_sync_err <= 1,
        "measured": f"遮挡区违规 {len(ui_violations)} 处, 包装对齐最大误差 {pkg_sync_err} 帧",
        "threshold": "0 违规且包装误差 ≤ 1 帧",
    })

    # 8. 镜头时长分布
    durs = [float(c["duration_sec"]) for c in edl["clips"]]
    min_d, max_d = min(durs), max(durs)
    mean_d = statistics.mean(durs)
    med_d = statistics.median(durs)
    b_short = sum(1 for d in durs if d < 1.0)
    b_mid = sum(1 for d in durs if 1.0 <= d <= 2.5)
    b_long = sum(1 for d in durs if d > 2.5)
    dist_ok = min_d >= 0.30 and max_d <= 8.0
    checks.append({
        "id": "shot_duration_dist",
        "name": "镜头时长分布",
        "passed": dist_ok,
        "measured": (
            f"共{len(durs)}镜 | min={min_d:.2f}s, med={med_d:.2f}s, mean={mean_d:.2f}s, max={max_d:.2f}s "
            f"(<1s:{b_short}, 1-2.5s:{b_mid}, >2.5s:{b_long})"
        ),
        "threshold": "0.30s ≤ 单镜 ≤ 8.0s",
    })

    # 9. 前 3 秒画面变化次数
    first_3s_cuts = [c for c in edl["clips"][1:] if int(c["timeline_in_frame"]) < 3 * fps]
    all_on_events = all(c.get("cut_event_id") in events_by_id for c in first_3s_cuts)
    first3_ok = len(first_3s_cuts) >= 2 and all_on_events
    checks.append({
        "id": "first_3s_changes",
        "name": "前 3 秒画面变化次数",
        "passed": first3_ok,
        "measured": f"{len(first_3s_cuts)} 次变化 (对应音频事件: {'全部' if all_on_events else '部分缺失'})",
        "threshold": "≥ 2 次且均对齐音频事件",
    })

    all_passed = all(c["passed"] for c in checks)
    report = {
        "step": "10_qa_inspector",
        "video_path": str(video_path.resolve()),
        "all_passed": all_passed,
        "pass_count": sum(1 for c in checks if c["passed"]),
        "total_checks": len(checks),
        "checks": checks,
    }
    write_json(pipeline_dir / "10_qa_report.json", report)

    md_lines = [
        "# 成片自动质检报告 (QA Report)",
        "",
        f"- **成片文件**：`{video_path.name}`",
        f"- **最终判定**：{'✅ **全部通过 (READY TO DELIVER)**' if all_passed else '❌ **未通过质检 (REJECTED - 禁止交付)**'}（{report['pass_count']}/{report['total_checks']} 项合格）",
        "",
        "| 序号 | 质检项目 | 结果 | 实测值 | 合格标准 |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for i, c in enumerate(checks, start=1):
        status = "✅ PASS" if c["passed"] else "❌ FAIL"
        md_lines.append(f"| {i} | {c['name']} | {status} | {c['measured']} | {c['threshold']} |")

    if ui_violations:
        md_lines += ["", "## UI 遮挡越界详情"] + [f"- {u}" for u in ui_violations]

    (pipeline_dir / "10_qa_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    # 若提供对标视频，额外输出逐秒结构对比
    if benchmark_path and benchmark_path.exists():
        compare_with_benchmark(pipeline_dir, edl, audio_events, benchmark_path)

    print(f"[qa] 质检完成：{report['pass_count']}/{report['total_checks']} 项通过 -> 10_qa_report.md")
    return report


def compare_with_benchmark(
    pipeline_dir: Path,
    edl: Dict[str, Any],
    audio_events: Dict[str, Any],
    benchmark_path: Path,
) -> Path:
    """对标视频逐秒结构分析与成片对比。"""
    ffmpeg = find_bin("ffmpeg")
    b_probe = ffprobe_media(benchmark_path)
    b_dur = float(b_probe.get("format", {}).get("duration") or 10.0)
    ours_dur = float(edl["total_duration_sec"])

    # 检测对标视频的镜头切点时刻
    scene_res = run_cmd([
        ffmpeg, "-hide_banner", "-i", str(benchmark_path),
        "-vf", "select='gt(scene,0.18)',showinfo", "-f", "null", "-",
    ], check=False)
    b_cuts = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", scene_res.stderr)]

    # 检测对标视频的静音区间
    sil_res = run_cmd([
        ffmpeg, "-hide_banner", "-i", str(benchmark_path),
        "-af", "silencedetect=noise=-42dB:d=0.35", "-f", "null", "-",
    ], check=False)
    b_sil_starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", sil_res.stderr)]

    max_sec = int(max(b_dur, ours_dur)) + 1
    lines = [
        "# 对标视频逐秒结构对比报告",
        "",
        f"- **我方成片**：{ours_dur:.2f}s（{len(edl['clips'])} 镜头，前3秒切换 {edl['first_3s_visual_changes']} 次）",
        f"- **对标视频**：`{benchmark_path.name}`（{b_dur:.2f}s，检出明显场景切点 {len(b_cuts)} 次）",
        "",
        "| 秒区间 | 我方镜头与角色 | 我方对应文案/声音事件 | 对标该秒变化特征 | 节奏建议 |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for sec in range(min(max_sec, 60)):
        s0, s1 = float(sec), float(sec + 1)
        our_clips = [c for c in edl["clips"] if c["timeline_in_sec"] < s1 and c["timeline_out_sec"] > s0]
        clip_desc = " / ".join(f"{c['clip_id']}({c['track_role']})" for c in our_clips) or "片尾"
        our_sents = [s["text"][:14] for s in audio_events["sentences"] if s["start_sec"] < s1 and s["end_sec"] > s0]
        sent_desc = "；".join(our_sents) if our_sents else "句间气口/BGM"

        bench_cut_here = sum(1 for t in b_cuts if s0 <= t < s1)
        bench_sil_here = any(s0 <= t < s1 for t in b_sil_starts)
        if s0 >= b_dur:
            bench_desc = "对标已结束"
        else:
            bench_desc = f"切镜 {bench_cut_here} 次" + (" · 有停顿气口" if bench_sil_here else " · 持续语音/画面")

        advice = "节奏紧凑" if len(our_clips) >= max(1, bench_cut_here) else "可考虑加密 B-roll"
        lines.append(f"| {sec:02d}s–{sec+1:02d}s | {clip_desc} | {sent_desc} | {bench_desc} | {advice} |")

    out_md = pipeline_dir / "10_benchmark_comparison.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[qa] 对标视频逐秒对比报告已生成 -> {out_md}")
    return out_md


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="9项强制自动质检与对标逐秒对比")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--video", help="指定待检 MP4（默认 output_final.mp4）")
    parser.add_argument("--benchmark", help="可选：对标视频路径")
    args = parser.parse_args()

    res = inspect_video(
        Path(args.pipeline_dir),
        video_path=Path(args.video) if args.video else None,
        benchmark_path=Path(args.benchmark) if args.benchmark else None,
    )
    if not res["all_passed"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
