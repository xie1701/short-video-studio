#!/usr/bin/env python3
"""为 24 套风格各建一套**真音效**包（源草稿里实际用过的音效，不再合成电子音）。

历史事故：旧版对没有专包的 17 套风格用 `synth_motion_sfx` 合成拟音，产出
`style_XX_ui.wav` 这类 0.05s、频谱重心 12kHz 的"叮"；step6 又跨风格兜底到 `style_01_*`，
于是任何一套风格的成片里都混进了别家的高频塑料音。

现在这一路改成「抽真音效」：直接调用 `dev_tools/extract_style_sfx.py`，从剪映源草稿的
`materials/audio/*.mp3` 里截出**源作者真正用过的**那几声，落盘到
`assets/sound_vibe/sfx/<style_id>/`；源草稿里没有音效的风格就什么都不产出（宁可静音）。
旧版的合成 wav 会被移进 `~/cola/outputs/deprecated_synth_sfx/` 留档（路径按当前机器 HOME 拼），不直接删。

设计原则：
* BGM 同样从源草稿抽真曲：`assets/sound_vibe/bgm/<style_id>_bgm.<ext>`，manifest 记
  `bgm_status: source_extracted`。源草稿没 BGM 的风格用静音占位（`_no_bgm.wav`）
  并记 `bgm_disabled_reason`，**绝不回落到 style_01 的 BGM**。
  这些原曲是剪映曲库版权曲目，仅限本机/本人项目使用（`bgm_license=source_draft_local_only`）。
* 音效密度 `sfx_min_gap_sec` 由源实测 `sfx_per_min` 反算，密度高的风格不再被统一上限削平。

    python dev_tools/build_sound_packs.py    # 用仓库专用 venv 的 python
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "short-video-studio/scripts"
DEV_TOOLS = ROOT / "dev_tools"
for _p in (SCRIPTS, DEV_TOOLS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import extract_style_sfx as extractor  # noqa: E402

ASSETS = ROOT / "short-video-studio/assets"
SFX_DIR = ASSETS / "sound_vibe/sfx"
MANIFEST = ASSETS / "sound_vibe/vibe_sound_manifest.json"
LIB = ASSETS / "style_spectrum/style_spectrum_library.json"
DEPRECATED = pathlib.Path.home() / "cola/outputs/deprecated_synth_sfx"

# 管线角色 → 源音效角色（取真音效时按这个顺序找）
PIPELINE_SLOT_ROLES: dict[str, tuple[str, ...]] = {
    "transition": ("cut",),
    "accent": ("accent", "hook", "misc"),
    "ui": ("info", "misc"),
    "receipt": ("receipt", "accent", "misc"),
}

SYNTH_KINDS = ("whoosh_in", "swish", "tick", "click", "chime", "impact", "pop_low", "pop", "drag")


def _referenced_flat_sfx() -> set[str]:
    """animation_sfx_pairs / sfx_intent_taxonomy 里被引用的平铺音效，迁移时必须保留。"""
    keep: set[str] = set()
    for rel in ("style_spectrum/animation_sfx_pairs.json", "style_spectrum/sfx_intent_taxonomy.json"):
        p = ASSETS / rel
        if not p.is_file():
            continue
        doc = json.loads(p.read_text(encoding="utf-8"))

        def walk(node) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("cc0_vibe_sfx", "sfx", "audio") and isinstance(v, str):
                        keep.add(pathlib.Path(v).name)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for x in node:
                    if isinstance(x, str) and x.startswith("sound_vibe/"):
                        keep.add(pathlib.Path(x).name)
                    else:
                        walk(x)

        walk(doc)
    return keep


def is_synth_output(path: pathlib.Path, style_id: str, seed_base: int) -> bool:
    """逐采样比对 `synthesize_sfx_waveform()` 的输出，判定这个 wav 是不是旧版合成音。"""
    try:
        import hashlib

        import numpy as np

        from synth_motion_sfx import synthesize_sfx_waveform
    except Exception:
        return False
    base = seed_base if seed_base >= 0 else int(hashlib.sha256(style_id.encode()).hexdigest()[:8], 16)
    try:
        import wave

        with wave.open(str(path)) as wf:
            ch = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())
        arr = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32767.0
        if ch > 1:
            arr = arr.reshape(-1, ch)
        for slot in ("transition", "accent", "ui", "receipt"):
            seed = base % 9973 + len(slot)
            for kind in SYNTH_KINDS:
                cand = synthesize_sfx_waveform(kind, seed=seed).astype(np.float64)
                if cand.shape == arr.shape and float(np.max(np.abs(cand - arr))) < 2e-3:
                    return True
    except Exception:
        return False
    return False


def deprecate_synth_sfx(verbose: bool = True) -> dict[str, int]:
    """把旧版合成的平铺音效（style_XX_role.wav）移进留档目录。只移动、不删除。"""
    import hashlib

    keep = _referenced_flat_sfx()
    moved = skipped = 0
    if not SFX_DIR.is_dir():
        return {"moved": 0, "kept_real": 0, "skipped_referenced": 0}
    for p in sorted(SFX_DIR.glob("style_*_*.wav")):
        part = p.stem.split("_")
        if len(part) < 3 or not part[1].isdigit():
            continue
        sid = f"{part[0]}_{part[1]}"
        if p.name in keep:
            skipped += 1
            continue
        seed_base = int(hashlib.sha256(sid.encode()).hexdigest()[:8], 16)
        if not is_synth_output(p, sid, seed_base):
            skipped += 1
            continue
        DEPRECATED.mkdir(parents=True, exist_ok=True)
        dest = DEPRECATED / p.name
        if dest.is_file():
            p.unlink()
        else:
            shutil.move(str(p), str(dest))
        moved += 1
    if verbose:
        print(f"[sound_packs] 合成音留档：移走 {moved} 个 -> {DEPRECATED}（保留真录音 {skipped} 个）")
    return {"moved": moved, "kept_real": skipped, "skipped_referenced": len(keep & {q.name for q in SFX_DIR.glob('*.wav')})}


def _rel(path: pathlib.Path) -> str:
    return path.resolve().relative_to(ASSETS.resolve()).as_posix()


NO_BGM_PLACEHOLDER = "sound_vibe/bgm/_no_bgm.wav"


def no_bgm_placeholder(root: pathlib.Path | None = None) -> str:
    """源草稿没 BGM 时用的静音占位（3s）。

    step3 会把它按片长循环铺满，所以听感就是“没有 BGM”；这么做是为了堵住 step3 里
    「缺 BGM 就回落到 style_01_bgm」的老行为，让任何风格都不会放出别家的音乐。
    """
    base = root if root is not None else ASSETS
    p = base / NO_BGM_PLACEHOLDER
    if not p.is_file():
        import wave

        p.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(b"\x00\x00\x00\x00" * (48000 * 3))
    return NO_BGM_PLACEHOLDER


def main() -> None:
    lib = json.loads(LIB.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {"styles": {}}
    styles = manifest.setdefault("styles", {})

    extractor.extract_all(force=False)
    truth_map = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((ASSETS / "sound_vibe/sfx_truth").glob("style_*.json"))
    }
    deprecate_synth_sfx()

    empty: list[str] = []
    no_bgm: list[str] = []
    built = 0
    for entry in lib["styles"]:
        sid = entry["style_id"]
        truth = truth_map.get(sid) or {}
        prev = styles.get(sid) or {}
        # BGM 只认本风格从源草稿抽出来的那一首，缺就缺，绝不回落到 style_01
        bgm_truth = truth.get("bgm") or {}
        bgm_rel = bgm_truth.get("file") if (bgm_truth.get("file") and (ASSETS / str(bgm_truth["file"])).is_file()) else None
        dual_cfg = entry.get("dual_bgm_structure") or {}
        bgm_base_db = float(dual_cfg.get("main_bgm_gain_db") if dual_cfg.get("main_bgm_gain_db") is not None else prev.get("bgm_base_db", -12.0))
        if bgm_rel is None:
            no_bgm.append(sid)
        else:
            # 旧 BGM 的节拍缓存（<sid>_bgm.json）属于另一首曲子时删掉，避免 step3 拿错 BPM
            stale = ASSETS / "sound_vibe" / "bgm" / f"{sid}_bgm.json"
            new_dur = float(bgm_truth.get("duration_sec") or 0.0)
            if stale.is_file() and new_dur > 0:
                try:
                    old_dur = float(json.loads(stale.read_text(encoding="utf-8")).get("duration_sec") or 0.0)
                except Exception:
                    old_dur = 0.0
                if abs(old_dur - new_dur) > 1.0:
                    stale.unlink()

        role_files: dict[str, list[str]] = {}
        for f in truth.get("files") or []:
            target = SFX_DIR / sid / pathlib.Path(f["file"]).name
            if target.is_file():
                role_files.setdefault(str(f["role"]), []).append(_rel(target))

        slots: dict[str, str] = {}
        for slot, preferred in PIPELINE_SLOT_ROLES.items():
            for role in preferred:
                if role_files.get(role):
                    slots[slot] = role_files[role][0]
                    break

        ppm = float(truth.get("sfx_per_min") or 0.0)
        if slots:
            built += 1
        else:
            empty.append(sid)
        styles[sid] = {
            "bgm_wav": bgm_rel or no_bgm_placeholder(),
            "bgm_status": "source_extracted" if bgm_rel else "no_source_bgm",
            "bgm_is_silence_placeholder": bgm_rel is None,
            "bgm_name": bgm_truth.get("name"),
            "bgm_duration_sec": bgm_truth.get("duration_sec"),
            "bgm_med_vol_db": bgm_truth.get("med_vol_db"),
            "bgm_dual": bool(bgm_truth.get("dual_bgm")),
            "bgm_switch_sec": bgm_truth.get("switch_sec"),
            "bgm_license": bgm_truth.get("license"),
            "bgm_disabled_reason": None if bgm_rel else (
                truth.get("bgm_disabled_reason") or f"{sid} 源草稿无 BGM，本风格不放 BGM"
            ),
            "source_bgm_names": [str(p.get("name")) for p in (bgm_truth.get("pieces") or [])] or (prev.get("source_bgm_names") or []),
            "source_sfx_names": [c["name"] for c in (truth.get("cues") or [])][:6],
            "sfx": slots,
            "sfx_roles": role_files,
            "sfx_dir": f"sound_vibe/sfx/{sid}",
            "sfx_status": "source_extracted" if slots else "source_empty",
            "sfx_per_min_source": ppm,
            "sfx_min_gap_sec": round(max(0.55, 60.0 / max(6.0, ppm) * 0.68), 2) if ppm > 0 else 6.0,
            "sfx_gain_db": float(prev.get("sfx_gain_db", -9.0)),
            "bgm_base_db": bgm_base_db,
            "duck_depth_db": float(prev.get("duck_depth_db", -10.0)),
            "measured": f"assets/sound_vibe/sfx_truth/{sid}.json",
        }

    manifest["version"] = "5.1.0"
    manifest["policy"] = {
        "bgm": "每套风格只用从它自己源草稿里抽出的原曲（bgm/<style_id>_bgm.<ext>）；源草稿没有就标记 no_source_bgm 并静音，绝不回落到别的风格",
        "sfx": "全部取自源草稿 materials/audio 里真实用过的音效（sfx_truth/<style>.json 为实测表），不再合成",
        "min_gap": "由实测 sfx_per_min 反算，不再统一 48 次/分上限",
        "license": "BGM 为剪映曲库原曲，仅限本机/本人项目使用（bgm_license=source_draft_local_only），不随包分发",
        "dual_bgm": (
            "step3_lock_audio 的双段引子写死了 style_06 的 BGM（跨风格），本仓已把 style_06_bgm.m4a "
            "移出 sound_vibe/bgm（留档在 ~/cola/outputs/deprecated_bgm/），双段引子因此停用；"
            "实测切点记在 sfx_truth/<style>.json 的 bgm.switch_sec（style_21=1.733s，不是拍脑袋的 3.0s），"
            "待 step3 改成读每风格自己的引子曲后可恢复"
        ),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"sound packs: {len(styles)} styles, {built} with source sfx, {len(empty)} without "
        f"{empty}; bgm source_extracted={len(styles) - len(no_bgm)} no_source_bgm={len(no_bgm)} {no_bgm}"
    )


if __name__ == "__main__":
    main()
