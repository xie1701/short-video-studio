#!/usr/bin/env python3
"""复刻模版提取器：把一条真实剪辑草稿提炼成**与素材无关**的结构模版。

模版只描述「结构」——轨序/角色、每段的 slot 与几何、关键帧与动画的段内相对比例、
文字位容量、素材角色映射、时长重映射所需的锚点。它**不含**任何素材路径、字体路径、
本机绝对路径、草稿名，也不含源文案；源草稿只留一枚脱敏哈希（`source_ref`）。

    模版 JSON 结构（v1）
    -------------------
    duration_sec / fps / canvas            画布与时长
    tracks[]                               role / track_type / render_index（数组序即 z 序）/ 段数 / 覆盖时长
    segments[]                             role / slot / start_ratio / duration_ratio /
                                           geometry{scale,transform,crop,size_ratio,cover_ratio} /
                                           keyframes（**段内相对比例时间**）/ animations（名字+时长+段内比例）/
                                           mask / effects / text_slot
    text_slots[]                           文字位：字号/颜色/描边/阴影/底框 + **字体身份**（名字/扩展名/
                                           文件名指纹，不含路径）+ **按版式算出的容量**（每行字数×可容行数）
    asset_roles{素材 id -> 角色}            a_roll / b_roll / overlay_card / sticker / photo /
                                           font / bgm / sfx / unknown
    counts{}                               unknown 计数与逐条原因（不瞎猜）
    requires_review[]                      结构里没法与素材无关表达的部分，如实标记

推断不出的素材角色一律记 `unknown` 并计数，绝不按位置猜成某个角色。

用法：
    python3 scripts/replica_template.py --style-id style_21 --out /tmp/replica_tpl.json
    python3 scripts/replica_template.py --draft <草稿目录或 draft_content.json> --out ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

US = 1_000_000
TEMPLATE_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
DECRYPTED_ROOT = ROOT / "forensic" / "decrypted"
LIBRARY_PATH = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "style_spectrum_library.json"

ROLES = ("a_roll", "b_roll", "overlay_card", "sticker", "photo", "font", "bgm", "sfx", "unknown")

# ---- 文字容量：与渲染器同一套字号换算 ----
# 渲染器 render_text_png() 的口径是 font_px = font_size(草稿单位) × 画布宽/172.8。
# 172.8 是实测常数（style_21 字幕字号 192 单位在 1080 宽画布上实测墨迹宽 517px vs 468px，
# 两个方向都差 10.5%，与 192/172.8=1.111 严丝合缝）。容量必须按这条换算推，
# 不能按源文案长度推。
PX_PER_UNIT = 172.8
DEFAULT_LINE_MAX_WIDTH = 0.82  # 草稿 material.line_max_width 缺省（套用端写回时也是这个值）
MIN_TEXT_LINES = 2             # 字幕位至少允许 2 行：源草稿的 1 行 ≠ 版式只容得下 1 行
MAX_TEXT_LINES = 3             # 上限 3 行：再多就把竖屏画面吃掉
FONT_EXTS = (".ttf", ".otf", ".ttc")


# ---------------------------------------------------------------- 小工具


def _us_to_sec(us: Any) -> float:
    try:
        return max(0.0, float(us) / US)
    except (TypeError, ValueError):
        return 0.0


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ratio(value: float, base: float, ndigits: int = 6) -> float:
    """比例时间统一 6 位小数（模版是「结构」，不是帧级真值）。"""
    if base <= 1e-9:
        return 0.0
    return round(max(0.0, float(value)) / float(base), ndigits)


def _clip_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 复用渲染器的解析口径


def _replica_module():
    """按需 import 只读复用的`渲染器`（解析文字样式/遮罩配置的口径与它保持一致）。"""
    if str(ROOT / "dev_tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "dev_tools"))
    import render_replica_from_draft as R  # noqa: E402  (只读引用，不改)
    return R


# ---------------------------------------------------------------- 几何


def layer_size_ratio(mat: dict, canvas_w: float, canvas_h: float, scale: float) -> Optional[List[float]]:
    """图层显示尺寸占画布比（宽, 高）：素材声明像素 → crop(uv) → contain(画布) → ×scale。

    与渲染器 `layer_geometry()` 同一条算式，但只用草稿里声明的像素宽高，
    不需要读素材文件——模版必须能在素材缺席时独立成立。
    """
    sw = _clip_float(mat.get("width")) or 0.0
    sh = _clip_float(mat.get("height")) or 0.0
    if sw <= 0 or sh <= 0:
        return None
    crop = mat.get("crop") or {}

    def _v(key: str, default: float) -> float:
        val = _clip_float(crop.get(key))
        return default if val is None else val

    x0, x1 = _v("upper_left_x", 0.0), _v("upper_right_x", 1.0)
    y0, y1 = _v("upper_left_y", 0.0), _v("lower_left_y", 1.0)
    x0, x1 = max(0.0, min(x0, x1)), min(1.0, max(x0, x1))
    y0, y1 = max(0.0, min(y0, y1)), min(1.0, max(y0, y1))
    cw, ch = max(1.0, (x1 - x0) * sw), max(1.0, (y1 - y0) * sh)
    sc = float(scale) if float(scale or 0) > 0 else 1.0
    fit = min(canvas_w / cw, canvas_h / ch)
    return [round(cw * fit * sc / canvas_w, 4), round(ch * fit * sc / canvas_h, 4)]


def _crop_uv(mat: dict) -> Optional[Dict[str, float]]:
    """有意义的 crop（减掉了像素）才记；整幅 crop 等价于无 crop。"""
    crop = mat.get("crop") or {}
    if not crop:
        return None
    x0 = _clip_float(crop.get("upper_left_x")) or 0.0
    x1 = _clip_float(crop.get("upper_right_x"))
    y0 = _clip_float(crop.get("upper_left_y")) or 0.0
    y1 = _clip_float(crop.get("lower_left_y"))
    x1 = 1.0 if x1 is None else x1
    y1 = 1.0 if y1 is None else y1
    if (x0, y0, x1, y1) == (0.0, 0.0, 1.0, 1.0):
        return None
    return {"x0": round(x0, 4), "y0": round(y0, 4), "x1": round(x1, 4), "y1": round(y1, 4)}


# ---------------------------------------------------------------- 文字样式


def text_style_of(mat: dict) -> dict:
    """文字素材 → 与素材/文案无关的样式（字号/颜色/描边/阴影/底框）。

    `material.font_size` 对识别字幕是 None，真值在 `content.styles[0].size`；
    这里借渲染器的 `parse_text_material()` 解析，保证「提取出来的样式」与
    「渲染时读到的样式」是同一口径（否则套用端写回去的字会走样）。
    """
    R = _replica_module()
    spec = R.parse_text_material(mat)
    content: Dict[str, Any] = {}
    raw = mat.get("content")
    if isinstance(raw, str) and raw.lstrip().startswith("{"):
        try:
            content = json.loads(raw)
        except Exception:
            content = {}
    st0 = (content.get("styles") or [{}])[0]
    strokes = st0.get("strokes") or []
    stroke: Optional[dict] = None
    if spec.get("stroke"):
        stroke = {
            "width_units": _clip_float((strokes[0] if strokes else {}).get("width")),
            "px": spec["stroke"]["px"],
            "color": list(spec["stroke"]["color"]),
        }
    shadow: Optional[dict] = None
    if mat.get("has_shadow"):
        sp = mat.get("shadow_point") or {}
        shadow = {
            "point": [_clip_float(sp.get("x")) or 0.0, _clip_float(sp.get("y")) or 0.0],
            "distance": _clip_float(mat.get("shadow_distance")) or 0.0,
            "smoothing": _clip_float(mat.get("shadow_smoothing")) or 0.0,
            "alpha": float(mat.get("shadow_alpha") if mat.get("shadow_alpha") is not None else 0.9),
            "color": list(spec.get("shadow", {}).get("color") or (0, 0, 0)),
        }
    bg = None
    if mat.get("background_style"):
        b = mat.get("background") or {}
        bg = {
            "style": int(mat.get("background_style") or 1),
            "color": mat.get("background_color") or "#000000",
            "alpha": float(mat.get("background_alpha") if mat.get("background_alpha") is not None else 1.0),
            "rgb": list(b.get("color") or (0, 0, 0)),
        }
    return {
        "font_size_units": round(float(spec.get("font_size") or 0.0), 4),
        "color": list(spec.get("color") or (255, 255, 255)),
        "bold": bool(mat.get("bold")),
        "stroke": stroke,
        "shadow": shadow,
        "background": bg,
        "global_alpha": _clip_float(mat.get("global_alpha")),
        "line_spacing": _clip_float(mat.get("line_spacing")) or 0.0,
        "alignment": mat.get("alignment"),
        # 行盒可用宽度（占画布宽比例）：草稿里通常缺省，缺省即渲染器/套用端的 0.82。
        # 容量与换行都要它，跟着样式一起搬，别让套用端再猜一次。
        "line_max_width": _clip_float(mat.get("line_max_width")),
    }


def font_ref_of(mat: dict) -> Optional[dict]:
    """文字素材 → 字体身份（**只留名字/扩展名/文件名指纹，绝不留路径**）。

    原来什么：`text_style_of()` 只搬字号/颜色/描边，字体一律丢掉，`review_reasons` 写死
    `font_not_portable`。结果套用端 24 个文字位全落一种系统黑体（Hiragino Sans GB），
    跟原版的俊雅体/思源粗宋完全不是一个观感——而这两个字体本机其实都有。

    改成什么：从 `content.styles[*].font.path` 取 **basename**。识别字幕的真值在这里；
    材质级 `mat.font_name` 是 null，`mat.fonts[].path` 是另一个用户目录的副本。

    为什么只留名字：模版要跨机器/跨平台复用，「Windows 用户目录 + AppData + 应用缓存」这类
    路径既是隐私又是死路径；字体**文件名**（如 `造字工房俊雅体.ttf`）才是可移植的身份，
    套用时按名字在本机就近解析（见 apply_replica_template.resolve_template_font）。

    返回 {name, ext, sha12, styles_with_font, distinct}；草稿真没写字体则返回 None。
    sha12 是「名字+扩展名」的短指纹，用来跨机器比对是不是同一个字体，不含任何目录信息。
    """
    raw = mat.get("content")
    content: Dict[str, Any] = {}
    if isinstance(raw, str) and raw.lstrip().startswith("{"):
        try:
            content = json.loads(raw)
        except Exception:
            content = {}

    def _split(path: str) -> Optional[tuple]:
        base = str(path or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not base:
            return None
        stem, _, ext = base.rpartition(".")
        if not stem or ("." + ext.casefold()) not in FONT_EXTS:
            return (base, "")
        return (stem, "." + ext.casefold())

    names: List[tuple] = []
    for st in content.get("styles") or []:
        got = _split(str((st.get("font") or {}).get("path") or ""))
        if got:
            names.append(got)
    if not names:
        # 兜底：材质级 fonts[] 里也有同一个文件（路径不同，文件名相同）
        for f in mat.get("fonts") or []:
            got = _split(str((f or {}).get("path") or ""))
            if got:
                names.append(got)
                break
    if not names:
        return None
    name, ext = names[0]
    fingerprint = hashlib.sha256((name + ext).casefold().encode("utf-8")).hexdigest()[:12]
    return {"name": name, "ext": ext, "sha12": fingerprint,
            "styles_with_font": len(names), "distinct": len({n for n, _ in names})}


def text_layout_capacity(style: dict, canvas_w: float, src_lines: int = 1) -> dict:
    """文字位容量：**由版式算**，不再拿源文案长度当上限。

    原来什么：`char_cap = max(2, len(源文案))`、`max_lines = 源文案行数`（这 24 位全是 1 行），
    新文案只要比源文案长一个字就被截成「…」，上一轮 19/24 位被截。这是把「源文案碰巧多长」
    当成了「版式能放多少」，两件事根本不是一回事。

    改成什么：
        可用宽度 = line_max_width × 画布宽
        单字宽   ≈ font_size_units × 画布宽 / 172.8
        每行字数 = floor(可用宽度 / 单字宽)
        可容行数 = 字幕位 2~3 行（源只有 1 行是「源文案只有 1 行」，不是版式放不下 2 行）
        容量     = 每行字数 × 可容行数

    为什么这样算：渲染器就是按 fontSize×画布宽/172.8 出字的，容量越过这条换算就会
    「写得下但渲染溢出」。每行只放得下 1 个字的大字位（78 号），容量天然是 2——那是
    **设计容量**（原版放的就是单个大字），不是 bug。
    """
    font_units = float(style.get("font_size_units") or 0.0)
    lmw = _clip_float(style.get("line_max_width"))
    line_max_width = lmw if (lmw and 0.0 < lmw <= 1.0) else DEFAULT_LINE_MAX_WIDTH
    usable = line_max_width * float(canvas_w)
    char_px = font_units * float(canvas_w) / PX_PER_UNIT if font_units > 0 else 0.0
    per_line = max(1, int(usable // char_px)) if char_px > 0 else 12
    lines = min(MAX_TEXT_LINES, max(MIN_TEXT_LINES, int(src_lines or 1)))
    return {
        "capacity_per_line": per_line,
        "capacity_lines": lines,
        "capacity": per_line * lines,
        "line_max_width": round(line_max_width, 4),
        "char_px": round(char_px, 1),
        "basis": "layout",
    }


# ---------------------------------------------------------------- 轨道/素材角色


def track_role(track_type: Optional[str], flag: Any, *, is_main: bool = False) -> str:
    tt = str(track_type or "")
    if tt == "video":
        if int(flag or 0) == 2:
            return "b_roll"
        return "a_roll" if is_main else "b_roll"
    return {
        "text": "text",
        "sticker": "sticker",
        "effect": "effect",
        "audio": "audio",
    }.get(tt, "unknown")


def _material_index(mats: dict) -> Dict[str, dict]:
    idx: Dict[str, dict] = {}
    for bucket, kind in (("videos", "video"), ("audios", "audio"), ("texts", "text"),
                         ("stickers", "sticker"), ("drafts", "compound")):
        for m in mats.get(bucket) or []:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            entry = dict(m)
            entry.setdefault("_bucket", bucket)
            entry["_kind"] = kind
            idx[m["id"]] = entry
    return idx


def _audio_track_is_music(segs: list, total_sec: float) -> bool:
    """音乐轨 vs 音效轨：靠「单段最长」而不是「轨覆盖总长」。

    一条音效轨可以由十几条 0.1~2s 的音效首尾铺满全片（覆盖 100% 时长），
    用覆盖时长判会把音效轨误判成 BGM。BGM 的特征是**单段就铺满大半片**。
    """
    longest = max((_us_to_sec((s.get("target_timerange") or {}).get("duration")) for s in segs), default=0.0)
    return total_sec > 0 and longest >= 0.5 * total_sec


def infer_asset_role(mat: dict, *, track_role_of_seg: str, cover_ratio: Optional[float],
                     audio_is_music: bool) -> str:
    """素材角色推断（保守：只在证据够时下结论，否则 unknown）。

    · 文字素材 → font（文字位靠的是字体/样式，不是视频素材）
    · 贴纸素材 → sticker
    · 音频素材 → 该轨整条覆盖 ≥60% 时长按 bgm，否则 sfx；复合片段音频 unknown
    · 视频素材 → 主轨 a_roll；叠加轨上显示面积 <55% 画布按 overlay_card，否则 b_roll
    · 静图 → photo；复合片段 → overlay_card（有可见内容）/ unknown（纯音频）
    """
    kind = mat.get("_kind")
    if kind == "text":
        return "font"
    if kind == "sticker":
        return "sticker"
    if kind == "audio":
        if not (mat.get("path") or ""):
            return "unknown"
        return "bgm" if audio_is_music else "sfx"
    if kind == "compound":
        sub = mat.get("draft") or {}
        visible = any((t.get("segments") or []) for t in sub.get("tracks") or []
                      if t.get("type") in ("video", "text", "sticker"))
        if not visible:
            return "unknown"
        return "overlay_card"
    if kind == "video":
        vtype = str(mat.get("type") or "")
        if vtype == "photo":
            return "photo"
        if not (mat.get("path") or ""):
            return "unknown"  # 无路径 = 复合片段占位，真身不在 videos 里
        if track_role_of_seg == "a_roll":
            return "a_roll"
        if track_role_of_seg == "b_roll":
            if cover_ratio is not None and cover_ratio < 0.55:
                return "overlay_card"
            return "b_roll"
        return "unknown"
    return "unknown"


# ---------------------------------------------------------------- 主入口


def extract_template(draft_path: Path | str, *, require_plaintext: bool = True) -> dict:
    """一条草稿 → 与素材无关的结构模版 dict。"""
    path = Path(draft_path)
    if path.is_dir():
        path = path / "draft_content.json"
    if not path.is_file():
        raise FileNotFoundError(f"草稿内容文件不存在: {path}")
    raw_bytes = path.read_bytes()
    try:
        draft = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:  # 加密草稿：要求先落到明文快照目录
        if require_plaintext:
            raise ValueError(f"草稿内容不是明文 JSON（需先用解密快照）: {exc}") from exc
        raise

    mats = draft.get("materials") or {}
    mat_idx = _material_index(mats)
    anim_by_id = {a.get("id"): a for a in mats.get("material_animations") or [] if isinstance(a, dict)}
    vfx_by_id = {e.get("id"): e for e in mats.get("video_effects") or [] if isinstance(e, dict)}
    mask_by_id = {mk.get("id"): mk for mk in mats.get("common_mask") or [] if isinstance(mk, dict)}

    canvas = draft.get("canvas_config") or {}
    canvas_w = float(canvas.get("width") or 1080)
    canvas_h = float(canvas.get("height") or 1920)
    fps = float(draft.get("fps") or 30.0)

    tracks_raw = draft.get("tracks") or []
    # 时长：草稿声明优先，否则取所有段尾最大值
    total_sec = _us_to_sec(draft.get("duration"))
    if total_sec <= 0:
        ends = []
        for t in tracks_raw:
            for s in t.get("segments") or []:
                tgt = s.get("target_timerange") or {}
                ends.append(_us_to_sec((tgt.get("start") or 0) + (tgt.get("duration") or 0)))
        total_sec = max(ends or [0.0])

    # 主轨判定（与渲染器一致：非叠加视频轨里覆盖时长最长的那条）
    def _cover(segs: list) -> float:
        return sum(_us_to_sec((s.get("target_timerange") or {}).get("duration")) for s in segs)

    video_main_candidates = [i for i, t in enumerate(tracks_raw)
                             if t.get("type") == "video" and int(t.get("flag") or 0) != 2]
    main_idx = None
    if video_main_candidates:
        main_idx = max(video_main_candidates,
                       key=lambda i: _cover(tracks_raw[i].get("segments") or []))

    tracks_out: List[dict] = []
    segments_out: List[dict] = []
    text_slots: List[dict] = []
    asset_roles: Dict[str, str] = {}
    review: List[dict] = []
    notes: List[str] = []
    counts: Dict[str, Any] = {r: 0 for r in ROLES}
    unknown_items: List[dict] = []
    mat_usage: Dict[str, dict] = {}
    file_slots: Dict[str, int] = {}  # 素材文件（内部）→ 匿名序号，用来表达「这段和主轨是同一个文件」

    def _file_slot(path: str) -> int | None:
        if not path:
            return None
        if path not in file_slots:
            file_slots[path] = len(file_slots)
        return file_slots[path]

    for t_i, tr in enumerate(tracks_raw):
        ttype = tr.get("type")
        segs = tr.get("segments") or []
        role = track_role(ttype, tr.get("flag"), is_main=(t_i == main_idx))
        reasons: List[str] = []
        if ttype == "video" and t_i in video_main_candidates and t_i != main_idx:
            role = "b_roll"
            reasons.append("secondary_video_track_not_rendered_as_main")
        audio_music = _audio_track_is_music(segs, total_sec) if ttype == "audio" else False
        t_total = _cover(segs)
        sizes = []
        for s_i, s in enumerate(segs):
            mid = s.get("material_id")
            mat = mat_idx.get(mid) or {}
            tgt = s.get("target_timerange") or {}
            srcr = s.get("source_timerange") or {}
            t_start = _us_to_sec(tgt.get("start"))
            t_dur = _us_to_sec(tgt.get("duration"))
            s_in = _us_to_sec(srcr.get("start"))
            s_dur = _us_to_sec(srcr.get("duration")) or t_dur
            cl = s.get("clip") or {}
            scale = _clip_float((cl.get("scale") or {}).get("x"))
            scale_y = _clip_float((cl.get("scale") or {}).get("y"))
            scale = 1.0 if scale is None else scale
            scale_y = scale if scale_y is None else scale_y
            transform = cl.get("transform") or {}
            size_ratio = layer_size_ratio(mat, canvas_w, canvas_h, scale) if mat.get("width") else None
            cover_ratio = round(size_ratio[0] * size_ratio[1], 4) if size_ratio else None
            if cover_ratio is not None:
                sizes.append(cover_ratio)

            kind = "video"
            if mid in mat_idx:
                mk = mat_idx[mid]["_kind"]
                if mk == "text":
                    kind = "text"
                elif mk == "sticker":
                    kind = "sticker"
                elif mk == "audio":
                    kind = "audio" if (mat.get("path") or "") else "compound"
                elif mk == "compound":
                    kind = "compound"
                elif str(mat.get("type")) == "photo":
                    kind = "photo"
                elif not (mat.get("path") or ""):
                    kind = "compound"
            elif ttype == "effect":
                kind = "effect"

            # 关键帧：草稿的 time_offset 是**整条时间线的源时间绝对值**，这里归一段内比例
            keyframes = []
            for kfo in s.get("common_keyframes") or []:
                ptype = kfo.get("property_type") or ""
                pts = []
                for k in kfo.get("keyframe_list") or []:
                    vals = k.get("values") or []
                    if not vals:
                        continue
                    t_rel = _us_to_sec(k.get("time_offset")) - s_in
                    pts.append({"t": _ratio(t_rel, s_dur), "v": round(float(vals[0]), 6)})
                if pts:
                    pts.sort(key=lambda p: p["t"])
                    keyframes.append({"property": ptype, "points": pts})

            animations = []
            for ref in s.get("extra_material_refs") or []:
                holder = anim_by_id.get(ref) or {}
                for an in holder.get("animations") or []:
                    an_kind = str(an.get("type") or "").strip()
                    an_name = str(an.get("name") or "").strip()
                    an_dur = _us_to_sec(an.get("duration"))
                    if not an_kind or an_dur <= 1e-4:
                        continue
                    start_raw = an.get("start")
                    an_start = _us_to_sec(start_raw) if start_raw not in (None, 0) else 0.0
                    animations.append({
                        "kind": an_kind,
                        "name": an_name,
                        "duration_sec": round(an_dur, 4),
                        "start_ratio": _ratio(an_start, t_dur),
                        "duration_ratio": _ratio(an_dur, t_dur),
                    })
            if animations:
                animations.sort(key=lambda a: (0 if a["kind"] == "in" else 1, a["start_ratio"]))

            mask = None
            for ref in s.get("extra_material_refs") or []:
                mk = mask_by_id.get(ref)
                if not mk:
                    continue
                if str(mk.get("type") or "mask").lower() == "none":
                    break  # 草稿里遮罩被关掉：本来就不该生效，不算未实现
                R = _replica_module()
                mask = {
                    "resource_type": str(mk.get("resource_type") or ""),
                    "name": mk.get("name"),
                    "config": R.mask_config(mk),
                }
                break

            effects = []
            for ref in s.get("extra_material_refs") or []:
                fx = vfx_by_id.get(ref)
                if not fx:
                    continue
                effects.append({
                    "name": fx.get("name"),
                    "effect_id": fx.get("effect_id"),
                    "render_index": fx.get("render_index"),
                    "adjust_params": fx.get("adjust_params") or [],
                })

            seg_reasons: List[str] = []
            if kind == "compound":
                seg_reasons.append("compound_clip_not_extracted")
            if mask:
                seg_reasons.append("mask_params_unvalidated_on_new_assets")
            if effects:
                seg_reasons.append("effect_params_unvalidated_on_new_assets")
            if _crop_uv(mat):
                seg_reasons.append("crop_uv_inherited_from_source_material")

            geometry = {
                "scale": [round(scale, 6), round(scale_y, 6)],
                "transform": [round(_clip_float(transform.get("x")) or 0.0, 6),
                              round(_clip_float(transform.get("y")) or 0.0, 6)],
                "rotation": round(_clip_float(cl.get("rotation")) or 0.0, 4),
                "alpha": _clip_float(cl.get("alpha")),
                "flip": cl.get("flip") or {},
                "crop": _crop_uv(mat),
                "size_ratio": size_ratio,
                "cover_ratio": cover_ratio,
            }

            text_slot_idx = None
            if kind == "text":
                style = text_style_of(mat)
                src_text = ""
                raw_c = mat.get("content")
                if isinstance(raw_c, str) and raw_c.lstrip().startswith("{"):
                    try:
                        src_text = str(json.loads(raw_c).get("text") or "")
                    except Exception:
                        src_text = ""
                elif isinstance(raw_c, str):
                    src_text = raw_c
                src_lines = int(src_text.count("\n") + 1) if src_text else 1
                cap = text_layout_capacity(style, canvas_w, src_lines=src_lines)
                font = font_ref_of(mat)
                text_slot_idx = len(text_slots)
                text_slots.append({
                    "slot": text_slot_idx,
                    "track_index": t_i,
                    "segment_slot": s_i,
                    "char_cap": int(cap["capacity"]),
                    "char_cap_basis": cap["basis"],
                    "capacity_per_line": int(cap["capacity_per_line"]),
                    "capacity_lines": int(cap["capacity_lines"]),
                    "char_px_estimate": cap["char_px"],
                    # 源文案长度只作参考，不再是容量上限（上一轮就是把它当上限才截了 19 位）
                    "char_cap_source": int(len(src_text.strip())),
                    "max_lines": int(cap["capacity_lines"]),
                    # 原版大字位写死了字框宽（≈703px）：只作说明，容量仍按行宽算
                    "design_fixed_width_px": _clip_float(mat.get("fixed_width")),
                    # 每行只放得下 1 个字的大字位：容量 2 是**设计值**，不是容量算错的 bug
                    "design_capacity_note": ("per_line=1（大字位，原版就只放单个大字）；容量 2 = 设计值"
                                             if cap["capacity_per_line"] <= 1 else None),
                    "align": style["alignment"],
                    "line_spacing": style["line_spacing"],
                    "font": font,
                    "style": style,
                    "requires_review": True,
                    "review_reasons": (["font_file_not_bundled"] if font else ["font_unknown"]),
                })

            seg_key = f"seg:{t_i}:{s_i}"
            # 该段所用素材的角色（bgm/sfx、a_roll/b_roll/overlay_card 在轨内也可能混用）
            seg_asset_role = None
            if mid and mid in mat_idx and kind != "effect":
                seg_asset_role = infer_asset_role(mat_idx[mid], track_role_of_seg=role,
                                                  cover_ratio=cover_ratio, audio_is_music=audio_music)
            segments_out.append({
                "key": seg_key,
                "track_index": t_i,
                "role": role,
                "asset_role": seg_asset_role,
                "slot": s_i,
                "kind": kind,
                "start_ratio": _ratio(t_start, total_sec),
                "duration_ratio": _ratio(t_dur, total_sec),
                "start_sec": round(t_start, 4),
                "duration_sec": round(t_dur, 4),
                "source_in_sec": round(s_in, 4),
                "source_duration_sec": round(s_dur, 4),
                "speed": _clip_float(s.get("speed")) or 1.0,
                "geometry": geometry,
                "keyframes": keyframes,
                "animations": animations,
                "mask": mask,
                "effects": effects,
                "text_slot": text_slot_idx,
                "same_material_as": None,
                "requires_review": bool(seg_reasons),
                "review_reasons": seg_reasons,
                "file_slot": _file_slot(str(mat.get("path") or "")) if kind in ("video", "photo") else None,
            })

            # 素材 → 角色（同一素材出现在多轨时：a_roll 优先，其次 overlay_card/b_roll）
            def _note_usage(mid_: str, cover: float | None) -> None:
                mat_ = mat_idx.get(mid_) or {}
                cand = infer_asset_role(mat_, track_role_of_seg=role,
                                        cover_ratio=cover, audio_is_music=audio_music)
                usage = mat_usage.setdefault(mid_, {"role": cand, "tracks": [], "reasons": []})
                usage["tracks"].append(f"{t_i}:{s_i}")
                if _role_rank(cand) > _role_rank(usage["role"]):
                    usage["role"] = cand

            if mid and mid in mat_idx and kind != "effect":
                _note_usage(mid, cover_ratio)
            # 复合片段的真身挂在 extra_material_refs 上（material_id 指向的是无路径占位）
            for ref in s.get("extra_material_refs") or []:
                if ref in mat_idx and mat_idx[ref]["_kind"] == "compound":
                    _note_usage(ref, cover_ratio)

        tracks_out.append({
            "index": t_i,
            "track_type": ttype,
            "role": role,
            "flag": tr.get("flag"),
            "render_index": t_i,  # 数组序即 z 序：越靠后越在上层
            "segment_count": len(segs),
            "total_sec": round(t_total, 4),
            "cover_ratio": round(max(sizes) if sizes else 1.0, 4),
            "is_driver": t_i == main_idx,
            "requires_review": bool(reasons),
            "review_reasons": reasons,
        })

    # ---- 叠加段与主轨是否**同一素材**（原版的推近/缩窗重构图，不是另一段内容） ----
    # 只记结构：同一文件 → 同一 file_slot → 指向时间上重叠的那一主轨段。不含任何路径/文件名。
    driver_segments = [s for s in segments_out if s["track_index"] == main_idx] if main_idx is not None else []
    by_slot: Dict[int, List[dict]] = {}
    for s in driver_segments:
        if s.get("file_slot") is not None:
            by_slot.setdefault(s["file_slot"], []).append(s)
    n_linked = 0
    for s in segments_out:
        if s in driver_segments or s.get("file_slot") is None:
            continue
        best, best_ov = None, 0.0
        for cand in by_slot.get(s["file_slot"], []):
            ov = min(s["start_sec"] + s["duration_sec"], cand["start_sec"] + cand["duration_sec"]) \
                - max(s["start_sec"], cand["start_sec"])
            if ov > best_ov:
                best, best_ov = cand, ov
        if best is not None and best_ov > 0.05:
            s["same_material_as"] = best["key"]
            n_linked += 1
    if n_linked:
        notes.append(f"{n_linked} 段叠加层与主轨用的是同一个素材文件（原版为推近/缩窗重构图），"
                     f"模版记 same_material_as；套用时跟随主轨同一素材与入点，不另接 B-roll")

    # ---- 素材角色表（推断不出的一律 unknown，逐条记原因，不瞎猜） ----
    for mid, mat in mat_idx.items():
        if mid in mat_usage:
            asset_roles[mid] = mat_usage[mid]["role"]
            if asset_roles[mid] == "unknown":
                unknown_items.append({
                    "material_id": mid,
                    "kind": mat.get("_kind"),
                    "segments": mat_usage[mid]["tracks"][:4],
                    "reason": _unknown_reason(mat),
                })
            continue
        asset_roles[mid] = "unknown"
        unknown_items.append({
            "material_id": mid,
            "kind": mat.get("_kind"),
            "reason": "not_referenced_by_any_segment",
        })
    for role_name in asset_roles.values():
        counts[role_name] = counts.get(role_name, 0) + 1
    counts["materials_total"] = len(mat_idx)
    counts["unknown_items"] = len(unknown_items)

    if text_slots:
        review.append({"scope": "text_font", "count": len(text_slots),
                       "reason": "字体文件不随模版走（版权 + 隐私门禁）：模版只记字体名/扩展名/文件名指纹，"
                                 "套用时按名字在本机就近解析（风格资产 → 系统字体 → 剪映缓存），"
                                 "真解析不到才回落系统字体，并逐位写进报告的 font_fallback_slots"})
        notes.append("文字的 alignment 字段在源草稿里缺省（None），按默认居中对齐处理")
    n_compound = sum(1 for s in segments_out if s["kind"] == "compound")
    if n_compound:
        review.append({"scope": "compound_clip", "count": n_compound,
                       "reason": "复合片段内容内嵌子时间线，模版只标记不复刻（套用时该段跳过并计数）"})
    n_mask = sum(1 for s in segments_out if s.get("mask"))
    if n_mask:
        review.append({"scope": "mask", "count": n_mask,
                       "reason": "遮罩参数原样搬运，未在新素材上校核（图层宽高比变化会影响圆角/羽化观感）"})
    n_fx = sum(1 for s in segments_out if s.get("effects"))
    if n_fx:
        review.append({"scope": "effect", "count": n_fx,
                       "reason": "画面特效参数原样搬运，未在新素材上校核"})

    template = {
        "template_version": TEMPLATE_VERSION,
        "kind": "replica_structure_template",
        "source_ref": "sha256:" + hashlib.sha256(raw_bytes).hexdigest()[:16],
        "duration_sec": round(total_sec, 4),
        "fps": fps,
        "canvas": {"width": int(canvas_w), "height": int(canvas_h)},
        "time_units": {"segment_anchor": "start_ratio/duration_ratio 相对总时长",
                       "keyframe_time": "段内相对比例（0=段首, 1=段尾）",
                       "animation_time": "段内相对比例（start_ratio/duration_ratio）"},
        "tracks": tracks_out,
        "segments": segments_out,
        "text_slots": text_slots,
        "asset_roles": asset_roles,
        "counts": counts,
        "unknown_items": unknown_items,
        "requires_review": review,
        "notes": notes,
    }
    return template


def _unknown_reason(mat: dict) -> str:
    if mat.get("_kind") == "video" and not (mat.get("path") or ""):
        return "video_material_without_path (复合片段占位，真身不可判定)"
    if mat.get("_kind") == "audio" and not (mat.get("path") or ""):
        return "audio_material_without_path (复合片段占位)"
    return "role_not_inferable_from_track_and_material_type"


def _role_rank(role_name: str) -> int:
    """同一素材多处引用时的角色优先级（主轨证据最强）。"""
    return {"a_roll": 7, "overlay_card": 6, "photo": 5, "b_roll": 4, "bgm": 3,
            "sfx": 2, "sticker": 1, "font": 0}.get(role_name, -1)


# ---------------------------------------------------------------- 按风格找草稿（不写死草稿名）


def resolve_draft_by_style(style_id: str, *, decrypted_root: Path | None = None,
                           library_path: Path | None = None) -> Path:
    """风格 id → 明文草稿文件：走风格库里的 `source_hash` 匹配，代码里不留草稿名。"""
    lib_p = Path(library_path or LIBRARY_PATH)
    lib = json.loads(lib_p.read_text(encoding="utf-8"))
    entry = next((s for s in lib.get("styles") or [] if s.get("style_id") == style_id), None)
    if not entry:
        raise SystemExit(f"风格库里没有 {style_id}")
    target = str(entry.get("source_hash") or "")
    if not target:
        raise SystemExit(f"{style_id} 没有 source_hash，无法定位草稿")
    root = Path(decrypted_root or DECRYPTED_ROOT)
    candidates = sorted(root.glob("*/draft_content.json"))
    for cand in candidates:
        if sha256_of_file(cand) == target:
            return cand
    raise SystemExit(f"{style_id} 的源草稿不在明文快照目录里（{len(candidates)} 套可比对）")


def summarize(template: dict) -> str:
    c = template.get("counts") or {}
    roles: Dict[str, int] = {}
    for s in template.get("segments") or []:
        roles[s["role"]] = roles.get(s["role"], 0) + 1
    return (f"{template['source_ref']} · {template['duration_sec']}s @{template['fps']}fps · "
            f"{len(template['tracks'])} 轨 / {len(template['segments'])} 段 · "
            f"段角色 {roles} · 素材 {c.get('materials_total')} 个（unknown {c.get('unknown', 0)}）· "
            f"文字位 {len(template.get('text_slots') or [])}")


def probe_duration_sec(path: Path | str) -> float:
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
        return max(0.0, float((proc.stdout or "").strip() or 0.0))
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="从真实草稿提炼与素材无关的复刻模版")
    ap.add_argument("--style-id", help="风格 id（走风格库哈希定位草稿）")
    ap.add_argument("--draft", help="草稿目录或 draft_content.json")
    ap.add_argument("--out", default=None, help="模版 JSON 输出路径")
    args = ap.parse_args()
    if args.draft:
        draft_path = Path(args.draft)
    elif args.style_id:
        draft_path = resolve_draft_by_style(args.style_id)
    else:
        ap.error("需要 --draft 或 --style-id")
    tpl = extract_template(draft_path)
    print("[replica-template] " + summarize(tpl))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[replica-template] -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
