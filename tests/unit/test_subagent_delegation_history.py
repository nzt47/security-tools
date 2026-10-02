"""委派记录（agent/subagent/delegation_history.py + 两处记录咽喉）单元测试

【为什么需要这份测试】「子代理」下拉此前只列**存活分身容器**，而委派一律
``destroy_after=True``（跑完即回收）⇒ 正常业务里那个面板恒为空，用户看到的是
"业务明明发生了、面板却什么都没有"。修法是每次委派落一条轻量记录。

本文件钉死三件事：
  1. 记录字段（含截断与缺省），且**不含交付物正文**（外来文本、体量不可控）；
  2. 两处咽喉都真的会落记录：容器 ``run_delegation``（单发）与
     ``delegate_many`` 的记录分支（批量**不经过**容器，必须各记各的）；
  3. **fail-soft**：写入失败只告警，绝不影响委派结果（这是本仓反复强调的
     "附带信息不得打挂主流程"）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent.subagent import delegation_history as dh_module
from agent.subagent.container import SubagentConfig, SubagentContainer
from agent.subagent.delegation import DelegationContext
from agent.subagent.delegation_history import (DelegationHistory,
                                               ERROR_MAX_CHARS, GOAL_MAX_CHARS)


def _ctx(goal: str = "把 docs 下的设计稿抽取为可复现步骤序列") -> DelegationContext:
    return DelegationContext(
        goal=goal, constraints=["只读"], prior_artifacts=[], prohibitions=[],
        artifact_format="文本要点", budget_tokens=4000, timeout_seconds=120,
        callback_url="internal://test", delegation_id="dlg-0001")


@dataclass
class _Outcome:
    ok: bool = True
    delegation_id: str = "dlg-0001"
    tier: str = "tier1"
    duration_ms: float = 12.3
    trace_id: str = "trace-1"
    error_code: str = ""
    error: str = ""
    artifacts: tuple = ({"name": "a"},)
    cost: object = None


class _Cost:
    total_tokens = 321


class _FakeExecutor:
    """记录入参并返回预设 outcome（真执行器不在单测里跑）"""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def execute(self, ctx, **kw):
        self.calls.append({"ctx": ctx, **kw})
        return self.outcome


@pytest.fixture
def store(tmp_path, monkeypatch):
    """把进程级单例换到 tmp（绝不碰仓库 data/subagent_delegations.jsonl）"""
    s = DelegationHistory(path=str(tmp_path / "delegations.jsonl"))
    monkeypatch.setattr(dh_module, "delegation_history", s)
    return s


class TestBuildRecord:
    def test_字段齐备且不含交付物正文(self):
        rec = DelegationHistory.build_record(
            ctx=_ctx(), outcome=_Outcome(), subagent="delegate-ab12", source="tool")
        assert rec["delegation_id"] == "dlg-0001"
        assert rec["subagent"] == "delegate-ab12"
        assert rec["source"] == "tool"
        assert rec["ok"] is True
        assert rec["tier"] == "tier1"
        assert rec["duration_ms"] == 12.3
        assert rec["trace_id"] == "trace-1"
        assert rec["artifact_count"] == 1
        assert rec["goal"].startswith("把 docs")
        # 不含正文/载荷：正文是外来文本且体量不可控，权威位置是 trace/产物
        assert "output_text" not in rec
        assert "payload" not in rec
        assert "result" not in rec

    def test_目标与错误被截断(self):
        rec = DelegationHistory.build_record(
            ctx=_ctx("目" * 500), outcome=_Outcome(ok=False, error="错" * 900))
        assert len(rec["goal"]) == GOAL_MAX_CHARS
        assert len(rec["error"]) == ERROR_MAX_CHARS

    def test_成本缺失时_tokens_为_None(self):
        assert DelegationHistory.build_record(
            ctx=_ctx(), outcome=_Outcome())["tokens"] is None
        assert DelegationHistory.build_record(
            ctx=_ctx(), outcome=_Outcome(cost=_Cost()))["tokens"] == 321

    def test_失败结果同样成记录(self):
        rec = DelegationHistory.build_record(
            ctx=_ctx(), outcome=_Outcome(ok=False, error_code="E_DELEGATION_INCOMPLETE",
                                         error="目标过短"))
        assert rec["ok"] is False
        assert rec["error_code"] == "E_DELEGATION_INCOMPLETE"


class TestQuery:
    def test_最新在前(self, tmp_path):
        s = DelegationHistory(path=str(tmp_path / "d.jsonl"))
        for i in range(3):
            s.append({"delegation_id": f"dlg-{i}"})
        assert [r["delegation_id"] for r in s.query(10)] == ["dlg-2", "dlg-1", "dlg-0"]
        assert [r["delegation_id"] for r in s.query(2)] == ["dlg-2", "dlg-1"]
        assert s.total() == 3

    def test_没有文件时为空列表而不是报错(self, tmp_path):
        s = DelegationHistory(path=str(tmp_path / "none.jsonl"))
        assert s.query(5) == []
        assert s.total() == 0

    def test_默认路径落在仓库_data_下(self):
        """【不易】不能是 CWD 相对路径：换个工作目录启动就会写到别处（与 health/storage 同口径）

        断言用**仓库标记文件**而不是"以 data/xxx.jsonl 结尾"：后者在层数写错时照样通过 ——
        本模块第一次落地时正是如此（照抄 health/storage 的两层推导，记录落到 agent/data/），
        而"结尾匹配"的断言没有拦住它。
        """
        import os
        p = DelegationHistory().path
        assert os.path.isabs(p)
        assert p.endswith(os.path.join("data", "subagent_delegations.jsonl"))
        repo_root = os.path.dirname(os.path.dirname(p))
        assert os.path.isfile(os.path.join(repo_root, "app_server.py")), (
            f"默认记录路径不在仓库根的 data/ 下（推导层数可能写错）: {p}")
        assert os.path.isfile(os.path.join(repo_root, "yunshu-ui", "package.json"))

    def test_写入失败只返回_False(self, tmp_path):
        """路径是目录 ⇒ 写不进去；调用方（委派链路）不能被它打挂"""
        s = DelegationHistory(path=str(tmp_path))
        assert s.record_outcome(ctx=_ctx(), outcome=_Outcome()) is False


class TestContainerChokePoint:
    def test_单发委派落一条记录_并透传_source(self, store):
        container = SubagentContainer(SubagentConfig(name="delegate-ab12", model_id="m"))
        executor = _FakeExecutor(_Outcome())

        outcome = container.run_delegation(_ctx(), executor=executor, source="ui")

        assert outcome.ok is True
        records = store.query(5)
        assert len(records) == 1
        rec = records[0]
        assert rec["subagent"] == "delegate-ab12"
        assert rec["source"] == "ui"
        assert rec["delegation_id"] == "dlg-0001"
        assert rec["ok"] is True

    def test_已销毁分身收到委派也留痕(self, store):
        """否则这次尝试在 UI 上**完全消失**（点了、没反应、也没记录）"""
        container = SubagentContainer(SubagentConfig(name="sa-gone", model_id="m"))
        container._is_destroyed = True  # 私有不变量：模拟生命周期回收后的容器

        outcome = container.run_delegation(_ctx(), executor=_FakeExecutor(_Outcome()),
                                           source="tool")

        assert outcome.ok is False and outcome.error_code == "E_SUBAGENT_DESTROYED"
        records = store.query(5)
        assert len(records) == 1
        assert records[0]["error_code"] == "E_SUBAGENT_DESTROYED"
        assert records[0]["subagent"] == "sa-gone"

    def test_记录写入失败不影响委派结果(self, tmp_path, monkeypatch):
        """fail-soft：历史是附带信息，写不进去也必须把委派结果原样返回"""
        broken = DelegationHistory(path=str(tmp_path))  # 路径是目录 ⇒ 必写失败
        monkeypatch.setattr(dh_module, "delegation_history", broken)
        container = SubagentContainer(SubagentConfig(name="sa-x", model_id="m"))

        outcome = container.run_delegation(_ctx(), executor=_FakeExecutor(_Outcome()))

        assert outcome.ok is True
        assert broken.query(5) == []


class TestBatchChokePoint:
    def test_批量委派逐条落记录(self, store):
        """批量走 executor.execute_many（不经过容器）⇒ 由 _record_batch 负责"""
        from agent.subagent.lifecycle import SubagentLifecycleManager

        mgr = SubagentLifecycleManager(max_subagents=5)
        items = [(_ctx(goal=f"任务{i}"), _ctx(goal=f"任务{i}")) for i in range(2)]
        containers = {i: type("C", (), {"config": SubagentConfig(name=f"sa-{i}", model_id="m")})()
                      for i in range(2)}
        results = [_Outcome(delegation_id="dlg-a"), _Outcome(ok=False, delegation_id="dlg-b")]

        mgr._record_batch(items, containers, results, "fan_out")

        records = store.query(10)
        assert [r["source"] for r in records] == ["fan_out", "fan_out"]
        assert [r["subagent"] for r in records] == ["sa-1", "sa-0"]
        assert [r["ok"] for r in records] == [False, True]

    def test_未产出结果的位置被跳过(self, store):
        from agent.subagent.lifecycle import SubagentLifecycleManager

        mgr = SubagentLifecycleManager(max_subagents=5)
        mgr._record_batch([(_ctx(), _ctx())], {}, [None], "fan_out")
        assert store.query(5) == []
