"""二次派发轮次机制守卫（agent/subagent/rounds.py + task_board 写侧 + delegate 路由）

语义：母体是唯一写板者；跟单 = 同 task_id 的第二轮，上一轮显式标 superseded。
本文件把守卫钉成**可证伪**断言（回退产品逻辑 ⇒ 立刻红）：

  R1 纯逻辑：link_follow_up 沿用 task_id / 写 round=2 与 previous_delegation_id /
     ③追加引用行，且**不改**原 ctx；
  R2 fail-closed 判定：不存在 / 已被取代 / 跨租户 / 跨主体 / 已到最大轮次 ⇒ 对应错误码；
  R3 看板写侧：跟单先补一行 superseded（append-only，不回改旧行），折叠态为本轮；
  R4 find_delegation：按 delegation_id 取最新行（含取代标记），未知名 ⇒ None；
  R5 路由接线：两条 delegate 端点接受 previous_delegation_id，回显 round 段，
     并把 task_id / metadata 真正透传给执行器；非法跟单 400 且**不执行**；
  R6 静态边界：rounds.py 不导入看板写 API，键名与 TaskRecord / STATUSES 对拍。

【不碰仓库 data/】board 夹具把两个进程级单例（task_board / delegation_history）
换到 tmp_path。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from flask import Flask

from agent.server_routes import routes_subagent as routes_module
from agent.server_routes.routes_subagent import register_routes
from agent.subagent import delegation_history as dh_module
from agent.subagent import task_board as tb_module
from agent.subagent.delegation import DelegationContext
from agent.subagent.delegation_history import DelegationHistory
from agent.subagent.rounds import (E_ROUND_LIMIT, E_ROUND_PREVIOUS_NOT_FOUND,
                                   E_ROUND_SCOPE_MISMATCH, MAX_ROUND, PREVIOUS_FIELD,
                                   ROUND_FIELD, SUPERSEDED_STATUS, RoundError,
                                   link_follow_up, reference_line, round_of,
                                   round_view, validate_previous)
from agent.subagent.task_board import STATUSES, TaskBoard, TaskRecord

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ════════════════════════════════════════════════════════════
#  fixtures / 替身
# ════════════════════════════════════════════════════════════


@dataclass
class _Outcome:
    ok: bool = True
    delegation_id: str = "dlg-1"
    trace_id: str = "trace-1"
    trace: object = None
    error_code: str = ""
    error: str = ""
    duration_ms: float = 12.3
    artifacts: tuple = ()
    cost: object = None
    output_text: str = "SECRET_BODY"
    payload: dict = field(default_factory=dict)


def _ctx(*, goal: str = "第一轮：把设计稿抽取为可复现步骤序列",
         delegation_id: str = "dlg-1", task_id: str = "", metadata=None,
         tenant_id: str = "default", subject_id: str = "",
         prior_artifacts=()) -> DelegationContext:
    return DelegationContext(
        goal=goal, constraints=["只读"], prior_artifacts=list(prior_artifacts),
        prohibitions=[], artifact_format="文本要点", budget_tokens=4000,
        timeout_seconds=120, callback_url="internal://test",
        delegation_id=delegation_id, task_id=task_id, tenant_id=tenant_id,
        subject_id=subject_id, metadata=metadata or {})


@pytest.fixture
def board(tmp_path, monkeypatch):
    """把进程级 task_board 与 delegation_history 都换到 tmp（不碰仓库 data/）"""
    b = TaskBoard(path=str(tmp_path / "board.jsonl"))
    monkeypatch.setattr(tb_module, "task_board", b)
    monkeypatch.setattr(routes_module, "task_board", b)
    monkeypatch.setattr(dh_module, "delegation_history",
                        DelegationHistory(path=str(tmp_path / "delegations.jsonl")))
    return b


def _seed(board, *, task_id: str = "task-1", delegation_id: str = "dlg-1",
          round_no: int = 1, tenant_id: str = "default", subject_id: str = "",
          goal: str = "第一轮：抽取步骤", status: str = "done") -> None:
    """直接写一条"上一轮"看板事件（模拟已经发生过的第一轮）"""
    assert board.record_create({
        "task_id": task_id, "board_op": "create", "status": status,
        "delegation_id": delegation_id, "round": round_no,
        "tenant_id": tenant_id, "subject_id": subject_id, "goal": goal,
        "trace_id": "trace-1",
    }) is True


# ════════════════════════════════════════════════════════════
#  R1 纯逻辑
# ════════════════════════════════════════════════════════════


class TestR1PureLogic:
    def test_round_of_归一化为_1_或_2(self):
        assert round_of({"round": 1}) == 1
        assert round_of({"round": 2}) == 2
        assert round_of({"round": 9}) == 1, "非法轮次不放大口径（回落 1）"
        assert round_of({"round": "x"}) == 1
        assert round_of({}) == 1
        assert round_of(_ctx(metadata={ROUND_FIELD: 2})) == 2

    def test_reference_line_含标识与截断目标(self):
        line = reference_line({"delegation_id": "dlg-1", "goal": "甲" * 300})
        assert "dlg-1" in line
        assert "甲" * 120 in line
        assert "甲" * 121 not in line, "引用行必须截断（只做引用，不搬正文）"

    def test_reference_line_无目标时仍给出标识(self):
        assert reference_line({"delegation_id": "dlg-1"}) == "上一轮委派 dlg-1"
        assert reference_line({}) == ""

    def test_link_follow_up_沿用_task_id_且不改原_ctx(self):
        previous = {"delegation_id": "dlg-1", "task_id": "task-1", "goal": "第一轮"}
        ctx = _ctx(delegation_id="dlg-2")
        linked = link_follow_up(ctx, previous)

        assert linked is not ctx, "必须是 replace 产物，不就地改原上下文"
        assert linked.task_id == "task-1", "同一条逻辑任务的第二轮必须沿用 task_id"
        assert linked.metadata[ROUND_FIELD] == MAX_ROUND
        assert linked.metadata[PREVIOUS_FIELD] == "dlg-1"
        assert any("dlg-1" in a for a in linked.prior_artifacts)
        # 原 ctx 必须原封不动（纯函数）
        assert ctx.task_id == "" and dict(ctx.metadata) == {} and not ctx.prior_artifacts

    def test_link_follow_up_无_task_id_时退回上一轮委派_id(self):
        linked = link_follow_up(_ctx(delegation_id="dlg-2"),
                                {"delegation_id": "dlg-1", "goal": "第一轮"})
        assert linked.task_id == "dlg-1"

    def test_引用行幂等(self):
        previous = {"delegation_id": "dlg-1", "task_id": "task-1", "goal": "第一轮"}
        ctx = _ctx(delegation_id="dlg-2",
                   prior_artifacts=[reference_line(previous)])
        linked = link_follow_up(ctx, previous)
        assert list(linked.prior_artifacts).count(reference_line(previous)) == 1

    def test_round_view_首轮与跟单(self):
        first = round_view(_ctx(delegation_id="dlg-x"))
        assert first == {"round": 1, "previous_delegation_id": "",
                         "task_id": "dlg-x", "follow_up": False}
        follow = round_view(_ctx(
            delegation_id="dlg-y", task_id="task-1",
            metadata={ROUND_FIELD: 2, PREVIOUS_FIELD: "dlg-1"}))
        assert follow == {"round": 2, "previous_delegation_id": "dlg-1",
                          "task_id": "task-1", "follow_up": True}


# ════════════════════════════════════════════════════════════
#  R2 fail-closed 判定
# ════════════════════════════════════════════════════════════


class TestR2Validation:
    def test_找不到上一轮(self):
        with pytest.raises(RoundError) as ei:
            validate_previous(None, previous_delegation_id="ghost")
        assert ei.value.code == E_ROUND_PREVIOUS_NOT_FOUND

    def test_已被取代的上一轮不可再作基准(self):
        previous = {"delegation_id": "dlg-1", "round": 1,
                    "status": SUPERSEDED_STATUS}
        with pytest.raises(RoundError) as ei:
            validate_previous(previous)
        assert ei.value.code == E_ROUND_LIMIT

    def test_跨租户被拒(self):
        with pytest.raises(RoundError) as ei:
            validate_previous({"delegation_id": "dlg-1", "tenant_id": "other"})
        assert ei.value.code == E_ROUND_SCOPE_MISMATCH

    def test_跨主体被拒(self):
        with pytest.raises(RoundError) as ei:
            validate_previous({"delegation_id": "dlg-1", "subject_id": "alice"},
                              subject_id="bob")
        assert ei.value.code == E_ROUND_SCOPE_MISMATCH

    def test_已到最大轮次被拒(self):
        with pytest.raises(RoundError) as ei:
            validate_previous({"delegation_id": "dlg-2", "round": 2})
        assert ei.value.code == E_ROUND_LIMIT

    def test_合法上一轮原样返回(self):
        previous = {"delegation_id": "dlg-1", "task_id": "task-1", "round": 1,
                    "tenant_id": "default", "subject_id": ""}
        assert validate_previous(previous) is previous


# ════════════════════════════════════════════════════════════
#  R3 / R4 看板写侧与查找
# ════════════════════════════════════════════════════════════


class TestR3BoardWriteSide:
    def test_跟单先补_superseded_行再写本轮(self, board):
        ctx1 = _ctx(delegation_id="dlg-1")
        board.record_outcome(ctx=ctx1, outcome=_Outcome(delegation_id="dlg-1"))
        ctx2 = _ctx(delegation_id="dlg-2", task_id="dlg-1",
                    metadata={ROUND_FIELD: 2, PREVIOUS_FIELD: "dlg-1"})
        board.record_outcome(ctx=ctx2, outcome=_Outcome(delegation_id="dlg-2"))

        raw = [json.loads(line) for line in
               Path(board.path).read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(raw) == 3, "第一轮 + 取代标记 + 第二轮 = 3 条原始事件"
        assert raw[0]["delegation_id"] == "dlg-1" and raw[0]["status"] == "done"
        marker = raw[1]
        assert marker["status"] == SUPERSEDED_STATUS
        assert marker["delegation_id"] == "dlg-1"
        assert marker["board_op"] == "update"
        assert raw[2]["delegation_id"] == "dlg-2"

        folded = board.query(10)
        assert len(folded) == 1, "同 task_id 折叠成一条逻辑任务"
        rec = folded[0]
        assert rec["round"] == 2
        assert rec["previous_delegation_id"] == "dlg-1"
        assert rec["status"] == "done", "折叠态是第二轮（最新胜）"

    def test_非跟单不写_superseded(self, board):
        board.record_outcome(ctx=_ctx(delegation_id="solo"),
                             outcome=_Outcome(delegation_id="solo"))
        raw = [json.loads(line) for line in
               Path(board.path).read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(raw) == 1
        assert raw[0]["status"] == "done"


class TestR4FindDelegation:
    def test_按_delegation_id_取最新行(self, board):
        ctx1 = _ctx(delegation_id="dlg-1")
        board.record_outcome(ctx=ctx1, outcome=_Outcome(delegation_id="dlg-1"))
        ctx2 = _ctx(delegation_id="dlg-2", task_id="dlg-1",
                    metadata={ROUND_FIELD: 2, PREVIOUS_FIELD: "dlg-1"})
        board.record_outcome(ctx=ctx2, outcome=_Outcome(delegation_id="dlg-2"))

        row2 = board.find_delegation("dlg-2")
        assert row2 is not None and row2["round"] == 2
        assert row2["previous_delegation_id"] == "dlg-1"
        # dlg-1 的最新行是"被取代"标记：再拿它当基准会被 validate_previous 拒（见 R2）
        marker = board.find_delegation("dlg-1")
        assert marker is not None and marker["status"] == SUPERSEDED_STATUS

    def test_未知名与空值返回_None(self, board):
        assert board.find_delegation("ghost") is None
        assert board.find_delegation("") is None


# ════════════════════════════════════════════════════════════
#  R5 路由接线
# ════════════════════════════════════════════════════════════


@dataclass
class _FakeOutcome:
    ok: bool = True
    delegation_id: str = "dlg-2"
    tier: str = "jsonl"
    duration_ms: float = 5.0
    trace_id: str = "trace-2"
    output_text: str = "第二轮产出"
    error_code: str = ""
    error: str = ""
    sub_reason: str = ""
    artifacts: tuple = field(default_factory=tuple)


class _FakeContainer:
    """记录 run_delegation 的入参（真执行器不在单测里跑）"""

    def __init__(self):
        self.calls = []

    def run_delegation(self, ctx, **kw):
        self.calls.append({"ctx": ctx, **kw})
        return _FakeOutcome()


class _FakeMgr:
    def __init__(self):
        self.container = _FakeContainer()
        self.delegate_calls = []

    def get(self, name):
        return self.container if name == "sa-1" else None

    def list(self):
        return []

    def delegate(self, config, ctx, **kw):
        self.delegate_calls.append({"config": config, "ctx": ctx, **kw})
        return _FakeOutcome(delegation_id="dlg-2-tmp")


class _FakeYunshu:
    def __init__(self, mgr):
        self._subagent_mgr = mgr
        self._llm = "fake-llm"

    def list_subagents(self):
        return []


@pytest.fixture
def client_and_mgr(board):
    mgr = _FakeMgr()
    state = type("S", (), {"Yunshu": _FakeYunshu(mgr)})()
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, state)
    return app.test_client(), mgr


class TestR5RouteWiring:
    def test_具名跟单_回显且透传(self, board, client_and_mgr):
        client, mgr = client_and_mgr
        _seed(board, delegation_id="dlg-1", task_id="task-1")

        r = client.post("/api/subagent/sa-1/delegate",
                        json={"task": "校验第一轮结论并修正错误",
                              "previous_delegation_id": "dlg-1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["round"] == {"round": 2, "previous_delegation_id": "dlg-1",
                                 "task_id": "task-1", "follow_up": True}
        assert len(mgr.container.calls) == 1
        ctx = mgr.container.calls[0]["ctx"]
        assert ctx.task_id == "task-1"
        assert ctx.metadata[ROUND_FIELD] == 2
        assert ctx.metadata[PREVIOUS_FIELD] == "dlg-1"
        assert any("dlg-1" in a for a in ctx.prior_artifacts), "跟单须带上引用行"
        # 八要素回显也要能看到引用（透明）
        assert any("dlg-1" in a for a in body["elements"]["prior_artifacts"])

    def test_首轮_round_段恒有(self, board, client_and_mgr):
        client, mgr = client_and_mgr
        r = client.post("/api/subagent/sa-1/delegate",
                        json={"task": "把设计稿抽取为可复现步骤序列"})
        assert r.status_code == 200
        assert r.get_json()["round"] == {"round": 1, "previous_delegation_id": "",
                                         "task_id": r.get_json()["round"]["task_id"],
                                         "follow_up": False}

    def test_未知上一轮_400_且不执行(self, board, client_and_mgr):
        client, mgr = client_and_mgr
        r = client.post("/api/subagent/sa-1/delegate",
                        json={"task": "校验第一轮结论并修正错误",
                              "previous_delegation_id": "ghost"})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == E_ROUND_PREVIOUS_NOT_FOUND
        assert mgr.container.calls == [], "非法跟单不得触发执行"

    def test_跨租户跟单_400(self, board, client_and_mgr):
        client, mgr = client_and_mgr
        _seed(board, tenant_id="other")
        r = client.post("/api/subagent/sa-1/delegate",
                        json={"task": "校验第一轮结论并修正错误",
                              "previous_delegation_id": "dlg-1"})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == E_ROUND_SCOPE_MISMATCH
        assert mgr.container.calls == []

    def test_已是第二轮不可再跟_400(self, board, client_and_mgr):
        client, mgr = client_and_mgr
        _seed(board, round_no=2)
        r = client.post("/api/subagent/sa-1/delegate",
                        json={"task": "再校验一次第一轮结论并修正错误",
                              "previous_delegation_id": "dlg-1"})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == E_ROUND_LIMIT

    def test_已被取代的上一轮再引用_400(self, board, client_and_mgr):
        client, mgr = client_and_mgr
        _seed(board)
        # 模拟一次已经发生过的跟单：写入 dlg-1 的取代标记
        board.record_update({
            "task_id": "task-1", "board_op": "update", "status": SUPERSEDED_STATUS,
            "delegation_id": "dlg-1", "round": 1, "tenant_id": "default",
            "subject_id": "", "goal": "第一轮：抽取步骤"})
        r = client.post("/api/subagent/sa-1/delegate",
                        json={"task": "再校验一次第一轮结论并修正错误",
                              "previous_delegation_id": "dlg-1"})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == E_ROUND_LIMIT
        assert mgr.container.calls == []

    def test_临时分身跟单也走同一份判定(self, board, client_and_mgr):
        client, mgr = client_and_mgr
        _seed(board, delegation_id="dlg-1", task_id="task-1")
        r = client.post("/api/subagent/delegate",
                        json={"task": "校验第一轮结论并修正错误",
                              "previous_delegation_id": "dlg-1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["ephemeral"] is True
        assert body["round"]["round"] == 2
        assert body["round"]["previous_delegation_id"] == "dlg-1"
        ctx = mgr.delegate_calls[0]["ctx"]
        assert ctx.task_id == "task-1"
        assert ctx.metadata[PREVIOUS_FIELD] == "dlg-1"


# ════════════════════════════════════════════════════════════
#  R6 静态边界
# ════════════════════════════════════════════════════════════


class TestR6StaticBoundary:
    def test_rounds_不导入看板模块(self):
        src = (_REPO_ROOT / "agent" / "subagent" / "rounds.py").read_text(
            encoding="utf-8")
        assert not re.search(
            r"^\s*(?:from|import)\s+agent\.subagent\.task_board", src, re.M), (
            "rounds.py 是纯逻辑模块，不得导入看板（母体唯一写板者边界）")
        assert "task_board" not in "".join(
            line for line in src.splitlines() if line.strip().startswith("import")
            or line.strip().startswith("from")), "导入行里不得出现看板模块"

    def test_键名与看板字段对拍(self):
        fields = TaskRecord.__dataclass_fields__
        assert ROUND_FIELD in fields, "round 不再是看板字段 —— 契约漂移"
        assert PREVIOUS_FIELD in fields, (
            "previous_delegation_id 不再是看板字段 —— 契约漂移")
        assert SUPERSEDED_STATUS in STATUSES
