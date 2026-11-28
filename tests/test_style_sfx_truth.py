"""音效真值：只放本风格真音效，跨风格兜底一律拒绝。

回归的是用户实测事故：「成片里第 1/9/11/25/33/37/50/55/66/70 秒都有很讨厌的高频"叮"」——
根因是 step6 的 `_default_cc0_sfx()` 写死兜底到 style_01_*/style_15_*，加上 build_sound_packs
合成的 0.05s / 12kHz 电子音。这组测试锁死：候选只能来自 sound_vibe/sfx/<style_id>/，
本风格为空就不出 sfx，密度以 sfx_truth/<style_id>.json 的 sfx_per_min 为准。
"""

import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))
sys.path.insert(0, str(ROOT / "dev_tools"))

from common import maybe_reexec_in_venv

maybe_reexec_in_venv()

from common import resolve_asset_path, write_json  # noqa: E402
import step6_packaging  # noqa: E402

ASSETS = ROOT / "short-video-studio" / "assets"


def _write_wav(path: Path, seconds: float = 0.30, freq: float = 440.0, sr: int = 48000, amp: float = 0.3) -> Path:
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(seconds * sr)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            v = int(amp * 32767 * math.sin(2 * math.pi * freq * i / sr))
            frames += int(v).to_bytes(2, "little", signed=True) * 2
        wf.writeframes(bytes(frames))
    return path


def _write_pipeline(pdir: Path, duration_sec: float = 12.0, clips: int = 12, fps: int = 30,
                    full_speech: bool = False) -> Path:
    """造一份最小可跑的 step6 输入（音效足够密，才能体现密度差异）。

    `full_speech=True` 时人声覆盖整片——音效策略会把"没有人声的空白"判成纯静音段禁响，
    要测密度上限就必须给满人声。
    """
    pdir.mkdir(parents=True, exist_ok=True)
    clip_len = duration_sec / clips
    clip_rows = []
    for i in range(clips):
        t0 = i * clip_len
        clip_rows.append({
            "clip_id": f"c{i + 1}",
            "track_role": "b_roll" if i else "a_roll",
            "media_path": "",
            "timeline_in_sec": round(t0, 3),
            "timeline_out_sec": round(t0 + clip_len, 3),
            "timeline_in_frame": int(round(t0 * fps)),
            "timeline_out_frame": int(round((t0 + clip_len) * fps)),
            "cut_event_frame": int(round(t0 * fps)),
            "cut_event_id": f"e{i + 1}",
            "cut_event_type": "sentence_start" if i % 2 == 0 else "pause_end",
        })
    write_json(pdir / "03_audio_events.json", {
        "locked_duration_sec": duration_sec,
        "sentences": [{
            "id": "s1", "text": "这一句用来测试音效密度", "keywords": ["音效密度"],
            "start_sec": 0.0, "end_sec": duration_sec if full_speech else min(3.0, duration_sec),
            "start_frame": 0,
            "end_frame": int((duration_sec if full_speech else min(3.0, duration_sec)) * fps),
            "emotion_or_info": "hook",
        }],
        "events": [
            {"id": "e1", "type": "sentence_start", "time_sec": 0.0, "frame": 0, "sentence_id": "s1"},
            {"id": "e2", "type": "sentence_start", "time_sec": round(duration_sec / 2, 3),
             "frame": int(duration_sec / 2 * fps), "sentence_id": "s1"},
        ],
    })
    write_json(pdir / "04_edl.json", {
        "style_id": "style_01",
        "fps": fps,
        "clips": clip_rows,
    })
    return pdir


class StyleSfxTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.assets = self.root / "assets"
        (self.assets / "sound_vibe" / "sfx").mkdir(parents=True)
        (self.assets / "sound_vibe" / "sfx_truth").mkdir(parents=True)
        self._orig_assets = step6_packaging.ASSETS_DIR
        step6_packaging.ASSETS_DIR = self.assets
        self.addCleanup(setattr, step6_packaging, "ASSETS_DIR", self._orig_assets)

    # ── 1. 跨风格音效必须被拒 ─────────────────────────────────────────────
    def test_cross_style_sfx_is_rejected(self):
        _write_wav(self.assets / "sound_vibe" / "sfx" / "style_01" / "cut_转场.wav")
        _write_wav(self.assets / "sound_vibe" / "sfx" / "style_01" / "ui_提示.wav")
        # 平铺的旧文件（含合成音的命名习惯）也必须完全不被当成候选
        _write_wav(self.assets / "sound_vibe" / "sfx" / "style_01_ui.wav", seconds=0.05, freq=12000.0)

        pool01 = step6_packaging.scan_style_sfx("style_01")
        self.assertTrue(pool01.get("cut"), pool01)
        self.assertEqual(step6_packaging.scan_style_sfx("style_02"), {}, "别的风格目录为空时不得借用 style_01")
        self.assertIsNone(step6_packaging._default_cc0_sfx("cut", "style_02"))
        self.assertIsNone(step6_packaging._default_cc0_sfx("ui", "style_99"))

        pdir = _write_pipeline(self.root / "p_cross")
        res = step6_packaging.run_step6(pdir, style_id="style_02")
        self.assertEqual(res["sfx_cues"], [], "本风格没有音效时不允许塞别家的音效")
        self.assertIn("style_02", res["sfx_disabled_reason"])

    # ── 2. 本风格为空时不出 sfx ──────────────────────────────────────────
    def test_empty_style_yields_silent_sfx_track(self):
        # 目录存在但一个文件都没有（源草稿确实没音效），并且 truth 明确记 0
        (self.assets / "sound_vibe" / "sfx" / "style_04").mkdir(parents=True)
        write_json(self.assets / "sound_vibe" / "sfx_truth" / "style_04.json", {
            "style_id": "style_04", "sfx_count": 0, "sfx_per_min": 0.0, "files": [],
        })
        pdir = _write_pipeline(self.root / "p_empty")
        res = step6_packaging.run_step6(pdir, style_id="style_04")
        self.assertEqual(res["sfx_cues"], [])
        self.assertIn("源草稿", res["sfx_disabled_reason"])
        self.assertEqual(res["budget"]["used_sfx_cues"], 0)
        self.assertEqual(res["sfx_per_min_target"], 0.0)

        sfx_wav = pdir / "06_sfx_track.wav"
        self.assertTrue(sfx_wav.is_file(), "即使不放音效也要产出空轨，下游 step8 依赖它")
        with wave.open(str(sfx_wav)) as wf:
            frames = wf.readframes(wf.getnframes())
        self.assertEqual(max((abs(b) for b in frames), default=0), 0, "禁用音效后 06_sfx_track.wav 必须是纯静音")

    # ── 3. 真音效目录优先于任何兜底 ───────────────────────────────────────
    def test_style_dir_beats_any_fallback(self):
        _write_wav(self.assets / "sound_vibe" / "sfx" / "style_01" / "cut_外来转场.wav")
        _write_wav(self.assets / "sound_vibe" / "sfx" / "style_01_ui.wav", seconds=0.05, freq=12000.0)
        own = [
            _write_wav(self.assets / "sound_vibe" / "sfx" / "style_07" / "cut_呼.wav", freq=300.0),
            _write_wav(self.assets / "sound_vibe" / "sfx" / "style_07" / "info_翻页.wav", freq=800.0),
            _write_wav(self.assets / "sound_vibe" / "sfx" / "style_07" / "receipt_小票.wav", freq=600.0),
        ]
        pdir = _write_pipeline(self.root / "p_own")
        res = step6_packaging.run_step6(pdir, style_id="style_07")
        self.assertGreater(len(res["sfx_cues"]), 0)
        own_dir = (self.assets / "sound_vibe" / "sfx" / "style_07").resolve()
        for cue in res["sfx_cues"]:
            p = Path(cue["file"]).resolve()
            self.assertEqual(p.parent, own_dir, f"cue 必须是本风格目录里的文件，实际：{p}")
            self.assertNotIn("style_01", cue["sfx_name"])
        used = {Path(c["file"]).name for c in res["sfx_cues"]}
        self.assertTrue(used <= {p.name for p in own}, used)

    # ── 4. 密度跟 sfx_truth 走 ───────────────────────────────────────────
    def test_density_follows_sfx_truth(self):
        """密度是**上限**，不是撒点器。

        2026-11-24 起音效必须挂在可归因锚点上（切点/花字/换行/拍点），密度只决定"最多能响几次"。
        所以这里的语料是"每个切点都是结构性切点 + 人声铺满"，此时条数就完全由每条/分钟上限决定：
        6/min 的风格只能落 6 声，48/min 的能落满 24 声。
        """
        sfx_files = []
        for idx_f in range(1, 11):
            rel_f = f"sound_vibe/sfx/style_15/cut_呼{idx_f:02d}.wav"
            _write_wav(self.assets / rel_f, freq=300.0 + idx_f * 20.0)
            sfx_files.append({"file": rel_f, "role": "cut", "name": f"呼{idx_f:02d}"})
        results = {}
        for tag, ppm in (("sparse", 6.0), ("dense", 48.0)):
            write_json(self.assets / "sound_vibe" / "sfx_truth" / "style_15.json", {
                "style_id": "style_15", "sfx_count": 12, "sfx_per_min": ppm,
                "files": sfx_files,
            })
            pdir = _write_pipeline(self.root / f"p_{tag}", duration_sec=24.0, clips=24, full_speech=True)
            results[tag] = step6_packaging.run_step6(pdir, style_id="style_15")

        self.assertEqual(results["sparse"]["sfx_per_min_target"], 6.0)
        self.assertEqual(results["dense"]["sfx_per_min_target"], 48.0)
        self.assertLess(results["sparse"]["budget"]["max_sfx_cues"], results["dense"]["budget"]["max_sfx_cues"])
        self.assertLessEqual(
            results["dense"]["budget"]["used_sfx_cues"],
            results["dense"]["budget"]["max_sfx_cues"],
        )
        self.assertGreater(
            results["dense"]["budget"]["used_sfx_cues"],
            results["sparse"]["budget"]["used_sfx_cues"],
            "密度高的风格必须真的落更多音效",
        )
        # 6/min 的稀疏风格只能落很少几声（预算算法保留 15/min 下限，24s → 6 个上限）
        self.assertLessEqual(results["sparse"]["budget"]["used_sfx_cues"], 6)

    # ── 5. 角色归类与真值表契约（抽取工具） ──────────────────────────────
    def test_role_classification_and_truth_contract(self):
        from extract_style_sfx import classify_role

        cases = {
            "“呼”的转场音效": "cut",
            "一声响铃": "hook",
            "铃铛": "hook",
            "快速翻页声": "info",
            "键盘空格声": "info",
            "开瓶塞": "accent",
            "发条转动": "accent",
            "哇呜": "accent",
            "风铃声": "accent",
            "小票打印": "receipt",
            "叮，提示音4": "misc",
        }
        for name, role in cases.items():
            self.assertEqual(classify_role(name), role, name)

        truth_root = ASSETS / "sound_vibe" / "sfx_truth"
        styles = [f"style_{i:02d}" for i in range(1, 25)]
        missing = [s for s in styles if not (truth_root / f"{s}.json").is_file()]
        if missing:
            self.skipTest(f"本机尚未跑 dev_tools/extract_style_sfx.py：{missing}")
        for sid in styles:
            truth = json.loads((truth_root / f"{sid}.json").read_text(encoding="utf-8"))
            self.assertEqual(truth["style_id"], sid)
            self.assertIn("sfx_count", truth)
            self.assertIn("sfx_per_min", truth)
            self.assertIn("gain_db_median", truth)
            for f in truth["files"]:
                self.assertTrue(f["file"].startswith(f"sound_vibe/sfx/{sid}/"), f["file"])
                resolved = resolve_asset_path(f["file"], ASSETS)
                if resolved is None:
                    self.skipTest(f"未挂载本机私有音效资产（公开仓库不含源草稿音频）：{f['file']}")
                self.assertTrue(resolved.is_file(), f["file"])
            for cue in truth["cues"]:
                self.assertIn("offset_from_cut_sec", cue)
                self.assertIn("gain_db", cue)
        # 真音效一律落在风格子目录里，平铺目录不再承载候选
        t21 = json.loads((truth_root / "style_21.json").read_text(encoding="utf-8"))
        self.assertGreater(t21["sfx_count"], 0)
        self.assertGreater(t21["sfx_per_min"], 20.0)

    # ── 6. BGM 也只能是自家那一首（源草稿里抽的真曲） ──────────────
    def test_bgm_never_borrows_other_style(self):
        from vibe_sound import get_style_sound_profile

        # 没有 manifest 节点时，step6 必须明说“不放 BGM”，而不是偷偷挂上 style_01 的
        pdir = _write_pipeline(self.root / "p_bgm_none")
        res = step6_packaging.run_step6(pdir, style_id="style_23")
        self.assertIsNone(res["bgm"]["file"])
        self.assertIn("style_23", res["bgm_disabled_reason"] or "")

        # 未知风格不再按 archetype 借别家的声音包
        self.assertIsNone(get_style_sound_profile("style_99"))

        # manifest 指向本风格时正常认领；指向别家文件名也不会被当真
        bgm_dir = self.assets / "sound_vibe" / "bgm"
        own = _write_wav(bgm_dir / "style_23_bgm.wav", seconds=1.0, freq=220.0)
        _write_wav(bgm_dir / "style_01_bgm.wav", seconds=1.0, freq=440.0)
        write_json(self.assets / "sound_vibe" / "vibe_sound_manifest.json", {
            "styles": {"style_23": {
                "bgm_wav": "sound_vibe/bgm/style_23_bgm.wav",
                "bgm_status": "source_extracted",
                "bgm_name": "柔和的光",
                "bgm_dual": False,
                "bgm_switch_sec": None,
            }}
        })
        pdir2 = _write_pipeline(self.root / "p_bgm_own")
        res2 = step6_packaging.run_step6(pdir2, style_id="style_23")
        self.assertEqual(res2["bgm"]["file"], "sound_vibe/bgm/style_23_bgm.wav")
        self.assertTrue((self.assets / res2["bgm"]["file"]).is_file())
        self.assertIsNone(res2["bgm_disabled_reason"])
        self.assertEqual(Path(own).name, "style_23_bgm.wav")

    # ── 7. 真盘上的 manifest 不许出现跨风格 BGM/音效 ────────────────
    def test_real_manifest_has_zero_cross_style_audio(self):
        mf_p = ASSETS / "sound_vibe" / "vibe_sound_manifest.json"
        if not mf_p.is_file():
            self.skipTest("缺少 vibe_sound_manifest.json")
        mf = json.loads(mf_p.read_text(encoding="utf-8"))
        styles = mf.get("styles") or {}
        self.assertTrue(styles)
        for sid, node in styles.items():
            bgm = node.get("bgm_wav")
            if bgm and not node.get("bgm_is_silence_placeholder"):
                self.assertIn(f"{sid}_bgm", str(bgm), f"{sid} 的 BGM 指向了别家：{bgm}")
            for role, rel in (node.get("sfx") or {}).items():
                self.assertIn(f"/sfx/{sid}/", str(rel), f"{sid} 的 {role} 音效指向了别家：{rel}")
            for role, rels in (node.get("sfx_roles") or {}).items():
                for rel in rels:
                    self.assertIn(f"/sfx/{sid}/", str(rel), f"{sid} 的 {role} 音效指向了别家：{rel}")

    # ── 8. BGM 真值与源草稿一致；双 BGM 切点是实测值 ────────────────
    def test_bgm_truth_matches_source_draft(self):
        truth_root = ASSETS / "sound_vibe" / "sfx_truth"
        fg_p = ROOT / "forensic" / "jianying_ground_truth_full.json"
        src_p = ROOT / "forensic" / "style_sources.json"
        if not (truth_root / "style_21.json").is_file() or not fg_p.is_file():
            self.skipTest("尚未抽取真 BGM")
        ground = {p["name"]: (p.get("audio") or {}) for p in json.loads(fg_p.read_text(encoding="utf-8"))["projects"]}
        sources = json.loads(src_p.read_text(encoding="utf-8"))["styles"]
        checked = 0
        for sid, draft in sources.items():
            t_p = truth_root / f"{sid}.json"
            self.assertTrue(t_p.is_file(), f"{sid} 缺 truth")
            bgm = json.loads(t_p.read_text(encoding="utf-8")).get("bgm") or {}
            self.assertIn("med_vol_db", bgm)
            self.assertIn("switch_sec", bgm)
            g = ground.get(draft) or {}
            # forensic 自家表里少数草稿漏抓了某个 BGM 层（例：style_12 源草稿只记了 Elegant），
            # 层数对不上就不做逐值对比，避免拿错基准去细化
            if g.get("bgm_count") is not None and int(g["bgm_count"]) != len(bgm.get("layers") or []):
                continue
            if g.get("bgm_med_vol_db") is not None:
                # 四舍五入到 0.1dB 的边界上可能差一档（-9.1 / -9.2），允许 0.15dB 容差
                self.assertAlmostEqual(
                    float(bgm["med_vol_db"]), float(g["bgm_med_vol_db"]), delta=0.15,
                    msg=f"{sid} BGM 音量真值对不上",
                )
                checked += 1
        self.assertGreater(checked, 10)

        t21 = json.loads((truth_root / "style_21.json").read_text(encoding="utf-8"))["bgm"]
        self.assertEqual(t21["name"], "It is All in Your Imagination")
        self.assertTrue(t21["dual_bgm"])
        bgm_hit = resolve_asset_path(t21["file"], ASSETS)
        if bgm_hit is None:
            self.skipTest(f"未挂载本机私有 BGM 资产（公开仓库不含源草稿曲目）：{t21['file']}")
        self.assertTrue(bgm_hit.is_file())
        # 实测切点（复合片段 0.45→1.733s 退出），不是拍脑袋的 3.0s
        self.assertAlmostEqual(float(t21["switch_sec"]), 1.733, places=3)
        self.assertNotAlmostEqual(float(t21["switch_sec"]), 3.0, places=1)

    # ── 9. 无 BGM 的静音占位：存在且真的是静音 ───────────────────────
    def test_no_bgm_placeholder_is_silent(self):
        import build_sound_packs

        rel = build_sound_packs.no_bgm_placeholder(root=self.root / "placeholder_assets")
        p = self.root / "placeholder_assets" / rel
        self.assertTrue(p.is_file())
        with wave.open(str(p)) as wf:
            frames = wf.readframes(wf.getnframes())
            self.assertGreater(wf.getnframes(), 48000)
        self.assertEqual(max((abs(b) for b in frames), default=0), 0)


if __name__ == "__main__":
    unittest.main()
