# 项目交付收尾报告 · 阶段 4 动态装载（2026-08-31）

> 交付范围：云枢（Yunshu）插件化阶段 4 全部任务 **T4.1–T4.2**
> 关联文档：[插件化方案总览](yunshu-pluginization/README.md) · [PLAN-4 动态装载](yunshu-pluginization/PLAN-4-dynamic-loading.md)
> 交付基准：T4.1 代码 `5e980b4f`；T4.2 代码与归档提交见提交记录（origin/GitHub + gitee 双远端同步）

## 1. 交付范围与目标

| 模块 | 目标 | 结果 |
|------|------|------|
| T4.1 后端扫描 + reload | `plugins/loader.py` 目录扫描自动加载 + `POST /api/plugins/reload`（原子重建注册表，失败保留旧清单） | ✅（`5e980b4f`） |
| T4.2 前端运行时发现 | `pluginDiscovery.ts`（fetchPlugins / reloadPlugins）+ PluginPanel「刷新」按钮（成功更新列表 + Toast，失败保留旧列表）+ 加载态 | ✅ |
| T4.2 进阶：动态装载 | manifest 声明 `clientSlot` 的插件 → 列表项「加载 UI」→ `import()` 客户端模块 → `register(slotRegistry)` / 默认导出组件挂入插槽 | ✅ |
| 演示全链路 | `plugins/demo_plugin.py`（schema + clientSlot）+ `public/plugins/demo-ui.js`（register）→ 新增插件 → 刷新可见 → Schema 面板可用 → 加载 UI 挂入 panels | ✅ |
| 零回归 | 前端 363 用例 + 后端插件相关 30 用例无回归 | ✅ |

## 2. 已完成工作与成果

| 交付物 | 内容 | 状态 |
|--------|------|------|
| `plugins/loader.py` | T4.1 目录扫描自动加载（`pkgutil` 扫描、单插件失败隔离、原子重建注册表）+ `register_blueprints` 增量挂载 | ✅（T4.1 提交） |
| `app_server.py` | `POST /api/plugins/reload`（`require_token`，失败 500 + 保留旧注册表）；启动装配改用 `loader.load_all()` | ✅（T4.1 提交） |
| `plugins/plugin_api.py` | T4.2 新增 `Plugin.client_slot` 可选字段（`{slotId, module}`），manifest 输出 `client_slot`（None 默认） | ✅ 本提交 |
| `plugins/demo_plugin.py` | 演示插件：schema（greeting/show_badge/poll_interval）+ `submit_url=/api/demo/config`（GET/POST 闭环）+ `client_slot={slotId:"panels", module:"/plugins/demo-ui.js"}` + `/api/demo/probe` 路由 | ✅ 本提交 |
| `yunshu-ui/src/plugins/pluginDiscovery.ts` | `PluginInfo` 契约（camelCase）+ `fetchPlugins`（GET + 归一化：submit_url/client_slot → submitUrl/clientSlot，空 schema → null）+ `reloadPlugins`（POST 后重新拉取）+ `loadClientUi`（动态 import → register(registry) 或默认导出组件 mountToSlot）+ `SlotRegistryFacade`（React/createElement/mountToSlot/extendProfile/openPanel）+ 生产 `/static` 前缀回退 | ✅ 本提交 |
| `yunshu-ui/src/plugins/slotRegistry.ts` | 新增 `extendProfile(slotId, item)`：运行时把动态条目追加进 profile 清单（PanelSwitcher 等清单型消费方可见），同 id 幂等 | ✅ 本提交 |
| `yunshu-ui/src/plugins/PluginPanel.tsx` | 改用 discovery 模块；顶部「刷新」按钮（刷新中禁用 + 「刷新中…」）；刷新成功 → 列表替换 + 成功 Toast + 选中项值重新预填；刷新失败 → 错误 Toast + **保留旧列表**；clientSlot 插件列表项「加载 UI」按钮（加载态 + 成功/失败 Toast，失败不影响其他功能） | ✅ 本提交 |
| `yunshu-ui/public/plugins/demo-ui.js` | 原生 ES 模块（无转译）：导出 `register(registry)`，用 `registry.createElement` 构建组件 → `mountToSlot('panels')` + `extendProfile` + `openPanel` | ✅ 本提交 |
| `yunshu-ui/package.json` | `build:flask` 追加复制 `dist/plugins` → Flask `static/plugins`（生产托管 `/static/plugins/demo-ui.js` 可用） | ✅ 本提交 |
| 单测 | `pluginDiscovery.test.ts`（11 项：fetchPlugins 归一化 / reloadPlugins 时序与失败 / applyClientModule 三种约定）；`PluginPanel.test.tsx` 扩展（刷新成功新插件可见 + Schema 面板可用、刷新失败保留旧列表、刷新中禁用、clientSlot 加载 UI 成功/失败 Toast） | ✅ 本提交 |
| 后端单测 | `test_plugin_schema.py` 扩展 3 项：client_slot 默认 None、manifest 输出、demo 插件声明 | ✅ 本提交 |

## 3. 验证结果

| 验证项 | 结果 |
|--------|------|
| `npx tsc -b --noEmit` | ✅ 通过（0 错误） |
| `npx vitest run` | ✅ **26 文件 / 363 用例全部通过**（T4.2 新增 30 条：pluginDiscovery 11 + PluginPanel 扩展） |
| `npm run build`（生产构建） | ✅ 通过（`dist/plugins/demo-ui.js` 随 public/ 复制，6.5s，仅 chunk 体积提示） |
| 后端 pytest（插件相关） | ✅ `test_plugin_schema.py` + `test_plugin_loader.py` + `test_plugin_submit_url.py` 30 项通过 |
| `loader.load_all()` 冒烟 | ✅ 发现 demo（loaded=1，内置插件幂等跳过） |
| `app_server` 装配冒烟 | ✅ `GET /api/plugins` → 9 插件（含 demo，client_slot/schema/submit_url 完整）；`GET /api/demo/probe`、`GET /api/demo/config` → 200 |
| `POST /api/plugins/reload` 冒烟 | ✅ 带 token 200，刷新后 demo 仍在；**无 token 401**（与既有写端点一致的鉴权约束，前端错误 Toast + 保留旧列表为预期降级行为） |

## 4. 遇到的问题与解决方案

| 问题 | 根因 | 解决方案 |
|------|------|----------|
| `POST /api/plugins/reload` 在启用 `FLASK_API_TOKEN` 的环境返回 401 | T4.1 契约要求 reload 需 `require_token`（与全部危险写端点一致），前端无令牌注入机制 | 与既有 schema 提交端点（如 `/api/status/config`）同一约束：未配置 token 的环境全链路可用；配置了 token 的环境刷新失败按「失败保留旧列表 + 错误 Toast」降级（即验收项「刷新失败不崩溃」）。前端单测以 mock fetch 覆盖成功路径 |
| 动态 `import()` 的路径在 dev（Vite public/）与生产（Flask `/static/`）不一致 | `public/` 资源 dev 在根路径、生产由 Flask 静态目录托管 | `importClientModule` 对 `/plugins/` 前缀回退一次 `/static/plugins/`；`build:flask` 追加复制 `dist/plugins` → `static/plugins`，dev 与生产托管均可用 |
| 动态挂入 panels 插槽的条目在面板切换器不可见 | `getManifestEntries` 按 profile 的 panels 白名单过滤，动态条目不在清单中 | `slotRegistry.extendProfile` 运行时追加 profile 条目（同 id 幂等）；`register` 约定内调用，配合 `openPanel` 触发 panelsStore 重渲染，装载结果立即可见 |
| 单测中动态模块无真实可 import 路径 | jsdom 下 `import('/plugins/demo-ui.js')` 不可解析 | 动态 import 薄封装（`importClientModule`）与模块应用逻辑（`applyClientModule`）分离：单测直接测 `applyClientModule` 的三种约定（register / 默认导出 / 抛错），PluginPanel 点击接线用 `vi.mock` 桩化 `loadClientUi` 验证 |

## 5. 阶段 4 完成标准对照（PLAN-4 §3）

- [x] `plugins/demo_plugin.py`（含 schema）放入目录 → 启动后自动出现在 `/api/plugins`
- [x] `POST /api/plugins/reload` 不重启进程刷新清单；单插件损坏不影响启动（T4.1 验证）
- [x] 前端插件面板有「刷新」按钮，能发现新插件
- [x] manifest 声明 `clientSlot` 的插件可被前端动态加载并挂入插槽（进阶）

## 6. 备注

- 四阶段改造路线（T1.x–T4.x）全部交付完毕，方案 README 进度表已标记阶段 4 完成。
- 生产托管动态 UI 的完整链路：`npm run build:flask` 后 `static/plugins/demo-ui.js` 由 Flask `/static/<path>` 提供，manifest module 路径 `/plugins/demo-ui.js` 经前端 `/static` 前缀回退命中。
