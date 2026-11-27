"""E4-2：脚本编排 run 的只读投影（索引 / checkpoint）。

判据（docs/plan/2026-11-27-p4-编排安全设计.md §12.3）：
  - 索引行只放事实（run_id/会话/结果码/耗时/调用数/白名单规模），**不放脚本源码与 stdout**；
  - checkpoint 落在 run_dir 里，可由 read_file 直接读；
  - 读失败回空块（结果面坏了不影响任务列表）。
"""
from __future__ import annotations

import json

from agent.orchestration import runs


def _entry(**kw):
    base = {"run_id": "run-1", "line": "engineering", "ok": True, "code": "",
            "duration_ms": 10, "calls": 1, "spawned": 0, "denied": 0,
            "session_id": "sess-1", "whitelist_size": 2,
            "limits": {"timeout_sec": 120, "max_calls": 50},
            "script": "BODY", "stderr_tail": "noise"}
    base.update(kw)
    return base


class TestRunIndex:
    def test_写入与读取(self, tmp_path):
        assert runs.record_run(_entry(), root=tmp_path) is True
        data = runs.read_runs(root=tmp_path)
        assert data["total"] == 1
        row = data["runs"][0]
        assert row["run_id"] == "run-1"
        assert row["whitelist_size"] == 2
        assert row["limits"]["max_calls"] == 50

    def test_索引不含脚本与_stderr(self, tmp_path):
        runs.record_run(_entry(), root=tmp_path)
        raw = (tmp_path / runs.INDEX_NAME).read_text(encoding="utf-8")
        assert "BODY" not in raw and "noise" not in raw
        assert "script" not in raw and "stderr_tail" not in raw

    def test_新的在前_可按会话过滤(self, tmp_path):
        runs.record_run(_entry(run_id="run-1", session_id="a"), root=tmp_path)
        runs.record_run(_entry(run_id="run-2", session_id="b"), root=tmp_path)
        runs.record_run(_entry(run_id="run-3", session_id="a"), root=tmp_path)
        all_rows = runs.read_runs(root=tmp_path)
        assert [r["run_id"] for r in all_rows["runs"]] == ["run-3", "run-2", "run-1"]
        only_a = runs.read_runs(root=tmp_path, session_id="a")
        assert [r["run_id"] for r in only_a["runs"]] == ["run-3", "run-1"]

    def test_空_run_id_不写(self, tmp_path):
        assert runs.record_run(_entry(run_id=""), root=tmp_path) is False
        assert runs.read_runs(root=tmp_path)["total"] == 0

    def test_统计失败码分布(self, tmp_path):
        runs.record_run(_entry(run_id="a"), root=tmp_path)
        runs.record_run(_entry(run_id="b", ok=False, code="timeout"), root=tmp_path)
        st = runs.stats(root=tmp_path)
        assert st["total"] == 2
        assert st["codes"]["ok"] == 1 and st["codes"]["timeout"] == 1
        assert st["last_ts"]

    def test_坏行被跳过(self, tmp_path):
        path = runs.index_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json}\n" + json.dumps(
            {"run_id": "ok-1", "ok": True}, ensure_ascii=False) + "\n", encoding="utf-8")
        data = runs.read_runs(root=tmp_path)
        assert data["total"] == 1 and data["runs"][0]["run_id"] == "ok-1"


class TestCheckpoint:
    def test_写入_run_json_不含脚本(self, tmp_path):
        payload = {"ok": True, "code": "", "calls": 1,
                   "script": "SECRET", "stderr_tail": "x" * 9000}
        assert runs.write_checkpoint(tmp_path / "run-1", payload) is True
        data = json.loads((tmp_path / "run-1" / runs.CHECKPOINT_NAME).read_text(encoding="utf-8"))
        assert "script" not in data
        assert len(data["stderr_tail"]) <= 2000

    def test_写失败不抛(self, tmp_path):
        blocker = tmp_path / "blocked"
        blocker.write_text("file", encoding="utf-8")
        assert runs.write_checkpoint(blocker, {"ok": True}) is False


class TestReadCheckpoint:
    """E4-2③：run 详情（同一 GET 的 ?run_id=）的读侧白名单与路径安全"""

    def _write(self, root, run_id="run-x", **payload):
        body = {"run_id": run_id, "ok": True, "code": "", "calls": 2,
                "script": "SECRET-BODY", "stderr_tail": "y" * 9000}
        body.update(payload)
        target = root / run_id
        target.mkdir(parents=True, exist_ok=True)
        (target / runs.CHECKPOINT_NAME).write_text(
            json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return target

    def test_读回白名单字段_不含脚本(self, tmp_path):
        self._write(tmp_path, session_id="s1", state_ops=3)
        got = runs.read_checkpoint("run-x", root=tmp_path)
        assert got is not None
        assert got["run_id"] == "run-x" and got["calls"] == 2
        assert got["session_id"] == "s1" and got["state_ops"] == 3
        assert "script" not in got
        assert len(got["stderr_tail"]) <= 4000

    def test_不存在的_run_回_none(self, tmp_path):
        assert runs.read_checkpoint("run-missing", root=tmp_path) is None

    def test_目录名不匹配_不做路径拼接(self, tmp_path):
        # 名字里带穿越/盘符也只会走「等值匹配」失败分支，不会去读目录外文件
        for bad in ("../run-x", "run-x/../../etc", "", "state", "C:\\Windows"):
            assert runs.read_checkpoint(bad, root=tmp_path) is None

    def test_坏_json_回_none(self, tmp_path):
        target = tmp_path / "run-bad"
        target.mkdir(parents=True)
        (target / runs.CHECKPOINT_NAME).write_text("{oops", encoding="utf-8")
        assert runs.read_checkpoint("run-bad", root=tmp_path) is None

    def test_write_checkpoint_补时间戳(self, tmp_path):
        runs.write_checkpoint(tmp_path / "run-1", {"ok": True})
        data = json.loads((tmp_path / "run-1" / runs.CHECKPOINT_NAME).read_text(encoding="utf-8"))
        assert data.get("ts")

