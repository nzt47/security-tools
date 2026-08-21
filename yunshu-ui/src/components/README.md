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

## 使用约定

- 颜色/间距/圆角/阴影一律语义 Token（`bg-card`、`text-foreground`、`border-border`、`rounded-md/lg`、`shadow-card`），禁止硬编码色值。
- 类名合并统一 `cn()`（`@/lib/cn`）。
- 同类结构重复 ≥ 2 次时抽象为组件（见 frontend-rules.md）；新基础组件先评审再进 `ui/`。
- 组件 API 变更须向后兼容；接口冻结后不轻易改签名。
