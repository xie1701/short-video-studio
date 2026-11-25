---
task_id: T011
title: V6.0 Wave 4 · 多模态审美审片官 (aesthetic_critic.py) 与跨品类批量基准检验
owner: codex
branch: agent/codex/T011
allowed_files:
  - short-video-studio/scripts/aesthetic_critic.py
  - dev_tools/bench_talking_head.py
  - tests/test_aesthetic_critic.py
forbidden_files:
  - short-video-studio/scripts/step4_build_edl.py
  - short-video-studio/scripts/step6_packaging.py
  - short-video-studio/scripts/step8_mix_audio.py
  - short-video-studio/scripts/render_template_overlays.py
  - short-video-studio/scripts/qa_inspector.py
  - short-video-studio/scripts/render_mp4.py
report_path: TEAM/reports/T011_codex.md
test_commands:
  - "{python} -m unittest tests.test_aesthetic_critic -v"
  - "{python} -m unittest tests.test_director_plan tests.test_v6_motion_and_audio tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v"
---

# T011：多模态审美审片官 (`aesthetic_critic.py`) 与跨品类回归基准脚本

> 开始前必查：`git branch --show-current` 必须为 `agent/codex/T011`。前提是 T010 已合入 `main`。先通读 `AGENTS.md` 与 [TEAM/V6_MILLION_EDITOR_SPEC.md](../V6_MILLION_EDITOR_SPEC.md)。

## 一、 背景与闭环收口定位
29 项 QA (`qa_inspector.py`) 保证工程零失误，但“好不好看、信息减法到不到位、哪里太闷或字压背景”，需要一双审美审片官的眼睛。
本任务实现类似 `script_polish` 的解耦审片闭环：
- 自动将 `output_final.mp4` 关键时间点拼成带精确秒标的高清审片接触表，配套可读摘要生成 `10b_aesthetic_review_tasks.json`；
- 消费多模态大模型（或内置离线确定性启发式审片器）写入的 `10b_aesthetic_review.json`，对 **6 个专业维度（0~10分）** 进行客观体检，并可将高价值的修正补丁自动安全回写 `03b_director_plan.json` 触发下一轮精修（最多 1 次修订防止振荡）。

## 二、 必须实现的功能（严格限于 `allowed_files`）

### 1. 新建 `short-video-studio/scripts/aesthetic_critic.py`
定义常量：`REVIEW_TASKS_FILE = "10b_aesthetic_review_tasks.json"`、`REVIEW_RESULT_FILE = "10b_aesthetic_review.json"`、`CRITIC_DIMENSIONS = ("info_distillation", "rhythm_breathing", "spatial_avoidance", "audio_tension", "motion_craft", "theme_fit")`。

必须实现并导出：
1. **`build_aesthetic_review_package(pipeline_dir: Path, out_image_name: str = "10b_critic_sheet.jpg") -> Dict[str, Any]`**
   - 收集关键审片时刻（首 3 秒采样 `0.5s, 1.5s, 2.5s` + 每条高光幕中点 + 随机抽样 `plain_bridge/clean_breath`）；
   - 若存在 `output_final.mp4`，使用 `ffmpeg` 精确 `-ss` 抽帧并 Pillow 拼成 `10b_critic_sheet.jpg`（每格标注秒数、所属幕 `act_id` 与原语名称；测试构造无 mp4 场景时安全降级跳过图片合成而不崩溃）；
   - 读取 `03b_director_plan.json`、`01b_composition_profile.json`、`06_packaging.json`、`08_mix_metrics.json`、`10_qa_report.json`，输出结构化 `10b_aesthetic_review_tasks.json` 与审片 Rubric。

2. **`evaluate_review_heuristics(pipeline_dir: Path) -> Dict[str, Any]`**
   - 纯离线确定性审美基准测量器（当外部大模型审片未提供结果时作为兜底自检）：
     * 检查高光视觉大字是否全部 `<= 7` 字（越限扣分并生成 `shorten_visual_copy` patch）；
     * 检查高光占比是否在 `[0.35, 0.55]`（过满 >0.65 生成 `convert_to_clean_breath` patch）；
     * 检查 `allow_behind_person == False` 时是否彻底消除 `behind_person` 项；
     * 检查音效数是否在 `[6, 14]` 且存在至少 1 条生效 `delay_entry` 或 `silence_drop`；
     * 产出规范化的 `10b_aesthetic_review.json`（含 `scores`、`mean_score`、`min_score`、`passed`（均分 >= 8.5 且单项 >= 8.0）、`patch_suggestions`）。

3. **`apply_critic_patches_to_director_plan(pipeline_dir: Path, review_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]`**
   - 安全读取 `10b_aesthetic_review.json` 中的 `patch_suggestions`（如调整某 `act_id` 的 `primitive` 为 `clean_breath` / `negation_strike`，精简过长 `visual_copy`，或对高张力幕补一条 `silence_drop`），经 `director_plan.validate_director_plan` 校验防幻觉通过后写回 `03b_director_plan.json`（同时打标记 `"critic_revised": True` 防止无限重修）。

### 2. 新建 `dev_tools/bench_talking_head.py`
- 通用的多品类口播回归测评与汇总脚本（严禁写死任何本机个人家目录路径，通过命令行 `--projects <dir1> <dir2>...` 或 `--workspace-root` 传入，也支持 `--smoke` 纯内存/临时目录构造典型品类合成项目做无媒体冒烟评测）：
  - 汇总每个测评项目的以下核心验收指标并打印对比表：
    * `qa_fail_count`（硬性必须为 `0`）
    * `max_headline_chars`（目标 `<= 7`）
    * `highlight_ratio`（目标 `0.35..0.55`）
    * `sfx_count`（目标 `6..14`）
    * `has_music_breathing`（`delay_entry` 或 `silence_drop`）
    * `critic_mean_score`（目标 `>= 8.5`）
  - 可直接输出 JSON 与简洁 Markdown 终验记分卡。

### 3. 新增单元测试 `tests/test_aesthetic_critic.py`
- 测试覆盖：
  1. `build_aesthetic_review_package` 在模拟流水线目录生成完整的 `10b_aesthetic_review_tasks.json`；
  2. `evaluate_review_heuristics` 对一份高质量目录打出均分 `>= 8.5` 且 `passed=True`，而对一份“大字超 10 字 + 音效 28 条过吵 + 大头强行开启人后弹幕”的反面样本准确扣分并产生对应 `patch_suggestions`；
  3. `apply_critic_patches_to_director_plan` 能安全应用补丁回写 `03b_director_plan.json`、二次运行仍满足防幻觉校验，且二次审片得分提升并带有 `critic_revised=True` 幂等锁；
  4. 运行 `dev_tools/bench_talking_head.py --smoke`（或直接调用其核心评测函数）验证跨品类汇总记分卡正常输出且零隐私路径泄漏。

## 三、 交卷步骤
1. 严格仅在 `allowed_files`（含 `TEAM/reports/T011_codex.md`）内改动，严禁写入任何个人绝对路径；
2. 运行任务单全部 `test_commands` 确认 PASS；
3. 运行闸门检查：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T011`，确认 `=== GATE PASS: T011 ===`；
4. 按 `TEAM/REPORT_TEMPLATE.md` 写入 `TEAM/reports/T011_codex.md`，在 `agent/codex/T011` 分支本地 `git commit`（严禁 `git push`）。
