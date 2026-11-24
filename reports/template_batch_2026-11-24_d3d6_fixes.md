# 重影 6 套 · D3/D4/D5/D6 修复与前后实测（2026-11-24）

产物目录：`<产物根>/template_batch_24/`（每套 `final.mp4` / `report.json` / `frames/`）
跑法：`python dev_tools/run_template_batch.py --styles style_07,style_09,style_11,style_16,style_19,style_22 --jobs 1 --force --date 2026-11-24_d3d6`
批跑汇总：`reports/template_batch_2026-11-24_d3d6.csv` / `.md`
基线（改前）report.json 原样留档：**不在仓库内**（含素材绝对路径，已移到 `<产物根>/_baseline_before_d3d6/<style>.report.json`）。仓库里只保留本文件的对照表。

## 一、6 套 × 前后对照（数字全部取自 report.json）

| 风格 | ghost FAIL 对数（前→后） | overlay_pairs_checked（前→后） | 抠像 rebuilt / dropped（前→后） | backdrop rebuilt / rejected（前→后） | sub_video_layer_no_asset（前→后） | z_order_mismatch（前→后） | 判定（前→后） |
|---|---|---|---|---|---|---|---|
| style_07 | 2 → **0** | 25 → 25 | 4/0 → 4/0 | 1 → 0（photo 拒 1） | 0 → 0 | 4 → 4 | check_fail → check_fail（仅 z 序） |
| style_09 | 1 → **0** | 12 → 12 | 0/1 → 0/1 | 0 → 0 | 0 → 0 | 3 → 3 | check_fail → **ok** |
| style_11 | 2 → **0** | 5 → 5 | 3/3 → 3/3 | 0 → 0 | 0 → 0 | 0 → 0 | check_fail → **ok** |
| style_16 | 6 → **0** | 10 → 10 | 4/1 → 4/1 | 2 → 0（photo 拒 2） | 0 → 0 | 9 → 9 | check_fail → check_fail（仅 z 序） |
| style_19 | 10 → **0** | 14 → 13 | 4/1 → 4/1 | 2 → 0（无模糊拒 2） | 0 → 0 | 2 → 2 | check_fail → check_fail（仅 z 序） |
| style_22 | 1 → **0** | 7 → 7 | 4/0 → 4/0 | 0 → 0 | 0 → 0 | 2 → 2 | check_fail → **ok** |

- 重影 FAIL 对：**22 → 0**（阈值仍然是 `tolerance_mae = 0.02`，一帧未动）。
- `cutout_dropped_still_rendered` 6 套全 0（"说跳过但画面里还在"的状态不存在）。
- z 序那一列是另一条防线的数字，见第四节（不是本轮四条缺陷，且 16/19 改前就没过）。

## 二、四条缺陷各自的修法

### D3 子层素材避让用错时间窗（`apply_replica_template.py`）

一句话：`build_sub_draft()` 里子层的 `avoid` 从「父段整窗 `_taken_near(parent_start, parent_dur)` + 该复合片段所有已用素材」改成「**子层自己的 `[t0, t0+dur)` 窗口** + 只与该窗口重叠的已用素材」（`_sub_taken()`）。

证据（style_23 模板，25 层顺序播放的复合片段 ×12）：
```
b_roll 池 27 条 → compounds_applied 12 · sub_segments_total 254 · sub_video_layer_no_asset 0
b_roll 池 6  条 → compounds_applied 12 · sub_segments_total 254 · sub_video_layer_no_asset 0
```
6 条素材就能填满 25 层顺序子层，说明「按子层峰值放大素材池」那个绕法不再必要（`mine_replica_assets.py` 里的绕过未改，只是不再需要）。

### D4 抠像层丢绑定后留同源不透明副本（`apply_replica_template.py`）

一句话：二选一里选「不留同源副本」那条——先按 `material_fingerprint` 尝试**真正重建**（在主轨找同素材、且新时间轴上同时上屏的段当锚），找不到就**彻底不分配素材**：`plan.pop()` + 计 `cutout_layer_dropped_no_binding`，并在写段后对账 `cutout_dropped_still_rendered`（应为 0）。

证据（style_09 / 11 / 16 / 19）：4 套各丢 1/3/1/1 层，`cutout_dropped_detail` 里逐层记了 key + 原版指纹（例：`seg:5:2 fp:42280c450f0e`、`seg:4:1 fp:1337a18807e8`）；这 4 套 `cutout_dropped_still_rendered = 0`，草稿里也查不到对应落段。

### D5 照片竖条被误判成背景板（`apply_replica_template.py`）

一句话：`_backdrop_of_cutout()` 加两条**证据门槛**——原版那层必须是**视频**且**真带模糊特效**（`effects_adjust_blur > 0`，与 verify 的「同源+同入点+sigma≥8」豁免同口径）；照片竖条 / 无模糊层一律不当板，分别计 `backdrop_rejected_photo` / `backdrop_rejected_not_blurred`。

证据：style_07 `seg:4:1`（kind=photo + 线性蒙版 + 无模糊）→ `backdrop_rejected_photo 1`、backdrop_rebuilt 1→0；style_16 `seg:1:0/1:1`（photo）→ 拒 2；style_19 `seg:1:0/3:0`（video 但无模糊）→ 拒 2。
连带修掉一处会让"照片层"变形的空洞：顶层照片层原来没走 `_aspect_crop`（只有复合片段子层走了），换图后覆盖面积差好几倍（style_16 `seg:1:1` 原版 cover 1.006 → 未裁 3.17 = 三倍画面；style_07 `seg:4:0` 原版 0.094 → 未裁 0.441）。补上后逐层 cover 与原版逐位一致（07：0.094/1.44；16：1.0/1.006）。

### D6 重影判据缺第三条豁免（`verify_replica_template.py`）

一句话：`check_overlay_ghosting()` 加第三条**带证据**的豁免——逐对回 `template.json` 核「原版同一时刻本来就有 ≥2 层同指纹视频/照片层」（草稿段 id → `segment_map` → 模版段 key；两侧 `material_fingerprint` 相同 + 原版时间窗真重叠 + 该时刻同指纹层 ≥2），证据写进 `detail[*].designed_same_source`（哪两层、指纹前缀、原版时刻、重叠秒数）。拿不出证据的同源叠层照判 FAIL。

本轮被这条豁免放行的对（全部带证据）：

| 风格 | 对 | 原版时刻 | 原版同指纹两层 | 指纹前缀 | 重叠 |
|---|---|---|---|---|---|
| style_07 | t0:0 / t3:0 | 2.133s | seg:0:0 / seg:5:0 | fp:75553c99a | 4.27s |
| style_09 | t0:4 / t2:2 | 5.933s | seg:0:4 / seg:5:3 | fp:8ef39ca35 | 1.73s |
| style_11 | t0:0 / t1:0 | 0.967s | seg:0:0 / seg:4:0 | fp:55744e8bd | 1.93s |
| style_11 | t0:2 / t2:0 | 4.792s | seg:0:2 / seg:5:0 | fp:55744e8bd | 1.35s |
| style_11 | t0:3 / t3:0 | 6.183s | seg:0:3 / seg:6:0 | fp:55744e8bd | 1.43s |
| style_11 | t0:14 / t1:1 | 20.117s | seg:0:14 / seg:4:3 | fp:55744e8bd | 1.13s |
| style_11 | t0:15 / t1:2 | 22.292s | seg:0:15 / seg:4:4 | fp:55744e8bd | 3.22s |
| style_16 | t0:1 / t1:0 | 3.908s | seg:0:1 / seg:5:0 | fp:811f349f8 | 2.98s |
| style_16 | t0:7 / t1:1 | 15.658s | seg:0:7 / seg:5:1 | fp:811f349f8 | 1.32s |
| style_16 | t0:8 / t1:2 | 17.183s | seg:0:8 / seg:5:2 | fp:811f349f8 | 1.73s |
| style_19 | t0:0 / t1:0 | 0.450s | seg:0:0 / seg:4:0 | fp:6e140ab4c | 0.90s |
| style_19 | t0:1 / t1:1 | 1.908s | seg:0:1 / seg:4:1 | fp:6e140ab4c | 2.02s |
| style_22 | t0:0 / t1:0 | 0.750s | seg:0:0 / seg:3:0 | fp:b8486ddcb | 1.50s |
| style_22 | t0:10 / t1:3 | 14.458s | seg:0:10 / seg:3:3 | fp:b8486ddcb | 2.28s |
| style_22 | t1:5 / t2:1 | 20.583s | seg:3:5 / seg:5:1 | fp:4fe0ba595 | 0.97s |

style_22 的那一对 MAE 0.0208（阈值 0.02）走的就是「同源放行」，不是放宽阈值。

另修一处点检前提：`overlay_left_blank_detail` 里被拒接层的点检原来一律要求「成片 == 只渲主轨」，
但该时刻若另有**原版设计的叠层**在场，像素差本来就不归被拒接层（style_11 4.79s：被拒接的照片抠像层
+ 原版画中画 seg:5:0 同场，mae 0.2303）。现在逐点分开取证：可分辨时才比像素；不可分辨时改用
**结构证据**（该层模版 key 在草稿里无落段），并把覆盖该点的层写进 `rejected_layer_spots[*].covered_by`
（style_11 → `pixel_decisive: false, layer_absent_from_draft: true, covered_by: [seg:5:0]`）。
结构证据对两种情况都做，所以「说跳过但画面里还在」照样会被拦。

## 三、没修干净 / 仍未过的

1. **z 序防线（style_07 / 16 / 19 仍 check_fail，ghost 已全过）**：这不是四条缺陷之一，也不是新引入的视觉问题，但数字变差了，如实列：
   | 风格 | z_ok（前→后） | top_layer_not_visible（前→后） | 说明 |
   |---|---|---|---|
   | style_07 | True → False | 0 → 3 | 改前 3 对全是 `solo_render_failed`（没测到就"通过"）；本轮 jobs=1 单渲成功，量到 ncc 0.311/0.329/0.331。同一批对的**原版侧同样不可见**（orig visible 1/5、0/8、0/6）——照片竖条是带线性蒙版+羽化的软边层 |
   | style_16 | False → False | 1 → 3 | 原版侧单渲出来是纯色块（blocks=1、ncc=[]），新侧 ncc 0.033–0.089；两侧都识别不出，属 z 序点检自身的口径问题 |
   | style_19 | False → False | 3 → 1 | 改前 3，改后 1（0.0–0.9s 两对从 ncc −0.152 升到 0.59 → 可见） |
   未动阈值、未动 z 序判据；补 `_aspect_crop` 之后新层 cover 与原版**逐位一致**（07：0.094/1.44；16：1.0/1.006；19：1.69/1.0），几何无误。
2. `z_order_mismatch`（apply 自报）6 套均未变（4/3/0/9/2/2）——按缺陷清单附注，这个计数把不渲染的轨也算进去了，口径待主链澄清，本轮未动。
3. style_16 仍有 `font_fallback_slots: 3`（本机缺原版字体），属已知观感问题，非缺陷。

## 四、验证

- 全量测试：`.short-video-studio/venv/bin/python -m unittest discover -s tests -q` → `Ran 158 tests in 24.493s` `OK (skipped=1)`。
- 新增单测（`tests/test_studio.py::ReplicaTemplateTests`）：子层避让窗口、抠像丢弃/指纹重绑、照片不当背景板、有模糊视频仍当背景板、原版同指纹证据、点检结论口径、照片层展示宽高比。
- 隐私门禁：`grep -rn 'JianyingPro\|/Users/\|com\.lveditor\.draft\|Movies/' short-video-studio/scripts/*.py dev_tools/*.py` 命中数与改前一致（仅 `apply_stickers_to_replica.py` 注释、`audit_consistency.py` 的检查逻辑两处既有命中）。
