#!/usr/bin/env python3
"""音效策略：每条音效都必须说得出「它凭什么响」，说不出就不出。

用户实测原话：「这一版的音效过于密集，感觉是没有任何缘由就出现……应该好好抽象下这些音效……
是要在什么情况下才会出现，调用的原则是怎样，作用域又是怎样，而不是无脑的硬植入。」

旧 step6 只按「密度 + 最小间隔」把音效铺在每个切点上（2026-11-24 style_21 实测 41 条），
所以成片里每一刀都"呼"一声，却没有任何一处说得清为什么是这一刀。本模块把它换成三层判定：

  1. **锚点（凭什么响）**：cut=贴在结构性切点上；flower=贴在花字出现/退出上；line=贴字幕换行；
     beat=贴 BGM 拍点。四类都挂不上 → 不出。
     `stress`（口播中段的重音微切）、`bgm_beat`（等分节拍微切）不算结构性切点——那正是
     "没有任何缘由就响一声"的来源。
  2. **作用域（在哪能响）**：纯静音段不响（除非该风格实测就允许）、口播中段不响、
     镜头类型要在该风格的允许清单里。
  3. **密度（最多响几次）**：全片每分钟上限、同角色最小间隔、同角色每分钟上限，
     全部来自 `forensic/style_trigger_rules_sfx_flower.json` 的 24 套实测值。

输出给 step6 的每条 cue 都带 `reason` / `anchor_time` / `policy`；没出的连原因一起记进 `dropped`，
所以"为什么这里没有音效"也是可查的。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # 同目录导入（scripts/）
    from trigger_policy_common import (  # type: ignore
        SFX_REASONS,
        classify_sfx_anchor,
        in_windows,
        is_structural_cut_type,
        nearest_offset,
        silence_windows,
        sorted_float,
        sliding_count,
        style_rules,
    )
except ImportError:  # pragma: no cover - 兼容直接跑脚本
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from trigger_policy_common import (  # type: ignore
        SFX_REASONS,
        classify_sfx_anchor,
        in_windows,
        is_structural_cut_type,
        nearest_offset,
        silence_windows,
        sorted_float,
        sliding_count,
        style_rules,
    )

# 两条音效之间的绝对下限：比 0.3s 更密的叠音在任何风格里都只会被听成"电流声"
HARD_MIN_GAP_SEC = 0.30
# 规则表里的允许阈值：触发类型占该套音效 ≥10% 才算"这套风格会在这种情况下响"
TRIGGER_ALLOW_SHARE = 0.10


def _role_gap_floor(rules: Dict[str, Any], role: str) -> float:
    roles = rules.get("roles") or {}
    node = roles.get(role) or {}
    gap = node.get("min_gap_sec")
    try:
        gap_v = float(gap) if gap is not None else 0.0
    except (TypeError, ValueError):
        gap_v = 0.0
    # 真值里的 role.min_gap 是从两三条 cue 里量到的极端值，直接当硬闸会把整片卡死；
    # 取它的 60% 作为"同角色不许连击"的门（仍然比全局 min_gap 严格）。
    return max(HARD_MIN_GAP_SEC, round(gap_v * 0.6, 3))


def _role_ppm_cap(rules: Dict[str, Any], role: str) -> Optional[float]:
    caps = rules.get("max_per_min_by_role") or {}
    v = caps.get(role)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class SfxPolicy:
    """一套风格的音效门禁。用法：policy.evaluate(candidates, context) -> result。"""

    def __init__(self, style_id: str, rules_path: Optional[Path] = None):
        self.style_id = str(style_id)
        node = style_rules(self.style_id, rules_path)
        self.found = bool(node.get("found"))
        self.rules = node.get("sfx") or {}
        self.beat = node.get("beat") or {}
        scope = self.rules.get("scope") or {}
        self.allowed_triggers = [t for t in (self.rules.get("allowed_triggers") or []) if t]
        shares = self.rules.get("trigger_share") or {}
        # 表里的 allowed_triggers 已按 ≥10% 占比筛过；这里再兜一层，防止手改规则表放水
        self.allowed_triggers = [t for t in self.allowed_triggers
                                 if float(shares.get(t, 1.0)) >= TRIGGER_ALLOW_SHARE] or self.allowed_triggers
        self.max_per_min = float(self.rules.get("max_per_min") or 30.0)
        self.min_gap = max(HARD_MIN_GAP_SEC, float(self.rules.get("min_gap_sec") or 0.9))
        self.silence_ban = bool(scope.get("silence_ban", True))
        self.midline_ban = bool(scope.get("midline_ban", True))
        self.structural_cut_only = bool(scope.get("structural_cut_only", True))
        self.allowed_shot_types = set(scope.get("allowed_shot_types") or ["a_roll", "b_roll"])
        self.tol = float(self.rules.get("tol_sec") or 0.12)

    # ── 锚点 ────────────────────────────────────────────────────────────────
    def build_anchors(self, context: Dict[str, Any]) -> Dict[str, Any]:
        cuts_all = sorted_float(context.get("cut_times") or [])
        cut_events = context.get("cut_events") or []
        structural: List[float] = []
        if cut_events:
            for ev in cut_events:
                t = ev.get("time_sec")
                if t is None:
                    continue
                if self.structural_cut_only and not is_structural_cut_type(ev.get("type")):
                    continue
                structural.append(float(t))
            structural = sorted_float(structural)
        else:
            structural = cuts_all
        flowers_in = sorted_float(context.get("flower_starts") or [])
        flowers_out = sorted_float(context.get("flower_ends") or [])
        line_starts = sorted_float(context.get("line_starts") or [])
        beats = sorted_float(context.get("beats") or [])
        conf = float(self.beat.get("confidence") or 0.0)
        if conf and conf < 0.45:
            beats = []  # 节拍估计不可靠就不许拿它当理由
        return {
            "cut": structural,
            "flower": sorted(set(flowers_in) | set(flowers_out)),
            "line": line_starts,
            "beat": beats,
            "_all_cuts": cuts_all,
        }

    # ── 主判定 ──────────────────────────────────────────────────────────────
    def evaluate(
        self,
        candidates: Sequence[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """candidates: [{time_sec, role?, file?, label?, event_id?, event_type?, shot_type?, origin?}]
        context: {cut_events|cut_times, flower_starts, flower_ends, line_starts, beats,
                  sentences, duration_sec}
        """
        duration = float(context.get("duration_sec") or 0.0)
        anchors = self.build_anchors(context)
        sents = context.get("sentences") or []
        silences = context.get("silence_windows")
        if silences is None:
            silences = silence_windows(sents, duration) if sents else []

        accepted: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        rows: List[Dict[str, Any]] = []

        ordered = sorted(candidates, key=lambda c: float(c.get("time_sec") or 0.0))
        acc_times: List[float] = []
        role_times: Dict[str, List[float]] = {}
        free_times: List[float] = []
        free_cap = float(self.rules.get("max_free_per_min") or 0.0)

        for cand in ordered:
            t = round(float(cand.get("time_sec") or 0.0), 3)
            base = {
                "time_sec": t,
                "origin": cand.get("origin") or "",
                "event_id": cand.get("event_id"),
                "event_type": cand.get("event_type"),
            }
            info = classify_sfx_anchor(t, anchors, {"cut": self.tol, "flower": self.tol,
                                                    "line": self.tol, "beat": 0.08})
            reason = info["reason"]
            anchor_time = info["anchor_time"]
            offset = info["offset_sec"]

            def drop(why: str, detail: str = "") -> None:
                dropped.append({**base, "reason": reason, "anchor_time": anchor_time,
                                "offset_sec": offset, "dropped_because": why, "detail": detail})
                rows.append({**base, "decision": "drop", "trigger": reason or "none",
                             "anchor_time": anchor_time, "offset_sec": offset, "why": why})

            if reason is None:
                drop("no_anchor", "最近锚点都不在容差内：不许响")
                continue
            if reason not in self.allowed_triggers:
                drop("trigger_not_in_style",
                     f"{self.style_id} 的源草稿里 {reason} 占比不足/不存在")
                continue
            if reason == "cut" and self.structural_cut_only and cand.get("event_type"):
                if not is_structural_cut_type(cand.get("event_type")):
                    drop("non_structural_cut",
                         f"切点类型 {cand.get('event_type')} 是口播中段微切，不算结构性切点")
                    continue

            role = str(cand.get("role") or "")
            map_role = (self.rules.get("role_for_trigger") or {}).get(reason)
            if not role or (map_role and role != map_role and role not in (self.rules.get("roles") or {})):
                role = map_role or role or ("misc")
            base["role"] = role

            shot_type = cand.get("shot_type")
            if shot_type and shot_type not in self.allowed_shot_types:
                drop("shot_type_scope", f"{shot_type} 不在该风格允许的镜头类型 {sorted(self.allowed_shot_types)} 里")
                continue

            if self.silence_ban and in_windows(t, silences):
                drop("silence_ban", "落在纯静音段（没有人声的空白）里：没画面转折也没人声，不许响")
                continue

            if self.midline_ban and self._is_midline(t, sents, anchors):
                drop("midline_ban", "落在口播句中且不贴切点/换行/花字：不许在句子中间插一声")
                continue

            if acc_times and (t - acc_times[-1]) < self.min_gap - 1e-6:
                drop("global_min_gap", f"距上一条 {t - acc_times[-1]:.3f}s < {self.min_gap:.3f}s")
                continue

            r_gap = _role_gap_floor(self.rules, role)
            rt = role_times.get(role) or []
            if rt and (t - rt[-1]) < r_gap - 1e-6:
                drop("role_min_gap", f"同角色 {role} 距上一条 {t - rt[-1]:.3f}s < {r_gap:.3f}s")
                continue

            if sliding_count(acc_times, t, 60.0) >= int(math.floor(self.max_per_min)):
                drop("max_per_min", f"最近 60s 已满 {self.max_per_min:.0f} 条")
                continue

            cap = _role_ppm_cap(self.rules, role)
            if cap is not None and sliding_count(rt, t, 60.0) >= max(1, int(math.ceil(cap))):
                drop("role_max_per_min", f"{role} 最近 60s 已满 {cap:.1f} 条（源草稿同角色密度）")
                continue

            if free_cap <= 0 and reason == "free":
                drop("no_free_quota", "该风格源草稿里没有任何无锚点音效")
                continue

            cue = {
                **base,
                "role": role,
                "reason": reason,
                "anchor_time": anchor_time,
                "offset_sec": offset,
                "policy": self.describe(reason, role),
            }
            if cand.get("file"):
                cue["file"] = cand["file"]
            accepted.append(cue)
            acc_times.append(t)
            role_times.setdefault(role, []).append(t)
            if reason == "free":
                free_times.append(t)
            rows.append({**base, "decision": "keep", "trigger": reason, "anchor_time": anchor_time,
                         "offset_sec": offset, "why": self._why(reason, anchor_time, offset, role)})

        return {
            "style_id": self.style_id,
            "accepted": accepted,
            "dropped": dropped,
            "rows": rows,
            "n_candidates": len(ordered),
            "n_accepted": len(accepted),
            "n_dropped": len(dropped),
            "rules_used": {
                "allowed_triggers": self.allowed_triggers,
                "min_gap_sec": self.min_gap,
                "max_per_min": self.max_per_min,
                "silence_ban": self.silence_ban,
                "midline_ban": self.midline_ban,
                "structural_cut_only": self.structural_cut_only,
                "allowed_shot_types": sorted(self.allowed_shot_types),
            },
        }

    @staticmethod
    def _is_midline(t: float, sents: Sequence[Dict[str, Any]],
                    anchors: Dict[str, Any]) -> bool:
        """t 是否落在某句人声的"中段"：在句内、离句首超过 0.25s、又不贴任何锚点。"""
        inside = None
        for s in sents:
            try:
                a = float(s.get("start_sec", 0.0))
                b = float(s.get("end_sec", 0.0))
            except (TypeError, ValueError):
                continue
            if a <= t <= b:
                inside = (a, b)
                break
        if inside is None:
            return False
        a, _b = inside
        if t - a <= 0.25:
            return False
        for key in ("cut", "flower", "line", "beat"):
            off, _ = nearest_offset(t, anchors.get(key) or [])
            if off is not None and off <= 0.12:
                return False
        return True

    def describe(self, reason: str, role: str) -> str:
        return (f"{self.style_id} 源草稿触发={reason} 角色={role}"
                f"（allowed={','.join(self.allowed_triggers)} · min_gap={self.min_gap:.2f}s"
                f" · ≤{self.max_per_min:.0f}/min）")

    @staticmethod
    def _why(reason: str, anchor_time: Optional[float], offset: Optional[float], role: str) -> str:
        label = {"cut": "结构性切点", "flower": "花字出现/退出", "line": "字幕换行", "beat": "BGM 拍点"}.get(reason, reason)
        off_txt = f"偏差 {offset * 1000:.0f}ms" if offset is not None else ""
        return f"贴{label} @{anchor_time:.3f}s {off_txt} · 角色 {role}".strip()


def format_table(result: Dict[str, Any], limit: int = 200) -> str:
    """可读的依据清单：每条出/不出 + 理由 + 参照点。"""
    lines = [
        f"音效依据表 · {result.get('style_id')} · 候选 {result.get('n_candidates')} → "
        f"出 {result.get('n_accepted')} / 不出 {result.get('n_dropped')}",
        f"  规则：{result.get('rules_used')}",
        f"  {'时间':>7s}  {'判定':4s} {'触发':7s} {'锚点':>8s} {'偏差':>7s}  理由",
    ]
    rows = sorted(result.get("rows") or [], key=lambda r: r.get("time_sec") or 0.0)
    for r in rows[:limit]:
        anchor = r.get("anchor_time")
        off = r.get("offset_sec")
        lines.append(
            f"  {float(r.get('time_sec') or 0):7.3f}  {'出' if r['decision'] == 'keep' else '不出':4s} "
            f"{str(r.get('trigger') or '-'):7s} {(f'{anchor:.3f}' if anchor is not None else '-'):>8s} "
            f"{(f'{off * 1000:.0f}ms' if off is not None else '-'):>7s}  {r.get('why')}"
        )
    if len(rows) > limit:
        lines.append(f"  … 其余 {len(rows) - limit} 条略")
    return "\n".join(lines)


def policy_for(style_id: str, rules_path: Optional[Path] = None) -> SfxPolicy:
    return SfxPolicy(style_id, rules_path)
