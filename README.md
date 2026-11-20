# short-video-studio 短视频制片工作室

> **面向零拍摄与零剪辑基础用户的端到端 Agent Skill（支持 Claude Code / Gemini Antigravity）**：以 **「先音后画，以音定画」** 为核心工程铁律，打通「账号定位与 4 周 5A 日历策划 → 带简笔构图示意的拍摄任务卡 / 素材盘点反推 → 9 步确定性音视频后期流水线 → 基于 20 条标杆视频的「风格谱」一键包装（Remotion / HyperFrames / 剪映 / 3D LUT）→ 9 项客观自动质检 → 本地 1080×1920 高清成片与剪映专业版草稿双出片 → 发布后数据周复盘」全流程。

---

## 一、 核心设计原则

1. **首次对话「先猜后确认」双轨制**
   - **门店轨（Store Track）**：服务实体店老板 / 本地生活商家，以**评论区咨询、私信、到店核销**为核心目标。
   - **博主轨（Creator Track）**：服务个人创作者 / 知识或泛娱乐 IP，以**3 秒留存率、完播率、净涨粉**为核心目标。
2. **唯一选题与文案方法论（内部严格溯源，对外全程大白话）**
   - 严格以 [建立可循环的内容系统.md](short-video-studio/references/%E5%BB%BA%E7%AB%8B%E5%8F%AF%E5%BE%AA%E7%8E%AF%E7%9A%84%E5%86%85%E5%AE%B9%E7%B3%BB%E7%BB%9F.md)（融合欧阳思维笔记 310 节点 + 2026 编导课 179 页完整生产线）与速查手册 [methodology-cheatsheet.md](short-video-studio/references/methodology-cheatsheet.md) 为方法论总库，涵盖三有原则、账号四件套、三阶段内容配比、六大变现脚本、八大爆款元素与词根、进店理由人货场、25 宫格选题法、先定事件再写文案铁律、十大开篇技巧、中间六种结构（含编导八大问）、结尾一主一辅、晒过程/个人故事/说同城专项脚本及 17 种口播拍法。
   - **全程去黑话铁律**：内部选型严格遵循方法论并过「一句话说清发生了什么事」事件自检；但**向用户对话、输出 4 周日历、口播分镜、拍摄卡与周复盘时，严禁出现课件或专业黑话（如水桶流、漏斗流、5A、钩子、人设、A-roll/B-roll 等），全部转化为通俗大白话**；落实**第一眼画面信息密度大于第一句话**且**前 3 秒至少 2 次画面切换**。
3. **三入口与「最多打断 3 次」交互契约**
   - **入口 1（从实拍创作开始）**：主页四件套与用户定位梳理 → 大白话 4 周内容日历 → 先过事件自检再出大白话文案分镜 → 带 SVG 简笔示意与分镜头稳度指导的手机拍摄卡。
   - **入口 2（带素材进来）**：一键盘点零散实拍/口播视频 (`inventory_media.py`)，自动诊断帧率/音轨/锐度稳度，并用大白话反推最匹配的拍法类型、吸引角度与开头建议。
   - **入口 3（纯代码制片 `--mode code-video`）**：零实拍直出，按 `SPEC → STRUCTURE → 单 Chrome 批量渲染 → 自动化像素取证 VERIFY.md` 全闭环出片。
   - **仅 3 次交互打断**：① 确认选题 → ② 确认拍摄卡或素材 → ③ 确认锁定音轨与低清粗剪预览（支持**只改画面不动音轨**与**改词重锁音轨**两条分支）；其余剪辑细节自动推进并在交付时呈报《我替你做的决定》。
4. **「先音后画，以音定画」不可逆流水线**
   - Step 3 锁定纯语音 + BGM 节拍后生成 `03_audio_events.json` 骨架，后续一切切镜、J-cut、花字弹出与特效音均严格绑定音频事件帧。
5. **「风格谱 (Style Spectrum)」20 套标杆包装库与跨平台复刻**
   - 逐帧分析 20 条真实参考视频（累计 4034.8 秒 / 103,981 帧），提炼 6 大包装流派与 20 套量化「风格谱」（镜头分布、节拍对齐、字幕规范、33³ 3D LUT、开头结构、封面版式），支持 `--style-id` 一键注入流水线，或导出为 **Remotion / HyperFrames / 剪映专业版草稿**。

---

## 二、 仓库目录结构

```text
short-video-studio/
├── README.md                          # 本文档
├── install.sh                         # macOS / Linux 一键安装与环境初始化脚本
├── install.ps1                        # Windows PowerShell 一键安装与部署脚本
├── 启动HyperFrames工程.command         # macOS 双击一键启动 HyperFrames Studio 预览（支持新建风格谱工程）
├── 启动HyperFrames工程.bat             # Windows 双击一键启动 HyperFrames Studio 预览
├── BRIEF.md                           # 原始需求说明文档
├── 建立可循环的内容系统.md             # 方法论原稿留档
└── short-video-studio/                # Agent Skill 本体目录（安装后挂载至 ~/.claude/skills/short-video-studio 等）
    ├── SKILL.md                       # Skill 主控编排与硬性规则
    ├── references/                    # 方法论原文、公式速查、JSON Schema、平台安全区与资产授权清单
    ├── assets/                        # 内置 CC0 资产与 20 套「风格谱」跨平台复刻库
    │   ├── bgm/                       # CC0 程序化背景音乐（100 / 120 BPM）
    │   ├── sfx/                       # CC0 瞬态转场与提示音效
    │   ├── luts/                      # 基础 3D LUT (warm_store / clean_creator / high_contrast)
    │   ├── stickers/                  # 原创可商用 SVG 矢量贴纸
    │   ├── subtitle_templates/        # templates.json（4 套基础模板 + style_01~style_20 共 24 套）
    │   ├── remotion_templates/        # Remotion 根入口与代码补镜模板
    │   └── style_spectrum/            # 20 条参考视频「风格谱」全量资产库
    │       ├── STYLE_SPECTRUM_REPORT.md      # 20 条参考视频 9 维逐帧拆解总报告
    │       ├── style_spectrum_library.json   # 6 大流派母版 + 20 套风格谱主数据库
    │       ├── style_spectrum_raw.json       # 逐帧物理扫描原始统计数据
    │       ├── luts/                         # 20 套逆向 33³ 3D .cube LUT (style_01~20.cube)
    │       ├── keyframes/                    # 20 张 3×3 高清关键帧采样图谱 (*_contact.jpg)
    │       ├── remotion/                     # StyleSpectrumPresets.ts + StyleSpectrumPack.tsx
    │       ├── hyperframes/                  # style-spectrum-presets.css + style-spectrum-template.html
    │       └── jianying/                     # jianying_style_presets.json（剪映样式/调色/节奏预设）
    └── scripts/                       # 25 个 Python 确定性工程脚本
        ├── common.py                  # 公共工具模块（路径、venv、FFmpeg、字体、安全区与 JSON 读写）
        ├── check_and_setup.py         # 环境检测、隔离 venv 依赖安装与 CC0 资产生成
        ├── generate_builtin_assets.py # 程序化生成零版权风险 CC0 BGM/SFX/LUT/贴纸
        ├── analyze_style_spectrum.py  # 逐帧特征提取引擎（镜头/BPM/对齐率/色彩LUT/OCR字幕/图谱）
        ├── build_style_spectrum_assets.py # 合并视觉精校并构建主库与三端预设文件
        ├── apply_style_spectrum.py    # 风格谱一键导出 (Remotion/HyperFrames/剪映) 与流水线应用
        ├── inventory_media.py         # 入口 2：素材盘点与选题公式反推
        ├── generate_shot_card.py      # 入口 1：生成带 SVG 简笔构图的拍摄任务卡
        ├── generate_code_broll.py     # 代码生成叙事补镜（动效文字 / 数据柱状图 / 流程图）
        ├── step1_normalize.py         # Step 1：VFR 转 30fps CFR (1080x1920) 与 48kHz WAV 提取
        ├── step2_transcribe.py        # Step 2：逐字时间戳对齐（无口播自动切换 Edge-TTS 配音）
        ├── step3_lock_audio.py        # Step 3：去废话停顿、对齐强拍并锁定音轨事件
        ├── step4_build_edl.py         # Step 4：以音定画生成 EDL（J-cut、前 3 秒多切点、自动补镜）
        ├── step5_subtitles.py         # Step 5：逐字关键词高亮字幕（支持基础 4 套 + style_01~20）
        ├── step6_packaging.py         # Step 6：SFX 先行与 ±1 帧同步花字贴纸（含预算上限控制）
        ├── step7_color_grade.py       # Step 7：基础曝光白平衡校色 + 3D .cube LUT
        ├── step8_mix_audio.py         # Step 8：人声降噪 + BGM 自动闪避 + -14.0 LUFS 闭环混音
        ├── step9_title_cover.py       # Step 9：爆款标题与高密度 9:16 首帧封面生成
        ├── render_mp4.py              # 出片 1：本地合成 MP4（支持粤语缺字回退与超宽字幕缩号防重叠）
        ├── export_jianying.py         # 出片 2：剪映全风格可编辑工程导出（FCPXML 1.10 + Alpha MOV + SRT/LUT）
        ├── export_hyperframes.py      # 出片 3：HyperFrames Studio 多轨可编辑工程 + 双击启动器导出
        ├── qa_inspector.py            # 9 项强制自动质检 + 对标视频逐秒对比
        ├── content_log_review.py      # 发布数据沉淀与每周一页结构化复盘
        └── run_pipeline.py            # 端到端一键编排入口（full / lock-preview / reedit-visuals / relock-voice，支持 --style-id）
```

---

## 三、 30 秒跨平台一键安装

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

安装脚本会自动完成以下工作：
1. 检查 `ffmpeg`、`ffprobe`、`node`、`uv` 与开源可商用字体环境；
2. 创建独立虚拟环境 `~/.short-video-studio/venv`（安装 `pillow`、`numpy`、`soundfile`、`edge-tts` 等依赖）；
3. 确定性生成全套 CC0 BGM、音效、3D LUT 与 SVG 贴纸；
4. 将 `short-video-studio/` 软链/部署至 `~/.claude/skills/short-video-studio`（Windows 为 `%USERPROFILE%\.claude\skills\short-video-studio`）；macOS/Linux 若检测到 `~/.gemini/config/skills` 存在，还会同步创建软链。

---

## 四、 使用指南

### 方式 1：在 Claude Code 中直接对话触发
安装完成后打开任意 Claude Code 会话，直接用自然语言说明诉求即可自动触发 `short-video-studio`：
- **带素材进场**：“帮我把 `./raw_media` 里的探店口播和空镜素材盘点一下，用门店轨剪一条宣传成片。”
- **从零起号策划**：“我想做一个餐饮门店短视频账号，帮我制定 4 周发片计划并先出第一条的大白话拍摄卡。”
- **复盘迭代**：“这是这条视频发出去后的播放、3 秒留存和私信咨询数，帮我记入周复盘。”

### 方式 2：命令行单独调用各工程工具

> 建议通过 `~/.short-video-studio/venv/bin/python` 运行，脚本内置 `maybe_reexec_in_venv()` 也会在普通 `python3` 调用时自动切换至隔离环境。

1. **生成带简笔示意的拍摄任务卡（入口 1）**
   ```bash
   python3 short-video-studio/scripts/generate_shot_card.py \
     --output ./my_video/shot_card.md \
     --title "花300万砸200平纯手工厨房" \
     --track store
   ```
2. **素材全量盘点与反推选题（入口 2）**
   ```bash
   python3 short-video-studio/scripts/inventory_media.py \
     --input-dir ./my_video/raw_media \
     --output ./my_video/pipeline/00_inventory.json \
     --track store
   ```
3. **代码生成叙事补镜（缺少 B-Roll 时）**
   ```bash
   python3 short-video-studio/scripts/generate_code_broll.py \
     --output ./my_video/broll_data.mp4 \
     --mode data_chart \
     --title "200平纯手工明档厨房" \
     --items-json '[{"label":"纯手工现点现做","value":"200平 / 34%","ratio":0.34},{"label":"拒绝预制菜占比","value":"100%","ratio":1.0}]'
   ```
4. **运行流水线（四大执行模式）**
   - **打断 #3 预览**（跑至锁定音轨 + 粗剪预览 `output_preview.mp4`）：
     ```bash
     python3 short-video-studio/scripts/run_pipeline.py \
       --project-dir ./my_video --track store --mode lock-preview
     ```
   - **只改画面（音轨 `03_*` 一比特不改，仅重排画面）**：
     ```bash
     python3 short-video-studio/scripts/run_pipeline.py \
       --project-dir ./my_video --track store --mode reedit-visuals --prefer-broll-only --lut high_contrast.cube
     ```
   - **改口播词（重锁音轨并驱动重排全部镜头与字幕）**：
     ```bash
     python3 short-video-studio/scripts/run_pipeline.py \
       --project-dir ./my_video --track creator --mode relock-voice --script-json ./new_script.json
     ```
   - **端到端全链路出片 + 剪映草稿 + 9 项强检**（支持 `--style-id` 套用 20 套参考风格谱及 `--benchmark` 逐秒对比）：
     ```bash
     python3 short-video-studio/scripts/run_pipeline.py \
       --project-dir ./my_video --track store --mode full --style-id style_02 --benchmark ./benchmark.mp4
     ```
5. **独立校验与导出**
   - 自动质检：`python3 short-video-studio/scripts/qa_inspector.py --pipeline-dir ./my_video/pipeline`
   - 剪映草稿：`python3 short-video-studio/scripts/export_jianying.py --pipeline-dir ./my_video/pipeline`
   - HyperFrames 工程：`python3 short-video-studio/scripts/export_hyperframes.py --pipeline-dir ./my_video/pipeline`
6. **发布数据沉淀与每周一页复盘**
   ```bash
   python3 short-video-studio/scripts/content_log_review.py \
     --log-dir ./my_video/content-log \
     --add-json '{"title":"新店开门","track":"store","script_type":"给进店理由","viral_element":"算钱对比","hook_type":"数字开篇","views":18500,"retention_3s":0.46,"completion_rate":0.28,"dm_inquiries":37}'
   ```

### 方式 3：出片后期导入剪映与 HyperFrames 双击启动指南

#### 1) 怎么导入剪映专业版（兼容 6.0 ~ 11.7.0+，一键还原全风格多轨）
由于剪映专业版 `6.0+`（含 macOS `11.7.0`）对内部草稿 `draft_info.json` 启用了私有加密，**切勿直接复制旧版明文草稿文件夹去草稿箱**。流水线出片时已在 `<项目>/pipeline/jianying_draft/` 中自动生成剪映官方原生支持的多轨工程包：

1. **开启导入开关（仅首次需要）**：打开剪映专业版 → 右上角齿轮「设置」→「全局设置」→ 确认开启「导入工程」。
2. **一键导入全部多轨内容**：在剪映首页「本地草稿」右上角点击 `...` →「**导入工程**」（或新建空白草稿后直接把文件拖进时间线），选中目录中的 **`<project_name>.fcpxml`**。
   - 导入后自动生成以下完整可编辑轨道，无需逐张手拖图片：
     - **主视频轨 (`V1`)**：全套精剪调色分镜（`seg_xxx.mp4`）；
     - **上层透明包装轨 (`alpha_layers/*.mov`)**：按顺序无重叠挂好对应「风格谱」的分屏圆角画框、知识卡片、顶部步骤条、Founder 徽章与语义花字（每块均可独立拖动、隐藏开关或删除）；
     - **顶层双色字幕轨 (`alpha_layers/sub_*.mov`)**：100% 保留商业字体、关键词放大高亮、描边底框与抖音安全区位置；
     - **三轨独立解耦音频 (`A1–A3`)**：`纯净人声干音`、`专属 BGM`、`切镜音效 SFX`，可任意单独静音或调音量。
3. **仅需修改某句错别字 / 自定义全文字体时**：在时间线最顶层删掉对应那一句的双色字幕块，然后将同目录下的 `<project_name>.srt`（或 `.ass`）拖入剪映「文本 → 本地字幕」即可按纯文本自由改字；如需二次微调滤镜强度，可在「调节 → 滤镜 → 导入 LUT」加载同目录的 `*.cube` 文件。

#### 2) 怎么用最简单的方式启动 HyperFrames 工程（双击直开，完整还原全部流水线工程）
每次 `run_pipeline.py` 剪辑包装完成后，会自动在 `<项目>/pipeline/` 下同步产出 **两份可编辑工程**：`jianying_draft/`（剪映包）与 **`hyperframes_draft/`**（HyperFrames Studio 工程，含全部分镜、风格画框/卡片/花字、原生 HTML 双色高亮字幕与 3 轨独立音频，**并直接附带双击启动器**）。前置仅需 `Node.js >= 22` 与 `FFmpeg`（macOS 可运行 `brew install node ffmpeg`）。

- **场景 A（直接打开已做好的成片工程，例如 `style_12`）**：
  - 进入 `<项目>/pipeline/hyperframes_draft/`，直接双击里面的 **[启动HyperFrames工程.command](启动HyperFrames工程.command)**（macOS）或 **[启动HyperFrames工程.bat](启动HyperFrames工程.bat)**（Windows），3 秒内自动打开浏览器 HyperFrames Studio 实时时间线预览，可修改文案/图层后直接点击重新渲染。
  - 或者双击仓库根目录的 `启动HyperFrames工程.command` → 直接点选 `<项目>`、`<项目>/pipeline` 或 `<项目>/pipeline/hyperframes_draft`（甚至 `TestinVideo`），若尚无 `hyperframes_draft/` 还会自动帮你转出后直接打开！
- **场景 B（套用「20 套风格谱」新建示例工程或新建空白工程）**：
  - 双击仓库根目录 `启动HyperFrames工程.command` 后，选 `[2]` 输入风格编号（回车默认 `style_14`）或选 `[3]` 新建空白竖屏工程，即可立即打开 Studio 预览。

---

## 五、 9 项强制自动质检红线 (`qa_inspector.py`)

每次成片交付前自动运行，**任一项不达标即阻断交付（退出码 2）**并输出 `10_qa_report.md`：

| 序号 | 质检项目 | 硬性通过标准 |
| :---: | :--- | :--- |
| 1 | 综合响度（EBU R128） | `-14.0 ± 1.0 LUFS`（配合 `alimiter` 两步闭环稳控） |
| 2 | 黑帧检测 | 全片检出 `0` 处黑帧（短镜自动 `tpad` 末帧定格防黑屏） |
| 3 | 超长静音段 | 连续超过 `0.8s` 的静音段为 `0` 段 |
| 4 | 音画同步与总时长差 | 同期声锁定且音视频流时长差 `≤ 1.0 帧`（`33.3ms` @ 30fps） |
| 5 | 字幕与锁定语音偏差 | 字幕时间点与语音句边界最大偏差 `≤ 1.0 帧` |
| 6 | 切点对齐音频事件 | 镜头切点对齐 `03_audio_events.json` 偏差 `≤ 1 帧`（支持 2–4 帧合法 J-cut） |
| 7 | 平台 UI 安全区与包装对齐 | 字幕/花字/贴纸均位于安全区 `x∈[60,880], y∈[160,1480]` 内且包装对齐误差 `≤ 1 帧` |
| 8 | 镜头时长分布 | 全片单镜时长位于 `0.30s – 8.0s` 区间，避免碎镜头或死镜头 |
| 9 | 黄金前 3 秒画面变化 | 前 `3.0s` 内画面切换次数 `≥ 2` 次，且均对齐音频事件 |

> 实战增强亮点：`render_mp4.py` 已针对方言/生僻字与高密度台词内置**单字缺字检测与系统字体自动回退**（如粤语常见字 `係/咗/嘅/冇/啲/喺/嗰` 自动回退至 `STHeiti` / `Hiragino Sans GB`）以及**超宽字幕自适应缩调字号**机制，彻底杜绝豆腐块 `▯` 与字形重叠。

---

## 六、 可商用内置资产与字体合规声明

详尽授权溯源见 [assets-attribution.md](short-video-studio/references/assets-attribution.md)：
- **背景音乐 & 音效**：由 `generate_builtin_assets.py` 纯数字信号合成（48kHz 16-bit WAV），**CC0 1.0 公共领域可商用**。
- **SVG 贴纸 & 3D LUTs & Remotion/代码补镜模板**：项目原创绘制与生成，**CC0 1.0 / MIT 可商用**。
- **字幕字体**：优先加载 **思源黑体 / 思源宋体 / 得意黑（SIL OFL 1.1）** 及 **阿里妈妈数黑体（免费可商用）**，并在缺失特定生僻/方言字符时单字回退以保障成片视觉完整性。
- **风格谱 3D LUT 与三端预设**：`assets/style_spectrum/luts/` 下的 20 套 `33³` `.cube` LUT 与 Remotion/HyperFrames/剪映模板均由本项目脚本原创生成，**CC0 1.0 / MIT 可商用**。

---

## 七、 参考包装「风格谱 (Style Spectrum)」跨平台复刻库

已基于 **20 条**参考视频（竖屏 9:16 13 条 / 横屏 16:9 7 条，累计 **4034.8 秒 / 67.2 分钟**，全帧扫描 **103,981 帧**）完成逐帧解析与视觉图谱复核，提炼出 **6 大包装流派母版**与 **20 套可直接复刻的「风格谱」资产**。完整逐条 9 维拆解（含关键帧图谱预览）见 [STYLE_SPECTRUM_REPORT.md](short-video-studio/assets/style_spectrum/STYLE_SPECTRUM_REPORT.md)。

### 1. 六大包装流派母版 (Archetypes)

| 流派 ID | 流派名称 | 包含样本 | 适用场景 | 色彩与字幕特征 | 节奏与对齐公式 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `arch_01_tech_ui_console` | **暗黑科技 / AI 工作流控制台风** | `style_01`, `style_09`, `style_10`, `style_15`, `style_17` | AI 工具测评、Agent 工作流演示、SaaS 产品教程、极客硬核拆解 | 暗黑低亮度基底 (`Y=36~84`) + 琥珀金/科技蓝强调色 + 克制小号居中底字 (`36~46px`) | 顶部章节步骤条常驻 + 内部 UI/卡片高频切换 + 强事件对齐 (`60%~90%`) |
| `arch_02_high_contrast_story` | **强反差财经 / 深度人物叙事拼贴风** | `style_03`, `style_16`, `style_19` | 商业案例揭秘、创始人深度访谈、剪辑思维干货、高信息量长视频 | 中性电影对比调或暖肤色 + 亮黄/翠绿/报刊米白强视觉冲击块 | 高速切镜 (`med 1.25s~2.20s`，全片 `118~290` 镜) + 高频音效 (`63~90 SFX/min`) |
| `arch_03_editorial_matte_depth` | **高级感杂志排版 / 人物前景遮罩风** | `style_11`, `style_12`, `style_18`, `style_20` | 审美/设计评论、个人IP方法论教学、Notion/极简白底知识拆解 | 极净白底或深红/书墙反差色 + 少而精的无衬线黑体层级 (`≤2` 种字体) | 前 3 秒人物遮罩大字或实物定格抓眼，中段结构化清单与快切案例交替 |
| `arch_04_punch_talking_head` | **高密口播卡点 / 干货大字冲击风** | `style_02`, `style_04`, `style_07`, `style_14` | 创始人认知口播、实体商家/建材家居老板IP、认知颠覆与痛点科普 | 暖肤色高透或暖琥珀书桌调 + 大号粗黑体 (`48~74px`) 亮黄变色放大 | 口播句间紧剪 + 推拉跳剪 + 卡片弹窗音效卡点 (对齐率 `79%~92%`) |
| `arch_05_cinema_slow_space` | **电影感空间漫游 / 人文宋体金句风** | `style_06`, `style_08`, `style_13` | 高端室内设计/建筑展厅实拍、心理学/哲学金句、深度真实生活随拍访谈 | 暖木质感色温或冷青电影灰调；思源宋体 (`SourceHanSerifCN`) 或极简细黑投影 | 尊重呼吸感与空间尺度（空镜缓推或随拍微跳剪），少音效或零花字 |
| `arch_06_paper_stopmotion` | **纸张拼贴定格 / 工程图解科普风** | `style_05` | 收纳改造、家居尺寸科普、产品结构拆解、强动手感实物教程 | 低饱和褶皱白纸底纹 (`sat=9.1%`) + 暖木纹理 + 红线尺寸标尺 | 前 3 秒手部模型定格 3 连切，全片依靠纸片元件位移与定格节奏驱动 |

### 2. 20 套「风格谱」速查总表 (`style_01` ~ `style_20`)

| ID | 风格名称 | 画幅 / 帧率 | 时长 | 总镜数 (硬切/跳剪) | 中位镜长 (`<1s`快切) | 事件对齐 (`≤1f`/`≤3f`) | BPM (响度) | 音效密度 | 字幕字号 / 位置 / 动效 | LUT 与调性 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
| `style_01` | 复古胶片控制台 · AI 模板展示风 | 9:16 / 30p | 150.0s | 16 (8/7) | 6.55s (0.0%) | 26.7% / 53.3% | 103.4 (-12.6 LUFS) | 59.6/min | 40px · Y=0.80 · `fade_slide_up` | `style_01.cube` (深沉低饱和质感调) |
| `style_02` | 极简硬核口播 · 书法炸裂封面风 | 9:16 / 30p | 56.1s | 35 (23/11) | 1.17s (37.1%) | 44.1% / 79.4% | 157.9 (-11.1 LUFS) | 70.6/min | 74px · Y=0.78 · `hard_cut` | `style_02.cube` (均衡自然高通透调) |
| `style_03` | 十亿访谈长片 · 顶部时间节点进度条风 | 16:9 / 24p | 552.7s | 290 (200/89) | 1.58s (23.8%) | 27.3% / 68.9% | 111.1 (-14.2 LUFS) | 80.7/min | 46px · Y=0.89 · `hard_cut` | `style_03.cube` (暖色高饱和门店/人物调) |
| `style_04` | 质感拟物纸片 · AI 框架科普风 | 9:16 / 24p | 46.9s | 12 (10/1) | 4.08s (16.7%) | 36.4% / 81.8% | 84.5 (-13.2 LUFS) | 75.4/min | 48px · Y=0.76 · `hard_cut` | `style_04.cube` (暖色高饱和门店/人物调) |
| `style_05` | 皱纹纸拼贴定格 · 工程图解科普风 | 9:16 / 24p | 169.9s | 11 (5/5) | 1.50s (36.4%) | 30.0% / 80.0% | 96.8 (-10.9 LUFS) | 78.1/min | 48px · Y=0.88 · `fade_slide_up` | `style_05.cube` (暖色高饱和门店/人物调) |
| `style_06` | 10秒情感金句 · 衬线宋体剪影大片风 | 9:16 / 30p | 10.0s | 4 (3/0) | 2.32s (25.0%) | 0.0% / 66.7% | 146.3 (-14.4 LUFS) | 72.1/min | 78px · Y=0.69 · `hard_cut` | `style_06.cube` (均衡自然高通透调) |
| `style_07` | 商业导师书桌 · 暗金波点全屏花字风 | 9:16 / 24p | 126.6s | 27 (16/10) | 4.08s (22.2%) | 57.7% / 80.8% | 153.8 (-15.0 LUFS) | 75.8/min | 64px · Y=0.83 · `pop_spring` | `style_07.cube` (深沉低饱和质感调) |
| `style_08` | 真实生活随拍 · 文艺双语宋体微跳剪风 | 9:16 / 24p | 278.2s | 29 (7/21) | 5.33s (13.8%) | 46.4% / 85.7% | 107.1 (-11.3 LUFS) | 89.1/min | 50px · Y=0.67 · `fade_slide_up` | `style_08.cube` (清透冷中性/极简白调) |
| `style_09` | 电影剪辑工作站 · 3D 胶片墙极客风 | 16:9 / 30p | 151.3s | 24 (20/3) | 2.12s (20.8%) | 30.4% / 60.9% | 120.0 (-11.2 LUFS) | 69.0/min | 36px · Y=0.90 · `hard_cut` | `style_09.cube` (深沉低饱和质感调) |
| `style_10` | 暗黑幕后工作室 · 右下固定画中画教程风 | 16:9 / 25p | 229.3s | 11 (8/2) | 17.76s (9.1%) | 10.0% / 60.0% | 93.8 (-13.7 LUFS) | 78.2/min | 44px · Y=0.91 · `hard_cut` | `style_10.cube` (深沉低饱和质感调) |
| `style_11` | 人物前景遮罩巨字 · 高级感杂志教程风 | 16:9 / 24p | 156.3s | 90 (80/9) | 1.35s (35.6%) | 34.8% / 77.5% | 130.4 (-14.9 LUFS) | 70.2/min | 38px · Y=0.90 · `hard_cut` | `style_11.cube` (均衡自然高通透调) |
| `style_12` | Apple/Notion 极简白底 · 人像抠像卡片风 | 9:16 / 24p | 224.8s | 4 (0/3) | 65.81s (25.0%) | 66.7% / 100.0% | 146.3 (-14.5 LUFS) | 30.4/min | 54px · Y=0.77 · `hard_cut` | `style_12.cube` (均衡自然高通透调) |
| `style_13` | 建筑空间漫游 · 电影感慢调奢享风 | 9:16 / 30p | 473.4s | 34 (28/5) | 1.57s (11.8%) | 27.3% / 72.7% | 95.2 (-14.9 LUFS) | 76.9/min | 46px · Y=0.72 · `hard_cut` | `style_13.cube` (深沉低饱和质感调) |
| `style_14` | 毛玻璃卡片叠映 · 居家创始人信任风 | 9:16 / 24p | 259.3s | 25 (16/8) | 8.71s (0.0%) | 37.5% / 91.7% | 120.0 (-12.9 LUFS) | 94.6/min | 50px · Y=0.74 · `fade_slide_up` | `style_14.cube` (均衡自然高通透调) |
| `style_15` | AI Agent 流程导航 · 工牌/账单小票风 | 16:9 / 24p | 123.3s | 11 (10/0) | 11.71s (18.2%) | 80.0% / 90.0% | 162.2 (-13.0 LUFS) | 72.0/min | 40px · Y=0.90 · `pop_spring` | `style_15.cube` (暗黑高对比科技调) |
| `style_16` | 商业悬疑纪实 · 撕纸拍立得拼贴风 | 9:16 / 30p | 313.8s | 118 (95/22) | 2.20s (10.2%) | 37.6% / 76.1% | 109.1 (-12.3 LUFS) | 89.7/min | 48px · Y=0.84 · `hard_cut` | `style_16.cube` (深沉低饱和质感调) |
| `style_17` | 深蓝 SaaS 演示 · 右下圆角画中画风 | 9:16 / 24p | 79.8s | 15 (8/6) | 1.75s (26.7%) | 21.4% / 78.6% | 93.8 (-14.5 LUFS) | 75.2/min | 46px · Y=0.70 · `hard_cut` | `style_17.cube` (暗黑高对比科技调) |
| `style_18` | 白板方法论 · 手写字纸片反差条风 | 9:16 / 24p | 190.1s | 39 (19/19) | 2.71s (12.8%) | 44.7% / 71.1% | 111.1 (-14.0 LUFS) | 49.5/min | 64px · Y=0.75 · `hard_cut` | `style_18.cube` (深沉低饱和质感调) |
| `style_19` | 专业剪辑大师课 · 顶部细条章节 + 绿块冲击风 | 16:9 / 24p | 233.1s | 137 (122/14) | 1.25s (35.8%) | 44.1% / 82.4% | 146.3 (-15.3 LUFS) | 63.3/min | 46px · Y=0.90 · `hard_cut` | `style_19.cube` (暗黑高对比科技调) |
| `style_20` | 模型盒悬念开篇 · 书墙双语毛玻璃金句风 | 16:9 / 24p | 210.0s | 88 (66/21) | 1.27s (38.6%) | 27.6% / 75.9% | 90.9 (-14.7 LUFS) | 89.2/min | 42px · Y=0.87 · `fade_slide_up` | `style_20.cube` (深沉低饱和质感调) |

### 3. 跨平台复刻命令行速查

```bash
# 1. 列出 6 大流派母版与 20 套风格谱摘要
python3 short-video-studio/scripts/apply_style_spectrum.py --list

# 2. 在流水线中直接套用 style_02 出片（自动绑定字幕模板 + 3D LUT + J-cut）
python3 short-video-studio/scripts/run_pipeline.py --project-dir ./my_video --track creator --mode full --style-id style_02

# 3. 一键导出至 剪映专业版样板草稿 / Remotion Props / 可双击启动的 HyperFrames 工程目录
python3 short-video-studio/scripts/apply_style_spectrum.py --style-id style_16 \
  --title "360亿大佬的真实困局" \
  --subtitle "看懂商业底层的现金流真相" \
  --export-jianying-template ./out_style_16/jianying_draft \
  --export-remotion-props ./out_style_16/remotion_props.json \
  --export-hyperframes ./out_style_16_hyperframes

# 4. 针对自己的参考视频库重新逐帧提取「风格谱」并更新资产
python3 short-video-studio/scripts/analyze_style_spectrum.py --input-dir /path/to/reference_videos --workers 6
python3 short-video-studio/scripts/build_style_spectrum_assets.py
```

- **Remotion 组件入口**：在 [Root.tsx](short-video-studio/assets/remotion_templates/Root.tsx) 中已注册 `StyleSpectrumShowcase`，并可单独复用 [StyleSpectrumPack.tsx](short-video-studio/assets/style_spectrum/remotion/StyleSpectrumPack.tsx) 中的 `StyleCoverCard`、`StyleSubtitleBar`、`StyleChapterBar`、`StyleGraphicCard`。
- **HyperFrames 母版入口**：打开 [style-spectrum-template.html](short-video-studio/assets/style_spectrum/hyperframes/style-spectrum-template.html)`?style=style_02` 即可按风格 ID 切换预览；使用 `--export-hyperframes <工程目录>` 会直接生成含 `index.html`、`hyperframes.json` 与 `启动HyperFrames工程.command` 的完整工程（双击即打开 Studio，也可按 `.html` 结尾仅导出单文件）。
- **剪映专业版导出与预设库**：全片剪映可编辑工程优先由流水线 `export_jianying.py` 产出（`*.fcpxml` + `alpha_layers/*.mov` + `*.srt` + `*.cube`），风格参数预设位于 `short-video-studio/assets/style_spectrum/jianying/jianying_style_presets.json` 及 `short-video-studio/assets/style_spectrum/luts/style_01.cube` ~ `style_20.cube`。
