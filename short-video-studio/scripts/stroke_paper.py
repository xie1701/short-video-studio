#!/usr/bin/env python3
"""剪映「撕纸描边」business 效果 → numpy 1:1 移植（纯函数，无 I/O 副作用）。

真值不是猜的：本机缓存里有这套效果的**完整 AmazingFeature 工程**，
路径由调用方用仓库既有的缓存发现逻辑（`render_replica_from_draft._discover_root("cache")`）给出：

    <cache>/effect/<resource_id>/<hash>/AmazingFeature/
        main.scene                     实体内序（= 渲染链顺序）
        material/{getInPut,blur0..5,noise,blurX,blurY}.material   uniform 绑定 + 默认值
        xshader/{...}.frag              GLSL 源码（本模块逐行照抄）
        image/zi1.png                  720x960 白纸纸纹（环带颜色）
        image/peopleTex.png            720x960 做旧灰纹理（内部 soft-light）
        lua/ImageBusinessSlider.lua    三个滑杆 → uniform 的映射

渲染链（由 main.scene 实体顺序 + 各 material 的 `mattingmaskEJ` 绑定定死）：
    getInPut → blur0 → blur1 → blur2 → blur3 → blur4 → blur5 → noise → blurX → blurY
    RT 依次：myInputTex → mid → oriMatting → mid → oriMatting → mid → upMatting
             → outputTex → NewScreenRT → 最终输出

    getInPut : gl_FragColor = vec4(oriCol.rgb, oriMatting)          alpha = 抠像蒙版
    blur0    : 竖向 7 抽头加权（0.20/0.19/0.17/0.15/0.13/0.11/0.08，i=1..6），取 .a
    blur1    : 横向同权重，末了 step(0.1, mask) 二值化
    blur2    : 竖向纯 max 抽头
    blur3    : 横向纯 max 抽头，**采样点先被噪声抖动**（撕纸锯齿的唯一来源）
    blur4/5  : 竖向 / 横向纯 max 抽头 ×3 档 → upMatting（决定环宽）
    noise    : flag = step(.9,ori)+step(.9,up) → (r,g,b) = (两层都在内, 只有一层=环带, 并集)
    blurX/Y  : blurX 竖向 → blurY 横向，b 通道再抹一遍（阴影要软），r/g 只抹 0.1px×3 抽头
    blurY    : 最终合成（内部=soft-light 纸纹后的原画，环带=zi1 纸纹，环外=黑色投影）

两处像素口径（都从 shader 源码推出，不是拟合）：
  * `mySize = u_ScreenParams.xy/min(x,y)*720`，`offset = lineSize/mySize`。
    当 u_ScreenParams = 渲染纹理自身宽高（本效果 rt/*.rt 不写死分辨率，跟屏幕走），
    采样偏移换算回像素正好是 `lineSize` px —— 即 **1 单位 lineSize = 1 个图层像素**。
    调用方烘焙时图层像素 1:1 等于画布像素（图层按 1.5 倍放大后铺满画布，见 bake_stroke_layer），
    所以 = 1 个画布像素。
  * 两处噪声抖动都是 **UV 比例**（与分辨率无关），换算成像素 = 幅值 × 图层高：
      blur3 的 `mix(0.05,0.08,sizeFlag)*(noise-0.5)*ratio_offset` 与 noise 的
      `0.012*ins1*(fbs-0.5)*ratio_offset`，ratio_offset 在 W<H 时取 (H/W,1)，所以 x/y 两个
      方向换算回像素后幅值相等。按 material 默认 ins1=2.0 直接算，幅值 = 2.4% 图层高（≈69 px
      @2880），是原版可见锯齿（±3~6 px）的一个数量级以上 → 这两处幅值必须标定，
      见 BLUR3_JIT_GAIN / NOISE_JIT_GAIN 注释。它们正是「撕纸感」的来源，不能删。

`blur0` 的 `myuv=(uv.x,1-uv.y)` 与 `blurY` 里 `oriTex` 的 `1.0-uv.y` 是**同一套 RT 翻转约定**
（RT 是 FBO，y 朝下），在最终合成处互相抵消。本模块把所有 pass 放在同一 y 空间里算，
省略这两次翻转：掩膜链的几何完全等价（对称算子，翻转可交换）。
唯一不能省的是 **贴图**：`sucai0`/`sucai1` 在 shader 里是按未翻转的 uv 采的，落到最终（未翻转）画面里
就是上下镜像的，所以本模块用 `tex[::-1]` 再拉伸。这一条不是猜的——`peopleTex` 翻与不翻，
同帧人像内部 MAE 8.84(无 soft-light) / 9.42(不翻) / **8.27(翻)**，翻转明显更贴原版。

material/*.material 里连同 uniform 名存着**编辑器默认值**（记录格式：名字长度前缀 + 名字 +
类型标记 + 8 字节 double，按记录的先后顺序一一对应），读出来是：
    blurY : sucai0Ins=0.0（运行时被滑杆改成 texture=0.6）、lineSize=1.8（→4*size=1.4）、
            edgeColOffset=1.0（blurY.frag 里根本没用到）、shadowIns=1.0
    noise : ins1=2.0、ins2=1.0、sizeFlag=0.9（noise.frag 里没有 sizeFlag）
    blur3 : sizeFlag=1.0、lineSize=1.0（运行时被滑杆改成 distance / 1*distance）
这些是**编辑器默认**，不等于运行时真值：shadowIns 的默认 1.0 实测偏深（见 SHADOW_INS），
ins1/ins2 同理（见 BLUR3_JIT_GAIN）。只有 sizeFlag 那两处能被 lua 的 remap 证实。

绑定的贴图**与直觉相反的那一对**按 material 实际记录来（字符串表顺序 name→path）：
    blurY.material: sucai1EJ → image/zi1.png ；sucai0EJ → image/peopleTex.png
    即 shader 里 soft-light 用的 sucai0 = peopleTex（做旧灰纹理，均值 131），
    环带用的 sucai1 = zi1（近纯白纸纹，95% 像素 lum>200）——与原片「纸环很白」一致。
"""

from __future__ import annotations

import math

import numpy as np

# xshader/blur0.frag::half_gaussian_weight —— 声明 9 个，循环只跑 i=1..6（即用到 0..6）
HALF_GAUSS = (0.20, 0.19, 0.17, 0.15, 0.13, 0.11, 0.08, 0.05, 0.02)
_GAUSS_TAPS = 6
_GAUSS_NUM = HALF_GAUSS[0] + 2.0 * sum(HALF_GAUSS[1:_GAUSS_TAPS + 1])

# material/noise.material 与 material/blurY.material 的默认值
INS1 = 2.0
INS2 = 1.0
SHADOW_INS_DEFAULT = 1.0

# ---- 以下三个是**对着原版像素标定**的值（结构 1:1 照抄 shader，只有幅值/未暴露的 uniform 要反解）----
#
# LINE_PX：1 个单位 lineSize 等于几个图层像素。
#   shader 自己就定死了：`mySize = u_ScreenParams.xy/min(x,y)*720`、`offset = lineSize/mySize`，
#   当 u_ScreenParams = 渲染靶自身宽高（rt/*.rt 不写分辨率，跟屏幕走）时，换算回像素正好是
#   `lineSize` px。取 1.0 后实测纸带法向厚度中位 7 px（原版 8 px 起）、
#   4s=1.4px 的 b 通道高斯 → 阴影宽度与原版一致；改成 1.3/1.6 后 1.0s 差量反而从 16.26 升到
#   16.32/16.72（同帧逐像素），纸环位置也从 52 px 漂到 56/65 px，故锁 1.0。
LINE_PX = 1.0  # 仅在拿不到图层尺寸时的兜底；实际用 line_px_for() 按几何算

def line_px_for(layer_w, layer_h) -> float:
    """1 个单位 lineSize 在**该图层**上等于几个像素。

    直接从 shader 推，不标定：
        `mySize = u_ScreenParams.xy / min(u_ScreenParams.x, u_ScreenParams.y) * 720`
        `offset = vec2(0,1) * lineSize / mySize`
    偏移是 **uv 单位**，落到 `inputHeight` 高的贴图上就是
        px = lineSize / mySize.y * layer_h
           = lineSize * layer_h / (720 * layer_h / min(layer_w, layer_h))
           = lineSize * min(layer_w, layer_h) / 720
    min 消掉了——所以只要图层与渲染靶同比例，1 单位 lineSize = min/720 像素，
    与分辨率无关（1080×1920 → 1.5；我们烘的 1620×2880 → **2.25**）。
    早期把这里写死成 1.0，纸边法向厚度只有原版的 1/3（7px vs 21px）。
    """
    w = float(layer_w)
    h = float(layer_h)
    if w <= 0 or h <= 0:
        return LINE_PX
    return min(w, h) / 720.0


# SHADOW_INS：blurY.shadowIns。lua 里这一行被注释掉了（`-- self.blurYMaterial4["shadowIns"]`），
#   运行时不写 → 实际用 material 默认值。material/blurY.material 里读到的是 1.0，
#   但那是**编辑器默认**，不是运行时真值；所以仍按像素反解（任务要求）而不用默认值顶替。
#   测法（可复现）：画布逐行 y=1040..1760，在 dt∈[46,100] 里取该行最暗点，
#   alpha = 1 - L/Lb（Lb = 没描边的 base 同位置亮度），对行取中位。三帧 0.5/1.0/1.5s：
#     原版            0.222 / 0.230 / 0.227
#     ins=1.0（材质值）0.274 / 0.278 / 0.281   ← 偏深 0.05，确认材质默认值不是运行时值
#     线性外推 0.226/0.278*1.0 ≈ 0.81；实测扫 0.60/0.70/0.75/0.84/1.0 →
#       0.195/0.200/0.204、0.216/0.221/0.224、0.226/0.231/0.234、0.243/…、0.274/…
#     （取值随 ins 略非线性，因为「该行最暗点」是个 argmin 口径），**0.75 才真正落到 0.226–0.234**。
SHADOW_INS = 0.75

# BLUR3_JIT_GAIN / NOISE_JIT_GAIN：两处噪声抖动的幅值（shader 里是写死的常数 × ins1/ins2，
#   material 默认 ins1=2.0 / ins2=1.0）。按 1:1 实现后量出来：
#   · 抖动量换算成 UV 分别是 0.0288（blur3）与 0.012*ins1=0.024（noise），即 2.4% 图层高度，
#     在 2880 高的图层上 ≈ 69–83 px；而原版纸带内缘锯齿只有 ±2.5–4 px、外缘 ±4–6 px
#     （360px 区放大 2 倍逐像素量），差一个数量级 → ins1/ins2 的运行时值显然是 effect 属性表
#     里的另一档（草稿的 adjust_params 只有 size/distance/texture 三个，读不到）。
#   · 两项的作用完全不同，分开标定：
#     blur3 抖的是「造环之前」的掩膜 → 只把整条环推来推去（原版纸像素离轮廓 11–70 px 的
#       散布主要来自它），取 0.35 时纸带位置/厚度分布最贴原版；试 1.0 则环被推到
#       离轮廓 230 px 以外（行扫描 x_p50 从 34 蹦到 233），肯定不对。
#     noise 抖的是 ori/up 两次采样 → 相对位移大于环宽就会把环撕成碎片；原版环是连续的，
#       取 0.15（残量只负责边缘那种高频毛刺）。
BLUR3_JIT_GAIN = 0.70
NOISE_JIT_GAIN = 0.15

# 噪声参数（xshader/blur3.frag 与 xshader/noise.frag 里写死的常数）
BLUR3_NOISE_SIZE = 3.5
NOISE_SIZE = 20.0
NOISE_ROT = 1.2


# --------------------------------------------------------------------------- #
# 噪声：逐行照抄 xshader/blur3.frag 与 xshader/noise.frag（完全确定性）
# --------------------------------------------------------------------------- #
def _fract(x):
    return x - np.floor(x)


def hash22(px, py):
    """xshader/blur3.frag::hash22 / xshader/noise.frag::hash22。

    vec2 p2 = fract(p*1324.);
    p2 += dot(p2, p2.yx+22.22);
    return fract((p2.xy+p2.yx)*p2.xy)-0.5;
    """
    p2x = _fract(px * 1324.0)
    p2y = _fract(py * 1324.0)
    d = p2x * (p2y + 22.22) + p2y * (p2x + 22.22)      # dot(p2, p2.yx + 22.22)
    p2x = p2x + d
    p2y = p2y + d
    s = p2x + p2y
    return _fract(s * p2x) - 0.5, _fract(s * p2y) - 0.5


def n21(px, py, seed, tweak=False):
    """n21(p, seed)。tweak=True 是 xshader/noise.frag 那一版（<0.5 减 0.05，否则加 0.05）。

    vec2 p3 = fract(p.yx*23.512);
    p3 += dot(p3, p3.yx+15.412+seed);
    return fract((p3.x+p3.y)*p3.x);
    """
    p3x = _fract(py * 23.512)                          # p.yx
    p3y = _fract(px * 23.512)
    k = 15.412 + seed
    d = p3x * (p3y + k) + p3y * (p3x + k)
    p3x = p3x + d
    p3y = p3y + d
    r = _fract((p3x + p3y) * p3x)
    if tweak:
        r = np.where(r < 0.5, r - 0.05, r + 0.05)
    return r


def noise2d(ux, uy, seed, smooth=True, tweak=False):
    """双线性 value noise：blur3 用 smoothstep 插值，noise.frag 用线性插值（源码里注释不同）。"""
    lvx = _fract(ux)
    lvy = _fract(uy)
    if smooth:
        lvx = lvx * lvx * (3.0 - 2.0 * lvx)
        lvy = lvy * lvy * (3.0 - 2.0 * lvy)
    idx = np.floor(ux)
    idy = np.floor(uy)
    bl = n21(idx, idy, seed, tweak)
    br = n21(idx + 1.0, idy, seed, tweak)
    tl = n21(idx, idy + 1.0, seed, tweak)
    tr = n21(idx + 1.0, idy + 1.0, seed, tweak)
    b = bl + (br - bl) * lvx
    t = tl + (tr - tl) * lvx
    return b + (t - b) * lvy


def fbs_noise_in(ux, uy, seed):
    """noise.frag::fbs_noise_in —— 0.7*n(uv)+0.5*n(2uv)+0.3*n(5uv)（线性插值版 noise）。"""
    return (0.7 * noise2d(ux, uy, seed + 11.23, smooth=False, tweak=True)
            + 0.5 * noise2d(ux * 2.0, uy * 2.0, seed + 29.98, smooth=False, tweak=True)
            + 0.3 * noise2d(ux * 5.0, uy * 5.0, seed + 12.13, smooth=False, tweak=True))


def fbs_noise_out(ux, uy, seed):
    return (0.7 * noise2d(ux, uy, seed + 12.14, smooth=False, tweak=True)
            + 0.5 * noise2d(ux * 2.0, uy * 2.0, seed + 22.23, smooth=False, tweak=True)
            + 0.3 * noise2d(ux * 5.0, uy * 5.0, seed + 11.15, smooth=False, tweak=True))


def blend_soft_light(base, blend):
    """blurY.frag::blendSoftLight —— 逐分量照抄。
    (blend<0.5) ? 2*base*blend + base*base*(1-2*blend)
                : sqrt(base)*(2*blend-1) + 2*base*(1-blend)
    """
    lo = 2.0 * base * blend + base * base * (1.0 - 2.0 * blend)
    hi = np.sqrt(np.clip(base, 0.0, 1.0)) * (2.0 * blend - 1.0) + 2.0 * base * (1.0 - blend)
    return np.where(blend < 0.5, lo, hi)


# --------------------------------------------------------------------------- #
# 采样原语：GLSL texture2D 的 clamp + 双线性
# --------------------------------------------------------------------------- #
def _sample(field, x, y):
    """在像素坐标 (x,y) 处双线性取样，边缘 clamp（RT 是 CLAMP_TO_EDGE）。"""
    h, w = field.shape
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = x - x0
    fy = y - y0
    xi = x0.astype(np.int32)
    yi = y0.astype(np.int32)
    xi1 = np.clip(xi + 1, 0, w - 1)
    yi1 = np.clip(yi + 1, 0, h - 1)
    xi = np.clip(xi, 0, w - 1)
    yi = np.clip(yi, 0, h - 1)
    a = field[yi, xi]
    b = field[yi, xi1]
    c = field[yi1, xi]
    d = field[yi1, xi1]
    fx = fx.astype(np.float32)
    fy = fy.astype(np.float32)
    top = a + (b - a) * fx
    bot = c + (d - c) * fx
    return top + (bot - top) * fy


def _pad_edge(field, pad):
    """边缘复制 pad（一次 pass 只做一次，13 个抽头共用——逐抽头 pad 是白烧内存带宽）。"""
    return np.pad(field, pad, mode="edge") if pad > 0 else field


def _shift_p(p, pad, h, w, dy, dx):
    """在已 pad 的缓冲上做「整数偏移 + 恒定小数偏移」双线性取样（等价 GLSL clamp+lerp）。"""
    iy = int(math.floor(dy))
    ix = int(math.floor(dx))
    fy = np.float32(dy - iy)
    fx = np.float32(dx - ix)

    def sl(oy, ox):
        return p[pad + oy:pad + oy + h, pad + ox:pad + ox + w]

    if fx == 0.0 and fy == 0.0:
        return sl(iy, ix)
    if fy == 0.0:
        return sl(iy, ix) * (1.0 - fx) + sl(iy, ix + 1) * fx
    if fx == 0.0:
        return sl(iy, ix) * (1.0 - fy) + sl(iy + 1, ix) * fy
    top = sl(iy, ix) * (1.0 - fx) + sl(iy, ix + 1) * fx
    bot = sl(iy + 1, ix) * (1.0 - fx) + sl(iy + 1, ix + 1) * fx
    return top * (1.0 - fy) + bot * fy


def _shift(field, dy, dx):
    """单抽头平移（便利函数；批量抽头请走 _pad_edge + _shift_p）。"""
    h, w = field.shape
    pad = int(max(abs(math.floor(dy)), abs(math.floor(dx)))) + 2
    return _shift_p(_pad_edge(field, pad), pad, h, w, dy, dx)


def _tap_pad(step_px):
    """一个 pass 需要的 pad：最远抽头 6*step 的整数部分 + 1（给双线性的 +1）再 +1 余量。"""
    return int(math.ceil(_GAUSS_TAPS * abs(step_px))) + 2


def _gauss_pass(field, axis, step_px, taps=_GAUSS_TAPS):
    """blur0/blur1/blurX/blurY 的加权和：中心 0.20，两侧 i=1..taps 各 half_gaussian_weight[i]，
    最后除以 num（= 0.20 + 2*Σw[1..taps]）。axis=0 竖向、1 横向。

    taps 默认 6（`for(int i=1;i<7;i++)`）；blurX/blurY 里抹 r/g 那两段源码写的是
    `for(int i=1;i<4;i++)`——只跑 3 抽头、num 也只累加到 1.22，所以这里必须能收窄。
    抽头少 3 个 = 环带内/外缘的过渡窄一点，虽然只差 0.4–0.6 px 的权重，但照抄就该照抄。
    """
    h, w = field.shape
    pad = _tap_pad(step_px)
    p = _pad_edge(field, pad)
    out = field * np.float32(HALF_GAUSS[0])
    for i in range(1, taps + 1):
        off = i * step_px
        wgt = np.float32(HALF_GAUSS[i])
        if axis == 0:
            out = out + (_shift_p(p, pad, h, w, off, 0.0) + _shift_p(p, pad, h, w, -off, 0.0)) * wgt
        else:
            out = out + (_shift_p(p, pad, h, w, 0.0, off) + _shift_p(p, pad, h, w, 0.0, -off)) * wgt
    num = HALF_GAUSS[0] + 2.0 * sum(HALF_GAUSS[1:taps + 1])
    return out / np.float32(num)


def _max_pass(field, axis, step_px):
    """blur2/blur4/blur5 的纯 max 抽头（中心 + 两侧 i=1..6）。"""
    h, w = field.shape
    pad = _tap_pad(step_px)
    p = _pad_edge(field, pad)
    out = field
    for i in range(1, _GAUSS_TAPS + 1):
        off = i * step_px
        if axis == 0:
            out = np.maximum(out, _shift_p(p, pad, h, w, off, 0.0))
            out = np.maximum(out, _shift_p(p, pad, h, w, -off, 0.0))
        else:
            out = np.maximum(out, _shift_p(p, pad, h, w, 0.0, off))
            out = np.maximum(out, _shift_p(p, pad, h, w, 0.0, -off))
    return out


def _step(edge, x):
    return np.where(x >= edge, np.float32(1.0), np.float32(0.0))


# --------------------------------------------------------------------------- #
# 抖动场
# --------------------------------------------------------------------------- #
def _ratio_offset(w, h):
    """blur3.frag::ratio_offset = mix(vec2(1,x/y), vec2(y/x,1), 1-step(x,y))。"""
    if w < h:
        return np.float32(h / w), np.float32(1.0)
    return np.float32(1.0), np.float32(w / h)


def _grid(w, h):
    """uv（GLSL 约定，像素中心 +0.5）与像素坐标网格。"""
    xs = (np.arange(w, dtype=np.float32) + 0.5) / np.float32(w)
    ys = (np.arange(h, dtype=np.float32) + 0.5) / np.float32(h)
    ux = np.broadcast_to(xs[None, :], (h, w))
    uy = np.broadcast_to(ys[:, None], (h, w))
    return ux, uy


def blur3_jitter(ux, uy, w, h, size_flag, gain=BLUR3_JIT_GAIN):
    """blur3.frag 里的 mynoise（UV 单位）。

    ratio_offset = mix(vec2(1.0,x/y), vec2(y/x,1.0), 1.0-step(x,y))
    noiseUV = uv; if (x<y) noiseUV.x *= x/y;
    mynoise = mix(0.05,0.08,sizeFlag)*(vec2(noise(noiseUV*3.5,12.43),noise(uv*3.5,12.11))-0.5)*ratio_offset
    """
    amp = 0.05 + (0.08 - 0.05) * float(size_flag)
    nux = ux
    if w < h:
        nux = ux * np.float32(w / h)
    n1 = noise2d(nux * np.float32(BLUR3_NOISE_SIZE), uy * np.float32(BLUR3_NOISE_SIZE), 12.43, smooth=True)
    n2 = noise2d(ux * np.float32(BLUR3_NOISE_SIZE), uy * np.float32(BLUR3_NOISE_SIZE), 12.11, smooth=True)
    rx, ry = _ratio_offset(w, h)
    amp = np.float32(amp * gain)
    return (amp * (n1 - np.float32(0.5)) * rx, amp * (n2 - np.float32(0.5)) * ry)


def noise_flags_jitter(ux, uy, w, h, ins1=INS1, ins2=INS2, gain=NOISE_JIT_GAIN):
    """noise.frag 的两组采样抖动 (mynoise, mynoise2)，以及 rot(1.2) 用的坐标。

    mat2 rot(1.2) = mat2(c,-s,s,c)，GLSL 列主序 → v*M = (x*c - y*s, x*s + y*c)。
    """
    rx, ry = _ratio_offset(w, h)
    nx, ny = ux * np.float32(NOISE_SIZE), uy * np.float32(NOISE_SIZE)
    c = np.float32(math.cos(NOISE_ROT))
    s = np.float32(math.sin(NOISE_ROT))
    qx = nx * c - ny * s
    qy = nx * s + ny * c
    hx, hy = hash22(ux, uy)
    ins1 = ins1 * gain
    ins2 = ins2 * gain

    # mynoise : fbs_noise_in 两分量，12.32 / 11.24，hash 系数 0.003*ins2
    jx1 = np.float32(0.012 * ins1) * (fbs_noise_in(nx, ny, 12.32) - np.float32(0.5)) * rx \
        + np.float32(0.003 * ins2) * hx * rx
    jy1 = np.float32(0.012 * ins1) * (fbs_noise_in(nx, ny, 11.24) - np.float32(0.5)) * ry \
        + np.float32(0.003 * ins2) * hy * ry
    # mynoise2: fbs_noise_out，坐标先乘 rot(1.2)，hash 系数 0.004*ins2
    jx2 = np.float32(0.012 * ins1) * (fbs_noise_out(qx, qy, 11.94) - np.float32(0.5)) * rx \
        + np.float32(0.004 * ins2) * hx * rx
    jy2 = np.float32(0.012 * ins1) * (fbs_noise_out(qx, qy, 13.52) - np.float32(0.5)) * ry \
        + np.float32(0.004 * ins2) * hy * ry
    return (jx1, jy1), (jx2, jy2)


# --------------------------------------------------------------------------- #
# 与时间无关的场：整段烘焙共用（抖动只跟 uv 有关，逐帧算一遍是白费 CPU）
# --------------------------------------------------------------------------- #
class PaperFields:
    """图层尺寸 + sizeFlag 固定时，所有噪声/抖动场的预计算缓存。

    这些场是 uv 的纯函数（不随帧变），一段 100 帧的烘焙只需算一次：
        blur3 的 mynoise（造环前的掩膜抖动）、noise.frag 的两组采样抖动。
    不缓存也不会算错——只是每帧重算一遍（单帧 ~3s → ~1s）。
    """

    def __init__(self, h: int, w: int, size_flag: float,
                 jit3=BLUR3_JIT_GAIN, jitn=NOISE_JIT_GAIN):
        self.h, self.w, self.size_flag = int(h), int(w), float(size_flag)
        ux, uy = _grid(self.w, self.h)
        self.ux, self.uy = ux, uy
        self.px = ux * np.float32(self.w) - np.float32(0.5)
        self.py = uy * np.float32(self.h) - np.float32(0.5)
        jx, jy = blur3_jitter(ux, uy, self.w, self.h, self.size_flag, jit3)
        self.jx3 = jx * np.float32(self.w)
        self.jy3 = jy * np.float32(self.h)
        (jx1, jy1), (jx2, jy2) = noise_flags_jitter(ux, uy, self.w, self.h, gain=jitn)
        self.jx1 = jx1 * np.float32(self.w)
        self.jy1 = jy1 * np.float32(self.h)
        self.jx2 = jx2 * np.float32(self.w)
        self.jy2 = jy2 * np.float32(self.h)

    def matches(self, h, w, size_flag) -> bool:
        return (self.h, self.w) == (int(h), int(w)) and abs(self.size_flag - float(size_flag)) < 1e-9


# --------------------------------------------------------------------------- #
# 主链
# --------------------------------------------------------------------------- #
def render_stroke_layer(fg_rgb, alpha, *, size, distance, texture,
                        paper_rgb=(1.0, 1.0, 1.0), ring_tex=None, grain_tex=None,
                        shadow_ins=SHADOW_INS, line_px=LINE_PX,
                        jit3=None, jitn=None, fields=None):
    """抠像 matte + 原图 → 撕纸描边 RGBA 图层。

    参数
    ----
    fg_rgb   : (H,W,3) float32，0..1，人像原画（= getInPut 的 oriCol）
    alpha    : (H,W)   float32，0..1，抠像蒙版（= getInPut 的 oriMatting）
    size     : 草稿 effects_adjust_size     → blur4/5.lineSize=3s, blurX/blurY.lineSize=4s
    distance : 草稿 effects_adjust_distance → blur0/1/2.lineSize=7d, blur3.lineSize=1d, sizeFlag=1d
    texture  : 草稿 effects_adjust_texture  → blurY.sucai0Ins=1t（内部 soft-light 强度）
    paper_rgb: 未用（环带颜色来自 ring_tex；留参数只为可读性/兜底常量色）
    ring_tex : (h,w,3) float32 0..1 环带纸纹（image/zi1.png → sucai1）
    grain_tex: (h,w,3) float32 0..1 内部做旧纹理（image/peopleTex.png → sucai0）
    shadow_ins: blurY.shadowIns（材质默认 1.0；本草稿实测反解见 bake_stroke_layer）
    line_px  : 1 单位 lineSize 等于几个图层像素（见模块 docstring 推导）
    fields   : 可选的 PaperFields 缓存（尺寸/ sizeFlag 一致时直接复用，逐帧烘焙快 ~3 倍）

    返回 (H,W,4) float32，rgb 0..1、alpha 0..1（未做 uint8 量化）。
    """
    h, w = alpha.shape
    d = float(distance)
    s = float(size)
    t = float(texture)
    lp = float(line_px)

    ls_blur01 = 7.0 * d * lp        # ImageBusinessSlider.lua: effects_adjust_distance → 0..7
    ls_blur2 = 7.0 * d * lp
    ls_blur3 = 1.0 * d * lp
    size_flag = d                   # lua: blur3.sizeFlag = remap(distance, 0, 1)
    ls_blur45 = 3.0 * s * lp        # effects_adjust_size → 0..3
    ls_blurxy = 4.0 * s * lp        # effects_adjust_size → 0..4

    if fields is None or not fields.matches(h, w, size_flag):
        fields = PaperFields(h, w, size_flag,
                             BLUR3_JIT_GAIN if jit3 is None else jit3,
                             NOISE_JIT_GAIN if jitn is None else jitn)
    px, py = fields.px, fields.py

    a = np.asarray(alpha, dtype=np.float32)

    # --- getInPut → blur0（竖向加权）→ blur1（横向加权 + step(0.1)） ---
    mid = _gauss_pass(a, 0, ls_blur01)
    ori = _step(0.1, _gauss_pass(mid, 1, ls_blur01))

    # --- blur2（竖向 max）→ blur3（横向 max，采样点被噪声抖动） ---
    mid = _max_pass(ori, 0, ls_blur2)
    mid = _max_pass_jitter(mid, 1, ls_blur3, fields.jx3, fields.jy3, px, py)
    ori_mask = mid                       # rt/oriMatting.rt

    # --- blur4（竖向 max）→ blur5（横向 max）→ upMask ---
    mid = _max_pass(ori_mask, 0, ls_blur45)
    up_mask = _max_pass(mid, 1, ls_blur45)

    # --- noise：两层都采一次带抖动的样本，算 (r,g,b) 三层旗标 ---
    ori_s = _sample(ori_mask, px + fields.jx1, py + fields.jy1)
    up_s = _sample(up_mask, px + fields.jx2, py + fields.jy2)
    flag = _step(0.9, ori_s) + _step(0.9, up_s)
    fm_r = _step(1.5, flag)                       # 两层都在内
    fm_g = _step(0.5, flag) * _step(flag, 1.5)    # 只有一层 = 环带
    fm_b = _step(0.5, flag)                       # 并集

    # --- blurX（竖向）→ blurY（横向）---
    # b 用 lineSize=4s 的 7 抽头；r/g 是 `for(i=1;i<4)` 的 3 抽头、间距 0.1 px（几乎不动，只去台阶）
    fb = _gauss_pass(fm_b, 0, ls_blurxy)
    fr = _gauss_pass(fm_r, 0, 0.1, taps=3)
    fg = _gauss_pass(fm_g, 0, 0.1, taps=3)
    fb = _gauss_pass(fb, 1, ls_blurxy)
    fr = _gauss_pass(fr, 1, 0.1, taps=3)
    fg = _gauss_pass(fg, 1, 0.1, taps=3)
    m_r = np.clip(fr, 0.0, 1.0)
    m_g = np.clip(fg, 0.0, 1.0)
    m_b = np.clip(fb, 0.0, 1.0)

    # --- blurY 合成 ---
    ori_col = np.asarray(fg_rgb, dtype=np.float32)
    if grain_tex is not None and t > 0.0:
        sc = _resize(grain_tex[::-1], h, w)
        ori_col = ori_col + (blend_soft_light(ori_col, sc) - ori_col) * np.float32(t)
    if ring_tex is not None:
        s1 = _resize(ring_tex[::-1], h, w)
    else:
        s1 = np.empty((h, w, 3), np.float32)
        s1[:, :] = np.asarray(paper_rgb, np.float32)

    # outCol = mix(vec4(0), vec4(0,0,0,0.6), oriMask.b*shadowIns)
    out_a = np.float32(0.6) * m_b * np.float32(shadow_ins)
    # resuleCol = mix(outCol, oriCol*oriMask.r, oriMask.r)   ← 逐分量
    rr = m_r[..., None]
    col = ori_col * rr * rr
    al = out_a * (1.0 - m_r) + m_r * m_r
    # resuleCol = mix(resuleCol, sucai1Col, oriMask.g)
    gg = m_g[..., None]
    rgb = col * (1.0 - gg) + s1 * gg
    aout = al * (1.0 - m_g) + m_g * np.float32(1.0)
    return np.concatenate([np.clip(rgb, 0.0, 1.0), np.clip(aout, 0.0, 1.0)[..., None]], axis=2)


def _max_pass_jitter(field, axis, step_px, jx_px, jy_px, px, py):
    """blur3 的 max 抽头，但采样点整体被 (jx_px, jy_px) 平移。"""
    out = _sample(field, px + jx_px, py + jy_px)
    for i in range(1, _GAUSS_TAPS + 1):
        off = i * step_px
        if axis == 0:
            out = np.maximum(out, _sample(field, px + jx_px, py + jy_px + off))
            out = np.maximum(out, _sample(field, px + jx_px, py + jy_px - off))
        else:
            out = np.maximum(out, _sample(field, px + jx_px + off, py + jy_px))
            out = np.maximum(out, _sample(field, px + jx_px - off, py + jy_px))
    return out


def _resize(tex, h, w):
    """720x960 贴图按 uv 拉伸到图层尺寸（GLSL 双线性采样 = 双线性缩放）。"""
    th, tw = tex.shape[:2]
    if (th, tw) == (h, w):
        return np.asarray(tex, np.float32)
    ys = (np.arange(h, dtype=np.float32) + 0.5) * (th / h) - 0.5
    xs = (np.arange(w, dtype=np.float32) + 0.5) * (tw / w) - 0.5
    y0 = np.clip(np.floor(ys).astype(np.int32), 0, th - 1)
    x0 = np.clip(np.floor(xs).astype(np.int32), 0, tw - 1)
    y1 = np.clip(y0 + 1, 0, th - 1)
    x1 = np.clip(x0 + 1, 0, tw - 1)
    fy = np.clip(ys - np.floor(ys), 0.0, 1.0)[:, None, None]
    fx = np.clip(xs - np.floor(xs), 0.0, 1.0)[None, :, None]
    top = tex[y0][:, x0] * (1 - fx) + tex[y0][:, x1] * fx
    bot = tex[y1][:, x0] * (1 - fx) + tex[y1][:, x1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


# --------------------------------------------------------------------------- #
# 资源加载（缓存根由调用方给，不写死路径）
# --------------------------------------------------------------------------- #
EFFECT_RESOURCE_ID = "7172457738302132773"


def find_effect_dir(cache_root, resource_id: str = EFFECT_RESOURCE_ID):
    """<cache>/effect/<id>/<hash>/AmazingFeature —— 找不到返回 None（调用方降级）。"""
    from pathlib import Path

    root = Path(str(cache_root)) / "effect" / str(resource_id)
    if not root.is_dir():
        return None
    for cand in sorted(root.glob("*/AmazingFeature")):
        if (cand / "image" / "zi1.png").is_file():
            return cand
    return None


def load_textures(effect_dir):
    """读 image/zi1.png（环带纸纹 = sucai1）与 image/peopleTex.png（内部做旧 = sucai0）。"""
    from PIL import Image

    def _img(name):
        p = effect_dir / "image" / name
        return np.asarray(Image.open(p).convert("RGB"), dtype=np.float32) / 255.0

    return {"ring": _img("zi1.png"), "grain": _img("peopleTex.png")}
