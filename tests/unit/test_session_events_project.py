# -*- coding: utf-8 -*-
"""P5 / E5-1 Phase A：会话事件溯源只读投影（scripts/session_events_project.py）

钉住方案里的不变量：只读源文件、冻结池跳过、幂等（(sid,ts,role,content) → evt_id）、
坏行进 quarantine、输出原子写、dry-run 不落盘。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import session_events_project as P  # noqa: E402


def _msg(role: str, content: str, ts: str, **extra) -> str:
    rec = {"role": role, "content": content, "timestamp": ts, **extra}
    return json.dumps(rec, ensure_ascii=False)


def _write_session(root: Path, sid: str, lines: list[str]) -> Path:
    d = root / "data" / "sessions" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d / "messages.jsonl"


def _events(root: Path, sid: str) -> list[dict]:
    path = root / "data" / "session_events" / (sid + ".jsonl")
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class TestProjection:
    def test_两条消息投影成两个事件(self, tmp_path):
        _write_session(tmp_path, "s1", [
            _msg("user", "你好", "2026-11-27T01:00:00+00:00"),
            _msg("assistant", "在的", "2026-11-27T01:00:05+00:00", steps=[{"t": "x"}]),
        ])
        report = P.project_all(tmp_path)
        assert report["sessions"] == 1 and report["rows"] == 2 and report["events"] == 2
        evts = _events(tmp_path, "s1")
        assert [e["seq"] for e in evts] == [1, 2]
        assert [e["type"] for e in evts] == ["message.user", "message.assistant"]
        assert evts[0]["payload"]["content"] == "你好"
        assert evts[1]["payload"]["steps"] == [{"t": "x"}], "payload 要保留原始记录（含工具步骤）"
        assert all(e["src"] == "legacy" and e["v"] == 1 for e in evts)

    def test_乱序输入按时间编号(self, tmp_path):
        _write_session(tmp_path, "s2", [
            _msg("assistant", "后", "2026-11-27T01:00:02+00:00"),
            _msg("user", "前", "2026-11-27T01:00:01+00:00"),
        ])
        P.project_all(tmp_path)
        evts = _events(tmp_path, "s2")
        assert [e["payload"]["content"] for e in evts] == ["前", "后"]
        assert [e["seq"] for e in evts] == [1, 2]

    def test_未知角色归为other(self, tmp_path):
        _write_session(tmp_path, "s3", [_msg("weird", "x", "2026-11-27T01:00:00+00:00")])
        P.project_all(tmp_path)
        assert _events(tmp_path, "s3")[0]["type"] == "message.other"


class TestIdempotent:
    def test_重跑只补新事件_文件内容不变(self, tmp_path):
        _write_session(tmp_path, "s4", [_msg("user", "一", "2026-11-27T01:00:00+00:00")])
        P.project_all(tmp_path)
        path = tmp_path / "data" / "session_events" / "s4.jsonl"
        first = path.read_bytes()
        again = P.project_all(tmp_path)
        assert again["written"] == 0
        assert path.read_bytes() == first, "幂等：重跑不得改变已有投影的字节"

        # 追加一条新消息后，只补它（旧事件 seq 不变）
        src = tmp_path / "data" / "sessions" / "s4" / "messages.jsonl"
        src.write_text(src.read_text(encoding="utf-8") + _msg("assistant", "二", "2026-11-27T01:00:09+00:00") + "\n",
                       encoding="utf-8")
        third = P.project_all(tmp_path)
        assert third["written"] == 1
        evts = _events(tmp_path, "s4")
        assert [e["seq"] for e in evts] == [1, 2]
        assert evts[1]["payload"]["content"] == "二"

    def test_dry_run不落盘(self, tmp_path):
        _write_session(tmp_path, "s5", [_msg("user", "x", "2026-11-27T01:00:00+00:00")])
        report = P.project_all(tmp_path, dry_run=True)
        assert report["rows"] == 1
        assert not (tmp_path / "data" / "session_events").exists()


class TestQuarantine:
    def test_坏行隔离_其余照常(self, tmp_path):
        _write_session(tmp_path, "s6", [
            "{not json",
            _msg("user", "好的", "2026-11-27T01:00:00+00:00"),
            json.dumps({"role": "user", "content": "没时间戳"}),
        ])
        report = P.project_all(tmp_path)
        reasons = sorted(q["reason"] for q in report["quarantine"])
        assert reasons == ["invalid_json", "missing_timestamp"], reasons
        assert report["events"] == 1
        assert _events(tmp_path, "s6")[0]["payload"]["content"] == "好的"

    def test_冻结池跳过不迁移(self, tmp_path):
        _write_session(tmp_path, "s7", [_msg("user", "会话内", "2026-11-27T01:00:00+00:00")])
        legacy = tmp_path / "data" / "messages.jsonl"
        legacy.write_text(json.dumps({"role": "user", "content": "历史", "timestamp": "2026-10-01T00:00:00+00:00"},
                                     ensure_ascii=False) + "\n", encoding="utf-8")
        report = P.project_all(tmp_path)
        assert report["legacy_rows"] == 1 and report["skipped_legacy"] == 1
        assert report["events"] == 1, "冻结池不得进入事件投影"
        # 投影目录里只有会话 s7 的事件文件（冻结池没有自己的 sid ⇒ 不产生文件）
        names = sorted(p.name for p in (tmp_path / "data" / "session_events").glob("*.jsonl"))
        assert names == ["s7.jsonl"], names

    def test_源文件一个字节都不改(self, tmp_path):
        src = _write_session(tmp_path, "s8", [_msg("user", "原文", "2026-11-27T01:00:00+00:00")])
        before = src.read_bytes()
        P.project_all(tmp_path)
        assert src.read_bytes() == before

    def test_event_id稳定且不含内容明文(self):
        a = P.event_id("s", "t", "user", "秘密")
        assert a == P.event_id("s", "t", "user", "秘密")
        assert a != P.event_id("s", "t", "user", "秘密2")
        assert a.startswith("evt_") and "秘密" not in a
