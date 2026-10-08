# 宿主功能页自动发现（feature registry）

这是「新增功能自动进 UI」的**宿主面**机制。新增一个功能页，只要：

1. 新建目录 `yunshu-ui/src/features/<key>/index.tsx`；
2. 导出默认组件与 `manifest`：

```tsx
import { Puzzle } from 'lucide-react'

export const manifest = { key: 'my-feature', label: '我的功能', icon: Puzzle, order: 900 }

export default function MyFeaturePage() {
  return <div>...</div>
}
```

构建时 `src/workbench/featureRegistry.ts` 用 `import.meta.glob(..., { eager: true })`
静态发现它，并自动挂进 `HUB_NAV`；**无需改 hubNav.tsx，也无需改任何导航清单**。

## 两条自动路径

| 场景 | 做法 | 是否改前端 |
|---|---|---|
| 宿主功能页（本仓自有页面） | 放一个 `src/features/<key>/index.tsx`（带 manifest） | 否（自动发现） |
| 插件自带 UI / 配置 | 后端 `Plugin.client_slot`（UI）+ `Plugin.schema`（配置） | 否（扩展中心自动呈现） |

> 边界：只发现 `features/<key>/index.tsx` 一层；必须同时导出 `default` 与 `manifest`。
> 需要导航参数 props 的页面，沿用 `derivePanelParams` 的「key 即参数」约定。
>
> 【并入宿主页的子视图**不要**带 manifest】2026-10-08 实例：「主线 × 四面」并入
> 「装配车间 → 分身创建与组装」成为第 3 个视图后，文件搬到 `pages/hub/workshop/line-assembly.tsx`
> 并**删掉 manifest** —— 留着它本机制仍会把它挂成一条顶层导航项，等于同一页面对外开两个入口
> （本仓明确禁止双入口，回归用例见 `pages/hub/workshop/line-assembly.test.tsx`
> 「不再导出 manifest」）。判据：这个页面**是否需要自己出现在左侧导航**；不需要就不要 manifest。
