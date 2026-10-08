"""P4 共享任务看板（agent/subagent/task_board.py + 两处写板咽喉 + HTTP 读面）单元测试

语义：母体是唯一写板者；fan_out / 单发委派**逐任务**写板；看板是 append-only 事件流，
状态由 task_id 折叠（最新一条胜）。本文件把任务书要求的 7 条可证伪守卫逐条钉死：

  G1 双追溯：3 任务批量 ⇒ 板上 3 条，delegation_id / trace_id 与 outcome 对齐；
  G2 分身不直接写板：物化 task_file 不含板路径/键名、子进程 env 不含板写入口、
     静态：task_board 写 API 只被母体侧模块导入（channel.py 与分身侧不导入）；
  G3 追加写不可回改：同 task_id 先 create 后 update，文件前缀字节不变；
  G4 fail-soft：写板失败只在计数器里显式计入，委派语义原样返回；
  G5 失败任务也上板：unavailable outcome ⇒ status=failed 且 error_code 正确；
  G6 无正文：板行不含 output_text / payload / artifacts 正文；
  G7 读面：/api/subagent/history 的 board 段（最小 Flask app + 替身，不 import app_server）。

【不碰仓库 data/】autouse 夹具把 delegation_history 与 task_board 两个进程级单例
都换到 tmp_path；用例之间互不串味，也不写生产流水。
"""
from __future__ import annotations

import json
import os
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
from agent.subagent.task_board import (DEFAULT_PATH, GOAL_MAX_CHARS, TaskBoard,
                                       build_record)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ════════════════════════════════════════════════════════════
#  fixtures / 替身
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_history(tmp_path, monkeypatch):
    """委派流水单例也换到 tmp（看板写板点与它同址，避免污染仓库 data/）"""
    monkeypatch.setattr(
        dh_module, "delegation_history",
        DelegationHistory(path=str(tmp_path / "delegations.jsonl")))


@pytest.fixture
def store(tmp_path, monkeypatch):
    """把进程级 task_board 单例换到 tmp（绝不碰仓库 data/subagent_task_board.jsonl）"""
    board = TaskBoard(path=str(tmp_path / "task_board.jsonl"))
    monkeypatch.setattr(tb_module, "task_board", board)
    return board


def _ctx(*, goal: str = "把 docs 下的设计稿抽取为可复现步骤序列",
         delegation_id: str = "dlg-0001", metadata: dict | None = None,
         tenant_id: str = "default", subject_id: str = "owner") -> DelegationContext:
    return DelegationContext(
        goal=goal, constraints=["只读"], prior_artifacts=[], prohibitions=[],
        artifact_format="文本要点", budget_tokens=4000, timeout_seconds=120,
        callback_url="internal://test", delegation_id=delegation_id,
        tenant_id=tenant_id, subject_id=subject_id, metadata=metadata or {})


@dataclass
class _Outcome:
    ok: bool = True
    delegation_id: str = "dlg-0001"
    trace_id: str = "trace-1"
    trace: object = None
    error_code: str = ""
    error: str = ""
    duration_ms: float = 12.3
    artifacts: tuple = ({"name": "a"},)
    cost: object = None
    # 正文类字段：看板行**绝不能**把它们带出去（G6）
    output_text: str = "SECRET_BODY"
    payload: dict = field(default_factory=lambda: {"result": "SECRET_BODY"})


class _Cost:
    total_tokens = 321


class _FakeExecutor:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def execute(self, ctx, **kw):
        self.calls.append({"ctx": ctx, **kw})
        return self.outcome


# ════════════════════════════════════════════════════════════
#  G1 双追溯：3 任务批量 ⇒ 板上 3 条，delegation_id / trace_id 对齐
# ════════════════════════════════════════════════════════════


class TestG1DualTraceability:
    def test_三任务批量逐条上板且双追溯对齐(self, store):
        from agent.subagent.lifecycle import SubagentLifecycleManager

        mgr = SubagentLifecycleManager(max_subagents=5)
        items, outcomes, containers = [], [], {}
        for i in range(3):
            delegation_id = f"fan-ab12-{i + 1}"
            ctx = _ctx(goal=f"第 {i + 1} 个任务：把该线设计稿抽取为可复现步骤序列",
                       delegation_id=delegation_id,
                       metadata={"line": "dev", "fan_out_index": i,
                                 "fan_out_batch_id": "fan-ab12",
                                 "workspace_id": "ws_test"})
            items.append((object(), ctx))
            containers[i] = type(
                "C", (), {"config": type("Cfg", (), {
                    "name": f"fan-out-ab12-{i + 1}"})()})()
            outcomes.append(_Outcome(
                delegation_id=delegation_id, trace_id=f"trace-{i + 1}",
                trace={"parent_trace_id": f"ptrace-{i + 1}"},
                ok=(i != 1), error_code=("E_DELEGATION_FAILED" if i == 1 else "")))

        mgr._record_batch(items, containers, outcomes, "fan_out")

        board = store.query(10)
        assert len(board) == 3, f"3 任务批量应写 3 条看板记录，实际 {len(board)}"
        by_task = {rec["task_id"]: rec for rec in board}
        for i, (_, ctx) in enumerate(items):
            rec = by_task[ctx.delegation_id]
            assert rec["delegation_id"] == f"fan-ab12-{i + 1}"
            assert rec["trace_id"] == f"trace-{i + 1}", "trace_id 与 outcome 未对齐"
            assert rec["parent_trace_id"] == f"ptrace-{i + 1}"
            assert rec["fan_out_index"] == i
            assert rec["fan_out_batch_id"] == "fan-ab12"
            assert rec["workspace_id"] == "ws_test"
            assert rec["subagent"] == f"fan-out-ab12-{i + 1}"
            assert rec["source"] == "fan_out"
            assert rec["board_op"] == "create"
            assert rec["line"] == "dev"
        assert {r["status"] for r in board} == {"done", "failed"}


# ════════════════════════════════════════════════════════════
#  G2 分身不得直接写板（三路机械验证）
# ════════════════════════════════════════════════════════════


class TestG2NoSubagentWritePath:
    def test_a_物化_task_file_不含板路径或键名(self, tmp_path):
        from agent.subagent.executor import DelegationExecutor

        ex = DelegationExecutor(workspace=str(tmp_path / "ws"))
        path = ex._write_task_file(_ctx(
            goal="把该线设计稿抽取为可复现步骤序列并逐条核验",
            metadata={"line": "dev", "fan_out_batch_id": "fan-ab12",
                      "workspace_id": "ws_test"}))
        text = Path(path).read_text(encoding="utf-8")
        for needle in (DEFAULT_PATH, "subagent_task_board", "task_board", "board_op"):
            assert needle not in text, f"task_file 里出现了看板写入口: {needle}"
        assert not (tmp_path / "ws" / "subagent_task_board.jsonl").exists(), (
            "物化 task_file 的同时把看板文件也带出来了")

    def test_b_子进程环境不含板路径或写入口(self, tmp_path):
        from agent.subagent.executor import DelegationExecutor

        ex = DelegationExecutor(workspace=str(tmp_path / "ws"))
        env = ex._build_env(())
        assert isinstance(env, dict) and env
        # 判据锚在"看板写入口"本身，而不是子串 task_board：测试进程自带的
        # PYTEST_CURRENT_TEST 值里就含本测试文件名（假阳性来源，已实测踩到）。
        for key, value in env.items():
            assert "task_board" not in str(key).lower(), (
                f"子进程环境出现了看板写入口键: {key}")
            text = str(value)
            assert "subagent_task_board.jsonl" not in text, (
                f"子进程环境带上了看板路径值: {key}={text}")
            assert DEFAULT_PATH not in text, (
                f"子进程环境带上了看板默认路径: {key}={text}")

    def test_c_静态断言写_API_只被母体侧模块导入(self):
        write_api = re.compile(
            r"^\s*(?:from\s+agent\.subagent\.task_board\s+import|"
            r"import\s+agent\.subagent\.task_board)", re.MULTILINE)
        roots = [p for p in (_REPO_ROOT / "agent", _REPO_ROOT / "plugins")
                 if p.exists()]
        importers: set[str] = set()
        for base in roots:
            for py in base.rglob("*.py"):
                text = py.read_text(encoding="utf-8", errors="replace")
                if write_api.search(text):
                    importers.add(str(py.relative_to(_REPO_ROOT)).replace(os.sep, "/"))
        # task_board.py 是定义处（不 import 自身），故导入方白名单只列母体侧三处；
        # 分身侧（channel.py 及子进程脚本）不得出现在这里。
        allowed = {
            "agent/subagent/lifecycle.py",
            "agent/subagent/container.py",
            "agent/server_routes/routes_subagent.py",
        }
        assert importers == allowed, (
            f"task_board 写 API 的导入方偏离母体侧白名单: {sorted(importers)}")
        channel = _REPO_ROOT / "agent" / "subagent" / "channel.py"
        assert "task_board" not in channel.read_text(encoding="utf-8"), (
            "分身侧 channel.py 导入了看板写入口")


# ════════════════════════════════════════════════════════════
#  G3 追加写不可回改
# ════════════════════════════════════════════════════════════


class TestG3AppendOnly:
    def test_同_task_id_先_create_后_update_前缀字节不变(self, tmp_path):
        board = TaskBoard(path=str(tmp_path / "b.jsonl"))
        assert board.record_create({"task_id": "t1", "status": "pending",
                                    "board_op": "create"}) is True
        prefix = Path(board.path).read_bytes()
        assert prefix.endswith(b"\n") and b'"board_op": "create"' in prefix

        assert board.record_update({"task_id": "t1", "status": "done",
                                    "board_op": "create"}) is True
        after = Path(board.path).read_bytes()
        assert after.startswith(prefix), "追加写回改了旧行（前缀字节被改写）"
        assert after != prefix
        assert after.count(b"\n") == 2
        # 折叠后只看到最新态（update 覆盖 create）
        folded = board.query(10)
        assert len(folded) == 1
        assert folded[0]["status"] == "done" and folded[0]["board_op"] == "update"


# ════════════════════════════════════════════════════════════
#  G4 fail-soft：写板失败不影响委派语义，且被显式计入
# ════════════════════════════════════════════════════════════


class TestG4FailSoft:
    def test_写板失败委派结果不变且写失败被计数(self, tmp_path, monkeypatch):
        from agent.subagent.container import SubagentConfig, SubagentContainer

        broken = TaskBoard(path=str(tmp_path))  # 路径是目录 ⇒ 必写失败
        monkeypatch.setattr(tb_module, "task_board", broken)
        container = SubagentContainer(SubagentConfig(name="sa-x", model_id="m"))

        outcome = container.run_delegation(_ctx(), executor=_FakeExecutor(_Outcome()))

        assert outcome.ok is True, "看板写失败改变了委派结果（违反 fail-soft）"
        assert broken.write_failed >= 1, "写失败没有被显式计数"
        assert broken.query(5) == []

    def test_写失败计数在快照里如实暴露(self, tmp_path):
        broken = TaskBoard(path=str(tmp_path))
        assert broken.record_outcome(ctx=_ctx(), outcome=_Outcome()) is False
        snap = broken.snapshot()
        assert snap["records"] == [] and snap["write_failed"] == 1


# ════════════════════════════════════════════════════════════
#  G5 失败任务也上板
# ════════════════════════════════════════════════════════════


class TestG5FailedTasksOnBoard:
    def test_unavailable_批次任务_status_failed_且错误码正确(self, store):
        from agent.subagent.lifecycle import (E_SUBAGENT_UNAVAILABLE,
                                              SubagentLifecycleManager)

        mgr = SubagentLifecycleManager(max_subagents=5)
        ctxs = [_ctx(goal="第 1 个任务：设计稿抽取为步骤序列", delegation_id="fan-b-1"),
                _ctx(goal="第 2 个任务：设计稿抽取为步骤序列", delegation_id="fan-b-2")]
        items = [(object(), c) for c in ctxs]
        unavailable = mgr._unavailable_outcome(ctxs[1], "分身容量上限")
        assert unavailable.error_code == E_SUBAGENT_UNAVAILABLE
        results = [_Outcome(delegation_id="fan-b-1"), unavailable]

        mgr._record_batch(items, {}, results, "fan_out")

        board = {r["task_id"]: r for r in store.query(10)}
        assert set(board) == {"fan-b-1", "fan-b-2"}
        assert board["fan-b-1"]["status"] == "done"
        assert board["fan-b-2"]["status"] == "failed"
        assert board["fan-b-2"]["error_code"] == E_SUBAGENT_UNAVAILABLE
        assert board["fan-b-2"]["ok"] is False


# ════════════════════════════════════════════════════════════
#  G6 无正文
# ════════════════════════════════════════════════════════════


class TestG6NoBody:
    def test_板行不含交付物正文与载荷(self, tmp_path):
        rec = build_record(_ctx(), _Outcome(cost=_Cost()), source="tool",
                           subagent="delegate-x")
        for banned in ("output_text", "payload", "result", "artifacts"):
            assert banned not in rec, f"板行带上了正文字段: {banned}"
        line = json.dumps(rec, ensure_ascii=False)
        assert "SECRET_BODY" not in line, "板行泄漏了交付物正文"
        assert rec["artifact_count"] == 1, "只记数量，不记正文"
        assert rec["tokens"] == 321
        assert rec["goal"] and len(rec["goal"]) <= GOAL_MAX_CHARS

    def test_目标超长被截断(self):
        rec = build_record(_ctx(goal="目" * 500), _Outcome())
        assert len(rec["goal"]) == GOAL_MAX_CHARS


# ════════════════════════════════════════════════════════════
#  G7 读面：/api/subagent/history 的 board 段
# ════════════════════════════════════════════════════════════


class _FakeYunshu:
    _subagent_mgr = None
    _llm = None

    def list_subagents(self):
        return []


class TestG7HistoryBoardSegment:
    def test_board_段给出折叠后最新态_不新增路由(self, tmp_path, monkeypatch):
        board = TaskBoard(path=str(tmp_path / "board.jsonl"))
        board.record_create({"task_id": "t1", "board_op": "create",
                             "status": "running", "goal": "任务一"})
        board.record_update({"task_id": "t1", "board_op": "update",
                             "status": "done", "goal": "任务一"})
        board.record_create({"task_id": "t2", "board_op": "create",
                             "status": "failed", "error_code": "E_X"})
        monkeypatch.setattr(tb_module, "task_board", board)
        monkeypatch.setattr(routes_module, "task_board", board)
        monkeypatch.setattr(routes_module, "delegation_history",
                            DelegationHistory(path=str(tmp_path / "d.jsonl")))

        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, type("S", (), {"Yunshu": _FakeYunshu()})())
        client = app.test_client()

        rules = {str(r.rule) for r in app.url_map.iter_rules()}
        assert "/api/subagent/history" in rules
        assert "/api/subagent/board" not in rules, "board 段不得新增路由"

        body = client.get("/api/subagent/history").get_json()
        assert body["ok"] is True
        seg = body["board"]
        assert seg["total"] == 3, "total 应为未折叠的原始事件数"
        assert seg["count"] == 2, "count 应为折叠后的任务数"
        assert seg["write_failed"] == 0
        by_task = {r["task_id"]: r for r in seg["records"]}
        assert set(by_task) == {"t1", "t2"}
        assert by_task["t1"]["status"] == "done"
        assert by_task["t1"]["board_op"] == "update"
        assert by_task["t2"]["status"] == "failed"


# ════════════════════════════════════════════════════════════
#  额外：默认路径锚在仓库根（与 delegation_history 同款的层数守卫）
# ════════════════════════════════════════════════════════════


class TestDefaultPath:
    def test_默认路径落在仓库_data_下(self):
        assert os.path.isabs(DEFAULT_PATH)
        assert DEFAULT_PATH.endswith(
            os.path.join("data", "subagent_task_board.jsonl"))
        repo_root = os.path.dirname(os.path.dirname(DEFAULT_PATH))
        assert os.path.isfile(os.path.join(repo_root, "app_server.py")), (
            f"默认看板路径不在仓库根 data/ 下（推导层数可能写错）: {DEFAULT_PATH}")
