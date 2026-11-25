# 任务交卷回执（T005）

- **负责人** `codex`
- **分支** `agent/codex/T005`
- **核心改动摘要**

  1. 剪辑表读取两套风格的节奏目标。切点取自已有音频事件，画面提前或延后时仍可按帧核对切点。
  2. `style_01` 优先保留前 3.2 秒的口播画面，实拍补镜目标为 57.2%。`style_21` 在事件充足时安排至少四个开场画面切片，实拍补镜目标为 75.6%，衔接口播推镜为 1.20 倍。
  3. `style_21` 将素材清单里与台词对应的同场景标签记为弱匹配。末镜有对应素材且能放进配额时，可以使用实拍补镜。
  4. `style_21` 的开场自我介绍保留同步人像。直接依据不足时，底图可采用与后续同源台词对应的安全素材。
  5. `04_edl.json` 记录风格目标、口播原速和语义门禁判定。整段素材无法匹配时回到可用的口播画面。

## 改动文件清单（对照 allowed_files）

- `short-video-studio/scripts/step4_build_edl.py` 接入节奏目标，本轮修复同场景标签选镜、末镜配额和开场抠像底图。
- `tests/test_sprint3_step4_rhythm.py` 验证两套风格、事件不足、词尾保护、语义降级、契约缺失和 `style_02` 隔离。
- `TEAM/reports/T005_codex.md` 追记终审修复与回归结果。

## 闸门与测试实测输出

执行命令 `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T005`

```text
Ran 7 tests in 0.745s
OK
Ran 33 tests in 7.733s
OK
[gate] 分支校验通过: agent/codex/T005
[gate] 变更白名单通过 (3 个文件): ['TEAM/reports/T005_codex.md', 'short-video-studio/scripts/step4_build_edl.py', 'tests/test_sprint3_step4_rhythm.py']
=== GATE PASS: T005 ===
```

终审点名的两条既有测试用原断言复跑，均通过。

- `test_high_broll_edl_and_qa_styles_21_to_24` 通过。用同一组台词及标签做合成测量，`style_21` 的 `broll_ratio_with_cards` 从 0.406 恢复至 0.740，末镜采用「现场」标签对应的实拍。
- `test_corner_cutout_upper_torso_crop_and_shaky_preroll_skip` 通过。首镜恢复前景抠像，开机晃动的素材仍从安全区间开始取帧。

```text
Ran 2 tests in 57.410s
OK
```

## 接口对齐与遗留风险说明

- 契约字段为剪辑表顶层的 `choreography_applied`。开场底图借用后续同源台词的依据时，`sem_match` 留有对应的句子 ID。
- 同场景标签按弱匹配记录。覆盖率达到 74.0% 的标签样本仍低于语义门禁的强匹配下限，严格校验会报告命中率不足。
