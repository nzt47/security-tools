# TASK-S4-04 验收报告 — subagent 真实现（八要素委派 + CLI 通道 + 回收三件套 + 临时凭据 + 工具裁剪）

> 所属阶段：S4 治理与安全｜波次：第二波｜worktree：`s404`（`--base master`）
> 开工基线：`master` / `15eae00d`（实际起点为当时 master 头 `ab6ddb9f`）
> 交付分支：`s404/main`｜提交：`3298ae94`
> 验收日期：2026-09-12｜依赖：S2-01（Trace，已交付 `TraceContext.child()`）、S4-01（Actor 矩阵，已结案）

---

## 一、交付物清单

| # | 交付物 | 规模 | 性质 |
|---|---|---|---|
| 1 | `agent/subagent/delegation.py` | 495 行（全新增） | **新增**：委派契约八要素 + `task_file` 物化 + 校验器 |
| 2 | `agent/subagent/channel.py` | 702 行（全新增） | **新增**：§3.10 CLI 通道物理协议 + 三级降级解析 + taint |
| 3 | `agent/subagent/executor.py` | 919 行（全新增） | **新增**：真执行器（LLM 循环 / 并行编排 / 全闸门） |
| 4 | `agent/subagent/collection.py` | 749 行（全新增） | **新增**：回收三件套 + 计费两栏 + stage 闸门 |
| 5 | `agent/subagent/credentials.py` | 545 行（全新增） | **新增**：§5.9 临时凭据（TTL / 销毁 / 每来源独立 / manifest 闸门） |
| 6 | `agent/subagent/toolset.py` | 518 行（全新增） | **新增**：§5.7 机制 3 工具裁剪子集（对齐 S4-01 矩阵） |
| 7 | `agent/subagent/barrier.py` | +243 行（→587 行） | **追加**：`ConcurrencyBarrier`（§4.2 并发上限 + 回压） |
| 8 | `agent/subagent/sandbox.py` | +114 行（→667 行） | **追加**：§5.9 第三方执行默认隔离 |
| 9 | `agent/subagent/container.py` | +75 行（→361 行） | **追加**：`run_delegation()`（既有 `execute()` 行为不变） |
| 10 | `agent/subagent/lifecycle.py` | +65 行（→391 行） | **追加**：`delegate()`（分身旁挂契约⑦超时） |
| 11 | `agent/subagent/__init__.py` | +132/-4 行 | **改动**：导出全部新能力（`__all__` 42 项） |
| 12 | `tests/unit/test_subagent_{delegation,channel,toolset,credentials,collection,barrier,isolation,executor,container_delegation}.py` | **487 例** / 3654 行 | **新增** 9 个用例文件 |
| 13 | `scripts/demo_s4_04_agent_cli.py` | 137 行 | **新增**：端到端样例用本地协议桩（真实进程） |
| 14 | `scripts/demo_s4_04_delegation.py` | 376 行 | **新增**：端到端样例驱动器 |
| 15 | `docs/zh/.../evidence/TASK-S4-04_端到端样例_20260912.{md,json}` | 84 行 md + JSON | **新增**：可复现证据（本报告 §四 引用） |
| 16 | 本文件 + `S4-04_交付结案报告_20260912.md` | — | **新增** |

> 提交统计：`20 files changed, 8211 insertions(+), 6 deletions(-)`（提交 `3298ae94`）。

**落点纪律**：全部落在 `agent/subagent/` 与 `tests/unit/`、`scripts/`、`docs/`，与第一波（`agent/security`、`agent/policy`、`agent/memory`、`agent/monitoring`）**零文件重叠**（与批次总表 §二 的预判一致）。

---

## 二、§四 验收清单逐条核验

| # | 验收项 | 结论 | 证据（命令 / 观测） |
|---|---|---|---|
| 1 | 八要素缺任一即拒绝委派（用例） | ✅ | `tests/unit/test_subagent_delegation.py` — `@pytest.mark.parametrize("element", EIGHT_ELEMENTS)` 逐要素 ×3 组断言（`validate` / 拒绝点名 / 上下文自校验）+ 全缺 8/8。实测：`all-missing: ('goal','constraints','prior_artifacts','prohibitions','artifact_format','budget_tokens','timeout_seconds','callback_url')` |
| 2 | task_file 符合 §3.10 物化格式；CLI 入口可执行 | ✅ | 八要素**平铺顶层** + `schema_version`/`tenancy`/`trace`/`policy_version`；往返一致 + UTF-8 中文保真。CLI 入口经**真实子进程**验证：`'python.exe' 'scripts/demo_s4_04_agent_cli.py' --emit jsonl -p '<task_file>' --output-format json --max-turns 10` |
| 3 | 输出解析：JSON Lines 成功 → 重试 1 次 → 纯文本+LLM 抽取 → `E_UPSTREAM_FORMAT`（四级用例） | ✅ | `test_subagent_channel.py::TestThreeTierDegradation` 14 例：tier1 `jsonl` / tier2 `jsonl_retry`（attempts=2）/ tier3 `text_extract` / tier4 `upstream_format`。**四条独立路径**都能到达 `E_UPSTREAM_FORMAT`（`no_llm` / `parse` / `empty` / `returncode`）；端到端实测见证据 §C |
| 4 | 并行委派受并发上限与回压约束（测试） | ✅ | `test_subagent_barrier.py`（并发不变量 `peak ≤ N`、`BackpressureTimeout`、纯回压不丢弃）+ `test_subagent_executor.py::TestParallelOrchestration`。端到端实测：上限 2 → **峰值 in_flight = 2**；屏障收紧到 1 且池开 4 时产生 `E_BACKPRESSURE_TIMEOUT` 显式失败（非无限排队）；结果保序 |
| 5 | 回收三件套齐全才计成本；缺任一标记浪费并阻塞 stage 推进 | ✅ | `test_subagent_collection.py` — 齐全→`counted=True`；**5 类不齐全参数化**→`counted=False`/`wasted=True`/`counted_tokens=0` 且浪费**可见**（`wasted_tokens`/`wasted_cost_usd`）；`StageGate.require` 抛 `StageBlocked` 并点名缺哪一件 |
| 6 | 临时凭据 TTL 结束销毁（注入后强制销毁用例）；每来源独立凭据 | ✅ | `test_subagent_credentials.py` 63 例：`ttl > 任务时长 → CredentialTTLTooLong`（**拒绝而非静默截断**）；`finally` 在正常/异常/超时三路径销毁；销毁后读明文抛 `CredentialDestroyed`、`wipe_verified=True`；同名异来源 = 两条独立凭据。端到端实测：委派后存活凭据 **0** |
| 7 | 裁剪工具集外调用被拒（无记忆读写/核心改写/审批权断言） | ✅ | `test_subagent_toolset.py` 70 例：记忆读/写、审批四动词、核心改写六项、治理写四项**全部被拒**；**显式授权也不能解锁禁区**（矩阵拒绝行优先于授权子集）；未登记受保护类别 fail-closed。端到端实测见证据 §B（含间接路径 `mcp:filesystem::approval.approve` → 命中 `approval.approve`） |
| 8 | 委派全程 Trace 带 `actor=sub_agent` + `parent_trace_id` | ✅ | `test_subagent_executor.py::TestDelegationTrace` 7 例 + 端到端证据 §G：13 行落库 Trace **全部** `actor=sub_agent` 且 `parent_trace_id` 非空 |
| 9 | **【S2-01 #5】** 子 Trace 经 `TraceContext.child()` 生成 | ✅ | `executor._enter_child_trace()` 唯一入口即 `parent.child()`；断言 `parent_trace_id != parent.trace_id`（证明派生了新 id 而非直接抄父 id）且 `facade.chain(child_id) == [本次委派行]`（证明它就是本次委派的父环节点） |
| 10 | 既有 process_distill/subagent 套件零回归；新增单测全绿、覆盖率 ≥80% | ✅ | `process_distill` **32 例**（与任务书基线数字一致）全绿；`test_subagent.py`+`test_subagent_manager.py`+distill 家族 166 passed。新增 **487 例全绿**；`agent.subagent` 包覆盖率 **91%**（12 文件相关套件 612 passed） |
| 11 | 真实委派端到端样例在验收报告可复现 | ✅ | `python scripts/demo_s4_04_delegation.py --json <path>`（真实子进程）；证据存档见 §四，脚本随提交入库 |

---

## 三、实施要点（逐条呼应本任务特有硬约束）

### 3.1 八要素校验前置（硬约束 #1）
`DelegationExecutor.execute` 的**第一件事**是 `ctx.require_valid()`：不合格**立即拒绝**，不落盘、不签发凭据、不写 Trace、不占用并发槽位。拒绝原因**点名缺哪一项**（`①目标`…`⑧回调地址`）并给出**机器可读明细** `outcome.error_detail["missing"]`——调用方无需解析中文串即可知道该补哪一项。

**「未声明」与「声明为空」的分界**（本任务的核心判定设计）：③已有成果与④禁止事项在真实委派中**合法地可能为空**（首个委派没有已有成果），故 `[]` 记为「显式声明无」，`None` 记为「未声明 → 拒绝」。②约束为空则**拒绝**——无约束的委派等同于没写边界，正是 §3.9 点名的含糊；①目标另设 `MIN_GOAL_CHARS=8` 的机器可判定下界。

### 3.2 CLI 通道物理协议与三级降级（§3.10）
命令行严格为 `<agent_cli> -p <task_file.json> --output-format json --max-turns N`。

两条刻意的判定：
- **超时不重试**：超时来自契约⑦的预算，重试会翻倍占用；超时直接进第 3 级（有输出则抽取，无输出则记格式失败），并在 `sub_reason` 保留 `timeout` 真实成因，**不把超时伪装成格式问题**。
- **JSON Lines 严格**：tier 1/2 只接受「每个非空行都是一个 JSON 对象」，带 markdown 围栏或多行缩进对象一律不合格。放宽会让「上游到底有没有遵守协议」失去机器可读证据；围栏样本因此必然走到 tier 3/4（端到端证据 §C 的 C4 即为该路径）。

### 3.3 真执行器与并行编排
- **内部 LLM 等价实现**（`LlmChannelExecutor`）：真实多轮循环，逐轮 `llm.chat` 直到产出最终 JSON 对象或达到 `--max-turns`，再按 §3.10 输出 JSON Lines。与外部 CLI 的差别只在「谁来执行」，协议侧完全一致，故两者可互换注入。
- **外部 CLI 为可选路径**：`CP_SUBAGENT_AGENT_CLI` 配置后即走真实子进程；未配置且无 LLM 时**显式失败**（`_UnconfiguredExecutor` 返回明确错误），**不静默返回假成功**。
- **并发与回压**：`ConcurrencyBarrier` 用 `BoundedSemaphore` 保证 `in_flight ≤ N` 不变量（多还抛 `ValueError`，不静默抬高上限），等待采用 §4.2 的指数退避 + 抖动，`queue_timeout` 到期抛 `BackpressureTimeout`——让「排不进去」成为**显式失败**而不是永久挂队。
- **跨线程上下文**（上游已知坑 #4）：`TraceContext` 是 ContextVar 语义，**不跨线程继承**；父 Trace 以参数显式传入，子上下文在 **worker 内部**由 `child()` 现场生成，不做任何隐式继承假设。

### 3.4 安全：临时凭据 / 工具裁剪 / 隔离
- **凭据**：`credential_scope` 在 `finally` **无条件**销毁（超时/取消/`KeyboardInterrupt` 都不经过 `except Exception`，只有 `finally` 能保证销毁）；`destroy()` 先擦明文再落状态；销毁后 `value` 抛异常而非返回空串——「隐藏失败会长期留存凭据」，故把「读取失败」做成**可断言事实**。每来源独立凭据（独立 id / 环境变量 / TTL / 销毁）。
- **工具裁剪的单一权威**：工具名 → §7.0 矩阵操作的**映射表**（寻址），判定仍走 `agent.security.actor_matrix.decide()`。矩阵收紧一行 → 工具层自动跟随；未登记的受保护类别 **fail-closed 拒绝**（「未登记」≠「允许」）。
- **间接调用拦截**：`name_candidates()` 把 `mcp:filesystem::memory.write` 一类包装名展开为全部后缀形态；`check_spec()` 扫描 `alias`/`aliases`/`target`/`redirect`/`delegate` 等**全部别名槽位**。端到端证据 §B 的 B3 即间接路径被拒。
- **隔离**：§5.9 三个「无」落成**默认值**（`trusted=False`）。隔离覆盖把 `HOME`/`SSH_AUTH_SOCK` 置为**空串**而非删除——「存在但为空」能挡住 `os.environ.get("SSH_AUTH_SOCK", default)` 一类回退默认值；宿主云凭据（`AWS_`/`GH_TOKEN`/`OPENAI_` …）按前缀**移除**。`env_mode=ENV_REPLACE` 是必需的：若叠加宿主环境，被隔离删掉的键会被宿主环境**带回来**（静默失效）。
- **外来文本不可信**（§5.7 机制 1/2）：`TaintedText` 的 `str()` 只返回占位符（故 `%s` 日志不可能泄漏原文），拼接与 f-string 格式化**直接抛异常**（fail-closed），只有 `for_sandbox_slot()` 能取原文；`for_system_prompt()` / `for_tool_arg()` 显式拒绝。LLM 抽取时外来文本**只进 user 消息的沙箱槽位**，system prompt 为云枢自有文本。
- **子代理不可审批**：`approval.approve/deny/reject/submit` 全部被拒（矩阵 + 工具层双重），并有断言守护（`test_subagent_toolset.py`）。

### 3.5 实现期发现并修复的**真实缺陷**（7 项）

| # | 缺陷 | 后果 | 发现方式 |
|---|---|---|---|
| 1 | `DelegationContext.__post_init__` 把 `prior_artifacts=None` 归一为 `()` | **未声明被悄悄变成「已声明为无」→ 绕过 §3.9 准入** | 逐要素参数化用例 |
| 2 | `TemporaryCredential.is_expired/age_seconds/destroyed_at` 用墙上时钟，忽略管理器注入的 clock | 签发与到期**不同源**，注入时钟的调用方拿到自相矛盾的状态；TTL 到期不可测 | 时钟注入用例 |
| 3 | `authorized_capabilities` 未做名称规范化 | 同一 capability 的 `Read-File`/`read_file` 两种写法被判成两条 → 合法工具被误拒 | 规范化用例 |
| 4 | `executor.py` 清理导入时误删 `RawOutput` | LLM 执行器的**全部错误路径**触发 `NameError`（静默变成另一个异常） | 测试 |
| 5 | `lifecycle.py` 未导入 `Any` | 类型检查失败（注解为字符串故运行期不报，易漏网） | mypy |
| 6 | `credentials.age_seconds` 返回 Any | `no-any-return` 违反 | mypy |
| 7 | 回调审计 `status=payload["status"]` 类型不符 | `arg-type` 违反 | mypy |

> 说明：缺陷 4 若未被测出，会在「LLM 执行器出错」这条**冷路径**上长期潜伏——正是「隐藏失败」的典型形态。缺陷 1/2 属**语义级**问题（不是崩溃而是判定错误），仅在「逐要素 / 注入时钟」这类穷举式用例下才现形。

---

## 四、端到端样例（真实子进程委派）

**复现命令**

```powershell
cd <worktree>
python scripts/demo_s4_04_delegation.py `
  --json "docs/zh/CloudPivot_v7.2重构计划/evidence/TASK-S4-04_端到端样例_20260912.json"
```

**诚实标注（对齐任务书「上游已知坑 #1」）**
本环境 `.env` 为空、`CP_SUBAGENT_AGENT_CLI` 未配置、无外部 LLM 凭证，故**子代理侧使用本地协议桩**（`scripts/demo_s4_04_agent_cli.py`，**真实子进程**，不做 LLM 推理）。因此本样例提供的是「**协议与安全的真实证据**」，不含模型推理质量证据；配置 `CP_SUBAGENT_AGENT_CLI` 后同一代码路径即走真实第三方 CLI，无需改动任何实现。

完整证据存档：[`evidence/TASK-S4-04_端到端样例_20260912.md`](evidence/TASK-S4-04_端到端样例_20260912.md)（含 JSON 原始态）。要点摘录：

### A. 一次成功委派（§3.9 + §3.10 + §3.4）

| 观测项 | 实测值 |
|---|---|
| 结果 | `ok=True` / tier=`jsonl` / 调用次数=1 |
| §3.10 命令行 | `<python.exe> <demo_s4_04_agent_cli.py> --emit jsonl -p <task_file.json> --output-format json --max-turns 10` |
| 回收三件套齐全 | `True` |
| 成本计入核算 | `True`（counted_tokens=1500） |
| Trace actor / status | `sub_agent` / `success` |
| Trace `parent_trace_id` | 非空（= `child()` 派生 id，**≠** 编排 trace id） |
| 裁剪后可见工具 | `['read_file', 'search_docs']` |

### D. §5.9 隔离实测 —— **从子进程内部回读**（最强证据）

不是宿主侧的假定，而是**被委派进程自己报告的实测值**：

| 探针 | 子进程内实测值 |
|---|---|
| `HOME` | ``（空） |
| `USERPROFILE` | ``（空） |
| `SSH_AUTH_SOCK` | ``（空） |
| `CP_SANDBOX_HOST_NETWORK` | `0` |
| 宿主 `AWS_SECRET_ACCESS_KEY` 可见 | `False` |
| 宿主 `GITHUB_TOKEN` 可见 | `False` |
| 宿主 `OPENAI_API_KEY` 可见 | `False` |
| 临时凭据键名（仅键名，无明文） | `['CP_TEMP_MCP_GITHUB_GITHUB_TOKEN', 'CP_TEMP_MCP_SEARCH_SEARCH_KEY']` |

**三个「无」与「凭据只走临时通道」由子进程实测坐实**。

### B. 工具裁剪闸门（含间接路径）

| 子代理声明的调用 | 委派结果 | error_code | 命中比对形态 | 矩阵操作 |
|---|---|---|---|---|
| `['read_file']` | ok=`True` | — | — | — |
| `['memory.write']` | ok=`False` | `E_TOOL_NOT_AUTHORIZED` | `memory.write` | `memory.write` |
| `['mcp:filesystem::approval.approve']` | ok=`False` | `E_TOOL_NOT_AUTHORIZED` | `approval.approve` | `approval.approve` |

### C. 三级降级

| 样例 | 输出形态 | tier | 调用次数 | 结果 |
|---|---|---|---|---|
| C3 | 纯文本（+ 桩 LLM 抽取） | `text_extract` | 2 | ok=`True` |
| C4 | markdown 围栏（严格拒绝） | `upstream_format` | 2 | `E_UPSTREAM_FORMAT`（`no_llm`） |

### E. 凭据销毁

委派后存活凭据数 **0**；`credentials_destroyed=True`；签发/销毁累计 3/3；凭据记录 `destroyed=True`、`wipe_verified=True`、`destroy_reason=task_end`，且只保留**指纹**（`af51df92a4233cfa`）不保留明文。

### F. 并行与并发上限

委派 6 个、成功 6 个；上限 2 → **实测峰值 in_flight = 2**（`峰值 ≤ 上限 = True`）；准入 6；结果**保序** `True`。

### G. 落库 Trace 行

13 行 `capability_id=subagent.delegate` 全部 `actor=sub_agent` 且 `parent_trace_id` **非空**（含 `success` 与 `error` 两种终态——失败委派同样留痕）。

---

## 五、质量证据

### 5.1 新增单测与覆盖率

| 套件 | 例数 |
|---|---|
| `test_subagent_delegation.py` | 70 |
| `test_subagent_channel.py` | 80 |
| `test_subagent_toolset.py` | 70 |
| `test_subagent_credentials.py` | 63 |
| `test_subagent_collection.py` | 75 |
| `test_subagent_barrier.py` | 19 |
| `test_subagent_isolation.py` | 29 |
| `test_subagent_executor.py` | 66 |
| `test_subagent_container_delegation.py` | 15 |
| **合计（新增）** | **487 全绿** |

```powershell
python -m pytest tests/unit/test_subagent_delegation.py tests/unit/test_subagent_channel.py `
  tests/unit/test_subagent_toolset.py tests/unit/test_subagent_credentials.py `
  tests/unit/test_subagent_collection.py tests/unit/test_subagent_barrier.py `
  tests/unit/test_subagent_isolation.py tests/unit/test_subagent_executor.py `
  tests/unit/test_subagent_container_delegation.py -q -p no:randomly
# → 487 passed
```

**覆盖率**（`--cov=agent.subagent`，含既有 subagent 相关套件共 12 文件 612 passed）：

```
agent\subagent\__init__.py           11      0   100%
agent\subagent\barrier.py           241     21    91%
agent\subagent\channel.py           289      5    98%
agent\subagent\collection.py        337     17    95%
agent\subagent\container.py          95      7    93%
agent\subagent\credentials.py       239      6    97%
agent\subagent\delegation.py        182      9    95%
agent\subagent\executor.py          399     36    91%
agent\subagent\lifecycle.py         117      7    94%
agent\subagent\observability.py      31     31     0%   ← 既有模块（本次未改动）
agent\subagent\sandbox.py           217     30    86%
TOTAL                              2487    220    91%
```

**新增/改动模块最低 86%、包整体 91%**，均 ≥80%。仅 `observability.py` 为 0%（**既有模块、本次未改动**，其测试归属另行登记）。

### 5.2 邻接回归（零降级）

| 套件 | 结果 |
|---|---|
| `test_subagent.py` + `test_subagent_manager.py` + distill 家族（`test_process_distill.py` / `test_distiller.py` / `test_distill_feedback.py` / `test_knowledge_distill.py`） | **166 passed / 0 failed** |
| 其中 `test_process_distill.py` **基线 32 例** | **32 passed**（与任务书基线数字一致） |
| `test_trace_v2.py` + `test_trace_v2_integration.py` + `test_events_v1.py` | **176 passed / 0 failed** |
| approval / security / actor_matrix 邻接（`-k "approval or security or actor_matrix"`） | **658 passed / 2 skipped / 0 failed** |

### 5.3 本地门禁

| 门禁 | 结果 | 命令 |
|---|---|---|
| kwarg 扫描（agent，HIGH） | ✅ 0 项 | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` → exit 0 |
| kwarg 扫描（tests，HIGH） | ✅ 0 项 | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` → exit 0 |
| mypy：**新增模块** | ✅ 0 错 | `python -m mypy agent/subagent/{delegation,channel,toolset,credentials,collection,executor,container}.py` → 无 `agent.subagent.*` 报错 |
| mypy：既有阻塞模块 | ✅ 无回归 | `agent/env_config_manager.py` + `agent/network_config.py`：基线 `Found 477 errors in 80 files`，本次**同为 477/80** |
| mypy：改动模块残留 | ✅ 仅 3 项**既有**债 | `sandbox.py:472`、`lifecycle.py:248/260` —— 已用主工作区 master 版**逐条比对确认基线同存**（非本次引入） |
| importlinter | ✅ 2 kept / 0 broken | `lint-imports --config .importlinter`（需 `PYTHONUTF8=1`，否则 GBK 解码报错） |
| 架构规则（arch_rules） | ✅ 通过 / 未豁免违规 0 | `python -m agent.observability.arch_rules --check --root agent --exemptions docs/architecture/legacy_exemptions.json` → 7 规则、4 违规全部已豁免（与基线一致，**无新增环依赖**） |
| pre-commit（真实提交场景） | ✅ 8 passed / 2 skipped | 见 §5.4 |
| 产物漂移还原 | ✅ | 门禁生成的 `docs/architecture/arch_rules_report.json` 已 `git checkout --` 还原；提交后 `git status` 干净 |

### 5.4 pre-commit（真实提交，未使用 `--no-verify`）

```
关键字参数冲突扫描 (HIGH 风险拦截)........................................Passed
工具定义索引同步校验 (YAML → tool_index.json).............................Passed
敏感信息检测 (API key / 私钥 / 密码)......................................Passed
知识卡片 CLI 全生命周期校验 (32 项断言)...................................Passed
CLI parser 注册一致性校验 (AST 符号级)....................................Passed
并行会话 index 隔离校验 (空 index / 运行时文件混入).......................Passed
PowerShell 静态分析 (PSScriptAnalyzer)................(no files to check)Skipped
logging.disable 泄漏扫描 (try/finally 保护)...............................Passed
策略即代码 schema 门禁 (TASK-S4-02 / §3.11)...........(no files to check)Skipped
docs 链接预检诊断 (失效链接 + 修复建议)...................................Passed
exit=0
```

`git commit` 直接执行成功（pre-commit 生效并自动还原了 `data/learned_workflows.json` 的 3 条运行时统计漂移）。

---

## 六、遗留问题（逐条带归属与阻塞性判定）

| # | 遗留项 | 归属 | 阻塞性 | 说明 |
|---|---|---|---|---|
| 1 | 端到端样例的子代理侧为**本地协议桩**（环境无外部 agent CLI / LLM 凭证） | 部署/环境 | **不阻塞** | 已如实标注；配置 `CP_SUBAGENT_AGENT_CLI` 后同一路径即真实第三方 CLI。模型推理质量证据需真实凭证环境 |
| 2 | Trace 父链的**中间子上下文不落库** | S2-01（Trace）/ 本任务登记 | 不阻塞 | 落库委派行的 `parent_trace_id` = `child()` 派生子上下文 id；该子上下文本身无行（`finish()` 硬编码 `parent_trace_id=""` 且 `actor=ACTOR_AUTO`，无法承载 `actor=sub_agent`，故委派行改走 `record()` 以同时取得 actor 与非空父链）。任务级聚合由 `task_id` 承载（delegation 行继承编排 `task_id`）。如需「子上下文也落行」，需 S2-01 扩展 `finish()` 的 actor/parent 入参 |
| 3 | `agent/subagent/observability.py` 覆盖率 0% | 既有模块（非本次改动） | 不阻塞 | 本次未触碰；建议随 P5 骨架清理一并评估 |
| 4 | 未新增 `EventType` 事件类型 | 本任务裁定 | 不阻塞 | 委派留痕走 **Trace + `audit.facade.record`**（`subagent.delegation.{rejected,backpressure,callback,cost}`），**不改动** S2-03 的既有枚举与测试；如需事件流订阅另行提案 |
| 5 | 「每能力 ≥20 条同类轨迹」口径 | 口径纪律（批次总表 §三-5） | 不阻塞 | 本任务**不声称**任何「真实能力内化」；仅交付委派执行器与回收证据链 |
| 6 | 委派执行成本的真实金额 | S5-03（成本刹车） | 不阻塞 | `CostLedger` 已按 §3.9 分「计入核算 / 视为浪费」两栏产出 token 与 USD；真实单价与刹车阈值由 S5-03 承接 |

---

## 七、结论

任务书 §四 **11 项验收清单全部通过**。`agent/subagent/` 已从占位骨架升级为**真实委派执行器**：八要素准入闸门、§3.10 CLI 物理协议与三级降级、真实 LLM 多轮执行与并行编排、§4.2 并发回压、§3.9 回收三件套与 stage 阻塞、§5.9 临时凭据与第三方默认隔离、§5.7 机制 3 工具裁剪（对齐 S4-01 单一权威矩阵）、S2-01 `TraceContext.child()` 子 Trace 串联（遗留 #5 已消费）均已落地并有机器可读证据。

新增 **487 例单测**全绿、`agent.subagent` 覆盖率 **91%**、邻接与基线套件**零回归**（`process_distill` 32 例基线全绿）、本地门禁**全绿**。既有公开接口与行为未被破坏（`SubagentContainer.execute()` 语义原样保留，升级走新增路径）。
