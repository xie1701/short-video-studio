"""B-roll 选片必须按「这句口播讲了什么画面」排序，而不是按池子轮转。

用户实测：第 2 句讲「卡座、中空、灯笼、岭南元素」，配的 10 条全是 DJI 航拍；第 7 句讲
「桑拿鸡从山上捉回来」，配了一栋大楼外观。根因是选片根本没看画面描述（抽帧判读结果
落在 00_inventory/00_visual_index，manifest 里没有）。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "short-video-studio" / "scripts"))

from common import maybe_reexec_in_venv, write_json

maybe_reexec_in_venv()

import broll_match as BM


def _clip(cid, description="", tags=(), dur=10.0, sharp=60.0, stab=0.9, source="agent"):
    """一条候选素材（字段口径与 01_manifest/00_inventory 一致）。"""
    return {
        "id": cid,
        "normalized_mp4": f"/tmp/svs_fixture/{cid}.mp4",
        "role": "b_roll",
        "duration_sec": dur,
        "sharpness_score": sharp,
        "stability_score": stab,
        "description": description,
        "scene_tags_zh": list(tags),
        "scene_labels_source": source if (description or tags) else "",
        "best_in_sec": 0.3,
    }


class BrollMatchScoreTests(unittest.TestCase):
    """单句打分的五个硬要求：选对的、粤语能读、没有就诚实说没有、Agent 覆盖优先、门禁能 FAIL。"""

    def test_same_line_prefers_semantically_relevant_clip(self):
        line = "中间上边摆好多卡座，一个中空，跟住有啲灯笼、有啲造型、岭南元素嘅"
        pool = [
            _clip("drone", "无人机航拍施工工地全景", ["航拍", "工地"], sharp=900.0, stab=1.0),
            _clip("booth", "餐厅大堂里成排卡座，中间圆形挑空中庭", ["卡座", "大堂"], sharp=60.0, stab=0.6),
        ]
        best = BM.match_broll(line, pool)
        self.assertEqual(best["clip"]["id"], "booth", "画面越相关的越该先上，质量分只做同分裁决")
        self.assertEqual(best["tier"], "hit")
        self.assertTrue(any("卡座" in h for h in best["hits"]), best["hits"])
        self.assertEqual(best["source"], "rules")
        # 无关的那条不许硬凑成 hit
        bad = BM.score_clip(line, pool[0])
        self.assertNotEqual(bad["tier"], "hit", bad)

    def test_cantonese_line_matches_mandarin_description(self):
        cases = [
            ("我哋桑拿鸡个鸡，就系从山上捉翻嚟嘅", "博主在养殖场抓着一只走地鸡查看", "桑拿鸡"),
            ("𠮶边就系我哋嘅鱼池，到时就有好多新鲜嘅海鲜", "圆形水池里成群的活鱼游动", "鱼池"),
            ("中间有啲灯笼、有啲造型", "墙上挂着成排的中式灯笼装饰", "灯笼"),
        ]
        for line, desc, tag in cases:
            with self.subTest(line=line):
                r = BM.score_clip(line, _clip("x", desc, [tag]))
                self.assertEqual(r["tier"], "hit", (line, desc, r))
                self.assertTrue(r["hits"])
                self.assertIn(tag, "".join(r["hits"]))

    def test_no_relevant_material_is_reported_as_none(self):
        line = "我哋嘅四大天王：虾饺、排骨、凤爪同埋叉烧包"
        pool = [_clip("drone", "无人机航拍大楼外观商务街区", ["航拍", "大楼"]), _clip("bare", "")]
        for m in pool:
            r = BM.score_clip(line, m)
            self.assertEqual(r["tier"], "none", r)
            self.assertEqual(r["hits"], [], "没有依据就不许编依据")
        ranked = BM.rank_pool(line, pool)  # 全废池也不许崩
        self.assertEqual(len(ranked), 2)
        self.assertTrue(all(r["tier"] == "none" for r in ranked))
        self.assertIsNone(BM.match_broll(line, []))

    def test_blank_clip_text_says_so_instead_of_guessing(self):
        r = BM.score_clip("厨房两百平，纯手工现点现做", _clip("bare", ""))
        self.assertEqual(r["tier"], "none")
        self.assertIn("没有可用画面描述", r["why"])

    def test_reuse_and_short_duration_rank_lower(self):
        line = "我哋嘅鱼池，好多新鲜嘅海鲜"
        clip = _clip("fish", "圆形水池里成群的活鱼游动", ["鱼池"], dur=4.0)
        fresh = BM.score_clip(line, clip, used_count=0, need_sec=2.0)
        used = BM.score_clip(line, clip, used_count=3, need_sec=2.0)
        short = BM.score_clip(line, clip, used_count=0, need_sec=8.0)
        self.assertEqual(fresh["score"], used["score"], "复用只影响排序，不许把语义分也改掉")
        self.assertGreater(fresh["rank"], used["rank"])
        self.assertGreater(fresh["rank"], short["rank"], "时长不够的后排")

    def test_agent_override_file_wins(self):
        with tempfile.TemporaryDirectory() as td:
            pipe = Path(td)
            write_json(pipe / BM.AGENT_CHOICE_FILE, {
                "clips": [{"timeline_in_sec": 10.833, "media_path": "/tmp/svs_fixture/agent.mp4",
                           "why": "Agent 看过原片，这一刀必须是空镜"}]
            })
            choices = BM.load_agent_choices(pipe)
            self.assertEqual(len(choices["items"]), 1)
            pool = [_clip("relevant", "餐厅大堂成排卡座", ["卡座"], sharp=90.0),
                    _clip("agent", "", dur=9.0)]
            picked = BM.match_broll("中间摆好多卡座", pool, agent_choices=choices, timeline_in_sec=10.83)
            self.assertEqual(picked["clip"]["id"], "agent", "Agent 覆盖要优先采信")
            self.assertEqual(picked["source"], "agent")
            # 没有覆盖文件（或不在这一刀）时回到语义排序
            rules = BM.match_broll("中间摆好多卡座", pool, agent_choices=choices, timeline_in_sec=50.0)
            self.assertEqual(rules["source"], "rules")
            self.assertEqual(rules["clip"]["id"], "relevant")
            self.assertEqual(BM.load_agent_choices(None)["items"], [])

    def test_gate_fails_closed_on_none_ratio_and_hit_ratio(self):
        bad = {"clips": 10, "hit": 3, "weak": 2, "none": 5, "hit_ratio": 0.3, "none_ratio": 0.5}
        gate = BM.check_semantic_gate(bad)
        self.assertFalse(gate["pass"], "不达标必须 FAIL（fail-closed）")
        self.assertFalse(gate["raw_pass"])
        self.assertEqual(len(gate["fails"]), 2, gate)
        waived = BM.check_semantic_gate(bad, allow=True)
        self.assertTrue(waived["pass"], "显式放行才允许继续")
        self.assertTrue(waived["waived"])
        self.assertFalse(waived["raw_pass"], "放行不许把账改成达标")
        self.assertEqual(waived["fails"], gate["fails"])
        good = BM.check_semantic_gate({"clips": 10, "hit": 8, "weak": 1, "none": 1,
                                       "hit_ratio": 0.8, "none_ratio": 0.1})
        self.assertTrue(good["pass"])
        self.assertTrue(BM.check_semantic_gate({"clips": 0})["pass"], "没有外来画面就不用审")


class Step4SemanticSelectionTests(unittest.TestCase):
    """整条 step4 链路：选片按台词排序、账本写清依据、门禁真能停下流水线。"""

    SENTS = [
        "中间上边摆好多卡座，一个中空，跟住有啲灯笼、有啲造型、岭南元素嘅",
        "我哋嘅四大天王：虾饺、排骨、凤爪同埋叉烧包",
        "最后俾大家睇下我哋依家装修嘅进度如何",
        "所以食嘅就系鸡𠮶种原汁原味",
    ]

    def _pipeline(self, root, broll_specs):
        ar = root / "aroll.mp4"
        ar.write_bytes(b"fixture-mp4")
        items = [{"id": "ar1", "filename": "aroll.mp4", "role": "a_roll", "video_grade": "usable",
                  "normalized_mp4": str(ar), "duration_sec": 12.0,
                  "sharpness_score": 45.0, "stability_score": 0.9}]
        for spec in broll_specs:
            p = root / f"{spec['id']}.mp4"
            p.write_bytes(b"fixture-mp4")
            items.append({
                "id": spec["id"], "filename": p.name, "role": "b_roll", "video_grade": "usable",
                "normalized_mp4": str(p), "source_path": str(p), "duration_sec": 8.0,
                "best_in_sec": 0.4, "sharpness_score": 48.0, "stability_score": 0.9,
                "semantic_tags": [],
                "description": spec.get("description", ""),
                "scene_tags_zh": spec.get("tags", []),
                "scene_labels_source": "agent" if spec.get("description") else "",
            })
        sents, evts, t = [], [], 0.0
        for i, text in enumerate(self.SENTS, start=1):
            sents.append({"id": f"s{i}", "text": text, "keywords": [], "start_sec": round(t, 2),
                          "end_sec": round(t + 2.0, 2), "source_media": str(ar),
                          "source_in_sec": round(t, 2), "source_out_sec": round(t + 2.0, 2)})
            evts.append({"id": f"e{i}", "type": "sentence_start", "sentence_id": f"s{i}",
                         "time_sec": round(t, 2), "frame": int(round(t * 30))})
            t += 2.0
        pipe = root / "p"
        pipe.mkdir()
        write_json(pipe / "01_manifest.json", {"items": items})
        write_json(pipe / "03_audio_events.json", {
            "locked_duration_sec": round(t, 2), "locked_duration_frames": int(round(t * 30)),
            "sentences": sents, "events": evts,
        })
        return pipe

    def test_step4_picks_matching_broll_and_records_why(self):
        from step4_build_edl import run_step4

        specs = [
            {"id": "b_booth", "description": "餐厅大堂里成排卡座，中间圆形挑空中庭", "tags": ["卡座", "大堂"]},
            {"id": "b_dimsum", "description": "多层蒸笼蒸汽腾腾，笼内饺子近景", "tags": ["蒸笼", "饺子"]},
            {"id": "b_reno", "description": "两名施工人员在装修现场观察空旷毛坯环境", "tags": ["装修", "毛坯"]},
            {"id": "b_chicken", "description": "博主在养殖场抓着一只走地鸡查看", "tags": ["养殖场", "活鸡"]},
        ]
        with tempfile.TemporaryDirectory() as td:
            pipe = self._pipeline(Path(td).resolve(), specs)
            edl = run_step4(pipe, track="store", style_id="style_21", edit_profile="documentary")

            brolls = [c for c in edl["clips"] if c["track_role"] in ("b_roll", "code_broll")]
            self.assertTrue(brolls)
            by_sent = {}
            for c in brolls:
                by_sent.setdefault(c["sentence_id"], []).append(c)
            expect = {"s1": "b_booth", "s2": "b_dimsum", "s3": "b_reno", "s4": "b_chicken"}
            # 分镜号不是每句都必然有（刀法由风格真值定，这里不许为了测试去改镜数），
            # 但「哪句配了哪条素材」必须对得上：配错就说明选片又回到按池子轮转
            covered = 0
            for sid, want in expect.items():
                pics = by_sent.get(sid) or []
                if pics:
                    covered += 1
                got = [Path(str(c["media_path"])).stem for c in pics]
                self.assertTrue(all(want in g for g in got),
                                f"{sid} 应配 {want}（台词语义），实际 {got}")
            self.assertGreaterEqual(covered, 3, f"至少三句要落下外来画面：{ {k: len(v) for k, v in by_sent.items()} }")

            # 账本：每条都要有 score/tier/hits/why，且不许编依据
            for c in brolls:
                sm = c["sem_match"]
                for key in ("score", "tier", "hits", "why", "source"):
                    self.assertIn(key, sm, c)
                self.assertIn(sm["tier"], ("hit", "weak", "none"))
                if sm["tier"] == "none":
                    self.assertEqual(sm["hits"], [], "无依据的镜头不许编依据")
                else:
                    self.assertTrue(sm["hits"], "有依据的镜头必须写清凭什么")
            stat = edl["broll_semantic"]
            self.assertEqual(stat["clips"], len(brolls))
            self.assertEqual(len(stat["rows"]), len(brolls))
            self.assertIn("hit_ratio", stat)
            self.assertIn("none_ratio", stat)
            self.assertTrue(stat["gate"]["pass"], stat["gate"])

    def test_step4_gate_fails_closed_when_nothing_matches(self):
        from step4_build_edl import BrollSemanticGateError, run_step4

        specs = [{"id": f"b_blank{i}", "description": "", "tags": []} for i in range(1, 5)]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve() / "bad"
            base.mkdir()
            pipe = self._pipeline(base, specs)
            with self.assertRaises(BrollSemanticGateError):
                run_step4(pipe, track="store", style_id="style_21",
                          edit_profile="documentary", enforce_broll_gate=True)
            # 显式放行：不抛，但账本照实记「不达标」
            edl = run_step4(pipe, track="store", style_id="style_21", edit_profile="documentary",
                            allow_broll_mismatch=True, enforce_broll_gate=True)
            self.assertFalse(edl["broll_semantic"]["gate"]["raw_pass"])
            self.assertTrue(edl["broll_semantic"]["gate"]["waived"])
            # 剪辑表照写：出事也要留下可审计的账
            self.assertTrue((pipe / "04_edl.json").is_file())

    def test_step4_cli_exits_nonzero_without_waiver(self):
        specs = [{"id": f"b_blank{i}", "description": "", "tags": []} for i in range(1, 5)]
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            (base / "bad").mkdir()
            pipe = self._pipeline(base / "bad", specs)
            cmd = [sys.executable, str(ROOT / "short-video-studio" / "scripts" / "step4_build_edl.py"),
                   "--pipeline-dir", str(pipe), "--track", "store", "--style-id", "style_21",
                   "--edit-profile", "documentary"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stdout[-800:] + r.stderr[-800:])
            self.assertIn("B-roll", r.stdout)
            r2 = subprocess.run(cmd + ["--allow-broll-mismatch"], capture_output=True, text=True)
            self.assertEqual(r2.returncode, 0, r2.stdout[-800:] + r2.stderr[-800:])
            self.assertIn("放行", r2.stdout)


if __name__ == "__main__":
    unittest.main()
