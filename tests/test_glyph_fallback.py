"""逐字字形回退 / 覆盖门禁测试。

背景（用户实测 2026-11-24）：「阿里妈妈字体应该是不支持粤语文字的，很多字都显示不出来，换成思源宋体。」
AlimamaShuHeiTi-Bold.otf 的 cmap 缺 10 个粤语常用字（佢 冇 咗 哋 啲 喺 嘅 嘢 嚟 𠮶），
PIL/FreeType 不做字体回退，缺字直接画成空白 —— 这里锁住四条：自动回退、空链报错、门禁 fail-closed、.ttc 能加载。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))
sys.path.insert(0, str(ROOT / "dev_tools"))

from common import maybe_reexec_in_venv  # noqa: E402

maybe_reexec_in_venv()

from common import ASSETS_DIR, load_subtitle_templates, write_json  # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageFont  # noqa: E402

import glyph_fallback as G  # noqa: E402
import render_mp4 as R  # noqa: E402
import verify_glyph_coverage  # noqa: E402

CANTONESE = "𠮶边就系我哋嘅厨房"
ALIMAMA = str(ASSETS_DIR / "fonts" / "AlimamaShuHeiTi-Bold.otf")
HEAVY = str(ASSETS_DIR / "fonts" / "SourceHanSerifCN-Heavy.otf")
# 全机字体都画不出来的字（CJK 扩展 B 区的 𪚥），用来验 fail-closed
UNCOVERABLE = "\U0002A6A5"


def ink_pixels(font, ch: str) -> int:
    img = Image.new("L", (240, 240), 0)
    ImageDraw.Draw(img).text((30, 30), ch, font=font, fill=255)
    return sum(1 for px in img.tobytes() if px)


class GlyphFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    # ── 1. 缺字自动回退 ──
    def test_missing_glyph_falls_back_along_chain(self):
        # 主字体有：原样返回主字体
        f_ok = R.glyph_font_for("边", ALIMAMA, 75)
        self.assertEqual(Path(f_ok.path).name, "AlimamaShuHeiTi-Bold.otf")
        # 阿里妈妈缺「嘅」→ 回落到随包的思源宋体
        f_kw = R.glyph_font_for("嘅", ALIMAMA, 75)
        self.assertEqual(Path(f_kw.path).name, "SourceHanSerifCN-Heavy.otf")
        # 思源宋体也缺「𠮶」（U+2CA36）→ 落到系统 Heiti TC（.ttc index=0）
        f_ext = R.glyph_font_for("𠮶", ALIMAMA, 75)
        self.assertEqual(Path(f_ext.path).name, "STHeiti Medium.ttc")
        # 字号不变（只换字体，不改大小）
        self.assertEqual(int(f_ext.size), 75)
        # 回退字体必须真能画出墨迹（不是豆腐块/空白）
        self.assertGreater(ink_pixels(f_ext, "𠮶"), 0)
        # 回退链的有序性：每级只补前一级缺的字，不整体换字体
        ref, via = G.font_for_char("嘅", ALIMAMA)
        self.assertEqual(ref.filename, "SourceHanSerifCN-Heavy.otf")
        self.assertEqual(via, "SourceHanSerifCN-Heavy.otf")

    # ── 2. 回退链为空时报错（配置缺了不能静默） ──
    def test_empty_fallback_chain_raises(self):
        with self.assertRaises(G.GlyphCoverageError):
            G.require_coverage([("s1", "嘅喺𠮶")], ALIMAMA, chain=())
        # 配置本身缺失/为空 → strict 直接抛（fail-closed，不许当成“没有缺字”）
        broken = self.root / "font_aliases.json"
        broken.write_text(json.dumps({"entries": {}}, ensure_ascii=False), encoding="utf-8")
        G.glyph_fallback_chain.cache_clear()
        try:
            with mock.patch.object(G, "ALIAS_PATH", broken):
                with self.assertRaises(G.GlyphCoverageError):
                    G.glyph_fallback_chain(strict=True)
                self.assertEqual(G.glyph_fallback_chain(), ())
                # 链为空时默认 fail-closed：配置错了不许当成“没有缺字”放过
                with self.assertRaises(G.GlyphCoverageError):
                    G.coverage_report([("s1", "嘅")], ALIMAMA)
                # 只看覆盖结果时，主字体缺的字必须被报成缺字
                rep = G.coverage_report([("s1", "嘅")], ALIMAMA, strict_chain=False)
                self.assertEqual(rep["missing"], ["嘅"])
        finally:
            G.glyph_fallback_chain.cache_clear()

    # ── 3. 门禁 fail-closed ──
    def test_gate_fails_closed_and_records_coverage(self):
        good = self.root / "05_good.json"
        write_json(good, {"template_id": "style_21", "font_path": ALIMAMA,
                          "entries": [{"id": "sub_001", "text": CANTONESE}]})
        with mock.patch.object(sys, "argv", ["verify_glyph_coverage.py", "--subs", str(good), "--json"]):
            self.assertEqual(verify_glyph_coverage.main(), 0)

        bad = self.root / "05_bad.json"
        write_json(bad, {"template_id": "style_21", "font_path": ALIMAMA,
                         "entries": [{"id": "sub_001", "text": "我系牛哥"},
                                     {"id": "sub_002", "text": "呢个" + UNCOVERABLE + "字"}]})
        with mock.patch.object(sys, "argv", ["verify_glyph_coverage.py", "--subs", str(bad)]):
            self.assertEqual(verify_glyph_coverage.main(), 1)

        # step5 门禁：缺字即 exit 非 0，但仍然把 glyph_coverage 落库（带缺字与所在行）
        pdir = self.root / "pipe"
        pdir.mkdir()
        words = [{"word": ch, "start_sec": round(i * 0.2, 3), "end_sec": round((i + 1) * 0.2, 3),
                  "is_keyword": False} for i, ch in enumerate("呢个" + UNCOVERABLE + "字")]
        write_json(pdir / "03_audio_events.json", {"sentences": [{
            "id": "sent_001", "text": "".join(w["word"] for w in words),
            "start_sec": 0.0, "end_sec": 3.2, "keywords": [], "words": words,
        }]})
        from step5_subtitles import run_step5
        with self.assertRaises(SystemExit) as ctx:
            run_step5(pdir, track="store", template_id="style_21")
        self.assertNotEqual(int(ctx.exception.code or 0), 0)
        doc = json.loads((pdir / "05_subtitles.json").read_text(encoding="utf-8"))
        self.assertIn("glyph_coverage", doc)
        self.assertIn(UNCOVERABLE, doc["glyph_coverage"]["missing"])
        self.assertTrue(doc["glyph_coverage"]["missing_lines"])

    # ── 4. .ttc 能正常加载（并取到正确字面） ──
    def test_ttc_loads_and_covers_ext_b(self):
        ttc = "/System/Library/Fonts/STHeiti Medium.ttc"
        if not Path(ttc).is_file():
            self.skipTest("本机没有 STHeiti Medium.ttc")
        font = ImageFont.truetype(ttc, 75, index=0)
        self.assertEqual(Path(font.path).name, "STHeiti Medium.ttc")
        self.assertEqual(int(font.index), 0)
        self.assertTrue(G.font_has_glyph(ttc, 0, "𠮶"))
        self.assertGreater(ink_pixels(font, "𠮶"), 0)

    # ── 5. 一行混排：缺字不空白，字号/基线一致 ──
    def test_cantonese_line_renders_without_blank_glyphs(self):
        tpl = dict(load_subtitle_templates()["style_21"])
        tpl["bg_box_color"] = "#00000000"
        tpl["bg_box"] = "none"
        font_px = int(tpl.get("font_size", 75))
        fonts_used = set()
        for ch in CANTONESE:
            font = R.glyph_font_for(ch, ALIMAMA, font_px)
            fonts_used.add(Path(font.path).name)
            self.assertEqual(int(font.size), font_px, f"「{ch}」字号被改了")
            self.assertGreater(ink_pixels(font, ch), 0, f"「{ch}」画成了空白")

        self.assertIn("AlimamaShuHeiTi-Bold.otf", fonts_used)
        self.assertIn("SourceHanSerifCN-Heavy.otf", fonts_used)
        self.assertIn("STHeiti Medium.ttc", fonts_used)

        out = self.root / "line.png"
        w, h = R.render_subtitle_png({"text": CANTONESE, "keywords": [], "words": []}, tpl, ALIMAMA, out)
        self.assertTrue(out.is_file())
        self.assertGreater(w, 0)
        self.assertGreater(h, font_px)  # 有字形就有墨高，不是空图

    # ── 6. step5 正常路径把 glyph_coverage 写进 05_subtitles.json ──
    def test_step5_writes_glyph_coverage_field(self):
        pdir = self.root / "pipe_ok"
        pdir.mkdir()
        words = [{"word": ch, "start_sec": round(i * 0.2, 3), "end_sec": round((i + 1) * 0.2, 3),
                  "is_keyword": False} for i, ch in enumerate(CANTONESE)]
        write_json(pdir / "03_audio_events.json", {"sentences": [{
            "id": "sent_001", "text": CANTONESE, "start_sec": 0.0, "end_sec": 3.2,
            "keywords": ["嘅"], "words": words,
        }]})
        from step5_subtitles import run_step5
        run_step5(pdir, track="store", template_id="style_21")
        doc = json.loads((pdir / "05_subtitles.json").read_text(encoding="utf-8"))
        cov = doc["glyph_coverage"]
        self.assertEqual(cov["missing"], [])
        self.assertGreaterEqual(cov["chars"], 8)
        self.assertTrue(cov["fallback_used"], "思源宋体也缺 𠮶，必须记下回退字体")
        self.assertEqual(Path(doc["font_path"]).name, "SourceHanSerifCN-Heavy.otf")

    # ── 7. 花字/大字逐字绘制同样走回退（缺字不空白） ──
    def test_flower_sprite_draws_missing_glyphs_with_fallback(self):
        from render_template_overlays import _text_sprite

        def sprite(ch: str, font_path: str):
            return _text_sprite(ch, font_path, 700, 96, (255, 255, 255, 255), None, 0, None, 4, 3.0, 0)

        for ch in ("嘅", "喺"):
            via_primary = sprite(ch, ALIMAMA)          # 主字体缺字 → 应自动回落
            via_fallback = sprite(ch, HEAVY)           # 直接用回落字体 → 参照图
            self.assertIsNotNone(via_primary)
            self.assertEqual(via_primary.size, via_fallback.size, f"「{ch}」回退后尺寸应与回落字体一致")
            self.assertIsNone(
                ImageChops.difference(via_primary.convert("RGB"), via_fallback.convert("RGB")).getbbox(),
                f"「{ch}」没回落到 {Path(HEAVY).name}（画出来与豆腐块/主字体一致）",
            )


if __name__ == "__main__":
    unittest.main()
