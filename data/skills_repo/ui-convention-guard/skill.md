---
id: ui-convention-guard
name: UI 规范守卫
description: 改云枢前端之前先跑 ui_guard_check，按规则把原生控件、字号、标题层级与 Tab 顺序修到基线不降；只改 className 的两类修复可直接用，其余按建议人工判断。
category: custom
tags:
- ui
- frontend
- guard
- convention
version: 1.0.0
enabled: true
status: approved
author: yunshu
source: local
content_type: markdown
description_zh: 用于在修改云枢前端（yunshu-ui）前自检 UI 封装规范：原生按钮/输入/下拉、任意值字号、Button 满宽对齐、标题字号 token、标题是否在 Tab 上面。把违规变成 file:line 与修复建议，并按"先根因后表象"的顺序修，禁止用 --deselect 或改基线绕过。
---

# UI 规范守卫（ui-convention-guard）

## 何时使用

- 要改 `yunshu-ui/src` 下任何页面/组件（尤其是新增页面、页头、Tab、按钮、标题）；
- 评审一段 UI diff，想知道它有没有破坏封装规范；
- 规范守卫报红（基线升高 / 出现 error）时，按它给的 file:line 修；
- 想在对话里回答"现在前端规范有几条红"。

## 工作流

1. **先跑检查**：`ui_guard_check(mode="fast")`（或工作台「治理与运营 → 规范守卫」）。
   看 `counts / by_rule / baseline.regressions`；`regressions` 非空就是**这次新引入**的违规。
2. **先修根因**：`tabs.order` → 用 `PageTabsHeader`（标题恒在 Tab 上面）；
   `heading.scale` → 在标签上补 `text-title/text-ui/text-body`，删掉 CSS 里的字号；
   `button.alignment` → 满宽/ flex-1 的 Button 显式写 `justify-start`（行）或 `justify-center`（动作）。
3. **再修表象**：`audit.raw_control` 换 `ui/Button|Input|Select|Textarea|Slider`；
   `audit.arbitrary_text` 换 9 档 token；`audit.css_hardcoded_color` 换语义 token。
4. **复检**：再跑一次 `ui_guard_check`，要求 `baseline.regressions == []`；
   最后在 `yunshu-ui/` 跑 `npm run ui:check`（tsc + eslint + 守卫 + 量具）。

## 规则与修法

| 规则 | 含义 | 修法 |
|---|---|---|
| `button.alignment` | 满宽 Button 基类居中；`text-left` 管不到 flex 子项 | 显式 `justify-start` / `justify-center` |
| `heading.scale` | 标题字号由 CSS 类/父选择器决定 | 在标签上补 `text-*`（h1 title / h2 ui / h3 body） |
| `tabs.order` | Tabs 出现在标题之前 | 用 `PageTabsHeader` |
| `audit.raw_control` | 原生 button/select/input/textarea/range | 换 `components/ui` 原语 |
| `audit.arbitrary_text` | `text-[13px]` 之类 | 换 9 档 token |
| `audit.css_hardcoded_color` | CSS 里 `#hex` / `rgb()` | 换语义 token / 调色板类 |

## 约束 / 反例

- **不许**用 `--deselect` / 改 `data/ui_primitive_baseline.json` 让检查变绿；基线只降不升。
- **不许**把标题字号写回 CSS（`.pl-title { font-size }`），守卫 `heading.scale` 会红。
- **不许**在页面里再手拼一个页级标题；带页签的叶子页只用 `PageTabsHeader`。
- 自动修复只覆盖 `button.alignment` 与 `heading.scale` 的**字面量 className**；
  `className={cn(...)}` 不猜，按 fix_hint 人工补。

## 示例

一次典型修复：

1. `ui_guard_check()` 返回 `button.alignment @ pages/hub/x.tsx:42`；
2. 把该行 `<Button className="w-full text-left">` 改成 `<Button className="w-full text-left justify-start">`；
3. 复检 `baseline.regressions == []`，`error == 0`；
4. `npm run ui:check` 全绿后提交（按路径）。
