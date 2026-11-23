#!/usr/bin/env python3
"""
按帧渲染叠加图层：带 keyframe 的图片 / 文字 / 贴纸层用 PIL 逐帧合成，输出 RGBA 帧序列。

为什么不用 ffmpeg 滤镜
──────────────────────
剪映的 keyframe 会同时改 `scale`（卡片末段缩小退场、巨字从 1.0 缩到 0.01）和位置，
而 ffmpeg 的 `scale` 滤镜**不允许输出尺寸逐帧变化**（实测 t=0 与 t=1 尺寸相同，静默不变）；
`zoompan` 带 alpha 也不按输入帧推进。所以这类动画只能自己按帧算——好在这些都是静图/文字，
PIL 逐帧合成既精确又快（一张画布尺寸的 RGBA 帧几毫秒）。

几何真值（从原版帧反解，见 reports 记录）
──────────────────────────────────────────
- 显示高 = 素材原生高 × clip.scale；显示宽 = 显示高 × 画布宽/画布高（按画布比例裁宽）。
  例：主页素材 1080×1440、scale 0.2341 → 显示 189×336（原版实测 189×336），
  不是 1920×0.2341=449（旧实现大 34%）。
- 段级「模糊」作用在**缩放后的画布像素**上：`effects_adjust_blur=0.5` 实测 σ≈4px。
- 位置：center_x = W/2 + x·W/2，center_y = H/2 − y·H/2（剪映 y 正=向上）。

用法
────
    layer = AnimLayer(image=pil_rgba, natural=(w, h), base_scale=0.2341,
                      base_pos=(0.13, 0.67), t_dur=2.9,
                      scale_kf=[(0.0, 0.2341), (2.217, 0.2341), (2.9, 0.01)],
                      pos_kf=[(0.0, 0.13, 0.67), (2.9, 0.20, 0.72)],
                      alpha_kf=[(0.0, 1.0), (2.9, 0.0)], blur_sigma=4.0)
    n = render_layer_seq(layer, 1080, 1920, 60, Path("/tmp/layer_frames"))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageFilter


@dataclass
class AnimLayer:
    image: Image.Image                      # RGBA 原图
    natural: Tuple[int, int]                # (w, h) 原生尺寸
    t_dur: float                            # 段时长（秒，图层本地时间）
    base_scale: float = 1.0
    base_pos: Tuple[float, float] = (0.0, 0.0)   # transform 单位（-1..1）
    scale_kf: Optional[Sequence[Tuple[float, float]]] = None
    pos_kf: Optional[Sequence[Tuple[float, float, float]]] = None
    alpha_kf: Optional[Sequence[Tuple[float, float]]] = None
    alpha_static: float = 1.0
    blur_sigma: float = 0.0                 # 显示尺寸下的 σ（px）
    crop_to_canvas_aspect: bool = True


def _interp(kf: Sequence[Tuple], t: float) -> Tuple:
    """分段线性插值；首点前保持、末点后保持（剪映 keyframe 语义）。"""
    if not kf:
        raise ValueError("空 keyframe")
    if t <= kf[0][0]:
        return kf[0][1:]
    if t >= kf[-1][0]:
        return kf[-1][1:]
    for i in range(1, len(kf)):
        t0, t1 = kf[i - 1][0], kf[i][0]
        if t <= t1:
            span = max(1e-6, t1 - t0)
            r = (t - t0) / span
            return tuple(a + (b - a) * r for a, b in zip(kf[i - 1][1:], kf[i][1:]))
    return kf[-1][1:]


def display_size(natural: Tuple[int, int], scale: float,
                 canvas: Tuple[int, int]) -> Tuple[int, int]:
    """显示尺寸：高 = 原生高 × scale，宽 = 高 × 画布宽/画布高。"""
    nw, nh = natural
    cw, ch = canvas
    h = max(2, int(round(nh * scale)))
    w = max(2, int(round(h * cw / ch)))
    return w, h


def _prepare_source(layer: AnimLayer, canvas: Tuple[int, int]) -> Image.Image:
    """按画布比例横向裁切源图（cover，按高适配），保留最大可用细节。"""
    im = layer.image.convert("RGBA")
    if not layer.crop_to_canvas_aspect:
        return im
    cw, ch = canvas
    w, h = im.size
    target_ratio = cw / ch
    cur = w / h
    if abs(cur - target_ratio) < 1e-4:
        return im
    if cur > target_ratio:          # 太宽 → 裁宽
        new_w = int(round(h * target_ratio))
        x0 = (w - new_w) // 2
        return im.crop((x0, 0, x0 + new_w, h))
    new_h = int(round(w / target_ratio))  # 太高 → 裁高
    y0 = (h - new_h) // 2
    return im.crop((0, y0, w, y0 + new_h))


def render_layer_seq(layer: AnimLayer, canvas_w: int, canvas_h: int,
                     fps: float, out_dir: Path, t_start: float = 0.0) -> int:
    """逐帧渲染成画布大小的 RGBA PNG 序列（f00000.png …），返回帧数。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f*.png"):
        old.unlink()
    src = _prepare_source(layer, (canvas_w, canvas_h))
    n = max(1, int(round(layer.t_dur * fps)))
    written = 0
    for i in range(n):
        t = i / fps
        sc = _interp(layer.scale_kf, t)[0] if layer.scale_kf else layer.base_scale
        if layer.pos_kf:
            px, py = _interp(layer.pos_kf, t)
        else:
            px, py = layer.base_pos
        al = _interp(layer.alpha_kf, t)[0] if layer.alpha_kf else layer.alpha_static
        if al <= 0.004 or sc <= 0.0005:
            continue                     # 全透明帧直接跳过，省磁盘
        dw, dh = display_size(layer.natural, sc, (canvas_w, canvas_h))
        if dw < 2 or dh < 2:
            continue
        frame = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        im = src.resize((dw, dh), Image.LANCZOS)
        if layer.blur_sigma > 0.25:
            rgb = im.convert("RGB").filter(ImageFilter.GaussianBlur(layer.blur_sigma))
            im = Image.merge("RGBA", (*rgb.split(), im.split()[3]))
        if al < 0.999:
            a = im.split()[3].point(lambda v, k=al: int(v * k))
            im.putalpha(a)
        cx = canvas_w / 2 + px * canvas_w / 2
        cy = canvas_h / 2 - py * canvas_h / 2
        frame.paste(im, (int(round(cx - dw / 2)), int(round(cy - dh / 2))), im)
        frame.save(out_dir / ("f%05d.png" % i))
        written += 1
    return written


def main() -> None:
    import argparse
    from PIL import Image as _I
    ap = argparse.ArgumentParser(description="按帧渲染一个叠加图层（自测用）")
    ap.add_argument("--image", required=True)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--tx", type=float, default=0.0)
    ap.add_argument("--ty", type=float, default=0.0)
    ap.add_argument("--blur", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=1.0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--out", default="/tmp/layer_frames")
    args = ap.parse_args()
    im = _I.open(args.image).convert("RGBA")
    layer = AnimLayer(image=im, natural=im.size, t_dur=args.duration,
                      base_scale=args.scale, base_pos=(args.tx, args.ty),
                      blur_sigma=args.blur)
    n = render_layer_seq(layer, 1080, 1920, args.fps, Path(args.out))
    dw, dh = display_size(im.size, args.scale, (1080, 1920))
    print(f"{n} 帧 → {args.out}（显示尺寸 {dw}x{dh}）")


if __name__ == "__main__":
    main()
