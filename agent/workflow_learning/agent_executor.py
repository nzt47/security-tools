"""Agent 执行器 — 自由 Agent 模式 (LLM + 工具循环)

触发条件: classify_workflow_mode 返回 "agent"
    (分支数 > 3 或步骤数 > 10, DAG 失去可维护性)

设计 (【变易】按需演进 + 【简易】最小充分解):
    - 不重新实现 LLM 调用栈, 通过注入 AgentRunner 回调解耦:
        * 生产环境注入 ToolCallingService.chat_with_steps (agent/tool_calling.py)
        * 测试环境注入 mock runner (避免真实 LLM 调用)
    - 黑板角色 (per docs/workflow_dag_vs_agent.md §3 Agent 模式):
        * 会话短期记忆: 每轮工具调用输入/输出写入黑板
        * output_schema=None (Agent 输出形态开放, 不强校验)
        * snapshot 作为 LLM 上下文压缩与 trace 依据
        * record_failure 用于 Agent 反思与重试

不变量 (【不易】):
    - WorkflowExecutionResult 契约与 WorkflowExecutor 一致 (skipped_llm=False)
    - 不修改 LearnedWorkflow 自身状态 (wf.record_execution 由调用方负责)
    - runner 失败不中断主流程, 转 result.success=False (边界显性化)
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .blackboard import SharedBlackboard
from .models import LearnedWorkflow, WorkflowExecutionResult
from .observability import logger, emit_metric, track_event


# Agent 运行器接口 (解耦 LLM 调用栈)
#   入参: (task_text, params, tools_hint) → 工具提示来自 wf.steps
#   出参: {"text": str, "steps": [{"type": str, ...}]}
#   与 ToolCallingService.chat_with_steps 返回结构对齐
AgentRunner = Callable[[str, Dict[str, Any], List[str]], Dict[str, Any]]


class AgentExecutor:
    """自由 Agent 执行器 — 复用外部 LLM + 工具循环

    用法:
        # 生产环境
        from agent.tool_calling import ToolCallingService
        tc = ToolCallingService(llm_service=...)
        agent_exec = AgentExecutor(runner=lambda t, p, h: tc.chat_with_steps(
            [{"role": "user", "content": t}], tools_whitelist=h or None
        ))

        # 测试环境
        agent_exec = AgentExecutor(runner=lambda t, p, h: {
            "text": "mock", "steps": [{"type": "tool_call", "result": "ok"}]
        })
    """

    def __init__(self, runner: Optional[AgentRunner] = None) -> None:
        # runner 可后置注入 (set_runner), 兼容依赖初始化顺序
        self._runner = runner

    def set_runner(self, runner: AgentRunner) -> None:
        """后置注入 Agent 运行器 (与 WorkflowExecutor.set_tool_executor 同构)"""
        self._runner = runner

    def execute(self, wf: LearnedWorkflow, task_text: str,
                params: Optional[Dict[str, Any]] = None) -> WorkflowExecutionResult:
        """执行 Agent 模式工作流

        Args:
            wf:        LearnedWorkflow (steps 作为工具提示给 LLM, 不执行)
            task_text: 用户原始任务
            params:    调用方传入参数

        Returns:
            WorkflowExecutionResult — skipped_llm 始终 False
            (Agent 模式必调 LLM, 这是与 DAG 模式的本质区别)
        """
        t0 = time.time()
        params = params or {}
        # [TLM-L1] 黑板退化为会话短期记忆 — output_schema=None (文档 §3 反模式)
        blackboard = SharedBlackboard()
        # 工具提示: 从 wf.steps 提取工具名, 供 LLM 工具白名单
        tools_hint = [s.tool_name for s in wf.steps if s.tool_name]

        success = True
        output: Any = None
        error: Optional[str] = None
        steps_executed = 0

        try:
            if self._runner is None:
                raise RuntimeError(
                    "未配置 AgentRunner, 无法执行 Agent 模式 "
                    "(请通过 set_runner 注入 ToolCallingService.chat_with_steps)")
            # [TLM-L1] 调用 LLM + 工具循环 — I/O 在锁外 (本类不加锁, 由调用方保护)
            result = self._runner(task_text, params, tools_hint)
            output = result.get("text")
            agent_steps = result.get("steps", []) or []
            steps_executed = len(agent_steps)
            # 工具调用步骤写入黑板 (短期记忆, 无 schema 强校验)
            for i, step in enumerate(agent_steps):
                if isinstance(step, dict) and step.get("type") == "tool_call":
                    blackboard.write(
                        f"agent_step_{i}", "output",
                        step.get("result"), schema=None,
                    )
        except Exception as e:  # noqa: BLE001  Agent 执行异常边界
            success = False
            error = f"Agent 执行失败: {e}"
            blackboard.record_failure("agent", "execution_error", str(e))

        # [TLM-L1] 黑板快照 — 纯内存深拷贝, 可在任意上下文调用
        bb_snapshot = blackboard.snapshot()

        elapsed = (time.time() - t0) * 1000
        result = WorkflowExecutionResult(
            matched=True,
            workflow_id=wf.id,
            workflow_name=wf.name,
            similarity=1.0,
            confidence=wf.confidence,
            output=output,
            steps_executed=steps_executed,
            success=success,
            skipped_llm=False,  # Agent 模式必调 LLM (与 DAG 本质区别)
            execution_time_ms=round(elapsed, 2),
            error=error,
        )

        # 可观测层 (锁外, 本类不加锁; 调用方在 executor 中亦锁外调用)
        emit_metric(
            "yunshu_wf_agent_execution_total",
            labels={"success": "true" if success else "failure",
                    "workflow_id": wf.id},
            kind="counter",
        )
        try:
            track_event("wf_agent_executed", {
                "workflow_id": wf.id,
                "success": success,
                "steps_executed": steps_executed,
                "tools_hint": tools_hint,
                "failures": len(bb_snapshot.get("failures", [])),
                "snapshot": bb_snapshot,
            })
        except Exception:  # noqa: BLE001  trace 失败不影响主流程
            logger.debug("[AgentExecutor] 黑板快照 trace 失败, 已忽略", exc_info=True)

        logger.info("[AgentExecutor] %s Agent 执行 %s (%d 步, %.2fms, 工具=%s)",
                    wf.id, "成功" if success else "失败",
                    steps_executed, elapsed, tools_hint)
        return result
