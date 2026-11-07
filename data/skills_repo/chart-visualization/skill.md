---
id: chart-visualization
name: chart-visualization
description: This skill should be used when the user wants to visualize data. It intelligently selects the most suitable chart type from 26 available options, extracts parameters based on detailed specifications, 
author: yunshu-ingestion
category: ingested
source: ingestion:trae
description_zh: 用户想把数据画成图时用这个技能：从 26 种图表里挑最合适的一种，按规格抽取参数，用 JavaScript 脚本生成图表图片。
tags: [ingested, trae]
content_type: markdown
enabled: true
version: 1.0.0
status: approved
---

# Chart Visualization Skill

把数据变成图表的**提示词资产**：选型（26 种）→ 参数规格（`args`）→ 用本机可执行的方式画出来。

## 何时使用

- 用户给出一组数据，要求「画个图 / 出个图表 / 可视化一下」；
- 需要按数据特征挑一种图表类型（时间序列、对比、占比、关系与流向、地图、层级、统计分布、流程）；
- 手上已有某张图的 `args` 规格，只想重新生成 / 复现这张图。

## 资产说明（仅提示词资产，2026-11-08 定稿）

本技能**只有** `SKILL.md` 与 `CARD.md`，**没有** `scripts/` 与 `references/` ——
上游 [antvis/chart-visualization-skills](https://github.com/antvis/chart-visualization-skills)
的那两个目录没有随源导入。因此：

- **不要**去找 `scripts/generate.js` 或 `references/*.md`：本仓库里不存在，去找只会白跑一次；
- 可用部分是下面这套「选型表 + `args` 规格」；真的要出图，用第 3 步里本机可执行的方式；
- 想补齐资产：把上游的 `scripts/` 与 `references/` 拷进 `data/skills_repo/chart-visualization/`，
  再走 `python scripts/intake_skill.py chart-visualization --execute --fix-baselines` 重新入轨。

## Workflow

### 1. Intelligent Chart Selection

按数据特征选类型（这张表是本技能的主体，不依赖任何外部文件）：

- **Time Series**: `generate_line_chart`（趋势）或 `generate_area_chart`（累积趋势）；
  两个量纲不同用 `generate_dual_axes_chart`。
- **Comparisons**: `generate_bar_chart`（分类）或 `generate_column_chart`；
  频数分布用 `generate_histogram_chart`。
- **Part-to-Whole**: `generate_pie_chart` 或 `generate_treemap_chart`（层级）。
- **Relationships & Flow**: `generate_scatter_chart`（相关性）、`generate_sankey_chart`（流向）、
  `generate_venn_chart`（重叠）。
- **Maps**: `generate_district_map`（区域）、`generate_pin_map`（点）、`generate_path_map`（路径）。
- **Hierarchies & Trees**: `generate_organization_chart` 或 `generate_mind_map`。
- **Specialized**:
    - `generate_radar_chart`：多维对比；
    - `generate_funnel_chart`：阶段转化；
    - `generate_liquid_chart`：百分比/进度；
    - `generate_word_cloud_chart`：词频；
    - `generate_boxplot_chart` / `generate_violin_chart`：统计分布；
    - `generate_network_graph`：节点-边关系；
    - `generate_fishbone_diagram`：因果分析；
    - `generate_flow_diagram`：流程；
    - `generate_spreadsheet`：表格/透视。

### 2. Parameter Extraction

不要依赖 `references/`（未导入）。按下面这套**通用约定**从用户数据里抽字段：

| 字段 | 说明 |
|---|---|
| `data` | 行数组；每行一条记录（对象键名即系列/维度名）|
| `title` | 图题（可选）|
| `theme` | 主题名（可选；不传就用默认）|
| `style` | 样式对象（可选：颜色、图例、坐标轴单位等）|
| 类型专属字段 | 如 sankey 的 `source`/`target`/`value`、boxplot 的 `group`/`values`、地图的行政区名 —— 缺字段就问用户要，**不要编数据** |

若日后补齐了上游 `references/`，可再按 `references/generate_<chart>.md` 逐字段核对。

### 3. Chart Generation

**本仓库没有 `scripts/generate.js`**，不要执行它。按环境选一种：

1. **Python（默认）**：用 matplotlib 生成 PNG（`figure.savefig`），把文件写给用户；
2. **前端/网页**：输出 ECharts 的 `option` 对象（可直接粘进图表页）；
3. **只给规格**：用户自己有渲染器时，把下面的 `args` JSON 交给他。

`args` 规格（复现用；与上游脚本的入参同形，但这里只是**规格**，不是脚本参数）：

```json
{
  "tool": "generate_chart_type_name",
  "args": {
    "data": [...],
    "title": "...",
    "theme": "...",
    "style": { ... }
  }
}
```

### 4. Result Return

回给用户：图（文件路径或图片）+ 本次用的完整 `args` 规格（方便他改一版重出）。

## 示例

用户：「把这 3 个月的日活画成折线图」

选型：时间序列 ⇒ `generate_line_chart`；抽参：

```json
{
  "tool": "generate_line_chart",
  "args": {
    "data": [
      { "month": "2026-08", "dau": 1200 },
      { "month": "2026-09", "dau": 1580 },
      { "month": "2026-10", "dau": 1910 }
    ],
    "title": "日活趋势"
  }
}
```

生成：用这份 `args` 写一段 matplotlib（或 ECharts `option`）代码并执行，把图片路径与 `args` 一并回给用户。
**不要**执行 `node ./scripts/generate.js`（本仓库没有该文件）。

## Reference Material

上游的字段规格在 [antvis/chart-visualization-skills](https://github.com/antvis/chart-visualization-skills)
的 `references/` 目录（26 种图表各一份），脚本在它的 `scripts/generate.js`。
**本仓库未导入这两处**（见上面「资产说明」），所以本文只保留自足的部分。

## License

This `SKILL.md` is provided by [antvis/chart-visualization-skills](https://github.com/antvis/chart-visualization-skills).
Licensed under the [MIT License](https://github.com/antvis/chart-visualization-skills/blob/master/LICENSE).
