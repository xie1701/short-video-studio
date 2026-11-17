#!/usr/bin/env python3
"""
[入口 2：带素材进来] 素材盘点与选题反推脚本
功能：
1. 扫描输入目录的视音频素材，提取时长、分辨率、编码、帧率、VFR(可变帧率)检测、音频轨道状态。
2. 利用 ffmpeg signalstats 检测平均亮度(曝光过暗/过曝)及 silencedetect 检测静音/有效有声片段。
3. 基于盘点结果反推最适合的轨道公式、5A层级、话题方向与剪辑策略建议。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import find_bin, ffprobe_media, maybe_reexec_in_venv, parse_fps, run_cmd, write_json

MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def analyze_video_quality(path: Path, duration: float) -> Dict[str, Any]:
    """用 ffmpeg 快速采样检测亮度曝光与静音比例。"""
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg or duration <= 0.1:
        return {"exposure": "unknown", "avg_luma": None, "silence_ratio": 0.0, "issues": []}

    issues: List[str] = []
    # 采样检测亮度（最多抽前 15 秒，每 10 帧抽 1 帧并归一化到 8-bit yuv420p，避免 10-bit 素材 YAVG 溢出 >255）
    cmd = [
        ffmpeg, "-v", "info", "-t", str(min(duration, 15.0)), "-i", str(path),
        "-vf", "select='not(mod(n,10))',format=yuv420p,signalstats,metadata=print",
        "-f", "null", "-",
    ]
    res = run_cmd(cmd, check=False)
    yavg_vals = [float(m) for m in re.findall(r"lavfi\.signalstats\.YAVG=([0-9.]+)", res.stderr)]
    avg_luma = round(sum(yavg_vals) / len(yavg_vals), 1) if yavg_vals else 128.0
    if avg_luma < 55:
        exposure = "underexposed"
        issues.append(f"画面偏暗 (YAVG={avg_luma})，建议第7步适当提亮曝光")
    elif avg_luma > 205:
        exposure = "overexposed"
        issues.append(f"画面过曝 (YAVG={avg_luma})，建议第7步压低高光")
    else:
        exposure = "normal"

    # 静音检测
    cmd_sil = [
        ffmpeg, "-v", "info", "-i", str(path),
        "-af", "silencedetect=noise=-42dB:d=0.6",
        "-f", "null", "-",
    ]
    res_sil = run_cmd(cmd_sil, check=False)
    sil_durs = [float(m) for m in re.findall(r"silence_duration:\s*([0-9.]+)", res_sil.stderr)]
    sil_total = sum(sil_durs)
    sil_ratio = round(min(1.0, sil_total / max(duration, 0.1)), 2)
    if sil_ratio > 0.6:
        issues.append(f"有效人声较少(静音占比 {int(sil_ratio*100)}%)，适合作为 B-roll 或补拍/配音")

    return {
        "exposure": exposure,
        "avg_luma": avg_luma,
        "silence_ratio": sil_ratio,
        "issues": issues,
    }


def classify_clip(v_stream: Dict[str, Any], a_stream: Optional[Dict[str, Any]], quality: Dict[str, Any], duration: float) -> str:
    if a_stream and quality["silence_ratio"] < 0.55 and duration >= 2.0:
        return "a_roll_candidate"
    return "b_roll_candidate"


def recommend_angle(clips: List[Dict[str, Any]], track: str) -> Dict[str, Any]:
    """根据素材盘点结果，依据《建立可循环的内容系统》反推最稳妥的选题结构建议。"""
    has_aroll = any(c["suggested_role"] == "a_roll_candidate" for c in clips)
    total_dur = round(sum(c["duration_sec"] for c in clips), 2)
    broll_count = sum(1 for c in clips if c["suggested_role"] == "b_roll_candidate")

    if track == "store":
        if has_aroll:
            formula = "水桶流视频公式：精准人群钩子 + 产品/环境细节展示 + 老板人格化表达(原则/经历) + 价值说明 + 转化指令"
            hook = "颠覆切入 / 痛点切入（例如：做这行这么多年，只坚持一条底线…）"
            a_level = "A2 人设信任型 / A3 垂直说服型"
        else:
            formula = "漏斗流视频公式：自带流量同城/行业话题 + 细节B-roll视觉展示 + TTS配音专业植入 + 人设结尾"
            hook = "信息密度开篇 + 好奇切入（用高食欲/高冲击画面配TTS首句）"
            a_level = "A1 泛流量型"
    else:
        if has_aroll:
            formula = "漏斗流视频公式：话题钩子(社会/认知/内幕) + 观点+原因+案例 + 人设露出 + 互动结尾"
            hook = "对立面开篇 / 不字开头 / 认知颠覆切入"
            a_level = "A1 泛流量型 / A2 人设信任型"
        else:
            formula = "纯视觉+代码补镜流：信息缺口开篇 + TTS节奏配音 + B-roll与数据图表混剪 + 金句结尾"
            hook = "数字开篇 / 信息缺口开篇"
            a_level = "A1 泛流量型"

    missing_alert = []
    if broll_count < 2:
        missing_alert.append("可用 B-roll 画面较少，建议 Step 4 自动使用代码生成叙事素材(文字动画/数据图)补齐前 3 秒多切点")
    if not has_aroll:
        missing_alert.append("缺少清晰同期声口播素材，Step 2/3 将自动引导使用文案 TTS 配音或请用户补录一段配音")

    return {
        "track": track,
        "total_raw_duration_sec": total_dur,
        "has_usable_aroll": has_aroll,
        "recommended_5a_level": a_level,
        "recommended_formula": formula,
        "recommended_hook": hook,
        "gaps_and_remedy": missing_alert,
    }


def inventory_directory(input_dir: Path, track: str) -> Dict[str, Any]:
    files = sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTS])
    clips: List[Dict[str, Any]] = []

    for f in files:
        meta = ffprobe_media(f)
        streams = meta.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if not v_stream:
            continue

        dur = float(meta.get("format", {}).get("duration") or v_stream.get("duration") or 0.0)
        w = int(v_stream.get("width", 0))
        h = int(v_stream.get("height", 0))
        r_fps = parse_fps(v_stream.get("r_frame_rate", "0/1"))
        avg_fps = parse_fps(v_stream.get("avg_frame_rate", "0/1"))
        is_vfr = abs(r_fps - avg_fps) > 0.5 or avg_fps == 0.0

        quality = analyze_video_quality(f, dur)
        issues = list(quality["issues"])
        if is_vfr:
            issues.append(f"检测到可变帧率 VFR (r_fps={r_fps:.2f}, avg_fps={avg_fps:.2f})，Step 1 将强制转为 30fps CFR 防音画漂移")
        if w > h:
            issues.append(f"横屏素材 ({w}x{h})，Step 1 将自动裁切/居中适配 1080x1920 竖屏")
        if w < 720 or h < 1080:
            issues.append(f"分辨率偏低 ({w}x{h})，建议仅作局部插画或超分升采样")

        role = classify_clip(v_stream, a_stream, quality, dur)
        clips.append({
            "filename": f.name,
            "path": str(f.resolve()),
            "duration_sec": round(dur, 2),
            "width": w,
            "height": h,
            "r_fps": round(r_fps, 2),
            "avg_fps": round(avg_fps, 2),
            "is_vfr": is_vfr,
            "has_audio": a_stream is not None,
            "quality": quality,
            "issues": issues,
            "suggested_role": role,
            "usable": dur >= 0.8,
        })

    recommendation = recommend_angle(clips, track)
    return {
        "input_dir": str(input_dir.resolve()),
        "clip_count": len(clips),
        "clips": clips,
        "recommendation": recommendation,
    }


def render_markdown_summary(data: Dict[str, Any]) -> str:
    rec = data["recommendation"]
    lines = [
        "# 素材盘点与选题反推报告",
        "",
        f"- **盘点素材总数**：{data['clip_count']} 条（累计原始时长 {rec['total_raw_duration_sec']} 秒）",
        f"- **适用轨道**：{'门店轨' if rec['track']=='store' else '博主轨'}",
        f"- **推荐 5A 层级**：{rec['recommended_5a_level']}",
        f"- **反推视频公式**：{rec['recommended_formula']}",
        f"- **建议开篇钩子**：{rec['recommended_hook']}",
        "",
        "## 素材明细表",
        "",
        "| 文件名 | 时长 | 分辨率 | VFR | 有声 | 建议用途 | 画质与规整诊断 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for c in data["clips"]:
        diag = "；".join(c["issues"]) if c["issues"] else "正常可用"
        lines.append(
            f"| `{c['filename']}` | {c['duration_sec']}s | {c['width']}x{c['height']} | "
            f"{'是(需转CFR)' if c['is_vfr'] else '否'} | {'有' if c['has_audio'] else '无'} | "
            f"{'同期声口播(A-roll)' if c['suggested_role']=='a_roll_candidate' else '空镜/特写(B-roll)'} | {diag} |"
        )
    if rec["gaps_and_remedy"]:
        lines += ["", "## 缺口与自动补救策略"] + [f"- {g}" for g in rec["gaps_and_remedy"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="素材盘点与选题反推")
    parser.add_argument("--input-dir", required=True, help="原始素材文件夹路径")
    parser.add_argument("--output", required=True, help="输出 JSON 路径")
    parser.add_argument("--track", choices=["store", "creator"], default="store", help="门店轨或博主轨")
    args = parser.parse_args()

    inv = inventory_directory(Path(args.input_dir), args.track)
    out_json = Path(args.output)
    write_json(out_json, inv)
    out_md = out_json.with_suffix(".md")
    out_md.write_text(render_markdown_summary(inv), encoding="utf-8")
    print(f"[inventory] 已盘点 {inv['clip_count']} 条素材 -> {out_json} & {out_md}")


if __name__ == "__main__":
    main()
