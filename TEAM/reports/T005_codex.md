# 任务交卷回执（T005）

- **负责人** `codex`
- **分支** `agent/codex/T005`
- **核心改动摘要**

  1. 剪辑表读取两套风格的节奏目标。切点取自已有音频事件，画面提前或延后时仍可按帧核对切点。
  2. `style_01` 优先保留前 3.2 秒的口播画面，实拍补镜目标为 57.2%。`style_21` 在事件充足时安排至少四个开场画面切片，实拍补镜目标为 75.6%，衔接口播推镜为 1.20 倍。
  3. 补镜需要有台词依据和可用的素材区间。缺少匹配实拍时优先保留口播，剪辑表继续记录语义门禁判定。
  4. `04_edl.json` 记录使用的风格目标、口播原速和参考计划的降级原因。

## 改动文件清单（对照 allowed_files）

- `short-video-studio/scripts/step4_build_edl.py` 接入节奏目标、事件选点、匹配补镜、口播原速和审计字段。
- `tests/test_sprint3_step4_rhythm.py` 验证两套风格、事件不足、词尾保护、语义降级、契约缺失和 `style_02` 隔离。

## 闸门与测试实测输出

执行命令 `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T005`

```text
Ran 7 tests in 0.342s
OK
Ran 33 tests in 5.060s
OK
=== GATE PASS: T005 ===
```

同一组临时素材实测，`style_01` 开场 3 个切片，实拍覆盖 57.2%。`style_21` 开场 4 个切片，实拍覆盖 72.6%，镜长中位数 0.967 秒，口播推镜为 1.20 倍。用同一组素材对照基线，`style_02`、`style_03` 和 `style_24` 的原有剪辑表字段逐项一致。

## 接口对齐与遗留风险说明

- 契约字段为剪辑表顶层的 `choreography_applied`。其余风格写入 `null`。
- 附加回归共运行 14 项，12 项通过。以下两项在基线通过、在本提交失败，现有断言保留。
  - `test_high_broll_edl_and_qa_styles_21_to_24` 的 `style_21` 样本在按台词依据选镜后，实际补镜覆盖为 40.6%，低于旧断言的 60% 覆盖下限。
  - `test_corner_cutout_upper_torso_crop_and_shaky_preroll_skip` 的开场自我介绍缺少匹配底图，首镜使用口播原画面，旧断言要求首镜抠像。
