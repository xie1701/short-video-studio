#!/usr/bin/env python3
"""复刻模版提取器：把一条真实剪辑草稿提炼成**与素材无关**的结构模版。

模版只描述「结构」——轨序/角色、每段的 slot 与几何、关键帧与动画的段内相对比例、
文字位容量、素材角色映射、时长重映射所需的锚点。它**不含**任何素材路径、字体路径、
本机绝对路径、草稿名，也不含源文案；源草稿只留一枚脱敏哈希（`source_ref`）。

    模版 JSON 结构（v2）
    -------------------
    duration_sec / fps / canvas            画布与时长
    tracks[]                               role / track_type / render_index（原版 z 序键，见下）/ 段数 / 覆盖时长
    z_order{}                              该层按 render_index 升序后的轨序 + 与数组序差了哪几条
    tracks[]/segments[]                    **两个 z 口径分开记**：段级 `render_index` = 原草稿里段上那个值
                                           （剪映的 z 序），轨级 `render_index` = 该轨段级值的聚合键（max）；
                                           `track_index`/`index` 才是数组序（= 草稿的 track_render_index）
    segments[]                             role / slot / start_ratio / duration_ratio /
                                           geometry{scale,transform,crop,size_ratio,cover_ratio} /
                                           keyframes（**段内相对比例时间**）/ animations（名字+时长+段内比例）/
                                           mask / effects / text_slot / render_index
    text_slots[]                           文字位：字号/颜色/描边/阴影/底框 + **字体身份**（名字/扩展名/
                                           文件名指纹，不含路径）+ **按版式算出的容量**（每行字数×可容行数）
    asset_roles{素材 id -> 角色}            a_roll / b_roll / overlay_card / sticker / photo /
                                           font / bgm / sfx / unknown
    segments[].compound                    **复合片段的内嵌子时间线结构**（递归，最多 3 层）：
                                           {tracks[], segments[], sub_slots[]} —— 与顶层**同一 schema**
                                           （字段名/比例口径/角色推断规则完全一致），另带
                                           depth / duration_sec / length_delta_sec / asset_profile
    counts{}                               unknown 计数、复合片段计数与逐条原因（不瞎猜）
    requires_review[]                      结构里没法与素材无关表达的部分，如实标记

    复合片段（compound）为什么必须递归提取
    --------------------------------------
    剪映的复合片段不是一块素材，而是**另一条时间线**：父段 `material_id` 指向一个
    没有 path 的视频/音频占位，真身在 `extra_material_refs` 里的 `materials.drafts[*]`
    条目上（`drafts[i].draft` 就是那条子时间线的 draft_content）。原版 0.45~1.667s 的
    「三格拼贴底板」就是这种：5 张静图各自一条轨、scale 0.8、错开入场。
    只标 `compound_unrealized` 等于把这层设计整块丢掉（成片只剩主轨压暗）。
    所以这里按与顶层**同一个提取函数**递归拆它的轨/段/几何/关键帧/动画/文字位。

推断不出的素材角色一律记 `unknown` 并计数，绝不按位置猜成某个角色。

    z 序为什么必须记成**结构字段**（v2 的主要变化）
    ----------------------------------------------
    原草稿里有两个不同的东西，以前被当成同一个：
      · `segments[].track_render_index` = **轨数组序**（原版 0.45~1.667s 的拼贴轨是 2、缩图层是 6）；
      · `segments[].render_index`        = **剪映的 z 序**（同一时刻拼贴=3、缩图层=2，拼贴在上）。
    两者在原版里就是冲突的：数组序说拼贴在缩图层**下面**，render_index 说它在**上面**；
    成片证明后者对（拼贴看得见）。以前 `tracks[].render_index` 直接写的是数组序 t_i，
    套用端又把它当轨下标用，于是拼贴永远落到缩图层下面——条带几何一点没变却「看起来更少」。
    现在按原版语义分开记：段级留原值，轨级留聚合键（同一轨里段值不统一时取 max，理由见
    apply 端 `_track_z_key()`），并额外给一个 `z_order` 小结，让套用端**按它排序**而不是猜。

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
# v2：tracks[]/segments[] 的 render_index 改成「原版 z 序」语义（原来写的是轨下标），
# 并新增 tracks[].render_index_values / z_order{}。旧 v1 模版仍能被套用端读（缺字段时退回数组序）。
TEMPLATE_VERSION = 2
# 复合片段递归深度上限：与渲染器 render_replica_from_draft.MAX_COMPOUND_DEPTH 同一口径。
# 渲染器在父深度 depth >= 3 时不再渲子时间线，所以提取端也只能拆到第 3 层为止；
# 再深的段如实标 compound_depth_exceeded，**不静默拍平**。
MAX_COMPOUND_DEPTH = 3
# 子时间线总长 vs 父段时长的容差（秒）：一帧多以内算一致（原版两处差 0.0001s）。
COMPOUND_LEN_TOL = 0.04
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


def material_fingerprint(path: str) -> str | None:
    """原始素材的**身份指纹**：只有哈希，不含路径也不含文件名。

    为什么需要它：模版里 `file_slot` 只在**本层**内有效（每层一条文件槽表），所以跨复合
    片段比对「这两层原版用的是不是同一张图」时它给不出答案。指纹按**文件名**算，因为复合
    片段的子草稿里同一张图会挂在不同占位根下（整条路径各不相同），而文件名在实际素材库里
    就是那把钥匙。套用端拿它区分：
      · 原版这两层本来就是同一张图 → 该复用到同一张新图（显式放行）；
      · 两张不同的图 → 绝不许被套用端复用成同一张（全局唯一）。
    """
    name = Path(str(path or "")).name
    if not name:
        return None
    return "fp:" + hashlib.sha1(name.casefold().encode("utf-8")).hexdigest()[:12]


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


# ---------------------------------------------------------------- 复合片段


def compound_sub_draft(seg: dict, mat_idx: Dict[str, dict]) -> Optional[dict]:
    """段 → 内嵌子时间线（`materials.drafts[*].draft`）。与渲染器同一口径。

    复合片段在父时间线上只是「一个没有 path 的占位素材」：`material_id` 指向
    videos/audios 里那条空路径占位，真身挂在 `extra_material_refs` 的 drafts 条目上。
    优先级按 `extra_material_refs` 的顺序找第一份**带 draft dict** 的 drafts 条目；
    找不到就返回 None（调用端计 `compound_sub_timeline_missing`，绝不凭空造一个）。
    """
    for ref in seg.get("extra_material_refs") or []:
        entry = mat_idx.get(ref) or {}
        if entry.get("_kind") == "compound" and isinstance(entry.get("draft"), dict):
            return entry["draft"]
    return None


def compound_asset_profile(seg_list: List[dict]) -> dict:
    """子时间线的素材构成剖面（只用于报告与套用端定向，不含任何路径）。

    为什么需要它：套用端要能回答「这层复合片段到底是照片拼贴、视频叠层还是纯音效」，
    才能按角色去取对应池子（照片层必须取**图片**，不能拿视频顶）。
    """
    by_role: Dict[str, int] = {}
    for s in seg_list:
        r = str(s.get("asset_role") or s.get("role") or "unknown")
        by_role[r] = by_role.get(r, 0) + 1
    photo = by_role.get("photo", 0)
    video = by_role.get("a_roll", 0) + by_role.get("b_roll", 0) + by_role.get("overlay_card", 0)
    audio = by_role.get("bgm", 0) + by_role.get("sfx", 0)
    text = by_role.get("font", 0)
    sticker = by_role.get("sticker", 0)
    if photo and not video and not audio:
        layout = "photo_collage" if photo >= 2 else "photo_layer"
    elif video and not photo and not audio:
        layout = "video_stack"
    elif audio and not (photo or video or text or sticker):
        layout = "audio_only"
    elif text and not (photo or video or audio or sticker):
        layout = "text_only"
    else:
        layout = "mixed"
    return {
        "segments": len(seg_list),
        "by_asset_role": by_role,
        "photo_layers": photo,
        "video_layers": video,
        "audio_layers": audio,
        "text_layers": text,
        "sticker_layers": sticker,
        "layout": layout,
    }


# ---------------------------------------------------------------- 主入口


def _extract_level(draft: dict, *, depth: int = 0) -> dict:
    """一条时间线（顶层草稿 **或**复合片段内嵌子草稿）→ 同一套结构字段。

    顶层与子层走**同一个函数**，所以 `segment.compound.tracks/segments/sub_slots`
    与模板顶层的 `tracks/segments/text_slots` 是同一 schema：字段名、时间口径
    （start_ratio/duration_ratio 相对**本层**总时长；关键帧/动画为段内比例）、
    角色推断规则全部一样。子层不做特殊化，否则套用端就要写第二套逻辑。
    """
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
            if s.get("visible") is False:
                # 「素材被隐藏」的段（段级 `visible: False`）：原版成片里根本没有它。
                # 提取进模版就会在套用端被**真画出来**——style_01 的 14 段全压在 0–4s，
                # 正是他试过又隐藏掉的那批开场方案。跳过并计数，不假装它是模版的一部分。
                counts["invisible_segments_skipped"] = \
                    counts.get("invisible_segments_skipped", 0) + 1
                continue
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
                # 动画时长超出本段长度：结构上不可能完整播完（剪映会自动截），模版必须拆开记：
                # 段内占比比例化后 start_ratio+duration_ratio > 1 的就是这一类。
                # 套用端会按新段长重新钳一次并计 anim_clamped（不在提取端静默截短）。
                over = [a for a in animations
                        if a["start_ratio"] + a["duration_ratio"] > 1.0005]
                if over:
                    counts["animations_exceeding_segment"] = \
                        counts.get("animations_exceeding_segment", 0) + len(over)
                    anim_overflow = [{"kind": a["kind"], "name": a["name"],
                                      "duration_sec": a["duration_sec"],
                                      "segment_sec": round(t_dur, 4),
                                      "overflow_sec": round(a["duration_sec"] - t_dur, 4)}
                                     for a in over]
                else:
                    anim_overflow = []
            else:
                anim_overflow = []

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

            # 人像抠像层：素材的 `matting.path` 非空 = 原版真抠了像（快速画笔/自定义抠像），
            # 这类叠加层是「同一条素材切出人物压在背景上」，不是另一段内容。模版必须标出来——
            # 套用端若把它当普通 B-roll 另接一条新素材满幅盖上去，就会把主轨画面盖成重影。
            # 只记结构（是不是抠像层 / 有没有描边 / 描边颜色与强度参数），不记 matting 路径与
            # custom id（那既是本机死路径也是隐私）。
            # 描边参数为什么要进模版：套用端要把「抠像 + 撕纸描边」在新素材上原样重建，
            # 而描边的环宽/颗粒/颜色是**设计值**（不是像素路径）；丢了它就只能用默认值瞎猜。
            matting = None
            mt = mat.get("matting") or {}
            if mt.get("path"):
                strokes = mt.get("strokes") or []
                st = strokes[0] if strokes else {}
                st_params = {}
                for _p in st.get("adjust_params") or []:
                    _nm = _p.get("name")
                    if not _nm:
                        continue
                    _v = _p.get("value")
                    if _v is None:
                        _v = _p.get("default_value")
                    try:
                        st_params[str(_nm)] = round(float(_v), 6)
                    except (TypeError, ValueError):
                        continue
                st_color = st.get("color")
                if isinstance(st_color, (list, tuple)) and len(st_color) >= 3:
                    try:
                        st_color = [round(float(c), 6) for c in st_color[:4]]
                    except (TypeError, ValueError):
                        st_color = None
                else:
                    st_color = None
                matting = {
                    "person_cutout": True,
                    "stroke": bool(mt.get("enable_matting_stroke")),
                    "stroke_name": (st.get("resource_name") if strokes else None),
                    "stroke_color": st_color,
                    "stroke_params": st_params or None,
                    "quick_brush": bool(mt.get("has_use_quick_brush")),
                }

            seg_reasons: List[str] = []
            # 注：复合片段是否拆成 sub_structure 由下面的递归提取决定（拆成功就不加 reason）；
            # 拆不了才在那一分支里记 compound_sub_timeline_missing / compound_depth_exceeded。
            if matting:
                # 原来这里写的是 `person_matting_layer_not_reconstructible`（模版自称抠像层重建不了）。
                # 改成：模版带全重建所需的**结构真值**（同素材绑定 + 描边参数），套用端在
                # 新素材上真跑抠像重建；只剩「描边参数是按原版反解的近似值」这一点要人复核。
                seg_reasons.append("person_matting_layer_rebuilt_from_source")
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

            # ---- 复合片段：递归拆内嵌子时间线（同一套 schema；深度上限与渲染器一致） ----
            compound = None
            if kind == "compound":
                sub = compound_sub_draft(s, mat_idx)
                if sub is None:
                    seg_reasons.append("compound_sub_timeline_missing")
                elif depth >= MAX_COMPOUND_DEPTH:
                    # 渲染器在父深度 >= MAX_COMPOUND_DEPTH 时就不渲子时间线了；提取端同样停住，
                    # 但**不拍平、也不丢**：父段还在，只是内容没拆开，如实计入 requires_review。
                    seg_reasons.append("compound_depth_exceeded")
                else:
                    lvl = _extract_level(sub, depth=depth + 1)
                    sub_dur = float(lvl["duration_sec"])
                    delta = round(sub_dur - t_dur, 4)
                    len_ok = abs(delta) <= COMPOUND_LEN_TOL
                    if not len_ok:
                        # 子时间线总长 ≠ 父段时长：结构上不一致，套用端必须按父段时长重映射，
                        # 这里如实记差值（不静默按子线长度改写父段）。
                        seg_reasons.append("compound_length_mismatch")
                    prof = compound_asset_profile(lvl["segments"])
                    compound = {
                        "depth": depth + 1,
                        "duration_sec": sub_dur,
                        "length_delta_sec": delta,
                        "length_matches_parent": len_ok,
                        "tracks": lvl["tracks"],
                        "segments": lvl["segments"],
                        "z_order": lvl["z_order"],
                        "sub_slots": lvl["text_slots"],
                        "counts": lvl["counts"],
                        "requires_review": lvl["requires_review"],
                        "notes": lvl["notes"],
                        "asset_profile": prof,
                    }
                    if lvl["counts"].get("animations_exceeding_segment"):
                        seg_reasons.append("compound_animation_exceeds_sub_segment")
                    if prof["layout"] in ("photo_collage", "photo_layer", "video_stack"):
                        # 有可见内容的复合片段就是一种叠加构图，不再当 unknown
                        if seg_asset_role in (None, "unknown"):
                            seg_asset_role = "overlay_card"

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
                "animations_overflow": anim_overflow,
                "mask": mask,
                "matting": matting,
                "effects": effects,
                "text_slot": text_slot_idx,
                "same_material_as": None,
                # 原版 z 序真值（段级）：剪映就是按它决定谁盖住谁。取原草稿里段上那个值（可能缺省=
                # 主轨/音频轨），**不能**换成轨下标——两者在同一份原版里就不一致（见模块注释）。
                "render_index": (None if s.get("render_index") is None
                                  else int(s.get("render_index"))),
                "compound": compound,
                "requires_review": bool(seg_reasons),
                "review_reasons": seg_reasons,
                "file_slot": _file_slot(str(mat.get("path") or "")) if kind in ("video", "photo") else None,
                # 原始素材身份指纹（跨层可比、不含路径）：套用端靠它判断「原版同图 → 复用」
                "material_fingerprint": (material_fingerprint(str(mat.get("path") or ""))
                                         if kind in ("video", "photo") else None),
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

        z_values = sorted({int(s.get("render_index")) for s in segs
                           if s.get("render_index") is not None})
        tracks_out.append({
            "index": t_i,
            "track_type": ttype,
            "role": role,
            "flag": tr.get("flag"),
            # 原来什么：`"render_index": t_i`（数组下标）——这与原草稿里**段上那个** render_index
            # 完全是两个字段（原版拼贴轨数组序 2 / 段 render_index 3），套用端按它排序才会把拼贴
            # 排到缩图层（数组序 6 / render_index 2）下面，成片里拼贴被盖住。
            # 改成什么：记**原版 z 序键**——该轨段级 render_index 的聚合值（一条轨里段值不统一时
            # 取 max，理由见 apply_replica_template._track_z_key 的注释；此处无段值时退回数组序）。
            "render_index": (max(z_values) if z_values else t_i),
            "render_index_values": z_values,   # 该轨段级 render_index 的全部取值（升序去重）
            "render_index_basis": ("max_of_segments" if z_values else "track_array_order"),
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
        notes.append(f"{n_linked} 段叠加层与主轨用的是同一个素材文件（原版为推近/缩窗/人像抠像），"
                     f"模版记 same_material_as；套用时跟随主轨同一素材与入点，不另接 B-roll")
        notes.append(f"其中 {sum(1 for s in segments_out if s.get('matting') and s.get('same_material_as'))} "
                     f"段是人像抠像层：套用端跟主轨同素材同入点重跑抠像 + 撕纸描边（alpha 合成），"
                     f"抠像失败或新素材无人时才跳过并计入 overlay_left_blank（不给它另开新素材满幅覆盖）")

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
    n_compound_extracted = sum(1 for s in segments_out if s.get("compound"))
    if n_compound:
        review.append({"scope": "compound_clip", "count": n_compound,
                       "reason": "复合片段的内嵌子时间线已递归提取（`segments[].compound` = "
                                 "{tracks/segments/sub_slots}，与顶层同一 schema，深度上限 "
                                 f"{MAX_COMPOUND_DEPTH}）：套用端按子结构重写 materials.drafts[*].draft "
                                 "+ subdraft/ 目录，父段用 extra_material_refs 引用；"
                                 "子层照片层映射到**图片**素材（asset_role=photo），不拿视频顶"})
    if n_compound > n_compound_extracted:
        review.append({"scope": "compound_unresolved", "count": n_compound - n_compound_extracted,
                       "reason": "这些复合片段没有可拆的子时间线（子草稿缺失或超过递归深度上限），"
                                 "套用端会跳过并逐条计数，不拍平成一张图"})
    n_matte = sum(1 for s in segments_out if s.get("matting"))
    if n_matte:
        review.append({"scope": "person_matting", "count": n_matte,
                       "reason": "人像抠像层（matting.path 非空）与素材绑定：模版带的是结构真值"
                                 "（same_material_as 同素材同入点 + 描边颜色/强度参数），不含原版抠像遮罩。"
                                 "套用端在新素材上按这层结构重跑抠像（Vision alpha）+ 撕纸描边；"
                                 "新素材里没有人/抠像失败时该层跳过并计 overlay_left_blank，绝不另接新素材满幅盖主轨"})
    n_mask = sum(1 for s in segments_out if s.get("mask"))
    if n_mask:
        review.append({"scope": "mask", "count": n_mask,
                       "reason": "遮罩参数原样搬运，未在新素材上校核（图层宽高比变化会影响圆角/羽化观感）"})
    n_fx = sum(1 for s in segments_out if s.get("effects"))
    if n_fx:
        review.append({"scope": "effect", "count": n_fx,
                       "reason": "画面特效参数原样搬运，未在新素材上校核"})

    level: Dict[str, Any] = {
        "duration_sec": round(total_sec, 4),
        "fps": fps,
        "canvas": {"width": int(canvas_w), "height": int(canvas_h)},
        "time_units": {"segment_anchor": "start_ratio/duration_ratio 相对**本层**总时长",
                       "keyframe_time": "段内相对比例（0=段首, 1=段尾）",
                       "animation_time": "段内相对比例（start_ratio/duration_ratio）"},
        "tracks": tracks_out,
        "segments": segments_out,
        "z_order": z_order_summary(tracks_out, segments_out),
        "text_slots": text_slots,
        "asset_roles": asset_roles,
        "counts": counts,
        "unknown_items": unknown_items,
        "requires_review": review,
        "notes": notes,
    }
    return level


# ---------------------------------------------------------------- z 序

# 有画面 z 序的轨类型（音频轨不参入排序：渲染器另起一路做混音，不看轨序）
AUDIO_TRACK_TYPES = ("audio",)


def track_z_key(track: dict) -> float:
    """一层时间线里某条轨的 z 序键（**只有画面轨**才该被调用）。

    取模版已记好的轨级 `render_index`；缺字段（v1 老模版）就逐条退回数组序，
    行为与旧版一致（宁可不变，也不凭猜测重排）。
    """
    ri = track.get("render_index")
    if isinstance(ri, (int, float)):
        return float(ri)
    return float(track.get("index") or 0)


def sort_tracks_by_render_index(tracks: List[dict]) -> List[dict]:
    """按原版 `render_index` 升序排轨（同值保持原有相对顺序 = 稳定排序），音频轨留到最后。

    为什么升序：本项目渲染器「**轨序即 z 序**」（units.sort 用轨下标），数组越靠后越在上层；
    而剪映的 `render_index` 也是越大越在上。两者方向一致，所以只要按 render_index 升序
    写出去，输出轨序与输出段上的 render_index 就自洽了。
    为什么音频轨不参与：音频没有画面 z 序（原版音频轨段的 render_index 就是缺省），
    把它混进画面排序里只会让轨序无端变动；原版也把音频轨放在数组末尾。
    """
    visual = [t for t in tracks if t.get("track_type") not in AUDIO_TRACK_TYPES
              and t.get("role") != "audio"]
    vis_ids = {id(t) for t in visual}
    audio = [t for t in tracks if id(t) not in vis_ids]
    visual.sort(key=lambda t: (track_z_key(t), int(t.get("index") or 0)))
    return visual + audio


def z_order_summary(tracks: List[dict], segments: List[dict], *,
                    max_pairs: int = 8) -> dict:
    """一层时间线的 z 序小结（只描述结构，不改轨序）。

    顶层与子时间线都要有：套用端是按它排序写 tracks 的，报告也靠它对账。
    两个关键量：
      · `moved_tracks`：按 render_index 升序应该移动几条轨（= 迁移幅度）；
      · `array_order_vs_render_index_conflicts`：**原版自己**的数组序与 render_index 冲突的
        视频层对数（原版 0.45~1.667s 的拼贴 vs 缩图层就是一对）。冲突 > 0 说明这套草稿的
        「数组序」不能当 z 序用，必须按 render_index 重排——这正是上一轮条带被盖住的原因。
    """
    order_by_array = [int(t.get("index") or 0) for t in tracks]
    order_by_z = [int(t.get("index") or 0) for t in sort_tracks_by_render_index(tracks)]
    moved = sum(1 for i, idx in enumerate(order_by_z) if order_by_array[i] != idx)

    track_type_of = {int(t.get("index") or 0): t.get("track_type") for t in tracks}
    video_segs = [s for s in segments
                  if s.get("render_index") is not None
                  and track_type_of.get(int(s.get("track_index") or 0)) == "video"]
    conflicts: List[dict] = []
    n_conf = 0
    for i, a in enumerate(video_segs):
        for b in video_segs[i + 1:]:
            if a.get("track_index") == b.get("track_index"):
                continue
            ov = min(float(a["start_sec"]) + float(a["duration_sec"]),
                     float(b["start_sec"]) + float(b["duration_sec"])) \
                - max(float(a["start_sec"]), float(b["start_sec"]))
            if ov <= 0.02:
                continue
            by_z = float(a["render_index"]) - float(b["render_index"])
            by_array = float(a["track_index"]) - float(b["track_index"])
            if by_z * by_array < 0:
                n_conf += 1
                if len(conflicts) < max_pairs:
                    conflicts.append({
                        "overlap": [round(max(float(a["start_sec"]), float(b["start_sec"])), 3),
                                    round(min(float(a["start_sec"]) + float(a["duration_sec"]),
                                              float(b["start_sec"]) + float(b["duration_sec"])), 3)],
                        "on_top_by_render_index": a["key"] if by_z > 0 else b["key"],
                        "on_top_by_array_order": a["key"] if by_array > 0 else b["key"],
                        "render_index": [a["render_index"], b["render_index"]],
                        "track_index": [a["track_index"], b["track_index"]],
                    })
    return {
        "order_by_array": order_by_array,
        "order_by_render_index": order_by_z,
        "moved_tracks": moved,
        "audio_tracks_kept_last": sum(1 for t in tracks
                                      if t.get("track_type") in AUDIO_TRACK_TYPES
                                      or t.get("role") == "audio"),
        "array_order_vs_render_index_conflicts": n_conf,
        "conflict_pairs": conflicts,
        "basis": ("轨级 render_index = 该轨段级值的 max；缺字段退回数组序（v1 老模版不重排）"),
    }


def _walk_compounds(segments: List[dict], *, depth: int = 1) -> List[dict]:
    """递归拉平每一层的复合片段（只用来汇总计数，不改结构）。"""
    out: List[dict] = []
    for s in segments:
        c = s.get("compound")
        if not c:
            continue
        out.append({"depth": depth, "parent_key": s.get("key"),
                    "sub_segments": len(c.get("segments") or []),
                    "asset_profile": c.get("asset_profile") or {},
                    "length_matches_parent": c.get("length_matches_parent"),
                    "length_delta_sec": c.get("length_delta_sec")})
        out.extend(_walk_compounds(c.get("segments") or [], depth=depth + 1))
    return out


def _scan_compound_reasons(segments: List[dict]) -> Dict[str, int]:
    """递归扫 review_reasons：深度超限 / 子草稿缺失 / 长度不一致（含嵌套层）。"""
    acc = {"depth_exceeded": 0, "missing": 0, "length_mismatch": 0}
    for s in segments:
        rs = s.get("review_reasons") or []
        if "compound_depth_exceeded" in rs:
            acc["depth_exceeded"] += 1
        if "compound_sub_timeline_missing" in rs:
            acc["missing"] += 1
        if "compound_length_mismatch" in rs:
            acc["length_mismatch"] += 1
        c = s.get("compound")
        if c:
            sub = _scan_compound_reasons(c.get("segments") or [])
            for k in acc:
                acc[k] += sub[k]
    return acc


def extract_template(draft_path: Path | str, *, require_plaintext: bool = True) -> dict:
    """一条草稿 → 与素材无关的结构模版 dict（含递归的复合片段子结构）。"""
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

    level = _extract_level(draft, depth=0)
    counts = level["counts"]
    notes = level["notes"]
    review = level["requires_review"]

    # ---- 复合片段台账：逐层汇总（提取/未拆/子段/照片层/长度不一致/动画溢出） ----
    comps = _walk_compounds(level["segments"])
    scan = _scan_compound_reasons(level["segments"])
    counts["compound_segments"] = sum(1 for s in level["segments"] if s["kind"] == "compound")
    counts["compounds_extracted"] = len(comps)
    counts["compound_sub_segments_total"] = sum(c["sub_segments"] for c in comps)
    counts["compound_photo_layers"] = sum(
        int((c["asset_profile"] or {}).get("photo_layers") or 0) for c in comps)
    counts["compound_depth_exceeded"] = scan["depth_exceeded"]
    counts["compound_sub_timeline_missing"] = scan["missing"]
    counts["compound_length_mismatch"] = scan["length_mismatch"]
    counts["compounds_max_depth"] = max((c["depth"] for c in comps), default=0)
    if comps:
        notes.append(
            f"复合片段 {len(comps)} 个已递归提取子时间线（子段共 "
            f"{counts['compound_sub_segments_total']} 个，其中照片层 "
            f"{counts['compound_photo_layers']} 层，最深第 {counts['compounds_max_depth']} 层）："
            f"子层与顶层同一 schema，套用端按子结构重建 materials.drafts[*].draft + subdraft/")
    if counts["compound_length_mismatch"]:
        notes.append(f"其中 {counts['compound_length_mismatch']} 个复合片段子时间线总长与父段时长不一致"
                     f"（已在段 review_reasons 记 compound_length_mismatch，套用端按父段重映射）")
    zo = level["z_order"]
    if zo.get("array_order_vs_render_index_conflicts"):
        notes.append(
            f"轨**数组序**与原版 `render_index` 有 {zo['array_order_vs_render_index_conflicts']} 处冲突"
            f"（原版 0.45~1.667s 的拼贴 vs 同源缩图层就是一对：数组序说拼贴在下、render_index 说它在上）："
            f"套用端必须按 `render_index` 升序重排写 tracks（见 z_order.order_by_render_index），"
            f"否则拼贴会被缩图层整块盖住——这就是上一轮「条带看着更少」的真因")
    if zo.get("moved_tracks"):
        notes.append(f"按 render_index 升序应移动 {zo['moved_tracks']} 条轨（音频轨留末尾、不参入排序）")

    template = {
        "template_version": TEMPLATE_VERSION,
        "kind": "replica_structure_template",
        "source_ref": "sha256:" + hashlib.sha256(raw_bytes).hexdigest()[:16],
        "duration_sec": level["duration_sec"],
        "fps": level["fps"],
        "canvas": level["canvas"],
        "max_compound_depth": MAX_COMPOUND_DEPTH,
        "time_units": level["time_units"],
        "tracks": level["tracks"],
        "segments": level["segments"],
        "z_order": level["z_order"],
        "text_slots": level["text_slots"],
        "asset_roles": level["asset_roles"],
        "counts": counts,
        "unknown_items": level["unknown_items"],
        "requires_review": review,
        "compounds": comps,
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
    # 明文快照有两种落盘名：正常解密的 `draft_content.json`，以及「主稿缺失、从 .backup 复原」
    # 的那套写成 `__latest_close.json`（style_01 就是这一种 —— 只认前者会让它永远解析不到，
    # 实测哈希与风格库 `source_hash` 完全一致，纯粹是文件名不同）。
    candidates = sorted(list(root.glob("*/draft_content.json")) + list(root.glob("*/__latest_close.json")))
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
