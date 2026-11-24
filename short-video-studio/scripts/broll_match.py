#!/usr/bin/env python3
"""
[B-roll 选片的语义匹配器：按这句台词「讲了什么画面」排序素材]

问题（用户实测反馈）：第 2 句讲「卡座、中空、灯笼、岭南元素」，配的 10 条全是 DJI 航拍；
第 7 句讲「桑拿鸡从山上捉回来」，配了一栋『泰然食集』大楼外观。根因不是没有抽帧判读
（`scene_labels_source=agent` 已经 70/70），而是**选片环节根本没看画面描述**——它按
「谁更稳、谁更锐、谁用得少」轮转池子，画面自然对不上台词。

本模块只做一件事：给定一句台词 + 一批候选素材（带 scene_tags_zh / description / note /
时长 / 清晰度 / 稳定性），算出「这条画面像不像在说这句台词」，并排出先后。

设计原则：
1. **纯离线、确定性、无网络、不调大模型**——同样的输入永远同一结果，可复现、可审计。
2. **概念空间而不是字面 tag**：台词是主题词（卡座/桑拿鸡/岭南元素），素材侧是画面描述
   （「男子在装修现场站立远眺」），两边字面永不相交，必须靠同义词表 + 小概念表搭桥。
3. **粤语要能读**：口播里满是「嘅/咗/喺/𠮶/啲」，剥掉虚词后概念才抽得出来。
4. **档位要诚实**：素材侧真的没有可用描述，或描述与台词没有公共概念，就是 `none`，
   不许为了好看硬凑 `hit`（下游门禁就是查这个，宁可 FAIL 也不许编）。
5. **Agent 覆盖**：若流水线里已经有 `04b_broll_choice.json`（Agent 逐句指定了素材），
   优先采信它，并在结果里标 `source="agent"`，方便回溯「这条是谁选的」。

对外主入口：`score_clip()` 单条打分，`rank_pool()` 整池排序，`match_broll()` 直接取最优，
`check_semantic_gate()` 门禁判定（fail-closed）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 常量集中定义：改权重/阈值只动这里
# ---------------------------------------------------------------------------
AGENT_CHOICE_FILE = "04b_broll_choice.json"

TIER_HIT = "hit"
TIER_WEAK = "weak"
TIER_NONE = "none"

# 档位阈值：**具体**概念对上才算 hit（0.62×kind 系数）；只有泛场景词沾上（如一句讲茶楼、
# 画面只有「门店」两个字）落在 weak——「同场景」不等于「对得上这句话」。
HIT_MIN = 0.60
WEAK_MIN = 0.18

# 打分：强概念按命中顺序递减（第一项最值钱），弱概念与整句重叠只做补充
STRONG_WEIGHTS: Tuple[float, ...] = (0.62, 0.20, 0.08)
# 软命中：概念对上了，但有一边只用了泛称（台词说「茶楼」，画面标签只有「门店」）
# 这种只能算「同场景」，不能算「对得上这句话」
SOFT_WEIGHTS: Tuple[float, ...] = (0.30, 0.10)
SOFT_CAP = 0.32
SOFT_TERM_FACTOR = 0.55
WEAK_EACH = 0.10
WEAK_CAP = 0.20
# 概念越具体越可信：具体物件/食材/菜名 > 动作 > 泛场景 > 纯人物 > 抽象意图。
# 否则「招牌」碰上一张门店外景就能压过「虾饺」碰上一笼真饺子。
KIND_FACTOR: Dict[str, float] = {
    "entity": 1.00, "action": 0.95, "scene": 0.85, "person": 0.80, "intent": 0.70,
}
OVERLAP_WEIGHT = 0.12
OVERLAP_MIN = 0.25
AGENT_SCORE_BONUS = 0.04  # 标签来自抽帧判读时才加，且只加在「已经有命中」的分数上

# 排序用（语义分是主序，以下只做同分裁决/硬约束）
QUALITY_SHARP_DIV = 200.0
QUALITY_SHARP_CAP = 6.0
QUALITY_STAB = 4.0
AGENT_LABEL_RANK_BONUS = 3.0
REUSE_PEN = 12.0
SHORT_PEN = 15.0
TINY_DUR_SEC = 1.2

# 门禁：默认 fail-closed。命中率不够、或「无依据」镜头超过 1/4，就必须 FAIL
MAX_NONE_RATIO = 0.25
MIN_HIT_RATIO = 0.45

# ---------------------------------------------------------------------------
# 小概念表：concept -> 会出现在台词或素材描述里的字面
#   terms   = 判定「命中这个概念」的字面
#   related = 只能算「沾边」(weak) 的相邻概念名
# 只收「画面能拍出来」的名词/动作；景别词（近景/特写/全景）故意不收——
# 它们每条描述都有，收进来等于给所有素材加分，等于没匹配。
# ---------------------------------------------------------------------------
_CONCEPTS: Dict[str, Dict[str, Any]] = {
    # ---- 门店 / 空间 ----
    "座位卡座": {
        "kind": "entity",
        "terms": ("卡座", "卡位", "散台", "雅座", "座位", "餐桌", "餐台", "台位", "booth"),
        "related": ("空间挑空", "门店茶楼"),
    },
    "空间挑空": {
        "kind": "entity",
        "terms": ("中空", "挑空", "挑高", "空间展示", "空间", "大厅", "大堂", "厅面", "空旷", "空场地", "毛坯"),
        "related": ("门店茶楼", "装修进度"),
    },
    "灯笼装饰": {
        "kind": "entity",
        "terms": ("灯笼", "灯饰", "吊灯", "挂饰", "装饰", "造型", "圆窗", "花窗", "圆环", "摆件"),
        "related": ("岭南风格", "装修进度"),
    },
    "岭南风格": {
        "kind": "entity",
        "terms": ("岭南", "中式", "古风", "国潮", "元素"),
        "related": ("灯笼装饰", "装修进度"),
    },
    "门店茶楼": {
        "kind": "entity",
        # 「招牌」不收：台词里的「招牌菜」和画面里的「招牌」是两回事，收进来只会让门店外景
        # 抢走讲菜品的句子
        "terms": ("茶楼", "门头", "店招", "外立面", "建筑外观", "大楼", "商业街",
                  "店面", "店铺"),
        # 泛称：店面可以是任何店，「门店」两个字不足以证明画面就是这句说的那家店
        "weak_terms": ("门店", "餐厅", "品牌", "商标", "店"),
        "related": ("街区客流", "座位卡座"),
    },
    "街区客流": {
        "kind": "scene",
        "terms": ("街道", "路人", "客流", "路过", "门口", "广场", "街头"),
        "related": ("门店茶楼",),
    },
    # ---- 后厨 / 出品 ----
    "厨房后厨": {
        "kind": "entity",
        "terms": ("厨房", "后厨", "灶台", "明档", "备菜", "操作台", "厨师", "案板"),
        "related": ("手工现做", "出餐成品"),
    },
    "手工现做": {
        "kind": "action",
        "terms": ("手工", "现做", "现点", "现制", "现包", "现切", "现杀", "现蒸", "现炸",
                  "包饺子", "包馅", "擀皮", "捏合", "切肉", "切鱼", "剁", "刀工", "手部特写"),
        "related": ("厨房后厨", "点心虾饺"),
    },
    "点心虾饺": {
        "kind": "entity",
        "terms": ("虾饺", "饺子", "蒸饺", "煎饺", "点心", "蒸笼", "烧卖", "虾"),
        "related": ("手工现做", "早茶饮茶"),
    },
    "排骨凤爪叉烧": {
        "kind": "entity",
        # 不收单字「包」：会误命中「包饺子/包馅/包装」
        "terms": ("排骨", "凤爪", "叉烧", "叉烧包", "包子", "肠粉", "干炒", "炒面", "牛河", "烧味"),
        "related": ("点心虾饺", "早茶饮茶"),
    },
    "早茶饮茶": {
        "kind": "entity",
        "terms": ("早茶", "饮茶", "喝茶", "叹茶", "一盅两件", "茶市", "茶"),
        "related": ("点心虾饺", "排骨凤爪叉烧"),
    },
    "出餐成品": {
        "kind": "entity",
        "terms": ("出餐", "成品", "摆盘", "上菜", "蒸菜", "菜品", "热气", "蒸汽", "揭锅", "锅盖"),
        "related": ("厨房后厨", "点心虾饺"),
    },
    # ---- 食材 / 生鲜 ----
    "桑拿鸡": {
        "kind": "entity",
        "terms": ("桑拿鸡", "蒸鸡", "活鸡", "鸡群", "喂鸡", "捉鸡", "抓鸡", "走地鸡", "养殖场", "山上", "鸡"),
        "related": ("食材", "出餐成品"),
    },
    "鱼池海鲜": {
        "kind": "entity",
        "terms": ("鱼池", "海鲜", "海鲜区", "活鱼", "水族箱", "水池", "带子", "蚌", "蟹", "鱼", "水台"),
        "related": ("食材",),
    },
    "食材": {
        "kind": "entity",
        # 「新鲜/溯源」是品质词不是物件：收了它们，「养殖场溯源」会因为「这句说新鲜」判成对上
        "terms": ("食材", "原料", "生肉", "肉片", "禽肉", "金针菇",
                  "羊排", "牛排", "猪排", "排骨", "牛肉", "猪肉", "羊肉", "鸡肉", "青菜", "蔬菜"),
        "related": ("厨房后厨", "出餐成品"),
    },
    # ---- 装修 / 施工 ----
    "装修进度": {
        # 装修/施工/进度 是本句的具体主题（不是泛场景），按具体概念计分
        "kind": "entity",
        # 不收「现场」：几乎每段装修素材都带这两个字，收进来等于给所有素材加分
        "terms": ("装修", "施工", "进度", "工地", "板材", "瓷砖", "管道", "脚手架", "立柱",
                  "墙面", "工艺", "建材", "搬木板", "搬运", "框架", "盯工"),
        "related": ("人物现场解说", "门店茶楼"),
    },
    "人物现场解说": {
        "kind": "action",
        "terms": ("现场解说", "讲解", "指引", "看图纸", "图纸", "色卡", "选材", "设计细节", "沟通"),
        "related": ("装修进度", "团队工人"),
    },
    "团队工人": {
        "kind": "action",
        "terms": ("工人", "团队", "施工人员", "工匠", "师傅", "管家"),
        "related": ("装修进度",),
    },
    # ---- 航拍 / 宏观 ----
    "航拍": {
        "kind": "scene",
        "terms": ("航拍", "无人机", "dji", "俯瞰", "高空"),
        "related": ("门店茶楼", "装修进度"),
    },
    # ---- 人物 / 情绪 ----
    "人物出镜": {
        "kind": "person",
        "terms": ("人物出镜", "出镜", "口播", "博主", "男子", "真人", "对镜头", "微笑"),
        "related": (),
    },
    "氛围": {
        "kind": "scene",
        "terms": ("氛围", "舒服", "干净", "明亮", "烟火气", "宽敞", "高大"),
        "related": ("环境",),
    },
    "环境": {
        "kind": "scene",
        "terms": ("环境", "门店环境", "实景"),
        "related": ("氛围", "门店茶楼"),
    },
    # ---- 数字 / 钱（开场自报身家的对位） ----
    "价格数字": {
        "kind": "intent",
        "terms": ("成本", "价格", "多少钱", "预算", "收费", "单价", "三百万", "百万", "块钱"),
        "related": (),
    },
}

# 语气词 / 粤语虚词 / 口语套话：先剥掉，再抽概念（否则「嘅」「咗」会把词切断）
_FILLERS: Tuple[str, ...] = (
    "我哋", "你哋", "佢哋", "大家", "人哋", "呢个", "𠮶个", "嗰个", "咁样", "呢度", "𠮶度", "嗰度",
    "非常之", "我觉得", "即系", "就系", "都系", "仲有", "同埋", "跟住", "其实", "所以", "因为",
    "有啲", "好多", "一个", "依家", "而家", "到时", "不如", "然后", "不过", "可以", "绝对",
    "嘅", "咗", "喺", "咁", "咩", "嗰", "𠮶", "啲", "哋", "噃", "囉", "㗎", "啱", "佢", "係",
    "的", "了", "着", "过", "呢", "吧", "啊", "呀", "啦", "嘛", "就", "都", "还", "很", "也",
)
_FILLER_RE = re.compile("|".join(sorted((re.escape(w) for w in _FILLERS), key=len, reverse=True)))
_PUNCT_RE = re.compile(r"[\s，。、；：！？…,.;:!?\"'“”‘’()（）《》【】\-—~·]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-z]{2,}")

# 景别/运镜/时间等「每条素材都有」的词：算重叠前去掉，避免假重叠
_STOP_TOKENS = frozenset({
    "近景", "中景", "远景", "全景", "特写", "仰拍", "俯拍", "半身", "镜头", "画面", "人物", "出镜",
    "移动", "静止", "推拉", "摇镜", "跟随", "手持", "延时", "升格", "片段", "日常", "花絮",
})

_PRICE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万|千|百|元|块|折|%|倍|平|平方米)")

# jieba 可选：有就用（词更准），没有就零依赖兜底（字符 bigram），行为都可复现
try:  # pragma: no cover - 环境相关
    import jieba as _jieba

    try:
        _jieba.setLogLevel(60)  # 关掉「Building prefix dict」噪声，别污染流水线日志
    except Exception:
        pass
except Exception:  # pragma: no cover - 没有 jieba 也必须能跑
    _jieba = None


# ---------------------------------------------------------------------------
# 文本处理
# ---------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    """剥语气词/粤语虚词/标点，只留能承载概念的字面（「我哋嘅卡座」→「卡座」）。"""
    s = str(text or "").lower()
    s = _FILLER_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    return " ".join(s.split())


def _tokens(text: str) -> List[str]:
    """分词：有 jieba 走 jieba；没有就用 CJK 串 + 截取 2 字词，保证零依赖也确定性。"""
    s = normalize_text(text)
    if not s:
        return []
    out: List[str] = []
    if _jieba is not None:
        try:
            out = [str(t).strip() for t in _jieba.lcut(s, HMM=False)]
        except Exception:
            out = []
    if not out:
        for chunk in _CJK_RE.findall(s):
            if len(chunk) <= 2:
                out.append(chunk)
            else:
                out.extend(chunk[i:i + 2] for i in range(0, len(chunk) - 1))
        out.extend(_LATIN_RE.findall(s))
    return [t for t in out if t and not _PUNCT_RE.fullmatch(t)]


def _lookup_table() -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for concept, meta in _CONCEPTS.items():
        for term in meta["terms"]:
            rows.append((term, concept))
        for term in meta.get("weak_terms") or ():
            rows.append((term, concept))
    return sorted(rows, key=lambda x: len(x[0]), reverse=True)


_LOOKUP: List[Tuple[str, str]] = _lookup_table()


def concepts_of(text: str) -> Dict[str, List[str]]:
    """把一段文本概念化 → {概念: [命中的字面]}。最长匹配优先，重叠位置不重复计。"""
    s = normalize_text(text)
    if not s:
        return {}
    taken = [False] * len(s)
    out: Dict[str, List[str]] = {}
    for term, concept in _LOOKUP:
        start = s.find(term)
        while start >= 0:
            end = start + len(term)
            if not any(taken[start:end]):
                for i in range(start, end):
                    taken[i] = True
                bucket = out.setdefault(concept, [])
                if term not in bucket:
                    bucket.append(term)
            start = s.find(term, start + 1)
    if _PRICE_RE.search(s):
        out.setdefault("价格数字", []).append(_PRICE_RE.search(s).group(0))
    return out


def concepts_detail(text: str) -> Dict[str, Tuple[str, float]]:
    """概念化并标明字面强度：1.0=具体词（茶楼/门头/虾饺），0.55=泛称（门店/餐厅）。"""
    s = normalize_text(text)
    if not s:
        return {}
    taken = [False] * len(s)
    out: Dict[str, Tuple[str, float]] = {}
    for term, concept in _LOOKUP:
        start = s.find(term)
        while start >= 0:
            end = start + len(term)
            if not any(taken[start:end]):
                for i in range(start, end):
                    taken[i] = True
                weak_terms = _CONCEPTS.get(concept, {}).get("weak_terms") or ()
                strength = SOFT_TERM_FACTOR if term in weak_terms else 1.0
                prev = out.get(concept)
                if prev is None or strength > prev[1]:
                    out[concept] = (term, strength)
            start = s.find(term, start + 1)
    if _PRICE_RE.search(s):
        out.setdefault("价格数字", (_PRICE_RE.search(s).group(0), 1.0))
    return out


def _related_of(concept: str) -> Tuple[str, ...]:
    return tuple(_CONCEPTS.get(concept, {}).get("related") or ())


def _kind_factor(concept: str) -> float:
    kind = str(_CONCEPTS.get(concept, {}).get("kind") or "entity")
    return KIND_FACTOR.get(kind, 1.0)


def describe_clip(item: Dict[str, Any]) -> str:
    """素材侧可匹配文本 = 画面标签 + 描述/备注（这是「画面里到底是什么」的唯一一手证据）。"""
    parts: List[str] = []
    for key in ("description", "vlm_description", "note", "user_note", "caption", "summary"):
        v = str(item.get(key) or "").strip()
        if v:
            parts.append(v)
    for key in ("scene_tags_zh", "semantic_tags", "manual_tags", "tags"):
        for t in item.get(key) or []:
            t_s = str(t).strip()
            if t_s:
                parts.append(t_s)
    for key in ("parent_dir", "folder", "rel_path"):
        v = str(item.get(key) or "").strip()
        if v:
            parts.append(v)
    for src in (item.get("filename"), item.get("source_path"), item.get("normalized_mp4"), item.get("media_path")):
        if src:
            for tok in _CJK_RE.findall(Path(str(src)).stem):
                if len(tok) >= 2:
                    parts.append(tok)
    return " ".join(parts)


def _label_source(item: Dict[str, Any]) -> str:
    src = str(item.get("scene_labels_source") or item.get("label_source") or "").strip()
    if src:
        return src
    # 没写来源但带了画面标签：按非 agent 处理（保守，不给额外加权）
    return "unknown"


def _overlap(sentence_text: str, clip_text: str) -> float:
    """台词的字词有多大比例出现在素材描述里（0~1，景别词不算）。"""
    s_tokens = {t for t in _tokens(sentence_text) if len(t) >= 2 and t not in _STOP_TOKENS}
    if not s_tokens:
        return 0.0
    c_tokens = {t for t in _tokens(clip_text) if len(t) >= 2 and t not in _STOP_TOKENS}
    if not c_tokens:
        return 0.0
    return round(len(s_tokens & c_tokens) / len(s_tokens), 3)


# ---------------------------------------------------------------------------
# 单条打分
# ---------------------------------------------------------------------------
def score_clip(
    sentence_text: str,
    clip: Dict[str, Any],
    used_count: int = 0,
    need_sec: Optional[float] = None,
    keywords: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """台词 ↔ 单条素材：返回 score(0~1)/tier/hits/why，外加 rank（含质量/复用/时长裁决）。"""
    s_det = concepts_detail(sentence_text)
    for kw in keywords or []:
        for concept, (term, strength) in concepts_detail(str(kw)).items():
            prev = s_det.get(concept)
            if prev is None or strength > prev[1]:
                s_det[concept] = (term, strength)

    clip_text = describe_clip(clip)
    c_det = concepts_detail(clip_text)

    hits: List[str] = []
    why_parts: List[str] = []

    if not c_det:
        score = 0.0
        tier = TIER_NONE
        why = "素材侧没有可用画面描述（scene_tags_zh/description/备注都是空的），不能声称对得上"
    elif not s_det:
        score = 0.0
        tier = TIER_NONE
        why = "台词里没有可匹配的画面概念（这句是纯语气/纯观点），没有公共概念"
    else:
        # 强命中：两边都用具体词说同一个概念（茶楼↔茶楼、虾饺↔饺子）→ 这就是「画面在说这件事」
        # 软命中：概念对上了但有一边只有泛称（台词「茶楼」↔ 标签「门店」）→ 只算同场景
        strong: List[Tuple[str, str, str, float]] = []  # (concept, 台词字面, 素材字面, kind系数)
        soft: List[Tuple[str, str, str, float]] = []
        for concept in sorted(s_det):
            if concept not in c_det:
                continue
            s_term, s_str = s_det[concept]
            c_term, c_str = c_det[concept]
            row = (concept, s_term, c_term, _kind_factor(concept))
            if min(s_str, c_str) >= 1.0:
                strong.append(row)
            else:
                soft.append(row)
        strong.sort(key=lambda x: (-x[3], x[0]))
        soft.sort(key=lambda x: (-x[3], x[0]))
        covered = {row[0] for row in strong} | {row[0] for row in soft}

        # 沾边：台词概念 A 与素材概念 B 是相邻概念（同域/近义）；已命中的概念不再重复算
        weak: List[Tuple[str, str, str]] = []
        for concept in sorted(s_det):
            if concept in covered:
                continue
            for rel in _related_of(concept):
                if rel in c_det and rel not in s_det:
                    weak.append((concept, s_det[concept][0], c_det[rel][0]))
                    break

        strong_part = 0.0
        for i, (concept, s_w, c_w, kf) in enumerate(strong):
            base = STRONG_WEIGHTS[i] if i < len(STRONG_WEIGHTS) else STRONG_WEIGHTS[-1]
            strong_part += base * kf
            hits.append(f"{concept}「{s_w}」↔「{c_w}」")
        soft_part = 0.0
        for i, (concept, s_w, c_w, kf) in enumerate(soft):
            base = SOFT_WEIGHTS[i] if i < len(SOFT_WEIGHTS) else SOFT_WEIGHTS[-1]
            soft_part += base * kf
            hits.append(f"{concept}「{s_w}」≈「{c_w}」(泛称)")
        soft_part = min(SOFT_CAP, soft_part)
        weak_part = min(WEAK_CAP, WEAK_EACH * len(weak))
        for concept, s_w, c_w in weak:
            hits.append(f"{concept}~「{s_w}」≈「{c_w}」(沾边)")
        ov = _overlap(sentence_text, clip_text)
        overlap_part = OVERLAP_WEIGHT * ov if ov >= OVERLAP_MIN else 0.0

        score = min(1.0, strong_part + soft_part + weak_part + overlap_part)
        if score > 0.0 and _label_source(clip) == "agent":
            # 标签是抽帧判读出来的（不是文件名猜的），同命中更可信一点
            score = min(1.0, score + AGENT_SCORE_BONUS)
        score = round(score, 3)
        tier = TIER_HIT if score >= HIT_MIN else (TIER_WEAK if score >= WEAK_MIN else TIER_NONE)

        if score <= 0.0:
            tier = TIER_NONE
            why = "素材描述与这句台词没有公共概念"
        else:
            if strong:
                why_parts.append("概念命中：" + "、".join(f"{c}「{s_w}」↔「{c_w}」" for c, s_w, c_w, _kf in strong[:3]))
            if soft:
                why_parts.append("同场景泛称：" + "、".join(f"{c}「{s_w}」≈「{c_w}」" for c, s_w, c_w, _kf in soft[:3]))
            if weak:
                why_parts.append("沾边：" + "、".join(f"{c}~{c_w}" for c, _s, c_w in weak[:3]))
            if overlap_part > 0:
                why_parts.append(f"描述与台词的词重叠 {ov:.0%}")
            if _label_source(clip) == "agent":
                why_parts.append("画面标签来自抽帧判读")
            why = "；".join(why_parts)

    # ── 排序分：语义分是主序（×100），质量/时长只在同分时裁决 ──
    stab = float(clip.get("stability_score") or 0.8)
    sharp = float(clip.get("sharpness_score") or 40.0)
    rank = score * 100.0
    rank += min(QUALITY_SHARP_CAP, max(0.0, sharp) / QUALITY_SHARP_DIV)
    rank += QUALITY_STAB * max(0.0, min(1.0, stab))
    if _label_source(clip) == "agent":
        rank += AGENT_LABEL_RANK_BONUS
    rank -= REUSE_PEN * max(0, int(used_count))
    dur = float(clip.get("duration_sec") or 0.0)
    if need_sec and dur > 0 and dur < float(need_sec):
        rank -= SHORT_PEN
    if dur and dur < TINY_DUR_SEC:
        rank -= SHORT_PEN

    return {
        "score": round(float(score), 3),
        "tier": tier,
        "hits": hits[:6],
        "why": why,
        "rank": round(rank, 2),
        "label_source": _label_source(clip),
        "concepts": {k: v[0] for k, v in c_det.items()},
    }


def rank_pool(
    sentence_text: str,
    pool: Sequence[Dict[str, Any]],
    used_counts: Optional[Dict[str, int]] = None,
    need_sec: Optional[float] = None,
    keywords: Optional[Sequence[str]] = None,
    exclude_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """整池按台词排序：语义分高的在前，同分由质量/时长/复用次数裁决。"""
    used = used_counts or {}
    skip = {str(x) for x in (exclude_ids or ())}
    rows: List[Dict[str, Any]] = []
    for m in pool:
        if str(m.get("id") or "") in skip:
            continue
        m_score = score_clip(sentence_text, m, used_count=used.get(str(m.get("id")), 0),
                             need_sec=need_sec, keywords=keywords)
        rows.append({"clip": m, **m_score})
    rows.sort(key=lambda r: (-r["rank"], str(r["clip"].get("id") or "")))
    return rows


# ---------------------------------------------------------------------------
# Agent 覆盖：流水线里已经有人逐句指定素材时，优先采信人/Agent 的判断
# ---------------------------------------------------------------------------
def load_agent_choices(pipeline_dir: Optional[Path]) -> Dict[str, Any]:
    """读 `04b_broll_choice.json`：{"clips":[{"timeline_in_sec":10.83,"media_path":"..."}]}。"""
    if not pipeline_dir:
        return {"items": [], "by_time": {}}
    p = Path(pipeline_dir) / AGENT_CHOICE_FILE
    if not p.is_file():
        return {"items": [], "by_time": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"items": [], "by_time": {}}
    items = [x for x in (data.get("clips") or []) if isinstance(x, dict)]
    by_time: Dict[float, Dict[str, Any]] = {}
    for it in items:
        try:
            t = round(float(it.get("timeline_in_sec")), 3)
        except Exception:
            continue
        if str(it.get("media_path") or "").strip():
            by_time[t] = it
    return {"items": items, "by_time": by_time}


def agent_choice_for(choices: Optional[Dict[str, Any]], timeline_in_sec: float,
                     tol: float = 0.12) -> Optional[Dict[str, Any]]:
    """按时间线找 Agent 指定项（切点可能有几帧误差，给 tol 容差）。"""
    by_time = (choices or {}).get("by_time") or {}
    if not by_time:
        return None
    best: Optional[Dict[str, Any]] = None
    best_d = tol
    for t, it in by_time.items():
        d = abs(float(t) - float(timeline_in_sec))
        if d <= best_d:
            best_d = d
            best = it
    return best


def _same_media(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        return Path(a).resolve() == Path(b).resolve()
    except Exception:
        return Path(a).name == Path(b).name


def same_media(a: str, b: str) -> bool:
    """两条路径是不是同一份素材（Agent 覆盖文件里可能写源路径、也可能写归一化路径）。"""
    return _same_media(a, b)


def match_broll(
    sentence_text: str,
    pool: Sequence[Dict[str, Any]],
    used_counts: Optional[Dict[str, int]] = None,
    need_sec: Optional[float] = None,
    keywords: Optional[Sequence[str]] = None,
    exclude_ids: Optional[Iterable[str]] = None,
    agent_choices: Optional[Dict[str, Any]] = None,
    timeline_in_sec: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """给一句台词挑最贴的一条：Agent 覆盖优先，否则按语义分排序取第一条。"""
    if not pool:
        return None
    ranked = rank_pool(sentence_text, pool, used_counts=used_counts, need_sec=need_sec,
                       keywords=keywords, exclude_ids=exclude_ids)
    rows = {str(r["clip"].get("id") or r["clip"].get("normalized_mp4")): r for r in ranked}

    if timeline_in_sec is not None and agent_choices:
        forced = agent_choice_for(agent_choices, timeline_in_sec)
        if forced:
            mp = str(forced.get("media_path") or "")
            for r in ranked:
                if _same_media(str(r["clip"].get("normalized_mp4") or r["clip"].get("media_path") or ""), mp):
                    return {**r, "source": "agent", "agent_why": str(forced.get("why") or "")}

    if not ranked:
        return None
    best = dict(ranked[0])
    best["source"] = "rules"
    return best


# ---------------------------------------------------------------------------
# 汇总 + 门禁
# ---------------------------------------------------------------------------
def summarize_choices(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """把每条 B-roll 的对位结果汇总成 EDL 里的 `broll_semantic`（含逐条依据）。"""
    rows = [r for r in rows]
    out_rows: List[Dict[str, Any]] = []
    by_role: Dict[str, Dict[str, int]] = {}
    for r in rows:
        tier = str(r.get("tier") or TIER_NONE)
        role = str(r.get("role") or "")
        b = by_role.setdefault(role, {"clips": 0, TIER_HIT: 0, TIER_WEAK: 0, TIER_NONE: 0})
        b["clips"] += 1
        b[tier] = b.get(tier, 0) + 1
        out_rows.append({
            "clip_id": r.get("clip_id"),
            "role": role,
            "timeline_in_sec": r.get("timeline_in_sec"),
            "sentence": str(r.get("sentence") or "")[:80],
            "media": str(r.get("media") or ""),
            "score": float(r.get("score") or 0.0),
            "tier": tier,
            "hits": list(r.get("hits") or []),
            "why": str(r.get("why") or ""),
            "source": str(r.get("source") or "rules"),
        })
    n = len(out_rows)
    if not n:
        return {"clips": 0, "hit": 0, "weak": 0, "none": 0, "hit_ratio": 0.0,
                "aligned_ratio": 0.0, "none_ratio": 0.0, "by_role": {}, "rows": [],
                "agent_sourced": 0}
    hit = sum(1 for r in out_rows if r["tier"] == TIER_HIT)
    weak = sum(1 for r in out_rows if r["tier"] == TIER_WEAK)
    none = n - hit - weak
    return {
        "clips": n,
        "hit": hit,
        "weak": weak,
        "none": none,
        "hit_ratio": round(hit / n, 4),
        "aligned_ratio": round((hit + weak) / n, 4),
        "none_ratio": round(none / n, 4),
        "by_role": by_role,
        "agent_sourced": sum(1 for r in out_rows if r["source"] == "agent"),
        "rows": out_rows,
    }


def check_semantic_gate(
    summary: Dict[str, Any],
    allow: bool = False,
    max_none_ratio: float = MAX_NONE_RATIO,
    min_hit_ratio: float = MIN_HIT_RATIO,
) -> Dict[str, Any]:
    """对位门禁（fail-closed）：命中率不够 / 无依据镜头超阈值 都必须 FAIL。

    allow=True 是显式放行（`--allow-broll-mismatch`）：仍然如实记录不达标项，
    只是不阻断出片——不能说「没达标」变成「达标」。
    """
    clips = int(summary.get("clips") or 0)
    hit_ratio = float(summary.get("hit_ratio") or 0.0)
    none_ratio = float(summary.get("none_ratio") or 0.0)
    fails: List[str] = []
    if clips <= 0:
        return {"pass": True, "raw_pass": True, "waived": False, "fails": [],
                "thresholds": {"min_hit_ratio": min_hit_ratio, "max_none_ratio": max_none_ratio}}
    if hit_ratio < min_hit_ratio:
        fails.append(
            f"语义命中率 {hit_ratio:.0%} 低于下限 {min_hit_ratio:.0%}"
            f"（{clips} 条 B-roll 里只有 {summary.get('hit', 0)} 条有画面依据）"
        )
    if none_ratio > max_none_ratio:
        fails.append(
            f"无依据镜头 {summary.get('none', 0)}/{clips} = {none_ratio:.0%}，超过上限 {max_none_ratio:.0%}"
        )
    return {
        "pass": (not fails) or bool(allow),
        "raw_pass": not fails,
        "waived": bool(fails) and bool(allow),
        "fails": fails,
        "thresholds": {"min_hit_ratio": min_hit_ratio, "max_none_ratio": max_none_ratio},
    }
