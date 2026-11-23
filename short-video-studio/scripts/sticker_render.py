#!/usr/bin/env python3
"""
剪映贴纸渲染：把 sticker 轨渲染成「画布大小的 RGBA 图层」，再叠到成片上。

几何真值（用原版帧反解出来的，不是猜的）
────────────────────────────────────────
实测草稿（画布 1080×1920）里的「手机屏幕边框」贴纸：素材原生 475×540、`clip.scale = 2.1882`、
`clip.transform = (0.5721, -0.1499)`；画布 1080×1920。
按 `显示宽 = 原生宽 × scale × 画布宽/720` 算：475×2.1882×1.5 = 1559px 宽、
高 1559×540/475 = 1772px，中心 y = 960 + 0.1499×960 = 1104 → 上边界 218px = 画布 11.4%。
在原版 11.0s 帧上量「手+手机」上边界 ≈ 0.115 —— 吻合。
所以 **720 是贴纸缩放的参考宽**（剪映 infoSticker.lua 里 `setScale(viewerWidth/720)` 也印证）。

坐标：`center_x = W/2 + tx·W/2`，`center_y = H/2 - ty·H/2`（剪映 y 正=向上）。

用法
────
    layers = plan_sticker_layers(draft, base_dir, W, H, fps, total, work_dir, report)
    overlay_stickers("base.mp4", layers, "out.mp4")
"""

from __future__ import annotations

import json
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from sticker_assets import resolve_sticker_dir, sticker_sequence

try:  # 允许脚本目录被直接加入 sys.path 的用法
    from animations import parse_animations, summarize
    from anim_render import render_animated_layer
except Exception:  # pragma: no cover
    from .animations import parse_animations, summarize  # type: ignore
    from .anim_render import render_animated_layer  # type: ignore

US = 1_000_000
STICKER_REF_WIDTH = 720.0   # 剪映贴纸缩放的参考宽（同比 lua 里的 viewerWidth/720）

# ---------- 出场动画真值：窗口 + 缓动 + 位移幅度 ----------
# 草稿里 out 动画的两个字段决定窗口：
#   · `start` 存在 = **相对该段起点**的微秒偏移（不是时间线绝对时间）；
#   · `start` 缺失 = 剪映按「段尾倒数 duration」摆位，窗口 = [段时长 - duration, 段时长]。
#     style_21 手机边框贴纸实测：target_timerange = 10.433333 + 2.466667 → 段尾 12.9000；
#     出「向下滑动」duration = 500000（0.5s）且 **没有 start 字段** → 窗口 [12.4000, 12.9000]。
#     旧实现用 out_start_slack=0.03 + out_dur_scale=0.8 凑出 [12.47, 12.87]，起点晚了 0.07s。
#
# 幅度/缓动也是从原版帧反解的（不是拍的）：
#   · 方法：把贴纸图层（画布大小 RGBA，静止帧 12.35s）的顶边带（y<360、alpha>0.6，共 50420 px）
#     当模板，在 12.30–12.55s 的每帧上只搜竖直位移 dy，取 MAE 最小
#     （tag=60fps，残差 4.5–11）。
#   · 实测 dy = 0/2/10/23/42/65/94/128/167（t = 12.400/12.417/…/12.533）；
#     拟合 dy = 9440·u²（u = t − 12.4000）→ 预测 2.7/10.3/23.6/42.3/65.6/94.4/129.2/166.5，
#     残差 ≤1.3px。线性拟合同一批点残差 ≥30px，指数 1.5/2.5 均明显变差 → 起点 12.4000、
#     缓动 **p²**（不是线性）。顶边 = 218 + dy：220/228/241/260 与原版逐帧量到的完全一致。
#   · 幅度：p = u/0.5 → dy = H·GAIN·p²，GAIN = 9440·0.25/1920 = 1.229（画布高等比；
#     12.467/12.533 两点分别得 1.229/1.223，取均值 1.226→取 1.229 与首帧吻合）。
STICKER_OUT_SLIDE = {
    "向下滑动": {"ux": 0.0, "uy": 1.0, "ease": 2.0, "gain": 1.229},
}


def sticker_state_fn(specs: List["AnimSpec"], seg_dur: float, canvas_w: int, canvas_h: int):
    """逐帧状态函数：出场滑动用草稿真值接管，其余动画原样走 animations.anim_state。

    返回 None = 没有需要接管的出场动画（调用方继续走 animations 的默认幅度表）。
    为什么接管而不是改标定：`downward-slide` 的旧标定（0.8×时长 + 0.03 尾隙 + 线性）
    是在错误的窗口上拟合出来的，真值窗口/曲线与它不相容；接管后只影响登记在
    STICKER_OUT_SLIDE 里的出场动画，入场动画（缩小/淡入…）一字未动。
    """
    from animations import AnimState, anim_state

    handled = [sp for sp in specs if sp.kind == "out" and sp.name in STICKER_OUT_SLIDE]
    if not handled:
        return None
    rest = [sp for sp in specs if not (sp.kind == "out" and sp.name in STICKER_OUT_SLIDE)]
    plan = []
    for sp in handled:
        k = STICKER_OUT_SLIDE[sp.name]
        dur_o = max(1e-6, float(sp.duration))
        # start 缺失 → 段尾倒数 duration；写了 start → 相对段起点的偏移（越界钳到段尾）
        t1 = (sp.start + dur_o) if sp.start is not None else seg_dur
        t1 = min(t1, seg_dur)
        plan.append((t1 - dur_o, dur_o, k))

    def _fn(t_local: float) -> "AnimState":
        st = anim_state(rest, t_local, seg_dur, canvas_w, canvas_h)
        for t0, dur_o, k in plan:
            p = (t_local - t0) / dur_o
            p = 0.0 if p < 0 else (1.0 if p > 1 else p)
            if p <= 0.0:
                continue
            w = p ** float(k["ease"])
            st.dx += float(k["ux"]) * canvas_w * float(k["gain"]) * w
            st.dy += float(k["uy"]) * canvas_h * float(k["gain"]) * w
        return st

    return _fn


@dataclass
class StickerLayer:
    path: Path          # 画布大小的 RGBA mov
    t_start: float
    dur: float
    name: str


def _run(cmd: List[str], timeout: int = 600) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"贴纸渲染失败: {' '.join(cmd[:6])}...\n{r.stderr[-1500:]}")


def paint_size(intrinsic_w: int, intrinsic_h: int, scale: float, canvas_w: int) -> tuple:
    """素材原生尺寸 × scale × 画布宽/720 → 渲染像素尺寸（偶数）。"""
    k = scale * canvas_w / STICKER_REF_WIDTH
    w = max(2, int(round(intrinsic_w * k / 2)) * 2)
    h = max(2, int(round(intrinsic_h * k / 2)) * 2)
    return w, h


def render_one_layer(
    seq_dir: Path, static: bool, seq_fps: float, out: Path,
    canvas_w: int, canvas_h: int, draw_w: int, draw_h: int,
    center_x: float, center_y: float, t_start: float, dur: float,
    out_fps: float, alpha_kf: Optional[List] = None,
) -> Path:
    """渲染一个贴纸段为画布尺寸 RGBA mov（时间轴已归零，叠的时候按 t_start 显隐）。"""
    x = int(round(center_x - draw_w / 2))
    y = int(round(center_y - draw_h / 2))
    # 贴纸常比画布还宽（例如手机边框贴纸渲染宽 1560 > 1080）：先算与画布的交集，
    # 裁掉画布外的部分，再 pad 到画布坐标上；直接拿越界坐标 pad 会把图层算歪。
    left = max(0, x)
    top = max(0, y)
    right = min(canvas_w, x + draw_w)
    bottom = min(canvas_h, y + draw_h)
    if right <= left or bottom <= top:
        raise RuntimeError(f"贴纸完全落在画布外: x={x} y={y} w={draw_w} h={draw_h}")
    crop_w, crop_h = right - left, bottom - top
    src_x, src_y = left - x, top - y
    pad_x, pad_y = left, top
    fps = seq_fps if (seq_fps and not static) else out_fps

    if static:
        inputs = ["-loop", "1", "-i", str(seq_dir / "f00000.png")]
    else:
        inputs = ["-framerate", f"{fps:g}", "-i", str(seq_dir / "f%05d.png")]

    chain = [
        f"[0:v]scale={draw_w}:{draw_h}:flags=lanczos,setsar=1,format=rgba[sc]",
        f"[sc]crop={crop_w}:{crop_h}:{src_x}:{src_y}[cr]",
        f"[cr]pad={canvas_w}:{canvas_h}:{pad_x}:{pad_y}:color=black@0,"
        f"fps={out_fps},format=rgba[pad]",
    ]
    last = "[pad]"
    if alpha_kf:
        sel = []
        for (t, a) in alpha_kf:
            sel.append(f"if(between(t,{t - 0.001:.4f},{t + 0.001:.4f}),{a:.4f},")
        expr = "".join(sel) + "1" + ")" * len(sel)
        chain.append(f"{last}fade=t=in:st=0:d=0.001:alpha=1,geq=a='alpha(X,Y)',format=rgba[ak]")
        last = "[ak]"
    chain.append(f"{last}trim=duration={dur:.4f},setpts=PTS-STARTPTS[vo]")
    _run(["ffmpeg", "-y", "-v", "error", *inputs,
          "-filter_complex", ";".join(chain), "-map", "[vo]",
          "-t", f"{dur:.4f}", "-r", f"{out_fps:g}",
          "-c:v", "qtrle", "-pix_fmt", "argb", str(out)])
    return out


def _layer_tag(draw_w: int, draw_h: int, cx: float, cy: float, dur: float, fps: float) -> str:
    """图层缓存指纹：几何/时长/帧率 + 脚本自身 sha1 前 8 位。

    只按文件名（st_01_010433.mov）缓存会让上一版算错的图层被永久复用——
    曾经出现过“素材解对了但叠上去还是全透明”的假象，真因就是陈旧缓存。
    """
    src = f"{draw_w}x{draw_h}@{cx:.1f},{cy:.1f}|{dur:.4f}|{fps:g}"
    try:
        src += "|" + hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:8]
    except OSError:
        pass
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:8]


def plan_sticker_layers(
    draft: dict, base_dir: Path, canvas_w: int, canvas_h: int,
    out_fps: float, total_sec: float, work: Path, report: dict,
    cache_root: Optional[Path] = None, depth: int = 0,
    animations_by_id: Optional[Dict[str, dict]] = None,
) -> List[StickerLayer]:
    """遍历 sticker 轨，逐段渲染成画布大小 RGBA 图层。缺素材的按 missing 计数、不中断。"""
    st_by_id = {m.get("id"): m for m in (draft.get("materials", {}).get("stickers") or [])}
    layers: List[StickerLayer] = []
    miss = 0
    skipped = 0
    (work / "stickers").mkdir(parents=True, exist_ok=True)
    for tr in draft.get("tracks") or []:
        if tr.get("type") != "sticker":
            continue
        for k, seg in enumerate(tr.get("segments") or []):
            mat = st_by_id.get(seg.get("material_id")) or {}
            tgt = seg.get("target_timerange") or {}
            t_start = float(tgt.get("start") or 0) / US
            dur = float(tgt.get("duration") or 0) / US
            if dur <= 0.01:
                continue
            d = resolve_sticker_dir(mat.get("path") or "", mat.get("resource_id") or "",
                                    cache_root=cache_root) if cache_root else \
                resolve_sticker_dir(mat.get("path") or "", mat.get("resource_id") or "")
            if not d:
                miss += 1
                continue
            try:
                seq = sticker_sequence(d)
            except Exception as exc:  # 素材在但解析不了，也按 missing 记，别整批崩
                report.setdefault("sticker_notes", []).append(f"{mat.get('name')}: {exc}")
                miss += 1
                continue
            clip = seg.get("clip") or {}
            sc = clip.get("scale") or {}
            if isinstance(sc, (int, float)):
                sc_x = sc_y = float(sc)
            else:
                sc_x = float(sc.get("x") or 1.0)
                sc_y = float(sc.get("y") or 1.0)
            tr_ = clip.get("transform") or {}
            tx = float(tr_.get("x") or 0.0)
            ty = float(tr_.get("y") or 0.0)
            # 原生尺寸：从第一帧读
            from PIL import Image
            with Image.open(seq.frames_dir / "f00000.png") as im:
                iw, ih = im.size
            draw_w, draw_h = paint_size(iw, ih, sc_x, canvas_w)
            if abs(sc_x - sc_y) > 1e-3:
                draw_h = max(2, int(round(ih * sc_y * canvas_w / STICKER_REF_WIDTH / 2)) * 2)
            cx = canvas_w / 2 + tx * canvas_w / 2
            cy = canvas_h / 2 - ty * canvas_h / 2
            rot = float(clip.get("rotation") or 0.0)
            if abs(rot) > 0.5:
                skipped += 1
                report.setdefault("sticker_notes", []).append(f"{mat.get('name')}: 旋转 {rot:.1f}° 未实现")
            if depth == 0:
                pass
            out = work / "stickers" / (f"st_{k:02d}_{int(t_start * 1000):06d}_{_layer_tag(draw_w, draw_h, cx, cy, dur, out_fps)}.mov")
            specs = parse_animations(seg, animations_by_id or {})
            if specs:
                # 带入场/出场动画：只能 PIL 逐帧（ffmpeg 的 scale 不能逐帧改尺寸）
                tag = _layer_tag(draw_w, draw_h, cx, cy, dur, out_fps) + "a"
                out = work / "stickers" / (f"st_{k:02d}_{int(t_start * 1000):06d}_{tag}.mov")
                if not out.is_file() or out.stat().st_size < 1024:
                    try:
                        render_animated_layer(
                            sorted(seq.frames_dir.glob("f*.png")),
                            (canvas_w, canvas_h), (draw_w, draw_h), (cx, cy),
                            t_start, dur, out_fps, specs, out,
                            state_fn=sticker_state_fn(specs, dur, canvas_w, canvas_h))
                    except Exception as exc:
                        report.setdefault("sticker_notes", []).append(
                            f"{mat.get('name')}: 动画渲染失败回退静态（{exc}）")
                        specs = []
                if specs and depth == 0:
                    report["animated_stickers"] = report.get("animated_stickers", 0) + 1
                    report.setdefault("sticker_anims", []).extend(summarize(specs))
            if not specs and (not out.is_file() or out.stat().st_size < 1024):
                render_one_layer(seq.frames_dir, seq.static, seq.fps, out,
                                 canvas_w, canvas_h, draw_w, draw_h, cx, cy,
                                 t_start, dur, out_fps)
            layers.append(StickerLayer(out, t_start, dur, str(mat.get("name") or "")))
    if depth == 0:
        if miss:
            report["unrealized"]["sticker_missing"] = report["unrealized"].get("sticker_missing", 0) + miss
        if skipped:
            report["unrealized"]["sticker_rotation"] = report["unrealized"].get("sticker_rotation", 0) + skipped
    layers.sort(key=lambda l: l.t_start)
    return layers


def overlay_stickers(video_in: Path, layers: List[StickerLayer], video_out: Path,
                     out_fps: float, total_sec: float) -> None:
    """把贴纸图层按时间窗叠到成片上（一次 ffmpeg 搞定，不重编码音频）。"""
    if not layers:
        if Path(video_in) != Path(video_out):
            video_out.write_bytes(Path(video_in).read_bytes())
        return
    inputs = ["-i", str(video_in)]
    for l in layers:
        inputs += ["-i", str(l.path)]
    chain = []
    cur = "[0:v]"
    for i, l in enumerate(layers, start=1):
        # overlay 的第二路输入按**绝对时间**取帧：必须把图层从 0 平移到它的时间窗口，
        # 否则 10.4s 才开始的那层在 10.4s 时早已 EOF（eof_action=pass 直接跳过 → 什么都不叠）。
        chain.append(f"[{i}:v]setpts=PTS+{l.t_start:.4f}/TB,format=rgba[l{i}]")
        nxt = f"[v{i}]"
        en = f"between(t,{l.t_start:.4f},{l.t_start + l.dur:.4f})"
        chain.append(f"{cur}[l{i}]overlay=0:0:format=auto:eof_action=pass:enable='{en}'{nxt}")
        cur = nxt
    graph = ";".join(chain)
    _run(["ffmpeg", "-y", "-v", "error", *inputs,
          "-filter_complex", graph, "-map", cur,
          "-map", "0:a?" , "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
          "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
          "-t", f"{total_sec:.3f}", str(video_out)], timeout=1800)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="单测：把一个贴纸目录按给定几何渲染成图层")
    ap.add_argument("--path", required=True)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--tx", type=float, default=0.0)
    ap.add_argument("--ty", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--out", default="/tmp/sticker_layer.mov")
    args = ap.parse_args()
    d = resolve_sticker_dir(args.path)
    if not d:
        raise SystemExit("素材目录不存在")
    seq = sticker_sequence(d)
    from PIL import Image
    with Image.open(seq.frames_dir / "f00000.png") as im:
        iw, ih = im.size
    W, H = 1080, 1920
    dw, dh = paint_size(iw, ih, args.scale, W)
    cx = W / 2 + args.tx * W / 2
    cy = H / 2 - args.ty * H / 2
    out = render_one_layer(seq.frames_dir, seq.static, seq.fps, Path(args.out),
                           W, H, dw, dh, cx, cy, 0.0, args.duration, 60.0)
    print(f"素材 {iw}x{ih} → 渲染 {dw}x{dh} @({cx:.0f},{cy:.0f})  → {out}")


if __name__ == "__main__":
    main()
