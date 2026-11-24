#!/usr/bin/env python3
"""风格指纹：把每套风格的音效/花字可测指标做成指纹，复用时用来证明「这就是这套风格」。

用户的要求：「风格模版是要围绕着怎样可复用而成立，复用后又从哪些点可以印证这就是对应的风格。」

两个入口
────────
· `fingerprint(style_id)`：这套风格**应该在**什么范围（源自 24 套源草稿实测表）。
· `verify(style_id, pipeline_dir)`：这次**实际**做出来的 `06_packaging.json` 是否落在这个范围里，
  逐项 PASS/偏差，并给出「最像哪套风格」的排名——套错风格能当场看出来。

可测项
──────
音效：角色配比、每分密度、触发类型配比、最短间隔、音效来源目录（禁跨风格串味）
花字：每分密度、触发类型配比、平均字数、配色、停留时长
节拍：BPM（风格 BGM 的实测值）

用法：
    python3 scripts/style_fingerprint.py --style style_21 --pipeline-dir ../pipeline
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from trigger_policy_common import style_rules  # type: ignore
except ImportError:  # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from trigger_policy_common import style_rules  # type: ignore

# 判定带宽：密度允许低到源草稿的 35%（策略更严更干净），高过 110% 就算过密
FLOWER_TRIGGERS = frozenset({"number", "chapter", "keyword", "line_echo", "cut",
                             "card", "fragment", "free"})
SFX_PPM_BAND = (0.35, 1.10)
FLOWER_PPM_BAND = (0.20, 1.20)
TRIGGER_SHARE_SLACK = 0.25
ROLE_COS_MIN = 0.60
BPM_TOLERANCE = 0.04


def _share(counts: Dict[str, Any]) -> Dict[str, float]:
    total = sum(float(v or 0) for v in (counts or {}).values())
    if total <= 0:
        return {}
    return {str(k): round(float(v or 0) / total, 4) for k, v in counts.items() if float(v or 0) > 0}


def fingerprint(style_id: str, rules_path: Optional[Path] = None) -> Dict[str, Any]:
    """这套风格的可测指纹（音效 + 花字 + BPM）。"""
    node = style_rules(str(style_id), rules_path)
    sfx = node.get("sfx") or {}
    flower = node.get("flower") or {}
    beat = node.get("beat") or {}
    roles = {str(k): int((v or {}).get("n") or 0) for k, v in (sfx.get("roles") or {}).items()}
    trig = {k: int(v or 0) for k, v in (sfx.get("trigger_mix") or {}).items()}
    ftrig = {k: int(v or 0) for k, v in (flower.get("trigger_mix") or {}).items()}
    return {
        "style_id": str(style_id),
        "found": bool(node.get("found")),
        "sample_duration_sec": node.get("duration_sec"),
        "sfx": {
            "n_cues": int(sfx.get("n_cues") or 0),
            "per_min": float(sfx.get("per_min") or 0.0),
            "max_per_min": float(sfx.get("max_per_min") or 0.0),
            "min_gap_sec": float(sfx.get("min_gap_sec") or 0.0),
            "role_shares": _share(roles),
            "role_counts": roles,
            "trigger_shares": _share(trig),
            "allowed_triggers": list(sfx.get("allowed_triggers") or []),
            "broll_share": (sfx.get("scope") or {}).get("broll_share"),
        },
        "flower": {
            "n_cues": int(flower.get("n_cues") or 0),
            "per_min": float(flower.get("per_min") or 0.0),
            "max_per_min": float(flower.get("max_per_min") or 0.0),
            "trigger_shares": _share(ftrig),
            "allowed_triggers": list(flower.get("allowed_triggers") or []),
            "always_allowed_triggers": list(flower.get("always_allowed_triggers") or ["number"]),
            "chars": [int(flower.get("chars_min") or 2), int(flower.get("chars_max") or 10)],
            "chars_median": flower.get("chars_median"),
            "hold_sec": [flower.get("hold_sec_min"), flower.get("hold_sec_max")],
            "colors": dict(flower.get("colors") or {}),
            "fonts": flower.get("fonts") or [],
            "zones": (flower.get("scope") or {}).get("zones") or [],
        },
        "beat": {"bpm": beat.get("bpm"), "confidence": beat.get("confidence")},
    }


def _load_packaging(pipeline_dir: Path) -> Dict[str, Any]:
    p = Path(pipeline_dir) / "06_packaging.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sfx_dir_of(file_path: str) -> str:
    p = Path(str(file_path or ""))
    parts = p.parts
    if "sfx" in parts:
        i = parts.index("sfx")
        if i + 1 < len(parts):
            return parts[i + 1]
    return ""


def measure_pipeline(pipeline_dir: Path, style_id: Optional[str] = None) -> Dict[str, Any]:
    """把一次成片的音效/花字实测指标抽出来（与 fingerprint 同构，便于直接比）。"""
    pkg = _load_packaging(pipeline_dir)
    cues = pkg.get("sfx_cues") or []
    # 花字的判定口径：带花字触发类型的元素才算花字；开场巨字/常驻顶栏/片尾 CTA 是结构件
    # （trigger=hook/ending），不参与花字密度与配比。老产物没有 trigger 字段时退回按 kind 筛。
    _all_vis = list(pkg.get("visual_elements") or [])
    _has_trigger = any(v.get("trigger") for v in _all_vis)
    if _has_trigger:
        vis = [v for v in _all_vis if str(v.get("trigger") or "") in FLOWER_TRIGGERS]
    else:
        vis = [v for v in _all_vis if str(v.get("kind") or "") not in
               ("center_burst_title", "persistent_top_bar", "end_card_cta")]
    dur = 0.0
    for name in ("03_audio_events.json", "04_edl.json"):
        p = Path(pipeline_dir) / name
        if not p.is_file():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        dur = float(d.get("locked_duration_sec") or d.get("total_duration_sec") or 0.0)
        if dur > 0:
            break
    if dur <= 0 and pkg.get("budget"):
        dur = 0.0

    role_counts: Dict[str, int] = {}
    trig_counts: Dict[str, int] = {}
    src_dirs: Dict[str, int] = {}
    times: List[float] = []
    for c in cues:
        role_counts[str(c.get("role") or "misc")] = role_counts.get(str(c.get("role") or "misc"), 0) + 1
        trig = str(c.get("reason") or (c.get("anchor") or {}).get("kind") or "free")
        trig_counts[trig] = trig_counts.get(trig, 0) + 1
        src_dirs[_sfx_dir_of(c.get("file"))] = src_dirs.get(_sfx_dir_of(c.get("file")), 0) + 1
        try:
            times.append(float(c.get("time_sec") or 0.0))
        except (TypeError, ValueError):
            pass
    times.sort()
    gaps = [round(times[i + 1] - times[i], 3) for i in range(len(times) - 1)]

    ftrig: Dict[str, int] = {}
    chars: List[int] = []
    holds: List[float] = []
    fg_colors: Dict[str, int] = {}
    for e in vis:
        t = str(e.get("trigger") or "unknown")
        ftrig[t] = ftrig.get(t, 0) + 1
        txt = str(e.get("text_or_asset") or "").strip().strip("“”")
        if txt:
            chars.append(len(txt))
        try:
            holds.append(round(float(e.get("read_hold_sec") or 0.0), 2))
        except (TypeError, ValueError):
            pass
        fg = str(e.get("fg_hex") or "")
        if fg:
            fg_colors[fg] = fg_colors.get(fg, 0) + 1

    bpm = None
    ae_p = Path(pipeline_dir) / "03_audio_events.json"
    if ae_p.is_file():
        try:
            bpm = (json.loads(ae_p.read_text(encoding="utf-8")).get("bgm") or {}).get("bpm")
        except Exception:
            bpm = None

    return {
        "style_id_declared": pkg.get("style_id"),
        "duration_sec": dur,
        "sfx": {
            "n_cues": len(cues),
            "per_min": round(len(cues) / dur * 60.0, 2) if dur > 0 else None,
            "min_gap_sec": min(gaps) if gaps else None,
            "role_shares": _share(role_counts),
            "role_counts": role_counts,
            "trigger_shares": _share(trig_counts),
            "trigger_counts": trig_counts,
            "source_dirs": src_dirs,
        },
        "flower": {
            "n_cues": len(vis),
            "per_min": round(len(vis) / dur * 60.0, 2) if dur > 0 else None,
            "trigger_shares": _share(ftrig),
            "trigger_counts": ftrig,
            "chars_mean": round(sum(chars) / len(chars), 2) if chars else None,
            "hold_mean": round(sum(holds) / len(holds), 2) if holds else None,
            "colors": fg_colors,
        },
        "beat": {"bpm": bpm},
    }


def _status(ok: bool, warn: bool = False) -> str:
    if ok:
        return "PASS"
    return "WARN" if warn else "FAIL"


def verify(style_id: str, pipeline_dir: Path, rules_path: Optional[Path] = None) -> Dict[str, Any]:
    """逐项核对这次成片是不是这套风格。返回 {verdict, checks[], style_guess}。"""
    fp = fingerprint(style_id, rules_path)
    got = measure_pipeline(Path(pipeline_dir), style_id)
    checks: List[Dict[str, Any]] = []

    def add(name: str, status: str, measured: Any, target: Any, note: str = "") -> None:
        checks.append({"name": name, "status": status, "measured": measured,
                       "target": target, "note": note})

    # 0. 声明风格
    declared = got.get("style_id_declared")
    add("style_id_declared", _status(declared == style_id, warn=False) if declared else "WARN",
        declared, style_id, "06_packaging.json 里声明的风格")

    # 1. 音效密度
    tgt_ppm = fp["sfx"]["per_min"]
    m_ppm = got["sfx"]["per_min"] or 0.0
    lo, hi = tgt_ppm * SFX_PPM_BAND[0], tgt_ppm * SFX_PPM_BAND[1]
    add("sfx_per_min", _status(lo <= m_ppm <= hi, warn=(m_ppm <= hi and m_ppm >= lo * 0.6)),
        round(m_ppm, 1), [round(lo, 1), round(hi, 1)],
        f"源草稿 {tgt_ppm:.1f}/min；低到 35% 算更干净，高过 110% 算过密")

    # 2. 音效触发配比：未知触发一律 FAIL，占比偏差超 25pp 记偏差
    allowed = set(fp["sfx"]["allowed_triggers"]) | {"free"}
    unknown = {k: v for k, v in (got["sfx"]["trigger_shares"] or {}).items() if k not in allowed}
    add("sfx_trigger_known", _status(not unknown), sorted(unknown) or "none", sorted(allowed),
        "出现该风格不存在的触发类型 = 音效不是按这套风格的理由响的")
    max_dev = 0.0
    for t, s in (fp["sfx"]["trigger_shares"] or {}).items():
        if s < 0.10:
            continue
        m = float((got["sfx"]["trigger_shares"] or {}).get(t, 0.0))
        max_dev = max(max_dev, abs(m - s))
    add("sfx_trigger_mix", _status(max_dev <= TRIGGER_SHARE_SLACK, warn=max_dev <= TRIGGER_SHARE_SLACK * 1.6),
        round(max_dev, 3), f"≤{TRIGGER_SHARE_SLACK}", "主触发类型占比与源草稿的最大偏差")

    # 3. 角色配比（余弦）
    tgt_roles = fp["sfx"]["role_shares"]
    got_roles = got["sfx"]["role_shares"] or {}
    keys = set(tgt_roles) | set(got_roles)
    if tgt_roles and keys:
        num = sum(float(tgt_roles.get(k, 0.0)) * float(got_roles.get(k, 0.0)) for k in keys)
        dn = math.sqrt(sum(float(tgt_roles.get(k, 0.0)) ** 2 for k in keys))
        dm = math.sqrt(sum(float(got_roles.get(k, 0.0)) ** 2 for k in keys))
        cos = round(num / (dn * dm), 3) if dn > 0 and dm > 0 else 0.0
        add("sfx_role_mix", _status(cos >= ROLE_COS_MIN), cos, f"≥{ROLE_COS_MIN}",
            f"源: { {k: round(v, 2) for k, v in tgt_roles.items()} }")
    else:
        cos = None

    # 4. 最短间隔（不许连击）
    tgt_gap = max(0.30, float(fp["sfx"]["min_gap_sec"] or 0.0))
    m_gap = got["sfx"]["min_gap_sec"]
    if m_gap is not None and (got["sfx"]["n_cues"] or 0) >= 2:
        add("sfx_min_gap", _status(float(m_gap) >= tgt_gap * 0.6), m_gap, f"≥{round(tgt_gap * 0.6, 2)}",
            "同片最短的两条音效间隔；低于源草稿 60% 就有'连击'听感")
    else:
        add("sfx_min_gap", "WARN", m_gap, tgt_gap, "音效不足两条，无法量间隔")

    # 5. 音效来源：只许本风格目录
    dirs = got["sfx"]["source_dirs"] or {}
    bad_dirs = {d: n for d, n in dirs.items() if d and d != style_id}
    add("sfx_source_dir", _status(not bad_dirs), sorted(bad_dirs) or sorted(dirs), style_id,
        "跨风格音效串味")
    if dirs and style_id in dirs:
        add("sfx_source_share", _status(dirs.get(style_id, 0) >= sum(dirs.values()) * 0.99),
            round(dirs.get(style_id, 0) / sum(dirs.values()), 3), "≥0.99", "本风格真音效占比")

    # 6. 花字密度与触发
    ftgt = fp["flower"]["per_min"]
    fm = got["flower"]["per_min"] or 0.0
    flo, fhi = ftgt * FLOWER_PPM_BAND[0], ftgt * FLOWER_PPM_BAND[1]
    add("flower_per_min", _status(flo <= fm <= fhi, warn=fm <= fhi * 1.3 and fm >= flo * 0.6),
        round(fm, 1), [round(flo, 1), round(fhi, 1)], f"源草稿 {ftgt:.1f}/min")
    fallowed = set(fp["flower"]["allowed_triggers"]) | set(fp["flower"]["always_allowed_triggers"])
    funknown = {k: v for k, v in (got["flower"]["trigger_shares"] or {}).items()
                if k not in fallowed and k != "unknown"}
    add("flower_trigger_known", _status(not funknown), sorted(funknown) or "none", sorted(fallowed),
        "花字触发类型必须出自这套风格的清单")

    # 7. 花字字数
    cmin, cmax = fp["flower"]["chars"]
    cm = got["flower"]["chars_mean"]
    if cm is not None:
        add("flower_chars", _status(cm <= cmax + 3, warn=cm <= cmax + 5), cm, f"≤{cmax + 3}",
            f"源草稿 {cmin}~{cmax} 字")

    # 8. 花字配色（源草稿主色是否出现在成片里）
    tgt_colors = {str(v).upper() for v in (fp["flower"]["colors"] or {}).values() if v}
    got_colors = {str(k).upper() for k in (got["flower"]["colors"] or {}) if k}
    if tgt_colors and got_colors:
        inter = tgt_colors & got_colors
        add("flower_palette", _status(bool(inter)), sorted(inter), sorted(tgt_colors),
            "花字主色/描边色是否用回这套源草稿的配色")

    # 9. BPM
    tbpm = (fp.get("beat") or {}).get("bpm")
    mbpm = (got.get("beat") or {}).get("bpm")
    if tbpm and mbpm:
        rel = abs(float(mbpm) - float(tbpm)) / float(tbpm)
        add("bgm_bpm", _status(rel <= BPM_TOLERANCE), round(float(mbpm), 1), round(float(tbpm), 1),
            "BGM 不是这套风格的曲子会在这里露馅")

    guess, ranking = guess_style(got, rules_path)
    # 10. 视觉处理指纹（转场 / 抠像 / 变速 / 贴纸 / 特效）：
    #     音效花字对得上、不代表画面处理也对得上——style_21 与 style_15 的音效可以很像，
    #     但一个每镜扣人、一个全是贴纸。
    try:
        checks.extend(visual_checks(style_id, Path(pipeline_dir)))
    except Exception as _ve:  # 视觉指纹出问题不许把音效侧的结论吞掉
        checks.append({"name": "visual_pipeline", "status": "WARN", "measured": None,
                       "target": "视觉处理指纹", "note": f"视觉指纹未核对（{_ve}）"})
    fails = [c for c in checks if c["status"] == "FAIL"]
    warns = [c for c in checks if c["status"] == "WARN"]
    verdict = "PASS" if not fails and guess == style_id else ("FAIL" if fails else "WARN")
    if guess != style_id and guess is not None:
        verdict = "FAIL"
    return {
        "style_id": style_id,
        "verdict": verdict,
        "checks": checks,
        "n_pass": len([c for c in checks if c["status"] == "PASS"]),
        "n_warn": len(warns),
        "n_fail": len(fails),
        "style_guess": guess,
        "style_ranking": ranking[:5],
        "measured": got,
        "fingerprint": fp,
    }


_PALETTE_CACHE: Dict[str, Any] = {}


def _log_norm(v: float, ceiling: float) -> float:
    return round(math.log1p(max(0.0, float(v))) / math.log1p(ceiling), 4)


def _feature_vec(node: Dict[str, Any], kind: str) -> List[float]:
    """风格判别特征：音效密度(对数)/触发配比/角色配比/花字密度/配色重合/BPM。

    密度用对数归一，否则"策略比源草稿更干净"会把距离全压在密度这一维上，
    把 style_21 误判成同门但更稀的 style_19。
    """
    s, f = node["sfx"], node["flower"]
    bpm = float((node.get("beat") or {}).get("bpm") or 0.0)
    ts, fs, rs = s["trigger_shares"], f["trigger_shares"], s["role_shares"]
    if kind == "finger":
        tgt_colors = {str(v).upper() for v in (f.get("colors") or {}).values() if v}
        got_colors = tgt_colors
    else:
        tgt_colors = set()
        got_colors = {str(k).upper() for k in (f.get("colors") or {}) if k}
    palette = 0.0
    if kind == "measured" and got_colors:
        best = 0.0
        if _PALETTE_CACHE.get("_v") != frozenset(got_colors):
            _PALETTE_CACHE["_v"] = frozenset(got_colors)
            _PALETTE_CACHE["best"] = 0.0
            for sid in _all_style_ids():
                fc = {str(v).upper() for v in (fingerprint(sid)["flower"].get("colors") or {}).values() if v}
                if fc:
                    _PALETTE_CACHE["best"] = max(_PALETTE_CACHE["best"],
                                                 len(fc & got_colors) / float(len(fc | got_colors)))
        best = float(_PALETTE_CACHE.get("best") or 0.0)
        palette = best
    return [
        _log_norm(s.get("per_min") or 0.0, 80.0),
        float(ts.get("cut", 0.0)),
        float(ts.get("flower", 0.0)),
        float(ts.get("line", 0.0)),
        float(ts.get("beat", 0.0)),
        float(rs.get("cut", 0.0)),
        float(rs.get("accent", 0.0)),
        float(rs.get("info", 0.0)),
        float(rs.get("hook", 0.0)),
        _log_norm(f.get("per_min") or 0.0, 40.0),
        float(fs.get("number", 0.0)),
        float(fs.get("keyword", 0.0)),
        float(fs.get("chapter", 0.0)),
        float(fs.get("cut", 0.0)),
        ((bpm - 90.0) / 60.0) if bpm else 0.0,
        palette,
    ]


def guess_style(measured: Dict[str, Any], rules_path: Optional[Path] = None) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """用可测指标反推"这更像哪套风格"（套错风格时和声明值对不上）。"""
    try:
        table = style_rules("style_01", rules_path)  # 触发加载
    except Exception:
        table = {}
    ids = _all_style_ids(rules_path)
    if not ids:
        return None, []
    mv = _feature_vec(measured, "measured")
    ranking: List[Dict[str, Any]] = []
    for sid in ids:
        fv = _feature_vec(fingerprint(sid, rules_path), "finger")
        d = sum(abs(a - b) for a, b in zip(mv, fv))
        ranking.append({"style_id": sid, "distance": round(d, 3)})
    ranking.sort(key=lambda r: r["distance"])
    return (ranking[0]["style_id"] if ranking else None), ranking


def _all_style_ids(rules_path: Optional[Path] = None) -> List[str]:
    from trigger_policy_common import load_rule_table

    table = load_rule_table(rules_path)
    styles = table.get("styles") or {}
    return sorted(styles.keys())


def format_report(result: Dict[str, Any]) -> str:
    lines = [f"风格指纹核对 · {result['style_id']} → {result['verdict']}"
             f"（PASS {result['n_pass']} / WARN {result['n_warn']} / FAIL {result['n_fail']}）"]
    for c in result["checks"]:
        flag = {"PASS": "✔", "WARN": "△", "FAIL": "✘"}[c["status"]]
        lines.append(f"  {flag} {c['name']:22s} 实测 {str(c['measured'])[:34]:34s} 目标 {str(c['target'])[:34]:34s} {c['note']}")
    if result.get("style_guess"):
        lines.append(f"  最像：{result['style_guess']}  排名 "
                     f"{[ (r['style_id'], r['distance']) for r in result.get('style_ranking', [])[:3] ]}")
    return "\n".join(lines)


# ────────────────────── 视觉处理指纹（转场 / 抠像 / 变速 / 贴纸 / 特效）──────────────────
#
# 与音效/花字指纹同一个入口（`verify`），但核的是**画面上的那些手法**：
#   · 这套风格把人物层的多少比例做了抠像（真值 0 段就一段也不许抠）
#   · 变速是否只落在 B-roll（A-roll 口播永远原速）
#   · 转场是否仍然是 0（24 套源草稿全部硬切）
#   · 贴纸/特效的条数与时长是否在真值量级（有原生草稿时才量）
# 规则来源：`forensic/style_trigger_rules_visual.json`（由 dev_tools/build_trigger_rules_visual.py 反推）
try:  # 同目录导入（脚本被单跑/被测试导入都能用）
    from trigger_policy_common import REPO_ROOT as _REPO_ROOT  # type: ignore
    _VISUAL_RULES_PATH = _REPO_ROOT / "forensic" / "style_trigger_rules_visual.json"
except Exception:  # pragma: no cover
    _VISUAL_RULES_PATH = Path(__file__).resolve().parents[2] / "forensic" / "style_trigger_rules_visual.json"

CUTOUT_SHARE_SLACK = 1.15      # 比真值多抠 15% 以内可接受（内容优先原则允许更保守，不允许更激进）
VISUAL_COUNT_BAND = (0.0, 1.50)  # 条数类：不超过真值 150%（0 条不算错，可能只是没遇到机会）

_VISUAL_CACHE: Dict[str, Any] = {}


def visual_rules(style_id: str, rules_path: Optional[Path] = None) -> Dict[str, Any]:
    """取某套风格的视觉五节规则（带缓存）；找不到就是空壳（核对时全记 WARN，不乱判 PASS）。"""
    p = Path(rules_path) if rules_path else _VISUAL_RULES_PATH
    key = f"{p}|{style_id}"
    if key in _VISUAL_CACHE:
        return _VISUAL_CACHE[key]
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        doc = {}
    node = ((doc.get("styles") or {}).get(str(style_id)) or {})
    _VISUAL_CACHE[key] = node
    return node


def visual_fingerprint(style_id: str, rules_path: Optional[Path] = None) -> Dict[str, Any]:
    """这套风格**应该**长成什么样（视觉处理层面）。"""
    node = visual_rules(style_id, rules_path)
    cut = node.get("cutout") or {}
    spd = node.get("speed") or {}
    ovl = node.get("overlay") or {}
    eff = node.get("effect") or {}
    trn = node.get("transition") or {}
    cut_ev, cut_lim = cut.get("evidence") or {}, cut.get("limits") or {}
    spd_ev, spd_lim = spd.get("evidence") or {}, spd.get("limits") or {}
    ovl_ev, ovl_lim = ovl.get("evidence") or {}, ovl.get("limits") or {}
    eff_ev, eff_lim = eff.get("evidence") or {}, eff.get("limits") or {}
    trn_ev = trn.get("evidence") or {}
    return {
        "style_id": str(style_id),
        "found": bool(node),
        "cutout": {
            "enabled": bool(cut.get("enabled")),
            "truth_segments": cut_ev.get("truth_segments"),
            "share_of_person": cut_ev.get("matting_share_of_person"),
            "share_upper": cut_lim.get("max_share_of_person_layer"),
            "max_per_min": cut_lim.get("max_clips_per_min"),
            "layer": (cut.get("scope") or {}).get("layer"),
        },
        "speed": {
            "enabled": bool(spd.get("enabled")),
            "truth_segments": spd_ev.get("truth_segments"),
            "values": list(spd_lim.get("speed_values_in_truth") or []),
            "max_per_min": spd_lim.get("max_per_min"),
            "scope_roles": (spd.get("scope") or {}).get("allow") or [],
        },
        "overlay": {
            "enabled": bool(ovl.get("enabled")),
            "truth_stickers": ovl_ev.get("truth_stickers"),
            "max_per_min": ovl_lim.get("max_count_per_min"),
            "max_dur_sec": ovl_lim.get("max_dur_sec"),
            "cut_align_rate": ovl_ev.get("cut_align_rate"),
        },
        "effect": {
            "enabled": bool(eff.get("enabled")),
            "truth_effects": eff_ev.get("truth_effects"),
            "names": eff_ev.get("names") or [],
            "max_per_min": eff_lim.get("max_count_per_min"),
            "max_dur_sec": eff_lim.get("max_dur_sec"),
            "max_coverage": eff_lim.get("max_coverage_ratio"),
        },
        "transition": {
            "truth_count": trn_ev.get("truth_transition_count"),
            "default": trn.get("default"),
        },
    }


def _find_draft(pipeline_dir: Path) -> Optional[Path]:
    """找导出端产出的原生草稿（有就量贴纸/特效/转场，没有就如实记「未导出」）。"""
    p = Path(pipeline_dir)
    for cand in (p / "draft_content.json", p / "jianying_draft" / "draft_content.json"):
        if cand.is_file():
            return cand
    hits = sorted(p.glob("**/draft_content.json"))
    return hits[0] if hits else None


def measure_visual_pipeline(pipeline_dir: Path) -> Dict[str, Any]:
    """把这次成片的视觉处理实测出来（EDL + 原生草稿）。"""
    p = Path(pipeline_dir)
    edl: Dict[str, Any] = {}
    ep = p / "04_edl.json"
    if ep.is_file():
        try:
            edl = json.loads(ep.read_text(encoding="utf-8"))
        except Exception:
            edl = {}
    clips = list(edl.get("clips") or [])
    a_roll_sec = sum(float(c.get("duration_sec") or 0.0) for c in clips
                     if str(c.get("track_role")) == "a_roll")
    cut_sec, cut_n, cut_off_scope, cut_rows = 0.0, 0, [], []
    for c in clips:
        lay = c.get("pip_layout") or {}
        if not lay.get("cutout"):
            continue
        d = float(c.get("duration_sec") or 0.0)
        cut_sec += d
        cut_n += 1
        if str(c.get("track_role")) != "a_roll":
            cut_off_scope.append(c.get("clip_id"))
        cut_rows.append({"clip_id": c.get("clip_id"),
                         "timeline_in_sec": c.get("timeline_in_sec"),
                         "duration_sec": round(d, 3)})
    gate = edl.get("cutout_gate") or {}
    sp_rows, sp_vals, aroll_sp = [], [], []
    for c in clips:
        sp = float(c.get("speed") or 1.0)
        if abs(sp - 1.0) <= 1e-4:
            continue
        if str(c.get("track_role")) == "a_roll":
            aroll_sp.append(c.get("clip_id"))
        sp_rows.append({"clip_id": c.get("clip_id"), "role": c.get("track_role"),
                        "speed": round(sp, 3), "duration_sec": c.get("duration_sec")})
        sp_vals.append(round(sp, 3))
    tp = edl.get("treatment_policy") or {}
    trans = tp.get("transition") or {}
    out: Dict[str, Any] = {
        "has_edl": bool(edl),
        "style_id_declared": edl.get("style_id"),
        "duration_sec": edl.get("total_duration_sec"),
        "a_roll_sec": round(a_roll_sec, 3),
        "cutout": {"clips": cut_n, "sec": round(cut_sec, 3),
                   "share_of_person": round(cut_sec / a_roll_sec, 3) if a_roll_sec else 0.0,
                   "per_min": round(cut_n / (float(edl.get("total_duration_sec") or 0.0) / 60.0), 2)
                   if float(edl.get("total_duration_sec") or 0.0) > 0 else None,
                   "off_scope_clips": cut_off_scope, "rows": cut_rows,
                   "candidates": gate.get("candidates"), "dropped": gate.get("dropped"),
                   "enabled": bool(((tp.get("cutout") or {}).get("enabled")) if tp else None)},
        "speed": {"clips": len(sp_rows), "values": sorted(set(sp_vals)),
                  "a_roll_clips": aroll_sp, "rows": sp_rows},
        "transition": {"applied": int(trans.get("applied") or 0),
                       "dropped": int(trans.get("dropped") or 0)},
        "overlay": None, "effect": None, "draft": None,
    }
    dp = _find_draft(p)
    if dp is not None:
        try:
            dr = json.loads(dp.read_text(encoding="utf-8"))
        except Exception:
            dr = {}
        vmat = {m.get("id"): m for m in ((dr.get("materials") or {}).get("videos") or [])}
        tracks = dr.get("tracks") or []
        stk, eff, trans_n, mat_n = [], [], 0, 0
        for t in tracks:
            ttype = t.get("type")
            for s in t.get("segments") or []:
                tr = s.get("target_timerange") or {}
                d0 = float(tr.get("duration") or 0) / 1e6
                if ttype == "sticker":
                    stk.append({"start": round(float(tr.get("start") or 0) / 1e6, 3),
                                "dur": round(d0, 3)})
                elif ttype == "effect":
                    eff.append({"start": round(float(tr.get("start") or 0) / 1e6, 3),
                                "dur": round(d0, 3)})
                elif ttype == "video":
                    mat = vmat.get(s.get("material_id")) or {}
                    mb = mat.get("matting") if isinstance(mat.get("matting"), dict) else {}
                    if str((mb or {}).get("path") or "").strip():
                        mat_n += 1
        tr_mats = (dr.get("materials") or {}).get("transitions") or []
        tr_ids = {t.get("id") for t in tr_mats}
        for t in tracks:
            for s in t.get("segments") or []:
                trans_n += sum(1 for r in (s.get("extra_material_refs") or []) if r in tr_ids)
        ddur = float(dr.get("duration") or 0) / 1e6
        out["draft"] = str(dp.name)
        out["overlay"] = {
            "stickers": len(stk),
            "per_min": round(len(stk) / (ddur / 60.0), 2) if ddur > 0 else None,
            "max_dur_sec": max([s["dur"] for s in stk]) if stk else 0.0,
            "rows": stk[:40],
        }
        out["effect"] = {
            "effects": len(eff),
            "per_min": round(len(eff) / (ddur / 60.0), 2) if ddur > 0 else None,
            "max_dur_sec": max([e["dur"] for e in eff]) if eff else 0.0,
            "rows": eff[:40],
        }
        out["transition"]["draft_count"] = trans_n
        out["cutout"]["draft_matting_segments"] = mat_n
    return out


def visual_checks(style_id: str, pipeline_dir: Path,
                  rules_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """逐项核对视觉处理：返回与 verify() 同构的 check 列表（name/status/measured/target/note）。"""
    fp = visual_fingerprint(style_id, rules_path)
    got = measure_visual_pipeline(Path(pipeline_dir))
    checks: List[Dict[str, Any]] = []

    def add(name: str, status: str, measured: Any, target: Any, note: str = "") -> None:
        checks.append({"name": name, "status": status, "measured": measured,
                       "target": target, "note": note})

    if not fp.get("found"):
        add("visual_rules", "WARN", None, style_id, "规则里没有这套风格的视觉处理规则，未核")
        return checks
    if not got.get("has_edl"):
        add("visual_rules", "WARN", None, "04_edl.json",
            "这次没有 04_edl.json，抠像/变速/转场未核（只核到音效花字）")
        return checks

    # 1. 抠像：作用域 + 密度（人物层里被抠的比例）
    cu, cfp = got["cutout"], fp["cutout"]
    if cu.get("off_scope_clips"):
        add("cutout_scope", "FAIL", cu["off_scope_clips"], "只许挂在 a_roll",
            "抠像挂在非口播层：那一层没有可抠的主体")
    else:
        add("cutout_scope", "PASS", 0, "只许挂在 a_roll", "抠像全部落在人物层")
    upper = float(cfp.get("share_upper") or 0.0)
    share = float(cu.get("share_of_person") or 0.0)
    if not cfp.get("enabled"):
        add("cutout_share", "PASS" if cu.get("clips", 0) == 0 else "FAIL",
            share, 0.0, "这套风格真值 0 段抠像，成片也不许有")
    else:
        if share > upper + 1e-6:
            add("cutout_share", "FAIL", share, f"≤{upper:.2f}",
                f"源草稿把人物层的 {float(cfp.get('share_of_person') or 0):.0%} 做了抠像，这版超了")
        elif share <= 0.0:
            add("cutout_share", "WARN", share, f"≤{upper:.2f}",
                "这套风格会用抠像，但这次一处没用（可能素材里没有可抠的人）")
        else:
            add("cutout_share", "PASS", share, f"≤{upper:.2f}",
                f"源草稿 {float(cfp.get('share_of_person') or 0):.0%}；内容优先原则允许更保守")

    # 2. 变速：A-roll 零容忍；倍速必须出自真值
    sp, sfp = got["speed"], fp["speed"]
    add("speed_aroll_lock", "PASS" if not sp.get("a_roll_clips") else "FAIL",
        sp.get("a_roll_clips") or 0, 0, "A-roll 口播变速 = 口型与字音拆开")
    vals = [float(v) for v in (sp.get("values") or [])]
    truth_vals = [float(v) for v in (sfp.get("values") or [])]
    if vals:
        bad = [v for v in vals if not any(abs(v - t) <= 0.15 for t in truth_vals)]
        add("speed_values", "PASS" if not bad else "FAIL", sorted(set(vals)),
            truth_vals or 1.0, "倍速必须出自该风格真值（不自己发明倍速）")
    elif sfp.get("enabled"):
        add("speed_usage", "WARN", 0, f"源草稿 {sfp.get('truth_segments')} 段",
            "这套风格会用变速，但这次一段没变（可能没有可压缩的等待段）")
    else:
        add("speed_usage", "PASS", 0, 0, "这套风格真值 0 段变速，原速正确")

    # 3. 转场：真值 0 → 成片必须 0
    tr = got["transition"]
    tcount = int(tr.get("applied") or 0) + int(tr.get("draft_count") or 0)
    add("transition_hard_cut", "PASS" if tcount == 0 else "FAIL", tcount, 0,
        "这套风格源草稿转场 = 0，默认硬切")

    # 4. 贴纸 / 画面特效：有原生草稿才量，没有就如实说「未核」
    ov, ofp = got.get("overlay"), fp["overlay"]
    if ov is None:
        add("overlay_stickers", "WARN", None, "draft_content.json", "没导出原生草稿，贴纸未核")
    else:
        cap = float(ofp.get("max_per_min") or 0.0) if ofp.get("enabled") else 0.0
        pm = float(ov.get("per_min") or 0.0)
        if not ofp.get("enabled") and ov.get("stickers"):
            add("overlay_stickers", "FAIL", pm, 0, "这套风格真值没有贴纸")
        elif ofp.get("enabled") and cap > 0 and pm > cap * VISUAL_COUNT_BAND[1]:
            add("overlay_stickers", "FAIL", pm, f"≤{cap * VISUAL_COUNT_BAND[1]:.1f}/min", "贴纸过密")
        else:
            add("overlay_stickers", "PASS", pm, f"≤{cap:.1f}/min" if cap else 0,
                "贴纸条数在真值量级")
    ef, efp = got.get("effect"), fp["effect"]
    if ef is None:
        add("effect_usage", "WARN", None, "draft_content.json", "没导出原生草稿，画面特效未核")
    else:
        cap = float(efp.get("max_per_min") or 0.0) if efp.get("enabled") else 0.0
        pm = float(ef.get("per_min") or 0.0)
        if not efp.get("enabled") and ef.get("effects"):
            add("effect_usage", "FAIL", ef.get("effects"), 0,
                "这套风格真值 0 个画面特效")
        elif efp.get("enabled") and cap > 0 and pm > cap * VISUAL_COUNT_BAND[1]:
            add("effect_usage", "FAIL", pm, f"≤{cap * VISUAL_COUNT_BAND[1]:.1f}/min", "特效过密")
        else:
            add("effect_usage", "PASS", pm, f"≤{cap:.1f}/min" if cap else 0,
                "画面特效条数在真值量级")
    return checks


def format_visual_report(style_id: str, pipeline_dir: Path) -> str:
    """只打视觉处理这一段（排查画面手法用）。"""
    checks = visual_checks(style_id, Path(pipeline_dir))
    if not checks:
        return f"视觉处理指纹 · {style_id}：无数据"
    lines = [f"视觉处理指纹 · {style_id}"]
    for c in checks:
        flag = {"PASS": "✔", "WARN": "△", "FAIL": "✘"}[c["status"]]
        lines.append(f"  {flag} {c['name']:22s} 实测 {str(c['measured'])[:40]:40s} "
                     f"目标 {str(c['target'])[:26]:26s} {c['note']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="风格指纹：fingerprint / verify")
    ap.add_argument("--style", required=True, help="style_id，如 style_21")
    ap.add_argument("--pipeline-dir", help="给了就做 verify，否则只打印指纹")
    ap.add_argument("--visual", action="store_true", help="只打印视觉处理那一段（抠像/变速/转场/贴纸/特效）")
    args = ap.parse_args()
    if args.visual and args.pipeline_dir:
        print(format_visual_report(args.style, Path(args.pipeline_dir)))
    elif args.pipeline_dir:
        print(format_report(verify(args.style, Path(args.pipeline_dir))))
    elif args.visual:
        print(json.dumps(visual_fingerprint(args.style), ensure_ascii=False, indent=1))
    else:
        fp = fingerprint(args.style)
        print(json.dumps(fp, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
