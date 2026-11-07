# chart-visualization

This skill should be used when the user wants to visualize data. It intelligently selects the most suitable chart type from 26 available options, extracts parameters based on detailed specifications, and generates a chart image using a JavaScript script.

## 何时使用

- 用户给出一组数据，要求「画个图 / 出个图表 / 可视化一下」；
- 需要按数据特征挑一种图表类型（时间序列、对比、占比、关系与流向、地图、层级、统计分布、流程）；
- 手上已有某张图的 `args` 规格，只想重新生成 / 复现这张图。

## 步骤

（原文没有这一节）

## 参数

（原文没有这一节）

## 约束 / 反例

（原文没有这一节）

## 示例

用户：「把这 3 个月的日活画成折线图」→ 选 `generate_line_chart`，
按 `references/generate_line_chart.md` 抽取 `args`（`data` / `title`），
再 `node ./scripts/generate.js '<payload>'` 生成图片并回传 URL + 所用 `args`。

