"""工作流技能(toolchain) vs 工作流(hybrid) 分层测试。

覆盖（A/B/C 三方向）：
    A. 资产类型声明：toolchain 禁 need_llm 步骤；hybrid 放行；默认 toolchain 兼容
    B. 步骤级 LLM 混合执行：need_llm 步骤走 llm_step_runner、其余走本地工具；
       skipped_llm 语义按资产类型如实报告
    C. need_llm 步骤未配 runner 时报错（不静默）；纯工具链无需 runner
"""

import pathlib

import pytest

from agent.workflow_learning.executor import WorkflowExecutor
from agent.workflow_learning.generator import WorkflowGenerator
from agent.workflow_learning.matcher import WorkflowMatcher
from agent.workflow_learning.models import LearnedWorkflow, WorkflowStep
from agent.workflow_learning.repository import WorkflowRepository


# ═══════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def repo(tmp_path):
    return WorkflowRepository(path=str(tmp_path / "wf.json"))


@pytest.fixture
def matcher():
    return WorkflowMatcher()


def _store(repo, matcher, wf):
    WorkflowGenerator(repo, matcher).generate_and_store(wf)
    return wf


def _toolchain_wf(**kw):
    base = dict(
        id="tc-1", name="纯工具链", task_signature="文件|统计",
        workflow_type="toolchain",
        steps=[WorkflowStep(step_id="s1", tool_name="read_file",
                            params_template={"path": "${input}"})],
    )
    base.update(kw)
    return LearnedWorkflow(**base)


def _hybrid_wf(**kw):
    base = dict(
        id="hy-1", name="混合工作流", task_signature="文件|判断",
        workflow_type="hybrid",
        steps=[
            WorkflowStep(step_id="decide", tool_name="", need_llm=True,
                         prompt_template="判断路径: ${input}"),
            WorkflowStep(step_id="read", tool_name="read_file",
                         params_template={"path": "${prev_output}"}),
        ],
    )
    base.update(kw)
    return LearnedWorkflow(**base)


# ═══════════════════════════════════════════════════════════════
#  A. 资产类型声明与校验
# ═══════════════════════════════════════════════════════════════

class TestWorkflowTypeDeclaration:
    def test_toolchain_rejects_llm_step(self):
        with pytest.raises(ValueError):
            LearnedWorkflow(
                id="bad", name="x", task_signature="x",
                workflow_type="toolchain",
                steps=[WorkflowStep(step_id="s1", tool_name="",
                                    need_llm=True)])

    def test_hybrid_allows_llm_step(self):
        wf = _hybrid_wf()
        assert wf.workflow_type == "hybrid"
        assert wf.steps[0].need_llm is True

    def test_default_type_is_toolchain(self):
        """既有 workflow（未声明类型）默认 toolchain → 向后兼容"""
        wf = _toolchain_wf()
        assert wf.workflow_type == "toolchain"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValueError):
            LearnedWorkflow(id="x", name="x", task_signature="x",
                            workflow_type="bogus", steps=[])

    def test_toolchain_persists_and_reloads(self, repo, matcher):
        """toolchain workflow 可持久化并原样读回"""
        wf = _store(repo, matcher, _toolchain_wf())
        loaded = repo.get(wf.id)
        assert loaded.workflow_type == "toolchain"
        assert loaded.steps[0].need_llm is False


# ═══════════════════════════════════════════════════════════════
#  B. 步骤级 LLM 混合执行
# ═══════════════════════════════════════════════════════════════

class TestHybridExecution:
    def test_hybrid_mixed_steps(self, repo, matcher):
        """need_llm 步骤走 runner，工具步骤走 tool_executor"""
        wf = _store(repo, matcher, _hybrid_wf())
        tool_calls = []
        llm_prompts = []

        def tool_exec(tool_name, params):
            tool_calls.append((tool_name, params))
            return {"ok": True, "content": "文件内容"}

        def llm_runner(prompt, ctx):
            llm_prompts.append(prompt)
            return "/tmp/target.txt"

        ex = WorkflowExecutor(repo, matcher, tool_executor=tool_exec,
                              llm_step_runner=llm_runner)
        res = ex.execute_by_id(wf.id, "处理文件", params={"x": 1})
        assert res.success is True
        assert res.steps_executed == 2
        assert len(llm_prompts) == 1          # 1 次 LLM 决策
        assert len(tool_calls) == 1           # 1 次本地工具
        assert tool_calls[0][1]["path"] == "/tmp/target.txt"  # 数据流转
        # 含 LLM 步骤 → skipped_llm=False（如实报告调用了 LLM）
        assert res.skipped_llm is False

    def test_toolchain_skipped_llm_true(self, repo, matcher):
        """纯工具链执行成功 → skipped_llm=True（免 LLM 0-Token）"""
        wf = _store(repo, matcher, _toolchain_wf())

        def tool_exec(tool_name, params):
            return {"ok": True}

        ex = WorkflowExecutor(repo, matcher, tool_executor=tool_exec)
        res = ex.execute_by_id(wf.id, "读文件")
        assert res.success is True
        assert res.skipped_llm is True

    def test_hybrid_llm_step_output_flows_to_tool(self, repo, matcher):
        """LLM 步骤输出 → prev_output → 工具步骤参数引用"""
        wf = _hybrid_wf()
        wf.steps[0].prompt_template = "只输出文件路径"
        _store(repo, matcher, wf)

        seen_paths = []

        def tool_exec(tool_name, params):
            seen_paths.append(params.get("path"))
            return {"ok": True}

        def llm_runner(prompt, ctx):
            return "/data/a.txt"

        ex = WorkflowExecutor(repo, matcher, tool_executor=tool_exec,
                              llm_step_runner=llm_runner)
        ex.execute_by_id(wf.id, "帮我读文件")
        assert seen_paths == ["/data/a.txt"]


# ═══════════════════════════════════════════════════════════════
#  C. runner 缺失与边界
# ═══════════════════════════════════════════════════════════════

class TestRunnerMissing:
    def test_hybrid_need_llm_without_runner_errors(self, repo, matcher):
        """hybrid 含 need_llm 但未配 runner → 执行失败（不静默跳过）"""
        wf = _store(repo, matcher, _hybrid_wf())

        def tool_exec(tool_name, params):
            return {"ok": True}

        ex = WorkflowExecutor(repo, matcher, tool_executor=tool_exec)
        res = ex.execute_by_id(wf.id, "处理")
        assert res.success is False
        assert "llm_step_runner" in (res.error or "")

    def test_toolchain_without_tool_executor_errors(self, repo, matcher):
        """toolchain 有工具步骤但未配 tool_executor → 失败"""
        wf = _store(repo, matcher, _toolchain_wf())
        ex = WorkflowExecutor(repo, matcher)
        res = ex.execute_by_id(wf.id, "读")
        assert res.success is False

    def test_pure_llm_workflow_needs_no_tool_executor(self, repo, matcher):
        """纯 need_llm 步骤的 hybrid 无需 tool_executor"""
        wf = LearnedWorkflow(
            id="pure-llm", name="纯LLM", task_signature="x",
            workflow_type="hybrid",
            steps=[WorkflowStep(step_id="s1", tool_name="",
                                need_llm=True, prompt_template="想: ${input}")],
        )
        _store(repo, matcher, wf)
        llm_calls = []

        def llm_runner(prompt, ctx):
            llm_calls.append(prompt)
            return "回答完毕"

        ex = WorkflowExecutor(repo, matcher, llm_step_runner=llm_runner)
        res = ex.execute_by_id(wf.id, "你好")
        assert res.success is True
        assert len(llm_calls) == 1
