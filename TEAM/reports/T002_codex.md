# 任务交卷回执（T002）

- **负责人**：`codex`
- **分支**：`agent/codex/T002`
- **核心改动摘要**
  1. 实拍补镜优先取素材清单指定的安全区间，其次用边界检测结果。两者都缺失时，长素材避开容易晃动的两端，短素材尽量取中间。
  2. 主讲画面先利用同一视频里接得上的源帧。仍短超过 0.25 秒时，开放的镜头才由语义匹配且时长够用的实拍接管。
  3. 修正 `style_02` 的语义匹配字段，过渡句可以选到对应实拍。动态花字期间的主讲与补镜切换落在句界或气口的事件帧。
  4. 写入剪辑表前复核源区间和实际可播放时长，记录边界依据与剩余冻帧情况。

## 改动文件清单（对照 allowed_files）

- `short-video-studio/scripts/step4_build_edl.py` — 分配安全源区间，处理主讲画面不足和句组切换，复核最终镜头。
- `tests/test_ab_cut_trim.py` — 新增六项回归测试，覆盖清单与检测优先级、素材重用、默认边距、主讲画面不足和切点事件。
- `TEAM/reports/T002_codex.md` — 任务回执。

## 闸门与测试实测输出

- 执行命令：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T002`
- 输出关键行

  ```text
  Ran 18 tests in 3.982s

  OK
  [gate] 分支校验通过: agent/codex/T002
  [gate] 变更白名单通过 (2 个文件): ['short-video-studio/scripts/step4_build_edl.py', 'tests/test_ab_cut_trim.py']
  === GATE PASS: T002 ===
  ```

- 使用合成视频验证了 MP4 单镜兜底。约 0.47 秒源画面按 1.5 秒时间线输出了 45 帧。
- 联调时额外运行 `tests.test_studio`，共 234 项，3 项断言失败、4 项报错、1 项跳过。7 项失败和报错均用修改前代码复现。断言失败涉及既有分层抠像，报错来自取证文件缺失和字体目录写权限。

## 接口对齐与遗留风险说明

- 兼容 T001 的可选 `analyze_clip_safe_window(media_path)` 接口，实拍区间写入 `boundary_trim_reason`。后续镜头的 `timeline_in_frame - j_cut_lead_frames` 与所引用的原音频事件保持帧对齐。
- 锁定镜头或缺少合适实拍时，最后用放慢已有画面补足较大缺口，原声保持原速，口型可能有偏差。直接比较原始源区间与时间线时长的检查需要按 `speed` 折算。原生剪映导出目前按原速写源区间，复现此兜底需要读取 `speed`。
