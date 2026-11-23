#!/usr/bin/env python3
"""
把 Apple Vision 的场景分类标签翻成中文语义标签（本地、零联网、零模型）。

为什么需要它
──────────
用户实测最痛的一条是「配错镜」：`broll_semantics` 能拿到的线索只有文件名 / 文件夹名 / 画面 OCR /
手写素材说明。iPhone 原始命名（`IMG_1938.MOV`）这四个来源全空，于是"一张沙发一张沙发的坐"
被配到板材办公区——不是选片算法坏，是**画面里是什么根本没人知道**。

`VNClassifyImageRequest` 是 macOS 自带的 ImageNet 级分类器（1000+ 英文标签，不联网、不要 token），
`vision_inspector` 已经把它跑起来了。这里把标签翻成中文词，接进 `broll_semantics` 的概念空间，
让"画面里是沙发"变成一个可检索的事实。

局限（写在这里，避免又被当成 100% 解决）
────────────────────────────────────────
分类器给的是**物件/场景级**标签（sofa / chair / kitchen / car），不是"这段话在讲什么"。
它能解决"牌子写着沙发区但画面是办公桌"，不能解决"这张图能不能说明成本高"。
后者要么本地跑视觉语言模型（Ollama + qwen2.5-vl 这类，7B 量化要 6–8GB 内存），要么走云端多模态。
`scripts/tag_frames.py` 两种都支持，没配就走分类器 + OCR，并在结果里标明用的是哪一种。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

CONF_FLOOR = 0.18

# 英文标签 → 中文概念词。一个标签可以给多个中文词（"kitchen" → 后厨、厨房）。
# 只收门店/生活实拍里真正会遇到的：吃住行、装修建材、美业、汽车、医疗、办公、人物、街景。
LABEL_ZH: Dict[str, Tuple[str, ...]] = {
    # ── 人 ──
    "people": ("人物出镜",), "person": ("人物出镜",), "adult": ("人物出镜",), "man": ("人物出镜",),
    "woman": ("人物出镜",), "child": ("人物出镜", "小孩"), "baby": ("人物出镜", "小孩"),
    "face": ("人物特写", "人脸"), "selfie": ("人物出镜", "自拍"), "smile": ("人物出镜", "笑脸"),
    "suit": ("正装", "西装"), "necktie": ("正装", "西装"), "jeans": ("休闲装",), "clothing": ("服装",),
    "dress": ("服装",), "shirt": ("服装",), "hat": ("服装", "帽子"), "shoe": ("鞋",),
    # ── 吃 ──
    "food": ("后厨餐食", "食物"), "dish": ("后厨餐食", "菜"), "meal": ("后厨餐食",),
    "restaurant": ("后厨餐食", "餐厅"), "kitchen": ("后厨餐食", "厨房"), "dining": ("后厨餐食", "用餐"),
    "meat": ("肉",), "beef": ("牛肉", "肉"), "pork": ("猪肉", "肉"), "fish": ("鱼", "海鲜"),
    "seafood": ("海鲜",), "shrimp": ("海鲜", "虾"), "chicken": ("鸡肉",), "lamb": ("羊肉", "肉"),
    "barbecue": ("烧烤", "炭火"), "grill": ("炭火", "烤"), "smoke": ("炭火", "烟"),
    "fire": ("炭火", "火"), "vegetable": ("蔬菜",), "fruit": ("水果",), "bread": ("面点",),
    "cake": ("甜点",), "coffee": ("饮品",), "tea": ("饮品", "茶"), "wine": ("酒水",),
    "beer": ("酒水",), "bottle": ("饮品",), "cup": ("餐具",), "plate": ("餐具",),
    "bowl": ("餐具",), "chopsticks": ("餐具",), "pot": ("锅具",), "pan": ("锅具",),
    # ── 住 / 装修建材 ──
    "furniture": ("家具",), "sofa": ("沙发", "家具"), "couch": ("沙发", "家具"),
    "chair": ("椅子",), "table": ("桌子",), "bed": ("床", "卧室"), "mattress": ("床垫", "床"),
    "cabinet": ("柜子", "全屋定制"), "shelf": ("柜子", "收纳"), "closet": ("衣柜", "全屋定制"),
    "wardrobe": ("衣柜", "全屋定制"), "door": ("门", "木门"), "window": ("窗", "门窗"),
    "floor": ("地板",), "tile": ("瓷砖",), "ceiling": ("吊顶",), "wall": ("墙面",),
    "lamp": ("灯具",), "light": ("灯光",), "curtain": ("窗帘",), "carpet": ("地毯",),
    "pillow": ("软装",), "mirror": ("镜子",), "sink": ("卫浴",), "bathtub": ("卫浴",),
    "toilet": ("卫浴",), "bathroom": ("卫浴", "卫生间"), "living": ("客厅",), "bedroom": ("卧室",),
    "room": ("室内",), "house": ("房屋",), "apartment": ("房屋",), "building": ("楼", "建筑"),
    # ── 工地 / 工具 / 制造 ──
    "construction": ("工地", "施工"), "crane": ("工地", "吊装"), "scaffold": ("工地",),
    "concrete": ("水泥", "施工"), "brick": ("砖", "建材"), "wood": ("木材", "板材"),
    "lumber": ("木材", "板材"), "board": ("板材",), "metal": ("金属",), "steel": ("钢材",),
    "tool": ("工具",), "hammer": ("工具",), "drill": ("工具",), "saw": ("工具",),
    "machine": ("设备", "机器"), "factory": ("工厂", "车间"), "workshop": ("工厂", "车间"),
    "warehouse": ("仓库",), "box": ("货物", "包装"), "package": ("货物", "包装"),
    "pallet": ("货物",), "pipe": ("管材",), "wire": ("电线",), "paint": ("涂料", "刷漆"),
    "brush": ("刷子", "刷漆"),
    # ── 车 ──
    "car": ("汽车",), "vehicle": ("汽车",), "automobile": ("汽车",), "truck": ("货车",),
    "bus": ("车",), "bicycle": ("电动车", "自行车"), "motorcycle": ("摩托车",),
    "wheel": ("车轮",), "tire": ("轮胎",), "garage": ("汽修", "车库"), "parking": ("停车",),
    "dashboard": ("车内", "仪表"), "steering": ("车内", "方向盘"),
    # ── 美业 / 医美 ──
    "hair": ("头发", "美业"), "salon": ("美业", "理发"), "makeup": ("美妆",),
    "cosmetics": ("美妆", "护肤"), "skin": ("护肤", "皮肤"), "nail": ("美甲",),
    "spa": ("美容", "护理"), "massage": ("按摩", "护理"), "hospital": ("医疗",),
    "clinic": ("门诊", "医疗"), "doctor": ("医生", "医疗"), "medicine": ("药品", "医疗"),
    "dentist": ("牙科", "医疗"), "xray": ("检查", "医疗"),
    # ── 办公 / 数码 ──
    "office": ("办公",), "desk": ("办公桌", "办公"), "computer": ("电脑", "办公"),
    "laptop": ("电脑",), "keyboard": ("键盘",), "monitor": ("显示器",), "screen": ("屏幕",),
    "phone": ("手机",), "camera": ("拍摄",), "paper": ("文件",), "book": ("书", "读书"),
    "notebook": ("笔记本",), "pen": ("文具",), "document": ("文件",),
    # ── 街景 / 环境 / 交通 ──
    "street": ("街道", "街景"), "road": ("路",), "city": ("城市",), "town": ("街区",),
    "shop": ("门店", "店铺"), "store": ("门店", "店铺"), "market": ("市场", "菜市场"),
    "supermarket": ("超市",), "mall": ("商场",), "sign": ("招牌",), "signboard": ("门头", "招牌"),
    "banner": ("横幅", "招牌"), "poster": ("海报",), "advertisement": ("广告",),
    "crowd": ("人群",), "parade": ("活动", "人群"), "stage": ("舞台", "活动"),
    "park": ("公园",), "garden": ("花园",), "tree": ("树木", "绿植"), "plant": ("绿植",),
    "flower": ("花",), "grass": ("草地",), "mountain": ("山",), "river": ("河",),
    "sea": ("海",), "beach": ("海边",), "sky": ("天空",), "sunset": ("夕阳", "光线"),
    "snow": ("雪",), "rain": ("雨",), "night": ("夜景",), "indoor": ("室内",),
    "outdoor": ("户外",), "landscape": ("风景",), "nature": ("自然",),
    # ── 视觉属性 ──
    "text": ("画面文字",), "font": ("文字",), "letter": ("文字",), "number": ("数字",),
    "money": ("钱", "价格"), "cash": ("现金", "价格"), "coin": ("价格",), "card": ("卡片",),
    "receipt": ("小票", "价格"), "chart": ("图表", "数据"), "diagram": ("图表",),
    "graph": ("图表",), "whiteboard": ("白板", "讲解"), "presentation": ("演示", "讲解"),
    "microphone": ("麦克风", "收音"), "speaker": ("音响",), "headphones": ("耳机",),
}

# 归类到「这个标签能当什么线索」：命中这些词就说明画面里确实有可对位的内容
SCENE_GROUP = {
    "人物出镜": "person", "人物特写": "person", "自拍": "person",
    "后厨餐食": "food", "炭火": "food", "烧烤": "food", "肉": "food", "海鲜": "food",
    "家具": "goods", "沙发": "goods", "椅子": "goods", "桌子": "goods", "床": "goods",
    "柜子": "goods", "板材": "goods", "木材": "goods", "瓷砖": "goods", "门窗": "goods",
    "汽车": "vehicle", "轮胎": "vehicle", "车内": "vehicle",
    "美业": "beauty", "护肤": "beauty", "美妆": "beauty", "医疗": "beauty",
    "工地": "site", "施工": "site", "工厂": "site", "仓库": "site",
    "门店": "place", "店铺": "place", "街道": "place", "商场": "place", "市场": "place",
    "办公": "place", "室内": "place", "户外": "place",
}


def labels_to_tags(labels: Iterable[Any], conf_floor: float = CONF_FLOOR) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Vision 分类结果 → 中文语义标签。返回 (中文词列表, 命中的原始证据)。"""
    tags: List[str] = []
    evidence: List[Dict[str, Any]] = []
    seen: set = set()
    for item in labels or []:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip().lower()
            conf = float(item.get("conf") or 0.0)
        else:
            label, conf = str(item).strip().lower(), 1.0
        if not label or conf < conf_floor:
            continue
        # 分类器标签是英文短语（"coffee mug"），逐词拆开查
        words: List[str] = [label] + label.replace("_", " ").split()
        hit = False
        for w in words:
            for zh in LABEL_ZH.get(w, ()):
                hit = True
                if zh not in seen:
                    seen.add(zh)
                    tags.append(zh)
        if hit:
            evidence.append({"label": label, "conf": round(conf, 3)})
    return tags, evidence


def tag_groups(tags: Iterable[str]) -> List[str]:
    """这些标签落在哪几个大类上（person / food / goods / vehicle / beauty / site / place）。"""
    out: List[str] = []
    for t in tags or []:
        g = SCENE_GROUP.get(t)
        if g and g not in out:
            out.append(g)
    return out
