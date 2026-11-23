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
  6. 抽关键帧（人脸/字幕/缩窗/大字位/原重影发生的 3.0s）+ 逐帧人脸检测 + 推近裁切的人脸审计；
  7. **逐对验重影**：另渲一版「只渲主轨」对照，对每一对时间重叠的 video 段取重叠中点帧
     逐像素比差（同源 + 满幅不透明 + 差异可见 → FAIL），并对被拒接的叠层点位做「与主轨一致」
     点检（证明是那层没上，不是调低了透明度）；
  8. 报告落 `report.json`（含素材池脱敏清单、字体逐位表、文字容量表、人脸检测、叠层同源对账）。

用法：
    python3 dev_tools/verify_replica_template.py --style-id style_21 \
        --assets-json assets.json --texts-json texts.json --pool-json asset_pool.json \
        --out ~/Downloads/replica_template_real
    python3 dev_tools/verify_replica_template.py --no-render      # 只到草稿，不渲
"""

from __future__ import annotations

import argparse
import hashlib
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


def _hash_ref(name: str) -> str:
    """文件名 → 脱敏引用（报告里只出现哈希，不写文件名/路径）。"""
    return "p" + hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:10]


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
    # 人像抠像层（重建的撕纸抠像三明治）：按层中点取帧。
    # 上一轮这些层全被跳过，关键帧里根本没人像层可看；不给它一类取样，
    # “重建了没、描边在不在”在 frames/ 里就没有证据。
    vid_by_id = {m["id"]: m for m in (mats.get("videos") or [])}
    for tr, s in _segments_of(draft):
        if tr.get("type") != "video":
            continue
        mat = vid_by_id.get(s.get("material_id")) or {}
        if not (mat.get("matting") or {}).get("path"):
            continue
        t0 = float((s.get("target_timerange") or {}).get("start") or 0) / 1e6
        dur = float((s.get("target_timerange") or {}).get("duration") or 0) / 1e6
        cand.append({"t": t0 + dur * 0.5, "cat": "抠像",
                     "label": "抠像层" + ("+撕纸描边" if (mat.get("matting") or {})
                                           .get("enable_matting_stroke") else "")})

    total = float(template.get("duration_sec") or 0.0)
    cand = [c for c in cand if 0.0 <= c["t"] < max(0.1, total - 0.05)]
    cand.sort(key=lambda c: c["t"])
    caps = {"抠像": 2, "缩窗": 2, "大字位隐": 1, "大字位": 1, "推近": 2, "字幕": 2,
            "贴纸": 1, "主轨": 1}
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
    picked = _ensure_time(picked, T_GHOST_PROBE, "叠层验收帧", want=max(1, want))
    picked.sort(key=lambda c: c["t"])
    return picked[:want]


# 原重影就出现在 3.0s（主轨 × 抠像叠层同源满幅），这一帧必须进 `frames/`，
# 否则「修好了没」只能靠数字说话，人眼没有可核对的证据。
T_GHOST_PROBE = 3.0


def _ensure_time(picked: List[dict], t: float, label: str, want: int) -> List[dict]:
    """保证 `frames/` 里有 t 附近的一帧：没有就顶掉优先级最低的那一帧。"""
    if any(abs(p["t"] - t) <= 0.35 for p in picked):
        return picked
    order = ["主轨", "贴纸", "字幕", "推近", "大字位", "大字位隐", "缩窗", "抠像"]

    def _rank(item: dict) -> int:
        try:
            return order.index(item.get("cat"))
        except ValueError:
            return len(order)

    out = list(picked)
    if len(out) >= want:
        out.sort(key=_rank)
        out = out[1:]
    out.append({"t": round(t, 3), "cat": "叠层", "label": f"{label} {t:.1f}s"})
    return out


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


def _grab_frame(mp4: Path, t: float, out_jpg: Path) -> Optional[Path]:
    """按时间抽一帧（`-ss` 放在 -i 之后 = 解码后定位，两版对比必须落在同一帧）。"""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-ss", f"{max(0.0, t):.4f}",
              "-frames:v", "1", "-q:v", "2", str(out_jpg)])
    except Exception:
        return None
    return out_jpg if out_jpg.is_file() else None


def _frame_delta(a: Optional[Path], b: Optional[Path]) -> Optional[dict]:
    """两帧的差异：返回 {mae, p99, changed_frac}（归一化到 0~1）；读不到图返回 None。"""
    if not a or not b or not a.is_file() or not b.is_file():
        return None
    try:
        import numpy as np
        from PIL import Image
        with Image.open(a) as ia, Image.open(b) as ib:
            if ia.size != ib.size:
                ib = ib.resize(ia.size)
            xa = np.asarray(ia.convert("RGB"), dtype=np.float32) / 255.0
            xb = np.asarray(ib.convert("RGB"), dtype=np.float32) / 255.0
    except Exception:
        return None
    d = np.abs(xa - xb).mean(axis=2)
    return {"mae": round(float(d.mean()), 4),
            "p99": round(float(np.quantile(d, 0.99)), 4),
            "changed_frac": round(float((d > 0.08).mean()), 4)}


def _faces_in(paths: List[Path]) -> Dict[str, int]:
    """逐图人脸数（Apple Vision）：用来量「同一张脸是不是出现了两次」。"""
    if not paths:
        return {}
    try:
        from vision_inspector import inspect_images
        res = inspect_images(list(paths)) or {}
    except Exception:
        return {}
    out: Dict[str, int] = {}
    for p in paths:
        obj = res.get(str(p)) or {}
        out[str(p)] = len(obj.get("faces") or [])
    return out


def _main_track_of(draft: dict) -> Optional[dict]:
    best = None
    for tr in draft.get("tracks") or []:
        if tr.get("type") != "video" or int(tr.get("flag") or 0) == 2:
            continue
        cover = sum(int((s.get("target_timerange") or {}).get("duration") or 0)
                    for s in tr.get("segments") or [])
        if best is None or cover > best[0]:
            best = (cover, tr)
    return best[1] if best else None


def video_overlap_pairs(draft: dict, draft_dir: Path, canvas_w: int = 1080,
                        canvas_h: int = 1920) -> List[dict]:
    """草稿里**时间上重叠**的每一对视频段（跨轨也算），带素材指纹与是否同源。

    「同一素材」按**草稿里引用的实际文件**判（不是按文件名/声称的宽高）。
    图层满幅与不透明用渲染器同一条几何算式算（contain → ×scale），不另猜一套。
    """
    mats = {m["id"]: m for m in (draft.get("materials") or {}).get("videos") or []}
    vfx_by_id = {e.get("id"): e for e in (draft.get("materials") or {}).get("video_effects") or []}
    drafts_ids = {d["id"] for d in (draft.get("materials") or {}).get("drafts") or []
                  if d.get("draft")}
    try:
        import importlib
        R = importlib.import_module("render_replica_from_draft")
    except Exception:
        R = None
    items: List[dict] = []
    for ti, tr in enumerate(draft.get("tracks") or []):
        if tr.get("type") != "video":
            continue
        for si, s in enumerate(tr.get("segments") or []):
            mat = mats.get(s.get("material_id")) or {}
            src = _draft_media_path(mat, draft_dir)
            # 复合片段的父段只是个容器（占位素材没 path），它的像素在子层里——子层单独入账，
            # 容器本身不进对账，否则每条复合片段都会跟自己的子层冒出一堆「同源=None」的假对。
            if src is None and any(r in drafts_ids for r in (s.get("extra_material_refs") or [])):
                continue
            tgt = s.get("target_timerange") or {}
            t0 = float(tgt.get("start") or 0) / 1e6
            dur = float(tgt.get("duration") or 0) / 1e6
            src_in = float((s.get("source_timerange") or {}).get("start") or 0) / 1e6
            clip = s.get("clip") or {}
            sc = float((clip.get("scale") or {}).get("x") or 1.0)
            alpha = float(clip.get("alpha") if clip.get("alpha") is not None else 1.0)
            cover = None
            blur = None
            if R is not None and src is not None:
                try:
                    geom = R.layer_geometry(mat, str(src), canvas_w, canvas_h, sc)
                    cover = (geom["lw"] * geom["lh"]) / float(canvas_w * canvas_h)
                except Exception:
                    geom, cover = None, None
                # 段级「模糊」特效真值（背景板靠它才是模糊底，而不是第二份取景）
                for ref in s.get("extra_material_refs") or []:
                    fx = vfx_by_id.get(ref) or {}
                    if "模糊" not in str(fx.get("name") or ""):
                        continue
                    val = 0.0
                    for p_ in fx.get("adjust_params") or []:
                        if str(p_.get("name")) == "effects_adjust_blur":
                            v_ = p_.get("value")
                            val = float(v_ if v_ is not None else (p_.get("default_value") or 0.0))
                    if val > 0:
                        try:
                            blur = (R.yes_blur_sigma(val, geom["lw"], geom["lh"])
                                    if geom else R.yes_blur_sigma(val, canvas_w, canvas_h))
                        except Exception:
                            blur = None
            items.append({"track": ti, "idx": si, "seg_id": s.get("id"), "src": str(src) if src else None,
                          "start": t0, "dur": dur, "alpha": alpha, "scale": sc, "cover": cover,
                          "src_in": round(src_in, 4), "blur_sigma": (round(blur, 2) if blur else None),
                          # 抠像层：素材带 matting 结构 → 渲染器走「Vision 抠像 + alphamerge」合成，
                          # 这一层的像素是**带 alpha 的**，不是同源第二份满幅画面。
                          "cutout": bool((mat.get("matting") or {}).get("path")),
                          "in_compound": False})

    # ---- 复合片段的子层也要进对账 ----
    # 为什么：复合片段的内层（照片拼贴/卡片堆）在时间上互相重叠、且跟主轨/抠像层同时在画面上，
    # 不展开子时间线就会漏掉「两块复合片段同时铺开且用了同一张图」这类重影。
    # 子层的时间轴 = 父段起点 + 子段入点 × (父段时长/子时间线总长)。
    drafts_by_id = {d["id"]: d for d in (draft.get("materials") or {}).get("drafts") or []
                    if d.get("draft")}
    for ti, tr in enumerate(draft.get("tracks") or []):
        if tr.get("type") != "video":
            continue
        for si, s in enumerate(tr.get("segments") or []):
            entry = next((drafts_by_id[r] for r in (s.get("extra_material_refs") or [])
                          if r in drafts_by_id), None)
            if entry is None:
                continue
            sub = entry.get("draft") or {}
            sub_dur = float(sub.get("duration") or 0) / 1e6
            p_start = float((s.get("target_timerange") or {}).get("start") or 0) / 1e6
            p_dur = float((s.get("target_timerange") or {}).get("duration") or 0) / 1e6
            k = (p_dur / sub_dur) if sub_dur > 1e-6 else 1.0
            sub_vids = {m["id"]: m for m in (sub.get("materials") or {}).get("videos") or []}
            sub_fx = {e.get("id"): e for e in (sub.get("materials") or {}).get("video_effects") or []}
            for sti, st in enumerate(sub.get("tracks") or []):
                if st.get("type") != "video":
                    continue
                for ss in st.get("segments") or []:
                    m2 = sub_vids.get(ss.get("material_id")) or {}
                    if not m2:
                        continue
                    src2 = _draft_media_path(m2, draft_dir)
                    tgt2 = ss.get("target_timerange") or {}
                    t2 = p_start + float(tgt2.get("start") or 0) / 1e6 * k
                    d2 = float(tgt2.get("duration") or 0) / 1e6 * k
                    sc2 = float(((ss.get("clip") or {}).get("scale") or {}).get("x") or 1.0)
                    al2 = float(((ss.get("clip") or {}).get("alpha")
                                 if (ss.get("clip") or {}).get("alpha") is not None else 1.0))
                    cover2 = None
                    blur2 = None
                    if R is not None and src2 is not None:
                        try:
                            g2 = R.layer_geometry(m2, str(src2), canvas_w, canvas_h, sc2)
                            cover2 = (g2["lw"] * g2["lh"]) / float(canvas_w * canvas_h)
                        except Exception:
                            cover2 = None
                        for ref in ss.get("extra_material_refs") or []:
                            fx = sub_fx.get(ref) or {}
                            if "模糊" not in str(fx.get("name") or ""):
                                continue
                            val = 0.0
                            for p_ in fx.get("adjust_params") or []:
                                if str(p_.get("name")) == "effects_adjust_blur":
                                    v_ = p_.get("value")
                                    val = float(v_ if v_ is not None else 0.0)
                            if val > 0:
                                try:
                                    blur2 = (R.yes_blur_sigma(val, g2["lw"], g2["lh"])
                                             if cover2 is not None else None)
                                except Exception:
                                    blur2 = None
                    items.append({
                        "track": ti, "idx": si * 100 + sti,
                        "seg_id": ss.get("id"), "src": str(src2) if src2 else None,
                        "start": t2, "dur": d2, "alpha": al2, "scale": sc2, "cover": cover2,
                        "src_in": round(float((ss.get("source_timerange") or {}).get("start") or 0) / 1e6, 4),
                        "blur_sigma": (round(blur2, 2) if blur2 else None),
                        "cutout": False, "in_compound": True, "sub_track": sti,
                        "photo": (m2.get("type") or "") == "photo"})

    pairs: List[dict] = []
    for i in range(len(items)):
        a = items[i]
        for j in range(i + 1, len(items)):
            b = items[j]
            a1, b1 = a["start"] + a["dur"], b["start"] + b["dur"]
            ov = min(a1, b1) - max(a["start"], b["start"])
            if ov <= 0.02:
                continue
            top = a if (a["track"] > b["track"]) else b  # 数组序即 z 序
            bot = b if top is a else a
            mid = (max(a["start"], b["start"]) + min(a1, b1)) / 2.0
            # 同入点按**重叠中点时刻**比（不是段头）：背景板是一条长段连续播，主轨是切段；
            # 同一帧上两者各自播到哪个源时刻，才是“是不是同一个画面”的真口径
            top_ip = float(top["src_in"]) + (mid - float(top["start"]))
            bot_ip = float(bot["src_in"]) + (mid - float(bot["start"]))
            pairs.append({
                "a": f"t{a['track']}:{a['idx']}", "b": f"t{b['track']}:{b['idx']}",
                "overlap_start": round(max(a["start"], b["start"]), 3),
                "overlap_end": round(min(a1, b1), 3),
                "overlap_mid": round((max(a["start"], b["start"]) + min(a1, b1)) / 2.0, 3),
                "overlap_sec": round(ov, 3),
                "same_source": bool(a["src"] and a["src"] == b["src"]),
                "top_alpha": round(top["alpha"], 3),
                "top_cover": (round(top["cover"], 3) if top["cover"] is not None else None),
                "top_seg_id": top["seg_id"],
                "top_is_cutout": bool(top["cutout"]),
                "bottom_is_cutout": bool(bot["cutout"]),
                # 背景板证据：真带模糊特效 + 与下层同入点 + 同取景 → “同一条片子的模糊底”
                "top_blur_sigma": top["blur_sigma"],
                "same_in_point": bool(abs(top_ip - bot_ip) <= 0.06),
                "in_point_delta": round(abs(top_ip - bot_ip), 3),
                "same_scale": bool(abs(float(top["scale"]) - float(bot["scale"])) <= 0.06),
                # 子层（复合片段内部）的对比要单独标出来：它们不在顶层轨上，z 序取父轨
                "a_in_compound": bool(a.get("in_compound")),
                "b_in_compound": bool(b.get("in_compound")),
            })
    return pairs


def cutout_alpha_probe(draft: dict, draft_dir: Path, final_mp4: Optional[Path], frames_dir: Path,
                       fps: int = 30, max_zooms: int = 3) -> dict:
    """逐层实测抠像层的 alpha（不是只看草稿里的标记就说「重建了」）。

    做法：对草稿里每一条带 `matting` 的叠加段，按它的真实入点抽一段 0.2s，重跑一遍
    `person_matting`（与渲染器同一个 Vision 引擎），量 alpha 覆盖率 + 存帧；
    前 `max_zooms` 层再从成片里按人脸/头顶位置裁一块 2× 放大图落 `frames/`——
    “撕纸白边在不在、头发有没有被啃掉”必须能用人眼看，不靠数字。

    覆盖率是区分「抠像」与「同源满幅重影」的硬证据：满幅不透明层的覆盖率≈1.0，
    真正的人像抠像层远小于 1（背景是透明的）。
    """
    import numpy as np

    work = Path(frames_dir) / "cutout_probe"
    work.mkdir(parents=True, exist_ok=True)
    canvas_w = int((draft.get("canvas_config") or {}).get("width") or 1080)
    canvas_h = int((draft.get("canvas_config") or {}).get("height") or 1920)
    mats = {m["id"]: m for m in (draft.get("materials") or {}).get("videos") or []}
    layers: List[dict] = []
    zooms: List[dict] = []
    try:
        sys.path.insert(0, str(SCRIPTS))
        from person_matting import generate_person_matte_video  # noqa: E402
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "layers": [], "edge_zooms": []}

    for ti, tr in enumerate(draft.get("tracks") or []):
        if tr.get("type") != "video":
            continue
        for s in tr.get("segments") or []:
            mat = mats.get(s.get("material_id")) or {}
            if not (mat.get("matting") or {}).get("path"):
                continue
            src = _draft_media_path(mat, draft_dir)
            tgt = s.get("target_timerange") or {}
            srcr = s.get("source_timerange") or {}
            t0 = float(tgt.get("start") or 0) / 1e6
            dur = float(tgt.get("duration") or 0) / 1e6
            src_in = float(srcr.get("start") or 0) / 1e6
            sc = float(((s.get("clip") or {}).get("scale") or {}).get("x") or 1.0)
            row = {"track": ti, "seg_id": s.get("id"), "t_start": round(t0, 3), "dur": round(dur, 3),
                   "src_in": round(src_in, 3), "scale": round(sc, 3),
                   "stroke": bool((mat.get("matting") or {}).get("enable_matting_stroke"))}
            if not src or dur <= 0.05:
                row["ok"] = False
                row["reason"] = "missing_source"
                layers.append(row)
                continue
            # 取段中点附近 0.2s：避开首帧（黑帧/空 alpha）与切点
            t_mid = t0 + max(0.05, min(dur - 0.05, dur * 0.5))
            sseek = max(0.0, src_in + (t_mid - t0) - 0.1)
            tag = f"t{ti:02d}_{len(layers):02d}"
            seg_p = work / f"{tag}_seg.mp4"
            matte_p = work / f"{tag}_matte.mp4"
            try:
                _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{sseek:.4f}", "-t", "0.2",
                      "-i", str(src), "-vf", f"fps={fps},setpts=PTS-STARTPTS", "-an",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                      "-pix_fmt", "yuv420p", str(seg_p)])
                meta = generate_person_matte_video(seg_p, matte_p, quality="balanced", fps=fps)
            except Exception as exc:
                row["ok"] = False
                row["reason"] = f"{type(exc).__name__}: {exc}"
                layers.append(row)
                continue
            if not matte_p.is_file():
                row["ok"] = False
                row["reason"] = "matte_not_generated"
                layers.append(row)
                continue
            # 读 matte 末帧（gray）：覆盖率 = alpha>128 的像素占比
            try:
                raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(matte_p),
                                      "-vf", "select='eq(n,0)'", "-vframes", "1",
                                      "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                     capture_output=True).stdout
                mw = int((meta or {}).get("width") or canvas_w)
                mh = int((meta or {}).get("height") or canvas_h)
                if len(raw) < mw * mh:
                    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(matte_p),
                                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                         capture_output=True).stdout
                    raw = raw[-mw * mh:] if len(raw) >= mw * mh else b""
                if raw and len(raw) >= mw * mh:
                    a = np.frombuffer(raw[-mw * mh:], np.uint8).reshape(mh, mw)
                    cover = float((a > 128).mean())
                    row["alpha_coverage"] = round(cover, 4)
                    row["has_transparent_area"] = bool(cover < 0.98)
                    # 头顶位置：第一排 alpha 足够多的像素（用来定位放大裁切框）
                    rows_dense = np.where((a > 110).sum(axis=1) > max(8, mw // 40))[0]
                    if rows_dense.size:
                        y_head = int(rows_dense[0])
                        band = a[y_head:y_head + max(4, mh // 25)] > 110
                        cols = np.where(band.any(axis=0))[0]
                        cx = int(cols.mean()) if cols.size else mw // 2
                        row["head"] = {"cx_frac": round(cx / mw, 4),
                                       "y_frac": round(y_head / mh, 4)}
                row["ok"] = True
            except Exception as exc:
                row["ok"] = False
                row["reason"] = f"{type(exc).__name__}: {exc}"
            layers.append(row)

            # 边缘放大图：从成片同一时刻裁一块，2× 放大；裁切框跟着头顶走
            if final_mp4 and final_mp4.is_file() and len(zooms) < max_zooms:
                head = row.get("head") or {}
                cx = int(round(float(head.get("cx_frac") or 0.5) * canvas_w))
                cy = int(round(float(head.get("y_frac") or 0.18) * canvas_h))
                cw, ch = 400, 700
                x0 = max(0, min(canvas_w - cw, cx - cw // 2))
                y0 = max(0, min(canvas_h - ch, cy - int(ch * 0.35)))
                out_jpg = Path(frames_dir) / f"cutout_edge_{len(zooms) + 1:02d}_{t_mid:.2f}s_zoom2x.jpg"
                try:
                    _run(["ffmpeg", "-y", "-v", "error", "-i", str(final_mp4),
                          "-ss", f"{t_mid:.4f}", "-frames:v", "1",
                          "-vf", f"crop={cw}:{ch}:{x0}:{y0},scale={cw * 2}:{ch * 2}:flags=lanczos",
                          "-q:v", "2", str(out_jpg)])
                    zooms.append({"frame": out_jpg.name, "t": round(t_mid, 3),
                                  "crop": [x0, y0, cw, ch], "zoom": 2.0,
                                  "uses_segment": f"t{ti}", "stroke": row["stroke"],
                                  "alpha_coverage": row.get("alpha_coverage")})
                except Exception as exc:
                    zooms.append({"t": round(t_mid, 3), "error": f"{type(exc).__name__}: {exc}"})

    ok_layers = [r for r in layers if r.get("ok") and r.get("alpha_coverage") is not None]
    cutout_like = [r for r in ok_layers if r.get("has_transparent_area")]
    return {
        "ok": bool(ok_layers),
        "layers_probed": len(layers),
        "layers_with_person_matte": len(ok_layers),
        "layers_with_transparent_alpha": len(cutout_like),
        "coverage_by_seg": {r["seg_id"]: r.get("alpha_coverage") for r in ok_layers if r.get("seg_id")},
        "layers": layers,
        "edge_zooms": zooms,
        "note": "覆盖率 = Alpha>128 像素占比；满幅不透明层 ≈1.0，真抠像层远小于 1",
    }


def render_main_only(draft: dict, draft_dir: Path, work: Path, fps: int, style_id: str) -> Optional[Path]:
    """「只渲主轨」对照版：把非主轨视频段的段全摘掉，其余轨（文字/贴纸/音频）原样保留。

    为什么这么做：要回答的是「这一帧里是不是多叠了一层同源画面」。对照版只少叠层，
    文字/贴纸/音频都在两份里一模一样，它们的差在对比里自然抵消。
    """
    import copy
    main_tr = _main_track_of(draft)
    if main_tr is None:
        return None
    sub = copy.deepcopy(draft)
    keep = None
    for tr in sub.get("tracks") or []:
        if tr.get("type") == "video" and int(tr.get("flag") or 0) != 2:
            keep = tr
            break
    if keep is None:
        return None
    for tr in sub.get("tracks") or []:
        if tr.get("type") == "video" and tr is not keep:
            tr["segments"] = []
    import importlib
    R = importlib.import_module("render_replica_from_draft")
    rr = {"style_id": style_id, "draft": "main_only", "main_segments": 0, "overlay_segments": 0,
          "matte_done": 0, "matte_failed": 0, "text_segments": 0, "sticker_segments": 0,
          "masked_main_segments": 0, "masked_overlay_segments": 0, "compounds_rendered": [],
          "unrealized": {}, "notes": []}
    try:
        return Path(R.render_timeline(sub, draft_dir, style_id + "_mainonly", work / "main_only",
                                      fps, False, rr, depth=0))
    except Exception:
        return None


def compound_structure_audit(template: dict, apply_rep: dict, draft: dict,
                             draft_dir: Path) -> dict:
    """复合片段台账 + 「子结构真的落地了」核对（不信 apply 自报，去看草稿/磁盘）。

    一项一项回答：
      · 模版拆出了几个复合片段、题几个子段、几个照片层（提取端口径）
      · 套用端写回了几个（compounds_applied）
      · 没写的分别为什么（compounds_skipped）
      · 写回的那些在草稿里是不是真有两个层次：父段（path 空的占位素材）→ extra_material_refs
        里的 drafts 条目 → 子时间线 tracks/segments，且 subdraft/<uuid>/draft_content.json 在盘上
      · 子时间线的照片层是不是真的都是**图片**（material.type==photo 且文件后缀是图）
        — 这是「角色跨层映射」的硬校验：photo 层映射成视频就算失败
    """
    tpl_counts = template.get("counts") or {}
    mats = draft.get("materials") or {}
    vid_by_id = {m["id"]: m for m in (mats.get("videos") or [])}
    aud_by_id = {m["id"]: m for m in (mats.get("audios") or [])}
    drafts_by_id = {d["id"]: d for d in (mats.get("drafts") or [])}
    IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic")

    rows: List[dict] = []
    photo_checked = 0
    photo_wrong_type = 0
    sub_segments_found = 0
    sub_photo_found = 0
    subdraft_files_ok = 0
    photo_files: List[str] = []
    for ti, tr in enumerate(draft.get("tracks") or []):
        for s in tr.get("segments") or []:
            refs = [r for r in (s.get("extra_material_refs") or []) if r in drafts_by_id]
            if not refs:
                continue
            entry = drafts_by_id[refs[0]]
            sub = entry.get("draft") or {}
            mat = vid_by_id.get(s.get("material_id")) or aud_by_id.get(s.get("material_id")) or {}
            sub_tracks = sub.get("tracks") or []
            sub_segs = [sg for t in sub_tracks for sg in (t.get("segments") or [])]
            sub_segments_found += len(sub_segs)
            sub_mats = {m["id"]: m for m in (sub.get("materials") or {}).get("videos") or []}
            sub_photo = 0
            for t in sub_tracks:
                for sg in t.get("segments") or []:
                    m2 = sub_mats.get(sg.get("material_id")) or {}
                    if (m2.get("type") or "") != "photo":
                        continue
                    sub_photo += 1
                    photo_checked += 1
                    p = _draft_media_path(m2, draft_dir)
                    if p is None or p.suffix.casefold() not in IMG_EXT:
                        photo_wrong_type += 1
                    elif p is not None:
                        # 全片照片去重的**独立**核对（不看 apply 自报）：把子时间线里每层用的
                        # 图片文件名收集起来，同一文件名出现两次 = 同一张图上了两次屏。
                        photo_files.append(p.name.casefold())
            sub_photo_found += sub_photo
            # subdraft/ 落盘核对：路径里的 <uuid> 目录下真有 draft_content.json
            file_ok = False
            fp = str(entry.get("draft_file_path") or "")
            if "_##/" in fp:
                rel = fp.split("_##/", 1)[1].replace("\\", "/")
                file_ok = (draft_dir / rel).is_file()
            if file_ok:
                subdraft_files_ok += 1
            rows.append({
                "track": ti, "track_type": tr.get("type"), "flag": tr.get("flag"),
                "start": round(float((s.get("target_timerange") or {}).get("start") or 0) / 1e6, 3),
                "duration": round(float((s.get("target_timerange") or {}).get("duration") or 0) / 1e6, 3),
                "parent_material_has_path": bool(mat.get("path")),
                "drafts_ref": refs[0][:8],
                "sub_duration_sec": round(float(sub.get("duration") or 0) / 1e6, 3),
                "sub_tracks": len(sub_tracks),
                "sub_segments": len(sub_segs),
                "sub_photo_layers": sub_photo,
                "subdraft_file_on_disk": file_ok,
            })

    dup_files = sorted({f for f in photo_files if photo_files.count(f) > 1})
    return {
        "compounds_in_template": tpl_counts.get("compounds_extracted", 0),
        "compound_segments_in_template": tpl_counts.get("compound_segments", 0),
        "compounds_extracted": tpl_counts.get("compounds_extracted", 0),
        "compounds_applied": apply_rep.get("compounds_applied", 0),
        "compounds_skipped": apply_rep.get("compounds_skipped") or {},
        "sub_segments_total": apply_rep.get("sub_segments_total", 0),
        "sub_segments_found_in_draft": sub_segments_found,
        "photo_layers_mapped": apply_rep.get("photo_layers_mapped", 0),
        "photo_layers_found_in_draft": sub_photo_found,
        "photo_layers_wrong_type": photo_wrong_type,
        # 全片照片去重（草稿实测）：不同图数 / 重复上的图数 / 重复的文件名
        "photo_files_distinct": len(set(photo_files)),
        "photo_files_duplicated": len(photo_files) - len(set(photo_files)),
        "photo_files_duplicated_names": [_hash_ref(f) for f in dup_files],
        "compound_photo_layers_in_template": tpl_counts.get("compound_photo_layers", 0),
        "compound_anim_clamped": apply_rep.get("compound_anim_clamped", 0),
        "compound_time_scaled": apply_rep.get("compound_time_scaled", 0),
        "compound_length_mismatch": apply_rep.get("compound_length_mismatch", 0),
        "compound_depth_exceeded": tpl_counts.get("compound_depth_exceeded", 0),
        "subdraft_dirs_on_disk_ok": subdraft_files_ok,
        "drafts_entries_in_draft": len(drafts_by_id),
        "detail": rows,
        "ok": bool(apply_rep.get("compounds_applied", 0) == len(rows) and photo_wrong_type == 0
                   and subdraft_files_ok == len(rows)
                   and len(photo_files) == len(set(photo_files))),
    }


def _components_of_mask(mask, min_area: int = 200, max_boxes: int = 8) -> List[dict]:
    """二值 mask → 连通区域包围盒（下采样后的粗略 BFS，够用来数「几块」）。"""
    import numpy as np
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    boxes: List[dict] = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            ys, xs = [], []
            while stack:
                cy, cx = stack.pop()
                ys.append(cy)
                xs.append(cx)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(ys) < min_area:
                continue
            boxes.append({"x0": int(min(xs)), "x1": int(max(xs)),
                          "y0": int(min(ys)), "y1": int(max(ys)), "px": len(ys)})
    boxes.sort(key=lambda b: -b["px"])
    return boxes[:max_boxes]


def compound_region_check(final_mp4: Path, main_only: Optional[Path], frames_dir: Path,
                          window: Tuple[float, float], *, reference_mp4: Optional[Path] = None,
                          fps: int = 60, tag: str = "collage") -> dict:
    """抽复合片段那段的帧 + 逐帧差量，回答「拼贴底板真的出现了吗」。

    三个判据说三条事：
      ① **不是空白/纯黑**：该段帧的亮度不能接近全黑，且与「只渲主轨」对照版的差量
         必须显著（mean MAE > 0.02）——差量≈ 0 就意味着那一层根本没上；
      ② **不是单张大图**：把「成片 − 只渲主轨」差量 mask 做连通区域，看是不是 3+ 块
         分开的小区域（原版是 5 条错开的照片条）；当然也看草稿结构（子层图层数/缩放）；
      ③ 有原版参考就逐帧比 MAE：构图不同但「同样有拼贴层」，MAE 应该明显小于
         「成片 vs 白色底」而不是≈0（后者意味两边都没东西）。
    """
    import numpy as np
    from PIL import Image

    t0, t1 = float(window[0]), float(window[1])
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {"window": [round(t0, 3), round(t1, 3)], "samples": []}

    # ---- ① 逐帧差量：成片 vs 只渲主轨（同一时间点，解码后定位同一帧）----
    step = max(1, int(round(fps / 10)))   # 每秒取样 ~10 点
    samples: List[dict] = []
    t = t0
    k = 0
    while t <= t1 + 1e-6 and k < 60:
        fa = _grab_frame(final_mp4, t, frames_dir / f"{tag}_frame{k:02d}_{t:.2f}s.jpg")
        delta = None
        if main_only is not None:
            fb = _grab_frame(main_only, t, frames_dir / "ghost" / f"{tag}_main{k:02d}_{t:.2f}s.jpg")
            delta = _frame_delta(fa, fb)
        samples.append({"t": round(t, 3), "frame": fa.name if fa else None,
                        "delta_vs_main_only": delta})
        t += step / float(fps)
        k += 1
    out["samples"] = samples
    maes = [s["delta_vs_main_only"]["mae"] for s in samples
            if s.get("delta_vs_main_only")]
    out["delta_vs_main_only"] = {
        "frames": len(maes),
        "mean_mae": round(float(np.mean(maes)), 4) if maes else None,
        "max_mae": round(float(np.max(maes)), 4) if maes else None,
        "min_mae": round(float(np.min(maes)), 4) if maes else None,
        "frames_above_0.02": int(sum(1 for m in maes if m > 0.02)),
    }

    # ---- ② 连通区域：差量 mask 是不是 3+ 块分开的条带（而不是一整张大图/一片空白）----
    mid = (t0 + t1) / 2.0
    fa = _grab_frame(final_mp4, mid, frames_dir / f"{tag}_mid_{mid:.2f}s.jpg")
    blocks = 0
    boxes: List[dict] = []
    lum = None
    if fa and main_only is not None:
        fb = _grab_frame(main_only, mid, frames_dir / "ghost" / f"{tag}_midmain_{mid:.2f}s.jpg")
        if fb:
            a = np.asarray(Image.open(fa).convert("RGB").resize((270, 480)), dtype=np.float32) / 255.0
            b = np.asarray(Image.open(fb).convert("RGB").resize((270, 480)), dtype=np.float32) / 255.0
            d = np.abs(a - b).mean(axis=2)
            mask = d > 0.08
            boxes = _components_of_mask(mask, min_area=120, max_boxes=8)
            blocks = len(boxes)
            lum = round(float(a.mean()), 4)
    out["mid_frame"] = fa.name if fa else None
    out["luminance_mid"] = lum
    out["visible_blocks"] = blocks
    out["visible_block_boxes"] = boxes
    out["blocks_basis"] = ("diff(成片 − 只渲主轨) 的连通区域数：含抠像层/文字等所有被抳掉的层，"
                          "不等于拼贴块数；拼贴本身的块数看下面 layer_reference")
    out["not_blank"] = bool(lum is not None and lum > 0.03)

    # ---- ③ 原版同段参考：同时间点逐帧 MAE ----
    if reference_mp4 is not None and Path(reference_mp4).is_file():
        ref_rows: List[dict] = []
        for i, s in enumerate(samples):
            if i % 3:      # 每 3 帧比一次，够看趋势也省时间
                continue
            fr = _grab_frame(reference_mp4, s["t"], frames_dir / f"{tag}_ref{i:02d}_{s['t']:.2f}s.jpg")
            fd = _grab_frame(final_mp4, s["t"], frames_dir / f"{tag}_new{i:02d}_{s['t']:.2f}s.jpg")
            ref_rows.append({"t": s["t"],
                             "delta": _frame_delta(fd, fr),
                             "ref_frame": fr.name if fr else None,
                             "new_frame": fd.name if fd else None})
        dm = [r["delta"]["mae"] for r in ref_rows if r.get("delta")]
        out["vs_reference"] = {
            "reference": _rel(reference_mp4),
            "frames": len(dm),
            "mean_mae": round(float(np.mean(dm)), 4) if dm else None,
            "min_mae": round(float(np.min(dm)), 4) if dm else None,
            "max_mae": round(float(np.max(dm)), 4) if dm else None,
            "rows": ref_rows,
        }
    return out


def reference_original_render(out: Path, style_id: str, tpl: dict,
                              style_dir_name: str,
                              meta: Optional[dict] = None) -> Optional[Path]:
    """用**同一个渲染器**渲一遍原版草稿，作为「原版同段」参考帧（只读调用）。

    为什么要渲原版：验收要求「0.45~1.667s 的帧与原版同段对比」，而原版成片不在仓库里。
    这里的参考是「原版草稿 + 原版素材 + 同一渲染器」，因此两边差异只来自**换素材**，
    与渲染口径无关——这是能拿到的最干净的对照。原版素材不在本机时返回 None。

    **渲染前先按原版 `render_index` 排一遍轨序**（`_sort_draft_z_order`，子稿递归）：
    本项目渲染器「轨序即 z 序」，直接拿原版草稿（数组序）去渲，参考片自己就把 0.45~1.667s
    的拼贴画在同源缩图层**下面**了——旧报告里 orig_visible 1/0/0/0/0/0、以及「原版也看不见
    条带」的误判都是这么来的。剪映放出来的原版是按 render_index 排的，这步只是把参考片
    还原成它本来的样子，不是改设计。
    """
    try:
        import importlib
        R = importlib.import_module("render_replica_from_draft")
        draft_path = resolve_draft_by_style(style_id)
        root = Path(R._discover_root("drafts"))
        base = root / style_dir_name
        if not base.is_dir():
            return None
        draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
        draft, zfix = _sort_draft_z_order(draft, scope="original")
        if meta is not None:
            meta["z_order_fix"] = zfix
        rr = {"style_id": f"{style_id}_origref", "draft": "origref", "main_segments": 0,
              "overlay_segments": 0, "matte_done": 0, "matte_failed": 0, "text_segments": 0,
              "sticker_segments": 0, "masked_main_segments": 0, "masked_overlay_segments": 0,
              "compounds_rendered": [], "unrealized": {}, "notes": []}
        mp4 = Path(R.render_timeline(draft, base, f"{style_id}_origref", out / "original_render",
                                     int(round(float(tpl.get("fps") or 30))), False, rr, depth=0))
        (out / "original_render" / "orig_report.json").write_text(
            json.dumps(rr, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return mp4 if Path(mp4).is_file() else None
    except Exception as exc:
        (out / "original_render").mkdir(parents=True, exist_ok=True)
        (out / "original_render" / "error.txt").write_text(f"{type(exc).__name__}: {exc}",
                                                            encoding="utf-8")
        return None


def _pick_compound_entry(draft: dict, t_start: float, tol: float = 0.2) -> Optional[dict]:
    """按父段的绝对起点找复合片段的 drafts 条目（不提草稿名、不靠下标）。"""
    mats = draft.get("materials") or {}
    drafts_by_id = {d["id"]: d for d in (mats.get("drafts") or []) if d.get("draft")}
    for tr in draft.get("tracks") or []:
        for s in tr.get("segments") or []:
            st = float((s.get("target_timerange") or {}).get("start") or 0) / 1e6
            if abs(st - float(t_start)) > tol:
                continue
            for r in s.get("extra_material_refs") or []:
                if r in drafts_by_id:
                    return drafts_by_id[r]
    return None


def _render_layer_gray(sub_draft: dict, base_dir: Path, work: Path, fps: int,
                       times: List[float], tag: str) -> List[Optional[Path]]:
    """把一条子时间线单独渲成带 alpha 的 MOV，再合成到中性灰底上抽帧。

    为什么在中性灰底上看：子时间线单独渲出来背景是**透明的**（alpha MOV），直接存 jpg
    是全黑；合成到灰底上才能人眼看到「到底画了几块」。"""
    import importlib
    work.mkdir(parents=True, exist_ok=True)
    R = importlib.import_module("render_replica_from_draft")
    rr = {"style_id": tag, "draft": tag, "main_segments": 0, "overlay_segments": 0,
          "matte_done": 0, "matte_failed": 0, "text_segments": 0, "sticker_segments": 0,
          "masked_main_segments": 0, "masked_overlay_segments": 0, "compounds_rendered": [],
          "unrealized": {}, "notes": []}
    out: List[Optional[Path]] = []
    try:
        mov = Path(R.render_timeline(sub_draft, base_dir, tag, work / "layer", fps, False,
                                     rr, depth=1, alpha_out=True))
    except Exception:
        return [None for _ in times]
    for i, t in enumerate(times):
        jpg = work / f"{tag}_gray_{i}_{t:.2f}.jpg"
        try:
            _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                  "-i", f"color=c=0x555555:s=1080x1920:r={fps}:d={max(0.2, t + 0.2):.3f}",
                  "-ss", f"{t:.4f}", "-i", str(mov),
                  "-filter_complex", "[1:v]setpts=PTS-STARTPTS[ov];[0:v][ov]overlay=0:0:shortest=1",
                  "-frames:v", "1", "-q:v", "2", str(jpg)])
        except Exception:
            out.append(None)
            continue
        out.append(jpg if jpg.is_file() else None)
    return out


def compound_layer_reference(out: Path, style_id: str, tpl: dict, draft_dir: Path,
                             draft: dict, window: Tuple[float, float],
                             frames_dir: Path) -> dict:
    """「原版同段」的**层级别**对比：把原版复合片段的子时间线和新子时间线各渲一遍。

    为什么还要这一份（已有「成片 vs 原版成片」）：成片对比里两边主轨素材不同，差异
    混在一起；子层单独渲在灰底上，比的才是同一件事——「拼贴底板这个结构还在不在」。
    两边都只用各自的素材，所以「块数/位置/长条比例」一致才说明结构真的搬过来了。
    """
    import numpy as np
    from PIL import Image
    res: Dict[str, Any] = {"window": [round(window[0], 3), round(window[1], 3)]}
    try:
        src_draft = json.loads(Path(resolve_draft_by_style(style_id)).read_text(encoding="utf-8"))
        import importlib
        R = importlib.import_module("render_replica_from_draft")
        src_base = Path(R._discover_root("drafts")) / Path(resolve_draft_by_style(style_id)).parent.name
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
        return res

    # 模版里这块复合片段的父段起点（用它去原版草稿里找同一个子时间线）
    parent = None
    for s in tpl.get("segments") or []:
        if s.get("compound") and (s["compound"].get("asset_profile") or {}).get("photo_layers", 0) >= 2:
            if parent is None or float(s["start_sec"]) < float(parent["start_sec"]):
                parent = s
    if parent is None:
        res["error"] = "template_has_no_photo_compound"
        return res
    orig_entry = _pick_compound_entry(src_draft, float(parent["start_sec"]))
    new_entry = _pick_compound_entry(draft, float(parent["start_sec"]), tol=1.0)
    if not orig_entry or not new_entry:
        res["error"] = ("original_compound_not_found" if not orig_entry
                        else "new_compound_not_found")
        return res

    fps = int(round(float(tpl.get("fps") or 30)))
    dur = float(window[1]) - float(window[0])
    # 窗口是**父时间线上的绝对时间**，而子时间线从 0 开始：抽帧点要先减父段起点，
    # 否则会把「0.6s 的子层」拿去和「0.6s 的成片」比（差 0.35s，比的不是同一个瞬间）。
    rel0 = max(0.05, float(window[0]) - float(parent["start_sec"]))
    times = [round(min(rel0 + dur * f, max(0.2, rel0 + dur - 0.05)), 3)
             for f in (0.25, 0.55, 0.85)]
    ref_dir = Path(frames_dir) / "compound_ref"
    # 先清掉上一轮的同名旧帧：抽帧时点会随版本变（改过相对/绝对时间口径），
    # 不清就会留下两套时点的帧——产物目录变成「混状态」就没人能判断哪张是这次的。
    for stale in ref_dir.rglob("*.jpg"):
        stale.unlink()
    ref_frames = _render_layer_gray(orig_entry["draft"], src_base, ref_dir / "orig", fps, times, "orig")
    new_frames = _render_layer_gray(new_entry["draft"], Path(draft_dir), ref_dir / "new", fps, times, "new")

    rows: List[dict] = []
    for i, t in enumerate(times):
        rf, nf = ref_frames[i], new_frames[i]
        row: Dict[str, Any] = {"t": t}
        d = _frame_delta(nf, rf)
        row["delta_vs_original_layer"] = d
        try:
            if rf is not None:
                a = np.asarray(Image.open(rf).convert("RGB").resize((270, 480)), dtype=np.float32) / 255.0
                bg = np.abs(a - (0x55 / 255.0)).mean(axis=2) > 0.06
                row["original_blocks"] = len(_components_of_mask(bg, min_area=120))
            if nf is not None:
                b = np.asarray(Image.open(nf).convert("RGB").resize((270, 480)), dtype=np.float32) / 255.0
                bg2 = np.abs(b - (0x55 / 255.0)).mean(axis=2) > 0.06
                row["new_blocks"] = len(_components_of_mask(bg2, min_area=120))
                row["new_nonbg_frac"] = round(float(bg2.mean()), 4)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["original_frame"] = str(rf.relative_to(frames_dir)) if rf else None
        row["new_frame"] = str(nf.relative_to(frames_dir)) if nf else None
        rows.append(row)
    res["rows"] = rows
    res["original_parent_start"] = float(parent["start_sec"])
    res["note"] = ("两边都是子时间线单独渲在 0x555555 中性灰底上的帧；blocks = 灰底以外的"
                   "连通区域数（原版是 5 条错开的照片条，所以不应是 1）")
    return res


def _ncc(a, b) -> float:
    """归一化互相关系数（-1~1）：同一块图像内容≈1，被别的东西盖住≈0 或负。"""
    import numpy as np
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    if a.size == 0 or a.shape != b.shape:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a.dot(b) / d) if d > 1e-6 else 0.0


def _video_layers_overlapping(draft: dict, t0: float, t1: float,
                             main_idx: Optional[int]) -> List[dict]:
    """列出一段时间里**其它视频层的层序证据**（不看内容，只看草稿结构）。

    真实草稿的 z 序是段上的 `render_index`（不是 track 数组序，两者在这份原版里就冲突：
    复合片段所在轨在数组里排在缩图层前面，但 render_index 3 > 2 说明它在上面）。
    新草稿这边看 track 数组序（渲染器只认这个）。把两边都列出来，才能判断「条带看不见」
    到底是素材尺寸造成的还是层序造成的。
    """
    from pathlib import Path as _P
    out: List[dict] = []
    for ti, tr in enumerate(draft.get("tracks") or []):
        if tr.get("type") != "video" or ti == main_idx:
            continue
        for s in tr.get("segments") or []:
            tt = s.get("target_timerange") or {}
            a = float(tt.get("start") or 0) / 1e6
            b = a + float(tt.get("duration") or 0) / 1e6
            if min(b, t1) - max(a, t0) <= 0.02:
                continue
            cl = s.get("clip") or {}
            alpha = cl.get("alpha")
            alpha = 1.0 if alpha is None else float(alpha)
            out.append({"track": ti, "start": round(a, 3), "end": round(b, 3),
                        "render_index": s.get("render_index"),
                        "scale": round(float((cl.get("scale") or {}).get("x") or 1.0), 3),
                        "alpha": round(alpha, 3),
                        "has_mask": bool(s.get("extra_material_refs"))})
    return out


def _compound_z(draft: dict, start: float, tol: float = 0.2) -> dict:
    """这块复合片段的父段在草稿里的 z 序证据：自己的 render_index / track 序，与同窗口内其它
    视频叠层比一比，回答「它到底在上还是在下」。

    为什么要这个：原版 0.45~1.667s 这片子叠了「同源缩图层」和「5 条带拼贴」，前者的
    render_index=2、后者=3——拼贴在**上面**所以看得见。套用端写回时把 render_index 当成了
    track 序号写（模板里没搬），拼贴就落到缩图层下面就看不见了。这就是「条带几何一致、
    但只读得出几条」的真原因，不靠肉眼猜。
    """
    mats = draft.get("materials") or {}
    drafts_ids = {d.get("id") for d in (mats.get("drafts") or []) if d.get("id")}
    out: Dict[str, Any] = {"start": round(start, 3)}
    me = None
    for ti, tr in enumerate(draft.get("tracks") or []):
        if tr.get("type") != "video":
            continue
        for s in tr.get("segments") or []:
            st = float((s.get("target_timerange") or {}).get("start") or 0) / 1e6
            if abs(st - start) > tol:
                continue
            if drafts_ids & set(s.get("extra_material_refs") or []):
                me = {"track": ti, "render_index": s.get("render_index")}
                break
        if me:
            break
    if not me:
        out["found"] = False
        return out
    t0 = me["track"]
    out.update({"found": True, "track": t0, "render_index": me.get("render_index")})
    tt = None
    for s in (draft.get("tracks") or [])[t0].get("segments") or []:
        st = float((s.get("target_timerange") or {}).get("start") or 0) / 1e6
        if abs(st - start) <= tol:
            tt = s
            break
    a = float((tt.get("target_timerange") or {}).get("start") or 0) / 1e6 if tt else start
    b = a + (float((tt.get("target_timerange") or {}).get("duration") or 0) / 1e6 if tt else 0.0)
    others = [x for x in _video_layers_overlapping(draft, a, b, _main_track_idx(draft))
              if x["track"] != t0]
    other_ri = [x["render_index"] for x in others if x.get("render_index") is not None]
    out["other_layers"] = others
    out["other_max_track"] = max((x["track"] for x in others), default=None)
    out["other_max_render_index"] = max(other_ri) if other_ri else None
    ri = me.get("render_index")
    out["on_top_by_render_index"] = (ri is not None and (not other_ri or ri > max(other_ri)))
    out["on_top_by_track_order"] = (out["other_max_track"] is None or t0 > out["other_max_track"])
    out["on_top"] = bool(out["on_top_by_render_index"] and out["on_top_by_track_order"])
    return out


def _main_track_idx(draft: dict) -> Optional[int]:
    mt = _main_track_of(draft)
    if not mt:
        return None
    for ti, tr in enumerate(draft.get("tracks") or []):
        if tr is mt:
            return ti
    return None


# ---------------------------------------------------------------- z 序（谁盖住谁）
# 本项目渲染器「轨序即 z 序」，剪映草稿的 z 序在**段上的 render_index**。验证端要回答两件事：
#   ① 原版/新版草稿里，同一时刻哪一层在上（结构真值，不需要渲）；
#   ② 它真的能在那份成片上看见吗（层单独渲 + 可见性判定，防「结构说在上、实际被抹掉」）。


def _draft_track_z_key(tr: dict, idx: int) -> float:
    """一条草稿轨的 z 序键 = 段上 render_index 的最大值（与套用端 `_track_z_key` 同一口径）。"""
    vals = [float(s["render_index"]) for s in (tr.get("segments") or [])
            if isinstance(s.get("render_index"), (int, float))]
    return max(vals) if vals else float(idx)


def _sort_draft_z_order(draft: dict, *, scope: str = "top") -> Tuple[dict, dict]:
    """把草稿轨序按 render_index 升序排好（递归进复合片段子稿），返回副本 + 台账。

    为什么验证端也要排：参考片是「原版草稿 + 原版素材 + 同一个渲染器」渲的，而渲染器只认轨序。
    直接拿原版草稿去渲，渲染器会把拼贴画在缩图层**下面**（数组序），参考片自己就把拼贴盖住
    了——旧报告里 orig_visible 1/0/0/0/0/0 就是这么来的，拿它做基准等于拿错的真值。
    按原版 render_index 排一遍再渲，参考片才是「剪映放出来的原版」。
    音频轨留末尾（没有画面 z 序）；同键稳定（保持原相对顺序）。
    """
    d = json.loads(json.dumps(draft))
    levels: List[dict] = []

    def _one(level: dict, sc: str) -> None:
        trs = level.get("tracks") or []
        keyed = [(_draft_track_z_key(t, i), i) for i, t in enumerate(trs)]
        visual = [k for k in keyed if trs[k[1]].get("type") != "audio"]
        audio = [k for k in keyed if trs[k[1]].get("type") == "audio"]
        visual.sort(key=lambda k: (k[0], k[1]))
        order = [k[1] for k in visual + audio]
        level["tracks"] = [trs[i] for i in order]
        levels.append({"scope": sc, "tracks": len(order),
                       "moved_tracks": sum(1 for pos, old in enumerate(order) if old != pos),
                       "new_order_old_indices": order})
        for mat in (level.get("materials") or {}).get("drafts") or []:
            sub = mat.get("draft")
            if isinstance(sub, dict):
                _one(sub, f"{sc}/draft:{(mat.get('id') or '')[:8]}")

    _one(d, scope)
    return d, {"levels": levels,
               "moved_tracks": sum(l["moved_tracks"] for l in levels),
               "basis": "轨级 z 键 = 段上 render_index 的 max；音频轨留末尾；同键稳定"}


def _draft_video_layers(draft: dict) -> List[dict]:
    """草稿里**主轨以外**的视频叠层（只带结构指纹，不带素材路径）。"""
    from replica_template import layer_size_ratio
    mats = draft.get("materials") or {}
    canvas = draft.get("canvas_config") or {}
    cw = float(canvas.get("width") or 1080)
    ch = float(canvas.get("height") or 1920)
    drafts_ids = {d.get("id") for d in (mats.get("drafts") or []) if d.get("id")}
    by_id = {m["id"]: m for m in (mats.get("videos") or []) if m.get("id")}
    main_ti = _main_track_idx(draft)
    out: List[dict] = []
    for ti, tr in enumerate(draft.get("tracks") or []):
        if tr.get("type") != "video" or ti == main_ti:
            continue
        for s in tr.get("segments") or []:
            tg = s.get("target_timerange") or {}
            a = float(tg.get("start") or 0) / 1e6
            dur = float(tg.get("duration") or 0) / 1e6
            cl = s.get("clip") or {}
            scale = float((cl.get("scale") or {}).get("x") or 1.0)
            alpha = 1.0 if cl.get("alpha") is None else float(cl.get("alpha"))
            mat = by_id.get(s.get("material_id")) or {}
            size = layer_size_ratio(mat, cw, ch, scale) if mat.get("width") else None
            cover = round(size[0] * size[1], 3) if size else None
            is_comp = bool(drafts_ids & set(s.get("extra_material_refs") or []))
            out.append({"track": ti, "start": round(a, 3), "end": round(a + dur, 3),
                        "scale": round(scale, 3), "alpha": round(alpha, 3),
                        "render_index": s.get("render_index"),
                        "kind": "compound" if is_comp else str(mat.get("type") or "video"),
                        "cover": cover})
    return out


def _layer_qualifies(layer: dict) -> bool:
    """「同时间重叠且都满幅/半透明」的层才算 z 序有意义的一对（小窗叠层另当别论）。"""
    if float(layer.get("alpha") or 0.0) <= 0.05:
        return False          # 全透明层：不存在「盖不盖住」
    cover = layer.get("cover")
    return (cover is None or float(cover) >= 0.85) or float(layer.get("alpha") or 1.0) <= 0.98


def _layer_pairs(layers: List[dict]) -> List[dict]:
    out: List[dict] = []
    for i, a in enumerate(layers):
        if not _layer_qualifies(a):
            continue
        for b in layers[i + 1:]:
            if b["track"] == a["track"] or not _layer_qualifies(b):
                continue
            ov0, ov1 = max(a["start"], b["start"]), min(a["end"], b["end"])
            if ov1 - ov0 <= 0.02:
                continue
            out.append({"a": a, "b": b, "overlap": [round(ov0, 3), round(ov1, 3)]})
    return out


def _match_layers(src: dict, candidates: List[dict]) -> List[dict]:
    """原版一层 → 新版里对应的**那一组**层段（靠「同类 + 同 scale + 时间窗重叠」匹配，不看素材）。

    为什么不用素材匹配：套用端本来就换了一批素材，路径/指纹都对不上；而**结构指纹**
    （复合片段 vs 普通视频、clip.scale、在哪个时间窗里）是原版与新版都该一致的。
    为什么返回一组而不是一个：套用端会把一段长叠层按新素材长度**切成几段**（原版 t1 那条
    5.3~9.65s 的层在新稿里就是 4 段），一对一匹配会直接找不到；一组里取最高轨位就是
    「这一组（这一层设计）在新稿里被放在哪里」。
    """
    out: List[dict] = []
    for cand in candidates:
        if (cand["kind"] == "compound") != (src["kind"] == "compound"):
            continue
        if abs(cand["scale"] - src["scale"]) > 0.05:
            continue
        if src["kind"] == "compound" and (abs(cand["start"] - src["start"]) > 0.35
                                          or abs(cand["end"] - src["end"]) > 0.35):
            # 复合片段不会被切成几段（父段时间跟着锚点走），所以要求起止都对得上：
            # 不要求的话原版的 0.45s 拼贴和 5.88s 拼贴（都是 compound/scale 1.0）会互相串。
            continue
        ov = min(cand["end"], src["end"]) - max(cand["start"], src["start"])
        short = min(cand["end"] - cand["start"], src["end"] - src["start"])
        if ov < 0.6 * max(1e-6, short):
            continue
        out.append(cand)
    return out


def _top_of_group(group: List[dict], t_mid: float | None = None) -> dict | None:
    """一组层段里**位置最高**的那层（轨位最大）；给了 t_mid 就优先取盖住该时刻的那段。"""
    if not group:
        return None
    pool = group
    if t_mid is not None:
        covering = [g for g in group if g["start"] - 0.05 <= t_mid <= g["end"] + 0.05]
        if covering:
            pool = covering
    return max(pool, key=lambda g: g["track"])


def _us_of(sec: float) -> int:
    return int(round(max(0.0, float(sec)) * 1e6))


def _solo_layer_draft(draft: dict, layer: dict, t0: float, t1: float) -> Optional[dict]:
    """只留这一层的迷你草稿（时间裁到 [t0,t1] 并反到 0 起），用来「层单独渲染」。"""
    trs = draft.get("tracks") or []
    if layer["track"] >= len(trs):
        return None
    tr = trs[layer["track"]]
    segs: List[dict] = []
    for s in tr.get("segments") or []:
        tg = s.get("target_timerange") or {}
        a = float(tg.get("start") or 0) / 1e6
        d = float(tg.get("duration") or 0) / 1e6
        lo, hi = max(a, t0), min(a + d, t1)
        if hi - lo <= 0.02:
            continue
        s2 = json.loads(json.dumps(s))
        s2["target_timerange"] = {"start": _us_of(lo - t0), "duration": _us_of(hi - lo)}
        src = s.get("source_timerange") or {}
        # 段被窗口从中间切开时源入点跟着走（只在窗口端点那一段发生）
        s2["source_timerange"] = {"start": _us_of(float(src.get("start") or 0) / 1e6 + (lo - a)),
                                  "duration": _us_of(hi - lo)}
        segs.append(s2)
    if not segs:
        return None
    mini = json.loads(json.dumps(draft))
    mini["tracks"] = [{"id": tr.get("id"), "is_default_name": True,
                       "segments": segs, "type": "video"}]   # 去掉 flag，让它成为这条迷你时间线的主轨
    mini["duration"] = _us_of(t1 - t0)
    mini.pop("keyframes", None)
    return mini


def _solo_layer_frames(drf: dict, base: Path, layer: dict, t0: float, t1: float, t_mid: float,
                       work: Path, fps: int, tag: str) -> Optional[Path]:
    """「层单独渲染」一张灰底帧。

    复合片段层直接渲它的**子时间线**（它自己就是这层的内容；把父段塞进迷你草稿会让
    渲染器在子稿里重建占位素材，与铺在成片里的不是同一份东西）；普通视频层用只留这一层的
    迷你草稿（窗口裁到 [t0,t1] 并反到 0 起，1~2 秒的短渲）。两边都落在 0x555555 灰底上，
    才能用「灰底以外的块」做可见性判定。
    """
    if layer.get("kind") == "compound":
        entry = _pick_compound_entry(drf, float(layer["start"]), tol=0.2)
        sub = (entry or {}).get("draft")
        if isinstance(sub, dict):
            rel = max(0.02, min(float(t_mid) - float(layer["start"]),
                                max(0.05, float(layer["end"]) - float(layer["start"]) - 0.05)))
            jpgs = _render_layer_gray(sub, base, work, fps, [round(rel, 3)], tag)
            return jpgs[0] if jpgs else None
    solo = _solo_layer_draft(drf, layer, float(t0), float(t1))
    if solo is None:
        return None
    jpgs = _render_layer_gray(solo, base, work, fps, [round(max(0.02, t_mid - t0), 3)], tag)
    return jpgs[0] if jpgs else None


def _visible_blocks(final_jpg: Optional[Path], solo_jpg: Optional[Path]) -> dict:
    """层单独渲的每一块，在成片同一位置还认不认得出（NCC ≥ 0.5 算看得见）。"""
    import numpy as np
    from PIL import Image
    if final_jpg is None or solo_jpg is None:
        return {"blocks": None, "visible": None, "ncc": []}
    try:
        solo = np.asarray(Image.open(solo_jpg).convert("RGB").resize((270, 480)),
                          dtype=np.float32) / 255.0
        fin = np.asarray(Image.open(final_jpg).convert("RGB").resize((270, 480)),
                         dtype=np.float32) / 255.0
    except Exception as exc:
        return {"blocks": None, "visible": None, "ncc": [], "error": f"{type(exc).__name__}"}
    mask = np.abs(solo - (0x55 / 255.0)).mean(axis=2) > 0.06
    boxes = _components_of_mask(mask, min_area=120, max_boxes=8)
    vis, nccs = 0, []
    for b in boxes:
        y0, y1 = max(0, b["y0"]), min(479, b["y1"])
        x0, x1 = max(0, b["x0"]), min(269, b["x1"])
        if y1 - y0 < 3 or x1 - x0 < 3:
            continue
        lc = solo[y0:y1 + 1, x0:x1 + 1].mean(axis=2)
        if float(lc.std()) < 0.04:
            continue
        c = _ncc(lc, fin[y0:y1 + 1, x0:x1 + 1].mean(axis=2))
        nccs.append(round(c, 3))
        if c >= 0.5:
            vis += 1
    return {"blocks": len(boxes), "visible": vis,
            "visible_frac": round(vis / max(1, len(boxes)), 3), "ncc": nccs}


def z_layer_order_audit(final_mp4: Optional[Path], draft: dict, draft_dir: Path,
                        style_id: str, tpl: dict, frames_dir: Path, *,
                        reference_mp4: Optional[Path] = None,
                        max_render_pairs: int = 3) -> dict:
    """**z 序回归防线**：对每对「同时间重叠且都满幅/半透明」的视频层，
    比较原版与新版**谁在上面**；不一致就报 `z_order_inverted`。

    判「谁在上面」两个口径都用上：
      · 结构：原版 = 段上 render_index（剪映真值），新版 = 轨序（本项目渲染器真值）；
      · 像素：把该层**单独渲**在 0x555555 灰底上，再拿它的每一块去成片里比内容（NCC ≥ 0.5 = 看得见）。
        结构说在上的那层如果实际看不见（被抹掉/遮罩/抠像失败），计 `top_layer_not_visible`——
        这是「结构对了但成片没上」的另一类坑，不能只看结构就放行。
    原版侧比的是**按 render_index 排好序再渲的参考片**（见 _sort_draft_z_order），否则参考片自己
    就把拼贴盖住了，等于拿错的真值来对账。
    """
    res: Dict[str, Any] = {"window": None, "pairs": [], "z_order_inverted": 0,
                           "top_layer_not_visible": 0, "ok": False}
    if final_mp4 is None or not Path(final_mp4).is_file():
        res["skipped"] = "no_final_render"
        return res
    try:
        src_draft = json.loads(Path(resolve_draft_by_style(style_id)).read_text(encoding="utf-8"))
        import importlib
        R = importlib.import_module("render_replica_from_draft")
        src_base = Path(R._discover_root("drafts")) / Path(resolve_draft_by_style(style_id)).parent.name
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
        return res
    # 原版这边统一用「按 render_index 排好序」的副本（与参考片同一口径，子稿也递归排了）：
    # 否则层单独渲出来的是数组序下的原版，跟参考片对不上，z 序比较也就成了两个口径。
    src_draft, res["original_z_fix"] = _sort_draft_z_order(src_draft, scope="original")

    orig_layers = _draft_video_layers(src_draft)
    new_layers = _draft_video_layers(draft)
    pairs = _layer_pairs(orig_layers)
    res["orig_layers"] = len(orig_layers)
    res["new_layers"] = len(new_layers)
    res["candidate_pairs"] = len(pairs)
    if not pairs:
        res.update({"ok": True, "note": "原版没有「同时间重叠且都满幅/半透明」的视频层对"})
        return res

    work = Path(frames_dir) / "zorder"
    work.mkdir(parents=True, exist_ok=True)
    for stale in work.rglob("*.jpg"):
        stale.unlink()
    fps = int(round(float(tpl.get("fps") or 30)))

    rows: List[dict] = []
    for k, pair in enumerate(pairs):
        a, b = pair["a"], pair["b"]
        row: Dict[str, Any] = {"idx": k, "overlap": pair["overlap"],
                              "orig": {"a": _layer_brief(a), "b": _layer_brief(b)},
                              "inverted": None}
        # 原版谁在上：render_index 大者
        ri_a, ri_b = a.get("render_index"), b.get("render_index")
        if ri_a is None or ri_b is None:
            row["skipped"] = "render_index_missing_in_original"
            rows.append(row)
            continue
        row["orig"]["top"] = "a" if float(ri_a) > float(ri_b) else "b"
        # 新版对应层组（结构指纹匹配；套用端会把长叠层切成几段，所以要按「组」比）
        ga = _match_layers(a, new_layers)
        gb = _match_layers(b, new_layers)
        t_mid = round((pair["overlap"][0] + pair["overlap"][1]) / 2, 3)
        ta, tb = _top_of_group(ga, t_mid), _top_of_group(gb, t_mid)
        if ta is None or tb is None:
            row["skipped"] = "new_layer_not_matched"
            row["new_matched"] = {"a": len(ga), "b": len(gb)}
            rows.append(row)
            continue
        if ta["track"] == tb["track"]:
            row["skipped"] = "matched_to_same_track"
            rows.append(row)
            continue
        row["new"] = {"a": _layer_brief(ta), "b": _layer_brief(tb),
                      "a_group_tracks": sorted({g["track"] for g in ga}),
                      "b_group_tracks": sorted({g["track"] for g in gb})}
        row["new"]["top"] = "a" if ta["track"] > tb["track"] else "b"
        row["inverted"] = row["new"]["top"] != row["orig"]["top"]
        if row["inverted"]:
            res["z_order_inverted"] += 1
        row["_top_layer"] = {"a": ta, "b": tb}
        rows.append(row)

    # 像素证据（只对前 max_render_pairs 对做，优先做判反转的、以及涉及复合片段的）
    def _rank(r: dict) -> tuple:
        return (0 if r.get("inverted") else 1,
                0 if "compound" in json.dumps(r.get("orig") or {}) else 1,
                r["idx"])
    judged = [r for r in rows if r.get("inverted") is not None]
    for row in sorted(judged, key=_rank)[:max_render_pairs]:
        t_mid = round((row["overlap"][0] + row["overlap"][1]) / 2, 3)
        ev: Dict[str, Any] = {"t": t_mid}
        for tag, drf, base, mp4, layers in (
                ("new", draft, Path(draft_dir), final_mp4, new_layers),
                ("orig", src_draft, src_base, reference_mp4, orig_layers)):
            if mp4 is None:
                ev[tag] = {"skipped": "no_render"}
                continue
            top_key = row[tag]["top"]
            brief = row[tag][top_key]
            # 从层列表里重新定位「结构说在上」的那一层（同轨同起点）
            cand = [l for l in layers if l["track"] == brief["track"]
                    and abs(l["start"] - brief["start"]) < 0.02]
            if not cand:
                ev[tag] = {"skipped": "top_layer_not_located"}
                continue
            wdir = work / f"{tag}{row['idx']}"
            solo_jpg = _solo_layer_frames(drf, base, cand[0], row["overlap"][0],
                                          row["overlap"][1], t_mid, wdir, fps,
                                          f"{tag}{row['idx']}")
            if solo_jpg is None:
                ev[tag] = {"skipped": "solo_render_failed"}
                continue
            fin_jpg = _grab_frame(Path(mp4), t_mid, wdir / f"final_{t_mid:.2f}s.jpg")
            vb = _visible_blocks(fin_jpg, solo_jpg)
            # 这一对的上层若还被**更上面的第三层**盖住，那它看不见与「谁在上」无关，
            # 不能拿来判失败（否则会把「上面还压着一层满幅不透明层」误报成 z 序问题）。
            upper = [l for l in layers
                     if l["start"] - 0.05 <= t_mid <= l["end"] + 0.05
                     and l["track"] > brief["track"]
                     and float(l.get("alpha") or 1.0) > 0.5]
            ev[tag] = {**vb,
                       "solo_frame": str(solo_jpg.relative_to(frames_dir)) if solo_jpg else None,
                       "final_frame": str(fin_jpg.relative_to(frames_dir)) if fin_jpg else None,
                       "top_track": brief["track"], "top_scale": brief["scale"],
                       "top_kind": brief["kind"],
                       "covered_by_upper_track": (max(l["track"] for l in upper)
                                                  if upper else None)}
        row["evidence"] = ev
        nv = (ev.get("new") or {}).get("visible_frac")
        new_ev = ev.get("new") or {}
        if (nv is not None and nv < 0.5 and new_ev.get("covered_by_upper_track") is None):
            row["top_layer_not_visible"] = True
            res["top_layer_not_visible"] += 1

    for row in rows:
        row.pop("_top_layer", None)
    res["pairs"] = rows
    res["pairs_judged"] = len(judged)
    res["pairs_unmatched"] = sum(1 for r in rows if r.get("skipped"))
    res["ok"] = bool(res["z_order_inverted"] == 0 and res["top_layer_not_visible"] == 0)
    res["verdict"] = ("可接受：重叠层对的上下关系与原版一致（结构 + 层单独渲的可见性都对得上）"
                      if res["ok"] else
                      f"需修：{res['z_order_inverted']} 对层序与原版相反（z_order_inverted）"
                      f" · {res['top_layer_not_visible']} 对「结构说在上、成片里看不见」")
    return res


def _layer_brief(layer: dict) -> dict:
    """层的最小指纹（报告里不出现素材路径/文件名）。"""
    return {"track": layer["track"], "start": layer["start"], "end": layer["end"],
            "scale": layer["scale"], "alpha": layer["alpha"],
            "render_index": layer.get("render_index"), "kind": layer["kind"],
            "cover": layer.get("cover")}


def strip_visibility(final_mp4: Path, main_only: Optional[Path], draft: dict,
                     draft_dir: Path, style_id: str, tpl: dict, frames_dir: Path,
                     window: Tuple[float, float] = (0.53, 1.63), n: int = 6,
                     fps: int = 60,
                     reference_mp4: Optional[Path] = None) -> dict:
    """拼贴条带「到底看得见几条」：连通域计数，原版该时刻 vs 新成片。

    上一轮只说了「三条带子只部分读得出来，像是带子更少」，没分清是**素材尺寸/比例**
    （新图不是 1242×344 那种长条）还是**层叠顺序**（被上层不透明层盖住）。这里把判据拆开：
      ① `layer_blocks`：两边（原版子时间线 / 新子时间线）各自**单独**渲在中性灰底上，数连通域
         → 「这一层自己画出了几块」= 几何/素材比例的直接结果；
      ② `visible_in_final`：拿①里每一块的位置去成片里比同一块内容（NCC ≥ 0.5 算透传上屏），
         数「真的看得到的块」→ 被上层不透明层盖住就不算；
      ③ `z_order`：把这段时间里其它视频层的 render_index（原版）与 track 序（新草稿）列出来。
    ①不少但②少 = 层叠顺序；①少 = 素材尺寸/比例。两张对照帧（成片 / 只渲主轨）都落盘。
    """
    import numpy as np
    from PIL import Image

    frames_dir = Path(frames_dir)
    strip_dir = frames_dir / "strips"
    strip_dir.mkdir(parents=True, exist_ok=True)
    for stale in strip_dir.glob("*.jpg"):
        stale.unlink()
    res: Dict[str, Any] = {"window": [round(window[0], 3), round(window[1], 3)],
                           "samples": [], "frame_pairs": []}

    # 目标：模版里最早出现的那块「照片拼贴」复合片段（原版 0.45~1.667s 的 5 条带）
    parent = None
    for s in tpl.get("segments") or []:
        c = s.get("compound") or {}
        if (c.get("asset_profile") or {}).get("photo_layers", 0) >= 2:
            if parent is None or float(s["start_sec"]) < float(parent["start_sec"]):
                parent = s
    if parent is None:
        return {"skipped": "template_has_no_photo_compound"}
    p0 = float(parent["start_sec"])

    try:
        import importlib
        R = importlib.import_module("render_replica_from_draft")
        src_draft = json.loads(Path(resolve_draft_by_style(style_id)).read_text(encoding="utf-8"))
        src_base = Path(R._discover_root("drafts")) / Path(resolve_draft_by_style(style_id)).parent.name
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
        return res
    orig_entry = _pick_compound_entry(src_draft, p0)
    new_entry = _pick_compound_entry(draft, p0, tol=1.0)
    if not orig_entry or not new_entry:
        res["error"] = "original_compound_not_found" if not orig_entry else "new_compound_not_found"
        return res

    times = [round(window[0] + (window[1] - window[0]) * i / max(1, n - 1), 3) for i in range(n)]
    rel_times = [round(max(0.05, t - p0), 3) for t in times]
    ref_dir = strip_dir / "layer"
    orig_layers = _render_layer_gray(orig_entry["draft"], src_base, ref_dir / "orig", fps,
                                     rel_times, "orig")
    new_layers = _render_layer_gray(new_entry["draft"], Path(draft_dir), ref_dir / "new", fps,
                                    rel_times, "new")

    # 原版/新草稿的主轨下标（用于排除主轨本身）
    def _main_idx(d: dict) -> Optional[int]:
        mt = _main_track_of(d)
        if not mt:
            return None
        for ti, tr in enumerate(d.get("tracks") or []):
            if tr is mt:
                return ti
        return None

    rows: List[dict] = []
    for i, (t, lt) in enumerate(zip(times, rel_times)):
        row: Dict[str, Any] = {"t": t, "layer_t": lt}
        # ① 层单独渲：两边同一套常数数连通域
        for tag, lp in (("orig", orig_layers[i]), ("new", new_layers[i])):
            if not lp:
                row[f"{tag}_layer_blocks"] = None
                row[f"{tag}_boxes"] = []
                continue
            a = np.asarray(Image.open(lp).convert("RGB").resize((270, 480)),
                           dtype=np.float32) / 255.0
            mask = np.abs(a - (0x55 / 255.0)).mean(axis=2) > 0.06
            boxes = _components_of_mask(mask, min_area=120, max_boxes=12)
            row[f"{tag}_layer_blocks"] = len(boxes)
            row[f"{tag}_boxes"] = boxes
            row[f"{tag}_layer_frame"] = str(lp.relative_to(frames_dir))
        # ② 成片 / 只渲主轨 对照帧（两边都落盘）
        fa = _grab_frame(final_mp4, t, strip_dir / f"strip{i + 1:02d}_{t:.2f}s_final.jpg")
        fb = None
        if main_only is not None:
            fb = _grab_frame(main_only, t, strip_dir / f"strip{i + 1:02d}_{t:.2f}s_mainonly.jpg")
        row["final_frame"] = str(fa.relative_to(frames_dir)) if fa else None
        row["main_only_frame"] = str(fb.relative_to(frames_dir)) if fb else None
        row["delta_vs_main_only"] = _frame_delta(fa, fb)
        # ③ 把每一块拿回成片里比内容：NCC ≥ 0.5 才算「这块真的在成片上看得见」
        for tag, mp4 in (("orig", reference_mp4), ("new", final_mp4)):
            boxes = row.get(f"{tag}_boxes") or []
            if not boxes or mp4 is None:
                row[f"{tag}_visible_in_final"] = None
                continue
            fdir = strip_dir / tag
            fdir.mkdir(parents=True, exist_ok=True)
            fp = _grab_frame(mp4, t, fdir / f"strip{i + 1:02d}_{t:.2f}s.jpg")
            if fp is None:
                row[f"{tag}_visible_in_final"] = None
                continue
            fa_img = np.asarray(Image.open(fp).convert("RGB").resize((270, 480)),
                                dtype=np.float32) / 255.0
            layer_img = row.get(f"{tag}_layer_frame")
            if not layer_img:
                row[f"{tag}_visible_in_final"] = None
                continue
            la_img = np.asarray(Image.open(frames_dir / layer_img).convert("RGB")
                                .resize((270, 480)), dtype=np.float32) / 255.0
            vis, nccs = 0, []
            for b in boxes:
                y0, y1 = max(0, b["y0"]), min(479, b["y1"])
                x0, x1 = max(0, b["x0"]), min(269, b["x1"])
                if y1 - y0 < 3 or x1 - x0 < 3:
                    continue
                lc = la_img[y0:y1 + 1, x0:x1 + 1].mean(axis=2)
                # 近纯色的块不算（内容没有起伏时 NCC 不稳定，容易误判成「看得见」）
                if float(lc.std()) < 0.04:
                    continue
                c = _ncc(lc, fa_img[y0:y1 + 1, x0:x1 + 1].mean(axis=2))
                nccs.append(round(c, 3))
                if c >= 0.5:
                    vis += 1
            row[f"{tag}_visible_in_final"] = vis
            row[f"{tag}_ncc"] = nccs
        rows.append(row)

    res["samples"] = rows
    res["frame_pairs"] = [{"t": r["t"], "final": r.get("final_frame"),
                          "main_only": r.get("main_only_frame")} for r in rows]
    sum_orig = sum(r.get("orig_layer_blocks") or 0 for r in rows)
    sum_new = sum(r.get("new_layer_blocks") or 0 for r in rows)
    vis_orig = sum(r.get("orig_visible_in_final") or 0 for r in rows)
    vis_new = sum(r.get("new_visible_in_final") or 0 for r in rows)
    res["summary"] = {
        "frames": len(rows),
        "orig_layer_blocks_total": sum_orig,
        "new_layer_blocks_total": sum_new,
        "orig_visible_in_final_total": vis_orig,
        "new_visible_in_final_total": vis_new,
        "orig_layer_blocks_per_frame": [r.get("orig_layer_blocks") for r in rows],
        "new_layer_blocks_per_frame": [r.get("new_layer_blocks") for r in rows],
        "orig_visible_per_frame": [r.get("orig_visible_in_final") for r in rows],
        "new_visible_per_frame": [r.get("new_visible_in_final") for r in rows],
    }

    # ③ 层序证据：这段时间里到底谁在上面
    w0, w1 = window
    orig_layers_list = _video_layers_overlapping(src_draft, w0, w1, _main_idx(src_draft))
    new_layers_list = _video_layers_overlapping(draft, w0, w1, _main_idx(draft))
    z_orig = _compound_z(src_draft, p0)
    z_new = _compound_z(draft, p0)
    res["z_order"] = {
        "original_overlapping_layers": orig_layers_list,
        "new_overlapping_layers": new_layers_list,
        "original_compound": z_orig,
        "new_compound": z_new,
        "note": ("原版看段上的 render_index（剪映的 z 序），新草稿看 track 数组序（本项目渲染器只认它）；"
                 "两边口径不同，正是「条带几何一致但能见度不同」的根源"),
    }
    ri = [x.get("render_index") for x in orig_layers_list if x.get("render_index") is not None]
    res["z_order"]["original_max_render_index"] = max(ri) if ri else None
    res["z_order"]["new_max_track"] = max((x["track"] for x in new_layers_list), default=None)

    # 归因：先看层单独渲的块数（几何/素材比例），再看层序证据
    #   原版按剪映语义看 render_index（拼贴在缩图层上面 → 看得见）；
    #   新草稿按本项目渲染器的 track 数组序看（拼贴在缩图层下面 → 看不见）。
    if sum_new < sum_orig:
        cause = "material_size_or_aspect"
    elif (z_orig.get("on_top_by_render_index") and not z_new.get("on_top_by_track_order")):
        cause = "layer_order"
    elif vis_new < vis_orig:
        cause = "layer_order"
    else:
        cause = "none"
    res["cause"] = cause
    res["verdict"] = (
        "需继续修：模板没搬段上的 render_index，新草稿里这块拼贴被同源缩图层盖住"
        if cause == "layer_order" else
        "需继续修：新图的取景比例不是原版长条（裁出来不是「带子」）"
        if cause == "material_size_or_aspect" else
        "可接受：两边几何与能见度一致，不构成本轮降级")
    res["cause_basis"] = ("①成片里可见条带（NCC≥0.5 的连通块）少而层单独渲的块数一致 → 层叠顺序；"
                          "①本身就少 → 素材尺寸/比例（裁切后的取景不是原版长条比例）")
    return res


def check_overlay_ghosting(draft: dict, draft_dir: Path, full_mp4: Path, main_mp4: Optional[Path],
                           frames_dir: Path, *, rejected: Optional[List[dict]] = None,
                           spot_times: Optional[List[float]] = None, tol: float = 0.02,
                           cutout_evidence: Optional[dict] = None) -> dict:
    """逐对验重影（可重复检查）：对**每一对时间重叠的 video 段**取重叠中点那一帧，
    与「只渲主轨」版本逐像素比。

    判 FAIL 的条件是：① 两层是**同一个素材文件**（同源）；② 与「只渲主轨」对照版的差异
    **可见**（mae > tol 或 99 分位差 > 0.35——只看平均差会被大面积同色背景稀释，
    取「或」是因为只在局部叠一块（小窗/竖条）平均差很小、但那块地方人眼一眼就看得见）。
    为什么不再加「满幅」条件：同源两层只要画面不一样，就说明同一段片子被以**另一种取景**
    又画了一遍（实测 3.0s 主轨 cover 裁切× 叠层 contain 只占 46% 画面，依旧是人眼一眼
    就看出的重影），满幅与否不能当免死金牌；它只作为元数据记录。
    另附人脸证据：若整帧人脸数 > 对照版且 ≥2，就是「同一张脸出现两次」的硬证。

    **两条同源例外，都必须拿证据换**：
    ① 抠像三明治：同源对里若顶层是抠像层（素材带 `matting` → 渲染时走 Vision alpha +
       alphamerge），它就不是「不透明同源第二份画面」，而是原版设计（主轨那条片子的抠像人像
       压在背景上）。但不能光看草稿标记就放行——必须 `cutout_evidence` 里**这一层**实测到
       0 < alpha 覆盖率 < 0.98（真有透明区），否则仍判 FAIL。
    ② 模糊背景板：顶层是同一素材、**同一入点（≤0.06s）**、而且真带「模糊」特效
       （sigma≥8px）——那是原版三明治的模糊底（同一条片子的虚化版），不是“以另一种取景
       又画一遍”。模糊底与主轨的**取景可以不同**（原版底板是个 0.89 幅宽的带，主轨是 1.2 倍谁近），
       但那只是“同一帧被虚化+裁切”，不是第二个帧；真正防重影的钥在“同入点 + 真模糊”：
       没有这两条的普通同源叠层依然判 FAIL。

    还会对**被拒接的叠层**（apply 报告的 overlay_left_blank_detail）做点检：这些时间点上
    成片应与「只渲主轨」**几乎相同**（mae ≈ 0）——不是重影消失，而是那一层根本没上。
    """
    pairs_all = video_overlap_pairs(draft, draft_dir,
                                   int((draft.get("canvas_config") or {}).get("width") or 1080),
                                   int((draft.get("canvas_config") or {}).get("height") or 1920))
    # 复合片段的子层对账：**不同素材的两层不可能重影**（像素不是同一份），所以只对
    # 涉及子层的「同源」对真抽帧验，其余只计数。这不是放水：同源就是重影的充要前提，
    # 而子层同源对（两块复合片段时间重叠又用了同一张图）现在全部会被抽到。
    pairs = [p for p in pairs_all
             if not ((p.get("a_in_compound") or p.get("b_in_compound")) and not p["same_source"])]
    compound_pairs_skipped = len(pairs_all) - len(pairs)
    # 对比帧单独落 frames/ghost/：frames/ 顶层只留那几张人眼要看的关键帧，不混取证帧
    frames_dir = Path(frames_dir) / "ghost"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("*.jpg"):
        try:
            stale.unlink()
        except OSError:
            pass
    rows: List[dict] = []
    if main_mp4 is None:
        return {"ok": False, "reason": "main_only_render_failed", "pairs_checked": 0, "detail": []}
    for k, p in enumerate(pairs):
        t = float(p["overlap_mid"])
        fa = _grab_frame(full_mp4, t, frames_dir / f"pair{k:02d}_full_{t:.2f}s.jpg")
        fb = _grab_frame(main_mp4, t, frames_dir / f"pair{k:02d}_main_{t:.2f}s.jpg")
        delta = _frame_delta(fa, fb)
        faces = _faces_in([x for x in (fa, fb) if x])
        n_full = faces.get(str(fa), 0) if fa else 0
        n_main = faces.get(str(fb), 0) if fb else 0
        top_opaque_full = (p["top_alpha"] >= 0.99
                           and (p["top_cover"] is None or p["top_cover"] >= 0.85))
        visible = bool(delta and (delta["mae"] > tol or delta["p99"] > 0.35))
        cov = None
        if p.get("top_is_cutout") and cutout_evidence:
            cov = (cutout_evidence.get("coverage_by_seg") or {}).get(p.get("top_seg_id"))
        cutout_ok = bool(p.get("top_is_cutout") and cov is not None and 0.0 < float(cov) < 0.98)
        blur_sigma = float(p.get("top_blur_sigma") or 0.0)
        blur_board_ok = bool(p["same_source"] and blur_sigma >= 8.0 and p.get("same_in_point"))
        if p["same_source"] and visible and cutout_ok:
            verdict = "PASS"
            why = (f"同源但顶层是抠像层：实测 alpha 覆盖率 {float(cov):.2%}（有透明区）= 原版设计"
                   f"（主轨那条片子的抠像人像压在背景上），不是不透明同源第二份画面")
        elif p["same_source"] and visible and blur_board_ok:
            verdict = "PASS"
            why = (f"同源但顶层是模糊背景板：与下层同入点、且真带模糊（sigma={blur_sigma:.1f}px）"
                   f"= 同一条片子的虚化底，不是另一种取景的第二份画面")
        elif p["same_source"] and visible:
            verdict = "FAIL"
            why = ("同一素材的两层以不同取景叠在一起（同源第二份画面 = 重影）"
                   if not p.get("top_is_cutout") else
                   "顶层自称抠像层，但没实测到透明区（抠像没真跑 / 整层不透明）= 重影风险")
        elif p["same_source"]:
            verdict = "PASS"
            why = "同源但对照版看不出差异（像素级重复 / 带 alpha，无可见重影）"
        else:
            verdict = "PASS"
            why = "不同素材的正常叠层（原版构图）"
        rows.append({**p, "frames": [x.name for x in (fa, fb) if x], "delta": delta,
                     "faces_full": n_full, "faces_main_only": n_main,
                     "same_face_twice": bool(n_full >= 2 and n_full > n_main),
                     "top_opaque_fullframe": top_opaque_full,
                     "top_alpha_coverage": (round(float(cov), 4) if cov is not None else None),
                     "why": (why + "；同一帧测到两张脸是设计使然（抠像人像层 + 底下同一条主轨），不是重影"
                             if (cutout_ok and n_full >= 2 and n_full > n_main) else why),
                     "verdict": verdict})

    # 被拒接的叠层：点检「成片 == 只渲主轨」，证明那一层真的没上、不是调低了透明度。
    # `spot_times`（3.0s / 模版重叠中点）是**通用**点检：重建后这些点位本来就有画面差，
    # 所以只有 rejected（in原报告里被留空的层）的点位要求「与只渲主轨几乎相同」。
    spots: List[dict] = []
    if main_mp4 is not None:
        timed: List[tuple] = []
        for row in (rejected or []):
            t0 = float(row.get("start") or 0.0)
            dur = float(row.get("duration") or 0.0)
            if dur > 0.06:
                timed.append((t0 + dur / 2.0, "rejected_overlay"))
        timed.extend((float(x), "spot_time") for x in (spot_times or []))
        seen: List[tuple] = []
        for t, kind in sorted(timed):
            if not (0.0 <= t):
                continue
            if any(abs(t - s) < 0.45 for s, _ in seen):
                continue
            seen.append((t, kind))
        for k, (t, kind) in enumerate(seen):
            fa = _grab_frame(full_mp4, t, frames_dir / f"spot{k:02d}_full_{t:.2f}s.jpg")
            fb = _grab_frame(main_mp4, t, frames_dir / f"spot{k:02d}_main_{t:.2f}s.jpg")
            delta = _frame_delta(fa, fb)
            spots.append({"t": round(t, 3), "kind": kind,
                          "delta_vs_main_only": delta,
                          "same_as_main_only": bool(delta and delta["mae"] <= tol),
                          "frames": [x.name for x in (fa, fb) if x]})

    fails = [r for r in rows if r["verdict"] == "FAIL"]
    rej_spots = [s for s in spots if s.get("kind") == "rejected_overlay"]
    rej_ok = all(s["same_as_main_only"] for s in rej_spots) if rej_spots else True
    return {
        "ok": bool(not fails and rej_ok),
        "tolerance_mae": tol,
        "pairs_checked": len(rows),
        "pairs_total_with_compound": len(pairs_all),
        "compound_pairs_skipped_distinct_assets": compound_pairs_skipped,
        "compound_same_source_pairs_checked": sum(
            1 for r in rows if r["same_source"] and (r.get("a_in_compound") or r.get("b_in_compound"))),
        "ghost_rejected": len(fails),
        "pairs_same_source": sum(1 for r in rows if r["same_source"]),
        "pairs_same_source_cutout": sum(1 for r in rows if r["same_source"] and r.get("top_is_cutout")),
        "pairs_same_source_blur_board": sum(1 for r in rows if r["same_source"] and r.get("top_blur_sigma")),
        "same_face_twice": sum(1 for r in rows if r["same_face_twice"]),
        "detail": rows,
        "rejected_layer_spots": rej_spots,
        "spot_checks": len(spots),
        "frames_dir": str(frames_dir),
        "spots_all_match_main_only": (all(s["same_as_main_only"] for s in rej_spots) if rej_spots else None),
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
    ap.add_argument("--no-reference-original", action="store_true",
                    help="不渲原版参考片（省 ~2 分钟；默认渲染，用于「原版同段」对比）")
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
        # 复合片段：提取端递归拆出的子结构（子层与顶层同一 schema）
        "compounds": tpl.get("compounds") or [],
        "compound_counts": {k: v for k, v in (tpl.get("counts") or {}).items()
                            if "compound" in k},
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
        # z 序迁移（这一轮的核心）：重排幅度 / 残留错位 / 每个复合片段子稿的排序结果
        "tracks_reordered": apply_rep.get("tracks_reordered"),
        "z_order_mismatch": apply_rep.get("z_order_mismatch"),
        "z_order_inverted_pairs": apply_rep.get("z_order_inverted_pairs"),
        "track_order": apply_rep.get("track_order"),
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
        "overlay_left_blank": apply_rep.get("overlay_left_blank", 0),
        "overlay_left_blank_detail": apply_rep.get("overlay_left_blank_detail") or [],
        # 抠像三明治重建台账：重建了几层 / 跳采几层（分原因）/ 背景板 / 描边
        "cutout_layers_rebuilt": apply_rep.get("cutout_layers_rebuilt", 0),
        "cutout_rebuilt_detail": apply_rep.get("cutout_rebuilt_detail") or [],
        "cutout_layers_skipped": apply_rep.get("cutout_layers_skipped") or {},
        "backdrop_rebuilt": apply_rep.get("backdrop_rebuilt", 0),
        "backdrop_rebuilt_detail": apply_rep.get("backdrop_rebuilt_detail") or [],
        "backdrop_skipped": apply_rep.get("backdrop_skipped") or {},
        "stroke_applied": apply_rep.get("stroke_applied", 0),
        "ghost_rejected": apply_rep.get("ghost_rejected", 0),
        "overlap_conflicts_avoided": apply_rep.get("overlap_conflicts_avoided", 0),
        "asset_reuse_by_slot": apply_rep.get("asset_reuse_by_slot") or [],
        "video_overlap_pairs": apply_rep.get("video_overlap_pairs") or [],
        "video_overlap_same_asset": apply_rep.get("video_overlap_same_asset", 0),
        "crop_uv_dropped": apply_rep.get("crop_uv_dropped", 0),
        "crop_uv_dropped_detail": apply_rep.get("crop_uv_dropped_detail") or [],
        # 复合片段重建台账：拆出多少 / 写回多少 / 没写的原因 / 子段数 / 照片层映射数
        "compounds_extracted": apply_rep.get("compounds_extracted", 0),
        "compounds_applied": apply_rep.get("compounds_applied", 0),
        "compounds_skipped": apply_rep.get("compounds_skipped") or {},
        "compound_applied_detail": apply_rep.get("compound_applied_detail") or [],
        "sub_segments_total": apply_rep.get("sub_segments_total", 0),
        "photo_layers_mapped": apply_rep.get("photo_layers_mapped", 0),
        # 照片全局唯一台账（跨复合片段/跨层级的同一本账）
        "duplicate_photo_blocked": apply_rep.get("duplicate_photo_blocked", 0),
        "duplicate_photo_blocked_detail": apply_rep.get("duplicate_photo_blocked_detail") or [],
        "duplicate_photo_allowed": apply_rep.get("duplicate_photo_allowed", 0),
        "photo_allocations": apply_rep.get("photo_allocations") or [],
        "photo_layers_unfilled": apply_rep.get("photo_layers_unfilled", 0),
        "photo_assets_distinct": apply_rep.get("photo_assets_distinct", 0),
        "photo_pool_size": apply_rep.get("photo_pool_size", 0),
        "compound_anim_clamped": apply_rep.get("compound_anim_clamped", 0),
        "compound_time_scaled": apply_rep.get("compound_time_scaled", 0),
        "compound_length_mismatch": apply_rep.get("compound_length_mismatch", 0),
        "photo_layer_aspect_cropped": apply_rep.get("photo_layer_aspect_cropped", 0),
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
    # 复合片段：不信套用端的自报，回草稿里逐条核「两个层次真写进去了没」
    report["steps"]["compounds"] = compound_structure_audit(
        tpl, apply_rep, draft, Path(draft_json).parent)
    # 模版里每一对时间重叠的视频段 → 新片里的处置（逐对列，不靠汇总数字猜）
    report["steps"]["overlay_pairs"] = {
        "template_pairs": apply_rep.get("overlay_pairs_resolved") or [],
        "template_pairs_same_source_kept": sum(1 for r in (apply_rep.get("overlay_pairs_resolved") or [])
                                               if r["resolution"] == "kept_same_source"),
        "final_overlap_pairs": apply_rep.get("video_overlap_pairs") or [],
        "final_same_source_pairs": apply_rep.get("video_overlap_same_asset", 0),
    }
    # 两个水印大字位：material.global_alpha=0 但段上有 KFTypeGlobalAlpha 曲线（0.70→0），
    # 原版是「亮 0.7 再飞出」而不是不显示——不写清楚容易被误解成「多画了两行字」
    report["notes"] = [
        "文字位的 material.global_alpha=0 不总等于「不显示」：段上带 KFTypeGlobalAlpha 关键帧时"
        "0 只是静态终态，真值是曲线（本模版两个水印大字位 = 0.70→0 + 缩到 0.01 飞走）",
        # 抠像三明治重建的两个口径决定（写下来，免得上游以为可以随便改）
        "抠像层重建走「草稿标 meta、渲染时执行」：套用端只把 matting 结构（同素材同入点 + 描边"
        "颜色/强度）写进新草稿素材，Vision 抠像 + 撕纸描边由渲染器在渲染时做。理由是描边像素"
        "换算常数只在渲染器里且逐帧反解；且脚本里烘好的 RGBA 层一旦当普通素材引用，抽段走 h264 会丢 alpha。",
        "背景板按「主轨同素材同入点 + 模糊」重建（按抠像层切块对齐），不另接 B-roll：参考片"
        "原帧里模糊底仍能看到人形轮廓（同一人的虚化版），另接一条 B-roll 会出现「清晰的人 +"
        "背后模糊的另一个人」（实测 Vision 逐帧数出两个人）。",
        "复合片段（内嵌子时间线，含 0.45~1.667s 那块照片拼贴底板）**已递归提取并重建**："
        "模版 `segments[].compound` 与顶层同一 schema，套用端按子结构重写 "
        "materials.drafts[*].draft + subdraft/<uuid>/ 并让父段用 extra_material_refs 引用；"
        "子时间线的照片层（asset_role=photo）**只映射到新图片**（按原版图层宽高比居中裁一刀，"
        "保留长条取景），不拿视频顶。没有贴纸/音效素材的复合片段跳过并逐条计 compounds_skipped。",
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
                # 渲染器自己的抠像台账（不是套用端“我以为写了就成”）：真跑了几个 matte、
                # 失败几个、烘了几层撕纸描边。抠像失败时渲染器会不下假前景（overlay_matte_failed）。
                "matte_done": rr.get("matte_done"),
                "matte_failed": rr.get("matte_failed"),
                "matte_stroke_segments": rr.get("matte_stroke_segments"),
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

    # ---- 4b. 原版参考渲染（只读调用同一个渲染器；原版素材不在本机时跳过）----
    # 为什么要渲:验收要求「0.45~1.667s 的帧与原版同段对比」，而原版成片
    # 不在仓库里。这里用「原版草稿 + 原版素材 + 同一渲染器」当参考，两边差异就只来自
    # 「换素材/换文案」，不含渲染口径差异。
    reference_mp4: Optional[Path] = None
    if not args.no_render and not args.no_reference_original:
        t0 = time.time()
        ref_meta: Dict[str, Any] = {}
        try:
            src_dir_name = resolve_draft_by_style(args.style_id).parent.name
            reference_mp4 = reference_original_render(out, args.style_id, tpl, src_dir_name,
                                                     meta=ref_meta)
        except Exception as exc:
            reference_mp4 = None
            report.setdefault("original_reference_error", f"{type(exc).__name__}: {exc}")
        report["steps"]["original_reference"] = {
            "mp4": _rel(reference_mp4) if reference_mp4 else None,
            "ok": bool(reference_mp4), "elapsed_sec": round(time.time() - t0, 1),
            # 参考片是按原版 render_index 排过序才渲的（否则渲染器的「轨序即 z 序」会
            # 把原版的拼贴也盖住，基准就错了）；这里记录动了多少轨
            "z_order_fix": ref_meta.get("z_order_fix"),
        }
    else:
        report["steps"]["original_reference"] = {"skipped": True}

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

        # ---- 5a. 抠像层实测：每层重跑 Vision 抠像量 alpha 覆盖率 + 3 张 2× 边缘放大图 ----
        # 不只信 apply 的“已重建”台账：这里拿 alpha 覆盖率和人眼可看的放大图当证据。
        t0 = time.time()
        try:
            fps_i = int(round(float(tpl.get("fps") or 30)))
            cutout_probe = cutout_alpha_probe(draft, Path(draft_json).parent, final_mp4,
                                              frames_dir, fps=fps_i, max_zooms=3)
            cutout_probe["elapsed_sec"] = round(time.time() - t0, 1)
        except Exception as exc:
            cutout_probe = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                            "layers": [], "edge_zooms": [], "coverage_by_seg": {}}
        report["steps"]["cutout_alpha"] = cutout_probe

        # ---- 5b. 逐对验重影（真跑：多渲一版「只渲主轨」，再逐对抽帧比差）----
        t0 = time.time()
        main_only = None
        try:
            fps_i = int(round(float(tpl.get("fps") or 30)))
            main_only = render_main_only(draft, Path(draft_json).parent, out / "render",
                                         fps_i, args.style_id + "_mainonly")
            ghost = check_overlay_ghosting(
                draft, Path(draft_json).parent, final_mp4, main_only, frames_dir,
                rejected=(apply_rep.get("overlay_left_blank_detail") or []),
                spot_times=[3.0] + [float(r["overlap_mid"]) for r in
                                    (apply_rep.get("overlay_pairs_resolved") or [])],
                cutout_evidence=cutout_probe)
            ghost["main_only_mp4"] = _rel(main_only) if main_only else None
            ghost["elapsed_sec"] = round(time.time() - t0, 1)
        except Exception as exc:
            ghost = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                     "elapsed_sec": round(time.time() - t0, 1)}
        report["steps"]["ghost_check"] = ghost

        # ---- 5c. 复合片段那段（照片拼贴底板）：抽帧 + 逐帧差量 + 原版同段对比 ----
        t0 = time.time()
        comp_window = None
        for s in tpl.get("segments") or []:
            c = s.get("compound") or {}
            if (c.get("asset_profile") or {}).get("photo_layers", 0) >= 2:
                if comp_window is None or float(s["start_sec"]) < comp_window[0]:
                    comp_window = (float(s["start_sec"]),
                                   float(s["start_sec"]) + float(s["duration_sec"]))
        if comp_window is not None:
            try:
                fps_i = int(round(float(tpl.get("fps") or 30)))
                region = compound_region_check(
                    final_mp4, main_only, frames_dir,
                    (comp_window[0] + 0.08, comp_window[1] - 0.04),
                    reference_mp4=(reference_mp4 if reference_mp4 else None), fps=fps_i)
                region["elapsed_sec"] = round(time.time() - t0, 1)
            except Exception as exc:
                region = {"error": f"{type(exc).__name__}: {exc}"}
            # 层级别对比：原版子时间线 vs 新子时间线（各自单独渲在灰底上）
            try:
                region["layer_reference"] = compound_layer_reference(
                    out, args.style_id, tpl, Path(draft_json).parent, draft,
                    (comp_window[0] + 0.08, comp_window[1] - 0.04), frames_dir)
            except Exception as exc:
                region["layer_reference"] = {"error": f"{type(exc).__name__}: {exc}"}
            # 条带可见性：6 帧 ×（成片 + 只渲主轨），并用连通域数回答「原版能看到几条 / 新成片能看到几条」
            try:
                region["strip_visibility"] = strip_visibility(
                    final_mp4, main_only, draft, Path(draft_json).parent, args.style_id, tpl,
                    frames_dir,
                    window=(comp_window[0] + 0.08, comp_window[0] + 1.18), n=6,
                    fps=int(round(float(tpl.get("fps") or 30))),
                    reference_mp4=reference_mp4 if reference_mp4 else None)
            except Exception as exc:
                region["strip_visibility"] = {"error": f"{type(exc).__name__}: {exc}"}
            report["steps"]["compound_region"] = region
        else:
            report["steps"]["compound_region"] = {"skipped": "no_photo_compound_in_template"}

        # ---- 5d. z 序回归防线（这一轮的核心）：同时间重叠的满幅/半透明层，原版 vs 新版谁在上 ----
        t0 = time.time()
        try:
            z_audit = z_layer_order_audit(
                final_mp4, draft, Path(draft_json).parent, args.style_id, tpl, frames_dir,
                reference_mp4=reference_mp4)
            z_audit["elapsed_sec"] = round(time.time() - t0, 1)
        except Exception as exc:
            z_audit = {"ok": False, "z_order_inverted": None,
                       "error": f"{type(exc).__name__}: {exc}",
                       "elapsed_sec": round(time.time() - t0, 1)}
        report["steps"]["z_order"] = z_audit
    else:
        ghost = {"ok": False, "reason": "no_render"}
        report["steps"]["frames"] = {"skipped": True}
        report["steps"]["face_framing"] = {"skipped": True}
        report["steps"]["cutout_alpha"] = {"skipped": True}
        report["steps"]["ghost_check"] = {"skipped": True}
        report["steps"]["z_order"] = {"skipped": True}

    # ---- 6. 结论 ----
    ff = report["steps"].get("face_framing") or {}
    report["verdict"] = {
        "template_ok": bool(tpl["segments"]),
        "draft_ok": Path(draft_json).is_file(),
        "render_ok": bool(render_rep.get("ok")) if not args.no_render else None,
        "asset_reused": apply_rep.get("asset_reused"),
        "content_linked": apply_rep.get("content_linked", 0),
        "overlay_pairs_checked": (report["steps"].get("ghost_check") or {}).get("pairs_checked"),
        "ghost_rejected": (report["steps"].get("ghost_check") or {}).get("ghost_rejected"),
        "overlay_left_blank": apply_rep.get("overlay_left_blank", 0),
        "asset_reuse_by_slot": apply_rep.get("asset_reuse_by_slot") or [],
        "assets_distinct_in_use": len({r["asset_ref"] for r in (apply_rep.get("asset_reuse_by_slot") or [])}),
        "overlap_conflicts_avoided": apply_rep.get("overlap_conflicts_avoided", 0),
        "template_overlap_pairs": len(apply_rep.get("overlay_pairs_resolved") or []),
        "template_pairs_same_source_kept": sum(
            1 for r in (apply_rep.get("overlay_pairs_resolved") or [])
            if r["resolution"] == "kept_same_source"),
        # z 序迁移（这一轮的核心）：重排幅度 + 残留错位 + 逐对层序防线
        "tracks_reordered": apply_rep.get("tracks_reordered"),
        "z_order_mismatch": apply_rep.get("z_order_mismatch"),
        "z_order_inverted_pairs_apply": apply_rep.get("z_order_inverted_pairs"),
        "z_order_inverted": (report["steps"].get("z_order") or {}).get("z_order_inverted"),
        "z_order_top_layer_not_visible": (report["steps"].get("z_order") or {}).get(
            "top_layer_not_visible"),
        "z_order_ok": (report["steps"].get("z_order") or {}).get("ok"),
        "unknown_assets": tpl["counts"].get("unknown", 0),
        "fonts_resolved": {k: v.get("via") for k, v in (apply_rep.get("font_resolved") or {}).items()},
        "font_fallback_slots": [f["slot"] for f in (apply_rep.get("font_fallback_slots") or [])],
        "texts_truncated": apply_rep.get("texts_truncated"),
        "texts_used": apply_rep.get("texts_used"),
        "face_framing": (ff or {}).get("summary"),
        # 抠像三明治重建（这一轮的核心验收）
        "cutout_layers_rebuilt": apply_rep.get("cutout_layers_rebuilt", 0),
        "cutout_layers_skipped": apply_rep.get("cutout_layers_skipped") or {},
        "backdrop_rebuilt": apply_rep.get("backdrop_rebuilt", 0),
        "stroke_applied": apply_rep.get("stroke_applied", 0),
        "cutout_matte_done_render": (render_rep or {}).get("matte_done"),
        "cutout_matte_failed_render": (render_rep or {}).get("matte_failed"),
        "cutout_stroke_layers_render": (render_rep or {}).get("matte_stroke_segments"),
        "cutout_alpha_probed": (report["steps"].get("cutout_alpha") or {}).get("layers_with_person_matte"),
        "cutout_alpha_transparent": (report["steps"].get("cutout_alpha") or {}).get("layers_with_transparent_alpha"),
        "cutout_edge_zooms": [z.get("frame") for z in ((report["steps"].get("cutout_alpha") or {}).get("edge_zooms") or [])],
        "ghost_ok": (report["steps"].get("ghost_check") or {}).get("ok"),
        # 复合片段（这一轮的核心验收）
        "compounds_extracted": apply_rep.get("compounds_extracted", 0),
        "compounds_applied": apply_rep.get("compounds_applied", 0),
        "compounds_skipped": apply_rep.get("compounds_skipped") or {},
        "sub_segments_total": apply_rep.get("sub_segments_total", 0),
        "photo_layers_mapped": apply_rep.get("photo_layers_mapped", 0),
        # 照片全片唯一（这一轮的核心）
        "duplicate_photo_blocked": apply_rep.get("duplicate_photo_blocked", 0),
        "duplicate_photo_allowed": apply_rep.get("duplicate_photo_allowed", 0),
        "photo_layers_unfilled": apply_rep.get("photo_layers_unfilled", 0),
        "photo_assets_distinct": apply_rep.get("photo_assets_distinct", 0),
        "photo_pool_size": apply_rep.get("photo_pool_size", 0),
        "compound_structure_ok": (report["steps"].get("compounds") or {}).get("ok"),
        "compound_region": {
            "window": (report["steps"].get("compound_region") or {}).get("window"),
            "delta_vs_main_only": (report["steps"].get("compound_region") or {}).get(
                "delta_vs_main_only"),
            "visible_blocks": (report["steps"].get("compound_region") or {}).get("visible_blocks"),
            "vs_reference": (report["steps"].get("compound_region") or {}).get("vs_reference"),
            "layer_reference": (report["steps"].get("compound_region") or {}).get(
                "layer_reference"),
            # 条带可见性：连通域数（原版该时刻 vs 新成片）+ 归因（素材尺寸 vs 层叠顺序）
            "strip_visibility": ((report["steps"].get("compound_region") or {}).get(
                "strip_visibility") or {}).get("summary"),
            "strip_cause": ((report["steps"].get("compound_region") or {}).get(
                "strip_visibility") or {}).get("cause"),
        },
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    # ---- 控制台摘要（不打印草稿名/家目录） ----
    a = report["steps"]["apply"]
    print(f"[verify] 模版: {report['steps']['extract']['summary']}")
    print(f"[verify] 新草稿: {a['segments_total']} 段 · {a['duration_sec']}s "
          f"(模版 {a['template_duration_sec']}s, Δ{a['duration_delta_sec']}s) · 角色 {a['segments_by_role']}")
    print(f"[verify] 照片全局去重: 挡掉重复分配 {a['duplicate_photo_blocked']} 次 · "
          f"原版同比图放行 {a['duplicate_photo_allowed']} 次 · 池不够留空 {a['photo_layers_unfilled']} 层 · "
          f"用到不同图 {a['photo_assets_distinct']} 张 / 池 {a['photo_pool_size']} 张")
    print(f"[verify] 复用/回卷/缩段: {a['asset_reused']} / {a['asset_wrapped']} / {a['slot_shrunk']} · "
          f"跟随主轨同素材 {a['content_linked']} 段 · skipped {a['skipped']}")
    to = a.get("track_order") or {}
    if to.get("top"):
        tp = to["top"]
        print(f"[verify] z 序迁移: 重排 {tp['tracks_reordered']}/{tp['tracks_total']} 条轨 · "
              f"残留错位 {tp['z_order_mismatch']} 条 / 反转层对 {tp['z_order_inverted_pairs']} · "
              f"子稿重排共 {to.get('tracks_reordered_total', 0) - tp['tracks_reordered']} 条 "
              f"（{len(to.get('compounds') or [])} 个复合片段）")
        print(f"[verify]   轨序(原数组序→按 render_index): {tp['order_before']} → {tp['order_after']}")
        for c in (to.get("compounds") or []):
            print(f"[verify]   子稿 {c['scope']}: 重排 {c['tracks_reordered']}/{c['tracks_total']} · "
                  f"{c['order_before']} → {c['order_after']} · 错位 {c['z_order_mismatch']}")
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
    cp = report["steps"].get("compounds") or {}
    if cp:
        print(f"[verify] 复合片段: 提取 {cp.get('compounds_extracted')} / 写回 {cp.get('compounds_applied')} "
              f"/ 跳过 {cp.get('compounds_skipped')} · 子段 {cp.get('sub_segments_total')} "
              f"（草稿内 {cp.get('sub_segments_found_in_draft')}）· 照片层映射 {cp.get('photo_layers_mapped')} "
              f"（错类型 {cp.get('photo_layers_wrong_type')}）· subdraft 落盘 {cp.get('subdraft_dirs_on_disk_ok')}")
    cr = report["steps"].get("compound_region") or {}
    if cr.get("window"):
        dm = cr.get("delta_vs_main_only") or {}
        vr = cr.get("vs_reference") or {}
        print(f"[verify] 拼贴底板段 {cr['window']}: 帧 {dm.get('frames')} 张 · 与只渲主轨 "
              f"MAE 均 {dm.get('mean_mae')} / 最小 {dm.get('min_mae')} · >0.02 的帧 {dm.get('frames_above_0.02')} "
              f"· 中帧亮度 {cr.get('luminance_mid')} · 可见块 {cr.get('visible_blocks')}")
        if vr:
            print(f"[verify]   与原版同段: 帧 {vr.get('frames')} · MAE 均 {vr.get('mean_mae')} "
                  f"/ 范围 {vr.get('min_mae')}~{vr.get('max_mae')}")
        lr = cr.get("layer_reference") or {}
        for row in (lr.get("rows") or []):
            d = row.get("delta_vs_original_layer") or {}
            print(f"[verify]   子层对比 @{row.get('t')}s: 原版块 {row.get('original_blocks')} / "
                  f"新版块 {row.get('new_blocks')} · MAE {d.get('mae')}")
        sv = cr.get("strip_visibility") or {}
        ss = sv.get("summary") or {}
        if ss:
            print(f"[verify] 条带可见性 {sv.get('window')}: 层单独渲的块数 原版 {ss.get('orig_layer_blocks_per_frame')} "
                  f"/ 新版 {ss.get('new_layer_blocks_per_frame')} · 在成片里看得见的块 "
                  f"原版 {ss.get('orig_visible_per_frame')} / 新版 {ss.get('new_visible_per_frame')} "
                  f"→ 归因 {sv.get('cause')}")
            print(f"[verify]   对照帧落盘: {_rel(out / 'frames' / 'strips')}")
    if fr.get("face_check", {}).get("ok"):
        print(f"[verify] 关键帧 {fr['count']} 张 · 检出人脸 {fr['face_check']['frames_with_face']} 张")
    if ff.get("summary"):
        print(f"[verify] 推近/缩窗人脸审计: {ff['summary']}")
    ca = report["steps"].get("cutout_alpha") or {}
    if ca.get("layers_probed"):
        covs = [r.get("alpha_coverage") for r in (ca.get("layers") or []) if r.get("alpha_coverage") is not None]
        print(f"[verify] 抠像层实测: 探 {ca['layers_probed']} 层 · 有 matte {ca.get('layers_with_person_matte')} · "
              f"有透明区 {ca.get('layers_with_transparent_alpha')} · alpha 覆盖率 "
              f"{['%.2f' % c for c in covs]}")
        for z in (ca.get("edge_zooms") or []):
            print(f"[verify]   边缘放大图 {z.get('frame')} @{z.get('t')}s stroke={z.get('stroke')} "
                  f"alpha={z.get('alpha_coverage')}")
    gc = report["steps"].get("ghost_check") or {}
    if gc.get("ok"):
        print(f"[verify] 叠层重影: 重叠段对 {gc['pairs_checked']} · 判 FAIL {gc['ghost_rejected']} · "
              f"同源对 {gc['pairs_same_source']} · 同一张脸两次 {gc['same_face_twice']}")
        for row in gc.get("detail") or []:
            d = row.get("delta") or {}
            print(f"[verify]   {row['a']} × {row['b']} @{row['overlap_mid']}s "
                  f"同源={row['same_source']} 满幅不透明={row['top_opaque_fullframe']} "
                  f"MAE={d.get('mae')} p99={d.get('p99')} 人脸 {row['faces_full']}/{row['faces_main_only']} "
                  f"-> {row['verdict']}")
        spots = gc.get("rejected_layer_spots") or []
        if spots:
            worst = max((s.get("delta_vs_main_only") or {}).get("mae", 0.0) for s in spots)
            print(f"[verify] 被拒叠层点检 {len(spots)} 处：与只渲主轨最大差 {worst} "
                  f"（全部一致={gc.get('spots_all_match_main_only')}）")
    elif gc:
        print(f"[verify] 叠层重影检查未完成: {gc.get('reason') or gc.get('error') or 'skipped'}")
    print(f"[verify] 叠层留空 {report['verdict']['overlay_left_blank']} 段（原因见 report.json "
          f"overlay_left_blank_detail）")
    zo = report["steps"].get("z_order") or {}
    if zo.get("skipped"):
        print("[verify] z 序防线: 未跑（无渲染产物）")
    elif zo.get("error"):
        print(f"[verify] z 序防线出错: {zo['error']}")
    elif zo.get("pairs") is not None:
        print(f"[verify] z 序防线: 候选层对 {zo.get('candidate_pairs')} · 判了 {zo.get('pairs_judged')} "
              f"· 序反转 {zo.get('z_order_inverted')} · 说在上却看不见 {zo.get('top_layer_not_visible')} "
              f"→ {'OK' if zo.get('ok') else 'FAIL'}")
        for row in (zo.get("pairs") or []):
            if row.get("skipped"):
                print(f"[verify]   {row['overlap']}s: 跳过（{row['skipped']}）")
                continue
            ev = row.get("evidence") or {}
            nv = (ev.get("new") or {}).get("visible_frac")
            ov = (ev.get("orig") or {}).get("visible_frac")
            print(f"[verify]   {row['overlap']}s: 原版在上={row['orig']['top']} "
                  f"(ri {row['orig']['a']['render_index']}/{row['orig']['b']['render_index']}) · "
                  f"新版在上={row['new']['top']} (轨 {row['new']['a']['track']}/{row['new']['b']['track']})"
                  f" → {'**反转**' if row['inverted'] else '一致'}"
                  f" · 上层可见占比 原版 {ov} / 新版 {nv}")
        if zo.get("pairs_judged") is not None:
            print(f"[verify]   层单独渲的对照帧: {_rel(out / 'frames' / 'zorder')}")
    print(f"[verify] 抠像三明治: 重建 {report['verdict']['cutout_layers_rebuilt']} 层 · "
          f"跳过 {report['verdict']['cutout_layers_skipped']} · "
          f"背景板重建 {report['verdict']['backdrop_rebuilt']} · 描边 {report['verdict']['stroke_applied']} 层 · "
          f"渲染 matte {report['verdict']['cutout_matte_done_render']} / 失败 {report['verdict']['cutout_matte_failed_render']}")
    return 0 if (report["verdict"]["draft_ok"] and report["verdict"]["render_ok"] is not False
                 and not (gc.get("ok") and gc.get("ghost_rejected"))
                 and not ((report["steps"].get("z_order") or {}).get("z_order_inverted")
                          or (report["steps"].get("z_order") or {}).get("top_layer_not_visible"))
                 ) else 1


def _seg_roles(segments: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in segments:
        out[s["role"]] = out.get(s["role"], 0) + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
