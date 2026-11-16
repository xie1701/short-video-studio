#!/usr/bin/env python3
"""
short-video-studio 统一流水线编排器
支持 4 种工作模式：
1. full           : 跑通 Step 1 -> Step 9 -> 渲染预览与成片 -> 导出剪映草稿 -> 9项自动质检 -> 生成《我替你做的决定》
2. lock-preview   : 停在打断#3，输出锁定音轨与低清粗剪预览 (output_preview.mp4)
3. reedit-visuals : [打断#3 分支A] 用户只改画面 -> 严格保持 Step 3 锁定音轨不变，重排 Step 4-9 + 出片 + 质检
4. relock-voice   : [打断#3 分支B] 用户改口播词 -> 回到 Step 2/3 重新锁定音轨，再自动重排 Step 4-9 + 出片 + 质检
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from common import ASSETS_DIR, maybe_reexec_in_venv, read_json

maybe_reexec_in_venv()

from export_jianying import export_jianying_draft
from generate_code_broll import generate_demo_media_pack
from qa_inspector import inspect_video
from render_mp4 import render_mp4
from step1_normalize import run_step1
from step2_transcribe import run_step2
from step3_lock_audio import run_step3
from step4_build_edl import run_step4
from step5_subtitles import run_step5
from step6_packaging import run_step6
from step7_color_grade import run_step7
from step8_mix_audio import run_step8
from step9_title_cover import run_step9


def write_decisions_made(pipeline_dir: Path, track: str) -> Path:
    """生成《我替你做的决定》默认参数清单，方便用户一句话直接修改。"""
    ae = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    sub = read_json(pipeline_dir / "05_subtitles.json")
    pkg = read_json(pipeline_dir / "06_packaging.json")
    col = read_json(pipeline_dir / "07_color_plan.json")
    mix = read_json(pipeline_dir / "08_mix_metrics.json")
    jy = read_json(pipeline_dir / "jianying_export_status.json") if (pipeline_dir / "jianying_export_status.json").exists() else {}

    lines = [
        "# 我替你做的决定（默认配置与一句话修改指南）",
        "",
        "为了让创作尽量少打断你，除核心选题、素材确认和音轨粗剪预览外，其余后期细节已按平台最优默认值完成：",
        "",
        "| 环节 | 我替你做的默认决定 | 如何一句话修改 |",
        "| :--- | :--- | :--- |",
        f"| **适用轨道** | `{'门店轨 (同城转化)' if track=='store' else '博主轨 (留存涨粉)'}` | “改按博主轨/门店轨风格重出” |",
        f"| **BGM 与强拍对齐** | `{Path(ae['bgm']['file']).name}` ({ae['bgm']['bpm']} BPM)，钩子首字对齐 0.00s 强拍 | “换一首更舒缓/更燃的 BGM” |",
        f"| **口播句间停顿** | 统一裁为 `{ae['pause_duration_sec']}s`（已自动删掉废话与气口） | “把句间停顿改紧凑点(0.15s)或舒缓点(0.28s)” |",
        f"| **J-cut 音画交错** | 句与句之间声音先进 `{edl['j_cut_default_frames']} 帧`，画面再切 | “把 J-cut 改成 2 帧或 4 帧” |",
        f"| **字幕样式模板** | `{sub['template_name']}` (`{sub['template_id']}`)，可商用字体 `{Path(sub['font_path']).name if sub.get('font_path') else '默认字体'}` | “字幕换成极简白/强反差红黑/暖米色模板” |",
        f"| **包装密度预算** | 音效 {pkg['budget']['used_sfx_cues']}/{pkg['budget']['max_sfx_cues']} 个，花字贴纸 {pkg['budget']['used_visual_elements']}/{pkg['budget']['max_visual_elements']} 个（均在安全区±0帧对齐） | “把花字贴纸再精简一点” |",
        f"| **调色 LUT** | 自动白平衡曝光基础校色 + `{col['lut_name']}` | “调色换成清透自然 / 高反差 LUT” |",
        f"| **混音响度** | 人声降噪 + BGM 自动闪避，成品响度 `{mix['measured_lufs']:.2f} LUFS` | “BGM 音量再小一点 / 大一点” |",
        f"| **出片与草稿** | 高清成片 `output_final.mp4` + 剪映草稿 `{jy.get('status', 'unknown')}` | “导出剪映草稿供我微调” |",
        "",
    ]
    out_md = pipeline_dir / "decisions_made.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_md


def run_visual_to_delivery(
    pipeline_dir: Path,
    track: str,
    j_cut_frames: int,
    subtitle_template: Optional[str],
    lut_name: Optional[str],
    prefer_broll_only: bool,
    benchmark: Optional[Path],
    simulate_jy_fail: bool,
    preview_only: bool = False,
) -> Dict[str, Any]:
    run_step4(pipeline_dir, track=track, j_cut_frames=j_cut_frames, prefer_broll_only=prefer_broll_only)
    run_step5(pipeline_dir, track=track, template_id=subtitle_template)
    run_step6(pipeline_dir)
    run_step7(pipeline_dir, track=track, lut_name=lut_name)
    run_step8(pipeline_dir)
    run_step9(pipeline_dir, track=track)

    preview_mp4 = render_mp4(pipeline_dir, preview=True)
    if preview_only:
        return {"preview_mp4": str(preview_mp4)}

    final_mp4 = render_mp4(pipeline_dir, preview=False)
    jy_status = export_jianying_draft(pipeline_dir, simulate_failure=simulate_jy_fail)
    qa_report = inspect_video(pipeline_dir, video_path=final_mp4, benchmark_path=benchmark)
    decisions_md = write_decisions_made(pipeline_dir, track)

    return {
        "preview_mp4": str(preview_mp4),
        "final_mp4": str(final_mp4),
        "jianying": jy_status,
        "qa_report": qa_report,
        "decisions_made_md": str(decisions_md),
    }


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="short-video-studio 端到端流水线编排器")
    parser.add_argument("--project-dir", required=True, help="项目根目录")
    parser.add_argument("--raw-dir", help="原始素材目录（默认 <project-dir>/raw_media）")
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument(
        "--mode",
        choices=["full", "lock-preview", "reedit-visuals", "relock-voice"],
        default="full",
        help="执行模式",
    )
    parser.add_argument("--script-json", help="口播或配音台词 JSON 文件")
    parser.add_argument("--force-tts", action="store_true", help="强制使用文案生成 TTS 配音")
    parser.add_argument("--bgm", help="自定义 BGM 文件路径")
    parser.add_argument("--pause-sec", type=float, default=0.20, help="句间停顿 (0.15-0.30s)")
    parser.add_argument("--j-cut-frames", type=int, default=None, help="句间 J-cut 声先入帧数 (默认 3，或由 --style-id 决定)")
    parser.add_argument("--style-id", help="可选：套用风格谱模板 ID (style_01 ~ style_20)，自动绑定字幕/LUT/J-cut")
    parser.add_argument("--subtitle-template", help="字幕模板 ID")
    parser.add_argument("--lut", help="调色 LUT 文件名")
    parser.add_argument("--prefer-broll-only", action="store_true", help="改画模式：尽量切换为纯 B-roll/代码补镜")
    parser.add_argument("--benchmark", help="可选：对标视频路径")
    parser.add_argument("--simulate-jy-fail", action="store_true", help="测试剪映草稿失效降级保护")
    args = parser.parse_args()

    # 若传入 --style-id，自动从风格谱主库加载默认字幕模板、3D LUT 与 J-cut 参数
    j_cut_frames = 3 if args.j_cut_frames is None else args.j_cut_frames
    subtitle_template = args.subtitle_template
    lut_name = args.lut
    if args.style_id:
        lib_path = ASSETS_DIR / "style_spectrum" / "style_spectrum_library.json"
        if not lib_path.exists():
            raise FileNotFoundError(f"未找到风格谱数据库: {lib_path}")
        lib = read_json(lib_path)
        entry = next((s for s in lib.get("styles", []) if s["style_id"] == args.style_id), None)
        if not entry:
            raise KeyError(f"风格谱 ID 不存在: {args.style_id}（可选 style_01 ~ style_20）")
        if not subtitle_template:
            subtitle_template = args.style_id
        if not lut_name:
            lut_name = f"{args.style_id}.cube"
        if args.j_cut_frames is None:
            j_cut_frames = int(entry.get("beat_alignment", {}).get("recommended_j_cut_frames", 3))
        print(
            f"[pipeline] 已挂载风格谱 {args.style_id} ({entry['style_name_cn']}) -> "
            f"字幕={subtitle_template}, LUT={lut_name}, J-cut={j_cut_frames}帧"
        )

    project_dir = Path(args.project_dir)
    raw_dir = Path(args.raw_dir) if args.raw_dir else (project_dir / "raw_media")
    pipeline_dir = project_dir / "pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)

    script_p = Path(args.script_json) if args.script_json else None
    bgm_p = Path(args.bgm) if args.bgm else None
    bench_p = Path(args.benchmark) if args.benchmark else None

    if args.mode in ("full", "lock-preview"):
        if not raw_dir.exists() or not any(raw_dir.glob("*.mp4")):
            print("[pipeline] 未检测到原始素材，自动生成演示素材包...")
            generate_demo_media_pack(raw_dir)
        run_step1([raw_dir], pipeline_dir)
        run_step2(pipeline_dir, track=args.track, script_json_path=script_p, force_tts=args.force_tts)
        run_step3(pipeline_dir, track=args.track, bgm_path=bgm_p, pause_sec=args.pause_sec)

    elif args.mode == "relock-voice":
        if not (pipeline_dir / "01_manifest.json").exists():
            run_step1([raw_dir], pipeline_dir)
        # 改口播词：从 Step 2/3 重新锁定音轨，再重排 Step 4-9
        run_step2(pipeline_dir, track=args.track, script_json_path=script_p, force_tts=True if script_p else args.force_tts)
        run_step3(pipeline_dir, track=args.track, bgm_path=bgm_p, pause_sec=args.pause_sec)

    elif args.mode == "reedit-visuals":
        # 只改画面：严格校验锁定音轨存在且绝不触碰 03_* 文件
        if not (pipeline_dir / "03_audio_events.json").exists():
            raise FileNotFoundError("reedit-visuals 模式要求 03_audio_events.json 已锁定存在。")
        print("[pipeline] 只改画面模式：Step 1–3 已锁定音轨保持原封不动，仅按音轨事件重排画面。")

    res = run_visual_to_delivery(
        pipeline_dir=pipeline_dir,
        track=args.track,
        j_cut_frames=j_cut_frames,
        subtitle_template=subtitle_template,
        lut_name=lut_name,
        prefer_broll_only=args.prefer_broll_only,
        benchmark=bench_p,
        simulate_jy_fail=args.simulate_jy_fail,
        preview_only=(args.mode == "lock-preview"),
    )

    if args.mode != "lock-preview" and not res["qa_report"]["all_passed"]:
        print("[ERROR] 成片未通过 9 项自动质检，已阻断交付！请查看 10_qa_report.md")
        sys.exit(2)

    print("\n[OK] 流水线执行完毕。")


if __name__ == "__main__":
    main()
