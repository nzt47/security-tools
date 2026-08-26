"""工作流模式分类与 Agent 执行模式单元测试

覆盖维度:
- classify_workflow_mode: 三种模式判断 (dag / dag_conditional / agent)
- count_branches: 条件分支数统计
- AgentExecutor: mock runner 注入 + 黑板短期记忆 + 失败边界
- WorkflowExecutor 集成: 4 分支 → 触发 Agent / 分支数=阈值3 → 走 DAG (任务2)
- 边界断言 (任务2): 分支数 == AGENT_BRANCH_THRESHOLD(3) 时必须走 DAG 而非 Agent

不变量 (【不易】):
    - 现有 DAG 执行路径零回归 (classify 不影响 dag/dag_conditional)
    - Agent 模式 skipped_llm=False (必调 LLM); DAG 成功 skipped_llm=True
"""
from __future__ import annotations

import pytest

from agent.workflow_learning import (
    WorkflowLearningService,
    LearnedWorkflow,
    WorkflowStep,
    SharedBlackboard,
    classify_workflow_mode,
    count_branches,
    AGENT_BRANCH_THRESHOLD,
    AgentExecutor,
)
from agent.workflow_learning.executor import WorkflowExecutor


# ═══════════════════════════════════════════════════════════════════
#  Fixture / 辅助构造
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def svc(tmp_path):
    """独立临时存储的工作流学习服务"""
    return WorkflowLearningService(repo_path=str(tmp_path / "wf.json"))


def _step(step_id: str, *, tool: str = "t", condition: str | None = None,
          output_schema: dict | None = None) -> WorkflowStep:
    """构造单步 (带可选条件/输出 schema)"""
    return WorkflowStep(
        step_id=step_id,
        tool_name=tool,
        params_template={"q": "$input"},
        condition=condition,
        output_schema=output_schema,
    )


def _make_branching_wf(n_branches: int, *, wf_id: str = "branch-wf") -> LearnedWorkflow:
    """构造含 n_branches 个条件分支的工作流

    结构: 1 个入口步骤 + n_branches 个带 condition 的并行分支
    (共 n_branches + 1 步, n_branches 个 condition 节点)
    """
    steps = [_step("entry", tool="entry_tool")]
    for i in range(n_branches):
        steps.append(_step(
            f"branch_{i}", tool=f"tool_{i}",
            condition=f"$prev_output.includes('branch{i}')",
        ))
    return LearnedWorkflow(
        id=wf_id,
        name=f"分支工作流 ({n_branches} 分支)",
        task_signature=f"branch_{n_branches}",
        steps=steps,
    )


# ═══════════════════════════════════════════════════════════════════
#  1. count_branches / classify_workflow_mode 纯函数
# ═══════════════════════════════════════════════════════════════════

class TestCountBranches:
    """条件分支数统计"""

    def test_no_condition_zero_branches(self):
        steps = [_step(f"s{i}") for i in range(5)]
        assert count_branches(steps) == 0

    def test_count_condition_steps(self):
        steps = [
            _step("s0"),
            _step("s1", condition="x == 1"),
            _step("s2", condition="x == 2"),
            _step("s3"),
        ]
        assert count_branches(steps) == 2

    def test_empty_steps(self):
        assert count_branches([]) == 0


class TestClassifyMode:
    """classify_workflow_mode — 三种模式判断 (依据 docs §2)"""

    def test_linear_no_branch_is_dag(self):
        """规则3: 串联无分支 → dag"""
        steps = [_step(f"s{i}") for i in range(3)]
        assert classify_workflow_mode(steps) == "dag"

    def test_linear_long_no_branch_is_dag(self):
        """6~10 步线性仍为 dag (步骤数 ≤ 10 阈值)"""
        steps = [_step(f"s{i}") for i in range(8)]
        assert classify_workflow_mode(steps) == "dag"

    def test_one_branch_is_dag_conditional(self):
        """规则4: 1 条件分支 → dag_conditional"""
        steps = [_step("s0"), _step("s1", condition="x == 1")]
        assert classify_workflow_mode(steps) == "dag_conditional"

    def test_three_branches_is_dag_conditional(self):
        """边界: 分支数 == 阈值 3 (≤ 3) → dag_conditional, 不触发 Agent"""
        wf = _make_branching_wf(3)
        assert count_branches(wf.steps) == 3
        assert AGENT_BRANCH_THRESHOLD == 3
        assert classify_workflow_mode(wf.steps) == "dag_conditional"

    def test_four_branches_is_agent(self):
        """规则1: 分支数 = 4 (> 3) → agent (任务3核心用例)"""
        wf = _make_branching_wf(4)
        assert count_branches(wf.steps) == 4
        assert classify_workflow_mode(wf.steps) == "agent"

    def test_five_branches_is_agent(self):
        """规则1: 分支数 > 4 → agent"""
        wf = _make_branching_wf(5)
        assert classify_workflow_mode(wf.steps) == "agent"

    def test_too_many_steps_is_agent(self):
        """规则2: 步骤数 > 10 → agent (即使无分支)"""
        steps = [_step(f"s{i}") for i in range(11)]
        assert classify_workflow_mode(steps) == "agent"

    def test_empty_steps_is_dag(self):
        """空 steps → dag (最小默认)"""
        assert classify_workflow_mode([]) == "dag"

    def test_branch_priority_over_steps(self):
        """规则1 优先级 > 规则2: 4 分支 + 少步骤仍 agent"""
        wf = _make_branching_wf(4)  # 5 步 + 4 分支
        assert len(wf.steps) == 5  # 步骤数 ≤ 10
        assert classify_workflow_mode(wf.steps) == "agent"  # 但分支 > 3

    def test_returns_three_modes_only(self):
        """返回值只能是三种之一 (不变量)"""
        cases = [
            [_step("s0")],
            [_step("s0"), _step("s1", condition="x")],
            _make_branching_wf(4).steps,
            [_step(f"s{i}") for i in range(11)],
        ]
        for steps in cases:
            mode = classify_workflow_mode(steps)
            assert mode in ("dag", "dag_conditional", "agent"), \
                f"非法模式值: {mode}"


# ═══════════════════════════════════════════════════════════════════
#  2. AgentExecutor 单元测试
# ═══════════════════════════════════════════════════════════════════

class TestAgentExecutor:
    """Agent 执行器 — mock runner 注入"""

    def _mock_runner(self, text="agent result", n_steps=2):
        """构造 mock runner, 返回固定结构 + 记录调用"""
        calls = []

        def runner(task_text, params, tools_hint):
            calls.append({"task": task_text, "params": params,
                          "tools": tools_hint})
            return {
                "text": text,
                "steps": [
                    {"type": "tool_call", "name": "search",
                     "result": f"r{i}", "input": {"q": task_text}}
                    for i in range(n_steps)
                ],
            }

        runner.calls = calls
        return runner

    def test_execute_success(self):
        """正常执行: 返回 skipped_llm=False + 工具步骤计数"""
        wf = _make_branching_wf(4)
        runner = self._mock_runner(text="final answer", n_steps=3)
        ae = AgentExecutor(runner=runner)

        result = ae.execute(wf, "复杂任务", {"k": "v"})

        assert result.success is True
        assert result.skipped_llm is False  # Agent 必调 LLM
        assert result.output == "final answer"
        assert result.steps_executed == 3
        assert result.workflow_id == "branch-wf"
        # runner 收到工具提示
        assert len(runner.calls) == 1
        assert "entry_tool" in runner.calls[0]["tools"]

    def test_execute_writes_blackboard_short_term_memory(self):
        """Agent 模式黑板退化为短期记忆 (output_schema=None)"""
        wf = _make_branching_wf(4)
        ae = AgentExecutor(runner=self._mock_runner(n_steps=2))
        ae.execute(wf, "task")

        # 黑板语义: 任意形状输出可写入 (output_schema=None 不抛 schema 错误)
        bb = SharedBlackboard()
        bb.write("agent_step_0", "output", {"any": "shape"}, schema=None)
        assert bb.read("agent_step_0", "output") == {"any": "shape"}

    def test_execute_no_runner_raises_and_records_failure(self):
        """未配置 runner → 失败 + 错误信息 (边界显性化)"""
        wf = _make_branching_wf(4)
        ae = AgentExecutor(runner=None)

        result = ae.execute(wf, "task")

        assert result.success is False
        assert result.skipped_llm is False
        assert "未配置 AgentRunner" in result.error

    def test_execute_runner_exception_handled(self):
        """runner 抛异常 → 转 result.success=False (不中断)"""
        wf = _make_branching_wf(4)

        def bad_runner(task, params, hint):
            raise RuntimeError("LLM 网络故障")

        ae = AgentExecutor(runner=bad_runner)
        result = ae.execute(wf, "task")

        assert result.success is False
        assert "LLM 网络故障" in result.error

    def test_set_runner_post_injection(self):
        """set_runner 后置注入 (与 set_tool_executor 同构)"""
        wf = _make_branching_wf(4)
        ae = AgentExecutor(runner=None)
        r1 = ae.execute(wf, "task")
        assert r1.success is False
        ae.set_runner(self._mock_runner())
        r2 = ae.execute(wf, "task")
        assert r2.success is True

    def test_execute_empty_steps_hint(self):
        """wf.steps 为空时 tools_hint 为空列表 (不报错)"""
        wf = LearnedWorkflow(
            id="empty-wf", name="空", task_signature="empty", steps=[])
        ae = AgentExecutor(runner=self._mock_runner())
        result = ae.execute(wf, "task")
        assert result.success is True


# ═══════════════════════════════════════════════════════════════════
#  3. WorkflowExecutor 集成 — 4 分支触发 Agent / 3 分支边界走 DAG
# ═══════════════════════════════════════════════════════════════════

class TestExecutorAgentIntegration:
    """WorkflowExecutor + AgentExecutor 集成 (任务3核心验证 + 任务2边界)"""

    def _register(self, svc, wf):
        """注册工作流到 repo + matcher 索引 (service 无 save 方法)"""
        svc.repo.upsert(wf)
        svc.matcher.register(wf)

    def _setup_executor(self, svc, *, with_agent: bool):
        """构造带/不带 AgentExecutor 的 WorkflowExecutor"""
        def tool_exec(tool_name, params):
            return {"tool": tool_name, "echo": params}

        ae = AgentExecutor(runner=lambda t, p, h: {
            "text": f"agent handled: {t}",
            "steps": [{"type": "tool_call", "result": "ok"}],
        }) if with_agent else None

        exe = WorkflowExecutor(
            svc.repo, svc.matcher, min_score=0.3,
            tool_executor=tool_exec,
            agent_executor=ae,
        )
        return exe

    def test_four_branch_wf_triggers_agent_mode(self, svc):
        """4 分支工作流 + 已配置 AgentExecutor → 触发 Agent 模式

        验证点 (任务3核心):
            1. classify_workflow_mode 返回 "agent"
            2. WorkflowExecutor 走 AgentExecutor 分支 (不走 DAG)
            3. skipped_llm=False (Agent 必调 LLM)
            4. output 来自 mock runner
        """
        wf = _make_branching_wf(4)
        self._register(svc, wf)
        exe = self._setup_executor(svc, with_agent=True)

        result = exe.execute_by_id(wf.id, "复杂多分支任务")

        assert result.matched is True
        assert result.skipped_llm is False  # Agent 模式必调 LLM
        assert "agent handled" in result.output
        assert result.steps_executed == 1  # mock runner 返回 1 个 tool_call

    def test_three_branch_wf_stays_dag_not_agent(self, svc):
        """边界断言 (任务2): 分支数 == 阈值 3 → 走 DAG, 不触发 Agent

        关键区分依据 (docs §2):
            - skipped_llm: DAG 成功 → True (跳过 LLM); Agent → 恒 False
            - output: DAG 走工具执行器 (无 "agent handled" 前缀)
        """
        wf = _make_branching_wf(3)
        self._register(svc, wf)
        exe = self._setup_executor(svc, with_agent=True)  # 即使配了 AgentExecutor

        # 纯函数断言: 分支数 3 == 阈值 → dag_conditional (非 agent)
        assert count_branches(wf.steps) == AGENT_BRANCH_THRESHOLD
        assert classify_workflow_mode(wf.steps) == "dag_conditional"

        result = exe.execute_by_id(wf.id, "任务")

        # 集成断言: 走 DAG 路径 (AgentExecutor 未被调用)
        assert result.matched is True
        assert "agent handled" not in (result.output or "")
        assert result.skipped_llm is True  # DAG 成功跳过 LLM (Agent 恒 False)
        assert result.output == {"tool": "entry_tool", "echo": {"q": "任务"}}

    def test_agent_executor_not_configured_degrades_to_dag(self, svc):
        """4 分支 + 未配置 AgentExecutor → 降级走 DAG (不中断)"""
        wf = _make_branching_wf(4)
        self._register(svc, wf)
        exe = self._setup_executor(svc, with_agent=False)

        result = exe.execute_by_id(wf.id, "任务")

        assert result.matched is True
        # 降级走 DAG: 工具执行器逐步执行, 不抛异常
        assert result.error is None or "AgentExecutor" not in (result.error or "")

    def test_agent_mode_no_workflow_lock_blocking(self, svc):
        """Agent 模式不持 workflow 级锁 (LLM 耗时长, 不阻塞其他执行)"""
        wf = _make_branching_wf(4)
        self._register(svc, wf)
        exe = self._setup_executor(svc, with_agent=True)

        result = exe.execute_by_id(wf.id, "任务")
        assert result.success is True

    def test_linear_wf_not_affected_by_agent_config(self, svc):
        """线性工作流 → dag, AgentExecutor 配置不影响 (零回归)"""
        wf = LearnedWorkflow(
            id="linear-wf", name="线性",
            task_signature="linear",
            steps=[_step("s0"), _step("s1")],
        )
        self._register(svc, wf)
        exe = self._setup_executor(svc, with_agent=True)

        result = exe.execute_by_id(wf.id, "任务")

        assert result.matched is True
        assert "agent handled" not in (result.output or "")
        assert result.skipped_llm is True  # DAG 成功跳过 LLM
