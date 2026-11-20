#!/usr/bin/env python3
"""
[Step 9: 黄金封面帧优选 + 已调色风格同步封套 + 像素测宽防破框 + 去套话标题]
1. 单次批抽策略减少子进程开销，优先对选出帧施加 Step 7 调色滤镜保持封面/成片影调一致。
2. 彻底清除“深蓝底+5px粗黄框”模板味，按 style_id 同步 resolve_style_palette 色板。
3. 使用 Pillow textbbox 动态缩字控制副标签胶囊与主大字宽度，100% 保证落入 SAFE_ZONE 与 3:4 窗内。
4. 避开 CTA 词摘取有效关键短语，防止拼接出“只做【评论区】？”等病句。
输出：pipeline/09_title_cover.md, pipeline/09_title_cover.json, pipeline/09_cover.jpg, pipeline/09_cover_3x4.jpg
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SAFE_ZONE,
    bbox_iou,
    contrast_ratio,
    find_bin,
    find_commercial_font,
    fit_text_line,
    hex_to_rgba,
    load_style_applied,
    load_style_spectrum_entry,
    maybe_reexec_in_venv,
    read_json,
    run_cmd,
    write_json,
)
from generate_code_broll import resolve_style_palette

maybe_reexec_in_venv()

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from vision_inspector import compute_laplacian_sharpness, inspect_images

_CTA_STOP_KWS = {"评论区", "点赞", "关注", "私信", "门店地址", "戳链接", "收藏"}


def _fit_text(draw: Any, text: str, font_path: Optional[str], start_size: int, max_width: int, min_size: int = 24) -> Tuple[Any, str]:
    return fit_text_line(draw, str(text or ""), font_path, max_width=max_width, max_size=start_size, min_size=min_size)


def pick_best_cover_frame(
    edl: Dict[str, Any],
    pipeline_dir: Path,
    work_dir: Path,
    color_plan: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    """按镜头精准 -ss 快速抽帧最多 10 张候选帧，优选高锐度+自然睁眼帧，并同步施加对应 Step 7 调色。"""
    ffmpeg = find_bin("ffmpeg")
    clips = edl.get("clips", [])
    if not ffmpeg or not clips:
        return None, {"score": 0.0, "face_bbox": None, "reason": "no_media"}

    real_clips = [c for c in clips if c.get("track_role") != "code_broll" and Path(c["media_path"]).exists()]
    pool = real_clips or [c for c in clips if Path(c["media_path"]).exists()]
    if not pool:
        return None, {"score": 0.0, "face_bbox": None, "reason": "no_existing_file"}

    vf_by_cid = {p["clip_id"]: p.get("vf_filter") for p in (color_plan or {}).get("clip_plans", []) if p.get("apply_color_grade")}
    candidates: List[Tuple[Path, float, str, Optional[str], float]] = []

    for c_idx, c in enumerate(pool[:4]):
        c_in = float(c.get("source_in_sec", 0.0))
        c_out = float(c.get("source_out_sec", c_in + 1.0))
        span = max(0.1, c_out - c_in)
        offsets = [0.08, span * 0.45, span * 0.80] if c_idx == 0 else [0.10, span * 0.50]
        for off in offsets:
            if len(candidates) >= 10:
                break
            ts = min(max(0.0, c_out - 0.03), max(0.0, c_in + off))
            out_png = work_dir / f"cand_{len(candidates):02d}.png"
            run_cmd([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{ts:.3f}", "-i", c["media_path"],
                "-frames:v", "1", "-s", f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
                str(out_png),
            ], check=False)
            if out_png.exists():
                candidates.append((out_png, round(float(c.get("timeline_in_sec", 0.0)) + off, 2), c["clip_id"], c["media_path"], ts))
        if len(candidates) >= 10:
            break

    if not candidates:
        return None, {"score": 0.0, "face_bbox": None, "reason": "extract_failed"}

    vis_map = inspect_images([p for p, _, _, _, _ in candidates])
    sharps = []
    for p, _, _, _, _ in candidates:
        with Image.open(p) as im:
            sharps.append(compute_laplacian_sharpness(np.asarray(im.convert("RGB"))))
    s_min, s_max = min(sharps), max(sharps)

    best_idx = 0
    best_score = -1.0
    best_face: Optional[Dict[str, float]] = None
    best_detail = ""

    for idx, ((p, tl_sec, cid, _, _), sh) in enumerate(zip(candidates, sharps)):
        s_norm = (sh - s_min) / max(1e-4, s_max - s_min) if s_max > s_min else 0.7
        vr = vis_map.get(str(p), {})
        faces = vr.get("faces") or []
        f_bb = None
        if faces:
            f0 = faces[0]
            f_bb = f0.get("bbox")
            eye_r = float(f0.get("eye_open_ratio", 0.24))
            mouth_r = float(f0.get("mouth_open_ratio", 0.15))
            if eye_r < 0.13:
                face_score = 0.15
            elif mouth_r > 0.42:
                face_score = 0.45
            else:
                face_score = min(1.0, 0.72 + (eye_r - 0.13) * 2.0)
        else:
            face_score = 0.55

        comp_score = 0.85
        if f_bb:
            fy_c = float(f_bb["y"]) + float(f_bb["h"]) * 0.5
            if fy_c > 1.0:
                fy_c /= float(CANVAS_HEIGHT)
            comp_score = 1.0 if 0.22 <= fy_c <= 0.65 else 0.55

        total = 0.45 * s_norm + 0.40 * face_score + 0.15 * comp_score
        if total > best_score:
            best_score = total
            best_idx = idx
            best_face = f_bb
            best_detail = f"clip={cid}@{tl_sec}s sharp={sh:.0f} face={face_score:.2f}"

    chosen_path, chosen_t, chosen_cid, chosen_mp, chosen_src_ts = candidates[best_idx]
    vf_grade = vf_by_cid.get(chosen_cid)
    if vf_grade:
        graded_png = work_dir / "best_graded.png"
        run_cmd([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{chosen_src_ts:.3f}", "-i", chosen_mp,
            "-frames:v", "1", "-vf", f"{vf_grade},scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}",
            str(graded_png),
        ], check=False)
        if graded_png.exists():
            chosen_path = graded_png

    return chosen_path, {
        "score": round(best_score, 3),
        "best_frame_sec": chosen_t,
        "best_clip_id": chosen_cid,
        "face_bbox": best_face,
        "graded_matched": bool(vf_grade),
        "detail": best_detail,
    }


def _choose_cover_card_y(face_bbox: Optional[Dict[str, float]], card_h: int = 340) -> int:
    """在 9:16 SAFE_ZONE 与 3:4 窗 [240, 1680] 内选与人脸重叠最小的 Y 坐标。"""
    cand_ys = [280, 980, 340, 880, 1080, 240]
    if not face_bbox:
        return 300
    fx, fy, fw, fh = float(face_bbox["x"]), float(face_bbox["y"]), float(face_bbox["w"]), float(face_bbox["h"])
    if max(fx, fy, fw, fh) <= 1.01:
        f_px = {"x": fx * CANVAS_WIDTH, "y": fy * CANVAS_HEIGHT, "w": fw * CANVAS_WIDTH, "h": fh * CANVAS_HEIGHT}
    else:
        f_px = {"x": fx, "y": fy, "w": fw, "h": fh}
    best_y = cand_ys[0]
    best_iou = 999.0
    for cy in cand_ys:
        c_box = {"x": 80.0, "y": float(cy), "w": 780.0, "h": float(card_h)}
        iou = bbox_iou(c_box, f_px)
        if iou < best_iou:
            best_iou = iou
            best_y = cy
        if iou == 0.0:
            break
    return best_y


def _render_ui_safe_preview(composed: Image.Image, out_path: Path) -> None:
    """生成叠加抖音/小红书平台交互区半透明红区与安全区绿框的验收预览图。"""
    ov = Image.new("RGBA", composed.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    # 顶部搜索与返回区 y < 160
    d.rectangle([0, 0, CANVAS_WIDTH, SAFE_ZONE["y_min"]], fill=(239, 68, 68, 80))
    # 右侧互动按钮区 x > 880
    d.rectangle([SAFE_ZONE["x_max"], SAFE_ZONE["y_min"], CANVAS_WIDTH, SAFE_ZONE["y_max"]], fill=(239, 68, 68, 90))
    # 底部文案与进度条区 y > 1480
    d.rectangle([0, SAFE_ZONE["y_max"], CANVAS_WIDTH, CANVAS_HEIGHT], fill=(239, 68, 68, 105))
    # 合法安全区绿线框
    d.rectangle([SAFE_ZONE["x_min"], SAFE_ZONE["y_min"], SAFE_ZONE["x_max"], SAFE_ZONE["y_max"]], outline=(34, 197, 94, 230), width=4)
    Image.alpha_composite(composed.convert("RGBA"), ov).convert("RGB").save(out_path, quality=88)


def run_step9(pipeline_dir: Path, track: str = "store", style_id: Optional[str] = None) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    color_p = pipeline_dir / "07_color_plan.json"
    color_plan = read_json(color_p) if color_p.exists() else None
    raw_intel_p = pipeline_dir / "00_raw_intel.json"
    raw_intel = read_json(raw_intel_p) if raw_intel_p.exists() else {}

    eff_style = style_id or edl.get("style_id") or load_style_applied(pipeline_dir)
    pal = resolve_style_palette(eff_style, track=track)

    sentences = audio_events["sentences"]
    hook_text = sentences[0]["text"] if sentences else "先看真实对比再决定"
    keywords: List[str] = []
    for s in sentences[:4]:
        for kw in s.get("keywords", []):
            if str(kw).strip() and str(kw).strip() not in _CTA_STOP_KWS and kw not in keywords:
                keywords.append(str(kw).strip())
    top_kw = keywords[0] if keywords else hook_text[:6]
    sec_kw = keywords[1] if len(keywords) > 1 else (top_kw if len(sentences) <= 1 else "")

    hook_clean = hook_text.rstrip("。！？，；")
    if len(hook_clean) > 22:
        parts = [p for p in hook_clean.split("，") if p]
        hook_clean = "，".join(parts[:2]) if len("，".join(parts[:2])) <= 24 else parts[0]

    sent2_clean = (sentences[1]["text"].rstrip("。！？，；") if len(sentences) > 1 else "")
    hook_parts = [p.strip() for p in hook_text.replace("？", "，").replace("！", "，").split("，") if p.strip()]
    alt_line2 = sec_kw or (hook_parts[1][:8] if len(hook_parts) > 1 else (sent2_clean[:8] or hook_clean[-8:]))

    raw_qs = raw_intel.get("customer_questions", [])
    raw_pains = raw_intel.get("pain_keywords", [])
    pain_phrase = ""
    if raw_pains:
        p0 = str(raw_pains[0]).strip()
        pain_phrase = f"{p0}讲透" if p0.endswith("问题") else f"{p0}问题讲透"

    if track == "store":
        q_lead = f"被问最多的问题：{raw_qs[0][:20]}" if raw_qs else hook_clean
        titles = [
            q_lead,
            f"把「{top_kw}」真实成本摊开算一遍，同城老客自己判断",
            f"做这行最在意「{sec_kw or top_kw}」，直接把一线标准讲透",
        ]
        cover_main_l1 = top_kw[:8] if len(top_kw) >= 2 else hook_clean[:8]
        cover_main_l2 = alt_line2[:8] if alt_line2[:8] != cover_main_l1 else (hook_clean[-8:] or "实地真算账")
        cover_sub = f"门店实拍 · {pain_phrase}" if pain_phrase else f"实拍纪实 · {(sec_kw or top_kw)[:8]}"
        hashtags = "#同城探店 #实体店日常 #本地生活 #真实记录 #一线实拍"
    else:
        q_lead = f"评论区都在问：{raw_qs[0][:20]}" if raw_qs else hook_clean
        titles = [
            q_lead,
            f"把「{top_kw}」和「{sec_kw or top_kw}」放在一起看，差别在哪很清楚",
            f"少走弯路的一条实操细节：{hook_clean[:18]}",
        ]
        cover_main_l1 = top_kw[:8] if len(top_kw) >= 2 else hook_clean[:8]
        cover_main_l2 = alt_line2[:8] if alt_line2[:8] != cover_main_l1 else (hook_clean[-8:] or "核心对照看")
        cover_sub = f"实操复盘 · {(sec_kw or top_kw)[:8]}"
        hashtags = "#短视频实操 #创作复盘 #内容拆解 #经验分享 #干货"

    cover_main = f"{cover_main_l1}\n{cover_main_l2}"
    cover_path = pipeline_dir / "09_cover.jpg"
    cover_3x4_path = pipeline_dir / "09_cover_3x4.jpg"
    ui_preview_path = pipeline_dir / "09_ui_safe_preview.jpg"

    with tempfile.TemporaryDirectory(prefix="svs_cover_") as td:
        best_frame_p, best_meta = pick_best_cover_frame(edl, pipeline_dir, Path(td), color_plan=color_plan)
        if best_frame_p and best_frame_p.exists():
            base_img = Image.open(best_frame_p).convert("RGBA")
        else:
            base_img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), hex_to_rgba(pal["bg_hex"], 255))

    style_entry = load_style_spectrum_entry(eff_style) if eff_style else None
    cover_layout_id = str(((style_entry or {}).get("title_cover_layout") or {}).get("layout_id") or "")
    use_jianying_poster = (cover_layout_id == "jianying_poster_4zone") or (eff_style == "style_14")

    card_x1, card_x2 = 80, SAFE_ZONE["x_max"] - 20  # [80, 860] -> w=780
    card_h = 340
    card_y = _choose_cover_card_y(best_meta.get("face_bbox"), card_h=card_h)

    overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = find_commercial_font(pal.get("font_candidates"))

    if use_jianying_poster:
        # 复刻《11月14日延期》四区高低错落无闷框爆款封面（左上引子+正红巨字 / 中左微斜黄字签 / 下方白引子+红白大字）
        # 上下轻柔暗角护底（不挡主实景和人脸，纯增文字对比）
        grad = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for gy in range(0, 520, 4):
            a = int(115 * (1.0 - gy / 520.0))
            gd.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 16, a))
        for gy in range(1060, CANVAS_HEIGHT, 4):
            a = int(135 * min(1.0, (gy - 1060) / 520.0))
            gd.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 16, a))
        overlay = Image.alpha_composite(overlay, grad)
        draw = ImageDraw.Draw(overlay)

        safe_w = SAFE_ZONE["x_max"] - 80  # 800px，严格在 [80, 880] 内不触碰右侧栏
        lead_small = "门店真实记录" if track == "store" else "实操真相拆解"
        f_lead, fit_lead = _fit_text(draw, lead_small, font_path, 48, 420, min_size=28)
        draw.text((88, 220), fit_lead, fill=(255, 255, 255, 255), font=f_lead, stroke_width=5, stroke_fill=(0, 0, 0, 255))

        f_l1, fit_l1 = _fit_text(draw, cover_main_l1, font_path, 108, safe_w - 20, min_size=56)
        draw.text((84 + 4, 286 + 6), fit_l1, fill=(0, 0, 0, 220), font=f_l1, stroke_width=7, stroke_fill=(0, 0, 0, 220))
        draw.text((84, 286), fit_l1, fill=(255, 42, 36, 255), font=f_l1, stroke_width=7, stroke_fill=(0, 0, 0, 255))

        # 中左区：微倾斜 -4.5° 明黄黑边贴纸字（cover_main_l2）
        f_l2, fit_l2 = _fit_text(draw, cover_main_l2, font_path, 82, safe_w - 60, min_size=44)
        l2_tmp = Image.new("RGBA", (safe_w + 80, 180), (0, 0, 0, 0))
        l2_d = ImageDraw.Draw(l2_tmp)
        l2_d.text((28 + 3, 42 + 4), fit_l2, fill=(0, 0, 0, 220), font=f_l2, stroke_width=6, stroke_fill=(0, 0, 0, 220))
        l2_d.text((28, 42), fit_l2, fill=(255, 226, 52, 255), font=f_l2, stroke_width=6, stroke_fill=(0, 0, 0, 255))
        l2_rot = l2_tmp.rotate(4.5, resample=Image.Resampling.BICUBIC, expand=True)
        l2_bb = l2_rot.getbbox()
        if l2_bb:
            l2_rot = l2_rot.crop(l2_bb)
        l2_y = 1040 if (best_meta.get("face_bbox") and best_meta["face_bbox"].get("y", 999) < 880) else 860
        overlay.alpha_composite(l2_rot, dest=(84, min(1120, l2_y)))

        # 下方转化区：白字黑边副标 + 红白双色大字（严格落在 y <= 1460 安全区内）
        f_sub, fit_sub = _fit_text(draw, cover_sub, font_path, 44, safe_w - 20, min_size=26)
        draw.text((88 + 2, 1228 + 3), fit_sub, fill=(0, 0, 0, 220), font=f_sub, stroke_width=4, stroke_fill=(0, 0, 0, 220))
        draw.text((88, 1228), fit_sub, fill=(255, 255, 255, 255), font=f_sub, stroke_width=4, stroke_fill=(0, 0, 0, 255))

        cta_punch = (top_kw[:4] + "实测") if len(top_kw) >= 2 else "一线实拍"
        f_cta, fit_cta = _fit_text(draw, cta_punch, font_path, 86, safe_w - 20, min_size=46)
        split_at = max(1, len(fit_cta) // 2)
        p_left, p_right = fit_cta[:split_at], fit_cta[split_at:]
        draw.text((86 + 3, 1296 + 5), p_left, fill=(0, 0, 0, 225), font=f_cta, stroke_width=6, stroke_fill=(0, 0, 0, 225))
        draw.text((86, 1296), p_left, fill=(255, 36, 56, 255), font=f_cta, stroke_width=6, stroke_fill=(0, 0, 0, 255))
        lw = int(draw.textbbox((0, 0), p_left, font=f_cta, stroke_width=6)[2])
        if p_right:
            draw.text((86 + lw + 3, 1296 + 5), p_right, fill=(0, 0, 0, 225), font=f_cta, stroke_width=6, stroke_fill=(0, 0, 0, 225))
            draw.text((86 + lw, 1296), p_right, fill=(255, 255, 255, 255), font=f_cta, stroke_width=6, stroke_fill=(0, 0, 0, 255))

        card_y = 220
        main_txt_col = (255, 255, 255, 255)
        card_fill = (0, 0, 0, 255)
        thumb_eval_box = (80, 220, 860, 440)
    else:
        # 其余风格沿用同步色板杂志卡片封套
        card_fill = hex_to_rgba(pal["card_bg_hex"], 224 if pal.get("is_light") else 212)
        border_col = hex_to_rgba(pal["border_hex"], 230)
        accent_col = hex_to_rgba(pal["accent_hex"], 255)
        badge_txt_col = hex_to_rgba(pal["badge_text_hex"], 255)
        main_txt_col = hex_to_rgba(pal["title_hex"], 255)

        if contrast_ratio(main_txt_col[:3], card_fill[:3]) < 4.5:
            main_txt_col = (17, 24, 39, 255) if pal.get("is_light") else (255, 255, 255, 255)
        l2_col = accent_col if contrast_ratio(accent_col[:3], card_fill[:3]) >= 3.2 else main_txt_col

        draw.rounded_rectangle([card_x1, card_y, card_x2, card_y + card_h], radius=22, fill=card_fill, outline=border_col, width=2)
        draw.rounded_rectangle([card_x1, card_y + 24, card_x1 + 8, card_y + card_h - 24], radius=4, fill=accent_col)

        inner_x = card_x1 + 32
        max_inner_w = (card_x2 - 28) - inner_x
        f_sub, fit_sub = _fit_text(draw, cover_sub, font_path, 34, max_inner_w - 44, min_size=22)
        sub_bb = draw.textbbox((0, 0), fit_sub, font=f_sub)
        cap_w = min(max_inner_w, max(220, int(sub_bb[2] - sub_bb[0]) + 44))
        draw.rounded_rectangle([inner_x, card_y + 28, inner_x + cap_w, card_y + 84], radius=12, fill=accent_col)
        draw.text((inner_x + cap_w // 2, card_y + 56), fit_sub, fill=badge_txt_col, font=f_sub, anchor="mm")

        f_l1, fit_l1 = _fit_text(draw, cover_main_l1, font_path, 78, max_inner_w, min_size=40)
        f_l2, fit_l2 = _fit_text(draw, cover_main_l2, font_path, 72, max_inner_w, min_size=36)
        draw.text((inner_x, card_y + 114), fit_l1, fill=main_txt_col, font=f_l1)
        draw.text((inner_x, card_y + 216), fit_l2, fill=l2_col, font=f_l2)
        thumb_eval_box = (card_x1, card_y, card_x2, card_y + card_h)

    composed = Image.alpha_composite(base_img, overlay).convert("RGB")
    composed.save(cover_path, quality=92)

    crop_3x4 = composed.crop((0, 240, CANVAS_WIDTH, 1680))
    crop_3x4.save(cover_3x4_path, quality=92)

    _render_ui_safe_preview(composed, ui_preview_path)
    # 180px 缩略图可读性验收：确认缩放后文字区域保留清晰对比与结构，不糊连
    thumb_180 = composed.resize((180, 320))
    thumb_box = (
        int(thumb_eval_box[0] * 180 / CANVAS_WIDTH),
        int(thumb_eval_box[1] * 320 / CANVAS_HEIGHT),
        int(thumb_eval_box[2] * 180 / CANVAS_WIDTH),
        int(thumb_eval_box[3] * 320 / CANVAS_HEIGHT),
    )
    thumb_patch = np.asarray(thumb_180.crop(thumb_box), dtype=np.float32)
    thumb_luma_std = float(np.std(0.2126 * thumb_patch[:, :, 0] + 0.7152 * thumb_patch[:, :, 1] + 0.0722 * thumb_patch[:, :, 2]))
    thumb_180_legible_ok = bool(contrast_ratio(main_txt_col[:3], card_fill[:3]) >= 4.5 and thumb_luma_std >= 18.0)

    md_content = "\n".join([
        "# 标题与黄金封面交付方案",
        "",
        f"- **轨道策略**：{'门店轨（强信任与评论咨询转化）' if track=='store' else '博主轨（强好奇与完播留存）'}",
        f"- **黄金封面选帧**：{best_meta.get('detail', '首帧回退')} (综合分 `{best_meta.get('score', 0)}` | 卡片Y={card_y} | 调色同步={'是' if best_meta.get('graded_matched') else '否'} | 180px缩略图={'通过' if thumb_180_legible_ok else '偏低'})",
        f"- **封面主大字**：`{cover_main.replace(chr(10), ' / ')}`",
        f"- **封面副标签**：`{fit_sub}`",
        f"- **双构图封面**：`{cover_path.name}` (9:16 首帧) & `{cover_3x4_path.name}` (3:4 主页网格) & `{ui_preview_path.name}` (平台UI遮罩预览)",
        "",
        "## 备选发布标题（3 选 1，已去 AI 套话）",
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
        "cover_sub": fit_sub,
        "hashtags": hashtags,
        "best_frame_meta": best_meta,
        "card_y": card_y,
        "thumb_180_legible_ok": thumb_180_legible_ok,
        "cover_jpg": str(cover_path.resolve()),
        "cover_3x4_jpg": str(cover_3x4_path.resolve()),
        "ui_safe_preview_jpg": str(ui_preview_path.resolve()),
    }
    write_json(pipeline_dir / "09_title_cover.json", out)
    print(f"[step9] 黄金封面(9:16+3:4)与标题已生成 (选帧分 {best_meta.get('score', 0)}) -> 09_cover.jpg")
    return out


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 9: 黄金封面帧优选与去套话标题")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--style-id", help="绑定风格谱 ID")
    args = parser.parse_args()
    run_step9(Path(args.pipeline_dir), track=args.track, style_id=args.style_id)


if __name__ == "__main__":
    main()
