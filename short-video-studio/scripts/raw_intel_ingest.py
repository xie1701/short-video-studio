#!/usr/bin/env python3
"""
[阶段 1：一手原始材料接入与口语去 AI 味剧本检查器]
拒绝二手模板填空，直接从一手原始材料抽取客户真实语言与物证：
1. 原始评论区 / 微信客服导出 / 收银台高频提问文本 -> 提取客户原话顾虑动词、高频追问与真实痛点词组。
2. 原始不可伪造物证目录 (发票/原料/对比照/检测单) -> 建立物证资产索引。
3. 剧本「口语化 + 留存节拍 + 实证锚点」静态检查器：
   - 单句有效字数 > 16 字预警（一口气断句红线）
   - 拦截书面语/AI陈词滥调（赋能/综上所述/不仅如此/痛点/打造/90%的人/别再无效努力 等）
   - 检验首句物理锚点、每 6–8 秒模式中断 (Micro-Hook) 与连续平铺缺乏数字/实物证据警告
输出：pipeline/00_raw_intel.json 与 pipeline/00_script_lint.md
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import read_json, write_json

PAIN_TRIGGERS = [
    "怕", "坑", "贵", "假", "拼接", "预制", "冻肉", "冷冻", "添加剂", "多久",
    "地址", "在哪", "价格", "多少钱", "真假", "靠谱", "退", "包退", "售后", "翻车",
    "后悔", "智商税", "踩雷", "踩坑", "新鲜", "坏了", "偷工", "减料", "套路", "隐形",
    "质保", "保修", "教程", "零基础", "平替", "避坑", "虚标", "溢价", "对比", "实测",
    "原厂", "收费",
]

AI_CLICHE_BLACKLIST = [
    "综上所述", "赋能", "抓手", "不仅如此", "与此同时", "痛点", "打造",
    "90%的人", "别再无效努力", "让我们一起", "众所周知", "总而言之",
    "接下来让我们", "不容错过", "深度剖析", "闭环思维", "干货满满",
]

MICRO_HOOK_CUES = (
    "但是", "其实", "结果", "没想到", "记住", "关键", "你看", "看我", "真相",
    "为什么", "千万别", "反而", "到底", "?", "？", "!", "！",
)

PROOF_KEYWORDS = {
    "发票": "财务/进货凭证",
    "进货": "进货单据",
    "账单": "成本账单",
    "对比": "前后对比",
    "原料": "原材料实拍",
    "后厨": "制作环境",
    "检测": "检验/检测报告",
    "聊天": "真实客户反馈",
    "订单": "真实成交单据",
}

_TS_PREFIX_RE = re.compile(
    r"^\[?\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\]?\s*"
)


def _clean_utterance(raw_line: str) -> str:
    line = raw_line.strip()
    if not line:
        return ""
    # 剥离聊天记录常见导出前缀（精确匹配 YYYY-MM-DD HH:MM(:SS)，不贪婪吞掉无方括号整行文本）
    m_ts = _TS_PREFIX_RE.match(line)
    has_ts = bool(m_ts)
    if m_ts:
        line = line[m_ts.end():]
    if has_ts:
        line = re.sub(r"^[^：:\n]{1,18}[：:]\s*", "", line)
    else:
        line = re.sub(r"^(?:用户\w*|客户\w*|网友\w*|买家\w*|顾客\w*|客服\w*|@[^\s：:]{1,16})[：:]\s*", "", line)
    return line.strip()


def _is_url_or_num_token(val: str) -> bool:
    s = val.strip()
    return not s or s.isdigit() or s.startswith(("http://", "https://", "www."))


def parse_comments_sources(files: List[Path]) -> List[str]:
    utterances: List[str] = []
    for fp in files:
        if not fp.exists():
            continue
        suf = fp.suffix.lower()
        if suf == ".csv":
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                rows = list(csv.reader(f))
                if not rows:
                    continue
                text_col: Optional[int] = None
                header = [c.strip().lower() for c in rows[0]]
                for idx, col_name in enumerate(header):
                    if any(k in col_name for k in ("comment", "content", "text", "msg", "quote", "评论", "内容", "问题", "原话")):
                        text_col = idx
                        break
                data_rows = rows[1:] if text_col is not None else rows
                if text_col is None and data_rows:
                    col_scores = []
                    max_cols = max(len(r) for r in data_rows)
                    for ci in range(max_cols):
                        vals = [r[ci].strip() for r in data_rows if ci < len(r)]
                        valid = [v for v in vals if not _is_url_or_num_token(v)]
                        avg_l = (sum(len(v) for v in valid) / len(valid)) if valid else 0.0
                        zh_bonus = 1.5 if any(re.search(r"[\u4e00-\u9fff]", v) for v in valid) else 0.3
                        col_scores.append((avg_l * zh_bonus, ci))
                    text_col = max(col_scores)[1] if col_scores else 0
                for row in data_rows:
                    if text_col is not None and text_col < len(row):
                        c = _clean_utterance(row[text_col])
                        if len(c) >= 3 and not _is_url_or_num_token(c):
                            utterances.append(c)
        elif suf == ".json":
            raw = read_json(fp)
            if isinstance(raw, dict):
                cand_list = raw.get("comments") or raw.get("messages") or raw.get("items") or raw.get("utterances") or raw.get("sentences") or []
                for v in raw.values():
                    if not cand_list and isinstance(v, list):
                        cand_list = v
            elif isinstance(raw, list):
                cand_list = raw
            else:
                cand_list = []
            for item in cand_list:
                txt = item.get("text") or item.get("content") or item.get("comment") or "" if isinstance(item, dict) else str(item)
                c = _clean_utterance(str(txt))
                if len(c) >= 3:
                    utterances.append(c)
        else:
            for raw in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
                c = _clean_utterance(raw)
                if len(c) >= 3 and not c.startswith("#") and not c.startswith("---"):
                    utterances.append(c)
    seen = set()
    uniq: List[str] = []
    for u in utterances:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def extract_raw_intel(
    comments_files: List[Path],
    proof_dir: Optional[Path] = None,
    track: str = "store",
) -> Dict[str, Any]:
    utterances = parse_comments_sources(comments_files)
    trigger_counts: Counter[str] = Counter()
    pain_phrases: List[Dict[str, Any]] = []
    customer_questions: List[str] = []

    store_boost = {"地址", "在哪", "价格", "多少钱", "冻肉", "预制", "新鲜", "售后", "退", "隐形"}
    for u in utterances:
        hits = [kw for kw in PAIN_TRIGGERS if kw in u]
        for kw in hits:
            trigger_counts[kw] += (2 if (track == "store" and kw in store_boost) else 1)
        is_q = any(q in u for q in ("?", "？", "吗", "怎么", "多少", "哪", "是不是", "能不能", "会不会", "有没"))
        if is_q and u not in customer_questions:
            customer_questions.append(u)
        if hits or is_q:
            pain_phrases.append({
                "quote": u,
                "triggers": hits,
                "is_question": is_q,
                "score": len(hits) * 2 + (1 if is_q else 0),
            })

    pain_phrases.sort(key=lambda x: (-x["score"], len(x["quote"])))

    proof_assets: List[Dict[str, Any]] = []
    if proof_dir and proof_dir.exists():
        exts = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".pdf"}
        for f in sorted(proof_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            label = "实景物证"
            for k, v in PROOF_KEYWORDS.items():
                if k in f.name:
                    label = v
                    break
            proof_assets.append({
                "filename": f.name,
                "path": str(f.resolve()),
                "ext": f.suffix.lower(),
                "proof_type": label,
            })

    top_keywords = [k for k, _ in trigger_counts.most_common(10)]
    return {
        "step": "00_raw_intel",
        "track": track,
        "verified_by_real_users": len(utterances) > 0,
        "utterance_count": len(utterances),
        "top_pain_triggers": [{"word": k, "count": c} for k, c in trigger_counts.most_common(12)],
        "pain_keywords": top_keywords,
        "raw_pain_phrases": pain_phrases[:20],
        "customer_questions": customer_questions[:15],
        "proof_assets": proof_assets,
    }


def lint_script(script_items: List[Dict[str, Any]], raw_intel: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对口播台词做口语化断句、去 AI 陈词滥调、实证锚点与 6–8 秒留存节拍体检。"""
    issues: List[Dict[str, Any]] = []
    valid_items = [it for it in script_items if not it.get("is_filler", False) and str(it.get("text", "")).strip()]

    if valid_items:
        first_txt = str(valid_items[0]["text"]).strip()
        if re.match(r"^(大家好|哈喽|hello|今天我们来|今天给大家|我是)", first_txt, re.IGNORECASE):
            issues.append({
                "line": 1,
                "severity": "error",
                "code": "weak_opening",
                "text": first_txt,
                "message": "前3秒禁止寒暄/自我介绍开头，应直接抛出具体冲突、数字或客户原话痛点。",
            })

    chars_since_hook = 0
    dry_abstract_run = 0
    for idx, it in enumerate(valid_items, start=1):
        txt = str(it.get("text", "")).strip()
        eff_chars = re.sub(r"[\s，。！？、；：“”‘’（）.,!?]+", "", txt)
        char_len = len(eff_chars)

        if char_len > 16:
            issues.append({
                "line": idx,
                "severity": "warn",
                "code": "too_long_breath",
                "text": txt,
                "message": f"单句有效字数 {char_len} 字 > 16 字口播红线，建议拆短为两口短句以利呼吸与字幕单行排布。",
            })

        cliches = [c for c in AI_CLICHE_BLACKLIST if c in txt]
        if cliches:
            issues.append({
                "line": idx,
                "severity": "error",
                "code": "ai_cliche",
                "text": txt,
                "message": f"命中书面/AI陈词滥调 {cliches}，请替换为行业具体名词或客户原话口语。",
            })

        has_concrete = bool(re.search(r"\d+|半|两|三|块|元|年|天|小时|分钟|秒|斤|克|米|厘米|后厨|门店|合同|报告|票|图|表|实测|拆", txt))
        if has_concrete:
            dry_abstract_run = 0
        else:
            dry_abstract_run += 1
            if dry_abstract_run >= 4:
                issues.append({
                    "line": idx,
                    "severity": "warn",
                    "code": "abstract_fluff",
                    "text": txt,
                    "message": "连续 4 句缺乏具体数字、时间刻度或实物证据锚点，建议插入实拍数字或对比细节消除说教感。",
                })
                dry_abstract_run = 0

        emo = str(it.get("emotion", "info"))
        has_hook_cue = emo in ("hook", "twist", "persona", "cta") or any(c in txt for c in MICRO_HOOK_CUES)
        if idx > 1:
            if has_hook_cue:
                chars_since_hook = 0
            else:
                chars_since_hook += char_len
                if chars_since_hook > 34:
                    issues.append({
                        "line": idx,
                        "severity": "warn",
                        "code": "missing_micro_hook",
                        "text": txt,
                        "message": f"连续约 {chars_since_hook/4.5:.1f} 秒（{chars_since_hook}字）纯平铺陈述无转折/提问/物证提示，建议在此处插入 Micro-Hook 或视觉证据切换。",
                    })
                    chars_since_hook = 0

    pain_kws = (raw_intel or {}).get("pain_keywords", [])
    full_txt = "".join(str(it.get("text", "")) for it in valid_items)
    matched_pains = [k for k in pain_kws if k in full_txt]

    errors = sum(1 for i in issues if i["severity"] == "error")
    warns = sum(1 for i in issues if i["severity"] == "warn")
    return {
        "passed": errors == 0,
        "error_count": errors,
        "warn_count": warns,
        "sentence_count": len(valid_items),
        "matched_raw_pain_keywords": matched_pains,
        "issues": issues,
    }


def render_lint_markdown(lint_res: Dict[str, Any], raw_intel: Dict[str, Any]) -> str:
    honest_banner = (
        ""
        if raw_intel.get("verified_by_real_users")
        else "> ⚠️ **诚实基线声明**：当前未导入真实评论/客服原话，以上选题假设与角度均来自行业公式推演，**未经真实用户问题验证**。\n\n"
    )
    lines = [
        "# 一手原材萃取与剧本口语体检报告 (00_script_lint)",
        "",
        honest_banner.rstrip("\n"),
        f"- **原始评论/对话样本数**：{raw_intel.get('utterance_count', 0)} 条 | 实拍物证资产：{len(raw_intel.get('proof_assets', []))} 件",
        f"- **真实用户验证状态**：{'✅ 已获真实原话验证' if raw_intel.get('verified_by_real_users') else '⚠️ 仅公式推演（未经真实用户问题验证）'}",
        f"- **高频客户顾虑词**：{', '.join(raw_intel.get('pain_keywords', [])) or '（未提供原始评论文件）'}",
        f"- **剧本口语体检结果**：{'✅ PASS' if lint_res['passed'] else '❌ 存在必须修改的AI套话/开篇硬伤'} "
        f"（Error {lint_res['error_count']} / Warn {lint_res['warn_count']}）",
        "",
    ]
    if raw_intel.get("customer_questions"):
        lines += ["## 客户原话高频追问（可直接做开头嘴替）"]
        for q in raw_intel["customer_questions"][:6]:
            lines.append(f"- 「{q}」")
        lines.append("")

    if lint_res["issues"]:
        lines += [
            "## 剧本逐句体检问题",
            "",
            "| 句号 | 级别 | 类型 | 原句 | 编导修改建议 |",
            "| :---: | :---: | :--- | :--- | :--- |",
        ]
        for iss in lint_res["issues"]:
            lines.append(
                f"| #{iss['line']} | `{iss['severity'].upper()}` | `{iss['code']}` | "
                f"{iss['text']} | {iss['message']} |"
            )
    else:
        lines.append("剧本口语自然度、断句节奏与 Micro-Hook 节拍全部合格。")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="一手原始材料接入与口语去AI味剧本检查器")
    parser.add_argument("--comments-file", nargs="*", default=[], help="同行评论区/客服聊天导出文件(.txt/.md/.csv)")
    parser.add_argument("--proof-dir", help="门店原始物证材料目录")
    parser.add_argument("--script-json", help="待体检口播文案 JSON")
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--output", required=True, help="输出 00_raw_intel.json 路径")
    args = parser.parse_args()

    comment_paths = [Path(p) for p in args.comments_file]
    proof_p = Path(args.proof_dir) if args.proof_dir else None
    intel = extract_raw_intel(comment_paths, proof_dir=proof_p, track=args.track)

    if args.script_json and Path(args.script_json).exists():
        raw_script = read_json(Path(args.script_json))
        script_items = (
            raw_script.get("sentences") or raw_script.get("segments") or []
            if isinstance(raw_script, dict)
            else raw_script
        )
        lint_res = lint_script(script_items, raw_intel=intel)
    else:
        lint_res = lint_script([], raw_intel=intel)

    intel["script_lint"] = lint_res
    out_json = Path(args.output)
    write_json(out_json, intel)
    lint_md = out_json.parent / "00_script_lint.md"
    lint_md.write_text(render_lint_markdown(lint_res, intel), encoding="utf-8")
    print(
        f"[raw_intel] 已萃取原话 {intel['utterance_count']} 条 / 物证 {len(intel['proof_assets'])} 件，"
        f"剧本检查 Error={lint_res['error_count']} Warn={lint_res['warn_count']} -> {out_json}"
    )


if __name__ == "__main__":
    main()
