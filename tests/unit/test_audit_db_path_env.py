"""L1-c 回归：``chain._resolve_path`` 必须读 ``AUDIT_DB_PATH``（与既有惯例同语义）

【被测缺陷】``_resolve_path`` 原为 ``os.path.abspath(db_path or DEFAULT_DB_PATH)``，
**完全不读环境变量** ⇒ ``get_audit_chain()`` 无参调用恒落硬编码生产路径：
测试进程即使设了 ``AUDIT_DB_PATH``（`tests/conftest.py` 的会话级隔离就是这么做的），
经无参 ``get_audit_chain()`` 写入的记录仍会进**生产审计链**（append-only 哈希链，
污染只能靠重建消除）。实测代价见 `docs/closeout/L1_审计链修复报告_20260921.md` §7.1。

【语义来源（照抄，不发明）】`agent/audit/facade.py:198`：
``db_path or os.getenv("AUDIT_DB_PATH") or DEFAULT_DB_PATH`` —— 空串等同未设置；
不做 strip / 存在性检查；相对路径交给末尾的 ``os.path.abspath``。
"""

from __future__ import annotations

import os
import sqlite3

import pytest

import agent.audit.chain as chain_mod
from agent.audit.chain import DEFAULT_DB_PATH, get_audit_chain, reset_audit_chains

pytestmark = [pytest.mark.unit, pytest.mark.p3]


@pytest.fixture(autouse=True)
def _cleanup():
    reset_audit_chains()
    yield
    reset_audit_chains()


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """把三个审计路径环境变量指到 tmp（与 tests/conftest.py 的会话级隔离同款）"""
    target = tmp_path / "env_audit" / "audit_chain.db"
    monkeypatch.setenv("AUDIT_DB_PATH", str(target))
    monkeypatch.setenv("AUDIT_ROOTS_PATH", str(tmp_path / "env_audit" / "roots.jsonl"))
    monkeypatch.setenv("AUDIT_SIGNING_KEY", str(tmp_path / "env_audit" / "k.pem"))
    return target


def test_get_audit_chain_without_args_uses_env_path(isolated_env):
    """设 ``AUDIT_DB_PATH`` 后，**无参** ``get_audit_chain()`` 写入的确实是该路径"""
    chain = get_audit_chain()
    try:
        assert chain.db_path == os.path.abspath(str(isolated_env)), \
            f"无参 get_audit_chain() 未遵守 AUDIT_DB_PATH：{chain.db_path}"
        assert chain.db_path != DEFAULT_DB_PATH, "无参调用仍落硬编码生产路径"
        chain.append("env.path.write", actor="l1c", subject="env-path")
        assert chain.flush(timeout=10.0) is True
        assert isolated_env.exists(), "记录没有写到环境变量指定的库"
        conn = sqlite3.connect(str(isolated_env))
        try:
            rows = conn.execute("SELECT seq, action FROM audit_chain").fetchall()
        finally:
            conn.close()
        assert [r[1] for r in rows] == ["env.path.write"], rows
    finally:
        chain.close(timeout=5.0)


def test_explicit_arg_beats_env(isolated_env):
    """优先级：显式实参 > 环境变量（与 facade 一致，测试隔离依赖这一点）"""
    explicit = os.path.join(os.path.dirname(str(isolated_env)), "explicit.db")
    assert chain_mod._resolve_path(explicit) == os.path.abspath(explicit)


def test_empty_env_value_falls_back_to_default(monkeypatch):
    """空串与未设置等价（``or`` 短路）——与 facade 的口径逐字一致"""
    monkeypatch.setenv("AUDIT_DB_PATH", "")
    assert chain_mod._resolve_path(None) == os.path.abspath(DEFAULT_DB_PATH)
    monkeypatch.delenv("AUDIT_DB_PATH", raising=False)
    assert chain_mod._resolve_path(None) == os.path.abspath(DEFAULT_DB_PATH)


def test_relative_env_value_is_absolutized(monkeypatch):
    """相对路径按 cwd 归一（facade 也是把相对路径交给同一个 ``abspath``）"""
    monkeypatch.setenv("AUDIT_DB_PATH", os.path.join("rel_dir", "x.db"))
    assert chain_mod._resolve_path(None) == os.path.abspath(
        os.path.join("rel_dir", "x.db"))


def test_facade_and_chain_resolve_the_same_env_path(isolated_env):
    """**跨模块一致性**：门面（facade）与链（chain）对同一环境变量必须解析出同一路径

    两处一旦再次分叉，就会重现"设了环境变量却仍写生产库"的缺陷。
    """
    from agent.audit.facade import AuditFacade

    facade = AuditFacade(enabled=False)      # enabled=False：只解析路径，不起链
    assert os.path.abspath(str(facade._db_path)) == chain_mod._resolve_path(None)
    assert chain_mod._resolve_path(None) == os.path.abspath(str(isolated_env))
