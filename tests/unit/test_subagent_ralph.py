"""E2-4 Ralph 循环：达标即停 / 轮次上限 / 无新证据止损 / 缺 acceptance"""
from __future__ import annotations

import pytest

from agent.subagent.delegation import DelegationContext
from agent.tools import subagent_tools as st


class FakeOutcome:
    def __init__(self, payload, text="产出正文", artifacts=(), ok=True, error=""):
        self.ok = ok
        self.payload = payload
        self.output_text = text
        self.artifacts = artifacts
        self.error = error


PASS = {"status": "done", "self_eval": {"verdict": "pass", "issues": []}}
FAIL = {"status": "done", "self_eval": {"verdict": "fail", "issues": ["还缺一节"]}}


class FakeManager:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def delegate(self, config, ctx, **kw):
        self.calls.append({"ctx": ctx, **kw})
        if not self.outcomes:
            raise AssertionError("Ralph 轮次超出预设")
        return self.outcomes.pop(0)


def _ctx(goal="把交接文档补完并自检"):
    return DelegationContext(
        goal=goal, constraints=["只读"], prior_artifacts=[], prohibitions=["不要联网"],
        artifact_format="json", budget_tokens=1000, timeout_seconds=30,
        callback_url="http://127.0.0.1:1/cb")


def _run(outcomes, rounds=4, acceptance="给出补齐后的交接文档并自检通过"):
    manager = FakeManager(outcomes)
    result = st.run_ralph(manager, object(), _ctx(), llm=object(), tools=("read_file",),
                          acceptance=acceptance, max_rounds=rounds,
                          result_mapper=lambda o, c: {"ok": o.ok, "result": o.output_text})
    return manager, result


def test_第二轮达标即停():
    manager, result = _run([FakeOutcome(FAIL), FakeOutcome(PASS)])
    assert result["ok"] is True
    assert result["ralph"]["status"] == "done" and result["ralph"]["rounds"] == 2
    assert len(manager.calls) == 2
    # 每轮目标/约束都带验收条件；第二轮还带上一轮的缺失项
    assert "【验收条件】" in manager.calls[0]["ctx"].goal
    assert "【上一轮未达标，必须补齐】还缺一节" in manager.calls[1]["ctx"].goal
    assert "【验收条件】给出补齐后的交接文档并自检通过" in manager.calls[0]["ctx"].constraints
    # 每轮 delegation_id 唯一（不复用同一轮标识）
    ids = {c["ctx"].delegation_id for c in manager.calls}
    assert len(ids) == 2


def test_轮次耗尽止损():
    outcomes = [FakeOutcome(FAIL, text="第%d版" % i) for i in range(3)]
    manager, result = _run(outcomes, rounds=3)
    assert result["ok"] is False
    assert result["error_code"] == "E_RALPH_BLOCKED"
    assert result["ralph"]["status"] == "blocked"
    assert result["ralph"]["reason"] == "max_rounds"
    assert len(result["ralph"]["history"]) == 3


def test_连续无新证据提前止损():
    same = [FakeOutcome(FAIL, text="一模一样") for _ in range(4)]
    manager, result = _run(same, rounds=4)
    assert result["ralph"]["reason"] == "no_progress"
    assert result["ralph"]["rounds"] == 3, "第 3 轮发现连续两轮无新证据即停"
    assert len(manager.calls) == 3


def test_判定口径_证据为空或状态不done都不算达标():
    assert st.ralph_verdict(FakeOutcome(PASS, text=""))["accepted"] is False
    assert st.ralph_verdict(FakeOutcome(PASS, text="   "))["accepted"] is False
    assert st.ralph_verdict(FakeOutcome({"status": "blocked",
                                         "self_eval": {"verdict": "pass"}}))["accepted"] is False
    assert st.ralph_verdict(FakeOutcome(PASS))["accepted"] is True
    # 产物条目也算证据
    art = FakeOutcome({"status": "done", "self_eval": {"verdict": "ok"}},
                      text="", artifacts=({"path": "docs/x.md"},))
    assert st.ralph_verdict(art)["accepted"] is True


def test_轮次收敛与缺_acceptance():
    assert st._clamp_ralph_rounds(0) == 2
    assert st._clamp_ralph_rounds(99) == st.MAX_RALPH_ROUNDS
    assert st._clamp_ralph_rounds("x") == st.DEFAULT_RALPH_ROUNDS

    class Dummy:
        _llm = object()
        _subagent_mgr = FakeManager([FakeOutcome(PASS)])

    base = {
        "goal": "把交接文档补完并自检", "constraints": ["只读"],
        "prior_artifacts": [], "prohibitions": ["不要联网"],
        "artifact_format": "json", "budget_tokens": 1000,
        "timeout_seconds": 30, "callback_url": "http://127.0.0.1:1/cb",
        "ralph": True,
    }
    out = st._run_delegate(Dummy(), base)
    assert out["ok"] is False and out["error_code"] == "E_RALPH_NO_ACCEPTANCE"
