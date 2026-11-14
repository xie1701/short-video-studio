# short-video-studio 短视频制片工作室

> **面向零拍摄与零剪辑基础用户的端到端 Claude Code Agent Skill**：以 **「先音后画，以音定画」** 为核心工程铁律，打通「账号定位与 4 周 5A 日历策划 → 带简笔构图示意的拍摄任务卡 / 素材盘点反推 → 9 步确定性音视频后期流水线 → 9 项客观自动质检 → 本地 1080×1920 高清成片与剪映专业版草稿双出片 → 发布后数据周复盘」全流程。

---

## 一、 核心设计原则

1. **首次对话「先猜后确认」双轨制**
   - **门店轨（Store Track）**：服务实体店老板 / 本地生活商家，以**评论区咨询、私信、到店核销**为核心目标。
   - **博主轨（Creator Track）**：服务个人创作者 / 知识或泛娱乐 IP，以**3 秒留存率、完播率、净涨粉**为核心目标。
2. **唯一选题与文案方法论（强制溯源标注）**
   - 严格以 [建立可循环的内容系统.md](short-video-studio/references/%E5%BB%BA%E7%AB%8B%E5%8F%AF%E5%BE%AA%E7%8E%AF%E7%9A%84%E5%86%85%E5%AE%B9%E7%B3%BB%E7%BB%9F.md) 与速查手册 [methodology-cheatsheet.md](short-video-studio/references/methodology-cheatsheet.md) 为唯一方法论依据。
   - 所有脚本强制显式标注 **5A 层级、阶段配比、客户五层问题、漏斗流/水桶流公式、6 大话题、8 大钩子、10 大开篇技巧、6 大人物切入与 4 大结尾**；落实**第一眼画面信息密度大于第一句话**且**前 3 秒至少 2 次场景切换**。
3. **双入口与「最多打断 3 次」交互契约**
   - **入口 1（从创作开始）**：定位梳理 → 4 周 5A 内容日历 → 逐条文案分镜 → 带 SVG 简笔示意的手机拍摄卡。
   - **入口 2（带素材进来）**：一键盘点零散实拍/口播视频 (`inventory_media.py`)，自动诊断 VFR 帧率/音轨状况并反推最匹配的 5A 选题公式。
   - **仅 3 次交互打断**：① 确认选题 → ② 确认拍摄卡或素材 → ③ 确认锁定音轨与低清粗剪预览（支持**只改画面不动音轨**与**改词重锁音轨**两条分支）；其余剪辑细节自动推进并在交付时呈报《我替你做的决定》。
4. **「先音后画，以音定画」不可逆流水线**
   - Step 3 锁定纯语音 + BGM 节拍后生成 `03_audio_events.json` 骨架，后续一切切镜、J-cut、花字弹出与特效音均严格绑定音频事件帧。

---

## 二、 仓库目录结构

```text
short-video-studio/
├── README.md                          # 本文档
├── install.sh                         # macOS / Linux 一键安装与环境初始化脚本
├── install.ps1                        # Windows PowerShell 一键安装与部署脚本
├── BRIEF.md                           # 原始需求说明文档
├── 建立可循环的内容系统.md             # 方法论原稿留档
└── short-video-studio/                # Claude Code Skill 本体目录（安装后挂载至 ~/.claude/skills/short-video-studio）
    ├── SKILL.md                       # Skill 主控编排与硬性规则
    ├── references/                    # 方法论原文、公式速查、JSON Schema、平台安全区与资产授权清单
    ├── assets/                        # 内置 CC0 资产（BGM、SFX、3D LUT、SVG 贴纸、字幕/Remotion 模板）
    └── scripts/                       # 17 个 Python 确定性工程脚本
        ├── check_and_setup.py         # 环境检测、隔离 venv 依赖安装与 CC0 资产生成
        ├── generate_builtin_assets.py # 程序化生成零版权风险 CC0 BGM/SFX/LUT/贴纸
        ├── inventory_media.py         # 入口 2：素材盘点与选题公式反推
        ├── generate_shot_card.py      # 入口 1：生成带 SVG 简笔构图的拍摄任务卡
        ├── generate_code_broll.py     # 代码生成叙事补镜（动效文字 / 数据柱状图 / 流程图）
        ├── step1_normalize.py         # Step 1：VFR 转 30fps CFR (1080x1920) 与 48kHz WAV 提取
        ├── step2_transcribe.py        # Step 2：逐字时间戳对齐（无口播自动切换 Edge-TTS 配音）
        ├── step3_lock_audio.py        # Step 3：去废话停顿、对齐强拍并锁定音轨事件
        ├── step4_build_edl.py         # Step 4：以音定画生成 EDL（J-cut、前 3 秒多切点、自动补镜）
        ├── step5_subtitles.py         # Step 5：逐字关键词高亮字幕（平台安全区内）
        ├── step6_packaging.py         # Step 6：SFX 先行与 ±1 帧同步花字贴纸（含预算上限控制）
        ├── step7_color_grade.py       # Step 7：基础曝光白平衡校色 + 3D .cube LUT
        ├── step8_mix_audio.py         # Step 8：人声降噪 + BGM 自动闪避 + -14.0 LUFS 闭环混音
        ├── step9_title_cover.py       # Step 9：爆款标题与高密度 9:16 首帧封面生成
        ├── render_mp4.py              # 出片 1：本地合成 MP4（支持粤语缺字回退与超宽字幕缩号防重叠）
        ├── export_jianying.py         # 出片 2：剪映专业版草稿导出（异常自动降级保护）
        ├── qa_inspector.py            # 9 项强制自动质检 + 对标视频逐秒对比
        ├── content_log_review.py      # 发布数据沉淀与每周一页结构化复盘
        └── run_pipeline.py            # 端到端一键编排入口（full / lock-preview / reedit-visuals / relock-voice）
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
4. 将 `short-video-studio/` 软链/部署至 `~/.claude/skills/short-video-studio`（Windows 为 `%USERPROFILE%\.claude\skills\short-video-studio`）。

---

## 四、 使用指南

### 方式 1：在 Claude Code 中直接对话触发
安装完成后打开任意 Claude Code 会话，直接用自然语言说明诉求即可自动触发 `short-video-studio`：
- **带素材进场**：“帮我把 `./raw_media` 里的探店口播和空镜素材盘点一下，用门店轨剪一条宣传成片。”
- **从零起号策划**：“我想做一个餐饮门店短视频账号，帮我制定 4 周 5A 内容日历并先出第一条的拍摄卡。”
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
   - **端到端全链路出片 + 剪映草稿 + 9 项强检**（可选对标逐秒对比）：
     ```bash
     python3 short-video-studio/scripts/run_pipeline.py \
       --project-dir ./my_video --track store --mode full --benchmark ./benchmark.mp4
     ```
5. **独立校验与导出**
   - 自动质检：`python3 short-video-studio/scripts/qa_inspector.py --pipeline-dir ./my_video/pipeline`
   - 剪映草稿：`python3 short-video-studio/scripts/export_jianying.py --pipeline-dir ./my_video/pipeline`
6. **发布数据沉淀与每周一页复盘**
   ```bash
   python3 short-video-studio/scripts/content_log_review.py \
     --log-dir ./my_video/content-log \
     --add-json '{"title":"新店开门","track":"store","formula":"水桶流","hook_type":"数字冲击","views":18500,"retention_3s":0.46,"completion_rate":0.28,"inquiries":37}'
   ```

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
