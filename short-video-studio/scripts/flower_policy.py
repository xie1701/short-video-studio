#!/usr/bin/env python3
"""花字策略：什么时候才允许上屏。

用户实测原话：「音效过于密集，感觉是没有任何缘由就出现……花字之类的也要抽象出在什么情况下才会出现，
调用原则、作用域，而不是无脑硬植入。所有的 24 个风格模版也都要反思。」

旧 step6 的花字是**倒挂**的：它把音效 cue 当花字锚点（`for cue in sfx_cues:` 直接拿 cue 的位置生成花字），
于是"有音效就有花字"，两者一起变成装饰性噪声。这里把它掰正，花字有自己的一套触发条件：

  1. **有出处**：文案必须能从同期人声里溯到——`number`（数字+量词/年份）、`chapter`（章节/条目转折）、
     `keyword`（句中人声里出现的关键词）、`line_echo`（整句回声）。溯不到出处的改写型花字**不上屏**。
  2. **有位置**：要么贴切点，要么贴该句的起声，不落在句子正中间把人口型挡住。
  3. **有作用域**：镜头类型要在允许清单里（比如只在 B-roll 上压、只在人物特写上压）；
     版式/配色/字号/停留时长用该风格源草稿的实测值。
  4. **有密度上限**：每分钟条数、最小间隔、最长停留，全部来自
     `forensic/style_trigger_rules_sfx_flower.json` 的 24 套实测值。

输出每条花字都带 `trigger` / `anchor` / `policy` / `evidence_text`（出处原文），没出的记进 `dropped`。
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:  # 同目录导入
    from trigger_policy_common import (  # type: ignore
        in_windows,
        nearest_offset,
        silence_windows,
        sorted_float,
        style_rules,
    )
except ImportError:  # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from trigger_policy_common import (  # type: ignore
        in_windows,
        nearest_offset,
        silence_windows,
        sorted_float,
        style_rules,
    )

# 数字触发只认"有量级"的数字：金额/规模/时长/百分比。
# 2026-11-24 实测事故：宽松的量词表会把「四大天王」匹配成数字「四大天」（天=天数的量词），
# 于是花字上屏一个「四大天」。所以中文数值后面必须是**量级单位**，不能是「个/张/间/天」这类分类量词；
# 带阿拉伯数字的则允许接分类量词（99元 / 25张卡座 / 1993年）。
_DIGIT_UNIT = (r"\d[\d,\.]*\s*(?:万|亿|千|百|%|％|折|元|块|平|米|倍|年|岁|小时|分钟|"
               r"个|张|间|家|条|人|位|套|层|楼|克|斤|公里|秒|天)")
_CN_MAGNITUDE = r"[零一二两三四五六七八九十百千]{1,6}(?:万|亿)[零一二两三四五六七八九十百千]{0,4}"
_CN_SCALE_UNIT = (r"[零一二两三四五六七八九十百千]{1,5}"
                  r"(?:元|块|平|米|倍|折|%|％|年|岁|斤|克|公里|分钟|小时)")
NUM_MEASURE_RE = re.compile("(?:%s|%s|%s)" % (_DIGIT_UNIT, _CN_MAGNITUDE, _CN_SCALE_UNIT))
SECTION_RE = re.compile(
    r"(一、|二、|三、|四、|五、|六、|①|②|③|第一步|第二步|第一点|第二点|"
    r"^第[一二三四五六七八九十]+[，,、]|^第[一二三四五六七八九十]+[步点条]|首先|其次|再次|最后|另外)"
)
# 花字最短停留：短于 0.8s 的"闪一下"在任何风格里都读不完
HARD_MIN_HOLD_SEC = 0.8

# 花字必须是**完整短语**：以口语虚词开头（"我哋嘅…"）或结尾（"…嘅"）都是半截子，上屏就是残词。
_BAD_HEAD = ("我哋", "我们", "我", "你", "嘅", "的", "仲", "就", "系", "都", "好", "呢", "嗰",
             "𠮶", "所以", "但是", "因为", "然后", "大家", "呢个", "这个",
             # 问句开头：花字不能是半句提问（"唔知道大家…"）
             "唔知道", "不知道", "知唔知", "你知唔知", "点解")
_BAD_TAIL = ("嘅", "的", "了", "把", "跟", "同", "和", "就", "系", "都", "仲", "呀", "啦", "嘛", "呢", "啊")
TRIGGER_ALLOW_SHARE = 0.10


def text_quality_issue(text: str) -> str:
    """花字文案能不能直接上屏；返回问题描述，没问题返回空串。"""
    t = (text or "").strip()
    if not t:
        return "空文案"
    if any(t.startswith(h) for h in _BAD_HEAD) and len(t) <= 3:
        return f"以口语虚词开头（{t}），是半截子"
    if any(t.startswith(h) for h in _BAD_HEAD):
        return f"以口语虚词开头（{t[:3]}…），读者看到的是半句"
    if any(t.endswith(tl) for tl in _BAD_TAIL):
        return f"以虚词结尾（…{t[-1]}），是半截子"
    return ""


def _norm(s: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")


def detect_trigger(
    text: str,
    *,
    sentence_text: str = "",
    allowed: Sequence[str] = (),
    at_cut: bool = False,
    at_line_start: bool = False,
    is_long_turn: bool = False,
) -> Dict[str, Any]:
    """给一条花字候选找触发理由。返回 {trigger, evidence_text, why}。

    判定优先级：数字 > 章节转折 > 整句回声 > 关键词 > 切点。全都不是就 trigger=None（不许上屏）。
    """
    t = (text or "").strip()
    nt = _norm(t)
    sent = sentence_text or ""
    ns = _norm(sent)
    if not nt:
        return {"trigger": None, "evidence_text": "", "why": "空文案"}

    if NUM_MEASURE_RE.search(t):
        return {"trigger": "number", "evidence_text": NUM_MEASURE_RE.search(t).group(0),
                "why": "数字+量词是这套风格上花字的第一类理由"}
    if SECTION_RE.search(t) or (is_long_turn and ns):
        return {"trigger": "chapter", "evidence_text": t,
                "why": "章节/条目转折（标题式序号或段落落地后的长镜）"}
    if ns and nt == ns:
        return {"trigger": "line_echo", "evidence_text": sent,
                "why": "整句回声：花字就是这句人声的上屏版"}
    if ns and (nt in ns or (len(ns) >= 2 and ns in nt)):
        return {"trigger": "keyword", "evidence_text": sent,
                "why": "关键词出处：文案能在同期人声里逐字找到"}
    if at_line_start and ns:
        return {"trigger": "keyword", "evidence_text": sent,
                "why": "跟着换句上屏（句首）"}
    if at_cut:
        return {"trigger": "cut", "evidence_text": sent or t,
                "why": "贴切点上屏（画面换、字跟着换）"}
    return {"trigger": None, "evidence_text": "", "why": "既不是数字/章节，也不能在同期人声里溯到出处"}


class FlowerPolicy:
    """一套风格的花字门禁。用法：policy.evaluate(candidates, context) -> result。"""

    def __init__(self, style_id: str, rules_path: Optional[Path] = None):
        self.style_id = str(style_id)
        node = style_rules(self.style_id, rules_path)
        self.found = bool(node.get("found"))
        self.rules = node.get("flower") or {}
        scope = self.rules.get("scope") or {}
        shares = self.rules.get("trigger_share") or {}
        # 数字是"内容自证"的触发条件：只要这句人声里真说了数字，把它压上屏不需要风格许可。
        # 其余触发（章节/关键词/整句回声/切点）才受该风格实测配比约束。
        self.always_allowed = set(self.rules.get("always_allowed_triggers") or ["number"])
        # 花字的字面归属天然比音效噪声大（编辑会改写/总结），所以只要这套源草稿里出现过
        # 这种触发条件，就允许运行期用；不允许的是"一次都没出现过"的类型。
        self.allowed_triggers = [t for t in (self.rules.get("allowed_triggers") or [])
                                 if float(shares.get(t, 1.0)) > 0.0]
        if not self.allowed_triggers:
            self.allowed_triggers = list(self.rules.get("allowed_triggers") or [])
        self.max_per_min = float(self.rules.get("max_per_min") or 12.0)
        # 真值里的花字 min_gap 往往只有两三个样本（整片 3 张的情况很常见），直接当硬闸会把
        # 长片卡死；取「实测最小间隔」与「中位间隔 ×0.6」里更宽松的那个，下限 1.0s。
        _g_min = float(self.rules.get("min_gap_sec") or 2.0)
        _g_med = float(self.rules.get("gap_median_sec") or _g_min)
        self.min_gap = max(1.0, min(_g_min, _g_med * 0.6))
        self.chars_min = max(2, int(self.rules.get("chars_min") or 2))
        self.chars_max = max(self.chars_min + 1, int(self.rules.get("chars_max") or 10))
        self.hold_min = max(HARD_MIN_HOLD_SEC, float(self.rules.get("hold_sec_min") or 1.2))
        self.hold_max = max(self.hold_min, float(self.rules.get("hold_sec_max") or 4.0))
        self.allowed_shot_types = set(scope.get("allowed_shot_types") or ["a_roll", "b_roll"])
        self.tol = float(self.rules.get("tol_sec") or 0.12)
        self.colors = self.rules.get("colors") or {}

    def evaluate(
        self,
        candidates: Sequence[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """candidates: [{text, start_sec, dur_sec?, sentence_id?, sentence_text?, keywords?,
                         shot_type?, section?}]
        context: {cut_times, line_starts, sentences, duration_sec, shot_median_sec?}
        """
        duration = float(context.get("duration_sec") or 0.0)
        cuts = sorted_float(context.get("cut_times") or [])
        lines = sorted_float(context.get("line_starts") or [])
        sents = context.get("sentences") or []
        silences = context.get("silence_windows")
        if silences is None:
            silences = silence_windows(sents, duration) if sents else []

        accepted: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        rows: List[Dict[str, Any]] = []
        acc_starts: List[float] = []
        acc_spans: List[tuple] = []

        ordered = sorted(candidates, key=lambda c: float(c.get("start_sec") or 0.0))
        for cand in ordered:
            t = round(float(cand.get("start_sec") or 0.0), 3)
            text = str(cand.get("text") or "").strip()
            sent_txt = str(cand.get("sentence_text") or "")
            base = {"start_sec": t, "text": text, "sentence_id": cand.get("sentence_id"),
                    "origin": cand.get("origin") or ""}

            def drop(why: str, trigger: Optional[str] = None, detail: str = "") -> None:
                dropped.append({**base, "trigger": trigger, "dropped_because": why, "detail": detail})
                rows.append({**base, "decision": "drop", "trigger": trigger or "none", "why": why})

            if not text:
                drop("empty_text")
                continue
            ntext = _norm(text)
            if len(ntext) < self.chars_min:
                drop("text_too_short", None, f"{len(ntext)} 字 < 该风格最少 {self.chars_min} 字")
                continue
            bad = text_quality_issue(text)
            if bad:
                drop("broken_phrase", None, bad)
                continue
            if len(ntext) > self.chars_max + 6:
                drop("text_too_long", None, f"{len(ntext)} 字 > 该风格上限 {self.chars_max}（+6 容差）")
                continue

            off_cut, _ = nearest_offset(t, cuts)
            off_line, _ = nearest_offset(t, lines)
            at_cut = off_cut is not None and off_cut <= self.tol
            at_line = off_line is not None and off_line <= self.tol
            trig = detect_trigger(
                text, sentence_text=sent_txt, allowed=self.allowed_triggers,
                at_cut=at_cut, at_line_start=at_line,
                is_long_turn=bool(cand.get("long_turn")),
            )
            trigger = trig["trigger"]
            if trigger is None:
                drop("no_trigger", None, trig["why"])
                continue
            if trigger not in self.allowed_triggers and trigger not in self.always_allowed:
                drop("trigger_not_in_style", trigger, f"{self.style_id} 的源草稿里没有这种花字触发条件")
                continue

            shot_type = cand.get("shot_type")
            if shot_type and shot_type not in self.allowed_shot_types:
                drop("shot_type_scope", trigger,
                     f"{shot_type} 不在该风格允许的镜头类型 {sorted(self.allowed_shot_types)} 里")
                continue

            if self._silence_ban() and in_windows(t, silences):
                drop("silence_ban", trigger, "落在纯静音段里：没有人声托着，花字上屏没有出处")
                continue

            hold = float(cand.get("dur_sec") or 0.0)
            if hold <= 0:
                hold = min(self.hold_max, max(self.hold_min, 0.28 * max(1, len(ntext)) + 0.9))
            hold = max(self.hold_min, min(self.hold_max, hold))
            end = round(min(duration, t + hold), 3)
            if end - t < HARD_MIN_HOLD_SEC:
                drop("hold_too_short", trigger, f"停留 {end - t:.2f}s < {HARD_MIN_HOLD_SEC}s（会闪一下）")
                continue

            if acc_starts and (t - acc_starts[-1]) < self.min_gap - 1e-6:
                drop("min_gap", trigger, f"距上一张 {t - acc_starts[-1]:.2f}s < {self.min_gap:.2f}s")
                continue
            overlap = any(not (end <= a + 1e-6 or t >= b - 1e-6) for a, b in acc_spans)
            if overlap:
                drop("overlap_previous", trigger, "与上一张花字在时间上叠住（同屏两张字会打架）")
                continue
            if len(acc_starts) > 0 and duration > 0:
                in_min = sum(1 for x in acc_starts if x > t - 60.0)
                if in_min >= max(1, int(math.floor(self.max_per_min))):
                    drop("max_per_min", trigger, f"最近 60s 已满 {self.max_per_min:.0f} 张")
                    continue

            elem = {
                **base,
                "trigger": trigger,
                "anchor_time": round(t if (at_cut or at_line) else t, 3),
                "anchor_kind": "cut" if at_cut else ("line" if at_line else "sentence"),
                "anchor_offset_sec": round(off_cut if at_cut else (off_line or 0.0), 3),
                "evidence_text": trig["evidence_text"],
                "hold_sec": round(end - t, 3),
                "end_sec": end,
                "policy": self.describe(trigger),
                "why": trig["why"],
            }
            accepted.append(elem)
            acc_starts.append(t)
            acc_spans.append((t, end))
            rows.append({**base, "decision": "keep", "trigger": trigger,
                         "anchor_time": elem["anchor_time"], "offset_sec": elem["anchor_offset_sec"],
                         "why": f"{trig['why']} · 停留 {elem['hold_sec']:.2f}s · 出处「{trig['evidence_text'][:12]}」"})

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
                "always_allowed_triggers": sorted(self.always_allowed),
                "max_per_min": self.max_per_min,
                "min_gap_sec": self.min_gap,
                "chars": [self.chars_min, self.chars_max],
                "hold_sec": [self.hold_min, self.hold_max],
                "allowed_shot_types": sorted(self.allowed_shot_types),
                "colors": self.colors,
            },
        }

    def _silence_ban(self) -> bool:
        return True

    def describe(self, trigger: str) -> str:
        return (f"{self.style_id} 花字触发={trigger}"
                f"（allowed={','.join(self.allowed_triggers)} · ≤{self.max_per_min:.0f}/min"
                f" · 间隔≥{self.min_gap:.2f}s · {self.chars_min}~{self.chars_max}字）")


def format_table(result: Dict[str, Any], limit: int = 200) -> str:
    lines = [
        f"花字依据表 · {result.get('style_id')} · 候选 {result.get('n_candidates')} → "
        f"出 {result.get('n_accepted')} / 不出 {result.get('n_dropped')}",
        f"  规则：{result.get('rules_used')}",
        f"  {'时间':>7s}  {'判定':4s} {'触发':9s} {'锚点':>8s}  理由",
    ]
    rows = sorted(result.get("rows") or [], key=lambda r: r.get("start_sec") or 0.0)
    for r in rows[:limit]:
        anchor = r.get("anchor_time")
        lines.append(
            f"  {float(r.get('start_sec') or 0):7.3f}  {'出' if r['decision'] == 'keep' else '不出':4s} "
            f"{str(r.get('trigger') or '-'):9s} {(f'{anchor:.3f}' if anchor is not None else '-'):>8s}  "
            f"{str(r.get('text'))[:12]}｜{r.get('why')}"
        )
    if len(rows) > limit:
        lines.append(f"  … 其余 {len(rows) - limit} 条略")
    return "\n".join(lines)


def policy_for(style_id: str, rules_path: Optional[Path] = None) -> FlowerPolicy:
    return FlowerPolicy(style_id, rules_path)
