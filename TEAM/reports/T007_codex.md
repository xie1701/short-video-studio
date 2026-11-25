# 任务交卷回执（T007）

- **负责人** `codex`
- **分支** `agent/codex/T007`
- **核心改动摘要**

  1. 从锁定音轨生成导演任务清单，附上完整句表、已有构图提示和可用实拍素材的描述。
  2. 保存总谱前检查每句的归属、画面大字和音乐时间，并从原句核对用词与数字。构图限制人后遮挡时会拦截人后弹幕。
  3. 离线方案按台词安排否定连击、条目顺序、机制对比和留白，保留探店的数字与菜品清单，并在反转处压低配乐。
  4. 流水线优先读取校验通过的总谱。文件缺失或校验失败时可在内存中重新编排，保存由命令行显式触发。

## 改动文件清单（对照 allowed_files）

- `short-video-studio/scripts/director_plan.py` 实现任务清单、总谱校验、离线生成、只读加载和命令行。
- `tests/test_director_plan.py` 覆盖两种题材、用词与数字溯源、构图限制、音乐边界、回退和命令行落盘。
- `TEAM/reports/T007_codex.md` 记录交付接口、实测结果和已知边界。

## 闸门与测试实测输出

执行命令 `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T007`

```text
Ran 19 tests in 0.251s
OK
Ran 9 tests in 3.159s
OK
[gate] 分支校验通过: agent/codex/T007
[gate] 变更白名单通过 (2 个文件): ['short-video-studio/scripts/director_plan.py', 'tests/test_director_plan.py']
=== GATE PASS: T007 ===
```

## 接口对齐与遗留风险说明

- 总谱按规格提供 `acts`、`theme_profile` 与 `music_cues`，覆盖率以锁定时间上的高光幕时长重算。原语枚举采用任务单逐项列出的 12 个值。
- 只读加载器可直接供下游使用。命令行提供 `build-tasks`、`apply-plan` 和 `fallback` 三个入口。
- 句子是排期的最小单位。锁定时间凑不出目标覆盖率时，保留句子边界并报告偏差。
- 既有 `style_02` 回归记录了 `step4_build_edl.py` 中的文件句柄告警，该组 9 项测试全部通过。
