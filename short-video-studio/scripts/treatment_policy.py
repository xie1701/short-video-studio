#!/usr/bin/env python3
"""视觉类处理的「触发条件」执行器——风格会这个手法 ≠ 每一镜都该套。

用户原话（2026-11-24）：
「风格模版应该好好抽象下这些音效、花字之类的是要在什么情况下才会出现，调用的原则是怎样，
作用域又是怎样，而不是无脑的硬植入……不仅仅是音效和花字，还有转场、抠像、变速等等模版里
包含的那些针对具体素材元素的处理，都是要提炼出真正的风格条件才是。」

本模块是 **唯一决策口**：转场 / 抠像-画中画 / 变速 / 叠加层-贴纸 / 画面特效，全部先问这里，
再决定套不套。规则本身不在代码里，而在 `forensic/style_trigger_rules_visual.json`
（由 `dev_tools/build_trigger_rules_visual.py` 从 24 套真源草稿反推），所以：

  · 阈值只在一处量过、一处存放；运行时只读，不另立口径；
  · 每套风格的密度/作用域/上限各不相同，代码不写死任何一套的数字；
  · 没有满足条件就**不套**，并且必须给出一句人话理由（能直接念给用户听）；
  · 规则文件缺失/这套风格量不到 → 一律按「不套」并说明原因（宁可少套，不可硬植）。

五个对外判定（都返回 `(allowed, reason, scope)`）：
    allow_transition(ctx) / allow_cutout(ctx) / allow_speed(ctx) / allow_overlay(ctx) / allow_effect(ctx)

两个流水线执行口（step4 / apply_replica_template 用）：
    apply_cutout_policy(clips, style_entry, pipeline_dir, style_id)  → 逐镜放行/降级 + 理由，落 EDL
    apply_speed_policy(clips, style_entry, pipeline_dir, style_id, manifest) → 逐镜变速/保持原速 + 理由

与 `pip_policy` 的分工（不许两套口径打架）：
    · 内容门槛（有人在不在、脸够不够大、画面里有没有「要看的东西」）由 `pip_policy.decide` 实现；
    · 风格门槛（这套风格允不允许、密度预算、单段上限、台词是否在指认画面…）由本模块实现；
    · 阈值全部来自同一份规则 JSON：本模块把它换算成 pip_policy 的 policy 入参再调用一次，
      所以两个模块看到的是同一组数字。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pip_policy

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "forensic" / "style_trigger_rules_visual.json"

TREATMENTS = ("transition", "cutout", "speed", "overlay", "effect")

# 这几种条件由内容门槛（pip_policy.decide）执行：本模块不重复实现，避免两套口径
CONTENT_KEYS = {"face_present", "face_min_size", "no_display_terms"}

# 规则文件缺失/风格缺节时的兜底：一律不套（宁可少套，不可硬植）
FALLBACK_SECTION: Dict[str, Dict[str, Any]] = {
    "transition": {"enabled": False, "mode": "any_of",
                   "default_reason": "没有该风格的转场规则 → 默认硬切",
                   "conditions": [], "scope": {"allow": [], "forbid": ["*"]}, "limits": {"max_count": 0}},
    "cutout": {"enabled": False, "default_reason": "没有该风格的抠像规则 → 不抠像",
               "conditions": [], "scope": {"allow": [], "forbid": ["*"]}, "limits": {}},
    "speed": {"enabled": False, "default_reason": "没有该风格的变速规则 → 原速播放",
              "conditions": [], "scope": {"allow": [], "forbid": ["*"]}, "limits": {}},
    "overlay": {"enabled": False, "default_reason": "没有该风格的叠加层规则 → 不加叠加",
                "conditions": [], "scope": {"allow": [], "forbid": ["*"]}, "limits": {}},
    "effect": {"enabled": False, "default_reason": "没有该风格的画面特效规则 → 不加特效",
               "conditions": [], "scope": {"allow": [], "forbid": ["*"]}, "limits": {}},
}

_CACHE: Dict[str, Dict[str, Any]] = {}
_RULES_DOC: Optional[Dict[str, Any]] = None


# ────────────────────────── 规则装载 ──────────────────────────
def rules_available() -> bool:
    return RULES_PATH.is_file()


def load_rules_doc(path: Optional[Path] = None) -> Dict[str, Any]:
    """整份规则文档（带缓存）。缺文件返回空壳，调用方一律走兜底「不套」。"""
    global _RULES_DOC
    p = Path(path) if path else RULES_PATH
    if _RULES_DOC is not None and Path(path or RULES_PATH) == RULES_PATH:
        return _RULES_DOC
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        doc = {"styles": {}, "error": f"规则文件不可读：{p}"}
    if Path(path or RULES_PATH) == RULES_PATH:
        _RULES_DOC = doc
    return doc


def style_known(style_id: Optional[str]) -> bool:
    """规则里到底有没有这一套——用于区分「风格判定为不套」和「根本不知道这是哪套」。"""
    return bool(((load_rules_doc().get("styles") or {}).get(str(style_id or ""))))


def style_rules(style_id: Optional[str]) -> Dict[str, Any]:
    """取一套风格的五节规则；缺就返回兜底（每节 enabled=False）。"""
    sid = str(style_id or "")
    if sid in _CACHE:
        return _CACHE[sid]
    doc = load_rules_doc()
    sec = ((doc.get("styles") or {}).get(sid) or {})
    out: Dict[str, Any] = {}
    for name in TREATMENTS:
        s = dict(sec.get(name) or {})
        if not s:
            s = dict(FALLBACK_SECTION[name])
        s.setdefault("_style_id", sid)
        out[name] = s
    out["_meta"] = {"style_id": sid,
                    "style_name_cn": sec.get("style_name_cn"),
                    "available": rules_available(),
                    "known_style": bool(sec)}
    _CACHE[sid] = out
    return out


def reload_rules() -> None:
    """测试/长驻进程换规则文件后手动清缓存。"""
    global _RULES_DOC
    _RULES_DOC = None
    _CACHE.clear()


def _section(ctx: Dict[str, Any], name: str, rules: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if rules is not None:
        # 允许直接传某一节（便于单测）；也允许传整份 style_rules(...) 的结果
        if name in rules and isinstance(rules.get(name), dict):
            return rules[name]
        if "conditions" in rules or "enabled" in rules:
            return rules
    return style_rules(ctx.get("style_id")).get(name) or dict(FALLBACK_SECTION[name])


def _scope_of(sec: Dict[str, Any]) -> Dict[str, Any]:
    sc = dict(sec.get("scope") or {})
    sc["limits"] = dict(sec.get("limits") or {})
    return sc


def _fmt(tpl: str, **kw: Any) -> str:
    """理由模板安全填充：数字/字符串都能塞，缺字段不抛异常。"""
    try:
        return str(tpl).format(**kw)
    except Exception:
        out = str(tpl)
        for k, v in kw.items():
            out = out.replace("{" + k + "}", str(v))
        return re.sub(r"\{[^}]*\}", "", out)


# ────────────────────────── 通用条件求值 ──────────────────────────
def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _terms_hit(text: Any, terms: List[str]) -> Optional[str]:
    s = str(text or "")
    low = s.lower()
    for t in terms:
        t = str(t or "").strip()
        if not t:
            continue
        if t.isascii():
            if t.lower() in low:
                return t
        elif t in s:
            return t
    return None


def _eval_check(check: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[bool, Any]:
    """执行一条可执行判定，返回 (是否通过, 命中值/说明)。

    判定键与 `forensic/style_trigger_rules_visual.json` 一一对应，新增键必须两处一起加。
    """
    key = str(check.get("key") or "")
    role = str(ctx.get("track_role") or "")

    if key == "style_truth_positive":
        return _as_float(check.get("value")) > 0, check.get("value")

    if key == "track_role_in":
        allow = [str(x) for x in (check.get("allow") or [])]
        return role in allow, role or "unknown"

    if key == "face_present":
        return bool(ctx.get("face_anchor")), ctx.get("face_anchor")

    if key == "face_min_size":
        fa = ctx.get("face_anchor") or {}
        h = _as_float(fa.get("h"))
        w = _as_float(fa.get("w"))
        return (h >= _as_float(check.get("min_h")) and w >= _as_float(check.get("min_w"))), (h, w)

    if key == "no_display_terms":
        hit = _terms_hit(ctx.get("visual_text"), list(check.get("terms") or []))
        return hit is None, hit or ""

    if key == "no_deictic_terms":
        hit = _terms_hit(ctx.get("sentence_text"), list(check.get("terms") or []))
        return hit is None, hit or ""

    if key == "secondary_distinct":
        main_m = str(ctx.get("main_media") or "")
        sec_m = str(ctx.get("secondary_media_path") or "")
        if not sec_m:
            return False, "没有副镜"
        return (not main_m) or (Path(sec_m).name != Path(main_m).name), Path(sec_m).name

    if key == "duration_le":
        d = _as_float(ctx.get("duration_sec"))
        return d <= _as_float(check.get("value")), d

    if key == "duration_between":
        d = _as_float(ctx.get("duration_sec"))
        lo, hi = _as_float(check.get("min")), _as_float(check.get("max"))
        return (lo <= d <= hi), d

    if key == "speed_value_in":
        vals = [_as_float(v) for v in (check.get("values") or []) if v is not None]
        tol = _as_float(check.get("tol"), 0.0)
        want = _as_float(ctx.get("speed"), 1.0)
        ok = any(abs(want - v) <= max(1e-6, tol) for v in vals)
        return ok, want

    if key == "source_headroom":
        sp = _as_float(ctx.get("speed"), 1.0)
        need = _as_float(ctx.get("duration_sec")) * sp
        have = _as_float(ctx.get("source_headroom_sec"))
        return have >= need, (need, have)

    if key == "near_cut":
        dist = ctx.get("at_cut_dist")
        if dist is None:
            return False, "不知道离切点多远"
        return _as_float(dist) <= _as_float(check.get("tol_sec")), _as_float(dist)

    if key == "paired_with_text":
        return bool(ctx.get("has_text")), ctx.get("has_text")

    if key == "count_budget":
        used = int(ctx.get("used_count") or 0)
        total = _as_float(ctx.get("total_duration_sec"))
        per_min = _as_float(check.get("max_per_min"))
        max_n = max(1, int(math.ceil(per_min * max(0.1, total) / 60.0))) if total else max(1, int(per_min))
        extra = int(ctx.get("count_this") or 1)
        return used + extra <= max_n, max_n

    if key == "run_budget":
        """抠像要成段：同一段连续抠像里加镜不算新段，跨出去才算起新的一段。"""
        max_runs = max(1, int(check.get("max_runs") or 1))
        used = int(ctx.get("runs_started") or 0)
        if ctx.get("continues_run"):
            return used <= max_runs, max_runs
        return used + 1 <= max_runs, max_runs

    if key == "cutout_budget_left":
        """抠像预算按「人物层时长 × 真值人物层被抠比例」算，按全片占比算会虚高。"""
        share = _as_float(check.get("person_share"))
        person_sec = _as_float(ctx.get("person_sec"))
        used = _as_float(ctx.get("used_sec"))
        d = _as_float(ctx.get("duration_sec"))
        budget = max(0.0, share * person_sec - used)
        return d <= budget + 1e-6, (d, budget, share)

    if key == "coverage_budget_left":
        total = _as_float(ctx.get("total_duration_sec"))
        ratio = _as_float(check.get("max_ratio"))
        used = _as_float(ctx.get("used_sec"))
        d = _as_float(ctx.get("duration_sec"))
        budget = max(0.0, ratio * total - used)
        return d <= budget + 1e-6, (d, budget, ratio)

    if key == "instant_near_cut":
        d = _as_float(ctx.get("duration_sec"))
        if d > _as_float(check.get("instant_max_sec"), 0.6):
            return True, d
        dist = ctx.get("at_cut_dist")
        if dist is None:
            return False, "不知道离切点多远"
        return _as_float(dist) <= _as_float(check.get("tol_sec")), (d, _as_float(dist))

    if key == "subject_dominant":
        fa = ctx.get("face_anchor") or {}
        faces = int(fa.get("faces") or 0)
        h = _as_float(fa.get("h"))
        min_h = _as_float(check.get("min_h"))
        if faces > max(1, int(check.get("faces_warn") or 2)):
            return h >= min_h * 1.1, faces
        return True, faces

    if key == "user_explicit":
        return bool(ctx.get("user_explicit")), ctx.get("transition_whitelist") or ""

    if key == "source_native_overlap":
        return bool(ctx.get("source_native_overlap")), ""

    if key == "chapter_break":
        return (bool(ctx.get("chapter_break"))
                and _as_float(ctx.get("silence_sec")) >= _as_float(check.get("silence_sec"), 0.3)), \
               ctx.get("silence_sec")

    return False, f"未知判定键 {key}"


def _eval_section(sec: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
    """按规则文档判定：返回 (allowed, reason, 失败条件 id)。"""
    mode = str(sec.get("mode") or "all_required")
    conds = list(sec.get("conditions") or [])
    if mode == "any_of":
        hits: List[str] = []
        for c in conds:
            ok, val = _eval_check(c.get("check") or {}, ctx)
            if ok:
                hits.append(_fmt(str(c.get("reason") or "满足"), value=val))
        if hits:
            return True, hits[0], None
        return False, str(sec.get("default_reason") or "不满足该风格的转场条件，保持硬切"), "any_of"

    for c in conds:
        if not c.get("required", True):
            continue
        ck = c.get("check") or {}
        if str(ck.get("key")) in CONTENT_KEYS:
            # 内容门槛已由 pip_policy 判过（见 allow_cutout），这里不重复实现
            continue
        ok, val = _eval_check(ck, ctx)
        if ok:
            continue
        kw: Dict[str, Any] = {"value": val, "h": 0.0, "w": 0.0,
                              "need": 0.0, "have": 0.0, "ratio": 0.0, "share": 0.0,
                              "values": ",".join(f"{v:g}" for v in (ck.get("values") or []))}
        if isinstance(val, tuple):
            if ck.get("key") == "face_min_size":
                kw["h"], kw["w"] = val[0], val[1]
            elif ck.get("key") == "source_headroom":
                kw["need"], kw["have"] = val[0], val[1]
            elif ck.get("key") in ("coverage_budget_left",):
                kw["ratio"] = val[2]
            elif ck.get("key") == "cutout_budget_left":
                kw["share"] = val[2]
        reason = _fmt(str(c.get("reason") or "不满足条件"), **kw)
        return False, reason, str(c.get("id") or ck.get("key"))
    return True, "", None


# ────────────────────────── 五个对外判定 ──────────────────────────
def allow_transition(ctx: Dict[str, Any], rules: Optional[Dict[str, Any]] = None
                     ) -> Tuple[bool, str, Dict[str, Any]]:
    """这一处切点能不能插转场。默认硬切；只有显式放行/素材自带溶接/章节断点才可能为真。"""
    sec = _section(ctx, "transition", rules)
    scope = _scope_of(sec)
    cut_kind = str(ctx.get("cut_kind") or "")
    forbid = [str(x) for x in (scope.get("forbid") or [])]
    if cut_kind in forbid:
        return False, f"这是{cut_kind}（{_CUT_KIND_CN.get(cut_kind, cut_kind)}），节奏点不插转场", scope
    if ctx.get("in_hook") and "hook_first_3s" in forbid:
        return False, "开场 3 秒是留人点，不插转场", scope
    ok, reason, _cid = _eval_section(sec, ctx)
    if ok:
        max_dur = _as_float((sec.get("limits") or {}).get("max_duration_sec"), 0.5)
        scope["hard_cut_elsewhere"] = True
        scope["max_duration_sec"] = max_dur
        return True, reason, scope
    return False, reason, scope


_CUT_KIND_CN = {
    "mid_sentence": "句中换气点",
    "inside_fast_cut_run": "快切串里的刀",
    "hook_first_3s": "开场留人段",
    "cutout_boundary": "抠像层进出点",
}


def allow_cutout(ctx: Dict[str, Any], rules: Optional[Dict[str, Any]] = None
                 ) -> Tuple[bool, str, Dict[str, Any]]:
    """这一镜能不能把人物抠出来。内容门槛先过（pip_policy），再查风格密度/台词/作用域。"""
    sec = _section(ctx, "cutout", rules)
    scope = _scope_of(sec)
    if not sec.get("enabled", False):
        n = ((sec.get("evidence") or {}).get("truth_segments"))
        return False, f"这套风格真值 {n if n is not None else 0} 段抠像，整片不抠（风格会抠像 ≠ 每镜都抠）", scope

    # ① 内容门槛：用同一份规则算出的 policy 调 pip_policy，保证两处阈值一致
    verdict = ctx.get("content_verdict")
    if verdict is None:
        verdict = pip_policy.decide(
            clip=ctx.get("clip") or {},
            policy=derived_content_policy(sec),
            visual_text=str(ctx.get("visual_text") or ""),
            face_anchor=ctx.get("face_anchor"),
        )
    if not verdict.get("allowed"):
        return False, str(verdict.get("reason") or "内容门槛未通过"), scope

    # ② 风格门槛：台词是否在指认画面、单段上限、预算、多人脸等
    ok, reason, cid = _eval_section(sec, ctx)
    if not ok:
        return False, reason, scope
    layer = str(scope.get("layer") or "person_over_broll")
    return True, f"真人出镜、画面没有要看的东西、且没超该风格密度（{layer}）", scope


def allow_speed(ctx: Dict[str, Any], rules: Optional[Dict[str, Any]] = None
                ) -> Tuple[bool, str, Dict[str, Any]]:
    """这一段 B-roll 能不能变速。A-roll 口播永远原速。"""
    sec = _section(ctx, "speed", rules)
    scope = _scope_of(sec)
    if not sec.get("enabled", False):
        return False, "这套风格真值 0 段变速，全片原速", scope
    role = str(ctx.get("track_role") or "")
    if role == "a_roll":
        return False, "A-roll 口播不许变速（口型必须与录音对齐）", scope
    ok, reason, _cid = _eval_section(sec, ctx)
    if ok:
        sp = _as_float(ctx.get("speed"), 1.0)
        return True, f"B-roll 压缩等待段（{sp:g}×，该风格真值用过的倍速）", scope
    return False, reason, scope


def allow_overlay(ctx: Dict[str, Any], rules: Optional[Dict[str, Any]] = None
                  ) -> Tuple[bool, str, Dict[str, Any]]:
    """这一处贴纸/叠加层能不能加。必须跟切点进场、与花字成对、不超上限。"""
    sec = _section(ctx, "overlay", rules)
    scope = _scope_of(sec)
    if not sec.get("enabled", False):
        return False, "这套风格真值没有贴纸/叠加层，不加", scope
    ok, reason, _cid = _eval_section(sec, ctx)
    if ok:
        return True, "贴切点进场、与当刻花字同框，且没超该风格条数", scope
    return False, reason, scope


def allow_effect(ctx: Dict[str, Any], rules: Optional[Dict[str, Any]] = None
                 ) -> Tuple[bool, str, Dict[str, Any]]:
    """这一处画面特效能不能加。氛围类长盖一段、冲击类 ≤0.6s 贴切点，其余不加。"""
    sec = _section(ctx, "effect", rules)
    scope = _scope_of(sec)
    if not sec.get("enabled", False):
        return False, "这套风格真值 0 个画面特效，不加（24 套里只有 7 套有）", scope
    ok, reason, _cid = _eval_section(sec, ctx)
    if ok:
        d = _as_float(ctx.get("duration_sec"))
        kind = "冲击类贴切点" if d <= _as_float((sec.get("limits") or {}).get("instant_max_sec"), 0.6) else "氛围类盖一个段落"
        return True, f"{kind}（{d:.2f}s），没超该风格覆盖上限", scope
    return False, reason, scope


# ────────────────────────── 与 pip_policy 共用的口径 ──────────────────────────
def derived_content_policy(sec: Dict[str, Any]) -> Dict[str, Any]:
    """把规则里的数字换算成 pip_policy 的 policy 入参——同一份阈值，两个模块共用。"""
    lim = sec.get("limits") or {}
    return {
        "enabled": True,
        "require_face_min_h": _as_float(lim.get("require_face_min_h"), 0.12),
        "require_face_min_w": _as_float(lim.get("require_face_min_w"), 0.07),
        "max_ratio_of_aroll": 1.0,          # 密度预算在本模块按「人物层 × 真值比例」算，pip 只做内容门槛
        "min_gap_sec": 0.0,                 # 间隔由本模块按该风格真值（可能是连续成段）判
        "forbid_visual_terms": _terms_from_rules(sec),
    }


def _terms_from_rules(sec: Dict[str, Any]) -> List[str]:
    for c in sec.get("conditions") or []:
        ck = c.get("check") or {}
        if str(ck.get("key")) == "no_display_terms":
            return [str(t) for t in (ck.get("terms") or [])]
    return list(pip_policy.DEFAULT_POLICY["forbid_visual_terms"])


def _person_share(sec: Dict[str, Any]) -> float:
    lim = sec.get("limits") or {}
    return _as_float(lim.get("max_share_of_person_layer"))


# ────────────────────────── 流水线执行口：抠像 ──────────────────────────
def _norm_media(p: Any) -> str:
    return pip_policy._norm_name(p)


def apply_cutout_policy(clips: List[Dict[str, Any]], style_entry: Optional[Dict[str, Any]],
                        pipeline_dir: Path, style_id: Optional[str] = None) -> Dict[str, Any]:
    """统一抠像门禁：内容门槛（pip_policy）→ 风格门槛（本模块）→ 逐镜放行/降级 + 理由。

    必须在人脸锚点算完之后调用。降级动作仍由 `pip_policy.apply_cutout_gate` 执行（唯一写口），
    本模块只提供「这一镜让不让抠」的判定，所以不存在两套降级逻辑。
    """
    rules = style_rules(style_id or (style_entry or {}).get("style_id"))
    sec = rules["cutout"]
    scope = _scope_of(sec)
    texts = pip_policy.load_visual_texts(pipeline_dir)

    state: Dict[str, Any] = {"used_sec": 0.0, "used_clips": 0, "last_t": -1e9,
                             "last_end": -1e9, "runs": 0}
    run_tol = _as_float((sec.get("limits") or {}).get("run_gap_tol_sec"), 1.0)
    a_rolls = [c for c in clips if str(c.get("track_role")) == "a_roll"]
    person_sec = sum(float(c.get("duration_sec") or 0.0) for c in a_rolls)
    share = _person_share(sec)
    min_gap = _as_float((sec.get("limits") or {}).get("min_gap_sec"))

    decisions: List[Dict[str, Any]] = []
    order = {id(c): i for i, c in enumerate(clips)}

    def _post_decide(clip: Dict[str, Any], _tin: float) -> Optional[str]:
        """pip 允许之后的第二道：风格门槛。返回拒绝理由字符串 = 改判为降级。"""
        lay = clip.get("pip_layout") or {}
        tin = float(clip.get("timeline_in_sec") or 0.0)
        dur = float(clip.get("duration_sec") or 0.0)
        continues = (tin - state["last_end"]) <= run_tol
        ctx = {
            "style_id": rules["_meta"]["style_id"],
            "track_role": str(clip.get("track_role") or ""),
            "duration_sec": dur,
            "timeline_in_sec": tin,
            "total_duration_sec": sum(float(c.get("duration_sec") or 0.0) for c in clips),
            "person_sec": person_sec,
            "used_sec": state["used_sec"],
            "runs_started": state["runs"],
            "continues_run": continues,
            "visual_text": texts.get(_norm_media(clip.get("media_path")), ""),
            "sentence_text": str(clip.get("sentence_text") or ""),
            "face_anchor": lay.get("face_anchor"),
            "main_media": str(clip.get("media_path") or ""),
            "secondary_media_path": str(lay.get("secondary_media_path") or ""),
        }
        allowed, reason, _sc = allow_cutout(ctx, rules)
        if allowed and min_gap > 0 and tin - state["last_t"] < min_gap:
            allowed = False
            reason = f"距上一个抠像镜只有 {tin - state['last_t']:.1f}s，该风格密度是 ≥{min_gap:.0f}s"
        if allowed:
            if not continues:
                state["runs"] += 1
            state["used_sec"] += dur
            state["used_clips"] += 1
            state["last_t"] = tin
            state["last_end"] = max(state["last_end"], tin + dur)
        decisions.append({
            "clip_id": clip.get("clip_id"),
            "timeline_in_sec": round(tin, 3),
            "duration_sec": round(dur, 3),
            "media": _norm_media(clip.get("media_path")),
            "stage": "风格门槛",
            "allowed": bool(allowed),
            "reason": reason,
            "scope": {"allow": scope.get("allow"), "layer": scope.get("layer")},
        })
        return None if allowed else reason

    # 内容门槛 + 降级写口仍是 pip_policy（口径由本模块给出的 policy 指定）
    entry = dict(style_entry or {})
    spec = dict(entry.get("pip_composition_spec") or {})
    pip_pol = dict(spec.get("cutout_policy") or {})
    pip_pol.update(derived_content_policy(sec))
    spec["cutout_policy"] = pip_pol
    entry["pip_composition_spec"] = spec

    gate = pip_policy.apply_cutout_gate(clips, entry, pipeline_dir, post_decide=_post_decide)

    # 把 pip 阶段就拒掉的镜头也并进本模块的账里（逐镜理由一条不少）
    for row in gate.get("rows") or []:
        if row.get("allowed"):
            continue
        if any(d.get("clip_id") == row.get("clip_id") for d in decisions):
            continue
        decisions.append({
            "clip_id": row.get("clip_id"),
            "timeline_in_sec": row.get("timeline_in_sec"),
            "duration_sec": None,
            "media": row.get("media"),
            "stage": "内容门槛",
            "allowed": False,
            "reason": row.get("reason"),
            "scope": {"allow": scope.get("allow"), "layer": scope.get("layer")},
        })
    decisions.sort(key=lambda d: (d.get("timeline_in_sec") or 0.0))

    allowed_n = sum(1 for d in decisions if d.get("allowed"))
    stats = {
        "style_id": rules["_meta"]["style_id"],
        "enabled": bool(sec.get("enabled")),
        "policy": {
            "max_share_of_person_layer": share,
            "max_runs": (sec.get("limits") or {}).get("max_runs"),
            "runs_used": state["runs"],
            "min_gap_sec": min_gap,
            "max_seg_sec": (sec.get("limits") or {}).get("max_seg_sec"),
            "require_face_min_h": (sec.get("limits") or {}).get("require_face_min_h"),
            "require_face_min_w": (sec.get("limits") or {}).get("require_face_min_w"),
            "person_layer_sec": round(person_sec, 3),
            "budget_sec": round(share * person_sec, 3),
        },
        "candidates": len(decisions),
        "allowed": allowed_n,
        "dropped": len(decisions) - allowed_n,
        "used_sec": round(state["used_sec"], 3),
        "a_roll_clips": len(a_rolls),
        "rows": decisions,
        "pip_gate": {k: gate.get(k) for k in ("candidates", "allowed", "downgraded")},
        "scope": {"allow": scope.get("allow"), "forbid": scope.get("forbid"),
                  "layer": scope.get("layer")},
        "note_cn": ("抠像按「这一镜是否真有可抠的人、是否正在给人看东西、是否超该风格密度」逐镜判，"
                    "不满足就只保留该风格的真值几何（推近/窗口）不抠像。"),
    }
    return stats


# ────────────────────────── 流水线执行口：变速 ──────────────────────────
def apply_speed_policy(clips: List[Dict[str, Any]], style_entry: Optional[Dict[str, Any]],
                       pipeline_dir: Path, style_id: Optional[str] = None,
                       manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """逐镜决定变不变速：只有该风格真值有变速、且这一镜是 B-roll、素材余量够时才写 `clip["speed"]`。

    `render_mp4` 只认显式 `speed` 字段（不从 source/target 反解），所以这里写 `speed` 的同时
    把 `source_out_sec` 同步成 `source_in + dur×speed`，两边口径一致。
    """
    rules = style_rules(style_id or (style_entry or {}).get("style_id"))
    sec = rules["speed"]
    scope = _scope_of(sec)
    durs = {str(it.get("normalized_mp4")): _as_float(it.get("duration_sec"))
            for it in ((manifest or {}).get("items") or [])}
    sp_vals = [v for v in ((sec.get("limits") or {}).get("speed_values_in_truth") or []) if v]
    want_speed = max(sp_vals) if sp_vals else 1.0
    total = sum(float(c.get("duration_sec") or 0.0) for c in clips)

    # 同源帧占用表：变速会多吃、多吃的帧不能是别的段已经在用的帧
    # （否则成片上等于把同一段画面播两遍，还会被质检判成「倒带」）
    spans: Dict[str, List[Tuple[str, float, float]]] = {}
    for c in clips:
        mp = str(c.get("media_path") or "")
        if not mp or str(c.get("track_role")) == "code_broll":
            continue
        a = float(c.get("source_in_sec") or 0.0)
        b = float(c.get("source_out_sec") or a)
        spans.setdefault(mp, []).append((str(c.get("clip_id")), a, b))

    def _frames_free(mp: str, clip_id: str, a: float, b: float) -> bool:
        for cid, x, y in spans.get(mp, []):
            if cid == clip_id:
                continue
            if min(b, y) - max(a, x) > 1e-3:
                return False
        return True

    rows: List[Dict[str, Any]] = []
    applied = 0
    used_sec = 0.0
    for c in clips:
        role = str(c.get("track_role") or "")
        dur = float(c.get("duration_sec") or 0.0)
        cur = float(c.get("speed") or 1.0)
        if role == "a_roll" and abs(cur - 1.0) > 1e-6:
            # 上游（模版套用）给的 A-roll 变速一律清掉：口型必须与录音对齐
            c["speed"] = 1.0
            rows.append({"clip_id": c.get("clip_id"), "track_role": role, "allowed": False,
                         "reason": "A-roll 口播不许变速（口型必须与录音对齐）",
                         "scope": {"allow": scope.get("allow")}})
        if role != "b_roll":
            continue
        src_in = float(c.get("source_in_sec") or 0.0)
        src_out = float(c.get("source_out_sec") or 0.0)
        media_dur = durs.get(str(c.get("media_path")), 0.0)
        headroom = (media_dur - src_in) if media_dur > 0 else max(0.0, src_out - src_in)
        ctx = {
            "style_id": rules["_meta"]["style_id"],
            "track_role": role,
            "duration_sec": dur,
            "total_duration_sec": total,
            "speed": want_speed,
            "source_headroom_sec": headroom,
            "used_sec": used_sec,
        }
        allowed, reason, _sc = allow_speed(ctx, rules)
        if allowed and want_speed > 1.0:
            need = dur * want_speed
            if headroom + 1e-6 < need:
                allowed = False
                reason = f"素材余量不足（需要 {need:.1f}s，只有 {headroom:.1f}s），保持原速"
            elif not _frames_free(str(c.get("media_path") or ""), str(c.get("clip_id") or ""),
                                  src_in, src_in + need):
                allowed = False
                reason = "变速要多吃的素材帧已被别的镜头占用（会重复播同一段画面），保持原速"
        if allowed:
            applied += 1
            used_sec += dur
            c["speed"] = round(want_speed, 4)
            c["source_out_sec"] = round(src_in + dur * want_speed, 4)
            c["speed_reason"] = reason
            c["speed_scope"] = scope.get("allow")
            rows.append({"clip_id": c.get("clip_id"), "track_role": role, "allowed": True,
                         "speed": round(want_speed, 4), "reason": reason,
                         "scope": {"allow": scope.get("allow")}})
        else:
            if abs(float(c.get("speed") or 1.0) - 1.0) > 1e-6:
                c["speed"] = 1.0
            rows.append({"clip_id": c.get("clip_id"), "track_role": role, "allowed": False,
                         "reason": reason, "scope": {"allow": scope.get("allow")}})
    stats = {
        "style_id": rules["_meta"]["style_id"],
        "enabled": bool(sec.get("enabled")),
        "policy": {"speed_values_in_truth": sp_vals,
                   "min_dur_sec": (sec.get("limits") or {}).get("min_dur_sec"),
                   "max_dur_sec": (sec.get("limits") or {}).get("max_dur_sec"),
                   "max_per_min": (sec.get("limits") or {}).get("max_per_min")},
        "b_roll_clips": sum(1 for c in clips if str(c.get("track_role")) == "b_roll"),
        "applied": applied,
        "kept_original": len(rows) - applied,
        "applied_sec": round(used_sec, 3),
        "rows": rows,
        "scope": {"allow": scope.get("allow"), "forbid": scope.get("forbid")},
        "note_cn": ("变速只压 B-roll 的无信息等待段；A-roll 口播永远原速；"
                    "这套风格真值没有变速就全片原速。"),
    }
    return stats


# ────────────────────────── 日志 ──────────────────────────
def summarize_cutout(stats: Dict[str, Any]) -> str:
    if not stats.get("candidates"):
        return "[treatment] 抠像：本片没有抠像候选"
    if not stats.get("enabled"):
        return f"[treatment] 抠像：这套风格真值 0 段抠像，{stats.get('candidates')} 个候选一律不抠"
    cut = [r for r in (stats.get("rows") or []) if not r.get("allowed")]
    by = {}
    for r in cut:
        by[str(r.get("stage"))] = by.get(str(r.get("stage")), 0) + 1
    tail = "、".join(f"{k} {v}" for k, v in sorted(by.items())) or "无"
    return (f"[treatment] 抠像：候选 {stats['candidates']} 镜 → 放行 {stats['allowed']} 镜，"
            f"不抠 {stats['dropped']} 镜（{tail}）；已用 {stats.get('used_sec', 0):.1f}s / "
            f"预算 {stats.get('policy', {}).get('budget_sec', 0):.1f}s")


def summarize_speed(stats: Dict[str, Any]) -> str:
    if not stats.get("b_roll_clips"):
        return "[treatment] 变速：本片没有 B-roll，全部原速"
    if not stats.get("enabled"):
        return f"[treatment] 变速：这套风格真值 0 段变速，{stats['b_roll_clips']} 段 B-roll 全部原速"
    return (f"[treatment] 变速：B-roll {stats['b_roll_clips']} 段 → 变速 {stats['applied']} 段"
            f"（{stats.get('applied_sec', 0):.1f}s），其余原速")


__all__ = [
    "RULES_PATH",
    "allow_cutout", "allow_effect", "allow_overlay", "allow_speed", "allow_transition",
    "apply_cutout_policy", "apply_speed_policy",
    "derived_content_policy", "load_rules_doc", "reload_rules", "rules_available",
    "style_known", "style_rules", "summarize_cutout", "summarize_speed",
]
