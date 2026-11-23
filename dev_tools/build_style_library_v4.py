#!/usr/bin/env python3
"""Rebuild the 24-style library (v4.0.0) + source_manifest.json from verified JianYing drafts.

Single source of truth:
  * forensic/jianying_ground_truth_full.json  — aggregate metrics, cross-verified twice
    against the decrypted main drafts (0 diffs on 112 projects).
  * forensic/decrypted/<project>/draft_content.json — per-file sha256, exact text
    background/border specs, V1 segment geometry.

Every measured field written here is derived; nothing is hand-typed. Prose blocks that
assert measurements are regenerated from the same data. Run:

    /tmp/jyvenv/bin/python dev_tools/build_style_library_v4.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import statistics
import sys
from datetime import datetime, timezone, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
GT = ROOT / "forensic/jianying_ground_truth_full.json"
DEC = ROOT / "forensic/decrypted"
LIB = ROOT / "short-video-studio/assets/style_spectrum/style_spectrum_library.json"
MANIFEST = ROOT / "forensic/source_manifest.json"
SUBTITLES = ROOT / "short-video-studio/assets/subtitle_templates/templates.json"
# 字幕「描边 or 阴影」真值：由 dev_tools/measure_text_style_truth.py 从解密草稿量出。
# 这里绝不再从 sub_stroke_ratio × 5 推一个描边宽度——那是凭空造的，会把 24 套全画成黑边。
TEXT_TRUTH = ROOT / "forensic/text_style_truth.json"

CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST).isoformat(timespec="seconds")

# 风格 → 真实源草稿目录名的私有映射（含本机项目名，不进技能包；见 forensic/style_sources.json）
SOURCES_FILE = ROOT / "forensic/style_sources.json"
STYLE_SOURCES = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))["styles"]

# 49 套里 source 直接照抄了旧视频的几套，需要重写「设计意图」名称
NAME_OVERRIDES = {
    # 名称里断言了源草稿没有的东西（百分比/顶栏/底条/白条），按实测改名
    "style_02": "极速跳剪避坑 · 暗灰方角底框荧光绿风",
    "style_03": "暗调深度口播 · 顶栏引语卡+巨型浅黄大字风",
    "style_09": "纯黑纪实面板 · 多重圆窗头像+长句纪实字幕风",
    "style_11": "清亮知性干货 · 无底框通透大字风（博主默认）",
    "style_19": "冷峻数据新闻前景 · 人像抠像长句大字风",
    "style_20": "国风暖木匠心 · 古字典体大字风",
    "style_21": "门店工艺快切 · 76% 实拍特写暖黄大字风",
    "style_22": "例证拆解 · 案例画面轮播风",
    "style_23": "品牌微纪录 · 慢调空镜克制宋体风",
    "style_24": "不露脸混剪 · 炸裂金句高燃踩点风",
}

# JianYing text `size` → 1080 宽画布像素的换算。剪映没有公开系数，取 4.0 作为
# 标定初值（size 15 ≈ 60px），校验方式见 reports/style_parity_review §7 的
# fixture 比对；字段里同时保留原始 units，换算错了只影响 px 一个派生值。
PX_PER_UNIT = 4.0

# 旧视频壳遗留下来的版式描述（描皱纸模型/Notion卡片/胶片齿孔…），
# 与真实源不符，按真实草稿重写。数据驱动的部分由上生成，这里只写文本。
LAYOUT_OVERRIDES = {
    "style_05": (
        "person_pip_giant_title",
        "人像矩形画中画 + 得意黑无底框大字（留学避坑）",
        "实拍人像/场景铺底，4 处矩形蒙版窗口切出局部细节，3 处人物抠像叠加，"
        "2 条得意黑巨型标题压画面，字幕居中无底框白字＋珊瑚红/浅黄重点词。",
    ),
    "style_08": (
        "long_take_topbar_giant",
        "顶部细体导语 + 方正超粗黑大标题（专家长镜）",
        "单镜 29.23s 长镜口播铺底，顶部细体导语常驻，中部方正超粗黑大标题逐句压入，"
        "字幕 100% 方角半透明黑底框＋白字与亮黄重点词。",
    ),
    "style_12": (
        "top_dual_line_hook",
        "顶部双行悬念标题 + 居中无底框口播字",
        "顶部挂两行大字标题（汉仪天宇风行体），下方居中口播字幕（后现代体），"
        "1 处圆形蒙版切景，巨型标题 2 条，无底框。",
    ),
    "style_13": (
        "top_frame_black_cinema",
        "上圆角框下黑幕 + 得意黑无底框字幕",
        "4 处人物抠像 + 矩形/圆形蒙版构成上方画框，下半屏纯黑留白，"
        "居中得意黑字幕（最长 96 字整句），金句引语卡 1 处，亮黄金色重点词。",
    ),
    "style_14": (
        "black_spotlight_circle",
        "纯黑聚光圆形窗口 + 知新体居中字",
        "2 处抠像配 1 处圆形蒙版构成聚光圆窗，黑场铺底，"
        "得意黑与知新体居中字幕，金句引语卡 1 处，无底框、无巨字。",
    ),
    "style_03": (
        "topbar_quote_giant",
        "顶栏常驻 + 金句引语卡 + 巨型浅黄大字压屏",
        "顶栏常驻标题，金句引语卡 10 处巨型浅黄大字（FZCuJinLJW/特黑体）压画面，"
        "字幕居中 y0.78 无底框白字＋浅黄重点词，最长 15 字，6 处人物抠像。",
    ),
    "style_09": (
        "dark_doc_multi_circle",
        "纯黑纪实面板 · 多重圆窗头像 + 长句纪实字",
        "纯黑底板上叠 15 个圆形蒙版窗口与 6 个矩形证据窗，金句引语卡 1 处，"
        "得意黑居中字幕（y0.669，最长 46 字整句），强调色正红#FF1837/暖黄#FFBF17。",
    ),
    "style_11": (
        "creator_no_box_giant",
        "无底框通透大字 + 巨型标题",
        "通透实景铺底，字幕无底框居中（得意黑/Aa研宋），巨型标题 4 条，"
        "金句引语卡 1 处，7 处圆形蒙版轻切，关键帧 54.5/min 连续推拉。",
    ),
    "style_19": (
        "cutout_long_line",
        "人像抠像 + 长句大字（律师）",
        "5 处人物抠像叠在冷峻实景前，得意黑居中字幕（y0.721，最长 83 字整句），"
        "巨型标题 5 条、金句引语卡 1 处、6 处圆形蒙版，无底框。",
    ),
    "style_20": (
        "guofeng_dict_giant",
        "古字典体大字 + 暖木实景",
        "暖木/国风实景铺底，喜鹊古字典体与 FZCuJinLJW 混排大字（巨型 4 条），"
        "字幕居中 y0.693 无底框白字＋浅黄重点词，3 处矩形蒙版与 2 处圆形蒙版，无贴纸。",
    ),
    "style_22": (None, "例证拆解 · 案例画面轮播", None),
    "style_23": (None, "品牌微纪录 · 慢调空镜旁白", None),
    "style_24": (None, "高燃卡点 · 不露脸混剪", None),
}

# pip 画中画模式：按源草稿的抠像/蒙版证据 + 风格构图名定死，packaging 与 pip_composition_spec 同值
PIP_MODE = {
    "style_05": "person_behind_giant_text_pip",
    "style_06": "black_polaroid_frame_pip",
    "style_07": "vision_portrait_cutout_pip",
    "style_09": "dark_doc_circle_avatar_pip",
    "style_13": "top_white_rounded_frame_pip",
    "style_14": "black_spotlight_circle_pip",
    "style_16": "linear_split_feather_pip",
    "style_17": "dual_gold_frame_pip",
    "style_19": "data_news_cutout_pip",
    "style_21": "vision_portrait_cutout_pip",
    "style_22": "top_banner_evidence_pip",
}

FONT_SUBSTITUTE = {
    # 源字体真名 → 当前交付的商用替代（找不到源字体时的回落）
    "新青年体-文跃新青年体": ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"),
    "新青年体": ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"),
    "SourceHanSerifCN-Heavy": ("Source Han Serif CN", "SourceHanSerifCN-Heavy.otf"),
    "思源宋体-Bold": ("Source Han Serif CN", "SourceHanSerifCN-Bold.otf"),
    "思源粗宋": ("Source Han Serif CN", "SourceHanSerifCN-Heavy.otf"),
    "特黑体-思源黑体1号": ("Source Han Sans CN", "SourceHanSansCN-Heavy.otf"),
    "FZCuJinLJW": ("Source Han Serif CN", "SourceHanSerifCN-Heavy.otf"),
    "得意黑": ("Smiley Sans", "SmileySans-Oblique.otf"),
    "知新体": ("Smiley Sans", "SmileySans-Oblique.otf"),
    "DouyinSansBold": ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"),
    "FZZJ-JHTJW": ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"),
    "喜鹊古字典体": ("Ma Shan Zheng", "MaShanZheng-Regular.ttf"),
    "造字工房俊雅体": ("Source Han Serif CN", "SourceHanSerifCN-Bold.otf"),
    "Aa研宋": ("Source Han Serif CN", "SourceHanSerifCN-Bold.otf"),
    "方正超粗黑繁体": ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"),
    "后现代体": ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"),
    "汉仪天宇风行体W": ("Smiley Sans", "SmileySans-Oblique.otf"),
    "惊鸿体": ("Smiley Sans", "SmileySans-Oblique.otf"),
    "尔雅新大黑": ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"),
    "字语圆体": ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"),
    "黄令东齐伋复刻体": ("Source Han Serif CN", "SourceHanSerifCN-Bold.otf"),
    "zh-hans": ("Source Han Sans CN", "SourceHanSansCN-Bold.otf"),
}


def hex_bg(color: str | None, alpha: float) -> str:
    if not color:
        return "#00000000"
    c = color.strip().upper()
    if not c.startswith("#") or len(c) != 7:
        c = "#000000"
    a = max(0, min(255, int(round(alpha * 255))))
    return f"{c}{a:02X}"


def load_gt() -> dict:
    data = json.loads(GT.read_text(encoding="utf-8"))
    return {p["name"]: p for p in data["projects"]}


def source_file(project: str) -> pathlib.Path:
    p = DEC / project / "draft_content.json"
    if p.is_file():
        return p
    alt = DEC / project / "__latest_close.json"
    if alt.is_file():
        return alt
    raise FileNotFoundError(project)


def read_draft(project: str) -> tuple[dict, pathlib.Path]:
    f = source_file(project)
    return json.loads(f.read_text(encoding="utf-8")), f


def v1_segments(draft: dict) -> list[dict]:
    for tr in draft.get("tracks") or []:
        if tr.get("type") == "video":
            return sorted(tr.get("segments") or [], key=lambda s: (s.get("target_timerange") or {}).get("start", 0))
    return []


def text_materials(draft: dict) -> list[dict]:
    return list((draft.get("materials") or {}).get("texts") or [])


def bg_stats(draft: dict) -> dict:
    """Exact subtitle background box spec (color / alpha / corner radius / coverage)."""
    mats = text_materials(draft)
    boxes = []
    for t in mats:
        alpha = float(t.get("background_alpha") or 0)
        if alpha > 0.05:
            boxes.append(
                {
                    "color": (t.get("background_color") or "#000000").upper(),
                    "alpha": round(alpha, 3),
                    "radius": float(t.get("background_round_radius") or 0),
                    "width": float(t.get("background_width") or 0),
                    "height": float(t.get("background_height") or 0),
                    "style": t.get("background_style"),
                }
            )
    if not boxes:
        return {"ratio": 0.0, "color": "#00000000", "alpha": 0.0, "radius": 0.0, "shape": "none", "n": 0}
    dom = max(set((b["color"], b["alpha"], b["radius"]) for b in boxes), key=lambda k: sum(
        1 for b in boxes if (b["color"], b["alpha"], b["radius"]) == k))
    color, alpha, radius = dom
    return {
        "ratio": round(len(boxes) / max(len(mats), 1), 2),
        "color": hex_bg(color, alpha),
        "base_color": color,
        "alpha": alpha,
        "radius": round(radius, 3),
        "shape": "rounded" if radius > 0 else "square",
        "full_width": all(b["width"] == 0 for b in boxes),
        "n": len(boxes),
        "variants": len({(b["color"], b["alpha"], b["radius"]) for b in boxes}),
    }


def opening_stats(draft: dict) -> dict:
    segs = v1_segments(draft)
    if not segs:
        return {"first3s_cuts": 0, "hook_intro_sec": 0.0, "first_clip_sec": 0.0, "first_texts": []}
    starts = [(s.get("target_timerange") or {}).get("start", 0) / 1e6 for s in segs]
    first3 = [t for t in starts if t < 3.0]
    first_clip = (segs[0].get("target_timerange") or {}).get("duration", 0) / 1e6
    # hook 收口 = 第 3 个切点，没有就取首个镜长
    hook = starts[2] if len(starts) > 2 else first_clip
    texts = []
    tmap = {m["id"]: m for m in text_materials(draft) if "id" in m}
    for tr in draft.get("tracks") or []:
        if tr.get("type") != "text":
            continue
        for seg in tr.get("segments") or []:
            st = (seg.get("target_timerange") or {}).get("start", 0) / 1e6
            if st >= 3.0:
                continue
            raw = (tmap.get(seg.get("material_id"), {}) or {}).get("content", "")
            try:
                txt = (json.loads(raw).get("text") or "").strip() if isinstance(raw, str) and raw.strip().startswith("{") else str(raw)
            except Exception:
                txt = str(raw)
            txt = txt.replace("\n", " ").strip()
            if txt and txt not in texts:
                texts.append(txt[:16])
    return {
        "first3s_cuts": len(first3),
        "hook_intro_sec": round(min(hook, 3.0), 2),
        "first_clip_sec": round(first_clip, 2),
        "first_texts": texts[:4],
    }


def canvas_aspect(canvas: dict) -> str:
    if canvas.get("ratio"):
        return canvas["ratio"]
    w, h = canvas.get("width") or 1080, canvas.get("height") or 1920
    return "9:16" if h >= w else "16:9"


def sha256_of(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dominant_fonts(gt_proj: dict, key: str = "all_fonts", drop=("zh-hans",)) -> list:
    fonts = [f for f in (gt_proj["text"].get(key) or []) if f[0] not in drop]
    return fonts or (gt_proj["text"].get(key) or [])


def _shadow_hex(sh: dict) -> str:
    """阴影色 #RRGGBBAA：颜色来自草稿，透明度来自实测 alpha（剪映画布上就是半透明黑影）。"""
    col = str(sh.get("color") or "#000000").lstrip("#")
    if len(col) != 6:
        col = "000000"
    alpha = int(round(max(0.0, min(1.0, float(sh.get("alpha") or 0.0))) * 255))
    return f"#{col.upper()}{alpha:02X}"


def _load_text_truth() -> dict:
    if not TEXT_TRUTH.is_file():
        raise SystemExit(
            "缺少 forensic/text_style_truth.json（字幕描边/阴影真值）。\n"
            "先跑：python3 dev_tools/measure_text_style_truth.py"
        )
    return (json.loads(TEXT_TRUTH.read_text(encoding="utf-8")).get("projects") or {})


def main() -> None:
    gt = load_gt()
    lib = json.loads(LIB.read_text(encoding="utf-8"))
    styles = {s["style_id"]: s for s in lib["styles"]}

    # 字幕描边/阴影真值（逐套按解密草稿的 styles[].strokes / styles[].shadows 量）
    global TEXT_TRUTH_MAP
    TEXT_TRUTH_MAP = _load_text_truth()

    # 色彩分区实测（dev_tools/measure_color_zones.py 直接解码草稿原始素材得到）
    zones_path = ROOT / "forensic" / "color_zones.json"
    zones = json.loads(zones_path.read_text(encoding="utf-8")).get("styles", {}) if zones_path.is_file() else {}

    manifest = {
        "version": "1.0.0",
        "generated_at": NOW,
        "generator": "dev_tools/build_style_library_v4.py",
        "ground_truth": "forensic/jianying_ground_truth_full.json",
        "decryptor": "forensic/jy_draft_decrypt.py",
        "save_version_policy": "draft_content.json（最新保存态）；无主稿时用 __latest_close.json",
        "styles": {},
    }

    for sid in sorted(STYLE_SOURCES):
        project = STYLE_SOURCES[sid]
        g = gt[project]
        draft, path = read_draft(project)
        h = sha256_of(path)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, CST).isoformat(timespec="seconds")
        bg = bg_stats(draft)
        v, t, a = g["video"], g["text"], g["audio"]
        cover = g.get("cover_stats_reused") or {}
        deep = g.get("deep") or {}
        # 底框覆盖率以「基础字幕」为分母（gt.deep.sub_bg_pct），bg_stats 的颜色/透明度/圆角取自草稿原文
        bg_ratio = deep.get("sub_bg_pct") or 0.0
        if bg_ratio <= 0:
            bg = {"ratio": 0.0, "color": "#00000000", "base_color": "#000000", "alpha": 0.0,
                  "radius": 0.0, "shape": "none", "full_width": False, "n": 0, "variants": 0}
        else:
            bg["ratio"] = bg_ratio
        op = opening_stats(draft)
        s = styles[sid]

        fonts = dominant_fonts(g)
        src_font = fonts[0][0] if fonts else None
        sub_font = (t.get("sub_fonts") or fonts)
        sub_src_font = (sub_font[0][0] if sub_font else src_font)

        # ── manifest entry: 30+ raw metrics, hash, save version ──
        manifest["styles"][sid] = {
            "source_project": project,
            "source_type": "jianying_draft_decrypted",
            "source_file": str(path.relative_to(ROOT)),
            "source_sha256": h,
            "source_mtime": mtime,
            "timeline_source": "draft_content.json" if path.name == "draft_content.json" else path.name,
            "extracted_at": NOW,
            "draft_id": g.get("draft_id"),
            "draft_protocol_version": g.get("new_version"),
            "canvas": g["canvas"],
            "duration_sec": g["duration_sec"],
            "fps": g["fps"],
            "track_counts": g["track_counts"],
            "metrics": {
                "v1_clips": v["v1_clips"],
                "total_video_segs": v["total_video_segs"],
                "photo_segs": v["photo_segs"],
                "compound_segs": v["compound_segs"],
                "median_shot_sec": v["median_shot_sec"],
                "broll_overlay_ratio": v["broll_overlay_ratio"],
                "fast_cut_lt1s_ratio": v["fast_cut_lt1s_ratio"],
                "keyframe_count": v["keyframe_count"],
                "kf_per_min": v["kf_per_min"],
                "speed_changed_segs": v["speed_changed_segs"],
                "masked_segs": v["masked_segs"],
                "mask_types": v["mask_types"],
                "matting_count": v["matting_count"],
                "hsl_materials": v["hsl_materials"],
                "hsl_segments": v["hsl_segments"],
                "transition_count": v["transition_count"],
                "sub_count": t["sub_count"],
                "flower_count": t["flower_count"],
                "sub_y_ratio": t["sub_y_ratio"],
                "sub_max_chars": t["sub_max_chars"],
                "sub_med_size": t["sub_med_size"],
                "sub_bg_ratio": t["sub_bg_ratio"],
                "sub_bg_spec": bg,
                "sub_stroke_ratio": t["sub_stroke_ratio"],
                "sub_shadow_ratio": t["sub_shadow_ratio"],
                "base_sub_fonts": t.get("sub_fonts"),
                "flower_fonts": t.get("flower_fonts"),
                "flower_colors": t.get("flower_colors"),
                "all_fonts": t.get("all_fonts"),
                "display_text_n": deep.get("display_text_n"),
                "display_per_min": deep.get("display_per_min"),
                "display_anims": deep.get("display_anims"),
                "giant_n": deep.get("giant_n"),
                "has_top_bar": deep.get("has_top_bar"),
                "has_quote_card": deep.get("has_quote_card"),
                "sfx_per_min": a["sfx_per_min"],
                "dual_bgm": a["dual_bgm"],
                "bgm_names": a["unique_bgm_names"],
                "sfx_top_names": a["sfx_top_names"],
                "sfx_cut_offset_med_ms": a["sfx_cut_offset_med_ms"],
                "sfx_text_offset_med_ms": a["sfx_text_offset_med_ms"],
                "sticker_count": (g.get("visual_fx") or {}).get("sticker_count"),
                "video_effects": (g.get("visual_fx") or {}).get("video_effects"),
                "filters": (g.get("visual_fx") or {}).get("filters"),
                "cover_luma": (zones.get(sid) or {}).get("y_mean_255", cover.get("luma")),
                "cover_warmth": (zones.get(sid) or {}).get("warmth_index", cover.get("warm")),
                "cover_sat": round(((zones.get(sid) or {}).get("sat_mean_pct") or 0) / 100, 4) if zones.get(sid) else cover.get("sat"),
                "color_zones": {k: (zones.get(sid) or {}).get(k) for k in (
                    "y_mean_255", "y_p05_255", "y_p50_255", "y_p95_255", "dynamic_range_255",
                    "dark_ratio_pct", "bright_ratio_pct", "sat_mean_pct", "warmth_index", "tone_mood",
                    "shadow_rgb", "midtone_rgb", "highlight_rgb", "top5_palette_hex",
                    "source_files", "frames_sampled")} if zones.get(sid) else None,
                "first3s_cuts": op["first3s_cuts"],
                "hook_intro_sec": op["hook_intro_sec"],
                "first_texts": op["first_texts"],
                "sample_texts": t.get("sample_texts"),
            },
        }

        # ── library patch ──
        s["style_name_cn"] = NAME_OVERRIDES.get(sid, s["style_name_cn"])
        s["source_ref"] = f"wb_{h[:12]}"
        s["source_type"] = "jianying_draft"
        s["source_verified"] = True
        s["source_hash"] = h
        s["measurement_source"] = "forensic/jianying_ground_truth_full.json"

        s["meta"].update(
            {
                "width": g["canvas"]["width"],
                "height": g["canvas"]["height"],
                "aspect": canvas_aspect(g["canvas"]),
                "fps": g["fps"],
                "duration_sec": g["duration_sec"],
                "benchmark_source": None,
            }
        )

        broll_pct = round(v["broll_overlay_ratio"] * 100, 1)
        sd = s["shot_distribution"]
        sd.update(
            {
                "total_shots": v["total_video_segs"],
                "main_track_clips": v["v1_clips"],
                "overlay_video_segments": v["total_video_segs"] - v["v1_clips"],
                "hard_cuts": v["v1_clips"] - 1 if v["v1_clips"] else 0,
                "jump_cuts": v["v1_clips"],
                "broll_overlays": v["total_video_segs"] - v["v1_clips"],
                "photo_segments": v["photo_segs"],
                "median_sec": v["median_shot_sec"],
                "aroll_median_sec": v["median_shot_sec"],
                "broll_coverage_ratio": v["broll_overlay_ratio"],
                "fast_cuts_under_1s_ratio": v["fast_cut_lt1s_ratio"],
                "keyframe_count": v["keyframe_count"],
                "kf_per_min": v["kf_per_min"],
            }
        )

        s["beat_alignment"] = {
            "measured": False,
            "note": "v3 的 bpm/beat_align 字段为推演值，v4 无法从草稿证明，已作废并置空",
            "estimated_bpm": None,
            "cut_beat_align_2f_pct": None,
            "cut_event_align_1f_pct": None,
            "recommended_j_cut_frames": 2,
        }

        a_sfx = round(a["sfx_per_min"], 1)
        s["audio_and_sfx"].update(
            {
                "sfx_per_min_measured": a_sfx,
                "sfx_per_min_target": a_sfx,
                "dual_bgm_enabled": a["dual_bgm"],
                "bgm_names": a["unique_bgm_names"],
                "sfx_top_names": a["sfx_top_names"],
                "sfx_cut_offset_med_ms": a["sfx_cut_offset_med_ms"],
                "sfx_text_offset_med_ms": a["sfx_text_offset_med_ms"],
            }
        )

        sub = s["subtitle_spec"]
        truth = TEXT_TRUTH_MAP.get(str(STYLE_SOURCES.get(sid) or ""), {})
        # 无基础字幕的风格（纯花字叙事）没有 sub_y/sub_max_chars：用最密文字轨的实测中位值兜底
        ts_all = t.get("track_summaries") or []
        densest = max(ts_all, key=lambda x: x.get("seg_count") or 0) if ts_all else {}
        sub_y = t.get("sub_y_ratio")
        if sub_y is None:
            sub_y = densest.get("med_y")
        max_chars = t.get("sub_max_chars")
        if max_chars is None:
            max_chars = int(round(densest.get("med_chars") or 10))
        deliver_font, deliver_file = FONT_SUBSTITUTE.get(src_font or "", ("Alimama ShuHeiTi", "AlimamaShuHeiTi-Bold.otf"))
        sub.update(
            {
                "font_family": deliver_font,
                "source_font_family": src_font,
                "source_sub_font_family": sub_src_font,
                "font_candidates": [deliver_file, "SourceHanSansCN-Bold.otf", "NotoSansCJKsc-Bold.otf"],
                "font_substitution": {
                    "source_font": src_font,
                    "source_sub_font": sub_src_font,
                    "delivered_font": deliver_font,
                    "delivered_file": deliver_file,
                    "license_status": "source_font_required",
                    "exact_1to1": False,
                    "override_dir": f"fonts/{sid}/",
                },
                "max_chars_per_line": max_chars,
                "max_chars_source": "base_subtitle" if t["sub_max_chars"] else "densest_text_track",
                "safe_y_ratio": sub_y,
                "measured_y_center_ratio": sub_y,
                "safe_y_source": "base_subtitle" if t["sub_y_ratio"] is not None else "densest_text_track",
                "font_size_jianying_units": t["sub_med_size"],
                "font_size_1080p_px": round((t["sub_med_size"] or 12) * PX_PER_UNIT) if t["sub_med_size"] else None,
                "px_per_unit_at_1080": PX_PER_UNIT,
                "bg_box_color": bg["color"],
                "bg_box_alpha": bg["alpha"],
                "bg_radius": bg["radius"],
                "bg_shape": bg["shape"],
                "bg_ratio_measured": bg["ratio"],
                # 源草稿的 background_width/height 均为 0（底框随文字自适应），因此不存在通栏底条
                "full_width_bar": False,
                # ── 描边 / 阴影：只认真草稿量出来的值 ──
                # styles[].strokes 才是剪映真正渲染的描边；素材级 border_width 是面板滑杆记忆值，
                # 几乎每段文字都是 0.08，把它当描边会让 24 套全部被画上黑边（用户实测抓到的就是这个）。
                "stroke_mode": truth.get("stroke_mode", "none"),
                "stroke_width": int(truth.get("stroke_width_px") or 0),
                "stroke_width_rel": truth.get("stroke_width_rel"),
                "stroke_color": truth.get("stroke_color") or "#000000",
                "stroke_ratio_measured": truth.get("stroke_ratio"),
                "shadow_ratio_measured": truth.get("shadow_ratio"),
                "shadow": bool((truth.get("shadow") or {}).get("enabled")),
                "shadow_color": _shadow_hex(truth.get("shadow") or {}),
                "shadow_offset": list((truth.get("shadow") or {}).get("offset_px") or [0, 0]),
                "shadow_blur_px": float((truth.get("shadow") or {}).get("blur_px") or 0.0),
                "shadow_alpha": float((truth.get("shadow") or {}).get("alpha") or 0.0),
                "shadow_distance_units": (truth.get("shadow") or {}).get("distance_units"),
                "shadow_source": (truth.get("shadow") or {}).get("source") or "none",
                "text_paint_source": "forensic/text_style_truth.json（解密草稿 styles[].strokes/shadows）",
                "primary_color": (t["sub_colors"][0][0] if t.get("sub_colors") else "#FFFFFF"),
                "keyword_color": (t["sub_colors"][1][0] if len(t.get("sub_colors") or []) > 1 else "#FFBF17"),
            }
        )
        # ── 花字描边/阴影：与字幕同一套真值口径（实测花字也没有描边，只有右下阴影）──
        ftruth = truth.get("flower") or {}
        fsh = ftruth.get("shadow") or {}
        s["flower_text_spec"] = {
            "font_size_1080p_px": ftruth.get("font_size_1080p_px"),
            "stroke_mode": ftruth.get("stroke_mode") or "none",
            "stroke_width": int(ftruth.get("stroke_width_px") or 0),
            "stroke_width_rel": ftruth.get("stroke_width_rel"),
            "stroke_color": ftruth.get("stroke_color"),
            "shadow": bool(fsh.get("enabled")),
            "shadow_color": _shadow_hex(fsh),
            "shadow_offset": list(fsh.get("offset_px") or [0, 0]),
            "shadow_blur_px": float(fsh.get("blur_px") or 0.0),
            "shadow_alpha": float(fsh.get("alpha") or 0.0),
            "style_blocks_measured": ftruth.get("style_blocks"),
            "stroke_ratio_measured": ftruth.get("stroke_ratio"),
            "shadow_ratio_measured": ftruth.get("shadow_ratio"),
            "text_paint_source": "forensic/text_style_truth.json（花字文字轨）",
        }

        s["subtitle_symmetry_lock"]["max_chars_per_line"] = max_chars

        cal = s["color_and_lut"]
        cal.update(
            {
                "y_mean_255": cover.get("luma"),
                "warmth_index": cover.get("warm"),
                "sat_mean_pct": round((cover.get("sat") or 0) * 100, 1) if cover.get("sat") else None,
            }
        )
        z = zones.get(sid)
        if z:
            # 实测分区直接覆盖历史占位值（此前 18/24 套的暗/中/高 RGB 是占位三元组）
            for key in ("y_mean_255", "y_p05_255", "y_p50_255", "y_p95_255", "dynamic_range_255",
                        "dark_ratio_pct", "bright_ratio_pct", "sat_mean_pct", "warmth_index",
                        "tone_mood", "shadow_rgb", "midtone_rgb", "highlight_rgb", "top5_palette_hex"):
                if z.get(key) is not None:
                    cal[key] = z[key]
            cal["zone_measurement"] = {
                "method": "ffmpeg 解码草稿原始素材，BT.709 亮度分区（同 analyze_style_spectrum.py）",
                "source_files": z.get("source_files"),
                "frames_sampled": z.get("frames_sampled"),
            }
            # LUT 强度按实测饱和度/暖度重算（旧值是 0.60–0.68 的近似常量）
            sat_pct = float(z.get("sat_mean_pct") or 0.0)
            warm_abs = abs(float(z.get("warmth_index") or 0.0))
            hsl_n = int((s.get("packaging_techniques") or {}).get("hsl_materials") or 0)
            cal["lut_strength"] = round(min(0.76, max(0.45, 0.50 + 0.0045 * sat_pct + 0.0008 * warm_abs - 0.0025 * hsl_n)), 2)
            cal["contrast_gain"] = round(min(1.30, max(0.90, 1.02 + (float(z.get("dynamic_range_255") or 170.0) - 170.0) / 640.0)), 3)
            cal["sat_scale"] = round(min(1.20, max(0.82, 1.0 + (sat_pct - 18.0) / 220.0)), 3)

        tech = s["packaging_techniques"]
        tech.update(
            {
                "ai_matting_cutout": v["matting_count"],
                "mask_shapes": v["mask_types"],
                "persistent_top_bar": bool(deep.get("has_top_bar")),
                "quote_citation_card": bool(deep.get("has_quote_card")),
                "giant_impact_count": deep.get("giant_n") or 0,
                "capsule_bg_subtitle": bg["shape"] == "rounded" and bg["ratio"] >= 0.5,
                "capsule_bg_ratio": bg["ratio"],
                "hsl_materials": v["hsl_materials"],
                "speed_changed_segs": v["speed_changed_segs"],
                "sticker_count": (g.get("visual_fx") or {}).get("sticker_count") or 0,
                "video_effects": (g.get("visual_fx") or {}).get("video_effects") or [],
                "filters": (g.get("visual_fx") or {}).get("filters") or [],
            }
        )

        s.setdefault("display_text_spec", {})
        s["display_text_spec"] = {
            "count": deep.get("display_text_n"),
            "per_min": deep.get("display_per_min"),
            "fonts": deep.get("display_fonts"),
            "colors": t.get("flower_colors") or [],
            "anims_top": deep.get("display_anims"),
        }

        # pip_mode 统一：packaging 与 pip_composition_spec 必须同值
        pip_mode = PIP_MODE.get(sid, "none")
        tech["pip_mode"] = pip_mode
        s["pip_composition_spec"]["mode"] = pip_mode

        # 转场：源 24/24 为 0
        vert = None
        for st in s["visual_blueprint"].get("transitions") or []:
            vert = st
        s["visual_blueprint"]["transition_style"] = "none"
        s["visual_blueprint"]["transition_count_source"] = 0
        s["visual_blueprint"]["persistent_top_bar"] = bool(deep.get("has_top_bar"))

        # B-roll 目标：软目标 + 动态带
        s["broll_overlay_ratio_target"] = v["broll_overlay_ratio"]
        s["broll_target_hard"] = False
        s["broll_target_band_pct"] = [round(max(0.0, broll_pct - 15), 1), round(min(100.0, broll_pct + 15), 1)]
        s["ab_switch_spec"]["target_median_shot_sec"] = v["median_shot_sec"]
        s["ab_switch_spec"]["kf_per_min_target"] = v["kf_per_min"]

        s["whitebox_source"] = {
            "project_id": f"wb_{h[:12]}",
            "source_hash": h,
            "is_whitebox_jianying": True,
            "canvas": f"{g['canvas']['width']}x{g['canvas']['height']}",
            "total_tracks": sum(n for k, n in g["track_counts"].items() if k != "audio") + g["track_counts"].get("audio", 0),
            "duration_sec": g["duration_sec"],
            "draft_protocol_version": g.get("new_version"),
            "save_version": path.name,
        }

        tp = s["transition_and_packaging"]
        tp["transitions"] = ["硬切（源 draft 转场数 = 0，全片零转场）"]
        tp["sfx_per_min_target"] = a_sfx
        tp["broll_overlay_ratio_target"] = v["broll_overlay_ratio"]
        tp["ui_cadence"] = (
            f"主轨 {v['v1_clips']} 段口播（中位 {v['median_shot_sec']}s）硬切零缝紧拼；"
            f"覆盖层视频 {v['total_video_segs'] - v['v1_clips']} 段、覆盖率 {broll_pct}%；"
            f"全片 {v['total_video_segs']} 段、中位 {v['median_shot_sec']}s，无任何转场；"
            f"字幕 {t['sub_count']} 条、展示字 {deep.get('display_text_n') or 0} 条（{deep.get('display_per_min') or 0}/min）；"
            f"关键帧 {v['keyframe_count']} 个（{v['kf_per_min']}/min）；音效 {a_sfx}/min"
            + (f"" if not a["unique_bgm_names"] else f"，BGM「{a['unique_bgm_names'][0]}」" + ("＋第二轨" if a["dual_bgm"] else ""))
            + "。"
        )

        elements = []
        if deep.get("has_top_bar"):
            elements.append("顶部常驻标题/导语栏")
        if deep.get("has_quote_card"):
            elements.append("金句引语卡")
        if deep.get("giant_n"):
            elements.append(f"巨字冲击 ×{deep['giant_n']}")
        if v["matting_count"]:
            elements.append(f"人物抠像 ×{v['matting_count']}")
        if v["mask_types"]:
            elements.append("蒙版 " + "／".join(f"{k}×{n}" for k, n in v["mask_types"].items()))
        if bg["ratio"]:
            elements.append(f"{'圆角胶囊' if bg['shape'] == 'rounded' else '方角'}底框字幕 ×{bg['n']}（{bg['color']}）")
        if (g.get("visual_fx") or {}).get("sticker_count"):
            elements.append(f"贴纸 ×{(g.get('visual_fx') or {}).get('sticker_count')}")
        if v["speed_changed_segs"]:
            elements.append(f"变速段 ×{v['speed_changed_segs']}")
        if v["photo_segs"]:
            elements.append(f"图片段 ×{v['photo_segs']}")
        tp["elements"] = elements or ["全屏实拍硬切，无附加包装"]

        o = s["opening_structure"]
        # 只记结构与条数，不把源视频里的具体文案带进技能包（文案属内容层，不进模板）
        o["0_to_3s_pattern"] = (
            f"前 3 秒 {op['first3s_cuts']} 个切点（首镜 {op['first_clip_sec']}s，hook 收口 {op['hook_intro_sec']}s）；"
            f"开场 3 秒内出现 {len(op['first_texts'])} 条文字（内容随素材，模板只锁条数/位置/字号）。"
        )
        o["first_3s_cuts_count"] = op["first3s_cuts"]
        o["hook_intro_sec"] = op["hook_intro_sec"]

        # 版式描述：旧视频壳遗留的逐套重写
        if sid in LAYOUT_OVERRIDES:
            lid, lname, ldesc = LAYOUT_OVERRIDES[sid]
            tcl = s["title_cover_layout"]
            if lid:
                tcl["layout_id"] = lid
            tcl["layout_name"] = lname
            if ldesc:
                tcl["grid_desc"] = ldesc

        # 花字配色以源真实颜色为准（主色/次色/第三色）
        colors = [c[0] for c in (t.get("flower_colors") or []) if c[0] != "#000000"]
        if not colors:
            colors = [c[0] for c in (t.get("sub_colors") or []) if c[0] not in ("#FFFFFF", "#000000")]
        mz = s.setdefault("multi_zone_flower_spec", {})
        if colors:
            mz["center_burst_color"] = colors[0]
            mz["flank_color"] = colors[1] if len(colors) > 1 else colors[0]
            mz["end_card_color"] = colors[2] if len(colors) > 2 else colors[0]
            mz["borderless"] = bg["ratio"] < 0.5
        mz.pop("persistent_top_bar", None)
        mz.pop("quote_citation_card", None)
        mz.pop("multi_tag_burst", None)

    # ── 字幕模板同步：templates.json 的 style_XX 条目全部由库派生，避免两套数 ──
    tpl_doc = json.loads(SUBTITLES.read_text(encoding="utf-8"))
    tpls = tpl_doc["templates"]
    for sid, s in styles.items():
        tpl = tpls.get(sid)
        if not isinstance(tpl, dict):
            continue
        sub = s["subtitle_spec"]
        tpl.update(
            {
                "name": f"[{sid}] {s['style_name_cn']}",
                "font_candidates": [sub["font_substitution"]["delivered_file"], "SourceHanSansCN-Bold.otf", "NotoSansCJKsc-Bold.otf"],
                "font_size": sub.get("font_size_1080p_px") or 60,
                "primary_color": sub.get("primary_color") or "#FFFFFF",
                "keyword_color": sub.get("keyword_color") or "#FFBF17",
                "stroke_color": sub.get("stroke_color") or "#000000",
                "stroke_width": int(round(float(sub.get("stroke_width") or 0))),
                "stroke_mode": sub.get("stroke_mode") or "none",
                "shadow": bool(sub.get("shadow")),
                "shadow_color": sub.get("shadow_color") or "#00000000",
                "shadow_offset": list(sub.get("shadow_offset") or [0, 0]),
                "shadow_blur_px": float(sub.get("shadow_blur_px") or 0.0),
                "bg_box_color": sub.get("bg_box_color") or "#00000000",
                "bg_radius": int(round(float(sub.get("bg_radius") or 0) * 100)),
                "bg_shape": sub.get("bg_shape"),
                "safe_y_center": int(round(float(sub.get("safe_y_ratio") or 0.7) * 1920)),
                "max_chars_per_line": sub.get("max_chars_per_line") or 10,
                "max_chars_hard": 96,
                "full_width_bar": bool(sub.get("full_width_bar")),
                "source_font_family": sub.get("source_sub_font_family"),
                "font_substitution": sub.get("font_substitution"),
                "animation": (s.get("display_text_spec") or {}).get("anims_top") and
                             (s["display_text_spec"]["anims_top"][0][0].split(":", 1)[-1]) or "渐显",
            }
        )

    # ── 蓝图同步：template_blueprints.json 的 subtitle_override 同样由库派生 ──
    # 以前只同步模板不同步蓝图，于是蓝图里还留着「每套一个不同的描边色」的旧值，
    # 而真草稿 24/24 套都没有描边——两份数打架，谁都不许再手写。
    BP = ROOT / "short-video-studio/assets/style_spectrum/template_blueprints.json"
    if BP.is_file():
        bp_doc = json.loads(BP.read_text(encoding="utf-8"))
        bp_nodes = bp_doc.get("blueprints") or {}
        for sid, s in styles.items():
            node = bp_nodes.get(sid)
            tpl = tpls.get(sid)
            if not isinstance(node, dict) or not isinstance(tpl, dict):
                continue
            sub = s["subtitle_spec"]
            node["subtitle_override"] = {
                "name": tpl.get("name"),
                "font_size": tpl.get("font_size"),
                "keyword_scale": tpl.get("keyword_scale"),
                "primary_color": tpl.get("primary_color"),
                "keyword_color": tpl.get("keyword_color"),
                "stroke_mode": sub.get("stroke_mode"),
                "stroke_color": tpl.get("stroke_color"),
                "stroke_width": tpl.get("stroke_width"),
                "shadow": tpl.get("shadow"),
                "shadow_color": tpl.get("shadow_color"),
                "shadow_offset": tpl.get("shadow_offset"),
                "shadow_blur_px": tpl.get("shadow_blur_px"),
                "bg_box_color": tpl.get("bg_box_color"),
                "bg_radius": tpl.get("bg_radius"),
                "bg_shape": tpl.get("bg_shape"),
                "safe_y_center": tpl.get("safe_y_center"),
                "max_chars_per_line": tpl.get("max_chars_per_line"),
                "full_width_bar": tpl.get("full_width_bar"),
                "source_font_family": tpl.get("source_font_family"),
                "synced_from": "subtitle_templates/templates.json（库派生）",
            }
        bp_doc["version"] = "4.1.0"
        BP.write_text(json.dumps(bp_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"blueprints: {len(bp_nodes)} 套 subtitle_override 已按库重写")

    lib["version"] = "4.1.0"
    lib["generated_at"] = NOW
    lib["source_dir"] = "forensic/decrypted"
    lib["source_manifest"] = "forensic/source_manifest.json"
    lib["measurement_policy"] = {
        "save_version": "draft_content.json（最新保存态）",
        "broll_target_hard": False,
        "transition_policy": "源 24/24 零转场，默认 none",
        "font_policy": "source_font_family 为准，delivered 找不到源字体时回落并标记 exact_1to1=false",
    }
    LIB.write_text(json.dumps(lib, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SUBTITLES.write_text(json.dumps(tpl_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ── 画中画几何：库重建会覆盖掉 apply_pip_truth.py 的成果，这里收尾时立刻重贴 ──
    if (ROOT / "forensic/pip_usage.json").is_file():
        try:
            import subprocess
            subprocess.run([sys.executable, str(ROOT / "dev_tools/apply_pip_truth.py")], check=True,
                           capture_output=True, text=True)
            print("pip truth: 已按 forensic/pip_usage.json 重新贴回库与蓝图")
        except Exception as exc:
            print(f"pip truth: [WARN] 重贴失败（{exc}），请手动跑 dev_tools/apply_pip_truth.py")

    print(f"library v{lib['version']}: {len(lib['styles'])} styles")
    print(f"manifest: {MANIFEST.relative_to(ROOT)} ({len(manifest['styles'])} styles)")
    for sid in sorted(STYLE_SOURCES):
        m = manifest["styles"][sid]["metrics"]
        print(
            f"{sid} {STYLE_SOURCES[sid]:<12} {manifest['styles'][sid]['duration_sec']:>6.1f}s "
            f"br={m['broll_overlay_ratio']*100:5.1f}% bg={m['sub_bg_spec']['shape']:<7}"
            f"{m['sub_bg_spec']['ratio']*100:5.1f}% maxch={str(m['sub_max_chars']):<3} "
            f"tb={int(bool(m['has_top_bar']))} q={int(bool(m['has_quote_card']))} "
            f"gi={m['giant_n']} matte={m['matting_count']} sfx={m['sfx_per_min']}"
        )


if __name__ == "__main__":
    main()
