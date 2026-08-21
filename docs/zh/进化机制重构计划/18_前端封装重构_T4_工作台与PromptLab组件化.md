# 任务 T4：工作台与 PromptLab 组件化

> 所属计划：[14_前端封装理想设计_审计与重构总览.md](14_前端封装理想设计_审计与重构总览.md)
> 任务 ID：EVO-FE-T4 | 依赖：T1（弱） | 工作量：中

---

## 一、目标描述

拆分 `src/pages/PromptLab.tsx`（722 行、含 7 个内部组件）为独立模块；梳理工作台（Workbench）面板组件复用度，抽离可复用单元（DetachButton 通用化、MessageItem/Markdown 检查）。不改变任何用户可见行为与状态持久化契约。

**【不易】边界**：持久化键 `yunshu:prompt-lab:v1` 与 `yunshu:mosaic:layout:v1` 不可变；`useLayoutStore` / `usePromptLabStore` 的导出与行为不变；SSE 消费逻辑（`sendMessage`）不迁移、不重写；面板 ID（nav/chat/think/code）与 IPC 通道不可变。

## 二、前置依赖

T1（弱依赖：新模块内工具引用遵循 T1 约定；T1 未完成也可先用既有 `cn()`）。

## 三、执行步骤

1. **PromptLab.tsx 拆分**（目标文件数 ≤ 6）：
   - `src/pages/prompt-lab/RadarChart.tsx`（现 53-93 行）；
   - `src/pages/prompt-lab/FactorControl.tsx`（96-166，slider/select/text/toggle 四控件 + 类型 Props 导出）；
   - `src/pages/prompt-lab/FactorCard.tsx`（169-196）；
   - `src/pages/prompt-lab/SystemPartCard.tsx` + `AddSystemPartForm.tsx`（199-282）；
   - `src/pages/prompt-lab/CustomFactorForm.tsx`（285-399）；
   - `src/pages/prompt-lab/index.tsx`（主页面 401-722，只保留布局与编排，内联子组件全部 import 替代）。
   - 拆分原则：仅移动代码 + 补齐 import/Props 类型，不改逻辑；`PromptLab.css` 对应样式按需随组件就近迁移或保留单文件（不扩大样式拆分范围）。
2. **工作台复用检查**：
   - `src/components/workbench/DetachButton.tsx`：Props 泛化（`panelId` / `title` / `route` 参数化），确认 DetachedChatApp 与各 Panel 的调用点签名不变；
   - `src/components/workbench/chat/Markdown.tsx` / `MessageItem.tsx` / `MessageInput.tsx`：检查是否有可复用片段（如 markdown 渲染配置、输入框发送逻辑）被 CodeEditorPanel / ThinkingPanel 重复实现——有则抽共享子组件，无则不动（避免过度抽象）；
   - `ThinkingPanel` / `CodeEditorPanel` / `SidebarPanel`：仅做依赖梳理，记录各自职责到组件索引，不重构流式逻辑。
3. **组件索引补录**：把 PromptLab 子组件与工作台共享组件登记到 `src/components/README.md`（与 T2 同一文件，避免冲突时在任务报告注明）。
4. **单元测试**：
   - `FactorControl`：四种控件渲染与值回调（@testing-library）；
   - `RadarChart`：传入数据渲染（recharts 最小断言，不测图表内部）；
   - 主页面冒烟测试：渲染 + 默认值回显（保持既有 PromptLab 行为）；
   - DetachButton：参数化后渲染与 IPC 调用（mock electronAPI）。
5. **回归验证**：`npm run check` / `npm run lint` / `npm test` / `npm run build:flask`；手工验证 PromptLab 页（因素调节 / 自定义因素增删 / 系统组件开关 / LLM 配置弹层）与工作台（发送消息流式 / 面板 detach 独立窗口）行为一致。

## 四、预期成果（交付物）

- `src/pages/prompt-lab/` 目录（5 子组件 + index.tsx）；
- DetachButton 参数化（签名向后兼容）或维持原样（若检查后无需改动，任务报告说明理由）；
- 工作台复用检查报告（哪些片段重复 / 哪些已共享 / 哪些刻意不抽象）；
- 组件索引更新、新增子组件测试；
- 任务报告：PromptLab 拆分前后行数对比。

## 五、评估标准（验收条件）

1. `src/pages/PromptLab.tsx` 不再存在（主逻辑迁移至 `prompt-lab/index.tsx`），新目录单文件 ≤ 300 行；
2. 拆分不改变任何业务逻辑：Persist 键不变，反序列化 sanitize 行为不变；
3. 新增子组件均有测试，主页面冒烟测试通过，覆盖率 ≥ 80%（关键路径）；
4. 工作台面板 detach / 流式对话回归通过（含 Electron 模式或 mockElectron 模式）；
5. `npm run check` 无类型错误、`npm run lint` 无新增告警、`npm test` 全部通过；
6. 未修改 `useLayoutStore` 的 `sendMessage/stopStreaming` 与 IPC 契约；
7. 复用检查报告明确区分"已抽 / 不抽（理由）"，无过度抽象（对照总览 4.2 反模式清单）。

## 六、涉及模块

`yunshu-ui/src/pages/PromptLab.tsx`（拆分）、`yunshu-ui/src/pages/prompt-lab/`（新增）、`yunshu-ui/src/components/workbench/`（复用检查）、`yunshu-ui/src/components/README.md`、`yunshu-ui/src/pages/PromptLab.css`。
