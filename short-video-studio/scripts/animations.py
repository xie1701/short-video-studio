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
}

# 用原版帧反解出来的覆盖值（键 = 动画名，字段同 DEFAULTS）。留空即全用 DEFAULTS。
CALIBRATION: Dict[str, dict] = {
    # 用原版帧反解的「向下滑动」出场（style_21 手机边框）：草稿写 duration=0.5，
    # 但实测 12.45s 还是静止、12.85s 已完全移出 → 真实时长约 0.6×，起点比「段尾减时长」晚约 0.08s。
    "向下滑动": {"slide_gain": 0.8, "out_dur_scale": 0.6, "out_start_slack": 0.08},
    # 「缩小」入场（同一处手机边框）：14 组参数扫下来 a0=0 s0=1.3 最优——
    # 起手是「从大处淡入」，10.45s 原版几乎还看不见边框，所以起点透明度必须是 0。
    "缩小": {"scale0": 1.3, "alpha0": 0.0},
}

# 全库 113 套草稿里共 150 个动画名、2332 条。按行为归族比逐个硬编码可维护：
# 同族共享一套数学，族内个别名字用 DEFAULTS 单独调幅度。
FAMILY: Dict[str, str] = {}
for _names, _fam in (
    (("渐显", "渐隐", "闪白", "闪黑", "暗场", "亮场", "淡入", "淡出"), "fade"),
    (("缩小", "放大", "轻微放大", "轻微缩小", "弹出", "跃进", "收拢", "折叠",
      "生长", "生长 II", "生长III", "弹性放大", "胶囊放大"), "scale"),
    (("向左滑动", "向右滑动", "向上滑动", "向下滑动", "滚入", "滚出", "随机上升",
      "滑入", "推入", "飞跃", "向右缓入", "向右快速进", "左移", "右移"), "slide"),
    (("向右露出", "向左露出", "向上露出", "向下露出", "向右擦除", "向左擦除",
      "向上擦除", "向下擦除", "开幕", "开幕 II", "逐字显影", "打字机",
      "新年打字机", "打字光标", "打字"), "reveal"),
    (("横向模糊", "雪光模糊", "模糊", "发光模糊", "模糊发光", "向左模糊",
      "向右模糊 II", "晕开", "光晕"), "blur"),
    (("圆柱体滚动", "旋转", "翻转", "翻页", "3D翻转"), "rotate"),
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
    scale: float = 1.0
    dx: float = 0.0        # 像素，右为正
    dy: float = 0.0        # 像素，下为正
    alpha: float = 1.0
    blur: float = 0.0      # 高斯 sigma（像素）

    def is_identity(self) -> bool:
        return (abs(self.scale - 1.0) < 1e-4 and abs(self.dx) < 0.05
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


def _cfg(name: str) -> dict:
    fam = family_of(name)
    base = dict(DEFAULTS.get(name) or DEFAULTS.get(fam) or DEFAULTS["渐显"])
    base.update(FAMILY_DEFAULTS.get(name) or {})
    base.update(CALIBRATION.get(name) or {})
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
               W: int, H: int) -> AnimState:
    """段内本地时间 t_local（秒）→ 动画修正量。多个动画重叠时按乘法/叠加合成。"""
    st = AnimState()
    if not specs:
        return st
    for sp in specs:
        cfg = _cfg(sp.name)
        if sp.kind == "in":
            t0, t1 = sp.start or 0.0, (sp.start or 0.0) + sp.duration
            p_raw = (t_local - t0) / max(1e-6, sp.duration)
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
        # 缩放：从 cfg.scale0 线性回到 1.0（w=1 时是起点值）
        st.scale *= _lerp(1.0, cfg["scale0"], w)
        # 透明度
        st.alpha *= _lerp(1.0, cfg["alpha0"], w)
        # 模糊
        st.blur = max(st.blur, _lerp(0.0, cfg["blur0"], w))
        # 位移：入场从反方向进来，出场往运动方向出去（都按「一整帧」算）
        fam = family_of(sp.name)
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
    # 1) 缩小入场：起点放大、结束归位
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.0, 1.0, W, H)
    assert abs(s.scale - _cfg("缩小")["scale0"]) < 1e-6, s
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
    # 5) 无动画 = 恒等
    assert anim_state([], 0.3, 1.0, W, H).is_identity()
    print("animations.py self-test OK")


if __name__ == "__main__":
    _self_test()
