# -*- coding: utf-8 -*-
"""cognitive_loop 跨 workspace 共享教训池（一期 MVP）单测。

覆盖设计不变量：I1 隔离 / I3 taint / I4 fail-closed / I5 可遗忘，以及双上限与幂等去重。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.subagent import cognitive_lessons as cl  # noqa: E402


def _env(tmp_path: Path, enabled: bool = True) -> dict:
    return {
        cl.ENV_SHARED: "1" if enabled else "0",
        cl.ENV_DIR: str(tmp_path),
    }


def _rows(tmp_path: Path) -> list:
    path = tmp_path / cl.LESSONS_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Test开关与作用域:
    def test_默认关时不读不写(self, tmp_path):
        env = _env(tmp_path, enabled=False)
        with cl.scope_context("t1", "s1"):
            assert cl.record_lesson("sig", "advice", environ=env) is None
            assert cl.recall_shared("sig", environ=env) == []
        assert not (tmp_path / cl.LESSONS_FILENAME).exists()

    def test_开启但无作用域时不读不写(self, tmp_path):
        env = _env(tmp_path)
        assert cl.record_lesson("sig", "advice", environ=env) is None
        assert cl.recall_shared("sig", environ=env) == []

    def test_作用域缺失任一项视为无作用域(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", ""):
            assert cl.current_scope() is None
            assert cl.record_lesson("sig", "advice", environ=env) is None


class Test隔离:
    def test_跨主体不可见(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            assert cl.record_lesson("sig", "A 的建议", environ=env)
        with cl.scope_context("t1", "s2"):
            assert cl.recall_shared("sig", environ=env) == []
        with cl.scope_context("t1", "s1"):
            hits = cl.recall_shared("sig", environ=env)
        assert [h["advice"] for h in hits] == ["A 的建议"]

    def test_跨租户不可见(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "tenant1", environ=env)
        with cl.scope_context("t2", "s1"):
            assert cl.recall_shared("sig", environ=env) == []

    def test_不同签名不互相命中(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig-a", "for-a", environ=env)
            cl.record_lesson("sig-b", "for-b", environ=env)
            assert [h["advice"] for h in cl.recall_shared("sig-a", environ=env)] == ["for-a"]


class Test双上限与幂等:
    def test_advice_超长被截断(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "x" * (cl.ADVICE_CHAR_CAP + 100), environ=env)
            hits = cl.recall_shared("sig", environ=env)
        assert len(hits[0]["advice"]) == cl.ADVICE_CHAR_CAP

    def test_回读条数上限(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            for i in range(cl.RECALL_COUNT_CAP + 3):
                cl.record_lesson("sig", "advice-%d" % i, environ=env)
            hits = cl.recall_shared("sig", environ=env)
        assert len(hits) == cl.RECALL_COUNT_CAP

    def test_字符预算截断(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "a" * 10, environ=env)
            cl.record_lesson("sig", "b" * 10, environ=env)
            hits = cl.recall_shared("sig", char_budget=12, environ=env)
        assert len(hits) == 1

    def test_同内容重复确认不产生第二条(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            first = cl.record_lesson("sig", "same", environ=env)
            second = cl.record_lesson("sig", "same", environ=env)
        assert first == second
        rows = _rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["confirmations"] == 2


class Testtaint与可遗忘与failclosed:
    def test_回读带_upstream_taint(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "advice", environ=env)
            hits = cl.recall_shared("sig", environ=env)
        assert hits[0]["taint"] == cl.TAINT

    def test_erase_后不再注入(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            lid = cl.record_lesson("sig", "advice", environ=env)
            assert cl.erase(lid, environ=env) == 1
            assert cl.recall_shared("sig", environ=env) == []
            assert cl.erase(lid, environ=env) == 0

    def test_erase_受作用域约束(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            lid = cl.record_lesson("sig", "advice", environ=env)
        with cl.scope_context("t1", "s2"):
            assert cl.erase(lid, environ=env) == 0
        with cl.scope_context("t1", "s1"):
            assert len(cl.recall_shared("sig", environ=env)) == 1

    def test_TTL_过期不注入(self, tmp_path):
        env = _env(tmp_path)
        past = datetime.now(timezone.utc) - timedelta(days=cl.DEFAULT_TTL_DAYS + 1)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "old", environ=env, now=past)
            assert cl.recall_shared("sig", environ=env) == []

    def test_文件损坏时回读为空且写入不覆盖(self, tmp_path):
        env = _env(tmp_path)
        path = tmp_path / cl.LESSONS_FILENAME
        path.write_text("{ not json", encoding="utf-8")
        with cl.scope_context("t1", "s1"):
            assert cl.recall_shared("sig", environ=env) == []
            assert cl.record_lesson("sig", "advice", environ=env) is None
        assert path.read_text(encoding="utf-8") == "{ not json"

    def test_目录未配置时不写(self):
        env = {cl.ENV_SHARED: "1", cl.ENV_DIR: ""}
        with cl.scope_context("t1", "s1"):
            assert cl.record_lesson("sig", "advice", environ=env) is None

class Test接线到cognitive_loop:
    """共享池与 cognitive_loop._recall/_remember 的接线（经 os.environ 开关）。"""

    def _inv(self, tmp_path):
        import types
        return types.SimpleNamespace(task_file=str(tmp_path / "task.json"))

    def test_recall_合并共享池(self, tmp_path, monkeypatch):
        from agent.subagent import cognitive_loop as loop
        monkeypatch.setenv(cl.ENV_SHARED, "1")
        monkeypatch.setenv(cl.ENV_DIR, str(tmp_path))
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "共享建议", environ=None)
        with cl.scope_context("t1", "s1"):
            rows = loop._recall(self._inv(tmp_path))
        assert any(r.get("advice") == "共享建议" for r in rows)

    def test_recall_无作用域时不带共享(self, tmp_path, monkeypatch):
        from agent.subagent import cognitive_loop as loop
        monkeypatch.setenv(cl.ENV_SHARED, "1")
        monkeypatch.setenv(cl.ENV_DIR, str(tmp_path))
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "共享建议", environ=None)
        rows = loop._recall(self._inv(tmp_path))
        assert all(r.get("advice") != "共享建议" for r in rows)

    def test_remember_写共享池(self, tmp_path, monkeypatch):
        from agent.subagent import cognitive_loop as loop
        monkeypatch.setenv(cl.ENV_SHARED, "1")
        monkeypatch.setenv(cl.ENV_DIR, str(tmp_path))
        with cl.scope_context("t1", "s1"):
            loop._remember(self._inv(tmp_path), {"goal": "g"},
                           {"verdict": "revise", "issues": ["boom"], "suggestion": "换个做法"})
            hits = cl.recall_shared("boom", environ=None)
        assert [h["advice"] for h in hits] == ["换个做法"]

