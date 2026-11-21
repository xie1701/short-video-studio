---
name: short-video-studio
description: 策划、剪辑和交付面向实体门店获客或个人账号增长的短视频。适用于从真实素材策划选题、编写口播和拍摄任务卡、剪辑已有实拍，或用代码制作图解与产品演示。以锁定音轨为剪辑骨架，支持成片质检、MP4 与可编辑工程导出，以及发布数据复盘。
---

# short-video-studio 短视频制片工作室

服务**不懂拍摄、不懂剪辑、不懂包装**的用户，把专业短视频编导方法论与工业级「先音后画」后期工程封装为一条龙智能工作室。

---

## 一、 首次对话铁律：先猜后确认判定轨道

首次进入对话时，**不问开放题、不说行话**，根据用户上下文先给出最合理的方向猜测让用户确认：

- **门店轨（Store Track）**：实体店老板 / 本地生活商家，核心目标同城获客。成功标准 = **评论区咨询、私信、到店核销**。
- **博主轨（Creator Track）**：个人创作者 / 知识或泛娱乐账号，追求更多新观众。成功标准 = **开头 3 秒留人、看完全片、涨粉**。

> 话术示范：“我看你这次主要是帮实体店拉本地客人、要评论私信和到店，对吗？如果不是，直接告诉我是做个人自媒体涨粉。”

---

## 二、 唯一选题与文案方法论（内部严格溯源，对外全程大白话）

- 完整编导知识总库：[references/建立可循环的内容系统.md](references/建立可循环的内容系统.md)（融合欧阳思维笔记 310 节点 + 2026 编导课 179 页完整生产线）
- 速查手册与大白话交付模板：[references/methodology-cheatsheet.md](references/methodology-cheatsheet.md)

**三条硬性约束**：
1. **【最高优先级】对用户全程说大白话，严禁蹦出课件或专业黑话**：内部思考与脚本编排严格按方法论选型（阶段配比、六大脚本类型、八大爆款元素、客户五层问题、漏斗/水桶串场、八大钩子、十大开篇技巧、中间六种结构、结尾一主一辅、17 种口播拍法），但**在面对用户对话、交付 4 周内容日历、口播稿、分镜表、拍摄卡与周复盘时，严禁直接输出「水桶流、漏斗流、5A、A1–A5、开篇钩子、人设、爆点前置、A-roll/B-roll、J-cut/L-cut、Room Tone、LUFS、完播率」等黑话或英文缩写**，必须全部按 [methodology-cheatsheet.md](references/methodology-cheatsheet.md) 第零节转译成老百姓听得懂的人话（用「拉新让更多人认识 / 晒真实细节亮老板底线 / 开头3秒留住人的第一句话 / 真人出镜镜头与特写补拍」等表达）。
2. **写稿前必过「事件一句话」自检**：动笔前先答「一句话能不能说清这条视频里发生了什么事？」。说不清具体事件（只剩形容词和口号）必须退回重想；写稿落实**「用看得见的画面代替抽象感受」**与**「把专业词换成老奶奶也听得懂的话」**。
3. **开篇先让观众看懂发生了什么**。口播默认前三秒两次切换。纪实片允许连续展示完整动作，图解默认一次切换。结合证据可读性和对应片型调整镜长。

---

## 三、 三入口工作流与「最多打断 3 次」交互契约

每生产一条视频，最多只允许在以下 **3 个节点** 打断用户确认；其余一切参数（BGM、停顿、J-cut、字幕模板、包装点位、LUT、响度）全部按默认值自动推进：

### 入口路由（优先一手原材，拒绝二手模板套话）
- **一手原始材料接入与剧本口语体检**（有同行评论/客服聊天/门店物证或口播初稿时先运行）：
  ```bash
  python3 scripts/raw_intel_ingest.py --comments-file <评论/客服导出.txt> --proof-dir <物证目录> --script-json <脚本.json> --track <store|creator> --output <项目目录>/pipeline/00_raw_intel.json
  ```
  源记录保留原文件指纹、定位及已知角色。只有明确核实客户导出来源后才加 `--verified-customer-export`。价格、数据、比较和实拍承诺用 `--sources-json` 关联原件，格式见 [pipeline-schemas.md](references/pipeline-schemas.md)。
- **入口 1（从实拍创作开始）**：完成账号主页四件套与用户定位（面向企业/个人消费/大众流量 + 6 维画像）→ 按三阶段配比生成大白话 4 周内容日历 → 逐条先过「事件一句话」自检再产出大白话口播稿、分镜与拍法建议（17 种口播拍法）。
- **入口 2（带实拍素材进来）**：运行深度素材盘点（HDR/10-bit 探测 + 拉普拉斯锐度/抖动锁 `best_in_sec` + Apple Vision 人脸/显著性感知），输出 `00_inventory.json` 与 `00_shot_log.json` 反推选题：
  ```bash
  python3 scripts/inventory_media.py --input-dir <素材目录> --output <项目目录>/pipeline/00_inventory.json --track <store|creator>
  ```
- **入口 3（纯代码制片 `Code2Video` `--mode code-video`，零实拍 / 科普图解 / 产品 UI 演示 / 风格化短片）**：
  - 编码前**必读总纲** [references/code-video-methodology.md](references/code-video-methodology.md) 及对应工艺手册：
    - 动态图形/品牌 Promo/UI 演示 → [references/craft-motion-promo.md](references/craft-motion-promo.md)
    - 商业逻辑/数学物理科普/数据图表 → [references/craft-explainer-data.md](references/craft-explainer-data.md)
    - 指定艺术流派/粒子仿真/无缝循环 → [references/craft-stylized-generative.md](references/craft-stylized-generative.md)
    - 配音语速/拟音/节拍同步 → [references/craft-sound-beat-sync.md](references/craft-sound-beat-sync.md)
  - 严格执行 **`Brief → 00_code_spec.json/SPEC.md/STRUCTURE.md → frame(t)确定性渲染 → Cold Read + 10_action_strips.jpg + VERIFY.md`** 闭环：
    ```bash
    python3 scripts/code_video_spec.py --init-spec --title "<标题>" --one-idea "<核心命题>" --out-dir <pipeline目录>
    python3 scripts/code_video_spec.py --validate-spec <pipeline目录>/00_code_spec.json
    python3 scripts/run_pipeline.py --account <账号> --workspace-root <工作区> --track <store|creator> --mode code-video --code-spec <pipeline目录>/00_code_spec.json --script-json <台词JSON(含say/show)> --style-id style_14
    ```

### 专属工作区零污染与多账号物理隔离铁律（MUST）
- **原素材严格只读**：所有剪辑加工、转码、临时帧和成片导出统一写入独立专属加工目录（默认 `~/.short-video-studio/workspaces/accounts/<account_slug>/projects/<project_id>/`，可用 `--workspace-root` 指定），**严禁向用户原素材 `--raw-dir` 写入任何文件**。若误将 `--project-dir` 配到 `--raw-dir` 内部，流水线自动重定向隔离并告警。
- **账号与轨道归档**：`--account` 定位账号的合规白名单与发布记录。流水线按 `--track` 读取对应的 `content-log/account_profile_<track>.json`。
- **完工自动清理 (`tidy_workspace`)**：适用机检完成且成片进入审片或归档后，复制本次产物至 `out/` 后，默认清理 `_render_final/`、`01_normalized/`、`08_voice_denoised.wav`、`08_premix_*.wav` 等高体积中间临时文件，同时保留锁轨音视频分轨与 `08_sfx_audit_window.json` 以支持 `tidy` 后二次复验 `#20` 及 `--mode reedit-visuals` / `--mode render-locked` 无损重剪（传入 `--deep-clean` 才全清，`--keep-temp` 可全留调试）。

### 广告法与平台合规前置 + 诚实基线
- **文本/转写双级审校 (`compliance_guard.py`)**：`must` 级违禁词（极限词/绝对承诺/功效/机关背书，精准排除「最近/最后/最怕/第一口」零误杀）必须改写或弃用；`warn` 级支持账号白名单 `ack`。**实拍真人口播命中 `must` 时严禁靠改字幕遮掩，必须整句弃用或重拍并留痕**（`00_compliance_report.json/.md`）。
- **诚实基线**：无真实评论/客服原材时显式声明「未经真实用户问题验证」；复盘样本 `< 3` 条时显式标注「样本不足暂不调整」。

### 3 次打断节点（Strict Interrupt Cap = 3，带指纹锁与打扰审计）
1. **🛑 打断 #1：确认选题**（或基于原材与盘点接触表 `00_contact_sheet.jpg` 拍板选题与脚本）。
2. **🛑 打断 #2：确认拍摄任务卡或锁定音轨盲听版 (`03_blind_audio.m4a`)**
   - 需实拍时用大白话身体尺子任务卡（含必录现场动作声、10 秒安静环境音、外接麦拍手、说错不停重说、拍完回看 3 条，并落实「口播/第一视角允许轻呼吸感、特写/物证镜头手肘贴稳防糊、不用美颜、说话情绪大于一切」拍摄铁律；支持 `--audio-events` 按配音时长 +2.0s 反定每镜时长与 `--industry catering|auto_repair`）：
     ```bash
     python3 scripts/generate_shot_card.py --output <项目目录>/shot_card.md --title "<选题>" --track <store|creator> --industry catering
     ```
   - 缺镜头时调用 7 种信息修辞同构补镜（内置阻尼弹簧微过冲、`--backend html|pillow|auto` 双引擎、内嵌视频先走 `extract_clip_sequence` 预抽 JPG 帧序列）：
     ```bash
     python3 scripts/generate_code_broll.py --output <补镜.mp4> --mode <kinetic_text|data_chart|flow_diagram|proportion|day_night|chips|versus> --register warm_food --backend auto --title "<标题>" --items-json '<JSON>'
     ```
3. **🛑 打断 #3：确认锁定音轨、低清粗剪预览与方案哈希锁 (`workflow_guard.py`)**
   - 跑至 Step 3 锁定音轨并出 `03_blind_audio.m4a` 与粗剪预览 `output_preview.mp4`：
     ```bash
     python3 scripts/run_pipeline.py --account <账号> --raw-dir <原素材目录> --track <store|creator> --mode lock-preview
     ```
   - 拍板可用 `workflow_guard.py lock --pipeline-dir <dir>` 上锁（改 EDL/字幕/包装一个字节锁即失效），并根据反馈走分支：`--mode render-locked`（锁定重渲染）、`--mode reedit-visuals`（只改画面音轨不动）或 `--mode relock-voice`（重锁音轨）。
   - 仅在确实向用户发起确认时调用 `workflow_guard.py record-stop`，用 `audit-stops` 核算。自动运行节点记录在 `pipeline_progress.json`。

---

## 四、先音后画与交付质检

所有中间文件契约见 [references/pipeline-schemas.md](references/pipeline-schemas.md)，安全区与预算规范见 [references/platform-safe-zones.md](references/platform-safe-zones.md)。

一键完整跑通流水线（盘点+接触表 → Step 1–9 → MP4 与按需工程 → 分目标验收 → 《我替你做的决定》+ 防 XSS 打点审片页 `review_player.html` → 归档 `out/` → 自动分级清理）：
```bash
python3 scripts/run_pipeline.py --account <账号> --raw-dir <原素材目录> --track <store|creator> --mode <full|code-video|...> [--style-id style_14] [--code-spec <00_code_spec.json>] [--code-html <index.html>] [--code-backend auto] [--visual-index <00_visual_index.json>] [--licenses-json <licenses.json>] [--hook-variants 2] [--dialect mandarin|cantonese|dialect] [--store-anchor <店名>]
```

各环节硬核机制（v5 全链路进化 + 影视反 NG 五原则 A1–A5 + 广电包装五原则 B6–B15）：
- **`inventory_media.py` & `step1_normalize.py`**：EXIF 方向校准 Vision 多时点采样；单遍 BT.709 亮度与等面积锐度/急甩惩罚 + Stereo PCM 动态范围体检；**2D FFT 相位相关稳度**；物理几何裁切与人脸眼部居中构图、HDR→Rec.709 Hable 映射及声道不平衡智能挽救。
- **`step2_transcribe.py` & `step3_lock_audio.py`**：合规与字数刷新前置于 TTS 合成前；TTS 采用 `edge-tts WordBoundary` 流式捕获 + `10ms RMS` 真实起音修正（消除 40–55ms 提前漂移）、句级哈希缓存、`say`/`show` 听看分离及 `_project_say_words_to_show` 跨通道发音→视觉词边界单调投射（`say_text` 同步持久化至 `03_audio_events.json` 供 `#21` 盲听 ASR 对齐）；切头去噪与修音后重算韵律停顿；分源文件隔离防混叠 `retake` 优胜；零漂移整小节循环 BGM、全局静默底噪抽样真 Room Tone、Equal-Power 渐变。
- **`step4_build_edl.py`**：**A1** `0.40s` Handles；**A2** `audio_only` 保声遮脸与首镜门控；**A3/A4** 指代词缺失告警；单向不回退 B-roll 游标、真 J-cut + 真 L-cut 声画错位、`1.15x` 锁眼微推拉。
- **`step5_subtitles.py` & `render_mp4.py` & `step6/7/8/9`**：保护跨词数字冒号/小数点、透传 `keywords`、动态重写合规 ASS 样式、7点避脸 `final_bbox` (`y<=1468`)；Peak-aligned SFX（零 `+18dB` 噪点注水）；中点测光与全部人脸镜 BT.709 皮肤保护；锁定一致 `-14.0 LUFS` 四端三轨 (`voice_master` / `bgm_ducked` / `sfx_master`)；`focus_blur` 毛玻璃背板 + 不动点眼部 Ken Burns；封面去套话、`09_ui_safe_preview.jpg` 遮罩验收与真 3 秒 `--hook-variants` (`09_hook_v*.mp4`)。

### 机检、缺项复核与终审 (`qa_inspector.py` + `independent_verifier.py`)
- 检查输出 `PASS / FAIL / SKIPPED_UNAVAILABLE / NOT_REQUESTED`。默认 `--exports auto`，工程故障单独报。`--exports mp4` 只做成片，明确选中的工程进入必检。OCR 等缺项列为人工实查。`approve-final --reviewed <ID> --note <实查结果>` 记录实际结果，剪映需写明导入版本和轨道检查结果。
  - **1–11 项流水线契约**（响度/黑帧/静音/音画同步/字幕与 `03_audio_events` 交叉校准/切点含 L-cut 对齐/安全区与零 `<=3` 残句及预算/非相邻零倒带与保声遮脸/前3秒切镜/A-Roll句界严锁/`common.LEAK_REGEX_PATTERNS` 防露馅）；
  - **12–22 项单遍解码黑盒裁判**（300–3400Hz 语谱画外音与 A 轨覆盖、OCR 安全区+字幕内容回环 Recall、人脸零遮挡与实测单框 WCAG `>=3.8:1`、BT.709 矩阵 `common.SKIN_YCBCR_RANGE` 肤色与 `max_luma_jump`、相对 FCPXML、HyperFrames 原生逐条剪辑与官方运行时三点抽帧、OCR 防露馅、屏蔽字幕静止检测、双带遮罩+相对 MAD 全量切点 Spot-Verify、统一 `compute_sfx_band_windows` 全量 cues 净抬升 + **5ms Spectral Flux 毫秒起音偏差校验**、`large-v3` 优先+粤语 `yue` 独立 ASR 盲听、严格只读广告法复核与 `is_verified_commercial_font` 清单）。
- **成片取证与终审**：`10_action_strips.jpg` 提供开篇及关键切点连拍。代码制片的 `VERIFY.md` 记录实际抽到的画面和 OCR 覆盖，事实台账、遮挡、阅读时间和闪烁留给人工复核。通用机检通过后状态为 `MACHINE_PASS_PENDING_HUMAN`。人工复核后运行 `workflow_guard.py approve-final --pipeline-dir <dir>`，签字要求质检报告中的方案与成片 SHA-256 均匹配当前版本。旧报告需重新质检。
- **片型与归档验证**：`--edit-profile auto|talking|documentary|explainer` 控制开头切换、镜长和静止容限。改画面时核对锁定人声和音乐指纹。`workflow_guard.py verify-out --out-dir <dir>` 校验归档文件指纹。
- **风格复刻洁净室 (`workflow_guard.py replication-diff`)**：pHash 距离 `≤6` 或 OCR 连续 12 字重合直接 `BLOCK`，明暗自适应采样 ΔE，`secrets.SystemRandom` 真随机洗牌并将答案键分离至 `blind_test_answer_key.json`。

---

## 五、发布复盘与参数实验

每条发布后收集后台数据（2 秒划走比例 `bounce_2s`、3 秒/5 秒留住多少人、复播比例 `rewatch_rate`、本地观众占比 `local_traffic_pct`、有效咨询率 `inquiry_rate`、掉人最狠的秒数 `drop_off_sec`、脚本类型 `script_type`、吸引角度 `viral_element`、关联 `edl_path`），写入按 `--account` 与 `--track` 双重隔离的 `content-log`：
```bash
python3 scripts/content_log_review.py --account <账号> --track <store|creator> --add-json '<数据JSON>'
```
按平台、视频 ID、观察窗口去重，保留指标分子分母。每周报告按同条件汇总，将留存掉点落回剪辑表，供人工提出假设。使用 `--register-plan` 提前登记规则，`--replay-plan` 回放历史，`--activate-candidate` 显式启用达标且可复验的候选。启用后在制作命令指定相应 `--platform`。旧资料按原轨道保留，演示使用单独目录。[实验输入与证据字段](references/pipeline-schemas.md)

---

## 六、 资产授权与环境检查

- 所有随仓字体、CC0 BGM、物理 SFX 与 LUT 的商用授权清单见 [references/assets-attribution.md](references/assets-attribution.md)。
- 首次使用或排查依赖/字体/无头 HTML 渲染桥（复用本机 Chrome）时运行：`python3 scripts/check_and_setup.py --init-fonts --init-assets --install-deps --init-browser`。

---

## 七、 「风格谱 (Style Spectrum)」参考包装复刻库

已内置基于 20 条标杆视频 + 剪映实操标杆《11月14日延期》提炼的 **6 大包装流派母版** 与 **20 套「风格谱 (`style_01` ~ `style_20`)」**（其中 **`style_14`** 为默认门店旗舰风格谱：无底框 62px 居中粗黑体 + 1.133x 暖金 `#FFE28A` 关键词高亮 + `x=540px` 绝对正中对称带 `[220, 860]` + 无底框高冲击花字 + 53% B-roll 现场动作声轻混 + 四区爆款封面；详尽拆解见 [STYLE_SPECTRUM_REPORT.md](assets/style_spectrum/STYLE_SPECTRUM_REPORT.md)，主数据库见 [style_spectrum_library.json](assets/style_spectrum/style_spectrum_library.json)）：

| 流派 ID | 流派名称 | 代表风格 ID | 典型场景 |
| :--- | :--- | :--- | :--- |
| `arch_01_tech_ui_console` | 暗黑科技 / AI 工作流控制台风 | `style_01`, `09`, `10`, `15`, `17` | AI 工具、SaaS 演示、Agent 工作流教程 |
| `arch_02_high_contrast_story` | 强反差财经 / 深度叙事拼贴风 | `style_03`, `16`, `19` | 商业案例、人物访谈、高能快切思维干货 |
| `arch_03_editorial_matte_depth` | 高级感杂志 / 人物前景遮罩风 | `style_11`, `12`, `18`, `20` | 审美评论、白底拆解、白板教学 |
| `arch_04_punch_talking_head` | 高密口播卡点 / 干货大字冲击风 | `style_02`, `04`, `07`, `14` | 创始人认知口播、实体商家 IP、痛点科普 |
| `arch_05_cinema_slow_space` | 电影感空间漫游 / 人文宋体金句风 | `style_06`, `08`, `13` | 建筑/展厅探店、人文心理学金句、实景访谈 |
| `arch_06_paper_stopmotion` | 纸张拼贴定格 / 工程图解科普风 | `style_05` | 收纳改造、尺寸科普、手工拼贴演示 |

常用指令：
```bash
# 1. 列出全库 6 大母版与 20 套风格谱摘要
python3 scripts/apply_style_spectrum.py --list

# 2. 在流水线中直接套用任一风格谱（自动绑定字幕模板 + 3D LUT + J-cut）
python3 scripts/run_pipeline.py --account <账号> --raw-dir <素材目录> --track creator --mode full --style-id style_02

# 3. 绑定风格谱至已有流水线目录，或导出零外链 HyperFrames 样板 HTML
python3 scripts/apply_style_spectrum.py --style-id style_16 --apply-pipeline <项目目录>/pipeline
python3 scripts/apply_style_spectrum.py --style-id style_16 --export-hyperframes <out.html>

# 4. 针对新的参考视频目录重新提取「风格谱」并更新资产库
python3 scripts/analyze_style_spectrum.py --input-dir <参考视频目录>
python3 scripts/build_style_spectrum_assets.py
```
