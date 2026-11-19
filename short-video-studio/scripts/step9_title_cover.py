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
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SAFE_ZONE,
    find_bin,
    find_commercial_font,
    load_style_applied,
    maybe_reexec_in_venv,
    read_json,
    run_cmd,
    write_json,
)
from generate_code_broll import resolve_style_palette

maybe_reexec_in_venv()

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from vision_inspector import check_bbox_iou, compute_laplacian_sharpness, inspect_images

_CTA_STOP_KWS = {"评论区", "点赞", "关注", "私信", "门店地址", "戳链接", "收藏"}


def _fit_text(draw: Any, text: str, font_path: Optional[str], start_size: int, max_width: int, min_size: int = 24) -> Tuple[Any, str]:
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


def _hex_to_rgba(hex_str: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
    return (24, 24, 28, alpha)


def pick_best_cover_frame(
    edl: Dict[str, Any],
    pipeline_dir: Path,
    work_dir: Path,
    color_plan: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    """按镜头集中批抽最多 10 张候选帧，优选高锐度+自然睁眼帧，并同步施加对应 Step 7 调色。"""
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
        timestamps = []
        for off in offsets:
            if len(candidates) + len(timestamps) >= 10:
                break
            ts = min(max(0.0, c_out - 0.03), max(0.0, c_in + off))
            timestamps.append((ts, off))
        if not timestamps:
            break

        # 每个镜头最多 spawn 1 次 ffmpeg（用 select='...' 一次批抽该镜头所有偏移帧），大幅减少进程冷启动
        if len(timestamps) == 1:
            ts, off = timestamps[0]
            out_png = work_dir / f"cand_{len(candidates):02d}.png"
            run_cmd([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{ts:.3f}", "-i", c["media_path"],
                "-frames:v", "1", "-s", f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
                str(out_png),
            ], check=False)
            if out_png.exists():
                candidates.append((out_png, round(float(c.get("timeline_in_sec", 0.0)) + off, 2), c["clip_id"], c["media_path"], ts))
        else:
            sel_expr = "+".join(f"lt(prev_pts*TB,{ts:.3f})*gte(pts*TB,{ts:.3f})" for ts, _ in timestamps)
            batch_pat = work_dir / f"c{c_idx}_%02d.png"
            run_cmd([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-t", f"{max(0.5, c_out + 0.1):.3f}", "-i", c["media_path"],
                "-vf", f"select='{sel_expr}',scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}",
                "-fps_mode", "passthrough",
                str(batch_pat),
            ], check=False)
            extracted = sorted(work_dir.glob(f"c{c_idx}_*.png"))
            for k, p_img in enumerate(extracted[: len(timestamps)]):
                ts, off = timestamps[k]
                out_png = work_dir / f"cand_{len(candidates):02d}.png"
                p_img.replace(out_png)
                candidates.append((out_png, round(float(c.get("timeline_in_sec", 0.0)) + off, 2), c["clip_id"], c["media_path"], ts))

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
            comp_score = 1.0 if 0.22 <= fy_c <= 0.65 else 0.55

        total = 0.45 * s_norm + 0.40 * face_score + 0.15 * comp_score
        if total > best_score:
            best_score = total
            best_idx = idx
            best_face = f_bb
            best_detail = f"clip={cid}@{tl_sec}s sharp={sh:.0f} face={face_score:.2f}"

    chosen_path, chosen_t, chosen_cid, chosen_mp, chosen_src_ts = candidates[best_idx]
    # 若该镜头有 Step 7 调色方案，直接输出带调色 LUT 的最终封面底图，确保与正片色调完全一致
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
    cand_ys = [280, 980, 340, 880]
    if not face_bbox:
        return 300
    f_px = {
        "x": float(face_bbox["x"]) * CANVAS_WIDTH,
        "y": float(face_bbox["y"]) * CANVAS_HEIGHT,
        "w": float(face_bbox["w"]) * CANVAS_WIDTH,
        "h": float(face_bbox["h"]) * CANVAS_HEIGHT,
    }
    best_y = cand_ys[0]
    best_iou = 999.0
    for cy in cand_ys:
        c_box = {"x": 80.0, "y": float(cy), "w": 780.0, "h": float(card_h)}
        iou = check_bbox_iou(c_box, f_px)
        if iou < best_iou:
            best_iou = iou
            best_y = cy
        if iou == 0.0:
            break
    return best_y


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
    sec_kw = keywords[1] if len(keywords) > 1 else (top_kw if len(sentences) <= 1 else "关键细节")

    hook_clean = hook_text.rstrip("。！？，；")
    if len(hook_clean) > 22:
        parts = [p for p in hook_clean.split("，") if p]
        hook_clean = "，".join(parts[:2]) if len("，".join(parts[:2])) <= 24 else parts[0]

    raw_qs = raw_intel.get("customer_questions", [])
    raw_pains = raw_intel.get("pain_keywords", [])

    if track == "store":
        q_lead = f"被问最多的问题：{raw_qs[0][:20]}" if raw_qs else hook_clean
        titles = [
            q_lead,
            f"把「{top_kw}」真实成本摊开算一遍，同城老客自己判断",
            f"做这行最在意「{sec_kw}」，直接把一线标准讲透",
        ]
        cover_main_l1 = top_kw[:8] if len(top_kw) >= 2 else hook_clean[:8]
        cover_main_l2 = "一线标准拆解"
        cover_sub = f"门店实拍 · {raw_pains[0]}问题讲透" if raw_pains else f"实拍纪实 · {sec_kw[:8]}"
        hashtags = "#同城探店 #实体店日常 #本地生活 #真实记录 #一线实拍"
    else:
        q_lead = f"评论区都在问：{raw_qs[0][:20]}" if raw_qs else hook_clean
        titles = [
            q_lead,
            f"把「{top_kw}」和「{sec_kw}」放在一起看，差别在哪很清楚",
            f"少走弯路的一条实操细节：{hook_clean[:18]}",
        ]
        cover_main_l1 = top_kw[:8] if len(top_kw) >= 2 else hook_clean[:8]
        cover_main_l2 = "关键方法拆解"
        cover_sub = f"实操复盘 · {sec_kw[:8]}"
        hashtags = "#短视频实操 #创作复盘 #内容拆解 #经验分享 #干货"

    cover_main = f"{cover_main_l1}\n{cover_main_l2}"
    cover_path = pipeline_dir / "09_cover.jpg"
    cover_3x4_path = pipeline_dir / "09_cover_3x4.jpg"

    with tempfile.TemporaryDirectory(prefix="svs_cover_") as td:
        best_frame_p, best_meta = pick_best_cover_frame(edl, pipeline_dir, Path(td), color_plan=color_plan)
        if best_frame_p and best_frame_p.exists():
            base_img = Image.open(best_frame_p).convert("RGBA")
        else:
            base_img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), _hex_to_rgba(pal["bg_hex"], 255))

    card_x1, card_x2 = 80, SAFE_ZONE["x_max"] - 20  # [80, 860] -> w=780
    card_h = 340
    card_y = _choose_cover_card_y(best_meta.get("face_bbox"), card_h=card_h)

    overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_path = find_commercial_font(pal.get("font_candidates"))

    # 采用与当前 style_id 同步的高级极简杂志风/暖食风卡片（废除深蓝底+5px粗黄框）
    card_fill = _hex_to_rgba(pal["card_bg_hex"], 216 if pal.get("is_light") else 204)
    border_col = _hex_to_rgba(pal["border_hex"], 230)
    accent_col = _hex_to_rgba(pal["accent_hex"], 255)
    badge_txt_col = _hex_to_rgba(pal["badge_text_hex"], 255)
    main_txt_col = _hex_to_rgba(pal["title_hex"], 255)

    draw.rounded_rectangle([card_x1, card_y, card_x2, card_y + card_h], radius=22, fill=card_fill, outline=border_col, width=2)
    draw.rounded_rectangle([card_x1, card_y + 24, card_x1 + 8, card_y + card_h - 24], radius=4, fill=accent_col)

    # 副标签胶囊：实测宽度后自适应宽，绝不超出胶囊或卡片
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
    draw.text((inner_x, card_y + 216), fit_l2, fill=accent_col, font=f_l2)

    composed = Image.alpha_composite(base_img, overlay).convert("RGB")
    composed.save(cover_path, quality=92)

    crop_3x4 = composed.crop((0, 240, CANVAS_WIDTH, 1680))
    crop_3x4.save(cover_3x4_path, quality=92)

    md_content = "\n".join([
        "# 标题与黄金封面交付方案",
        "",
        f"- **轨道策略**：{'门店轨（强信任与评论咨询转化）' if track=='store' else '博主轨（强好奇与完播留存）'}",
        f"- **黄金封面选帧**：{best_meta.get('detail', '首帧回退')} (综合分 `{best_meta.get('score', 0)}` | 卡片Y={card_y} | 调色同步={'是' if best_meta.get('graded_matched') else '否'})",
        f"- **封面主大字**：`{cover_main.replace(chr(10), ' / ')}`",
        f"- **封面副标签**：`{fit_sub}`",
        f"- **双构图封面**：`{cover_path.name}` (9:16 首帧) & `{cover_3x4_path.name}` (3:4 主页网格)",
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
        "cover_jpg": str(cover_path.resolve()),
        "cover_3x4_jpg": str(cover_3x4_path.resolve()),
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
