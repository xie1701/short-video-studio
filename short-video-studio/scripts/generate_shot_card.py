#!/usr/bin/env python3
"""
[素材获取：拍摄任务卡]
为需要实拍的短视频生成傻瓜式手机拍摄任务卡：
逐个镜头写明：拍什么、机位与景别、目标时长、备份条数、手机拍摄要点，并生成 SVG 简笔构图示意卡片。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from common import write_json

DEFAULT_SHOTS = [
    {
        "shot_no": 1,
        "role": "A-roll 钩子口播",
        "what": "老板/主理人直视镜头，手里拿核心产品或账单/原料，说出前 3 秒开篇钩子",
        "angle_framing": "正面平视 · 中近景（胸部以上，头顶留 15% 空间避开顶部 UI 栏）",
        "duration_sec": 3.5,
        "backup_takes": 3,
        "diagram_type": "portrait_hook",
        "phone_tips": "竖屏 1080p/30fps（关闭HDR避免发灰）；用领夹麦或距离手机 40cm 内收音；脸部正对窗户自然光",
        "spoken_line": "钩子第一句（先声夺人 + 高密度画面）",
    },
    {
        "shot_no": 2,
        "role": "B-roll 核心细节特写",
        "what": "近距离拍产品细节/制作过程/食材纹理/检测动作（用于盖在口播上做前3秒多切点）",
        "angle_framing": "45°俯拍 · 特写（镜头距主体 20-30cm，点击屏幕锁定对焦）",
        "duration_sec": 4.0,
        "backup_takes": 2,
        "diagram_type": "closeup_detail",
        "phone_tips": "开启网格线将主体放中间偏上安全区；手腕贴桌保持稳，横移或微推 2 秒即可，不要乱晃",
        "spoken_line": "覆盖在第一段关键词重音处，仅换画面不改原声",
    },
    {
        "shot_no": 3,
        "role": "B-roll 门店/工作场景背书",
        "what": "门头/工作台/顾客排队/制作环境全景到中景切换，证明“真实靠谱”",
        "angle_framing": "微仰拍或平视横摇 · 中全景",
        "duration_sec": 5.0,
        "backup_takes": 2,
        "diagram_type": "wide_scene",
        "phone_tips": "用 1x 主摄不要用广角防畸变；从左向右匀速平移 4 秒，每段多留 1 秒头尾余量",
        "spoken_line": "配合人设表达（初心/原则/底线）与价值说明段",
    },
    {
        "shot_no": 4,
        "role": "A-roll 结尾人设与转化指令",
        "what": "回到真人直视镜头，给出坚定承诺或互动指令（评论/私信/到店）",
        "angle_framing": "微侧 15° · 近景（营造朋友面对面聊天感）",
        "duration_sec": 4.5,
        "backup_takes": 2,
        "diagram_type": "portrait_hook",
        "phone_tips": "说完最后一字停顿 1 秒微笑定格，不要话音未落就伸手关录制",
        "spoken_line": "结尾金句 + 互动/转化指令",
    },
]


def build_sketch_svg(diagram_type: str, shot_no: int) -> str:
    """生成清晰直观的 9:16 竖屏简笔构图示意 SVG（标出平台安全框与主体位置）。"""
    base_header = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="320" viewBox="0 0 180 320">'
        '<rect x="2" y="2" width="176" height="316" rx="14" fill="#F8FAFC" stroke="#334155" stroke-width="3"/>'
        '<rect x="10" y="26" width="136" height="218" fill="none" stroke="#10B981" stroke-width="1.5" stroke-dasharray="4,3"/>'
        '<text x="14" y="20" font-size="9" fill="#64748B" font-family="sans-serif">TOP UI SAFE</text>'
        '<text x="14" y="262" font-size="9" fill="#EF4444" font-family="sans-serif">BOTTOM UI 遮挡区</text>'
        '<rect x="148" y="115" width="24" height="125" rx="4" fill="#E2E8F0"/>'
        '<text x="160" y="180" font-size="8" fill="#64748B" text-anchor="middle" transform="rotate(90 160 180)">互动栏</text>'
    )
    if diagram_type == "portrait_hook":
        body = (
            '<circle cx="78" cy="92" r="24" fill="#FDE68A" stroke="#0F172A" stroke-width="2.5"/>'
            '<path d="M40 185 C40 135, 116 135, 116 185 Z" fill="#93C5FD" stroke="#0F172A" stroke-width="2.5"/>'
            '<circle cx="70" cy="88" r="2.5" fill="#0F172A"/><circle cx="86" cy="88" r="2.5" fill="#0F172A"/>'
            '<path d="M71 100 Q78 106 85 100" fill="none" stroke="#0F172A" stroke-width="2"/>'
            '<rect x="30" y="195" width="96" height="28" rx="6" fill="#FEF08A" stroke="#CA8A04" stroke-width="1.5"/>'
            '<text x="78" y="213" font-size="10" font-weight="bold" text-anchor="middle" fill="#0F172A">字幕安全区</text>'
        )
    elif diagram_type == "closeup_detail":
        body = (
            '<ellipse cx="78" cy="145" rx="48" ry="34" fill="#FED7AA" stroke="#9A3412" stroke-width="2.5"/>'
            '<rect x="44" y="128" width="68" height="34" rx="8" fill="#FB923C" stroke="#7C2D12" stroke-width="2"/>'
            '<line x1="25" y1="80" x2="55" y2="115" stroke="#0284C7" stroke-width="2.5" marker-end="url(#a)"/>'
            '<text x="78" y="70" font-size="11" font-weight="bold" text-anchor="middle" fill="#0369A1">45° 俯拍特写</text>'
            '<text x="78" y="150" font-size="11" font-weight="bold" text-anchor="middle" fill="#FFFFFF">产品主体</text>'
        )
    else:
        body = (
            '<rect x="24" y="88" width="108" height="90" fill="#E0E7FF" stroke="#3730A3" stroke-width="2.5"/>'
            '<rect x="52" y="126" width="52" height="52" fill="#A5B4FC" stroke="#3730A3" stroke-width="2"/>'
            '<path d="M26 205 L128 205" stroke="#059669" stroke-width="3"/>'
            '<polygon points="128,200 138,205 128,210" fill="#059669"/>'
            '<text x="78" y="75" font-size="11" font-weight="bold" text-anchor="middle" fill="#312E81">环境全景横移</text>'
            '<text x="78" y="222" font-size="9" text-anchor="middle" fill="#059669">匀速横扫 4s</text>'
        )
    footer = (
        f'<text x="78" y="295" font-size="11" font-weight="bold" text-anchor="middle" fill="#0F172A">镜头 #{shot_no} 构图框</text>'
        '</svg>'
    )
    return base_header + body + footer


def build_ascii_sketch(diagram_type: str) -> str:
    if diagram_type == "portrait_hook":
        return "\n".join([
            "┌──────────────────────┐",
            "│  (避开顶部UI遮挡区)  │",
            "│       ╭───╮          │",
            "│       │^_^│  ←人物   │",
            "│      ╭┴───┴╮  居中   │",
            "│      │     │         │",
            "│ [字幕黄金安全区]     │",
            "│░░░░░░░░░░░░░░░░░░░░░░│",
            "└──────────────────────┘",
        ])
    if diagram_type == "closeup_detail":
        return "\n".join([
            "┌──────────────────────┐",
            "│  ↘ 手机45°俯拍       │",
            "│                      │",
            "│     ╔══════════╗     │",
            "│     ║ 产品特写 ║     │",
            "│     ╚══════════╝     │",
            "│  (锁定对焦·稳住手腕) │",
            "│░░░░░░░░░░░░░░░░░░░░░░│",
            "└──────────────────────┘",
        ])
    return "\n".join([
        "┌──────────────────────┐",
        "│  门店/工作场景全景   │",
        "│   ┌──────┬──────┐    │",
        "│   │ 招牌 │ 门面 │    │",
        "│   └──────┴──────┘    │",
        "│  ───匀速横移 4s───▶  │",
        "│░░░░░░░░░░░░░░░░░░░░░░│",
        "└──────────────────────┘",
    ])


def generate_shot_card(output_md: Path, title: str, track: str, shots: List[Dict[str, Any]]) -> None:
    output_md.parent.mkdir(parents=True, exist_ok=True)
    svg_dir = output_md.parent / "shot_sketches"
    svg_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# 手机拍摄任务卡：{title}",
        "",
        f"- **适用轨道**：{'门店轨（实拍人设+门店信任细节）' if track=='store' else '博主轨（高信息密度+节奏卡点）'}",
        "- **手机统一设置**：`1080×1920 竖屏` · `30fps` · 关闭自带美颜/HDR防发灰 · 擦干净主摄镜头",
        "- **核心原则**：**只管念词或拍好画面，不用自己数节拍；每条多拍 1 秒头尾余量，回来全交给流水线以音定画！**",
        "",
    ]

    for s in shots:
        svg_file = svg_dir / f"shot_{s['shot_no']:02d}.svg"
        svg_file.write_text(build_sketch_svg(s.get("diagram_type", "portrait_hook"), s["shot_no"]), encoding="utf-8")
        lines += [
            f"## 镜头 #{s['shot_no']}：{s['role']}",
            "",
            f"- **拍什么**：{s['what']}",
            f"- **对应台词/用途**：{s.get('spoken_line', '')}",
            f"- **机位与景别**：{s['angle_framing']}",
            f"- **建议录制时长**：{s['duration_sec']} 秒（头尾各多停 1 秒）",
            f"- **备份条数**：建议拍 **{s['backup_takes']} 条**（挑最顺的一条）",
            f"- **手机拍摄要点**：{s['phone_tips']}",
            f"- **简笔构图示意**：见 SVG 文件 `{svg_file.name}`",
            "",
            "```text",
            build_ascii_sketch(s.get("diagram_type", "portrait_hook")),
            "```",
            "",
        ]

    output_md.write_text("\n".join(lines), encoding="utf-8")
    write_json(output_md.with_suffix(".json"), {"title": title, "track": track, "shots": shots})


def main() -> None:
    parser = argparse.ArgumentParser(description="生成带简笔构图示意的手机拍摄任务卡")
    parser.add_argument("--output", required=True, help="输出 Markdown 任务卡路径")
    parser.add_argument("--title", default="八年老店老板说句大实话", help="视频选题标题")
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--shots-json", help="可选：自定义镜头列表 JSON 文件")
    args = parser.parse_args()

    shots = DEFAULT_SHOTS
    if args.shots_json:
        shots = json.loads(Path(args.shots_json).read_text(encoding="utf-8"))
    generate_shot_card(Path(args.output), args.title, args.track, shots)
    print(f"[shot_card] 已生成拍摄任务卡与 SVG 简笔示意 -> {args.output}")


if __name__ == "__main__":
    main()
