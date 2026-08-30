# PLAN-3：Schema 驱动自解释 UI（阶段 3）

> 配套总览：`docs/yunshu-pluginization/README.md`
> 目标：让「插件的声明即文档、界面即渲染结果」——后端插件声明 JSON Schema，前端通用渲染器自动生成表单/面板。
> 前置：阶段 1（`/api/plugins`）、阶段 2（插槽体系）。

---

## 1. 自解释 UI 的本质

传统方式：每个功能手写一个 React 页面（如 PromptLab 的 21KB 表单）。
自解释方式：插件声明「我是什么、需要什么参数、能干什么」，前端用**通用渲染器**根据 Schema 自动生成 UI。

```
后端插件 (Plugin.schema)                   前端
┌─────────────────────────┐   /api/plugins  ┌──────────────────────────┐
│ { name: "personality",  │ ─────────────►  │ <PluginPanel/>           │
│   description: "性格微调", │                │   └─ <SchemaRenderer      │
│   params: { properties:  │                │        schema={p.schema}/>│
│     { mood: {type:"select", options:[...]} } }                        │
└─────────────────────────┘                └──────────────────────────┘
```

**收益**：新增功能 = 后端注册插件 + 声明 Schema，前端 **UI 零手写代码**。

---

## 2. Schema 协议（后端）

`Plugin.schema` 采用 **JSON Schema 子集**（阶段 1 的 `plugin_api.py` 已预留 `schema` 字段）：

```json
{
  "type": "object",
  "title": "性格微调",
  "description": "调整云枢的拟人化性格参数",
  "properties": {
    "mood": {
      "type": "string",
      "title": "性格基调",
      "enum": ["calm", "playful", "serious"],
      "default": "calm"
    },
    "verbosity": {
      "type": "integer",
      "title": "啰嗦程度",
      "minimum": 1,
      "maximum": 10,
      "default": 5
    },
    "topics": {
      "type": "array",
      "title": "感兴趣的话题",
      "items": { "type": "string" }
    }
  },
  "required": ["mood"]
}
```

**支持的字段子集（前端渲染器实现范围，超出则降级为 textarea JSON）：**

| JSON Schema 关键字 | 渲染控件 |
|---|---|
| `type: string` + `enum` | 下拉 select |
| `type: string` | 单行输入 |
| `type: string` + `format: textarea` | 多行输入 |
| `type: integer/number` | 数字输入（支持 min/max） |
| `type: boolean` | 开关 |
| `type: array` + `items` | 标签列表 / 多选 |
| `type: object` 嵌套 | 分组折叠 |
| `title` / `description` / `default` | 标签、说明、默认值 |

**T3.1 具体改动：**

1. `plugins/plugin_api.py`：`Plugin.schema` 字段启用（已存在，补校验：`schema` 必须为 dict 或 None）。
2. 给 **2–3 个真实插件**补 schema（建议 `status`、`safety`、`skills`），作为验证样例。
3. `/api/plugins` 响应已含 `schema` 字段（T1.1 的 manifest 已预留），核对输出。

---

## 3. 前端通用渲染器（`yunshu-ui/src/plugins/SchemaRenderer.tsx`）

```tsx
// 核心签名
export interface SchemaRendererProps {
  schema: Record<string, any>;        // JSON Schema 子集
  value: Record<string, any>;         // 当前值
  onChange: (next: Record<string, any>) => void;
}

export function SchemaRenderer({ schema, value, onChange }: SchemaRendererProps) {
  // 遍历 schema.properties：
  //   - 按类型分发到 <SelectField/> <InputField/> <NumberField/> <SwitchField/> <TagsField/> <TextareaField/>
  //   - 支持嵌套 object（折叠分组）
  //   - 未知类型 → <JsonFallbackField/>（textarea + JSON.parse 校验）
  // 底部可选「提交」按钮，onSubmit 收集 value
}
```

**配套子组件**（同目录 `fields/` 或单文件内实现）：`SelectField`、`InputField`、`NumberField`、`SwitchField`、`TagsField`、`TextareaField`、`JsonFallbackField`，全部受控组件。

**约束：**

- 纯展示/受控，不直接发请求；提交动作由调用方（PluginPanel）负责。
- 每个字段控件单独导出，方便未来第三方插件按需 import（`fields/` 目录）。
- 配 vitest 单测：每种类型字段、嵌套 object、未知类型降级、default 填充。

---

## 4. 插件面板（`yunshu-ui/src/plugins/PluginPanel.tsx`）

**位置**：挂到 `panels` 插槽（或新增 `plugin` 插槽），作为「插件中心」入口。

**行为：**

1. 挂载时 `fetch('/api/plugins')` 拉取 manifest。
2. 左侧列表：插件名 + description（自解释）；点击右侧渲染 `SchemaRenderer`。
3. 提交时按插件约定 POST 到对应端点（阶段 3 先支持插件自声明 `submitUrl` 字段，或由列表页映射表提供）。
4. 渲染 `schema` 为空的插件时显示「该插件暂无可配置界面」+ 原始 routes 列表（至少自解释它暴露了什么）。

**T3.3 验证目标（演示插件）：**

- 选一个真实插件（推荐 `personality` 相关或 `status`）写 schema；
- 在插件面板中**不写任何手写表单**，仅靠 SchemaRenderer 完成「查看 + 修改参数 + 提交」闭环；
- 对比改造前手写表单的页面（如 PromptLab 的性格区），确认体验等价。

---

## 5. 与阶段 2 的衔接

| 阶段 2 产物 | 阶段 3 用途 |
|---|---|
| `panels` 插槽 | PluginPanel 作为面板之一挂入 |
| `slotRegistry` | 后续第三方插件可向插槽注册自定义组件 |
| profile.json | 可配置插件面板显隐 |

---

## 6. 回归策略

- 后端：`python -m pytest tests/ -x -q`（新增 `/api/plugins` schema 字段不影响既有用例）。
- 前端：`npx tsc -b --noEmit && npx vitest run`（新增 SchemaRenderer 单测）。
- 冒烟：插件面板打开 → 渲染示例插件表单 → 修改 → 提交 → 后端生效。

---

## 7. 完成标准（阶段 3 结束）

- [ ] `Plugin.schema` 协议文档化并在 2–3 个真实插件上生效
- [ ] `SchemaRenderer` 支持上表全部类型 + 未知类型降级 + 单测通过
- [ ] 插件面板可浏览全部插件（名称/描述/routes）并 schema 驱动编辑至少一个插件
- [ ] 演示验证：某功能从「手写表单」变为「Schema 声明 + 自动渲染」
