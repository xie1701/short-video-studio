#!/usr/bin/env python3
"""V12 M1 通用语义断句、style_02 一体化与通用 QA 守卫回归测试。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import common  # noqa: E402
import director_plan  # noqa: E402
import independent_verifier  # noqa: E402
import kinetic_choreographer  # noqa: E402
import qa_inspector  # noqa: E402
import render_template_overlays  # noqa: E402
import step5_subtitles  # noqa: E402


_STRICT_MID_TAILS = frozenset("地得之把被从向给俾在喺这那呢嗰𠮶某每各哪")
_AUX_HEAD_ALLOW = (
    "之前", "之后", "之内", "之外", "之间", "之上", "之下", "之中", "之一", "之类",
    "了解", "了不起", "得到", "着急", "着想", "过了", "过去", "过来", "过年", "过关", "过头", "过分",
)


def _make_word_timed_sentence(sid: str, text: str, start_sec: float, end_sec: float) -> Dict[str, Any]:
    clean = [ch for ch in text if not ch.isspace()]
    dur = max(0.3, end_sec - start_sec)
    step = dur / max(1, len(clean))
    words = []
    for idx, ch in enumerate(clean):
        ws = round(start_sec + idx * step, 4)
        we = round(start_sec + (idx + 1) * step, 4)
        words.append({"word": ch, "start_sec": ws, "end_sec": we})
    return {
        "id": sid,
        "text": text,
        "start_sec": round(start_sec, 4),
        "end_sec": round(end_sec, 4),
        "words": words,
    }


def _build_audio_events(rows: List[tuple[str, float, float]]) -> Dict[str, Any]:
    sents = [_make_word_timed_sentence(f"s{i}", txt, st, en) for i, (txt, st, en) in enumerate(rows)]
    return {
        "sentences": sents,
        "locked_duration_sec": round(max((s["end_sec"] for s in sents), default=0.0) + 0.5, 3),
    }


def _write_minimal_pipeline_for_step5(pdir: Path, audio: Dict[str, Any]) -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "03_audio_events.json").write_text(json.dumps(audio, ensure_ascii=False, indent=2), encoding="utf-8")
    (pdir / "04_edl.json").write_text(
        json.dumps({"clips": [], "fps": 30, "total_duration_sec": audio["locked_duration_sec"]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _make_dummy_clip(out_path: Path, duration: float = 0.5) -> None:
    ff = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [
            ff, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=30",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", f"{duration:.2f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(out_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _assert_entries_clean(testcase: unittest.TestCase, entries: List[Dict[str, Any]], context: str) -> None:
    plain_rows = [
        (
            re.sub(r"[\s，。！？、；：,.!?;:]+", "", str(e.get("text") or e.get("text_or_asset") or "")),
            float(e.get("start_sec") or 0.0),
            float(e.get("end_sec") or 0.0),
        )
        for e in entries
        if str(e.get("text") or e.get("text_or_asset") or "").strip()
    ]
    for i, (cur, _cs, ce) in enumerate(plain_rows):
        testcase.assertTrue(cur, f"{context}: empty row at {i}")
        if (
            cur[0] in step5_subtitles._BAD_SUB_HEAD_AUX
            and not cur.startswith(_AUX_HEAD_ALLOW)
            and not (cur[0] == "呢" and step5_subtitles._is_cantonese_ne_demo_head(cur))
        ):
            testcase.fail(f"{context}: dangling aux head in '{cur}'")
        if len(cur) >= 6:
            testcase.assertIsNone(
                re.search(r"(第[一二三四五六七八九十两\d]+)$", cur),
                f"{context}: trailing ordinal in '{cur}'",
            )
        if i + 1 < len(plain_rows):
            nxt, ns, _ne = plain_rows[i + 1]
            if ns - ce <= 0.45:
                testcase.assertNotIn(cur[-1], _STRICT_MID_TAILS, f"{context}: strict mid-tail in '{cur}' | '{nxt}'")
                joint = cur[-4:] + nxt[:4]
                cut_k = len(cur[-4:])
                for ms_nu, me_nu in step5_subtitles.iter_num_unit_spans(joint):
                    testcase.assertFalse(
                        ms_nu < cut_k < me_nu,
                        f"{context}: split numeral-unit '{joint[ms_nu:me_nu]}' across '{cur}' | '{nxt}'",
                    )


class V12SemanticSegmentationTests(unittest.TestCase):
    def test_isolated_jieba_does_not_leak_terms_across_calls(self) -> None:
        custom = "奇异果工坊"
        segs_with = step5_subtitles.dp_segment_short_lines(
            f"今天来探访{custom}门店升级方案",
            max_width_px=640,
            font_path=None,
            font_size=54,
            terms=[custom],
            max_chars=9,
        )
        texts_with = [t for _, _, t in segs_with]
        self.assertTrue(any(custom in t for t in texts_with), texts_with)

        toks_plain = step5_subtitles.tokenize_semantic_words(f"这里提到{custom}四个字", terms=None)
        self.assertNotIn(custom, toks_plain, f"Custom term leaked into clean tokenizer: {toks_plain}")

    def test_mandarin_talking_head_dp_segmentation_zero_dangling_zero_split(self) -> None:
        audio = _build_audio_events([
            ("不如先把一句没有用的废话删干净，画面自然就清楚了", 0.0, 4.2),
            ("少即是多，这句话在剪辑台上永远成立，也永远最难做到", 4.5, 9.2),
            ("一条三分钟的片子，我前后改过八遍才敢发出去", 9.6, 13.8),
            ("写稿录音剪辑最花时间的其实是写稿", 14.2, 17.6),
            ("终端里敲一条命令，片子和字幕就一起出来了，剩下的交给流程", 18.0, 22.8),
        ])
        for style_id in ("style_01", "style_09"):
            with tempfile.TemporaryDirectory() as td:
                pdir = Path(td)
                _write_minimal_pipeline_for_step5(pdir, audio)
                res = step5_subtitles.run_step5(pdir, template_id=style_id)
                entries = res["entries"]
                _assert_entries_clean(self, entries, f"koubo/{style_id}")
                joined = "|".join(e["text"] for e in entries)
                self.assertNotIn("一句没有|用的", joined)
                self.assertNotIn("剪辑|台上", joined)
                self.assertNotIn("最花|时间", joined)
                self.assertNotIn("出来了|的", joined)

    def test_cantonese_and_asr_rebalance_repairs_dangling_tails_without_merging_pronoun_next(self) -> None:
        raw_sents = [
            _make_word_timed_sentence("s0", "我系牛哥我", 0.0, 1.1),
            _make_word_timed_sentence("s1", "花咗两百万装修", 1.15, 2.6),
            _make_word_timed_sentence("s2", "之前系做茶餐厅嘅", 2.9, 4.3),
            _make_word_timed_sentence("s3", "佢饮茶嘅氛围好好", 4.38, 5.9),
            _make_word_timed_sentence("s4", "摆好多", 6.1, 6.7),
            _make_word_timed_sentence("s5", "卡座同包厢", 6.75, 7.9),
            _make_word_timed_sentence("s6", "𠮶边就系我哋嘅", 8.1, 9.3),
            _make_word_timed_sentence("s7", "厨房", 9.36, 10.1),
        ]
        reb = step5_subtitles.rebalance_asr_sentences(raw_sents, max_chars=9)
        reb_texts = [s["text"] for s in reb]
        self.assertEqual(reb_texts[0], "我系牛哥", reb_texts)
        self.assertTrue(reb_texts[1].startswith("我花咗"), reb_texts)
        self.assertIn("之前系做茶餐厅嘅", reb_texts)
        self.assertTrue(any(t.startswith("佢饮茶嘅") for t in reb_texts), reb_texts)
        self.assertTrue(any("摆好多卡座" in t for t in reb_texts), reb_texts)
        self.assertTrue(any("我哋嘅厨房" in t for t in reb_texts), reb_texts)

        audio = {"sentences": raw_sents, "locked_duration_sec": 10.6}
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            _write_minimal_pipeline_for_step5(pdir, audio)
            res = step5_subtitles.run_step5(pdir, template_id="style_01")
            _assert_entries_clean(self, res["entries"], "cantonese_store/style_01")
            texts = [e["text"] for e in res["entries"]]
            self.assertFalse(any(t.endswith(("我", "好多", "我哋嘅")) for t in texts[:-1]), texts)
            self.assertFalse(any("嘅佢" in t for t in texts), texts)

    def test_unseen_renovation_transcript_generalizes_without_domain_lexicon(self) -> None:
        audio = _build_audio_events([
            ("这间老店总共五百平米", 0.0, 2.2),
            ("我们只用了十五天拆掉旧墙体", 2.3, 5.0),
            ("把动线重新理顺划分三个工区", 5.1, 7.8),
            ("之前看清楚水电走位再动工", 8.1, 10.6),
            ("省下来一大笔反复返工的预算", 10.7, 13.4),
        ])
        for style_id in ("style_01", "style_09"):
            with tempfile.TemporaryDirectory() as td:
                pdir = Path(td)
                _write_minimal_pipeline_for_step5(pdir, audio)
                res = step5_subtitles.run_step5(pdir, template_id=style_id)
                _assert_entries_clean(self, res["entries"], f"unseen_reno/{style_id}")
                joined = "|".join(e["text"] for e in res["entries"])
                self.assertNotIn("五百|平米", joined)
                self.assertNotIn("十五|天", joined)
                self.assertNotIn("三个|工区", joined)

    def test_split_semantic_two_lines_never_dangles_particles(self) -> None:
        samples = [
            "因为这是你想事情的起点不是终点",
            "把动线理顺之后再开始装修厨房",
            "花咗两百万去改造呢间老茶楼",
        ]
        for s in samples:
            l1, l2 = step5_subtitles.split_semantic_two_lines(s)
            self.assertTrue(l1 and l2, (l1, l2))
            self.assertNotIn(l1[-1], "的嘅地得之了咗着过把被从向给俾在喺", (s, l1, l2))
            self.assertNotIn(l2[0], "的嘅地得之了咗着过啲", (s, l1, l2))

    def test_normalize_zh_numerals_generalizes_beyond_fixed_numbers(self) -> None:
        cases = {
            "两百平": "200平",
            "八百平": "800平",
            "十五天": "15天",
            "二十八套": "28套",
            "三千五百块": "3500块",
            "十万": "10万",
        }
        for src, expected in cases.items():
            self.assertEqual(kinetic_choreographer._normalize_zh_numerals(src), expected)

    def test_split_clean_phrases_style02_width_and_semantics(self) -> None:
        fp = kinetic_choreographer._style02_font_path()
        samples = [
            "招人总是觉得第一反应去百度文库找表格管理制度",
            "做管理不是雇人就行了是我们想事情的起点",
            "写稿录音剪辑最花时间的其实是写稿",
        ]
        for s in samples:
            chunks = kinetic_choreographer.split_clean_phrases(
                s,
                target_min=4,
                target_max=9,
                max_width_px=640,
                font_path=fp,
                font_size=69,
            )
            self.assertTrue(chunks, s)
            for ch in chunks:
                self.assertLessEqual(len(ch), 9, (s, chunks))
                pw, _ = step5_subtitles._measure_text_px(ch, fp, 69)
                self.assertLessEqual(pw, 640, (ch, pw))
                self.assertNotIn(ch[0], step5_subtitles._BAD_SUB_HEAD_AUX, (s, chunks))
                self.assertNotIn(ch[-1], _STRICT_MID_TAILS, (s, chunks))

    def test_style02_unification_no_collisions_max_5_rows_no_char_carving(self) -> None:
        # 1) 粤语探店（含 5 行清单 + 延迟首行 + 跨句助词）
        store_audio = _build_audio_events([
            ("我系牛哥我", 0.0, 0.9),
            ("花咗两百万装修", 0.95, 2.5),
            ("之前系做茶餐厅嘅", 2.8, 4.1),
            ("佢饮茶嘅氛围好好", 4.15, 5.5),
            ("我哋有四大天王虾饺排骨凤爪叉烧包", 5.8, 10.6),
            ("𠮶边就系我哋嘅", 10.9, 12.1),
            ("厨房", 12.15, 13.0),
        ])
        store_plan = {
            "step": "03b_director_plan",
            "source": "agent",
            "style_id": "style_02",
            "theme_profile": "style_02_unified",
            "acts": [
                {
                    "act_id": "act_01",
                    "sentence_ids": ["s0", "s1"],
                    "dramatic_role": "hook",
                    "emotion_intensity": 0.8,
                    "primitive": "number_burst_pair",
                    "visual_copy": {"headline": "200万", "items": ["重装翻新"], "accent_token": "200万", "footnote": ""},
                    "camera_intent": "steady_hold",
                    "broll_policy": "lock_aroll",
                    "sfx_budget": 1,
                },
                {
                    "act_id": "act_02",
                    "sentence_ids": ["s2", "s3"],
                    "dramatic_role": "bridge",
                    "emotion_intensity": 0.4,
                    "primitive": "plain_bridge",
                    "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                    "camera_intent": "steady_hold",
                    "broll_policy": "allow_broll",
                    "sfx_budget": 0,
                },
                {
                    "act_id": "act_03",
                    "sentence_ids": ["s4"],
                    "dramatic_role": "proof",
                    "emotion_intensity": 0.8,
                    "primitive": "list_build_gather_dim_overlay",
                    "visual_copy": {"headline": "四大天王", "items": ["虾饺", "排骨", "凤爪", "叉烧包"], "accent_token": "", "footnote": ""},
                    "camera_intent": "steady_hold",
                    "broll_policy": "lock_aroll",
                    "sfx_budget": 1,
                },
                {
                    "act_id": "act_04",
                    "sentence_ids": ["s5", "s6"],
                    "dramatic_role": "explain",
                    "emotion_intensity": 0.6,
                    "primitive": "pain_slam_danmaku",
                    "visual_copy": {"headline": "厨房", "items": [], "accent_token": "", "footnote": ""},
                    "camera_intent": "steady_hold",
                    "broll_policy": "lock_aroll",
                    "sfx_budget": 1,
                },
            ],
            "music_cues": [],
        }
        # 2) 商业管理（含 stacked_dual_color 短标题延迟 + "是我们想事情的起点"）
        biz_audio = _build_audio_events([
            ("做管理看到做法先找他的前提", 0.0, 4.0),
            ("这不是雇人就行了是我们想事情的起点", 4.2, 8.6),
            ("招人总是觉得第一反应去百度文库找表格", 8.9, 13.0),
        ])
        biz_plan = {
            "step": "03b_director_plan",
            "source": "agent",
            "style_id": "style_02",
            "theme_profile": "style_02_unified",
            "acts": [
                {
                    "act_id": "act_01",
                    "sentence_ids": ["s0"],
                    "dramatic_role": "explain",
                    "emotion_intensity": 0.7,
                    "primitive": "stacked_dual_color",
                    "visual_copy": {"headline": "做管理", "items": ["先找他的前提"], "accent_token": "", "footnote": ""},
                    "camera_intent": "steady_hold",
                    "broll_policy": "lock_aroll",
                    "sfx_budget": 1,
                },
                {
                    "act_id": "act_02",
                    "sentence_ids": ["s1"],
                    "dramatic_role": "explain",
                    "emotion_intensity": 0.7,
                    "primitive": "pain_slam_danmaku",
                    "visual_copy": {"headline": "不是雇人", "items": ["起点"], "accent_token": "", "footnote": ""},
                    "camera_intent": "steady_hold",
                    "broll_policy": "lock_aroll",
                    "sfx_budget": 1,
                },
                {
                    "act_id": "act_03",
                    "sentence_ids": ["s2"],
                    "dramatic_role": "bridge",
                    "emotion_intensity": 0.4,
                    "primitive": "plain_bridge",
                    "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                    "camera_intent": "steady_hold",
                    "broll_policy": "allow_broll",
                    "sfx_budget": 0,
                },
            ],
            "music_cues": [],
        }
        # 3) 未见过装修文本（自动总导演计划）
        reno_audio = _build_audio_events([
            ("这间老店总共五百平米", 0.0, 2.2),
            ("我们只用了十五天拆掉旧墙体划分三个工区", 2.4, 6.2),
            ("省下来一大笔反复返工的预算", 6.5, 9.5),
        ])
        reno_plan = director_plan.generate_deterministic_director_plan(reno_audio, "style_02")

        for name, audio_d, plan_d in (
            ("store", store_audio, store_plan),
            ("biz", biz_audio, biz_plan),
            ("reno", reno_audio, reno_plan),
        ):
            stage = kinetic_choreographer.plan_kinetic_stage(audio_d, style_id="style_02", director_plan=plan_d)
            fg = [
                it for it in stage["items"]
                if it.get("z_layer", "foreground") == "foreground"
                and it.get("variant") not in {"editorial_ticker", "pillar_badge"}
                and str(it.get("text_or_asset") or "").strip()
            ]
            # 1) 零同Y/同X前景碰撞
            for i, a in enumerate(fg):
                for b in fg[i + 1 :]:
                    ov = min(float(a["end_sec"]), float(b["end_sec"])) - max(float(a["start_sec"]), float(b["start_sec"]))
                    if ov > 0.05 and abs(float(a["x_norm"]) - float(b["x_norm"])) < 0.25:
                        self.assertGreaterEqual(
                            abs(float(a["y_norm"]) - float(b["y_norm"])),
                            0.06,
                            f"{name}: foreground overlap {a['text_or_asset']} vs {b['text_or_asset']} ({ov:.2f}s)",
                        )
            # 2) 同时在屏 <= 5 行
            hi = [it for it in fg if it.get("variant") != "bridge_bar"]
            pts = sorted({float(it["start_sec"]) for it in hi} | {float(it["end_sec"]) for it in hi})
            for t0, t1 in zip(pts, pts[1:]):
                if t1 - t0 > 0.04:
                    mid = 0.5 * (t0 + t1)
                    cnt = sum(1 for it in hi if float(it["start_sec"]) <= mid <= float(it["end_sec"]))
                    self.assertLessEqual(cnt, 5, f"{name}: >5 simultaneous foreground items ({cnt}) at {mid:.2f}s")
            # 3) bridge_bar 全量断句干净且无残片硬切
            bbs = sorted([it for it in fg if it.get("variant") == "bridge_bar"], key=lambda x: float(x["start_sec"]))
            _assert_entries_clean(self, bbs, f"style02_bb/{name}")
            joined_all = "|".join(str(it.get("text_or_asset") or "") for it in fg)
            self.assertNotIn("我哋嘅四大天王虾饺", joined_all)
            self.assertNotIn("我哋嘅厨|", joined_all)
            self.assertNotIn("是我们想事情的|", joined_all)
            for bb in bbs:
                self.assertLessEqual(len(str(bb["text_or_asset"])), 9, bb)

    def test_qa_inspector_universal_guards_reject_bad_and_pass_clean(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "03_audio_events.json").write_text(
                json.dumps({"sentences": [{"id": "s0", "text": "测试", "start_sec": 0.0, "end_sec": 4.0}], "locked_duration_sec": 4.5}),
                encoding="utf-8",
            )
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{
                        "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                        "timeline_in_sec": 0.0, "timeline_out_sec": 4.0, "duration_sec": 4.0,
                        "source_in_sec": 0.0, "source_out_sec": 4.0,
                    }],
                    "fps": 30, "total_duration_sec": 4.0,
                }),
                encoding="utf-8",
            )
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")

            fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

            # Case 1: 坏分词（动貌挂尾「花咗｜两百万」、拆数词「十五｜平米」、助词行首「的废话」）+ 同位前景碰撞 -> 必 FAIL
            (pdir / "05_subtitles.json").write_text(
                json.dumps({
                    "entries": [
                        {"id": "sub1", "text": "我花咗", "start_sec": 0.1, "end_sec": 1.0, "suppressed": False},
                        {"id": "sub2", "text": "两百万去装修", "start_sec": 1.05, "end_sec": 2.0, "suppressed": False},
                        {"id": "sub3", "text": "大概十五", "start_sec": 2.1, "end_sec": 2.8, "suppressed": False},
                        {"id": "sub4", "text": "平米左右", "start_sec": 2.85, "end_sec": 3.5, "suppressed": False},
                        {"id": "sub5", "text": "的废话删掉", "start_sec": 3.55, "end_sec": 4.0, "suppressed": False},
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(
                json.dumps({
                    "kinetic_stage": {
                        "enabled": True,
                        "theme_profile": "style_02_unified",
                        "items": [
                            {"id": "k1", "group_id": "v6_01", "variant": "hero_white", "z_layer": "foreground", "text_or_asset": "先导行", "start_sec": 0.2, "end_sec": 2.2, "x_norm": 0.0, "y_norm": -0.25},
                            {"id": "k2", "group_id": "v6_01", "variant": "hero_lime", "z_layer": "foreground", "text_or_asset": "撞同行", "start_sec": 1.0, "end_sec": 3.0, "x_norm": 0.0, "y_norm": -0.25},
                        ],
                    }
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                qa_bad = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            bad_by_id = {c["id"]: c for c in qa_bad["checks"]}
            self.assertEqual(bad_by_id["subtitle_segmentation_clean"]["status"], "FAIL", bad_by_id["subtitle_segmentation_clean"])
            self.assertEqual(bad_by_id["overlay_cross_act_overlap"]["status"], "FAIL", bad_by_id["overlay_cross_act_overlap"])

            # Case 2: 干净分词（含合法「之前看清楚」、句末「嘅」+「佢」、名物化「最花时间的」+「其实是写稿」）+ 无碰撞舞台 -> 必 PASS
            (pdir / "05_subtitles.json").write_text(
                json.dumps({
                    "entries": [
                        {"id": "sub1", "text": "之前系做茶餐厅嘅", "start_sec": 0.1, "end_sec": 1.2, "suppressed": False},
                        {"id": "sub2", "text": "佢饮茶嘅氛围好好", "start_sec": 1.25, "end_sec": 2.3, "suppressed": False},
                        {"id": "sub3", "text": "写稿最花时间的", "start_sec": 2.4, "end_sec": 3.2, "suppressed": False},
                        {"id": "sub4", "text": "其实是写稿", "start_sec": 3.25, "end_sec": 4.0, "suppressed": False},
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(
                json.dumps({
                    "kinetic_stage": {
                        "enabled": True,
                        "theme_profile": "style_02_unified",
                        "items": [
                            {"id": "k1", "group_id": "v6_01", "variant": "hero_lime", "z_layer": "foreground", "text_or_asset": "四大天王", "start_sec": 0.1, "end_sec": 4.0, "x_norm": 0.0, "y_norm": -0.115},
                            {"id": "k2", "group_id": "v6_01", "variant": "hero_white", "z_layer": "foreground", "text_or_asset": "虾饺", "start_sec": 0.5, "end_sec": 4.0, "x_norm": 0.0, "y_norm": -0.205},
                            {"id": "k3", "group_id": "v6_01", "variant": "hero_white", "z_layer": "foreground", "text_or_asset": "排骨", "start_sec": 1.2, "end_sec": 4.0, "x_norm": 0.0, "y_norm": -0.289},
                            {"id": "k4", "group_id": "v6_01", "variant": "hero_white", "z_layer": "foreground", "text_or_asset": "凤爪", "start_sec": 2.0, "end_sec": 4.0, "x_norm": 0.0, "y_norm": -0.373},
                            {"id": "k5", "group_id": "v6_01", "variant": "hero_white", "z_layer": "foreground", "text_or_asset": "叉烧包", "start_sec": 2.8, "end_sec": 4.0, "x_norm": 0.0, "y_norm": -0.457},
                        ],
                    }
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                qa_ok = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            ok_by_id = {c["id"]: c for c in qa_ok["checks"]}
            self.assertEqual(ok_by_id["subtitle_segmentation_clean"]["status"], "PASS", ok_by_id["subtitle_segmentation_clean"])
            self.assertEqual(ok_by_id["overlay_cross_act_overlap"]["status"], "PASS", ok_by_id["overlay_cross_act_overlap"])

            # Case 3: style_02_unified 同时在屏 >5 行前景花字 -> 必 FAIL overlay_cross_act_overlap
            (pdir / "06_packaging.json").write_text(
                json.dumps({
                    "kinetic_stage": {
                        "enabled": True,
                        "theme_profile": "style_02_unified",
                        "items": [
                            {"id": f"r{i}", "group_id": "v6_01", "variant": "hero_white", "z_layer": "foreground", "text_or_asset": f"第{i}条", "start_sec": 0.2, "end_sec": 3.5, "x_norm": 0.0, "y_norm": round(-0.05 - i * 0.08, 3)}
                            for i in range(6)
                        ],
                    }
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                qa_six = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            six_by_id = {c["id"]: c for c in qa_six["checks"]}
            self.assertEqual(six_by_id["overlay_cross_act_overlap"]["status"], "FAIL", six_by_id["overlay_cross_act_overlap"])

    def test_iteration2_sto_aspect_and_cantonese_ne_demonstratives_pass_qa(self) -> None:
        audio = _build_audio_events([
            ("我系牛哥我", 0.0, 0.9),
            ("花咗三百万喺金湾开咗一间六百平嘅点赞茶楼", 0.95, 4.6),
            ("呢度有二十四个恒温海鲜池", 4.9, 7.6),
            ("每日凌晨两点半从渔港直接运过来", 7.8, 10.8),
            ("每日朝早五点就要开始整", 11.1, 13.4),
            ("呢批用法国牛油做嘅牛角包最受欢迎", 13.48, 16.8),
            ("为什么会这样呢？", 17.1, 18.6),
            ("其实原因很简单。", 19.0, 20.6),
        ])
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
        fp02 = kinetic_choreographer._style02_font_path()
        for sid in ("style_01", "style_09", "style_02"):
            with tempfile.TemporaryDirectory() as td:
                pdir = Path(td)
                vid = pdir / "out.mp4"
                _make_dummy_clip(vid, 0.4)
                _write_minimal_pipeline_for_step5(pdir, audio)
                (pdir / "04_edl.json").write_text(
                    json.dumps({
                        "clips": [{
                            "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                            "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                            "duration_sec": audio["locked_duration_sec"],
                            "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                        }],
                        "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                res = step5_subtitles.run_step5(pdir, template_id=sid)
                joined = "|".join(e["text"] for e in res["entries"])
                self.assertNotIn("开咗|一间", joined, (sid, joined))
                self.assertNotIn("就要开始整呢", joined, (sid, joined))
                self.assertNotIn("两点|半", joined, (sid, joined))
                pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                if sid == "style_02":
                    plan = director_plan.generate_deterministic_director_plan(audio, "style_02")
                    stage = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                    pkg["kinetic_stage"] = stage
                    for it in stage["items"]:
                        if it.get("variant") == "bridge_bar":
                            pw, _ = step5_subtitles._measure_text_px(str(it["text_or_asset"]), fp02, 69)
                            self.assertLessEqual(pw, 640, (it["text_or_asset"], pw))
                (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                by_id = {c["id"]: c for c in rep["checks"]}
                self.assertEqual(by_id["subtitle_segmentation_clean"]["status"], "PASS", (sid, by_id["subtitle_segmentation_clean"]))
                self.assertEqual(by_id["overlay_cross_act_overlap"]["status"], "PASS", (sid, by_id["overlay_cross_act_overlap"]))

    def test_iteration2_acoustic_pauses_units_compounds_and_mandarin_modifiers(self) -> None:
        def _wt_with_gaps(sid: str, text: str, gaps: Dict[int, float]) -> Dict[str, Any]:
            words = []
            cur = 0.0
            for idx, ch in enumerate(text):
                words.append({"word": ch, "start_sec": round(cur, 3), "end_sec": round(cur + 0.14, 3)})
                cur += 0.14 + gaps.get(idx, 0.0)
            return {"id": sid, "text": text, "start_sec": 0.0, "end_sec": round(cur, 3), "words": words}

        # 1) Acoustic pauses (0.23s–0.54s) respected in step5_subtitles
        s_k8 = _wt_with_gaps("k8", "之前检查三件事开头有没有钩子节奏有没有塌结尾有没有动作", {6: 0.43, 13: 0.33, 19: 0.23})
        c_k8 = [c["text"] for c in step5_subtitles.split_sentence_into_chunks(s_k8, max_chars=10, font_size=54, max_safe_px=632)]
        self.assertEqual(c_k8, ["之前检查三件事", "开头有没有钩子", "节奏有没有塌", "结尾有没有动作"], c_k8)

        s_k11 = _wt_with_gaps("k11", "先做出来再改别等一个完美的版本才开始完美的版本不会来", {5: 0.49, 17: 0.42})
        c_k11 = [c["text"] for c in step5_subtitles.split_sentence_into_chunks(s_k11, max_chars=10, font_size=54, max_safe_px=632)]
        self.assertIn("先做出来再改", c_k11, c_k11)
        self.assertFalse(any("再改别等" in t for t in c_k11), c_k11)

        s_b27 = _wt_with_gaps("b27", "两个人干分工计酬监督天然就有一雇人三样全碎了", {13: 0.54, 16: 0.25})
        c_b27 = [c["text"] for c in step5_subtitles.split_sentence_into_chunks(s_b27, max_chars=10, font_size=54, max_safe_px=632)]
        self.assertTrue(any(t.endswith("天然就有") for t in c_b27), c_b27)
        self.assertFalse(any("有一雇" in t for t in c_b27), c_b27)

        # 2) Units, compound protection, and _coalesce_pos_pairs fix
        self.assertEqual(step5_subtitles.tokenize_semantic_words("二十八天省下百分之十五"), ["二十八天", "省下", "百分之十五"])
        for txt, bad_split in (
            ("做成三千五百元一平的微水泥开放式", "微|水泥"),
            ("我们店二零二五年升级了六套德国进口的舒适化洁牙设备", "升级了|六套"),
            ("我们店二零二五年升级了六套德国进口的舒适化洁牙设备", "舒适|化"),
            ("系统响应时间从三千二百毫秒降到四十五毫秒", "三千二百|毫秒"),
            ("每日凌晨两点半从渔港直接", "两点|半"),
            ("是我从百度文库下载了一份装修公司管理制度", "装修|公司"),
        ):
            dp = [t for _, _, t in step5_subtitles.dp_segment_short_lines(txt, 640, font_size=54, max_chars=9)]
            self.assertNotIn(bad_split, "|".join(dp), (txt, dp))

        # 3) Mandarin modifiers & comma-crossing 1-char pronoun
        dp_zhege = [t for _, _, t in step5_subtitles.dp_segment_short_lines("我觉得这个方案特别值得考虑", 640, font_size=54, max_chars=9)]
        self.assertFalse(any(t.endswith("这个") for t in dp_zhege[:-1]), dp_zhege)
        dp_zui = [t for _, _, t in step5_subtitles.dp_segment_short_lines("这里面最核心的第一条建议你必须记住", 640, font_size=54, max_chars=9)]
        self.assertFalse(any(t.endswith("最") for t in dp_zui[:-1]), dp_zui)
        dp_bu = [t for _, _, t in step5_subtitles.dp_segment_short_lines("你如果不把这件事情的底层逻辑想清楚", 640, font_size=54, max_chars=9)]
        self.assertFalse(any(t.endswith("不") for t in dp_bu[:-1]), dp_bu)

        dp_c1 = [t for _, _, t in step5_subtitles.dp_segment_short_lines("之前只系做街坊生意嘅，佢每朝早嘅香气真系好正", 640, font_size=69, max_chars=9)]
        self.assertFalse(any(t.endswith(("嘅 佢", "嘅佢")) for t in dp_c1), dp_c1)
        dp_c2 = [t for _, _, t in step5_subtitles.dp_segment_short_lines("以前一直做小本生意的，他每天早上六点开门", 640, font_size=69, max_chars=9)]
        self.assertFalse(any(t.endswith(("的 他", "的他")) for t in dp_c2), dp_c2)

    def test_iteration2_style02_collision_cross_sentence_width_and_legacy_sto(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        # 1) stacked_dual_color snap collision fix
        ae_col = {"locked_duration_sec": 5.0, "sentences": [_make_word_timed_sentence("s0", "做管理肯定得找准好前提条件", 0.0, 4.0)]}
        plan_col = {
            "step": "03b_director_plan", "source": "agent", "style_id": "style_02", "theme_profile": "style_02_unified",
            "acts": [{"act_id": "act_01", "sentence_ids": ["s0"], "dramatic_role": "explain", "emotion_intensity": 0.7,
                      "primitive": "stacked_dual_color", "visual_copy": {"headline": "做管理", "items": ["前提条件"]},
                      "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 1}],
            "music_cues": [],
        }
        st_col = kinetic_choreographer.plan_kinetic_stage(ae_col, style_id="style_02", director_plan=plan_col)
        fg_col = [it for it in st_col["items"] if it.get("z_layer", "foreground") == "foreground" and it.get("variant") != "bridge_bar"]
        for i, a in enumerate(fg_col):
            for b in fg_col[i + 1 :]:
                ov = min(float(a["end_sec"]), float(b["end_sec"])) - max(float(a["start_sec"]), float(b["start_sec"]))
                if ov > 0.05 and abs(float(a["x_norm"]) - float(b["x_norm"])) < 0.45:
                    self.assertGreaterEqual(abs(float(a["y_norm"]) - float(b["y_norm"])), 0.06, (a, b, ov))

        # 2) No cross-sentence "耐用 瓷砖胶" and strict bridge_bar <= 640px
        ae_cross = {
            "locked_duration_sec": 6.0,
            "sentences": [
                _make_word_timed_sentence("s0", "选对材料才能防潮耐用", 0.0, 2.8),
                _make_word_timed_sentence("s1", "瓷砖胶千万别掺水泥", 2.9, 5.5),
            ],
        }
        plan_cross = {
            "step": "03b_director_plan", "source": "agent", "style_id": "style_02", "theme_profile": "style_02_unified",
            "acts": [{"act_id": "act_01", "sentence_ids": ["s0", "s1"], "dramatic_role": "explain", "emotion_intensity": 0.7,
                      "primitive": "stacked_dual_color", "visual_copy": {"headline": "选对", "items": ["防潮耐用"]},
                      "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 1}],
            "music_cues": [],
        }
        st_cross = kinetic_choreographer.plan_kinetic_stage(ae_cross, style_id="style_02", director_plan=plan_cross)
        for it in st_cross["items"]:
            self.assertNotIn("耐用 瓷砖胶", str(it.get("text_or_asset") or ""))
            if it.get("variant") == "bridge_bar":
                pw, _ = step5_subtitles._measure_text_px(str(it["text_or_asset"]), fp02, 69)
                self.assertLessEqual(pw, 640, (it["text_or_asset"], pw))

        # 3) Legacy plan_kinetic_stage(director_plan=None) on STO-like segmented speech
        sto_like = {
            "locked_duration_sec": 12.0,
            "sentences": [
                _make_word_timed_sentence("s0", "我系牛哥我", 0.0, 0.9),
                _make_word_timed_sentence("s1", "花咗三百万喺金湾开咗一间六百平嘅点赞茶楼", 0.95, 4.2),
                _make_word_timed_sentence("s2", "𠮶边", 4.5, 4.9),
                _make_word_timed_sentence("s3", "就系我哋嘅", 4.92, 5.5),
                _make_word_timed_sentence("s4", "鱼池到时就有好多新鲜嘅海鲜", 5.52, 8.8),
                _make_word_timed_sentence("s5", "欢迎大家来点赞茶楼", 9.0, 11.5),
            ],
        }
        leg = kinetic_choreographer.plan_kinetic_stage(sto_like, style_id="style_02")
        for it in leg["items"]:
            if it.get("variant") == "bridge_bar":
                txt = str(it.get("text_or_asset") or "")
                self.assertNotIn(" ", txt, txt)
                self.assertFalse(txt.endswith("我哋嘅"), txt)
                pw, _ = step5_subtitles._measure_text_px(txt, fp02, 69)
                self.assertLessEqual(pw, 640, (txt, pw))

    def test_iteration2_negative_qa_cases_fail(self) -> None:
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "03_audio_events.json").write_text(
                json.dumps({"sentences": [{"id": "s0", "text": "测试", "start_sec": 0.0, "end_sec": 4.0}], "locked_duration_sec": 4.5}),
                encoding="utf-8",
            )
            (pdir / "04_edl.json").write_text(
                json.dumps({"clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 4.0, "duration_sec": 4.0, "source_in_sec": 0.0, "source_out_sec": 4.0}], "fps": 30, "total_duration_sec": 4.0}),
                encoding="utf-8",
            )
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")

            neg_cases = [
                # 1) dangling_wo_pron_tail
                (
                    [{"id": "sub1", "sentence_id": "s0", "text": "我系阿明我", "start_sec": 0.0, "end_sec": 1.0, "suppressed": False},
                     {"id": "sub2", "sentence_id": "s1", "text": "花咗八十万装修", "start_sec": 1.05, "end_sec": 2.5, "suppressed": False}],
                    [],
                    "subtitle_segmentation_clean",
                ),
                # 2) dangling_haoduo_tail at 0.65s
                (
                    [{"id": "sub1", "sentence_id": "s0", "text": "店里面摆好多", "start_sec": 0.0, "end_sec": 1.2, "suppressed": False},
                     {"id": "sub2", "sentence_id": "s1", "text": "法式羊角面包", "start_sec": 1.85, "end_sec": 3.2, "suppressed": False}],
                    [],
                    "subtitle_segmentation_clean",
                ),
                # 3) same_y_diff_x_overlap at x=±0.16
                (
                    [{"id": "sub1", "sentence_id": "s0", "text": "传统烤瓷牙很厚", "start_sec": 0.0, "end_sec": 2.0, "suppressed": True, "suppress_reason": "kinetic_typography_stage"}],
                    [{"id": "k1", "group_id": "v6_01", "variant": "hero_white", "z_layer": "foreground", "text_or_asset": "传统烤瓷牙很厚", "start_sec": 0.1, "end_sec": 2.2, "x_norm": -0.16, "y_norm": -0.25},
                     {"id": "k2", "group_id": "v6_01", "variant": "hero_lime", "z_layer": "foreground", "text_or_asset": "全瓷贴面更自然", "start_sec": 0.4, "end_sec": 2.5, "x_norm": 0.16, "y_norm": -0.25}],
                    "overlay_cross_act_overlap",
                ),
            ]
            for entries, ks_items, expect_fail_id in neg_cases:
                (pdir / "05_subtitles.json").write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
                (pdir / "06_packaging.json").write_text(
                    json.dumps({"kinetic_stage": {"enabled": bool(ks_items), "theme_profile": "style_02_unified", "items": ks_items}}, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                by_id = {c["id"]: c for c in rep["checks"]}
                self.assertEqual(by_id[expect_fail_id]["status"], "FAIL", (expect_fail_id, by_id[expect_fail_id]))

    def test_iteration3_mandarin_ne_question_vs_cantonese_split(self) -> None:
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")

            # Positive cases: Mandarin rhetorical "呢？" followed by noun / time / reason
            pos_pairs = [
                ("为什么会这样呢？", "问题出在流程上。"),
                ("这到底是为什么呢？", "今天我们一起来拆解。"),
                ("难道真的是这样呢？", "原因其实很复杂。"),
            ]
            for s_a, s_b in pos_pairs:
                audio = _build_audio_events([(s_a, 0.0, 1.5), (s_b, 1.9, 3.6)])
                _write_minimal_pipeline_for_step5(pdir, audio)
                (pdir / "04_edl.json").write_text(
                    json.dumps({
                        "clips": [{
                            "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                            "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                            "duration_sec": audio["locked_duration_sec"],
                            "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                        }],
                        "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                step5_subtitles.run_step5(pdir, template_id="style_01")
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                by_id = {c["id"]: c for c in rep["checks"]}
                self.assertEqual(by_id["subtitle_segmentation_clean"]["status"], "PASS", (s_a, s_b, by_id["subtitle_segmentation_clean"]))

            # Negative case: real Cantonese demonstrative split "整呢" | "批用法国牛油做嘅牛角包"
            (pdir / "03_audio_events.json").write_text(
                json.dumps({
                    "sentences": [
                        {"id": "s0", "text": "每日朝早五点就要开始整", "start_sec": 0.0, "end_sec": 1.5},
                        {"id": "s1", "text": "呢批用法国牛油做嘅牛角包", "start_sec": 1.58, "end_sec": 3.2},
                    ],
                    "locked_duration_sec": 3.5,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (pdir / "05_subtitles.json").write_text(
                json.dumps({
                    "entries": [
                        {"id": "sub1", "sentence_id": "s0", "text": "每日朝早五点就要开始整呢", "start_sec": 0.0, "end_sec": 1.5, "suppressed": False},
                        {"id": "sub2", "sentence_id": "s1", "text": "批用法国牛油做嘅牛角包", "start_sec": 1.58, "end_sec": 3.2, "suppressed": False},
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                rep_bad = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            bad_by_id = {c["id"]: c for c in rep_bad["checks"]}
            self.assertEqual(bad_by_id["subtitle_segmentation_clean"]["status"], "FAIL", bad_by_id["subtitle_segmentation_clean"])

    def test_iteration3_inviolability_lattice_no_splits_and_units(self) -> None:
        # 1) 从一开始 & 这件事情 & 三点一刻
        s_congyi = [t for _, _, t in step5_subtitles.dp_segment_short_lines("其实最怕的不是慢而是方向从一开始就错了", 632, max_chars=9)]
        self.assertNotIn("从一|开始", "|".join(s_congyi), s_congyi)
        self.assertFalse(any(t.endswith("从一") for t in s_congyi), s_congyi)

        s_zhejian = [t for _, _, t in step5_subtitles.dp_segment_short_lines("你如果不把这件事情的底层逻辑想清楚", 632, max_chars=9)]
        self.assertNotIn("这件|事情", "|".join(s_zhejian), s_zhejian)
        self.assertFalse(any(t.endswith("这件") for t in s_zhejian), s_zhejian)
        self.assertFalse(any(len(t) <= 3 for t in s_zhejian), s_zhejian)

        s_kefen = [t for _, _, t in step5_subtitles.dp_segment_short_lines("每天三点一刻必须完成九百八十万货款确认", 632, max_chars=9)]
        self.assertNotIn("三点|一刻", "|".join(s_kefen), s_kefen)
        self.assertTrue(any("三点一刻" in t for t in s_kefen), s_kefen)
        s_11kefen = [t for _, _, t in step5_subtitles.dp_segment_short_lines("每天十一点一刻前完成对账", 632, max_chars=9)]
        self.assertTrue(any("十一点一刻" in t for t in s_11kefen), s_11kefen)
        # "这一刻" 不应被数字量词误匹配成原子数词
        self.assertEqual(
            [m.group(0) for m in step5_subtitles._NUM_UNIT_PRESPLIT_RE.finditer("这一刻我们终于等到了")],
            [],
        )

        # 2) 蛋挞 & 干炒牛河 & 还有种
        dp_danta = [t for _, _, t in step5_subtitles.dp_segment_short_lines("呢批新出炉嘅蛋挞香到不得了", 632, max_chars=9)]
        self.assertNotIn("蛋|挞", "|".join(dp_danta), dp_danta)
        dp_niuhe = [t for _, _, t in step5_subtitles.dp_segment_short_lines("呢罐淡奶同呢碟干炒牛河最夹", 632, max_chars=9)]
        self.assertNotIn("干|炒牛河", "|".join(dp_niuhe), dp_niuhe)

        self.assertNotIn("还有种", step5_subtitles._UNIVERSAL_SPOKEN_LEXICON)
        pos_hz = step5_subtitles._pseg_cut_clean("还有种特别的做法")
        self.assertFalse(any(w == "还有种" for w, _ in pos_hz), pos_hz)

    def test_iteration3_legal_and_reno_corpora_pass_all_styles_and_adaptive_bridge(self) -> None:
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
        fp02 = kinetic_choreographer._style02_font_path()

        legal_audio = _build_audio_events([
            ("我们帮客户追回了九百八十万的货款损失", 0.0, 3.8),
            ("其实是因为这个补充协议里面有漏洞", 4.1, 7.5),
            ("每天三点一刻必须完成九百八十万货款确认", 7.8, 11.5),
            ("其实最怕的不是慢而是方向从一开始就错了", 11.8, 15.5),
            ("你如果不把这件事情的底层逻辑想清楚", 15.8, 19.2),
        ])
        reno_audio = _build_audio_events([
            ("这间老店总共五百平米做成了微水泥开放式", 0.0, 4.0),
            ("我们花了二十八天省下百分之十五的材料损耗", 4.3, 8.2),
            ("省下来一大笔反复返工的预算", 8.5, 11.5),
        ])

        for cname, audio in (("LEGAL_CONTRACT", legal_audio), ("RENO_MANDARIN", reno_audio)):
            for sid in ("style_01", "style_09", "style_02"):
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid)
                    sub_joined = "|".join(e["text"] for e in res["entries"])
                    self.assertNotIn("其实是因为这个|", sub_joined, (cname, sid, sub_joined))
                    self.assertNotIn("从一|开始", sub_joined, (cname, sid, sub_joined))
                    self.assertNotIn("这件|事情", sub_joined, (cname, sid, sub_joined))

                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = director_plan.generate_deterministic_director_plan(audio, "style_02")
                        stage = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = stage
                        for it in stage["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt = str(it.get("text_or_asset") or "")
                                fs = int(it.get("font_size_px") or 69)
                                self.assertGreaterEqual(fs, 60, (cname, txt, fs))
                                pw, _ = step5_subtitles._measure_text_px(txt, fp02, fs)
                                self.assertLessEqual(pw, 640, (cname, txt, fs, pw))
                                im = render_template_overlays.render_kinetic_stage_item_png(
                                    it, font_candidates=it.get("font_candidates"),
                                )
                                if im is not None:
                                    self.assertLessEqual(im.width, 640, (cname, txt, im.width))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, sid, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, by_id["style_typography_unity"]))

        # Adaptive 60px (10.0u) fallback handles unavoidable 10-char Num+的+NP cleanly
        u_10 = kinetic_choreographer._bridge_units_for_text("百分之十五的材料损耗", fp02)
        self.assertGreaterEqual(u_10, 10.0)
        pw_10, _ = step5_subtitles._measure_text_px("百分之十五的材料损耗", fp02, int(round(u_10 * 6)))
        self.assertLessEqual(pw_10, 640, (u_10, pw_10))

    def test_iteration3_koubo_pause_comma_and_legacy_all_branches(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) KOUBO sent_011 style_02 bridge_bar respects 0.49s pause (never fuses "先做出来再改别等")
        words_k11 = []
        cur_t = 0.0
        txt_k11 = "先做出来再改别等一个完美的版本才开始完美的版本不会来"
        gaps_k11 = {5: 0.49, 17: 0.42}
        for idx, ch in enumerate(txt_k11):
            words_k11.append({"word": ch, "start_sec": round(cur_t, 3), "end_sec": round(cur_t + 0.14, 3)})
            cur_t += 0.14 + gaps_k11.get(idx, 0.0)
        ae_k11 = {
            "sentences": [{"id": "s0", "text": txt_k11, "start_sec": 0.0, "end_sec": round(cur_t, 3), "words": words_k11}],
            "locked_duration_sec": round(cur_t + 0.5, 3),
        }
        plan_k11 = {
            "step": "03b_director_plan", "source": "agent", "style_id": "style_02", "theme_profile": "style_02_unified",
            "acts": [{"act_id": "act_01", "sentence_ids": ["s0"], "dramatic_role": "bridge", "emotion_intensity": 0.4,
                      "primitive": "plain_bridge", "visual_copy": {"headline": "", "items": []},
                      "camera_intent": "steady_hold", "broll_policy": "allow_broll", "sfx_budget": 0}],
            "music_cues": [],
        }
        st_k11 = kinetic_choreographer.plan_kinetic_stage(ae_k11, style_id="style_02", director_plan=plan_k11)
        bb_k11 = [str(it.get("text_or_asset") or "") for it in st_k11["items"] if it.get("variant") == "bridge_bar"]
        self.assertIn("先做出来再改", bb_k11, bb_k11)
        self.assertFalse(any("再改别等" in t for t in bb_k11), bb_k11)

        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            _write_minimal_pipeline_for_step5(pdir, ae_k11)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{
                        "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                        "timeline_in_sec": 0.0, "timeline_out_sec": ae_k11["locked_duration_sec"],
                        "duration_sec": ae_k11["locked_duration_sec"],
                        "source_in_sec": 0.0, "source_out_sec": ae_k11["locked_duration_sec"],
                    }],
                    "fps": 30, "total_duration_sec": ae_k11["locked_duration_sec"],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            step5_subtitles.run_step5(pdir, template_id="style_02")
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_02", "sfx_cues": [], "kinetic_stage": st_k11}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                rep_k11 = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            by_id_k11 = {c["id"]: c for c in rep_k11["checks"]}
            self.assertEqual(by_id_k11["subtitle_segmentation_clean"]["status"], "PASS", by_id_k11["subtitle_segmentation_clean"])

        # 2) Comma-bounded 2-char clause ("不动，她其实") never fuses across comma
        ph_fit = kinetic_choreographer.split_clean_phrases(
            "很多人卡在平台期不动，她其实只是睡眠不足",
            target_min=4, target_max=9, max_width_px=640, font_path=fp02, font_size=69, strict_width=True,
        )
        self.assertFalse(any("不动 她其实" in p or "不动她其实" in p for p in ph_fit), ph_fit)

        # 3) Legacy plan_kinetic_stage(director_plan=None) on BIZ-like & STO-like multi-branch speech
        biz_legacy = _build_audio_events([
            ("公司破产那年我负债200多万", 0.0, 2.5),
            ("之前一直觉得是员工不够努力", 2.8, 5.2),
            ("现在才发现是老板没给前提", 5.5, 8.0),
            ("第一", 8.4, 8.9),
            ("先答清楚", 9.0, 9.8),
            ("两个字", 10.0, 10.6),
            ("是师傅", 10.7, 11.4),
            ("欢迎大家评论区留言告诉我", 11.8, 14.5),
        ])
        sto_legacy = _build_audio_events([
            ("我系牛哥", 0.0, 0.9),
            ("花咗三百万喺金湾开咗一间六百平嘅点赞茶楼", 1.0, 4.2),
            ("𠮶边", 4.5, 4.9),
            ("就系我哋嘅", 4.92, 5.5),
            ("厨房", 5.52, 6.2),
            ("欢迎大家评论区留言找我", 6.5, 9.2),
        ])
        for name, ae_leg in (("biz_legacy", biz_legacy), ("sto_legacy", sto_legacy)):
            leg = kinetic_choreographer.plan_kinetic_stage(ae_leg, style_id="style_02", director_plan=None)
            for it in leg["items"]:
                if it.get("variant") == "bridge_bar":
                    txt = str(it.get("text_or_asset") or "")
                    fs = int(it.get("font_size_px") or 69)
                    self.assertNotIn(" ", txt, (name, txt))
                    self.assertGreaterEqual(fs, 60, (name, txt, fs))
                    pw, _ = step5_subtitles._measure_text_px(txt, fp02, fs)
                    self.assertLessEqual(pw, 640, (name, txt, fs, pw))
                    im = render_template_overlays.render_kinetic_stage_item_png(
                        it, font_candidates=it.get("font_candidates"),
                    )
                    if im is not None:
                        self.assertLessEqual(im.width, 640, (name, txt, im.width))

    def test_iteration4_mandarin_ne_topic_rhetorical_and_pronoun_boundary_qa(self) -> None:
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")

            # 1) Single-sentence Mandarin topic "呢，..." and rhetorical "呢？..." must PASS
            for sent_txt in (
                "说实话呢，这套方法确实省事不少",
                "至于预算呢，大家提前留好两成余量",
                "这个流程呢，其实很简单，大家按步骤做就行。",
                "这样行不行呢？问题就出在第一步",
                "对不对呢？其实很简单。",
            ):
                audio = _build_audio_events([(sent_txt, 0.0, 3.2)])
                _write_minimal_pipeline_for_step5(pdir, audio)
                (pdir / "04_edl.json").write_text(
                    json.dumps({
                        "clips": [{
                            "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                            "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                            "duration_sec": audio["locked_duration_sec"],
                            "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                        }],
                        "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                step5_subtitles.run_step5(pdir, template_id="style_01")
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                by_id = {c["id"]: c for c in rep["checks"]}
                self.assertEqual(by_id["subtitle_segmentation_clean"]["status"], "PASS", (sent_txt, by_id["subtitle_segmentation_clean"]))

            # 2) Same-sentence Cantonese demonstrative split "整呢 | 批..." must still FAIL
            (pdir / "03_audio_events.json").write_text(
                json.dumps({"sentences": [{"id": "s0", "text": "每日朝早五点就要开始整呢批用法国牛油做嘅牛角包", "start_sec": 0.0, "end_sec": 3.2}], "locked_duration_sec": 3.5}, ensure_ascii=False),
                encoding="utf-8",
            )
            (pdir / "04_edl.json").write_text(
                json.dumps({"clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 3.5, "duration_sec": 3.5, "source_in_sec": 0.0, "source_out_sec": 3.5}], "fps": 30, "total_duration_sec": 3.5}),
                encoding="utf-8",
            )
            (pdir / "05_subtitles.json").write_text(
                json.dumps({
                    "entries": [
                        {"id": "sub1", "sentence_id": "s0", "text": "每日朝早五点就要开始整呢", "start_sec": 0.0, "end_sec": 1.5, "suppressed": False},
                        {"id": "sub2", "sentence_id": "s0", "text": "批用法国牛油做嘅牛角包", "start_sec": 1.55, "end_sec": 3.2, "suppressed": False},
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                rep_canto = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            self.assertEqual({c["id"]: c for c in rep_canto["checks"]}["subtitle_segmentation_clean"]["status"], "FAIL")

            # 3) Real split words ending in pronoun (e.g. "自 | 我提升", "马耳 | 他") still FAIL
            for left_s, right_s in (("坚持自", "我提升很重要"), ("我们去了马耳", "他旅游一周")):
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": "s0", "text": left_s + right_s, "start_sec": 0.0, "end_sec": 3.0}], "locked_duration_sec": 3.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": "sub1", "sentence_id": "s0", "text": left_s, "start_sec": 0.0, "end_sec": 1.4, "suppressed": False},
                            {"id": "sub2", "sentence_id": "s0", "text": right_s, "start_sec": 1.45, "end_sec": 3.0, "suppressed": False},
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep_sw = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                self.assertEqual({c["id"]: c for c in rep_sw["checks"]}["subtitle_segmentation_clean"]["status"], "FAIL", (left_s, right_s))

    def test_iteration4_unseen_corpora_edge02_verb_pronoun_and_punct_fixes(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) 2-char verb + 1-char pronoun ("告诉 | 我") prevented; numeric 1,000 / 3:30 preserved
        gaosu_segs = kinetic_choreographer.split_clean_phrases(
            "欢迎大家在评论区留言告诉我你的真实想法",
            target_min=4, target_max=8, max_width_px=640, font_path=fp02, font_size=69, strict_width=True,
        )
        self.assertNotIn("告诉|我", "|".join(gaosu_segs), gaosu_segs)
        self.assertTrue(any("告诉我" in s for s in gaosu_segs), gaosu_segs)

        num_dp = [t for _, _, t in step5_subtitles.dp_segment_short_lines("每天3:30汇总1,000份反馈", 800, max_chars=16, strip_punct=False)]
        self.assertEqual(num_dp, ["每天3:30汇总1,000份反馈"], num_dp)
        num_dp_clean = [t for _, _, t in step5_subtitles.dp_segment_short_lines("每天3:30汇总文档", 632, max_chars=12)]
        self.assertEqual(num_dp_clean, ["每天3:30汇总文档"], num_dp_clean)

        # 2) Build MED_BEAUTY_MANDARIN (with 0.36s pause after 二十八天), AUTO_REPAIR_MIXED, CANTONESE_BAKERY, EDGE_02
        w_med = []
        t_m = 0.0
        txt_m = "我们用二十八天追踪了百分之三十五的胶原蛋白流失"
        for idx, ch in enumerate(txt_m):
            w_med.append({"word": ch, "start_sec": round(t_m, 3), "end_sec": round(t_m + 0.14, 3)})
            t_m += 0.14 + (0.36 if idx == 6 else 0.0)
        med_audio = {
            "sentences": [
                {"id": "s0", "text": txt_m, "start_sec": 0.0, "end_sec": round(t_m, 3), "words": w_med},
                _make_word_timed_sentence("s1", "之前觉得做项目很贵，现在发现买错方案才最贵", round(t_m + 0.3, 3), round(t_m + 4.5, 3)),
            ],
            "locked_duration_sec": round(t_m + 5.0, 3),
        }
        auto_audio = _build_audio_events([
            ("做完检测别急先把故障码看懂再动手", 0.0, 3.5),
            ("去年帮车主追回了五十万的售后赔付损失", 3.8, 7.6),
        ])
        bakery_audio = _build_audio_events([
            ("我系阿强我", 0.0, 0.9),
            ("喺天河开咗一间两百八十平嘅手工烘焙坊", 0.95, 4.2),
            ("每日朝早五点就要开始整", 4.5, 6.8),
            ("呢批用法国牛油做嘅牛角包最受欢迎", 6.88, 10.2),
        ])
        edge02_audio = _build_audio_events([
            ("他们公司追回了三千八百万的逾期货款", 0.0, 3.5),
        ])

        for cname, audio in (
            ("MED_BEAUTY_MANDARIN", med_audio),
            ("AUTO_REPAIR_MIXED", auto_audio),
            ("CANTONESE_BAKERY", bakery_audio),
            ("EDGE_02", edge02_audio),
        ):
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    sub_joined = "|".join(e["text"] for e in res["entries"])
                    if cname == "AUTO_REPAIR_MIXED":
                        self.assertNotIn("故障|码", sub_joined, (cname, mode, sub_joined))
                    if cname == "MED_BEAUTY_MANDARIN":
                        self.assertNotIn("百分之三十五的|胶原蛋白流失", sub_joined, (cname, mode, sub_joined))
                        self.assertNotIn("项目很|贵", sub_joined, (cname, mode, sub_joined))
                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt = str(it.get("text_or_asset") or "")
                                fs = int(it.get("font_size_px") or 69)
                                self.assertNotIn(" ", txt, (cname, mode, txt))
                                self.assertFalse(txt.endswith("很"), (cname, mode, txt))
                                self.assertGreaterEqual(fs, 60, (cname, mode, txt, fs))
                                pw, _ = step5_subtitles._measure_text_px(txt, fp02, fs)
                                self.assertLessEqual(pw, 640, (cname, mode, txt, fs, pw))
                        bb_joined = "|".join(str(it.get("text_or_asset") or "") for it in st["items"] if it.get("variant") == "bridge_bar")
                        if cname == "AUTO_REPAIR_MIXED":
                            self.assertNotIn("故障|码", bb_joined, (cname, mode, bb_joined))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

    def test_iteration5_numeric_colon_comma_emergency_split_and_med_beauty_4sent(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) Direct DP multi-punctuation scan, thousands-comma atomic tokenization, and clean_display_text numeric colons
        self.assertEqual(
            [t for _, _, t in step5_subtitles.dp_segment_short_lines("每天3:30汇总，现在开始", 800, max_chars=16, strip_punct=True)],
            ["每天3:30汇总", "现在开始"],
        )
        self.assertEqual(
            step5_subtitles.tokenize_semantic_words("15:30前必须发出1,200单生鲜包裹"),
            ["15:30", "前", "必须", "发出", "1,200单", "生鲜", "包裹"],
        )
        self.assertEqual(
            kinetic_choreographer.clean_display_text("15:30前必须发出1,200单生鲜包裹"),
            "15:30前必须发出1,200单生鲜包裹",
        )
        self.assertEqual(
            kinetic_choreographer.split_clean_phrases("每天3:30汇总，现在开始", target_min=3, target_max=8, max_width_px=640, font_path=fp02, font_size=69, strict_width=True),
            ["每天3:30汇总", "现在开始"],
        )

        # 2) QA inspector positive/negative boundary guards (2+2 emergency split, Mandarin "呢，", Cantonese "呢一...", 3-char "了+heavy_np")
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 3.5, "duration_sec": 3.5, "source_in_sec": 0.0, "source_out_sec": 3.5}],
                    "fps": 30, "total_duration_sec": 3.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")

            qa_cases = [
                ("今天我们要聊的是人工智能行业", ["今天我们要聊的是人工", "智能行业"], "FAIL"),
                ("今天我们要聊的是售后服务行业", ["今天我们要聊的是售后", "服务行业"], "FAIL"),
                ("他买的是很贵的电脑配件", ["他买的是很贵的电脑", "配件"], "FAIL"),
                ("百分之三十五的胶原蛋白流失", ["百分之三十五的胶原", "蛋白流失"], "PASS"),
                ("一千二百平的分拣中心动线", ["一千二百平的分拣", "中心动线"], "PASS"),
                ("说实话呢，个个都觉得这套方法好", ["说实话呢", "个个都觉得这套方法好"], "PASS"),
                ("至于预算呢，批下来还要再等两天", ["至于预算呢", "批下来还要再等两天"], "PASS"),
                ("今日同大家试下呢一套新出炉嘅虾饺", ["今日同大家试下呢", "一套新出炉嘅虾饺"], "FAIL"),
                ("今日同大家试下呢一份新出炉嘅虾饺", ["今日同大家试下呢", "一份新出炉嘅虾饺"], "FAIL"),
                ("今日同大家试下呢一次新出炉嘅虾饺", ["今日同大家试下呢", "一次新出炉嘅虾饺"], "FAIL"),
                ("今日同大家试下呢一条新出炉嘅清蒸鱼", ["今日同大家试下呢", "一条新出炉嘅清蒸鱼"], "FAIL"),
                ("我买咗两百平嘅海鲜餐厅", ["我买咗", "两百平嘅海鲜餐厅"], "FAIL"),
                ("我们二零二五年升级了六套德国进口设备", ["我们二零二五年升级了", "六套德国进口设备"], "FAIL"),
            ]
            for sent_txt, sub_parts, expect_status in qa_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": "s0", "text": sent_txt, "start_sec": 0.0, "end_sec": 3.0}], "locked_duration_sec": 3.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": "s0", "text": sp, "start_sec": round(k * 1.45, 2), "end_sec": round(k * 1.45 + 1.4, 2), "suppressed": False}
                            for k, sp in enumerate(sub_parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (sent_txt, sub_parts, got))

        # 3) Full 4-mode pipeline checks on 3:30+comma, PEDIATRIC_PET_CARE (8:30), LOGISTICS_WAREHOUSE (15:30 + 1,200单 + 分拣中心动线), and 4-sent MED_BEAUTY_MANDARIN
        w_med = []
        t_m = 7.8
        txt_med_s3 = "我们用二十八天追踪了百分之三十五的胶原蛋白流失"
        for idx, ch in enumerate(txt_med_s3):
            w_med.append({"word": ch, "start_sec": round(t_m, 3), "end_sec": round(t_m + 0.14, 3)})
            t_m += 0.14 + (0.36 if idx == 6 else 0.0)
        med4_audio = {
            "sentences": [
                _make_word_timed_sentence("s0", "做了三年医美才看明白。", 0.0, 2.0),
                _make_word_timed_sentence("s1", "很多人以为皮肤松弛只是缺水，其实根本不是。", 2.3, 7.5),
                {"id": "s2", "text": txt_med_s3, "start_sec": 7.8, "end_sec": round(t_m, 3), "words": w_med},
                _make_word_timed_sentence("s3", "之前觉得做项目很贵，现在发现买错方案才最贵。", round(t_m + 0.3, 3), round(t_m + 4.3, 3)),
            ],
            "locked_duration_sec": round(t_m + 4.8, 3),
        }
        pet_audio = _build_audio_events([
            ("幼猫肠胃比较脆弱，换粮一定要循序渐进。", 0.0, 3.5),
            ("每天8:30给猫咪喂15克益生菌，持续两周就稳了。", 3.8, 7.8),
        ])
        logistics_audio = _build_audio_events([
            ("每天3:30汇总，现在开始。", 0.0, 2.4),
            ("15:30前必须发出1,200单生鲜包裹。", 2.7, 6.2),
            ("我们去年优化了一千二百平的分拣中心动线。", 6.5, 10.2),
            ("现在整体出库效率直接翻了一倍，欢迎评论区交流。", 10.5, 14.5),
        ])

        for cname, audio in (
            ("MED_BEAUTY_4SENT", med4_audio),
            ("PEDIATRIC_PET_CARE", pet_audio),
            ("LOGISTICS_WAREHOUSE", logistics_audio),
        ):
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    sub_joined = "|".join(e["text"] for e in res["entries"])
                    if cname == "PEDIATRIC_PET_CARE":
                        self.assertIn("8:30", sub_joined, (cname, mode, sub_joined))
                        self.assertNotIn("猫|咪", sub_joined, (cname, mode, sub_joined))
                    if cname == "LOGISTICS_WAREHOUSE":
                        self.assertIn("3:30", sub_joined, (cname, mode, sub_joined))
                        self.assertIn("15:30", sub_joined, (cname, mode, sub_joined))
                        self.assertIn("1,200单", sub_joined, (cname, mode, sub_joined))
                        self.assertNotIn("汇|总", sub_joined, (cname, mode, sub_joined))
                        self.assertNotIn("1|200", sub_joined, (cname, mode, sub_joined))
                        self.assertNotIn("优化了|一千二百平的|", sub_joined, (cname, mode, sub_joined))
                        self.assertIn("一千二百平的分拣|中心动线", sub_joined, (cname, mode, sub_joined))
                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        bbs = [str(it.get("text_or_asset") or "") for it in st["items"] if it.get("variant") == "bridge_bar"]
                        bb_joined = "|".join(bbs)
                        for txt in bbs:
                            pw, _ = step5_subtitles._measure_text_px(txt, fp02, int(next(it.get("font_size_px") or 69 for it in st["items"] if it.get("text_or_asset") == txt)))
                            self.assertLessEqual(pw, 640, (cname, mode, txt, pw))
                        if cname == "MED_BEAUTY_4SENT":
                            self.assertTrue(
                                any("追踪了" in t or "百分之三十五" in t for t in bbs),
                                (cname, mode, bbs),
                            )
                        if cname == "PEDIATRIC_PET_CARE":
                            self.assertIn("8:30", bb_joined, (cname, mode, bbs))
                            self.assertNotIn("猫|咪", bb_joined, (cname, mode, bbs))
                        if cname == "LOGISTICS_WAREHOUSE":
                            all_stage_txt = "|".join(str(it.get("text_or_asset") or "") for it in st["items"])
                            self.assertNotIn("330", all_stage_txt, (cname, mode, all_stage_txt))
                            self.assertNotIn("1530", all_stage_txt, (cname, mode, all_stage_txt))
                            self.assertNotIn("1|200", all_stage_txt, (cname, mode, all_stage_txt))
                            if mode == "style_02":
                                self.assertIn("15:30", bb_joined, (cname, mode, bbs))
                                self.assertIn("1,200单", bb_joined, (cname, mode, bbs))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

    def test_iteration6_filler_digits_pct_crossborder_and_qa_anti_bypass(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) _drop_filler_tokens / _clean_word_tokens preserve per-char digits, thousands commas, and Chinese reduplications
        for raw_txt, exp_plain in (
            ("每天3:30汇总1,000份反馈。", "每天3:30汇总1,000份反馈"),
            ("我们去年做到1,000,000单，全程零差评。", "我们去年做到1,000,000单全程零差评"),
            ("天天向上，人人努力，看看看这里", "天天向上人人努力看看看这里"),
        ):
            ws = [{"word": ch, "start_sec": round(i * 0.1, 3), "end_sec": round((i + 1) * 0.1, 3)} for i, ch in enumerate(raw_txt)]
            cleaned = step5_subtitles._clean_word_tokens(ws, strip_punct=True)
            self.assertEqual("".join(w["word"] for w in cleaned), exp_plain, raw_txt)

        # Genuine filler words & multi-char non-numeric stutters are still removed
        stutter_ws = [
            {"word": "诶", "start_sec": 0.0, "end_sec": 0.1},
            {"word": "那个", "start_sec": 0.1, "end_sec": 0.25},
            {"word": "那个", "start_sec": 0.25, "end_sec": 0.4},
            {"word": "方案", "start_sec": 0.4, "end_sec": 0.6},
        ]
        self.assertEqual([w["word"] for w in step5_subtitles._drop_filler_tokens(stutter_ws)], ["那个", "方案"])

        # 2) % is exclusive terminal unit (no greedy %+单 fusion)
        toks_pct = step5_subtitles.tokenize_semantic_words("12.8%单日发出")
        self.assertNotIn("12.8%单", toks_pct, toks_pct)
        self.assertEqual(toks_pct[0], "12.8%", toks_pct)

        # 3) 4-mode full checks on 1,000, 1,000,000, 天天/人人, 12.8%单日发出1,200单, and UNSEEN_CROSSBORDER_LIVE
        w_cb = []
        t_cb = 3.0
        txt_cb = "我们花了一百五十万重做了一千八百平的东南亚海外仓发货动线，记住，千万别照搬别人模板。"
        for idx, ch in enumerate(txt_cb):
            w_cb.append({"word": ch, "start_sec": round(t_cb, 3), "end_sec": round(t_cb + 0.14, 3)})
            t_cb += 0.14 + (0.32 if idx == 23 else 0.0)
        crossborder_audio = {
            "sentences": [
                _make_word_timed_sentence("s0", "做跨境电商最怕盲目烧钱。", 0.0, 2.7),
                {"id": "s1", "text": txt_cb, "start_sec": 3.0, "end_sec": round(t_cb, 3), "words": w_cb},
                _make_word_timed_sentence("s2", "欢迎在评论区聊聊你的仓库痛点。", round(t_cb + 0.3, 3), round(t_cb + 3.5, 3)),
            ],
            "locked_duration_sec": round(t_cb + 4.0, 3),
        }
        corpora_it6 = (
            ("NUMERIC_1000", _build_audio_events([("每天3:30汇总1,000份反馈", 0.0, 3.0)])),
            ("NUMERIC_1000000", _build_audio_events([("我们去年做到1,000,000单，全程零差评。", 0.0, 4.0)])),
            ("REPEATED_ZH_CHARS", _build_audio_events([("天天向上，人人努力，看看看这里", 0.0, 3.5)])),
            ("PCT_PLUS_DAN", _build_audio_events([("转化率从3.5%涨到12.8%，单日发出1,200单。", 0.0, 4.0)])),
            ("UNSEEN_CROSSBORDER_LIVE", crossborder_audio),
        )
        for cname, audio in corpora_it6:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    sub_joined = "|".join(e["text"] for e in res["entries"])
                    if cname == "NUMERIC_1000":
                        self.assertIn("1,000份反馈", sub_joined, (cname, mode, sub_joined))
                    elif cname == "NUMERIC_1000000":
                        self.assertIn("1,000,000单", sub_joined, (cname, mode, sub_joined))
                    elif cname == "REPEATED_ZH_CHARS":
                        self.assertIn("天天向上", sub_joined, (cname, mode, sub_joined))
                        self.assertIn("人人努力", sub_joined, (cname, mode, sub_joined))
                    elif cname == "PCT_PLUS_DAN":
                        self.assertIn("12.8%", sub_joined, (cname, mode, sub_joined))
                        self.assertIn("1,200单", sub_joined, (cname, mode, sub_joined))
                    elif cname == "UNSEEN_CROSSBORDER_LIVE":
                        self.assertNotIn("花了|一百五十万", sub_joined, (cname, mode, sub_joined))
                        self.assertNotIn("重|做", sub_joined, (cname, mode, sub_joined))
                        self.assertNotIn("海外|仓", sub_joined, (cname, mode, sub_joined))
                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt_bb = str(it.get("text_or_asset") or "")
                                fs_bb = int(it.get("font_size_px") or 69)
                                pw, _ = step5_subtitles._measure_text_px(txt_bb, fp02, fs_bb)
                                self.assertLessEqual(pw, 640, (cname, mode, txt_bb, fs_bb, pw))
                        if cname == "UNSEEN_CROSSBORDER_LIVE" and mode == "style_02":
                            bb_joined = "|".join(str(it.get("text_or_asset") or "") for it in st["items"] if it.get("variant") == "bridge_bar")
                            self.assertNotIn("花了|一百五十万", bb_joined, bb_joined)
                            self.assertIn("海外仓发货动线", bb_joined, bb_joined)
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 4) Early-comma same_sent_strict anti-bypass & bb_items self-overlap QA checks
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 3.5, "duration_sec": 3.5, "source_in_sec": 0.0, "source_out_sec": 3.5}],
                    "fps": 30, "total_duration_sec": 3.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")
            anti_bypass_cases = [
                ("整呢，今天我们要整呢批用法国牛油做嘅牛角包", ["整呢", "批用法国牛油做嘅牛角包"], "FAIL"),
                ("今天要讲呢，其实今天要讲呢一套新出炉嘅虾饺", ["今天要讲呢", "一套新出炉嘅虾饺"], "FAIL"),
                ("说实话我们今天要讲的是人工，其实我们今天要聊的是人工智能行业", ["说实话我们今天要讲的是人工", "智能行业"], "FAIL"),
                ("说实话呢，个个都觉得这套方法好", ["说实话呢", "个个都觉得这套方法好"], "PASS"),
                ("至于预算呢，批下来还要再等两天", ["至于预算呢", "批下来还要再等两天"], "PASS"),
                ("记得关注我，底下评论区告诉我", ["记得关注我", "底下评论区告诉我"], "PASS"),
            ]
            for sent_txt, sub_parts, expect_status in anti_bypass_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": "s0", "text": sent_txt, "start_sec": 0.0, "end_sec": 3.0}], "locked_duration_sec": 3.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": "s0", "text": sp, "start_sec": round(k * 1.45, 2), "end_sec": round(k * 1.45 + 1.4, 2), "suppressed": False}
                            for k, sp in enumerate(sub_parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (sent_txt, sub_parts, got))

            (pdir / "05_subtitles.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
            (pdir / "06_packaging.json").write_text(
                json.dumps({
                    "style_id": "style_02", "sfx_cues": [],
                    "kinetic_stage": {
                        "theme_profile": "style_02_unified",
                        "items": [
                            {"id": "bb_1", "variant": "bridge_bar", "z_layer": "foreground", "text_or_asset": "第一段过渡", "start_sec": 1.0, "end_sec": 2.2, "x_norm": 0.0, "y_norm": -0.45, "font_size_px": 69},
                            {"id": "bb_2", "variant": "bridge_bar", "z_layer": "foreground", "text_or_asset": "第二段过渡", "start_sec": 1.5, "end_sec": 2.5, "x_norm": 0.0, "y_norm": -0.45, "font_size_px": 69},
                        ],
                    },
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                rep_bb = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            self.assertEqual({c["id"]: c for c in rep_bb["checks"]}["overlay_cross_act_overlap"]["status"], "FAIL")

    def test_iteration7_gate6_remediation(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) Word-level ABAB reduplications preserved while discourse/pronoun stutters dropped; 并重装 coalesced
        abab_ws = [
            {"word": w, "start_sec": round(i * 0.15, 3), "end_sec": round((i + 1) * 0.15, 3)}
            for i, w in enumerate(["我们", "先", "研究", "研究", "这个", "方案"])
        ]
        abab_chunks = step5_subtitles.split_sentence_into_chunks({"id": "s0", "text": "", "start_sec": 0.0, "end_sec": 0.9, "words": abab_ws})
        self.assertEqual("".join(c["text"] for c in abab_chunks), "我们先研究研究这个方案", abab_chunks)

        stutter_ws = [
            {"word": w, "start_sec": round(i * 0.15, 3), "end_sec": round((i + 1) * 0.15, 3)}
            for i, w in enumerate(["诶", "那个", "那个", "方案"])
        ]
        self.assertEqual([w["word"] for w in step5_subtitles._drop_filler_tokens(stutter_ws)], ["那个", "方案"])

        toks_reinstall = step5_subtitles.tokenize_semantic_words("我们在广州建了中央冷链厂并重装了三家综合体育馆")
        self.assertIn("重装", toks_reinstall, toks_reinstall)
        self.assertNotIn("并重", toks_reinstall, toks_reinstall)
        toks_equal = step5_subtitles.tokenize_semantic_words("质量与速度并重装不下")
        self.assertIn("并重", toks_equal, toks_equal)

        # 2) rebalance_asr_sentences non-merge guards for temporal adverbials & new clause verbs
        non_merge_pairs = [
            [
                {"id": "s0", "text": "公司去年就倒闭了", "start_sec": 0.0, "end_sec": 1.6, "words": []},
                {"id": "s1", "text": "三年后我重新出发", "start_sec": 1.68, "end_sec": 3.2, "words": []},
            ],
            [
                {"id": "s0", "text": "最后却还是亏本了", "start_sec": 0.0, "end_sec": 1.6, "words": []},
                {"id": "s1", "text": "老板决定马上关店", "start_sec": 1.68, "end_sec": 3.2, "words": []},
            ],
        ]
        for pair in non_merge_pairs:
            reb = step5_subtitles.rebalance_asr_sentences(pair, max_chars=9)
            self.assertEqual(len(reb), 2, (pair, reb))

        # 3) clean_display_text, TikTok Shop long wrap, 2-char orphan tie-breaker, and adaptive modifier/negation fallback
        self.assertEqual(kinetic_choreographer.clean_display_text("现在呢，这批货"), "现在呢这批货")
        wrap_segs = [t for _, _, t in step5_subtitles.dp_segment_short_lines("TikTok Shop Global Crossborder", 640, font_path=fp02, font_size=69, max_chars=9, strict_width=True)]
        self.assertEqual(wrap_segs[0], "TikTok Shop", wrap_segs)
        for seg_t in wrap_segs:
            pw, _ = step5_subtitles._measure_text_px(seg_t, fp02, 69)
            self.assertLessEqual(pw, 640, (seg_t, pw))

        abab_bridge = kinetic_choreographer._split_bridge_phrases_adaptive("回去再考虑考虑这笔1,000万的预算。", target_min=4, target_max=9, font_path=fp02)
        self.assertEqual(abab_bridge, ["回去再考虑考虑", "这笔1,000万的预算"], abab_bridge)

        pre_cold = kinetic_choreographer._split_bridge_phrases_adaptive("我们花了三百万重建了六百五十平的预冷车间，", target_min=4, target_max=9, font_path=fp02)
        self.assertEqual(pre_cold, ["我们花了三百万重建了", "六百五十平的预冷车间"])
        for seg_t in pre_cold:
            pw, _ = step5_subtitles._measure_text_px(seg_t, fp02, 60)
            self.assertLessEqual(pw, 640, (seg_t, pw))

        # 4) 4-mode end-to-end M1 QA pass on all Gate 6 positive corpora
        corpora_it7 = (
            ("INTER_SENT_PUNCT_COMPOUND", _build_audio_events([("他买了一台电脑。", 2.1, 4.1), ("配件全部都是原装。", 4.2, 6.2)])),
            ("INTER_SENT_PUNCT_DE", _build_audio_events([("也是我最喜欢的。", 2.1, 4.1), ("店里每天都爆满。", 4.2, 6.2)])),
            ("MIXED_TECH_APPLE", _build_audio_events([("每天上午9:30到下午3:25，我们在小程序和TikTok Shop同步直播。", 0.0, 5.0)])),
            ("CROSS_SENT_ASR_SPLITS", _build_audio_events([("我们花了三百万打造的", 0.0, 1.8), ("供应链系统终于上线了", 1.86, 3.6)])),
            ("NUM_COLON_EARLY_IN_SENT", _build_audio_events([("每天3:30我们汇总了", 0.0, 1.5), ("一千两百份客户反馈", 1.6, 3.5)])),
            ("ABAB_CONSIDER", _build_audio_events([
                ("今天我们先看预算。", 0.0, 2.0),
                ("回去再考虑考虑这笔1,000万的预算。", 2.2, 5.4),
                ("欢迎大家评论区留言。", 5.6, 7.6),
            ])),
            ("CANTO_MOD_AND_NEG", _build_audio_events([
                ("𠮶边就系我哋嘅鱼池到时就有好多新鲜嘅海鲜喺𠮶度供应俾大家选择", 0.0, 4.8),
                ("不如先把一句没有用的废话删干净", 5.0, 8.0),
                ("写稿录音剪辑最花时间的其实是写稿", 8.2, 11.2),
            ])),
            ("XIANZAINE", _build_audio_events([("现在呢，这批货已经全部发往海外仓了。", 0.0, 3.5)])),
        )
        for cname, audio in corpora_it7:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    sub_joined = "|".join(e["text"] for e in res["entries"])
                    if cname == "CROSS_SENT_ASR_SPLITS":
                        self.assertNotIn("打造的|供应链", sub_joined, (cname, mode, sub_joined))
                    elif cname == "NUM_COLON_EARLY_IN_SENT":
                        self.assertNotIn("汇总了|一千两百份", sub_joined, (cname, mode, sub_joined))

                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        bbs = [str(it.get("text_or_asset") or "") for it in st["items"] if it.get("variant") == "bridge_bar"]
                        bb_joined = "|".join(bbs)
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt_bb = str(it.get("text_or_asset") or "")
                                fs_bb = int(it.get("font_size_px") or 69)
                                self.assertGreaterEqual(fs_bb, 60, (cname, mode, txt_bb, fs_bb))
                                pw, _ = step5_subtitles._measure_text_px(txt_bb, fp02, fs_bb)
                                self.assertLessEqual(pw, 640, (cname, mode, txt_bb, fs_bb, pw))
                        all_items_joined = "|".join(str(it.get("text_or_asset") or "") for it in st["items"])
                        if cname == "ABAB_CONSIDER":
                            self.assertIn("考虑考虑", all_items_joined, (mode, all_items_joined))
                            self.assertNotIn("考虑|考虑", all_items_joined, (mode, all_items_joined))
                        elif cname == "CANTO_MOD_AND_NEG":
                            self.assertNotIn("就有好多|", bb_joined, (mode, bbs))
                            self.assertNotIn("没有|用的", bb_joined, (mode, bbs))
                            self.assertNotIn("最花|时间的", bb_joined, (mode, bbs))
                        elif cname == "XIANZAINE":
                            self.assertNotIn("现在呢 这批货", bb_joined, (mode, bbs))

                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 5) Negative QA anti-bypass & verified ASCII space preservation checks
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 4.5, "duration_sec": 4.5, "source_in_sec": 0.0, "source_out_sec": 4.5}],
                    "fps": 30, "total_duration_sec": 4.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")
            qa_cases = [
                ([("每天3:30汇总文档", 0.0, 3.0)], ["每天3:", "30汇总文档"], "FAIL"),
                ([("每天3:30汇总1,000份反馈", 0.0, 3.0)], ["每天3:30汇总1,", "000份反馈"], "FAIL"),
                ([("每天3:30汇总1,0份反馈", 0.0, 3.0)], ["每天3:30汇总1,", "0份反馈"], "FAIL"),
                ([("说实话，效果不错", 0.0, 3.0)], ["说实话，", "效果不错"], "PASS"),
                ([("注意：千万别错过", 0.0, 3.0)], ["注意：", "千万别错过"], "PASS"),
                ([("也是我最喜欢的", 0.0, 2.0), ("店里每天都爆满", 2.05, 4.0)], ["也是我最喜欢的", "店里每天都爆满"], "FAIL"),
                ([("他买了一台电脑", 0.0, 2.0), ("配件全部都是原装", 2.05, 4.0)], ["他买了一台电脑", "配件全部都是原装"], "FAIL"),
                ([("我们在TikTok直播", 0.0, 3.0)], ["我们在TikT", "ok直播"], "FAIL"),
                ([("我们在TikTokShop直播", 0.0, 3.0)], ["我们在TikTok", "Shop直播"], "FAIL"),
                ([("我们在TikTok Shop Global Crossborder直播", 0.0, 3.0)], ["我们在TikTok Shop", "Global Crossborder直播"], "PASS"),
            ]
            for s_rows, parts, expect_status in qa_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 4.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": f"s{k if len(s_rows) > 1 else 0}", "text": sp, "start_sec": round(k * 2.0, 2), "end_sec": round(k * 2.0 + (1.9 if len(s_rows) == 1 else 2.0), 2), "suppressed": False}
                            for k, sp in enumerate(parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (s_rows, parts, got))

    def test_iteration8_challenger_remediation(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) ABAB 'n'-tagged reduplications preserved/coalesced while demonstrative+noun & discourse stutters dropped
        for w_seq, exp_joined in (
            (["稍微", "意思", "意思", "就行了"], "稍微意思意思就行了"),
            (["我们", "体验", "体验", "这套", "系统"], "我们体验体验这套系统"),
        ):
            ws_ab = [
                {"word": w, "start_sec": round(i * 0.14, 3), "end_sec": round((i + 1) * 0.14, 3)}
                for i, w in enumerate(w_seq)
            ]
            kept_words = [w["word"] for w in step5_subtitles._drop_filler_tokens(ws_ab)]
            self.assertEqual("".join(kept_words), exp_joined, kept_words)
            chunks_ab = step5_subtitles.split_sentence_into_chunks(
                {"id": "s0", "text": "", "start_sec": 0.0, "end_sec": round(len(w_seq) * 0.14, 3), "words": ws_ab}
            )
            self.assertEqual("".join(c["text"] for c in chunks_ab), exp_joined, chunks_ab)
        self.assertIn("意思意思", step5_subtitles.tokenize_semantic_words("稍微意思意思就行了"))
        self.assertIn("体验体验", step5_subtitles.tokenize_semantic_words("我们体验体验这套系统"))
        self.assertEqual(
            [w["word"] for w in step5_subtitles._drop_filler_tokens([
                {"word": "诶", "start_sec": 0.0, "end_sec": 0.1},
                {"word": "那个", "start_sec": 0.1, "end_sec": 0.2},
                {"word": "那个", "start_sec": 0.2, "end_sec": 0.3},
                {"word": "方案", "start_sec": 0.3, "end_sec": 0.4},
            ])],
            ["那个", "方案"],
        )

        # 2) Object/quantifier-followed "并重装/并重做" coalesced without breaking "德才并重" or "并重装不下"
        toks_obj = step5_subtitles.tokenize_semantic_words("他删掉了旧版本并重装这套系统")
        self.assertIn("重装", toks_obj, toks_obj)
        self.assertNotIn("并重", toks_obj, toks_obj)
        toks_qty = step5_subtitles.tokenize_semantic_words("我们推翻旧方案并重做一遍")
        self.assertIn("重做", toks_qty, toks_qty)
        self.assertNotIn("并重", toks_qty, toks_qty)
        self.assertIn("并重", step5_subtitles.tokenize_semantic_words("德才并重"))
        self.assertIn("并重", step5_subtitles.tokenize_semantic_words("性能与稳定性并重装不下"))

        # 3) Cross-sentence ASR parity non-regression: still keep independent clauses apart
        for pair in (
            [
                {"id": "s0", "text": "公司去年就倒闭了", "start_sec": 0.0, "end_sec": 1.6, "words": []},
                {"id": "s1", "text": "三年后我重新出发", "start_sec": 1.68, "end_sec": 3.2, "words": []},
            ],
            [
                {"id": "s0", "text": "最后却还是亏本了", "start_sec": 0.0, "end_sec": 1.6, "words": []},
                {"id": "s1", "text": "第一我们要先把定位搞清楚", "start_sec": 1.68, "end_sec": 3.2, "words": []},
            ],
        ):
            self.assertEqual(len(step5_subtitles.rebalance_asr_sentences(pair, max_chars=9)), 2, pair)

        # 4) 4-mode end-to-end M1 QA pass across all Iteration 8 unseen & multi-sentence corpora (both no-words and word-timed)
        corpora_it8 = (
            ("ASR_LE_LONG_NP_10", _build_audio_events([("我们昨晚升级了", 0.0, 1.5), ("智能仓储分拣控制系统", 1.58, 3.4)])),
            ("ASR_ZUO_HAODUO", _build_audio_events([("我哋今日买咗", 0.0, 1.4), ("好多新鲜嘅海鲜", 1.48, 3.2)])),
            ("ASR_DE_VERB_MOD", _build_audio_events([("这是我花了三百万买的", 0.0, 1.8), ("会自己打包的机器", 1.86, 3.6)])),
            ("COMMA_LE", _build_audio_events([("流程跑通了，团队效率自然就上去了。", 0.0, 3.0)])),
            ("PUNCT_MIXED_1", _build_audio_events([("注意：第一、选品看毛利；第二、投流看ROI（至少1:3.5）——千万别盲目加仓！", 0.0, 4.5)])),
            ("BRAND_CLOUD_RUN_NW", {
                "sentences": [{"id": "s0", "text": "我们用Google Cloud Run和AWS Lambda做渲染。", "start_sec": 0.0, "end_sec": 4.0, "words": []}],
                "locked_duration_sec": 4.5,
            }),
            ("BRAND_LEAN_STARTUP_NW", {
                "sentences": [{"id": "s0", "text": "推荐这本The Lean Startup给大家。", "start_sec": 0.0, "end_sec": 3.5, "words": []}],
                "locked_duration_sec": 4.0,
            }),
            ("UNSEEN_3RD_SAAS_OPS_NW", {
                "sentences": [{"id": "s0", "text": "去年全平台做到2,500万营业额，零坏账。", "start_sec": 0.0, "end_sec": 4.0, "words": []}],
                "locked_duration_sec": 4.5,
            }),
            ("UNSEEN_3RD_AUTO_REPAIR", _build_audio_events([
                ("门店最关键有三个动作：第一、读故障码；第二、测压差；第三、做均衡。", 0.0, 4.5),
                ("把这三步走顺了，返修率能压到1.2%以内。", 4.6, 8.2),
            ])),
            ("UNSEEN_3RD_CANTO_BAKERY", _build_audio_events([
                ("我哋今日买咗好多新鲜嘅海鲜，记得提早预订。", 0.0, 4.0),
            ])),
        )
        for cname, audio in corpora_it8:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    _assert_entries_clean(self, res["entries"], f"{cname}/{mode}")
                    sub_texts = [e["text"] for e in res["entries"]]
                    if cname == "BRAND_CLOUD_RUN_NW":
                        self.assertTrue(any("Google Cloud Run" in t for t in sub_texts), sub_texts)
                        self.assertTrue(any("AWS Lambda" in t for t in sub_texts), sub_texts)
                    elif cname == "BRAND_LEAN_STARTUP_NW":
                        self.assertTrue(any("The Lean Startup" in t for t in sub_texts), sub_texts)
                    elif cname == "UNSEEN_3RD_AUTO_REPAIR":
                        self.assertIn("第一读故障码", sub_texts, sub_texts)
                        self.assertIn("第二测压差", sub_texts, sub_texts)
                        self.assertIn("第三做均衡", sub_texts, sub_texts)

                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt_bb = str(it.get("text_or_asset") or "")
                                fs_bb = int(it.get("font_size_px") or 69)
                                pw, _ = step5_subtitles._measure_text_px(txt_bb, fp02, fs_bb)
                                self.assertLessEqual(pw, 640, (cname, mode, txt_bb, fs_bb, pw))
                            self.assertNotIn("走顺了返修率以内", str(it.get("text_or_asset") or ""))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 5) QA comma-"了" positive vs unpunctuated-"了" negative & unspaced alphanumeric anti-bypass checks
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 4.5, "duration_sec": 4.5, "source_in_sec": 0.0, "source_out_sec": 4.5}],
                    "fps": 30, "total_duration_sec": 4.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")
            it8_qa_cases = [
                ([("流程跑通了，团队效率自然就上去了。", 0.0, 3.0)], ["流程跑通了", "团队效率自然就上去了"], "PASS"),
                ([("把这三步走顺了，返修率能压到1.2%以内。", 0.0, 3.0)], ["把这三步走顺了", "返修率能压到1.2%以内"], "PASS"),
                ([("我们去了三家门店", 0.0, 3.0)], ["我们去了", "三家门店"], "FAIL"),
                ([("我们在用iPhone16拍视频", 0.0, 3.0)], ["我们在用iPhone", "16拍视频"], "FAIL"),
                ([("我们用GPT4做初筛", 0.0, 3.0)], ["我们用GPT", "4做初筛"], "FAIL"),
                ([("我们用GPT 4做初筛", 0.0, 3.0)], ["我们用GPT", "4做初筛"], "PASS"),
            ]
            for s_rows, parts, expect_status in it8_qa_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 4.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": "s0", "text": sp, "start_sec": round(k * 2.0, 2), "end_sec": round(k * 2.0 + 1.9, 2), "suppressed": False}
                            for k, sp in enumerate(parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (s_rows, parts, got))

    def test_iteration9_challenger_remediation(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) "并重" at clause/sentence end coalesces without breaking "德才并重" or "并重装不下"
        self.assertEqual(
            step5_subtitles.tokenize_semantic_words("建议先备份并重装"),
            ["建议", "先", "备份", "并", "重装"],
        )
        self.assertEqual(
            step5_subtitles.tokenize_semantic_words("建议备份并重装，明天再用"),
            ["建议", "备份", "并", "重装", "，", "明天", "再", "用"],
        )
        self.assertEqual(step5_subtitles.tokenize_semantic_words("德才并重"), ["德才", "并重"])
        self.assertIn("并重", step5_subtitles.tokenize_semantic_words("性能与稳定性并重装不下"))

        # 2) Hyphenated ASCII tokens preserved intact during tokenization & DP segmentation
        dp_hyph = [
            t for _, _, t in step5_subtitles.dp_segment_short_lines(
                "我们在用USB-C接口，支持Wi-Fi-6和Type-C快速传输数据。",
                632,
                font_size=59,
                max_chars=9,
            )
        ]
        joined_hyph = "|".join(dp_hyph)
        self.assertIn("USB-C", joined_hyph, dp_hyph)
        self.assertIn("Wi-Fi-6", joined_hyph, dp_hyph)
        self.assertIn("Type-C", joined_hyph, dp_hyph)

        # 3) Non-merge guards: independent SVO/temporal/ordinal clauses still stay separated
        for pair in (
            [
                {"id": "s0", "text": "公司去年就倒闭了", "start_sec": 0.0, "end_sec": 1.6, "words": []},
                {"id": "s1", "text": "三年后我重新出发", "start_sec": 1.68, "end_sec": 3.2, "words": []},
            ],
            [
                {"id": "s0", "text": "最后却还是亏本了", "start_sec": 0.0, "end_sec": 1.6, "words": []},
                {"id": "s1", "text": "第一我们要先把定位搞清楚", "start_sec": 1.68, "end_sec": 3.2, "words": []},
            ],
            [
                {"id": "s0", "text": "最后却还是亏本了", "start_sec": 0.0, "end_sec": 1.6, "words": []},
                {"id": "s1", "text": "老板决定关店", "start_sec": 1.68, "end_sec": 3.2, "words": []},
            ],
            [
                {"id": "s0", "text": "今天和大家聊聊我们是怎么布置店里的", "start_sec": 0.0, "end_sec": 3.2, "words": []},
                {"id": "s1", "text": "镜头转向大厅的卡座区域", "start_sec": 3.2, "end_sec": 5.2, "words": []},
            ],
            [
                {"id": "s0", "text": "这个其实是我去年花了三百万特地从德国走出来的", "start_sec": 0.0, "end_sec": 3.6, "words": []},
                {"id": "s1", "text": "全自动包装设备", "start_sec": 3.66, "end_sec": 5.2, "words": []},
            ],
        ):
            self.assertEqual(len(step5_subtitles.rebalance_asr_sentences(pair, max_chars=9)), 2, pair)

        # 4) 4-mode end-to-end M1 QA pass on all 4 challenger_m1_8 defect corpora (including Whisper space-separated words)
        mb_text = "我们用Google Cloud Run和AWS Lambda，同时接入Cloudflare Workers做渲染。"
        mb_toks = ["我们", "用", "Google", "Cloud", "Run", "和", "AWS", "Lambda", "，", "同时", "接入", "Cloudflare", "Workers", "做", "渲染", "。"]
        mb_words = [
            {"word": tok, "start_sec": round(i * 0.3, 3), "end_sec": round((i + 1) * 0.3, 3)}
            for i, tok in enumerate(mb_toks)
        ]
        corpora_it9 = (
            ("NP_LEN2_SYSTEM", _build_audio_events([("我们昨晚升级了", 0.0, 1.5), ("智能仓储分拣系统", 1.58, 3.3)])),
            ("LOOP_A_PLATFORM", _build_audio_events([("我们刚上线了", 0.0, 1.4), ("全自动订单处理平台", 1.48, 3.3)])),
            ("LOOP_A_TOOL", _build_audio_events([("门店刚引入了", 0.0, 1.4), ("智能预约排班工具", 1.48, 3.3)])),
            ("LOOP_B_GEN_LONG", _build_audio_events([("这个其实是我去年花了三百万特地从德国买回来的", 0.0, 3.6), ("全自动包装设备", 3.66, 5.2)])),
            ("MULTI_BRAND_NW", {"sentences": [{"id": "s0", "text": mb_text, "start_sec": 0.0, "end_sec": 5.0, "words": []}], "locked_duration_sec": 5.5}),
            ("MULTI_BRAND_RW", {"sentences": [{"id": "s0", "text": mb_text, "start_sec": 0.0, "end_sec": 5.0, "words": mb_words}], "locked_duration_sec": 5.5}),
            ("HYPHEN_ASCII_MULTI", _build_audio_events([("我们在用USB-C接口，支持Wi-Fi-6和Type-C快速传输数据。", 0.0, 4.5)])),
        )
        for cname, audio in corpora_it9:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    _assert_entries_clean(self, res["entries"], f"{cname}/{mode}")
                    sub_texts = [e["text"] for e in res["entries"]]
                    if cname in ("MULTI_BRAND_NW", "MULTI_BRAND_RW"):
                        self.assertTrue(any("Google Cloud Run" in t for t in sub_texts), (cname, mode, sub_texts))
                        self.assertTrue(any("AWS Lambda" in t for t in sub_texts), (cname, mode, sub_texts))
                        self.assertTrue(any("Cloudflare Workers" in t for t in sub_texts), (cname, mode, sub_texts))
                        self.assertFalse(any(len(re.sub(r"[\s，。！？、；：,.!?;:]+", "", t)) <= 3 for t in sub_texts), (cname, mode, sub_texts))
                    elif cname == "HYPHEN_ASCII_MULTI":
                        self.assertTrue(any("USB-C" in t for t in sub_texts), sub_texts)
                        self.assertTrue(any("Wi-Fi-6" in t for t in sub_texts), sub_texts)
                        self.assertTrue(any("Type-C" in t for t in sub_texts), sub_texts)

                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt_bb = str(it.get("text_or_asset") or "")
                                fs_bb = int(it.get("font_size_px") or 69)
                                pw, _ = step5_subtitles._measure_text_px(txt_bb, fp02, fs_bb)
                                self.assertLessEqual(pw, 640, (cname, mode, txt_bb, fs_bb, pw))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 5) Hyphenated ASCII negative QA anti-bypass checks vs spaced dash PASS
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 4.5, "duration_sec": 4.5, "source_in_sec": 0.0, "source_out_sec": 4.5}],
                    "fps": 30, "total_duration_sec": 4.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")
            hyph_qa_cases = [
                ([("我们在用USB-C接口", 0.0, 3.0)], ["我们在用USB-", "C接口"], "FAIL"),
                ([("我们在用USB-C接口", 0.0, 3.0)], ["我们在用USB", "-C接口"], "FAIL"),
                ([("我们在用USB-C-123接口", 0.0, 3.0)], ["我们在用USB-C", "-123接口"], "FAIL"),
                ([("看A - B接口", 0.0, 3.0)], ["看A -", "B接口"], "PASS"),
            ]
            for s_rows, parts, expect_status in hyph_qa_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 4.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": "s0", "text": sp, "start_sec": round(k * 2.0, 2), "end_sec": round(k * 2.0 + 1.9, 2), "suppressed": False}
                            for k, sp in enumerate(parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (s_rows, parts, got))

    def test_iteration10_challenger_remediation(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        tpl01 = common.load_subtitle_templates()["style_01"]
        fp01 = common.find_source_font(
            tpl01.get("source_font_family"), "style_01", tpl01.get("font_candidates")
        ) or common.find_commercial_font(tpl01.get("font_candidates"), verified_only=True)
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) Numeric+ASCII-unit tokens atomic while preserving alphanumeric product IDs, USB-C, iOS 18, 3.5%, 1,000, 8:30
        for tok_in, expected_tok in (
            ("支持2.4G低延迟传输", "2.4G"),
            ("全系支持5G高速网络", "5G"),
            ("兼容100W快充和60fps录制", "100W"),
            ("兼容100W快充和60fps录制", "60fps"),
            ("配备16GB内存与120Hz屏幕", "16GB"),
            ("配备16GB内存与120Hz屏幕", "120Hz"),
            ("我们在用iPhone16和GPT4测试", "iPhone16"),
            ("我们在用iPhone16和GPT4测试", "GPT4"),
            ("连接USB-C接口升级iOS 18系统", "USB-C"),
            ("增长3.5%，营收1,000万，8:30开播", "3.5%"),
            ("增长3.5%，营收1,000万，8:30开播", "1,000万"),
            ("增长3.5%，营收1,000万，8:30开播", "8:30"),
        ):
            toks = step5_subtitles.tokenize_semantic_words(tok_in)
            self.assertIn(expected_tok, toks, (tok_in, toks))

        # 2) SVO vs Compound-NP morpho-syntactic parity: SVO clauses stay unmerged, compound/numeral NPs merge
        svo_non_merge_pairs = [
            ("昨晚出事了", "老板推出新品"),
            ("大家请注意了", "教练演示动作"),
            ("最后还是崩了", "系统丢失数据"),
            ("老板终于同意了", "员工调整方案"),
            ("刚开业就火了", "顾客排队打卡"),
            ("刚才已经说清楚了", "客户提出质疑"),
            ("我们去年就亏本了", "老板放弃门店"),
            ("服务出了问题了", "客户投诉门店"),
        ]
        for p_txt, c_txt in svo_non_merge_pairs:
            pair = [
                {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 1.5, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": 1.58, "end_sec": 3.2, "words": []},
            ]
            reb = step5_subtitles.rebalance_asr_sentences(pair, max_chars=9)
            self.assertEqual(len(reb), 2, (p_txt, c_txt, reb))
            self.assertTrue(step5_subtitles.is_independent_svo_clause(c_txt, prev_plain=p_txt), (p_txt, c_txt))

        np_merge_pairs = [
            ("我们升级了", 0.0, 1.4, "双核独立显卡", 1.48, 3.0),
            ("我们昨晚升级了", 0.0, 1.5, "全自动智能订单分拣处理系统", 1.58, 3.5),
            ("这个其实是我去年花了三百万特地从德国买回来的", 0.0, 3.6, "五套全自动设备", 3.66, 5.2),
            ("这个其实是我去年花了三百万特地从德国买回来的", 0.0, 3.6, "全自动智能仓储分拣控制系统", 3.66, 5.5),
        ]
        for p_txt, ps, pe, c_txt, cs, ce in np_merge_pairs:
            pair = [
                {"id": "s0", "text": p_txt, "start_sec": ps, "end_sec": pe, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": cs, "end_sec": ce, "words": []},
            ]
            reb = step5_subtitles.rebalance_asr_sentences(pair, max_chars=9)
            self.assertEqual(len(reb), 1, (p_txt, c_txt, reb))
            self.assertFalse(step5_subtitles.is_independent_svo_clause(c_txt, prev_plain=p_txt), (p_txt, c_txt))

        # 3) 4-mode end-to-end M1 QA pass on all Iteration 10 corpora (units, SVO/NP, brands, spaced-ASCII punct)
        hw_sents = [
            ("做硬件测评，音频和供电最容易翻车。", 0.0, 2.5),
            ("第一、麦克风接口要用USB-C直连电脑，", 2.6, 5.2),
            ("第二、无线接收器要支持2.4G低延迟传输，", 5.3, 8.2),
            ("第三、快充协议必须兼容100W以上。", 8.3, 10.8),
            ("这样整套设备带出去拍一整天都稳。", 10.9, 13.5),
        ]
        hw_words = [
            [
                {"word": w, "start_sec": round(st + idx_w * ((en - st) / max(1, len(toks_w))), 3), "end_sec": round(st + (idx_w + 1) * ((en - st) / max(1, len(toks_w))), 3)}
                for idx_w, w in enumerate(toks_w)
            ]
            for (t_s, st, en), toks_w in zip(
                hw_sents,
                [
                    ["做", "硬件", "测评", "，", "音频", "和", "供电", "最", "容易", "翻车", "。"],
                    ["第一", "、", "麦克风", "接口", "要", "用", "USB-C", "直连", "电脑", "，"],
                    ["第二", "、", "无线", "接收器", "要", "支持", "2.4", "G", "低", "延迟", "传输", "，"],
                    ["第三", "、", "快充", "协议", "必须", "兼容", "100", "W", "以上", "。"],
                    ["这样", "整套", "设备", "带出去", "拍", "一整天", "都", "稳", "。"],
                ],
            )
        ]
        gcr_words = [
            {"word": w, "start_sec": round(i * 0.3, 3), "end_sec": round((i + 1) * 0.3, 3)}
            for i, w in enumerate(["我们", "用", "Google", "Cloud", "Run", "做", "渲染", "。"])
        ]
        corpora_it10 = (
            ("BLIND_HARDWARE_NW", _build_audio_events(hw_sents)),
            ("BLIND_HARDWARE_W", {"sentences": [{"id": f"s{i}", "text": t, "start_sec": st, "end_sec": en, "words": hw_words[i]} for i, (t, st, en) in enumerate(hw_sents)], "locked_duration_sec": 14.0}),
            ("UNIT_5G", _build_audio_events([("新款手机全系支持5G高速网络下载", 0.0, 3.0)])),
            ("UNIT_24G", _build_audio_events([("无线接收器要支持2.4G低延迟传输", 0.0, 3.0)])),
            ("SVO_TUICHU", _build_audio_events([("昨晚出事了", 0.0, 1.5), ("老板推出新品", 1.58, 3.2)])),
            ("SVO_YANSHI", _build_audio_events([("大家请注意了", 0.0, 1.5), ("教练演示动作", 1.58, 3.2)])),
            ("SVO_DIUSHI", _build_audio_events([("最后还是崩了", 0.0, 1.5), ("系统丢失数据", 1.58, 3.2)])),
            ("SVO_TIAOZHENG", _build_audio_events([("老板终于同意了", 0.0, 1.6), ("员工调整方案", 1.68, 3.3)])),
            ("SVO_PAIDUI", _build_audio_events([("刚开业就火了", 0.0, 1.5), ("顾客排队打卡", 1.58, 3.2)])),
            ("SVO_TICHU", _build_audio_events([("刚才已经说清楚了", 0.0, 1.6), ("客户提出质疑", 1.68, 3.3)])),
            ("NP_XIANKA", _build_audio_events([("我们升级了", 0.0, 1.4), ("双核独立显卡", 1.48, 3.0)])),
            ("NP_LEN13", _build_audio_events([("我们昨晚升级了", 0.0, 1.5), ("全自动智能订单分拣处理系统", 1.58, 3.5)])),
            ("GEN_LONG_WUTAO", _build_audio_events([("这个其实是我去年花了三百万特地从德国买回来的", 0.0, 3.6), ("五套全自动设备", 3.66, 5.2)])),
            ("GEN_LONG_LEN13", _build_audio_events([("这个其实是我去年花了三百万特地从德国买回来的", 0.0, 3.6), ("全自动智能仓储分拣控制系统", 3.66, 5.5)])),
            ("BRAND_SINGLE_GCR_NW", _build_audio_events([("我们用Google Cloud Run做渲染。", 0.0, 3.0)])),
            ("BRAND_SINGLE_GCR_W", {"sentences": [{"id": "s0", "text": "我们用Google Cloud Run做渲染。", "start_sec": 0.0, "end_sec": 3.0, "words": gcr_words}], "locked_duration_sec": 3.5}),
            ("CAP_WORKERS_3", _build_audio_events([("支持Cloudflare Workers做渲染。", 0.0, 3.0)])),
            ("CAP_AZURE", _build_audio_events([("我们用Microsoft Azure Functions做渲染。", 0.0, 3.5)])),
            ("WS_LOWERCASE", _build_audio_events([("我们用google cloud run做渲染。", 0.0, 3.0)])),
            ("MULTI_SP", _build_audio_events([("我们用Google  Cloud   Run做渲染。", 0.0, 3.0)])),
            ("CASE_DIFF", _build_audio_events([("我们用gOoGlE cLoUd RuN做渲染。", 0.0, 3.0)])),
            ("WS_HYPHEN_MIX", _build_audio_events([("支持 Wi-Fi-6 和 Type-C 传输，速度直接翻倍。", 0.0, 4.0)])),
            ("WS_HYPHEN_SPACE", _build_audio_events([("支持 Wi-Fi 6 和 Type-C 传输，速度直接翻倍。", 0.0, 4.0)])),
            ("PUNCT_SPACE_NOHYPH_NW", _build_audio_events([("支持全功能 iOS 18 传输，速度直接翻倍。", 0.0, 4.0)])),
        )
        for cname, audio in corpora_it10:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    _assert_entries_clean(self, res["entries"], f"{cname}/{mode}")
                    sub_texts = [e["text"] for e in res["entries"]]
                    if sid == "style_01" and cname in ("BRAND_SINGLE_GCR_NW", "BRAND_SINGLE_GCR_W", "CAP_WORKERS_3", "CAP_AZURE", "WS_LOWERCASE", "MULTI_SP", "CASE_DIFF"):
                        for e_sub in res["entries"]:
                            t_sub = e_sub["text"]
                            self.assertGreater(len(re.sub(r"[\s，。！？、；：,.!?;:]+", "", t_sub)), 3, (cname, sub_texts))
                            pw59, _ = step5_subtitles._measure_text_px(t_sub, fp01, 59)
                            self.assertLessEqual(pw59, 632, (cname, t_sub, pw59))
                            self.assertLessEqual(int(e_sub["final_bbox"]["w"]), 640, (cname, t_sub, e_sub["final_bbox"]))
                            if cname in ("BRAND_SINGLE_GCR_NW", "BRAND_SINGLE_GCR_W", "CAP_WORKERS_3", "CAP_AZURE"):
                                pw75, _ = step5_subtitles._measure_text_px(t_sub, fp01, int(tpl01.get("font_size", 75)))
                                self.assertLessEqual(pw75, 640, (cname, t_sub, pw75))
                    if cname in ("BLIND_HARDWARE_NW", "BLIND_HARDWARE_W", "UNIT_24G"):
                        self.assertTrue(any("2.4G" in t for t in sub_texts), (cname, mode, sub_texts))
                    if cname == "UNIT_5G":
                        self.assertTrue(any("5G" in t for t in sub_texts), (cname, mode, sub_texts))

                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt_bb = str(it.get("text_or_asset") or "")
                                fs_bb = int(it.get("font_size_px") or 69)
                                pw, _ = step5_subtitles._measure_text_px(txt_bb, fp02, fs_bb)
                                self.assertLessEqual(pw, 640, (cname, mode, txt_bb, fs_bb, pw))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 4) Negative QA anti-bypass: unverified subtitle comma/period without transcript punct MUST FAIL, while true dangling-了 fails and SVO passes
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 4.5, "duration_sec": 4.5, "source_in_sec": 0.0, "source_out_sec": 4.5}],
                    "fps": 30, "total_duration_sec": 4.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")
            it10_qa_cases = [
                ([("我们去了三家门店", 0.0, 3.0)], ["我们去了，", "三家门店"], "FAIL"),
                ([("我们去了三家门店", 0.0, 3.0)], ["我们去了。", "三家门店"], "FAIL"),
                ([("我们在做TikTok运营", 0.0, 3.0)], ["我们在做TikT，", "ok运营"], "FAIL"),
                ([("我们在用USB-C接口", 0.0, 3.0)], ["我们在用USB-，", "C接口"], "FAIL"),
                ([("我刚买了两斤苹果", 0.0, 3.0)], ["我刚买了", "两斤苹果"], "FAIL"),
                ([("我们昨晚升级了智能仓储分拣系统", 0.0, 3.0)], ["我们昨晚升级了", "智能仓储分拣系统"], "FAIL"),
                ([("刚开业就火了", 0.0, 1.5), ("顾客排队打卡", 1.58, 3.2)], ["刚开业就火了", "顾客排队打卡"], "PASS"),
                ([("刚才已经说清楚了", 0.0, 1.6), ("客户提出质疑", 1.68, 3.3)], ["刚才已经说清楚了", "客户提出质疑"], "PASS"),
            ]
            for s_rows, parts, expect_status in it10_qa_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 4.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": f"s{k if len(s_rows) > 1 else 0}", "text": sp, "start_sec": round(k * 2.0, 2), "end_sec": round(k * 2.0 + (1.9 if len(s_rows) == 1 else 1.5), 2), "suppressed": False}
                            for k, sp in enumerate(parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (s_rows, parts, got))

    def test_iteration11_gate10_remediation(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) Case-insensitive video/bitrate/camera/hardware ASCII units preserved while keeping iPhone16/GPT4/USB-C/iOS 18 intact
        for tok_in, expected_tok in (
            ("支持60FPS和4K录制", "60FPS"),
            ("支持60FPS和4K录制", "4K"),
            ("跑稳100Mbps码率", "100Mbps"),
            ("支持2.5Gbps和128Kbps传输", "2.5Gbps"),
            ("支持2.5Gbps和128Kbps传输", "128Kbps"),
            ("支持1080p和720p推流", "1080p"),
            ("支持1080p和720p推流", "720p"),
            ("主摄升级到48MP传感器", "48MP"),
            ("延迟低至200MS，屏幕100PX，噪音80DB，电池5000MAH", "200MS"),
            ("延迟低至200MS，屏幕100PX，噪音80DB，电池5000MAH", "100PX"),
            ("延迟低至200MS，屏幕100PX，噪音80DB，电池5000MAH", "80DB"),
            ("延迟低至200MS，屏幕100PX，噪音80DB，电池5000MAH", "5000MAH"),
            ("搭载4nm芯片与24bit音频，峰值1000nits和300dpi", "4nm"),
            ("搭载4nm芯片与24bit音频，峰值1000nits和300dpi", "24bit"),
            ("搭载4nm芯片与24bit音频，峰值1000nits和300dpi", "1000nits"),
            ("搭载4nm芯片与24bit音频，峰值1000nits和300dpi", "300dpi"),
            ("配备100Wh电源与200mA电流和12kV防护", "100Wh"),
            ("配备100Wh电源与200mA电流和12kV防护", "200mA"),
            ("配备100Wh电源与200mA电流和12kV防护", "12kV"),
            ("我们在用iPhone16和GPT4连USB-C跑iOS 18", "iPhone16"),
            ("我们在用iPhone16和GPT4连USB-C跑iOS 18", "GPT4"),
            ("我们在用iPhone16和GPT4连USB-C跑iOS 18", "USB-C"),
        ):
            toks = step5_subtitles.tokenize_semantic_words(tok_in)
            self.assertIn(expected_tok, toks, (tok_in, toks))

        # 2) Jieba 'mq' non-numeral noun normalization ("零件") & structural SVO / predicative-"的" parity
        part_pairs = step5_subtitles._pseg_cut_clean("全自动精密零件")
        self.assertEqual(part_pairs[-1], ("零件", "n"), part_pairs)
        self.assertTrue(step5_subtitles.is_compound_np_body("全自动精密零件", prev_plain="昨晚采购了"))
        self.assertFalse(step5_subtitles.is_independent_svo_clause("全自动精密零件", prev_plain="昨晚采购了"))
        self.assertEqual(
            len(step5_subtitles.rebalance_asr_sentences([
                {"id": "s0", "text": "昨晚采购了", "start_sec": 0.0, "end_sec": 1.4, "words": []},
                {"id": "s1", "text": "全自动精密零件", "start_sec": 1.48, "end_sec": 3.2, "words": []},
            ], max_chars=9)),
            1,
        )

        for p_txt, c_txt in (
            ("昨天出故障了", "工厂生产零件"),
            ("刚开业就爆单了", "老客户直接转发推荐"),
            ("昨晚突然停电了", "系统自动生成报表"),
            ("今天彻底断货了", "官网下架商品"),
            ("机器突然死机了", "工厂停产配件"),
            ("刚刚到货了", "仓库清点货物"),
            ("前面堵车了", "交警疏导交通"),
            ("这是我去年花三百万从德国买回来的", "价格便宜很多"),
            ("这是我去年买的", "屏幕特别清晰"),
        ):
            reb = step5_subtitles.rebalance_asr_sentences([
                {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 1.5, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": 1.56, "end_sec": 3.2, "words": []},
            ], max_chars=9)
            self.assertEqual(len(reb), 2, (p_txt, c_txt, reb))

        # 3) 4-mode end-to-end M1 QA pass on all Gate 10 corpora
        cam_sents = [
            ("拍视频想要画面干净，参数设置很关键。", 0.0, 2.5),
            ("第一、机位必须支持60FPS流畅录制，", 2.6, 5.2),
            ("第二、无线图传要跑稳100Mbps码率，", 5.3, 8.0),
            ("第三、主摄至少配48MP高清传感器。", 8.1, 10.8),
        ]
        corpora_it11 = (
            ("BLIND_CAMERA_NW", {"sentences": [{"id": f"s{i}", "text": t, "start_sec": st, "end_sec": en, "words": []} for i, (t, st, en) in enumerate(cam_sents)], "locked_duration_sec": 11.3}),
            ("BLIND_CAMERA_W", _build_audio_events(cam_sents)),
            ("ADV_UNIT_FPS_UPPER", _build_audio_events([("这台相机支持60FPS和4K高清录制。", 0.0, 3.0)])),
            ("TARG_ASCII_100MBPS_SPLIT", _build_audio_events([("无线图传必须支持100Mbps稳定码率", 0.0, 3.0)])),
            ("ADV_1080P", _build_audio_events([("新款相机支持1080p高清视频录制", 0.0, 3.0)])),
            ("UNIT_720P", _build_audio_events([("新版软件支持720p高清推流", 0.0, 3.0)])),
            ("UNIT_MBPS", _build_audio_events([("路由器全系支持500Mbps高速宽带", 0.0, 3.0)])),
            ("UNIT_48MP", _build_audio_events([("主摄升级到48MP高清传感器", 0.0, 3.0)])),
            ("BLIND_ECOMMERCE_NW", {"sentences": [{"id": "s0", "text": "直播间刚开业就爆单了", "start_sec": 0.0, "end_sec": 2.0, "words": []}, {"id": "s1", "text": "老客户直接转发推荐给身边朋友。", "start_sec": 2.08, "end_sec": 4.8, "words": []}], "locked_duration_sec": 5.3}),
            ("BLIND_ECOMMERCE_W", _build_audio_events([("直播间刚开业就爆单了", 0.0, 2.0), ("老客户直接转发推荐给身边朋友。", 2.08, 4.8)])),
            ("ADV_UNSEEN_NP_PART", _build_audio_events([("昨晚采购了", 0.0, 1.4), ("全自动精密零件", 1.48, 3.2)])),
            ("ADV_UNSEEN_SVO_FACTORY", _build_audio_events([("昨天出故障了", 0.0, 1.4), ("工厂生产零件", 1.48, 3.2)])),
            ("ADV_SVO_BAODAN_4TOK", _build_audio_events([("刚开业就爆单了", 0.0, 1.5), ("老客户直接转发推荐", 1.58, 3.4)])),
            ("ADV_SVO_TINGDIAN_4TOK", _build_audio_events([("昨晚突然停电了", 0.0, 1.5), ("系统自动生成报表", 1.58, 3.4)])),
            ("ADV_SVO_DUANHUO_SUFFIX", _build_audio_events([("今天彻底断货了", 0.0, 1.5), ("官网下架商品", 1.58, 3.2)])),
            ("ADV_SVO_SIJI_SUFFIX", _build_audio_events([("机器突然死机了", 0.0, 1.5), ("工厂停产配件", 1.58, 3.2)])),
            ("SVO_WAREHOUSE", _build_audio_events([("刚刚到货了", 0.0, 1.4), ("仓库清点货物", 1.48, 3.1)])),
            ("SVO_TRAFFIC", _build_audio_events([("前面堵车了", 0.0, 1.4), ("交警疏导交通", 1.48, 3.1)])),
            ("ADV_DE_LONG_CLAUSE", _build_audio_events([("这是我去年花三百万从德国买回来的", 0.0, 3.6), ("价格便宜很多", 3.66, 5.2)])),
            ("ADV_DE_ADJ_CLAUSE", _build_audio_events([("这是我去年买的", 0.0, 1.5), ("屏幕特别清晰", 1.56, 3.2)])),
            ("BLIND_CANTO_DIMSUM_NW", {"sentences": [{"id": "s0", "text": "我系阿文我", "start_sec": 0.0, "end_sec": 1.2, "words": []}, {"id": "s1", "text": "喺佛山开咗一间三百平嘅手工点心铺。", "start_sec": 1.28, "end_sec": 4.8, "words": []}], "locked_duration_sec": 5.3}),
            ("BLIND_CANTO_DIMSUM_W", _build_audio_events([("我系阿文我", 0.0, 1.2), ("喺佛山开咗一间三百平嘅手工点心铺。", 1.28, 4.8)])),
        )
        for cname, audio in corpora_it11:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    _assert_entries_clean(self, res["entries"], f"{cname}/{mode}")
                    sub_texts = [e["text"] for e in res["entries"]]
                    if cname in ("BLIND_CANTO_DIMSUM_NW", "BLIND_CANTO_DIMSUM_W"):
                        for t_sub in sub_texts:
                            self.assertGreater(len(re.sub(r"[\s，。！？、；：,.!?;:]+", "", t_sub)), 3, (cname, mode, sub_texts))
                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt_bb = str(it.get("text_or_asset") or "")
                                fs_bb = int(it.get("font_size_px") or 69)
                                pw, _ = step5_subtitles._measure_text_px(txt_bb, fp02, fs_bb)
                                self.assertLessEqual(pw, 640, (cname, mode, txt_bb, fs_bb, pw))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 4) Negative QA checks including auditor 'i'/'l' idiom head ("核心的 | 问题所在")
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 4.5, "duration_sec": 4.5, "source_in_sec": 0.0, "source_out_sec": 4.5}],
                    "fps": 30, "total_duration_sec": 4.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")
            it11_qa_cases = [
                ([("核心的问题所在", 0.0, 3.0)], ["核心的", "问题所在"], "FAIL"),
                ([("最关键的原因是这里", 0.0, 3.0)], ["最关键的", "原因是这里"], "FAIL"),
                ([("真正核心的问题在这里", 0.0, 3.0)], ["真正核心的", "问题在这里"], "FAIL"),
                ([("我们去了三家门店", 0.0, 3.0)], ["我们去了，", "三家门店"], "FAIL"),
                ([("我们去了三家门店", 0.0, 3.0)], ["我们去了", "三家门店"], "FAIL"),
                ([("我们在用USB-C接口", 0.0, 3.0)], ["我们在用USB-", "C接口"], "FAIL"),
                ([("我们在用iPhone16拍视频", 0.0, 3.0)], ["我们在用iPhone", "16拍视频"], "FAIL"),
                ([("8:30准时开播", 0.0, 3.0)], ["8:", "30准时开播"], "FAIL"),
                ([("支持1080p高清视频", 0.0, 3.0)], ["支持1080", "p高清视频"], "FAIL"),
                ([("支持500Mbps高速宽带", 0.0, 3.0)], ["支持500", "Mbps高速宽带"], "FAIL"),
                ([("主摄升级到48MP高清传感器", 0.0, 3.0)], ["主摄升级到48", "MP高清传感器"], "FAIL"),
                ([("昨天出故障了", 0.0, 1.5), ("工厂生产零件", 1.56, 3.2)], ["昨天出故障了", "工厂生产零件"], "PASS"),
                ([("这是我去年买的", 0.0, 1.5), ("屏幕特别清晰", 1.56, 3.2)], ["这是我去年买的", "屏幕特别清晰"], "PASS"),
                ([("这是我去年花三百万从德国买回来的", 0.0, 2.5), ("价格便宜很多", 2.56, 4.2)], ["从德国买回来的", "价格便宜很多"], "PASS"),
            ]
            for s_rows, parts, expect_status in it11_qa_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 4.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": f"s{k if len(s_rows) > 1 else 0}", "text": sp, "start_sec": round(k * 2.0, 2), "end_sec": round(k * 2.0 + (1.9 if len(s_rows) == 1 else 1.5), 2), "suppressed": False}
                            for k, sp in enumerate(parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (s_rows, parts, got))

    def test_iteration12_post_audit_remediation(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) Auditor Check 6 #1: [NP+的] vs [VP+的] & [v-noun + deg + a]
        dp_540 = [t for _, _, t in step5_subtitles.dp_segment_short_lines("我们店里的生意很好", max_width_px=540, max_chars=9)]
        self.assertNotEqual(dp_540, ["我们店里的", "生意很好"], dp_540)
        self.assertTrue(step5_subtitles._is_v_noun_deg_predicate("生意很好"))
        self.assertFalse(step5_subtitles._is_v_noun_deg_predicate("卖得很好"))

        # 2) Auditor Check 6 #2 & #3: legacy 3-split, num_anchor_dim_overlay, adaptive bridge_bar
        self.assertEqual(
            kinetic_choreographer._split_into_n_at_word_bounds("支持120FPS 8K与500Kbps", 3),
            ["支持120FPS", "8K", "与500Kbps"],
        )
        self.assertEqual(
            kinetic_choreographer._split_bridge_phrases_adaptive("采购了精密工业组件", target_min=4, target_max=9, font_path=fp02),
            ["采购了精密工业组件"],
        )
        self.assertEqual(
            kinetic_choreographer._split_bridge_phrases_adaptive("开咗一间两百平嘅日式拉面馆", target_min=4, target_max=9, font_path=fp02),
            ["开咗一间", "两百平嘅日式拉面馆"],
        )
        self.assertEqual(
            kinetic_choreographer._split_bridge_phrases_adaptive("我喺佛山开咗一间三百平嘅手工点心铺", target_min=4, target_max=9, font_path=fp02),
            ["我喺佛山", "开咗一间", "三百平嘅手工点心铺"],
        )
        self.assertEqual(kinetic_choreographer._count_genitive_or_tail_breaks(["虽然人工成本高咗好多", "但系回头客一直好稳定"]), 0)
        self.assertEqual(kinetic_choreographer._count_genitive_or_tail_breaks(["店里面摆好多", "法式羊角面包"]), 1)
        self.assertEqual(kinetic_choreographer._count_genitive_or_tail_breaks(["我们今天买了好多", "新鲜食材"]), 1)

        # 3) Reviewer & Challenger 3: contiguous alphanumeric tokens & _NUM_UNIT_PRESPLIT_RE
        self.assertEqual(
            [m.group(0) for m in step5_subtitles._NUM_UNIT_PRESPLIT_RE.finditer("支持60FPS和4K录制")],
            ["60FPS", "4K"],
        )
        self.assertEqual(
            [m.group(0) for m in step5_subtitles._NUM_UNIT_PRESPLIT_RE.finditer("支持1080p60和4K60升格录制")],
            ["1080p60", "4K60"],
        )
        for txt, expected_tok in (
            ("这台新主机支持3D建模和实时渲染", "3D"),
            ("机器24h连续运行每隔30s自动上报10MB日志。", "30s"),
            ("系统支持全天候24h实时监控报警", "24h"),
            ("每个数据包固定512B高速校验写入", "512B"),
            ("支持1080p60帧率丝滑录制", "1080p60"),
            ("相机支持4K60高清稳定输出", "4K60"),
            ("我们在用iPhone16和GPT4连USB-C跑iOS 18", "iPhone16"),
            ("我们在用iPhone16和GPT4连USB-C跑iOS 18", "GPT4"),
            ("我们在用iPhone16和GPT4连USB-C跑iOS 18", "USB-C"),
        ):
            toks = step5_subtitles.tokenize_semantic_words(txt)
            self.assertIn(expected_tok, toks, (txt, toks))

        # 4) Challenger 4: 6 silent cross-clause false-merge pairs stay 2 sentences
        c4_pairs = [
            ("刚才崩溃了", "门店重启收银系统"),
            ("系统崩溃了", "技术重启服务器"),
            ("今天刚买的", "手机就卡了"),
            ("昨晚刚装的", "软件就崩了"),
            ("这是我去年买的", "味道非常正宗"),
            ("这就是大家担心的", "后续维护很麻烦"),
        ]
        for p_txt, c_txt in c4_pairs:
            reb = step5_subtitles.rebalance_asr_sentences(
                [
                    {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 1.5, "words": []},
                    {"id": "s1", "text": c_txt, "start_sec": 1.56, "end_sec": 3.2, "words": []},
                ],
                max_chars=9,
            )
            self.assertEqual(len(reb), 2, (p_txt, c_txt, reb))

        # 5) 4-mode end-to-end M1 QA pass on Auditor Check 6, Reviewer 6 alphanumeric, and Challenger 1-4 corpora
        canto_svc = [("虽然人工成本高咗好多，但系回头客一直好稳定，", 0.0, 4.0)]
        ecom_ops = [
            ("很多人以为这是流量红利，其实核心在于我们提前采购了", 0.0, 3.0),
            ("全自动精密零件，把售后返修率压到了百分之一以下。", 3.08, 6.2),
        ]
        corpora_it12 = (
            ("AUD_DIANLI", _build_audio_events([("我们店里的生意很好。", 0.0, 2.5)])),
            ("AUD_120FPS_8K", _build_audio_events([("支持120FPS 8K与500Kbps。", 0.0, 3.0)])),
            ("AUD_JINGMI", _build_audio_events([("采购了精密工业组件。", 0.0, 2.5)])),
            ("AUD_LAMIAN", _build_audio_events([("开咗一间两百平嘅日式拉面馆。", 0.0, 3.2)])),
            ("REV_BOUNDARY_3D", _build_audio_events([("这台新主机支持3D建模和实时渲染", 0.0, 3.0)])),
            ("REV_BOUNDARY_1080P60", _build_audio_events([("支持1080p60帧率丝滑录制", 0.0, 3.0)])),
            ("REV_BOUNDARY_4K60", _build_audio_events([("相机支持4K60高清稳定输出", 0.0, 3.0)])),
            ("REV_ADV_24H_30S", _build_audio_events([("机器24h连续运行每隔30s自动上报10MB日志。", 0.0, 4.0)])),
            ("REV_ADV_24H_MID", _build_audio_events([("系统支持全天候24h实时监控报警", 0.0, 3.0)])),
            ("REV_ADV_512B_MID", _build_audio_events([("每个数据包固定512B高速校验写入", 0.0, 3.0)])),
            ("ATK_3D", _build_audio_events([("新款设备支持3D结构光人脸识别。", 0.0, 3.0)])),
            ("ATK_1080P60", _build_audio_events([("相机最高支持1080p60和4K60升格录制。", 0.0, 3.2)])),
            ("STRESS_CANTO_SERVICE_W", _build_audio_events(canto_svc)),
            ("STRESS_CANTO_SERVICE_NW", {"sentences": [{"id": "s0", "text": canto_svc[0][0], "start_sec": 0.0, "end_sec": 4.0, "words": []}], "locked_duration_sec": 4.5}),
            ("STRESS_ECOMMERCE_OPS_W", _build_audio_events(ecom_ops)),
            ("STRESS_ECOMMERCE_OPS_NW", {"sentences": [{"id": f"s{i}", "text": t, "start_sec": st, "end_sec": en, "words": []} for i, (t, st, en) in enumerate(ecom_ops)], "locked_duration_sec": 6.7}),
            ("C4_LE_BENGKUI_SYS", _build_audio_events([("刚才崩溃了", 0.0, 1.5), ("门店重启收银系统", 1.56, 3.2)])),
            ("C4_LE_BENGKUI_SRV", _build_audio_events([("系统崩溃了", 0.0, 1.5), ("技术重启服务器", 1.56, 3.2)])),
            ("C4_DE_KA_LE", _build_audio_events([("今天刚买的", 0.0, 1.5), ("手机就卡了", 1.56, 3.2)])),
            ("C4_DE_BENG_LE", _build_audio_events([("昨晚刚装的", 0.0, 1.5), ("软件就崩了", 1.56, 3.2)])),
            ("C4_DE_ZHENGZONG", _build_audio_events([("这是我去年买的", 0.0, 1.5), ("味道非常正宗", 1.56, 3.2)])),
            ("C4_DE_MAFAN", _build_audio_events([("这就是大家担心的", 0.0, 1.5), ("后续维护很麻烦", 1.56, 3.2)])),
        )
        for cname, audio in corpora_it12:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    _assert_entries_clean(self, res["entries"], f"{cname}/{mode}")
                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt_bb = str(it.get("text_or_asset") or "")
                                fs_bb = int(it.get("font_size_px") or 69)
                                pw, _ = step5_subtitles._measure_text_px(txt_bb, fp02, fs_bb)
                                self.assertLessEqual(pw, 640, (cname, mode, txt_bb, fs_bb, pw))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 6) Negative QA probes: Check 6 #1/#2/#3 & Challenge 1 quantifiers
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 4.5, "duration_sec": 4.5, "source_in_sec": 0.0, "source_out_sec": 4.5}],
                    "fps": 30, "total_duration_sec": 4.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")
            it12_sub_qa = [
                ([("店里的生意很好", 0.0, 3.0)], ["店里的", "生意很好"], "FAIL"),
                ([("我们店里的生意很好", 0.0, 3.0)], ["我们店里的", "生意很好"], "FAIL"),
                ([("这是我去年花三百万从德国买回来的", 0.0, 2.5), ("价格便宜很多", 2.56, 4.2)], ["从德国买回来的", "价格便宜很多"], "PASS"),
                ([("这是我去年买的", 0.0, 1.5), ("卖得很好", 1.56, 3.2)], ["这是我去年买的", "卖得很好"], "PASS"),
                ([("店里面摆好多法式羊角面包", 0.0, 3.0)], ["店里面摆好多", "法式羊角面包"], "FAIL"),
                ([("我们今天买了好多新鲜食材", 0.0, 3.0)], ["我们今天买了好多", "新鲜食材"], "FAIL"),
                ([("店里面摆好多，法式羊角面包", 0.0, 3.0)], ["店里面摆好多", "法式羊角面包"], "FAIL"),
                ([("我们今天买了好多，新鲜食材", 0.0, 3.0)], ["我们今天买了好多", "新鲜食材"], "FAIL"),
            ]
            for s_rows, parts, expect_status in it12_sub_qa:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 4.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": f"s{k if len(s_rows) > 1 else 0}", "text": sp, "start_sec": round(k * 2.0, 2), "end_sec": round(k * 2.0 + (1.9 if len(s_rows) == 1 else 1.5), 2), "suppressed": False}
                            for k, sp in enumerate(parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (s_rows, parts, got))

            # Synthetic fg_highlights ' 8K与' -> FAIL subtitle_segmentation_clean
            (pdir / "03_audio_events.json").write_text(json.dumps({"sentences": [{"id": "s0", "text": "支持120FPS 8K与500Kbps。", "start_sec": 0.0, "end_sec": 3.0}], "locked_duration_sec": 3.5}, ensure_ascii=False), encoding="utf-8")
            (pdir / "05_subtitles.json").write_text(json.dumps({"entries": [{"id": "sub_001", "sentence_id": "s0", "text": "支持120FPS", "start_sec": 0.0, "end_sec": 1.5, "suppressed": True, "suppress_reason": "kinetic_typography_stage"}, {"id": "sub_002", "sentence_id": "s0", "text": "8K与500Kbps", "start_sec": 1.55, "end_sec": 3.0, "suppressed": True, "suppress_reason": "kinetic_typography_stage"}]}, ensure_ascii=False), encoding="utf-8")
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_02", "sfx_cues": [], "kinetic_stage": {"enabled": True, "theme_profile": "style_02_unified", "items": [
                {"id": "ks_1", "group_id": "grp_00", "variant": "hero_white", "z_layer": "foreground", "text_or_asset": "支持120FPS", "start_sec": 0.0, "end_sec": 3.0, "x_norm": 0.0, "y_norm": -0.28},
                {"id": "ks_2", "group_id": "grp_00", "variant": "hero_lime", "z_layer": "foreground", "text_or_asset": " 8K与", "start_sec": 1.0, "end_sec": 3.0, "x_norm": 0.0, "y_norm": -0.41},
                {"id": "ks_3", "group_id": "grp_00", "variant": "red_pill", "z_layer": "foreground", "text_or_asset": "500Kbps", "start_sec": 2.0, "end_sec": 3.0, "x_norm": 0.0, "y_norm": -0.56},
            ]}}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            self.assertEqual({c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]["status"], "FAIL")

            # Synthetic bb_items '拉面馆' <=3 orphan -> FAIL ui_safe_zone
            (pdir / "03_audio_events.json").write_text(json.dumps({"sentences": [{"id": "s0", "text": "开咗一间两百平嘅日式拉面馆。", "start_sec": 0.0, "end_sec": 3.2}], "locked_duration_sec": 3.7}, ensure_ascii=False), encoding="utf-8")
            (pdir / "05_subtitles.json").write_text(json.dumps({"entries": [{"id": "sub_001", "sentence_id": "s0", "text": "开咗一间", "start_sec": 0.0, "end_sec": 1.0, "suppressed": True, "suppress_reason": "kinetic_typography_stage"}, {"id": "sub_002", "sentence_id": "s0", "text": "两百平嘅日式拉面馆", "start_sec": 1.05, "end_sec": 3.2, "suppressed": True, "suppress_reason": "kinetic_typography_stage"}]}, ensure_ascii=False), encoding="utf-8")
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_02", "sfx_cues": [], "kinetic_stage": {"enabled": True, "theme_profile": "style_02_unified", "items": [
                {"id": "ks_1", "group_id": "grp_00", "variant": "bridge_bar", "z_layer": "foreground", "text_or_asset": "开咗一间", "start_sec": 0.0, "end_sec": 1.0, "x_norm": 0.0, "y_norm": -0.52},
                {"id": "ks_2", "group_id": "grp_00", "variant": "bridge_bar", "z_layer": "foreground", "text_or_asset": "两百平嘅日式", "start_sec": 1.05, "end_sec": 2.2, "x_norm": 0.0, "y_norm": -0.52},
                {"id": "ks_3", "group_id": "grp_00", "variant": "bridge_bar", "z_layer": "foreground", "text_or_asset": "拉面馆", "start_sec": 2.25, "end_sec": 3.2, "x_norm": 0.0, "y_norm": -0.52},
            ]}}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            self.assertEqual({c["id"]: c for c in rep["checks"]}["ui_safe_zone"]["status"], "FAIL")

    def test_iteration13_morph_comma_haoduo_and_dotted_alnum(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) Morphological [j+ng], [stem+k], and style/spec bound suffixes without over-merging
        for sent, atomic_tok in (
            ("开咗一间百五平嘅广式烧腊店", "广式"),
            ("开咗一间百五平嘅川式火锅店", "川式"),
            ("做咗一套全自动式分拣线", "全自动式"),
            ("做了一个沉浸式体验展厅", "沉浸式"),
            ("买了一台旗舰款手机真不错", "旗舰款"),
            ("我们选了基础版套餐很划算", "基础版"),
            ("换了一套工业级芯片效率高", "工业级"),
            ("老板们都在看这个方案", "老板们"),
        ):
            toks = step5_subtitles.tokenize_semantic_words(sent)
            self.assertIn(atomic_tok, toks, (sent, toks))
            dp540 = "|".join(t for _, _, t in step5_subtitles.dp_segment_short_lines(sent, max_width_px=540, max_chars=9))
            self.assertIn(atomic_tok, dp540, (sent, dp540))
            br = "|".join(kinetic_choreographer._split_bridge_phrases_adaptive(sent, target_min=4, target_max=9, font_path=fp02))
            self.assertIn(atomic_tok, br, (sent, br))

        self.assertNotIn("北京风", step5_subtitles.tokenize_semantic_words("北京风很大今天出门小心"))
        self.assertNotIn("个型", step5_subtitles.tokenize_semantic_words("他是个型男很有气质"))
        self.assertNotIn("有型", step5_subtitles.tokenize_semantic_words("我们团队里的人很有型"))
        self.assertNotIn("这个款", step5_subtitles.tokenize_semantic_words("这个款很好看"))
        self.assertNotIn("十个款", step5_subtitles.tokenize_semantic_words("有十个款"))

        # 2) rebalance_asr_sentences len<=44 comma split + silent cross-clause comma guards
        for p_txt, c_txt in (
            ("其实核心在于我们昨天升级了", "全自动智能仓储系统，把发货出错率压到了千分之一以下。"),
            ("其实昨天我们采购了", "全自动精密零件，把售后返修率压到了百分之一以下。"),
            ("很多同行不知道，我们昨天采购了", "全自动精密零件，把售后返修率压到了百分之一以下。"),
        ):
            reb = step5_subtitles.rebalance_asr_sentences([
                {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 1.6, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": 1.68, "end_sec": 3.8, "words": []},
            ], max_chars=9)
            self.assertEqual(len(reb), 2, (p_txt, c_txt, reb))
            self.assertTrue(reb[0]["text"].endswith("，"), (p_txt, c_txt, reb))

        for p_txt, c_txt in (
            ("今天刚买的", "手机就卡了，根本没法用。"),
            ("昨晚刚装的", "软件就崩了，没法继续跑。"),
            ("这是我去年买的", "味道非常正宗，回头客特别多。"),
            ("这就是大家担心的", "后续维护很麻烦，成本也高。"),
            ("刚才崩溃了", "门店重启收银系统，才恢复正常。"),
            ("系统崩溃了", "技术重启服务器，折腾了半小时。"),
        ):
            reb = step5_subtitles.rebalance_asr_sentences([
                {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 1.5, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": 1.56, "end_sec": 3.4, "words": []},
            ], max_chars=9)
            self.assertEqual(len(reb), 2, (p_txt, c_txt, reb))
            self.assertEqual(reb[0]["text"], p_txt, (p_txt, c_txt, reb))

        # 3) [V + 了/咗/得 + 好多] before pronoun/SVO vs bare NP quantifier
        for cp, np in (
            ("成本直接降了好多", "我们利润才稳得住"),
            ("门店营业额涨了好多", "我们终于看到回头钱"),
            ("每月开销省了好多", "大家奖金也跟着涨了"),
            ("现在效率提了好多", "佢哋都轻松咗"),
        ):
            self.assertTrue(step5_subtitles.is_degree_complement_haoduo(cp, np), (cp, np))
        for cp, np in (
            ("店里面摆好多", "法式羊角面包"),
            ("我们今天买了好多", "新鲜食材"),
            ("我们今天买了好多", "我们的工具"),
            ("每月省了好多", "优质食材"),
            ("每月省了好多", "成本和人工"),
            ("今年赚了好多", "纯利润"),
            ("我们买了好多", "大家庭用的食材"),
        ):
            self.assertFalse(step5_subtitles.is_degree_complement_haoduo(cp, np), (cp, np))

        # 4) Dotted ASCII tokens & shared unit regex accuracy
        for raw_s, exp_clean, tok_keep in (
            ("机器升级到USB3.2接口搭配H.265和Wi-Fi 7芯片。", "机器升级到USB3.2接口搭配H.265和Wi-Fi 7芯片", "USB3.2"),
            ("机器升级到USB3.2接口搭配H.265和Wi-Fi 7芯片。", "机器升级到USB3.2接口搭配H.265和Wi-Fi 7芯片", "H.265"),
            ("主板支持PCIe4.0固态硬盘高速直连传输。", "主板支持PCIe4.0固态硬盘高速直连传输", "PCIe4.0"),
            ("今天发布的v1.5版本修复了所有已知内存泄漏。", "今天发布的v1.5版本修复了所有已知内存泄漏", "v1.5"),
            ("支持HTTP2.0和iOS18.2系统升级。", "支持HTTP2.0和iOS18.2系统升级", "HTTP2.0"),
            ("支持HTTP2.0和iOS18.2系统升级。", "支持HTTP2.0和iOS18.2系统升级", "iOS18.2"),
        ):
            self.assertEqual(step5_subtitles.strip_display_punctuation(raw_s), exp_clean)
            self.assertEqual(kinetic_choreographer.clean_display_text(raw_s), exp_clean)
            self.assertIn(tok_keep, step5_subtitles.tokenize_semantic_words(raw_s))

        self.assertEqual(
            [m.group(0) for m in step5_subtitles._NUM_UNIT_PRESPLIT_RE.finditer("100米步行两分钟就到")],
            ["100米", "两分钟"],
        )
        self.assertEqual(
            [m.group(0) for m in kinetic_choreographer._NUM_UNIT_RE.finditer("全程开启24度空调以110km时速跑了四百公里，")],
            ["24度", "110km", "四百公里"],
        )
        for u_s, u_exp in (
            ("30天", ["30天"]), ("500克", ["500克"]), ("100米步行", ["100米"]),
            ("15分钟", ["15分钟"]), ("6000+", ["6000+"]),
            ("USB3.2", []), ("PCIe4.0", []), ("v1.5", []), ("H.265", []),
        ):
            self.assertEqual([m.group(0) for m in kinetic_choreographer._NUM_UNIT_RE.finditer(u_s)], u_exp, u_s)

        # 5) 4-mode end-to-end M1 QA pass on all Iteration 13 corpora (including gi >= 2 num_anchor_dim_overlay)
        le39_sents = [
            ("其实核心在于我们昨天升级了", 0.0, 1.8),
            ("全自动智能仓储系统，把发货出错率压到了千分之一以下。", 1.88, 5.2),
        ]
        le42_sents = [
            ("其实昨天我们采购了", 0.0, 1.5),
            ("全自动精密零件，把售后返修率压到了百分之一以下。", 1.58, 4.8),
        ]
        both_comma_44 = [
            ("很多同行不知道，我们昨天采购了", 0.0, 2.0),
            ("全自动精密零件，把售后返修率压到了百分之一以下。", 2.08, 5.4),
        ]
        d1_ev_sents = [
            ("今天我们实测新款纯电车型续航。", 0.0, 2.5),
            ("城市道路表显电耗非常稳定。", 2.8, 5.0),
            ("全程开启24度空调以110km时速跑了四百公里，", 5.3, 8.8),
            ("15分钟就能把电量从百分之二十充到百分之八十。", 9.1, 12.6),
        ]
        usb32_gi2_sents = [
            ("今天这台机器全面升级核心配置。", 0.0, 2.4),
            ("散热模组和供电电路全部重新设计。", 2.7, 5.1),
            ("机器升级到USB3.2接口搭配H.265和Wi-Fi 7芯片。", 5.4, 9.2),
        ]
        corpora_it13 = (
            ("MORPH_GUANGSHI", _build_audio_events([("开咗一间百五平嘅广式烧腊店。", 0.0, 3.2)])),
            ("MORPH_CHUANSHI", _build_audio_events([("开咗一间百五平嘅川式火锅店。", 0.0, 3.2)])),
            ("MORPH_QUANZIDONGSHI", _build_audio_events([("做咗一套全自动式分拣线。", 0.0, 3.0)])),
            ("MORPH_SPEC_PACK", _build_audio_events([("买了一台旗舰款手机，选了基础版套餐，换了一套工业级芯片。", 0.0, 4.8)])),
            ("LE_39_COMMA_W", _build_audio_events(le39_sents)),
            ("LE_39_COMMA_NW", {"sentences": [{"id": f"s{i}", "text": t, "start_sec": st, "end_sec": en, "words": []} for i, (t, st, en) in enumerate(le39_sents)], "locked_duration_sec": 5.7}),
            ("LE_42_NO_P_COMMA_W", _build_audio_events(le42_sents)),
            ("BOTH_COMMA_LE_44", _build_audio_events(both_comma_44)),
            ("ATK_HAODUO_JIANG", _build_audio_events([("成本直接降了好多，我们利润才稳得住。", 0.0, 3.5)])),
            ("HAODUO_ZHANG", _build_audio_events([("门店营业额涨了好多，我们终于看到回头钱。", 0.0, 3.6)])),
            ("HAODUO_SHENG", _build_audio_events([("每月开销省了好多，大家奖金也跟着涨了。", 0.0, 3.6)])),
            ("CANTO_TI_HAODUO", _build_audio_events([("现在效率提了好多，佢哋都轻松咗。", 0.0, 3.2)])),
            ("ATK_USB32_W", _build_audio_events([("机器升级到USB3.2接口搭配H.265和Wi-Fi 7芯片。", 0.0, 4.2)])),
            ("ATK_USB32_NW", {"sentences": [{"id": "s0", "text": "机器升级到USB3.2接口搭配H.265和Wi-Fi 7芯片。", "start_sec": 0.0, "end_sec": 4.2, "words": []}], "locked_duration_sec": 4.7}),
            ("PCIE40_W", _build_audio_events([("主板支持PCIe4.0固态硬盘高速直连传输。", 0.0, 3.6)])),
            ("V15_W", _build_audio_events([("今天发布的v1.5版本修复了所有已知内存泄漏。", 0.0, 3.8)])),
            ("HTTP20_IOS182", _build_audio_events([("支持HTTP2.0和iOS18.2系统升级。", 0.0, 3.4)])),
            ("D1_EV_AUTO_W", _build_audio_events(d1_ev_sents)),
            ("D1_EV_AUTO_NW", {"sentences": [{"id": f"s{i}", "text": t, "start_sec": st, "end_sec": en, "words": []} for i, (t, st, en) in enumerate(d1_ev_sents)], "locked_duration_sec": 13.1}),
            ("USB32_GI2", _build_audio_events(usb32_gi2_sents)),
            ("NUM_30TIAN_GI2", _build_audio_events([("我们先看第一阶段。", 0.0, 2.0), ("团队集中梳理核心流程。", 2.3, 4.6), ("仅用30天就完成了全面上线。", 4.9, 7.8)])),
            ("NUM_500KE_GI2", _build_audio_events([("先选好当天食材。", 0.0, 2.0), ("控制好火候和腌制时间。", 2.3, 4.6), ("每份足足500克牛肉而且现切现炒。", 4.9, 8.0)])),
            ("NUM_100MI_GI2", _build_audio_events([("这家店交通很方便。", 0.0, 2.0), ("就在主干道转角位置。", 2.3, 4.6), ("出地铁口100米步行两分钟就到。", 4.9, 7.9)])),
        )
        for cname, audio in corpora_it13:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    _assert_entries_clean(self, res["entries"], f"{cname}/{mode}")
                    sub_joined = "|".join(e["text"] for e in res["entries"])
                    if cname == "ATK_USB32_W":
                        self.assertIn("USB3.2", sub_joined)
                        self.assertIn("H.265", sub_joined)
                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        stage_joined = "|".join(str(it.get("text_or_asset") or "") for it in st["items"])
                        if cname == "ATK_HAODUO_JIANG":
                            self.assertNotIn("降了好多我们", stage_joined, (mode, stage_joined))
                        elif cname == "HAODUO_ZHANG":
                            self.assertNotIn("涨了好多我们", stage_joined, (mode, stage_joined))
                        elif cname in ("ATK_USB32_W", "ATK_USB32_NW", "USB32_GI2"):
                            if st["items"]:
                                self.assertIn("USB3.2", stage_joined, (mode, stage_joined))
                                self.assertIn("H.265", stage_joined, (mode, stage_joined))
                            if mode == "legacy":
                                self.assertIn("USB3.2", stage_joined, (mode, stage_joined))
                                self.assertIn("H.265", stage_joined, (mode, stage_joined))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 6) Negative QA anti-bypass probes for Iteration 13
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 4.5, "duration_sec": 4.5, "source_in_sec": 0.0, "source_out_sec": 4.5}],
                    "fps": 30, "total_duration_sec": 4.5,
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_01", "sfx_cues": []}), encoding="utf-8")
            it13_neg_qa = [
                ([("开咗一间百五平嘅广式烧腊店", 0.0, 3.0)], ["开咗一间百五平嘅广", "式烧腊店"], "FAIL"),
                ([("开咗一间百五平嘅川式火锅店", 0.0, 3.0)], ["开咗一间百五平嘅川", "式火锅店"], "FAIL"),
                ([("做咗一套全自动式分拣线", 0.0, 3.0)], ["做咗一套全自动", "式分拣线"], "FAIL"),
                ([("买了一台旗舰款手机", 0.0, 3.0)], ["买了一台旗舰", "款手机"], "FAIL"),
                ([("我们选了基础版套餐", 0.0, 3.0)], ["我们选了基础", "版套餐"], "FAIL"),
                ([("每月省了好多，成本和人工", 0.0, 3.0)], ["每月省了好多", "成本和人工"], "FAIL"),
                ([("今年赚了好多，纯利润", 0.0, 3.0)], ["今年赚了好多", "纯利润"], "FAIL"),
                ([("我们买了好多，我们的工具", 0.0, 3.0)], ["我们买了好多", "我们的工具"], "FAIL"),
                ([("每月省了好多，优质食材", 0.0, 3.0)], ["每月省了好多", "优质食材"], "FAIL"),
                ([("我们买了好多，大家庭用的食材", 0.0, 3.0)], ["我们买了好多", "大家庭用的食材"], "FAIL"),
                ([("机器升级到USB3.2接口搭配", 0.0, 3.0)], ["机器升级到USB3", ".2接口搭配"], "FAIL"),
                ([("搭配H.265芯片", 0.0, 3.0)], ["搭配H", ".265芯片"], "FAIL"),
                ([("主板支持PCIe4.0固态硬盘", 0.0, 3.0)], ["主板支持PCIe4", ".0固态硬盘"], "FAIL"),
                ([("今天发布的v1.5版本", 0.0, 3.0)], ["今天发布的v1", ".5版本"], "FAIL"),
                ([("全程开启24度空调", 0.0, 3.0)], ["全程开启24", "度空调"], "FAIL"),
                ([("15分钟就能充满", 0.0, 3.0)], ["15", "分钟就能充满"], "FAIL"),
                ([("方式很多而且很好用", 0.0, 3.0)], ["方式很多", "而且很好用"], "PASS"),
                ([("式样很多任你挑选", 0.0, 3.0)], ["式样很多", "任你挑选"], "PASS"),
            ]
            for s_rows, parts, expect_status in it13_neg_qa:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 4.5}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": "s0", "text": sp, "start_sec": round(k * 2.0, 2), "end_sec": round(k * 2.0 + 1.9, 2), "suppressed": False}
                            for k, sp in enumerate(parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (s_rows, parts, got))

    def test_iteration14_onechar_np_units_svo_and_fg_qa(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) auditor_m1_13 Check 6: 1-char pre-comma object nouns vs false-merge guards
        for p_txt, c_txt, expected_texts in (
            ("店里刚进了", "货，老板连夜清点库存。", ["店里刚进了货，", "老板连夜清点库存。"]),
            ("今天我买了", "菜，回家准备做晚饭。", ["今天我买了菜，", "回家准备做晚饭。"]),
            ("昨天他买了", "车，全家都特别开心。", ["昨天他买了车，", "全家都特别开心。"]),
            ("佢昨日买咗", "车，全家都好开心。", ["佢昨日买咗车，", "全家都好开心。"]),
            ("这是我昨天买的", "车，开起来很稳。", ["这是我昨天买的车，开起来很稳。"]),
        ):
            reb = step5_subtitles.rebalance_asr_sentences([
                {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 0.9, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": 0.95, "end_sec": 2.6, "words": []},
            ], max_chars=9)
            self.assertEqual([r["text"] for r in reb], expected_texts, (p_txt, c_txt, reb))

        for p_txt, c_txt in (
            ("今天我买了", "啊，真的非常划算。"),
            ("昨天他去了", "呢，结果还没开门。"),
            ("这是我去年买的", "吧，我也记不太清。"),
            ("刚才已经说完了", "对，就是这个意思。"),
            ("刚刚到货了", "看，大家都在清点。"),
            ("刚刚到货了", "车，已经停在门口。"),
        ):
            reb = step5_subtitles.rebalance_asr_sentences([
                {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 0.9, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": 0.95, "end_sec": 2.6, "words": []},
            ], max_chars=9)
            self.assertEqual(len(reb), 2, (p_txt, c_txt, reb))
            self.assertEqual(reb[0]["text"], p_txt, (p_txt, c_txt, reb))

        # 2) challenger_m1_13 Challenge 1: 1-char nominal heads in is_compound_np_body
        for np_s in ("全自动分拣线", "全自动打包机", "无刷电机板", "老字号烧腊店", "半自动包装线", "冷链物流车"):
            self.assertTrue(step5_subtitles.is_compound_np_body(np_s), np_s)
        for non_np_s in ("我买了车", "手机卡了", "大家吃了饭", "门店清理货"):
            self.assertFalse(step5_subtitles.is_compound_np_body(non_np_s), non_np_s)

        # 3) challenger_m1_13 Challenge 2: single-source units & jieba straddle guard (including omitting "把")
        for txt_u, tok_u in (
            ("客厅装了1.5匹变频空调", "1.5匹"),
            ("搭配2000毫安传感器", "2000毫安"),
            ("每片500毫克配比", "500毫克"),
            ("含100微克叶酸", "100微克"),
            ("装了5千瓦电站", "5千瓦"),
            ("扩容10兆瓦机组", "10兆瓦"),
            ("接入35千伏变电站", "35千伏"),
            ("配备65英寸屏幕", "65英寸"),
            ("预留10英尺空间", "10英尺"),
            ("占地50公顷园区", "50公顷"),
            ("距离20海里海域", "20海里"),
            ("镶嵌2克拉钻戒", "2克拉"),
            ("采用100瓦灯泡", "100瓦"),
            ("稳定220伏电压", "220伏"),
            ("输出10安电流", "10安"),
            ("100米步行两分钟就到", "100米"),
            ("三秒之内搞定", "三秒"),
            ("二十八天省下百分之十五", "二十八天"),
            ("二十八天省下百分之十五", "百分之十五"),
        ):
            toks = step5_subtitles.tokenize_semantic_words(txt_u)
            self.assertIn(tok_u, toks, (txt_u, toks))
            spans_u = [txt_u[ms:me] for ms, me in step5_subtitles.iter_num_unit_spans(txt_u)]
            self.assertIn(tok_u, spans_u, (txt_u, spans_u))
            if tok_u not in ("三秒", "二十八天"):
                spans_kc = [txt_u[ms:me] for ms, me in step5_subtitles.iter_num_unit_spans(txt_u, pattern=kinetic_choreographer._NUM_UNIT_RE)]
                self.assertIn(tok_u, spans_kc, (txt_u, spans_kc))

        for ch_txt, ch_tok in (
            ("总共装了十五匹空调", "十五匹"),
            ("内置两千毫安电池", "两千毫安"),
            ("每粒五百毫克配方", "五百毫克"),
            ("屏幕足足六十五英寸", "六十五英寸"),
        ):
            spans_ch = [ch_txt[ms:me] for ms, me in step5_subtitles.iter_num_unit_spans(ch_txt, pattern=kinetic_choreographer._NUM_UNIT_RE)]
            self.assertIn(ch_tok, spans_ch, (ch_txt, spans_ch))

        self.assertTrue(any("3个" in t for t in step5_subtitles.tokenize_semantic_words("3个人")))
        for txt_strad, bad_strad in (
            ("一把抓到底", "一把"),
            ("一口气做完", "一口"),
            ("一头雾水完全不懂", "一头"),
            ("2024安全第一", "2024安"),
            ("2024把握机会", "2024把"),
            ("2024支持国产", "2024支"),
            ("2024节目丰富", "2024节"),
            ("2024口碑爆棚", "2024口"),
            ("花了300把店盘下来", "300把"),
        ):
            toks_s = step5_subtitles.tokenize_semantic_words(txt_strad)
            self.assertNotIn(bad_strad, toks_s, (txt_strad, toks_s))
            spans_s = [txt_strad[ms:me] for ms, me in step5_subtitles.iter_num_unit_spans(txt_strad)]
            self.assertNotIn(bad_strad, spans_s, (txt_strad, spans_s))

        # 4) challenger_m1_13 Challenge 4: [vn + v + n] SVO guard & DP "好多，" handling
        for np_svo_false in ("进口冷冻牛排", "出口包装食品", "统一配送货品", "批量采购原料", "限时折扣商品"):
            self.assertFalse(step5_subtitles.is_independent_svo_clause(np_svo_false), np_svo_false)
        self.assertFalse(step5_subtitles.is_degree_complement_haoduo("店里省了好多", "进口冷冻牛排"))
        self.assertFalse(step5_subtitles._is_pronoun_led_clause("大家奖金"))
        self.assertTrue(step5_subtitles._is_pronoun_led_clause("大家奖金也跟着涨了"))
        for svo_true in (
            "管理提升效率", "大家奖金也跟着涨了", "老板亲自下厨炒菜", "员工每天加班干活",
            "工厂生产零件", "官网下架商品", "工厂停产配件", "仓库清点货物", "交警疏导交通", "技术重启服务器", "门店清理货",
        ):
            self.assertTrue(step5_subtitles.is_independent_svo_clause(svo_true), svo_true)

        dp_hd = [t for _, _, t in step5_subtitles.dp_segment_short_lines("店里省了好多，进口冷冻牛排", 632, font_size=59, max_chars=9)]
        self.assertNotEqual(dp_hd[0], "店里省了好多", dp_hd)

        # 5) 4-mode end-to-end M1 QA pass on all Iteration 14 corpora
        home_reno_sents = [
            ("客厅装了法式复古吊灯和1.5匹变频空调，", 0.0, 2.8),
            ("全屋换装便携式储能电源搭配2000毫安传感器。", 3.0, 6.2),
        ]
        corpora_it14 = (
            ("AUD_HUO", _build_audio_events([("店里刚进了", 0.0, 0.9), ("货，老板连夜清点库存。", 0.95, 2.6)])),
            ("AUD_CAI", _build_audio_events([("今天我买了", 0.0, 0.9), ("菜，回家准备做晚饭。", 0.95, 2.6)])),
            ("AUD_CHE", _build_audio_events([("昨天他买了", 0.0, 0.9), ("车，全家都特别开心。", 0.95, 2.6)])),
            ("EDGE_ZUO_COMMA_LE44", _build_audio_events([("其实佢哋上个月升级咗", 0.0, 1.5), ("全自动分拣线，每日出货效率翻咗两倍。", 1.58, 4.2)])),
            ("MANDARIN_LE_FENJIANXIAN", _build_audio_events([("其实我们上个月升级了", 0.0, 1.5), ("全自动分拣线，每日出货效率翻了两倍。", 1.58, 4.2)])),
            ("BLIND_HOME_RENO_W", _build_audio_events(home_reno_sents)),
            ("BLIND_HOME_RENO_NW", {"sentences": [{"id": f"s{i}", "text": t, "start_sec": st, "end_sec": en, "words": []} for i, (t, st, en) in enumerate(home_reno_sents)], "locked_duration_sec": 6.7}),
            ("BLIND_TECH_UNITS", _build_audio_events([("新电站并网接入35千伏线路，配置5千瓦逆变器与100瓦照明灯，", 0.0, 3.8), ("同时预留50公顷扩展区和65英寸中控大屏。", 4.0, 7.2)])),
            ("HAODUO_JINKOU_NIUPAI_COMMA", _build_audio_events([("店里省了好多，进口冷冻牛排", 0.0, 3.0)])),
            ("STRADDLE_2024_ANQUAN", _build_audio_events([("2024安全放在首位，一口气把流程跑通。", 0.0, 3.2)])),
            ("STRADDLE_BAWO_ZHICHI", _build_audio_events([("2024把握机会，2024支持国产，2024节目丰富，2024口碑爆棚。", 0.0, 5.2)])),
            ("HUALE_300_BA_FULL", _build_audio_events([("花了300把店盘下来", 0.0, 2.8)])),
        )
        for cname, audio in corpora_it14:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    _assert_entries_clean(self, res["entries"], f"{cname}/{mode}")
                    sub_joined = "|".join(e["text"] for e in res["entries"])
                    if cname in ("BLIND_HOME_RENO_W", "BLIND_HOME_RENO_NW"):
                        self.assertIn("1.5匹", sub_joined, (cname, mode, sub_joined))
                        self.assertIn("2000毫安", sub_joined, (cname, mode, sub_joined))

                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        stage_joined = "|".join(str(it.get("text_or_asset") or "") for it in st["items"])
                        for it in st["items"]:
                            if it.get("variant") == "bridge_bar":
                                txt_bb = str(it.get("text_or_asset") or "")
                                fs_bb = int(it.get("font_size_px") or 69)
                                pw, _ = step5_subtitles._measure_text_px(txt_bb, fp02, fs_bb)
                                self.assertLessEqual(pw, 640, (cname, mode, txt_bb, fs_bb, pw))
                        if cname in ("BLIND_HOME_RENO_W", "BLIND_HOME_RENO_NW") and mode == "legacy":
                            self.assertIn("1.5匹", stage_joined, (cname, mode, stage_joined))
                            self.assertIn("2000毫安", stage_joined, (cname, mode, stage_joined))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 6) Negative QA anti-bypass probes + positive controls for Iteration 14
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 5.0, "duration_sec": 5.0, "source_in_sec": 0.0, "source_out_sec": 5.0}],
                    "fps": 30, "total_duration_sec": 5.0,
                }),
                encoding="utf-8",
            )

            qa_it14_cases = [
                # (name, sents, sub_parts, bb_texts, fg_texts, style_id, expect_status)
                ("BB_FG_USB3_DOT2", [("机器升级到USB3.2接口", 0.0, 3.0)], [], ["机器升级到USB3"], [".2接口"], "style_02", "FAIL"),
                ("FG_FG_USB3_DOT2", [("机器升级到USB3.2接口", 0.0, 3.0)], [], [], ["机器升级到USB3", ".2接口"], "style_02", "FAIL"),
                ("CROSS_PCIE40_DOT", [("主板支持PCIe4.0固态硬盘", 0.0, 3.0)], [], ["主板支持PCIe4"], [".0固态硬盘"], "style_02", "FAIL"),
                ("BB_FG_TIME", [("早上8:30开门", 0.0, 3.0)], [], ["早上8"], [":30开门"], "style_02", "FAIL"),
                ("SUB_TIME_COLON", [("早上8:30开门", 0.0, 3.0)], ["早上8", ":30开门"], None, None, "style_01", "FAIL"),
                ("SUB_TIME_COLON2", [("早上8:30开门", 0.0, 3.0)], ["早上8:", "30开门"], None, None, "style_01", "FAIL"),
                ("FG_ONLY_LAOBAN", [("老板们都在看", 0.0, 3.0)], [], [], ["老板"], "style_02", "FAIL"),
                ("FG_ONLY_USB3", [("机器升级到USB3.2接口", 0.0, 3.0)], [], [], ["机器升级到USB3"], "style_02", "FAIL"),
                ("FG_ONLY_24", [("全程开启24度空调", 0.0, 3.0)], [], [], ["全程开启24"], "style_02", "FAIL"),
                ("NEG_HAODUO_SHENG_NP", [("店里省了好多，进口冷冻牛排", 0.0, 3.0)], ["店里省了好多", "进口冷冻牛排"], None, None, "style_01", "FAIL"),
                ("NEG_HAODUO_SHENG_NP_NOPUNCT", [("店里省了好多进口冷冻牛排", 0.0, 3.0)], ["店里省了好多", "进口冷冻牛排"], None, None, "style_01", "FAIL"),
                ("HAODUO_JIANGJIN_RAW", [("每月开销省了好多，大家奖金", 0.0, 3.0)], ["每月开销省了好多", "大家奖金"], None, None, "style_01", "FAIL"),
                ("HUALE_300_BA", [("花了300把店盘下来", 0.0, 3.0)], ["花了300把", "店盘下来"], None, None, "style_01", "FAIL"),
                ("HAODUO_JIANGJIN_FULL", [("每月开销省了好多，大家奖金也跟着涨了", 0.0, 3.0)], ["每月开销省了好多", "大家奖金"], None, ["大家奖金"], "style_02", "PASS"),
                ("POS_2024_ANQUAN", [("2024安全第一", 0.0, 2.0)], ["2024", "安全第一"], None, ["2024"], "style_02", "PASS"),
            ]
            for cname, s_rows, sub_parts, bb_texts, fg_texts, sid, expect_status in qa_it14_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 5.0}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": "s0", "text": sp, "start_sec": round(k * 1.5, 2), "end_sec": round(k * 1.5 + 1.4, 2), "suppressed": False}
                            for k, sp in enumerate(sub_parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                pkg_q: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                if bb_texts is not None or fg_texts is not None:
                    items_q: List[Dict[str, Any]] = []
                    for k_b, bt in enumerate(bb_texts or []):
                        items_q.append({
                            "id": f"bb_{k_b+1}", "group_id": "grp_00", "variant": "bridge_bar",
                            "text_or_asset": bt, "start_sec": round(k_b * 1.5, 2), "end_sec": round(k_b * 1.5 + 1.4, 2),
                            "track_lane": 1, "x_norm": 0.5, "y_norm": 0.5, "font_size_px": 69,
                        })
                    for k_f, ft in enumerate(fg_texts or []):
                        items_q.append({
                            "id": f"fg_{k_f+1}", "group_id": "grp_00", "variant": "hero_white",
                            "text_or_asset": ft, "start_sec": round(k_f * 1.5, 2), "end_sec": round(k_f * 1.5 + 1.4, 2),
                            "track_lane": 2 + k_f, "x_norm": 0.5, "y_norm": 0.3 + k_f * 0.1, "font_size_px": 80,
                        })
                    pkg_q["kinetic_stage"] = {"style_id": "style_02", "items": items_q}
                (pdir / "06_packaging.json").write_text(json.dumps(pkg_q, ensure_ascii=False), encoding="utf-8")
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (cname, got))

    def test_iteration15_zg_haoduo_q_obj_units_and_4tok_np_guards(self) -> None:
        fp02 = kinetic_choreographer._style02_font_path()
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}

        # 1) auditor_m1_14 Defect A: context-stripped 'zg' verbs & cross-comma subject swallowing
        self.assertTrue(step5_subtitles.is_degree_complement_haoduo("今年攒了好多", "我们终于买下新设备"))
        self.assertTrue(step5_subtitles.is_degree_complement_haoduo("刚才踩了好多", "脚底都磨破了"))
        self.assertTrue(step5_subtitles.is_degree_complement_haoduo("今年攒咗好多", "我哋终于买咗新设备"))
        self.assertFalse(step5_subtitles.is_degree_complement_haoduo("今年攒了好多", "精品咖啡豆"))

        self.assertEqual(
            [t for _, _, t in step5_subtitles.dp_segment_short_lines("今年攒了好多，我们终于买下新设备。", 632, font_size=59, max_chars=9)],
            ["今年攒了好多", "我们终于买下新设备"],
        )
        self.assertEqual(
            [t for _, _, t in step5_subtitles.dp_segment_short_lines("刚才踩了好多，脚底都磨破了。", 632, font_size=59, max_chars=9)],
            ["刚才踩了好多", "脚底都磨破了"],
        )
        self.assertEqual(
            [t for _, _, t in step5_subtitles.dp_segment_short_lines("今年攒了好多，精品咖啡豆。", 632, font_size=59, max_chars=9)],
            ["今年攒了好多精品咖啡豆"],
        )

        # 2) auditor_m1_14 Defect B & challenger_m1_14 Challenges 4 & 5
        for np_s in ("便携式制冰盒", "仓库分拣箱", "老字号点心铺", "手工烧腊坊", "复古茶艺阁"):
            self.assertTrue(step5_subtitles.is_compound_np_body(np_s), np_s)
        for non_np_s in ("买了三次", "跑了两趟", "换了几种", "大家吃了饭", "门店清理货"):
            self.assertFalse(step5_subtitles.is_compound_np_body(non_np_s), non_np_s)

        for p_txt, c_txt in (
            ("昨天刚买了", "便携式制冰盒，夏天特别实用。"),
            ("师父刚刚修了", "锁，现在开门顺滑多了。"),
            ("刚下班我买咗", "餸，返屋企煮餐好嘅。"),
            ("去年老板盘下了", "老字号点心铺，生意一直很稳定。"),
        ):
            reb = step5_subtitles.rebalance_asr_sentences([
                {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 0.9, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": 0.95, "end_sec": 2.8, "words": []},
            ], max_chars=9)
            self.assertGreater(len(reb[0]["text"]), len(p_txt), (p_txt, c_txt, reb))

        for p_txt, c_txt in (
            ("刚刚到货了", "看，大家都在清点。"),
            ("老板刚才说了", "走，我们去看看。"),
            ("今天我买了", "啊，真的非常划算。"),
            ("刚刚到货了", "车，已经停在门口。"),
        ):
            reb = step5_subtitles.rebalance_asr_sentences([
                {"id": "s0", "text": p_txt, "start_sec": 0.0, "end_sec": 0.9, "words": []},
                {"id": "s1", "text": c_txt, "start_sec": 0.95, "end_sec": 2.6, "words": []},
            ], max_chars=9)
            self.assertEqual(len(reb), 2, (p_txt, c_txt, reb))
            self.assertEqual(reb[0]["text"], p_txt, (p_txt, c_txt, reb))

        dp_qie = [t for _, _, t in step5_subtitles.dp_segment_short_lines("今年店里升级咗半自动切肉机，出餐速度快咗好多。", 632, font_size=59, max_chars=9)]
        self.assertIn("半自动切肉机", "|".join(dp_qie), dp_qie)
        dp_bing = [t for _, _, t in step5_subtitles.dp_segment_short_lines("昨天刚买了便携式制冰盒，夏天特别实用。", 632, font_size=59, max_chars=9)]
        self.assertIn("便携式制冰盒", "|".join(dp_bing), dp_bing)
        ph_qie = kinetic_choreographer._split_bridge_phrases_adaptive("今年店里升级咗半自动切肉机，出餐速度快咗好多。", font_path=fp02)
        self.assertIn("半自动切肉机", "|".join(ph_qie), ph_qie)

        # 3) challenger_m1_14 Challenge 1: ambiguous single-char units & ordinal straddle protection
        for txt_strad, bad_tok in (
            ("充到80只要十五分钟", "80只"),
            ("今年业绩冲到五十成为行业领头羊", "五十成"),
            ("今年业绩冲到五十成为行业第一", "五十成"),
            ("50分配到不同部门", "50分"),
            ("30点亮屏幕", "30点"),
            ("二十位于市中心", "二十位"),
            ("三十家里都在用", "三十家"),
            ("二十条件非常苛刻", "二十条"),
            ("二十名副其实的高手", "二十名"),
            ("第一时间看通知", "一时"),
            ("我们店的全城第一只是引用顾客原话。", "一只"),
            ("第三分工计酬", "三分"),
        ):
            toks_s = step5_subtitles.tokenize_semantic_words(txt_strad)
            self.assertNotIn(bad_tok, toks_s, (txt_strad, toks_s))
            spans_s = [txt_strad[ms:me] for ms, me in step5_subtitles.iter_num_unit_spans(txt_strad)]
            self.assertNotIn(bad_tok, spans_s, (txt_strad, spans_s))

        self.assertTrue(any("3个" in t for t in step5_subtitles.tokenize_semantic_words("3个人")))
        self.assertIn("10年", [s_u[a:b] for s_u in ("10年前开业",) for a, b in step5_subtitles.iter_num_unit_spans(s_u)])
        self.assertIn("80只", [s_u[a:b] for s_u in ("买了80只鸡",) for a, b in step5_subtitles.iter_num_unit_spans(s_u)])
        self.assertIn("一只", [s_u[a:b] for s_u in ("买了一只鸭",) for a, b in step5_subtitles.iter_num_unit_spans(s_u)])

        # 4) challenger_m1_14 Challenge 3: 4-token [N + V/VN + A/NS + N] product NPs vs genuine SVO clauses
        for np_4 in ("手工制作新鲜烧腊", "古法现烤酥脆面包", "手工现做广式点心", "传统工艺酿造酱油", "低温慢煮健康鸡胸"):
            self.assertFalse(step5_subtitles.is_independent_svo_clause(np_4), np_4)
            self.assertFalse(step5_subtitles.is_degree_complement_haoduo("店里省了好多", np_4), np_4)

        for svo_4 in (
            "老板亲自下厨炒菜", "员工每天加班干活", "工厂生产零件", "大家奖金也跟着涨了",
            "系统自动生成报表", "老客户直接转发推荐", "门店重启收银系统", "团队集中梳理流程",
            "总部统一配送货物", "师傅手工修理机器", "厨师现场制作点心", "店长亲自检查库存",
        ):
            self.assertTrue(step5_subtitles.is_independent_svo_clause(svo_4), svo_4)

        # 5) 4-mode end-to-end M1 QA pass on all Iteration 15 corpora
        ev_sents = [
            ("新款纯电SUV内置两千毫安辅助供电模块，搭配800V高压快充，", 0.0, 3.6),
            ("实测从百分之二十充到百分之八十只要十五分钟，续航直接拉满六百公里。", 3.8, 7.6),
        ]
        corpora_it15 = (
            ("DEFECT_A_ZAN", _build_audio_events([("今年攒了好多，我们终于买下新设备。", 0.0, 3.0)])),
            ("DEFECT_A_CAI", _build_audio_events([("刚才踩了好多，脚底都磨破了。", 0.0, 3.0)])),
            ("DEFECT_B_BINGHE", _build_audio_events([("昨天刚买了", 0.0, 0.9), ("便携式制冰盒，夏天特别实用。", 0.95, 3.2)])),
            ("BLIND_EV_W", _build_audio_events(ev_sents)),
            ("BLIND_EV_NW", {"sentences": [{"id": f"s{i}", "text": t, "start_sec": st, "end_sec": en, "words": []} for i, (t, st, en) in enumerate(ev_sents)], "locked_duration_sec": 8.1}),
            ("ARABIC_80_ZHIYAO_LEG", _build_audio_events([("充电速度非常快，充到80只要十五分钟。", 0.0, 3.2)])),
            ("WUSHI_CHENG_SHEEP", _build_audio_events([("今年业绩冲到五十成为行业领头羊。", 0.0, 3.0)])),
            ("STRAD_WUSHI_CHENG", _build_audio_events([("今年业绩冲到五十成为行业第一。", 0.0, 3.0)])),
            ("EDGE_1C_SONG_CANTO", _build_audio_events([("刚下班我买咗", 0.0, 0.9), ("餸，返屋企煮餐好嘅。", 0.95, 2.8)])),
            ("EDGE_1C_SUO", _build_audio_events([("师父刚刚修了", 0.0, 0.9), ("锁，现在开门顺滑多了。", 0.95, 2.8)])),
            ("EDGE_EQUIP_DIANXINPU", _build_audio_events([("去年老板盘下了", 0.0, 0.9), ("老字号点心铺，生意一直很稳定。", 0.95, 3.2)])),
            ("EDGE_EQUIP_QIEROUJI", _build_audio_events([("今年店里升级咗", 0.0, 0.9), ("半自动切肉机，出餐速度快咗好多。", 0.95, 3.2)])),
            ("MED_BEAUTY_4SENT", _build_audio_events([("其实很多人护肤都踩过坑。", 0.0, 2.0), ("只看表面成分很容易买错。", 2.2, 4.2), ("我们用二十八天追踪了百分之三十五的用户变化，", 4.4, 7.6), ("结果真的让人很意外。", 7.8, 9.6)])),
            ("UNSEEN_CROSSBORDER_LIVE", _build_audio_events([("跨境直播这两年竞争很大。", 0.0, 2.0), ("很多人上来就盲目投流。", 2.2, 4.2), ("我们花了一百五十万才跑通这套转化模型，", 4.4, 7.6), ("复购率直接翻了一倍。", 7.8, 9.6)])),
        )
        for cname, audio in corpora_it15:
            for mode in ("style_01", "style_09", "style_02", "legacy"):
                sid = "style_02" if mode == "legacy" else mode
                with tempfile.TemporaryDirectory() as td:
                    pdir = Path(td)
                    vid = pdir / "out.mp4"
                    _make_dummy_clip(vid, 0.4)
                    _write_minimal_pipeline_for_step5(pdir, audio)
                    (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
                    (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
                    (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
                    (pdir / "04_edl.json").write_text(
                        json.dumps({
                            "clips": [{
                                "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                                "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                                "duration_sec": audio["locked_duration_sec"],
                                "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                            }],
                            "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                        }),
                        encoding="utf-8",
                    )
                    res = step5_subtitles.run_step5(pdir, template_id=sid, enable_v6_director=(sid == "style_02"))
                    if cname == "STRAD_WUSHI_CHENG":
                        self.assertTrue(res["entries"], (cname, mode))
                        _assert_entries_clean(self, res["entries"][:-1], f"{cname}/{mode}")
                    else:
                        _assert_entries_clean(self, res["entries"], f"{cname}/{mode}")
                    sub_texts = [e["text"] for e in res["entries"]]
                    if cname in ("BLIND_EV_W", "BLIND_EV_NW", "ARABIC_80_ZHIYAO_LEG"):
                        self.assertFalse(any(t.endswith(("80只", "八十只")) for t in sub_texts), (cname, mode, sub_texts))
                    if cname in ("WUSHI_CHENG_SHEEP", "STRAD_WUSHI_CHENG"):
                        self.assertFalse(any(t.endswith("五十成") for t in sub_texts), (cname, mode, sub_texts))

                    pkg: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                    if sid == "style_02":
                        plan = None if mode == "legacy" else director_plan.generate_deterministic_director_plan(audio, "style_02")
                        st = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
                        pkg["kinetic_stage"] = st
                        stage_texts = [str(it.get("text_or_asset") or "") for it in st["items"]]
                        if cname in ("BLIND_EV_W", "BLIND_EV_NW", "ARABIC_80_ZHIYAO_LEG"):
                            self.assertFalse(any(t.endswith(("80只", "八十只")) for t in stage_texts), (cname, mode, stage_texts))
                        if cname in ("WUSHI_CHENG_SHEEP", "STRAD_WUSHI_CHENG"):
                            self.assertFalse(any(t.endswith("五十成") for t in stage_texts), (cname, mode, stage_texts))
                    (pdir / "06_packaging.json").write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
                    with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                         mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                        rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                    by_id = {c["id"]: c for c in rep["checks"]}
                    for cid in ("subtitle_segmentation_clean", "overlay_cross_act_overlap", "ui_safe_zone"):
                        self.assertEqual(by_id[cid]["status"], "PASS", (cname, mode, cid, by_id[cid]))
                    if sid == "style_02":
                        self.assertEqual(by_id["style_typography_unity"]["status"], "PASS", (cname, mode, by_id["style_typography_unity"]))

        # 6) Negative QA anti-bypass probes + positive controls for Iteration 15
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{"clip_id": "c1", "track_role": "a_roll", "media_path": str(vid), "timeline_in_sec": 0.0, "timeline_out_sec": 5.0, "duration_sec": 5.0, "source_in_sec": 0.0, "source_out_sec": 5.0}],
                    "fps": 30, "total_duration_sec": 5.0,
                }),
                encoding="utf-8",
            )

            qa_it15_cases = [
                # (name, sents, sub_parts, bb_texts, fg_items, style_id, expect_status)
                ("DEFECT_A_WOMEN_NEG", [("今年攒了好多，我们终于买下新设备。", 0.0, 3.0)], ["今年攒了好多我们", "终于买下新设备"], None, None, "style_01", "FAIL"),
                ("DEFECT_A_JIAODI_NEG", [("刚才踩了好多，脚底都磨破了。", 0.0, 3.0)], ["刚才踩了好多脚底", "都磨破了"], None, None, "style_01", "FAIL"),
                ("DEFECT_A_WOMEN_NOCOMMA_NEG", [("今年攒了好多我们终于买下新设备", 0.0, 3.0)], ["今年攒了好多我们", "终于买下新设备"], None, None, "style_01", "FAIL"),
                ("DEFECT_A_MERGED_SHICAI_POS", [("我们今天买了好多新鲜食材，都准备好了", 0.0, 3.0)], ["我们今天买了好多新鲜食材", "都准备好了"], None, None, "style_01", "PASS"),
                ("DEFECT_A_MAIZUO_SHICAI_POS", [("买咗好多新鲜食材", 0.0, 3.0)], ["买咗好多新鲜食材"], None, None, "style_01", "PASS"),
                ("DEFECT_B_BINGHE_NEG", [("昨天刚买了便携式制冰盒，夏天特别实用。", 0.0, 3.0)], ["昨天刚买了", "便携式制冰盒"], None, None, "style_01", "FAIL"),
                ("DEFECT_B_BENGKUI_POS", [("刚才崩溃了，门店重启收银系统", 0.0, 3.0)], ["刚才崩溃了", "门店重启收银系统"], None, None, "style_01", "PASS"),
                ("DEFECT_B_KADUN_POS", [("系统卡顿了，技术重启服务器", 0.0, 3.0)], ["系统卡顿了", "技术重启服务器"], None, None, "style_01", "PASS"),
                ("DEFECT_B_SHENGJI_POS", [("系统升级了，全新界面更流畅", 0.0, 3.0)], ["系统升级了", "全新界面更流畅"], None, None, "style_01", "PASS"),
                ("CH1_HANGYE_DIYI_POS", [("今年业绩冲到五十成为行业第一。", 0.0, 3.0)], ["今年业绩冲到", "五十成为行业第一"], None, None, "style_01", "PASS"),
                ("CH1_DANGLING_ORD_NEG", [("今天先讲第一步很关键", 0.0, 3.0)], ["今天先讲第一", "步很关键"], None, None, "style_01", "FAIL"),
                ("CH1_CROSS_ORD_NEG", [("过去那条线", 0.0, 1.4), ("第三过了夫妻店那条线", 1.5, 3.0)], ["过去那条线第三", "过了夫妻店那条线"], None, None, "style_01", "FAIL"),
                ("CH2_FG_HAODUO_SHAOLA_NEG", [("店里省了好多，手工制作烧腊", 0.0, 3.0)], [], [], ["店里省了好多"], "style_02", "FAIL"),
                ("CH2_FG_GAOZUO_HAODUO_POS", [("出餐速度快咗好多，效率高咗好多", 0.0, 3.0)], [], [], ["高咗好多"], "style_02", "PASS"),
                ("CH2_BB_FG_300BA_NEG", [("花了300把店盘下来", 0.0, 3.0)], [], ["花了300把"], ["店盘下来"], "style_02", "FAIL"),
                ("CH2_BB_FG_HAODUO_NEG", [("店里省了好多，进口冷冻牛排", 0.0, 3.0)], [], ["店里省了好多"], ["进口冷冻牛排"], "style_02", "FAIL"),
                ("CH2_BB_FG_1CHAR_NEG", [("店里刚进了货，老板清点", 0.0, 3.0)], [], ["店里刚进了"], ["货老板清点"], "style_02", "FAIL"),
                ("CH2_BB_FG_GENITIVE_NEG", [("𠮶边就系我哋嘅厨房", 0.0, 3.0)], [], ["𠮶边就系我哋嘅"], ["厨房"], "style_02", "FAIL"),
                ("CH2_FG_MULTISENT_BREAK_NEG", [("欢迎各位老板", 0.0, 1.0), ("老板们都在看", 1.1, 3.0)], [], [], [{"text": "老板", "st": 0.5, "en": 2.5}], "style_02", "FAIL"),
                ("CH3_SHAOLA_NEG", [("店里省了好多，手工制作新鲜烧腊", 0.0, 3.0)], ["店里省了好多", "手工制作新鲜烧腊"], None, None, "style_01", "FAIL"),
                ("CH3_MIANBAO_NEG", [("店里省了好多，古法现烤酥脆面包", 0.0, 3.0)], ["店里省了好多", "古法现烤酥脆面包"], None, None, "style_01", "FAIL"),
                ("CH3_DIANXIN_NEG", [("店里省了好多，手工现做广式点心", 0.0, 3.0)], ["店里省了好多", "手工现做广式点心"], None, None, "style_01", "FAIL"),
            ]
            for cname, s_rows, sub_parts, bb_texts, fg_items, sid, expect_status in qa_it15_cases:
                (pdir / "03_audio_events.json").write_text(
                    json.dumps({"sentences": [{"id": f"s{k}", "text": t, "start_sec": st, "end_sec": en} for k, (t, st, en) in enumerate(s_rows)], "locked_duration_sec": 5.0}, ensure_ascii=False),
                    encoding="utf-8",
                )
                (pdir / "05_subtitles.json").write_text(
                    json.dumps({
                        "entries": [
                            {"id": f"sub_{k+1:03d}", "sentence_id": "s0", "text": sp, "start_sec": round(k * 1.5, 2), "end_sec": round(k * 1.5 + 1.4, 2), "suppressed": False}
                            for k, sp in enumerate(sub_parts)
                        ]
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                pkg_q: Dict[str, Any] = {"style_id": sid, "sfx_cues": []}
                if bb_texts is not None or fg_items is not None:
                    items_q: List[Dict[str, Any]] = []
                    for k_b, bt in enumerate(bb_texts or []):
                        items_q.append({
                            "id": f"bb_{k_b+1}", "group_id": "grp_00", "variant": "bridge_bar",
                            "text_or_asset": bt, "start_sec": round(k_b * 1.5, 2), "end_sec": round(k_b * 1.5 + 1.4, 2),
                            "track_lane": 1, "x_norm": 0.5, "y_norm": 0.5, "font_size_px": 69,
                        })
                    for k_f, fit in enumerate(fg_items or []):
                        if isinstance(fit, str):
                            fit = {"text": fit}
                        items_q.append({
                            "id": f"fg_{k_f+1}", "group_id": "grp_00", "variant": fit.get("variant", "hero_white"),
                            "text_or_asset": fit["text"], "start_sec": fit.get("st", round(k_f * 1.5, 2)), "end_sec": fit.get("en", round(k_f * 1.5 + 1.4, 2)),
                            "track_lane": 2 + k_f, "x_norm": 0.5, "y_norm": 0.3 + k_f * 0.1, "font_size_px": 80,
                        })
                    pkg_q["kinetic_stage"] = {"style_id": "style_02", "items": items_q}
                (pdir / "06_packaging.json").write_text(json.dumps(pkg_q, ensure_ascii=False), encoding="utf-8")
                with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                     mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                    rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
                got = {c["id"]: c for c in rep["checks"]}["subtitle_segmentation_clean"]
                self.assertEqual(got["status"], expect_status, (cname, got))

    def test_wave1_bug1b_universal_dp_cohesion_2plus1_2plus2_1plus1_and_copula(self) -> None:
        cases = [
            ("我花咗三百万喺金湾开咗一间六百平嘅点赞茶楼", ("金|湾", "开咗|一间", "点赞|茶楼")),
            ("我哋桑拿鸡个鸡就系从山上", ("桑拿|鸡", "我哋桑拿|")),
            ("一早点赞茶楼呢一个特色就系朝早", ("点赞|茶楼", "呢|一个")),
            ("粤式茶点其实好讲究", ("粤式|茶", "茶|点")),
        ]
        for txt, bad_splits in cases:
            for mc, fs, px in ((9, 54, 632), (9, 69, 640), (8, 75, 632)):
                sent = _make_word_timed_sentence("s0", txt, 0.0, 4.0)
                chunks = step5_subtitles.split_sentence_into_chunks(
                    sent, max_chars=mc, font_size=fs, max_safe_px=px,
                )
                joined = "|".join(c["text"] for c in chunks)
                for bad in bad_splits:
                    self.assertNotIn(bad, joined, (txt, mc, fs, chunks))
                _assert_entries_clean(self, chunks, f"bug1b/{mc}/{fs}")
        # 带术语表（含 => 硬映射）同样不劈词
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "terms.txt"
            tf.write_text("# 注释\n桑娜鸡 => 桑拿鸡\n点赞茶楼\n金湾\n", encoding="utf-8")
            terms = step5_subtitles.load_protected_terms(tf)
            self.assertIn("桑拿鸡", terms)
            self.assertFalse(any("=>" in t for t in terms), terms)
            sent_s = _make_word_timed_sentence("s0", "我哋桑拿鸡个鸡就系从山上", 0.0, 3.0)
            chs = step5_subtitles.split_sentence_into_chunks(
                sent_s, max_chars=9, font_size=54, max_safe_px=632, protected_terms=terms,
            )
            self.assertTrue(any("桑拿鸡" in c["text"] for c in chs), chs)

    def test_wave1_bug1b_sem_override_guard_repairs_broken_terms_and_aspect_split(self) -> None:
        audio = _build_audio_events([
            ("我系牛哥", 0.0, 0.9),
            ("花咗三百万喺金湾开咗一间六百平嘅点赞茶楼", 0.95, 4.8),
            ("中间上边非常舒服", 5.0, 6.8),
            ("一早点赞茶楼呢一个特色就系朝早", 7.0, 10.2),
        ])
        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            _write_minimal_pipeline_for_step5(pdir, audio)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{
                        "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                        "timeline_in_sec": 0.0, "timeline_out_sec": audio["locked_duration_sec"],
                        "duration_sec": audio["locked_duration_sec"],
                        "source_in_sec": 0.0, "source_out_sec": audio["locked_duration_sec"],
                    }],
                    "fps": 30, "total_duration_sec": audio["locked_duration_sec"],
                }),
                encoding="utf-8",
            )
            (pdir / "terms.txt").write_text("金湾\n点赞茶楼\n", encoding="utf-8")
            # 故意在 03b_director_plan.json 放入拆破保护词与动貌断宾的 semantic_subtitles
            (pdir / "03b_director_plan.json").write_text(
                json.dumps({
                    "step": "03b_director_plan",
                    "source": "agent",
                    "style_id": "style_03",
                    "native_style_mode": True,
                    "semantic_subtitles": [
                        {"text": "我系牛哥", "keywords": []},
                        {"text": "花咗三百万喺金", "keywords": []},
                        {"text": "湾开咗", "keywords": []},
                        {"text": "一间六百平嘅点赞茶楼", "keywords": ["六百平"]},
                        {"text": "中间上边非常舒服", "keywords": ["中间上边"]},
                        {"text": "一早点赞", "keywords": []},
                        {"text": "茶楼呢一个特色就系朝早", "keywords": []},
                    ],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            res = step5_subtitles.run_step5(pdir, track="store", template_id="style_03")
            self.assertEqual(res["broken_protected_terms"], [], res["broken_protected_terms"])
            joined = "|".join(e["text"] for e in res["entries"])
            self.assertNotIn("金|湾", joined)
            self.assertNotIn("开咗|一间", joined)
            self.assertNotIn("点赞|茶楼", joined)
            # 未违规句子保留 semantic_curated=True
            clean_entry = next(e for e in res["entries"] if e["text"] == "中间上边非常舒服")
            self.assertTrue(clean_entry.get("semantic_curated"), clean_entry)
            (pdir / "06_packaging.json").write_text(json.dumps({"style_id": "style_03", "sfx_cues": []}), encoding="utf-8")
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            by_id = {c["id"]: c for c in rep["checks"]}
            self.assertEqual(by_id["subtitle_segmentation_clean"]["status"], "PASS", by_id["subtitle_segmentation_clean"])

    def test_wave1_bug2a_clean_breath_emits_bridge_bar_and_preserves_multi_act_coverage(self) -> None:
        audio = _build_audio_events([
            ("我系牛哥我", 0.0, 1.0),
            ("花咗三百万喺金湾开咗一间六百平嘅点赞茶楼", 1.05, 4.6),
            ("中间上边", 4.8, 6.1),
            ("摆好多", 6.12, 6.6),
            ("卡座一个中空环境非常舒服", 6.62, 9.8),
            ("我哋有四大天王虾饺排骨凤爪叉烧包", 10.0, 13.8),
            ("绝对品质好抵食欢迎大家来", 14.0, 17.0),
        ])
        plan = {
            "step": "03b_director_plan",
            "source": "agent",
            "style_id": "style_02",
            "theme_profile": "style_02_unified",
            "acts": [
                {"act_id": "act_01", "sentence_ids": ["s0"], "dramatic_role": "setup", "emotion_intensity": 0.2,
                 "primitive": "clean_breath", "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                 "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 0},
                {"act_id": "act_02", "sentence_ids": ["s1"], "dramatic_role": "hook", "emotion_intensity": 0.9,
                 "primitive": "number_burst_pair", "visual_copy": {"headline": "300万", "items": ["点赞茶楼"], "accent_token": "300万", "footnote": ""},
                 "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 1},
                {"act_id": "act_03", "sentence_ids": ["s2"], "dramatic_role": "setup", "emotion_intensity": 0.2,
                 "primitive": "clean_breath", "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                 "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 0},
                {"act_id": "act_04", "sentence_ids": ["s3"], "dramatic_role": "bridge", "emotion_intensity": 0.3,
                 "primitive": "plain_bridge", "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                 "camera_intent": "steady_hold", "broll_policy": "allow_broll", "sfx_budget": 0},
                {"act_id": "act_05", "sentence_ids": ["s4"], "dramatic_role": "bridge", "emotion_intensity": 0.3,
                 "primitive": "plain_bridge", "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                 "camera_intent": "steady_hold", "broll_policy": "allow_broll", "sfx_budget": 0},
                {"act_id": "act_06", "sentence_ids": ["s5"], "dramatic_role": "proof", "emotion_intensity": 0.85,
                 "primitive": "list_gather_depth", "visual_copy": {"headline": "四大天王", "items": ["虾饺", "排骨", "凤爪", "叉烧包"], "accent_token": "", "footnote": ""},
                 "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 1},
                {"act_id": "act_07", "sentence_ids": ["s6"], "dramatic_role": "setup", "emotion_intensity": 0.2,
                 "primitive": "clean_breath", "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                 "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 0},
            ],
            "music_cues": [],
        }
        stage = kinetic_choreographer.plan_kinetic_stage(audio, style_id="style_02", director_plan=plan)
        cb_items = [it for it in stage["items"] if it.get("act_id") in {"act_01", "act_03", "act_07"}]
        self.assertTrue(cb_items, stage["items"])
        for it in cb_items:
            self.assertEqual(it["variant"], "bridge_bar", it)
            self.assertEqual(it["typography_tier"], "L1", it)
            self.assertIsNone(it["sfx_role"], it)
        # act_03 不吞并 act_04/act_05；act_04/act_05 仍保住跨句"摆好多卡座"
        act3_texts = [it["text_or_asset"] for it in stage["items"] if it.get("act_id") == "act_03"]
        self.assertEqual(act3_texts, ["中间上边"], act3_texts)
        plain_texts = [it["text_or_asset"] for it in stage["items"] if it.get("act_id") in {"act_04", "act_05"}]
        self.assertTrue(any("摆好多卡座" in t for t in plain_texts), plain_texts)

        sents = audio["sentences"]
        speech_total = sum(max(0.0, s["end_sec"] - s["start_sec"]) for s in sents)
        ivs = sorted((it["start_sec"], it["end_sec"]) for it in stage["items"] if it["end_sec"] > it["start_sec"])
        merged: List[tuple[float, float]] = []
        for a, b in ivs:
            if merged and a <= merged[-1][1] + 0.05:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        cov = sum(max(0.0, min(s["end_sec"], b) - max(s["start_sec"], a)) for s in sents for a, b in merged)
        self.assertGreaterEqual(cov / speech_total, 0.80, f"coverage={cov/speech_total:.3f}")

    def test_wave1_bug2b_and_bug2c_pain_slam_fg_zero_overlap_and_glossary_arrow_parsing(self) -> None:
        # 1) Bug 2b: pain_slam_danmaku 前景兜底（顺序接力 + 3行并发）严格 0 重叠
        comp_fg_only = {
            "primary_stage_mode": "closeup_chest_stage",
            "allow_behind_person": False,
            "aroll_profiles": [{"media_path": "a.mp4", "allow_behind_person": False, "face_bbox": {"x": 0.3, "y": 0.15, "w": 0.35, "h": 0.30}}],
        }
        seq_audio = _build_audio_events([("我负债两百万不是被骗不是行情差", 0.0, 5.5)])
        seq_plan = {
            "step": "03b_director_plan", "source": "agent", "style_id": "style_02", "theme_profile": "style_02_unified",
            "acts": [{
                "act_id": "act_01", "sentence_ids": ["s0"], "dramatic_role": "hook", "emotion_intensity": 0.9,
                "primitive": "pain_slam_danmaku",
                "visual_copy": {"headline": "负债两百万", "items": ["✕ 不是被骗", "✕ 不是行情差"], "accent_token": "", "footnote": ""},
                "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 1,
            }],
            "music_cues": [],
        }
        conc_audio = _build_audio_events([("不是贵不是慢不是难而是思路不对", 0.0, 2.2)])
        conc_plan = {
            "step": "03b_director_plan", "source": "agent", "style_id": "style_02", "theme_profile": "style_02_unified",
            "acts": [{
                "act_id": "act_01", "sentence_ids": ["s0"], "dramatic_role": "hook", "emotion_intensity": 0.9,
                "primitive": "pain_slam_danmaku",
                "visual_copy": {"headline": "思路不对", "items": ["✕ 不是贵", "✕ 不是慢", "✕ 不是难"], "accent_token": "", "footnote": ""},
                "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": 1,
            }],
            "music_cues": [],
        }
        for label, au, pl in (("seq_2rows", seq_audio, seq_plan), ("conc_3rows", conc_audio, conc_plan)):
            st = kinetic_choreographer.plan_kinetic_stage(au, style_id="style_02", director_plan=pl, composition_profile=comp_fg_only)
            fg = [it for it in st["items"] if it.get("z_layer", "foreground") == "foreground" and it.get("variant") != "bridge_bar"]
            self.assertGreaterEqual(len(fg), 3, (label, fg))
            for i, ia in enumerate(fg):
                for ib in fg[i + 1 :]:
                    ov = min(float(ia["end_sec"]), float(ib["end_sec"])) - max(float(ia["start_sec"]), float(ib["start_sec"]))
                    if ov > 0.05 and abs(float(ia["x_norm"]) - float(ib["x_norm"])) < 0.45:
                        self.assertGreaterEqual(
                            abs(float(ia["y_norm"]) - float(ib["y_norm"])),
                            0.06,
                            f"{label}: overlap {ia} vs {ib} ({ov:.2f}s)",
                        )

        # 2) Bug 2c: 术语表 `错词 => 正词` 正确解析，bridge_bar 不拆开「桑拿鸡」
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            au_c = _build_audio_events([("我哋桑拿鸡个鸡就系从山上捉翻嚟嘅", 0.0, 4.0)])
            (pdir / "03_audio_events.json").write_text(json.dumps(au_c, ensure_ascii=False), encoding="utf-8")
            (pdir / "terms.txt").write_text("桑娜鸡 => 桑拿鸡\n", encoding="utf-8")
            pl_c = {
                "step": "03b_director_plan", "source": "agent", "style_id": "style_02", "theme_profile": "style_02_unified",
                "acts": [{
                    "act_id": "act_01", "sentence_ids": ["s0"], "dramatic_role": "bridge", "emotion_intensity": 0.3,
                    "primitive": "plain_bridge", "visual_copy": {"headline": "", "items": [], "accent_token": "", "footnote": ""},
                    "camera_intent": "steady_hold", "broll_policy": "allow_broll", "sfx_budget": 0,
                }],
                "music_cues": [],
            }
            st_c = kinetic_choreographer.plan_kinetic_stage(au_c, style_id="style_02", pipeline_dir=pdir, director_plan=pl_c)
            bbs = [str(it["text_or_asset"]) for it in st_c["items"] if it.get("variant") == "bridge_bar"]
            self.assertTrue(any("桑拿鸡" in t for t in bbs), bbs)
            self.assertFalse(any("=>" in t for t in bbs), bbs)

    def test_pain_slam_danmaku_mixed_concurrent_sequential_3rows_zero_qa_overlap(self) -> None:
        comp_fg_only = {
            "primary_stage_mode": "closeup_chest_stage",
            "allow_behind_person": False,
            "aroll_profiles": [{"media_path": "a.mp4", "allow_behind_person": False, "face_bbox": {"x": 0.3, "y": 0.15, "w": 0.35, "h": 0.30}}],
        }
        txt_mix = "太贵离远这几个问题通通不是其实方法不对"
        words_mix = [
            {"word": "太", "start_sec": 0.0, "end_sec": 0.2},
            {"word": "贵", "start_sec": 0.2, "end_sec": 0.4},
            {"word": "离", "start_sec": 0.4, "end_sec": 0.6},
            {"word": "远", "start_sec": 0.6, "end_sec": 0.8},
        ]
        for idx, ch in enumerate("这几个问题通通不是其实"):
            words_mix.append({"word": ch, "start_sec": round(0.8 + idx * 0.2, 3), "end_sec": round(1.0 + idx * 0.2, 3)})
        for idx, ch in enumerate("方法不对"):
            words_mix.append({"word": ch, "start_sec": round(3.0 + idx * 0.45, 3), "end_sec": round(3.45 + idx * 0.45, 3)})
        mixed_audio = {
            "sentences": [{"id": "s0", "text": txt_mix, "start_sec": 0.0, "end_sec": 4.8, "words": words_mix}],
            "locked_duration_sec": 5.3,
        }
        mixed_plan = {
            "step": "03b_director_plan",
            "source": "agent",
            "style_id": "style_02",
            "theme_profile": "style_02_unified",
            "acts": [{
                "act_id": "act_01",
                "sentence_ids": ["s0"],
                "dramatic_role": "hook",
                "emotion_intensity": 0.9,
                "primitive": "pain_slam_danmaku",
                "visual_copy": {
                    "headline": "这几个问题",
                    "items": ["✕ 太贵", "✕ 离远", "✕ 方法不对"],
                    "accent_token": "",
                    "footnote": "",
                },
                "camera_intent": "steady_hold",
                "broll_policy": "lock_aroll",
                "sfx_budget": 1,
            }],
            "music_cues": [],
        }
        st = kinetic_choreographer.plan_kinetic_stage(
            mixed_audio, style_id="style_02", director_plan=mixed_plan, composition_profile=comp_fg_only,
        )
        fg = [it for it in st["items"] if it.get("z_layer", "foreground") == "foreground" and it.get("variant") != "bridge_bar"]
        self.assertEqual(len(fg), 4, fg)
        for i, ia in enumerate(fg):
            for ib in fg[i + 1 :]:
                ov = min(float(ia["end_sec"]), float(ib["end_sec"])) - max(float(ia["start_sec"]), float(ib["start_sec"]))
                if ov > 0.05 and abs(float(ia["x_norm"]) - float(ib["x_norm"])) < 0.45:
                    self.assertGreaterEqual(
                        abs(float(ia["y_norm"]) - float(ib["y_norm"])),
                        0.06,
                        f"mixed_3rows overlap: {ia} vs {ib} ({ov:.2f}s)",
                    )

        fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            _write_minimal_pipeline_for_step5(pdir, mixed_audio)
            step5_subtitles.run_step5(pdir, template_id="style_02", enable_v6_director=True)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{
                        "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                        "timeline_in_sec": 0.0, "timeline_out_sec": mixed_audio["locked_duration_sec"],
                        "duration_sec": mixed_audio["locked_duration_sec"],
                        "source_in_sec": 0.0, "source_out_sec": mixed_audio["locked_duration_sec"],
                    }],
                    "fps": 30, "total_duration_sec": mixed_audio["locked_duration_sec"],
                }),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(
                json.dumps({"style_id": "style_02", "sfx_cues": [], "kinetic_stage": st}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                rep = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            ov_chk = {c["id"]: c for c in rep["checks"]}["overlay_cross_act_overlap"]
            self.assertEqual(ov_chk["status"], "PASS", ov_chk)

    def test_pain_slam_behind_person_3rows_zero_collision_and_fallback_pipeline_dir(self) -> None:
        comp_bp = {
            "primary_stage_mode": "wide_behind_person",
            "allow_behind_person": True,
            "aroll_profiles": [{
                "media_path": "a.mp4",
                "allow_behind_person": True,
                "face_bbox": {"x": 0.3, "y": 0.35, "w": 0.35, "h": 0.30},
            }],
        }
        txt_mix = "太贵离远这几个问题通通不是其实方法不对"
        words_mix = [
            {"word": "太", "start_sec": 0.0, "end_sec": 0.2},
            {"word": "贵", "start_sec": 0.2, "end_sec": 0.4},
            {"word": "离", "start_sec": 0.4, "end_sec": 0.6},
            {"word": "远", "start_sec": 0.6, "end_sec": 0.8},
        ]
        for idx, ch in enumerate("这几个问题通通不是其实"):
            words_mix.append({"word": ch, "start_sec": round(0.8 + idx * 0.2, 3), "end_sec": round(1.0 + idx * 0.2, 3)})
        for idx, ch in enumerate("方法不对"):
            words_mix.append({"word": ch, "start_sec": round(3.0 + idx * 0.45, 3), "end_sec": round(3.45 + idx * 0.45, 3)})
        mixed_audio = {
            "sentences": [{"id": "s0", "text": txt_mix, "start_sec": 0.0, "end_sec": 4.8, "words": words_mix}],
            "locked_duration_sec": 5.3,
        }
        mixed_plan = {
            "step": "03b_director_plan",
            "source": "agent",
            "style_id": "style_02",
            "theme_profile": "style_02_unified",
            "acts": [{
                "act_id": "act_01",
                "sentence_ids": ["s0"],
                "dramatic_role": "hook",
                "emotion_intensity": 0.9,
                "primitive": "pain_slam_danmaku",
                "visual_copy": {
                    "headline": "这几个问题",
                    "items": ["✕ 太贵", "✕ 离远", "✕ 方法不对"],
                    "accent_token": "",
                    "footnote": "",
                },
                "camera_intent": "steady_hold",
                "broll_policy": "lock_aroll",
                "sfx_budget": 1,
            }],
            "music_cues": [],
        }
        st_bp = kinetic_choreographer.plan_kinetic_stage(
            mixed_audio, style_id="style_02", director_plan=mixed_plan, composition_profile=comp_bp,
        )
        bp_items = [it for it in st_bp["items"] if it.get("z_layer") == "behind_person"]
        self.assertEqual(len(bp_items), 3, bp_items)
        for i, ia in enumerate(bp_items):
            for ib in bp_items[i + 1:]:
                ov = min(float(ia["end_sec"]), float(ib["end_sec"])) - max(float(ia["start_sec"]), float(ib["start_sec"]))
                if ov > 0.05 and abs(float(ia["x_norm"]) - float(ib["x_norm"])) < 0.45:
                    self.assertGreaterEqual(
                        abs(float(ia["y_norm"]) - float(ib["y_norm"])),
                        0.06,
                        f"behind_person 3-row overlap: {ia} vs {ib} ({ov:.2f}s)",
                    )

        # Secondary fallback in plan_kinetic_stage when 03_audio_events.json is absent on disk
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "terms.txt").write_text("潮汕手打牛肉丸\n", encoding="utf-8")
            (pdir / "00_raw_intel.json").write_text(
                json.dumps({"entities": ["沙湾古镇姜埋奶"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            au_mem = _build_audio_events([
                ("今天带大家吃潮汕手打牛肉丸", 0.0, 3.0),
                ("再去吃一碗沙湾古镇姜埋奶", 3.1, 6.0),
            ])
            st_fb = kinetic_choreographer.plan_kinetic_stage(
                au_mem, style_id="style_02", pipeline_dir=pdir, composition_profile=comp_bp,
            )
            item_texts = [str(it.get("text_or_asset") or "") for it in st_fb["items"]]
            self.assertTrue(any("潮汕手打牛肉丸" in t for t in item_texts), item_texts)
            self.assertTrue(any("沙湾古镇姜埋奶" in t for t in item_texts), item_texts)

    def test_c3_c4_secondary_fallback_dict_entities_and_behind_person_y027(self) -> None:
        # C3: secondary fallback (03_audio_events.json absent on disk) + dict entities + pain_keywords + 10-11 char terms
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            (pdir / "terms.txt").write_text("古法柴火窑炉现烤面包\n", encoding="utf-8")
            (pdir / "00_raw_intel.json").write_text(
                json.dumps({
                    "entities": [
                        {"name": "古法柴火窑炉现烤面包"},
                        {"text": "零下六十度急冻金枪鱼腹"},
                        {"term": "冷链恒温直配体系"},
                    ],
                    "pain_keywords": ["石磨现磨黑芝麻糊", "超低温液氮急冻锁鲜工艺"],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            au_mem = _build_audio_events([
                ("还有古法柴火窑炉现烤面包和香", 0.0, 3.2),
                ("而是零下六十度急冻金枪鱼腹", 3.4, 6.5),
                ("搭配冷链恒温直配体系和石磨现磨黑芝麻糊", 6.7, 10.2),
                ("今天我们带大家看看超低温液氮急冻锁鲜工艺到底怎么做的全流程", 10.4, 14.2),
            ])
            comp_fb = {
                "primary_stage_mode": "closeup_chest_stage",
                "allow_behind_person": False,
                "aroll_profiles": [{"media_path": "a.mp4", "allow_behind_person": False}],
            }
            expected_terms = [
                "古法柴火窑炉现烤面包",
                "零下六十度急冻金枪鱼腹",
                "冷链恒温直配体系",
                "石磨现磨黑芝麻糊",
                "超低温液氮急冻锁鲜工艺",
            ]
            for prof in (comp_fb, None):
                st = kinetic_choreographer.plan_kinetic_stage(
                    au_mem, style_id="style_02", pipeline_dir=pdir, composition_profile=prof,
                )
                texts = [str(it.get("text_or_asset") or "") for it in st["items"]]
                for tm in expected_terms:
                    self.assertTrue(any(tm in t for t in texts), (prof is None, tm, texts))
                self.assertTrue(all(len(t) <= 15 for t in texts), (prof is None, texts))

            # qa_inspector unpacks dict entities + pain_keywords and catches splits
            vid = pdir / "out.mp4"
            _make_dummy_clip(vid, 0.4)
            (pdir / "01_manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (pdir / "02_transcript.json").write_text(json.dumps({"words": []}), encoding="utf-8")
            (pdir / "03_audio_events.json").write_text(json.dumps(au_mem, ensure_ascii=False), encoding="utf-8")
            (pdir / "07_color_plan.json").write_text(json.dumps({}), encoding="utf-8")
            (pdir / "04_edl.json").write_text(
                json.dumps({
                    "clips": [{
                        "clip_id": "c1", "track_role": "a_roll", "media_path": str(vid),
                        "timeline_in_sec": 0.0, "timeline_out_sec": au_mem["locked_duration_sec"],
                        "duration_sec": au_mem["locked_duration_sec"],
                        "source_in_sec": 0.0, "source_out_sec": au_mem["locked_duration_sec"],
                    }],
                    "fps": 30, "total_duration_sec": au_mem["locked_duration_sec"],
                }),
                encoding="utf-8",
            )
            fake_ctx = {"r128": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.5}, "long_silences": []}
            (pdir / "05_subtitles.json").write_text(
                json.dumps({
                    "entries": [
                        {"id": "sub1", "text": "而是零下六十度", "start_sec": 3.4, "end_sec": 4.9, "suppressed": False},
                        {"id": "sub2", "text": "急冻金枪鱼腹", "start_sec": 4.95, "end_sec": 6.5, "suppressed": False},
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (pdir / "06_packaging.json").write_text(
                json.dumps({"style_id": "style_02", "sfx_cues": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                qa_bad = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            chk_bad = {c["id"]: c for c in qa_bad["checks"]}["subtitle_segmentation_clean"]
            self.assertEqual(chk_bad["status"], "FAIL", chk_bad)
            self.assertIn("零下六十度急冻金枪鱼腹", chk_bad["measured"])

            (pdir / "05_subtitles.json").write_text(
                json.dumps({
                    "entries": [
                        {"id": "sub1", "text": "而是零下六十度急冻金枪鱼腹", "start_sec": 3.4, "end_sec": 6.5, "suppressed": False},
                        {"id": "sub2", "text": "超低温液氮急冻锁鲜工艺", "start_sec": 10.4, "end_sec": 12.2, "suppressed": False},
                    ]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(independent_verifier, "extract_audio_context_once", return_value=fake_ctx), \
                 mock.patch.object(independent_verifier, "run_blackbox_verification", return_value={"checks": []}):
                qa_ok = qa_inspector.inspect_video(pdir, video_path=vid, export_mode="mp4")
            chk_ok = {c["id"]: c for c in qa_ok["checks"]}["subtitle_segmentation_clean"]
            self.assertEqual(chk_ok["status"], "PASS", chk_ok)

        # C4: pain_slam_danmaku behind_person at boundary face_bbox["y"]=0.27 (2/3/4 rows, extra_rows[:3] cap, zero bbox overlap)
        comp_bp_027 = {
            "primary_stage_mode": "wide_behind_person",
            "allow_behind_person": True,
            "aroll_profiles": [{
                "media_path": "a.mp4",
                "allow_behind_person": True,
                "face_bbox": {"x": 0.3, "y": 0.27, "w": 0.35, "h": 0.30},
            }],
        }
        au_bp = {
            "sentences": [{
                "id": "s0",
                "source_media": "a.mp4",
                "text": "这几个问题价格虚高距离太远方法不对复购率低",
                "start_sec": 0.0,
                "end_sec": 6.0,
            }],
            "locked_duration_sec": 6.2,
        }
        for raw_items, exp_cnt in (
            (["✕ 价格虚高太离谱", "✕ 距离太远不方便"], 2),
            (["✕ 价格虚高太离谱", "✕ 距离太远不方便", "✕ 方法不对全白费", "✕ 复购率低没利润"], 3),
        ):
            plan_bp = {
                "step": "03b_director_plan",
                "source": "agent",
                "style_id": "style_02",
                "theme_profile": "style_02_unified",
                "acts": [{
                    "act_id": "act_01",
                    "sentence_ids": ["s0"],
                    "dramatic_role": "hook",
                    "emotion_intensity": 0.9,
                    "primitive": "pain_slam_danmaku",
                    "visual_copy": {
                        "headline": "这几个问题",
                        "items": raw_items,
                        "accent_token": "",
                        "footnote": "",
                    },
                    "camera_intent": "steady_hold",
                    "broll_policy": "lock_aroll",
                    "sfx_budget": 1,
                }],
                "music_cues": [],
            }
            st_bp = kinetic_choreographer.plan_kinetic_stage(
                au_bp, style_id="style_02", director_plan=plan_bp, composition_profile=comp_bp_027,
            )
            bp_items = [it for it in st_bp["items"] if it.get("z_layer") == "behind_person"]
            self.assertEqual(len(bp_items), exp_cnt, bp_items)
            for idx_a, ia in enumerate(bp_items):
                cy_a = float(ia["center_px"][1])
                fs_a = float(ia["font_size_px"])
                self.assertGreaterEqual(cy_a - 0.5 * fs_a, common.SAFE_ZONE["y_min"], ia)
                self.assertLessEqual(cy_a + 0.5 * fs_a, 0.27 * common.CANVAS_HEIGHT, ia)
                for ib in bp_items[idx_a + 1:]:
                    self.assertGreaterEqual(abs(float(ia["y_norm"]) - float(ib["y_norm"])), 0.06, (ia, ib))
                    cy_b = float(ib["center_px"][1])
                    fs_b = float(ib["font_size_px"])
                    self.assertGreaterEqual(abs(cy_a - cy_b), 0.5 * (fs_a + fs_b), (ia, ib))

    def test_trailing_subject_pronoun_narrow_serif_and_guard_hardening(self) -> None:
        tpl04 = step5_subtitles.load_subtitle_templates().get("style_04", {})
        fp04_cand = common.ASSETS_DIR / "fonts" / "SourceHanSerifCN-Heavy.otf"
        fp04 = str(fp04_cand) if fp04_cand.is_file() else director_plan._resolve_subtitle_font_path("style_04", tpl04)

        def _has_pronoun_tail_pair(chunks: List[Dict[str, Any]]) -> List[tuple[str, str]]:
            bad: List[tuple[str, str]] = []
            for idx in range(len(chunks) - 1):
                c0, c1 = chunks[idx], chunks[idx + 1]
                t0 = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(c0.get("text") or ""))
                t1 = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(c1.get("text") or ""))
                gap = float(c1.get("start_sec") or 0.0) - float(c0.get("end_sec") or 0.0)
                if (
                    gap <= 0.25
                    and len(t0) >= 4
                    and t0[-1] in {"我", "你", "他", "她", "它", "佢"}
                    and not any(t0[:-1].endswith(k) for k in step5_subtitles._PRON_TAIL_KEEP_PREV)
                    and not t1.startswith(("的", "嘅", "哋", "们", "自己", "也", "都"))
                ):
                    bad.append((t0, t1))
            return bad

        # 1) Direct split_sentence_into_chunks under narrow-serif style_04 width constraint
        cases_direct = [
            "抄最快抄完还有种我在做事的踏实感",
            "节奏有没有他结尾有没有动作",
            "写完方案还有种它在自动跑的感觉",
            "答不上来私信我",
        ]
        for idx_s, txt in enumerate(cases_direct):
            sent = _make_word_timed_sentence(f"s{idx_s}", txt, 0.0, max(1.4, len(txt) * 0.22))
            chunks = step5_subtitles.split_sentence_into_chunks(
                sent,
                max_chars=9,
                font_path=fp04,
                font_size=59,
                max_safe_px=632,
                strip_punct=True,
            )
            self.assertEqual(_has_pronoun_tail_pair(chunks), [], (txt, chunks))
            if txt == "抄最快抄完还有种我在做事的踏实感":
                self.assertEqual([c["text"] for c in chunks], ["抄最快抄完还有种", "我在做事的踏实感"])
            elif txt == "节奏有没有他结尾有没有动作":
                self.assertEqual([c["text"] for c in chunks], ["节奏有没有他结尾", "有没有动作"])
            elif txt == "答不上来私信我":
                self.assertEqual([c["text"] for c in chunks], ["答不上来私信我"])

        # 2) director_plan._build_semantic_subtitles across affected narrow/large styles
        audio = _build_audio_events([
            ("抄最快抄完还有种我在做事的踏实感", 0.0, 3.2),
            ("节奏有没有他结尾有没有动作", 3.5, 6.1),
            ("答不上来私信我", 6.4, 7.8),
        ])
        for sid in ("style_04", "style_06", "style_20", "style_21", "style_24"):
            sems = director_plan._build_semantic_subtitles(audio, sid)
            self.assertEqual(_has_pronoun_tail_pair(sems), [], (sid, sems))
            self.assertTrue(any(s.get("text") == "答不上来私信我" for s in sems), (sid, sems))

        # 3) Secondary guard helpers & _validate_and_repair_sem_override_chunks ("它" + "好多/有啲"+pronoun)
        self.assertTrue(step5_subtitles._has_trailing_subject_pronoun_split("抄最快抄完还有种我", "在做事的踏实感"))
        self.assertTrue(step5_subtitles._has_trailing_subject_pronoun_split("写完方案还有种它", "在自动跑"))
        self.assertFalse(step5_subtitles._has_trailing_subject_pronoun_split("答不上来私信我", "马上回复你"))
        self.assertFalse(step5_subtitles._has_trailing_subject_pronoun_split("记住要告诉我", "自己的想法"))

        ae_guard = _build_audio_events([
            ("写完方案还有种它在自动跑的感觉", 0.0, 3.2),
            ("比以前好咗好多佢话好开心", 3.5, 6.3),
        ])
        bad_override = [
            {"sentence_id": "s0", "text": "写完方案还有种它", "start_sec": 0.0, "end_sec": 1.6, "keywords": []},
            {"sentence_id": "s0", "text": "在自动跑的感觉", "start_sec": 1.65, "end_sec": 3.2, "keywords": []},
            {"sentence_id": "s1", "text": "比以前好咗好多佢", "start_sec": 3.5, "end_sec": 5.0, "keywords": []},
            {"sentence_id": "s1", "text": "话好开心", "start_sec": 5.05, "end_sec": 6.3, "keywords": []},
        ]
        repaired = step5_subtitles._validate_and_repair_sem_override_chunks(
            bad_override,
            ae_guard,
            prot_terms=[],
            tpl_max_chars=9,
            chunk_max_chars=9,
            chunk_fs=59,
            chunk_px=632,
            font_path=fp04,
            strip_punct=True,
            script_lines_punct=[],
            raw_pain_kws=set(),
        )
        self.assertEqual(_has_pronoun_tail_pair(repaired), [], repaired)
        self.assertFalse(any(str(c.get("text") or "").endswith("好多佢") for c in repaired), repaired)


if __name__ == "__main__":
    unittest.main()
