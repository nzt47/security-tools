"""TASK-S4-02 策略测试工具箱（**落盘隔离**）

【为什么必须有这个文件】
    通用硬约束 6：「涉及落盘存储的用例必须显式传路径或加 autouse 会话级隔离
    （S3-02/S3-03 两次踩坑）」。策略包有四处默认落盘：

        data/policies/decisions.jsonl   决策日志（模拟器数据源）
        data/policies/inbox.jsonl       例外收件箱本地账
        data/policies/policy_signing_key.pem   签名私钥（首次签名时生成）
        data/policies/policies.json     策略库（本仓库的真实配置）

    以及三个**进程级单例**（策略库 / 引擎 / 收件箱）与一个**进程级污点台账**。
    任何一个没隔离，都会让用例之间互相污染（典型表现：单独跑绿、全量跑红；
    或者本地跑绿、CI 跑红）。

【用法】

    from policy_testkit import isolate_policy

    @pytest.fixture(autouse=True)
    def _iso(tmp_path, monkeypatch):
        isolate_policy(tmp_path, monkeypatch)

``pytest.ini`` 已把 ``tests/unit`` 加进 ``pythonpath``，因此可直接 import。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

#: 需要清理的环境变量（避免宿主机/.env 影响用例）
ENV_KEYS = (
    "CP_POLICY_FILE",
    "CP_POLICY_BUILTIN_INVARIANTS",
    "CP_POLICY_REQUIRE_SIGNATURE",
    "CP_POLICY_CACHE_SIZE",
    "CP_POLICY_DECISION_LOG",
    "CP_POLICY_DECISION_LOG_ENABLED",
    "CP_POLICY_INBOX_BACKEND",
    "CP_POLICY_INBOX_PATH",
    "CP_POLICY_INBOX_DEDUPE_SECONDS",
    "CP_POLICY_EGRESS_GUARD",
    "CP_POLICY_GATEWAY_ENABLED",
    "CP_POLICY_TAINT_ENABLED",
    "CP_POLICY_TAINT_TTL_SECONDS",
    "CP_POLICY_OBSERVE",
    "CP_POLICY_SIGNING_KEY",
    "CP_POLICY_PUBLIC_KEY",
    "CP_EVENTS_DIR",
    "CP_EVENTS_ENABLED",
    "CP_EVENTS_AUDIT_MIRROR",
    # 审计链（S2-02）——**变量名以 agent/audit/facade.py:52-56 为准**，
    # 不是 CP_AUDIT_*。此前漏了这三个，导致策略用例的决策埋点写进了仓库
    # 运行时的 data/audit/audit_chain.db（gitignore 挡住了产物漂移，
    # 但污染了开发者本地台账）。
    "AUDIT_DB_PATH",
    "AUDIT_ROOTS_PATH",
    "AUDIT_SIGNING_KEY",
    "AUDIT_CHAIN_ENABLED",
    "AUDIT_DUAL_WRITE",
)


def isolate_policy(tmp_path: Any, monkeypatch: Any, *,
                   policy_file: Optional[str] = None,
                   write_policy_file: bool = False) -> Dict[str, str]:
    """把一个用例完全隔离到 ``tmp_path``

    Args:
        tmp_path: pytest 的 ``tmp_path``。
        monkeypatch: pytest 的 ``monkeypatch``。
        policy_file: 覆盖策略库路径；默认指向 ``tmp_path/policies.json``
            （**默认写入一个不存在也不创建的空库**——即「无文件策略」，
            这样内置不变量单独可见，用例断言不受仓库真实策略影响）。
        write_policy_file: 是否把默认策略库文件真的写出来（默认 False）。

    Returns:
        关键路径字典（``policy_file`` / ``decision_log`` / ``inbox`` /
        ``private_key`` / ``public_key`` / ``events_dir`` / ``audit_db`` /
        ``audit_roots`` / ``audit_key``）。
    """
    base = tmp_path / "policy_io"
    base.mkdir(parents=True, exist_ok=True)

    paths = {
        "policy_file": str(policy_file or (base / "policies.json")),
        "decision_log": str(base / "decisions.jsonl"),
        "inbox": str(base / "inbox.jsonl"),
        "private_key": str(base / "signing_key.pem"),
        "public_key": str(base / "signing_key.pub.pem"),
        "events_dir": str(base / "events"),
        "audit_db": str(base / "audit_chain.db"),
        "audit_roots": str(base / "daily_roots.jsonl"),
        "audit_key": str(base / "audit_signing_key.pem"),
    }

    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("CP_POLICY_FILE", paths["policy_file"])
    monkeypatch.setenv("CP_POLICY_DECISION_LOG", paths["decision_log"])
    monkeypatch.setenv("CP_POLICY_INBOX_PATH", paths["inbox"])
    monkeypatch.setenv("CP_POLICY_SIGNING_KEY", paths["private_key"])
    monkeypatch.setenv("CP_POLICY_PUBLIC_KEY", paths["public_key"])
    # 审计链隔离（S2-02）：env 变量只是「尚未构造门面时」的兜底——`audit` 是模块级
    # 单例，路径在导入期就解析完了，事后设 env 对它**无效**（实测：设了 env 仍往
    # data/audit/audit_chain.db 写）。真正的重定向必须 `bind()` 一条自有链。
    monkeypatch.setenv("AUDIT_DB_PATH", paths["audit_db"])
    monkeypatch.setenv("AUDIT_ROOTS_PATH", paths["audit_roots"])
    monkeypatch.setenv("AUDIT_SIGNING_KEY", paths["audit_key"])
    monkeypatch.delenv("AUDIT_DUAL_WRITE", raising=False)

    # 事件层隔离（沿用 S2-03 用例的既有口径）
    monkeypatch.setenv("CP_EVENTS_DIR", paths["events_dir"])
    monkeypatch.delenv("CP_EVENTS_ENABLED", raising=False)
    monkeypatch.delenv("CP_EVENTS_AUDIT_MIRROR", raising=False)

    if write_policy_file:
        with open(paths["policy_file"], "w", encoding="utf-8") as handle:
            json.dump({"schema": "policy.v1", "policies": []}, handle)

    _reset_singletons()          # 其中会 reset_audit_facade()（bind(None)）
    _bind_tmp_audit_chain(paths, monkeypatch)   # ⇒ 必须在 reset 之后绑定
    return paths


def _bind_tmp_audit_chain(paths: Dict[str, str], monkeypatch: Any) -> None:
    """把进程级审计门面绑定到本用例自己的 tmp 链（逐测试独立台账）

    为什么不是「设环境变量」：`agent.audit.facade.audit` 是模块级单例，其
    `_db_path/_roots_path/_key_path` 在**导入期**就已解析，`reset_audit_facade()`
    也不重建对象（只 close + bind(None) + reset_counters）。所以事后设 env 无效，
    只能 `bind()` 一条显式构造的链。

    这里用 `monkeypatch.setattr` 一步完成「绑定 + 测后还原」：setattr 会记录当前值，
    teardown 时写回，于是不必把每个 fixture 改成生成器。
    额外把 `_db_path` 等一并指向 tmp，使「链被关闭后懒重建」也不会落到默认路径。
    """
    try:
        from agent.audit import facade as facade_mod
        from agent.audit.chain import AuditChain, reset_audit_chains
    except Exception:  # noqa: BLE001 审计栈不可用时无需绑定
        return
    try:
        reset_audit_chains()
        chain = AuditChain(paths["audit_db"],
                           roots_path=paths["audit_roots"],
                           signing_key_path=paths["audit_key"],
                           auto_seal=False, auto_start_writer=False)
        monkeypatch.setattr(facade_mod.audit, "_chain", chain, raising=False)
        monkeypatch.setattr(facade_mod.audit, "_db_path", paths["audit_db"],
                            raising=False)
        monkeypatch.setattr(facade_mod.audit, "_roots_path", paths["audit_roots"],
                            raising=False)
        monkeypatch.setattr(facade_mod.audit, "_key_path", paths["audit_key"],
                            raising=False)
        monkeypatch.setattr(facade_mod.audit, "enabled", True, raising=False)
        paths["audit_chain"] = chain
    except Exception:  # noqa: BLE001 绑不上就退回"不写链"，绝不因隔离失败影响用例
        pass


def _reset_singletons() -> None:
    """清掉进程级单例（顺序重要：先关引擎的日志句柄，再弃库/收件箱/污点）

    事件 store 单例尤其关键：``agent/observability/events.py`` 的 ``_STORE`` 会把
    **第一次构造时的 ``CP_EVENTS_DIR``** 记在实例上，之后改环境变量对它无效。
    不重置的话，第一个用例的临时目录会被后续用例复用，表现为「本用例写入、
    本用例读不到」。S2 的用例是用显式 ``EventStore(path)`` 绕开的，这里用重置。
    """
    try:
        from agent.observability.events import reset_event_stores
        reset_event_stores()
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent.audit.facade import reset_audit_facade
        reset_audit_facade()
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent.audit.chain import reset_audit_chains
        reset_audit_chains()
    except Exception:  # noqa: BLE001
        pass
    try:
        from agent.policy import (
            reset_policy_engine,
            reset_policy_inbox,
            reset_policy_store,
            reset_secret_taint,
        )
    except Exception:  # noqa: BLE001 包不可导入时无需清理
        return
    for fn in (reset_policy_engine, reset_policy_store, reset_policy_inbox,
               reset_secret_taint):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass


def make_policy(**overrides: Any) -> Dict[str, Any]:
    """构造一条合法策略 dict（缺省字段可被 overrides 覆盖）"""
    base: Dict[str, Any] = {
        "id": "test.example",
        "version": "1.0.0",
        "owner": "tester",
        "effect": "deny",
        "match": {"field": "target.external", "op": "eq", "value": True},
        "message_template": "测试拒绝 {capability_id}",
        "effective_range": None,
        "break_glass_ttl_min": None,
        "signature": "",
    }
    base.update(overrides)
    return base


def write_policy_file(path: str, policies: List[Dict[str, Any]]) -> str:
    """把策略清单写成规范策略库文件"""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"schema": "policy.v1", "policies": policies}, handle,
                  ensure_ascii=False, indent=2)
    return path


def make_engine(store: Any = None, **kwargs: Any) -> Any:
    """构造**不落盘、不埋点**的引擎（用例默认形态）

    默认关闭决策日志与埋点：用例只关心判定语义时不希望 IO/审计参与，
    需要在测埋点的地方显式构造引擎。
    """
    from agent.policy import PolicyEngine

    kwargs.setdefault("decision_log", False)
    kwargs.setdefault("cache_size", 0)
    return PolicyEngine(store, **kwargs)


def make_store(policies: Optional[List[Dict[str, Any]]] = None,
               *, path: Optional[str] = None, **kwargs: Any) -> Any:
    """构造策略库（默认 ``autoload=False``，避免读到仓库真实策略）"""
    from agent.policy import PolicyStore

    kwargs.setdefault("autoload", False)
    store = PolicyStore(path=path, **kwargs)
    for item in (policies or []):
        store.add(item)
    return store
