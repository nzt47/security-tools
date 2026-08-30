# 进度日志

## 2026-06-19: 初始评估完成
- 完成了对云枢 40+ 个工具的全面评估
- 覆盖可用性、功能强度、稳定性三个维度
- 识别出 15+ 个具体问题
- 制定了包含 5 个阶段的修复计划
- 设计了 3 个独立会话的拆分方案

## 2026-06-19: 修复计划文档完成
- 创建了 `docs/tool-system-repair-plan.md` 完整修复计划
- 包含问题优先级矩阵（P0/P1/P2 分级）
- 5 个阶段详细任务分解（总工时 134h）
- 资源需求分析（技术/人力/时间）
- 3 个独立会话拆分方案
- 3 套可直接使用的独立会话提示词
- 风险分析与应对措施
- 验收标准和文件修改清单

## 2026-06-19: P0 紧急修复完成（Session A）
- 创建分支 `fix/tool-system-quickwins`
- 完成 7 个 P0 任务：
  1. **统一工具返回格式** — 所有工具统一返回 `{"ok": bool, "error": str, "data": ...}` 格式
  2. **放宽 web_search 结果限制** — 最多 8 条，snippet 300 字，token 动态控制
  3. **修复乱码注释** — 将损坏的中文注释修复为可读文本
  4. **search_files 路径安全校验** — 添加路径遍历防护
  5. **统一错误信息为中文** — 所有工具错误提示统一为中文
  6. **条件注册改为始终注册** — 4 个 v2 工具始终注册，运行时提示不可用
  7. **修复 ext_list 数据源同步** — 统一通过 ExtensionManager 为唯一数据源
- 测试：283/283 通过

## 2026-08-30: 插件化 T1.1 完成（插件注册表 + 装配器骨架）
- 新建 `plugins/plugin_api.py`：Plugin 协议层（name/version/description/schema/blueprint/routes + 幂等注册表 + manifest）
- 新建 `plugins/example.py` 临时示例插件（`/api/example/plugin-probe`，验证机制用，T1.10 删除）
- `app_server.py` 装配器改造：顶部导入 + blueprint 注册循环 + `/api/plugins` 端点（只加不改，+16 行）
- 验证：`import app_server` 无循环导入；启动服务冒烟 `/api/plugins`、`/api/example/plugin-probe`、`/api/health` 全部通过；路由集合 175→176 仅新增 `/api/plugins`；pytest app/API 子集 87 项全部通过
- 提交：`205a478d`（代码）+ `e5231633`（方案文档归档），已推送 origin/master

## 2026-08-30: 插件化 T1.2–T1.9 完成（8 个域插件拆分）
- 拆分 8 个域插件：chat（含 sessions/history/voice/news）、memory、status、skills、admin、safety、mcp_scheduler、system_tools
- 提交：`af157655`（chat+admin）、`092e1d68`（skills）、`2b9cc881`（mcp/scheduler）、`2c3562f5`（system_tools）、`3fc9c7f2`（memory）、`02db10b1`（status）、`7dd427db`（safety）
- 每域迁移后跑回归 + 冒烟；路由路径与迁移前完全一致（blueprint 不设 url_prefix）

## 2026-08-30: 插件化 T1.10 完成（装配器收尾 + 全量回归，阶段 1 结案）
- 删除临时插件 `plugins/example.py`；清理 `app_server.py` 迁移残留注释（208,505 B → 59,080 B，-71.7%，≤60KB 达标）
- 修复脚本直跑下插件延迟导入重复执行 `app_server` 的问题（`__main__` 注册 `sys.modules["app_server"]`），插件端点 500 → 200
- 路由集合对比 PASS（迁移前 175 = 迁移后 app_server 13 + plugins 163，仅新增 `/api/plugins`）
- `/api/plugins` manifest 8 插件完整；E2E 冒烟 8 端点全部 200
- 全量 pytest（PYTHONIOENCODING=utf-8 + seed 20260813）：15083 passed / 14 failed（7 基线 + 7 环境/顺序）/ 0 errors
- 前端：vitest 258 用例通过；tsc/build 修复前为既有破损（见下）
- 提交：`3cfb4fe4`，已推送 origin/master

## 2026-08-30: 排除项修复（前端构建 + 测试顺序污染）
- 前端：`main.tsx` 回退 legacy 入口（App.tsx），删除孤儿破损源码（WorkbenchApp、observability/*、utils/sentry、replayRecorder），`requestInterceptor` 移除失效动态 import；tsc / vitest 258 / npm run build 全绿
- 后端：`agent/prompt_manager/storage.py` 版本历史查询补 `rowid DESC` tiebreaker（修复 created_at 并列导致的顺序 flaky，实测 3/6→0/6、200 次循环 0 坏序）；`test_performance_alert_manager_singleton` autouse fixture 增加 alert_manager 重置（消除顺序污染）
- 提交：`97c8e50f`，已推送 origin + gitee

## 2026-08-30: 阶段 2 前端插槽化完成（T2.1–T2.4，结案）
- T2.1 `643699e5`：slotRegistry 核心（registerSlot / mountToSlot / getSlotEntries / loadProfile + 单测）
- T2.2 `e7836ced`：App.tsx 外壳插槽化（topbar / sidebar / main，行为不变）
- T2.3 `9ece9965`：SkillManagement / Knowledge / DevConsole 面板插槽化 + PanelSwitcher 统一驱动（zustand panelsStore）
- T2.4 `67a6e417`：profile 配置驱动完善——profile.json 为唯一组装配置（order/hidden）；`import.meta.glob ?raw` 惰性加载，文件缺失/损坏静默回退代码内 `DEFAULT_PROFILE`；新增 `reloadProfile(variant)` 运行时切换（含 `profile.alt.json` 验证变体）；`PROFILE.md` 界面组装文档
- 验证：tsc ✅；vitest 22 文件 / 292 用例 ✅（含 13 条回退与变体单测）；npm run lint 0 errors ✅；npm run build ✅；删除全部 profile 文件后 tsc+build 仍通过（回退实测）
- 提交：`25d51cc2`（进度/README 归档），已推送 origin（GitHub）+ gitee 至 `25d51cc2`

## 2026-08-31: 阶段 3 Schema 驱动自解释 UI 完成（T3.1–T3.3，结案）
- T3.1 `6c6269cb`：后端 Schema 协议落地——`Plugin.schema` 校验（`register_plugin` 非法抛 ValueError）+ status/safety/skills 声明 schema + manifest 输出 schema 字段；`tests/test_plugin_schema.py` 12 项
- T3.2 `9a64f0da`：前端通用 SchemaRenderer + 7 类字段控件（Select/Input/Textarea/Number/Switch/Tags/ObjectGroup）+ 未知降级 JsonFallbackField + 嵌套折叠；`SchemaRenderer.test.tsx` 23 + `fields.test.tsx` 18 项
- T3.3 `c19dbcfa`：插件中心——`Plugin.submit_url` 协议（写入 manifest）+ status 声明 `/api/status/config` 统一端点（字段分流真实子系统 + `StatusConfigManager` 持久化）；前端 `PluginPanel.tsx` 挂入 panels 插槽（列表 + SchemaRenderer + 值预填 + 提交 Toast + 空 schema/无端点降级）；`PluginPanel.test.tsx` 11 项 + `test_plugin_submit_url.py` 6 项
- 验证：tsc ✅；vitest 25 文件 / 344 用例 ✅（阶段 3 新增 52 条）；lint 0 errors ✅；build ✅；后端全量 pytest 1874 passed（唯一失败 `test_create_gitee_release_script.py` 为存量环境问题，stash 验证与阶段 3 无关）；status 闭环实测（改参 → 提交 → 生效 → 还原）✅
- 提交：`c19dbcfa`（代码）+ 结案报告归档，已推送 origin（GitHub）+ gitee

## 2026-08-31: 阶段 4 动态装载完成（T4.1–T4.2，四阶段全部交付）
- T4.1 `5e980b4f`：后端 `plugins/loader.py` 目录扫描自动加载（`pkgutil` 扫描、单插件失败隔离、原子重建注册表）+ `POST /api/plugins/reload`（`require_token`，失败保留旧注册表）；启动装配改 `loader.load_all()`
- T4.2 本提交：前端运行时发现 + 动态装载——
  - `plugins/plugin_api.py` 新增 `Plugin.client_slot` 可选字段（manifest 输出 `client_slot`）
  - `plugins/demo_plugin.py` 演示插件：schema + `submit_url=/api/demo/config`（GET/POST 闭环）+ `client_slot={slotId:"panels", module:"/plugins/demo-ui.js"}` + `/api/demo/probe`
  - `yunshu-ui/src/plugins/pluginDiscovery.ts`：`PluginInfo` 契约（camelCase）+ `fetchPlugins`（GET + 归一化：submit_url/client_slot → submitUrl/clientSlot，空 schema → null）+ `reloadPlugins`（POST 后重新拉取）+ `loadClientUi`（动态 import → `register(registry)` / 默认导出组件挂入插槽）+ `SlotRegistryFacade` + 生产 `/static` 前缀回退
  - `slotRegistry.ts` 新增 `extendProfile`（运行时追加 profile 条目）；`PluginPanel.tsx` 顶部「刷新」按钮（成功更新列表 + Toast / 失败保留旧列表 + Toast / 加载态禁用）+ clientSlot 插件「加载 UI」按钮
  - `public/plugins/demo-ui.js` 原生 ES 模块（`register(registry)` → mountToSlot + extendProfile + openPanel）；`build:flask` 追加复制 `dist/plugins` → `static/plugins`
  - 单测：`pluginDiscovery.test.ts` 11 项 + `PluginPanel.test.tsx` 扩展（刷新成功/失败/加载态 + 动态装载成功/失败）；后端 `test_plugin_schema.py` +3 项（client_slot 契约 + demo 声明）
- 验证：tsc ✅；vitest 26 文件 / 363 用例 ✅；npm run build ✅（dist/plugins 复制正确）；后端插件相关 pytest 30 项 ✅；`app_server` 冒烟：`/api/plugins` 9 插件（demo 含 client_slot/schema/submit_url）、`/api/demo/probe`、`/api/demo/config`、带 token `POST /api/plugins/reload` 200
- 提交：本提交（代码 + 结案报告 + 进度归档），已推送 origin（GitHub）+ gitee

## 2026-08-31: 阶段 4 交付收尾（CI 全绿 + stakeholders 确认结案，四阶段收官）
- 推送核查：`6011900a`（T4.2 代码）+ 本归档提交已推送 origin（GitHub）+ gitee 双远端
- CI/CD：`6011900a` 触发的 **13/13 个 workflow 全部 success**——yunshu-ui 前端测试（lint+typecheck / vitest 363+覆盖率 / build / 总结 4 job 全绿）、云枢系统测试流程（后端全量 pytest）、master commit 来源守卫、硬编码密码扫描、lock-discipline-scan、核心不变量监控、环境健康检查与工作区守卫、部署文档到 GitHub Pages、关键字参数冲突扫描 (Docker)、kwarg 扫描→SonarQube、日志性能守护、Error Reporting System CI/CD、可观测性质量保障（17 job）
- 结案报告更新：`docs/DELIVERY_CLOSEOUT_REPORT_PHASE4_20260831.md` 补充 §6 任务验收核对、§7 CI/CD 验证、§8 遗留问题（401 鉴权约束 / demo 插件保留 / lint 存量 warnings / static 构建产物部署流程）、§9 验收记录（stakeholders 确认）
- 遗留问题：均非阻塞（环境鉴权约束、演示插件保留、存量技术债、部署流程产物），无需本次修复
- 结论：阶段 1–4 四阶段插件化改造路线全部交付收官
