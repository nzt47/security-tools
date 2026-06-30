"""工作流执行器 — 优先执行本地工作流，避免冗余 LLM 调用

执行流程:
    1. matcher.match(task) → 候选列表
    2. 取最高分候选，若分数 >= 阈值，执行
    3. 逐步执行 WorkflowStep:
        a. 解析参数模板 ($input / $prev_output / $step.<n>.output / $param.<k>)
        b. 检查 condition (若存在)
        c. 调用工具执行器 (ToolExecutor 回调)
        d. 失败则中断
    4. 记录结果到工作流统计 (success/failure)
    5. 返回 WorkflowExecutionResult

设计:
    - 边界显性化: 工具执行失败、超时、条件不满足均抛 WorkflowExecutionError
    - 后端权威原则: 执行结果既写入 workflow 统计，也返回给调用方
    - 防连点: 同一 workflow 并发执行加锁
"""

from __future__ import annotations
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .models import (
    LearnedWorkflow,
    WorkflowStep,
    WorkflowExecutionResult,
)
from .exceptions import WorkflowExecutionError, ErrorCode
from .observability import logger, emit_metric, track_event, traced_action
from .repository import WorkflowRepository
from .matcher import WorkflowMatcher


# 工具执行器接口: (tool_name, params) -> output (str/dict)
ToolExecutor = Callable[[str, Dict[str, Any]], Any]


def _resolve_template(value: Any, ctx: Dict[str, Any]) -> Any:
    """递归解析参数模板中的引用"""
    if isinstance(value, str):
        return _resolve_string(value, ctx)
    if isinstance(value, dict):
        return {k: _resolve_template(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_template(v, ctx) for v in value]
    return value


_REF_RE = re.compile(r"\$\{([^}]+)\}|\$([a-z_][a-z0-9_.]*)")


def _resolve_string(s: str, ctx: Dict[str, Any]) -> Any:
    """解析字符串中的 $xxx / ${xxx} 引用"""
    def _replace(m: re.Match) -> str:
        key = m.group(1) or m.group(2)
        v = _lookup(key, ctx)
        return str(v) if v is not None else m.group(0)
    return _REF_RE.sub(_replace, s)


def _lookup(key: str, ctx: Dict[str, Any]) -> Any:
    """从上下文查找引用值"""
    parts = key.split(".")
    cur: Any = ctx
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            cur = getattr(cur, p, None)
        if cur is None:
            return None
    return cur


def _eval_condition(expr: str, ctx: Dict[str, Any]) -> bool:
    """简化版条件求值

    支持的表达式样例:
        $prev_output.includes("yes")
        len($input) > 10
        $step.1.output.success == true
    限制:
        - 仅支持单个比较/方法调用
        - 出于安全考虑不使用 eval，而是用正则匹配常见模式
    """
    expr = expr.strip()
    if not expr:
        return True
    # 简化实现: 把引用替换成实际值后，再尝试简单比较
    resolved = _resolve_string(expr, ctx)

    # 简单相等比较
    m = re.match(r"^(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)$", resolved)
    if m:
        left, op, right = m.group(1).strip(), m.group(2), m.group(3).strip()
        # 尝试去除引号
        if right.startswith('"') and right.endswith('"'):
            right = right[1:-1]
        if right.startswith("'") and right.endswith("'"):
            right = right[1:-1]
        try:
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
            if op == ">":
                return float(left) > float(right)
            if op == "<":
                return float(left) < float(right)
            if op == ">=":
                return float(left) >= float(right)
            if op == "<=":
                return float(left) <= float(right)
        except (ValueError, TypeError):
            return False

    # .includes(...) 调用
    m = re.match(r"^(.+?)\.includes\((.+)\)$", resolved)
    if m:
        haystack, needle = m.group(1), m.group(2).strip()
        if needle.startswith('"') and needle.endswith('"'):
            needle = needle[1:-1]
        return needle in haystack

    # 默认: 表达式存在但无法解析 → 不执行 (保守)
    logger.warning("[Executor] 无法解析条件表达式: %s", expr)
    return False


class WorkflowExecutor:
    """工作流执行器"""

    def __init__(self, repo: WorkflowRepository, matcher: WorkflowMatcher,
                 *, min_score: float = 0.3,
                 tool_executor: Optional[ToolExecutor] = None):
        self._repo = repo
        self._matcher = matcher
        self.min_score = min_score
        self._tool_executor = tool_executor
        self._exec_locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def set_tool_executor(self, executor: ToolExecutor) -> None:
        self._tool_executor = executor

    # ─── 主入口: 尝试本地工作流 ───

    def try_execute(self, task_text: str, *,
                    params: Optional[Dict[str, Any]] = None) -> WorkflowExecutionResult:
        """尝试匹配并执行本地工作流

        Returns:
            WorkflowExecutionResult — matched=False 表示无匹配，调用方应转 LLM
        """
        t0 = time.time()
        with traced_action("wf_try_execute", task_text=task_text[:80]) as ctx:
            candidates = self._matcher.match(task_text, top_k=3)
            if not candidates:
                ctx["matched"] = False
                return WorkflowExecutionResult(
                    matched=False, execution_time_ms=round((time.time() - t0) * 1000, 2),
                )

            wf, score = candidates[0]
            if score < self.min_score:
                ctx["matched"] = False
                ctx["reason"] = f"score {score:.3f} < {self.min_score}"
                return WorkflowExecutionResult(
                    matched=False, execution_time_ms=round((time.time() - t0) * 1000, 2),
                )

            ctx["matched"] = True
            ctx["workflow_id"] = wf.id
            ctx["score"] = score
            return self._execute_workflow(wf, task_text, params or {}, score)

    # ─── 直接执行指定工作流 ───

    def execute_by_id(self, wf_id: str, task_text: str, *,
                      params: Optional[Dict[str, Any]] = None) -> WorkflowExecutionResult:
        """按 ID 直接执行工作流 (用于人工触发)"""
        wf = self._repo.get(wf_id)
        if not wf:
            from .exceptions import WorkflowNotFoundError
            raise WorkflowNotFoundError(wf_id)
        return self._execute_workflow(wf, task_text, params or {}, similarity=1.0)

    # ─── 内部 ───

    def _execute_workflow(self, wf: LearnedWorkflow, task_text: str,
                          params: Dict[str, Any],
                          similarity: float) -> WorkflowExecutionResult:
        t0 = time.time()
        lock = self._get_lock(wf.id)
        with lock:
            ctx: Dict[str, Any] = {
                "input": task_text,
                "param": params,
                "step": {},  # step_id → {output: ...}
                "prev_output": "",
            }
            steps_executed = 0
            try:
                if not self._tool_executor:
                    raise WorkflowExecutionError(
                        "未配置工具执行器，无法执行工作流",
                        code=ErrorCode.EXECUTE_FAILED,
                    )

                for step in wf.steps:
                    # 条件检查
                    if step.condition and not _eval_condition(step.condition, ctx):
                        logger.info("[Executor] 步骤 %s 条件不满足，跳过",
                                    step.step_id)
                        continue

                    # 解析参数
                    resolved_params = _resolve_template(
                        step.params_template, ctx)

                    # 执行
                    step_t0 = time.time()
                    output = self._tool_executor(step.tool_name, resolved_params)
                    step_elapsed = (time.time() - step_t0) * 1000
                    steps_executed += 1

                    # 更新上下文
                    ctx["step"][step.step_id] = {"output": output}
                    ctx["prev_output"] = output

                    logger.info("[Executor] %s.%s → %s (%.2fms)",
                                wf.id, step.step_id,
                                str(output)[:80], step_elapsed)

                # 全部成功
                success = True
                final_output = ctx["prev_output"]
                error = None
            except WorkflowExecutionError as e:
                success = False
                final_output = None
                error = e.message
            except Exception as e:  # noqa: BLE001  工具执行异常
                success = False
                final_output = None
                error = f"步骤执行异常: {e}"

            # 更新工作流统计
            wf.record_execution(success)
            self._repo.upsert(wf)
            self._matcher.register(wf)  # 更新索引中的 confidence

            elapsed = (time.time() - t0) * 1000
            result = WorkflowExecutionResult(
                matched=True,
                workflow_id=wf.id,
                workflow_name=wf.name,
                similarity=similarity,
                confidence=wf.confidence,
                output=final_output,
                steps_executed=steps_executed,
                success=success,
                skipped_llm=success,  # 成功则跳过 LLM
                execution_time_ms=round(elapsed, 2),
                error=error,
            )

            emit_metric(
                "yunshu_wf_execution_total",
                labels={"success": "true" if success else "failure",
                        "workflow_id": wf.id},
                kind="counter",
            )
            emit_metric(
                "yunshu_wf_execution_latency_ms",
                value=elapsed,
                labels={"success": "true" if success else "failure"},
                kind="histogram",
            )
            track_event("wf_executed", {
                "workflow_id": wf.id, "success": success,
                "skipped_llm": result.skipped_llm,
            })
            logger.info("[Executor] 工作流 %s 执行 %s (%d 步, %.2fms, 跳过LLM=%s)",
                        wf.id, "成功" if success else "失败",
                        steps_executed, elapsed, result.skipped_llm)
            return result

    def _get_lock(self, wf_id: str) -> threading.Lock:
        with self._locks_guard:
            if wf_id not in self._exec_locks:
                self._exec_locks[wf_id] = threading.Lock()
            return self._exec_locks[wf_id]
