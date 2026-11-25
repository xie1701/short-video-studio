# 任务交卷回执（T002）

- **负责人**：`codex`
- **分支**：`agent/codex/T002`
- **核心改动摘要**
  1. 实拍补镜优先取素材清单指定的安全区间，其次用边界检测结果。两者都缺失时，长素材避开容易晃动的两端，短素材尽量取中间。
  2. 主讲画面保持 1.0 倍速。同一视频的句尾最多原速顺延 1.0 秒，出点受素材末尾、后句占用和标废区间约束。
  3. 修正 `style_02` 的语义匹配字段，过渡句可以选到对应实拍。动态花字期间的主讲与补镜切换落在句界或气口的事件帧。
  4. 主讲画面仍短超过 0.25 秒时，开放的镜头可由语义匹配且时长够用的实拍接管。剩余的主讲冻帧缺口如实标记并告警，放慢画面的兜底用于实拍补镜。

## 改动文件清单（对照 allowed_files）

- `short-video-studio/scripts/step4_build_edl.py` — 主讲句尾原速顺延最多 1.0 秒，缩小慢放兜底的适用范围，记录主讲画面的剩余缺口。
- `tests/test_ab_cut_trim.py` — 验证主讲画面始终原速，覆盖 0.50 秒缺口的顺延补齐、1.0 秒上限、标废窗、后句占用和缺口告警。
- `TEAM/reports/T002_codex.md` — 任务回执。

## 闸门与测试实测输出

- 执行命令：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T002`
- 输出关键行

  ```text
  Ran 19 tests in 5.687s

  OK
  [gate] 分支校验通过: agent/codex/T002
  [gate] 变更白名单通过 (3 个文件): ['TEAM/reports/T002_codex.md', 'short-video-studio/scripts/step4_build_edl.py', 'tests/test_ab_cut_trim.py']
  === GATE PASS: T002 ===
  ```

- 另跑跨句停顿和风格切换两项既有回归，全部通过。0.50 秒气口的合成 MP4 经 T001 检查，源区间覆盖整镜，切点与音频事件的最大偏差为 0 帧。
- 首轮联调额外运行 `tests.test_studio`，共 234 项，3 项断言失败、4 项报错、1 项跳过。7 项失败和报错均用修改前代码复现。断言失败涉及既有分层抠像，报错来自取证文件缺失和字体目录写权限。

## 接口对齐与遗留风险说明

- 兼容 T001 的可选 `analyze_clip_safe_window(media_path)` 接口，实拍区间写入 `boundary_trim_reason`。后续镜头的 `timeline_in_frame - j_cut_lead_frames` 与所引用的原音频事件保持帧对齐。
- 实拍余量耗尽且无法换镜时，A-roll 保持原速并将剩余缺口记为 `freeze_tail=True`，同时输出警告。
- 在 0.50 秒气口的合成用例中，现有 `aroll_sentence_lock` 句界检查报告超界 0.42 秒。该检查按原语音句尾计算，长气口顺延仍会收到这类提示。
