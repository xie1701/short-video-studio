---
name: short-video-studio
description: 面向零拍摄剪辑基础用户的全流程短视频生产智能体技能（含实拍双轨 + 纯代码制片 Code2Video 三入口）。支持多账号物理隔离 (--account) 与专属工作区零污染加工（严禁改动原素材目录，完工自动清理过程垃圾文件）、门店/博主双轨与《建立可循环的内容系统》唯一选题方法论、一手原材与诚实基线、广告法/平台合规双级前置拦截（含实拍原声整句弃用与 SHA-256 商用授权清单）、影视反 NG 五大硬守卫（2D FFT 相位相关二阶加速度稳度、声画分离 audio_only 保声遮脸、Handles 0.40s 掐头去尾、视觉指代词与禁忌反讽守卫、真静音重计时+提词半截词剔除）、纯代码制片方法论 (`frame(t)->image` 确定性渲染/阻尼弹簧/预抽帧/`say`/`show` 听看分离/SPEC与像素证据)、广电级包装、风格复刻洁净室与 22 项自动+独立黑盒质检及双态终审、MP4/自包含剪映/HyperFrames 三端出片与离线打点/动作连拍审片闭环。
---

# short-video-studio 短视频制片工作室

服务**不懂拍摄、不懂剪辑、不懂包装**的用户，把专业短视频编导方法论与工业级「先音后画」后期工程封装为一条龙智能工作室。

---

## 一、 首次对话铁律：先猜后确认判定轨道

首次进入对话时，**不问开放题**，根据用户上下文先给出最合理的轨道猜测让用户确认：

- **门店轨（Store Track）**：实体店老板 / 本地生活商家，核心目标同城获客。成功标准 = **评论区咨询、私信、到店核销**。
- **博主轨（Creator Track）**：个人创作者 / 知识或泛娱乐 IP，追求泛流量。成功标准 = **3 秒留存率、完播率、净涨粉**。

> 话术示范：“我判断你这次做的是【门店轨】，核心要同城咨询和到店，对吗？如果不是，直接告诉我是个人【博主轨】。”

---

## 二、 唯一选题与文案方法论（强制溯源标注）

- 唯一方法论原文：[references/建立可循环的内容系统.md](references/建立可循环的内容系统.md)
- 公式速查与强制标注模板：[references/methodology-cheatsheet.md](references/methodology-cheatsheet.md)

**硬性约束**：
1. 任何选题规划、4 周内容日历、口播文案或分镜脚本，**必须显式标注**：5A 层级、所属阶段配比、客户五层问题锚点、主公式（漏斗流 / 水桶流）、六大话题方向、八大开篇钩子切入点、十大开篇技巧、人格化六大切入点、中间结构与四大结尾类型。
2. 开篇前 3 秒必须落实：**第一眼画面信息密度大于第一句话** + 开头三句有噱头的话对应至少 2 次以上场景切换。

---

## 三、 三入口工作流与「最多打断 3 次」交互契约

每生产一条视频，最多只允许在以下 **3 个节点** 打断用户确认；其余一切参数（BGM、停顿、J-cut、字幕模板、包装点位、LUT、响度）全部按默认值自动推进：

### 入口路由（优先一手原材，拒绝二手模板套话）
- **一手原始材料接入与剧本口语体检**（有同行评论/客服聊天/门店物证或口播初稿时先运行）：
  ```bash
  python3 scripts/raw_intel_ingest.py --comments-file <评论/客服导出.txt> --proof-dir <物证目录> --script-json <脚本.json> --track <store|creator> --output <项目目录>/pipeline/00_raw_intel.json
  ```
- **入口 1（从实拍创作开始）**：完成账号搭建与用户定位（To B / To C / 曝光 + 6 维社会特征）→ 按 5A 阶段配比生成 4 周内容日历 → 逐条产出带公式标注的口语化文案与分镜。
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
- **多账号隔离 (`--account`)**：按账号隔离 `account_profile.json`、`compliance_whitelist.json` 与 `content-log/records.jsonl`，跨店代运营互不串味。
- **完工自动清理 (`tidy_workspace`)**：正式成片通过 22 项质检并同步交付物至 `out/` 后，默认清理 `_render_final/`、`01_normalized/`、`08_voice_denoised.wav`、`08_premix_*.wav` 等高体积中间临时文件，同时保留锁轨音视频分轨与 `08_sfx_audit_window.json` 以支持 `tidy` 后二次复验 `#20` 及 `--mode reedit-visuals` / `--mode render-locked` 无损重剪（传入 `--deep-clean` 才全清，`--keep-temp` 可全留调试）。

### 广告法与平台合规前置 + 诚实基线
- **文本/转写双级审校 (`compliance_guard.py`)**：`must` 级违禁词（极限词/绝对承诺/功效/机关背书，精准排除「最近/最后/最怕/第一口」零误杀）必须改写或弃用；`warn` 级支持账号白名单 `ack`。**实拍真人口播命中 `must` 时严禁靠改字幕遮掩，必须整句弃用或重拍并留痕**（`00_compliance_report.json/.md`）。
- **诚实基线**：无真实评论/客服原材时显式声明「未经真实用户问题验证」；复盘样本 `< 3` 条时显式标注「样本不足暂不调整」。

### 3 次打断节点（Strict Interrupt Cap = 3，带指纹锁与打扰审计）
1. **🛑 打断 #1：确认选题**（或基于原材与盘点接触表 `00_contact_sheet.jpg` 拍板选题与脚本）。
2. **🛑 打断 #2：确认拍摄任务卡或锁定音轨盲听版 (`03_blind_audio.m4a`)**
   - 需实拍时用身体尺子任务卡（含必录同期声、10 秒环境底噪、外接麦拍手、说错不停重说、拍完回看 3 条；支持 `--audio-events` 按配音时长 +2.5s 反定每镜时长与 `--industry catering|auto_repair`）：
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
   - 用 `workflow_guard.py record-stop` / `audit-stops` 确保全流程打扰严格 `<= 3` 次。

---

## 四、 「先音后画，以音定画」v5 剪辑流水线与 22 项 Fail-Closed 质检门禁

所有中间文件契约见 [references/pipeline-schemas.md](references/pipeline-schemas.md)，安全区与预算规范见 [references/platform-safe-zones.md](references/platform-safe-zones.md)。

一键完整跑通流水线（盘点+接触表 → Step 1–9 → MP4/自包含剪映/可播放离线 HyperFrames → 22 项 Fail-Closed 验收 → 《我替你做的决定》+ 防 XSS 打点审片页 `review_player.html` → 归档 `out/` → 自动分级清理）：
```bash
python3 scripts/run_pipeline.py --account <账号> --raw-dir <原素材目录> --track <store|creator> --mode <full|code-video|...> [--style-id style_14] [--code-spec <00_code_spec.json>] [--code-html <index.html>] [--code-backend auto] [--visual-index <00_visual_index.json>] [--licenses-json <licenses.json>] [--hook-variants 2] [--dialect mandarin|cantonese|dialect] [--store-anchor <店名>]
```

各环节硬核机制（v5 全链路进化 + 影视反 NG 五原则 A1–A5 + 广电包装五原则 B6–B15）：
- **`inventory_media.py` & `step1_normalize.py`**：EXIF 方向校准 Vision 多时点采样；单遍 BT.709 亮度与等面积锐度/急甩惩罚 + Stereo PCM 动态范围体检；**2D FFT 相位相关稳度**；物理几何裁切与人脸眼部居中构图、HDR→Rec.709 Hable 映射及声道不平衡智能挽救。
- **`step2_transcribe.py` & `step3_lock_audio.py`**：合规与字数刷新前置于 TTS 合成前；TTS 采用 `edge-tts WordBoundary` 流式捕获 + `10ms RMS` 真实起音修正（消除 40–55ms 提前漂移）、句级哈希缓存、`say`/`show` 听看分离及 `_project_say_words_to_show` 跨通道发音→视觉词边界单调投射（`say_text` 同步持久化至 `03_audio_events.json` 供 `#21` 盲听 ASR 对齐）；切头去噪与修音后重算韵律停顿；分源文件隔离防混叠 `retake` 优胜；零漂移整小节循环 BGM、全局静默底噪抽样真 Room Tone、Equal-Power 渐变。
- **`step4_build_edl.py`**：**A1** `0.40s` Handles；**A2** `audio_only` 保声遮脸与首镜门控；**A3/A4** 指代词缺失告警；单向不回退 B-roll 游标、真 J-cut + 真 L-cut 声画错位、`1.15x` 锁眼微推拉。
- **`step5_subtitles.py` & `render_mp4.py` & `step6/7/8/9`**：保护跨词数字冒号/小数点、透传 `keywords`、动态重写合规 ASS 样式、7点避脸 `final_bbox` (`y<=1468`)；Peak-aligned SFX（零 `+18dB` 噪点注水）；中点测光与全部人脸镜 BT.709 皮肤保护；锁定一致 `-14.0 LUFS` 四端三轨 (`voice_master` / `bgm_ducked` / `sfx_master`)；`focus_blur` 毛玻璃背板 + 不动点眼部 Ken Burns；封面去套话、`09_ui_safe_preview.jpg` 遮罩验收与真 3 秒 `--hook-variants` (`09_hook_v*.mp4`)。

### 22 项 Fail-Closed 门禁与双态终审 (`qa_inspector.py` + `independent_verifier.py`)
- 全部 22 项统一输出 **`PASS | FAIL | SKIPPED_UNAVAILABLE`**（缺依赖/缺草稿绝不伪装通过，`PASS` 方计入 `passed=True`）：
  - **1–11 项流水线契约**（响度/黑帧/静音/音画同步/字幕与 `03_audio_events` 交叉校准/切点含 L-cut 对齐/安全区与零 `<=3` 残句及预算/非相邻零倒带与保声遮脸/前3秒切镜/A-Roll句界严锁/`common.LEAK_REGEX_PATTERNS` 防露馅）；
  - **12–22 项单遍解码黑盒裁判**（300–3400Hz 语谱画外音与 A 轨覆盖、OCR 安全区+字幕内容回环 Recall、人脸零遮挡与实测单框 WCAG `>=3.8:1`、BT.709 矩阵 `common.SKIN_YCBCR_RANGE` 肤色与 `max_luma_jump`、URL 编码相对 FCPXML 与 `<template>` 可播放离线 HyperFrames 3 帧真实浏览器烟测、OCR 防露馅、屏蔽字幕静止检测、双带遮罩+相对 MAD 全量切点 Spot-Verify、统一 `compute_sfx_band_windows` 全量 cues 净抬升 + **5ms Spectral Flux 毫秒起音偏差校验**、`large-v3` 优先+粤语 `yue` 独立 ASR 盲听、严格只读广告法复核与 `is_verified_commercial_font` 清单）。
- **双态终审 + Cold Read + 自动化像素取证 (`10_code_verify_evidence.json` & `VERIFY.md`)**：出片自动生成前 3 秒（0.2s 步长）与关键切点（0.15s 步长）动作连拍条 `out/10_action_strips.jpg`；入口 3 纯代码模式下采用 `--batch-json` 单 Chrome 实例批量渲染 SVG/Canvas/2.5D Camera 场景（`tidy` 后亦支持 `--mode reedit-visuals` 自动重渲代码分幕自愈重建），并由 `code_video_spec.py` 自动抽帧实测主视觉高度占比（`>=33%`）与 Apple Vision OCR 事实台账对账填入 `VERIFY.md`；必检全过输出 `MACHINE_PASS_PENDING_HUMAN`；人工复核后运行 `workflow_guard.py approve-final --pipeline-dir <dir>` 校验 `plan_sha256`（含 `00_code_spec.json`）+ `output_final_sha256` 升级签字为 `APPROVED` 并同步更新 `out/10_qa_report.*`。
- **风格复刻洁净室 (`workflow_guard.py replication-diff`)**：pHash 距离 `≤6` 或 OCR 连续 12 字重合直接 `BLOCK`，明暗自适应采样 ΔE，`secrets.SystemRandom` 真随机洗牌并将答案键分离至 `blind_test_answer_key.json`。

---

## 五、 平台微观留存诊断与参数基因真闭环

每条发布后收集微观留存与转化数据（2 秒跳出率 `bounce_2s`、3 秒/5 秒留存、复播率 `rewatch_rate`、同城占比 `local_traffic_pct`、有效询单率 `inquiry_rate`、`drop_off_sec` 留存断崖秒数、关联 `edl_path`），写入按 `--account` 与 `--track` 双重隔离的 `content-log`：
```bash
python3 scripts/content_log_review.py --account <账号> --track <store|creator> --add-json '<数据JSON>'
```
输出 `weekly_review.md` 与 `account_profile.json`：将后台掉粉断崖秒数反查回 `04_edl.json` 具体镜头开出诊断处方，并把跑赢基线的镜长/动态停顿（含 `persona_pause_sec`）/首选风格谱自动写回 `account_profile.json` 驱动下一条视频进化。

---

## 六、 资产授权与环境检查

- 所有随仓字体、CC0 BGM、物理 SFX 与 LUT 的商用授权清单见 [references/assets-attribution.md](references/assets-attribution.md)。
- 首次使用或排查依赖/字体/无头 HTML 渲染桥（复用本机 Chrome）时运行：`python3 scripts/check_and_setup.py --init-fonts --init-assets --install-deps --init-browser`。

---

## 七、 「风格谱 (Style Spectrum)」参考包装复刻库

已内置基于 20 条标杆视频（103,981 帧）逐帧提炼的 **6 大包装流派母版** 与 **20 套「风格谱 (`style_01` ~ `style_20`)」**（详尽拆解见 [STYLE_SPECTRUM_REPORT.md](assets/style_spectrum/STYLE_SPECTRUM_REPORT.md)，主数据库见 [style_spectrum_library.json](assets/style_spectrum/style_spectrum_library.json)）：

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
