"""字幕/肤色黑盒量法的口径回归测试（2026-11-24 用户实测：三项质检连着挂，查下来大半是「量错对象」）。

这一组锁住五件事：
1. 画面自带的招牌/美术字（「拿火锅」「早茶」「点赞茶楼」）不算我们的字幕越界或跑偏；
2. 长行必须居中（step5 合并前先量宽 + 渲染落位保持口径居中）；
3. 亮底自适应投影：白字压亮墙时自动加深投影/补细描边到 WCAG ≥3.8，暗底一个像素都不动；
4. 避脸微移只在 SUBTITLE_SAFE_ZONE 内 ±60px，且水平中心仍锁 540px；
5. 肤色判据能把「调色把它推出去」和「素材本身逆光/剪影」分开，不硬掰也不假装通过。
"""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

from common import (  # noqa: E402
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SKIN_YCBCR_RANGE,
    SUBTITLE_CENTER_X,
    SUBTITLE_SAFE_ZONE,
    SUBTITLE_SYMMETRIC_MAX_W,
    glyph_contrast_ratio,
    load_subtitle_templates,
    maybe_reexec_in_venv,
)

maybe_reexec_in_venv()

import independent_verifier as IV  # noqa: E402
import render_mp4 as RM  # noqa: E402
import step5_subtitles as S5  # noqa: E402

FONT_CANDIDATES = [
    Path(ROOT) / "short-video-studio" / "assets" / "fonts" / "SourceHanSerifCN-Heavy.otf",
    Path(ROOT) / "short-video-studio" / "assets" / "fonts" / "SourceHanSansCN-Bold.otf",
]


def _font_path():
    for p in FONT_CANDIDATES:
        if p.is_file():
            return str(p)
    return None


def _centered_box(text_width=450):
    return {
        "x": SUBTITLE_CENTER_X - text_width // 2,
        "y": SUBTITLE_SAFE_ZONE["y_min"] + 90,
        "w": text_width,
        "h": 124,
    }


class SubtitleGeometryScopeTests(unittest.TestCase):
    """1. 画面自带文字不算我们字幕的越界/跑偏。"""

    def test_centered_plan_bbox_passes_even_if_footage_text_sits_in_same_row(self):
        # 05_subtitles 的口径：竖屏字幕居中在 x=540
        box = _centered_box(460)
        geo = IV.measure_subtitle_geometry({"final_bbox": box})
        self.assertAlmostEqual(box["x"] + box["w"] / 2.0, 540, places=1)
        self.assertTrue(geo["center_ok"])
        self.assertTrue(geo["in_safe_zone"])
        self.assertLessEqual(geo["center_dev_px"], 1.0)

    def test_footage_sign_ocr_box_is_not_our_subtitle_layer(self):
        # 实测：t=9.0s 全帧 OCR 在 (-7,1311) 读到门店招牌「拿火锅」，旧实现把它与字幕并成一行 -> 中心偏 196px
        our = [_centered_box(368)]
        sign_box = (-7, 1311, 121, 66)
        self.assertFalse(IV.ocr_box_is_our_subtitle(sign_box, our))
        # 我们自己的字幕行（383,1278,313,100）必须认成字幕层
        self.assertTrue(IV.ocr_box_is_our_subtitle((383, 1278, 313, 100), our))
        # 同一行但落在字幕框外的招牌仍不算：虚拟「合并后」的大框中心落在左侧画面
        self.assertFalse(IV.ocr_box_is_our_subtitle((-6, 1253, 102, 61), our))

    def test_wide_line_and_off_center_are_still_reported(self):
        # 真缺陷不许被放过：宽度超 640 / 整体偏右 都得报
        too_wide = {"x": 60, "y": 1270, "w": 700, "h": 124}
        geo = IV.measure_subtitle_geometry({"final_bbox": too_wide})
        self.assertFalse(geo["in_symmetric_zone"])
        self.assertFalse(geo["center_ok"])

    def test_text_match_ratio_tolerates_one_misread_char(self):
        self.assertGreater(IV.text_match_ratio("三分一二百平去制造", "三分一二百乎去制造"), 0.85)
        self.assertLess(IV.text_match_ratio("跟住有啲", "拿火锅"), 0.4)


class LongLineCenteringTests(unittest.TestCase):
    """2. 长行必须居中（换行/断句压进 640px，落位不推偏）。"""

    def test_place_xy_keeps_subtitle_centered(self):
        tpl = {"safe_y_center": 1334}
        for w in (240, 400, 620, SUBTITLE_SYMMETRIC_MAX_W):
            px, _py = RM.compute_subtitle_place_xy(w, 124, tpl)
            self.assertEqual(px, SUBTITLE_CENTER_X - w // 2, f"w={w} 必须严格居中")

    def test_overlong_png_still_centered_not_pushed_to_one_side(self):
        tpl = {"safe_y_center": 1334}
        px, _py = RM.compute_subtitle_place_xy(700, 124, tpl)
        self.assertEqual(px + 350, SUBTITLE_CENTER_X, "超宽兜底也不许把整行推到一侧")

    def test_merge_short_chunks_respects_width_budget(self):
        font = _font_path()
        chunks = [
            {"text": "三分之一", "start_sec": 0.0, "end_sec": 0.6, "words": [], "keywords": []},
            {"text": "两百平", "start_sec": 0.62, "end_sec": 1.0, "words": [], "keywords": []},
        ]
        merged = S5._merge_short_chunks(
            [dict(c) for c in chunks], min_chars=4, max_px=300, font_path=font, font_size=66
        )
        # 判的是「有没有并成一行」，不是「两百平这两个字还在不在」：并不到一起时它本来就该单独占一屏
        self.assertFalse(any("三分之一" in str(c["text"]) and "两百平" in str(c["text"]) for c in merged),
                         "超过宽度预算就不许合并成一行")
        self.assertEqual(len(merged), 2, "并不到一起就保持两块")
        loose = S5._merge_short_chunks(
            [dict(c) for c in chunks], min_chars=6, max_px=640, font_path=font, font_size=66
        )
        self.assertEqual(len(loose), 1, "宽度放得下时该并还是并")
        self.assertLessEqual(
            S5._measure_text_px(loose[0]["text"], font, 66)[0], 640 + 32, "合并后的行不能突破安全带"
        )


def _synthetic_bg(luma: int, w=CANVAS_WIDTH, h=200, noise=6, seed=7):
    rng = np.random.default_rng(seed)
    base = np.full((h, w, 3), luma, dtype=np.int16)
    base += rng.integers(-noise, noise + 1, size=base.shape)
    return np.clip(base, 0, 255).astype(np.uint8)


class AdaptiveShadowTests(unittest.TestCase):
    """3. 亮底自适应投影：亮底必须抬到 ≥3.8，暗底不动标定值。"""

    @classmethod
    def setUpClass(cls):
        cls.font = _font_path()
        if not cls.font:
            raise unittest.SkipTest("仓库内找不到可商用中文字体，跳过渲染类用例")
        tpls = load_subtitle_templates()
        cls.tpl = dict(tpls["style_21"])
        cls.entry = {
            "text": "供应俾大家选择",
            "words": [{"word": c, "start_sec": 0.0, "end_sec": 0.1} for c in "供应俾大家选择"],
            "keywords": [],
            "final_bbox": dict(x=230, y=1270, w=620, h=124),
        }

    def _place(self, w, h):
        return SUBTITLE_CENTER_X - w // 2, 1290

    def test_bright_background_gets_boosted_until_readable(self):
        """亮底可读性按「笔画紧邻那一圈最暗的垫底」判（ratio_effective）。

        有投影的模板在白墙上本来就被投影兜住了，此时不许乱加描边（会破坏 1:1 观感）；
        真的没有垫底（投影关掉）时才必须启动增强。两个方向都要测，否则就是橡皮章。
        """
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "sub.png"
            bg = _synthetic_bg(235)  # 接近白墙（背景亮度 0.83，与实测 t=48s 白鱼同一量级）
            w, h, meta = RM.render_subtitle_png_adaptive(
                dict(self.entry), self.tpl, self.font, out,
                bg_strip=bg, bg_strip_y0=1290, place_xy=self._place,
            )
            self.assertGreaterEqual(meta["contrast_after"], 3.8, "亮底必须可读（投影垫底也算）")

            # 关掉投影 = 白字直接压白墙：必须靠增强把对比度拉到 3.8 以上
            tpl_noshadow = {k: v for k, v in self.tpl.items() if k != "shadow"}
            tpl_noshadow["shadow"] = False
            out2 = Path(td) / "sub2.png"
            w2, h2, meta2 = RM.render_subtitle_png_adaptive(
                dict(self.entry), tpl_noshadow, self.font, out2,
                bg_strip=bg, bg_strip_y0=1290, place_xy=self._place,
            )
            self.assertGreaterEqual(meta2["contrast_after"], 3.8, "无垫底时必须靠增强把亮底字幕救到 ≥3.8:1")
            self.assertTrue(
                (meta2["shadow_boost"] > 1.0) or (meta2["stroke_extra"] > 0),
                "无垫底的白字压白墙必须真的启动增强（投影或描边）",
            )

    def test_dark_background_keeps_calibrated_parameters(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "sub.png"
            bg = _synthetic_bg(24)  # 暗底（标定工况）
            w, h, meta = RM.render_subtitle_png_adaptive(
                dict(self.entry), self.tpl, self.font, out,
                bg_strip=bg, bg_strip_y0=1290, place_xy=self._place,
            )
            self.assertEqual(meta["shadow_boost"], 1.0, "暗底必须保持已标定的 1:1 参数")
            self.assertEqual(meta["stroke_extra"], 0)
            self.assertGreater(meta["contrast_after"], 4.5)

    def test_glyph_contrast_metric_separates_shadow_strength(self):
        # 同一张字幕：投影加强前 vs 后，同一个度量必须真的变高
        with tempfile.TemporaryDirectory() as td:
            p1 = Path(td) / "a.png"
            p2 = Path(td) / "b.png"
            RM.render_subtitle_png(dict(self.entry), self.tpl, self.font, p1)
            RM.render_subtitle_png(dict(self.entry), self.tpl, self.font, p2, shadow_boost=2.2, stroke_extra=2)
            bg = _synthetic_bg(210)
            ratios = []
            for p in (p1, p2):
                with Image.open(p) as layer:
                    layer = layer.convert("RGBA")
                    px = SUBTITLE_CENTER_X - layer.width // 2
                    patch = Image.fromarray(bg[0:layer.height, px:px + layer.width]).convert("RGBA")
                    canvas = Image.alpha_composite(patch, layer)
                    ratios.append(glyph_contrast_ratio(np.asarray(canvas.convert("RGB")))["ratio"])
            self.assertGreater(ratios[1], ratios[0] + 0.5, "增强后的紧邻对比度必须实测更高")


class FaceAvoidShiftTests(unittest.TestCase):
    """4. 避脸微移只在安全区内 ±60px，且仍然严格居中。"""

    def setUp(self):
        tpls = load_subtitle_templates()
        self.tpl = dict(tpls["style_21"])
        self.font = _font_path()

    def test_shift_when_face_sits_on_default_line(self):
        box0, dy0 = S5._compute_authoritative_subtitle_bbox("我哋嘅四大天王", self.tpl, self.font, [])
        self.assertEqual(dy0, 0)
        # 把人脸框扣在默认字幕位置上
        face = {"x": box0["x"], "y": box0["y"], "w": box0["w"], "h": box0["h"]}
        box1, dy1 = S5._compute_authoritative_subtitle_bbox("我哋嘅四大天王", self.tpl, self.font, [face])
        self.assertNotEqual(dy1, 0, "压脸时必须挪")
        self.assertLessEqual(abs(dy1), S5.FACE_AVOID_MAX_SHIFT_PX, "位移不许超过 ±60px")
        self.assertEqual(box1["x"] + box1["w"] // 2, SUBTITLE_CENTER_X, "位移只走垂直，水平中心仍锁 540")
        self.assertGreaterEqual(box1["y"], SUBTITLE_SAFE_ZONE["y_min"])
        self.assertLessEqual(box1["y"] + box1["h"], SUBTITLE_SAFE_ZONE["y_max"])
        self.assertEqual(box1["y"] - box0["y"], dy1, "记录的位移量必须与 final_bbox 一致")

    def test_no_face_means_no_shift(self):
        box, dy = S5._compute_authoritative_subtitle_bbox("跟住有啲", self.tpl, self.font, [])
        self.assertEqual(dy, 0)
        self.assertEqual(box["x"] + box["w"] // 2, SUBTITLE_CENTER_X)


class SkinSectorClassificationTests(unittest.TestCase):
    """5. 肤色：调色推出的算违规，素材逆光/剪影标 source_limited。"""

    def test_warm_graded_face_is_violation(self):
        # 实测 t=0.0s：调色把 cr 从 167 推到 179.9
        v = IV.classify_skin_sample(cb=96.6, cr=179.9, yy_p50=118.5, yy_p90=166.0, skin_ratio=0.31)
        self.assertFalse(v["in_sector"])
        self.assertFalse(v["source_limited"])
        self.assertTrue(v["violation"])

    def test_backlit_silhouette_is_source_limited_not_violation(self):
        v = IV.classify_skin_sample(cb=97.0, cr=178.0, yy_p50=28.0, yy_p90=44.0, skin_ratio=0.05)
        self.assertFalse(v["in_sector"])
        self.assertTrue(v["source_limited"])
        self.assertEqual(v["source_limited_reason"], "face_in_shadow")
        self.assertFalse(v["violation"], "素材本身就黑的帧不许硬掰成「调色违规」")

    def test_healthy_skin_passes(self):
        r = SKIN_YCBCR_RANGE
        v = IV.classify_skin_sample(
            cb=(r["cb_min"] + r["cb_max"]) / 2.0,
            cr=(r["cr_min"] + r["cr_max"]) / 2.0,
            yy_p50=140.0,
            yy_p90=190.0,
            skin_ratio=0.4,
        )
        self.assertTrue(v["in_sector"])
        self.assertFalse(v["violation"])
        self.assertFalse(v["source_limited"])

    def test_insufficient_skin_pixels_is_flagged(self):
        v = IV.classify_skin_sample(cb=96.0, cr=180.0, yy_p50=120.0, yy_p90=170.0, skin_ratio=0.004)
        self.assertTrue(v["source_limited"])
        self.assertEqual(v["source_limited_reason"], "insufficient_skin_pixels")


if __name__ == "__main__":
    unittest.main()
