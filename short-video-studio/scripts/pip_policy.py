#!/usr/bin/env python3
"""抠像 / 画中画到底该套在哪几镜——**内容门槛**，不是按风格名字一刀切。

为什么要有这个模块（2026-11-24 用户实测，原话）：
「这个片子其实很多地方不适合抠像（比如 5-10 秒中，讲解效果图的地方，都把讲解图给扣没了）」。

根因不是抠像算法坏了，而是**套用端把风格里的「抠像能力」当成了每一镜都必须执行的指令**：
style_21 的真值里主轨 13 段有 8 段推近、叠加轨 7 段带抠像，于是复刻端给 15 个 A-roll 镜头
全部挂了 `geometry_pip_main_punch_in + cutout=True`，渲染端见 `cutout` 就去跑人像抠像合成
（满屏真人 + 副镜背景 + 纯色描边）。结果：主持人在讲一张拿在手上的效果图，图被抠掉了，
只剩一双带白边的手。

一条风格模版该带走的是「这个风格会不会用抠像、用多密」，而不是「每一镜都抠」。
所以这里给三道上限，任何一道不过就退化成「只推近、不抠像」：

1. **画面里得有脸**（`face_anchor` 存在且有足够面积）——手部特写、图纸特写、航拍里根本没有可抠的人像；
2. **画面里不能正在展示「要看的东西」**——抽帧判读的描述里出现图纸/效果图/屏幕/展板/菜单/样张等，
   抠像会把观众唯一该看的东西一起抠掉；
3. **密度与节奏**——`max_ratio_of_aroll` 与 `min_gap_sec` 按源草稿的真实密度定，
   不许把「偶尔一用的技法」变成「每一镜都在抠」。

所有门槛都写进 EDL 的 `cutout_gate`，每镜写明放行或降级的理由，事后能逐条核。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── 默认门槛（风格模版可在 pip_composition_spec.cutout_policy 里覆盖）─────────
DEFAULT_POLICY: Dict[str, Any] = {
    "enabled": True,
    # 人脸至少占画面这个比例——太小说明人在远处，抠出来也是一团糊
    "require_face_min_h": 0.12,
    "require_face_min_w": 0.07,
    # 抽帧判读描述里出现这些词 = 画面里有「必须被看见的东西」，抠像会把它们抠没
    "forbid_visual_terms": [
        "效果图", "图纸", "设计图", "平面图", "施工图", "手绘", "白板", "展板", "样品",
        "样张", "文件", "合同", "菜单", "价目表", "平板", "iPad", "手机屏", "电脑屏",
        "屏幕", "投屏", "PPT", "讲义", "报告", "证件", "证书", "表格",
    ],
    # 抠像最多占 A-roll 镜头的比例，以及与上一处抠像的最小间隔
    "max_ratio_of_aroll": 0.45,
    "min_gap_sec": 4.0,
}

_CUTOUT_MODES_PREFIX = ("geometry_pip",)
_CUTOUT_MODES_EXACT = ("vision_portrait_cutout_pip", "data_news_cutout_pip")


def _norm_name(p: Any) -> str:
    """归一化文件名：去掉 `norm_07_` 这种加工前缀与扩展名。

    剪辑表里的 `media_path` 指向 `01_normalized/norm_16_2026-10-22 152722.mp4`，
    而抽帧判读索引里的键是原始文件名 `2026-10-22 152722.mov`。不归一化就对不上，
    门槛会误以为「没有画面依据」——2026-11-24 第一版就是这么错的。
    """
    s = str(p or "").strip()
    if not s:
        return ""
    name = Path(s).name
    name = re.sub(r"^norm_\d+_", "", name)
    return Path(name).stem


def load_visual_texts(pipeline_dir: Path) -> Dict[str, str]:
    """读抽帧判读索引（`00_visual_index.json` / `00_inventory.json`），拼成「文件名 → 画面描述文本」。

    判读索引是唯一能说明「这一镜画面里在干什么」的依据；没有它就只能按人脸面积判，
    所以缺判读时本模块会如实把 `basis` 记成 `face_only`。
    """
    import json

    texts: Dict[str, str] = {}
    for fname in ("00_visual_index.json", "00_inventory.json"):
        p = Path(pipeline_dir) / fname
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        clips = data.get("clips") or data.get("items") or []
        for c in clips:
            if not isinstance(c, dict):
                continue
            names = [c.get("filename"), c.get("file"), c.get("name"), c.get("normalized_mp4")]
            txt = " ".join(
                str(x) for x in (
                    c.get("description"),
                    " ".join(str(t) for t in (c.get("semantic_tags") or [])),
                    " ".join(str(t) for t in (c.get("scene_tags_zh") or [])),
                    c.get("note"),
                    c.get("content"),
                    c.get("content_type"),
                ) if x
            )
            for n in names:
                k = _norm_name(n)
                if k and txt:
                    texts[k] = (texts.get(k, "") + " " + txt).strip()
    return texts


def _terms_hit(text: str, terms: List[str]) -> Optional[str]:
    low = str(text or "")
    for t in terms:
        t = str(t or "").strip()
        if not t:
            continue
        if t.isascii():
            if t.lower() in low.lower():
                return t
        elif t in low:
            return t
    return None


def decide(
    clip: Dict[str, Any],
    policy: Dict[str, Any],
    visual_text: str,
    face_anchor: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """单镜判定。返回 `{allowed, reason, basis}`——理由要能直接读给人听。"""
    pol = {**DEFAULT_POLICY, **(policy or {})}
    if not pol.get("enabled", True):
        return {"allowed": False, "reason": "风格未启用抠像", "basis": "policy"}

    if not face_anchor:
        return {"allowed": False, "reason": "画面里没检出人脸（手部/图纸/空镜不适合抠像）", "basis": "face"}

    fh = float(face_anchor.get("h") or 0.0)
    fw = float(face_anchor.get("w") or 0.0)
    if fh < float(pol.get("require_face_min_h") or 0.0) or fw < float(pol.get("require_face_min_w") or 0.0):
        return {
            "allowed": False,
            "reason": f"人脸太小（高 {fh:.0%} / 宽 {fw:.0%}），抠出来没有信息量",
            "basis": "face",
        }

    hit = _terms_hit(visual_text, list(pol.get("forbid_visual_terms") or []))
    if hit:
        return {
            "allowed": False,
            "reason": f"画面里在给人看「{hit}」，抠像会把要看的东西一起抠掉",
            "basis": "visual_index",
        }

    return {
        "allowed": True,
        "reason": "真人出镜、画面没有需要展示的物件",
        "basis": "visual_index" if visual_text else "face_only",
    }


def is_cutout_clip(clip: Dict[str, Any]) -> bool:
    lay = clip.get("pip_layout")
    if not isinstance(lay, dict):
        return False
    if not lay.get("cutout"):
        return False
    mode = str(lay.get("mode") or "")
    return mode in _CUTOUT_MODES_EXACT or mode.startswith(_CUTOUT_MODES_PREFIX)


def apply_cutout_gate(
    clips: List[Dict[str, Any]],
    style_entry: Optional[Dict[str, Any]],
    pipeline_dir: Path,
    post_decide: Optional[Callable[[Dict[str, Any], float], Optional[str]]] = None,
) -> Dict[str, Any]:
    """在剪辑表上批量执行抠像门槛：不合规的「抠像」降级为「只推近/只摆窗口」，并逐镜记理由。

    必须在人脸锚点算完之后调用（`face_anchor` 是本模块的第一道依据）。

    `post_decide(clip, timeline_in_sec) -> Optional[str]`：内容门槛放行之后的**第二道判定**，
    由 `treatment_policy` 提供（风格密度预算 / 台词是否在指认画面 / 单段上限…）。
    返回字符串 = 改判为「不抠」，该字符串就是逐镜理由。降级动作仍只在这里执行，
    所以「改判」和「降级」不会各写一套逻辑。
    """
    spec = (style_entry or {}).get("pip_composition_spec") or {}
    policy = dict(spec.get("cutout_policy") or {})
    pol = {**DEFAULT_POLICY, **policy}
    texts = load_visual_texts(pipeline_dir)

    a_rolls = [c for c in clips if str(c.get("track_role")) == "a_roll"]
    max_n = max(1, int(round(len(a_rolls) * float(pol.get("max_ratio_of_aroll") or 1.0))))
    min_gap = float(pol.get("min_gap_sec") or 0.0)

    rows: List[Dict[str, Any]] = []
    allowed_clips: List[Dict[str, Any]] = []
    allowed = 0
    last_tin = -1e9
    for c in clips:
        if not is_cutout_clip(c):
            continue
        lay = c["pip_layout"]
        tin = float(c.get("timeline_in_sec") or 0.0)
        vtext = texts.get(_norm_name(c.get("media_path")), "")
        verdict = decide(c, pol, vtext, lay.get("face_anchor"))
        if verdict["allowed"] and post_decide is not None:
            extra = post_decide(c, tin)
            if extra:
                verdict = {"allowed": False, "basis": "treatment_policy", "reason": extra}
        if verdict["allowed"]:
            if allowed >= max_n:
                verdict = {"allowed": False, "basis": "cap",
                           "reason": f"抠像已达风格上限（{max_n} 镜 / A-roll {len(a_rolls)} 镜）"}
            elif min_gap > 0 and tin - last_tin < min_gap:
                verdict = {"allowed": False, "basis": "gap",
                           "reason": f"距上一个抠像镜只有 {tin - last_tin:.1f}s，风格密度是 ≥{min_gap:.0f}s"}
        row = {
            "clip_id": c.get("clip_id"),
            "timeline_in_sec": round(tin, 3),
            "sentence_id": c.get("sentence_id"),
            "media": _norm_name(c.get("media_path")),
            "face": None if not lay.get("face_anchor") else {
                "h": round(float(lay["face_anchor"].get("h") or 0.0), 4),
                "w": round(float(lay["face_anchor"].get("w") or 0.0), 4),
            },
            "visual": vtext[:60],
            "allowed": bool(verdict["allowed"]),
            "reason": verdict["reason"],
            "basis": verdict["basis"],
        }
        rows.append(row)
        if verdict["allowed"]:
            allowed += 1
            last_tin = tin
            allowed_clips.append(c)
            continue
        # 降级：保留风格的真值几何（推近/窗口），但不再跑人像抠像
        lay["cutout"] = False
        lay["cutout_gated_reason"] = verdict["reason"]
        wins = lay.get("windows") or []
        # 真草稿里的「窗口」有两类：`scale≈1.0` 的是**满屏叠加层**（不是小窗），
        # 真正的画中画窗口 `scale` 明显小于 1。不抠像之后，满屏叠加层会把真人整条盖住，
        # 所以这里只留真窗口；没有真窗口就彻底退回「主轨推近」单源。
        real_wins = [w for w in wins if isinstance(w, dict) and float(w.get("scale") or 1.0) < 0.92]
        lay["windows"] = real_wins
        for w in real_wins:
            w["matting"] = False
            w["stroke_hex"] = None
        if not real_wins:
            lay.pop("secondary_media_path", None)
            lay.pop("secondary_in_sec", None)
            lay.pop("secondary_out_sec", None)
            lay.pop("secondary_face_anchor", None)
        # 内容优先：这一镜里正给人看图纸/屏幕（或者根本没有可抠的人）——不许再推近，
        # 1.2 倍推近会把他手上那张图的两边裁掉，而观众要看的就是那张图。
        if verdict["basis"] in ("visual_index", "face"):
            main = lay.get("main")
            if isinstance(main, dict):
                # 注意：pip_spec 里嵌套的 main/windows 是**共享对象**（step4 用 `{**pip_spec}` 浅拷贝挂上去的），
                # 直接改 main["scale"] 会把整条片子里所有镜头的几何一起改掉（2026-11-24 实测踩到）。
                lay["main"] = {**main, "scale": 1.0, "x": 0.0, "y": 0.0}
                lay["geometry_downgraded"] = "内容优先：不推近、不抠像，整张画面原样给完"

    stats = {
        "policy": {
            "require_face_min_h": pol.get("require_face_min_h"),
            "require_face_min_w": pol.get("require_face_min_w"),
            "max_ratio_of_aroll": pol.get("max_ratio_of_aroll"),
            "max_cutout_clips": max_n,
            "min_gap_sec": min_gap,
            "forbid_visual_terms": list(pol.get("forbid_visual_terms") or []),
        },
        "candidates": len(rows),
        "allowed": allowed,
        "downgraded": len(rows) - allowed,
        "a_roll_clips": len(a_rolls),
        "rows": rows,
        "note_cn": (
            "抠像不是「每一镜都套」：没人脸、或画面里正在给人看图纸/屏幕/样张的镜头，"
            "一律只保留该风格的真值几何（推近/窗口）不抠像——否则观众该看的东西会被抠没。"
        ),
    }
    return stats


def summarize_for_log(stats: Dict[str, Any]) -> str:
    """一行日志：让跑批的人立刻知道这轮有几镜被降级、为什么。"""
    rows = stats.get("rows") or []
    if not rows:
        return "[step4] 抠像门槛：本片没有抠像镜头"
    by_basis: Dict[str, int] = {}
    for r in rows:
        if not r.get("allowed"):
            by_basis[str(r.get("basis"))] = by_basis.get(str(r.get("basis")), 0) + 1
    tail = "、".join(f"{k} {v}" for k, v in sorted(by_basis.items())) or "无"
    return (
        f"[step4] 抠像门槛：候选 {len(rows)} 镜 → 放行 {stats.get('allowed')} 镜，"
        f"降级为「只推近/只摆窗口」{stats.get('downgraded')} 镜（{tail}）"
    )


__all__ = [
    "DEFAULT_POLICY",
    "apply_cutout_gate",
    "decide",
    "is_cutout_clip",
    "load_visual_texts",
    "summarize_for_log",
]
