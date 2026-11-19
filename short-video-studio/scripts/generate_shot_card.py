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
        "angle_framing": "手机立在眼睛齐高位置，离人一个手臂远，头顶留一拃空隙避开顶部遮挡",
        "duration_sec": 4.5,
        "backup_takes": 3,
        "diagram_type": "portrait_hook",
        "phone_tips": "竖屏 1080p/30fps（关 HDR 防发灰）；嘴离麦不超过两个巴掌；脸朝窗户自然光，说错别停直接重说那一句",
        "spoken_line": "钩子第一句（先声夺人 + 高密度画面）",
    },
    {
        "shot_no": 2,
        "role": "B-roll 核心细节特写 (需收现场同期声)",
        "what": "近距离拍产品细节/制作动作/检测瞬间，同时把锅响、切菜、工具动作声录进去",
        "angle_framing": "手机抬高约一拃 45° 俯拍，镜头离主体两掌宽（点击屏幕锁死对焦与曝光）",
        "duration_sec": 5.0,
        "backup_takes": 2,
        "diagram_type": "closeup_detail",
        "phone_tips": "手肘贴桌稳住不要晃，慢推 2 秒即可；多拍 3 秒余量别刚放下就按停止",
        "spoken_line": "覆盖在第一段关键词重音处，仅换画面不改原声",
    },
    {
        "shot_no": 3,
        "role": "B-roll 门店/工作场景背书",
        "what": "门头/工作台/顾客实景/操作区大景到中景，证明真实靠谱",
        "angle_framing": "往后退三步拍全貌，主摄 1x 不要开广角防变形，人物/招牌居中",
        "duration_sec": 6.0,
        "backup_takes": 2,
        "diagram_type": "wide_scene",
        "phone_tips": "从左往右匀速平移 4 秒，起幅先站稳停 1 秒再动，落幅也多停 1 秒",
        "spoken_line": "配合人设表达（初心/原则/底线）与价值说明段",
    },
    {
        "shot_no": 4,
        "role": "A-roll 结尾人设与转化指令",
        "what": "回到真人直视镜头，给出坚定承诺或互动指令（评论/私信/到店）",
        "angle_framing": "身体略微侧转一点，离手机一个手臂远平视，眼神盯镜头小圆孔不看屏幕字幕",
        "duration_sec": 5.0,
        "backup_takes": 2,
        "diagram_type": "portrait_hook",
        "phone_tips": "说完最后一字微笑停满 2 秒再抬手关录制，防止句尾被手挡脸",
        "spoken_line": "结尾金句 + 互动/转化指令",
    },
]

INDUSTRY_CHECKLISTS = {
    "general": [
        "准备 1–2 个能直接证明专业度的真实实物/单据道具，开拍放手边（别空手干讲）。",
    ],
    "catering": [
        "【餐饮必拍】早上开档鲜货上秤/拆箱过程 + 热锅下料滋啦同期声 + 明厨洁净操作台。",
        "【餐饮避坑】拍生肉血水、垃圾桶、杂乱后厨角落时严禁作为夸赞“鲜美/卫生”的垫画面。",
    ],
    "auto_repair": [
        "【汽修必拍】新旧配件放一块对比特写 + 配件防伪标/原厂码对光拍清 + 拧扭矩扳手清脆咔哒同期声。",
        "【汽修避坑】实拍车牌与客户微信界面注意错位遮挡隐私，切忌黑脏废油糊满镜头的无效抖拍。",
    ],
}


def build_sketch_svg(diagram_type: str, shot_no: int) -> str:
    """生成清晰直观的 9:16 竖屏简笔构图示意 SVG（标出平台安全框与主体位置）。"""
    base_header = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="320" viewBox="0 0 180 320">'
        '<defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#0284C7"/></marker></defs>'
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


def shots_from_audio_events(audio_events: Dict[str, Any]) -> List[Dict[str, Any]]:
    """画外音型片子：先锁配音，再按每句配音时长 +2.5s 反写每条实拍镜头目标时长。"""
    out: List[Dict[str, Any]] = []
    for idx, sent in enumerate(audio_events.get("sentences", []), start=1):
        s_dur = max(1.0, float(sent["end_sec"]) - float(sent["start_sec"]))
        rec_dur = round(s_dur + 2.5, 1)
        out.append({
            "shot_no": idx,
            "role": f"B-roll 画面镜头 #{idx} (按配音时长+2.5s定长)",
            "what": f"拍摄对应画面：与本句「{sent['text'][:16]}」提到的核心物品或操作动作强一致",
            "angle_framing": "主体居画面中间偏上；特写距离两掌宽，全景退后三步",
            "duration_sec": rec_dur,
            "backup_takes": 2,
            "diagram_type": "closeup_detail" if idx % 2 == 1 else "wide_scene",
            "phone_tips": f"本句配音长 {s_dur:.1f}s，请实拍不少于 {rec_dur:.1f}s（前 0.5s 和结尾 1s 拿稳别动）",
            "spoken_line": sent["text"],
        })
    return out


def generate_shot_card(
    output_md: Path,
    title: str,
    track: str,
    shots: List[Dict[str, Any]],
    industry: str = "general",
    voice_locked: bool = False,
) -> None:
    output_md.parent.mkdir(parents=True, exist_ok=True)
    svg_dir = output_md.parent / "shot_sketches"
    svg_dir.mkdir(parents=True, exist_ok=True)

    ind_tips = INDUSTRY_CHECKLISTS.get(industry, INDUSTRY_CHECKLISTS["general"])
    lines = [
        f"# 手机拍摄任务卡：{title}",
        "",
        f"- **适用轨道**：{'门店轨（实拍人设+门店信任细节）' if track=='store' else '博主轨（高信息密度+节奏卡点）'} | **行业模板**：`{industry}`",
        "- **手机统一设置**：`1080×1920 竖屏` · `30fps` · 关自带美颜与 HDR 防发灰 · **拍前先拿袖口擦干净主摄镜头**",
        "- **核心原则**：**用「一个手臂 / 一拃 / 退三步」做机位尺子；只管拍稳拍够时长，全片剪辑节奏交给流水线以音定画！**",
        "",
        "## 开拍 & 收工 5 条现场铁律（决定能不能直接用）",
        "1. **必录 2 个现场动作同期声**：热锅下菜滋啦、刀切声、工具清脆声等至少录进去，别全拍哑巴空镜。",
        "2. **单独录 10 秒门店环境底噪**：开拍前全员闭嘴停顿 10 秒，专门采一段安静 Room Tone，方便后期句间无痕填缝。",
        "3. **外接麦先拍一下手**：若手机外接无线麦或独立录音，每条开拍前对着镜头**拍一下手**做音画对齐基准点。",
        "4. **口播说错千万别按停**：直接停半秒把错的那一句重念一遍（流水线会自动对比多条 Take 择优淘汰 NG）。",
        "5. **拍完当场花 20 秒回看 3 条**：① 人声清不清、有没有喷麦破音 ② 主体焦点虚不虚 ③ 头顶留出一拃、底部躲开手机黑遮挡区。",
        "",
    ]
    if voice_locked:
        lines.extend([
            "## 画外音配音已锁定（按音反定实拍时长）",
            "- 下表每个镜头的建议录制时长已按**对应配音句长度 + 2.5 秒剪辑余量**自动算出，照着拍即可严丝合缝！",
            "",
        ])

    lines.append("## 行业专属备料与避坑提醒")
    for t in ind_tips:
        lines.append(f"- {t}")
    lines.append("")

    for s in shots:
        svg_file = svg_dir / f"shot_{s['shot_no']:02d}.svg"
        svg_file.write_text(build_sketch_svg(s.get("diagram_type", "portrait_hook"), s["shot_no"]), encoding="utf-8")
        lines += [
            f"## 镜头 #{s['shot_no']}：{s['role']}",
            "",
            f"- **拍什么**：{s['what']}",
            f"- **对应台词/用途**：{s.get('spoken_line', '')}",
            f"- **身体尺子机位**：{s['angle_framing']}",
            f"- **建议录制时长**：{s['duration_sec']} 秒（包含头尾各 1 秒以上起落幅余量）",
            f"- **备份条数**：建议拍 **{s['backup_takes']} 条**（说错不断连拍，挑最顺的一条）",
            f"- **手机拍摄要点**：{s['phone_tips']}",
            f"- **简笔构图示意**：见 SVG 文件 `{svg_file.name}`",
            "",
            "```text",
            build_ascii_sketch(s.get("diagram_type", "portrait_hook")),
            "```",
            "",
        ]

    output_md.write_text("\n".join(lines), encoding="utf-8")
    write_json(output_md.with_suffix(".json"), {"title": title, "track": track, "industry": industry, "shots": shots})


def main() -> None:
    parser = argparse.ArgumentParser(description="生成小白身体尺子拍摄任务卡（支持配音反定时长与行业提示）")
    parser.add_argument("--output", required=True, help="输出 Markdown 任务卡路径")
    parser.add_argument("--title", default="八年老店老板说句大实话", help="视频选题标题")
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--industry", choices=["general", "catering", "auto_repair"], default="general")
    parser.add_argument("--audio-events", help="可选：已锁定的 03_audio_events.json，按每句配音+2.5s反写实拍时长")
    parser.add_argument("--shots-json", help="可选：自定义镜头列表 JSON 文件")
    args = parser.parse_args()

    voice_locked = False
    if args.shots_json:
        shots = json.loads(Path(args.shots_json).read_text(encoding="utf-8"))
    elif args.audio_events and Path(args.audio_events).is_file():
        ae = json.loads(Path(args.audio_events).read_text(encoding="utf-8"))
        shots = shots_from_audio_events(ae) or DEFAULT_SHOTS
        voice_locked = True
    else:
        shots = DEFAULT_SHOTS
    generate_shot_card(Path(args.output), args.title, args.track, shots, industry=args.industry, voice_locked=voice_locked)
    print(f"[shot_card] 已生成拍摄任务卡与 SVG 简笔示意 -> {args.output}")


if __name__ == "__main__":
    main()
