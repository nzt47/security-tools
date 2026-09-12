"""TASK-S7-02 步骤 3 派工 单元测试

覆盖（任务书 §四 验收清单）：
- **八要素不全时拒绝派工**（逐要素单点缺失 + 缺目标文本 + 全空）
- **子代理无写权限/审批权**（工具裁剪断言：申请写类工具也不可见 + 越界裁剪集即失败）
- 派工上下文里八要素齐备且**约束/禁止事项非空**
- 预算与轮次上限生效（**超限即停并留证**）
- 补丁载荷解析（有 diff / 无 diff / 退化 patch_text）
- 委派失败**不拿半个补丁去验证**
- 决策顺序：八要素 → 预算预检 → 委派 → 预算记账 → 工具集断言 → 护栏
"""

from __future__ import annotations

import pytest

from repair_fixtures import (
    FakeDelegationExecutor,
    FakeOutcome,
    make_patch,
    make_ticket,
    simple_diff,
)

from agent.repair import delegate as DL
from agent.repair.budget import RepairBudget
from agent.repair.models import (
    REASON_BUDGET_EXCEEDED,
    REASON_DELEGATION_FAILED,
    REASON_DELEGATION_REJECTED,
    REASON_NO_PATCH,
    REASON_READONLY_ZONE,
    REASON_SCOPE_EXCEEDED,
)
from agent.repair.policy import REPAIR_FORBIDDEN_TOOLS, REPAIR_SUBAGENT_TOOLS, RepairPolicy


def make_request(**overrides):
    data = {
        "ticket": make_ticket(),
        "policy": RepairPolicy(),
        "run_logger": None,
        "authorized_capabilities": REPAIR_SUBAGENT_TOOLS,
    }
    data.update(overrides)
    return DL.DelegationRequest(**data)


def payload_with_diff(diff: str, **overrides):
    data = {
        "status": "done",
        "summary": "改了边界条件",
        "diff": diff,
        "rationale": "off-by-one：循环上界应为 < 而非 <=",
        "files": ["agent/demo_math.py"],
        "self_eval": {"verdict": "pass", "score": 0.8, "issues": []},
        "input_tokens": 700,
        "output_tokens": 500,
    }
    data.update(overrides)
    return data


# ════════════════════════════════════════════════════════════
#  八要素准入
# ════════════════════════════════════════════════════════════


class TestEightElementGate:
    def test_valid_context_is_complete(self):
        ctx = DL.build_delegation_context(make_ticket(), RepairPolicy())
        assert ctx.validate() == ()
        assert len(ctx.elements()) == 8

    def test_constraints_and_prohibitions_are_non_empty(self):
        ctx = DL.build_delegation_context(make_ticket(), RepairPolicy())
        assert ctx.constraints
        assert ctx.prohibitions
        assert any("只读区" in c for c in ctx.prohibitions)
        assert any("push" in c for c in ctx.prohibitions)

    def test_goal_mentions_the_failing_case(self):
        goal = DL.build_goal(make_ticket())
        assert "test_add" in goal
        assert len(goal) >= 8

    def test_goal_empty_without_failure(self):
        """没有失败项 → 目标为空 → 八要素必然不合格（**拒绝派工**）"""
        ticket = make_ticket(failure=None)
        assert DL.build_goal(ticket) == ""
        ctx = DL.build_delegation_context(ticket, RepairPolicy())
        assert "goal" in ctx.validate()

    @pytest.mark.parametrize("element,replacement", [
        ("goal", ""),
        ("constraints", []),
        ("prior_artifacts", None),
        ("prohibitions", None),
        ("artifact_format", ""),
        ("budget_tokens", 0),
        ("timeout_seconds", 0),
        ("callback_url", None),
    ])
    def test_each_missing_element_rejects_delegation(self, element, replacement):
        """逐要素单点缺失 → 拒绝派工（且原因是**该要素**）"""
        ctx = DL.build_delegation_context(make_ticket(), RepairPolicy())
        broken = {**ctx.to_dict(), element: replacement}
        from agent.subagent.delegation import DelegationContext
        bad = DelegationContext(**{k: v for k, v in broken.items()
                                  if k in DelegationContext.__dataclass_fields__})
        assert element in bad.validate()

    def test_delegate_rejects_when_goal_missing(self):
        """端到端：目标缺失时 ``delegate()`` 拒绝且不调用执行器"""
        executor = FakeDelegationExecutor([FakeOutcome(payload=payload_with_diff(
            simple_diff()))])
        outcome = DL.delegate(make_request(ticket=make_ticket(failure=None)),
                             executor=executor, budget=RepairBudget(token_limit=10 ** 6,
                                                                    max_rounds=1))
        assert outcome.ok is False
        assert outcome.reason == REASON_DELEGATION_REJECTED
        assert executor.calls == [], "八要素不合格时不得调用执行器"
        assert "goal" in outcome.detail.get("missing", [])

    def test_delegate_records_rejection_in_audit(self, run_logger):
        executor = FakeDelegationExecutor([])
        outcome = DL.delegate(
            make_request(ticket=make_ticket(failure=None), run_logger=run_logger),
            executor=executor, budget=RepairBudget(token_limit=10 ** 6, max_rounds=1))
        assert outcome.ok is False
        steps = [e for e in run_logger.trail() if e.step == "delegate"]
        assert steps and steps[0].status == "error"


# ════════════════════════════════════════════════════════════
#  工具裁剪：无写权限 / 无审批权 / 无记忆读写
# ════════════════════════════════════════════════════════════


class TestToolTrim:
    def test_allowlist_is_readonly_only(self):
        for tool in REPAIR_SUBAGENT_TOOLS:
            assert not any(tool.startswith(p) for p in
                           ("memory.", "approval.", "governance.", "core."))

    def test_assert_readonly_toolset_accepts_readonly(self):
        ok, problems = DL.assert_readonly_toolset({"tools": list(REPAIR_SUBAGENT_TOOLS)})
        assert ok is True and problems == []

    def test_assert_readonly_toolset_rejects_write_tools(self):
        ok, problems = DL.assert_readonly_toolset(
            {"tools": ["read_file", "write_file", "apply_patch"]})
        assert ok is False
        assert any("write_file" in p for p in problems)

    def test_assert_readonly_toolset_rejects_memory_and_approval(self):
        ok, problems = DL.assert_readonly_toolset(
            {"tools": ["read_file", "memory.write", "approval.approve"]})
        assert ok is False
        assert len(problems) == 2
    def test_forbidden_tools_are_documented(self):
        for name in ("write_file", "apply_patch", "memory.write", "approval.approve",
                     "git_push", "git_merge"):
            assert name in REPAIR_FORBIDDEN_TOOLS

    def test_delegate_forwards_readonly_allowlist(self):
        executor = FakeDelegationExecutor([FakeOutcome(payload=payload_with_diff(
            simple_diff()))])
        DL.delegate(make_request(), executor=executor,
                    budget=RepairBudget(token_limit=10 ** 6, max_rounds=1))
        call = executor.calls[0]
        assert tuple(call["tools"]) == REPAIR_SUBAGENT_TOOLS
        assert tuple(call["authorized_capabilities"]) == REPAIR_SUBAGENT_TOOLS
        assert "write_file" not in call["authorized_capabilities"]

    def test_unsafe_toolset_aborts_even_if_upstream_ok(self):
        """即使执行器自称成功，裁剪集里出现写类工具 → 本次产出作废"""
        executor = FakeDelegationExecutor([FakeOutcome(
            payload=payload_with_diff(simple_diff()),
            toolset={"tools": ["read_file", "write_file"], "denied_count": 0})])
        outcome = DL.delegate(make_request(), executor=executor,
                              budget=RepairBudget(token_limit=10 ** 6, max_rounds=1))
        assert outcome.ok is False
        assert outcome.reason == REASON_DELEGATION_FAILED
        assert "E_REPAIR_TOOLSET_UNSAFE" in str(outcome.detail)

    def test_real_toolset_trim_denies_memory_and_approval(self):
        """复用 S4-04 真实裁剪：矩阵类工具（记忆/审批）**申请也不可见**"""
        from agent.subagent.toolset import SubAgentToolset

        toolset = SubAgentToolset.build(
            ["read_file", "memory.write", "approval.approve", "core.rewrite"],
            ["read_file", "memory.write", "approval.approve", "core.rewrite"],
            actor="sub_agent:test")
        assert toolset.visible_tools() == ("read_file",)
        assert len(toolset.report.denied) == 3
        ok, problems = DL.assert_readonly_toolset(toolset.as_manifest())
        assert ok is True, problems

    def test_write_file_not_authorized_by_repair_pipeline(self):
        """通用写工具（如 ``write_file``）不靠矩阵拦，而靠**授权子集**拦：

        §7.0 矩阵管的是记忆/审批/治理/核心改写；``write_file`` 这类执行类工具
        不在矩阵拒绝项内，故本流程的挡板是「授权子集 = 只读白名单」——
        「申请 ∩ 授权」为空即不可见。这条断言把该挡板钉死。
        """
        from agent.subagent.toolset import SubAgentToolset

        requested = ("read_file", "write_file", "apply_patch")
        toolset = SubAgentToolset.build(requested, REPAIR_SUBAGENT_TOOLS,
                                        actor="sub_agent:test")
        assert toolset.visible_tools() == ("read_file",)
        assert len(toolset.report.denied) == 2

    def test_repair_allowlist_has_no_write_tools(self):
        for name in ("write_file", "edit_file", "apply_patch", "run_command",
                     "shell", "bash"):
            assert name not in REPAIR_SUBAGENT_TOOLS


# ════════════════════════════════════════════════════════════
#  护栏在派工内的落点
# ════════════════════════════════════════════════════════════


class TestGuardInsideDelegate:
    def _run(self, diff: str, *, policy=None):
        executor = FakeDelegationExecutor([FakeOutcome(payload=payload_with_diff(diff))])
        return DL.delegate(make_request(policy=policy or RepairPolicy()),
                           executor=executor,
                           budget=RepairBudget(token_limit=10 ** 6, max_rounds=1))

    def test_clean_patch_accepted(self):
        outcome = self._run(simple_diff())
        assert outcome.ok is True
        assert outcome.reason == ""
        assert outcome.guard.ok is True

    def test_readonly_zone_discards_patch(self):
        outcome = self._run(simple_diff("agent/audit/chain.py"))
        assert outcome.ok is False
        assert outcome.reason == REASON_READONLY_ZONE
        assert outcome.patch.rejected_reason == REASON_READONLY_ZONE

    def test_scope_exceeded_discards_patch(self):
        diff = "".join(simple_diff(f"agent/m{i}.py") for i in range(4))
        outcome = self._run(diff)
        assert outcome.ok is False
        assert outcome.reason == REASON_SCOPE_EXCEEDED

    def test_empty_diff_is_no_patch(self):
        outcome = self._run("")
        assert outcome.ok is False
        assert outcome.reason == REASON_NO_PATCH

    def test_test_assertion_change_passes_but_is_flagged(self):
        outcome = self._run(simple_diff("tests/unit/test_demo_math.py"))
        assert outcome.ok is True
        assert outcome.guard.test_assertions_touched == ["tests/unit/test_demo_math.py"]


# ════════════════════════════════════════════════════════════
#  预算与轮次（超限即停并留证）
# ════════════════════════════════════════════════════════════


class TestBudget:
    def test_headroom_check_stops_before_dispatch(self):
        executor = FakeDelegationExecutor([FakeOutcome(payload=payload_with_diff(
            simple_diff()))])
        budget = RepairBudget(token_limit=10 ** 6, max_rounds=1)
        budget.tokens_used = budget.token_limit  # 余额为 0
        outcome = DL.delegate(make_request(), executor=executor, budget=budget)
        assert outcome.ok is False
        assert outcome.reason == REASON_BUDGET_EXCEEDED
        assert executor.calls == [], "余额不足时不得派工"

    def test_consume_exceeding_budget_stops(self):
        executor = FakeDelegationExecutor([FakeOutcome(payload=payload_with_diff(
            simple_diff()), tokens=5000)])
        budget = RepairBudget(token_limit=1000, max_rounds=1)
        outcome = DL.delegate(make_request(), executor=executor, budget=budget)
        assert outcome.ok is False
        assert outcome.reason == REASON_BUDGET_EXCEEDED
        assert budget.tokens_used == 5000

    def test_tokens_are_accounted_on_success(self):
        executor = FakeDelegationExecutor([FakeOutcome(payload=payload_with_diff(
            simple_diff()), tokens=1200)])
        budget = RepairBudget(token_limit=10 ** 6, max_rounds=1)
        outcome = DL.delegate(make_request(), executor=executor, budget=budget)
        assert outcome.ok is True
        assert outcome.tokens_used == 1200
        assert budget.tokens_used == 1200

    def test_round_limit_raises(self):
        from agent.repair.budget import BudgetExceeded
        budget = RepairBudget(token_limit=100, max_rounds=1)
        budget.start_round()
        with pytest.raises(BudgetExceeded) as exc:
            budget.start_round()
        assert exc.value.code == "E_REPAIR_ROUNDS_EXCEEDED"

    def test_zero_tokens_is_recorded_honestly(self):
        executor = FakeDelegationExecutor([FakeOutcome(payload=payload_with_diff(
            simple_diff(), input_tokens=0, output_tokens=0), tokens=0)])
        budget = RepairBudget(token_limit=1000, max_rounds=1)
        DL.delegate(make_request(), executor=executor, budget=budget)
        assert budget.tokens_used == 0
        assert any("未计量" in n for n in budget.notes)


# ════════════════════════════════════════════════════════════
#  失败路径不产出
# ════════════════════════════════════════════════════════════


class TestFailurePaths:
    def test_upstream_failure_is_reported_not_verified(self):
        executor = FakeDelegationExecutor([FakeOutcome(
            payload={}, ok=False, error="CLI 不可用", error_code="E_CHANNEL")])
        outcome = DL.delegate(make_request(), executor=executor,
                              budget=RepairBudget(token_limit=10 ** 6, max_rounds=1))
        assert outcome.ok is False
        assert outcome.reason == REASON_DELEGATION_FAILED
        assert outcome.guard is None

    def test_executor_exception_is_captured(self):
        executor = FakeDelegationExecutor([RuntimeError("通道炸了")])
        outcome = DL.delegate(make_request(), executor=executor,
                              budget=RepairBudget(token_limit=10 ** 6, max_rounds=1))
        assert outcome.ok is False
        assert outcome.reason == REASON_DELEGATION_FAILED
        assert "通道炸了" in str(outcome.detail)

    def test_exception_is_audited(self, run_logger):
        executor = FakeDelegationExecutor([RuntimeError("通道炸了")])
        DL.delegate(make_request(run_logger=run_logger), executor=executor,
                    budget=RepairBudget(token_limit=10 ** 6, max_rounds=1))
        entry = [e for e in run_logger.trail() if e.step == "delegate"][0]
        assert entry.status == "error"
        assert "执行器异常" in str(entry.detail) or entry.detail.get("error")


class TestPatchPayload:
    def test_prefers_diff_key(self):
        patch = DL.parse_patch_payload(FakeOutcome(payload={"diff": "d1",
                                                            "patch_text": "d2"}))
        assert patch.diff == "d1"

    def test_falls_back_to_patch_text(self):
        patch = DL.parse_patch_payload(FakeOutcome(payload={"patch_text": "d2"}))
        assert patch.diff == "d2"

    def test_missing_diff_is_empty_not_fabricated(self):
        patch = DL.parse_patch_payload(FakeOutcome(payload={"summary": "我改好了"}))
        assert patch.diff == ""
        assert patch.non_empty is False

    def test_rationale_falls_back_to_summary(self):
        patch = DL.parse_patch_payload(FakeOutcome(payload={"summary": "为什么这样改"}))
        assert patch.rationale == "为什么这样改"

    def test_files_collected_from_artifacts(self):
        patch = DL.parse_patch_payload(FakeOutcome(payload={
            "artifacts": [{"path": "agent/x.py"}, {"path": "agent/y.py"}]}))
        assert patch.files == ["agent/x.py", "agent/y.py"]

    def test_envelope_is_json(self):
        import json
        patch = make_patch(simple_diff())
        payload = json.loads(DL.patch_envelope(patch))
        assert payload["diff"].startswith("diff --git")
        assert payload["schema"] == "repair.patch.v1"


class TestInputText:
    def test_input_contains_slices_and_gaps(self):
        ticket = make_ticket(evidence_gaps=["Trace 库中无相关记录"])
        text = DL.build_input_text(ticket)
        assert "源码切片" in text
        assert "证据缺口" in text
        assert "Trace 库中无相关记录" in text

    def test_input_has_no_whole_repo(self):
        text = DL.build_input_text(make_ticket())
        assert len(text) < 20000
