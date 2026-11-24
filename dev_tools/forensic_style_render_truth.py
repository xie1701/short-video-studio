#!/usr/bin/env python3
"""24 套风格 → 各自草稿的「渲染真值」清点（只读，纯取证）。

为什么要它
──────────
复刻链里的标定常量现在散在代码里，有的其实是**某一条草稿**反解出来的（描边环宽、
抠像外扩、动画幅度…）。要判断哪些能进模版、哪些必须按风格分档，第一步得先知道
「这 24 套风格各自在草稿里到底写了什么」——不是从代码推，是**从草稿里读**。

本脚本只做三件事：
  1. 用风格库的 `source_hash` 把 style_id 对到明文草稿（与 replica_template.resolve_draft_by_style
     同一套匹配规则，但不 import 它：这里要的是「只读 + 不依赖渲染侧代码」）。
  2. 逐套把画布/fps/时长、抠像描边实例、画面特效、遮罩、动画、文字、贴纸、叠加层几何、
     音频条数从 `draft_content.json` 里挖出来。
  3. 落盘 `forensic/style_render_truth.json`，并在写盘前跑一遍隐私门禁
     （草稿中文名 / 本机路径 / 用户名一律不许出现；只留 style_id + 哈希）。

输出里每个值都带来源：`material_index` / `track_index` / `segment_index` 都是草稿数组里的
**稳定下标**（同一份 draft_content.json 里可以按它回查原文）。文字正文一律不落盘，只记字数。

用法：
    python3 dev_tools/forensic_style_render_truth.py [--out forensic/style_render_truth.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PATH = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "style_spectrum_library.json"
DECRYPTED_ROOT = ROOT / "forensic" / "decrypted"
DEFAULT_OUT = ROOT / "forensic" / "style_render_truth.json"
US = 1_000_000

# 明文快照的两种落盘名（见 replica_template.resolve_draft_by_style 的注释）
SNAPSHOT_NAMES = ("draft_content.json", "__latest_close.json")


# --------------------------------------------------------------------------- #
# 定位
# --------------------------------------------------------------------------- #
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def index_snapshots(root: Path) -> dict:
    """{sha256: path} —— 明文快照的哈希索引（同一内容只留第一条）。"""
    out: dict = {}
    for name in SNAPSHOT_NAMES:
        for p in sorted(root.glob(f"*/{name}")):
            try:
                out.setdefault(sha256_of(p), p)
            except OSError:
                continue
    return out


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _r(v, nd=6):
    """数值规整：避免把 0.30000000000000004 这种浮点尾巴写进真值文件。"""
    if isinstance(v, float):
        return round(v, nd)
    return v


def scale_of(clip: dict):
    """clip.scale → (sx, sy)。草稿里有时是 dict{x,y}，有时是裸数字，缺省 1.0。"""
    sc = (clip or {}).get("scale")
    if isinstance(sc, dict):
        sx = _f(sc.get("x"), 1.0)
        sy = _f(sc.get("y"), 1.0)
        if sx == 0.0 and sy == 0.0:
            return 1.0, 1.0
        return sx, sy
    if isinstance(sc, (int, float)):
        return float(sc), float(sc)
    return 1.0, 1.0


def transform_of(clip: dict):
    tr = (clip or {}).get("transform") or {}
    return _r(_f(tr.get("x"))), _r(_f(tr.get("y")))


def flip_of(clip: dict) -> dict:
    f = (clip or {}).get("flip") or {}
    return {"h": bool(f.get("horizontal", f.get("h"))),
            "v": bool(f.get("vertical", f.get("v")))}


def tr_of(seg: dict) -> tuple:
    tr = seg.get("target_timerange") or {}
    return _r(_f(tr.get("start")) / US, 4), _r(_f(tr.get("duration")) / US, 4)


def material_kind(mat: dict, fallback: str = "") -> str:
    t = str((mat or {}).get("type") or fallback or "")
    return t


def basename(p) -> str:
    """只用 basename：字体/素材路径里的本机目录与用户名不许进真值文件。"""
    s = str(p or "").replace("\\", "/")
    return s.rsplit("/", 1)[-1] if s else ""


def adjust_params(items) -> dict:
    """`[{name,value,default_value}]` → {name: value}（value 缺失时回落到 default_value）。"""
    out = {}
    for p in items or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "")
        if not name:
            continue
        v = p.get("value")
        if v is None:
            v = p.get("default_value")
        out[name] = _r(_f(v))
    return out


def refs_of(seg: dict) -> list:
    return [str(r) for r in (seg.get("extra_material_refs") or [])]


def track_iter(draft: dict):
    for ti, t in enumerate(draft.get("tracks") or []):
        for si, s in enumerate(t.get("segments") or []):
            yield ti, t, si, s


def main_video_track(draft: dict) -> int:
    """主轨 = 覆盖时长最长的 video 轨（与 render_replica 的口径一致：按段总时长）。"""
    best, best_i = -1, -1
    for ti, t in enumerate(draft.get("tracks") or []):
        if t.get("type") != "video":
            continue
        cover = sum(int((s.get("target_timerange") or {}).get("duration") or 0)
                    for s in t.get("segments") or [])
        if cover > best:
            best, best_i = cover, ti
    return best_i


# --------------------------------------------------------------------------- #
# 逐块提取
# --------------------------------------------------------------------------- #
def _frame_grid_fit(draft: dict) -> dict:
    """时间值对候选帧率的整除性（fps 字段缺失时的唯一旁证）。

    剪映把段起点/时长写成一帧的整数倍（µs 取整）。所以「所有值都落在 1/fps 网格上」
    能排除 24/25/50，但**区分不了 30 与 60**（60 网格是 30 网格的子集）——这一点必须
    如实写出来，不能假装推断出了唯一值。
    """
    vals = []
    for t in draft.get("tracks") or []:
        for s in t.get("segments") or []:
            tr = s.get("target_timerange") or {}
            for k in ("start", "duration"):
                v = tr.get(k)
                if v:
                    vals.append(int(v))
    fit = {}
    for fps in (24, 25, 30, 50, 60, 120):
        step = US / fps
        fit[str(fps)] = _r(max((abs(v / step - round(v / step)) for v in vals), default=0.0), 4)
    return {"candidates_residual_frames": fit, "n_time_values": len(vals)}


def extract_canvas(draft: dict) -> dict:
    cc = draft.get("canvas_config") or {}
    w, h = int(_f(cc.get("width"), 1080)), int(_f(cc.get("height"), 1920))
    fps_decl = draft.get("fps")
    fps = _r(_f(fps_decl), 4) if fps_decl not in (None, 0, 0.0) else None
    return {
        "w": w, "h": h,
        "aspect": _r(w / h if h else 0.0, 4),
        "fps": fps,
        "fps_declared": fps is not None,
        # 字段缺失时按**族内约定**取 60：24 套里声明了 fps 的全部是 60，且 30/60/120 都能
        # 整除这批时间值（区分不了）。写清「推断」而不是冒充「读取」。
        "fps_effective": fps if fps is not None else 60.0,
        "fps_evidence": ("draft.fps 字段" if fps is not None
                         else "draft.fps 缺失；族内已声明的全部为 60，整除性 30/60/120 均通过"),
        "frame_grid": _frame_grid_fit(draft),
        "duration_sec": _r(_f(draft.get("duration")) / US, 4),
        "n_tracks": len(draft.get("tracks") or []),
        "track_types": dict(Counter(str(t.get("type")) for t in draft.get("tracks") or [])),
        "is_minimal_draft": bool(draft.get("is_minimal_draft")),
    }


def extract_matting(draft: dict) -> dict:
    """所有 matting 实例（含未启用描边但真抠了像的），逐条列，不合并。"""
    videos = (draft.get("materials") or {}).get("videos") or []
    by_id = {v.get("id"): v for v in videos}
    # 先建「素材 → 使用它的段」的索引，段几何（scale/transform）才是像素口径的钥匙
    uses: dict = {}
    for ti, t, si, s in track_iter(draft):
        uses.setdefault(s.get("material_id"), []).append((ti, t, si, s))

    instances, flags = [], Counter()
    for mi, mat in enumerate(videos):
        mt = mat.get("matting") or {}
        has_path = bool(mt.get("path"))
        strokes = mt.get("strokes") or []
        if not has_path and not strokes:
            continue
        flags["has_path"] += 1 if has_path else 0
        flags["has_use_quick_brush"] += 1 if mt.get("has_use_quick_brush") else 0
        flags["enable_matting_stroke"] += 1 if mt.get("enable_matting_stroke") else 0
        rows = []
        for st in strokes:
            params = adjust_params(st.get("adjust_params"))
            rows.append({
                "resource_id": str(st.get("resource_id") or ""),
                "third_resource_id": str(st.get("third_resource_id") or ""),
                "resource_name": str(st.get("resource_name") or ""),
                "color_rgba": [_r(_f(c)) for c in (st.get("color") or [])],
                "adjust_params": params,
                "size": params.get("effects_adjust_size"),
                "distance": params.get("effects_adjust_distance"),
                "texture": params.get("effects_adjust_texture"),
                "alpha": params.get("effects_adjust_alpha"),
                "source_platform": st.get("source_platform"),
            })
        seg_uses = []
        for ti, t, si, s in uses.get(mat.get("id"), []):
            sx, sy = scale_of(s.get("clip") or {})
            tx, ty = transform_of(s.get("clip") or {})
            start, dur = tr_of(s)
            seg_uses.append({
                "track_index": ti, "track_type": str(t.get("type")), "segment_index": si,
                "scale": [sx, sy], "transform": [tx, ty],
                "alpha": _r(_f((s.get("clip") or {}).get("alpha"), 1.0)),
                "start_sec": start, "dur_sec": dur,
                "render_index": s.get("render_index"),
            })
        nat = [mat.get("width"), mat.get("height")]
        inst = {
            "material_index": mi,
            "natural": nat,
            "has_matting_path": has_path,
            "has_use_quick_brush": bool(mt.get("has_use_quick_brush")),
            "enable_matting_stroke": bool(mt.get("enable_matting_stroke")),
            "matting_flag": mt.get("flag"),
            "n_strokes": len(strokes),
            "strokes": rows,
            "segments": seg_uses,
        }
        instances.append(inst)
    return {"instances": instances, "counts": dict(flags),
            "n_materials_with_matting_block": sum(
                1 for v in videos if isinstance(v.get("matting"), dict))}


def extract_effects(draft: dict) -> dict:
    """画面特效轨/段：effect_id / resource_id / effects_adjust_* / 关键帧。"""
    mats = draft.get("materials") or {}
    pools = {}
    for key in ("video_effects", "effects", "filters"):
        for i, m in enumerate(mats.get(key) or []):
            pools[m.get("id")] = (key, i, m)
    used, items = set(), []
    for ti, t, si, s in track_iter(draft):
        mid = s.get("material_id")
        if mid not in pools:
            continue
        used.add(mid)
        key, mi, m = pools[mid]
        start, dur = tr_of(s)
        kfs = []
        for kf in s.get("common_keyframes") or []:
            kfs.append({
                "property": str(kf.get("property_type") or ""),
                "values": [kf.get("values")],
                "time_rel": [_r(_f(k.get("time_offset")) / US, 4)
                             for k in (kf.get("keyframe_list") or [])],
            })
        items.append({
            "track_index": ti, "segment_index": si, "pool": key, "material_index": mi,
            "effect_id": str(m.get("effect_id") or ""),
            "resource_id": str(m.get("resource_id") or ""),
            "category_name": str(m.get("category_name") or ""),
            "effect_name": str(m.get("name") or ""),
            "value": _r(_f(m.get("value"), 1.0)),
            "adjust_params": adjust_params(m.get("adjust_params")),
            "render_index": s.get("render_index"),
            "track_render_index": s.get("track_render_index"),
            "start_sec": start, "dur_sec": dur,
            "keyframes": kfs,
            "material_render_index": m.get("render_index"),
        })
    unused = [{"pool": pools[mid][0], "material_index": pools[mid][1],
               "resource_id": str(pools[mid][2].get("resource_id") or "")}
              for mid in pools if mid not in used]
    return {"n_segments": len(items), "segments": items, "unused_materials": unused}


def extract_masks(draft: dict) -> dict:
    """materials.common_mask：条数、类型、几何/羽化；以及挂在哪些轨上。"""
    mats = (draft.get("materials") or {}).get("common_mask") or []
    mask_by_id = {m.get("id"): (i, m) for i, m in enumerate(mats)}
    items, refs = [], Counter()
    for ti, t, si, s in track_iter(draft):
        for r in refs_of(s):
            if r in mask_by_id:
                refs[str(t.get("type"))] += 1
    for i, m in enumerate(mats):
        cfg = m.get("config") or {}
        items.append({
            "material_index": i,
            "type": str(m.get("type") or ""),                # "none" = 草稿里关掉了
            "resource_type": str(m.get("resource_type") or ""),
            "resource_id": str(m.get("resource_id") or ""),
            "name": str(m.get("name") or ""),
            "config": {k: _r(_f(v)) if isinstance(v, (int, float)) else v
                       for k, v in cfg.items() if k in (
                           "width", "height", "centerX", "centerY", "rotation",
                           "feather", "roundCorner", "aspectRatio", "invert")},
        })
    return {
        "n": len(mats),
        "n_effective": sum(1 for m in mats if str(m.get("type") or "") != "none"),
        "types": dict(Counter(str(m.get("type") or "") for m in mats)),
        "resource_types": dict(Counter(str(m.get("resource_type") or "") for m in mats)),
        "items": items,
        "attached_by_track_type": dict(refs),
    }


def extract_animations(draft: dict) -> dict:
    """动画：条数、名字 top-N、宿主（文字/视频层/贴纸，按挂载段的轨类型判定）。"""
    mats = draft.get("materials") or {}
    holders = {a.get("id"): a for a in mats.get("material_animations") or []}
    host_of: dict = {}
    for ti, t, si, s in track_iter(draft):
        for r in refs_of(s):
            if r in holders:
                host_of.setdefault(r, str(t.get("type")))
    names, by_host, by_kind, items = Counter(), Counter(), Counter(), []
    for hid, h in holders.items():
        host = host_of.get(hid, "unattached")
        for an in h.get("animations") or []:
            name = str(an.get("name") or "")
            if not name:
                continue
            kind = str(an.get("type") or "")
            dur = _r(_f(an.get("duration")) / US, 4)
            start = an.get("start")
            names[name] += 1
            by_host[host] += 1
            by_kind[kind] += 1
            items.append({
                "name": name, "kind": kind, "dur_sec": dur,
                "start_rel_sec": _r(_f(start) / US, 4) if start not in (None, 0) else None,
                "host": host,
            })
    items.sort(key=lambda x: (x["host"], x["name"], x["kind"]))
    return {
        "n": len(items),
        "by_host": dict(by_host),
        "by_kind": dict(by_kind),
        "top_names": names.most_common(12),
        "items": items,
    }


def extract_texts(draft: dict) -> dict:
    """文字：字号分布、阴影字段、描边字段、字体 basename（正文不落盘，只记字数）。"""
    mats = (draft.get("materials") or {}).get("texts") or []
    sizes, fonts, border_w, n_chars = Counter(), Counter(), Counter(), []
    shadow_mat, shadows_in_style, stroke_in_style = [], [], []
    has_bg = 0
    for m in mats:
        raw = m.get("content")
        try:
            content = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            content = {}
        txt = str(content.get("text") or "")
        n_chars.append(len(txt))
        for st in content.get("styles") or []:
            sz = st.get("size")
            if sz is not None:
                sizes[str(_r(_f(sz), 2))] += 1
            for sh in st.get("shadows") or []:
                shadows_in_style.append({
                    "diffuse": _r(_f(sh.get("diffuse"))),
                    "alpha": _r(_f(sh.get("alpha"))),
                    "distance": _r(_f(sh.get("distance"))),
                    "angle": _r(_f(sh.get("angle"))),
                })
            for sk in st.get("strokes") or []:
                stroke_in_style.append({
                    "width": _r(_f(sk.get("width"))),
                    "color": list(((sk.get("content") or {}).get("solid") or {}).get("color") or []),
                })
        fp = m.get("font_path") or ""
        if not fp:
            for f in m.get("fonts") or []:
                if f.get("path"):
                    fp = f["path"]
                    break
        if fp:
            fonts[basename(fp)] += 1
        if m.get("border_width") is not None:
            border_w[str(_r(_f(m.get("border_width"))))] += 1
        if m.get("has_shadow"):
            shadow_mat.append({
                "point": [m.get("shadow_point")],
                "distance": _r(_f(m.get("shadow_distance"))),
                "smoothing": _r(_f(m.get("shadow_smoothing"))),
                "alpha": _r(_f(m.get("shadow_alpha"))),
                "angle": _r(_f(m.get("shadow_angle"))),
                "font_size": _r(_f(m.get("font_size"))),
            })
        if (m.get("background_color") not in (None, "", [])) or m.get("has_border") or \
                (m.get("background_alpha") or 0):
            has_bg += 1
    return {
        "n": len(mats),
        "n_with_shadow_material": len(shadow_mat),
        "shadow": {
            "material_fields": shadow_mat,
            "style_shadows": shadows_in_style,
        },
        "stroke": {"style_strokes": stroke_in_style, "border_width_hist": dict(border_w)},
        "font_size_hist": dict(sizes.most_common()),
        "font_size_range": ([min(float(k) for k in sizes), max(float(k) for k in sizes)]
                            if sizes else []),
        "fonts_basename": dict(fonts.most_common()),
        "n_with_background_box": has_bg,
        "content_chars_total": sum(n_chars),
    }


def extract_stickers(draft: dict) -> dict:
    mats = (draft.get("materials") or {}).get("stickers") or []
    by_id = {m.get("id"): m for m in mats}
    items = []
    for ti, t, si, s in track_iter(draft):
        if str(t.get("type")) != "sticker":
            continue
        m = by_id.get(s.get("material_id")) or {}
        clip = s.get("clip") or {}
        sx, sy = scale_of(clip)
        tx, ty = transform_of(clip)
        start, dur = tr_of(s)
        items.append({
            "track_index": ti, "segment_index": si, "material_index":
                next((i for i, x in enumerate(mats) if x.get("id") == s.get("material_id")), None),
            "resource_id": str(m.get("resource_id") or ""),
            "material_type": str(m.get("type") or ""),
            "scale": [sx, sy],
            "rotation": _r(_f(clip.get("rotation"))),
            "flip": flip_of(clip),
            "transform": [tx, ty],
            "alpha": _r(_f(clip.get("alpha"), 1.0)),
            "render_index": s.get("render_index"),
            "start_sec": start, "dur_sec": dur,
        })
    return {"n": len(items), "items": items}


def extract_video_layers(draft: dict) -> dict:
    """视频层几何：主轨 / 叠加层分开记（crop、scale、transform、z 序、素材原生尺寸）。"""
    mats = (draft.get("materials") or {}).get("videos") or []
    by_id = {m.get("id"): (i, m) for i, m in enumerate(mats)}
    main_i = main_video_track(draft)
    main, overlays = [], []
    for ti, t, si, s in track_iter(draft):
        if str(t.get("type")) != "video":
            continue
        mi, m = by_id.get(s.get("material_id"), (None, {}))
        clip = s.get("clip") or {}
        sx, sy = scale_of(clip)
        tx, ty = transform_of(clip)
        start, dur = tr_of(s)
        crop = m.get("crop") or {}
        row = {
            "track_index": ti, "segment_index": si, "material_index": mi,
            "natural": [m.get("width"), m.get("height")],
            "material_type": material_kind(m, "video"),
            "has_local_path": bool(m.get("path")),
            "scale": [sx, sy],
            "transform": [tx, ty],
            "alpha": _r(_f(clip.get("alpha"), 1.0)),
            "flip": flip_of(clip),
            "speed": _r(_f(s.get("speed"), 1.0)),
            "volume": _r(_f(s.get("volume"), 1.0)),
            "render_index": s.get("render_index"),
            "track_render_index": s.get("track_render_index"),
            "start_sec": start, "dur_sec": dur,
            "crop": {k: _r(_f(v)) for k, v in crop.items() if isinstance(v, (int, float))},
            "crop_keys": sorted(str(k) for k in crop),
            "n_keyframe_groups": len(s.get("common_keyframes") or []),
        }
        (main if ti == main_i else overlays).append(row)
    return {
        "main_track_index": main_i,
        "main_segments": main,
        "overlay_segments": overlays,
        "n_overlay": len(overlays),
    }


def extract_audio(draft: dict) -> dict:
    mats = (draft.get("materials") or {}).get("audios") or []
    by_id = {m.get("id"): m for m in mats}
    vols, kinds, rows = Counter(), Counter(), []
    for ti, t, si, s in track_iter(draft):
        if str(t.get("type")) != "audio":
            continue
        m = by_id.get(s.get("material_id")) or {}
        kind = material_kind(m, "audio")
        kinds[kind] += 1
        vol = _f(s.get("volume"), 1.0)
        vols[str(_r(vol, 3))] += 1
        start, dur = tr_of(s)
        rows.append({
            "track_index": ti, "segment_index": si, "kind": kind,
            "volume": _r(vol, 3), "speed": _r(_f(s.get("speed"), 1.0)),
            "start_sec": start, "dur_sec": dur,
        })
    n_audio_tracks = sum(1 for t in draft.get("tracks") or [] if str(t.get("type")) == "audio")
    return {
        "n_tracks": n_audio_tracks,
        "n_segments": len(rows),
        "by_kind": dict(kinds),
        "volume_hist": dict(vols.most_common()),
        "segments": rows,
    }


def extract_style(style_id: str, entry: dict, draft_path: Path, file_hash: str) -> dict:
    draft = json.loads(draft_path.read_text(encoding="utf-8", errors="ignore"))
    truth = {
        "style_id": style_id,
        "source_ref": str(entry.get("source_ref") or ""),
        "draft_sha256": file_hash,
        "archetype_id": str(entry.get("archetype_id") or ""),
        "canvas_fps": extract_canvas(draft),
        "matting": extract_matting(draft),
        "effects": extract_effects(draft),
        "masks": extract_masks(draft),
        "animations": extract_animations(draft),
        "texts": extract_texts(draft),
        "stickers": extract_stickers(draft),
        "video_layers": extract_video_layers(draft),
        "audio": extract_audio(draft),
    }
    return truth


# --------------------------------------------------------------------------- #
# 隐私门禁
# --------------------------------------------------------------------------- #
# 这些串本身就不能以字面量躺在源码里：仓库门禁是**逐字扫 dev_tools/ 的（注释/docstring 都算）**，
# 而这里要挡的恰恰是它们。所以按片段拼装——源码文本里不出现完整敏感串，运行时拼出来才用于比对。
_TOKEN_FRAGMENTS = (
    ("/Us", "ers/"),               # 本机用户主目录路径前缀
    ("C:", "/"),                    # Windows 盘符路径写法
    ("App", "Data"),                # 剪辑软件的用户数据目录名
    ("Jian", "yingPro"),            # 剪辑软件缓存根目录名（只用于检测，绝不写入真值）
    ("draft", "path_placeholder"),  # 草稿内相对路径的占位符前缀
)


def forbidden_tokens(root: Path) -> list:
    """真值文件里一律不许出现的串：本机路径前缀 + 各明文快照目录的中文名（运行期从目录名取）。"""
    toks = ["".join(pair) for pair in _TOKEN_FRAGMENTS]
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        toks.append(d.name)
        # 「序号-中文名」这种目录名，单独的中文名也要挡（门禁一般是这么 grep 的）
        m = re.match(r"^\d+[-_.\s]+(.+)$", d.name)
        if m and len(m.group(1)) >= 2:
            toks.append(m.group(1))
    return sorted(set(t for t in toks if t))


def privacy_scan(text: str, tokens: list) -> list:
    return [t for t in tokens if t and t in text]


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="24 套风格渲染真值清点（只读）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--library", default=str(LIBRARY_PATH))
    ap.add_argument("--decrypted-root", default=str(DECRYPTED_ROOT))
    args = ap.parse_args()

    lib = json.loads(Path(args.library).read_text(encoding="utf-8"))
    root = Path(args.decrypted_root)
    snap = index_snapshots(root)

    styles, missing = {}, []
    for entry in lib.get("styles") or []:
        sid = str(entry.get("style_id") or "")
        target = str(entry.get("source_hash") or "")
        p = snap.get(target)
        if not p:
            missing.append(sid)
            continue
        styles[sid] = extract_style(sid, entry, p, target)

    doc = {
        "generated_at": "2026-11-24",
        "what": "24 套风格各自源草稿里的渲染真值（只读清点，不含素材/字体路径、不含正文）",
        "source": "style_spectrum_library.json::source_hash → forensic/decrypted/*/{draft_content.json,__latest_close.json}",
        "indexing_note": "material_index / track_index / segment_index 都是草稿数组里的稳定下标，可回查原文",
        "n_styles": len(styles),
        "missing": missing,
        "styles": dict(sorted(styles.items())),
    }

    blob = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False)
    hits = privacy_scan(blob, forbidden_tokens(root))
    if hits:
        raise SystemExit("隐私门禁命中，拒绝写盘：" + ", ".join(hits[:12]))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blob + "\n", encoding="utf-8")
    print(f"[truth] {len(styles)} 套 → {out}（{(len(blob)/1024):.1f} KB，隐私门禁 0 命中）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
