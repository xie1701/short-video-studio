#!/usr/bin/env python3
"""
剪映「入场 / 出场动画」的数学复刻。

草稿里挂在 `segment.extra_material_refs` 的 `material_animations[*]` 上：

    {"type": "sticker_animation",
     "animations": [{"id": "1045515", "type": "in",  "name": "缩小",     "duration": 500000},
                    {"id": "1363006", "type": "out", "name": "向下滑动", "duration": 500000,
                     "start": 2000000}]}

`duration` 单位是微秒；`out` 的 `start` 是**相对该段起点的偏移**（微秒），缺省时按
「段尾倒数 duration」处理。`in` 永远从段起点开始。

这个模块只做纯计算：给「段内本地时间 → 缩放 / 位移 / 透明度 / 模糊」四组修正量，
渲染侧拿去逐帧施加（叠加层用 PIL 逐帧，贴纸用图层参数，文字用 PNG 逐帧）。

为什么单独一个模块
──────────────────
不实现这一层，所有「写进草稿但只在播放时生效」的动效都会变成静帧：
style_21 里 13 处动画（贴纸缩小入场、手机框下滑出场、标题日出、渐显渐隐…）
一个都没做，成片看起来就是「该动的地方一动不动」。

系数可标定
──────────
`DEFAULTS` 里的幅度是从剪映同名动画的常见档位定的初值；`CALIBRATION` 留给
「用原版帧反解最优值」的闭环——`sweep()` 就是那个闭环的工具函数，别手调。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

US = 1_000_000

# 动画幅度初值（可被 CALIBRATION 覆盖）。缩放类给的是「动画起点值 → 结束回到 1.0」。
DEFAULTS: Dict[str, dict] = {
    "fade":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "scale":    {"scale0": 1.25, "alpha0": 1.0, "blur0": 0.0},
    "slide":    {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "reveal":   {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "blur":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 24.0},
    "rotate":   {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "缩小":     {"scale0": 1.25, "alpha0": 1.0, "blur0": 0.0},
    "放大":     {"scale0": 0.75, "alpha0": 1.0, "blur0": 0.0},
    "渐显":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "渐隐":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "向左滑动": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向右滑动": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向上滑动": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向下滑动": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向右露出": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向左露出": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向上露出": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "向下露出": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0},
    "日出":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "日落":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "横向模糊": {"scale0": 1.00, "alpha0": 0.0, "blur0": 24.0},
    "雪光模糊": {"scale0": 1.00, "alpha0": 0.0, "blur0": 24.0},
    "模糊":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 24.0},
    "闪白":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    "闪黑":     {"scale0": 1.00, "alpha0": 0.0, "blur0": 0.0},
    # 新增两族：横向张开 / 高位落下（都不改 alpha，否则会「凭空淡入」）
    "flipx":    {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0, "flip0": 0.0},
    "drop":     {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0, "drop": 0.115},
    "镜像翻转": {"scale0": 1.00, "alpha0": 1.0, "blur0": 0.0, "flip0": 0.0},
    "抖动下降": {"scale0": 0.78, "alpha0": 1.0, "blur0": 0.0, "drop": 0.115},
}

# 用原版帧反解出来的覆盖值（键 = 动画名，字段同 DEFAULTS）。留空即全用 DEFAULTS。
#
# 每条都注明「反解方式」，数字不是拍的：
#   ease    偏移量曲线 w^k（w = 距动画起点的剩余权重）；k 由逐帧量到的位移/尺寸回归得出。
#   *_0     动画起点的偏移幅度（scale0 = 尺寸倍数，drop/slide_gain = 画布归一化位移）。
CALIBRATION: Dict[str, dict] = {
    # 本表是「贴纸」宿主下的标定（sticker_render 调 anim_state 不带 ctx）。
    # 视频叠加层 / 文字层的同名幅度见下面的 CTX_CALIBRATION["layer"]。
    #
    # 用原版帧反解的「向下滑动」出场（style_21 手机边框）：草稿写 duration=0.5，
    # 旧标定按「12.45s 静止、12.85s 全出」反解出 0.6×；后来主轨缩窗/一帧时移修完再扫，
    # 出场起点不变（段尾前 0.03s），但时长必须拉到 0.8×：12.2–13.0s 八点差量 21.86 → 18.15。
    "向下滑动": {"slide_gain": 0.8, "out_dur_scale": 0.8, "out_start_slack": 0.03},
    # 「缩小」入场（同一处手机边框）：起手是「从大处淡入」，起点透明度必须是 0。
    #
    # 2026-11-23 二次反解（第一次的值 1.6 / in_dur_scale 0.6 / 线性 是错的）：
    # 反解方式 —— 原版帧减去主轨底片（base，无贴纸）得 D = orig − base，只在贴纸不透明处非零；
    #   ① 几何：贴纸顶边 y_top / 左边 x_left 都是可测的直边，绕贴纸中心 (848.9, 1103.9) 等比缩
    #      可得 s = (CY − y_top)/(CY − 218) 与 s = (CX − x_left)/(CX − 69)。两条边独立算出的 s
    #      逐帧吻合到 0.2%（例：10.733s 146→1.081、6→1.081；10.833s 200→1.020、54→1.020）
    #      → 锚点确实是贴纸中心（另做了 (s,dx,dy) 三维 IoU 搜：dx=dy=0 处处最优）。
    #      实测 s(t) − 1 = 0.504·(1−p)²，p=(t−10.4333)/0.5，残差 ≤0.001（十帧）。
    #   ② 透明度：用该帧 s 的模板算 alpha = median(D / (T−base))（只取 |T−base|>40 的强反差像素，
    #      IQR ≤0.03 → 全局均匀透明）。实测 1−alpha = 0.46/0.205/0.083/0.021 @10.600/10.717/10.800/10.900
    #      → 与 ①同一条曲线：(1−p)²。
    #   ③ 窗口：p=1 落在 10.9333 = 段起点 + 0.5s（草稿 duration 原值，**不乘 0.6**）；
    #      旧标定把窗口压到 0.3s，所以 10.7s 之后我们已经全实、原版才 0.82。
    #   ④ 像素验证（合成预测 vs 原版，10.40–11.06s 逐帧）：旧值 MAE 8.03 → 新值 3.61；
    #      入场段 10.4333–10.9333 9.69 → 3.80（稳态地板 3.14）。
    #      单变量对照证伪两种猜测：纯淡入（s 恒 1）10.65、纯缩放（alpha 恒 1）24.73、
    #      线性 alpha 6.47 —— 都不是，必须是「缩放 + 同曲线淡入」。
    #
    # 注意：此表对 6 处贴纸「缩小」共用（时长 0.3s×4 / 0.5s×2），本组数值只在这条 0.5s 实例上
    # 反解过；0.3s 那四处没有任何一帧证据，改动属于「按同一动画语义推」而非实测。
    "缩小": {"scale0": 1.50, "alpha0": 0.0, "in_dur_scale": 1.0,
             "ease": 2.0, "alpha_ease": 2.0},
}

# 按「宿主」分的幅度表。剪映同名动画在贴纸 / 视频图层 / 文字上幅度不同（贴纸 PNG 自带透明
# 边距，包围盒 ≠ 可见墨迹），所以同一套曲线要按宿主给不同系数。
#   layer：视频叠加轨与文字层。数值均由原版帧反解，注释里写明反解方式：
#     ease       偏移曲线 w^k（w = 距动画起点的剩余权重）
#     alpha_ease 透明度曲线单独用另一个指数（实测尺寸与透明度并不是同一条曲线）
CTX_CALIBRATION: Dict[str, Dict[str, dict]] = {
    "layer": {
        # 卡片底边逐帧反解：scale = 1.5 → 1.0，九个采样点全在一条直线上
        # （p=0.11/0.44/0.89 实测 1.443/1.276/1.056）；透明度同时从 0 涨到 1，
        # 卡面亮度实测 p=0.11 时只有最终的 ~36%（9.9s 还是「鬼影」）、p=0.44 已 ~96%
        # → 透明度比尺寸衰减得快得多，扫 {1,3,6,10} 后取 k=6（p=0.11→0.50、0.44→0.97）。
        #
        # in_dur_scale 必须显式写 0.6：这一层的数值是「窗口 = 0.6×duration」下拟合的，
        # 而 _cfg() 是「CALIBRATION → CTX_CALIBRATION 逐字段覆盖」——贴纸那张表 2026-11-23
        # 二次反解把 in_dur_scale 改成 1.0（贴纸手机边框的入场窗口真是整段 0.5s），
        # 不在这里寫死 0.6 就会浡到文字/叠加层的「缩小」上（实测 6.85–7.0s 的文字入场
        # 被拉长到 0.6s，逐帧差 +1.2–1.4 级）。
        "缩小": {"scale0": 1.5, "alpha0": 0.0, "in_dur_scale": 0.6,
                 "ease": 1.0, "alpha_ease": 6.0},
        # 「收拢」：**透明度不淡入，只缩尺寸**。
        #
        # 原来什么：alpha0 = 0.0（「从大处淡入」），依据是「3.5s 那帧原版还看不见字」。
        # 改成什么：alpha0 = 1.0，只留 scale 0→1 的等比收缩。
        # 为什么（逐帧实测，style_17 3.5s 字幕，带内墨迹逐帧量）：
        #   原版 f105(t=3.500) 带内无字（亮度 5.7）→ f106 一帧直接到**满不透明**
        #   （墨迹均值 198.5，之后 f107…f115 恒定 198/199/198/199/198/199/199/198/198），
        #   同时墨迹**宽度**在缩：116→109→104→101→98→96→94→94→93 px。
        #   也就是说这一帧里只有尺寸在动、不透明度从头到尾是 1。
        # 旧依据错在哪：3.500 那帧原版的图层根本还没开始显示（不是 alpha 接近 0），
        #   把它当成「alpha 曲线起点」就把一整段淡入凭空加了出来。修前我们逐帧带内墨迹
        #   均值 130.5/137.5/156.1/171.3/182.2/190.1/193.3/196.2，到 f115 才追上原版。
        # 幅度与曲线也是逐帧反解出来的（同一段字幕，墨迹宽度相对稳态 93px 的余量）：
        #   f106..f115 = 116/109/104/101/98/96/94/94/93 → 余量 23/16/11/8/5/3/1/1/0，
        #   归一化后 1/.696/.478/.348/.217/.130/.043 —— 取 w=(1-p)^ease 拟合得 ease≈3.5；
        #   首帧 w=1 处 116/93 = 1.247 就是 scale0（旧值 1.35 是通用占位，偏大 8%）。
        # 影响面：只动 `layer` 宿主（文字层与视频叠加层）；贴纸那张表（CALIBRATION["收拢"]）不动。
        "收拢": {"scale0": 1.247, "alpha0": 1.0, "ease": 3.5},
        # 「雪光模糊」：8.2s 起手要「先亮起来再收清」，原版 0.53s 动画过半（8.5s）已经又亮又清，
        # 拿线性衰减会到 8.5s 还糊成一片（实测 alpha 只有 0.27、字完全读不出）。
        "雪光模糊": {"alpha0": 0.0, "blur0": 32.0, "ease": 1.0, "alpha_ease": 6.0},
        # 「镜像翻转」入场：卡片左右边界 12 帧量到 |scale_x| = 0.363→1.0，
        # 拟合 1-(1-p)^3 残差 ±0.02（首帧因半透明边被采样到偏小），即 out-cubic。
        "镜像翻转": {"flip0": 0.0, "ease": 3.0},
        # 「向左/向右滑动」入场：卡片左(右)边界逐帧量到 dx = A(1-p)^k，
        # 两张不同卡片回归出 k=2.16/2.22、A=1218/1319 px（画布宽 1080）→ k=2.2、A=1.2W。
        "向左滑动": {"slide_gain": 1.2, "ease": 2.2},
        "向右滑动": {"slide_gain": 1.2, "ease": 2.2},
        # 「抖动下降」入场：起手缩到 0.78 倍、从画面上方 0.115H 处落下（底边逐帧量到
        # -212px → 0，同时宽度 727→918）；原动画带阻尼抖动，这里只复刻主位移。
        "抖动下降": {"scale0": 0.78, "drop": 0.115, "ease": 2.4},
        # 「向右露出」入场（style_21 标题「粉丝认可度」，duration=0.3s，段内 0→0.3s）：
        # **机制本来就是平移，不是遮罩擦除**；错的是幅度和指数
        # （旧值 = 文本框宽 918 px × 线性 → 动画过半时整行字还糊在画面中间）。
        #
        # 反解方式（只量金色墨迹的右边界，两个阈值口径交叉验证一致）：
        #   文本静止位置 x ∈ [44, 784]；逐帧右边界 =
        #   f102..f118 → 31/117/199/273/343/401/467/525/577/623/663/699/729/752/770/780/784
        #   位移量 D(t) = 784 − right(t)；最小二乘拟合 D = A·(1−p)^k（p = 段内秒/0.3）：
        #   A = 936 px、k = 1.86，残差均值 1.4 px、最大 8.5 px（16 帧）。
        #
        # 「平移 vs 遮罩」的判据（这一步必须先证实族名背后的机制）：遮罩擦除的左边恒在
        # 文本左沿（44），只有右边在长；实测却是**字形列游程整体左移**——
        # f115/f116/f117 的前几个游程 = (10,34)(41,155)(166,296)… / (30,52)(62,172)… /
        # (39,62)(72,183)…，比末帧 (42,66)(73,187)… 整齐地左移 32/12/3 px，
        # 且右边界 = 784 + 平移量（752/770/780）。即整块文本在被推入画面（同时被屏幕左沿裁掉），
        # 不是逐列揭示。所以这里保留位移实现，只把幅度/缓动改成实测值：
        # slide_gain = 936 / 918（文本框 PNG 宽，render_text_png 实测 918×422）= 1.02，ease = 1.86。
        "向右露出": {"slide_gain": 1.02, "ease": 1.86},
        # 「日出」入场（style_21 标题「倾心诉说」/「落地呈现」，duration=0.4s）：
        # 旧实现用默认值（slide_gain=1.0、ease=1、alpha_ease=1），实测两条曲线都不对：
        # 起手文字应该在 0.15s 内就基本到位、亮度也快得多，而我们到动画 70% 时还几乎没动。
        # 后果：11.65–12.05s 这 24 帧里文字整块在错误位置、而且多半是半透明，
        # 11.90s 单帧 y=320..640 区域差 42–112 级（全片最差的一段之一）。
        #
        # 反解方式（style_21 11.65s 段，原版帧 − 主轨底片 base_video.mp4 = 纯文字层，
        # 再用稳定帧 12.10s 的文字层当模板，逐帧搜 (dy, alpha) 最小二乘）：
        #   dy(t)：11.7333→280、11.75→238、11.7667→200、11.7833→166、11.80→138、11.8167→112、
        #          11.8333→90、11.85→70、11.8667→54、11.8833→40、11.90→30、11.9167→20、
        #          11.9333→14、11.95→8、11.9667→4、11.9833→2（单位：画布像素）
        #   alpha(t)：11.75→0.68、11.7667→0.76、11.7833→0.80、11.80→0.83、11.8167→0.85、
        #          11.8333→0.87、11.85→0.89、11.8667→0.91、11.8833→0.93、11.90→0.95、
        #          11.9167→0.96、11.9333→0.98、11.95→0.99
        #   拟合 dy = A·(1−p)^k（p = 段内秒/0.4，t0 = 段起点 11.650）：
        #     **A = 565、k = 3.00**，16 个点最大残差 1.6 px。
        #     文字 PNG 实测 1122×605 → slide_gain = 565/605 = 0.934（旧值 1.0）。
        #   拟合 alpha = 1−(1−p)^e：**e = 3.7**，13 个点最大残差 0.04。
        #   （同一模型给 t0=11.6333（早一帧）时 A=615/k=2.85 也拟合到 1.5 px——那是把
        #     帧时间基准取成 11.633+i/60 的假象；段起点就是 11.650，不引入 1 帧偏移。）
        "日出": {"slide_gain": 0.934, "ease": 3.0, "alpha_ease": 3.7},
    },
    # 照片图层（视频轨上的 photo 素材）单独一档。同一个「抖动下降」在照片上是「黑场淡入」，
    # 在别的宿主上不带透明度，所以按「宿主 + 时长」再分一级（见下面的 0.40 键）。
    "layer_photo": {
        # style_17 7.733s 的 1.6s 照片（clip.alpha=0.5，入场「抖动下降」0.4s）：
        # 原版是黑场里淡入（7.733s 整帧均值 0.5，我们旧实现 alpha0=1 直接全亮 → 10.4）。
        #
        # 反解方式（量几何/透明度，不用整帧亮度——「抖动下降」本身在改覆盖面积）：
        #   1) 把 8.20s 稳定帧（clip.alpha=0.5 已是终态）当模板，逐帧搜 (scale, dy) 做对齐，
        #      再用最小二乘求增益 g（= 该帧的动画透明度）。实测 g：
        #      7.767→0.78、7.800→0.81、7.833→0.88、7.867→0.91、7.900→0.92、
        #      7.933→0.91、7.967→0.96、8.000→0.97、8.033→0.99、8.067→0.99（8.10 起 =1.00）；
        #      7.733 那帧整帧只有 0.5 个灰度级、照片已基本出画（底边 101px）→ g≈0。
        #   2) 三条曲线都用 w=1-p 的幂律：scale = 1-(1-s0)·w^A、dy = -D·H·w^B、
        #      alpha = 1-w^E。对 (s0,A,B,D,E) 做 5 维网格搜索，14 帧整帧灰度 MAE 由
        #      旧实现 11.05 降到 2.78；最优点 s0=0.72 / A=2.4 / B=3.5 / D=0.20 / E=6.0
        #      （s0 0.72–0.78 是一条平台，残差差 ≤0.08，取平台内最优）。
        #   3) 尺寸与位移的指数不同（2.4 vs 3.5），所以单独给 drop_ease，不动共享的 ease。
        # 只覆盖 0.40s：同名的其它时长档走共享标定，不受影响。
        "抖动下降@0.40": {"scale0": 0.72, "alpha0": 0.0, "ease": 2.4,
                          "alpha_ease": 6.0, "drop": 0.20, "drop_ease": 3.5},
    },
    # 主轨段级动画（material_animations 挂在视频段上）。目前只做模糊族——
    # 主轨要逐帧几何得拆段重渲，模糊族可以只叠一条 gblur。
    "main": {
        # 「横向模糊」入场（style_21 主轨 4.833s 段，0.2667s）实为**径向/变焦模糊 + 白光过曝**：
        # 原版 4.90s 帧用视觉复核是「以画面中心为原点向外放射」，不是横向拖影；
        # 各向同性/椭圆 gblur 试过（sigmaX 30–55），逐帧差 51–57 下不来。
        # 该动效实为「径向/变焦模糊 + 白光过曝」：原版 4.85/4.90/4.95/5.00/5.05 帧整帧亮度
        # 219.9/187.1/?? /187.1... 依次衰减到 164（无动效基准），按 out=(1-a)·in+255·a 反解
        # a = 0.6/0.5/0.4/0.2/0.1 → 线性 a = 0.65·(1-p)（p 为动画进度，残差 ≤0.04）。
        # 变焦模糊（径向）用 PIL 逐帧烘（见 render_replica 的 _zmanim_media），这里只给白场：
        # blur_x0=0 显式关掉各向同性/椭圆 gblur，否则会叠一条与径向模糊无关的糊边。
        "横向模糊": {"flash0": 0.65, "ease": 1.0, "in_dur_scale": 1.0, "blur_x0": 0.0},
    },
}

# 宿主表的继承链：子宿主没写的键回落到父宿主（photo 图层先吃 layer 的通用标定，
# 再用自己的「名字@时长」二级键覆盖）。
CTX_PARENT: Dict[str, str] = {"layer_photo": "layer"}

# 全库 113 套草稿里共 150 个动画名、2332 条。按行为归族比逐个硬编码可维护：
# 同族共享一套数学，族内个别名字用 DEFAULTS 单独调幅度。
FAMILY: Dict[str, str] = {}
for _names, _fam in (
    (("渐显", "渐隐", "闪白", "闪黑", "暗场", "亮场", "淡入", "淡出"), "fade"),
    (("缩小", "放大", "轻微放大", "轻微缩小", "弹出", "跃进", "收拢", "折叠",
      "生长", "生长 II", "生长III", "弹性放大", "胶囊放大"), "scale"),
    (("向左滑动", "向右滑动", "向上滑动", "向下滑动", "滚入", "滚出", "随机上升",
      "滑入", "推入", "飞跃", "向右缓入", "向右快速进", "左移", "右移",
      # 日出/日落就是「从下/上方滑入」，方向和幅度已写在 _SLIDE 里：
      # 漏了这两个名字的话它们会掉到淡入淡出族，位移整个消失（14.87s 那条就是这样）
      "日出", "日落"), "slide"),
    (("向右露出", "向左露出", "向上露出", "向下露出", "向右擦除", "向左擦除",
      "向上擦除", "向下擦除", "开幕", "开幕 II", "逐字显影", "打字机",
      "新年打字机", "打字光标", "打字"), "reveal"),
    (("横向模糊", "雪光模糊", "模糊", "发光模糊", "模糊发光", "向左模糊",
      "向右模糊 II", "晕开", "光晕"), "blur"),
    (("圆柱体滚动", "旋转", "翻转", "翻页", "3D翻转"), "rotate"),
    (("镜像翻转",), "flipx"),
    (("抖动下降",), "drop"),
):
    for _n in _names:
        FAMILY[_n] = _fam

# 同族但需要单独幅度的名字（没列到的用族默认）
FAMILY_DEFAULTS: Dict[str, dict] = {
    "轻微放大": {"scale0": 1.06},
    "轻微缩小": {"scale0": 0.94},
    "弹出":     {"scale0": 0.70},
    "跃进":     {"scale0": 1.40},
    "收拢":     {"scale0": 1.35},
    "折叠":     {"scale0": 1.30, "alpha0": 0.2},
    "滚入":     {"scale0": 1.00, "alpha0": 0.4},
    "随机上升": {"scale0": 1.00, "alpha0": 0.0},
    "向右缓入": {"scale0": 1.00, "alpha0": 0.5},
    "雪光模糊": {"blur0": 32.0},
    "向右模糊 II": {"blur0": 32.0},
}

# 无法用整块变换表达的（次原子/逐字/粒子/声波等）：按最近族兜底，并计入 unrealized
APPROX_FALLBACK: Dict[str, str] = {
    "打字光标": "reveal", "逐字显影": "reveal", "打字机": "reveal",
    "新年打字机": "reveal", "开幕": "reveal", "开幕 II": "reveal",
    "冰雪飘动": "blur", "呐喊声波": "blur", "激光雕刻": "reveal",
    "波浪弹入": "scale", "弹簧": "scale", "圆柱体滚动": "rotate",
}

# 这些名字是「未知/装饰性」动效，按淡入淡出兜底，并在报告里计数
FADE_FALLBACK = ("渐显", "渐隐", "闪白", "闪黑")

# 只做整块的淡入淡出（不位移不缩放）
ALPHA_ONLY = ("渐显", "渐隐", "闪白", "闪黑")

_SLIDE = {
    "向左滑动": (-1.0, 0.0),
    "向右滑动": (1.0, 0.0),
    "向上滑动": (0.0, -1.0),
    "向下滑动": (0.0, 1.0),
    "日出": (0.0, -1.0),
    "日落": (0.0, 1.0),
}

# 露出类：值是「运动方向」（屏幕坐标，右+/下+）
_REVEAL = {
    "向右露出": (1.0, 0.0),
    "向左露出": (-1.0, 0.0),
    "向上露出": (0.0, -1.0),
    "向下露出": (0.0, 1.0),
}


@dataclass
class AnimSpec:
    kind: str           # "in" / "out"
    name: str
    duration: float     # 秒
    start: Optional[float] = None   # 相对段起点的秒；None = in 用 0、out 用尾部


@dataclass
class AnimState:
    scale: float = 1.0      # 等比缩放
    scale_x: float = 1.0    # 仅横向（镜像翻转 / 露出）
    scale_y: float = 1.0    # 仅纵向
    dx: float = 0.0        # 像素，右为正
    dy: float = 0.0        # 像素，下为正
    alpha: float = 1.0
    blur: float = 0.0      # 高斯 sigma（像素）

    def sx(self) -> float:
        return self.scale * self.scale_x

    def sy(self) -> float:
        return self.scale * self.scale_y

    def is_identity(self) -> bool:
        return (abs(self.scale - 1.0) < 1e-4 and abs(self.scale_x - 1.0) < 1e-4
                and abs(self.scale_y - 1.0) < 1e-4 and abs(self.dx) < 0.05
                and abs(self.dy) < 0.05 and abs(self.alpha - 1.0) < 1e-3
                and self.blur < 0.05)


def parse_animations(seg: dict, animations_by_id: Dict[str, dict]) -> List[AnimSpec]:
    """把段上挂的入场/出场动画解析成 AnimSpec 列表（按 in → out 顺序）。"""
    specs: List[AnimSpec] = []
    for ref in seg.get("extra_material_refs") or []:
        holder = animations_by_id.get(ref) or {}
        for an in holder.get("animations") or []:
            kind = str(an.get("type") or "").strip()
            name = str(an.get("name") or "").strip()
            dur = float(an.get("duration") or 0) / US
            if not kind or dur <= 1e-4:
                continue
            start = an.get("start")
            start_s = (float(start) / US) if start not in (None, 0) else None
            if kind == "in" and start_s is None:
                start_s = 0.0
            specs.append(AnimSpec(kind, name, dur, start_s))
    specs.sort(key=lambda s: (0 if s.kind == "in" else 1, s.start or 0.0))
    return specs


def family_of(name: str) -> str:
    """动画名 → 行为族。没收录的按「淡入淡出」兜底（调用方应计入 unrealized）。"""
    if name in FAMILY:
        return FAMILY[name]
    if name in APPROX_FALLBACK:
        return APPROX_FALLBACK[name]
    return "fade"


def fallback_names(specs: List[AnimSpec]) -> List[str]:
    """需要如实上报为「兑底/近似」的动画名（每个条目一条 key，调用方拿去计数）。

    分两类：
      anim_fallback:<名>  未收录的名字 → 按淡入淡出兑底（行为可能与原版不同）
      anim_approx:<名>    收录了但只能用最近族近似（逐字/粒子/声波这类次原子动画）
    两者都**真的做了**（不是跳过），只是不假装它们是逐帧 1:1 的。
    """
    out = []
    for sp in specs:
        if sp.name not in FAMILY:
            out.append(f"anim_fallback:{sp.name}")
        elif sp.name in APPROX_FALLBACK:
            out.append(f"anim_approx:{sp.name}")
    return out


def _cfg(name: str, ctx: str | None = None, dur: float | None = None) -> dict:
    """幅度表：DEFAULTS → FAMILY_DEFAULTS → CALIBRATION → CTX_CALIBRATION[ctx]（沿继承链）。

    dur 给了就再叠一层「名字@时长」二级键——同名动画在不同时长下不是同一档
    （「抖动下降」0.4s 是黑场淡入、其它时长不带透明度），二级键只覆盖它自己那一档。
    """
    fam = family_of(name)
    base = dict(DEFAULTS.get(name) or DEFAULTS.get(fam) or DEFAULTS["渐显"])
    base.update(FAMILY_DEFAULTS.get(name) or {})
    base.update(CALIBRATION.get(name) or {})
    if ctx:
        chain: List[str] = []
        c: Optional[str] = ctx
        while c:
            chain.append(c)
            c = CTX_PARENT.get(c)
        for c in reversed(chain):
            tbl = CTX_CALIBRATION.get(c) or {}
            base.update(tbl.get(name) or {})
            if dur is not None:
                base.update(tbl.get(f"{name}@{dur:.2f}") or {})
    return base


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def _grid_dir(name: str) -> Tuple[float, float]:
    """族里没登记方向的名字：按名字里的方位词推方向，推不出就当「上升」。

    返回的是**运动方向**（屏幕坐标，右+/下+）。
    """
    if "左" in name:
        return (-1.0, 0.0)
    if "右" in name:
        return (1.0, 0.0)
    if "下" in name:
        return (0.0, 1.0)
    return (0.0, -1.0)


def anim_state(specs: List[AnimSpec], t_local: float, seg_dur: float,
               W: int, H: int, ctx: str | None = None) -> AnimState:
    """段内本地时间 t_local（秒）→ 动画修正量。多个动画重叠时按乘法/叠加合成。

    W/H 是「位移的单位尺寸」：视频叠加层传画布宽高（卡片的滑动量是按画布反解的），
    文字层传文本包围盒宽高（剪映文字入场是相对文本框的，拿画布算会把一行字甩出屏幕）。
    ctx 选幅度表：None = 贴纸那套标定（sticker_render 的旧行为），"layer" = 视频/文字层，
    "layer_photo" = 视频轨上的照片图层（继承 "layer"，再叠它自己的「名字@时长」标定）。
    """
    st = AnimState()
    if not specs:
        return st
    for sp in specs:
        cfg = _cfg(sp.name, ctx, sp.duration)
        if sp.kind == "in":
            dur_i = sp.duration * float(cfg.get("in_dur_scale") or 1.0)
            t0 = (sp.start or 0.0) + float(cfg.get("in_start_shift") or 0.0)
            t1 = t0 + dur_i
            p_raw = (t_local - t0) / max(1e-6, dur_i)
            p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
            # 起点权重 = 1 - p，终点归位
            w = 1.0 - p
        else:
            dur_o = sp.duration * float(cfg.get("out_dur_scale") or 1.0)
            if sp.start is not None:
                t1 = sp.start + dur_o
                # 草稿 start 是「相对段起点的偏移」，但剪映实际按「距段尾多久开始」计时
                if t1 > seg_dur:
                    t1 = seg_dur
            else:
                t1 = seg_dur - float(cfg.get("out_start_slack") or 0.0)
            t0 = t1 - dur_o
            p_raw = (t_local - t0) / max(1e-6, dur_o)
            p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
            w = p
        if w <= 0:
            continue
        # 缓动：剪映的入场/出场不是线性，偏移量按 w^k 退化（w=1 → 起点，w=0 → 归位）。
        # k 由原版帧逐帧反解（见 CTX_CALIBRATION 注释），linear = 1。
        # 透明度另走 alpha_ease：实测尺寸与亮度进程不是同一条曲线（缩小是尺寸线性 + 亮度 out-cubic）。
        wa = w ** float(cfg.get("alpha_ease") or 1.0)
        w_raw = w          # 未加 ease 的剩余权重，「抖动下降」的位移另有指数（drop_ease）
        w = w ** float(cfg.get("ease") or 1.0)
        # 缩放：从 cfg.scale0 回 1.0（w=1 时是起点值）
        st.scale *= _lerp(1.0, cfg["scale0"], w)
        # 透明度
        st.alpha *= _lerp(1.0, cfg["alpha0"], wa)
        # 模糊：跟透明度同一条曲线（先亮后清），用线性衰减会「糊到动画结束」
        st.blur = max(st.blur, _lerp(0.0, cfg["blur0"], wa))
        # 「镜像翻转」：横向从 flip0 张开到 1（纵向不动）
        fam = family_of(sp.name)
        if fam == "flipx":
            st.scale_x *= _lerp(1.0, float(cfg.get("flip0") if cfg.get("flip0") is not None else 0.0), w)
        # 「抖动下降」：从画面上方落下（原动画的阻尼抖动只复刻主位移）。
        # 实测放下与缩小的衰减指数不同（2.4 vs 3.5），所以位移单独用 drop_ease，缺省跟 ease。
        if fam == "drop":
            wd = w_raw ** float(cfg.get("drop_ease") or cfg.get("ease") or 1.0)
            st.dy -= H * float(cfg.get("drop") or 0.0) * wd
        # 位移：入场从反方向进来，出场往运动方向出去（都按「一整帧」算）
        if sp.name in _SLIDE or fam == "slide":
            ux, uy = _SLIDE.get(sp.name) or _grid_dir(sp.name)
        elif sp.name in _REVEAL:
            ux, uy = _REVEAL[sp.name]
        else:
            ux = uy = None
        if ux is not None:
            sign = -1.0 if sp.kind == "in" else 1.0
            gain = float(cfg.get("slide_gain") or 1.0)
            st.dx += sign * ux * W * gain * w
            st.dy += sign * uy * H * gain * w
    # 钳位
    st.alpha = max(0.0, min(1.0, st.alpha))
    st.blur = max(0.0, min(64.0, st.blur))
    return st


def _blur_span(sp: AnimSpec, seg_dur: float, cfg: dict) -> Tuple[float, float]:
    """模糊族动画在段内的时间窗 [t0, t1]（秒，段内本地时间）。和 anim_state 同一套计时。"""
    if sp.kind == "in":
        dur_i = sp.duration * float(cfg.get("in_dur_scale") or 1.0)
        t0 = (sp.start or 0.0) + float(cfg.get("in_start_shift") or 0.0)
        return t0, t0 + dur_i
    dur_o = sp.duration * float(cfg.get("out_dur_scale") or 1.0)
    if sp.start is not None:
        t1 = min(sp.start + dur_o, seg_dur)
    else:
        t1 = seg_dur - float(cfg.get("out_start_slack") or 0.0)
    return t1 - dur_o, t1


def blur_window(specs: List[AnimSpec], seg_dur: float,
                ctx: str | None = "main") -> Optional[Tuple[float, float]]:
    """模糊族动画的合并时间窗（段内本地秒）；没有模糊族动画返回 None。"""
    wins = [_blur_span(sp, seg_dur, _cfg(sp.name, ctx)) for sp in specs
            if family_of(sp.name) == "blur"]
    if not wins:
        return None
    return min(w[0] for w in wins), max(w[1] for w in wins)


def blur_sigmas(specs: List[AnimSpec], t_local: float, seg_dur: float,
                ctx: str | None = "main") -> Tuple[float, float]:
    """模糊族动画 → (sigmaX, sigmaY) 像素值，供主轨段用 gblur=sigma:sigmaV 逐帧烘。

    「横向模糊」是椭圆高斯（x 重 y 轻；ratio 由原版帧两向梯度反解），不是各向同性 gblur。
    """
    sx = sy = 0.0
    for sp in specs:
        if family_of(sp.name) != "blur":
            continue
        cfg = _cfg(sp.name, ctx)
        t0, t1 = _blur_span(sp, seg_dur, cfg)
        p_raw = (t_local - t0) / max(1e-6, t1 - t0)
        p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
        w = (1.0 - p) if sp.kind == "in" else p
        w = w ** float(cfg.get("ease") or 1.0)
        if w <= 0:
            continue
        bx = float(cfg["blur_x0"] if cfg.get("blur_x0") is not None
                   else (cfg.get("blur0") or 0.0))
        ratio = float(cfg["blur_y_ratio"] if cfg.get("blur_y_ratio") is not None else 1.0)
        sx = max(sx, bx * w)
        sy = max(sy, bx * w * ratio)
    return sx, sy


def flash_alpha(specs: List[AnimSpec], t_local: float, seg_dur: float,
                ctx: str | None = "main") -> float:
    """模糊族里的「过曝光」曲线 → 0..1 的白场叠加量（out = (1-a)·in + 255·a）。

    剪映的「横向模糊」入场不只是模糊，还带一层白光：原版 style_21 4.85s 整帧均值 219.9，
    衰减到 5.15s 的 164（无动效基准 162.5），逐帧反解出 a 线性衰减。
    """
    a = 0.0
    for sp in specs:
        if family_of(sp.name) != "blur":
            continue
        cfg = _cfg(sp.name, ctx)
        f0 = float(cfg.get("flash0") or 0.0)
        if f0 <= 0:
            continue
        t0, t1 = _blur_span(sp, seg_dur, cfg)
        p_raw = (t_local - t0) / max(1e-6, t1 - t0)
        p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
        w = (1.0 - p) if sp.kind == "in" else p
        a = max(a, f0 * (w ** float(cfg.get("ease") or 1.0)))
    return max(0.0, min(1.0, a))


def zoom_amp(specs: List[AnimSpec], t_local: float, seg_dur: float,
             ctx: str | None = "main") -> float:
    """模糊族里的「变焦模糊」幅度（0..1，= 采样缩放半径占画幅的比例）。

    径向模糊没有 ffmpeg 滤镜可表达（-vf 只吃单链），所以主轨走 PIL 逐帧烘焙；
    幅度由原版帧反解（见 CTX_CALIBRATION["main"]["横向模糊"]["zoom0"]）。
    """
    amp = 0.0
    for sp in specs:
        if family_of(sp.name) != "blur":
            continue
        cfg = _cfg(sp.name, ctx)
        z0 = float(cfg.get("zoom0") or 0.0)
        if z0 <= 0:
            continue
        t0, t1 = _blur_span(sp, seg_dur, cfg)
        p_raw = (t_local - t0) / max(1e-6, t1 - t0)
        p = 0.0 if p_raw < 0 else (1.0 if p_raw > 1 else p_raw)
        w = (1.0 - p) if sp.kind == "in" else p
        amp = max(amp, z0 * (w ** float(cfg.get("ease") or 1.0)))
    return max(0.0, min(0.5, amp))


def alpha_ramp(specs: List[AnimSpec], seg_dur: float, ctx: str | None = None):
    """纯透明度动画 → ("in"/"out", 段内起点秒, 时长)；不是纯淡入淡出返回 None。

    纯粹的渐显/渐隐不需要逐帧重绘（ffmpeg 的 fade 滤镜就能做），有位移/缩放/模糊的才要。
    调用方据此选「淡入淡出链」还是「PIL 逐帧图层」，两条路结果一致。
    """
    if len(specs) != 1:
        return None
    sp = specs[0]
    if family_of(sp.name) != "fade":
        return None
    cfg = _cfg(sp.name, ctx, sp.duration)
    if (abs(float(cfg.get("scale0") or 1.0) - 1.0) > 1e-6
            or float(cfg.get("blur0") or 0.0) > 1e-6
            or abs(float(cfg.get("alpha0") or 0.0)) > 1e-6):
        return None
    if sp.kind == "in":
        return ("in", sp.start or 0.0, sp.duration)
    dur_o = sp.duration * float(cfg.get("out_dur_scale") or 1.0)
    if sp.start is not None:
        t1 = min(sp.start + dur_o, seg_dur)
    else:
        t1 = seg_dur - float(cfg.get("out_start_slack") or 0.0)
    return ("out", max(0.0, t1 - dur_o), dur_o)


def summarize(specs: List[AnimSpec]) -> List[str]:
    """给报告用的一行摘要。"""
    out = []
    for sp in specs:
        tag = "入" if sp.kind == "in" else "出"
        start = "" if sp.start is None else f"@{sp.start:.2f}s"
        out.append(f"{tag}:{sp.name}({sp.duration:.2f}s{start})")
    return out


def sweep(name: str, values: Tuple[float, ...], key: str) -> List[dict]:
    """标定用：生成一组覆盖值（配合外部逐值量像素差）。

        for cfg in sweep("缩小", (1.1, 1.15, 1.2, 1.25), "scale0"):
            CALIBRATION["缩小"] = cfg
            ...
    """
    return [{key: v} for v in values]


def _self_test() -> None:
    W, H = 1080, 1920
    # 0) 贴纸宿主（不带 ctx）的标定不能被「layer」那套覆盖掉：sticker_render 依赖它
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.0, 1.0, W, H)
    assert abs(s.scale - _cfg("缩小", None)["scale0"]) < 1e-6 and abs(s.alpha) < 1e-6, s
    # 1) 缩小入场：起点放大、结束归位（视频/文字层：1.5 倍 + 淡入）
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.0, 1.0, W, H, ctx="layer")
    assert abs(s.scale - _cfg("缩小", "layer")["scale0"]) < 1e-6, s
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.5, 1.0, W, H, ctx="layer")
    assert abs(s.scale - 1.0) < 1e-6, s
    s = anim_state([AnimSpec("in", "缩小", 0.5)], 0.5, 1.0, W, H)
    assert abs(s.scale - 1.0) < 1e-6, s
    # 2) 渐隐出场：尾部 alpha 归零
    s = anim_state([AnimSpec("out", "渐隐", 0.5)], 1.0, 1.0, W, H)
    assert s.alpha < 1e-6, s
    s = anim_state([AnimSpec("out", "渐隐", 0.5)], 0.5, 1.0, W, H)
    assert abs(s.alpha - 1.0) < 1e-6, s
    # 3) 向下滑动出场：末帧按标定幅度下移（屏幕坐标 y 正 = 下）
    _g = float(_cfg("向下滑动").get("slide_gain") or 1.0)
    s = anim_state([AnimSpec("out", "向下滑动", 0.5)], 1.0, 1.0, W, H)
    assert abs(s.dy - H * _g) < 1e-6, s
    # 3b) 向下滑动**入场**：起点在画面上方（dy 为负）
    s = anim_state([AnimSpec("in", "向下滑动", 0.5)], 0.0, 1.0, W, H)
    assert abs(s.dy + H * _g) < 1e-6, s
    # 3c) 日出入场：从下方升起（dy = +H）
    s = anim_state([AnimSpec("in", "日出", 0.5)], 0.0, 1.0, W, H)
    assert abs(s.dy - H) < 1e-6, s
    # 3d) 族默认值不能把 alpha 误设成 0（缩放族入场应不透明）
    s = anim_state([AnimSpec("in", "轻微放大", 0.4)], 0.0, 1.0, W, H)
    assert abs(s.alpha - 1.0) < 1e-6 and abs(s.scale - 1.06) < 1e-6, s
    # 4) 区间外不动
    assert anim_state([AnimSpec("in", "缩小", 0.5)], 0.9, 1.0, W, H).is_identity()
    # 6) 镜像翻转入场：起点横向为 0（纵向不动），结尾回 1
    s = anim_state([AnimSpec("in", "镜像翻转", 0.4)], 0.0, 1.0, W, H, ctx="layer")
    assert abs(s.sx()) < 1e-6 and abs(s.sy() - 1.0) < 1e-6 and abs(s.alpha - 1.0) < 1e-6, s
    s = anim_state([AnimSpec("in", "镜像翻转", 0.4)], 0.4, 1.0, W, H, ctx="layer")
    assert abs(s.sx() - 1.0) < 1e-6, s
    # 6b) 缓动不是线性：镜像翻转 p=0.5 已到 0.875（out-cubic），线性只到 0.5
    s = anim_state([AnimSpec("in", "镜像翻转", 0.4)], 0.2, 1.0, W, H, ctx="layer")
    assert s.sx() > 0.8, s
    # 7) 抖动下降入场：起点在画面上方且略缩小
    s = anim_state([AnimSpec("in", "抖动下降", 0.4)], 0.0, 1.0, W, H, ctx="layer")
    assert s.dy < -0.1 * H and abs(s.scale - 0.78) < 1e-6, s
    # 7b) 照片图层 0.4s 专场标定：黑场淡入（起点 alpha=0），且不污染共享的 layer 档
    s = anim_state([AnimSpec("in", "抖动下降", 0.4)], 0.0, 1.0, W, H, ctx="layer_photo")
    assert s.alpha < 1e-6 and abs(s.scale - _cfg("抖动下降", "layer_photo", 0.4)["scale0"]) < 1e-6, s
    s = anim_state([AnimSpec("in", "抖动下降", 0.4)], 0.2, 1.0, W, H, ctx="layer_photo")
    assert 0.5 < s.alpha < 1.0, s                       # 半程已亮起来（alpha_ease=6）
    # 7c) 时长二级键只盖 0.40s 那一档：0.5s 的同名动画仍是共享的「不淡入」
    s = anim_state([AnimSpec("in", "抖动下降", 0.5)], 0.0, 1.0, W, H, ctx="layer_photo")
    assert abs(s.alpha - 1.0) < 1e-6, s
    # 8) 向左滑动入场起点必须整张推出画布外（否则「叠了个寂寞」的反面：没滑）
    s = anim_state([AnimSpec("in", "向左滑动", 0.333)], 0.0, 1.0, W, H, ctx="layer")
    assert s.dx >= W * 1.1, s
    # 8b) 主轨「横向模糊」入场：白场从 0.65 线性衰减到 0，窗后为 0
    _sp = [AnimSpec("in", "横向模糊", 0.2667)]
    assert abs(flash_alpha(_sp, 0.0, 1.0) - 0.65) < 1e-6
    assert abs(flash_alpha(_sp, 0.2667 / 2, 1.0) - 0.325) < 0.01
    assert flash_alpha(_sp, 0.4, 1.0) == 0.0
    assert blur_window(_sp, 1.0) == (0.0, 0.2667)
    # 8c) 各向同性/椭圆 gblur 要显式关掉，否则会多糊一层
    assert blur_sigmas(_sp, 0.0, 1.0) == (0.0, 0.0)
    # 5) 无动画 = 恒等
    assert anim_state([], 0.3, 1.0, W, H).is_identity()
    print("animations.py self-test OK")


if __name__ == "__main__":
    _self_test()
