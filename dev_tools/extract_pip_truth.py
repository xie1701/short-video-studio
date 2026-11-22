#!/usr/bin/env python3
"""画中画/叠加构图真值提取：24 套草稿的每一条视频轨到底怎么摆的。

判据全部来自草稿里能直接读到的数字，不做命名推测：

- `clip.transform.x/y`：画面在画布上的归一化偏移（-1..1，0 = 居中）
- `uniform_scale.scale` / `clip.scale`：缩放倍数（1.0 = 铺满画布，<1 = 缩小成窗）
- `materials.videos[].matting`：人像/物体抠像（有 path 才是真抠过）
- `matting.strokes`：抠像描边（颜色 + 粗细参数），源里「白轮廓」就是它
- `materials.videos[].video_mask_stroke`：蒙版描边

输出 `forensic/pip_usage.json`，并把 24 套按「主轨是否缩窗 / 叠加轨小窗在哪 / 有没有抠像」
归成一个可读的构图类型 `pip_mode`，供库与渲染共用。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
FORENSIC = ROOT / "forensic"
OUT = FORENSIC / "pip_usage.json"


def _load(sid: str, sources: Dict[str, str]) -> Optional[dict]:
    for name in ("draft_content.json", "__latest_close.json", "template-2.tmp"):
        p = FORENSIC / "decrypted" / sources[sid] / name
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def _geom(seg: Dict[str, Any]) -> Dict[str, float]:
    clip = seg.get("clip") or {}
    tr = clip.get("transform") or {}
    us = seg.get("uniform_scale") or {}
    sc = clip.get("scale")
    scale = None
    if isinstance(us, dict) and us.get("scale"):
        scale = float(us.get("scale"))
    elif isinstance(sc, dict) and sc.get("x"):
        scale = float(sc.get("x"))
    elif isinstance(sc, (int, float)):
        scale = float(sc)
    return {
        "x": round(float(tr.get("x") or 0.0), 3),
        "y": round(float(tr.get("y") or 0.0), 3),
        "scale": round(scale if scale else 1.0, 3),
    }


def _dominant(geoms: List[Dict[str, float]]) -> Dict[str, Any]:
    key = Counter((g["x"], g["y"], g["scale"]) for g in geoms)
    (x, y, s), n = key.most_common(1)[0]
    return {"x": x, "y": y, "scale": s, "segments": n, "total": len(geoms)}


def classify(main: Dict[str, Any], overlays: List[Dict[str, Any]]) -> str:
    """构图类型：只看几何与抠像，不猜名字。"""
    has_matting = any(o["matting"] for o in overlays)
    main_small = main["scale"] <= 0.78 and (abs(main["x"]) > 0.15 or abs(main["y"]) > 0.15)
    main_shrink_only = main["scale"] <= 0.92
    small_windows = [o["dominant"] for o in overlays
                     if o["dominant"]["scale"] <= 0.80
                     and (abs(o["dominant"]["x"]) > 0.2 or abs(o["dominant"]["y"]) > 0.2)]
    if has_matting and main_small:
        return "main_shrunk_person_cutout"
    if has_matting:
        return "person_cutout_overlay"
    if main_small:
        return "main_shrunk_window"
    if small_windows:
        side = small_windows[0]
        where = "corner" if (abs(side["x"]) > 0.3 and abs(side["y"]) > 0.3) else "edge"
        return f"overlay_small_window_{where}"
    if main_shrink_only:
        return "main_shrink_only"
    return "full_frame_overlay"


def build() -> Dict[str, Any]:
    sources = json.loads((FORENSIC / "style_sources.json").read_text(encoding="utf-8"))["styles"]
    styles: Dict[str, Any] = {}
    for sid in sorted(sources):
        draft = _load(sid, sources)
        if draft is None:
            raise SystemExit(f"{sid} 找不到解密草稿")
        mats = draft.get("materials") or {}
        videos = {m["id"]: m for m in (mats.get("videos") or [])}
        tracks = [t for t in (draft.get("tracks") or []) if t.get("type") == "video"]
        staged = []
        for ti, track in enumerate(tracks):
            segs = track.get("segments") or []
            geoms = [_geom(s) for s in segs]
            if not geoms:
                continue
            matting = False
            stroke: Optional[Dict[str, Any]] = None
            for s in segs:
                m = videos.get(s.get("material_id")) or {}
                mt = m.get("matting") or {}
                if isinstance(mt, dict) and mt.get("path"):
                    matting = True
                    if mt.get("strokes") and stroke is None:
                        st = mt["strokes"][0]
                        params = {p.get("name"): p.get("value") for p in (st.get("adjust_params") or [])}
                        stroke = {"color": st.get("color"), "resource": st.get("resource_name"),
                                  "params": params}
                vms = m.get("video_mask_stroke") or {}
                if isinstance(vms, dict) and vms.get("path") and stroke is None:
                    stroke = {"color": vms.get("color"), "mask_stroke": True}
            staged.append({
                "track_index": ti,
                "role": "main" if ti == 0 else "overlay",
                "segments": len(segs),
                "dominant": _dominant(geoms),
                "geometries": geoms,
                "matting": matting,
                "matting_stroke": stroke,
            })
        main = staged[0]["dominant"] if staged else {"x": 0, "y": 0, "scale": 1.0}
        overlays = staged[1:]
        styles[sid] = {
            "timeline_dur": round(float(draft.get("duration") or 0) / 1_000_000.0, 2),
            "video_tracks": len(tracks),
            "main": main,
            "overlays": overlays,
            "main_matting": bool(staged and staged[0]["matting"]),
            "pip_mode_truth": classify(main, overlays),
        }
    return {"version": "1.0.0",
            "source": "剪映草稿 clip.transform / uniform_scale / materials.videos.matting 直读",
            "count": len(styles), "styles": styles}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="只打印表格不写文件")
    args = ap.parse_args()
    doc = build()
    if not args.print:
        OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{'style':9} {'主轨(x,y,scale)':22} {'叠加轨数':6} {'小窗几何':24} 抠像 构图类型")
    for sid, node in doc["styles"].items():
        m = node["main"]
        small = [o for o in node["overlays"]
                 if o["dominant"]["scale"] <= 0.85 and (abs(o["dominant"]["x"]) > 0.15 or abs(o["dominant"]["y"]) > 0.15)]
        win = f"({small[0]['dominant']['x']},{small[0]['dominant']['y']},{small[0]['dominant']['scale']})" if small else "-"
        cut = "有" if (node["main_matting"] or any(o["matting"] for o in node["overlays"])) else "—"
        print(f"{sid:9} ({m['x']},{m['y']},{m['scale']})".ljust(32)
              + f"{len(node['overlays']):<7}{win:<25}{cut:<5}{node['pip_mode_truth']}")
    if not args.print:
        print(f"\n写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
