"""场景复用预算：原版「1.1 秒一镜也不乱」的真正原因。

2026-11-24 实测（24 套源草稿逐段现算，见 forensic/style_scene_budget.json）：

    style      时长   视频段  素材文件   段/文件   文件/分
    style_21   15.6s    25       6       4.2      23.1
    style_02   29.5s    29       2      14.5       4.1
    style_12   26.3s    14       1      14.0       2.3
    我们这条   60.9s    51      37       1.4      36.4   ← 每条素材只用一次，每镜都换地方

也就是说：原版不是「切得慢」，而是**在极少数素材文件内部反复取不同时间窗**，
所以再怎么碎切，观众始终待在同一个场景里。我们的素材池大、每条只用一次，
于是每秒换一个新场景 —— 这才是「看着乱」的量化根因，而不是元素太多或字幕太快。

所以「切哪条素材」这件事本身有因果规则：
1. 同一场景优先回用**已经用过的那个文件**的另一个时间窗（段/文件 向该风格真值靠）；
2. 每 10 秒引入的新文件数不得超过该风格真值；
3. 只有在语义分明显更高时才允许为新文件破 бюджет —— 语义对位仍然是主序。

数据只读 `assets/style_spectrum/style_scene_budget.json`（由 forensic 同名文件同步）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

SCENE_BUDGET_NAME = "style_scene_budget.json"

_CACHE: Dict[str, Any] = {}


def _budget_paths() -> List[Path]:
    here = Path(__file__).resolve()
    roots = [
        here.parents[1] / "assets" / "style_spectrum" / SCENE_BUDGET_NAME,  # 包内
        here.parents[2] / "forensic" / SCENE_BUDGET_NAME,                    # 仓库根
    ]
    return roots


def load_budgets() -> Dict[str, Any]:
    if "data" in _CACHE:
        return _CACHE["data"]
    for p in _budget_paths():
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            _CACHE["data"] = data
            return data
    _CACHE["data"] = {"styles": {}}
    return _CACHE["data"]


def budget_for(style_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """该风格的场景复用真值；没有就 None（不猜、不兜底成别的风格）。"""
    if not style_id:
        return None
    return (load_budgets().get("styles") or {}).get(str(style_id))


def target_segs_per_file(style_id: Optional[str]) -> Optional[float]:
    b = budget_for(style_id)
    return float(b["segs_per_file"]) if b else None


def measure(clips: List[Dict[str, Any]], duration_sec: float) -> Dict[str, Any]:
    """按成片侧的剪辑表算同一组口径（段、文件、段/文件、文件/分）。"""
    files: List[str] = []
    segs = 0
    for c in clips:
        segs += 1
        mp = c.get("media_path") or c.get("src") or c.get("source") or c.get("normalized_mp4")
        files.append(str(mp or ""))
    uniq = len(set(files))
    counts: Dict[str, int] = {}
    for f in files:
        counts[f] = counts.get(f, 0) + 1
    dur = max(float(duration_sec or 0.0), 0.01)
    return {
        "video_segments": segs,
        "distinct_files": uniq,
        "segs_per_file": round(segs / uniq, 2) if uniq else 0.0,
        "files_per_min": round(uniq / (dur / 60.0), 2),
        "segs_per_min": round(segs / (dur / 60.0), 2),
        "max_segs_on_one_file": max(counts.values()) if counts else 0,
    }


def check(clips: List[Dict[str, Any]], style_id: Optional[str], duration_sec: float,
          *, tol_ratio: float = 0.35) -> Dict[str, Any]:
    """段/文件 是否落在该风格真值的容差内。

    容差取真值的 ±35%：轻量片（段/文件 1.0~1.3 那几套，如 style_04/08/23）本来就用一条一段，
    重仓回用的（style_02/10/12/13/14 达 8~14）也不要求一模一样，只要不是「几乎每镜换新文件」。
    """
    meas = measure(clips, duration_sec)
    tgt = budget_for(style_id)
    if not tgt:
        return {"checked": False, "reason": "该风格没有场景复用真值", "measured": meas}
    target = float(tgt["segs_per_file"])
    lo = max(1.0, target * (1.0 - tol_ratio))
    hi = target * (1.0 + tol_ratio) + 0.5
    ok = lo <= meas["segs_per_file"] <= hi
    notes: List[str] = []
    if not ok:
        if meas["segs_per_file"] < lo:
            notes.append(
                f"每条素材只用 {meas['segs_per_file']} 次（真值 {target}）：镜头几乎每切一次就换一条新素材，"
                f"观众每秒换一个场景，这是「看着乱」的直接来源；应在同一场景内回用同文件的其它时间窗"
            )
        else:
            notes.append(
                f"每条素材用了 {meas['segs_per_file']} 次（真值 {target}）：过度回用同一条素材，观感会变成原地打转"
            )
    return {
        "checked": True,
        "style_id": style_id,
        "verdict": "PASS" if ok else "FAIL",
        "measured": meas,
        "target": {
            "segs_per_file": target,
            "files_per_min": tgt.get("files_per_min"),
            "segs_per_min": tgt.get("segs_per_min"),
            "duration_sec": tgt.get("duration_sec"),
        },
        "accepted_range": [round(lo, 2), round(hi, 2)],
        "notes": notes,
    }


def reuse_bonus(style_id: Optional[str], media_path: str, used_counts: Dict[str, int],
                new_files: int, duration_sec_so_far: float) -> float:
    """选片打分用：回用已用过的文件给正分，引入新文件给负分。

    只在语义分接近时起决定作用——语义对位（broll_match）仍是主序，
    这里最多加/减 REUSE_BONUS_MAX 分，不会把配错的画面救回来。
    """
    tgt = budget_for(style_id)
    if not tgt or not media_path:
        return 0.0
    target = max(1.0, float(tgt["segs_per_file"]))
    used = int(used_counts.get(media_path, 0))
    per_ten = max(0.5, float(tgt.get("files_per_min") or 12.0) / 6.0)  # 每 10 秒允许的新文件数
    budget_new = per_ten * max(0.5, float(duration_sec_so_far or 0.0) / 10.0)
    score = 0.0
    if used > 0:
        score += REUSE_BONUS_MAX * min(1.0, used / target)
    if new_files >= budget_new:
        score -= REUSE_BONUS_MAX * min(1.5, (new_files - budget_new + 1) / max(budget_new, 1.0))
    return round(score, 3)


REUSE_BONUS_MAX = 6.0
