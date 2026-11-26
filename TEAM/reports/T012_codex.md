# 任务交卷回执（T012）

- **负责人** `codex`
- **分支** `agent/codex/T012`
- **核心改动摘要**

  1. 商业口播用短句突出要点，底字幕保留原话，留白幕只保留底字幕。探店沿用原有花字，按每条素材的构图决定弹幕能否放在人身后。
  2. 单口播按导演安排使用固定、缓推和侧移三种机位，真人画面保持原速，重音对齐花字入场。
  3. 透明动效支持 Chrome 和 Pillow 两条渲染路径，配乐可以延迟进入、在反转时压低、在段落过渡时抬升，剪映草稿保留可编辑文字。
  4. 审片工具生成带时间标记的联系表和六维评分。建议修改经原话溯源校验后可写回一次，并给出重跑起点。

## 改动文件清单（对照 allowed_files）

### A · 舞台、机位与音效（`fa07a65`）

- `short-video-studio/scripts/kinetic_choreographer.py` 接入总谱和逐镜构图，安排否定连击、序号徽标、对比卡和留白幕，按幕预算、全片上限和时间间隔选重音。
- `short-video-studio/scripts/step4_build_edl.py` 按导演安排锁定口播镜头，对允许实拍切入的幕继续检验素材与台词的匹配度。单口播机位通过既有 `vf_extra` 接口渲染。
- `short-video-studio/scripts/step6_packaging.py` 为商业口播保留普通字幕，按动效入场帧生成音效，并记录总谱和构图摘要。
- `tests/test_v6_stage_and_camera.py` 验证商业口播、探店实拍选择、逐镜弹幕限制、无主标题的否定连击和离线回退。

### B · 透明动效、混音与剪映草稿（`f72f6fb`）

- `short-video-studio/scripts/capture_html_video.mjs` 支持单任务及批任务抓取透明 MOV，保留常规 H.264/BT.709 MP4 输出。
- `short-video-studio/scripts/render_template_overlays.py` 为两类题材提供对应的配色和字形布局，浏览器和 Pillow 路径共用弹簧曲线。
- `short-video-studio/scripts/step8_mix_audio.py` 在配乐轨接入平滑的延迟进入、静音落点和段落抬升，记录实际生效的配乐提示与衰减量。
- `short-video-studio/scripts/export_jianying_native.py` 将新的舞台文字和圆角底框映射到可编辑的剪映文字层，沿用六个轨位。
- `tests/test_v6_motion_and_audio.py` 验证双主题字形、两条透明渲染路径、混音增益和剪映文字材料。

### C · 审片与基准测评（`449b7b1`）

- `short-video-studio/scripts/aesthetic_critic.py` 生成联系表和审片任务，提供离线六维评分，安全写回一次经校验的修改。
- `dev_tools/bench_talking_head.py` 汇总多个项目的工程与审片指标，输出 Markdown 或 JSON 记分卡。
- `tests/test_aesthetic_critic.py` 验证真实抽帧、无视频回退、正反样本评分、补丁校验与一次修订锁，以及两种记分卡。

## 闸门与测试实测输出

- 执行命令 `"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T012`

### A · 舞台和机位

```text
Ran 4 tests in 1.437s
OK
```

商业测试样本部署 7 条帧对齐音效。开场至少两次切换，真人镜头为 1.0x，冻帧和程序插镜均为 0。探店样本在允许插镜的幕选中一条与台词匹配的素材。

### B · 动效、混音与剪映导出

```text
Ran 6 tests in 7.674s
OK
```

Chrome 与 Pillow 均输出带 Alpha 的 MOV。合成立体声测试测得反转窗口衰减 26 dB，样本差值小于 0.05，混音集成测试测得 −14.00 LUFS。

### C · 审片与记分卡

```text
Ran 6 tests in 0.768s
OK
```

`bench_talking_head.py --smoke` 的商业复盘、粤语探店和方法清单三个合成样本均通过。每个样本的 QA 失败数为 0，最长高光文案 6 字，高光覆盖率 50%，音效 6 条，审片均分 9.43。

### 既有回归与正式闸门

```text
Ran 86 tests in 8.066s
OK
Ran 550 tests in 91.942s
OK (skipped=1)
=== GATE PASS: T012 ===
```

正式闸门使用现有的 24 份原始特效测试样本。测试结束后已清理临时样本目录及链接。

## 接口对齐与遗留风险说明

- 新编排由总谱或构图画像启用，缺少两类输入的旧项目沿用原编排。导演总谱、构图画像、成片渲染器和工程质检器继续使用原有接口。
- 音效条数受导演预算和可用入场锚点限制。锚点不足时优先保证至少 1.2 秒的间隔与留白幕静音。
- 审片补丁通过溯源校验后写回，`critic_revised` 锁定一次修订。应用成功会给出从 `04_build_edl` 重跑的起点。真实双标杆成片的视觉终验由总控执行。
