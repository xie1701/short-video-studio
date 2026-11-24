# 元素因果规则表 · 数据结构与填写规范（v1）

目的：把 24 套剪映真草稿里「**每个元素为什么出现、为什么是这个参数、它影响谁**」抽成机器可读、可直接喂给 Agent 的规则。
不是盘点「有什么、有多少」，而是**因果与作用域**——只有这一层能当复用时的判断依据。

## 文件

- 分片（由三条子线各自产出，互不覆盖）：`forensic/causality_text.json` / `causality_audio.json` / `causality_visual.json`
- 合并后：`forensic/style_element_causality.json`（`dev_tools/merge_element_causality.py`）
- 运行期副本：`short-video-studio/assets/style_spectrum/style_element_causality.json`（剔掉任何本机路径与逐条文案）
- 人读版：`references/element-causality.md`（由合并脚本从 JSON 渲染，不手写）
- 消费端：`scripts/element_rules.py`（`explain(style_id, element)` 查因果；`check(pipeline_dir)` 逐条比对产出并给出偏离依据）

## 分片顶层

```json
{
  "version": "1.0",
  "domain": "text",
  "generated_from": "forensic/decrypted/<草稿>/ + forensic/text_style_truth.json",
  "styles": {
    "style_21": {
      "source_ref": "wb_<sha256 前 12 位>",
      "samples": 24,
      "elements": { "<element_key>": { ... } }
    }
  }
}
```

`element_key` 取值（各分片只出现自己域内的）：

- text：`subtitle` `keyword_highlight` `flower` `giant_text` `chapter_card` `end_card`
- audio：`sfx` `bgm` `dual_bgm_switch` `voice_ducking`
- visual：`cutout` `speed` `transition` `effect` `sticker` `pip` `overlay_geometry` `in_anim` `out_anim` `color_grade`

## 单个元素（所有字段必填；不适用的写 `null` 并给 `absent_reason`）

```json
{
  "present": true,
  "absent_reason": null,

  "why_appears": {
    "rule": "一句话说清「什么条件下才会出现」",
    "triggers": ["结构性切点", "口播逐句", "数字/价格出现", "指认画面"],
    "not_when": ["空镜", "画面里正给人看图纸", "同一段已出现过"],
    "evidence": "draft track 6 共 24 条，全部落在句首 0±2 帧"
  },

  "value_drivers": [
    {
      "param": "font_size",
      "value": "12 单位 / 1080 下 75px",
      "derived_from": "content.styles[0].size",
      "formula": "px = size × canvas_w / 172.8",
      "why_this_value": "源草稿里这个字段就是 12；不是估的",
      "calibrated_by": "forensic/style_render_truth.json",
      "tolerance": "±1px"
    }
  ],

  "scope": {
    "unit": "句",
    "coverage": "全片口播，逐句一条，不随镜头重置",
    "excludes": "纯 B-roll 段不挂字幕",
    "reset_on": "换句"
  },

  "affects": [
    {"target": "安全区检测", "how": "决定字幕带 y=0.695×H，越界即判不合格"},
    {"target": "花字落位", "how": "花字避让字幕带，二者不得压叠"}
  ],

  "limits": [
    {"name": "max_chars_per_line", "value": 10, "why": "源 24 条里最长 10 字；超了就换行而不是缩字号"}
  ],

  "coupling": [
    {"with": "flower", "type": "mutex_in_frame", "why": "源里花字与字幕从不同帧共存于同一 y 带"}
  ],

  "violation_symptoms": [
    "字号偏小 11% = 把 172.8 当成 192",
    "把背景框画出来但源里 bg_box_alpha=0"
  ],

  "reuse_check": [
    "新素材上这条规则还能不能落实？不能就把理由写进 blocked"
  ],

  "sample_table": [
    {"at_sec": 0.047, "value": "我系牛哥", "note": "首句首条，0 帧对齐"}
  ]
}
```

## 硬要求

1. **每条都要有证据**：`evidence` 里必须能指回具体草稿字段、轨道、条数或测量文件；禁止「一般来说」「通常」。
2. **区分「真值」与「推断」**：拿草稿字段直接读到的写进 `value_drivers[].derived_from`；由像素回标定出来的写 `calibrated_by`；纯推断的不许写进本表。
3. **零本机路径**：`source_ref` 用 `wb_<sha256 前 12 位>`，不得出现 `~/Movies`、`com.lveditor.draft`、草稿目录名。
4. **不适用就交代**：某套风格某元素真值里 0 条 → `present: false` + `absent_reason`（写清「真值 0 段」还是「本机素材没有」）。
5. **`scope` 必须回答「作用到哪些段/哪些轨」**，而不是「全片都用」这种废话。
