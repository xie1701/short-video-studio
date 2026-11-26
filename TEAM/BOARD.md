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

## Sprint 3 · 多风格 DNA 真接入主流水线 step4 / step6（已完成合并）

| 任务单 | 负责 Agent | 分支与 Worktree | 锁定独占文件 (`allowed_files`) | 当前状态 | 依赖与前置 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [T005](tasks/T005_sprint3_step4_rhythm.md) | **Codex** | `agent/codex/T005` (已合入并清理) | `short-video-studio/scripts/step4_build_edl.py`<br>`tests/test_sprint3_step4_rhythm.py` | `MERGED`（已合入 `main` 并清理 Worktree） | 与 T006 文件零交集，真并行 |
| [T006](tasks/T006_sprint3_step6_dna.md) | **DeepSeek Harness** | `agent/deepseek/T006` (已合入并清理) | `short-video-studio/scripts/step6_packaging.py`<br>`tests/test_sprint3_step6_dna.py` | `MERGED`（已合入 `main` 并清理 Worktree） | 与 T005 文件零交集，真并行 |

## V6.0 · `style_02` 百万剪辑师级通用口播引擎（已完成合并与双标杆真片终验）

> 架构总纲与终极验收三道闸门见 [V6_MILLION_EDITOR_SPEC.md](V6_MILLION_EDITOR_SPEC.md)。  
> 执行波次：`Wave 1 (T007 + T009)` → `Wave 2~4` [T012](tasks/T012_v6_wave2to4_bundle.md) → 已全部合入 `main` 并完成双标杆成片与 QA 终验。

| 波次 | 任务单 | 负责 Agent | 分支与 Worktree | 锁定独占文件 (`allowed_files`) | 当前状态 | 依赖与前置 |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **Wave 1** | [T007](tasks/T007_v6_director_plan.md) | **Codex** (`codex`) | `agent/codex/T007` (已合入并清理) | `short-video-studio/scripts/director_plan.py`<br>`tests/test_director_plan.py` | `MERGED`（两轮返工后终审通过，已合入 `main`） | 无前置（与 T009 文件零交集） |
| **Wave 1** | [T009](tasks/T009_v6_composition_profile.md) | **Codex B** (`codex-b`) | `agent/codex-b/T009` (已合入并清理) | `short-video-studio/scripts/composition_profile.py`<br>`tests/test_composition_profile.py` | `MERGED`（一轮返工后终审通过，已合入 `main`） | 无前置（与 T007 文件零交集） |
| **Wave 2~4** | [T012](tasks/T012_v6_wave2to4_bundle.md)<br>（合并 [T008](tasks/T008_v6_stage_and_camera.md) + [T010](tasks/T010_v6_motion_and_audio.md) + [T011](tasks/T011_v6_aesthetic_critic.md)） | **Codex** (`codex`) | `agent/codex/T012` (已合入并清理) | T008、T010、T011 三份白名单的并集（共 12 个文件，见任务单） | `MERGED`（一轮返工后终审通过，已合入 `main` 并通过全量 552 项回归） | 依赖 T007 + T009 已合入 `main` |

## 当前冲刺：V6.5 · 顶配百万爆款精剪跃迁（9.5/10 标尺 · 由 DeepSeek Harness 执行）

| 波次 | 任务单 | 负责 Agent | 分支与 Worktree | 锁定独占文件 (`allowed_files`) | 当前状态 | 依赖与前置 |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **V6.5 全包** | [T013](tasks/T013_v65_million_craft_polish.md) | **DeepSeek Harness** (`deepseek`) | `agent/deepseek/T013`<br>`.wt-shortVideo-CCstudio/deepseek` | `run_pipeline.py`、`director_plan.py`、`kinetic_choreographer.py`、`step6_packaging.py`、`render_template_overlays.py`、`export_jianying_native.py`、`aesthetic_critic.py`<br>`tests/test_v65_pipeline_and_critic.py`<br>`tests/test_v65_element_motion.py` | `IN_PROGRESS`（已建 Worktree 派单） | 依赖 V6.0 (`T007`+`T009`+`T012`) 已合入 `main` |

## 状态机约定
`READY` → `IN_PROGRESS`（已派发）→ `IN_REVIEW`（Worker 过 `gate` 并交回执）→ `MERGED`（总控合入 `main` + 端到端出片验收完成）
