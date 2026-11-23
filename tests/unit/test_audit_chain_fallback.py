"""审计链 DB 兜底：失败必须重试后 fail-closed，不能写内存链头分叉。"""
from __future__ import annotations

import sqlite3

import pytest

from agent.audit.chain import AuditChain, AuditEntryError, reset_audit_chains


def test_db_fallback_retries_then_fails_closed(tmp_path, monkeypatch):
    reset_audit_chains()
    chain = AuditChain(str(tmp_path / "audit_chain.db"),
                       roots_path=str(tmp_path / "roots.jsonl"),
                       signing_enabled=False, auto_seal=False,
                       enforce_single_writer=False)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(chain, "_connect", boom)
    with pytest.raises(AuditEntryError):
        chain._allocate_via_db(
            ts="2026-11-23T00:00:00+00:00", actor="test",
            action="test.fallback", subject="x", payload={"a": 1},
            src="system", trace_id="", workspace_id="",
            schema_version=1, reason="test")
    assert calls["n"] == 2, "DB 兜底应重试一次后才 fail-closed"
    chain.close(timeout=5.0)
