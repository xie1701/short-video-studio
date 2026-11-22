#!/usr/bin/env python3
"""
[B-roll 与口播文案的语义桥]

问题背景（实测）：`inventory_media` 从文件名/OCR/显著性里抽出来的 `semantic_tags`
绝大多数是**画面描述**（"真人出镜"、"空镜特写"、"明亮环境"、"DSC"），而口播文案里的词是
**主题词**（"鲜羊排"、"成本"、"工序"）。两边字面永不相交，于是 `sem_hits` 恒为 0，
选片退化成"谁更稳、谁更锐、谁用得少"，这就是"B-roll 跟文案对不上"的根因。

本模块提供一层**离线的概念桥**（不联网、不调模型、可复现）：
  口播句子 --概念化--> {概念: 命中词}   B-roll 素材 --概念化--> {概念: 命中词}
两边在同一个概念空间里比对，产出可审计的分数与依据（basis），零依据就是 0 分。

同时提供 `describe_clip()`：把素材侧所有可用的文字信号（文件名中文词、OCR、人工补的
`manual_tags`/`description`、视觉索引字段）合成一段描述，再概念化。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 概念空间：domain（同场景/同行业的语义邻域）+ kind（实体/场景/动作/意图/人物）
# surfaces 是"会在中文文案或素材描述里出现的字面"，匹配时取最长命中。
# ---------------------------------------------------------------------------
_CONCEPTS: Dict[str, Dict[str, Any]] = {
    # ---- 后厨 / 餐饮 ----
    "食材": {"kind": "entity", "domain": "后厨餐食", "surfaces": ("食材", "羊排", "牛排", "猪肉", "牛肉", "鸡", "鱼", "海鲜", "虾", "蔬菜", "青菜", "水果", "面粉", "米饭", "冻肉", "鲜肉", "鲜", "配料", "佐料", "调料", "肉")},
    "切配": {"kind": "action", "domain": "后厨餐食", "surfaces": ("现切", "切配", "切肉", "切片", "切菜", "刀工", "改刀", "切")},
    "烹饪": {"kind": "action", "domain": "后厨餐食", "surfaces": ("烹饪", "炒", "烤", "炭火", "炖", "煮", "蒸", "焖", "煎", "下锅", "火候", "出锅", "腌")},
    "菜品成品": {"kind": "entity", "domain": "后厨餐食", "surfaces": ("菜品", "成品", "出餐", "摆盘", "上菜", "装盘", "口味", "好吃", "鲜嫩", "多汁")},
    "厨房环境": {"kind": "scene", "domain": "后厨餐食", "surfaces": ("厨房", "后厨", "灶台", "明档", "备菜", "洗碗", "油烟", "卫生", "干净")},
    # ---- 零售 / 门店 ----
    "门店空间": {"kind": "scene", "domain": "零售门店", "surfaces": ("门店", "店铺", "店面", "门头", "招牌", "货架", "陈列", "收银台", "大厅", "前台")},
    "商品": {"kind": "entity", "domain": "零售门店", "surfaces": ("商品", "货品", "产品", "包装", "款式", "型号", "尺码", "库存", "进货", "新品")},
    "成交": {"kind": "action", "domain": "零售门店", "surfaces": ("成交", "签单", "下单", "付款", "买单", "开单", "收款", "顾客", "客户到店", "到店", "卖", "卖出", "售卖", "客流")},
    # ---- 生产 / 车间 ----
    "产线": {"kind": "scene", "domain": "生产车间", "surfaces": ("车间", "产线", "流水线", "工厂", "机器", "设备", "模具", "加工", "生产")},
    "工序": {"kind": "action", "domain": "生产车间", "surfaces": ("工序", "流程", "步骤", "环节", "打磨", "组装", "装配", "质检", "抽检")},
    # ---- 办公 / 职场 ----
    "办公空间": {"kind": "scene", "domain": "办公职场", "surfaces": ("办公室", "工位", "会议室", "公司", "团队", "同事", "上班")},
    "文档单据": {"kind": "entity", "domain": "办公职场", "surfaces": ("文档", "合同", "报表", "台账", "账本", "明细", "清单", "发票", "单据", "方案", "PPT", "表格", "资料")},
    "沟通": {"kind": "action", "domain": "办公职场", "surfaces": ("开会", "沟通", "汇报", "谈判", "面谈", "复盘", "讨论")},
    # ---- 户外 / 街头 ----
    "街道": {"kind": "scene", "domain": "户外街头", "surfaces": ("街道", "路口", "马路", "街边", "小区", "广场", "公园", "门外", "夜晚街头")},
    "出行": {"kind": "action", "domain": "户外街头", "surfaces": ("走路", "骑车", "开车", "出差", "赶路", "通勤")},
    # ---- 物流 / 仓储 ----
    "仓库物流": {"kind": "scene", "domain": "物流仓储", "surfaces": ("仓库", "库房", "集装箱", "物流", "快递", "打包", "发货", "运输", "货车")},
    # ---- 装修 / 施工 ----
    "工地装修": {"kind": "scene", "domain": "装修工地", "surfaces": ("工地", "装修", "施工", "贴砖", "刷墙", "吊顶", "水电", "防水", "木工", "验收")},
    "建材": {"kind": "entity", "domain": "装修工地", "surfaces": ("板材", "瓷砖", "柜子", "板材", "门窗", "五金", "石材", "涂料", "定制")},
    # ---- 美业 / 医美 ----
    "美业": {"kind": "scene", "domain": "美业医美", "surfaces": ("美业", "美容", "皮肤", "护理", "医美", "祛斑", "痘", "面膜", "项目体验", "仪器")},
    # ---- 教育 / 知识 ----
    "课堂": {"kind": "scene", "domain": "教育课堂", "surfaces": ("课堂", "教室", "讲课", "板书", "课本", "学生", "家长", "作业", "考试", "辅导", "留学", "升学")},
    # ---- 车内 ----
    "车内": {"kind": "scene", "domain": "车内出行", "surfaces": ("车内", "车里", "驾驶座", "方向盘", "后座", "试驾", "车况", "里程", "二手车")},
    # ---- 家居 ----
    "家居": {"kind": "scene", "domain": "家居生活", "surfaces": ("客厅", "卧室", "沙发", "床", "餐桌", "阳台", "收纳", "装修效果", "全屋定制")},
    # ---- 数码 / 科技 ----
    "数码": {"kind": "entity", "domain": "数码科技", "surfaces": ("手机", "电脑", "屏幕", "软件", "App", "APP", "系统", "数据", "AI", "相机", "设备界面")},
    # ---- 健康 / 中医 ----
    "养生": {"kind": "entity", "domain": "医疗健康", "surfaces": ("药材", "中药", "针灸", "推拿", "养生", "体质", "调理", "穴位", "诊室")},
    # ---- 财务 / 法律 ----
    "财务法务": {"kind": "entity", "domain": "金融财务", "surfaces": ("成本", "利润", "报价", "贷款", "利率", "还款", "税务", "账", "合同条款", "律师", "诉讼", "判决", "证据", "毛利", "赚", "售价", "定价", "收入", "净利", "亏")},
    # ---- 跨域：人物 / 表达 ----
    "人物出镜": {"kind": "person", "domain": "人物", "surfaces": ("真人", "出镜", "口播", "老板", "师傅", "员工", "老板娘", "客人", "自己", "主播", "老师", "医生", "律师", "脸")},
    "环境空镜": {"kind": "scene", "domain": "环境", "surfaces": ("空镜", "特写", "环境", "远景", "全景", "细节特写", "细节", "现场", "实拍", "实景")},
    # ---- 跨域：意图（文案要"表达什么"，画面要怎么接） ----
    "价格数字": {"kind": "intent", "domain": "意图", "surfaces": ("价格", "多少钱", "成本", "便宜", "贵", "收费", "单价", "预算")},
    "对比": {"kind": "intent", "domain": "意图", "surfaces": ("对比", "差别", "不如", "反而", "以前", "现在", "前后", "两者", "没有对比")},
    "证据": {"kind": "intent", "domain": "意图", "surfaces": ("证据", "证明", "实拍", "看得见", "明细", "数据", "检测报告", "实测", "有据")},
    "流程": {"kind": "intent", "domain": "意图", "surfaces": ("流程", "步骤", "怎么", "先", "再", "然后", "接下来", "第一步")},
    "结果": {"kind": "intent", "domain": "意图", "surfaces": ("结果", "最终", "效果", "变成", "做成", "完成", "搞定", "落地")},
    "痛点": {"kind": "intent", "domain": "意图", "surfaces": ("痛点", "难", "麻烦", "问题", "错", "坑", "浪费", "白花", "后悔", "踩")},
    "时间周期": {"kind": "intent", "domain": "意图", "surfaces": ("小时", "分钟", "天", "个月", "年", "等了", "马上", "立刻", "久")},
}

# 概念互相解释的权重（script 概念 ←→ clip 概念）
_KIND_MATRIX: Dict[Tuple[str, str], float] = {
    ("entity", "entity"): 1.00,
    ("entity", "scene"): 0.55,
    ("entity", "action"): 0.50,
    ("scene", "scene"): 1.00,
    ("scene", "entity"): 0.55,
    ("scene", "action"): 0.45,
    ("action", "action"): 1.00,
    ("action", "entity"): 0.50,
    ("action", "scene"): 0.45,
    ("intent", "intent"): 0.55,
    ("intent", "entity"): 0.35,
    ("intent", "action"): 0.35,
    ("person", "person"): 0.60,
    ("person", "scene"): 0.25,
    ("intent", "person"): 0.20,
    ("entity", "person"): 0.25,
    ("action", "person"): 0.35,
    ("person", "intent"): 0.20,
    ("person", "entity"): 0.25,
    ("person", "action"): 0.35,
    ("scene", "person"): 0.25,
}

KIND_CN = {"entity": "实体", "scene": "场景", "action": "动作", "intent": "意图", "person": "人物"}

# 判定档位：≥HIT 才算"文案对得上"，≥WEAK 记"沾边"，其余 none
HIT = 0.60
WEAK = 0.35

_PRICE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:元|块|万|千|折|%|倍)")
_HANGUL_OR_LATIN = re.compile(r"[A-Za-z]{2,}")

_LOOKUP: List[Tuple[str, str]] = sorted(
    ((s, c) for c, meta in _CONCEPTS.items() for s in meta["surfaces"]),
    key=lambda x: len(x[0]),
    reverse=True,
)


def _concept_meta(name: str) -> Dict[str, Any]:
    return _CONCEPTS.get(name, {"kind": "entity", "domain": ""})


def concepts_of(text: str) -> Dict[str, List[str]]:
    """把一段中文文本概念化：返回 {概念: [命中的字面]}。最长匹配优先，重叠的不重复计。"""
    s = str(text or "")
    if not s.strip():
        return {}
    taken = [False] * len(s)
    out: Dict[str, List[str]] = {}
    for surface, concept in _LOOKUP:
        start = s.find(surface)
        while start >= 0:
            end = start + len(surface)
            if not any(taken[start:end]):
                for i in range(start, end):
                    taken[i] = True
                bucket = out.setdefault(concept, [])
                if surface not in bucket:
                    bucket.append(surface)
            start = s.find(surface, start + 1)
    if _PRICE_RE.search(s) and "价格数字" not in out:
        out.setdefault("价格数字", []).append(_PRICE_RE.search(s).group(0))
    return out


def describe_clip(item: Dict[str, Any]) -> str:
    """把素材侧所有可用文字信号合成一段描述（人工补的描述/标签优先，其次 OCR、中文文件名、视觉索引）。"""
    parts: List[str] = []
    for key in ("description", "user_note", "note", "caption"):
        v = str(item.get(key) or "").strip()
        if v:
            parts.append(v)
    for key in ("manual_tags", "semantic_tags"):
        for t in item.get(key) or []:
            t_s = str(t).strip()
            if t_s:
                parts.append(t_s)
    for key in ("semantic_description", "action", "shot_type", "shot_size", "camera_motion", "location"):
        v = str(item.get(key) or "").strip()
        if v:
            parts.append(v)
    for t in item.get("ocr_texts") or item.get("texts") or []:
        txt = str(t.get("text") if isinstance(t, dict) else t).strip()
        if txt:
            parts.append(txt[:24])
    for key in ("parent_dir", "rel_path", "folder"):
        v = str(item.get(key) or "").strip()
        if v:
            for tok in re.findall(r"[\u4e00-\u9fff]{2,8}", v):
                parts.append(tok)
    for src in (item.get("filename"), item.get("source_path"), item.get("normalized_mp4"), item.get("media_path")):
        if not src:
            continue
        stem = Path(str(src)).stem
        for tok in re.findall(r"[\u4e00-\u9fff]{2,8}", stem):
            parts.append(tok)
    if item.get("faces"):
        parts.append("真人出镜")
    if float(item.get("duration_sec") or 0.0) > 0:
        pass
    return " ".join(parts)


def relate(script_text: str, clip_item: Dict[str, Any], script_keywords: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """句子 ↔ 素材的语义关系。返回 score(0~1)/verdict/concepts/basis；零依据一定是 0 分。"""
    s_concepts = concepts_of(script_text)
    if script_keywords:
        for kw in script_keywords:
            for c, hits in concepts_of(str(kw)).items():
                bucket = s_concepts.setdefault(c, [])
                for h in hits:
                    if h not in bucket:
                        bucket.append(h)
    c_concepts = concepts_of(describe_clip(clip_item))
    if not s_concepts or not c_concepts:
        return {"score": 0.0, "verdict": "none", "concepts": {}, "basis": [], "reason": "no_text_signal" if not c_concepts else "no_script_concept"}

    basis: List[str] = []
    per_concept: Dict[str, float] = {}
    domain_factor = 0.5
    for sc, s_hits in s_concepts.items():
        sm = _concept_meta(sc)
        best = 0.0
        best_line = ""
        for cc, c_hits in c_concepts.items():
            cm = _concept_meta(cc)
            if sc == cc:
                w = _KIND_MATRIX.get((sm["kind"], cm["kind"]), 0.0)
                w = max(w, 0.9)  # 同一概念本身就说明对上了
            elif sm["domain"] and sm["domain"] == cm["domain"]:
                w = _KIND_MATRIX.get((sm["kind"], cm["kind"]), 0.0) * domain_factor * 2.0
                w = min(w, 0.55)
            else:
                w = 0.0
            if w > best:
                best = w
                best_line = f"「{s_hits[0]}」({KIND_CN[sm['kind']]})↔「{c_hits[0]}」({KIND_CN[cm['kind']]})"
        if best > 0:
            per_concept[sc] = round(best, 3)
            basis.append(best_line)
    if not per_concept:
        return {"score": 0.0, "verdict": "none", "concepts": {}, "basis": [], "reason": "no_overlap"}

    # 取最强两项做主分（避免"什么都沾一点"被堆高），第三项起按 0.25 递减累加
    ranked = sorted(per_concept.items(), key=lambda x: (-x[1], x[0]))
    score = ranked[0][1]
    if len(ranked) > 1:
        score += ranked[1][1] * 0.45
    for _, w in ranked[2:]:
        score += w * 0.25
    score = round(min(1.0, score), 3)
    verdict = "hit" if score >= HIT else ("weak" if score >= WEAK else "none")
    return {"score": score, "verdict": verdict, "concepts": per_concept, "basis": basis[:4], "reason": "ok"}


def legacy_score(script_text: str, clip_item: Dict[str, Any], script_keywords: Optional[Sequence[str]] = None) -> int:
    """旧口径（字面 tag 命中数），只用于对照复现"为什么以前对不上"。"""
    s_txt = str(script_text or "")
    kws = [str(k) for k in (script_keywords or [])]
    tags = [str(t) for t in ((clip_item.get("semantic_tags") or []) + (clip_item.get("manual_tags") or []))]
    return sum(1 for tg in tags if tg and (tg in s_txt or any(tg in k or k in tg for k in kws)))


def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总外来画面（真实 B-roll + 代码图卡）的语义对位情况。"""
    rows = list(rows)
    if not rows:
        return {"clips": 0, "hit": 0, "weak": 0, "none": 0, "hit_ratio": 0.0, "by_role": {}}
    by_role: Dict[str, Dict[str, int]] = {}
    for r in rows:
        role = str(r.get("role") or "")
        b = by_role.setdefault(role, {"clips": 0, "hit": 0, "weak": 0, "none": 0})
        b["clips"] += 1
        b[str(r.get("verdict") or "none")] = b.get(str(r.get("verdict") or "none"), 0) + 1
    hit = sum(1 for r in rows if r.get("verdict") == "hit")
    weak = sum(1 for r in rows if r.get("verdict") == "weak")
    return {
        "clips": len(rows),
        "hit": hit,
        "weak": weak,
        "none": len(rows) - hit - weak,
        "hit_ratio": round(hit / len(rows), 4),
        "aligned_ratio": round((hit + weak) / len(rows), 4),
        "by_role": by_role,
    }


def evaluate_edl(edl: Dict[str, Any], sentences_by_id: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """逐镜复核已生成的剪辑表：这个外来画面和它压着的口播句到底有没有关系。"""
    rows: List[Dict[str, Any]] = []
    for c in edl.get("clips") or []:
        if str(c.get("track_role")) not in ("b_roll", "code_broll"):
            continue
        sent = (sentences_by_id or {}).get(str(c.get("sentence_id") or "")) or {}
        text = str(c.get("sentence_text") or sent.get("text") or "")
        rel = c.get("sem_match") if isinstance(c.get("sem_match"), dict) and c.get("sem_match") else relate(text, c, sent.get("keywords"))
        rows.append({
            "clip_id": c.get("clip_id") or c.get("id"),
            "role": c.get("track_role"),
            "sentence": text,
            "media": Path(str(c.get("media_path") or "")).name,
            "verdict": rel.get("verdict"),
            "score": rel.get("score"),
            "basis": "；".join(rel.get("basis") or []) or str(rel.get("reason") or ""),
        })
    return rows
