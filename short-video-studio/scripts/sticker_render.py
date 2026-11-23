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
    from animations import parse_animations, summarize, anim_state
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

# ---------- 翻转 / 旋转：草稿字段与符号都是反解出来的 ----------
# 原来什么：`clip.flip` / `clip.rotation` **完全没读**（旋转只记一笔「未实现」计数）。
#   草稿里 flip 的键名是 `horizontal`/`vertical`（不是 h/v），rotation 是角度。
# 真值反解（style_21 13.367–15.617s 复合片段里那条红箭头，sub sticker：
#   素材 540×540、scale 0.333651、transform(0.115721, -0.031941)、
#   flip.horizontal=true、rotation=-77.3588）：
#   · 原版 15.1s 帧取红像素（R>140,G<90,B<90）掩模，质心 (557.7, 1003.8)；
#   · 对本素材试 2×2 组合，按红掩模 IoU 对原版：
#       flip=False, PIL rotate(+77.4) IoU 0.028；flip=False, -77.4 → 0.037；
#       flip=True,  +77.4 → **0.926**（质心 555.7,1014.6）；flip=True, -77.4 → 0.067。
# 所以：**先水平镜像，再按 `−rotation` 度逆时针旋转（PIL rotate 正=逆时针）**。
# 宽度/顺序：**先缩放到显示尺寸（paint_size），再翻转、再旋转**（旋转只是把已经画好的
# 贴纸转一下，不改变它的 scale），旋转用 expand 保留整个图像，中心不动。
STICKER_ROT_EPS = 0.5      # 小于这个角度视为没旋转（抖动的零点几度不值得多两趟重采样）


def _flip_hv(clip: dict) -> tuple:
    """`clip.flip` → (h, v)。两种键名都认：剪映真字段是 horizontal/vertical。"""
    f = (clip or {}).get("flip") or {}
    return (bool(f.get("horizontal", f.get("h"))),
            bool(f.get("vertical", f.get("v"))))


def _transform_frames(frames, out_dir: Path, draw_w: int, draw_h: int,
                      hflip: bool, vflip: bool, rot: float) -> list:
    """把贴纸帧「缩放 → 翻转 → 旋转(expand)」预烘成一串 PNG，返回新帧列表。

    为什么用 PIL 预烘而不是 ffmpeg 滤镜：旋转后要按新的包围盒裁到画布上，
    ffmpeg 里得把 crop/pad 的坐标按 rotw()/roth() 重算一遍；PIL 的 rotate(expand=True)
    直接把尺寸与中心摆好，后面的 crop/pad 逻辑一个字不用改（把新尺寸当 draw_w/h 传即可）。
    逐帧成本可以接受：贴纸段通常只有一张静图，带序列的也很短（15 帧 @16fps）。
    """
    from PIL import Image
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, p in enumerate(frames):
        with Image.open(p) as im:
            im = im.convert("RGBA").resize((max(1, draw_w), max(1, draw_h)), Image.LANCZOS)
            if hflip:
                im = im.transpose(Image.FLIP_LEFT_RIGHT)
            if vflip:
                im = im.transpose(Image.FLIP_TOP_BOTTOM)
            if abs(rot) > STICKER_ROT_EPS:
                im = im.rotate(-rot, expand=True, resample=Image.BICUBIC)
            q = out_dir / ("f%05d.png" % i)
            im.save(q)
            out.append(q)
    return out


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
            # 出场同时**淡出**，曲线与位移同一条（alpha = 1 − w）——这是逐帧量出来的，
            # 不是「顺手加个淡出」：style_21 手机屏幕边框（10.4333+2.4667，出「向下滑动」
            # 0.5s、无 start → 窗口 [12.4000, 12.9000]）用「原版 − 主轨底片」在贴纸不透明
            # 像素（alpha>0.8，40–56 万像素/帧）上做最小二乘，得到原版相对我们（alpha=1）的
            # 透明度比：  12.20→0.99、12.35→0.99、12.45→0.98、12.55→0.89、12.65→0.73、
            #            12.75→0.49、12.85→贴纸已出画
            # p = (t−12.4)/0.5 → 1−p² = 0.96/0.91/0.75/0.51，与实测 0.89/0.73/0.49 逐点相差
            # ≤0.03。旧实现只位移不淡出，于是 12.4–12.9s 我们一直是一块**不透明的实心手机框**
            # 压在画面上（原版是半透明的虚影）——12.5s 这一帧字幕带 6.89 级、12.77s 带内 11.6 级，
            # 判它「不对」靠的就是这个，而不是位移量。
            st.alpha *= max(0.0, 1.0 - w)
        return st

    return _fn


# 注：这里曾经有一个 `alpha_compensated()` 包装，把 render_animated_layer 写进图层的
# **alpha²** 用开方硬顶回来。根因（`frame.paste(crop, (x,y), crop)` 把自身 alpha 当掩模、
# 于是 alpha 被平方）已于 2026-11-24 在 anim_render.render_animated_layer 里改成
# `frame.alpha_composite(crop, dest=(left, top))`；补偿必须删掉，否则会把已经正确的
# alpha 再开一次方（0.5 → 0.71，入场反而变亮、曲线仍错）。

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


def _layer_tag(draw_w: int, draw_h: int, cx: float, cy: float, dur: float, fps: float,
               extra: str = "") -> str:
    """图层缓存指纹：几何/时长/帧率 + 脚本自身 sha1 前 8 位。

    只按文件名（st_01_010433.mov）缓存会让上一版算错的图层被永久复用——
    曾经出现过“素材解对了但叠上去还是全透明”的假象，真因就是陈旧缓存。

    extra 是给「动画图层」的额外盐：动画曲线住在 animations.py / anim_render.py 里，
    这两个文件改了上面那几个数字完全不变，不把它们的指纹拌进来就会拿旧帧充新帧。
    """
    src = f"{draw_w}x{draw_h}@{cx:.1f},{cy:.1f}|{dur:.4f}|{fps:g}|{extra}"
    try:
        src += "|" + hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:8]
        import animations as _am
        import anim_render as _ar
        src += "|" + hashlib.sha1(Path(_am.__file__).read_bytes()).hexdigest()[:8]
        src += "|" + hashlib.sha1(Path(_ar.__file__).read_bytes()).hexdigest()[:8]
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
            hflip, vflip = _flip_hv(clip)
            # 翻转/旋转：先把帧烘成「已缩放+已翻转+已旋转」的序列，再当普通贴纸叠。
            # 旋转用 expand，包围盒会变大——所以 draw_w/draw_h 要换成新帧尺寸，
            # 而 cx/cy 不变（旋转是绕贴纸自身的中心转的）。
            frames_dir = seq.frames_dir
            static = seq.static
            if hflip or vflip or abs(rot) > STICKER_ROT_EPS:
                xd = work / "stickers" / (f"xf_{k:02d}_{int(t_start * 1000):06d}_"
                                         f"{_layer_tag(draw_w, draw_h, cx, cy, dur, out_fps, extra=f'f{int(hflip)}{int(vflip)}{rot:.2f}')}")
                if not (xd / "f00000.png").is_file():
                    _transform_frames(sorted(seq.frames_dir.glob("f*.png")), xd,
                                      draw_w, draw_h, hflip, vflip, rot)
                frames_dir = xd
                static = True
                with Image.open(xd / "f00000.png") as im:
                    draw_w, draw_h = im.size
                if depth == 0:
                    report.setdefault("sticker_notes", []).append(
                        f"{mat.get('name')}: 翻转 h={int(hflip)} v={int(vflip)} / 旋转 {rot:.1f}°（已实现）")
            if depth == 0:
                pass
            out = work / "stickers" / (f"st_{k:02d}_{int(t_start * 1000):06d}_{_layer_tag(draw_w, draw_h, cx, cy, dur, out_fps)}.mov")
            specs = parse_animations(seg, animations_by_id or {})
            if specs:
                # 带入场/出场动画：只能 PIL 逐帧（ffmpeg 的 scale 不能逐帧改尺寸）
                # 指纹必须拌上动画参数 + 两个渲染脚本的源码：动画曲线改了但几何没改时，
                # 只用几何指纹会命中旧图层（「改了不生效」的最常见真因）。
                _sig = ";".join(f"{sp.kind}:{sp.name}:{sp.duration:.4f}:{sp.start}" for sp in specs)
                tag = _layer_tag(draw_w, draw_h, cx, cy, dur, out_fps, extra=_sig) + "a"
                out = work / "stickers" / (f"st_{k:02d}_{int(t_start * 1000):06d}_{tag}.mov")
                if not out.is_file() or out.stat().st_size < 1024:
                    try:
                        _sfn = sticker_state_fn(specs, dur, canvas_w, canvas_h)
                        if _sfn is None:
                            def _sfn(t_local, _sp=specs, _d=dur):
                                return anim_state(_sp, t_local, _d, canvas_w, canvas_h)
                        render_animated_layer(
                            sorted(frames_dir.glob("f*.png")),
                            (canvas_w, canvas_h), (draw_w, draw_h), (cx, cy),
                            t_start, dur, out_fps, specs, out,
                            state_fn=_sfn)
                    except Exception as exc:
                        report.setdefault("sticker_notes", []).append(
                            f"{mat.get('name')}: 动画渲染失败回退静态（{exc}）")
                        specs = []
                if specs and depth == 0:
                    report["animated_stickers"] = report.get("animated_stickers", 0) + 1
                    report.setdefault("sticker_anims", []).extend(summarize(specs))
            if not specs and (not out.is_file() or out.stat().st_size < 1024):
                render_one_layer(frames_dir, static, seq.fps, out,
                                 canvas_w, canvas_h, draw_w, draw_h, cx, cy,
                                 t_start, dur, out_fps)
            # 可见时长 = min(段长, 素材能铺满的长度)。
            #   · 静态图（单帧）：素材无限循环 → 整段；
            #   · 序列贴纸（如「手势贴纸-赞」15 帧 @16fps = 0.9375s）：鲉映放完就**不再画**，
            #     草稿段长 1.05s 里最后 0.11s 是空的。我们旧实现把它当整段铺，overlay 默认
            #     eof_action=repeat 又把末帧拷贝到段尾 → 逐帧看起来「退场晚了」。
            #     实测原版 4.7167s（帧 283）末帧可见、4.7333s 已消失，正好 = 3.7833+0.9375。
            vis = dur
            if not seq.static and seq.fps:
                n_src = len(list(seq.frames_dir.glob("f*.png")))
                if n_src > 1:
                    vis = min(dur, n_src / float(seq.fps))
            layers.append(StickerLayer(out, t_start, vis, str(mat.get("name") or "")))
    if depth == 0:
        if miss:
            report["unrealized"]["sticker_missing"] = report["unrealized"].get("sticker_missing", 0) + miss
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
