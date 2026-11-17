#!/usr/bin/env python3
"""
[Step 7: 调色处理]
1. 基础校色：使用 ffmpeg signalstats 采样分析每条实拍镜头的曝光(YAVG)与白平衡(UAVG/VAVG)，
   计算补偿参数 (eq brightness/contrast + colorbalance)。
2. 风格套用：挂载内置可商用 3D LUT (.cube)（门店暖食 warm_store / 博主清透 clean_creator / 高反差 high_contrast）。
   注意：代码生成的叙事图表 (code_broll) 自动免除色温偏置与强LUT以保持UI配色精确。
输出：pipeline/07_color_plan.json
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import ASSETS_DIR, find_bin, maybe_reexec_in_venv, read_json, run_cmd, write_json


def probe_clip_color_stats(media_path: Path) -> Dict[str, float]:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg or not media_path.exists():
        return {"yavg": 128.0, "uavg": 128.0, "vavg": 128.0}
    cmd = [
        ffmpeg, "-v", "info", "-t", "3.0", "-i", str(media_path),
        "-vf", "select='not(mod(n,15))',format=yuv420p,signalstats,metadata=print",
        "-f", "null", "-",
    ]
    res = run_cmd(cmd, check=False)
    def avg_val(key: str, default: float = 128.0) -> float:
        vals = [float(m) for m in re.findall(rf"lavfi\.signalstats\.{key}=([0-9.]+)", res.stderr)]
        return round(sum(vals) / len(vals), 2) if vals else default

    return {
        "yavg": avg_val("YAVG", 128.0),
        "uavg": avg_val("UAVG", 128.0),
        "vavg": avg_val("VAVG", 128.0),
    }


# 各风格谱光学影调强化曲线（与内置 33³ LUT 叠加，拉开肉眼直观质感与冷暖性格）
STYLE_GRADE_PROFILES: Dict[str, Dict[str, float]] = {
    "style_14": {"b_off": 0.015, "contrast": 1.06, "sat": 1.08, "rs_off": 0.028, "bs_off": -0.024},  # 温暖居家高透
    "style_02": {"b_off": -0.01, "contrast": 1.16, "sat": 0.94, "rs_off": 0.005, "bs_off": 0.005},   # 硬核锐利高反差
    "style_03": {"b_off": 0.01,  "contrast": 1.09, "sat": 1.18, "rs_off": 0.055, "bs_off": -0.052},  # 暖黄高饱和人物故事
    "style_12": {"b_off": 0.045, "contrast": 1.06, "sat": 0.88, "rs_off": -0.01, "bs_off": 0.018},   # 清爽明亮极简通透
    "style_06": {"b_off": -0.055,"contrast": 1.22, "sat": 0.78, "rs_off": -0.032,"bs_off": 0.052},   # 电影低亮冷灰胶片调
    "style_05": {"b_off": 0.04,  "contrast": 1.05, "sat": 0.65, "rs_off": 0.038, "bs_off": -0.038},  # 低饱和暖米工程纸调
    "style_15": {"b_off": -0.065,"contrast": 1.24, "sat": 0.90, "rs_off": -0.045,"bs_off": 0.068},   # 暗夜冷青科技对比调
}


def compute_base_correction(stats: Dict[str, float], style_id: Optional[str] = None) -> Dict[str, Any]:
    y = stats["yavg"]
    u = stats["uavg"]
    v = stats["vavg"]
    prof = STYLE_GRADE_PROFILES.get(str(style_id or ""), {})
    # 目标曝光 YAVG ≈ 125；偏暗适度提亮，过亮略压低，再叠加流派性格偏移
    brightness = round(max(-0.22, min(0.22, (125.0 - y) / 420.0 + float(prof.get("b_off", 0.0)))), 3)
    # U 偏高偏蓝，V 偏高偏红，先轻柔归中再按风格赋予冷暖基调
    bs_shift = round(max(-0.14, min(0.14, (128.0 - u) / 450.0 + float(prof.get("bs_off", 0.0)))), 3)
    rs_shift = round(max(-0.14, min(0.14, (128.0 - v) / 450.0 + float(prof.get("rs_off", 0.0)))), 3)
    return {
        "brightness": brightness,
        "contrast": round(float(prof.get("contrast", 1.05)), 3),
        "saturation": round(float(prof.get("sat", 1.06)), 3),
        "rs": rs_shift,
        "bs": bs_shift,
    }


def run_step7(
    pipeline_dir: Path,
    track: str = "store",
    lut_name: Optional[str] = None,
    style_id: Optional[str] = None,
) -> Dict[str, Any]:
    edl = read_json(pipeline_dir / "04_edl.json")
    if not style_id:
        spec_p = pipeline_dir / "style_spectrum_applied.json"
        if spec_p.exists():
            style_id = read_json(spec_p).get("style_id")
    if not lut_name:
        lut_name = "warm_store.cube" if track == "store" else "clean_creator.cube"
    lut_path = ASSETS_DIR / "luts" / lut_name
    if not lut_path.exists():
        style_lut_path = ASSETS_DIR / "style_spectrum" / "luts" / lut_name
        lut_path = style_lut_path if style_lut_path.exists() else (ASSETS_DIR / "luts" / "clean_creator.cube")

    unique_media_stats: Dict[str, Dict[str, float]] = {}
    clip_plans: List[Dict[str, Any]] = []

    for clip in edl["clips"]:
        mp = clip["media_path"]
        is_code = clip["track_role"] == "code_broll"
        if is_code:
            clip_plans.append({
                "clip_id": clip["clip_id"],
                "track_role": clip["track_role"],
                "apply_color_grade": False,
                "reason": "代码生成UI图表保留原色",
                "vf_filter": None,
            })
            continue

        if mp not in unique_media_stats:
            unique_media_stats[mp] = probe_clip_color_stats(Path(mp))
        stats = unique_media_stats[mp]
        corr = compute_base_correction(stats, style_id=style_id)
        # 先做基础校色 (eq + colorbalance)，再套用 3D LUT
        lut_escaped = str(lut_path.resolve()).replace("\\", "/").replace(":", "\\:")
        vf_str = (
            f"eq=brightness={corr['brightness']}:contrast={corr['contrast']}:saturation={corr['saturation']},"
            f"colorbalance=rs={corr['rs']}:bs={corr['bs']},"
            f"lut3d='{lut_escaped}'"
        )
        clip_plans.append({
            "clip_id": clip["clip_id"],
            "track_role": clip["track_role"],
            "apply_color_grade": True,
            "measured_stats": stats,
            "base_correction": corr,
            "lut_file": str(lut_path.resolve()),
            "vf_filter": vf_str,
        })

    plan = {
        "step": "07_color_grade",
        "lut_name": lut_path.name,
        "lut_path": str(lut_path.resolve()),
        "clip_plans": clip_plans,
    }
    write_json(pipeline_dir / "07_color_plan.json", plan)
    graded_cnt = sum(1 for c in clip_plans if c["apply_color_grade"])
    print(f"[step7] 调色方案生成完成：{graded_cnt} 个实拍镜头先校色再套 `{lut_path.name}` -> 07_color_plan.json")
    return plan


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 7: 基础校色与风格 LUT 规划")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--lut", help="LUT 文件名（如 warm_store.cube / clean_creator.cube / high_contrast.cube）")
    args = parser.parse_args()
    run_step7(Path(args.pipeline_dir), track=args.track, lut_name=args.lut)


if __name__ == "__main__":
    main()
