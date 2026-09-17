# 自包含任务：为云枢实现 `fan_out`（按主线并行派发多 Agent）

> 复制下面整段到一个**新会话**即可执行。它不依赖任何前序对话上下文。

---

## 任务

在 `C:\Users\Administrator\agent`（中文 AI-agent 项目，代号"云枢"）中新增一个 **`fan_out`** 工具：
把 N 个任务**并行**派发给 N 个子代理，每个子代理按指定的**主线档案**装配自己的工具集，
汇总返回每个子任务的结果。

### 为什么需要它
`delegate` 工具是**串行单发**的。用户的核心诉求是"多 Agent，不同的 Agent 各管一条线"，
需要能**同时**跑多条线。底层并发原语已经存在（`DelegationExecutor.execute_many`），
本任务主要是**接线 + 按主线装配工具集**，不是从零实现并发。

## 必读的既有代码（先读，不要臆测 API）

| 关注点 | 文件 |
|---|---|
| 单发委派的完整流程（八要素校验 → 建 ctx → 取 manager → 建执行器 → 执行） | `agent/tools/subagent_tools.py`（`_run_delegate`，约 219-310 行） |
| 八要素契约与校验 | `agent/subagent/delegation.py`（`DelegationContext`、`validate_eight_elements`、`element_problems`、`ELEMENT_LABELS`、`EIGHT_ELEMENTS`） |
| 批量并发执行 | `agent/subagent/executor.py`（`execute_many(delegations, max_concurrency=...)`、`build_executor`、`ExecutionOutcome`、`DEFAULT_MAX_CONCURRENCY`） |
| 并发屏障 | `agent/subagent/barrier.py` |
| **子代理工具裁剪（申请 ∩ 授权 − 矩阵拒绝，fail-closed）** | `agent/subagent/toolset.py`（`SubAgentToolset.build`、`ToolNotAuthorized`） |
| **主线档案与装配器（本任务的核心依赖）** | `agent/lines/__init__.py`、`agent/lines/models.py`、`agent/lines/assembler.py`、`agent/lines/registry.py` |
| 主线装配指南（含四平面模型说明） | `docs/主线装配指南.md` |
| 工具注册模块的写法范例 | `agent/tools/file_tools_reg.py`、`agent/tools/system_tools.py` |
| 工具定义 YAML 范例 | `data/tool_definitions/grep.yaml`、`data/tool_definitions/delegate.yaml` |
| 单一接线点 | `agent/orchestrator/lifecycle_manager.py` 的 `_register_builtin_tools()` |

## 关键既有概念（避免踩坑）

- **四能力平面**：`resident` 常驻 / `perceive` 感知 / `act` 行动 / `govern` 治理。
  工具的 plane/effect/risk 声明在 `data/tool_definitions/*.yaml`。
- **主线档案**：`data/agent_lines/*.yaml`（已有 7 条：`assistant` `dev` `digital_life`
  `engineering` `harness` `knowledge` `recon`）。激活指针在 `data/agent_lines/_active.json`。
- **装配器**：`from agent.lines import assemble, get_line_registry`；
  `assemble(profile, available, meta=None, max_tools=None) -> AssemblyResult`，
  结果有 `.tools`（已排序的工具名列表）与 `.by_plane` / `.needs_approval`。
- **`SubAgentToolset.build(requested, authorized_capabilities)`** 取**三重交集**：
  申请 ∩ 授权 − §7.0 矩阵拒绝。两个入参都用**真实工具名**（如 `read_file`）。
  注意：它的 `_TOOL_OPERATION_RULES` 对真实工具名不设防（只认 `memory.read` 这类抽象点名），
  所以**授权清单本身就是唯一防线**——`fan_out` 必须只授予主线装配出来的工具。
- **`delegate` 的默认授予集是只读的 5 件套**（`agent/tools/subagent_tools.py` 的
  `_DEFAULT_SUBAGENT_TOOLS`），写文件/Shell 刻意不在内。`fan_out` 应按主线档案决定授予集，
  并**默认不授予 `govern` 平面的工具**。

## 具体要求

### 1. 工具签名
`fan_out(tasks, max_concurrency=4, ...)`，其中 `tasks` 是数组，每项包含：

```json
{
  "line": "engineering",          // 可选，主线 id；缺省用全局激活主线或只读默认集
  "goal": "…",                    // 必填
  "constraints": ["…"],           // 必填且非空
  "prior_artifacts": [],          // 必填，可为空列表
  "prohibitions": [],             // 必填，可为空列表
  "artifact_format": "…",         // 必填
  "budget_tokens": 20000,         // 必填且 >0
  "timeout_seconds": 600,         // 必填且 >0
  "callback_url": "internal://fan_out"  // 必填非空
}
```

### 2. 行为
1. **先全校验、后副作用**：逐个任务的八要素用 `element_problems` 校验；
   **任一任务不合格就整体拒绝**并返回哪个任务缺哪一项（不要在部分校验通过后就启动）。
2. **按主线装配授予集**：对每个任务，取其 `line` 的 `LineProfile`，
   用 `assemble()` 得到该线的工具集，再与 `SubAgentToolset.build` 取交集得到该子代理**真正可见**的工具。
   把结果写进 `authorized_capabilities`。
   - 主线不存在/已停用 ⇒ 该任务失败并说明原因，**不要**回退成"给全量工具"。
   - **绝不授予 `govern` 平面工具**（`ext_install`/`generate_tool`/`connect_mcp` 等），
     除非该主线显式 `allow_govern: true`——即便如此也要在返回里标注 `needs_approval`。
3. **并发生效**：调用 `execute_many(contexts, max_concurrency=...)`；
   尊重 `DEFAULT_MAX_CONCURRENCY` 上限，`max_concurrency` 参数要 clamp 到合法区间。
4. **逐任务结果**：返回
   `{ok, total, succeeded, failed, results: [{index, line, status, summary, outcome, error_code, tools_granted}]}`
   —— 单个子任务失败**不得**让整个 `fan_out` 失败（部分成功要如实报告）。
5. **预算与超时**：把每个任务的 `budget_tokens` / `timeout_seconds` 传给对应 ctx；
   汇总时给出总预算消耗。
6. **能力不足时明确报错**：若 `dl._subagent_mgr` 为空或没有 LLM/外部 CLI 通道，
   返回可读错误（照 `_run_delegate` 的错误口径），不要跑注定失败的空执行。

### 3. 治理声明（必做）
1. 建 `data/tool_definitions/fan_out.yaml`，字段顺序照 `grep.yaml`：
   `name / category / description / deprecated / version / plane / effect / risk / tags / schema / examples`
   - `plane: act`，`effect: execute`，`risk: high`（它会起多个并发子代理，成本与副作用都放大）
   - `category: async`
2. 把 `fan_out` 加进 `agent/tool_router.py` 的 `_DEFAULT_TOOL_CATEGORIES["async"]["tools"]`
   （**漏了这步它在关键词路由下不可达**）。
3. 跑 `python scripts/backfill_tool_planes.py --check` 必须是 OK。

### 4. 接线
在 `agent/orchestrator/lifecycle_manager.py` 的 `_register_builtin_tools()` 中注册新模块，
照现有 `reg_extra(self)` 那几行的写法。

### 5. 测试（必做）
新建 `tests/unit/test_fan_out.py`，至少覆盖：
- 八要素缺一项 ⇒ 整体拒绝，错误里点名是第几个任务缺什么
- 正常路径 ⇒ 每个子任务拿到的是**其主线装配出的工具集**（用 mock 的 executor 断言 `authorized_capabilities`）
- 主线不存在 ⇒ 该任务失败且**没有**退化成全量授权
- `govern` 平面工具默认不出现在任何子任务的授权集里
- 单个子任务抛异常 ⇒ `fan_out` 整体仍返回成功信封，`failed=1`
- `max_concurrency` 越界被 clamp

## 约束
- **不要**修改 `agent/lines/*`（模型已定稿）。若发现真实缺陷，在报告里指出而不是改。
- **不要**修改 `agent/tools/__init__.py`、`agent/tools/persistence.py`、`agent/tool_router.py`
  的既有分类内容（只允许往 `async` 列表里加 `fan_out` 一项）。
- 不要删改任何既有工具。
- 所有面向用户的描述用中文。
- 不用 `shell=True`，不引入新依赖。
- 完成后跑：
  ```powershell
  python -m pytest tests/unit/test_fan_out.py tests/unit/test_agent_lines.py -q
  python scripts/backfill_tool_planes.py --check
  python -c "import ast;ast.parse(open('agent/tools/fan_out_tools.py',encoding='utf-8').read())"
  ```

## 交付
最终消息报告：新建/修改的文件；`fan_out` 的 plane/effect/risk；上面每条验证命令的**实际输出**；
以及任何"提示词里说的 API 与真实代码不符"的地方（如实说明，不要迁就提示词）。
