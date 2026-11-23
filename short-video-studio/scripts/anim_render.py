#!/usr/bin/env python3
"""
带动画的图层渲染：把「贴纸 / 文字 / 卡片」渲染成画布尺寸的 RGBA 序列（逐帧施加动画）。

为什么非要用 PIL 逐帧
──────────────────────
ffmpeg 的 `scale` 滤镜**输出尺寸不能逐帧变化**（实测 `scale=w='trunc(...)':eval=frame`
在 t=0 与 t=0.95 尺寸完全相同，静默不变），`zoompan` 带 alpha 时也不按输入帧推进。
所以「缩小 / 放大 / 日出」这类带缩放的入场动画只能逐帧合成。

坐标约定
────────
剪映：`center_x = W/2 + tx·W/2`，`center_y = H/2 - ty·H/2`（y 正 = 向上）。
动画修正量来自 `animations.anim_state()`：scale / dx / dy（屏幕坐标，下正）/ alpha / blur。

对外接口
────────
    out = render_animated_layer(
        frames=[png1, png2, ...],          # 源帧（静态图就一张，自动循环）
        canvas=(1080, 1920),
        base_size=(w, h), base_center=(cx, cy),
        t_start=10.433, dur=2.467, fps=60.0,
        anims=[AnimSpec("in", "缩小", 0.5), AnimSpec("out", "向下滑动", 0.5)],
        out=Path("/tmp/layer.mov"),
    )
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageFilter

try:  # 允许脚本目录被直接加入 sys.path 的用法，也允许包内相对导入
    from animations import AnimSpec, AnimState, anim_state
except Exception:  # pragma: no cover
    from .animations import AnimSpec, AnimState, anim_state  # type: ignore

ALPHA_SKIP = 0.004     # 全透明帧不落盘，只留痕
BLUR_MAX = 40.0


@dataclass
class LayerFrames:
    frames_dir: Path
    count: int
    pattern: str = "f%05d.png"


def _load(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def render_animated_layer(
    frames: Sequence[Path],
    canvas: Tuple[int, int],
    base_size: Tuple[int, int],
    base_center: Tuple[float, float],
    t_start: float,
    dur: float,
    fps: float,
    anims: Optional[List[AnimSpec]] = None,
    out: Optional[Path] = None,
    loop_frames: bool = True,
    src_fps: Optional[float] = None,
    state_fn=None,
    ctx: Optional[str] = None,
) -> Tuple[Path, int]:
    """逐帧合成一个带动画的图层，返回 (视频路径, 落地帧数)。

    输出：画布尺寸的 RGBA 序列（ProRes 4444 / qtrle mov），时间轴已归零，
    叠加时用 `setpts=PTS+t_start/TB` 平移到它的时间窗（见 sticker_render.overlay_stickers）。

    src_fps   源序列播放帧率。缺省按贴纸那套（动图 16fps / 静态图循环）。视频素材必须
              显式给出（按输出帧率抽帧），否则 4 秒的底片会被当成 16fps 循环图。
    state_fn  可选：t_local -> AnimState 的自定义曲线（草稿关键帧那种）。给了就完全接管，
              不再看 anims。
    ctx       幅度表：None = 贴纸标定，"layer" = 视频叠加/文字层（见 animations.CTX_CALIBRATION）。
    """
    W, H = canvas
    bw, bh = base_size
    cx, cy = base_center
    fps = float(fps) if fps and fps > 0 else 30.0
    n = max(1, int(round(dur * fps)))
    src = [_load(p) for p in frames]
    if not src:
        raise ValueError("render_animated_layer: 没有源帧")
    anims = anims or []

    tmp = Path(out).with_suffix("") if out else None
    if out is None:
        raise ValueError("render_animated_layer: 需要 out 路径")
    seq_dir = tmp.parent / (tmp.name + "_frames")
    seq_dir.mkdir(parents=True, exist_ok=True)
    for old in seq_dir.glob("f*.png"):
        old.unlink()

    written = 0
    visible = False
    for i in range(n):
        t_local = i / fps
        st: AnimState = (state_fn(t_local) if state_fn
                         else (anim_state(anims, t_local, dur, W, H, ctx=ctx) if anims else AnimState()))
        # 源帧按「源帧率」推进：贴纸动图 16fps、静态图恒一张、视频素材=输出帧率
        sfps = float(src_fps) if src_fps else _src_fps(len(src), dur)
        si = 0 if len(src) == 1 else (
            int(t_local * sfps) % len(src) if loop_frames else min(len(src) - 1, int(t_local * sfps))
        )
        # 必须 copy：src[si] 是共享图像，putalpha/resize 会就地改动它——单帧源被 alpha=0
        # 的第一帧改过一次后，后面每一帧都拿到全透明图（整层凭空消失）。
        im = src[si].copy()
        if im.size != (bw, bh):
            im = im.resize((max(1, bw), max(1, bh)), Image.LANCZOS)
        sx, sy = st.sx(), st.sy()
        if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4:
            w2 = max(1, int(round(bw * sx)))
            h2 = max(1, int(round(bh * sy)))
            im = im.resize((w2, h2), Image.LANCZOS)
        if st.blur > 0.05:
            im = im.filter(ImageFilter.GaussianBlur(radius=min(BLUR_MAX, st.blur)))
        if st.alpha < 0.999:
            a = im.split()[3].point(lambda v: int(v * max(0.0, st.alpha)))
            im.putalpha(a)
        px = int(round(cx - im.width / 2 + st.dx))
        py = int(round(cy - im.height / 2 + st.dy))
        # 与画布求交后再贴，避免整幅超大图层（如 1560 宽的贴纸）把画布算歪
        left, top = max(0, px), max(0, py)
        right, bottom = min(W, px + im.width), min(H, py + im.height)
        if right <= left or bottom <= top:
            frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        else:
            frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            crop = im.crop((left - px, top - py, left - px + (right - left), top - py + (bottom - top)))
            frame.paste(crop, (left, top), crop)
        full_alpha = frame.split()[3]
        if full_alpha.getextrema()[1] > int(255 * ALPHA_SKIP):
            visible = True
        # 不能跳帧落盘：序号一断，后面的帧全部前移，整层的时间就对不上了。
        # 全透明的帧也照旧写（qtrle 压得极小），最后用 visible 判断整层是不是空壳。
        frame.save(seq_dir / ("f%05d.png" % written))
        written += 1

    if written == 0 or not visible:
        raise RuntimeError("render_animated_layer: 所有帧都是全透明，拒绝产出空壳")

    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps:g}",
         "-i", str(seq_dir / "f%05d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", "-r", f"{fps:g}", str(out)],
        check=True, capture_output=True,
    )
    return out, written


def _src_fps(count: int, dur: float) -> float:
    """源序列的播放帧率：贴纸动图按 16fps 播（infoSticker.lua 的默认），静态图无所谓。"""
    return 16.0 if count > 1 else 1.0


def _self_test() -> None:
    from PIL import ImageDraw
    base = Path("/tmp/anim_layer_test")
    base.mkdir(parents=True, exist_ok=True)
    p = base / "src.png"
    im = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((0, 0, 199, 299), fill=(255, 0, 0, 255))
    im.save(p)
    out = base / "layer.mov"
    # 缩小入场 0.5s：第 0 帧应比末帧大
    _, n = render_animated_layer([p], (1080, 1920), (200, 300), (540, 960), 0.0, 1.0, 30.0,
                                 [AnimSpec("in", "缩小", 0.5)], out)
    assert n > 10, n
    print("anim_render.py self-test OK", n)


if __name__ == "__main__":
    _self_test()
