"""E2-1 运行期登记表：登记/注销/快照、inbox 追加与 drain、线程绑定"""
from __future__ import annotations

import json
import os

import pytest

from agent.subagent.runs import RunRegistry, current_run, run_registry


@pytest.fixture()
def reg(tmp_path):
    return RunRegistry(root=str(tmp_path / "runs"),
                       snapshot_path=str(tmp_path / "runs.json"))


def test_register_and_ensure_dedupe(reg):
    h1 = reg.register(subagent="alpha", delegation_id="dlg-1")
    assert h1.run_id.startswith("r-") and len(h1.run_id) == 14
    h2 = reg.register(subagent="beta", delegation_id="dlg-1")
    assert h2 is h1, "同一 delegation 不得登记出两个 run"
    h3 = reg.ensure("dlg-1", "gamma")
    assert h3 is h1
    h4 = reg.ensure("dlg-2", "gamma")
    assert h4.run_id != h1.run_id


def test_list_runs_filter_and_order(reg):
    a = reg.register(subagent="a", delegation_id="d-a")
    b = reg.register(subagent="b", delegation_id="d-b")
    reg.finish(a, "done")
    running = reg.list_runs(status="running")
    assert [r["run_id"] for r in running] == [b.run_id]
    every = reg.list_runs(status="all")
    assert {r["run_id"] for r in every} == {a.run_id, b.run_id}
    done = reg.list_runs(status="done")
    assert [r["run_id"] for r in done] == [a.run_id]


def test_inbox_append_and_drain_cursor(reg):
    h = reg.register(subagent="a", delegation_id="d-1")
    assert reg.append_inbox(h.run_id, "第一条") is not None
    assert reg.append_inbox(h.run_id, "   ") is None, "空消息不得写入"
    assert reg.append_inbox("r-missing", "x") is None
    first = reg.drain_inbox(h)
    assert [r["text"] for r in first] == ["第一条"]
    assert reg.drain_inbox(h) == [], "drain 后不应重复消费"
    reg.append_inbox(h.run_id, "第二条")
    assert [r["text"] for r in reg.drain_inbox(h)] == ["第二条"]
    # 只读查看不消费
    assert [r["text"] for r in reg.inbox(h.run_id)] == ["第一条", "第二条"]


def test_inbox_file_is_append_only_jsonl(reg):
    h = reg.register(subagent="a", delegation_id="d-1")
    reg.append_inbox(h.run_id, "one")
    reg.append_inbox(h.run_id, "two")
    lines = [ln for ln in open(h.inbox_path, encoding="utf-8").read().splitlines() if ln.strip()]
    assert len(lines) == 2
    rows = [json.loads(ln) for ln in lines]
    assert rows[0]["text"] == "one" and rows[1]["text"] == "two"
    assert rows[0]["kind"] == "user" and rows[0]["at"]


def test_finish_interrupted_and_snapshot(reg):
    h = reg.register(subagent="a", delegation_id="d-1")
    reg.finish(h, "interrupted", "需求变了")
    assert h.status == "interrupted" and h.reason == "需求变了"
    payload = reg.snapshot()
    assert payload["count"] == 1 and payload["running"] == 0
    with open(reg._snapshot_path, encoding="utf-8") as fh:
        disk = json.load(fh)
    assert disk["runs"][0]["run_id"] == h.run_id
    assert disk["runs"][0]["status"] == "interrupted"


def test_cancel_event_and_prune(reg):
    h = reg.register(subagent="a", delegation_id="d-1")
    assert not h.cancel_event.is_set()
    h.cancel_event.set()
    assert h.to_dict()["cancelled"] is True
    reg.finish(h, "done")
    h.started_at = 0.0  # 造一个过期已结束 run
    assert reg.prune(ttl_seconds=1) == 1
    assert reg.get(h.run_id) is None
    # 运行中的 run 永不被 prune 清掉
    live = reg.register(subagent="b", delegation_id="d-2")
    live.started_at = 0.0
    assert reg.prune(ttl_seconds=1) == 0
    assert reg.get(live.run_id) is not None


def test_thread_binding_roundtrip(tmp_path):
    local = RunRegistry(root=str(tmp_path / "r"), snapshot_path=str(tmp_path / "s.json"))
    h = local.register(subagent="a", delegation_id="d")
    assert local.current() is None
    with local.bound(h):
        assert local.current() is h
    assert local.current() is None
    # 进程级单例的只读入口不抛
    assert current_run() is run_registry.current()
