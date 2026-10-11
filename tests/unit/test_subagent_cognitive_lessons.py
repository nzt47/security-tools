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


def _env_inst(tmp_path: Path, inst: str, n=None) -> dict:
    env = _env(tmp_path)
    env[cl.ENV_INSTANCE_ID] = inst
    if n is not None:
        env[cl.ENV_PROMOTION_N] = str(n)
    return env


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

    def test_同内容重复不产生第二条且同实例不自证(self, tmp_path):
        env = _env_inst(tmp_path, "A")
        with cl.scope_context("t1", "s1"):
            first = cl.record_lesson("sig", "same", environ=env)
            second = cl.record_lesson("sig", "same", environ=env)
        assert first == second
        rows = _rows(tmp_path)
        assert len(rows) == 1
        # 二期口径：confirmations = **不同 instance_id** 的独立确认数；同一实例反复记录不自证
        assert rows[0]["confirmations"] == 1
        assert rows[0]["confirmed_by"] == ["A"]
        assert rows[0]["state"] == cl.STATE_CANDIDATE


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


class TestInstanceId口径:
    """二期：instance_id = 部署实例稳定标识，只用于独立确认去重。"""

    def test_显式环境变量优先并去空白(self):
        assert cl.instance_id({cl.ENV_INSTANCE_ID: "  node-a  "}) == "node-a"

    def test_兜底稳定且非空(self):
        a = cl.instance_id({})
        b = cl.instance_id({})
        assert a and a == b and a.startswith("host-")


class Test提升闸门:
    """I2：candidate→shared 需 N 个不同 instance_id 的独立确认，或人工批准。"""

    def test_同实例反复记录不自证(self, tmp_path):
        env = _env_inst(tmp_path, "A", n=3)
        with cl.scope_context("t1", "s1"):
            for _ in range(5):
                cl.record_lesson("sig", "advice", environ=env)
        row = _rows(tmp_path)[0]
        assert row["confirmations"] == 1
        assert row["confirmed_by"] == ["A"]
        assert row["state"] == cl.STATE_CANDIDATE

    def test_确认数N减1不进shared(self, tmp_path):
        with cl.scope_context("t1", "s1"):
            for inst in ("A", "B"):
                cl.record_lesson("sig", "advice", environ=_env_inst(tmp_path, inst, n=3))
        row = _rows(tmp_path)[0]
        assert row["confirmations"] == 2
        assert row["state"] == cl.STATE_CANDIDATE

    def test_第N个不同实例自动进shared(self, tmp_path):
        with cl.scope_context("t1", "s1"):
            for inst in ("A", "B", "C"):
                cl.record_lesson("sig", "advice", environ=_env_inst(tmp_path, inst, n=3))
        row = _rows(tmp_path)[0]
        assert row["confirmed_by"] == ["A", "B", "C"]
        assert row["confirmations"] == 3
        assert row["state"] == cl.STATE_SHARED
        assert row.get("promoted_at")

    def test_N为0只走人工批准(self, tmp_path):
        with cl.scope_context("t1", "s1"):
            for inst in ("A", "B", "C", "D"):
                cl.record_lesson("sig", "advice", environ=_env_inst(tmp_path, inst, n=0))
        assert _rows(tmp_path)[0]["state"] == cl.STATE_CANDIDATE

    def test_人工批准提升且受作用域约束(self, tmp_path):
        env = _env_inst(tmp_path, "A", n=0)
        with cl.scope_context("t1", "s1"):
            lid = cl.record_lesson("sig", "advice", environ=env)
        with cl.scope_context("t1", "s2"):
            assert cl.promote(lid, approver="ops", environ=env) is False  # 别的 subject 越权 ⇒ 拒
        with cl.scope_context("t1", "s1"):
            assert cl.promote(lid, approver="ops", environ=env) is True
            hits = cl.recall_shared("sig", environ=env)
        assert hits[0]["state"] == cl.STATE_SHARED
        assert _rows(tmp_path)[0]["approved_by"] == "ops"

    def test_shared在回读中排前(self, tmp_path):
        with cl.scope_context("t1", "s1"):
            for inst in ("A", "B", "C"):
                cl.record_lesson("sig", "promoted", environ=_env_inst(tmp_path, inst, n=3))
            cl.record_lesson("sig", "candidate", environ=_env_inst(tmp_path, "A", n=3))
            hits = cl.recall_shared("sig", environ=_env_inst(tmp_path, "A", n=3))
        assert hits[0]["advice"] == "promoted"
        assert hits[0]["state"] == cl.STATE_SHARED


class Test旧行兼容:
    def test_一期行按candidate单实例保守解释(self, tmp_path):
        env = _env(tmp_path)
        old = {
            "schema_version": 1, "lesson_id": "legacy-1", "tenant": "t1", "subject": "s1",
            "signature": "sig", "advice": "old", "confidence": 0.9, "confirmations": 7,
            "created_at": datetime.now(timezone.utc).isoformat(), "ttl_days": 90,
            "taint": cl.TAINT,
        }
        (tmp_path / cl.LESSONS_FILENAME).write_text(
            json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
        with cl.scope_context("t1", "s1"):
            hits = cl.recall_shared("sig", environ=env)
        assert hits[0]["advice"] == "old"
        assert hits[0]["confirmations"] == 1  # 一期 confirmations 是"次数"，不做独立实例数用
        assert hits[0]["state"] == cl.STATE_CANDIDATE


class TestCLI:
    def _cli(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cog_lessons_cli", str(ROOT / "scripts" / "subagent_cognitive_lessons.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_人工批准经CLI(self, tmp_path):
        env = _env_inst(tmp_path, "A", n=0)
        with cl.scope_context("t1", "s1"):
            lid = cl.record_lesson("sig", "advice", environ=env)
        rc = self._cli().main(["--tenant", "t1", "--subject", "s1", "--dir", str(tmp_path),
                               "promote", lid, "--approver", "ops"])
        assert rc == 0
        assert _rows(tmp_path)[0]["state"] == cl.STATE_SHARED

    def test_缺失目录被拒(self, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_COGNITIVE_LESSONS_DIR", raising=False)
        rc = self._cli().main(["--tenant", "t1", "--subject", "s1", "--dir", "", "list"])
        assert rc == 2

class Test导出导入:
    """三期：教训池与 memory_ops 同口径导出/导入（域强制、幂等合并）"""

    def _cli(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "cl_cli_ops", ROOT / "scripts" / "subagent_cognitive_lessons.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_export_只含当前作用域(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig-a", "for-a", environ=env)
        with cl.scope_context("t1", "s2"):
            cl.record_lesson("sig-b", "for-b", environ=env)
        with cl.scope_context("t1", "s1"):
            rows = cl.list_lessons(environ=env)
        assert [r["signature"] for r in rows] == ["sig-a"]
        assert rows[0]["state"] == cl.STATE_CANDIDATE

    def test_import_强制目标作用域并幂等(self, tmp_path):
        env = _env(tmp_path)
        foreign = [{"lesson_id": "deadbeef", "tenant": "other", "subject": "other",
                    "signature": "sig-x", "advice": "advice-x", "confidence": 0.9,
                    "confirmed_by": ["inst-a"], "state": cl.STATE_SHARED}]
        with cl.scope_context("t1", "s1"):
            assert cl.import_lessons(foreign, environ=env) == 1
            assert cl.import_lessons(foreign, environ=env) == 1   # 幂等：不新增第二条
            rows = cl.list_lessons(environ=env)
        assert len(rows) == 1
        assert rows[0]["tenant"] == "t1" and rows[0]["subject"] == "s1"
        assert rows[0]["lesson_id"] != "deadbeef", "外来 lesson_id 不可信，必须重算"
        assert rows[0]["state"] == cl.STATE_SHARED

    def test_import_跳过空signature或advice(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            assert cl.import_lessons([{"signature": "", "advice": "x"},
                                      {"signature": "y", "advice": "   "},
                                      "not-a-mapping"], environ=env) == 0
            assert cl.list_lessons(environ=env) == []

    def test_import_合并确认人与状态(self, tmp_path):
        env = _env_inst(tmp_path, "inst-a", n=3)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "advice", environ=env)
        row = _rows(tmp_path)[0]
        merged = dict(row, confirmed_by=["inst-b"], instance_id="inst-b",
                      state=cl.STATE_SHARED, confidence=0.9)
        with cl.scope_context("t1", "s1"):
            assert cl.import_lessons([merged], environ=env) == 1
            rows = cl.list_lessons(environ=env)
        assert rows[0]["confirmed_by"] == ["inst-a", "inst-b"]
        assert rows[0]["state"] == cl.STATE_SHARED
        assert rows[0]["confidence"] >= 0.9

    def test_cli_导出导入往返(self, tmp_path):
        env = _env(tmp_path)
        with cl.scope_context("t1", "s1"):
            cl.record_lesson("sig", "advice", environ=env)
        cli = self._cli()
        dump = tmp_path / "dump.json"
        assert cli.main(["--tenant", "t1", "--subject", "s1", "--dir", str(tmp_path),
                         "export", "--out", str(dump)]) == 0
        assert dump.exists()
        other = tmp_path / "other"
        other.mkdir()
        assert cli.main(["--tenant", "t2", "--subject", "s2", "--dir", str(other),
                         "import", str(dump)]) == 0
        env2 = {"CP_SUBAGENT_COGNITIVE_SHARED_LESSONS": "1",
                "CP_SUBAGENT_COGNITIVE_LESSONS_DIR": str(other)}
        with cl.scope_context("t2", "s2"):
            rows = cl.list_lessons(environ=env2)
        assert len(rows) == 1
        assert rows[0]["tenant"] == "t2" and rows[0]["subject"] == "s2"


