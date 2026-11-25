# TEAM/BOARD.md · 多 AI Agent 协同看板

> 维护者：Antigravity（总控架构师 / 终审合并官）  
> 规则：主仓 `main` 只准通过 `dev_tools/team_ctl.py merge <TASK_ID>` 合入；所有改动必须源自独立 Worktree 分支并持有 `GATE PASS` 凭据。

## 当前冲刺：Sprint 1 · 消灭 A/B-roll 切换拖尾与片段头尾 NG 画面

| 任务单 | 负责 Agent | 分支与 Worktree | 锁定独占文件 (`allowed_files`) | 当前状态 | 依赖与前置 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [T001](tasks/T001_ng_boundary_detector.md) | **DeepSeek Harness** | `agent/deepseek/T001` (已合入并清理) | `short-video-studio/scripts/clip_boundary_audit.py`<br>`short-video-studio/scripts/qa_inspector.py`<br>`tests/test_cut_boundary_ng.py` | `MERGED`（已合入 `main` 并清理 Worktree） | 无（独立先行/并行） |
| [T002](tasks/T002_clean_ab_cut_trim.md) | **Codex** | `agent/codex/T002` (已合入并清理) | `short-video-studio/scripts/step4_build_edl.py`<br>`tests/test_ab_cut_trim.py` | `MERGED`（已合入 `main` 并清理 Worktree） | 可选消费 `clip_boundary_audit`（含内置回退，可与 T001 真并行） |

## Sprint 2 · 多风格白盒 DNA 复刻与编舞护栏（已完成合并）

| 任务单 | 负责 Agent | 分支与 Worktree | 锁定独占文件 (`allowed_files`) | 当前状态 | 依赖与前置 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [T003](tasks/T003_style03_17_dna.md) | **Codex** | `agent/codex/T003` (已合入并清理) | `short-video-studio/scripts/style_dna_profiles.py`<br>`short-video-studio/scripts/render_template_overlays.py`<br>`short-video-studio/assets/style_spectrum/style_spectrum_library.json`<br>`tests/test_style03_17_dna.py` | `MERGED`（已合入 `main` 并补齐红底条文字阴影比例缩放，清理 Worktree） | 与 T004 文件零交集，真并行 |
| [T004](tasks/T004_style01_21_choreographer.md) | **DeepSeek Harness** | `agent/deepseek/T004` (已合入并清理) | `short-video-studio/scripts/style_01_21_choreographer.py`<br>`short-video-studio/scripts/style_lock.py`<br>`tests/test_style01_21_dna.py` | `MERGED`（已合入 `main` 并清理 Worktree） | 与 T003 文件零交集，真并行 |

## 状态机约定
`READY` → `IN_PROGRESS`（已派发）→ `IN_REVIEW`（Worker 过 `gate` 并交回执）→ `MERGED`（总控合入 `main` + 端到端出片验收完成）
