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

## 2026-09-28: 遗留清理与「测试浮红」根治（本会话）
- 起点：`master` `1de7015e`；交付报告 `docs/closeout/遗留清理与浮红根治_20260928.md`（含逐条实测与**单变量反证**）
- **浮红根治（sleep 打桩作用域）**：`monkeypatch.setattr(<模块>.time, "sleep", ...)` 打的是**进程全局** `time.sleep`
  （实测 `tc.time is time` → True）⇒ 同进程泄漏的 daemon 轮询线程（生产代码 60 处 `time.sleep`，多处在轮询循环里）
  的 sleep 会混进记录器，把"环境噪声"判成"被测代码退化"（L6 记载的 CI 现场「slept 序列里出现越界值」）。
  新增作用域夹具 `scoped_sleep` / `scoped_async_sleep`（只记录被监视源文件发出的调用，其余透传真睡），
  改造 3 处用例，并新增 1 条**真实泄漏线程**回归锁（`BatchLogWriter` 故意不 `stop()`）。
  验证：`test_retry_budget.py` + `test_wait_index_retry.py` **28 passed**；单变量反证（`watched()` 换成恒 True）确定性变红
- **`test_skill_h3_migration.py` 的 3 条常红**：一半是**真 bug**（`[fixture]` 只隔离了 `SKILLS_MGMT_PATH`，漏了
  `SKILLS_JSON_PATH` ⇒ 本机读真实运行期目录而恒红、CI 冷启动却是绿），一半是**运行期台账正当漂移**
  （`data/skills_mgmt.json` 2026-09-28T19:49 经生产导入通道 +6 条 ⇒ 主轨独有由 2 变 8）。
  修法：隔离第二份运行期文件；判据拆「契约本体（⊇，两类来源都判）」+「快照精确性（只有 `[fixture]` 判）」，
  `[real]` 漂移时显式 skip 并贴出实测集合；新增 3 条非空转自证。验证：**26 passed / 2 skipped / 0 failed**
- **P2-2 任意路径写文件**：`generate_persistent` 的 `name`/`category` 未净化 —— **复现了**（停用净化后
  `category="../.."` 真的写到 `%TEMP%\my_tool.py`）。修法：前置两级净化（`_is_safe_path_segment` /
  `_is_safe_tool_module_name`）+ 31 条安全用例；`test_tool_generator_security.py` **59 passed**
- **门禁入口**：P1-1 `tool-retrieval-ci.yml` 补 `workflow_dispatch`；P1-3 新增 `route-conflict-gate.yml`（CLI 下限门，
  实测 0.9 s；默认下限 exit 0、`--min-pass 999` exit 1 反证）
- **失败基线收缩**：`failures_baseline.txt` 按"只允许收缩"纪律更新（见报告 §5）
- **第二批（同会话续）**：
  - **P2-8 + P2-5 运行期落点隔离**：`knowledge_audit.jsonl` 实测被测试追加（一次子集跑 **+2200 B**）⇒ 无条件重定向；
    技能主轨/工作流仓库实测 1822 用例跑完 **sha 一字未变** ⇒ 只在"文件不存在"时重定向（避免本机读真实台账的断言退化）；
    另加 6 条非空转自证（当场抓到过"助手用了未导入的 `pathlib.Path` 被 except 吞掉 ⇒ 夹具静默空转"）
  - **P2-4 破坏侧**：`test_tool_callability` 6 处 `T.clear()` / `test_fan_out` 无条件 `unregister` 会把**全局工具注册表**
    留成空表。确定性复现（收集期种 canary）：**1 条 → 0 条**；修后 **1 条 → 1 条**，三文件 **121 passed**；
    并加非空转守卫（停用还原那一行 ⇒ 守卫立刻红）。**泄漏侧未能复现**（`--runslow` 176 passed、落点逐字节不变）
  - **新发现并修掉的第二条常红**：`test_skill_description_single_source::test_main_track_only_allowlist_is_exact`
    与 h3 同一漂移家族；拆分时**非空转自证逼出一条"已不可达的 assert"**（多⇒skip、少⇒契约先红）⇒ 已删并写明承担者
  - **P1-2**：`rank_bm25` 的 `importorskip` 改硬前置 + 订正 `tool-retrieval-ci.yml` 过时注释；镜像断言补 CI 可跑孪生
  - **P2-1**：核验后判定"早已覆盖"（锚点测试比台账建议更强 + 扫描器已接进 `skills-check.yml`）
  - **事实订正**：pytest-timeout 的平台差异（`signal` on Linux ⇒ 用例判失败整轮继续；`thread` on Windows ⇒ `os._exit` 无摘要）
- **第三批：P0-1 判据重设计（中文召回 4/8 → 8/8）**
  - 推翻 MINSCORE1 的"不修"结论：候选特征从 5 个扩到 **15 个**，找到唯一能分开真阳性与 S10-03 噪声的新坐标
    —— **命中 token 在语料里的邻域规模 `reach`**（zh01=4 vs 噪声=1）；语义："域外查询只能与语料发生一次孤立碰撞"
  - 判据重设计四条：① 腿级地板 `_RRF_LEG_MIN_SCORE` 与调用方阈值**解耦**（收候选 vs 定验收分职责）；
    ② 补偿通道加"**证据非单点偶然**"（命中 ≥2 处 或 reach ≥2）；③ 编排层**不再用同一个 0.3 复判同一个 H/N**；
    ④ 观测透出 `quality_gate` + 闸日志新增 `evidence_reach/in_domain/gate_passed_via`
  - **验收（真实路径）**：中文 **8/8**、英文 8/8、S10-03 噪声锚空、真命中锚保留、负样本非空 **5/31 持平**、
    编排层端到端（真实 `_semantic_layer_match`）**8/8**；判据常量与有界键集合**一字未改**
  - **三条单变量反证**：停用编排层分层 ⇒ 端到端退回 4/8；腿级地板 ⇒ 新锁查 `tfidf_candidate_count`（改前恒 0）；
    证据条件 ⇒ 重写后的 `test_bm25_only_evidence_is_not_enough`
  - **误伤修正**：合成小语料下 `reach ≥ 2` 过严（真 query 主题词可能只落 1 条技能）⇒ 与"命中 ≥2 处"取或
  - **既有守卫抓到我的实现缺陷**：编排层最初写 `getattr(result,"quality_gate","")` 真值判断，MagicMock 替身会给出
    自动创建的真值属性 ⇒ 相关度闸被绕过（`Test答非所问_语义层误命中` 两条红）；已改为**白名单严格判定 + fail-closed**
- 仍未做（如实登记）：P1-2 剩余 4 条（产物全 gitignore，需造 tmp 产物孪生）、P2-3、P2-6~11、L6 残余、L10.2
  —— 见报告 §6/§8.8 与 `NEXT_SESSION_BACKLOG.md` 的 2026-09-28 回填段

## 2026-10-07: 契约治理第五轮（五）—— 写端点信封收口 · 自动 UI · RBAC 持久化 · 路由覆盖盘点
- 起点 `master 46159d6f` → 终点 `b5b67ae9`；A 段（三个老 PR 体检）+ B 段（六个 PR）合并。
- **A**：#970 已 squash 合并；#971 代码侧 111 passed、唯一红项为既有 flake 与 force-push 误触发的 legacy job；
  #967 CI 真实回归（line `prompt_note` 落入 F3-1 易变尾簇）⇒ 需一条适配提交。落纸见
  `docs/closeout/契约治理重构_第五轮_老PR体检_20261007.md`。
- **写端点信封**：第八批 `/api/personality/*`（#1034）、第九批 `/api/context/{config,compress}`（#1035）；
  「只加不改 + 两端原子化」；顺手修掉前端读错响应键（`custom_params`→`params`，此前静默不刷新）。
- **自动 UI（#1036）**：`src/features/<key>/index.tsx` + `manifest` 由 `import.meta.glob` 构建期自动挂进导航；
  `extensionHost` 按既有 `register(registry)` 约定动态挂载插件自带 UI；「扩展中心」成为 SchemaRenderer
  的**第二个真实消费方**。⇒ 新功能**零清单进 UI**。
- **RBAC 持久化（#1037）**：M-31 裁决；落盘 `{users,roles}`，路径**调用期**解析 `CP_ADMIN_RBAC_FILE`，
  测试地板隔离，AST 守卫 8 个写端点都必须落盘。
- **路由覆盖盘点（#1038）**：脚本 + 产物 + `GET /api/audit/route-ui-coverage` + 页面；
  「未分类活体只允许收缩」门禁。实测：静态 **451** / 活体 **233** / 死副本 **218**。
- **线上验证**：重启活体（PID 2684）后端点 **200 + v2**、无令牌 **401 + v2**；入口 chunk 含
  「扩展中心 / 路由覆盖」。⇒ 交付物在真实服务上可见。
- **遇到并解决**：① 自动解冲突用 `git add -u` 会把带冲突标记的文件当「已解决」⇒ 只对确认取 ours 的路径 add；
  ② 后台编排器函数内 `Write-Output` 污染返回值（已 GREEN 却走 STOP 分支）⇒ 日志只写文件；
  ③ 全局 `open`/`sleep` 探针捕到无关线程 ⇒ 探针收窄到目标目录（`test_load_tool_meta_cache` 单进程全量假红）。
- 未决：#967（设计边界待 owner 拍）、#971（等一次干净 CI）。
