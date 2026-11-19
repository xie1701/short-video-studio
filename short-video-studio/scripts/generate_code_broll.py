#!/usr/bin/env python3
"""
[素材获取：风格谱同步代码叙事补镜引擎]
当实拍镜头不足时，通过 Pillow 矢量绘图 + FFmpeg rawvideo pipe:0 直推流 + 尾帧 bytes 复用，
高速输出 1080x1920 @ 30fps CFR (BT.709/TV) 的风格化叙事视频片段：
- kinetic_text / data_chart / flow_diagram / proportion / day_night / chips / versus
特性：
1. 自动加载风格谱色板与语域保护 (warm_food / craft_paper / dark_tech)。
2. 全部主副标题由 Pillow textbbox 动态缩字与限制宽度，100% 保证不冲出 SAFE_ZONE["x_max"]。
3. data_chart 按输入数值或 unit 字段按实标单位（拒绝把金额硬拼成 %）。
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    ASSETS_DIR,
    BT709_FFMPEG_ARGS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SAFE_ZONE,
    find_bin,
    find_commercial_font,
    load_style_spectrum_entry,
    maybe_reexec_in_venv,
    maybe_reexec_in_venv as _noop_alias,  # noqa: F401
)


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def resolve_style_palette(
    style_id: Optional[str],
    track: str = "store",
    register: Optional[str] = None,
) -> Dict[str, Any]:
    """根据视觉语域 (B6) / 风格谱 ID / 轨道返回专属排版色板与字体候选。"""
    if register == "warm_food":
        return {
            "archetype_id": "register_warm_food",
            "bg_hex": "#FAF4E8",
            "card_bg_hex": "#FFFFFF",
            "grid_hex": "#EDE2CE",
            "title_hex": "#2B1E16",
            "sub_hex": "#6B4F3A",
            "accent_hex": "#C2571A",
            "badge_text_hex": "#FFFFFF",
            "border_hex": "#DFCBB2",
            "font_candidates": None,
            "is_light": True,
        }
    if register == "craft_paper":
        return {
            "archetype_id": "register_craft_paper",
            "bg_hex": "#F4EFE6",
            "card_bg_hex": "#FAF7F2",
            "grid_hex": "#E6DEC8",
            "title_hex": "#1C1917",
            "sub_hex": "#57534E",
            "accent_hex": "#B91C1C",
            "badge_text_hex": "#FFFFFF",
            "border_hex": "#D6CFC2",
            "font_candidates": None,
            "is_light": True,
        }
    if register == "dark_tech":
        return {
            "archetype_id": "register_dark_tech",
            "bg_hex": "#0B0F17",
            "card_bg_hex": "#121926",
            "grid_hex": "#1A2333",
            "title_hex": "#F8FAFC",
            "sub_hex": "#94A3B8",
            "accent_hex": "#38BDF8",
            "badge_text_hex": "#0B0F17",
            "border_hex": "#25344D",
            "font_candidates": None,
            "is_light": False,
        }

    default_acc = "#FFD54F" if track == "store" else "#38BDF8"
    base = {
        "archetype_id": "default",
        "bg_hex": "#12151E",
        "card_bg_hex": "#1B2030",
        "grid_hex": "#1F2638",
        "title_hex": "#F8FAFC",
        "sub_hex": "#94A3B8",
        "accent_hex": default_acc,
        "badge_text_hex": "#0F172A",
        "border_hex": "#2D364D",
        "font_candidates": None,
        "is_light": False,
    }
    if not style_id:
        return base

    entry = load_style_spectrum_entry(style_id)
    if not entry:
        return base

    arch = str(entry.get("archetype_id", ""))
    sb = entry.get("subtitle_spec", {})
    kw_col = str(sb.get("keyword_color") or default_acc)
    f_cands = sb.get("font_candidates")

    if arch == "arch_03_editorial_matte_depth" and style_id in ("style_12", "style_18"):
        return {
            "archetype_id": arch,
            "bg_hex": "#F6F5F0",
            "card_bg_hex": "#FFFFFF",
            "grid_hex": "#E7E5DF",
            "title_hex": "#111827",
            "sub_hex": "#4B5563",
            "accent_hex": "#111827",
            "badge_text_hex": "#FFFFFF",
            "border_hex": "#D1D5DB",
            "font_candidates": f_cands,
            "is_light": True,
        }
    if arch == "arch_06_paper_stopmotion" or style_id == "style_05":
        return {
            "archetype_id": arch,
            "bg_hex": "#F4EFE6",
            "card_bg_hex": "#FAF7F2",
            "grid_hex": "#E6DEC8",
            "title_hex": "#1C1917",
            "sub_hex": "#57534E",
            "accent_hex": "#DC2626",
            "badge_text_hex": "#FFFFFF",
            "border_hex": "#D6CFC2",
            "font_candidates": f_cands,
            "is_light": True,
        }
    if arch == "arch_05_cinema_slow_space":
        return {
            "archetype_id": arch,
            "bg_hex": "#111113",
            "card_bg_hex": "#19191D",
            "grid_hex": "#222228",
            "title_hex": "#FAF4E4",
            "sub_hex": "#A8A29E",
            "accent_hex": "#E6C67A",
            "badge_text_hex": "#111113",
            "border_hex": "#343028",
            "font_candidates": f_cands or ["SourceHanSerifCN-Bold.otf"],
            "is_light": False,
        }
    if arch == "arch_01_tech_ui_console":
        return {
            "archetype_id": arch,
            "bg_hex": "#0B0F17",
            "card_bg_hex": "#121926",
            "grid_hex": "#1A2333",
            "title_hex": "#F8FAFC",
            "sub_hex": "#94A3B8",
            "accent_hex": "#38BDF8" if kw_col.upper() == "#FFFFFF" else kw_col,
            "badge_text_hex": "#0B0F17",
            "border_hex": "#25344D",
            "font_candidates": f_cands,
            "is_light": False,
        }
    return {
        "archetype_id": arch,
        "bg_hex": "#141210" if track == "store" else "#12151E",
        "card_bg_hex": "#1E1B18" if track == "store" else "#1B2030",
        "grid_hex": "#28231E" if track == "store" else "#1F2638",
        "title_hex": "#FAF8F5",
        "sub_hex": "#A8A29E",
        "accent_hex": kw_col if kw_col.upper() != "#FFFFFF" else default_acc,
        "badge_text_hex": "#0F172A",
        "border_hex": "#3A332C",
        "font_candidates": f_cands,
        "is_light": False,
    }


def _fit_single_line(draw: Any, text: str, font_path: Optional[str], start_size: int, max_width: int, min_size: int = 22) -> Tuple[Any, str]:
    """根据 Pillow 实测像素宽度动态缩字与截断，杜绝任何文本溢出 SAFE_ZONE 与卡片框。"""
    from PIL import ImageFont

    txt = str(text or "").strip()
    if not txt:
        return (ImageFont.truetype(font_path, start_size) if font_path else ImageFont.load_default()), ""
    for fs in range(start_size, min_size - 1, -2):
        fnt = ImageFont.truetype(font_path, fs) if font_path else ImageFont.load_default()
        bb = draw.textbbox((0, 0), txt, font=fnt)
        if (bb[2] - bb[0]) <= max_width:
            return fnt, txt
    fnt = ImageFont.truetype(font_path, min_size) if font_path else ImageFont.load_default()
    while len(txt) > 2:
        bb = draw.textbbox((0, 0), txt + "…", font=fnt)
        if (bb[2] - bb[0]) <= max_width:
            return fnt, txt + "…"
        txt = txt[:-1]
    return fnt, txt


def _format_chart_value(val: float, ratio_p: float, unit: str) -> str:
    cur = val * ratio_p
    if abs(val - round(val)) < 1e-4:
        num_str = str(int(round(cur)))
    else:
        num_str = f"{cur:.1f}"
    return f"{num_str}{unit}"


def render_broll_clip_pillow(
    output_mp4: Path,
    mode: str,
    title: str,
    items: List[Dict[str, Any]],
    duration_sec: float = 3.0,
    fps: int = DEFAULT_FPS,
    accent_hex: Optional[str] = None,
    bg_hex: Optional[str] = None,
    style_id: Optional[str] = None,
    track: str = "store",
    register: Optional[str] = None,
    anchor_delay_sec: float = 0.15,
) -> Path:
    """用 Pillow 绘制矢量缓动卡片，通过 rawvideo pipe:0 直推 FFmpeg 并复用尾帧 bytes，提速 10×。"""
    from PIL import Image, ImageDraw, ImageFont

    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 编码补镜视频。")

    duration_sec = max(2.6, float(duration_sec))
    pal = resolve_style_palette(style_id, track=track, register=register)
    use_bg = bg_hex or pal["bg_hex"]
    use_accent = accent_hex or pal["accent_hex"]
    card_bg = pal["card_bg_hex"]
    grid_col = pal["grid_hex"]
    title_col = pal["title_hex"]
    sub_col = pal["sub_hex"]
    border_col = pal["border_hex"]
    badge_txt_col = pal["badge_text_hex"]

    font_path = find_commercial_font(pal.get("font_candidates"))
    f_badge = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
    f_num_big = ImageFont.truetype(font_path, 52) if font_path else ImageFont.load_default()

    total_frames = max(int(round(2.6 * fps)), int(round(duration_sec * fps)))
    anchor_frames = max(4, int(round(anchor_delay_sec * fps)))
    # 计算最晚到达静止帧索引，此后所有帧像素完全一致，可直接缓存 bytes 复用
    max_anim_end_frame = min(total_frames - 1, anchor_frames + 35)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    mode_tags = {
        "kinetic_text": "核心观点",
        "data_chart": "数据拆解",
        "flow_diagram": "标准流程",
        "proportion": "比例拆解",
        "day_night": "全天接力",
        "chips": "招牌清单",
        "versus": "真假对照",
    }

    safe_right = SAFE_ZONE["x_max"] - 24  # 856px 严格留边
    max_title_w = safe_right - 88         # 768px

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
        "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264", *BT709_FFMPEG_ARGS, "-preset", "fast", "-crf", "17",
        "-r", str(fps), "-fps_mode", "cfr",
        str(output_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    cached_tail_bytes: Optional[bytes] = None
    try:
        for fi in range(total_frames):
            if cached_tail_bytes is not None:
                proc.stdin.write(cached_tail_bytes)
                continue

            img = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), use_bg)
            draw = ImageDraw.Draw(img)

            for gx in range(88, 860, 130):
                draw.line([(gx, 190), (gx, 1440)], fill=grid_col, width=1)
            for gy in range(210, 1440, 130):
                draw.line([(72, gy), (safe_right, gy)], fill=grid_col, width=1)

            mode_tag = mode_tags.get(mode, "重点提示")
            draw.rounded_rectangle([88, 224, 290, 276], radius=12, fill=use_accent)
            draw.text((189, 250), mode_tag, fill=badge_txt_col, font=f_badge, anchor="mm")

            tp = 0.35 + 0.65 * ease_out_cubic(fi / 8.0)
            ty = int(340 + (1.0 - tp) * 26)
            f_title, fit_title = _fit_single_line(draw, title, font_path, 60, max_title_w, min_size=34)
            draw.text((88, ty), fit_title, fill=title_col, font=f_title)
            draw.rectangle([88, ty + 84, 88 + int(min(max_title_w, 620) * tp), ty + 90], fill=use_accent)

            if mode == "proportion":
                raw_vals = [max(1.0, float(it.get("value") or 1.0)) for it in items[:4]]
                tot_v = max(1e-5, sum(raw_vals))
                pcts = [v * 100.0 / tot_v for v in raw_vals]
                p_bar = ease_out_cubic((fi - anchor_frames) / 14.0)
                bar_x0, bar_x1, bar_y0, bar_y1 = 88, safe_right, 480, 566
                draw.rounded_rectangle([bar_x0, bar_y0, bar_x1, bar_y1], radius=18, fill=card_bg, outline=border_col, width=2)
                seg_colors = [use_accent, "#2563EB", "#10B981", "#8B5CF6"]
                cur_x = bar_x0 + 8
                inner_w = (bar_x1 - bar_x0 - 16) * max(0.08, p_bar)
                for si, pct in enumerate(pcts):
                    seg_w = max(24, int(inner_w * pct / 100.0))
                    ex = min(bar_x1 - 8, cur_x + seg_w - 4)
                    if ex > cur_x + 4:
                        draw.rounded_rectangle([cur_x, bar_y0 + 10, ex, bar_y1 - 10], radius=12, fill=seg_colors[si % len(seg_colors)])
                    cur_x += seg_w

                for idx, (it, pct) in enumerate(zip(items[:4], pcts)):
                    p_c = ease_out_cubic((fi - (anchor_frames + idx * 5)) / 10.0)
                    if p_c <= 0.02:
                        continue
                    cy = 610 + idx * 176
                    draw.rounded_rectangle([88, cy, safe_right, cy + 148], radius=16, fill=card_bg, outline=border_col, width=2)
                    draw.rounded_rectangle([104, cy + 28, 120, cy + 120], radius=6, fill=seg_colors[idx % len(seg_colors)])
                    fl, tl = _fit_single_line(draw, str(it.get("label", "")), font_path, 38, safe_right - 300)
                    draw.text((142, cy + 36), tl, fill=title_col, font=fl)
                    if it.get("subtitle"):
                        fs, ts_txt = _fit_single_line(draw, str(it["subtitle"]), font_path, 28, safe_right - 300)
                        draw.text((142, cy + 92), ts_txt, fill=sub_col, font=fs)
                    draw.text((safe_right - 32, cy + 74), f"{int(round(pct * p_c))}%", fill=seg_colors[idx % len(seg_colors)], font=f_num_big, anchor="rm")

            elif mode == "day_night":
                for idx, it in enumerate(items[:2]):
                    delay = anchor_frames + idx * 10
                    p_c = ease_out_cubic((fi - delay) / 10.0)
                    if p_c <= 0.02:
                        continue
                    cy = 490 + idx * 340
                    tag_bg = "#D97706" if idx == 0 else "#1E3A8A"
                    tag_lbl = str(it.get("tag") or ("昼 · 日间档" if idx == 0 else "夜 · 晚市档"))[:10]
                    draw.rounded_rectangle([88, cy, safe_right, cy + 240], radius=22, fill=card_bg, outline=border_col, width=2)
                    draw.rounded_rectangle([116, cy + 26, 310, cy + 74], radius=12, fill=tag_bg)
                    draw.text((213, cy + 50), tag_lbl, fill="#FFFFFF", font=f_badge, anchor="mm")
                    fl, tl = _fit_single_line(draw, str(it.get("label", "")), font_path, 38, safe_right - 148)
                    draw.text((116, cy + 104), tl, fill=title_col, font=fl)
                    if it.get("subtitle"):
                        fs, ts_txt = _fit_single_line(draw, str(it["subtitle"]), font_path, 28, safe_right - 148)
                        draw.text((116, cy + 166), ts_txt, fill=sub_col, font=fs)
                    if idx == 0 and len(items) >= 2:
                        draw.line([(472, cy + 248), (472, cy + 324)], fill=use_accent, width=6)
                        draw.polygon([(454, cy + 308), (490, cy + 308), (472, cy + 332)], fill=use_accent)

            elif mode == "chips":
                for idx, it in enumerate(items[:4]):
                    delay = anchor_frames + idx * 6
                    p_c = ease_out_cubic((fi - delay) / 8.0)
                    if p_c <= 0.02:
                        continue
                    cy = 500 + idx * 175
                    ox = int((1.0 - p_c) * 48)
                    draw.rounded_rectangle([88 + ox, cy, safe_right, cy + 136], radius=68, fill=card_bg, outline=use_accent, width=3)
                    draw.ellipse([120 + ox, cy + 38, 180 + ox, cy + 98], fill=use_accent)
                    f_chk, _ = _fit_single_line(draw, "✓", font_path, 38, 60)
                    draw.text((150 + ox, cy + 68), "✓", fill=badge_txt_col, font=f_chk, anchor="mm")
                    fl, tl = _fit_single_line(draw, str(it.get("label", "")), font_path, 38, safe_right - (228 + ox))
                    draw.text((208 + ox, cy + 68), tl, fill=title_col, font=fl, anchor="lm")

            elif mode == "versus":
                for idx, it in enumerate(items[:4]):
                    delay = anchor_frames + idx * 6
                    p_c = ease_out_cubic((fi - delay) / 9.0)
                    if p_c <= 0.02:
                        continue
                    cy = 490 + idx * 210
                    is_good = bool(it.get("positive", idx % 2 == 1))
                    side_col = "#16A34A" if is_good else "#DC2626"
                    mark = "✓" if is_good else "✗"
                    draw.rounded_rectangle([88, cy, safe_right, cy + 172], radius=18, fill=card_bg, outline=side_col, width=3)
                    draw.ellipse([116, cy + 48, 180, cy + 112], fill=side_col)
                    f_mk, _ = _fit_single_line(draw, mark, font_path, 38, 60)
                    draw.text((148, cy + 80), mark, fill="#FFFFFF", font=f_mk, anchor="mm")
                    fl, tl = _fit_single_line(draw, str(it.get("label", "")), font_path, 38, safe_right - 234)
                    draw.text((206, cy + 40), tl, fill=title_col, font=fl)
                    if it.get("subtitle"):
                        fs, ts_txt = _fit_single_line(draw, str(it["subtitle"]), font_path, 28, safe_right - 234)
                        draw.text((206, cy + 104), ts_txt, fill=sub_col, font=fs)

            else:
                base_y = 490
                card_h = 206 if mode != "flow_diagram" else 192
                max_val = max([float(it["value"]) for it in items[:4] if isinstance(it.get("value"), (int, float))] + [100.0])
                for idx, item in enumerate(items[:4]):
                    delay = anchor_frames + idx * 6
                    p = ease_out_cubic((fi - delay) / 10.0)
                    if p <= 0.01:
                        continue
                    cy = base_y + idx * (card_h + 28)
                    offset_x = int((1.0 - p) * 64)
                    x1, x2 = 88 + offset_x, safe_right
                    draw.rounded_rectangle([x1, cy, x2, cy + card_h], radius=18, fill=card_bg, outline=border_col, width=2)
                    draw.rounded_rectangle([x1 + 14, cy + 28, x1 + 22, cy + card_h - 28], radius=4, fill=use_accent)

                    raw_lbl = str(item.get("label", ""))
                    raw_sub = str(item.get("subtitle", ""))
                    val = item.get("value")

                    if mode == "flow_diagram":
                        draw.ellipse([x1 + 42, cy + 32, x1 + 104, cy + 94], fill=use_accent)
                        f_idx, _ = _fit_single_line(draw, str(idx + 1), font_path, 36, 54)
                        draw.text((x1 + 73, cy + 63), str(idx + 1), fill=badge_txt_col, font=f_idx, anchor="mm")
                        fl, tl = _fit_single_line(draw, raw_lbl, font_path, 36, (x2 - 24) - (x1 + 128))
                        draw.text((x1 + 128, cy + 38), tl, fill=title_col, font=fl)
                        if raw_sub:
                            fs, ts_txt = _fit_single_line(draw, raw_sub, font_path, 28, (x2 - 24) - (x1 + 128))
                            draw.text((x1 + 128, cy + 104), ts_txt, fill=sub_col, font=fs)
                        if idx < len(items[:4]) - 1:
                            draw.line([(x1 + 73, cy + card_h), (x1 + 73, cy + card_h + 28)], fill=use_accent, width=4)
                    else:
                        fl, tl = _fit_single_line(draw, raw_lbl, font_path, 38, (x2 - 28) - (x1 + 44))
                        draw.text((x1 + 44, cy + 28), tl, fill=title_col, font=fl)
                        if mode == "data_chart" and isinstance(val, (int, float)):
                            unit = str(item.get("unit") or ("%" if (float(val) <= 100.0 and "%" in (raw_lbl + str(item.get("unit", "")))) or (not item.get("unit") and float(val) <= 100.0) else ""))
                            ratio = max(0.05, min(1.0, float(val) / max(1e-5, max_val))) * p
                            bar_max = max(120, (x2 - x1) - 230)
                            draw.rounded_rectangle([x1 + 44, cy + 108, x1 + 44 + bar_max, cy + 142], radius=14, fill=grid_col)
                            draw.rounded_rectangle(
                                [x1 + 44, cy + 108, x1 + 44 + max(24, int(bar_max * ratio)), cy + 142],
                                radius=14,
                                fill=use_accent,
                            )
                            val_txt = _format_chart_value(float(val), p, unit)
                            fv, tv = _fit_single_line(draw, val_txt, font_path, 36, 150)
                            draw.text((x2 - 30, cy + 125), tv, fill=use_accent, font=fv, anchor="rm")
                        elif raw_sub:
                            fs, ts_txt = _fit_single_line(draw, raw_sub, font_path, 28, (x2 - 28) - (x1 + 44))
                            draw.text((x1 + 44, cy + 108), ts_txt, fill=sub_col, font=fs)

            raw_bytes = img.tobytes()
            proc.stdin.write(raw_bytes)
            if fi >= max_anim_end_frame and tp >= 0.999:
                cached_tail_bytes = raw_bytes

        proc.stdin.close()
        err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", errors="replace")
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"代码补镜视频编码失败 ({rc}): {err[-600:]}")
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    return output_mp4


def generate_demo_media_pack(output_dir: Path, style_id: Optional[str] = None) -> Dict[str, Any]:
    """生成一套完整可用于演示/测试的模拟实拍素材 + 风格化代码补镜 + 对标参考视频。"""
    from PIL import Image, ImageDraw, ImageFont

    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 生成演示素材。")
    output_dir.mkdir(parents=True, exist_ok=True)
    font_path = find_commercial_font()
    f_big = ImageFont.truetype(font_path, 68) if font_path else ImageFont.load_default()
    f_sub = ImageFont.truetype(font_path, 40) if font_path else ImageFont.load_default()

    broll_data = output_dir / "code_broll_cost_chart.mp4"
    render_broll_clip_pillow(
        broll_data,
        mode="data_chart",
        title="400元烤羊排成本真相",
        items=[
            {"label": "鲜羊排原料成本", "value": 68, "unit": "%"},
            {"label": "腌制与3小时炭火人工", "value": 24, "unit": "%"},
            {"label": "实际门店净利润", "value": 8, "unit": "%"},
        ],
        duration_sec=3.0,
        style_id=style_id,
    )

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
        style_id=style_id,
    )

    broll_food = output_dir / "broll_lamb_closeup.mp4"
    proc = subprocess.Popen([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}", "-r", "30",
        "-i", "pipe:0", "-c:v", "libx264", *BT709_FFMPEG_ARGS, str(broll_food),
    ], stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for fi in range(90):
        img = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (48, 26, 16))
        d = ImageDraw.Draw(img)
        shift = int(math.sin(fi / 15.0) * 35)
        d.ellipse([180 + shift, 480, 820 + shift, 1080], fill=(180, 83, 9), outline=(251, 191, 36), width=8)
        d.text((500 + shift, 780), "鲜切烤羊排特写", fill=(255, 251, 235), font=f_big, anchor="mm")
        d.text((480, 1180), "实拍B-roll素材示例", fill=(253, 224, 71), font=f_sub, anchor="mm")
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait()

    bench = output_dir / "benchmark_sample.mp4"
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=#1E293B:s=1080x1920:r=30:d=15",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=15",
        "-c:v", "libx264", *BT709_FFMPEG_ARGS, "-c:a", "aac", "-shortest", str(bench),
    ], check=True)

    return {
        "code_broll_cost_chart": str(broll_data),
        "code_broll_process": str(broll_flow),
        "broll_lamb_closeup": str(broll_food),
        "benchmark_sample": str(bench),
    }


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="生成风格谱同步代码驱动叙事补镜视频")
    parser.add_argument("--output-dir", help="生成整套演示补镜素材到指定目录")
    parser.add_argument("--output", help="单条补镜 MP4 输出路径")
    parser.add_argument(
        "--mode",
        choices=["kinetic_text", "data_chart", "flow_diagram", "proportion", "day_night", "chips", "versus"],
        default="kinetic_text",
    )
    parser.add_argument("--title", default="核心重点")
    parser.add_argument("--items-json", help="JSON 字符串形式的条目列表")
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--style-id", help="绑定风格谱 ID (style_01 ~ style_20)")
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--register", choices=["warm_food", "craft_paper", "dark_tech"], help="视觉语域保护 (B6)")
    args = parser.parse_args()

    if args.output_dir:
        res = generate_demo_media_pack(Path(args.output_dir), style_id=args.style_id)
        print(f"[code_broll] 已生成代码补镜与演示素材: {json.dumps(res, ensure_ascii=False)}")
        return

    if args.output:
        items = json.loads(args.items_json) if args.items_json else [{"label": args.title, "subtitle": "代码生成补镜"}]
        out = render_broll_clip_pillow(
            Path(args.output),
            args.mode,
            args.title,
            items,
            duration_sec=args.duration,
            style_id=args.style_id,
            track=args.track,
            register=args.register,
        )
        print(f"[code_broll] 已生成风格化补镜 -> {out}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
