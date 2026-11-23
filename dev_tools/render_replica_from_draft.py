#!/usr/bin/env python3
"""从解密剪映草稿直接驱动渲染器，复刻原版全片（盲审 P0-1a）。

与普通流水线的区别：不走 run_step4/5/6 的句子驱动重排，一切以草稿真值为准——
  - 主轨：段级 source/target 时程、变速、主轨 scale/transform 推近 → geometry_pip；
  - 复合片段（materials.drafts 内嵌子时间线）：递归复刻成 mp4，
    画面进叠加合成、声音按段落在时间线上进音频母带；
  - 叠加视频轨：先抽段 →（有 matting 就走 macOS Vision 人像抠像）→ 按草稿
    scale/transform/alpha/flip 合成回时间线（轨序即 z 序）；
  - 文本：按草稿原字体文件、原填充色、原字号（PX_PER_UNIT=4.0）、原阴影渲染 PNG，
    按草稿 transform 落位；不走模板近似；
  - 音频：草稿音频轨逐段裁切 → 定位 → amix 合成 08_mixed_master.wav；
  - 调色与画面特效：仍走 step7 校准链（其特效窗口本就来自源草稿 effect 轨真值）。

不假装：贴纸尺寸近似、关键帧动画、主轨 alpha、素材级 crop 复刻不了就计数上报
（report.unrealized），绝不静默跳过，也绝不拿 invented 内容顶替。

用法：
  python3 dev_tools/render_replica_from_draft.py --style-id style_21 --out /tmp/blind_review
  python3 dev_tools/render_replica_from_draft.py --all --out /tmp/blind_review [--preview]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PH_RE = re.compile(r"##_draftpath_placeholder_[0-9A-Fa-f\-]+_##")
US = 1_000_000
_CODE_SIG = ""


def _cache_tag(*parts) -> str:
    """段文件缓存指纹：参数或脚本一改，旧段自动失效（不静默复用旧烘焙）。"""
    import hashlib as _h
    global _CODE_SIG
    if not _CODE_SIG:
        _CODE_SIG = _h.sha1(Path(__file__).read_bytes()).hexdigest()[:8]
    blob = "|".join(str(p) for p in parts) + "|" + _CODE_SIG
    return _h.sha1(blob.encode()).hexdigest()[:8]
PX_PER_UNIT = 4.0  # 兜底值；实际按画布推导 canvas_w/192（实测 1080 宽草稿 = 5.625，与标题/字幕墨高吻合）
CANVAS_W, CANVAS_H = 1080, 1920
MAX_COMPOUND_DEPTH = 3


def _subst(path: str, base_dir: Path) -> str:
    return PH_RE.sub(str(base_dir), str(path or ""))


def load_draft(draft_dir: Path, decrypted_root: Path) -> dict:
    # 优先解密实时草稿（快照可能落后于用户最近编辑，字体/素材路径会变）
    import importlib.util

    spec = importlib.util.spec_from_file_location("jy_draft_decrypt", ROOT / "forensic" / "jy_draft_decrypt.py")
    jd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jd)
    live = draft_dir / "draft_content.json"
    if live.is_file():
        try:
            raw = live.read_text(encoding="utf-8", errors="ignore")
            return json.loads(jd.decrypt_text(raw))
        except Exception:
            pass
    dec = decrypted_root / draft_dir.name / "draft_content.json"
    if dec.is_file():
        return json.loads(dec.read_text(encoding="utf-8"))
    raise SystemExit(f"草稿既不能实时解密也没有快照: {draft_dir}")


def run_ffmpeg(cmd: list, timeout: int = 600) -> None:
    cmd = list(cmd)
    if "-nostdin" not in cmd:
        cmd.insert(1, "-nostdin")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg 失败: " + (proc.stderr or "")[-1200:])


def ffprobe_has_audio(path: str) -> bool:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60)
        return bool((proc.stdout or "").strip())
    except Exception:
        return False


def make_black_segment(out_path: Path, dur: float, fps: int, w: int, h: int) -> Path:
    """主轨素材缺失时的诚实占位：黑场同长段，盲审里一眼可见，不冒充内容。"""
    if not out_path.is_file():
        run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}:d={max(0.1, dur):.4f}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", str(out_path)], timeout=120)
    return out_path


def cover_crop_vf(w: int, h: int) -> str:
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,"
        f"crop={w}:{h}"
    )


JY_CACHE_ROOT = "/Users/jerry/Movies/JianyingPro/User Data/Cache"


def _font_candidates(mat: dict) -> list:
    """收集字体候选：绝对路径优先；相对路径按剪映 Cache 根解析；只留真实存在的文件。"""
    cands: list = []
    for f in mat.get("fonts") or []:
        if f.get("path"):
            cands.append(f["path"])
    content = mat.get("content")
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            fp = (json.loads(content).get("font") or {}).get("path")
            if fp:
                cands.append(fp)
        except Exception:
            pass
    out = []
    for p in cands:
        p = str(p)
        if not Path(p).is_file() and p.startswith("/"):
            alt = Path(JY_CACHE_ROOT) / p.lstrip("/")
            if alt.is_file():
                p = str(alt)
        if Path(p).is_file() and p not in out:
            out.append(p)
    return out


def parse_text_material(mat: dict) -> dict:
    """草稿文本素材 → {text, color, font_path, font_size, shadow, stroke, line_spacing}。"""
    out = {"text": "", "color": (255, 255, 255), "font_path": None,
           "font_candidates": _font_candidates(mat),
           "font_size": float(mat.get("font_size") or 24.0)}
    if out["font_candidates"]:
        out["font_path"] = out["font_candidates"][0]
    content = mat.get("content")
    txt = ""
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            cj = json.loads(content)
            txt = str(cj.get("text") or "")
            styles = cj.get("styles") or []
            if styles:
                st0 = styles[0]
                fill = ((st0.get("fill") or {}).get("content") or {}).get("solid") or {}
                rgb = fill.get("color") or []
                if len(rgb) >= 3:
                    out["color"] = tuple(max(0, min(255, round(c * 255))) for c in rgb[:3])
                strokes = st0.get("strokes") or []
                if strokes and float(strokes[0].get("width") or 0) > 0:
                    w_px = float(strokes[0].get("width") or 0) * out["font_size"] * PX_PER_UNIT
                    sc = ((strokes[0].get("content") or {}).get("solid") or {}).get("color") or []
                    if w_px > 0.5:
                        out["stroke"] = {
                            "px": round(w_px, 1),
                            "color": tuple(max(0, min(255, round(c * 255))) for c in sc[:3]) if len(sc) >= 3 else (0, 0, 0),
                        }
            for f in mat.get("fonts") or []:
                if f.get("path"):
                    out["font_path"] = f.get("path")
                    break
        except Exception:
            txt = content
    else:
        txt = str(content or "")
        for f in mat.get("fonts") or []:
            if f.get("path"):
                out["font_path"] = f.get("path")
                break
    out["text"] = txt
    if mat.get("has_shadow"):
        sp = mat.get("shadow_point") or {}
        out["shadow"] = {
            "dx": float(sp.get("x") or 0.0) * float(mat.get("shadow_distance") or 0.0) * PX_PER_UNIT,
            "dy": float(sp.get("y") or 0.0) * float(mat.get("shadow_distance") or 0.0) * PX_PER_UNIT,
            "blur": max(0.0, float(mat.get("shadow_smoothing") or 0.0) * PX_PER_UNIT),
            "alpha": float(mat.get("shadow_alpha") if mat.get("shadow_alpha") is not None else 0.9),
            "color": mat.get("shadow_color") or (0, 0, 0),
        }
        sc = out["shadow"]["color"]
        if isinstance(sc, str):
            sc = sc.strip()
            if sc.startswith("#") and len(sc) >= 7:
                out["shadow"]["color"] = tuple(int(sc[i : i + 2], 16) for i in (1, 3, 5))
            else:
                out["shadow"]["color"] = (0, 0, 0)
        elif isinstance(sc, (list, tuple)) and len(sc) >= 3:
            vals = [float(v) for v in sc[:3]]
            out["shadow"]["color"] = tuple(
                max(0, min(255, round(v * 255))) if v <= 1.0 else int(v) for v in vals
            )
    out["line_spacing"] = float(mat.get("line_spacing") or 0.0)
    return out


def render_text_png(spec: dict, scale: float, out_png: Path, fail_log: list | None = None,
                    pxu: float = PX_PER_UNIT) -> tuple:
    """按草稿字体/颜色/字号/阴影渲染透明 PNG，返回 (path, w, h)。字体加载失败逐候选重试并留痕。"""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    text = spec["text"]
    if not text.strip():
        raise ValueError("空文本段")
    font_px = max(12, int(round(spec["font_size"] * pxu * max(0.05, scale))))
    lines = text.split("\n")
    font = None
    for p in (spec.get("font_candidates") or ([spec.get("font_path")] if spec.get("font_path") else [])):
        try:
            font = ImageFont.truetype(p, font_px)
            break
        except Exception as e:
            if fail_log is not None:
                fail_log.append({"text": text[:12], "px": font_px, "path": str(p)[-60:], "err": f"{type(e).__name__}: {e}"[:120]})
    if font is None:
        font = ImageFont.load_default()
    ls = float(spec.get("line_spacing") or 0.0)
    line_h = int(font_px * 1.25 + font_px * ls)
    pad = int(font_px * 0.3) + 24
    tmp_img = Image.new("RGBA", (8, 8))
    tmp_draw = ImageDraw.Draw(tmp_img)
    widths = []
    for ln in lines:
        bbox = tmp_draw.textbbox((0, 0), ln, font=font)
        widths.append(int(bbox[2] - bbox[0]))
    w = max(8, max(widths) + pad * 2)
    h = max(8, line_h * len(lines) + pad * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def _paint(layer_draw, dx: float, dy: float, color, alpha=255):
        y_off = pad
        for ln in lines:
            layer_draw.text((pad + dx, y_off + dy), ln, font=font, fill=(*color, int(alpha)))
            y_off += line_h

    sh = spec.get("shadow")
    if sh and (abs(sh["dx"]) > 0.5 or abs(sh["dy"]) > 0.5):
        sh_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        # 剪映 shadow_point y 负=向下（屏幕坐标），PNG 坐标 y 正=向下 → dy 取反
        _paint(ImageDraw.Draw(sh_layer), sh["dx"], -sh["dy"], tuple(sh["color"]), alpha=int(255 * sh["alpha"]))
        if sh["blur"] > 0.3:
            sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(radius=sh["blur"]))
        img = Image.alpha_composite(img, sh_layer)
        draw = ImageDraw.Draw(img)
    stroke = spec.get("stroke")
    y_off = pad
    for ln in lines:
        if stroke:
            draw.text((pad, y_off), ln, font=font, fill=(*spec["color"], 255),
                      stroke_width=max(1, int(round(stroke["px"]))), stroke_fill=(*stroke["color"], 255))
        else:
            draw.text((pad, y_off), ln, font=font, fill=(*spec["color"], 255))
        y_off += line_h
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    # 自证：墨迹宽度与预期差太多 = 字体回退了，留痕不假装
    try:
        ink = img.split()[3].getbbox()
        ink_w = (ink[2] - ink[0]) if ink else 0
        exp_w = sum(1 for ch in "".join(lines) if not ch.isspace()) * font_px * 0.5
        if ink_w < exp_w * 0.5 and fail_log is not None:
            fail_log.append({"text": text[:12], "px": font_px, "ink_w": ink_w, "exp_w": round(exp_w),
                             "err": "ink_too_small: 字体回退或测量异常"})
    except Exception:
        pass
    return out_png, w, h


def _kf_pts(seg: dict, ptype: str) -> list:
    """取段上某类 keyframe 的 (相对秒, 值) 序列；无则空表。"""
    out = []
    for kfo in seg.get("common_keyframes") or []:
        if kfo.get("property_type") != ptype:
            continue
        for k in kfo.get("keyframe_list") or []:
            v = k.get("values") or []
            if v:
                out.append((float(k.get("time_offset") or 0) / US, float(v[0])))
    out.sort()
    return out


def _kf_final(seg: dict, ptype: str):
    pts = _kf_pts(seg, ptype)
    return pts[-1][1] if pts else None


def _kf_rel(seg: dict, ptype: str, src_in: float) -> list:
    """keyframe 时间是源时间（= 段源入点起的偏移），统一归一到段内相对秒。"""
    return [(max(0.0, t - float(src_in or 0.0)), v) for t, v in _kf_pts(seg, ptype)]


def _kf_steady(pts: list, dur: float):
    """取“保持时间最长”的那个 keyframe 值作为静态态。
    剪映的 clip.* 静态值可能是动画起点也可能终点（推近 vs 飞出），靠这个分辨：
    段首→首keyframe、末keyframe→段尾 两段谁长就取谁。"""
    if not pts:
        return None
    hold_first = pts[0][0]
    hold_last = max(0.0, float(dur) - pts[-1][0])
    return pts[0][1] if hold_first >= hold_last else pts[-1][1]


def _kf_piecewise(pts: list, t_start: float, pix: float, offset: float) -> str:
    """两点 keyframe → overlay x/y 分段线性表达式（首 keyframe 前保持首值，末点后保持末值）。"""
    (t0, v0), (t1, v1) = pts[0], pts[1]
    a0 = t_start + t0
    a1 = t_start + t1
    p0 = v0 * pix + offset
    p1 = v1 * pix + offset
    span = max(1e-3, a1 - a0)
    return (f"if(lt(t,{a0:.4f}),{p0:.2f},"
            f"if(lt(t,{a1:.4f}),{p0:.2f}+({p1 - p0:.2f})*(t-{a0:.4f})/{span:.4f},{p1:.2f}))")


def classify_tracks(draft: dict) -> dict:
    tracks = draft.get("tracks") or []
    vid_main, vid_over, txt_tracks, stk_tracks, fx_tracks, aud_tracks = [], [], [], [], [], []
    for idx, t in enumerate(tracks):
        tt = t.get("type")
        segs = t.get("segments") or []
        if tt == "video":
            (vid_over if t.get("flag") == 2 else vid_main).append((idx, segs))
        elif tt == "text":
            txt_tracks.append((idx, segs))
        elif tt == "sticker":
            stk_tracks.append((idx, segs))
        elif tt == "effect":
            fx_tracks.append((idx, segs))
        elif tt == "audio":
            aud_tracks.append((idx, segs))

    def _cover_len(item):
        return sum(int((s.get("target_timerange") or {}).get("duration") or 0) for s in item[1])

    vid_main.sort(key=_cover_len, reverse=True)
    return {
        "main": vid_main[0] if vid_main else (None, []),
        "overlays": vid_over,
        "texts": txt_tracks,
        "stickers": stk_tracks,
        "effects": fx_tracks,
        "audios": aud_tracks,
        "n_tracks": len(tracks),
    }


def _compound_is_visual(sub_draft: dict) -> bool:
    """复合片段有没有可见内容（视频轨上真有段）。纯音效叠层返回 False。"""
    for t in sub_draft.get("tracks") or []:
        if t.get("type") in ("video", "text", "sticker") and (t.get("segments") or []):
            return True
    return False


def _compound_has_personable_video(sub_draft: dict) -> bool:
    """复合片段里有没有真人视频素材（照片/文本拼的复合片段不抠像，直接上）。"""
    vids = (sub_draft.get("materials") or {}).get("videos") or []
    return any(v.get("type") == "video" and (v.get("path") or "") for v in vids)


def render_timeline(draft: dict, base_dir: Path, style_id: str, work: Path, fps_default: int,
                    preview: bool, report: dict, depth: int = 0, audio_only: bool = False,
                    alpha_out: bool = False) -> Path:
    """复刻一条时间线（含递归复合片段），返回最终 mp4。"""
    sys.path.insert(0, str(SCRIPTS))
    from common import read_json, write_json, find_bin, get_wav_duration_sec  # noqa: E402
    import render_mp4 as R  # noqa: E402
    from step7_color_grade import run_step7  # noqa: E402
    from person_matting import generate_person_matte_video  # noqa: E402

    tag = "tl" if depth == 0 else f"c{depth}"
    pdir = work / "pipeline"
    media_dir = work / "media"
    matte_dir = work / "matte"
    text_dir = work / "text_png"
    comp_dir = work / "compounds"
    for d in (pdir, media_dir, matte_dir, text_dir, comp_dir):
        d.mkdir(parents=True, exist_ok=True)

    mats = draft.get("materials") or {}
    fps = int(round(float(draft.get("fps") or fps_default)))
    canvas = draft.get("canvas_config") or {}
    canvas_w = int(canvas.get("width") or CANVAS_W)
    canvas_h = int(canvas.get("height") or CANVAS_H)
    pxu = canvas_w / 192.0  # 剑映字号单位：实测 font_size × 画布宽/192 ≈ 真实像素
    total_sec = float(draft.get("duration") or 0) / US
    if total_sec <= 0:
        segs_all = [s for t in draft.get("tracks") or [] for s in (t.get("segments") or [])]
        total_sec = max(
            ((s.get("target_timerange") or {}).get("start", 0) + (s.get("target_timerange") or {}).get("duration", 0))
            for s in segs_all
        ) / US
    W, H = CANVAS_W, CANVAS_H
    if depth == 0:
        report["fps"] = fps
        report["duration_sec"] = round(total_sec, 3)

    vid_by_id = {v["id"]: v for v in mats.get("videos") or []}
    aud_by_id = {a["id"]: a for a in mats.get("audios") or []}
    vfx_by_id = {e.get("id"): e for e in mats.get("video_effects") or []}

    def _seg_blur_sigma(seg: dict):
        """段级画面特效真值：extra_material_refs 里的 video_effects（如「模糊」）。
        实测剪映的 effects_adjust_blur 值 ×10 左右才对得上原版的模糊量。"""
        for _ref in seg.get("extra_material_refs") or []:
            _e = vfx_by_id.get(_ref)
            if not _e or "模糊" not in str(_e.get("name") or ""):
                continue
            _val = 0.5
            for _p in _e.get("adjust_params") or []:
                if _p.get("name") == "effects_adjust_blur":
                    _v = _p.get("value")
                    _val = float(_v if _v is not None else (_p.get("default_value") or 0.5))
            return max(1.0, 10.0 * _val)
        return None
    txt_by_id = {t["id"]: t for t in mats.get("texts") or []}
    stk_by_id = {s["id"]: s for s in mats.get("stickers") or []}
    drafts_entries = {dr["id"]: dr for dr in (mats.get("drafts") or []) if dr.get("draft")}

    def resolve_compound(seg: dict) -> dict | None:
        """段 → 内嵌子时间线 dict（经 extra_material_refs 关联）。"""
        for ref in seg.get("extra_material_refs") or []:
            dr = drafts_entries.get(ref)
            if dr:
                return dr["draft"]
        return None

    def materialize_photo(mat: dict, dur: float, out_base: Path) -> Path:
        outp = out_base.with_suffix(".mp4")
        if not outp.is_file():
            run_ffmpeg(["ffmpeg", "-y", "-loop", "1", "-t", f"{dur:.4f}",
                        "-i", _subst(mat.get("path") or "", base_dir),
                        "-vf", cover_crop_vf(W, H) + f",fps={fps}", "-an",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                        "-pix_fmt", "yuv420p", str(outp)])
        return outp

    def _compound_is_audio(sub: dict) -> bool:
        return any((t.get("segments") or []) for t in sub.get("tracks") or [] if t.get("type") == "audio")

    # ---------- 1. 复合片段预渲染（唯一化：同一子时间线只渲一次） ----------
    compound_media: dict = {}  # drafts entry id -> mp4 path
    compound_audio: dict = {}  # drafts entry id -> (mp4, has_audio)
    cls = classify_tracks(draft)

    def _build_master(cls_local: dict, comp_media: dict, comp_audio: dict, out_name: str) -> Path:
        """逐段裁切→定位→amix 合成母带；无音轨的源（照片/静图）探测后跳过并计数。"""
        segs = []
        for _idx, tl in cls_local["audios"]:
            segs += tl
        # 主轨视频**自带的声音**：剪映里口播常常就是主轨素材的原声（草稿里没有独立口播音轨），
        # 只拼音效轨/叠加轨会把整段人声丢掉。
        segs += list(cls_local["main"][1])
        # 复合片段音效叠层挂在视频轨上的：也按其段位置进母带
        for _idx, segs_v in cls_local["overlays"]:
            segs += segs_v
        inputs, chains, mix_ins = [], [], []
        n_in = 0
        no_stream_cache: dict = {}
        for s in segs:
            mat = aud_by_id.get(s.get("material_id")) or vid_by_id.get(s.get("material_id")) or {}
            if mat.get("type") == "photo":
                continue
            tgt = s.get("target_timerange") or {}
            srcr = s.get("source_timerange") or {}
            t_start = float(tgt.get("start") or 0) / US
            t_dur = float(tgt.get("duration") or 0) / US
            speed = float(s.get("speed") or 1.0)
            src = ""
            if not (mat.get("path") or ""):
                for ref in s.get("extra_material_refs") or []:
                    if ref in comp_media and comp_audio.get(ref, (None, False))[1]:
                        src = comp_media[ref]
                        break
                if not src:
                    if depth == 0:
                        report["unrealized"]["audio_segments_missing_source"] = report["unrealized"].get("audio_segments_missing_source", 0) + 1
                    continue
            else:
                src = _subst(mat.get("path") or "", base_dir)
                if not Path(src).is_file():
                    if depth == 0:
                        report["unrealized"]["audio_segments_missing_source"] = report["unrealized"].get("audio_segments_missing_source", 0) + 1
                    continue
            if src not in no_stream_cache:
                no_stream_cache[src] = ffprobe_has_audio(src)
            if not no_stream_cache[src]:
                if depth == 0:
                    report["unrealized"]["audio_segments_no_stream"] = report["unrealized"].get("audio_segments_no_stream", 0) + 1
                continue
            s_start = float(srcr.get("start") or 0) / US
            s_dur = float(srcr.get("duration") or (t_dur * speed)) / US
            vol = 1.0
            if isinstance(s.get("clip"), dict) and s["clip"].get("volume") is not None:
                vol = float(s["clip"]["volume"])
            elif s.get("volume") is not None:
                vol = float(s["volume"])
            inputs += ["-ss", f"{s_start:.4f}", "-t", f"{max(0.05, s_dur):.4f}", "-i", src]
            delay_ms = int(round(t_start * 1000))
            speed_fx = f"atempo={speed:.6f}," if abs(speed - 1.0) > 1e-4 else ""
            vol_fx = f"volume={vol:.4f}," if abs(vol - 1.0) > 1e-4 else ""
            chains.append(
                f"[{n_in}:a]aresample=48000,aformat=channel_layouts=stereo,{speed_fx}{vol_fx}adelay={delay_ms}|{delay_ms}[a{n_in}]"
            )
            mix_ins.append(f"[a{n_in}]")
            n_in += 1
        out = pdir / out_name
        if not mix_ins:
            return None  # 这条时间线根本没有可定位的音频（调用方决定：正常流程给静音母带，audio_only 才是错误）
        fc = (";".join(chains) + ";" + "".join(mix_ins)
              + f"amix=inputs={len(mix_ins)}:normalize=0:duration=longest,alimiter=limit=0.95,apad=whole_dur={total_sec:.4f},atrim=0:{total_sec:.4f}[af]")
        run_ffmpeg(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[af]",
                    "-t", f"{total_sec:.4f}",
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], timeout=300)
        return out
    all_segs = []
    for _i, segs in cls["overlays"]:
        all_segs += segs
    for _i, segs in cls["audios"]:
        all_segs += segs
    all_segs += cls["main"][1]
    for s in all_segs:
        mat = vid_by_id.get(s.get("material_id")) or aud_by_id.get(s.get("material_id")) or {}
        if mat and not (mat.get("path") or ""):
            sub = resolve_compound(s)
            if sub is None:
                continue
            key = None
            for ref in s.get("extra_material_refs") or []:
                if ref in drafts_entries:
                    key = ref
                    break
            if key in compound_media:
                continue
            if depth >= MAX_COMPOUND_DEPTH:
                report["unrealized"]["compound_depth_exceeded"] = report["unrealized"].get("compound_depth_exceeded", 0) + 1
                continue
            cdir = comp_dir / key[:8]
            cdir.mkdir(parents=True, exist_ok=True)
            sub_mp4 = cdir / "compound_alpha.mov"  # 带 alpha 的复合片段用 MOV（ProRes4444 进不了 mp4）
            import hashlib as _hl
            _ver = cdir / "compound_alpha.ver"
            _code_sig = _hl.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
            if not (sub_mp4.is_file() and _ver.is_file() and _ver.read_text().strip() == _code_sig):
                if not _compound_is_visual(sub):
                    # 纯音效叠层：只出音频母带，不渲画面（画面本来就没有）
                    sub_wav = cdir / "compound.wav"
                    sub_out = render_timeline(sub, base_dir, style_id, cdir, fps, False, report,
                                              depth + 1, audio_only=True)
                    if sub_out != sub_wav:
                        import shutil
                        shutil.move(str(sub_out), str(sub_wav))
                    compound_media[key] = sub_wav
                    compound_audio[key] = (sub_wav, True)
                    _ver.write_text(_code_sig)
                    if depth == 0:
                        report.setdefault("compounds_rendered", []).append(
                            {"ref": key[:8], "visual": False, "has_audio": True, "audio_only": True})
                    continue
                sub_out = render_timeline(sub, base_dir, style_id, cdir, fps, False, report, depth + 1,
                                          alpha_out=True)
                if sub_out != sub_mp4:
                    import shutil
                    shutil.move(str(sub_out), str(sub_mp4))
                _ver.write_text(_code_sig)
            compound_media[key] = sub_mp4
            compound_audio[key] = (sub_mp4, _compound_is_audio(sub))
            if depth == 0:
                report.setdefault("compounds_rendered", []).append(
                    {"ref": key[:8], "visual": _compound_is_visual(sub), "has_audio": _compound_is_audio(sub)}
                )

    if audio_only:
        # 纯音频时间线：只出母带 wav（复合片段音频 Shortcut）
        mw = _build_master(cls, compound_media, compound_audio, "master_only.wav")
        if mw is None:
            raise RuntimeError("纯音频复合片段里没有任何可定位的音频段")
        return mw

    # ---------- 2. 主轨 EDL ----------
    main_idx, main_segs = cls["main"]
    clips = []
    for k, s in enumerate(main_segs):
        mat = vid_by_id.get(s.get("material_id")) or {}
        if not mat:
            raise RuntimeError(f"主轨段 {k} 找不到素材 {s.get('material_id')}")
        mtype = mat.get("type")
        tgt = s.get("target_timerange") or {}
        srcr = s.get("source_timerange") or {}
        t_start = float(tgt.get("start") or 0) / US
        t_dur = float(tgt.get("duration") or 0) / US
        speed = float(s.get("speed") or 1.0)
        ckey = None
        if not (mat.get("path") or ""):
            for ref in s.get("extra_material_refs") or []:
                if ref in compound_media:
                    ckey = ref
                    break
        if ckey:
            src = compound_media[ckey]
            s_in = float(srcr.get("start") or 0) / US
            s_out = s_in + t_dur * speed
        elif mtype == "photo":
            src = materialize_photo(mat, max(t_dur, 0.5), media_dir / f"photo_{mat['id'][:8]}")
            s_in, s_out = 0.0, t_dur
        else:
            src = _subst(mat.get("path") or "", base_dir)
            s_in = float(srcr.get("start") or 0) / US
            s_out = s_in + t_dur * speed
        if not Path(src).is_file():
            raise RuntimeError(f"主轨素材不存在: {src}")
        clip = {
            "clip_id": f"m{k:03d}",
            "track_role": "a_roll",
            "media_path": src,
            "source_in_sec": round(s_in, 4),
            "source_out_sec": round(s_out, 4),
            "duration_sec": round(t_dur, 4),
            "timeline_in_frame": int(round(t_start * fps)),
            "timeline_out_frame": int(round((t_start + t_dur) * fps)),
        }
        cl = s.get("clip") or {}
        sc0 = float((cl.get("scale") or {}).get("x") or 1.0)
        tx0 = float((cl.get("transform") or {}).get("x") or 0.0)
        ty0 = float((cl.get("transform") or {}).get("y") or 0.0)
        alpha = float(cl.get("alpha") if cl.get("alpha") is not None else 1.0)
        if abs(alpha - 1.0) > 1e-3:
            report["unrealized"]["main_alpha"] = report["unrealized"].get("main_alpha", 0) + 1
        # keyframe（时间为源时间）：scale 动画拆段；位移取终态（推近类动画的偏移通常归零）
        _kfs = _kf_rel(s, "KFTypeScaleX", s_in)
        _fx = _kf_final(s, "KFTypePositionX")
        _fy = _kf_final(s, "KFTypePositionY")
        tx = _fx if _fx is not None else tx0
        ty = _fy if _fy is not None else ty0
        _t_end = t_start + t_dur
        pieces = []  # (t_a, t_b, scale|None=kenburns, tx, ty)
        if len(_kfs) == 2 and abs(_kfs[0][1] - _kfs[1][1]) > 1e-3 and _kfs[1][0] > _kfs[0][0]:
            _a0, _s0 = _kfs[0]
            _a1, _s1 = _kfs[1]
            _a0 = min(_a0, t_dur)
            _a1 = min(_a1, t_dur)
            if _a0 > 1e-3:
                pieces.append((t_start, t_start + _a0, _s0, tx, ty))
            if min(_s0, _s1) >= 0.999:
                # 纯推近（zoom≥1）：交给渲染器 ken_burns 逐帧插值
                pieces.append((t_start + _a0, t_start + _a1, None, tx, ty))
                report["unrealized"]["main_kf_zoom_kenburns"] = report["unrealized"].get("main_kf_zoom_kenburns", 0) + 1
            else:
                # 含缩窗（scale<1）：拆成按 2 帧步长的静态子段（黑底缩窗），近似动画
                _span = max(1e-3, _a1 - _a0)
                _n = max(2, int(round(_span * fps / 2)))
                for _i in range(_n):
                    _ta = t_start + _a0 + _span * _i / _n
                    _tb = t_start + _a0 + _span * (_i + 1) / _n
                    _ss = _s0 + (_s1 - _s0) * (_i + 0.5) / _n
                    pieces.append((_ta, _tb, _ss, tx, ty))
                report["unrealized"]["main_kf_zoom_sliced"] = report["unrealized"].get("main_kf_zoom_sliced", 0) + _n
            if _t_end > t_start + _a1 + 1e-4:
                pieces.append((t_start + _a1, _t_end, _s1, tx, ty))
            report["unrealized"]["main_keyframe_animations"] = report["unrealized"].get("main_keyframe_animations", 0) + 1
        else:
            _fs = _kfs[-1][1] if _kfs else None
            if _fs is not None or _fx is not None or _fy is not None:
                report["unrealized"]["main_keyframe_static_end"] = report["unrealized"].get("main_keyframe_static_end", 0) + 1
            pieces.append((t_start, _t_end, _fs if _fs is not None else sc0, tx, ty))
        for _pi, (_ta, _tb, _sc_p, _ptx, _pty) in enumerate(pieces):
            if _tb - _ta <= 1e-4:
                continue
            _f_in = int(round(_ta * fps))
            _f_out = int(round(_tb * fps))
            if _pi == 0:
                _f_in = int(round(t_start * fps))
            if _pi == len(pieces) - 1:
                _f_out = int(round(_t_end * fps))
            if _f_out <= _f_in:
                _f_out = _f_in + 1
            _s_in_p = s_in + (_f_in / fps - t_start) * speed
            _s_out_p = _s_in_p + ((_f_out - _f_in) / fps) * speed
            cid = f"m{k:03d}" if len(pieces) == 1 else f"m{k:03d}p{_pi}"
            _clip = {
                "clip_id": cid,
                "track_role": "a_roll",
                "media_path": src,
                "source_in_sec": round(_s_in_p, 4),
                "source_out_sec": round(_s_out_p, 4),
                "duration_sec": round((_f_out - _f_in) / fps, 4),
                "timeline_in_frame": _f_in,
                "timeline_out_frame": _f_out,
            }
            _vf_extra = []
            _seg_blur = _seg_blur_sigma(s)
            if _seg_blur:
                # 段级特效（模糊）要在缩放/缩窗前作用于源画面
                _vf_extra.append(f"gblur=sigma={_seg_blur:.2f}")
            if _sc_p is None:
                # ken_burns：从首帧 zoom 到末帧（两端均 ≥1）
                _clip["ken_burns"] = {"zoom_start": _s0, "zoom_end": _s1}
                _clip["pip_layout"] = None
                _zoom_piece = True
            else:
                _zoom_piece = False
            if not _zoom_piece:
                if _sc_p < 0.999:
                    # 缩窗：草稿真值是黑底居中缩窗（原版四角纯黑），不是渲染器默认的自身模糊铺底
                    _clip["pip_layout"] = None
                    w2 = int(round(W * _sc_p / 2)) * 2
                    h2 = int(round(H * _sc_p / 2)) * 2
                    px = max(0, min(W - w2, int(round((W - w2) / 2 + _ptx * W / 2))))
                    py = max(0, min(H - h2, int(round((H - h2) / 2 - _pty * H / 2))))
                    _vf_extra.append(f"scale={w2}:{h2}:flags=bicubic,pad={W}:{H}:{px}:{py}:black")
                elif _sc_p > 1.001 or abs(_ptx) > 1e-4 or abs(_pty) > 1e-4:
                    # face_anchor 显式置 None（顶层）：复刻用草稿的居中缩放，禁止人脸锚定推近
                    _clip["pip_layout"] = {"mode": "geometry_pip", "face_anchor": None,
                                           "main": {"scale": _sc_p, "x": _ptx, "y": -_pty}}
                else:
                    _clip["pip_layout"] = None
            if abs(alpha - 1.0) > 1e-3:
                # 主轨半透明＝与黑底混合：把 RGB 乘 alpha 烘进段里
                _a = max(0.0, min(1.0, alpha))
                _vf_extra.append(f"lutrgb=r='val*{_a:.4f}':g='val*{_a:.4f}':b='val*{_a:.4f}'")
            if _vf_extra:
                _clip["vf_extra"] = ",".join(_vf_extra)
            if mat.get("crop"):
                report["unrealized"]["material_crop"] = report["unrealized"].get("material_crop", 0) + 1
            clips.append(_clip)
    if depth == 0:
        report["main_segments"] = len(clips)

    edl = {
        "style_id": style_id,
        "fps": fps,
        "clips": clips,
        "duration_sec": round(total_sec, 3),
        "replica": True,
        "replica_note": "EDL 直接来自解密草稿主轨，不经 run_step4 重排",
    }
    write_json(pdir / "04_edl.json", edl)
    write_json(pdir / "01_manifest.json", {"items": []})
    write_json(pdir / "style_spectrum_applied.json", {"style_id": style_id})

    # ---------- 3. 音频母带（普通音频段 + 复合片段的声音） ----------
    master = _build_master(cls, compound_media, compound_audio, "08_mixed_master.wav")
    if master is None:
        # 有画面无音频的时间线：生成等长静音，不阻塞复刻
        run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"anullsrc=channel_layout=stereo:sample_rate=48000:d={total_sec:.4f}",
                    "-c:a", "pcm_s16le", str(pdir / "08_mixed_master.wav")], timeout=120)
        master = pdir / "08_mixed_master.wav"
        report["unrealized"]["timeline_no_audio"] = report["unrealized"].get("timeline_no_audio", 0) + 1
    if depth == 0:
        drift = abs(get_wav_duration_sec(master) - total_sec)
        if drift >= 0.5:
            raise RuntimeError(f"音频母带时长与草稿不符（差 {drift:.2f}s）")

    # ---------- 4. 调色 plan（草稿真值优先） ----------
    # 草稿没有 filter 素材 / filter 轨 / clip.adjust 时，原版就是原素材本色，
    # 禁止把 step7 推断的风格调色强套进复刻（style_21 实测被压暗约 40 级）。
    _has_draft_grade = (
        bool((draft.get("materials") or {}).get("filters"))
        or any(t.get("type") == "filter" for t in (draft.get("tracks") or []))
        or any((s.get("clip") or {}).get("adjust")
               for t in (draft.get("tracks") or []) for s in (t.get("segments") or []))
    )
    if _has_draft_grade:
        run_step7(pdir, style_id=style_id)
        color_plan = read_json(pdir / "07_color_plan.json")
    else:
        report["notes"].append("draft_grade_truth=none -> skip step7 grade")
        color_plan = {"clip_plans": [], "lut_path": ""}

    # ---------- 5. 主轨渲染 ----------
    ffmpeg_bin = find_bin("ffmpeg")
    if clips:
        base_mp4 = R.build_base_video_track(pdir, edl, color_plan, master, manifest={"items": []}, preview=preview)
    elif alpha_out:
        # 复合片段：底是透明的，不是黑场（否则全幅盖掉主轨）
        base_mp4 = pdir / "_render_final" / "base_alpha.mov"
        base_mp4.parent.mkdir(parents=True, exist_ok=True)
        if not base_mp4.is_file():
            run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"color=c=black@0.0:s={W}x{H}:r={fps}:d={max(0.1, total_sec):.4f},format=rgba",
                        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(base_mp4)], timeout=300)
    else:
        # 主轨 0 段（纯叠加/文本时间线，如照片翻页复合片段）：黑场底座，内容全在叠加层
        base_mp4 = pdir / "_render_final" / "base_video.mp4"
        base_mp4.parent.mkdir(parents=True, exist_ok=True)
        if not base_mp4.is_file():
            run_ffmpeg(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={fps}:d={max(0.1, total_sec):.4f}",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", str(base_mp4)], timeout=300)
        report.setdefault("notes", []).append("主轨 0 段：黑场底座，画面全在叠加/文本层")

    # ---------- 6. 合成单元 ----------
    units = []

    for t_idx, segs in cls["overlays"]:
        for o, s in enumerate(segs):
            mat = vid_by_id.get(s.get("material_id")) or {}
            tgt = s.get("target_timerange") or {}
            srcr = s.get("source_timerange") or {}
            t_start = float(tgt.get("start") or 0) / US
            t_dur = float(tgt.get("duration") or 0) / US
            speed = float(s.get("speed") or 1.0)
            s_in = float(srcr.get("start") or 0) / US
            s_dur = t_dur * speed
            ckey = None
            if not (mat.get("path") or ""):
                for ref in s.get("extra_material_refs") or []:
                    if ref in compound_media:
                        ckey = ref
                        break
            if ckey and not _compound_is_visual(drafts_entries[ckey]["draft"]):
                continue  # 纯音效复合片段：画面透明，声音已进母带
            if ckey:
                src = compound_media[ckey]
            else:
                src = _subst(mat.get("path") or "", base_dir)
            if not src or not Path(src).is_file():
                report["unrealized"]["overlay_missing_source"] = report["unrealized"].get("overlay_missing_source", 0) + 1
                continue
            cl = s.get("clip") or {}
            sc = float((cl.get("scale") or {}).get("x") or 1.0)
            tx = float((cl.get("transform") or {}).get("x") or 0.0)
            ty = float((cl.get("transform") or {}).get("y") or 0.0)
            alpha = float(cl.get("alpha") if cl.get("alpha") is not None else 1.0)
            flip = cl.get("flip") or {}
            if sc < 0.05 and s.get("common_keyframes"):
                # 打字机/缩放入场动画的起始态；终态按 1.0 铺开，动画本身计数不假装
                sc = 1.0
                report["unrealized"]["overlay_keyframe_animation"] = report["unrealized"].get("overlay_keyframe_animation", 0) + 1
            # keyframe 透明度/位置：两点线性直接表达（fade / overlay 表达式），不静默消失
            _kfa = _kf_rel(s, "KFTypeAlpha", s_in)
            _kfx = _kf_rel(s, "KFTypePositionX", s_in)
            _kfy = _kf_rel(s, "KFTypePositionY", s_in)
            fade = None
            if _kfa:
                if len(_kfa) == 2 and {_kfa[0][1], _kfa[1][1]} == {0.0, 1.0}:
                    fade = ("in" if _kfa[0][1] < _kfa[1][1] else "out", max(0.05, _kfa[1][0] - _kfa[0][0]), _kfa[0][0])
                    alpha = 1.0
                else:
                    alpha = (_kfa[0][1] + _kfa[-1][1]) / 2.0
                    report["unrealized"]["overlay_keyframe_alpha_approx"] = report["unrealized"].get("overlay_keyframe_alpha_approx", 0) + 1
                report["unrealized"]["overlay_keyframe_animations"] = report["unrealized"].get("overlay_keyframe_animations", 0) + 1
            elif alpha < 0.01 and s.get("common_keyframes"):
                alpha = 1.0
                report["unrealized"]["overlay_keyframe_animations"] = report["unrealized"].get("overlay_keyframe_animations", 0) + 1
            pos_kf = None
            # 叠加层缩放 keyframe：取“保持最久”的稳态值（卡片是 0.234 保持到 2.22s 再飞走，取终值 0.01 等于弄丢图层）
            _kfsx = _kf_rel(s, "KFTypeScaleX", s_in)
            _sc_steady = _kf_steady(_kfsx, t_dur)
            if _sc_steady is not None and abs(_sc_steady - sc) > 1e-3 and not (sc < 0.05):
                sc = _sc_steady
                report["unrealized"]["overlay_keyframe_scale_end"] = report["unrealized"].get("overlay_keyframe_scale_end", 0) + 1
            if len(_kfx) == 2 and len(_kfy) == 2 and _kfx[1][0] > _kfx[0][0] and _kfy[1][0] > _kfy[0][0]:
                pos_kf = (_kfx, _kfy)
                report["unrealized"]["overlay_keyframe_position"] = report["unrealized"].get("overlay_keyframe_position", 0) + 1
            elif _kfx or _kfy:
                report["unrealized"]["overlay_keyframe_position_approx"] = report["unrealized"].get("overlay_keyframe_position_approx", 0) + 1
            # 注：不做「与主轨同素材同几何就跳过」——那是 z 序三明治手法（主轨上面盖卡片、
            # 再叠同素材抠像人物），删了会把 8s 这类层序弄丢；抠像错位问题已由 fps 对齐根治。
            alpha_src = bool(ckey and _compound_is_visual(drafts_entries[ckey]["draft"]))
            seg_p = media_dir / (f"ov_{t_idx:02d}_{o:02d}_{_cache_tag('ov', s_in, s_dur, sc, tx, ty, alpha, fps, flip)}" + (".mov" if alpha_src else ".mp4"))
            if not seg_p.is_file():
                fl = "hflip," if flip.get("h") else ("vflip," if flip.get("v") else "")
                _ov_blur = _seg_blur_sigma(s)
                fl = (f"gblur=sigma={_ov_blur:.2f}," if _ov_blur else "") + fl
                if alpha_src:
                    # 复合片段输出是 ProRes4444 带 alpha：抽段必须保 alpha
                    run_ffmpeg([ffmpeg_bin, "-y", "-ss", f"{s_in:.4f}", "-t", f"{max(0.05, s_dur):.4f}", "-i", src,
                                "-vf", f"fps={fps},format=rgba,{fl}" + cover_crop_vf(W, H)
                                + (f",scale={int(round(W * sc / 2)) * 2}:{int(round(H * sc / 2)) * 2}:flags=bicubic" if abs(sc - 1.0) > 1e-3 else ""),
                                "-an", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(seg_p)], timeout=600)
                else:
                    if (mat.get("type") or "") == "photo":
                        # 静图：-t 放在 -i 前会限制输入读取（单帧图只能得 ~2 帧），必须 -loop 1 + 输出侧 -t
                        run_ffmpeg([ffmpeg_bin, "-y", "-loop", "1", "-i", src,
                                    "-vf", f"fps={fps},{fl}" + cover_crop_vf(W, H)
                                    + (f",scale={int(round(W * sc / 2)) * 2}:{int(round(H * sc / 2)) * 2}:flags=bicubic" if abs(sc - 1.0) > 1e-3 else ""),
                                    "-t", f"{max(0.05, s_dur):.4f}",
                                    "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                                    "-pix_fmt", "yuv420p", str(seg_p)])
                    else:
                        run_ffmpeg([ffmpeg_bin, "-y", "-ss", f"{s_in:.4f}", "-t", f"{max(0.05, s_dur):.4f}", "-i", src,
                                    "-vf", f"fps={fps},{fl}" + cover_crop_vf(W, H)
                                    + (f",scale={int(round(W * sc / 2)) * 2}:{int(round(H * sc / 2)) * 2}:flags=bicubic" if abs(sc - 1.0) > 1e-3 else ""),
                                    "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                                    "-pix_fmt", "yuv420p", str(seg_p)])
            matte_p = None
            # matting.path 为空是草稿占位标记，原版并未抠像；只有非空才走人像抠像
            want_matte = bool((mat.get("matting") or {}).get("path"))
            if ckey is not None:
                # 照片/文本拼的复合片段没有可抠的真人，抠了反而丢图层
                sub_ref = drafts_entries.get(ckey, {}).get("draft") or {}
                if not _compound_has_personable_video(sub_ref):
                    want_matte = False
            if want_matte:
                matte_p = matte_dir / f"ov_{t_idx:02d}_{o:02d}_{_cache_tag('matte', s_in, s_dur, fps, sc)}_matte.mp4"
                if not matte_p.is_file():
                    meta = generate_person_matte_video(seg_p, matte_p, quality="balanced", fps=fps)
                    if not meta or not meta.get("has_person") or not matte_p.is_file():
                        matte_p = None
            if want_matte and matte_p is None:
                report["matte_failed"] += 1
                report["unrealized"]["overlay_matte_failed"] = report["unrealized"].get("overlay_matte_failed", 0) + 1
                continue  # 抠像失败不下假前景
            units.append((t_idx, o, {
                "kind": "video", "seg": seg_p, "matte": matte_p, "t_start": t_start, "t_dur": t_dur,
                "scale": sc, "tx": tx, "ty": ty, "alpha": alpha, "fade": fade, "pos_kf": pos_kf,
            }))
            if depth == 0:
                report["overlay_segments"] += 1
                if matte_p:
                    report["matte_done"] += 1

    for t_idx, segs in cls["texts"]:
        for o, s in enumerate(segs):
            mat = txt_by_id.get(s.get("material_id")) or {}
            if not mat:
                report["unrealized"]["text_missing_material"] = report["unrealized"].get("text_missing_material", 0) + 1
                continue
            try:
                spec = parse_text_material(mat)
            except Exception:
                report["unrealized"]["text_parse_failed"] = report["unrealized"].get("text_parse_failed", 0) + 1
                continue
            if not spec["text"].strip():
                continue
            tgt = s.get("target_timerange") or {}
            t_start = float(tgt.get("start") or 0) / US
            t_dur = float(tgt.get("duration") or 0) / US
            cl = s.get("clip") or {}
            sc = float((cl.get("scale") or {}).get("x") or 1.0)
            if sc < 0.05 and s.get("common_keyframes"):
                sc = 1.0
                report["unrealized"]["text_keyframe_animation"] = report["unrealized"].get("text_keyframe_animation", 0) + 1
            tx = float((cl.get("transform") or {}).get("x") or 0.0)
            ty = float((cl.get("transform") or {}).get("y") or 0.0)
            png, pw, ph = render_text_png(spec, sc, text_dir / f"tx_{t_idx:02d}_{o:02d}.png",
                                           fail_log=report.setdefault("font_failures", []), pxu=pxu)
            units.append((t_idx, o, {
                "kind": "text", "png": png, "pw": pw, "ph": ph, "t_start": t_start, "t_dur": t_dur,
                "tx": tx, "ty": ty, "alpha": 1.0,
            }))
            if depth == 0:
                report["text_segments"] += 1

    for t_idx, segs in cls["stickers"]:
        for o, s in enumerate(segs):
            mat = stk_by_id.get(s.get("material_id")) or {}
            src = _subst(mat.get("path") or "", base_dir)
            if not src or not Path(src).is_file():
                report["unrealized"]["stickers"] = report["unrealized"].get("stickers", 0) + 1
                continue
            tgt = s.get("target_timerange") or {}
            t_start = float(tgt.get("start") or 0) / US
            t_dur = float(tgt.get("duration") or 0) / US
            cl = s.get("clip") or {}
            sc = float((cl.get("scale") or {}).get("x") or 1.0)
            tx = float((cl.get("transform") or {}).get("x") or 0.0)
            ty = float((cl.get("transform") or {}).get("y") or 0.0)
            units.append((t_idx, o, {
                "kind": "sticker", "png": src, "pw": None, "ph": None, "t_start": t_start, "t_dur": t_dur,
                "tx": tx, "ty": ty, "alpha": float(cl.get("alpha") or 1.0), "scale": sc,
            }))
            if depth == 0:
                report["sticker_segments"] += 1

    # ---------- 7. 一次合成 pass：z 序 = 轨序 ----------
    units.sort(key=lambda u: (u[0], u[1]))
    final_out = (work / "compound_alpha.mov") if alpha_out else (work / ("final.mp4" if depth == 0 else "compound.mp4"))
    if preview and depth == 0:
        run_ffmpeg([ffmpeg_bin, "-y", "-i", str(base_mp4), "-i", str(master),
                    "-map", "0:v", "-map", "1:a", "-shortest",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(final_out)])
        report["output"] = str(final_out)
        report["notes"].append("preview：只主轨+音频，未合成叠加/文本")
        return final_out

    from PIL import Image as PILImage  # noqa: E402

    inputs = ["-i", str(base_mp4)]
    n_in = 1
    fc_parts = []
    cur = "[0:v]"
    for n, (_t, _o, u) in enumerate(units):
        en = f"enable='between(t,{u['t_start']:.4f},{min(u['t_start'] + u['t_dur'], total_sec):.4f})'"
        if u["kind"] == "video":
            seg_i = n_in
            inputs += ["-i", str(u["seg"])]
            n_in += 1
            if u["matte"]:
                matte_i = n_in
                inputs += ["-i", str(u["matte"])]
                n_in += 1
                dur_u = min(u["t_dur"], total_sec - u["t_start"])
                t0_u = u["t_start"]
                # 前景与 matte 强制同 fps 同长（Vision 抠像输出帧率会漂，不拉齐就是逐帧错位的鬼影）；
                # 流必须平移到 t_start：enable 只管显隐，叠层流从 0 开始消费会让内容时间戳全错
                fc_parts.append(
                    f"[{seg_i}:v]fps={fps},setpts=PTS-STARTPTS,trim=duration={dur_u:.4f},setpts=PTS+{t0_u:.4f}/TB,format=rgba[fg{n}];"
                    f"[{matte_i}:v]fps={fps},setpts=PTS-STARTPTS,trim=duration={dur_u:.4f},setpts=PTS+{t0_u:.4f}/TB,format=gray[mm{n}];"
                    f"[fg{n}][mm{n}]alphamerge[cut{n}]"
                )
            else:
                fc_parts.append(f"[{seg_i}:v]fps={fps},setpts=PTS+{u['t_start']:.4f}/TB,format=rgba[cut{n}]")
            _cut_lbl = f"[cut{n}]"
            if u.get("fade"):
                _dir, _d, _st_off = u["fade"]
                fc_parts.append(f"{_cut_lbl}fade=t={_dir}:st={u['t_start'] + _st_off:.4f}:d={_d:.4f}:alpha=1[cutf{n}]")
                _cut_lbl = f"[cutf{n}]"
            if abs(u["alpha"] - 1.0) > 1e-3:
                fc_parts.append(f"{_cut_lbl}colorchannelmixer=aa={u['alpha']:.3f}[cutx{n}]")
                src_lbl = f"[cutx{n}]"
            else:
                src_lbl = _cut_lbl
            ov_w = int(round(W * u["scale"])) if abs(u.get("scale", 1.0) - 1.0) > 1e-3 else W
            ov_h = int(round(H * u["scale"])) if abs(u.get("scale", 1.0) - 1.0) > 1e-3 else H
            ox = int(round((W - ov_w) / 2 + u["tx"] * W / 2))
            oy = int(round((H - ov_h) / 2 - u["ty"] * H / 2))  # 剪映 y 正=向上
            if u.get("pos_kf"):
                (_pkx, _pky) = u["pos_kf"]
                # keyframe 位置与 clip.transform 同单位（-1..1，y 正=向上），换算到像素中心：
                #   px = W/2 + v*W/2 - ov_w/2 ；py = H/2 - v*H/2 - ov_h/2
                _xs = _kf_piecewise(_pkx, u["t_start"], W / 2.0, W / 2.0 - ov_w / 2)
                _ys = _kf_piecewise(_pky, u["t_start"], -H / 2.0, H / 2.0 - ov_h / 2)
                fc_parts.append(f"{cur}{src_lbl}overlay=x='{_xs}':y='{_ys}':{en}[v{n}]")
            else:
                fc_parts.append(f"{cur}{src_lbl}overlay=x={ox}:y={oy}:{en}[v{n}]")
        else:  # text / sticker PNG
            png_i = n_in
            inputs += ["-loop", "1", "-t", f"{max(0.05, u['t_dur']):.4f}", "-i", str(u["png"])]
            n_in += 1
            if u["kind"] == "sticker":
                with PILImage.open(u["png"]) as _im:
                    nat_w, nat_h = _im.size
                sc = float(u.get("scale") or 1.0)
                pw = max(2, int(round(nat_w * sc / 2)) * 2)
                ph = max(2, int(round(nat_h * sc / 2)) * 2)
                if (pw, ph) != (nat_w, nat_h):
                    fc_parts.append(f"[{png_i}:v]format=rgba,scale={pw}:{ph}[t{n}]")
                else:
                    fc_parts.append(f"[{png_i}:v]format=rgba[t{n}]")
                report.setdefault("notes", []).append(
                    f"sticker 尺寸按原生像素×{sc:.2f} 近似（草稿贴纸无绝对 px 真值）")
            else:
                pw, ph = u["pw"], u["ph"]
                fc_parts.append(f"[{png_i}:v]format=rgba[t{n}]")
            if abs(u["alpha"] - 1.0) > 1e-3:
                fc_parts.append(f"[t{n}]colorchannelmixer=aa={u['alpha']:.3f}[tx{n}]")
                lbl = f"[tx{n}]"
            else:
                lbl = f"[t{n}]"
            ox = int(round(W / 2 + u["tx"] * W / 2 - pw / 2))
            oy = int(round(H / 2 - u["ty"] * H / 2 - ph / 2))  # 剪映 y 正=向上
            fc_parts.append(f"{cur}{lbl}overlay=x={ox}:y={oy}:{en}[v{n}]")
        cur = f"[v{n}]"

    if not units:
        tail = "[0:v]null"
    else:
        tail = f"{cur}null"
    # 草稿 effect 轨真值：整画面特效按源窗口直接上（剪映「模糊」≈ gblur），不再蹭风格配置推断
    _vfx_by_id = vfx_by_id
    for _ti, _esegs in cls.get("effects") or []:
        for _s in _esegs:
            _e = _vfx_by_id.get(_s.get("material_id")) or {}
            _nm = str(_e.get("name") or "")
            _trr = _s.get("target_timerange") or {}
            _st = float(_trr.get("start") or 0) / US
            _en = _st + float(_trr.get("duration") or 0) / US
            if "模糊" in _nm:
                _bv = 0.5
                for _p in _e.get("adjust_params") or []:
                    if _p.get("name") == "effects_adjust_blur":
                        _bv = float(_p.get("value") if _p.get("value") is not None else (_p.get("default_value") or 0.5))
                tail += f",gblur=sigma={max(1.0, 10.0 * _bv):.2f}:enable='between(t,{_st:.3f},{_en:.3f})'"
            else:
                report["unrealized"]["effect_not_implemented"] = report["unrealized"].get("effect_not_implemented", 0) + 1
    fc_parts.append(tail + "[vout]")
    fc = ";".join(fc_parts)
    fscript = work / "composite_filter.txt"
    fscript.write_text(fc, encoding="utf-8")
    help_txt = subprocess.run([ffmpeg_bin, "-hide_banner", "-h", "long"], capture_output=True, text=True).stdout or ""
    if "filter_complex_script" in help_txt:
        fc_args = ["-filter_complex_script", str(fscript)]
    elif "/filter_complex" in help_txt:
        fc_args = ["-/filter_complex", str(fscript)]
    else:
        fc_args = ["-filter_complex", fc]
    master_i = n_in
    inputs += ["-i", str(master)]
    final = work / ("composite_final.mov" if alpha_out else "composite_final.mp4")
    enc_args = (["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
                if alpha_out else
                ["-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p"])
    run_ffmpeg([ffmpeg_bin, "-y", *inputs, *fc_args,
                "-map", "[vout]", "-map", f"{master_i}:a",
                "-t", f"{total_sec:.4f}",
                *enc_args,
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(final)], timeout=900)
    if alpha_out and units:
        # 自证：带图层的复合片段不允许交付全透明空壳（曾因静图 -t 放在 -i 前整段丢失）。
        # 多点采样：卡片/淡出类图层在采样点上可能合法为空，任一点非零即可。
        import io as _io
        from PIL import Image as _Img
        _amax_all = 0
        for _ts in (0.1, total_sec / 3, total_sec * 2 / 3):
            _ts = min(max(0.05, _ts), max(0.05, total_sec - 0.1))
            _probe = subprocess.run([ffmpeg_bin, "-v", "error", "-ss", f"{_ts:.3f}",
                                     "-i", str(final), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                                    capture_output=True)
            if _probe.stdout:
                _amax_all = max(_amax_all, _Img.open(_io.BytesIO(_probe.stdout)).convert("RGBA").getchannel("A").getextrema()[1])
            if _amax_all >= 8:
                break
        if _amax_all < 8:
            raise RuntimeError(f"复合片段合成输出全透明（{len(units)} 个图层全部丢失），拒绝交付空壳: {final}")
    import shutil

    shutil.move(str(final), str(final_out))
    if depth == 0:
        report["output"] = str(final_out)
    return final_out


def replicate_style(style_id: str, draft_name: str, out_dir: Path, draft_root: Path,
                    decrypted_root: Path, preview: bool = False) -> dict:
    draft_dir = draft_root / draft_name
    if not draft_dir.is_dir():
        raise SystemExit(f"草稿目录不存在: {draft_dir}")
    draft = load_draft(draft_dir, decrypted_root)
    work = out_dir / "replica_work" / style_id
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "style_id": style_id, "draft": draft_dir.name,
        "main_segments": 0, "overlay_segments": 0, "matte_done": 0, "matte_failed": 0,
        "text_segments": 0, "sticker_segments": 0,
        "compounds_rendered": [], "unrealized": {}, "notes": [],
    }
    final = render_timeline(draft, draft_dir, style_id, work, 30, preview, report, depth=0)
    out_mp4 = out_dir / f"{style_id}_replica.mp4"
    if final != out_mp4:
        import shutil
        shutil.copy2(str(final), str(out_mp4))
        report["output"] = str(out_mp4)
    import json as _json
    (work / "replica_report.json").write_text(
        _json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style-id")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="/tmp/blind_review")
    ap.add_argument("--draft-root", default="/Users/jerry/Movies/JianyingPro/User Data/Projects/com.lveditor.draft")
    ap.add_argument("--decrypted-root", default=str(ROOT / "forensic" / "decrypted"))
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    sources = json.loads((ROOT / "forensic" / "style_sources.json").read_text(encoding="utf-8"))["styles"]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = sorted(sources.items()) if args.all else [(args.style_id, sources[args.style_id])]
    failures = []
    for sid, dname in targets:
        print(f"[replica] {sid} <- {dname}")
        try:
            rep = replicate_style(sid, dname, out_dir, Path(args.draft_root), Path(args.decrypted_root), preview=args.preview)
            print(f"[replica] {sid} 完成 -> {rep.get('output')}  unrealized={rep['unrealized']}")
        except Exception as exc:
            failures.append(f"{sid}: {type(exc).__name__}: {exc}")
            print(f"[replica] {sid} 失败: {exc}")
    if failures:
        print("[replica] 失败清单：")
        for f in failures:
            print("  " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
