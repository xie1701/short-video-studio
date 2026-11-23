# 24 套批量链：必须改主链才能解决的缺陷清单（2026-11-24）

跑法：`python dev_tools/run_template_batch.py --all --jobs 3`
产物：`~/Downloads/template_batch_24/<style_id>/{final.mp4,report.json,frames/}`（每套一个子目录，24 套齐）
汇总：[`template_batch_2026-11-24.csv`](template_batch_2026-11-24.csv) · [`template_batch_2026-11-24.md`](template_batch_2026-11-24.md)

结果分布：**成功 10 · 检查未过 6 · 渲染失败 8 / 24 套**。
下面 5 条是失败与检查未过的**根因**，全部落在主链（`render_replica_from_draft.py` /
`apply_replica_template.py`），我这两支工具（`run_template_batch.py` / `mine_replica_assets.py`）
里无法绕过——除了 D3，我已在 `mine_replica_assets.py` 里按子层峰值放大素材池绕过。

> 说明：批跑期间 `render_replica_from_draft.py` / `apply_replica_template.py` / `replica_template.py`
> 被并行推进（`1dcab30 → c3ae900 → bcc90ec`）。下列缺陷都用 `git show <rev>:<file>` 核过，
> **在 `1dcab30` 就存在**，不是并行改动引入的。

---

## D1（7 套被挡）`clip.media_path` 没转 `str` → 写 EDL 时 JSON 崩

**位置**：`dev_tools/render_replica_from_draft.py`
- L1826 `"media_path": src,`（主轨简易段）
- L2002 `"media_path": _piece_src,`（主轨分段/遮罩段；`_piece_src = src` 时也是 `Path`）
- 落点：L2052 `write_json(pdir / "04_edl.json", edl)`

**为什么现在才炸**：`src` 在两条路径下是 `PosixPath`——① 主轨出现**照片段**（`materialize_photo()`
返回 `Path`，L1639-1647）；② 主轨出现**复合片段**（`compound_media[key] = sub_mp4` 是 `Path`，L1658/1684，
被 L1718 取出）。旧版套用端把「驱动轨上的复合片段」当普通素材换掉，所以主轨上从来不会有复合片段；
`c3ae900` 修好驱动轨复合片段后，这条老路径第一次被跑到。

**命中**：style_01 / 05（主轨照片）、08 / 10 / 15 / 20 / 23（主轨复合片段）= 7 套

**复现**（一条命令产出证据）：
```bash
python dev_tools/mine_replica_assets.py \
  --catalog ~/Downloads/template_batch_24/_pool/pool_catalog.json \
  --style-id style_05 --assets /tmp/a5.json --pool /tmp/p5.json
python dev_tools/verify_replica_template.py --style-id style_05 \
  --assets-json /tmp/a5.json --pool-json /tmp/p5.json --out /tmp/r5
# -> [verify] 渲染失败: TypeError: Object of type PosixPath is not JSON serializable
```

**证据**：`~/Downloads/template_batch_24/style_05/report.json` → `steps.render.error`；
定位到具体 clip 字段（把 `common.write_json` 包一层就打出 `$.clips[0].media_path -> .../photo_00E59D52.mp4`）。

**验证过的补法**（我只在 `/tmp` 副本上验，没动仓库）：把两处改成 `str(src)` / `str(_piece_src)` 后，
EDL 写成功、渲染真正开跑（style_05 跑到 100s）——说明就卡这一处，但同时暴露 D2。

---

## D2（同一条路上的第二步）主轨照片/复合片段进「遮罩 / 抠像合成」时 ffmpeg 卡死或尺寸不匹配

修掉 D1 之后 new 出来的，两条独立表现：

1. **卡死**（style_04）：`masked_media()` 拼的那条 ffmpeg
   `color=...:d=0.05 -ss 3.1334 -t 0.05 -i compound_alpha.mov -loop 1 -i mask_alpha_*.png ...
    overlay=...:shortest=1:eof_action=repeat -frames:v 2` 600s 不结束，
    `run_ffmpeg` 超时抛错，而**子进程没被杀**——事后 `ps` 看到它 773% CPU 空转 37 分钟。
   证据：`style_04/report.json` → `steps.render.error`（整条命令在里头）。
2. **尺寸不匹配**（style_05，打了 D1 补丁之后）：
   `[Parsed_alphamerge_20] Input frame sizes do not match (1340x2380 vs 1338x2380)`
   → `Error reinitializing filters` → 成片 0 字节。前景与 alpha 蒙版的宽高取整口径不一致（差 2px）。

**影响**：即使修好 D1，主轨带复合片段/照片的 8 套仍需要这一条才能出片。

---

## D3（已在我的工具里绕过）复合片段子层的 `avoid` 口径太粗：按父段整个窗口挡素材

**位置**：`short-video-studio/scripts/apply_replica_template.py`
- L1782（照片子层）、L1814（视频子层）：
  `avoid = _taken_near(parent_start, parent_dur) | {x["path"] for x in local_taken}`

**后果**：父段窗口里用过的素材对**同父段的所有子层**都禁用，于是「一个复合片段最多只能填满
`len(池)` 个子层」。style_23 的复合片段是 25 段**顺序播放**（同一时刻只上 1 层），本该复用同一批素材，
却被挡成 45 层留空 → 子时间线拼成空片 → `Output file does not contain any stream`，整片渲染失败。

**证据（改前）**：`style_23` 首跑 `steps.compounds.compounds_skipped = {"sub_video_layer_no_asset": 45}`、
`sub_segments_total 209`、渲染报 `命令执行失败 (234) ... Output file does not contain any stream`。

**我的绕过**（`dev_tools/mine_replica_assets.py::template_need`）：把「单个复合片段最多几层视频子层」
算进素材池需求（`max_sub_video_layers_per_compound`），池子按它放大。重跑后：
```
style_23: compounds 12→12 · compounds_skipped {} · sub_segments_total 254 · 池 A/B 27/27
style_15: compounds 16→16 · compounds_skipped {"sub_unknown_layer_kind": 3} · sub_segments_total 302 · 池 A/B 25/25
```
（style_15 这组数字与你这边的补丁实测一致：`compounds_applied 3→16`、只剩 `sub_unknown_layer_kind: 3`。）

**主链该改的**：`avoid` 用**子层自己的时间窗**（`[t0, t0+dur)`），而不是父段整窗；
或者至少对「与已放子层时间不重叠」的子层放行复用。否则长复合片段要么永远填不满，要么素材池被迫放大到
接近整个库（style_08 单个复合片段 40 层，已把池子撑到 42 条）。

---

## D4（真缺陷）抠像层 `no_main_binding` 跳过 → 画面留下同源不透明副本（重影）

**位置**：`short-video-studio/scripts/apply_replica_template.py` L1494-1500
`if not (linked and linked.get("asset")): _blank_overlay(...); cutout_layers_skipped["no_main_binding"]++`

**后果**：抠像层没重建，但原版那一层是「主轨同一条片子的人物抠出来压在模糊底上」；少掉它之后，
同源的另一份不透明画面留在原位 → 同一张脸/同一场景被画两遍。

**证据**：
- style_09：`cutout_layers_rebuilt 0`、`cutout_layers_skipped {"no_main_binding": 1}`；
  `ghost_check` 在 5.93s 抓到一对同源叠层（MAE 0.09，人脸 1/0）→ FAIL。
- style_11：`cutout_layers_rebuilt 3`、`skipped {"no_main_binding": 3}` → 4.79s / 6.18s 两对 FAIL（MAE 0.23 / 0.17）。
- style_16 / style_19 各有 1 层 `no_main_binding`，与 D5 叠加造成 6 / 10 对 FAIL。

**要主链回答的**：这些层为什么绑不到主轨（`same_material_as` 指向的锚段在新草稿里没上？
锚在主轨但素材为空？锚在复合片段内层？）——先定口径，再决定是补绑还是留空并在成片上收掉同源副本。

---

## D5（真缺陷）背景板重建把「带线性蒙版的照片层」也当背景板 → 全屏同源副本

**位置**：`short-video-studio/scripts/apply_replica_template.py`
`_backdrop_of_cutout()`（L1496 附近）：判据只有「不透明 + 时间窗大半被更高轨抠像层盖住」。

**后果**：模板里那种「kind=photo + `mask`=线性蒙版（无模糊）」的**照片竖条**被误判成抠像层的「模糊背景板」，
重建时换成抠像层的视频素材、**丢掉蒙版**、也不加模糊 → 全屏不透明同源第二份画面。

**证据**（style_07）：
- `report.json` → `steps.apply.backdrop_rebuilt_detail`：
  `{"key": "seg:4:1", "effects": [], "same_material_as_cutout": true, "cover_ratio": 0.651}`
- `template.json` 同段：`kind=photo`，`mask={"resource_type": "line", "width": 0.28, "feather": 0.36}`，`effects: []`
- 生成草稿里该段变成 `type=video`、无 mask、scale 1.0
- `ghost_check` 在 3.5s 判 2 对 FAIL（MAE 0.168，人脸 0 → 1，即主轨那张脸被同源副本盖掉了）

**要主链改的**：`_backdrop_of_cutout()` 加两条前置条件——原段必须**带模糊特效**（或原段本身就是模糊底），
且**没有自己的蒙版**；否则按普通照片层走 `photo` 池。`backdrop_rebuilt` 的语义应当与 verify 的豁免
（同源 + 同入点 + sigma≥8）对齐，现在两边不一致。

---

## D6（判据缺豁免）重影检查把「原版设计就是同源」判成 FAIL

`check_overlay_ghosting()`（`dev_tools/verify_replica_template.py`）的例外只有两条：顶层是**实测有透明区的抠像层**、
顶层是**同入点 + 真模糊（sigma≥8）的背景板**。但很多原版设计本身就是「同一段片子再画一遍」：

- style_11 的 track 5/4/6 在原版里就是主轨那条口播的**画中画/分屏**（`template.json` 里
  `same_material_as: seg:0:2`）；套用端按 `content_linked` 正确跟随主轨同素材，却被判重影。
- style_22 的 20.58s 是原版**同一张照片**错开叠两层（`material_fingerprint` 相同），
  套用端按 `duplicate_photo_allowed` 正确放行同图，MAE 0.027 只比阈值 0.02 高一点点。

**核过的口径**：22 个 FAIL 对**全部**落在「原版同刻就有 ≥2 层同指纹视频层」的时刻（脚本逐对核
`template.json` 的时间窗 + `material_fingerprint`）。但其中：
- style_07 2 对 / style_09 1 对 / style_11 2 对 / style_16 6 对 / style_19 10 对 **夹着 D4、D5 的真缺陷**；
- style_22 1 对是纯判据问题（原版同图放行 + 0.027 边际超阈）。

**建议**：给判据加第三条豁免——「这两段在原版里是同一个素材**且套用端按 `content_linked` /
`duplicate_photo_allowed` 显式放行**」，并把该放行记录写进 `ghost_check.detail` 作为证据；
同时把 D4/D5 修掉的层单独计数，不要把两类混在一个 `ghost_rejected` 里。

---

## 附：z 序自报残留计数口径（待主链澄清，非阻断）

`steps.apply.z_order_mismatch` 在 16/24 套里非零（style_16 9 条 / style_20 6 条 / style_03 7 条…），
但像素级 z 序防线（`z_layer_order_audit`）只在 **style_16（1 对）/ style_19（3 对）** 抓到
「说在上却看不见」。两个口径不一致：counter 很可能把「本来就不渲染的轨（effect/sticker）」也算进错位。
建议主链明确 `z_order_mismatch` 的定义（是否只统计参与渲染的轨），否则每套都会挂一个看不出影响的数字。

## 附：字体回落（不是 bug，但影响观感）

`font_fallback_slots` 非零：style_08（70 位全回落，方正超粗黑繁体）、style_12（28 位，后现代体）、
style_16（3）、style_06（1）、style_10（1）。套用端按名就近解析（风格资产 → 系统 → 缓存），
这几个名字在本机都没命中，已逐位记在 `report.json → steps.fonts.font_fallback_slots`。
