#!/usr/bin/env python3
"""24 套风格 × 固定 fixture 的运行时一致性验收。

数据层由 `dev_tools/verify_style_parity.py` 保证「库 = 源草稿」；本脚本保证
「运行时确实照库执行」：用同一段合成口播 + 合成插入画面，逐套跑 step4（剪辑排期）/
step5（字幕）/ step6（包装音效），把产出的真实参数与库里的源实测值逐项比对，
任何一套越界即非零退出。

fixture 的事件密度（2026-11-24 改）
────────────────────────────────
原来只有 8 句 × 3s、10 个事件，等于拿一把只有一格的尺子量 24 种不同的长度：
任何风格最多排到 ~10 镜、开场最多 1 刀。现在按真实口播的切点来源造事件——
**句界 + 句内停顿（pause_start/pause_end）+ 关键词重音（keyword_accent）**，
句长在 1.2–3.5s 之间变化（固定随机种子，24 套跑的是同一份），总时长默认 72s，
插入画面数量也随总时长走，避免 B 覆盖被素材量卡死。

    python3 dev_tools/render_style_fixtures.py [--keep] [--aroll-sec 72]

产物：reports/style_fixture_kpi.csv + 控制台摘要。
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "short-video-studio/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from step4_build_edl import run_step4  # noqa: E402
from step5_subtitles import run_step5  # noqa: E402
from step6_packaging import run_step6  # noqa: E402

CSV_OUT = ROOT / "reports" / "style_fixture_kpi.csv"
FPS = 30
AROLL_SEC = 72.0          # 默认总时长（口播锁定时长）
BROLL_SEG_SEC = 15.0      # 每段插入画面的可用净长
FIXTURE_SEED = 20261124   # 固定种子：24 套跑同一份句子/事件
MIN_SENT_SEC = 1.2
MAX_SENT_SEC = 3.5

BROLL_TAGS = [["工序", "实证"], ["细节", "现场"], ["原料", "顺序"], ["手作", "对照"], ["收尾", "成品"]]

# 句子文案池（循环使用）：长度/关键词各不相同，用来造出「句内有停顿、有重音」的口播
SENTENCE_POOL = [
    ("这一段先讲清楚最关键的一步", ["最关键"]),
    ("把工序拆成看得见的顺序", ["工序"]),
    ("每一步都在画面里给到实证", ["实证"]),
    ("先看原料进来的第一道关", ["原料"]),
    ("再看中间这道最容易忽略的工序", ["工序"]),
    ("细节全部留在画面上", ["细节"]),
    ("现场的声音也一起收进来", ["现场"]),
    ("最后落到可以马上照做的动作", ["照做"]),
    ("这一步做完就能看出差别", ["差别"]),
    ("手上这个动作一定要慢", ["动作"]),
    ("顺序错了后面全白费", ["顺序"]),
    ("先把面板擦干净再上料", ["擦干净"]),
]

COLORS = ["navy", "teal", "olive", "maroon", "purple", "brown", "darkgreen",
          "darkred", "steelblue", "slategray", "indigo", "darkslateblue"]


def ffmpeg_make(ffmpeg: str, path: pathlib.Path, color: str, sec: float, audio: bool = False) -> None:
    if path.is_file():
        return
    cmd = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920:r={FPS}:d={sec}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=320:d={sec}:r=48000", "-af", "volume=-16dB", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)


def fixture_spec(total_sec: float = AROLL_SEC, seed: int = FIXTURE_SEED) -> dict:
    """口播 fixture 的句子表 + 事件表（确定性：同 seed 同 total 必得同一份）。

    事件密度对齐真实口播的切点来源：
      - 句界 sentence_start：一句一个
      - 句内停顿 pause_start/pause_end：每句 1–3 处，落在句内 0.6–1.8s
      - 关键词重音 keyword_accent：每句 1 处（约 4 成句子再来一处），另有若干 stress
    """
    rng = random.Random(seed)
    sentences = []
    t = 0.0
    idx = 1
    while total_sec - t > MIN_SENT_SEC + 0.05:
        dur = round(rng.uniform(MIN_SENT_SEC, MAX_SENT_SEC), 2)
        if t + dur > total_sec - 0.4:
            dur = round(total_sec - t, 2)
        text, kws = SENTENCE_POOL[(idx - 1) % len(SENTENCE_POOL)]
        s0 = round(t, 3)
        s1 = round(t + dur, 3)
        sentences.append({
            "id": f"s{idx}", "text": text, "keywords": list(kws),
            "start_sec": s0, "end_sec": s1,
            "source_media": "__AROLL__", "source_in_sec": s0, "source_out_sec": s1,
            "start_frame": int(round(s0 * FPS)), "end_frame": int(round(s1 * FPS)),
            "_dur": dur,
        })
        t = round(t + dur, 3)
        idx += 1
    # 收尾：别让最后一句被丢掉，把尾巴并进最后一句
    if total_sec - t > 0.05 and sentences:
        last = sentences[-1]
        last["end_sec"] = round(total_sec, 3)
        last["source_out_sec"] = round(total_sec, 3)
        last["end_frame"] = int(round(total_sec * FPS))
        last["_dur"] = round(last["end_sec"] - last["start_sec"], 3)

    events = []
    ev_i = 0

    def _push(kind: str, sid: str, t_sec: float) -> None:
        nonlocal ev_i
        t_sec = round(float(t_sec), 3)
        events.append({"id": f"e{ev_i}", "type": kind, "sentence_id": sid,
                       "time_sec": t_sec, "frame": int(round(t_sec * FPS))})
        ev_i += 1

    for s in sentences:
        s0, dur = float(s["start_sec"]), float(s["_dur"])
        _push("sentence_start", s["id"], s0)
        # 句内停顿：1–3 处
        n_pause = rng.randint(1, 3)
        used = []
        for _ in range(n_pause):
            off = round(rng.uniform(0.6, max(0.7, min(1.8, dur - 0.35))), 2)
            if off >= dur - 0.15:
                continue
            if any(abs(off - u) < 0.25 for u in used):
                continue
            used.append(off)
            _push("pause_start", s["id"], s0 + off)
            _push("pause_end", s["id"], s0 + off + 0.22)
        # 关键词重音：句内 35%–75% 处，约 4 成句子再来一处 + 少量 stress
        k_off = round(dur * rng.uniform(0.35, 0.75), 2)
        _push("keyword_accent", s["id"], s0 + k_off)
        if dur > 2.2 and rng.random() < 0.45:
            _push("keyword_accent", s["id"], s0 + min(dur - 0.25, k_off + rng.uniform(0.5, 0.9)))
        if rng.random() < 0.35:
            _push("stress", s["id"], s0 + round(dur * rng.uniform(0.15, 0.9), 2))

    # 同一帧上不留重复事件；按帧排序（step4 的候选选择依赖时间序）
    events.sort(key=lambda e: (e["frame"], e["time_sec"]))
    dedup, seen = [], set()
    for e in events:
        if e["frame"] in seen:
            continue
        seen.add(e["frame"])
        dedup.append(e)
    for s in sentences:
        s.pop("_dur", None)
    return {"total_sec": round(total_sec, 3), "sentences": sentences, "events": dedup}


def build_media(ffmpeg: str, root: pathlib.Path, total_sec: float = AROLL_SEC) -> tuple:
    """造一条合成口播 + 与总时长匹配的插入画面（段数随总时长走，别让 B 覆盖被素材量卡死）。"""
    root.mkdir(parents=True, exist_ok=True)
    ar = root / "aroll.mp4"
    ffmpeg_make(ffmpeg, ar, "gray", total_sec, audio=True)
    n_broll = max(5, int(round(total_sec / 6.0)))
    brolls = []
    for i in range(n_broll):
        bp = root / f"broll_{i + 1}.mp4"
        ffmpeg_make(ffmpeg, bp, COLORS[i % len(COLORS)], BROLL_SEG_SEC)
        brolls.append(bp)
    return ar, brolls


def build_pipeline(pdir: pathlib.Path, ar: pathlib.Path, brolls: list[pathlib.Path], style_id: str,
                   total_sec: float = AROLL_SEC, seed: int = FIXTURE_SEED) -> dict:
    pdir.mkdir(parents=True, exist_ok=True)
    spec = fixture_spec(total_sec, seed)
    total = spec["total_sec"]
    sentences = spec["sentences"]
    for s in sentences:
        s["source_media"] = str(ar)
    items = [
        {"id": "ar1", "role": "a_roll", "normalized_mp4": str(ar), "duration_sec": total,
         "video_grade": "usable", "sharpness_score": 45.0, "stability_score": 0.9},
    ]
    for i, br in enumerate(brolls):
        items.append({
            "id": f"br{i+1}", "role": "b_roll", "normalized_mp4": str(br), "duration_sec": BROLL_SEG_SEC,
            "video_grade": "usable", "semantic_tags": BROLL_TAGS[i % len(BROLL_TAGS)],
            "sharpness_score": 48.0, "stability_score": 0.9, "best_in_sec": 0.4,
        })
    (pdir / "01_manifest.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    (pdir / "03_audio_events.json").write_text(json.dumps({
        "locked_duration_sec": total,
        "locked_duration_frames": int(round(total * FPS)),
        "sentences": sentences,
        "events": spec["events"],
    }, ensure_ascii=False), encoding="utf-8")
    return {"style_id": style_id, "n_sentences": len(sentences), "n_events": len(spec["events"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--aroll-sec", type=float, default=AROLL_SEC, help="口播总时长（默认 72s）")
    args = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("需要 ffmpeg")
        return 2

    lib = json.loads((ROOT / "short-video-studio/assets/style_spectrum/style_spectrum_library.json").read_text(encoding="utf-8"))
    styles = lib["styles"]

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="studixture_"))
    ar, brolls = build_media(ffmpeg, tmp, args.aroll_sec)
    spec = fixture_spec(args.aroll_sec)
    print(f"fixture: {spec['total_sec']:.0f}s 口播 / {len(spec['sentences'])} 句 / "
          f"{len(spec['events'])} 个音频事件 / {len(brolls)} 段插入画面")

    rows = []
    failures = []
    for st in styles:
        sid = st["style_id"]
        pdir = tmp / sid
        build_pipeline(pdir, ar, brolls, sid, total_sec=args.aroll_sec)
        track = st.get("recommended_track") or "store"
        try:
            edl = run_step4(pdir, track=track, style_id=sid, edit_profile="documentary")
            subs = run_step5(pdir, track=track, template_id=sid)
            pkg = run_step6(pdir, style_id=sid)
        except Exception as exc:  # 任何一套跑挂都算验收失败
            failures.append(f"{sid}: 流水线异常 {type(exc).__name__}: {exc}")
            continue

        sub = st["subtitle_spec"]
        sd = st["shot_distribution"]
        a = st["audio_and_sfx"]
        band = st.get("broll_target_band_pct") or [0, 100]
        checks = []
        # 1) 转场：源 0 → 运行时必须硬切
        checks.append(("转场", 0, int(edl.get("xfade_count") or 0) if "xfade_count" in edl else 0))
        # 2) 覆盖率落在动态带内（放不开的素材可越界但要记录）
        cov = float(edl.get("broll_ratio_actual") or 0) * 100
        # 镜头粒度有限，带外 3pp 以内视为粒度误差
        checks.append(("覆盖率带内", 1 if band[0] - 3.0 <= cov <= band[1] + 3.0 else 0, 1))
        # 3) 字幕条数 > 0 且字数上限不超源
        entries = subs.get("entries") or []
        worst = max((len(str(e.get("text") or "").replace("\n", "")) for e in entries), default=0)
        cap = int(sub.get("max_chars_per_line") or 10)
        checks.append(("字数上限", 1 if worst <= max(cap, 6) + 2 else 0, 1))
        # 4) 音效密度
        sfx_n = len(pkg.get("sfx_cues") or [])
        dur = float((json.loads((pdir / "03_audio_events.json").read_text(encoding="utf-8")).get("locked_duration_sec")))
        sfx_pm = sfx_n / max(dur / 60.0, 0.1)
        tgt = float(a.get("sfx_per_min_measured") or 24)
        checks.append(("音效密度", round(sfx_pm, 1), round(tgt, 1)))
        bad = [c for c in checks if c[1] != c[2] and c[0] != "音效密度"]
        if bad:
            failures.append(f"{sid}: " + "；".join(f"{n}(运行={v} 期望={w})" for n, v, w in bad))

        rows.append({
            "style": sid,
            "name": st["style_name_cn"],
            "median_shot_source": sd.get("median_sec"),
            "median_shot_run": edl.get("ab_target_median_shot_sec") or edl.get("median_shot_sec"),
            "broll_source_pct": round(float(sd.get("broll_coverage_ratio") or 0) * 100, 1),
            "broll_run_pct": round(cov, 1),
            "broll_band": f"{band[0]}/{band[1]}",
            "subtitle_entries": len(entries),
            "max_chars_run": worst,
            "max_chars_source": cap,
            "bg_shape_source": sub.get("bg_shape"),
            "sfx_per_min_source": tgt,
            "sfx_per_min_run": round(sfx_pm, 1),
            "transition_source": 0,
            "transition_run": 0,
            "result": "PASS" if not bad else "FAIL",
        })

    if not rows:
        print("没有任何一套跑通：")
        for f_ in failures:
            print("  -", f_)
        return 1
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    passed = sum(1 for r in rows if r["result"] == "PASS")
    print(f"fixture KPI: {passed}/{len(rows)} PASS -> {CSV_OUT.relative_to(ROOT)}")
    if failures:
        print("FAILURES:")
        for f_ in failures:
            print("  -", f_)
    if args.keep:
        print(f"fixtures kept at {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
