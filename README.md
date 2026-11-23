# short-video-studio 短视频制片工作室

面向实体门店获客和个人账号增长的短视频制片 Agent Skill。支持从一手真实材料策划选题、按锁定音轨「先音后画」剪辑、代码生成图解补镜、高清 MP4 与剪映/HyperFrames 可编辑工程导出、独立黑盒质检与盲审取证，以及发布数据对照实验复盘。

---

## 一、 核心设计原则

1. **首次对话「5 类用户分流 + 四要素掌控感进度卡」**
   - **门店轨（`store`）**：服务实体店老板 / 本地生活商家，以**评论区咨询、私信、到店核销**为核心目标（默认全局风格 `style_01`）。
   - **博主轨（`creator`）**：服务个人创作者 / 知识或泛娱乐账号，以**开头 3 秒留人、看完全片、新增关注**为核心目标（默认风格 `style_11`）。
   - 对话按 **5 类真实用户**先猜后确认，每轮附带大白话四要素进度卡（`00_progress_card.json`）与**任务耗时预估**；正式剪辑前强制提供**大白话文案与方案确认单（`--mode plan-only` / `00_confirm_plan.md`）**。
   - **内置 `gbro-cover-design` 封面技能 + Cola 自动联动**：识别 `PI_PROVIDER=cola` / `COLA_ACCOUNT_DIR` / `COLA_*` 时自动从口播挑选五官最清晰帧（`09_host_face_ref.png`）作为垫图生成 3:4 真人封面交接单。
   - **国内高速镜像 + 双系统离线全依赖包 + Windows 非 C 盘 + 跨项目零冗余**：安装一键接入清华/阿里/淘宝/HF 国内镜像，支持构建 macOS / Windows 离线全依赖包（含 Whisper 模型）；同一账号跨项目自动硬链接复用已归一素材。
2. **唯一选题与文案方法论（内部严格溯源，对外全程大白话）**
   - 以 [methodology-cheatsheet.md](short-video-studio/references/methodology-cheatsheet.md) 为日常速查手册、[建立可循环的内容系统.md](<short-video-studio/references/建立可循环的内容系统.md>) 为完整总库；搭配 [script-craft-and-platforms.md](short-video-studio/references/script-craft-and-platforms.md) 执行四轮审稿体检与双文件解耦导出。
   - **全程零黑话铁律**：内部按完整编导体系选型并过「事件一句话」自检；面向用户对话、4 周发片计划、口播稿、拍摄卡、剪辑表、《我替你做的决定》与周复盘全部转化为老百姓听得懂的大白话。
3. **三入口与「最多打断 3 次」交互契约**
   - **入口 1（从零策划实拍）**：主页定位 → 4 周发片计划 → 口播稿与带构图示意的手机拍摄卡。
   - **入口 2（带素材进来）**：一键盘点零散素材（高动态/Log 探测、锐度/防抖最佳入点、景别运镜、人脸显著性与缩略接触表），自动反推选题与剪辑方案；口播支持语音岛粗剪、动态讲解窗口 + 平滑圆环人像画中画与词边界 DP 短句字幕（`terms.txt` 保护词）。
   - **入口 3（纯代码制片 `--mode code-video` / 零实拍知识讲解动画 `--mode knowledge-animation`）**：零实拍直出，支持发光线描动画（16:9 / 9:16 / 1:1 / 3:4）+ 商业字体子集注入 + 15 种动效拟音与配乐混音 + 双比例 4K 代码封面 + 分平台发布文案包。
   - **仅 3 次交互打断**：① 确认选题 → ② 确认拍摄卡或盲听音轨 → ③ 确认锁定音轨与低清粗剪预览（支持只改画面不动音轨或改词重锁音轨）。
4. **「先音后画」+ 真居中字幕 + 真实物理音景**
   - Step 3 锁定纯语音 + CC0 真实录制配乐节拍（支持开场/正文双段配乐切换、真实波形起拍校准、语音岛静音中段切点微调），后续切镜、顺滑转场、特写动作声轻混与音效花字严格绑定音频事件帧。
   - 字幕四端（MP4 / 剪映 / HyperFrames / 质检）强锁 `X = 540px` 正中对称带 `[220, 860]`（宽 `640px`）与 `Y = 1310px` 基准，7 点自动避脸不撞点赞按钮。
5. **独立黑盒裁判与一页式盲审取证包**
   - 每次出片自动跑全套白盒契约 + 单遍解码黑盒校验（`-14.0 ±1.5 LUFS`、True Peak `≤ -1.0 dBTP`、单帧黑屏、含尾段的长静音检测、残留重说扫描、540px OCR 居中实测、单框 WCAG、BT.709 肤色、切点像素跳变、SFX 5ms 起音校准、本地 Whisper 盲听与商用授权清查），同时生成《独立盲审取证包》（`10_blind_review_pack.md` + `10_action_strips.jpg` + `09_cover_360px_feed.jpg` 手机信息流小图预览）。

---

## 二、 仓库目录结构

- `short-video-studio/SKILL.md`：Agent Skill 入口与工作流规范。
- `short-video-studio/references/`：10 篇精简方法论、交互协议、中间文件契约、安全区规范、代码制片/知识动画工艺与商用授权清单。
- `short-video-studio/scripts/`：全套端到端制片、知识动画、口播增强、导出、质检与复盘脚本。
- `short-video-studio/assets/`：5 款随仓开源可商用字体（含毛笔手写体 `MaShanZheng-Regular.ttf`）、CC0 真实录制 BGM & 物理 SFX（`sound_vibe/`）、知识动画 Canvas 运行时（`canvas_runtime/`）、28 套字幕模板与 24/24 全白盒剪映风格模板蓝图与 `17³` LUT（支持逐帧 AI 人像前景抠像、6 大 A/B-roll 切换样式范式与实测频率/关键帧推拉、30fps 动态文字动效与 `xfade` 镜头转场）。
- `dev_tools/`：离线样本分析与通用 LUT 重建工具（开发维护用，不随 Skill 分发包打包）。
- `tests/`：核心流水线与回归测试套件。
- `BRIEF.md`：项目初版立项设计简报（历史归档）。
- `install_skill.py` / `install.sh` / `install.ps1`：跨平台一键环境检查与安装脚本。

---

## 三、 安装与环境检查

前置要求：**Python 3.10+**、**FFmpeg / FFprobe**（macOS 未安装时脚本可自动调用 Homebrew 安装）、Git。

### macOS / Linux
```bash
git clone https://github.com/xie1701/short-video-studio.git
cd short-video-studio
bash install.sh
```

### Windows (PowerShell)
```powershell
git clone https://github.com/xie1701/short-video-studio.git
cd short-video-studio
.\install.ps1
```

安装入口先验证 Python 3.10+、FFmpeg/FFprobe 和核心 Python 依赖，再部署 Skill。支持 `--check-only` 只检环境和 `--skills-dir` 指定目录；已有安装自动保留备份以便回滚。Node 和本机 Chrome 用于 HTML 动效与 HyperFrames 官方运行时校验。

---

## 四、 使用指南

### 方式 1：在 Claude Code 中直接对话触发
安装后打开任意会话直接用自然语言描述即可：
- **带素材进场**：“帮我把 `./raw_media` 里的探店口播和空镜素材盘点一下，用门店轨剪一条宣传成片。”
- **从零起号策划**：“我想做一个餐饮门店短视频账号，帮我制定 4 周发片计划并先出第一条的手机拍摄卡。”
- **复盘迭代**：“这是这条视频发出去 48 小时后的播放、3 秒留存和咨询数，帮我记入周复盘。”

### 方式 2：命令行单独调用各工程工具

> 脚本内置 `maybe_reexec_in_venv()`，普通 `python3` 调用也会自动切换至 `~/.short-video-studio/venv` 隔离环境。

1. **一手客户原话接入与剧本口语体检**
   ```bash
   python3 short-video-studio/scripts/raw_intel_ingest.py \
     --comments-file ./comments.txt --proof-dir ./proofs --script-json ./script.json \
     --track store --output ./my_video/pipeline/00_raw_intel.json
   ```
2. **生成手机拍摄任务卡（入口 1）**
   ```bash
   python3 short-video-studio/scripts/generate_shot_card.py \
     --output ./my_video/shot_card.md --title "花300万砸200平纯手工厨房" --track store --industry catering
   ```
3. **素材全量盘点与反推选题（入口 2）**
   ```bash
   python3 short-video-studio/scripts/inventory_media.py \
     --input-dir ./my_video/raw_media --output ./my_video/pipeline/00_inventory.json --track store
   ```
4. **代码生成叙事补镜（缺少特写镜头时）**
   ```bash
   python3 short-video-studio/scripts/generate_code_broll.py \
     --output ./my_video/broll_data.mp4 --mode data_chart --title "200平纯手工明档厨房" \
     --items-json '[{"label":"纯手工现点现做","value":34,"unit":"%"},{"label":"拒绝预制菜占比","value":100,"unit":"%"}]'
   ```
5. **运行端到端流水线（多种执行模式）**
   ```bash
   # 粗剪预览（打断 #3）
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode lock-preview

   # 只改画面（锁定音轨一比特不变）
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode reedit-visuals --prefer-broll-only

   # 改词重锁音轨
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track creator --mode relock-voice --script-json ./new_script.json

   # 零实拍知识讲解动画（含 BGM + 15 种动效拟音 + 4K 双比例封面 + 分平台发布文案包）
   python3 short-video-studio/scripts/run_pipeline.py --account demo --track creator --mode knowledge-animation --anim-title "注意力的价格" --anim-idea "每天解锁96次手机却只记得3次" --anim-ratio 16x9 --anim-font-mode serif

   # 先出文案与剪辑方案确认单（不直接开剪，含缺镜排查与耗时预估）
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode plan-only

   # 一键正式出片（全仓默认 style_01 门店爆款原生风，支持 --keep-edl / --no-flower-text / --agent cola）
   python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode full --style-id style_01 --benchmark ./benchmark.mp4

   # 构建 macOS / Windows 离线全依赖分发 ZIP 包（含 Python 轮子 + Whisper 模型）
   python3 dev_tools/build_release_zips.py --targets macos-arm64 win-x64 --whisper-model small
   ```
6. **独立校验与工程导出**
   - 自动质检：`python3 short-video-studio/scripts/qa_inspector.py --pipeline-dir ./my_video/pipeline`
   - 剪映工程：`python3 short-video-studio/scripts/export_jianying.py --pipeline-dir ./my_video/pipeline`
   - HyperFrames 工程：`python3 short-video-studio/scripts/export_hyperframes.py --pipeline-dir ./my_video/pipeline`
7. **发布数据沉淀与对照实验复盘**
   ```bash
   python3 short-video-studio/scripts/content_log_review.py \
     --account demo --track store \
     --add-json '{"track":"store","platform":"douyin","video_id":"v-01","title":"新店开门","published_at":"2026-11-08T10:00:00+08:00","captured_at":"2026-11-10T10:00:00+08:00","observation_hours":48,"duration_sec":42,"traffic_source":"local_feed","metrics":{"retention_3s":{"numerator":8500,"denominator":18500},"completion_rate":{"numerator":5180,"denominator":18500},"inquiry_rate":{"numerator":37,"denominator":18500}}}'
   ```

### 方式 3：导入剪映与 HyperFrames 双击编辑

#### 1) 剪映专业版导入（相对路径 FCPXML 多轨工程）
1. 首次使用打开剪映专业版 → 设置 → 全局设置 → 开启「导入工程」。
2. 在首页点击「导入工程」，选中 `<项目>/pipeline/jianying_draft/*.fcpxml`：
   - **主视频轨 (`V1`)**：精剪调色分镜 `media/segments/seg_xxx.mp4`；
   - **上层透明包装与双色字幕轨 (`overlays/*.png` + 备用 `<title>`)**：严格保持 540px 正中对称排版、商业字体、暖金关键词高亮与安全区避脸；
   - **三轨解耦音频 (`A1–A3`)**：`voice_master.wav`（纯净人声）、`bgm_ducked.wav`（含闪避配乐与特写现场声）、`sfx_master.wav`（点睛音效），防削波对齐可独立调音。
3. 需改文字或二次调色时，可直接拖入同目录 `subtitles.srt` / `subtitles.ass` 或导入 `*.cube` LUT。实际导入目标剪映版本后，在终审签字中记录版本与检查结果。

#### 2) HyperFrames Studio 原生时间线编辑
打开 `<项目>/pipeline/hyperframes_draft/`，双击 `启动HyperFrames工程.command`（macOS）或 `启动HyperFrames工程.bat`（Windows），或在目录内运行 `npx --yes hyperframes@0.9.72 preview .`。工程内所有视频、字幕、花字、人声、配乐与音效均以独立时间线条目暴露，完全离线自包含。

---

## 五、 成片与工程验收

- `--exports auto` 默认尝试导出两种工程，工程异常时保留通过验收的 MP4；`--exports mp4` 只做成片；`--exports jianying|hyperframes|both` 将所选工程列为必检。
- MP4 实测覆盖响度（`-14.0 ±1.5 LUFS`）、单帧黑屏、长静音、音视频起点与长度差（`≤1 帧`）、OCR 540px 居中与安全区、单框 WCAG、BT.709 肤色、切点像素跳变、SFX 5ms 起音校准及本地 Whisper 盲听复核（`--strict-asr` 时缺模型阻断）。
- 同步生成《独立盲审取证包》（`10_blind_review_pack.md` + `10_action_strips.jpg`）。完成人工核对后，使用 `workflow_guard.py approve-final --pipeline-dir <目录> --reviewed <ID> --note "<复核记录>"` 签字。

---

## 五之二、 让画面真的对上台词（v4.0 新增）

剪辑表里的每个外来画面（B-roll / 图卡）都会写明**凭什么对得上这句话**，写不出来就是 `none`，不会瞎配：

- **素材侧唯一的主题词来源**：中文文件名、中文文件夹名、画面里的文字（OCR），以及你自己补的
  `素材说明.txt`——放在素材目录里，一行一条 `文件名 一句画面内容`：

  ```
  IMG_4821.MOV 后厨现切鲜羊排
  IMG_4830.MOV 门店门头与大厅
  ```

  没写的素材，盘点阶段会点名并把这份文件生成好等你填。**素材一点线索都没有时，命中率就是 0%——
  这是诚实的 0%，不是算法坏了**；补上说明后同一批素材能到 100%。
- **画中画小窗/推近以人脸为中心取景**：先检测人脸再裁，横向正中、纵向留在窗口 44%（留头顶余量），
  脸太大先退远也不切脸；素材里没检测到人脸就退回居中裁切，并在剪辑表里标明 `no_face_center_crop`。
- **成片质检会查这两件事**：`broll_semantic_fit`（声称对得上必须有依据）与
  `pip_face_framing`（会裁切的构图必须交代有没有按脸取景）。

```bash
# 审计一条成片的"画面↔台词"对位情况（逐镜给依据，输出 CSV）
python3 dev_tools/audit_broll_semantics.py --pipeline-dir ./output/<项目目录>/pipeline
```

## 五之三、 样式不许偷偷改（v4.0.2 新增）

选定一套样式之后，**它的每个数字都是真草稿量出来的，未经你同意一项都不许动**——字号、重点词颜色、
描边、底条、字幕高低、每行字数、插画面占比、小窗构图、转场（永远是硬切）、大字条数、花字密度、整体调色。

AI 常见的手滑：嫌字小就放大、嫌颜色闷就换色、嫌节奏慢就加转场、素材不够就悄悄降占比。
这些现在都会被抓住：

```bash
python3 scripts/style_lock.py --pipeline-dir ./output/<项目目录>/pipeline
# PASS：完全按这套执行
# FAIL：逐项列出「库里是 X、实际是 Y」
python3 scripts/style_lock.py --pipeline-dir ./output/<项目目录>/pipeline \
    --waive subtitles.font_size --reason "用户说字太小，同意放大"
```

- 只有你点过头的字段才算豁免，其余照拦；豁免记错样式整份作废。
- `run_pipeline.py` 在出片前会跑一次：有偏离就停下（退出码 3），要带着偏离出片必须显式加
  `--allow-style-deviation`，并在交付说明里写清改了哪几项。
- 成片质检多了 `style_lock_intact`，每轮的进度卡上有一行「🎨 画面样式」，红了你就能看见。

## 五之四、 字幕是阴影，不是描边（v4.1 纠正）

一套字幕的「样子」在剪映里由两个东西决定：描边（`styles[].strokes`）和阴影（`styles[].shadows`）。
这两个在草稿里是分开的数组，我们以前把「阴影距离 5」读成了「描边宽度 5」，于是 24 套全被画成白字描黑边。

真草稿量出来的事实：

| 项目 | 数字 |
| :--- | :--- |
| 24 套字幕文字样式 | 2945 个 |
| 真有描边的 | 164 个（5.6%） |
| 有阴影的 | 75.7% |
| 24 套字幕里有描边的 | **0 套** |
| 24 套花字样式 | 1636 个，真描边 **0 个** |

绝大多数样式是「白色/彩色字 + 向右下偏移 14–20px、透明度 0.35–0.9 的半透明黑影」，`style_08` 连阴影都没有。
现在 `subtitle_spec` / `flower_text_spec` 的 `stroke_mode`、`stroke_width`、`shadow_offset`、`shadow_blur_px`
全部来自 `forensic/text_style_truth.json`，`verify_style_parity.py` 有 142 项逐套比对，改错就红。

```bash
# 重新量（只读解密草稿，不需要联网）
JY_DRAFT_ROOT=<剪映草稿目录> python3 dev_tools/measure_text_style_truth.py
```

## 五之四之二、 按你的背景资料纠 ASR 的错（v4.1 新增）

Whisper 对店名、人名、行话、品牌词几乎必错。在两个目录里放文件，就会被用上（先放先用）：
`<项目>/pipeline/`、`<素材目录>/`。

| 文件 | 作用 |
| :--- | :--- |
| `背景资料.md` | 自由文本，取前 400 字拼进 ASR 提示词（"这些词会出现在这段话里"） |
| `术语表.txt` | 一行一个词；`错词 => 正词` 是硬映射；`#` 开头是注释 |
| `glossary.json` | `{"terms": ["羊排"], "replacements": {"炭火": "碳火"}}` |

转写后按术语表逐条比对：**3 字以上**只差一个字、且共有字 ≥2/3 才改；**2 字词**必须整词同音
（`碳火 → 炭火`，靠 `pypinyin`，已随包安装）才改，否则一律不动。每一次替换都写进
`02_transcript.json` 的 `asr_corrections`，能反查"这句改过哪里"。

## 五之五、 过程文件不许撒进你的素材目录（v4.1 纠正）

- 默认写到 `~/.short-video-studio/workspaces`（可用 `--workspace-root` 或 `SVS_WORKSPACE_ROOT` 改）。
- 那个目录写不进去时（例如沙箱只允许写当前工作目录），自动回落到 `<素材目录>/_SVS工作区/`，
  `pipeline/ state/ out/` 全在这一个目录下面。
- 素材目录根上不会多出任何文件；连「素材说明」填空骨架也写进 `_SVS工作区/素材说明_待填写.txt`，
  想贴着素材放就用 `--notes-out <路径>` 自己指定。

## 五之六、 片尾要收住，别让话断在半截（v4.2 新增）

拿现成素材剪的片子，末句常常停在半截事实上——「这个价格就这样」——既没把话题收回去，
也没告诉人下一步做什么。框架里「水桶流」公式最后一格是**转化指令**，这一格经常是空的，
而且没人会提醒你。

现在 `step6` 会做三件事：

1. **判收尾**（`scripts/ending_plan.py`）：看最后三句，落 `closed` / `soft` / `none` 三档，
   并把让你觉得没说完的那一句原样引出来。
2. **补一张收尾卡**：真没收住就在片尾挂 1.4–2.2 秒的 CTA 卡，主句 + 副行（地址/条件落副行）。
   卡上那句话用账号资料里的店名、城市、主打服务填实，不写「点赞关注」这种空话；
   元素带 `deviation_from_style_source: true` 标记，不是源草稿语法，可以删。
   `--no-flower-text` 时不硬塞。
3. **台词五格体检**：把现成台词对到「开篇钩子 / 细节展示 / 人格化表达 / 价值说明 / 转化指令」，
   缺哪一格点名——空着可以，但得知道自己缺的是哪一格。三句候选 CTA 会写进
   `09_publish_copy_pack.md` 的「四、片尾与下次拍摄补一句」，下次拍照着说一句就行。

`qa_inspector` 新增 `ending_complete`（PASS / WARN / FAIL），片尾没交代清楚过不了门禁。

## 五之七、 这套东西能装在哪台机器上（v4.2 新增）

一句话：**渲染链路到处能跑，Apple Vision 那几件事只有 mac 有。**

```bash
# 先问清楚这台机器能跑到哪步、哪几步会降级
python3 short-video-studio/scripts/platform_probe.py
```

| 能力 | macOS arm64 | macOS Intel | Windows x64 | Windows arm64 | Linux x64 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 渲染 / 字幕 / 调色 / 混音 | 能 | 能（最慢） | 能 | 能（需轮子） | 能（ffmpeg≥5.1） |
| 自动转写（faster-whisper） | 能 | 视轮子 | 能 | **多半装不上** | 能（联网装） |
| OCR / 人脸构图 / 人像抠像 | 能 | 能 | **降级**：构图退回居中、质检转人工 | 降级 | 降级 |
| 纯配音 TTS | 能 | 能 | 只有 edge-tts（要联网） | 同 | 同 |
| 离线包 | 有 | **没有** | 有（不含 ffmpeg） | **没有** | **没有** |

「降级」不是崩：成片照出，但小窗里人物可能不居中、封面不自动选脸、黑盒质检三项要人工签字。
想把这些在非 mac 上补回来要换技术选型（OCR→RapidOCR、人脸→YuNet/mediapipe、抠像→u2net onnx），
不是装个包的事。完整证据见 [reports/platform_portability_2026-11-23.md](reports/platform_portability_2026-11-23.md)。

本轮还顺手修了四件会让安装直接失败的事：离线包里的 ffmpeg 其实是 Homebrew 动态链接版（换机器 dyld 就崩）、
`find_bin` 只看文件在不看能不能跑、Ubuntu 22.04 的 ffmpeg 4.4 不认 `-fps_mode`、winget 装完 PATH 不刷新。

## 六、 可商用内置资产与风格谱复刻库

详尽授权溯源见 [assets-attribution.md](short-video-studio/references/assets-attribution.md)：
- **背景音乐 & 物理音效**：实拍与常规混音采用 `short-video-studio/assets/sound_vibe/` 下的 **CC0 1.0 真实录制 Freesound 音频**（均已完成波形起拍校准与 48kHz 统一处理）；知识讲解动画与动态图解叠加由 `scripts/synth_motion_sfx.py` 按需合成的 **15 种无版权动效拟音**。
- **字幕与动画字体**：随仓内置 5 款免费可商用字体——**阿里妈妈数黑体**、**得意黑（SIL OFL 1.1）**、**思源宋体 Bold & Heavy（SIL OFL 1.1）**、**马善政毛笔手写体（SIL OFL 1.1）**；本机若合法安装思源黑体同样识别放行，系统私有字体（如苹方/华文黑体/微软雅黑）全程拦截。
- **风格谱资产库**：内置 **5 大包装流派母版**与 **24 套量化「风格谱 (`style_01` ~ `style_24`)」**（含 `style_21` ~ `style_24` 四套 85%–100% 高空镜纯画面/少出镜风格，配套 24 套 `17³` CC0 `.cube` 3D LUT 与 28 套可商用字幕模板），详见 [STYLE_SPECTRUM_REPORT.md](short-video-studio/assets/style_spectrum/STYLE_SPECTRUM_REPORT.md)。

```bash
# 给用户点单：按人群与出镜条件现算 3~4 款（不甩全表）
python3 short-video-studio/scripts/apply_style_spectrum.py --menu store
python3 short-video-studio/scripts/apply_style_spectrum.py --menu creator --has-aroll no

# 查看全库 24 套风格谱摘要（维护/排查用）
python3 short-video-studio/scripts/apply_style_spectrum.py --list

# 套用指定风格谱出片（门店默认 style_01，博主默认 style_11）
python3 short-video-studio/scripts/run_pipeline.py --account demo --raw-dir ./raw_media --track store --mode full --style-id style_01

# 导出某套风格谱的离线 HyperFrames 样板工程
python3 short-video-studio/scripts/apply_style_spectrum.py --style-id style_16 --export-hyperframes ./out_style_16_hyperframes

# （开发维护）基于外部视频样本提取风格特征预设
python3 dev_tools/analyze_style_spectrum.py --input-dir /path/to/reference_videos --workers 6
```
