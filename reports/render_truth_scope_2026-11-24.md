# 渲染真值：进模版 / 重建 / 重算 / 只该进代码 的分界（2026-11-24）

> 仓库 HEAD `1dcab30`。本报告只读代码与草稿，**未改动任何已有文件**；新增的三个文件见文末「交付物」。
> 注：`dev_tools/render_replica_from_draft.py`、`dev_tools/mine_replica_assets.py`、
> `short-video-studio/scripts/*` 在本轮取证期间仍被并线修改，常量表里的**值**是本次扫描时刻读到的
> （`stroke_paper.SHADOW_INS` 就在扫描期间从 0.84 变成 0.75），出处行号以当时读到的为准。

---

## 0. 结论先行

1. **标定常量分四类，不是两类。** 除了「通用」与「每草稿」，还有第三类「**按资源 id**」——描边就是这一类：
   24 套里在用的描边资源有 **三种**，它们的 `size` 滑杆语义完全不同（见 §4）。第四类是「**按宿主**」
   （同名动画在 文字 / 视频层 / 贴纸 上幅度不同），`CTX_CALIBRATION` 已经在这么做，但只反解过 2 套草稿。
2. **描边宽度不是 `size × 常数`。** 撕纸描边 = 线性过原点（18–21 源像素/单位）；单层描边 = 带 0.3px 底宽的仿射；
   折线描边 = **包内 `size` 根本没绑定**（滑杆无效）。所以 `STROKE_SIZE_GAIN = 21` 这个数**只对撕纸描边成立**，
   而且成立的条件是「量在素材原生像素上」——换到画布还要乘 `s_disp = 素材原生宽 × clip.scale / 画布宽`。
3. **24 套里 5 套真有描边**（style_01 / 03 / 07 / 21 / 23），不是 2 套。其中 style_01 用的是第三种资源
   （单层描边），它连「px/单位」都不该有（底宽不为 0）。
4. **9 套草稿没有 `fps` 字段**。模版若只读草稿字段会拿到 0；必须以「按风格取值」的 fps=60 兜底（§7 第 6 项）。
5. **名字未收录的动画有 120 条实例 / 33 个名字**（占 73 个名字 / 579 条动画的 21%），目前按「淡入淡出」兜底——
   这是模版「换了片子仍然照搬动画」最大的失真来源，且它是**按名字**分档、不是按风格分档。

---

## 1. 方法与口径

两份数据、两条命令复现：

```bash
# 用仓库指定的 venv python（本机路径不落盘，按环境变量取）
PY=${SVS_PY:-python3}
$PY dev_tools/forensic_style_render_truth.py      # → forensic/style_render_truth.json（24 套全量）
$PY dev_tools/forensic_stroke_ring_calib.py       # → forensic/stroke_ring_calibration.json（描边交叉验证）
```

- **风格 → 草稿**：`style_spectrum_library.json` 的 `source_hash` == 明文快照的 sha256（与
  `replica_template.resolve_draft_by_style` 同一套匹配规则，两种落盘名都认）。
- **下标可回查**：真值文件里的 `material_index / track_index / segment_index` 都是草稿数组下标。
- **隐私**：真值只含 `style_id` + `source_ref` + `draft_sha256`；不含草稿中文名、本机路径、正文
  （文字只记字数与字号）。两个脚本写盘前各自跑一遍门禁，命中即拒写。
- **三个像素口径必须写清**，否则数字没法对账：
  - `src px`：素材原生像素（描边/模糊等效果在剪映里就是在这个网格上算的）；
  - `layer px`：渲染图层像素（`scale_kf` 烘焙后的图层，通常 = 原生 × clip.scale）；
  - `canvas px`：成片画布像素（1080×1920）。`canvas = src × 原生宽×clip.scale/画布宽`。

---

## 2. 24 套真值总表（细节见 `forensic/style_render_truth.json`）

† = 草稿里没有 `fps` 字段，按族内约定取 60（见 §7 第 6 项）。

| style | 画布 | fps | 时长 | 特效段 | 遮罩(生效/总) | 动画(文/视/贴) | 文字 | 字号范围 | 贴纸 | 叠加层 | 音轨/段 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| style_01 | 1080×1920 | 60† | 68.5s | 0 | 1/1 | 8/0/0 | 60 | 12–15 | 0 | 31 | 4/4 |
| style_02 | 1080×1920 | 60† | 29.47s | 1 | 1/1 | 21/0/0 | 41 | 14–32 | 1 | 4 | 2/21 |
| style_03 | 1080×1920 | 60 | 126.87s | 0 | 5/5 | 78/0/0 | 147 | 10–70 | 0 | 6 | 4/57 |
| style_04 | 1080×1920 | 60† | 19.5s | 1 | 2/2 | 14/1/0 | 25 | 9–72 | 0 | 2 | 3/14 |
| style_05 | 1080×1920 | 60 | 22.47s | 0 | 4/4 | 13/2/0 | 28 | 12–48 | 2 | 6 | 2/11 |
| style_06 | 1080×1920 | 60† | 21.67s | 0 | 2/2 | 17/0/0 | 21 | 8–20 | 0 | 1 | 2/14 |
| style_07 | 1080×1920 | 60 | 28.13s | 0 | 7/7 | 12/12/3 | 29 | 10–56 | 3 | 13 | 5/21 |
| style_08 | 1080×1920 | 60† | 58.47s | 4 | 0/0 | 28/0/0 | 70 | 12–68 | 1 | 0 | 3/30 |
| style_09 | 1080×1920 | 60 | 42.5s | 0 | 21/21 | 21/7/0 | 46 | 7–36 | 0 | 10 | 3/19 |
| style_10 | 1080×1920 | 60 | 18.92s | 0 | 8/8 | 22/0/0 | 26 | 12–64 | 0 | 10 | 4/16 |
| style_11 | 1080×1920 | 60 | 26.4s | 0 | 7/7 | 12/2/0 | 31 | 12–42 | 0 | 8 | 3/15 |
| style_12 | 1080×1920 | 60 | 26.3s | 0 | 1/1 | 10/1/0 | 28 | 12–42 | 0 | 1 | 3/18 |
| style_13 | 1080×1920 | 60 | 30.22s | 0 | 2/2 | 16/1/0 | 31 | 6–39 | 1 | 5 | 3/19 |
| style_14 | 1080×1920 | 60 | 20.9s | 0 | 1/1 | 16/1/0 | 19 | 12–28 | 1 | 2 | 3/11 |
| style_15 | 1080×1920 | 60† | 30.57s | 2 | 7/7 | 42/10/2 | 49 | 6–56 | 3 | 13 | 5/36 |
| style_16 | 1080×1920 | 60 | 28.33s | 0 | 2/2 | 18/6/0 | 27 | 8–80 | 0 | 9 | 3/14 |
| style_17 | 1080×1920 | 60† | 16.0s | 1 | 9/9 | 17/5/0 | 24 | 8–36 | 2 | 5 | 3/16 |
| style_18 | 1080×1920 | 60 | 38.2s | 0 | 1/1 | 25/1/0 | 47 | 12–64 | 1 | 7 | 3/22 |
| style_19 | 1080×1920 | 60 | 38.2s | 0 | 8/8 | 11/3/0 | 41 | 8–72 | 1 | 7 | 3/15 |
| style_20 | 1080×1920 | 60† | 30.7s | 2 | 5/5 | 28/4/0 | 42 | 6–108 | 0 | 4 | 4/27 |
| style_21 | 1080×1920 | 60 | 15.62s | 1 | 0/1 | 11/2/2 | 24 | 12–78 | 2 | 12 | 3/14 |
| style_22 | 1080×1920 | 60 | 23.48s | 0 | 1/1 | 16/2/0 | 38 | 12–76 | 0 | 8 | 4/14 |
| style_23 | 1080×1920 | 60† | 41.83s | 0 | 2/2 | 23/4/0 | 34 | 12–132 | 0 | 7 | 2/13 |
| style_24 | 1080×1920 | 60 | 36.42s | 0 | 5/5 | 18/11/0 | 35 | 12–48 | 0 | 15 | 3/20 |

跨套汇总（这 24 套是一族，很多「通用」结论因此才敢下）：

- **画布 / 时长基准一致**：全 1080×1920；时长 15.6–126.9s（3.5 倍差）→ 时长**必须**重映射，画布不必。
- **遮罩 103 条**：`circle` 49 / `rectangle` 35 / `line` 10 / **`mirror` 8 / `contour` 1（这两种
  `mask_shapes.is_supported()` 返回 False，现在进 `unrealized.mask_unsupported_type`）**；`type=="none"` 1 条（关掉）。
  feather：85 条没写（=0）、其余 0.1(×6) / 1.0(×4) / 0.14 / 0.36 / 0.66 / 0.76 / 0.84。
  roundCorner：78 条没写、0.1(×15) / 0.2(×8) / 1.0(×2)。
- **画面特效只有 7 种资源 / 12 个特效段**：模糊 `634025/6739752823140913675`（style_21，带 `effects_adjust_blur` 关键帧）、
  暗角 `634057/6723086142658318860`（style_02/08/15）、不规则黑框 `898673/6865921530488951309`（style_08/20）、
  荧幕噪点 `634779/6706773534066086413`、聚光灯 `634081/6750075966053159427`、怀旧边框 `3222474/7113820806446060040`、
  颤抖 `892172/6863326875649839623`。**参数都是标量滑杆**，没有一条是位置相关的关键帧（只有模糊那条有）。
- **动画 579 条 / 73 个名字**：渐显 61、缩小 34、打字光标 21、放大 21、向上滑动 19、冰雪飘动 17、随机上升 15、
  滚入 15、发光模糊 15、雪光模糊 15、模糊发光 14、开幕 13、轻微放大 13…（完整清单在真值文件里，逐条带 `host`）。
  未收录（按淡入淡出兜底）**120 条**；族近似（`APPROX_FALLBACK`）**97 条**。
- **文字**：阴影 `shadow_smoothing = 0.45` 占 776/约 900、`shadow_distance = 5.0` 占 802 → 这两个**几乎全族相同**，
  正是 `SHADOW_BLUR_EM=0.13` / `SHADOW_REF_WIDTH=720` 能当通用的根据（§3）。
  文字描边在这 24 套里**走 `border_width`（0.08 占 24 条 / 0.04 一条），`content.styles[*].strokes` 全为空**——
  也就是说 `parse_text_material` 里「`strokes[0].width × font_size × PX_PER_UNIT`」那条路径在 24 套里**一次都没触发**。
  字体 basename top：得意黑.ttf 180、zh-hans.ttf 161、造字工房俊雅体.ttf 98、FZCuJinLJW.ttf 83、
  特黑体-思源黑体1号.otf 77、SourceHanSerifCN-Heavy.otf 63、DouyinSansBold.otf 54…
- **贴纸**：18 条（15 条无旋转、90°/45° 各 1 条、翻转 0 条）；`clip.scale` 从 0.01 到 1.0 都有（0.01 是缩到看不见的退场帧），`rotation` 只 3 条非零（90/45），
  翻转 0 条。
- **音频**：音量字段在**段**上（`segment.volume`），不在素材上；取值从 0.001（当静音用）到 2.496。

---

## 3. 标定常量全量清点

**分档口径**：`通用` = 与草稿/风格无关（物理或剪映全族约定）；`每风格` = 该风格的渲染真值，换片子要照搬；
`每草稿` = 只在单条草稿上被反解的实例值（进模版要按实例，不能外推）；`按资源`/`按宿主` = 由草稿字段选档。

### 3.1 `dev_tools/render_replica_from_draft.py`

| 常量 | 值 | 出处 | 反解依据 | 反解用的草稿 | 分档 | 判据（从草稿哪个字段取） |
|---|---|---|---|---|---|---|
| `PTS_LAG_FRAMES` | 1 | :52 | overlay 给主帧 t 选副输入取「PTS ≥ t 的第一帧」→ 叠层整体领先 1 帧；逐帧 dy 0/2/10/23/42/65/94/128/167 对 3/10/23/… | style_21 | 通用 | 实现口径，不随草稿变 |
| `TEXT_LAG_FRAMES` | 1 | :59 | 段起点那帧原版**还没字**、下一帧直接满不透明（墨迹均值 198.5） | style_17 | 通用 | 同上（env 可关） |
| `PX_PER_UNIT` | 4.0 | :242 | 旧口径，已被 `canvas_w/172.8` 取代 | — | **废弃** | 不该再被使用 |
| 字号单位 `172.8` | `canvas_w/172.8` | :1468 | size12 黄字原版墨高 69px / 复刻 62px、宽 517/468，双向 10.5% = 192/172.8 | style_17（+style_21 192 单位=517px 复核） | 通用 | 画布宽比例，不随草稿变 |
| `SHADOW_REF_WIDTH` | 720.0 | :249 | 静态字幕三帧把「近白墨迹 alpha」与「变暗阴影 alpha」分开量 50% 边界：每轴 +4~6px；0.6364×5.0×1.5=4.8 落在中位 | style_21 | 通用 | 720 口径同 `STICKER_REF_WIDTH`；24 套 `shadow_distance=5.0` 占 802 条佐证 |
| `SHADOW_BLUR_EM` | 0.13 | :250 | σ 与**字号**成正比而与画布无关：75px→σ4.4、300px→σ11.4（线性残差 ≤1px） | style_21 | 通用 | 24 套 `shadow_smoothing=0.45` 占 776 条 |
| `SUB_BG_PAD_EM` | 0.41 | :255 | 底框 336×84 vs 墨迹 295×44 → 四边 20.5px，font_px≈50 → 0.41em | style_17 | 通用（**只 1 套复核**，留 per-style 覆盖位） | 文字 material 的底框字段 |
| `YES_BLUR_TAPS_GAIN` | 72.0 | :985 | effect 634025 的 lua：`intensity = v×72`、`u_Sample = floor(intensity)` | shader 真源 | 通用 | `effects_adjust_blur` 值本身 |
| `YES_BLUR_SIGMA_TAPS` | 4/15 | :986 | `GaussianBlur.frag`：`w_i = normpdf(i/s·15, σ=4)` → 索引空间 σ=4N/15 | shader 真源 | 通用 | 同上 |
| `YES_BLUR_REF_WIDTH` | 720.0 | :987 | 每 tap = `min(S_w,S_h)/720` 图层像素 | shader 真源 | 通用 | 图层尺寸，不随草稿变 |
| `SEG_BLUR_GAIN` | 0（旧 10） | :988 | 旧式线性口径，把「tap 数」当 σ，差一个数量级 | — | **废弃（仅 A/B）** | — |
| `STROKE_MATTE_DILATE` | 46.0 | :1280 | 逐帧减背景后：紧轮廓外有 60px 亮带、原版纯白像素距离直方图 45–60px 处同峰 → 环带中心 52px − 11/2 ≈ 46 | style_21 | **每草稿**（其实是每实例） | `matting.has_use_quick_brush` + 素材原生宽/画布宽（24 套里 5 套 quick_brush） |
| `STROKE_RESOURCE_ID` | 7172457738302132773 | :1282 | 兜底值（草稿里带 resource_id） | style_21 | **每风格** | `matting.strokes[0].resource_id`（24 套里出现 3 种） |
| 描边滑杆默认档 | 0.35 / 0.25 / 0.6 | :1318 附近 | extra.json 的默认值，草稿没写 value 时回落 | — | 兜底 | `matting.strokes[0].adjust_params` |

### 3.2 `short-video-studio/scripts/stroke_paper.py`（描边 shader 移植，常数住在这一层）

| 常量 | 值 | 出处 | 反解依据 | 草稿 | 分档 |
|---|---|---|---|---|---|
| `LINE_PX` | 1.0 | :89 | shader `offset = lineSize/mySize`、`mySize = xy/min*720` → 1 单位 = 1 图层像素；扫 1.0/1.3/1.6 差量 16.26/16.32/16.72 | style_21 | 通用（单位换算） |
| `SHADOW_INS` | 0.75（本轮扫描时 0.84，并线改过） | :101 | 环带最暗点 `alpha = 1 − L/Lb`：原版 0.222/0.230/0.227，ins=1.0 时 0.274/0.278/0.281 | style_21 | 通用（材质默认值，不是设计参数） |
| `BLUR3_JIT_GAIN` | 0.35 | :115 | 原版纸带内缘锯齿 ±2.5–4px、外缘 ±4–6px；取 1.0 时环被推到 230px 外 | style_21 | 通用（未暴露的 ins1） |
| `NOISE_JIT_GAIN` | 0.15 | :116 | 同上，超过环宽会把环撕碎 | style_21 | 通用 |
| `HALF_GAUSS` / `_GAUSS_TAPS` | 0.20…0.02 / 6 | :72-74 | `blur0.frag::half_gaussian_weight`（声明 9 个，循环跑 1..6） | shader 真源 | 通用 |
| `INS1` / `INS2` | 2.0 / 1.0 | :77-78 | material 二进制默认值 | shader 真源 | 通用 |
| `BLUR3_NOISE_SIZE` / `NOISE_SIZE` / `NOISE_ROT` | 3.5 / 20.0 / 1.2 | :119-121 | shader 写死常数 | shader 真源 | 通用 |

### 3.3 `short-video-studio/scripts/animations.py`（幅度/缓动，**全是逐帧反解**）

| 项 | 值 | 出处 | 反解依据 | 草稿 | 分档 |
|---|---|---|---|---|---|
| `CALIBRATION["向下滑动"]` | slide_gain 0.8 / out_dur_scale 0.8 / out_start_slack 0.03 | :83 | 逐帧 dy 序列（12.30–12.55s），线性/指数 1.5/2.5 都明显变差 | style_21（贴纸宿主） | 按宿主（贴纸另有 `STICKER_OUT_SLIDE`） |
| `CALIBRATION["缩小"]` | scale0 1.50 / alpha0 0 / in_dur_scale 1.0 / ease 2 / alpha_ease 2 | :105 | 贴纸顶边/左边两条独立直边算 s，实测 `s−1 = 0.504(1−p)²`；透明度同曲线 | style_21 | 按宿主 |
| `CTX_CALIBRATION["layer"]["缩小"]` | 1.5 / 0 / **in_dur_scale 0.6** / ease 1 / alpha_ease 6 | :126 | 卡片底边九个采样点线性 1.443/1.276/1.056；卡面亮度 p=0.11 只有 36% | style_21 | 按宿主 |
| `...["收拢"]` | scale0 1.247 / alpha0 1.0 / ease 3.5 | :145 | 带内墨迹宽度 116/109/104/101/98/96/94/94/93 → 余量 23/16/11/8/5/3/1/1/0 | style_17 | 按宿主 |
| `...["雪光模糊"]` | alpha0 0 / blur0 32 / ease 1 / alpha_ease 6 | :148 | 0.53s 动画过半时原版已又亮又清 | style_17 | 按宿主 |
| `...["镜像翻转"]` | flip0 0 / ease 3.0 | :151 | 12 帧量到 `abs(scale_x)` 0.363→1.0，拟合 `1−(1−p)³` 残差 ±0.02 | style_21 | 按宿主 |
| `...["向左/向右滑动"]` | slide_gain 1.2 / ease 2.2 | :154 | 两张卡片回归 k=2.16/2.22、A=1218/1319px（画布宽 1080） | style_21 | 按宿主 |
| `...["抖动下降"]` | scale0 0.78 / drop 0.115 / ease 2.4 | :158 | 底边 −212px→0、宽度 727→918 | style_21 | 按宿主 |
| `...["向右露出"]` | slide_gain 1.02 / ease 1.86 | :176 | 只量金色墨迹右边界 17 帧；A=936px、k=1.86，残差 1.4px（并先证伪「遮罩擦除」：整块字形左移而非逐列揭示） | style_21 | 按宿主 |
| `...["日出"]` | slide_gain 0.934 / ease 3.0 / alpha_ease 3.7 | :197 | 原版−底片得纯文字层，逐帧搜 (dy, alpha)：A=565px、k=3.00、e=3.7 | style_21 | 按宿主 |
| `CTX_CALIBRATION["layer_photo"]["抖动下降@0.40"]` | 0.72 / 0 / 2.4 / 6.0 / drop 0.20 / drop_ease 3.5 | :217 | 5 维网格搜，14 帧整帧灰度 MAE 11.05→2.78 | style_17 | 按宿主 × **时长二级键** |
| `CTX_CALIBRATION["main"]["横向模糊"]` | flash0 0.65 / ease 1 / in_dur_scale 1.0 / **blur_x0 0** | :231 | 整帧均值 219.9 衰减到 164（基准 162.5）→ `out=(1−a)in+255a`，a 线性 | style_21 | 按宿主（主轨） |
| `DEFAULTS` / `FAMILY_DEFAULTS` | 见 :38-73 / :264 | 同族共享初值 + 个别名字单独调 | — | **占位值，非实测** | 按名字 |
| 钳位 | alpha 0..1、blur ≤64、zoom ≤0.5 | :500-501 | 防溢出 | — | 通用 |

### 3.4 其余文件

| 常量 | 值 | 出处 | 依据 | 草稿 | 分档 |
|---|---|---|---|---|---|
| `STICKER_REF_WIDTH` | 720.0 | sticker_render.py:41 | `显示宽 = 原生宽 × scale × 画布宽/720`；11.0s 帧量「手+手机」上边界 0.115 vs 算得 0.114 | style_21 | 通用 |
| `STICKER_OUT_SLIDE["向下滑动"]` | uy 1.0 / ease 2.0 / gain 1.229 | sticker_render.py:61 | 顶边带模板逐帧搜 dy：0/2/10/23/42/65/94/128/167，拟合 `dy=9440u²` 残差 ≤1.3px | style_21 | 按宿主（sticker）× 名字 |
| `STICKER_ROT_EPS` | 0.5° | sticker_render.py:78 | 低于此角度的抖动不值得多两趟重采样 | — | 通用（工程阈值） |
| 翻转/旋转符号 | 先镜像再 `−rotation` 逆时针 | sticker_render.py:66-77 | 红箭头 2×2 组合按红掩模 IoU：flip=False 0.028/0.037、**flip=True,+77.4 → 0.926** | style_21 | 通用（符号是剪映约定） |
| `ALPHA_SKIP` | 0.004 | anim_render.py:42 | 全透明帧不落盘 | — | 通用（工程阈值） |
| `BLUR_MAX` | 40.0 | anim_render.py:43 | 模糊类动画上界 | — | 通用（工程阈值） |
| `FEATHER_GAIN` | 0.6 | mask_shapes.py:66 | `u_diff = feather×6`；全帧最小二乘 config.feather=0.1 → u_diff 0.056–0.0725；照搬 6 会宽 10 倍（MAE 4.1→9.4） | style_21 | **按类**：矩形/圆形 ×6 口径，`linear` 是直传（`SUPPORTED_MASK_TYPES` 里 line/linear 走另一条） |
| `CIRCLE_ROUND_CORNER` | 1.0 | mask_shapes.py:69 | 草稿圆形遮罩不带 roundCorner，UI「圆形」= 圆角拉满 | style_21 | 通用 |
| `FACE_HEADROOM` | 0.44 | face_framing.py:27 | 脸中心放裁切窗高度 44%，正中会头顶贴边 | 素材侧 | 按素材（不是风格） |
| `FACE_MAIN_RATIO` | 0.22 | face_framing.py:29 | 脸占窗比例达到 0.22 才当「脸是主体」 | 素材侧 | 按素材 |
| `headroom_for` 台阶 | 0.40→0.50 / 0.28→0.47 | face_framing.py:38-42 | 大特写收紧头顶余量 | 素材侧 | 按素材 |
| `_SAMPLE_TS` | (0.25, 0.55, 0.85) | face_framing.py:30 | 3 点抽帧取中位防眨眼 | — | 通用 |
| `_DEDUPE_MINIMAL_STYLES` | 6 个 style_id | render_mp4.py:96 | 极简草稿去重名单 | 6 套 | **按风格（硬编码白名单）** |
| `_AI_MATTING_MODES` | 3 种 pip_mode | render_mp4.py:1378 | 需要 AI 抠像的构图模式名 | — | 按风格字段 `pip_mode` |
| `_XFADE_STYLE_MAP` | 6 种转场名 | render_mp4.py:1750 | 转场名映射 | — | 通用 |
| `SCENE_CUT_THRESHOLD` | 0.16 | common.py:233 | 切点检测 | — | 通用（工程阈值） |
| `overlay_frame_render` 裸露阈值 | `al<=0.004` 跳帧、`sc<=0.0005`、`blur>0.25` 才模糊 | overlay_frame_render.py:117-127 | 性能阈值，不是标定 | — | 通用（工程阈值） |
| 叠加层显示尺寸 | `高 = 原生高 × scale`、`宽 = 高 × 画布宽/画布高` | overlay_frame_render.py:74-79 | 主页素材 1080×1440、scale 0.2341 → 显示 189×336（原版实测 189×336）；旧实现按 `1920×0.2341` 得 449，大 34% | style_21 | 通用（画布比例裁切，非 720 口径） |
| 叠加层模糊 | `effects_adjust_blur=0.5 → σ≈4px`（旧注释） | overlay_frame_render.py:20 | **已被 `yes_blur_sigma()` 取代**（改为 shader 公式） | style_21 | 废弃 |

### 3.5 清点结论（哪一类最多）

- **真正的通用物理（不该分档）**：720 口径（贴纸/阴影/模糊/描边四处在用）、字号 172.8 口径、
  `YES_BLUR_*` 三件套、`LINE_PX`、`FEATHER_GAIN` 的 ×6 归一化、遮罩坐标单位、动画缓动「幂律」形式本身。
- **按资源 id 分档**：描边资源（3 种，语义不同）、画面特效（effect_id 决定 params 语义）。
- **按宿主分档**：动画幅度（文字 / 视频层 / 贴纸 / 照片 / 主轨）。
- **每草稿/每实例**：`STROKE_MATTE_DILATE`、贴纸出场的 gain(1.229)、各动画的幅度数值本身。
- **只在 1 套上反解过的「疑似通用」**：`SUB_BG_PAD_EM`、`FEATHER_GAIN`、`SHADOW_INS`、`BLUR3_JIT_GAIN`。

---

## 4. 交叉验证：描边白环到底是不是同一个函数

### 4.1 三个资源，三种 size 语义（读 effect 包 lua，1:1 结论，非拟合）

24 套里 `enable_matting_stroke = true` 的实例共 **5 套 / 6 条**，用到的资源是：

| resource_id | 资源名 | 用它的风格 | size | lua 里 `effects_adjust_size` 的绑定 | 函数形式 |
|---|---|---|---|---|---|
| 7172457738302132773 | 撕纸描边 | style_07、style_21 | 0.35 | `blur4/5.lineSize = remap(size,0,3)`、`blurY/X.lineSize = remap(size,0,4)` | **线性过原点** |
| 7172457125392683528 | 单层描边 | style_01 | 0.35 | `blur0/1/2/3.lineSize = remap(size,0.3,7.5)` | **仿射带 0.3px 底宽** |
| 7172458277844816391 | 折线描边 | style_03(×2)、style_23 | 0.1 / 0.12 | **没有任何绑定**（lua 只认 `matting_stroke_adjust_color` 与 `effects_adjust_alpha`；包内 `image/` 为空、无画线 shader，掩膜来自 `share://bgmask.texture` 的算法图） | **size 无效** |

> 这一条本身就是答案：`STROKE_SIZE_GAIN = 21` 被当成「通用物理」是**错的归因**——它只在撕纸描边上成立。

### 4.2 量像素（同一套方法：合成 alpha → 按 shader 出图 → 法向扫描线量白环）

用 `stroke_paper.render_stroke_layer()`（撕纸描边 shader 的 1:1 numpy 移植）画一个 900×900、半径 260 的圆盘，
前景用红色（环带是纸白、内部是原画、外部是黑投影 → 一条扫描线上三种颜色互不混淆），取中心 ±10 行中位：

| size | band50（亮度≥50%） | band20（含羽化） | 含投影外沿（**不是**环宽） |
|---|---|---|---|
| 0.10 | 1.0 px | 1.0 | 3 |
| 0.20 | 3.0 | 3.0 | 7 |
| 0.30 | 5.0 | 5.0 | 12 |
| 0.35 | 6.0 | 6.0 | 14 |
| 0.45 | 8.0 | 8.0 | 18 |
| 0.60 | 10.5 | 11.0 | 24 |

线性拟合（distance=0.25 那一组，残差 ≤0.2px）：

```
环宽(图层px) = 19.16 · size − 0.80      # band50
环宽(图层px) = 20.00 · size − 1.00      # band20
纯 shader 抽头：6 抽头 × 3·size = 18·size   # blur4/5 各侧 6 步、每步 3·size px
```

**结论：对 size 是线性的（过原点，截距 −1px 是阈值口径造成的，不是物理偏置）；distance 不影响环宽**
（0.15/0.25/0.40 三档 band50 中位 5.5/5.5/5.0），只把环往外推（位置 = `42·distance + 9·size` 图层像素）。
所以 `21` 这个常数 = 「18 抽头 + ~1px 羽化 + 真实轮廓量不准」的量，属同一族。

### 4.3 换算到画布：同一资源也会差 1.33 倍

描边在**素材原生像素**网格上算出来，再随 `clip.scale` 放大铺到画布：

```
环宽(canvas px) = 环宽(src px) × s_disp ,  s_disp = 素材原生宽 × clip.scale / 画布宽
```

| 风格 | size | 素材原生 | clip.scale | s_disp | 预测图层环宽 | 预测画布环宽 | 公开锚点 | 对账 |
|---|---|---|---|---|---|---|---|---|
| style_21 | 0.35 | 1080×1920 | 1.6 | 1.60 | 5.9 px | **9.4 px** | 11 px（canvas，法向） | 差 1.6px（真实轮廓+纸纹+阈值口径），量级一致 |
| style_07 | 0.35 | 1080×1920 | 1.2 | 1.20 | 5.9 px | **7.1 px** | 6 px（未注明口径） | 若锚点是**源像素**口径则 5.9 ≈ 6 ✔；若是画布口径，7.1 vs 6 差 1.1px |
| style_03 | 0.1 | 420×598 | 1.057 / 1.0 | 0.411 / 0.389 | — | — | 21 px（canvas，中位） | 折线描边不可由包推导；按撕纸公式算只该有 **0.4 px** → 实测是它的 **≈50 倍** |
| style_23 | 0.12 | 600×906 | 1.0 | 0.556 | — | — | 未测 | 同资源第二条独立佐证（size 0.12 vs 0.1） |

**所以「21px vs 6px vs 21px」三个数并不矛盾，它们量在三个不同的口径上**：
0.35 × 18–20 ≈ 5.9–7.0 **源像素**（style_07 的 6 就是它）；× 1.5–1.6 = 9–11 **画布像素**（style_21 的 11 就是它）；
style_03 的 21 px 属于**另一种资源**（折线描边，算法图掩膜），在同一 size 下比撕纸宽约两个数量级。

### 4.4 「199 px/单位」这个反推为什么不能当成增益

`style_03` 那条描边（折线描边）：size = 0.10，素材 420×598，display ≈ 444×632（s_disp ≈ 0.41）。
公开笔记里 21px / 0.10 ≈ 210（≈199）这个算法把 **size 当成了有效的线性滑杆**。但：

1. 该资源包里 `size` 没有任何 uniform 绑定（§4.1），改 size 渲染不会变；
2. 若它真是线性，同一 size 下它比撕纸宽 50 倍，而它与撕纸的 shader 结构（六抽头 max 膨胀）毫无共同点；
3. 因此 210 只是「21 ÷ 0.1」这次除法，**不是可外推的增益**——换一个 size 值这个数就变，而画面不变。

正确的工程表述是：**折线描边的环宽由端上算法图的掩膜决定，只能按实例实测（每套风格一次），
不能写成 `px/单位`**；撕纸描边才可以写 `19.2 src px/单位`（band50）或 `21`（含羽化口径）。

---

## 5. 分界：进模版 / 重建 / 重算 / 只该进代码

### 5.1 必须进模版（换素材后仍要照搬，一个字都不能变）

| 真值 | 为什么 | 现在的模版有吗 |
|---|---|---|
| 描边：`resource_id` + `size/distance/texture/alpha` + 颜色 | 是设计值；换素材后「还是那个纸边」 | 有 name/color/params，**缺 `resource_id`** ← 必须补 |
| 描边的**图层口径**（`s_disp = 原生宽×clip.scale/画布宽`） | 决定环宽在画布上多大 | 没有 |
| 画面特效：`effect_id`/`resource_id` + `effects_adjust_*` | 特效是设计值（暗角/黑框/聚光灯） | 有 |
| z 序：段级 `render_index` / `track_render_index` | 换素材不改层叠关系 | 有 |
| 动画：名字 + 时长 + 段内相对起点 + 宿主 | 动效是设计（节奏） | 有名字/时长/比例 |
| 遮罩：类型 + `width/height/centerX/Y/rotation/feather/roundCorner` | 构图设计 | 有（`mask_params_unvalidated_on_new_assets` 已标） |
| 文字：字号(单位)、颜色、描边 `border_width`、阴影 5 参数、底框、字体身份 | 版式设计 | 有 text_slots |
| 贴纸：`resource_id`、`clip.scale`、`rotation`、`flip`、`transform` | 位置与朝向是设计 | 有 |
| 音频：条数/角色 + `segment.volume` 相对关系 | 混音响度是设计 | 部分（音量在段上，容易被漏搬） |
| 画布 + fps | 1080×1920@60 是全族约定 | 有画布；**fps 需兜底** |

### 5.2 必须重建（换素材后原值失效）

| 真值 | 为什么失效 | 怎么重建 |
|---|---|---|
| 抠像 matte（`matting.path`） | 原 matte 是**原素材**的逐帧 alpha | 新素材上重跑 Vision 抠像；`same_material_as` 强绑主轨同入点 |
| 描边的 `STROKE_MATTE_DILATE = 46` | 是「快速画笔在 style_21 那条素材上留的一圈原片」，与人物轮廓/边缘羽化有关 | 按新素材的 matte 边缘实测或按 `has_use_quick_brush` 分档取 |
| 折线描边的环宽 | 算法图掩膜随素材变 | 每套按原版实测一次 |
| 人脸构图 / 裁切锚点（`FACE_*`） | 换人换脸 | 每段用 Vision 检脸重新算；检不到脸就如实退回居中 |
| A-roll 同源绑定 | 原片「同一素材切出的人物压在背景上」 | 按模版 `same_material_as` 绑定新 A-roll，不能当独立 B-roll |
| 素材级 `crop`（UV） | 裁的是原素材的画幅 | 继承 crop 比例，在新素材上重新对齐人像 |

### 5.3 必须按新素材重算

| 真值 | 依据 |
|---|---|
| 时长重映射（15.6s ↔ 126.9s，3.5 倍差） | 段时长/关键帧/动画窗口都按 `duration_ratio` 重算 |
| 字号自适应（6–132 单位，同套内跨 10 倍） | 字号 = f(版式容量, 新文案字数)；`replica_template.capacity` 已按 `canvas_w/172.8` 推 |
| 版式容量（每行字数 × 行数） | 换文案就要重排 |
| 遮罩/关键帧的时间轴 | 段起点变了，`time_offset` 全部平移 |
| 贴纸/卡片的入场顺序与 `.01s` 级吸附 | 段边界变了，按帧网格重算 |

### 5.4 只该进代码、永远不该进模版（剪映内部物理）

| 物理 | 公式/常数 |
|---|---|
| 字号单位 | `font_px = size × canvas_w / 172.8` |
| 阴影位移 | `720` 口径：`shadow_point × distance × canvas_w/720` |
| 阴影模糊 | `σ = shadow_smoothing × font_px × 0.13`（∝ 字号，与画布无关） |
| 贴纸缩放 | `显示宽 = 原生宽 × scale × canvas_w/720` |
| 模糊特效 | `σ = (4·floor(72v)/15) × min(S)/720`、`RTRatio = 1 − 0.75v` |
| 遮罩羽化 | `u_diff = feather × 6 × (1/10 归一化)` |
| 描边 shader | `offset = lineSize/mySize`、`mySize = xy/min·720`、6 抽头 |
| 动画缓动 | 幂律 `w^k`（不是贝塞尔） |
| overlay 帧同步 | 领先 1 帧（`PTS_LAG_FRAMES`） |

---

## 6. 建议清单（交给主会话接线；我没有改任何已有代码）

按「风险 × 收益」排：

1. **`bake_stroke_layer` 会拿错 shader（真 bug 级）**：`matting_stroke_spec()` 读出 `resource_id`，
   但 `bake_stroke_layer` 无条件用 `stroke_paper`（只实现了 7172457738302132773）。5 套含描边的风格里有
   **3 套**（style_01 / 03 / 23）给的是别的资源，会**用撕纸的 shader 画别的效果**且不报错。
   建议：`if spec["resource_id"] != stroke_paper.EFFECT_RESOURCE_ID: 计入 unrealized['stroke_shader_unsupported:<rid>'] 并跳过烘焙`。
2. **描边宽度常数改成「按资源 + 按口径」**：删除对 `STROKE_SIZE_GAIN` 式的单值依赖，改
   `ring_src_px = gain(resource_id) * size`，画布换算用 `s_disp`；`gain` 表先落 3 条（§4.1）。
3. **模版补 `stroke_resource_id`**（`replica_template.py` 的 `matting` 块只存了 name/color/params）。
4. **`STROKE_MATTE_DILATE` 改为模版/草稿取值**：`matting.quick_brush` 为真时才有这圈余量；
   值按 `素材原生宽/画布宽` 归一后记录（style_21 是 1080/1080 → 46；若素材是 4K 就该是 46×2）。
5. **`CTX_CALIBRATION` 外置成数据**（`assets/style_spectrum/animation_calibration.json`）：
   现在它是「一套草稿反解出的全局表」，而 24 套里同名动画在 5 种宿主上幅度不同、还有 120 条名字未收录。
6. **fps 兜底**：9/24 套草稿没有 `fps` 字段；`int(round(float(draft.get("fps") or fps_default)))`
   依赖调用方传对。建议在模版层显式写 `fps`（本族 = 60）。
7. **遮罩 `mirror`/`contour` 未实现**（8+1 条，涉及 style_02/03/07/15）：目前只计数不渲染。
   要么实现（`mirror` 是「镜像翻转 + 矩形」的组合，`contour` 需要 matte 轮廓），要么在模版
   `requires_review` 里显式标出来，别只在 report 计数。
8. **文字描边的字段对不上**：24 套 `content.styles[*].strokes` 全空，描边信息只出现在 material 的
   `border_width`（0.08 占 24 条 / 0.04 一条）。`parse_text_material` 只读 `styles[*].strokes`，
   等于**这类描边一次都没画**。建议补 `border_width` 口径——但先拿一帧原版确认 0.08 到底是
   「真的有窄描边」还是「剪映的浮点默认值」（两个结论的修复方向相反）。
9. **`SUB_BG_PAD_EM` / `FEATHER_GAIN` 只在 1 套上反解**：建议在第 2 套（底框用 style_02 的方角、
   遮罩用 style_09 的 21 条）上复核一次再定「通用」。
10. **给每个真值标注口径**（src / layer / canvas）：本轮三个锚点对不上号的根因全是口径没写。

---

## 7. 要让模版在 24 套上都成立，最少需要新增的「按风格取值」数据项

**七项，缺一项就会在某一套上失真**（前四项是硬需求）：

| # | 数据项 | 取值域（本轮实测） | 为什么必须按风格 | 从草稿哪个字段来 |
|---|---|---|---|---|
| 1 | `stroke.resource_id` + `stroke.resource_name` | 3 种（撕纸 / 单层 / 折线） | 三种资源的 size 语义完全不同（线性 / 带底仿射 / 无效） | `materials.videos[*].matting.strokes[0].resource_id` |
| 2 | `stroke.gain_model`（**函数形式**，不只是系数） | `linear:19.2 src px/unit` / `affine:0.3+7.2·size` / `unbound` | 同上；写成单值必然错档 | 由 1 派生（资源 → 档位表） |
| 3 | `stroke.layer_scale_key`（`s_disp` 口径） | 0.39–1.60 | 同一资源同一 size，画布环宽差 1.33–4 倍 | `素材原生宽 × clip.scale / 画布宽`（`videos[*].width`、`clip.scale.x`、`canvas_config.width`） |
| 4 | `matting.dilate_canvas_px` | 46（style_21） | 是「快速画笔留的原片余量」，按实例不同 | `matting.has_use_quick_brush` + 原生宽/画布宽 |
| 5 | `animation.amplitude[name][host][dur_band]` | 5 宿主 × 73 名字 × 时长档 | 同名动画在文字/图层/贴纸/照片/主轨上幅度不同（已在 `CTX_CALIBRATION` 体现，但只有 2 套证据） | 模版 `animations[].name/duration` + 宿主 = 轨类型 |
| 6 | `fps` | 60（9/24 套草稿无此字段） | 缺字段会退化成 0 或调用方默认 | `draft.fps`，缺失时按族约定 |
| 7 | `mask.param_semantics[resource_type]` | circle / rectangle / line / linear / **mirror / contour** | `feather` 的 ×6 只对矩形/圆形成立，`linear` 直传；后两种未实现 | `materials.common_mask[*].resource_type` |

不必新增（模版已有或该进代码）：画布、z 序（`render_index`）、特效 `effects_adjust_*`、遮罩几何、
字号（`text_slots` 已有）、字体身份、贴纸 `resource_id/scale/rotation/flip`、音量（段上已有）。

---

## 8. 已经做到的 / 没做到的

**做到**
- 24 套全量真值落盘（画布/fps/时长、逐条 matting、逐条特效含关键帧、遮罩、动画含宿主、文字含字号/阴影/描边/字体、贴纸含旋转翻转、叠加层几何、音频条数与音量、复合片段内的段也在 `video_layers` 里按轨列出）。
- 7 个指定文件的标定常量清点（`render_replica_from_draft` / `animations` / `sticker_render` / `anim_render` /
  `mask_shapes` / `overlay_frame_render` / `render_mp4`）+ 3 个直接相关的（`stroke_paper` / `face_framing` / `common`）。
- 三个描边资源的功能形式判定（读 lua）+ 撕纸描边的像素级标定（18 / 19.2 / 21 三种口径）+ 逐风格画布换算表。
- 隐私门禁：对本轮新增的两个脚本用同一份禁用串表逐字 grep（编辑软件目录名 / 用户主目录前缀 /
  第三方工程包前缀 / 明文快照目录中文名）→ **零命中**；两个脚本写盘前各自再跑一遍自己的门禁。
- `python -m unittest discover -s tests -q` → `OK (skipped=1)`。

**没做到（如实说）**
1. **没有拿到原版视频**：本机只有明文草稿与 effect 缓存，没有这 24 套的成片帧。所以「白环宽度」的
   真值只能**从 shader 反推 + 合成剪影测量**，公开锚点（11 / 6 / 21 px）只能**对账**不能**复现**。
   §4.3 里 style_21 的 9.4 vs 11 那 1.6px 差、style_07 的 7.1 vs 6 那 1.1px 差，都需要一次原版逐帧才能定死。
2. **折线描边（7172458277844816391）不可由包推导**：包内无画线 shader、`image/` 为空、掩膜来自
   `share://bgmask.texture` 的端上算法。它在 style_03/style_23 上的真实环宽只能靠原版帧实测。
3. **单层描边（7172457125392683528）只做了 lua 判定，没有跑像素**：它的环是「两趟 7 抽头高斯 + smoothstep(0.05, 0.05+mix)」，
   不是 max 膨胀，要另写一个移植才能量；本轮只给出函数形式（`0.3 + 7.2·size`）。
4. **动画幅度的 per-style 值没有逐套反解**：24 套里 73 个名字、只有 6 个名字有实测幅度，
   其余仍是 `DEFAULTS`/`FAMILY_DEFAULTS` 的占位值。要做完需要逐套原版帧，属另一个工作包。
5. 复合片段内部（`materials.drafts`）的 matting/stroke 我只在顶层 `materials.videos` 里清点；
   子时间线里的同类实例没有单独展开（`video_layers` 会把子时间线的段按父段计入，但 matting 只算顶层）。

---

## 交付物

- [forensic/style_render_truth.json](../forensic/style_render_truth.json) — 24 套全量渲染真值
- [forensic/stroke_ring_calibration.json](../forensic/stroke_ring_calibration.json) — 描边白环交叉验证
- [dev_tools/forensic_style_render_truth.py](../dev_tools/forensic_style_render_truth.py) — 真值清点（只读）
- [dev_tools/forensic_stroke_ring_calib.py](../dev_tools/forensic_stroke_ring_calib.py) — 描边标定（只读）
- 本报告
