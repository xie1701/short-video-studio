# 任务交卷回执（T004）

- **负责人**：`deepseek`
- **分支**：`agent/deepseek/T004`
- **核心改动摘要（3–5 句大白话说明解决了什么问题、核心思路是什么）**：
  1. 新增 `style_01_21_choreographer.py`：把风格库里实测的节奏真值落成纯函数计划。`style_01` 出 3 拍错落斜标开场 + 中位 1.87s 口播紧拼 + 约 57% 语义画面覆盖；`style_21` 出前 3 秒 5 个画面切片（4 次硬切）+ 约 76% 工艺特写覆盖 + 纯构图推镜关键帧，两套风格都锁死口播倍速 1.0x。
  2. 候选画面按「标签和这句台词对得上」才会上，对不上就保住可用的口播镜头并写明降级原因，不硬塞无关画面，也不用加速口播凑快剪感。
  3. `style_lock.py` 新增只读深层体检 `check_style_dna_integrity()` 与 `--check-dna` 入口：核对无描边风格没有被偷偷加粗黑边、B-roll 目标占比在 0..1 且落在实测区间并与编舞契约同源、画中画模式/几何与 540px 对称居中不越界、全片仍是零转场硬切。
  4. 新增 `tests/test_style01_21_dna.py`：覆盖两套风格的编舞契约、真实库 5 套风格 DNA 全绿、CLI 向后兼容，以及越界占比 / 非 1.0 倍速锁 / 偷偷加描边三类异常的精准拦截。

## 改动文件清单（对照 allowed_files）
- `short-video-studio/scripts/style_01_21_choreographer.py` — 新增。导出 `STYLE_RHYTHM_CONTRACTS`、`plan_style_01_rhythm()`、`plan_style_21_rhythm()`；只读库/蓝图 JSON，纯函数、无随机、无写盘。
- `short-video-studio/scripts/style_lock.py` — 新增 `check_style_dna_integrity()`、`render_dna_report()` 与 `--check-dna` 开关；未传开关时原有文本/JSON 结构、`--waive`、`--clear-waiver` 与退出码 0/3 全部不变。
- `tests/test_style01_21_dna.py` — 新增 13 个用例（编舞计划 + 风格锁 DNA + CLI 兼容 + 跨风格隔离/隐私）。

## 闸门与测试实测输出
- 执行命令：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T004`
- 输出关键行（回执落盘后复跑的真实输出；`[gate] running` 里解释器的本机绝对路径按仓库隐私规范不落盘为固定路径）：
  ```text
  test_contract_comes_from_library_truth ... ok
  ...
  test_unknown_style_fails_without_crashing ... ok
  ----------------------------------------------------------------------
  Ran 13 tests in 0.246s

  OK
  test_v2_whitebox_style_spectrum_and_rulebooks_contract ... ok
  test_repo_privacy_and_zero_local_project_implants ... ok

  ----------------------------------------------------------------------
  Ran 2 tests in 0.210s

  OK
  [gate] 分支校验通过: agent/deepseek/T004
  [gate] 变更白名单通过 (4 个文件): ['TEAM/reports/T004_deepseek.md', 'short-video-studio/scripts/style_01_21_choreographer.py', 'short-video-studio/scripts/style_lock.py', 'tests/test_style01_21_dna.py']
  === GATE PASS: T004 ===
  ```
- 闸门同时逐条执行了 `style_lock.py --style-id style_01` / `style_21` 与两个 `--check-dna` 入口，四条命令均退出码 0，DNA 13 项检查全绿。
- 补充回归：`tests.test_studio.StyleLockTests` 9 个既有风格锁用例全部通过（`Ran 9 tests ... OK`），确认 `--pipeline-dir`、`--waive`、豁免、退出码 3 的既有行为未被破坏。

## 接口对齐与遗留风险说明
- 契约确认：
  - `STYLE_RHYTHM_CONTRACTS["style_01"|"style_21"]` 含 `broll_target_ratio`（0.572 / 0.756）、`opening_cuts_3s`（3 / 4）、`median_shot_sec`（1.87 / 1.15）、`opening_mode`、`punch_in_scale`（1.0 / 1.2）、`aroll_speed_lock=1.0`，全部从库与蓝图读取。
  - 两套计划都返回 `style_id` / `segments` / `broll_ratio_planned` / `aroll_speed_locked`；`style_01` 另有 `opening_beats`（严格 3 拍），`style_21` 另有 `opening_3s_cuts` / `fast_cuts_under_1s_ratio` / `punch_in_count` / `keyframe_count`。额外字段（切片帧号、推镜轨迹、降级原因）只是给后续接入用，不影响契约字段。
  - `check_style_dna_integrity()` 返回 `{"ok", "style_id", "checks", "issues"}`；`--check-dna` 只在显式传入时给报告加 `dna` 字段，未传入时输出与旧版一致。
- 遗留与边界：
  - 编舞计划是「镜头角色与配额建议」，不是最终 EDL；总片长小于 3 秒时 `opening_3s_cuts` 只按实际时长切，不会伪造 3 秒画面。
  - `broll_ratio_planned` 取决于候选画面标签与台词的语义交集；交集不足时按实际可用时长降级并在 `degrade_reason` 写清原因，绝不靠同一段画面反复硬塞。
  - DNA 的占比自洽采用库里的 `broll_target_band_pct` 做区间校验，并允许 `transition_and_packaging` 目标与总目标相差 ≤0.15（实测 `style_02` 0.22 与 0.112 属于同量级，不能误杀）。
  - 未修改 `forbidden_files` 中的 `style_dna_profiles.py`、`render_template_overlays.py`、`style_spectrum_library.json` 与 `step4_build_edl.py`；需要总控协调的新接口：无。
