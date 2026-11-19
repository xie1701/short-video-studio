#!/usr/bin/env python3
"""
成片渲染器：合成 1080x1920 30fps BT.709 竖屏成片 MP4 (output_preview.mp4 / output_final.mp4)。
严格对齐流水线权威 Schema：
- 01_manifest.json: items[*], normalized_mp4, faces[*].bbox ([x1,y1,x2,y2] 0..1 归一化), eye_anchor
- 04_edl.json: clips[*], timeline_in_frame/timeline_out_frame, vf_extra, ken_burns ({zoom_start, zoom_end})
- 05_subtitles.json: entries[*], final_bbox/bbox, suppressed
- 06_packaging.json: visual_elements[*], kind, text_or_asset, start_frame/end_frame, final_bbox/bbox
- 07_color_plan.json: clip_plans[*], apply_color_grade, vf_filter (完整 eq+colorbalance+lut3d)
- 08_mix_metrics.json: master_wav (08_mixed_master.wav)
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    BT709_FFMPEG_ARGS,
    BT709_VF_SUFFIX,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    SAFE_ZONE,
    SUBTITLE_SAFE_ZONE,
    compute_crop_transform,
    ffprobe_media,
    find_bin,
    find_commercial_font,
    frames_to_sec,
    get_wav_duration_sec,
    load_style_applied,
    load_style_spectrum_entry,
    load_subtitle_templates,
    maybe_reexec_in_venv,
    parse_video_stream_geometry,
    read_json,
    run_cmd,
    sec_to_frames,
)

maybe_reexec_in_venv()

from PIL import Image, ImageDraw, ImageFont


_PIL_FONT_CACHE: Dict[Tuple[str, int], Any] = {}
_TOFU_SIG_CACHE: Dict[Tuple[str, int], bytes] = {}
_MISSING_FONT_WARNED = False


def parse_hex_rgba(hex_str: str, default_alpha: int = 255) -> Tuple[int, int, int, int]:
    s = str(hex_str or "#FFFFFF").strip().lstrip("#")
    if len(s) == 6:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(default_alpha))
    if len(s) == 8:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16))
    return (255, 255, 255, int(default_alpha))


def _get_cached_font(font_path: Optional[str], size: int) -> Any:
    key = (font_path or "__default__", int(size))
    if key in _PIL_FONT_CACHE:
        return _PIL_FONT_CACHE[key]
    fnt = None
    if font_path and Path(font_path).exists():
        try:
            fnt = ImageFont.truetype(font_path, int(size))
        except Exception:
            fnt = None
    if fnt is None:
        global _MISSING_FONT_WARNED
        fb = find_commercial_font(verified_only=True)
        if fb and Path(fb).exists():
            try:
                fnt = ImageFont.truetype(fb, int(size))
            except Exception:
                fnt = None
    if fnt is None:
        if not _MISSING_FONT_WARNED:
            sys.stderr.write("[render_mp4] WARN: 未找到可用 TrueType 商业字体，回退 Pillow 默认字体\n")
            _MISSING_FONT_WARNED = True
        fnt = ImageFont.load_default()
    _PIL_FONT_CACHE[key] = fnt
    return fnt


def _render_glyph_bytes(font: Any, ch: str) -> bytes:
    img = Image.new("L", (48, 48), 0)
    ImageDraw.Draw(img).text((4, 4), ch, font=font, fill=255)
    return img.tobytes()


def _char_is_tofu(font: Any, font_key: str, ch: str) -> bool:
    if ch.isspace():
        return False
    sz = int(getattr(font, "size", 32) or 32)
    sig_key = (font_key, sz)
    if sig_key not in _TOFU_SIG_CACHE:
        _TOFU_SIG_CACHE[sig_key] = _render_glyph_bytes(font, "\ufffe")
    if ch in ("\ufffe", "\ufffd"):
        return True
    ch_bytes = _render_glyph_bytes(font, ch)
    if not any(ch_bytes):
        return True
    return ch_bytes == _TOFU_SIG_CACHE[sig_key]


def compute_subtitle_place_xy(img_w: int, img_h: int, tpl: Dict[str, Any]) -> Tuple[int, int]:
    """在唯一 SUBTITLE_SAFE_ZONE 内计算默认居中放置坐标。"""
    y_center = int(tpl.get("y_center", 1360))
    px = max(SUBTITLE_SAFE_ZONE["x_min"], min((CANVAS_WIDTH - img_w) // 2, SUBTITLE_SAFE_ZONE["x_max"] - img_w))
    py = max(SUBTITLE_SAFE_ZONE["y_min"], min(y_center - img_h // 2, SUBTITLE_SAFE_ZONE["y_max"] - img_h))
    return int(px), int(py)


def render_subtitle_png(
    entry: Dict[str, Any],
    tpl: Dict[str, Any],
    font_path: Optional[str],
    out_png: Path,
) -> Tuple[int, int]:
    """按模板样式与字词高亮信息渲染透明字幕 PNG（严格尊重 stroke_width=0）。"""
    words = entry.get("words") or []
    raw_text = str(entry.get("text") or "").strip()
    if not raw_text and words:
        raw_text = "".join(str(w.get("word", "")) for w in words)

    orig_fs = max(24, int(tpl.get("font_size", 58)))
    orig_stroke_w = int(tpl.get("stroke_width", 4))
    kw_scale = float(tpl.get("keyword_scale", 1.15))

    max_line_w = SUBTITLE_SAFE_ZONE["x_max"] - SUBTITLE_SAFE_ZONE["x_min"] - 40
    lines_text = raw_text.splitlines() if "\n" in raw_text else [raw_text]
    if not lines_text:
        lines_text = [""]

    base_sz = orig_fs
    probe_img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe_img)
    while base_sz > 28:
        fnt_probe = _get_cached_font(font_path, int(round(base_sz * kw_scale)))
        widest = max(
            probe_draw.textbbox((0, 0), ln, font=fnt_probe)[2] - probe_draw.textbbox((0, 0), ln, font=fnt_probe)[0]
            for ln in lines_text
        )
        if widest <= max_line_w:
            break
        base_sz -= 2

    stroke_w = 0 if orig_stroke_w <= 0 else max(1, int(round(orig_stroke_w * max(0.75, base_sz / float(orig_fs)))))
    primary_rgba = parse_hex_rgba(tpl.get("primary_color", "#FFFFFF"))
    kw_rgba = parse_hex_rgba(tpl.get("keyword_color", "#FFE066"))
    stroke_rgba = parse_hex_rgba(tpl.get("stroke_color", "#0D1117"))
    shadow_rgba = parse_hex_rgba(tpl.get("shadow_color", "#000000A8"), 168)
    bg_hex = str(tpl.get("bg_box", "none"))
    has_bg = bool(bg_hex and bg_hex.lower() != "none")

    fallback_font = None
    if tpl.get("font_fallback"):
        fallback_font = find_commercial_font(tpl.get("font_fallback"), verified_only=True)
    if not fallback_font:
        fallback_font = find_commercial_font(verified_only=True)

    kw_set = set(str(k) for k in (entry.get("keywords") or []))

    tokens: List[Tuple[str, bool, bool]] = []
    if words and "".join(str(w.get("word", "")) for w in words).replace("\n", "") == raw_text.replace("\n", ""):
        raw_idx = 0
        for w_obj in words:
            w_str = str(w_obj.get("word", ""))
            is_kw = bool(w_obj.get("is_keyword", False)) or (w_str in kw_set)
            for ch in w_str:
                while raw_idx < len(raw_text) and raw_text[raw_idx] == "\n":
                    tokens.append(("\n", False, True))
                    raw_idx += 1
                tokens.append((ch, is_kw, False))
                raw_idx += 1
        while raw_idx < len(raw_text):
            if raw_text[raw_idx] == "\n":
                tokens.append(("\n", False, True))
            else:
                tokens.append((raw_text[raw_idx], False, False))
            raw_idx += 1
    else:
        kw_flags = [False] * len(raw_text)
        for kw in kw_set:
            if not kw:
                continue
            start = 0
            while True:
                idx = raw_text.find(kw, start)
                if idx < 0:
                    break
                for j in range(idx, idx + len(kw)):
                    kw_flags[j] = True
                start = idx + len(kw)
        for ch, flag in zip(raw_text, kw_flags):
            tokens.append((ch, flag, ch == "\n"))

    lines: List[List[Tuple[str, Any, Tuple[int, int, int, int], int, int]]] = [[]]
    line_heights: List[int] = [0]
    for ch, is_kw, is_nl in tokens:
        if is_nl:
            lines.append([])
            line_heights.append(0)
            continue
        ch_sz = int(round(base_sz * (kw_scale if is_kw else 1.0)))
        fnt = _get_cached_font(font_path, ch_sz)
        fk = font_path or "__default__"
        if _char_is_tofu(fnt, fk, ch) and fallback_font and fallback_font != font_path:
            fnt = _get_cached_font(fallback_font, ch_sz)
        bb = probe_draw.textbbox((0, 0), ch, font=fnt, stroke_width=stroke_w)
        gw = max(4, bb[2] - bb[0])
        gh = max(8, bb[3] - bb[1])
        rgba = kw_rgba if is_kw else primary_rgba
        lines[-1].append((ch, fnt, rgba, gw, ch_sz))
        line_heights[-1] = max(line_heights[-1], gh)

    line_widths = [sum(item[3] + 2 for item in ln) - (2 if ln else 0) for ln in lines]
    pad_x = 28 if has_bg else 16
    pad_y = 18 if has_bg else 12
    line_gap = 10 if len(lines) > 1 else 0
    total_w = max(80, min(SUBTITLE_SAFE_ZONE["x_max"] - SUBTITLE_SAFE_ZONE["x_min"], max(line_widths or [60]) + pad_x * 2))
    total_h = max(48, sum(max(base_sz, h) for h in line_heights) + line_gap * max(0, len(lines) - 1) + pad_y * 2)

    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    if has_bg:
        bg_rgba = parse_hex_rgba(bg_hex, 195)
        draw.rounded_rectangle([(0, 0), (total_w - 1, total_h - 1)], radius=18, fill=bg_rgba)

    cur_y = pad_y
    use_shadow = bool(tpl.get("shadow", True))
    for ln_idx, ln in enumerate(lines):
        ln_w = line_widths[ln_idx]
        ln_h = max(base_sz, line_heights[ln_idx])
        cur_x = (total_w - ln_w) // 2
        for ch, fnt, rgba, gw, ch_sz in ln:
            y_off = cur_y + max(0, ln_h - ch_sz) // 2
            if use_shadow:
                draw.text(
                    (cur_x + 2, y_off + 3),
                    ch,
                    font=fnt,
                    fill=shadow_rgba,
                    stroke_width=stroke_w,
                    stroke_fill=shadow_rgba if stroke_w > 0 else None,
                )
            draw.text(
                (cur_x, y_off),
                ch,
                font=fnt,
                fill=rgba,
                stroke_width=stroke_w,
                stroke_fill=stroke_rgba if stroke_w > 0 else None,
            )
            cur_x += gw + 2
        cur_y += ln_h + line_gap

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)
    return total_w, total_h


def _resolve_packaging_palette(style_id: str) -> Dict[str, Tuple[int, int, int, int]]:
    spec = load_style_spectrum_entry(style_id) or {}
    pal = spec.get("palette") or {}
    if pal.get("bg_hex") and pal.get("accent_hex"):
        return {
            "bg": parse_hex_rgba(pal["bg_hex"], 232),
            "accent": parse_hex_rgba(pal["accent_hex"], 255),
            "text": parse_hex_rgba(pal.get("text_hex", "#FFFFFF"), 255),
            "border": parse_hex_rgba(pal.get("border_hex", pal["accent_hex"]), 220),
        }
    archetype = str(spec.get("archetype") or "").lower()
    if any(k in archetype for k in ("极简", "高端", "杂志", "冷调", "科技")):
        return {
            "bg": (18, 22, 29, 232),
            "accent": (90, 200, 250, 255),
            "text": (248, 250, 252, 255),
            "border": (90, 200, 250, 190),
        }
    if any(k in archetype for k in ("复古", "烟火", "日常", "探店")):
        return {
            "bg": (34, 24, 18, 234),
            "accent": (255, 169, 64, 255),
            "text": (255, 248, 235, 255),
            "border": (255, 169, 64, 210),
        }
    return {
        "bg": (15, 23, 42, 232),
        "accent": (250, 204, 21, 255),
        "text": (248, 250, 252, 255),
        "border": (250, 204, 21, 200),
    }


def _fit_line(draw: Any, text: str, font_path: Optional[str], max_w: int, start_sz: int, min_sz: int = 24) -> Tuple[Any, str]:
    txt = str(text or "").strip()
    if not txt:
        return _get_cached_font(font_path, start_sz), ""
    for sz in range(start_sz, min_sz - 1, -2):
        fnt = _get_cached_font(font_path, sz)
        bb = draw.textbbox((0, 0), txt, font=fnt)
        if (bb[2] - bb[0]) <= max_w:
            return fnt, txt
    fnt = _get_cached_font(font_path, min_sz)
    while len(txt) > 2:
        bb = draw.textbbox((0, 0), txt + "…", font=fnt)
        if (bb[2] - bb[0]) <= max_w:
            return fnt, txt + "…"
        txt = txt[:-1]
    return fnt, txt


def render_packaging_png(
    elem: Dict[str, Any],
    style_id: str = "style_01",
    color_plan: Optional[Dict[str, Any]] = None,
    font_candidates: Optional[List[str]] = None,
) -> Image.Image:
    """渲染视觉包装 PNG（兼容 06_packaging.json: kind/text_or_asset 及通用字段）。"""
    del color_plan
    pal = _resolve_packaging_palette(style_id)
    font_path = find_commercial_font(font_candidates)
    elem_kind = str(elem.get("kind") or elem.get("type") or "flower_text")
    main_text = str(elem.get("text_or_asset") or elem.get("text") or "").strip()
    max_safe_w = SAFE_ZONE["x_max"] - SAFE_ZONE["x_min"]

    if elem_kind == "hook_title_card":
        line1 = main_text or "核心重点"
        line2 = str(elem.get("subtext") or "").strip()
        w = min(max_safe_w, int((elem.get("final_bbox") or elem.get("bbox") or {}).get("w") or max_safe_w))
        h = 196 if line2 else 132
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=24, fill=pal["bg"], outline=pal["border"], width=3)
        draw.rounded_rectangle([(18, 22), (30, h - 22)], radius=6, fill=pal["accent"])
        f1, t1 = _fit_line(draw, line1, font_path, w - 76, 52, 30)
        draw.text((52, 26), t1, font=f1, fill=pal["text"])
        if line2:
            f2, t2 = _fit_line(draw, line2, font_path, w - 76, 34, 22)
            draw.text((52, 102), t2, font=f2, fill=pal["accent"])
        return img

    text = main_text or "重点"
    probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(probe)
    fnt, fitted = _fit_line(pdraw, text, font_path, max_safe_w - 64, 42, 24)
    bb = pdraw.textbbox((0, 0), fitted, font=fnt)
    tw, th = max(40, bb[2] - bb[0]), max(28, bb[3] - bb[1])
    w = min(max_safe_w, tw + 68)
    h = max(72, th + 32)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=18, fill=pal["bg"], outline=pal["border"], width=2)
    draw.rounded_rectangle([(12, 16), (22, h - 16)], radius=5, fill=pal["accent"])
    draw.text((38, (h - th) // 2 - 2), fitted, font=fnt, fill=pal["text"])
    return img


def resolve_packaging_source(
    elem: Dict[str, Any],
    pipeline_dir: Path,
    idx: int,
    style_id: str = "style_01",
    color_plan: Optional[Dict[str, Any]] = None,
    font_candidates: Optional[List[str]] = None,
) -> Path:
    out_png = pipeline_dir / "_render_final" / f"pkg_{idx:03d}.png"
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img = render_packaging_png(elem, style_id=style_id, color_plan=color_plan, font_candidates=font_candidates)
    img.save(out_png)
    return out_png


def _rects_overlap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def _avoid_face_boxes(
    px: int,
    py: int,
    pw: int,
    ph: int,
    faces: List[Dict[str, int]],
    y_min: int,
    y_max: int,
) -> Tuple[int, int]:
    if not faces:
        return px, max(y_min, min(py, y_max - ph))
    face_rects = [(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]) for f in faces]
    step_y = 60
    candidates = [py]
    for d in range(1, 12):
        candidates.append(py + d * step_y)
        candidates.append(py - d * step_y)
    for cy in candidates:
        cy = max(y_min, min(cy, y_max - ph))
        cand_rect = (px, cy, px + pw, cy + ph)
        if not any(_rects_overlap(cand_rect, fr) for fr in face_rects):
            return px, cy
    return px, max(y_min, min(py, y_max - ph))


def _place_from_final_bbox(
    entry: Dict[str, Any],
    pw: int,
    ph: int,
    zone: Dict[str, int],
    default_xy: Tuple[int, int],
    faces: List[Dict[str, int]],
) -> Tuple[int, int]:
    fb = entry.get("final_bbox") or entry.get("bbox")
    if isinstance(fb, dict) and {"x", "y", "w", "h"}.issubset(fb.keys()):
        bx = int(fb["x"]) + (int(fb["w"]) - pw) // 2
        by = int(fb["y"]) + (int(fb["h"]) - ph) // 2
        px = max(zone["x_min"], min(bx, zone["x_max"] - pw))
        py = max(zone["y_min"], min(by, zone["y_max"] - ph))
        return int(px), int(py)
    return _avoid_face_boxes(default_xy[0], default_xy[1], pw, ph, faces, zone["y_min"], zone["y_max"])


def _build_color_filter_for_clip(clip: Dict[str, Any], color_plan: Dict[str, Any]) -> Optional[str]:
    cf_str = str(clip.get("color_filter") or "").strip()
    if cf_str:
        return cf_str
    cid = clip.get("clip_id")
    for item in (color_plan.get("clip_plans") or []):
        if item.get("clip_id") == cid:
            if not item.get("apply_color_grade"):
                return None
            vf = str(item.get("vf_filter") or "").strip()
            return vf or None
    return None


def _resolve_master_wav(pipeline_dir: Path) -> Tuple[Path, Dict[str, Any]]:
    mix_p = pipeline_dir / "08_mix_metrics.json"
    if mix_p.exists():
        mix_metrics = read_json(mix_p)
        cand = Path(mix_metrics.get("master_wav") or (pipeline_dir / "08_mixed_master.wav"))
        if cand.exists():
            return cand, mix_metrics
    fallback = pipeline_dir / "08_mixed_master.wav"
    if not fallback.exists():
        raise FileNotFoundError(f"混音母带不存在: {fallback}")
    return fallback, {}


def _manifest_lookups(manifest: Dict[str, Any]) -> Tuple[Dict[str, List[Dict[str, int]]], Dict[str, Dict[str, Any]]]:
    faces_by_mp4: Dict[str, List[Dict[str, int]]] = {}
    eye_by_mp4: Dict[str, Dict[str, Any]] = {}
    for item in (manifest.get("items") or []):
        mp = str(item.get("normalized_mp4") or "")
        if not mp:
            continue
        norm_faces: List[Dict[str, int]] = []
        for f_obj in (item.get("faces") or []):
            bb = f_obj.get("bbox")
            if isinstance(bb, (list, tuple)) and len(bb) == 4:
                x1, y1, x2, y2 = [float(v) for v in bb]
                norm_faces.append({
                    "x": int(round(x1 * CANVAS_WIDTH)),
                    "y": int(round(y1 * CANVAS_HEIGHT)),
                    "w": max(1, int(round((x2 - x1) * CANVAS_WIDTH))),
                    "h": max(1, int(round((y2 - y1) * CANVAS_HEIGHT))),
                })
            elif isinstance(bb, dict) and {"x", "y", "w", "h"}.issubset(bb.keys()):
                norm_faces.append({k: int(bb[k]) for k in ("x", "y", "w", "h")})
        for key in {mp, str(Path(mp).resolve())}:
            faces_by_mp4[key] = norm_faces
            if item.get("eye_anchor"):
                eye_by_mp4[key] = item["eye_anchor"]
    return faces_by_mp4, eye_by_mp4


def _compute_base_cache_key(
    edl: Dict[str, Any],
    color_plan: Dict[str, Any],
    master_wav: Path,
    target_frames: int,
    preview: bool,
) -> str:
    st = master_wav.stat()
    payload = {
        "edl": edl,
        "color_plan": color_plan,
        "master_wav_size": st.st_size,
        "master_wav_mtime_ns": st.st_mtime_ns,
        "target_frames": target_frames,
        "preview": preview,
    }
    return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()[:16]


def build_base_video_track(
    pipeline_dir: Path,
    edl: Dict[str, Any],
    color_plan: Dict[str, Any],
    master_wav: Path,
    manifest: Optional[Dict[str, Any]] = None,
    preview: bool = False,
) -> Path:
    """按 EDL 分镜精确裁切、单次高质量 Ken Burns / 锁眼推拉 + 调色 + 严防尾帧泄漏地拼接底轨。"""
    ffmpeg_bin = find_bin("ffmpeg")
    work_dir = pipeline_dir / "_render_final"
    work_dir.mkdir(parents=True, exist_ok=True)
    base_mp4 = work_dir / ("base_preview.mp4" if preview else "base_video.mp4")
    cache_flag = work_dir / ("base_preview.hash" if preview else "base_video.hash")

    fps = int(edl.get("fps") or DEFAULT_FPS)
    master_dur = get_wav_duration_sec(master_wav)
    target_frames = max(1, round(master_dur * fps))
    cache_key = _compute_base_cache_key(edl, color_plan, master_wav, target_frames, preview)

    if base_mp4.exists() and cache_flag.exists() and cache_flag.read_text(encoding="utf-8").strip() == cache_key:
        return base_mp4

    tw = 540 if preview else CANVAS_WIDTH
    th = 960 if preview else CANVAS_HEIGHT
    seg_files: List[Path] = []
    geom_cache: Dict[str, Tuple[int, int]] = {}
    _, eye_by_mp4 = _manifest_lookups(manifest or {"items": []})

    clips = edl.get("clips") or []
    for idx, clip in enumerate(clips):
        seg_out = work_dir / f"seg_{idx:03d}_{'p' if preview else 'f'}.mp4"
        media_path = str(clip.get("media_path") or "")
        if not media_path or not Path(media_path).exists():
            raise FileNotFoundError(f"EDL 镜头素材不存在: {media_path}")

        src_in = max(0.0, float(clip.get("source_in_sec", 0.0)))
        if clip.get("timeline_out_frame") is not None and clip.get("timeline_in_frame") is not None:
            clip_frames = max(1, int(clip["timeline_out_frame"]) - int(clip["timeline_in_frame"]))
        else:
            clip_frames = max(1, sec_to_frames(float(clip.get("duration_sec", 1.0)), fps))
        clip_dur = frames_to_sec(clip_frames, fps)
        src_out = float(clip.get("source_out_sec", src_in + clip_dur))
        src_avail = max(0.1, src_out - src_in)

        if media_path not in geom_cache:
            probe = ffprobe_media(Path(media_path))
            v_s = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
            g = parse_video_stream_geometry(v_s)
            geom_cache[media_path] = (g["display_width"], g["display_height"])
        sw, sh = geom_cache[media_path]

        eye = clip.get("eye_anchor") or eye_by_mp4.get(media_path) or eye_by_mp4.get(str(Path(media_path).resolve())) or {}
        fx = float(eye.get("cx", eye.get("eye_cx", 0.50)))
        fy = float(eye.get("cy", eye.get("eye_cy", 0.36)))
        tx = compute_crop_transform(sw, sh, tw, th, fx, fy)

        vf_parts: List[str] = [f"fps=fps={fps}"]
        # 防尾帧泄漏：严格按 source_out_sec - source_in_sec 截断源视频，杜绝 tpad 前播放多余 NG 画面
        trim_dur = min(src_avail, clip_dur) if src_avail >= clip_dur - 0.01 else src_avail
        vf_parts.append(f"trim=duration={trim_dur:.4f},setpts=PTS-STARTPTS")

        vf_extra = str(clip.get("vf_extra") or "").strip()
        kb = clip.get("ken_burns")
        has_kb = isinstance(kb, dict) and ("zoom_start" in kb or "zoom_end" in kb)

        if has_kb and not preview and not vf_extra:
            z0 = max(1.0, float(kb.get("zoom_start", 1.0)))
            z1 = max(1.0, float(kb.get("zoom_end", 1.06)))
            pan_x = float(kb.get("pan_x", 0.0))
            pan_y = float(kb.get("pan_y", 0.0))
            if abs(z1 - z0) > 1e-4 or abs(pan_x) > 1e-4 or abs(pan_y) > 1e-4:
                prog = f"min(t/{clip_dur:.4f},1)"
                z_expr = f"({z0:.4f}+({z1 - z0:.4f})*{prog})"
                vf_parts.append(
                    f"scale=w='trunc({tx['scaled_w']}*{z_expr}/2)*2':h='trunc({tx['scaled_h']}*{z_expr}/2)*2':eval=frame:flags=bicubic"
                )
                cx0 = tx["crop_x"]
                cy0 = tx["crop_y"]
                x_off = f"{cx0}*({z_expr})+(iw-{tw})/2*0.0+({pan_x:.4f}*iw*{prog})" if tx["scaled_w"] == tw else f"(iw-{tw})*{fx:.4f}+({pan_x:.4f}*iw*{prog})"
                y_off = f"{cy0}*({z_expr})+({pan_y:.4f}*ih*{prog})" if tx["scaled_h"] == th else f"(ih-{th})*{fy:.4f}+({pan_y:.4f}*ih*{prog})"
                vf_parts.append(f"crop={tw}:{th}:x='max(0,min(iw-{tw},{x_off}))':y='max(0,min(ih-{th},{y_off}))'")
            elif sw != tw or sh != th:
                vf_parts.append(f"scale={tx['scaled_w']}:{tx['scaled_h']}:flags=bicubic")
                vf_parts.append(f"crop={tw}:{th}:{tx['crop_x']}:{tx['crop_y']}")
        else:
            if sw != tw or sh != th:
                vf_parts.append(f"scale={tx['scaled_w']}:{tx['scaled_h']}:flags=bicubic")
                vf_parts.append(f"crop={tw}:{th}:{tx['crop_x']}:{tx['crop_y']}")
            if vf_extra and not preview:
                vf_parts.append(vf_extra)

        cf = _build_color_filter_for_clip(clip, color_plan)
        if cf and not preview:
            vf_parts.append(cf)

        if src_avail < clip_dur - 0.01:
            pad_sec = (clip_dur - src_avail) + 0.3
            vf_parts.append(f"tpad=stop_mode=clone:stop_duration={pad_sec:.4f}")

        if preview:
            vf_parts.extend(["setsar=1", "format=yuv420p"])
        else:
            vf_parts.extend(BT709_VF_SUFFIX)

        cmd = [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}",
            "-t", f"{src_avail + 0.1:.4f}",
            "-i", media_path,
            "-an",
            "-vf", ",".join(vf_parts),
            "-frames:v", str(clip_frames),
            "-c:v", "libx264",
            "-preset", "ultrafast" if preview else "medium",
            "-crf", "26" if preview else "18",
            "-pix_fmt", "yuv420p",
            *([] if preview else BT709_FFMPEG_ARGS),
            str(seg_out),
        ]
        run_cmd(cmd)
        seg_files.append(seg_out)

    concat_txt = work_dir / f"concat_{'p' if preview else 'f'}.txt"
    concat_txt.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in seg_files) + "\n",
        encoding="utf-8",
    )
    exact_dur = frames_to_sec(target_frames, fps)
    run_cmd([
        ffmpeg_bin, "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_txt),
        "-c", "copy",
        "-t", f"{exact_dur:.4f}",
        str(base_mp4),
    ])
    cache_flag.write_text(cache_key, encoding="utf-8")
    return base_mp4


def render_mp4(pipeline_dir: Path, preview: bool = False) -> Path:
    ffmpeg_bin = find_bin("ffmpeg")
    edl = read_json(pipeline_dir / "04_edl.json")
    subs_data = read_json(pipeline_dir / "05_subtitles.json")
    pkg_data = read_json(pipeline_dir / "06_packaging.json")
    color_plan = read_json(pipeline_dir / "07_color_plan.json")
    manifest = read_json(pipeline_dir / "01_manifest.json")

    master_wav, _ = _resolve_master_wav(pipeline_dir)
    base_mp4 = build_base_video_track(pipeline_dir, edl, color_plan, master_wav, manifest=manifest, preview=preview)
    out_mp4 = pipeline_dir / ("output_preview.mp4" if preview else "output_final.mp4")

    work_dir = pipeline_dir / "_render_final"
    work_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(work_dir.glob("pkg_*.png")) + list(work_dir.glob("sub_*.png")):
        stale.unlink(missing_ok=True)

    fps = int(edl.get("fps") or DEFAULT_FPS)
    master_dur = get_wav_duration_sec(master_wav)
    target_frames = max(1, round(master_dur * fps))
    exact_dur = frames_to_sec(target_frames, fps)

    if preview:
        run_cmd([
            ffmpeg_bin, "-y",
            "-i", str(base_mp4),
            "-i", str(master_wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-t", f"{exact_dur:.4f}",
            "-movflags", "+faststart",
            str(out_mp4),
        ])
        return out_mp4

    faces_by_mp4, _ = _manifest_lookups(manifest)

    def _faces_at_time(t: float) -> List[Dict[str, int]]:
        for c in edl.get("clips") or []:
            if float(c.get("timeline_in_sec", 0.0)) <= t < float(c.get("timeline_out_sec", 0.0)):
                mp = str(c.get("media_path") or "")
                return faces_by_mp4.get(mp) or faces_by_mp4.get(str(Path(mp).resolve())) or []
        return []

    templates = load_subtitle_templates()
    tpl_id = subs_data.get("template_id") or "style_01"
    tpl = templates.get(tpl_id) or next(iter(templates.values()))
    font_path = subs_data.get("font_path") or find_commercial_font(tpl.get("font_candidates"))
    style_id = pkg_data.get("style_id") or load_style_applied(pipeline_dir) or "style_01"

    overlays: List[Tuple[Path, int, int, float, float]] = []

    for idx, elem in enumerate(pkg_data.get("visual_elements") or []):
        s_t = max(0.0, frames_to_sec(int(elem.get("start_frame", 0)), fps))
        e_t = min(exact_dur, frames_to_sec(int(elem.get("end_frame", 0)), fps))
        if e_t <= s_t:
            continue
        png_path = resolve_packaging_source(
            elem, pipeline_dir, idx,
            style_id=style_id, color_plan=color_plan, font_candidates=tpl.get("font_candidates"),
        )
        with Image.open(png_path) as im:
            pw, ph = im.size
        def_x = max(SAFE_ZONE["x_min"], (CANVAS_WIDTH - pw) // 2)
        def_y = SAFE_ZONE["y_min"] + 40
        px, py = _place_from_final_bbox(elem, pw, ph, SAFE_ZONE, (def_x, def_y), _faces_at_time(s_t))
        overlays.append((png_path, px, py, s_t, e_t))

    for idx, sub in enumerate(subs_data.get("entries") or []):
        if sub.get("suppressed"):
            continue
        s_t = max(0.0, float(sub.get("start_sec", 0.0)))
        e_t = min(exact_dur, float(sub.get("end_sec", s_t + 0.5)))
        if e_t <= s_t:
            continue
        sub_png = work_dir / f"sub_{idx:03d}.png"
        sw_px, sh_px = render_subtitle_png(sub, tpl, font_path, sub_png)
        def_x, def_y = compute_subtitle_place_xy(sw_px, sh_px, tpl)
        px, py = _place_from_final_bbox(sub, sw_px, sh_px, SUBTITLE_SAFE_ZONE, (def_x, def_y), _faces_at_time(s_t))
        overlays.append((sub_png, px, py, s_t, e_t))

    if not overlays:
        run_cmd([
            ffmpeg_bin, "-y",
            "-i", str(base_mp4),
            "-i", str(master_wav),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{exact_dur:.4f}",
            "-movflags", "+faststart",
            str(out_mp4),
        ])
        return out_mp4

    cmd = [ffmpeg_bin, "-y", "-i", str(base_mp4), "-i", str(master_wav)]
    for png_p, *_ in overlays:
        cmd.extend(["-i", str(png_p)])

    filter_chains: List[str] = []
    cur_label = "[0:v]"
    for i, (_, px, py, s_t, e_t) in enumerate(overlays):
        in_idx = i + 2
        out_label = "[vout]" if i == len(overlays) - 1 else f"[v{i}]"
        filter_chains.append(
            f"{cur_label}[{in_idx}:v]overlay=x={px}:y={py}:enable='between(t,{s_t:.4f},{e_t:.4f})'{out_label}"
        )
        cur_label = out_label

    filter_script = work_dir / "overlay_filter.txt"
    filter_expr = ";".join(filter_chains)
    filter_script.write_text(";\n".join(filter_chains), encoding="utf-8")

    cmd.extend([
        "-filter_complex", filter_expr,
        "-map", "[vout]",
        "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-pix_fmt", "yuv420p",
        *BT709_FFMPEG_ARGS,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{exact_dur:.4f}",
        "-movflags", "+faststart",
        str(out_mp4),
    ])
    run_cmd(cmd)
    return out_mp4


def main() -> None:
    parser = argparse.ArgumentParser(description="成片渲染器（支持粗剪预览与终稿 BT.709 导出）")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--preview", action="store_true", help="输出低清快速粗剪预览 (540x960)")
    args = parser.parse_args()
    out = render_mp4(Path(args.pipeline_dir), preview=args.preview)
    print(f"[render_mp4] 渲染完成: {out}")


if __name__ == "__main__":
    main()
