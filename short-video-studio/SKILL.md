---
name: short-video-studio
version: 4.8.0
description: 策划、剪辑和交付面向实体门店获客或个人账号增长的短视频。适用于从真实素材策划选题、编写口播和拍摄任务卡、剪辑已有实拍，或用代码制作图解与产品演示。以锁定音轨为剪辑骨架，支持成片质检、MP4 与可编辑工程导出，以及发布数据复盘。
---

# short-video-studio 短视频制片工作室

服务**不懂拍摄、不懂剪辑、不懂包装**的用户，把专业短视频编导方法论与工业级「先音后画」后期工程封装为一条龙智能工作室。

> **路径与按需读取约定**：本文中的相对路径（`scripts/...`、`references/...`、`assets/...`）均以本 `SKILL.md` 所在 Skill 目录为根。**与用户互动对话时必读** [references/interactive-coach-protocol.md](references/interactive-coach-protocol.md)；策划与写稿时**优先读取精简速查手册** [references/methodology-cheatsheet.md](references/methodology-cheatsheet.md)；仅在做 4 周账号体系规划或专项脚本深度设计时，再按需查阅完整总库 [references/建立可循环的内容系统.md](references/建立可循环的内容系统.md)。

---

## 一、 互动对话最高铁律：5 类用户分流 + 每轮必带【四要素掌控感进度卡】

### 1.1 首次进入对话：按 5 类真实用户先猜后确认（不问大空题、不说行话）
根据用户第一句话自动识别其所属类型，用**一句话先猜后确认**，同时亮出他专属的总步数（详细话术与点单菜单见 [references/interactive-coach-protocol.md](references/interactive-coach-protocol.md)）：
1. **实体店从零拍（`store_from_zero`，共 5 步，属 `store` 轨，默认 `style_01`）**：还没拍素材，想拉同城客人私信或进店 → `定话题写说话词 → 照手机清单开拍 → 听声音定节奏 → 看粗剪画面 → 拿成品与封面`。
2. **个人博主口播提质感（`creator_talking`，共 4 步，属 `creator` 轨，默认 `style_11` / `08` / `09` / `16`）**：有观点或已录好说话视频，想要小窗口、超大号重点字、金句卡片 → `定台词或收素材 → 听声音定节奏 → 选画面样式看粗剪 → 拿成品与封面`。
3. **手机存货一键挑剪（`footage_organizer`，共 3 步）**：已拍了一堆随手拍片段堆在文件夹里，懒得自己挑和剪 → `自动筛废片排顺序 → 听声音看粗剪 → 拿成品与封面`。
4. **社恐不露脸 / 纯图解动画（`faceless_visual`，共 4 步，默认 `style_24` / `22` / `06` 或纯代码制片）**：不想出镜，只有产品/环境特写或只有一段文字数据 → `定台词与配音 → 配全屏特写或动态图表 → 看粗剪确认 → 拿成品与封面`。
5. **发片后复盘调优（`post_review`，共 2 步）**：视频已发出一两天，拿后台数据来找病因 → `看数据找卡点 → 存下改进配方`。

### 1.2 每一轮回复强制携带【四要素掌控感进度卡】（MUST）
在陪伴用户推进项目的**每一轮回复**中，AI 必须同步运行 `workflow_guard.py status --pipeline-dir <dir> [--user-type <类型>]`，按 [interactive-coach-protocol.md](references/interactive-coach-protocol.md) 第二节格式展示大白话四要素进度卡（`当前进度 / 我刚做了或正在做 / 现在轮到你(附 A/B/C 选项) / 离能发还差`，严禁技术缩写或黑话）：
- **选画面样式不甩全表也不背编号**：按用户所属人群与有无真人出镜素材运行 `apply_style_spectrum.py --menu <store|creator> [--has-aroll no]`，把现算出的 **3~4 款大白话点单**原样给用户选 A/B/C/D；全仓默认输出风格为 **`style_01`（门店爆款原生风，以全屏实拍为主、按源草稿少量叠加抠像/推近窗）**。
- **字幕是「阴影」不是「描边」，一个字都不许自己加边（2026-11-23 实测纠正）**：解密草稿里 24 套字幕共 2945 个文字样式，**真描边只有 5.6%，阴影占 75.7%**；24 套里 23 套是「白/彩字 + 向右下偏移 14–20px 的半透明黑影」，`style_08` 连阴影都没有。v4.0 之前库里的 `stroke_width` 是 `sub_stroke_ratio × 5` 这个凭空值（而 `sub_stroke_ratio` 又把剪映面板的 `border_width` 默认 0.08 当成描边），于是 24 套全被画上 5px 黑边——用户实测抓到的「style_01 明明是深灰阴影，却变成描黑边」就是这个。现在 `subtitle_spec` 里 `stroke_mode` / `stroke_width` / `shadow` / `shadow_color` / `shadow_offset` / `shadow_blur_px` 全部来自 `forensic/text_style_truth.json`（`dev_tools/measure_text_style_truth.py` 从 `styles[].strokes` / `styles[].shadows` 量），花字同理（24 套 1636 个花字样式，真描边 0 个）。**不要凭手感加描边，也不要凭手感把阴影改成描边。**
- **过程文件只许落在两个地方：工作区，或素材目录下的 `_SVS工作区/`（2026-11-23 实测纠正）**：用户实测 Agent 跑完把中间产物撒在他的素材目录里。现在的规则是——默认工作区 `~/.short-video-studio/workspaces`（或 `--workspace-root` / `SVS_WORKSPACE_ROOT`）；如果那个目录写不进去（沙箱只允许写工作目录），自动回落到 `<素材目录>/_SVS工作区/`，所有 `pipeline/ state/ out/` 全在这一个目录下面，素材目录根上**一个文件都不许落**（`素材说明.txt` 骨架也改成写进 `_SVS工作区/素材说明_待填写.txt`，要贴着素材放就用 `--notes-out` 自己指定）。
- **风格必须 100% 照做，没有用户点头一项都不许改（硬门禁）**：一旦选定了 `style_XX`，就**完整套用库里这套的全部参数**——字号、关键词倍数、主色/关键词色、描边、底框、纵向位置、每行字数、B-roll 占比、画中画构图、转场（恒为硬切）、巨字条数、花字密度、调色与 LUT。**未经用户明确同意，禁止自行"优化"任何一项**（常见犯规：觉得字太小就放大、觉得颜色不够跳就换色、觉得节奏慢就加转场、觉得素材不够就调低 B-roll 占比、觉得花字少就多塞几条）。想改，先把"库里是 X、我想改成 Y、为什么"说清楚，等用户点头；用户点头后用 `style_lock.py --waive <字段> --reason "用户原话"` 留痕，再改。没有这条豁免记录就改，`run_pipeline.py` 在出片前会直接停下（退出码 3），`qa_inspector` 的 `style_lock_intact` 也会 FAIL。
  ```bash
  python3 scripts/style_lock.py --pipeline-dir <项目>/pipeline        # PASS / FAIL 逐项列出「库是多少、实际是多少」
  python3 scripts/style_lock.py --pipeline-dir <项目>/pipeline --waive subtitles.font_size --reason "用户说字太小"
  ```
- **先确认文案与剪辑方案再动手（硬门禁）**：不管是新写稿还是带已有素材来剪，**绝不能没让用户看过说话文案和镜头排查结果就直接剪成片**！先运行 `--mode plan-only` 输出大白话确认单 `00_confirm_plan.md`（含缺镜提醒与耗时预估），用户点头（或运行 `workflow_guard.py confirm-plan`）后再正式开剪；用户否决的废镜/时间窗立刻用 `workflow_guard.py exclude-shot` 持久化标废。
- **任务耗时预估 + Windows 非 C 盘 + 跨项目零冗余**：每次启动长任务前给出清晰中文等待时间预期；Windows 环境下首次工作区避开 C 盘并询问用户存放位置（持久化至 `~/.short-video-studio/config.json`）；同一账号不同项目自动通过内容指纹 + 硬链接零冗余复用已归一素材，不重复占满硬盘。
- **内置 `gbro-cover-design` 封面技能 + Cola 智能体自动联动**：已完整内置 `subskills/gbro-cover-design/`。当检测到当前智能体为 Cola（`PI_PROVIDER=cola` 或 `COLA_ACCOUNT_DIR` 存在或任一 `COLA_*` 环境变量命中）时，`step9_title_cover.py` 会自动从 A-roll 筛选五官最清晰自然的一帧保存为 `09_host_face_ref.png` 作为图 1 垫图，并产出 `09_gbro_cover_prompt.md` 与 `09_gbro_cover_handoff.json`，AI 直接调用该内置封面技能交付 3:4 爆款真人封面。
- **内容驱动知识讲解动画（严禁套用无关占位图）**：`--mode knowledge-animation` 必须按具体台词知识内容分幕驱动 8 类可视化组件（数字卡 / 价格柱状对比 / 左右反差 / 流程机制 / 占比环图 / 趋势折线 / 场景价目卡片 / 精排片名卡）或手写 `custom_scenes.js`，严禁出现与主题无关的星球/太空占位元素。

---

## 二、 唯一选题与文案方法论（内部严格溯源，对外全程大白话）

- 速查手册与大白话交付模板：[references/methodology-cheatsheet.md](references/methodology-cheatsheet.md)
- 完整编导知识总库：[references/建立可循环的内容系统.md](references/建立可循环的内容系统.md)

**三条硬性约束**：
1. **【最高优先级】对用户全程说大白话，严禁蹦出课件或专业黑话**：内部思考与脚本编排严格按方法论选型，但**在面对用户对话、交付 4 周内容日历、口播稿、分镜表、拍摄卡、剪辑表、《我替你做的决定》与周复盘时，严禁直接输出「水桶流、漏斗流、5A、A1–A5、开篇钩子、人设、爆点前置、A-roll/B-roll、J-cut/L-cut、Room Tone、LUFS、LUT、HDR、完播率」等黑话或英文缩写**，全部按 [methodology-cheatsheet.md](references/methodology-cheatsheet.md) 第零节转译成老百姓听得懂的人话。
2. **写稿前必过「事件一句话」自检**：动笔前先答「一句话能不能说清这条视频里发生了什么事？」。说不清具体事件必须退回重想；写稿落实**「用看得见的画面代替抽象感受」**与**「把专业词换成老奶奶也听得懂的话」**。
3. **开篇先让观众看懂发生了什么**：口播默认前 3 秒 2 次画面切换；纪实片允许连续展示完整动作；图解默认 1 次切换。结合画面信息可读性与片型调节镜长。

---

## 三、 三入口工作流与「最多打断 3 次」交互契约

每生产一条视频，最多只允许在 **3 个节点** 打断用户确认；其余后期参数全部按默认专业值自动推进并在交付时呈报大白话《我替你做的决定》。

### 三入口 × 四动作速览（主入口优先服务真实素材成片）
- **三条业务入口（对应 `scripts/run_pipeline.py --entry <new|edit|review>`）**：
  1. `--entry new`：从零策划与筹备开拍（产出确认单 `00_confirm_plan.md` 与拍摄清单）。
  2. `--entry edit`：现成实拍直接开剪成片（优先走先音后画 + 风格模板全流程）。
  3. `--entry review`：发片后数据复盘（关联 `09_publish_ops.md` 归因码与后台留存曲线）。
- **四个高频动作（对应 `--mode`）**：`plan-only`（出确认单）/ `full`（全流程出片）/ `patch-lines`（局部改几句）/ `reedit-visuals`（只改画面或 `--keep-edl` 换风格）。

### 入口路由（优先一手原材，拒绝二手模板套话）
- **一手原始材料接入与口播稿体检**（有同行评论/客服记录/门店凭证或口播初稿时优先运行；产出的 `00_raw_intel.json` 会自动透传至选镜、关键词高亮、花字提炼与封面）：
  ```bash
  python3 scripts/raw_intel_ingest.py \
    --comments-file <评论或客服导出.txt> \
    --proof-dir <实拍凭证目录> \
    --script-json <脚本.json> \
    --track <store|creator> \
    --output <项目目录>/pipeline/00_raw_intel.json
  ```
  仅在核实记录确实来自真实客户导出时才加 `--verified-customer-export`。价格、数据、对比与实拍承诺可用 `--sources-json` 关联原件，格式见 [references/pipeline-schemas.md](references/pipeline-schemas.md)。
- **入口 1（从零策划实拍）**：梳理账号主页四件套与受众定位 → 按三阶段配比生成大白话 4 周发片计划 → 逐条过「事件一句话」自检并写大白话口播稿 → 用 `generate_shot_card.py` 生成带 SVG 构图示意的手机拍摄卡。
- **入口 2（带实拍素材进来）**：运行素材深度盘点（高动态/Log 探测、锐度/防抖锁定最佳入点、景别/运镜分析、人脸与显著性感知、生成缩略接触表），输出 `00_inventory.json` 与 `00_shot_log.json` 反推选题：
  ```bash
  python3 scripts/inventory_media.py --input-dir <原素材目录> --output <项目目录>/pipeline/00_inventory.json --track <store|creator>
  ```
- **入口 3（零实拍代码制片 `--mode code-video` / 知识讲解动画 `--mode knowledge-animation`）**：
  - 编码前**必读总纲与四大工艺** [references/code-video-methodology.md](references/code-video-methodology.md) 及对应手册（[knowledge-animation-craft.md](references/knowledge-animation-craft.md) / [motion-broll-and-subtitles.md](references/motion-broll-and-subtitles.md) / [script-craft-and-platforms.md](references/script-craft-and-platforms.md)）。
  - **知识讲解动画（支持「无配音字幕即旁白」与「配音同步」双模式 + 16:9/9:16/1:1/3:4 画幅 + 商业字体注入 + 配乐/拟音混音 + 双比例 4K 代码封面 + 分平台发布文案包）**：
    ```bash
    python3 scripts/run_pipeline.py --account <账号> --workspace-root <工作区> --track creator --mode knowledge-animation --anim-title "<片名>" --anim-idea "<核心反常识命题>" --anim-ratio <16x9|9x16|1x1|3x4> --anim-font-mode <sans|serif>
    ```
  - **多卡片图解与产品演示规约闭环**：
    ```bash
    python3 scripts/code_video_spec.py --init-spec --title "<标题>" --one-idea "<核心命题>" --out-dir <pipeline目录>
    python3 scripts/code_video_spec.py --validate-spec <pipeline目录>/00_code_spec.json
    python3 scripts/run_pipeline.py --account <账号> --workspace-root <工作区> --track <store|creator> --mode code-video --code-spec <pipeline目录>/00_code_spec.json --script-json <台词JSON> --style-id <按赛道取值：store→style_01，creator→style_11>
    ```
  - **口播增强套件（语音岛粗剪、动态讲解窗口 + 平滑圆环人像双源画中画、词边界 DP 短句字幕、15 种动效拟音与字体子集化）**：
    - `step2_transcribe.py`（语音能量岛定界 + 成片滑窗重说复查）→ `step4_build_edl.py`（自动探测动态讲解窗口并绑定真双源圆环画中画）→ `step5_subtitles.py`（词边界 DP 断句 + `terms.txt` 保护词 + 双层柔和阴影 ASS/SRT + `05_subtitle_check.txt`）→ `synth_motion_sfx.py`（15 种无版权动效拟音峰值对齐）→ `step9_title_cover.py --code-cover-4k`（分平台发布文案包 `09_publish_copy_pack.md` + 360px 手机信息流小图预览 + 16:9/3:4 双比例 4K 封面）→ `subset_fonts.py`（按工程用字自动压缩子集字体）。

### 专属工作区隔离、合规门禁与诚实基线（MUST）
- **原素材严格只读**：所有加工、转码、临时帧和成片导出统一写入独立专属目录（默认 `~/.short-video-studio/workspaces/accounts/<account_slug>/projects/<project_id>/`，可用 `--workspace-root` 指定），**严禁向 `--raw-dir` 写入任何文件**。
- **广告法与平台合规前置 (`compliance_guard.py`)**：`must` 级违禁词（精准排除「最近/最后/最怕/第一口」零误杀）在文本/配音模式自动温和改写，在**实拍真人口播模式整句弃用并留痕**（严禁靠改字幕遮掩原声）；`warn` 级支持账号白名单 `compliance_whitelist.json` 凭证核销。
- **诚实基线**：未导入真实评论/客服原材时显式声明「未经真实顾客提问验证」；复盘数据未达到实验样本量前绝不盲调参数。

### 3 次打断节点（Strict Interrupt Cap = 3）
1. **🛑 打断 #1：确认选题与脚本**（或结合原材体检与 `00_contact_sheet.jpg` 拍板）。
2. **🛑 打断 #2：确认拍摄任务卡或锁定音轨盲听版 (`03_blind_audio.m4a`)**
   - 手机拍摄任务卡（支持 `--audio-events` 按配音反定每镜时长与 `--industry general|catering|auto_repair`）：
     ```bash
     python3 scripts/generate_shot_card.py --output <项目目录>/shot_card.md --title "<选题>" --track <store|creator> --industry catering
     ```
   - 缺镜头时调用 7 种修辞同构代码补镜（`--backend auto|html|pillow`）：
     ```bash
     python3 scripts/generate_code_broll.py --output <补镜.mp4> --mode <kinetic_text|data_chart|flow_diagram|proportion|day_night|chips|versus> --register warm_food --backend auto --title "<标题>" --items-json '<JSON>'
     ```
3. **🛑 打断 #3：确认锁定音轨、低清粗剪预览与方案哈希锁 (`workflow_guard.py`)**
   ```bash
   python3 scripts/run_pipeline.py --account <账号> --raw-dir <原素材目录> --track <store|creator> --mode lock-preview
   ```
   拍板后可用 `workflow_guard.py lock --pipeline-dir <dir>` 上锁，再按需走 `--mode render-locked`（锁定出片）、`--mode reedit-visuals`（只改画面音轨不动）或 `--mode relock-voice`（改词重锁音轨）。仅在真实向用户发起确认时调用 `workflow_guard.py record-stop`。

---

## 四、 先音后画流水线与独立验收交付

### 4.0 两条硬引擎（先看这两条，再谈剪辑）

**1) 转写：SenseVoice 是主引擎，Whisper 只是兜底。**
不是偏好问题，是实测差 20 倍以上、而且差在对不对：

| 引擎 | 同一段 54.9 秒粤语口播 | 结果 |
|---|---|---|
| SenseVoice int8（默认） | **1.1 秒** | 自己判出 `<\|yue\|>`，字面准确 |
| faster-whisper medium | 52 秒 | 被 `language=zh` 按住当普通话听，金湾→今晚 |
| faster-whisper small | 26 秒 | 同一句复读 4 遍 |

```bash
python3 scripts/asr_sensevoice.py <某段.wav> --tokens   # 自检：模型在不在、字级时间戳
python3 scripts/run_pipeline.py ... --asr-engine auto   # 默认：SenseVoice 优先，不可用才回落 whisper
```

模型放这三个位置任一即可（约 240 MB，`model.int8.onnx` + `tokens.txt`）：
`~/.short-video-studio/models/`、`~/.coli/models/`、`<skill>/vendor/models/`。
`--asr-engine sensevoice` 显式指定时模型缺失会直接报错，**不静默降级**。

**2) 看画面：抽密帧 + 多模态必须走大模型，严禁本地分类器冒充。**
流水线在 `plan-only` 会自动跑这一步（可 `--no-vision-package` 关掉）：

```bash
# ① 自动备料（run_pipeline 自己会跑；也可手动）
python3 scripts/tag_frames.py --input-dir <素材目录> --out <pipeline>/vision \
    --engine agent --frames-per-clip 4 --exclude-dirs 口播
# ② Agent 看 vision/grids/grid_*.jpg（一张 48 格，左上角「段序号-帧序号」）
# ③ 判定写 <pipeline>/vision/00_vision_labels.json 后并回链上
python3 scripts/tag_frames.py --apply --out <pipeline>/vision
```

判定一旦写回，`run_pipeline` 会自动认领并生成 `00_visual_index.json`；没有判定时，
`00_confirm_plan.md` 的「画面依据」一栏会明写**大模型还没判、选画面只能靠文件名**——
不许假装看过画面。

**3) 文案：ASR 只给逐字稿，上屏前必须整理一遍。**
SenseVoice 的好处是专名准、方言自判，代价是**逐字原话**——口癖、半截句、同一句的四个重拍版本、
现场口令 `三二一组` 全都留着。实测 96 句里能直接用的只有 20 出头。所以有第三步：

```bash
python3 scripts/script_polish.py --pipeline-dir <pipeline> --tasks   # run_pipeline 会自动跑；出任务清单
# Agent 按清单整理：只去口癖、合并被停顿切碎的半句、补标点；不改事实、粤语保持粤语
# 判定写 02b_polish_labels.json 后：
python3 scripts/script_polish.py --pipeline-dir <pipeline> --apply   # -> 02c_script_final.json
```

整理后的成稿是确认单、字幕、剪辑共同读的那份；听不准的地方必须写进 `note`，
确认单会单列成「需要你核一下的地方」，**不许猜**。`--no-polish` 可关（不建议）。

完整中间文件契约见 [references/pipeline-schemas.md](references/pipeline-schemas.md)，安全区与居中字幕规范见 [references/platform-safe-zones.md](references/platform-safe-zones.md)。

一键跑通端到端流水线（默认全局/门店轨 `style_01` / 博主轨 `style_11`，自动落盘 `out/` 并清理大体积临时文件）：
```bash
python3 scripts/run_pipeline.py \
  --account <账号> \
  --raw-dir <原素材目录> \
  --track <store|creator> \
  --mode <plan-only|full|code-video|knowledge-animation|lock-preview|reedit-visuals|relock-voice|patch-lines|render-locked> \
  [--style-id style_01] \
  [--require-plan-confirm] \
  [--keep-edl] \
  [--no-flower-text] \
  [--patch-json <patches.json>] \
  [--agent cola] \
  [--raw-intel <00_raw_intel.json>] \
  [--sources-json <sources.json>] \
  [--visual-index <00_visual_index.json>] \
  [--asr-engine auto|sensevoice|whisper] \
  [--no-vision-package] \
  [--licenses-json <licenses.json>] \
  [--dialect mandarin|cantonese|dialect] \
  [--store-anchor <门店名>] \
  [--multi-speaker] \
  [--hook-variants 2] \
  [--exports auto|mp4|jianying|hyperframes|both]
```

独立导出与单步重跑命令：
```bash
python3 scripts/export_jianying.py --pipeline-dir <pipeline目录>        # 烘焙版：字幕/花字烧成 PNG，不可改字
python3 scripts/export_jianying_native.py --pipeline-dir <pipeline目录> # 原生草稿：文字真可编辑（同字体族/同版式）
python3 scripts/export_hyperframes.py --pipeline-dir <pipeline目录>
python3 scripts/qa_inspector.py --pipeline-dir <pipeline目录> [--sync-window <起秒> <止秒>] [--strict-asr]
```

### 核心专业能力与出厂验收门禁
- **真居中对称字幕铁律**：全套渲染、剪映、HyperFrames 与白盒/黑盒质检统一锁死 `X = 540px` 正中对称带 `[220, 860]`（宽 `640px`）与 `Y = 1310px` 基准（自动 7 点避脸），绝不发生左偏或右撞点赞栏。
- **真实物理音景与现场动作声**：实拍流水线统一采用 `assets/sound_vibe/` CC0 真实录制乐段（波形校准真实起拍）与物理音效，支持开场→正文双段配乐切换、特写镜现场动作声轻混（`-10dB`）及三轨防削波总线控制（`-14.0 ±1.5 LUFS`、真峰值 `≤ -1.0 dBTP`）；知识动画与动态图解辅以 `synth_motion_sfx.py` 15 种无版权动效拟音。
- **视觉语义与封面**：支持高动态/常见 Log 色彩还原、横转竖人脸智能居中构图、53% 特写动态配比、景别与运镜防撞墙语法、全片情绪+眼部锐度选帧封面（9:16 + 3:4 防裁切 + 180px 真实像素对比度校验 + macOS Apple Vision 可选人物前景压字）。
- **独立双层质检与盲审取证包**：`qa_inspector.py` + `independent_verifier.py` 执行白盒契约 + 单遍解码黑盒校验（含 540px OCR 居中实测、单框 WCAG、BT.709 肤色、切点像素跳变、SFX 5ms 起音校准、本地 Whisper 盲听复核与商业授权清查），同时生成一页式大白话《独立盲审取证包》（`10_blind_review_pack.md` + `10_action_strips.jpg`）。
- **按源接线（不妥协版）**：想做到像素级/听感级 1:1，把源模版原版资产放进三个位置即可，代码会自动优先命中并在产物里标注：① 字体 `assets/style_spectrum/fonts/<style_id>/`（文件名含源字体族名，如 `知新体-Bold.otf`；命中后 `05_subtitles.json` 里 `font_exact_match: true`，否则回落可商用替代并给出放置路径提示）；② BGM `assets/sound_vibe/user/<style_id>/`（文件名含源曲名，如 `复合片段5.wav`）；③ 音效 `assets/sound_vibe/user/<style_id>/<role>.(wav|mp3|m4a)`，role 取 `transition/accent/ui/receipt`，命中角色会记进 `user_original_sfx_roles`。
- **两条工程交付路线**：`export_jianying.py` 出的是**烘焙版**（FCPXML + 透明 PNG 图层，`manifest.json` 里 `export_kind: baked_interchange`、`text_editable: false`，导入后文字不能改）；要改字走 `export_jianying_native.py` 出**原生草稿**（`draft_content.json` + `draft_meta_info.json` + 相对路径素材，字幕与花字是真文字素材，字体族沿用源模版的 `source_font_family`，字号/描边/底框/位置按实测写入）。原生草稿结构可用 `dev_tools/verify_native_draft.py` 对解密后的真草稿逐键自证（顶层键/素材表/各轨 segment/文字素材/视频素材零缺失）。HyperFrames 样板 `apply_style_spectrum.py --export-hyperframes` 也按单套风格生成（巨字 hook + 顶栏/画中画 + 按源字数上限切的字幕行 + 底框/描边/调色近似），不再是通用标题加一行字。
- **调色与画面特效只认源草稿真值**：三段曲线按「源分位→目标分位」直连（`black_lift` 可抬黑位），闭环校准最多 5 轮、判据是**特效之前**的 p05/p50/p95；验收器 `dev_tools/verify_grade_calibration.py`（自校 + 中性迁移两种口径，均要求残差 ≤12 级）。画面特效只做源 `effect` 轨里真有的那几段：24 套里仅 `style_02/04/08/15/17/20/21` 有（暗角/怀旧边框/不规则黑框/荧幕噪点/颤抖/聚光灯/模糊），其余 17 套一条不加；特效带 `window`，只在源时间窗内生效。真值由 `dev_tools/build_effect_usage.py` 从解密草稿生成 `forensic/effect_usage.json`，漂移由 `verify_style_parity.py` 的 `effects vs source track` 拦截。聚光灯用 `geq` 径向遮罩真挖亮圈，不再是暗角顶替。
- **画中画按真草稿几何渲染**：24 套都有叠加视频轨（1–9 条），库里的 `pip_composition_spec` 一律是 `geometry_pip_*`，几何取自 `forensic/pip_usage.json`（`clip.transform` / `uniform_scale` / `materials.videos.matting`）。渲染端单源按主轨推近或缩窗、双源把副素材摆到真值窗口坐标并加描边；`step4` 的范式分支也只写真值几何，不再套别人的版面。`render_profile.json` 的 `pip_geometry.windows_unrealized` 会明写「没拿到第二路素材的镜数」。
- **特性图自查**：`dev_tools/render_style_feature_sheet.py` 每套出 5 格（源素材 / 真草稿构图 / 库字幕 / 蓝图花字 / 源真值特效）并标出构图与源帧的逐像素差，供人眼核对，不必靠指标自述。
- **B-roll 必须和台词对得上，对不上就说对不上**：`broll_semantics.py` 是离线概念桥（场景/实体/动作/意图/人物 × 行业域），把台词和素材的文字线索概念化后再比，零依据就是 0 分。每个外来画面在剪辑表里带 `sem_match`（verdict + basis），md 表里有「凭什么对得上」一列。素材侧唯一的主题词来源是：中文文件名、中文文件夹名、画面 OCR、以及用户手写的 `素材说明.txt`（一行一条 `文件名 一句画面内容`，`inventory_media` 会自动读，缺失时盘点报告点名并生成填空骨架）。素材没线索时系统标 `none` 并计入 QA，不许假配。
- **小窗/推近以人脸为中心取景**：`face_framing.py` 抽 3 帧用 Apple Vision 找人脸，取最大一张跨帧中位作锚点；横向正中、纵向落窗高 44%（大特写自动往正中收），脸太大先退远再裁，贴边钳位。`step4` 把锚点写进 EDL（`face_anchor`/`secondary_face_anchor`/`face_framing`），渲染、封面、导出共用一份依据；检测不到人脸就退回居中并明写 `no_face_center_crop`。
- **人工终审签字与归档验证**：核对后执行 `workflow_guard.py approve-final --pipeline-dir <dir> [--reviewed <ID> ...] --note "<实查结果>"` 签字，并可用 `workflow_guard.py verify-out --out-dir <out目录>` 核对成品指纹。风格复刻场景用 `workflow_guard.py replication-diff` 跑洁净室防抄袭与乱序盲测。

---

## 五、 发布复盘与对照实验闭环

发片 48 小时后收集分子/分母快照数据（支持 `retention_3s`、`completion_rate`、`inquiry_rate`、`store_visit_rate`、`follow_rate` 与 `drop_off_sec` + `edl_path` 镜头归因），写入按账号与轨道隔离的 `content-log`：
```bash
# 1. 记录发布快照并生成大白话周复盘
python3 scripts/content_log_review.py --account <账号> --track <store|creator> --add-json '<快照JSON>'

# 2. 预登记对照实验 -> 回放历史 -> 达标后显式启用候选参数
python3 scripts/content_log_review.py --account <账号> --track <store|creator> --register-plan <plan.json>
python3 scripts/content_log_review.py --account <账号> --track <store|creator> --replay-plan <plan.json>
python3 scripts/content_log_review.py --account <账号> --track <store|creator> --activate-candidate <candidate_id>
```
启用后在制作命令加 `--platform <平台名>` 即可生效，旧配置自动归档到 `profile_history/`。

---

## 六、 风格谱参考包装库与环境初始化

- 已内置 **24 套 v4.0 剪映实测风格库**（`style_01` ~ `style_24`，24/24 全部解密自真实剪映主稿 `draft_content.json`，每套带 `source_hash`；指标由 `dev_tools/build_style_library_v4.py` 从草稿推导，`dev_tools/verify_style_parity.py` 逐字段校验 24/24 PASS）与 **4 大共享剪辑规则字典**：
  - **全局/门店获客默认（`style_01`）**：全屏实景出镜、中下部高低错落斜标开场、57% 店内特写穿插（保留 `-10dB` 现场动作真声）、居中无底框双色暖黄重点字、双段音乐；同组还有真轮廓白边抠像（`style_07` 60%、`style_21` 76% / 7 处抠像）、清冷奶油极简宋体（`style_04`）、暖金双格对比（`style_17`）、户外探盘 3D 金属立体大字（`style_18`）、国风古字典体大字（`style_20`）、人像压巨字+矩形画框（`style_05`）、上圆角框下黑幕（`style_13`）、慢调空镜微纪录（`style_23` 46%、4.08s 中位）。
  - **个人账号默认（`style_11`）**：清亮通透无底框大字；同组还有暗灰底框极速跳剪（`style_02` 11%、1.13s）、顶栏引语卡+10 条巨型浅黄大字（`style_03`）、黑底拍立得手写金句（`style_06`）、专家长镜 29.23s 一镜到底+方角底框（`style_08`）、多重圆窗纪实（`style_09`）、190 次/分高频推拉（`style_10`）、顶置双色悬念标（`style_12`）、纯黑聚光圆窗（`style_14`）、弹性双色花字流（`style_15` 10 条巨字）、羽化分屏书摘（`style_16`）、抠像长句大字（`style_19`）、案例轮播（`style_22` 41%）、不露脸快切混剪（`style_24` 55%）。
  - **口径**：所有风格源草稿转场数均为 0，默认硬切；B-roll 占比是风格先验、运行时按素材动态自适应（±15pp）；字体按 `source_font_family` 声明，缺源字体时回落替代并标 `exact_1to1=false`。
  - 完整大白话选用指南见 [assets/style_spectrum/STYLE_SPECTRUM_REPORT.md](assets/style_spectrum/STYLE_SPECTRUM_REPORT.md)。
  ```bash
  python3 scripts/apply_style_spectrum.py --menu <store|creator> [--has-aroll no]   # 给用户点单的 3~4 款
  python3 scripts/apply_style_spectrum.py --list [--has-aroll no]                  # 维护/排查时看全表
  python3 scripts/apply_style_spectrum.py --style-id style_01 --apply-pipeline <项目目录>/pipeline --apply-full --keep-edl
  python3 scripts/apply_style_spectrum.py --style-id style_01 --export-hyperframes <out_dir>
  ```
- 所有随仓内置的 5 款开源商用字体（阿里妈妈数黑体、得意黑、思源宋体 Bold & Heavy、马善政毛笔手写体）、CC0 真实录制 BGM、物理 SFX 与 `17³` LUT 授权清单见 [references/assets-attribution.md](references/assets-attribution.md)。
- 首次部署或排查依赖/字体/无头浏览器桥时运行：
  ```bash
  python3 scripts/check_and_setup.py --init-fonts --init-assets --install-deps --init-browser
  ```
