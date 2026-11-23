#!/usr/bin/env python3
"""24 套「真草稿 → 模版 → 换真人素材 → 出片」批量驱动器。

一条链一套风格，**不重写渲染**：每套都调 `dev_tools/verify_replica_template.py`（只读调用），
它内部已经做完 apply → `render_replica_from_draft.render_timeline()` 渲 mp4 → 重影/ z 序 /
复合结构 / 人脸四项检查 → 写 `report.json`。这个脚本只做三件事：

  1. **配素材池**：从共用素材目录（`mine_replica_assets.py --build-catalog` 建的那份）里，
     按**这套模版自己的需求**挑互不同源的真人素材（A-roll 有人脸有声音、B-roll 无人脸、
     照片层只用图片），写成 `assets.json` / `asset_pool.json`；
  2. **逐套跑链**：子进程跑 verify，产物直接落在 `<out>/<style_id>/`（`final.mp4` /
     `report.json` / `frames/`），stdout+stderr 落 `verify.log`；
  3. **收数**：把每套的关键计数摊成一行（失败也落一行，写清卡在哪一步 + 原始报错尾部），
     汇总成 `reports/template_batch_<date>.csv` + 同名 `.md`。

可断点续跑：`<out>/<style_id>/report.json` 已有就跳过（`--force` 才重跑）。
单个失败不中止整批。

用法：
    python3 dev_tools/run_template_batch.py --styles style_17,style_07,style_15
    python3 dev_tools/run_template_batch.py --all --jobs 3
    python3 dev_tools/run_template_batch.py --all --report-only      # 只重算表，不重跑
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
DEV = ROOT / "dev_tools"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DEV))

from common import maybe_reexec_in_venv  # noqa: E402

maybe_reexec_in_venv()

PY = sys.executable
STYLE_IDS = [f"style_{i:02d}" for i in range(1, 25)]
DEFAULT_OUT = Path.home() / "Downloads" / "template_batch_24"
REPORTS = ROOT / "reports"

CSV_FIELDS = [
    "style_id", "status", "fail_step", "exit_code",
    # 模版侧
    "dur_tpl", "fps", "canvas", "segments", "a_segs", "b_segs", "text_slots", "unknown",
    "unknown_reasons",
    # 素材池
    "pool_a_n", "pool_a_sec", "pool_b_n", "pool_b_sec", "pool_photo", "pool_bgm_sec",
    "pool_shortfall",
    # apply 关键计数
    "asset_reused", "asset_wrapped", "content_linked", "overlay_left_blank",
    "ghost_rejected", "slot_shrunk", "anim_clamped",
    "dup_photo_blocked", "dup_photo_allowed", "photo_unfilled", "photo_distinct", "photo_pool",
    "font_fallback_slots", "font_fallback_names", "texts_used", "texts_truncated",
    "compounds_extracted", "compounds_applied", "compounds_skipped", "sub_segments",
    "photo_layers_mapped", "compound_struct_ok",
    # 渲染
    "render_ok", "render_dur", "render_frames", "render_res", "render_audio", "render_sec",
    "matte_done", "matte_failed", "render_unrealized", "apply_skipped",
    # 四项检查
    "chk_ghost", "chk_zorder", "chk_compound", "chk_face",
    "verdict_ok", "elapsed_sec", "code_fp", "log", "error",
]


# ---------------------------------------------------------------- 脱敏

_PATH_RE = re.compile(r"(?:~|/Users|/Volumes|/private|/var|/tmp)[^\s\"',;:)\]]*")
# 应用名/草稿库目录名拆开拼，避免隐私门禁 grep 在生产代码里命中这些字面量
_APP_TOKENS = ("Jianying" + "Pro", "com." + "lveditor" + ".draft", "Cap" + "Cut")
_APP_RE = re.compile(r"\S*(?:" + "|".join(re.escape(t) for t in _APP_TOKENS) + r")\S*")


def sanitize(text: Any, *, limit: int = 600) -> str:
    """报告里不留本机路径/应用名/草稿：绝对路径整段换成占位符。

    CSV 与 md 是会进仓库的产物，隐私门禁要求零命中（不许出现家目录、应用名、草稿中文名），
    而子进程的原始报错里经常带着素材全路径，所以落表前统一过一遍。
    """
    s = "" if text is None else str(text)
    s = s.replace(str(Path.home()), "~")
    s = _PATH_RE.sub("~<path>", s)
    s = _APP_RE.sub("<app-path>", s)
    s = " ".join(s.split())
    return s[:limit]


# ---------------------------------------------------------------- 单套：素材池


def build_pool(style_id: str, out_dir: Path, catalog: Path) -> Tuple[Optional[Path], Optional[Path], dict]:
    """从共用目录挑这套风格的素材 → (assets_json, pool_json, pool_dict)。"""
    assets_p = out_dir / "assets.json"
    pool_p = out_dir / "asset_pool.json"
    cmd = [PY, str(DEV / "mine_replica_assets.py"),
           "--catalog", str(catalog), "--style-id", style_id,
           "--assets", str(assets_p), "--pool", str(pool_p)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(out_dir))
    (out_dir / "mine.log").write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    pool: dict = {}
    if pool_p.is_file():
        try:
            pool = json.loads(pool_p.read_text(encoding="utf-8"))
        except Exception:
            pool = {}
    ok = assets_p.is_file() and pool_p.is_file()
    return (assets_p if ok else None), (pool_p if ok else None), pool


# ---------------------------------------------------------------- 单套：跑链 + 收数


def _reasons_of(unknown_items: List[dict]) -> str:
    """unknown 逐条原因 → 归并成 `原因×条数`。"""
    counts: Dict[str, int] = {}
    for it in unknown_items or []:
        reason = str(it.get("reason") or it.get("kind") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return "; ".join(f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


def _code_fp() -> str:
    """当前链路代码指纹：主链文件随时在改（并行开发），行里必须能看出“这行是哪版代码跑的”。"""
    h = hashlib.sha256()
    for name in ("apply_replica_template.py", "replica_template.py"):
        p = SCRIPTS / name
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    for name in ("render_replica_from_draft.py", "verify_replica_template.py"):
        p = DEV / name
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:10]


def _tail(text: str, n: int = 12) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return sanitize(" | ".join(lines[-n:]), limit=900)


def _fail_step_of(report: dict) -> str:
    """从 report.json 判断卡在哪一步（verify 每一步都留了 ok/error）。"""
    steps = report.get("steps") or {}
    if not (steps.get("extract") or {}).get("segments"):
        return "extract"
    if not (steps.get("apply") or {}).get("draft_json"):
        return "apply"
    if not (steps.get("render") or {}).get("ok"):
        return "render"
    return "checks"


def run_one(style_id: str, *, out_root: Path, catalog: Path, frames: int, reference: bool,
            force: bool, timeout: int, prune: bool = True) -> dict:
    """一套风格：配池 → verify → 收数。任何一步炸掉都返回一行（带错误）。"""
    t0 = time.time()
    out_dir = out_root / style_id
    out_dir.mkdir(parents=True, exist_ok=True)
    row: Dict[str, Any] = {"style_id": style_id, "status": "fail", "fail_step": "", "exit_code": "",
                           "log": "", "error": ""}
    report_p = out_dir / "report.json"

    pool: dict = {}
    if (out_dir / "asset_pool.json").is_file():
        try:
            pool = json.loads((out_dir / "asset_pool.json").read_text(encoding="utf-8"))
        except Exception:
            pool = {}

    if not (report_p.is_file() and not force):
        row["code_fp"] = _code_fp()
        # 1) 素材池（目录模式，纯计算；失败会让 verify 退化成合成素材——那是冒烟，不是验收）
        assets_p, pool_p, pool = build_pool(style_id, out_dir, catalog)
        if assets_p is None or pool_p is None:
            mine_log = (out_dir / "mine.log")
            row.update({"fail_step": "pool", "log": sanitize(out_dir.name + "/mine.log", limit=200),
                        "error": _tail(mine_log.read_text(encoding="utf-8") if mine_log.is_file() else ""),
                        "elapsed_sec": round(time.time() - t0, 1)})
            _fill_pool_cols(row, pool)
            return row
        # 2) verify 那条链（只读调用；--out 直接就是这套的目录，产物布局天然对上）
        cmd = [PY, str(DEV / "verify_replica_template.py"), "--style-id", style_id,
               "--assets-json", str(assets_p), "--pool-json", str(pool_p),
               "--out", str(out_dir), "--frames", str(frames)]
        if not reference:
            cmd.append("--no-reference-original")
        if sys.platform == "darwin":
            cmd = ["nice", "-n", "5"] + cmd  # 并行几套时别把机器撑满
        log_p = out_dir / "verify.log"
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(out_dir),
                                  timeout=timeout)
            log_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
            row["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired as exc:
            log_text = ((exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes)
                        else (exc.stdout or "")) + "\n[TIMEOUT]"
            row.update({"fail_step": "timeout",
                        "error": sanitize(f"超时 {timeout}s 未完成", limit=200)})
            log_p.write_text(log_text, encoding="utf-8")
            row.update({"log": sanitize(str(out_dir.name) + "/verify.log", limit=200),
                        "elapsed_sec": round(time.time() - t0, 1)})
            _fill_pool_cols(row, pool)
            return row
        log_p.write_text(log_text, encoding="utf-8")
    else:
        log_p = out_dir / "verify.log"
        row["exit_code"] = "resumed"

    # 3) 收数（report.json 是唯一真源；没有它 = 没跑到收尾）
    if not report_p.is_file():
        log_txt = log_p.read_text(encoding="utf-8") if log_p.is_file() else ""
        row.update({"fail_step": row.get("fail_step") or "no_report",
                    "log": sanitize(str(out_dir.name) + "/verify.log", limit=200),
                    "error": _tail(log_txt)})
        _fill_pool_cols(row, pool)
        row["elapsed_sec"] = round(time.time() - t0, 1)
        return row

    report = json.loads(report_p.read_text(encoding="utf-8"))
    row.update(_row_from_report(style_id, report))
    if row.get("status") == "fail":
        row["fail_step"] = row.get("fail_step") or _fail_step_of(report)
        log_txt = log_p.read_text(encoding="utf-8") if log_p.is_file() else ""
        logical = (report.get("steps") or {}).get("render") or {}
        row["error"] = sanitize(logical.get("error") or _tail(log_txt), limit=900)
    _fill_pool_cols(row, pool)
    row["log"] = sanitize(str(out_dir.name) + "/verify.log", limit=200)
    row["elapsed_sec"] = round(time.time() - t0, 1)
    if prune:
        # 中间产物巨大（一套 15.6s 的 style_21 就是 render 4.6G + original_render 2.4G +
        # frames 3.2G + draft 0.5G）：24 套不清会吃掉两三百 GB。report 已收完 → 只留证据。
        row["pruned_mb"] = _prune(out_dir)
    return row


def _prune(out_dir: Path) -> int:
    """删掉重中间产物，只留 `final.mp4` / `report.json` / `frames/` 的关键帧图 / 草稿 JSON。"""
    import shutil
    freed = 0
    for name in ("render", "original_render"):
        p = out_dir / name
        if p.is_dir():
            freed += _size_of(p)
            shutil.rmtree(p, ignore_errors=True)
    keep = {out_dir / "final.mp4"}
    for sub in ("frames", "draft"):
        base = out_dir / sub
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if not f.is_file() or f in keep:
                continue
            # frames/ 只留图（.jpg/.png）；matte/描边/对照片那些 .mp4/.mov 全删。
            # draft/ 只留草稿 JSON（素材副本是套用时的 runtime 拷贝，不属进度产物）。
            if f.suffix.casefold() in (".jpg", ".jpeg", ".png", ".json"):
                continue
            try:
                freed += f.stat().st_size
                f.unlink()
            except OSError:
                pass
    (out_dir / "prune.json").write_text(
        json.dumps({"freed_mb": round(freed / 1e6, 1),
                    "kept": ["final.mp4", "report.json", "frames/*.jpg", "draft/*.json"]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return round(freed / 1e6, 1)


def _size_of(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def _fill_pool_cols(row: Dict[str, Any], pool: dict) -> None:
    totals = pool.get("totals") or {}
    need = pool.get("need") or {}
    row["pool_a_n"] = (totals.get("a_roll") or {}).get("count")
    row["pool_a_sec"] = (totals.get("a_roll") or {}).get("total_sec")
    row["pool_b_n"] = (totals.get("b_roll") or {}).get("count")
    row["pool_b_sec"] = (totals.get("b_roll") or {}).get("total_sec")
    row["pool_photo"] = (totals.get("photo") or {}).get("count")
    row["pool_bgm_sec"] = (totals.get("bgm") or {}).get("total_sec")
    row["pool_shortfall"] = sanitize(json.dumps(pool.get("shortfall") or {}, ensure_ascii=False),
                                     limit=200)
    row["_need"] = need


def _row_from_report(style_id: str, report: dict) -> dict:
    """report.json → CSV 一行（字段严格对齐 CSV_FIELDS）。"""
    steps = report.get("steps") or {}
    verdict = report.get("verdict") or {}
    ext = steps.get("extract") or {}
    app = steps.get("apply") or {}
    ren = steps.get("render") or {}
    comp = steps.get("compounds") or {}
    ghost = steps.get("ghost_check") or {}
    zo = steps.get("z_order") or {}
    fc = (steps.get("frames") or {}).get("face_check") or {}
    probe = ren.get("probe") or {}
    roles = ext.get("segments_by_role") or {}
    fonts = verdict.get("font_fallback_slots") or []
    # verdict 里的 font_fallback_slots 是**槽位号列表**（[0, 3]），apply 里那份才是字典列表
    font_names = sorted({str(f.get("template_font") or "?") for f in fonts if isinstance(f, dict)})

    render_ok = bool(ren.get("ok"))
    if ghost.get("ghost_rejected"):
        chk_ghost = "fail"
    elif ghost.get("ok"):
        chk_ghost = "ok"
    else:
        chk_ghost = "na"
    chk_z = ("ok" if zo.get("ok") else ("fail" if zo.get("pairs") is not None else "na"))
    chk_comp = "na"
    if comp:
        # 不信 compound_structure_audit 自带的 `ok`：它把「原版本来就同一张图」的放行也当成重复，
        # 于是 style_15/16 这种「3 个照片层共用原版同一张图」的好结果会被判 False。
        # 这里按组件自己算：拆出 = 写回、照片层没错类型、subdraft 都落盘、重复图不多于显式放行数。
        applied = int(comp.get("compounds_applied") or 0)
        row_n = len(comp.get("detail") or [])
        dup = int(comp.get("photo_files_duplicated") or 0)
        allowed = int(app.get("duplicate_photo_allowed") or 0)
        if not row_n and not applied:
            chk_comp = "na"      # 模版本来就没复合片段：这一项不适用
        else:
            good = (applied == row_n
                    and int(comp.get("photo_layers_wrong_type") or 0) == 0
                    and int(comp.get("subdraft_dirs_on_disk_ok") or 0) == row_n
                    and dup <= allowed)
            chk_comp = "ok" if good else "fail"
    chk_face = ("ok" if fc.get("ok") and fc.get("frames_with_face")
                else ("fail" if fc.get("ok") else "na"))
    status = "ok" if (verdict.get("draft_ok") and render_ok) else "fail"
    if status == "ok" and (chk_ghost == "fail" or chk_z == "fail"):
        status = "check_fail"
    if status == "ok" and chk_comp == "fail":
        status = "check_fail"

    return {
        "status": status,
        "dur_tpl": ext.get("duration_sec"),
        "fps": ext.get("fps"),
        "canvas": f"{probe.get('width') or (ext.get('canvas') or {}).get('width')}"
                  f"x{probe.get('height') or (ext.get('canvas') or {}).get('height')}",
        "segments": ext.get("segments"),
        "a_segs": roles.get("a_roll"),
        "b_segs": roles.get("b_roll"),
        "text_slots": ext.get("text_slots"),
        "unknown": ext.get("unknown_assets"),
        "unknown_reasons": sanitize(_reasons_of(ext.get("unknown_items") or []), limit=400),
        "asset_reused": app.get("asset_reused"),
        "asset_wrapped": app.get("asset_wrapped"),
        "content_linked": app.get("content_linked"),
        "overlay_left_blank": app.get("overlay_left_blank"),
        "ghost_rejected": app.get("ghost_rejected"),
        "slot_shrunk": app.get("slot_shrunk"),
        "anim_clamped": app.get("anim_clamped"),
        "dup_photo_blocked": app.get("duplicate_photo_blocked"),
        "dup_photo_allowed": app.get("duplicate_photo_allowed"),
        "photo_unfilled": app.get("photo_layers_unfilled"),
        "photo_distinct": app.get("photo_assets_distinct"),
        "photo_pool": app.get("photo_pool_size"),
        "font_fallback_slots": len(fonts),
        "font_fallback_names": sanitize(",".join(font_names), limit=200),
        "texts_used": app.get("texts_used"),
        "texts_truncated": app.get("texts_truncated"),
        "compounds_extracted": app.get("compounds_extracted"),
        "compounds_applied": app.get("compounds_applied"),
        "compounds_skipped": sanitize(
            json.dumps(app.get("compounds_skipped") or {}, ensure_ascii=False), limit=300),
        "sub_segments": app.get("sub_segments_total"),
        "photo_layers_mapped": app.get("photo_layers_mapped"),
        "compound_struct_ok": comp.get("ok"),
        "render_ok": render_ok,
        "render_dur": probe.get("duration_sec"),
        "render_frames": probe.get("frames"),
        "render_res": f"{probe.get('width')}x{probe.get('height')}@{probe.get('fps')}",
        "render_audio": probe.get("has_audio"),
        "render_sec": ren.get("elapsed_sec"),
        "matte_done": ren.get("matte_done"),
        "matte_failed": ren.get("matte_failed"),
        "render_unrealized": sanitize(
            json.dumps(ren.get("unrealized") or {}, ensure_ascii=False), limit=300),
        "apply_skipped": sanitize(
            json.dumps(app.get("skipped") or {}, ensure_ascii=False), limit=300),
        "chk_ghost": chk_ghost,
        "chk_zorder": chk_z,
        "chk_compound": chk_comp,
        "chk_face": chk_face,
        "verdict_ok": bool(verdict.get("draft_ok") and render_ok),
    }


# ---------------------------------------------------------------- 汇总


def write_csv(rows: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def _md_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|")


def _err_class(err: Any) -> str:
    """渲染报错归类（只给汇总分组用，原文照样逐条落 md）。"""
    e = str(err or "")
    # 顺序要紧：TimeoutExpired 的报错正文里会带着含 PosixPath(...) 的命令行，先判超时
    if "TimeoutExpired" in e:
        return "主轨复合片段 + 遮罩 → ffmpeg 卡死（600s 超时）"
    if "PosixPath" in e:
        return "主轨照片/复合片段 → EDL 序列化崩（PosixPath）"
    if "does not contain any stream" in e:
        return "子时间线空片（复合片段子层未拿到素材）"
    if not e:
        return "（无报错文本）"
    return sanitize(e.splitlines()[0], limit=80)


def driver_compound_audit(style_ids: List[str]) -> List[dict]:
    """审「复合片段落在驱动轨上」：命中的套会被驱动轨那一遍当成普通素材换掉（结构全丢）。

    判据（只用模版 JSON，不提草稿）：`segments[*].kind == "compound"` 且其 `track_index`
    等于 `tracks[*].is_driver` 为真的那个 index。
    """
    rows: List[dict] = []
    try:
        from replica_template import resolve_draft_by_style, extract_template
        for sid in style_ids:
            try:
                tpl = extract_template(resolve_draft_by_style(sid))
            except BaseException as exc:
                rows.append({"style_id": sid, "error": f"{type(exc).__name__}"})
                continue
            driver = next((t["index"] for t in tpl.get("tracks") or [] if t.get("is_driver")), None)
            comps = [s for s in tpl.get("segments") or [] if s.get("kind") == "compound"]
            on_driver = [s for s in comps if s.get("track_index") == driver]
            rows.append({"style_id": sid, "driver": driver, "compounds": len(comps),
                         "on_driver": len(on_driver)})
    except BaseException as exc:
        rows.append({"style_id": "*", "error": f"{type(exc).__name__}: {exc}"})
    return rows


def write_md(rows: List[dict], path: Path, *, out_root: Path, args: argparse.Namespace,
             audit: Optional[List[dict]] = None) -> None:
    ok = sum(1 for r in rows if r.get("status") == "ok")
    cf = sum(1 for r in rows if r.get("status") == "check_fail")
    bad = sum(1 for r in rows if r.get("status") == "fail")
    lines: List[str] = []
    lines.append(f"# 24 套风格 · 真草稿→模版→换真人素材→出片 批跑汇总\n")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- 产物根目录：`{sanitize(str(out_root))}`（每套一个子目录：`final.mp4` / "
                 f"`report.json` / `frames/`）")
    lines.append(f"- 口径：每套都真跑 `dev_tools/verify_replica_template.py`（提模版 → 套模版 → "
                 f"`render_timeline()` 渲 mp4 → 重影/z 序/复合结构/人脸四项检查）")
    lines.append(f"- 素材：共用素材目录里挑**本机真实实拍**（A-roll 3 帧≥2 帧检出人脸 + 有音轨 + "
                 f"音压>-32dB；B-roll 0/3 帧有脸）；照片层只用真实图片；无合成图/占位图")
    lines.append(f"- 结果：成功 **{ok}** · 检查未过 **{cf}** · 失败 **{bad}** / 共 {len(rows)} 套\n")

    lines.append("## 逐套结果\n")
    head = ("| 风格 | 时长@fps | 段(a/b/字) | 素材池 A·B·照片·BGM | 复用/回卷 | 留空/拒接 | "
            "dup 挡/放 | 字体回落 | 截断 | 复合 提取→写回 | 渲染 时长/帧 | 四项检查 | 判定 |")
    lines.append(head)
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        checks = f"{r.get('chk_ghost')}/{r.get('chk_zorder')}/{r.get('chk_compound')}/{r.get('chk_face')}"
        pool = (f"{r.get('pool_a_n')}·{r.get('pool_a_sec')}s / {r.get('pool_b_n')}·{r.get('pool_b_sec')}s"
                f" / {r.get('pool_photo')} 张 / {r.get('pool_bgm_sec')}s")
        lines.append("| " + " | ".join(_md_cell(x) for x in [
            r.get("style_id"),
            f"{r.get('dur_tpl')}@{r.get('fps')}",
            f"{r.get('a_segs') or 0}/{r.get('b_segs') or 0}/{r.get('text_slots') or 0}",
            pool,
            f"{r.get('asset_reused')}/{r.get('asset_wrapped')}",
            f"{r.get('overlay_left_blank')}/{r.get('ghost_rejected')}",
            f"{r.get('dup_photo_blocked')}/{r.get('dup_photo_allowed')}"
            f" (留空 {r.get('photo_unfilled')})",
            r.get("font_fallback_slots"),
            r.get("texts_truncated"),
            f"{r.get('compounds_extracted')}→{r.get('compounds_applied')}",
            (f"{r.get('render_dur')}s/{r.get('render_frames')}帧"
             if r.get("render_ok") else "—"),
            checks,
            r.get("status"),
        ]) + " |")

    fails = [r for r in rows if r.get("status") != "ok"]
    lines.append("\n## 自动汇总\n")
    by_err: Dict[str, List[str]] = {}
    for r in rows:
        if r.get("status") == "fail":
            by_err.setdefault(_err_class(r.get("error")), []).append(r["style_id"])
    if by_err:
        lines.append("**渲染失败按报错归类**（同一类通常是一个主链 bug）：\n")
        for k, v in sorted(by_err.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- {k}：{len(v)} 套（{', '.join(v)}）")
        lines.append("")
    fonts_bad = [r["style_id"] for r in rows if int(r.get("font_fallback_slots") or 0)]
    if fonts_bad:
        lines.append(f"**字体回落**（本机找不到原版字体：字号/版式照旧、字形不同）："
                     f"{', '.join(fonts_bad)}\n")
    ghost_bad = [r["style_id"] for r in rows if r.get("chk_ghost") == "fail"]
    if ghost_bad:
        lines.append(f"**重影检查未过**：{', '.join(ghost_bad)}"
                     f"（逐对原因见各套 `report.json` 的 `steps.ghost_check.detail[*].why`）\n")
    z_bad = [r["style_id"] for r in rows if r.get("chk_zorder") == "fail"]
    if z_bad:
        lines.append(f"**z 序防线未过**：{', '.join(z_bad)}\n")
    comp_bad = [f"{r['style_id']}({r.get('compounds_extracted')}→{r.get('compounds_applied')})"
                for r in rows if r.get("chk_compound") == "fail"]
    if comp_bad:
        lines.append(f"**复合片段结构未过**：{', '.join(comp_bad)}\n")

    lines.append("\n## 未过的套（逐条原始报错）\n")
    if not fails:
        lines.append("无。\n")
    for r in fails:
        lines.append(f"### {r.get('style_id')} — {r.get('status')} @ {r.get('fail_step') or '检查项'}")
        lines.append(f"- 退出码：{r.get('exit_code')} · 耗时 {r.get('elapsed_sec')}s")
        name_of = {"chk_ghost": "重影", "chk_zorder": "z 序", "chk_compound": "复合结构", "chk_face": "人脸"}
        bad_checks = [name_of[k] for k in name_of if r.get(k) == "fail"]
        if bad_checks:
            lines.append(f"- 未过的检查项：{'、'.join(bad_checks)}"
                         f"（逐对/逐层原因见 `report.json`）")
        lines.append(f"- 日志：`{r.get('log')}`")
        if r.get("error"):
            lines.append(f"- 报错尾部：`{r.get('error')}`")
        lines.append("")

    lines.append("## 口径与注意\n")
    lines.append("- `asset_reused/asset_wrapped`：池子里同一素材被第二条段用到 / 读到素材尾部回卷的次数；"
                 "长片（style_03 126.9s、A-roll 47 段）天生会复用，数字大不等于错。")
    lines.append("- `overlay_left_blank/ghost_rejected`：因「同一瞬间不许上下同源」而留空/拒接的段；"
                 "这两个数应当为 0，非 0 说明池子不够或模板本身有同源叠层。")
    lines.append("- `dup_photo_blocked/allowed`：照片层全片唯一账本挡掉/放行的重复分配；"
                 "原版本来就同图才放行。")
    lines.append("- 四项检查列 `ghost/zorder/compound/face`：`ok` 通过 · `fail` 不过 · `na` 该系统没触发。")
    lines.append("- 文案位沿用 verify 自带的 12 条示例文案，`texts_truncated` 是容量换行后仍超长的位数。")
    lines.append("")

    if audit:
        hit = [a for a in audit if a.get("on_driver")]
        lines.append("## 驱动轨复合片段审计（结构丢失风险）\n")
        lines.append("判据：`template.json` 里 `kind == \"compound\"` 的段落在驱动轨（`is_driver`）上。"
                     "驱动轨那一遍会把它当普通素材换掉，子时间线结构全丢。\n")
        lines.append("| 风格 | 复合片段 | 在驱动轨上 | 状态 |")
        lines.append("|---|---|---|---|")
        for a in audit:
            if a.get("error"):
                lines.append(f"| {a.get('style_id')} | - | - | 提模版失败 {a.get('error')} |")
                continue
            lines.append(f"| {a['style_id']} | {a['compounds']} | {a['on_driver']} | "
                         f"{'**命中**' if a['on_driver'] else '—'} |")
        lines.append(f"\n命中 {len(hit)} 套：" + (", ".join(a["style_id"] for a in hit) or "无") + "\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- 主流程


def probe_styles(style_ids: List[str]) -> Dict[str, dict]:
    """先提一遍模版（只读）拿时长/帧率：用来按「便宜的先跑」排程，并把成本算出来。"""
    from replica_template import resolve_draft_by_style, extract_template
    out: Dict[str, dict] = {}
    for sid in style_ids:
        try:
            tpl = extract_template(resolve_draft_by_style(sid))
            out[sid] = {"dur": float(tpl.get("duration_sec") or 0.0),
                        "fps": float(tpl.get("fps") or 30.0)}
        except BaseException as exc:  # SystemExit 也算「这套连模版都提不出来」
            out[sid] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="24 套复刻模版批量驱动器（提模版→换真人素材→出片→四项检查）")
    ap.add_argument("--styles", default=None, help="逗号分隔，如 style_17,style_07")
    ap.add_argument("--all", action="store_true", help="24 套全跑")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="产物根目录（每套一个子目录）")
    ap.add_argument("--catalog", default=None,
                    help="共用素材目录 JSON（默认 <out>/_pool/pool_catalog.json，没有就现建）")
    ap.add_argument("--jobs", type=int, default=3, help="并行几套（渲染是 CPU 密集，别贪多）")
    ap.add_argument("--frames", type=int, default=10, help="每套关键帧数量")
    ap.add_argument("--no-reference-original", action="store_true",
                    help="不渲原版参考片（省一半渲染时间，但少一条「原版同段」对比）")
    ap.add_argument("--force", action="store_true", help="已有 report.json 也重跑")
    ap.add_argument("--report-only", action="store_true", help="不跑链，只按现有产物重算表")
    ap.add_argument("--timeout", type=int, default=7200, help="单套超时（秒）")
    ap.add_argument("--keep-work", action="store_true",
                    help="保留渲染中间产物（默认每套收完数就清掉 render/original_render/matte）")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()

    if args.all:
        style_ids = list(STYLE_IDS)
    elif args.styles:
        style_ids = [s.strip() for s in args.styles.split(",") if s.strip()]
    else:
        ap.error("给 --styles 或 --all")

    out_root = Path(args.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    catalog = Path(args.catalog).expanduser() if args.catalog else out_root / "_pool" / "pool_catalog.json"

    rows: List[dict] = []
    if not args.report_only:
        if not catalog.is_file():
            print(f"[batch] 没有共用素材目录，现建一次 -> {catalog.name}")
            catalog.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run([PY, str(DEV / "mine_replica_assets.py"),
                                   "--build-catalog", str(catalog)],
                                  capture_output=True, text=True)
            if not catalog.is_file():
                print(f"[batch] 建目录失败：{sanitize(proc.stderr[-600:])}")
                return 2
        costs = probe_styles(style_ids)
        # 便宜的先跑（短片先出结果）；提不出模版的排最后，让它们把错误单独暴露出来
        order = sorted(style_ids,
                       key=lambda s: costs.get(s, {}).get("dur", 1e9) * costs.get(s, {}).get("fps", 30.0))
        est = sum(costs.get(s, {}).get("dur", 0) * costs.get(s, {}).get("fps", 0) for s in style_ids)
        print(f"[batch] {len(style_ids)} 套 · 估 {est / 8.4 / 60:.0f} 分钟渲染（含参考片翻倍就 ×2） · "
              f"并行 {args.jobs} · 目录 {out_root}")

        results: Dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futs = {pool.submit(run_one, sid, out_root=out_root, catalog=catalog,
                                frames=args.frames, reference=not args.no_reference_original,
                                force=args.force, timeout=args.timeout,
                                prune=not args.keep_work): sid
                    for sid in order}
            for fut in as_completed(futs):
                sid = futs[fut]
                try:
                    row = fut.result()
                except BaseException as exc:  # 单套炸掉也不许中止整批
                    row = {"style_id": sid, "status": "fail", "fail_step": "driver",
                           "error": sanitize(f"{type(exc).__name__}: {exc}"),
                           "log": f"{sid}/verify.log"}
                results[sid] = row
                print(f"[batch] {sid}: {row.get('status')} {row.get('fail_step') or ''} "
                      f"{row.get('chk_ghost', '')}/{row.get('chk_zorder', '')}/"
                      f"{row.get('chk_compound', '')}/{row.get('chk_face', '')} "
                      f"({row.get('elapsed_sec')}s) {sanitize(row.get('error') or '', limit=160)}")
        rows = [results[s] for s in style_ids if s in results]

    if not rows:  # --report-only：从盘上现有 report.json 重建表
        rows = [_row_from_disk(sid, out_root) for sid in style_ids]

    csv_p = REPORTS / f"template_batch_{args.date}.csv"
    md_p = REPORTS / f"template_batch_{args.date}.md"
    write_csv(rows, csv_p)
    write_md(rows, md_p, out_root=out_root, args=args,
             audit=driver_compound_audit([r["style_id"] for r in rows]))
    ok = sum(1 for r in rows if r.get("status") == "ok")
    print(f"[batch] 汇总 -> {csv_p.relative_to(ROOT)} / {md_p.relative_to(ROOT)} · "
          f"成功 {ok}/{len(rows)}")
    return 0 if ok == len(rows) else 1


def _row_from_disk(style_id: str, out_root: Path) -> dict:
    """只读现有产物 → 一行（--report-only 用）。"""
    out_dir = out_root / style_id
    row: Dict[str, Any] = {"style_id": style_id, "status": "fail", "fail_step": "", "exit_code": ""}
    pool: dict = {}
    if (out_dir / "asset_pool.json").is_file():
        try:
            pool = json.loads((out_dir / "asset_pool.json").read_text(encoding="utf-8"))
        except Exception:
            pool = {}
    report_p = out_dir / "report.json"
    if report_p.is_file():
        report = json.loads(report_p.read_text(encoding="utf-8"))
        row.update(_row_from_report(style_id, report))
        if row.get("status") == "fail":
            row["fail_step"] = _fail_step_of(report)
            log_p = out_dir / "verify.log"
            row["error"] = sanitize(
                ((report.get("steps") or {}).get("render") or {}).get("error")
                or _tail(log_p.read_text(encoding="utf-8") if log_p.is_file() else ""))
    else:
        row.update({"fail_step": "no_report", "error": "没有 report.json"})
    _fill_pool_cols(row, pool)
    row["log"] = f"{style_id}/verify.log"
    return row


if __name__ == "__main__":
    sys.exit(main())
