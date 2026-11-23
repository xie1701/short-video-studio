#!/usr/bin/env python3
"""
[花字入场动效原语库 v1]

把 style_spectrum_library.json 里 display_text_spec.anims_top 的剪映（JianYing）动效原名，
归一成一批可复用、可跨风格轮换的渲染原语，供 render_template_overlays.render_animated_overlay_mov
按 template_blueprints.json 的 overlay_style.anim_primitives 顺序轮换调用。

本次新增 6 类原语（剪映原名 → 原语键）：
  1. in:弹幕滚动 / loop:弹幕滚动          → barrel_scroll     横向匀速穿越画面，尾部轻微倾斜
  2. 镜像翻转                             → mirror_flip       Y 轴翻转（scale X 由 -1 → 1）
  3. 横向模糊（in:向左模糊 / 向右模糊 II）→ horizontal_blur   沿水平方向的拖影 / 运动模糊
  4. 快速翻页                             → page_flip         竖向裁切推进 + 轻微透视缩放
  5. in:折叠 / 展开                       → fold_expand       竖向压扁展开
  6. 滑动出入（向左/右滑动、向上滑出、向下露出、随机上升、滚出）→ slide_in_out 平移进出 + 缓动

另有 2 类缩放细分子型（缩小 / 放大）与 6 类沿用既有实现的别名（渐显 / 弹入 / 擦除 /
打字机 / 金属扫光 / 模糊显影 / 分段翻入），保证库里每个动效原名都能落到一个原语上。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from common import maybe_reexec_in_venv

# 被脚本导入时也要在 PIL 缺席前切到专用 venv；REPL / 测试进程内不重启宿主进程。
if __name__ == "__main__" or str(sys.argv[0]).endswith(".py"):
    maybe_reexec_in_venv()

from PIL import Image, ImageDraw, ImageFilter

# ── 原语键（渲染分发的模式名） ──────────────────────────────────────────────

BARREL_SCROLL = "barrel_scroll"
MIRROR_FLIP = "mirror_flip"
HORIZONTAL_BLUR = "horizontal_blur"
PAGE_FLIP = "page_flip"
FOLD_EXPAND = "fold_expand"
SLIDE_IN_OUT = "slide_in_out"
SCALE_IN = "scale_in"
SCALE_UP_IN = "scale_up_in"

# 由本模块提供帧生成器的模式（其余模式仍走 render_template_overlays 内既有实现）
MOTION_MODES = frozenset(
    {
        BARREL_SCROLL,
        MIRROR_FLIP,
        HORIZONTAL_BLUR,
        PAGE_FLIP,
        FOLD_EXPAND,
        SLIDE_IN_OUT,
        SCALE_IN,
        SCALE_UP_IN,
    }
)

# 各模式在画布上需要预留的对称留白（像素），用于平移 / 旋转 / 拖影时的出血裁剪
MOTION_PAD = {
    BARREL_SCROLL: 120,
    MIRROR_FLIP: 16,
    HORIZONTAL_BLUR: 60,
    PAGE_FLIP: 16,
    FOLD_EXPAND: 24,
    SLIDE_IN_OUT: 140,
    SCALE_IN: 32,
    SCALE_UP_IN: 32,
}

# ── 中文原语名 → 模式键（template_blueprints.json 的 anim_primitives 用中文名书写） ──

PRIMITIVES: Dict[str, str] = {
    "弹幕滚动": BARREL_SCROLL,
    "镜像翻转": MIRROR_FLIP,
    "横向模糊": HORIZONTAL_BLUR,
    "快速翻页": PAGE_FLIP,
    "折叠展开": FOLD_EXPAND,
    "滑动出入": SLIDE_IN_OUT,
    "缩小": SCALE_IN,
    "放大": SCALE_UP_IN,
    "渐显": "depth_behind_person",
    "弹入": "bounce_smash",
    "擦除": "wipe_reveal",
    "打字机": "typewriter_char",
    "金属扫光": "shimmer_3d_gold",
    "模糊显影": "ink_bloom",
    "分段翻入": "number_flip_trio",
    "三拍砸屏": "store_stagger_burst",
}

# ── 剪映动效原名 → 中文原语名（覆盖全库 60 个真实动效名） ──────────────────────

ANIM_NAME_TO_PRIMITIVE: Dict[str, str] = {
    # 缩放
    "in:缩小": "缩小",
    "in:缩小 II": "缩小",
    "in:放大": "放大",
    "in:轻微放大": "放大",
    "in:生长": "放大",
    "in:生长 II": "放大",
    "in:吸入": "缩小",
    # 淡入 / 上浮
    "in:渐显": "渐显",
    "in:日出": "渐显",
    "in:冰雪飘动": "渐显",
    "out:渐隐": "渐显",
    "out:发光模糊": "模糊显影",
    # 滚动 / 平移
    "in:滚入": "快速翻页",
    "out:滚出": "快速翻页",
    "in:圆柱体滚动": "快速翻页",
    "out:向下弹出": "快速翻页",
    "in:随机上升": "滑动出入",
    "in:向右露出": "滑动出入",
    "in:向下露出": "滑动出入",
    "in:向上露出": "滑动出入",
    "in:向右滑动": "滑动出入",
    "in:向左滑动": "滑动出入",
    "in:向上滑动": "滑动出入",
    "in:向下滑动": "滑动出入",
    "in:向右缓入": "滑动出入",
    "in:向右集合": "滑动出入",
    "loop:飘起": "滑动出入",
    # 水平方向拖影
    "in:向左模糊": "横向模糊",
    "in:向右模糊 II": "横向模糊",
    "in:色散拖影": "横向模糊",
    # 遮罩揭幕
    "in:开幕": "擦除",
    "in:向右擦除": "擦除",
    "in:激光雕刻": "擦除",
    # 逐字
    "in:逐字显影": "打字机",
    "in:打字光标": "打字机",
    "in:新年打字机": "打字机",
    # 折叠
    "in:折叠": "折叠展开",
    "in:收拢": "折叠展开",
    # 模糊显影
    "in:雪光模糊": "模糊显影",
    "in:发光模糊": "模糊显影",
    "in:模糊发光": "模糊显影",
    "in:流光扩散": "模糊显影",
    "in:水墨晕开": "模糊显影",
    "in:晕开": "模糊显影",
    "out:消散": "模糊显影",
    # 弹入
    "in:跃进": "弹入",
    "in:波浪弹入": "弹入",
    "in:向上弹入": "弹入",
    "in:弹簧": "弹入",
    "in:呐喊声波": "弹入",
    "out:炸开 II": "弹入",
    "loop:扩音器": "弹入",
    "loop:晃动": "弹入",
    # 弹幕
    "loop:弹幕滚动": "弹幕滚动",
    "in:弹幕滚动": "弹幕滚动",
}


def primitive_of(anim_name: str) -> Optional[str]:
    """单个剪映动效原名 → 中文原语名（未知返回 None）。"""
    return ANIM_NAME_TO_PRIMITIVE.get(str(anim_name or "").strip())


def resolve_mode(primitive: str, default: str = "bounce_smash") -> str:
    """中文原语名（或已归一化的模式键）→ 渲染分发用的模式键。"""
    p = str(primitive or "").strip()
    if not p:
        return default
    if p in PRIMITIVES:
        return PRIMITIVES[p]
    if p in MOTION_MODES or p in (
        "depth_behind_person",
        "bounce_smash",
        "wipe_reveal",
        "typewriter_char",
        "shimmer_3d_gold",
        "ink_bloom",
        "number_flip_trio",
        "store_stagger_burst",
    ):
        return p
    return default


# ── 缓动 ──────────────────────────────────────────────────────────────────


def _ease_out_cubic(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return 1.0 - (1.0 - u) ** 3


def _ease_out_back(u: float) -> float:
    u = max(0.0, min(1.0, u))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * ((u - 1.0) ** 3) + c1 * ((u - 1.0) ** 2)


# ── 图层工具 ──────────────────────────────────────────────────────────────


def _with_alpha(im: Image.Image, mul: float) -> Image.Image:
    if mul >= 0.995:
        return im
    r, g, b, a = im.split()
    a = a.point(lambda v: int(v * max(0.0, min(1.0, mul))))
    return Image.merge("RGBA", (r, g, b, a))


def _paste_clipped(dst: Image.Image, src: Image.Image, x: int, y: int) -> None:
    """把 src 在整数坐标 (x, y) 贴进 dst，负坐标/出血部分自动裁剪，不抛异常。"""
    x, y = int(round(x)), int(round(y))
    dx0, dy0 = max(0, x), max(0, y)
    sx0, sy0 = max(0, -x), max(0, -y)
    w = min(src.width - sx0, dst.width - dx0)
    h = min(src.height - sy0, dst.height - dy0)
    if w <= 0 or h <= 0:
        return
    dst.alpha_composite(src.crop((sx0, sy0, sx0 + w, sy0 + h)), dest=(dx0, dy0))


def _scaled(im: Image.Image, sx: float, sy: Optional[float] = None, alpha: float = 1.0) -> Image.Image:
    sy = sx if sy is None else sy
    nw = max(2, int(round(im.width * max(0.02, sx))))
    nh = max(2, int(round(im.height * max(0.02, sy))))
    return _with_alpha(im.resize((nw, nh), Image.Resampling.BILINEAR), alpha)


def _center_paste(frame: Image.Image, im: Image.Image, cx: int, cy: int) -> None:
    _paste_clipped(frame, im, cx - im.width // 2, cy - im.height // 2)


def _horizontal_trail(im: Image.Image, amount: float, direction: str, spread: float = 44.0) -> Image.Image:
    """水平方向运动模糊：多份横向位移副本叠加 + 轻微高斯模糊，amount 越大拖影越长。"""
    amt = max(0.0, min(1.0, amount))
    if amt <= 0.02:
        return im
    sign = 1 if direction == "right" else -1
    pad = int(round(spread * amt)) + 2
    wide = Image.new("RGBA", (im.width + pad * 2, im.height), (0, 0, 0, 0))
    wide.alpha_composite(im, dest=(pad, 0))
    acc = Image.new("RGBA", wide.size, (0, 0, 0, 0))
    steps = 7
    for i in range(steps):
        k = i / float(steps - 1)
        dx = int(round((k - 0.5) * 2.0 * pad * amt)) * sign
        strength = 0.62 * (1.0 - abs(k - 0.5) * 1.3)
        if strength <= 0.03:
            continue
        _paste_clipped(acc, _with_alpha(wide, strength), dx, 0)
    acc = acc.filter(ImageFilter.GaussianBlur(radius=1.2 + 2.4 * amt))
    return acc


# ── 帧生成器 ──────────────────────────────────────────────────────────────


def _frame_barrel_scroll(base: Image.Image, cw: int, ch: int, ox: int, oy: int,
                        fps: int, dur: float, direction: str = "left") -> Callable[[int], bytes]:
    """弹幕滚动：整条横向匀速穿越（线性、无缓动），尾部带 3.5° 轻微倾斜。"""
    tilted = base.rotate(3.5, resample=Image.Resampling.BICUBIC, expand=True)
    travel_sec = max(0.3, min(0.7, dur * 0.5))
    # 起手时右侧约 18% 已探入画面，避免整段空帧，再匀速平移到落位点
    start_x = cw - int(tilted.width * 0.18)
    end_x = ox + (base.width - tilted.width) // 2
    cy = oy + base.height // 2

    def _frame(fi: int) -> bytes:
        t = fi / float(fps)
        u = min(1.0, t / travel_sec)
        x = start_x + (end_x - start_x) * u
        y = cy - tilted.height // 2
        fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        _paste_clipped(fr, tilted, x, y)
        return fr.tobytes()

    return _frame


def _frame_mirror_flip(base: Image.Image, cw: int, ch: int, ox: int, oy: int,
                       fps: int, dur: float) -> Callable[[int], bytes]:
    """镜像翻转：Y 轴翻转，scale X 由 -1 连续变到 1（前 0.42s）。"""
    act = max(0.24, min(0.42, dur * 0.6))
    cx = ox + base.width // 2
    cy = oy + base.height // 2

    def _frame(fi: int) -> bytes:
        t = fi / float(fps)
        fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if t >= act:
            _center_paste(fr, base, cx, cy)
            return fr.tobytes()
        e = _ease_out_cubic(t / act)
        sx = -1.0 + 2.0 * e
        alpha = min(1.0, 0.45 + 0.55 * (t / act))
        im = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if sx < 0 else base
        _center_paste(fr, _scaled(im, abs(sx), 1.0, alpha), cx, cy)
        return fr.tobytes()

    return _frame


def _frame_horizontal_blur(base: Image.Image, cw: int, ch: int, ox: int, oy: int,
                           fps: int, dur: float, direction: str = "left") -> Callable[[int], bytes]:
    """横向模糊：沿水平方向的拖影收敛为清晰字（向左/向右模糊 II/色散拖影）。"""
    act = max(0.24, min(0.46, dur * 0.62))
    cx = ox + base.width // 2
    cy = oy + base.height // 2

    def _frame(fi: int) -> bytes:
        t = fi / float(fps)
        fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if t >= act:
            _center_paste(fr, base, cx, cy)
            return fr.tobytes()
        u = t / act
        im = _horizontal_trail(base, 1.0 - u, direction)
        _center_paste(fr, _with_alpha(im, min(1.0, 0.55 + 0.45 * u)), cx, cy)
        return fr.tobytes()

    return _frame


def _frame_page_flip(base: Image.Image, cw: int, ch: int, ox: int, oy: int,
                     fps: int, dur: float) -> Callable[[int], bytes]:
    """快速翻页：自上而下的竖向裁切推进 + 前沿暗色纸影，整体带轻微透视缩放。"""
    act = max(0.24, min(0.42, dur * 0.6))
    cx = ox + base.width // 2

    def _frame(fi: int) -> bytes:
        t = fi / float(fps)
        fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if t >= act:
            _center_paste(fr, base, cx, oy + base.height // 2)
            return fr.tobytes()
        e = _ease_out_cubic(t / act)
        vis_h = max(2, int(round(base.height * e)))
        sx = 1.0 + 0.12 * (1.0 - e)
        nw = max(2, int(round(base.width * sx)))
        nh = max(2, int(round(vis_h * sx)))
        part = base.crop((0, 0, base.width, vis_h)).resize((nw, nh), Image.Resampling.BILINEAR)
        px = cx - nw // 2
        py = oy
        _paste_clipped(fr, part, px, py)
        d = ImageDraw.Draw(fr)
        edge_y = py + nh
        if 0 <= edge_y < ch:
            d.line([(max(0, px), edge_y), (min(cw - 1, px + nw), edge_y)], fill=(0, 0, 0, 150), width=3)
        return fr.tobytes()

    return _frame


def _frame_fold_expand(base: Image.Image, cw: int, ch: int, ox: int, oy: int,
                       fps: int, dur: float) -> Callable[[int], bytes]:
    """折叠 / 展开：竖向由一条线压扁展开（scale Y 0.03 → 1，带弹性过冲）。"""
    act = max(0.22, min(0.4, dur * 0.55))
    cx = ox + base.width // 2
    cy = oy + base.height // 2

    def _frame(fi: int) -> bytes:
        t = fi / float(fps)
        fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if t >= act:
            _center_paste(fr, base, cx, cy)
            return fr.tobytes()
        u = t / act
        sy = max(0.03, min(1.08, _ease_out_back(u)))
        sx = 1.0 + 0.10 * (1.0 - min(1.0, u))
        _center_paste(fr, _scaled(base, sx, sy, min(1.0, 0.4 + 0.6 * u)), cx, cy)
        return fr.tobytes()

    return _frame


def _frame_slide_in_out(base: Image.Image, cw: int, ch: int, ox: int, oy: int,
                        fps: int, dur: float, direction: str = "up") -> Callable[[int], bytes]:
    """滑动出入：平移进出 + 缓动（向左/右滑动、向上滑出、向下露出、随机上升、滚出）。"""
    act = max(0.24, min(0.44, dur * 0.6))
    cx = ox + base.width // 2
    cy = oy + base.height // 2
    travel = float(200 if direction in ("up", "down") else 260)

    def _frame(fi: int) -> bytes:
        t = fi / float(fps)
        fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if t >= act:
            _center_paste(fr, base, cx, cy)
            return fr.tobytes()
        e = _ease_out_cubic(t / act)
        off = travel * (1.0 - e)
        dx = off if direction == "left" else (-off if direction == "right" else 0.0)
        dy = off if direction == "up" else (-off if direction == "down" else 0.0)
        alpha = min(1.0, 0.35 + 0.65 * e)
        _center_paste(fr, _with_alpha(base, alpha), cx + int(round(dx)), cy + int(round(dy)))
        return fr.tobytes()

    return _frame


def _frame_scale(base: Image.Image, cw: int, ch: int, ox: int, oy: int,
                 fps: int, dur: float, start_scale: float) -> Callable[[int], bytes]:
    """缩小（1.32→1.0）/ 放大（0.58→1.0）：弹性缩放落位。"""
    act = max(0.2, min(0.36, dur * 0.5))
    cx = ox + base.width // 2
    cy = oy + base.height // 2

    def _frame(fi: int) -> bytes:
        t = fi / float(fps)
        fr = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if t >= act:
            _center_paste(fr, base, cx, cy)
            return fr.tobytes()
        u = t / act
        sc = start_scale + (1.0 - start_scale) * _ease_out_back(u)
        _center_paste(fr, _scaled(base, max(0.2, min(1.36, sc)), None, min(1.0, 0.3 + 0.7 * u)), cx, cy)
        return fr.tobytes()

    return _frame


_DIRECTIONS = {
    HORIZONTAL_BLUR: "left",
    SLIDE_IN_OUT: "up",
}


def make_frame_fn(
    mode: str,
    base_img: Image.Image,
    cw: int,
    ch: int,
    ox: int,
    oy: int,
    fps: int,
    dur: float,
    direction: Optional[str] = None,
) -> Optional[Callable[[int], bytes]]:
    """按模式键返回 frame(fi) -> RGBA bytes 的帧生成器（未知模式返回 None）。"""
    d = direction or _DIRECTIONS.get(mode, "up")
    if mode == BARREL_SCROLL:
        return _frame_barrel_scroll(base_img, cw, ch, ox, oy, fps, dur, "left")
    if mode == MIRROR_FLIP:
        return _frame_mirror_flip(base_img, cw, ch, ox, oy, fps, dur)
    if mode == HORIZONTAL_BLUR:
        return _frame_horizontal_blur(base_img, cw, ch, ox, oy, fps, dur, d)
    if mode == PAGE_FLIP:
        return _frame_page_flip(base_img, cw, ch, ox, oy, fps, dur)
    if mode == FOLD_EXPAND:
        return _frame_fold_expand(base_img, cw, ch, ox, oy, fps, dur)
    if mode == SLIDE_IN_OUT:
        return _frame_slide_in_out(base_img, cw, ch, ox, oy, fps, dur, d)
    if mode == SCALE_IN:
        return _frame_scale(base_img, cw, ch, ox, oy, fps, dur, 1.32)
    if mode == SCALE_UP_IN:
        return _frame_scale(base_img, cw, ch, ox, oy, fps, dur, 0.58)
    return None
