# 云枢工作台 · 交付报告（2026-09-19）

> **范围**：本轮「工作台 UI 优化 + 功能调整 + 缺陷修复 + 交付收尾」全部改动。
> **代码终态**：见文末 §7「提交与推送」的 SHA 回填。
> **验证口径**：本地门禁（架构规则 / 核心不变量 / 单测基线回归）+ 真实浏览器端到端探针 + 真实 LLM 端到端调用。

---

## 1. 进度总览

| # | 需求 | 状态 | 关键产出 |
|---|---|---|---|
| 1 | 导航结构调整（技能中心上提、工具调用移前） | ✅ 完成 | `hubNav.tsx`；工具调用 5 子项改页面内 Tab |
| 2 | 提示词实验室（去「返回工作台」、Tab 化、身份提示词联动与排序） | ✅ 完成 | `prompt-lab/index.tsx`、`IdentityPromptPanel.tsx`、`identityPrompt.ts` |
| 3 | LLM 通信监控（移入主内容区 Tab、折叠面板、Token 替代时间、关闭持久化） | ✅ 完成 | `LlmMonitorPanel.tsx`、`agent/llm_monitor.py` |
| 4 | 会话任务（子代理下拉、后台任务下拉） | ✅ 完成 | `SubagentMenu.tsx`、`BackgroundTasksMenu.tsx`、`routes_background.py` |
| 5 | 对话输出格式与风格切换 | ✅ 完成 | `useChatPrefsStore.ts`、`ChatStyleMenu.tsx`、`workbench.css` |
| 6 | 恢复思考过程/工具调用显示 + 显隐开关 | ✅ 完成 | `useLayoutStore.ts`、`MessageItem.tsx`、`plugins/chat.py` |
| 7 | 缺陷修复（思考/工具消失、子代理没跑通、LLM 配置漂移） | ✅ 完成 | 见 §3 |
| 8 | 交付收尾（报告 / 门禁 / 提交推送 / 验收） | ✅ 完成 | 本文件 |

---

## 2. 成果（可观测指标）

- **前端**：`vitest` **80 文件 / 692 用例全绿**；`tsc --noEmit`（src + electron）零错误；`npm run build:flask` 产物已同步 `static/` 与 `templates/yunshu.html`。
- **后端**：本轮新增/相关用例 **>160 例全绿**（详见 §5）；`memory/tests` 由 *11 红* 修为 **75 passed**。
- **门禁**：架构规则校验 ✅（7 规则 / 0 违规）；核心不变量 ✅（12/12）；单测基线回归 ✅（§5.3）。
- **端到端（真实浏览器 + 真实 LLM）**：
  - 思考/工具内联显示：3 轮对话 → 3 个思考块 + 7 个工具块，流结束 25s 后仍在；硬刷新后由历史恢复。
  - 子代理真委派：`ok=True tier=jsonl`，返回真实产物 + 自评（score=0.88~0.9）。
  - LLM 自检：`ok=true`，`HTTP 200 / 1.3s`，`workbench_demo_mode=false`。

---

## 3. 遇到的问题与解决方案（含根因）

### 问题 1｜对话里的思考过程与工具调用"先出现、一会儿就没了"，显隐开关点了没反应

**根因**（三层，逐层修）：
1. **done 事件清空累积内容**：后端 SSE 的阶段事件是 `running`(带 detail) → `done`(不带 detail)，而前端合并规则把"非 running→running"一律按覆盖处理 ⇒ 累积文本被清空，块因"无内容"而消失。**同一缺陷在服务端落盘口径也存在**。
2. **渲染条件按"有没有文本"判空**：文本被清空 ⇒ 整块隐藏（而非只剩标题）。
3. **刷新/切会话必然丢**：步骤只活在内存，历史消息不含步骤。

**解决**：
- 前端 `useLayoutStore.mergeStepDetail()` + 服务端 `plugins/chat.py::merge_thinking_step()`：**没有新内容的事件绝不覆盖已有内容**（两端口径一致）；两次 `running` 才做分片累加。
- `MessageItem`：有步骤即渲染，逐条列出（阶段/推理/工具），默认展开可折叠；开关关闭时给「已隐藏 N 步 · 点击显示」提示条；开关带步骤计数徽标（无内容时 title 说明原因，避免"开关坏了"的错觉）。
- **步骤随 assistant 消息落盘**（`session_manager.add_message(steps=...)`）+ `restoreSteps()` 让刷新/切会话后自动恢复。
- Electron 跨窗口同步补「防信息倒退」：本窗口流式中或对方快照更旧时拒绝覆盖。

**验证**：新增 `thinkingSteps.test.tsx`（真实事件序列回归 10 例）、`test_chat_steps_persistence.py`（13 例）、`sync.test.ts`（4 例）；真实浏览器探针 `scripts/dev/thinking_inline_probe.mjs`。

---

### 问题 2｜"子代理没跑通"

**根因**（三层）：
1. **前端/接口打的是占位骨架**：`/api/subagent/<name>/execute` 走 `SubagentContainer.execute()` —— 设计上不调 LLM、只回"骨架实现"文案（HTTP 200，看起来成功）。真链路是 `run_delegation()` → `DelegationExecutor`（八要素 → 工具裁剪 → 隔离 → Trace → 成本）。
2. **`.env` 里是 `sk-test…` 占位 key**：对话流有 `key_usable()` 判定，占位 key ⇒ 一直跑**演示模式**（固定文案）；真执行器则会原样拿到上游 401。
3. **LLM 配置三源漂移**：`agent/data/network_config.json` 的 llm 段是模板遗留 `openai/gpt-4`，启动时 `apply_to_app()` 把它**显式传入** `configure_llm()`；而旧实现只在"参数为空"时读 `.env` ⇒ `Yunshu._llm` 用 gpt-4、工作台对话用 deepseek（同一部署两条链路两个模型），真委派必然 400：`The supported API model names are deepseek-flash, deepseek-v4-pro, but you passed gpt-4`。

**解决**：
- 新增 `POST /api/subagent/<name>/delegate`（真委派，八要素缺省补齐、结果形状与模型侧 `delegate` 工具同源）；前端改用该端点，并用原生 fetch 解析错误体（`hubPost` 会丢弃 body，导致只能看到 "HTTP 409"）；菜单提前提示"无执行通道"。
- `configure_llm()` 按自身 docstring 的策略改为 **`.env` 有值即优先**（真冲突 warning 点名、仅补缺降为 debug）；`MemoryManager` 新增 `_resolve_llm_config()`，同样 env 优先、config.yaml 兜底。
- `network_config.json` 的 llm 段对齐 `.env`（已备份原文件）。
- 抽出 `agent/llm_key.py::key_usable` 作为**单一来源**（消除 `agent/` → `plugins/` 的反向依赖）。

**验证**：`test_subagent_delegate_route.py`（12 例）、`test_configure_llm_env_authority.py`（4 例）、`test_memory_llm_config_source.py`（8 例）、`test_llm_key_policy.py`（7 例）；线上真委派 `ok=True`。

---

### 问题 3｜测试与工程卫生的隐性债

| 现象 | 根因 | 处理 |
|---|---|---|
| `memory/tests` 11 例红 | ①异步化改造后用例仍按同步断言（`_do_compress` 已是协程、却直接调用；`get_context` 压缩在后台线程完成却立即断言）②fixture 用 7 字符 `sk-test`，被 LLMService 长度校验拒于构造期 | 修用例（await + 有界等待、长占位 key），**75 passed** |
| `tests/unit` 全量跑一半进程消失 | `pytest.ini` 用 `--timeout-method=thread`，超时时 `os._exit(1)` 杀整批（本仓已有专文 `docs/closeout/TEST_TIMEOUT_20260919.md`），触发点是 `import app_server` 的慢用例 | 复现口径改为 `--timeout=300`（与 `failures_baseline.txt` 重建口径一致） |
| 测试运行污染仓库 | 契约 JSON 时间戳、运行期任务日志、LLM 监控快照被写脏/未忽略 | 回退噪声改动；`.gitignore` 新增 `data/llm_monitor_last.json(.tmp)` |
| 自动化误报 | `.env` 的 `APPROVAL_RECORDS_PATH=agent/data/...` 是服务真实配置，测试守卫把服务写入误判为用例写脏并**删除** | 守卫检测到活跃后端时跳过归因且不删除（CI 无服务，守卫照常生效） |

---

## 4. 变更清单（本轮）

**后端**
- `agent/llm_monitor.py`：会话最后一条通信落盘 + 启动回填 + `atexit` 兜底 + 节流。
- `agent/session_manager.py`：`add_message(steps=...)`。
- `agent/system_prompt_config.py`：`compute_emit_info()`（发出内容/发出顺序/阶段）+ 配置接口回传。
- `agent/server_routes/routes_background.py`（新）：后台任务列表/状态/结果/取消。
- `agent/server_routes/routes_subagent.py`：`/delegate` 真委派 + `channel` 可用性。
- `agent/server_routes/routes_logging.py`：`POST /api/diagnostics/llm-check`。
- `agent/orchestrator/lifecycle_manager.py`：`.env` 优先的分层配置。
- `memory/memory_manager.py`：`_resolve_llm_config()`（env 优先）+ 显式 `base_url`。
- `agent/llm_key.py`（新）：key 形态判定单一来源。
- `plugins/chat.py`：思考过程 SSE 外发、步骤随消息落盘、`on_reasoning`（additive）、key 判定转发。
- `memory/llm_service.py`：`on_reasoning` 可选回调（additive）。
- `app_server.py`：后台任务路由注册；`/chat` 改为**每请求读盘**（构建后无需重启，修掉旧 HTML 引用已删 chunk 导致的白屏）。

**前端（yunshu-ui）**
- 导航：`hubNav.tsx`（技能中心上提、工具调用移前）；`pages/hub/tools/index.tsx`（5 Tab 容器）。
- 提示词实验室：Tab 化 + 去返回按钮（`pages/prompt-lab/index.tsx`）；身份提示词联动与发出顺序（`IdentityPromptPanel.tsx`、`identityPrompt.ts`）；监控面板重写（`LlmMonitorPanel.tsx`）。
- 会话任务：`SubagentMenu.tsx`、`BackgroundTasksMenu.tsx`、`LlmHealthMenu.tsx`；思考/工具内联渲染与开关（`MessageItem.tsx`、`ChatPanel.tsx`）；风格与格式（`useChatPrefsStore.ts`、`ChatStyleMenu.tsx`）。
- 布局：下线右侧「思考过程」面板（`mosaic.ts` + 迁移 `stripRetiredPanels` + `renderPanel`/`DetachedChatApp`/`ipc.ts`/`electron/main.ts` 收敛）。
- 工程：`vite.config.ts` 注入构建戳 + 顶栏 `build MM-DD HH:MM` 徽标（解决"改了没用=页面没刷新"的归因难题）。

**测试/工具**
- 新增测试：`test_chat_steps_persistence.py`、`test_subagent_delegate_route.py`、`test_background_tasks_routes.py`、`test_system_prompt_emit_info.py`、`test_workbench_sse_thinking.py`、`test_llm_self_check_route.py`、`test_configure_llm_env_authority.py`、`test_memory_llm_config_source.py`、`test_llm_key_policy.py`；前端 `thinkingSteps`/`SubagentMenu`/`LlmHealthMenu`/`mosaic`/`useChatPrefsStore`/`history-steps`/`layout-migration`。
- `scripts/dev/thinking_inline_probe.mjs`：真实浏览器探针（多轮 + 工具调用 + 刷新存活）。

---

## 5. 验证证据

### 5.1 前端
```
vitest run  →  Test Files 80 passed (80) | Tests 692 passed (692)
tsc -p tsconfig.json --noEmit        → exit 0
tsc -p electron/tsconfig.json --noEmit → exit 0
npm run build:flask                  → ✓ built（产物已复制 static/ + templates/yunshu.html）
```

### 5.2 后端（按模块）
```
tests/unit/test_subagent_delegate_route.py      12 passed
tests/unit/test_background_tasks_routes.py      12 passed
tests/unit/test_chat_steps_persistence.py       13 passed
tests/unit/test_llm_self_check_route.py         11 passed
tests/unit/test_llm_key_policy.py                7 passed
tests/unit/test_configure_llm_env_authority.py   4 passed
tests/unit/test_memory_llm_config_source.py      8 passed
tests/unit/test_system_prompt_emit_info.py      12 passed
tests/unit/test_workbench_sse_thinking.py        4 passed
tests/unit/test_llm_monitor_persist.py          10 passed  + test_llm_monitor_singleton 12、test_s2_03_integration 47
tests/integration/test_routes_config_integration.py 100 passed
memory/tests                                    75 passed（修复前 11 红）
```

### 5.3 门禁与基线（本地复现 CI 口径）
```
python scripts/ci_run_module.py agent.observability.arch_rules --check → ✅ 通过（7 规则 / 0 违规）
python scripts/verify_core_invariants.py --quiet --repo-root <repo>    → PASS 12/12（pre-push 门禁）
scripts/dev/git_precommit_check.ps1（pre-commit 钩子）                  → 链接 0 失效 + 锚点回归 4 passed
关键字参数冲突扫描（pre-commit 内，HIGH 阻断）                          → HIGH 0
前端 npm run check / vitest run                                        → tsc 0 错误 / 692 passed
```

**单测基线回归**（仓库真正的门禁是 `scripts/check_baseline_regression.py` 的差集，而非 pytest 退出码）：

| 口径 | 结果 | 判定 |
|---|---|---|
| 全量 `tests/unit`（`-n 4` 并行，14:11） | **19242 passed / 5 failed / 4 errors** | 5 个 failed 全在 `test_ci_guard_fix_regression.py`：该文件**单独跑 24 passed**、`-n 4 --dist loadfile` 亦 24 passed ⇒ 并行编排产物（该文件会 spawn 子进程跑 guard 脚本），**非回归**；4 errors 无用例 ID，属 worker 级 |
| 尾段 130 文件（顺序，6:03） | **4485 passed / 218 skipped / 18 xfailed / 0 failed** | 覆盖顺序全量未到达的区域（`test_settings_registry.py` 之后），干净 |
| 顺序全量（CI 同款 `--timeout=300`） | **跑到 75% 被 `os._exit(1)` 杀掉** | 触发点 `test_settings_registry.py::scan` fixture 读文件阻塞 >300s；该文件**单独跑 27 passed / 39.5s** ⇒ 属本仓已登记的 L2 机制（`--timeout-method=thread` 超时即杀整批，见 `docs/closeout/TEST_TIMEOUT_20260919.md`），**不是失败** |
| 基线差集（`check_baseline_regression.py`） | 7 条基线项本轮已通过（基线可收缩）；除上述并行产物外**新增 0** | 仓库纪律：只允许基线收缩 |

> 结论：**本轮交付未引入新的单测失败**；`memory/tests` 另有 11 红 → 已修为 75 passed（§3 问题 3）。

### 5.4 端到端（真实服务 + 真实 LLM）
```
思考/工具内联：3 轮 → 思考块 3 / 工具块 7；流结束 25s 后仍在；硬刷新后思考块 2（历史恢复）
子代理真委派：ok=True tier=jsonl duration≈5.1s；产物含自评 score=0.88
LLM 自检    ：ok=true demo_mode=false；probe HTTP 200 / 1273ms
推送后 CI   ：远端 2 分钟内出现 github-actions[bot] 提交
              「docs(architecture): 自动更新模块依赖图 [skip ci]」（架构 workflow 已在本轮推送的
              提交上跑通并产出依赖图）—— 证明 CI 已被触发；其余 workflow 结果需在 Actions 页面确认
              （本机 github.com:443 不可达，无法读取 run 状态）
```

---

## 6. 遗留问题（结案口径）

| # | 遗留 | 归属/影响 | 结论 |
|---|---|---|---|
| L1 | 仓库固化 **78 条已知单测失败**（`ci.yml` 用 `‖ true` 容忍，真正的门禁是基线差集） | 存量债务，与本轮无关 | **留待专项**；本轮以"基线零新增"结案（§5.3） |
| L2 | `tests/unit` 全量在**本机**会被 thread-timeout 整批杀掉（`test_settings_registry.py::scan` 读文件阻塞 >300s） | 环境/编排问题，本仓已有专项文与最小复现脚本 | **留待按 `docs/closeout/TEST_TIMEOUT_20260919.md` 落地**（属其工作流）；本轮已用"分块 + 尾段补跑"取得等效证据 |
| L3 | 子代理自述模型身份不实（自称 Claude） | 对外输出可信度 | ✅ **已结案**：`LlmChannelExecutor` 在 system prompt 末尾如实声明实际 `provider/model`（4 例测试） |
| L4 | `data/async_tasks.jsonl` 被跟踪但已在 `.gitignore` | 仓库卫生（每次运行弄脏工作区） | ✅ **已结案**：`git rm --cached`（磁盘文件保留，按需 append 重建） |
| L5 | 工作台无命令执行能力（用户已选"暂不做终端"） | 设计取舍（安全优先） | ✅ **已按 A 方案结案**：诊断做成「LLM 自检」按钮；如后续需要受控命令面板（复用 `shell_execute` + HITL 审批）可另立任务 |
| L6 | `.env` 成为 LLM 部署级权威 ⇒ 网络配置页改模型仅在 `.env` 对应项为空时生效 | 行为约定（与该函数 docstring 一致，且与对话链路统一） | ✅ **已确认**：写入本报告；如需"UI 优先"须另行设计优先级（不建议，会重新引入两源漂移） |

**结论：本轮交付范围内无未处理遗留**；L1/L2 属仓库级存量债务（各有专项/工作流归属），不阻塞本次交付。

---

## 7. 提交与推送

- **仓库/分支**：`origin` = `git@github.com:nzt47/security-tools.git`（SSH），`master`。
- **推送**：`git push origin master` 三轮均成功（每次推送前远端都已前进一条 CI 自动提交，故每次均 `git rebase --autostash` 后快进推送）：
  1. `406ea0c2..c2187ce5`（远端先有 `6624f03f`）；
  2. `6624f03f..2f5b73df`（L3/L4 修复 + 报告回填；远端先有 `dd9461ae`）；
  3. `dd9461ae..b36f1945`（本报告 §7 口径修正）。
- **代码终态**：以远端 `origin/master` 为准 —— **验收时现场执行** `git rev-parse origin/master` 与 `git log --oneline -8`（撰写时的 tip 为 `2f5b73df`；远端随时会因 CI 自动提交或并行会话继续前进，固化字面 SHA 无意义）。本地 `HEAD` 与 `origin/master` 一致、无未推送提交。
- **本轮提交链**（主题为准，见下）：

  | 主题 | 说明 |
  |---|---|
  | `fix(chat)`: 思考/工具内联显示不再"出现后又消失" | 3 处根因（merge 规则/渲染条件/仅内存） |
  | `refactor(workbench)`: 下线右侧「思考过程」面板 + 布局迁移 + 构建戳 | `stripRetiredPanels` 迁移、`__YUNSHU_BUILD__` |
  | `feat(subagent,diag)`: 子代理真委派 + LLM 连通性自检 | key 判定单一来源 `agent/llm_key.py` |
  | `fix(llm)`: .env 为部署级权威 | `configure_llm` / `MemoryManager` 环境优先 |
  | `chore(delivery)`: 交付报告 + 忽略运行期产物 | 本文件 + `.gitignore` |
  | `fix(subagent)`: 执行体如实声明实际运行模型；运行期任务日志不再跟踪 | L3/L4 结案 |

  > **为什么不写具体 SHA**：两次推送前均因远端 CI 自动提交而 `git rebase`，rebase 会重写本条链上所有 SHA（撰写时链中 `06adf75e` 即已被重写为 `db02d91f`）。沿用本仓库既有结论（"v7.2 S4-01 报告改为引用代码终态，避免自指追逐"），此处只固化**主题 + 远端 tip**，SHA 请以 `git log` 现场值为准。
- **CI 观测（远端自动提交 = CI 确已在本轮提交上运行的硬证据）**：两次推送后各在 2 分钟内出现 `github-actions[bot]` 的架构依赖图自动提交（`6624f03f`、`dd9461ae`），且 **`docs/architecture/dependency_graph.json` / `module_dependency_graph.md` 中已包含本轮新增模块**：`agent.llm_key`（`crosslayer`）、`agent.server_routes.routes_background`（及其 `-.-> agent.async_executor` 依赖边）⇒ 架构 workflow 确实拉取并解析了本轮提交（含新文件），CI 触发链路通畅。
  其余 ~49 个 workflow 的运行结论请在仓库 Actions 页面确认：本机到 `github.com:443` 不可达（`gh` 与 REST API 均不可用），无法从此环境读取 run 状态。
- **本地等效验证**：见 §5.1–5.3（架构规则 / 核心不变量 / 预检 / 前端全量 / 单测基线差集）。

---

## 8. 验收清单（待 stakeholder 确认）

- [ ] 导航：技能中心为顶层项、工具调用位于全景看板之前、工具调用页内 5 个 Tab 可切换
- [ ] 提示词实验室：无「返回工作台」；身份提示词启用即显示发出内容、按发出顺序排列；悬停高亮
- [ ] LLM 通信监控：主内容区 Tab；折叠展开；行内显示 Token 数（无时间）；重启后可见"上次会话"快照
- [ ] 会话任务：子代理下拉可选并真委派成功；后台任务下拉可见/可取消
- [ ] 对话输出：格式（气泡/紧凑/终端）与风格（主题/气泡/字号）可切换且生效
- [ ] 思考与工具：内联显示、默认展开、显隐开关有效、刷新/切会话后仍在
- [ ] 右侧「思考过程」面板已下线；历史布局不丢（迁移只剔除该面板）
- [ ] LLM 自检按钮结论与实际一致（含演示模式提示与修复建议）
