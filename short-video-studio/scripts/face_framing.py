#!/usr/bin/env python3
"""
[人脸中心的画面裁切]

问题背景（实测）：画中画小窗 / 推近 / 缩窗 / 横屏转竖屏的裁切一律按画面正中取景，
只要人不站在正中（复盘里常见的"侧身指着货架""站在灶台左边"），小窗里就只剩半个身子，
甚至脸被切掉一半。

本模块给裁切一个"锚点"：
  1. 取片段内 3 个时间点抽帧 → Apple Vision 检测人脸（离线本地，无需联网/模型下载）；
  2. 取面积最大的一张脸，把它的中心换算成归一化坐标（跨 3 帧取中位，防止眨眼/侧脸抖动）；
  3. 裁切窗口以这张脸为中心（横向正中、纵向留 6% 头顶余量，避免头顶贴边）；
  4. 脸比窗口还大时，把裁切窗放大到能容下整张脸（宁可变"远一点"，也不切脸），仍不行才贴边。

没有检测到人脸的片段返回 None，渲染端退回原来的居中裁切 —— 不会假装自己做了人脸构图。
"""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 纵向锚位：脸中心放在裁切窗高度的 44% 处（正中会头顶贴边，再高会显得"低头"）
FACE_HEADROOM = 0.44
# 一张脸在窗口里至少占到这个比例才认为"人脸是主体"，否则按普通物体构图处理
FACE_MAIN_RATIO = 0.22
# 取点要盖住整段：实测 norm_05 只在 0.92s 有正脸，旧三点（25%/55%/85%）全落空 → 整条 a_roll 没脸锚点，
# 小窗人脸取景与花字避脸就全部瞎了。改成五点、含首尾附近。
_SAMPLE_TS = (0.08, 0.3, 0.5, 0.7, 0.92)


def headroom_for(anchor: Optional[Dict[str, Any]]) -> float:
    """纵向锚位：普通中景留 6% 头顶；脸越大越往正中收，否则大特写会被头顶余量切掉。"""
    if not anchor:
        return FACE_HEADROOM
    fh = float(anchor.get("h") or 0.0)
    if fh >= 0.40:
        return 0.50
    if fh >= 0.28:
        return 0.47
    return FACE_HEADROOM

_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}
_CACHE_PATH: Optional[Path] = None


def set_cache_path(path: Optional[Path]) -> None:
    global _CACHE_PATH
    _CACHE_PATH = Path(path) if path else None


def _load_cache_file() -> None:
    if _CACHE_PATH and _CACHE_PATH.is_file():
        try:
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for k, v in (data or {}).items():
            _CACHE.setdefault(k, v)


def _persist_cache() -> None:
    if not _CACHE_PATH:
        return
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_CACHE, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def _even(v: float) -> int:
    return max(2, int(round(float(v) / 2.0)) * 2)


def _frame_key(media_path: str, at_sec: float) -> str:
    return f"{Path(str(media_path)).name}@{round(float(at_sec), 2):.2f}"


def _extract_frames(media_path: Path, at_secs: Iterable[float], out_dir: Path) -> List[Tuple[float, Path]]:
    ffmpeg = "ffmpeg"
    out: List[Tuple[float, Path]] = []
    for i, ts in enumerate(at_secs):
        png = out_dir / f"f{i:02d}.jpg"
        r = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-ss", f"{max(0.0, float(ts)):.3f}", "-i", str(media_path),
             "-frames:v", "1", "-q:v", "3", str(png)],
            capture_output=True,
        )
        if r.returncode == 0 and png.is_file():
            out.append((float(ts), png))
    return out


def _bbox_center(bb: Dict[str, Any]) -> Tuple[float, float, float, float]:
    x = float(bb.get("x", 0.0))
    y = float(bb.get("y", 0.0))
    w = float(bb.get("w", 0.0))
    h = float(bb.get("h", 0.0))
    return x + w / 2.0, y + h / 2.0, w, h


def anchor_for(media_path: str, at_sec: float, clip_dur: float = 3.0,
               cache_scope: str = "") -> Optional[Dict[str, Any]]:
    """片段的人脸锚点（归一化）；没人脸返回 None。带进程内 + 磁盘缓存。"""
    _load_cache_file()
    key = f"{cache_scope}|{_frame_key(media_path, at_sec)}"
    if key in _CACHE:
        return _CACHE[key]
    p = Path(str(media_path))
    if not p.is_file():
        _CACHE[key] = None
        return None

    ts_list = [max(0.0, float(at_sec) - clip_dur * (0.5 - s)) for s in _SAMPLE_TS]
    with tempfile.TemporaryDirectory(prefix="svs_face_") as td:
        frames = _extract_frames(p, ts_list, Path(td))
        if not frames:
            _CACHE[key] = None
            return None
        try:
            from vision_inspector import inspect_images

            res = inspect_images([f for _, f in frames])
        except Exception:
            _CACHE[key] = None
            return None

    centers: List[Tuple[float, float, float, float]] = []
    for _, fp in frames:
        obj = res.get(str(fp)) or {}
        faces = obj.get("faces") or []
        if not faces:
            continue
        # 同一帧多张脸：取面积最大的（主体）
        best = max(faces, key=lambda f: float((f.get("bbox") or {}).get("w", 0.0)) * float((f.get("bbox") or {}).get("h", 0.0)))
        centers.append(_bbox_center(best.get("bbox") or {}))
    if not centers:
        _CACHE[key] = None
        _persist_cache()
        return None

    # 跨帧取中位，避免单帧侧脸/低头把窗口甩出去
    cxs = sorted(c[0] for c in centers)
    cys = sorted(c[1] for c in centers)
    ws = sorted(c[2] for c in centers)
    hs = sorted(c[3] for c in centers)
    mid = len(centers) // 2
    anchor = {
        "cx": round(cxs[mid], 4),
        "cy": round(cys[mid], 4),
        "w": round(ws[mid], 4),
        "h": round(hs[mid], 4),
        "faces": len(centers),
        "frames_sampled": len(frames),
        "source": "apple_vision",
    }
    _CACHE[key] = anchor
    _persist_cache()
    return anchor


def crop_rect(anchor: Dict[str, Any], src_w: int, src_h: int, out_w: int, out_h: int,
              headroom: Optional[float] = None) -> Dict[str, int]:
    """按锚点算出裁切矩形（源像素坐标）：先把窗口放大到容得下整张脸，再平移对准。"""
    headroom = headroom_for(anchor) if headroom is None else headroom
    ar = max(1e-6, float(out_w) / float(out_h))
    scale = max(float(out_w) / src_w, float(out_h) / src_h)  # 填满窗口所需的放大倍率（cover）
    fw = float(anchor["w"]) * src_w
    fh = float(anchor["h"]) * src_h
    # 脸（含 15% 余量）+ 一点肩线留白，不能超过裁切窗
    need_w = fw * 1.15
    need_h = fh * 1.15
    while scale > 1e-6:
        cw = float(out_w) / scale
        chh = float(out_h) / scale
        if cw >= need_w and chh >= need_h:
            break
        scale *= 0.94  # 逐步退到更"远"的取景，直到脸放得下
    cw = min(float(src_w), float(out_w) / scale)
    chh = min(float(src_h), float(out_h) / scale)
    if abs(cw / max(1e-6, chh) - ar) > 1e-3:  # 触到源边界时按源宽高比收敛
        if cw >= src_w:
            cw = float(src_w)
            chh = max(2.0, min(float(src_h), cw / ar))
        else:
            chh = float(src_h)
            cw = max(2.0, min(float(src_w), chh * ar))
    cx_px = float(anchor["cx"]) * src_w
    cy_px = float(anchor["cy"]) * src_h
    x = cx_px - cw / 2.0
    y = cy_px - chh * headroom
    x = max(0.0, min(float(src_w) - cw, x))
    y = max(0.0, min(float(src_h) - chh, y))
    rect = {"x": int(round(x)), "y": int(round(y)), "w": _even(cw), "h": _even(chh)}
    rect["x"] = min(rect["x"], max(0, src_w - rect["w"]))
    rect["y"] = min(rect["y"], max(0, src_h - rect["h"]))
    rect["x"] = _even(rect["x"])
    rect["y"] = _even(rect["y"])
    return rect


def face_inside(rect: Dict[str, int], anchor: Dict[str, Any], src_w: int, src_h: int,
                margin: float = 1.15) -> bool:
    """整张脸（含 margin）是否完整落在裁切矩形里——验收用。"""
    fx = float(anchor["cx"]) * src_w
    fy = float(anchor["cy"]) * src_h
    fw = float(anchor["w"]) * src_w * margin / 2.0
    fh = float(anchor["h"]) * src_h * margin / 2.0
    return bool(
        fx - fw >= rect["x"] and fx + fw <= rect["x"] + rect["w"]
        and fy - fh >= rect["y"] and fy + fh <= rect["y"] + rect["h"]
    )


def cover_crop_vf(w: int, h: int, anchor: Optional[Dict[str, Any]] = None,
                  face_bias: bool = False) -> str:
    """填满窗口的裁切滤镜：有人脸锚点就按脸取景，否则退回原居中（或原 0.24 偏上）逻辑。

    实现：用 crop 的表达式能力在**源分辨率**里挑一块与窗口等比的最大区域，
    横向把人脸放到正中，纵向把人脸中心放到窗高的 44%（留 6% 头顶），再缩到窗口尺寸。
    脸比窗口还大时会自动把取景退远（先缩小再裁），绝不切脸。
    """
    ow, oh = _even(w), _even(h)
    if anchor:
        ar = float(ow) / float(oh)
        cx = float(anchor.get("cx", 0.5))
        cy = float(anchor.get("cy", 0.5))
        # 脸高占窗口高度超过 ~87% 时先退远，否则一刀切下来必然切到脸
        pre = ""
        fh = float(anchor.get("h") or 0.0)
        if fh > 0:
            safe = math.sqrt(max(1.0, fh * 1.15 / 0.87))
            if safe > 1.02:
                pre = f"scale=iw/{safe:.4f}:ih/{safe:.4f}:flags=lanczos,"
        crop = (
            f"crop=w='min(iw,ih*{ar:.6f})':h='min(ih,iw/{ar:.6f})':"
            f"x='clip({cx:.6f}*iw-ow/2,0,iw-ow)':"
            f"y='clip({cy:.6f}*ih-oh*{headroom_for(anchor):.3f},0,ih-oh)'"
        )
        return f"{pre}{crop},scale={ow}:{oh}:flags=lanczos,setsar=1"
    if face_bias:
        return (
            f"scale={ow}:{oh}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={ow}:{oh}:(iw-{ow})/2:(ih-{oh})*0.24"
        )
    return (
        f"scale={ow}:{oh}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={ow}:{oh}:(iw-{ow})/2:(ih-{oh})/2"
    )


def punch_in_crop_expr(out_w: int, out_h: int, anchor: Optional[Dict[str, Any]]) -> str:
    """推近裁回画布时，按人脸对位（避免推近把人推出画）。适用于已放大过的镜头。"""
    if not anchor:
        return ""
    cx = float(anchor.get("cx", 0.5))
    cy = float(anchor.get("cy", 0.5))
    return (
        f"crop=w={_even(out_w)}:h={_even(out_h)}:"
        f"x='clip({cx:.6f}*iw-{_even(out_w)}/2,0,iw-{_even(out_w)})':"
        f"y='clip({cy:.6f}*ih-{_even(out_h)}*{headroom_for(anchor):.3f},0,ih-{_even(out_h)})'"
    )
