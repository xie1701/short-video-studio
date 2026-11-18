#!/usr/bin/env python3
"""
[出片 1：本地渲染 MP4（低清粗剪预览 + 高清成片）]
严格按 04_edl.json、05_subtitles.json、06_packaging.json、07_color_plan.json 与 08_mixed_master.wav 合成：
1. 按 EDL 时间表裁剪每个镜头并应用基础校色+LUT（缺长自动末帧定格 tpad）。
2. 将字幕(关键词高亮+可商用字体)、花字、贴纸渲染为小体积透明 PNG 贴片，
   用 FFmpeg overlay + enable='between(n, start_frame, end_frame)' 精准到帧叠加。
3. 混入 -14 LUFS 母带音频 08_mixed_master.wav，精确锁定视频帧数与音频时长，确保音视频时长差 ≤ 1 帧。
支持：
- --preview : 540x960 快速粗剪预览 (output_preview.mp4)
- 默认      : 1080x1920 高清成片 (output_final.mp4)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    find_bin,
    find_commercial_font,
    maybe_reexec_in_venv,
    read_json,
    run_cmd,
)
from step2_transcribe import get_wav_duration


def parse_hex_rgba(hex_str: str, default_alpha: int = 255) -> Tuple[int, int, int, int]:
    h = hex_str.strip().lstrip("#")
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), default_alpha
    if len(h) == 8:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
    return 255, 255, 255, default_alpha


_TOFU_CACHE: Dict[Tuple[str, str], bool] = {}


def _get_fallback_font_path() -> str | None:
    for p in ("/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc"):
        if Path(p).exists():
            return p
    return None


def _char_is_tofu(font: Any, font_key: str, ch: str) -> bool:
    if ord(ch) <= 32:
        return False
    cache_key = (font_key, ch)
    if cache_key in _TOFU_CACHE:
        return _TOFU_CACHE[cache_key]
    from PIL import Image, ImageDraw

    def _render(c: str):
        im = Image.new("L", (64, 64), 0)
        d = ImageDraw.Draw(im)
        d.text((8, 8), c, font=font, fill=255)
        return im.tobytes(), im.getbbox()

    b_ch, bbox = _render(ch)
    if bbox is None:
        _TOFU_CACHE[cache_key] = True
        return True
    b_missing, _ = _render("\ufffe")
    res = b_ch == b_missing
    _TOFU_CACHE[cache_key] = res
    return res


def compute_subtitle_place_xy(img_w: int, img_h: int, tpl: Dict[str, Any]) -> Tuple[int, int]:
    """计算字幕透明 PNG 在 1080x1920 安全区内的放置坐标 (place_x, place_y)。"""
    center_x = int(tpl.get("center_x") if tpl.get("center_x") is not None else 470)
    safe_y = int(tpl.get("safe_y_center") if tpl.get("safe_y_center") is not None else 1340)
    place_x = max(80, min(860 - img_w, center_x - img_w // 2))
    place_y = max(1160, min(1476 - img_h, safe_y - img_h // 2))
    return place_x, place_y


def render_subtitle_png(entry: Dict[str, Any], tpl: Dict[str, Any], font_path: str | None, out_png: Path) -> Tuple[int, int]:
    """将单条字幕（含逐字关键词变色高亮、缺字自动回退与超宽字号自适应）渲染为透明 PNG，返回放置坐标 (x, y)。"""
    from PIL import Image, ImageDraw, ImageFont

    font_size = int(tpl.get("font_size", 54))
    kw_size = int(round(font_size * float(tpl.get("keyword_scale", 1.15))))
    fb_path = _get_fallback_font_path()
    words = entry.get("words") or [{"word": ch, "is_keyword": False} for ch in entry["text"]]
    pad_x, pad_y = 26, 16
    stroke_w = int(tpl.get("stroke_width", 4))
    max_content_w = 760 - pad_x * 2

    dummy = Image.new("RGBA", (10, 10))
    d_draw = ImageDraw.Draw(dummy)

    def _measure_with_sizes(n_sz: int, k_sz: int, s_w: int):
        f_n = ImageFont.truetype(font_path, n_sz) if font_path else ImageFont.load_default()
        f_k = ImageFont.truetype(font_path, k_sz) if font_path else ImageFont.load_default()
        fb_n = ImageFont.truetype(fb_path, n_sz) if fb_path else None
        fb_k = ImageFont.truetype(fb_path, k_sz) if fb_path else None

        boxes: List[Tuple[str, bool, Any, int, int]] = []
        tot_w = 0
        m_h = k_sz + 10
        font_tag = str(font_path or "default")
        for w in words:
            ch = w["word"]
            is_kw = bool(w.get("is_keyword", False))
            primary_fnt = f_k if is_kw else f_n
            fallback_fnt = fb_k if is_kw else fb_n
            if font_path and fallback_fnt is not None and _char_is_tofu(primary_fnt, font_tag, ch):
                fnt = fallback_fnt
            else:
                fnt = primary_fnt
            bb = d_draw.textbbox((0, 0), ch, font=fnt, stroke_width=s_w)
            cw = max(14, (bb[2] - bb[0]) + 4)
            ch_h = (bb[3] - bb[1]) + 8
            boxes.append((ch, is_kw, fnt, cw, ch_h))
            tot_w += cw
            m_h = max(m_h, ch_h)
        return boxes, tot_w, m_h

    char_boxes, total_w, max_h = _measure_with_sizes(font_size, kw_size, stroke_w)
    min_fnt = min(28, max(24, int(tpl.get("font_size", 54))))
    if total_w > max_content_w:
        scale = (max_content_w / float(total_w)) * 0.97
        font_size = max(min_fnt, int(font_size * scale))
        kw_size = max(min_fnt + 4, int(kw_size * scale))
        stroke_w = max(2, int(round(stroke_w * scale)))
        char_boxes, total_w, max_h = _measure_with_sizes(font_size, kw_size, stroke_w)
        while total_w > max_content_w and font_size > min_fnt:
            font_size -= 2
            kw_size = max(min_fnt + 2, kw_size - 2)
            char_boxes, total_w, max_h = _measure_with_sizes(font_size, kw_size, stroke_w)

    img_w = min(760, max(total_w + pad_x * 2, 120))
    img_h = max_h + pad_y * 2 + 8
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg_rgba = parse_hex_rgba(tpl.get("bg_box_color", "#00000099"), 165)
    if bg_rgba[3] > 0:
        draw.rounded_rectangle([0, 0, img_w - 1, img_h - 1], radius=int(tpl.get("bg_radius", 16)), fill=bg_rgba)

    c_primary = parse_hex_rgba(tpl.get("primary_color", "#FFFFFF"))
    c_kw = parse_hex_rgba(tpl.get("keyword_color", "#FFD54F"))
    c_stroke = parse_hex_rgba(tpl.get("stroke_color", "#111111"))

    cursor_x = int((img_w - total_w) / 2)
    center_y = img_h // 2

    for ch, is_kw, fnt, cw, _ in char_boxes:
        col = c_kw if is_kw else c_primary
        draw.text(
            (cursor_x + cw // 2, center_y),
            ch,
            font=fnt,
            fill=col,
            stroke_width=stroke_w,
            stroke_fill=c_stroke,
            anchor="mm",
        )
        cursor_x += cw

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    # 水平保持在安全区 [80, 860] 中轴，垂直严格位于平台底部安全窗内 [1160, 1476]
    return compute_subtitle_place_xy(img_w, img_h, tpl)


def _resolve_packaging_theme(style_id: str) -> str:
    """将 20 套风格谱 ID 映射到对应的包装视觉皮肤主题。"""
    if style_id in ("style_01", "style_09", "style_10", "style_15", "style_17"):
        return "tech_console"
    if style_id in ("style_03", "style_16", "style_19"):
        return "story_emerald" if style_id == "style_19" else "story_yellow_punch"
    if style_id in ("style_11", "style_12", "style_18", "style_20"):
        return "editorial_white" if style_id in ("style_12", "style_18") else "editorial_dark"
    if style_id == "style_02":
        return "hardcore_raw_stroke"
    if style_id in ("style_06", "style_08", "style_13"):
        return "cinema_serif"
    if style_id == "style_05":
        return "paper_stopmotion"
    return "glass_founder"


def render_packaging_png(
    elem: Dict[str, Any],
    font_path: str | None,
    out_png: Path,
    default_style_id: str = "style_14",
) -> Tuple[int, int]:
    """渲染花字、顶部节点条或矢量贴纸为透明 PNG，按风格谱主题动态换肤，并严格遵循 elem['bbox']。"""
    from PIL import Image, ImageDraw, ImageFont

    bbox = elem["bbox"]
    w, h = int(bbox["w"]), int(bbox["h"])
    custom_p = elem.get("custom_png")
    if custom_p and Path(custom_p).exists():
        import shutil
        out_png.parent.mkdir(parents=True, exist_ok=True)
        if Path(custom_p).resolve() != out_png.resolve():
            shutil.copy2(custom_p, out_png)
        return int(bbox["x"]), int(bbox["y"])

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    kind = elem["kind"]
    style_id = str(elem.get("style_id") or default_style_id or "style_14")
    theme = _resolve_packaging_theme(style_id)

    fb_path = _get_fallback_font_path()

    def _safe_font(sz: int, text_sample: str) -> Any:
        use_font = font_path
        if font_path and fb_path:
            f_probe = ImageFont.truetype(font_path, sz)
            if any(_char_is_tofu(f_probe, str(font_path), ch) for ch in text_sample):
                use_font = fb_path
        return ImageFont.truetype(use_font, sz) if use_font else ImageFont.load_default()

    if kind == "milestone_bar":
        # 适用于 style_03 金线里程碑进度条 & style_15 极客顶部步骤导航条
        nodes = elem.get("nodes") or ["300万开店", "600平布局", "200平明厨", "早茶/桑拿鸡"]
        active_idx = int(elem.get("active_index", 0))
        is_tech = theme == "tech_console"
        bg_col = (10, 14, 22, 228) if is_tech else (18, 16, 14, 210)
        accent = (103, 232, 249, 255) if is_tech else (248, 231, 49, 255)
        draw.rounded_rectangle([1, 1, w - 2, h - 2], radius=12, fill=bg_col, outline=(*accent[:3], 170), width=2)
        n_nodes = max(2, len(nodes))
        pad_x = min(96, max(72, w // 10))
        step_x = (w - 2 * pad_x) / float(n_nodes - 1)
        line_y = h // 2 + 8
        draw.line([(pad_x, line_y), (w - pad_x, line_y)], fill=(148, 163, 184, 140), width=3)
        prog_x = pad_x + int(step_x * min(active_idx, n_nodes - 1))
        draw.line([(pad_x, line_y), (prog_x, line_y)], fill=accent, width=4)
        f_node = _safe_font(18, "".join(nodes))
        for ni, label in enumerate(nodes):
            nx = pad_x + int(step_x * ni)
            is_act = ni <= active_idx
            col_dot = accent if is_act else (100, 116, 139, 220)
            r_dot = 7 if ni == active_idx else 5
            draw.ellipse([nx - r_dot, line_y - r_dot, nx + r_dot, line_y + r_dot], fill=col_dot, outline=(255, 255, 255, 230), width=2)
            t_col = (255, 255, 255, 255) if is_act else (180, 190, 205, 220)
            draw.text((nx, line_y - 18), str(label)[:12], font=f_node, fill=t_col, anchor="mm")
    elif kind == "flower_text":
        text_val = str(elem["text_or_asset"]).strip()[:18]
        font_size = min(46, max(24, int(h * 0.46)))
        while font_size > 20:
            fnt = _safe_font(font_size, text_val)
            bb = draw.textbbox((0, 0), text_val, font=fnt)
            if (bb[2] - bb[0]) <= w - 58:
                break
            font_size -= 2
        fnt = _safe_font(font_size, text_val)

        if theme == "hardcore_raw_stroke":
            # style_02: 无底框硬核炸裂亮金黄大字 + 厚黑描边
            draw.text((w // 2, h // 2), text_val, font=fnt, fill=(239, 212, 95, 255), stroke_width=5, stroke_fill=(12, 12, 14, 255), anchor="mm")
        elif theme == "story_yellow_punch":
            # style_03: 强对比半透炭黑横条 + 亮黄#F8E731重描边大字
            draw.rounded_rectangle([2, 6, w - 3, h - 6], radius=10, fill=(14, 14, 16, 185), outline=(248, 231, 49, 210), width=2)
            draw.text((w // 2, h // 2), text_val, font=fnt, fill=(248, 231, 49, 255), stroke_width=3, stroke_fill=(15, 15, 15, 255), anchor="mm")
        elif theme == "story_emerald":
            # style_19: 翠绿实心冲击块 + 深黑粗字
            draw.rounded_rectangle([2, 4, w - 3, h - 4], radius=14, fill=(46, 204, 113, 240), outline=(255, 255, 255, 235), width=2)
            draw.text((w // 2, h // 2), text_val, font=fnt, fill=(10, 15, 12, 255), anchor="mm")
        elif theme == "editorial_white":
            # style_12/18: Apple/Notion 极简白底浅灰卡片 + 深墨灰细框 + 黑色细边左竖条
            draw.rounded_rectangle([2, 2, w - 3, h - 3], radius=16, fill=(248, 250, 252, 242), outline=(203, 213, 225, 255), width=3)
            draw.rounded_rectangle([14, 16, 22, h - 16], radius=4, fill=(15, 23, 42, 255))
            draw.text(((w + 14) // 2, h // 2), text_val, font=fnt, fill=(15, 23, 42, 255), anchor="mm")
        elif theme == "cinema_serif":
            # style_06/08/13: 电影感宋体金句条 —— 无重边框，细黑渐变底带装饰短金线
            draw.rounded_rectangle([4, 10, w - 5, h - 10], radius=8, fill=(10, 10, 12, 125))
            draw.line([(20, h // 2), (44, h // 2)], fill=(247, 206, 119, 220), width=2)
            draw.line([(w - 44, h // 2), (w - 20, h // 2)], fill=(247, 206, 119, 220), width=2)
            draw.text((w // 2, h // 2), text_val, font=fnt, fill=(250, 244, 228, 255), stroke_width=2, stroke_fill=(18, 16, 14, 255), anchor="mm")
        elif theme == "paper_stopmotion":
            # style_05: 皱纹米白纸片 + 红色工程标注线 + 红色铆钉点
            draw.rounded_rectangle([4, 4, w - 5, h - 5], radius=6, fill=(247, 243, 234, 242), outline=(220, 38, 38, 235), width=3)
            draw.ellipse([16, h // 2 - 7, 30, h // 2 + 7], fill=(220, 38, 38, 255))
            draw.text(((w + 18) // 2, h // 2), text_val, font=fnt, fill=(28, 25, 23, 255), anchor="mm")
        elif theme == "tech_console":
            # style_15/01/09/10/17: 暗黑极客终端胶囊
            draw.rounded_rectangle([2, 2, w - 3, h - 3], radius=10, fill=(10, 14, 23, 235), outline=(56, 189, 248, 220), width=2)
            draw.rounded_rectangle([14, 18, 22, h - 18], radius=3, fill=(251, 224, 136, 255))
            draw.text(((w + 14) // 2, h // 2), text_val, font=fnt, fill=(251, 224, 136, 255), anchor="mm")
        else:
            # 默认 style_14: 深色毛玻璃金边胶囊 + 左侧高亮竖条
            draw.rounded_rectangle([2, 2, w - 3, h - 3], radius=18, fill=(15, 23, 42, 220), outline=(255, 213, 79, 245), width=3)
            draw.rounded_rectangle([14, 18, 22, h - 18], radius=4, fill=(255, 213, 79, 255))
            draw.text(((w + 14) // 2, h // 2), text_val, font=fnt, fill=(255, 255, 255, 255), anchor="mm")
    else:
        name = Path(elem["text_or_asset"]).name
        fnt = ImageFont.truetype(font_path, 36) if font_path else ImageFont.load_default()
        if "arrow" in name:
            pts = [(12, h // 2 - 14), (w - 58, h // 2 - 14), (w - 58, 12), (w - 8, h // 2), (w - 58, h - 12), (w - 58, h // 2 + 14), (12, h // 2 + 14)]
            draw.polygon(pts, fill=(255, 213, 79, 245), outline=(17, 17, 17, 255))
        elif "check" in name:
            r = min(w, h) // 2 - 4
            cx, cy = w // 2, h // 2
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(34, 197, 94, 245), outline=(255, 255, 255, 255), width=4)
            draw.line([(cx - r // 2, cy), (cx - 4, cy + r // 2 - 6), (cx + r // 2, cy - r // 2 + 8)], fill=(255, 255, 255, 255), width=8)
        else:
            draw.rounded_rectangle([2, 2, w - 3, h - 3], radius=16, fill=(239, 68, 68, 242), outline=(255, 255, 255, 255), width=4)
            draw.text((w // 2, h // 2), "重点必看", font=fnt, fill=(255, 255, 255, 255), anchor="mm")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return int(bbox["x"]), int(bbox["y"])


def resolve_packaging_source(
    elem: Dict[str, Any],
    eid: str,
    render_final: Path,
    stage_assets: Path,
    font_path: Optional[str],
    cur_style_id: str,
) -> Optional[Path]:
    """定位或按需补渲染包装图层的 RGBA PNG 源文件路径。"""
    candidates = [
        render_final / f"{eid}.png",
        Path(str(elem.get("custom_png", ""))) if elem.get("custom_png") else Path("__none__"),
        stage_assets / f"{eid}.png",
    ]
    src_png = next((c for c in candidates if c.is_file()), None)
    if src_png is not None:
        return src_png
    generated = render_final / f"{eid}.png"
    render_packaging_png(elem, font_path, generated, default_style_id=cur_style_id)
    return generated if generated.is_file() else None


def build_base_video_track(
    pipeline_dir: Path,
    edl: Dict[str, Any],
    color_plan: Dict[str, Any],
    target_frames: int,
    fps: int,
    work_dir: Path,
) -> Path:
    """按 EDL 逐段精确裁切与调色后，无缝拼接成基础视频轨 (无音频)。"""
    base_mp4 = work_dir / "base_video.mp4"
    edl_p = pipeline_dir / "04_edl.json"
    color_p = pipeline_dir / "07_color_plan.json"
    if base_mp4.exists() and base_mp4.stat().st_size > 1_000_000:
        media_mtimes = [Path(c["media_path"]).stat().st_mtime for c in edl.get("clips", []) if Path(c["media_path"]).exists()]
        latest_dep = max(
            Path(__file__).stat().st_mtime,
            edl_p.stat().st_mtime if edl_p.exists() else 0.0,
            color_p.stat().st_mtime if color_p.exists() else 0.0,
            max(media_mtimes) if media_mtimes else 0.0,
        )
        if base_mp4.stat().st_mtime >= latest_dep:
            print(f"[render] 复用已就绪的基础视频轨 -> {base_mp4}")
            return base_mp4

    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 渲染视频轨。")

    color_map = {c["clip_id"]: c for c in color_plan.get("clip_plans", [])}
    seg_files: List[Path] = []
    accum_frames = 0
    clips = edl["clips"]

    for idx, clip in enumerate(clips):
        seg_out = work_dir / f"seg_{idx+1:03d}.mp4"
        # 最后一段吸收舍入误差以严丝合缝匹配 target_frames
        if idx == len(clips) - 1:
            clip_frames = max(1, target_frames - accum_frames)
        else:
            clip_frames = max(1, int(clip["timeline_out_frame"]) - int(clip["timeline_in_frame"]))
        accum_frames += clip_frames

        clip_dur = clip_frames / float(fps)
        src_in = float(clip["source_in_sec"])
        src_avail = max(0.1, float(clip["source_out_sec"]) - src_in)

        fc = clip.get("frame_canvas")
        tw = int(fc["w"]) if fc else CANVAS_WIDTH
        th = int(fc["h"]) if fc else CANVAS_HEIGHT
        y_frac = float(fc.get("crop_y_frac", 0.32)) if fc else 0.32
        crop_expr = f"crop={tw}:{th}:(iw-ow)/2:(ih-oh)*{y_frac:.3f}" if fc else f"crop={tw}:{th}"
        filters: List[str] = [
            f"scale={tw}:{th}:force_original_aspect_ratio=increase",
            crop_expr,
            f"fps={fps}",
        ]
        # 若源片段不足该镜头时长，末帧定格补齐 (绝不黑帧)
        if clip_dur > src_avail + 0.02:
            pad_sec = clip_dur - src_avail + 0.5
            filters.append(f"tpad=stop_mode=clone:stop_duration={pad_sec:.3f}")

        cp = color_map.get(clip["clip_id"])
        if cp and cp.get("apply_color_grade") and cp.get("vf_filter"):
            filters.append(cp["vf_filter"])

        if clip.get("vf_extra"):
            filters.append(str(clip["vf_extra"]))

        if fc:
            bx, by = int(fc["x"]), int(fc["y"])
            bg_hex = str(fc.get("bg_hex", "0x0F172A"))
            filters.append(f"pad={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{bx}:{by}:{bg_hex}")

        filters.extend([
            "setsar=1",
            "scale=out_color_matrix=bt709:out_range=tv",
            "format=yuv420p",
            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        ])
        vf_str = ",".join(filters)

        bt709_args = [
            "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]
        run_cmd([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{src_in:.4f}",
            "-i", clip["media_path"],
            "-an",
            "-vf", vf_str,
            "-frames:v", str(clip_frames),
            "-r", str(fps), "-fps_mode", "cfr",
            "-c:v", "libx264", *bt709_args, "-preset", "fast", "-crf", "17",
            str(seg_out),
        ])
        seg_files.append(seg_out)

    concat_list = work_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in seg_files) + "\n", encoding="utf-8")
    base_mp4 = work_dir / "base_video.mp4"
    # 各段 seg_xxx.mp4 均已精确按 1080x1920 CFR 30fps yuv420p 编码完成，直接 -c:v copy 无损合流，避免多代有损重编码
    run_cmd([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "copy",
        str(base_mp4),
    ])
    return base_mp4


def render_mp4(pipeline_dir: Path, preview: bool = False) -> Path:
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("需要 ffmpeg 渲染成片。")

    edl = read_json(pipeline_dir / "04_edl.json")
    subs = read_json(pipeline_dir / "05_subtitles.json")
    pkg = read_json(pipeline_dir / "06_packaging.json")
    color_plan = read_json(pipeline_dir / "07_color_plan.json")
    mix_metrics = read_json(pipeline_dir / "08_mix_metrics.json")

    fps = int(edl.get("fps", DEFAULT_FPS))
    master_wav = Path(mix_metrics["master_wav"])
    audio_dur = get_wav_duration(master_wav)
    target_frames = max(1, int(round(audio_dur * fps)))

    work_dir = pipeline_dir / ("_render_preview" if preview else "_render_final")
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. 构建精确帧数的基础视频轨
    base_mp4 = build_base_video_track(pipeline_dir, edl, color_plan, target_frames, fps, work_dir)

    # 2. 预渲染透明字幕层与包装层 PNG
    font_path = subs.get("font_path") or find_commercial_font()
    tpl = subs["template_config"]
    cur_style_id = str(subs.get("template_id") or pkg.get("style_id") or "style_14")
    overlays: List[Tuple[Path, int, int, int, int]] = []

    # 先叠底层全局舞台画布框/分屏卡片，再叠浮动花字/贴纸，最顶层铺字幕确保永不被遮挡
    for elem in pkg.get("stage_overlays", []):
        png_p = work_dir / f"{elem['id']}.png"
        px, py = render_packaging_png(elem, font_path, png_p, default_style_id=cur_style_id)
        overlays.append((png_p, px, py, int(elem["start_frame"]), int(elem["end_frame"])))

    for elem in pkg.get("visual_elements", []):
        png_p = work_dir / f"{elem['id']}.png"
        px, py = render_packaging_png(elem, font_path, png_p, default_style_id=cur_style_id)
        overlays.append((png_p, px, py, int(elem["start_frame"]), int(elem["end_frame"])))

    for sub in subs.get("entries", []):
        png_p = work_dir / f"{sub['id']}.png"
        px, py = render_subtitle_png(sub, tpl, font_path, png_p)
        sf = int(round(float(sub["start_sec"]) * fps))
        ef = max(sf + 1, int(round(float(sub["end_sec"]) * fps)))
        overlays.append((png_p, px, py, sf, ef))

    # 3. 一次性 filter_complex 叠加全部图层并封装母带音轨
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(base_mp4)]
    for png_p, *_ in overlays:
        cmd += ["-i", str(png_p)]
    cmd += ["-i", str(master_wav)]

    audio_input_idx = len(overlays) + 1
    fc_parts: List[str] = []
    cur_label = "[0:v]"
    for i, (_, px, py, sf, ef) in enumerate(overlays, start=1):
        out_label = f"[v{i}]"
        fc_parts.append(f"{cur_label}[{i}:v]overlay=x={px}:y={py}:enable='between(n,{sf},{ef})'{out_label}")
        cur_label = out_label

    if preview:
        fc_parts.append(f"{cur_label}scale=540:960,format=yuv420p[vout]")
        v_preset = "ultrafast"
        crf_val = "26"
        out_mp4 = pipeline_dir / "output_preview.mp4"
    else:
        fc_parts.append(f"{cur_label}format=yuv420p[vout]")
        v_preset = "medium"
        crf_val = "18"
        out_mp4 = pipeline_dir / "output_final.mp4"

    cmd += [
        "-filter_complex", ";".join(fc_parts),
        "-map", "[vout]",
        "-map", f"{audio_input_idx}:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-preset", v_preset, "-crf", crf_val,
        "-r", str(fps), "-fps_mode", "cfr",
        "-frames:v", str(target_frames),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{audio_dur:.4f}",
        str(out_mp4),
    ]
    run_cmd(cmd)
    print(f"[render] {'低清粗剪预览' if preview else '高清成片'}渲染完成 -> {out_mp4}")
    return out_mp4


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="出片1: 本地自动渲染预览与成片 MP4")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--preview", action="store_true", help="输出低清快速粗剪预览 (540x960)")
    args = parser.parse_args()
    render_mp4(Path(args.pipeline_dir), preview=args.preview)


if __name__ == "__main__":
    main()
