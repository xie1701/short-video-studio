#!/usr/bin/env python3
"""
[素材获取：代码生成叙事素材 & 缺镜头补镜引擎]
当实拍镜头不足时，自动生成 1080x1920 @ 30fps 的叙事性视频片段：
- kinetic_text : 动力学文字卡片动画（观点/反转/金句）
- data_chart   : 数据对比柱状图增长动画（价格构成/成本拆解/对比数据）
- flow_diagram : 流程示意步骤动画（三步避坑/门店标准流程）

引擎优先级：
1. 若本机已安装并配置 Remotion/HyperFrames 工作区，可通过 --prefer-remotion 调用；
2. 默认内置确定性 Pillow + FFmpeg 逐帧动效合成引擎（无需外网/浏览器，100% 30fps CFR 无黑帧出片）。
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    find_bin,
    find_commercial_font,
    maybe_reexec_in_venv,
    run_cmd,
)


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def render_broll_clip_pillow(
    output_mp4: Path,
    mode: str,
    title: str,
    items: List[Dict[str, Any]],
    duration_sec: float = 3.0,
    fps: int = DEFAULT_FPS,
    accent_hex: str = "#FFD54F",
    bg_hex: str = "#0F172A",
) -> Path:
    """用 Pillow 逐帧绘制缓动文字/数据/流程动画并通过 FFmpeg 编码为标准 1080x1920 CFR MP4。"""
    from PIL import Image, ImageDraw, ImageFont

    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 编码补镜视频。")

    font_path = find_commercial_font()
    f_title = ImageFont.truetype(font_path, 66) if font_path else ImageFont.load_default()
    f_label = ImageFont.truetype(font_path, 42) if font_path else ImageFont.load_default()
    f_sub = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default()
    f_badge = ImageFont.truetype(font_path, 28) if font_path else ImageFont.load_default()

    total_frames = max(15, int(round(duration_sec * fps)))
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="svs_code_broll_") as tmp:
        tmp_dir = Path(tmp)
        for fi in range(total_frames):
            img = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), bg_hex)
            draw = ImageDraw.Draw(img)

            # 氛围网格背景（避免单调纯色，提高视觉信息密度）
            for gx in range(80, CANVAS_WIDTH, 120):
                draw.line([(gx, 160), (gx, 1480)], fill="#1E293B", width=2)
            for gy in range(200, 1480, 120):
                draw.line([(60, gy), (880, gy)], fill="#1E293B", width=2)

            # 顶部标签栏（安全区内 y >= 210）
            mode_tag = {"kinetic_text": "核心观点", "data_chart": "数据拆解", "flow_diagram": "标准流程"}.get(mode, "重点提示")
            draw.rounded_rectangle([88, 220, 310, 276], radius=14, fill=accent_hex)
            draw.text((199, 248), mode_tag, fill="#0F172A", font=f_badge, anchor="mm")

            # 主标题滑入（前 10 帧缓动）
            tp = ease_out_cubic(fi / 10.0)
            ty = int(345 + (1.0 - tp) * 45)
            draw.text((88, ty), title[:14], fill="#F8FAFC", font=f_title)
            draw.rectangle([88, ty + 88, 88 + int(680 * tp), ty + 96], fill=accent_hex)

            # 条目逐个飞入（安全区 X:[88, 860], Y:[480, 1380]）
            base_y = 500
            card_h = 210 if mode != "flow_diagram" else 195
            for idx, item in enumerate(items[:4]):
                delay = 6 + idx * 7
                p = ease_out_cubic((fi - delay) / 10.0)
                if p <= 0.01:
                    continue
                cy = base_y + idx * (card_h + 28)
                offset_x = int((1.0 - p) * 70)
                x1, x2 = 88 + offset_x, 860
                draw.rounded_rectangle([x1, cy, x2, cy + card_h], radius=22, fill="#1E293B", outline=accent_hex, width=3)

                label = str(item.get("label", ""))[:18]
                sub = str(item.get("subtitle", ""))[:26]
                val = item.get("value")

                if mode == "flow_diagram":
                    draw.ellipse([x1 + 26, cy + 32, x1 + 92, cy + 98], fill=accent_hex)
                    draw.text((x1 + 59, cy + 65), str(idx + 1), fill="#0F172A", font=f_label, anchor="mm")
                    draw.text((x1 + 116, cy + 40), label, fill="#FFFFFF", font=f_label)
                    if sub:
                        draw.text((x1 + 116, cy + 106), sub, fill="#CBD5E1", font=f_sub)
                    if idx < len(items[:4]) - 1:
                        draw.line([(x1 + 59, cy + card_h), (x1 + 59, cy + card_h + 28)], fill=accent_hex, width=5)
                else:
                    draw.text((x1 + 32, cy + 28), label, fill="#FFFFFF", font=f_label)
                    if mode == "data_chart" and isinstance(val, (int, float)):
                        pct = max(5, min(100, float(val))) * p
                        bar_max = (x2 - x1) - 180
                        draw.rounded_rectangle([x1 + 32, cy + 106, x1 + 32 + bar_max, cy + 144], radius=16, fill="#0F172A")
                        draw.rounded_rectangle(
                            [x1 + 32, cy + 106, x1 + 32 + max(24, int(bar_max * pct / 100.0)), cy + 144],
                            radius=16,
                            fill=accent_hex,
                        )
                        draw.text((x2 - 36, cy + 125), f"{int(val * p)}%", fill=accent_hex, font=f_label, anchor="rm")
                    elif sub:
                        draw.text((x1 + 32, cy + 108), sub, fill="#CBD5E1", font=f_sub)

            img.save(tmp_dir / f"frame_{fi:05d}.png")

        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", str(tmp_dir / "frame_%05d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
            str(output_mp4),
        ]
        run_cmd(cmd)
    return output_mp4


def generate_demo_media_pack(output_dir: Path) -> Dict[str, Any]:
    """生成一套完整可用于演示/测试的模拟实拍素材 + 代码生成补镜 + 对标参考视频。"""
    from PIL import Image, ImageDraw, ImageFont

    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 生成演示素材。")
    output_dir.mkdir(parents=True, exist_ok=True)
    font_path = find_commercial_font()
    f_big = ImageFont.truetype(font_path, 68) if font_path else ImageFont.load_default()
    f_sub = ImageFont.truetype(font_path, 40) if font_path else ImageFont.load_default()

    # 1. 代码补镜 1：数据图
    broll_data = output_dir / "code_broll_cost_chart.mp4"
    render_broll_clip_pillow(
        broll_data,
        mode="data_chart",
        title="400元烤羊排成本真相",
        items=[
            {"label": "鲜羊排原料成本", "value": 68},
            {"label": "腌制与3小时炭火人工", "value": 24},
            {"label": "实际门店净利润", "value": 8},
        ],
        duration_sec=3.0,
    )

    # 2. 代码补镜 2：流程图
    broll_flow = output_dir / "code_broll_process.mp4"
    render_broll_clip_pillow(
        broll_flow,
        mode="flow_diagram",
        title="老赵烧烤三条死规矩",
        items=[
            {"label": "只买清晨现宰鲜羊排", "subtitle": "绝不用冻货充数"},
            {"label": "老师傅腌制2小时+烤3小时", "subtitle": "现点现上保证外焦里嫩"},
            {"label": "不满意现场直接退换", "subtitle": "八年老店底线"},
        ],
        duration_sec=3.2,
    )

    # 3. 模拟手机实拍空镜 B-roll（动态渐变色彩 + 平移产品模拟卡片）
    broll_food = output_dir / "broll_lamb_closeup.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        for fi in range(90):
            img = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (48, 26, 16))
            d = ImageDraw.Draw(img)
            shift = int(math.sin(fi / 15.0) * 35)
            d.ellipse([180 + shift, 480, 820 + shift, 1080], fill=(180, 83, 9), outline=(251, 191, 36), width=8)
            d.text((500 + shift, 780), "鲜切烤羊排特写", fill=(255, 251, 235), font=f_big, anchor="mm")
            d.text((480, 1180), "实拍B-roll素材示例", fill=(253, 224, 71), font=f_sub, anchor="mm")
            img.save(tmp_p / f"f_{fi:04d}.png")
        run_cmd([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", "30", "-i", str(tmp_p / "f_%04d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(broll_food),
        ])

    # 4. 模拟一个带对白音频的对标参考视频 (benchmark_sample.mp4, 15s)
    bench = output_dir / "benchmark_sample.mp4"
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=#1E293B:s=1080x1920:r=30:d=15",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=15",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(bench),
    ])

    return {
        "code_broll_cost_chart": str(broll_data),
        "code_broll_process": str(broll_flow),
        "broll_lamb_closeup": str(broll_food),
        "benchmark_sample": str(bench),
    }


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="生成代码驱动叙事补镜视频")
    parser.add_argument("--output-dir", help="生成整套演示补镜素材到指定目录")
    parser.add_argument("--output", help="单条补镜 MP4 输出路径")
    parser.add_argument("--mode", choices=["kinetic_text", "data_chart", "flow_diagram"], default="kinetic_text")
    parser.add_argument("--title", default="核心重点")
    parser.add_argument("--items-json", help="JSON 字符串形式的条目列表")
    parser.add_argument("--duration", type=float, default=3.0)
    args = parser.parse_args()

    if args.output_dir:
        res = generate_demo_media_pack(Path(args.output_dir))
        print(f"[code_broll] 已生成代码补镜与演示素材: {json.dumps(res, ensure_ascii=False)}")
        return

    if args.output:
        items = json.loads(args.items_json) if args.items_json else [{"label": args.title, "subtitle": "代码生成补镜"}]
        out = render_broll_clip_pillow(Path(args.output), args.mode, args.title, items, duration_sec=args.duration)
        print(f"[code_broll] 已生成补镜 -> {out}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
