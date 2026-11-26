"""V6.5 元素级物理编舞 + 克制微锚点的可测量回归。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_template_overlays as overlays
from common import detect_browser_renderer, find_bin
from kinetic_choreographer import _ticker_box as overlays_box, plan_kinetic_stage

FPS = 30
BROWSER = detect_browser_renderer()


def probe_video(path):
    probe = subprocess.run(
        [find_bin("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,pix_fmt,width,height,nb_frames",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(probe.stdout)["streams"][0]


def rgba_frames(path):
    source = probe_video(path)
    raw = subprocess.run(
        [find_bin("ffmpeg"), "-v", "error", "-i", str(path), "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(
        -1, source["height"], source["width"], 4,
    )


def render(elem, path, duration):
    return overlays.render_animated_overlay_mov(elem, path, duration_sec=duration, fps=FPS)


def red_pixels(frame):
    data = frame.astype(int)
    return int(((data[..., 0] > 150) & (data[..., 1] < 90) & (data[..., 2] < 90) & (data[..., 3] > 60)).sum())


def bright_pixels(frame, columns):
    data = frame.astype(int)[:, columns[0]:columns[1]]
    return int(((data[..., :3].min(axis=2) > 140) & (data[..., 3] > 90)).sum())


def gold_pixels(frame):
    data = frame.astype(int)
    return int(((data[..., 0] > 150) & (data[..., 1] > 110) & (data[..., 2] < 90)
                & (data[..., 3] > 60)).sum())


STRIKE = {
    "variant": "negation_line", "text_or_asset": "✕ 不是被骗", "theme_profile": "business_editorial",
    "font_size_px": 69, "element_choreo": "strike_slash", "kind": "kinetic_stage_item",
}
CARD = {
    "variant": "concept_card", "text_or_asset": "海底捞的师徒制", "theme_profile": "business_editorial",
    "card_lines": ["海底捞的师徒制", "✕ 师徒两个字", "✓ 分到钱"], "card_has_headline": True,
    "font_size_px": 72, "element_choreo": "stagger_card", "kind": "kinetic_stage_item",
}
NUMBER = {
    "variant": "hero_lime", "text_or_asset": "负债200万+", "theme_profile": "business_editorial",
    "font_size_px": 100, "element_choreo": "number_roll", "kind": "kinetic_stage_item",
}
TICKER = {
    "variant": "editorial_ticker", "text_or_asset": "百度文库", "theme_profile": "business_editorial",
    "font_size_px": 31, "element_choreo": "ticker_fade", "kind": "kinetic_stage_item",
}


class NoBrowserMixin:
    def no_browser(self):
        return patch.object(overlays, "detect_browser_renderer", return_value={"available": False})


class V65ElementMotionTests(NoBrowserMixin, unittest.TestCase):
    def test_strike_slash_is_transparent_qtrle_and_slashes_only_after_the_card(self):
        with tempfile.TemporaryDirectory() as directory, self.no_browser():
            mov = Path(directory) / "strike.mov"
            self.assertIsNotNone(render(STRIKE, mov, 1.0))
            source = probe_video(mov)
            self.assertEqual(source["pix_fmt"], "argb")
            self.assertEqual((source["width"] % 2, source["height"] % 2), (0, 0))
            frames = rgba_frames(mov)
            self.assertTrue(np.all(frames[0][0, 0] == 0))
            early, settled = red_pixels(frames[1]), red_pixels(frames[10])
            self.assertEqual(early, 0)
            self.assertGreater(settled, 200)
            self.assertGreater(settled, early * 10)

    @unittest.skipUnless(BROWSER.get("available"), "local Chrome and Playwright are unavailable")
    def test_strike_slash_matches_the_browser_svg_beat_table(self):
        with tempfile.TemporaryDirectory() as directory:
            mov = Path(directory) / "strike_web.mov"
            self.assertIsNotNone(render({**STRIKE, "out_anim": "fade_out"}, mov, 1.6))
            self.assertTrue(list(Path(directory).glob(".web-element-*.mov")))
            self.assertEqual(probe_video(mov)["pix_fmt"], "argb")
            frames = rgba_frames(mov)
            self.assertEqual(red_pixels(frames[1]), 0)
            self.assertGreater(red_pixels(frames[20]), 200)
            self.assertEqual(int(frames[-1][..., 3].sum()), 0)

    def test_stagger_card_reveals_title_then_negation_then_positive(self):
        with tempfile.TemporaryDirectory() as directory, self.no_browser():
            mov = Path(directory) / "card.mov"
            self.assertIsNotNone(render(CARD, mov, 1.2))
            frames = rgba_frames(mov)
            width = frames.shape[2]
            left = (0, width // 2)
            right = (width // 2, width)

            def texts(frame, columns):
                return bright_pixels(frame[int(frame.shape[0] * 0.45):], columns)

            self.assertEqual((texts(frames[1], left), texts(frames[1], right)), (0, 0))
            self.assertGreater(gold_pixels(frames[1]), 200)
            self.assertGreater(red_pixels(frames[7]), 60)
            self.assertEqual(texts(frames[7], right), 0)
            self.assertGreater(texts(frames[16], left), 200)
            self.assertGreater(texts(frames[16], right), 200)
            self.assertGreater(red_pixels(frames[16]), red_pixels(frames[7]) - 1)

    def test_number_roll_evolves_and_settles_on_the_spoken_value(self):
        with tempfile.TemporaryDirectory() as directory, self.no_browser():
            mov = Path(directory) / "number.mov"
            self.assertIsNotNone(render(NUMBER, mov, 1.0))
            frames = rgba_frames(mov)
            early, middle, settled = (frames[1], frames[4], frames[10])
            self.assertFalse(np.array_equal(early, middle))
            self.assertFalse(np.array_equal(middle, settled))
            ink = [int((frame[..., 3] > 80).sum()) for frame in (early, middle, settled)]
            self.assertLess(ink[0], ink[1])
            self.assertLess(ink[1], ink[2])
            boxes = [np.argwhere(frame[..., 3] > 80)[:, 1] for frame in (early, settled)]
            # 数字先用终值同字号居中排版：早帧更窄，左右两边都缩在终帧之内，整行不抖。
            self.assertGreater(int(boxes[0].min()), int(boxes[1].min()))
            self.assertLess(int(boxes[0].max()), int(boxes[1].max()))
            layout = overlays._number_roll_layout(
                NUMBER, overlays.find_commercial_font(["SourceHanSansCN-Bold.otf", "AlimamaShuHeiTi-Bold.otf"]),
                overlays.kinetic_item_paint(NUMBER), 1.0,
            )
            self.assertIsNotNone(layout)
            self.assertEqual(layout["roll_values"], [30, 90, 144, 182, 200])
            self.assertEqual(len(layout["layers"]), 5)
            self.assertIsNotNone(overlays.render_kinetic_stage_item_png(NUMBER))

    def test_pillar_progress_grid_fills_up_without_breaking_rounded_alpha(self):
        def gold_ratio(index, total):
            image = overlays.render_kinetic_stage_item_png({
                "variant": "pillar_badge", "text_or_asset": f"{index:02d}",
                "theme_profile": "business_editorial", "font_size_px": 69,
                "pillar_index": index, "pillar_total": total,
            })
            pixels = np.asarray(image)
            self.assertEqual((pixels[0, 0, 3], pixels[-1, -1, 3]), (0, 0))
            bottom = pixels[-4:]
            gold = (np.abs(bottom[..., :3].astype(int) - np.array([234, 179, 8])) < 26).all(axis=2)
            # 胶囊与进度格之间留 4px 干净间隙，进度格本身 4px 厚。
            self.assertEqual(int(pixels[-8:-4, :, 3].max()), 0)
            return float((gold & (bottom[..., 3] > 200)).sum())

        first, last = gold_ratio(1, 3), gold_ratio(3, 3)
        self.assertGreater(first, 0)
        self.assertGreater(last, first * 2)

    def test_element_choreo_fades_every_layer_and_vector_on_the_last_frame(self):
        for elem in (STRIKE, CARD, NUMBER):
            with self.subTest(choreo=elem["element_choreo"]), \
                    tempfile.TemporaryDirectory() as directory, self.no_browser():
                mov = Path(directory) / "fade.mov"
                self.assertIsNotNone(render({**elem, "out_anim": "fade_out"}, mov, 1.6))
                frames = rgba_frames(mov)
                self.assertGreater(int(frames[20][..., 3].sum()), 0)
                self.assertEqual(int(frames[-1][..., 3].sum()), 0)

        with tempfile.TemporaryDirectory() as directory, self.no_browser():
            mov = Path(directory) / "fade_strike.mov"
            self.assertIsNotNone(render({**STRIKE, "out_anim": "fade_out"}, mov, 1.6))
            frames = rgba_frames(mov)
            self.assertGreater(red_pixels(frames[20]), 200)
            self.assertEqual(red_pixels(frames[-1]), 0)

        with tempfile.TemporaryDirectory() as directory, self.no_browser():
            mov = Path(directory) / "no_fade.mov"
            self.assertIsNotNone(render(STRIKE, mov, 1.6))
            self.assertGreater(int(rgba_frames(mov)[-1][..., 3].sum()), 0)

    def test_editorial_ticker_only_lands_on_safe_long_quiet_windows(self):
        image = overlays.render_kinetic_stage_item_png(TICKER)
        self.assertIsNotNone(image)
        pixels = np.asarray(image)
        self.assertEqual(pixels[0, 0, 3], 0)
        self.assertTrue(np.any(pixels[..., 3] == 255))
        with tempfile.TemporaryDirectory() as directory, self.no_browser():
            mov = Path(directory) / "ticker.mov"
            self.assertIsNotNone(render(TICKER, mov, 1.6))
            frames = rgba_frames(mov)
            alphas = [int(frame[..., 3].sum()) for frame in (frames[0], frames[20], frames[-1])]
            self.assertEqual(alphas[0], 0)
            self.assertGreater(alphas[1], 0)
            self.assertLess(alphas[2], alphas[1] * 0.6)

        sentences = [
            {"id": "s0", "text": "公司倒闭那年我负债两百多万", "start_sec": 0.0, "end_sec": 4.0},
            {"id": "s1", "text": "第一点先问生意前提", "start_sec": 4.2, "end_sec": 8.0},
            {"id": "s2", "text": "行业报告都在百度文库", "start_sec": 9.6, "end_sec": 14.0},
            {"id": "s3", "text": "我们把每一笔现金流都记在共享表格里", "start_sec": 15.0, "end_sec": 20.0},
            {"id": "s4", "text": "这样月底复盘的时候一眼就能看明白", "start_sec": 21.0, "end_sec": 26.0},
            {"id": "s5", "text": "第二点核对现金流", "start_sec": 27.0, "end_sec": 31.0},
            {"id": "s6", "text": "讲完这些再看过去", "start_sec": 33.0, "end_sec": 40.0},
        ]
        audio = {"sentences": sentences, "locked_duration_sec": 40.0}

        def act(index, ids, primitive, headline="", items=(), accent="", budget=1):
            return {
                "act_id": f"act_{index:02d}", "sentence_ids": ids, "primitive": primitive,
                "visual_copy": {"headline": headline, "items": list(items), "accent_token": accent},
                "camera_intent": "steady_hold", "broll_policy": "lock_aroll", "sfx_budget": budget,
                "dramatic_role": "framework", "emotion_intensity": 0.8 if budget else 0.1,
            }

        plan = {
            "source": "agent", "theme_profile": "business_editorial",
            "acts": [
                act(1, ["s0"], "hero_intro_pop", "负债200万+"),
                act(2, ["s1"], "framework_pillar", "问生意前提", accent="01"),
                act(3, ["s2"], "plain_bridge", budget=0),
                act(4, ["s3"], "plain_bridge", budget=0),
                act(5, ["s4"], "plain_bridge", budget=0),
                act(6, ["s5"], "framework_pillar", "核对现金流", accent="02"),
                act(7, ["s6"], "plain_bridge", budget=0),
            ],
            "music_cues": [],
        }
        safe = plan_kinetic_stage(audio, director_plan=plan, composition_profile={"headroom_ratio": 0.24})
        tickers = [item for item in safe["items"] if item["variant"] == "editorial_ticker"]
        self.assertTrue(safe["stats"]["ticker"])
        self.assertEqual(len(tickers), 1)
        ticker = tickers[0]
        self.assertLessEqual(len(ticker["text_or_asset"]), 5)
        self.assertIsNone(ticker["sfx_role"])
        self.assertEqual(ticker["element_choreo"], "ticker_fade")
        self.assertFalse(any(cue.get("event_id") == ticker["id"] for cue in safe["items"] if cue.get("sfx_role")))
        self.assertIn(ticker["y_norm"], (-0.22, -0.05))
        self.assertIn(ticker["x_norm"], (-0.62, 0.62, -0.66, 0.66))
        box = overlays_box(ticker["text_or_asset"], ticker["x_norm"], ticker["y_norm"])
        self.assertGreaterEqual(box[1], 380)
        self.assertGreaterEqual(box[0], 48)
        self.assertLessEqual(box[2], 1080 - 48)
        self.assertLess(box[3], (0.5 + 0.31) * 1920)

        # 侧胸安全带落在脸上就放弃
        face_blocked = plan_kinetic_stage(audio, director_plan=plan, composition_profile={
            "face_bbox": {"x": 0.05, "y": 0.45, "w": 0.9, "h": 0.45},
        })
        self.assertFalse(face_blocked["stats"]["ticker"])
        # 只有泛指词时宁缺毋滥
        generic = dict(audio)
        generic["sentences"] = [
            {**row, "text": "大家都有自己的东西"} if row["id"] == "s2" else row for row in sentences
        ]
        self.assertFalse(plan_kinetic_stage(
            generic, director_plan=plan, composition_profile={"allow_behind_person": False},
        )["stats"]["ticker"])

        tight = {
            "sentences": sentences[:3],
            "locked_duration_sec": 14.0,
        }
        short_plan = {"source": "agent", "theme_profile": "business_editorial", "acts": [
            act(1, ["s0"], "hero_intro_pop", "负债200万+"),
            act(2, ["s1"], "framework_pillar", "问生意前提", accent="01"),
            act(3, ["s2"], "framework_pillar", "行业报告", accent="02"),
        ], "music_cues": []}
        short = plan_kinetic_stage(tight, director_plan=short_plan, composition_profile={"headroom_ratio": 0.24})
        self.assertFalse(short["stats"]["ticker"])
        self.assertFalse(any(item["variant"] == "editorial_ticker" for item in short["items"]))


if __name__ == "__main__":
    unittest.main()
