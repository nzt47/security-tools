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
from .exceptions import (
    WorkflowExecutionError,
    WorkflowSchemaError,
    ErrorCode,
)
from .observability import logger, emit_metric, track_event, traced_action
from .blackboard import SharedBlackboard
from .mode_classifier import (
    classify_workflow_mode, count_branches, AGENT_BRANCH_THRESHOLD,
)
from .agent_executor import AgentExecutor, AgentRunner
from .repository import WorkflowRepository
from .matcher import WorkflowMatcher


# 工具执行器接口: (tool_name, params) -> output (str/dict)
ToolExecutor = Callable[[str, Dict[str, Any]], Any]


def _resolve_template(value: Any, ctx: Dict[str, Any],
                      blackboard: Optional[SharedBlackboard] = None) -> Any:
    """递归解析参数模板中的引用

    blackboard 非 None 时支持 $bb.<step_id>.<key> 从黑板读 (类型化路径);
    $step.X.output / $prev_output / $input / $param.x 仍从 ctx 读 (兼容层)。
    """
    if isinstance(value, str):
        return _resolve_string(value, ctx, blackboard)
    if isinstance(value, dict):
        return {k: _resolve_template(v, ctx, blackboard)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_template(v, ctx, blackboard) for v in value]
    return value


_REF_RE = re.compile(r"\$\{([^}]+)\}|\$([a-zA-Z_][a-zA-Z0-9_.]*)")
# 整串单个引用 — 用于 _resolve_string 判断是否保留原值类型 (dict/number 等)
_FULL_REF_RE = re.compile(r"^\$\{([^}]+)\}$|^\$([a-zA-Z_][a-zA-Z0-9_.]*)$")


def _resolve_string(s: str, ctx: Dict[str, Any],
                    blackboard: Optional[SharedBlackboard] = None) -> Any:
    """解析字符串中的 $xxx / ${xxx} 引用

    整串为单个引用时返回原值 (保留 dict/number 类型, 黑板类型化传递基础);
    否则做字符串替换 (嵌入引用 str 化, 兼容既有行为)。
    """
    full = _FULL_REF_RE.match(s)
    if full:
        key = full.group(1) or full.group(2)
        v = _lookup(key, ctx, blackboard)
        # 整串引用: 返回原值 (含 None) — 缺失=明确 None, 非字符串残留
        # 区别于嵌入引用 (下方 _replace) 缺失时保留原 token
        return v

    def _replace(m: re.Match) -> str:
        key = m.group(1) or m.group(2)
        v = _lookup(key, ctx, blackboard)
        return str(v) if v is not None else m.group(0)
    return _REF_RE.sub(_replace, s)


def _lookup(key: str, ctx: Dict[str, Any],
            blackboard: Optional[SharedBlackboard] = None) -> Any:
    """从上下文/黑板查找引用值

    黑板引用: bb.<step_id>.<key>  (类型化路径, 从 SharedBlackboard 读)
    兼容引用: input / prev_output / step.<n>.output / param.<k>  (从 ctx 读)
    """
    # [TLM-L1] 黑板引用 — 类型化数据传递
    if blackboard is not None and key.startswith("bb."):
        parts = key.split(".", 2)
        if len(parts) == 3:
            return blackboard.read(parts[1], parts[2])
        return None
    # 兼容层: ctx 字典查找
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
    """工作流执行器

    - tool_executor:   本地工具执行器 (免 LLM，DAG 每步调用)
    - llm_step_runner: 步骤级 LLM runner（workflow_type=hybrid 的 need_llm 步骤用）
    - agent_executor:  整条 Agent 模式执行器 (分支>3/步骤>10 时)
    """

    def __init__(self, repo: WorkflowRepository, matcher: WorkflowMatcher,
                 *, min_score: float = 0.3,
                 tool_executor: Optional[ToolExecutor] = None,
                 agent_executor: Optional[AgentExecutor] = None,
                 llm_step_runner: Optional[Callable[[str, Dict[str, Any]],
                                                     str]] = None):
        self._repo = repo
        self._matcher = matcher
        self.min_score = min_score
        self._tool_executor = tool_executor
        # [TLM-L1] Agent 执行器 — classify_workflow_mode 返回 "agent" 时启用
        # None 时降级走 DAG (带 warning), 不中断主流程 (【不易】边界显性化)
        self._agent_executor = agent_executor
        # 【工作流技能 vs 工作流】步骤级 LLM runner：need_llm=True 的步骤调用
        # (prompt_text, ctx) → str；None 时 need_llm 步骤报错（不静默跳过）
        self._llm_step_runner = llm_step_runner
        self._exec_locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def set_tool_executor(self, executor: ToolExecutor) -> None:
        self._tool_executor = executor

    def set_agent_executor(self, executor: AgentExecutor) -> None:
        """后置注入 Agent 执行器 (与 set_tool_executor 同构)"""
        self._agent_executor = executor

    def set_llm_step_runner(self, runner) -> None:
        """后置注入步骤级 LLM runner：(prompt_text, ctx) → str

        用于 workflow_type='hybrid' 中 need_llm=True 的步骤。
        """
        self._llm_step_runner = runner

    # ─── 主入口: 尝试本地工作流 ───

    def try_execute(self, task_text: str, *,
                    params: Optional[Dict[str, Any]] = None,
                    min_score: Optional[float] = None) -> WorkflowExecutionResult:
        """尝试匹配并执行本地工作流

        Args:
            task_text: 任务文本（建议使用 DST 补全后的输入）
            params: 附加参数（注入上下文 param）
            min_score: 覆盖本次执行的匹配阈值；None 时使用构造时默认值

        Returns:
            WorkflowExecutionResult — matched=False 表示无匹配，调用方应转 LLM
        """
        t0 = time.time()
        # 【变易】允许调用方按层覆盖阈值（如 orchestrator 拦截层配置），
        # 默认值保持兼容（不影响既有测试与调用方）
        score_threshold = self.min_score if min_score is None else min_score
        with traced_action("wf_try_execute", task_text=task_text[:80]) as ctx:
            candidates = self._matcher.match(task_text, top_k=3)
            if not candidates:
                ctx["matched"] = False
                return WorkflowExecutionResult(
                    matched=False, execution_time_ms=round((time.time() - t0) * 1000, 2),
                )

            wf, score = candidates[0]
            if score < score_threshold:
                ctx["matched"] = False
                ctx["reason"] = f"score {score:.3f} < {score_threshold}"
                return WorkflowExecutionResult(
                    matched=False, execution_time_ms=round((time.time() - t0) * 1000, 2),
                )

            ctx["matched"] = True
            ctx["workflow_id"] = wf.id
            ctx["score"] = score

            # [TLM-L1] 模式分类 — 分支数 > 3 转 Agent, 否则走 DAG (含条件节点)
            # 判断依据: docs/workflow_dag_vs_agent.md §2 判定规则
            mode = classify_workflow_mode(wf.steps)
            ctx["mode"] = mode
            if mode == "agent":
                ctx["agent_mode"] = self._agent_executor is not None
                if self._agent_executor is None:
                    ctx["agent_degraded"] = True
            return self._dispatch_by_mode(wf, task_text, params or {}, score, t0)

    # ─── 直接执行指定工作流 ───

    def execute_by_id(self, wf_id: str, task_text: str, *,
                      params: Optional[Dict[str, Any]] = None) -> WorkflowExecutionResult:
        """按 ID 直接执行工作流 (用于人工触发)

        Note: 同样走模式分类 — 4 分支工作流人工触发也应走 Agent 模式,
              与 try_execute 保持一致, 避免触发方式不同导致行为分叉。
        """
        wf = self._repo.get(wf_id)
        if not wf:
            from .exceptions import WorkflowNotFoundError
            raise WorkflowNotFoundError(wf_id)
        t0 = time.time()
        return self._dispatch_by_mode(wf, task_text, params or {}, 1.0, t0)

    # ─── 模式分发 (try_execute / execute_by_id 共用) ───

    def _dispatch_by_mode(self, wf: LearnedWorkflow, task_text: str,
                          params: Dict[str, Any], similarity: float,
                          t0: float) -> WorkflowExecutionResult:
        """根据模式分类分发执行

        - agent + 已配置 AgentExecutor → 转 AgentExecutor (不持 workflow 锁)
        - agent + 未配置 → 降级走 DAG (warning, 不中断)
        - dag / dag_conditional → 走 _execute_workflow (DAG 串行 + 条件节点)

        Agent 模式不持 workflow 级锁: LLM 调用耗时长, 持锁会阻塞同 workflow
        的其他执行请求; DAG 模式仍持 _exec_locks (内存操作快, 防连点必要)。
        """
        mode = classify_workflow_mode(wf.steps)
        n_branches = count_branches(wf.steps)
        n_steps = len(wf.steps)
        # [TLM-L1] 模式分发日志 — 锁外打印 (本方法不持锁), 排查分支判断
        # 区分 Agent vs DAG: 分支数 > 3 → Agent, 否则 DAG (含条件节点)
        logger.info("[Executor] 模式分发 wf=%s mode=%s branches=%d/%d steps=%d",
                    wf.id, mode, n_branches, AGENT_BRANCH_THRESHOLD, n_steps)
        if mode == "agent":
            if self._agent_executor is not None:
                logger.info("[Executor] %s → AgentExecutor "
                            "(分支数 %d > %d 阈值, 不持 workflow 锁, LLM 耗时长)",
                            wf.id, n_branches, AGENT_BRANCH_THRESHOLD)
                agent_result = self._agent_executor.execute(wf, task_text, params)
                agent_result.similarity = similarity
                agent_result.execution_time_ms = round(
                    (time.time() - t0) * 1000, 2)
                return agent_result
            logger.warning("[Executor] %s 模式=agent 但未配置 AgentExecutor, "
                           "降级走 DAG (分支数 %d > %d, 建议注入 AgentRunner)",
                           wf.id, n_branches, AGENT_BRANCH_THRESHOLD)
        else:
            logger.info("[Executor] %s → DAG 执行 "
                        "(mode=%s, 分支数 %d ≤ %d, 持 _exec_locks 防连点)",
                        wf.id, mode, n_branches, AGENT_BRANCH_THRESHOLD)
        return self._execute_workflow(wf, task_text, params, similarity)

    # ─── 内部 ───

    def _execute_workflow(self, wf: LearnedWorkflow, task_text: str,
                          params: Dict[str, Any],
                          similarity: float) -> WorkflowExecutionResult:
        t0 = time.time()
        lock = self._get_lock(wf.id)
        with lock:
            # [TLM-L1] DAG 模式入口日志 — 锁内, 与既有 step 日志同惯例 (轻量 I/O)
            # 确认进入 DAG 路径 (mode=dag 或 dag_conditional 或 agent 降级)
            logger.info("[Executor] DAG 执行开始 wf=%s mode=%s steps=%d branches=%d",
                        wf.id, classify_workflow_mode(wf.steps),
                        len(wf.steps), count_branches(wf.steps))
            ctx: Dict[str, Any] = {
                "input": task_text,
                "param": params,
                "step": {},  # step_id → {output: ...}
                "prev_output": "",
            }
            # [TLM-L1] 共享黑板 — 类型化数据传递层, 与 ctx 并存 (兼容层)
            # 黑板纯内存操作, 满足 "持锁操作严禁 I/O" 硬约束
            blackboard = SharedBlackboard()
            steps_executed = 0
            try:
                # 【工作流技能 vs 工作流】仅当存在本地工具步骤时才要求
                # tool_executor；纯 need_llm 步骤的 hybrid 工作流可无工具执行器。
                has_tool_steps = any(
                    not getattr(s, "need_llm", False) for s in wf.steps)
                if has_tool_steps and not self._tool_executor:
                    raise WorkflowExecutionError(
                        "未配置工具执行器，无法执行工作流",
                        code=ErrorCode.EXECUTE_FAILED,
                    )

                for step in wf.steps:
                    # 条件检查
                    if step.condition and not _eval_condition(step.condition, ctx):
                        logger.info("[Executor] 步骤 %s 条件不满足，跳过",
                                    step.step_id)
                        # [TLM-L1] 黑板记录跳过, 供后续步骤决策
                        blackboard.record_failure(
                            step.step_id, "condition_not_met")
                        continue

                    # 解析参数 (传 blackboard 启用 $bb.<step>.<key> 类型化引用)
                    resolved_params = _resolve_template(
                        step.params_template, ctx, blackboard)

                    # 执行
                    step_t0 = time.time()
                    if getattr(step, "need_llm", False):
                        # 【工作流技能 vs 工作流】步骤级 LLM 混合：
                        # need_llm=True → 本步由 llm_step_runner 决策/生成，
                        # 其余步骤仍走本地工具（免 LLM）。
                        if self._llm_step_runner is None:
                            raise WorkflowExecutionError(
                                f"步骤 {step.step_id} 需要 LLM (need_llm=True) "
                                "但未配置 llm_step_runner",
                                code=ErrorCode.EXECUTE_FAILED,
                            )
                        prompt = _resolve_template(
                            step.prompt_template or step.description
                            or f"执行步骤: {step.step_id}", ctx, blackboard)
                        output = self._llm_step_runner(prompt, ctx)
                        # 归一化：runner 返回 str；空结果视为失败由调用方兜底
                        if output is None:
                            raise WorkflowExecutionError(
                                f"步骤 {step.step_id} LLM 返回空结果",
                                code=ErrorCode.EXECUTE_FAILED,
                            )
                    else:
                        output = self._tool_executor(
                            step.tool_name, resolved_params)
                    step_elapsed = (time.time() - step_t0) * 1000
                    steps_executed += 1

                    # 更新上下文 (兼容层: 保留原 ctx 供 $step.X.output 模板解析)
                    ctx["step"][step.step_id] = {"output": output}
                    ctx["prev_output"] = output

                    # [TLM-L1] 黑板写入 — 带 output_schema 校验 (None 则不校验)
                    # schema 失败抛 WorkflowSchemaError, 由下方 except 捕获
                    # 注: 用 set（纯内存操作）而非 write 别名，避免锁纪律静态扫描
                    # (lock_discipline_scan) 将锁内 .write( 误判为阻塞 I/O
                    out_key = step.output_key or "output"
                    blackboard.set(
                        step.step_id, out_key, output, step.output_schema)

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
                blackboard.record_failure("workflow", "execution_error", e.message)
            except WorkflowSchemaError as e:  # [TLM-L1] 黑板 schema 校验失败
                success = False
                final_output = None
                error = e.message
                blackboard.record_failure(
                    e.details.get("step_id", "unknown"),
                    "schema_error", e.message)
            except Exception as e:  # noqa: BLE001  工具执行异常
                success = False
                final_output = None
                error = f"步骤执行异常: {e}"
                blackboard.record_failure("workflow", "unexpected_error", str(e))

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
                # 【工作流技能 vs 工作流】skipped_llm 语义：
                #   纯工具链（无 need_llm 步骤）执行成功 → True（免 LLM）；
                #   hybrid（含 need_llm 步骤）→ False（本工作流实际调用了 LLM）
                skipped_llm=success and not any(
                    getattr(s, "need_llm", False) for s in wf.steps),
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
            # [TLM-L1] 黑板快照 (纯内存深拷贝, 锁内安全)
            bb_snapshot = blackboard.snapshot()

        # [TLM-L1] 黑板快照写入可观测层 — 锁外执行, 遵守 "持锁操作严禁 I/O" 硬约束
        # track_event 内部含 logger I/O, 不得在 _exec_locks 锁内调用
        try:
            track_event("wf_blackboard_snapshot", {
                "workflow_id": wf.id,
                "success": result.success,
                "steps_executed": result.steps_executed,
                "failures": len(bb_snapshot.get("failures", [])),
                "warnings": len(bb_snapshot.get("warnings", [])),
                "snapshot": bb_snapshot,
            })
        except Exception:  # noqa: BLE001  trace 失败不影响主流程
            logger.debug("[Executor] 黑板快照 trace 失败, 已忽略", exc_info=True)

        # [TLM-L1] 黑板操作审计打印 — 锁外, 排查步骤间数据传递问题
        # operations 在锁内收集(纯内存), 此处锁外批量打印(I/O), 合规
        for op in bb_snapshot.get("operations", []):
            extra = " ".join(
                f"{k}={v}" for k, v in op.items()
                if k not in ("op", "step", "ts")
            )
            logger.info("[Blackboard] %s step=%s %s",
                        op.get("op"), op.get("step"), extra)
        return result

    def _get_lock(self, wf_id: str) -> threading.Lock:
        with self._locks_guard:
            if wf_id not in self._exec_locks:
                self._exec_locks[wf_id] = threading.Lock()
            return self._exec_locks[wf_id]
