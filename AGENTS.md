# 仓库 Agent 约定（最短版）

## 前端（yunshu-ui/）

- **改任何 UI 前先读 `docs/ui/封装规范.md`**（≤80 行：选型表 + 四步 + 反例）。
- 提交前在 `yunshu-ui/` 跑 `npm run ui:check`：tsc + eslint + 3 个守卫 + 原语量具。
  红了先改代码；**不许** `--deselect`、**不许**改 `data/ui_primitive_baseline.json` 放水。
- 新原语准入：同一形态 ≥3 处重复 + 1 条契约测试 + 1 条守卫/量具 + `components/ui/index.ts` 导出；
  一个场景只留一个原语，换旧的先删旧再合新的。
- 标题字号必须写在标签的 `className`（`text-title/text-ui/text-body`），不许交给 CSS 类；
  带页签的叶子页用 `PageTabsHeader`（标题恒在 Tab 上面）。
- 对话里可用只读工具 `ui_guard_check`；界面在「治理与运营 → 规范守卫」（自动修复只碰字面量 className 的两类）。

## 提交

- 一律按路径：`git commit -F <msg> -- <paths>`。
- `data/` 下的运行期状态（feedback.db / handoff_state.json / system_prompt* / tools_config.json /
  llm_monitor_last.json）**不提交、不 checkout**。
- 交接口径：`docs/handoff/口径.md`；数字以 `python scripts/handoff_snapshot.py` 的交接卡为准。
