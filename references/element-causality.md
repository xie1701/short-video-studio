# 24 套风格 · 元素因果规则表（由脚本渲染，勿手改）

> 这张表回答的是四件事：**这个元素在什么条件下才会出现 → 为什么参数是这个值 → 它作用到哪些段/哪一层 → 它反过来约束了谁**。
> 元素清点（有几个、多密）在 `forensic/style_trigger_rules_*.json` 里；这里只放因果，因为只有因果能当复用时的判断依据。

生成：`dev_tools/merge_element_causality.py`｜分片：['causality_text.json', 'causality_audio.json', 'causality_visual.json']

## 元素索引

| 元素 | 中文 | 域 | 有这套风格的 | 它主要回答的问题 |
|---|---|---|---|---|
| `subtitle` | 字幕 | text | 23/24 | 字幕为什么出现 |
| `keyword_highlight` | 行内高亮 | text | 9/24 | 行内高亮为什么出现 |
| `flower` | 花字 | text | 23/24 | 花字为什么出现 |
| `giant_text` | 巨字 | text | 20/24 | 巨字为什么出现 |
| `chapter_card` | 章节卡 | text | 3/24 | 章节卡为什么出现 |
| `end_card` | 尾卡 | text | 13/24 | 尾卡为什么出现 |
| `sfx` | 音效 | audio | 24/24 | 音效为什么出现 |
| `bgm` | BGM | audio | 24/24 | BGM 为什么出现 |
| `dual_bgm_switch` | 双段 BGM 切换 | audio | 6/24 | 为什么切第二段 BGM |
| `voice_ducking` | 人声闪避 | audio | 24/24 | 什么时候压人声闪避 |
| `cutout` | 抠像 | visual | 18/24 | 这一镜为什么要抠像 |
| `speed` | 变速 | visual | 2/24 | 这一段为什么要变速 |
| `transition` | 转场 | visual | 0/24 | 这里为什么没有转场 |
| `effect` | 特效 | visual | 7/24 | 这里为什么挂特效 |
| `sticker` | 贴纸 | visual | 11/24 | 这里为什么贴贴纸 |
| `pip` | 画中画/缩窗 | visual | 23/24 | 这里为什么开小窗 |
| `overlay_geometry` | 叠加几何 | visual | 24/24 | 这一层为什么摆在这里 |
| `in_anim` | 入场动画 | visual | 24/24 | 元素为什么动进来 |
| `out_anim` | 出场动画 | visual | 13/24 | 元素为什么动出去 |
| `color_grade` | 调色 | visual | 24/24 | 这套为什么这样调色 |


## 字幕（`subtitle`）

**字幕为什么出现**

- **style_01**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [4]）共 49 段，覆盖片长 100%；相邻段无缝率 100%（Δ≤1 帧，按整轨顺序算）；其中 48 段是普通字号字幕（其余 1 段被升级）；y 中位 0.682×H；字幕起点 42% 落在主轨切点 ±2 帧内（到最近切点中位 0.73s）——由语音断句驱动，不跟切点
  - 作用域：句 / 全片口播 49 段（100% 时长），跨镜头不重置、不按镜头分段；本表 48 段为普通字号字幕，另 1 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 92% 的段（styles[0] 块）；另有 54% 的段带更大的 13.6 单位——那是同一条里的行内高亮块（见 keyword_high |
    | `fill_color` | #FFFFFF×44 / #FFE28A×4 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 92%；第二色（#FFE28A）只出现在关键词高亮块里，不是随机换色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 48 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.682×H = 1310px（库记 0.682） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 48 段的 y 中位就是 0.682，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.56px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（48 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 8/12 段都挂动画 |
    | `hold（停留时长）` | 中位 1.32s（0.5–2.83s），每字 189ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.29 字/秒 | 时长不是拍脑袋给的：字数 8 字 × 189ms ≈ 1.32s，跟口播语速走；100% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.682×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_02**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [5]）共 22 段，覆盖片长 98%；相邻段无缝率 95%（Δ≤1 帧，按整轨顺序算）；其中 12 段是普通字号字幕（其余 10 段被升级）；y 中位 0.76×H；字幕起点 75% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 22 段（98% 时长），跨镜头不重置、不按镜头分段；本表 12 段为普通字号字幕，另 10 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 16.0 单位 / 100px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 16.0 单位（100px）出现在 92% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×12 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 12 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.6 / distance 7.0u / point (0.891,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.76×H = 1460px（库记 0.76） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 12 段的 y 中位就是 0.76，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.8px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（12 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 21/29 段都挂动画 |
    | `hold（停留时长）` | 中位 0.93s（0.03–1.2s），每字 152ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 6.56 字/秒 | 时长不是拍脑袋给的：字数 6 字 × 152ms ≈ 0.93s，跟口播语速走；95% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.76×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_03**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [5]）共 82 段，覆盖片长 92%；相邻段无缝率 96%（Δ≤1 帧，按整轨顺序算）；其中 60 段是普通字号字幕（其余 22 段被升级）；y 中位 0.78×H；字幕起点 48% 落在主轨切点 ±2 帧内（到最近切点中位 0.67s）——由语音断句驱动，不跟切点
  - 作用域：句 / 全片口播 82 段（92% 时长），跨镜头不重置、不按镜头分段；本表 60 段为普通字号字幕，另 22 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 10.0 单位 / 62px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 10.0 单位（62px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×60 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 60 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.78×H = 1498px（库记 0.78） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 60 段的 y 中位就是 0.78，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.0px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（60 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 62/87 段都挂动画 |
    | `hold（停留时长）` | 中位 1.17s（0.45–3.08s），每字 178ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.63 字/秒 | 时长不是拍脑袋给的：字数 7 字 × 178ms ≈ 1.17s，跟口播语速走；96% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.78×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；直接套库里的 12.0 单位：本套真实 10.0 单位，字号偏大 -17%，字幕顶进花字带；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）
- **style_04**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [4]）共 17 段，覆盖片长 95%；相邻段无缝率 94%（Δ≤1 帧，按整轨顺序算）；其中 11 段是普通字号字幕（其余 6 段被升级）；y 中位 0.695×H；字幕起点 36% 落在主轨切点 ±2 帧内（到最近切点中位 1.27s）——由语音断句驱动，不跟切点
  - 作用域：句 / 全片口播 17 段（95% 时长），跨镜头不重置、不按镜头分段；本表 11 段为普通字号字幕，另 6 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 91% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×10 / #FFF589×1 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 91%；第二色（#FFF589）只出现在关键词高亮块里，不是随机换色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 11 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.695×H = 1335px（库记 0.695） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 11 段的 y 中位就是 0.695，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.6px |
    | `entry_anim` | 1/11 段挂了动画 [['in:打字光标', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（11 段里只有 1 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 13/14 段都挂动画 |
    | `hold（停留时长）` | 中位 1.13s（0.4–1.9s），每字 200ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.0 字/秒 | 时长不是拍脑袋给的：字数 5 字 × 200ms ≈ 1.13s，跟口播语速走；94% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.695×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_05**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [4]）共 18 段，覆盖片长 100%；相邻段无缝率 100%（Δ≤1 帧，按整轨顺序算）；其中 7 段是普通字号字幕（其余 11 段被升级）；y 中位 0.721×H；字幕起点 71% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 18 段（100% 时长），跨镜头不重置、不按镜头分段；本表 7 段为普通字号字幕，另 11 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×7 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 7 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.721×H = 1385px（库记 0.721） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 7 段的 y 中位就是 0.721，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.68px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（7 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 13/21 段都挂动画 |
    | `hold（停留时长）` | 中位 1.02s（0.73–1.78s），每字 178ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.61 字/秒 | 时长不是拍脑袋给的：字数 7 字 × 178ms ≈ 1.02s，跟口播语速走；100% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.721×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（time_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；直接套库里的 24.0 单位：本套真实 12.0 单位，字号偏大 -50%，字幕顶进花字带；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）
- **style_06**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [4]）共 10 段，覆盖片长 93%；相邻段无缝率 78%（Δ≤1 帧，按整轨顺序算）；其中 9 段是普通字号字幕（其余 1 段被升级）；y 中位 0.693×H；字幕起点 22% 落在主轨切点 ±2 帧内（到最近切点中位 3.4s）——由语音断句驱动，不跟切点
  - 作用域：句 / 全片口播 10 段（93% 时长），跨镜头不重置、不按镜头分段；本表 9 段为普通字号字幕，另 1 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 89% 的段（styles[0] 块）；另有 67% 的段带更大的 14.0 单位——那是同一条里的行内高亮块（见 keyword_high |
    | `fill_color` | #FFFFFF×8 / #FFF589×1 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 89%；第二色（#FFF589）只出现在关键词高亮块里，不是随机换色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 9 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.7 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.693×H = 1330px（库记 0.693） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 9 段的 y 中位就是 0.693，标准差极小——字幕从不下移去躲画面；库值与实测相差 -0.56px |
    | `entry_anim` | 8/9 段挂了动画 [['in:逐字显影', 2], ['in:轻微放大', 2]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（9 段里只有 8 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 9/12 段都挂动画 |
    | `hold（停留时长）` | 中位 1.47s（1.27–3.87s），每字 217ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 4.62 字/秒 | 时长不是拍脑袋给的：字数 8 字 × 217ms ≈ 1.47s，跟口播语速走；78% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.693×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_07**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [6]）共 20 段，覆盖片长 90%；相邻段无缝率 89%（Δ≤1 帧，按整轨顺序算）；其中 18 段是普通字号字幕（其余 2 段被升级）；y 中位 0.643×H；字幕起点 67% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 20 段（90% 时长），跨镜头不重置、不按镜头分段；本表 18 段为普通字号字幕，另 2 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 10.0 单位 / 62px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 10.0 单位（62px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×18 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 18 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.7 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.643×H = 1235px（库记 0.643） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 18 段的 y 中位就是 0.643，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.44px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（18 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 11/11 段都挂动画 |
    | `hold（停留时长）` | 中位 0.99s（0.67–2.52s），每字 212ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 4.71 字/秒 | 时长不是拍脑袋给的：字数 5 字 × 212ms ≈ 0.99s，跟口播语速走；89% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.643×H 被占；展示字落进 ±0.05 带就会压字（本套实测 1 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（coexist_same_band）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_08**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [8]）共 37 段，覆盖片长 70%；相邻段无缝率 83%（Δ≤1 帧，按整轨顺序算）；其中 37 段是普通字号字幕（其余 0 段被升级）；y 中位 0.693×H；字幕起点 3% 落在主轨切点 ±2 帧内（到最近切点中位 9.8s）——由语音断句驱动，不跟切点
  - 作用域：句 / 全片口播 37 段（70% 时长），跨镜头不重置、不按镜头分段；37 段全部是普通字号字幕（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×37 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 37 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | none | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 这套字幕不挂阴影（0/37 段有 shadow）：它靠字号/颜色跟画面拉开，不靠投影 |
    | `safe_y（字幕带中心）` | 0.693×H = 1330px（库记 0.693） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 37 段的 y 中位就是 0.693，标准差极小——字幕从不下移去躲画面；库值与实测相差 -0.42px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（37 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 28/33 段都挂动画 |
    | `hold（停留时长）` | 中位 1.07s（0.3–2.4s），每字 150ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 6.67 字/秒 | 时长不是拍脑袋给的：字数 7 字 × 150ms ≈ 1.07s，跟口播语速走；83% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.693×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_09**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [1]）共 27 段，覆盖片长 98%；相邻段无缝率 96%（Δ≤1 帧，按整轨顺序算）；其中 26 段是普通字号字幕（其余 1 段被升级）；y 中位 0.669×H；字幕起点 88% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 27 段（98% 时长），跨镜头不重置、不按镜头分段；本表 26 段为普通字号字幕，另 1 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 65% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×26 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 26 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.669×H = 1285px（库记 0.669） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 26 段的 y 中位就是 0.669，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.52px |
    | `entry_anim` | 3/26 段挂了动画 [['in:向左模糊', 1], ['in:冰雪飘动', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（26 段里只有 3 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 18/20 段都挂动画 |
    | `hold（停留时长）` | 中位 1.45s（0.32–3.5s），每字 176ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.69 字/秒 | 时长不是拍脑袋给的：字数 8 字 × 176ms ≈ 1.45s，跟口播语速走；96% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.669×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_10**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [6]）共 10 段，覆盖片长 64%；相邻段无缝率 44%（Δ≤1 帧，按整轨顺序算）；其中 6 段是普通字号字幕（其余 4 段被升级）；y 中位 0.721×H；字幕起点 67% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 10 段（64% 时长），跨镜头不重置、不按镜头分段；本表 6 段为普通字号字幕，另 4 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×6 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 6 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.721×H = 1385px（库记 0.629） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 6 段的 y 中位就是 0.721，标准差极小——字幕从不下移去躲画面；库值与实测相差 177.32px |
    | `entry_anim` | 1/6 段挂了动画 [['in:放大', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（6 段里只有 1 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 19/20 段都挂动画 |
    | `hold（停留时长）` | 中位 1.02s（0.67–1.43s），每字 166ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 6.02 字/秒 | 时长不是拍脑袋给的：字数 7 字 × 166ms ≈ 1.02s，跟口播语速走；44% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.721×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；直接套库里的 21.1 单位：本套真实 12.0 单位，字号偏大 -43%，字幕顶进花字带；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）
- **style_11**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [7]）共 21 段，覆盖片长 91%；相邻段无缝率 90%（Δ≤1 帧，按整轨顺序算）；其中 15 段是普通字号字幕（其余 6 段被升级）；y 中位 0.695×H；字幕起点 80% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 21 段（91% 时长），跨镜头不重置、不按镜头分段；本表 15 段为普通字号字幕，另 6 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×15 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 15 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.7 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.695×H = 1335px（库记 0.695） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 15 段的 y 中位就是 0.695，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.6px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（15 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 12/16 段都挂动画 |
    | `hold（停留时长）` | 中位 1.17s（0.52–1.58s），每字 183ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.45 字/秒 | 时长不是拍脑袋给的：字数 6 字 × 183ms ≈ 1.17s，跟口播语速走；90% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.695×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_12**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [3]）共 20 段，覆盖片长 91%；相邻段无缝率 95%（Δ≤1 帧，按整轨顺序算）；其中 15 段是普通字号字幕（其余 5 段被升级）；y 中位 0.669×H；字幕起点 60% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 20 段（91% 时长），跨镜头不重置、不按镜头分段；本表 15 段为普通字号字幕，另 5 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 14.0 单位 / 88px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 14.0 单位（88px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×15 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 15 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.7 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.669×H = 1285px（库记 0.669） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 15 段的 y 中位就是 0.669，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.52px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（15 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 10/13 段都挂动画 |
    | `hold（停留时长）` | 中位 1.13s（0.37–2.02s），每字 170ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.88 字/秒 | 时长不是拍脑袋给的：字数 6 字 × 170ms ≈ 1.13s，跟口播语速走；95% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.669×H 被占；展示字落进 ±0.05 带就会压字（本套实测 2 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（coexist_same_band）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_13**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [3]）共 21 段，覆盖片长 95%；相邻段无缝率 90%（Δ≤1 帧，按整轨顺序算）；其中 19 段是普通字号字幕（其余 2 段被升级）；y 中位 0.695×H；字幕起点 79% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 21 段（95% 时长），跨镜头不重置、不按镜头分段；本表 19 段为普通字号字幕，另 2 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 95% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×18 / #B71C1C×1 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 95%；第二色（#B71C1C）只出现在关键词高亮块里，不是随机换色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 19 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.695×H = 1335px（库记 0.695） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 19 段的 y 中位就是 0.695，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.6px |
    | `entry_anim` | 2/19 段挂了动画 [['in:发光模糊', 1], ['out:滚出', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（19 段里只有 2 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 12/12 段都挂动画 |
    | `hold（停留时长）` | 中位 1.23s（0.65–1.75s），每字 185ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.39 字/秒 | 时长不是拍脑袋给的：字数 7 字 × 185ms ≈ 1.23s，跟口播语速走；90% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.695×H 被占；展示字落进 ±0.05 带就会压字（本套实测 1 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（coexist_same_band）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_14**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [3]）共 11 段，覆盖片长 89%；相邻段无缝率 80%（Δ≤1 帧，按整轨顺序算）；其中 7 段是普通字号字幕（其余 4 段被升级）；y 中位 0.721×H；字幕起点 71% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 11 段（89% 时长），跨镜头不重置、不按镜头分段；本表 7 段为普通字号字幕，另 4 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 86% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×7 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 7 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.721×H = 1385px（库记 0.721） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 7 段的 y 中位就是 0.721，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.68px |
    | `entry_anim` | 3/7 段挂了动画 [['in:放大', 2], ['in:向右模糊 II', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（7 段里只有 3 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 12/12 段都挂动画 |
    | `hold（停留时长）` | 中位 1.08s（0.48–4.2s），每字 167ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 6.0 字/秒 | 时长不是拍脑袋给的：字数 6 字 × 167ms ≈ 1.08s，跟口播语速走；80% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.721×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；直接套库里的 15.0 单位：本套真实 12.0 单位，字号偏大 -20%，字幕顶进花字带；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）
- **style_15**：不出现——真值 0 段：这套没有 ASR 字幕轨（全部文字都是展示字轨），逐句字幕规则不适用
- **style_16**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [6]）共 14 段，覆盖片长 89%；相邻段无缝率 77%（Δ≤1 帧，按整轨顺序算）；其中 11 段是普通字号字幕（其余 3 段被升级）；y 中位 0.721×H；字幕起点 73% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 14 段（89% 时长），跨镜头不重置、不按镜头分段；本表 11 段为普通字号字幕，另 3 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 10.0 单位 / 62px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 10.0 单位（62px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×11 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 11 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.721×H = 1385px（库记 0.721） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 11 段的 y 中位就是 0.721，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.68px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（11 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 16/16 段都挂动画 |
    | `hold（停留时长）` | 中位 1.27s（0.83–2.05s），每字 181ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.53 字/秒 | 时长不是拍脑袋给的：字数 7 字 × 181ms ≈ 1.27s，跟口播语速走；77% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.721×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（time_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_17**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [1]）共 12 段，覆盖片长 73%；相邻段无缝率 82%（Δ≤1 帧，按整轨顺序算）；其中 5 段是普通字号字幕（其余 7 段被升级）；y 中位 0.693×H；字幕起点 40% 落在主轨切点 ±2 帧内（到最近切点中位 0.83s）——由语音断句驱动，不跟切点
  - 作用域：句 / 全片口播 12 段（73% 时长），跨镜头不重置、不按镜头分段；本表 5 段为普通字号字幕，另 7 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 8.0 单位 / 50px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 8.0 单位（50px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×5 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 5 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | none | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 这套字幕不挂阴影（0/5 段有 shadow）：它靠字号/颜色跟画面拉开，不靠投影；库记 shadow=True / distance=5.0，与源不符，以源为准 |
    | `safe_y（字幕带中心）` | 0.693×H = 1330px（库记 0.693） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 5 段的 y 中位就是 0.693，标准差极小——字幕从不下移去躲画面；库值与实测相差 -0.56px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（5 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 17/19 段都挂动画 |
    | `hold（停留时长）` | 中位 0.83s（0.63–0.9s），每字 139ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 7.2 字/秒 | 时长不是拍脑袋给的：字数 5 字 × 139ms ≈ 0.83s，跟口播语速走；82% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.693×H 被占；展示字落进 ±0.05 带就会压字（本套实测 1 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（coexist_same_band）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；直接套库里的 15.0 单位：本套真实 8.0 单位，字号偏大 -47%，字幕顶进花字带；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）
- **style_18**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [4]）共 26 段，覆盖片长 87%；相邻段无缝率 84%（Δ≤1 帧，按整轨顺序算）；其中 22 段是普通字号字幕（其余 4 段被升级）；y 中位 0.695×H；字幕起点 68% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 26 段（87% 时长），跨镜头不重置、不按镜头分段；本表 22 段为普通字号字幕，另 4 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 95% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×21 / #FFBF17×1 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 95%；第二色（#FFBF17）只出现在关键词高亮块里，不是随机换色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 22 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.695×H = 1335px（库记 0.695） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 22 段的 y 中位就是 0.695，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.6px |
    | `entry_anim` | 2/22 段挂了动画 [['in:轻微放大', 1], ['in:向左模糊', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（22 段里只有 2 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 23/25 段都挂动画 |
    | `hold（停留时长）` | 中位 1.16s（0.5–1.75s），每字 187ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.34 字/秒 | 时长不是拍脑袋给的：字数 7 字 × 187ms ≈ 1.16s，跟口播语速走；84% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.695×H 被占；展示字落进 ±0.05 带就会压字（本套实测 1 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（coexist_same_band）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_19**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [5]）共 31 段，覆盖片长 97%；相邻段无缝率 97%（Δ≤1 帧，按整轨顺序算）；其中 29 段是普通字号字幕（其余 2 段被升级）；y 中位 0.721×H；字幕起点 66% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 31 段（97% 时长），跨镜头不重置、不按镜头分段；本表 29 段为普通字号字幕，另 2 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 66% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×29 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 29 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.721×H = 1385px（库记 0.721） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 29 段的 y 中位就是 0.721，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.68px |
    | `entry_anim` | 1/29 段挂了动画 [['in:弹簧', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（29 段里只有 1 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 10/12 段都挂动画 |
    | `hold（停留时长）` | 中位 1.12s（0.53–2.25s），每字 172ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.81 字/秒 | 时长不是拍脑袋给的：字数 6 字 × 172ms ≈ 1.12s，跟口播语速走；97% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.721×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_20**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [9]）共 15 段，覆盖片长 55%；相邻段无缝率 57%（Δ≤1 帧，按整轨顺序算）；其中 13 段是普通字号字幕（其余 2 段被升级）；y 中位 0.693×H；字幕起点 54% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 15 段（55% 时长），跨镜头不重置、不按镜头分段；本表 13 段为普通字号字幕，另 2 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 14.0 单位 / 88px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 14.0 单位（88px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×13 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 13 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.693×H = 1330px（库记 0.693） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 13 段的 y 中位就是 0.693，标准差极小——字幕从不下移去躲画面；库值与实测相差 -0.56px |
    | `entry_anim` | 2/13 段挂了动画 [['in:滚入', 1], ['in:向右缓入', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（13 段里只有 2 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 26/29 段都挂动画 |
    | `hold（停留时长）` | 中位 0.93s（0.67–3.43s），每字 175ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.71 字/秒 | 时长不是拍脑袋给的：字数 5 字 × 175ms ≈ 0.93s，跟口播语速走；57% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.693×H 被占；展示字落进 ±0.05 带就会压字（本套实测 3 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（coexist_same_band）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_21**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [9]）共 15 段，覆盖片长 100%；相邻段无缝率 100%（Δ≤1 帧，按整轨顺序算）；其中 15 段是普通字号字幕（其余 0 段被升级）；y 中位 0.695×H；字幕起点 80% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 15 段（100% 时长），跨镜头不重置、不按镜头分段；15 段全部是普通字号字幕（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×15 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 15 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.7 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.695×H = 1335px（库记 0.695） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 15 段的 y 中位就是 0.695，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.6px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（15 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 9/9 段都挂动画 |
    | `hold（停留时长）` | 中位 0.97s（0.47–1.67s），每字 183ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.45 字/秒 | 时长不是拍脑袋给的：字数 5 字 × 183ms ≈ 0.97s，跟口播语速走；100% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.695×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（time_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_22**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [7]）共 24 段，覆盖片长 98%；相邻段无缝率 100%（Δ≤1 帧，按整轨顺序算）；其中 21 段是普通字号字幕（其余 3 段被升级）；y 中位 0.695×H；字幕起点 71% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 24 段（98% 时长），跨镜头不重置、不按镜头分段；本表 21 段为普通字号字幕，另 3 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×21 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 21 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.35 / distance 5.0u / point (0.636,-0 | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.695×H = 1335px（库记 0.695） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 21 段的 y 中位就是 0.695，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.6px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（21 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 15/17 段都挂动画 |
    | `hold（停留时长）` | 中位 0.97s（0.35–1.53s），每字 173ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.78 字/秒 | 时长不是拍脑袋给的：字数 6 字 × 173ms ≈ 0.97s，跟口播语速走；100% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.695×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_23**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [13]）共 14 段，覆盖片长 49%；相邻段无缝率 62%（Δ≤1 帧，按整轨顺序算）；其中 13 段是普通字号字幕（其余 1 段被升级）；y 中位 0.695×H；字幕起点 31% 落在主轨切点 ±2 帧内（到最近切点中位 1.43s）——由语音断句驱动，不跟切点
  - 作用域：句 / 全片口播 14 段（49% 时长），跨镜头不重置、不按镜头分段；本表 13 段为普通字号字幕，另 1 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×13 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 13 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.6 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.695×H = 1335px（库记 0.695） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 13 段的 y 中位就是 0.695，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.6px |
    | `entry_anim` | 1/13 段挂了动画 [['in:渐显', 1]] | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（13 段里只有 1 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 21/21 段都挂动画 |
    | `hold（停留时长）` | 中位 1.47s（0.7–2.2s），每字 225ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 4.44 字/秒 | 时长不是拍脑袋给的：字数 5 字 × 225ms ≈ 1.47s，跟口播语速走；62% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.695×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（time_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）；源 background_alpha=0 却画出底框
- **style_24**：口播每说完一句（ASR 断句点）上一条；句子换则切段，段与段首尾相接不留空帧
  - 不该出现：纯 B-roll / 空镜无人声；片尾静帧；同一句里主轨切点不触发字幕切段
  - 证据：base 轨（track [4]）共 24 段，覆盖片长 89%；相邻段无缝率 91%（Δ≤1 帧，按整轨顺序算）；其中 15 段是普通字号字幕（其余 9 段被升级）；y 中位 0.78×H；字幕起点 100% 落在主轨切点 ±2 帧内（到最近切点中位 0.0s）——这套是切点驱动的重剪风格，字幕起句跟着镜头切
  - 作用域：句 / 全片口播 24 段（89% 时长），跨镜头不重置、不按镜头分段；本表 15 段为普通字号字幕，另 9 段被升级为标题/大字（不适用：无台词的纯 B-roll 段、片尾定格）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 12.0 单位 / 75px @1080 | content.styles[0].size（该轨众数） | px = size × canvas_w / 172.8 = size × 6.25（1080 宽） | 主体 12.0 单位（75px）出现在 100% 的段（styles[0] 块），全片只有这一档字幕字号 |
    | `fill_color` | #FFFFFF×15 | content.styles[i].fill.content.solid.color | RGB 0-1 → #RRGGBB，无渐变、无透明 | 主色占 100%，全片字幕只有这一个色 |
    | `stroke` | none（styles[].strokes 为空） | materials.texts[].styles[].strokes | 有 strokes[] 才画描边，宽度取 strokes[].size；素材级 border_width | 15 段里 0 段有真描边；border_width 恒为 0.08 是剪映面板滑杆记忆值，与渲染无关 |
    | `shadow` | color #000000 / alpha 0.9 / distance 5.0u / point (0.636,-0. | materials.texts[].shadow_distance / shadow_point | offset_px = distance × 2.3（1080 宽）；σ = smoothing × f | 四件套在源里是固定常量，全片不随字号/颜色变；distance=5 换算 11.5px 是「贴纸感」的来源 |
    | `safe_y（字幕带中心）` | 0.78×H = 1498px（库记 0.78） | segment.clip.transform.y | y_ratio = (1 − transform.y) / 2；px = y_ratio × canva | 15 段的 y 中位就是 0.78，标准差极小——字幕从不下移去躲画面；库值与实测相差 0.0px |
    | `entry_anim` | none | segment.extra_material_refs → material_animation | 有 anim 引用才播；动画时长取 animations[].duration（源里只有 0.3/0.5 | 字幕默认不动画（15 段里只有 0 段挂动画，属零星手滑）；动画是展示字的资源——同一套里展示字 18/20 段都挂动画 |
    | `hold（停留时长）` | 中位 1.17s（0.47–2.15s），每字 181ms | target_timerange.duration | dur ≈ ch / cps，cps 中位 5.53 字/秒 | 时长不是拍脑袋给的：字数 7 字 × 181ms ≈ 1.17s，跟口播语速走；91% 的句首紧贴上一句句尾（Δ≤1 帧），说明它由 ASR 断句驱动而不是按秒切 |
  - 影响谁：keyword_highlight←高亮是同一 text 块的第二个 range；字幕换行会把 range 切断，所以先定行再定词；flower 安全区←字幕带 y=0.78×H 被占；展示字落进 ±0.05 带就会压字（本套实测 0 条字幕被压）；B-roll 可读性←字幕压在下 1/3，手部/产品特写出现在这条带上会被遮
  - 耦合：flower（y_band_mutex）；keyword_highlight（same_text_block）
  - 违反症状：字号偏小 11% = 把 172.8 当成 192（75px 会被算成 67px）；直接套库里的 17.8 单位：本套真实 12.0 单位，字号偏大 -32%，字幕顶进花字带；把 border_width=0.08 当描边画出黑边（源 styles[].strokes 为空）


## 行内高亮（`keyword_highlight`）

**行内高亮为什么出现**

- **style_01**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 74% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：27 条文字块含 2 个 style block（宿主：字幕 26 / 展示字 1；占字幕句 54%）；高亮块 1.133× 主体、颜色 #FFE28A；20/27 条贴块末；2/27 条高亮区间含数字或价格符号
  - 作用域：词 / 27 条命中块：字幕宿主 26 条（占字幕句 54%）、展示字宿主 1 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.133× 主体字号（75→85px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 27 条高亮里 26 条都是 1.133×，模板锁死的一个常数；既放大又换色 |
    | `highlight_color` | #FFE28A×26 / #FFFFFF×1 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#FFE28A），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 3 字（2–5） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；20/27 条落在块末、2/27 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 13% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.133 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色
- **style_02**：不出现——真值 2 段带第二个 style range 的强调块——只有 1–2 条，不成规律
- **style_03**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 79% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：14 条文字块含 2 个 style block（宿主：字幕 0 / 展示字 14；占字幕句 0%）；高亮块 1.0× 主体、颜色 #FFF589；11/14 条贴块末；7/14 条高亮区间含数字或价格符号
  - 作用域：词 / 14 条命中块：字幕宿主 0 条（占字幕句 0%）、展示字宿主 14 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.0× 主体字号（62→62px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 14 条高亮里 14 条都是 1.0×，模板锁死的一个常数；没有放大、只换色 |
    | `highlight_color` | #FFF589×13 / #FFFFFF×1 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#FFF589），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 6 字（1–8） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；11/14 条落在块末、7/14 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 0% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.0 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色
- **style_04**：不出现——真值 0 段带第二个 style range 的强调块——这套只用单色单字号
- **style_05**：不出现——真值 0 段带第二个 style range 的强调块——这套只用单色单字号
- **style_06**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 86% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：7 条文字块含 2 个 style block（宿主：字幕 6 / 展示字 1；占字幕句 67%）；高亮块 1.167× 主体、颜色 #FFF589；6/7 条贴块末；0/7 条高亮区间含数字或价格符号
  - 作用域：词 / 7 条命中块：字幕宿主 6 条（占字幕句 67%）、展示字宿主 1 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.167× 主体字号（75→88px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 7 条高亮里 6 条都是 1.167×，模板锁死的一个常数；既放大又换色 |
    | `highlight_color` | #FFF589×7 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#FFF589），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 2 字（2–6） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；6/7 条落在块末、0/7 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 17% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.167 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色
- **style_07**：不出现——真值 0 段带第二个 style range 的强调块——这套只用单色单字号
- **style_08**：不出现——真值 1 段带第二个 style range 的强调块——只有 1–2 条，不成规律
- **style_09**：不出现——真值 1 段带第二个 style range 的强调块——只有 1–2 条，不成规律
- **style_10**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 100% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：4 条文字块含 2 个 style block（宿主：字幕 0 / 展示字 4；占字幕句 0%）；高亮块 1.0× 主体、颜色 #B71C1C；4/4 条贴块末；1/4 条高亮区间含数字或价格符号
  - 作用域：词 / 4 条命中块：字幕宿主 0 条（占字幕句 0%）、展示字宿主 4 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.0× 主体字号（75→75px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 4 条高亮里 4 条都是 1.0×，模板锁死的一个常数；没有放大、只换色 |
    | `highlight_color` | #B71C1C×2 / #FFBF17×1 / #FFFFFF×1 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#B71C1C），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 3 字（2–4） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；4/4 条落在块末、1/4 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 0% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.0 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色
- **style_11**：不出现——真值 1 段带第二个 style range 的强调块——只有 1–2 条，不成规律
- **style_12**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 100% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：3 条文字块含 2 个 style block（宿主：字幕 0 / 展示字 3；占字幕句 0%）；高亮块 1.0× 主体、颜色 #FFA800；3/3 条贴块末；0/3 条高亮区间含数字或价格符号
  - 作用域：词 / 3 条命中块：字幕宿主 0 条（占字幕句 0%）、展示字宿主 3 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.0× 主体字号（88→88px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 3 条高亮里 3 条都是 1.0×，模板锁死的一个常数；没有放大、只换色 |
    | `highlight_color` | #FFA800×3 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#FFA800），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 5 字（5–5） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；3/3 条落在块末、0/3 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 0% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.0 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色
- **style_13**：不出现——真值 1 段带第二个 style range 的强调块——只有 1–2 条，不成规律
- **style_14**：不出现——真值 2 段带第二个 style range 的强调块——只有 1–2 条，不成规律
- **style_15**：不出现——真值 0 段带第二个 style range 的强调块——这套只用单色单字号；另有 3 条是缩小的小字副行（双语/拼音），不是强调
- **style_16**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 67% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：3 条文字块含 2 个 style block（宿主：字幕 0 / 展示字 3；占字幕句 0%）；高亮块 1.0× 主体、颜色 #FFF589；2/3 条贴块末；0/3 条高亮区间含数字或价格符号
  - 作用域：词 / 3 条命中块：字幕宿主 0 条（占字幕句 0%）、展示字宿主 3 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.0× 主体字号（62→62px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 3 条高亮里 3 条都是 1.0×，模板锁死的一个常数；没有放大、只换色 |
    | `highlight_color` | #FFF589×3 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#FFF589），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 4 字（4–5） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；2/3 条落在块末、0/3 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 0% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.0 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色
- **style_17**：不出现——真值 0 段带第二个 style range 的强调块——这套只用单色单字号
- **style_18**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 75% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：4 条文字块含 2 个 style block（宿主：字幕 1 / 展示字 3；占字幕句 5%）；高亮块 1.0× 主体、颜色 #FFBF17；3/4 条贴块末；0/4 条高亮区间含数字或价格符号
  - 作用域：词 / 4 条命中块：字幕宿主 1 条（占字幕句 5%）、展示字宿主 3 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.0× 主体字号（75→75px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 4 条高亮里 4 条都是 1.0×，模板锁死的一个常数；没有放大、只换色 |
    | `highlight_color` | #FFBF17×2 / #FFFFFF×1 / #B71C1C×1 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#FFBF17），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 2 字（2–3） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；3/4 条落在块末、0/4 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 0% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.0 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色
- **style_19**：不出现——真值 1 段带第二个 style range 的强调块——只有 1–2 条，不成规律
- **style_20**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 67% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：3 条文字块含 2 个 style block（宿主：字幕 0 / 展示字 3；占字幕句 0%）；高亮块 1.0× 主体、颜色 #FFF589；2/3 条贴块末；0/3 条高亮区间含数字或价格符号
  - 作用域：词 / 3 条命中块：字幕宿主 0 条（占字幕句 0%）、展示字宿主 3 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.0× 主体字号（88→88px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 3 条高亮里 3 条都是 1.0×，模板锁死的一个常数；没有放大、只换色 |
    | `highlight_color` | #FFF589×2 / #FFBF17×1 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#FFF589），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 3 字（2–5） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；2/3 条落在块末、0/3 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 0% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.0 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色
- **style_21**：不出现——真值 0 段带第二个 style range 的强调块——这套只用单色单字号
- **style_22**：不出现——真值 0 段带第二个 style range 的强调块——这套只用单色单字号
- **style_23**：不出现——真值 0 段带第二个 style range 的强调块——这套只用单色单字号
- **style_24**：句子要压重音时，不另起一条，而是在同一条 text 的 styles[] 追加第二块 style，用 range 只盖那几个字；落点 100% 在块末——是「句尾重音」机制，不是「给数字上色」
  - 不该出现：整句都是结论（没有重音落点）；同一句已经挂了花字
  - 证据：3 条文字块含 2 个 style block（宿主：字幕 0 / 展示字 3；占字幕句 0%）；高亮块 1.0× 主体、颜色 #FFDE00；3/3 条贴块末；0/3 条高亮区间含数字或价格符号
  - 作用域：词 / 3 条命中块：字幕宿主 0 条（占字幕句 0%）、展示字宿主 3 条；只作用于它所在的那一块文本（不适用：不跨句、不跨行；换行后 range 失效）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `highlight_scale` | 1.0× 主体字号（75→75px） | content.styles[1].size / content.styles[0].size | highlight_px = base_px × ratio；行宽也按同一比例增加 | 3 条高亮里 3 条都是 1.0×，模板锁死的一个常数；没有放大、只换色 |
    | `highlight_color` | #FFDE00×3 | content.styles[1].fill.content.solid.color | 纯色填充，不透明 | 高亮色只有一个（#FFDE00），主体色通常是 #FFFFFF；两者是固定配对，不是每组随机 |
    | `range_len` | 3 字（2–3） | content.styles[1].range = [start,end] | range 长度 = end − start；range 必须以字符下标为单位，不能用像素 | 高亮只盖句子里最值钱的那 2–4 个字，不整句变色；3/3 条落在块末、0/3 条是数字/价格（两端最容易做强调的位置） |
  - 影响谁：字幕行宽←放大 0% 会让同一行的像素宽增加；长句要提前断行，否则高亮词被挤到下一行；max_chars_per_line←按高亮后的宽度算，不按原字号算
  - 耦合：subtitle（same_text_block）；flower（mutex_in_frame）
  - 违反症状：把高亮做成独立字幕轨 → 同一帧两行叠字（源里所有高亮都在同一个 text 块的 range 里）；比例写成 1.16（库记）而源是 1.0 → 每行宽多出约 2px，12 字行会换行；高亮 range 跨换行 → 后半句整句变色


## 花字（`flower`）

**花字为什么出现**

- **style_01**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：10 条展示字，1.8/min；触发构成 {'number': 0, 'chapter': 1, 'keyword': 2, 'line_echo': 0, 'cut': 0, 'card': 6, 'fragment': 2, 'free': 0}；b-roll 占比 0.909，zones ['middle', 'bottom']
  - 作用域：条 / 10 条独立文字段（每条自占一段轨道），落位 {'top': 1, 'middle': 4, 'bottom': 5}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 94px（94–94），1.25× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 全片只有一档 94px，靠颜色和落位区分重点，不靠字号 |
    | `fill_color` | #B71C1C×3 / #FFE28A×2 / #FFFFFF×2 / #FF4C2C×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 4 个色就覆盖了 80% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（10/10 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.3s（1.3–3.0s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.3–3.0s 是兜底；实际中位 1.3s，与字数 4 字对应 |
    | `entry_anim` | 6/10 段挂动画 [['in:渐显', 3], ['in:生长 II', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 1 / middle 4 / bottom 5 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 1, 'middle': 4, 'bottom': 5} |
  - 影响谁：subtitle 安全区←同帧可并存（10/10 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 0 条大字共占展示层：同帧不同带（0/10 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 2/min（逻辑 cue），超了就变弹幕；把花字字号改成比字幕小：源里是 1.25× 字幕基准，改小就退化成第二行字幕
- **style_02**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：18 条展示字，12.2/min；触发构成 {'number': 0, 'chapter': 13, 'keyword': 1, 'line_echo': 0, 'cut': 4, 'card': 0, 'fragment': 0, 'free': 1}；b-roll 占比 0.158，zones ['bottom', 'top']
  - 作用域：条 / 18 条独立文字段（每条自占一段轨道），落位 {'top': 2, 'middle': 1, 'bottom': 15}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 100px（88–150），1.0× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里就有 5 档字号，字数越少字号越大（字数与字号相关 r=-0.5） |
    | `fill_color` | #FFFFFF×10 / #BCFF00×6 / #B71C1C×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 3 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（17/18 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.1s（0.6–3.03s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–1.4s 是兜底；实际中位 1.1s，与字数 6 字对应 |
    | `entry_anim` | 14/18 段挂动画 [['loop:弹幕滚动', 3], ['in:开幕', 2]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 2 / middle 1 / bottom 15 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 2, 'middle': 1, 'bottom': 15} |
  - 影响谁：subtitle 安全区←同帧可并存（1/18 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 4 条大字共占展示层：同帧不同带（1/18 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 13/min（逻辑 cue），超了就变弹幕；把花字字号统一成 64px：源里同一套有 5 档，统一后重点全平
- **style_03**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：43 条展示字，4.7/min；触发构成 {'number': 7, 'chapter': 2, 'keyword': 12, 'line_echo': 0, 'cut': 10, 'card': 0, 'fragment': 0, 'free': 22}；b-roll 占比 0.189，zones ['top', 'bottom']
  - 作用域：条 / 43 条独立文字段（每条自占一段轨道），落位 {'bottom': 15, 'top': 28}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 88px（62–169），1.42× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 10 档字号（62–169px），字号由元素角色定，不由字数定 |
    | `fill_color` | #FFFFFF×22 / #FFF589×16 / #B71C1C×4 / #FFBF17×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 4 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（43/43 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 2.68s（0.02–18.27s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–2.91s 是兜底；实际中位 2.68s，与字数 8 字对应 |
    | `entry_anim` | 26/43 段挂动画 [['in:向上滑动', 7], ['out:消散', 7]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 28 / middle 0 / bottom 15 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'bottom': 15, 'top': 28} |
  - 影响谁：subtitle 安全区←同帧可并存（10/43 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 44 条大字共占展示层：同帧不同带（5/43 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 5/min（逻辑 cue），超了就变弹幕；把花字字号统一成 60px：源里同一套有 10 档，统一后重点全平
- **style_04**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：3 条展示字，21.5/min；触发构成 {'number': 0, 'chapter': 8, 'keyword': 0, 'line_echo': 0, 'cut': 0, 'card': 0, 'fragment': 0, 'free': 0}；b-roll 占比 0.375，zones ['bottom', 'top']
  - 作用域：条 / 3 条独立文字段（每条自占一段轨道），落位 {'middle': 1, 'bottom': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 100px（56–100），1.33× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 全片只有一档 100px，靠颜色和落位区分重点，不靠字号 |
    | `fill_color` | #FFF589×2 / #B71C1C×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（3/3 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.13s（0.87–1.2s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–1.2s 是兜底；实际中位 1.13s，与字数 8 字对应 |
    | `entry_anim` | 3/3 段挂动画 [['in:向下露出', 1], ['in:打字光标', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 0 / middle 1 / bottom 2 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'middle': 1, 'bottom': 2} |
  - 影响谁：subtitle 安全区←同帧可并存（1/3 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 10 条大字共占展示层：同帧不同带（1/3 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 22/min（逻辑 cue），超了就变弹幕；把花字字号改成比字幕小：源里是 1.33× 字幕基准，改小就退化成第二行字幕
- **style_05**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：5 条展示字，13.4/min；触发构成 {'number': 0, 'chapter': 0, 'keyword': 0, 'line_echo': 0, 'cut': 6, 'card': 0, 'fragment': 0, 'free': 3}；b-roll 占比 0.333，zones ['bottom', 'top']
  - 作用域：条 / 5 条独立文字段（每条自占一段轨道），落位 {'top': 1, 'bottom': 2, 'middle': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 130px（75–297），1.73× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里就有 4 档字号，字数越少字号越大（字数与字号相关 r=-0.92） |
    | `fill_color` | #FFFFFF×3 / #FFBF17×1 / #FFF589×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 3 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（5/5 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.05s（0.02–1.38s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–1.43s 是兜底；实际中位 1.05s，与字数 6 字对应 |
    | `entry_anim` | 1/5 段挂动画 [['in:生长 II', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 1 / middle 2 / bottom 2 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 1, 'bottom': 2, 'middle': 2} |
  - 影响谁：subtitle 安全区←时段互斥（0/5 条与字幕重叠），展示字只出现在字幕空档；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 16 条大字共占展示层：同帧不同带（0/5 条重合）
  - 耦合：subtitle（time_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 14/min（逻辑 cue），超了就变弹幕；把花字字号统一成 190px：源里同一套有 4 档，统一后重点全平
- **style_06**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：10 条展示字，15.2/min；触发构成 {'number': 0, 'chapter': 6, 'keyword': 0, 'line_echo': 0, 'cut': 0, 'card': 4, 'fragment': 0, 'free': 1}；b-roll 占比 0.818，zones ['bottom', 'top']
  - 作用域：条 / 10 条独立文字段（每条自占一段轨道），落位 {'top': 2, 'middle': 1, 'bottom': 7}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 112px（75–125），1.49× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里就有 5 档字号，字数越少字号越大（字数与字号相关 r=-0.72） |
    | `fill_color` | #FFF589×6 / #FFFFFF×4 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（9/10 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.5s（0.03–21.63s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–2.07s 是兜底；实际中位 1.5s，与字数 6 字对应 |
    | `entry_anim` | 7/10 段挂动画 [['out:渐隐', 1], ['in:向右缓入', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 2 / middle 1 / bottom 7 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 2, 'middle': 1, 'bottom': 7} |
  - 影响谁：subtitle 安全区←同帧可并存（8/10 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 0 条大字共占展示层：同帧不同带（0/10 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 16/min（逻辑 cue），超了就变弹幕；把花字字号统一成 60px：源里同一套有 5 档，统一后重点全平
- **style_07**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：3 条展示字，19.2/min；触发构成 {'number': 0, 'chapter': 9, 'keyword': 0, 'line_echo': 0, 'cut': 0, 'card': 0, 'fragment': 0, 'free': 0}；b-roll 占比 0.778，zones ['bottom', 'top']
  - 作用域：条 / 3 条独立文字段（每条自占一段轨道），落位 {'middle': 1, 'bottom': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 127px（127–202），2.05× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 全片只有一档 127px，靠颜色和落位区分重点，不靠字号 |
    | `fill_color` | #B71C1C×2 / #FFBF17×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（3/3 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.42s（1.4–2.75s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.4–1.74s 是兜底；实际中位 1.42s，与字数 7 字对应 |
    | `entry_anim` | 3/3 段挂动画 [['in:滚入', 2], ['in:流光扩散', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 0 / middle 1 / bottom 2 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'middle': 1, 'bottom': 2} |
  - 影响谁：subtitle 安全区←同帧可并存（1/3 条展示字与字幕时间重叠），同 y 带只有 1 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 7 条大字共占展示层：同帧不同带（1/3 条重合）
  - 耦合：subtitle（coexist_same_band）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 20/min（逻辑 cue），超了就变弹幕；把花字字号改成比字幕小：源里是 2.05× 字幕基准，改小就退化成第二行字幕
- **style_08**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：5 条展示字，15.9/min；触发构成 {'number': 0, 'chapter': 3, 'keyword': 3, 'line_echo': 0, 'cut': 2, 'card': 0, 'fragment': 0, 'free': 23}；b-roll 占比 0.0，zones ['bottom', 'middle']
  - 作用域：条 / 5 条独立文字段（每条自占一段轨道），落位 {'bottom': 4, 'top': 1}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 150px（112–150），2.0× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 全片只有一档 150px，靠颜色和落位区分重点，不靠字号 |
    | `fill_color` | #FFFFFF×4 / #FFDE00×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（5/5 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.57s（0.73–58.47s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–1.8s 是兜底；实际中位 1.57s，与字数 8 字对应 |
    | `entry_anim` | 3/5 段挂动画 [['in:打字光标', 1], ['in:缩小', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 1 / middle 0 / bottom 4 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'bottom': 4, 'top': 1} |
  - 影响谁：subtitle 安全区←同帧可并存（1/5 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 25 条大字共占展示层：同帧不同带（3/5 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 16/min（逻辑 cue），超了就变弹幕；把花字字号改成比字幕小：源里是 2.0× 字幕基准，改小就退化成第二行字幕
- **style_09**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：15 条展示字，12.7/min；触发构成 {'number': 4, 'chapter': 5, 'keyword': 3, 'line_echo': 0, 'cut': 4, 'card': 0, 'fragment': 0, 'free': 2}；b-roll 占比 0.611，zones ['middle', 'bottom']
  - 作用域：条 / 15 条独立文字段（每条自占一段轨道），落位 {'middle': 8, 'bottom': 5, 'top': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 75px（44–225），1.0× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 7 档字号（44–225px），字号由元素角色定，不由字数定 |
    | `fill_color` | #FFFFFF×7 / #FF1837×3 / #A4A4A4×1 / #FFBF17×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 4 个色就覆盖了 80% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（12/15 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 2.73s（0.8–42.5s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.05–3.77s 是兜底；实际中位 2.73s，与字数 5 字对应 |
    | `entry_anim` | 13/15 段挂动画 [['in:渐显', 5], ['in:雪光模糊', 2]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 2 / middle 8 / bottom 5 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'middle': 8, 'bottom': 5, 'top': 2} |
  - 影响谁：subtitle 安全区←同帧可并存（14/15 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 4 条大字共占展示层：同帧不同带（1/15 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 13/min（逻辑 cue），超了就变弹幕；把花字字号统一成 48px：源里同一套有 7 档，统一后重点全平
- **style_10**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：9 条展示字，15.9/min；触发构成 {'number': 0, 'chapter': 7, 'keyword': 1, 'line_echo': 1, 'cut': 4, 'card': 0, 'fragment': 0, 'free': 3}；b-roll 占比 0.875，zones ['top', 'middle']
  - 作用域：条 / 9 条独立文字段（每条自占一段轨道），落位 {'top': 5, 'middle': 2, 'bottom': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 131px（88–132），1.75× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 3 档字号（88–132px），字号由元素角色定，不由字数定 |
    | `fill_color` | #FFBF17×5 / #FFFFFF×2 / #F9F3C4×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 3 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（7/9 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.27s（0.78–2.13s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–1.95s 是兜底；实际中位 1.27s，与字数 6 字对应 |
    | `entry_anim` | 9/9 段挂动画 [['in:放大', 2], ['in:向右模糊 II', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 5 / middle 2 / bottom 2 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 5, 'middle': 2, 'bottom': 2} |
  - 影响谁：subtitle 安全区←同帧可并存（4/9 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 9 条大字共占展示层：同帧不同带（3/9 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 16/min（逻辑 cue），超了就变弹幕；把花字字号统一成 131px：源里同一套有 3 档，统一后重点全平
- **style_11**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：8 条展示字，11.4/min；触发构成 {'number': 0, 'chapter': 4, 'keyword': 3, 'line_echo': 0, 'cut': 1, 'card': 0, 'fragment': 0, 'free': 0}；b-roll 占比 0.625，zones ['top', 'bottom']
  - 作用域：条 / 8 条独立文字段（每条自占一段轨道），落位 {'bottom': 3, 'top': 3, 'middle': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 84px（75–125），1.13× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 3 档字号（75–125px），字号由元素角色定，不由字数定 |
    | `fill_color` | #FFFFFF×7 / #B71C1C×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（8/8 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.53s（0.77–3.22s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.04–2.14s 是兜底；实际中位 1.53s，与字数 6 字对应 |
    | `entry_anim` | 4/8 段挂动画 [['in:向下露出', 2], ['in:新年打字机', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 3 / middle 2 / bottom 3 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'bottom': 3, 'top': 3, 'middle': 2} |
  - 影响谁：subtitle 安全区←同帧可并存（3/8 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 6 条大字共占展示层：同帧不同带（0/8 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 12/min（逻辑 cue），超了就变弹幕；把花字字号统一成 84px：源里同一套有 3 档，统一后重点全平
- **style_12**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：11 条展示字，9.2/min；触发构成 {'number': 4, 'chapter': 0, 'keyword': 2, 'line_echo': 0, 'cut': 2, 'card': 0, 'fragment': 0, 'free': 0}；b-roll 占比 0.25，zones ['top', 'middle']
  - 作用域：条 / 11 条独立文字段（每条自占一段轨道），落位 {'top': 3, 'middle': 3, 'bottom': 5}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 106px（75–125），1.2× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 4 档字号（75–125px），字号由元素角色定，不由字数定 |
    | `fill_color` | #FFFFFF×5 / #FFA800×5 / #000000×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 3 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（10/11 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.9s（0.98–10.03s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.19–5.0s 是兜底；实际中位 1.9s，与字数 8 字对应 |
    | `entry_anim` | 8/11 段挂动画 [['in:跃进', 2], ['in:折叠', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 3 / middle 3 / bottom 5 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 3, 'middle': 3, 'bottom': 5} |
  - 影响谁：subtitle 安全区←同帧可并存（7/11 条展示字与字幕时间重叠），同 y 带只有 2 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 1 条大字共占展示层：同帧不同带（1/11 条重合）
  - 耦合：subtitle（coexist_same_band）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 10/min（逻辑 cue），超了就变弹幕；把花字字号统一成 106px：源里同一套有 4 档，统一后重点全平
- **style_13**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：8 条展示字，11.9/min；触发构成 {'number': 1, 'chapter': 7, 'keyword': 0, 'line_echo': 0, 'cut': 1, 'card': 0, 'fragment': 0, 'free': 1}；b-roll 占比 0.8，zones ['middle', 'top']
  - 作用域：条 / 8 条独立文字段（每条自占一段轨道），落位 {'top': 1, 'middle': 2, 'bottom': 5}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 103px（38–112），1.37× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里就有 4 档字号，字数越少字号越大（字数与字号相关 r=-0.68） |
    | `fill_color` | #FFFFFF×4 / #FFBF17×3 / #B71C1C×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 3 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（7/8 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.91s（1.28–3.95s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.27–2.25s 是兜底；实际中位 1.91s，与字数 6 字对应 |
    | `entry_anim` | 8/8 段挂动画 [['in:滚入', 2], ['in:向左模糊', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 1 / middle 2 / bottom 5 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 1, 'middle': 2, 'bottom': 5} |
  - 影响谁：subtitle 安全区←同帧可并存（4/8 条展示字与字幕时间重叠），同 y 带只有 1 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 4 条大字共占展示层：同帧不同带（0/8 条重合）
  - 耦合：subtitle（coexist_same_band）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 12/min（逻辑 cue），超了就变弹幕；把花字字号统一成 103px：源里同一套有 4 档，统一后重点全平
- **style_14**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：10 条展示字，11.5/min；触发构成 {'number': 0, 'chapter': 2, 'keyword': 0, 'line_echo': 0, 'cut': 3, 'card': 0, 'fragment': 0, 'free': 3}；b-roll 占比 0.125，zones ['bottom', 'top']
  - 作用域：条 / 10 条独立文字段（每条自占一段轨道），落位 {'bottom': 6, 'top': 2, 'middle': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 94px（94–131），1.25× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 全片只有一档 94px，靠颜色和落位区分重点，不靠字号 |
    | `fill_color` | #FFFFFF×8 / #FFBF17×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（10/10 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.7s（0.85–3.85s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.12–2.91s 是兜底；实际中位 1.7s，与字数 6 字对应 |
    | `entry_anim` | 10/10 段挂动画 [['in:渐显', 4], ['in:雪光模糊', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 2 / middle 2 / bottom 6 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'bottom': 6, 'top': 2, 'middle': 2} |
  - 影响谁：subtitle 安全区←同帧可并存（3/10 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 2 条大字共占展示层：同帧不同带（0/10 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 12/min（逻辑 cue），超了就变弹幕；把花字字号改成比字幕小：源里是 1.25× 字幕基准，改小就退化成第二行字幕
- **style_15**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：26 条展示字，27.5/min；触发构成 {'number': 0, 'chapter': 9, 'keyword': 1, 'line_echo': 1, 'cut': 3, 'card': 0, 'fragment': 0, 'free': 14}；b-roll 占比 0.357，zones ['middle', 'bottom']
  - 作用域：条 / 26 条独立文字段（每条自占一段轨道），落位 {'bottom': 17, 'top': 4, 'middle': 5}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 100px（38–150），1.06× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里就有 7 档字号，字数越少字号越大（字数与字号相关 r=-0.39） |
    | `fill_color` | #FFFFFF×17 / #FFBF17×4 / #000000×3 / #B71C1C×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 4 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（22/26 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.15s（0.37–30.57s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–1.65s 是兜底；实际中位 1.15s，与字数 4 字对应 |
    | `entry_anim` | 21/26 段挂动画 [['in:渐显', 5], ['in:模糊发光', 4]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 4 / middle 5 / bottom 17 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'bottom': 17, 'top': 4, 'middle': 5} |
  - 影响谁：subtitle 安全区←本套没有 ASR 字幕轨，展示字独占 y 0.69 带，不需要为字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 15 条大字共占展示层：同帧不同带（10/26 条重合）
  - 耦合：subtitle（n/a）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 24/min（逻辑 cue），超了就变弹幕；把花字字号统一成 64px：源里同一套有 7 档，统一后重点全平
- **style_16**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：6 条展示字，6.4/min；触发构成 {'number': 1, 'chapter': 6, 'keyword': 1, 'line_echo': 0, 'cut': 3, 'card': 0, 'fragment': 0, 'free': 2}；b-roll 占比 0.538，zones ['top', 'middle']
  - 作用域：条 / 6 条独立文字段（每条自占一段轨道），落位 {'top': 3, 'middle': 3}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 62px（50–62），1.0× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 全片只有一档 62px，靠颜色和落位区分重点，不靠字号 |
    | `fill_color` | #FFFFFF×6 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 1 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（6/6 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 3.78s（1.13–7.37s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.3–3.24s 是兜底；实际中位 3.78s，与字数 8 字对应 |
    | `entry_anim` | 6/6 段挂动画 [['in:渐显', 3], ['in:打字光标', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 3 / middle 3 / bottom 0 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 3, 'middle': 3} |
  - 影响谁：subtitle 安全区←时段互斥（0/6 条与字幕重叠），展示字只出现在字幕空档；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 10 条大字共占展示层：同帧不同带（0/6 条重合）
  - 耦合：subtitle（time_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 7/min（逻辑 cue），超了就变弹幕；把花字字号改成比字幕小：源里是 1.0× 字幕基准，改小就退化成第二行字幕
- **style_17**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：5 条展示字，18.8/min；触发构成 {'number': 0, 'chapter': 0, 'keyword': 0, 'line_echo': 0, 'cut': 5, 'card': 0, 'fragment': 0, 'free': 7}；b-roll 占比 0.417，zones ['bottom', 'middle']
  - 作用域：条 / 5 条独立文字段（每条自占一段轨道），落位 {'bottom': 5}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 75px（75–112），1.5× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 3 档字号（75–112px），字号由元素角色定，不由字数定 |
    | `fill_color` | #FFFFFF×3 / #FFDE00×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（4/5 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.1s（0.97–1.83s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–1.5s 是兜底；实际中位 1.1s，与字数 7 字对应 |
    | `entry_anim` | 5/5 段挂动画 [['in:滚入', 1], ['in:雪光模糊', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 0 / middle 0 / bottom 5 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'bottom': 5} |
  - 影响谁：subtitle 安全区←同帧可并存（1/5 条展示字与字幕时间重叠），同 y 带只有 1 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 14 条大字共占展示层：同帧不同带（2/5 条重合）
  - 耦合：subtitle（coexist_same_band）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 19/min（逻辑 cue），超了就变弹幕；把花字字号统一成 66px：源里同一套有 3 档，统一后重点全平
- **style_18**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：11 条展示字，9.4/min；触发构成 {'number': 0, 'chapter': 5, 'keyword': 8, 'line_echo': 0, 'cut': 5, 'card': 0, 'fragment': 0, 'free': 2}；b-roll 占比 0.6，zones ['top', 'middle']
  - 作用域：条 / 11 条独立文字段（每条自占一段轨道），落位 {'top': 5, 'middle': 4, 'bottom': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 81px（75–289），1.08× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 6 档字号（75–289px），字号由元素角色定，不由字数定 |
    | `fill_color` | #B71C1C×5 / #FFFFFF×4 / #FFBF17×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 3 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（7/11 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.85s（0.92–2.85s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.03–1.92s 是兜底；实际中位 1.85s，与字数 8 字对应 |
    | `entry_anim` | 10/11 段挂动画 [['in:打字光标', 2], ['in:向右擦除', 2]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 5 / middle 4 / bottom 2 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 5, 'middle': 4, 'bottom': 2} |
  - 影响谁：subtitle 安全区←同帧可并存（6/11 条展示字与字幕时间重叠），同 y 带只有 1 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 11 条大字共占展示层：同帧不同带（2/11 条重合）
  - 耦合：subtitle（coexist_same_band）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 10/min（逻辑 cue），超了就变弹幕；把花字字号统一成 80px：源里同一套有 6 档，统一后重点全平
- **style_19**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：3 条展示字，9.4/min；触发构成 {'number': 3, 'chapter': 1, 'keyword': 3, 'line_echo': 0, 'cut': 1, 'card': 0, 'fragment': 0, 'free': 1}；b-roll 占比 0.667，zones ['top', 'bottom']
  - 作用域：条 / 3 条独立文字段（每条自占一段轨道），落位 {'bottom': 1, 'top': 2}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 94px（50–150），1.25× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 3 档字号（50–150px），字号由元素角色定，不由字数定 |
    | `fill_color` | #FFBF17×1 / #B71C1C×1 / #FFFFFF×1 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 3 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（2/3 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 13.18s（1.43–13.18s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–1.73s 是兜底；实际中位 13.18s，与字数 45 字对应 |
    | `entry_anim` | 2/3 段挂动画 [['in:发光模糊', 1], ['in:新年打字机', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 2 / middle 0 / bottom 1 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'bottom': 1, 'top': 2} |
  - 影响谁：subtitle 安全区←同帧可并存（3/3 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 9 条大字共占展示层：同帧不同带（0/3 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 10/min（逻辑 cue），超了就变弹幕；把花字字号统一成 94px：源里同一套有 3 档，统一后重点全平
- **style_20**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：22 条展示字，25.4/min；触发构成 {'number': 1, 'chapter': 6, 'keyword': 3, 'line_echo': 0, 'cut': 5, 'card': 9, 'fragment': 0, 'free': 2}；b-roll 占比 0.231，zones ['middle', 'bottom']
  - 作用域：条 / 22 条独立文字段（每条自占一段轨道），落位 {'middle': 6, 'bottom': 16}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 112px（38–150），1.27× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里就有 5 档字号，字数越少字号越大（字数与字号相关 r=-0.38） |
    | `fill_color` | #FFFFFF×14 / #FFF589×8 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（20/22 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.2s（0.03–30.67s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.0–2.57s 是兜底；实际中位 1.2s，与字数 5 字对应 |
    | `entry_anim` | 19/22 段挂动画 [['in:水墨晕开', 5], ['in:晕开', 2]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 0 / middle 6 / bottom 16 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'middle': 6, 'bottom': 16} |
  - 影响谁：subtitle 安全区←同帧可并存（10/22 条展示字与字幕时间重叠），同 y 带只有 1 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 4 条大字共占展示层：同帧不同带（1/22 条重合）
  - 耦合：subtitle（coexist_same_band）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 24/min（逻辑 cue），超了就变弹幕；把花字字号统一成 72px：源里同一套有 5 档，统一后重点全平
- **style_21**：不出现——真值 0 段：展示字全部命中更具体的规则（giant_text×8、end_card×1），没有剩下的普通花字
- **style_22**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：12 条展示字，15.3/min；触发构成 {'number': 1, 'chapter': 3, 'keyword': 5, 'line_echo': 0, 'cut': 2, 'card': 0, 'fragment': 0, 'free': 1}；b-roll 占比 0.75，zones ['middle', 'top']
  - 作用域：条 / 12 条独立文字段（每条自占一段轨道），落位 {'middle': 6, 'top': 3, 'bottom': 3}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 93px（75–112），1.24× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里就有 5 档字号，字数越少字号越大（字数与字号相关 r=-0.56） |
    | `fill_color` | #3982F6×3 / #B71C1C×3 / #FFFFFF×2 / #047BFF×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 4 个色就覆盖了 83% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（6/12 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.5s（0.53–2.77s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.11–2.27s 是兜底；实际中位 1.5s，与字数 2 字对应 |
    | `entry_anim` | 10/12 段挂动画 [['in:开幕', 2], ['loop:飘起', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 3 / middle 6 / bottom 3 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'middle': 6, 'top': 3, 'bottom': 3} |
  - 影响谁：subtitle 安全区←同帧可并存（10/12 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 5 条大字共占展示层：同帧不同带（1/12 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 16/min（逻辑 cue），超了就变弹幕；把花字字号统一成 93px：源里同一套有 5 档，统一后重点全平
- **style_23**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：17 条展示字，8.6/min；触发构成 {'number': 0, 'chapter': 14, 'keyword': 0, 'line_echo': 0, 'cut': 2, 'card': 0, 'fragment': 1, 'free': 3}；b-roll 占比 0.6，zones ['bottom', 'middle']
  - 作用域：条 / 17 条独立文字段（每条自占一段轨道），落位 {'top': 3, 'middle': 6, 'bottom': 8}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 75px（75–112），1.0× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 全片只有一档 75px，靠颜色和落位区分重点，不靠字号 |
    | `fill_color` | #FFF589×15 / #FFFFFF×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 2 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（17/17 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 2.13s（1.03–9.53s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.17–3.21s 是兜底；实际中位 2.13s，与字数 4 字对应 |
    | `entry_anim` | 17/17 段挂动画 [['in:渐显', 11], ['in:随机上升', 3]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 3 / middle 6 / bottom 8 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 3, 'middle': 6, 'bottom': 8} |
  - 影响谁：subtitle 安全区←时段互斥（0/17 条与字幕重叠），展示字只出现在字幕空档；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 4 条大字共占展示层：同帧不同带（0/17 条重合）
  - 耦合：subtitle（time_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 9/min（逻辑 cue），超了就变弹幕；把花字字号改成比字幕小：源里是 1.0× 字幕基准，改小就退化成第二行字幕
- **style_24**：展示字只在有可讲内容的锚点上出现：数字/价格、章节头、关键词、文字卡片；纯口播长镜不挂
  - 不该出现：静音段；字幕正带同帧；同一帧已有更大字号元素
  - 证据：8 条展示字，11.5/min；触发构成 {'number': 0, 'chapter': 7, 'keyword': 0, 'line_echo': 0, 'cut': 4, 'card': 0, 'fragment': 0, 'free': 0}；b-roll 占比 0.909，zones ['bottom', 'top']
  - 作用域：条 / 8 条独立文字段（每条自占一段轨道），落位 {'top': 2, 'bottom': 5, 'middle': 1}（不适用：字幕段、giant/chapter/end 卡）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 111px（75–150），1.48× 本套字幕基准 | content.styles[0].size | px = size × canvas_w / 172.8 | 同一套里 6 档字号（75–150px），字号由元素角色定，不由字数定 |
    | `fill_color` | #FFBF17×3 / #FFF589×3 / #FFFFFF×2 | content.styles[].fill.content.solid.color | 纯色；同一套里允许 3–5 个色，但都由模板的 accent 色系派生 | 取色不是随机的：使用次数前 3 个色就覆盖了 100% 的展示字 |
    | `stroke` | none | content.styles[].strokes | 只认 styles[].strokes；素材级 border_width 不作数 | 展示字靠阴影拉对比（8/8 段有阴影），不靠描边；库记 stroke_mode=none |
    | `hold` | 中位 1.59s（1.03–2.55s） | target_timerange.duration | hold ≈ max(字符数/阅读速度, 锚点间隔)；锚点缺失时用 fallback_hold_sec | 展示字停留由它跟的锚点决定：1.18–2.55s 是兜底；实际中位 1.59s，与字数 6 字对应 |
    | `entry_anim` | 6/8 段挂动画 [['in:放大', 3], ['in:激光雕刻', 1]] | segment.extra_material_refs → material_animation | 动画时长取 animations[].duration；源里只有 0.3/0.4/0.5s 三档，与停留 | 展示字几乎条条挂入场动画（这是它和字幕最大的区别）；动画时长是固定档，不随字数变化 |
    | `safe_zone` | top 2 / middle 1 / bottom 5 | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；top<0.35 / middle<0.62 | 落位由画面主体决定：人脸在上就往下压，产品特写在下就往上提；本套实际分布 {'top': 2, 'bottom': 5, 'middle': 1} |
  - 影响谁：subtitle 安全区←同帧可并存（2/8 条展示字与字幕时间重叠），同 y 带只有 0 条——落进带里就是替代式标题，字幕让位；人脸/主体遮挡←落位按主体反打：top 区有脸时改 middle；giant_text←与本套 11 条大字共占展示层：同帧不同带（0/8 条重合）
  - 耦合：subtitle（y_band_mutex）；sfx（co_onset）
  - 违反症状：花字压字幕带 → 两行黄字叠在一起，最典型的「看起来乱」；每句话都挂一个 → 源的上限是 12/min（逻辑 cue），超了就变弹幕；把花字字号统一成 111px：源里同一套有 6 档，统一后重点全平


## 巨字（`giant_text`）

**巨字为什么出现**

- **style_01**：不出现——真值 0 段（无 ≥1.8× 字幕基准的单词块）
- **style_02**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：4 条 ≥1.8× 的字块；0/4 条落在前 5 秒；字号 200px，字数中位 4
  - 作用域：词/行 / 4 条，实际占片长 28%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 200px = 2.0× 字幕基准（100px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 4 条全是 200px（2.0×），同一个模板值，不是逐条调出来的；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 2–5 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.7（1344px），top 占 0% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 4 条里 0 条落 top、0 条落 middle、4 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（1/4 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_03**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：44 条 ≥1.8× 的字块；6/44 条落在前 5 秒；字号 122px，字数中位 3
  - 作用域：词/行 / 44 条，实际占片长 26%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 122px = 1.97× 字幕基准（62px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 44 条里字号跨度 112–438px，换成倍数就是 1.81–7.06×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 1–6 字（中位 3） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.74（1412px），top 占 34% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 44 条里 15 条落 top、0 条落 middle、29 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←5/44 条 大字 与 字幕 同帧且同 y 带，属于「替代式」：占住带的一方让另一方让位；落带内时该镜头字幕让位；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（coexist_same_band）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_04**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：10 条 ≥1.8× 的字块；3/10 条落在前 5 秒；字号 144px，字数中位 4
  - 作用域：词/行 / 10 条，实际占片长 50%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 144px = 1.92× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 10 条里字号跨度 138–450px，换成倍数就是 1.83–5.99×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 2–6 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.67（1291px），top 占 30% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 10 条里 3 条落 top、1 条落 middle、6 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（1/10 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_05**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：16 条 ≥1.8× 的字块；5/16 条落在前 5 秒；字号 163px，字数中位 4
  - 作用域：词/行 / 16 条，实际占片长 48%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 163px = 2.17× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 16 条里字号跨度 150–227px，换成倍数就是 2.0–3.03×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 2–6 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.68（1310px），top 占 12% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 16 条里 2 条落 top、3 条落 middle、11 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（1/16 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（time_mutex）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_06**：不出现——真值 0 段（无 ≥1.8× 字幕基准的单词块）
- **style_07**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：7 条 ≥1.8× 的字块；2/7 条落在前 5 秒；字号 202px，字数中位 5
  - 作用域：词/行 / 7 条，实际占片长 40%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 202px = 3.25× 字幕基准（62px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 7 条里字号跨度 177–349px，换成倍数就是 2.86–5.63×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 4–6 字（中位 5） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.4（778px），top 占 43% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 7 条里 3 条落 top、1 条落 middle、3 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（5/7 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_08**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：25 条 ≥1.8× 的字块；5/25 条落在前 5 秒；字号 150px，字数中位 3
  - 作用域：词/行 / 25 条，实际占片长 35%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 150px = 2.0× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 25 条里字号跨度 150–426px，换成倍数就是 2.0–5.68×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 1–6 字（中位 3） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.69（1330px），top 占 0% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 25 条里 0 条落 top、0 条落 middle、25 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←1/25 条 大字 与 字幕 同帧且同 y 带，属于「替代式」：占住带的一方让另一方让位；落带内时该镜头字幕让位；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（coexist_same_band）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_09**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：4 条 ≥1.8× 的字块；2/4 条落在前 5 秒；字号 150px，字数中位 4
  - 作用域：词/行 / 4 条，实际占片长 14%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 150px = 2.0× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 4 条里字号跨度 150–226px，换成倍数就是 2.0–3.01×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 4–4 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.33（643px），top 占 75% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 4 条里 3 条落 top、0 条落 middle、1 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（3/4 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_10**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：9 条 ≥1.8× 的字块；4/9 条落在前 5 秒；字号 262px，字数中位 4
  - 作用域：词/行 / 9 条，实际占片长 66%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 262px = 3.5× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 9 条里字号跨度 197–397px，换成倍数就是 2.63–5.3×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 1–4 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.16（308px），top 占 56% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 9 条里 5 条落 top、1 条落 middle、3 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（3/9 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_11**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：6 条 ≥1.8× 的字块；2/6 条落在前 5 秒；字号 262px，字数中位 4
  - 作用域：词/行 / 6 条，实际占片长 24%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 262px = 3.5× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 6 条里字号跨度 225–300px，换成倍数就是 3.0–4.0×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 1–4 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.61（1176px），top 占 33% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 6 条里 2 条落 top、1 条落 middle、3 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（1/6 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（y_band_mutex）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_12**：不出现——真值 1 段（只有个别条，不成规律）
- **style_13**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：4 条 ≥1.8× 的字块；2/4 条落在前 5 秒；字号 300px，字数中位 4
  - 作用域：词/行 / 4 条，实际占片长 16%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 300px = 4.0× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 4 条里字号跨度 241–300px，换成倍数就是 3.22–4.0×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 4–4 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.13（259px），top 占 100% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 4 条里 4 条落 top、0 条落 middle、0 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（3/4 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（y_band_mutex）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_14**：不出现——真值 2 段（只有个别条，不成规律）
- **style_15**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：15 条 ≥1.8× 的字块；4/15 条落在前 5 秒；字号 225px，字数中位 2
  - 作用域：词/行 / 15 条，实际占片长 39%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 225px = 2.39× 字幕基准（94px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 15 条里字号跨度 200–350px，换成倍数就是 2.13–3.72×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 2–4 字（中位 2） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.69（1330px），top 占 0% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 15 条里 0 条落 top、3 条落 middle、12 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←源里 大字 与 字幕 从不在同一帧出现（0/15 条重叠）；落带内时该镜头字幕让位；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（n/a）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_16**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：10 条 ≥1.8× 的字块；5/10 条落在前 5 秒；字号 291px，字数中位 3
  - 作用域：词/行 / 10 条，实际占片长 45%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 291px = 4.7× 字幕基准（62px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 10 条里字号跨度 200–500px，换成倍数就是 3.23–8.06×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 1–4 字（中位 3） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.51（974px），top 占 40% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 10 条里 4 条落 top、3 条落 middle、3 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（5/10 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（time_mutex）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_17**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：14 条 ≥1.8× 的字块；7/14 条落在前 5 秒；字号 112px，字数中位 3
  - 作用域：词/行 / 14 条，实际占片长 70%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 112px = 2.25× 字幕基准（50px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 14 条里字号跨度 94–225px，换成倍数就是 1.88–4.5×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 2–6 字（中位 3） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.65（1250px），top 占 7% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 14 条里 1 条落 top、3 条落 middle、10 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←源里 大字 与 字幕 从不在同一帧出现（0/14 条重叠）；落带内时该镜头字幕让位；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（time_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_18**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：11 条 ≥1.8× 的字块；5/11 条落在前 5 秒；字号 225px，字数中位 4
  - 作用域：词/行 / 11 条，实际占片长 36%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 225px = 3.0× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 11 条里字号跨度 150–400px，换成倍数就是 2.0–5.33×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 2–5 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.23（447px），top 占 64% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 11 条里 7 条落 top、1 条落 middle、3 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（9/11 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_19**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：9 条 ≥1.8× 的字块；4/9 条落在前 5 秒；字号 234px，字数中位 5
  - 作用域：词/行 / 9 条，实际占片长 22%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 234px = 3.13× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 9 条里字号跨度 152–449px，换成倍数就是 2.03–5.98×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 3–6 字（中位 5） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.23（450px），top 占 56% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 9 条里 5 条落 top、0 条落 middle、4 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（4/9 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（time_mutex）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_20**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：4 条 ≥1.8× 的字块；1/4 条落在前 5 秒；字号 276px，字数中位 2
  - 作用域：词/行 / 4 条，实际占片长 17%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 276px = 3.14× 字幕基准（88px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 4 条里字号跨度 225–678px，换成倍数就是 2.56–7.7×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 1–3 字（中位 2） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.62（1188px），top 占 0% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 4 条里 0 条落 top、2 条落 middle、2 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（2/4 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_21**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：8 条 ≥1.8× 的字块；3/8 条落在前 5 秒；字号 249px，字数中位 4
  - 作用域：词/行 / 8 条，实际占片长 57%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 249px = 3.33× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 8 条里字号跨度 150–489px，换成倍数就是 2.0–6.52×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 1–5 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.24（467px），top 占 62% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 8 条里 5 条落 top、2 条落 middle、1 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（8/8 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（time_mutex）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_22**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：5 条 ≥1.8× 的字块；3/5 条落在前 5 秒；字号 327px，字数中位 4
  - 作用域：词/行 / 5 条，实际占片长 20%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 327px = 4.36× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 5 条里字号跨度 274–475px，换成倍数就是 3.65–6.33×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 2–5 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.23（446px），top 占 60% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 5 条里 3 条落 top、0 条落 middle、2 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（2/5 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（coexist_same_band）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_23**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：4 条 ≥1.8× 的字块；1/4 条落在前 5 秒；字号 150px，字数中位 3
  - 作用域：词/行 / 4 条，实际占片长 11%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 150px = 2.0× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 4 条里字号跨度 150–825px，换成倍数就是 2.0–10.99×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 1–3 字（中位 3） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.41（779px），top 占 50% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 4 条里 2 条落 top、1 条落 middle、1 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（1/4 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带内；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（time_mutex）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画
- **style_24**：开场钩子 / 分点标题 / 结论词上，用 ≥1.8× 正文字号的单词砸一下；同一镜头最多砸一次（多块时是阶梯出字，不是并排）
  - 不该出现：连续长句段；画面里正给人看关键信息（会互相遮）
  - 证据：11 条 ≥1.8× 的字块；4/11 条落在前 5 秒；字号 157px，字数中位 4
  - 作用域：词/行 / 11 条，实际占片长 43%（并集）；集中在前 5s 与分点处（不适用：字幕带、普通展示字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `font_size` | 中位 157px = 2.1× 字幕基准（75px） | content.styles[0].size | px = size × canvas_w/172.8；判定线 px ≥ 1.8 × base_px | 11 条里字号跨度 150–301px，换成倍数就是 2.0–4.01×；1.8× 是「砸一下」的下限，低于它就和普通展示字分不开 |
    | `chars` | 2–6 字（中位 4） | content.text 的字符数 | char_len，不含换行 | 字越大能塞的字越少；超过 6 字就必须换行，视觉冲击就没了 |
    | `position` | y 中位 0.65（1252px），top 占 36% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 11 条里 4 条落 top、0 条落 middle、7 条压到下部（与字幕同带）；大字位置由主体决定，不是一个固定点 |
  - 影响谁：字幕带←同帧可以并存（2/11 条 大字 与 字幕 时间重叠），但 0 条落进同一 y 带——分层是空间上的，不是时间上的；落带；人脸/主体←大字直接盖在主体上，必须配合抠像或虚化，否则读不清；chapter_card←同一个分点只用一种：要么大字砸，要么小卡列，不叠加
  - 耦合：flower（y_band_mutex）；subtitle（y_band_mutex）
  - 违反症状：把 giant 当普通花字处理 → 字号只有 1.2×，冲击感消失；大字盖住字幕带 → 两行叠字；大字字数超 6 又硬塞一行 → 被压扁/出画


## 章节卡（`chapter_card`）

**章节卡为什么出现**

- **style_01**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_02**：只有脚本里真有分点结构才出：文字以序号前缀（一、/第二/1./(1)）开头，且压住该分点的引入句
  - 不该出现：线性叙事无分点；同一句重复出现但没有序号（那是复挂的展示字/常驻标题，不是章节卡）
  - 证据：6 条全部带序号前缀（数字点号为主），共 3 个分点，3 个被复挂；停留中位 1.07s，y 中位 0.7
  - 作用域：节 / 6 条，分布在 3 个分点；一条分点卡压住该节引入句（不适用：线性叙述段、无序号装饰字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `ordinal_form` | 数字点号×6 | content.text 开头的序号写法 | 文本以 一、/第二/1./(1) 开头 ⟹ 分点卡；否则归 flower | 6 条全部带序号前缀（数字点号为主），3 个标题被复挂——这是「分点讲解」的结构指纹，不是装饰 |
    | `font_size` | 中位 100px（1.0× 字幕基准） | content.styles[0].size | px = size × canvas_w/172.8 | 相对字幕基准 1.0×：并不比正文大——它靠位置（top/middle）和停留时长跟字幕分层，不靠字号 |
    | `hold` | 中位 1.07s（0.8–2.37s） | target_timerange.duration | hold ≥ 覆盖该分点第一句的时长 | 分点卡要压住整个小节的引入句，所以比普通展示字长；短的（<1.2s）在本表里已排除，属于一闪而过的贴纸 |
    | `position` | y 中位 0.7（1344px），top+middel 占 0% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 分点卡当「页眉」：落 top/middle，把底部字幕带整条让出来 |
    | `spacing` | 相邻分点间隔中位 0.77s | target_timerange.start 序列 | 间隔 = 下一条分点起点 − 上一条分点起点 | 分点之间的间隔＝每个小节的长度；间隔过于接近说明只是同一条的复挂，不是新分点 |
  - 影响谁：字幕/花字层级←分点卡落 top/middle，把字幕带让出来；本套 0/6 条与字幕同 y 带；节奏←分点卡出现的位置＝段落切点；它决定后面几个镜头的主题；giant_text←同一条分点只用一种表现形式（大字砸 或 小卡列），不叠加
  - 耦合：giant_text（alternative）；subtitle（y_band_mutex）
  - 违反症状：把每个展示字都当分点卡 → 满屏页眉；分点卡停留太短（<1.2s）→ 观众还没读完就没了；复挂同一分点换字号/换位置 → 观众认不出是同一节
- **style_03**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_04**：不出现——真值 1 段 / 1 个不同序号标题（阈值 ≥3 个分点）——分点不足 3 个，不成规律
- **style_05**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_06**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_07**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_08**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_09**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_10**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_11**：不出现——真值 1 段 / 1 个不同序号标题（阈值 ≥3 个分点）——分点不足 3 个，不成规律
- **style_12**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_13**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_14**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_15**：只有脚本里真有分点结构才出：文字以序号前缀（一、/第二/1./(1)）开头，且压住该分点的引入句
  - 不该出现：线性叙事无分点；同一句重复出现但没有序号（那是复挂的展示字/常驻标题，不是章节卡）
  - 证据：6 条全部带序号前缀（序号汉字为主），共 3 个分点，2 个被复挂；停留中位 1.88s，y 中位 0.53
  - 作用域：节 / 6 条，分布在 3 个分点；一条分点卡压住该节引入句（不适用：线性叙述段、无序号装饰字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `ordinal_form` | 序号汉字×6 | content.text 开头的序号写法 | 文本以 一、/第二/1./(1) 开头 ⟹ 分点卡；否则归 flower | 6 条全部带序号前缀（序号汉字为主），2 个标题被复挂——这是「分点讲解」的结构指纹，不是装饰 |
    | `font_size` | 中位 75px（0.8× 字幕基准） | content.styles[0].size | px = size × canvas_w/172.8 | 相对字幕基准 0.8×：并不比正文大——它靠位置（top/middle）和停留时长跟字幕分层，不靠字号 |
    | `hold` | 中位 1.88s（1.4–3.23s） | target_timerange.duration | hold ≥ 覆盖该分点第一句的时长 | 分点卡要压住整个小节的引入句，所以比普通展示字长；短的（<1.2s）在本表里已排除，属于一闪而过的贴纸 |
    | `position` | y 中位 0.53（1027px），top+middel 占 83% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 分点卡当「页眉」：落 top/middle，把底部字幕带整条让出来 |
    | `spacing` | 相邻分点间隔中位 0.33s | target_timerange.start 序列 | 间隔 = 下一条分点起点 − 上一条分点起点 | 分点之间的间隔＝每个小节的长度；间隔过于接近说明只是同一条的复挂，不是新分点 |
  - 影响谁：字幕/花字层级←分点卡落 top/middle，把字幕带让出来；本套 0/6 条与字幕同 y 带；节奏←分点卡出现的位置＝段落切点；它决定后面几个镜头的主题；giant_text←同一条分点只用一种表现形式（大字砸 或 小卡列），不叠加
  - 耦合：giant_text（alternative）；subtitle（n/a）
  - 违反症状：把每个展示字都当分点卡 → 满屏页眉；分点卡停留太短（<1.2s）→ 观众还没读完就没了；复挂同一分点换字号/换位置 → 观众认不出是同一节
- **style_16**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_17**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_18**：只有脚本里真有分点结构才出：文字以序号前缀（一、/第二/1./(1)）开头，且压住该分点的引入句
  - 不该出现：线性叙事无分点；同一句重复出现但没有序号（那是复挂的展示字/常驻标题，不是章节卡）
  - 证据：3 条全部带序号前缀（第N为主），共 3 个分点，0 个被复挂；停留中位 1.85s，y 中位 0.29
  - 作用域：节 / 3 条，分布在 3 个分点；一条分点卡压住该节引入句（不适用：线性叙述段、无序号装饰字）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `ordinal_form` | 第N×3 | content.text 开头的序号写法 | 文本以 一、/第二/1./(1) 开头 ⟹ 分点卡；否则归 flower | 3 条全部带序号前缀（第N为主），0 个标题被复挂——这是「分点讲解」的结构指纹，不是装饰 |
    | `font_size` | 中位 150px（2.0× 字幕基准） | content.styles[0].size | px = size × canvas_w/172.8 | 相对字幕基准 2.0×：比正文大、比 giant 小，靠字号就能分层 |
    | `hold` | 中位 1.85s（1.7–1.9s） | target_timerange.duration | hold ≥ 覆盖该分点第一句的时长 | 分点卡要压住整个小节的引入句，所以比普通展示字长；短的（<1.2s）在本表里已排除，属于一闪而过的贴纸 |
    | `position` | y 中位 0.29（557px），top+middel 占 100% | segment.clip.transform.y | y_ratio = (1 − transform.y)/2 | 分点卡当「页眉」：落 top/middle，把底部字幕带整条让出来 |
    | `spacing` | 相邻分点间隔中位 6.64s | target_timerange.start 序列 | 间隔 = 下一条分点起点 − 上一条分点起点 | 分点之间的间隔＝每个小节的长度；间隔过于接近说明只是同一条的复挂，不是新分点 |
  - 影响谁：字幕/花字层级←分点卡落 top/middle，把字幕带让出来；本套 0/3 条与字幕同 y 带；节奏←分点卡出现的位置＝段落切点；它决定后面几个镜头的主题；giant_text←同一条分点只用一种表现形式（大字砸 或 小卡列），不叠加
  - 耦合：giant_text（alternative）；subtitle（y_band_mutex）
  - 违反症状：把每个展示字都当分点卡 → 满屏页眉；分点卡停留太短（<1.2s）→ 观众还没读完就没了；复挂同一分点换字号/换位置 → 观众认不出是同一节
- **style_19**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_20**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_21**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_22**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_23**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡
- **style_24**：不出现——真值 0 段 / 0 个不同序号标题（阈值 ≥3 个分点）——这套是线性叙述，没有分点结构；序号只出现在字幕句里，不是卡


## 尾卡（`end_card`）

**尾卡为什么出现**

- **style_01**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：2 条收口卡，最早 65.7s / 片长 68.5s；停留中位 2.77s
  - 作用域：条 / 2 条，只作用于最后 4%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 65.7s（片长 68.5s 的最后 4%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.46（890px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 2.77s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_02**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：1 条收口卡，最早 27.0s / 片长 29.5s；停留中位 1.3s
  - 作用域：条 / 1 条，只作用于最后 8%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 27.0s（片长 29.5s 的最后 8%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.76（1460px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 1.3s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_03**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_04**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_05**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_06**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：2 条收口卡，最早 18.1s / 片长 21.7s；停留中位 2.63s
  - 作用域：条 / 2 条，只作用于最后 16%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 18.1s（片长 21.7s 的最后 16%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.45（872px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 2.63s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_07**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：1 条收口卡，最早 27.1s / 片长 28.1s；停留中位 1.0s
  - 作用域：条 / 1 条，只作用于最后 4%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 27.1s（片长 28.1s 的最后 4%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.58（1121px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 1.0s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_08**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：3 条收口卡，最早 55.5s / 片长 58.5s；停留中位 1.93s
  - 作用域：条 / 3 条，只作用于最后 5%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 55.5s（片长 58.5s 的最后 5%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.71（1370px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 1.93s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_09**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：1 条收口卡，最早 41.3s / 片长 42.5s；停留中位 1.18s
  - 作用域：条 / 1 条，只作用于最后 3%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 41.3s（片长 42.5s 的最后 3%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.7（1335px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 1.18s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_10**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：2 条收口卡，最早 17.0s / 片长 18.9s；停留中位 1.67s
  - 作用域：条 / 2 条，只作用于最后 10%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 17.0s（片长 18.9s 的最后 10%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.69（1315px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 1.67s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_11**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：1 条收口卡，最早 25.4s / 片长 26.4s；停留中位 0.98s
  - 作用域：条 / 1 条，只作用于最后 4%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 25.4s（片长 26.4s 的最后 4%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.43（827px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 0.98s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_12**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：1 条收口卡，最早 25.4s / 片长 26.3s；停留中位 0.93s
  - 作用域：条 / 1 条，只作用于最后 4%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 25.4s（片长 26.3s 的最后 4%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.57（1096px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 0.93s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_13**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_14**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_15**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：2 条收口卡，最早 28.9s / 片长 30.6s；停留中位 0.82s
  - 作用域：条 / 2 条，只作用于最后 5%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 28.9s（片长 30.6s 的最后 5%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.69（1330px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 0.82s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_16**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_17**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_18**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_19**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_20**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：3 条收口卡，最早 27.1s / 片长 30.7s；停留中位 2.57s
  - 作用域：条 / 3 条，只作用于最后 12%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 27.1s（片长 30.7s 的最后 12%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.69（1330px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 2.57s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_21**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：1 条收口卡，最早 12.9s / 片长 15.6s；停留中位 2.72s
  - 作用域：条 / 1 条，只作用于最后 17%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 12.9s（片长 15.6s 的最后 17%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.47（895px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 2.72s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI
- **style_22**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_23**：不出现——真值 0 段（片尾没有独立收口字卡）
- **style_24**：片尾最后 ~15% 出现一组收口/CTA（1–3 条阶梯出字），压到最后一帧；它吃满片尾剩余时长
  - 不该出现：整片无 CTA 的纯纪实；片尾仍有口播时（收口句会跟着字幕走）
  - 证据：1 条收口卡，最早 34.6s / 片长 36.4s；停留中位 1.77s
  - 作用域：条 / 1 条，只作用于最后 5%（不适用：全片免责声明长条（y>0.95、跨全片））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `at_end` | 起点 ≥ 34.6s（片长 36.4s 的最后 5%） | target_timerange.start / draft.duration | t ≥ 0.75 × duration；且 t+du ≥ 0.95 × duration | 收口卡必须压到最后一帧，否则观众在它之前就划走 |
    | `position` | y 中位 0.42（800px） | segment.clip.transform.y | y_ratio = (1 − transform.y)/2；本表限定 0.30–0.92 才算收口卡 | 落中下部：上方留给画面主体/logo，下方不出安全区；贴到 0.95 以上的长条是全片免责声明，不是收口卡 |
    | `hold` | 中位 1.77s | target_timerange.duration | hold = 片尾剩余时长（一般 0.8–3s） | 收口卡不自己定长短，它吃满片尾；短于 0.8s 读不完一句 CTA |
  - 影响谁：片尾 BGM/音效←收口卡起＝BGM 收尾点，二者必须同帧；字幕←收口卡所在时段通常无口播（字幕已结束）；若还有口播，收口句必须先于字幕让位，否则两行叠字
  - 耦合：flower（y_band_mutex）
  - 违反症状：收口卡提前到 80% 处 → 观众以为视频完了，完播率掉；收口卡落进 y>0.95 的安全区外 / 或压住平台 UI


## 音效（`sfx`）

**音效为什么出现**

- **style_01**：1 条里 1 条挂上可观测锚点（100%），挂上的部分是 cut 100%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由；花字上屏：这套的花字不带声音
  - 证据：1 条 sound 段 / 68.5s；触发 cut 100%；锚点偏移中位 0.066s（≈2 帧@30fps）、|偏移| 最大 0.066s；其中 0% 同时贴切点与花字；另有 1 段保留原声（主轨镜头原声@1.367s 0.166s 3.0dB），不计入音效 cue
  - 作用域：cue（一条音效一个事件） / 整片 1 条，分布在 1 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.567, 'p10': 1.567, 'p90': 1.567, 'max': 1.567,  | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.567s（够听清一个动作就收），浸响类最长 1.57s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': -6.47, 'p10': -6.47, 'p90': -6.47, 'min': -6.47,  | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 这套没有一条留在默认 0dB，全部被手动调过；被压低的 1 条（到 -6.5dB）时长中位 1.567s —— 压是给长尾巴/浸响，免得糊住人声 |
    | `action_dur_vs_material` | {'trimmed_share': 0.0, 'src_in_median': 0.0} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 0% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 1.0, 'fade_over_dur_median': 0.383, 'fad | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.38~0.38 × cue_dur | 只有 1/1 条带淡出，且最短的一条也有 1.57s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.066, 'p90': 0.066, 'by_anchor': {'cut': 0.066}} | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 66ms、最大 66ms，0% 落在 ≤1.5 帧内：偏移再大也在 0.12s 容差内，是手动对齐留下的痕迹，不是提前铺 |
  - 影响谁：响度预算（integrated LUFS）←1 条 cue / 68.5s ≈ 0.9/分；源草稿整体被拉到 -14.0 附近；这套音效极少，几乎不影响响度预算；真峰（true peak）←这套没有任何正增益（最高 -6.5dB），真峰不会被音效顶起来；口播干净度←100% 的 cue 压在人声上；同族音效集中在 1–6kHz，落在人声泛音带上方
  - 耦合：cut（shared_instant）；bgm（level_delta）
  - 违反症状：只有 1 个音色却响 1 次（复用 1.0×）= 同一个声音反复撞耳朵，越听越吵；把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；impact 一族占 100% → 再怎么排也是同一个音色反复，听成'只有一种声音'
- **style_02**：20 条里 20 条挂上可观测锚点（100%），挂上的部分是 cut 60% / flower 30% / line 10%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：20 条 sound 段 / 29.467s；触发 cut 60% / flower 30% / line 10%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.1s；其中 30% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 20 条，分布在 1 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.717, 'p10': 0.493, 'p90': 1.134, 'max': 1.267,  | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.717s（够听清一个动作就收），浸响类最长 1.27s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 6.0, 'p10': 0.0, 'p90': 10.012, 'min': -4.07, 'ma | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（25% 的 cue 没动过）；被抬高的 14 条（到 +14.0dB）时长中位 0.667s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 1 条（到 -4. |
    | `action_dur_vs_material` | {'trimmed_share': 0.6, 'src_in_median': 0.167} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 60% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.167s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.05, 'fade_over_dur_median': 0.563, 'fa | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.56~0.56 × cue_dur | 只有 1/20 条带淡出，且最短的一条也有 1.07s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.007, 'by_anchor': {'cut': 0.0, 'flo | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 100ms，90% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←20 条 cue / 29.467s ≈ 40.7/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +14.0dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.667s，靠短时长控住能；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 20%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 40.7/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 40.7/分 = 连击；源里 gap 中位 1.234s，比这更密就成机枪
- **style_03**：53 条里 53 条挂上可观测锚点（100%），挂上的部分是 flower 53% / cut 36% / line 11%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 8% 落在这里，92% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：53 条 sound 段 / 126.867s；触发 flower 53% / cut 36% / line 11%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.067s；其中 28% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 53 条，分布在 3 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 8% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.216, 'p10': 0.63, 'p90': 2.347, 'max': 3.567, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.216s（够听清一个动作就收），浸响类最长 3.57s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -7.964, 'p90': 2.664, 'min': -18.25,  | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（55% 的 cue 没动过）；被抬高的 6 条（到 +6.3dB）时长中位 0.85s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 18 条（到 -18.2 |
    | `action_dur_vs_material` | {'trimmed_share': 0.698, 'src_in_median': 0.167} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 70% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.167s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 67ms，98% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
    | `name_reuse` | {'unique_names': 27, 'cues': 53, 'reuse_ratio': 1.96, 'same_ | materials.audios[].name 去重 + 同名 cue 时间差 | 同一名字复用次数 = cues / unique_names | 这套允许重名（27 个名字撑 53 条），但同一条音效两次之间至少 1.48s；重名不是随手，是同一功能反复用 |
  - 影响谁：响度预算（integrated LUFS）←53 条 cue / 126.867s ≈ 25.1/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬；真峰（true peak）←最高 +6.3dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.85s，靠短时长控住能量）；口播干净度←92% 的 cue 压在人声上；文字类（翻页/键盘）占 11%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 25.1/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 25.1/分 = 连击；源里 gap 中位 1.492s，比这更密就成机枪
- **style_04**：13 条里 13 条挂上可观测锚点（100%），挂上的部分是 flower 46% / line 31% / cut 23%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 8% 落在这里，92% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：13 条 sound 段 / 19.5s；触发 flower 46% / line 31% / cut 23%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 8% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 13 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 8% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.067, 'p10': 0.474, 'p90': 1.54, 'max': 1.867, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.067s（够听清一个动作就收），浸响类最长 1.87s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': 0.0, 'p90': 6.0, 'min': 0.0, 'max': 1 | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（77% 的 cue 没动过）；被抬高的 3 条（到 +19.2dB）时长中位 1.0s —— 抬是给短瞬态，让它从口播里跳出来 |
    | `action_dur_vs_material` | {'trimmed_share': 0.462, 'src_in_median': 0.35} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 46% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.35s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.077, 'fade_over_dur_median': 0.768, 'f | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.77~0.77 × cue_dur | 只有 1/13 条带淡出，且最短的一条也有 1.87s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←13 条 cue / 19.5s ≈ 40.0/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←最高 +19.2dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 1.0s，靠短时长控住能量）；口播干净度←92% 的 cue 压在人声上；文字类（翻页/键盘）占 23%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 40.0/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 40.0/分 = 连击；源里 gap 中位 1.083s，比这更密就成机枪
- **style_05**：10 条里 10 条挂上可观测锚点（100%），挂上的部分是 cut 80% / flower 20%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：10 条 sound 段 / 22.467s；触发 cut 80% / flower 20%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 40% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 10 条，分布在 1 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.775, 'p10': 0.281, 'p90': 1.581, 'max': 2.916,  | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.775s（够听清一个动作就收），浸响类最长 2.92s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -1.07, 'p90': 0.0, 'min': -10.7, 'max | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（90% 的 cue 没动过）；被压低的 1 条（到 -10.7dB）时长中位 1.2s —— 压是给长尾巴/浸响，免得糊住人声 |
    | `action_dur_vs_material` | {'trimmed_share': 0.5, 'src_in_median': 0.067} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 50% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.067s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←10 条 cue / 22.467s ≈ 26.7/分；源草稿整体被拉到 -10.9 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←这套没有任何正增益（最高 0.0dB），真峰不会被音效顶起来；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 10%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 26.7/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 26.7/分 = 连击；源里 gap 中位 2.166s，比这更密就成机枪
- **style_06**：13 条里 13 条挂上可观测锚点（100%），挂上的部分是 flower 54% / line 38% / cut 8%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：13 条 sound 段 / 21.667s；触发 flower 54% / line 38% / cut 8%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 0% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 13 条，分布在 1 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.733, 'p10': 0.507, 'p90': 1.06, 'max': 1.067, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.733s（够听清一个动作就收），浸响类最长 1.07s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -5.816, 'p90': 6.348, 'min': -6.02, ' | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（31% 的 cue 没动过）；被抬高的 4 条（到 +6.7dB）时长中位 0.617s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 5 条（到 -6.0d |
    | `action_dur_vs_material` | {'trimmed_share': 0.692, 'src_in_median': 0.133} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 69% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.133s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.154, 'fade_over_dur_median': 0.719, 'f | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.72~0.72 × cue_dur | 只有 2/13 条带淡出，且最短的一条也有 1.07s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'line': 0.0, 'flow | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←13 条 cue / 21.667s ≈ 36.0/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +6.7dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.617s，靠短时长控住能量；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 15%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 36.0/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 36.0/分 = 连击；源里 gap 中位 1.283s，比这更密就成机枪
- **style_07**：20 条里 17 条挂上可观测锚点（85%），挂上的部分是 cut 71% / flower 24% / beat 6%；余下 3 条无锚点，是编辑随手 accent（额度单独计，不放开成常态）
  - 不该出现：纯静音/无人声段（源里只剩 5% 落在这里，95% 都压在人声上）
  - 证据：20 条 sound 段 / 28.133s；触发 cut 60% / flower 20% / free 15% / beat 5%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.06s；其中 30% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 20 条，分布在 4 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 5% 落在无人声处）；无锚点的句子中段（free 额度 3 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.05, 'p10': 0.645, 'p90': 1.534, 'max': 4.267, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.05s（够听清一个动作就收），浸响类最长 4.27s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -8.954, 'p90': 0.0, 'min': -11.09, 'm | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（55% 的 cue 没动过）；被抬高的 1 条（到 +3.1dB）时长中位 1.117s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 8 条（到 -11.1 |
    | `action_dur_vs_material` | {'trimmed_share': 0.8, 'src_in_median': 0.233} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 80% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.233s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.02, 'by_anchor': {'cut': 0.0, 'free | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 60ms，94% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
    | `name_reuse` | {'unique_names': 12, 'cues': 20, 'reuse_ratio': 1.67, 'same_ | materials.audios[].name 去重 + 同名 cue 时间差 | 同一名字复用次数 = cues / unique_names | 这套允许重名（12 个名字撑 20 条），但同一条音效两次之间至少 0.3s；重名不是随手，是同一功能反复用 |
  - 影响谁：响度预算（integrated LUFS）←20 条 cue / 28.133s ≈ 42.7/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +3.1dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 1.117s，靠短时长控住能量；口播干净度←95% 的 cue 压在人声上；文字类（翻页/键盘）占 5%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：3 条无锚点 cue = 听感里的'不知道为什么响了一声'；把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 42.7/分太多 → 大片没有声音事件，画面在动而耳朵是空的
- **style_08**：29 条里 29 条挂上可观测锚点（100%），挂上的部分是 flower 83% / line 14% / cut 3%；没有一条是无理由的
  - 不该出现：BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：29 条 sound 段 / 58.467s；触发 flower 83% / line 14% / cut 3%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.067s；其中 0% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 29 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 38% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.7, 'p10': 0.393, 'p90': 1.56, 'max': 3.166, 'mi | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.7s（够听清一个动作就收），浸响类最长 3.17s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': 0.0, 'p90': 6.268, 'min': -3.91, 'max | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（76% 的 cue 没动过）；被抬高的 6 条（到 +11.5dB）时长中位 0.567s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 1 条（到 -3.9 |
    | `action_dur_vs_material` | {'trimmed_share': 0.31, 'src_in_median': 0.233} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 31% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.233s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.034, 'fade_over_dur_median': 0.295, 'f | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.29~0.29 × cue_dur | 只有 1/29 条带淡出，且最短的一条也有 0.57s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 67ms，97% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←29 条 cue / 58.467s ≈ 29.8/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +11.5dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.567s，靠短时长控住能；口播干净度←62% 的 cue 压在人声上；文字类（翻页/键盘）占 10%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 29.8/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 29.8/分 = 连击；源里 gap 中位 1.234s，比这更密就成机枪
- **style_09**：17 条里 17 条挂上可观测锚点（100%），挂上的部分是 cut 76% / flower 18% / line 6%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：17 条 sound 段 / 42.5s；触发 cut 76% / flower 18% / line 6%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 41% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 17 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.133, 'p10': 0.643, 'p90': 2.437, 'max': 3.15, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.133s（够听清一个动作就收），浸响类最长 3.15s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -8.022, 'p90': 0.0, 'min': -13.79, 'm | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（53% 的 cue 没动过）；被抬高的 1 条（到 +6.0dB）时长中位 1.183s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 7 条（到 -13.8 |
    | `action_dur_vs_material` | {'trimmed_share': 0.529, 'src_in_median': 0.317} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 53% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.317s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.059, 'fade_over_dur_median': 0.231, 'f | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.23~0.23 × cue_dur | 只有 1/17 条带淡出，且最短的一条也有 0.43s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←17 条 cue / 42.5s ≈ 24.0/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←最高 +6.0dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 1.183s，靠短时长控住能量；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 24%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 24.0/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 24.0/分 = 连击；源里 gap 中位 2.175s，比这更密就成机枪
- **style_10**：15 条里 15 条挂上可观测锚点（100%），挂上的部分是 cut 73% / flower 27%；没有一条是无理由的
  - 不该出现：BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：15 条 sound 段 / 18.917s；触发 cut 73% / flower 27%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 53% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 15 条，分布在 3 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 20% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.933, 'p10': 0.424, 'p90': 2.296, 'max': 3.85, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.933s（够听清一个动作就收），浸响类最长 3.85s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -7.458, 'p90': 0.0, 'min': -10.77, 'm | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（73% 的 cue 没动过）；被抬高的 1 条（到 +4.9dB）时长中位 3.85s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 3 条（到 -10.8d |
    | `action_dur_vs_material` | {'trimmed_share': 0.6, 'src_in_median': 0.117} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 60% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.117s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←15 条 cue / 18.917s ≈ 47.6/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +4.9dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 3.85s，靠短时长控住能量）；口播干净度←80% 的 cue 压在人声上；同族音效集中在 1–6kHz，落在人声泛音带上方
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 47.6/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 47.6/分 = 连击；源里 gap 中位 1.175s，比这更密就成机枪
- **style_11**：13 条里 13 条挂上可观测锚点（100%），挂上的部分是 cut 69% / line 15% / flower 15%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 8% 落在这里，92% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：13 条 sound 段 / 26.4s；触发 cut 69% / line 15% / flower 15%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 31% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 13 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 8% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.734, 'p10': 0.61, 'p90': 1.074, 'max': 1.433, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.734s（够听清一个动作就收），浸响类最长 1.43s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -6.996, 'p90': 0.0, 'min': -10.22, 'm | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（62% 的 cue 没动过）；被压低的 5 条（到 -10.2dB）时长中位 0.983s —— 压是给长尾巴/浸响，免得糊住人声 |
    | `action_dur_vs_material` | {'trimmed_share': 0.769, 'src_in_median': 0.167} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 77% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.167s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'line' | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←13 条 cue / 26.4s ≈ 29.5/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←这套没有任何正增益（最高 0.0dB），真峰不会被音效顶起来；口播干净度←92% 的 cue 压在人声上；文字类（翻页/键盘）占 15%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 29.5/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 29.5/分 = 连击；源里 gap 中位 1.442s，比这更密就成机枪
- **style_12**：16 条里 16 条挂上可观测锚点（100%），挂上的部分是 cut 62% / line 31% / flower 6%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：16 条 sound 段 / 26.3s；触发 cut 62% / line 31% / flower 6%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 44% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 16 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.992, 'p10': 0.55, 'p90': 1.85, 'max': 2.533, 'm | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.992s（够听清一个动作就收），浸响类最长 2.53s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': -1.725, 'p10': -10.275, 'p90': 0.0, 'min': -16.52 | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（50% 的 cue 没动过）；被压低的 8 条（到 -16.5dB）时长中位 0.992s —— 压是给长尾巴/浸响，免得糊住人声 |
    | `action_dur_vs_material` | {'trimmed_share': 0.375, 'src_in_median': 0.15} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 38% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.15s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.125, 'fade_over_dur_median': 0.447, 'f | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.29~0.61 × cue_dur | 只有 2/16 条带淡出，且最短的一条也有 0.93s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'line' | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←16 条 cue / 26.3s ≈ 36.5/分；源草稿整体被拉到 -14.5 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←这套没有任何正增益（最高 0.0dB），真峰不会被音效顶起来；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 12%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 36.5/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 36.5/分 = 连击；源里 gap 中位 1.45s，比这更密就成机枪
- **style_13**：17 条里 17 条挂上可观测锚点（100%），挂上的部分是 cut 41% / line 29% / flower 29%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：17 条 sound 段 / 30.217s；触发 cut 41% / line 29% / flower 29%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 18% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 17 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.033, 'p10': 0.493, 'p90': 1.827, 'max': 2.167,  | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.033s（够听清一个动作就收），浸响类最长 2.17s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -8.336, 'p90': 0.0, 'min': -11.38, 'm | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（65% 的 cue 没动过）；被压低的 6 条（到 -11.4dB）时长中位 1.141s —— 压是给长尾巴/浸响，免得糊住人声 |
    | `action_dur_vs_material` | {'trimmed_share': 0.588, 'src_in_median': 0.15} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 59% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.15s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'line' | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←17 条 cue / 30.217s ≈ 33.8/分；源草稿整体被拉到 -14.9 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←这套没有任何正增益（最高 0.0dB），真峰不会被音效顶起来；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 12%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 33.8/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 33.8/分 = 连击；源里 gap 中位 1.099s，比这更密就成机枪
- **style_14**：10 条里 10 条挂上可观测锚点（100%），挂上的部分是 cut 50% / flower 30% / line 20%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：10 条 sound 段 / 20.9s；触发 cut 50% / flower 30% / line 20%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 20% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 10 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.3, 'p10': 0.347, 'p90': 1.705, 'max': 2.5, 'min | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.3s（够听清一个动作就收），浸响类最长 2.5s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -1.034, 'p90': 0.734, 'min': -10.34,  | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（80% 的 cue 没动过）；被抬高的 1 条（到 +7.3dB）时长中位 2.5s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 1 条（到 -10.3dB |
    | `action_dur_vs_material` | {'trimmed_share': 0.6, 'src_in_median': 0.166} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 60% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.166s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←10 条 cue / 20.9s ≈ 28.7/分；源草稿整体被拉到 -12.6 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←最高 +7.3dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 2.5s，靠短时长控住能量）；口播干净度←100% 的 cue 压在人声上；同族音效集中在 1–6kHz，落在人声泛音带上方
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 28.7/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 28.7/分 = 连击；源里 gap 中位 1.3s，比这更密就成机枪
- **style_15**：33 条里 30 条挂上可观测锚点（91%），挂上的部分是 flower 53% / cut 27% / line 17% / beat 3%；余下 3 条无锚点，是编辑随手 accent（额度单独计，不放开成常态）
  - 不该出现：四类锚点这套都用过（flower / cut / line / beat）——没有'绝对不响'的位置，只有密度与最小间隔在约束它
  - 证据：33 条 sound 段 / 30.567s；触发 flower 48% / cut 24% / line 15% / free 9% / beat 3%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.038s；其中 12% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 33 条，分布在 3 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 15% 落在无人声处）；无锚点的句子中段（free 额度 3 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.6, 'p10': 0.333, 'p90': 0.993, 'max': 1.566, 'm | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.6s（够听清一个动作就收），浸响类最长 1.57s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 3.09, 'p10': 0.0, 'p90': 8.6, 'min': 0.0, 'max':  | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（48% 的 cue 没动过）；被抬高的 17 条（到 +8.6dB）时长中位 0.6s —— 抬是给短瞬态，让它从口播里跳出来 |
    | `action_dur_vs_material` | {'trimmed_share': 0.545, 'src_in_median': 0.233} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 55% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.233s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.061, 'fade_over_dur_median': 0.39, 'fa | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.25~0.53 × cue_dur | 只有 2/33 条带淡出，且最短的一条也有 0.53s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 38ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←33 条 cue / 30.567s ≈ 64.8/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +8.6dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.6s，靠短时长控住能量）；口播干净度←85% 的 cue 压在人声上；文字类（翻页/键盘）占 24%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：3 条无锚点 cue = 听感里的'不知道为什么响了一声'；把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 64.8/分太多 → 大片没有声音事件，画面在动而耳朵是空的
- **style_16**：13 条里 13 条挂上可观测锚点（100%），挂上的部分是 cut 62% / flower 38%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：13 条 sound 段 / 28.333s；触发 cut 62% / flower 38%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 23% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 13 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.083, 'p10': 0.71, 'p90': 2.243, 'max': 2.417, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.083s（够听清一个动作就收），浸响类最长 2.42s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': -5.96, 'p10': -16.432, 'p90': 0.0, 'min': -17.99, | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（46% 的 cue 没动过）；被压低的 7 条（到 -18.0dB）时长中位 1.333s —— 压是给长尾巴/浸响，免得糊住人声 |
    | `action_dur_vs_material` | {'trimmed_share': 0.692, 'src_in_median': 0.133} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 69% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.133s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←13 条 cue / 28.333s ≈ 27.5/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←这套没有任何正增益（最高 0.0dB），真峰不会被音效顶起来；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 15%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 27.5/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 27.5/分 = 连击；源里 gap 中位 1.892s，比这更密就成机枪
- **style_17**：15 条里 14 条挂上可观测锚点（93%），挂上的部分是 cut 57% / flower 36% / line 7%；余下 1 条无锚点，是编辑随手 accent（额度单独计，不放开成常态）
  - 不该出现：BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：15 条 sound 段 / 16.0s；触发 cut 53% / flower 33% / free 7% / line 7%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 33% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 15 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 27% 落在无人声处）；无锚点的句子中段（free 额度 1 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.767, 'p10': 0.487, 'p90': 1.28, 'max': 2.1, 'mi | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.767s（够听清一个动作就收），浸响类最长 2.1s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -6.862, 'p90': 2.754, 'min': -9.98, ' | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（47% 的 cue 没动过）；被抬高的 2 条（到 +4.6dB）时长中位 0.733s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 6 条（到 -10.0 |
    | `action_dur_vs_material` | {'trimmed_share': 0.733, 'src_in_median': 0.167} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 73% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.167s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←15 条 cue / 16.0s ≈ 56.2/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←最高 +4.6dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.733s，靠短时长控住能量；口播干净度←73% 的 cue 压在人声上；文字类（翻页/键盘）占 13%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：1 条无锚点 cue = 听感里的'不知道为什么响了一声'；把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 56.2/分太多 → 大片没有声音事件，画面在动而耳朵是空的
- **style_18**：21 条里 21 条挂上可观测锚点（100%），挂上的部分是 cut 57% / flower 29% / line 14%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 10% 落在这里，90% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：21 条 sound 段 / 38.2s；触发 cut 57% / flower 29% / line 14%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 43% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 21 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 10% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.85, 'p10': 0.583, 'p90': 1.583, 'max': 2.484, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.85s（够听清一个动作就收），浸响类最长 2.48s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': -4.35, 'p10': -8.48, 'p90': 0.0, 'min': -9.35, 'm | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（48% 的 cue 没动过）；被压低的 11 条（到 -9.3dB）时长中位 0.867s —— 压是给长尾巴/浸响，免得糊住人声 |
    | `action_dur_vs_material` | {'trimmed_share': 0.524, 'src_in_median': 0.167} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 52% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.167s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←21 条 cue / 38.2s ≈ 33.0/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←这套没有任何正增益（最高 0.0dB），真峰不会被音效顶起来；口播干净度←90% 的 cue 压在人声上；文字类（翻页/键盘）占 19%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 33.0/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 33.0/分 = 连击；源里 gap 中位 1.417s，比这更密就成机枪
- **style_19**：14 条里 14 条挂上可观测锚点（100%），挂上的部分是 cut 79% / flower 14% / line 7%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：14 条 sound 段 / 38.2s；触发 cut 79% / flower 14% / line 7%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.116s；其中 36% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 14 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 1.091, 'p10': 0.493, 'p90': 2.917, 'max': 13.183, | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 1.091s（够听清一个动作就收），浸响类最长 13.18s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -6.403, 'p90': 0.0, 'min': -8.68, 'ma | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（64% 的 cue 没动过）；被抬高的 1 条（到 +3.0dB）时长中位 13.183s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 4 条（到 -8.7 |
    | `action_dur_vs_material` | {'trimmed_share': 0.214, 'src_in_median': 0.267} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 21% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.267s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'flowe | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 116ms，93% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←14 条 cue / 38.2s ≈ 22.0/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←最高 +3.0dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 13.183s，靠短时长控住能；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 14%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 22.0/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 22.0/分 = 连击；源里 gap 中位 1.433s，比这更密就成机枪
- **style_20**：24 条里 22 条挂上可观测锚点（92%），挂上的部分是 flower 45% / cut 41% / beat 9% / line 5%；余下 2 条无锚点，是编辑随手 accent（额度单独计，不放开成常态）
  - 不该出现：四类锚点这套都用过（flower / cut / beat / line）——没有'绝对不响'的位置，只有密度与最小间隔在约束它
  - 证据：24 条 sound 段 / 30.7s；触发 flower 42% / cut 38% / beat 8% / free 8% / line 4%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.04s；其中 29% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 24 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 21% 落在无人声处）；无锚点的句子中段（free 额度 2 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.65, 'p10': 0.363, 'p90': 1.363, 'max': 2.766, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.65s（够听清一个动作就收），浸响类最长 2.77s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 5.0, 'p10': -2.1, 'p90': 7.991, 'min': -5.0, 'max | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（25% 的 cue 没动过）；被抬高的 15 条（到 +8.6dB）时长中位 0.633s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 3 条（到 -5.0 |
    | `action_dur_vs_material` | {'trimmed_share': 0.875, 'src_in_median': 0.1} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 88% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.1s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.042, 'fade_over_dur_median': 0.154, 'f | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.15~0.15 × cue_dur | 只有 1/24 条带淡出，且最短的一条也有 0.43s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'flower': 0.0, 'cu | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 40ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←24 条 cue / 30.7s ≈ 46.9/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态多、；真峰（true peak）←最高 +8.6dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.633s，靠短时长控住能量；口播干净度←79% 的 cue 压在人声上；文字类（翻页/键盘）占 17%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：2 条无锚点 cue = 听感里的'不知道为什么响了一声'；把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 46.9/分太多 → 大片没有声音事件，画面在动而耳朵是空的
- **style_21**：12 条里 12 条挂上可观测锚点（100%），挂上的部分是 cut 58% / flower 33% / line 8%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 0% 落在这里，100% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：12 条 sound 段 / 15.617s；触发 cut 58% / flower 33% / line 8%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 17% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 12 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 0% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.891, 'p10': 0.594, 'p90': 1.624, 'max': 1.817,  | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.891s（够听清一个动作就收），浸响类最长 1.82s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -6.489, 'p90': 0.0, 'min': -8.13, 'ma | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（75% 的 cue 没动过）；被抬高的 1 条（到 +8.0dB）时长中位 0.15s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 2 条（到 -8.1dB |
    | `action_dur_vs_material` | {'trimmed_share': 0.833, 'src_in_median': 0.2} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 83% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.2s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.167, 'fade_over_dur_median': 0.202, 'f | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.16~0.24 × cue_dur | 只有 2/12 条带淡出，且最短的一条也有 1.23s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'line' | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←12 条 cue / 15.617s ≈ 46.1/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +8.0dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.15s，靠短时长控住能量）；口播干净度←100% 的 cue 压在人声上；文字类（翻页/键盘）占 33%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 46.1/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 46.1/分 = 连击；源里 gap 中位 1.5s，比这更密就成机枪
- **style_22**：13 条里 13 条挂上可观测锚点（100%），挂上的部分是 cut 69% / flower 23% / line 8%；没有一条是无理由的
  - 不该出现：纯静音/无人声段（源里只剩 8% 落在这里，92% 都压在人声上）；BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：13 条 sound 段 / 23.483s；触发 cut 69% / flower 23% / line 8%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.066s；其中 46% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 13 条，分布在 3 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 8% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.883, 'p10': 0.516, 'p90': 2.31, 'max': 3.533, ' | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.883s（够听清一个动作就收），浸响类最长 3.53s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -9.16, 'p90': 0.0, 'min': -10.55, 'ma | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（62% 的 cue 没动过）；被抬高的 1 条（到 +6.0dB）时长中位 0.767s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 4 条（到 -10.6 |
    | `action_dur_vs_material` | {'trimmed_share': 0.692, 'src_in_median': 0.383} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 69% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.383s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.077, 'fade_over_dur_median': 0.34, 'fa | materials.audio_fades[].fade_out_duration（挂在段的 e | fade_out = 0.34~0.34 × cue_dur | 只有 1/13 条带淡出，且最短的一条也有 0.88s —— 淡出只给长尾巴：瞬态硬切才听得出'砸'，加了淡出反而变软 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.014, 'by_anchor': {'cut': 0.0, 'flo | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 66ms，92% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←13 条 cue / 23.483s ≈ 33.2/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +6.0dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.767s，靠短时长控住能量；口播干净度←92% 的 cue 压在人声上；文字类（翻页/键盘）占 31%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 33.2/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 33.2/分 = 连击；源里 gap 中位 1.641s，比这更密就成机枪
- **style_23**：12 条里 12 条挂上可观测锚点（100%），挂上的部分是 flower 67% / cut 33%；没有一条是无理由的
  - 不该出现：BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由
  - 证据：12 条 sound 段 / 41.833s；触发 flower 67% / cut 33%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 25% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 12 条，分布在 1 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 33% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.833, 'p10': 0.566, 'p90': 0.866, 'max': 1.5, 'm | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.833s（够听清一个动作就收），浸响类最长 1.5s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 2.99, 'p10': 0.299, 'p90': 8.34, 'min': 0.0, 'max | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（17% 的 cue 没动过）；被抬高的 10 条（到 +12.0dB）时长中位 0.833s —— 抬是给短瞬态，让它从口播里跳出来 |
    | `action_dur_vs_material` | {'trimmed_share': 0.833, 'src_in_median': 0.167} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 83% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.167s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'flower': 0.0, 'cu | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
    | `name_reuse` | {'unique_names': 5, 'cues': 12, 'reuse_ratio': 2.4, 'same_na | materials.audios[].name 去重 + 同名 cue 时间差 | 同一名字复用次数 = cues / unique_names | 这套允许重名（5 个名字撑 12 条），但同一条音效两次之间至少 0.83s；重名不是随手，是同一功能反复用 |
  - 影响谁：响度预算（integrated LUFS）←12 条 cue / 41.833s ≈ 17.2/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +12.0dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 0.833s，靠短时长控住能；口播干净度←67% 的 cue 压在人声上；文字类（翻页/键盘）占 42%，集中在 2–8kHz，正撞人声清晰度频段
  - 耦合：flower（same_anchor）；cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；文字类（翻页/键盘）占 42%，全在 2–8kHz → 人声齿音被糊，听成'沙'；低于 17.2/分太多 → 大片没有声音事件，画面在动而耳朵是空的
- **style_24**：19 条里 19 条挂上可观测锚点（100%），挂上的部分是 cut 95% / line 5%；没有一条是无理由的
  - 不该出现：BGM 节拍网格：源里 0 条踩拍，拍点不是这套的理由；花字上屏：这套的花字不带声音
  - 证据：19 条 sound 段 / 36.417s；触发 cut 95% / line 5%；锚点偏移中位 0.0s（≈0 帧@30fps）、|偏移| 最大 0.0s；其中 53% 同时贴切点与花字
  - 作用域：cue（一条音效一个事件） / 整片 19 条，分布在 2 条独立音效轨上，次序由锚点决定而不是等距铺（不适用：纯静音段（源里只有 16% 落在无人声处）；无锚点的句子中段（free 额度 0 条））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `cue_duration_sec` | {'median': 0.933, 'p10': 0.437, 'p90': 1.987, 'max': 3.133,  | tracks[type=audio].segments[].target_timerange.d | 直接读段长；不做任何衰减，源里放多长就是多长 | 瞬态/转场类被剪到 p50 0.933s（够听清一个动作就收），浸响类最长 3.13s 靠淡出收尾而不是剪短 |
    | `gain_db` | {'median': 0.0, 'p10': -5.896, 'p90': 0.878, 'min': -8.0, 'm | segments[].volume（线性）→ dB = 20·log10(volume) | dB = 20·log10(volume) | 0dB 是默认档（74% 的 cue 没动过）；被抬高的 2 条（到 +7.3dB）时长中位 1.475s —— 抬是给短瞬态，让它从口播里跳出来；被压低的 3 条（到 -8.0d |
    | `action_dur_vs_material` | {'trimmed_share': 0.474, 'src_in_median': 0.183} | source_timerange.start / target_timerange.durati | 只截取源音效的一小段；不重新合成 | 47% 的 cue 从素材中段起播（跳过前导空拍），起播点中位 0.183s —— 目的是让'响'落在锚点上，不是让素材从头放 |
    | `fade_out_sec` | {'share_with_fade': 0.0, 'fade_over_dur_median': None, 'fade | materials.audio_fades[].fade_out_duration（挂在段的 e | 本套未使用淡出 | 本套一条淡出都没有：全部硬切，靠素材本身的尾巴收 |
    | `sfx_cut_offset_sec` | {'median': 0.0, 'p90': 0.0, 'by_anchor': {'cut': 0.0, 'line' | cue 落点 − 最近锚点时刻（主轨切点 / 花字起 / 字幕换行） | offset = t_cue − t_anchor；容差 0.12s（切点/花字/换行）、0.08s（拍 | |偏移| 中位 0ms、最大 0ms，100% 落在 ≤1.5 帧内：声音是被'事件'拖进来的，不是提前/延后铺的 |
  - 影响谁：响度预算（integrated LUFS）←19 条 cue / 36.417s ≈ 31.3/分；源草稿整体被拉到 -14.0 附近，音效是其中最难压的一段（瞬态；真峰（true peak）←最高 +7.3dB 的 cue 单条就能把整体真峰顶上去（这类高增益 cue 的时长中位 1.475s，靠短时长控住能量；口播干净度←84% 的 cue 压在人声上；同族音效集中在 1–6kHz，落在人声泛音带上方
  - 耦合：cut（shared_instant）；bgm（level_delta）
  - 违反症状：把 stress/bgm_beat 这类'口播中段微切'当切点 → 每句中间都'叮'一下，正是用户投诉的那种吵；低于 31.3/分太多 → 大片没有声音事件，画面在动而耳朵是空的；高于 31.3/分 = 连击；源里 gap 中位 1.225s，比这更密就成机枪


## BGM（`bgm`）

**BGM 为什么出现**

- **style_01**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 68.5s；2 首曲目（喜剧幽默搞笑古风  eighteen / Food2），床位压到 -10.5dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：2 段 BGM（类型 ['music']）/ 68.5s；覆盖 1.0；床位（≤-3dB 的段）中位 -10.95dB、时长加权 -10.5dB；人声覆盖中位 1.0（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 2 段 / 2 首曲目，覆盖 1.0 的时间窗（不适用：1 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -10.95, 'duration_weighted': -10.5, 'min': -10.95 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -10.5dB（比音效低 4.0dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.25, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 0.25s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 1.0, 'fade_out_median': 0.783, 'shar | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 2/2 段带尾巴淡出（中位 0.783s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -10.5dB、人声覆盖 1.0；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_02**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.033s 起铺到 29.467s；1 首曲目（Sunrise），床位压到 -7.32dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 29.467s；覆盖 1.0；床位（≤-3dB 的段）中位 -7.32dB、时长加权 -7.32dB；人声覆盖中位 0.977（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -7.32, 'duration_weighted': -7.32, 'min': -7.32,  | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -7.32dB（比音效低 13.3dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 0.999, 'gap_count | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 0.999 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -7.32dB、人声覆盖 0.977；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_03**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.017s 起铺到 126.867s；1 首曲目（IV (抖音原版) （剪辑版）），床位压到 -8.8dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：4 段 BGM（类型 ['music']）/ 126.867s；覆盖 1.0；床位（≤-3dB 的段）中位 -9.47dB、时长加权 -8.8dB；人声覆盖中位 0.897（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 4 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：3 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -9.47, 'duration_weighted': -8.8, 'min': -16.49,  | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -8.8dB（比音效低 8.8dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 21.058, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 21.058s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/4 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -8.8dB、人声覆盖 0.897；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_04**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 19.5s；1 首曲目（Judgment Day.m4a），床位压到 -7.97dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['extract_music']）/ 19.5s；覆盖 1.0；床位（≤-3dB 的段）中位 -7.97dB、时长加权 -7.97dB；人声覆盖中位 0.952（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -7.97, 'duration_weighted': -7.97, 'min': -7.97,  | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -7.97dB（比音效低 8.0dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.333, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 0.333s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -7.97dB、人声覆盖 0.952；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_05**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.017s 起铺到 22.467s；1 首曲目（Push Up（剪辑版）），床位压到 -11.0dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 22.467s；覆盖 1.0；床位（≤-3dB 的段）中位 -11.0dB、时长加权 -11.0dB；人声覆盖中位 1.0（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -11.0, 'duration_weighted': -11.0, 'min': -11.0,  | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -11.0dB（比音效低 11.0dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 0.999, 'gap_count | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 0.999 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -11.0dB、人声覆盖 1.0；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -10.9 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_06**：只要片子有人声就一直有床：99% 的时间窗里有 BGM，从 0.067s 起铺到 21.667s；1 首曲目（美丽的神话 钢琴演奏），床位压到 -5.95dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 21.667s；覆盖 0.989；床位（≤-3dB 的段）中位 -5.95dB、时长加权 -5.95dB；人声覆盖中位 0.929（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 0.989 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -5.95, 'duration_weighted': -5.95, 'min': -5.95,  | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -5.95dB（比音效低 6.0dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.9, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 0.9s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 0.989, 'duplicated_ratio': 0.997, 'gap_cou | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 99% 的时间被床覆盖（重复覆盖 0.997 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -5.95dB、人声覆盖 0.929；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_07**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 28.133s；1 首曲目（Push Up（剪辑版）），床位压到 -15.43dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 28.133s；覆盖 1.0；床位（≤-3dB 的段）中位 -15.43dB、时长加权 -15.43dB；人声覆盖中位 0.897（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -15.43, 'duration_weighted': -15.43, 'min': -15.4 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -15.43dB（比音效低 15.4dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -15.43dB、人声覆盖 0.897；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_08**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 58.467s；1 首曲目（Void（Original ）），床位压到 -10.01dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 58.467s；覆盖 1.0；床位（≤-3dB 的段）中位 -10.01dB、时长加权 -10.01dB；人声覆盖中位 0.702（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -10.01, 'duration_weighted': -10.01, 'min': -10.0 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -10.01dB（比音效低 10.0dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 1.0, 'fade_out_median': 1.767, 'shar | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 1/1 段带尾巴淡出（中位 1.767s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -10.01dB、人声覆盖 0.702；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_09**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 42.5s；1 首曲目（Abel Korzeniowski - Dance For Me Wallis.mp3），床位压到 -9.79dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：2 段 BGM（类型 ['extract_music']）/ 42.5s；覆盖 1.0；床位（≤-3dB 的段）中位 -9.68dB、时长加权 -9.79dB；人声覆盖中位 0.984（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 2 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：1 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -9.68, 'duration_weighted': -9.79, 'min': -13.42, | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -9.79dB（比音效低 9.8dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 10.708, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 10.708s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.5, 'fade_out_median': 0.967, 'shar | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 1/2 段带尾巴淡出（中位 0.967s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -9.79dB、人声覆盖 0.984；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_10**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 18.917s；1 首曲目（20250604211833-15），床位压到 -8.22dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['extract_music']）/ 18.917s；覆盖 1.0；床位（≤-3dB 的段）中位 -8.22dB、时长加权 -8.22dB；人声覆盖中位 0.644（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -8.22, 'duration_weighted': -8.22, 'min': -8.22,  | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -8.22dB（比音效低 8.2dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.633, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 0.633s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 1.0, 'fade_out_median': 1.733, 'shar | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 1/1 段带尾巴淡出（中位 1.733s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -8.22dB、人声覆盖 0.644；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_11**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 26.4s；2 首曲目（Push Up（剪辑版） / 复合片段1），床位压到 -11.45dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：2 段 BGM（类型 ['music']）/ 26.4s；覆盖 1.0；床位（≤-3dB 的段）中位 -12.61dB、时长加权 -11.45dB；人声覆盖中位 0.957（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 2 段 / 2 首曲目，覆盖 1.0 的时间窗（不适用：1 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -12.61, 'duration_weighted': -11.45, 'min': -12.6 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -11.45dB（比音效低 11.4dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/2 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.101, 'gap_count | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.101 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -11.45dB、人声覆盖 0.957；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_12**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 26.3s；2 首曲目（Abel Korzeniowski - Dance For Me Wallis.mp3 / Elegant），床位压到 -9.61dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：2 段 BGM（类型 ['extract_music', 'music']）/ 26.3s；覆盖 1.0；床位（≤-3dB 的段）中位 -9.64dB、时长加权 -9.61dB；人声覆盖中位 0.913（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 2 段 / 2 首曲目，覆盖 1.0 的时间窗（不适用：1 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -9.64, 'duration_weighted': -9.61, 'min': -12.55, | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -9.61dB（比音效低 7.9dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.667, 'share_offset_from_head': 0.5} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 50% 的段从曲子中段起（中位 0.667s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.5, 'fade_out_median': 2.033, 'shar | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 1/2 段带尾巴淡出（中位 2.033s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -9.61dB、人声覆盖 0.913；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.5 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_13**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 30.217s；1 首曲目（Abel Korzeniowski - Dance For Me Wallis.mp3），床位压到 -9.36dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：2 段 BGM（类型 ['extract_music']）/ 30.217s；覆盖 1.0；床位（≤-3dB 的段）中位 -10.39dB、时长加权 -9.36dB；人声覆盖中位 0.962（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 2 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：1 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -10.39, 'duration_weighted': -9.36, 'min': -13.02 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -9.36dB（比音效低 9.4dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 11.108, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 11.108s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/2 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -9.36dB、人声覆盖 0.962；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.9 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_14**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 20.9s；1 首曲目（Veil of Darkness - 黑暗之幕），床位压到 -5.0dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 20.9s；覆盖 1.0；床位（≤-3dB 的段）中位 -5.0dB、时长加权 -5.0dB；人声覆盖中位 0.895（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -5.0, 'duration_weighted': -5.0, 'min': -5.0, 'ma | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -5.0dB（比音效低 5.0dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.183, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 0.183s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -5.0dB、人声覆盖 0.895；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -12.6 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_15**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 30.567s；2 首曲目（Intro / Sunrise），床位压到 -7.0dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：3 段 BGM（类型 ['music']）/ 30.567s；覆盖 1.0；床位（≤-3dB 的段）中位 -5.0dB、时长加权 -7.0dB；人声覆盖中位 0.859（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 3 段 / 2 首曲目，覆盖 1.0 的时间窗（不适用：1 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -5.0, 'duration_weighted': -7.0, 'min': -9.0, 'ma | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -7.0dB（比音效低 10.1dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/3 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 2.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 2.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -7.0dB、人声覆盖 0.859；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_16**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 28.333s；1 首曲目（Melody (Original Mix)），床位压到 -9.55dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 28.333s；覆盖 1.0；床位（≤-3dB 的段）中位 -9.55dB、时长加权 -9.55dB；人声覆盖中位 0.887（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -9.55, 'duration_weighted': -9.55, 'min': -9.55,  | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -9.55dB（比音效低 3.6dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -9.55dB、人声覆盖 0.887；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_17**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 16.0s；1 首曲目（Judgment Day.m4a），床位压到 -12.12dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['extract_music']）/ 16.0s；覆盖 1.0；床位（≤-3dB 的段）中位 -12.12dB、时长加权 -12.12dB；人声覆盖中位 0.727（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -12.12, 'duration_weighted': -12.12, 'min': -12.1 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -12.12dB（比音效低 12.1dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.367, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 0.367s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -12.12dB、人声覆盖 0.727；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_18**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 38.2s；1 首曲目（Push Up（剪辑版）），床位压到 -12.39dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 38.2s；覆盖 1.0；床位（≤-3dB 的段）中位 -12.39dB、时长加权 -12.39dB；人声覆盖中位 0.874（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -12.39, 'duration_weighted': -12.39, 'min': -12.3 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -12.39dB（比音效低 8.0dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -12.39dB、人声覆盖 0.874；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_19**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 38.2s；1 首曲目（Push Up（剪辑版）），床位压到 -14.56dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 38.2s；覆盖 1.0；床位（≤-3dB 的段）中位 -14.56dB、时长加权 -14.56dB；人声覆盖中位 0.968（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -14.56, 'duration_weighted': -14.56, 'min': -14.5 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -14.56dB（比音效低 14.6dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -14.56dB、人声覆盖 0.968；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_20**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.033s 起铺到 30.7s；3 首曲目（Movie-style piano and attack peen(942566) / SoCold臭味相投(DJ版) / 千山），床位压到 -6.6dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：3 段 BGM（类型 ['music']）/ 30.7s；覆盖 1.0；床位（≤-3dB 的段）中位 -7.5dB、时长加权 -6.6dB；人声覆盖中位 0.574（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 3 段 / 3 首曲目，覆盖 1.0 的时间窗（不适用：2 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -7.5, 'duration_weighted': -6.6, 'min': -10.0, 'm | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -6.6dB（比音效低 11.6dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 5.5, 'share_offset_from_head': 0.667} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 67% 的段从曲子中段起（中位 5.5s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.333, 'fade_out_median': 0.833, 'sh | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 1/3 段带尾巴淡出（中位 0.833s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.117, 'gap_count | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.117 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -6.6dB、人声覆盖 0.574；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_21**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 15.617s；2 首曲目（It is All in Your Imagination / 复合片段5），床位压到 -9.19dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：2 段 BGM（类型 ['music']）/ 15.617s；覆盖 1.0；床位（≤-3dB 的段）中位 -9.95dB、时长加权 -9.19dB；人声覆盖中位 1.0（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 2 段 / 2 首曲目，覆盖 1.0 的时间窗（不适用：1 段提前收（片尾/换床））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -9.95, 'duration_weighted': -9.19, 'min': -9.95,  | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -9.19dB（比音效低 9.2dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 55.233, 'share_offset_from_head': 0.5} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 50% 的段从曲子中段起（中位 55.233s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.5, 'fade_out_median': 0.5, 'share_ | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 1/2 段带尾巴淡出（中位 0.5s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.082, 'gap_count | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.082 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -9.19dB、人声覆盖 1.0；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_22**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 23.483s；1 首曲目（Push Up（剪辑版）），床位压到 -11.09dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 23.483s；覆盖 1.0；床位（≤-3dB 的段）中位 -11.09dB、时长加权 -11.09dB；人声覆盖中位 0.983（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -11.09, 'duration_weighted': -11.09, 'min': -11.0 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -11.09dB（比音效低 11.1dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.0, 'share_offset_from_head': 0.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 0% 的段从曲子中段起（中位 0s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -11.09dB、人声覆盖 0.983；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_23**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 41.833s；1 首曲目（柔和的光（柚仔版）），床位压到 -17.47dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['music']）/ 41.833s；覆盖 1.0；床位（≤-3dB 的段）中位 -17.47dB、时长加权 -17.47dB；人声覆盖中位 0.492（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -17.47, 'duration_weighted': -17.47, 'min': -17.4 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -17.47dB（比音效低 20.5dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.833, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 0.833s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -17.47dB、人声覆盖 0.492；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟
- **style_24**：只要片子有人声就一直有床：100% 的时间窗里有 BGM，从 0.0s 起铺到 36.417s；1 首曲目（Dark_Model_-_Judgment_Day.mp3），床位压到 -13.33dB 让口播站前面
  - 不该出现：片头干口播：本套没有（BGM 从 0s 起就铺满）——想留干口播开场属于主动偏离
  - 证据：1 段 BGM（类型 ['extract_music']）/ 36.417s；覆盖 1.0；床位（≤-3dB 的段）中位 -13.33dB、时长加权 -13.33dB；人声覆盖中位 0.887（0.9 以上＝床位全程压在人声下）
  - 作用域：片 / 1 段 / 1 首曲目，覆盖 1.0 的时间窗（不适用：无（源里没有'中途撤床'的段落））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | {'median': -13.33, 'duration_weighted': -13.33, 'min': -13.3 | tracks[type=audio].segments[type=music|extract_m | dB = 20·log10(volume) | 床位的任务只是'别让耳朵空'，所以压到 -13.33dB（比音效低 13.3dB）；只有片头 sting 才会短暂到 0dB |
    | `source_in_sec` | {'median': 0.117, 'share_offset_from_head': 1.0} | source_timerange.start | 从曲子第几秒起播（跳过前奏） | 100% 的段从曲子中段起（中位 0.117s）：源里不用前奏，直接进已经成立的律动 |
    | `tail_fade_sec` | {'share_with_fade_out': 0.0, 'fade_out_median': None, 'share | materials.audio_fades[].fade_out_duration / fade | fade_out ≈ 0.9~2.0s（约片长的 5%）；换床时新床 fade_in ≈ 0.57s | 0/1 段带尾巴淡出（中位 0s）：床不能'咔'一下断，断在口播最后一个字上会听出剪辑点 |
    | `coverage_ratio` | {'covered_ratio': 1.0, 'duplicated_ratio': 1.0, 'gap_count': | BGM 段时间轴按 0.25s 网格采样 | covered = |{t: ∃BGM(t)}| / |grid| | 100% 的时间被床覆盖（重复覆盖 1.0 是叠层/接棒造成的）：源里没有'中途静音'的段落，BGM 一旦进就不退 |
  - 影响谁：口播清晰度←床位 -13.33dB、人声覆盖 0.887；床一旦抬 → 字头被掩蔽；低频/中频占用←床占 100~400Hz，音效占 2~8kHz，两者错开；床抬起来会先糊人声胸腔共鸣；整体 LUFS←库里标称 -14.0 LUFS（编写常量，不是本套测量值）
  - 耦合：sfx（level_delta）；voice_ducking（owned_by）；dual_bgm_switch（sequence）
  - 违反症状：床抬到 0dB 附近 → 口播糊、字幕听不清（源里正片一次都没有高过 -5dB）；床压到 -20dB 以下 → 片子变'干'，每句之间露白，听成没做完；床在句中断掉（不做淡出）→ 听到一次'咔'，比没有床更糟


## 双段 BGM 切换（`dual_bgm_switch`）

**为什么切第二段 BGM**

- **style_01**：这套不是'一直一条床'：2 首曲目、1 个结构点（sequential_handoff 100%）。主换点在 2.833s —— 两首曲目接棒（前一首淡出/短交叠，后一首淡入）；距最近主轨切点 +0.0s、距换句 +0.0s、距 B-roll 窗口边界 +0.0s。换床必须落在结构位上，不然听成两首歌打架
  - 不该出现：只上一首曲目就够的短片（这套换，是因为开场/段落需要仪式感）
  - 证据：2 段 → 2 个 piece / 2 首；结构点 [('sequential_handoff', 2.833)]；主换点 t=2.833s、距切点 0.0s
  - 作用域：段 / 全片 68.5s 内 2 个 piece、1 个结构点（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `switch_sec` | 2.833 | BGM 段边界（曲目变化 / sting 退出） | sequential→后一首 start；overlay sting→sting end；paralle | 实测 2.833s，距最近主轨切点 +0.0s、距换句 +0.0s、距 B-roll 窗口边界 +0.0s：锚在'开场结构走完/画面换了'的那一刻，不是按秒数拍的 |
    | `gain_step_db` | {'sequential_handoff': -10.95} | 相邻两段 volume 字段之差 | ΔdB = dB(后) − dB(前) | 换段同时电平也变：从 0dB 的 sting 掉到床通常 8~14dB，就是'仪式感结束、叙事开始' |
    | `overlap_sec` | 0.0 | 两条 BGM 段时间轴交集 | overlap = min(end) − max(start) | 没有叠层，全靠接棒（接棒靠淡入淡出，不靠叠） |
  - 影响谁：段落感←1 个结构点把片子切成 2 个音乐段；口播清晰度←往下台阶时口播变清楚；往上/叠层的段落必须没有密集口播，否则字头被盖；真峰←0dB 的 sting 段是整片最响的音乐段，真峰由它决定
  - 耦合：bgm（owned_by）；voice_ducking（interaction）；sfx（shared_boundary）
  - 违反症状：换床点落在句子中间 → 两首歌律动撞在一起，听成车祸；片头 sting 拉长到整段 → 开场变吵，正片床没机会进；两首曲目直接硬切不淡入淡出 → 听到'咔'
- **style_02**：不出现——只有一首曲目（Sunrise）铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_03**：不出现——只有一首曲目（IV (抖音原版) （剪辑版））铺 4 段——不是双段换曲。同一曲目有 3 次电平台阶（63.35s +9.5dB/85.267s -8.8dB/107.167s -7.7dB），那是音量自动化，记在 bgm.value_drivers 与 voice_ducking.manual_steps 里，不当换段
- **style_04**：不出现——只有一首曲目（Judgment Day.m4a）铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_05**：不出现——只有一首曲目（Push Up（剪辑版））铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_06**：不出现——只有一首曲目（美丽的神话 钢琴演奏）铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_07**：不出现——只有一首曲目（Push Up（剪辑版））铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_08**：不出现——只有一首曲目（Void（Original ））铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_09**：不出现——只有一首曲目（Abel Korzeniowski - Dance For Me Wallis.mp3）铺 2 段——不是双段换曲。同一曲目有 1 次电平台阶（20.65s -7.5dB），那是音量自动化，记在 bgm.value_drivers 与 voice_ducking.manual_steps 里，不当换段
- **style_10**：不出现——只有一首曲目（20250604211833-15）铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_11**：这套不是'一直一条床'：2 首曲目、1 个结构点（overlay_sting_exit 100%）。主换点在 22.217s —— 中段 sting 叠在床的上方、退出时回单床；距换句 +0.067s、距花字 +0.0s。换床必须落在结构位上，不然听成两首歌打架
  - 不该出现：只上一首曲目就够的短片（这套换，是因为开场/段落需要仪式感）
  - 证据：2 段 → 2 个 piece / 2 首；结构点 [('overlay_sting_exit', 22.217)]；主换点 t=22.217s（附近 1s 内没有主轨切点）
  - 作用域：段 / 全片 26.4s 内 2 个 piece、1 个结构点（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `switch_sec` | 22.217 | BGM 段边界（曲目变化 / sting 退出） | sequential→后一首 start；overlay sting→sting end；paralle | 实测 22.217s，距换句 +0.067s、距花字 +0.0s：锚在'开场结构走完/画面换了'的那一刻，不是按秒数拍的 |
    | `gain_step_db` | {'overlay_sting_exit': -12.61} | 相邻两段 volume 字段之差 | ΔdB = dB(后) − dB(前) | 换段同时电平也变：从 0dB 的 sting 掉到床通常 8~14dB，就是'仪式感结束、叙事开始' |
    | `overlap_sec` | 2.667 | 两条 BGM 段时间轴交集 | overlap = min(end) − max(start) | 叠层最长 2.667s：sting 压在床上方制造开场仪式感，退出即交棒；两条长床并列则整段都叠着（更厚，但更占频段、更吃口播） |
  - 影响谁：段落感←1 个结构点把片子切成 2 个音乐段；口播清晰度←往下台阶时口播变清楚；往上/叠层的段落必须没有密集口播，否则字头被盖；真峰←0dB 的 sting 段是整片最响的音乐段，真峰由它决定
  - 耦合：bgm（owned_by）；voice_ducking（interaction）；sfx（shared_boundary）
  - 违反症状：换床点落在句子中间 → 两首歌律动撞在一起，听成车祸；片头 sting 拉长到整段 → 开场变吵，正片床没机会进；两首曲目直接硬切不淡入淡出 → 听到'咔'
- **style_12**：这套不是'一直一条床'：2 首曲目、1 个结构点（sequential_handoff 100%）。主换点在 13.3s —— 两首曲目接棒（前一首淡出/短交叠，后一首淡入）；距最近主轨切点 +0.0s、距换句 +0.0s、距花字 +0.0s。换床必须落在结构位上，不然听成两首歌打架
  - 不该出现：只上一首曲目就够的短片（这套换，是因为开场/段落需要仪式感）
  - 证据：2 段 → 2 个 piece / 2 首；结构点 [('sequential_handoff', 13.3)]；主换点 t=13.3s、距切点 0.0s
  - 作用域：段 / 全片 26.3s 内 2 个 piece、1 个结构点（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `switch_sec` | 13.3 | BGM 段边界（曲目变化 / sting 退出） | sequential→后一首 start；overlay sting→sting end；paralle | 实测 13.3s，距最近主轨切点 +0.0s、距换句 +0.0s、距花字 +0.0s：锚在'开场结构走完/画面换了'的那一刻，不是按秒数拍的 |
    | `gain_step_db` | {'sequential_handoff': 5.82} | 相邻两段 volume 字段之差 | ΔdB = dB(后) − dB(前) | 换段同时电平也变：从 0dB 的 sting 掉到床通常 8~14dB，就是'仪式感结束、叙事开始' |
    | `overlap_sec` | 0.0 | 两条 BGM 段时间轴交集 | overlap = min(end) − max(start) | 没有叠层，全靠接棒（接棒靠淡入淡出，不靠叠） |
  - 影响谁：段落感←1 个结构点把片子切成 2 个音乐段；口播清晰度←往下台阶时口播变清楚；往上/叠层的段落必须没有密集口播，否则字头被盖；真峰←0dB 的 sting 段是整片最响的音乐段，真峰由它决定
  - 耦合：bgm（owned_by）；voice_ducking（interaction）；sfx（shared_boundary）
  - 违反症状：换床点落在句子中间 → 两首歌律动撞在一起，听成车祸；片头 sting 拉长到整段 → 开场变吵，正片床没机会进；两首曲目直接硬切不淡入淡出 → 听到'咔'
- **style_13**：不出现——只有一首曲目（Abel Korzeniowski - Dance For Me Wallis.mp3）铺 2 段——不是双段换曲。同一曲目有 1 次电平台阶（21.05s -5.3dB），那是音量自动化，记在 bgm.value_drivers 与 voice_ducking.manual_steps 里，不当换段
- **style_14**：不出现——只有一首曲目（Veil of Darkness - 黑暗之幕）铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_15**：这套是叠层而不是换段：2 首（Intro / Sunrise）几乎同起同收，主床 30.57s、副床 30.57s，没有可观测的换点
  - 不该出现：只上一首曲目就够的短片（这套换，是因为开场/段落需要仪式感）
  - 证据：3 段 → 2 个 piece / 2 首；结构点 [('parallel_layers', 30.567)]；没有换点（叠层铺满）
  - 作用域：段 / 全片 30.567s 内 2 个 piece、1 个结构点（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `switch_sec` | None | BGM 段边界（曲目变化 / sting 退出） | sequential→后一首 start；overlay sting→sting end；paralle | 没有换点：两条床同起同收，听感是'更厚'而不是'换歌' |
    | `gain_step_db` | {'parallel_layers': -4.0} | 相邻两段 volume 字段之差 | ΔdB = dB(后) − dB(前) | 换段同时电平也变：从 0dB 的 sting 掉到床通常 8~14dB，就是'仪式感结束、叙事开始' |
    | `overlap_sec` | 30.567 | 两条 BGM 段时间轴交集 | overlap = min(end) − max(start) | 叠层最长 30.567s：sting 压在床上方制造开场仪式感，退出即交棒；两条长床并列则整段都叠着（更厚，但更占频段、更吃口播） |
  - 影响谁：段落感←1 个结构点把片子切成 2 个音乐段；口播清晰度←往下台阶时口播变清楚；往上/叠层的段落必须没有密集口播，否则字头被盖；真峰←0dB 的 sting 段是整片最响的音乐段，真峰由它决定
  - 耦合：bgm（owned_by）；voice_ducking（interaction）；sfx（shared_boundary）
  - 违反症状：换床点落在句子中间 → 两首歌律动撞在一起，听成车祸；片头 sting 拉长到整段 → 开场变吵，正片床没机会进；两首曲目直接硬切不淡入淡出 → 听到'咔'
- **style_16**：不出现——只有一首曲目（Melody (Original Mix)）铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_17**：不出现——只有一首曲目（Judgment Day.m4a）铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_18**：不出现——只有一首曲目（Push Up（剪辑版））铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_19**：不出现——只有一首曲目（Push Up（剪辑版））铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_20**：这套不是'一直一条床'：3 首曲目、2 个结构点（intro_overlay_exit 50% / sequential_handoff 50%）。主换点在 13.167s —— 两首曲目接棒（前一首淡出/短交叠，后一首淡入）；距最近主轨切点 -0.733s、距换句 -0.033s、距 B-roll 窗口边界 -0.733s。换床必须落在结构位上，不然听成两首歌打架
  - 不该出现：只上一首曲目就够的短片（这套换，是因为开场/段落需要仪式感）
  - 证据：3 段 → 3 个 piece / 3 首；结构点 [('intro_overlay_exit', 2.933), ('sequential_handoff', 13.167)]；主换点 t=13.167s、距切点 -0.733s
  - 作用域：段 / 全片 30.7s 内 3 个 piece、2 个结构点（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `switch_sec` | 13.167 | BGM 段边界（曲目变化 / sting 退出） | sequential→后一首 start；overlay sting→sting end；paralle | 实测 13.167s，距最近主轨切点 -0.733s、距换句 -0.033s、距 B-roll 窗口边界 -0.733s：锚在'开场结构走完/画面换了'的那一刻，不是按秒数拍的 |
    | `gain_step_db` | {'intro_overlay_exit': -10.0, 'sequential_handoff': 5.0} | 相邻两段 volume 字段之差 | ΔdB = dB(后) − dB(前) | 换段同时电平也变：从 0dB 的 sting 掉到床通常 8~14dB，就是'仪式感结束、叙事开始' |
    | `overlap_sec` | 2.9 | 两条 BGM 段时间轴交集 | overlap = min(end) − max(start) | 叠层最长 2.9s：sting 压在床上方制造开场仪式感，退出即交棒；两条长床并列则整段都叠着（更厚，但更占频段、更吃口播） |
  - 影响谁：段落感←2 个结构点把片子切成 3 个音乐段；口播清晰度←往下台阶时口播变清楚；往上/叠层的段落必须没有密集口播，否则字头被盖；真峰←0dB 的 sting 段是整片最响的音乐段，真峰由它决定
  - 耦合：bgm（owned_by）；voice_ducking（interaction）；sfx（shared_boundary）
  - 违反症状：换床点落在句子中间 → 两首歌律动撞在一起，听成车祸；片头 sting 拉长到整段 → 开场变吵，正片床没机会进；两首曲目直接硬切不淡入淡出 → 听到'咔'
- **style_21**：这套不是'一直一条床'：2 首曲目、1 个结构点（intro_overlay_exit 100%）。主换点在 1.733s —— 片头 sting 叠在床的上方、退出时把正片床露出来（sting ≤3.5s）；距最近主轨切点 +0.066s、距换句 +0.066s、距花字 +0.066s、距 B-roll 窗口边界 +0.066s。换床必须落在结构位上，不然听成两首歌打架
  - 不该出现：只上一首曲目就够的短片（这套换，是因为开场/段落需要仪式感）
  - 证据：2 段 → 2 个 piece / 2 首；结构点 [('intro_overlay_exit', 1.733)]；主换点 t=1.733s、距切点 0.066s
  - 作用域：段 / 全片 15.617s 内 2 个 piece、1 个结构点（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `switch_sec` | 1.733 | BGM 段边界（曲目变化 / sting 退出） | sequential→后一首 start；overlay sting→sting end；paralle | 实测 1.733s，距最近主轨切点 +0.066s、距换句 +0.066s、距花字 +0.066s、距 B-roll 窗口边界 +0.066s：锚在'开场结构走完/画面换了'的那一 |
    | `gain_step_db` | {'intro_overlay_exit': -9.95} | 相邻两段 volume 字段之差 | ΔdB = dB(后) − dB(前) | 换段同时电平也变：从 0dB 的 sting 掉到床通常 8~14dB，就是'仪式感结束、叙事开始' |
    | `overlap_sec` | 1.283 | 两条 BGM 段时间轴交集 | overlap = min(end) − max(start) | 叠层最长 1.283s：sting 压在床上方制造开场仪式感，退出即交棒；两条长床并列则整段都叠着（更厚，但更占频段、更吃口播） |
  - 影响谁：段落感←1 个结构点把片子切成 2 个音乐段；口播清晰度←往下台阶时口播变清楚；往上/叠层的段落必须没有密集口播，否则字头被盖；真峰←0dB 的 sting 段是整片最响的音乐段，真峰由它决定
  - 耦合：bgm（owned_by）；voice_ducking（interaction）；sfx（shared_boundary）
  - 违反症状：换床点落在句子中间 → 两首歌律动撞在一起，听成车祸；片头 sting 拉长到整段 → 开场变吵，正片床没机会进；两首曲目直接硬切不淡入淡出 → 听到'咔'
- **style_22**：不出现——只有一首曲目（Push Up（剪辑版））铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_23**：不出现——只有一首曲目（柔和的光（柚仔版））铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有
- **style_24**：不出现——只有一首曲目（Dark_Model_-_Judgment_Day.mp3）铺 1 段——不是双段换曲。电平也全程一致，连台阶都没有


## 人声闪避（`voice_ducking`）

**什么时候压人声闪避**

- **style_01**：源草稿**不做侧链压缩**：3 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -10.5dB）：音效（-6.47dB）只比它高 4.0dB —— 这套音效本来就轻，层次靠'少'而不是靠'响'；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 1.0）
  - 证据：3 条音频段中带 volume 关键帧 0 条；床位 -10.5dB、音效 -6.47dB（差 4.0dB）；人声覆盖中位 1.0；手动台阶 0 次；降噪：无
  - 作用域：全片 / 2 段 BGM 全部压在人声下（覆盖率中位 1.0）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -10.5 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 1.0 的人声覆盖下把床压到 -10.5dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 4.0 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 4.0dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -10.5dB + 音效高 4.0dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.0dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_02**：源草稿**不做侧链压缩**：21 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -7.32dB）：音效（6.0dB）比它高 13.3dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.977）
  - 证据：21 条音频段中带 volume 关键帧 0 条；床位 -7.32dB、音效 6.0dB（差 13.3dB）；人声覆盖中位 0.977；手动台阶 0 次；降噪：人声镜头（video 轨）
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.977）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -7.32 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.977 的人声覆盖下把床压到 -7.32dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 13.3 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 13.3dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [0.85], 'n_materials': 28, 'attached_to': '人声镜头（vid | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 开了 [0.85] 的人声降噪（挂在人声镜头上）：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -7.32dB + 音效高 13.3dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_03**：源草稿**不做侧链压缩**：57 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -8.8dB）：音效（0.0dB）比它高 8.8dB，音效跳在床上面、口播站在最前面；中途只有 3 次手动台阶（63.35s +9.5dB/85.267s -8.8dB/107.167s -7.7dB）
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.897）
  - 证据：57 条音频段中带 volume 关键帧 0 条；床位 -8.8dB、音效 0.0dB（差 8.8dB）；人声覆盖中位 0.897；手动台阶 3 次；降噪：无
  - 作用域：全片 / 4 段 BGM 全部压在人声下（覆盖率中位 0.897）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -8.8 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.897 的人声覆盖下把床压到 -8.8dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 8.8 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 8.8dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 3, 'steps': [{'at': 63.35, 'delta_db': 9.47, 'spee | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 台阶全部出现在段落边界（切点/换句附近），方向都是'往下'：源里的做法是'到这一段把床按低一点'，而不是'每句话进来就压一下' |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -8.8dB + 音效高 8.8dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_04**：源草稿**不做侧链压缩**：14 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -7.97dB）：音效（0.0dB）比它高 8.0dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.952）
  - 证据：14 条音频段中带 volume 关键帧 0 条；床位 -7.97dB、音效 0.0dB（差 8.0dB）；人声覆盖中位 0.952；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.952）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -7.97 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.952 的人声覆盖下把床压到 -7.97dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 8.0 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 8.0dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -7.97dB + 音效高 8.0dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_05**：源草稿**不做侧链压缩**：11 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -11.0dB）：音效（0.0dB）比它高 11.0dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 1.0）
  - 证据：11 条音频段中带 volume 关键帧 0 条；床位 -11.0dB、音效 0.0dB（差 11.0dB）；人声覆盖中位 1.0；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 1.0）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -11.0 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 1.0 的人声覆盖下把床压到 -11.0dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 11.0 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 11.0dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -11.0dB + 音效高 11.0dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -10.9 LUFS 是编写常量；这套连 SNR 标称值都没有——源草稿没有可测的 SNR 真值（段字段推不出
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_06**：源草稿**不做侧链压缩**：14 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -5.95dB）：音效（0.0dB）只比它高 6.0dB —— 这套音效本来就轻，层次靠'少'而不是靠'响'；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.929）
  - 证据：14 条音频段中带 volume 关键帧 0 条；床位 -5.95dB、音效 0.0dB（差 6.0dB）；人声覆盖中位 0.929；手动台阶 0 次；降噪：人声镜头（video 轨）
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.929）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -5.95 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.929 的人声覆盖下把床压到 -5.95dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 6.0 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 6.0dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [0.85], 'n_materials': 3, 'attached_to': '人声镜头（vide | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 开了 [0.85] 的人声降噪（挂在人声镜头上）：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -5.95dB + 音效高 6.0dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_07**：源草稿**不做侧链压缩**：21 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -15.43dB）：音效（0.0dB）比它高 15.4dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.897）
  - 证据：21 条音频段中带 volume 关键帧 0 条；床位 -15.43dB、音效 0.0dB（差 15.4dB）；人声覆盖中位 0.897；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.897）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -15.43 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.897 的人声覆盖下把床压到 -15.43dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 15.4 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 15.4dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -15.43dB + 音效高 15.4dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_08**：源草稿**不做侧链压缩**：30 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -10.01dB）：音效（0.0dB）比它高 10.0dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.702）
  - 证据：30 条音频段中带 volume 关键帧 0 条；床位 -10.01dB、音效 0.0dB（差 10.0dB）；人声覆盖中位 0.702；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.702）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -10.01 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.702 的人声覆盖下把床压到 -10.01dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 10.0 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 10.0dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -10.01dB + 音效高 10.0dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_09**：源草稿**不做侧链压缩**：19 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -9.79dB）：音效（0.0dB）比它高 9.8dB，音效跳在床上面、口播站在最前面；中途只有 1 次手动台阶（20.65s -7.5dB）
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.984）
  - 证据：19 条音频段中带 volume 关键帧 0 条；床位 -9.79dB、音效 0.0dB（差 9.8dB）；人声覆盖中位 0.984；手动台阶 1 次；降噪：无
  - 作用域：全片 / 2 段 BGM 全部压在人声下（覆盖率中位 0.984）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -9.79 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.984 的人声覆盖下把床压到 -9.79dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 9.8 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 9.8dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 1, 'steps': [{'at': 20.65, 'delta_db': -7.48, 'spe | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 台阶全部出现在段落边界（切点/换句附近），方向都是'往下'：源里的做法是'到这一段把床按低一点'，而不是'每句话进来就压一下' |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -9.79dB + 音效高 9.8dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_10**：源草稿**不做侧链压缩**：16 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -8.22dB）：音效（0.0dB）比它高 8.2dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.644）
  - 证据：16 条音频段中带 volume 关键帧 0 条；床位 -8.22dB、音效 0.0dB（差 8.2dB）；人声覆盖中位 0.644；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.644）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -8.22 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.644 的人声覆盖下把床压到 -8.22dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 8.2 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 8.2dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -8.22dB + 音效高 8.2dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_11**：源草稿**不做侧链压缩**：15 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -11.45dB）：音效（0.0dB）比它高 11.4dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.957）
  - 证据：15 条音频段中带 volume 关键帧 0 条；床位 -11.45dB、音效 0.0dB（差 11.4dB）；人声覆盖中位 0.957；手动台阶 0 次；降噪：无
  - 作用域：全片 / 2 段 BGM 全部压在人声下（覆盖率中位 0.957）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -11.45 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.957 的人声覆盖下把床压到 -11.45dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 11.4 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 11.4dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -11.45dB + 音效高 11.4dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_12**：源草稿**不做侧链压缩**：18 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -9.61dB）：音效（-1.725dB）只比它高 7.9dB —— 这套音效本来就轻，层次靠'少'而不是靠'响'；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.913）
  - 证据：18 条音频段中带 volume 关键帧 0 条；床位 -9.61dB、音效 -1.725dB（差 7.9dB）；人声覆盖中位 0.913；手动台阶 0 次；降噪：无
  - 作用域：全片 / 2 段 BGM 全部压在人声下（覆盖率中位 0.913）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -9.61 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.913 的人声覆盖下把床压到 -9.61dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 7.9 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 7.9dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -9.61dB + 音效高 7.9dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.5 LUFS 是编写常量；这套连 SNR 标称值都没有——源草稿没有可测的 SNR 真值（段字段推不出
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_13**：源草稿**不做侧链压缩**：19 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -9.36dB）：音效（0.0dB）比它高 9.4dB，音效跳在床上面、口播站在最前面；中途只有 1 次手动台阶（21.05s -5.3dB）
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.962）
  - 证据：19 条音频段中带 volume 关键帧 0 条；床位 -9.36dB、音效 0.0dB（差 9.4dB）；人声覆盖中位 0.962；手动台阶 1 次；降噪：无
  - 作用域：全片 / 2 段 BGM 全部压在人声下（覆盖率中位 0.962）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -9.36 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.962 的人声覆盖下把床压到 -9.36dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 9.4 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 9.4dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 1, 'steps': [{'at': 21.05, 'delta_db': -5.26, 'spe | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 台阶全部出现在段落边界（切点/换句附近），方向都是'往下'：源里的做法是'到这一段把床按低一点'，而不是'每句话进来就压一下' |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -9.36dB + 音效高 9.4dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.9 LUFS 是编写常量；这套连 SNR 标称值都没有——源草稿没有可测的 SNR 真值（段字段推不出
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_14**：源草稿**不做侧链压缩**：11 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -5.0dB）：音效（0.0dB）只比它高 5.0dB —— 这套音效本来就轻，层次靠'少'而不是靠'响'；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.895）
  - 证据：11 条音频段中带 volume 关键帧 0 条；床位 -5.0dB、音效 0.0dB（差 5.0dB）；人声覆盖中位 0.895；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.895）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -5.0 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.895 的人声覆盖下把床压到 -5.0dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 5.0 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 5.0dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -5.0dB + 音效高 5.0dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -12.6 LUFS 是编写常量；这套连 SNR 标称值都没有——源草稿没有可测的 SNR 真值（段字段推不出
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_15**：源草稿**不做侧链压缩**：36 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -7.0dB）：音效（3.09dB）比它高 10.1dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.859）
  - 证据：36 条音频段中带 volume 关键帧 0 条；床位 -7.0dB、音效 3.09dB（差 10.1dB）；人声覆盖中位 0.859；手动台阶 0 次；降噪：无
  - 作用域：全片 / 3 段 BGM 全部压在人声下（覆盖率中位 0.859）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -7.0 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.859 的人声覆盖下把床压到 -7.0dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 10.1 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 10.1dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -7.0dB + 音效高 10.1dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_16**：源草稿**不做侧链压缩**：14 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -9.55dB）：音效（-5.96dB）只比它高 3.6dB —— 这套音效本来就轻，层次靠'少'而不是靠'响'；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.887）
  - 证据：14 条音频段中带 volume 关键帧 0 条；床位 -9.55dB、音效 -5.96dB（差 3.6dB）；人声覆盖中位 0.887；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.887）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -9.55 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.887 的人声覆盖下把床压到 -9.55dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 3.6 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 3.6dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -9.55dB + 音效高 3.6dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_17**：源草稿**不做侧链压缩**：16 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -12.12dB）：音效（0.0dB）比它高 12.1dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.727）
  - 证据：16 条音频段中带 volume 关键帧 0 条；床位 -12.12dB、音效 0.0dB（差 12.1dB）；人声覆盖中位 0.727；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.727）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -12.12 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.727 的人声覆盖下把床压到 -12.12dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 12.1 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 12.1dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -12.12dB + 音效高 12.1dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_18**：源草稿**不做侧链压缩**：22 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -12.39dB）：音效（-4.35dB）比它高 8.0dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.874）
  - 证据：22 条音频段中带 volume 关键帧 0 条；床位 -12.39dB、音效 -4.35dB（差 8.0dB）；人声覆盖中位 0.874；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.874）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -12.39 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.874 的人声覆盖下把床压到 -12.39dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 8.0 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 8.0dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -12.39dB + 音效高 8.0dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_19**：源草稿**不做侧链压缩**：15 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -14.56dB）：音效（0.0dB）比它高 14.6dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.968）
  - 证据：15 条音频段中带 volume 关键帧 0 条；床位 -14.56dB、音效 0.0dB（差 14.6dB）；人声覆盖中位 0.968；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.968）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -14.56 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.968 的人声覆盖下把床压到 -14.56dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 14.6 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 14.6dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -14.56dB + 音效高 14.6dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_20**：源草稿**不做侧链压缩**：27 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -6.6dB）：音效（5.0dB）比它高 11.6dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.574）
  - 证据：27 条音频段中带 volume 关键帧 0 条；床位 -6.6dB、音效 5.0dB（差 11.6dB）；人声覆盖中位 0.574；手动台阶 0 次；降噪：人声镜头（video 轨）
  - 作用域：全片 / 3 段 BGM 全部压在人声下（覆盖率中位 0.574）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -6.6 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.574 的人声覆盖下把床压到 -6.6dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 11.6 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 11.6dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [0.85], 'n_materials': 14, 'attached_to': '人声镜头（vid | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 开了 [0.85] 的人声降噪（挂在人声镜头上）：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -6.6dB + 音效高 11.6dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_21**：源草稿**不做侧链压缩**：14 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -9.19dB）：音效（0.0dB）比它高 9.2dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 1.0）
  - 证据：14 条音频段中带 volume 关键帧 0 条；床位 -9.19dB、音效 0.0dB（差 9.2dB）；人声覆盖中位 1.0；手动台阶 0 次；降噪：无
  - 作用域：全片 / 2 段 BGM 全部压在人声下（覆盖率中位 1.0）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -9.19 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 1.0 的人声覆盖下把床压到 -9.19dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 9.2 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 9.2dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -9.19dB + 音效高 9.2dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_22**：源草稿**不做侧链压缩**：14 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -11.09dB）：音效（0.0dB）比它高 11.1dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.983）
  - 证据：14 条音频段中带 volume 关键帧 0 条；床位 -11.09dB、音效 0.0dB（差 11.1dB）；人声覆盖中位 0.983；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.983）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -11.09 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.983 的人声覆盖下把床压到 -11.09dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 11.1 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 11.1dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -11.09dB + 音效高 11.1dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_23**：源草稿**不做侧链压缩**：13 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -17.47dB）：音效（2.99dB）比它高 20.5dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.492）
  - 证据：13 条音频段中带 volume 关键帧 0 条；床位 -17.47dB、音效 2.99dB（差 20.5dB）；人声覆盖中位 0.492；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.492）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -17.47 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.492 的人声覆盖下把床压到 -17.47dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 20.5 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 20.5dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -17.47dB + 音效高 20.5dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故
- **style_24**：源草稿**不做侧链压缩**：20 条音频段里带音量关键帧的 0 条。所谓 ducking 就是床位一次压好（时长加权 -13.33dB）：音效（0.0dB）比它高 13.3dB，音效跳在床上面、口播站在最前面；全片没有任何闪避动作
  - 不该出现：纯音乐段/无人声段（这套几乎没有：人声覆盖率中位 0.887）
  - 证据：20 条音频段中带 volume 关键帧 0 条；床位 -13.33dB、音效 0.0dB（差 13.3dB）；人声覆盖中位 0.887；手动台阶 0 次；降噪：无
  - 作用域：全片 / 1 段 BGM 全部压在人声下（覆盖率中位 0.887）（不适用：无）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `bed_gain_db` | -13.33 | BGM 段 volume（时长加权） | bed_db = Σ(gain_i × dur_i)/Σdur_i | 0.887 的人声覆盖下把床压到 -13.33dB：压深了片子发空，压浅了口播糊；这个值在 24 套里跨度从 -5 到 -17dB，随风格（口播密度/曲目响度）走 |
    | `sfx_above_bed_db` | 13.3 | sfx gain 中位 − bed gain（时长加权） | delta = median(sfx_dB) − bed_db | 13.3dB 是源里'音效盖过床'的最小保障；低于 6dB 音效就沉进床里听不见 |
    | `manual_step` | {'count': 0, 'steps': []} | 同名 BGM 相邻段 volume 差（≥3dB 记一次台阶） | step = 后段 dB − 前段 dB | 这套一次台阶都没有——床从头到尾一个电平 |
    | `voice_denoise` | {'rate': [], 'n_materials': 0, 'attached_to': '无'} | materials.realtime_denoises[].denoise_rate / is_ | 剪映'降噪'开关的强度 | 没有开任何降噪：人声干净度是靠录的时候/降噪，不是靠把 BGM 压死 |
  - 影响谁：口播清晰度←床位 -13.33dB + 音效高 13.3dB 构成层次；任一环错位都听成'吵'或'糊'；真峰←床压低后真峰由音效瞬态决定，不由床决定；响度一致性←库里标称 -14.0 LUFS 是编写常量；标称 11.5dB SNR 同样是常量——源草稿没有可测的 SNR 真值（段
  - 耦合：bgm（level_relation）；sfx（level_relation）；dual_bgm_switch（shared_boundary）
  - 违反症状：做成侧链压缩（每句进来压一下）→ 听感变成'呼吸感'，与源那种'稳态发闷'完全不同；只压 BGM 不抬音效 → 音效沉底，等于没有音效；床位跟着音效一起压 → 整片忽大忽小，像音量事故


## 抠像（`cutout`）

**这一镜为什么要抠像**

- **style_01**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 3 段抠像里 3 段在叠加轨、2 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 3 段，落在 track [2, 12, 17]，跨度 0.0–3.0s，覆盖 10.7%、成 1 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 10.7% 片长 / 人物层被抠 18.5%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [0.699, 1.0]（中位 1.0；2/3 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 3 段的 scale 就是这些数：2 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 1 段缩到 [0.699] 成角窗 |
    | `clip.transform.x` | [0.0, 0.38] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.079），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.301, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 1 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.3, 3.0]（中位 3.0） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 3.0s（真值上限 3.75s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
    | `matting.strokes[0].adjust_params.effects_adjust_size` | [0.35]（1 段带描边，色 #FFFFFF） | materials.videos[].matting.strokes[].adjust_para | 环宽_源px = 0.3 + 7.2·size（单层描边）｜= 19.16·size（撕纸描边）｜折线描 | 本套只出现 单层描边：1 段开描边、2 段不开——描边是「人叠在花画面上要分离主体」时才加，不是抠像默认配置 |
    | `matting.strokes[0].adjust_params.effects_adjust_alpha` | [1.0] | materials.videos[].matting.strokes[].adjust_para | — | 描边 alpha 一律 1.0（纯色实线）；半透明白边在真值里不存在，压到 0.5 会像没抠干净 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_02**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 3 段抠像里 3 段在叠加轨、3 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 3 段，落在 track [3]，跨度 12.9–16.167s，覆盖 11.1%、成 1 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 11.1% 片长 / 人物层被抠 99.0%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.0, 1.0]（中位 1.0；3/3 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 3 段的 scale 就是这些数：3 段是 1.0（全屏抠像，人直接叠在满屏副镜上），没有缩窗段 |
    | `clip.transform.x` | [0.0, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.000），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [0.0, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.0, 1.167]（中位 1.1） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 1.167s（真值上限 1.46s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_03**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 6 段抠像里 6 段在叠加轨、2 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 6 段，落在 track [3]，跨度 1.933–124.55s，覆盖 9.7%、成 6 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 9.7% 片长 / 人物层被抠 100.0%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.0, 1.2]（中位 1.028；2/6 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 6 段的 scale 就是这些数：2 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 4 段缩到 [] 成角窗 |
    | `clip.transform.x` | [0.0, 0.057] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 越靠边越接近 |x| = 1−scale：本套精确贴边 1 段，说明 x 是「窗口可动范围」的归一化坐标 |
    | `clip.transform.y` | [-0.477, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [0.733, 4.65]（中位 0.925） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 4.65s（真值上限 5.81s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
    | `matting.strokes[0].adjust_params.effects_adjust_size` | [0.1]（2 段带描边，色 #FFFFFF） | materials.videos[].matting.strokes[].adjust_para | 环宽_源px = 0.3 + 7.2·size（单层描边）｜= 19.16·size（撕纸描边）｜折线描 | 本套只出现 折线描边：2 段开描边、4 段不开——描边是「人叠在花画面上要分离主体」时才加，不是抠像默认配置 |
    | `matting.strokes[0].adjust_params.effects_adjust_alpha` | [1.0] | materials.videos[].matting.strokes[].adjust_para | — | 描边 alpha 一律 1.0（纯色实线）；半透明白边在真值里不存在，压到 0.5 会像没抠干净 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_04**：不出现——真值 0 段：本套 8 段视频（2 条视频轨）里没有任何一段挂 matting（materials.videos[].matting.path 全空；剔除 <0.1s 残段后仍为 0）。
  - 硬加会怎样：给这套加抠像 → 背景出现一层没有素材的黑底（副镜没准备）
- **style_05**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 4 段抠像里 3 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 4 段，落在 track [0, 1, 3]，跨度 9.883–13.533s，覆盖 24.1%、成 1 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 24.1% 片长 / 人物层被抠 18.4%（不适用：主轨 1 段例外（真值就在这里））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.0, 1.241]（中位 1.12；0/4 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 4 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 4 段缩到 [] 成角窗 |
    | `clip.transform.x` | [-0.427, 0.517] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.240），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.303, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [0.883, 2.083]（中位 1.225） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 2.083s（真值上限 2.6s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_06**：不出现——真值 0 段：本套 5 段视频（2 条视频轨）里没有任何一段挂 matting（materials.videos[].matting.path 全空；剔除 <0.1s 残段后仍为 0）。
  - 硬加会怎样：给这套加抠像 → 背景出现一层没有素材的黑底（副镜没准备）
- **style_07**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 4 段抠像里 4 段在叠加轨、1 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 4 段，落在 track [4, 5]，跨度 0.0–23.333s，覆盖 33.2%、成 4 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 33.2% 片长 / 人物层被抠 47.9%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.0, 1.2]（中位 1.2；1/4 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 4 段的 scale 就是这些数：1 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 3 段缩到 [] 成角窗 |
    | `clip.transform.x` | [-0.117, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.083），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [0.0, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.4, 4.267]（中位 1.833） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 4.267s（真值上限 5.33s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
    | `matting.strokes[0].adjust_params.effects_adjust_size` | [0.35]（1 段带描边，色 #FFFFFF） | materials.videos[].matting.strokes[].adjust_para | 环宽_源px = 0.3 + 7.2·size（单层描边）｜= 19.16·size（撕纸描边）｜折线描 | 本套只出现 撕纸描边：1 段开描边、3 段不开——描边是「人叠在花画面上要分离主体」时才加，不是抠像默认配置 |
    | `matting.strokes[0].adjust_params.effects_adjust_alpha` | 该资源（撕纸描边）没有 alpha 滑杆：描边恒为不透明 | materials.videos[].matting.strokes[].adjust_para | — | 包内没有 alpha 绑定，说明「白边就是白边」，想做淡边只能换资源 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_08**：不出现——真值 0 段：本套 2 段视频（1 条视频轨）里没有任何一段挂 matting（materials.videos[].matting.path 全空；剔除 <0.1s 残段后仍为 0）。
  - 硬加会怎样：给这套加抠像 → 背景出现一层没有素材的黑底（副镜没准备）
- **style_09**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 1 段抠像里 1 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 1 段，落在 track [5]，跨度 2.967–3.3s，覆盖 0.8%、成 1 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 0.8% 片长 / 人物层被抠 3.7%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [0.919, 0.919]（中位 0.919；0/1 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 1 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 1 段缩到 [0.919] 成角窗 |
    | `clip.transform.x` | [0.0, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.081），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [0.357, 0.357] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [0.333, 0.333]（中位 0.333） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 0.333s（真值上限 0.42s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_10**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 8 段抠像里 8 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 8 段，落在 track [5]，跨度 0.0–16.966s，覆盖 53.1%、成 4 段连续抠像；与指认型台词同框 1 段
  - 作用域：段（连续成段，不是逐镜） / 53.1% 片长 / 人物层被抠 92.5%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.1, 1.2]（中位 1.15；0/8 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 8 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 8 段缩到 [] 成角窗 |
    | `clip.transform.x` | [-0.1, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 越靠边越接近 |x| = 1−scale：本套精确贴边 2 段，说明 x 是「窗口可动范围」的归一化坐标 |
    | `clip.transform.y` | [-0.1, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [0.733, 2.083]（中位 1.192） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 2.083s（真值上限 2.6s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_11**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 7 段抠像里 6 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 7 段，落在 track [0, 4, 5]，跨度 0.0–23.9s，覆盖 44.1%、成 3 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 44.1% 片长 / 人物层被抠 30.4%（不适用：主轨 1 段例外（真值就在这里））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.0, 1.1]（中位 1.1；0/7 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 7 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 7 段缩到 [] 成角窗 |
    | `clip.transform.x` | [-0.289, 0.364] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.100），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.624, 0.086] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.133, 3.217]（中位 1.433） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 3.217s（真值上限 4.02s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
    | `clip.alpha` | [0.0, 0.65] | segments[].clip.alpha（字段缺省 = 1.0） | — | 抠像段不做淡入淡出：alpha 由入场动画（位移/缩放）承担，改 alpha 会和背景层合成出灰边 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_12**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 1 段抠像里 1 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 1 段，落在 track [2]，跨度 17.85–19.2s，覆盖 5.1%、成 1 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 5.1% 片长 / 人物层被抠 100.0%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.3, 1.3]（中位 1.3；0/1 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 1 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 1 段缩到 [] 成角窗 |
    | `clip.transform.x` | [0.0, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.300），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.157, -0.157] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.35, 1.35]（中位 1.35） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 1.35s（真值上限 1.69s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_13**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 4 段抠像里 4 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 4 段，落在 track [2]，跨度 3.367–26.417s，覆盖 27.7%、成 3 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 27.7% 片长 / 人物层被抠 72.2%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [0.346, 1.2]（中位 1.1；0/4 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 4 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 4 段缩到 [0.346] 成角窗 |
    | `clip.transform.x` | [-0.49, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.100），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.05, 0.445] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.233, 3.95]（中位 1.591） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 3.95s（真值上限 4.94s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_14**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 2 段抠像里 2 段在叠加轨、2 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 2 段，落在 track [2]，跨度 15.2–18.333s，覆盖 15.0%、成 1 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 15.0% 片长 / 人物层被抠 100.0%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.0, 1.0]（中位 1.0；2/2 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 2 段的 scale 就是这些数：2 段是 1.0（全屏抠像，人直接叠在满屏副镜上），没有缩窗段 |
    | `clip.transform.x` | [0.0, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.000），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [0.0, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.133, 2.0]（中位 1.567） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 2.0s（真值上限 2.5s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_15**：不出现——真值 0 段：本套 26 段视频（6 条视频轨）里没有任何一段挂 matting（materials.videos[].matting.path 全空；剔除 <0.1s 残段后仍为 0）。
  - 硬加会怎样：给这套加抠像 → 背景出现一层没有素材的黑底（副镜没准备）
- **style_16**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 5 段抠像里 5 段在叠加轨、3 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 5 段，落在 track [5, 13]，跨度 2.417–20.3s，覆盖 37.1%、成 2 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 37.1% 片长 / 人物层被抠 100.0%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.0, 1.741]（中位 1.0；3/5 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 5 段的 scale 就是这些数：3 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 2 段缩到 [] 成角窗 |
    | `clip.transform.x` | [0.0, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.100），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [0.0, 0.127] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.317, 2.983]（中位 2.233） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 2.983s（真值上限 3.73s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_17**：不出现——真值 0 段：本套 16 段视频（4 条视频轨）里没有任何一段挂 matting（materials.videos[].matting.path 全空；剔除 <0.1s 残段后仍为 0）。
  - 硬加会怎样：给这套加抠像 → 背景出现一层没有素材的黑底（副镜没准备）
- **style_18**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 4 段抠像里 4 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 4 段，落在 track [3]，跨度 1.5–28.6s，覆盖 20.2%、成 3 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 20.2% 片长 / 人物层被抠 48.4%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.3, 1.5]（中位 1.5；0/4 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 4 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 4 段缩到 [] 成角窗 |
    | `clip.transform.x` | [-0.179, 0.201] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.163），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.143, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.017, 2.85]（中位 1.925） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 2.85s（真值上限 3.56s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_19**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 5 段抠像里 5 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 5 段，落在 track [3, 4]，跨度 0.0–20.75s，覆盖 24.4%、成 4 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 24.4% 片长 / 人物层被抠 76.2%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [0.642, 1.3]（中位 1.3；0/5 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 5 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 5 段缩到 [0.642] 成角窗 |
    | `clip.transform.x` | [0.0, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.300），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [0.0, 0.099] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [0.9, 3.967]（中位 1.383） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 3.967s（真值上限 4.96s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_20**：不出现——真值 0 段：本套 20 段视频（3 条视频轨）里没有任何一段挂 matting（materials.videos[].matting.path 全空；剔除 <0.1s 残段后仍为 0）。
  - 硬加会怎样：给这套加抠像 → 背景出现一层没有素材的黑底（副镜没准备）
- **style_21**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 7 段抠像里 7 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 7 段，落在 track [6]，跨度 0.0–10.017s，覆盖 61.2%、成 1 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 61.2% 片长 / 人物层被抠 100.0%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.2, 1.6]（中位 1.2；0/7 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 7 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 7 段缩到 [] 成角窗 |
    | `clip.transform.x` | [0.0, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.200），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.199, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [0.917, 2.017]（中位 1.233） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 2.017s（真值上限 2.52s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
    | `matting.strokes[0].adjust_params.effects_adjust_size` | [0.35]（1 段带描边，色 #FFFFFF） | materials.videos[].matting.strokes[].adjust_para | 环宽_源px = 0.3 + 7.2·size（单层描边）｜= 19.16·size（撕纸描边）｜折线描 | 本套只出现 撕纸描边：1 段开描边、6 段不开——描边是「人叠在花画面上要分离主体」时才加，不是抠像默认配置 |
    | `matting.strokes[0].adjust_params.effects_adjust_alpha` | 该资源（撕纸描边）没有 alpha 滑杆：描边恒为不透明 | materials.videos[].matting.strokes[].adjust_para | — | 包内没有 alpha 绑定，说明「白边就是白边」，想做淡边只能换资源 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_22**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 4 段抠像里 4 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 4 段，落在 track [3]，跨度 0.0–15.6s，覆盖 26.4%、成 3 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 26.4% 片长 / 人物层被抠 64.4%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.1, 1.431]（中位 1.15；0/4 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 4 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 4 段缩到 [] 成角窗 |
    | `clip.transform.x` | [-0.059, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.100），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.183, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [0.967, 2.283]（中位 1.475） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 2.283s（真值上限 2.85s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_23**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 1 段抠像里 1 段在叠加轨、0 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 1 段，落在 track [10]，跨度 4.867–14.4s，覆盖 22.8%、成 1 段连续抠像；与指认型台词同框 0 段
  - 作用域：段（连续成段，不是逐镜） / 22.8% 片长 / 人物层被抠 65.6%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [1.0, 1.0]（中位 1.0；0/1 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 1 段的 scale 就是这些数：0 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 1 段缩到 [] 成角窗 |
    | `clip.transform.x` | [0.184, 0.184] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.000），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [-0.275, -0.275] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [9.533, 9.533]（中位 9.533） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 9.533s（真值上限 11.92s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
    | `matting.strokes[0].adjust_params.effects_adjust_size` | [0.12]（1 段带描边，色 #FFFFFF） | materials.videos[].matting.strokes[].adjust_para | 环宽_源px = 0.3 + 7.2·size（单层描边）｜= 19.16·size（撕纸描边）｜折线描 | 本套只出现 折线描边：1 段开描边、0 段不开——描边是「人叠在花画面上要分离主体」时才加，不是抠像默认配置 |
    | `matting.strokes[0].adjust_params.effects_adjust_alpha` | [1.0] | materials.videos[].matting.strokes[].adjust_para | — | 描边 alpha 一律 1.0（纯色实线）；半透明白边在真值里不存在，压到 0.5 会像没抠干净 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗
- **style_24**：抠像的因果是「这一镜要同时看到人和另一层画面」：人从背景提出来 → 下面垫一条不同源的副镜 → 叠加轨（render_index>0）出画。本套 3 段抠像里 3 段在叠加轨、1 段仍是全屏 scale=1（人满屏叠在满屏副镜上，抠不抠看不出窗，只是把原背景换掉）。
  - 不该出现：画面里正给人看图纸/屏幕/样张（抠像会把要看的东西一起抠掉）；副镜与主镜同源（自己叠自己，边缘会双影）；空镜/手部/图卡特写（没有人像主体可提）
  - 证据：materials.videos[].matting.path 非空 + 剔除 <0.1s 残段 = 3 段，落在 track [3]，跨度 28.05–33.734s，覆盖 15.6%、成 1 段连续抠像；与指认型台词同框 1 段
  - 作用域：段（连续成段，不是逐镜） / 15.6% 片长 / 人物层被抠 30.0%（不适用：B-roll、图卡、照片、纯字幕层；主轨无抠像）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale / uniform_scale.scale` | [0.8, 1.0]（中位 0.8；1/3 段 =1.0 全屏） | segments[].uniform_scale.scale | segments[].clip | disp_w = W·scale；disp_h = (nh/nw)·W·scale（scale=1 表示 | 真值 3 段的 scale 就是这些数：1 段是 1.0（全屏抠像，人直接叠在满屏副镜上），其余 2 段缩到 [0.8] 成角窗 |
    | `clip.transform.x` | [0.0, 0.0] | segments[].clip.transform.x（-1..1，0=居中，±(1−scale | left_px = (W/2)·(x + 1 − scale)；cx = W/2·(1+x) | 本套没有精确贴边的窗（最近一段离边界 0.200），x 仍是可动范围归一化坐标，只是这一套都留了边 |
    | `clip.transform.y` | [0.0, 0.0] | segments[].clip.transform.y | top_px = (H/2)·(y + 1 − disp_h/H)；y = −(1 − disp_h/H | 人像抠像默认「头顶贴边、脚出画」——本套 0 段 y 落在贴顶位置 |
    | `target_timerange.duration` | [1.267, 2.267]（中位 2.15） | segments[].target_timerange.duration | 片段时长 = 口播句/动作过程长度；不额外补长 | 最长 2.267s（真值上限 2.83s）；抠像是「一段」而不是「一镜」，进出抠像状态本身是画面变化，所以成段出现、不逐镜开关 |
  - 影响谁：背景层（副镜）←抠出来才有「下面」；副镜与主镜必须不同源，否则边缘双影；花字/字幕←人像成为前景后，花字要压在人的留白侧；缩窗时花字必须避开窗口矩形；描边←描边宽 = (0.3+7.2·size)×scale，缩窗后同样变细，所以小窗要么不加描边要么加粗 size
  - 耦合：overlay_geometry（same_source_of_truth）；pip（subset）；color_grade（ordering）
  - 违反症状：把「讲解效果图」的镜头抠了 → 图纸被抠没，只剩人对着空白讲（真值只有 2 段踩到指认台词，本机复现过）；副镜拿同一段素材 → 同源套娃，边缘一圈双影，质检会判重帧；抠像缩窗后没同步缩描边 → 白边比人还粗


## 变速（`speed`）

**这一段为什么要变速**

- **style_01**：变速只服务一件事：把「无信息等待」压掉——B-roll 里的走路/操作/翻页过程按 2× 快进，口播层一格不动。本套 1 段全部在叠加层（track [2]），主轨 0 段。
  - 不该出现：主轨口播；静图/照片（没有时间基准）；字幕/贴纸层
  - 证据：1 段 source/target 比值 = [2.0]，与 materials.speeds[].speed 字段一致；全部 render_index>0（主轨 0 段）
  - 作用域：段 / 1 段 / 叠加层 31 段（不适用：主轨 A-roll、字幕、贴纸）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `speed（source_dur / target_dur）` | [2.0]（1 段全同值） | segments[].source_timerange.duration ÷ target_ti | speed = src_dur / tgt_dur；tgt_dur = src_dur / speed | 真值只出现 [2.0]，是整数倍：2× 让 1.33s 的过程压成 0.67s，刚好一个快切窗口；1.5×/1.35× 这类「中间值」真值里一次都没有 |
    | `target_timerange.duration` | [1.333, 1.333]（中位 1.333） | segments[].target_timerange.duration | tgt_dur = src_dur / 2 | 压完的落点仍要 ≥ 0.3s：再短观众看不出这是同一动作，只觉得闪了一下 |
    | `作用层（role）` | overlay only | tracks[].type + 段 render_index | — | 口播层倍速会改口型/字音对应；B-roll 无同步声，压了只影响画面 |
  - 影响谁：音轨←同段音频按同倍率拉伸（或静音）；不拉伸 = 音画分离；字幕时间戳←变速段若压着台词，字幕时间戳必须按 target 时间重算，否则漂 50%；源帧占用←2× 用掉的源区间是落点的 2 倍；同一源帧不许再被别的镜引用，否则质检判倒带
  - 耦合：subtitle（ordering）；pip（co_occur）；bgm（side_effect）
  - 违反症状：同一段源帧被两镜占用（一镜 2×、一镜原速）→ 质检判「倒带」；主轨 1.5× → 口型错位；且字幕没重算 → 字比声晚半句；把 1.33s 的过程压到 0.2s → 观众只看到一帧闪
- **style_02**：不出现——真值 0 段：29 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_03**：不出现——真值 0 段：55 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_04**：不出现——真值 0 段：8 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_05**：不出现——真值 0 段：22 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_06**：不出现——真值 0 段：5 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_07**：不出现——真值 0 段：30 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_08**：不出现——真值 0 段：2 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_09**：不出现——真值 0 段：37 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_10**：不出现——真值 0 段：25 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_11**：不出现——真值 0 段：27 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_12**：不出现——真值 0 段：14 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_13**：不出现——真值 0 段：23 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_14**：不出现——真值 0 段：13 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_15**：不出现——真值 0 段：26 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_16**：不出现——真值 0 段：24 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_17**：不出现——真值 0 段：16 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_18**：不出现——真值 0 段：32 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_19**：不出现——真值 0 段：29 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_20**：不出现——真值 0 段：20 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_21**：不出现——真值 0 段：25 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_22**：不出现——真值 0 段：26 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_23**：不出现——真值 0 段：15 段视频逐段算 source_timerange/target_timerange 比值 = 1.000，主轨与叠加层都没有变速。
  - 硬加会怎样：主轨加 1.2× → 口型对不上，质检判音画不同步
- **style_24**：变速只服务一件事：把「无信息等待」压掉——B-roll 里的走路/操作/翻页过程按 2× 快进，口播层一格不动。本套 3 段全部在叠加层（track [7, 8, 9]），主轨 0 段。
  - 不该出现：主轨口播；静图/照片（没有时间基准）；字幕/贴纸层
  - 证据：3 段 source/target 比值 = [2.0]，与 materials.speeds[].speed 字段一致；全部 render_index>0（主轨 0 段）
  - 作用域：段 / 3 段 / 叠加层 15 段（不适用：主轨 A-roll、字幕、贴纸）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `speed（source_dur / target_dur）` | [2.0]（3 段全同值） | segments[].source_timerange.duration ÷ target_ti | speed = src_dur / tgt_dur；tgt_dur = src_dur / speed | 真值只出现 [2.0]，是整数倍：2× 让 1.33s 的过程压成 0.67s，刚好一个快切窗口；1.5×/1.35× 这类「中间值」真值里一次都没有 |
    | `target_timerange.duration` | [1.033, 2.55]（中位 1.85） | segments[].target_timerange.duration | tgt_dur = src_dur / 2 | 压完的落点仍要 ≥ 0.3s：再短观众看不出这是同一动作，只觉得闪了一下 |
    | `作用层（role）` | overlay only | tracks[].type + 段 render_index | — | 口播层倍速会改口型/字音对应；B-roll 无同步声，压了只影响画面 |
  - 影响谁：音轨←同段音频按同倍率拉伸（或静音）；不拉伸 = 音画分离；字幕时间戳←变速段若压着台词，字幕时间戳必须按 target 时间重算，否则漂 50%；源帧占用←2× 用掉的源区间是落点的 2 倍；同一源帧不许再被别的镜引用，否则质检判倒带
  - 耦合：subtitle（ordering）；pip（co_occur）；bgm（side_effect）
  - 违反症状：同一段源帧被两镜占用（一镜 2×、一镜原速）→ 质检判「倒带」；主轨 1.5× → 口型错位；且字幕没重算 → 字比声晚半句；把 1.33s 的过程压到 0.2s → 观众只看到一帧闪


## 转场（`transition`）

**这里为什么没有转场**

- **style_01**：不出现——真值 0 次：materials.transitions 条数 = 0，且 55 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_02**：不出现——真值 0 次：materials.transitions 条数 = 0，且 29 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_03**：不出现——真值 0 次：materials.transitions 条数 = 0，且 55 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_04**：不出现——真值 0 次：materials.transitions 条数 = 0，且 8 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_05**：不出现——真值 0 次：materials.transitions 条数 = 0，且 22 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_06**：不出现——真值 0 次：materials.transitions 条数 = 0，且 5 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_07**：不出现——真值 0 次：materials.transitions 条数 = 0，且 30 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_08**：不出现——真值 0 次：materials.transitions 条数 = 0，且 2 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_09**：不出现——真值 0 次：materials.transitions 条数 = 0，且 37 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_10**：不出现——真值 0 次：materials.transitions 条数 = 0，且 25 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_11**：不出现——真值 0 次：materials.transitions 条数 = 0，且 27 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_12**：不出现——真值 0 次：materials.transitions 条数 = 0，且 14 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_13**：不出现——真值 0 次：materials.transitions 条数 = 0，且 23 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_14**：不出现——真值 0 次：materials.transitions 条数 = 0，且 13 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_15**：不出现——真值 0 次：materials.transitions 条数 = 0，且 26 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_16**：不出现——真值 0 次：materials.transitions 条数 = 0，且 24 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_17**：不出现——真值 0 次：materials.transitions 条数 = 0，且 16 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_18**：不出现——真值 0 次：materials.transitions 条数 = 0，且 32 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_19**：不出现——真值 0 次：materials.transitions 条数 = 0，且 29 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_20**：不出现——真值 0 次：materials.transitions 条数 = 0，且 20 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_21**：不出现——真值 0 次：materials.transitions 条数 = 0，且 25 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_22**：不出现——真值 0 次：materials.transitions 条数 = 0，且 26 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_23**：不出现——真值 0 次：materials.transitions 条数 = 0，且 15 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完
- **style_24**：不出现——真值 0 次：materials.transitions 条数 = 0，且 45 段视频的 extra_material_refs 里没有任何一条指向 transition。这套的时间缝合方式是「硬切」——切点本身就是换场。
  - 硬加会怎样：在句内切点加 0.5s 叠化 → 字幕闪断，读者读不完


## 特效（`effect`）

**这里为什么挂特效**

- **style_01**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 25 条轨 = {'video': 10, 'text': 11, 'audio': 4}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_02**：画面特效只有两种合法用法：①氛围类长时间盖住一个段落（暗角/边框/噪点，≥1s）；②冲击类 ≤0.6s 贴在切点上（模糊/颤抖）。本套 1 个：氛围类 1 个、冲击类 0 个，累计覆盖 17.5% 片长。
  - 不该出现：整片挂滤镜（覆盖 >20% 片长）；盖住人讲话的正脸（观感像坏片）；瞬时特效离切点 >0.25s（渲染出来像闪帧/坏帧）
  - 证据：effect 轨 1 段：['暗角']；时长 [5.167, 5.167]s（中位 5.167）；起点距最近切点 [0, 0]s；覆盖 17.5%（真值口径 17.5%）
  - 作用域：段（effect 轨整段） / 1 段 / 17.5% 片长（不适用：口播正脸特写、抠像边缘）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `target_timerange.duration` | [5.167, 5.167]（中位 5.167） | effect 轨 segments[].target_timerange.duration | 氛围类：duration ≈ 段落长度；冲击类：duration ≤ 0.6 | 本套氛围类 1 个跨整个段落、冲击类 0 个只 0.17–0.6s——两种用法不能混：长盖的不能短、冲击的不能长 |
    | `起点对切点的偏移` | [0, 0]s | effect 轨 segments[].target_timerange.start − 最近主 | offset = |start − nearest_cut|，瞬时类要求 ≤0.25 | 冲击类必须贴在切点上（切点＝观众眨眼的那一帧）；氛围类可以落在段中 |
    | `adjust_params（强度）` | 暗角: effects_adjust_texture=1.0 | materials.video_effects[].adjust_params[].value | 强度 = value；与 default_value 的比值说明「用了几成」 | 暗角.effects_adjust_texture=1.0（默认 1.0 → 1.00×） |
    | `覆盖占比` | 17.5% | Σ effect 时长 ÷ 片长 | coverage = Σdur / duration | 真值上限 26.9%；特效是段落语气，不是全片底色 |
  - 影响谁：调色←特效叠在 LUT 之后：暗角会让边缘掉 1–2 档，调色时要按「有暗角」的结果校 y_mean；字幕对比度←暗角托底时字幕可以只靠 shadow；无暗角必须描边；抠像边缘←冲击特效压在抠像边缘 → 白边抖成锯齿
  - 耦合：color_grade（ordering）；cutout（mutex_in_frame）；sticker（ordering）
  - 违反症状：把「暗角」当全片滤镜挂 → 覆盖 100%，画面像监控回放；瞬时特效离切点 1s 以上 → 渲染出来像坏帧，观众以为文件损坏；特效强度抄默认值（颤抖 range 0.15/speed 0.33）→ 晃到看不清字，真值压到 0.09/0.12
- **style_03**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 16 条轨 = {'video': 2, 'text': 10, 'audio': 4}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_04**：画面特效只有两种合法用法：①氛围类长时间盖住一个段落（暗角/边框/噪点，≥1s）；②冲击类 ≤0.6s 贴在切点上（模糊/颤抖）。本套 1 个：氛围类 1 个、冲击类 0 个，累计覆盖 21.4% 片长。
  - 不该出现：整片挂滤镜（覆盖 >20% 片长）；盖住人讲话的正脸（观感像坏片）；瞬时特效离切点 >0.25s（渲染出来像闪帧/坏帧）
  - 证据：effect 轨 1 段：['怀旧边框']；时长 [4.167, 4.167]s（中位 4.167）；起点距最近切点 [0, 0]s；覆盖 21.4%（真值口径 21.4%）
  - 作用域：段（effect 轨整段） / 1 段 / 21.4% 片长（不适用：口播正脸特写、抠像边缘）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `target_timerange.duration` | [4.167, 4.167]（中位 4.167） | effect 轨 segments[].target_timerange.duration | 氛围类：duration ≈ 段落长度；冲击类：duration ≤ 0.6 | 本套氛围类 1 个跨整个段落、冲击类 0 个只 0.17–0.6s——两种用法不能混：长盖的不能短、冲击的不能长 |
    | `起点对切点的偏移` | [0, 0]s | effect 轨 segments[].target_timerange.start − 最近主 | offset = |start − nearest_cut|，瞬时类要求 ≤0.25 | 冲击类必须贴在切点上（切点＝观众眨眼的那一帧）；氛围类可以落在段中 |
    | `adjust_params（强度）` | 怀旧边框: effects_adjust_vertical_shift=0.44,effects_adjust_size | materials.video_effects[].adjust_params[].value | 强度 = value；与 default_value 的比值说明「用了几成」 | 怀旧边框.effects_adjust_vertical_shift=0.44（默认 0.5 → 0.88×）; 怀旧边框.effects_adjust_size=0.49（默认  |
    | `覆盖占比` | 21.4% | Σ effect 时长 ÷ 片长 | coverage = Σdur / duration | 真值上限 31.8%；特效是段落语气，不是全片底色 |
  - 影响谁：调色←特效叠在 LUT 之后：暗角会让边缘掉 1–2 档，调色时要按「有暗角」的结果校 y_mean；字幕对比度←暗角托底时字幕可以只靠 shadow；无暗角必须描边；抠像边缘←冲击特效压在抠像边缘 → 白边抖成锯齿
  - 耦合：color_grade（ordering）；cutout（mutex_in_frame）；sticker（ordering）
  - 违反症状：把「暗角」当全片滤镜挂 → 覆盖 100%，画面像监控回放；瞬时特效离切点 1s 以上 → 渲染出来像坏帧，观众以为文件损坏；特效强度抄默认值（颤抖 range 0.15/speed 0.33）→ 晃到看不清字，真值压到 0.09/0.12
- **style_05**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 10 条轨 = {'video': 3, 'text': 4, 'sticker': 1, 'audio': 2}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_06**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 8 条轨 = {'video': 2, 'text': 4, 'audio': 2}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_07**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 16 条轨 = {'video': 5, 'text': 3, 'sticker': 3, 'audio': 5}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_08**：画面特效只有两种合法用法：①氛围类长时间盖住一个段落（暗角/边框/噪点，≥1s）；②冲击类 ≤0.6s 贴在切点上（模糊/颤抖）。本套 4 个：氛围类 4 个、冲击类 0 个，累计覆盖 28.8% 片长。
  - 不该出现：整片挂滤镜（覆盖 >20% 片长）；盖住人讲话的正脸（观感像坏片）；瞬时特效离切点 >0.25s（渲染出来像闪帧/坏帧）
  - 证据：effect 轨 4 段：['暗角', '不规则黑框', '荧幕噪点', '暗角']；时长 [2.0, 6.033]s（中位 4.4）；起点距最近切点 [10.967, 20.233]s；覆盖 28.8%（真值口径 28.8%）
  - 作用域：段（effect 轨整段） / 4 段 / 28.8% 片长（不适用：口播正脸特写、抠像边缘）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `target_timerange.duration` | [2.0, 6.033]（中位 4.4） | effect 轨 segments[].target_timerange.duration | 氛围类：duration ≈ 段落长度；冲击类：duration ≤ 0.6 | 本套氛围类 4 个跨整个段落、冲击类 0 个只 0.17–0.6s——两种用法不能混：长盖的不能短、冲击的不能长 |
    | `起点对切点的偏移` | [10.967, 20.233]s | effect 轨 segments[].target_timerange.start − 最近主 | offset = |start − nearest_cut|，瞬时类要求 ≤0.25 | 冲击类必须贴在切点上（切点＝观众眨眼的那一帧）；氛围类可以落在段中 |
    | `adjust_params（强度）` | 暗角: effects_adjust_texture=1.0; 不规则黑框: effects_adjust_backgr | materials.video_effects[].adjust_params[].value | 强度 = value；与 default_value 的比值说明「用了几成」 | 暗角.effects_adjust_texture=1.0（默认 1.0 → 1.00×）; 不规则黑框.effects_adjust_background_animation=1 |
    | `覆盖占比` | 28.8% | Σ effect 时长 ÷ 片长 | coverage = Σdur / duration | 真值上限 35.0%；特效是段落语气，不是全片底色 |
  - 影响谁：调色←特效叠在 LUT 之后：暗角会让边缘掉 1–2 档，调色时要按「有暗角」的结果校 y_mean；字幕对比度←暗角托底时字幕可以只靠 shadow；无暗角必须描边；抠像边缘←冲击特效压在抠像边缘 → 白边抖成锯齿
  - 耦合：color_grade（ordering）；cutout（mutex_in_frame）；sticker（ordering）
  - 违反症状：把「暗角」当全片滤镜挂 → 覆盖 100%，画面像监控回放；瞬时特效离切点 1s 以上 → 渲染出来像坏帧，观众以为文件损坏；特效强度抄默认值（颤抖 range 0.15/speed 0.33）→ 晃到看不清字，真值压到 0.09/0.12
- **style_09**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 14 条轨 = {'video': 4, 'text': 7, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_10**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 12 条轨 = {'video': 3, 'text': 5, 'audio': 4}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_11**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 13 条轨 = {'video': 4, 'text': 6, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_12**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 9 条轨 = {'video': 2, 'text': 4, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_13**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 10 条轨 = {'video': 2, 'text': 4, 'sticker': 1, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_14**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 11 条轨 = {'video': 2, 'text': 5, 'sticker': 1, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_15**：画面特效只有两种合法用法：①氛围类长时间盖住一个段落（暗角/边框/噪点，≥1s）；②冲击类 ≤0.6s 贴在切点上（模糊/颤抖）。本套 2 个：氛围类 2 个、冲击类 0 个，累计覆盖 16.9% 片长。
  - 不该出现：整片挂滤镜（覆盖 >20% 片长）；盖住人讲话的正脸（观感像坏片）；瞬时特效离切点 >0.25s（渲染出来像闪帧/坏帧）
  - 证据：effect 轨 2 段：['暗角', '暗角']；时长 [1.833, 3.333]s（中位 2.583）；起点距最近切点 [0, 0]s；覆盖 16.9%（真值口径 16.9%）
  - 作用域：段（effect 轨整段） / 2 段 / 16.9% 片长（不适用：口播正脸特写、抠像边缘）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `target_timerange.duration` | [1.833, 3.333]（中位 2.583） | effect 轨 segments[].target_timerange.duration | 氛围类：duration ≈ 段落长度；冲击类：duration ≤ 0.6 | 本套氛围类 2 个跨整个段落、冲击类 0 个只 0.17–0.6s——两种用法不能混：长盖的不能短、冲击的不能长 |
    | `起点对切点的偏移` | [0, 0]s | effect 轨 segments[].target_timerange.start − 最近主 | offset = |start − nearest_cut|，瞬时类要求 ≤0.25 | 冲击类必须贴在切点上（切点＝观众眨眼的那一帧）；氛围类可以落在段中 |
    | `adjust_params（强度）` | 暗角: effects_adjust_texture=1.0; 暗角: effects_adjust_texture=1 | materials.video_effects[].adjust_params[].value | 强度 = value；与 default_value 的比值说明「用了几成」 | 暗角.effects_adjust_texture=1.0（默认 1.0 → 1.00×）; 暗角.effects_adjust_texture=1.0（默认 1.0 → 1.00 |
    | `覆盖占比` | 16.9% | Σ effect 时长 ÷ 片长 | coverage = Σdur / duration | 真值上限 26.1%；特效是段落语气，不是全片底色 |
  - 影响谁：调色←特效叠在 LUT 之后：暗角会让边缘掉 1–2 档，调色时要按「有暗角」的结果校 y_mean；字幕对比度←暗角托底时字幕可以只靠 shadow；无暗角必须描边；抠像边缘←冲击特效压在抠像边缘 → 白边抖成锯齿
  - 耦合：color_grade（ordering）；cutout（mutex_in_frame）；sticker（ordering）
  - 违反症状：把「暗角」当全片滤镜挂 → 覆盖 100%，画面像监控回放；瞬时特效离切点 1s 以上 → 渲染出来像坏帧，观众以为文件损坏；特效强度抄默认值（颤抖 range 0.15/speed 0.33）→ 晃到看不清字，真值压到 0.09/0.12
- **style_16**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 17 条轨 = {'video': 4, 'text': 10, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_17**：画面特效只有两种合法用法：①氛围类长时间盖住一个段落（暗角/边框/噪点，≥1s）；②冲击类 ≤0.6s 贴在切点上（模糊/颤抖）。本套 1 个：氛围类 1 个、冲击类 0 个，累计覆盖 9.6% 片长。
  - 不该出现：整片挂滤镜（覆盖 >20% 片长）；盖住人讲话的正脸（观感像坏片）；瞬时特效离切点 >0.25s（渲染出来像闪帧/坏帧）
  - 证据：effect 轨 1 段：['颤抖']；时长 [1.533, 1.533]s（中位 1.533）；起点距最近切点 [0, 0]s；覆盖 9.6%（真值口径 9.6%）
  - 作用域：段（effect 轨整段） / 1 段 / 9.6% 片长（不适用：口播正脸特写、抠像边缘）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `target_timerange.duration` | [1.533, 1.533]（中位 1.533） | effect 轨 segments[].target_timerange.duration | 氛围类：duration ≈ 段落长度；冲击类：duration ≤ 0.6 | 本套氛围类 1 个跨整个段落、冲击类 0 个只 0.17–0.6s——两种用法不能混：长盖的不能短、冲击的不能长 |
    | `起点对切点的偏移` | [0, 0]s | effect 轨 segments[].target_timerange.start − 最近主 | offset = |start − nearest_cut|，瞬时类要求 ≤0.25 | 冲击类必须贴在切点上（切点＝观众眨眼的那一帧）；氛围类可以落在段中 |
    | `adjust_params（强度）` | 颤抖: effects_adjust_range=0.09,effects_adjust_speed=0.12 | materials.video_effects[].adjust_params[].value | 强度 = value；与 default_value 的比值说明「用了几成」 | 颤抖.effects_adjust_range=0.09（默认 0.15 → 0.60×）; 颤抖.effects_adjust_speed=0.12（默认 0.33 → 0.36 |
    | `覆盖占比` | 9.6% | Σ effect 时长 ÷ 片长 | coverage = Σdur / duration | 真值上限 17.0%；特效是段落语气，不是全片底色 |
  - 影响谁：调色←特效叠在 LUT 之后：暗角会让边缘掉 1–2 档，调色时要按「有暗角」的结果校 y_mean；字幕对比度←暗角托底时字幕可以只靠 shadow；无暗角必须描边；抠像边缘←冲击特效压在抠像边缘 → 白边抖成锯齿
  - 耦合：color_grade（ordering）；cutout（mutex_in_frame）；sticker（ordering）
  - 违反症状：把「暗角」当全片滤镜挂 → 覆盖 100%，画面像监控回放；瞬时特效离切点 1s 以上 → 渲染出来像坏帧，观众以为文件损坏；特效强度抄默认值（颤抖 range 0.15/speed 0.33）→ 晃到看不清字，真值压到 0.09/0.12
- **style_18**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 10 条轨 = {'video': 2, 'text': 4, 'sticker': 1, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_19**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 11 条轨 = {'video': 4, 'text': 3, 'sticker': 1, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_20**：画面特效只有两种合法用法：①氛围类长时间盖住一个段落（暗角/边框/噪点，≥1s）；②冲击类 ≤0.6s 贴在切点上（模糊/颤抖）。本套 2 个：氛围类 1 个、冲击类 1 个，累计覆盖 12.2% 片长。
  - 不该出现：整片挂滤镜（覆盖 >20% 片长）；盖住人讲话的正脸（观感像坏片）；瞬时特效离切点 >0.25s（渲染出来像闪帧/坏帧）
  - 证据：effect 轨 2 段：['不规则黑框', '聚光灯']；时长 [0.833, 2.9]s（中位 1.866）；起点距最近切点 [0, 0]s；覆盖 12.2%（真值口径 12.2%）
  - 作用域：段（effect 轨整段） / 2 段 / 12.2% 片长（不适用：口播正脸特写、抠像边缘）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `target_timerange.duration` | [0.833, 2.9]（中位 1.866） | effect 轨 segments[].target_timerange.duration | 氛围类：duration ≈ 段落长度；冲击类：duration ≤ 0.6 | 本套氛围类 1 个跨整个段落、冲击类 1 个只 0.17–0.6s——两种用法不能混：长盖的不能短、冲击的不能长 |
    | `起点对切点的偏移` | [0, 0]s | effect 轨 segments[].target_timerange.start − 最近主 | offset = |start − nearest_cut|，瞬时类要求 ≤0.25 | 冲击类必须贴在切点上（切点＝观众眨眼的那一帧）；氛围类可以落在段中 |
    | `adjust_params（强度）` | 不规则黑框: effects_adjust_background_animation=1.0; 聚光灯: effects | materials.video_effects[].adjust_params[].value | 强度 = value；与 default_value 的比值说明「用了几成」 | 不规则黑框.effects_adjust_background_animation=1.0（默认 1.0 → 1.00×）; 聚光灯.effects_adjust_speed=0. |
    | `覆盖占比` | 12.2% | Σ effect 时长 ÷ 片长 | coverage = Σdur / duration | 真值上限 20.3%；特效是段落语气，不是全片底色 |
  - 影响谁：调色←特效叠在 LUT 之后：暗角会让边缘掉 1–2 档，调色时要按「有暗角」的结果校 y_mean；字幕对比度←暗角托底时字幕可以只靠 shadow；无暗角必须描边；抠像边缘←冲击特效压在抠像边缘 → 白边抖成锯齿
  - 耦合：color_grade（ordering）；cutout（mutex_in_frame）；sticker（ordering）
  - 违反症状：把「暗角」当全片滤镜挂 → 覆盖 100%，画面像监控回放；瞬时特效离切点 1s 以上 → 渲染出来像坏帧，观众以为文件损坏；特效强度抄默认值（颤抖 range 0.15/speed 0.33）→ 晃到看不清字，真值压到 0.09/0.12
- **style_21**：画面特效只有两种合法用法：①氛围类长时间盖住一个段落（暗角/边框/噪点，≥1s）；②冲击类 ≤0.6s 贴在切点上（模糊/颤抖）。本套 1 个：氛围类 0 个、冲击类 1 个，累计覆盖 1.1% 片长。
  - 不该出现：整片挂滤镜（覆盖 >20% 片长）；盖住人讲话的正脸（观感像坏片）；瞬时特效离切点 >0.25s（渲染出来像闪帧/坏帧）
  - 证据：effect 轨 1 段：['模糊']；时长 [0.167, 0.167]s（中位 0.167）；起点距最近切点 [0, 0]s；覆盖 1.1%（真值口径 1.1%）
  - 作用域：段（effect 轨整段） / 1 段 / 1.1% 片长（不适用：口播正脸特写、抠像边缘）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `target_timerange.duration` | [0.167, 0.167]（中位 0.167） | effect 轨 segments[].target_timerange.duration | 氛围类：duration ≈ 段落长度；冲击类：duration ≤ 0.6 | 本套氛围类 0 个跨整个段落、冲击类 1 个只 0.17–0.6s——两种用法不能混：长盖的不能短、冲击的不能长 |
    | `起点对切点的偏移` | [0, 0]s | effect 轨 segments[].target_timerange.start − 最近主 | offset = |start − nearest_cut|，瞬时类要求 ≤0.25 | 冲击类必须贴在切点上（切点＝观众眨眼的那一帧）；氛围类可以落在段中 |
    | `adjust_params（强度）` | 模糊:  | materials.video_effects[].adjust_params[].value | 强度 = value；与 default_value 的比值说明「用了几成」 | 暗角这类只有 texture 一档且取满值 1.0（=默认满档），说明「要就开到满，不开就别开」 |
    | `覆盖占比` | 1.1% | Σ effect 时长 ÷ 片长 | coverage = Σdur / duration | 真值上限 6.4%；特效是段落语气，不是全片底色 |
  - 影响谁：调色←特效叠在 LUT 之后：暗角会让边缘掉 1–2 档，调色时要按「有暗角」的结果校 y_mean；字幕对比度←暗角托底时字幕可以只靠 shadow；无暗角必须描边；抠像边缘←冲击特效压在抠像边缘 → 白边抖成锯齿
  - 耦合：color_grade（ordering）；cutout（mutex_in_frame）；sticker（ordering）
  - 违反症状：把「暗角」当全片滤镜挂 → 覆盖 100%，画面像监控回放；瞬时特效离切点 1s 以上 → 渲染出来像坏帧，观众以为文件损坏；特效强度抄默认值（颤抖 range 0.15/speed 0.33）→ 晃到看不清字，真值压到 0.09/0.12
- **style_22**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 14 条轨 = {'video': 3, 'text': 7, 'audio': 4}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_23**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 18 条轨 = {'video': 5, 'text': 11, 'audio': 2}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」
- **style_24**：不出现——真值 0 个画面特效：effect 轨 0 段、materials.video_effects 未被任何段引用（本套 15 条轨 = {'video': 6, 'text': 6, 'audio': 3}）。画面处理全交给切点 + 花字/贴纸，没有滤镜式的一层。
  - 硬加会怎样：整片挂暗角 → 画面从「通透实拍」变成「回忆滤镜」


## 贴纸（`sticker`）

**这里为什么贴贴纸**

- **style_01**：不出现——真值 0 个贴纸段：本套 25 条轨里根本没有 sticker 轨（{'video': 10, 'text': 11, 'audio': 4}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_02**：贴纸是「指示器」不是装饰：真值里 指示箭头 1 段。它必须①贴在切点上跟着新镜进场（距切点 [0, 0]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 1 段（track [9]）：{'指示箭头': 1}；时长 [1.133, 1.133]s；起点距最近切点 [0, 0]s；与花字同框 100.0%
  - 作用域：段 / 1 段 / 片长 29.467s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.513, 0.513] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.513]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [-0.8, -0.8] / y [-0.262, -0.262] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏上（y 中位 -0.262<0，避开底部字幕带） |
    | `clip.rotation` | [90.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0, 0]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_03**：不出现——真值 0 个贴纸段：本套 16 条轨里根本没有 sticker 轨（{'video': 2, 'text': 10, 'audio': 4}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_04**：不出现——真值 0 个贴纸段：本套 9 条轨里根本没有 sticker 轨（{'video': 2, 'text': 3, 'effect': 1, 'audio': 3}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_05**：贴纸是「指示器」不是装饰：真值里 手势（点赞等反应） 1 段、指示箭头 1 段。它必须①贴在切点上跟着新镜进场（距切点 [0, 0]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 2 段（track [7]）：{'手势（点赞等反应）': 1, '指示箭头': 1}；时长 [1.317, 1.433]s；起点距最近切点 [0, 0]s；与花字同框 100.0%
  - 作用域：段 / 2 段 / 片长 22.467s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.633, 1.0] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.633, 1.0]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [0.507, 0.611] / y [-0.25, -0.09] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏上（y 中位 -0.170<0，避开底部字幕带） |
    | `clip.rotation` | [0.0, 44.993] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0, 0]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_06**：不出现——真值 0 个贴纸段：本套 8 条轨里根本没有 sticker 轨（{'video': 2, 'text': 4, 'audio': 2}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_07**：贴纸是「指示器」不是装饰：真值里 圈/框/划线（标注） 3 段。它必须①贴在切点上跟着新镜进场（距切点 [0, 0]s）②本套贴纸不与花字同框（同框率 0.0%）：它是纯标注，靠画面里的物体定位而不是靠文案命名；其中 1 组是「同时同长、成组标注」（3 张 10.717s 起 3.95s）。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 3 段（track [8, 9, 10]）：{'圈/框/划线（标注）': 3}；时长 [3.95, 3.95]s；起点距最近切点 [0, 0]s；与花字同框 0.0%
  - 作用域：段 / 3 段 / 片长 28.133s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.29, 0.29] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.29]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [-0.362, 0.499] / y [0.169, 0.475] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏下/居中（y 中位 0.239，这套的字幕不在底部） |
    | `clip.rotation` | [0.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0, 0]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（independent）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_08**：贴纸是「指示器」不是装饰：真值里 指示箭头 1 段。它必须①贴在切点上跟着新镜进场（距切点 [0.567, 0.567]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 1 段（track [10]）：{'指示箭头': 1}；时长 [0.567, 0.567]s；起点距最近切点 [0.567, 0.567]s；与花字同框 100.0%
  - 作用域：段 / 1 段 / 片长 58.467s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.512, 0.512] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.512]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [-0.801, -0.801] / y [-0.214, -0.214] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏上（y 中位 -0.214<0，避开底部字幕带） |
    | `clip.rotation` | [90.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0.567, 0.567]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_09**：不出现——真值 0 个贴纸段：本套 14 条轨里根本没有 sticker 轨（{'video': 4, 'text': 7, 'audio': 3}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_10**：不出现——真值 0 个贴纸段：本套 12 条轨里根本没有 sticker 轨（{'video': 3, 'text': 5, 'audio': 4}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_11**：不出现——真值 0 个贴纸段：本套 13 条轨里根本没有 sticker 轨（{'video': 4, 'text': 6, 'audio': 3}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_12**：不出现——真值 0 个贴纸段：本套 9 条轨里根本没有 sticker 轨（{'video': 2, 'text': 4, 'audio': 3}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_13**：贴纸是「指示器」不是装饰：真值里 图标/其他 1 段。它必须①贴在切点上跟着新镜进场（距切点 [1.283, 1.283]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 1 段（track [6]）：{'图标/其他': 1}；时长 [1.283, 1.283]s；起点距最近切点 [1.283, 1.283]s；与花字同框 100.0%
  - 作用域：段 / 1 段 / 片长 30.217s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [1.0, 1.0] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [1.0]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [0.199, 0.199] / y [0.658, 0.658] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏下/居中（y 中位 0.658，这套的字幕不在底部） |
    | `clip.rotation` | [0.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [1.283, 1.283]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_14**：贴纸是「指示器」不是装饰：真值里 色块（底衬） 1 段。它必须①贴在切点上跟着新镜进场（距切点 [0, 0]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 1 段（track [7]）：{'色块（底衬）': 1}；时长 [1.5, 1.5]s；起点距最近切点 [0, 0]s；与花字同框 100.0%
  - 作用域：段 / 1 段 / 片长 20.9s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.01, 0.01] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.01]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [0.0, 0.0] / y [-0.462, -0.462] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏上（y 中位 -0.462<0，避开底部字幕带） |
    | `clip.rotation` | [0.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0, 0]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_15**：贴纸是「指示器」不是装饰：真值里 图标/其他 3 段。它必须①贴在切点上跟着新镜进场（距切点 [0.167, 1.534]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 3 段（track [10]）：{'图标/其他': 3}；时长 [0.967, 1.7]s；起点距最近切点 [0.167, 1.534]s；与花字同框 100.0%
  - 作用域：段 / 3 段 / 片长 30.567s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.91, 1.19] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.91, 1.0, 1.19]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [-0.075, 0.0] / y [-0.582, -0.488] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏上（y 中位 -0.488<0，避开底部字幕带） |
    | `clip.rotation` | [0.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0.167, 1.534]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_16**：不出现——真值 0 个贴纸段：本套 17 条轨里根本没有 sticker 轨（{'video': 4, 'text': 10, 'audio': 3}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_17**：贴纸是「指示器」不是装饰：真值里 色块（底衬） 1 段、指示箭头 1 段。它必须①贴在切点上跟着新镜进场（距切点 [0.633, 1.533]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 2 段（track [9]）：{'色块（底衬）': 1, '指示箭头': 1}；时长 [0.633, 1.533]s；起点距最近切点 [0.633, 1.533]s；与花字同框 100.0%
  - 作用域：段 / 2 段 / 片长 16.0s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.01, 0.668] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.01, 0.668]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [-0.784, 0.0] / y [-0.567, -0.273] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏上（y 中位 -0.420<0，避开底部字幕带） |
    | `clip.rotation` | [0.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0.633, 1.533]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_18**：贴纸是「指示器」不是装饰：真值里 圈/框/划线（标注） 1 段。它必须①贴在切点上跟着新镜进场（距切点 [1.367, 1.367]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 1 段（track [6]）：{'圈/框/划线（标注）': 1}；时长 [1.483, 1.483]s；起点距最近切点 [1.367, 1.367]s；与花字同框 100.0%
  - 作用域：段 / 1 段 / 片长 38.2s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.23, 0.23] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.23]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [0.331, 0.331] / y [0.492, 0.492] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏下/居中（y 中位 0.492，这套的字幕不在底部） |
    | `clip.rotation` | [0.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [1.367, 1.367]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_19**：贴纸是「指示器」不是装饰：真值里 圈/框/划线（标注） 1 段。它必须①贴在切点上跟着新镜进场（距切点 [0, 0]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 1 段（track [7]）：{'圈/框/划线（标注）': 1}；时长 [13.183, 13.183]s；起点距最近切点 [0, 0]s；与花字同框 100.0%
  - 作用域：段 / 1 段 / 片长 38.2s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [0.53, 0.53] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [0.53]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [-0.545, -0.545] / y [-0.377, -0.377] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏上（y 中位 -0.377<0，避开底部字幕带） |
    | `clip.rotation` | [0.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0, 0]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_20**：不出现——真值 0 个贴纸段：本套 18 条轨里根本没有 sticker 轨（{'video': 3, 'text': 10, 'effect': 1, 'audio': 4}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_21**：贴纸是「指示器」不是装饰：真值里 手势（点赞等反应） 1 段、圈/框/划线（标注） 1 段。它必须①贴在切点上跟着新镜进场（距切点 [0.416, 0.966]s）②与当刻花字同框（真值同框率 100.0%）——同框是这套的硬条件，孤贴不存在。
  - 不该出现：空镜；已经有一层花字压在同一个位置；抠像小窗上（会压住脸）
  - 证据：sticker 轨段 2 段（track [11]）：{'手势（点赞等反应）': 1, '圈/框/划线（标注）': 1}；时长 [1.05, 2.467]s；起点距最近切点 [0.416, 0.966]s；与花字同框 100.0%
  - 作用域：段 / 2 段 / 片长 15.617s（不适用：口播主轨自身、字幕带）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `clip.scale` | [1.0, 2.188] | sticker 段 segments[].clip.scale.x | 显示尺寸 = 素材原生宽 × W/素材宽 × scale（贴纸按素材像素比铺到画布） | 本套 scale 集合 [1.0, 2.188]：指示类（箭头/圈）缩到画布的 1/4–1/2 当标注，边框类贴到 1.0–2.2 当「屏幕/内容框」 |
    | `clip.transform.x / y` | x [-0.611, 0.572] / y [-0.15, 0.092] | sticker 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − dh/H | 贴纸落点跟着被指的物体走：落点偏上（y 中位 -0.029<0，避开底部字幕带） |
    | `clip.rotation` | [0.0] | sticker 段 segments[].clip.rotation | — | 箭头按「指向」旋转：默认朝右，向下指就 90°；别的角度（≈45°）是真值里手调的斜指 |
    | `起点对齐切点` | [0.416, 0.966]s | sticker 起点 − 最近主轨切点 | — | 贴纸跟着切点一起进场；落在镜中间会像「凭空冒出来」 |
  - 影响谁：花字←同框：贴纸给花字腾位置或做底衬，二者不能压在同一块像素上；字幕安全区←贴纸 y 偏上/偏侧，底部字幕带留出来；人脸构图←贴纸不许压在人物脸上（真值贴纸落在画面边缘/物体附近）
  - 耦合：flower（co_occur）；cutout（mutex_in_frame）；effect（ordering）
  - 违反症状：贴纸落在镜中间（不在切点）→ 凭空冒出来，像后期漏剪；孤立贴纸不同框花字 → 观众不知道它在指什么，只觉得画面脏；箭头旋转角乱写 → 指错方向，比不贴更糟
- **style_22**：不出现——真值 0 个贴纸段：本套 14 条轨里根本没有 sticker 轨（{'video': 3, 'text': 7, 'audio': 4}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_23**：不出现——真值 0 个贴纸段：本套 18 条轨里根本没有 sticker 轨（{'video': 5, 'text': 11, 'audio': 2}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突
- **style_24**：不出现——真值 0 个贴纸段：本套 15 条轨里根本没有 sticker 轨（{'video': 6, 'text': 6, 'audio': 3}）。
  - 硬加会怎样：给这套贴装饰贴纸 → 画面立刻变「模板感」，与实拍调性冲突


## 画中画/缩窗（`pip`）

**这里为什么开小窗**

- **style_01**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 2 段、主轨缩窗/移位 0 段，窗口 scale 集合 [0.699]，四角落位 1 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 31 段（整屏 29 / 缩窗 2）；主轨 24 段里缩窗/移位 0 段；主轨 scale 众数 1.0；窗口 scale 集合 [0.699, 1.254]
  - 作用域：段 / 缩窗 2 段 / 画面层 55 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [0.699, 1.254] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.699（= 画布宽 69.9%），再小观众看不清窗里是什么；最大 1.254（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.133, 0.38] / y [-0.323, -0.301] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 1 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 2 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_02**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 1 段、主轨缩窗/移位 0 段，窗口 scale 集合 [1.0]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 4 段（整屏 3 / 缩窗 1）；主轨 25 段里缩窗/移位 0 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.0]
  - 作用域：段 / 缩窗 1 段 / 画面层 29 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.0] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 1.0（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.0] / y [-0.413, -0.413] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_03**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 2 段、主轨缩窗/移位 3 段，窗口 scale 集合 [1.057, 1.0, 1.0, 3.276, 1.0]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 6 段（整屏 4 / 缩窗 2）；主轨 49 段里缩窗/移位 3 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.0, 1.057, 3.276]
  - 作用域：段 / 缩窗 5 段 / 画面层 55 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.057, 1.0, 1.0, 3.276, 1.0] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 3.276（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.057] / y [-0.477, 0.466] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 1 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_04**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = full_frame_overlay；逐段实测：叠加缩窗 2 段、主轨缩窗/移位 0 段，窗口 scale 集合 [0.846]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 2 段（整屏 0 / 缩窗 2）；主轨 6 段里缩窗/移位 0 段；主轨 scale 众数 1.0；窗口 scale 集合 [0.846, 1.1]
  - 作用域：段 / 缩窗 2 段 / 画面层 8 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [0.846, 1.1] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.846（= 画布宽 84.6%），再小观众看不清窗里是什么；最大 1.1（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.1, 0.0] / y [0.0, 0.696] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 1 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_05**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 4 段、主轨缩窗/移位 2 段，窗口 scale 集合 [1.0, 1.0, 1.0, 1.241, 1.0, 1.0]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 6 段（整屏 2 / 缩窗 4）；主轨 16 段里缩窗/移位 2 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.0, 1.241]
  - 作用域：段 / 缩窗 6 段 / 画面层 22 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.0, 1.0, 1.0, 1.241, 1.0, 1.0] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 1.241（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.427, 0.517] / y [-0.303, 0.244] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 2 段 / x>0 2 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_06**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = full_frame_overlay；逐段实测：叠加缩窗 1 段、主轨缩窗/移位 1 段，窗口 scale 集合 [1.0, 1.0]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 1 段（整屏 0 / 缩窗 1）；主轨 4 段里缩窗/移位 1 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.0]
  - 作用域：段 / 缩窗 2 段 / 画面层 5 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.0, 1.0] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 1.0（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.0] / y [-0.211, 0.7] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_07**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 8 段、主轨缩窗/移位 0 段，窗口 scale 集合 [0.733, 0.793]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 13 段（整屏 5 / 缩窗 8）；主轨 17 段里缩窗/移位 0 段；主轨 scale 众数 1.0；窗口 scale 集合 [0.733, 0.793, 1.0, 1.2]
  - 作用域：段 / 缩窗 8 段 / 画面层 30 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [0.733, 0.733, 1.0, 0.733, 1.0, 0.793, 1.2, 1.0] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.733（= 画布宽 73.3%），再小观众看不清窗里是什么；最大 1.2（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.673, 0.678] / y [-0.065, 0.671] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 3 段 / x>0 3 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_08**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = main_shrink_only；逐段实测：叠加缩窗 0 段、主轨缩窗/移位 1 段，窗口 scale 集合 [0.9]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 0 段（整屏 0 / 缩窗 0）；主轨 2 段里缩窗/移位 1 段；主轨 scale 众数 0.9；窗口 scale 集合 [0.9]
  - 作用域：段 / 缩窗 1 段 / 画面层 2 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 0.9 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 0.9：视野被裁到 111%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [0.9] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.9（= 画布宽 90.0%），再小观众看不清窗里是什么；最大 0.9（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.0] / y [0.0, 0.0] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_09**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = main_shrunk_person_cutout；逐段实测：叠加缩窗 10 段、主轨缩窗/移位 13 段，窗口 scale 集合 [0.349, 0.6, 0.601, 0.747]，四角落位 16 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 10 段（整屏 0 / 缩窗 10）；主轨 27 段里缩窗/移位 13 段；主轨 scale 众数 0.6；窗口 scale 集合 [0.349, 0.6, 0.601, 0.747, 0.751, 0.919]
  - 作用域：段 / 缩窗 23 段 / 画面层 37 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 0.6 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 0.6：视野被裁到 167%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [0.919, 0.919, 0.919, 1.0, 0.751, 0.747, 1.0, 0.349, 0.349,  | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.349（= 画布宽 34.9%），再小观众看不清窗里是什么；最大 1.0（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.585, 0.426] / y [-0.495, 0.474] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 16 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 15 段 / x>0 1 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_10**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 4 段、主轨缩窗/移位 1 段，窗口 scale 集合 [1.0, 1.0, 1.1, 1.1, 1.0]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 10 段（整屏 6 / 缩窗 4）；主轨 15 段里缩窗/移位 1 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.0, 1.1]
  - 作用域：段 / 缩窗 5 段 / 画面层 25 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.0, 1.0, 1.1, 1.1, 1.0] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 1.1（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.1, 0.0] / y [-0.206, 0.684] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 2 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_11**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 5 段、主轨缩窗/移位 2 段，窗口 scale 集合 [1.0, 1.0, 1.0, 1.0, 1.2, 1.0, 1.0]，四角落位 3 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 8 段（整屏 3 / 缩窗 5）；主轨 19 段里缩窗/移位 2 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.0, 1.2]
  - 作用域：段 / 缩窗 7 段 / 画面层 27 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.0, 1.0, 1.0, 1.0, 1.2, 1.0, 1.0] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 1.2（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.289, 0.364] / y [-0.624, 0.086] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 3 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 3 段 / x>0 4 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_12**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 1 段、主轨缩窗/移位 4 段，窗口 scale 集合 [1.3, 1.251, 1.2, 1.2, 1.3]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 1 段（整屏 0 / 缩窗 1）；主轨 13 段里缩窗/移位 4 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.2, 1.251, 1.3]
  - 作用域：段 / 缩窗 5 段 / 画面层 14 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.3, 1.251, 1.2, 1.2, 1.3] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.2（= 画布宽 120.0%），再小观众看不清窗里是什么；最大 1.3（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.0] / y [-0.251, -0.157] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_13**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 3 段、主轨缩窗/移位 1 段，窗口 scale 集合 [0.346, 0.773]，四角落位 1 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 5 段（整屏 2 / 缩窗 3）；主轨 18 段里缩窗/移位 1 段；主轨 scale 众数 1.0；窗口 scale 集合 [0.346, 0.773, 1.2]
  - 作用域：段 / 缩窗 4 段 / 画面层 23 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.2, 0.773, 0.346, 1.2] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.346（= 画布宽 34.6%），再小观众看不清窗里是什么；最大 1.2（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.49, 0.227] / y [-0.357, 0.445] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 1 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 1 段 / x>0 1 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_14**：不出现——真值 0 个缩窗：2 段叠加视频全部 scale=1.000、x=y=0（整屏覆盖主轨），缩窗/分屏在这套里不存在。
  - 硬加会怎样：给这套开小窗 → 满屏实拍的「现场感」立刻掉成「解说台」
- **style_15**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = overlay_small_window_edge；逐段实测：叠加缩窗 9 段、主轨缩窗/移位 0 段，窗口 scale 集合 [0.64, 0.904]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 13 段（整屏 4 / 缩窗 9）；主轨 13 段里缩窗/移位 0 段；主轨 scale 众数 1.0；窗口 scale 集合 [0.64, 0.904, 1.01]
  - 作用域：段 / 缩窗 9 段 / 画面层 26 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.01, 0.904, 1.01, 0.64, 0.64, 0.64, 0.904, 1.01, 1.01] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.64（= 画布宽 64.0%），再小观众看不清窗里是什么；最大 1.01（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.749, 1.251] / y [-0.232, 0.428] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 4 段 / x>0 3 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_16**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 1 段、主轨缩窗/移位 0 段，窗口 scale 集合 [1.741]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 9 段（整屏 8 / 缩窗 1）；主轨 15 段里缩窗/移位 0 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.741]
  - 作用域：段 / 缩窗 1 段 / 画面层 24 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.741] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.741（= 画布宽 174.1%），再小观众看不清窗里是什么；最大 1.741（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.0] / y [0.127, 0.127] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_17**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = full_frame_overlay；逐段实测：叠加缩窗 5 段、主轨缩窗/移位 0 段，窗口 scale 集合 [0.85]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 5 段（整屏 0 / 缩窗 5）；主轨 11 段里缩窗/移位 0 段；主轨 scale 众数 1.0；窗口 scale 集合 [0.85]
  - 作用域：段 / 缩窗 5 段 / 画面层 16 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [0.85, 0.85, 0.85, 0.85, 0.85] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.85（= 画布宽 85.0%），再小观众看不清窗里是什么；最大 0.85（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.0] / y [-0.572, 0.572] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_18**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 4 段、主轨缩窗/移位 4 段，窗口 scale 集合 [1.5, 1.5, 1.3, 1.5, 1.2, 1.5, 1.0, 1.5]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 7 段（整屏 3 / 缩窗 4）；主轨 25 段里缩窗/移位 4 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.0, 1.2, 1.3, 1.5]
  - 作用域：段 / 缩窗 8 段 / 画面层 32 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.5, 1.5, 1.3, 1.5, 1.2, 1.5, 1.0, 1.5] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 1.5（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.179, 0.201] / y [-0.143, 0.058] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 2 段 / x>0 5 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_19**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 1 段、主轨缩窗/移位 6 段，窗口 scale 集合 [0.543, 0.642]，四角落位 6 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 7 段（整屏 6 / 缩窗 1）；主轨 22 段里缩窗/移位 6 段；主轨 scale 众数 1.3；窗口 scale 集合 [0.543, 0.642]
  - 作用域：段 / 缩窗 7 段 / 画面层 29 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.3 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.3：视野被裁到 77%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [0.642, 0.543, 0.543, 0.543, 0.543, 0.543, 0.543] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.543（= 画布宽 54.3%），再小观众看不清窗里是什么；最大 0.642（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.575, 0.0] / y [-0.529, 0.099] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 6 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 6 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_20**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = full_frame_overlay；逐段实测：叠加缩窗 4 段、主轨缩窗/移位 0 段，窗口 scale 集合 [1.0, 1.0, 1.0, 1.0]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 4 段（整屏 0 / 缩窗 4）；主轨 16 段里缩窗/移位 0 段；主轨 scale 众数 1.0；窗口 scale 集合 [1.0]
  - 作用域：段 / 缩窗 4 段 / 画面层 20 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.0, 1.0, 1.0, 1.0] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 1.0（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.0] / y [-0.705, 0.525] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_21**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 2 段、主轨缩窗/移位 5 段，窗口 scale 集合 [0.628, 0.63]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 12 段（整屏 10 / 缩窗 2）；主轨 13 段里缩窗/移位 5 段；主轨 scale 众数 1.2；窗口 scale 集合 [0.628, 0.63, 0.998, 1.5, 1.6]
  - 作用域：段 / 缩窗 7 段 / 画面层 25 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.2 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.2：视野被裁到 83%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [0.998, 1.6, 1.6, 1.5, 0.628, 0.63, 0.63] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.628（= 画布宽 62.8%），再小观众看不清窗里是什么；最大 1.6（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.026] / y [-0.199, 0.107] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 3 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_22**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 5 段、主轨缩窗/移位 1 段，窗口 scale 集合 [0.24, 0.752, 0.82, 0.822]，四角落位 1 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 8 段（整屏 3 / 缩窗 5）；主轨 18 段里缩窗/移位 1 段；主轨 scale 众数 1.0；窗口 scale 集合 [0.24, 0.752, 0.82, 0.822, 1.431]
  - 作用域：段 / 缩窗 6 段 / 画面层 26 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.431, 0.822, 0.24, 0.82, 0.24, 0.752] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.24（= 画布宽 24.0%），再小观众看不清窗里是什么；最大 1.431（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.814, 0.378] / y [-0.626, 0.15] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 1 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 5 段 / x>0 1 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_23**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 3 段、主轨缩窗/移位 0 段，窗口 scale 集合 [1.0, 1.129, 1.222]，四角落位 0 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 7 段（整屏 4 / 缩窗 3）；主轨 8 段里缩窗/移位 0 段；主轨 scale 众数 1.1；窗口 scale 集合 [1.0, 1.129, 1.222]
  - 作用域：段 / 缩窗 3 段 / 画面层 15 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.1 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.1：视野被裁到 91%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.0, 1.129, 1.222] | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 1.0（= 画布宽 100.0%），再小观众看不清窗里是什么；最大 1.222（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [0.0, 0.184] / y [-0.64, 0.503] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 0 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 0 段 / x>0 1 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了
- **style_24**：画中画的因果是「这一刻要同时看两个画面」：主画面占满或放大，副画面缩成窗挪到边角。本套真值 pip 标签 = person_cutout_overlay；逐段实测：叠加缩窗 10 段、主轨缩窗/移位 2 段，窗口 scale 集合 [0.5, 0.8]，四角落位 2 段。
  - 不该出现：副画面本身就是信息主体（该整屏就整屏）；主画面里的人脸会被窗口压到；窗口进入字幕带（y > 0.6H）
  - 证据：叠加 15 段（整屏 5 / 缩窗 10）；主轨 30 段里缩窗/移位 2 段；主轨 scale 众数 1.0；窗口 scale 集合 [0.5, 0.8, 1.0, 1.127]
  - 作用域：段 / 缩窗 12 段 / 画面层 45 段（不适用：字幕带、人脸矩形）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `主轨 scale（常驻放大裁切）` | 1.0 | 主轨段 segments[].clip.scale.x 众数 | 主画面显示宽 = W·scale；scale>1 = 裁切放大（等效推近机位，不是关键帧） | 这套主轨常用 1.0：视野被裁到 100%，比加关键帧推拉更稳（真值关键帧因此很少） |
    | `窗口 scale` | [1.0, 1.0, 1.127, 1.0, 1.0, 0.8, 0.8, 0.5, 0.5, 0.5, 0.8, 0. | 叠加/主轨段 segments[].clip.scale.x | win_w = W·scale；win_h = (nh/nw)·W·scale | 最小 0.5（= 画布宽 50.0%），再小观众看不清窗里是什么；最大 1.127（>1 是放大窗，用来裁掉窗内杂物） |
    | `窗口 x / y` | x [-0.5, 0.0] / y [-0.688, 0.473] | 段 segments[].clip.transform | left = (W/2)(x + 1 − scale)；|x| ≤ 1−scale 是「拖到不出画」的边 | 四角落位 2 段（|x|>0.3 且 |y|>0.3）是这套唯一的窗位语法；居中开窗不存在 |
    | `窗口朝向（左右）` | x<0 3 段 / x>0 0 段 | 同 x 的符号 | — | 窗口放在与主体视线相反的一侧：主体在左就开右窗，观众眼睛不用来回跳 |
  - 影响谁：人脸构图←窗口不能压主画面人脸；副窗里的人脸要完整落在窗内（边框裁到下巴就废）；字幕←窗口矩形与字幕带互斥：窗在下方就必须把字幕挪到上方或缩短；贴纸←贴纸不许贴在窗口边上，会像窗口的角标
  - 耦合：cutout（subset）；subtitle（mutex_in_frame）；face_framing（constraint）
  - 违反症状：窗口开在 y>0.6H → 压住字幕，质检判字幕越界；窗口盖在人脸上 → 观众以为切错镜；窗里人脸被裁一半 → 副画面成了「半张脸」，信息反而没了


## 叠加几何（`overlay_geometry`）

**这一层为什么摆在这里**

- **style_01**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 55 段视频里 29 段叠加层是整屏覆盖、2 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：55 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 2 段（3.6%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 55 段 / 10 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-65, 368]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 29 段 / 缩窗 2 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 55 段，全部缺省 = 1.0 | segments[].clip.alpha（缺省 1.0） | — | 全部段 alpha=1.0：淡入淡出交给动画与切点，不靠 alpha |
    | `蒙版（可选）` | 1 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"width": 0.28, "centerY": 0.057676, "rotation": -10.0, "feather": 0.14} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_02**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 29 段视频里 3 段叠加层是整屏覆盖、1 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：29 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 0 段（0.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 29 段 / 2 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [0, 0]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 3 段 / 缩窗 1 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 29 段，其中 1 段非 1.0：0.6×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 1 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 1 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"height": 0.87109, "width": 0.5} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_03**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 55 段视频里 4 段叠加层是整屏覆盖、2 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：55 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 4 段（7.3%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 55 段 / 2 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-1229, 0]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 4 段 / 缩窗 2 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 55 段，其中 2 段非 1.0：0.7×1、0.46×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 2 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 5 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerX": 0.062361, "centerY": -1.0, "feather": 1.0, "width": 0.28} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_04**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 8 段视频里 0 段叠加层是整屏覆盖、2 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：8 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 0 段（0.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 8 段 / 2 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-108, 83]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 0 段 / 缩窗 2 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 8 段，其中 2 段非 1.0：0.5×1、0.58×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 2 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 2 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"height": 1.001357, "width": 1.0} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_05**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 22 段视频里 2 段叠加层是整屏覆盖、4 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：22 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 5 段（22.7%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 22 段 / 3 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-231, 279]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 2 段 / 缩窗 4 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 22 段，其中 2 段非 1.0：0.0×2） | segments[].clip.alpha（缺省 1.0） | — | 仅 2 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 4 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"width": 0.443338, "height": 0.311873, "centerX": -0.037775, "centerY": |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_06**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 5 段视频里 0 段叠加层是整屏覆盖、1 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：5 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 2 段（40.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 5 段 / 2 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [0, 0]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 0 段 / 缩窗 1 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 5 段，其中 1 段非 1.0：0.3×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 1 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 2 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerY": 0.160999, "height": 0.336264, "width": 0.81068} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_07**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 30 段视频里 5 段叠加层是整屏覆盖、8 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：30 段视频：都在 1080×1920 画布上；本套 3 段命中贴边（|x| = 1−scale）、越界 4 段（13.3%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 30 段 / 5 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-364, 366]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 5 段 / 缩窗 8 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 30 段，其中 11 段非 1.0：0.0×6、0.6×3） | segments[].clip.alpha（缺省 1.0） | — | 仅 11 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 7 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"width": 0.5, "height": 0.28, "centerX": 0.148566, "centerY": 0.205534, |
    | `旋转` | [90.0] | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_08**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 2 段视频里 0 段叠加层是整屏覆盖、0 段是缩窗/缩放；主轨 scale 众数 0.9。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：2 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 0 段（0.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 2 段 / 1 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [0, 54]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 0 段 / 缩窗 0 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 2 段，全部缺省 = 1.0 | segments[].clip.alpha（缺省 1.0） | — | 全部段 alpha=1.0：淡入淡出交给动画与切点，不靠 alpha |
    | `蒙版（可选）` | 0 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 本套不用蒙版：换画面靠硬切，不靠擦除 |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_09**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 37 段视频里 0 段叠加层是整屏覆盖、10 段是缩窗/缩放；主轨 scale 众数 0.6。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：37 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 17 段（45.9%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 37 段 / 4 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-108, 581]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 0 段 / 缩窗 10 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 37 段，其中 3 段非 1.0：0.3×2、0.0×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 3 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 21 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"width": 0.604803, "height": 0.33869, "centerX": 0.082969, "centerY": 0 |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_10**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 25 段视频里 6 段叠加层是整屏覆盖、4 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：25 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 2 段（8.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 25 段 / 3 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-108, 0]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 6 段 / 缩窗 4 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 25 段，其中 1 段非 1.0：0.79×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 1 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 8 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"height": 1.001328, "width": 1.0} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_11**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 27 段视频里 3 段叠加层是整屏覆盖、5 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：27 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 6 段（22.2%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 27 段 / 4 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-156, 197]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 3 段 / 缩窗 5 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 27 段，其中 7 段非 1.0：0.0×4、0.65×2） | segments[].clip.alpha（缺省 1.0） | — | 仅 7 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 7 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerX": -0.030905, "centerY": 0.337888, "height": 0.349227, "width": |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_12**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 14 段视频里 0 段叠加层是整屏覆盖、1 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：14 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 1 段（7.1%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 14 段 / 2 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-162, 0]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 0 段 / 缩窗 1 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 14 段，其中 1 段非 1.0：0.54×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 1 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 1 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerY": 0.340373, "height": 0.349227, "width": 0.62362} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_13**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 23 段视频里 2 段叠加层是整屏覆盖、3 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：23 段视频：都在 1080×1920 画布上；本套 1 段命中贴边（|x| = 1−scale）、越界 2 段（8.7%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 23 段 / 2 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-770, 245]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 2 段 / 缩窗 3 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 23 段，其中 6 段非 1.0：0.35×3、0.0×2） | segments[].clip.alpha（缺省 1.0） | — | 仅 6 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 2 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerY": 0.203727, "height": 0.460486, "roundCorner": 0.1, "width": 0 |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_14**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 13 段视频里 2 段叠加层是整屏覆盖、0 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：13 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 0 段（0.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 13 段 / 2 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-108, 0]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 2 段 / 缩窗 0 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 13 段，其中 2 段非 1.0：0.0×2） | segments[].clip.alpha（缺省 1.0） | — | 仅 2 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 1 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerY": 0.294821, "height": 0.430566, "width": 0.768868} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_15**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 26 段视频里 4 段叠加层是整屏覆盖、9 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：26 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 5 段（19.2%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 26 段 / 6 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-459, 670]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 4 段 / 缩窗 9 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 26 段，其中 5 段非 1.0：0.0×2、0.5×2） | segments[].clip.alpha（缺省 1.0） | — | 仅 5 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 7 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerY": 0.234711, "height": 0.442708, "roundCorner": 1.0, "width": 0 |
    | `旋转` | [90.0] | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_16**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 24 段视频里 8 段叠加层是整屏覆盖、1 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：24 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 1 段（4.2%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 24 段 / 4 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-423, 0]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 8 段 / 缩窗 1 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 24 段，其中 8 段非 1.0：0.3×5、0.0×3） | segments[].clip.alpha（缺省 1.0） | — | 仅 8 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 2 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"width": 0.28, "centerX": -0.539511, "centerY": 0.169258, "rotation": 9 |
    | `旋转` | [90.0] | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_17**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 16 段视频里 0 段叠加层是整屏覆盖、5 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：16 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 0 段（0.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 16 段 / 4 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-54, 81]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 0 段 / 缩窗 5 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 16 段，其中 8 段非 1.0：0.5×5、0.0×3） | segments[].clip.alpha（缺省 1.0） | — | 仅 8 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 9 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerY": 0.249846, "feather": 0.1, "height": 0.578499, "roundCorner": |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_18**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 32 段视频里 3 段叠加层是整屏覆盖、4 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：32 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 1 段（3.1%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 32 段 / 2 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-367, 87]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 3 段 / 缩窗 4 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 32 段，其中 7 段非 1.0：0.6×5、0.0×2） | segments[].clip.alpha（缺省 1.0） | — | 仅 7 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 1 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"width": 0.57064, "height": 0.319558, "centerX": -0.136865, "centerY":  |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_19**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 29 段视频里 6 段叠加层是整屏覆盖、1 段是缩窗/缩放；主轨 scale 众数 1.3。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：29 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 6 段（20.7%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 29 段 / 4 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-162, 193]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 6 段 / 缩窗 1 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 29 段，其中 3 段非 1.0：0.45×2、0.08×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 3 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 8 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerX": 0.060325, "centerY": 0.279373, "height": 0.394339, "width":  |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_20**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 20 段视频里 0 段叠加层是整屏覆盖、4 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：20 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 1 段（5.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 20 段 / 3 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-108, 0]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 0 段 / 缩窗 4 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 20 段，其中 3 段非 1.0：0.0×2、0.6×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 3 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 5 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerY": 0.2321, "height": 0.405049, "width": 0.723301} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_21**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 25 段视频里 10 段叠加层是整屏覆盖、2 段是缩窗/缩放；主轨 scale 众数 1.2。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：25 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 1 段（4.0%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 25 段 / 6 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-324, 215]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 10 段 / 缩窗 2 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 25 段，其中 2 段非 1.0：0.65×2） | segments[].clip.alpha（缺省 1.0） | — | 仅 2 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 1 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerX": -0.235808, "centerY": -0.216216, "feather": 0.66022, "rotati |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_22**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 26 段视频里 3 段叠加层是整屏覆盖、5 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：26 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 3 段（11.5%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 26 段 / 3 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-344, 615]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 3 段 / 缩窗 5 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 26 段，其中 1 段非 1.0：0.0×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 1 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 1 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerX": 0.028235, "centerY": 0.333775, "height": 0.301082, "width":  |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_23**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 15 段视频里 4 段叠加层是整屏覆盖、3 段是缩窗/缩放；主轨 scale 众数 1.1。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：15 段视频：都在 1080×1920 画布上；本套 0 段命中贴边（|x| = 1−scale）、越界 2 段（13.3%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 15 段 / 5 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-120, 99]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 4 段 / 缩窗 3 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 15 段，其中 3 段非 1.0：0.4×2、0.3×1） | segments[].clip.alpha（缺省 1.0） | — | 仅 3 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 2 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"height": 0.906434, "width": 0.507603} |
    | `旋转` | 0 | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍
- **style_24**：画布几何是全部画面的底座：每条视频层都由 clip.scale + clip.transform 两个字段定死位置，公式 left=(W/2)(x+1−scale)、top=(H/2)(y+1−dh/H)（scale=1 即铺满画布宽度，竖屏素材正好整屏）。本套 45 段视频里 5 段叠加层是整屏覆盖、10 段是缩窗/缩放；主轨 scale 众数 1.0。
  - 不该出现：字幕/贴纸/花字层（它们走各自坐标系，但仍以画布为基准）
  - 证据：45 段视频：都在 1080×1920 画布上；本套 3 段命中贴边（|x| = 1−scale）、越界 1 段（2.2%，多为整屏段平移留白/带 crop）；全库对照：15 段贴边 / 603 段视频
  - 作用域：段（每条视频层） / 45 段 / 6 条视频轨（不适用：字幕/贴纸自有坐标系）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `显示尺寸` | W·scale，height = (nh/nw)·W·scale | segments[].clip.scale.x（缺省 1.0） | disp_w = W·scale；disp_h = (nh/nw)·W·scale；scale=1 时素 | 证据：16:9 素材(1920×1080)在真值里被放大到 scale≈1.774=1920/1080 才铺满竖屏——说明基准是「宽对齐」而不是「cover 对齐」，抄错会把上下裁 |
    | `画布坐标` | left ∈ [-418, 108]px | segments[].clip.transform.x / y | left = (W/2)(x + 1 − scale)；top = (H/2)(y + 1 − disp | x,y 是「窗口可动范围」的归一化：x=0 居中、x=±(1−scale) 顶到边。本机 15 段正好落在贴边值上，误差 <1px——这不是巧合而是控件边界 |
    | `铺满判定` | 整屏 5 段 / 缩窗 10 段 | scale=1 且 x=y=0 | — | 整屏覆盖是默认（观众看 100% 画面）；缩窗只在「同时看两个画面」时用 |
    | `alpha` | 45 段，其中 4 段非 1.0：0.0×4） | segments[].clip.alpha（缺省 1.0） | — | 仅 4 段 alpha≠1（垫底/过渡画面）；正常画面层一律 1.0，否则边缘与背景混色 |
    | `蒙版（可选）` | 5 个 | materials.common_mask + 段 extra_material_refs | 线性蒙版：width/rotation/feather 决定软边位置 | 真值只有一个线性蒙版：config {"centerY": 0.35591, "height": 0.430566, "width": 0.768868} |
    | `旋转` | [90.0, 270.0] | segments[].clip.rotation | 旋转角直接作用在画布坐标系上，先转后定位 | 视频层几乎不旋转（真值只在个别段有角度）；旋转是贴纸/素材的语法 |
  - 影响谁：抠像←抠像窗就是这套几何的一种（带 alpha 的窗口）；字幕/花字←画布坐标决定安全区：字幕带 y≈0.68H 以下不放任何画面元素；贴纸←贴纸落位用同一套 left/top 公式
  - 耦合：cutout（same_source_of_truth）；pip（same_source_of_truth）；subtitle（constraint）
  - 违反症状：把 16:9 素材按 scale=1 当全屏 → 上下各留 600+px 黑边，客户第一眼就说「怎么有黑边」；用 |x|>1−scale 摆窗 → 窗口出画一半，观众看不到窗里的东西；把 scale 当「百分比」直接乘素材宽 → 尺寸整体偏大/偏小 1.5 倍


## 入场动画（`in_anim`）

**元素为什么动进来**

- **style_01**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 6 个：宿主 {'text': 6}，时长中位 0.5s，名字 ['渐显', '生长 II', '向右滑动', '向左滑动']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：6 个；宿主 {'text': 6}；时长 [0.5, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 6 个 / 8 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.5, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐显': 3, '生长 II': 1, '向右滑动': 1, '向左滑动': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 6} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_02**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 18 个：宿主 {'text': 18}，时长中位 0.4s，名字 ['向右集合', '开幕', '激光雕刻', '随机上升']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：18 个；宿主 {'text': 18}；时长 [0.3, 0.4]s（中位 0.4）；字段 duration=400000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 18 个 / 21 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 0.4]（中位 0.4） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'向右集合': 2, '开幕': 2, '激光雕刻': 2, '随机上升': 2, '渐显': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 18} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_03**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 62 个：宿主 {'text': 62}，时长中位 0.5s，名字 ['冰雪飘动', '逐字显影', '向上滑动', '发光模糊']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：62 个；宿主 {'text': 62}；时长 [0.183, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 62 个 / 78 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.183, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'冰雪飘动': 9, '逐字显影': 7, '向上滑动': 7, '发光模糊': 5, '打字光标': 5} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 62} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_04**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 15 个：宿主 {'text': 14, 'video': 1}，时长中位 0.3s，名字 ['缩小', '开幕', '收拢', '打字光标']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：15 个；宿主 {'text': 14, 'video': 1}；时长 [0.2, 0.3]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 15 个 / 15 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.2, 0.3]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'缩小': 3, '开幕': 3, '收拢': 2, '打字光标': 2, '向下露出': 2} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 14, 'video': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_05**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 15 个：宿主 {'video': 2, 'text': 13}，时长中位 0.3s，名字 ['放大', '横向模糊', '向上滑动', '生长 II']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：15 个；宿主 {'video': 2, 'text': 13}；时长 [0.3, 0.5]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 15 个 / 15 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 0.5]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'放大': 5, '横向模糊': 1, '向上滑动': 1, '生长 II': 1, '折叠': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 2, 'text': 13} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_06**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 16 个：宿主 {'text': 16}，时长中位 0.5s，名字 ['逐字显影', '轻微放大', '随机上升', '冰雪飘动']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：16 个；宿主 {'text': 16}；时长 [0.5, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 16 个 / 17 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.5, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'逐字显影': 2, '轻微放大': 2, '随机上升': 2, '冰雪飘动': 2, '水墨晕开': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 16} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_07**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 23 个：宿主 {'video': 9, 'text': 11, 'sticker': 3}，时长中位 0.4s，名字 ['向左滑动', '流光扩散', '旋入', '轻微放大']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：23 个；宿主 {'video': 9, 'text': 11, 'sticker': 3}；时长 [0.3, 0.7]s（中位 0.4）；字段 duration=400000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 23 个 / 27 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 0.7]（中位 0.4） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'向左滑动': 6, '流光扩散': 3, '旋入': 3, '轻微放大': 2, '发光模糊': 2} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 9, 'text': 11, 'sticker': 3} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_08**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 28 个：宿主 {'text': 28}，时长中位 0.3s，名字 ['缩小', '跃进', '向右缓入', '随机上升']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：28 个；宿主 {'text': 28}；时长 [0.2, 0.333]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 28 个 / 28 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.2, 0.333]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'缩小': 4, '跃进': 3, '向右缓入': 3, '随机上升': 3, '晕开': 2} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 28} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_09**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 27 个：宿主 {'text': 21, 'video': 6}，时长中位 0.433s，名字 ['渐显', '镜像翻转', '向左模糊', '雪光模糊']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：27 个；宿主 {'text': 21, 'video': 6}；时长 [0.2, 1.5]s（中位 0.433）；字段 duration=433000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 27 个 / 28 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.2, 1.5]（中位 0.433） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套 2 个超过 1s 的是「打字/光标」类长驻动画（时长跟着文本走） |
    | `animations[].name` | {'渐显': 5, '镜像翻转': 5, '向左模糊': 2, '雪光模糊': 2, '打字光标': 2} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 21, 'video': 6} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_10**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 20 个：宿主 {'text': 20}，时长中位 0.5s，名字 ['放大', '滚入', '波浪弹入', '向右模糊 II']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：20 个；宿主 {'text': 20}；时长 [0.3, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 20 个 / 22 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'放大': 5, '滚入': 2, '波浪弹入': 2, '向右模糊 II': 2, '折叠': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 20} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_11**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 14 个：宿主 {'text': 12, 'video': 2}，时长中位 0.3s，名字 ['向下露出', '渐显', '向右露出', '发光模糊']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：14 个；宿主 {'text': 12, 'video': 2}；时长 [0.3, 1.033]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 14 个 / 14 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 1.033]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套 1 个超过 1s 的是「打字/光标」类长驻动画（时长跟着文本走） |
    | `animations[].name` | {'向下露出': 3, '渐显': 2, '向右露出': 1, '发光模糊': 1, '新年打字机': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 12, 'video': 2} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_12**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 11 个：宿主 {'video': 1, 'text': 10}，时长中位 0.3s，名字 ['轻微放大', '雪光模糊', '跃进', '折叠']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：11 个；宿主 {'video': 1, 'text': 10}；时长 [0.2, 0.3]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 11 个 / 11 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.2, 0.3]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'轻微放大': 2, '雪光模糊': 2, '跃进': 2, '折叠': 1, '模糊发光': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 1, 'text': 10} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_13**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 15 个：宿主 {'text': 14, 'video': 1}，时长中位 0.3s，名字 ['滚入', '向左模糊', '折叠', '镜像翻转']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：15 个；宿主 {'text': 14, 'video': 1}；时长 [0.3, 3.95]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 15 个 / 17 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 3.95]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套 1 个超过 1s 的是「打字/光标」类长驻动画（时长跟着文本走） |
    | `animations[].name` | {'滚入': 3, '向左模糊': 2, '折叠': 1, '镜像翻转': 1, '发光模糊': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 14, 'video': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_14**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 16 个：宿主 {'video': 1, 'text': 15}，时长中位 0.5s，名字 ['渐显', '向上滑动', '放大', '雪光模糊']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：16 个；宿主 {'video': 1, 'text': 15}；时长 [0.3, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 16 个 / 17 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐显': 4, '向上滑动': 3, '放大': 3, '雪光模糊': 1, '波浪弹入': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 1, 'text': 15} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_15**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 50 个：宿主 {'video': 8, 'text': 40, 'sticker': 2}，时长中位 0.3s，名字 ['渐显', '模糊发光', '向上滑动', '快速翻页']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：50 个；宿主 {'video': 8, 'text': 40, 'sticker': 2}；时长 [0.1, 0.533]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 50 个 / 54 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.1, 0.533]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐显': 11, '模糊发光': 6, '向上滑动': 4, '快速翻页': 3, '缩小': 3} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 8, 'text': 40, 'sticker': 2} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_16**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 21 个：宿主 {'video': 5, 'text': 16}，时长中位 0.5s，名字 ['渐显', '滚入', '横向模糊', '展开']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：21 个；宿主 {'video': 5, 'text': 16}；时长 [0.3, 1.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 21 个 / 24 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 1.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套 1 个超过 1s 的是「打字/光标」类长驻动画（时长跟着文本走） |
    | `animations[].name` | {'渐显': 6, '滚入': 2, '横向模糊': 1, '展开': 1, '缩小': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 5, 'text': 16} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_17**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 22 个：宿主 {'text': 17, 'video': 5}，时长中位 0.3s，名字 ['渐显', '随机上升', '收拢', '缩小']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：22 个；宿主 {'text': 17, 'video': 5}；时长 [0.3, 0.4]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 22 个 / 22 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 0.4]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐显': 6, '随机上升': 3, '收拢': 2, '缩小': 2, '抖动下降': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 17, 'video': 5} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_18**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 26 个：宿主 {'text': 25, 'video': 1}，时长中位 0.4s，名字 ['向上露出', '向右模糊 II', '打字光标', '圆柱体滚动']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：26 个；宿主 {'text': 25, 'video': 1}；时长 [0.3, 1.3]s（中位 0.4）；字段 duration=400000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 26 个 / 26 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 1.3]（中位 0.4） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套 1 个超过 1s 的是「打字/光标」类长驻动画（时长跟着文本走） |
    | `animations[].name` | {'向上露出': 4, '向右模糊 II': 2, '打字光标': 2, '圆柱体滚动': 2, '轻微放大': 2} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 25, 'video': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_19**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 13 个：宿主 {'video': 3, 'text': 10}，时长中位 0.5s，名字 ['发光模糊', '轻微放大', '展开', '向下滑动']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：13 个；宿主 {'video': 3, 'text': 10}；时长 [0.3, 11.383]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 13 个 / 14 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 11.383]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套 1 个超过 1s 的是「打字/光标」类长驻动画（时长跟着文本走） |
    | `animations[].name` | {'发光模糊': 3, '轻微放大': 2, '展开': 1, '向下滑动': 1, '圆柱体滚动': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 3, 'text': 10} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_20**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 31 个：宿主 {'text': 28, 'video': 3}，时长中位 0.3s，名字 ['水墨晕开', '冰雪飘动', '渐显', '向上滑动']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：31 个；宿主 {'text': 28, 'video': 3}；时长 [0.3, 1.0]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 31 个 / 32 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 1.0]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'水墨晕开': 5, '冰雪飘动': 3, '渐显': 2, '向上滑动': 2, '向右缓入': 2} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 28, 'video': 3} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_21**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 11 个：宿主 {'video': 1, 'text': 9, 'sticker': 1}，时长中位 0.4s，名字 ['缩小', '日出', '横向模糊', '向右露出']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：11 个；宿主 {'video': 1, 'text': 9, 'sticker': 1}；时长 [0.267, 0.533]s（中位 0.4）；字段 duration=400000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 11 个 / 15 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.267, 0.533]（中位 0.4） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'缩小': 4, '日出': 2, '横向模糊': 1, '向右露出': 1, '雪光模糊': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 1, 'text': 9, 'sticker': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_22**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 16 个：宿主 {'text': 14, 'video': 2}，时长中位 0.3s，名字 ['镜像翻转', '开幕', '缩小', '向右露出']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：16 个；宿主 {'text': 14, 'video': 2}；时长 [0.3, 1.5]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 16 个 / 18 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 1.5]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套 1 个超过 1s 的是「打字/光标」类长驻动画（时长跟着文本走） |
    | `animations[].name` | {'镜像翻转': 2, '开幕': 2, '缩小': 2, '向右露出': 1, '打字光标': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 14, 'video': 2} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_23**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 26 个：宿主 {'text': 22, 'video': 4}，时长中位 0.5s，名字 ['渐显', '随机上升', '开幕', '向右滑动']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：26 个；宿主 {'text': 22, 'video': 4}；时长 [0.2, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 26 个 / 27 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.2, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐显': 16, '随机上升': 3, '开幕': 3, '向右滑动': 2, '吸入': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 22, 'video': 4} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了
- **style_24**：入场动画的因果是「这个元素是新出现的」：跟着切点/句首一起进，时长 0.2–0.5s，位移/渐显类为主。本套 28 个：宿主 {'video': 11, 'text': 17}，时长中位 0.3s，名字 ['放大', '渐显', '缩小', '横向模糊']。
  - 不该出现：已存在元素的重复强调（用 loop 不用 in）
  - 证据：material_animations[].animations[] type=in：28 个；宿主 {'video': 11, 'text': 17}；时长 [0.3, 0.7]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 28 个 / 29 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 0.7]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'放大': 6, '渐显': 4, '缩小': 4, '横向模糊': 3, '展开': 3} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 11, 'text': 17} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点对齐←入场动画起点必须＝元素出现帧（切点/句首），偏一帧就看得出「慢半拍」；字幕时间戳←动画只改可见性/位移，不改 target_timerange——字幕时间戳不因动画延后；画面节奏←进场长度吃掉镜头的开头，短镜（<0.5s）配 0.5s 动画等于没有画面
  - 耦合：transition（mutex_in_frame）；flower（co_occur）
  - 违反症状：入场动画时长 >0.5s → 字还没完全出现就被切走；把入场动画挂在视频底片上 → 前 0.3s 出现黑帧；每个字都加动画 → 观众在等字，节奏散了


## 出场动画（`out_anim`）

**元素为什么动出去**

- **style_01**：不出现——真值 0 个出场动画：本套 8 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_02**：不出现——真值 0 个出场动画：本套 21 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_03**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 16 个，时长 [0.5, 0.5]s，宿主 {'text': 16}，名字 ['消散', '炸开', '渐隐', '炸开 II']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：16 个；宿主 {'text': 16}；时长 [0.5, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 16 个 / 78 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.5, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'消散': 7, '炸开': 3, '渐隐': 3, '炸开 II': 3} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 16} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_04**：不出现——真值 0 个出场动画：本套 15 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_05**：不出现——真值 0 个出场动画：本套 15 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_06**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 1 个，时长 [0.5, 0.5]s，宿主 {'text': 1}，名字 ['渐隐']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：1 个；宿主 {'text': 1}；时长 [0.5, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 1 个 / 17 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.5, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐隐': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_07**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 4 个，时长 [0.2, 0.5]s，宿主 {'video': 3, 'text': 1}，名字 ['向右滑动', '向左滑动', '滚出']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：4 个；宿主 {'video': 3, 'text': 1}；时长 [0.2, 0.5]s（中位 0.2）；字段 duration=200000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 4 个 / 27 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.2, 0.5]（中位 0.2） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'向右滑动': 2, '向左滑动': 1, '滚出': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 3, 'text': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_08**：不出现——真值 0 个出场动画：本套 28 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_09**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 1 个，时长 [0.467, 0.467]s，宿主 {'video': 1}，名字 ['镜像翻转']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：1 个；宿主 {'video': 1}；时长 [0.467, 0.467]s（中位 0.467）；字段 duration=467000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 1 个 / 28 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.467, 0.467]（中位 0.467） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'镜像翻转': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_10**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 2 个，时长 [0.283, 0.3]s，宿主 {'text': 2}，名字 ['炸开 II', '日落']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：2 个；宿主 {'text': 2}；时长 [0.283, 0.3]s（中位 0.291）；字段 duration=291000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 2 个 / 22 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.283, 0.3]（中位 0.291） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'炸开 II': 1, '日落': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 2} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_11**：不出现——真值 0 个出场动画：本套 14 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_12**：不出现——真值 0 个出场动画：本套 11 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_13**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 2 个，时长 [0.3, 0.5]s，宿主 {'text': 2}，名字 ['向下弹出', '滚出']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：2 个；宿主 {'text': 2}；时长 [0.3, 0.5]s（中位 0.4）；字段 duration=400000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 2 个 / 17 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.3, 0.5]（中位 0.4） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'向下弹出': 1, '滚出': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 2} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_14**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 1 个，时长 [0.4, 0.4]s，宿主 {'text': 1}，名字 ['滚出']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：1 个；宿主 {'text': 1}；时长 [0.4, 0.4]s（中位 0.4）；字段 duration=400000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 1 个 / 17 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.4, 0.4]（中位 0.4） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'滚出': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_15**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 4 个，时长 [0.267, 0.3]s，宿主 {'video': 2, 'text': 2}，名字 ['飘散', '晕开', '渐隐']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：4 个；宿主 {'video': 2, 'text': 2}；时长 [0.267, 0.3]s（中位 0.3）；字段 duration=300000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 4 个 / 54 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.267, 0.3]（中位 0.3） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'飘散': 2, '晕开': 1, '渐隐': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 2, 'text': 2} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_16**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 3 个，时长 [0.5, 0.5]s，宿主 {'video': 1, 'text': 2}，名字 ['渐隐', '发光模糊']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：3 个；宿主 {'video': 1, 'text': 2}；时长 [0.5, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 3 个 / 24 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.5, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐隐': 2, '发光模糊': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 1, 'text': 2} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_17**：不出现——真值 0 个出场动画：本套 22 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_18**：不出现——真值 0 个出场动画：本套 26 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_19**：不出现——真值 0 个出场动画：本套 14 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突
- **style_20**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 1 个，时长 [0.4, 0.4]s，宿主 {'video': 1}，名字 ['渐隐']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：1 个；宿主 {'video': 1}；时长 [0.4, 0.4]s（中位 0.4）；字段 duration=400000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 1 个 / 32 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.4, 0.4]（中位 0.4） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐隐': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_21**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 4 个，时长 [0.5, 0.5]s，宿主 {'video': 1, 'text': 2, 'sticker': 1}，名字 ['渐隐', '向下滑动']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：4 个；宿主 {'video': 1, 'text': 2, 'sticker': 1}；时长 [0.5, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 4 个 / 15 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.5, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐隐': 3, '向下滑动': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'video': 1, 'text': 2, 'sticker': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_22**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 1 个，时长 [0.2, 0.2]s，宿主 {'text': 1}，名字 ['向下弹出']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：1 个；宿主 {'text': 1}；时长 [0.2, 0.2]s（中位 0.2）；字段 duration=200000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 1 个 / 18 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.2, 0.2]（中位 0.2） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'向下弹出': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_23**：出场动画只在「元素要消失但镜头不切」时用：以消失类动词为主（渐隐/消散/炸开），不做位移。本套 1 个，时长 [0.5, 0.5]s，宿主 {'text': 1}，名字 ['渐隐']。
  - 不该出现：元素随切点一起消失的镜头（出场由硬切承担）
  - 证据：material_animations[].animations[] type=out：1 个；宿主 {'text': 1}；时长 [0.5, 0.5]s（中位 0.5）；字段 duration=500000µs 是 1/60s 网格上的整帧数
  - 作用域：个（每个元素一条） / 1 个 / 27 条动画引用（不适用：视频底片）
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `animations[].duration` | [0.5, 0.5]（中位 0.5） | material_animations[].animations[].duration（µs） | 秒 = duration / 1e6；帧 = 秒 × fps（60fps → 0.5s = 30 帧） | 标准档 0.3/0.4/0.5s：全库 24 套的中位就在 0.33–0.5s；本套没有超过 1s 的长驻动画，全部落在标准档 |
    | `animations[].name` | {'渐隐': 1} | material_animations[].animations[].name | — | 名字就是方向语义：向左/向右滑动＝位移；渐显/渐隐＝透明度；炸开/消散＝消失；生长/放大＝缩放。选名字前先定「从哪来/往哪去」 |
    | `宿主类型` | {'text': 1} | 段所在 track.type（text / sticker / video） | — | 动画只挂文字与贴纸（信息层），视频底片极少挂——挂上去就是黑帧/抖帧 |
  - 影响谁：切点←出场动画必须在切点之前收完；跨切点淡出＝两个画面混在一起；下一镜的入场←同一元素「出场紧接入场」会连成一条动画，观众看到的是它在飞；元素时长←出场动画占用元素尾部，元素本身时长不变（target_timerange 不改）
  - 耦合：cut（mutex）；in_anim（ordering）
  - 违反症状：出场动画 >0.5s → 镜头已经切了字还在淡出，穿帮；用位移类出场（滑出）→ 观众以为画面被拖走；给每个元素都配出场 → 与入场动画 1:1，整片变成动画秀（真值 in:out≈13:1）
- **style_24**：不出现——真值 0 个出场动画：本套 29 条动画引用里没有 type=out 的条目。元素的出场由硬切承担（切点即消失）。
  - 硬加会怎样：给这套加入场动画 → 每个字都「弹」一下，与这套的干脆调性冲突


## 调色（`color_grade`）

**这套为什么这样调色**

- **style_01**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_01.cube，强度 0.56），外加 19 个 HSL 素材挂在 19 段上但启用位为 0（取色残留，不许复刻）。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：25 条轨、55 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 19 个（样本色 ['#FFE64444', '#FFE64444', '#FFE64444']）挂 19 段但未启用；调色真值来自 246 帧实测
  - 作用域：全片（一套一个 LUT） / 55 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.56 | — | out = lerp(src, lut(src), strength) | 实测强度 0.56：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.023 / 1.025 | — | css_filter_approx = brightness(1.046) contrast(1.135 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [-0.0144, -0.0006, 0.0481]｜highlight [-0.0054, 0.0015 | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 色偏量级很小（±0.015≈±4/255）：真值是「微微暖影冷高光」，不是风格化重调色 |
    | `亮度/饱和基线` | y_mean 134.0 / p05 48.0 / p95 220.1；sat 23.5% | — | — | 这是「成片应该落在哪」的验收线：均衡自然高通透调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 19 个素材 / 19 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.56 压到 1/4（0.14）→ 成片 y_mean 离基线 134.0 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；把残留 HSL 素材当调色加上去 → 全片偏色（本库 style_01/03/05/09 都有这种残留）
- **style_02**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_02.cube，强度 0.63），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：12 条轨、29 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 72 帧实测
  - 作用域：全片（一套一个 LUT） / 29 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.63 | — | out = lerp(src, lut(src), strength) | 实测强度 0.63：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.038 / 1.037 | — | css_filter_approx = brightness(0.957) contrast(1.129 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 81.0 / p05 22.9 / p95 204.2；sat 26.2% | — | — | 这是「成片应该落在哪」的验收线：暗黑高对比科技调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.63 压到 1/4（0.158）→ 成片 y_mean 离基线 81.0 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_03**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_03.cube，强度 0.7），外加 31 个 HSL 素材挂在 31 段上但启用位为 0（取色残留，不许复刻）。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：16 条轨、55 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 31 个（样本色 ['#FFE64444', '#FFE64444', '#FFE64444']）挂 31 段但未启用；调色真值来自 198 帧实测
  - 作用域：全片（一套一个 LUT） / 55 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.7 | — | out = lerp(src, lut(src), strength) | 实测强度 0.7：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围 |
    | `contrast_gain / sat_scale` | 0.921 / 1.16 | — | css_filter_approx = brightness(0.921) contrast(1.04) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 39.5 / p05 7.8 / p95 114.3；sat 53.1% | — | — | 这是「成片应该落在哪」的验收线：深沉低饱和质感调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 31 个素材 / 31 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.7 压到 1/4（0.175）→ 成片 y_mean 离基线 39.5 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；把残留 HSL 素材当调色加上去 → 全片偏色（本库 style_01/03/05/09 都有这种残留）
- **style_04**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_04.cube，强度 0.58），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：9 条轨、8 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 21 帧实测
  - 作用域：全片（一套一个 LUT） / 8 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.58 | — | out = lerp(src, lut(src), strength) | 实测强度 0.58：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 0.959 / 0.979 | — | css_filter_approx = brightness(1.065) contrast(1.045 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 206.0 / p05 123.3 / p95 254.0；sat 13.3% | — | — | 这是「成片应该落在哪」的验收线：清透冷中性/极简白调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.58 压到 1/4（0.145）→ 成片 y_mean 离基线 206.0 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_05**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_05.cube，强度 0.57），外加 2 个 HSL 素材挂在 2 段上但启用位为 0（取色残留，不许复刻）。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：10 条轨、22 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 2 个（样本色 ['#FFE64444', '#FFE64444']）挂 2 段但未启用；调色真值来自 54 帧实测
  - 作用域：全片（一套一个 LUT） / 22 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.57 | — | out = lerp(src, lut(src), strength) | 实测强度 0.57：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 0.993 / 0.991 | — | css_filter_approx = brightness(1.066) contrast(1.24) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0294, -0.0055, -0.0318]｜highlight [0.0012, -0.0003 | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 色偏量级很小（±0.015≈±4/255）：真值是「微微暖影冷高光」，不是风格化重调色 |
    | `亮度/饱和基线` | y_mean 162.7 / p05 62.4 / p95 215.2；sat 16.0% | — | — | 这是「成片应该落在哪」的验收线：均衡自然高通透调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 2 个素材 / 2 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.57 压到 1/4（0.142）→ 成片 y_mean 离基线 162.7 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；把残留 HSL 素材当调色加上去 → 全片偏色（本库 style_01/03/05/09 都有这种残留）
- **style_06**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_06.cube，强度 0.57），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：8 条轨、5 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 50 帧实测
  - 作用域：全片（一套一个 LUT） / 5 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.57 | — | out = lerp(src, lut(src), strength) | 实测强度 0.57：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 0.918 / 0.979 | — | css_filter_approx = brightness(0.932) contrast(1.132 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 157.7 / p05 94.8 / p95 199.7；sat 13.3% | — | — | 这是「成片应该落在哪」的验收线：暖色高饱和门店/人物调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.57 压到 1/4（0.142）→ 成片 y_mean 离基线 157.7 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_07**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_07.cube，强度 0.6），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：16 条轨、30 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 64 帧实测
  - 作用域：全片（一套一个 LUT） / 30 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.6 | — | out = lerp(src, lut(src), strength) | 实测强度 0.6：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.005 / 1.008 | — | css_filter_approx = brightness(0.988) contrast(1.109 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 133.9 / p05 39.1 / p95 199.4；sat 19.7% | — | — | 这是「成片应该落在哪」的验收线：暖色高饱和门店/人物调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.6 压到 1/4（0.15）→ 成片 y_mean 离基线 133.9 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_08**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_08.cube，强度 0.64），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：11 条轨、2 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 183 帧实测
  - 作用域：全片（一套一个 LUT） / 2 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.64 | — | out = lerp(src, lut(src), strength) | 实测强度 0.64：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.024 / 1.044 | — | css_filter_approx = brightness(0.96) contrast(1.12)  | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [-0.02, -0.01, 0.02]｜highlight [0.03, 0.02, -0.01] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 色偏量级很小（±0.015≈±4/255）：真值是「微微暖影冷高光」，不是风格化重调色 |
    | `亮度/饱和基线` | y_mean 145.3 / p05 41.1 / p95 213.5；sat 27.6% | — | — | 这是「成片应该落在哪」的验收线：暖色高饱和门店/人物调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.64 压到 1/4（0.16）→ 成片 y_mean 离基线 145.3 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_09**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_09.cube，强度 0.55），外加 28 个 HSL 素材挂在 28 段上但启用位为 0（取色残留，不许复刻）。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：14 条轨、37 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 28 个（样本色 ['#FFE64444', '#FFE64444', '#FFE64444']）挂 28 段但未启用；调色真值来自 406 帧实测
  - 作用域：全片（一套一个 LUT） / 37 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.55 | — | out = lerp(src, lut(src), strength) | 实测强度 0.55：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.062 / 1.02 | — | css_filter_approx = brightness(0.902) contrast(1.04) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 115.5 / p05 17.1 / p95 214.0；sat 22.4% | — | — | 这是「成片应该落在哪」的验收线：暖色高饱和门店/人物调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 28 个素材 / 28 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.55 压到 1/4（0.138）→ 成片 y_mean 离基线 115.5 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；把残留 HSL 素材当调色加上去 → 全片偏色（本库 style_01/03/05/09 都有这种残留）
- **style_10**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_10.cube，强度 0.55），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：12 条轨、25 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 48 帧实测
  - 作用域：全片（一套一个 LUT） / 25 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.55 | — | out = lerp(src, lut(src), strength) | 实测强度 0.55：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.002 / 0.969 | — | css_filter_approx = brightness(1.029) contrast(1.091 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 163.4 / p05 64.4 / p95 222.9；sat 11.1% | — | — | 这是「成片应该落在哪」的验收线：均衡自然高通透调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.55 压到 1/4（0.138）→ 成片 y_mean 离基线 163.4 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_11**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_11.cube，强度 0.57），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：13 条轨、27 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 60 帧实测
  - 作用域：全片（一套一个 LUT） / 27 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.57 | — | out = lerp(src, lut(src), strength) | 实测强度 0.57：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 0.978 / 0.978 | — | css_filter_approx = brightness(1.045) contrast(1.058 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 181.7 / p05 80.6 / p95 223.9；sat 13.1% | — | — | 这是「成片应该落在哪」的验收线：均衡自然高通透调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.57 压到 1/4（0.142）→ 成片 y_mean 离基线 181.7 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_12**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_12.cube，强度 0.57），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：9 条轨、14 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 58 帧实测
  - 作用域：全片（一套一个 LUT） / 14 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.57 | — | out = lerp(src, lut(src), strength) | 实测强度 0.57：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 0.991 / 0.981 | — | css_filter_approx = brightness(1.066) contrast(1.24) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0034, -0.0001, -0.0088]｜highlight [-0.0025, 0.0007 | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 色偏量级很小（±0.015≈±4/255）：真值是「微微暖影冷高光」，不是风格化重调色 |
    | `亮度/饱和基线` | y_mean 170.5 / p05 88.9 / p95 240.6；sat 13.8% | — | — | 这是「成片应该落在哪」的验收线：暖色高饱和门店/人物调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.57 压到 1/4（0.142）→ 成片 y_mean 离基线 170.5 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_13**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_13.cube，强度 0.59），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：10 条轨、23 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 70 帧实测
  - 作用域：全片（一套一个 LUT） / 23 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.59 | — | out = lerp(src, lut(src), strength) | 实测强度 0.59：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.068 / 0.99 | — | css_filter_approx = brightness(0.972) contrast(1.194 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0197, -0.0033, -0.0257]｜highlight [-0.0006, 0.0015 | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 色偏量级很小（±0.015≈±4/255）：真值是「微微暖影冷高光」，不是风格化重调色 |
    | `亮度/饱和基线` | y_mean 171.5 / p05 31.0 / p95 231.6；sat 15.7% | — | — | 这是「成片应该落在哪」的验收线：暖色高饱和门店/人物调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.59 压到 1/4（0.147）→ 成片 y_mean 离基线 171.5 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_14**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_14.cube，强度 0.58），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：11 条轨、13 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 48 帧实测
  - 作用域：全片（一套一个 LUT） / 13 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.58 | — | out = lerp(src, lut(src), strength) | 实测强度 0.58：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 0.993 / 0.991 | — | css_filter_approx = brightness(0.934) contrast(1.24) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0051, -0.0013, -0.0021]｜highlight [0.0061, -0.0002 | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 色偏量级很小（±0.015≈±4/255）：真值是「微微暖影冷高光」，不是风格化重调色 |
    | `亮度/饱和基线` | y_mean 162.7 / p05 62.4 / p95 215.1；sat 16.0% | — | — | 这是「成片应该落在哪」的验收线：均衡自然高通透调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.58 压到 1/4（0.145）→ 成片 y_mean 离基线 162.7 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_15**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_15.cube，强度 0.64），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：18 条轨、26 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 38 帧实测
  - 作用域：全片（一套一个 LUT） / 26 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.64 | — | out = lerp(src, lut(src), strength) | 实测强度 0.64：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.061 / 1.05 | — | css_filter_approx = brightness(0.967) contrast(1.115 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 87.7 / p05 9.6 / p95 206.0；sat 28.9% | — | — | 这是「成片应该落在哪」的验收线：深沉低饱和质感调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.64 压到 1/4（0.16）→ 成片 y_mean 离基线 87.7 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_16**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_16.cube，强度 0.68），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：17 条轨、24 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 64 帧实测
  - 作用域：全片（一套一个 LUT） / 24 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.68 | — | out = lerp(src, lut(src), strength) | 实测强度 0.68：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.033 / 1.082 | — | css_filter_approx = brightness(0.901) contrast(1.04) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 68.9 / p05 3.8 / p95 182.4；sat 36.1% | — | — | 这是「成片应该落在哪」的验收线：暗黑高对比科技调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.68 压到 1/4（0.17）→ 成片 y_mean 离基线 68.9 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_17**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_17.cube，强度 0.73），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：13 条轨、16 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 36 帧实测
  - 作用域：全片（一套一个 LUT） / 16 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.73 | — | out = lerp(src, lut(src), strength) | 实测强度 0.73：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围 |
    | `contrast_gain / sat_scale` | 1.082 / 1.115 | — | css_filter_approx = brightness(0.978) contrast(1.146 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 96.2 / p05 11.0 / p95 220.7；sat 43.2% | — | — | 这是「成片应该落在哪」的验收线：深沉低饱和质感调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.73 压到 1/4（0.182）→ 成片 y_mean 离基线 96.2 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_18**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_18.cube，强度 0.57），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：10 条轨、32 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 135 帧实测
  - 作用域：全片（一套一个 LUT） / 32 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.57 | — | out = lerp(src, lut(src), strength) | 实测强度 0.57：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 0.99 / 0.99 | — | css_filter_approx = brightness(1.016) contrast(1.079 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 99.0 / p05 26.1 / p95 177.0；sat 15.8% | — | — | 这是「成片应该落在哪」的验收线：深沉低饱和质感调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.57 压到 1/4（0.142）→ 成片 y_mean 离基线 99.0 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_19**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_19.cube，强度 0.55），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：11 条轨、29 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 86 帧实测
  - 作用域：全片（一套一个 LUT） / 29 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.55 | — | out = lerp(src, lut(src), strength) | 实测强度 0.55：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.002 / 0.967 | — | css_filter_approx = brightness(1.007) contrast(1.14) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 164.8 / p05 65.7 / p95 223.9；sat 10.7% | — | — | 这是「成片应该落在哪」的验收线：均衡自然高通透调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.55 压到 1/4（0.138）→ 成片 y_mean 离基线 164.8 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_20**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_20.cube，强度 0.76），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：18 条轨、20 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 74 帧实测
  - 作用域：全片（一套一个 LUT） / 20 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.76 | — | out = lerp(src, lut(src), strength) | 实测强度 0.76：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围 |
    | `contrast_gain / sat_scale` | 0.942 / 1.2 | — | css_filter_approx = brightness(0.973) contrast(1.071 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 95.6 / p05 43.4 / p95 163.6；sat 65.4% | — | — | 这是「成片应该落在哪」的验收线：深沉低饱和质感调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.76 压到 1/4（0.19）→ 成片 y_mean 离基线 95.6 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_21**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_21.cube，强度 0.6），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：17 条轨、25 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 46 帧实测
  - 作用域：全片（一套一个 LUT） / 25 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.6 | — | out = lerp(src, lut(src), strength) | 实测强度 0.6：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.075 / 1.015 | — | css_filter_approx = brightness(1.015) contrast(1.11) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 140.1 / p05 32.6 / p95 237.9；sat 21.3% | — | — | 这是「成片应该落在哪」的验收线：均衡自然高通透调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.6 压到 1/4（0.15）→ 成片 y_mean 离基线 140.1 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_22**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_22.cube，强度 0.55），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：14 条轨、26 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 56 帧实测
  - 作用域：全片（一套一个 LUT） / 26 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.55 | — | out = lerp(src, lut(src), strength) | 实测强度 0.55：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.0 / 0.967 | — | css_filter_approx = brightness(1.037) contrast(1.09) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 163.9 / p05 65.0 / p95 222.3；sat 10.7% | — | — | 这是「成片应该落在哪」的验收线：均衡自然高通透调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.55 压到 1/4（0.138）→ 成片 y_mean 离基线 163.9 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_23**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_23.cube，强度 0.76），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：18 条轨、15 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 28 帧实测
  - 作用域：全片（一套一个 LUT） / 15 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.76 | — | out = lerp(src, lut(src), strength) | 实测强度 0.76：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围 |
    | `contrast_gain / sat_scale` | 0.948 / 1.2 | — | css_filter_approx = brightness(1.028) contrast(1.081 | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 77.9 / p05 36.8 / p95 160.9；sat 88.8% | — | — | 这是「成片应该落在哪」的验收线：深沉低饱和质感调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.76 压到 1/4（0.19）→ 成片 y_mean 离基线 77.9 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架
- **style_24**：调色不来自草稿节点：本套 enable_lut=0、enable_hsl=0、enable_adjust=0 段，草稿里没有任何滤镜/LUT 挂载。真值调色是「对源素材实测出来的 LUT」（style_24.cube，强度 0.65），也没有 HSL 取色残留。
  - 不该出现：逐镜调不同色（真值没有逐镜 LUT）；把草稿里残留的 HSL 素材当调色加上去（启用位 0，加了就偏色）
  - 证据：草稿扫描：15 条轨、45 段视频，enable_lut/hsl/adjust 命中 0/0/0；HSL 素材 0 个（样本色 []）挂 0 段但未启用；调色真值来自 101 帧实测
  - 作用域：全片（一套一个 LUT） / 45 段视频统一（不适用：字幕/花字（文字颜色走 subtitle_spec，不吃 LUT））
  - 参数推导：

    | 参数 | 真值 | 来源字段 | 公式 | 为什么是这个值 |
    |---|---|---|---|---|
    | `lut_strength` | 0.65 | — | out = lerp(src, lut(src), strength) | 实测强度 0.65：不是 1.0 全量套 LUT，而是「半量」——全量会把肤色推黄/推青，半量只改氛围（本套就是半量以下，留了很大余量给肤色） |
    | `contrast_gain / sat_scale` | 1.071 / 1.048 | — | css_filter_approx = brightness(0.970) contrast(1.13) | 对比/饱和是 LUT 之外的二次微调；本套 CSS 近似值可直接用于预览对齐 |
    | `分区色偏（shadow/midtone/highlight）` | shadow [0.0, 0.0, 0.0]｜highlight [0.0, 0.0, 0.0] | — | 每档加性色偏，量级 ±0.015（≈±4/255） | 本套分区色偏≈0：纯靠 LUT 出调子，没有额外分区染色 |
    | `亮度/饱和基线` | y_mean 103.6 / p05 18.1 / p95 220.9；sat 28.5% | — | — | 这是「成片应该落在哪」的验收线：深沉低饱和质感调；调完必须回到这条线上，否则 LUT 强度写错 |
    | `HSL 残留` | 0 个素材 / 0 段引用 / 启用 0 | materials.hsl[].custom_color + segments[].enable | — | 挂上但没启用＝剪映留下的取色残留；照抄会在成片上多一层偏色。真值口径：启用位为 0 一律不复刻 |
  - 影响谁：画面特效←LUT 在前、特效在后；先调色再加暗角才不脱色；抠像描边←纯白描边过 LUT 会变灰；描边必须在调色后再合成；肤色←skin guard：肤色越界就压 LUT 强度，而不是换 LUT
  - 耦合：effect（ordering）；cutout（ordering）；subtitle（mutex）
  - 违反症状：把 lut_strength 从 0.65 压到 1/4（0.163）→ 成片 y_mean 离基线 103.6 差 20–30，画面发灰；把 lut_strength 写成 1.0（全量）→ 肤色推黄，暗部发青；给这套额外挂 HSL 取色 → 多出一层偏色，与实测 LUT 打架


## 怎么用

1. Agent 选处理前先查这张表：`scripts/element_rules.py explain <style_id> <element>` → 拿到「为什么出现/参数推导/作用域」。
2. 出片后跑 `scripts/element_rules.py check --pipeline-dir <管道>`：逐元素比对真值，偏离项会指到具体参数与秒数。
3. 表里 `present:false` 的元素仍然留着「硬加会怎样」——那正是复用时要防的错。
