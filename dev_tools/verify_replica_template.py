#!/usr/bin/env python3
"""复刻模版端到端验证：真草稿 → 提模版 → 换新素材/新文案 → 出原生草稿 → 渲出片 → 验人脸/字体/文字。

链路（全部真跑，不 mock）：

  1. 按 `--style-id` 在风格库里查 `source_hash`，在明文草稿快照目录里**按哈希**定位草稿
     （代码里不写草稿名，输出里也只打脱敏哈希）；
  2. `replica_template.extract_template()` 提结构模版（字体名 + 按版式算的文字容量）；
  3. 素材：`--assets-json` 给真实实拍素材（由 `dev_tools/mine_replica_assets.py` 从本机草稿库挑，
     A-roll 有人脸有声音、B-roll 无人脸）；不给就现场用 ffmpeg 造合成素材（仅链路冒烟）；
  4. `apply_replica_template.apply_template()` 套模版 → 新草稿 JSON（可编辑原生草稿），
     同时给出字体解析表 / 文字容量换行截断表；
  5. 用 `dev_tools/render_replica_from_draft.py` 的 `render_timeline()`（**只读调用，不改**）渲成 mp4；
  6. 抽关键帧（人脸/字幕/缩窗/大字位）+ 逐帧人脸检测 + 推近裁切的人脸审计；
  7. 报告落 `report.json`（含素材池脱敏清单、字体逐位表、文字容量表、人脸检测结果）。

用法：
    python3 dev_tools/verify_replica_template.py --style-id style_21 \
        --assets-json assets.json --texts-json texts.json --pool-json asset_pool.json \
        --out ~/Downloads/replica_template_real
    python3 dev_tools/verify_replica_template.py --no-render      # 只到草稿，不渲
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "dev_tools"))

from common import maybe_reexec_in_venv  # noqa: E402

maybe_reexec_in_venv()

from replica_template import extract_template, resolve_draft_by_style, summarize  # noqa: E402
from apply_replica_template import apply_template  # noqa: E402

# 仅用于「不给外部素材」的链路冒烟：内容是合成图，**不能**当真实效果验收
DEFAULT_TEXTS = [
    "别再一条一条瞎剪了",
    "一条片子其实只有三件事",
    "第一件 把节奏钉死",
    "第二件 让字站在正确的位置",
    "第三件 换素材也不跑版",
    "我们把这套结构抽成了模版",
    "镜头长度 关键帧 动画 全在里面",
    "换一批素材",
    "换一版文案",
    "直接套上去",
    "一分钟出一条结构一样的片子",
    "门店老板也能自己出片",
]

MEDIA_RECIPES = [
    ("a1_motion_bars", 3.6, "testsrc2=size=1080x1920:rate=30,noise=alls=8:allf=t", None),
    ("a2_cells", 3.2, "cellauto=size=540x960:rate=30:rule=110:random_fill_ratio=0.35",
     "scale=1080:1920,format=yuv420p"),
    ("a3_zoom_rgb", 4.0, "rgbtestsrc=size=1080x1920:rate=30",
     "zoompan=z='min(zoom+0.002,1.4)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920"),
    ("b1_fractal", 4.2, "mandelbrot=size=540x960:rate=30", "scale=1080:1920,hue=h=40:s=1.6"),
    ("b2_life", 3.4, "life=size=540x960:rate=30:mold=10:ratio=0.25:death_color=#1b2b4b:life_color=#f3e7a2",
     "scale=1080:1920,format=yuv420p"),
    ("b3_bars", 3.0, "smptebars=size=1080x1920:rate=30", "rotate=0.02:fillcolor=black,noise=alls=12:allf=t"),
]


def _rel(p: Any) -> str:
    """输出路径统一相对当前目录（不把家目录写进报告）。"""
    try:
        return str(Path(p).resolve().relative_to(Path.cwd()))
    except Exception:
        return str(p)


def _run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd[:3])}...\n{proc.stderr[-800:]}")


def build_placeholder_media(media_dir: Path) -> Dict[str, List[str]]:
    """现场造合成素材（幂等）。只用于链路冒烟——真实效果验收必须给 --assets-json。"""
    media_dir.mkdir(parents=True, exist_ok=True)
    made: Dict[str, List[str]] = {"a_roll": [], "b_roll": [], "bgm": []}
    for name, dur, src, vf in MEDIA_RECIPES:
        out = media_dir / f"{name}.mp4"
        if not out.is_file():
            cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src]
            if vf:
                cmd += ["-vf", vf]
            cmd += ["-t", f"{dur}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-an", str(out)]
            _run(cmd)
        made["a_roll" if name.startswith("a") else "b_roll"].append(str(out))
    bgm = media_dir / "bgm_bed.m4a"
    if not bgm.is_file():
        _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
              "-i", "sine=frequency=220:sample_rate=48000,volume=0.22",
              "-t", "20", "-c:a", "aac", "-b:a", "128k", str(bgm)])
    made["bgm"].append(str(bgm))
    return made


def probe_mp4(path: Path) -> dict:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "stream=codec_type,width,height,nb_frames,r_frame_rate,channels",
             "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120)
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        st = next((s for s in streams if s.get("codec_type") == "video"), {})
        au = next((s for s in streams if s.get("codec_type") == "audio"), {})
        return {
            "duration_sec": round(float((data.get("format") or {}).get("duration") or 0.0), 3),
            "width": st.get("width"), "height": st.get("height"),
            "fps": st.get("r_frame_rate"),
            "frames": int(st.get("nb_frames") or 0),
            "has_audio": bool(au),
            "audio_channels": au.get("channels"),
            "bytes": path.stat().st_size if path.is_file() else 0,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- 草稿侧解析


def _draft_media_path(mat: dict, draft_dir: Path) -> Optional[Path]:
    raw = str(mat.get("path") or "")
    if not raw:
        return None
    if "_##/" in raw:  # ##_draftpath_placeholder_<uuid>_##/materials/video/x.mp4
        rel = raw.split("_##/", 1)[1]
        p = draft_dir / rel
    else:
        p = Path(raw)
    return p if p.is_file() else None


def _segments_of(draft: dict) -> List[Tuple[dict, dict]]:
    """[(track, segment)]：只列真画面/文字/贴纸轨，按轨序（=z 序）排好。"""
    out: List[Tuple[dict, dict]] = []
    for tr in draft.get("tracks") or []:
        if tr.get("type") not in ("video", "text", "sticker"):
            continue
        for s in tr.get("segments") or []:
            out.append((tr, s))
    return out


def pick_frame_times(draft: dict, template: dict, want: int = 10) -> List[dict]:
    """按「能看出问题」的标准挑关键帧：缩窗、大字位、推近（人脸）、字幕、贴纸、主轨。

    不按时间等距切：等距会连着抽十几张字幕帧，把缩窗（10~13s）和水印大字位
    （6.8s）全错过——上一轮就是从这上面看不出「字体没应用 / 人脸被裁」。
    按类别轮转取帧，保证每一类都有代表帧。
    """
    cand: List[dict] = []
    mats = draft.get("materials") or {}
    text_by_id = {m["id"]: m for m in (mats.get("texts") or [])}

    main_tr = None
    for tr in draft.get("tracks") or []:
        if tr.get("type") == "video" and int(tr.get("flag") or 0) != 2:
            main_tr = tr
            break
    for s in (main_tr or {}).get("segments") or []:
        t0 = float((s.get("target_timerange") or {}).get("start") or 0) / 1e6
        dur = float((s.get("target_timerange") or {}).get("duration") or 0) / 1e6
        sc = float(((s.get("clip") or {}).get("scale") or {}).get("x") or 1.0)
        mid = t0 + dur * 0.55
        if sc < 0.99:
            cand.append({"t": mid, "cat": "缩窗", "label": f"缩窗 sc={sc:.2f}"})
        elif sc > 1.15:
            cand.append({"t": mid, "cat": "推近", "label": f"推近 sc={sc:.2f}（人脸）"})
        else:
            cand.append({"t": mid, "cat": "主轨", "label": "主轨"})
    for tr, s in _segments_of(draft):
        if tr.get("type") == "text":
            mat = text_by_id.get(s.get("material_id")) or {}
            size = float(mat.get("font_size") or 0.0)
            t0 = float((s.get("target_timerange") or {}).get("start") or 0) / 1e6
            dur = float((s.get("target_timerange") or {}).get("duration") or 0) / 1e6
            alpha = float(mat.get("global_alpha") if mat.get("global_alpha") is not None else 1.0)
            if alpha < 0.01:
                # material.global_alpha=0 不等于「不显示」：段上常带 KFTypeGlobalAlpha 曲线
                # （style_21 的两个水印大字位就是 0.70→0 + 缩到 0.01 飞走，原版可见）
                cat, label = "大字位隐", f"大字位(material alpha=0 + 关键帧曲线) {size:.0f}号"
            elif size >= 40:
                cat, label = "大字位", f"大字位 {size:.0f}号"
            else:
                cat, label = "字幕", f"字幕 {size:.0f}号"
            cand.append({"t": t0 + min(0.35, dur * 0.5), "cat": cat, "label": label})
    for tr, s in _segments_of(draft):
        if tr.get("type") == "sticker":
            t0 = float((s.get("target_timerange") or {}).get("start") or 0) / 1e6
            dur = float((s.get("target_timerange") or {}).get("duration") or 0) / 1e6
            cand.append({"t": t0 + dur * 0.5, "cat": "贴纸", "label": "贴纸/叠加"})

    total = float(template.get("duration_sec") or 0.0)
    cand = [c for c in cand if 0.0 <= c["t"] < max(0.1, total - 0.05)]
    cand.sort(key=lambda c: c["t"])
    caps = {"缩窗": 2, "大字位隐": 1, "大字位": 2, "推近": 3, "字幕": 3, "贴纸": 1, "主轨": 1}
    picked: List[dict] = []

    def _add(item: dict, gap: float = 0.22) -> bool:
        if any(abs(item["t"] - p["t"]) <= gap for p in picked):
            return False
        picked.append(item)
        return True

    for cat in caps:
        n = 0
        for item in [c for c in cand if c["cat"] == cat]:
            if n >= caps[cat] or len(picked) >= want:
                break
            if _add(item):
                n += 1
    for item in cand:  # 还差就按时间补（不凑满也不硬凑重复帧）
        if len(picked) >= want:
            break
        _add(item)
    picked.sort(key=lambda c: c["t"])
    return picked[:want]


def extract_frames(mp4: Path, times: List[dict], out_dir: Path) -> List[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.jpg"):  # 上一轮的帧不留着，否则 frames/ 里会新旧混杂
        try:
            stale.unlink()
        except OSError:
            pass
    rows = []
    for i, item in enumerate(times):
        name = f"{i + 1:02d}_{item['t']:.2f}s_{item['label'].replace('/', '_').replace(' ', '_')}.jpg"
        name = name.replace("（", "(").replace("）", ")").replace("=", "")
        png = out_dir / name
        try:
            _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{item['t']:.3f}", "-i", str(mp4),
                  "-frames:v", "1", "-q:v", "2", str(png)])
            rows.append({"frame": png.name, "t": round(item["t"], 3), "label": item["label"],
                         "ok": png.is_file()})
        except Exception as exc:
            rows.append({"frame": png.name, "t": round(item["t"], 3), "label": item["label"],
                         "ok": False, "error": str(exc)[:120]})
    return rows


def face_check_frames(frames: List[dict], frames_dir: Path) -> dict:
    """渲出的关键帧逐张做人脸检测：人脸到底在不在画面里。"""
    paths = [frames_dir / f["frame"] for f in frames if f.get("ok")]
    if not paths:
        return {"ok": False, "reason": "没有可用帧"}
    try:
        from vision_inspector import inspect_images
        res = inspect_images(paths)
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    rows = []
    detected = 0
    for f in frames:
        p = frames_dir / f["frame"]
        obj = (res or {}).get(str(p)) or {}
        faces = obj.get("faces") or []
        best = 0.0
        cx = cy = None
        for face in faces:
            bb = face.get("bbox") or {}
            area = float(bb.get("w") or 0) * float(bb.get("h") or 0)
            if area > best:
                best = area
                cx = round(float(bb.get("x") or 0) + float(bb.get("w") or 0) / 2, 3)
                cy = round(float(bb.get("y") or 0) + float(bb.get("h") or 0) / 2, 3)
        if faces:
            detected += 1
        rows.append({**f, "faces": len(faces), "face_area": round(best, 4),
                     "face_cx": cx, "face_cy": cy})
    return {"ok": True, "frames": len(rows), "frames_with_face": detected, "detail": rows,
            "backend": (res and next((v.get("backend") for v in res.values() if v.get("backend")), None))}


def face_framing_audit(draft: dict, draft_dir: Path, canvas_w: int, canvas_h: int) -> dict:
    """推近/缩窗时人脸会不会被裁掉：按渲染器的几何算式解析推一遍，再抽帧验一次。

    渲染器口径（render_replica_from_draft → render_mp4.build_graded_segment_cmd）：
      ① 源素材先 cover 到画布（等比放大到铺满，居中裁）；② 再按 clip.scale 放大并在画布上裁回。
    主轨 punch-in 分支显式把 `pip_layout.face_anchor` 置 None（= 禁止人脸锚定推近），
    所以这里算的就是「居中裁」的可见窗口，用来回答「人脸有没有被裁掉」。
    """
    mats = {m["id"]: m for m in (draft.get("materials") or {}).get("videos") or []}
    main_tr = None
    for tr in draft.get("tracks") or []:
        if tr.get("type") == "video" and int(tr.get("flag") or 0) != 2:
            main_tr = tr
            break
    rows: List[dict] = []
    if main_tr is None:
        return {"segments": rows, "note": "没有主轨"}
    try:
        from face_framing import anchor_for
    except Exception:
        anchor_for = None  # type: ignore

    for s in main_tr.get("segments") or []:
        mat = mats.get(s.get("material_id")) or {}
        src = _draft_media_path(mat, draft_dir)
        tgt = s.get("target_timerange") or {}
        srcr = s.get("source_timerange") or {}
        dur = float(tgt.get("duration") or 0) / 1e6
        src_in = float(srcr.get("start") or 0) / 1e6
        clip = s.get("clip") or {}
        sc = float((clip.get("scale") or {}).get("x") or 1.0)
        tx = float((clip.get("transform") or {}).get("x") or 0.0)
        ty = float((clip.get("transform") or {}).get("y") or 0.0)
        sw, sh = int(mat.get("width") or 0), int(mat.get("height") or 0)
        row = {"t": round(float(tgt.get("start") or 0) / 1e6, 3), "scale": round(sc, 3),
               "src_w": sw, "src_h": sh, "mode": "缩窗" if sc < 0.99 else ("推近" if sc > 1.001 else "满屏")}
        if not src or sw <= 0 or sh <= 0:
            row["face"] = None
            rows.append(row)
            continue
        # ① cover 到画布：可见源区域（归一化）
        cover = max(canvas_w / sw, canvas_h / sh)
        scaled_w, scaled_h = sw * cover, sh * cover
        bx0 = max(0.0, (scaled_w - canvas_w) / 2.0) / scaled_w
        bx1 = min(scaled_w, (scaled_w - canvas_w) / 2.0 + canvas_w) / scaled_w
        by0 = max(0.0, (scaled_h - canvas_h) / 2.0) / scaled_h
        by1 = min(scaled_h, (scaled_h - canvas_h) / 2.0 + canvas_h) / scaled_h
        # ② 画布内再按 scale 放大后居中/按 transform 偏移裁回
        if sc > 1.001:
            bw = int(round(canvas_w * sc / 2)) * 2
            bh = int(round(canvas_h * sc / 2)) * 2
            mx, my = tx, -ty
            ox = max(0, min(bw - canvas_w, int(round((bw - canvas_w) / 2.0 - mx * canvas_w / 2.0))))
            oy = max(0, min(bh - canvas_h, int(round((bh - canvas_h) / 2.0 - my * canvas_h / 2.0))))
            u0, u1 = ox / bw, (ox + canvas_w) / bw
            v0, v1 = oy / bh, (oy + canvas_h) / bh
        elif sc < 0.99:
            # 缩窗：整帧缩到画布中央 + 黑底，画面内容一点不裁
            u0, u1, v0, v1 = 0.0, 1.0, 0.0, 1.0
        else:
            u0, u1, v0, v1 = 0.0, 1.0, 0.0, 1.0
        win = {"x0": round(bx0 + (bx1 - bx0) * u0, 3), "x1": round(bx0 + (bx1 - bx0) * u1, 3),
               "y0": round(by0 + (by1 - by0) * v0, 3), "y1": round(by0 + (by1 - by0) * v1, 3)}
        row["visible_src_window"] = win
        row["visible_src_width_frac"] = round(win["x1"] - win["x0"], 3)
        anchor = anchor_for(str(src), src_in + dur / 2.0, max(1.0, min(3.0, dur)), cache_scope="audit") \
            if anchor_for else None
        if not anchor:
            row["face"] = False
            rows.append(row)
            continue
        fx0, fx1 = float(anchor["cx"]) - float(anchor["w"]) / 2, float(anchor["cx"]) + float(anchor["w"]) / 2
        fy0, fy1 = float(anchor["cy"]) - float(anchor["h"]) / 2, float(anchor["cy"]) + float(anchor["h"]) / 2
        inside = (fx0 >= win["x0"] - 1e-3 and fx1 <= win["x1"] + 1e-3
                  and fy0 >= win["y0"] - 1e-3 and fy1 <= win["y1"] + 1e-3)
        cut_x = max(0.0, min(fx1, win["x1"]) - max(fx0, win["x0"])) / max(1e-6, fx1 - fx0)
        cut_y = max(0.0, min(fy1, win["y1"]) - max(fy0, win["y0"])) / max(1e-6, fy1 - fy0)
        row.update({"face": True, "face_cx": anchor["cx"], "face_cy": anchor["cy"],
                    "face_w": round(float(anchor["w"]), 3), "face_h": round(float(anchor["h"]), 3),
                    "face_kept_frac": round(cut_x * cut_y, 3), "face_fully_inside": bool(inside)})
        rows.append(row)
    with_face = [r for r in rows if r.get("face")]
    cropped = [r for r in with_face if not r.get("face_fully_inside")]
    punched = [r for r in rows if r["mode"] == "推近"]
    return {
        "segments": rows,
        "summary": {
            "segments": len(rows),
            "punch_in_segments": len(punched),
            "shrink_window_segments": sum(1 for r in rows if r["mode"] == "缩窗"),
            "segments_with_face": len(with_face),
            "face_partially_cropped": len(cropped),
        },
        "note": "推近分支在渲染器里被显式写成 face_anchor=None（复刻按草稿居中缩放，不开人脸锚定）；"
                "缩窗是把整帧缩小放中央，本身不裁画面。",
    }


def _font_table(template: dict, apply_rep: dict) -> List[dict]:
    resolved = apply_rep.get("font_resolved") or {}
    fallback = {f["slot"] for f in (apply_rep.get("font_fallback_slots") or [])}
    rows = []
    for slot in template.get("text_slots") or []:
        font = slot.get("font") or {}
        nm = font.get("name")
        hit = resolved.get(nm) or {}
        rows.append({
            "slot": slot["slot"],
            "font_name": nm,
            "ext": font.get("ext"),
            "sha12": font.get("sha12"),
            "resolved_via": hit.get("via") if nm in resolved else ("fallback" if slot["slot"] in fallback else None),
            "resolved_name": Path(hit["path"]).name if hit.get("path") else None,
            "font_size_units": (slot.get("style") or {}).get("font_size_units"),
            "char_cap": slot.get("char_cap"),
            "capacity_per_line": slot.get("capacity_per_line"),
            "capacity_lines": slot.get("capacity_lines"),
            "global_alpha": (slot.get("style") or {}).get("global_alpha"),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="复刻模版端到端验证（提取→套用→渲出片→人脸/字体/文字验收）")
    ap.add_argument("--style-id", default="style_21")
    ap.add_argument("--draft", default=None, help="草稿目录或 draft_content.json（优先于 --style-id）")
    ap.add_argument("--out", default="/tmp/replica_template_run")
    ap.add_argument("--assets-json", default=None, help="真实素材表 {角色: [路径...]}")
    ap.add_argument("--pool-json", default=None, help="脱敏素材清单（mine_replica_assets 产出）")
    ap.add_argument("--texts-json", default=None)
    ap.add_argument("--frames", type=int, default=10, help="关键帧数量")
    ap.add_argument("--no-face-check", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {"style_id": args.style_id, "steps": {}}

    # ---- 1. 提模版 ----
    t0 = time.time()
    draft_path = Path(args.draft) if args.draft else resolve_draft_by_style(args.style_id)
    tpl = extract_template(draft_path)
    tpl_path = out / "template.json"
    tpl_path.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    report["steps"]["extract"] = {
        "source_ref": tpl["source_ref"],
        "summary": summarize(tpl),
        "template_json": _rel(tpl_path),
        "duration_sec": tpl["duration_sec"],
        "fps": tpl["fps"],
        "canvas": tpl["canvas"],
        "tracks": len(tpl["tracks"]),
        "segments": len(tpl["segments"]),
        "segments_by_role": _seg_roles(tpl["segments"]),
        "text_slots": len(tpl["text_slots"]),
        "asset_roles": tpl["counts"],
        "unknown_assets": tpl["counts"].get("unknown", 0),
        "unknown_items": tpl["unknown_items"],
        "requires_review": tpl["requires_review"],
        "elapsed_sec": round(time.time() - t0, 2),
    }

    # ---- 2. 新素材 / 新文案 ----
    if args.assets_json:
        assets = json.loads(Path(args.assets_json).read_text(encoding="utf-8"))
        assets_src = "real"
    else:
        assets = build_placeholder_media(out / "media")
        assets_src = "synthetic"
    texts: List[Any] = DEFAULT_TEXTS
    if args.texts_json:
        texts = json.loads(Path(args.texts_json).read_text(encoding="utf-8"))
    pool = json.loads(Path(args.pool_json).read_text(encoding="utf-8")) if args.pool_json else None
    report["steps"]["assets"] = {
        "source": assets_src,
        "counts": {k: len(v) for k, v in assets.items()},
        "paths": {k: [_rel(p) for p in v] for k, v in assets.items()},
        "texts": len(texts),
        "pool": pool,
        "warning": ("合成素材（testsrc2/分形/色块）：只能证明链路通，不能当作真实效果验收"
                    if assets_src == "synthetic" else None),
    }

    # ---- 3. 套模版 ----
    t0 = time.time()
    apply_rep: Dict[str, Any] = {}
    draft_json = apply_template(tpl, assets, texts, out / "draft", apply_rep,
                               style_id=args.style_id)
    draft = json.loads(Path(draft_json).read_text(encoding="utf-8"))
    report["steps"]["apply"] = {
        "draft_json": _rel(draft_json),
        "duration_sec": apply_rep.get("duration_sec"),
        "template_duration_sec": apply_rep.get("template_duration_sec"),
        "duration_delta_sec": apply_rep.get("duration_delta_sec"),
        "segments_total": apply_rep.get("segments_total"),
        "segments_by_role": apply_rep.get("segments_by_role"),
        "tracks_out": apply_rep.get("tracks_out"),
        "asset_reused": apply_rep.get("asset_reused"),
        "content_linked": apply_rep.get("content_linked", 0),
        "asset_wrapped": apply_rep.get("asset_wrapped"),
        "asset_fallback": apply_rep.get("asset_fallback"),
        "slot_shrunk": apply_rep.get("slot_shrunk"),
        "anim_clamped": apply_rep.get("anim_clamped"),
        "texts_used": apply_rep.get("texts_used"),
        "texts_truncated": apply_rep.get("texts_truncated"),
        "text_reused": apply_rep.get("text_reused"),
        "seg_clipped": apply_rep.get("seg_clipped", 0),
        "crop_uv_dropped": apply_rep.get("crop_uv_dropped", 0),
        "crop_uv_dropped_detail": apply_rep.get("crop_uv_dropped_detail") or [],
        "skipped": apply_rep.get("skipped"),
        "anchors_sample": (apply_rep.get("anchors") or [])[:5],
        "elapsed_sec": round(time.time() - t0, 2),
    }
    # 字体逐位表 / 文字容量表
    report["steps"]["fonts"] = {
        "font_resolved": apply_rep.get("font_resolved") or {},
        "font_fallback_slots": apply_rep.get("font_fallback_slots") or [],
        "slots": _font_table(tpl, apply_rep),
        "fallback_font": apply_rep.get("font_fallback"),
    }
    report["steps"]["text_layout"] = apply_rep.get("text_layout") or []
    # 两个水印大字位：material.global_alpha=0 但段上有 KFTypeGlobalAlpha 曲线（0.70→0），
    # 原版是「亮 0.7 再飞出」而不是不显示——不写清楚容易被误解成「多画了两行字」
    report["notes"] = [
        "文字位的 material.global_alpha=0 不总等于「不显示」：段上带 KFTypeGlobalAlpha 关键帧时"
        "0 只是静态终态，真值是曲线（本模版两个水印大字位 = 0.70→0 + 缩到 0.01 飞走）",
    ]

    # ---- 4. 渲出片（只读调用复刻渲染器） ----
    render_rep: Dict[str, Any] = {}
    final_mp4 = out / "final.mp4"
    if not args.no_render:
        t0 = time.time()
        import importlib
        R = importlib.import_module("render_replica_from_draft")
        rr = {"style_id": args.style_id + "_template", "draft": "generated",
              "main_segments": 0, "overlay_segments": 0, "matte_done": 0, "matte_failed": 0,
              "text_segments": 0, "sticker_segments": 0, "masked_main_segments": 0,
              "masked_overlay_segments": 0, "compounds_rendered": [], "unrealized": {},
              "notes": []}
        try:
            mp4 = R.render_timeline(draft, Path(draft_json).parent, args.style_id + "_template",
                                    out / "render", int(round(float(tpl["fps"]))), False, rr, depth=0)
            info = probe_mp4(Path(mp4))
            shutil.copy2(mp4, final_mp4)
            render_rep = {
                "ok": True,
                "mp4": _rel(mp4),
                "final_mp4": _rel(final_mp4),
                "probe": info,
                "main_segments": rr.get("main_segments"),
                "overlay_segments": rr.get("overlay_segments"),
                "text_segments": rr.get("text_segments"),
                "sticker_segments": rr.get("sticker_segments"),
                "masked_segments": (rr.get("masked_main_segments") or 0) + (rr.get("masked_overlay_segments") or 0),
                "animated_overlays": rr.get("animated_overlays"),
                "unrealized": rr.get("unrealized"),
                "notes": rr.get("notes"),
                "elapsed_sec": round(time.time() - t0, 1),
            }
            (out / "render" / "replica_report.json").write_text(
                json.dumps(rr, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            render_rep = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                          "elapsed_sec": round(time.time() - t0, 1)}
    report["steps"]["render"] = render_rep

    # ---- 5. 关键帧 + 人脸检测 ----
    if final_mp4.is_file() and not args.no_render:
        canvas = tpl.get("canvas") or {}
        times = pick_frame_times(draft, tpl, want=max(8, args.frames))
        frames_dir = out / "frames"
        frames = extract_frames(final_mp4, times, frames_dir)
        face_rep = {"ok": False, "reason": "skipped"}
        if not args.no_face_check:
            face_rep = face_check_frames(frames, frames_dir)
        report["steps"]["frames"] = {"dir": _rel(frames_dir), "count": len(frames),
                                     "list": frames, "face_check": face_rep}
        report["steps"]["face_framing"] = face_framing_audit(
            draft, Path(draft_json).parent, int(canvas.get("width") or 1080),
            int(canvas.get("height") or 1920))
    else:
        report["steps"]["frames"] = {"skipped": True}
        report["steps"]["face_framing"] = {"skipped": True}

    # ---- 6. 结论 ----
    ff = report["steps"].get("face_framing") or {}
    report["verdict"] = {
        "template_ok": bool(tpl["segments"]),
        "draft_ok": Path(draft_json).is_file(),
        "render_ok": bool(render_rep.get("ok")) if not args.no_render else None,
        "asset_reused": apply_rep.get("asset_reused"),
        "content_linked": apply_rep.get("content_linked", 0),
        "unknown_assets": tpl["counts"].get("unknown", 0),
        "fonts_resolved": {k: v.get("via") for k, v in (apply_rep.get("font_resolved") or {}).items()},
        "font_fallback_slots": [f["slot"] for f in (apply_rep.get("font_fallback_slots") or [])],
        "texts_truncated": apply_rep.get("texts_truncated"),
        "texts_used": apply_rep.get("texts_used"),
        "face_framing": (ff or {}).get("summary"),
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    # ---- 控制台摘要（不打印草稿名/家目录） ----
    a = report["steps"]["apply"]
    print(f"[verify] 模版: {report['steps']['extract']['summary']}")
    print(f"[verify] 新草稿: {a['segments_total']} 段 · {a['duration_sec']}s "
          f"(模版 {a['template_duration_sec']}s, Δ{a['duration_delta_sec']}s) · 角色 {a['segments_by_role']}")
    print(f"[verify] 复用/回卷/缩段: {a['asset_reused']} / {a['asset_wrapped']} / {a['slot_shrunk']} · "
          f"跟随主轨同素材 {a['content_linked']} 段 · skipped {a['skipped']}")
    print(f"[verify] 字体: {report['verdict']['fonts_resolved']} · "
          f"回落到系统字体的文字位 {report['verdict']['font_fallback_slots']}")
    print(f"[verify] 文字: 用 {report['verdict']['texts_used']} 位 · "
          f"截断 {report['verdict']['texts_truncated']} 位")
    if render_rep:
        if render_rep.get("ok"):
            pr = render_rep["probe"]
            print(f"[verify] 渲染成功 -> {render_rep['final_mp4']} · {pr.get('duration_sec')}s · "
                  f"{pr.get('width')}x{pr.get('height')} · {pr.get('frames')} 帧 · "
                  f"{render_rep['elapsed_sec']}s")
            print(f"[verify] 渲出图层: 主轨 {render_rep['main_segments']} / 叠加 {render_rep['overlay_segments']} / "
                  f"文字 {render_rep['text_segments']} / 遮罩 {render_rep['masked_segments']}")
            print(f"[verify] unrealized: {render_rep['unrealized']}")
        else:
            print(f"[verify] 渲染失败: {render_rep.get('error')}")
    fr = report["steps"].get("frames") or {}
    if fr.get("face_check", {}).get("ok"):
        print(f"[verify] 关键帧 {fr['count']} 张 · 检出人脸 {fr['face_check']['frames_with_face']} 张")
    if ff.get("summary"):
        print(f"[verify] 推近/缩窗人脸审计: {ff['summary']}")
    return 0 if (report["verdict"]["draft_ok"] and report["verdict"]["render_ok"] is not False) else 1


def _seg_roles(segments: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in segments:
        out[s["role"]] = out.get(s["role"], 0) + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
