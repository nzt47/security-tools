# src/components 组件索引

> 页面一律从 `@/components/ui` 统一出口导入基础组件（禁止直接引用单文件路径）。
> 新增基础组件需先在 `ui/index.ts` 登记再使用。

## 基础组件（src/components/ui/）

| 组件 | 说明 | Props 要点 |
|---|---|---|
| `Button` | 通用按钮（语义 Token） | `variant: primary/default/danger/ghost`、`size: sm/md`、`loading` |
| `Input` | 通用输入框 | `label`、`error`、`loading` |
| `Card` | 统一卡片容器 | `rounded-lg + border-border + shadow-card` |
| `ModalBase` | 弹窗基座（受控） | `open/onClose/title/footer/width/closeOnMask`；内置遮罩/Esc/滚动锁定/焦点还原 |
| `Empty` | 空态占位 | `description/icon/children` |
| `Loading` | 居中加载态 | `text/className` |
| `PageContainer` | 页面骨架（标题+说明+操作区+内容） | `title/description/actions/children` |
| `Table` | 通用表格壳（泛型 `Table<T>`） | `columns/dataSource/rowKey/loading/emptyText` |
| `Pagination` | 分页 | `page/pageSize/total/onChange`（共 N 条 + 上/下一页） |
| `Select` | 下拉选择（原生 select） | `options/value/onChange/label/placeholder/error` |
| `FormField` | 表单项容器（label+error） | `label/error/required/children`（Input/Select 已内置 label，组合场景使用） |
| `ThemeToggle` | 深浅模式切换 | 持久化键 `localStorage['yunshu-theme']` |

## 业务组件（src/components/）

| 组件 | 说明 |
|---|---|
| `ConfirmDialog` | 通用确认弹窗（基于 ModalBase，`danger` 红色确认按钮 + loading 防重复提交） |
| `Toaster` / `toast` | 全局提示（`toast.success/error/info`，axios 拦截器错误统一走此通道） |

## 页面子组件（src/pages/prompt-lab/，提示词实验室拆分）

> 深度合并：原工作台「身份提示词」「LLM 通信」已并入提示词实验室
> （hubNav 不再挂载独立页）。本地沙箱 7 段「系统提示词组件」随之移除，
> 系统提示词改由后端身份提示词配置（identityPrompt.ts）驱动。

| 组件 | 说明 |
|---|---|
| `RadarChart` | 五维效果评估雷达图（SVG 手绘，零图表库依赖） |
| `FactorControl` | 因素控件（slider/select/text/toggle 四态） |
| `FactorCard` | 因素卡片（名称 + 说明 + 控件 + 自定义删除） |
| `CustomFactorForm` | 添加自定义因素弹窗（按控件类型动态表单） |
| `IdentityPromptPanel` | 身份提示词编辑区（线上配置：启停/自定义内容/保存/重置/统计；并入原「身份提示词」页） |
| `LlmMonitorPanel` | LLM 通信监控区（统计 + 收发记录 + 10s 自动刷新/清空；并入原「LLM 通信」页） |
| `PreviewPanel` | 右侧实时预览面板（LLM 配置/提示词/模拟输出/雷达图/Token 估算/导出） |
| `index.tsx` | 主页面编排（状态与布局，样式见 `src/pages/PromptLab.css`） |

## 使用约定

- 颜色/间距/圆角/阴影一律语义 Token（`bg-card`、`text-foreground`、`border-border`、`rounded-md/lg`、`shadow-card`），禁止硬编码色值。
- 类名合并统一 `cn()`（`@/lib/cn`）。
- 同类结构重复 ≥ 2 次时抽象为组件（见 frontend-rules.md）；新基础组件先评审再进 `ui/`。
- 组件 API 变更须向后兼容；接口冻结后不轻易改签名。
