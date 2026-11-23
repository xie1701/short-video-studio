#!/usr/bin/env python3
"""24 套风格 × 固定 fixture：`--grammar style` 到底有没有把刀法拉近该套自己的真值。

验收逻辑（不许调阈值刷绿）
─────────────────────────
  1. 用 `dev_tools/render_style_fixtures.py` 的同一套 fixture 逻辑（**事件密度版**：72s 口播、
     句长 1.2–3.5s 变化、句内停顿 + 关键词重音，共 150+ 个音频事件），24 套逐套跑
     `run_step4(..., grammar="style")`，拿产出的 04_edl.json 调 `edit_grammar.check_edl`。
  2. 同一套 fixture 再跑一遍 `grammar="dynamic"` 作对照（旧行为）。
  3. 逐套比「谁更接近该套自己的真值」：先比通过项数，再比归一化误差均值。
  4. 出 `reports/edit_grammar_<date>.csv` + `.md`，末尾给 PASS/FAIL、逐项失败频次表，
     以及「有没有哪套 style 反而更差」。

    python dev_tools/verify_edit_grammar.py [--keep] [--date 2026-11-24] [--aroll-sec 72]
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "short-video-studio" / "scripts"
DEV = ROOT / "dev_tools"
for p in (SCRIPTS, DEV):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from render_style_fixtures import AROLL_SEC, build_media, build_pipeline, fixture_spec  # noqa: E402
from step4_build_edl import run_step4  # noqa: E402
from edit_grammar import check_edl, load_grammar  # noqa: E402

REPORT_DIR = ROOT / "reports"


def _rel_err(actual, target) -> float | None:
    if actual is None or target in (None, 0, 0.0):
        return None
    return abs(float(actual) - float(target)) / max(abs(float(target)), 1e-6)


def _distance(check: dict) -> float | None:
    """单项归一化距离：镜头数/时长用相对误差，比例类用绝对差。"""
    name = check["name"]
    act, tgt, tol = check["actual"], check["target"], check["tol"]
    if act is None or tgt is None:
        return None
    if name in ("快切占比(<1s)",):
        return abs(float(act) - float(tgt))
    if name == "B 覆盖占比":
        lo, hi = [float(x) for x in str(tgt).split("–")]
        if lo <= float(act) <= hi:
            return 0.0
        return min(abs(float(act) - lo), abs(float(act) - hi))
    if name == "开场 3s 切数":
        return abs(float(act) - float(tgt))
    return _rel_err(act, tgt) or 0.0


def run_one(pdir: pathlib.Path, sid: str, track: str, mode: str) -> tuple:
    edl = run_step4(pdir, track=track, style_id=sid, edit_profile="documentary", grammar=mode)
    g = load_grammar(sid)
    gate = check_edl(edl, g)
    return edl, gate, g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--date", default="2026-11-24")
    ap.add_argument("--aroll-sec", type=float, default=AROLL_SEC, help="口播总时长（默认 72s）")
    args = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("需要 ffmpeg")
        return 2

    lib = json.loads((ROOT / "short-video-studio/assets/style_spectrum/style_spectrum_library.json")
                     .read_text(encoding="utf-8"))
    styles = lib["styles"]

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="grammar_fixture_"))
    ar, brolls = build_media(ffmpeg, tmp, args.aroll_sec)
    spec = fixture_spec(args.aroll_sec)
    n_sent = len(spec["sentences"])

    rows, cmp_rows = [], []
    for st in styles:
        sid = st["style_id"]
        track = st.get("recommended_track") or "store"
        per_mode = {}
        for mode in ("style", "dynamic"):
            pdir = tmp / f"{sid}_{mode}"
            build_pipeline(pdir, ar, brolls, sid, total_sec=args.aroll_sec)
            try:
                edl, gate, g = run_one(pdir, sid, track, mode)
            except Exception as exc:  # 任何一套跑挂都算失败，不吞
                per_mode[mode] = {"error": f"{type(exc).__name__}: {exc}"}
                rows.append({
                    "style": sid, "mode": mode, "result": "FAIL", "fail_reason": f"流水线异常 {exc}",
                })
                continue
            per_mode[mode] = {"edl": edl, "gate": gate, "grammar": g}
            m, t = gate["metrics"], gate["targets"]
            rows.append({
                "style": sid,
                "source_ref": g.get("source_ref") or "",
                "mode": mode,
                "target_shots": t.get("shots"),
                "shots": m.get("n_shots"),
                "target_p50": t.get("p50"),
                "p50": m.get("p50"),
                "target_p10": t.get("p10"),
                "p10": m.get("p10"),
                "target_p90": t.get("p90"),
                "p90": m.get("p90"),
                "target_fast": t.get("fast_ratio"),
                "fast": m.get("fast_ratio"),
                "target_b_band": "/".join(str(x) for x in (t.get("b_band") or [])),
                "b": m.get("b_ratio"),
                "target_opening": t.get("opening_cuts"),
                "opening": m.get("opening_cuts"),
                "target_longest_cap": t.get("longest_cap"),
                "longest": m.get("longest_sec"),
                "budget_capped": bool((edl.get("edit_grammar") or {}).get("shot_budget_capped")),
                "result": "PASS" if gate["pass"] else "FAIL",
                "fail_reason": "；".join(gate["fail_reasons"]),
            })

        a, b = per_mode.get("style"), per_mode.get("dynamic")
        if "error" in a or "error" in b:
            cmp_rows.append({"style": sid, "closer": "n/a", "note": "有一套跑挂，无法对照"})
            continue
        ga, gb = a["gate"], b["gate"]
        passed_a = sum(1 for c in ga["checks"] if c["pass"])
        passed_b = sum(1 for c in gb["checks"] if c["pass"])
        da = [d for d in (_distance(c) for c in ga["checks"]) if d is not None]
        db = [d for d in (_distance(c) for c in gb["checks"]) if d is not None]
        mean_a = sum(da) / max(1, len(da))
        mean_b = sum(db) / max(1, len(db))
        closer = passed_a > passed_b or (passed_a == passed_b and mean_a < mean_b - 1e-9)
        cmp_rows.append({
            "style": sid,
            "source_ref": a["grammar"].get("source_ref") or "",
            "checks_pass_style": f"{passed_a}/{len(ga['checks'])}",
            "checks_pass_dynamic": f"{passed_b}/{len(gb['checks'])}",
            "mean_dist_style": round(mean_a, 4),
            "mean_dist_dynamic": round(mean_b, 4),
            "closer": "style" if closer else ("tie" if abs(mean_a - mean_b) < 1e-9 and passed_a == passed_b else "dynamic"),
            "fail_style": "；".join(ga["fail_reasons"]),
        })

    # ── 落盘 CSV ──
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    csv_p = REPORT_DIR / f"edit_grammar_{args.date}.csv"
    fields = ["style", "source_ref", "mode", "target_shots", "shots", "target_p50", "p50",
              "target_p10", "p10", "target_p90", "p90", "target_fast", "fast",
              "target_b_band", "b", "target_opening", "opening", "target_longest_cap", "longest",
              "budget_capped", "result", "fail_reason"]
    with csv_p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # ── fixture 的「事件预算」：把「够不够」写成数据，而不是一句含糊的解释 ──
    evs = spec["events"]
    n_events = len(evs)
    n_body = sum(1 for e in evs if 2.85 < float(e["time_sec"]) < args.aroll_sec - 0.8)
    n_open = sum(1 for e in evs if 0.50 <= float(e["time_sec"]) <= 2.90)
    capped = [r["style"] for r in rows
              if r.get("mode") == "style" and r.get("budget_capped") is True]
    max_reachable = 1 + n_body
    tgt_shots = [int(r["target_shots"]) for r in rows if r.get("mode") == "style" and r.get("target_shots")]
    over_budget = sorted(s for s in tgt_shots if s > max_reachable)

    # ── 落盘 MD ──
    n_style = sum(1 for r in cmp_rows if r.get("closer") == "style")
    n_dyn = sum(1 for r in cmp_rows if r.get("closer") == "dynamic")
    n_tie = sum(1 for r in cmp_rows if r.get("closer") == "tie")
    n_na = sum(1 for r in cmp_rows if r.get("closer") == "n/a")
    md = [
        f"# 刀法接入验收（{args.date} · 事件密度版）",
        "",
        f"- fixture：{args.aroll_sec:.0f}s 合成口播（{n_sent} 句，句长 1.2–3.5s 变化）+ "
        f"{len(brolls)} 段 15s 合成插入（与 dev_tools/render_style_fixtures.py 同一份）",
        f"- 事件密度：{n_events} 个音频事件（句界 {sum(1 for e in evs if e['type'] == 'sentence_start')}、"
        f"句内停顿 {sum(1 for e in evs if e['type'].startswith('pause'))}、"
        f"关键词/重音 {sum(1 for e in evs if e['type'] in ('keyword_accent', 'stress'))}）",
        "- 每套跑两遍：`--grammar style`（读 forensic/style_edit_grammar.json）与 `--grammar dynamic`（旧行为）",
        "- 门禁阈值在 `short-video-studio/scripts/edit_grammar.py` 里一次定义（TOL_*），本报告不放宽任何阈值",
        "",
        f"**style 更接近自身真值：{n_style}/{len(cmp_rows)} 套；dynamic 更近：{n_dyn}；打平：{n_tie}；无法对照：{n_na}**",
        "",
        "## fixture 的事件预算",
        "",
        f"- 音频事件共 {n_events} 个：正文可用（2.85s–片尾）{n_body} 个，开场窗口（0.50–2.90s）{n_open} 个",
        f"- 因此最多可排约 {max_reachable} 个镜头、开场最多 {n_open} 刀；"
        f"目标镜数超过这个上限的套：{len(over_budget)}"
        + (f"（{', '.join('style_%02d' % s for s in over_budget)}）" if over_budget else "（无）"),
        f"- 镜头数仍被封顶（EDL 里 `shot_budget_capped`）的套数：{len(capped)}/{len(cmp_rows)}"
        + (f"（{', '.join(capped)}）" if capped else ""),
        "",
        "## 逐套目标 vs 实际",
        "",
        "| 风格 | 模式 | 镜头数 目标/实际 | p50 目标/实际 | p10 目标/实际 | p90 目标/实际 "
        "| 快切 目标/实际 | B 带/实际 | 开场 目标/实际 | 最长镜上限/实际 | 结果 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for r in rows:
        if "shots" not in r:
            md.append(f"| {r['style']} | {r['mode']} | - | - | - | - | - | - | - | - | FAIL（{r['fail_reason']}） |")
            continue
        md.append(
            f"| {r['style']} | {r['mode']} | {r['target_shots']}/{r['shots']} | "
            f"{r['target_p50']}/{r['p50']} | {r['target_p10']}/{r['p10']} | {r['target_p90']}/{r['p90']} | "
            f"{r['target_fast']}/{r['fast']} | {r['target_b_band']}/{r['b']} | "
            f"{r['target_opening']}/{r['opening']} | {r['target_longest_cap']}/{r['longest']} | "
            f"{r['result']}{'（预算封顶）' if r.get('budget_capped') else ''} |"
        )
    md += ["", "## 谁更接近真值（先比通过项数，再比归一化距离均值）", "",
           "| 风格 | style 通过 | dynamic 通过 | style 距离 | dynamic 距离 | 更接近 | style 未过项 |",
           "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"]
    for r in cmp_rows:
        if r.get("closer") == "n/a":
            md.append(f"| {r['style']} | - | - | - | - | n/a | {r.get('note','')} |")
            continue
        md.append(f"| {r['style']} | {r['checks_pass_style']} | {r['checks_pass_dynamic']} | "
                  f"{r['mean_dist_style']} | {r['mean_dist_dynamic']} | {r['closer']} | {r['fail_style']} |")
    # ── 逐项失败频次（style vs dynamic 对照）+ 有没有更差的套 ──
    def _fail_counts(mode: str) -> dict:
        cnt: dict = {}
        for r in rows:
            if r.get("mode") != mode or "fail_reason" not in r:
                continue
            for part in str(r["fail_reason"]).split("；"):
                name = part.split(":")[0].strip()
                if name:
                    cnt[name] = cnt.get(name, 0) + 1
        return cnt
    fc_style, fc_dyn = _fail_counts("style"), _fail_counts("dynamic")
    worse = [r["style"] for r in cmp_rows if r.get("closer") == "dynamic"]
    md += ["", "## 逐项未过频次（style vs dynamic，共 24 套）", "",
           "| 检查项 | style 未过 | dynamic 未过 |", "| :--- | :--- | :--- |"]
    for name in sorted(set(fc_style) | set(fc_dyn), key=lambda k: -(fc_style.get(k, 0) - fc_dyn.get(k, 0))):
        md.append(f"| {name} | {fc_style.get(name, 0)}/24 | {fc_dyn.get(name, 0)}/24 |")
    md += ["", "## style 反而更差的套", "",
           ("- " + "、".join(worse)) if worse else "- 无（没有任何一套在 style 模式下比 dynamic 更远离自身真值）", ""]
    md_p = REPORT_DIR / f"edit_grammar_{args.date}.md"
    md_p.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("逐项未过频次 style/dynamic：" + "，".join(
        f"{k} {fc_style.get(k, 0)}/{fc_dyn.get(k, 0)}" for k in sorted(set(fc_style) | set(fc_dyn))))
    print("style 反而更差：" + ("、".join(worse) if worse else "无"))

    print(f"style 更接近真值 {n_style}/{len(cmp_rows)} 套（dynamic {n_dyn} / 打平 {n_tie} / 无法对照 {n_na}）")
    print(f"-> {csv_p.relative_to(ROOT)}")
    print(f"-> {md_p.relative_to(ROOT)}")
    if args.keep:
        print(f"fixtures kept at {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
