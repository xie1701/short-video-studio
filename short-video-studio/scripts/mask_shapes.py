#!/usr/bin/env python3
"""剪映「遮罩」alpha 场的 1:1 复刻（纯函数，无副作用）。

真值来自本机剪映特效包 lumi_rectangle_mask 的 shader：
  .../Cache/effect/82432980/02b8999168d121538a98ea59127483ef/amazingfeature/
  effects/lumi_rectangle_mask/{lua/Process.lua, xshader/picture.vert, xshader/picture.frag}

数学链路（逐行对着源码抄，不猜）：

1) Process.lua onStart/onUpdate —— config → uniform
     u_center      = (posX, posY)                 -- 默认 (0,0)
     u_size        = (scaleX, scaleY)             -- 默认 (1,1)；草稿 config 的 width/height 直接落这里
     u_roundCorner = roundCorner                  -- 默认 0（圆角）
     if u_size.x * u_aspect >= u_size.y:  r = u_size.y * u_roundCorner
     else:                                r = u_size.x * u_aspect * u_roundCorner
     u_rightTop    = (u_size.x * u_aspect, u_size.y)
     u_circleCenter= (u_rightTop.x - r, u_rightTop.y - r)
     u_aspect      = 输出纹理宽/高                 -- 即图层自身宽高比
   Process.lua setEffectAttr:
     feather → u_diff = feather * 6        （lua 里的 6 倍；草稿 config 到 property 还差一层归一化，
                                            实际落地系数标定为 FEATHER_GAIN=0.6，见该常量注释）
     rz/rotationZ → u_rotate = (360 - (-value)) * 2π / 360
     invert → u_invert = 1.0/0.0

2) picture.vert —— 像素 → 遮罩空间的半平面坐标
     sampIn = ((uv0.x-0.5)*2 - u_center.x) * u_aspect , ((uv0.y-0.5)*2 - u_center.y)
     sampIn = rotate(sampIn, u_rotate)              -- 默认 u_rotate=0 → 恒等
     decay  = sqrt(1 - (mod(u_rotate, π/2)/(π/4) - 1)^2); decay = sqrt(sqrt(decay))
     即 u_rotate=0 时 decay=0（羽化不扩张），45° 时 decay=1（羽化 ±0.005 扩张，可忽略）。

3) picture.frag —— 由「射线到圆角矩形边界的比例」算 alpha
     samp = abs(sampIn)
     if samp.y*u_circleCenter.x >= u_rightTop.y*samp.x: alpha = samp.y/u_size.y        -- 上/下直边
     elif samp.y*u_rightTop.x <= u_circleCenter.y*samp.x: alpha = samp.x/(u_aspect*u_size.x)  -- 左/右直边
     else:  a=|cc|²-r² ; b=-2·dot(samp,cc) ; c=|samp|²
            alpha = (-b-sqrt(b²-4ac))/(2a)          -- 角部：射线参数 t（边界处 t=1）
     alpha = clamp(smoothstep((1-decay*0.005)-u_diff, (1+decay*0.005)+u_diff, alpha), 0, 1)
     if u_invert < 1.0: alpha = 1.0 - alpha         -- 默认 u_invert=0 → 取反
   ⇒ 最终 alpha：遮罩内 1、外 0，羽化带跨 alpha 1±u_diff（相对遮罩半尺寸的**比例**）。

4) frag 输出 vec4(min(color+alpha,1), 1.0)：合成 pass 里 u_albedo 是黑，故输出即 alpha 灰度 =
   遮罩灰度图。剪映合成时把该灰度当 alpha 乘到图层上（外面露纯黑/透下层），即 alpha 乘性、非加法。
   本机实测原版羽化带外像素恒为 0,0,0、带内亮度 = 原画 × alpha，与乘性一致。

口径（剪映遮罩 config 的 width/height/centerX/centerY 单位）：
  width  = 遮罩**满宽**占画布宽的比例   （shader 里 u_size.x 是半宽，单位 = 半画布宽）
  height = 遮罩**满高**占画布高的比例   （u_size.y 为半高，单位 = 半画布高）
  centerY 正 = 向上（与 clip.transform.y 同向）；遮罩中心屏上位置 = 0.5 - centerY/2
  圆角半径 r 以 u_size 单位计 ⇒ 屏幕圆角半径(高方向比例) = r * 0.5
  圆形遮罩 = roundCorner 取 1.0 的圆角矩形（宽高按 aspect 配平后即真圆）
"""

from __future__ import annotations

import numpy as np

# Process.lua setEffectAttr 里写的是 `u_diff = feather * 6`；但草稿 config 的 feather 与 lua 收到的
# property 不同量级（引擎在 config→property 这一跳还有一层归一化，约 1/10）。用原版成片反解：
#   * 全帧最小二乘（取原画复刻 × alpha 拟合原版帧）：config.feather=0.1 的 4 个遮罩得 u_diff=
#     0.0575~0.0725（矩形 0.0575-0.06 / 胶囊 roundCorner=1 0.055-0.0625 / 圆形 0.07-0.0725）；
#   * 只在真在变的羽化过渡带（上/左/右共 88k 像素）拟合：最优 0.056（残差 1.32-1.55 级），
#     0.06 为 1.40-1.64，0.03 为 4.0、0.10 为 4.7（后两者明显变差）。
# 故有效系数 = 6 × 0.1 = 0.6（与 lua 的 6 同向，中间差一个 1/10 归一化）。
# 若照搬 lua 的 6（u_diff=0.6）羽化带会比原版宽 10 倍：可见区直接溢出画面，全帧 MAE 4.1 → 9.4。
# 反解方法：mask_alpha(u_diff_override=d) 遍历 d，对 |orig - gain·rep·alpha| 取最小（gain 同时拟合）。
FEATHER_GAIN = 0.6

# 草稿里圆形遮罩 config 不带 roundCorner；剪映 UI 的「圆形」= 圆角拉满。
CIRCLE_ROUND_CORNER = 1.0

# 线性遮罩（lumi_linear_mask）的 feather 是**直接**当 u_diff 用（Process.lua: _setEffectAttr("u_diff", value)），
# 与矩形/圆形那个 ×6 不是一套；同机实测（style_21 13.37-15.62s）先用源码值。
SUPPORTED_MASK_TYPES = ("rectangle", "circle", "ellipse", "line", "linear")


def _f(cfg: dict, key: str, default: float) -> float:
    v = cfg.get(key)
    if v is None:
        return float(default)
    return float(v)


def shape_params(cfg: dict, aspect: float, feather_gain: float | None = None,
                 u_diff_override: float | None = None) -> dict:
    """config → Process.lua 的 uniform 组（onStart/onUpdate 的算法，逐行照抄）。

    feather_gain / u_diff_override 只给标定与回归用（标定脚本扫 feather_gain 找原版真值）。
    """
    rtype = str(cfg.get("resource_type") or "").lower()
    is_circle = rtype in ("circle", "ellipse")
    size_x = _f(cfg, "width", 1.0)
    size_y = _f(cfg, "height", 1.0)
    cx = _f(cfg, "centerX", _f(cfg, "posX", 0.0))
    cy = _f(cfg, "centerY", _f(cfg, "posY", 0.0))
    default_rc = CIRCLE_ROUND_CORNER if is_circle else 0.0
    round_corner = _f(cfg, "roundCorner", default_rc)
    feather = _f(cfg, "feather", 0.0)
    rotation_deg = _f(cfg, "rotation", 0.0)  # 草稿里没有就不转

    if size_x * aspect >= size_y:
        radius = size_y * round_corner
    else:
        radius = size_x * aspect * round_corner

    # setEffectAttr("rz") → u_rotate = (360 - (-value)) * 2π / 360
    u_rotate = (360.0 - (-rotation_deg)) * 2.0 * np.pi / 360.0
    return {
        "is_circle": is_circle,
        "u_center": (cx, cy),
        "u_size": (size_x, size_y),
        "u_aspect": float(aspect),
        "u_roundCorner": round_corner,
        "u_rightTop": (size_x * aspect, size_y),
        "u_circleCenter": (size_x * aspect - radius, size_y - radius),
        "u_circleRadius": radius,
        "u_diff": (float(u_diff_override) if u_diff_override is not None
                   else feather * (FEATHER_GAIN if feather_gain is None else float(feather_gain))),
        "u_rotate": float(u_rotate),
        "u_invert": 1.0 if cfg.get("invert") else 0.0,
        "feather": feather,
    }


def linear_params(cfg: dict) -> dict:
    """lumi_linear_mask 的 uniform：orientation 只由 rotation 决定（见 Process.lua setEffectAttr）。"""
    rotation_deg = _f(cfg, "rotation", 0.0)
    u_rotate = -rotation_deg * 2.0 * np.pi / 360.0
    return {
        "u_center": (_f(cfg, "centerX", _f(cfg, "posX", 0.0)), _f(cfg, "centerY", _f(cfg, "posY", 0.0))),
        "u_orient": (-np.sin(u_rotate), np.cos(u_rotate)),
        "u_rotate": float(u_rotate),
        "u_diff": _f(cfg, "feather", 0.0),
        "u_invert": 1.0 if cfg.get("invert") else 0.0,
    }


def is_supported(resource_type: str) -> bool:
    return str(resource_type or "").lower() in SUPPORTED_MASK_TYPES


def _smoothstep(e0, e1, x):
    """GLSL smoothstep；e1==e0（feather=0 的硬边遮罩）时退化为阶跃，不产生 0/0。"""
    den = e1 - e0
    if abs(float(den)) < 1e-12:
        return (np.asarray(x) >= e0).astype(np.float32)
    t = np.clip((x - e0) / den, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _linear_alpha(w: int, h: int, cfg: dict, aspect: float, ow: int, oh: int,
                  u_diff_override: float | None = None) -> np.ndarray:
    """lumi_linear_mask/picture.frag：alpha = smoothstep(-0.005·decay-u_diff, +u_diff, dot(orient, samp))。

    与矩形/圆形相反：默认（invert=0）**不取反**，即线的正侧可见、负侧透明。
    """
    p = linear_params(cfg)
    if u_diff_override is not None:
        p["u_diff"] = float(u_diff_override)
    xs = (np.arange(ow, dtype=np.float32) + 0.5) / ow
    ys = (np.arange(oh, dtype=np.float32) + 0.5) / oh
    tcy = (1.0 - ys)[:, None]
    tcx = xs[None, :]
    sx = ((tcx - 0.5) * 2.0 - p["u_center"][0]) * aspect
    sy = (tcy - 0.5) * 2.0 - p["u_center"][1]
    ox, oy = p["u_orient"]
    n = np.hypot(ox, oy) or 1.0
    raw = (sx * ox + sy * oy) / n
    rot = p["u_rotate"]
    z = np.mod(rot, np.pi / 2.0) / (np.pi / 4.0) - 1.0 if abs(rot) > 1e-9 else -1.0
    decay = float(np.sqrt(np.sqrt(max(0.0, 1.0 - z * z))))
    alpha = np.clip(_smoothstep(-0.005 * decay - p["u_diff"], p["u_diff"], raw), 0.0, 1.0)
    if p["u_invert"] > 0.0:
        alpha = 1.0 - alpha
    return alpha.astype(np.float32)


def mask_alpha(w: int, h: int, cfg: dict, aspect: float | None = None,
               out_w: int | None = None, out_h: int | None = None,
               feather_gain: float | None = None,
               u_diff_override: float | None = None) -> np.ndarray:
    """剪映遮罩 alpha 场，返回 float32[H,W]，值域 0..1（1=遮罩内可见）。

    w, h     : 图层（渲染纹理）像素尺寸 —— 决定 u_aspect 与 uv 归一化口径
    cfg      : 草稿 materials.common_mask[*]["config"]（含 resource_type）
    aspect   : u_aspect，默认 w/h（Process.lua 取输出纹理宽高比）
    out_w/h  : 采样网格尺寸，默认 w/h；给别的值就等于在另一分辨率上按同一 uv 采样
               （像素中心 (x+0.5)/out_w、y 方向按 GL 约定翻成向上）
    """
    ow = int(out_w or w)
    oh = int(out_h or h)
    asp = float(aspect) if aspect else float(w) / float(h)
    rtype = str(cfg.get("resource_type") or "").lower()
    if rtype in ("line", "linear"):
        return _linear_alpha(w, h, cfg, asp, ow, oh, u_diff_override=u_diff_override)
    p = shape_params(cfg, asp, feather_gain=feather_gain, u_diff_override=u_diff_override)

    xs = (np.arange(ow, dtype=np.float32) + 0.5) / ow
    ys = (np.arange(oh, dtype=np.float32) + 0.5) / oh
    # picture.vert: uv0.y 自下而上；输出数组自上而下 → 翻一次
    tcy = (1.0 - ys)[:, None]
    tcx = xs[None, :]

    sx = ((tcx - 0.5) * 2.0 - p["u_center"][0]) * asp
    sy = (tcy - 0.5) * 2.0 - p["u_center"][1]
    rot = p["u_rotate"]
    if abs(rot) > 1e-9:
        cos_r, sin_r = np.cos(rot), np.sin(rot)
        sx, sy = cos_r * sx - sin_r * sy, sin_r * sx + cos_r * sy
    ux = np.abs(sx)
    uy = np.abs(sy)

    size_x, size_y = p["u_size"]
    cc_x, cc_y = p["u_circleCenter"]
    rt_x, rt_y = p["u_rightTop"]
    radius = p["u_circleRadius"]

    with np.errstate(divide="ignore", invalid="ignore"):
        cond_a = uy * cc_x >= rt_y * ux          # 上/下直边
        cond_b = uy * rt_x <= cc_y * ux          # 左/右直边
        a_val = uy / max(size_y, 1e-12)
        b_val = ux / max(asp * size_x, 1e-12)

        qa = cc_x * cc_x + cc_y * cc_y - radius * radius
        qb = -2.0 * (ux * cc_x + uy * cc_y)
        qc = ux * ux + uy * uy
        if abs(qa) < 1e-8:
            c_val = np.where(np.abs(qb) < 1e-8, 100.0, -qc / np.where(np.abs(qb) < 1e-8, 1.0, qb))
        else:
            disc = qb * qb - 4.0 * qa * qc
            c_val = (-qb - np.sqrt(np.where(disc > 0.0, disc, 0.0))) / (2.0 * qa)

    alpha = np.where(cond_a, a_val, np.where(cond_b, b_val, c_val)).astype(np.float32)
    alpha = np.nan_to_num(alpha, nan=0.0, posinf=1e6, neginf=0.0)

    # picture.vert 的 decay：手动旋转时把羽化带按 ±0.005*decay 张开
    if abs(rot) > 1e-9:
        z = np.mod(rot, np.pi / 2.0) / (np.pi / 4.0) - 1.0
        decay = np.sqrt(np.sqrt(max(0.0, 1.0 - z * z)))
    else:
        decay = 0.0
    lo = (1.0 - decay * 0.005) - p["u_diff"]
    hi = (1.0 + decay * 0.005) + p["u_diff"]
    alpha = np.clip(_smoothstep(lo, hi, alpha), 0.0, 1.0)

    if p["u_invert"] < 1.0:
        alpha = 1.0 - alpha
    return alpha.astype(np.float32)


def bake_over_black(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """主轨口径：遮罩区外为纯黑且不透明 → 画面×alpha（一张不透明整帧）。"""
    a = alpha[..., None] if alpha.ndim == 2 else alpha
    return np.clip(np.asarray(rgb, dtype=np.float32) * a, 0.0, 255.0)


def save_alpha_png(alpha: np.ndarray, path) -> str:
    """alpha(0..1) → 8bit 灰度 PNG（给 ffmpeg alphamerge / format=gray 用）。"""
    from PIL import Image

    g = np.clip(np.round(np.asarray(alpha, dtype=np.float32) * 255.0), 0, 255).astype(np.uint8)
    Image.fromarray(g, mode="L").save(path)
    return str(path)


def _self_test() -> int:
    """自测：形状/羽化/位置的定量断言 + 预览图落 /tmp 供肉眼核。"""
    import json
    from pathlib import Path

    W, H = 1080, 1920
    rect = {"resource_type": "rectangle", "width": 0.844098883572568,
            "height": 0.578498803827751, "centerY": 0.249846422397772,
            "feather": 0.1, "roundCorner": 0.1}
    pill = {"resource_type": "rectangle", "width": 0.833333333333333,
            "height": 0.46875, "centerY": 0.380702836004932,
            "feather": 0.1, "roundCorner": 1.0}
    circ = {"resource_type": "circle", "width": 0.833333333333333,
            "height": 0.46875, "centerY": 0.380208333333333, "feather": 0.1}
    out = Path("/tmp/mask_preview")
    out.mkdir(parents=True, exist_ok=True)
    ok = True

    # 1) 矩形：宽度/高度/中心 与 config 对得上（用 alpha=0.5 等值线，smoothstep 中点 = 几何边界）
    a = mask_alpha(W, H, rect)
    assert a.shape == (H, W) and a.dtype == np.float32, (a.shape, a.dtype)
    half = 0.5
    row_in = a[int(round(0.25 * H)), :]
    cols = np.where(row_in >= half)[0]
    print(f"[rect] alpha>=0.5 列 {cols.min()}..{cols.max()}  → 宽 {((cols.max()-cols.min())/W):.4f} "
          f"(期望≈{rect['width']:.4f})")
    col_in = a[:, int(W / 2)]
    rows = np.where(col_in >= half)[0]
    print(f"[rect] alpha>=0.5 行 {rows.min()}..{rows.max()}  → 高 {((rows.max()-rows.min())/H):.4f} "
          f"(期望≈{rect['height']:.4f})  中心 y={((rows.min()+rows.max())/2/H):.4f} "
          f"(期望≈{0.5-rect['centerY']/2:.4f})")
    exp_w = rect["width"]
    if abs((cols.max() - cols.min()) / W - exp_w) > 0.01:
        print(f"  !! 宽度偏差 {(cols.max()-cols.min())/W - exp_w:+.4f}")
        ok = False
    exp_top = 0.5 - rect["centerY"] / 2 - rect["height"] / 2
    exp_bot = 0.5 - rect["centerY"] / 2 + rect["height"] / 2
    if abs(rows.min() / H - exp_top) > 0.01 or abs(rows.max() / H - exp_bot) > 0.01:
        print(f"  !! 上下边偏差 {rows.min()/H - exp_top:+.4f}/{rows.max()/H - exp_bot:+.4f}")
        ok = False
    print(f"[rect] alpha 中心 {a[H//2, W//2]:.3f}  角 {a[10,10]:.3f}  最上 {a[0, W//2]:.3f}")

    # 2) 圆形：真圆（水平直径 ≈ 垂直直径，同一像素口径）
    ac = mask_alpha(W, H, circ)
    r_row = ac[int(round((0.5 - circ["centerY"] / 2) * H)), :] >= half
    c_col = ac[:, int(W / 2)] >= half
    dw = np.where(r_row)[0][-1] - np.where(r_row)[0][0]
    dh = np.where(c_col)[0][-1] - np.where(c_col)[0][0]
    print(f"[circle] 水平直径 {dw:.0f}px / 垂直直径 {dh:.0f}px → 圆度 {dw/dh:.3f} "
          f"(期望≈{circ['width']*W:.0f}px)")
    if abs(dw / dh - 1.0) > 0.02:
        ok = False

    # 3) 羽化带宽度应与 u_diff 成正比（u_diff=0.6 时过渡带占半尺寸 60%）
    soft = mask_alpha(W, H, {**rect, "feather": 0.02})
    col_soft = soft[:, int(W / 2)]
    band_hard = np.sum((col_in > 0.02) & (col_in < 0.98))
    band_soft = np.sum((col_soft > 0.02) & (col_soft < 0.98))
    print(f"[feather] 过渡带像素数 feather=0.1 → {band_hard} ; feather=0.02 → {band_soft}")
    if not band_hard > band_soft:
        ok = False

    # 4) invert 取反
    inv = mask_alpha(W, H, {**rect, "invert": True})
    print(f"[invert] 中心 {inv[H//2, W//2]:.3f}（期望 0）")
    if abs(inv[H//2, W//2]) > 5e-3:
        ok = False

    # 4b) feather=0（草稿照片卡片遮罩不带 feather）→ 硬边，不得出 NaN
    hard = mask_alpha(W, H, {"resource_type": "rectangle", "width": 1.0, "height": 1.0,
                             "roundCorner": 0.1})
    print(f"[hard] NaN={int(np.isnan(hard).sum())} 中心 {hard[H//2, W//2]:.3f} 角 {hard[0,0]:.3f}"
          f" / u_diff={shape_params({'width':1.0,'height':1.0}, 1080/1920)['u_diff']:.4f}")
    if np.isnan(hard).any() or hard[H//2, W//2] < 0.99 or hard[0, 0] > 0.01:
        ok = False

    # 4c) 羽化系数标定值：config.feather=0.1 → u_diff≈0.06（原版帧反解结果）
    ud = shape_params(rect, W / H)["u_diff"]
    print(f"[feather] config.feather=0.1 → u_diff={ud:.4f}（标定 0.0575~0.0725）")
    if not (0.05 <= ud <= 0.08):
        ok = False

    # 4d) 线性遮罩（lumi_linear_mask）：沿 rotation 定义的朝向做 smoothstep，默认不取反
    line = {"resource_type": "line", "width": 0.28, "centerX": -0.2358, "centerY": -0.2162,
            "rotation": 57.3860705560057, "feather": 0.660219580407963}
    al = mask_alpha(W, H, line)
    print(f"[line] 左上 {al[0,0]:.3f} 中心 {al[H//2, W//2]:.3f} 右上 {al[0,-1]:.3f} 左下 {al[-1,0]:.3f}"
          f" （朝向 = rotation 57.4° → 右上最大）")
    if not (al[0, -1] > al[H // 2, W // 2] > al[-1, 0]) or np.isnan(al).any():
        ok = False

    # 5) 预览图：三张 alpha 灰度 + 一张叠加在原版帧上的效果图
    save_alpha_png(a, out / "rect_alpha.png")
    save_alpha_png(ac, out / "circle_alpha.png")
    save_alpha_png(mask_alpha(W, H, pill), out / "pill_alpha.png")
    save_alpha_png(mask_alpha(W, H, line), out / "line_alpha.png")
    try:
        from PIL import Image

        src = Path("/tmp/maskprobe/rep_2.0.png")
        if src.is_file():
            rgb = np.asarray(Image.open(src).convert("RGB"), dtype=np.float32)
            baked = bake_over_black(rgb, a).astype(np.uint8)
            Image.fromarray(baked).save(out / "rect_on_replica_2.0.png")
            print(f"预览已写 {out}")
    except Exception as exc:  # 预览不是硬要求
        print(f"预览跳过: {exc}")
    print(json.dumps({"ok": ok}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
