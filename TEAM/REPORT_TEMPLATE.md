# 任务交卷回执（<TASK_ID>）

- **负责人**：`<agent>`
- **分支**：`agent/<agent>/<TASK_ID>`
- **核心改动摘要（3–5 句大白话说明解决了什么问题、核心思路是什么）**：
  1. ...
  2. ...

## 改动文件清单（对照 allowed_files）
- `path/to/file1.py` — 改动说明
- `path/to/test_file.py` — 新增/修改测试用例

## 闸门与测试实测输出
- 执行命令：`"$HOME/.short-video-studio/venv/bin/python" dev_tools/team_ctl.py gate <TASK_ID>`
- 输出关键行（须包含 `=== GATE PASS: <TASK_ID> ===` 及 unittest 实测总数/耗时）：
  ```text
  （粘贴真实输出）
  ```

## 接口对齐与遗留风险说明
- 契约确认：...
- 若涉及需要总控协调的新接口或边界情况，请在此明确标注（未发现风险写“无”）。
