#!/usr/bin/env python3
"""
剪映「入场 / 出场动画」的数学复刻。

草稿里挂在 `segment.extra_material_refs` 的 `material_animations[*]` 上：

    {"type": "sticker_animation",
     "animations": [{"id": "1045515", "type": "in",  "name": "缩小",     "duration": 500000},
                    {"id": "1363006", "type": "out", "name": "向下滑动", "duration": 500000,
                     "start": 2000000}]}

`duration` 单位是微秒；`out` 的 `start` 是**相对该段起点的偏移**（微秒），缺省时按
「段尾倒数 duration」处理。`in` 永远从段起点开始。

这个模块只做纯计算：给「段内本地时间 → 缩放 / 位移 / 透明度 / 模糊」四组修正量，
渲染侧拿去逐帧施加（叠加层用 PIL 逐帧，贴纸用图层参数，文字用 PNG 逐帧）。

为什么单独一个模块
──────────────────
不实现这一层，所有「写进草稿但只在播放时生效」的动效都会变成静帧：
style_21 里 13 处动画（贴纸缩小入场、手机框下滑出场、标题日出、渐显渐隐…）
一个都没做，成片看起来就是「该动的地方一动不动」。

系数可标定
──────────
`DEFAULTS` 里的幅度是从剪映同名动画的常见档位定的初值；`CALIBRATION` 留给
「用原版帧反解最优值」的闭环——`sweep()` 就是那个闭环的工具函数，别手调。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

US = 1_000_000

# 动画幅度初值（可被 CALIBRATION 覆盖）。缩放类给的是「动画起点值 → 结束回到 1.0」。
DEFAULTS: Dict[str, dict] = {
    "fade":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "scale":    {"scale0": 1.25, "alpha0": 1.0, "blur0": 0.0},
    "slide":    {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "reveal":   {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "blur":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 24.0},
    "rotate":   {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "缩小":     {"scale0": 1.25, "alpha0": 1.0, "blur0": 0.0},
    "放大":     {"scale0": 0.75, "alpha0": 1.0, "blur0": 0.0},
    "渐显":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "渐隐":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "向左滑动": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向右滑动": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向上滑动": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向下滑动": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向右露出": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向左露出": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向上露出": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向下露出": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "日出":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "日落":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "横向模糊": {"scale0": 1.00, "alpha0": 0.0, "blur0": 24.0},
    "雪光模糊": {"scale0": 1.00, "alpha0": 0.0, "blur0": 24.0},
    "模糊":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 24.0},
    "闪白":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "闪黑":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    # 新增两族：横向张开 / 高位落下（都不改 alpha，否则会「凭空淡入」）
    "flipx":    {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0, "flip0": 0.0},
    "drop":     {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0, "drop": 0.115},
    "镜像翻转": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0, "flip0": 0.0},
    "抖动下降": {"scale0": 0.78, "alpha0": 1.0, "blur0": 0.0, "drop": 0.115},
}

# 用原版帧反解出来的覆盖值（键 = 动画名，字段同 DEFAULTS）。留空即全用 DEFAULTS。
#
# 每条都注明「反解方式」，数字不是拍的：
#   ease    偏移量曲线 w^k（w = 距动画起点的剩余权重）；k 由逐帧量到的位移/尺寸回归得出。
#   *_0     动画起点的偏移幅度（scale0 = 尺寸倍数，drop/slide_gain = 画布归一化位移）。
CALIBRATION: Dict[str, dict] = {
    # 本表是「贴纸」宿主下的标定（sticker_render 调 anim_state 不带 ctx）。
    # 视频叠加层 / 文字层的同名幅度见下面的 CTX_CALIBRATION["layer"]。
    #
    # 用原版帧反解的「向下滑动」出场（style_21 手机边框）：草稿写 duration=0.5，
    # 但实测 12.45s 还是静止、12.85s 已完全移出 → 真实时长约 0.6×，起点比「段尾减时长」晚约 0.08s。
    "向下滑动": {"slide_gain": 0.8, "out_dur_scale": 0.6, "out_start_slack": 0.03},
    # 「缩小」入场（同一处手机边框）：14 组参数扫下来 a0=0 s0=1.3 最优——
    # 起手是「从大处淡入」，10.45s 原版几乎还看不见边框，所以起点透明度必须是 0。
    "缩小": {"scale0": 1.3, "alpha0": 0.0, "in_dur_scale": 0.88},
}

# 按「宿主」分的幅度表。剪映同名动画在贴纸 / 视频图层 / 文字上幅度不同（贴纸 PNG 自带透明
# 边距，包围盒 ≠ 可见墨迹），所以同一套曲线要按宿主给不同系数。
#   layer：视频叠加轨与文字层。数值均由原版帧反解，注释里写明反解方式：
#     ease       偏移曲线 w^k（w = 距动画起点的剩余权重）
#     alpha_ease 透明度曲线单独用另一个指数（实测尺寸与透明度并不是同一条曲线）
CTX_CALIBRATION: Dict[str, Dict[str, dict]] = {
    "layer": {
        # 卡片底边逐帧反解：scale = 1.5 → 1.0，九个采样点全在一条直线上
        # （p=0.11/0.44/0.89 实测 1.443/1.276/1.056）；透明度同时从 0 涨到 1，
        # 卡面亮度实测 p=0.11 时只有最终的 ~36%（9.9s 还是「鬼影」）、p=0.44 已 ~96%
        # → 透明度比尺寸衰减得快得多，扫 {1,3,6,10} 后取 k=6（p=0.11→0.50、0.44→0.97）。
        "缩小": {"scale0": 1.5, "alpha0": 0.0, "ease": 1.0, "alpha_ease": 6.0},
        # 「收拢」：段首那帧原版还看不见字（3.5s 只能看到上一行），同样是「从大处淡入」。
        "收拢": {"scale0": 1.35, "alpha0": 0.0, "alpha_ease": 3.0},
        # 「雪光模糊」：8.2s 起手要「先亮起来再收清」，原版 0.53s 动画过半（8.5s）已经又亮又清，
        # 拿线性衰减会到 8.5s 还糊成一片（实测 alpha 只有 0.27、字完全读不出）。
        "雪光模糊": {"alpha0": 0.0, "blur0": 32.0, "ease": 1.0, "alpha_ease": 6.0},
        # 「镜像翻转」入场：卡片左右边界 12 帧量到 |scale_x| = 0.363→1.0，
        # 拟合 1-(1-p)^3 残差 ±0.02（首帧因半透明边被采样到偏小），即 out-cubic。
        "镜像翻转": {"flip0": 0.0, "ease": 3.0},
        # 「向左/向右滑动」入场：卡片左(右)边界逐帧量到 dx = A(1-p)^k，
        # 两张不同卡片回归出 k=2.16/2.22、A=1218/1319 px（画布宽 1080）→ k=2.2、A=1.2W。
        "向左滑动": {"slide_gain": 1.2, "ease": 2.2},
        "向右滑动": {"slide_gain": 1.2, "ease": 2.2},
        # 「抖动下降」入场：起手缩到 0.78 倍、从画面上方 0.115H 处落下（底边逐帧量到
        # -212px → 0，同时宽度 727→918）；原动画带阻尼抖动，这里只复刻主位移。
        "抖动下降": {"scale0": 0.78, "drop": 0.115, "ease": 2.4},
    },
    # 主轨段级动画（material_animations 挂在视频段上）。目前只做模糊族——
    # 主轨要逐帧几何得拆段重渲，模糊族可以只叠一条 gblur。
    "main": {
        # 「横向模糊」入场（style_21 主轨 4.833s 段，0.2667s）实为**径向/变焦模糊 + 白光过曝**：
        # 原版 4.90s 帧用视觉复核是「以画面中心为原点向外放射」，不是横向拖影；
        # 各向同性/椭圆 gblur 试过（sigmaX 30–55），逐帧差 51–57 下不来。
        # 该动效实为「径向/变焦模糊 + 白光过曝」：原版 4.85/4.90/4.95/5.00/5.05 帧整帧亮度
        # 219.9/187.1/?? /187.1... 依次衰减到 164（无动效基准），按 out=(1-a)·in+255·a 反解
        # a = 0.6/0.5/0.4/0.2/0.1 → 线性 a = 0.65·(1-p)（p 为动画进度，残差 ≤0.04）。
        # 变焦模糊（径向）用 PIL 逐帧烘（见 render_replica 的 _zmanim_media），这里只给白场：
        # blur_x0=0 显式关掉各向同性/椭圆 gblur，否则会叠一条与径向模糊无关的糊边。
        "横向模糊": {"flash0": 0.65, "ease": 1.0, "in_dur_scale": 1.0, "blur_x0": 0.0},
    },
}

# 全库 113 套草稿里共 150 个动画名、2332 条。按行为归族比逐个硬编码可维护：
# 同族共享一套数学，族内个别名字用 DEFAULTS 单独调幅度。
FAMILY: Dict[str, str] = {}
for _names, _fam in (
    (("渐显", "渐隐", "闪白", "闪黑", "暗场", "亮场", "淡入", "淡出"), "fade"),
    (("缩小", "放大", "轻微放大", "轻微缩小", "弹出", "跃进", "收拢", "折叠",
      "生长", "生长 II", "生长III", "弹性放大", "胶囊放大"), "scale"),
    (("向左滑动", "向右滑动", "向上滑动", "向下滑动", "滚入", "滚出", "随机上升",
      "滑入", "推入", "飞跃", "向右缓入", "向右快速进", "左移", "右移",
      # 日出/日落就是「从下/上方滑入」，方向和幅度已写在 _SLIDE 里：
      # 漏了这两个名字的话它们会掉到淡入淡出族，位移整个消失（14.87s 那条就是这样）
      "日出", "日落"), "slide"),
    (("向右露出", "向左露出", "向上露出", "向下露出", "向右擦除", "向左擦除",
      "向上擦除", "向下擦除", "开幕", "开幕 II", "逐字显影", "打字机",
      "新年打字机", "打字光标", "打字"), "reveal"),
    (("横向模糊", "雪光模糊", "模糊", "发光模糊", "模糊发光", "向左模糊",
      "向右模糊 II", "晕开", "光晕"), "blur"),
    (("圆柱体滚动", "旋转", "翻转", "翻页", "3D翻转"), "rotate"),
    (("镜像翻转",), "flipx"),
    (("抖动下降",), "drop"),
):
    for _n in _names:
        FAMILY[_n] = _fam

# 同族但需要单独幅度的名字（没列到的用族默认）
FAMILY_DEFAULTS: Dict[str, dict] = {
    "轻微放大": {"scale0": 1.06},
    "轻微缩小": {"scale0": 0.94},
    "弹出":     {"scale0": 0.70},
    "跃进":     {"scale0": 1.40},
    "收拢":     {"scale0": 1.35},
    "折叠":     {"scale0": 1.30, "alpha0": 0.2},
    "滚入":     {"scale0": 1.00, "alpha0": 0.4},
    "随机上升": {"scale0": 1.00, "alpha0": 0.0},
    "向右缓入": {"scale0": 1.00, "alpha0": 0.5},
    "雪光模糊": {"blur0": 32.0},
    "向右模糊 II": {"blur0": 32.0},
}

# 无法用整块变换表达的（次原子/逐字/粒子/声波等）：按最近族兜底，并计入 unrealized
APPROX_FALLBACK: Dict[str, str] = {
    "打字光标": "reveal", "逐字显影": "reveal", "打字机": "reveal",
    "新年打字机": "reveal", "开幕": "reveal", "开幕 II": "reveal",
    "冰雪飘动": "blur", "呐喊声波": "blur", "激光雕刻": "reveal",
    "波浪弹入": "scale", "弹簧": "scale", "圆柱体滚动": "rotate",
}

# 这些名字是「未知/装饰性」动效，按淡入淡出兜底，并在报告里计数
FADE_FALLBACK = ("渐显", "渐隐", "闪白", "闪黑")

# 只做整块的淡入淡出（不位移不缩放）
ALPHA_ONLY = ("渐显", "渐隐", "闪白", "闪黑")

_SLIDE = {
    "向左滑动": (-1.0, 0.0),
    "向右滑动": (1.0, 0.0),
    "向上滑动": (0.0, -1.0),
    "向下滑动": (0.0, 1.0),
    "日出": (0.0, -1.0),
    "日落": (0.0, 1.0),
}

# 露出类：值是「运动方向」（屏幕坐标，右+/下+）
_REVEAL = {
    "向右露出": (1.0, 0.0),
    "向左露出": (-1.0, 0.0),
    "向上露出": (0.0, -1.0),
    "向下露出": (0.0, 1.0),
}


@dataclass
class AnimSpec:
    kind: str           # "in" / "out"
    name: str
    duration: float     # 秒
    start: Optional[float] = None   # 相对段起点的秒；None = in 用 0、out 用尾部


@dataclass
class AnimState:
    scale: float = 1.0      # 等比缩放
    scale_x: float = 1.0    # 仅横向（镜像翻转 / 露出）
    scale_y: float = 1.0    # 仅纵向
    dx: float = 0.0        # 像素，右为正
    dy: float = 0.0        # 像素，下为正
    alpha: float = 1.0
    blur: float = 0.0      # 高斯 sigma（像素）

    def sx(self) -> float:
        return self.scale * self.scale_x

    def sy(self) -> float:
        return self.scale * self.scale_y

    def is_identity(self) -> bool:
        return (abs(self.scale - 1.0) < 1e-4 and abs(self.scale_x - 1.0) < 1e-4
                and abs(self.scale_y - 1.0) < 1e-4 and abs(self.dx) < 0.05
                and abs(self.dy) < 0.05 and abs(self.alpha - 1.0) < 1e-3
                and self.blur < 0.05)


def parse_animations(seg: dict, animations_by_id: Dict[str, dict]) -> List[AnimSpec]:
    """把段上挂的入场/出场动画解析成 AnimSpec 列表（按 in → out 顺序）。"""
    specs: List[AnimSpec] = []
    for ref in seg.get("extra_material_refs") or []:
        holder = animations_by_id.get(ref) or {}
        for an in holder.get("animations") or []:
            kind = str(an.get("type") or "").strip()
            name = str(an.get("name") or "").strip()
            dur = float(an.get("duration") or 0) / US
            if not kind or dur <= 1e-4:
                continue
            start = an.get("start")
            start_s = (float(start) / US) if start not in (None, 0) else None
            if kind == "in" and start_s is None:
                start_s = 0.0
            specs.append(AnimSpec(kind, name, dur, start_s))
    specs.sort(key=lambda s: (0 if s.kind == "in" else 1, s.start or 0.0))
    return specs


def family_of(name: str) -> str:
    """动画名 → 行为族。没收录的按「淡入淡出」兜底（调用方应计入 unrealized）。"""
    if name in FAMILY:
        return FAMILY[name]
    if name in APPROX_FALLBACK:
        return APPROX_FALLBACK[name]
    return "fade"


def fallback_names(specs: List[AnimSpec]) -> List[str]:
    """需要如实上报为「兑底/近似」的动画名（每个条目一条 key，调用方拿去计数）。

    分两类：
      anim_fallback:<名>  未收录的名字 → 按淡入淡出兑底（行为可能与原版不同）
      anim_approx:<名>    收录了但只能用最近族近似（逐字/粒子/声波这类次原子动画）
    两者都**真的做了**（不是跳过），只是不假装它们是逐帧 1:1 的。
    """
    out = []
    for sp in specs:
        if sp.name not in FAMILY:
            out.append(f"anim_fallback:{sp.name}")
        elif sp.name in APPROX_FALLBACK:
            out.append(f"anim_approx:{sp.name}")
    return out


def _cfg(name: str, ctx: str | None = None) -> dict:
    fam = family_of(name)
    base = dict(DEFAULTS.get(name) or DEFAULTS.get(fam) or DEFAULTS["渐显"])
    base.update(FAMILY_DEFAULTS.get(name) or {})
    base.update(CALIBRATION.get(name) or {})
    if ctx:
        base.update((CTX_CALIBRATION.get(ctx) or {}).get(name) or {})
    return base


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def _grid_dir(name: str) -> Tuple[float, float]:
    """族里没登记方向的名字：按名字里的方位词推方向，推不出就当「上升」。

    返回的是**运动方向**（屏幕坐标，右+/下+）。
    """
    if "左" in name:
        return (-1.0, 0.0)
    if "右" in name:
        return (1.0, 0.0)
    if "下" in name:
        return (0.0, 1.0)
    return (0.0, -1.0)


def anim_state(specs: List[AnimSpec], t_local: float, seg_dur: float,
               W: int, H: int, ctx: str | None = None) -> AnimState:
    """段内本地时间 t_local（秒）→ 动画修正量。多个动画重叠时按乘法/叠加合成。

    W/H 是「位移的单位尺寸」：视频叠加层传画布宽高（卡片的滑动量是按画布反解的），
    文字层传文本包围盒宽高（剪映文字入场是相对文本框的，拿画布算会把一行字甩出屏幕）。
    ctx 选幅度表：None = 贴纸那套标定（sticker_render 的旧行为），"layer" = 视频/文字层。
    """
    st = AnimState()
    if not specs:
        return st
    for sp in specs:
        cfg = _cfg(sp.name, ctx)
        if sp.kind == "in":
            dur_i = sp.duration * float(cfg.get("in_dur_scale") or 1.0)
            t0 = (sp.start or 0.0) + float(cfg.get("in_start_shift") or 0.0)
            t1 = t0 + dur_i
            p_raw = (t_local - t0) / max(1e-6, dur_i)
            p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
            # 起点权重 = 1 - p，终点归位
            w = 1.0 - p
        else:
            dur_o = sp.duration * float(cfg.get("out_dur_scale") or 1.0)
            if sp.start is not None:
                t1 = sp.start + dur_o
                # 草稿 start 是「相对段起点的偏移」，但剪映实际按「距段尾多久开始」计时
                if t1 > seg_dur:
                    t1 = seg_dur
            else:
                t1 = seg_dur - float(cfg.get("out_start_slack") or 0.0)
            t0 = t1 - dur_o
            p_raw = (t_local - t0) / max(1e-6, dur_o)
            p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
            w = p
        if w <= 0:
            continue
        # 缓动：剪映的入场/出场不是线性，偏移量按 w^k 退化（w=1 → 起点，w=0 → 归位）。
        # k 由原版帧逐帧反解（见 CTX_CALIBRATION 注释），linear = 1。
        # 透明度另走 alpha_ease：实测尺寸与亮度进程不是同一条曲线（缩小是尺寸线性 + 亮度 out-cubic）。
        wa = w ** float(cfg.get("alpha_ease") or 1.0)
        w = w ** float(cfg.get("ease") or 1.0)
        # 缩放：从 cfg.scale0 回 1.0（w=1 时是起点值）
        st.scale *= _lerp(1.0, cfg["scale0"], w)
        # 透明度
        st.alpha *= _lerp(1.0, cfg["alpha0"], wa)
        # 模糊：跟透明度同一条曲线（先亮后清），用线性衰减会「糊到动画结束」
        st.blur = max(st.blur, _lerp(0.0, cfg["blur0"], wa))
        # 「镜像翻转」：横向从 flip0 张开到 1（纵向不动）
        fam = family_of(sp.name)
        if fam == "flipx":
            st.scale_x *= _lerp(1.0, float(cfg.get("flip0") if cfg.get("flip0") is not None else 0.0), w)
        # 「抖动下降」：从画面上方落下（原动画的阻尼抖动只复刻主位移）
        if fam == "drop":
            st.dy -= H * float(cfg.get("drop") or 0.0) * w
        # 位移：入场从反方向进来，出场往运动方向出去（都按「一整帧」算）
        if sp.name in _SLIDE or fam == "slide":
            ux, uy = _SLIDE.get(sp.name) or _grid_dir(sp.name)
        elif sp.name in _REVEAL:
            ux, uy = _REVEAL[sp.name]
        else:
            ux = uy = None
        if ux is not None:
            sign = -1.0 if sp.kind == "in" else 1.0
            gain = float(cfg.get("slide_gain") or 1.0)
            st.dx += sign * ux * W * gain * w
            st.dy += sign * uy * H * gain * w
    # 钳位
    st.alpha = max(0.0, min(1.0, st.alpha))
    st.blur = max(0.0, min(64.0, st.blur))
    return st


def _blur_span(sp: AnimSpec, seg_dur: float, cfg: dict) -> Tuple[float, float]:
    """模糊族动画在段内的时间窗 [t0, t1]（秒，段内本地时间）。和 anim_state 同一套计时。"""
    if sp.kind == "in":
        dur_i = sp.duration * float(cfg.get("in_dur_scale") or 1.0)
        t0 = (sp.start or 0.0) + float(cfg.get("in_start_shift") or 0.0)
        return t0, t0 + dur_i
    dur_o = sp.duration * float(cfg.get("out_dur_scale") or 1.0)
    if sp.start is not None:
        t1 = min(sp.start + dur_o, seg_dur)
    else:
        t1 = seg_dur - float(cfg.get("out_start_slack") or 0.0)
    return t1 - dur_o, t1


def blur_window(specs: List[AnimSpec], seg_dur: float,
                ctx: str | None = "main") -> Optional[Tuple[float, float]]:
    """模糊族动画的合并时间窗（段内本地秒）；没有模糊族动画返回 None。"""
    wins = [_blur_span(sp, seg_dur, _cfg(sp.name, ctx)) for sp in specs
            if family_of(sp.name) == "blur"]
    if not wins:
        return None
    return min(w[0] for w in wins), max(w[1] for w in wins)


def blur_sigmas(specs: List[AnimSpec], t_local: float, seg_dur: float,
                ctx: str | None = "main") -> Tuple[float, float]:
    """模糊族动画 → (sigmaX, sigmaY) 像素值，供主轨段用 gblur=sigma:sigmaV 逐帧烘。

    「横向模糊」是椭圆高斯（x 重 y 轻；ratio 由原版帧两向梯度反解），不是各向同性 gblur。
    """
    sx = sy = 0.0
    for sp in specs:
        if family_of(sp.name) != "blur":
            continue
        cfg = _cfg(sp.name, ctx)
        t0, t1 = _blur_span(sp, seg_dur, cfg)
        p_raw = (t_local - t0) / max(1e-6, t1 - t0)
        p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
        w = (1.0 - p) if sp.kind == "in" else p
        w = w ** float(cfg.get("ease") or 1.0)
        if w <= 0:
            continue
        bx = float(cfg["blur_x0"] if cfg.get("blur_x0") is not None
                   else (cfg.get("blur0") or 0.0))
        ratio = float(cfg["blur_y_ratio"] if cfg.get("blur_y_ratio") is not None else 1.0)
        sx = max(sx, bx * w)
        sy = max(sy, bx * w * ratio)
    return sx, sy


def flash_alpha(specs: List[AnimSpec], t_local: float, seg_dur: float,
                ctx: str | None = "main") -> float:
    """模糊族里的「过曝光」曲线 → 0..1 的白场叠加量（out = (1-a)·in + 255·a）。

    剪映的「横向模糊」入场不只是模糊，还带一层白光：原版 style_21 4.85s 整帧均值 219.9，
    衰减到 5.15s 的 164（无动效基准 162.5），逐帧反解出 a 线性衰减。
    """
    a = 0.0
    for sp in specs:
        if family_of(sp.name) != "blur":
            continue
        cfg = _cfg(sp.name, ctx)
        f0 = float(cfg.get("flash0") or 0.0)
        if f0 <= 0:
            continue
        t0, t1 = _blur_span(sp, seg_dur, cfg)
        p_raw = (t_local - t0) / max(1e-6, t1 - t0)
        p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
        w = (1.0 - p) if sp.kind == "in" else p
        a = max(a, f0 * (w ** float(cfg.get("ease") or 1.0)))
    return max(0.0, min(1.0, a))


def zoom_amp(specs: List[AnimSpec], t_local: float, seg_dur: float,
             ctx: str | None = "main") -> float:
    """模糊族里的「变焦模糊」幅度（0..1，= 采样缩放半径占画幅的比例）。

    径向模糊没有 ffmpeg 滤镜可表达（-vf 只吃单链），所以主轨走 PIL 逐帧烘焙；
    幅度由原版帧反解（见 CTX_CALIBRATION["main"]["横向模糊"]["zoom0"]）。
    """
    amp = 0.0
    for sp in specs:
        if family_of(sp.name) != "blur":
            continue
        cfg = _cfg(sp.name, ctx)
        z0 = float(cfg.get("zoom0") or 0.0)
        if z0 <= 0:
            continue
        t0, t1 = _blur_span(sp, seg_dur, cfg)
        p_raw = (t_local - t0) / max(1e-6, t1 - t0)
        p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
        w = (1.0 - p) if sp.kind == "in" else p
        amp = max(amp, z0 * (w ** float(cfg.get("ease") or 1.0)))
    return max(0.0, min(0.5, amp))


def alpha_ramp(specs: List[AnimSpec], seg_dur: float, ctx: str | None = None):
    """纯透明度动画 → ("in"/"out", 段内起点秒, 时长)；不是纯淡入淡出返回 None。

    纯粹的渐显/渐隐不需要逐帧重绘（ffmpeg 的 fade 滤镜就能做），有位移/缩放/模糊的才要。
    调用方据此选「淡入淡出链」还是「PIL 逐帧图层」，两条路结果一致。
    """
    if len(specs) != 1:
        return None
    sp = specs[0]
    if family_of(sp.name) != "fade":
        return None
    cfg = _cfg(sp.name, ctx)
    if (abs(float(cfg.get("scale0") or 1.0) - 1.0) > 1e-6
            or float(cfg.get("blur0") or 0.0) > 1e-6
            or abs(float(cfg.get("alpha0") or 0.0)) > 1e-6):
        return None
    if sp.kind == "in":
        return ("in", sp.start or 0.0, sp.duration)
    dur_o = sp.duration * float(cfg.get("out_dur_scale") or 1.0)
    if sp.start is not None:
        t1 = min(sp.start + dur_o, seg_dur)
    else:
        t1 = seg_dur - float(cfg.get("out_start_slack") or 0.0)
    return ("out", max(0.0, t1 - dur_o), dur_o)


def summarize(specs: List[AnimSpec]) -> List[str]:
    """给报告用的一行摘要。"""
    out = []
    for sp in specs:
        tag = "入" if sp.kind == "in" else "出"
        start = "" if sp.start is None else f"@{sp.start:.2f}s"
        out.append(f"{tag}:{sp.name}({sp.duration:.2f}s{start})")
    return out


def sweep(name: str, values: Tuple[float, ...], key: str) -> List[dict]:
    """标定用：生成一组覆盖值（配合外部逐值量像素差）。

        for cfg in sweep("缩小", (1.1, 1.15, 1.2, 1.25), "scale0"):
            CALIBRATION["缩小"] = cfg
            ...
    """
    return [{key: v} for v in values]


def _self_test() -> None:
    W, H = 1080, 1920
    # 0) 贴纸宿主（不带 ctx）的标定不能被「layer」那套覆盖掉：sticker_render 依赖它
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.0, 1.0, W, H)
    assert abs(s.scale - 1.3) < 1e-6 and abs(s.alpha) < 1e-6, s
    # 1) 缩小入场：起点放大、结束归位（视频/文字层：1.5 倍 + 淡入）
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.0, 1.0, W, H, ctx="layer")
    assert abs(s.scale - _cfg("缩小", "layer")["scale0"]) < 1e-6, s
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.5, 1.0, W, H, ctx="layer")
    assert abs(s.scale - 1.0) < 1e-6, s
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.5, 1.0, W, H)
    assert abs(s.scale - 1.0) < 1e-6, s
    # 2) 渐隐出场：尾部 alpha 归零
    s = anim_state([AnimSpec("out", "渐隐", 0.5)], 1.0, 1.0, W, H)
    assert s.alpha < 1e-6, s
    s = anim_state([AnimSpec("out", "渐隐", 0.5)], 0.5, 1.0, W, H)
    assert abs(s.alpha - 1.0) < 1e-6, s
    # 3) 向下滑动出场：末帧按标定幅度下移（屏幕坐标 y 正 = 下）
    _g = float(_cfg("向下滑动").get("slide_gain") or 1.0)
    s = anim_state([AnimSpec("out", "向下滑动", 0.5)], 1.0, 1.0, W, H)
    assert abs(s.dy - H * _g) < 1e-6, s
    # 3b) 向下滑动**入场**：起点在画面上方（dy 为负）
    s = anim_state([AnimSpec("in", "向下滑动", 0.5)], 0.0, 1.0, W, H)
    assert abs(s.dy + H * _g) < 1e-6, s
    # 3c) 日出入场：从下方升起（dy = +H）
    s = anim_state([AnimSpec("in", "日出", 0.5)], 0.0, 1.0, W, H)
    assert abs(s.dy - H) < 1e-6, s
    # 3d) 族默认值不能把 alpha 误设成 0（缩放族入场应不透明）
    s = anim_state([AnimSpec("in", "轻微放大", 0.4)], 0.0, 1.0, W, H)
    assert abs(s.alpha - 1.0) < 1e-6 and abs(s.scale - 1.06) < 1e-6, s
    # 4) 区间外不动
    assert anim_state([AnimSpec("in", "缩小", 0.5)], 0.9, 1.0, W, H).is_identity()
    # 6) 镜像翻转入场：起点横向为 0（纵向不动），结尾回 1
    s = anim_state([AnimSpec("in", "镜像翻转", 0.4)], 0.0, 1.0, W, H, ctx="layer")
    assert abs(s.sx()) < 1e-6 and abs(s.sy() - 1.0) < 1e-6 and abs(s.alpha - 1.0) < 1e-6, s
    s = anim_state([AnimSpec("in", "镜像翻转", 0.4)], 0.4, 1.0, W, H, ctx="layer")
    assert abs(s.sx() - 1.0) < 1e-6, s
    # 6b) 缓动不是线性：镜像翻转 p=0.5 已到 0.875（out-cubic），线性只到 0.5
    s = anim_state([AnimSpec("in", "镜像翻转", 0.4)], 0.2, 1.0, W, H, ctx="layer")
    assert s.sx() > 0.8, s
    # 7) 抖动下降入场：起点在画面上方且略缩小
    s = anim_state([AnimSpec("in", "抖动下降", 0.4)], 0.0, 1.0, W, H, ctx="layer")
    assert s.dy < -0.1 * H and abs(s.scale - 0.78) < 1e-6, s
    # 8) 向左滑动入场起点必须整张推出画布外（否则「叠了个寂寞」的反面：没滑）
    s = anim_state([AnimSpec("in", "向左滑动", 0.333)], 0.0, 1.0, W, H, ctx="layer")
    assert s.dx >= W * 1.1, s
    # 8b) 主轨「横向模糊」入场：白场从 0.65 线性衰减到 0，窗后为 0
    _sp = [AnimSpec("in", "横向模糊", 0.2667)]
    assert abs(flash_alpha(_sp, 0.0, 1.0) - 0.65) < 1e-6
    assert abs(flash_alpha(_sp, 0.2667 / 2, 1.0) - 0.325) < 0.01
    assert flash_alpha(_sp, 0.4, 1.0) == 0.0
    assert blur_window(_sp, 1.0) == (0.0, 0.2667)
    # 8c) 各向同性/椭圆 gblur 要显式关掉，否则会多糊一层
    assert blur_sigmas(_sp, 0.0, 1.0) == (0.0, 0.0)
    # 5) 无动画 = 恒等
    assert anim_state([], 0.3, 1.0, W, H).is_identity()
    print("animations.py self-test OK")


if __name__ == "__main__":
    _self_test()
