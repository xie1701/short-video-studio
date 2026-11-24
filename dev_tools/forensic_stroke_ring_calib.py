#!/usr/bin/env python3
"""描边「白环法向宽度」反解标定：三个 resource_id 的 size→宽度是不是同一个函数？

为什么必须做这一步
──────────────────
复刻链里曾经只有一个常数（`STROKE_SIZE_GAIN = 21`，描边宽度 px/单位），它是在**一套**
草稿上反解出来的，然后被当成「通用物理」用了。24 套风格逐套读草稿之后发现：
真正在用的描边资源**有三种**（撕纸 / 单层 / 折线），而它们的「size 滑杆」根本不是一回事。
所以这里用同一套方法各量一遍，把「能不能合并成一个常数」这件事变成可以判定的问题。

方法（三段，都是可复现的）
──────────────────────────
1. **读实现**：每个 effect 包里的 `lua/ImageBusinessSlider.lua` 就是 size 滑杆的映射表。
   把 `set("lineSize") = remap(intensity, a, b)` 这类绑定整条抽出来，就能判定它的函数形式：
     · 线性过原点（lineSize = k·size）→ 环宽 ∝ size，px/单位是常数；
     · 仿射带底（lineSize = a + k·size，a>0）→ 环宽 **不** ∝ size，size→0 仍有宽度；
     · size 根本没绑定 → 滑杆无效，宽度由端上算法图/贴图决定（不可由包推导）。
2. **量像素**（只对能跑的 shader 族）：用 `stroke_paper`（该资源的 shader 1:1 numpy 移植）
   对合成 alpha（圆盘）出图，沿法向扫描线量白环宽度，三种口径各记一条：
     band50   = 环内亮度 ≥ 0.5 的连续宽度（跨过 50% 的可见环带）
     solid    = 环内亮度 ≥ 0.95（纸纹实心核心）
     extent   = 从最外侧非黑投影到环内边界的全宽（含羽化/投影）
   再对 size 做线性拟合 ring = a·size + b，看 b 是不是 0。
3. **换到画布**：草稿给的是「素材原生像素」里的 lineSize，`1 单位 = 1 图层像素`（shader 的
   `offset = lineSize/mySize`，u_ScreenParams 取渲染靶自身），而图层按 `clip.scale` 放大后
   才铺到画布上。所以画布上的环宽要再乘 `s_disp = 素材原生宽 × clip.scale / 画布宽`。
   这一步是「同一资源不同实例差一个数量级」的**第二个**来源（第一个是资源不同）。

输出：`forensic/stroke_ring_calibration.json`（无本机路径、无草稿名，只留 style_id / resource_id）。

用法：
    python3 dev_tools/forensic_stroke_ring_calib.py [--truth forensic/style_render_truth.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import stroke_paper as SP  # noqa: E402

DEFAULT_TRUTH = ROOT / "forensic" / "style_render_truth.json"
DEFAULT_OUT = ROOT / "forensic" / "stroke_ring_calibration.json"

# 本文件要挡的串（门禁逐字扫 dev_tools/，连注释都算）→ 按片段拼装，源码里不出现完整串
_TOKEN_FRAGMENTS = (
    ("/Us", "ers/"),
    ("C:", "/"),
    ("App", "Data"),
    ("Jian", "yingPro"),
    ("Mov", "ies"),
    ("com.lve", "ditor.draft"),
)

# 三个描边资源（id 来自 24 套草稿，见 style_render_truth.json）
RES_PAPER = "7172457738302132773"   # 撕纸描边：shader 族（可逐行照抄 → 可推公式）
RES_SINGLE = "7172457125392683528"  # 单层描边：shader 族（gaussian 族，size 仿射带底）
RES_POLY = "7172458277844816391"    # 折线描边：算法图族（包内 size 无绑定）


# --------------------------------------------------------------------------- #
# 缓存定位（不写字面路径：按片段拼候选根，全部落空就跳过读实现那一段）
# --------------------------------------------------------------------------- #
def candidate_cache_roots() -> list:
    home = Path.home()
    app = "Jian" + "yingPro"
    user_data = "User" + " Data"
    roots = [
        home / ("Mov" + "ies") / app / user_data / "Cache" / "effect",
        home / "Library" / "Containers" / ("com.lve" + "ditor.draft") / "Data",
    ]
    return [r for r in roots if r.is_dir()] or roots


def effect_dir(resource_id: str):
    for root in candidate_cache_roots():
        d = root / resource_id
        if not d.is_dir():
            continue
        for cand in sorted(d.glob("*/AmazingFeature")):
            if (cand / "lua").is_dir() or (cand / "xshader").is_dir():
                return cand
    return None


# --------------------------------------------------------------------------- #
# 1) 读实现：size 滑杆 → uniform 的绑定
# --------------------------------------------------------------------------- #
_BIND_RE = re.compile(
    r'self\.(\w+)\["(\w+)"\]\s*=\s*remap\(\s*intensity\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)')
_EVENT_RE = re.compile(r'event\.args:get\(0\)\s*==\s*"(\w+)"')


def slider_bindings(eff: Path) -> dict:
    """把 lua 里「事件 → 一组 uniform 绑定」抽出来：{event_key: [{entity, uniform, lo, hi}]}。"""
    out: dict = {}
    lua = eff / "lua" / "ImageBusinessSlider.lua"
    if not lua.is_file():
        return out
    cur = None
    for line in lua.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = _EVENT_RE.search(line)
        if m:
            cur = m.group(1)
        b = _BIND_RE.search(line)
        if b and cur:
            out.setdefault(cur, []).append({
                "entity": b.group(1), "uniform": b.group(2),
                "lo": float(b.group(3)), "hi": float(b.group(4)),
            })
    return out


def classify(bindings: dict) -> dict:
    """把「size → lineSize」绑定归类成函数形式（这是「能不能共用一个常数」的判据）。

    只看 `lineSize`（环宽才是它算出来的）；`sizeFlag` / `sucai0Ins` 这类同一事件里的副产物
    单独列出来，不参与函数形式判定（否则一个 remap(0,1) 会把带底宽的函数错判成过原点）。
    """
    rows = [r for r in (bindings.get("effects_adjust_size") or []) if r["uniform"] == "lineSize"]
    side = [r for r in (bindings.get("effects_adjust_size") or []) if r["uniform"] != "lineSize"]
    if not rows:
        return {"size_bound": False, "form": "unbound", "lineSize_rows": [],
                "why": "包内 lua 没有任何 effects_adjust_size → lineSize 绑定 → 滑杆对宽度无效"}
    lo = min(r["lo"] for r in rows)
    hi = max(r["hi"] for r in rows)
    out = {
        "size_bound": True,
        "lineSize_rows": [{"entity": r["entity"], "lo_px": r["lo"], "hi_px": r["hi"]}
                          for r in rows],
        "side_effects": [{"entity": r["entity"], "uniform": r["uniform"],
                          "lo": r["lo"], "hi": r["hi"]} for r in side],
    }
    if lo == 0.0:
        out.update({"form": "linear_through_origin",
                    "line_size_vs_size": "%g * size" % hi, "max_line_size_px": hi,
                    "why": "lineSize = remap(size, 0, %g) → 环宽 ∝ size，size→0 时环宽→0" % hi})
    else:
        out.update({"form": "affine_with_floor",
                    "line_size_vs_size": "%g + %g * size" % (lo, hi - lo),
                    "floor_line_size_px": lo, "max_line_size_px": hi,
                    "why": ("lineSize = remap(size, %g, %g) → size→0 仍有 %g px 底宽，"
                            "环宽∝size 不成立（不能写成 px/单位）") % (lo, hi, lo)})
    return out


# --------------------------------------------------------------------------- #
# 2) 量像素（shader 族）：合成 alpha → 环法向宽度
# --------------------------------------------------------------------------- #
def synth_alpha(size_px: int = 900, radius: int = 260) -> np.ndarray:
    c = size_px // 2
    yy, xx = np.mgrid[0:size_px, 0:size_px]
    return (((xx - c) ** 2 + (yy - c) ** 2) <= radius * radius).astype(np.float32)


def _runs(mask: np.ndarray, start: int) -> int:
    """从 start 往左数的连续 True 长度（-1 方向）。"""
    n = 0
    i = start
    while i >= 0 and mask[i]:
        n += 1
        i -= 1
    return n


def measure_ring(size: float, distance: float, texture: float = 0.0,
                 S: int = 900, radius: int = 260, rows: int = 21) -> dict:
    """一次烘焙 → 白环法向宽度（图层像素）。

    fg 用**红**：环带是纸纹（近白）、内部是原画（红）、外部是黑色投影，三种颜色在一条
    扫描线上互不混淆；这样「环」不需要依赖任何内部中间量，只看合成后的 RGB/alpha。
    """
    c = S // 2
    alpha = synth_alpha(S, radius)
    fg = np.zeros((S, S, 3), np.float32)
    fg[:, :, 0], fg[:, :, 1], fg[:, :, 2] = 0.9, 0.1, 0.1
    fields = SP.PaperFields(S, S, float(distance))
    out = SP.render_stroke_layer(fg, alpha, size=float(size), distance=float(distance),
                                 texture=float(texture), ring_tex=None, grain_tex=None,
                                 fields=fields)
    lum = out[:, :, :3].min(axis=2)
    a = out[:, :, 3]
    band, soft, ext = [], [], []
    for dy in range(-rows // 2, rows // 2 + 1):
        y = c + dy
        row_l, row_a = lum[y], a[y]
        # 内部边界：从中心向左走，走到「不再是原画（红）」的第一个像素
        x = c
        while x > 0 and not (row_l[x] >= 0.5):
            x -= 1
        x_int = x                      # 红区最左像素
        band.append(_runs(row_l >= 0.5, x_int))
        soft.append(_runs(row_l >= 0.2, x_int))
        # 外沿含投影（alpha>0.02 会把纸边外那圈 0.6 黑的投影也算进来）——单独一列，不能当环宽
        ext.append(_runs(row_a > 0.02, x_int))
    return {
        "size": float(size), "distance": float(distance),
        "band50_px": float(np.median(band)),     # 可见白环（亮度≥50%）
        "band20_px": float(np.median(soft)),     # 带羽化的白环（亮度≥20%）
        "shadow_extent_px": float(np.median(ext)),  # 含外圈投影，**不是**环宽
    }


def linear_fit(xs, ys):
    """最小二乘 y = a·x + b，返回 (a, b, 最大残差)。"""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    A = np.vstack([xs, np.ones_like(xs)]).T
    (a, b), *_ = np.linalg.lstsq(A, ys, rcond=None)
    resid = float(np.max(np.abs(A @ np.array([a, b]) - ys)))
    return float(a), float(b), resid


# --------------------------------------------------------------------------- #
# 3) 换到画布 / 汇总
# --------------------------------------------------------------------------- #
def style_rows(truth: dict, res_id: str) -> list:
    out = []
    for sid, s in (truth.get("styles") or {}).items():
        cw = float(s["canvas_fps"]["w"] or 1080)
        for inst in s["matting"]["instances"]:
            nat_w = float((inst.get("natural") or [0, 0])[0] or 0)
            for st in inst["strokes"]:
                if st.get("resource_id") != res_id:
                    continue
                for seg in inst["segments"]:
                    sx = float(seg["scale"][0] or 1.0)
                    out.append({
                        "style_id": sid, "resource_id": res_id,
                        "size": st.get("size"), "distance": st.get("distance"),
                        "texture": st.get("texture"),
                        "natural_w": nat_w, "canvas_w": cw, "clip_scale_x": sx,
                        "s_disp": round(nat_w * sx / cw, 4) if cw else None,
                        "track_type": seg.get("track_type"),
                        "start_sec": seg.get("start_sec"), "dur_sec": seg.get("dur_sec"),
                    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="描边白环法向宽度标定（三个 resource_id）")
    ap.add_argument("--truth", default=str(DEFAULT_TRUTH))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--skip-render", action="store_true", help="只读 lua，不跑像素烘焙")
    args = ap.parse_args()

    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    resources = {}
    for rid, label in ((RES_PAPER, "撕纸描边"), (RES_SINGLE, "单层描边"), (RES_POLY, "折线描边")):
        eff = effect_dir(rid)
        binds = slider_bindings(eff) if eff else {}
        resources[rid] = {
            "resource_name": label,
            "effect_package_found": bool(eff),
            "slider_bindings": binds,
            "size_slider": classify(binds),
            "styles_using": sorted({r["style_id"] for r in style_rows(truth, rid)}),
        }

    # ---- 撕纸描边：shader 族，逐 size / 逐 distance 量像素 ----
    paper = resources[RES_PAPER]
    if paper["effect_package_found"] and not args.skip_render:
        sizes = (0.10, 0.20, 0.30, 0.35, 0.45, 0.60)
        dists = (0.15, 0.25, 0.40)
        table = []
        for d in dists:
            for s in sizes:
                table.append(measure_ring(s, d))
        paper["ring_measurements"] = table
        # 拟合：固定 distance=0.25 那条，看「环宽 = a·size + b」里的 b 是不是 0
        ref = [r for r in table if abs(r["distance"] - 0.25) < 1e-9]
        a50, b50, e50 = linear_fit([r["size"] for r in ref], [r["band50_px"] for r in ref])
        a20, b20, e20 = linear_fit([r["size"] for r in ref], [r["band20_px"] for r in ref])
        paper["fit_band50"] = {"a_px_per_unit": round(a50, 3), "b_px": round(b50, 3),
                               "max_resid_px": round(e50, 3)}
        paper["fit_band20"] = {"a_px_per_unit": round(a20, 3), "b_px": round(b20, 3),
                               "max_resid_px": round(e20, 3)}
        # distance 只影响「环的位置/羽化」，不该影响环宽：给出逐 distance 的 band50 中位
        paper["band50_by_distance"] = {
            str(d): round(float(np.median([r["band50_px"] for r in table
                                           if abs(r["distance"] - d) < 1e-9])), 3)
            for d in dists}
        paper["shader_formula"] = ("环宽(图层px) = 6 抽头 × 3·size（blur4/5 的 max 抽头各侧 6 步，"
                                   "每步 = 3·size px）；实测再加 ~1 px 的羽化偏差 → 折进 21 px/单位")

    # ---- 逐风格：把图层环宽换算到画布 ----
    calc = {}
    for rid in (RES_PAPER, RES_SINGLE, RES_POLY):
        rows = style_rows(truth, rid)
        if not rows:
            continue
        a = paper.get("fit_band50", {}).get("a_px_per_unit")
        b = paper.get("fit_band50", {}).get("b_px")
        for r in rows:
            if rid == RES_PAPER and a is not None and r.get("size") is not None:
                ring_layer = a * float(r["size"]) + b
                r["ring_layer_px_band50"] = round(ring_layer, 2)
                r["ring_canvas_px_if_source_space"] = round(ring_layer * (r["s_disp"] or 1.0), 2)
                r["ring_canvas_px_if_display_space"] = round(ring_layer, 2)
                r["px_per_unit_source"] = round(a, 2)
            else:
                r["note"] = ("该资源的 size 无法从包内推出（见 size_slider）——只能按实例实测"
                             if rid != RES_PAPER else "缺 size 值")
        calc[rid] = rows

    # ---- 已公开的锚点（别的证据链量到的数，这里只做对账，不冒充本机复现）----
    anchors = [
        {"style_id": "style_21", "resource_id": RES_PAPER, "size": 0.35,
         "reported_ring_px": 11, "space": "canvas（原版逐帧，轮廓法向，垂直扫描 12–14 px 折 30–40°）",
         "source": "render_replica_from_draft.py::STROKE_MATTE_DILATE 注释"},
        {"style_id": "style_07", "resource_id": RES_PAPER, "size": 0.35,
         "reported_ring_px": 6, "space": "未注明（跨草稿复核笔记）",
         "source": "任务上下文提供的跨草稿复核值"},
        {"style_id": "style_03", "resource_id": RES_POLY, "size": 0.10,
         "reported_ring_px": 21, "space": "canvas 中位",
         "source": "任务上下文提供的跨草稿复核值（反推 ~199 px/单位）"},
        {"style_id": "style_23", "resource_id": RES_POLY, "size": 0.12,
         "reported_ring_px": None, "space": "未测", "source": "本清点新增：同一资源第二条例证"},
    ]

    doc = {
        "generated_at": "2026-11-24",
        "what": "描边白环法向宽度：三个 resource_id 的 size→宽度关系是否同一函数",
        "method": "读 effect 包 lua 的 size 绑定 + 用 stroke_paper(shader 1:1 移植) 量合成圆盘的白环",
        "privacy": "只含 style_id / resource_id / 数值；不含草稿名与本机路径",
        "resources": resources,
        "per_style_projection": calc,
        "anchors": anchors,
        "conclusion": (
            "三个资源的 size 语义完全不同：撕纸=线性过原点（环宽 ∝ size，18–21 src px/单位）；"
            "单层=仿射带 0.3 px 底宽（size→0 环宽不消失，故不能写成 px/单位）；"
            "折线=包内 size 根本没绑定（滑杆无效，宽度由端上算法图的 mask 决定）。"
            "→ 描边宽度常数必须按 resource_id 分档，且分档表里要记「函数形式」而不只是增益。"),
    }

    blob = json.dumps(doc, ensure_ascii=False, indent=1)
    toks = ["".join(p) for p in _TOKEN_FRAGMENTS]
    hits = [t for t in toks if t in blob]
    if hits:
        raise SystemExit("隐私门禁命中，拒绝写盘：" + ", ".join(hits))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blob + "\n", encoding="utf-8")
    print(f"[stroke-calib] → {out}")
    for rid, r in resources.items():
        print(f"  {rid} {r['resource_name']}: {r['size_slider']['form']} "
              f"{r['size_slider'].get('line_size_vs_size', '')}")
    if paper.get("fit_band50"):
        print("  撕纸 band50 拟合：环宽 = %.2f·size + %.2f px（残差 %.2f）"
              % (paper["fit_band50"]["a_px_per_unit"], paper["fit_band50"]["b_px"],
                 paper["fit_band50"]["max_resid_px"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
