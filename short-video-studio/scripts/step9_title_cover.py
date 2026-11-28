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
from datetime import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import face_framing
from common import (
    ASSETS_DIR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SAFE_ZONE,
    SCRIPTS_DIR,
    SUBSKILLS_DIR,
    bbox_iou,
    contrast_ratio,
    detect_active_agent,
    detect_browser_renderer,
    find_bin,
    find_commercial_font,
    fit_text_line,
    get_cached_font,
    hex_to_rgba,
    load_style_applied,
    load_style_spectrum_entry,
    load_typography_cover_rules,
    maybe_reexec_in_venv,
    read_json,
    run_cmd,
    write_json,
)

if __name__ == "__main__":
    maybe_reexec_in_venv()

from generate_code_broll import resolve_style_palette

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from vision_inspector import compute_laplacian_sharpness, inspect_images, segment_person_mask

_CTA_STOP_KWS = {"评论区", "点赞", "关注", "私信", "门店地址", "戳链接", "收藏"}


def _fit_text(draw: Any, text: str, font_path: Optional[str], start_size: int, max_width: int, min_size: int = 24) -> Tuple[Any, str]:
    return fit_text_line(draw, str(text or ""), font_path, max_width=max_width, max_size=start_size, min_size=min_size)


def pick_best_cover_frame(
    edl: Dict[str, Any],
    pipeline_dir: Path,
    work_dir: Path,
    color_plan: Optional[Dict[str, Any]] = None,
    audio_events: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    """结合全片情绪峰值句 + 开篇实拍镜头抽帧最多 12 张，以人脸眼部局部对焦锐度 + 自然睁眼优选黄金封面帧，并同步 Step 7 调色。"""
    ffmpeg = find_bin("ffmpeg")
    clips = edl.get("clips", [])
    if not ffmpeg or not clips:
        return None, {"score": 0.0, "face_bbox": None, "reason": "no_media"}

    real_clips = [c for c in clips if c.get("track_role") != "code_broll" and Path(c["media_path"]).exists()]
    pool = real_clips or [c for c in clips if Path(c["media_path"]).exists()]
    if not pool:
        return None, {"score": 0.0, "face_bbox": None, "reason": "no_existing_file"}

    # 真人出镜镜头优先入池（至少涵盖开篇/中段 A-roll），避免毛坯/无人背影夺走封面
    aroll_pool = [c for c in pool if c.get("track_role") == "a_roll"]
    selected_clips: List[Dict[str, Any]] = []
    seen_cids: set[str] = set()
    for c in (aroll_pool[:4] + pool[:4]):
        if c["clip_id"] not in seen_cids:
            selected_clips.append(c)
            seen_cids.add(c["clip_id"])
        if len(selected_clips) >= 6:
            break
    if audio_events:
        high_sents = [
            s for s in (audio_events.get("sentences") or [])
            if s.get("emotion_or_info") in ("hook", "twist", "persona") or (s.get("keywords") and len(s.get("keywords", [])) >= 2)
        ]
        for hs in high_sents[:4]:
            hs_t = float(hs.get("start_sec", 0.0))
            hit_c = next((c for c in (aroll_pool or pool) if float(c.get("timeline_in_sec", 0.0)) <= hs_t <= float(c.get("timeline_out_sec", 0.0))), None)
            if hit_c and hit_c["clip_id"] not in seen_cids:
                selected_clips.append(hit_c)
                seen_cids.add(hit_c["clip_id"])
            if len(selected_clips) >= 8:
                break

    vf_by_cid = {p["clip_id"]: p.get("vf_filter") for p in (color_plan or {}).get("clip_plans", []) if p.get("apply_color_grade")}
    candidates: List[Tuple[Path, float, str, Optional[str], float]] = []

    for c_idx, c in enumerate(selected_clips):
        c_in = float(c.get("source_in_sec", 0.0))
        c_out = float(c.get("source_out_sec", c_in + 1.0))
        span = max(0.1, c_out - c_in)
        offsets = [0.08, span * 0.45, span * 0.80] if c_idx == 0 else [span * 0.35, span * 0.65]
        for off in offsets:
            if len(candidates) >= 12:
                break
            ts = min(max(0.0, c_out - 0.03), max(0.0, c_in + off))
            out_png = work_dir / f"cand_{len(candidates):02d}.png"
            run_cmd([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{ts:.3f}", "-i", c["media_path"],
                "-frames:v", "1",
                "-vf", _cover_vf_with_face(str(c.get("media_path") or ""), ts),
                str(out_png),
            ], check=False)
            if out_png.exists():
                candidates.append((out_png, round(float(c.get("timeline_in_sec", 0.0)) + off, 2), c["clip_id"], c["media_path"], ts))
        if len(candidates) >= 12:
            break

    if not candidates:
        return None, {"score": 0.0, "face_bbox": None, "reason": "extract_failed"}

    man_p = pipeline_dir / "01_manifest.json"
    saliency_by_mp: Dict[str, Dict[str, Any]] = {}
    if man_p.is_file():
        try:
            for it in read_json(man_p).get("items") or []:
                if it.get("normalized_mp4") and isinstance(it.get("saliency_bbox"), dict):
                    saliency_by_mp[str(it["normalized_mp4"])] = it["saliency_bbox"]
        except Exception:
            pass

    def _filter_primary_faces(face_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        kept: List[Dict[str, Any]] = []
        for f in face_list or []:
            bb = f.get("bbox") if isinstance(f, dict) else None
            if not isinstance(bb, dict):
                continue
            bw = float(bb.get("w", 0.0))
            bh = float(bb.get("h", 0.0))
            norm_area = (bw * bh) if max(bw, bh, float(bb.get("x", 0.0)), float(bb.get("y", 0.0))) <= 1.05 else ((bw * bh) / float(CANVAS_WIDTH * CANVAS_HEIGHT))
            if norm_area >= 0.015:
                kept.append(f)
        return kept

    vis_map = inspect_images([p for p, _, _, _, _ in candidates])
    sharps = []
    for p, _, _, _, _ in candidates:
        vr = vis_map.get(str(p), {})
        faces_c = _filter_primary_faces(vr.get("faces") or [])
        with Image.open(p) as im:
            rgb_arr = np.asarray(im.convert("RGB"))
        full_sh = compute_laplacian_sharpness(rgb_arr)
        if faces_c:
            fb = faces_c[0]["bbox"]
            h_i, w_i = rgb_arr.shape[:2]
            x0 = max(0, int(float(fb["x"]) * w_i))
            y0 = max(0, int(float(fb["y"]) * h_i))
            x1 = min(w_i, max(x0 + 16, int((float(fb["x"]) + float(fb["w"])) * w_i)))
            y1 = min(h_i, max(y0 + 16, int((float(fb["y"]) + float(fb["h"])) * h_i)))
            face_sh = compute_laplacian_sharpness(rgb_arr[y0:y1, x0:x1])
            sharps.append(round(face_sh * 0.70 + full_sh * 0.30, 2))
        else:
            sharps.append(full_sh)
    s_min, s_max = min(sharps), max(sharps)

    has_clear_face_cands = any(
        bool(_filter_primary_faces((vis_map.get(str(p), {}) or {}).get("faces") or []))
        for p, _, _, _, _ in candidates
    )

    best_idx = 0
    best_score = -1.0
    best_face: Optional[Dict[str, float]] = None
    best_detail = ""

    for idx, ((p, tl_sec, cid, mp_s, _), sh) in enumerate(zip(candidates, sharps)):
        s_norm = (sh - s_min) / max(1e-4, s_max - s_min) if s_max > s_min else 0.7
        vr = vis_map.get(str(p), {})
        faces = _filter_primary_faces(vr.get("faces") or [])
        if has_clear_face_cands and not faces:
            # 片中有清晰正面人脸帧时，强制剔除无人背影/毛坯空镜，绝不挑工地背影当封面
            continue
        f_bb = None
        if faces:
            f0 = faces[0]
            f_bb = f0.get("bbox")
            eye_r = float(f0.get("eye_open_ratio", 0.24))
            mouth_r = float(f0.get("mouth_open_ratio", 0.15))
            area_f = float(f_bb.get("w", 0.2)) * float(f_bb.get("h", 0.2)) if isinstance(f_bb, dict) else 0.08
            if eye_r < 0.13:
                face_score = 0.15
            elif mouth_r > 0.42:
                face_score = 0.48
            else:
                face_score = min(1.0, 0.72 + (eye_r - 0.13) * 1.8 + min(0.12, area_f))
        else:
            f_bb = saliency_by_mp.get(str(mp_s or ""))
            face_score = 0.55

        comp_score = 0.85
        if f_bb:
            fy_c = float(f_bb["y"]) + float(f_bb["h"]) * 0.5
            if fy_c > 1.0:
                fy_c /= float(CANVAS_HEIGHT)
            comp_score = 1.0 if 0.22 <= fy_c <= 0.65 else 0.55

        total = 0.42 * s_norm + 0.43 * face_score + 0.15 * comp_score
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
            "-frames:v", "1",
            "-vf", f"{vf_grade}," + _cover_vf_with_face(str(chosen_mp), chosen_src_ts),
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


def _cover_vf_with_face(media_path: str, at_sec: float) -> str:
    """封面/首帧的裁切同样以人脸为中心：横屏素材居中裁会把头切掉，封面是最贵的一帧。"""
    try:
        anchor = face_framing.anchor_for(media_path, float(at_sec), 1.0)
    except Exception:
        anchor = None
    return face_framing.cover_crop_vf(CANVAS_WIDTH, CANVAS_HEIGHT, anchor)


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


def _make_attribution_code(pipeline_dir: Path, title_seed: str, style_id: Optional[str] = "") -> str:
    """生成确定性发布复盘归因码 SVS-YYYYMMDD-XXXX。"""
    day_str = datetime.now().strftime("%Y%m%d")
    raw = f"{pipeline_dir.name}|{style_id or ''}|{title_seed or ''}"
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:4].upper()
    return f"SVS-{day_str}-{suffix}"


def write_publish_ops_doc(
    pipeline_dir: Path,
    attribution_code: str,
    preferred_title: str,
    style_id: Optional[str] = None,
    track: str = "store",
) -> Path:
    """输出发布前 3 秒静音三问自测卡与复盘归因码单 `09_publish_ops.md`。"""
    edl_p = pipeline_dir / "04_edl.json"
    sample_json = json.dumps({
        "video_id": attribution_code,
        "attribution_code": attribution_code,
        "track": track,
        "platform": "douyin",
        "traffic_source": "local_feed" if track == "store" else "feed",
        "observation_hours": 48,
        "title": preferred_title[:28],
        "style_id": str(style_id or "style_01"),
        "edl_path": str(edl_p.resolve()),
    }, ensure_ascii=False)
    lines = [
        "# 发布前 3 秒自测卡与复盘归因码",
        "",
        f"- **本次出片归因码**：`{attribution_code}`",
        f"- **推荐主标题**：{preferred_title}",
        "",
        "## 一、发前 3 秒静音三问自测（关掉声音过一遍，只要 15 秒）",
        "1. [ ] **关掉声音看前 3 秒**：不听解说词，单看开场画面和顶部大字，路人能不能一秒看懂你在讲什么事？",
        "2. [ ] **小图清不清楚**：把 `09_cover_3x4.jpg` 缩到手机列表大小（或看 `09_ui_safe_preview.jpg`），人脸和封面字有没有被平台按钮挡住？",
        "3. [ ] **片尾收住没有**：看到最后一帧时，观众知不知道接下来该点赞、评论还是私信到店？",
        "",
        "## 二、发布后 48 小时复盘填报（把后台数字贴进这一条即可追踪病因）",
        f"- 记录归因码 `{attribution_code}`，后续运行：",
        "```bash",
        f"python3 scripts/content_log_review.py --account <你的账号> --track {track} --add-json '{sample_json}'",
        "```",
        "",
    ]
    out_p = pipeline_dir / "09_publish_ops.md"
    out_p.write_text("\n".join(lines), encoding="utf-8")
    return out_p


def build_publish_copy_pack(
    pipeline_dir: Path,
    title_seed: str,
    hook_text: str,
    sentences: List[Dict[str, Any]],
    keywords: List[str],
    track: str = "store",
    cover_main: str = "",
    cover_sub: str = "",
    has_4k_covers: bool = False,
    attribution_code: str = "",
) -> Dict[str, Any]:
    """
    生成分平台差异化发布文案交付包（09_publish_copy_pack.md/.json）：
    同一份素材按不同平台消费场景重写标题钩子、正文节奏与互动引导，绝不一键群发同质文案。
    """
    clean_hook = re.sub(r"[。！？!?]+$", "", str(hook_text or title_seed or "真实拆解")).strip()
    top_kw = keywords[0] if keywords else clean_hook[:6]
    sec_kw = keywords[1] if len(keywords) > 1 else top_kw
    summary_pts = [
        re.sub(r"[。！？!?]+$", "", str(s.get("text") or "").strip())
        for s in (sentences or [])[:4]
        if str(s.get("text") or "").strip()
    ]
    core_line = summary_pts[1] if len(summary_pts) > 1 else clean_hook

    clean_seed = re.sub(r"[。！？!?]+$", "", str(title_seed or "")).strip()
    primary_dy = (clean_seed or clean_hook)[:28]
    dy_titles = [
        primary_dy,
        f"别猜了，把「{top_kw[:6]}」和「{sec_kw[:6]}」摆在一起看"[:28],
        f"为什么大家总在「{top_kw[:6]}」上吃亏？看完就明白"[:28],
    ]
    xhs_titles = [
        f"收藏备用｜{top_kw[:8]}一次看懂"[:20],
        f"关于{top_kw[:6]}，没人告诉你这步"[:20],
        f"{clean_hook[:18]}（附核对清单）"[:20],
    ]
    bili_titles = [
        f"【深度拆解】{clean_hook[:26]}？从原理到真实细节讲透"[:40],
        f"为什么「{top_kw[:8]}」和直觉相反？用可核验的细节还原真相"[:40],
        f"{title_seed[:24]}：完整过程、关键对比与常见误区"[:40],
    ]

    dy_body = f"{clean_hook[:38]}。{core_line[:44]}。你平时遇到过类似情况吗？评论区聊聊。"[:100]
    xhs_paragraphs = [
        f"很多人问过「{top_kw}」到底怎么判断，今天直接把最关键的细节拆开讲清楚。",
        f"1️⃣ 先看核心现象：{clean_hook}。",
        f"2️⃣ 再看容易忽略的细节：{core_line}。",
        "建议先收藏，下次真用到的时候照着核对一遍，不容易走弯路。",
    ]
    bili_desc = (
        f"{clean_hook}。本期视频按真实细节逐层拆解「{top_kw}」与「{sec_kw}」背后的关键差别，"
        f"并附上可直接核对的实操要点。如果你有不同看法或补充案例，欢迎在评论区讨论。"
    )
    bili_pinned = f"置顶互动：你对「{top_kw}」最在意哪一点？在评论区告诉我，下期优先拆解。"

    tags_dy = "#真实记录 #实用干货 #细节拆解 #日常避坑" if track == "creator" else "#同城探店 #实体店日常 #本地生活 #真实记录"
    tags_xhs = "#涨知识 #实用经验 #生活技巧 #避坑指南 #干货收藏" if track == "creator" else "#本地好店 #同城推荐 #实体店实拍 #门店日常 #真实测评"
    tags_bili = f"#{top_kw} #知识科普 #深度拆解 #实用技巧 #真实体验 #逻辑思维 #避坑指南 #细节控"

    pack = {
        "attribution_code": attribution_code,
        "preferred_title": dy_titles[0],
        "preferred_reason": "第一句直接抛出反常识或具体问题，不绕弯子，信息密度最高且正文可直接兑现。",
        "platforms": {
            "douyin_shipinhao": {"titles": dy_titles, "copy": dy_body, "hashtags": tags_dy, "limit_note": "标题 <=28字，正文 <=100字"},
            "xiaohongshu": {"titles": xhs_titles, "paragraphs": xhs_paragraphs, "hashtags": tags_xhs, "limit_note": "标题 <=20字，分段易收藏"},
            "bilibili": {
                "titles": bili_titles,
                "description": bili_desc,
                "pinned_comment": bili_pinned,
                "category_hint": "知识 / 生活实录" if track == "creator" else "生活 / 探店纪实",
                "hashtags": tags_bili,
                "limit_note": "标题 <=40字，信息+悬念完整兑现",
            },
        },
        "cover_summary": {
            "main_text": cover_main.replace("\n", " / "),
            "sub_text": cover_sub,
            "has_dual_4k_code_covers": has_4k_covers,
        },
        "fact_checklist": [
            "标题、封面字与视频正文承诺完全一致，没有夸大或标题党",
            "片中出现的数字、价格、时间、出处均已核对准确",
            "示意图或类比已照实说明，未冒充实测结论",
            "已清除其他平台残留词（如小红书不带小黄车口令）",
        ],
    }

    md_lines = [
        f"# 分平台发布文案与封面交付包",
        "",
        *([f"> **复盘归因码**：`{attribution_code}`（发前 3 秒自测见 `09_publish_ops.md`）"] if attribution_code else []),
        f"> **首选推荐标题**：`{pack['preferred_title']}`",
        f"> **推荐理由**：{pack['preferred_reason']}",
        "",
        "## 一、分平台备选标题（按字数上限与场景差异化定制）",
        "",
        "| 平台 | 备选标题 | 规则提示 |",
        "| :--- | :--- | :--- |",
        *[f"| 抖音 / 视频号 | {i}. {t} | ≤28 字，口语直接 |" for i, t in enumerate(dy_titles, 1)],
        *[f"| 小红书 | {i}. {t} | ≤20 字，强调收藏实用 |" for i, t in enumerate(xhs_titles, 1)],
        *[f"| B 站 | {i}. {t} | ≤40 字，信息 + 悬念 |" for i, t in enumerate(bili_titles, 1)],
        "",
        "## 二、各平台可直接复制的发布正文",
        "",
        "### 1. 抖音 / 视频号",
        dy_body,
        "",
        f"**话题**：{tags_dy}",
        "",
        "### 2. 小红书",
        "\n\n".join(xhs_paragraphs),
        "",
        f"**话题**：{tags_xhs}",
        "",
        "### 3. B 站",
        f"**简介**：{bili_desc}",
        "",
        f"**标签**：{tags_bili}",
        f"**建议分区**：{pack['platforms']['bilibili']['category_hint']}",
        f"**置顶评论**：{bili_pinned}",
        "",
        "## 三、封面说明与发布前事实核对",
        f"- **封面主大字**：`{pack['cover_summary']['main_text']}` | **副标签**：`{cover_sub}`",
        (
            "- **4K 代码封面**：`09_cover_land_4k.png`（3840×2160 横版，核心锁中央 4:3）& `09_cover_port_4k.png`（2160×2880 竖版 3:4）"
            if has_4k_covers
            else "- **标准封面**：`09_cover.jpg`（9:16 首帧）& `09_cover_3x4.jpg`（3:4 主页卡片）"
        ),
        "",
        *[f"- [ ] {chk}" for chk in pack["fact_checklist"]],
        "",
    ]
    # 片尾与「下次拍摄补一句」：现成素材最难对上的就是框架最后那一格
    _pk_p = pipeline_dir / "06_packaging.json"
    if _pk_p.is_file():
        try:
            _ep = (read_json(_pk_p).get("ending_plan") or {})
        except Exception:
            _ep = {}
        if _ep:
            _fm = _ep.get("framework_map") or {}
            md_lines += [
                "## 四、片尾与下次拍摄补一句",
                f"- **这次片尾**：{_ep.get('verdict')} —— {_ep.get('reason')}",
                (
                    f"- **五格结构**：缺「{'、'.join(_fm['missing'])}」；"
                    f"有素材的是「{'、'.join(_fm.get('covered') or [])}」"
                    if _fm else "- **五格结构**：未体检"
                ),
                "- **下次拍的时候，末句挑一句这样说**（现在是剪进去的，不改原声）：",
                *[
                    f"  {i + 1}. 【{c.get('type')}】{c.get('text')}"
                    for i, c in enumerate(_ep.get("candidates") or [])
                ],
                "",
            ]
    (pipeline_dir / "09_publish_copy_pack.md").write_text("\n".join(md_lines), encoding="utf-8")
    write_json(pipeline_dir / "09_publish_copy_pack.json", pack)
    return pack


def render_dual_4k_code_covers(
    out_dir: Path,
    kicker: str,
    hero_lines: List[str],
    title: str,
    palette: Optional[Dict[str, str]] = None,
    font_mode: str = "serif",
    with_guides: bool = True,
) -> Dict[str, Any]:
    """用单源码 cover_4k.html + 无头浏览器 2x 缩放渲染横版 4K (3840×2160，核心锁中央 4:3) + 竖版 4K (2160×2880)。"""
    br = detect_browser_renderer()
    runtime_dir = ASSETS_DIR / "canvas_runtime"
    if not br.get("available") or not (runtime_dir / "cover_4k.html").is_file():
        return {"ok": False, "reason": "browser_or_runtime_unavailable", "outputs": []}

    out_dir.mkdir(parents=True, exist_ok=True)
    check_dir = out_dir / "work_checks"
    with tempfile.TemporaryDirectory(prefix="svs_cover4k_") as td:
        tdp = Path(td)
        for fn in ("stage_clock.js", "visual_kit.js", "cover_4k.html"):
            shutil.copy2(runtime_dir / fn, tdp / fn)
        cov_data = {
            "kicker": str(kicker or "")[:16],
            "hero_lines": [str(x)[:8] for x in (hero_lines or [title[:6]])[:2]],
            "title": str(title or "")[:14],
            "font_mode": font_mode,
            "palette": palette or {
                "bg": "#040711",
                "ink": "#f1f5f9",
                "sub": "#94a3b8",
                "accent": "#5ce1e6",
                "accent2": "#f59e0b",
                "alert": "#ff6b57",
            },
        }
        (tdp / "cover_data.js").write_text(
            "window.SVS_COVER_DATA = " + json.dumps(cov_data, ensure_ascii=False, indent=2) + ";\n",
            encoding="utf-8",
        )
        try:
            from knowledge_animator import inject_stage_commercial_fonts
            inject_stage_commercial_fonts(
                tdp,
                extra_text=f"{cov_data['kicker']}{''.join(cov_data['hero_lines'])}{cov_data['title']}",
                html_files=("cover_4k.html",),
            )
        except Exception:
            pass
        cmd = [
            str(br["node_bin"]),
            str(SCRIPTS_DIR / "capture_html_video.mjs"),
            str(tdp),
            "--cover-4k",
            "--page", "cover_4k.html",
            "--out-dir", str(out_dir.resolve()),
            "--name", "09_cover",
            "--check-dir", str(check_dir.resolve()),
        ]
        if with_guides:
            cmd.append("--guides")
        cap_env = dict(os.environ)
        ff = find_bin("ffmpeg")
        if ff:
            cap_env["FFMPEG_BIN"] = ff
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=cap_env)
        if proc.returncode != 0:
            return {"ok": False, "reason": (proc.stderr or proc.stdout)[-300:], "outputs": []}
        try:
            return json.loads(proc.stdout)
        except Exception:
            return {
                "ok": (out_dir / "09_cover_land_4k.png").is_file() and (out_dir / "09_cover_port_4k.png").is_file(),
                "outputs": [
                    str((out_dir / "09_cover_land_4k.png").resolve()),
                    str((out_dir / "09_cover_port_4k.png").resolve()),
                ],
            }


_GBRO_STYLE_CATALOG = {
    "style-01": ("深色渐变风", "style-01-深色渐变风.md", "人物居中，大字覆盖人物后方，深色渐变背景，高对比强冲击"),
    "style-02": ("纯色扁平风", "style-02-纯色扁平风.md", "人物抠图感，置于纯色扁平背景上，干净清爽，主体道具近大远小推向镜头"),
    "style-03": ("产品主视觉风", "style-03-产品主视觉风.md", "产品、菜品或UI占画面60-70%绝对主角，人物偏小做引导手势"),
    "style-04": ("对比卡片风", "style-04-对比卡片风.md", "人物手持两张对比卡片，前景大亮后景小暗推向镜头，适合前后对比/算账拆解"),
    "style-05": ("极简留白风", "style-05-极简留白风.md", "大面积浅色留白超60%，文字是唯一视觉锤，人物轻量出镜偏角落"),
    "style-06": ("海报拼贴风", "style-06-海报拼贴风.md", "前中后三层纵深遮挡拼贴，多张参考图叠加构图，叙事丰富纵深感强"),
    "style-07": ("人物侧置留白风", "style-07-人物侧置留白风.md", "人物偏左或偏右占宽约30%，对侧约65%留白放超大标题，版面大气"),
    "style-08": ("背影构图风", "style-08-背影构图风.md", "人物背对镜头不露脸，望向前方主视觉或远景，制造代入感和想象空间"),
    "style-09": ("局部出镜风", "style-09-局部出镜风.md", "只露手部、半脸或侧脸轮廓，产品或菜品是绝对主角，人物只是见证者"),
    "style-10": ("正面对视风", "style-10-正面对视风.md", "人物正面居中直视镜头建立强眼神接触，文字上下两行夹住脸部不遮眼嘴"),
}

_GBRO_STYLE_SPECS: Dict[str, Dict[str, Any]] = {
    "style-01": {"requires_fig2": False, "recommended_fig2": False, "forbid_front_face": False, "font_mode": "gbro_heavy", "bg_rule": "深色渐变"},
    "style-02": {"requires_fig2": False, "recommended_fig2": False, "forbid_front_face": False, "font_mode": "gbro_heavy", "bg_rule": "单一纯色扁平无渐变"},
    "style-03": {"requires_fig2": True, "recommended_fig2": True, "forbid_front_face": False, "font_mode": "gbro_heavy", "bg_rule": "浅色或中性干净底"},
    "style-04": {"requires_fig2": False, "recommended_fig2": False, "forbid_front_face": False, "font_mode": "gbro_heavy", "bg_rule": "深色渐变衬托两张对比卡"},
    "style-05": {"requires_fig2": False, "recommended_fig2": False, "forbid_front_face": False, "font_mode": "thin_clean", "bg_rule": "浅色极简大留白"},
    "style-06": {"requires_fig2": False, "recommended_fig2": True, "forbid_front_face": False, "font_mode": "editorial_white", "bg_rule": "深色沉稳纵深层"},
    "style-07": {"requires_fig2": False, "recommended_fig2": False, "forbid_front_face": False, "font_mode": "editorial_dark", "bg_rule": "中性或浅色留白"},
    "style-08": {"requires_fig2": False, "recommended_fig2": False, "forbid_front_face": True, "font_mode": "editorial_white", "bg_rule": "大气深色渐变"},
    "style-09": {"requires_fig2": False, "recommended_fig2": True, "forbid_front_face": False, "font_mode": "editorial_dark", "bg_rule": "浅色或中性突出主体"},
    "style-10": {"requires_fig2": False, "recommended_fig2": False, "forbid_front_face": False, "font_mode": "gbro_heavy", "bg_rule": "高饱和纯色或深色渐变"},
}

_GBRO_BANNED_FX = ("全息投影", "粒子效果", "发光特效")


def _norm_media_key(path_or_name: str) -> str:
    name = Path(str(path_or_name or "").replace("\\", "/")).name
    return re.sub(r"^norm_[0-9]+_", "", name)


def extract_content_ref_frames(
    pipeline_dir: Path,
    subject_hint: str = "",
    style_id: Optional[str] = None,
    max_refs: int = 2,
) -> List[Dict[str, Any]]:
    """
    从实拍 B-roll（04_edl.json 优先，回退 01_manifest.json）结合 00_visual_index.json 与 03_audio_events.json
    抽取贴合本片菜品/产品/场景的图2+参考帧（09_content_ref_01.png 等），剔除大面积正面人脸帧避免与图1撞车。
    纯单机口播无实拍 B-roll 时返回 []。
    """
    pipeline_dir = Path(pipeline_dir)
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        return []

    man_p = pipeline_dir / "01_manifest.json"
    edl_p = pipeline_dir / "04_edl.json"
    vi_p = pipeline_dir / "00_visual_index.json"
    ae_p = pipeline_dir / "03_audio_events.json"

    man_items = (read_json(man_p).get("items") or []) if man_p.is_file() else []
    man_by_path: Dict[str, Dict[str, Any]] = {}
    man_by_key: Dict[str, Dict[str, Any]] = {}
    for it in man_items:
        if not isinstance(it, dict):
            continue
        for k in ("normalized_mp4", "source_path"):
            p_raw = str(it.get(k) or "").strip()
            if p_raw:
                man_by_path[p_raw] = it
                man_by_key[_norm_media_key(p_raw)] = it

    vi_clips = (read_json(vi_p).get("clips") or []) if vi_p.is_file() else []
    vi_by_key: Dict[str, Dict[str, Any]] = {}
    for vc in vi_clips:
        if not isinstance(vc, dict):
            continue
        key = _norm_media_key(str(vc.get("filename") or vc.get("file") or vc.get("source_path") or ""))
        if key:
            vi_by_key[key] = vc

    # 汇总候选 B-roll（先取 EDL 实际入片，再用 manifest 补足）
    candidates: List[Dict[str, Any]] = []
    seen_resolved: set[str] = set()

    if edl_p.is_file():
        for c in (read_json(edl_p).get("clips") or []):
            if not isinstance(c, dict) or c.get("track_role") != "b_roll":
                continue
            mp_str = str(c.get("media_path") or "").strip()
            mp = Path(mp_str)
            if not mp_str or not mp.is_file():
                continue
            res_key = str(mp.resolve())
            if res_key in seen_resolved:
                continue
            seen_resolved.add(res_key)
            candidates.append({
                "media_path": mp,
                "t_in": float(c.get("source_in_sec") or 0.0),
                "t_out": float(c.get("source_out_sec") or 0.0),
                "from_edl": True,
            })

    for it in man_items:
        if not isinstance(it, dict):
            continue
        if it.get("role") not in ("b_roll", "b_roll_candidate"):
            continue
        if it.get("usable") is False or it.get("video_grade") == "ng":
            continue
        if it.get("generated") is True or it.get("source") in ("generated", "code", "code_broll"):
            continue
        mp_str = str(it.get("normalized_mp4") or it.get("source_path") or "").strip()
        mp = Path(mp_str)
        if not mp_str or not mp.is_file():
            continue
        res_key = str(mp.resolve())
        if res_key in seen_resolved:
            continue
        seen_resolved.add(res_key)
        dur = float(it.get("duration_sec") or 2.0)
        candidates.append({
            "media_path": mp,
            "t_in": 0.15,
            "t_out": max(0.6, dur - 0.15),
            "from_edl": False,
        })

    if not candidates:
        return []

    query_terms: List[str] = []
    hint_clean = re.sub(r"[\s，。！？、,.!?]+", "", str(subject_hint or "")).strip()
    if len(hint_clean) >= 2:
        query_terms.append(hint_clean)
        for tok in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", hint_clean):
            if tok not in query_terms:
                query_terms.append(tok)
    if ae_p.is_file():
        try:
            for sent in (read_json(ae_p).get("sentences") or [])[:6]:
                for kw in (sent.get("keywords") or []):
                    kws = str(kw).strip()
                    if len(kws) >= 2 and kws not in _CTA_STOP_KWS and kws not in query_terms:
                        query_terms.append(kws)
        except Exception:
            pass

    scored_cands: List[Dict[str, Any]] = []
    for cand in candidates:
        mp: Path = cand["media_path"]
        mkey = _norm_media_key(str(mp))
        man_row = man_by_path.get(str(mp)) or man_by_key.get(mkey) or {}
        vi_row = vi_by_key.get(mkey) or {}
        tags = list(dict.fromkeys(
            str(t).strip()
            for row in (vi_row, man_row)
            for key in ("semantic_tags", "scene_tags_zh", "tags", "manual_tags", "entities")
            for t in (row.get(key) or [])
            if str(t).strip()
        ))
        desc = str(vi_row.get("description") or vi_row.get("summary") or man_row.get("description") or "").strip()
        meta_blob = f"{mkey} {' '.join(tags)} {desc}"

        hint_score = 0.0
        if hint_clean and (hint_clean in meta_blob or any(t in hint_clean for t in tags if len(t) >= 2)):
            hint_score = 120.0
        elif hint_clean:
            hint_toks = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", hint_clean)
            hint_score = 60.0 * sum(1 for tk in hint_toks if tk in meta_blob)

        kw_hits = sum(1 for qt in query_terms if qt in meta_blob)
        sharp = float(man_row.get("sharpness_score") or 40.0)
        faces = man_row.get("faces") or []
        large_face = any(
            isinstance(f, dict)
            and float((f.get("bbox") if isinstance(f.get("bbox"), dict) else f).get("w", 0.0))
            * float((f.get("bbox") if isinstance(f.get("bbox"), dict) else f).get("h", 0.0))
            >= 0.04
            for f in faces
        )
        score = (
            (12.0 if cand["from_edl"] else 0.0)
            + hint_score
            + kw_hits * 18.0
            + min(25.0, sharp * 0.25)
            - (80.0 if large_face else 0.0)
        )
        t_in = float(cand["t_in"])
        t_out = max(t_in + 0.2, float(cand["t_out"]))
        apex = man_row.get("action_apex_sec") or man_row.get("best_in_sec")
        if apex is not None and t_in <= float(apex) <= t_out:
            ts = float(apex)
        else:
            ts = t_in + (t_out - t_in) * 0.45
        scored_cands.append({
            "media_path": mp,
            "time_sec": round(ts, 3),
            "tags": tags,
            "description": desc,
            "score": score,
            "hint_score": hint_score,
        })

    scored_cands.sort(key=lambda x: (-x["score"], x["media_path"].name))

    # 若未指定唯一明确命中的 subject_hint，按 style_id 在优质候选池中做确定性轮换，避免全套封面复用同一盘菜
    top_pool = scored_cands[:min(8, len(scored_cands))]
    if style_id and len(top_pool) >= 2:
        top_has_unique_hint = (
            bool(hint_clean)
            and top_pool[0]["hint_score"] > 0
            and top_pool[0]["score"] - top_pool[1]["score"] >= 50.0
        )
        if not top_has_unique_hint:
            m_sid = re.search(r"(\d+)", str(style_id))
            rot = (int(m_sid.group(1)) - 1) % len(top_pool) if m_sid else 0
            top_pool = top_pool[rot:] + top_pool[:rot]

    rest = [c for c in scored_cands if c not in top_pool]
    ordered = top_pool + rest

    refs_out: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="svs_content_ref_") as td:
        td_p = Path(td)
        extracted: List[Tuple[Path, Dict[str, Any]]] = []
        for idx_c, cand in enumerate(ordered[:min(len(ordered), max_refs + 4)]):
            out_tmp = td_p / f"cref_{idx_c:02d}.png"
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{cand['time_sec']:.3f}",
                "-i", str(cand["media_path"]),
                "-frames:v", "1",
                "-vf", "scale=1080:1440:force_original_aspect_ratio=increase,crop=1080:1440",
                str(out_tmp),
            ]
            res = subprocess.run(cmd, check=False)
            if res.returncode == 0 and out_tmp.is_file():
                extracted.append((out_tmp, cand))

        if not extracted:
            return []

        try:
            vis_map = inspect_images([x[0] for x in extracted])
        except Exception:
            vis_map = {}

        # 若存在不含大脸撞车的实拍帧，优先保留无大脸帧
        non_face_picks: List[Tuple[Path, Dict[str, Any]]] = []
        face_picks: List[Tuple[Path, Dict[str, Any]]] = []
        for fpath, cand in extracted:
            vinfo = vis_map.get(str(fpath)) or vis_map.get(str(fpath.resolve())) or {}
            faces = vinfo.get("faces") or []
            big_face = any(
                float((f.get("bbox") if isinstance(f.get("bbox"), dict) else f).get("w", 0.0))
                * float((f.get("bbox") if isinstance(f.get("bbox"), dict) else f).get("h", 0.0))
                >= 0.06
                for f in faces if isinstance(f, dict)
            )
            if big_face:
                face_picks.append((fpath, cand))
            else:
                non_face_picks.append((fpath, cand))

        final_picks = (non_face_picks or face_picks)[:max(1, int(max_refs))]
        for idx_out, (fpath, cand) in enumerate(final_picks, start=1):
            dest = pipeline_dir / f"09_content_ref_{idx_out:02d}.png"
            shutil.copy2(fpath, dest)
            refs_out.append({
                "path": str(dest.resolve()),
                "source_media": str(cand["media_path"].resolve()),
                "time_sec": cand["time_sec"],
                "tags": list(cand["tags"])[:6],
                "description": cand["description"][:120],
                "score": round(float(cand["score"]), 2),
            })
    return refs_out


def extract_best_host_face_frame(pipeline_dir: Path) -> Optional[Dict[str, Any]]:
    """
    从 A-roll 口播素材中自动筛选主播五官最清晰、角度端正、曝光自然的一帧，
    保存为 pipeline/09_host_face_ref.png，作为 gbro-cover-design 封面制作的图1人脸垫图。
    """
    ffmpeg = find_bin("ffmpeg")
    if not ffmpeg:
        return None
    man_p = pipeline_dir / "01_manifest.json"
    edl_p = pipeline_dir / "04_edl.json"
    aroll_sources: List[Tuple[Path, float, float, List[Dict[str, Any]]]] = []
    seen_paths: set[str] = set()

    if man_p.is_file():
        for it in (read_json(man_p).get("items") or []):
            if it.get("role") != "a_roll" or it.get("usable") is False or it.get("video_grade") == "ng":
                continue
            mp = Path(str(it.get("normalized_mp4") or it.get("source_path") or ""))
            if mp.is_file() and str(mp.resolve()) not in seen_paths:
                seen_paths.add(str(mp.resolve()))
                aroll_sources.append((mp, 0.25, max(0.8, float(it.get("duration_sec") or 4.0) - 0.25), list(it.get("excluded_windows") or [])))
    if not aroll_sources and edl_p.is_file():
        for c in (read_json(edl_p).get("clips") or []):
            if c.get("track_role") != "a_roll":
                continue
            mp = Path(str(c.get("media_path") or ""))
            if mp.is_file() and str(mp.resolve()) not in seen_paths:
                seen_paths.add(str(mp.resolve()))
                aroll_sources.append((mp, float(c.get("source_in_sec") or 0.1), float(c.get("source_out_sec") or 2.0), []))

    if not aroll_sources:
        return None

    from vision_inspector import compute_laplacian_sharpness, inspect_images

    with tempfile.TemporaryDirectory(prefix="svs_host_face_") as td:
        td_p = Path(td)
        cand_items: List[Tuple[Path, Path, float]] = []
        per_clip = max(2, min(6, 14 // max(1, len(aroll_sources))))
        idx_c = 0
        for mp, t_start, t_end, excl in aroll_sources[:4]:
            span = max(0.3, t_end - t_start)
            for k in range(per_clip):
                ts = round(t_start + span * ((k + 0.5) / float(per_clip)), 3)
                if any(float(w.get("start_sec", 0)) <= ts <= float(w.get("end_sec", -1)) for w in excl):
                    continue
                out_f = td_p / f"face_{idx_c:02d}.png"
                res = subprocess.run(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{ts:.3f}", "-i", str(mp), "-frames:v", "1", str(out_f)],
                    check=False,
                )
                if res.returncode == 0 and out_f.is_file():
                    cand_items.append((out_f, mp, ts))
                    idx_c += 1

        if not cand_items:
            return None

        vis_map = inspect_images([c[0] for c in cand_items])
        best_score = -1e9
        best_pick: Optional[Tuple[Path, Path, float, Dict[str, Any]]] = None
        for fpath, src_mp, ts in cand_items:
            with Image.open(fpath) as im:
                rgb = im.convert("RGB")
                w, h = rgb.size
                arr = np.asarray(rgb, dtype=np.float32)
            vinfo = vis_map.get(str(fpath)) or vis_map.get(str(fpath.resolve())) or {}
            faces = vinfo.get("faces") or []
            if faces:
                f0 = max(
                    faces,
                    key=lambda b: float((b.get("bbox") if isinstance(b.get("bbox"), dict) else b).get("w", 0.0))
                    * float((b.get("bbox") if isinstance(b.get("bbox"), dict) else b).get("h", 0.0)),
                )
                bb0 = f0.get("bbox") if isinstance(f0.get("bbox"), dict) else f0
                fx, fy = float(bb0.get("x", 0.3)), float(bb0.get("y", 0.15))
                fw, fh = float(bb0.get("w", 0.25)), float(bb0.get("h", 0.25))
                area = fw * fh
                cx = fx + fw * 0.5
                cy = fy + fh * 0.5
                # 人脸区域裁出算五官拉普拉斯清晰度与亮度
                x1, y1 = max(0, int(fx * w)), max(0, int(fy * h))
                x2, y2 = min(w, int((fx + fw) * w)), min(h, int((fy + fh) * h))
                patch = arr[y1:y2, x1:x2] if (x2 - x1 > 16 and y2 - y1 > 16) else arr
                gray_p = 0.2126 * patch[:, :, 0] + 0.7152 * patch[:, :, 1] + 0.0722 * patch[:, :, 2]
                sharp = float(compute_laplacian_sharpness(gray_p))
                luma = float(np.mean(gray_p))
                # 五官正常度：人脸面积合理(4%~35%)、位置靠中上不贴边、曝光不死黑不惨白
                area_score = 35.0 if 0.04 <= area <= 0.35 else max(0.0, 20.0 - abs(area - 0.14) * 80.0)
                center_score = max(0.0, 25.0 - abs(cx - 0.50) * 55.0 - abs(cy - 0.34) * 35.0)
                edge_ok = 12.0 if (fx > 0.05 and fy > 0.04 and fx + fw < 0.95 and fy + fh < 0.85) else -20.0
                luma_score = 18.0 if 75.0 <= luma <= 205.0 else max(-10.0, 18.0 - abs(luma - 140.0) * 0.2)
                sharp_score = min(45.0, sharp * 0.45)
                score = area_score + center_score + edge_ok + luma_score + sharp_score
                meta = {"has_face": True, "face_bbox": f0, "sharpness": round(sharp, 1), "luma": round(luma, 1)}
            else:
                # 非 macOS 降级：取画面上中半区清晰度 + 亮度作为兜底
                roi = arr[int(h * 0.12):int(h * 0.62), int(w * 0.18):int(w * 0.82)]
                gray_p = 0.2126 * roi[:, :, 0] + 0.7152 * roi[:, :, 1] + 0.0722 * roi[:, :, 2]
                sharp = float(compute_laplacian_sharpness(gray_p))
                luma = float(np.mean(gray_p))
                score = min(35.0, sharp * 0.35) + (15.0 if 75.0 <= luma <= 205.0 else 0.0)
                meta = {"has_face": False, "sharpness": round(sharp, 1), "luma": round(luma, 1)}
            if score > best_score:
                best_score = score
                best_pick = (fpath, src_mp, ts, meta)

        if best_pick is None:
            return None
        ref_out = pipeline_dir / "09_host_face_ref.png"
        shutil.copy2(best_pick[0], ref_out)
        return {
            "path": str(ref_out.resolve()),
            "source_media": str(best_pick[1].resolve()),
            "time_sec": best_pick[2],
            "score": round(best_score, 2),
            **best_pick[3],
        }


def _render_gbro_prompt_text(
    rec_key: str,
    *,
    clean_main: str,
    clean_sub: str,
    sub_line: str,
    subject_desc: str,
    expression: str,
    face_ref: Optional[Dict[str, Any]],
    content_refs: List[Dict[str, Any]],
) -> str:
    """严格对照 references/style-01..10.md 生成贴合风格与参考图的 3:4 中文提示词。"""
    style_cn, _, style_desc = _GBRO_STYLE_CATALOG[rec_key]
    has_fig1 = bool(face_ref)
    has_fig2 = len(content_refs) >= 1
    has_fig3 = len(content_refs) >= 2
    expr = expression or ("自信笃定" if rec_key != "style-08" else "")
    subj = subject_desc or clean_main

    fig1_std = (
        f"参考图1（09_host_face_ref.png）的真实五官特征、发型与神态，保持五官一致性，只要半身，{expr}"
        if has_fig1
        else f"真实亲切的行业主理人半身形象，{expr}"
    )
    fig2_note = (
        f"画面主视觉融入图2（{Path(content_refs[0]['path']).name}）所呈现的「{subj}」真实质感与核心画面"
        if has_fig2
        else f"搭配与「{subj}」相关的具象实物道具"
    )
    fig3_note = (
        f"；补充展示图3（{Path(content_refs[1]['path']).name}）作为次要叙事层次。"
        if has_fig3
        else ""
    )
    gbro_font_desc = (
        f"超粗方正中文黑体无衬线大字「{clean_main}」（纯白粗字带轻微暗色投影），重点关键词「{clean_sub}」使用高饱和亮黄色矩形色块配黑色超粗字体突出，字号巨大、行距紧凑、强对比一眼看清"
    )

    if rec_key == "style-01":
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【前景主体】{fig1_std}，居中偏下站位，双手自然比划或托举{fig2_note}{fig3_note}",
            f"【文字排版】画面中上部采用{gbro_font_desc}，大字覆盖在人物身后被人物和主体部分遮挡形成层次感，前景散点极少主题小元素只做点缀。",
            "【光影与背景】背景采用深灰至炭黑柔和影棚渐变，主光打亮人物正面与道具轮廓，边缘光勾勒肩线，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    elif rec_key == "style-02":
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【前景主体】{fig1_std}，人物呈现干净抠图感，手部将与「{subj}」相关的实物道具或卡片近大远小推向镜头（带有轻微动态模糊透视感）。"
            + (f" 道具界面或主盘清晰展示图2内容{fig3_note}" if has_fig2 else ""),
            f"【文字排版】画面上方中央放置{gbro_font_desc}，文字被前景主体部分遮挡形成立体层次，前景散点极少扁平小图标带轻微投影。",
            "【色彩与背景】背景采用单一低饱和纯净扁平底色（色块均匀统一，整体清爽干净），主体高饱和突出，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    elif rec_key == "style-03":
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【绝对主视觉】参考图2作为画面绝对主视觉，占据画面约 65% 居中偏上展开，清晰锐利展现「{subj}」真实细节与质感{fig3_note}",
            f"【人物引导】{( '参考图1的五官特征，' if has_fig1 else '' )}半身人物体量偏小（约占画面 25%），置于画面右下侧偏下，微笑做引导手势指向图2主视觉方向。",
            f"【文字排版】中文标题「{clean_main}」与「{clean_sub}」采用醒目粗黑体压在主视觉边缘形成层次感，浅色或中性干净背景衬托主角，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    elif rec_key == "style-04":
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【对比双卡】{fig1_std}，人物居中，左手捏着一张偏小、暗淡破旧感的对比卡片置于远景（写着「{clean_sub}」），右手将一张更大、明亮清晰的对比卡片猛地推向镜头前方带轻微动态模糊（写着「{clean_main}」），形成强烈近大远小与一明一暗对比。"
            + (f" 卡片内部主图参考图2展示「{subj}」{fig3_note}" if has_fig2 else ""),
            f"【文字排版】巨大的中文大字「{clean_main}」与亮点标签「{clean_sub}」覆盖在人物背后形成前后遮挡层次。",
            "【光影与背景】深色系影棚背景衬托双卡强烈对比，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    elif rec_key == "style-05":
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【轻量人物】{('参考图1的五官特征，' if has_fig1 else '')}半身体量偏小（约占画面 20%），置于画面左下角，轻量自然神态朝向对侧留白区。",
            f"【极简文字】画面上方与右侧保持超过 60% 大面积浅米白留白，中文标题「{clean_main}」占据留白区主体，采用偏细或中等粗细的干净深色无衬线字体，字间距宽松呼吸感强；下方一行较小深灰副标题「{clean_sub}」作补充"
            + (f"（留白区旁轻量点缀图2微缩元素）" if has_fig2 else "。"),
            "【背景规范】整体浅色极简无多余装饰，构图克制高级，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    elif rec_key == "style-06":
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【三层纵深】前景：将{('图2「' + subj + '」' if has_fig2 else '「' + subj + '」实物')}作为前景大元素置于侧前方带轻微投影与浮动感；中景：{fig1_std}，居于中景与前景元素形成局部遮挡；后景：错落排列 2 至 3 张轻微虚化的相关实拍小卡（{('含图3场景' if has_fig3 else '呼应主题场景')}）作为背景层。",
            f"【文字排版】巨大的中文大字「{clean_main}」与副标「{clean_sub}」采用纯白粗体配暗色描边，压在人物与前景元素之间强化三层纵深遮挡。",
            "【背景规范】深色沉稳背景衬托各层素材，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    elif rec_key == "style-07":
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【侧置人物】{fig1_std}，置于画面左侧占画面宽度约 30%，身体和眼神自然朝向右侧大留白区。",
            f"【对侧留白】画面右侧约 65% 为大面积浅灰暖白留白区，超大深色中文标题「{clean_main}」与补充标签「{clean_sub}」占满留白区（主字大副字小，呼吸距离充足不拥挤）"
            + (f"，下方轻量嵌入图2「{subj}」参考小图。" if has_fig2 else "。"),
            "【背景规范】中性或浅色柔和背景，版面大气对比鲜明，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    elif rec_key == "style-08":
        fig1_back = (
            "参考图1的发型与身形特征，人物背对镜头站在画面中下方，只见后脑、肩膀和背部轮廓，发丝清晰，完全不露脸"
            if has_fig1
            else "人物背对镜头站在画面中下方，只见后脑、肩膀和背部轮廓，完全不露脸"
        )
        front_target = (
            f"人物前方与上方展现图2所呈现的「{subj}」开阔实景与纵深亮光{fig3_note}"
            if has_fig2
            else f"人物前方是通往「{subj}」的开阔纵深空间与温暖远景光影"
        )
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【背影构图】{fig1_back}，微微仰头或迈步向前具有动势感，{front_target}",
            f"【文字排版】巨大的白色中文大字「{clean_main}」与副标「{clean_sub}」位于人物上方开阔空间，字间距宽松，与背景对比清晰。",
            "【光影与背景】大气深色渐变背景营造强烈代入感与纵深感，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    elif rec_key == "style-09":
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【绝对主角】画面主体为「{subj}」，"
            + (f"直接采用图2实拍特写（清晰锐利、细节可读），占据画面约 65%{fig3_note}" if has_fig2 else "以高清特写占据画面约 65%，清晰锐利。"),
            f"【局部出镜】画面右下角边缘仅局部出镜{('参考图1的' if has_fig1 else '')}手部特写或半侧脸轮廓（占画面 15-20%），手指轻触或托举主体，人物只是见证者不抢主角位。",
            f"【文字排版】深色或高对比中文标题「{clean_main}」与标签「{clean_sub}」压在主体上方留白处，浅色中性背景让产品成为绝对焦点，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]
    else:  # style-10
        body = [
            f"【风格定位】{style_cn}（{style_desc}）。",
            f"【正面对视】{fig1_std}，正面居中直视镜头与观众建立强烈眼神接触，手势自然呼应「{subj}」"
            + (f"（脸部侧边极小点缀图2元素{fig3_note}）" if has_fig2 else "。"),
            f"【上下夹字】「{clean_main}」以超大号纯白粗黑体位于人物脸部上方，「{clean_sub}」以高饱和黄底黑字标签位于人物脸部下方，两行文字上下夹住脸部形成自然框架，留足呼吸距离，绝不遮挡眼睛和嘴巴。",
            "【光影与背景】高饱和纯色或深色渐变影棚背景与人物形成高对比，所有关键元素距画面四边至少保留约 10% 安全边距。",
        ]

    raw_prompt = "\n".join(["3:4 竖版构图。", "", *body])
    for banned in _GBRO_BANNED_FX:
        raw_prompt = raw_prompt.replace(banned, "")
    return raw_prompt


def build_gbro_cover_handoff(
    pipeline_dir: Path,
    title_main: str,
    title_sub: str,
    hook_text: str,
    track: str = "store",
    agent_info: Optional[Dict[str, Any]] = None,
    *,
    style_id: Optional[str] = None,
    gbro_style: Optional[str] = None,
    dish_or_subject: str = "",
    dir_plan: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    通用衔接内置 `gbro-cover-design` 10 风格多参考图封面制作技能：
    1. 自动从 A-roll 抽取五官最清晰自然的主播人像帧 (09_host_face_ref.png) 作为图1人脸垫图；
    2. 自动从实拍 B-roll 结合视觉索引抽取贴合本片菜品/产品/场景的内容帧 (09_content_ref_*.png) 作为图2+；
    3. 按 `dir_plan`（`gbro_cover_spec` / `cover_copy`）或内容动态匹配全部 10 套风格模板（含 `style-03` 缺图2安全降级），
       输出标准化 `09_gbro_cover_prompt.md` 与 `09_gbro_cover_handoff.json`。
    """
    pipeline_dir = Path(pipeline_dir)
    gbro_dir = SUBSKILLS_DIR / "gbro-cover-design"
    if not (gbro_dir / "SKILL.md").is_file():
        return None

    if dir_plan is None:
        dp_p = pipeline_dir / "03b_director_plan.json"
        if dp_p.is_file():
            try:
                dir_plan = read_json(dp_p)
            except Exception:
                dir_plan = None

    dp_obj = dir_plan if isinstance(dir_plan, dict) else {}
    gbro_spec = dp_obj.get("gbro_cover_spec") if isinstance(dp_obj.get("gbro_cover_spec"), dict) else {}
    cov_copy = dp_obj.get("cover_copy") if isinstance(dp_obj.get("cover_copy"), dict) else {}

    eff_style_id = style_id or dp_obj.get("style_id")
    eff_subject = str(
        dish_or_subject
        or gbro_spec.get("dish_or_subject")
        or cov_copy.get("dish_or_subject")
        or ""
    ).strip()

    face_ref = extract_best_host_face_frame(pipeline_dir)
    content_refs = extract_content_ref_frames(
        pipeline_dir,
        subject_hint=eff_subject,
        style_id=eff_style_id,
        max_refs=2,
    )

    cand_style = str(
        gbro_style
        or gbro_spec.get("gbro_style")
        or cov_copy.get("gbro_style")
        or ""
    ).strip()
    if cand_style not in _GBRO_STYLE_CATALOG:
        blob = f"{title_main} {title_sub} {hook_text}"
        m_num = re.search(r"(\d+)", str(eff_style_id or ""))
        idx_num = int(m_num.group(1)) if m_num else 1
        if any(k in blob for k in ("对比", "VS", "差价", "算账", "成本", "真相", "元", "块", "破产")):
            cand_style = "style-04"
        elif content_refs:
            pool_styles = ("style-03", "style-06", "style-09", "style-01")
            cand_style = pool_styles[(idx_num - 1) % len(pool_styles)]
        elif track == "store":
            cand_style = "style-01"
        else:
            cand_style = "style-10"

    fallback_from: Optional[str] = None
    if cand_style == "style-03" and not content_refs:
        fallback_from = "style-03"
        cand_style = "style-01" if track == "store" else "style-10"

    rec_key = cand_style
    style_cn, style_file, _ = _GBRO_STYLE_CATALOG[rec_key]
    style_ref_path = gbro_dir / "references" / style_file

    clean_main = str(gbro_spec.get("headline_top") or cov_copy.get("headline_top") or title_main).replace("\n", "").strip()[:10] or "核心真相"
    clean_sub = str(gbro_spec.get("highlight_badge") or cov_copy.get("highlight_badge") or title_sub).strip()[:12] or "实拍干货"
    sub_line = str(gbro_spec.get("sub_line") or cov_copy.get("sub_line") or "").strip()[:14]
    if not eff_subject:
        first_tags = content_refs[0].get("tags") if content_refs else []
        eff_subject = str(first_tags[0]) if first_tags else clean_main

    prompt_text = _render_gbro_prompt_text(
        rec_key,
        clean_main=clean_main,
        clean_sub=clean_sub,
        sub_line=sub_line,
        subject_desc=eff_subject,
        expression=str(gbro_spec.get("expression") or "").strip(),
        face_ref=face_ref,
        content_refs=content_refs,
    )

    output_3x4_target = pipeline_dir / "09_gbro_i2i_cover_3x4.png"
    prompt_md = pipeline_dir / "09_gbro_cover_prompt.md"
    fig2_md_lines = (
        [f"- **图 {idx + 2}（内容实拍参考图）**：`{ref['path']}`（标签：{', '.join(ref.get('tags') or []) or '实拍主视觉'}）" for idx, ref in enumerate(content_refs)]
        if content_refs
        else ["- **图 2+（内容实拍参考图）**：当前项目为纯口播单机镜头，无额外实拍 B-roll 参考图"]
    )
    prompt_md.write_text(
        "\n".join([
            "# gbro-cover-design 多参考图封面制作交接单",
            "",
            f"- **画幅要求**：`3:4` 竖版（目标输出：`09_gbro_i2i_cover_3x4.png`）",
            f"- **内置技能入口**：`subskills/gbro-cover-design/SKILL.md`",
            f"- **推荐封面风格**：`{rec_key}` · `{style_cn}`（模板：`subskills/gbro-cover-design/references/{style_file}`）",
            f"- **核心主体/菜品**：`{eff_subject}`",
            f"- **图 1（人脸参考垫图）**：`{face_ref['path']}`" if face_ref else "- **图 1（人脸参考垫图）**：当前项目无出镜 A-roll，可跳过图1或由用户补充个人照",
            *fig2_md_lines,
            "",
            "## 可直接用于生图模型的 3:4 竖版提示词",
            "",
            "```text",
            prompt_text,
            "```",
            "",
            "> 提醒：生图完成后请逐字核对画面中文标题是否有错字或多笔少笔。",
            "",
        ]),
        encoding="utf-8",
    )

    ref_order = []
    if face_ref:
        ref_order.append("figure_1_face_ref")
    for idx in range(len(content_refs)):
        ref_order.append(f"figure_2_content_refs[{idx}]")

    handoff: Dict[str, Any] = {
        "enabled": True,
        "schema_version": "v12.1",
        "agent": (agent_info or {}).get("agent", "antigravity"),
        "signal": (agent_info or {}).get("signal", "universal"),
        "aspect_ratio": "3:4",
        "subskill_skill_md": str((gbro_dir / "SKILL.md").resolve()),
        "recommended_style_id": rec_key,
        "recommended_style_name": style_cn,
        "recommended_style_template": str(style_ref_path.resolve()) if style_ref_path.is_file() else None,
        "gbro_style": rec_key,
        "dish_or_subject": eff_subject,
        "figure_1_face_ref": face_ref,
        "figure_2_content_refs": content_refs,
        "has_broll_content_refs": bool(content_refs),
        "output_path": str(output_3x4_target.resolve()),
        "recommended_lovart_params": {
            "aspect_ratio": "3:4",
            "preferred_models": ["seedream/seedream-5-5", "openai/gpt-image-2", "vertex/nano-banana-2-1"],
            "ref_image_order": ref_order,
        },
        "prompt_md": str(prompt_md.resolve()),
        "prompt_text": prompt_text,
    }
    if fallback_from:
        handoff["style_fallback_from"] = fallback_from
        handoff["style_fallback_reason"] = "style-03 requires mandatory figure_2_content_refs"

    write_json(pipeline_dir / "09_gbro_cover_handoff.json", handoff)
    print(f"[step9] 已抽取多参考图(图1={bool(face_ref)}, 图2+={len(content_refs)})并生成 {rec_key} 封面提示词 -> {prompt_md.name}")
    return handoff


def run_step9(
    pipeline_dir: Path,
    track: str = "store",
    style_id: Optional[str] = None,
    code_cover_4k: bool = False,
    agent: Optional[str] = None,
) -> Dict[str, Any]:
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

    dir_plan_p = pipeline_dir / "03b_director_plan.json"
    dir_plan = read_json(dir_plan_p) if dir_plan_p.is_file() else {}
    dir_acts = list(dir_plan.get("acts") or [])
    cov_spec = dir_plan.get("cover_copy") if isinstance(dir_plan.get("cover_copy"), dict) else None
    is_unified_s02 = (eff_style == "style_02") and (
        str(dir_plan.get("theme_profile") or "") == "style_02_unified" or dir_plan_p.is_file()
    )
    if cov_spec and (cov_spec.get("headline_top") or cov_spec.get("highlight_badge")):
        cover_main_l1 = str(cov_spec.get("headline_top") or cover_main_l1).strip()[:10]
        cover_main_l2 = str(cov_spec.get("highlight_badge") or cover_main_l2).strip()[:10]
        raw_sub = str(cov_spec.get("sub_line") or "").strip()
        cover_sub = raw_sub[:14] if raw_sub else (
            f"粤语实拍 · {cover_main_l2[:7]}" if track == "store" else f"实操复盘 · {cover_main_l2[:7]}"
        )
    elif dir_acts:
        act0 = dir_acts[0].get("visual_copy") or {}
        act1 = dir_acts[1].get("visual_copy") or {} if len(dir_acts) > 1 else {}
        a0_items = [str(x).strip() for x in (act0.get("items") or []) if str(x).strip()]
        a0_hl = str(act0.get("headline") or "").strip()
        a1_hl = str(act1.get("headline") or "").strip()
        # 自动拼接饱满短句（严禁出现「那年」「六百」单薄二字残片）
        cand_pairs = [x for x in [a0_hl, *a0_items, a1_hl] if len(x) >= 3]
        if len(a0_hl) == 2 and a0_items and len(a0_hl + a0_items[0]) <= 8:
            cand_pairs.insert(0, f"{a0_hl}·{a0_items[0]}")
        num_item = next((x for x in cand_pairs if any(ch.isdigit() for ch in x)), "")
        cover_main_l1 = (num_item or (cand_pairs[0] if cand_pairs else cover_main_l1))[:9]
        sub_pool = [x for x in cand_pairs if x and x != cover_main_l1 and x not in cover_main_l1]
        if sub_pool:
            cover_main_l2 = sub_pool[0][:9]
        elif len(hook_parts) >= 2 and hook_parts[1] != cover_main_l1:
            cover_main_l2 = hook_parts[1][:9]
        sec_clean = re.sub(r"^(不是|但是|而且|所以)", "", str(cover_main_l2 or "")).strip() or "核心真相"
        cover_sub = (
            f"粤语实拍 · {sec_clean[:7]}" if track == "store" else f"实操复盘 · {sec_clean[:7]}"
        )
    else:
        # 无总谱时亦禁用 2 字碎片复读，确保封面双行表达完整人话
        if len(cover_main_l1) <= 3 and len(hook_parts) >= 1 and len(hook_parts[0]) >= 4:
            cover_main_l1 = hook_parts[0][:9]
        if len(cover_main_l2) <= 3 or cover_main_l2 == cover_main_l1:
            cand_b = next((hp[:9] for hp in hook_parts[1:] if len(hp) >= 4 and hp[:9] != cover_main_l1), "")
            cover_main_l2 = cand_b or (sent2_clean[:9] if len(sent2_clean) >= 4 else "一线真实全拆解")

    titles = [str(t).rstrip("。") for t in titles]
    cover_main = f"{cover_main_l1}\n{cover_main_l2}"
    cover_path = pipeline_dir / "09_cover.jpg"
    cover_3x4_path = pipeline_dir / "09_cover_3x4.jpg"
    ui_preview_path = pipeline_dir / "09_ui_safe_preview.jpg"

    gbro_i2i_raw = str(dir_plan.get("gbro_cover_3x4_path") or "").strip()
    gbro_i2i_p: Optional[Path] = Path(gbro_i2i_raw).expanduser() if gbro_i2i_raw else None
    if not (gbro_i2i_p and gbro_i2i_p.is_file()):
        for cand_name in ("09_gbro_i2i_cover_3x4.png", "09_gbro_i2i_cover_3x4.jpg"):
            cp = pipeline_dir / cand_name
            if cp.is_file():
                gbro_i2i_p = cp
                break
        else:
            gbro_i2i_p = None

    person_cutout_applied = False
    person_mask_img: Optional[Image.Image] = None
    best_meta: Dict[str, Any] = {"score": 0.96, "detail": "gbro_i2i_cover", "graded_matched": True}
    cover_mode = "template_composite"

    if gbro_i2i_p is not None:
        # V11 真图生图 gbro-cover-design 封面直出路径：保持原图文字与人像零二次压字污染，
        # 标准输出 3:4 (1080x1440) 并柔化上下延展成 9:16 (1080x1920) 完整海报。
        cover_mode = "gbro_i2i_generated"
        with Image.open(gbro_i2i_p) as src_im:
            src_rgb = src_im.convert("RGB")
        sw, sh = src_rgb.size
        target_ratio = 3.0 / 4.0
        cur_ratio = sw / float(max(1, sh))
        if abs(cur_ratio - target_ratio) > 0.02:
            if cur_ratio > target_ratio:
                nw = int(round(sh * target_ratio))
                x0 = max(0, (sw - nw) // 2)
                src_rgb = src_rgb.crop((x0, 0, x0 + nw, sh))
            else:
                nh = int(round(sw / target_ratio))
                y0 = max(0, (sh - nh) // 2)
                src_rgb = src_rgb.crop((0, y0, sw, y0 + nh))
        crop_3x4 = src_rgb.resize((CANVAS_WIDTH, 1440), Image.Resampling.LANCZOS)
        crop_3x4.save(cover_3x4_path, quality=94)

        # 9:16 满屏首帧延展：分别取 3:4 原图最顶/最底 12px 纯净边缘条垂直柔化拉伸，杜绝顶部标题字变成白雾残影
        top_strip = crop_3x4.crop((0, 0, CANVAS_WIDTH, 12)).resize((CANVAS_WIDTH, 280), Image.Resampling.BICUBIC)
        top_strip = top_strip.filter(ImageFilter.GaussianBlur(radius=18))
        bot_strip = crop_3x4.crop((0, 1440 - 12, CANVAS_WIDTH, 1440)).resize((CANVAS_WIDTH, 280), Image.Resampling.BICUBIC)
        bot_strip = bot_strip.filter(ImageFilter.GaussianBlur(radius=18))
        bg_916 = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT))
        bg_916.paste(top_strip, (0, 0))
        bg_916.paste(crop_3x4, (0, 240))
        bg_916.paste(bot_strip, (0, 1640))
        composed_rgba = bg_916.convert("RGBA")
        # 给 3:4 上下边缘加 28px 柔羽化 Alpha 完美融入延展底
        alpha_mask = Image.new("L", (CANVAS_WIDTH, 1440), 255)
        am_draw = ImageDraw.Draw(alpha_mask)
        fade_h = 28
        for i_f in range(fade_h):
            val = int(round(255.0 * (i_f + 1) / float(fade_h)))
            am_draw.line([(0, i_f), (CANVAS_WIDTH, i_f)], fill=val)
            am_draw.line([(0, 1440 - 1 - i_f), (CANVAS_WIDTH, 1440 - 1 - i_f)], fill=val)
        fg_34 = crop_3x4.convert("RGBA")
        fg_34.putalpha(alpha_mask)
        composed_rgba.alpha_composite(fg_34, dest=(0, 240))
        composed = composed_rgba.convert("RGB")
        composed.save(cover_path, quality=94)

        fit_sub = cover_sub
        card_y = 260
        thumb_eval_box = (80, 260, 1000, 760)
        font_path = find_commercial_font(["AlimamaShuHeiTi-Bold.otf", "SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf"])
    else:
        with tempfile.TemporaryDirectory(prefix="svs_cover_") as td:
            td_p = Path(td)
            best_frame_p, best_meta = pick_best_cover_frame(
                edl, pipeline_dir, td_p, color_plan=color_plan, audio_events=audio_events
            )
            if best_frame_p and best_frame_p.exists():
                base_img = Image.open(best_frame_p).convert("RGBA")
                # 决策 2A：若存在人脸主体且本机支持 macOS 原生 Apple Vision 前景人像分割，预提取人物前景蒙版用于主体压字纵深海报
                if best_meta.get("face_bbox"):
                    mask_p = td_p / "person_mask.png"
                    if segment_person_mask(best_frame_p, mask_p):
                        try:
                            with Image.open(mask_p) as m_im:
                                m_l = m_im.convert("L").resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.BILINEAR)
                                m_arr = np.asarray(m_l, dtype=np.float32)
                                cov = float(np.mean(m_arr >= 128.0))
                                if 0.08 <= cov <= 0.65:
                                    person_mask_img = m_l
                        except Exception:
                            person_mask_img = None
            else:
                base_img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), hex_to_rgba(pal["bg_hex"], 255))

        cover_rules = load_typography_cover_rules()
        layouts_cfg = cover_rules.get("cover_3x4_layouts") or {}
        style_entry = load_style_spectrum_entry(eff_style) if eff_style else None
        cover_layout_id = str(((style_entry or {}).get("title_cover_layout") or {}).get("layout_id") or "")
        use_gbro_poster = (
            bool(cov_spec)
            or dir_plan_p.is_file()
            or eff_style == "style_02"
            or cover_layout_id not in ("four_corner_editorial",)
        )
        use_jianying_poster = (not use_gbro_poster) and (cover_layout_id == "jianying_poster_4zone")
        sub_spec = (style_entry or {}).get("subtitle_spec") or {}
        style_font_cands = sub_spec.get("font_candidates") or pal.get("font_candidates")
        if use_gbro_poster or is_unified_s02:
            # 狗哥爆款封面：超粗无衬线方正黑体，硬朗高点击视觉冲击
            style_font_cands = ["AlimamaShuHeiTi-Bold.otf", "SourceHanSansCN-Bold.otf", "NotoSansSC-Bold.ttf"]
        kw_hex_style = "#FFE135" if use_gbro_poster else str(sub_spec.get("keyword_color") or pal.get("accent_hex") or "#FFBF17")

        card_x1, card_x2 = 80, SAFE_ZONE["x_max"] - 20  # [80, 860] -> w=780
        card_h = 340
        card_y = _choose_cover_card_y(best_meta.get("face_bbox"), card_h=card_h)

        scrim_layer = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        mid_layer = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        scrim_draw = ImageDraw.Draw(scrim_layer)
        font_path = find_commercial_font(style_font_cands)

        if use_gbro_poster:
            # gbro-cover-design 统一爆款封面：深色柔焦渐变背景 + 清晰正脸人物立体前置 + 上方超粗白字 + 高饱和亮黄警示色块黑字（10% 四边安全区）
            if person_mask_img is not None:
                # 有人像抠像：轻微压暗背景并柔和边缘，突出前景真人立体质感
                dim_bg = ImageEnhance.Brightness(base_img.convert("RGB")).enhance(0.68)
                dim_bg = dim_bg.filter(ImageFilter.GaussianBlur(radius=2.8))
                base_rgb = dim_bg.convert("RGBA")
                fg_p = base_img.copy()
                fg_p.putalpha(person_mask_img)
                base_img = Image.alpha_composite(base_rgb, fg_p)
                person_cutout_applied = True
            for gy in range(0, 620, 4):
                a = int(175 * (1.0 - gy / 620.0))
                scrim_draw.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 18, a))
            for gy in range(1120, CANVAS_HEIGHT, 4):
                a = int(195 * min(1.0, (gy - 1120) / 480.0))
                scrim_draw.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 18, a))

            safe_x = 104  # >= 约10% 左安全区，右不超过 SAFE_ZONE["x_max"] (880)
            safe_w = SAFE_ZONE["x_max"] - safe_x - 16
            fb_raw = best_meta.get("face_bbox")
            face_top_px = 9999.0
            face_bot_px = 0.0
            if isinstance(fb_raw, dict):
                rfy = float(fb_raw.get("y", 0.5))
                rfh = float(fb_raw.get("h", 0.18))
                is_norm = max(rfy, rfh, float(fb_raw.get("w", 0.1))) <= 1.05
                face_top_px = rfy * CANVAS_HEIGHT if is_norm else rfy
                face_bot_px = face_top_px + (rfh * CANVAS_HEIGHT if is_norm else rfh)

            # 1) 上半部超粗纯白主标题（紧守 [256, 1680] 3:4 窗且绝不压额头眉眼）
            f_l1, fit_l1 = _fit_text(draw, cover_main_l1, font_path, 96, safe_w, min_size=50)
            l1_bb = draw.textbbox((0, 0), fit_l1, font=f_l1, stroke_width=6)
            l1_h = max(68, int(l1_bb[3] - l1_bb[1]))
            l1_y = 264
            if face_top_px < 460:
                l1_y = max(248, min(286, int(face_top_px) - l1_h - 14))
            draw.text((safe_x + 4, l1_y + 6), fit_l1, fill=(0, 0, 0, 235), font=f_l1, stroke_width=6, stroke_fill=(0, 0, 0, 235))
            draw.text((safe_x, l1_y), fit_l1, fill=(255, 255, 255, 255), font=f_l1, stroke_width=6, stroke_fill=(0, 0, 0, 255))

            # 2) 高饱和亮黄色 #FFE135 警示牌矩形色块 + 超粗纯黑大字（头顶宽裕放标题下方，大头近景落胸前下方绝不遮脸）
            f_l2, fit_l2 = _fit_text(draw, cover_main_l2, font_path, 76, safe_w - 48, min_size=40)
            l2_bb = draw.textbbox((0, 0), fit_l2, font=f_l2)
            l2_tw = int(l2_bb[2] - l2_bb[0])
            l2_th = max(56, int(l2_bb[3] - l2_bb[1]))
            pill_w = min(safe_w, l2_tw + 52)
            pill_h = l2_th + 30
            if face_top_px >= l1_y + l1_h + pill_h + 44:
                pill_y = l1_y + l1_h + 24
            else:
                pill_y = max(int(face_bot_px) + 34 if face_bot_px > 0 else 1180, 1210)
                pill_y = min(1450 - pill_h, pill_y)
            draw.rounded_rectangle(
                [safe_x + 4, pill_y + 5, safe_x + pill_w + 4, pill_y + pill_h + 5],
                radius=12, fill=(0, 0, 0, 180),
            )
            draw.rounded_rectangle(
                [safe_x, pill_y, safe_x + pill_w, pill_y + pill_h],
                radius=12, fill=(255, 225, 53, 255),
            )
            draw.text(
                (safe_x + pill_w // 2, pill_y + pill_h // 2 - int(l2_bb[1] // 3)),
                fit_l2, fill=(14, 14, 18, 255), font=f_l2, anchor="mm",
            )

            fit_sub = cover_sub
            if cover_sub and face_top_px < 560 and pill_y >= 1150:
                f_sub_p, fit_sub = _fit_text(draw, cover_sub, font_path, 38, safe_w, min_size=24)
                sub_y = max(pill_y - 54, int(face_bot_px) + 12) if pill_y - int(face_bot_px) >= 92 else pill_y + pill_h + 18
                if sub_y + 42 <= 1540:
                    draw.text((safe_x + 2, sub_y + 2), fit_sub, fill=(0, 0, 0, 220), font=f_sub_p, stroke_width=4, stroke_fill=(0, 0, 0, 220))
                    draw.text((safe_x, sub_y), fit_sub, fill=(245, 245, 248, 255), font=f_sub_p, stroke_width=4, stroke_fill=(0, 0, 0, 255))

            card_y = l1_y
            thumb_eval_box = (
                safe_x - 8,
                min(l1_y, pill_y) - 8,
                min(860, max(safe_x + 360, safe_x + max(int(l1_bb[2] - l1_bb[0]), pill_w) + 16)),
                max(l1_y + l1_h, pill_y + pill_h) + 12,
            )
        elif use_jianying_poster:
            # 四区高低错落无闷框爆款封面（底图压暗渐变留在主体图层下方，保人物清晰）
            for gy in range(0, 560, 4):
                a = int(125 * (1.0 - gy / 560.0))
                scrim_draw.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 16, a))
            for gy in range(1060, CANVAS_HEIGHT, 4):
                a = int(145 * min(1.0, (gy - 1060) / 520.0))
                scrim_draw.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 16, a))

            safe_w = SAFE_ZONE["x_max"] - 80  # 800px，严格在 [80, 880] 内不触碰右侧栏
            # 起始 y=262，严格落在 3:4 主页封面裁切 [240, 1680] 内部，绝不齐头切字！
            lead_small = "门店真实记录" if track == "store" else "实操真相拆解"
            f_lead, fit_lead = _fit_text(draw, lead_small, font_path, 46, 420, min_size=28)
            draw.text((88, 262), fit_lead, fill=(255, 255, 255, 255), font=f_lead, stroke_width=5, stroke_fill=(0, 0, 0, 255))

            f_l1, fit_l1 = _fit_text(draw, cover_main_l1, font_path, 106, safe_w - 20, min_size=54)
            draw.text((84 + 4, 326 + 6), fit_l1, fill=(0, 0, 0, 220), font=f_l1, stroke_width=7, stroke_fill=(0, 0, 0, 220))
            draw.text((84, 326), fit_l1, fill=(255, 42, 36, 255), font=f_l1, stroke_width=7, stroke_fill=(0, 0, 0, 255))

            # 中左区：微倾斜明黄黑边贴纸字（cover_main_l2），严格转换归一化 face_bbox 到像素坐标并钳制右边界 <= 868
            f_l2, fit_l2 = _fit_text(draw, cover_main_l2, font_path, 80, safe_w - 80, min_size=42)
            l2_tmp = Image.new("RGBA", (safe_w + 80, 180), (0, 0, 0, 0))
            l2_d = ImageDraw.Draw(l2_tmp)
            l2_d.text((28 + 3, 42 + 4), fit_l2, fill=(0, 0, 0, 220), font=f_l2, stroke_width=6, stroke_fill=(0, 0, 0, 220))
            l2_d.text((28, 42), fit_l2, fill=(255, 226, 52, 255), font=f_l2, stroke_width=6, stroke_fill=(0, 0, 0, 220))
            l2_rot = l2_tmp.rotate(4.5, resample=Image.Resampling.BICUBIC, expand=True)
            l2_bb = l2_rot.getbbox()
            if l2_bb:
                l2_rot = l2_rot.crop(l2_bb)
            max_rot_w = SAFE_ZONE["x_max"] - 84 - 12
            if l2_rot.width > max_rot_w:
                sc_r = max_rot_w / float(l2_rot.width)
                l2_rot = l2_rot.resize((max(1, int(l2_rot.width * sc_r)), max(1, int(l2_rot.height * sc_r))), Image.Resampling.LANCZOS)
            fb_raw = best_meta.get("face_bbox")
            face_y_px = 9999.0
            face_bottom_px = 0.0
            if isinstance(fb_raw, dict):
                raw_fy = float(fb_raw.get("y", 0.5))
                raw_fh = float(fb_raw.get("h", 0.16))
                face_y_px = raw_fy * CANVAS_HEIGHT if max(raw_fy, raw_fh) <= 1.01 else raw_fy
                face_bottom_px = face_y_px + (raw_fh * CANVAS_HEIGHT if max(raw_fy, raw_fh) <= 1.01 else raw_fh)
            l2_y = 1010 if face_y_px < 860.0 else 760
            if face_bottom_px > 0:
                l2_y = max(l2_y, int(face_bottom_px) + 28)
            l2_y = max(500, min(1210 - l2_rot.height, l2_y))

            l2_put_front = False
            if person_mask_img is not None:
                lx1 = max(0, min(CANVAS_WIDTH - 1, 84))
                ly1 = max(0, min(CANVAS_HEIGHT - 1, int(l2_y)))
                lx2 = max(lx1 + 1, min(CANVAS_WIDTH, 84 + l2_rot.width))
                ly2 = max(ly1 + 1, min(CANVAS_HEIGHT, int(l2_y) + l2_rot.height))
                l2_mask_roi = np.asarray(person_mask_img, dtype=np.float32)[ly1:ly2, lx1:lx2]
                if l2_mask_roi.size > 0 and float(np.mean(l2_mask_roi >= 128.0)) > 0.15:
                    l2_put_front = True
            if l2_put_front:
                overlay.alpha_composite(l2_rot, dest=(84, l2_y))
                draw = ImageDraw.Draw(overlay)
            else:
                mid_layer.alpha_composite(l2_rot, dest=(84, l2_y))

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

            card_y = 262
            l1_w = int(draw.textbbox((0, 0), fit_l1, font=f_l1, stroke_width=7)[2])
            thumb_eval_box = (80, 256, min(860, max(380, 88 + l1_w + 24)), 460)
        elif cover_layout_id == "top_giant_bottom_sub":
            # 顶部胶囊标签 + 顶部大号主标题 + 底部双行副标构图
            grad = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            gd = ImageDraw.Draw(grad)
            for gy in range(200, 620, 4):
                a = int(185 * (1.0 - abs(gy - 360) / 260.0))
                gd.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 18, max(0, a)))
            for gy in range(1180, 1520, 4):
                a = int(185 * (1.0 - abs(gy - 1340) / 180.0))
                gd.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 18, max(0, a)))
            overlay = Image.alpha_composite(overlay, grad)
            draw = ImageDraw.Draw(overlay)

            safe_w = SAFE_ZONE["x_max"] - 80
            kw_rgba = hex_to_rgba(kw_hex_style, 255)
            if contrast_ratio(kw_rgba[:3], (10, 12, 18)) < 5.5:
                kw_rgba = (255, 214, 55, 255)
            f_sub, fit_sub = _fit_text(draw, cover_sub, font_path, 36, safe_w - 80, min_size=22)
            sub_bb = draw.textbbox((0, 0), fit_sub, font=f_sub)
            cap_w = min(safe_w, max(240, int(sub_bb[2] - sub_bb[0]) + 56))
            cap_x = 84
            draw.rounded_rectangle([cap_x, 262, cap_x + cap_w, 322], radius=16, fill=(183, 28, 28, 240))
            draw.text((cap_x + cap_w // 2, 292), fit_sub, fill=(255, 255, 255, 255), font=f_sub, anchor="mm")

            f_l1, fit_l1 = _fit_text(draw, cover_main_l1, font_path, 104, safe_w - 20, min_size=52)
            draw.text((84 + 4, 344 + 5), fit_l1, fill=(0, 0, 0, 235), font=f_l1, stroke_width=7, stroke_fill=(0, 0, 0, 235))
            draw.text((84, 344), fit_l1, fill=kw_rgba, font=f_l1, stroke_width=7, stroke_fill=(0, 0, 0, 255))

            f_l2, fit_l2 = _fit_text(draw, cover_main_l2, font_path, 78, safe_w - 20, min_size=42)
            draw.text((84 + 3, 1280 + 4), fit_l2, fill=(0, 0, 0, 235), font=f_l2, stroke_width=5, stroke_fill=(0, 0, 0, 235))
            draw.text((84, 1280), fit_l2, fill=(255, 255, 255, 255), font=f_l2, stroke_width=5, stroke_fill=(0, 0, 0, 255))
            card_y = 262
            l1_w = int(draw.textbbox((0, 0), fit_l1, font=f_l1, stroke_width=7)[2])
            thumb_eval_box = (80, 258, min(860, max(380, 88 + max(cap_w, l1_w) + 24)), 475)
        elif cover_layout_id == "four_corner_editorial":
            # 四角留白 / 黑底画框高定构图封面
            grad = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
            gd = ImageDraw.Draw(grad)
            gd.rectangle([0, 0, CANVAS_WIDTH, 475], fill=(8, 10, 14, 215))
            gd.rectangle([0, 1210, CANVAS_WIDTH, CANVAS_HEIGHT], fill=(8, 10, 14, 220))
            gd.rectangle([68, 252, SAFE_ZONE["x_max"] - 12, 1456], outline=(255, 245, 137, 200), width=3)
            overlay = Image.alpha_composite(overlay, grad)
            draw = ImageDraw.Draw(overlay)

            safe_w = SAFE_ZONE["x_max"] - 110
            f_l1, fit_l1 = _fit_text(draw, cover_main_l1, font_path, 96, safe_w, min_size=48)
            draw.text((96 + 3, 286 + 4), fit_l1, fill=(0, 0, 0, 235), font=f_l1, stroke_width=6, stroke_fill=(0, 0, 0, 235))
            draw.text((96, 286), fit_l1, fill=(255, 245, 137, 255), font=f_l1, stroke_width=6, stroke_fill=(0, 0, 0, 255))
            f_l2, fit_l2 = _fit_text(draw, cover_main_l2, font_path, 68, safe_w, min_size=36)
            draw.text((96, 1252), fit_l2, fill=(255, 255, 255, 255), font=f_l2, stroke_width=5, stroke_fill=(0, 0, 0, 245))
            f_sub, fit_sub = _fit_text(draw, cover_sub, font_path, 36, safe_w, min_size=22)
            draw.text((96, 1348), fit_sub, fill=(255, 245, 137, 240), font=f_sub, stroke_width=3, stroke_fill=(0, 0, 0, 230))
            card_y = 276
            l1_w = int(draw.textbbox((0, 0), fit_l1, font=f_l1, stroke_width=6)[2])
            thumb_eval_box = (84, 268, min(860, max(380, 96 + l1_w + 28)), 445)
        elif cover_layout_id == "left_right_split":
            lr_zones = (layouts_cfg.get("left_right_split") or {}).get("zones") or {}
            hl_z = lr_zones.get("left_headline_col") or {"x": 80, "y": 330, "max_w": 520, "size": 88, "color": "#FFBF17"}
            aux_z = lr_zones.get("left_aux_points") or {"x": 84, "y": 540, "max_w": 480, "size": 36, "color": "#FFFFFF"}
            ban_z = lr_zones.get("bottom_banner") or {"x": 80, "y": 1270, "max_w": 760, "size": 68, "color": "#FFFFFF"}

            for gx in range(0, 620, 6):
                a = int(190 * (1.0 - gx / 620.0))
                scrim_draw.rectangle([gx, 210, gx + 6, 1140], fill=(10, 12, 18, max(0, a)))
            for gy in range(1140, 1490, 4):
                scrim_draw.rectangle([0, gy, CANVAS_WIDTH, gy + 4], fill=(10, 12, 18, 180))

            # 归一化 face_bbox -> 含额头上方安全余量的像素避让框，防止副标胶囊压中眉眼
            padded_face: Optional[Dict[str, float]] = None
            fb_raw = best_meta.get("face_bbox")
            if isinstance(fb_raw, dict):
                fx = float(fb_raw.get("x", 0.0))
                fy = float(fb_raw.get("y", 0.0))
                fw = float(fb_raw.get("w", 0.0))
                fh = float(fb_raw.get("h", 0.0))
                if max(fx, fy, fw, fh) <= 1.01:
                    fx, fy, fw, fh = fx * CANVAS_WIDTH, fy * CANVAS_HEIGHT, fw * CANVAS_WIDTH, fh * CANVAS_HEIGHT
                if fw > 10 and fh > 10:
                    head_pad_top = max(60.0, fh * 0.36)
                    padded_face = {
                        "x": fx - 24.0,
                        "y": fy - head_pad_top,
                        "w": fw + 48.0,
                        "h": fh + head_pad_top + 24.0,
                    }

            hl_col = hex_to_rgba(str(hl_z.get("color") or kw_hex_style), 255)
            if contrast_ratio(hl_col[:3], (10, 12, 18)) < 5.0:
                hl_col = (255, 214, 55, 255)
            hx = int(hl_z.get("x", 80))
            hy = int(hl_z.get("y", 330))
            hw = min(int(hl_z.get("max_w", 520)), SAFE_ZONE["x_max"] - hx - 20)
            f_l1, fit_l1 = _fit_text(draw, cover_main_l1, font_path, int(hl_z.get("size", 88)), hw, min_size=42)
            l1_bb_pre = draw.textbbox((hx, hy), fit_l1, font=f_l1, stroke_width=6)
            l1_h_pre = max(60, int(l1_bb_pre[3] - l1_bb_pre[1]))
            l1_box_pre = {"x": float(hx), "y": float(hy), "w": float(max(120, l1_bb_pre[2] - l1_bb_pre[0])), "h": float(l1_h_pre)}
            if padded_face and bbox_iou(l1_box_pre, padded_face) > 0.0:
                hy = max(258, min(hy, int(padded_face["y"] - l1_h_pre - 12)))
            draw.text((hx + 3, hy + 4), fit_l1, fill=(0, 0, 0, 235), font=f_l1, stroke_width=6, stroke_fill=(0, 0, 0, 235))
            draw.text((hx, hy), fit_l1, fill=hl_col, font=f_l1, stroke_width=6, stroke_fill=(0, 0, 0, 255))

            bx = int(ban_z.get("x", 80))
            by = int(ban_z.get("y", 1270))
            ax = int(aux_z.get("x", 84))
            ay = int(aux_z.get("y", 540))
            aw = min(int(aux_z.get("max_w", 480)), SAFE_ZONE["x_max"] - ax - 20)
            f_sub, fit_sub = _fit_text(draw, cover_sub, font_path, int(aux_z.get("size", 36)), aw - 32, min_size=22)
            sub_bb = draw.textbbox((0, 0), fit_sub, font=f_sub)
            cap_w = min(aw, max(200, int(sub_bb[2] - sub_bb[0]) + 40))
            if padded_face and bbox_iou({"x": float(ax), "y": float(ay), "w": float(cap_w), "h": 58.0}, padded_face) > 0.0:
                left_gap = int(padded_face["x"]) - 12 - ax
                if left_gap >= 280:
                    aw = min(aw, left_gap)
                    f_sub, fit_sub = _fit_text(draw, cover_sub, font_path, int(aux_z.get("size", 36)), aw - 32, min_size=20)
                    sub_bb = draw.textbbox((0, 0), fit_sub, font=f_sub)
                    cap_w = min(aw, max(200, int(sub_bb[2] - sub_bb[0]) + 40))
                else:
                    # 居中大头像：自动让位至下方暗角横幅正上方，绝不压脸/眉眼
                    ax = bx
                    ay = max(int(padded_face["y"] + padded_face["h"] + 24), by - 78)
                    aw = min(int(ban_z.get("max_w", 760)), SAFE_ZONE["x_max"] - ax - 20)
                    f_sub, fit_sub = _fit_text(draw, cover_sub, font_path, int(aux_z.get("size", 36)), aw - 32, min_size=22)
                    sub_bb = draw.textbbox((0, 0), fit_sub, font=f_sub)
                    cap_w = min(aw, max(200, int(sub_bb[2] - sub_bb[0]) + 40))

            draw.rounded_rectangle([ax, ay, ax + cap_w, ay + 58], radius=12, fill=(22, 26, 36, 230), outline=hl_col, width=2)
            draw.text((ax + cap_w // 2, ay + 29), fit_sub, fill=(255, 255, 255, 255), font=f_sub, anchor="mm")

            bw_max = min(int(ban_z.get("max_w", 760)), SAFE_ZONE["x_max"] - bx - 12)
            f_l2, fit_l2 = _fit_text(draw, cover_main_l2, font_path, int(ban_z.get("size", 68)), bw_max, min_size=36)
            draw.text((bx + 3, by + 4), fit_l2, fill=(0, 0, 0, 235), font=f_l2, stroke_width=5, stroke_fill=(0, 0, 0, 235))
            draw.text((bx, by), fit_l2, fill=(255, 255, 255, 255), font=f_l2, stroke_width=5, stroke_fill=(0, 0, 0, 255))
            card_y = hy
            l1_bb = draw.textbbox((hx, hy), fit_l1, font=f_l1, stroke_width=6)
            thumb_eval_box = (
                max(0, min(hx, int(l1_bb[0]))),
                max(0, min(hy, int(l1_bb[1]))),
                min(860, max(380, int(l1_bb[2]) + 24)),
                min(SAFE_ZONE["y_max"], max(hy + 140, int(l1_bb[3]) + 24)),
            )
        else:
            # 通用杂志卡片封套
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
            l1_w = int(draw.textbbox((0, 0), fit_l1, font=f_l1)[2])
            l2_w = int(draw.textbbox((0, 0), fit_l2, font=f_l2)[2])
            thumb_eval_box = (card_x1, card_y, min(card_x2, max(card_x1 + 320, inner_x + max(cap_w, l1_w, l2_w) + 28)), card_y + card_h)

        stage_bg = Image.alpha_composite(base_img, scrim_layer)
        if use_jianying_poster:
            # 纵深合成：压暗底图 -> 中景贴纸字 -> (可选)前景人像边缘主体压字(重叠<=35%) -> 顶/底主标题清晰保底
            stage = Image.alpha_composite(stage_bg, mid_layer)
            if person_mask_img is not None:
                tx1, ty1, tx2, ty2 = (
                    max(0, min(CANVAS_WIDTH - 1, int(thumb_eval_box[0]))),
                    max(0, min(CANVAS_HEIGHT - 1, int(thumb_eval_box[1]))),
                    max(1, min(CANVAS_WIDTH, int(thumb_eval_box[2]))),
                    max(1, min(CANVAS_HEIGHT, int(thumb_eval_box[3]))),
                )
                title_region_mask = np.asarray(person_mask_img, dtype=np.float32)[ty1:ty2, tx1:tx2]
                fg_overlap = float(np.mean(title_region_mask >= 128.0)) if title_region_mask.size > 0 else 0.0
                if fg_overlap <= 0.35:
                    fg_person = base_img.copy()
                    fg_person.putalpha(person_mask_img)
                    stage = Image.alpha_composite(stage, fg_person)
                    person_cutout_applied = True
            composed = Image.alpha_composite(stage, overlay).convert("RGB")
        else:
            composed = Image.alpha_composite(stage_bg, overlay).convert("RGB")
        composed.save(cover_path, quality=92)

        crop_3x4 = composed.crop((0, 240, CANVAS_WIDTH, 1680))
        crop_3x4.save(cover_3x4_path, quality=92)

    _render_ui_safe_preview(composed, ui_preview_path)

    # 360px 手机信息流真实缩略对照图（左侧单列流 360px 首帧 + 右侧双列瀑布流 176px 主页封面，一眼看清手机上字清不清）
    feed_preview_path = pipeline_dir / "09_cover_360px_feed.jpg"
    try:
        feed_canvas = Image.new("RGB", (600, 720), (18, 20, 26))
        fd = ImageDraw.Draw(feed_canvas)
        f_lbl = get_cached_font(font_path, 20)
        card_916 = composed.resize((320, 568), Image.Resampling.LANCZOS)
        card_34 = composed.crop((0, 240, CANVAS_WIDTH, 1680)).resize((180, 240), Image.Resampling.LANCZOS)
        feed_canvas.paste(card_916, (24, 56))
        feed_canvas.paste(card_34, (382, 56))
        fd.text((24, 22), "手机单列全屏预览 (320px)", fill=(226, 232, 240), font=f_lbl)
        fd.text((382, 22), "双列瀑布流预览 (180px)", fill=(250, 204, 21), font=f_lbl)
        fd.text((382, 316), "一眼辨字检查：", fill=(226, 232, 240), font=f_lbl)
        fd.text((382, 348), "不放大能看清大字吗？", fill=(148, 163, 184), font=f_lbl)
        fd.text((382, 380), "主体有没有被切掉？", fill=(148, 163, 184), font=f_lbl)
        feed_canvas.save(feed_preview_path, quality=92)
    except Exception:
        pass

    # 180px 缩略图真实像素对比度与清晰度验收（直接从最终合成像素采样，消灭硬编码黑白 21:1 假检）
    thumb_180 = composed.resize((180, 320))
    tx0 = max(0, min(176, int(thumb_eval_box[0] * 180 / CANVAS_WIDTH)))
    ty0 = max(0, min(316, int(thumb_eval_box[1] * 320 / CANVAS_HEIGHT)))
    tx1_t = max(tx0 + 4, min(180, int(thumb_eval_box[2] * 180 / CANVAS_WIDTH)))
    ty1_t = max(ty0 + 4, min(320, int(thumb_eval_box[3] * 320 / CANVAS_HEIGHT)))
    thumb_box = (tx0, ty0, tx1_t, ty1_t)
    thumb_patch = np.asarray(thumb_180.crop(thumb_box), dtype=np.float32)
    thumb_luma = 0.2126 * thumb_patch[:, :, 0] + 0.7152 * thumb_patch[:, :, 1] + 0.0722 * thumb_patch[:, :, 2]
    thumb_luma_std = float(np.std(thumb_luma))
    # 实测文字墨迹区域前后景相对亮度极端对比 (p96 vs p08)
    lin_l = np.clip(thumb_luma / 255.0, 0.0, 1.0)
    lin_l = np.where(lin_l <= 0.03928, lin_l / 12.92, ((lin_l + 0.055) / 1.055) ** 2.4)
    measured_cover_wcag = round(float((np.percentile(lin_l, 96) + 0.05) / (np.percentile(lin_l, 8) + 0.05)), 2)
    thumb_180_legible_ok = bool(measured_cover_wcag >= 3.8 and thumb_luma_std >= 18.0)

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
    dual_4k_res: Optional[Dict[str, Any]] = None
    if code_cover_4k:
        dual_4k_res = render_dual_4k_code_covers(
            pipeline_dir,
            kicker=fit_sub,
            hero_lines=[cover_main_l1, cover_main_l2],
            title=titles[0][:12] if titles else cover_main_l1,
            palette={
                "bg": "#05070d",
                "ink": "#f1f5f9",
                "sub": "#94a3b8",
                "accent": pal.get("accent_hex", "#5ce1e6"),
                "accent2": "#f59e0b",
                "alert": "#ff6b57",
            },
        )
    has_4k = bool(dual_4k_res and dual_4k_res.get("ok"))
    attr_code = _make_attribution_code(pipeline_dir, titles[0] if titles else hook_clean, eff_style)
    copy_pack = build_publish_copy_pack(
        pipeline_dir,
        title_seed=titles[0] if titles else hook_clean,
        hook_text=hook_clean,
        sentences=sentences,
        keywords=keywords,
        track=track,
        cover_main=cover_main,
        cover_sub=fit_sub,
        has_4k_covers=has_4k,
        attribution_code=attr_code,
    )
    ops_path = write_publish_ops_doc(
        pipeline_dir,
        attribution_code=attr_code,
        preferred_title=copy_pack["preferred_title"],
        style_id=eff_style,
        track=track,
    )

    # 全环境与全部 24 套风格默认产出 gbro-cover-design 交接资产（09_host_face_ref.png + 09_content_ref_*.png + 09_gbro_cover_prompt.md）
    agent_info = detect_active_agent(agent)
    _dp_gbro = dir_plan.get("gbro_cover_spec") if isinstance(dir_plan.get("gbro_cover_spec"), dict) else {}
    _dp_cov = dir_plan.get("cover_copy") if isinstance(dir_plan.get("cover_copy"), dict) else {}
    gbro_handoff: Optional[Dict[str, Any]] = build_gbro_cover_handoff(
        pipeline_dir,
        title_main=cover_main_l1,
        title_sub=cover_main_l2,
        hook_text=hook_clean,
        track=track,
        agent_info=agent_info,
        style_id=eff_style,
        gbro_style=str(_dp_gbro.get("gbro_style") or _dp_cov.get("gbro_style") or "").strip() or None,
        dish_or_subject=str(_dp_gbro.get("dish_or_subject") or _dp_cov.get("dish_or_subject") or "").strip(),
        dir_plan=dir_plan,
    )

    out = {
        "step": "09_title_cover",
        "attribution_code": attr_code,
        "titles": titles,
        "cover_main": cover_main,
        "cover_sub": fit_sub,
        "hashtags": hashtags,
        "best_frame_meta": best_meta,
        "card_y": card_y,
        "thumb_180_legible_ok": thumb_180_legible_ok,
        "measured_cover_wcag": measured_cover_wcag,
        "thumb_180_contrast_ratio": measured_cover_wcag,
        "person_cutout_applied": person_cutout_applied,
        "cover_mode": cover_mode,
        "gbro_cover_source": str(gbro_i2i_p.resolve()) if gbro_i2i_p is not None else None,
        "cover_jpg": str(cover_path.resolve()),
        "cover_3x4_jpg": str(cover_3x4_path.resolve()),
        "ui_safe_preview_jpg": str(ui_preview_path.resolve()),
        "feed_360px_preview_jpg": str(feed_preview_path.resolve()) if feed_preview_path.is_file() else None,
        "publish_copy_pack_md": str((pipeline_dir / "09_publish_copy_pack.md").resolve()),
        "publish_copy_pack_json": str((pipeline_dir / "09_publish_copy_pack.json").resolve()),
        "publish_ops_md": str(ops_path.resolve()),
        "dual_4k_covers": dual_4k_res,
        "preferred_title": copy_pack["preferred_title"],
        "gbro_cover_handoff": gbro_handoff,
    }
    write_json(pipeline_dir / "09_title_cover.json", out)
    print(f"[step9] 黄金封面(9:16+3:4)与分平台文案包已生成 (选帧分 {best_meta.get('score', 0)}) -> 09_cover.jpg")
    return out


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 9: 黄金封面帧优选与去套话标题")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--style-id", help="绑定视觉模板 ID")
    parser.add_argument("--code-cover-4k", action="store_true", help="同时用代码绘制横版 16:9 + 竖版 3:4 双比例 4K 封面")
    parser.add_argument("--agent", default=None, help="可选：显式指定运行智能体（如 cola 自动调用内置 gbro-cover-design）")
    args = parser.parse_args()
    run_step9(Path(args.pipeline_dir), track=args.track, style_id=args.style_id, code_cover_4k=args.code_cover_4k, agent=args.agent)


if __name__ == "__main__":
    main()
