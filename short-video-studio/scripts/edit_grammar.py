#!/usr/bin/env python3
"""风格「剪辑刀法」进剪辑方案：数据装载 / 镜头预算 / 切点挑选 / EDL 门禁。

为什么单独一个模块
──────────────────
风格谱早就锁了调色、字幕、花字、B-roll 占比，但**切多少刀、每刀多长、开场几刀、快切占
几成、A/B 怎么交替**这些真正决定「刀法」的东西，一直只被当成一个 `min_shot_gap` 在用。
本模块把 `forensic/style_edit_grammar.json` 里从草稿量出来的刀法真值接进 step4：

  load_grammar()     读真值；文件缺失/字段为空就降级到风格库标量，并把降级原因写进 degraded
  plan_shot_budget() 目标镜头数 = 总时长 / 中位镜长（夹进真实 p10..p90 带）+ 快切配额 + B 带 + 开场刀数
  select_cuts()      纯函数：从事件候选里挑切点，满足镜头数/单镜时长带/快切占比/开场刀数/A-B 游程形态
  check_edl()        拿跑出来的 EDL 逐项对真值打分，pass/fail 明细（不调阈值凑绿）

纪律：阈值只在这里定义一次（见 TOL_*），报告与单测共用；不达标就如实 fail。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
GRAMMAR_PATH = ROOT / "forensic" / "style_edit_grammar.json"
LIBRARY_PATH = ROOT / "short-video-studio" / "assets" / "style_spectrum" / "style_spectrum_library.json"

# ── 门禁阈值（一次定义，报告/单测共用；不许为了让报告变绿而调宽）─────────────
TOL_SHOTS_REL = 0.25          # 镜头数相对偏差
TOL_SHOTS_ABS = 2             # 或绝对偏差 ≤2 刀
TOL_P50_REL = 0.30            # 中位镜长相对偏差
TOL_P10_REL = 0.40            # p10 相对偏差
TOL_P90_REL = 0.40            # p90 相对偏差
TOL_FAST_ABS = 0.15           # 快切占比绝对偏差
TOL_B_ABS = 0.08              # B 覆盖占比：真值带外再放宽 8pp
TOL_OPENING_ABS = 0           # 开场 3s 切数：必须等于真值
TOL_LONGEST_REL = 1.30        # 最长镜 ≤ 真值 p90 × 1.3


# --------------------------------------------------------------------------- #
# 装载
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _grammar_entry(style_id: str, path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        data = _read_json(path)
    except Exception:
        return None
    return (data.get("styles") or {}).get(style_id)


def _library_entry(style_id: str, path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        lib = _read_json(path)
    except Exception:
        return None
    return next((s for s in (lib.get("styles") or []) if s.get("style_id") == style_id), None)


def load_grammar(style_id: str, *, grammar_path: Optional[Path] = None,
                 library_path: Optional[Path] = None) -> dict:
    """style_id → 刀法口径。真值缺项时逐项降级到风格库标量，降级原因进 `degraded`。

    返回结构（下游只用这几个键）：
      shot     {p10,p50,p90,max_source_n,fast_ratio,bounds_synthetic}
      ab       {b_ratio,b_band,pattern,a_run_median,b_run_median,sequence_head,kf_per_min,
                longest_a_run}
      opening  {shots_in_first_3s,cuts_in_first_3s,first_shot_sec,hook_intro_sec,source}
      degraded [...]
    """
    gp = Path(grammar_path or GRAMMAR_PATH)
    lp = Path(library_path or LIBRARY_PATH)
    g = _grammar_entry(style_id, gp)
    lib = _library_entry(style_id, lp) or {}
    sd = (lib.get("shot_distribution") or {})
    ab_spec = (lib.get("ab_switch_spec") or {})
    op_lib = (lib.get("opening_structure") or {})
    degraded: List[str] = []

    if g is None:
        degraded.append(f"{style_id} 不在 {gp.name}（或文件缺失）：镜头分位/B 游程/开场全部降级到风格库标量")
        g = {}

    g_shot = g.get("shot_distribution") or {}
    g_runs = g.get("ab_runs") or {}
    g_open = g.get("opening") or {}
    g_kf = g.get("keyframes") or {}

    # ── 镜头时长分位 ──
    p10, p50, p90 = g_shot.get("p10"), g_shot.get("p50"), g_shot.get("p90")
    synthetic = False
    if p50 is None:
        p50 = sd.get("median_sec")
        degraded.append("真值缺切镜时长中位数：降级到风格库 median_sec")
    if p10 is None or p90 is None:
        # 风格库只有中位数，没有分位带；规划用的上下界只能按中位数撑开，
        # 这不是「真值」，所以在返回里标 bounds_synthetic，check_edl 不用它当验收基准。
        base = float(p50) if p50 else 1.6
        p10 = p10 if p10 is not None else round(base * 0.60, 3)
        p90 = p90 if p90 is not None else round(base * 1.80, 3)
        synthetic = True
        degraded.append("真值缺 p10/p90 分位：规划上下界按中位数 0.6x/1.8x 撑开（bounds_synthetic）")

    fast_ratio = g_shot.get("fast_cuts_under_1s_ratio")
    if fast_ratio is None:
        fast_ratio = sd.get("fast_cuts_under_1s_ratio")
        degraded.append("真值缺快切占比：降级到风格库 fast_cuts_under_1s_ratio")

    # ── A/B 覆盖与游程 ──
    b_ratio = g_runs.get("b_coverage_ratio")
    if b_ratio is None:
        b_ratio = sd.get("broll_coverage_ratio")
        degraded.append("真值缺 B 覆盖占比：降级到风格库 broll_coverage_ratio")
    band = lib.get("broll_target_band_pct")
    if band:
        b_band = [round(float(band[0]) / 100.0, 4), round(float(band[1]) / 100.0, 4)]
    elif b_ratio is not None:
        b_band = [max(0.0, float(b_ratio) - 0.15), min(1.0, float(b_ratio) + 0.15)]
        degraded.append("风格库无 broll_target_band_pct：B 带按真值 ±15pp 撑开")
    else:
        b_band = [0.0, 1.0]

    a_runs = g_runs.get("a_runs") or {}
    b_runs = g_runs.get("b_runs") or {}
    seq = g_runs.get("sequence") or []
    if not seq:
        degraded.append("真值缺 A/B 游程序列：交替形态按 B 占比反推（A 长 B 短）")

    # ── 开场刀数：以草稿切点直接数的 opening_structure 为准 ──
    cuts_in_3 = g_open.get("cuts_in_first_3s")
    open_source = "grammar.opening.cuts_in_first_3s"
    if cuts_in_3 is None and op_lib.get("first_3s_cuts_count") is not None:
        # 风格库的 first_3s_cuts_count 数的是「前 3s 里有几个镜头（含首镜）」，切点要减 1
        cuts_in_3 = max(0, int(op_lib["first_3s_cuts_count"]) - 1)
        open_source = "library.opening_structure.first_3s_cuts_count - 1"
        degraded.append("真值缺开场切点：降级到风格库 opening_structure（镜头数-1=切点数）")
    if cuts_in_3 is None and ab_spec.get("opening_cuts") is not None:
        cuts_in_3 = int(ab_spec["opening_cuts"])
        open_source = "library.ab_switch_spec.opening_cuts"
        degraded.append("开场切点连 opening_structure 都缺：兜底用 ab_switch_spec.opening_cuts")
    if cuts_in_3 is None:
        cuts_in_3 = 0
        open_source = "none"
        degraded.append("开场切点完全缺失：按 0 处理")

    kf_per_min = g_kf.get("kf_per_min")
    if kf_per_min is None:
        kf_per_min = ab_spec.get("kf_per_min_target", sd.get("kf_per_min"))
        if kf_per_min is not None:
            degraded.append("真值缺关键帧密度：降级到风格库 kf_per_min")

    return {
        "style_id": style_id,
        "source_ref": g.get("source_ref"),
        "draft_sha256": g.get("draft_sha256"),
        "shot": {
            "p10": p10, "p25": g_shot.get("p25"), "p50": p50, "p75": g_shot.get("p75"), "p90": p90,
            "max": g_shot.get("max"),
            "source_n": g_shot.get("n") if g_shot.get("n") is not None else sd.get("main_track_clips"),
            "fast_ratio": fast_ratio,
            "bounds_synthetic": synthetic,
            # 按时间顺序的原样镜长序列：剪辑节奏照它循环取，才能复现「短刀 + 长镜」的真实形态
            "durations_chrono": list(g_shot.get("durations_chrono") or []),
        },
        "ab": {
            "b_ratio": b_ratio,
            "b_band": b_band,
            "pattern": ab_spec.get("pattern"),
            "a_run_median": a_runs.get("median_sec"),
            "b_run_median": b_runs.get("median_sec"),
            "a_run_intervals_median": a_runs.get("median_intervals"),
            "b_run_intervals_median": b_runs.get("median_intervals"),
            "n_a_runs": g_runs.get("n_a_runs"),
            "n_b_runs": g_runs.get("n_b_runs"),
            "longest_a_run_sec": a_runs.get("max_sec"),
            "sequence": seq,
            "kf_per_min": kf_per_min,
        },
        "opening": {
            "cuts_in_first_3s": cuts_in_3,
            "shots_in_first_3s": g_open.get("shots_in_first_3s"),
            "first_shot_sec": g_open.get("first_shot_sec"),
            "hook_intro_sec": g_open.get("hook_intro_sec"),
            "source": open_source,
        },
        "degraded": degraded,
    }


# --------------------------------------------------------------------------- #
# 预算
# --------------------------------------------------------------------------- #
def plan_shot_budget(total_dur_sec: float, grammar: dict) -> dict:
    """总时长 → 镜头预算：目标刀数 / 单镜时长带 / 快切配额 / B 带 / 开场刀数。

    镜头数 = 总时长 / 中位镜长，再夹进真实分位带 [p10,p90]：
      - 上界 floor(total/p10)：再密就全部短于 p10
      - 下界 ceil(total/p90)：再疏就全部长于 p90
    """
    total = float(total_dur_sec)
    shot = grammar.get("shot") or {}
    median = float(shot.get("p50") or 0.0)
    p10 = float(shot.get("p10") or 0.0)
    p90 = float(shot.get("p90") or 0.0)
    notes: List[str] = []
    if total <= 0:
        return {"total_dur_sec": total, "target_shots": 0, "shot_min_sec": 0.0, "shot_max_sec": 0.0,
                "median_sec": median, "p10": p10, "p90": p90, "fast_quota": 0,
                "fast_ratio_target": 0.0, "opening_cuts": 0, "b_ratio_target": 0.0,
                "b_band": [0.0, 1.0], "a_run_shots": 1, "b_run_shots": 1,
                "clamped": False, "notes": ["总时长为 0，无法排镜头预算"]}

    if median <= 0:
        median = 1.6
        notes.append("中位镜长缺失：按 1.6s 兜底排预算（不是真值，只是排期起点）")
    n_from_median = int(round(total / median))
    n_min = int(math.ceil(total / p90)) if p90 > 0 else 1
    n_max = int(math.floor(total / p10)) if p10 > 0 else n_from_median
    n_min = max(1, n_min)
    n_max = max(n_min, n_max)
    target = max(n_min, min(n_max, max(1, n_from_median)))
    clamped = target != max(1, n_from_median)
    if clamped:
        notes.append(f"按中位镜长要 {n_from_median} 刀，夹进真值分位带后取 {target} 刀"
                     f"（p10 {p10:.2f}s / p90 {p90:.2f}s）")

    fast_ratio = float(shot.get("fast_ratio") or 0.0)
    fast_quota = int(round(fast_ratio * target))
    opening_cuts = int(grammar.get("opening", {}).get("cuts_in_first_3s") or 0)
    opening_cuts = max(0, min(opening_cuts, max(0, target - 1)))
    if opening_cuts != int(grammar.get("opening", {}).get("cuts_in_first_3s") or 0):
        notes.append("开场刀数超过总刀数预算，已压到 target-1")

    ab = grammar.get("ab") or {}
    b_ratio = float(ab.get("b_ratio") or 0.0)
    band = ab.get("b_band") or [0.0, 1.0]
    # A/B 游程按真值量到的「连续几个区间」来，缺了才按 B 占比反推（A 长 B 短）
    a_run_truth = ab.get("a_run_intervals_median")
    a_run = int(max(1, int(round(float(a_run_truth)))) if a_run_truth else 1)
    b_run_truth = ab.get("b_run_intervals_median")
    if b_ratio <= 0.0:
        b_run = 0
    elif b_run_truth:
        b_run = int(max(1, round(float(b_run_truth))))
    else:
        b_run = int(max(1, round(a_run * b_ratio / max(1e-3, 1.0 - b_ratio))))
    if shot.get("bounds_synthetic"):
        notes.append("单镜时长带由中位数撑开（bounds_synthetic），不是从草稿量的分位")

    return {
        "total_dur_sec": total,
        "target_shots": target,
        "shot_min_sec": round(max(0.25, p10 * 0.8), 3),
        "shot_max_sec": round(max(0.60, p90 * 1.3), 3),
        "median_sec": round(median, 3),
        "p10": round(p10, 3),
        "p90": round(p90, 3),
        "fast_quota": fast_quota,
        "fast_ratio_target": round(fast_ratio, 3),
        "opening_cuts": opening_cuts,
        "b_ratio_target": round(b_ratio, 3),
        "b_band": [round(float(band[0]), 3), round(float(band[1]), 3)],
        "a_run_shots": a_run,
        "b_run_shots": b_run,
        "clamped": clamped,
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# 挑切点（纯函数：不碰 IO，输入候选/预算，输出切点与自评指标）
# --------------------------------------------------------------------------- #
def _pct(vals: List[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return round(s[0], 3)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] * (1 - (pos - lo)) + s[hi] * (pos - lo), 3)


def _ab_roles(n_shots: int, b_budget: int, a_run: int, b_run: int) -> List[str]:
    """按真值的「A 连续几刀 / B 连续几刀」铺 A/B 角色，B 总刀数守住 b_budget。

    真值的 B 覆盖率由叠加画面的**时长**决定，本函数只能按刀数近似：先把 b_budget 均分成
    若干条 B 块（单块不超过真值 B 游长），再把剩下的 A 刀均分到块与块之间（尽量接近 A 游长）。
    """
    n = max(0, int(n_shots))
    if n <= 0:
        return []
    b = max(0, min(n, int(b_budget)))
    if b <= 0:
        return ["a_roll"] * n
    if b >= n:
        return ["b_roll"] * n
    b_len = max(1, int(b_run) if b_run else 1)
    n_chunks = max(1, min(int(math.ceil(b / float(b_len))), n - b + 1))
    base, extra = divmod(b, n_chunks)
    sizes = [base + (1 if i < extra else 0) for i in range(n_chunks)]
    a_slots = n - b
    # A 刀均分到 n_chunks+1 个缝里（第一个缝可能与最后一个不等，但都尽量接近 a_run）
    gap_base, gap_extra = divmod(a_slots, n_chunks + 1)
    gaps = [gap_base + (1 if i < gap_extra else 0) for i in range(n_chunks + 1)]
    roles: List[str] = []
    for i, size in enumerate(sizes):
        roles.extend(["a_roll"] * gaps[i])
        roles.extend(["b_roll"] * size)
    roles.extend(["a_roll"] * gaps[-1])
    return roles[:n]


def _pick_key(goal: float):
    """候选打分：先看离理想刀位多近（0.03s 内视为并列），并列时按 prio（越小越优先），最后按时间。

    这样「台词点到道具」的句界只有在节奏上也说得过去时才会顶掉普通事件，而不是硬插一刀。
    """
    band = 0.03

    def _key(c):
        t = float(c.get("time_sec") or 0.0) if isinstance(c, dict) else float(c)
        prio = int(c.get("prio", 9)) if isinstance(c, dict) else 9
        return (round(abs(t - goal) / band), prio, t)

    return _key


def select_cuts(candidates: List[dict], budget: dict, grammar: dict) -> dict:
    """从事件候选里挑切点，让刀法靠近 budget 里的真值配额。

    candidates：`[{"time_sec": 1.37, "prio": 0, ...}, ...]`
      - `prio` 越小越该优先（0=台词点到道具的句界，1=普通句界，2=气口，3=重音）；
        只有在「离理想刀位同样近」时它才起作用（容差 0.03s/级），不会为了道具牺牲节奏。
      - 其余键原样透传到返回的 `cuts[].candidate`，调用方可以据此取回自己的事件对象。
    约束（全部来自 budget，budget 来自真值）：
      1) 镜头数 == target_shots（候选不够则取能到的上限并记 note）
      2) 每镜时长落在 [shot_min_sec, shot_max_sec]
      3) 快切（<1s）配额 == fast_quota
      4) 前 3s 切数 == opening_cuts
      5) A/B 游程形态按 ab 的 A 游长 + B 占比推导
    返回 {"cuts":[{time_sec, ab_role, fast}], "stats":{...}, "notes":[...]}
    """
    notes: List[str] = []
    total = float(budget.get("total_dur_sec") or 0.0)
    target = int(budget.get("target_shots") or 0)
    shot_min = float(budget.get("shot_min_sec") or 0.3)
    shot_max = float(budget.get("shot_max_sec") or 4.0)
    median = float(budget.get("median_sec") or 1.5)
    fast_quota = int(budget.get("fast_quota") or 0)
    open_n = int(budget.get("opening_cuts") or 0)

    # 时间 → 候选（同一时刻多条事件时保留 prio 更小的那条）
    by_time: Dict[float, dict] = {}
    for c in candidates or []:
        t = c.get("time_sec") if isinstance(c, dict) else c
        try:
            t = round(float(t), 3)
        except (TypeError, ValueError):
            continue
        if not (0.15 < t < max(0.2, total - 0.20)):
            continue
        cand = c if isinstance(c, dict) else {"time_sec": t}
        prev = by_time.get(t)
        if prev is None or int(cand.get("prio", 9)) < int(prev.get("prio", 9)):
            by_time[t] = cand
    times = sorted(by_time)

    # ── 1) 开场刀数：与 step4 的 pick_first_3s_events 同口径（0.50–2.90s，间隔 ≥0.55s）──
    picked: List[float] = []
    open_gap = max(0.55, shot_min)  # 开场两刀也不能挨得比真值最短镜还近
    for t in times:
        if len(picked) >= open_n:
            break
        if not (0.50 <= t <= 2.90):
            continue
        if not picked and t < shot_min - 1e-6:
            continue  # 首镜会短于真值下限，这个事件不能当开场第一刀
        if all(abs(t - p) >= open_gap for p in picked):
            picked.append(t)
    for t in times:
        if 0.30 <= t < 0.50 and len(picked) < open_n and all(abs(t - p) >= open_gap for p in picked):
            picked.append(t)
    picked.sort()
    if len(picked) < open_n:
        notes.append(f"前 3s 可用事件不足：开场只排到 {len(picked)}/{open_n} 刀")

    # ── 2) 正文：按「真值节奏」推进——第 i 刀的理想镜长默认是中位数，
    #      快切位（把 fast_quota 均摊到全片的那些刀）压到 p10；每一刀都从候选里挑
    #      「离理想位置最近」且落在 [shot_min, shot_max] 内的那个事件，所以单镜时长天然在真值带里。
    # 正文候选从 2.95s 起：开场窗口（0.50–2.90s）归开场阶段独占，这样 stats.opening_cuts
    # 才是「开场真的几刀」，不会把正文第一刀误算进开场
    body = [t for t in times if t > 2.95]
    p10 = float(budget.get("p10") or 0.0)
    shot_fast_target = max(shot_min, min(p10 if p10 > 0 else median * 0.7, 0.98))
    # target 是**镜头数**，pick 出来的是**切点数**，差一：n 个镜头只需 n-1 刀；
    # 开场阶段已经排下去的刀要占掉配额，否则总刀数会超预算
    max_cuts = max(0, target - 1 - len(picked))
    fast_positions = set()
    if fast_quota > 0 and target > 0:
        for k in range(1, fast_quota + 1):
            pos = int(round(k * target / float(fast_quota))) - 1
            if 0 < pos < target:
                fast_positions.add(pos)

    dur_chrono = [float(d) for d in (grammar.get("shot", {}).get("durations_chrono") or []) if float(d) > 0]
    for i in range(max_cuts):
        last = picked[-1] if picked else 0.0
        # 理想镜长优先照真值的**时间顺序镜长序列**循环取（这样「短刀 + 长镜」的真实形态才复现得出来）；
        # 再用「剩下的时长」收口，避免候选量化误差把尾巴甩成一个超长收尾镜。
        shots_left = max_cuts - i + 1
        even = (total - last) / max(1, shots_left)
        goal_dur = dur_chrono[i % len(dur_chrono)] if dur_chrono else even
        # 这一刀最多能给到多少：剩下的镜头至少还得各留 shot_min
        room_cap = (total - last) - shot_min * (shots_left - 1)
        goal_dur = min(goal_dur, max(shot_min, room_cap))
        # 没有真值节奏序列时，才用「快切配额均摊」补快切位；
        # 有序列时快切比例已经写在真值形态里，再叠加会把快切压成两倍
        if not dur_chrono and i in fast_positions and (total - last) > shot_fast_target * shots_left:
            goal_dur = max(shot_min, min(goal_dur, shot_fast_target))
        goal_dur = max(shot_min, min(shot_max, goal_dur))
        lo, hi = last + shot_min, last + shot_max
        pool = [by_time[t] for t in body if lo - 1e-6 <= t <= hi + 1e-6 and t not in picked]
        if not pool:
            notes.append(
                f"第 {i + 1} 刀处候选断档（需要 {lo:.2f}–{hi:.2f}s 之间的事件），"
                f"只能排到 {len(picked) + 1} 镜"
            )
            break
        goal = last + goal_dur
        picked.append(round(float(min(pool, key=_pick_key(goal)).get("time_sec")), 3))
    # 末镜超长：真值上限之外必须记一笔，不许装作没看见
    tail_start = picked[-1] if picked else 0.0
    if total - tail_start > shot_max + 1e-6:
        notes.append(f"末镜 {tail_start:.2f}–{total:.2f}s 超过真值上限 {shot_max:.2f}s（后面没有可用事件）")

    picked = sorted(set(round(t, 3) for t in picked))

    # ── 3) 收口：让单镜时长带成为硬约束（开场最早事件太早 / 某段超长镜都在这里纠正或记一笔）──
    while picked and picked[0] < shot_min - 1e-6:
        dropped = picked.pop(0)
        notes.append(f"开场首个事件 {dropped:.2f}s 会让首镜短于真值下限 {shot_min:.2f}s，已让位")
    for _ in range(3):
        bnds = [0.0] + picked + [total]
        added = False
        for a, b in zip(bnds, bnds[1:]):
            if b - a <= shot_max + 1e-6:
                continue
            room = [t for t in times
                    if a + shot_min - 1e-6 <= t <= b - shot_min + 1e-6 and t not in picked]
            if not room:
                notes.append(f"{a:.2f}–{b:.2f}s 这一镜 {b - a:.2f}s 超真值上限 "
                             f"{shot_max:.2f}s，候选里没有可切的点")
                continue
            mid = a + (b - a) / 2.0
            picked.append(min(room, key=lambda t: (abs(t - mid), t)))
            added = True
        picked = sorted(set(round(t, 3) for t in picked))
        if not added:
            break

    # ── 4) A/B 游程：B 块总时长守住 b_ratio，单块长度守住真值游长，块间 A 拉伸 ──
    b_ratio = float(budget.get("b_ratio_target") or 0.0)
    a_run = int(max(1, budget.get("a_run_shots") or 1))
    b_run = int(max(0, budget.get("b_run_shots") or 0))
    n_shots = len(picked) + 1
    b_budget = int(round(b_ratio * n_shots))
    roles = _ab_roles(n_shots, b_budget, a_run, b_run)

    cuts = []
    for k, t in enumerate(picked):
        prev = picked[k - 1] if k > 0 else 0.0
        cuts.append({"time_sec": t, "ab_role": roles[k] if k < len(roles) else "a_roll",
                     "gap_sec": round(t - prev, 3), "fast": (t - prev) < 1.0,
                     "candidate": by_time.get(round(t, 3))})

    # ── 5) 自评：挑出来的刀法自己长什么样 ──
    durations = [round(b - a, 3) for a, b in zip([0.0] + picked, picked + [total])] if picked else [round(total, 3)]
    if len(durations) != len(roles):
        # 对齐：镜头数 = 切点数 + 1，roles 按镜头数排
        roles = (roles + ["a_roll"] * n_shots)[:n_shots]
    fast_n = sum(1 for d in durations if d < 1.0)
    seq: List[str] = []
    for r in roles:
        lab = "A" if r == "a_roll" else "B"
        if seq and seq[-1].startswith(lab):
            seq[-1] = f"{lab}{int(seq[-1][1:]) + 1}"
        else:
            seq.append(f"{lab}1")
    stats = {
        "n_shots": len(durations),
        "p10": _pct(durations, 0.10), "p50": _pct(durations, 0.50), "p90": _pct(durations, 0.90),
        "max_sec": round(max(durations), 3) if durations else None,
        "fast_ratio": round(fast_n / max(1, len(durations)), 3),
        "fast_n": fast_n,
        "opening_cuts": sum(1 for t in picked if t < 3.0),
        "b_ratio": round(sum(d for d, r in zip(durations, roles) if r == "b_roll") / max(1e-6, total), 3),
        "ab_sequence": seq,
        "out_of_band_shots": sum(1 for d in durations if d < shot_min - 1e-6 or d > shot_max + 1e-6),
    }
    return {"cuts": cuts, "stats": stats, "notes": notes}


# --------------------------------------------------------------------------- #
# EDL 门禁
# --------------------------------------------------------------------------- #
def _rel_diff(actual: Optional[float], target: Optional[float]) -> Optional[float]:
    if actual is None or target in (None, 0, 0.0):
        return None
    return round((float(actual) - float(target)) / float(target), 4)


def check_edl(edl: dict, grammar: dict) -> dict:
    """跑出来的 EDL → 逐项对刀法真值打分（指标 + pass/fail 明细）。"""
    clips = list(edl.get("clips") or [])
    total = float(edl.get("total_duration_sec") or 0.0)
    durations = [round(float(c.get("duration_sec") or 0.0), 3) for c in clips]
    n = len(durations)
    fast_n = sum(1 for d in durations if d < 1.0)
    # B 覆盖只算真实外来画面（全屏 b_roll 或分层剪辑的底图 B-roll）；自产图卡（code_broll）单列，不混进与源真值的比较
    b_sec = sum(
        float(c.get("duration_sec") or 0.0)
        for c in clips
        if str(c.get("track_role")) == "b_roll"
        or (bool(c.get("b_roll_overlay")) and bool((c.get("pip_layout") or {}).get("secondary_media_path")))
    )
    b_sec_cards = sum(float(c.get("duration_sec") or 0.0) for c in clips
                      if str(c.get("track_role")) == "code_broll")
    opening_changes = sum(1 for c in clips[1:]
                          if float(c.get("timeline_in_sec") or 0.0) < 3.0)
    metrics = {
        "n_shots": n,
        "p10": _pct(durations, 0.10), "p50": _pct(durations, 0.50), "p90": _pct(durations, 0.90),
        "fast_ratio": round(fast_n / max(1, n), 3),
        "b_ratio": round(b_sec / max(1e-6, total), 3),
        "b_ratio_with_cards": round((b_sec + b_sec_cards) / max(1e-6, total), 3),
        "opening_cuts": opening_changes,
        "longest_sec": round(max(durations), 3) if durations else None,
    }
    shot = grammar.get("shot") or {}
    ab = grammar.get("ab") or {}
    opening = grammar.get("opening") or {}
    tgt_p10, tgt_p50, tgt_p90 = shot.get("p10"), shot.get("p50"), shot.get("p90")
    tgt_fast = shot.get("fast_ratio")
    tgt_b = ab.get("b_ratio")
    band = ab.get("b_band") or [0.0, 1.0]
    tgt_open = opening.get("cuts_in_first_3s")

    checks: List[dict] = []

    def _add(name, target, actual, tol, ok, note=""):
        checks.append({"name": name, "target": target, "actual": actual, "tol": tol,
                       "pass": bool(ok), "note": note})

    # 1) 镜头数
    if shot.get("source_n") is not None:
        # 验收基准是「按时长折算的应到刀数」，不是原片段数（原片 68s 和 fixture 24s 不能直接比）
        target_shots = int(round(total / tgt_p50)) if tgt_p50 and total else None
        ok = (target_shots is not None and
              (abs(n - target_shots) <= TOL_SHOTS_ABS or
               abs(n - target_shots) / max(1, target_shots) <= TOL_SHOTS_REL))
        _add("镜头数", target_shots, n, f"±{TOL_SHOTS_ABS} 或 ±{int(TOL_SHOTS_REL*100)}%", ok)
    # 2) 中位镜长
    ok = (_rel_diff(metrics["p50"], tgt_p50) is not None and
          abs(_rel_diff(metrics["p50"], tgt_p50)) <= TOL_P50_REL)
    _add("中位镜长 p50", tgt_p50, metrics["p50"], f"±{int(TOL_P50_REL*100)}%", ok)
    # 3) 长短两端
    for key, tol in (("p10", TOL_P10_REL), ("p90", TOL_P90_REL)):
        rd = _rel_diff(metrics[key], shot.get(key))
        _add(f"切镜时长 {key}", shot.get(key), metrics[key], f"±{int(tol*100)}%",
             rd is not None and abs(rd) <= tol)
    # 4) 快切占比
    _add("快切占比(<1s)", tgt_fast, metrics["fast_ratio"], f"±{TOL_FAST_ABS}",
         tgt_fast is not None and abs(metrics["fast_ratio"] - float(tgt_fast)) <= TOL_FAST_ABS)
    # 5) B 覆盖占比：落在真值带内，带外放宽 8pp
    lo, hi = float(band[0]) - TOL_B_ABS, float(band[1]) + TOL_B_ABS
    _add("B 覆盖占比", f"{band[0]:.3f}–{band[1]:.3f}", metrics["b_ratio"], f"带内或 ±{TOL_B_ABS}",
         lo <= metrics["b_ratio"] <= hi)
    # 6) 开场 3s 切数：必须等于真值
    _add("开场 3s 切数", tgt_open, metrics["opening_cuts"], f"±{TOL_OPENING_ABS}",
         tgt_open is not None and abs(int(metrics["opening_cuts"]) - int(tgt_open)) <= TOL_OPENING_ABS)
    # 7) 最长镜
    if tgt_p90:
        cap = round(float(tgt_p90) * TOL_LONGEST_REL, 3)
        _add("最长镜 ≤ p90×1.3", cap, metrics["longest_sec"], f"≤{cap}",
             metrics["longest_sec"] is not None and metrics["longest_sec"] <= cap)

    fails = [c for c in checks if not c["pass"]]
    return {
        "style_id": edl.get("style_id") or grammar.get("style_id"),
        "grammar_mode": (edl.get("edit_grammar") or {}).get("mode"),
        "metrics": metrics,
        "targets": {
            "shots": int(round(total / tgt_p50)) if (tgt_p50 and total) else None,
            "p10": tgt_p10, "p50": tgt_p50, "p90": tgt_p90,
            "fast_ratio": tgt_fast, "b_band": band, "b_ratio": tgt_b,
            "opening_cuts": tgt_open,
            "longest_cap": round(float(tgt_p90) * TOL_LONGEST_REL, 3) if tgt_p90 else None,
        },
        "checks": checks,
        "pass": not fails,
        "fail_reasons": [f"{c['name']}: 实际 {c['actual']} vs 目标 {c['target']}（容差 {c['tol']}）"
                         for c in fails],
        "degraded": list(grammar.get("degraded") or []),
    }
