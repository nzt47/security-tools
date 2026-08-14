#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务 7：告警升级 → 人工接管完整链路 集成测试

覆盖 alert_manager.escalate / self_healer.verify_heal 升级接线：
- verify_heal 连续 2 次失败 → 告警升级（alert_escalated 结构化日志含 from/to level）
- 升级创建人工接管条目（open）→ 人工 resolve 闭环
- 自愈动作与 permission_system 黑名单双保险（security_blocked 记录）

验收标准对应：
- #4  verify_heal 连续 2 次失败触发 alert_escalated 且日志含 from/to level
- #6  自愈动作命中黑名单时返回 SKIPPED 且记录 security_blocked
"""
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from agent.monitoring.alert_manager import AlertManager
from agent.monitoring.self_healer import HealStatus, SelfHealer


@pytest.fixture
def alert_manager():
    """直接实例化 AlertManager（不触碰单例），测试后停掉接管清扫线程"""
    am = AlertManager(config_path=None)
    yield am
    am._takeover_queue.stop()


class TestEscalationChain:
    """verify_heal 失败 → 告警升级 → 接管 → resolve 完整链路（验收 #4）"""

    def test_verify_heal_twice_failure_escalates_and_takeover(self, alert_manager, caplog):
        am = alert_manager
        healer = am._healer

        with patch.object(healer, "verify_action", return_value=(False, "模拟验证失败")), \
                patch("agent.monitoring.self_healer.time.sleep"), \
                patch.object(am._notifier, "send_critical") as m_send_critical, \
                patch.object(am._notifier, "send"):
            with caplog.at_level(logging.INFO, logger="agent.monitoring.alert_manager"):
                ok1 = healer.verify_heal("restart_service", timeout=0)
                ok2 = healer.verify_heal("restart_service", timeout=0)

        # 两次验证均失败
        assert ok1 is False
        assert ok2 is False

        # 升级结构化日志：action + from/to level（验收 #4）
        assert '"action": "alert_escalated"' in caplog.text
        assert '"from_level": "warning"' in caplog.text
        assert '"to_level": "critical"' in caplog.text

        # 升级通知已发出（critical 渠道）
        assert m_send_critical.called

        # 创建人工接管条目（open 待接管）
        takeovers = am.get_takeovers()
        assert len(takeovers) == 1
        assert takeovers[0]["status"] == "open"
        assert takeovers[0]["alert_name"] == "heal_verify_failed:restart_service"

        # 人工处置闭环：assign → resolve
        tid = takeovers[0]["takeover_id"]
        assert am._takeover_queue.assign(tid, "ops-oncall") is True
        assert am._takeover_queue.resolve(tid, "人工重启服务并验证通过") is True
        resolved = am.get_takeovers(status="resolved")
        assert len(resolved) == 1
        assert resolved[0]["resolution"] == "人工重启服务并验证通过"

    def test_verify_heal_single_failure_no_escalation(self, alert_manager, caplog):
        """单次失败不升级（阈值=2），且无接管条目"""
        am = alert_manager
        healer = am._healer

        with patch.object(healer, "verify_action", return_value=(False, "模拟验证失败")), \
                patch("agent.monitoring.self_healer.time.sleep"):
            with caplog.at_level(logging.INFO, logger="agent.monitoring.alert_manager"):
                ok = healer.verify_heal("clear_cache", timeout=0)

        assert ok is False
        assert '"action": "alert_escalated"' not in caplog.text
        assert am.get_takeovers() == []

    def test_verify_success_resets_failure_counter(self, alert_manager):
        """验证成功清空连续失败计数（成功后再失败 1 次不升级）"""
        am = alert_manager
        healer = am._healer

        # mock 健康分模块（成功路径：健康分 >= 阈值 0.7）
        mock_module = MagicMock()
        mock_health = MagicMock()
        mock_health.overall = 0.8
        mock_module.health_assessor.get_history.return_value = [mock_health]

        # 失败 1 次（timeout=0 直接进入超时失败路径）
        with patch.object(healer, "verify_action", return_value=(False, "模拟验证失败")), \
                patch("time.sleep"):
            healer.verify_heal("gc_collect", timeout=0)

        # 成功（清空计数）
        with patch.object(healer, "verify_action", return_value=(True, "ok")), \
                patch("time.sleep"), \
                patch.dict(sys.modules, {"agent.health.assessor": mock_module}):
            assert healer.verify_heal("gc_collect", timeout=5.0) is True

        # 再次失败 1 次 → 计数从 0 起，不升级
        with patch.object(healer, "verify_action", return_value=(False, "模拟验证失败")), \
                patch("time.sleep"):
            healer.verify_heal("gc_collect", timeout=0)

        assert am.get_takeovers() == []

    def test_escalate_same_level_returns_none(self, alert_manager):
        """级别相同不重复升级（返回 None，不创建接管）"""
        am = alert_manager
        from agent.monitoring.alert_evaluator import Alert, AlertSeverity, AlertState
        alert = Alert(
            name="already-critical",
            state=AlertState.FIRING,
            severity=AlertSeverity.CRITICAL,
            value=0,
            threshold=0,
            condition="already_critical",
            message="已是最高级别",
        )
        with patch.object(am._notifier, "send_critical"):
            result = am.escalate(alert, AlertSeverity.CRITICAL, reason="已是最高级别")
        assert result is None
        assert am.get_takeovers() == []


class TestSelfHealSecurityBlocked:
    """自愈动作与权限黑名单双保险（验收 #6）"""

    def test_blacklist_hit_returns_skipped_with_security_blocked(self):
        """restart_command 含 rm -rf → SKIPPED(危险操作拦截) + security_blocked=True"""
        healer = SelfHealer(config={})
        result = healer.execute_action(
            "restart_service",
            context={"restart_command": "rm -rf /tmp/data", "service_name": "yunshu"},
        )
        assert result.status == HealStatus.SKIPPED
        assert "危险操作拦截" in result.message
        # 记录可查：security_blocked 字段为 True
        records = healer.get_records()
        assert records[-1]["security_blocked"] is True
        assert records[-1]["status"] == "skipped"

    def test_clear_cache_blacklist_hit(self):
        """cache_patterns 命中黑名单（format 盘符）→ 拦截"""
        healer = SelfHealer(config={})
        result = healer.execute_action(
            "clear_cache",
            context={"cache_paths": ["/tmp/cache"], "cache_patterns": ["format C: /q"]},
        )
        assert result.status == HealStatus.SKIPPED
        assert "危险操作拦截" in result.message
        assert healer.get_records()[-1]["security_blocked"] is True

    def test_safe_command_not_blocked(self):
        """安全命令不拦截（_check_security 返回 None）"""
        healer = SelfHealer(config={})
        assert healer._check_security(
            "restart_service", {"restart_command": ["x.cmd"], "service_name": "yunshu"}
        ) is None
        # 非破坏性动作不校验（无破坏面）
        assert healer._check_security("gc_collect", {}) is None
