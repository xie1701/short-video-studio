#!/usr/bin/env python3
"""剪映「单层描边」business 效果 → numpy 1:1 移植（纯函数，无 I/O 副作用）。

和 `stroke_paper.py`（撕纸描边）是**两套完全不同的 shader**，只共用「1 单位 = min(w,h)/720 px」
这一条像素口径，所以必须按 resource_id 分派，不能拿撕纸的数学去渲这个包。

效果包结构（`<cache>/effect/<resource_id>/<hash>/AmazingFeature/`）：
    main.scene                    实体内序：blur0 → blur1 → blur2 → blur3 → Pass2
    material/{blur0..blur3,pass2}.material   uniform 绑定 + 编辑器默认值
    xshader/{blur0..blur3,pass2}.frag       GLSL 源码（本模块逐行照抄）
    lua/ImageBusinessSlider.lua             两个滑杆 → uniform 的映射
    image/                                  空目录（本效果不吃贴图）

渲染链（main.scene 实体顺序 + 各 material 的 `mattingmaskEJ` 绑定定死）：
    blur0(mid.rt) ← share://bgmask.texture     竖向加权高斯
    blur1(cam0.rt) ← rt/mid.rt                 横向加权高斯
    blur2(mid.rt) ← rt/cam0.rt                 竖向加权高斯
    blur3(cam0.rt) ← rt/mid.rt                 横向加权高斯   → upMatting 的原料
    Pass2(outputTex.rt) ← bgmask / cam0 / input  合成（边色 / 原画 / 掩膜）
四次高斯的权重都是 `half_gaussian_weight[0..6] = 0.20/0.19/0.17/0.15/0.13/0.11/0.08`
（`for(i=1;i<7;i++)`，除 num 归一化），四个材料用**同一个** `lineSize`。

滑杆语义（lua/ImageBusinessSlider.lua::onEvent，逐行照抄）：
    effects_adjust_size  s → blur0..blur3.uniform "lineSize" = remap(s, 0.3, 7.5)
                             Pass2.sizeFlag = remap(s, 0, 1)
      注意 `0.3` 是**底宽**：s=0 时环也不是 0 宽（这一点与撕纸描边 `3*s`/`4*s` 线性过原点不同，
      也不能写成「px/单位」的系数）。
    effects_adjust_alpha a → Pass2.alpha = a       （0..1，描边强度）
    matting_stroke_adjust_color (R,G,B) → Pass2.R/G/B（三个 float，边色）
      —— lua 里没有这几行的写入（颜色由 `ImageBusinessSlider.json` 的 LV/color 组按
      `edgeColOffset` 绑定，`edgeColOffset` 那条 lua 分支被注释掉了），但 pass2.frag 明确读
      `vec3(R,G,B)` 当边色；草稿里 color 是 [1,1,1,1] → 白色描边。

pass2.frag 的合成口径（本模块逐行照抄，注释里标出每一行的 numpy 对应）：
    oriCol.rgb = clamp(oriCol.rgb / oriCol.a)   ← 输入纹理是**预乘**的，先反预乘
    upMatting = smoothstep(0.05, 0.05 + mix(0.035, 0.01, sizeFlag), blur3)
    if (oriCol.a > 0) rgb = mix(oriCol.rgb, edgeCol, upMatting) else rgb = edgeCol
    rgb = mix(rgb, oriCol.rgb, oriMatting)
    rgb = clamp(rgb / clamp(oriCol.a + upMatting, 0, 1))     ← 按总覆盖率还原成直通色
    minValue = 7×7 (半径 3) 腐蚀 min(orimattingMask)
    lastAlpha = clamp(oriCol.a + (upMatting - minValue) * alpha, 0, 1)

像素口径（与 `stroke_paper.line_px_for()` 同一条推导，不是拟合）：
    shader: `mySize = u_ScreenParams.xy/min(x,y)*720`、`offset = lineSize/mySize`
            → 采样位移 = lineSize * min(w,h)/720 像素；
            `texSize` 完全同一个式子，所以腐蚀半径 3 单位 = 3 * min(w,h)/720 像素。
    1 单位 = min(图层宽,高)/720 像素（1080×1920 → 1.5；烘的 1620×2880 → 2.25）。

y 翻转：blur0 用 `myuv=(uv.x,1-uv.y)` 读 bgmask，blur2 用 `1-uv.y` 读 cam0.rt，
Pass2 又用 `1-uv.y` 读 cam0.rt；blur1/blur3 与 Pass2 读 bgmask/oriTex 不翻转。
这正是 RT(FBO) 的交替翻转约定。四次高斯都是对称可分离算子，翻转与它可交换，
代数上三次翻转在最终采样处正好抵消（F∘G∘F∘G∘F∘G∘F = G⁴），所以本模块把所有 pass
放在同一个 y 空间里算，结果与源码逐像素等价；本效果没有贴图，也就没有 `tex[::-1]` 那类事。

alpha 语义（与 `stroke_paper.render_stroke_layer` 对齐，供 `alphamerge` 用）：
    返回**直通（非预乘）**RGBA，rgb 0..1、alpha 0..1。环外 alpha=0。
    源码在 `clamp(oriCol.a + upMatting, 0, 1) == 0` 处会 0 除（GLSL → inf → clamp 到 1），
    那片 alpha 全是 0、画面上不可见；本模块照抄成 rgb=1 会白白让「先乘 alpha 再叠加」的
    下游看到白底，故该处取 rgb=0（与撕纸版「环外 rgb=0」一致）。这是唯一一处**有意**偏离
    源码字面（0 除的取值本来就未定义），其余逐行照抄。
"""

from __future__ import annotations

import math

import numpy as np

# 复用撕纸版的 GLSL 采样原语（双线性 + CLAMP_TO_EDGE）与 720 口径：
# 两个模块的 sampler 语义必须完全一致，重复实现迟早漂移。
from stroke_paper import _gauss_pass, _pad_edge, _shift_p, line_px_for

EFFECT_RESOURCE_ID = "7172457125392683528"

# lua: remap(intensity, 0.3, 7.5) —— 单位是 shader 的 lineSize，不是像素
LINE_SIZE_MIN = 0.3
LINE_SIZE_MAX = 7.5

# pass2.material 里的编辑器默认值（读 material 记录的 double）
PASS2_ALPHA_DEFAULT = 1.0     # lua 用 effects_adjust_alpha 覆盖；草稿里是 1.0
DOWN_INS = 0.05               # pass2.frag::downIns
RING_SOFT_LO = 0.035          # mix(0.035, 0.01, sizeFlag)
RING_SOFT_HI = 0.01
EROSION_RADIUS = 3.0          # pass2.frag::erosionRadius（单位同 lineSize）


def line_size_for(size: float) -> float:
    """草稿 `effects_adjust_size`（0..1）→ lineSize（shader 单位）。

    lua: `remap(intensity, 0.3, 7.5)` = 0.3 + 7.2*size。0.3 是底宽，必须保留。
    """
    s = float(size)
    return LINE_SIZE_MIN + (LINE_SIZE_MAX - LINE_SIZE_MIN) * s


def _smoothstep(edge0, edge1, x):
    if edge1 <= edge0:
        return np.where(x >= edge1, np.float32(1.0), np.float32(0.0))
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _unpremul(fg_rgb, alpha):
    """pass2.frag::`oriCol.rgb = clamp(oriCol.rgb / oriCol.a, 0, 1)`。

    效果输入的 `share://input.texture` 是预乘 RGBA（所以 shader 才要除 a）。调用方给的是
    **直通**原画 + 独立掩膜，因此这里先按预乘还原（`fg*a / a`），与源码数学一致；
    a=0 处源码是 0/0（未定义，该像素的 rgb 随后被 edgeCol 顶替），这里取 0 防止 inf/nan。
    """
    a = np.asarray(alpha, np.float32)[..., None]
    prem = np.asarray(fg_rgb, np.float32) * a
    safe = np.where(a > 0.0, a, np.float32(1.0))
    return np.where(a > 0.0, np.clip(prem / safe, 0.0, 1.0), np.float32(0.0)), a


def _erosion_min(alpha, radius_px):
    """pass2.frag 的 7×7 腐蚀：`minValue = min(minValue, sample(uv + (x,y)/texSize))`。

    初值 1.0（源码 `float minValue = 1.0;`），x/y 各跑 -3..3 共 49 个抽头。
    偏移在 uv 里是 `x/texSize`，换算成像素正好 `x * min(w,h)/720`（见模块 docstring）。
    49 个偏移都是**常量**，所以走 `_pad_edge` + `_shift_p`（pad 只做一次、双线性权重
    只算一次），不要逐个 `_sample`——后者每个抽头都要 floor/astype 一整帧，慢 3 倍。
    """
    h, w = alpha.shape
    step = float(radius_px)
    pad = int(math.ceil(EROSION_RADIUS * abs(step))) + 3
    p = _pad_edge(alpha, pad)
    out = np.full((h, w), np.float32(1.0), np.float32)
    for i in range(int(2 * EROSION_RADIUS) + 1):
        dy = (i - EROSION_RADIUS) * step
        for j in range(int(2 * EROSION_RADIUS) + 1):
            dx = (j - EROSION_RADIUS) * step
            out = np.minimum(out, _shift_p(p, pad, h, w, dy, dx))
    return out


def render_solid_layer(fg_rgb, alpha, *, size, alpha_gain=PASS2_ALPHA_DEFAULT,
                       color_rgb=(1.0, 1.0, 1.0), ring_tex=None, line_px=None):
    """抠像 matte + 原图 → 单层描边 RGBA 图层（blur0 → blur1 → blur2 → blur3 → Pass2）。

    参数
    ----
    fg_rgb    : (H,W,3) float32 0..1，原画（= Pass2 的 oriTex，直通色）
    alpha     : (H,W)   float32 0..1，抠像蒙版（= bgmask / orimattingMask）
    size      : 草稿 `effects_adjust_size`（0..1）→ lineSize = 0.3 + 7.2*size
    alpha_gain: 草稿 `effects_adjust_alpha`（0..1）→ Pass2.alpha（描边强度；内部像素不受影响，
                因为内部 up-minValue≈0）
    color_rgb : 边色（= Pass2.R/G/B，草稿 `color`，默认白）
    ring_tex  : 本效果包的 pass2.frag **不采任何贴图**（`edgeColLine` 声明了但没读，`image/`
                是空目录），参数只为与 `render_stroke_layer` 同形；传了也不生效。
    line_px   : 1 单位 lineSize 等于几个图层像素（见 `stroke_paper.line_px_for`）；
                None → 按图层几何现算

    返回 (H,W,4) float32，rgb 0..1 直通色、alpha 0..1（未量化）。
    """
    a = np.asarray(alpha, np.float32)
    h, w = a.shape
    lp = line_px_for(w, h) if line_px is None else float(line_px)
    ls = line_size_for(size) * lp          # 单位 → 像素；blur0..3 四遍用同一个值

    # --- blur0 → blur1 → blur2 → blur3（四遍都是 7 抽头加权高斯，除 num） ---
    mid = _gauss_pass(a, 0, ls)            # blur0.frag：竖向
    mid = _gauss_pass(mid, 1, ls)          # blur1.frag：横向
    mid = _gauss_pass(mid, 0, ls)          # blur2.frag：竖向
    up_raw = _gauss_pass(mid, 1, ls)       # blur3.frag：横向 → cam0.rt

    # --- Pass2 ---
    size_flag = float(size)                                    # lua: remap(size, 0, 1)
    soft = RING_SOFT_LO + (RING_SOFT_HI - RING_SOFT_LO) * size_flag
    up = _smoothstep(DOWN_INS, DOWN_INS + soft, up_raw)        # smoothstep 后才是 upMatting

    ori_rgb, a3 = _unpremul(fg_rgb, a)                         # 已含 clamp(rgb/a)
    edge = np.asarray(color_rgb, np.float32).reshape(1, 1, 3)

    edge_full = np.broadcast_to(edge, ori_rgb.shape)
    # if (oriCol.a > 0) rgb = mix(oriCol.rgb, edgeCol, upMatting) else rgb = edgeCol
    res = np.where(a3 > 0.0, ori_rgb + (edge_full - ori_rgb) * up[..., None], edge_full)
    # res = mix(res, oriCol.rgb, orimatting)
    res = res + (ori_rgb - res) * a3
    den = np.clip(a3 + up[..., None], 0.0, 1.0)
    rgb = np.where(den > 0.0, res / np.where(den > 0.0, den, np.float32(1.0)), np.float32(0.0))

    min_value = _erosion_min(a, EROSION_RADIUS * lp)
    out_a = np.clip(a + (up - min_value) * float(alpha_gain), 0.0, 1.0)
    return np.concatenate([np.clip(rgb, 0.0, 1.0), out_a[..., None]], axis=2).astype(np.float32)


# --------------------------------------------------------------------------- #
# 资源加载（缓存根由调用方给，不写死路径）
# --------------------------------------------------------------------------- #
def find_effect_dir(cache_root, resource_id: str = EFFECT_RESOURCE_ID):
    """`<cache>/effect/<id>/<hash>/AmazingFeature` —— 找不到返回 None（调用方降级）。

    本包没有 `image/zi1.png` 那类判据（目录是空的），所以按 `xshader/pass2.frag` 认。
    """
    from pathlib import Path

    root = Path(str(cache_root)) / "effect" / str(resource_id)
    if not root.is_dir():
        return None
    for cand in sorted(root.glob("*/AmazingFeature")):
        if (cand / "xshader" / "pass2.frag").is_file():
            return cand
    return None


def load_textures(effect_dir):  # noqa: ARG001
    """本效果包不吃贴图（`image/` 为空、pass2.frag 只读 R/G/B 三个 float uniform）。

    保留这个函数只为让两个描边模块的调用形状一致；永远返回空 dict。
    """
    return {}
