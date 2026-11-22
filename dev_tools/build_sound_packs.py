#!/usr/bin/env python3
"""为 24 套风格各建一套音效包（源里已有的保留，缺的按源音效类别合成）。

设计原则：
* 源 BGM / 音效是剪映曲库与版权库里的具体曲目，无法随包分发；manifest 里记录**源曲目名**
  与 `bgm_status: source_required`，用户把同名文件放进 `sound_vibe/user/<style_id>/` 即
  可 1:1 换回原始音色。
* 缺的 17 套风格不再借用别家音效，而是按源草稿里出现频率最高的音效**类别**
  （转场呼 / 撞击 / 叮铃 / 打字 / 纸张 / 气泡 / 收银）合成同族拟音，并标明来源类别。
* 音效密度 `sfx_min_gap_sec` 由源实测 `sfx_per_min` 反算，密度高的风格不再被统一上限削平。

    /tmp/jyvenv/bin/python dev_tools/build_sound_packs.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "short-video-studio/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import numpy as np  # noqa: E402

from synth_motion_sfx import SR, synthesize_sfx_waveform  # noqa: E402

ASSETS = ROOT / "short-video-studio/assets"
SFX_DIR = ASSETS / "sound_vibe/sfx"
MANIFEST = ASSETS / "sound_vibe/vibe_sound_manifest.json"
LIB = ASSETS / "style_spectrum/style_spectrum_library.json"
SRC = ROOT / "forensic/source_manifest.json"

# 源音效名 → 合成原语（按子串命中，先长后短）
CATEGORY_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("嗖", "呼", "转场", "whoosh", "旋风", "扫光", "闪回"), "whoosh_in", "转场呼"),
    (("纸张", "翻页", "报纸", "书本"), "swish", "纸张翻动"),
    (("打字", "键盘"), "tick", "打字"),
    (("鼠标", "点击", "off"), "click", "点击"),
    (("叮", "铃", "水滴", "风铃", "闪光", "高音", "魔法", "闪耀", "仙尘"), "chime", "叮铃水滴"),
    (("敲", "撞击", "咚", "砰", "锣", "枪", "刀", "哐", "tom鼓", "震撼", "弹簧", "拉扯", "拔刀"), "impact", "撞击敲击"),
    (("钱", "收银", "到账", "金币", "结账"), "pop_low", "收银到账"),
    (("气泡", "啵", "放屁", "Q弹", "噗"), "pop", "气泡弹出"),
    (("发条", "齿轮", "旋钮", "咔哒"), "drag", "机械转动"),
]


def classify(name: str) -> tuple[str, str]:
    low = str(name).lower()
    for keys, kind, label in CATEGORY_RULES:
        if any(k.lower() in low for k in keys):
            return kind, label
    return "pop", "通用点缀"


def write_wav(path: pathlib.Path, wave_data: np.ndarray) -> None:
    data = np.clip(np.asarray(wave_data, dtype=np.float64), -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(int(pcm.shape[1]) if pcm.ndim > 1 else 1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


def main() -> None:
    lib = json.loads(LIB.read_text(encoding="utf-8"))
    src = json.loads(SRC.read_text(encoding="utf-8"))["styles"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {"styles": {}}
    styles = manifest.setdefault("styles", {})

    created = 0
    for entry in lib["styles"]:
        sid = entry["style_id"]
        m = src[sid]["metrics"]
        top = [(n, c) for n, c in (m.get("sfx_top_names") or [])]
        kinds: dict[str, str] = {}
        labels: dict[str, str] = {}
        for name, _count in top:
            kind, label = classify(name)
            kinds.setdefault(kind, name)
            labels.setdefault(label, "")
        # 三槽位选取：转场 / 重音 / 界面点缀（缺失就按序补）
        order = list(kinds)

        def pick(*preferred: str) -> str:
            for p in preferred:
                if p in kinds:
                    return p
            return order[0] if order else "pop"

        slots = {
            "transition": pick("whoosh_in", "swish"),
            "accent": pick("impact", "chime", "pop_low"),
            "ui": pick("tick", "click", "pop"),
            "receipt": pick("pop_low", "chime", "pop"),
        }
        seed_base = int(hashlib.sha256(sid.encode()).hexdigest()[:8], 16)
        sfx_paths: dict[str, str] = {}
        for slot, kind in slots.items():
            out = SFX_DIR / f"{sid}_{slot}.wav"
            if not out.is_file():
                write_wav(out, synthesize_sfx_waveform(kind, seed=seed_base % 9973 + len(slot)))
                created += 1
            sfx_paths[slot] = f"sound_vibe/sfx/{out.name}"

        sfx_pm = float(m.get("sfx_per_min") or 24.0)
        prev = styles.get(sid) or {}
        bgm_rel = prev.get("bgm_wav")
        styles[sid] = {
            "bgm_wav": bgm_rel if bgm_rel and (ASSETS / bgm_rel).is_file() else None,
            "bgm_status": "bundled" if bgm_rel and (ASSETS / bgm_rel).is_file() else "source_required",
            "source_bgm_names": m.get("bgm_names") or [],
            "source_sfx_names": [n for n, _ in top[:6]],
            "sfx": sfx_paths,
            "sfx_source_kinds": {slot: kinds.get(kind, kind) for slot, kind in slots.items()},
            "sfx_per_min_source": sfx_pm,
            "sfx_min_gap_sec": round(max(0.55, 60.0 / max(6.0, sfx_pm) * 0.68), 2),
            "sfx_gain_db": float(prev.get("sfx_gain_db", -9.0)),
            "bgm_base_db": float(prev.get("bgm_base_db", -12.0)),
            "duck_depth_db": float(prev.get("duck_depth_db", -10.0)),
            "measured": "forensic/source_manifest.json",
        }

    manifest["version"] = "4.0.0"
    manifest["policy"] = {
        "bgm": "源曲目不可随包分发；bundled=随包替代，source_required=需用户按 source_bgm_names 自备",
        "sfx": "按源草稿出现频率最高的音效类别合成同族拟音；sfx_per_min_source 为实测密度",
        "min_gap": "由实测 sfx_per_min 反算，不再统一 48 次/分上限",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bundled = sum(1 for v in styles.values() if v["bgm_status"] == "bundled")
    print(f"sound packs: {len(styles)} styles, {created} new wav, bgm bundled={bundled} source_required={len(styles)-bundled}")


if __name__ == "__main__":
    main()
