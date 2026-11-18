---
name: short-video-studio
description: 面向零拍摄剪辑基础用户的全流程短视频生产智能体技能。支持实体店老板「门店轨」（同城获客/咨询/私信/到店）与个人创作者「博主轨」（3秒留存/完播/涨粉），严格以《建立可循环的内容系统》为唯一选题与文案方法论，支持从零创作（定位+4周5A日历+文案分镜+带简笔示意拍摄卡）与带素材进来（盘点+反推选题）双入口，执行「先音后画，以音定画」9步确定性剪辑流水线（含代码生成补镜、J-cut、前3秒多切点、CC0 真实声学音效、-14 LUFS 混音）、11项强制自动质检+声画多模态终验、MP4与剪映草稿双出片、以及 content-log 周复盘闭环；内置基于 20 条标杆视频逐帧提炼的 6 大包装流派与 20 套「风格谱 (Style Spectrum)」复刻库（支持 --style-id 一键套用及导出至 Remotion / HyperFrames / 剪映专业版）。当用户要规划短视频账号、生成内容日历/口播脚本、做拍摄任务卡、盘点或剪辑口播/门店短视频、做音频驱动自动粗剪/精剪、套用或提取参考视频包装风格谱、或做每周短视频数据复盘时触发。
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

## 三、 双入口工作流与「最多打断 3 次」交互契约

每生产一条视频，最多只允许在以下 **3 个节点** 打断用户确认；其余一切参数（BGM、停顿、J-cut、字幕模板、包装点位、LUT、响度）全部按默认值自动推进：

### 入口路由
- **入口 1（从创作开始）**：完成账号搭建与用户定位（To B / To C / 曝光 + 6 维社会特征）→ 按 5A 阶段配比生成 4 周内容日历 → 逐条产出带公式标注的文案与分镜。
- **入口 2（带素材进来）**：运行素材盘点脚本，反推最适合当前素材的选题结构与公式，直接进入确认。
  ```bash
  python3 scripts/inventory_media.py --input-dir <素材目录> --output <项目目录>/pipeline/00_inventory.json --track <store|creator>
  ```

### 3 次打断节点（Strict Interrupt Cap = 3）
1. **🛑 打断 #1：确认选题**（或 4 周日历中选定本条要拍的选题方向与核心钩子，先给推荐项让用户确认）。
2. **🛑 打断 #2：确认拍摄任务卡或素材**
   - 需实拍时生成带 SVG 简笔构图示意的手机拍摄任务卡：
     ```bash
     python3 scripts/generate_shot_card.py --output <项目目录>/shot_card.md --title "<选题>" --track <store|creator>
     ```
   - 缺镜头时调用代码生成叙事补镜（文字动画 / 数据图 / 流程示意）：
     ```bash
     python3 scripts/generate_code_broll.py --output <补镜.mp4> --mode <kinetic_text|data_chart|flow_diagram> --title "<标题>" --items-json '<JSON>'
     ```
3. **🛑 打断 #3：确认锁定音轨和低清粗剪预览**
   - 先跑至 Step 3 锁定音轨并输出低清粗剪预览 `output_preview.mp4`：
     ```bash
     python3 scripts/run_pipeline.py --project-dir <项目目录> --track <store|creator> --mode lock-preview
     ```
   - 根据用户反馈严格走对应分支：
     - **用户只改画面** → 音轨文件 (`03_*`) 原封不动，执行：
       ```bash
       python3 scripts/run_pipeline.py --project-dir <项目目录> --track <store|creator> --mode reedit-visuals
       ```
     - **用户要改口播词** → 回到 Step 2/3 重新锁定音轨，再按新音轨自动重排画面：
       ```bash
       python3 scripts/run_pipeline.py --project-dir <项目目录> --track <store|creator> --mode relock-voice --script-json <新台词.json>
       ```

---

## 四、 「先音后画，以音定画」9 步流水线与自动质检

所有中间文件数据结构见 [references/pipeline-schemas.md](references/pipeline-schemas.md)，安全区与预算规范见 [references/platform-safe-zones.md](references/platform-safe-zones.md)。

一键完整跑通流水线（Step 1–9 + 渲染 MP4 + 剪映草稿 + 11 项强制质检 + 生成《我替你做的决定》；支持 `--style-id` 一键挂载风格谱）：
```bash
python3 scripts/run_pipeline.py --project-dir <项目目录> --track <store|creator> --mode full [--style-id style_02] [--benchmark <对标视频.mp4>]
```

各单步脚本均支持独立重跑：
1. `step1_normalize.py`：手机 VFR 转恒定 30fps CFR (1080×1920) 并保留 48kHz 原始 WAV。
2. `step2_transcribe.py`：提取逐字时间戳并过滤口头禅与摄影师/导演画外场记口令（无口播则自动走 TTS 配音生成）。
3. `step3_lock_audio.py`：按字去废话/气口/口误与画外杂音，句间停顿统一 `0.15–0.30s`；BGM 节拍检测并将钩子首字对齐强拍；标注全部音频事件。**此后绝不再改声音。**
4. `step4_build_edl.py`：以 `03_audio_events.json` 为唯一骨架出剪辑表（JSON + 可读表格）；切点全落音频事件；同期声音画强绑定（**台词明示展示实物/效果图时默认保护原生 `a_roll`，不被泛用 B-roll 覆盖**），盖 B-roll 只换画面；句间默认 2–4 帧 J-cut；前 3 秒 ≥ 2 次画面变化；画面不足自动补 B-roll / 代码补镜 / 定格。
5. `step5_subtitles.py`：直读逐字时间戳，关键词高亮，支持 4 套基础模板或 20 套「风格谱」模板 (`style_01` ~ `style_20`)。
6. `step6_packaging.py`：挂载 `vibe_sound` 多流派真实 CC0 物理音效先行落轨，花字贴纸 ±1 帧对齐重音/音效，仅置于情绪/信息点并执行预算上限。
7. `step7_color_grade.py`：基础白平衡与曝光校色后套用内置或风格谱 3D `.cube` LUT（自动识别 `assets/luts/` 与 `assets/style_spectrum/luts/`）。
8. `step8_mix_audio.py`：人声高通降噪 + 按流派差异化 BGM（CC0 `sound_vibe`）与自动闪避 + 统一响度到 **-14.0 LUFS**。
9. `step9_title_cover.py`：生成高点击标题与高信息密度封面帧 `09_cover.jpg`（严禁出现任何“STYLE/风格/Notion/模板”等内部开发字眼）。

### 出片与自动质检门槛
- **出片**：默认本地自动渲染 `output_final.mp4`（分屏画布支持 `frame_canvas.crop_y_frac` 精细控制人像/道具裁剪高度），并调用 `export_jianying.py` 独立导出兼容剪映 6.0~11.7+ 原生导入协议的全风格工程包（`<project>.fcpxml` 多轨工程直接内嵌主分镜、上层 `alpha_layers/*.mov` 透明画框/卡片/花字与双色安全区字幕轨、以及三轨独立 WAV；另附 `<project>.srt`/`.ass` 文字替换备选、`overlays/` PNG 与 `.cube` LUT，导出失败仍自动降级仅出 MP4 并明确提示）。
- **11 项自动质检 (`qa_inspector.py`)**：必须全部通过才可交付（1 综合响度、2 黑帧、3 >0.8s 静音、4 音画同步 ≤1 帧、5 字幕偏差、6 切点对齐音频事件、7 UI 遮挡区/代码卡互斥、8 镜头分布/0 倒带/A-roll 占比、9 前 3 秒 ≥2 切、10 A-Roll 句界严锁与口型同步、11 零开发词与风格名称露馅审计）；提供对标视频时必出逐秒对比表 `10_benchmark_comparison.md`。
- **多模态双审终验铁律（机器盲区必查）**：跑完 `11/11` 自动 QA 后，仍须执行两项实测抽检：
  1. **人声纯度抽听**：用多模态能力直读多切片跨 Cut 边界（±0.5s 拼接缝）音轨，确认 **100% 仅剩主讲人原声**；严禁气口吸附越过外部低谷把摄影师/导演画外提词（「OK」「对」「中间」「再讲一次」等）或句首残响吸入成片。
  2. **关键切面抽帧实看**：抽取各语义 B-roll 与实物展示段画面网格核查——说“看效果图/实物”时须有手拿实物画面；B-roll 需优选与口播同日同服装连戏镜头，严禁高亮核心话语配脏乱地面/电线俯拍、人物出画或与情绪背离的尴尬摆拍，尽量避免同一 B-roll 片段全片反复播；分屏画幅按需调整 `crop_y_frac` 防止裁切道具或人脸。
- **交付清单**：交付成片时必须同步呈现 `decisions_made.md`（《我替你做的决定》），让用户一句话即可微调任意默认项。

---

## 五、 发布后数据回路与周复盘

每条发布后引导用户截图或口述后台数据（播放、3 秒留存、完播率、点赞、评论、私信/咨询数），写入本地 `content-log` 并每周输出一页复盘：
```bash
python3 scripts/content_log_review.py --log-dir <项目目录>/content-log --add-json '<数据JSON>'
```
输出 `weekly_review.md`：明确指出哪些钩子和节奏跑赢账号基线，并给出下周日历的具体调整方案与数据依据。

---

## 六、 资产授权与环境检查

- 所有内置字体、CC0 BGM、音效、SVG 贴纸与 LUT 的商用授权清单见 [references/assets-attribution.md](references/assets-attribution.md)。
- 首次使用或排查依赖缺失时运行：`python3 scripts/check_and_setup.py --init-assets --install-deps`。

---

## 七、 「风格谱 (Style Spectrum)」参考包装复刻库

已内置基于 20 条标杆视频（103,981 帧）逐帧提炼的 **6 大包装流派母版** 与 **20 套「风格谱 (`style_01` ~ `style_20`)」**（详尽拆解见 [STYLE_SPECTRUM_REPORT.md](assets/style_spectrum/STYLE_SPECTRUM_REPORT.md)，主数据库见 [style_spectrum_library.json](assets/style_spectrum/style_spectrum_library.json)）：

| 流派 ID | 流派名称 | 代表风格 ID | 典型场景 |
| :--- | :--- | :--- | :--- |
| `arch_01_tech_ui_console` | 暗黑科技 / AI 工作流控制台风 | `style_01`, `09`, `10`, `15`, `17` | AI 工具、SaaS 演示、Agent 工作流教程 |
| `arch_02_high_contrast_story` | 强反差财经 / 深度叙事拼贴风 | `style_03`, `16`, `19` | 商业案例、人物访谈、高能快切思维干货 |
| `arch_03_editorial_matte_depth` | 高级感杂志 / 人物前景遮罩风 | `style_11`, `12`, `18`, `20` | 审美评论、Notion 白底拆解、白板教学 |
| `arch_04_punch_talking_head` | 高密口播卡点 / 干货大字冲击风 | `style_02`, `04`, `07`, `14` | 创始人认知口播、实体商家 IP、痛点科普 |
| `arch_05_cinema_slow_space` | 电影感空间漫游 / 人文宋体金句风 | `style_06`, `08`, `13` | 建筑/展厅探店、人文心理学金句、实景访谈 |
| `arch_06_paper_stopmotion` | 纸张拼贴定格 / 工程图解科普风 | `style_05` | 收纳改造、尺寸科普、手工拼贴演示 |

常用指令：
```bash
# 1. 列出全库 6 大母版与 20 套风格谱摘要
python3 scripts/apply_style_spectrum.py --list

# 2. 在流水线中直接套用任一风格谱（自动绑定字幕模板 + 3D LUT + J-cut）
python3 scripts/run_pipeline.py --project-dir <项目目录> --track creator --mode full --style-id style_02

# 3. 绑定风格谱至已有流水线目录，或一键导出至 剪映草稿 / HyperFrames / Remotion
python3 scripts/apply_style_spectrum.py --style-id style_16 --apply-pipeline <项目目录>/pipeline
python3 scripts/apply_style_spectrum.py --style-id style_16 \
  --export-jianying-template <草稿目录> \
  --export-hyperframes <工程目录|html路径> \
  --export-remotion-props <json路径>

# 4. 针对新的参考视频目录重新提取「风格谱」并更新资产库
python3 scripts/analyze_style_spectrum.py --input-dir <参考视频目录>
python3 scripts/build_style_spectrum_assets.py
```
