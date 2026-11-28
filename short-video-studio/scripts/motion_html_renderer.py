#!/usr/bin/env python3
"""
motion_html_renderer.py · V11.0 真 30fps HTML/CSS/JS 动态视觉与柔羽化合成引擎

基于仓库内置 `capture_html_video.mjs`（Playwright Chromium + 确定性 `window.seek(t)`），
以单浏览器实例 `--batch-json` 高速批量输出带无损 Alpha 通道的 30fps 动态 `.mov`，
彻底替代 V10.0 的静态死 PNG 面板与静态花字：
- 真逐字敲击 + 闪烁光标打字机（style_03 / 14 / 16 / 18 / 20）
- 真纸质感米白大纲清单白板（style_15 原母本真魂）
- 真浅灰杂志测评白板与交错弹入卡片（style_24 原母本真魂）
- 真纯黑纪实双区逐句高亮引号/卷宗卡（style_09 / 19 / 22）
- 真柔和径向羽化圆窗 + 动态问号/痛点/国风金句（style_03 / 07 / 12 / 13 / 14 / 17 / 18 / 20 / 23）
- 真圆角方窗 + 双卡对比（style_04 / 05 / 10 / 13 / 17 / 24）
- 真商业口播风格自适应动态概念图解卡（compare / trio / chain）
- 真 30fps 透明前景动效花字（竖排大黄字/悬浮概念卡/胶囊气泡/发光金句/满屏白字冲击）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from common import (
    ASSETS_DIR,
    BT709_FFMPEG_ARGS,
    BT709_VF_SUFFIX,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SCRIPTS_DIR,
    find_bin,
    normalize_display_glyphs,
)

STAGE_HTML_SRC = ASSETS_DIR / "motion_templates" / "stage.html"
FONTS_SRC_DIR = ASSETS_DIR / "fonts"
CAPTURE_SCRIPT = SCRIPTS_DIR / "capture_html_video.mjs"

_HTML_PANEL_MODES = frozenset({
    "dark_circle_panel_pip",
    "dark_typewriter_card_pip",
    "framed_box_pip",
    "review_whiteboard_pip",
    "paper_outline_pip",
    "concept_diagram_pip",
})


def is_html_motion_available() -> bool:
    """检查本机是否具备 node + capture_html_video.mjs + playwright-core + 浏览器与舞台模板。"""
    if os.environ.get("SVS_DISABLE_HTML_MOTION", "").strip() in ("1", "true", "yes"):
        return False
    if not STAGE_HTML_SRC.is_file() or not CAPTURE_SCRIPT.is_file():
        return False
    node_bin = shutil.which("node")
    if not node_bin:
        return False
    pw_dir = Path.home() / ".short-video-studio" / "node_tools" / "node_modules" / "playwright-core"
    if not pw_dir.exists() and not os.environ.get("SVS_PLAYWRIGHT_DIR"):
        return False
    return True


def _ensure_stage_workspace(stage_dir: Path) -> Path:
    """准备供本地 HTTP 静态服务器托管的舞台目录（stage.html + 随包商业字体软链/拷贝）。"""
    stage_dir = Path(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    dst_html = stage_dir / "stage.html"
    if (
        (not dst_html.is_file())
        or (dst_html.stat().st_mtime < STAGE_HTML_SRC.stat().st_mtime)
        or (dst_html.stat().st_size != STAGE_HTML_SRC.stat().st_size)
    ):
        shutil.copy2(STAGE_HTML_SRC, dst_html)
    fonts_dst = stage_dir / "fonts"
    if not fonts_dst.exists():
        try:
            fonts_dst.symlink_to(FONTS_SRC_DIR.resolve(), target_is_directory=True)
        except Exception:
            fonts_dst.mkdir(parents=True, exist_ok=True)
            for fp in FONTS_SRC_DIR.glob("*.*"):
                if fp.suffix.lower() in (".otf", ".ttf", ".woff", ".woff2"):
                    shutil.copy2(fp, fonts_dst / fp.name)
    return stage_dir


def _semantic_wrap(text: str, max_chars: int = 12, max_lines: int = 4) -> List[str]:
    """用 step5 语义分词器安全折行，并做粤语高清展示字形映射（𠮶 -> 嗰）。"""
    clean = normalize_display_glyphs(re.sub(r"\s+", "", str(text or "").strip()))
    if not clean:
        return []
    try:
        from step5_subtitles import dp_segment_short_lines
        segs = dp_segment_short_lines(
            clean,
            max_width_px=max(280, max_chars * 68),
            font_path=None,
            font_size=56,
            max_chars=max(5, max_chars),
            strip_punct=False,
        )
        lines = [t.strip("，、；：,;:。！？!? ") for _, _, t in segs if t.strip("，、；：,;:。！？!? ")]
        if lines:
            return lines[:max_lines]
    except Exception:
        pass
    parts = [p for p in re.split(r"[，。！？、,.!?；;]+", clean) if p]
    return parts[:max_lines] if parts else [clean[:max_chars]]


def _pinyin_upper(text: str) -> str:
    clean = re.sub(r"[^\u4e00-\u9fff]", "", normalize_display_glyphs(text))[:8]
    if not clean:
        return "CORE FRAMEWORK"
    try:
        from pypinyin import Style, pinyin  # type: ignore
        return " ".join(str(x[0] if x else "").upper() for x in pinyin(clean, style=Style.NORMAL))
    except Exception:
        return "CORE FRAMEWORK"


def soft_circle_mask_png(cache_dir: Path, radius: int, feather_px: int = 14) -> Path:
    """生成带柔和径向羽化边缘的抗锯齿圆形 Alpha 遮罩（告别生硬剪纸圆边）。"""
    r = max(24, int(radius))
    f = max(2, min(28, int(feather_px)))
    p = Path(cache_dir) / f"_v11_soft_cmask_r{r}_f{f}.png"
    if p.is_file():
        return p
    d = r * 2
    hi = Image.new("L", (d * 2, d * 2), 0)
    inset = max(4, f * 2)
    ImageDraw.Draw(hi).ellipse([(inset, inset), (d * 2 - inset - 1, d * 2 - inset - 1)], fill=255)
    hi = hi.filter(ImageFilter.GaussianBlur(radius=f * 0.9))
    hi.resize((d, d), Image.Resampling.LANCZOS).save(p)
    return p


def soft_rounded_mask_png(cache_dir: Path, w: int, h: int, radius: int) -> Path:
    w, h, r = max(32, int(w) & ~1), max(32, int(h) & ~1), max(8, int(radius))
    p = Path(cache_dir) / f"_v11_rmask_{w}x{h}_r{r}.png"
    if p.is_file():
        return p
    hi = Image.new("L", (w * 2, h * 2), 0)
    ImageDraw.Draw(hi).rounded_rectangle([(4, 4), (w * 2 - 5, h * 2 - 5)], radius=r * 2, fill=255)
    hi.resize((w, h), Image.Resampling.LANCZOS).save(p)
    return p


def _cover_crop_vf(w: int, h: int, face_bias: bool = True) -> str:
    w_e = max(2, int(w) & ~1)
    h_e = max(2, int(h) & ~1)
    y_expr = "(ih-oh)*0.22" if face_bias else "(ih-oh)/2"
    return f"scale={w_e}:{h_e}:force_original_aspect_ratio=increase:flags=lanczos,crop={w_e}:{h_e}:(iw-ow)/2:{y_expr},setsar=1"


def resolve_concept_diagram_geometry(pip_layout: Dict[str, Any], style_id: str = "") -> Dict[str, int]:
    """单一真值几何解析器：保证 HTML 外环与 FFmpeg 实拍圆窗圆心/半径/安全顶距 100% 同步。"""
    sid = str(style_id or pip_layout.get("_style_id") or "").strip()
    tb_spec = None
    try:
        from style_visual_truth import get_persistent_top_bar_spec
        tb_spec = get_persistent_top_bar_spec(sid) if sid else None
    except Exception:
        tb_spec = None
    raw_top_safe = pip_layout.get("top_safe_y")
    has_top_bar = bool(tb_spec) or (raw_top_safe is not None and int(raw_top_safe) > 160)
    if raw_top_safe is not None and int(raw_top_safe) > 160:
        top_safe_y = int(raw_top_safe)
    elif tb_spec:
        top_safe_y = int(tb_spec.get("top_safe_y") or 356)
    else:
        top_safe_y = 160
    cr = int(pip_layout.get("circle_r") or 92)
    cx = int(pip_layout.get("circle_cx") or 888)
    raw_cy = pip_layout.get("circle_cy")
    if raw_cy is not None:
        cy = int(raw_cy)
        if has_top_bar:
            cy = max(cy, top_safe_y + cr)
    else:
        cy = max(top_safe_y + cr + 10, 458) if has_top_bar else 220
    return {
        "circle_cx": cx,
        "circle_cy": cy,
        "circle_r": cr,
        "top_safe_y": top_safe_y,
        "has_top_bar": int(has_top_bar),
    }


def build_panel_job_spec(pip_layout: Dict[str, Any], dur_sec: float, style_id: str = "") -> Optional[Dict[str, Any]]:
    """将 EDL clip 的 pip_layout 转换为 stage.html 消费的标准化 30fps 动效任务规格。"""
    mode = str(pip_layout.get("mode") or "").strip()
    if mode not in _HTML_PANEL_MODES:
        return None
    variant = str(pip_layout.get("variant") or "")
    title = normalize_display_glyphs(str(pip_layout.get("panel_title") or pip_layout.get("giant_back_text") or "核心要点").strip())
    quote = normalize_display_glyphs(str(pip_layout.get("quote_text") or title).strip())
    subtitle = normalize_display_glyphs(str(pip_layout.get("panel_subtitle") or "").strip())
    accent = str(pip_layout.get("accent_hex") or "#FFE14D")
    accent2 = str(pip_layout.get("accent2_hex") or "#DC2626")
    fg = str(pip_layout.get("fg_hex") or "#F8FAFC")
    dur = max(0.6, round(float(dur_sec), 3))
    sec_media = str(pip_layout.get("secondary_media_path") or "")
    has_sec = bool(sec_media and Path(sec_media).is_file())
    custom_lines = [normalize_display_glyphs(str(x)) for x in (pip_layout.get("panel_lines") or []) if str(x).strip()]
    custom_items = pip_layout.get("panel_items") if isinstance(pip_layout.get("panel_items"), list) else []

    if mode == "dark_typewriter_card_pip":
        is_blur = variant in ("s16_blur_dark_typewriter", "s18_blur_white_strip")
        lines = custom_lines or _semantic_wrap(quote, max_chars=12 if is_blur else 13, max_lines=4)
        return {
            "kind": "typewriter_quote",
            "variant": variant,
            "dur": dur,
            "opaque": not is_blur,
            "alpha": is_blur,
            "title": title,
            "subtitle": subtitle,
            "citation": normalize_display_glyphs(str(pip_layout.get("citation") or "")),
            "lines": lines,
            "accent": accent,
            "accent2": accent2,
            "fg": fg,
            "font_role": "serif" if variant in ("s03_black_typewriter", "s20_black_quote_top") else "sans",
        }

    if mode == "paper_outline_pip":
        items = custom_items or [
            {"ord": ["一、", "二、", "三、"][i], "text": ln}
            for i, ln in enumerate((custom_lines or _semantic_wrap(quote, max_chars=11, max_lines=3))[:3])
        ]
        return {
            "kind": "paper_outline_whiteboard",
            "variant": variant or "s15_paper_outline",
            "dur": dur,
            "opaque": True,
            "alpha": False,
            "title": title[:8] or "核心三要素",
            "pinyin_sub": _pinyin_upper(title or "核心要素"),
            "items": items,
            "circle_cx": int(pip_layout.get("circle_cx") or 184),
            "circle_cy": int(pip_layout.get("circle_cy") or 216),
            "circle_r": int(pip_layout.get("circle_r") or 106),
            "accent": accent,
        }

    if mode == "review_whiteboard_pip":
        pts = custom_items or [{"text": ln} for ln in (custom_lines or _semantic_wrap(quote, max_chars=10, max_lines=2))]
        return {
            "kind": "magazine_review_whiteboard",
            "variant": variant or "s24_whiteboard_multi",
            "dur": dur,
            "opaque": True,
            "alpha": False,
            "title": title[:7] or "实测拆解",
            "items": pts[:3],
            "accent": accent,
            "font_role": "oblique",
        }

    if mode == "concept_diagram_pip":
        geom = resolve_concept_diagram_geometry(pip_layout, style_id=style_id)
        return {
            "kind": "concept_diagram_card",
            "diagram_mode": str(pip_layout.get("diagram_mode") or "compare"),
            "light_theme": bool(pip_layout.get("light_theme")),
            "with_anchor_circle": bool(pip_layout.get("with_anchor_circle", True)),
            "circle_cx": geom["circle_cx"],
            "circle_cy": geom["circle_cy"],
            "circle_r": geom["circle_r"],
            "top_safe_y": geom["top_safe_y"],
            "dur": dur,
            "opaque": True,
            "alpha": False,
            "title": title[:10] or "核心拆解",
            "subtitle": subtitle or "底层逻辑",
            "lines": custom_lines or _semantic_wrap(quote, max_chars=10, max_lines=3),
            "items": custom_items,
            "accent": accent,
            "accent2": accent2,
            "font_role": str(pip_layout.get("font_role") or "sans"),
        }

    if mode == "dark_circle_panel_pip":
        if variant.startswith("bottom_left"):
            lines = custom_lines or _semantic_wrap(quote, max_chars=14, max_lines=4)
            return {
                "kind": "doc_evidence_panel",
                "variant": variant,
                "dur": dur,
                "opaque": True,
                "alpha": False,
                "has_sec": has_sec,
                "title": title[:10],
                "subtitle": subtitle or "核心焦点",
                "lines": lines,
                "circle_cx": 205,
                "circle_cy": 1085,
                "circle_r": 114,
                "accent": accent,
                "accent2": accent2,
                "font_role": "serif" if variant == "bottom_left_yellow_quote" else "sans",
            }
        is_blur_bg = variant in ("top_blur_qmark", "top_blur_gold")
        lines = custom_lines or _semantic_wrap(quote, max_chars=9, max_lines=2)
        return {
            "kind": "circle_focus_panel",
            "variant": variant,
            "dur": dur,
            "opaque": not is_blur_bg,
            "alpha": is_blur_bg,
            "title": title,
            "subtitle": subtitle,
            "lines": lines[:2],
            "circle_cx": int(pip_layout.get("circle_cx") or 540),
            "circle_cy": int(pip_layout.get("circle_cy") or 520),
            "circle_r": int(pip_layout.get("circle_r") or 220),
            "accent": accent,
            "accent2": accent2,
            "font_role": "serif" if variant in ("top_guofeng_gold", "top_blur_gold", "top_red_pain", "top_blur_qmark") else "sans",
        }

    if mode == "framed_box_pip":
        if variant == "s04_dual_yellow_header":
            fx, fy, fw, fh, rad = 130, 400, 820, 800, 38
        elif variant == "s04_focus_giant_word":
            fx, fy, fw, fh, rad = 200, 210, 680, 680, 36
        elif variant == "s05_center_pip":
            fx, fy, fw, fh, rad = 140, 440, 800, 560, 20
        elif variant == "s10_green_tag_box":
            fx, fy, fw, fh, rad = 130, 330, 820, 640, 26
        elif variant == "s13_red_warn_box":
            fx, fy, fw, fh, rad = 130, 430, 820, 740, 24
        elif variant == "s24_rounded_impact":
            fx, fy, fw, fh, rad = 80, 320, 920, 640, 36
        else:
            fx, fy, fw, fh, rad = 110, 260, 860, 760, 22
        lines = custom_lines or _semantic_wrap(quote, max_chars=10, max_lines=2)
        return {
            "kind": "framed_box_panel",
            "variant": variant,
            "dur": dur,
            "opaque": True,
            "alpha": False,
            "fx": fx, "fy": fy, "fw": fw, "fh": fh, "rad": rad,
            "title": title,
            "subtitle": subtitle,
            "lines": lines,
            "accent": accent,
            "accent2": accent2,
            "font_role": "serif" if variant in ("s04_dual_yellow_header", "s13_red_warn_box") else "sans",
        }
    return None


def build_callout_job_spec(
    elem: Dict[str, Any],
    style_id: str,
    dur_sec: float,
    top_safe_y: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """将前景动态花字元素转为透明通道 30fps HTML 动效任务规格。"""
    kind = str(elem.get("kind") or elem.get("type") or "")
    if kind not in ("center_burst_title", "flower_text", "stamp_tag", "kinetic_callout"):
        return None
    if kind == "stamp_tag" and elem.get("burst_tags"):
        return None
    text = normalize_display_glyphs(str(elem.get("text_or_asset") or elem.get("text") or "").strip())
    if not text:
        return None
    # 保护 style_01 三拍开场综艺斜标 / style_02 舞台 / style_03 / style_17 既有契约
    if style_id in ("style_01", "style_02", "style_03", "style_17"):
        return None
    custom_var = str(elem.get("callout_variant") or "")
    sub = normalize_display_glyphs(str(elem.get("subtext") or "").strip())
    items = elem.get("items") if isinstance(elem.get("items"), list) else []
    dur = max(0.6, min(6.0, round(float(dur_sec), 3)))

    var_map = {
        "style_05": ("s05_crimson_3d", "serif", 960, "#FDE047"),
        "style_06": ("s06_warm_gold", "serif_bold", 980, "#F4E2A8"),
        "style_08": ("s08_giant_white", "sans", 900, "#FFE14D"),
        "style_10": ("s08_giant_white", "sans", 920, "#FFE135"),
        "style_12": ("s12_pill_bubbles", "sans", 940, "#FFE135"),
        "style_15": ("s15_glow_white", "sans", 960, "#FFE14D"),
        "style_16": ("s16_glow_white", "sans", 950, "#FACC15"),
        "style_18": ("s18_blur_white_strip", "sans", 560, "#DC2626"),
        "style_19": ("s08_giant_white", "sans", 930, "#FFD426"),
        "style_20": ("s20_crimson_plate", "serif", 1030, "#F3D37A"),
        "style_21": ("s21_lime_arrow", "sans", 240, "#D9F94D"),
        "style_22": ("s22_floating_tags", "serif_bold", 820, "#DC2626"),
        "style_23": ("s06_warm_gold", "serif_bold", 980, "#FDE68A"),
        "style_24": ("s24_vertical_yellow", "oblique", 310, "#FFE135"),
    }
    def_var, def_font, def_y, def_acc = var_map.get(style_id, ("s08_giant_white", "sans", 940, "#FFE14D"))
    variant = custom_var or def_var
    lines = _semantic_wrap(text, max_chars=8, max_lines=2)
    z_layer = str(elem.get("z_layer") or "")
    eff_top_safe = int(top_safe_y if top_safe_y is not None else (elem.get("top_safe_y") or 160))
    if elem.get("callout_y") is not None:
        y_val = int(elem["callout_y"])
    elif z_layer == "behind_person" or (style_id in ("style_10", "style_16") and kind == "center_burst_title"):
        fb = elem.get("final_bbox") or elem.get("bbox") or {}
        y_val = int(fb.get("y") or 280)
    else:
        y_val = def_y
    if eff_top_safe > 160 and z_layer != "behind_person":
        y_val = max(y_val, eff_top_safe + 12)
    return {
        "kind": "kinetic_callout",
        "variant": variant,
        "dur": dur,
        "opaque": False,
        "alpha": True,
        "title": text[:16],
        "subtitle": sub,
        "lines": lines,
        "items": items,
        "y": y_val,
        "top_safe_y": eff_top_safe,
        "z_layer": z_layer,
        "accent": str(elem.get("accent_hex") or def_acc),
        "font_role": str(elem.get("font_role") or def_font),
    }


def batch_render_motion_jobs(
    jobs: Dict[str, Dict[str, Any]],
    work_dir: Path,
    fps: int = 30,
) -> Dict[str, Path]:
    """
    将一条视频中所有 HTML 30fps 动态面板与透明前景花字，合并为单次 Chromium `--batch-json` 高速批渲染。
    内置内容哈希缓存，同一批规格不重复渲染。返回 {job_key: rendered_media_path}。
    """
    if not jobs or not is_html_motion_available():
        return {}

    stage_dir = _ensure_stage_workspace(Path(work_dir) / "_v11_motion_stage")
    stage_html_hash = hashlib.sha256(STAGE_HTML_SRC.read_bytes()).hexdigest()[:10] if STAGE_HTML_SRC.is_file() else ""
    out_map: Dict[str, Path] = {}
    batch_tasks: List[Dict[str, Any]] = []
    pending_keys: List[Tuple[str, Path]] = []

    for jkey, spec in jobs.items():
        use_alpha = bool(spec.get("alpha"))
        ext = ".mov" if use_alpha else ".mp4"
        payload_dict = {**spec, "fps": int(fps), "_stage_html": stage_html_hash}
        payload = json.dumps(payload_dict, ensure_ascii=False, sort_keys=True)
        h12 = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        out_file = stage_dir / f"m_{h12}{ext}"
        if out_file.is_file() and out_file.stat().st_size > 2048:
            out_map[jkey] = out_file
            continue
        job_json_name = f"job_{h12}.json"
        (stage_dir / job_json_name).write_text(payload, encoding="utf-8")
        batch_tasks.append({
            "projectDir": str(stage_dir.resolve()),
            "page": "stage.html",
            "query": f"job={job_json_name}",
            "fps": int(fps),
            "dur": float(spec.get("dur") or 2.8),
            "size": f"{CANVAS_WIDTH}x{CANVAS_HEIGHT}",
            "alpha": use_alpha,
            "jpeg": not use_alpha,
            "out": str(out_file.resolve()),
        })
        pending_keys.append((jkey, out_file))

    if batch_tasks:
        manifest_p = stage_dir / f"batch_{hashlib.sha256(str(len(batch_tasks)).encode()).hexdigest()[:6]}.json"
        manifest_p.write_text(json.dumps({"tasks": batch_tasks}, ensure_ascii=False, indent=2), encoding="utf-8")
        env = dict(os.environ)
        env["FFMPEG_BIN"] = find_bin("ffmpeg")
        cmd = ["node", str(CAPTURE_SCRIPT), "--batch-json", str(manifest_p)]
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            print(f"[v11_motion] [WARN] HTML 批渲染失败，回退 PIL: {(res.stderr or res.stdout)[-300:]}", file=sys.stderr)
        for jkey, out_file in pending_keys:
            if out_file.is_file() and out_file.stat().st_size > 2048:
                out_map[jkey] = out_file
    return out_map


def build_html_motion_panel_cmd(
    ffmpeg_bin: str,
    media_path: str,
    src_in: float,
    src_avail: float,
    base_vf_chain: str,
    pip_layout: Dict[str, Any],
    rendered_bg_or_ov: Path,
    clip_frames: int,
    fps: int,
    out_mp4: Path,
    style_id: str = "",
) -> Optional[List[str]]:
    """
    将预先由 Chromium 30fps 渲染好的动态底板/透明层视频，与实拍主播/B-roll 实时合成为最终片段 MP4。
    圆窗全部采用 14px 柔和径向羽化遮罩（告别生硬剪纸边），彻底实现 30fps 真动效！
    """
    mode = str(pip_layout.get("mode") or "").strip()
    if mode not in _HTML_PANEL_MODES or not Path(rendered_bg_or_ov).is_file():
        return None
    work = out_mp4.parent
    tail_str = ",".join(BT709_VF_SUFFIX)
    variant = str(pip_layout.get("variant") or "")
    sec_media = str(pip_layout.get("secondary_media_path") or "")
    sec_in = max(0.0, float(pip_layout.get("secondary_in_sec") or 0.0))
    has_sec = bool(sec_media and Path(sec_media).is_file() and str(Path(sec_media).resolve()) != str(Path(media_path).resolve()))
    clip_dur = max(0.2, clip_frames / float(max(1, fps)))
    mov_p = str(rendered_bg_or_ov)
    panel_ss = max(0.0, float(pip_layout.get("v11_panel_offset_sec") or 0.0))
    mov_in_args = (
        ["-ss", f"{panel_ss:.4f}", "-t", f"{clip_dur + 0.18:.4f}", "-i", mov_p]
        if panel_ss > 0.005
        else ["-i", mov_p]
    )

    # 1) 真打字机卡（炭墨全屏动态 mp4 或 透明 mov 叠重虚化视频）
    if mode == "dark_typewriter_card_pip":
        is_blur = variant in ("s16_blur_dark_typewriter", "s18_blur_white_strip")
        if is_blur:
            fc = (
                f"[0:v]{base_vf_chain},boxblur=26:3,eq=brightness=-0.26:saturation=0.66[bg];"
                f"[1:v]fps=fps={fps},setpts=PTS-STARTPTS[ov];"
                f"[bg][ov]overlay=0:0:shortest=1,{tail_str}[vout]"
            )
            inputs = ["-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path, *mov_in_args]
        else:
            # 炭墨动态打字机直接作为高画质主视频（带微动态光标与逐字敲出，杜绝静止检测）
            fc = f"[0:v]fps=fps={fps},setpts=PTS-STARTPTS,{tail_str}[vout]"
            inputs = [*mov_in_args]
        return [
            ffmpeg_bin, "-y", *inputs, "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # 2) style_15 纸底大纲清单白板（左上柔和圆窗主播 + 动态逐条展开大纲）
    if mode == "paper_outline_pip":
        cx = int(pip_layout.get("circle_cx") or 184)
        cy = int(pip_layout.get("circle_cy") or 216)
        cr = int(pip_layout.get("circle_r") or 106)
        diam = cr * 2
        cmask = soft_circle_mask_png(work, cr, feather_px=10)
        fc = (
            f"[0:v]{base_vf_chain},{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[c_raw];"
            f"[1:v]format=gray[cm];[c_raw][cm]alphamerge[c_ok];"
            f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[bg];"
            f"[bg][c_ok]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(cmask),
            *mov_in_args,
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # 3) 商业口播风格自适应动态概念图解卡（可选右上角柔和主播圆窗）
    if mode == "concept_diagram_pip":
        if bool(pip_layout.get("with_anchor_circle", True)):
            geom = resolve_concept_diagram_geometry(pip_layout, style_id=str(style_id or pip_layout.get("_style_id") or ""))
            cx = geom["circle_cx"]
            cy = geom["circle_cy"]
            cr = geom["circle_r"]
            diam = cr * 2
            cmask = soft_circle_mask_png(work, cr, feather_px=10)
            fc = (
                f"[0:v]{base_vf_chain},{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[c_raw];"
                f"[1:v]format=gray[cm];[c_raw][cm]alphamerge[c_ok];"
                f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[bg];"
                f"[bg][c_ok]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(cmask),
                *mov_in_args,
            ]
        else:
            fc = f"[0:v]fps=fps={fps},setpts=PTS-STARTPTS,{tail_str}[vout]"
            inputs = [*mov_in_args]
        return [
            ffmpeg_bin, "-y", *inputs, "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # 4) style_24 杂志纸底测评白板（中央圆角实拍视频卡槽 + 顶部黄蓝大字 + 底部交错弹入卡片）
    if mode == "review_whiteboard_pip":
        fw, fh, rad = 860, 500, 28
        rmask = soft_rounded_mask_png(work, fw, fh, rad)
        vid_src = sec_media if has_sec else media_path
        vid_in = sec_in if has_sec else src_in
        vid_vf = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS" if has_sec else base_vf_chain
        fc = (
            f"[0:v]{vid_vf},{_cover_crop_vf(fw, fh, face_bias=not has_sec)},format=rgba[p_raw];"
            f"[1:v]format=gray[rm];[p_raw][rm]alphamerge[p_ok];"
            f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[bg];"
            f"[bg][p_ok]overlay=x=110:y=316:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{vid_in:.4f}", "-t", f"{(clip_dur if has_sec else src_avail) + 0.15:.4f}", "-i", vid_src,
            "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
            *mov_in_args,
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # 5) 柔和羽化圆窗看板（顶部/左下，兼容纯黑/虚化/双区实拍）
    if mode == "dark_circle_panel_pip":
        is_bottom_left = variant.startswith("bottom_left")
        is_blur_bg = variant in ("top_blur_qmark", "top_blur_gold")
        if is_bottom_left:
            cx, cy, cr = 205, 1085, 114
            feather = 10
        else:
            cx = int(pip_layout.get("circle_cx") or 540)
            cy = int(pip_layout.get("circle_cy") or 520)
            cr = int(pip_layout.get("circle_r") or 220)
            feather = 18
        diam = cr * 2
        cmask = soft_circle_mask_png(work, cr, feather_px=feather)

        if is_blur_bg:
            fc = (
                f"[0:v]{base_vf_chain},split=2[b_src][c_src];"
                f"[b_src]boxblur=24:2,eq=brightness=-0.22:saturation=0.72[bg_blur];"
                f"[c_src]{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[c_raw];"
                f"[1:v]format=gray[cm];[c_raw][cm]alphamerge[c_ok];"
                f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[ov];"
                f"[bg_blur][ov]overlay=0:0:shortest=1[m1];"
                f"[m1][c_ok]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(cmask),
                *mov_in_args,
            ]
        elif variant == "bottom_left_doc" and has_sec:
            sec_trim = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS"
            fc = (
                f"[0:v]{base_vf_chain},{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[c_raw];"
                f"[1:v]format=gray[cm];[c_raw][cm]alphamerge[c_ok];"
                f"[3:v]{sec_trim},{_cover_crop_vf(920, 526, face_bias=False)}[ev];"
                f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[bg];"
                f"[bg][ev]overlay=x=80:y=252:shortest=1[m1];"
                f"[m1][c_ok]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(cmask),
                *mov_in_args,
                "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
            ]
        else:
            fc = (
                f"[0:v]{base_vf_chain},{_cover_crop_vf(diam, diam, face_bias=True)},format=rgba[c_raw];"
                f"[1:v]format=gray[cm];[c_raw][cm]alphamerge[c_ok];"
                f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[bg];"
                f"[bg][c_ok]overlay=x={cx - cr}:y={cy - cr}:shortest=1,{tail_str}[vout]"
            )
            inputs = [
                "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                "-loop", "1", "-framerate", str(fps), "-i", str(cmask),
                *mov_in_args,
            ]
        return [
            ffmpeg_bin, "-y", *inputs, "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    # 6) 圆角方窗 / 双卡对比（动态弹入标题 + 圆角实时视频）
    if mode == "framed_box_pip":
        if variant == "s17_dual_compare_cards":
            fw, fh, rad = 880, 380, 24
            rmask = soft_rounded_mask_png(work, fw, fh, rad)
            if has_sec:
                sec_trim = f"fps=fps={fps},trim=duration={clip_dur:.4f},setpts=PTS-STARTPTS"
                fc = (
                    f"[0:v]{base_vf_chain},{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[t_raw];"
                    f"[3:v]{sec_trim},{_cover_crop_vf(fw, fh, face_bias=False)},format=rgba[b_raw];"
                    f"[1:v]format=gray,split=2[m1][m2];"
                    f"[t_raw][m1]alphamerge[t_ok];[b_raw][m2]alphamerge[b_ok];"
                    f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[bg];"
                    f"[bg][t_ok]overlay=x=100:y=170:shortest=1[s1];"
                    f"[s1][b_ok]overlay=x=100:y=585:shortest=1,{tail_str}[vout]"
                )
                inputs = [
                    "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                    "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
                    *mov_in_args,
                    "-ss", f"{sec_in:.4f}", "-t", f"{clip_dur + 0.2:.4f}", "-i", sec_media,
                ]
            else:
                fc = (
                    f"[0:v]{base_vf_chain},split=2[sa][sb];"
                    f"[sa]{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[t_raw];"
                    f"[sb]scale=iw*1.16:ih*1.16,crop=iw/1.16:ih/1.16,{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[b_raw];"
                    f"[1:v]format=gray,split=2[m1][m2];"
                    f"[t_raw][m1]alphamerge[t_ok];[b_raw][m2]alphamerge[b_ok];"
                    f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[bg];"
                    f"[bg][t_ok]overlay=x=100:y=170:shortest=1[s1];"
                    f"[s1][b_ok]overlay=x=100:y=585:shortest=1,{tail_str}[vout]"
                )
                inputs = [
                    "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
                    "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
                    *mov_in_args,
                ]
            return [
                ffmpeg_bin, "-y", *inputs, "-an", "-filter_complex", fc, "-map", "[vout]",
                "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
            ]

        spec = build_panel_job_spec(pip_layout, clip_dur) or {}
        fx, fy = int(spec.get("fx") or 130), int(spec.get("fy") or 400)
        fw, fh = int(spec.get("fw") or 820), int(spec.get("fh") or 800)
        rad = int(spec.get("rad") or 36)
        rmask = soft_rounded_mask_png(work, fw, fh, rad)
        fc = (
            f"[0:v]{base_vf_chain},{_cover_crop_vf(fw, fh, face_bias=True)},format=rgba[f_raw];"
            f"[1:v]format=gray[rm];[f_raw][rm]alphamerge[f_ok];"
            f"[2:v]fps=fps={fps},setpts=PTS-STARTPTS[bg];"
            f"[bg][f_ok]overlay=x={fx}:y={fy}:shortest=1,{tail_str}[vout]"
        )
        return [
            ffmpeg_bin, "-y",
            "-ss", f"{src_in:.4f}", "-t", f"{src_avail + 0.1:.4f}", "-i", media_path,
            "-loop", "1", "-framerate", str(fps), "-i", str(rmask),
            *mov_in_args,
            "-an", "-filter_complex", fc, "-map", "[vout]",
            "-frames:v", str(clip_frames), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS, str(out_mp4),
        ]

    return None
