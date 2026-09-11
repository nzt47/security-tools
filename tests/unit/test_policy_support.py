"""TASK-S4-02 支撑模块用例：决策日志 / 例外收件箱 / 签名

这三个模块的**失败与降级路径**是生产真实会走的（磁盘满、账本被打断、密钥不可用、
收件箱后端切换），因此单独一份用例把它们逐条走一遍——「没测到的降级路径」等于
「没实现的降级路径」。
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from policy_testkit import isolate_policy, make_policy, make_store

from agent.policy.decisions import (
    MAX_LINE_BYTES,
    DecisionLog,
    DecisionLogError,
    DecisionRecord,
)
from agent.policy.engine import DecisionObserver, PolicyEngine
from agent.policy.inbox import InboxItem, PolicyInbox, get_policy_inbox, reset_policy_inbox
from agent.policy.models import (
    EFFECT_ASK,
    EFFECT_DENY,
    Policy,
    PolicyContext,
    PolicyDecision,
)
from agent.policy.signing import (
    PolicySigner,
    audit_signature_state,
    self_sign,
    verify_policy_signature as _verify_sig,
)
from agent.policy.taint import mark_secret_read


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    return isolate_policy(tmp_path, monkeypatch)


def _decision(effect=EFFECT_DENY, **kwargs):
    base = dict(effect=effect, policy_id="p.one", policy_version="1.0.0",
                reason_code="policy_deny", capability_id="cp.a.b",
                action="http.post", actor="u1", tenant_id="t1", latency_ms=0.5)
    base.update(kwargs)
    return PolicyDecision(**base)


def _ctx(**kwargs):
    return PolicyContext.build(capability_id="cp.a.b", actor="u1",
                               target={"external": True}, **kwargs)


# ════════════════════════════════════════════════════════════
#  决策日志
# ════════════════════════════════════════════════════════════


class TestDecisionRecord:
    def test_to_json_line_可解析(self):
        line = DecisionRecord(ts="2026-09-11T10:00:00+08:00", effect="deny").to_json_line()
        assert json.loads(line)["effect"] == "deny"

    def test_from_dict_容错非法字段(self):
        record = DecisionRecord.from_dict({"ts": None, "input": "not-a-dict",
                                          "latency_ms": "abc"})
        assert record.input == {} and record.latency_ms == 0.0

    def test_from_dict_空输入(self):
        assert DecisionRecord.from_dict({}).ts == ""

    def test_day_取日历日(self):
        assert DecisionRecord(ts="2026-09-11T10:00:00+08:00").day() == "2026-09-11"
        assert DecisionRecord(ts="bad").day() == ""

    def test_ctx_还原(self):
        record = DecisionRecord(input=PolicyContext.build(capability_id="cp.x.y").input)
        assert record.ctx().capability_id == "cp.x.y"

    def test_extra_参与序列化(self):
        line = DecisionRecord(ts="t", extra={"k": 1}).to_json_line()
        assert json.loads(line)["extra"] == {"k": 1}

    def test_不可序列化载荷退回_default_str(self):
        line = DecisionRecord(ts="t", input={"obj": object()}).to_json_line()
        assert json.loads(line)["input"]["obj"]


class TestDecisionLog:
    def test_写入与读回(self, tmp_path):
        log = DecisionLog(str(tmp_path / "d.jsonl"))
        assert log.append(_ctx(), _decision()) is True
        assert log.stats["write_count"] == 1
        log.close()
        assert len(DecisionLog(str(tmp_path / "d.jsonl")).read()) == 1

    def test_未启用时不写(self, tmp_path):
        log = DecisionLog(str(tmp_path / "d.jsonl"), enabled=False)
        assert log.append(_ctx(), _decision()) is False
        assert log.enabled is False
        assert not (tmp_path / "d.jsonl").exists()

    def test_目录不存在时自动创建(self, tmp_path):
        log = DecisionLog(str(tmp_path / "nested" / "deep" / "d.jsonl"))
        assert log.append(_ctx(), _decision()) is True
        log.close()

    def test_超长记录被丢弃(self, tmp_path):
        log = DecisionLog(str(tmp_path / "d.jsonl"))
        huge = DecisionRecord(ts="t", extra={"blob": "x" * (MAX_LINE_BYTES + 10)})
        assert log.append(_ctx(), _decision(), extra=huge.extra) is False
        assert log.stats["failure_count"] == 1

    def test_strict_模式抛异常(self, tmp_path):
        log = DecisionLog(str(tmp_path / "d.jsonl"), strict=True)
        with pytest.raises(DecisionLogError):
            log.append(_ctx(), _decision(),
                       extra={"blob": "x" * (MAX_LINE_BYTES + 10)})

    def test_磁盘余量不足时停写(self, tmp_path):
        log = DecisionLog(str(tmp_path / "d.jsonl"), min_free_bytes=1 << 62)
        assert log.append(_ctx(), _decision()) is False
        assert log.stats["skipped_low_disk"] == 1

    def test_无法打开文件时计入失败(self, tmp_path):
        # 用一个**目录**当文件路径 ⇒ open(..., "a") 必然失败
        target = tmp_path / "as_dir"
        target.mkdir()
        log = DecisionLog(str(target))
        assert log.append(_ctx(), _decision()) is False
        assert log.stats["failure_count"] >= 1

    def test_flush_与_close_幂等(self, tmp_path):
        log = DecisionLog(str(tmp_path / "d.jsonl"))
        assert log.flush() is True            # 未打开 handle 时也返回 True
        log.append(_ctx(), _decision())
        assert log.flush() is True
        log.close()
        log.close()                            # 二次 close 不报错
        assert log.append(_ctx(), _decision()) is False

    def test_上下文管理器(self, tmp_path):
        with DecisionLog(str(tmp_path / "d.jsonl")) as log:
            assert log.append(_ctx(), _decision()) is True
        assert log.enabled is False

    def test_时间窗过滤(self, tmp_path):
        path = str(tmp_path / "d.jsonl")
        log = DecisionLog(path)
        log.append(_ctx(), _decision(), ts="2026-01-01T00:00:00+08:00")
        log.append(_ctx(), _decision(), ts="2026-09-01T00:00:00+08:00")
        log.close()
        reader = DecisionLog(path, enabled=False)
        assert len(reader.read(since="2026-06-01")) == 1
        assert len(reader.read(until="2026-06-01")) == 1
        assert len(reader.read(since_days=1)) == 0
        assert len(reader.read(limit=1)) == 1

    def test_按日分片与目录路径(self, tmp_path):
        """``decisions.jsonl`` + ``decisions.20260911.jsonl`` 同时被读到"""
        active = tmp_path / "decisions.jsonl"
        shard = tmp_path / "decisions.20260911.jsonl"
        shard.write_text(DecisionRecord(ts="2026-09-11T10:00:00+08:00",
                                        effect="deny").to_json_line() + "\n",
                         encoding="utf-8")
        log = DecisionLog(str(active))
        log.append(_ctx(), _decision())
        log.close()
        reader = DecisionLog(str(active), enabled=False)
        assert len(reader.read()) == 2
        # 直接把目录当路径也支持
        assert len(DecisionLog(str(tmp_path), enabled=False).read()) == 2

    def test_脏行被跳过(self, tmp_path):
        path = tmp_path / "d.jsonl"
        path.write_text("not json\n\n" + DecisionRecord(ts="t", effect="deny").to_json_line()
                        + "\n", encoding="utf-8")
        assert len(DecisionLog(str(path), enabled=False).read()) == 1

    def test_目录作路径时不存在也无妨(self, tmp_path):
        assert DecisionLog(str(tmp_path / "none"), enabled=False).read() == []

    def test_不可写路径不抛异常(self, tmp_path):
        # 把「一个普通文件」当成目录的父级 ⇒ makedirs/open 必然失败
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        log = DecisionLog(str(blocker / "sub" / "d.jsonl"))
        assert log.append(_ctx(), _decision()) is False
        assert log.stats["failure_count"] >= 1

    def test_引擎默认写入决策日志(self, tmp_path):
        path = str(tmp_path / "engine.jsonl")
        log = DecisionLog(path)
        engine = PolicyEngine(make_store(), cache_size=0, decision_log=log,
                              observer=DecisionObserver(enabled=False), inbox=False)
        engine.check(_ctx())
        log.close()
        assert engine.decision_log is log
        assert len(DecisionLog(path, enabled=False).read()) == 1


# ════════════════════════════════════════════════════════════
#  例外收件箱
# ════════════════════════════════════════════════════════════


class _FakeQueue:
    """``TakeoverQueue`` 的最小替身（**不拉起监控栈**，见 inbox 模块文档）"""

    def __init__(self):
        self.created = []

    def create_takeover(self, alert, reason, evidence=None):
        record = {"takeover_id": f"tk{len(self.created)}", "alert": alert,
                  "reason": reason, "evidence": evidence}
        self.created.append(record)
        return type("R", (), {"takeover_id": record["takeover_id"]})()


class TestPolicyInbox:
    def test_log_后端落账(self, tmp_path):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "i.jsonl"))
        item = inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
        assert item is not None and item.kind == "policy.ask"
        assert len(inbox.pending()) == 1
        assert inbox.stats["submitted"] == 1

    def test_非例外不入箱(self, tmp_path):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "i.jsonl"))
        assert inbox.submit(_decision("allow"), ctx=_ctx()) is None
        assert inbox.submit(_decision(EFFECT_DENY), ctx=_ctx()) is None

    def test_takeover_后端复用接管队列(self, tmp_path):
        queue = _FakeQueue()
        inbox = PolicyInbox(backend="takeover", queue=queue,
                            path=str(tmp_path / "i.jsonl"))
        item = inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
        assert item.takeover_id == "tk0"
        assert len(queue.created) == 1
        assert inbox.stats["queue_routed"] == 1
        # takeover 后端不写本地账
        assert not (tmp_path / "i.jsonl").exists()

    def test_both_后端双写(self, tmp_path):
        queue = _FakeQueue()
        inbox = PolicyInbox(backend="both", queue=queue,
                            path=str(tmp_path / "i.jsonl"))
        item = inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
        assert item.takeover_id == "tk0"
        assert len(inbox.pending()) == 1

    def test_解析器注入取代静态依赖(self, tmp_path):
        """队列解析器由**组合根注册**；``agent.policy`` 不 import ``agent.monitoring``

        这条不是风格问题：`agent.monitoring.self_healer → agent.permission_system`
        已经存在，只要 policy 侧静态引用 `agent.monitoring.alert_manager`，
        `arch_rules` 的 `no_circular_dependency` 就会判出环并阻断 CI。
        """
        from agent.policy.inbox import (
            get_queue_resolver, register_queue_resolver,
        )
        queue = _FakeQueue()
        assert get_queue_resolver() is None
        register_queue_resolver(lambda: queue)
        try:
            assert get_queue_resolver() is not None
            inbox = PolicyInbox(backend="takeover", path=str(tmp_path / "i.jsonl"))
            item = inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
            assert item.takeover_id == "tk0"
            assert len(queue.created) == 1
        finally:
            register_queue_resolver(None)
        assert get_queue_resolver() is None

    def test_解析器抛异常不影响决策(self, tmp_path):
        from agent.policy.inbox import register_queue_resolver
        register_queue_resolver(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        try:
            inbox = PolicyInbox(backend="takeover", path=str(tmp_path / "i.jsonl"))
            item = inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
            assert item is not None and item.takeover_id == ""
        finally:
            register_queue_resolver(None)

    def test_未注册解析器时_takeover_后端静默降级(self, tmp_path):
        from agent.policy.inbox import register_queue_resolver
        register_queue_resolver(None)
        inbox = PolicyInbox(backend="takeover", path=str(tmp_path / "i.jsonl"))
        item = inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
        assert item is not None and item.takeover_id == ""

    def test_policy_包不静态依赖_monitoring(self):
        """AST 守卫：`agent/policy/*.py` 不得出现 `agent.monitoring` 的**导入**"""
        import ast
        import pathlib as _pl
        offenders = []
        for path in sorted(_pl.Path("agent/policy").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and "monitoring" in str(node.module or ""):
                    offenders.append(f"{path.name}:{node.lineno} from {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if "monitoring" in alias.name:
                            offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
        assert offenders == [], f"agent.policy 静态依赖了 monitoring: {offenders}"

    def test_off_后端完全关闭(self, tmp_path):
        inbox = PolicyInbox(backend="off", path=str(tmp_path / "i.jsonl"))
        assert inbox.enabled is False
        assert inbox.submit(_decision(EFFECT_ASK), ctx=_ctx()) is None

    def test_非法后端回退_log(self):
        assert PolicyInbox(backend="nonsense").backend == "log"

    def test_后端读环境变量(self, monkeypatch):
        monkeypatch.setenv("CP_POLICY_INBOX_BACKEND", "takeover")
        monkeypatch.setenv("CP_POLICY_INBOX_PATH", "x/y.jsonl")
        inbox = PolicyInbox()
        assert inbox.backend == "takeover" and inbox.path == "x/y.jsonl"

    def test_队列不可用时降级不丢例外(self, tmp_path):
        class Boom:
            def create_takeover(self, *a, **k):
                raise RuntimeError("queue down")

        inbox = PolicyInbox(backend="both", queue=Boom(),
                            path=str(tmp_path / "i.jsonl"))
        item = inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
        assert item is not None and item.takeover_id == ""
        assert inbox.stats["failures"] >= 1
        assert len(inbox.pending()) == 1          # 本地账仍兜住了

    def test_break_glass_例外入箱(self, tmp_path):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "i.jsonl"))
        item = inbox.submit(_decision("allow", break_glass=True), ctx=_ctx())
        assert item.kind == "policy.break_glass" and item.break_glass is True

    def test_去重窗口关闭后每次都入账(self, tmp_path):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "i.jsonl"),
                            dedupe_window_seconds=0)
        for _ in range(3):
            inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
        assert len(inbox.pending()) == 3
        assert inbox.stats["deduped"] == 0

    def test_prune_recent_清理窗口(self, tmp_path):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "i.jsonl"),
                            dedupe_window_seconds=0.0001)
        inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
        import time
        time.sleep(0.01)
        assert inbox.prune_recent() == 1

    def test_读账容错脏行(self, tmp_path):
        path = tmp_path / "i.jsonl"
        path.write_text("bad\n" + json.dumps({"item_id": "pi_1", "kind": "policy.ask",
                                              "duplicate_count": 2}) + "\n",
                        encoding="utf-8")
        inbox = PolicyInbox(backend="log", path=str(path))
        items = inbox.pending()
        assert len(items) == 1 and items[0].duplicate_count == 2

    def test_读账保留最大复计数(self, tmp_path):
        path = tmp_path / "i.jsonl"
        lines = [json.dumps({"item_id": "pi_1", "kind": "policy.ask",
                             "duplicate_count": n}) for n in (1, 5, 3)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        inbox = PolicyInbox(backend="log", path=str(path))
        assert inbox.pending()[0].duplicate_count == 5

    def test_close_幂等(self, tmp_path):
        inbox = PolicyInbox(backend="log", path=str(tmp_path / "i.jsonl"))
        inbox.submit(_decision(EFFECT_ASK), ctx=_ctx())
        inbox.close()
        inbox.close()

    def test_InboxItem_to_dict(self):
        item = InboxItem(item_id="pi", kind="policy.ask", policy_id="p",
                         policy_version="1", capability_id="c", tenant_id="t",
                         actor="a", message="m", created_at="now", effect="ask")
        body = item.to_dict()
        assert body["item_id"] == "pi" and body["duplicate_count"] == 1

    def test_单例可重置(self):
        first = get_policy_inbox()
        reset_policy_inbox()
        assert get_policy_inbox() is not first

    def test_引擎默认收件箱被延迟创建(self, tmp_path):
        """``inbox=None`` 时才允许延迟解析；``inbox=False`` 是显式关闭。"""
        engine = PolicyEngine(make_store([make_policy(id="i2.ask", effect="ask",
                                                      match={})]),
                              cache_size=0, decision_log=False,
                              observer=DecisionObserver(enabled=False))
        assert engine._inbox_enabled is True  # noqa: SLF001
        engine.check(_ctx())
        # 默认收件箱已被解析（进程级单例），且落在隔离路径里
        assert get_policy_inbox().backend in ("log", "both", "takeover")


# ════════════════════════════════════════════════════════════
#  签名（降级路径）
# ════════════════════════════════════════════════════════════


class TestSignerDegraded:
    def test_不可解析私钥时降级(self, tmp_path):
        key = tmp_path / "broken.pem"
        key.write_text("not a pem", encoding="utf-8")
        signer = PolicySigner(private_key_path=str(key),
                              public_key_path=str(tmp_path / "b.pub.pem"))
        assert signer.degraded is True
        assert signer.scheme == "sha256-self"
        assert "私钥不可解析" in signer.degraded_reason
        assert signer.sign(make_policy(id="d.a")).startswith("sha256-self:")

    def test_非_ed25519_私钥时降级(self, tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = tmp_path / "rsa.pem"
        key.write_bytes(rsa.generate_private_key(public_exponent=65537,
                                                 key_size=2048).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()))
        signer = PolicySigner(private_key_path=str(key),
                              public_key_path=str(tmp_path / "r.pub.pem"))
        assert signer.degraded is True
        assert "非 ed25519" in signer.degraded_reason

    def test_不自动生成密钥时降级(self, tmp_path):
        signer = PolicySigner(private_key_path=str(tmp_path / "absent.pem"),
                              public_key_path=str(tmp_path / "absent.pub.pem"),
                              auto_generate=False)
        assert signer.degraded is True
        assert "无私钥" in signer.degraded_reason
        assert signer.public_key_pem == ""

    def test_自动生成并落盘密钥(self, tmp_path):
        key = tmp_path / "sub" / "k.pem"
        public = tmp_path / "sub" / "k.pub.pem"
        signer = PolicySigner(private_key_path=str(key),
                              public_key_path=str(public))
        assert key.exists() and public.exists()
        assert signer.degraded is False

    def test_密钥路径不可写时降级(self, tmp_path):
        blocked = tmp_path / "as_dir"
        blocked.mkdir()
        signer = PolicySigner(private_key_path=str(blocked),
                              public_key_path=str(tmp_path / "x.pub.pem"))
        assert signer.degraded is True

    def test_读取密钥抛_OSError_时降级(self, tmp_path, monkeypatch):
        key = tmp_path / "k.pem"
        key.write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
        real_open = open

        def fake_open(path, *args, **kwargs):
            if str(path).endswith("k.pem") and "r" in (args[0] if args else "r"):
                raise OSError("boom")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)
        signer = PolicySigner(private_key_path=str(key),
                              public_key_path=str(tmp_path / "k.pub.pem"),
                              auto_generate=False)
        assert signer.degraded is True

    def test_sign_dict_不修改入参(self, tmp_path):
        signer = PolicySigner(private_key_path=str(tmp_path / "k.pem"),
                              public_key_path=str(tmp_path / "k.pub.pem"))
        original = make_policy(id="sd.a")
        snapshot = dict(original)
        signed = signer.sign_dict(original)
        assert original == snapshot          # 入参未被就地改写
        assert signed is not original
        assert signed["signature"]

    def test_sign_接受_Policy_对象(self, tmp_path):
        signer = PolicySigner(private_key_path=str(tmp_path / "k.pem"),
                              public_key_path=str(tmp_path / "k.pub.pem"))
        assert signer.sign(Policy.parse(make_policy(id="sp.a"))).startswith("ed25519:")

    def test_非_ed25519_公钥时报错(self, tmp_path):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        pem = rsa.generate_private_key(public_exponent=65537, key_size=2048
                                       ).public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        result = _verify_sig(
            Policy.parse(make_policy(id="rk.a", signature="ed25519:00")),
            public_key_pem=pem)
        assert result.ok is False and "非 ed25519" in result.reason

    def test_公钥文件读取失败不崩(self, tmp_path):
        result = _verify_sig(
            Policy.parse(make_policy(id="pk.a", signature="ed25519:00")),
            public_key_path=str(tmp_path / "as_dir" and tmp_path / "never.pem"))
        assert result.ok is False

    def test_audit_signature_state_只报真问题(self):
        signed = make_policy(id="as.a")
        signed["signature"] = self_sign(signed)
        unsigned = make_policy(id="as.b")
        bad = make_policy(id="as.c", signature="ed25519:dead")
        problems = audit_signature_state([
            Policy.parse(signed), Policy.parse(unsigned), Policy.parse(bad)])
        assert len(problems) == 1
        assert "as.c" in problems[0]

    def test_audit_signature_state_强制签名时报缺失(self, monkeypatch):
        monkeypatch.setenv("CP_POLICY_REQUIRE_SIGNATURE", "1")
        problems = audit_signature_state([Policy.parse(make_policy(id="as.d"))])
        assert any("未签名" in p for p in problems)

    def test_签名材料对_dict_与_Policy_一致(self, tmp_path):
        signer = PolicySigner(private_key_path=str(tmp_path / "k.pem"),
                              public_key_path=str(tmp_path / "k.pub.pem"))
        raw = make_policy(id="pl.same")
        assert signer.sign(raw) == signer.sign(Policy.parse(raw))
