# TEAM/BOARD.md · 多 AI Agent 协同看板

> 维护者：Antigravity（总控架构师 / 终审合并官）  
> 规则：主仓 `main` 只准通过 `dev_tools/team_ctl.py merge <TASK_ID>` 合入；所有改动必须源自独立 Worktree 分支并持有 `GATE PASS` 凭据。

## 当前冲刺：Sprint 1 · 消灭 A/B-roll 切换拖尾与片段头尾 NG 画面

| 任务单 | 负责 Agent | 分支与 Worktree | 锁定独占文件 (`allowed_files`) | 当前状态 | 依赖与前置 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [T001](tasks/T001_ng_boundary_detector.md) | **DeepSeek Harness** | `agent/deepseek/T001`<br>`../.wt-shortVideo-CCstudio/deepseek` | `short-video-studio/scripts/clip_boundary_audit.py`<br>`short-video-studio/scripts/qa_inspector.py`<br>`tests/test_cut_boundary_ng.py` | `READY`（待派发） | 无（独立先行/并行） |
| [T002](tasks/T002_clean_ab_cut_trim.md) | **Codex** | `agent/codex/T002`<br>`../.wt-shortVideo-CCstudio/codex` | `short-video-studio/scripts/step4_build_edl.py`<br>`tests/test_ab_cut_trim.py` | `READY`（待派发） | 可选消费 `clip_boundary_audit`（含内置回退，可与 T001 真并行） |

## 状态机约定
`READY` → `IN_PROGRESS`（已派发）→ `IN_REVIEW`（Worker 过 `gate` 并交回执）→ `MERGED`（总控合入 `main` + 端到端出片验收完成）
