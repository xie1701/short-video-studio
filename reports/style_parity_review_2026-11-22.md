# 24 套风格 vs 原剪映草稿：全量差异审计（解密复核版 · 第二轮）

> 审计对象：`short-video-studio` v3.2（commit `f434f9c`）
> 第一轮：2026-11-22 早；第二轮（解密后全量复核）：2026-11-22 晚
> 结论口径：**当前不是 24/24 一比一**。按「模板语法」可修到 24/24；「像素/字体/音乐」1:1 取决于精确资产接线（见 §8），不是代码不可达。

---

## 0.5 第二轮：加密主稿全部解密，ground truth 重建

第一轮的「真实值」部分来自明文 `template.json.bak` / subdraft 拼凑，39 个主稿加密不可读。本轮已把 **剪映 6.x 草稿加密完全逆向并离线解密**：

- **算法**（逆向自本机 `/Applications/VideoFusion-macOS.app/Contents/Frameworks/libvideoeditor.dylib`，arm64 `lvve::CryptoKeyVEAdaptor::queryKey` 内联实现）：
  1. 密文文件本体是一段 base64 文本；
  2. AES-256-GCM 的 key（32B）与 IV（16B）**就嵌在这段文本自身的 12 个固定偏移**（`0x4dda188` 偏移表 = 0,7,20,33,40,47,59,66,76,89,99,127），每处取 4 字符按序拼成 48 字节；
  3. 剔除这 12 块后 base64 解码，末 16 字节为 GCM tag，其余为密文；解密即得明文 JSON。
- **实现**：`forensic/jy_draft_decrypt.py`（纯 Python，pycryptodome，含 GCM tag 校验），解密器与提取器均严格只读草稿原文，明文落在仓库取证目录。
- **覆盖**：113 个草稿目录 × 4 类文件（draft_content.json / .bak / draft_meta_info.json / template-2.tmp）共 **454 个加密文件全部解开，0 失败**，明文在 `forensic/decrypted/`；全量指标从解密主稿重建为 `forensic/jianying_ground_truth_full.json`（112 套，提取器 `forensic/jy_extract_ground_truth.py`）。
- **交叉验证**：解密主稿 vs 第一轮旧口径，**112 套核心指标 0 处差异**（v1 镜数/总段数/中位镜长/B-roll/关键帧/音效密度全部一致）——第一轮提取是忠实的。全库 **112 套草稿转场计数为 0**，坐实「24/24 源无转场，当前 xfade 系凭空推演」。

---

## 0. 一句话结论

- **真实同源**：19 套是单草稿直出，4 套是「草稿视觉 + 旧参考视频数据」的混合体，1 套是「style_08 主源数据 + 辅助源长镜标签 + 两套 KPI」的缝合体。
- **可量化硬伤**：8 套 B-roll 目标与源相差 8pp 以上，最大 49pp；23/24 套被额外加了源里根本不存在的转场；字幕底框有多处方向性错误（含 3 套受草稿保存版本影响，见 §2.5）；7 套花字仍是通用兜底；17 套没有专属音效包。
- **内部一致性**：报告与主库在 10 套上数值不一致；PIP 模式在主库与蓝图之间有 5 套自相矛盾。
- **"24/24 真草稿"** 是过度声明。当前严格可审计的是 **19 套直出 + 4 套半换血 + 1 套缝合**。

---

## 1. 审计方法与证据链

这次不是看截图猜，是把之前对剪映草稿的只读取证结果重新逐项对齐：

| 证据 | 路径 | 用途 |
| :--- | :--- | :--- |
| 全草稿取证 | `<scratch_dir>/jianying_forensic_full.json` | 114 个草稿的轨道、字幕、音频、视频、特效原始计数 |
| 65 套深度指标 | `<scratch_dir>/jianying_65_deep_techniques.json` | 中位镜长、B-roll、关键帧、SFX、色彩、字体、标签 |
| 4 套替换候选 | `<scratch_dir>/v31_4_replacement_candidates.jpg` | 证明 style_05/12/13/14 的候选源分别为对应 4 套独立参考草稿（详见 `forensic/style_sources.json`） |
| 主库 | `short-video-studio/assets/style_spectrum/style_spectrum_library.json` | 运行时真正读取的参数 |
| 蓝图 | `short-video-studio/assets/style_spectrum/template_blueprints.json` | 覆盖层、PIP、转场、动效 |
| 字幕模板 | `short-video-studio/assets/subtitle_templates/templates.json` | 字幕字体、字号、颜色、底框、Y 轴 |
| 对外报告 | `short-video-studio/assets/style_spectrum/STYLE_SPECTRUM_REPORT.md` | v3.2 的 24 套宣称值 |
| 渲染器 | `short-video-studio/scripts/render_template_overlays.py`、`render_mp4.py`、`step5_subtitles.py`、`step6_packaging.py`、`step4_build_edl.py` | 验证参数是否真被执行 |

> 取证脚本本身是严格只读的，不解密、不绕过加密草稿，只解析磁盘上已明文的 `template.json.bak` / `template.tmp` / `Timelines/*` / `subdraft/*`。这也是它能作为审计底稿的原因。

---

## 2. 同源关系真相：24 套到底从哪来

| style | 对外名称 | 实际数据源 | 类型 |
| :--- | :--- | :--- | :--- |
| `style_01` | 门店原生爆款 · 综艺分拍斜标 | `source_ref[style_01]` | 单草稿直出 |
| `style_02` | 极速跳剪 · 通宽暗灰底框 | `source_ref[style_02]` | 单草稿直出 |
| `style_03` | 暗调深度 · 通宽深红底条 | `source_ref[style_03]` | 单草稿直出 |
| `style_04` | 清冷奶油 · 极简描边宋体 | `source_ref[style_04]` | 单草稿直出 |
| `style_05` | 人像前景压巨字 | **`source_ref[style_05]`（视觉/节奏）+ 旧参考视频（时长/色彩/B-roll）** | **半换血** |
| `style_06` | 黑底拍立得 · 毛笔金句 | `source_ref[style_06]` | 单草稿直出 |
| `style_07` | 人像白边抠像 · 虚化展厅 | `source_ref[style_07]` | 单草稿直出 |
| `style_08` | 专家长镜 · 左上斜杠大标 | **`source_ref[style_08]`（数据/画面）+ 辅助源（长镜标签/关键帧）** | **缝合体** |
| `style_09` | 纯黑纪实 · 双格金线圆头像 | `source_ref[style_09]` | 单草稿直出 |
| `style_10` | 高频推拉 · 极净职场 | `source_ref[style_10]` | 单草稿直出 |
| `style_11` | 清亮知性 · 圆角胶囊字幕 | `source_ref[style_11]` | 单草稿直出 |
| `style_12` | 顶置双色悬念标 | **`source_ref[style_12]`（视觉/节奏）+ 旧参考视频（时长/色彩/B-roll）** | **半换血** |
| `style_13` | 上白圆角画框 · 下黑宽幕 | **`source_ref[style_13]`（视觉/节奏）+ 旧参考视频（时长/色彩/B-roll）** | **半换血** |
| `style_14` | 纯黑聚光圆窗 | **`source_ref[style_14]`（视觉/节奏）+ 旧参考视频（时长/色彩/B-roll）** | **半换血** |
| `style_15` | 实景访谈 · 弹性双色花字 | `source_ref[style_15]` | 单草稿直出 |
| `style_16` | 暗黑羽化分屏 · 书摘巨字 | `source_ref[style_16]` | 单草稿直出 |
| `style_17` | 暖金强切 · 双格金线 | `source_ref[style_17]` | 单草稿直出 |
| `style_18` | 户外探盘 · 3D 金属立体字 | `source_ref[style_18]` | 单草稿直出 |
| `style_19` | 冷峻数据新闻 · 抠像白条 | `source_ref[style_19]` | 单草稿直出 |
| `style_20` | 国风暖木 · 黑条双行宋体 | `source_ref[style_20]` | 单草稿直出 |
| `style_21` | 门店工艺快切 · 85% 特写 | `source_ref[style_21]` | 单草稿直出 |
| `style_22` | 九成例证拆解 · 顶栏横幅 | `source_ref[style_22]` | 单草稿直出（但 B-roll 宣称失真） |
| `style_23` | 品牌微纪录 · 95% 空镜 | `source_ref[style_23]` | 单草稿直出（但 B-roll 宣称失真） |
| `style_24` | 100% 不露脸混剪 | `source_ref[style_24]` | 单草稿直出（但 B-roll 宣称失真） |

> 关键证据：`v31_4_replacement_candidates.jpg` 明确标出了 style_05/12/13/14 的替换映射关系；而主库里这 4 套的 `duration_sec` 仍然是旧视频的 169.852 / 224.816 / 473.365 / 149.952 秒。这是「换了一半」的直接证据。

### 2.5 解密后的新发现：3 处「源状态」取决于草稿保存版本

解密后，每个草稿有多个保存态：主稿 `draft_content.json`（最新保存）、`template.json.bak`（较早的打包态）、`.backup/close.bak`（历次关闭）。三套源草稿的字幕底框在版本间不同：

| 源草稿 | 打包态 template.json.bak | **最终保存态（解密主稿）** | mtime 证据 |
| :--- | :--- | :--- | :--- |
| `source_ref[style_01]` | 100% 胶囊底框 | **0% 底框** | template-2.tmp 21:35 > template.json.bak 17:13；close.bak（最新）亦 0% |
| `source_ref[style_02]` | 100% 胶囊底框 | **67% 胶囊底框**（部分段落无底框） | 主稿 09-12 22:09 > template.json.bak 09-11 23:31 |
| `source_ref[style_11]` | 100% 胶囊底框 | **0% 底框** | 主稿 09-12 21:59 > template.json.bak 09-11 23:37 |

含义：**「源」必须钉死到某个具体保存版本**（在 `source_manifest.json` 里记 `source_file` + 内容 hash），否则 1:1 没有单一定义。style_11 整套风格的「圆角胶囊」标签、style_02 的「底条」判罚，都建立在较早的打包态上。矩阵（`style_parity_matrix.csv`）已同时更新为最终保存态口径；若确定以打包态为准，01/02/11 三行需回拨。其余 109 套草稿两种口径完全一致。

---

## 3. 全量硬指标对照

> 数值格式：`当前库 / 原草稿`。中位镜长、B-roll、关键帧、SFX 均来自真实草稿时间线，不是报告文案。

| style | 源 | 时长(s) | 中位镜长(s) | B-roll(%) | 关键帧(/min) | SFX(/min) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `style_01` | source_ref[style_01] | 68.5/68.5 | 1.67/1.87 | 53.3/57.2 | 0/0 | 24.0/1.8 |
| `style_02` | source_ref[style_02] | 29.47/29.5 | 1.13/1.13 | 11.2/11.2 | 0/0 | 40.7/40.7 |
| `style_03` | source_ref[style_03] | 126.87/126.9 | 2.63/2.63 | 9.7/9.7 | 20.8/20.8 | 25.1/25.1 |
| `style_04` | source_ref[style_04] | 19.5/19.5 | 3.6/3.6 | 22.6/22.6 | 86.2/86.2 | 40.0/40.0 |
| `style_05` | source_ref[style_05] | **169.852/22.5** | 1.43/1.43 | **52.0/27.2** | 26.7/26.7 | 27/26.7 |
| `style_06` | source_ref[style_06] | 21.67/21.7 | 3.13/3.13 | 99.8/99.8 | 0/0 | 36.0/36.0 |
| `style_07` | source_ref[style_07] | 28.13/28.1 | 1.4/1.4 | 59.8/59.8 | 34.1/34.1 | 42.7/42.7 |
| `style_08` | 42-社会评论 | 23.53/23.5 | **1.48/1.48（报告写 6.00）** | **20.0/19.9（报告写 0）** | **34.9/20.4** | 30.6/30.6 |
| `style_09` | source_ref[style_09] | 42.5/42.5 | 1.5/1.5 | 33.4/33.4 | 11.3/11.3 | 24.0/24.0 |
| `style_10` | source_ref[style_10] | 18.92/18.9 | 1.12/1.12 | 57.4/57.4 | 190.3/190.3 | 47.6/47.6 |
| `style_11` | source_ref[style_11] | 26.4/26.4 | 1.32/1.32 | 34.3/34.3 | 54.5/54.5 | 29.5/29.5 |
| `style_12` | source_ref[style_12] | **224.816/26.3** | 1.9/1.9 | **40.0/5.1** | 31.9/31.9 | 36/36.5 |
| `style_13` | source_ref[style_13] | **473.365/30.2** | 1.46/1.46 | **65.0/38.3** | 33.8/33.8 | 34/33.8 |
| `style_14` | source_ref[style_14] | **149.952/20.9** | 1.78/1.78 | **45.0/15.0** | 23.0/23.0 | 29/28.7 |
| `style_15` | source_ref[style_15] | 30.57/30.6 | 2.27/2.27 | 35.2/35.2 | 109.9/109.9 | **48.0/64.8** |
| `style_16` | source_ref[style_16] | 28.33/28.3 | 1.75/1.75 | 56.4/56.4 | 36.0/36.0 | 27.5/27.5 |
| `style_17` | source_ref[style_17] | 16.0/16.0 | 1.53/1.53 | 15.8/15.8 | 48.8/48.8 | **48.0/56.2** |
| `style_18` | source_ref[style_18] | 38.2/38.2 | 1.58/1.58 | 41.7/41.7 | 25.1/25.1 | 33.0/33.0 |
| `style_19` | source_ref[style_19] | 38.2/38.2 | 1.45/1.45 | 24.4/24.4 | 20.4/20.4 | 22.0/22.0 |
| `style_20` | source_ref[style_20] | 30.7/30.7 | 1.83/1.83 | 11.5/11.5 | 13.7/13.7 | 46.9/46.9 |
| `style_21` | source_ref[style_21] | 15.62/15.6 | 1.2/1.15 | **85.0/75.6** | 92.2/92.2 | 44.0/46.1 |
| `style_22` | source_ref[style_22] | 23.48/23.5 | 1.45/1.35 | **90.0/41.0** | 46.0/46.0 | 36.0/33.2 |
| `style_23` | source_ref[style_23] | 41.83/41.8 | **2.8/4.08** | **95.0/46.4** | 22.9/22.9 | 18.0/17.2 |
| `style_24` | source_ref[style_24] | 36.42/36.4 | 1.1/1.11 | **100.0/54.8** | 16.5/16.5 | **48.0/31.3** |

### 3.1 节奏与镜头：9 套有硬偏差

- **最大三处失真**：
  - `style_22` B-roll 90% vs 41%（+49pp）
  - `style_23` B-roll 95% vs 46.4%（+48.6pp）
  - `style_24` B-roll 100% vs 54.8%（+45.2pp）
- `style_21` 85% vs 75.6%，多 9.4pp。
- 4 套半换血的 B-roll 全部沿用旧视频：05（52 vs 27.2）、12（40 vs 5.1）、13（65 vs 38.3）、14（45 vs 15）。
- `style_01` 中位镜长 1.67 vs 1.87，报告却写 1.87——库和报告各说各话。
- `style_08` 报告写 6.00s / 0% B-roll，库写 1.48s / 20%，真实 42-社会评论是 1.48s / 19.9%；而 source_ref[style_08]真实是 29.23s / 0%。一个风格三家数。

### 3.2 字幕：底框方向错了 6 套，长句被强行截断

| style | 真实字幕底框（最终保存态） | 当前底框 | 判定 |
| :--- | :--- | :--- | :--- |
| `style_01` | 0% 底框（打包态为 100% 胶囊，见 §2.5） | `#00000000` 透明 | **存版本争议，需钉死源版本** |
| `style_02` | 67% 胶囊底框（打包态 100%） | `#1E2220D4` 通宽双行底条 | **形状错**（源是胶囊/无底框，不是通条） |
| `style_03` | 0% 底框 | `#B91C1CE6` 深红实心通条 | **无中生有** |
| `style_10` | 0% 底框 | `#0F172A99` 深色框 | **无中生有** |
| `style_11` | 0% 底框（打包态为 100% 胶囊，见 §2.5） | `#11182788` 圆角胶囊 | **存版本争议，需钉死源版本** |
| `style_17` | 42% 有底框 | `#00000000` 透明 | **缺底框** |
| `style_19` | 0% 底框 | `#FFFFFFE6` 白条 | **无中生有** |
| `style_20` | 0% 底框 | `#14100DB8` 黑条 | **无中生有** |

另外 3 个几何硬差：

- `style_10` 字幕 Y=0.721，真实 0.629——低了约 **176px**，正好压到胸口/手部。
- `style_09` 单行最多 10 字，真实 46 字；`style_13` 10 字 vs 96 字；`style_19` 10 字 vs 83 字。`step5_subtitles.py` 里 `tpl_max_chars = max(6, min(14, ...))` 把上限死死钳在 14 字，所以这三套的「长法条/长案例字幕」永远排不出来。
- 阴影：`style_17` 真实只有 58% 的字幕带阴影，当前 100% 全带。

### 3.3 包装 / PIP / 花字：7 套还是通用兜底

`render_template_overlays.py` 只对 18 个 `template_family` 做了专属绘制；以下 7 套落到最后的通用「倾斜描边字」分支：

`style_07`、`style_10`、`style_11`、`style_15`、`style_17`、`style_21`、`style_24`

这意味着：

- `style_07` 源里 **0 条花字**、29 条底字幕、3 个贴纸、7 个蒙版段；当前仍会按 24 条/分钟自动生成花字。
- `style_15` 源里 **0 条底字幕**、45–49 条展示文字（88.3–96.2 条/分钟）；当前是「底字幕 + 32 条/分钟花字」——语法完全不同。
- `style_21` 源里 0 条花字、24 条底字幕、2 个贴纸；当前通用花字会凭空加字。
- `style_24` 源里 0 条花字、1 条展示字；当前也会加。

PIP 这边，11 套模式在 `render_mp4.py` 里确有实现（圆窗、相框、双格金线、羽化分屏、数据新闻背景等），但主库和蓝图有 **5 套 PIP 模式互相打架**：

| style | 主库 `pip_composition_spec` | 蓝图 `pip_spec` | `packaging_techniques.pip_mode` |
| :--- | :--- | :--- | :--- |
| `style_05` | `person_behind_giant_text_pip` | 同左 | `paper_craft_card_pip` |
| `style_12` | `none` | `none` | `minimal_white_note_card_pip` |
| `style_13` | `top_white_rounded_frame_pip` | 同左 | `none` |
| `style_14` | `black_spotlight_circle_pip` | 同左 | `none` |
| `style_21` | `vision_portrait_cutout_pip` | 同左 | `none` |

运行时 `apply_style_spectrum.py` 读的是主库，`render_template_overlays.py` 读的是蓝图，`step6` 读的是 `packaging_techniques`——三个入口三套答案。

### 3.4 转场：23/24 套加了源里不存在的转场

所有 24 个源草稿的 `transitions` 数组都是空的。源里的「过渡感」来自单镜头 in/out 动画（渐显、横向模糊、镜像翻转、快速翻页、滑动等），不是 xfade。

当前只有 `style_08` 的 `transition_style = none`，其余 23 套都会在渲染时插入 5 帧 xfade：

`zoomin`（01/07/10/17/18/21）、`fadeblack`（02/05/09/14/15/16/19/24）、`dissolve`（03/04/06/11/13/20/23）、`wipeleft`（12/22）。

这不是「复刻」，是二次创作。对 1:1 来说，**这一步应该默认关闭**，直到能从源里逐边界提取到真实转场。

### 3.5 调色与特效：LUT 是合成的，HSL 和视频特效基本没做

- 源草稿里大量使用逐镜头 HSL：`source_ref[style_03]` 31 处、`source_ref[style_09]` 28 处、`source_ref[style_01]` 19 处。
- 当前是「17³ 合成 LUT + 60%–72% 强度混合」，不是源 LUT，也不是源 HSL。聚合 luma/warm/sat 能对上，但场景级关系对不上。
- 源视频特效未实现：暗角、怀旧边框、模糊、聚光灯、不规则黑框、颤抖、荧幕噪点。
- 源贴纸未实现：`style_07` 3 个、`style_15` 3 个、`style_17` 2 个、`style_21` 2 个、`style_02/18/19` 各 1 个。
- 源变速未实现：`style_01` 1 段、`style_24` 3 段。
- 源任意蒙版未实现：`style_09` 15 个圆形 + 6 个矩形，当前只做一个头像圆窗；`style_07` 镜面/圆形/线性/矩形共 7 段，当前只做一个人像抠像。

### 3.6 音频：7/24 有专属包，17 套靠通用音效

`assets/sound_vibe/bgm` 只有 `style_01/02/03/05/06/12/15` 七套；其余 17 套没有专属 BGM/SFX 包。`step6_packaging.py` 把 SFX 预算钳在 48 条/分钟，而源里：

- `style_15` 64.8 条/分钟
- `style_17` 56.2 条/分钟
- `style_01` 只有 1.8 条/分钟（当前 24，多了 13 倍）

源 SFX 是具体音色（回忆-刷一下、纸张抖动声、高音闪光、打字声……），当前是过渡/重音/UI 三类通用音色的复用。节奏密度可以接近，音色不可能 1:1。

### 3.7 字体：8 套同源，3 套部分同源，13 套替换

| 判定 | styles |
| :--- | :--- |
| 字体同源 | `02`、`04`、`08`、`09`、`11`、`18`、`19`、`22`（得意黑 = Smiley Sans） |
| 部分同源 | `06`（底字幕思源宋体对，展示字喜鹊古字典体不对）、`15`（展示字思源宋体对，底字幕 DouyinSansBold 不对）、`23`（展示字思源宋体对，底字幕造字工房不对） |
| 明显替换 | `01`（新青年体→阿里妈妈）、`03`（FZCuJinLJW/特黑→思源宋 Heavy）、`07`（DouyinSansBold→阿里妈妈）、`10`（知新体→Smiley）、`16`（特黑→阿里妈妈）、`17`（DouyinSansBold→思源宋 Heavy）、`20`（FZCuJinLJW/喜鹊→思源宋 Heavy）、`21`（思源宋 Heavy→阿里妈妈）、`24`（FZZJ-JHTJW→思源宋 Heavy） |

`style_05/12/13/14` 的源字体分别是得意黑/后现代体/得意黑/知新体，当前 05 用阿里妈妈、12 用 Smiley、13 用思源宋体、14 用阿里妈妈——只有 12 勉强对。

---

## 4. 代码级系统性缺口

| # | 缺口 | 证据 | 影响 |
| :--- | :--- | :--- | :--- |
| 1 | 三套真源（主库 / 蓝图 / 报告）互不同步 | 10 套报告 vs 主库数值不一致；5 套 PIP 冲突 | 换风格后实际渲染与宣称不一致 |
| 2 | 转场默认注入 | `render_mp4.py` `_XFADE_STYLE_MAP` + `xf_frames=5` | 23 套全部多出源里没有的叠化/黑场 |
| 3 | 花字只有 8 种动效原语 | `render_template_overlays.py` 的 `anim_mode` 分支 | 源里 3–8 种/套的具体动效被压成 1 种 |
| 4 | 7 套覆盖层是通用兜底 | `render_template_overlays.py` 末尾 `_draw_tilted_stroked_text` | 07/10/11/15/17/21/24 的花字版式不是源版式 |
| 5 | 字幕最多 14 字 | `step5_subtitles.py` `min(14, ...)` | 09/13/19 的长句字幕无法复刻 |
| 6 | LUT 是合成且只 60–72% 混合 | `step7_color_grade.py` `build_blended_cube_lut` | 源 HSL 场景关系丢失 |
| 7 | 视频特效/贴纸/变速/任意蒙版未实现 | 全脚本搜索无对应处理 | 源里大量装饰与节奏细节缺失 |
| 8 | SFX 预算上限 48/min | `step6_packaging.py` `min(48.0, ...)` | 15/17 等高密度风格被削 |
| 9 | 17 套无专属音效包 | `assets/sound_vibe/` 只有 7 套 | 音色与源不对应 |
| 10 | HyperFrames 样板是通用页 | `apply_style_spectrum.py` 只输出标题+一行字幕 | 不是 24 套的 1:1 可编辑样板 |
| 11 | 剪映 FCPXML 是烘焙导出 | `export_jianying.py` 把字幕/包装烤成 PNG/MOV | 打开后不能像原草稿那样改文字动效/HSL |

---

## 5. 逐风格裁决

> 分级：**P0** = 源身份/定义性指标错误，必须重做；**P1** = 主要视觉语法有硬差；**P2** = 只剩动效/密度/转场类小差。

### P0（8 套）

**`style_05` 人像前景压巨字**
- 真实 source_ref[style_05]：22.5s、1.43s、B-roll 27.2%、luma 85.1、sat 15.1%、subY 0.721、matte 3、矩形蒙版 4、展示字 56.1/min、字体得意黑。
- 当前：169.85s、B-roll 52%、luma 185、sat 9.1%、subY 0.76、topbar true（源 false）、matte 0（源 3）、展示字 15/min。
- 只有中位镜长/关键帧/SFX 对。时长、色彩、B-roll、PIP 全部是旧视频壳。

**`style_08` 专家长镜**
- 数据是 42-社会评论：23.5s、1.48s、19.9%、kf 20.4、luma 56.6、sat 45.6%。
- 名字/报告是 source_ref[style_08]：29.23s 一镜到底、0% B-roll、kf 34.9、luma 145、胶囊字幕。
- 当前 kf 用了 16 的 34.9，其余用 42 的；报告又写 6.00s/0%。三套口径，必须二选一。

**`style_12` 顶置双色悬念标**
- 真实 source_ref[style_12]：26.3s、B-roll 5.1%、luma 166.9、warm 20.6、sat 16.5%、subY 0.669、maxchars 16、topbar true。
- 当前：224.8s、B-roll 40%、luma 224.7、warm 0.97、sat 5.1%、subY 0.76、maxchars 10。
- 只有 topbar/matte/giant/节奏对。

**`style_13` 上白圆角画框**
- 真实 source_ref[style_13]：30.2s、B-roll 38.3%、luma 83.5、warm 12.8、sat 10.4%、subY 0.695、maxchars 96、matte 4、展示字 23.8/min。
- 当前：473.4s、B-roll 65%、luma 95.9、warm 26.5、sat 33.8%、subY 0.72、maxchars 10、matte 0、展示字 2/min。

**`style_14` 纯黑聚光圆窗**
- 真实 source_ref[style_14]：20.9s、B-roll 15%、luma 40.2、warm 2.5、sat 6.6%、subY 0.721、topbar false、quote true、giant 0、matte 2、展示字 43.1/min。
- 当前：149.95s、B-roll 45%、luma 43.7、warm 6.86、sat 20.5%、subY 0.76、topbar true、quote false、giant 2、matte 0、展示字 12/min。

**`style_22` 九成例证拆解**
- 源 source_ref[style_22] B-roll 41%，当前 90%；源没有顶栏、没有金句卡，当前都开了。
- 主轨 18 镜 + 8 个副轨镜头 = 26 段（`total_shots` 记的是总段数，不算夸大），但「九成画面都是例证」这个定义性卖点与源不符；展示字 43.4/min 也被写成 32/min。
- 「九成例证」这个名称本身与源不符。

**`style_23` 品牌微纪录**
- 源 source_ref[style_23] B-roll 46.4%，当前 95%；中位镜长真实 4.08s，当前 2.8s。
- 源没有金句卡，当前开了；源有 2 段圆形蒙版抠像，当前 matte 0。
- 「95% 慢调空镜」不成立。

**`style_24` 100% 不露脸混剪**
- 源 source_ref[style_24] B-roll 54.8%，当前 100%；源 dual BGM false，当前 true。
- 源 SFX 31.3/min，当前 48/min；源 matte 3，当前 0；源 luma 83.5/warm 41.1/sat 42.3%，当前 92/24/34%。
- 「100% 不露脸」不成立，源里仍有 3 段 AI 抠像。

### P1（11 套）

**`style_01`**：B-roll 53.3 vs 57.2；SFX 24 vs 1.8（多 13 倍）；luma 142.8 vs 134.2；warm 0.9 vs -12.1（方向反了）；sat 20.5 vs 32.5；字幕底框：最终保存态 0% / 打包态 100%（见 §2.5，需钉死源版本后二选一）；源 giant 0 但当前 4 条巨字；字体新青年体→阿里妈妈。报告 med 1.87 vs 库 1.67。

**`style_02`**：节奏/色彩/字体全对，但源最终态是 67% 胶囊底框+33% 无底框（打包态 100% 胶囊），当前是通宽暗灰双行底条，形状不对；源 giant 0，当前 3；展示字 32 vs 46.8/min；动效 1 种 vs 源 6 种（弹幕滚动/随机上升/打字光标/开幕/激光雕刻/晕开）。

**`style_03`**：深红实心通条是源里没有的（源底框 0%）；源顶栏常驻 true，当前 false；字体 FZCuJinLJW/特黑→思源宋 Heavy；maxchars 10 vs 15；subY .76 vs .78；31 处 HSL 未复刻；动效 1 种 vs 8 种。

**`style_07`**：通用花字兜底；源 0 条花字、29 条底字幕；字体 DouyinSansBold→阿里妈妈；7 段蒙版、3 个贴纸、6 种滑动/闪现动画未做。

**`style_09`**：多出源里没有的顶栏；maxchars 10 vs 46；源 21 段蒙版（15 圆 + 6 矩）只做了一个头像圆窗；giant 3 vs 2。

**`style_10`**：字幕 Y 0.721 vs 0.629（低 176px）；深色底框源里没有；展示字 32 vs 63.4/min；字体知新体→Smiley；动效 1 vs 4；源 60 个真实关键帧的曲线未复刻，运行时只是按 190.3/min 的预算生成均匀推拉。

**`style_15`**：源 0 条底字幕、45–49 条展示字（88.3–96.2/min），当前是「底字幕 + 32/min 花字」；SFX 48 vs 64.8；字体 DouyinSansBold 底字幕丢失；快速翻页/渐显/滑动/飘散动画未做。

**`style_17`**：源 42% 字幕有底框，当前全透明；阴影 100% vs 源 58%；maxchars 10 vs 7；字体 DouyinSansBold→思源宋 Heavy；展示字 32 vs 71.2/min；2 个贴纸、9 段蒙版未做。

**`style_19`**：白条底框源里没有（源 0%）；源有描边，当前 stroke 0；maxchars 10 vs 83；展示字 20 vs 20.4 对；动效 1 vs 4。

**`style_20`**：黑条底框源里没有（源 0%）；展示字 32 vs 54.7/min；字体 FZCuJinLJW/喜鹊→思源宋 Heavy；不规则黑框/聚光灯特效缺失；动效 1 vs 6。

**`style_21`**：B-roll 85 vs 75.6；luma 146 vs 152.5；warm 18 vs 6.3；sat 24 vs 12.3；通用花字兜底；源 0 花字、2 贴纸、5 个复合片段、模糊特效未做。

### P2（5 套）

**`style_04`**：节奏/色彩/字体/底框全对；maxchars 10 vs 7；展示字 32 vs 46.2；动效 1 vs 6；怀旧边框特效缺失；转场多加了 dissolve。

**`style_06`**：节奏/色彩/B-roll/topbar/giant 全对；展示字 32 vs 49.8；动效 1 vs 6；展示字喜鹊古字典体→马善政毛笔；转场多加了 dissolve。

**`style_11`**：各项几乎全对；展示字 32 vs 31.8；动效 1 vs 4；转场多加了 dissolve；字幕底框：最终保存态 0% / 打包态 100% 胶囊（见 §2.5，源版本钉死后二选一）。

**`style_16`**：各项几乎全对；字体特黑→阿里妈妈；展示字 32 vs 33.9；动效 1 vs 4；转场多加了 fadeblack。

**`style_18`**：各项几乎全对；展示字 32 vs 42.4；动效 1 vs 6；转场多加了 zoomin；3D 金属字实现可用。

---

## 6. 要做到 24/24「模板语法 1:1」的路线

### P0：先止血（1–2 天）

1. **建 `source_manifest.json`**：每套记录 `source_project`、`source_type`、`source_hash`、`timeline_source`、`extracted_at`，以及 30+ 原始指标。没有这个文件，不允许再写「24/24 白盒」。
2. **4 套半换血重做**：按 57/31/37/61 的真实值改 `duration`、`broll`、`luma/warm/sat`、`subY`、`maxchars`、`topbar/quote/matte/giant`、`display_per_min`；旧视频的 169.852/224.816/473.365/149.952 秒必须删掉。
3. **`style_08` 二选一**：要「专家长镜」就用 source_ref[style_08]（58.5s/29.23s/0%/kf 34.9/胶囊字幕/方正超粗黑）；要「社会评论」就改名并用 42-社会评论（23.5s/1.48s/19.9%/kf 20.4/得意黑）。不能继续缝。
4. **22/23/24 改回真实 B-roll**：41% / 46.4% / 54.8%，并同步删掉 22 的顶栏+金句卡、23 的金句卡、24 的 dual BGM。
5. **转场默认 `none`**：所有 `transition_style` 先归零，删掉无条件 xfade 注入；等能从源里逐边界提取到真实转场再开。
6. **报告重生成**：`STYLE_SPECTRUM_REPORT.md` 由主库自动生成，禁止手写第二套数。

### P1：补齐视觉语法（3–5 天）

7. **字幕底框/几何**：按 3.2 表逐套改；把 `min(14, ...)` 的上限改成 `min(96, ...)`，并为 09/13/19 单独允许长行。
8. **7 套通用花字改专属**：07/10/11/15/17/21/24 各写一个 `template_family` 分支，使用源里的花字条数、位置、颜色、字号。
9. **补 6 类缺失动效**：弹幕滚动、镜像翻转、横向模糊、快速翻页、展开/折叠、滑动出入；把每套的 `display_anims` 前 3 名映射到真实原语。
10. **音频**：为 17 套补专属 BGM/SFX 包；SFX 上限从 48 提到 72；按源 `sfx_per_min` 和 `dual_bgm` 设目标；`sfx_cut_offset_med_ms`/`sfx_text_offset_med_ms` 都是 0ms，按切点/字点对齐。
11. **调色/特效**：把 19–31 处 HSL 按镜头写成近似曲线；补暗角、怀旧边框、模糊、聚光灯、不规则黑框、颤抖、荧幕噪点；LUT 强度按源风格单独定，不再统一 60–72%。
12. **贴纸/蒙版/变速**：至少覆盖 02/07/15/17/18/19/21 的贴纸，09/07/10 的多重蒙版，01/24 的变速段。

### P2：字体、导出一致性、验收（2–3 天）

13. **字体声明**：每套加 `font_substitution` 字段，明确「源字体 / 交付字体 / 授权状态」。不能 1:1 的 13 套如实标注。
14. **导出**：HyperFrames 样板改成按风格生成，不再通用标题+一行字幕；剪映 FCPXML 明确标注「烘焙版」，另做「可编辑草稿」路线（同字体/同特效 ID）。
15. **验收**：每套固定 10s fixture 渲染，自动核对下文 KPI；再做 24 对盲审接触表。

### 6.5 A-roll/B-roll 动态配比（应用层策略，用户明确要求）

**库里的 B-roll 目标（41%/46.4%/54.8%…）是风格先验，不是硬约束。** 实际生产中 A/B-roll 配比必须按实际素材动态调整：

1. **落点改为软目标**：`step4_build_edl.py` / `apply_style_spectrum.py` 里 `broll_target` 只作为初始权重与偏差告警阈值，不再作为切分依据硬执行；新增 `broll_target_hard=false` 配置。
2. **动态配比算法**：按实际素材质量打分（A-roll 可用时长、人脸/口播段完整性、B-roll 素材覆盖率与画质），在风格先验 ±15pp 带内自适应：A-roll 充足则上调 A-roll 占比（保证口播完整性优先），B-roll 素材丰富且 A-roll 不足则上调 B-roll。
3. **风格节奏不受影响**：中位镜长、关键帧密度、SFX 密度、字幕/花字语法这些「语法层」指标仍然 1:1 锁定；B-roll 配比是「内容层」变量。
4. **验收口径同步修改**：KPI 表中 B-roll 一项从「与源 ±2pp」改为「与源 ±15pp 带内 + 记录实际值与调整原因」，避免动态调整被判为偏差。

---

## 7. 验收 KPI（建议直接写进测试）

| 维度 | 容差 |
| :--- | :--- |
| 源清单 | 24/24 有 `source_project` + `source_hash` + `source_type` |
| 时长 | ±0.1s |
| 中位镜长 | ±0.05s |
| B-roll | ±15pp 动态带内（应用层按实际素材自适应，见 §6.5；记录实际值与调整原因） |
| 关键帧密度 | ±2/min |
| SFX 密度 | ±3/min |
| luma | ±5 |
| warm | ±5 |
| sat | ±2pp |
| 字幕 Y | ±0.01 |
| 字幕底框 | 有无必须一致；胶囊/通条形状必须一致 |
| 顶栏 / 金句卡 / 抠像 / 巨字数 | 完全一致 |
| 转场数 | 与源一致（当前应为 0） |
| 花字条数 | ±1 |
| 专属音效包 | 24/24 |
| 字体 | 同源，或 `font_substitution` 已声明 |

---

## 8. 通往 100% 1:1 的资产接线方案（版权不设限，以精确资产为准）

用户已明确：不受素材版权约束，目标是不惜一切代价 100% 1:1。工程上正确做法不是「绕开版权」，而是**把「源真名 + 用户自备授权文件」变成一等公民**：

1. **精确字体接线**：源字体真名全部入 `source_manifest.json`（新青年体、FZCuJinLJW、喜鹊古字典体、造字工房俊雅体、汉仪天宇风行体、得意黑、知新体、DouyinSansBold、Aa 研宋、FZZJ-JHTJW…）。渲染器加载顺序：`fonts/<style>/` 用户放置的授权字体文件 → 严格按 manifest 真名匹配 → 找不到才回落到当前替代字体并在产物里打「非 1:1」标记。用户把机器上已有的同款字体拷进来即可字形 1:1，其余替代字体一栏如实声明。
2. **精确音频接线**：源 BGM/SFX 真名（IV、Push Up、Sunrise、回忆-刷一下、纸张抖动声…）入 manifest；`sound_vibe/<style>/` 接受用户自备音频文件按真名接线；找不到再走 CC0 替换 + 标记。SFX 上限从 48/min 解除，按源密度执行。
3. **内容素材**：原草稿里的人、店、房、产品不可复现——这是内容层，模板只复刻语法；1:1 指的是「同一套语法 + 用户自己的素材跑出同风格的成片」。
4. **剪映私有特效**：粒子、转场、文字动画、HSL、蒙版羽化的「同像素」复现只有一条真实路径：**导出原生剪映草稿**（同字体/同特效 ID/同素材），在剪映内打开。当前 ffmpeg/Pillow 渲染管线能到「同族同语法」；要像素级 1:1 就走草稿导出线，两者不冲突，前者负责快速出片，后者负责精仿。

所以「100% 1:1」拆成三句话：

- **模板语法 1:1**：24/24 可达，按 P0–P2 做完。
- **像素级 1:1**：用户按 manifest 自备精确字体/音频后可达；缺哪项就标哪项，不虚报。
- **可编辑工程 1:1**：FCPXML 做不到；需要原生剪映草稿导出（解密工具链已就位，反向写回加密草稿的能力也已验证存在——解密器同时证明了格式可逆）。

---

## 附录：产物

- 逐套机器可读矩阵（已更新为最终保存态口径）：`reports/style_parity_matrix.csv`
- 本报告：`reports/style_parity_review_2026-11-22.md`
- **解密器**（纯 Python，含 GCM tag 校验）：`forensic/jy_draft_decrypt.py`
- **全量 ground truth**（112 套，从解密主稿重建）：`forensic/jianying_ground_truth_full.json`；提取器 `forensic/jy_extract_ground_truth.py`
- 解密明文（454 个文件）：`forensic/decrypted/`
- 官方 dylib 探针（调用链逆向过程产物）：`forensic/jy_decrypt.py`；/tmp/jyroot/MacOS/runner*.c(pp)
- 取证原始数据：`<scratch_dir>/jianying_forensic_full.json`、`jianying_65_deep_techniques.json`
- 4 套替换候选证据：`<scratch_dir>/v31_4_replacement_candidates.jpg`
- 剪映本机安装（解密依据的官方库）：`/Applications/VideoFusion-macOS.app`（11.8.0），关键导出符号 `lvve::EncryptUtils::*`、密钥派生 `lvve::CryptoKeyVEAdaptor::queryKey`
