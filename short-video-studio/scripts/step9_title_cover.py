#!/usr/bin/env python3
"""
[Step 9: 标题、封面文案与高信息密度封面帧生成]
1. 根据所属轨道（门店轨重转化咨询，博主轨重好奇完播）生成 3 组高点击标题、话题标签与封面大字文案。
2. 截取第 1 个镜头首帧画面，在安全区上绘制高对比度封面大字卡片，输出 pipeline/09_cover.jpg。
输出：pipeline/09_title_cover.md 与 pipeline/09_cover.jpg
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    find_bin,
    find_commercial_font,
    maybe_reexec_in_venv,
    read_json,
    run_cmd,
    write_json,
)


def run_step9(pipeline_dir: Path, track: str = "store") -> Dict[str, Any]:
    from PIL import Image, ImageDraw, ImageFont

    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    sentences = audio_events["sentences"]
    hook_text = sentences[0]["text"] if sentences else "揭秘行业真相"
    keywords: List[str] = []
    for s in sentences[:3]:
        keywords.extend(s.get("keywords", []))
    top_kw = keywords[0] if keywords else hook_text[:6]

    hook_clean = hook_text.rstrip("。！？，；")
    if len(hook_clean) > 22:
        parts = [p for p in hook_clean.split("，") if p]
        hook_clean = "，".join(parts[:2]) if len("，".join(parts[:2])) <= 24 else parts[0]

    if track == "store":
        titles = [
            f"开了8年才敢说实话：{hook_clean}",
            f"为什么我宁愿少赚也要坚持【{top_kw}】？同城老客都懂",
            f"别被表面低价坑了！带你看清背后的真实成本",
        ]
        cover_main = f"越贵的菜\n利润越低？" if "利润" in hook_text else f"{hook_clean[:8]}\n真相在这"
        cover_sub = f"8年老板底线 · 实话实说"
        hashtags = "#同城美食 #实体店老板 #良心好店 #餐饮干货 #探店推荐"
        accent = "#FFD54F"
    else:
        titles = [
            f"{hook_clean}（90%的人第一步就错了）",
            f"拆解【{top_kw}】：真正拉开差距的其实是这 1 点",
            f"别再无效努力了！看懂这套底层节奏再动手",
        ]
        cover_main = f"懂得越多\n做得越差？" if "越差" in hook_text else f"{hook_clean[:8]}\n核心真相"
        cover_sub = f"高完播节奏 · 底层拆解"
        hashtags = "#短视频干货 #自媒体运营 #口播技巧 #内容创作 #涨粉秘籍"
        accent = "#4FC3F7"

    # 从首镜抽第 1 帧作为封面底图
    cover_path = pipeline_dir / "09_cover.jpg"
    raw_frame_png = pipeline_dir / "09_cover_raw.png"
    first_clip = edl["clips"][0]
    ffmpeg = find_bin("ffmpeg")
    if ffmpeg and Path(first_clip["media_path"]).exists():
        run_cmd([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(first_clip["source_in_sec"]),
            "-i", first_clip["media_path"],
            "-frames:v", "1", "-s", f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
            str(raw_frame_png),
        ])
        base_img = Image.open(raw_frame_png).convert("RGBA")
        raw_frame_png.unlink(missing_ok=True)
    else:
        base_img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (20, 28, 45, 255))

    overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = find_commercial_font()
    f_main = ImageFont.truetype(font_path, 96) if font_path else ImageFont.load_default()
    f_sub = ImageFont.truetype(font_path, 42) if font_path else ImageFont.load_default()

    # 在安全区上半部分绘制高对比度封面文字底板 (Y: 360 ~ 780)
    draw.rounded_rectangle([80, 360, 860, 760], radius=28, fill=(10, 14, 24, 185), outline=accent, width=6)
    draw.rounded_rectangle([110, 392, 540, 458], radius=12, fill=accent)
    draw.text((325, 425), cover_sub, fill="#0F172A", font=f_sub, anchor="mm")
    draw.multiline_text((112, 495), cover_main, fill="#FFFFFF", font=f_main, spacing=20)

    composed = Image.alpha_composite(base_img, overlay).convert("RGB")
    composed.save(cover_path, quality=92)

    md_content = "\n".join([
        "# 标题与封面交付方案",
        "",
        f"- **轨道策略**：{'门店轨（强信任与评论咨询转化）' if track=='store' else '博主轨（强点击好奇与高完播）'}",
        f"- **封面主大字**：`{cover_main.replace(chr(10), ' / ')}`",
        f"- **封面副标签**：`{cover_sub}`",
        f"- **封面图文件**：`{cover_path.name}`",
        "",
        "## 备选发布标题（3 选 1）",
        *[f"{i}. {t}" for i, t in enumerate(titles, start=1)],
        "",
        "## 推荐话题标签",
        hashtags,
        "",
    ])
    (pipeline_dir / "09_title_cover.md").write_text(md_content, encoding="utf-8")
    out = {
        "step": "09_title_cover",
        "titles": titles,
        "cover_main": cover_main,
        "cover_sub": cover_sub,
        "hashtags": hashtags,
        "cover_jpg": str(cover_path.resolve()),
    }
    write_json(pipeline_dir / "09_title_cover.json", out)
    print(f"[step9] 标题与高密度封面已生成 -> 09_title_cover.md & 09_cover.jpg")
    return out


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 9: 生成发布标题与高密度封面")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    args = parser.parse_args()
    run_step9(Path(args.pipeline_dir), track=args.track)


if __name__ == "__main__":
    main()
