# UI 规范守卫

> 把 `docs/ui/封装规范.md` 变成可执行的检查与修复顺序。

## 何时使用

- 改 `yunshu-ui/src` 下任何页面/组件之前；
- 评审 UI diff；
- 规范守卫报红时。

## 步骤

1. 跑 `ui_guard_check`（fast），看 `counts / by_rule / baseline.regressions`；
2. 先修根因：Tabs 顺序 → `PageTabsHeader`；标题字号 → 标签上补 `text-*`；Button 对齐 → `justify-*`；
3. 再修表象：原生控件换 `components/ui` 原语；任意字号换 9 档 token；CSS 颜色换语义 token；
4. 复检 `baseline.regressions == []`，再跑 `npm run ui:check`。

## 参数

- `mode`：fast（默认，纯 Python）/ full（额外跑 npm/vitest 对拍）；
- `rule`：只看某条规则（逗号分隔）；
- `limit`：返回明细条数上限。

## 约束 / 反例

- 不改基线、不 `--deselect`；基线只降不升。
- 不把标题字号写回 CSS。
- 带页签的叶子页只用 `PageTabsHeader`，不手拼页级标题。
- 自动修复只覆盖字面量 className；`cn(...)` 按 fix_hint 人工补。

## 示例

```text
ui_guard_check(rule="button.alignment")
→ findings: [{file: pages/hub/x.tsx, line: 42, fix_hint: 加 justify-start}]
改 className → 复检 regressions=[] → npm run ui:check
```
