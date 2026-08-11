"""FailureCollector 失败案例收集器单元测试

覆盖：
- AlertChannel / AlertLevel 枚举值
- AlertRule / AlertEvent dataclass 默认值
- FailureCollector 初始化、告警规则增删、失败收集、告警检查/触发/处理器
- 全局单例工厂与便捷函数 collect_failure

外部依赖（failure_analysis、时间）一律 mock。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring
import logging
from unittest.mock import Mock, patch

import pytest

from agent.cognitive.failure_analysis import (
    FailureRecord,
    FailureSeverity,
    FailureType,
)
from agent.cognitive.failure_collector import (
    AlertChannel,
    AlertEvent,
    AlertLevel,
    AlertRule,
    FailureCollector,
    _create_failure_collector,
    collect_failure,
    get_failure_collector,
)


# ═══════════════════════════════════════════════════════════
#  枚举与 dataclass
# ═══════════════════════════════════════════════════════════

class TestAlertEnums:
    """AlertChannel / AlertLevel 枚举"""

    def test_alert_channel_values(self):
        """AlertChannel 应包含 4 个渠道且 value 正确"""
        assert list(AlertChannel) == [
            AlertChannel.CONSOLE,
            AlertChannel.LOG,
            AlertChannel.WEBHOOK,
            AlertChannel.EMAIL,
        ]
        assert AlertChannel.CONSOLE.value == "console"
        assert AlertChannel.LOG.value == "log"
        assert AlertChannel.WEBHOOK.value == "webhook"
        assert AlertChannel.EMAIL.value == "email"

    def test_alert_level_values(self):
        """AlertLevel 应包含 4 个级别且 value 正确"""
        assert list(AlertLevel) == [
            AlertLevel.INFO,
            AlertLevel.WARNING,
            AlertLevel.CRITICAL,
            AlertLevel.FATAL,
        ]
        assert AlertLevel.INFO.value == "info"
        assert AlertLevel.WARNING.value == "warning"
        assert AlertLevel.CRITICAL.value == "critical"
        assert AlertLevel.FATAL.value == "fatal"


class TestAlertRuleDefaults:
    """AlertRule dataclass 默认值"""

    def test_defaults(self):
        """AlertRule 可选字段应具备正确默认值"""
        rule = AlertRule(rule_id="r1", name="规则")
        assert rule.failure_type is None
        assert rule.min_severity == FailureSeverity.MEDIUM
        assert rule.threshold == 1
        assert rule.time_window_seconds == 3600
        assert rule.channel == AlertChannel.LOG
        assert rule.enabled is True
        assert rule.cooldown_seconds == 300
        assert rule.last_alert_time == 0.0
        assert rule.description == ""


class TestAlertEventDefaults:
    """AlertEvent dataclass 默认值"""

    def test_defaults(self):
        """AlertEvent details 默认应为独立空字典"""
        event = AlertEvent(
            alert_id="a1", rule_id="r1", failure_type="api_fiction",
            severity="high", count=1, time_window_seconds=3600,
            triggered_at=1.0, message="m",
        )
        assert event.details == {}


# ═══════════════════════════════════════════════════════════
#  公共 fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def analyzer():
    """mock 的失败分析器：分类为 UNKNOWN、固定修复建议"""
    a = Mock()
    a.classify_failure.return_value = FailureType.UNKNOWN
    a.generate_fix_suggestion.return_value = "检查输入"
    return a


@pytest.fixture
def collector(analyzer):
    """每个测试独立、注入 mock analyzer 的收集器"""
    return FailureCollector(analyzer=analyzer)


def _make_alert(**overrides):
    """构造带默认字段的告警事件"""
    kwargs = dict(
        alert_id="a1", rule_id="r1", failure_type="api_fiction",
        severity="medium", count=1, time_window_seconds=3600,
        triggered_at=1.0, message="告警消息",
    )
    kwargs.update(overrides)
    return AlertEvent(**kwargs)


# ═══════════════════════════════════════════════════════════
#  初始化
# ═══════════════════════════════════════════════════════════

class TestFailureCollectorInit:
    """FailureCollector 初始化"""

    def test_injected_analyzer(self, analyzer):
        """显式传入 analyzer 时不应走工厂函数"""
        c = FailureCollector(analyzer=analyzer)
        assert c.analyzer is analyzer

    def test_default_analyzer_from_factory(self):
        """未传入 analyzer 时应从 get_failure_analyzer 获取"""
        fake = Mock()
        with patch("agent.cognitive.failure_collector.get_failure_analyzer", return_value=fake):
            c = FailureCollector()
        assert c.analyzer is fake

    def test_default_handlers_registered(self, collector):
        """初始化后应注册 CONSOLE 与 LOG 默认处理器"""
        assert collector._alert_handlers[AlertChannel.CONSOLE] == collector._handle_console_alert
        assert collector._alert_handlers[AlertChannel.LOG] == collector._handle_log_alert


class TestInitialize:
    """initialize 方法"""

    def test_registers_default_rules(self, collector):
        """首次初始化应注册 4 条默认告警规则"""
        collector.initialize()
        rule_ids = {r["rule_id"] for r in collector.get_alert_rules()}
        assert rule_ids == {
            "critical_api_fiction",
            "high_data_invention",
            "flow_skip_warning",
            "any_critical",
        }

    def test_idempotent(self, collector):
        """重复初始化不应重复注册默认规则"""
        collector.initialize()
        collector.initialize()
        assert len(collector.get_alert_rules()) == 4


# ═══════════════════════════════════════════════════════════
#  告警规则管理
# ═══════════════════════════════════════════════════════════

class TestAlertRuleManagement:
    """add_alert_rule / remove_alert_rule / register_alert_handler"""

    def test_add_alert_rule(self, collector):
        """添加规则后应出现在规则列表中"""
        collector.add_alert_rule(AlertRule(rule_id="r1", name="n"))
        assert any(r.rule_id == "r1" for r in collector._alert_rules)

    def test_remove_existing_rule(self, collector):
        """移除存在的规则应返回 True"""
        collector.add_alert_rule(AlertRule(rule_id="r1", name="n"))
        assert collector.remove_alert_rule("r1") is True
        assert all(r.rule_id != "r1" for r in collector._alert_rules)

    def test_remove_missing_rule(self, collector):
        """移除不存在的规则应返回 False"""
        assert collector.remove_alert_rule("missing") is False

    def test_register_alert_handler(self, collector):
        """注册自定义处理器应覆盖渠道映射"""
        handler = Mock()
        collector.register_alert_handler(AlertChannel.EMAIL, handler)
        assert collector._alert_handlers[AlertChannel.EMAIL] is handler


# ═══════════════════════════════════════════════════════════
#  失败收集
# ═══════════════════════════════════════════════════════════

class TestCollectFailure:
    """collect_failure 方法"""

    def test_creates_record(self, analyzer, collector):
        """应分类失败、生成建议并写入分析器，返回完整 FailureRecord"""
        record = collector.collect_failure(
            "t1", "消息", source="src", severity=FailureSeverity.HIGH,
            context={"a": 1}, evidence=["e1"],
        )
        analyzer.classify_failure.assert_called_once_with("消息")
        analyzer.generate_fix_suggestion.assert_called_once_with(FailureType.UNKNOWN)
        analyzer.record_failure.assert_called_once_with(record)
        assert isinstance(record, FailureRecord)
        assert record.trace_id == "t1"
        assert record.message == "消息"
        assert record.source == "src"
        assert record.severity == FailureSeverity.HIGH
        assert record.context == {"a": 1}
        assert record.evidence == ["e1"]
        assert record.suggested_fix == "检查输入"

    def test_default_arguments(self, analyzer, collector):
        """context/evidence 缺省时应降级为空容器，severity 默认 MEDIUM"""
        record = collector.collect_failure("t1", "消息")
        assert record.context == {}
        assert record.evidence == []
        assert record.severity == FailureSeverity.MEDIUM

    def test_triggers_alert(self, analyzer, collector):
        """达到规则阈值时应触发告警"""
        collector._alert_rules.clear()
        collector.add_alert_rule(AlertRule(
            rule_id="r1", name="n", failure_type=FailureType.UNKNOWN,
            min_severity=FailureSeverity.MEDIUM, threshold=1, cooldown_seconds=0,
        ))
        with patch.object(collector, "_trigger_alert") as trigger:
            collector.collect_failure("t1", "消息")
            trigger.assert_called_once()


# ═══════════════════════════════════════════════════════════
#  告警检查
# ═══════════════════════════════════════════════════════════

class TestCheckAlertRules:
    """_check_alert_rules 方法各分支"""

    def _setup(self, collector, **rule_kwargs):
        collector._alert_rules.clear()
        defaults = dict(
            rule_id="r1", name="n", failure_type=FailureType.API_FICTION,
            min_severity=FailureSeverity.MEDIUM, threshold=1,
            time_window_seconds=3600, channel=AlertChannel.LOG, cooldown_seconds=0,
        )
        defaults.update(rule_kwargs)
        rule = AlertRule(**defaults)
        collector.add_alert_rule(rule)
        return rule

    def test_threshold_trigger(self, collector):
        """计数达到阈值时应触发告警并更新 last_alert_time"""
        rule = self._setup(collector)
        record = FailureRecord(trace_id="t", failure_type=FailureType.API_FICTION)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_trigger_alert") as trigger:
            collector._check_alert_rules(
                FailureType.API_FICTION, FailureSeverity.HIGH, "t1", record)
            trigger.assert_called_once()
        assert rule.last_alert_time == 1000.0
        assert collector._failure_counts["api_fiction"] == [1000.0]
        assert collector._failure_counts["__all__"] == [1000.0]

    def test_threshold_not_reached(self, collector):
        """计数未达阈值时不触发告警"""
        self._setup(collector, threshold=2)
        record = FailureRecord(trace_id="t", failure_type=FailureType.API_FICTION)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_trigger_alert") as trigger:
            collector._check_alert_rules(
                FailureType.API_FICTION, FailureSeverity.HIGH, "t1", record)
            trigger.assert_not_called()

    def test_cooldown_prevents_repeated_alert(self, collector):
        """冷却期内重复命中不应再次触发"""
        self._setup(collector, cooldown_seconds=300)
        record = FailureRecord(trace_id="t", failure_type=FailureType.API_FICTION)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_trigger_alert") as trigger:
            collector._check_alert_rules(
                FailureType.API_FICTION, FailureSeverity.HIGH, "t1", record)
            trigger.assert_called_once()
            collector._check_alert_rules(
                FailureType.API_FICTION, FailureSeverity.HIGH, "t1", record)
            trigger.assert_called_once()  # 冷却生效，仍只有一次

    def test_severity_below_minimum_skipped(self, collector):
        """严重程度不达标时应跳过"""
        self._setup(collector, min_severity=FailureSeverity.CRITICAL)
        record = FailureRecord(trace_id="t", failure_type=FailureType.API_FICTION)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_trigger_alert") as trigger:
            collector._check_alert_rules(
                FailureType.API_FICTION, FailureSeverity.HIGH, "t1", record)
            trigger.assert_not_called()

    def test_failure_type_mismatch_skipped(self, collector):
        """失败类型不匹配时应跳过"""
        self._setup(collector)
        record = FailureRecord(trace_id="t", failure_type=FailureType.FLOW_SKIP)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_trigger_alert") as trigger:
            collector._check_alert_rules(
                FailureType.FLOW_SKIP, FailureSeverity.HIGH, "t1", record)
            trigger.assert_not_called()

    def test_disabled_rule_skipped(self, collector):
        """enabled=False 的规则应被跳过"""
        self._setup(collector, enabled=False)
        record = FailureRecord(trace_id="t", failure_type=FailureType.API_FICTION)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_trigger_alert") as trigger:
            collector._check_alert_rules(
                FailureType.API_FICTION, FailureSeverity.HIGH, "t1", record)
            trigger.assert_not_called()

    def test_all_types_rule(self, collector):
        """failure_type=None 的规则应对任意类型生效并计入 __all__ 计数"""
        self._setup(collector, failure_type=None, min_severity=FailureSeverity.CRITICAL)
        record = FailureRecord(trace_id="t", failure_type=FailureType.FLOW_SKIP)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_trigger_alert") as trigger:
            collector._check_alert_rules(
                FailureType.FLOW_SKIP, FailureSeverity.CRITICAL, "t1", record)
            trigger.assert_called_once()


# ═══════════════════════════════════════════════════════════
#  告警触发与处理器
# ═══════════════════════════════════════════════════════════

class TestTriggerAlert:
    """_trigger_alert 方法"""

    def test_invokes_registered_handler(self, collector):
        """应调用渠道对应处理器并组装完整 AlertEvent"""
        received = []
        collector.register_alert_handler(AlertChannel.WEBHOOK, received.append)
        rule = AlertRule(rule_id="r1", name="规则名", channel=AlertChannel.WEBHOOK)
        record = FailureRecord(
            trace_id="t", failure_type=FailureType.API_FICTION, source="src",
            suggested_fix="fix",
        )
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0):
            collector._trigger_alert(
                rule, FailureType.API_FICTION, FailureSeverity.HIGH, 3, "t1", record)
        assert len(received) == 1
        alert = received[0]
        assert alert.alert_id == "alert_1000_r1"
        assert alert.rule_id == "r1"
        assert alert.failure_type == "api_fiction"
        assert alert.severity == "high"
        assert alert.count == 3
        assert alert.time_window_seconds == 3600
        assert alert.triggered_at == 1000.0
        assert alert.details["rule_name"] == "规则名"
        assert alert.details["threshold"] == 1
        assert alert.details["trace_id"] == "t1"
        assert alert.details["source"] == "src"
        assert alert.details["suggested_fix"] == "fix"

    def test_handler_exception_swallowed(self, collector):
        """处理器抛异常不应中断，且日志处理器仍应执行"""
        def bad_handler(alert):
            raise RuntimeError("handler boom")
        collector.register_alert_handler(AlertChannel.WEBHOOK, bad_handler)
        rule = AlertRule(rule_id="r1", name="n", channel=AlertChannel.WEBHOOK)
        record = FailureRecord(trace_id="t", failure_type=FailureType.API_FICTION)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_handle_log_alert") as log_handler:
            collector._trigger_alert(
                rule, FailureType.API_FICTION, FailureSeverity.HIGH, 1, "t1", record)
            log_handler.assert_called_once()

    def test_no_handler_channel_falls_back_to_log(self, collector):
        """无对应处理器的渠道也应继续走日志处理器"""
        rule = AlertRule(rule_id="r1", name="n", channel=AlertChannel.EMAIL)
        record = FailureRecord(trace_id="t", failure_type=FailureType.API_FICTION)
        with patch("agent.cognitive.failure_collector.time.time", return_value=1000.0), \
             patch.object(collector, "_handle_log_alert") as log_handler:
            collector._trigger_alert(
                rule, FailureType.API_FICTION, FailureSeverity.HIGH, 1, "t1", record)
            log_handler.assert_called_once()


class TestHandleConsoleAlert:
    """_handle_console_alert 控制台输出"""

    def test_prints_alert_details(self, collector, capsys):
        """控制台处理器应输出告警关键信息"""
        alert = _make_alert(details={"trace_id": "t1", "suggested_fix": "修复" * 50})
        collector._handle_console_alert(alert)
        out = capsys.readouterr().out
        assert "告警" in out
        assert "a1" in out
        assert "api_fiction" in out
        assert "Trace ID: t1" in out
        assert "建议修复:" in out


class TestHandleLogAlert:
    """_handle_log_alert 日志输出"""

    def test_warning_level(self, collector, caplog):
        """非 critical/fatal 严重程度应记录为 WARNING"""
        alert = _make_alert(severity="medium", details={"trace_id": "t1"})
        with caplog.at_level(logging.WARNING, logger="agent.cognitive.failure_collector"):
            collector._handle_log_alert(alert)
        assert "[告警-WARNING]" in caplog.text
        assert "count=1" in caplog.text
        assert "window=3600s" in caplog.text
        assert "trace_id=t1" in caplog.text

    def test_critical_level(self, collector, caplog):
        """critical/fatal 严重程度应记录为 CRITICAL"""
        alert = _make_alert(severity="critical", details={})
        with caplog.at_level(logging.CRITICAL, logger="agent.cognitive.failure_collector"):
            collector._handle_log_alert(alert)
        assert "[告警-CRITICAL]" in caplog.text
        assert "trace_id=N/A" in caplog.text


# ═══════════════════════════════════════════════════════════
#  规则 / 统计查询
# ═══════════════════════════════════════════════════════════

class TestGetAlertRules:
    """get_alert_rules 方法"""

    def test_maps_rule_fields(self, collector):
        """规则应映射为完整字典且 failure_type=None 显示为 all"""
        collector._alert_rules.clear()
        collector.add_alert_rule(AlertRule(
            rule_id="r1", name="n", failure_type=None, min_severity=FailureSeverity.CRITICAL,
            threshold=2, time_window_seconds=600, channel=AlertChannel.EMAIL,
            enabled=False, cooldown_seconds=60, description="desc",
        ))
        rules = collector.get_alert_rules()
        assert len(rules) == 1
        item = rules[0]
        assert item["rule_id"] == "r1"
        assert item["name"] == "n"
        assert item["failure_type"] == "all"
        assert item["min_severity"] == "critical"
        assert item["threshold"] == 2
        assert item["time_window_seconds"] == 600
        assert item["channel"] == "email"
        assert item["enabled"] is False
        assert item["cooldown_seconds"] == 60
        assert item["description"] == "desc"

    def test_failure_type_value(self, collector):
        """failure_type 非空时应序列化为枚举 value"""
        collector._alert_rules.clear()
        collector.add_alert_rule(AlertRule(
            rule_id="r1", name="n", failure_type=FailureType.API_FICTION))
        assert collector.get_alert_rules()[0]["failure_type"] == "api_fiction"


class TestGetFailureStatistics:
    """get_failure_statistics 方法"""

    def test_merges_summary_and_rules(self, analyzer, collector):
        """应透传分析器统计并附带告警规则状态"""
        analyzer.get_failure_summary.return_value = {"total_failures": 3}
        collector.add_alert_rule(AlertRule(rule_id="r1", name="n"))
        stats = collector.get_failure_statistics(hours=48)
        analyzer.get_failure_summary.assert_called_once_with(hours=48)
        assert stats["total_failures"] == 3
        assert stats["alert_rules"] == collector.get_alert_rules()


# ═══════════════════════════════════════════════════════════
#  工厂 / 单例 / 便捷函数
# ═══════════════════════════════════════════════════════════

class TestFailureCollectorFactory:
    """工厂函数与全局单例"""

    def test_create_factory_initializes(self):
        """_create_failure_collector 应创建并初始化收集器"""
        with patch("agent.cognitive.failure_collector.FailureCollector") as cls:
            cls.return_value = Mock()
            _create_failure_collector()
            cls.return_value.initialize.assert_called_once()

    def test_get_returns_same_instance_fallback(self):
        """singleton 不可用时 fallback 单例应保持同一实例且已初始化"""
        import agent.cognitive.failure_collector as fc_mod
        with patch.object(fc_mod, "_SINGLETON_AVAILABLE", False), \
             patch.object(fc_mod, "_global_failure_collector", None), \
             patch.object(fc_mod, "_create_failure_collector",
                          wraps=fc_mod._create_failure_collector):
            inst1 = get_failure_collector()
            inst2 = get_failure_collector()
            assert inst1 is inst2
            assert isinstance(inst1, FailureCollector)
            fc_mod._create_failure_collector.assert_called_once()

    def test_get_via_singleton_manager(self):
        """singleton 可用时应走 get_singleton 路径"""
        import agent.cognitive.failure_collector as fc_mod
        fake = FailureCollector(analyzer=Mock())
        with patch.object(fc_mod, "_SINGLETON_AVAILABLE", True), \
             patch.object(fc_mod, "get_singleton", return_value=fake):
            assert get_failure_collector() is fake
            fc_mod.get_singleton.assert_called_once_with("failure_collector")


class TestCollectFailureFunction:
    """模块级 collect_failure 便捷函数"""

    def test_forwards_safe_kwargs(self):
        """应透传非保留字 kwargs（context 等）并保留显式参数"""
        import agent.cognitive.failure_collector as fc_mod
        with patch.object(fc_mod, "get_failure_collector") as getter:
            result = fc_mod.collect_failure(
                "t1", "消息", "src", severity=FailureSeverity.HIGH,
                context={"a": 1}, evidence=["e1"],
            )
            getter.assert_called_once_with()
            kwargs = getter.return_value.collect_failure.call_args.kwargs
            assert kwargs["trace_id"] == "t1"
            assert kwargs["message"] == "消息"
            assert kwargs["source"] == "src"
            assert kwargs["severity"] == FailureSeverity.HIGH
            assert kwargs["context"] == {"a": 1}
            assert kwargs["evidence"] == ["e1"]
            assert result is getter.return_value.collect_failure.return_value
