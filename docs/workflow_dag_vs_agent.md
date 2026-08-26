# 工作流模式判断: DAG vs Agent

## 1. 为什么需要区分

`WorkflowLearningService` 对本地工作流有两种执行模式:

| 维度 | DAG 模式 | Agent 模式 |
|------|----------|------------|
| 执行方式 | 按步骤串行执行工具, `condition` 表达分支 | LLM + 工具循环 (chat_with_steps) |
| 分支表达 | `WorkflowStep.condition` (静态谓词) | LLM 运行时自由决策 |
| LLM 调用 | 不调 (成功时 `skipped_llm=True`) | 必调 (`skipped_llm=False`) |
| 锁 | 持 `_exec_locks` 防连点 | 不持 workflow 锁 (LLM 耗时长) |
| 黑板角色 | 类型化数据传递 ($bb.<step>.<key>) | 会话短期记忆 (output_schema=None) |
| 失败处理 | 中断 / 静态降级 | 重试 / 换工具 / 追问用户 |

## 2. 判定规则 (按优先级)

1. **分支数 > 3** → Agent
   - 超过 3 条分支时, 条件节点组合爆炸, DAG 失去可读性与可维护性

   **测试用例** (`tests/unit/test_workflow_mode.py::test_four_branch_wf_triggers_agent_mode`):

   构造 1 个入口步骤 + 4 个带 `condition` 的并行分支 (共 5 步, 4 个 condition 节点):

   ```python
   steps = [WorkflowStep(step_id="entry", tool_name="entry_tool")]
   for i in range(4):
       steps.append(WorkflowStep(
           step_id=f"branch_{i}", tool_name=f"tool_{i}",
           condition=f"$prev_output.includes('branch{i}')",
       ))
   wf = LearnedWorkflow(id="branch-wf", steps=steps, ...)
   ```

   验证点:
   - `count_branches(wf.steps)` 返回 `4` (4 个 condition 节点)
   - `classify_workflow_mode(wf.steps)` 返回 `"agent"` (分支数 4 > 3 阈值)
   - `WorkflowExecutor.execute_by_id(wf.id, ...)` 走 `_dispatch_by_mode` 的 Agent 分支, 调用 `AgentExecutor.execute`
   - 返回 `WorkflowExecutionResult(skipped_llm=False)` (Agent 模式必调 LLM, 与 DAG 本质区别)

   执行日志 (实测, `scripts/demo_agent_mode_4branch.py`):
   ```
   [1] 工作流 branch-wf: 5 步 / 4 条件分支
       classify_workflow_mode → 'agent' (阈值 3)
   [2] 真实 LLM 探测: [X] 不可用 (LLM_API_KEY 是占位符 'sk-real-key')
       → 降级 mock runner (调用点仍可观测)
   [3] 执行 execute_by_id(branch-wf, ...)
       success        = True
       skipped_llm    = False  (False = Agent 必调 LLM)
       steps_executed = 1
   [4] LLM 调用点证据: runner 被调用 1 次
   ```

   真调 LLM 验证 (`--force-real`): 注入真实 `ToolCallingService` 后 LLM 调用链真实触发,
   本地占位 key 被 DeepSeek 服务端拒绝 (HTTP 401 `Authentication Fails`), 失败被
   `AgentExecutor` 边界捕获 (`success=False`, 不中断主流程)。填入真实 key 后即可成功。

   对比: 3 分支工作流 (`_make_branching_wf(3)`) → `classify_workflow_mode` 返回
   `"dag_conditional"`, 走 DAG + 条件节点路径 (不触发 Agent)。

2. **需运行时动态决策** (如 "根据搜索结果决定是否调用总结工具") → Agent
   - 决策依据是上游工具的非结构化输出, 无法在编译期枚举

3. **串联 ≤ 5 步 且 无条件分支** → DAG
   - 线性流水线, 黑板数据流清晰, 无需 LLM

## 3. 实现位置

- `agent/workflow_learning/mode_classifier.py` — `classify_workflow_mode` / `count_branches`
  (纯函数, 无副作用, 可在锁内安全调用)
- `agent/workflow_learning/executor.py` — `_dispatch_by_mode` (try_execute / execute_by_id 共用)
- `agent/workflow_learning/agent_executor.py` — `AgentExecutor` (runner 注入解耦 LLM 调用栈)
- `scripts/demo_agent_mode_4branch.py` — 4 分支用例手动调试脚本

## 4. 边界情况

- 分支数 == 阈值 3: 走 `dag_conditional` (DAG + 条件节点), **不触发 Agent**
- 步骤数 > 10 (即使无分支): Agent
- 4 分支 + 未配置 AgentExecutor: 降级走 DAG (warning, 不中断)
- 空 steps: dag (最小默认)

## 5. 决策流程

```
classify_workflow_mode(steps)
  ├─ branches > 3 ────────────→ "agent"
  ├─ steps > 10 ──────────────→ "agent"
  ├─ branches == 0 ───────────→ "dag"
  └─ 其他 (branches 1~3) ─────→ "dag_conditional"
```
