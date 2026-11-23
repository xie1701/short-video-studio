#!/usr/bin/env python3
"""
片尾收尾诊断 + 转化指令（CTA）生成。

用户实测
────────
「已有素材剪辑的成片最后收尾都很生硬，主题也没有很好的收尾，感觉话还没说完就结束了，
看看应该有个怎样的 CTA 来收尾。而且这种类型的都很难按《建立可循环的内容系统》的框架来创作。」

为什么会这样
────────────
拿现成素材剪的片子，台词是现场随口说的，末句往往停在半截事实上（"这个价格就这样"），
既没有把话题收住，也没有让人知道下一步做什么。框架里那套「水桶流」公式是
**开篇钩子 + 细节展示 + 人格化表达 + 价值说明 + 转化指令**，最后那一格在现成素材里经常空缺——
但没人会告诉你缺了，成片就那样发出去了。

这里做三件事，都不改用户的台词
──────────────────────────────
1. **判断收没收住**：看最后 3 句（去掉语气词）——有没有转化指令 / 是不是互动提问 / 是不是
   金句或人设式收尾。给 `closed` / `soft` / `none` 三档，并写清"哪一句让你觉得没说完"。
2. **按框架给三条候选 CTA**：互动型、到店型、人设型（框架里的四种结尾：金句 / 感悟 / 互动 / 人设），
   用账号资料里的店名、城市、主打服务把话填实，不给"点赞关注"这种空话。
3. **补一张尾卡**：真的没结尾时，在最后一句之上挂一张 1.4–2.2 秒的 CTA 尾卡（文字 + 行动），
   由 `step6` 落进包装层；同时写进发布清单，下次拍的时候照着补一句。

**不改台词、不配音、不硬塞一句假话进原声**：尾卡是画面上的一行字，用户觉得不合适可以删掉，
`--waive`/删元素都会留痕。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# 转化指令的实际说法（用户自己在店里会怎么说）
CTA_MARKERS = (
    "评论区", "留言", "私信", "主页", "关注", "点赞", "收藏", "转发", "分享",
    "到店", "来店", "店里", "地址", "电话", "团购", "领券", "优惠", "活动",
    "找我", "咨询", "问我", "报我", "扫码", "下单", "预订", "预约",
)
# 收尾类型（框架：金句 / 感悟 / 互动 / 人设）
ENDING_TYPES = {
    "互动": ("吗", "呢", "?", "？", "你们", "大家", "有没有", "是不是", "怎么看"),
    "感悟": ("说到底", "其实", "我觉得", "这些年", "说实话", "回头看", "总的"),
    "人设": ("我就是", "我这个人", "我这人", "死脑筋", "底线", "宁可", "也不", "绝不"),
    "金句": ("记住", "一句话", "永远是", "从来", "不是", "而是"),
}

# 句首剥的语气词。「这个/那个」不在里面：口播里它们是实词（「这个价格」不是废话）。
FILLER = ("嗯", "啊", "呃", "然后", "就是", "对吧", "好吧", "行吧")


def _clean(text: str) -> str:
    """只剥**开头**的语气词和结尾标点。

    早先这里是全局 replace，结果「我这个人做事实在」被削成「我个人做事实在」，
    「那个价格」被削成「价格」——语气词只在句首才是语气词，句中就是实词。
    """
    s = str(text or "").strip()
    changed = True
    while changed:
        changed = False
        for f in FILLER:
            if s.startswith(f) and len(s) > len(f):
                s = s[len(f):].lstrip("，、 　")
                changed = True
    return s.strip("，。！？,.!? 　")


def classify_ending(sentence: str) -> Dict[str, Any]:
    """单句属于哪种收尾（可多标签；都没有就是 none）。"""
    s = str(sentence or "")
    tags: List[str] = []
    if any(m in s for m in CTA_MARKERS):
        tags.append("转化指令")
    for kind, marks in ENDING_TYPES.items():
        if any(m in s for m in marks):
            tags.append(kind)
    return {"text": s, "tags": tags, "has_cta": "转化指令" in tags}


def analyze_ending(
    segments: Sequence[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
    topic_hint: str = "",
    tail_n: int = 3,
) -> Dict[str, Any]:
    """看最后几句到底收没收住；没就给出可用的 CTA 候选。

    segments: 02_transcript.json 的 segments（读 text / is_filler）
    profile:  账号资料（store_name / city / main_service / address 等，缺就少说一点，不编）
    """
    live = [s for s in segments if not s.get("is_filler") and str(s.get("text") or "").strip()]
    tail = live[-tail_n:] if tail_n > 0 else []
    marks = [classify_ending(str(s.get("text") or "")) for s in tail]
    has_cta = any(m["has_cta"] for m in marks)
    last_text = _clean(str(tail[-1].get("text") or "")) if tail else ""
    # 收尾类型只看**末句**：中段出现「从来不用冻肉」不代表片子是金句收尾
    kinds = [t for t in (marks[-1]["tags"] if marks else []) if t != "转化指令"]
    last_len = len(last_text)

    if has_cta:
        verdict = "closed"
        reason = "末句已经是转化指令"
    elif kinds:
        verdict = "soft"
        reason = f"末句「{last_text}」是「{'/'.join(dict.fromkeys(kinds))}」式收尾，但没有告诉观众下一步做什么"
    elif tail and last_len <= 6:
        verdict = "soft"
        reason = f"末句只有 {last_len} 个字（「{last_text}」），像话没说完"
    else:
        verdict = "none"
        reason = f"末句停在事实陈述（「{last_text[:20]}」），话题没往回收，也没有行动指引"

    prof = profile or {}
    store = str(prof.get("store_name") or prof.get("brand") or prof.get("name") or "").strip()
    city = str(prof.get("city") or prof.get("region") or "").strip()
    service = str(prof.get("main_service") or prof.get("industry") or prof.get("category") or "").strip()
    address = str(prof.get("address") or prof.get("location") or "").strip()
    topic = _clean(topic_hint) or (_clean(str(tail[-2].get("text") or "")) if len(tail) >= 2 else "")

    def _who() -> str:
        return f"在{city}想{service}的" if (city and service) else (
            f"想{service}的" if service else ("在" + city if city else "有需要的"))

    def _where() -> str:
        if address:
            return f"{address}，报我名字就行"
        return "地址写在主页，来之前先私信我一声"

    candidates: List[Dict[str, str]] = []
    if service or city or store:
        candidates.append({
            "type": "互动",
            "text": f"关于{service or topic or '这件事'}你遇到过什么情况？评论区说一句，我一条条回。",
        })
        candidates.append({
            "type": "到店",
            "text": f"{_who()}，直接来店里看看——{_where()}。",
        })
    candidates.append({
        "type": "人设",
        "text": "我这人做事实在，不合适的我会当场告诉你不合适，省得你白花钱。",
    })
    candidates.append({
        "type": "金句",
        "text": f"{topic or service or '做这行'}这件事，说到底就是别糊弄人。",
    })
    if store:
        for c in candidates:
            if store not in c["text"] and c["type"] == "到店":
                c["text"] = c["text"].replace("来店里看看", f"来{store}看看")

    return {
        "verdict": verdict,
        "reason": reason,
        "has_cta": has_cta,
        "ending_types": list(dict.fromkeys(kinds)),
        "tail_sentences": [m["text"] for m in marks],
        "candidates": candidates[:3],
        "needs_operator_line": verdict != "closed",
        "frame_ref": "《建立可循环的内容系统》结尾四类：金句 / 感悟 / 互动 / 人设；水桶流公式末位=转化指令",
        "note": "只补画面上的尾卡，不改原声、不编造事实；候选句按账号资料填实，资料缺就不硬编。",
    }


def best_card_text(plan: Dict[str, Any]) -> str:
    """给尾卡挑一句话：优先「到店」（门店最能转化），其次互动。"""
    cands = plan.get("candidates") or []
    for want in ("到店", "互动", "人设", "金句"):
        for c in cands:
            if c.get("type") == want and str(c.get("text") or "").strip():
                return str(c["text"]).strip()
    return "有问题评论区问，我一条条回" if not cands else str(cands[0].get("text") or "").strip()


def fit_card_text(text: str, max_chars: int = 24) -> str:
    """尾卡一行字放不下就按标点断开，别在半句话中间剁（"…省" 这种最难看）。"""
    s = str(text or "").strip()
    if len(s) <= max_chars:
        return s
    head = s[:max_chars]
    for sep in ("；", "，", "——", "：", "、"):
        cut = head.rfind(sep)
        if cut >= max_chars // 2:
            return head[:cut]
    return head.rstrip("，。？！") + "…"


def split_card_text(text: str, max_chars: int = 18) -> tuple:
    """尾卡拆成「主句 + 副行」：主句要短到一眼看完，地址/条件这类落在副行。

    「在珠海想烧烤的，直接来老杨家烧烤看看——香洲区凤凰路 12 号，报我名字就行」
    剁成「在珠海想烧烤的，直接来老杨家烧烤」+「香洲区凤凰路 12 号，报我名字」，两行都读得完。
    """
    s = str(text or "").strip()
    for dash in ("——", "—", "－"):
        if dash in s:
            head, tail = s.split(dash, 1)
            if head.strip() and tail.strip():
                return (fit_card_text(head, max_chars + 6), tail.strip().rstrip("。"))
    if len(s) <= max_chars:
        return (s, "")
    cut = s.rfind("，", 0, max_chars)
    if cut >= max_chars // 2:
        return (s[:cut], s[cut + 1:].strip().rstrip("。"))
    return (fit_card_text(s, max_chars), "")


def card_frames(plan: Dict[str, Any], total_frames: int, fps: int) -> tuple:
    """尾卡出现的时间窗：最后一个镜头的后 60%，最多 2.2 秒，最少 1.4 秒（读得完一句话）。"""
    if total_frames <= 0:
        return (0, 0)
    longest = min(total_frames, int(round(1.4 * fps)))
    if plan.get("verdict") == "closed":
        return (total_frames - longest, total_frames)
    span = int(round(2.2 * fps))
    span = max(longest, min(span, total_frames))
    return (max(0, total_frames - span), total_frames)


# ── 五格结构体检：现成素材怎么对上《建立可循环的内容系统》的「水桶流」 ──────────
# 框架原文（第 262 行）：精准人群钩子/开篇钩子 + 细节展示 + 人格化表达 + 价值说明 + 转化指令
FRAMEWORK_MARKERS = {
    "开篇钩子": ("你知道", "很多人", "干了", "开了", "这家", "我们店", "今天", "先说", "你敢"),
    "细节展示": ("度", "小时", "斤", "遍", "毫米", "公分", "分钟", "工序", "成本", "斤两", "次", "道"),
    "人格化表达": ("我这个人", "我就是", "我觉得", "我们店", "我一直", "我不", "实话", "老实"),
    "价值说明": ("为什么", "因为", "所以", "值", "划算", "省", "贵", "便宜", "差别", "区别"),
    "转化指令": CTA_MARKERS,
}


def map_framework(segments: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """把现成台词对到五格上——不评价好坏，只告诉你哪一格有素材、哪一格是空的。

    现成素材剪的片子难套框架，难在这儿：不是台词不好，是五格里总有一两格没人说过。
    空着的那格要么补一句，要么就把这一格从结构里去掉（短片的合法做法），
    但得让人知道去掉的是哪一格，而不是整条片子糊过去。
    """
    live = [(_clean(str(s.get("text") or "")), i) for i, s in enumerate(segments)
            if not s.get("is_filler") and str(s.get("text") or "").strip()]
    filled: Dict[str, List[int]] = {k: [] for k in FRAMEWORK_MARKERS}
    for text, idx in live:
        for bucket, marks in FRAMEWORK_MARKERS.items():
            if any(m in text for m in marks):
                filled[bucket].append(idx)
    # 开篇钩子必须落在头两句，否则不算
    filled["开篇钩子"] = [i for i in filled["开篇钩子"] if i <= 1]
    covered = [k for k, v in filled.items() if v]
    missing = [k for k, v in filled.items() if not v]
    return {
        "covered": covered,
        "missing": missing,
        "sentence_index": filled,
        "sentence_count": len(live),
        "verdict": "完整" if not missing else ("缺 " + "、".join(missing)),
        "note": "空着的格子可以补一句，也可以有意识地去掉；但不能不知道自己缺了哪一格。",
    }
