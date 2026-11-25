# AGENTS.md · 短视频工作台协同开发宪法

欢迎进入 `shortVideo-CCstudio` 多 AI Agent 协同工作区。本仓库采用 **单总控（统筹/派单/终审/合并）+ 多专业执行手（独立 Worktree 并行编码）** 模式。

## 一、 团队分工
- **Antigravity（总架构师 / 终审官）**：拆解任务、定义接口契约、审阅 `git diff`、合并入 `main`、执行全量回归、端到端渲染成片并抽帧视觉终验。
- **Codex（执行手 A）**：主力代码改造、状态机与剪辑算法实现、边界修复与回归单测。
- **DeepSeek Harness（执行手 B）**：信号/视觉检测算法、QA 质检护栏、规则提取与多风格平行迁移。

## 二、 五条绝对红线（违反任意一条，闸门直接拦截打回）
1. **先查分支，严禁碰 `main`**：开工前先执行 `git branch --show-current`。必须处于任务单指定的 `agent/<owner>/<task_id>` 分支与独立 Worktree；若在 `main` 分支立即停手报错。
2. **严格遵守 `allowed_files` 白名单**：只允许创建/修改任务单 Frontmatter 中 `allowed_files` 与 `report_path` 列出的文件。确需修改白名单之外的文件时，**立即停手在回复中申请扩权**，绝不私自越权改动。
3. **零隐私泄漏、零黑话、零跨风格污染**：
   - 严禁在代码、注释、文档、测试中写入任何个人用户家目录绝对路径或本地参考草稿名。路径一律通过 `Path(__file__).resolve()`、环境变量 `$HOME` 或 `common.ASSETS_DIR` 动态拼装。
   - 面向用户/商家的话术文案禁止堆砌内部术语，须使用大白话。
   - 任何单一风格的修改不得破坏其他风格的既有断言与隔离规则。
4. **KISS 与测试真实性原则**：
   - 坚持第一性原理与极简实现，优先复用仓库既有工具函数（如 `common.py` 中的 `run_cmd` / `ffmpeg_binary`），禁止过度防御与重复逻辑。
   - **严禁通过删除、跳过或放宽现有测试断言** 来换取通过。
5. **闸门通过后方可交卷**：
   - 交卷前必须在本 Worktree 根目录运行：
     `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate <TASK_ID>`
   - 看见 `=== GATE PASS: <TASK_ID> ===` 后，按 `TEAM/REPORT_TEMPLATE.md` 写入对应 `TEAM/reports/...` 回执，并在本分支执行一次本地 `git add` + `git commit`（**禁止 `git push`**）。

## 三、 工程环境与架构速览
- **Python 解释器**：`"$HOME/.short-video-studio/venv/bin/python"`（禁止依赖系统裸 `python3`）。
- **核心流水线目录**：`short-video-studio/scripts/`
  - `step1..step3`：素材盘点、音频对齐、语音事件时间戳（`03_audio_events.json`）。
  - `step4_build_edl.py`：选镜、A/B 切换与 EDL 排期（`04_edl.json`）。
  - `step5_subtitles.py` / `step6_packaging.py` / `kinetic_choreographer.py`：字幕、花字舞台、0ms 音效咬合。
  - `step7..step9` / `render_mp4.py` / `export_jianying_native.py`：调色、混音、合成与多端导出。
  - `qa_inspector.py` / `independent_verifier.py`：白盒 + 黑盒自动化质检（`10_qa_report.json`）。
- **全量回归指令（总控合并时跑）**：`"$HOME/.short-video-studio/venv/bin/python" -m unittest discover -s tests -v`
