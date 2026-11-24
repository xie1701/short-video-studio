# 24 套风格 1:1 复刻 · 闭环执行记录（2026-11-23）

本文件记录审计落地的**每一条修复**、**怎么验的**、以及**还没到位的部分**。
所有数字都可由仓库内脚本重算，不依赖任何手工填写。

---

## 0. 当前闸门状态（三条命令，跑得过才算数）

```
$ /tmp/jyvenv/bin/python -m pytest tests/test_studio.py -q
53 passed

$ /tmp/jyvenv/bin/python dev_tools/verify_style_parity.py
self-check vs decrypted drafts: 24/24 clean
blueprint overlay: 96 checks
parity: 24/24 styles PASS  (528 field checks)

$ /tmp/jyvenv/bin/python dev_tools/render_style_fixtures.py
fixture KPI: 24/24 PASS -> reports/style_fixture_kpi.csv
```

---

## 1. 解密与 ground truth（可复现的前提）

- `forensic/jy_draft_decrypt.py`：剪映 6.x 草稿 AES-256-GCM 解密，key(32B)+IV(16B) 嵌在密文文本自身
  12 个固定偏移 `(0,7,20,33,40,47,59,66,76,89,99,127)` 各取 4 字符。
- 批量：113 个草稿 × 4 类文件 = **454/454 全部解开**，明文落 `forensic/decrypted/`。
- `forensic/jy_extract_ground_truth.py` → `forensic/jianying_ground_truth_full.json`（112 个项目）。
- 交叉验证：解密主稿 vs 第一轮旧口径，112 套核心指标 **0 处差异**；全库转场计数 **全为 0**。

## 2. 风格库 v4.0.0：单源构建 + 逐字段校验

- `dev_tools/build_style_library_v4.py`：唯一数据源是解密草稿本身，所有测量字段派生，无手写数字。
- `dev_tools/verify_style_parity.py`：**528 项字段校验**，且直读草稿自证 sha256/v1_clips/segments/转场/抠像。
- `dev_tools/gen_style_report.py`：`STYLE_SPECTRUM_REPORT.md` 全部由库生成（禁止手改）。
- 隐私：风格→草稿名映射移出仓库（`forensic/style_sources.json`），仓库内不出现任何本机项目名。

## 3. 转场：源 0 → 交付 0

24 套 `transition_style: none`、`transition_source_count: 0`、`transition_policy: hard_cut_only`；
`render_mp4.py` 加硬约束：`transition_source_count > 0` 才允许查 xfade 映射表。

## 4. A-roll/B-roll：从硬指标改成动态策略

- 库字段：`broll_prior_ratio` + `broll_target_band_pct`（±15pp）+ `broll_target_hard=false`。
- `step4_build_edl.py`：双向校正 —— A-roll 不够就上调插入画面，插入画面超目标就按语义优先级收回来；
  每次都写 `broll_policy_note` 说明收敛原因。
- 覆盖率口径拆分：`broll_ratio_actual` 只算真实插入画面，图卡单列 `code_card_ratio`。
- 运行时验收 13/24 → **24/24 PASS**（11 套低插入比风格此前被天然排期拉到 ~53%）。

## 5. 字幕：字数上限、底框、字体来源

- `max_chars_per_line` 逐套按源实测（7–96 字），去掉 `min(14, …)` 硬压。
- 底框：按源 `background_width/height` 实测，不存在通栏底条（`full_width_bar: false`）。
- 字体：`assets/style_spectrum/fonts/<style_id>/` 放源模版原版字体即优先命中，
  `05_subtitles.json` 记 `font_exact_match: true`；否则回落可商用替代并给出放置提示。

## 6. 花字版式与动效：10 套脱离通用兜底

- 新增 `overlay_motion.py`：6 类动效原语（弹幕滚动/镜像翻转/横向模糊/快速翻页/折叠展开/滑动出入）+ 2 个缩放子型，
  `ANIM_NAME_TO_PRIMITIVE` 覆盖库里**全部 54 个真实动效名**。
- `render_template_overlays.py`：10 个 family 各自身份（cream 极简宋体 / 白描边抠像 / 极净两行 /
  博主双色 / 上白下黑宽幕 / 访谈散落 / 暖金勾边 / 门店小签 / 微纪录宋体 / 不露脸斜体砸屏），
  巨字改用各自专属渐变立体色，长文本压到 ≤840px 居中对称。
- `template_blueprints.json`：24 套统一补 `anim_primitives`(3 条) + `flower_colors` + `giant_count`
  + `source_font` + `flower_font` + `letter_spacing_px`；校验器新增 **96 项**蓝图 vs 库一致性检查
  （转场必须 none、巨字数必须等于库、花字色不得越出源实测色、源字体不得为空）。

## 7. 调色：先修数据，再谈按源

**发现的问题**：库里 `shadow_rgb / midtone_rgb / highlight_rgb` 有 18 套是历史占位值
（暗部恒 32/34/38、中调等于均值、高光恒 225/224/220）。

**修法**：新增 `dev_tools/measure_color_zones.py`，直接解码草稿原始素材，按 V1 主轨**实际占用时长加权**
重算 24 套（口径与旧分析器一致：BT.709 亮度、<0.28/0.28–0.72/>0.72 分区、饱和与暖度同定义）。
量化差异示例：style_09 亮度均值 137.8→**115.5**、style_18 141.4→**99.0**（原来判成明亮，实为暗调）、
style_21 162.9→**140.1**、style_24 94.0→**103.6**。

**落地**：`step7_color_grade.py` 单镜链 = `eq → colorbalance(三段) → curves(三锚点) → lut3d(逐套强度) → 特效 → BT.709`；
`color_effects.json` 24 套逐套给出曲线锚点/三段分色/饱和缩放/暖度/7 类特效开关，`validate_color_effects()` 缺键即报错。

**能力边界（如实说）**：
1. 曲线是近似 —— 输入锚点假定通用实拍分布，绝对值不追满分位；
2. 局部模糊是整幅轻高斯（真·背景虚化由 pip 承担）；
3. 聚光是暗角式压暗，做不出「圆形挖孔 + 孔外纯黑」；
4. 颗粒是均匀时域噪声，无按亮度分层的胶片响应；
5. 轻微颤抖只有 ±2–3px 平移，无旋转/果冻。

## 8. 音频：24 套音效包 + 原曲接线

- `dev_tools/build_sound_packs.py`：24 套各一套，7 套源 BGM 保留，17 套按源音效类别合成同族拟音（83 个新 wav）。
- 原曲优先：`assets/sound_vibe/user/<style_id>/` 里文件名含源曲名即命中；音效按角色名
  `transition/accent/ui/receipt` 覆盖，命中角色记入 `user_original_sfx_roles`。
- SFX 上限从 48 次/分放宽到 72（style_15 实测 64.8）。

## 9. 工程交付：烘焙版与可编辑版彻底分开

| | 烘焙版 `export_jianying.py` | 原生草稿 `export_jianying_native.py` |
|---|---|---|
| 载体 | FCPXML + 透明 PNG 图层 | `draft_content.json` + 相对路径素材 |
| 文字 | 烧在图里，不能改 | `type: text` 真文字素材，可改字改样式 |
| 标记 | `export_kind: baked_interchange`、`text_editable: false` | `export_kind: jianying_native_draft`、`editable_text: true` |
| 自证 | 独立黑盒校验 | `dev_tools/verify_native_draft.py`：顶层键/素材表/三种轨/文字/视频素材**零缺失** + 时间线自洽 + 无悬空引用 + 未烘焙成图层 |

HyperFrames 样板也改成按单套风格生成（巨字 hook + 顶栏/画中画占位 + 按源字数上限切的字幕行 + 底框/描边/调色近似），
不再是「通用标题 + 一行字」。

## 10. 仍未闭环的三件事

1. **`style_01` ↔ `style_14` 的 v3.0 整体互换**：v4 库按互换后的名字保留（内容已对齐真草稿），
   但「名字要不要换回来」是产品决策，需要你定。
2. **花字配色目前是分支内常量**：10 个专属分支的颜色由源实测写成字面量，蓝图里的
   `flower_colors` 只作校验与文档；库若再变，需要同步改分支（已知技术债）。
3. **24 对盲审接触表**（P2-3）未做：需要把复刻渲染与原版画面并排出图供人工盲评，
   原版画面需先用剪映导出，这一步必须在本机剪映里跑。

## 11. 1:1 的三层可达性（重申）

- **模板语法 1:1**：24/24 已达成，且可机器验证（三条闸门）。
- **像素级 1:1**：取决于你有没有把源字体/原曲放进 `fonts/<style_id>/`、`sound_vibe/user/<style_id>/`；
  放进去即按原版渲染，代码已接线并测试。
- **可编辑工程 1:1**：原生草稿导出已做到 schema 同构（相对真草稿零缺失），
  但「剪映打开后是否 100% 无差异」必须本机打开一次确认——这一步我无法代做。

## 12. 第三轮深挖：调色曲线与画面特效（2026-11-23）

这一轮把「三条近似处理」里的两条真正做掉了，也顺手抓出两个此前没人提的问题。

### 12.1 三段曲线其实一直没在改画面（已修）

`curves` 滤镜的坐标单位是 0–1（写 0–255 直接报错），但生成出的曲线点在低端被压得极密，
实测「把中间调 0.5 抬到 0.8」只让画面均值从 127.6 动到 129.9——曲线在跑，画面几乎不动。

修法：
- 曲线点改成「源分位 → 目标分位」直连，不再用 `curve_weight` 反推（反推会在目标超出可达范围时把整条曲线顶到饱和）；
- 曲线内置黑位可抬（`black_lift`）：片源有死黑而目标暗部更亮时，只抬分位不够，必须抬黑位；
- 闭环校准从 3 轮改 5 轮，判据改为「**特效之前**的 p05/p50/p95」，因为源片实测分位本来就取自未加暗角/聚光的素材。

`dev_tools/verify_grade_calibration.py` 是这一层的验收器（两种口径）：

| 口径 | 含义 | 结果 |
|---|---|---|
| A 自校 | 每套源片自己的片段喂进该套调色链 | **24/24 PASS**，平均最大残差 **2.5 级**（0–255），最差 `style_04` 9.8（高光压缩） |
| B 中性迁移 | 同一份素材喂进 24 条链 | **24/24 PASS**，平均最大残差 **2.4 级**，最差 `style_20` 4.5 |

回归护栏（`tests/test_studio.py`）：曲线必须落在 0–1、必须真能抬 40 级以上亮度、校准残差必须 ≤12 级。

### 12.2 画面特效此前是「按名字猜的」（已改为源 effect 轨真值）

从解密草稿的 `materials.video_effects` + effect 轨 `target_timerange` 直读，真值是：

| 风格 | 源特效 | 时间窗 |
|---|---|---|
| `style_02` | 暗角 | 11.00s 起 5.17s |
| `style_04` | 怀旧边框 | 15.33s 起 4.17s |
| `style_08` | 暗角 / 不规则黑框 / 荧幕噪点 / 暗角 | 14.63s / 18.77s / 29.17s / 38.43s |
| `style_15` | 暗角 ×2 | 13.87s / 24.70s |
| `style_17` | 颤抖 | 4.50s 起 1.53s |
| `style_20` | 不规则黑框 / 聚光灯 | 0.03s / 5.70s |
| `style_21` | 模糊（整画面） | 1.67s 起 0.17s |

**24 套里只有这 7 套真的有画面特效**，其余 17 套一条都没有。而此前 `color_effects.json` 是按风格命名
「推测」填的（名字里有「方角」就补一个不规则黑框），等于给源里没有特效的风格也压了暗角/噪点。

修法：
- `dev_tools/build_effect_usage.py`：抽真值 → `forensic/effect_usage.json`，并按参数映射回写 `color_effects.json.effects`（源里没有的一律 `false`）；
- 特效带 `window`，`_effect_filters(..., clip_window=)` 只在源时间窗内生效；
- 聚光灯从 `vignette`（只能整圈渐变）换成 `geq` 径向遮罩：内圈系数恒 1、羽化带线性过渡，能真做出「中间一个亮圈」；
- 「模糊」确认是**整画面**特效（只有一个 `effects_adjust_blur` 参数），不是空间局部虚化，之前的描述是错的。

校验：`verify_style_parity.py` 新增 `effects vs source track: 34 checks`；
含特效后的终帧分布平均偏离从 21.3 级降到 11.8 级（少了那些凭空加的特效）。

### 12.3 闸门（本轮后）

```
60 passed
self-check vs decrypted drafts: 24/24 clean
blueprint overlay: 288 checks
effects vs source track: 34 checks
parity: 24/24 styles PASS  (528 field checks)
fixture KPI: 24/24 PASS
调色闭环：自校 24/24 PASS（平均 2.5 级）｜中性迁移 24/24 PASS（平均 2.4 级）
```

## 13. 第三轮第二批：多特点帧出图 + 画中画按真草稿重排

### 13.1 每套风格出 5 格特性图（人眼核对用）

`dev_tools/render_style_feature_sheet.py`：每行一套风格，5 格依次是
① 源素材原样 → ② 真草稿构图（主轨 scale/x/y + 叠加窗 + 抠像描边）→ ③ 库字幕（裁片放大到能读）
→ ④ 蓝图花字（裁片）→ ⑤ 源真值特效（按 effect 轨时间窗，源里没有就明写「源草稿无画面特效」）。

② 格完全按 `forensic/pip_usage.json` 摆位（中心 = W(1+x)/2、尺寸 = W·scale），并在标签里给出
与 ① 的逐像素差，避免「看起来差不多」这种口头判断。产出 4 张：
`reports/style_features_2026-11-23_A~D.jpg`（每张 6 套）。

出图过程修掉两个真问题：本机读不到某套素材时整行会变全黑（已加「草稿素材 → 同源帧 → 实测三段色渐变」
三级回退）；字幕/花字整幅缩到 396px 宽根本看不清（已改裁片 + 原尺寸附近宽度）。

### 13.2 画中画按真草稿全部重排（13 套 `none` 是错的）

真值：24 套**都有**叠加视频轨（1–9 条），19 套有人像/物体抠像；主轨也不都满屏——
`style_19` 推近 1.3、`style_21` 1.2、`style_23` 1.1、`style_08` 缩到 0.9、`style_09` 缩到 0.6 并偏到左下
（对应它「主画面左下小块 + 上方全宽图 + 右下三小格」的纪实版面）。旧库里 13 套写 `none`、11 套写手写版面名
（`person_behind_giant_text_pip` 之类），既漏掉了真实叠加，也把不相干的版面套到风格上。

改法：
- `dev_tools/extract_pip_truth.py` → `forensic/pip_usage.json`：逐轨读 `clip.transform.x/y`、`uniform_scale.scale`、
  `materials.videos.matting`（含 `strokes` 描边色与粗细）。
- `dev_tools/apply_pip_truth.py` → 库 `pip_composition_spec = {mode: geometry_pip_*, main:{x,y,scale}, windows:[...], cutout}`，
  蓝图 `pip_spec` 同步；`--check` 防漂移。
- 渲染端新增 `geometry_pip` 分支：单源按主轨几何推近/缩窗（缩窗时背景走自身模糊铺底），
  双源把副素材摆到真值窗口坐标并加描边；`step4` 里 depth/dual 两个范式也不再硬套 `blur_bg_center_card_pip`，
  一律用该风格真值几何。
- 抠像不再成为死代码：几何模式且 `cutout=true`、主轨满屏的镜头，仍走 AI 人像抠像合成（内部按满屏真人 + 副镜背景 + 描边实现，与源结构一致）。
- `render_profile.json` 新增 `pip_geometry`：`geometry_pip_single / geometry_pip_dual / windows_unrealized`，
  `windows_unrealized>0` 明写「该镜没拿到第二路素材，只落了主轨几何」，不悄悄吞掉。

落地验证（新增测试）：单源推近 1.3 后左上角红块区域像素差 > 8；双源窗口 0.4 居中后窗口内是副素材
（绿通道 > 190）、窗口外仍是主素材（绿通道 < 120）。两条都是真跑 ffmpeg 出帧量的，不是查参数。

### 13.3 闸门（第三轮后）

```
62 passed
self-check vs decrypted drafts: 24/24 clean
blueprint overlay: 288 checks
effects vs source track: 34 checks
parity: 24/24 styles PASS  (528 field checks)
fixture KPI: 24/24 PASS
调色闭环：自校 24/24（平均 2.5 级）｜中性迁移 24/24（平均 2.4 级）
pip 真值：24/24 与 forensic/pip_usage.json 一致
```

## 14. 第四轮：B-roll 与台词对不对得上 / 小窗里的人会不会被切掉

### 14.1 先量化「B-roll 跟文案对不上」，别争论

`dev_tools/audit_broll_semantics.py` 造一套真实手机素材（文件名 `IMG_4821.MOV`，标签走
`inventory_media._infer_semantic_tags` 的真实推断），配一段真实烧烤门店口播，跑一遍剪辑表，
然后逐镜问：**这个外来画面和它压着的那句话，到底有没有关系。**

结果（旧逻辑）：**0/5 = 0%**。旧口径（字面 tag 命中）也是 0/5。
根因在素材侧，不在打分权重：

```
IMG_4821.MOV      -> ['空镜特写']
VID_20240312_...  -> ['空镜特写']
DSC09931.MOV      -> ['DSC', '空镜特写']
```

手机素材名没有语义、画面没有文字、没有人手写说明时，`semantic_tags` 只剩「空镜特写」这类
**画面描述**；而台词里的词是「鲜羊排」「成本」「工序」这类**主题词**。两边字面永不相交，
`sem_hits` 恒为 0，选片退化成「谁更稳、谁更锐、谁用得少」。代码图卡的标签是从文案关键字生成的，
所以那类反而"永远对得上"——这就是用户实测看到的错位。

### 14.2 修法：概念桥 + 唯一能救的高精度入口

- 新增 `scripts/broll_semantics.py`：一套离线概念空间（场景/实体/动作/意图/人物 × 16 个行业域，
  约 300 个中文表面词）。两边都概念化后比对，输出 `score / verdict / basis`，
  **零依据必须 0 分**，`basis` 写成「「鲜羊排」(实体)↔「羊排」(实体)」这种人话，进剪辑表可审。
- `step4` 打分从 `sem_hits * 6.5` 换成 `relate().score * 10.0`；每个外来画面写
  `sem_match` + `media_signal`（用的是素材侧哪段文字），EDL 顶层写 `broll_semantic` 汇总；
  剪辑表 md 多一列「凭什么对得上」。`SVS_LEGACY_SEM_MATCH=1` 保留旧口径，只用于 A/B 对照。
- 新增素材说明入口：`inventory_media.load_media_notes()` 读 `<素材目录>/素材说明.txt`
  （一行一条 `文件名 一句画面内容`，也支持 `media_notes.json`）→ `description` + `manual_tags`
  → 随 `step1` 进 `01_manifest.json`。没写说明的素材，盘点报告会点名，并自动生成可填空的骨架。
- 同样的输入，补上说明后：**5/5 = 100% 命中**。所以"对不上"不是算法笨，是素材侧没有话可对；
  现在系统会对你说实话——没依据就是 `none`，QA 会把它计入，不再瞎配。

### 14.3 小窗里的人被切掉：改成以人脸为中心取景

旧逻辑：小窗/缩窗/圆窗/相框一律按画面正中（或固定偏上 24%）裁。

新增 `scripts/face_framing.py`：
- 片段内抽 3 帧 → Apple Vision 检测人脸（本地离线）→ 取面积最大的脸，跨帧取中位，
  得到归一化锚点 `{cx, cy, w, h}`；
- 裁切以这张脸为中心：横向正中、纵向落在窗口 44%（中景留 6% 头顶；脸越大越往正中收，
  大特写不会被"头顶余量"切掉）；
- 脸高超过窗口的 87% 时先把取景退远（宁可变远也不切脸）；贴边时钳位不越界；
- 检测不到人脸 → 返回 `None`，渲染端**照实退回**居中裁切，并在 `pip_layout.face_framing`
  里写 `no_face_center_crop`，不假装做过。

接线：`step4` 给小窗/缩窗/推近/相框/圆窗的镜头统一算锚点写进 EDL（`face_anchor` /
`secondary_face_anchor` / `face_framing`）；`render_mp4` 的 `geometry_pip` 缩窗、
`_cover_crop_vf` 全部模式、推近裁回画布、双源副素材窗口，都按锚点取景；
`step9` 封面/首帧候选也改走同一套（封面是最贵的一帧）。`render_profile.json` 新增
`face_framing.windows_face_centered / windows_no_face_detected`。

实测（14 条真草稿素材 × 4 种窗口 = 48 个窗口，检测跑在**输出帧**上）：

```
旧逻辑出问题（窗里找不到脸 / 脸贴边）9/48 = 18.8%
新逻辑：9/9 全部修复，剩余问题 0
对照图 reports/face_framing_proof.jpg（左=旧居中，右=人脸中心，人眼可核）
```

### 14.4 又一批「参数在、链上不生效」

| 参数 | 之前 | 现在 |
| :--- | :--- | :--- |
| `overlay_style.flower_per_min` | 蓝图里躺着，0 处读取，花字预算恒按 `ceil(dur/3.5)` | `step6.flower_budget_from_source()`：style_15 实测 88 条/分 → 60s 片 18 条变 88 条 |
| `overlay_style.giant_count` | 0 处读取，巨字恒按 2 条 | `step6.giant_count_from_source()`：style_01/08/15/21/24 → 0/2/10/4/3（并按片长设 sanity 上限） |
| `overlay_style.letter_spacing_px` | 0 处读取，14 个分支字距写死 | `render_template_overlays.letter_spacing()`：蓝图有值就用蓝图，缺省才用分支默认 |
| `subtitle_override.safe_y_center` | 疑问项 | 实际由 `step5` 读 `templates.json` 的 `safe_y_center`，不是死参数（此前 grep 名字写错） |
| `镜像翻转` 原语 | 被判为"没被任何风格用到" | 它挂在 `ANIM_NAME_TO_PRIMITIVE`，源动效名叫「镜像翻转」就会触发；当前 24 套没有该名字属正常 |

### 14.5 QA 新增两条自查（防止再出假实现）

- `broll_semantic_fit`：每个外来画面都必须写 `sem_match`；声称 `hit/weak` 却没有 `basis` → FAIL。
- `pip_face_framing`：会裁切的构图必须标 `face_center` 或 `no_face_center_crop`，未交代 → FAIL。

### 14.6 闸门（第四轮后）

```
74 passed
self-check vs decrypted drafts: 24/24 clean
blueprint overlay: 288 checks
effects vs source track: 34 checks
parity: 24/24 styles PASS  (528 field checks)
fixture KPI: 24/24 PASS
B-roll 语义审计（带素材说明）：5/5 = 100%（无说明时如实 0%，并标 none）
小窗人脸构图：48 个窗口，旧逻辑坏 9 个 → 修复后 0 个
```

