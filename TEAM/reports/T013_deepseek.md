# 任务交卷回执（T013）

- **负责人**：`deepseek`
- **分支**：`agent/deepseek/T013`
- **核心改动摘要（3–5 句大白话说明解决了什么问题、核心思路是什么）**：
  1. 一条命令就能跑完 V6：`style_02` 进剪 step4 前自动生成构图画像和导演任务包，外部总谱先校验再落盘，出片后自动补一份可编辑的剪映原生草稿和一份六维审片结论；审片不过时按建议回写总谱、从 step4 重剪 1 轮，靠 `critic_revised` 锁保证只修一次。
  2. 大字动效从“整张卡片一起弹”升级成“元素各自动”：否定句弹入后红线划除、对比卡三拍错峰揭示、核心数字五拍滚动砸定、结构徽标带 01/02/03 进度格；四套效果都只靠元素上的声明字段驱动，Chrome SVG 与无浏览器 Pillow 兜底共用同一份分拍时间与几何数据，所以离线看到的和有浏览器的效果一致。
  3. 商业口播里“大字已经写着同一句话，底下字幕还复读一遍”的廉价感被自动去掉：只有底字幕同一句、且前景大字完整罩住它那段时间才隐藏，长句和偏半窗的底字幕一律保留，口播覆盖率不下降。
  4. 探店长片加了一次克制的配乐呼吸（核心揭晓前 0.35s、-20dB），商业长段落在不挡脸、不压字幕、避开开场与高光余韵的前提下补最多两个小号微锚点；反转幕主标题不再留空。
  5. 全量回归 565 项单测全过，白名单/隐私/分支闸门全绿。

## 改动文件清单（对照 allowed_files）

- `short-video-studio/scripts/run_pipeline.py` — 一键 V6 闭环：新增 `--no-v6-director` / `--director-plan` / `--director-review-pause` / `--auto-critic-revise`；`_prepare_v6_director()` 串起 01b 构图画像、03b 导演任务包与总谱校验；`_render_and_verify_only()` 补跑剪映原生草稿与审美审片（失败只记状态）；`_revise_once_with_critic()` 完成一次幂等重剪。
- `short-video-studio/scripts/step6_packaging.py` — 商业模式把舞台高光项传给 `business_caption_items` 做避重，统计 `suppressed_duplicate_captions`；新增 `no_v6_director` 开关，显式退回 V5 规则路径。
- `short-video-studio/scripts/kinetic_choreographer.py` — 元素级元数据派发（`element_choreo` 四类 + 全片 `pillar_index/pillar_total`）；短句底字幕避重（`is_duplicate_short_caption` / `duplicate_short_captions`）；商业长段落的 `editorial_ticker` 微锚点生成（避脸 IoU 判定 + 底部字幕区护栏 + 静音）。
- `short-video-studio/scripts/render_template_overlays.py` — 四套元素级物理编舞渲染器（`strike_slash` / `stagger_card` / `number_roll` / `ticker_fade`）与结构进度格、微锚点胶囊静帧；Chrome SVG 与 Pillow 兜底共用 `_element_beats` + `_element_frames` 同一帧表。
- `short-video-studio/scripts/director_plan.py` — 探店 `>=20s` 且无 `silence_drop` 时补一次克制呼吸并可补一条温和推升；反转幕主标题支持“句尾截取 + 溯源校验”兜底。
- `short-video-studio/scripts/export_jianying_native.py` — 原生草稿兼容 `editorial_ticker` 变体（小号胶囊 + 6 轨可编辑）与 `pillar_index/pillar_total` 元数据。
- `short-video-studio/scripts/aesthetic_critic.py` — 新增 `duplicate_short_captions` / `element_choreo_ratio` 指标与 `suppress_duplicate_captions` 建议标签，`motion_craft` 区分整卡动效与元素级编舞；CLI 支持 `--review-file`。
- `tests/test_v65_pipeline_and_critic.py` — 新增 7 项：一键闭环产物、总谱复用、非 `style_02`/`--no-v6-director` 零变化、外部总谱校验与审稿暂停、一次幂等自动重剪、底字幕避重、探店呼吸与反转主标题。
- `tests/test_v65_element_motion.py` — 新增 6 项：红线划除、三拍错峰、数字滚动、进度格、微锚点落位与淡入淡出。

## 闸门与测试实测输出

- 执行命令：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate T013`
- 输出关键行（须包含 `=== GATE PASS: T013 ===` 及 unittest 实测总数/耗时）：
  ```text
  [gate] running: ... -m unittest tests.test_v65_pipeline_and_critic tests.test_v65_element_motion -v
  Ran 13 tests in 6.596s
  OK
  [gate] running: ... -m unittest tests.test_director_plan tests.test_composition_profile tests.test_v6_stage_and_camera tests.test_v6_motion_and_audio tests.test_aesthetic_critic tests.test_style02_polish tests.test_studio.ProductionPipelineV48Tests.test_v50_style02_kinetic_stage_and_broll_policy tests.test_studio.StudioTests.test_repo_privacy_and_zero_local_project_implants -v
  Ran 75 tests in 10.123s
  OK
  [gate] running: ... dev_tools/bench_talking_head.py --smoke
  | 商业复盘 | 0 | 6 | 0.5 | 6 | 是 | 9.37 | 是 |
  | 粤语探店 | 0 | 6 | 0.5 | 6 | 是 | 9.37 | 是 |
  | 方法清单 | 0 | 6 | 0.5 | 6 | 是 | 9.37 | 是 |
  [gate] running: ... -m unittest discover -s tests
  Ran 565 tests in 159.199s
  OK (skipped=1)
  [gate] 分支校验通过: agent/deepseek/T013
  [gate] 变更白名单通过 (10 个文件): ['TEAM/reports/T013_deepseek.md', 'short-video-studio/scripts/aesthetic_critic.py', 'short-video-studio/scripts/director_plan.py', 'short-video-studio/scripts/export_jianying_native.py', 'short-video-studio/scripts/kinetic_choreographer.py', 'short-video-studio/scripts/render_template_overlays.py', 'short-video-studio/scripts/run_pipeline.py', 'short-video-studio/scripts/step6_packaging.py', 'tests/test_v65_element_motion.py', 'tests/test_v65_pipeline_and_critic.py']
  === GATE PASS: T013 ===
  ```
- 阶段 A/B/C 本地提交（同一分支，禁止 push）：
  ```text
  8a35158 feat(T013-A): wire V6 composition, director, native draft, and critic into run_pipeline
  8433887 feat(T013-B): add element-level choreography and duplicate short-caption suppression
  88ed5ab feat(T013-C): support one-shot auto critic revision and contrast headline fallback
  ```

## 接口对齐与遗留风险说明

- 契约确认：
  - `render_mp4.py` / `qa_inspector.py` / `composition_profile.py` / `step4_build_edl.py` / `step8_mix_audio.py` / `capture_html_video.mjs` 零改动；元素级编舞完全由 `elem["element_choreo"]` 在 `render_animated_overlay_mov()` 内部分发，输出仍是偶数宽高、`argb/qtrle` 的透明 `.mov`，`render_mp4` 拿到的仍是 `(mov_path, width, height)`。
  - `business_caption_items(audio_events, subtitles, fps)` 旧调用 100% 兼容：不传 `highlight_items` 时完全不避重。
  - `run_visual_to_delivery()` / `_render_and_verify_only()` 只追加了带默认值的关键词参数；非 `style_02` 与 `--no-v6-director` 路径不产生任何新文件。
- 需要总控知道的边界：
  1. 底字幕避重比任务单多了一道“安全保底”：前景大字必须**完整罩住**底字幕的时段才隐藏（容差 0.02s），否则保留。原因是不加这道护栏时，`tests/test_v6_stage_and_camera.py::test_business_full_pipeline_preserves_captions_and_locks_camera_to_audio` 里“不是被骗”（2.80–5.60s）会被 3.10–6.07s 的大字判为复读而丢掉，既有断言被破坏；加护栏后该用例 14 条底字幕全保留，而新用例里时段被完整覆盖的短句仍会被正确隐藏。
  2. `01b_composition_profile.json` 目前不产出 `face_bbox`（`composition_profile.py` 属禁区文件，未改），所以 `editorial_ticker` 的避脸走两级判定：有 `face_bbox` 时按 `IoU == 0.00` 判；没有时要求 `headroom_ratio >= 0.18` 只在顶栏安全带落位，不满足直接放弃（宁缺毋滥）。若后续要让画像直接带人脸上框，需要在 `composition_profile.py` 增字段，请总控派单。
  3. 微锚点全片最多 2 个（`_TICKER_MAX_ITEMS`），任务单未规定上限，这里按“克制、零抢戏”取上限值。
  4. 本 Worktree 缺 `.gitignore` 忽略的本地只读资产 `forensic/decrypted/`，全量回归会因 `test_studio.EffectLayerParityTests.test_effects_match_source_effect_track` 失败。已按 `team_ctl.py` 同步本地资产的同一机制，把主仓该目录逐个软链进 Worktree（未提交、不影响改动清单）；建议总控把该路径补进 `team_ctl.py` 的 `_IGNORED_LOCAL_ASSET_GLOBS`。
