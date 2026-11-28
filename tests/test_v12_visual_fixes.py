#!/usr/bin/env python3
"""V12.0 视觉穿帮清零与多镜跨屏回归单测（覆盖 F4..F12 及 style_visual_truth 契约）。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "short-video-studio" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import SAFE_ZONE, check_bbox_in_safe_zone, contrast_ratio, hex_to_rgba, write_json  # noqa: E402
import element_rules  # noqa: E402
import motion_html_renderer as mhr  # noqa: E402
import person_matting  # noqa: E402
import render_mp4  # noqa: E402
import step4_build_edl as s4  # noqa: E402
import step6_packaging as s6  # noqa: E402
import style_truth_renderer as str_ren  # noqa: E402
import style_visual_truth as svt  # noqa: E402

FPS = 30
STAGE_HTML_PATH = ROOT / "short-video-studio" / "assets" / "motion_templates" / "stage.html"


def _decode_frames_at_times(mp4_path: Path, times_sec: List[float]) -> List[Tuple[float, np.ndarray]]:
    import av  # type: ignore

    out: List[Tuple[float, np.ndarray]] = []
    with av.open(str(mp4_path)) as container:
        stream = container.streams.video[0]
        tb = float(stream.time_base)
        for t_req in times_sec:
            container.seek(max(0, int(round(t_req / tb))), stream=stream, any_frame=False, backward=True)
            best_arr = None
            for frame in container.decode(stream):
                t_f = float(frame.pts * tb) if frame.pts is not None else t_req
                best_arr = frame.to_ndarray(format="rgb24")
                if t_f >= t_req - 0.04:
                    break
            if best_arr is not None:
                out.append((t_req, best_arr))
    return out


def _make_long_pipeline_fixture(
    pdir: Path,
    style_id: str,
    total_sec: float = 28.0,
    face_norm: Dict[str, float] | None = None,
    with_director_plan: bool = True,
) -> Path:
    pdir.mkdir(parents=True, exist_ok=True)
    n_sents = max(8, int(total_sec // 3.0))
    dt = total_sec / float(n_sents)
    sentences: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    clips: List[Dict[str, Any]] = []
    dummy_aroll = str((pdir / "norm_aroll.mp4").resolve())
    for i in range(n_sents):
        t0 = round(i * dt, 3)
        t1 = round((i + 1) * dt, 3)
        f0 = int(round(t0 * FPS))
        f1 = int(round(t1 * FPS))
        sid = f"s{i + 1}"
        sentences.append({
            "id": sid,
            "text": f"第{i + 1}条核心实战法则先看利润结构再谈落地",
            "keywords": ["利润结构", "核心法则"],
            "start_sec": t0,
            "end_sec": t1,
            "start_frame": f0,
            "end_frame": f1,
            "emotion_or_info": "hook" if i == 0 else ("ending" if i == n_sents - 1 else "info"),
        })
        events.append({
            "id": f"e{i + 1}",
            "type": "sentence_start",
            "time_sec": t0,
            "frame": f0,
            "sentence_id": sid,
        })
        clips.append({
            "clip_id": f"c{i + 1}",
            "track_role": "a_roll",
            "media_path": dummy_aroll,
            "sentence_id": sid,
            "timeline_in_sec": t0,
            "timeline_out_sec": t1,
            "timeline_in_frame": f0,
            "timeline_out_frame": f1,
            "duration_sec": round(t1 - t0, 3),
            "cut_event_frame": f0,
            "cut_event_id": f"e{i + 1}",
        })
    write_json(pdir / "03_audio_events.json", {
        "locked_duration_sec": total_sec,
        "sentences": sentences,
        "events": events,
    })
    write_json(pdir / "04_edl.json", {"style_id": style_id, "fps": FPS, "total_duration_sec": total_sec, "clips": clips})
    faces_list = [{"bbox": face_norm}] if face_norm else []
    write_json(pdir / "01_manifest.json", {
        "items": [{"normalized_mp4": dummy_aroll, "role": "a_roll", "duration_sec": total_sec, "faces": faces_list}]
    })
    if with_director_plan:
        callouts = []
        if style_id == "style_06":
            callouts.append({
                "text": "行稳致远方能长久",
                "variant": "s06_vertical_poem",
                "callout_y": 390,
                "start_sec": 6.0,
                "end_sec": 9.0,
            })
        write_json(pdir / "03b_director_plan.json", {
            "style_id": style_id,
            "cover_copy": {"headline_top": "先看结构", "highlight_badge": "再谈落地"},
            "style_callouts": callouts,
        })
    return pdir


class V12VisualFixesTests(unittest.TestCase):
    def test_f4_stage_html_no_ktv_orbs_and_achromatic_micro_motion(self) -> None:
        html_txt = STAGE_HTML_PATH.read_text(encoding="utf-8")
        for forbidden in ("amb-orb-1", "amb-orb-2", "amb-sweep", "先找底层前提", "先看利润结构"):
            self.assertNotIn(forbidden, html_txt)
        self.assertIn("ed-breath-top", html_txt)
        self.assertIn("ed-breath-bot", html_txt)
        self.assertIn("ed-progress-fill", html_txt)

        if not mhr.is_html_motion_available():
            return
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            spec = mhr.build_panel_job_spec(
                {
                    "mode": "dark_typewriter_card_pip",
                    "variant": "s03_black_typewriter",
                    "panel_title": "核心结论",
                    "quote_text": "先看清问题本质再设计可行方案",
                },
                dur_sec=3.5,
                style_id="style_03",
            )
            self.assertIsNotNone(spec)
            rendered = mhr.batch_render_motion_jobs({"tw_test": spec}, work, fps=FPS)
            mp4_p = rendered.get("tw_test")
            self.assertIsNotNone(mp4_p)
            self.assertTrue(mp4_p.is_file())
            frames = _decode_frames_at_times(mp4_p, [i * 0.5 for i in range(7)])
            self.assertGreaterEqual(len(frames), 6)

            # 1) 色度与 R-B 时域漂移检验（纯灰呼吸不可染色）
            chroma_p95_max = 0.0
            temporal_rb_p95_max = 0.0
            for i in range(len(frames) - 1):
                a = frames[i][1].astype(np.float32)[360:1400]
                b = frames[i + 1][1].astype(np.float32)[360:1400]
                la = 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
                lb = 0.2126 * b[:, :, 0] + 0.7152 * b[:, :, 1] + 0.0722 * b[:, :, 2]
                bg_mask = (la < 120.0) & (lb < 120.0)
                if int(bg_mask.sum()) >= 500:
                    ch_a = a.max(axis=2) - a.min(axis=2)
                    ch_b = b.max(axis=2) - b.min(axis=2)
                    chroma_p95_max = max(chroma_p95_max, float(np.percentile(np.concatenate([ch_a[bg_mask], ch_b[bg_mask]]), 95)))
                    rb_diff = np.abs((b[:, :, 0] - b[:, :, 2]) - (a[:, :, 0] - a[:, :, 2]))
                    temporal_rb_p95_max = max(temporal_rb_p95_max, float(np.percentile(rb_diff[bg_mask], 95)))
            self.assertLessEqual(chroma_p95_max, 16.0)
            self.assertLessEqual(temporal_rb_p95_max, 12.0)

            # 2) bb_no_visual_freeze 270x480 掩膜检验（全程无 >3.0s 静止）
            thumbs = []
            for t, arr in frames:
                sm = np.asarray(Image.fromarray(arr).resize((270, 480), Image.Resampling.BILINEAR), dtype=np.float32)
                sm[46:88, :, :] = 0.0
                sm[285:380, :, :] = 0.0
                thumbs.append((t, sm))
            cur_static = max_static = 0.0
            for i in range(len(thumbs) - 1):
                diff = np.abs(thumbs[i + 1][1] - thumbs[i][1])
                if float(np.mean(diff)) < 1.2 and float(np.percentile(diff, 95)) < 5.0:
                    cur_static += thumbs[i + 1][0] - thumbs[i][0]
                    max_static = max(max_static, cur_static)
                else:
                    cur_static = 0.0
            self.assertLessEqual(max_static, 3.0)

    def test_f5_top_bar_compact_height_top_safe_y_and_circle_sync(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for sid in ("style_03", "style_06", "style_08", "style_12"):
                tb = svt.get_persistent_top_bar_spec(sid)
                self.assertIsNotNone(tb)
                im = str_ren.render_truth_overlay_png(
                    {
                        "kind": "persistent_top_bar",
                        "top_bar_mode": tb["mode"],
                        "text_or_asset": "核心实战拆解",
                        "subtext": "先看利润结构",
                    },
                    style_id=sid,
                )
                self.assertIsNotNone(im, sid)
                self.assertLessEqual(im.height, 152, f"{sid} top_bar height={im.height} > 152")
                self.assertGreaterEqual(svt.get_top_safe_y(sid), 356)

                pdir = _make_long_pipeline_fixture(
                    root / f"p_{sid}",
                    sid,
                    total_sec=28.0,
                    face_norm={"x": 0.2043, "y": 0.3014, "w": 0.3919, "h": 0.2204},
                )
                pkg = s6.run_step6(pdir, style_id=sid)
                elems = pkg["visual_elements"]
                tbs = [e for e in elems if e.get("kind") == "persistent_top_bar"]
                self.assertTrue(tbs, sid)
                tb_bb = tbs[0]["final_bbox"]
                tb_bottom = tb_bb["y"] + tb_bb["h"]
                self.assertLessEqual(tb_bottom, 325, f"{sid} tb_bottom={tb_bottom}")
                for ov in elems:
                    if ov.get("kind") == "persistent_top_bar":
                        continue
                    obb = ov["final_bbox"]
                    self.assertGreaterEqual(
                        obb["y"], tb_bottom + 12,
                        f"{sid} {ov.get('kind')} y={obb['y']} collides with top_bar bottom={tb_bottom}",
                    )

                # concept_diagram_pip: build_panel_job_spec 与 build_html_motion_panel_cmd 100% 几何同步
                lay = {"mode": "concept_diagram_pip", "diagram_mode": "compare", "panel_title": "核心对比"}
                spec = mhr.build_panel_job_spec(lay, 3.0, style_id=sid)
                geom = mhr.resolve_concept_diagram_geometry(lay, style_id=sid)
                self.assertEqual(spec["circle_cx"], geom["circle_cx"])
                self.assertEqual(spec["circle_cy"], geom["circle_cy"])
                self.assertEqual(spec["circle_r"], geom["circle_r"])
                self.assertGreaterEqual(spec["circle_cy"] - spec["circle_r"], svt.get_top_safe_y(sid))
                dummy_mov = root / f"dummy_{sid}.mov"
                dummy_mov.write_bytes(b"0" * 4096)
                cmd = mhr.build_html_motion_panel_cmd(
                    ffmpeg_bin="ffmpeg",
                    media_path=str(root / "in.mp4"),
                    src_in=0.0,
                    src_avail=3.0,
                    base_vf_chain="scale=1080:1920",
                    pip_layout=lay,
                    rendered_bg_or_ov=dummy_mov,
                    clip_frames=90,
                    fps=30,
                    out_mp4=root / f"out_{sid}.mp4",
                    style_id=sid,
                )
                self.assertIn(f"overlay=x={geom['circle_cx'] - geom['circle_r']}:y={geom['circle_cy'] - geom['circle_r']}", " ".join(cmd))

            # style_03 fg_offset_y & style_12 circle_cy 均避让顶栏
            s03_cut = next(b["layout"] for b in svt.get_visual_truth("style_03")["scene_beats"] if b["layout"]["mode"] == "vision_portrait_cutout_pip")
            self.assertGreaterEqual(s03_cut["fg_offset_y"], 260)
            s12_cir = svt.get_visual_truth("style_12")["scene_beats"][0]["layout"]
            self.assertGreaterEqual(s12_cir["circle_cy"] - s12_cir["circle_r"] - 8, 356)

    def test_f6_style15_and_24_no_bottom_sub_band_and_t0_readable(self) -> None:
        html_txt = STAGE_HTML_PATH.read_text(encoding="utf-8")
        self.assertNotIn("bottom-sub-band", html_txt)

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            for mode, variant, png_name in (
                ("review_whiteboard_pip", "s24_whiteboard_multi", "re_v10_whiteboard.png"),
                ("paper_outline_pip", "s15_paper_outline", "pa_v11_paper_outline_pip.png"),
            ):
                cmd = str_ren.build_truth_scene_panel_cmd(
                    ffmpeg_bin="ffmpeg",
                    media_path=str(work / "in.mp4"),
                    src_in=0.0,
                    src_avail=3.0,
                    base_vf_chain="scale=1080:1920",
                    pip_layout={"mode": mode, "variant": variant, "panel_title": "核心要点", "quote_text": "产品细节实测拆解"},
                    clip_frames=90,
                    fps=30,
                    out_mp4=work / f"{mode[:2]}.mp4",
                )
                self.assertIsNotNone(cmd)
                png_p = work / png_name
                self.assertTrue(png_p.is_file(), f"Missing {png_p}")
                arr = np.asarray(Image.open(png_p).convert("RGB"), dtype=np.float32)
                luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
                bot_p75 = float(np.percentile(luma[1500:1900, 100:980], 75))
                self.assertGreaterEqual(bot_p75, 175.0, f"{mode} PIL bottom luma={bot_p75}")

            if mhr.is_html_motion_available():
                spec15 = mhr.build_panel_job_spec(
                    {
                        "mode": "paper_outline_pip",
                        "variant": "s15_paper_outline",
                        "panel_title": "核心三要素",
                        "panel_lines": ["利润结构", "组织落地", "复购机制"],
                    },
                    dur_sec=2.5,
                    style_id="style_15",
                )
                rendered = mhr.batch_render_motion_jobs({"po_test": spec15}, work, fps=FPS)
                mp4_15 = rendered.get("po_test")
                self.assertTrue(mp4_15 and mp4_15.is_file())
                frs = _decode_frames_at_times(mp4_15, [0.03, 1.2])
                self.assertEqual(len(frs), 2)
                for idx_f, (_, fr) in enumerate(frs):
                    a = fr.astype(np.float32)
                    luma = 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
                    bot_p75 = float(np.percentile(luma[1500:1900, 100:980], 75))
                    top_p75 = float(np.percentile(luma[200:600, 100:980], 75))
                    self.assertGreaterEqual(bot_p75, 175.0)
                    self.assertLessEqual(top_p75 - bot_p75, 45.0)
                    if idx_f == 0:
                        ink_pct = float((luma[360:1240, 80:1000] < 120.0).mean() * 100.0)
                        self.assertGreaterEqual(ink_pct, 1.5, f"t=0.03s dark_ink_pct={ink_pct:.2f}% < 1.5%")

    def test_f6_magazine_review_whiteboard_t0_ink_and_4s_no_freeze(self) -> None:
        if not mhr.is_html_motion_available():
            return
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            spec24 = mhr.build_panel_job_spec(
                {
                    "mode": "review_whiteboard_pip",
                    "variant": "s24_whiteboard_multi",
                    "panel_title": "实测痛点",
                    "panel_lines": ["第一条核心问题", "第二条关键对比"],
                },
                dur_sec=4.0,
                style_id="style_24",
            )
            self.assertIsNotNone(spec24)
            rendered = mhr.batch_render_motion_jobs({"mr_4s": spec24}, work, fps=FPS)
            mp4_24 = rendered.get("mr_4s")
            self.assertTrue(mp4_24 and mp4_24.is_file())

            t0_frs = _decode_frames_at_times(mp4_24, [0.03])
            self.assertEqual(len(t0_frs), 1)
            a0 = t0_frs[0][1].astype(np.float32)
            luma0 = 0.2126 * a0[:, :, 0] + 0.7152 * a0[:, :, 1] + 0.0722 * a0[:, :, 2]
            bot_p75 = float(np.percentile(luma0[1500:1900, 100:980], 75))
            dark_ink_pct = float((luma0[360:1240, 80:1000] < 120.0).mean() * 100.0)
            self.assertGreaterEqual(bot_p75, 175.0)
            self.assertGreaterEqual(dark_ink_pct, 1.5, f"mr t=0.03s dark_ink_pct={dark_ink_pct:.2f}% < 1.5%")

            step_frs = _decode_frames_at_times(mp4_24, [i * 0.5 for i in range(9)])
            self.assertGreaterEqual(len(step_frs), 8)
            chroma_p95_max = 0.0
            temporal_rb_p95_max = 0.0
            for i in range(len(step_frs) - 1):
                a = step_frs[i][1].astype(np.float32)[360:1400]
                b = step_frs[i + 1][1].astype(np.float32)[360:1400]
                la = 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
                lb = 0.2126 * b[:, :, 0] + 0.7152 * b[:, :, 1] + 0.0722 * b[:, :, 2]
                bg_mask = (la < 120.0) & (lb < 120.0)
                if int(bg_mask.sum()) >= 500:
                    ch_a = a.max(axis=2) - a.min(axis=2)
                    ch_b = b.max(axis=2) - b.min(axis=2)
                    chroma_p95_max = max(chroma_p95_max, float(np.percentile(np.concatenate([ch_a[bg_mask], ch_b[bg_mask]]), 95)))
                    rb_diff = np.abs((b[:, :, 0] - b[:, :, 2]) - (a[:, :, 0] - a[:, :, 2]))
                    temporal_rb_p95_max = max(temporal_rb_p95_max, float(np.percentile(rb_diff[bg_mask], 95)))
            self.assertLessEqual(chroma_p95_max, 16.0)
            self.assertLessEqual(temporal_rb_p95_max, 12.0)

            thumbs = []
            for t, arr in step_frs:
                sm = np.asarray(Image.fromarray(arr).resize((270, 480), Image.Resampling.BILINEAR), dtype=np.float32)
                sm[46:88, :, :] = 0.0
                sm[285:380, :, :] = 0.0
                thumbs.append((t, sm))
            cur_static = max_static = 0.0
            for i in range(len(thumbs) - 1):
                diff = np.abs(thumbs[i + 1][1] - thumbs[i][1])
                if float(np.mean(diff)) < 1.2 and float(np.percentile(diff, 95)) < 5.0:
                    cur_static += thumbs[i + 1][0] - thumbs[i][0]
                    max_static = max(max_static, cur_static)
                else:
                    cur_static = 0.0
            self.assertLessEqual(max_static, 3.0)

    def test_f7_style04_giant_text_no_truncation(self) -> None:
        html_txt = STAGE_HTML_PATH.read_text(encoding="utf-8")
        fb_idx = html_txt.find('kind === "framed_box_panel"')
        self.assertNotEqual(fb_idx, -1)
        fb_seg = html_txt[fb_idx:fb_idx + 3000]
        for bad_slice in ("title.slice(0, 6)", "title.slice(0, 7)", "title.slice(0, 8)"):
            self.assertNotIn(bad_slice, fb_seg)

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            p6 = str_ren.render_person_behind_giant_v10_png("前提搬不过来", work / "g6.png")
            p10 = str_ren.render_person_behind_giant_v10_png("门店制度不能直接照搬", work / "g10.png")
            for gp in (p6, p10):
                self.assertTrue(gp.is_file())
                arr = np.asarray(Image.open(gp).convert("RGBA"))
                # 上半区 y < 1056 必须存在饱满文字墨迹
                self.assertGreater(int((arr[:1056, :, 3] > 180).sum()), 8000)

            co = mhr.build_callout_job_spec({"kind": "center_burst_title", "text_or_asset": "门店制度不能直接照搬复制"}, "style_08", 2.0)
            self.assertGreater(len(co["title"]), 10)

    def test_f7_pil_fallback_s04_focus_giant_word_no_truncation_and_safe_y(self) -> None:
        title14 = "门店激励制度绝对不能直接照搬"
        wrapped = str_ren._wrap_lines(title14, max_chars_per_line=max(6, (len(title14) + 1) // 2), max_lines=2)
        self.assertEqual(len("".join(wrapped)), len(title14))

        titles = ["核心真相", "前提搬不过来", "门店制度不能直接照搬", title14]
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            ink_counts: Dict[str, int] = {}
            for idx, title in enumerate(titles):
                sub = work / f"pil_{idx}"
                sub.mkdir(parents=True, exist_ok=True)
                cmd = str_ren.build_truth_scene_panel_cmd(
                    ffmpeg_bin="ffmpeg",
                    media_path=str(sub / "in.mp4"),
                    src_in=0.0,
                    src_avail=3.0,
                    base_vf_chain="scale=1080:1920",
                    pip_layout={"mode": "framed_box_pip", "variant": "s04_focus_giant_word", "panel_title": title},
                    clip_frames=90,
                    fps=FPS,
                    out_mp4=sub / "out.mp4",
                )
                self.assertIsNotNone(cmd)
                bg_png = sub / "out_v10_fbox_bg.png"
                self.assertTrue(bg_png.is_file(), f"Missing {bg_png}")
                arr = np.asarray(Image.open(bg_png).convert("RGB"), dtype=np.float32)
                luma = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
                mask = luma[890:1200, 60:1020] > 130.0
                ys = np.where(mask.any(axis=1))[0] + 890
                self.assertGreater(len(ys), 0, f"PIL s04_focus_giant_word missing ink for {title}")
                self.assertGreaterEqual(int(ys.min()), 896, f"{title} y_min={int(ys.min())} overlaps frame")
                self.assertLess(int(ys.max()), 1056, f"{title} y_max={int(ys.max())} >= 1056")
                ink_counts[title] = int(mask.sum())

            # Compare 14-char render against truncated 10-char prefix at same 2-line path
            sub_trunc = work / "pil_trunc10"
            sub_trunc.mkdir(parents=True, exist_ok=True)
            str_ren.build_truth_scene_panel_cmd(
                ffmpeg_bin="ffmpeg",
                media_path=str(sub_trunc / "in.mp4"),
                src_in=0.0,
                src_avail=3.0,
                base_vf_chain="scale=1080:1920",
                pip_layout={"mode": "framed_box_pip", "variant": "s04_focus_giant_word", "panel_title": title14[:10]},
                clip_frames=90,
                fps=FPS,
                out_mp4=sub_trunc / "out.mp4",
            )
            arr10 = np.asarray(Image.open(sub_trunc / "out_v10_fbox_bg.png").convert("RGB"), dtype=np.float32)
            luma10 = 0.2126 * arr10[:, :, 0] + 0.7152 * arr10[:, :, 1] + 0.0722 * arr10[:, :, 2]
            ink10 = int((luma10[890:1056, 60:1020] > 130.0).sum())
            self.assertNotEqual(ink_counts[title14], ink10)

    def test_f7_html_s04_focus_giant_word_safe_y_below_frame(self) -> None:
        if not mhr.is_html_motion_available():
            return
        titles = ["核心真相", "前提搬不过来", "门店制度不能直接照搬", "门店激励制度绝对不能直接照搬"]
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            jobs = {
                f"fg_{idx}": mhr.build_panel_job_spec(
                    {"mode": "framed_box_pip", "variant": "s04_focus_giant_word", "panel_title": title},
                    dur_sec=2.5,
                    style_id="style_04",
                )
                for idx, title in enumerate(titles)
            }
            rendered = mhr.batch_render_motion_jobs(jobs, work, fps=FPS)
            for idx, title in enumerate(titles):
                mp4_p = rendered.get(f"fg_{idx}")
                self.assertTrue(mp4_p and mp4_p.is_file(), f"Missing rendered mp4 for {title}")
                frs = _decode_frames_at_times(mp4_p, [0.35, 1.2])
                self.assertEqual(len(frs), 2)
                for tr, fr in frs:
                    a = fr.astype(np.float32)
                    luma = 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
                    # 排除底部进度条 y=1105..1122，检查框下文字区 y=896..1104
                    ys = np.where((luma[896:1105, 60:1020] > 130.0).any(axis=1))[0] + 896
                    self.assertGreater(len(ys), 0, f"HTML s04_focus_giant_word missing ink for {title} at t={tr}")
                    self.assertLess(int(ys.max()), 1056, f"{title} at t={tr} y_max={int(ys.max())} >= 1056")
                    # 确认不侵入上方框底安全带 y=880..895（框边之外无文字叠压）
                    self.assertEqual(int((luma[880:896, 200:880] > 160.0).sum()), 0, f"{title} at t={tr} overlaps frame")

    def test_f8_avoid_faces_head_padding_and_style10_16_depth_routing(self) -> None:
        biz_face = {"x": 0.2043, "y": 0.3014, "w": 0.3919, "h": 0.2204}  # y=579..1002
        sto_face = {"x": 0.3828, "y": 0.2753, "w": 0.2345, "h": 0.1319}  # y=529..782
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for tag, face_n in (("biz", biz_face), ("sto", sto_face)):
                fy_px = int(round(face_n["y"] * 1920))
                fh_px = int(round(face_n["h"] * 1920))
                crown_top = max(0, fy_px - int(round(0.55 * fh_px)))
                # 1) 前景风格避让：必须避开发冠 [crown_top, fy_px + fh_px]
                pdir_fg = _make_long_pipeline_fixture(root / f"{tag}_s05", "style_05", total_sec=28.0, face_norm=face_n)
                pkg_fg = s6.run_step6(pdir_fg, style_id="style_05")
                for el in pkg_fg["visual_elements"]:
                    if el.get("kind") in ("stamp_tag", "quote_citation_card", "end_card_cta"):
                        bb = el["final_bbox"]
                        overlaps_crown = not (bb["y"] + bb["h"] <= crown_top or bb["y"] >= fy_px + fh_px)
                        self.assertFalse(overlaps_crown, f"{tag} {el['kind']} bbox={bb} overlaps crown {crown_top}..{fy_px}")

                # 2) style_10 / style_16 center_burst_title 必须打 z_layer="behind_person" + callout_y
                for sid in ("style_10", "style_16"):
                    pdir_g = _make_long_pipeline_fixture(root / f"{tag}_{sid}", sid, total_sec=28.0, face_norm=face_n)
                    pkg_g = s6.run_step6(pdir_g, style_id=sid)
                    bursts = [e for e in pkg_g["visual_elements"] if e.get("kind") == "center_burst_title"]
                    self.assertTrue(bursts, (tag, sid))
                    for b in bursts:
                        self.assertEqual(b.get("z_layer"), "behind_person")
                        self.assertIsNotNone(b.get("callout_y"))
                        co_spec = mhr.build_callout_job_spec(b, sid, 2.2)
                        self.assertLessEqual(co_spec["y"], 360)
                        self.assertEqual(co_spec["z_layer"], "behind_person")

            # 非 style_10/16 胸口花字默认 y 不被拉到顶部
            co_s08 = mhr.build_callout_job_spec({"kind": "center_burst_title", "text_or_asset": "核心突破"}, "style_08", 2.0)
            self.assertEqual(co_s08["y"], 900)
            co_s15 = mhr.build_callout_job_spec({"kind": "center_burst_title", "text_or_asset": "核心突破"}, "style_15", 2.0)
            self.assertEqual(co_s15["y"], 960)

    def test_f8_rendered_style10_portrait_over_top_text(self) -> None:
        """真实渲染验证：style_10 标准 a_roll 镜头遇 top giant text 自动升级 self_matting_behind_text 并将人像置顶。"""
        ffmpeg_bin = render_mp4.find_bin("ffmpeg")
        if not ffmpeg_bin or person_matting.ensure_macos_vision_matte_bin() is None:
            return
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td) / "proj_s10"
            pdir.mkdir(parents=True, exist_ok=True)
            # 造一段 2.4s 带真实头肩剪影的合成口播视频（头顶伸到 y=300，肩到 y=1920）+ 纯声 wav
            src_mp4 = pdir / "norm_aroll.mp4"
            wav_p = pdir / "08_mixed_master.wav"
            img = Image.new("RGB", (1080, 1920), (24, 28, 36))
            dr = ImageDraw.Draw(img)
            # 简单人物头肩躯干（足够让 Vision PersonSegmentation 识别为前景人物）
            dr.ellipse([(380, 320), (700, 740)], fill=(210, 168, 140))  # 脸+头
            dr.ellipse([(405, 310), (675, 430)], fill=(45, 36, 32))     # 头发
            dr.rounded_rectangle([(240, 720), (840, 1920)], radius=140, fill=(38, 64, 110))  # 肩膀与上身
            png_bg = pdir / "person_frame.png"
            img.save(png_bg)
            subprocess.run(
                [ffmpeg_bin, "-y", "-loop", "1", "-framerate", "30", "-t", "2.4", "-i", str(png_bg),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src_mp4)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [ffmpeg_bin, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "2.4", str(wav_p)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            write_json(pdir / "01_manifest.json", {
                "items": [{"normalized_mp4": str(src_mp4), "role": "a_roll", "duration_sec": 2.4,
                           "faces": [{"bbox": {"x": 0.36, "y": 0.22, "w": 0.28, "h": 0.17}}]}]
            })
            write_json(pdir / "04_edl.json", {
                "style_id": "style_10", "fps": 30, "total_duration_sec": 2.4,
                "clips": [{
                    "clip_id": "c1", "track_role": "a_roll", "media_path": str(src_mp4),
                    "source_in_sec": 0.0, "source_out_sec": 2.4,
                    "timeline_in_sec": 0.0, "timeline_out_sec": 2.4,
                    "timeline_in_frame": 0, "timeline_out_frame": 72, "duration_sec": 2.4,
                }],
            })
            write_json(pdir / "05_subtitles.json", {"template_id": "style_10", "entries": []})
            write_json(pdir / "07_color_plan.json", {"clip_plans": []})
            write_json(pdir / "06_packaging.json", {
                "style_id": "style_10",
                "visual_elements": [{
                    "id": "pkg_001", "kind": "center_burst_title", "z_layer": "behind_person",
                    "callout_y": 320, "text_or_asset": "核心突破", "fg_hex": "#FFFFFF", "accent_hex": "#FFE135",
                    "start_frame": 0, "end_frame": 72,
                    "bbox": {"x": 200, "y": 320, "w": 680, "h": 150},
                    "final_bbox": {"x": 200, "y": 320, "w": 680, "h": 150},
                }],
            })
            out_mp4 = render_mp4.render_mp4(pdir, preview=False)
            self.assertTrue(out_mp4.is_file())
            frs = _decode_frames_at_times(out_mp4, [1.0])
            self.assertEqual(len(frs), 1)
            fr = frs[0][1]
            # 若 Vision 对该简笔人像生效，人脸核心区（y=440..560, x=480..600）不应被纯白文字高亮盖穿
            core = fr[440:560, 480:600]
            bright_frac = float((core.min(axis=2) > 225).mean())
            self.assertLessEqual(bright_frac, 0.15, f"face core occluded by top white text: {bright_frac:.3f}")

    def test_f9_style07_wcag_aa_contrast(self) -> None:
        def _rel_lum(hx: str) -> float:
            r, g, b = hex_to_rgba(hx, 255)[:3]
            vals = []
            for c in (r, g, b):
                v = c / 255.0
                vals.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
            return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]

        s07 = svt.get_visual_truth("style_07")
        for beat in s07["scene_beats"]:
            lay = beat["layout"]
            if lay.get("variant") == "top_red_pain":
                bg_l = _rel_lum(lay["bg_hex"])
                for k in ("accent_hex", "sub_hex"):
                    fg_l = _rel_lum(lay[k])
                    ratio = (max(fg_l, bg_l) + 0.05) / (min(fg_l, bg_l) + 0.05)
                    self.assertGreaterEqual(ratio, 4.5, f"{k}={lay[k]} contrast={ratio:.2f}")

        html_txt = STAGE_HTML_PATH.read_text(encoding="utf-8")
        idx = html_txt.find('variant === "top_red_pain"')
        self.assertNotEqual(idx, -1)
        seg = html_txt[idx:idx + 1200]
        self.assertIn("#FF6B6B", seg)
        self.assertIn("#FF8787", seg)
        self.assertNotIn("#C92A2A", seg)
        self.assertNotIn("#E03131", seg)

    def test_f10_style06_vertical_poem_safe_zone(self) -> None:
        html_txt = STAGE_HTML_PATH.read_text(encoding="utf-8")
        idx = html_txt.find('variant === "s06_vertical_poem"')
        self.assertNotEqual(idx, -1)
        seg = html_txt[idx:idx + 500]
        m_right = re.search(r"right\s*:\s*(\d+)px", seg)
        self.assertIsNotNone(m_right)
        self.assertLessEqual(1080 - int(m_right.group(1)), SAFE_ZONE["x_max"])

        with tempfile.TemporaryDirectory() as td:
            pdir = _make_long_pipeline_fixture(Path(td) / "p06", "style_06", total_sec=28.0)
            pkg = s6.run_step6(pdir, style_id="style_06")
            poems = [e for e in pkg["visual_elements"] if e.get("callout_variant") == "s06_vertical_poem"]
            self.assertTrue(poems)
            bb = poems[0]["final_bbox"]
            self.assertLess(bb["w"], bb["h"])
            self.assertLessEqual(bb["x"] + bb["w"], SAFE_ZONE["x_max"])
            self.assertGreaterEqual(bb["y"], 380)
            self.assertTrue(check_bbox_in_safe_zone(bb)[0])

    def test_f11_style03_21_smooth_matting_and_element_rules(self) -> None:
        rmp4_txt = (SCRIPTS / "render_mp4.py").read_text(encoding="utf-8")
        pm_txt = (SCRIPTS / "person_matting.py").read_text(encoding="utf-8")
        self.assertIn('quality="accurate"', rmp4_txt)
        self.assertNotIn('quality="balanced"', rmp4_txt)
        self.assertIn('quality: str = "accurate"', pm_txt)
        self.assertNotIn("setValue(4.2", pm_txt)
        self.assertNotIn("CIDisplacementDistortion", pm_txt)

        s21 = svt.get_visual_truth("style_21")
        s21_cut = next(b["layout"] for b in s21["scene_beats"] if b["layout"]["mode"] == "vision_portrait_cutout_pip")
        self.assertFalse(s21_cut["torn_paper"])

        # 验证 element_rules 允许 style_21 平滑白描边通过，同时未描边时仍按预期拦截
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            write_json(pdir / "06_packaging.json", {"style_id": "style_21", "visual_elements": []})
            write_json(pdir / "01_manifest.json", {"items": [{"role": "a_roll", "duration_sec": 12.0}, {"role": "b_roll", "duration_sec": 8.0}]})
            write_json(pdir / "04_edl.json", {
                "style_id": "style_21", "total_duration_sec": 12.0,
                "clips": [
                    {"clip_id": "c1", "track_role": "a_roll", "duration_sec": 4.0,
                     "pip_layout": {"mode": "vision_portrait_cutout_pip", "cutout": True, "outline_px": 14, "torn_paper": False}},
                    {"clip_id": "c2", "track_role": "a_roll", "duration_sec": 8.0},
                ],
            })
            res_ok = element_rules.audit(pdir)
            torn_rows_ok = [r for r in res_ok["rows"] if r[0] == "cutout" and r[1] == "撕纸锯齿白边"]
            self.assertTrue(torn_rows_ok and str(torn_rows_ok[0][4]).startswith("PASS"), torn_rows_ok)

            write_json(pdir / "04_edl.json", {
                "style_id": "style_21", "total_duration_sec": 12.0,
                "clips": [
                    {"clip_id": "c1", "track_role": "a_roll", "duration_sec": 4.0,
                     "pip_layout": {"mode": "vision_portrait_cutout_pip", "cutout": True, "outline_px": 0, "torn_paper": False}},
                    {"clip_id": "c2", "track_role": "a_roll", "duration_sec": 8.0},
                ],
            })
            res_fail = element_rules.audit(pdir)
            torn_rows_fail = [r for r in res_fail["rows"] if r[0] == "cutout" and r[1] == "撕纸锯齿白边"]
            self.assertTrue(torn_rows_fail and str(torn_rows_fail[0][4]).startswith("FAIL"), torn_rows_fail)

    def test_f12_multiclip_panel_spanning_sto_style24(self) -> None:
        # 构造真实 STO style_24 特征的 48 镜时间线（含 <3.2s 开场、b_roll 穿插、2x 变速与推近）
        clips: List[Dict[str, Any]] = []
        sentences: List[Dict[str, Any]] = []
        t = 0.0
        for i in range(48):
            dur = 1.2 if i % 3 == 0 else 1.15
            t0 = round(t, 4)
            t1 = round(t + dur, 4)
            f0 = int(round(t0 * FPS))
            f1 = int(round(t1 * FPS))
            role = "a_roll" if i % 2 == 0 else "b_roll"
            sid = f"s{i // 2 + 1}"
            if i % 2 == 0:
                sentences.append({"id": sid, "text": f"门店产品实测第{i // 2 + 1}点细节对比", "start_sec": t0, "end_sec": t1, "keywords": ["细节对比"]})
            clips.append({
                "clip_id": f"clip_{i + 1:03d}",
                "track_role": role,
                "media_path": f"media_{role}_{i % 3}.mp4",
                "sentence_id": sid,
                "source_in_sec": round(i * 2.5, 3),
                "source_out_sec": round(i * 2.5 + dur, 3),
                "timeline_in_sec": t0,
                "timeline_out_sec": t1,
                "timeline_in_frame": f0,
                "timeline_out_frame": f1,
                "duration_sec": dur,
                "speed": 2.0 if role == "b_roll" else 1.0,
                "punch_in_zoom": 1.15 if (i % 4 == 0) else 1.0,
            })
            t = t1
        total_dur = round(t, 4)

        sent_by_id = {s["id"]: s for s in sentences}
        sent_idx_map = {s["id"]: idx for idx, s in enumerate(sentences)}
        beats = svt.get_visual_truth("style_24")["scene_beats"]
        used_clip_ids: set = set()
        _HTML_SPAN_MODES = {
            "dark_circle_panel_pip", "dark_typewriter_card_pip", "framed_box_pip",
            "review_whiteboard_pip", "paper_outline_pip", "concept_diagram_pip",
        }
        _SPAN_ANY_ROLE_MODES = {"review_whiteboard_pip", "concept_diagram_pip", "dark_typewriter_card_pip", "framed_box_pip"}
        for b_i, beat in enumerate(beats):
            target_t = float(beat["frac"]) * total_dur
            max_d = float(beat["max_dur"])
            base_lay = dict(beat["layout"])
            _b_mode = base_lay["mode"]
            cands = [c for c in clips if c["clip_id"] not in used_clip_ids and not c.get("pip_layout")]
            chosen_c = min(cands, key=lambda c: abs(0.5 * (c["timeline_in_sec"] + c["timeline_out_sec"]) - target_t))
            _ci = clips.index(chosen_c)
            _target_beat_dur = max(4.0, min(5.6, max_d * 1.25))
            span_indices = [_ci]
            span_dur = float(chosen_c["duration_sec"])
            r = _ci + 1
            while r < len(clips) and span_dur < _target_beat_dur * 0.88:
                if clips[r]["clip_id"] in used_clip_ids or clips[r].get("pip_layout"):
                    break
                span_indices.append(r)
                span_dur += float(clips[r]["duration_sec"])
                r += 1
            span_clips = [clips[x] for x in sorted(span_indices)]
            for sc in span_clips:
                used_clip_ids.add(sc["clip_id"])
            f0_g = span_clips[0]["timeline_in_frame"]
            f1_g = span_clips[-1]["timeline_out_frame"]
            gdur = round((f1_g - f0_g) / float(FPS), 4)
            gid = f"v11_grp_{b_i:02d}"
            for sc in span_clips:
                sc["pip_layout"] = {
                    **base_lay,
                    "v11_panel_group_id": gid,
                    "v11_panel_group_dur": gdur,
                    "v11_panel_offset_sec": round((sc["timeline_in_frame"] - f0_g) / float(FPS), 4),
                    "single_source": True,
                }

        # 验证不删镜头（48 镜全部保留）、占比 >= 20%、每组 >= 3.0s 且 offset 严格单调递增
        self.assertEqual(len(clips), 48)
        wb_clips = [c for c in clips if (c.get("pip_layout") or {}).get("mode") == "review_whiteboard_pip"]
        wb_dur = sum(float(c["duration_sec"]) for c in wb_clips)
        self.assertGreaterEqual(wb_dur / total_dur, 0.20)
        self.assertTrue(any(c["track_role"] == "b_roll" for c in wb_clips))
        self.assertTrue(all(c["pip_layout"]["single_source"] is True for c in wb_clips if c["track_role"] == "b_roll"))
        groups: Dict[str, List[Tuple[float, float]]] = {}
        for c in wb_clips:
            pl = c["pip_layout"]
            groups.setdefault(pl["v11_panel_group_id"], []).append((pl["v11_panel_group_dur"], pl["v11_panel_offset_sec"]))
        for gid, vals in groups.items():
            self.assertGreaterEqual(vals[0][0], 3.0, gid)
            self.assertEqual(vals[0][1], 0.0, gid)
            for k in range(len(vals) - 1):
                self.assertGreater(vals[k + 1][1], vals[k][1], gid)

    def test_f12_real_step4_run_on_sto_style24_fixture(self) -> None:
        """端到端运行 step4_build_edl.run_step4() 验证真实 STO style_24 多镜跨屏分组与 <3.2s 首镜覆盖。"""
        ffmpeg_bin = render_mp4.find_bin("ffmpeg")
        if not ffmpeg_bin:
            return
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td) / "sto24"
            pdir.mkdir(parents=True, exist_ok=True)
            aroll = pdir / "norm_aroll.mp4"
            broll1 = pdir / "norm_b1.mp4"
            broll2 = pdir / "norm_b2.mp4"
            for mp, col in ((aroll, "0x20242C"), (broll1, "0x385240"), (broll2, "0x4B3B2A")):
                subprocess.run(
                    [ffmpeg_bin, "-y", "-f", "lavfi", "-i", f"color=c={col}:s=1080x1920:r=30:d=35",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mp)],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            sents = []
            events = []
            t = 0.0
            for i in range(18):
                t0, t1 = round(t, 3), round(t + 1.8, 3)
                sents.append({
                    "id": f"s{i + 1}",
                    "text": f"这盘菜第{i + 1}步现炒火候十足",
                    "keywords": ["现炒火候"],
                    "start_sec": t0, "end_sec": t1,
                    "start_frame": int(round(t0 * 30)), "end_frame": int(round(t1 * 30)),
                    "source_media": str(aroll),
                    "source_in_sec": t0, "source_out_sec": t1,
                })
                events.append({"id": f"e{i + 1}", "type": "sentence_start", "time_sec": t0, "frame": int(round(t0 * 30)), "sentence_id": f"s{i + 1}"})
                t = t1
            write_json(pdir / "01_manifest.json", {
                "items": [
                    {"id": "a1", "normalized_mp4": str(aroll), "role": "a_roll", "duration_sec": 35.0, "faces": [{"bbox": {"x": 0.35, "y": 0.28, "w": 0.30, "h": 0.16}}]},
                    {"id": "b1", "normalized_mp4": str(broll1), "role": "b_roll", "duration_sec": 35.0, "description": "现炒火候实拍"},
                    {"id": "b2", "normalized_mp4": str(broll2), "role": "b_roll", "duration_sec": 35.0, "description": "菜品细节出锅"},
                ]
            })
            write_json(pdir / "03_audio_events.json", {
                "locked_duration_sec": round(t, 3),
                "locked_duration_frames": int(round(t * 30)),
                "sentences": sents,
                "events": events,
            })
            # 在 03b_director_plan 中覆盖含 t=1.5s 首段白板的 style_scene_beats
            write_json(pdir / "03b_director_plan.json", {
                "style_id": "style_24",
                "style_scene_beats": [
                    {"target_sec": 1.2, "max_dur": 4.2, "layout": {"mode": "review_whiteboard_pip", "variant": "s24_whiteboard_multi", "bg_hex": "#EEF1F5", "accent_hex": "#F59E0B"}},
                    {"target_sec": 12.0, "max_dur": 4.6, "layout": {"mode": "review_whiteboard_pip", "variant": "s24_whiteboard_multi", "bg_hex": "#EEF1F5", "accent_hex": "#F59E0B"}},
                    {"target_sec": 24.0, "max_dur": 4.4, "layout": {"mode": "review_whiteboard_pip", "variant": "s24_whiteboard_multi", "bg_hex": "#EEF1F5", "accent_hex": "#F59E0B"}},
                ],
            })
            edl_res = s4.run_step4(pdir, track="store", style_id="style_24")
            clips = edl_res["clips"]
            wb = [c for c in clips if (c.get("pip_layout") or {}).get("mode") == "review_whiteboard_pip"]
            self.assertGreaterEqual(len(wb), 3)
            wb_dur = sum(float(c["duration_sec"]) for c in wb)
            self.assertGreaterEqual(wb_dur / float(edl_res["total_duration_sec"]), 0.20)
            self.assertTrue(any(float(c["timeline_in_sec"]) < 3.2 for c in wb))
            self.assertTrue(any(c["pip_layout"].get("v11_panel_group_id") for c in wb))

    def test_style_visual_truth_purged_and_m2_helpers(self) -> None:
        svt_txt = (SCRIPTS / "style_visual_truth.py").read_text(encoding="utf-8")
        for leaked in ("大轩王啦", "法找他", "二一组", "括我自己", "先看清底层前提"):
            self.assertNotIn(leaked, svt_txt)

        # verify extract_sentence_punch still rejects fragments via jieba boundary check
        w1 = svt.extract_sentence_punch({"text": "看到做法先找他的前提，海底捞的师徒制核心", "keywords": ["法找他", "前提"]}, max_chars=4)
        self.assertEqual(w1, "前提")

        for i in range(1, 25):
            sid = f"style_{i:02d}"
            self.assertIsInstance(svt.get_diagram_theme(sid), dict)
            self.assertIsInstance(svt.get_scene_beat_specs(sid), list)
            self.assertIsInstance(svt.get_callout_templates(sid), list)
            self.assertGreaterEqual(svt.get_top_safe_y(sid), 160)

        s06_cts = svt.get_callout_templates("style_06")
        self.assertTrue(any(t.get("variant") == "s06_vertical_poem" and t.get("callout_y") == 390 for t in s06_cts))

    def test_v12_bgm_lifted_robust_voice_spans_merges_soft_tail_aroll(self) -> None:
        import wave

        sr = 16000
        dur = 6.0
        n_samp = int(dur * sr)
        t_ax = np.arange(n_samp, dtype=np.float32) / float(sr)
        voice = np.zeros(n_samp, dtype=np.float32)
        # 0.0..4.15s 与 5.20..6.00s 为强主语音（RMS ~0.177, p80 ~0.177）
        m_main = (t_ax < 4.15) | (t_ax >= 5.20)
        voice[m_main] = 0.25 * np.sin(2.0 * np.pi * 220.0 * t_ax[m_main])
        # 4.70..4.90s 为弱句尾气声（RMS ~0.099）：无 BGM 时过 0.46*p80=0.081，混入 BGM 后低于 0.78*p80=0.138
        m_tail = (t_ax >= 4.70) & (t_ax < 4.90)
        voice[m_tail] = 0.14 * np.sin(2.0 * np.pi * 220.0 * t_ax[m_tail])

        def _write_wav(path: Path, arr: np.ndarray) -> None:
            pcm = np.clip(arr * 32767.0, -32768.0, 32767.0).astype(np.int16)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(pcm.tobytes())

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p_nobgm = root / "no_bgm"
            p_silent = root / "silent_bgm"
            p_bgm = root / "with_bgm"
            for p in (p_nobgm, p_silent, p_bgm):
                p.mkdir(parents=True, exist_ok=True)
                _write_wav(p / "03_locked_voice.wav", voice)
            _write_wav(p_silent / "03_locked_bgm.wav", np.zeros(n_samp, dtype=np.float32))
            _write_wav(p_bgm / "03_locked_bgm.wav", 0.22 * np.sin(2.0 * np.pi * 110.0 * t_ax))

            self.assertFalse(s4._has_nonsilent_bgm(p_nobgm))
            self.assertFalse(s4._has_nonsilent_bgm(p_silent))
            self.assertTrue(s4._has_nonsilent_bgm(p_bgm))

            spans_no = s4._compute_robust_voice_spans(voice, sr, has_nonsilent_bgm=False)
            spans_yes = s4._compute_robust_voice_spans(voice, sr, has_nonsilent_bgm=True)
            ov_no = sum(max(0.0, min(5.2, e) - max(4.5, s)) for s, e in spans_no)
            ov_yes = sum(max(0.0, min(5.2, e) - max(4.5, s)) for s, e in spans_yes)
            self.assertGreaterEqual(ov_no, 0.16)
            self.assertLess(ov_yes, 0.05)

            # 端到端运行 run_step4：存在非静音 BGM 时，三次自愈合并 [4.5, 5.2] 同源弱尾碎片
            aroll_mp4 = p_bgm / "norm_aroll.mp4"
            aroll_mp4.touch()
            write_json(p_bgm / "01_manifest.json", {
                "items": [{"id": "a1", "normalized_mp4": str(aroll_mp4), "role": "a_roll", "duration_sec": 10.0}]
            })
            sents = [
                {"id": "s1", "text": "前面先讲重点", "keywords": [], "source_media": str(aroll_mp4),
                 "start_sec": 0.0, "end_sec": 3.1, "start_frame": 0, "end_frame": 93,
                 "source_in_sec": 0.0, "source_out_sec": 3.1},
                {"id": "s2", "text": "这里把细节说清楚", "keywords": [], "source_media": str(aroll_mp4),
                 "start_sec": 3.1, "end_sec": 4.5, "start_frame": 93, "end_frame": 135,
                 "source_in_sec": 3.1, "source_out_sec": 4.5},
                {"id": "s3", "text": "尾音收住", "keywords": ["尾音"], "source_media": str(aroll_mp4),
                 "start_sec": 4.5, "end_sec": 5.2, "start_frame": 135, "end_frame": 156,
                 "source_in_sec": 4.5, "source_out_sec": 5.2},
                {"id": "s4", "text": "最后看结论", "keywords": [], "source_media": str(aroll_mp4),
                 "start_sec": 5.2, "end_sec": 6.0, "start_frame": 156, "end_frame": 180,
                 "source_in_sec": 7.0, "source_out_sec": 7.8},
            ]
            events = [
                {"id": "e1", "type": "sentence_start", "sentence_id": "s1", "time_sec": 0.0, "frame": 0},
                {"id": "e2", "type": "sentence_start", "sentence_id": "s2", "time_sec": 3.1, "frame": 93},
                {"id": "e3", "type": "sentence_start", "sentence_id": "s3", "time_sec": 4.5, "frame": 135},
                {"id": "e4", "type": "sentence_start", "sentence_id": "s4", "time_sec": 5.2, "frame": 156},
            ]
            write_json(p_bgm / "03_audio_events.json", {
                "locked_duration_sec": 6.0,
                "locked_duration_frames": 180,
                "sentences": sents,
                "events": events,
            })
            edl = s4.run_step4(p_bgm, track="store", style_id="style_07")
            isolated_tail = [
                c for c in edl["clips"]
                if c.get("track_role") == "a_roll"
                and abs(float(c["timeline_in_sec"]) - 4.5) <= 0.15
                and abs(float(c["timeline_out_sec"]) - 5.2) <= 0.10
            ]
            self.assertEqual(isolated_tail, [], f"弱尾 A-roll 碎片未被合并: {edl['clips']}")

    def test_v12_strategy_d_heals_head_whip_via_broll_swap_and_aroll_extension(self) -> None:
        ffmpeg_bin = render_mp4.find_bin("ffmpeg")
        if not ffmpeg_bin:
            return

        w, h = 360, 640

        def _render_synth(out_p: Path, dur: float, whip_head_sec: float = 0.0, stripe_offset: int = 0) -> None:
            from PIL import ImageFilter

            cmd = [
                ffmpeg_bin, "-y", "-v", "error",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
                "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out_p),
            ]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            assert proc.stdin is not None
            try:
                base = Image.new("RGB", (w, h), (26, 30, 38))
                dr = ImageDraw.Draw(base)
                for y in range(0, h, 36):
                    for x in range(0, w, 36):
                        if ((x // 36 + y // 36 + stripe_offset) % 2) == 0:
                            dr.rectangle([x, y, x + 35, y + 35], fill=(230, 224, 210))
                for x in range(0, w, 54):
                    dr.line([(x, 0), (x + 100, h)], fill=(40, 140, 210), width=5)
                n_frames = int(round(dur * FPS))
                for idx_f in range(n_frames):
                    t_f = idx_f / float(FPS)
                    if whip_head_sec > 0.0 and t_f < whip_head_sec:
                        shift = (idx_f * 90) % 240
                        fr = Image.new("RGB", (w, h), (18 + (idx_f % 2) * 140, 22 + ((idx_f + 1) % 2) * 120, 30))
                        fdr = ImageDraw.Draw(fr)
                        fdr.rectangle([shift, 80, shift + 110, h - 80], fill=(240, 240, 240) if idx_f % 2 == 0 else (30, 30, 30))
                        fr = fr.filter(ImageFilter.GaussianBlur(radius=10))
                    else:
                        fr = base
                    proc.stdin.write(np.asarray(fr, dtype=np.uint8).tobytes())
            finally:
                proc.stdin.close()
            err = (proc.stderr.read() if proc.stderr is not None else b"").decode("utf-8", "replace")
            if proc.stderr is not None:
                proc.stderr.close()
            if proc.wait() != 0:
                raise RuntimeError(err)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ar1 = root / "aroll_1.mp4"
            ar2 = root / "aroll_2.mp4"
            br1 = root / "broll_clean.mp4"
            br2 = root / "broll_tail.mp4"
            _render_synth(ar1, 16.0, whip_head_sec=0.0, stripe_offset=0)
            _render_synth(ar2, 4.0, whip_head_sec=0.35, stripe_offset=1)
            _render_synth(br1, 6.0, whip_head_sec=0.0, stripe_offset=2)
            _render_synth(br2, 12.0, whip_head_sec=0.0, stripe_offset=3)

            # Case A: prev_a (>=3s) + 有可匹配实拍 B-roll -> 策略 D 将 prev_a 转为 B-roll 并延展至干净事件点，使 ar2 跳过头部甩镜
            p_swap = root / "case_swap"
            p_swap.mkdir(parents=True, exist_ok=True)
            write_json(p_swap / "01_manifest.json", {
                "items": [
                    {"id": "a1", "normalized_mp4": str(ar1), "role": "a_roll", "duration_sec": 16.0},
                    {"id": "a2", "normalized_mp4": str(ar2), "role": "a_roll", "duration_sec": 4.0},
                    {"id": "b1", "normalized_mp4": str(br1), "role": "b_roll", "duration_sec": 6.0,
                     "safe_in_sec": 0.4, "safe_out_sec": 5.6,
                     "description": "大厅的卡座近景与座位布置", "semantic_tags": ["卡座", "座位"]},
                    {"id": "b2", "normalized_mp4": str(br2), "role": "b_roll", "duration_sec": 12.0,
                     "safe_in_sec": 0.4, "safe_out_sec": 11.6,
                     "description": "走廊灯光氛围实拍", "semantic_tags": ["灯光", "走廊"]},
                ]
            })
            sents_a = [
                {"id": "s0", "text": "先看第一点", "keywords": [], "source_media": str(ar1),
                 "start_sec": 0.0, "end_sec": 1.2, "start_frame": 0, "end_frame": 36,
                 "source_in_sec": 0.0, "source_out_sec": 1.2},
                {"id": "s1", "text": "再看第二点", "keywords": [], "source_media": str(ar1),
                 "start_sec": 1.2, "end_sec": 2.2, "start_frame": 36, "end_frame": 66,
                 "source_in_sec": 1.2, "source_out_sec": 2.2},
                {"id": "s2p", "text": "第三点先讲清楚", "keywords": [], "source_media": str(ar1),
                 "start_sec": 2.2, "end_sec": 3.4, "start_frame": 66, "end_frame": 102,
                 "source_in_sec": 2.2, "source_out_sec": 3.4},
                {"id": "s2", "text": "这里的卡座座位很宽敞", "keywords": ["卡座"], "source_media": str(ar1),
                 "start_sec": 3.4, "end_sec": 4.8, "start_frame": 102, "end_frame": 144,
                 "source_in_sec": 3.4, "source_out_sec": 4.8},
                {"id": "s3", "text": "中间再往下看看", "keywords": [], "source_media": str(ar2),
                 "start_sec": 4.8, "end_sec": 7.2, "start_frame": 144, "end_frame": 216,
                 "source_in_sec": 0.0, "source_out_sec": 2.4},
                {"id": "s4a", "text": "走廊的灯光很有氛围", "keywords": ["灯光"], "source_media": str(ar1),
                 "start_sec": 7.2, "end_sec": 9.8, "start_frame": 216, "end_frame": 294,
                 "source_in_sec": 5.2, "source_out_sec": 7.8},
                {"id": "s4b", "text": "走廊的灯光柔和自然", "keywords": ["走廊"], "source_media": str(ar1),
                 "start_sec": 9.8, "end_sec": 12.4, "start_frame": 294, "end_frame": 372,
                 "source_in_sec": 7.8, "source_out_sec": 10.4},
                {"id": "s5", "text": "灯光打在走廊很舒服", "keywords": ["灯光"], "source_media": str(ar1),
                 "start_sec": 12.4, "end_sec": 14.8, "start_frame": 372, "end_frame": 444,
                 "source_in_sec": 10.4, "source_out_sec": 12.8},
                {"id": "s6", "text": "欢迎大家到店体验", "keywords": [], "source_media": str(ar1),
                 "start_sec": 14.8, "end_sec": 16.0, "start_frame": 444, "end_frame": 480,
                 "source_in_sec": 12.8, "source_out_sec": 14.0},
            ]
            evs_a = [
                {"id": "e0", "type": "sentence_start", "sentence_id": "s0", "time_sec": 0.0, "frame": 0},
                {"id": "e1", "type": "sentence_start", "sentence_id": "s1", "time_sec": 1.2, "frame": 36},
                {"id": "e2p", "type": "sentence_start", "sentence_id": "s2p", "time_sec": 2.2, "frame": 66},
                {"id": "e2", "type": "sentence_start", "sentence_id": "s2", "time_sec": 3.4, "frame": 102},
                {"id": "e3", "type": "sentence_start", "sentence_id": "s3", "time_sec": 4.8, "frame": 144},
                {"id": "e_db", "type": "bgm_downbeat", "time_sec": 5.3, "frame": 159},
                {"id": "e4a", "type": "sentence_start", "sentence_id": "s4a", "time_sec": 7.2, "frame": 216},
                {"id": "e4b", "type": "sentence_start", "sentence_id": "s4b", "time_sec": 9.8, "frame": 294},
                {"id": "e5", "type": "sentence_start", "sentence_id": "s5", "time_sec": 12.4, "frame": 372},
                {"id": "e6", "type": "sentence_start", "sentence_id": "s6", "time_sec": 14.8, "frame": 444},
            ]
            write_json(p_swap / "03_audio_events.json", {
                "locked_duration_sec": 16.0, "locked_duration_frames": 480,
                "sentences": sents_a, "events": evs_a,
            })
            # 仅锁定 s3 所在 act 不插图卡，确保 s2[3.4..4.8] 为未锁定 prev_a、s3 为带甩镜 ca
            write_json(p_swap / "03b_director_plan.json", {
                "style_id": "style_07",
                "acts": [
                    {"act_id": "act_01", "sentence_ids": ["s0", "s1", "s2p", "s2"], "broll_policy": "allow_verified_broll"},
                    {"act_id": "act_02", "sentence_ids": ["s3"], "broll_policy": "lock_aroll"},
                    {"act_id": "act_03", "sentence_ids": ["s4a", "s4b", "s5", "s6"], "broll_policy": "allow_verified_broll"},
                ],
            })
            edl_swap = s4.run_step4(p_swap, track="store", style_id="style_07")
            ar2_clips_a = [c for c in edl_swap["clips"] if c.get("media_path") == str(ar2)]
            self.assertTrue(ar2_clips_a, edl_swap["clips"])
            self.assertGreaterEqual(float(ar2_clips_a[0]["source_in_sec"]), 0.40, ar2_clips_a[0])
            idx_ar2_a = edl_swap["clips"].index(ar2_clips_a[0])
            self.assertGreater(idx_ar2_a, 0)
            prev_swap = edl_swap["clips"][idx_ar2_a - 1]
            self.assertEqual(prev_swap["track_role"], "b_roll", edl_swap["clips"])
            self.assertEqual(prev_swap["media_path"], str(br1), prev_swap)
            self.assertGreaterEqual(float(prev_swap["timeline_out_sec"]), 5.25, prev_swap)

            # Case B: zero_broll_mode / 无可用 B-roll 兜底时，仅在安全源余量与 <=0.35s 句尾过冲内延展 prev_a 至干净切点
            p_ext = root / "case_extend"
            p_ext.mkdir(parents=True, exist_ok=True)
            write_json(p_ext / "01_manifest.json", {
                "items": [
                    {"id": "a1", "normalized_mp4": str(ar1), "role": "a_roll", "duration_sec": 16.0},
                    {"id": "a2", "normalized_mp4": str(ar2), "role": "a_roll", "duration_sec": 4.0},
                ]
            })
            sents_b = [
                {**sents_a[0]},
                {**sents_a[1]},
                {**sents_a[2]},
                {"id": "s2", "text": "这个工作台做工很扎实", "keywords": [], "source_media": str(ar1),
                 "start_sec": 3.4, "end_sec": 5.0, "start_frame": 102, "end_frame": 150,
                 "source_in_sec": 3.4, "source_out_sec": 5.0},
                {**sents_a[4]},
                {"id": "s4", "text": "最后汇总整体节奏与落地", "keywords": [], "source_media": str(ar1),
                 "start_sec": 7.2, "end_sec": 16.0, "start_frame": 216, "end_frame": 480,
                 "source_in_sec": 5.6, "source_out_sec": 14.4},
            ]
            evs_b = [
                {"id": "e0", "type": "sentence_start", "sentence_id": "s0", "time_sec": 0.0, "frame": 0},
                {"id": "e1", "type": "sentence_start", "sentence_id": "s1", "time_sec": 1.2, "frame": 36},
                {"id": "e2p", "type": "sentence_start", "sentence_id": "s2p", "time_sec": 2.2, "frame": 66},
                {"id": "e2", "type": "sentence_start", "sentence_id": "s2", "time_sec": 3.4, "frame": 102},
                {"id": "e3", "type": "sentence_start", "sentence_id": "s3", "time_sec": 4.8, "frame": 144},
                {"id": "e_db", "type": "bgm_downbeat", "time_sec": 5.3, "frame": 159},
                {"id": "e4", "type": "sentence_start", "sentence_id": "s4", "time_sec": 7.2, "frame": 216},
            ]
            write_json(p_ext / "03_audio_events.json", {
                "locked_duration_sec": 16.0, "locked_duration_frames": 480,
                "sentences": sents_b, "events": evs_b,
            })
            edl_ext = s4.run_step4(p_ext, track="store", style_id="style_08")
            ar2_clips_b = [c for c in edl_ext["clips"] if c.get("media_path") == str(ar2)]
            self.assertTrue(ar2_clips_b, edl_ext["clips"])
            self.assertGreaterEqual(float(ar2_clips_b[0]["source_in_sec"]), 0.40, ar2_clips_b[0])
            idx_ar2 = edl_ext["clips"].index(ar2_clips_b[0])
            self.assertGreater(idx_ar2, 0)
            prev_ext = edl_ext["clips"][idx_ar2 - 1]
            self.assertEqual(prev_ext["track_role"], "a_roll")
            self.assertGreaterEqual(float(prev_ext["timeline_out_sec"]), 5.25, prev_ext)


if __name__ == "__main__":
    unittest.main()
