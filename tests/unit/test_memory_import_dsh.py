# -*- coding: utf-8 -*-
"""DSH → ① 提炼导入：只取真人提问轨迹（不取 assistant / 系统注入 / subagent）。"""

from __future__ import annotations

import json

import pytest
import zstandard

from agent.memory_import import service
from agent.memory_import.readers import dsh_prompts


def _write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
    path.write_bytes(zstandard.ZstdCompressor().compress(raw.encode("utf-8")))


def _session(sid, origin="top"):
    return {"type": "session", "id": sid, "createdAt": 1790000000000,
            "cwd": "C:/work", "origin": origin}


def _user(text, kind="user", ms=1790000001000):
    return {"type": "user/message", "time": ms,
            "data": {"role": "user", "source": {"kind": kind},
                     "content": [{"type": "text", "text": text}]}}


def _asst(text, ms=1790000002000):
    return {"type": "assistant/message", "time": ms,
            "data": {"message": {"role": "assistant",
                                 "content": [{"type": "text", "text": text}]}}}


@pytest.fixture()
def dsh_root(tmp_path):
    root = tmp_path / "sessions"
    _write(root / "proj-a" / "s1" / "session.jsonl.zstd", [
        _session("s1"),
        _user("问题一"),
        _user("系统注入的快照", kind="plugin"),
        _asst("回答一"),
        _user("问题二" + "x" * 10, ms=1790000003000),
    ])
    _write(root / "proj-a" / "sub1" / "session.jsonl.zstd", [
        _session("sub1", origin="subagent"),
        _user("子代理任务"),
    ])
    return root


def test_reader_takes_only_human_prompts(dsh_root):
    result = dsh_prompts.read_dsh_prompts(root=dsh_root)
    assert [r.text for r in result.records] == ["问题一", "问题二" + "x" * 10]
    assert all(r.source == "dsh" and r.kind == "prompt" and r.role == "user"
               for r in result.records)
    assert {r.session_id for r in result.records} == {"s1"}
    assert {r.project for r in result.records} == {"proj-a"}
    assert result.report["total_records"] == 2
    assert result.report["injected_user_messages"] == 1
    assert result.report["sessions"]["subagent_skipped"] == 1


def test_reader_can_include_subagents(dsh_root):
    result = dsh_prompts.read_dsh_prompts(root=dsh_root, include_subagents=True)
    assert {r.session_id for r in result.records} == {"s1", "sub1"}
    assert len(result.records) == 3


def test_collect_includes_dsh_source(dsh_root):
    collected = service.collect(sources=["dsh"], dsh_root=dsh_root)
    assert len(collected.records) == 2
    assert collected.reports["dsh"]["total_records"] == 2
    assert collected.reports["dsh"]["sessions"]["files"] == 2


def test_preview_batches_dsh_prompts(dsh_root, tmp_path):
    plan = service.preview(sources=["dsh"], dsh_root=dsh_root, root=tmp_path / "plans")
    assert plan["stats"]["kept"] == 2
    assert "dsh" in plan["sources"]
    assert len(plan["batches"]) == 1
    batch = plan["batches"][0]
    assert batch["carrier"] == "memory_note"
    assert batch["source"] == "dsh"
    assert "问题一" in batch["digest"]
    assert batch["stats"]["prompts"] == 2
    assert batch["title"].startswith("dsh 提问轨迹")


def test_preview_batches_dsh_per_session(tmp_path):
    """两个会话 → 两个批次；提问不会因同月合并被 digest 截掉。"""
    root = tmp_path / "sessions"
    _write(root / "p" / "s1" / "session.jsonl.zstd", [_session("s1"), _user("问题甲")])
    _write(root / "p" / "s2" / "session.jsonl.zstd", [_session("s2"), _user("问题乙")])
    plan = service.preview(sources=["dsh"], dsh_root=root, root=tmp_path / "plans")
    assert len(plan["batches"]) == 2
    assert all(b["title"].startswith("dsh 提问轨迹") for b in plan["batches"])
    assert all(b["stats"]["prompts"] == 1 for b in plan["batches"])
    digests = "\n".join(b["digest"] for b in plan["batches"])
    assert "问题甲" in digests and "问题乙" in digests
