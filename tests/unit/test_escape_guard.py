"""TASK-S2-03 逃逸检测（受管任务文件）单元测试

覆盖范围：
- 受控写台账（登记 / 查询 / 基线）
- 首次见到存量文件 → 只登记基线，**不误报逃逸**
- 受控写后内容一致 → clean
- 内容变更且未经受控写 → escape（reason / capability_id / digest 前后）
- 幂等：同一 (path, 新摘要) 只计一次（重放不重复计数）
- 变更任务 id 定位（``changed_task_ids`` → escape.task_id）
- 文件消失（detect_missing）
- 检测过程**只读**（绝不改写用户改动）
- 总开关 CP_ESCAPE_GUARD=0
"""

import json
import os

import pytest

from agent.observability import escape as G
from agent.observability import events as ev


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(ev.ENV_DIR, str(tmp_path / "events"))
    monkeypatch.setenv(G.ENV_GUARD, "1")
    monkeypatch.delenv(G.ENV_WATCH, raising=False)
    ev.reset_event_stores()
    yield
    ev.reset_event_stores()


@pytest.fixture
def task_file(tmp_path):
    path = tmp_path / "scheduled_tasks.json"
    path.write_text(json.dumps({"tasks": [{"id": "t1", "command": "echo hi"}]}),
                    encoding="utf-8")
    return str(path)


@pytest.fixture
def ledger(tmp_path):
    """台账置于事件目录内（与默认 `ledger_path()` 同构）"""
    (tmp_path / "events").mkdir(parents=True, exist_ok=True)
    return G.GovernedWriteLedger(str(tmp_path / "events" / "ledger.jsonl"))


def _write(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _statuses(results):
    return [row["status"] for row in results]


# ════════════════════════════════════════════════════════════
#  1. 台账与指纹
# ════════════════════════════════════════════════════════════


class TestLedger:
    def test_record_and_last(self, task_file, ledger):
        entry = ledger.record(task_file, writer="create_scheduled_task")
        assert entry["sha256"] and entry["writer"] == "create_scheduled_task"
        assert ledger.last(task_file)["sha256"] == entry["sha256"]

    def test_entries_filter_by_path(self, task_file, ledger, tmp_path):
        other = tmp_path / "other.json"
        other.write_text("{}", encoding="utf-8")
        ledger.record(task_file, writer="a")
        ledger.record(str(other), writer="b")
        assert len(ledger.entries()) == 2
        assert len(ledger.entries(task_file)) == 1

    def test_last_none_when_unseen(self, task_file, ledger):
        assert ledger.last(task_file) is None

    def test_file_digest_missing(self, tmp_path):
        info = G.file_digest(str(tmp_path / "nope"))
        assert info == {"exists": False, "sha256": "", "size": 0}

    def test_file_digest_stable(self, task_file):
        assert G.file_digest(task_file)["sha256"] == G.file_digest(task_file)["sha256"]

    def test_guard_disabled_skips_recording(self, task_file, ledger, monkeypatch):
        monkeypatch.setenv(G.ENV_GUARD, "0")
        assert ledger.record(task_file, writer="x") is None
        assert ledger.entries() == []

    def test_governed_capability_mapping(self):
        assert G.governed_capability("data/scheduled_tasks.json") == \
            "cp.governed.scheduled_task.write"
        assert G.governed_capability("C:/x/whatever.json").startswith("cp.governed.")

    def test_governed_write_context_manager(self, task_file, ledger, monkeypatch):
        monkeypatch.setattr(G, "get_ledger", lambda: ledger)
        with G.governed_write(task_file, writer="w1"):
            pass
        assert ledger.last(task_file)["writer"] == "w1"

    def test_watched_files_default_and_env(self, monkeypatch):
        assert any(p.endswith("scheduled_tasks.json")
                   for p in G.watched_files())
        monkeypatch.setenv(G.ENV_WATCH, os.pathsep.join(["a.json", "b.json"]))
        assert len(G.watched_files()) == 2


# ════════════════════════════════════════════════════════════
#  2. 检测生命周期
# ════════════════════════════════════════════════════════════


class TestDetection:
    def test_first_sight_is_baseline_not_escape(self, task_file, ledger):
        results = G.detect_escapes([task_file], ledger=ledger)
        assert _statuses(results) == ["baseline"]
        assert results[0]["emitted"] is False
        assert ev.read_events(types=["escape"],
                              directory=os.path.dirname(ledger.path)) == []

    def test_governed_write_then_clean(self, task_file, ledger):
        ledger.record(task_file, writer="create_scheduled_task")
        assert _statuses(G.detect_escapes([task_file], ledger=ledger)) == ["clean"]

    def test_manual_edit_detected_as_escape(self, task_file, ledger):
        ledger.record(task_file, writer="create_scheduled_task")
        _write(task_file, {"tasks": [{"id": "evil", "command": "rm -rf /"}]})
        results = G.detect_escapes([task_file], ledger=ledger)
        assert _statuses(results) == ["escape"]
        row = results[0]
        assert row["reason"] == G.REASON_UNTRACKED_CHANGE
        assert row["capability_id"] == "cp.governed.scheduled_task.write"
        assert row["digest_before"] != row["digest_after"]
        assert row["emitted"] is True

    def test_escape_event_payload_fields(self, task_file, ledger):
        ledger.record(task_file, writer="create_scheduled_task")
        _write(task_file, {"tasks": [{"id": "evil"}]})
        G.detect_escapes([task_file], ledger=ledger)
        env = ev.read_events(types=["escape"],
                             directory=os.path.dirname(ledger.path))[0]
        for field in ("task_id", "reason", "capability_id", "path",
                      "digest_before", "digest_after"):
            assert field in env.payload, field
        assert env.payload["reason"] == G.REASON_UNTRACKED_CHANGE

    def test_escape_idempotent_on_repeat_detection(self, task_file, ledger):
        ledger.record(task_file, writer="w")
        _write(task_file, {"tasks": [{"id": "evil"}]})
        first = G.detect_escapes([task_file], ledger=ledger)
        second = G.detect_escapes([task_file], ledger=ledger)
        assert first[0]["emitted"] is True
        assert second[0]["emitted"] is False
        base = os.path.dirname(ledger.path)
        assert len(ev.read_events(types=["escape"], directory=base)) == 1
        assert len(ev.read_events(types=["intervention"], directory=base)) == 1

    def test_second_manual_edit_emits_again(self, task_file, ledger):
        ledger.record(task_file, writer="w")
        _write(task_file, {"tasks": [{"id": "evil-1"}]})
        G.detect_escapes([task_file], ledger=ledger)
        _write(task_file, {"tasks": [{"id": "evil-2"}]})
        results = G.detect_escapes([task_file], ledger=ledger)
        assert results[0]["emitted"] is True
        base = os.path.dirname(ledger.path)
        assert len(ev.read_events(types=["escape"], directory=base)) == 2

    def test_changed_task_ids_located(self, task_file, ledger):
        ledger.record(task_file, writer="w",
                      extra={"task_ids": ["t1"]})
        _write(task_file, {"tasks": [{"id": "t1"}, {"id": "evil"}]})
        row = G.detect_escapes([task_file], ledger=ledger)[0]
        assert row["changed_task_ids"] == ["evil"]
        env = ev.read_events(types=["escape"],
                             directory=os.path.dirname(ledger.path))[0]
        assert env.payload["task_id"] == "evil"
        assert env.payload["changed_task_ids"] == ["evil"]

    def test_detection_is_readonly(self, task_file, ledger):
        ledger.record(task_file, writer="w")
        _write(task_file, {"tasks": [{"id": "evil", "command": "rm -rf /"}]})
        before = open(task_file, encoding="utf-8").read()
        G.detect_escapes([task_file], ledger=ledger)
        assert open(task_file, encoding="utf-8").read() == before

    def test_missing_file_skipped_by_default(self, task_file, ledger):
        ledger.record(task_file, writer="w")
        os.remove(task_file)
        assert _statuses(G.detect_escapes([task_file], ledger=ledger)) == ["missing"]

    def test_missing_file_detected_when_asked(self, task_file, ledger):
        ledger.record(task_file, writer="w")
        os.remove(task_file)
        results = G.detect_escapes([task_file], ledger=ledger, detect_missing=True)
        assert _statuses(results) == ["escape"]
        assert results[0]["reason"] == G.REASON_MISSING

    def test_guard_disabled_returns_empty(self, task_file, ledger, monkeypatch):
        monkeypatch.setenv(G.ENV_GUARD, "0")
        assert G.detect_escapes([task_file], ledger=ledger) == []

    def test_detection_failure_is_swallowed(self, monkeypatch):
        def _boom(path):
            raise OSError("boom")

        monkeypatch.setattr(G, "watched_files", lambda paths=None: ["x"])
        monkeypatch.setattr(G, "file_digest", _boom)
        assert G.detect_escapes() == []

    def test_escape_summary(self, task_file, ledger):
        ledger.record(task_file, writer="w")
        _write(task_file, {"tasks": [{"id": "evil"}]})
        G.detect_escapes([task_file], ledger=ledger)
        summary = G.escape_summary(directory=os.path.dirname(ledger.path))
        assert summary["total"] == 1
        assert summary["by_reason"] == {G.REASON_UNTRACKED_CHANGE: 1}
        assert summary["ledger"].endswith(G.LEDGER_FILENAME)
