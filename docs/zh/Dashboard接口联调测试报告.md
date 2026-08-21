# Dashboard 接口联调测试报告

> 项目：云枢 · AI 智能体桌面工作台（Electron + React + Flask）
> 模块：`src/pages/Dashboard`（前端） ↔ `GET /api/dashboard/summary`（后端）
> 日期：2026-08-21

---

## 1. 概述

Dashboard 仪表盘页面数据源由组件内 Mock 切换为真实后端接口 `GET /api/dashboard/summary`，本文档记录接口契约、联调验证过程、错误处理测试结果与日志排查机制。

涉及代码：

| 端 | 文件 | 说明 |
|---|---|---|
| 后端 | `agent/server_routes/routes_dashboard_summary.py` | 新增接口（Mock 数据，待接真实统计） |
| 后端 | `app_server.py` | 路由注册接线 |
| 前端 | `src/api/dashboard.ts` | 请求封装 + 数据完整性校验 + 日志 |
| 前端 | `src/pages/Dashboard/index.tsx` | 页面组件（Mock → API） |
| 前端 | `src/mocks/devMock.ts` | dev 错误场景模拟 |
| 前端 | `src/layouts/MainLayout.tsx` | Header「接口场景」下拉 |

## 2. 测试环境

| 项 | 值 |
|---|---|
| 前端 | Vite dev server `http://localhost:5173`（Web 模式，base=/static/） |
| 后端 | `python app_server.py`，监听 `127.0.0.1:5678` |
| 代理 | Vite `server.proxy`：`/api` → `http://127.0.0.1:5678`（`changeOrigin: true`） |
| 环境配置 | `.env.development`：`VITE_MOCK_API=false`、`VITE_API_PROXY_TARGET=http://127.0.0.1:5678` |
| 测试账号 | `admin / 123456`（取自项目根 `.env` 的 `YUNSHU_ADMIN_USERNAME/PASSWORD`） |
| 单测 | Vitest + Testing Library（jsdom） |

## 3. 接口契约

### 请求

```
GET /api/dashboard/summary
```

### 响应（统一 `{code, data, message}` 包装，符合前端 `request.ts` 解包约定）

```json
{
  "code": 200,
  "data": {
    "stats": {
      "totalUsers": 12480,
      "totalOrders": 3926,
      "conversionRate": 3.42,
      "activeUsers": 8153
    },
    "trend": [
      { "day": "08-15", "visits": 1860 }
    ],
    "roles": [
      { "name": "普通用户", "value": 10640 }
    ]
  },
  "message": "success"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `stats.totalUsers` | number | 总用户数 |
| `stats.totalOrders` | number | 总订单数 |
| `stats.conversionRate` | number | 转化率（百分比数值，前端加 `%` 展示） |
| `stats.activeUsers` | number | 活跃用户数 |
| `trend[].day` | string | 日期（MM-DD），后端以今天为基准动态回推 7 天 |
| `trend[].visits` | number | 访问量 |
| `roles[].name` | string | 角色名称 |
| `roles[].value` | number | 人数 |

## 4. 测试内容与方法

| 类型 | 方法 | 覆盖点 |
|---|---|---|
| 单元测试 | `npx vitest run src/pages/Dashboard/Dashboard.test.tsx` | 加载成功 / 请求失败 / 加载中三态 |
| 浏览器实测 | 真实登录 → 访问 `#/` | 真实接口渲染链路 |
| 错误场景 | Header「接口场景」下拉 + devMock 参数化拦截 | 业务错误 / 空数据 / 畸形数据 |
| 编译验证 | `npm run check`（tsc -b --noEmit） | 类型安全 |

## 5. 测试用例与结果

### 5.1 单元测试（3/3 通过）

| 用例 | 断言 | 结果 |
|---|---|---|
| 加载成功：渲染统计卡片与图表标题 | 4 卡片格式化数值（12,480 / 3,926 / 3.42% / 8,153）+「访问趋势」「用户角色分布」标题 | ✅ |
| 请求失败：显示错误空态 | 「数据加载失败，请稍后重试」；卡片与图表不渲染 | ✅ |
| 加载中：spinner → 数据视图 | 请求挂起时 `.animate-spin` 可见；resolve 后卡片出现、spinner 消失 | ✅ |

### 5.2 浏览器实测

| 场景 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 正常数据 | 登录后访问 Dashboard | 真实接口数据渲染（趋势 X 轴为后端动态日期 08-15~08-21） | ✅ |
| 业务错误(500) | Header 下拉选择「业务错误(500)」 | request.ts 拦截器 Toast + 组件显示「数据加载失败」空态 | ✅ |
| 恢复正常 | 下拉选择「正常数据」并刷新 | 真实数据重新渲染 | ✅ |
| 空数据 | devMock `?mock_error=empty` | 返回 `code:200, data:null` | ✅ |
| 畸形数据 | devMock `?mock_error=invalid` | 返回字段缺失/类型错误数据，触发数据校验 warn 日志 | ✅ |

### 5.3 数据来源真实性验证

前端旧 Mock 趋势日期为 `08-12~08-18`，接口返回为 `08-15~08-21`（后端动态生成）——页面渲染日期与接口一致，证明数据确为真实接口返回，而非前端 Mock 残留。

## 6. 错误处理与日志排查机制

### 6.1 链路

```
组件 useEffect → getDashboardSummary({mockError}) → request.ts（Token 注入/解包/拦截）
  → devMock（dev 拦截）| 真实后端
      ↓ 成功           ↓ 失败
validateDashboardSummary    request.ts：code!==200 → toast + reject
      ↓ 校验 warn/正常      ↓
  组件渲染                 组件 catch → 空态
```

### 6.2 日志约定（前缀 `[yunshu]`）

| 节点 | 级别 | 内容 |
|---|---|---|
| 请求发起 | info | `[dashboard] 请求运营统计总览 GET /dashboard/summary` |
| 返回成功 | info | stats 摘要 + trend/roles 数量 |
| 校验未通过 | warn | 问题明细（字段缺失/类型错误/数值越界） |
| 请求失败 | error | 错误对象 |
| 组件加载完成/失败 | info / warn | `[Dashboard] ...` |

排查入口：浏览器 Console 过滤 `[yunshu]` 或 `[dashboard]`。

### 6.3 生产环境性能

- `logger.ts` 已做条件编译：`import.meta.env.PROD` 为 true 时，`debug/info` 编译期为 `noop`（Vite 静态替换 + esbuild 消除不可达分支），**生产零开销**；`warn/error` 保留（数据异常/接口失败是生产排障必需）。
- 注意：`src/utils/request.ts` 的 `[perf]` 请求耗时日志为既有代码，无环境守卫，生产仍会打印 `console.info`——如需彻底裁剪可在该层补 `import.meta.env.PROD` 守卫（本次未改动，避免超范围变更）。
- 模拟场景相关代码（Header 下拉、Dashboard 读 localStorage 开关）均以 `import.meta.env.DEV` 守卫，**生产构建不含此逻辑**。

## 7. 已知事项与建议

1. **后端数据为 Mock**：`routes_dashboard_summary.py` 返回内置 Mock 数据，接入真实统计时仅需替换 `_get_dashboard_summary()` 数据来源，接口路径与响应结构不变（契约稳定）。
2. **趋势日期动态**：后端按当前日期回推 7 天，跨日展示自动顺延，无需前端改动。
3. **环比字段缺失**：真实接口未提供环比（delta/positive），卡片暂不展示"较上周期"；如需可在后端补充。
4. **jsdom 图表警告**：单测中 Recharts 输出 `width(0) and height(0)` 警告，属 jsdom 无法测量容器尺寸的正常现象，不影响断言。

## 8. 结论

- Dashboard 已从 Mock 数据完整切换为真实后端接口，端到端链路（代理 → 后端 → 前端渲染）验证通过。
- 错误处理逻辑（拦截器 toast / 组件空态 / 数据校验）经单测与浏览器实测双重验证生效。
- Header「接口场景」下拉提供可视化的错误模拟切换入口，仅开发环境可见，不影响生产。
- 日志机制覆盖请求全生命周期，生产环境已通过条件编译裁剪 info/debug 开销。
