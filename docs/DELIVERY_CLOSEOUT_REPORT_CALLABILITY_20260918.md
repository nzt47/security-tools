# 交付收尾报告：工具 / 技能「可被 LLM 调用」统一标注（2026-09-18）

> 交付范围：统一字段标注（L1 权威声明 + 派生清单）+ 模型可见集过滤 + 目录/预览 API + 界面三档标识 + 守门脚本与测试 + 规范文档
> 关联文档：[工具与技能可调用性标注规范](工具与技能可调用性标注规范.md) · [主线装配指南](主线装配指南.md) · [工具集评估与重分类报告](工具集评估与重分类报告.md)
> 上一份交付：[主线装配与工具集重分类收尾](主线装配指南.md)（同链路，本次为其补上"可调用性"这一治理维度）

## 1. 交付范围与目标

| 目标 | 内容 |
|------|------|
| 统一字段 | 工具与技能同构的九项字段：`tool_name` / `tool_type` / `llm_callable` / `callable_mode` / `schema_registered` / `host_executor` / `permission_level` / `sandbox_allowed` / `reason` |
| 可自动解析 | 派生清单 `data/capability_manifest.json`（不手改，`--check` 守门），REST 可读 |
| 权限控制可接 | 模型可见集按声明 fail-closed 过滤；检索索引同步排除；三档标识供人眼与程序共用一套口径 |
| 单一口径 | 判定只在 `agent/lines/callability.py`；目录端点、装配预览、界面三处读同一份清单 |
| 不破坏现状 | 开关打开前后**模型可见集完全相同**（可验证的零行为变化）+ 一处环境变量回滚 |

## 2. 已完成工作与成果

| 交付物 | 内容 | 状态 |
|--------|------|------|
| [agent/lines/callability.py](../agent/lines/callability.py) | 字段定义、判定（硬阻断/软阻断）、清单构建、`non_callable_tool_names` | ✅ 新增 |
| [agent/lines/models.py](../agent/lines/models.py) | `ToolMeta` 携带六个声明字段（目录/UI 直接可读） | ✅ 扩展，0 破坏 |
| [agent/tools/__init__.py](../agent/tools/__init__.py) | `get_tool_defs` 隐藏声明判否工具；新增 `registry_facts()`（schema/执行器/source） | ✅ 扩展 |
| [data/tool_definitions/*.yaml](../data/tool_definitions) | 91 个工具补齐可调用性声明（与 plane/effect/risk 相邻，字段顺序统一） | ✅ 全量 |
| [data/skill_callability.yaml](../data/skill_callability.yaml) | 技能侧声明（defaults + 个别覆盖；含"为何不用 front matter"的理由） | ✅ 新增 |
| [data/capability_manifest.json](../data/capability_manifest.json) | 统一清单 114 条（91 工具 + 23 技能实体），含八字段 + 标识 + 诊断字段 + 统计 + 口径声明 | ✅ 派生 |
| [scripts/backfill_tool_callability.py](../scripts/backfill_tool_callability.py) | 幂等回填；`--check` 查缺失 + 对拍 `permission_level` 与治理轴派生值 | ✅ 新增 |
| [scripts/sync_capability_manifest.py](../scripts/sync_capability_manifest.py) | 派生清单 + 自洽性校验（三条不变量）+ `--check` 拦手改 + `--summary` | ✅ 新增 |
| [scripts/sync_tool_index.py](../scripts/sync_tool_index.py) | 检索索引排除判否工具（否则 hybrid 召回会绕过隐藏） | ✅ 扩展 |
| [agent/server_routes/routes_agent_lines.py](../agent/server_routes/routes_agent_lines.py) | `/planes` 每行带 `callability`；新增 `/api/capability-manifest`；装配预览 `tools_meta` 每行带 `callability`（与目录同源，附 `callability_source`） | ✅ 扩展 |
| [agent/settings/registry.py](../agent/settings/registry.py) | 登记 `CP_TOOL_CALLABILITY_ENFORCE`（回滚开关，默认开） | ✅ 扩展 |
| [yunshu-ui/src/lib/callability.ts](../yunshu-ui/src/lib/callability.ts) | 契约类型 + 展示层纯函数 + 只读客户端（判定权威在后端，前端不重算） | ✅ 新增 |
| [yunshu-ui/src/pages/hub/components/ui.tsx](../yunshu-ui/src/pages/hub/components/ui.tsx) | `CallabilityBadge`（含 compact 档，供装配预览 chip） | ✅ 扩展 |
| 界面接线 | 工具集页 / 主线管理页（选择器 + 装配预览 chip）/ 技能库与技能中心：徽章 + 图例 | ✅ |
| [tests/unit/test_tool_callability.py](../tests/unit/test_tool_callability.py) | 50 用例：声明层 / 判定层 / 派生层 / 接线层 / REST 面（真实 app 三类标 `slow`，另留一条源码级快速守门） | ✅ 新增 |
| [scripts/dev/route_unify_smoke.mjs](../scripts/dev/route_unify_smoke.mjs) | 顺带修掉两处导航时序 flaky（本次交付中发现） | ✅ 修复 |
| [docs/工具与技能可调用性标注规范.md](工具与技能可调用性标注规范.md) | 字段规范 / 判定规则 / 三层归属 / 用法 / 新增能力时的标注步骤 / 文件索引 | ✅ 新增 |

## 3. 验证结果

| 验证项 | 结果 |
|--------|------|
| 单元测试（本次新增） | `tests/unit/test_tool_callability.py`：**快车道 45 passed / 5.05s**（`-m "not slow"`）+ **慢车道 5 passed / 74s**（`-m slow --runslow`，真实 app 端点/标注/清单三验） |
| 关联回归（4 个文件） | `test_tool_callability + test_agent_lines + test_tool_definitions_yaml + test_permission_policies_consistency` = **143 passed / 0 failed**（快车道口径） |
| 守门脚本（CI 口径） | `backfill_tool_planes --check` / `backfill_tool_callability --check` / `sync_tool_index --check` / `sync_capability_manifest --check` **全部 exit 0** |
| 清单自洽性 | 八字段齐全；❌ ⇔ 不可达；⚠️ 必须给出成因；✅ 必有执行器 + Schema；手改清单会被 `--check` 拦住 |
| 前端 | `tsc -p tsconfig.json --noEmit` 0 错 · `eslint` 0 告警 · `vitest src/lib/callability.test.tsx` **16 passed** · `vitest src/pages/hub` **99 passed** · `npm run build:flask` 成功 |
| 浏览器冒烟（真实 Edge 无头 + CDP，打运行实例 5678） | `npm run smoke:route` **19 PASS / 0 FAIL**（连跑两次一致） |
| 线上实测（重启后） | `/api/capability-manifest` 200，`total=114 ✅80 ⚠️33 ❌1`，`by_trigger={model:90, system:23, none:1}`（清单文件按 mtime 重读，改数据后无需重启后端）；`/api/agent-lines/planes` 91 行全带标注；装配预览 `tools_meta` 37 行带标注且 `callability_source` 正确；`/chat` 引用的新 chunk 200 且含标识文案 |
| 清单可复现（CI 口径） | 把两个 .gitignore 的运行时技能文件指到不存在路径后重算 ⇒ 与提交产物**逐字段一致**（`test_清单只依赖入库数据` 锁死） |
| 零行为变化 | 判否集合 ⊆ `internal`（当前仅 `process_distill_run`）⇒ 模型可见集与改动前完全相同，由测试锁死 |
| 本地其它门禁 | `verify_core_invariants` 12/12 PASS · `simulate_ci_guard_pipeline --assert-allowed` PASS · `check_ps1_encoding` PASS · `lint-imports` 2 contracts kept / 0 broken · 文档链接与锚点预检 PASS |
| CI/CD | 见 §6（推送后回填） |

## 4. 遇到的问题与解决方案

| 问题 | 根因 | 解决方案 |
|------|------|----------|
| **31 个技能全被标成 ❌，看上去像"技能全坏了"** | 判定把"不可达"与"可达但不经模型发起"混为一谈 | 拆成**硬阻断**（无执行器/无内容实体/已停用/被策略拒绝/内部专用 ⇒ ❌）与**软阻断**（声明 false / manual / 缺 Schema ⇒ ⚠️）；新增 `reachable` / `trigger` / `reason_kind` 三个字段把成因说全。`llm_callable` 语义不动（manual 恒 false，权限/网关读它） |
| **CI 红灯：清单不可复现（23 处差异）** | 清单读了 `data/skills.json` / `data/skills_mgmt.json` —— 两者都在 .gitignore 里，干净 checkout / CI 里不存在 ⇒ CI 重算的清单与提交产物必然不一致（8 个只在台账里的技能 + 15 个 reason/标识漂移） | 清单口径收紧为**仓库可复现**：默认只读入库数据（工具 YAML / 技能覆盖表 / `skills_repo/*/skill.md` / 策略文件 / 静态注册点扫描）；运行时技能目录与台账改为 `include_runtime_catalog=True` 显式并入（**产物不提交**），只声明而仓库无实体的技能登记进 `runtime_only_declarations` 如实披露。新增 `test_清单只依赖入库数据` 把"干净 checkout 可复算"钉死 |
| **CI 红灯：`test_fan_out` 字段顺序断言** | 该用例按"字段顺序照 grep.yaml"逐字比对 key 列表，新增五个标注字段后必然不等 | 更新期望列表（把新字段按其真实插入位置列入），保留"顺序契约"这一原始意图 |
| **CI 红灯：`test_settings_registry` 零缺口守卫出现 `<unresolved>`** | `scripts/scan_settings.py` 的 `KNOWN_READ_HELPERS` 把 **`_flag`** 登记为"环境开关读取助手"的名字契约；我把新的布尔归一助手命名成 `_flag`，于是 `_flag(doc.get(...), True)` 被当成开关读取点、参数非字面量 ⇒ 产出 `<unresolved>` 动态家族 | 助手改名 `_as_bool`（不带 env/getenv 词干，正则也匹配不到），并在 docstring 里写明"名字有讲究，勿改回 `_flag`" |
| **`test_skill_merge` 在 CI 超时（>60s）；同代码 rerun 又通过** | 分片争用：ci.yml 的单元测试分片是 **`-n 2` 并行** + `scripts/split_unit_tests.py` 按用例数贪心均衡，同一 shard 内两个 worker 相邻跑；本次新增的 `test_tool_callability.py` 因导入 `app_server`（连带 torch/sentence-transformers）单文件耗时 ~90s，挤到邻居 ⇒ 2s 的用例在负载下被顶到 60s 超时。证据：① 本地单独跑 2.17s；② 拆分计时 `import 0.39s / 构造 0.00s / 建 3 个技能 0.67s / merge 0.05s`（纯 Jaccard，不碰嵌入模型）；③ 仓库台账记录 2.81s；④ **同一份代码 `c1a72324` 首跑失败、`gh run rerun --failed` 后 shard 1 全绿**；⑤ 仓库自身文档已记录同类现象（shard 被 runner 回收、`can't start new thread`、排队风暴） | 把本交付的测试拆成**快慢两级**：`TestRestSurface`（依赖真实 app）整体标 `@pytest.mark.slow`，由 ci.yml 的 `-m "not slow"` 跳过、交给 `full-regression.yml --runslow` 单独跑；快速车道保留一条**源码级**端点存在性守门（`TestRestSurfaceStatic`），零导入开销。实测本文件：**快车道 45 passed / 5.05s（原 ~90s）**，慢车道 5 passed / 74s |
| 路由冒烟 flaky：两次失败用例不是同一批（15/4、17/2） | "hash 落地"与"导航重渲染"不在同一帧，断言落地即读 `innerText`；"展开栏目"一步更糟——读到未渲染就去点，反而把正在展开的组收起 | 两处即时取值改 `waitFor` 轮询（先等"已展开"，等不到再点，点完再等结果）；连跑两次 19/0 |
| `sync_capability_manifest.py --summary` 在 Windows 崩（UnicodeEncodeError） | 中文 Windows 控制台默认 GBK，打印 ✅/⚠️/❌ 失败（清单其实已正确落盘） | 脚本顶部显式 `sys.stdout.reconfigure(encoding="utf-8")` |
| 探针取 `/assets/index-*.js` 得 404，一度判为"产物没上线" | 页面用的是绝对 `/static/assets/...`，我的探针漏了前缀 | 按页面里真实的 `src` 取值复测 → 200（**是探针写错，不是产物问题**） |
| 新端点 `/api/capability-manifest` 与 `callability` 字段在运行实例上 404 / 缺失 | 后端进程 18:03 启动，内存里是改动前的代码（Python 代码改动必须重启） | 按 `start_yunshu.bat` 口径重启（kill 旧 PID → `python app_server.py`）；本次两次重启，日志留在 `logs/app_server_restart_*.log` |
| 派生清单可能被人手改而与权威漂移 | 派生文件天然有这个风险 | `--check` 逐字段对拍 + 装配预览与目录"同源"断言（`test_装配预览与目录的标注同源`） |
| **CI 架构规则红灯：`agent.lines.callability ↔ agent.tools` 循环依赖** | 本模块为取"运行时执行器事实"惰性 `from agent.tools import registry_facts`，而 `agent.tools` 反向依赖本模块（`get_tool_defs` 读 `non_callable_tool_names` 隐藏判否工具）⇒ 双向依赖。架构规则按 AST 扫描，**不看调用时机**，惰性导入照样判违规；本地单测、import-linter、pre-commit 全部绿，**只有 CI 的 architecture-check 能发现** | 依赖倒置：`callability.py` 不再导入 `agent.tools`，运行时事实改由调用方注入（`build_manifest(executor_facts=...)`）；`scripts/sync_capability_manifest.py --runtime` 在**脚本侧**取 `registry_facts()`（scripts/ 不在扫描根内）；`runtime_executors()` 保留为显式空实现 + 依赖倒置说明，防后人"顺手补个 import"。新增两条单测钉住：AST 扫本模块 import + 注入事实优先于静态扫描。提交 `8457346e` |
| `lint-imports` 本地报 `'gbk' codec can't decode byte 0x90` | 中文 Windows 默认编码，与改动无关 | `PYTHONUTF8=1` 复跑 → 2 contracts kept / 0 broken（CI 为 Linux UTF-8，不受影响） |
| 迁移脚本曾把治理声明整段抹掉（历史事故） | `migrate_tools_to_yaml.py` 不带 `--out` 会重写生产目录，且它从 Python 源码反推、不认识治理字段 | 本次新增字段同样落在 YAML ⇒ 由 `backfill_tool_callability --check` 与 `test_tool_callability` 双向守门；文档中重申"不要不带 `--out` 跑迁移脚本" |

## 5. 最终状态确认

- **代码**：4 个提交已推送到 `origin/master`（`18a9fa4c..8457346e`）：
  - `be85f84f` feat(callability)：后端 + 数据 + 脚本 + 测试（103 files）
  - `192925fb` feat(ui)：前端标识 + 构建产物引用（8 files）
  - `584803a3` fix(smoke)：冒烟时序 flaky（1 file）
  - `549060a9` docs(callability)：标注规范 + 装配指南 + 本报告（3 files）
  - `8457346e` fix(callability,ci)：拆循环依赖（architecture-check 红灯收口，3 files）
- **数据**：91 个工具 YAML 已具备可调用性声明；`data/capability_manifest.json` 为派生清单；`data/tool_index.json` 已按新口径重生成。
- **运行态**：入站 static 前端产物已重建（`npm run build:flask`），后端已重启并 `/api/health` 就绪；页面刷新即可看到标识。
- **回滚路径**：① 只回滚"模型可见集过滤" ⇒ `CP_TOOL_CALLABILITY_ENFORCE=0`；② 回滚声明 ⇒ 改 YAML/覆盖表后重跑 `sync_capability_manifest.py`；③ 整体回滚 ⇒ `git revert` 三个提交（无数据库/无迁移，纯数据 + 代码）。
- **不变量**：模型可见集与改动前完全相同（判否集合 ⊆ internal）；判定只有一处实现；清单不可手改（`--check` 守门）。

## 6. CI/CD 验证结论

- **推送**：`origin/master`（GitHub，CI 所在）已更新到 **`920a5670`**；`gitee` 镜像未推（见 §7）。
- **最终结论（head `920a5670`）**：21 个 workflow run —— **19 success、2 进行中（扩展系统健康检查 / Daily Regression Tests，均为 workflow_run 串联触发，非本交付改动面）**，
  其中 **「云枢系统测试流程」= success（21 个 job 全绿）**，含 6 个单元测试分片、4 个集成分片、
  E2E、覆盖率、文档链接预检与锚点回归、代码质量、安全扫描、知识库审计 CLI 冒烟。
- **发现并修掉四个真红灯**（全部由 CI 抓出，本地单测/pre-commit 都曾全绿）：
  1. `architecture-check`：`agent.lines.callability → agent.tools` 循环依赖 → 提交 `8457346e`（依赖倒置）；
  2. `单元测试 Shard 1`：清单不可复现（依赖 .gitignore 里的运行时技能文件，CI 报 23 处差异）→ 提交 `bc75e11d`（清单口径收紧为仓库可复现 + 两条守门单测）；
  3. `单元测试 Shard 2`：`test_fan_out` 字段顺序断言过期 → 同 `bc75e11d`；
  4. `单元测试 Shard 2`：`test_settings_registry` 零缺口守卫 `<unresolved>`（我的布尔归一助手名 `_flag` 撞上 `scripts/scan_settings.py` 的"环境开关读取助手"名字契约）→ 同 `bc75e11d`（改名 `_as_bool`）。
- **其余失败项为 `cancelled`**：本仓 workflow 统一配了 `concurrency: cancel-in-progress`
  （同 workflow 同 ref 只留最新一批），密集推送时旧 run 被新 run 取代 ⇒ **`cancelled` 不等于失败**。
- **逐项结论（head = `c1a72324`，22 个 run）**：除下表外全部 `success`/`skipped`：

| 工作流 | 结论 |
|--------|------|
| 云枢系统测试流程（6 shard × 3 Python） | 修复前：`8457346e`/`c1a72324`/`9d3361c0`/`22a91c0b` 四个 head 各命中一次 `failure`，**每次唯一失败都是** `test_skill_merge.py::TestServiceMerge::test_service_auto_merge_duplicates` 超时（>60s），跨 shard 漂移（5 → 1 → 2），且 `9d3361c0` 的 Shard 2 整片耗时 **1424s** ⇒ 负载型既有 flake。**修复后：最终 head `920a5670` 上「云枢系统测试流程」= success**（21 个 job 全绿：6 单元分片 + 4 集成分片 + E2E + 覆盖率 + 文档链接预检 + 代码质量 + 安全扫描…） |
| 架构规则校验 | `success`（循环依赖已拆） |
| 循环依赖校验 / 核心不变量 / 关键字参数 / lock-discipline / 硬编码密码扫描 / 环境健康 / 日期无关守卫等 | 全部 `success` |
| yunshu-ui 前端测试 / 部署文档到 GitHub Pages / Daily Regression Tests / 扩展系统健康检查 | `success` |
| master commit 来源守卫 | `success`（未阻断） |

> **并行会话交叉确认**：另一会话在 `d17c08c0` 里独立复现并定位了同两条 Shard 2 红灯
> （`_flag` 名字契约、`test_fan_out` 键序），给出的最小修法与我实际采用的一致；
> 它们另提了一条**更接近根因**的后续建议：让 `scripts/scan_settings.py` 的助手识别
> **按模块作用域**而不是按名字（属扫描器域，登记为 §7 遗留）。

## 7. 遗留问题与后续建议

| 项 | 性质 | 处理 |
|----|------|------|
| `tool_type` 的 `api` / `script` 两档当前 0 条 | 范围边界 | 本清单的口径是"模型可调用的能力面"，REST 端点与 `scripts/*.py` 不是模型可调用的形态。**明确不做**；将来若要按端点做权限控制再纳入（字段已支持，需补一份端点/脚本声明表） |
| `internal: true` 仍判 ❌（硬阻断）而非 ⚠️ | 口径选择 | 理由：`internal` 是"设计上不对外开放"（既不进模型可见集、也不进检索索引），不只是"模型不发起"。若 owner 希望统一成"可达即 ⚠️"，改 `judge()` 一处 + 一条断言即可 —— **待拍板**（默认保持现状） |
| `data/agent_lines/_active.json`、`data/tools_config.json`、`data/system_prompt_config.json` 留在工作区未提交 | 运行时状态 | 这三处是**运行中的应用**写的状态（活动主线、工具开关、`_last_applied` 时间戳），非本次交付物；不混进交付提交，由 owner 决定是否入库 |
| `gitee` 镜像未推送 | 发布动作 | 本次只推 `origin`（GitHub，CI 所在）。需要时 `git push gitee master` |
| `scripts/scan_settings.py` 的助手识别按**名字**而非**模块作用域** | 扫描器域的根因（并行会话 `d17c08c0` 提出） | 本次用最小改法（我的助手改名 `_as_bool`）绕开；根治需让扫描器按模块作用域判定"这是不是 env 读取助手"，属扫描器域，**未改**（避免与其它会话对撞同一文件） |
| `test_skill_merge` 在 CI 反复超时（>60s，本地 2.17s） | 分片争用：CI 分片参数是 `-n 2 --dist=loadscope --timeout=60 --timeout-method=signal`，同分片邻位一重（相邻模块在导入 torch/sentence-transformers）就把 2s 用例的**墙钟**顶爆。四个 head 各命中一次、跨 shard 5→1→2 漂移，同一份代码 `gh run rerun --failed` 后又全绿 | ① 消除**自身**那一半成因：本交付测试拆快慢两级（本文件 90s → 5s）；② 按本仓 `pytest.ini` 的既有纪律给该用例显式 `@pytest.mark.timeout(240)` 覆盖（"极慢/易受负载影响的用例不要依赖全局默认"）——真挂死仍在 240s 失败，不掩盖问题；行内写明证据与理由 |
| 清单口径只覆盖**仓库实体**技能（23 个） | 可复现性约束 | 只在运行时存在的技能（内联指令型台账条目、`extension_store` 装入的技能）不在清单内 ⇒ 界面无徽章（静默退化）。它们登记在 `runtime_only_declarations` 里披露；要看运行时全貌用 `--runtime --summary`（产物不提交） |
| "人眼确认徽章观感/位置" | 人工验收 | 属 owner 验收项（已重建产物 + 重启后端，刷新 `/chat` 即可） |
| 技能清单在界面上只覆盖 `/api/skills` 的 31 个 id | 已知边界 | `extension_store` 后续装入的技能不在清单口径内 ⇒ 静默无徽章（退化为现状，不报错） |

## 8. 验收清单（请 owner 确认）

- [ ] 字段设计与你给的规格一致（九项 + 诊断字段 + 三档标识）
- [ ] 三档标识的语义认可：✅ 可被模型发起 / ⚠️ 可执行但触发有条件（含"由系统·人工触发"）/ ❌ 不可达
- [ ] 技能侧 23 ⚠️（仓库实体；运行时技能不在清单口径内）的判定认可（技能不由模型发起，但照常生效）
- [ ] `internal: true` 保持 ❌ 的裁量认可（或要求改为 ⚠️）
- [ ] 模型可见集过滤默认开启（开关 `CP_TOOL_CALLABILITY_ENFORCE`）认可
- [ ] §7 遗留项的处理方式认可（尤其"api/script 不做"与"gitee 是否镜像"）
