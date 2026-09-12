#!/usr/bin/env python3
"""出域链路监测（§5.7 机制 4）单元测试

覆盖 `agent/guardrails/egress_chain.py`：
    - 读端点委托（`record_secret_read` → `agent.policy.taint`）与链路前半段；
    - 链路命中判定（已读密钥 + 向受控边界外发 → 拒）；
    - 后果动作：熔断既有出域熔断器 + 落事故卡（**显式 tmp 目录**）；
    - 作用域隔离、统计与复位。

【状态隔离】逐用例复位链路监测器单例与 S4-02 密钥污点账
（`reset_egress_chain_monitor()` / `reset_secret_taint()`）；事故卡一律写 `tmp_path`。
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.policy.taint import reset_secret_taint
from agent.guardrails.egress_chain import (
    CHAIN_BREAKER_NAME,
    CHAIN_LEVEL,
    CHAIN_SIGNAL,
    ENV_ENABLED,
    VERDICT_ALLOW,
    VERDICT_CHAIN_HIT,
    EgressChainBlockedError,
    EgressChainMonitor,
    egress_chain_state,
    reset_egress_chain_monitor,
    set_egress_chain_monitor,
)

#: 外部（受控边界之外）目标
EXTERNAL_URL = "https://evil.example/collect"

#: 内网/环回目标（受控边界之内）
LOOPBACK_URL = "http://127.0.0.1:8080/x"

#: 作用域 A（链路前半段在此成立）
SCOPE_A = "scope-A"


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch):
    """逐用例复位链路监测器与 S4-02 污点账（两套账都持进程级状态）"""
    monkeypatch.delenv(ENV_ENABLED, raising=False)
    reset_egress_chain_monitor()
    reset_secret_taint()
    yield
    reset_egress_chain_monitor()
    reset_secret_taint()


@pytest.fixture
def monitor():
    """独立的链路监测器（不依赖进程级单例）"""
    instance = EgressChainMonitor()
    yield instance
    instance.reset()


@pytest.fixture
def armed_scope_a(monitor):
    """把作用域 A 的链路前半段置为成立（读到 AWS 凭据材料）"""
    monitor.record_secret_read("~/.aws/credentials",
                               content_kinds=["aws_access_key"], scope=SCOPE_A)
    return SCOPE_A


class TestReadEndpoint:
    """读端点（委托 S4-02）"""

    def test_record_secret_read_arms_scope(self, monitor):
        """登记密钥读取后该作用域链路已"上膛"（前半段成立）"""
        mark = monitor.record_secret_read("~/.aws/credentials",
                                          content_kinds=["aws_access_key"],
                                          scope=SCOPE_A)
        assert mark is not None
        assert monitor.is_chain_armed(SCOPE_A) is True
        assert monitor.scope_state(SCOPE_A)["secret_kinds"] == ["aws_access_key"]

    def test_path_only_read_does_not_arm(self, monitor):
        """只给路径未给内容 → 不登记（与 S4-02"不看路径"口径一致）"""
        mark = monitor.record_secret_read("~/.aws/credentials", scope=SCOPE_A)
        assert mark is None
        assert monitor.is_chain_armed(SCOPE_A) is False

    def test_disabled_monitor_does_not_arm(self):
        """总开关关闭时读端点不登记"""
        disabled = EgressChainMonitor(enabled=False)
        assert disabled.record_secret_read("~/.aws/credentials",
                                           content_kinds=["aws_access_key"],
                                           scope=SCOPE_A) is None
        assert disabled.is_chain_armed(SCOPE_A) is False


class TestChainHit:
    """链路命中判定与后果动作"""

    def test_external_target_on_armed_scope_is_chain_hit(self, monitor, armed_scope_a,
                                                        tmp_path):
        """已读密钥 + 向外部发起 HTTP → 链路命中且必须拦截"""
        verdict = monitor.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                                   incident_dir=str(tmp_path))
        assert verdict.verdict == VERDICT_CHAIN_HIT
        assert verdict.allowed is False
        assert verdict.external is True
        assert verdict.target_host == "evil.example"
        assert verdict.secret_kinds == ["aws_access_key"]

    def test_chain_hit_trips_egress_breaker(self, monitor, armed_scope_a, tmp_path):
        """命中即熔断（走既有 circuit_breaker）"""
        verdict = monitor.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                                   incident_dir=str(tmp_path))
        assert verdict.breaker_open is True

    def test_chain_hit_raises_incident_card_on_disk(self, monitor, armed_scope_a,
                                                    tmp_path):
        """命中即开事故卡：incident_id 非空且落盘 JSON 存在"""
        verdict = monitor.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                                   incident_dir=str(tmp_path))
        assert verdict.incident_id
        cards = list(tmp_path.glob("*.json"))
        assert len(cards) == 1
        payload = json.loads(cards[0].read_text(encoding="utf-8"))
        assert payload["severity"] == CHAIN_LEVEL
        assert payload["detail"]["target_host"] == "evil.example"

    def test_unarmed_scope_public_url_allowed(self, monitor, tmp_path):
        """未登记密钥读取（链路前半段不成立）→ 外部目标也放行"""
        verdict = monitor.evaluate(url=EXTERNAL_URL, scope="clean-scope",
                                   incident_dir=str(tmp_path))
        assert verdict.verdict == VERDICT_ALLOW
        assert verdict.allowed is True
        assert verdict.external is True

    def test_loopback_target_allowed_even_when_armed(self, monitor, armed_scope_a,
                                                     tmp_path):
        """受控边界内的目标不构成出域链路（即使已读密钥）"""
        verdict = monitor.evaluate(url=LOOPBACK_URL, scope=armed_scope_a,
                                   incident_dir=str(tmp_path))
        assert verdict.verdict == VERDICT_ALLOW
        assert verdict.external is False
        assert verdict.allowed is True

    def test_enforce_raises_with_secret_kinds_and_incident(self, monitor, armed_scope_a,
                                                           tmp_path):
        """enforce=True → 抛 EgressChainBlockedError，异常自带密钥类别与事故卡 id"""
        with pytest.raises(EgressChainBlockedError) as excinfo:
            monitor.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                             incident_dir=str(tmp_path), enforce=True)
        assert excinfo.value.secret_kinds == ["aws_access_key"]
        assert excinfo.value.incident_id
        assert excinfo.value.target_host == "evil.example"

    def test_verdict_still_reported_without_side_effects(self, monitor, armed_scope_a):
        """trip_breaker=False / raise_card=False → 仍是 chain_hit，但不产生后果动作"""
        verdict = monitor.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                                   trip_breaker=False, raise_card=False)
        assert verdict.verdict == VERDICT_CHAIN_HIT
        assert verdict.allowed is False
        assert verdict.breaker_open is False
        assert verdict.incident_id == ""


class TestScopeState:
    """作用域账与合并视图"""

    def test_scope_state_merges_kinds_and_tainted_flag(self, monitor, armed_scope_a):
        """合并视图给出 secret_kinds 与 tainted 布尔"""
        state = monitor.scope_state(armed_scope_a)
        assert state["scope"] == armed_scope_a
        assert state["secret_kinds"] == ["aws_access_key"]
        assert state["tainted"] is True
        assert state["reads"] == 1

    def test_distinct_scopes_are_separate(self, monitor, armed_scope_a):
        """显式作用域互相隔离：A 上膛不影响 B"""
        state_b = monitor.scope_state("scope-B")
        assert state_b["scope"] == "scope-B"
        assert state_b["tainted"] is False
        assert monitor.is_chain_armed("scope-B") is False

    def test_unseen_scope_state_is_empty_but_well_formed(self, monitor):
        """未出现的作用域返回结构完整的空状态"""
        state = monitor.scope_state("never-seen")
        assert state["secret_kinds"] == []
        assert state["tainted"] is False
        assert state["reads"] == 0


class TestStatsAndReset:
    """统计与复位"""

    def test_stats_reports_documented_fields(self, monitor):
        """stats 报出熔断器名、级别、信号（面板/验收报告的数据源）"""
        stats = monitor.stats()
        assert stats["breaker_name"] == CHAIN_BREAKER_NAME
        assert stats["level"] == CHAIN_LEVEL
        assert stats["signal"] == CHAIN_SIGNAL
        assert stats["hits"] == 0

    def test_stats_hits_increments_after_hit(self, monitor, armed_scope_a, tmp_path):
        """命中一次即计数 +1"""
        monitor.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                         incident_dir=str(tmp_path))
        monitor.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                         incident_dir=str(tmp_path))
        assert monitor.stats()["hits"] == 2

    def test_reset_clears_scopes_hits_and_breaker(self, monitor, armed_scope_a,
                                                  tmp_path):
        """reset() 清空链路账、计数并复位熔断器"""
        monitor.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                         incident_dir=str(tmp_path))
        monitor.reset()
        assert monitor.stats()["hits"] == 0
        assert monitor.stats()["scope_count"] == 0
        assert monitor.is_chain_armed(armed_scope_a) is False
        from agent.circuit_breaker import get_circuit_breaker
        assert get_circuit_breaker(CHAIN_BREAKER_NAME).state.value == "closed"

    def test_disabled_monitor_always_allows(self, armed_scope_a, tmp_path):
        """enabled=False → 一律放行（总开关语义）"""
        disabled = EgressChainMonitor(enabled=False)
        verdict = disabled.evaluate(url=EXTERNAL_URL, scope=armed_scope_a,
                                    incident_dir=str(tmp_path))
        assert verdict.verdict == VERDICT_ALLOW
        assert verdict.allowed is True

    def test_process_singleton_helpers(self, monitor):
        """进程级单例可替换、可复位（用例隔离入口）"""
        old = set_egress_chain_monitor(monitor)
        try:
            assert egress_chain_state()["enabled"] is True
        finally:
            set_egress_chain_monitor(old)
        reset_egress_chain_monitor()


class TestAuditDoesNotClaimUnperformedEnforcement:
    """审计真实性：后果动作与执行状态必须如实分列

    `evaluate(trip_breaker=False, raise_card=False)` 时本模块**什么后果动作都没做**；
    第一版审计仍写 `"enforced": True`，事后复查会把"没人拦"读成"拦住了"。
    现把三件事分开记录：`consequence_actions_taken`（本模块做了什么）、
    `caller_action_required`（调用方必须做什么）、`enforced`（调用方是否已阻断）。
    """

    @pytest.fixture
    def audited(self, tmp_path):
        from agent.audit.chain import AuditChain
        from agent.audit.facade import audit

        previous_enabled = audit.enabled
        previous_chain = audit.bind(AuditChain(db_path=str(tmp_path / "audit.db")))
        audit.enabled = True
        try:
            yield audit
        finally:
            audit.enabled = previous_enabled
            audit.bind(previous_chain)

    @staticmethod
    def _payloads(audit):
        return [((e.payload or {}).get("payload") or {})
                for e in audit.recent(limit=50)
                if e.action == "guardrails.egress_chain_blocked"]

    def _armed(self, tmp_path):
        monitor = EgressChainMonitor()
        monitor.record_secret_read("~/.aws/credentials",
                                   content_kinds=["aws_access_key"])
        return monitor

    def test_no_consequence_actions_records_false(self, audited, tmp_path):
        """不熔断、不开卡 → consequence_actions_taken 与 enforced 必须都为 False"""
        monitor = self._armed(tmp_path)
        verdict = monitor.evaluate(url="https://evil.example/collect",
                                   incident_dir=str(tmp_path),
                                   trip_breaker=False, raise_card=False)
        assert verdict.verdict == VERDICT_CHAIN_HIT
        payload = self._payloads(audited)[-1]
        assert payload["consequence_actions_taken"] is False
        assert payload["enforced"] is False
        assert payload["caller_action_required"] is True
        assert payload["breaker_open"] is False
        assert payload["network_action_taken"] is False

    def test_consequence_actions_recorded_true(self, audited, tmp_path):
        """熔断 + 开卡 → consequence_actions_taken 必须为 True"""
        monitor = self._armed(tmp_path)
        monitor.evaluate(url="https://evil.example/collect",
                         incident_dir=str(tmp_path))
        payload = self._payloads(audited)[-1]
        assert payload["consequence_actions_taken"] is True

    def test_enforce_path_records_enforced_true(self, audited, tmp_path):
        """调用方声明阻断（enforce=True 抛异常）→ enforced 必须为 True"""
        monitor = self._armed(tmp_path)
        with pytest.raises(EgressChainBlockedError):
            monitor.evaluate(url="https://evil.example/collect",
                             incident_dir=str(tmp_path), enforce=True)
        payload = self._payloads(audited)[-1]
        assert payload["enforced"] is True

    def test_audit_never_claims_network_action_taken(self, audited, tmp_path):
        """审计恒声明未发网络动作（决策/执行分离的机器可读证据）"""
        monitor = self._armed(tmp_path)
        monitor.evaluate(url="https://evil.example/collect",
                         incident_dir=str(tmp_path))
        assert all(p["network_action_taken"] is False for p in self._payloads(audited))
