#!/usr/bin/env python3
"""
[Step 5: 字幕生成与统一权威布局]
1. 读取锁定音轨 03_audio_events.json 的逐字时间戳，结合 terms.txt 保护词与词边界动态规划（DP）切分清爽单行短句。
2. 修复数字旁中文标点残留（仅保住夹在数字间的小数点 . 与时间冒号 : 以及数字后 %），尊重模板 punct_stripped 配置。
3. 基于唯一权威 SUBTITLE_SAFE_ZONE 与 01_manifest.json 裁切后精确人脸框一次性完成像素测宽与避脸位移，
   写出权威 final_bbox（同步写入 bbox），供 render / jianying / hyperframes / qa 四端同源消费。
4. 双通道防重复：code_broll 在场覆盖 >=60% 时标记 suppressed=True 并跳过上屏（仅普通无画中画图解）。
输出：pipeline/05_subtitles.json、05_subtitles.ass、05_subtitles.srt 与 05_subtitle_check.txt
"""

from __future__ import annotations

import argparse
import difflib
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common import (
    BT709_FFMPEG_ARGS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SUBTITLE_CENTER_X,
    SUBTITLE_DEFAULT_Y_CENTER,
    SUBTITLE_SAFE_ZONE,
    SUBTITLE_SYMMETRIC_MAX_W,
    bbox_iou,
    check_bbox_in_safe_zone,
    find_bin,
    find_commercial_font,
    find_source_font,
    get_cached_font,
    load_subtitle_templates,
    maybe_reexec_in_venv,
    parse_manifest_faces,
    read_json,
    write_json,
)
import element_rules
import glyph_fallback as glyph_gate

# 仅保留夹在数字间的小数点/冒号/千分位逗号(如 3.5 / 18:30 / 1,200)以及数字后的%，彻底清掉紧贴数字的中文逗号句号！
_NON_CORE_PUNCT_RE = re.compile(
    r"(?<![A-Za-z0-9])\.|(?<=[A-Za-z0-9])\.(?!\d)"
    r"|(?<!\d)[:：](?!\d)|(?<!\d)[:：](?=\d)|(?<=\d)[:：](?!\d)"
    r"|(?<!\d),(?!\d)|(?<!\d),(?=\d)|(?<=\d),(?!\d{3}(?!\d))"
    r"|[，。、；！？!?;“”‘’\"'（）()]+"
)

_DP_NO_LEAD_CHARS = set("的了呢吗吧啊呀哦哇啦嘛着过么嘅咗啲哋咧啰之得地")
_DP_NO_TAIL_CHARS = set("的嘅地得之了咗着过把被从跟比在对向给俾让和与及是将用为喺有系都真很太最更也就才还仲唔不没呢哪这那嗰𠮶某每各能要会想敢该种")
_DP_MID_CLAUSE_NO_TAIL_ASPECT = frozenset("了咗着过住")
_DP_UNIT_AFTER_NUM = set("倍度年多万亿%％岁个次米元块斤克天月时分秒件家店条步项位层种平张台间人只杯碗盘之遍回趟场套组批名款道首篇集期页行句字词点折成半单匹节颗粒片支根盏扇口头亩瓦伏安")
_CANTONESE_NE_DEMO_PREFIXES = (
    "呢度", "呢批", "呢个", "呢间", "呢啲", "呢种", "呢边", "呢排",
    "呢次", "呢条", "呢位", "呢家", "呢套", "呢份", "呢款", "呢张", "呢只",
    "呢一个", "呢一间", "呢一批", "呢一种", "呢一套", "呢一份", "呢一次", "呢一条",
)
_DP_HARD_SPLIT_RE = re.compile(r"([。！？!?]+)")
_DP_SOFT_PUNCT = set("，、；：,;:")
_CLAUSE_BREAK_PREFIXES = (
    "不是", "也不是", "唔系", "但是", "可是", "不过", "然而", "所以", "因此", "因为", "是因为",
    "其实", "如果", "只要", "一旦", "而且", "然后", "跟住", "到时", "晚上", "朝早", "早上",
    "最后", "首先", "其次", "再次", "总结", "第一", "第二", "第三", "第四",
    "我觉得", "唔知道", "可以喺", "就系", "就是", "是我", "包括", "还有", "仲有", "也不",
    "有啲", "不如", "与其", "另外", "或者", "甚至", "反而", "虽然", "尽管", "既然", "除非",
    "不仅", "不但", "接下来", "关键是", "问题是", "核心是",
)
# 通用公众平台专名（非特定片源词典，供未传 terms.txt 时识别常见中文平台复合名）
_UNIVERSAL_PUBLIC_PLATFORMS = (
    "百度文库", "微信公众号", "小红书", "抖音小店", "剪映",
)
# 通用粤语/口语功能词与指示词（仅收 jieba 默认词典缺失的粤语/口语词；严禁收任何标准普通话常用词以免覆盖其真实词性，
# 且严禁写入业务/探店故事专名或以「的/嘅」结尾的粘连词）
_UNIVERSAL_SPOKEN_LEXICON = frozenset({
    "我哋", "佢哋", "你哋", "就系", "唔系", "唔知", "唔知道", "唔好", "唔同", "唔够", "唔错", "唔会",
    "依家", "点解", "点样", "跟住", "朝早", "呢度", "𠮶度", "嗰度", "𠮶边", "嗰边", "𠮶种", "嗰种", "呢种",
    "呢边", "呢个", "𠮶个", "嗰个", "呢间", "𠮶间", "嗰间", "呢啲", "𠮶啲", "嗰啲", "呢一个",
    "呢批", "呢排", "呢次", "呢条", "呢位", "呢家", "呢套", "呢份", "呢款", "呢张", "呢只",
    "有啲", "好多", "乜嘢", "食乜嘢", "同埋", "喺𠮶度", "喺嗰度", "可以喺", "俾大家",
    "捉翻嚟", "到时", "原汁原味", "非常之", "好抵食", "评论区", "仲有",
    "点赞", "完播", "口播", "私信", "置顶", "短视频",
})
# 上表里实际充当系词/副词/连词/代词/介词短语、而非实体名词的口语词（防止 DP 误当专名 'nz' 触发定中假内聚）
_SPOKEN_NON_NOUN_WORDS = frozenset({
    "我哋", "佢哋", "你哋", "就系", "唔系", "唔知", "唔知道", "唔好", "唔同", "唔够", "唔错", "唔会",
    "依家", "点解", "点样", "跟住", "呢度", "𠮶度", "嗰度", "𠮶边", "嗰边", "𠮶种", "嗰种", "呢种",
    "呢边", "呢个", "𠮶个", "嗰个", "呢间", "𠮶间", "嗰间", "呢啲", "𠮶啲", "嗰啲", "呢一个",
    "呢批", "呢排", "呢次", "呢条", "呢位", "呢家", "呢套", "呢份", "呢款", "呢张", "呢只",
    "有啲", "好多", "乜嘢", "食乜嘢", "同埋", "喺𠮶度", "喺嗰度", "可以喺", "俾大家",
    "捉翻嚟", "到时", "非常之", "好抵食", "仲有",
})
_COMMON_SINGLE_PRED_VERBS = frozenset(
    "是系有没唔做去来吃喝买卖看睇写说讲用找拿放给俾让叫想要会能敢该"
)
_CLAUSE_START_PROTECT_RE = re.compile(
    r"^(?:第[一二三四五六七八九十两\d]+|首先|其次|再次|最后|总结|"
    r"不是|但是|可是|不过|然而|其实|所以|因此|因为|如果|只要|一旦|或者|另外|还有|真系好)"
)
_STOP_KEYWORDS = {
    "一个", "一些", "呢个", "𠮶个", "嗰个", "这种", "那个", "这个", "什么", "乜嘢", "所以",
    "因为", "觉得", "如果", "但是", "现在", "时候", "跟住", "大家", "不是", "是我", "两个",
    "一次", "下来", "出来", "还有", "包括", "就是", "就系", "可以", "非常", "绝对", "好多",
    "之后", "自己", "一间", "呢一个", "都会", "是的",
}


def _terms_file_for(pipeline_dir: Path) -> Optional[Path]:
    """术语表可能在三处：pipeline/terms.txt、素材侧 `_SVS工作区/术语表.txt`、项目根。"""
    cands = [
        Path(pipeline_dir) / "terms.txt",
        Path(pipeline_dir).parent / "terms.txt",
        Path(pipeline_dir).parent / "_SVS工作区" / "术语表.txt",
        Path(pipeline_dir).parent / "术语表.txt",
    ]
    inv_p = Path(pipeline_dir) / "00_inventory.json"
    if inv_p.is_file():
        try:
            inv = read_json(inv_p)
            for key in ("raw_dir", "source_dir", "input_dir"):
                v = str(inv.get(key) or "").strip()
                if v:
                    cands.append(Path(v) / "_SVS工作区" / "术语表.txt")
                    cands.append(Path(v) / "术语表.txt")
        except Exception:
            pass
    for c in cands:
        if c.is_file():
            return c
    return None


def load_protected_terms(terms_path: Optional[Path] = None, extra_terms: Optional[List[str]] = None) -> List[str]:
    terms: List[str] = []
    if terms_path and Path(terms_path).is_file():
        for ln in Path(terms_path).read_text(encoding="utf-8").splitlines():
            t = ln.strip()
            if not t or t.startswith("#"):
                continue
            # 支持「错词 => 正词」硬映射格式：提取右侧正词进保护词典（不再把整行带符号放进去）
            if "=>" in t:
                t = t.split("=>", 1)[1].strip()
            if len(t) >= 2 and t not in _STOP_KEYWORDS and t not in terms:
                terms.append(t)
    for t in (extra_terms or []):
        ts = str(t or "").strip()
        if "=>" in ts:
            ts = ts.split("=>", 1)[1].strip()
        if len(ts) >= 2 and ts not in _STOP_KEYWORDS and ts not in terms:
            terms.append(ts)
    terms.sort(key=len, reverse=True)
    return terms


_NUM_ONLY_RE = re.compile(r"^(?:[0-9]+(?:,[0-9]{3})*(?:[.:][0-9]+)?|[零〇一二三四五六七八九十百千万亿两廿]+)$")
_MULTI_UNIT_PAT = r"平方米|立方米|平方|平米|立方|毫秒|微秒|纳秒|厘米|毫米|分米|微米|纳米|公里|公分|公斤|千克|毫升|毫安时|毫安|毫克|微克|千瓦时|千瓦|兆瓦|千伏|英寸|英尺|公顷|海里|克拉|小时|分钟|个月|点半|点一刻|点二刻|点三刻"
_ASCII_UNIT_PAT = (
    r"(?i:"
    r"GHz|MHz|kHz|Hz|Gbps|Mbps|Kbps|bps|GB|MB|KB|TB|PB|"
    r"kWh|Wh|kW|mW|mA|kV|mV|MP|ms|us|nm|um|px|fps|dpi|ppi|bits|bit|"
    r"mAh|dB|mm|cm|km|kg|ml|mg|nits|nit|rpm|[GKMTWVALPsShHdDbBxX]"
    r")(?![A-Za-z0-9])"
)
_UNIT_ONLY_RE = re.compile(
    r"^(?:[%％倍度年万亿岁个次米元块斤克天月时分秒件家店条步项位层种平张台间人只杯碗盘遍回趟场套组批名款道首篇集期页行句字词点折成半单周吨升箱瓶盒袋桶辆座栋户号楼排匹节颗粒片支根盏扇口头亩瓦伏安]|"
    rf"{_MULTI_UNIT_PAT}|{_ASCII_UNIT_PAT}|一刻|二刻|三刻)$"
)
_SINGLE_UNIT_CHARS_NO_PCT = r"倍度年万亿岁个次米元块斤克天月时分秒件家店条步项位层种平张台间人只杯碗盘遍回趟场套组批名款道首篇集期页行句字词点折成单周吨升箱瓶盒袋桶辆座栋户号楼排匹节颗粒片支根盏扇口头亩瓦伏安"
_SINGLE_UNIT_CHARS = r"%％" + _SINGLE_UNIT_CHARS_NO_PCT
_UNIT_TAIL_PAT = rf"(?:{_MULTI_UNIT_PAT}|[万亿千百]+(?:{_MULTI_UNIT_PAT}|[{_SINGLE_UNIT_CHARS_NO_PCT}]半?)?|[{_SINGLE_UNIT_CHARS_NO_PCT}]半?)"
_NUM_UNIT_PRESPLIT_RE = re.compile(
    r"(?:[零〇一二两三四五六七八九十百千万亿廿]+分之(?:[十百千万亿]?[一二三四五六七八九十零〇]+|两(?![百千万亿]))(?:点[零〇一二两三四五六七八九十]+)?|"
    rf"(?<![A-Za-z0-9.])[0-9]+(?:,[0-9]{{3}})*(?:[.:][0-9]+)?(?:[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9])|[多余来]?(?:[%％]|{_UNIT_TAIL_PAT}))?(?![A-Za-z0-9])|"
    rf"[零〇一二两三四五六七八九十百千万亿廿]{{2,}}(?:点(?:一刻|二刻|三刻|半)|(?:[点余多来])?[零〇一二两三四五六七八九十百千万亿]*(?:[%％]|{_MULTI_UNIT_PAT}|[{_SINGLE_UNIT_CHARS_NO_PCT}]半?)?)?|"
    rf"(?!零件)[零〇一二两三四五六七八九十百千万亿廿](?:[%％]|{_MULTI_UNIT_PAT}|[{_SINGLE_UNIT_CHARS_NO_PCT}]半?))"
)
_AMBIGUOUS_SINGLE_CJK_UNITS = frozenset(
    "匹节颗粒片支根盏扇口头亩瓦伏安"
    "只成分点位家条名款道首篇集期页行句字词折单排间层种平张台件步项场套组批遍回趟杯碗盘瓶盒袋桶辆座栋户号楼吨升箱次人时"
)
_MULTI_UNIT_END_RE = re.compile(rf"(?:{_MULTI_UNIT_PAT})$")
_ONE_CHAR_CH_NUMERALS = frozenset("零〇一二两三四五六七八九十百千万亿廿")
_ALNUM_MIXED_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]+(?:\.?[0-9]+)+[A-Za-z0-9]*(?![A-Za-z0-9])"
)
_MULTI_WORD_ASCII_BRAND_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[ \t]+[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*){1,2}(?![A-Za-z0-9])"
)
_HYPHEN_ASCII_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+(?![A-Za-z0-9])"
)
_NON_ABAB_STUTTER_WORDS = frozenset({
    "觉得", "应该", "可以", "可能", "需要", "开始", "出来", "起来",
    "大家", "我们", "你们", "他们", "自己",
})

_CLEAN_JIEBA_TOK = None
_CLEAN_PSEG_TOK = None

_TRUE_NUMERAL_MQ_START_RE = re.compile(
    r"^(?:[0-9〇一二两三四五六七八九十百千万亿廿几半]|"
    r"零(?:$|[0-9零〇一二两三四五六七八九十百千万亿点度分秒元块倍成号上下]))"
)
_DEMO_ORD_MQ_START = frozenset("这那哪每各上下前后另此第拾")


def _normalize_pos_tag(word: str, flag: str) -> str:
    """将 jieba 误标为 'mq' 的非数词实体名词（如「零件」「零部件」）归一化为普通名词 'n'。"""
    if flag == "mq" and len(word) >= 2:
        if not _TRUE_NUMERAL_MQ_START_RE.match(word) and word[0] not in _DEMO_ORD_MQ_START:
            return "n"
    return flag


def _get_clean_pseg_tokenizer() -> Tuple[Any, Any]:
    """返回隔离的 jieba Tokenizer 与 POSTokenizer（永不污染全局 jieba.dt）。"""
    global _CLEAN_JIEBA_TOK, _CLEAN_PSEG_TOK
    if _CLEAN_JIEBA_TOK is None or _CLEAN_PSEG_TOK is None:
        import jieba  # type: ignore
        import jieba.posseg as pseg  # type: ignore
        jieba.setLogLevel(60)
        tok = jieba.Tokenizer()
        tok.initialize()
        _CLEAN_JIEBA_TOK = tok
        _CLEAN_PSEG_TOK = pseg.POSTokenizer(tok)
    return _CLEAN_JIEBA_TOK, _CLEAN_PSEG_TOK


def _is_cantonese_ne_demo_head(text: str, pos_after_flag: str = "") -> bool:
    """判断以「呢」开头的是否为粤语指示代词短语（呢度/呢批/呢个/呢+量词/名词），而非句末语气助词。"""
    t = str(text or "").lstrip("，、；：,;: 。！？!? ")
    if not t.startswith("呢"):
        return False
    if t.startswith(_CANTONESE_NE_DEMO_PREFIXES):
        return True
    if len(t) >= 2:
        if pos_after_flag in {"q", "m", "mq", "r", "n", "nz", "f", "s", "t"}:
            return True
        sub_pairs = _pseg_cut_clean(t[1:4])
        if sub_pairs and sub_pairs[0][1] in {"q", "m", "mq", "r", "n", "nz", "f", "s", "t"}:
            return True
    return False


_DEG_HAODUO_HEAD_CHARS = frozenset("高高低大小长短快慢多好坏差贵平轻重稳顺热冷清亮暗强弱难易少")
_DEG_HAODUO_CLAUSE_STARTERS = (
    "但系", "但是", "不过", "所以", "因此", "而且", "然后", "其实", "结果", "现在", "如今", "依家", "反而", "却",
)
_PERSONAL_CLAUSE_PRONOUNS = frozenset({
    "我", "你", "他", "她", "它", "佢", "咱", "咱们",
    "我们", "你们", "他们", "她们", "它们", "我哋", "你哋", "佢哋", "大家",
})


def _is_pronoun_led_clause(
    np_plain: str,
    prot_terms: Optional[Sequence[str]] = None,
) -> bool:
    """判断后续片段是否以人称代词领起独立分句（排除「我们的工具」「他买的食材」等定中名词短语）。"""
    t = strip_display_punctuation(str(np_plain or "")).replace(" ", "")
    if not t:
        return False
    ps = [
        (wp, _normalize_pos_tag(wp, fp))
        for wp, fp in _pseg_cut_clean(t, prot_terms=prot_terms)
    ]
    if len(ps) < 2 or ps[0][0] not in _PERSONAL_CLAUSE_PRONOUNS:
        return False
    de_idxs = [idx_p for idx_p, (wp, _fp) in enumerate(ps) if wp in ("的", "嘅")]
    if de_idxs:
        return any(fp in ("p", "v", "vg", "vd", "d", "ad") for _wp, fp in ps[de_idxs[-1] + 1 :])
    return any(fp in ("p", "v", "vg", "vd", "d", "ad", "a", "z", "c", "y") for _wp, fp in ps[1:])


def is_degree_complement_haoduo(
    cur_plain: str,
    nxt_plain: str = "",
    prot_terms: Optional[Sequence[str]] = None,
) -> bool:
    """区分程度补语「高咗好多/贵了很多/差好多」与定语修饰「就有好多｜新鲜嘅海鲜」「摆好多｜卡座」。"""
    cp = strip_display_punctuation(str(cur_plain or "")).replace(" ", "")
    np = strip_display_punctuation(str(nxt_plain or "")).replace(" ", "")
    if len(cp) < 3 or not cp.endswith(("好多", "有啲")):
        return False
    is_haoduo = cp.endswith("好多")
    stem = cp[:-2]
    head = ""
    if stem.endswith(("咗", "了", "得")) and len(stem) >= 2:
        head = stem[:-1]
    elif stem.endswith("起嚟") and len(stem) >= 3:
        head = stem[:-2]
    if head:
        h_pairs = _pseg_cut_clean(head, prot_terms=prot_terms)
        hf = h_pairs[-1][1] if h_pairs else ""
        hw = h_pairs[-1][0] if h_pairs else ""
        if hf not in ("v", "vn", "vg", "a", "ad", "an", "ag", "z") and stem.endswith(("咗", "了", "得")):
            s_ctx = _pseg_cut_clean(stem, prot_terms=prot_terms)
            if s_ctx:
                t_pair = s_ctx[-2] if (len(s_ctx) >= 2 and s_ctx[-1][0] in ("咗", "了", "得")) else s_ctx[-1]
                if t_pair[1] in ("v", "vn", "vg", "a", "ad", "an", "ag", "z"):
                    hw, hf = t_pair
                elif stem.endswith(("咗", "得")):
                    le_ctx = _pseg_cut_clean(head + "了", prot_terms=prot_terms)
                    if le_ctx:
                        t_pair2 = le_ctx[-2] if (len(le_ctx) >= 2 and le_ctx[-1][0] == "了") else le_ctx[-1]
                        if t_pair2[1] in ("v", "vn", "vg", "a", "ad", "an", "ag", "z"):
                            hw, hf = t_pair2
        if hf in ("a", "ad", "an", "ag", "z") or (is_haoduo and head[-1] in _DEG_HAODUO_HEAD_CHARS):
            return True
        is_verb_or_aspect_pred = (
            hf in ("v", "vn", "vg")
            or (stem.endswith(("咗", "了", "得")) and len(hw) == 1 and hf == "n" and hw not in _STANDALONE_MEASURE_UNITS)
        )
        if is_verb_or_aspect_pred:
            if (
                not np
                or np.startswith(_DEG_HAODUO_CLAUSE_STARTERS)
                or _is_pronoun_led_clause(np, prot_terms=prot_terms)
                or is_independent_svo_clause(np, prot_terms=prot_terms, prev_plain=cp)
            ):
                return True
            np_pairs = _pseg_cut_clean(np, prot_terms=prot_terms)
            if np_pairs and np_pairs[0][1] in ("c", "d"):
                return True
        return False
    s_pairs = _pseg_cut_clean(stem, prot_terms=prot_terms)
    sf = s_pairs[-1][1] if s_pairs else ""
    if sf in ("a", "ad", "an", "ag", "z") or (is_haoduo and stem[-1] in _DEG_HAODUO_HEAD_CHARS):
        if not np or np.startswith(_DEG_HAODUO_CLAUSE_STARTERS) or is_independent_svo_clause(np, prot_terms=prot_terms, prev_plain=cp):
            return True
        np_pairs = _pseg_cut_clean(np, prot_terms=prot_terms)
        if np_pairs and np_pairs[0][1] in ("c", "d", "r"):
            return True
    if np and (np.startswith(_DEG_HAODUO_CLAUSE_STARTERS) or (_pseg_cut_clean(np, prot_terms=prot_terms)[:1] or [("", "")])[0][1] == "c"):
        return True
    return False


def iter_num_unit_spans(
    text: str,
    pattern: Optional[re.Pattern[str]] = None,
) -> List[Tuple[int, int]]:
    """返回「数字+单位」原子跨度，并对高歧义单字单位执行 jieba 跨界词频防护（不切碎「一把抓/一口气/一头雾水/2024安全」）。"""
    s = str(text or "")
    if not s:
        return []
    pat = pattern or _NUM_UNIT_PRESPLIT_RE
    raw_matches = [m.span() for m in pat.finditer(s)]
    if not raw_matches:
        return []
    tok_dict = None
    jieba_spans: Optional[List[Tuple[str, int, int]]] = None
    spans_out: List[Tuple[int, int]] = []
    for ms, me in raw_matches:
        mt = s[ms:me]
        if (
            len(mt) >= 2
            and mt[-1] in _AMBIGUOUS_SINGLE_CJK_UNITS
            and not _MULTI_UNIT_END_RE.search(mt)
        ):
            if jieba_spans is None:
                try:
                    tok_dict, _ = _get_clean_pseg_tokenizer()
                    jieba_spans = list(tok_dict.tokenize(s))
                except Exception:
                    jieba_spans = []
            straddled = False
            left_straddled = False
            if tok_dict is not None:
                for w_j, ws_j, we_j in jieba_spans:
                    freq_j = tok_dict.FREQ.get(w_j, 0)
                    if ws_j < ms < we_j and (
                        freq_j >= 50
                        or (len(w_j) >= 3 and we_j >= me)
                    ):
                        left_straddled = True
                        break
                    if ws_j < me < we_j and (
                        freq_j >= 50
                        or (len(w_j) >= 3 and ws_j == ms)
                    ):
                        straddled = True
                        break
            if left_straddled:
                continue
            if straddled:
                trimmed = mt[:-1]
                num_core = trimmed.rstrip("+＋")
                if num_core and _NUM_ONLY_RE.match(num_core) and not (len(num_core) == 1 and num_core in _ONE_CHAR_CH_NUMERALS):
                    spans_out.append((ms, me - 1))
                continue
        spans_out.append((ms, me))
    return spans_out


def _pseg_cut_clean(
    text: str,
    prot_terms: Optional[Sequence[str]] = None,
    pause_breaks: Optional[Sequence[int]] = None,
) -> List[Tuple[str, str]]:
    """先按保护词、通用口语/平台词与「数字+量词」原子预拆分，再按真实停顿切段并调用隔离 POSTokenizer 标注词性。"""
    s = str(text or "")
    if not s:
        return []
    hard_prot = sorted(
        {
            tm for tm in (
                set(prot_terms or ())
                | _UNIVERSAL_SPOKEN_LEXICON
                | set(_UNIVERSAL_PUBLIC_PLATFORMS)
            )
            if len(tm) >= 2 and tm in s
        },
        key=len,
        reverse=True,
    )
    atomic_spans: List[Tuple[int, int, str]] = []
    occupied: List[Tuple[int, int]] = []

    def _claim(a: int, b: int, flag: str) -> None:
        if b <= a:
            return
        if any(a < ob and b > oa for oa, ob in occupied):
            return
        occupied.append((a, b))
        atomic_spans.append((a, b, flag))

    for tm in hard_prot:
        for m in re.finditer(re.escape(tm), s):
            _claim(m.start(), m.end(), "nz")
    for m_hyph in _HYPHEN_ASCII_TOKEN_RE.finditer(s):
        _claim(m_hyph.start(), m_hyph.end(), "nz")
    for m_alnum in _ALNUM_MIXED_TOKEN_RE.finditer(s):
        _claim(m_alnum.start(), m_alnum.end(), "nz")
    for ms_num, me_num in iter_num_unit_spans(s):
        if me_num - ms_num >= 2:
            _claim(ms_num, me_num, "mq")
    atomic_spans.sort(key=lambda x: x[0])

    valid_pauses = sorted(
        {
            p for p in (pause_breaks or ())
            if 0 < p < len(s) and not any(sa < p < sb for sa, sb, _ in atomic_spans)
        }
    )

    def _cut_gap_aware(pt_tok: Any, seg_s: str, offset: int) -> List[Tuple[str, str]]:
        sub_cuts = [0] + [p - offset for p in valid_pauses if offset < p < offset + len(seg_s)] + [len(seg_s)]
        res: List[Tuple[str, str]] = []
        for a_i, b_i in zip(sub_cuts, sub_cuts[1:]):
            if b_i <= a_i:
                continue
            chunk_str = seg_s[a_i:b_i]
            if pt_tok is not None:
                for w, f in pt_tok.cut(chunk_str):
                    if w:
                        ws = str(w)
                        res.append((ws, _normalize_pos_tag(ws, str(f))))
            else:
                for ch in chunk_str:
                    res.append((ch, "x"))
        return res

    raw_pairs: List[Tuple[str, str]] = []
    try:
        _, pt = _get_clean_pseg_tokenizer()
        last_i = 0
        for sa, sb, flag in atomic_spans:
            if sa > last_i:
                raw_pairs.extend(_cut_gap_aware(pt, s[last_i:sa], last_i))
            tok_sp = s[sa:sb]
            raw_pairs.append((tok_sp, _normalize_pos_tag(tok_sp, flag)))
            last_i = sb
        if last_i < len(s):
            raw_pairs.extend(_cut_gap_aware(pt, s[last_i:], last_i))
    except Exception:
        last_i = 0
        for sa, sb, flag in atomic_spans:
            if sa > last_i:
                raw_pairs.extend(_cut_gap_aware(None, s[last_i:sa], last_i))
            raw_pairs.append((s[sa:sb], flag))
            last_i = sb
        if last_i < len(s):
            raw_pairs.extend(_cut_gap_aware(None, s[last_i:], last_i))
    if not valid_pauses:
        return _coalesce_pos_pairs(raw_pairs)
    pause_set = set(valid_pauses)
    return _coalesce_pos_pairs(raw_pairs, pause_breaks=pause_set)


_BOUND_LOC_OR_SUFFIX = frozenset("上下里内外中前后边旁制区感率化性台场")
_BOUND_FACILITY_SUFFIX = frozenset("仓库厂站港园馆室厅")
_BOUND_DERIV_SUFFIX = frozenset("化度率性感制")
_SUFFIX_MOD_CHARS = frozenset("式型级款版")
_SUFFIX_DERIV_STEM_POS = frozenset({"n", "vn", "v", "a", "b", "an", "ad", "nz", "ns", "nt", "j", "ng", "i"})
_BOUND_TECH_OBJ_SUFFIX = frozenset("码卡仪器剂槽阀泵芯")
_STANDALONE_MEASURE_UNITS = (
    "平方米", "平方", "平米", "毫秒", "微秒", "纳秒", "厘米", "毫米", "公分",
    "公斤", "千克", "毫升", "毫安时", "毫安", "毫克", "微克", "千瓦时", "千瓦", "兆瓦", "千伏",
    "英寸", "英尺", "公顷", "海里", "克拉",
    "小时", "分钟", "点半", "点一刻", "点二刻", "点三刻", "一刻", "二刻", "三刻",
    "天", "年", "月", "日", "时", "分", "秒", "周", "岁", "元", "块", "角", "钱",
    "万", "亿", "倍", "折", "成", "平", "度", "米", "克", "斤", "两", "吨", "升", "%", "％", "半",
    "匹", "节", "颗", "粒", "片", "支", "根", "盏", "扇", "口", "头", "亩", "瓦", "伏", "安",
)


def _coalesce_pos_pairs(
    pairs: List[Tuple[str, str]],
    pause_breaks: Optional[set[int]] = None,
) -> List[Tuple[str, str]]:
    """通用形态句法合并：数量+单字名词、名词+方位后缀、形容词/动名词+化度率性感、下/上+量词、代词+自己/哋、一XX、好+单字形。"""
    if len(pairs) < 2:
        return pairs
    cur = list(pairs)
    out: List[Tuple[str, str]] = []
    pauses = pause_breaks or set()
    char_pos = 0
    i = 0
    while i < len(cur):
        w, f = cur[i]
        boundary_pos = char_pos + len(w)
        if i + 1 < len(cur) and boundary_pos not in pauses:
            nw, nf = cur[i + 1]
            # 1) 数字/分类量词 + 单字名词/量词（三件+事 -> 三件事，两个+字 -> 两个字）
            #    已有独立计量/时间/货币单位的 mq（如「二十八天」「六百平」「三百万」）绝不再吞并后续单字（如「省下」的「省」）
            if (
                f in ("m", "mq")
                and len(nw) == 1
                and nf in ("n", "ng", "q", "vn")
                and not w.endswith(_STANDALONE_MEASURE_UNITS)
                and not any(ch.isascii() and ch.isalpha() for ch in w)
            ):
                out.append((w + nw, "n"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 2) 下/上/前/后/另 + 数量词（下+一条 -> 下一条）
            if w in ("下", "上", "前", "后", "另") and nf in ("m", "mq") and len(nw) <= 3:
                out.append((w + nw, "mq"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 3) 指示代词 + 单字量词（这+句 -> 这句，哪+条 -> 哪条）
            if w in ("这", "那", "哪", "每", "各") and len(nw) == 1 and nf in ("q", "mq", "n"):
                out.append((w + nw, "mq"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 4) 短名词/动名词(<=2字) + 单字方位/粘着词缀（利润+里 -> 利润里，前提+上 -> 前提上，店+里 -> 店里）
            #    以及 2..3 字实词/形/动词干 + 派生词缀（舒适+化 -> 舒适化，踏实+感 -> 踏实感，饱和+度 -> 饱和度）
            if f in ("n", "vn", "ns", "s") and len(w) <= 2 and len(nw) == 1 and nw in _BOUND_LOC_OR_SUFFIX:
                out.append((w + nw, "s"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            if 2 <= len(w) <= 3 and f in ("n", "vn", "v", "a", "b", "an", "ad") and nw in _BOUND_DERIV_SUFFIX:
                out.append((w + nw, "n"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            if len(w) == 2 and f in ("n", "vn", "ns", "s", "b", "j") and len(nw) == 1 and nw in _BOUND_FACILITY_SUFFIX:
                out.append((w + nw, "n"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 4a) 2字名词/动名词 + 单字器材/编码后缀（故障+码 -> 故障码，检测+仪 -> 检测仪）
            if (
                len(w) == 2
                and f in ("n", "vn")
                and len(nw) == 1
                and nw in _BOUND_TECH_OBJ_SUFFIX
                and not (nw == "卡" and i + 2 < len(cur) and str(cur[i + 2][0])[:1] in "在到住掉了着死顿")
            ):
                out.append((w + nw, "n"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 4b) 单字实词/简称 + 单字粘着语素（蛋+挞 -> 蛋挞，广+式 -> 广式，川+式 -> 川式，沪+式 -> 沪式）
            if (
                len(w) == 1
                and f in ("n", "ng", "nr", "ns", "nz", "b", "j")
                and len(nw) == 1
                and nf in ("g", "ng", "k")
                and w not in _DP_NO_TAIL_CHARS
                and nw not in _DP_NO_LEAD_CHARS
            ):
                out.append((w + nw, "n"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 4c) 实词/修饰语词干 + 粘着后缀（全自动+式 -> 全自动式，沉浸+式 -> 沉浸式，旗舰+款 -> 旗舰款，工业+级 -> 工业级，基础+版 -> 基础版，老板+们 -> 老板们）
            if (
                1 <= len(w) <= 4
                and f in _SUFFIX_DERIV_STEM_POS
                and w not in _DP_NO_TAIL_CHARS
                and len(nw) == 1
                and nw not in _DP_NO_LEAD_CHARS
                and (
                    nf == "k"
                    or (
                        len(w) >= 2
                        and nw in _SUFFIX_MOD_CHARS
                        and nf in ("k", "ng", "n", "q", "m")
                    )
                )
            ):
                out.append((w + nw, "n"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 5) 2字名词 + 台上/台下/里头（剪辑+台上 -> 剪辑台上）
            if f in ("n", "vn") and len(w) == 2 and nw in ("台上", "台下", "里头"):
                out.append((w + nw, "s"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 5b) 2字名词 + 单字状态形容词（行情+差 -> 行情差，效率+高 -> 效率高，味道+好 -> 味道好）
            if f in ("n", "vn") and len(w) == 2 and len(nw) == 1 and nf in ("a", "ag"):
                out.append((w + nw, "a"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 5c) 单字动词 + 单字结果补语（抄+完 -> 抄完，做+完 -> 做完，改+完 -> 改完），及「重+单字动词」（重+做 -> 重做）
            if f in ("v", "vg") and len(w) == 1 and nw in ("完", "掉", "住", "好", "到", "破", "碎", "光", "满", "错", "对", "清", "成"):
                out.append((w + nw, "v"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            if w == "重" and len(nw) == 1 and nf in ("v", "vn"):
                out.append((w + nw, "v"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 5d) 2字非叠字动词/形容词/可动词化双字词 ABAB 重叠（考虑+考虑 -> 考虑考虑，意思+意思 -> 意思意思，体验+体验 -> 体验体验）
            if (
                len(w) == 2
                and nw == w
                and w[0] != w[1]
                and f in ("v", "vn", "vd", "vg", "a", "an", "ad", "z", "n")
                and all("\u4e00" <= ch <= "\u9fff" for ch in w)
                and w not in _NON_ABAB_STUTTER_WORDS
                and w not in _STUTTER_DISCOURSE_2CHAR
                and not (
                    f == "n"
                    and i >= 1
                    and cur[i - 1][1] in ("r", "m", "mq", "q")
                    and cur[i - 1][0] not in ("我们", "你们", "他们", "她们", "它们", "咱", "大家", "自己", "我", "你", "他", "她", "佢")
                )
            ):
                out.append((w + nw, "v" if (f.startswith("v") or f == "n") else "a"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 5e) 误粘连「并重」+单字动词+句末/标点/体貌/补语/宾语或数量状语（「并重装/并重装了/并重装，/并重做一遍」->「并」+「重装/重做」）
            if (
                w == "并重"
                and len(nw) == 1
                and nw in "装做建写设启拍录编排"
                and nf in ("v", "vn", "vg")
                and (
                    i + 2 == len(cur)
                    or cur[i + 2][1] == "x"
                    or cur[i + 2][0] in ("了", "咗", "着", "过", "完", "好", "成", "掉", "上", "下", "起", "回")
                    or (
                        cur[i + 2][1] in ("n", "nr", "ns", "nt", "nz", "vn", "r", "m", "mq", "q", "b", "a", "f", "s", "l", "t")
                        and not cur[i + 2][0].startswith("不")
                    )
                )
            ):
                out.append(("并", "c"))
                out.append(("重" + nw, "v"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 6) 好/真/太 + 单字形容词（好+傻 -> 好傻）
            if w in ("好", "真", "太") and len(nw) == 1 and nf in ("a", "ag", "ad"):
                out.append((w + nw, "a"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 7) 重叠量词：一X+X（一条+条 -> 一条条）
            if len(w) == 2 and w[0] == "一" and len(nw) == 1 and nw == w[1]:
                out.append((w + nw, "mq"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 8) 代词 + 自己 / 哋（你+自己 -> 你自己，我+哋 -> 我哋）
            if w in ("你", "我", "他", "她", "它", "咱", "佢") and nw in ("自己", "哋", "们"):
                out.append((w + nw, "r"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
            # 9) 代词 + 那/这（别人+那 -> 别人那）
            if w in ("别人", "大家", "自己") and nw in ("那", "这"):
                out.append((w + nw, "r"))
                char_pos += len(w) + len(nw)
                i += 2
                continue
        out.append((w, f))
        char_pos += len(w)
        i += 1
    return out


def _merge_numeral_units(toks: List[str]) -> List[str]:
    """把「六百」+「平」、「三」+「百万」、「廿」+「五」这类相邻碎片拼回一整块。"""
    out: List[str] = []
    for tk in toks:
        if out and _NUM_ONLY_RE.match(out[-1]):
            if _UNIT_ONLY_RE.match(tk) or (_NUM_ONLY_RE.match(tk) and len(out[-1]) <= 3):
                out[-1] = out[-1] + tk
                continue
        out.append(tk)
    return out


def _merge_cohesive_phrases(toks: List[str], prot_set: set[str]) -> List[str]:
    """合并被分词器拆开的保护词、通用口语/平台词与形态句法搭子。"""
    if len(toks) < 2:
        return toks
    cohesive = _UNIVERSAL_SPOKEN_LEXICON | set(_UNIVERSAL_PUBLIC_PLATFORMS) | prot_set
    out = list(toks)
    changed = True
    while changed and len(out) >= 2:
        changed = False
        nxt: List[str] = []
        i = 0
        while i < len(out):
            merged_hit = None
            for span in (3, 2):
                if i + span <= len(out):
                    cand = "".join(out[i : i + span])
                    if cand in cohesive:
                        merged_hit = (cand, span)
                        break
            if merged_hit:
                nxt.append(merged_hit[0])
                i += merged_hit[1]
                changed = True
            else:
                nxt.append(out[i])
                i += 1
        out = nxt
    # 通用重叠量词与所有格原子合并（「一条」+「条」→「一条条」，「你」+「自己」→「你自己」）
    final_out: List[str] = []
    i = 0
    while i < len(out):
        cur_t = out[i]
        if i + 1 < len(out):
            nxt_t = out[i + 1]
            if (len(cur_t) == 2 and cur_t[0] == "一" and len(nxt_t) == 1 and nxt_t == cur_t[1]):
                final_out.append(cur_t + nxt_t)
                i += 2
                continue
            if cur_t in ("你", "我", "他", "她", "它", "咱", "佢") and nxt_t in ("自己", "哋", "们"):
                final_out.append(cur_t + nxt_t)
                i += 2
                continue
        final_out.append(cur_t)
        i += 1
    return final_out


def tokenize_semantic_words(
    sentence: str,
    terms: Optional[List[str]] = None,
    pause_breaks: Optional[Sequence[int]] = None,
) -> List[str]:
    """
    词边界切分器：基于隔离 POSTokenizer + 原子保护预拆分 + 通用形态句法合并。
    严格保护 terms 中的专有名词、成语与“数字+单位”不被切碎，且绝不污染全局 jieba 状态。
    """
    s = str(sentence or "")
    if not s:
        return []
    prot = sorted([t for t in (terms or []) if t], key=len, reverse=True)
    prot_set = set(prot)
    raw_toks = _merge_numeral_units([w for w, _f in _pseg_cut_clean(s, prot_terms=prot, pause_breaks=pause_breaks) if w])

    # 后处理：强制合并跨 token 的“数字 + 单位/小数”
    merged: List[str] = []
    for tok in raw_toks:
        if (
            merged
            and re.search(r"[0-9]$", merged[-1])
            and not re.search(r"[A-Za-z]", merged[-1])
            and tok
            and ((len(tok) == 1 and tok in _DP_UNIT_AFTER_NUM) or bool(_UNIT_ONLY_RE.match(tok)) or tok[0] in "0123456789.:")
        ):
            merged[-1] += tok
        else:
            merged.append(tok)
    return _merge_cohesive_phrases(merged, prot_set)


def split_semantic_two_lines(
    text: str,
    keywords: Optional[Sequence[str]] = None,
    terms: Optional[Sequence[str]] = None,
    max_first: Optional[int] = None,
) -> Tuple[str, str]:
    """按语义词边界平衡切分为双行，绝不在专有名词、保护词、数字量词或助词/介词边界腰斩。"""
    t = str(text or "").strip()
    if not t:
        return "", ""
    if "\n" in t:
        parts = [p.strip() for p in t.splitlines() if p.strip()]
        return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0] if parts else "", "")
    for sep in ("，", "。", "！", "？", "：", " ", "/", "|", "·"):
        if sep in t:
            parts = [p.strip() for p in t.split(sep, 1) if p.strip()]
            if len(parts) == 2:
                return (parts[0][:max_first] if max_first else parts[0]), parts[1]
    if len(t) < 5 or (max_first is not None and len(t) <= max_first and len(t) < 6):
        return t, ""

    kw_list = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    prot = sorted(
        set([str(x).strip() for x in (terms or []) if len(str(x).strip()) >= 2])
        | set(k for k in kw_list if len(k) >= 2)
        | _UNIVERSAL_SPOKEN_LEXICON
        | set(_UNIVERSAL_PUBLIC_PLATFORMS),
        key=len,
        reverse=True,
    )
    pos_pairs = _pseg_cut_clean(t, prot_terms=prot)
    toks = tokenize_semantic_words(t, terms=prot)
    bounds: List[int] = [0]
    for tk in toks:
        bounds.append(bounds[-1] + len(tk))

    pos_before: Dict[int, str] = {}
    pos_after: Dict[int, str] = {}
    cur_p = 0
    for w_p, f_p in pos_pairs:
        end_p = cur_p + len(w_p)
        pos_after[cur_p] = f_p
        pos_before[end_p] = f_p
        cur_p = end_p

    prot_spans: List[Tuple[int, int]] = []
    num_spans: List[Tuple[int, int]] = []
    for tm in prot:
        for m in re.finditer(re.escape(tm), t):
            prot_spans.append((m.start(), m.end()))
    for m_hyph in _HYPHEN_ASCII_TOKEN_RE.finditer(t):
        prot_spans.append((m_hyph.start(), m_hyph.end()))
        num_spans.append((m_hyph.start(), m_hyph.end()))
    for m_alnum in _ALNUM_MIXED_TOKEN_RE.finditer(t):
        prot_spans.append((m_alnum.start(), m_alnum.end()))
        num_spans.append((m_alnum.start(), m_alnum.end()))
    for ms_nu, me_nu in iter_num_unit_spans(t):
        if me_nu - ms_nu >= 2:
            prot_spans.append((ms_nu, me_nu))
            num_spans.append((ms_nu, me_nu))

    cands = [b for b in bounds if 2 <= b <= len(t) - 2 and not any(s < b < e for s, e in prot_spans)]
    if not cands:
        base_toks = tokenize_semantic_words(t, terms=[])
        base_bounds = [0]
        for tk in base_toks:
            base_bounds.append(base_bounds[-1] + len(tk))
        cands = [b for b in sorted(set(bounds + base_bounds)) if 2 <= b <= len(t) - 2 and not any(s < b < e for s, e in num_spans)]
    if not cands:
        cands = [b for b in range(2, len(t) - 1) if not any(s < b < e for s, e in num_spans)]
    if not cands:
        cands = [b for b in bounds if 1 <= b <= len(t) - 1 and not any(s < b < e for s, e in num_spans)]
    if not cands:
        cut_fallback = min(max_first or len(t), (len(t) + 1) // 2)
        return t[:cut_fallback], t[cut_fallback:]

    kw_starts = set()
    for kw in kw_list:
        idx_k = t.find(kw)
        if 2 <= idx_k <= len(t) - 2:
            kw_starts.add(idx_k)

    _STRICT_NO_TAIL = frozenset("的嘅地得之把被从向给俾在喺了咗着过这那呢嗰𠮶某每各")
    mid = len(t) / 2.0
    best_pos = cands[0]
    best_cost = float("inf")
    for pos in cands:
        cost = abs(pos - mid) * 1.4
        if max_first is not None and pos > max_first:
            cost += (pos - max_first) * 25.0
        if pos in kw_starts:
            cost -= 6.0
        left_last = t[pos - 1]
        right_first = t[pos]
        if left_last in _STRICT_NO_TAIL:
            cost += 260.0
        elif left_last in _DP_NO_TAIL_CHARS:
            cost += 60.0
        if pos_before.get(pos) in {"u", "uj", "ug", "ud", "ul", "uz", "p", "c", "m", "mq"}:
            cost += 90.0
        if right_first in _DP_NO_LEAD_CHARS and not (right_first == "呢" and _is_cantonese_ne_demo_head(t[pos:], pos_after.get(pos, ""))):
            cost += 260.0
        if any(t[pos:].startswith(pfx) for pfx in _CLAUSE_BREAK_PREFIXES):
            cost -= 10.0
        if cost < best_cost:
            best_cost = cost
            best_pos = pos
    return t[:best_pos], t[best_pos:]


def dp_segment_short_lines(
    sentence: str,
    max_width_px: int,
    font_path: Optional[str] = None,
    font_size: int = 62,
    terms: Optional[List[str]] = None,
    max_chars: int = 12,
    strip_punct: bool = True,
    word_gaps: Optional[Dict[int, float]] = None,
    strict_width: bool = False,
) -> List[Tuple[int, int, str]]:
    """
    词边界 + 词性句法 + 声学气口动态规划（DP）最优短句切分：
    返回 [(start_char_idx, end_char_idx, display_text), ...]。
    """
    raw = str(sentence or "")
    if not raw.strip():
        return []
    prot = [t for t in (terms or []) if len(t) >= 2]
    gaps_map = word_gaps or {}
    pause_breaks = [gp for gp, gs in gaps_map.items() if gs >= 0.20]
    toks = tokenize_semantic_words(raw, terms=prot, pause_breaks=pause_breaks)
    bounds = [0]
    for tk in toks:
        bounds.append(bounds[-1] + len(tk))
    nb = len(bounds)
    if nb <= 1:
        return []

    def _disp(a: int, b: int) -> str:
        sub = raw[a:b].strip()
        if strip_punct:
            sub = strip_display_punctuation(sub)
            sub = re.sub(r"(?<=[\u4e00-\u9fff])\s+|\s+(?=[\u4e00-\u9fff])", "", sub)
            sub = re.sub(r"\s+", " ", sub).strip()
        return sub

    # 预计算保护词区间：用户 terms + 通用功能词/平台词 + 数字量词 + 短双/三词英文品牌
    prot_spans: List[Tuple[int, int]] = []
    for tm in set(prot) | _UNIVERSAL_SPOKEN_LEXICON | set(_UNIVERSAL_PUBLIC_PLATFORMS):
        if len(tm) < 2:
            continue
        for m in re.finditer(re.escape(tm), raw):
            prot_spans.append((m.start(), m.end()))
    for m_hyph in _HYPHEN_ASCII_TOKEN_RE.finditer(raw):
        h_tok = m_hyph.group(0)
        if len(h_tok) <= 20 and _measure_text_px(h_tok, font_path, font_size)[0] <= max_width_px:
            prot_spans.append((m_hyph.start(), m_hyph.end()))
    for m_alnum in _ALNUM_MIXED_TOKEN_RE.finditer(raw):
        a_tok = m_alnum.group(0)
        if len(a_tok) <= 20 and _measure_text_px(a_tok, font_path, font_size)[0] <= max_width_px:
            prot_spans.append((m_alnum.start(), m_alnum.end()))
    for ms_nu, me_nu in iter_num_unit_spans(raw):
        if me_nu - ms_nu >= 2:
            prot_spans.append((ms_nu, me_nu))
    for m in _MULTI_WORD_ASCII_BRAND_RE.finditer(raw):
        brand_s = m.group(0)
        if len(brand_s) <= 20 and _measure_text_px(brand_s, font_path, font_size)[0] <= max_width_px:
            prot_spans.append((m.start(), m.end()))
        else:
            for m2 in re.finditer(
                r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*[ \t]+[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9])",
                brand_s,
            ):
                sub_b = m2.group(0)
                if len(sub_b) <= 20 and _measure_text_px(sub_b, font_path, font_size)[0] <= max_width_px:
                    prot_spans.append((m.start() + m2.start(), m.start() + m2.end()))

    # 仅对未受保护的四字「2+2」名词/动名词复合词（如「胶原蛋白」）记录紧急兜底切分点，
    # 当长句「数量+的+四字复合词+...」无论如何也放不进单行宽时，宁可「胶原|蛋白」也不触发「的|」或<=3残句
    emergency_split_bounds: set[int] = set()
    _b_cur = 0
    for tk in toks:
        _b_nxt = _b_cur + len(tk)
        if (
            len(tk) == 4
            and not any(ps < _b_cur + 2 < pe for ps, pe in prot_spans)
            and not (gaps_map and any(_b_cur < gp < _b_nxt and gaps_map[gp] >= 0.22 for gp in gaps_map))
        ):
            _tk_ps = _pseg_cut_clean(tk, prot_terms=prot)
            if len(_tk_ps) == 1 and _tk_ps[0][1] in {"n", "vn", "nz"}:
                _tk_l = _pseg_cut_clean(tk[:2], prot_terms=prot)
                _tk_r = _pseg_cut_clean(tk[2:], prot_terms=prot)
                if (
                    len(_tk_l) == 1
                    and len(_tk_r) == 1
                    and _tk_l[0][1] in {"n", "ns", "vn", "nz", "v", "a", "b"}
                    and _tk_r[0][1] in {"n", "ns", "vn", "nz"}
                ):
                    emergency_split_bounds.add(_b_cur + 2)
        _b_cur = _b_nxt
    if emergency_split_bounds:
        bounds = sorted(set(bounds) | emergency_split_bounds)
        nb = len(bounds)

    # 预计算隔离词性边界（pseg + 原子词合并），通用识别助词/介词/连词/量词/修饰结构
    pos_before: Dict[int, str] = {}
    pos_after: Dict[int, str] = {}
    tok_before_len: Dict[int, int] = {}
    tok_after_len: Dict[int, int] = {}
    tok_before_word: Dict[int, str] = {}
    tok_after_word: Dict[int, str] = {}
    pos_pairs = _pseg_cut_clean(raw, prot_terms=prot, pause_breaks=pause_breaks)
    _cursor_p = 0
    for _w_p, _flag_p in pos_pairs:
        _end_p = _cursor_p + len(_w_p)
        pos_after[_cursor_p] = _flag_p
        pos_before[_end_p] = _flag_p
        tok_after_len[_cursor_p] = len(_w_p)
        tok_before_len[_end_p] = len(_w_p)
        tok_after_word[_cursor_p] = _w_p
        tok_before_word[_end_p] = _w_p
        _cursor_p = _end_p

    # 通用形态句法保护（无特定片源字面词）：重叠量词「一XX」、代词+自己/哋、通用「NP+的/嘅+单字名」
    for m_auto in re.finditer(
        r"(?:一([个条次步天张间份句种环段块招])\1|[你我他她它咱佢]自己的?|[我你佢]哋嘅?|"
        r"[\u4e00-\u9fff]{1,3}[的嘅][\u4e00-\u9fff](?=$|[，。！？、；：,\s]|是|系|都|就|里|上|下|中|内|外))",
        raw,
    ):
        prot_spans.append((m_auto.start(), m_auto.end()))

    total_compact_len = len(_disp(0, len(raw)).replace(" ", ""))
    _NO_TAIL_POS = {"u", "uj", "ug", "ud", "ul", "uz", "p", "c"}
    _STRICT_TAIL_AUX = frozenset("的嘅地得之")
    _STRICT_TAIL_PREP = frozenset("把被从向给俾和与及跟同在喺对为")
    _STRICT_TAIL_DEMO = frozenset("这那呢嗰𠮶某每各哪")
    _STRICT_TAIL_MULTI_MOD = (
        "有啲", "好多", "呢啲", "𠮶啲", "嗰啲", "呢个", "嗰个", "𠮶个",
        "这个", "那个", "哪个", "每个", "某个", "呢一个", "𠮶一个", "嗰一个",
        "𠮶种", "嗰种", "这种", "那种", "呢种", "哪种", "每种", "某种",
    )
    _STRICT_TAIL_NUM_CLF_MOD = ("几个", "一间", "一份", "一种")
    _DIR_COMPLEMENTS = frozenset({"起来", "出来", "下去", "过去", "回来", "进去", "过来", "上来", "下来", "出来了", "起来了", "下去了"})
    _NEG_MOD_TAILS = ("没有", "没", "不", "唔", "别", "不要", "不能", "不会", "不想", "不敢", "不用")
    _DEG_NEG_SINGLE_ADV = frozenset("最很太极更不没唔")

    def _break_cost(pos: int) -> float:
        if pos <= 0 or pos >= len(raw):
            return 0.0
        if pos in emergency_split_bounds:
            return 245.0
        in_prot = any(ps < pos < pe for ps, pe in prot_spans)
        left = raw[:pos]
        right = raw[pos:]
        # 句末标点与软标点一样是天然断点；但若被护词包含仍重罚；英文词间空格不视为 0 成本标点且重罚拆开
        is_inter_ascii_space = bool(
            left and right
            and (left[-1].isspace() or right[0].isspace())
            and re.search(r"[A-Za-z0-9]\s*$", left)
            and re.match(r"^\s*[A-Za-z0-9]", right)
        )
        left_punct = bool(left and (left[-1] in _DP_SOFT_PUNCT or left[-1] in "。！？!?")) and not is_inter_ascii_space
        if left_punct and not in_prot:
            if left[-1] in _DP_SOFT_PUNCT:
                _l_hd = left.rstrip("，、；：,;: 。！？!?")
                _r_hd = right.lstrip("，、；：,;: 。！？!?")
                if _l_hd.endswith(("好多", "有啲")) and _r_hd:
                    _r_cl_hd = re.split(r"[，。！？、；：,;:!?]", _r_hd, 1)[0]
                    if (
                        not is_degree_complement_haoduo(_l_hd, _r_cl_hd, prot_terms=prot)
                        and not _is_pronoun_led_clause(_r_cl_hd, prot_terms=prot)
                        and not is_independent_svo_clause(_r_cl_hd, prot_terms=prot, prev_plain=_l_hd)
                    ):
                        left_punct = False
            if left_punct:
                return 0.0
        cost = 5.0
        if is_inter_ascii_space:
            cost += 260.0
        r_trim = right.lstrip("，、；：,;: 。！？!?")
        l_trim = left.rstrip("，、；：,;: 。！？!?")
        r0 = r_trim[:1]
        l_last = l_trim[-1:]
        pb = pos_before.get(pos, "")
        pa = pos_after.get(pos, "")
        lb = tok_before_len.get(pos, 2)
        la = tok_after_len.get(pos, 2)
        wb = tok_before_word.get(pos, "")
        wa = tok_after_word.get(pos, "")

        gap_v = gaps_map.get(pos) if gaps_map else None
        if gap_v is None and gaps_map and left and left[-1] in "，、；：,;:":
            gap_v = gaps_map.get(pos - 1)
        has_pause_22 = bool(gap_v is not None and gap_v >= 0.22 and not in_prot)

        # 1) 严禁助词/单字方位词/单字量词/粘着后缀作下一行首（豁免粤语指示代词「呢度/呢批/呢个/...」）
        is_canto_ne_demo = (r0 == "呢" and _is_cantonese_ne_demo_head(r_trim, pa))
        if (r0 in _DP_NO_LEAD_CHARS and not is_canto_ne_demo) or (
            pa in {"u", "uj", "ug", "ud", "ul", "uz", "uv", "f", "q"} and la == 1 and not is_canto_ne_demo
        ):
            cost += 1200.0
        if (
            (pa == "k" and la == 1 and pb not in {"", "x", "u", "uj", "ug", "ud", "ul", "uz", "uv", "c", "p"})
            or (pb == "j" and pa in {"g", "ng", "k"} and lb == 1 and la == 1)
            or (la == 1 and wa in _SUFFIX_MOD_CHARS and pa in {"k", "ng", "n", "q", "m"} and pb in _SUFFIX_DERIV_STEM_POS and lb >= 2)
        ):
            cost += 1200.0
        _is_dir_end = l_trim.endswith(("出来了", "起来了", "下去了", "回来了", "过去了", "进来了", "过来了"))
        # 2) 严禁句中动貌/结构助词/介词/指示代词/多字修饰量词/否定修饰挂尾
        if l_last in _DP_MID_CLAUSE_NO_TAIL_ASPECT and r_trim and not _is_dir_end and (
            pa in {"m", "mq", "q", "n", "vn", "nr", "ns", "nt", "nz", "r", "v", "a", "b", "i"}
            or r0.isdigit()
            or r0 in "一二两三四五六七八九十百千万亿"
        ):
            _clause_r = re.split(r"[，。！？、；：,;:!?]", r_trim, 1)[0]
            _is_num_head_np = bool(pa in {"m", "mq", "q"} or r0.isdigit() or r0 in "一二两三四五六七八九十百千万亿")
            _is_heavy_np_rhs = (
                l_last in {"了", "咗", "着", "过"}
                and len(_clause_r) >= 8
                and (
                    (
                        _is_num_head_np
                        and len(_clause_r) > la
                        and _clause_r[la] in {"的", "嘅"}
                        and (
                            (len(l_trim) >= 3 and la >= 4)
                            or (len(l_trim) >= 5 and la >= 3)
                        )
                    )
                    or (
                        not _is_num_head_np
                        and len(l_trim) >= 3
                        and la >= 4
                        and ("的" in _clause_r[2:10] or "嘅" in _clause_r[2:10])
                    )
                )
                and not _clause_r.endswith(("的", "嘅"))
            )
            cost += 45.0 if _is_heavy_np_rhs else 420.0
        elif l_last in _STRICT_TAIL_AUX and r_trim:
            # 区分定语助词（后接体词性成分，严禁断开）与 4+字名物化「VP的」（后接 是/系/其实/就/都/会 等谓语）
            _prev_before_de_pos = pos_before.get(pos - 1, "")
            _is_num_quant_de = (
                _prev_before_de_pos in {"m", "mq", "q"}
                or bool(re.search(r"[零〇一二两三四五六七八九十百千万亿廿\d%％][平间套家斤克元米层项吨箱台]?[的嘅]$", l_trim))
            )
            _clause_r_de = re.split(r"[，。！？、；：,;:!?]", r_trim, 1)[0]
            _left_de_has_pred = bool(
                l_last in {"的", "嘅"}
                and len(l_trim) >= 4
                and any(fp in {"v", "vg", "vd", "l"} for _wp, fp in _pseg_cut_clean(l_trim[:-1], prot_terms=prot))
            )
            if (
                l_last in {"的", "嘅"}
                and len(l_trim) >= 4
                and not l_trim.endswith(("我的", "你的", "他的", "她的", "它的", "咱的", "佢的", "们的"))
                and not _is_num_quant_de
                and (
                    is_predicative_after_headless_de(l_trim, _clause_r_de, prot_terms=prot)
                    or (
                        _left_de_has_pred
                        and not (pa in {"n", "nr", "ns", "nt", "nz", "vn", "s", "f", "m", "mq", "q", "r", "a", "i", "l"})
                        and not (pa == "v" and _is_v_noun_deg_predicate(_clause_r_de, prot_terms=prot))
                        and (r_trim.startswith(("是", "系", "其实", "就", "都", "也", "才", "会", "要", "能", "交给")) or pa in {"v", "d"})
                    )
                )
            ):
                cost += 95.0
            else:
                cost += 1050.0
        elif l_last in _STRICT_TAIL_PREP and r_trim and (pb == "p" or (lb == 1 and pb != "n")):
            cost += 720.0
        elif l_last in _STRICT_TAIL_DEMO and r_trim:
            cost += 680.0
        elif l_trim.endswith(_STRICT_TAIL_MULTI_MOD) and r_trim:
            _clause_r_mod = re.split(r"[，。！？、；：,;:!?]", r_trim, 1)[0]
            if not (
                l_trim.endswith(("好多", "有啲"))
                and (
                    is_degree_complement_haoduo(l_trim, _clause_r_mod, prot_terms=prot)
                    or _is_pronoun_led_clause(_clause_r_mod, prot_terms=prot)
                    or is_independent_svo_clause(_clause_r_mod, prot_terms=prot, prev_plain=l_trim)
                )
            ):
                cost += 650.0
        elif l_trim.endswith(_STRICT_TAIL_NUM_CLF_MOD) and r_trim:
            cost += 340.0
        elif not has_pause_22 and pb in {"d", "zg"} and l_last in _DEG_NEG_SINGLE_ADV and lb == 1 and r_trim and pa in {"v", "a", "ad", "an", "p", "d", "m", "n", "vn"}:
            cost += 370.0
        elif l_trim.endswith(_NEG_MOD_TAILS) and r_trim and pa in {"v", "p", "a", "n", "vn"}:
            cost += 240.0
        elif pb == "m" and lb == 1 and l_last == "一" and pa in {"v", "vn", "a", "ad"}:
            cost += 560.0 if (l_trim.endswith("从一") and not has_pause_22) else 480.0
        elif pb in {"m", "mq", "q"} and pa in {"n", "nr", "ns", "nt", "nz", "vn", "a", "mq"}:
            if re.search(r"[这那哪每各某][个件条位家间套批种款张只次项环段块]$", l_trim):
                cost += 650.0
            else:
                cost += 260.0
        elif pb in {"q", "m"} and lb == 1 and l_last in {"种", "个", "件", "条", "批", "套", "份", "款", "张", "只", "位", "家"} and r_trim:
            cost += 380.0
        elif pb in _NO_TAIL_POS and not (pb == "ul" and _is_dir_end):
            cost += 220.0
        elif pb in {"v", "a"} and wa in _DIR_COMPLEMENTS:
            cost += 220.0
        elif pb == "m" and l_trim.endswith(("一起", "一块", "一同", "一齐")) and pa == "v":
            cost += 160.0
        elif lb == 1 and pb in {"v", "a"} and pa == "p":
            cost += 240.0
        elif pb == "d" and (pa in {"v", "a", "p", "d", "m"} or (lb == 1 and pa in {"n", "vn"})):
            cost += 160.0
        elif (
            l_last in {"我", "你", "他", "她", "它", "佢"}
            and lb == 1
            and r_trim
            and not any(l_trim[:-1].endswith(k) for k in _PRON_TAIL_KEEP_PREV)
            and not r_trim.startswith(("的", "嘅", "哋", "们", "自己", "也", "都"))
            and (gap_v is None or gap_v <= 0.25)
        ):
            cost += 720.0 if len(l_trim) >= 4 else (240.0 if pa in {"v", "a", "d", "p", "t", "m"} else 0.0)
        elif not has_pause_22 and lb == 1 and pb == "v" and pa in {"n", "nr", "ns", "nt", "nz", "vn", "r", "m", "mq", "s", "p"}:
            cost += 280.0
        elif not has_pause_22 and pb == "v" and pa == "r" and la == 1 and r_trim and l_last not in _DP_NO_TAIL_CHARS:
            cost += 220.0
        elif l_last in {"是", "系", "有", "没", "唔", "不"} and r_trim and pa in {"n", "nr", "ns", "nt", "nz", "vn", "r", "m", "mq", "a", "v", "p"}:
            if not (has_pause_22 and l_last in {"是", "系", "有"}):
                cost += 65.0
        elif not has_pause_22 and l_last in _DP_NO_TAIL_CHARS:
            cost += 48.0

        # 3) 复合名词与定中短语内聚（非声学停顿处防止劈开「微|水泥」「装修|公司」「材料|损耗」「管理|制度」「干|炒牛河」）
        if not has_pause_22:
            _pa_is_noun = pa in {"n", "vn", "nz"} and wa not in _SPOKEN_NON_NOUN_WORDS
            _pb_is_nz_or_noun = wb not in _SPOKEN_NON_NOUN_WORDS
            _has_loc_prep_prefix = bool(
                len(l_trim) >= 2
                and l_trim[-2] in {"喺", "在", "到", "从", "去", "回", "往"}
                and pos_before.get(pos - 1, "") in {"p", "yg", "v"}
            )
            if lb == 1 and la == 1 and pb in {"v", "a", "b"} and pa == "v" and l_last not in _DP_NO_TAIL_CHARS:
                cost += 160.0
            elif (
                lb == 1
                and la == 1
                and l_last not in _DP_NO_TAIL_CHARS
                and r0 not in _DP_NO_LEAD_CHARS
                and r0 not in _DP_NO_TAIL_CHARS
                and (
                    (pb in {"ng", "nr", "ns", "nt", "nz", "b", "j"} and pa in {"n", "ng", "ns", "s"})
                    or (
                        pa == "v"
                        and (
                            (_has_loc_prep_prefix and pb in {"ng", "nr", "ns", "nt", "nz", "b", "j", "a", "n"})
                            or (pb in {"nr", "ns", "nt", "nz", "b"} and wa not in _COMMON_SINGLE_PRED_VERBS)
                        )
                    )
                )
            ):
                cost += 220.0
            elif lb == 1 and la == 2 and pb in {"b", "a", "n", "ng", "j"} and _pa_is_noun:
                cost += 220.0
            elif (
                lb == 2
                and la == 1
                and _pb_is_nz_or_noun
                and (
                    pb in {"nr", "ns", "nt", "nz", "b"}
                    or (pb in {"n", "vn", "a"} and not l_trim.endswith(("一个", "一间", "一份", "一种", "呢个", "嗰个", "𠮶个")))
                )
                and pa in {"n", "ng"}
                and r0 not in _DP_NO_LEAD_CHARS
                and r0 not in _DP_NO_TAIL_CHARS
            ):
                cost += 210.0 if pb in {"nr", "ns", "nt", "nz", "b"} else 180.0
            elif lb in (1, 2, 3) and la in (2, 3) and _pb_is_nz_or_noun and (
                (pb in {"nz", "b", "a"} and ((pa in {"a", "b"} and wa not in _SPOKEN_NON_NOUN_WORDS) or _pa_is_noun))
                or (pb == "i" and l_trim.endswith(_SPEC_MOD_HEAD_PREFIXES) and ((pa in {"a", "b", "v"} and wa not in _SPOKEN_NON_NOUN_WORDS) or _pa_is_noun))
            ):
                cost += 260.0 if l_trim.endswith(_SPEC_MOD_HEAD_PREFIXES) else (260.0 if (lb == 2 and la == 2 and pb == "nz" and _pa_is_noun) else 220.0)
            elif (
                lb in (1, 2, 3)
                and la in (2, 3)
                and pb in {"nz", "b"}
                and _pb_is_nz_or_noun
                and pa == "v"
                and is_compound_np_body(
                    re.split(r"[，。！？、；：,;:!?]", l_trim)[-1][-lb:] + re.split(r"[，。！？、；：,;:!?]", r_trim, 1)[0],
                    prot_terms=prot,
                )
            ):
                cost += 460.0
            elif lb == 2 and la in (2, 3) and _pb_is_nz_or_noun and pb in {"n", "nz", "vn", "b", "a", "t"} and _pa_is_noun:
                cost += 260.0 if (la == 2 and pb in {"nz", "b"}) else 220.0
            elif lb == 2 and la == 2 and pb in {"ns", "nr", "nt", "v"} and _pa_is_noun:
                cost += 180.0
            elif _pb_is_nz_or_noun and pb in {"n", "ns", "nr", "nt", "nz", "vn"} and (pa in {"n", "vn", "s"} and wa not in _SPOKEN_NON_NOUN_WORDS) and (lb + la) <= 4:
                cost += 35.0
            elif pb == "r" and lb <= 2 and pa == "v":
                cost += 25.0

        # 4) 明显新分句/新主语/转折词前换行奖励
        if any(r_trim.startswith(pfx) for pfx in _CLAUSE_BREAK_PREFIXES):
            cost -= 18.0
        if r0.isdigit():
            cost += 25.0
        if in_prot:
            cost += 2000.0
        elif gap_v is not None:
            if gap_v >= 0.35:
                cost -= 80.0
            elif gap_v >= 0.22:
                cost -= 28.0
            elif gap_v >= 0.10:
                cost -= 9.0
            elif gap_v < 0.04 and not left_punct:
                cost += 10.0
        return cost

    inf = float("inf")
    best = [inf] * nb
    prev = [-1] * nb
    best[0] = 0.0
    max_len_soft = max(6, int(max_chars))
    max_len_hard = max(8, int(max_chars) + 2)
    soft_width_cap = int(max_width_px) if strict_width else int(round(max_width_px * 1.08))

    for j in range(1, nb):
        for i in range(j):
            if best[i] == inf:
                continue
            txt = _disp(bounds[i], bounds[j])
            if not txt:
                if best[i] < best[j]:
                    best[j] = best[i]
                    prev[j] = i
                continue
            raw_compact_len = len(txt.replace(" ", ""))
            ascii_n = sum(1 for ch in txt if ch.isascii() and not ch.isspace())
            cjk_n = raw_compact_len - ascii_n
            eff_len = (cjk_n + math.ceil(ascii_n * 0.55)) if ascii_n >= 4 else raw_compact_len
            if eff_len > max_len_hard:
                continue
            pw, _ = _measure_text_px(txt, font_path, font_size)
            width_pen = 0.0
            if pw > max_width_px:
                if (not strict_width) and pw <= soft_width_cap and eff_len <= max_len_hard:
                    width_pen = 42.0
                else:
                    continue
            ext_n = max(0, eff_len - max_len_soft)
            len_ext_pen = (10.0 if ext_n == 1 else ext_n * 24.0) if pw <= max_width_px else ext_n * 28.0
            compact_len = raw_compact_len

            # 若本段内部跨越 >=0.16s/0.22s/0.35s 真实声学气口，重罚跨气口粘连以促成自然停顿换行
            mid_gap_pen = 0.0
            if gaps_map and compact_len >= 4:
                for gpos, gsec in gaps_map.items():
                    if gsec >= 0.35 and bounds[i] + 2 <= gpos <= bounds[j] - 2:
                        if not any(ps < gpos < pe for ps, pe in prot_spans):
                            mid_gap_pen += 420.0
                    elif gsec >= 0.22 and bounds[i] + 2 <= gpos <= bounds[j] - 2:
                        if not any(ps < gpos < pe for ps, pe in prot_spans):
                            mid_gap_pen += 160.0
                    elif gsec >= 0.16 and compact_len >= 7 and bounds[i] + 3 <= gpos <= bounds[j] - 3:
                        if not any(ps < gpos < pe for ps, pe in prot_spans):
                            mid_gap_pen += 22.0

            _seg_raw = raw[bounds[i]:bounds[j]]
            _tail_after = raw[bounds[j]:].lstrip("，、；：,;: 。！？!?")
            _seg_stripped = _seg_raw.rstrip("，、；：,;:。！？!? ")
            _seg_bad_tail = bool(_seg_stripped and _seg_stripped[-1] in (_STRICT_TAIL_AUX | _STRICT_TAIL_PREP | _STRICT_TAIL_DEMO))
            _prev_is_punct = bool(bounds[i] > 0 and raw[bounds[i] - 1] in "，、；：,;:。！？!?")
            _next_is_punct_or_end = bool(bounds[j] >= len(raw) or raw[bounds[j]] in "，、；：,;:。！？!?")
            _is_natural_clause = (
                compact_len >= 2
                and not _seg_bad_tail
                and (
                    bool(_seg_raw and _seg_raw[-1] in "，、；：,;:")
                    or (_prev_is_punct and _next_is_punct_or_end)
                )
            ) or (
                compact_len >= 4
                and any(_tail_after.startswith(pfx) for pfx in _CLAUSE_BREAK_PREFIXES)
            )
            short_pen = max(0, 5 - compact_len) * 2.5 if j < nb - 1 or i > 0 else 0.0
            if compact_len <= 2 and (i > 0 or j < nb - 1):
                if _is_natural_clause:
                    short_pen += 45.0
                else:
                    short_pen += 320.0 if total_compact_len >= 5 else 24.0
            elif compact_len == 3 and (i > 0 or j < nb - 1):
                if _is_natural_clause:
                    short_pen += 8.0
                else:
                    short_pen += 280.0 if total_compact_len >= 6 else 12.0

            # 避免在无标点长块内部粘连两个完整分句（>=4 字后出现明确新分句引导词）
            mid_clause_pen = 0.0
            if compact_len >= 8:
                for k_c in range(4, len(txt) - 2):
                    if txt[k_c:].startswith(_CLAUSE_BREAK_PREFIXES):
                        mid_clause_pen += 22.0
                        break

            cand = best[i] + _break_cost(bounds[j]) + short_pen + mid_gap_pen + width_pen + len_ext_pen + mid_clause_pen + 1.0
            mid = raw[bounds[i]:bounds[j]]
            if _DP_HARD_SPLIT_RE.search(mid.rstrip("。！？!? ")):
                cand += 40.0
            else:
                _mid_soft = mid.rstrip("，、；：,;: ")
                for _m_soft in re.finditer(r"[，、；：,;:]", _mid_soft):
                    _left_s = strip_display_punctuation(_mid_soft[:_m_soft.start()]).strip()
                    _right_s = strip_display_punctuation(_mid_soft[_m_soft.end():]).strip()
                    _is_num_delim = (
                        _m_soft.group(0) in ",:："
                        and bool(_left_s and _left_s[-1].isdigit() and _right_s and _right_s[0].isdigit())
                    )
                    _abs_c_start = bounds[i] + _m_soft.start()
                    _abs_c_end = bounds[i] + _m_soft.end()
                    _full_l_c = strip_display_punctuation(
                        re.split(r"[，。！？、；：,;:!?]", raw[:_abs_c_start].rstrip(" ，、；：,;:"))[-1]
                    ).strip()
                    _full_r_c = strip_display_punctuation(
                        re.split(r"[，。！？、；：,;:!?]", raw[_abs_c_end:].lstrip(" ，、；：,;:"), 1)[0]
                    ).strip()
                    _full_r_use = _full_r_c or _right_s
                    _full_l_use = _full_l_c or _left_s
                    _is_non_deg_haoduo_comma = bool(
                        _left_s.endswith(("好多", "有啲"))
                        and _right_s
                        and not is_degree_complement_haoduo(
                            _full_l_use,
                            _full_r_use,
                            prot_terms=prot,
                        )
                        and not _is_pronoun_led_clause(_full_r_use, prot_terms=prot)
                        and not is_independent_svo_clause(_full_r_use, prot_terms=prot, prev_plain=_full_l_use)
                    )
                    if not _is_num_delim and not _is_non_deg_haoduo_comma and len(_left_s) >= 1 and len(_right_s) >= 1:
                        cand += 500.0
                        if len(_right_s) == 1 and _right_s in "我你他她佢":
                            cand += 180.0
                        break
            if cand < best[j]:
                best[j] = cand
                prev[j] = i

    if best[nb - 1] == inf:
        return [(0, len(raw), _disp(0, len(raw)) or raw.strip())]

    out: List[Tuple[int, int, str]] = []
    cur = nb - 1
    while cur > 0:
        p = prev[cur]
        disp_t = _disp(bounds[p], bounds[cur])
        if disp_t:
            out.append((bounds[p], bounds[cur], disp_t))
        cur = p
    out.reverse()
    return out


def align_corrected_text_to_words(
    corrected_text: str,
    asr_words: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], float]:
    """用 difflib.SequenceMatcher 将人工校对后的纯文本字符级对齐到逐字时间戳，保留数字间小数点/冒号，未匹配字平滑插值。"""
    norm_corr = strip_display_punctuation(re.sub(r"\s+", "", str(corrected_text or "")))
    clean_chars = [ch for ch in norm_corr if ch.strip()]
    asr_flat: List[Tuple[str, float, float]] = []
    for w in asr_words:
        wt = strip_display_punctuation(re.sub(r"\s+", "", str(w.get("word") or "")))
        if not wt:
            continue
        ws = float(w.get("start_sec", 0.0))
        we = float(w.get("end_sec", ws + 0.08))
        n = len(wt)
        for k, ch in enumerate(wt):
            asr_flat.append((ch, ws + (we - ws) * k / n, ws + (we - ws) * (k + 1) / n))

    if not clean_chars or not asr_flat:
        return [], 0.0
    sm = difflib.SequenceMatcher(None, clean_chars, [c for c, _, _ in asr_flat], autojunk=False)
    times: List[Optional[Tuple[float, float]]] = [None] * len(clean_chars)
    matched = 0
    for blk in sm.get_matching_blocks():
        matched += blk.size
        for k in range(blk.size):
            _, s, e = asr_flat[blk.b + k]
            times[blk.a + k] = (s, e)

    first_asr_t = float(asr_flat[0][1])
    n = len(times)
    i = 0
    while i < n:
        if times[i] is None:
            j = i
            while j < n and times[j] is None:
                j += 1
            gap_n = max(1, j - i)
            if i == 0 and j < n and times[j] is not None:
                # 句首未匹配字：严格落在第一个已匹配字 times[j][0] 之前，彻底避免时间轴倒挂重叠
                t1 = float(times[j][0])
                t0 = max(0.0, min(first_asr_t, t1 - 0.12 * gap_n))
                if t1 <= t0 + 0.01:
                    t0 = max(0.0, t1 - 0.04 * gap_n)
                span = max(0.015, (t1 - t0) / gap_n)
                for k in range(i, j):
                    s_k = max(0.0, t1 - span * (j - k))
                    e_k = max(s_k + 0.01, t1 - span * (j - k - 1))
                    times[k] = (s_k, e_k)
            else:
                t0 = float(times[i - 1][1]) if i > 0 and times[i - 1] else first_asr_t
                t1 = float(times[j][0]) if j < n and times[j] else (t0 + 0.16 * gap_n)
                span = max(0.02 * gap_n, t1 - t0) / gap_n
                for k in range(i, j):
                    times[k] = (t0 + span * (k - i), t0 + span * (k - i + 1))
            i = j
        else:
            i += 1

    aligned = [
        {"word": ch, "start_sec": round(float(t[0]), 4), "end_sec": round(float(t[1]), 4)}
        for ch, t in zip(clean_chars, times)
        if t is not None
    ]
    return aligned, round(matched / max(1, len(clean_chars)), 4)


def fmt_srt_time(sec: float) -> str:
    ms = int(round(max(0.0, float(sec)) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_r = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_r:03d}"


def strip_display_punctuation(text: str) -> str:
    raw = str(text or "")
    # 先将夹在数字间的全角冒号归一化为半角冒号
    raw = re.sub(r"(?<=\d)：(?=\d)", ":", raw)
    return _NON_CORE_PUNCT_RE.sub("", raw)


_FILLER_WORDS = {"诶", "欸", "呃", "嗯", "啊", "呀", "哇", "唉", "哦", "噢", "喔", "咧"}
_STUTTER_DISCOURSE_2CHAR = frozenset({
    "那个", "这个", "就是", "然后", "其实", "因为", "所以", "但是", "不过",
    "如果", "只要", "虽然", "而且", "或者", "首先", "其次", "最后", "我们",
    "你们", "他们", "她们", "它们", "自己", "大家", "什么", "怎么", "哪个", "哪些",
})
_ABAB_PREV_MODS = frozenset({"先", "再", "去", "来", "要", "想", "可以", "好好", "一起", "互相", "彼此", "多"})


def _drop_filler_tokens(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """剔掉纯语气词与紧邻重复多字口误词（绝不误删逐字流中的数字、标点、字母与中文 AA/ABAB 叠词）。"""
    out: List[Dict[str, Any]] = []
    for w in words:
        t = str(w.get("word") or "").strip()
        if not t:
            continue
        if t in _FILLER_WORDS:
            continue
        if (
            out
            and len(t) >= 2
            and not t.isascii()
            and not any(ch.isdigit() for ch in t)
            and not t.startswith("一")
            and not (len(t) == 2 and t[0] == t[1])
            and str(out[-1].get("word") or "") == t
        ):
            _is_abab_content = False
            if (
                len(t) == 2
                and all("\u4e00" <= ch <= "\u9fff" for ch in t)
                and t not in _STUTTER_DISCOURSE_2CHAR
                and t not in _NON_ABAB_STUTTER_WORDS
            ):
                _tp = _pseg_cut_clean(t)
                if len(_tp) == 1 and _tp[0][1] in ("v", "vn", "vd", "vg", "a", "an", "ad", "z"):
                    _is_abab_content = True
                elif len(_tp) == 1 and _tp[0][1] == "n":
                    _prev_w_tok = str(out[-2].get("word") or "").strip() if len(out) >= 2 else ""
                    _prev_is_demo = bool(
                        _prev_w_tok
                        and (
                            _prev_w_tok in ("这个", "那个", "这套", "那套", "这批", "那批", "一种", "一个", "一套", "呢个", "嗰个", "𠮶个")
                            or _prev_w_tok[-1] in ("这", "那", "呢", "嗰", "𠮶", "个", "套", "批", "种", "件", "家", "条")
                        )
                    )
                    if not _prev_is_demo:
                        _is_abab_content = True
                elif len(out) >= 2 and str(out[-2].get("word") or "").strip() in _ABAB_PREV_MODS:
                    _is_abab_content = True
            if not _is_abab_content:
                # 紧邻重复（结巴 / NG 残留）只留第一次的时长，不重复占字
                out[-1]["end_sec"] = max(float(out[-1].get("end_sec", 0.0)), float(w.get("end_sec", 0.0)))
                continue
        out.append(w)
    return out


def _clean_word_tokens(raw_words: List[Dict[str, Any]], strip_punct: bool) -> List[Dict[str, Any]]:
    """逐词清洗标点，并保护跨 token 拆分的数字小数点/时间冒号(如 '18:' + '30')不被误杀。"""
    if not strip_punct:
        out: List[Dict[str, Any]] = []
        for w in raw_words:
            t = str(w.get("word", "")).strip()
            if t:
                cw = dict(w)
                cw["word"] = t
                out.append(cw)
        return _drop_filler_tokens(out)

    out = []
    n = len(raw_words)
    for idx, w in enumerate(raw_words):
        orig = str(w.get("word", ""))
        prev_txt = str(raw_words[idx - 1].get("word", "")) if idx > 0 else ""
        next_txt = str(raw_words[idx + 1].get("word", "")) if idx + 1 < n else ""
        # 如果当前词以 .:： 结尾且下一词以数字开头，或以 .:： 开头且上一词以数字结尾，暂时用占位符保护
        # 对千分位逗号（如 '1' + ',' + '200'）同样在前后数字满足千分位分组时保护
        upcoming_digits = "".join(str(rw.get("word") or "") for rw in raw_words[idx + 1 : idx + 6])
        comma_lead_ok = bool(
            orig
            and orig[0] == ","
            and prev_txt
            and prev_txt[-1].isdigit()
            and re.match(r"^,\d{3}(?!\d)", orig + upcoming_digits)
        )
        comma_trail_ok = bool(
            orig
            and orig[-1] == ","
            and (prev_txt[-1].isdigit() if len(orig) == 1 and prev_txt else (len(orig) >= 2 and orig[-2].isdigit()))
            and re.match(r"^\d{3}(?!\d)", upcoming_digits)
        )
        lead_protected = bool(
            (orig and orig[0] == "." and prev_txt and prev_txt[-1].isascii() and prev_txt[-1].isalnum() and (next_txt[:1].isdigit() or (len(orig) >= 2 and orig[1].isdigit())))
            or (orig and orig[0] in ":：" and prev_txt and prev_txt[-1].isdigit())
            or comma_lead_ok
        )
        trail_protected = bool(
            (orig and orig[-1] == "." and prev_txt[-1:] + orig[:-1] and (orig[-2:-1] or prev_txt[-1:]).isascii() and (orig[-2:-1] or prev_txt[-1:]).isalnum() and next_txt and next_txt[0].isdigit())
            or (orig and orig[-1] in ":：" and next_txt and next_txt[0].isdigit())
            or comma_trail_ok
        )
        tmp = orig
        if lead_protected:
            tmp = "9" + tmp
        if trail_protected:
            tmp = tmp + ("999" if comma_trail_ok else "9")
        cleaned = strip_display_punctuation(tmp)
        if lead_protected and cleaned.startswith("9"):
            cleaned = cleaned[1:]
        if trail_protected:
            if comma_trail_ok and cleaned.endswith("999"):
                cleaned = cleaned[:-3]
            elif (not comma_trail_ok) and cleaned.endswith("9"):
                cleaned = cleaned[:-1]
        cleaned = cleaned.strip()
        # 句末标点会在这一步被剥掉。句界信息一旦丢了，后面的呼吸组与 DP 就再也分不清
        # 「为什么这么贵。其实…」和「为什么这么贵其实…」，会把两句话粘进同一行。
        # 所以在这里先打一个只在内部流转、落盘前会摘掉的标记。
        sent_end = bool(re.search(r"[。！？!?]", orig))
        if not cleaned:
            if sent_end and out:
                out[-1]["_sent_end"] = True
            continue
        cw = dict(w)
        cw["word"] = cleaned
        if sent_end:
            cw["_sent_end"] = True
        out.append(cw)
    return _drop_filler_tokens(out)


def fmt_ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass_text(txt: str) -> str:
    return str(txt or "").replace("\r\n", r"\N").replace("\n", r"\N").replace("{", r"\{").replace("}", r"\}")


def _measure_text_px(text: str, font_path: Optional[str], font_size: int) -> Tuple[int, int]:
    fnt = get_cached_font(font_path, font_size)
    if fnt is not None:
        try:
            bb = fnt.getbbox(text)
            return int(max(40, (bb[2] - bb[0]) + max(8, len(text) * 3))), int(max(font_size + 16, (bb[3] - bb[1]) + 28))
        except Exception:
            pass
    return int(len(text) * font_size * 1.04), int(font_size * 1.35 + 24)


# 避脸微移：只允许在 SUBTITLE_SAFE_ZONE 内上下挪，且上限 ±60px（用户实测 2026-11-24：
# 再大的位移会把字幕甩到胸口/腰部，观感比轻微压脸更差）。优先级：原地不动 > 近处不重叠 > IoU 最小。
FACE_AVOID_MAX_SHIFT_PX = 60
FACE_AVOID_STEP_PX = 15
FACE_AVOID_IOU_LIMIT = 0.01


def _compute_authoritative_subtitle_bbox(
    text: str,
    tpl: Dict[str, Any],
    font_path: Optional[str],
    face_boxes_px: List[Dict[str, Any]],
    keywords: Optional[Sequence[str]] = None,
    terms: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, int], int]:
    """按画布绝对正中 x=540 对称安全带 [220, 860] 计算尺寸并仅在垂直方向避开人脸五官，产出四端统一 final_bbox。

    返回 (final_bbox, face_avoid_dy)。水平中心永远锁在 540px（位移只走垂直方向，居中不会被破坏），
    face_avoid_dy 会写回 05_subtitles.json，供渲染/工程/质检三端对账。
    """
    base_fs = int(tpl.get("font_size", 62))
    kw_scale = float(tpl.get("keyword_scale", 1.133))
    dual_banner = bool(tpl.get("dual_line_banner"))
    safe_w_cap = SUBTITLE_SYMMETRIC_MAX_W  # 640px ([220, 860]，中心严格锁定 540px)
    center_x = SUBTITLE_CENTER_X  # 540px

    clean_len = len(str(text or "").replace("\n", "").strip())
    if dual_banner and ("\n" in str(text or "") or clean_len >= 5):
        raw_t = str(text or "").strip()
        if "\n" not in raw_t:
            l1, l2 = split_semantic_two_lines(raw_t, keywords=keywords, terms=terms)
        else:
            parts = raw_t.splitlines()
            l1, l2 = parts[0], (parts[1] if len(parts) > 1 else "")
        eff_fs = min(84, max(62, int(round(base_fs * min(1.15, max(1.0, (1.0 + kw_scale) * 0.5))))))
        w1, h1 = _measure_text_px(l1, font_path, eff_fs)
        w2, h2 = _measure_text_px(l2 or l1, font_path, eff_fs)
        est_w = max(w1, w2)
        est_h_2 = max(h1, h2) * 2 + 12
        box_w = min(safe_w_cap, max(180, est_w + 56))
        box_h = min(284, max(168, est_h_2 + 24))
    else:
        eff_fs = int(round(base_fs * min(1.20, max(1.0, (1.0 + kw_scale) * 0.5))))
        if dual_banner:
            eff_fs = min(100, eff_fs)
        est_w, est_h = _measure_text_px(text, font_path, eff_fs)
        box_w = min(safe_w_cap, max(160, est_w + 32))
        box_h = min(220, max(84, est_h + 16))
    # 水平中心 100% 锁定画布正中央 540px，且 box_w <= 640 保证永不触发右侧 860px 单侧推偏
    box_x = center_x - box_w // 2

    # 钳位 safe_y_center，默认对齐黄金水位 SUBTITLE_DEFAULT_Y_CENTER (1310px)
    safe_yc = int(tpl.get("safe_y_center", SUBTITLE_DEFAULT_Y_CENTER))
    box_y = max(SUBTITLE_SAFE_ZONE["y_min"], min(SUBTITLE_SAFE_ZONE["y_max"] - box_h, safe_yc - box_h // 2))
    cand = {"x": int(box_x), "y": int(box_y), "w": int(box_w), "h": int(box_h)}

    face_avoid_dy = 0
    if face_boxes_px:
        cur_iou = max(bbox_iou(cand, fb) for fb in face_boxes_px)
        if cur_iou > FACE_AVOID_IOU_LIMIT:
            y_lo = SUBTITLE_SAFE_ZONE["y_min"]
            y_hi = max(y_lo, SUBTITLE_SAFE_ZONE["y_max"] - box_h)
            offsets: List[int] = [0]
            d = FACE_AVOID_STEP_PX
            while d <= FACE_AVOID_MAX_SHIFT_PX:
                offsets.extend([d, -d])
                d += FACE_AVOID_STEP_PX
            best_cand, best_iou, best_dy = cand, cur_iou, 0
            for dy in offsets:
                ty = max(y_lo, min(y_hi, box_y + dy))
                alt = dict(cand, y=int(ty))
                iou_v = max(bbox_iou(alt, fb) for fb in face_boxes_px)
                if iou_v <= FACE_AVOID_IOU_LIMIT:
                    best_cand, best_iou, best_dy = alt, iou_v, int(ty) - int(box_y)
                    break
                if iou_v < best_iou:
                    best_cand, best_iou, best_dy = alt, iou_v, int(ty) - int(box_y)
            cand = best_cand
            face_avoid_dy = int(best_dy)
    return cand, face_avoid_dy


def _norm_tokens(s: Any) -> str:
    return strip_display_punctuation(re.sub(r"\s+", "", str(s or "")))


def align_words_to_sentence_text(text: str, asr_words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把**整理后**的句子文字重新对回逐字时间戳。

    为什么会不一致：ASR 给的是原话（`然后唔知道大家` / `诶唔知道大家中意食` / 句尾多个 `听`），
    上屏前 Agent 整理过（去口癖、补标点、合半句），字数和字面都变了。
    旧实现直接把 ASR 词表拼起来当字幕，于是屏幕上印的还是那堆口癖 —— 整理白整理了。
    """
    norm = _norm_tokens(text)
    if not norm or not asr_words:
        return []
    # align_corrected_text_to_words 返回的是逐字 dict 列表（word/start_sec/end_sec）
    char_times, _cov = align_corrected_text_to_words(text, asr_words)
    if len(char_times) != len(norm):
        return []
    out: List[Dict[str, Any]] = []
    i = 0
    for tok in tokenize_semantic_words(text):
        n = len(_norm_tokens(tok))
        if n <= 0:
            continue
        span = char_times[i:i + n]
        if len(span) < n:
            return []
        out.append({"word": tok,
                    "start_sec": float(span[0]["start_sec"]),
                    "end_sec": float(span[-1]["end_sec"])})
        i += n
    return out if i == len(norm) else []


# ── 字幕上屏文字的两处收口（2026-11-24 用户实测：字号放大后冒出「四大轩王啦」这种行，
#    以及只剩 2~3 个字的残句）────────────────────────────────────────────────
MIN_CHUNK_CHARS = 4      # 少于这个字数的字幕块不单独占一屏，并进相邻块
# 与成稿文字的相似下限：只改「听错一两个字」的情况（四大轩王→四大天王），改写过的整句不动。
# 配合「窗口单调向前」（cursor）使用：每块只在上一块之后的成稿文字里找，避免两块文字重叠。
CONFIRM_MATCH_RATIO = 0.75


def _retarget_words_to_text(words: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    """把逐词表里的**字形**按成稿订正，词数与时间轴一概不动。

    2026-11-24：ASR 把「四大天王」听成「四大轩王」，字幕就印错。成稿是用户核过的，
    整句做一次字符对齐，凡是「一对一替换」的位置就换成成稿的字（同音误听、专名听错都在这里修好）；
    增删块（口癖、重复词）保持原样，因此不会改动块边界，也不会把时间戳拉偏。
    """
    base = str(text or "")
    if not words or not base:
        return words
    joined = "".join(str(w.get("word") or "") for w in words)
    if not joined or joined == base:
        return words
    sm = difflib.SequenceMatcher(None, joined, base, autojunk=False)
    chars = list(joined)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                chars[i1 + k] = base[j1 + k]
    fixed = "".join(chars)
    if fixed == joined:
        return words
    out: List[Dict[str, Any]] = []
    pos = 0
    for w in words:
        wt = str(w.get("word") or "")
        n = len(wt)
        nw = dict(w)
        nw["word"] = fixed[pos : pos + n] or wt
        pos += n
        out.append(nw)
    return out

def _clean_display_keywords(raw_kws: Sequence[Any], sentence_text: str) -> List[str]:
    """清理句级伪关键词与跨词 4-gram 垃圾，严格经隔离 POSTokenizer 自然词边界与词性校验，
    只保留落在完整词边界上的高信息量实词，并将截断数词还原（「六百→六百平」「三分→三分之一」）。"""
    txt = strip_display_punctuation(re.sub(r"\s+", "", str(sentence_text or "")))
    if not txt:
        return []
    pos_pairs = _pseg_cut_clean(txt)
    toks = tokenize_semantic_words(txt)
    valid_bounds: set[str] = set()
    for i_t in range(len(toks)):
        valid_bounds.add(toks[i_t])
        if i_t + 1 < len(toks):
            valid_bounds.add(toks[i_t] + toks[i_t + 1])
    # 记录完整词性边界，拒绝跨词切分或以助词/介词/连词/代词起止的碎片
    pos_at_start: Dict[int, str] = {}
    pos_at_end: Dict[int, str] = {}
    cur_p = 0
    for w_p, f_p in pos_pairs:
        pos_at_start[cur_p] = f_p
        cur_p += len(w_p)
        pos_at_end[cur_p] = f_p

    _bad_head = set("是的了咗在把被从跟比和与让给俾那这呢嗰𠮶")
    _bad_tail = set("的嘅了咗呢吗吧啊呀么啲喺在把被从跟比和与")
    _bad_pos = {"u", "uj", "ug", "ud", "ul", "uz", "p", "c", "y", "e", "o"}
    out: List[str] = []
    for k in (raw_kws or []):
        ks = re.sub(r"\s+", "", str(k or ""))
        if not ks or ks in _STOP_KEYWORDS or len(ks) < 2:
            continue
        if ks in txt:
            m_frac = re.search(
                re.escape(ks) + r"(?:分之[一二两三四五六七八九十]+(?![百千万亿])|之[一二两三四五六七八九十]+(?![百千万亿])|[平平米个间张条只杯碗盘倍年天块元斤克]?)",
                txt,
            )
            if m_frac and re.fullmatch(r"[零〇一二两三四五六七八九十百千万亿0-9]+分?", ks):
                cand = m_frac.group(0)
                if len(cand) > len(ks):
                    ks = cand
        if ks not in txt or ks in _STOP_KEYWORDS or len(ks) < 2 or len(ks) > 6:
            continue
        if ks[0] in _bad_head or ks[-1] in _bad_tail:
            continue
        if (not re.search(r"\d", ks)) and (ks not in valid_bounds):
            continue
        hit_idx = txt.find(ks)
        if hit_idx >= 0 and not re.search(r"\d", ks):
            ps = pos_at_start.get(hit_idx, "")
            pe = pos_at_end.get(hit_idx + len(ks), "")
            if ps in _bad_pos or pe in _bad_pos:
                continue
        if ks not in out:
            out.append(ks)
    return out


def _paint_keyword_flags(words: List[Dict[str, Any]], keywords: Sequence[str]) -> List[Dict[str, Any]]:
    """先重置再按完整关键词短语重染 word['is_keyword']，彻底消除残词/虚词连带高亮。"""
    if not words:
        return words
    new_ws = [dict(w) for w in words]
    for w_item in new_ws:
        w_item["is_keyword"] = False
    if not keywords:
        return new_ws
    buf_tokens: List[Tuple[int, int, int]] = []
    cur_pos = 0
    joined_w: List[str] = []
    for wi, w_item in enumerate(new_ws):
        wt = strip_display_punctuation(re.sub(r"\s+", "", str(w_item.get("word") or "")))
        if not wt:
            continue
        joined_w.append(wt)
        buf_tokens.append((cur_pos, cur_pos + len(wt), wi))
        cur_pos += len(wt)
    joined_str = "".join(joined_w)
    for pk in keywords:
        pk_clean = strip_display_punctuation(re.sub(r"\s+", "", str(pk or "")))
        if not pk_clean:
            continue
        search_i = 0
        while True:
            hit_at = joined_str.find(pk_clean, search_i)
            if hit_at < 0:
                break
            hit_end = hit_at + len(pk_clean)
            for cs, ce, wi in buf_tokens:
                if cs < hit_end and ce > hit_at:
                    new_ws[wi]["is_keyword"] = True
            search_i = hit_end
    return new_ws


def _repair_words_from_script(words: List[Dict[str, Any]], script_text: str) -> List[Dict[str, Any]]:
    """当成稿文字与口播逐字高度吻合时，通过字符级 difflib 对齐补回 ASR 漏字（「氛→氛围」「四大王→四大天王」）。"""
    if not words or not str(script_text or "").strip():
        return words
    asr_plain = _norm_tokens("".join(str(w.get("word") or "") for w in words))
    scr_plain = _norm_tokens(script_text)
    if not asr_plain or not scr_plain:
        return words
    if asr_plain == scr_plain:
        return words
    ratio = difflib.SequenceMatcher(None, asr_plain, scr_plain, autojunk=False).ratio()
    if ratio < 0.78 or abs(len(scr_plain) - len(asr_plain)) > max(3, int(len(asr_plain) * 0.20)):
        return _retarget_words_to_text(words, scr_plain)
    aligned, _ = align_corrected_text_to_words(scr_plain, words)
    if len(aligned) != len(scr_plain):
        return _retarget_words_to_text(words, scr_plain)
    # 沿旧词表继承 is_keyword / _sent_end 元信息
    old_kw_flags = []
    for w in words:
        nt = _norm_tokens(w.get("word"))
        for _ in nt:
            old_kw_flags.append(bool(w.get("is_keyword", False)))
    sm = difflib.SequenceMatcher(None, asr_plain, scr_plain, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "replace"):
            for k in range(min(i2 - i1, j2 - j1)):
                if i1 + k < len(old_kw_flags) and old_kw_flags[i1 + k]:
                    aligned[j1 + k]["is_keyword"] = True
    # 严格锁回原参考词首尾时间包络 [ref_t0, ref_t1]，防止句首/句尾补回漏字后外扩 ±0.16s 触发字幕交叉同步偏差 FAIL
    ref_t0 = float(words[0]["start_sec"])
    ref_t1 = float(words[-1]["end_sec"])
    if aligned and float(aligned[0]["start_sec"]) < ref_t0 - 1e-4:
        first_e = float(words[0]["end_sec"])
        span_end = max(ref_t0 + 0.02, first_e)
        head_k = 0
        while head_k + 1 < len(aligned) and float(aligned[head_k + 1]["start_sec"]) < ref_t0 - 1e-4:
            head_k += 1
        head_cnt = head_k + 1
        if float(aligned[head_k]["end_sec"]) > span_end:
            span_end = max(span_end, float(aligned[head_k]["end_sec"]))
        step_h = (span_end - ref_t0) / float(max(1, head_cnt))
        for idx_h in range(head_cnt):
            aligned[idx_h]["start_sec"] = round(ref_t0 + idx_h * step_h, 4)
            aligned[idx_h]["end_sec"] = round(ref_t0 + (idx_h + 1) * step_h, 4)
    if aligned and float(aligned[-1]["end_sec"]) > ref_t1 + 1e-4:
        tail_anchor = min(float(words[-1]["start_sec"]), max(ref_t0, ref_t1 - 0.04))
        tail_start_idx = len(aligned) - 1
        while tail_start_idx > 0 and float(aligned[tail_start_idx - 1]["end_sec"]) > ref_t1 + 1e-4:
            tail_start_idx -= 1
        while tail_start_idx > 0 and float(aligned[tail_start_idx]["start_sec"]) >= tail_anchor - 1e-4:
            if float(aligned[tail_start_idx - 1]["start_sec"]) >= tail_anchor - 1e-4:
                tail_start_idx -= 1
            else:
                break
        tail_anchor = min(tail_anchor, float(aligned[tail_start_idx]["start_sec"]))
        if tail_start_idx > 0:
            tail_anchor = max(tail_anchor, float(aligned[tail_start_idx - 1]["end_sec"]))
        tail_cnt = len(aligned) - tail_start_idx
        step_t = max(0.01, (ref_t1 - tail_anchor) / float(max(1, tail_cnt)))
        for idx_o in range(tail_cnt):
            ci = tail_start_idx + idx_o
            aligned[ci]["start_sec"] = round(tail_anchor + idx_o * step_t, 4)
            aligned[ci]["end_sec"] = round(ref_t1 if idx_o == tail_cnt - 1 else (tail_anchor + (idx_o + 1) * step_t), 4)
    # 如果任一原词带 _sent_end，保留在最末字符
    if words[-1].get("_sent_end") and aligned:
        aligned[-1]["_sent_end"] = True
    return aligned


def _restore_script_punctuation(sent_text: str, script_lines: Sequence[str]) -> str:
    """将 02c_script_final.json 中的柔和标点（，、；：。！？）安全回贴到口播段落文字中，供 DP 在自然停顿点 zero-cost 断开。"""
    s_raw = str(sent_text or "").strip()
    if not s_raw or not script_lines or any(ch in re.sub(r"(?<=\d)[.:：,](?=\d)|(?<=[A-Za-z])\.(?=\d)", "", s_raw) for ch in "，、；：,;:"):
        return s_raw
    s_plain = _norm_tokens(s_raw)
    if len(s_plain) < 6:
        return s_raw
    for ln in script_lines:
        ln_str = str(ln or "").strip()
        if not ln_str:
            continue
        ln_plain = _norm_tokens(ln_str)
        if not ln_plain:
            continue
        # 建立 ln_plain 下标 -> 其后紧随的原始标点 映射
        punct_after: Dict[int, str] = {}
        pi = -1
        for idx_l, ch in enumerate(ln_str):
            is_num_sep = (
                (ch == "." and 0 < idx_l < len(ln_str) - 1 and ln_str[idx_l - 1].isascii() and ln_str[idx_l - 1].isalnum() and ln_str[idx_l + 1].isdigit())
                or (
                    ch in ":：,"
                    and 0 < idx_l < len(ln_str) - 1
                    and ln_str[idx_l - 1].isdigit()
                    and ln_str[idx_l + 1].isdigit()
                    and (ch != "," or bool(re.match(r"^\d{3}(?!\d)", ln_str[idx_l + 1 :])))
                )
            )
            if (ch in "，、；：,;:" or ch in "。！？!?") and not is_num_sep:
                if pi >= 0:
                    punct_after[pi] = punct_after.get(pi, "") + ch
            elif is_num_sep:
                pi += 1
            elif _norm_tokens(ch):
                pi += len(_norm_tokens(ch))
        if not punct_after:
            continue
        sm = difflib.SequenceMatcher(None, s_plain, ln_plain, autojunk=False)
        all_blocks = list(sm.get_matching_blocks())
        anchor_blocks = [b for b in all_blocks if b.size >= 4]
        if not anchor_blocks or sum(b.size for b in anchor_blocks) < min(6, int(len(s_plain) * 0.45)):
            continue
        blocks = [b for b in all_blocks if b.size >= 2]
        insert_after: Dict[int, str] = {}
        for b in blocks:
            for k in range(b.size):
                lp = b.b + k
                sp = b.a + k
                if lp in punct_after and sp < len(s_plain) - 1:
                    insert_after[sp] = punct_after[lp][0]
        if insert_after:
            out_chars: List[str] = []
            sp_idx = 0
            for ch_r in s_raw:
                out_chars.append(ch_r)
                if sp_idx < len(s_plain) and (ch_r == s_plain[sp_idx] or (ch_r == "：" and s_plain[sp_idx] == ":")):
                    if sp_idx in insert_after:
                        out_chars.append(insert_after[sp_idx])
                    sp_idx += 1
            return "".join(out_chars)
    return s_raw


def _join_chunk_texts(l_txt: str, r_txt: str) -> str:
    if l_txt and r_txt and l_txt[-1].isascii() and l_txt[-1].isalnum() and r_txt[0].isascii() and r_txt[0].isalnum():
        return l_txt + " " + r_txt
    return l_txt + r_txt


def _has_trailing_subject_pronoun_split(left_txt: str, right_txt: str) -> bool:
    lt = str(left_txt or "").rstrip()
    rt = str(right_txt or "").lstrip()
    if not lt or not rt or lt[-1] in "，、；：,;:。！？!?":
        return False
    lp = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", lt)
    rp = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", rt)
    return bool(
        len(lp) >= 4
        and lp[-1] in {"我", "你", "他", "她", "它", "佢"}
        and not any(lp[:-1].endswith(k) for k in _PRON_TAIL_KEEP_PREV)
        and not rp.startswith(("的", "嘅", "哋", "们", "自己", "也", "都"))
    )


def _merge_short_chunks(
    chunks: List[Dict[str, Any]],
    min_chars: int = MIN_CHUNK_CHARS,
    max_px: Optional[int] = None,
    font_path: Optional[str] = None,
    font_size: int = 62,
    protected_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """把 2~3 个字的残句并进相邻块（**并完必须仍在对称安全带内**）。

    为什么：字号修到真值（75px）后一行只放得下 8 个字，句尾常被切出一个「仲有」「去制造」，
    单占一屏既难看也被平台安全区体检判成「残句」。

    2026-11-24 实测补充：这里以前只管字数不管宽度，「三分之一」+「两百平去制造」被并成
    10 字 640px 的满宽行，渲染器只能靠溢出兑底把整行缩字，字号和居中都被带偏。
    现在合并前先量宽，超过对称安全带（max_px）就不并。
    """
    def _too_wide(txt: str, cap_mult: float = 1.0) -> bool:
        if not max_px:
            return False
        w, _ = _measure_text_px(str(txt or ""), font_path, font_size)
        return w > int(round(int(max_px) * cap_mult))

    def _clean_char_len(txt: str) -> int:
        return len(re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(txt or "")))

    def _eff_char_len(txt: str) -> int:
        compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(txt or ""))
        ascii_n = sum(1 for ch in compact if ch.isascii())
        cjk_n = len(compact) - ascii_n
        return (cjk_n + math.ceil(ascii_n * 0.55)) if ascii_n >= 4 else len(compact)

    def _is_ordinal_or_clause_starter(txt: str) -> bool:
        plain = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(txt or ""))
        return bool(_ORDINAL_SHORT_SENT_RE.match(plain) or _CLAUSE_START_PROTECT_RE.match(plain))

    def _creates_ordinal_tail(txt: str) -> bool:
        plain = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(txt or ""))
        return len(plain) >= 4 and bool(re.search(r"(第[一二三四五六七八九十两\d]+)$", plain))

    pre_chunks: List[Dict[str, Any]] = []
    for ch in chunks:
        ch_clean = dict(ch)
        # 剥掉被断行留在行首的孤立标点（如 '：虾饺排' -> '虾饺排'），避免既难看又以 4 字符长度骗过 <=3 字残句检测
        _raw_t = str(ch_clean.get("text") or "").lstrip("：；，、,;:。！？!? ")
        if _raw_t:
            ch_clean["text"] = _raw_t
        pre_chunks.append(ch_clean)

    # 先把孤立序数词/起句连接短块（「第一」「第二」「第三」）向右并入所属短句，严禁向左粘到前句末尾
    i_ord = 0
    while i_ord < len(pre_chunks) - 1:
        cur_o = pre_chunks[i_ord]
        nxt_o = pre_chunks[i_ord + 1]
        cur_o_plain = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(cur_o.get("text") or ""))
        if _ORDINAL_SHORT_SENT_RE.match(cur_o_plain) and _clean_char_len(cur_o.get("text") or "") < min_chars:
            gap_o = float(nxt_o["start_sec"]) - float(cur_o["end_sec"])
            m_ord_txt = _join_chunk_texts(str(cur_o.get("text") or ""), str(nxt_o.get("text") or ""))
            if gap_o <= 1.20 and _eff_char_len(m_ord_txt) <= 12 and not _too_wide(m_ord_txt, cap_mult=1.20):
                nxt_o["text"] = m_ord_txt
                nxt_o["start_sec"] = min(float(cur_o["start_sec"]), float(nxt_o["start_sec"]))
                nxt_o["words"] = list(cur_o.get("words") or []) + list(nxt_o.get("words") or [])
                nxt_o["keywords"] = list(dict.fromkeys(list(cur_o.get("keywords") or []) + list(nxt_o.get("keywords") or [])))
                pre_chunks.pop(i_ord)
                continue
        i_ord += 1

    out: List[Dict[str, Any]] = []
    for ch_clean in pre_chunks:
        ch_t = str(ch_clean.get("text") or "")
        if out and _clean_char_len(ch_t) < min_chars and not _is_ordinal_or_clause_starter(ch_t):
            prev = out[-1]
            gap = float(ch_clean["start_sec"]) - float(prev["end_sec"])
            merged_txt = _join_chunk_texts(str(prev.get("text") or ""), ch_t)
            if gap <= 0.50 and not _too_wide(merged_txt) and not _creates_ordinal_tail(merged_txt):
                prev["text"] = merged_txt
                prev["end_sec"] = max(float(prev["end_sec"]), float(ch_clean["end_sec"]))
                prev["words"] = list(prev.get("words") or []) + list(ch_clean.get("words") or [])
                prev["keywords"] = list(dict.fromkeys(list(prev.get("keywords") or []) + list(ch_clean.get("keywords") or [])))
                continue
        out.append(ch_clean)
    # 并不到一起的残块：按**完整语义词元**从邻块借词，绝不按单字劈开双字词或保护词（「好|傻」「两百|平」「桑拿|鸡」）
    if len(out) >= 2:
        out = _rebalance_short_chunks(out, min_chars, max_px, font_path, font_size, protected_terms=protected_terms)
    # 头一块太短（第一块就只剩两个字）并到后一块
    if len(out) >= 2 and _clean_char_len(out[0].get("text") or "") < min_chars:
        first, second = out[0], out[1]
        merged_txt = _join_chunk_texts(str(first.get("text") or ""), str(second.get("text") or ""))
        if float(second["start_sec"]) - float(first["end_sec"]) <= 0.50 and _eff_char_len(merged_txt) <= 12 and not _too_wide(merged_txt):
            second["text"] = merged_txt
            second["start_sec"] = min(float(first["start_sec"]), float(second["start_sec"]))
            second["words"] = list(first.get("words") or []) + list(second.get("words") or [])
            second["keywords"] = list(dict.fromkeys(list(first.get("keywords") or []) + list(second.get("keywords") or [])))
            out.pop(0)
    # 句内扫尾兜底：若仍留有 <=3 字残块（如句内呼吸间隔 >0.50s 跳过上面分支），优先并入更短的相邻块（放宽到句内 gap<=1.20s，绝不留单挂碎字）
    changed = True
    while changed and len(out) >= 2:
        changed = False
        for k in range(len(out)):
            cur_k_txt = str(out[k].get("text") or "")
            if _clean_char_len(cur_k_txt) >= min_chars:
                continue
            cur_is_starter = _is_ordinal_or_clause_starter(cur_k_txt)
            cand_neighbors: List[Tuple[int, int]] = []
            if k - 1 >= 0 and not cur_is_starter:
                gap_prev = float(out[k]["start_sec"]) - float(out[k - 1]["end_sec"])
                if gap_prev <= 1.20:
                    cand_neighbors.append((_clean_char_len(out[k - 1].get("text") or ""), k - 1))
            if k + 1 < len(out):
                gap_next = float(out[k + 1]["start_sec"]) - float(out[k]["end_sec"])
                if gap_next <= 1.20:
                    cand_neighbors.append((_clean_char_len(out[k + 1].get("text") or ""), k + 1))
            merged_in_sweep = False
            for _, nb_i in sorted(cand_neighbors):
                lo, hi = (nb_i, k) if nb_i < k else (k, nb_i)
                m_txt = _join_chunk_texts(str(out[lo].get("text") or ""), str(out[hi].get("text") or ""))
                if _eff_char_len(m_txt) <= 12 and not _too_wide(m_txt) and not _creates_ordinal_tail(m_txt):
                    out[lo]["text"] = m_txt
                    out[lo]["end_sec"] = max(float(out[lo]["end_sec"]), float(out[hi]["end_sec"]))
                    out[lo]["words"] = list(out[lo].get("words") or []) + list(out[hi].get("words") or [])
                    out[lo]["keywords"] = list(dict.fromkeys(list(out[lo].get("keywords") or []) + list(out[hi].get("keywords") or [])))
                    out.pop(hi)
                    changed = True
                    merged_in_sweep = True
                    break
            if not merged_in_sweep and cand_neighbors:
                # 兜底：若 2~3 字残块按严格 max_px 左右均无法合并，允许 20% 宽容度并优先保住语义边界（如「重做了」向右并入宾语）；
                # 若邻块为受保护的多词 ASCII 品牌且合并后仅多出一个 <=3 字短尾，放宽字数与安全宽容度以免孤立残句；
                # 但若该邻块本身已是未闭合的半截多词品牌（如「Microsoft Azure | Functions」），拒绝再膨胀，改走词间空格均衡切分。
                fallback_cands: List[Tuple[int, int, int, int, int]] = []
                for nb_len, nb_i in cand_neighbors:
                    lo, hi = (nb_i, k) if nb_i < k else (k, nb_i)
                    m_txt = _join_chunk_texts(str(out[lo].get("text") or ""), str(out[hi].get("text") or ""))
                    if _creates_ordinal_tail(m_txt):
                        continue
                    nb_txt_raw = str(out[nb_i].get("text") or "")
                    splits_adj_brand = False
                    if nb_i > k and nb_i + 1 < len(out):
                        nxt_after = str(out[nb_i + 1].get("text") or "")
                        if nb_txt_raw and nxt_after and nb_txt_raw[-1].isascii() and nb_txt_raw[-1].isalnum() and nxt_after[0].isascii() and nxt_after[0].isalnum():
                            splits_adj_brand = True
                    elif nb_i < k and nb_i - 1 >= 0:
                        prev_before = str(out[nb_i - 1].get("text") or "")
                        if prev_before and nb_txt_raw and prev_before[-1].isascii() and prev_before[-1].isalnum() and nb_txt_raw[0].isascii() and nb_txt_raw[0].isalnum():
                            splits_adj_brand = True
                    has_ascii_brand = (not splits_adj_brand) and (
                        bool(_MULTI_WORD_ASCII_BRAND_RE.search(m_txt))
                        or sum(1 for ch in m_txt if ch.isascii() and ch.isalnum()) >= 8
                    )
                    tier = -1
                    if not (splits_adj_brand and _too_wide(m_txt)) and _eff_char_len(m_txt) <= 12 and not _too_wide(m_txt, cap_mult=1.20):
                        tier = 0
                    elif has_ascii_brand and _eff_char_len(m_txt) <= 16 and not _too_wide(m_txt, cap_mult=1.20):
                        tier = 1
                    elif has_ascii_brand and _eff_char_len(m_txt) <= 16 and not _too_wide(m_txt, cap_mult=1.30):
                        tier = 2
                    if tier >= 0:
                        bad_tail = 1 if (m_txt and m_txt[-1] in {"的", "嘅", "了", "咗", "着", "过"}) else 0
                        prefer_right = 0 if nb_i > k else 1
                        fallback_cands.append((tier, bad_tail, prefer_right, nb_len, nb_i))
                if fallback_cands:
                    _, _, _, _, nb_i = min(fallback_cands)
                    lo, hi = (nb_i, k) if nb_i < k else (k, nb_i)
                    m_txt = _join_chunk_texts(str(out[lo].get("text") or ""), str(out[hi].get("text") or ""))
                    out[lo]["text"] = m_txt
                    out[lo]["end_sec"] = max(float(out[lo]["end_sec"]), float(out[hi]["end_sec"]))
                    out[lo]["words"] = list(out[lo].get("words") or []) + list(out[hi].get("words") or [])
                    out[lo]["keywords"] = list(dict.fromkeys(list(out[lo].get("keywords") or []) + list(out[hi].get("keywords") or [])))
                    out.pop(hi)
                    changed = True
                else:
                    # 品牌词间空格再平衡：当整段多词英文品牌与残块连写必超宽时，在品牌词间空格处均衡切分，
                    # 确保双行均落在 max_px 之内且零 <=3 字残句（如「我们用Google | Cloud Run做渲染」）。
                    _sp_no_tail = frozenset("的嘅地得之把被从向给俾在喺这那呢嗰𠮶了咗着过")
                    best_brand_split: Optional[Tuple[int, int, int, int, str, str]] = None
                    for _, nb_i in cand_neighbors:
                        nb_txt_s = str(out[nb_i].get("text") or "")
                        if not nb_txt_s:
                            continue
                        for m_sp in re.finditer(r"(?<=[A-Za-z0-9])\s+(?=[A-Za-z0-9])", nb_txt_s):
                            if nb_i < k:
                                l_cand = nb_txt_s[: m_sp.start()].rstrip()
                                r_head = nb_txt_s[m_sp.end() :].lstrip()
                                r_cand = (r_head + (" " if (r_head and cur_k_txt and r_head[-1].isascii() and cur_k_txt[0].isascii()) else "") + cur_k_txt).strip()
                            else:
                                l_tail = nb_txt_s[: m_sp.start()].rstrip()
                                l_cand = (cur_k_txt + (" " if (cur_k_txt and l_tail and cur_k_txt[-1].isascii() and l_tail[0].isascii()) else "") + l_tail).strip()
                                r_cand = nb_txt_s[m_sp.end() :].lstrip()
                            if _clean_char_len(l_cand) < min_chars or _clean_char_len(r_cand) < min_chars:
                                continue
                            if _too_wide(l_cand) or _too_wide(r_cand):
                                continue
                            if _creates_ordinal_tail(l_cand) or _creates_ordinal_tail(r_cand):
                                continue
                            if l_cand[-1] in _sp_no_tail or r_cand[0] in _BAD_SUB_HEAD_AUX:
                                continue
                            wl, _ = _measure_text_px(l_cand, font_path, font_size)
                            wr, _ = _measure_text_px(r_cand, font_path, font_size)
                            cand_key = (abs(wl - wr), max(wl, wr), nb_i, m_sp.start(), m_sp.end(), l_cand, r_cand)
                            if best_brand_split is None or cand_key[:2] < best_brand_split[:2]:
                                best_brand_split = cand_key
                    if best_brand_split is not None:
                        _, _, nb_i, sp_s, sp_e, l_cand, r_cand = best_brand_split
                        lo, hi = (nb_i, k) if nb_i < k else (k, nb_i)
                        nb_obj = out[nb_i]
                        cur_obj = out[k]
                        nb_ws = [dict(w) for w in (nb_obj.get("words") or [])]
                        cur_ws = [dict(w) for w in (cur_obj.get("words") or [])]
                        nb_s0 = float(nb_obj["start_sec"])
                        nb_e0 = float(nb_obj["end_sec"])
                        nb_l_ws: List[Dict[str, Any]] = []
                        nb_r_ws: List[Dict[str, Any]] = []
                        if nb_ws:
                            acc_pos = 0
                            for wi, w_it in enumerate(nb_ws):
                                wt_s = str(w_it.get("word") or "")
                                w_end_pos = acc_pos + len(wt_s)
                                if w_end_pos <= sp_e and w_end_pos >= sp_s:
                                    nb_l_ws = [dict(x) for x in nb_ws[: wi + 1]]
                                    nb_r_ws = [dict(x) for x in nb_ws[wi + 1 :]]
                                    break
                                if acc_pos < sp_s < w_end_pos:
                                    cut_local = sp_s - acc_pos
                                    cut_r_local = sp_e - acc_pos
                                    ws_t = float(w_it.get("start_sec", nb_s0))
                                    we_t = float(w_it.get("end_sec", nb_e0))
                                    mid_t = round(ws_t + (we_t - ws_t) * (cut_local / max(1, len(wt_s))), 4)
                                    w_l_part = dict(w_it, word=wt_s[:cut_local], end_sec=mid_t)
                                    w_r_part = dict(w_it, word=wt_s[cut_r_local:], start_sec=mid_t)
                                    nb_l_ws = [dict(x) for x in nb_ws[:wi]] + ([w_l_part] if w_l_part["word"] else [])
                                    nb_r_ws = ([w_r_part] if w_r_part["word"] else []) + [dict(x) for x in nb_ws[wi + 1 :]]
                                    break
                                acc_pos = w_end_pos
                        if nb_i < k:
                            new_l_ws = nb_l_ws
                            new_r_ws = nb_r_ws + cur_ws
                            if new_l_ws and new_r_ws:
                                if str(new_l_ws[-1].get("word") or "").endswith(" "):
                                    new_l_ws[-1]["word"] = str(new_l_ws[-1]["word"]).rstrip()
                                l_st, l_en = float(new_l_ws[0]["start_sec"]), float(new_l_ws[-1]["end_sec"])
                                r_st, r_en = max(l_en, float(new_r_ws[0]["start_sec"])), float(new_r_ws[-1]["end_sec"])
                            else:
                                nb_txt_len = max(1, _clean_char_len(nb_obj.get("text") or ""))
                                kept_ratio = max(0.15, min(0.85, _clean_char_len(l_cand) / float(nb_txt_len)))
                                split_t = round(nb_s0 + (nb_e0 - nb_s0) * kept_ratio, 4)
                                l_st, l_en = nb_s0, split_t
                                r_st, r_en = split_t, float(cur_obj["end_sec"])
                        else:
                            new_l_ws = cur_ws + nb_l_ws
                            new_r_ws = nb_r_ws
                            if new_l_ws and new_r_ws:
                                if str(new_l_ws[-1].get("word") or "").endswith(" "):
                                    new_l_ws[-1]["word"] = str(new_l_ws[-1]["word"]).rstrip()
                                l_st, l_en = float(new_l_ws[0]["start_sec"]), float(new_l_ws[-1]["end_sec"])
                                r_st, r_en = max(l_en, float(new_r_ws[0]["start_sec"])), float(new_r_ws[-1]["end_sec"])
                            else:
                                nb_txt_len = max(1, _clean_char_len(nb_obj.get("text") or ""))
                                mv_len = _clean_char_len(nb_obj.get("text") or "") - _clean_char_len(r_cand)
                                mv_ratio = max(0.15, min(0.85, mv_len / float(nb_txt_len)))
                                split_t = round(nb_s0 + (nb_e0 - nb_s0) * mv_ratio, 4)
                                l_st, l_en = float(cur_obj["start_sec"]), split_t
                                r_st, r_en = split_t, nb_e0
                        merged_kws = list(dict.fromkeys(list(out[lo].get("keywords") or []) + list(out[hi].get("keywords") or [])))
                        out[lo] = {
                            "text": l_cand,
                            "start_sec": round(l_st, 4),
                            "end_sec": round(max(l_st + 0.08, l_en), 4),
                            "words": new_l_ws,
                            "keywords": merged_kws,
                        }
                        out[hi] = {
                            "text": r_cand,
                            "start_sec": round(max(out[lo]["end_sec"], r_st), 4),
                            "end_sec": round(max(max(out[lo]["end_sec"], r_st) + 0.08, r_en), 4),
                            "words": new_r_ws,
                            "keywords": merged_kws,
                        }
                        changed = True
                    else:
                        # 局部双块语义重切兜底：当 <=3 字残句因邻块末尾为「的/嘅」无法借 1 词且合并不进安全宽时，
                        # 沿两块联合文本的完整语义词边界寻找双侧均 >=min_chars 且 <=max_px 的最优二分点
                        # （如「开咗一间三百平嘅手工」+「点心铺」->「开咗一间｜三百平嘅手工点心铺」）。
                        _resplit_no_tail = frozenset("的嘅地得之把被从向给俾在喺这那呢嗰𠮶")
                        _resplit_soft_tail = frozenset("了咗着过")
                        _best_resplit: Optional[Tuple[Tuple[int, int, int], int, int, str, str]] = None
                        for _, nb_i in cand_neighbors:
                            lo, hi = (nb_i, k) if nb_i < k else (k, nb_i)
                            l_obj, r_obj = out[lo], out[hi]
                            comb_txt = _join_chunk_texts(str(l_obj.get("text") or ""), str(r_obj.get("text") or ""))
                            if not comb_txt or len(comb_txt) > 22:
                                continue
                            toks_c = tokenize_semantic_words(comb_txt, terms=protected_terms)
                            acc_pos_c = 0
                            for tk_c in toks_c[:-1]:
                                acc_pos_c += len(tk_c)
                                l_new = comb_txt[:acc_pos_c].strip()
                                r_new = comb_txt[acc_pos_c:].strip()
                                if (
                                    _clean_char_len(l_new) < min_chars
                                    or _clean_char_len(r_new) < min_chars
                                    or _too_wide(l_new)
                                    or _too_wide(r_new)
                                    or _creates_ordinal_tail(l_new)
                                    or _creates_ordinal_tail(r_new)
                                    or (l_new and l_new[-1] in _resplit_no_tail)
                                    or (r_new and r_new[0] in _BAD_SUB_HEAD_AUX and not (r_new[0] == "呢" and _is_cantonese_ne_demo_head(r_new)))
                                    or _has_trailing_subject_pronoun_split(l_new, r_new)
                                ):
                                    continue
                                pen_tail = 1 if (l_new.endswith(("几个", "一间", "一份", "一种")) or l_new[-1] in _resplit_soft_tail) else 0
                                score_c = (pen_tail, abs(_clean_char_len(l_new) - _clean_char_len(r_new)), _eff_char_len(r_new))
                                if _best_resplit is None or score_c < _best_resplit[0]:
                                    _best_resplit = (score_c, lo, hi, l_new, r_new)
                        if _best_resplit is not None:
                            _, lo, hi, l_new, r_new = _best_resplit
                            l_obj, r_obj = out[lo], out[hi]
                            comb_txt = _join_chunk_texts(str(l_obj.get("text") or ""), str(r_obj.get("text") or ""))
                            comb_ws = [dict(w) for w in (l_obj.get("words") or [])] + [dict(w) for w in (r_obj.get("words") or [])]
                            l_s0 = float(l_obj["start_sec"])
                            r_e0 = float(r_obj["end_sec"])
                            new_l_ws: List[Dict[str, Any]] = []
                            new_r_ws: List[Dict[str, Any]] = []
                            cut_c = len(l_new.replace(" ", ""))
                            if comb_ws and sum(len(str(w.get("word") or "").replace(" ", "")) for w in comb_ws) == len(comb_txt.replace(" ", "")):
                                acc_c = 0
                                split_ok = False
                                for wi, w_it in enumerate(comb_ws):
                                    wn = len(str(w_it.get("word") or "").replace(" ", ""))
                                    acc_c += wn
                                    if acc_c == cut_c:
                                        new_l_ws = [dict(x) for x in comb_ws[: wi + 1]]
                                        new_r_ws = [dict(x) for x in comb_ws[wi + 1 :]]
                                        split_ok = bool(new_l_ws and new_r_ws)
                                        break
                                if not split_ok:
                                    continue
                                l_st, l_en = float(new_l_ws[0]["start_sec"]), float(new_l_ws[-1]["end_sec"])
                                r_st, r_en = max(l_en, float(new_r_ws[0]["start_sec"])), float(new_r_ws[-1]["end_sec"])
                            else:
                                ratio_c = max(0.2, min(0.8, cut_c / float(max(1, len(comb_txt.replace(" ", ""))))))
                                split_t = round(l_s0 + (r_e0 - l_s0) * ratio_c, 4)
                                l_st, l_en = l_s0, split_t
                                r_st, r_en = split_t, r_e0
                            merged_kws = list(dict.fromkeys(list(l_obj.get("keywords") or []) + list(r_obj.get("keywords") or [])))
                            out[lo] = {
                                "text": l_new,
                                "start_sec": round(l_st, 4),
                                "end_sec": round(max(l_st + 0.08, l_en), 4),
                                "words": new_l_ws,
                                "keywords": merged_kws,
                            }
                            out[hi] = {
                                "text": r_new,
                                "start_sec": round(max(out[lo]["end_sec"], r_st), 4),
                                "end_sec": round(max(max(out[lo]["end_sec"], r_st) + 0.08, r_en), 4),
                                "words": new_r_ws,
                                "keywords": merged_kws,
                            }
                            changed = True
            if changed:
                break
    return out


def _rebalance_short_chunks(
    chunks: List[Dict[str, Any]],
    min_chars: int,
    max_px: Optional[int],
    font_path: Optional[str],
    font_size: int,
    protected_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """不足 min_chars 的残句：按**完整语义词/保护词**向邻块借词，严禁按单字从词中间劈半。"""
    def _chars(txt: str) -> int:
        return len(re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(txt or "")))

    def _eff_chars(txt: str) -> int:
        compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(txt or ""))
        ascii_n = sum(1 for ch in compact if ch.isascii())
        cjk_n = len(compact) - ascii_n
        return (cjk_n + math.ceil(ascii_n * 0.55)) if ascii_n >= 4 else len(compact)

    def _too_wide(txt: str, cap_mult: float = 1.0) -> bool:
        if not max_px:
            return False
        w, _ = _measure_text_px(str(txt or ""), font_path, font_size)
        return w > int(round(int(max_px) * cap_mult))

    def _creates_ordinal_tail(txt: str) -> bool:
        plain = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(txt or ""))
        return len(plain) >= 4 and bool(re.search(r"(第[一二三四五六七八九十两\d]+)$", plain))

    def _terms_with_brands(text_s: str) -> List[str]:
        brands = [m.group(0) for m in _MULTI_WORD_ASCII_BRAND_RE.finditer(str(text_s or "")) if len(m.group(0)) <= 20]
        return list(dict.fromkeys(brands + list(protected_terms or []) + list(_UNIVERSAL_SPOKEN_LEXICON) + list(_UNIVERSAL_PUBLIC_PLATFORMS)))

    _rebal_no_tail = frozenset("的嘅地得之把被从向给俾在喺这那呢嗰𠮶最很太极更不没唔")

    def _word_spans(nb_words: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
        """把 nb_words 按语义分词划分成不可再拆的 token 索引区间 [(s_i, e_i), ...]。"""
        joined = "".join(str(w.get("word") or "") for w in nb_words)
        if not joined or len(joined) != sum(len(str(w.get("word") or "")) for w in nb_words):
            return [(i, i + 1) for i in range(len(nb_words))]
        char_to_wi: List[int] = []
        for wi, w in enumerate(nb_words):
            for _ in str(w.get("word") or ""):
                char_to_wi.append(wi)
        toks = tokenize_semantic_words(joined, terms=_terms_with_brands(joined))
        spans: List[Tuple[int, int]] = []
        pos = 0
        for tk in toks:
            n_c = len(tk)
            if n_c <= 0 or pos >= len(char_to_wi):
                continue
            w0 = char_to_wi[pos]
            w1 = char_to_wi[min(len(char_to_wi) - 1, pos + n_c - 1)] + 1
            if spans and w0 < spans[-1][1]:
                w0 = spans[-1][1]
            if w1 > w0:
                spans.append((w0, w1))
            pos += n_c
        if not spans:
            return [(i, i + 1) for i in range(len(nb_words))]
        return spans

    def _text_tokens_with_brands(text_s: str) -> List[str]:
        raw_t = str(text_s or "")
        if not raw_t:
            return []
        brand_spans: List[Tuple[int, int]] = []
        for m in _MULTI_WORD_ASCII_BRAND_RE.finditer(raw_t):
            if len(m.group(0)) <= 20:
                brand_spans.append((m.start(), m.end()))
        if not brand_spans:
            return tokenize_semantic_words(raw_t, terms=_terms_with_brands(raw_t))
        out_t: List[str] = []
        last = 0
        eff_t = _terms_with_brands(raw_t)
        for bs, be in brand_spans:
            if bs > last:
                out_t.extend(tokenize_semantic_words(raw_t[last:bs], terms=eff_t))
            out_t.append(raw_t[bs:be])
            last = be
        if last < len(raw_t):
            out_t.extend(tokenize_semantic_words(raw_t[last:], terms=eff_t))
        return [tk for tk in out_t if tk]

    for i in range(len(chunks)):
        cur = chunks[i]
        while _chars(cur.get("text")) < min_chars:
            moved = False
            cur_plain_chk = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(cur.get("text") or ""))
            cur_is_starter = bool(_ORDINAL_SHORT_SENT_RE.match(cur_plain_chk) or _CLAUSE_START_PROTECT_RE.match(cur_plain_chk))
            for j, take_from_head in ((i - 1, False), (i + 1, True)):
                if not (0 <= j < len(chunks)):
                    continue
                if cur_is_starter and not take_from_head:
                    continue
                nb = chunks[j]
                nb_words = list(nb.get("words") or [])
                cur_words = list(cur.get("words") or [])
                if nb_words and cur_words:
                    w_spans = _word_spans(nb_words)
                    if len(w_spans) <= 1:
                        continue
                    s_idx, e_idx = w_spans[0] if take_from_head else w_spans[-1]
                    donor_slice = nb_words[s_idx:e_idx]
                    cand_nb_words = nb_words[e_idx:] if take_from_head else nb_words[:s_idx]
                    cand_cur_words = cur_words + donor_slice if take_from_head else donor_slice + cur_words
                    cand_nb_text = "".join(str(w.get("word") or "") for w in cand_nb_words)
                    cand_cur_text = "".join(str(w.get("word") or "") for w in cand_cur_words)
                else:
                    nb_txt = str(nb.get("text") or "")
                    cur_txt = str(cur.get("text") or "")
                    txt_toks = _text_tokens_with_brands(nb_txt)
                    while txt_toks and not txt_toks[0].strip():
                        txt_toks.pop(0)
                    while txt_toks and not txt_toks[-1].strip():
                        txt_toks.pop()
                    if len(txt_toks) <= 1:
                        continue
                    if take_from_head:
                        donor_t = txt_toks[0]
                        cand_nb_text = nb_txt[len(donor_t):].lstrip()
                        cand_cur_text = (cur_txt + " " + donor_t) if (cur_txt and donor_t and cur_txt[-1].isascii() and donor_t[0].isascii()) else (cur_txt + donor_t)
                    else:
                        donor_t = txt_toks[-1]
                        cand_nb_text = nb_txt[:len(nb_txt) - len(donor_t)].rstrip()
                        cand_cur_text = (donor_t + " " + cur_txt) if (donor_t and cur_txt and donor_t[-1].isascii() and cur_txt[0].isascii()) else (donor_t + cur_txt)
                    donor_slice = []
                    cand_nb_words = []
                    cand_cur_words = []
                if _chars(cand_nb_text) < min_chars:
                    continue
                if _too_wide(cand_nb_text) or _too_wide(cand_cur_text):
                    continue
                if _creates_ordinal_tail(cand_nb_text) or _creates_ordinal_tail(cand_cur_text):
                    continue
                # 借词后绝不允许制造新的行首助词或行尾挂尾虚词（「在/的/嘅/这/那」），豁免粤语指示代词「呢度/呢批/...」
                if cand_nb_text and cand_nb_text[0] in _BAD_SUB_HEAD_AUX and not (cand_nb_text[0] == "呢" and _is_cantonese_ne_demo_head(cand_nb_text)):
                    continue
                if cand_cur_text and cand_cur_text[0] in _BAD_SUB_HEAD_AUX and not (cand_cur_text[0] == "呢" and _is_cantonese_ne_demo_head(cand_cur_text)):
                    continue
                if take_from_head and cand_cur_text and (
                    cand_cur_text[-1] in _rebal_no_tail
                    or cand_cur_text[-1] in {"了", "咗", "着", "过"}
                    or _has_trailing_subject_pronoun_split(cand_cur_text, cand_nb_text)
                ):
                    continue
                if (not take_from_head) and cand_nb_text and (
                    cand_nb_text[-1] in _rebal_no_tail
                    or cand_nb_text[-1] in {"了", "咗", "着", "过"}
                    or _has_trailing_subject_pronoun_split(cand_nb_text, cand_cur_text)
                ):
                    continue
                if nb_words and cur_words:
                    nb["words"] = cand_nb_words
                    nb["text"] = cand_nb_text
                    cur["words"] = cand_cur_words
                    cur["text"] = cand_cur_text
                    if take_from_head:
                        nb["start_sec"] = float(cand_nb_words[0]["start_sec"])
                        cur["end_sec"] = float(donor_slice[-1]["end_sec"])
                    else:
                        nb["end_sec"] = float(cand_nb_words[-1]["end_sec"])
                        cur["start_sec"] = float(donor_slice[0]["start_sec"])
                else:
                    nb_s0, nb_e0 = float(nb["start_sec"]), float(nb["end_sec"])
                    old_len = max(1, len(str(nb.get("text") or "")))
                    kept_ratio = max(0.2, min(0.85, len(cand_nb_text) / float(old_len)))
                    nb["text"] = cand_nb_text
                    cur["text"] = cand_cur_text
                    if take_from_head:
                        split_t = round(nb_s0 + (nb_e0 - nb_s0) * (1.0 - kept_ratio), 4)
                        nb["start_sec"] = split_t
                        cur["end_sec"] = split_t
                    else:
                        split_t = round(nb_s0 + (nb_e0 - nb_s0) * kept_ratio, 4)
                        nb["end_sec"] = split_t
                        cur["start_sec"] = split_t
                moved = True
                break
            if not moved:
                # 借整词会导致邻块变碎或超宽时：若「当前残块+相邻整块」合起来仍在行宽安全带内且字数 <= 9，直接合并成一句
                for j, on_left in ((i - 1, True), (i + 1, False)):
                    if not (0 <= j < len(chunks)):
                        continue
                    if cur_is_starter and on_left:
                        continue
                    nb = chunks[j]
                    nb_words = list(nb.get("words") or [])
                    cur_words = list(cur.get("words") or [])
                    if nb_words and cur_words:
                        merged_words = (nb_words + cur_words) if on_left else (cur_words + nb_words)
                        merged_txt = "".join(str(w.get("word") or "") for w in merged_words)
                        if _eff_chars(merged_txt) <= 9 and not _too_wide(merged_txt) and not _creates_ordinal_tail(merged_txt):
                            nb["words"] = merged_words
                            nb["text"] = merged_txt
                            nb["start_sec"] = float(merged_words[0]["start_sec"])
                            nb["end_sec"] = float(merged_words[-1]["end_sec"])
                            cur["words"] = []
                            cur["text"] = ""
                            moved = True
                            break
                    else:
                        merged_txt = (
                            _join_chunk_texts(str(nb.get("text") or ""), str(cur.get("text") or ""))
                            if on_left
                            else _join_chunk_texts(str(cur.get("text") or ""), str(nb.get("text") or ""))
                        )
                        if _eff_chars(merged_txt) <= 9 and not _too_wide(merged_txt) and not _creates_ordinal_tail(merged_txt):
                            nb["text"] = merged_txt
                            nb["start_sec"] = min(float(nb["start_sec"]), float(cur["start_sec"]))
                            nb["end_sec"] = max(float(nb["end_sec"]), float(cur["end_sec"]))
                            cur["words"] = []
                            cur["text"] = ""
                            moved = True
                            break
                break
    return [c for c in chunks if str(c.get("text") or "").strip()]


def _confirm_chunk_text(chunk: Dict[str, Any], sent_text: str, cursor: int = 0) -> Tuple[Dict[str, Any], int]:
    """把 ASR 听错的字换成用户核过的成稿用字（只换字，不动时间轴）。

    例：ASR 把「四大天王」听成「四大轩王」，词表上屏就会印错。成稿是用户核过的，
    同一长度窗口里模糊匹配上就采信成稿的字形；匹配不上就不动（怕是另一句话）。
    """
    txt = str(chunk.get("text") or "")
    base = str(sent_text or "")
    n = len(txt.replace(" ", ""))
    if not base or n == 0 or n > len(base):
        return chunk, cursor
    import difflib

    best_ratio, best, best_at = 0.0, "", cursor
    for i in range(max(0, cursor), len(base) - n + 1):
        win = base[i : i + n]
        if len(win) != n:
            continue
        r = difflib.SequenceMatcher(None, win, txt).ratio()
        if r > best_ratio:
            best_ratio, best, best_at = r, win, i
    if best_ratio >= CONFIRM_MATCH_RATIO and best and best != txt:
        chunk = dict(chunk)
        chunk["text"] = best
    return chunk, best_at + (n if best else 0)

_CLAUSE_PRED_VERBS = frozenset({
    "决定", "开始", "停止", "准备", "计划", "打算", "希望", "想要",
    "觉得", "认为", "以为", "发现", "知道", "看到", "听到", "变成",
    "成为", "转向", "负责", "导致", "造成", "引起", "要求", "需要",
    "允许", "禁止", "出现", "发生", "继续", "完成", "结束",
    "推出", "演示", "丢失", "调整", "放弃", "投诉", "提出", "提交",
    "发布", "展示", "扩大", "生成", "讲解", "检查", "启动", "引发",
    "失去", "拿到", "找到", "买到", "收到", "拒绝", "接受", "同意",
    "反对", "支持", "批评", "表扬", "破坏", "改变", "影响", "打破",
    "超过", "低于", "缺少", "缺乏", "消耗", "占用", "浪费", "损坏",
    "解决", "产生", "带来", "关闭", "排队", "打卡", "质疑",
    "提升", "提高", "降低", "增加", "减少", "改善",
    "重启", "升级", "接入", "部署", "修复", "安装", "更换", "搭建",
    "调试", "维护", "备份", "迁移", "重置", "排查", "恢复",
})

_AGENT_ROLE_HEADS = frozenset({
    "老板", "员工", "客户", "顾客", "用户", "教练", "老师", "医生",
    "司机", "厨师", "主播", "商家", "店家", "店长", "经理", "主管",
    "同事", "朋友", "伙伴", "团队", "选手", "观众", "粉丝", "网友",
    "买家", "卖家", "房东", "租客", "股东", "合伙人", "负责人", "创始人",
    "大家",
})
_SPEC_MOD_HEAD_PREFIXES = (
    "全自动", "半自动", "智能", "双核", "多核", "单核", "高速", "超清",
    "高清", "无线", "有线", "便携", "工业", "商用", "家用", "通用",
    "专用", "新型", "新款", "一体", "立式", "台式", "自动", "电动",
    "手动", "液冷", "风冷", "红外", "激光", "视觉", "语音", "数字",
    "双向", "双通道", "分布式", "模块化",
)
_NOMINAL_TECH_TAIL_CHARS = frozenset(
    "统台具备卡口机器械仪屏线板芯座架条盘心站馆厂库室厅铺坊档摊阁轩"
)
_NP_HEAD_NOUN_SUFFIXES = _NOMINAL_TECH_TAIL_CHARS | frozenset("件品案式构型网")
_TOOL_SYSTEM_HEAD_WORDS = frozenset({
    "系统", "平台", "工具", "设备", "软件", "硬件", "显卡", "机器", "仪器", "装置",
    "方案", "制度", "流程", "表格", "机制", "体系", "架构", "模块", "接口", "引擎", "模型",
})
_ADV_MOD_WORDS = frozenset({
    "自动", "手动", "直接", "立刻", "马上", "重新", "正式", "提前", "陆续", "批量", "统一", "紧急", "迅速", "快速",
})
_MIS_TAGGED_PRED_VERBS = frozenset({"清点", "盘点", "核对", "清理"})
_COMPLETE_CLAUSE_LE_TAILS = (
    "出事了", "注意了", "崩了", "火了", "说清楚了", "讲清楚了", "写清楚了",
    "亏本了", "倒闭了", "结束了", "完成了", "成功了", "失败了",
    "崩溃了", "宕机了", "死机了", "掉线了", "卡住了", "卡顿了", "报错了",
    "超时了", "停电了", "断货了", "堵车了", "爆单了", "到货了", "出故障了",
)
_DEGREE_MOD_WORDS = frozenset({
    "很", "非常", "特别", "十分", "挺", "蛮", "真", "太", "超", "超级",
    "更", "最", "极其", "极为", "相当", "比较", "有点", "有些", "有点儿",
    "好", "真系", "实在", "确实", "真的", "的确",
})
_CLAUSE_BOUND_SPLIT_RE = re.compile(
    r"(?<!\d)[，。！？、；：,;:!?]|(?<=\d)[，。！？、；：;:!?]|(?<=\d),(?!\d{3}(?!\d))"
)


def _is_completed_le_clause(
    prev_plain: str,
    prot_terms: Optional[Sequence[str]] = None,
) -> bool:
    """结构化判断以「了/咗」结尾的前句是否已是自带完整谓词/宾语/结果补语的小句（而非及物动词+「了」悬空等待宾语）。"""
    _last_clause = _CLAUSE_BOUND_SPLIT_RE.split(str(prev_plain or ""))[-1]
    prev_p = strip_display_punctuation(_last_clause).replace(" ", "")
    if not prev_p or not prev_p.endswith(("了", "咗")):
        return False
    if prev_p.endswith(_COMPLETE_CLAUSE_LE_TAILS):
        return True
    p_pairs = _pseg_cut_clean(prev_p, prot_terms=prot_terms)
    if len(p_pairs) < 2:
        return False
    p_core = list(p_pairs[:-1]) if p_pairs[-1][0] in ("了", "咗") else list(p_pairs)
    if not p_core:
        return False
    # 1) [V + (了/过/着) + N + 了]（如「昨天出故障了」「服务出了问题了」）：动词已在前句自带宾语
    for vi in range(len(p_core) - 1):
        if p_core[vi][1] in ("v", "vn", "vg"):
            if any(
                p_core[ni][1] in ("n", "nr", "ns", "nt", "nz", "vn", "s")
                for ni in range(vi + 1, len(p_core))
            ):
                return True
    # 2) [V + 结果形容词补语 + 了]（如「说清楚了」「跑通了」）
    if len(p_core) >= 2 and p_core[-1][1] in ("a", "ad") and p_core[-2][1] in ("v", "vn", "vg"):
        return True
    # 3) [... + 状态副词(ad) + 谓词 + 了]（如「突然停电了」「彻底断货了」「突然死机了」）
    if len(p_core) >= 2 and p_core[-2][1] == "ad" and p_core[-1][1] in ("v", "vn", "vg", "n", "a"):
        return True
    return False


def _is_v_noun_deg_predicate(clause_plain: str, prot_terms: Optional[Sequence[str]] = None) -> bool:
    """识别 jieba 误标首词为 'v' 的名词主语谓语小句（如「生意很好」「安排很合理」「配合很默契」），区别于「卖得很好」。"""
    cp = strip_display_punctuation(str(clause_plain or "")).replace(" ", "")
    if not cp:
        return False
    pairs = _pseg_cut_clean(cp, prot_terms=prot_terms)
    if len(pairs) < 2 or pairs[0][1] != "v":
        return False
    if any(wp in ("得", "了", "咗", "着", "过", "起来", "下去", "出来") for wp, _ in pairs[1:3]):
        return False
    if pairs[1][0] in _DEGREE_MOD_WORDS or pairs[1][1] in ("d", "zg", "ad"):
        return pairs[-1][1] in ("a", "ad", "an", "z", "vn", "v", "nrt")
    return len(pairs) == 2 and pairs[1][1] in ("a", "ad", "an", "z")


def is_predicative_after_headless_de(
    prev_plain: str,
    nxt_plain: str,
    prot_terms: Optional[Sequence[str]] = None,
) -> bool:
    """判断无头「的/嘅」名物化短句（如「这是我去年买的」「从德国买回来的」）后接的是否为谓词性主谓评价小句（[N + (d/ad) + a/v] / [N-A + 很多]）。"""
    _last_p_clause = _CLAUSE_BOUND_SPLIT_RE.split(str(prev_plain or ""))[-1]
    _first_n_clause = _CLAUSE_BOUND_SPLIT_RE.split(str(nxt_plain or ""), 1)[0]
    p_pl = strip_display_punctuation(_last_p_clause).replace(" ", "")
    n_pl = strip_display_punctuation(_first_n_clause).replace(" ", "")
    if not p_pl or not n_pl or not p_pl.endswith(("的", "嘅")):
        return False
    if p_pl.endswith(("我的", "你的", "他的", "她的", "它的", "咱的", "佢的", "们的")):
        return False
    p_pairs = _pseg_cut_clean(p_pl[:-1], prot_terms=prot_terms)
    if not any(fp in ("v", "vg", "vd", "l") for _wp, fp in p_pairs):
        return False
    n_pairs = _pseg_cut_clean(n_pl, prot_terms=prot_terms)
    if not n_pairs:
        return False
    if any(wp in ("的", "嘅") for wp, _fp in n_pairs) and n_pairs[-1][1] in ("n", "nr", "ns", "nt", "nz", "vn", "s", "l", "j"):
        return False
    last_w, last_f = n_pairs[-1]
    if last_f in ("a", "z", "ad", "an") and len(n_pairs) >= 2 and n_pairs[0][1] in ("n", "nr", "ns", "nt", "nz", "vn", "v"):
        return True
    if (
        len(n_pairs) >= 3
        and n_pairs[0][1] in ("n", "nr", "ns", "nt", "nz", "vn", "v")
        and not any(wp in ("的", "嘅") for wp, _fp in n_pairs)
        and (
            (last_f in ("a", "z", "ad", "an") and any((fp in ("d", "ad", "z") or wp in _DEGREE_MOD_WORDS) for wp, fp in n_pairs[1:-1]))
            or (last_f in ("nz", "vn", "v", "nrt") and any(wp in _DEGREE_MOD_WORDS for wp, _fp in n_pairs[1:-1]))
        )
    ):
        return True
    if last_w in ("很多", "不少", "多了", "得多", "一点", "一些") and len(n_pairs) >= 2:
        prev_w, prev_f = n_pairs[-2]
        if prev_f in ("a", "ad", "z"):
            return True
        if len(prev_w) == 4 and all("\u4e00" <= ch <= "\u9fff" for ch in prev_w):
            sub_l = _pseg_cut_clean(prev_w[:2], prot_terms=prot_terms)
            sub_r = _pseg_cut_clean(prev_w[2:], prot_terms=prot_terms)
            if sub_l and sub_r and sub_l[0][1] in ("n", "vn") and sub_r[0][1] in ("a", "ad", "v"):
                return True
    return False


_ONE_CHAR_NOM_EXCLUDE = (
    _DP_NO_LEAD_CHARS
    | _DP_NO_TAIL_CHARS
    | frozenset("哎唉哼嗯哈哟喂但而或且如若我你他她它佢咱谁啥")
)


_CANTO_X_NON_NOUNS = frozenset("咩囉啫㗎喇嘞吖喎喔喺佢哋啲嘅咗唔睇嚟攞俾揾")
_MAT_OBJ_PREFIXES = ("门", "铁", "木", "铜", "钢", "金", "皮", "布", "纸")
_ABSTRACT_Q_HEADS = frozenset(
    "次遍趟回种类点下个位名件套批份款只度倍成折天年月周岁时分秒元角块斤两克吨升匹节颗粒支根条页行句字词段环步项排户"
)
_NON_AGENT_GONG_WORDS = frozenset({
    "手工", "人工", "做工", "加工", "代工", "施工", "停工",
    "开工", "复工", "返工", "完工", "用工", "精工",
})


def _is_one_char_nominal_object(p_plain: str, ch: str) -> bool:
    """语境识别单字宾语名词（如「货」「菜」「车」「锁」「餸」），同时严格排除助词/叹词/动词/副词/代词。"""
    if not ch or len(ch) != 1 or ch in _ONE_CHAR_NOM_EXCLUDE or ch in _BAD_SUB_HEAD_AUX or ch in _CANTO_X_NON_NOUNS:
        return False
    iso_pairs = _pseg_cut_clean(ch)
    if not iso_pairs:
        return False
    iso_f = _normalize_pos_tag(ch, iso_pairs[0][1])
    if iso_f in ("n", "nr", "ns", "nt", "nz", "vn", "ng"):
        return True
    if iso_f in ("zg", "g", "x"):
        ctx_str = (p_plain[-4:] if p_plain else "买了") + ch
        ctx_pairs = _pseg_cut_clean(ctx_str)
        if ctx_pairs and len(ctx_pairs[-1][0]) == 1:
            ctx_f = _normalize_pos_tag(ctx_pairs[-1][0], ctx_pairs[-1][1])
            if ctx_f in ("n", "nr", "ns", "nt", "nz", "vn", "ng"):
                return True
        if iso_f == "x" and "\u4e00" <= ch <= "\u9fff":
            tok_d, _pt_d = _get_clean_pseg_tokenizer()
            if tok_d.FREQ.get(ch, 0) == 0:
                le_ps = _pseg_cut_clean("买了" + ch)
                if le_ps and le_ps[-1][1] not in ("y", "e", "o", "p", "c", "d", "r", "v", "vg", "vd"):
                    return True
    if iso_f in ("v", "zg", "g"):
        tok_d, pt_d = _get_clean_pseg_tokenizer()
        if any(
            tok_d.FREQ.get(pfx + ch, 0) >= 20
            and pt_d.word_tag_tab.get(pfx + ch, "") in ("n", "ng", "nz")
            for pfx in _MAT_OBJ_PREFIXES
        ):
            return True
    return False


def _is_q_nominal_head(ch: str) -> bool:
    """识别作复合名词中心语的单字容器/器物量词名素（如「制冰盒」「分拣箱」），排除抽象动量/名量词（「次/趟/种/个」）。"""
    if not ch or len(ch) != 1 or ch in _ABSTRACT_Q_HEADS or ch in _ONE_CHAR_NOM_EXCLUDE or ch in _BAD_SUB_HEAD_AUX:
        return False
    if ch in _NOMINAL_TECH_TAIL_CHARS:
        return True
    new_p = _pseg_cut_clean("新" + ch)
    if new_p and new_p[0][0] == "新" + ch and new_p[0][1] in ("n", "ns", "nz"):
        return True
    small_p = _pseg_cut_clean("小" + ch)
    if small_p and small_p[0][0] == "小" + ch and small_p[0][1] == "n":
        return True
    return False


def is_independent_svo_clause(
    text_plain: str,
    prot_terms: Optional[Sequence[str]] = None,
    prev_plain: str = "",
) -> bool:
    """判断无标点短句是否为独立 [主语 + (状语) + 谓语 + (宾语)] 子句（不应跨「了/的」与前句合并）。"""
    c_plain = strip_display_punctuation(str(text_plain or "")).replace(" ", "")
    if not c_plain:
        return False
    c_pairs = [
        (wp, _normalize_pos_tag(wp, fp))
        for wp, fp in _pseg_cut_clean(c_plain, prot_terms=prot_terms)
    ]
    if len(c_pairs) < 2:
        return False
    w0, f0 = c_pairs[0]
    if f0 in ("m", "mq", "q") or _NUM_UNIT_PRESPLIT_RE.match(c_plain):
        return False
    # 排除「施事+动作+的+中心名词」定中结构（如「老板推荐的系统」）
    de_idxs = [idx_p for idx_p, (wp, _fp) in enumerate(c_pairs) if wp in ("的", "嘅")]
    if (
        de_idxs
        and c_pairs[-1][1] in ("n", "nr", "ns", "nt", "nz", "vn", "s", "l", "j")
        and not any(fp in ("p", "v", "vg", "vd") for _wp, fp in c_pairs[de_idxs[-1] + 1 :])
    ):
        return False

    # 1) 施事主语（人称代词/人称角色/职业称谓）+ 谓语动词或状语起句（「老客户直接」「我们利润才稳得住」）
    is_agent = (
        f0 == "nr"
        or w0 in _AGENT_ROLE_HEADS
        or w0 in _PERSONAL_CLAUSE_PRONOUNS
        or any(w0.endswith(r) for r in _AGENT_ROLE_HEADS if len(r) >= 2)
        or (
            len(w0) >= 2
            and f0 in ("n", "vn", "nz", "j")
            and w0[-1] in ("师", "员", "长", "手", "者", "客", "主", "迷", "粉", "户", "警", "官", "商", "工")
            and w0 != "机器人"
            and w0 not in _NON_AGENT_GONG_WORDS
        )
    )
    if is_agent:
        if any(fp in ("v", "vg", "vd") for _wp, fp in c_pairs[1:]):
            return True
        if c_pairs[1][1] in ("d", "ad"):
            return True
        if (
            len(c_pairs) >= 3
            and c_pairs[1][1] == "vn"
            and c_pairs[1][0] in _CLAUSE_PRED_VERBS
            and c_pairs[2][1] in ("n", "nr", "ns", "nt", "nz", "vn", "v", "vg")
        ):
            return True

    is_spec_head = (
        f0 in ("nz", "b", "a")
        or w0.startswith(_SPEC_MOD_HEAD_PREFIXES)
        or bool(re.match(r"^(?:双|单|多|全|半|超|高|低|微|轻|重|大|小|长|短|薄|厚|冷|热|软|硬|快|慢)[核屏卡摄缸门路向层道清速精]", w0))
    )
    subj_pos_ok = f0 in ("n", "nr", "ns", "nt", "nz", "vn", "j")
    prev_complete = _is_completed_le_clause(prev_plain, prot_terms=prot_terms)

    # 2) 前句已是带终结「了2」的完整小句（如「昨天出故障了」「刚刚到货了」「刚开业就爆单了」），
    #    后接非规格修饰头的 [实体名词 + 谓词/状语/动作词] 必为独立新分句。
    if prev_complete and subj_pos_ok and not is_spec_head:
        if any(fp in ("v", "vg", "vd", "d", "ad") for _wp, fp in c_pairs[1:]):
            return True
        if (
            len(c_pairs) >= 3
            and (c_pairs[1][1] == "vn" or c_pairs[1][0] in _MIS_TAGGED_PRED_VERBS)
            and c_pairs[2][1] in ("n", "nr", "ns", "nt", "nz", "vn", "s", "l", "j", "v", "vg")
        ):
            return True

    # 3) 通用 3+ / 4+ 元 [N1 + (d/ad/vn)* + V + N2/V2] 句法判定：
    #    排除规格修饰/工具器械名词复合体（如「双核独立显卡」「智能仓储分拣系统」「全自动精密零件」），
    #    但允许独立实体主语 + 动作动词 + 宾语（含「官网下架商品」「工厂停产配件」「工厂生产零件」「系统自动生成报表」）。
    if len(c_pairs) >= 3 and subj_pos_ok and not is_spec_head:
        tail_w, tail_f = c_pairs[-1]
        tail_is_tool_head = (
            tail_w in _TOOL_SYSTEM_HEAD_WORDS
            or tail_w[-1] in _NOMINAL_TECH_TAIL_CHARS
        )
        if len(c_pairs) == 3 and (c_pairs[1][1] in ("v", "vn", "vg", "a") or c_pairs[1][0] in _MIS_TAGGED_PRED_VERBS):
            if c_pairs[2][1] in ("ul", "y") and c_pairs[1][1] in ("v", "vn", "vg", "a"):
                return True
            if c_pairs[2][1] in ("n", "nr", "ns", "nt", "nz", "vn", "s", "l", "j", "v", "vg") and (c_pairs[1][1] != "a" or c_pairs[1][0] in _MIS_TAGGED_PRED_VERBS):
                v1 = c_pairs[1][0]
                f1 = c_pairs[1][1]
                w2, f2 = c_pairs[2]
                entity_subj = (
                    f0 in ("nr", "nt", "ns", "j")
                    or (len(w0) >= 2 and w0[-1] in ("厂", "司", "店", "院", "局", "部", "校", "站", "库", "行", "台", "网"))
                )
                if v1 in _CLAUSE_PRED_VERBS:
                    return True
                if f2 in ("v", "vg", "vd") and not tail_is_tool_head and f0 != "vn":
                    return True
                if (f1 == "v" or v1 in _MIS_TAGGED_PRED_VERBS) and not tail_is_tool_head and f0 != "vn" and entity_subj:
                    return True
                if f1 == "vn" and not tail_is_tool_head and entity_subj:
                    return True
        else:
            # 4+ token 含状语修饰的独立子句（如「系统自动生成报表」「老客户直接转发推荐」）
            v_pos = -1
            for idx_k in range(1, len(c_pairs) - 1):
                wk, fk = c_pairs[idx_k]
                if fk in ("d", "ad") or (fk in ("vn", "a") and wk in _ADV_MOD_WORDS):
                    continue
                if fk in ("v", "vn", "vg") or wk in _MIS_TAGGED_PRED_VERBS or (fk == "a" and tail_f in ("ul", "y")):
                    v_pos = idx_k
                break
            if v_pos >= 1:
                entity_subj = (
                    f0 in ("nr", "nt", "ns", "j")
                    or (len(w0) >= 2 and w0[-1] in ("厂", "司", "店", "院", "局", "部", "校", "站", "库", "行", "台", "网"))
                )
                has_adv_mod = any(
                    c_pairs[k][1] in ("d", "ad") or c_pairs[k][0] in _ADV_MOD_WORDS
                    for k in range(1, v_pos)
                )
                has_attr_mod_before_tail = any(
                    fp in ("a", "b", "ns", "z", "uj")
                    for _wp, fp in c_pairs[v_pos + 1 : -1]
                )
                tail_modified_by_b = len(c_pairs) >= 2 and c_pairs[-2][1] == "b"
                if (
                    (
                        c_pairs[v_pos][0] in _CLAUSE_PRED_VERBS
                        and not (not entity_subj and not has_adv_mod and has_attr_mod_before_tail)
                    )
                    or (
                        (tail_f in ("v", "vg", "vd", "ul", "y") or any(fp in ("ul", "y") for _wp, fp in c_pairs[v_pos + 1 :]))
                        and not tail_modified_by_b
                    )
                    or (
                        tail_f in ("n", "nr", "ns", "nt", "nz", "vn", "s", "l", "j")
                        and not tail_is_tool_head
                        and f0 != "vn"
                        and (entity_subj or has_adv_mod)
                        and not has_attr_mod_before_tail
                    )
                ):
                    return True
    return False


def is_compound_np_body(
    text_plain: str,
    prot_terms: Optional[Sequence[str]] = None,
    *,
    max_len: int = 16,
    prev_plain: str = "",
) -> bool:
    """判断短语是否为合法复合名词体（含 13~16 字多层定语复合名与「双核独立显卡」类名物化尾词）。"""
    c_plain = strip_display_punctuation(str(text_plain or "")).replace(" ", "")
    if not c_plain or len(c_plain) > max_len:
        return False
    c_pairs = [
        (wp, _normalize_pos_tag(wp, fp))
        for wp, fp in _pseg_cut_clean(c_plain, prot_terms=prot_terms)
    ]
    if not c_pairs:
        return False
    if is_independent_svo_clause(c_plain, prot_terms=prot_terms, prev_plain=prev_plain):
        return False
    c_head_pos = c_pairs[0][1]
    if c_head_pos not in ("n", "nr", "ns", "nt", "nz", "vn", "a", "b", "j", "i"):
        return False
    if c_pairs[0][0] in _PERSONAL_CLAUSE_PRONOUNS:
        return False
    if any(fp in ("l", "i") and any(ch in wp for ch in ("了", "咗", "着", "过")) for wp, fp in c_pairs):
        return False
    if any(fp in ("d", "ad", "p", "c", "r", "ul", "uj", "uz", "ug", "ud", "uv", "y") for _wp, fp in c_pairs):
        return False
    tail_w, tail_f = c_pairs[-1]
    non_num_prev = len(c_pairs) >= 2 and c_pairs[-2][1] not in ("m", "mq", "r")
    if len(tail_w) == 1:
        if (
            len(c_pairs) < 2
            or len(c_plain) < 3
            or tail_w in _DP_NO_LEAD_CHARS
            or tail_w in _DP_NO_TAIL_CHARS
        ):
            return False
        nominal_tail_ok = (
            tail_f in ("n", "nr", "ns", "nt", "nz", "vn", "s", "ng")
            or (tail_f == "zg" and _is_one_char_nominal_object("买了", tail_w))
            or (tail_f == "q" and non_num_prev and _is_q_nominal_head(tail_w))
            or (tail_f == "v" and non_num_prev and tail_w in _NOMINAL_TECH_TAIL_CHARS and tail_w not in _CLAUSE_PRED_VERBS)
        )
    else:
        nominal_tail_ok = tail_f in ("n", "nr", "ns", "nt", "nz", "vn", "s", "l", "i", "j") or (
            tail_f == "v"
            and tail_w[-1] in _NOMINAL_TECH_TAIL_CHARS
            and len(c_pairs) >= 2
            and tail_w not in _CLAUSE_PRED_VERBS
        )
    if not nominal_tail_ok:
        return False
    last_idx = len(c_pairs) - 1
    v_indices = [
        idx_p
        for idx_p, (_wp, fp) in enumerate(c_pairs)
        if fp in ("v", "vg", "vd") and not (idx_p == last_idx and tail_f == "v")
    ]
    if not v_indices:
        return True
    if not (1 <= len(v_indices) <= 2):
        return False
    if not all(
        0 < vi < last_idx
        and len(c_pairs[vi][0]) >= 2
        and c_pairs[vi][0] not in _CLAUSE_PRED_VERBS
        for vi in v_indices
    ):
        return False
    tail_slice = c_pairs[v_indices[-1] + 1 :]
    return all(
        (fp in ("n", "nr", "ns", "nt", "nz", "vn", "s", "l", "j", "b", "a", "ng"))
        or (fp == "zg" and len(wp) == 1 and _is_one_char_nominal_object("买了", wp))
        or (fp == "q" and len(wp) == 1 and _is_q_nominal_head(wp))
        or (idx_s == len(tail_slice) - 1 and tail_f == "v")
        for idx_s, (wp, fp) in enumerate(tail_slice)
    )


def _restore_ascii_word_spaces(
    words: List[Dict[str, Any]],
    raw_sent_text: str,
    *,
    strip_punct: bool = True,
) -> List[Dict[str, Any]]:
    """保留 Whisper 词流中英文/数字词之间的单空格（如 Google + Cloud + Run -> Google Cloud Run）。"""
    if not words or not raw_sent_text or not re.search(r"[A-Za-z0-9]\s+[A-Za-z0-9]", raw_sent_text):
        return words
    raw_no_ws = re.sub(r"\s+", "", raw_sent_text)
    raw_clean = strip_display_punctuation(raw_no_ws) if strip_punct else raw_no_ws
    words_joined = "".join(re.sub(r"\s+", "", str(w.get("word") or "")) for w in words)
    if not raw_clean or not words_joined:
        return words
    space_bounds: set[int] = set()
    for m_sp in re.finditer(r"(?<=[A-Za-z0-9])\s+(?=[A-Za-z0-9])", raw_sent_text):
        pref = re.sub(r"\s+", "", raw_sent_text[: m_sp.start()])
        pref_clean = strip_display_punctuation(pref) if strip_punct else pref
        space_bounds.add(len(pref_clean))
    if not space_bounds:
        return words
    if words_joined == raw_clean:
        w_bounds = space_bounds
    else:
        w_bounds = set()
        sm = difflib.SequenceMatcher(None, words_joined, raw_clean, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for jb in space_bounds:
                    if j1 < jb < j2:
                        w_bounds.add(i1 + (jb - j1))
    if not w_bounds:
        return words
    out_words = [dict(w) for w in words]
    pos = 0
    for idx_w, w_item in enumerate(out_words):
        wt_plain = re.sub(r"\s+", "", str(w_item.get("word") or ""))
        pos += len(wt_plain)
        if pos in w_bounds and idx_w + 1 < len(out_words):
            wt = str(w_item.get("word") or "")
            nwt = str(out_words[idx_w + 1].get("word") or "")
            if wt and not wt.endswith(" ") and wt[-1].isascii() and wt[-1].isalnum() and nwt and nwt[0].isascii() and nwt[0].isalnum():
                out_words[idx_w]["word"] = wt + " "
    return out_words


def split_sentence_into_chunks(
    sent: Dict[str, Any],
    max_chars: int = 10,
    font_path: Optional[str] = None,
    font_size: int = 62,
    max_safe_px: int = SUBTITLE_SYMMETRIC_MAX_W - 32,
    strip_punct: bool = True,
    protected_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    把长句按真静音 (>=0.30s) 呼吸组 + 词边界动态规划（DP）拆分为无残句、不破词、不拆保护词的短句字幕块。
    """
    clean_fn = strip_display_punctuation if strip_punct else (lambda s: str(s or "").strip())
    raw_sent_text = str(sent.get("text") or "").strip()
    words = _clean_word_tokens(list(sent.get("words") or []), strip_punct=strip_punct)
    # 字符级对齐修复 ASR 漏听/同音误听（「氛→氛围」「四大王→四大天王」「四大轩王→四大天王」）
    if strip_punct and raw_sent_text:
        words = _repair_words_from_script(words, raw_sent_text)
    elif raw_sent_text:
        words = _retarget_words_to_text(words, raw_sent_text)
    if words and raw_sent_text:
        words = _restore_ascii_word_spaces(words, raw_sent_text, strip_punct=strip_punct)
    # 关键词过滤虚词碎片并向上扩全截断词（「一个」剔除，「六百」→「六百平」「三分」→「三分之一」）
    _disp_kws = sent.get("display_keywords")
    if isinstance(_disp_kws, list):
        raw_kws = [str(k) for k in _disp_kws if str(k).strip()]
    else:
        raw_kws = [str(k) for k in (sent.get("keywords") or []) if str(k).strip()]
    sent_kws = _clean_display_keywords(raw_kws, raw_sent_text)
    if words and sent_kws:
        # 若上游塞入跨越真实声学气口 (>=0.10s) 的碎词（如「公司名|改」中间停顿 0.18s），绝不让其充当保护词锁死断句
        clean_sent_str = "".join(str(w.get("word") or "") for w in words)
        word_end_gaps: List[Tuple[int, float]] = []
        acc_c = 0
        for wa, wb in zip(words, words[1:]):
            acc_c += len(str(wa.get("word") or ""))
            g_w = float(wb.get("start_sec", 0.0)) - float(wa.get("end_sec", 0.0))
            if g_w >= 0.10:
                word_end_gaps.append((acc_c, g_w))
        if word_end_gaps and clean_sent_str:
            filtered_kws: List[str] = []
            for kw in sent_kws:
                kw_pos = clean_sent_str.find(kw)
                if kw_pos >= 0 and any(kw_pos < gp < kw_pos + len(kw) for gp, _ in word_end_gaps):
                    continue
                filtered_kws.append(kw)
            sent_kws = filtered_kws
    if words:
        if any(w.get("is_keyword") for w in words) and sent_kws == raw_kws:
            pass
        elif any(w.get("is_keyword") for w in words) or sent_kws:
            # 只要做过关键词清洗，同步按完整清洗结果重染（含 cleaned==[] 时清掉上游脏标签，同时不误伤单元测试构造的 words 标色）
            words = _paint_keyword_flags(words, sent_kws)
    # 过滤上游 ASR 关键词中跨词/半拉噪声（如「文库下载」交叉「百度文库」、「人那成立」交叉「别人」、「是因」截断「是因为」），
    # 绝不让脏关键词进入保护词表锁死 DP 断句边界或劈开固定词组
    _known_cohesive = _UNIVERSAL_SPOKEN_LEXICON | set(_UNIVERSAL_PUBLIC_PLATFORMS) | set(protected_terms or [])
    _known_spans: List[Tuple[int, int]] = []
    for _kt in _known_cohesive:
        if len(_kt) >= 2:
            for _mk in re.finditer(re.escape(_kt), raw_sent_text):
                _known_spans.append((_mk.start(), _mk.end()))
    _kw_bad_head = set("是的了咗在把被从跟比和与让给俾那这")
    safe_kws_for_prot: List[str] = []
    for _kw in sent_kws:
        _ks = str(_kw).strip()
        if (
            not (2 <= len(_ks) <= 4)
            or _ks[0] in _kw_bad_head
            or _ks[0] in _DP_NO_LEAD_CHARS
            or _ks[-1] in _DP_NO_TAIL_CHARS
            or _ks in _STOP_KEYWORDS
        ):
            continue
        _kw_pairs = _pseg_cut_clean(_ks)
        if _kw_pairs and _kw_pairs[-1][1] in ("d", "p", "c", "u", "uj", "ul", "zg"):
            continue
        _crosses = False
        for _mk in re.finditer(re.escape(_ks), raw_sent_text):
            a_k, b_k = _mk.start(), _mk.end()
            for a_c, b_c in _known_spans:
                # 若与已知完整保护词发生部分交叉或仅为真子串，拒绝用它锁死切分
                if (a_k < b_c and b_k > a_c) and not (a_k == a_c and b_k == b_c):
                    _crosses = True
                    break
            if _crosses:
                break
        if not _crosses:
            safe_kws_for_prot.append(_ks)
    eff_terms = load_protected_terms(extra_terms=list(protected_terms or []) + safe_kws_for_prot)

    if not words:
        raw_t = str(sent.get("text", "")).strip()
        dp_segs = dp_segment_short_lines(
            raw_t,
            max_width_px=max_safe_px,
            font_path=font_path,
            font_size=font_size,
            terms=eff_terms,
            max_chars=max_chars,
            strip_punct=strip_punct,
        )
        if not dp_segs:
            clean_txt = clean_fn(raw_t).strip()
            return [{
                "text": clean_txt or raw_t,
                "start_sec": sent["start_sec"],
                "end_sec": sent["end_sec"],
                "words": [],
                "keywords": sent_kws,
            }]
        total_chars = max(1, sum(len(t.replace(" ", "")) for _, _, t in dp_segs))
        t0, t1 = float(sent["start_sec"]), float(sent["end_sec"])
        span = max(0.2, t1 - t0)
        cursor_t = t0
        out_no_words: List[Dict[str, Any]] = []
        for _, _, seg_t in dp_segs:
            seg_dur = span * (max(1, len(seg_t.replace(" ", ""))) / total_chars)
            out_no_words.append({
                "text": seg_t,
                "start_sec": round(cursor_t, 4),
                "end_sec": round(min(t1, cursor_t + seg_dur), 4),
                "words": [],
                "keywords": sent_kws,
            })
            cursor_t += seg_dur
        return _guard_no_aux_head(
            out_no_words,
            max_chars=max_chars,
            max_width_px=max_safe_px,
            font_path=font_path,
            font_size=font_size,
        )

    breath_groups: List[List[Dict[str, Any]]] = []
    cur_grp: List[Dict[str, Any]] = [words[0]]
    _bg_no_split_tails = ("有啲", "好多", "呢个", "嗰个", "𠮶个", "呢啲", "𠮶啲", "嗰啲", "一间", "一个", "一份", "一种")
    for idx_w, (prev_w, cur_w) in enumerate(zip(words, words[1:]), 1):
        gap = float(cur_w["start_sec"]) - float(prev_w["end_sec"])
        # 句末强制断组：一屏之内不跨句；仅当真实大停顿 (>=0.55s) 且断点不在虚词/修饰词上时拆呼吸组，
        # 中小停顿交由 DP 结合 word_gaps 与词性边界做全局最优断句。
        if prev_w.get("_sent_end"):
            breath_groups.append(cur_grp)
            cur_grp = [cur_w]
            continue
        cur_grp_tail = "".join(str(x.get("word") or "") for x in cur_grp[-3:])
        cur_w_txt = str(cur_w.get("word") or "")
        nxt_w_preview = "".join(str(x.get("word") or "") for x in words[idx_w : idx_w + 4])
        safe_breath_break = bool(
            cur_grp_tail
            and cur_grp_tail[-1] not in _DP_NO_TAIL_CHARS
            and not cur_grp_tail.endswith(_bg_no_split_tails)
            and cur_w_txt
            and (
                cur_w_txt[0] not in _BAD_SUB_HEAD_AUX
                or (cur_w_txt[0] == "呢" and _is_cantonese_ne_demo_head(nxt_w_preview))
            )
        )
        if gap >= 0.55 and len(cur_grp) >= 4 and safe_breath_break:
            breath_groups.append(cur_grp)
            cur_grp = [cur_w]
        else:
            cur_grp.append(cur_w)
    if cur_grp:
        last_gap = (
            float(cur_grp[0]["start_sec"]) - float(breath_groups[-1][-1]["end_sec"])
            if breath_groups else 0.0
        )
        if breath_groups and len(cur_grp) <= 3 and not breath_groups[-1][-1].get("_sent_end") and last_gap < 0.35:
            breath_groups[-1].extend(cur_grp)
        else:
            breath_groups.append(cur_grp)

    chunks: List[Dict[str, Any]] = []
    for grp in breath_groups:
        txt = "".join(str(x["word"]) for x in grp)
        px_w, _ = _measure_text_px(txt, font_path, font_size)
        has_clear_gap = any(
            (float(b["start_sec"]) - float(a["end_sec"]) >= 0.18)
            and min(i + 1, len(grp) - i - 1) >= 4
            for i, (a, b) in enumerate(zip(grp, grp[1:]))
        )
        if len(txt) <= max_chars and px_w <= max_safe_px and not has_clear_gap:
            grp_clean = [dict(x) for x in grp]
            if grp_clean and str(grp_clean[-1].get("word") or "").endswith(" "):
                grp_clean[-1]["word"] = str(grp_clean[-1]["word"]).rstrip()
            chunks.append({
                "text": txt.strip(),
                "start_sec": grp[0]["start_sec"],
                "end_sec": grp[-1]["end_sec"],
                "words": grp_clean,
                "keywords": sent_kws,
            })
            continue

        # 按「先保留标点成稿分词 DP，后剥标点回填逐字时间轴」流程：从成稿把软标点（，、；：。！？）回贴至 grp，
        # 同时把 words 之间真实声学气口映射为字符级 word_gaps 喂给 DP，让断点主动落在真实换气处。
        dp_input = _restore_script_punctuation(txt, [raw_sent_text]) if (strip_punct and raw_sent_text) else txt
        char_to_wi: List[int] = []
        for wi, w_item in enumerate(grp):
            for _ in str(w_item["word"]):
                char_to_wi.append(wi)
        dp_to_norm: List[Optional[int]] = []
        norm_i = 0
        for ch in dp_input:
            while norm_i < len(txt) and txt[norm_i].isspace() and not ch.isspace():
                norm_i += 1
            if norm_i < len(txt) and ch == txt[norm_i]:
                dp_to_norm.append(norm_i)
                norm_i += 1
            else:
                dp_to_norm.append(None)
        word_gaps: Dict[int, float] = {}
        for pos in range(1, len(dp_input)):
            p_norm = next((dp_to_norm[k] for k in range(pos - 1, -1, -1) if dp_to_norm[k] is not None), None)
            n_norm = next((dp_to_norm[k] for k in range(pos, len(dp_to_norm)) if dp_to_norm[k] is not None), None)
            if p_norm is None or n_norm is None:
                continue
            p_wi, n_wi = char_to_wi[p_norm], char_to_wi[n_norm]
            if p_wi != n_wi:
                word_gaps[pos] = max(0.0, float(grp[n_wi]["start_sec"]) - float(grp[p_wi]["end_sec"]))

        dp_segs = dp_segment_short_lines(
            dp_input,
            max_width_px=max_safe_px,
            font_path=font_path,
            font_size=font_size,
            terms=eff_terms,
            max_chars=max_chars,
            strip_punct=strip_punct,
            word_gaps=word_gaps,
        )
        used_end_wi = 0
        for s_ch, e_ch, _ in dp_segs:
            mapped = [dp_to_norm[k] for k in range(max(0, s_ch), min(len(dp_to_norm), e_ch)) if dp_to_norm[k] is not None]
            if not mapped:
                continue
            w_start = max(used_end_wi, char_to_wi[mapped[0]])
            w_end = char_to_wi[min(len(char_to_wi) - 1, mapped[-1])] + 1
            if w_end <= w_start:
                continue
            sl = [dict(x) for x in grp[w_start:w_end]]
            used_end_wi = w_end
            if sl:
                if str(sl[-1].get("word") or "").endswith(" "):
                    sl[-1]["word"] = str(sl[-1]["word"]).rstrip()
                chunks.append({
                    "text": "".join(str(x["word"]) for x in sl).strip(),
                    "start_sec": sl[0]["start_sec"],
                    "end_sec": sl[-1]["end_sec"],
                    "words": sl,
                    "keywords": sent_kws,
                })
        if used_end_wi < len(grp):
            tail_sl = [dict(x) for x in grp[used_end_wi:]]
            if tail_sl and str(tail_sl[-1].get("word") or "").endswith(" "):
                tail_sl[-1]["word"] = str(tail_sl[-1]["word"]).rstrip()
            if chunks and len(tail_sl) <= 3:
                chunks[-1]["words"].extend(tail_sl)
                chunks[-1]["text"] = (chunks[-1]["text"] + "".join(str(x["word"]) for x in tail_sl)).strip()
                chunks[-1]["end_sec"] = tail_sl[-1]["end_sec"]
            elif tail_sl:
                chunks.append({
                    "text": "".join(str(x["word"]) for x in tail_sl).strip(),
                    "start_sec": tail_sl[0]["start_sec"],
                    "end_sec": tail_sl[-1]["end_sec"],
                    "words": tail_sl,
                    "keywords": sent_kws,
                })
    # 时间必须落在本句窗口内、且前后不重叠：成稿文字与 ASR 词表对不齐时，
    # 插值会算到句界之外（2026-11-24 实测最大越窗 1480ms，1 处）
    t_lo = float(sent["start_sec"])
    t_hi = float(sent["end_sec"])
    prev_end = t_lo
    for _c in chunks:
        st = min(max(float(_c["start_sec"]), prev_end), t_hi)
        en = min(max(float(_c["end_sec"]), st + 0.08), t_hi)
        _c["start_sec"] = round(st, 4)
        _c["end_sec"] = round(en, 4)
        prev_end = en
    for _c in chunks:
        for _w in _c.get("words") or []:
            _w.pop("_sent_end", None)
    return _guard_no_aux_head(
        chunks,
        max_chars=max_chars,
        max_width_px=max_safe_px,
        font_path=font_path,
        font_size=font_size,
    )


_BAD_SUB_HEAD_AUX = frozenset("的地得之了咗着过嘅啲嘛呢吧啊呀")
_GENITIVE_AUX_HEAD = frozenset("的嘅地得之")


def _guard_no_aux_head(
    chunks: List[Dict[str, Any]],
    max_chars: int = 12,
    *,
    max_width_px: Optional[int] = None,
    font_path: Optional[str] = None,
    font_size: int = 62,
) -> List[Dict[str, Any]]:
    """全风格通用字幕行首护栏：绝不允许以「的/地/得/了/咗/着/过/嘅」等助词作下一条字幕的第一个字，
    且避免把「的/嘅」孤零零拽到上一条末尾（豁免粤语「呢度/呢批/呢个/...」指示代词）。"""
    if len(chunks) < 2:
        return chunks

    def _fits_width(txt: str) -> bool:
        if not max_width_px:
            return True
        pw, _ = _measure_text_px(txt, font_path, font_size)
        return pw <= int(max_width_px)

    def _has_ord_tail(txt: str) -> bool:
        plain = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(txt or ""))
        return len(plain) >= 4 and bool(re.search(r"(第[一二三四五六七八九十两\d]+)$", plain))

    out = [dict(c) for c in chunks]
    changed = True
    while changed:
        changed = False
        for k in range(len(out) - 1):
            cur_t = str(out[k].get("text") or "")
            nxt_t = str(out[k + 1].get("text") or "")
            if not cur_t or not nxt_t or nxt_t[0] not in _BAD_SUB_HEAD_AUX:
                continue
            if nxt_t[0] == "呢" and _is_cantonese_ne_demo_head(nxt_t):
                continue
            cur_w = list(out[k].get("words") or [])
            nxt_w = list(out[k + 1].get("words") or [])
            if len(cur_t) + len(nxt_t) <= max(12, max_chars + 2) and _fits_width(cur_t + nxt_t) and not _has_ord_tail(cur_t + nxt_t):
                out[k]["text"] = cur_t + nxt_t
                out[k]["end_sec"] = max(float(out[k]["end_sec"]), float(out[k + 1]["end_sec"]))
                out[k]["words"] = cur_w + nxt_w
                out[k]["keywords"] = list(dict.fromkeys(list(out[k].get("keywords") or []) + list(out[k + 1].get("keywords") or [])))
                out.pop(k + 1)
                changed = True
                break
            # 若下块首字为的/嘅/地/得/之，优先从上块末尾推一个短修饰词给下块（保持修饰语+的+中心语完整）
            if nxt_t[0] in _GENITIVE_AUX_HEAD:
                cur_toks = tokenize_semantic_words(cur_t)
                if len(cur_toks) >= 2:
                    tail_tk = cur_toks[-1]
                    rem_cur = cur_t[: len(cur_t) - len(tail_tk)]
                    if (
                        (
                            1 <= len(tail_tk) <= 3
                            or (len(tail_tk) <= 6 and bool(re.match(r"^(?:[0-9零〇一二两三四五六七八九十百千万亿廿]+|%)", tail_tk)))
                        )
                        and len(rem_cur) >= MIN_CHUNK_CHARS
                        and rem_cur[-1] not in _GENITIVE_AUX_HEAD
                        and not _has_ord_tail(rem_cur)
                        and len(tail_tk) + len(nxt_t) <= max(12, max_chars + 2)
                        and _fits_width(tail_tk + nxt_t)
                    ):
                        out[k]["text"] = rem_cur
                        out[k + 1]["text"] = tail_tk + nxt_t
                        if cur_w and sum(len(str(w.get("word") or "")) for w in cur_w) == len(cur_t):
                            mv_ws: List[Dict[str, Any]] = []
                            mv_len = 0
                            while cur_w and mv_len < len(tail_tk):
                                w_last = cur_w.pop()
                                mv_ws.insert(0, w_last)
                                mv_len += len(str(w_last.get("word") or ""))
                            if mv_len == len(tail_tk):
                                out[k]["words"] = cur_w
                                out[k + 1]["words"] = mv_ws + nxt_w
                                if cur_w:
                                    out[k]["end_sec"] = float(cur_w[-1].get("end_sec", out[k]["end_sec"]))
                                out[k + 1]["start_sec"] = float(mv_ws[0].get("start_sec", out[k + 1]["start_sec"]))
                        changed = True
                        break
                if len(cur_t) + len(nxt_t) <= max(13, max_chars + 3) and _fits_width(cur_t + nxt_t) and not _has_ord_tail(cur_t + nxt_t):
                    out[k]["text"] = cur_t + nxt_t
                    out[k]["end_sec"] = max(float(out[k]["end_sec"]), float(out[k + 1]["end_sec"]))
                    out[k]["words"] = cur_w + nxt_w
                    out[k]["keywords"] = list(dict.fromkeys(list(out[k].get("keywords") or []) + list(out[k + 1].get("keywords") or [])))
                    out.pop(k + 1)
                    changed = True
                    break
            if len(nxt_t) >= 2 and len(cur_t) < 13 and _fits_width(cur_t + nxt_t[0]) and not _has_ord_tail(cur_t + nxt_t[0]):
                ch_mv = nxt_t[0]
                out[k]["text"] = cur_t + ch_mv
                out[k + 1]["text"] = nxt_t[1:]
                if nxt_w and len(str(nxt_w[0].get("word") or "")) == 1:
                    w_mv = nxt_w.pop(0)
                    cur_w.append(w_mv)
                    out[k]["words"] = cur_w
                    out[k + 1]["words"] = nxt_w
                    out[k]["end_sec"] = float(w_mv.get("end_sec", out[k]["end_sec"]))
                    if nxt_w:
                        out[k + 1]["start_sec"] = float(nxt_w[0].get("start_sec", out[k + 1]["start_sec"]))
                changed = True
                break
    return out


_TAIL_FILLER_CHARS = set("嘅啲啦嘛的了咗呢吧啊呀哦喂么咯噃囉㗎")
_BAD_TAIL_AUX_CHARS = _TAIL_FILLER_CHARS | {"有", "系", "在", "着", "去", "来", "住", "到", "个", "就", "得", "把", "被", "从", "跟", "比", "和", "与"}


def _subtitle_tail_highlight_target(style_id: str) -> float:
    """从因果规则表读取正文句尾重音覆盖率（仅 style_01 54% / style_06 67% 等显式启用字幕高亮的风格生效）。"""
    kh = element_rules.get_element_rule(style_id, "keyword_highlight")
    if not isinstance(kh, dict) or not kh.get("present"):
        return 0.0
    ev = str((kh.get("why_appears") or {}).get("evidence") or "")
    m = re.search(r"占字幕句\s*(\d+(?:\.\d+)?)%", ev)
    if not m:
        return 0.0
    pct = float(m.group(1))
    return min(0.68, pct / 100.0) if pct > 0.0 else 0.0


def _apply_phrase_end_highlights(
    entries: List[Dict[str, Any]],
    target_ratio: float,
    protected_terms: Optional[List[str]] = None,
) -> int:
    """对尚未含关键词高亮且字数 >= 4 的非屏蔽短句，沿分词边界在句末实词（2–3 字）落下重音高亮，杜绝「住有/啦有/得系」等跨词伪高亮。"""
    active = [e for e in entries if not e.get("suppressed")]
    if not active or target_ratio <= 0.0:
        return 0
    target_n = int(round(len(active) * target_ratio))
    already = sum(1 for e in active if any(w.get("is_keyword") for w in (e.get("words") or [])))
    if already >= target_n:
        return 0
    added = 0
    for e in active:
        if already >= target_n:
            break
        words = e.get("words") or []
        plain = re.sub(r"\s+", "", str(e.get("text") or ""))
        if len(plain) < 4 or not words or any(w.get("is_keyword") for w in words):
            continue
        new_ws = [dict(w) for w in words]
        # 构造 words 文字字符映射，并结合 tokenize_semantic_words 按整词锁定句尾实词
        tok_src = "".join(re.sub(r"\s+", "", str(w.get("word") or "")) for w in new_ws)
        if not tok_src:
            continue
        # 先剥除句尾纯语气助词（「嘅/啲/啦/嘛/的/了」），再按整词边界锁定句尾实词（2–4字）
        # 绝不从词中间硬切片造出「0多万」「情差」「司名」「百平」等脏碎片！
        core_src = tok_src.rstrip("".join(_TAIL_FILLER_CHARS))
        if len(core_src) < 2:
            continue
        sem_toks = tokenize_semantic_words(core_src, terms=protected_terms)
        tok_spans: List[Tuple[int, int, str]] = []
        pos = 0
        for tk in sem_toks:
            ntk = re.sub(r"\s+", "", tk)
            if not ntk:
                continue
            tok_spans.append((pos, pos + len(ntk), ntk))
            pos += len(ntk)
        max_hl = 3 if len(plain) <= 6 else 5
        picked_span: Optional[Tuple[int, int, str]] = None
        for cs, ce, ntk in reversed(tok_spans):
            if all(ch in _BAD_TAIL_AUX_CHARS for ch in ntk) or ntk in _STOP_KEYWORDS:
                continue
            # 若整句较短且尾词为 4 字纯中文双音节复合词（如「点赞茶楼」「岭南元素」，不含数字量词），
            # 优先 jieba 细拆，无细拆时按 2+2 取后双字完整实词（「茶楼」「元素」），绝不从 3/5 字词中硬切片
            if len(ntk) > max_hl and len(ntk) == 4 and re.fullmatch(r"[\u4e00-\u9fff]{4}", ntk) and not re.search(r"[零一二两三四五六七八九十百千万亿]", ntk):
                sub_tail = ""
                try:
                    _clean_tok, _ = _get_clean_pseg_tokenizer()
                    sub_parts = [sp for sp in _clean_tok.lcut(ntk) if sp.strip()]
                    if len(sub_parts) >= 2 and 2 <= len(sub_parts[-1]) <= max_hl:
                        sub_tail = sub_parts[-1]
                except Exception:
                    pass
                if not sub_tail:
                    sub_tail = ntk[2:]
                if (
                    sub_tail
                    and sub_tail not in _STOP_KEYWORDS
                    and not all(ch in _BAD_TAIL_AUX_CHARS for ch in sub_tail)
                    and sub_tail[0] not in _DP_NO_LEAD_CHARS
                    and sub_tail[-1] not in _DP_NO_TAIL_CHARS
                ):
                    picked_span = (ce - len(sub_tail), ce, sub_tail)
                    break
            if len(ntk) < 2 or len(ntk) > max_hl or (ce - cs) >= len(plain):
                continue
            if ntk[0] in _DP_NO_LEAD_CHARS or ntk[-1] in _DP_NO_TAIL_CHARS:
                continue
            picked_span = (cs, ce, ntk)
            break
        if picked_span is None:
            continue
        cs_pick, ce_pick, tail_phrase = picked_span
        picked_indices: List[int] = []
        w_pos = 0
        for wi, w_item in enumerate(new_ws):
            wt = re.sub(r"\s+", "", str(w_item.get("word") or ""))
            if not wt:
                continue
            ws_i, we_i = w_pos, w_pos + len(wt)
            w_pos = we_i
            if ws_i < ce_pick and we_i > cs_pick:
                picked_indices.append(wi)
        if not picked_indices or (ce_pick - cs_pick) >= len(plain):
            continue
        for wi in picked_indices:
            new_ws[wi]["is_keyword"] = True
        e["words"] = new_ws
        kws = _clean_display_keywords(list(e.get("keywords") or []) + [tail_phrase], plain)
        if tail_phrase and tail_phrase not in kws:
            kws.append(tail_phrase)
        e["keywords"] = kws
        already += 1
        added += 1
    return added


def _build_safe_ass_style(raw_ass_style: str, font_path: Optional[str], font_size: Optional[int] = None) -> str:
    """动态对齐 ASS Style 中的商用字体名、字号、对称左右边距 (MarginL=MarginR=220 保正中 540px) 与 MarginV >= 452。"""
    parts = [p.strip() for p in str(raw_ass_style or "").split(",")]
    if len(parts) < 23:
        return str(raw_ass_style or "")
    if font_size and int(font_size) > 0:
        parts[2] = str(int(font_size))
    if font_path:
        stem = Path(font_path).stem
        low_stem = stem.lower()
        if "alimama" in low_stem:
            parts[1] = "Alimama ShuHeiTi"
        elif "smileysans" in low_stem:
            parts[1] = "Smiley Sans"
        elif "sourcehanserifcn-heavy" in low_stem:
            parts[1] = "Source Han Serif CN Heavy"
        elif "sourcehanserif" in low_stem:
            parts[1] = "Source Han Serif CN"
        elif "sourcehansans" in low_stem:
            parts[1] = "Source Han Sans CN"
        elif "notoserifsc" in low_stem:
            parts[1] = "Noto Serif CJK SC"
        elif "notosans" in low_stem:
            parts[1] = "Noto Sans CJK SC"
        elif "pingfang" not in low_stem and "stheiti" not in low_stem:
            parts[1] = stem
    sym_margin_x = str(CANVAS_WIDTH - SUBTITLE_SAFE_ZONE["x_max"])  # 220 -> [220, 860] 轴心 540px
    parts[19] = sym_margin_x
    parts[20] = sym_margin_x
    min_margin_v = CANVAS_HEIGHT - SUBTITLE_SAFE_ZONE["y_max"]  # 452
    try:
        cur_mv = int(float(parts[21]))
    except Exception:
        cur_mv = min_margin_v
    if cur_mv < min_margin_v:
        parts[21] = str(min_margin_v)
    return ",".join(parts)


def _load_semantic_subtitle_override(
    pipeline_dir: Path,
    audio_events: Dict[str, Any],
    strip_punct: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    """
    V11.0 大模型语义精修字幕直通通道：
    当 pipeline 目录下存在 `05a_semantic_subtitles.json`（或 `03b_director_plan.json` 含 `semantic_subtitles`）时，
    将大模型规划的人类级意群分句与关键词，按字符级精确对齐到 `03_audio_events.json` 的扁平逐字时间戳流。
    要求纯文本字符覆盖率 >= 0.90（杜绝篡改口播原话），时间轴严格单调不重叠，原话一字不差。
    """
    raw_items: Any = None
    sem_p = Path(pipeline_dir) / "05a_semantic_subtitles.json"
    if sem_p.is_file():
        try:
            doc = read_json(sem_p)
            raw_items = doc.get("segments") if isinstance(doc, dict) else doc
        except Exception:
            raw_items = None
    if not raw_items:
        dp_p = Path(pipeline_dir) / "03b_director_plan.json"
        if dp_p.is_file():
            try:
                dp_doc = read_json(dp_p)
                raw_items = dp_doc.get("semantic_subtitles") if isinstance(dp_doc, dict) else None
            except Exception:
                raw_items = None
    if not isinstance(raw_items, list) or not raw_items:
        return None

    # 收集全片扁平逐字时间戳流（连同原 sentence_id 映射）
    flat_words: List[Dict[str, Any]] = []
    flat_sids: List[str] = []
    for sent in audio_events.get("sentences") or []:
        sid = str(sent.get("id") or "s000")
        s_words = _clean_word_tokens(list(sent.get("words") or []), strip_punct=strip_punct)
        s_txt = str(sent.get("text") or "").strip()
        if strip_punct and s_txt and s_words:
            s_words = _repair_words_from_script(s_words, s_txt)
        for w in s_words:
            wt = _norm_tokens(w.get("word"))
            if not wt:
                continue
            ws = float(w.get("start_sec", sent.get("start_sec", 0.0)))
            we = float(w.get("end_sec", max(ws + 0.04, sent.get("end_sec", ws + 0.04))))
            n_ch = len(wt)
            for k, ch in enumerate(wt):
                flat_words.append({
                    "word": ch,
                    "start_sec": ws + (we - ws) * k / n_ch,
                    "end_sec": ws + (we - ws) * (k + 1) / n_ch,
                })
                flat_sids.append(sid)
    if not flat_words:
        return None

    cleaned_items: List[Dict[str, Any]] = []
    for it in raw_items:
        if isinstance(it, str):
            it = {"text": it}
        if not isinstance(it, dict):
            continue
        txt = str(it.get("text") or "").strip()
        if strip_punct:
            txt = strip_display_punctuation(re.sub(r"\s+", "", txt))
        if not txt:
            continue
        cleaned_items.append({
            "text": txt,
            "keywords": [str(k).strip() for k in (it.get("keywords") or []) if str(k).strip()],
            "display_lines": list(it.get("display_lines") or []) if isinstance(it.get("display_lines"), list) else None,
        })
    if not cleaned_items:
        return None

    full_target = "".join(_norm_tokens(it["text"]) for it in cleaned_items)
    full_asr = "".join(w["word"] for w in flat_words)
    if not full_target or not full_asr:
        return None
    # 粤语 嗰/𠮶 同义归一用于比对覆盖率，确保不因字形规范化误判失配
    _norm_cmp = lambda s: s.replace("𠮶", "嗰")
    sm_chk = difflib.SequenceMatcher(None, _norm_cmp(full_target), _norm_cmp(full_asr), autojunk=False)
    if sm_chk.ratio() < 0.88:
        return None

    # 逐字符映射每个目标字符的时间戳和所属 sentence_id
    aligned_chars, cov = align_corrected_text_to_words(
        full_target,
        [{"word": _norm_cmp(w["word"]), "start_sec": w["start_sec"], "end_sec": w["end_sec"]} for w in flat_words],
    )
    if len(aligned_chars) != len(full_target) or cov < 0.85:
        return None
    # 恢复目标字符本身（保留原定义字形）并定位对应 sentence_id
    asr_times = [(w["start_sec"], w["end_sec"], sid) for w, sid in zip(flat_words, flat_sids)]
    out_chunks: List[Dict[str, Any]] = []
    pos = 0
    prev_end = 0.0
    for it in cleaned_items:
        norm_t = _norm_tokens(it["text"])
        n_c = len(norm_t)
        if n_c <= 0 or pos + n_c > len(aligned_chars):
            continue
        seg_ws: List[Dict[str, Any]] = []
        for idx_c in range(n_c):
            ac = dict(aligned_chars[pos + idx_c])
            ac["word"] = norm_t[idx_c]
            seg_ws.append(ac)
        pos += n_c
        st = max(prev_end, float(seg_ws[0]["start_sec"]))
        en = max(st + 0.08, float(seg_ws[-1]["end_sec"]))
        # 收集本条精修短句覆盖到的全部有序去重 sentence_id（同时纳入时间窗重叠的原句，与 qa_inspector 原生 merged_sentence_ids 严丝合缝对齐）
        covered_sids: List[str] = []
        for s_orig in audio_events.get("sentences") or []:
            s_id0 = str(s_orig.get("id") or "")
            if not s_id0:
                continue
            os_s = float(s_orig.get("start_sec", 0.0))
            os_e = float(s_orig.get("end_sec", 0.0))
            if os_s <= en + 0.04 and os_e >= st - 0.04 and s_id0 not in covered_sids:
                covered_sids.append(s_id0)
        if not covered_sids:
            for cw in seg_ws:
                c_mid = 0.5 * (float(cw["start_sec"]) + float(cw["end_sec"]))
                c_sid = min(asr_times, key=lambda x: abs(0.5 * (x[0] + x[1]) - c_mid))[2]
                if c_sid not in covered_sids:
                    covered_sids.append(c_sid)
        # 若首/尾字落在多字 ASR token 内部（如「哥我」），把起止时间柔和吸附到所属句组真实词边界（偏差 <= 0.16s 时），确保与 03_audio_events 交叉误差 <= 1 帧
        sent_by_id_loc = {str(s_o.get("id") or ""): s_o for s_o in (audio_events.get("sentences") or [])}
        ref_ws_loc = [w for sid_k in covered_sids for w in ((sent_by_id_loc.get(sid_k) or {}).get("words") or [])]
        if ref_ws_loc:
            r_starts = [float(w["start_sec"]) for w in ref_ws_loc if "start_sec" in w]
            r_ends = [float(w["end_sec"]) for w in ref_ws_loc if "end_sec" in w]
            if r_starts:
                near_s = min(r_starts, key=lambda x: abs(x - st))
                if abs(near_s - st) <= 0.30 and near_s < en - 0.06:
                    if near_s >= prev_end - 0.005:
                        st = max(prev_end, near_s)
                    elif out_chunks and near_s > out_chunks[-1]["start_sec"] + 0.12:
                        # 极罕见跨句 ASR 双字 token 边界：微调前条尾时间齐平，彻底消除边界漂移
                        st = near_s
                        out_chunks[-1]["end_sec"] = round(near_s, 4)
                        if out_chunks[-1].get("words"):
                            out_chunks[-1]["words"][-1]["end_sec"] = round(near_s, 4)
            if r_ends:
                near_e = min(r_ends, key=lambda x: abs(x - en))
                if abs(near_e - en) <= 0.30 and near_e > st + 0.06:
                    en = near_e
        # 确保单句或合并句组时间窗完整包住 [st, en]
        for s_orig in audio_events.get("sentences") or []:
            s_id0 = str(s_orig.get("id") or "")
            if not s_id0:
                continue
            os_s = float(s_orig.get("start_sec", 0.0))
            os_e = float(s_orig.get("end_sec", 0.0))
            if os_s <= en + 0.04 and os_e >= st - 0.04 and s_id0 not in covered_sids:
                covered_sids.append(s_id0)
        seg_ws[0]["start_sec"] = round(st, 4)
        seg_ws[-1]["end_sec"] = round(en, 4)
        prev_end = en
        best_sid = covered_sids[0] if covered_sids else flat_sids[0]
        clean_kws = _clean_display_keywords(it["keywords"], it["text"])
        seg_ws = _paint_keyword_flags(seg_ws, clean_kws)
        chunk_rec: Dict[str, Any] = {
            "sentence_id": best_sid,
            "text": it["text"],
            "start_sec": round(st, 4),
            "end_sec": round(en, 4),
            "words": seg_ws,
            "keywords": clean_kws,
            "display_lines": it["display_lines"],
        }
        if len(covered_sids) > 1:
            chunk_rec["merged_sentence_ids"] = covered_sids
        out_chunks.append(chunk_rec)
    return out_chunks if out_chunks else None


_SEM_AUX_HEAD_ALLOW = (
    "之前", "之后", "之内", "之外", "之间", "之上", "之下", "之中", "之一", "之类",
    "了解", "了不起", "得到", "着急", "着想", "过了", "过去", "过来", "过年", "过关", "过头", "过分",
)
_SEM_STRICT_TAIL_CHARS = frozenset("地得之把被从向给俾在喺这那嗰𠮶某每各")
_SEM_MULTI_MOD_TAILS = (
    "有啲", "好多", "呢啲", "𠮶啲", "嗰啲", "呢个", "嗰个", "𠮶个",
    "这个", "那个", "哪个", "每个", "某个", "呢一个", "𠮶一个", "嗰一个",
    "𠮶种", "嗰种", "这种", "那种", "呢种", "哪种", "每种", "某种",
)


def _validate_and_repair_sem_override_chunks(
    sem_chunks: List[Dict[str, Any]],
    audio_events: Dict[str, Any],
    *,
    prot_terms: List[str],
    tpl_max_chars: int,
    chunk_max_chars: int,
    chunk_fs: int,
    chunk_px: int,
    font_path: Optional[str],
    strip_punct: bool,
    script_lines_punct: List[str],
    raw_pain_kws: set[str],
) -> List[Dict[str, Any]]:
    """逐句校验 05a/director_plan 语义直通分块；仅对拆破保护词或出现挂尾/动貌/助词割裂的句子回退重切，干净句保持原样。"""
    if not sem_chunks:
        return sem_chunks

    def _plain(val: Any) -> str:
        return strip_display_punctuation(str(val or "")).replace(" ", "")

    def _sids_of(obj: Dict[str, Any]) -> set[str]:
        sids = {str(x) for x in (obj.get("merged_sentence_ids") or []) if x}
        for k_id in ("sentence_id", "id"):
            if obj.get(k_id):
                sids.add(str(obj[k_id]))
        return sids

    sents_orig = list(audio_events.get("sentences") or [])
    sents_by_id = {str(s.get("id") or ""): s for s in sents_orig if s.get("id")}
    all_prot = sorted(
        {t for t in prot_terms if len(t) >= 2}
        | _UNIVERSAL_SPOKEN_LEXICON
        | set(_UNIVERSAL_PUBLIC_PLATFORMS),
        key=len,
        reverse=True,
    )

    def _has_transcript_punct(ch_a: Dict[str, Any], ch_b: Dict[str, Any], ta: str, tb: str) -> bool:
        raw_a = str(ch_a.get("text") or "").strip()
        if raw_a.endswith(("。", "？", "！", "?", "!")):
            return True
        tail_chars = ta[-min(4, len(ta)):]
        head_chars = tb[:min(3, len(tb))]
        if not tail_chars or not head_chars:
            return False
        pat = re.compile(
            r"\s*".join(re.escape(c) for c in tail_chars)
            + r"\s*(?:[，。！？、；;?!]|[:：,](?!\d))\s*"
            + r"\s*".join(re.escape(c) for c in head_chars)
        )
        sids = _sids_of(ch_a) | _sids_of(ch_b)
        for sid in sids:
            ref_t = str((sents_by_id.get(sid) or {}).get("text") or "")
            if ref_t and pat.search(ref_t):
                return True
        # 检查相邻两原句句界标点
        s_list = sorted(sids)
        if len(s_list) >= 2:
            for sid in s_list:
                ref_t = str((sents_by_id.get(sid) or {}).get("text") or "").strip()
                if ref_t.endswith(("，", "。", "！", "？", "、", "；", ",", ".", "!", "?", ";")):
                    if _plain(ref_t).endswith(tail_chars):
                        return True
        return False

    def _pair_violates(ch_a: Dict[str, Any], ch_b: Dict[str, Any]) -> bool:
        ta = _plain(ch_a.get("text"))
        tb = _plain(ch_b.get("text"))
        if not ta or not tb:
            return False
        gap = float(ch_b.get("start_sec") or 0.0) - float(ch_a.get("end_sec") or 0.0)
        if gap > 0.85:
            return False
        same_sent = bool(_sids_of(ch_a) & _sids_of(ch_b))
        has_punct = _has_transcript_punct(ch_a, ch_b, ta, tb)
        # 1) 保护词 / 数词量词 / 混合英文连字符词跨界腰斩
        if not has_punct:
            joint_full = ta + tb
            cut_k = len(ta)
            for tm in all_prot:
                for m in re.finditer(re.escape(tm), joint_full):
                    if m.start() < cut_k < m.end():
                        return True
            for sp_s, sp_e in iter_num_unit_spans(joint_full):
                if sp_s < cut_k < sp_e:
                    return True
            for pat_tok in (_ALNUM_MIXED_TOKEN_RE, _HYPHEN_ASCII_TOKEN_RE):
                for m in pat_tok.finditer(joint_full):
                    if m.start() < cut_k < m.end():
                        return True
        # 2) 行首助词
        if (
            tb[0] in _BAD_SUB_HEAD_AUX
            and not (tb[0] == "呢" and _is_cantonese_ne_demo_head(tb))
            and not tb.startswith(_SEM_AUX_HEAD_ALLOW)
        ):
            return True
        # 3) 介词/助词/指示代词/多字修饰语挂尾
        if not has_punct:
            if ta[-1] in _SEM_STRICT_TAIL_CHARS:
                return True
            if ta[-1] == "呢" and (same_sent or gap <= 0.15) and _is_cantonese_ne_demo_head("呢" + tb):
                return True
            if ta[-1] == "哪" and (same_sent or gap <= 0.15) and tb[0] in "个间批啲种边排次条位家套份款张只度里些样":
                return True
            if ta.endswith(_SEM_MULTI_MOD_TAILS):
                if not (
                    ta.endswith(("好多", "有啲"))
                    and (
                        is_degree_complement_haoduo(ta, tb, prot_terms=all_prot)
                        or is_independent_svo_clause(tb, prot_terms=all_prot, prev_plain=ta)
                    )
                ):
                    return True
            if (
                gap <= 0.25
                and len(ta) >= 4
                and ta[-1] in {"我", "你", "他", "她", "它", "佢"}
                and not any(ta[:-1].endswith(k) for k in _PRON_TAIL_KEEP_PREV)
                and not tb.startswith(("的", "嘅", "哋", "们", "自己", "也", "都"))
            ):
                return True
            if (
                gap <= 0.25
                and len(ta) >= 4
                and not tb.startswith(("的", "嘅", "哋", "们"))
                and any(
                    ta.endswith(("好多" + _pr, "有啲" + _pr))
                    and (
                        is_degree_complement_haoduo(ta[: -len(_pr)], _pr + tb, prot_terms=all_prot)
                        or _is_pronoun_led_clause(_pr + tb, prot_terms=all_prot)
                    )
                    for _pr in sorted(_PERSONAL_CLAUSE_PRONOUNS, key=len, reverse=True)
                )
            ):
                return True
        # 4) 定语「的/嘅」与中心语断开、动貌「了/咗/着/过」与宾语数量断开
        if (same_sent or gap <= 0.12) and not has_punct:
            if ta[-1] in {"的", "嘅"} and not ta.endswith(("出来的", "起来的", "下去的", "过来的", "过去的")):
                pairs_jn = _pseg_cut_clean(ta + tb, prot_terms=all_prot)
                pos_acc = 0
                pa_j = ""
                for wp, fp in pairs_jn:
                    if pos_acc == len(ta):
                        pa_j = _normalize_pos_tag(wp, fp)
                        break
                    pos_acc += len(wp)
                if (
                    pa_j in {"n", "nr", "ns", "nt", "nz", "vn", "s", "f", "m", "mq", "q", "a", "i", "l"}
                    or (pa_j == "v" and _is_v_noun_deg_predicate(tb, prot_terms=all_prot))
                ) and not (
                    is_independent_svo_clause(tb, prot_terms=all_prot, prev_plain=ta)
                    or is_predicative_after_headless_de(ta, tb, prot_terms=all_prot)
                ):
                    return True
            if ta[-1] in {"了", "咗", "着", "过"} and not ta.endswith(("出来了", "起来了", "下去了", "回来了", "过去了", "进来了", "过来了")):
                pairs_jn = _pseg_cut_clean(ta + tb, prot_terms=all_prot)
                pos_acc = 0
                pa_j = ""
                for wp, fp in pairs_jn:
                    if pos_acc == len(ta):
                        pa_j = _normalize_pos_tag(wp, fp)
                        break
                    pos_acc += len(wp)
                nxt_head_cl = _CLAUSE_BOUND_SPLIT_RE.split(tb, 1)[0]
                b_a_np = bool(
                    pa_j in {"b", "a"}
                    and not _is_completed_le_clause(ta, prot_terms=all_prot)
                    and (
                        is_compound_np_body(nxt_head_cl, prot_terms=all_prot, prev_plain=ta)
                        or (pa_j == "b" and not _is_v_noun_deg_predicate(nxt_head_cl, prot_terms=all_prot))
                    )
                )
                if (pa_j in {"m", "mq", "q", "n", "nr", "ns", "nt", "nz", "vn"} or b_a_np) and not is_independent_svo_clause(tb, prot_terms=all_prot, prev_plain=ta):
                    nxt_pairs = _pseg_cut_clean(tb, prot_terms=all_prot)
                    first_len = len(nxt_pairs[0][0]) if nxt_pairs else 0
                    first_pos = _normalize_pos_tag(nxt_pairs[0][0], nxt_pairs[0][1]) if nxt_pairs else ""
                    is_heavy = (
                        len(tb) >= 8
                        and ("的" in tb[2:] or "嘅" in tb[2:])
                        and not tb.endswith(("的", "嘅"))
                        and (
                            (
                                first_pos in {"m", "mq", "q"}
                                and len(nxt_pairs) >= 2
                                and nxt_pairs[1][0] in {"的", "嘅"}
                                and ((len(ta) >= 3 and first_len >= 4) or (len(ta) >= 5 and first_len >= 3))
                            )
                            or (first_pos not in {"m", "mq", "q"} and len(ta) >= 3 and first_len >= 4)
                        )
                    )
                    if not is_heavy:
                        return True
        if same_sent and gap <= 0.35 and (len(ta) < MIN_CHUNK_CHARS or len(tb) < MIN_CHUNK_CHARS):
            return True
        return False

    bad_sids: set[str] = set()
    for idx_c, ch in enumerate(sem_chunks):
        raw_c = str(ch.get("text") or "").strip()
        ta_c = _plain(raw_c)
        if ta_c and len(ta_c) >= 6 and re.search(r"(第[一二三四五六七八九十两\d]+)$", ta_c):
            if not raw_c.endswith(("。", "？", "！", "?", "!")):
                bad_sids.update(_sids_of(ch))
        if re.search(r"(?:[嘅的]\s+|[嘅的][，、；：,;:]\s*|[，、；：,;:]\s*)[佢他她我你]$", raw_c):
            bad_sids.update(_sids_of(ch))
        if idx_c + 1 < len(sem_chunks) and _pair_violates(ch, sem_chunks[idx_c + 1]):
            bad_sids.update(_sids_of(ch))
            bad_sids.update(_sids_of(sem_chunks[idx_c + 1]))

    if not bad_sids:
        for ch in sem_chunks:
            ch.setdefault("semantic_curated", True)
        return sem_chunks

    merged_sentences = rebalance_asr_sentences(sents_orig, max_chars=tpl_max_chars)
    changed = True
    while changed:
        changed = False
        for ch in sem_chunks:
            cs = _sids_of(ch)
            if (cs & bad_sids) and not (cs <= bad_sids):
                bad_sids.update(cs)
                changed = True
        for ms in merged_sentences:
            ms_sids = _sids_of(ms)
            if (ms_sids & bad_sids) and not (ms_sids <= bad_sids):
                bad_sids.update(ms_sids)
                changed = True

    kept_chunks: List[Dict[str, Any]] = []
    for ch in sem_chunks:
        if not (_sids_of(ch) & bad_sids):
            ch_copy = dict(ch)
            ch_copy.setdefault("semantic_curated", True)
            kept_chunks.append(ch_copy)

    repaired_chunks: List[Dict[str, Any]] = []
    for ms in merged_sentences:
        if not (_sids_of(ms) & bad_sids):
            continue
        sent_eff = dict(ms)
        if script_lines_punct:
            sent_eff["text"] = _restore_script_punctuation(str(sent_eff.get("text") or ""), script_lines_punct)
        s_txt_full = str(sent_eff.get("text") or "")
        raw_s_kws = list(sent_eff.get("keywords") or [])
        if raw_pain_kws:
            for pk in raw_pain_kws:
                if pk in s_txt_full and pk not in raw_s_kws:
                    raw_s_kws.append(pk)
        cleaned_kws = _clean_display_keywords(raw_s_kws, s_txt_full)
        sent_eff["keywords"] = cleaned_kws
        if isinstance(sent_eff.get("display_keywords"), list):
            sent_eff["display_keywords"] = _clean_display_keywords(sent_eff["display_keywords"], s_txt_full) or cleaned_kws
        if sent_eff.get("words"):
            sent_eff["words"] = _paint_keyword_flags(sent_eff["words"], cleaned_kws)
        sub_chunks = _merge_short_chunks(
            list(split_sentence_into_chunks(
                sent_eff,
                max_chars=chunk_max_chars,
                font_path=font_path,
                font_size=chunk_fs,
                max_safe_px=chunk_px,
                strip_punct=strip_punct,
                protected_terms=list(prot_terms),
            )),
            max_px=chunk_px,
            font_path=font_path,
            font_size=chunk_fs,
            protected_terms=list(prot_terms),
        )
        for sch in sub_chunks:
            rep_ch = dict(sch)
            rep_ch["sentence_id"] = str(ms.get("id") or "")
            if ms.get("merged_sentence_ids"):
                rep_ch["merged_sentence_ids"] = list(ms["merged_sentence_ids"])
            rep_ch["semantic_curated"] = False
            repaired_chunks.append(rep_ch)

    combined = kept_chunks + repaired_chunks
    combined.sort(key=lambda c: (float(c.get("start_sec") or 0.0), float(c.get("end_sec") or 0.0)))
    return combined or sem_chunks


_ORDINAL_SHORT_SENT_RE = re.compile(r"^第[一二三四五六七八九十两\d]+[条点步个件]?$")
_PRON_TAIL_KEEP_PREV = (
    "包括", "私信", "告诉", "觉得", "帮", "替", "给", "俾", "对", "向",
    "把", "被", "拿", "问", "找", "爱", "信", "懂", "看", "睇", "拍", "跟", "陪",
)
_CROSS_MERGE_STRICT_TAIL = (
    "喺", "在", "好多", "有啲", "呢啲", "𠮶啲", "嗰啲", "呢个", "嗰个", "𠮶个",
    "就系", "就是", "一间", "一个", "一份", "一种", "把", "被", "从", "向", "给", "俾", "和", "与",
    "𠮶种", "嗰种", "这种", "那种", "呢种", "哪种", "每种", "某种",
)
_SUBJECT_PRONOUN_STARTERS = ("我", "你", "他", "她", "它", "佢", "咱", "大家", "其实", "绝对", "所以", "但是", "不过", "而且", "然后", "因为")


def _is_complete_short_predicate(text_plain: str) -> bool:
    """判断 2~4 字短句是否已是完整主谓/感叹短句（如「我好傻」「真系好」），避免与下句错合。"""
    t = str(text_plain or "").strip()
    if not t or len(t) > 4:
        return False
    pairs = _pseg_cut_clean(t)
    if len(pairs) == 2 and pairs[0][1] == "r" and pairs[1][1] in ("a", "v", "ad"):
        return True
    if len(pairs) >= 2 and pairs[0][1] == "r" and pairs[-1][1] in ("a", "ag"):
        return True
    return False


def rebalance_asr_sentences(
    sentences: Sequence[Dict[str, Any]],
    max_chars: int = 9,
) -> List[Dict[str, Any]]:
    """通用跨句 ASR 断句边界修复：
    1) 剥离前句末尾错挂的下句主语代词（如「我系牛哥我」+「花咗…」→「我系牛哥」+「我花咗…」）；
    2) 合并被 ASR 硬切开的跨句定语/助词/介词短语（如「一间六百平」+「嘅店」、「𠮶边就系我哋嘅」+「厨房」），
       同时严防把句末语气「的/嘅」并进以人称代词/副词/连词开头的新句（如「岭南元素嘅」+「佢饮茶嘅氛围」保持分离）。
    """
    rebalanced_sents: List[Dict[str, Any]] = []
    for raw_s in sentences:
        s_copy = dict(raw_s)
        s_copy["words"] = [dict(w) for w in (raw_s.get("words") or [])]
        if rebalanced_sents:
            prev_s = rebalanced_sents[-1]
            p_txt = str(prev_s.get("text") or "").strip()
            c_txt = str(s_copy.get("text") or "").strip()
            gap = float(s_copy.get("start_sec", 0.0)) - float(prev_s.get("end_sec", 0.0))
            same_source = str(prev_s.get("source_media") or "") == str(s_copy.get("source_media") or "")
            if (
                same_source
                and -0.08 <= gap < 0.25
                and len(p_txt) >= 4
                and p_txt[-1] in ("我", "你", "他", "佢")
                and not any(p_txt[:-1].endswith(k) for k in _PRON_TAIL_KEEP_PREV)
                and c_txt
                and not c_txt.startswith(("的", "嘅", "哋", "们", "自己", "也", "都"))
            ):
                tail_ch = p_txt[-1]
                prev_s["text"] = p_txt[:-1]
                s_copy["text"] = tail_ch + c_txt
                p_ws = list(prev_s.get("words") or [])
                if p_ws and str(p_ws[-1].get("word") or "").strip() == tail_ch:
                    moved_w = p_ws.pop()
                    prev_s["words"] = p_ws
                    if p_ws:
                        prev_s["end_sec"] = float(p_ws[-1].get("end_sec", prev_s["end_sec"]))
                    s_copy["words"] = [moved_w] + list(s_copy.get("words") or [])
                    s_copy["start_sec"] = min(float(s_copy.get("start_sec", 0.0)), float(moved_w.get("start_sec", s_copy["start_sec"])))
        rebalanced_sents.append(s_copy)

    _SPLIT_COMMA_RE = re.compile(r"(?<!\d)[，、；,;]|(?<=\d)[，、；;]|(?<=\d),(?!\d{3}(?!\d))")

    def _split_sent_at_comma(
        sent_obj: Dict[str, Any], *, split_last: bool = False, min_head_len: int = 2
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        raw_t = str(sent_obj.get("text") or "").strip()
        if not raw_t:
            return None
        matches = list(_SPLIT_COMMA_RE.finditer(raw_t))
        if not matches:
            return None
        chosen = matches[-1] if split_last else matches[0]
        cut_pos = chosen.end()
        head_t = raw_t[:cut_pos].strip()
        rest_t = raw_t[cut_pos:].strip()
        head_clean = strip_display_punctuation(head_t).replace(" ", "")
        rest_clean = strip_display_punctuation(rest_t).replace(" ", "")
        if len(head_clean) < max(1, int(min_head_len)) or len(rest_clean) < 2:
            return None
        s0 = float(sent_obj.get("start_sec", 0.0))
        e0 = float(sent_obj.get("end_sec", s0))
        ws = [dict(w) for w in (sent_obj.get("words") or [])]
        head_ws: List[Dict[str, Any]] = []
        rest_ws: List[Dict[str, Any]] = []
        ws_ok = False
        if ws:
            acc_c = 0
            target_c = len(head_clean)
            for w_it in ws:
                wt = str(w_it.get("word") or "")
                wc = strip_display_punctuation(wt).replace(" ", "")
                if acc_c < target_c:
                    if acc_c + len(wc) <= target_c:
                        head_ws.append(dict(w_it))
                        acc_c += len(wc)
                    else:
                        need = target_c - acc_c
                        seen_c = 0
                        cut_w = len(wt)
                        for idx_ch, ch_w in enumerate(wt):
                            if strip_display_punctuation(ch_w).replace(" ", ""):
                                seen_c += 1
                                if seen_c == need:
                                    cut_w = idx_ch + 1
                                    break
                        ws_t = float(w_it.get("start_sec", s0))
                        we_t = float(w_it.get("end_sec", e0))
                        mid_t = round(ws_t + (we_t - ws_t) * (need / float(max(1, len(wc)))), 4)
                        wl = dict(w_it, word=wt[:cut_w], end_sec=mid_t)
                        wr = dict(w_it, word=wt[cut_w:], start_sec=mid_t)
                        if wl["word"]:
                            head_ws.append(wl)
                        if wr["word"]:
                            rest_ws.append(wr)
                        acc_c = target_c
                elif acc_c == target_c and not rest_ws and not wc:
                    head_ws.append(dict(w_it))
                else:
                    rest_ws.append(dict(w_it))
            if acc_c == target_c and head_ws and rest_ws:
                ws_ok = True
        if ws_ok:
            h_st = float(head_ws[0].get("start_sec", s0))
            h_en = max(h_st, float(head_ws[-1].get("end_sec", e0)))
            r_st = max(h_en, float(rest_ws[0].get("start_sec", h_en)))
            r_en = max(r_st, float(rest_ws[-1].get("end_sec", e0)))
        else:
            ratio = max(0.1, min(0.9, len(head_clean) / float(max(1, len(head_clean) + len(rest_clean)))))
            split_t = round(s0 + (e0 - s0) * ratio, 4)
            h_st, h_en = s0, split_t
            r_st, r_en = split_t, max(split_t, e0)
            head_ws, rest_ws = [], []
        head_obj = dict(sent_obj, text=head_t, start_sec=round(h_st, 4), end_sec=round(h_en, 4), words=head_ws)
        rest_obj = dict(sent_obj, text=rest_t, start_sec=round(r_st, 4), end_sec=round(r_en, 4), words=rest_ws)
        return head_obj, rest_obj

    def _check_tail_merge(
        p_seg_txt: str,
        c_seg_txt: str,
        p_seg_dur: float,
        *,
        enforce_len: bool = True,
    ) -> Tuple[bool, bool, bool]:
        p_seg_txt = str(p_seg_txt or "").strip()
        c_seg_txt = str(c_seg_txt or "").strip()
        p_seg_plain = strip_display_punctuation(p_seg_txt).replace(" ", "")
        c_seg_plain = strip_display_punctuation(c_seg_txt).replace(" ", "")
        p_tail_clause = _CLAUSE_BOUND_SPLIT_RE.split(p_seg_txt)[-1].strip()
        p_tail_plain = strip_display_punctuation(p_tail_clause).replace(" ", "")
        c_head_clause = _CLAUSE_BOUND_SPLIT_RE.split(c_seg_txt, 1)[0].strip()
        c_head_plain = strip_display_punctuation(c_head_clause).replace(" ", "")
        pair_len = len(p_seg_txt) + len(c_seg_txt)
        eff_len_ok = (pair_len <= 44) if enforce_len else True

        c_pairs = [
            (wp, _normalize_pos_tag(wp, fp))
            for wp, fp in (_pseg_cut_clean(c_seg_plain) if c_seg_plain else [])
        ]
        c_head_pos = c_pairs[0][1] if c_pairs else ""
        _de_indices = [idx_p for idx_p, (wp, _fp) in enumerate(c_pairs) if wp in ("的", "嘅")]
        _last_de_i = _de_indices[-1] if _de_indices else -1
        _c_is_de_rel_np = bool(
            len(c_pairs) >= 3
            and 1 <= _last_de_i < len(c_pairs) - 1
            and not c_seg_plain.startswith(_SUBJECT_PRONOUN_STARTERS)
            and c_pairs[0][1] in ("v", "vn", "vg", "d", "a", "ad", "b", "p")
            and all(fp in ("n", "nr", "ns", "nt", "nz", "vn", "s", "f", "l", "i", "j", "a", "b", "m", "mq", "q") for _wp, fp in c_pairs[_last_de_i + 1 :])
            and c_pairs[-1][1] in ("n", "nr", "ns", "nt", "nz", "vn", "s", "f", "l", "i", "j")
            and c_pairs[-1][0][-1] not in ("的", "嘅", "了", "咗", "呢", "吗", "吧", "啊")
        )
        _quant_head_prefixes = ("好多", "不少", "很多", "大量", "大批", "一堆", "一群", "几批", "各种", "各类", "所有", "全部")
        _c_has_quant_head = bool(
            c_seg_plain.startswith(_quant_head_prefixes)
            and len(c_seg_plain) <= 12
            and (not enforce_len or pair_len <= 36)
            and not any(fp in ("v", "vg", "vd", "d", "ad", "p", "c", "r", "ul", "y") for _wp, fp in c_pairs[1:])
        )
        _c_has_clause_marker = any(fp in ("d", "ad", "p", "c", "r", "ul", "uj", "y") for _wp, fp in c_pairs)
        _c_is_svo_clause = is_independent_svo_clause(c_seg_plain, prev_plain=p_tail_plain or p_seg_plain)
        _c_is_pure_np_body = is_compound_np_body(c_seg_plain, max_len=16, prev_plain=p_tail_plain or p_seg_plain)
        _c_is_compound_np = bool(_c_is_pure_np_body and (not enforce_len or pair_len <= 36))
        _c_is_one_char_nom = bool(
            len(c_seg_plain) == 1
            and _is_one_char_nominal_object(p_tail_plain or p_seg_plain, c_seg_plain)
        )
        _c_is_pure_short_np = bool(
            (
                not _c_is_svo_clause
                and (c_head_pos in ("n", "nr", "ns", "nt", "nz", "vn") or _c_is_one_char_nom)
                and (len(c_seg_plain) >= 2 or _c_is_one_char_nom)
                and len(c_seg_plain) <= 6
                and not any(fp.startswith("v") for _wp, fp in c_pairs[1:])
            )
            or _c_is_compound_np
            or _c_has_quant_head
        )
        _aspect_dir_allow = ("出来了", "起来了", "下去了", "回来了", "过去了", "进来了", "过来了")
        _c_is_temporal_adv = bool(re.match(
            r"^(?:[零〇一二两三四五六七八九十百千万亿\d]+(?:年|个月|周|天|小时|分钟|秒)(?:后|前|以后|以前|之后|之前)|[一二][零〇九]\d{2}年)",
            c_seg_plain,
        ))
        _c_is_num_head = bool(
            not _c_is_temporal_adv
            and (c_head_pos in ("m", "mq", "q") or _NUM_UNIT_PRESPLIT_RE.match(c_seg_plain))
        )
        _c_is_num_head_np = bool(
            _c_is_num_head
            and len(c_seg_plain) <= 16
            and not _c_has_clause_marker
            and not _c_is_svo_clause
            and (
                len(c_pairs) == 1
                or (
                    len(c_pairs) >= 2
                    and (
                        c_pairs[-1][1] in ("n", "nr", "ns", "nt", "nz", "vn", "s", "l", "j")
                        or (c_pairs[-1][1] == "v" and c_pairs[-1][0][-1] in _NOMINAL_TECH_TAIL_CHARS)
                    )
                )
            )
        )

        genitive_tm = False
        if (
            p_seg_txt.endswith(("嘅", "的"))
            and not p_seg_plain.endswith(("出来的", "起来的", "下去的", "过来的", "过去的"))
            and c_seg_plain
            and eff_len_ok
        ):
            if (
                c_seg_plain.startswith(_SUBJECT_PRONOUN_STARTERS)
                or c_head_pos in ("r", "d", "c", "y", "e")
                or (c_seg_plain[0] in _DP_NO_LEAD_CHARS and not (c_seg_plain[0] == "呢" and _is_cantonese_ne_demo_head(c_seg_plain)))
                or _c_is_svo_clause
                or is_predicative_after_headless_de(p_tail_plain or p_seg_plain, c_head_plain or c_seg_plain)
            ):
                genitive_tm = False
            elif (len(c_seg_plain) >= 2 or _c_is_one_char_nom) and (
                len(p_tail_plain or p_seg_txt) <= 8
                or len(c_seg_plain) <= 4
                or _c_is_de_rel_np
                or _c_is_pure_np_body
                or _c_has_quant_head
                or _c_is_num_head_np
                or (p_seg_dur < 3.2 and c_head_pos in ("n", "nr", "ns", "nt", "nz", "vn", "s", "f", "a", "b", "m", "mq", "q", "i", "l"))
            ):
                genitive_tm = True
        aspect_tm = bool(
            p_seg_txt.endswith(("了", "咗", "着", "过"))
            and not p_seg_plain.endswith(_aspect_dir_allow)
            and c_seg_plain
            and eff_len_ok
            and not c_seg_plain.startswith(_SUBJECT_PRONOUN_STARTERS)
            and not _ORDINAL_SHORT_SENT_RE.match(c_seg_plain)
            and not _CLAUSE_START_PROTECT_RE.match(c_seg_plain)
            and not _c_is_svo_clause
            and not (len(c_seg_plain) == 1 and _is_completed_le_clause(p_tail_plain or p_seg_plain))
            and (c_head_pos not in ("r", "d", "c", "p", "u") or _c_has_quant_head)
            and (_c_is_num_head or _c_is_pure_short_np)
        )
        broken_tm = bool(
            (c_seg_plain.startswith(("嘅", "的", "地", "得", "咗", "了", "着", "过")) and eff_len_ok)
            or (
                any(
                    p_seg_txt.endswith(t)
                    and not (t in ("好多", "有啲") and is_degree_complement_haoduo(p_tail_plain or p_seg_plain, c_head_plain or c_seg_plain))
                    for t in _CROSS_MERGE_STRICT_TAIL
                )
                and eff_len_ok
            )
            or genitive_tm
            or aspect_tm
        )
        return genitive_tm, aspect_tm, broken_tm

    merged_sentences: List[Dict[str, Any]] = []
    for s_copy in rebalanced_sents:
        if merged_sentences:
            prev_s = merged_sentences[-1]
            p_txt = str(prev_s.get("text") or "").strip()
            c_txt = str(s_copy.get("text") or "").strip()
            p_plain = strip_display_punctuation(p_txt).replace(" ", "")
            c_plain = strip_display_punctuation(c_txt).replace(" ", "")
            p_dur = float(prev_s.get("end_sec", 0.0)) - float(prev_s.get("start_sec", 0.0))
            c_dur = float(s_copy.get("end_sec", 0.0)) - float(s_copy.get("start_sec", 0.0))
            gap = float(s_copy.get("start_sec", 0.0)) - float(prev_s.get("end_sec", 0.0))
            same_source = str(prev_s.get("source_media") or "") == str(s_copy.get("source_media") or "")

            genitive_tail_merge, aspect_tail_merge, is_broken_phrase = _check_tail_merge(p_txt, c_txt, p_dur)
            is_short_stub = (
                (p_dur < 0.85 or c_dur < 0.85)
                and len(p_txt) + len(c_txt) <= max_chars * 2 + 4
                and not _ORDINAL_SHORT_SENT_RE.match(p_plain)
                and not _ORDINAL_SHORT_SENT_RE.match(c_plain)
                and not _is_complete_short_predicate(p_plain)
            )
            if (
                same_source
                and (is_broken_phrase or is_short_stub)
                and -0.08 <= gap < (0.25 if is_broken_phrase else 0.16)
                and p_txt
                and c_plain
                and not _CLAUSE_START_PROTECT_RE.match(c_plain)
                and not p_txt.endswith(("。", "？", "！", "?", "!"))
            ):
                mids = list(prev_s.get("merged_sentence_ids") or ([prev_s["id"]] if prev_s.get("id") else []))
                if s_copy.get("id") and s_copy["id"] not in mids:
                    mids.append(s_copy["id"])
                prev_s["merged_sentence_ids"] = mids
                prev_s["text"] = p_txt + c_txt
                prev_s["end_sec"] = max(float(prev_s.get("end_sec", 0.0)), float(s_copy.get("end_sec", 0.0)))
                prev_s["words"] = list(prev_s.get("words") or []) + list(s_copy.get("words") or [])
                prev_s["keywords"] = list(dict.fromkeys(list(prev_s.get("keywords") or []) + list(s_copy.get("keywords") or [])))
                continue
            if (
                same_source
                and -0.08 <= gap < 0.25
                and p_txt
                and c_plain
                and not p_txt.endswith(("。", "？", "！", "?", "!"))
                and not _CLAUSE_START_PROTECT_RE.match(c_plain)
                and (len(p_txt) + len(c_txt) > 44 or bool(_SPLIT_COMMA_RE.search(c_txt)) or bool(_SPLIT_COMMA_RE.search(p_txt)))
                and p_txt.endswith(("了", "咗", "着", "过", "的", "嘅"))
            ):
                split_c = _split_sent_at_comma(s_copy, split_last=False, min_head_len=1)
                handled_multi = False
                if split_c is not None:
                    c_head_obj, c_rest_obj = split_c
                    _, _, c_head_broken = _check_tail_merge(p_txt, c_head_obj["text"], p_dur, enforce_len=False)
                    if c_head_broken and len(p_txt) + len(c_head_obj["text"]) <= 44:
                        mids = list(prev_s.get("merged_sentence_ids") or ([prev_s["id"]] if prev_s.get("id") else []))
                        if s_copy.get("id") and s_copy["id"] not in mids:
                            mids.append(s_copy["id"])
                        prev_s["merged_sentence_ids"] = mids
                        prev_s["text"] = p_txt + c_head_obj["text"]
                        prev_s["end_sec"] = max(float(prev_s.get("end_sec", 0.0)), float(c_head_obj.get("end_sec", 0.0)))
                        prev_s["words"] = list(prev_s.get("words") or []) + list(c_head_obj.get("words") or [])
                        s_copy = c_rest_obj
                        handled_multi = True
                    elif c_head_broken:
                        split_p = _split_sent_at_comma(prev_s, split_last=True)
                        if split_p is not None:
                            p_head_obj, p_tail_obj = split_p
                            p_tail_dur = max(0.2, float(p_tail_obj["end_sec"]) - float(p_tail_obj["start_sec"]))
                            _, _, pt_broken = _check_tail_merge(p_tail_obj["text"], c_head_obj["text"], p_tail_dur)
                            if pt_broken and len(p_tail_obj["text"]) + len(c_head_obj["text"]) <= 44:
                                mids = list(prev_s.get("merged_sentence_ids") or ([prev_s["id"]] if prev_s.get("id") else []))
                                if s_copy.get("id") and s_copy["id"] not in mids:
                                    mids.append(s_copy["id"])
                                p_tail_merged = dict(
                                    p_tail_obj,
                                    merged_sentence_ids=mids,
                                    text=p_tail_obj["text"] + c_head_obj["text"],
                                    end_sec=max(float(p_tail_obj["end_sec"]), float(c_head_obj["end_sec"])),
                                    words=list(p_tail_obj.get("words") or []) + list(c_head_obj.get("words") or []),
                                    keywords=list(dict.fromkeys(list(prev_s.get("keywords") or []) + list(s_copy.get("keywords") or []))),
                                )
                                merged_sentences[-1] = p_head_obj
                                merged_sentences.append(p_tail_merged)
                                s_copy = c_rest_obj
                                handled_multi = True
                if not handled_multi:
                    split_p = _split_sent_at_comma(prev_s, split_last=True)
                    if split_p is not None:
                        p_head_obj, p_tail_obj = split_p
                        p_tail_dur = max(0.2, float(p_tail_obj["end_sec"]) - float(p_tail_obj["start_sec"]))
                        _, _, pt_broken = _check_tail_merge(p_tail_obj["text"], c_txt, p_tail_dur)
                        if pt_broken and len(p_tail_obj["text"]) + len(c_txt) <= 44:
                            mids = list(prev_s.get("merged_sentence_ids") or ([prev_s["id"]] if prev_s.get("id") else []))
                            if s_copy.get("id") and s_copy["id"] not in mids:
                                mids.append(s_copy["id"])
                            p_tail_merged = dict(
                                p_tail_obj,
                                merged_sentence_ids=mids,
                                text=p_tail_obj["text"] + c_txt,
                                end_sec=max(float(p_tail_obj["end_sec"]), float(s_copy.get("end_sec", 0.0))),
                                words=list(p_tail_obj.get("words") or []) + list(s_copy.get("words") or []),
                                keywords=list(dict.fromkeys(list(prev_s.get("keywords") or []) + list(s_copy.get("keywords") or []))),
                            )
                            merged_sentences[-1] = p_head_obj
                            merged_sentences.append(p_tail_merged)
                            continue
        merged_sentences.append(s_copy)
    return merged_sentences


def run_step5(
    pipeline_dir: Path,
    track: str = "store",
    template_id: Optional[str] = None,
    enable_v6_director: Optional[bool] = None,
) -> Dict[str, Any]:
    audio_events = read_json(pipeline_dir / "03_audio_events.json")
    edl_p = pipeline_dir / "04_edl.json"
    edl = read_json(edl_p) if edl_p.exists() else {"clips": []}
    man_p = pipeline_dir / "01_manifest.json"
    manifest = read_json(man_p) if man_p.exists() else {"items": []}

    # 获取每段规范素材在 1080x1920 裁切后画布的精确像素人脸框（兼容归一化与像素坐标系）
    faces_by_mp4: Dict[str, List[Dict[str, int]]] = {}
    for it in manifest.get("items", []):
        mp = str(it.get("normalized_mp4", ""))
        f_list = parse_manifest_faces(it)
        if mp and f_list:
            faces_by_mp4[mp] = f_list

    code_ranges: List[Tuple[float, float, str]] = [
        (float(c["timeline_in_sec"]), float(c["timeline_out_sec"]), str(c["clip_id"]))
        for c in edl.get("clips", [])
        if c.get("track_role") == "code_broll"
    ]
    templates = load_subtitle_templates()
    if not template_id or template_id not in templates:
        template_id = "store_bold_yellow" if track == "store" else "creator_clean_white"
    tpl = templates[template_id]
    # 字体优先级：源模版原版字体（放 assets/style_spectrum/fonts/<style_id>/）> 可商用替代
    source_font_family = tpl.get("source_font_family") or (tpl.get("font_substitution") or {}).get("source_font")
    source_font = find_source_font(source_font_family, template_id, tpl.get("font_candidates"))
    exact_font = bool(source_font)
    font_path = source_font or find_commercial_font(tpl.get("font_candidates"), verified_only=True) or find_commercial_font()
    strip_punct = bool(tpl.get("punct_stripped", True))

    entries: List[Dict[str, Any]] = []
    ass_dialogues: List[str] = []
    kw_hex_bgr = tpl["keyword_color"].lstrip("#")
    ass_kw_color = f"&H00{kw_hex_bgr[4:6]}{kw_hex_bgr[2:4]}{kw_hex_bgr[0:2]}&"

    raw_intel_p = pipeline_dir / "00_raw_intel.json"
    raw_pain_kws = set(str(k) for k in ((read_json(raw_intel_p).get("pain_keywords") or []) if raw_intel_p.is_file() else []) if str(k).strip())

    # 切分前即加载 terms.txt 与原材关键词作为 DP 保护词，从源头禁止把专有名词/品牌词切碎
    # 2026-11-24 实测：术语表在素材侧 `_SVS工作区/术语表.txt`，而这里只找 pipeline/terms.txt，
    # 于是「金湾」「点赞茶楼」「卡座」「中空」全被切碎，字幕在词中间断行。
    prot_terms = load_protected_terms(_terms_file_for(pipeline_dir), extra_terms=list(raw_pain_kws))

    _kinetic_stage_suppressed = False
    try:
        from kinetic_choreographer import is_kinetic_stage_style
        if enable_v6_director is not None:
            _v6_sub_flag = bool(enable_v6_director)
        else:
            _dp_path = Path(pipeline_dir) / "03b_director_plan.json"
            if _dp_path.is_file():
                _dp_doc = read_json(_dp_path)
                _v6_sub_flag = not bool(_dp_doc.get("native_style_mode"))
            else:
                _v6_sub_flag = False
        _kinetic_stage_suppressed = bool(is_kinetic_stage_style(template_id, enable_v6_director=_v6_sub_flag))
    except Exception:
        _kinetic_stage_suppressed = False

    idx = 1
    suppressed_count = 0
    # 合并 V10.0 真母本常规字幕覆写（如 style_15 常规字幕实为精致 58px/10字而非开场 94px/4字拼音巨标），
    # 同时严格保证常规字幕单行上限 >= 8 字（数学防死锁，杜绝 max_chars<=7 强制拆出 <=3 字残句）
    _truth_sub_patch: Dict[str, Any] = {}
    if not _kinetic_stage_suppressed:
        try:
            from style_visual_truth import get_visual_truth
            _truth_sub_patch = dict(get_visual_truth(template_id).get("subtitle_patch") or {})
        except Exception:
            _truth_sub_patch = {}
    if _truth_sub_patch:
        tpl = {**tpl, **_truth_sub_patch}
        kw_hex_bgr = str(tpl.get("keyword_color") or "#FFE28A").lstrip("#")
        if len(kw_hex_bgr) >= 6:
            ass_kw_color = f"&H00{kw_hex_bgr[4:6]}{kw_hex_bgr[2:4]}{kw_hex_bgr[0:2]}&"
    tpl_char_cap = max(8, int(tpl.get("max_chars_hard", 96)))
    base_fs = int(_truth_sub_patch.get("font_size") or tpl.get("font_size", 62))
    _line_px = SUBTITLE_SYMMETRIC_MAX_W - 8
    _chars_by_font = max(4, int(SUBTITLE_SYMMETRIC_MAX_W // max(1, base_fs)))
    _cfg_max_chars = int(_truth_sub_patch.get("max_chars_per_line") or tpl.get("max_chars_per_line", 10))
    tpl_max_chars = max(4, min(tpl_char_cap, _cfg_max_chars, _chars_by_font))
    kw_scale_f = float(_truth_sub_patch.get("keyword_scale") or tpl.get("keyword_scale", 1.133))
    eff_measure_fs = base_fs
    dual_banner = bool(tpl.get("dual_line_banner"))
    def _faces_for_window(st_s: float, en_s: float) -> List[Dict[str, Any]]:
        out_f: List[Dict[str, Any]] = []
        for c in edl.get("clips", []):
            if float(c["timeline_in_sec"]) < en_s and float(c["timeline_out_sec"]) > st_s:
                out_f.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
        return out_f

    if _kinetic_stage_suppressed:
        # 动态舞台模式下由 kinetic_stage 承担画面渲染（如 style_02_unified L1 ~69px@640px<=9字），
        # ASS 断词尺度保持 54px/9 字，兼顾分词整洁与舞台画框不溢出缩字。
        chunk_fs = min(eff_measure_fs, 54)
        chunk_px = _line_px
        chunk_max_chars = min(tpl_char_cap, 9 if template_id == "style_02" else 10)
    else:
        chunk_fs = min(84, max(62, eff_measure_fs)) if dual_banner else min(eff_measure_fs, max(54, int((_line_px - 36) // 10)))
        chunk_px = int(_line_px * 1.85) if dual_banner else _line_px
        chunk_max_chars = max(8, tpl_max_chars, min(tpl_char_cap, max(8, _cfg_max_chars))) if dual_banner else max(9, tpl_max_chars)

    # 加载 02c_script_final.json 成稿软标点，供无标点口播段回贴自然逗号断点
    script_lines_punct: List[str] = []
    sc_p = pipeline_dir / "02c_script_final.json"
    if sc_p.is_file():
        try:
            sc_doc = read_json(sc_p)
            script_lines_punct = [
                str(ln.get("text") or "").strip()
                for ln in (sc_doc.get("lines") or [])
                if isinstance(ln, dict) and str(ln.get("text") or "").strip()
            ]
        except Exception:
            script_lines_punct = []

    # V11.0 大模型语义精修字幕直通检查：若项目含合法 `05a_semantic_subtitles.json` 或 `director_plan.semantic_subtitles`，
    # 直接使用人类剪辑师级精修语义分句 + 原话逐字毫秒级对齐时间戳；但对任何拆破保护词或出现挂尾/动貌割裂的句子立即回退重切。
    _sem_override_chunks = _load_semantic_subtitle_override(pipeline_dir, audio_events, strip_punct=strip_punct)
    if _sem_override_chunks:
        _sem_override_chunks = _validate_and_repair_sem_override_chunks(
            _sem_override_chunks,
            audio_events,
            prot_terms=prot_terms,
            tpl_max_chars=tpl_max_chars,
            chunk_max_chars=chunk_max_chars,
            chunk_fs=chunk_fs,
            chunk_px=chunk_px,
            font_path=font_path,
            strip_punct=strip_punct,
            script_lines_punct=script_lines_punct,
            raw_pain_kws=raw_pain_kws,
        )
        for ch in _sem_override_chunks:
            st_sec = round(float(ch["start_sec"]), 4)
            en_sec = round(float(ch["end_sec"]), 4)
            sub_dur = max(0.05, en_sec - st_sec)
            active_faces = []
            for c in edl.get("clips", []):
                if float(c["timeline_in_sec"]) < en_sec and float(c["timeline_out_sec"]) > st_sec:
                    active_faces.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))
            ch_kws = [k for k in (ch.get("keywords") or []) if str(k).strip() and str(k).strip() in str(ch["text"])]
            final_bbox, face_avoid_dy = _compute_authoritative_subtitle_bbox(
                ch["text"], tpl, font_path, active_faces, keywords=ch_kws, terms=prot_terms,
            )
            safe_ok, safe_msg = check_bbox_in_safe_zone(final_bbox)
            suppressed = False
            suppressed_by = None
            suppress_reason = None
            for cs, ce, cid in code_ranges:
                ov = max(0.0, min(en_sec, ce) - max(st_sec, cs))
                if ov / sub_dur >= 0.60:
                    suppressed = True
                    suppressed_by = cid
                    suppress_reason = "B9_code_broll_dual_channel_dedup"
                    suppressed_count += 1
                    break
            if _kinetic_stage_suppressed and not suppressed:
                suppressed = True
                suppressed_by = "kinetic_typography_stage"
                suppress_reason = "kinetic_typography_stage"
                suppressed_count += 1
            entry_obj = {
                "id": f"sub_{idx:03d}",
                "sentence_id": ch["sentence_id"],
                "text": ch["text"],
                **({"merged_sentence_ids": ch["merged_sentence_ids"]} if ch.get("merged_sentence_ids") else {}),
                "start_sec": st_sec,
                "end_sec": en_sec,
                "keywords": ch_kws,
                "words": ch["words"],
                "bbox": final_bbox,
                "final_bbox": final_bbox,
                "face_avoid_dy": int(face_avoid_dy),
                "safe_zone_ok": safe_ok,
                "safe_msg": safe_msg,
                "suppressed": suppressed,
                "semantic_curated": bool(ch.get("semantic_curated", True)),
            }
            if ch.get("display_lines") and len(ch["display_lines"]) >= 2:
                entry_obj["display_lines"] = list(ch["display_lines"][:2])
            elif dual_banner and "\n" not in str(ch["text"]) and len(str(ch["text"]).strip()) >= 5:
                dl1, dl2 = split_semantic_two_lines(str(ch["text"]), keywords=ch_kws, terms=prot_terms)
                if dl2:
                    entry_obj["display_lines"] = [dl1, dl2]
            if suppressed:
                entry_obj["suppressed_by"] = suppressed_by
                entry_obj["suppress_reason"] = suppress_reason or "B9_code_broll_dual_channel_dedup"
            entries.append(entry_obj)
            idx += 1

    merged_sentences: List[Dict[str, Any]] = (
        [] if _sem_override_chunks else rebalance_asr_sentences(audio_events["sentences"], max_chars=tpl_max_chars)
    )

    for sent in merged_sentences:
        sent_eff = dict(sent)
        if script_lines_punct:
            sent_eff["text"] = _restore_script_punctuation(str(sent_eff.get("text") or ""), script_lines_punct)
        s_txt_full = str(sent_eff.get("text") or "")
        raw_s_kws = list(sent_eff.get("keywords") or [])
        if raw_pain_kws:
            for pk in raw_pain_kws:
                if pk in s_txt_full and pk not in raw_s_kws:
                    raw_s_kws.append(pk)
        cleaned_kws = _clean_display_keywords(raw_s_kws, s_txt_full)
        sent_eff["keywords"] = cleaned_kws
        if isinstance(sent_eff.get("display_keywords"), list):
            sent_eff["display_keywords"] = _clean_display_keywords(sent_eff["display_keywords"], s_txt_full) or cleaned_kws
        if sent_eff.get("words"):
            sent_eff["words"] = _paint_keyword_flags(sent_eff["words"], cleaned_kws)
        sent_prot = list(prot_terms)
        _chunks = _merge_short_chunks(
            list(split_sentence_into_chunks(
                sent_eff,
                max_chars=chunk_max_chars,
                font_path=font_path,
                font_size=chunk_fs,
                max_safe_px=chunk_px,
                strip_punct=strip_punct,
                protected_terms=sent_prot,
            )),
            max_px=chunk_px,
            font_path=font_path,
            font_size=chunk_fs,
            protected_terms=sent_prot,
        )
        for ch in _chunks:
            st_sec = round(float(ch["start_sec"]), 4)
            en_sec = round(float(ch["end_sec"]), 4)
            sub_dur = max(0.05, en_sec - st_sec)

            # 收集该字幕在时间线上重叠镜头的精确人脸框以执行源头避脸
            active_faces: List[Dict[str, Any]] = []
            for c in edl.get("clips", []):
                if float(c["timeline_in_sec"]) < en_sec and float(c["timeline_out_sec"]) > st_sec:
                    active_faces.extend(faces_by_mp4.get(str(c.get("media_path", "")), []))

            # 仅保留真正出现在本字幕块内的干净关键词（避免整句无关词污染相邻分块）
            raw_ch_kws = ch.get("keywords", sent_eff.get("keywords", []))
            ch_kws = [k for k in raw_ch_kws if str(k).strip() and str(k).strip() in str(ch["text"])]
            final_bbox, face_avoid_dy = _compute_authoritative_subtitle_bbox(
                ch["text"], tpl, font_path, active_faces, keywords=ch_kws, terms=sent_prot,
            )
            safe_ok, safe_msg = check_bbox_in_safe_zone(final_bbox)

            suppressed = False
            suppressed_by = None
            suppress_reason = None
            for cs, ce, cid in code_ranges:
                ov = max(0.0, min(en_sec, ce) - max(st_sec, cs))
                if ov / sub_dur >= 0.60:
                    suppressed = True
                    suppressed_by = cid
                    suppress_reason = "B9_code_broll_dual_channel_dedup"
                    suppressed_count += 1
                    break
            if _kinetic_stage_suppressed and not suppressed:
                suppressed = True
                suppressed_by = "kinetic_typography_stage"
                suppress_reason = "kinetic_typography_stage"
                suppressed_count += 1

            entry_obj: Dict[str, Any] = {
                "id": f"sub_{idx:03d}",
                "sentence_id": sent["id"],
                "text": ch["text"],
                "start_sec": st_sec,
                "end_sec": en_sec,
                "keywords": ch_kws,
                "words": ch["words"],
                "bbox": final_bbox,
                "final_bbox": final_bbox,
                "face_avoid_dy": int(face_avoid_dy),
                "safe_zone_ok": safe_ok,
                "safe_msg": safe_msg,
                "suppressed": suppressed,
            }
            if dual_banner and "\n" not in str(ch["text"]) and len(str(ch["text"]).strip()) >= 5:
                dl1, dl2 = split_semantic_two_lines(str(ch["text"]), keywords=ch_kws, terms=sent_prot)
                if dl2:
                    entry_obj["display_lines"] = [dl1, dl2]
            if sent.get("merged_sentence_ids"):
                entry_obj["merged_sentence_ids"] = list(sent["merged_sentence_ids"])
            if suppressed:
                entry_obj["suppressed_by"] = suppressed_by
                entry_obj["suppress_reason"] = suppress_reason or "B9_code_broll_dual_channel_dedup"

            entries.append(entry_obj)
            idx += 1

    # 跨句连贯助词护栏：消除句末切分留下的下一句首个纯助词（「的/地/得/嘅/啲/嘛/呢/吧/啊/呀」，不含易作动词的「过/了/着」）
    # 严禁把下句动词粘到前句序数词（如「第三」+「过了夫妻店」）；搬移时同步合并 merged_sentence_ids 并防下句变成 <=3 字残句！
    _cross_sent_aux = frozenset("的地得嘅啲嘛呢吧啊呀")
    k_cross = 0 if not _sem_override_chunks else len(entries)
    while k_cross < len(entries) - 1:
        cur_e = entries[k_cross]
        nxt_e = entries[k_cross + 1]
        if not cur_e.get("suppressed") and not nxt_e.get("suppressed"):
            t0 = str(cur_e.get("text") or "")
            t1 = str(nxt_e.get("text") or "")
            same_sent = str(cur_e.get("sentence_id") or "") == str(nxt_e.get("sentence_id") or "")
            aux_set = _BAD_SUB_HEAD_AUX if same_sent else _cross_sent_aux
            if (
                t0 and t1 and t1[0] in aux_set
                and not (t1[0] == "呢" and _is_cantonese_ne_demo_head(t1))
                and (same_sent or (len(t0.replace(" ", "")) >= 3 and not _CLAUSE_START_PROTECT_RE.match(t0.replace(" ", ""))))
                and float(nxt_e["start_sec"]) - float(cur_e["end_sec"]) <= 0.35
                and len(t0) <= 15
            ):
                ch_mv = t1[0]
                cand_t0 = t0 + ch_mv
                cand_t1 = t1[1:]
                # 预判剥离后是否会把下句打成 <=3 字残句；若是，先尝试把残剩段并入下下条同句块或整条合并，均不可行则不剥离
                cand_t1_compact = cand_t1.replace(" ", "")
                merge_into_next2 = False
                merge_whole_into_cur = False
                if cand_t1_compact and len(cand_t1_compact) < MIN_CHUNK_CHARS:
                    if k_cross + 2 < len(entries) and not entries[k_cross + 2].get("suppressed"):
                        n2 = entries[k_cross + 2]
                        if (
                            str(n2.get("sentence_id") or "") == str(nxt_e.get("sentence_id") or "")
                            and float(n2["start_sec"]) - float(nxt_e["end_sec"]) <= 0.50
                        ):
                            m2_txt = cand_t1 + str(n2.get("text") or "")
                            m2_w, _ = _measure_text_px(m2_txt, font_path, chunk_fs)
                            if len(m2_txt.replace(" ", "")) <= 12 and m2_w <= chunk_px:
                                merge_into_next2 = True
                    if not merge_into_next2:
                        m0_txt = t0 + t1
                        m0_w, _ = _measure_text_px(m0_txt, font_path, chunk_fs)
                        if len(m0_txt.replace(" ", "")) <= 12 and m0_w <= chunk_px:
                            merge_whole_into_cur = True
                    if not merge_into_next2 and not merge_whole_into_cur:
                        k_cross += 1
                        continue

                def _merge_sids(dst_e: Dict[str, Any], src_e: Dict[str, Any]) -> None:
                    sids = list(dst_e.get("merged_sentence_ids") or ([dst_e["sentence_id"]] if dst_e.get("sentence_id") else []))
                    src_sids = list(src_e.get("merged_sentence_ids") or ([src_e["sentence_id"]] if src_e.get("sentence_id") else []))
                    for sid_x in src_sids:
                        if sid_x and sid_x not in sids:
                            sids.append(sid_x)
                    if len(sids) > 1:
                        dst_e["merged_sentence_ids"] = sids

                if merge_whole_into_cur:
                    cur_e["text"] = t0 + t1
                    cur_e["end_sec"] = max(float(cur_e["end_sec"]), float(nxt_e["end_sec"]))
                    cur_e["words"] = list(cur_e.get("words") or []) + list(nxt_e.get("words") or [])
                    cur_e["keywords"] = list(dict.fromkeys(list(cur_e.get("keywords") or []) + list(nxt_e.get("keywords") or [])))
                    _merge_sids(cur_e, nxt_e)
                    bb0, dy0 = _compute_authoritative_subtitle_bbox(cur_e["text"], tpl, font_path, _faces_for_window(float(cur_e["start_sec"]), float(cur_e["end_sec"])), keywords=cur_e.get("keywords"), terms=prot_terms)
                    cur_e["bbox"] = cur_e["final_bbox"] = bb0
                    cur_e["face_avoid_dy"] = int(dy0)
                    cur_e["safe_zone_ok"], cur_e["safe_msg"] = check_bbox_in_safe_zone(bb0)
                    entries.pop(k_cross + 1)
                    continue

                cur_e["text"] = cand_t0
                nxt_e["text"] = cand_t1
                cw = list(cur_e.get("words") or [])
                nw = list(nxt_e.get("words") or [])
                if nw and str(nw[0].get("word") or "")[:1] == ch_mv:
                    mv_w = nw.pop(0)
                    cw.append(mv_w)
                    cur_e["words"] = cw
                    nxt_e["words"] = nw
                    if mv_w.get("end_sec") is not None:
                        cur_e["end_sec"] = round(float(mv_w["end_sec"]), 4)
                    if nw and nw[0].get("start_sec") is not None:
                        nxt_e["start_sec"] = round(float(nw[0]["start_sec"]), 4)
                _merge_sids(cur_e, nxt_e)
                bb0, dy0 = _compute_authoritative_subtitle_bbox(cur_e["text"], tpl, font_path, _faces_for_window(float(cur_e["start_sec"]), float(cur_e["end_sec"])), keywords=cur_e.get("keywords"), terms=prot_terms)
                cur_e["bbox"] = cur_e["final_bbox"] = bb0
                cur_e["face_avoid_dy"] = int(dy0)
                cur_e["safe_zone_ok"], cur_e["safe_msg"] = check_bbox_in_safe_zone(bb0)
                if not nxt_e["text"].strip():
                    cur_e["end_sec"] = max(float(cur_e["end_sec"]), float(nxt_e["end_sec"]))
                    entries.pop(k_cross + 1)
                    continue
                if merge_into_next2:
                    n2 = entries[k_cross + 2]
                    n2["text"] = nxt_e["text"] + str(n2.get("text") or "")
                    n2["start_sec"] = min(float(nxt_e["start_sec"]), float(n2["start_sec"]))
                    n2["words"] = list(nxt_e.get("words") or []) + list(n2.get("words") or [])
                    n2["keywords"] = list(dict.fromkeys(list(nxt_e.get("keywords") or []) + list(n2.get("keywords") or [])))
                    _merge_sids(n2, nxt_e)
                    bb2, dy2 = _compute_authoritative_subtitle_bbox(n2["text"], tpl, font_path, _faces_for_window(float(n2["start_sec"]), float(n2["end_sec"])), keywords=n2.get("keywords"), terms=prot_terms)
                    n2["bbox"] = n2["final_bbox"] = bb2
                    n2["face_avoid_dy"] = int(dy2)
                    n2["safe_zone_ok"], n2["safe_msg"] = check_bbox_in_safe_zone(bb2)
                    entries.pop(k_cross + 1)
                    continue
                bb1, dy1 = _compute_authoritative_subtitle_bbox(nxt_e["text"], tpl, font_path, _faces_for_window(float(nxt_e["start_sec"]), float(nxt_e["end_sec"])), keywords=nxt_e.get("keywords"), terms=prot_terms)
                nxt_e["bbox"] = nxt_e["final_bbox"] = bb1
                nxt_e["face_avoid_dy"] = int(dy1)
                nxt_e["safe_zone_ok"], nxt_e["safe_msg"] = check_bbox_in_safe_zone(bb1)
                continue
        k_cross += 1

    if not dual_banner:
        tail_hl_target = _subtitle_tail_highlight_target(template_id)
        if tail_hl_target > 0.0:
            _apply_phrase_end_highlights(entries, tail_hl_target, protected_terms=prot_terms)

    for entry_obj in entries:
        if entry_obj.get("suppressed"):
            continue
        st_sec = float(entry_obj["start_sec"])
        en_sec = float(entry_obj["end_sec"])
        ass_text = ""
        for w in entry_obj.get("words") or []:
            w_txt = _escape_ass_text(str(w["word"]))
            if not w_txt:
                continue
            if w.get("is_keyword"):
                ass_text += f"{{\\c{ass_kw_color}\\b1}}{w_txt}{{\\r}}"
            else:
                ass_text += w_txt
        if not ass_text:
            ass_text = _escape_ass_text(entry_obj["text"])
        plain_ass = _escape_ass_text(entry_obj["text"])
        # 双层柔和模糊阴影（Layer 0 柔黑阴影 + Layer 1 清晰主层，禁用自动换行 \q2）
        final_bbox = entry_obj["final_bbox"]
        ass_cx = SUBTITLE_CENTER_X
        ass_cy = int(final_bbox["y"]) + int(final_bbox["h"]) // 2
        ass_dialogues.append(
            f"Dialogue: 0,{fmt_ass_time(st_sec)},{fmt_ass_time(en_sec)},Default,,0,0,0,,"
            f"{{\\q2\\an5\\pos({ass_cx + 4},{ass_cy + 5})\\1c&H000000&\\1a&HB0&\\3c&H000000&\\3a&HB0&\\shad0\\blur4}}{plain_ass}"
        )
        ass_dialogues.append(
            f"Dialogue: 1,{fmt_ass_time(st_sec)},{fmt_ass_time(en_sec)},Default,,0,0,0,,"
            f"{{\\q2\\an5\\pos({ass_cx},{ass_cy})}}{ass_text}"
        )

    safe_ass_style = _build_safe_ass_style(tpl["ass_style"], font_path, font_size=base_fs)
    ass_content = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {CANVAS_WIDTH}",
        f"PlayResY: {CANVAS_HEIGHT}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        safe_ass_style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *ass_dialogues,
    ]) + "\n"

    (pipeline_dir / "05_subtitles.ass").write_text(ass_content, encoding="utf-8")

    # 同步导出纯净单行 SRT 与逐行断句检查报告（含字/秒节奏与保护词防拆核验）
    srt_lines: List[str] = []
    check_lines: List[str] = [f"字幕模板：{template_id} | 总条数：{len(entries)} | 让位挂起：{suppressed_count}", ""]
    active_entries = [e for e in entries if not e.get("suppressed")]
    for s_idx, ent in enumerate(active_entries, start=1):
        srt_lines.append(f"{s_idx}\n{fmt_srt_time(ent['start_sec'])} --> {fmt_srt_time(ent['end_sec'])}\n{ent['text']}\n")
        dur_e = max(0.05, float(ent["end_sec"]) - float(ent["start_sec"]))
        cps_e = len(str(ent["text"]).replace(" ", "")) / dur_e
        flag = "  <- 偏快 (>9字/秒)" if cps_e > 9.0 else ""
        check_lines.append(f"{s_idx:02d} [{ent['start_sec']:6.2f}-{ent['end_sec']:6.2f}] {dur_e:4.2f}s {cps_e:4.1f}字/秒 | {ent['text']}{flag}")

    broken_terms: List[str] = []
    for a, b in zip(active_entries, active_entries[1:]):
        ta = str(a["text"]).replace(" ", "")
        tb = str(b["text"]).replace(" ", "")
        joint = ta[-4:] + "|" + tb[:4]
        cut_i = joint.index("|")
        joined_plain = joint.replace("|", "")
        for tm in prot_terms:
            for m in re.finditer(re.escape(tm), joined_plain):
                if m.start() < cut_i < m.end():
                    broken_terms.append(f"{a['id']}/{b['id']} 拆开了「{tm}」")
    check_lines.append("\n保护词防拆检查：" + ("通过" if not broken_terms else "；".join(broken_terms)))
    (pipeline_dir / "05_subtitles.srt").write_text("\n".join(srt_lines), encoding="utf-8")
    (pipeline_dir / "05_subtitle_check.txt").write_text("\n".join(check_lines) + "\n", encoding="utf-8")

    out_json = {
        "step": "05_subtitles",
        "template_id": template_id,
        "template_name": tpl["name"],
        "font_path": font_path,
        "font_license": tpl["license"],
        "font_source_family": source_font_family,
        "font_exact_match": exact_font,
        "exact_1to1_hint": (
            "字体已是源模版原版，字形可 1:1"
            if exact_font else
            "当前为可商用替代字体；把源模版字体放进 assets/style_spectrum/fonts/"
            f"{template_id}/ 即可按原版字形渲染"
        ),
        "template_config": tpl,
        "count": len(entries),
        "suppressed_count": suppressed_count,
        "kinetic_stage_suppressed": _kinetic_stage_suppressed,
        "srt_path": str((pipeline_dir / "05_subtitles.srt").resolve()),
        "check_report_path": str((pipeline_dir / "05_subtitle_check.txt").resolve()),
        "broken_protected_terms": broken_terms,
        "entries": entries,
    }
    # ── 字形覆盖门禁（fail-closed）──
    # PIL 不做字体回退，缺字就是空白/豆腐块；用户实测「阿里妈妈字体…很多字都显示不出来」。
    # 这里对将要上屏的全部字幕文本逐字核对「主字体 + glyph_fallback_chain」并落库 glyph_coverage，
    # 缺一个字就终止（exit 非 0），绝不带着豆腐块往下渲染。
    try:
        coverage = glyph_gate.coverage_report(
            [(str(e.get("id") or i), str(e.get("text") or "")) for i, e in enumerate(entries, start=1)],
            font_path,
            strict_chain=True,
        )
    except glyph_gate.GlyphCoverageError as exc:
        out_json["glyph_coverage"] = {"chars": 0, "missing": ["<配置错误>"], "fallback_used": {}, "error": str(exc)}
        write_json(pipeline_dir / "05_subtitles.json", out_json)
        print(f"[step5] 字形覆盖配置错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
    out_json["glyph_coverage"] = {
        "chars": coverage["chars"],
        "missing": coverage["missing"],
        "fallback_used": coverage["fallback_used"],
        "total_chars": coverage["total_chars"],
        "primary": coverage["primary"],
        "chain": coverage["chain"],
        "fallback_chars": coverage["fallback_chars"],
        "missing_lines": coverage["missing_lines"],
    }
    write_json(pipeline_dir / "05_subtitles.json", out_json)
    if coverage["missing"]:
        print(
            f"[step5] 字形覆盖 FAIL：{len(coverage['missing'])} 个字无字体可画"
            f"（{''.join(coverage['missing'])}），上屏会变成空白/豆腐块；"
            "请在 assets/fonts/font_aliases.json → glyph_fallback_chain 补一套覆盖的字体",
            file=sys.stderr,
        )
        for ln in coverage["missing_lines"]:
            print(f"  ✗ {ln['id']}「{ln['text']}」缺 {''.join(ln['chars'])}", file=sys.stderr)
        raise SystemExit(1)
    print(
        f"[step5] 字幕生成完成：{len(entries)} 条（图解画面自动让位 {suppressed_count} 条，"
        f"模板 `{template_id}`, 字体 `{Path(font_path).name if font_path else 'default'}`"
        f"｜字形覆盖 {coverage['chars']} 字全中，回退 {sum(coverage['fallback_used'].values())} 字次）"
        "-> 05_subtitles.json"
    )
    return out_json


def main() -> None:
    maybe_reexec_in_venv()
    parser = argparse.ArgumentParser(description="Step 5: 逐字时间戳字幕与统一避脸布局生成")
    parser.add_argument("--pipeline-dir", required=True)
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument("--template", help="字幕模板ID")
    parser.add_argument("--corrected-text", help="可选：人工校对后的纯文本文件，字符级回填逐字时间戳后重排字幕")
    args = parser.parse_args()
    pdir = Path(args.pipeline_dir)
    if args.corrected_text and Path(args.corrected_text).is_file():
        ae_p = pdir / "03_audio_events.json"
        if ae_p.is_file():
            ae = read_json(ae_p)
            corr_lines = [ln.strip() for ln in Path(args.corrected_text).read_text(encoding="utf-8").splitlines() if ln.strip()]
            for sent, corr in zip(ae.get("sentences") or [], corr_lines):
                aligned, ratio = align_corrected_text_to_words(corr, sent.get("words") or [])
                if aligned and ratio >= 0.35:
                    sent["text"] = corr
                    sent["words"] = aligned
            write_json(ae_p, ae)
    run_step5(pdir, track=args.track, template_id=args.template)


if __name__ == "__main__":
    main()
