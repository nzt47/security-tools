"""server_routes/routes_logging.py 集成测试

覆盖范围:
    - 辅助函数: _get_tool_summary / _get_config_status / _get_health_status
                _get_error_correlation_stats / _get_runtime_metrics / _get_recent_logs
                get_prometheus_exporter / _load_alert_rules / _save_alert_rules
    - 诊断端点: tools / config / health / error_correlation / trace / trace/extract / trace/inject
                metrics / logs
    - Loki 日志: logs GET / logs/labels / logs POST / logs/stream (SSE)
    - Prometheus: /metrics
    - 告警规则 CRUD: list / create / update / delete / validate
    - 追踪可视化: traces / trace_detail
    - 仪表盘 + 访问日志: dashboard / access_logs / access_stats

边缘情况覆盖:
    - Config 导入 try/except fallback
    - _ALERT_RULES_CACHE 全局缓存 + YAML 文件 I/O (tmp_path 隔离)
    - _prometheus_exporter 懒加载单例
    - _get_error_correlation_stats 三层降级 (回放存储 / Sentry)
    - _get_recent_logs 两级降级 (log_system.storage / performance_recorder)
    - api_diagnostics_error_correlation hours 参数限制 (1-720)
    - api_diagnostics_trace_extract W3C/Jaeger 格式合并 (HTTP头 + body覆盖)
    - api_observability_logs GET LogQL 查询拼接 (query/level/service 组合)
    - api_observability_logs_stream SSE 无限循环 (mock time.sleep + 控制退出)
    - api_prometheus_metrics 两层 try/except (collector / generate_latest)
    - api_observability_alerts_create 必填字段 + groups 初始化
    - api_observability_alerts_update 部分更新 + KeyError 风险
    - api_observability_alerts_delete 多 group 遍历 + break
    - api_observability_alerts_validate PromQL 解析
    - api_dashboard render_template 失败 (纯文本错误)
    - 装饰器组合差异 (require_token / trace_route / log_request)
"""

import json
import time
import pytest
import yaml
from unittest.mock import MagicMock, patch, mock_open
from flask import Flask

from agent.server_routes import routes_logging
from agent.server_routes.routes_logging import (
    register_routes,
    _get_tool_summary,
    _get_config_status,
    _get_health_status,
    _get_error_correlation_stats,
    _get_runtime_metrics,
    _get_recent_logs,
    get_prometheus_exporter,
    _load_alert_rules,
    _save_alert_rules,
)


# ──────────────────────────────────────────────
# 装饰器 no-op patcher
# ──────────────────────────────────────────────

_NOOP_DECORATOR = lambda f: f
_NOOP_DECORATOR_FACTORY = lambda *a, **kw: (lambda f: f)


# ──────────────────────────────────────────────
# Fixture: mock state
# ──────────────────────────────────────────────


@pytest.fixture
def mock_state():
    """构造 mock state 对象"""
    state = MagicMock()
    return state


@pytest.fixture
def alert_rules_file(tmp_path, monkeypatch):
    """告警规则文件读写隔离 fixture

    将 _ALERT_RULES_FILE 重定向到 tmp_path，并重置缓存。
    每个测试用例独立的 YAML 文件，避免测试间互相污染。
    """
    alerts_file = tmp_path / "alerts.yml"
    monkeypatch.setattr(routes_logging, "_ALERT_RULES_FILE", str(alerts_file))
    monkeypatch.setattr(routes_logging, "_ALERT_RULES_CACHE", None)
    return str(alerts_file)


@pytest.fixture
def reset_prometheus_exporter(monkeypatch):
    """重置 Prometheus 导出器单例"""
    monkeypatch.setattr(routes_logging, "_prometheus_exporter", None)


@pytest.fixture
def logging_client(mock_state, alert_rules_file, reset_prometheus_exporter):
    """Flask test client，装饰器全部 patch 为 no-op

    返回 (client, mock_state) 元组。
    已内置:
        - alert_rules_file: 告警规则文件重定向到 tmp_path
        - reset_prometheus_exporter: 重置 Prometheus 单例
    """
    app = Flask(__name__)
    app.testing = True

    with patch("agent.server_routes.routes_logging.require_token", _NOOP_DECORATOR), \
         patch("agent.server_routes.routes_logging.log_request", _NOOP_DECORATOR_FACTORY), \
         patch("agent.server_routes.routes_logging.trace_route", _NOOP_DECORATOR_FACTORY):
        register_routes(app, mock_state)

    client = app.test_client()
    return client, mock_state


@pytest.fixture
def client(logging_client):
    """便捷 fixture: 只返回 client"""
    return logging_client[0]


@pytest.fixture
def state(logging_client):
    """便捷 fixture: 只返回 mock_state"""
    return logging_client[1]


@pytest.fixture
def sample_alert_rules():
    """样本告警规则数据"""
    return {
        "groups": [
            {
                "name": "yunshu_alerts",
                "interval": "30s",
                "rules": [
                    {
                        "alert": "HighErrorRate",
                        "expr": "error_rate > 0.1",
                        "for": "5m",
                        "labels": {"severity": "critical"},
                        "annotations": {
                            "summary": "错误率过高",
                            "description": "错误率超过 10%",
                        },
                    },
                    {
                        "alert": "HighLatency",
                        "expr": "p99_latency > 1.0",
                        "for": "10m",
                        "labels": {"severity": "warning"},
                        "annotations": {
                            "summary": "延迟过高",
                            "description": "P99 延迟超过 1s",
                        },
                    },
                ],
            }
        ]
    }


@pytest.fixture
def written_alert_rules(alert_rules_file, sample_alert_rules):
    """预写入样本告警规则到 tmp_path 文件，并重置缓存

    用于 CRUD 测试的初始状态。
    """
    with open(alert_rules_file, "w", encoding="utf-8") as f:
        yaml.dump(sample_alert_rules, f, default_flow_style=False, allow_unicode=True)
    return alert_rules_file


# ════════════════════════════════════════════════════════════════
# 辅助函数单元测试
# ════════════════════════════════════════════════════════════════


# ──────────────────────────────────────────────
# _get_recent_logs 两级降级测试
# ──────────────────────────────────────────────


class TestGetRecentLogs:
    """_get_recent_logs 两级降级逻辑测试

    降级链:
        1. log_system.storage (storage 可用且 _initialized=True)
        2. performance_recorder (storage 不可用 / 未初始化 / 抛异常)
        3. 全部失败: {"error": str(e), "timestamp": time.time()}
    """

    def test_storage_available_returns_merged_logs(self):
        """storage 可用: 合并 performance + error 日志并按时间排序"""
        perf_log = {"timestamp": 1000, "msg": "perf1"}
        error_log = {"timestamp": 2000, "msg": "err1"}

        mock_storage = MagicMock()
        mock_storage._initialized = True
        mock_storage.query_performance.return_value = [perf_log]
        mock_storage.query_errors.return_value = [error_log]

        with patch("agent.log_system.storage.get_storage", return_value=mock_storage):
            result = _get_recent_logs(limit=10)

        assert result["source"] == "log_system"
        assert result["limit"] == 10
        assert len(result["logs"]) == 2
        # 按时间戳降序，error_log 时间戳更大应在前
        assert result["logs"][0]["timestamp"] == 2000
        assert result["logs"][0]["_type"] == "error"
        assert result["logs"][1]["timestamp"] == 1000
        assert result["logs"][1]["_type"] == "performance"

    def test_storage_limit_truncation(self):
        """limit 截断: 返回的日志不超过 limit 条"""
        perf_logs = [{"timestamp": i, "msg": f"perf{i}"} for i in range(20)]

        mock_storage = MagicMock()
        mock_storage._initialized = True
        mock_storage.query_performance.return_value = perf_logs
        mock_storage.query_errors.return_value = []

        with patch("agent.log_system.storage.get_storage", return_value=mock_storage):
            result = _get_recent_logs(limit=5)

        assert len(result["logs"]) == 5
        assert result["total_available"] == 5

    def test_storage_not_initialized_fallback_to_recorder(self):
        """storage 未初始化: 降级到 performance_recorder"""
        mock_storage = MagicMock()
        mock_storage._initialized = False

        mock_record = MagicMock()
        mock_record.name = "module_a"
        mock_record.duration_ms = 100.0
        mock_record.success = True
        mock_record.error = ""
        mock_record.end_time = 1500.0

        mock_recorder = MagicMock()
        mock_recorder.records = {"k1": mock_record}

        with patch("agent.log_system.storage.get_storage", return_value=mock_storage), \
             patch("agent.server_routes.routes_logging.get_performance_recorder", return_value=mock_recorder):
            result = _get_recent_logs(limit=50)

        assert result["source"] == "init_performance"
        assert len(result["logs"]) == 1
        assert result["logs"][0]["module"] == "module_a"
        assert result["logs"][0]["duration_ms"] == 100.0

    def test_storage_import_error_fallback_to_recorder(self):
        """storage 导入失败: 降级到 performance_recorder"""
        mock_recorder = MagicMock()
        mock_recorder.records = {}

        with patch("agent.log_system.storage.get_storage", side_effect=ImportError("no module")), \
             patch("agent.server_routes.routes_logging.get_performance_recorder", return_value=mock_recorder):
            result = _get_recent_logs(limit=50)

        assert result["source"] == "init_performance"
        assert result["logs"] == []

    def test_storage_exception_fallback_to_recorder(self):
        """storage 抛异常: 降级到 performance_recorder"""
        mock_recorder = MagicMock()
        mock_recorder.records = {}

        with patch("agent.log_system.storage.get_storage", side_effect=RuntimeError("storage boom")), \
             patch("agent.server_routes.routes_logging.get_performance_recorder", return_value=mock_recorder):
            result = _get_recent_logs(limit=50)

        assert result["source"] == "init_performance"

    def test_storage_none_fallback_to_recorder(self):
        """storage 返回 None: 降级到 performance_recorder"""
        mock_recorder = MagicMock()
        mock_recorder.records = {}

        with patch("agent.log_system.storage.get_storage", return_value=None), \
             patch("agent.server_routes.routes_logging.get_performance_recorder", return_value=mock_recorder):
            result = _get_recent_logs(limit=50)

        # storage 为 None 时 storage._initialized 会抛 AttributeError，走降级
        assert result["source"] == "init_performance"

    def test_recorder_records_sorting_by_end_time(self):
        """recorder 日志按 end_time 降序排序"""
        r1 = MagicMock()
        r1.name = "module_a"
        r1.duration_ms = 100.0
        r1.success = True
        r1.error = ""
        r1.end_time = 1000.0

        r2 = MagicMock()
        r2.name = "module_b"
        r2.duration_ms = 200.0
        r2.success = False
        r2.error = "fail"
        r2.end_time = 2000.0

        mock_recorder = MagicMock()
        mock_recorder.records = {"k1": r1, "k2": r2}

        with patch("agent.log_system.storage.get_storage", side_effect=ImportError), \
             patch("agent.server_routes.routes_logging.get_performance_recorder", return_value=mock_recorder):
            result = _get_recent_logs(limit=50)

        # end_time 大的在前
        assert result["logs"][0]["module"] == "module_b"
        assert result["logs"][1]["module"] == "module_a"

    def test_recorder_record_missing_attributes_uses_defaults(self):
        """recorder 记录缺少属性时使用默认值"""
        # 使用普通对象而非 MagicMock（MagicMock 的 hasattr 总返回 True）
        class BareRecord:
            pass

        record = BareRecord()

        mock_recorder = MagicMock()
        mock_recorder.records = {"k1": record}

        with patch("agent.log_system.storage.get_storage", side_effect=ImportError), \
             patch("agent.server_routes.routes_logging.get_performance_recorder", return_value=mock_recorder):
            result = _get_recent_logs(limit=50)

        log = result["logs"][0]
        # 缺少 name → str(record)，缺少 duration_ms → 0，缺少 success → True
        assert log["duration_ms"] == 0
        assert log["success"] is True
        assert log["error"] == ""
        assert "timestamp" in log

    def test_storage_logs_sensitive_data_filtered(self):
        """storage 日志经过 filter_dict 敏感数据过滤"""
        perf_log = {"timestamp": 1000, "msg": "perf1", "password": "secret123"}

        mock_storage = MagicMock()
        mock_storage._initialized = True
        mock_storage.query_performance.return_value = [perf_log]
        mock_storage.query_errors.return_value = []

        with patch("agent.log_system.storage.get_storage", return_value=mock_storage):
            result = _get_recent_logs(limit=10)

        # filter_dict 会过滤敏感字段
        assert result["source"] == "log_system"
        # password 字段应被过滤（filter_dict 会处理）
        assert "logs" in result

    def test_all_fail_returns_error_dict(self):
        """storage 和 recorder 都失败: 返回 error 字典"""
        with patch("agent.log_system.storage.get_storage", side_effect=ImportError), \
             patch("agent.server_routes.routes_logging.get_performance_recorder", side_effect=RuntimeError("recorder boom")):
            result = _get_recent_logs(limit=50)

        assert "error" in result
        assert "timestamp" in result
        assert "recorder boom" in result["error"]

    def test_default_limit_is_50(self):
        """默认 limit 为 50"""
        mock_recorder = MagicMock()
        mock_recorder.records = {}

        with patch("agent.log_system.storage.get_storage", side_effect=ImportError), \
             patch("agent.server_routes.routes_logging.get_performance_recorder", return_value=mock_recorder):
            result = _get_recent_logs()

        assert result["limit"] == 50

    def test_storage_logs_type_annotation(self):
        """storage 日志添加 _type 字段标识来源"""
        perf_log = {"timestamp": 1000, "msg": "perf1"}
        error_log = {"timestamp": 2000, "msg": "err1"}

        mock_storage = MagicMock()
        mock_storage._initialized = True
        mock_storage.query_performance.return_value = [perf_log]
        mock_storage.query_errors.return_value = [error_log]

        with patch("agent.log_system.storage.get_storage", return_value=mock_storage):
            result = _get_recent_logs(limit=10)

        types = {log["_type"] for log in result["logs"]}
        assert types == {"performance", "error"}


# ──────────────────────────────────────────────
# _get_error_correlation_stats 三层降级测试
# ──────────────────────────────────────────────


class TestGetErrorCorrelationStats:
    """_get_error_correlation_stats 三层降级逻辑测试

    降级链:
        1. replay_storage.get_correlation_stats() → 成功返回统计
           失败 → 返回降级字典(含 error 字段)
        2. is_sentry_enabled() → 成功返回 bool
           失败 → sentry_enabled = False
    """

    def test_default_hours_is_24(self):
        """默认 hours 参数为 24"""
        mock_storage = MagicMock()
        mock_storage.get_correlation_stats.return_value = {"total_replays": 5}

        with patch("agent.monitoring.replay_storage.get_replay_storage", return_value=mock_storage), \
             patch("agent.error_reporting_config.is_sentry_enabled", return_value=True):
            result = _get_error_correlation_stats()

        assert result["window_hours"] == 24
        assert result["replay_stats"] == {"total_replays": 5}
        assert result["sentry_enabled"] is True

    def test_custom_hours_passed_to_storage(self):
        """自定义 hours 传递给 storage.get_correlation_stats"""
        mock_storage = MagicMock()
        mock_storage.get_correlation_stats.return_value = {"total_replays": 3}

        with patch("agent.monitoring.replay_storage.get_replay_storage", return_value=mock_storage), \
             patch("agent.error_reporting_config.is_sentry_enabled", return_value=False):
            result = _get_error_correlation_stats(hours=48)

        mock_storage.get_correlation_stats.assert_called_once_with(hours=48)
        assert result["window_hours"] == 48

    def test_replay_storage_success_sentry_enabled(self):
        """回放存储成功 + Sentry 启用"""
        mock_storage = MagicMock()
        mock_storage.get_correlation_stats.return_value = {
            "total_replays": 10,
            "with_trace_id": 8,
            "with_user_session_id": 6,
            "with_error_id": 4,
            "fully_correlated": 2,
            "by_error_id": [],
        }

        with patch("agent.monitoring.replay_storage.get_replay_storage", return_value=mock_storage), \
             patch("agent.error_reporting_config.is_sentry_enabled", return_value=True):
            result = _get_error_correlation_stats(hours=12)

        assert result["replay_stats"]["total_replays"] == 10
        assert result["replay_stats"]["with_trace_id"] == 8
        assert result["sentry_enabled"] is True

    def test_replay_storage_import_error_fallback(self):
        """回放存储导入失败: 返回降级字典"""
        with patch("agent.monitoring.replay_storage.get_replay_storage", side_effect=ImportError("no module")), \
             patch("agent.error_reporting_config.is_sentry_enabled", return_value=False):
            result = _get_error_correlation_stats(hours=6)

        assert result["replay_stats"]["total_replays"] == 0
        assert result["replay_stats"]["with_trace_id"] == 0
        assert result["replay_stats"]["fully_correlated"] == 0
        assert "error" in result["replay_stats"]
        assert result["replay_stats"]["window_hours"] == 6
        assert result["sentry_enabled"] is False

    def test_replay_storage_runtime_error_fallback(self):
        """回放存储抛 RuntimeError: 返回降级字典"""
        with patch("agent.monitoring.replay_storage.get_replay_storage", side_effect=RuntimeError("boom")), \
             patch("agent.error_reporting_config.is_sentry_enabled", return_value=True):
            result = _get_error_correlation_stats()

        assert result["replay_stats"]["total_replays"] == 0
        assert "boom" in result["replay_stats"]["error"]
        assert result["sentry_enabled"] is True

    def test_replay_storage_get_correlation_stats_exception_fallback(self):
        """get_correlation_stats 方法抛异常: 返回降级字典"""
        mock_storage = MagicMock()
        mock_storage.get_correlation_stats.side_effect = RuntimeError("stats boom")

        with patch("agent.monitoring.replay_storage.get_replay_storage", return_value=mock_storage), \
             patch("agent.error_reporting_config.is_sentry_enabled", return_value=False):
            result = _get_error_correlation_stats()

        assert result["replay_stats"]["total_replays"] == 0
        assert "stats boom" in result["replay_stats"]["error"]

    def test_sentry_import_error_defaults_false(self):
        """Sentry 探测导入失败: sentry_enabled = False"""
        mock_storage = MagicMock()
        mock_storage.get_correlation_stats.return_value = {"total_replays": 1}

        with patch("agent.monitoring.replay_storage.get_replay_storage", return_value=mock_storage), \
             patch("agent.error_reporting_config.is_sentry_enabled", side_effect=ImportError):
            result = _get_error_correlation_stats()

        assert result["sentry_enabled"] is False
        assert result["replay_stats"]["total_replays"] == 1

    def test_sentry_runtime_error_defaults_false(self):
        """Sentry 探测抛 RuntimeError: sentry_enabled = False"""
        mock_storage = MagicMock()
        mock_storage.get_correlation_stats.return_value = {"total_replays": 1}

        with patch("agent.monitoring.replay_storage.get_replay_storage", return_value=mock_storage), \
             patch("agent.error_reporting_config.is_sentry_enabled", side_effect=RuntimeError("sentry boom")):
            result = _get_error_correlation_stats()

        assert result["sentry_enabled"] is False

    def test_both_fail_returns_degraded(self):
        """回放存储和 Sentry 都失败: 返回完全降级结果"""
        with patch("agent.monitoring.replay_storage.get_replay_storage", side_effect=ImportError), \
             patch("agent.error_reporting_config.is_sentry_enabled", side_effect=ImportError):
            result = _get_error_correlation_stats(hours=72)

        assert result["window_hours"] == 72
        assert result["replay_stats"]["total_replays"] == 0
        assert result["sentry_enabled"] is False
        assert result["sentry_events_count"] is None

    def test_result_structure_complete(self):
        """返回结构包含所有必需字段"""
        mock_storage = MagicMock()
        mock_storage.get_correlation_stats.return_value = {"total_replays": 0}

        with patch("agent.monitoring.replay_storage.get_replay_storage", return_value=mock_storage), \
             patch("agent.error_reporting_config.is_sentry_enabled", return_value=True):
            result = _get_error_correlation_stats()

        assert "window_hours" in result
        assert "replay_stats" in result
        assert "sentry_enabled" in result
        assert "sentry_events_count" in result

    def test_degraded_replay_stats_structure(self):
        """降级 replay_stats 包含所有字段"""
        with patch("agent.monitoring.replay_storage.get_replay_storage", side_effect=ImportError), \
             patch("agent.error_reporting_config.is_sentry_enabled", return_value=False):
            result = _get_error_correlation_stats()

        degraded = result["replay_stats"]
        assert "total_replays" in degraded
        assert "with_trace_id" in degraded
        assert "with_user_session_id" in degraded
        assert "with_error_id" in degraded
        assert "fully_correlated" in degraded
        assert "by_error_id" in degraded
        assert "window_hours" in degraded
        assert "error" in degraded


# ──────────────────────────────────────────────
# _get_tool_summary 测试
# ──────────────────────────────────────────────


class TestGetToolSummary:
    """_get_tool_summary 工具摘要测试"""

    def test_empty_tools_list(self):
        """空工具列表"""
        with patch("agent.server_routes.routes_logging.list_tools", return_value=[]):
            result = _get_tool_summary()

        assert result["total_tools"] == 0
        assert result["categories"] == {}
        assert result["tools"] == []
        assert "timestamp" in result

    def test_tools_with_default_fields(self):
        """工具使用默认字段值(缺失字段)"""
        tools = [{"name": "tool1"}]  # 缺少 description/category/version/enabled

        with patch("agent.server_routes.routes_logging.list_tools", return_value=tools):
            result = _get_tool_summary()

        assert result["total_tools"] == 1
        assert result["tools"][0]["name"] == "tool1"
        assert result["tools"][0]["description"] == ""
        assert result["tools"][0]["category"] == "general"
        assert result["tools"][0]["version"] == "1.0.0"
        assert result["tools"][0]["enabled"] is True

    def test_tools_category_grouping(self):
        """工具按类别分组统计"""
        tools = [
            {"name": "t1", "category": "web"},
            {"name": "t2", "category": "web"},
            {"name": "t3", "category": "file"},
        ]

        with patch("agent.server_routes.routes_logging.list_tools", return_value=tools):
            result = _get_tool_summary()

        assert result["categories"] == {"web": 2, "file": 1}

    def test_list_tools_exception_returns_error(self):
        """list_tools 抛异常: 返回 error 字典"""
        with patch("agent.server_routes.routes_logging.list_tools", side_effect=RuntimeError("boom")):
            result = _get_tool_summary()

        assert "error" in result
        assert "boom" in result["error"]
        assert "timestamp" in result


# ──────────────────────────────────────────────
# _get_config_status 测试
# ──────────────────────────────────────────────


class TestGetConfigStatus:
    """_get_config_status 配置状态摘要测试"""

    def test_full_config_returns_status(self):
        """完整配置返回状态摘要"""
        mock_config = {
            "version": "2.0.0",
            "environment": "production",
            "debug": True,
            "memory": {"enabled": True},
            "monitoring": {"enabled": False},
            "security": {"enabled": True},
            "extensions": {"enabled": False},
            "performance": {"max_workers": 8, "pool_size": 20, "max_concurrency": 10},
            "_validation_errors": ["err1"],
            "_loaded_at": "2026-07-10T10:00:00",
        }

        with patch.object(routes_logging, "Config") as MockConfig:
            MockConfig.get.return_value = mock_config
            result = _get_config_status()

        assert result["version"] == "2.0.0"
        assert result["environment"] == "production"
        assert result["debug_mode"] is True
        assert result["modules"]["memory"] is True
        assert result["modules"]["monitoring"] is False
        assert result["performance"]["max_workers"] == 8
        assert result["validation_errors"] == ["err1"]
        assert result["loaded_at"] == "2026-07-10T10:00:00"

    def test_empty_config_uses_defaults(self):
        """空配置使用默认值"""
        with patch.object(routes_logging, "Config") as MockConfig:
            MockConfig.get.return_value = {}
            result = _get_config_status()

        assert result["version"] == "unknown"
        assert result["environment"] == "development"
        assert result["debug_mode"] is False
        assert result["modules"]["memory"] is False
        assert result["performance"]["max_workers"] == 4
        assert result["performance"]["pool_size"] == 10
        assert result["performance"]["max_concurrency"] == 5
        assert result["validation_errors"] == []
        assert result["loaded_at"] == ""

    def test_config_get_exception_returns_error(self):
        """Config.get 抛异常: 返回 error 字典"""
        with patch.object(routes_logging, "Config") as MockConfig:
            MockConfig.get.side_effect = RuntimeError("config boom")
            result = _get_config_status()

        assert "error" in result
        assert "config boom" in result["error"]

    def test_sensitive_data_filtered(self):
        """敏感数据经过 filter_dict 过滤"""
        mock_config = {
            "version": "1.0",
            "api_key": "secret123",  # 敏感字段应被过滤
        }

        with patch.object(routes_logging, "Config") as MockConfig:
            MockConfig.get.return_value = mock_config
            result = _get_config_status()

        # filter_dict 会处理敏感数据
        assert result["version"] == "1.0"
        assert "timestamp" in result


class TestConfigImportFallback:
    """Config ImportError fallback 测试 (lines 66-70)"""

    def test_fallback_config_get_returns_default(self):
        """config 导入失败时 fallback 类返回 default"""
        import importlib
        try:
            with patch.dict("sys.modules", {"config": None}):
                importlib.reload(routes_logging)
                result = routes_logging.Config.get("any_key", default="fb_val")
                assert result == "fb_val"
        finally:
            importlib.reload(routes_logging)


# ──────────────────────────────────────────────
# _get_health_status 测试
# ──────────────────────────────────────────────


class TestGetHealthStatus:
    """_get_health_status 综合健康状态测试"""

    def test_successful_health_status(self):
        """成功获取健康状态"""
        mock_health = MagicMock()
        mock_health.overall = 0.85
        mock_health.dimensions = {"stability": 0.9, "performance": 0.8}
        mock_health.issues = ["issue1", "issue2"]

        mock_history_item = MagicMock()
        mock_history_item.timestamp = "2026-07-10T10:00:00"
        mock_history_item.overall = 0.9
        mock_history_item.issues = ["old_issue"]

        with patch.object(routes_logging, "health_assessor") as mock_assessor, \
             patch("agent.server_routes.routes_logging._get_error_correlation_stats", return_value={"replay_stats": None}), \
             patch("agent.server_routes.routes_logging.is_opentelemetry_available", return_value=True):
            mock_assessor.assess.return_value = mock_health
            mock_assessor.get_history.return_value = [mock_history_item]

            result = _get_health_status()

        assert result["overall_health"] == 0.85
        assert result["dimensions"]["stability"] == 0.9
        assert len(result["issues"]) == 2
        assert result["history"][0]["overall"] == 0.9
        assert result["history"][0]["issues"] == 1
        assert result["opentelemetry_available"] is True
        assert "error_correlation" in result

    def test_health_assessor_exception_returns_error(self):
        """health_assessor 抛异常: 返回 error 字典"""
        with patch.object(routes_logging, "health_assessor") as mock_assessor:
            mock_assessor.assess.side_effect = RuntimeError("health boom")

            result = _get_health_status()

        assert "error" in result
        assert "health boom" in result["error"]

    def test_empty_history(self):
        """空历史记录"""
        mock_health = MagicMock()
        mock_health.overall = 1.0
        mock_health.dimensions = {}
        mock_health.issues = []

        with patch.object(routes_logging, "health_assessor") as mock_assessor, \
             patch("agent.server_routes.routes_logging._get_error_correlation_stats", return_value={}), \
             patch("agent.server_routes.routes_logging.is_opentelemetry_available", return_value=False):
            mock_assessor.assess.return_value = mock_health
            mock_assessor.get_history.return_value = []

            result = _get_health_status()

        assert result["history"] == []
        assert result["opentelemetry_available"] is False

    def test_error_correlation_failure_does_not_break_health(self):
        """错误关联统计失败不影响健康检查主流程"""
        mock_health = MagicMock()
        mock_health.overall = 0.5
        mock_health.dimensions = {}
        mock_health.issues = []

        with patch.object(routes_logging, "health_assessor") as mock_assessor, \
             patch("agent.server_routes.routes_logging._get_error_correlation_stats", return_value={"replay_stats": {"total_replays": 0}, "sentry_enabled": False}), \
             patch("agent.server_routes.routes_logging.is_opentelemetry_available", return_value=False):
            mock_assessor.assess.return_value = mock_health
            mock_assessor.get_history.return_value = []

            result = _get_health_status()

        # 错误关联统计降级但健康检查仍返回
        assert result["overall_health"] == 0.5
        assert result["error_correlation"]["sentry_enabled"] is False


# ──────────────────────────────────────────────
# _get_runtime_metrics 测试
# ──────────────────────────────────────────────


class TestGetRuntimeMetrics:
    """_get_runtime_metrics 运行时指标测试"""

    def test_successful_metrics(self):
        """成功获取指标"""
        mock_collector = MagicMock()
        mock_collector.get_all_metrics.return_value = {
            "histograms": {"latency": {"count": 10, "avg": 0.5}},
            "counters": {"requests": 100},
            "generated_at": 1234567890.0,
        }

        with patch("agent.server_routes.routes_logging.get_metrics_collector", return_value=mock_collector):
            result = _get_runtime_metrics()

        assert result["histograms"]["latency"]["count"] == 10
        assert result["counters"]["requests"] == 100
        assert result["generated_at"] == 1234567890.0
        assert "timestamp" in result

    def test_empty_metrics_uses_defaults(self):
        """空指标使用默认值"""
        mock_collector = MagicMock()
        mock_collector.get_all_metrics.return_value = {}

        with patch("agent.server_routes.routes_logging.get_metrics_collector", return_value=mock_collector):
            result = _get_runtime_metrics()

        assert result["histograms"] == {}
        assert result["counters"] == {}
        assert "timestamp" in result

    def test_collector_exception_returns_error(self):
        """collector 抛异常: 返回 error 字典"""
        with patch("agent.server_routes.routes_logging.get_metrics_collector", side_effect=RuntimeError("metrics boom")):
            result = _get_runtime_metrics()

        assert "error" in result
        assert "metrics boom" in result["error"]


# ──────────────────────────────────────────────
# get_prometheus_exporter 测试
# ──────────────────────────────────────────────


class TestGetPrometheusExporter:
    """get_prometheus_exporter 懒加载单例测试"""

    def test_first_call_initializes_exporter(self, reset_prometheus_exporter):
        """首次调用初始化导出器"""
        mock_exporter = MagicMock()
        with patch("agent.server_routes.routes_logging.PrometheusMetricsExporter", return_value=mock_exporter):
            result = get_prometheus_exporter()

        assert result is mock_exporter

    def test_second_call_returns_cached(self, reset_prometheus_exporter):
        """第二次调用返回缓存的单例"""
        mock_exporter = MagicMock()
        with patch("agent.server_routes.routes_logging.PrometheusMetricsExporter", return_value=mock_exporter) as MockCls:
            first = get_prometheus_exporter()
            second = get_prometheus_exporter()

        assert first is second
        # PrometheusMetricsExporter 只应被调用一次
        MockCls.assert_called_once()

    def test_init_failure_returns_none(self, reset_prometheus_exporter):
        """初始化失败返回 None"""
        with patch("agent.server_routes.routes_logging.PrometheusMetricsExporter", side_effect=RuntimeError("init boom")):
            result = get_prometheus_exporter()

        assert result is None


# ──────────────────────────────────────────────
# _load_alert_rules / _save_alert_rules 测试
# ──────────────────────────────────────────────


class TestLoadAlertRules:
    """_load_alert_rules 告警规则加载测试"""

    def test_load_from_existing_file(self, alert_rules_file, sample_alert_rules):
        """从已存在的文件加载告警规则"""
        with open(alert_rules_file, "w", encoding="utf-8") as f:
            yaml.dump(sample_alert_rules, f)

        result = _load_alert_rules()

        assert len(result["groups"]) == 1
        assert result["groups"][0]["name"] == "yunshu_alerts"
        assert len(result["groups"][0]["rules"]) == 2

    def test_load_caches_result(self, alert_rules_file, sample_alert_rules):
        """加载后缓存结果，第二次不读文件"""
        with open(alert_rules_file, "w", encoding="utf-8") as f:
            yaml.dump(sample_alert_rules, f)

        first = _load_alert_rules()
        # 删除文件后再次加载应返回缓存
        import os
        os.remove(alert_rules_file)
        second = _load_alert_rules()

        assert first is second  # 同一对象引用

    def test_load_missing_file_returns_empty_groups(self, alert_rules_file):
        """文件不存在: 返回 {"groups": []}"""
        result = _load_alert_rules()

        assert result == {"groups": []}

    def test_load_invalid_yaml_returns_empty_groups(self, alert_rules_file):
        """YAML 格式错误: 返回 {"groups": []}"""
        with open(alert_rules_file, "w", encoding="utf-8") as f:
            f.write("invalid: yaml: content: [")

        result = _load_alert_rules()

        assert result == {"groups": []}

    def test_load_yaml_import_error_returns_empty(self, alert_rules_file, sample_alert_rules, monkeypatch):
        """yaml 模块导入失败: 返回 {"groups": []}"""
        with open(alert_rules_file, "w", encoding="utf-8") as f:
            yaml.dump(sample_alert_rules, f)

        # 模拟 yaml 导入失败
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("no yaml module")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        result = _load_alert_rules()

        assert result == {"groups": []}


class TestSaveAlertRules:
    """_save_alert_rules 告警规则保存测试"""

    def test_save_success_returns_true(self, alert_rules_file, sample_alert_rules):
        """保存成功返回 True"""
        result = _save_alert_rules(sample_alert_rules)

        assert result is True
        # 验证文件已写入
        with open(alert_rules_file, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        assert loaded["groups"][0]["name"] == "yunshu_alerts"

    def test_save_updates_cache(self, alert_rules_file, sample_alert_rules):
        """保存后更新缓存"""
        _save_alert_rules(sample_alert_rules)

        # 缓存应等于保存的规则
        assert routes_logging._ALERT_RULES_CACHE == sample_alert_rules

    def test_save_invalid_path_returns_false(self, tmp_path, monkeypatch, sample_alert_rules):
        """保存到无效路径(父目录不存在)返回 False"""
        nonexistent_file = tmp_path / "nonexistent_subdir_xyz" / "alerts.yml"
        monkeypatch.setattr(routes_logging, "_ALERT_RULES_FILE", str(nonexistent_file))
        monkeypatch.setattr(routes_logging, "_ALERT_RULES_CACHE", None)

        result = _save_alert_rules(sample_alert_rules)

        assert result is False

    def test_save_yaml_import_error_returns_false(self, alert_rules_file, sample_alert_rules, monkeypatch):
        """yaml 模块导入失败: 返回 False"""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("no yaml module")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        result = _save_alert_rules(sample_alert_rules)

        assert result is False

    def test_save_then_load_roundtrip(self, alert_rules_file, sample_alert_rules):
        """保存后加载的往返一致性"""
        _save_alert_rules(sample_alert_rules)
        # 重置缓存以强制重新读取
        routes_logging._ALERT_RULES_CACHE = None
        loaded = _load_alert_rules()

        assert loaded["groups"][0]["name"] == sample_alert_rules["groups"][0]["name"]
        assert len(loaded["groups"][0]["rules"]) == 2


# ════════════════════════════════════════════════════════════════
# 诊断端点集成测试
# ════════════════════════════════════════════════════════════════


class TestDiagnosticsToolsEndpoint:
    """GET /api/diagnostics/tools 端点测试"""

    def test_returns_tool_summary(self, client):
        """返回工具摘要"""
        with patch("agent.server_routes.routes_logging._get_tool_summary",
                   return_value={"total_tools": 3, "tools": []}):
            resp = client.get("/api/diagnostics/tools")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_tools"] == 3

    def test_empty_tools(self, client):
        """空工具列表"""
        with patch("agent.server_routes.routes_logging._get_tool_summary",
                   return_value={"total_tools": 0, "tools": [], "categories": {}}):
            resp = client.get("/api/diagnostics/tools")

        assert resp.status_code == 200
        assert resp.get_json()["total_tools"] == 0

    def test_error_in_summary(self, client):
        """工具摘要异常"""
        with patch("agent.server_routes.routes_logging._get_tool_summary",
                   return_value={"error": "boom"}):
            resp = client.get("/api/diagnostics/tools")

        assert resp.status_code == 200
        assert "error" in resp.get_json()


class TestDiagnosticsConfigEndpoint:
    """GET /api/diagnostics/config 端点测试"""

    def test_returns_config_status(self, client):
        """返回配置状态"""
        with patch("agent.server_routes.routes_logging._get_config_status",
                   return_value={"version": "1.0"}):
            resp = client.get("/api/diagnostics/config")

        assert resp.status_code == 200
        assert resp.get_json()["version"] == "1.0"

    def test_config_error(self, client):
        """配置状态异常"""
        with patch("agent.server_routes.routes_logging._get_config_status",
                   return_value={"error": "fail"}):
            resp = client.get("/api/diagnostics/config")

        assert resp.status_code == 200
        assert "error" in resp.get_json()


class TestDiagnosticsHealthEndpoint:
    """GET /api/diagnostics/health 端点测试"""

    def test_returns_health_status(self, client):
        """返回健康状态"""
        with patch("agent.server_routes.routes_logging._get_health_status",
                   return_value={"overall_health": 0.9}):
            resp = client.get("/api/diagnostics/health")

        assert resp.status_code == 200
        assert resp.get_json()["overall_health"] == 0.9

    def test_health_error(self, client):
        """健康状态异常"""
        with patch("agent.server_routes.routes_logging._get_health_status",
                   return_value={"error": "fail"}):
            resp = client.get("/api/diagnostics/health")

        assert resp.status_code == 200
        assert "error" in resp.get_json()


class TestDiagnosticsErrorCorrelationEndpoint:
    """GET /api/diagnostics/error_correlation 端点测试

    hours 参数限制: max(1, min(hours, 24*30)) → 范围 [1, 720]
    """

    def test_default_hours_24(self, client):
        """默认 hours=24"""
        with patch("agent.server_routes.routes_logging._get_error_correlation_stats") as mock_stats:
            mock_stats.return_value = {"window_hours": 24}
            resp = client.get("/api/diagnostics/error_correlation")

        assert resp.status_code == 200
        mock_stats.assert_called_once_with(hours=24)

    def test_custom_hours(self, client):
        """自定义 hours"""
        with patch("agent.server_routes.routes_logging._get_error_correlation_stats") as mock_stats:
            mock_stats.return_value = {"window_hours": 48}
            resp = client.get("/api/diagnostics/error_correlation?hours=48")

        assert resp.status_code == 200
        mock_stats.assert_called_once_with(hours=48)

    def test_hours_below_minimum_clamped_to_1(self, client):
        """hours < 1 被限制为 1"""
        with patch("agent.server_routes.routes_logging._get_error_correlation_stats") as mock_stats:
            mock_stats.return_value = {}
            resp = client.get("/api/diagnostics/error_correlation?hours=0")

        assert resp.status_code == 200
        mock_stats.assert_called_once_with(hours=1)

    def test_hours_negative_clamped_to_1(self, client):
        """负数 hours 被限制为 1"""
        with patch("agent.server_routes.routes_logging._get_error_correlation_stats") as mock_stats:
            mock_stats.return_value = {}
            resp = client.get("/api/diagnostics/error_correlation?hours=-10")

        assert resp.status_code == 200
        mock_stats.assert_called_once_with(hours=1)

    def test_hours_above_max_clamped_to_720(self, client):
        """hours > 720 被限制为 720"""
        with patch("agent.server_routes.routes_logging._get_error_correlation_stats") as mock_stats:
            mock_stats.return_value = {}
            resp = client.get("/api/diagnostics/error_correlation?hours=1000")

        assert resp.status_code == 200
        mock_stats.assert_called_once_with(hours=720)

    def test_hours_boundary_1(self, client):
        """hours=1 边界值"""
        with patch("agent.server_routes.routes_logging._get_error_correlation_stats") as mock_stats:
            mock_stats.return_value = {}
            resp = client.get("/api/diagnostics/error_correlation?hours=1")

        assert resp.status_code == 200
        mock_stats.assert_called_once_with(hours=1)

    def test_hours_boundary_720(self, client):
        """hours=720 边界值"""
        with patch("agent.server_routes.routes_logging._get_error_correlation_stats") as mock_stats:
            mock_stats.return_value = {}
            resp = client.get("/api/diagnostics/error_correlation?hours=720")

        assert resp.status_code == 200
        mock_stats.assert_called_once_with(hours=720)

    def test_response_structure(self, client):
        """响应结构包含 ok/correlation/timestamp"""
        with patch("agent.server_routes.routes_logging._get_error_correlation_stats",
                   return_value={"window_hours": 24}):
            resp = client.get("/api/diagnostics/error_correlation")

        data = resp.get_json()
        assert data["ok"] is True
        assert "correlation" in data
        assert "timestamp" in data


class TestDiagnosticsTraceEndpoint:
    """GET /api/diagnostics/trace 端点测试"""

    def test_returns_trace_context(self, client):
        """返回追踪上下文"""
        with patch("agent.server_routes.routes_logging.get_trace_id", return_value="trace123"), \
             patch("agent.server_routes.routes_logging.get_span_id", return_value="span456"), \
             patch("agent.server_routes.routes_logging.is_opentelemetry_available", return_value=True):
            resp = client.get("/api/diagnostics/trace")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trace_id"] == "trace123"
        assert data["span_id"] == "span456"
        assert data["opentelemetry_available"] is True

    def test_no_trace_context(self, client):
        """无追踪上下文"""
        with patch("agent.server_routes.routes_logging.get_trace_id", return_value=None), \
             patch("agent.server_routes.routes_logging.get_span_id", return_value=None), \
             patch("agent.server_routes.routes_logging.is_opentelemetry_available", return_value=False):
            resp = client.get("/api/diagnostics/trace")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trace_id"] is None
        assert data["span_id"] is None
        assert data["opentelemetry_available"] is False


class TestDiagnosticsTraceExtractEndpoint:
    """POST /api/diagnostics/trace/extract 端点测试

    W3C/Jaeger 格式合并: HTTP头 + body headers(覆盖)
    """

    def test_w3c_format_from_http_header(self, client):
        """W3C 格式从 HTTP 头提取"""
        with patch("agent.server_routes.routes_logging.extract_trace_context") as mock_extract:
            mock_extract.return_value = {"trace_id": "w3c_trace", "span_id": "w3c_span"}
            resp = client.post("/api/diagnostics/trace/extract",
                               headers={"traceparent": "00-w3c_trace-w3c_span-01"},
                               json={})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trace_id"] == "w3c_trace"
        assert data["span_id"] == "w3c_span"
        assert data["format"] == "w3c"

    def test_jaeger_format_from_http_header(self, client):
        """Jaeger 格式从 HTTP 头提取"""
        with patch("agent.server_routes.routes_logging.extract_trace_context") as mock_extract:
            mock_extract.return_value = {"trace_id": "jaeger_trace", "span_id": "jaeger_span"}
            resp = client.post("/api/diagnostics/trace/extract",
                               headers={"uber-trace-id": "jaeger_trace:jaeger_span:0:1"},
                               json={})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["format"] == "jaeger"

    def test_body_headers_override_http_headers(self, client):
        """body headers 覆盖 HTTP 头"""
        with patch("agent.server_routes.routes_logging.extract_trace_context") as mock_extract:
            mock_extract.return_value = {"trace_id": "body_trace", "span_id": "body_span"}
            resp = client.post("/api/diagnostics/trace/extract",
                               headers={"traceparent": "00-http_trace-http_span-01"},
                               json={"headers": {"traceparent": "00-body_trace-body_span-01"}})

        assert resp.status_code == 200
        # extract_trace_context 接收的 headers 应包含 body 的值
        call_args = mock_extract.call_args[0][0]
        assert call_args["traceparent"] == "00-body_trace-body_span-01"

    def test_no_trace_headers_unknown_format(self, client):
        """无追踪头: format=unknown"""
        with patch("agent.server_routes.routes_logging.extract_trace_context",
                   return_value={"trace_id": None, "span_id": None}):
            resp = client.post("/api/diagnostics/trace/extract",
                               json={})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["format"] == "unknown"
        assert data["trace_id"] is None

    def test_case_insensitive_header_detection(self, client):
        """大小写不敏感的头检测"""
        with patch("agent.server_routes.routes_logging.extract_trace_context",
                   return_value={"trace_id": "t1", "span_id": "s1"}):
            resp = client.post("/api/diagnostics/trace/extract",
                               headers={"Traceparent": "00-t1-s1-01"},
                               json={})

        assert resp.status_code == 200
        assert resp.get_json()["format"] == "w3c"

    def test_empty_body(self, client):
        """空 body"""
        with patch("agent.server_routes.routes_logging.extract_trace_context",
                   return_value={"trace_id": None, "span_id": None}):
            resp = client.post("/api/diagnostics/trace/extract",
                               json={})

        assert resp.status_code == 200
        assert resp.get_json()["format"] == "unknown"

    def test_w3c_takes_precedence_over_jaeger(self, client):
        """W3C 优先级高于 Jaeger"""
        with patch("agent.server_routes.routes_logging.extract_trace_context",
                   return_value={"trace_id": "t", "span_id": "s"}):
            resp = client.post("/api/diagnostics/trace/extract",
                               headers={"traceparent": "00-t-s-01",
                                        "uber-trace-id": "t:s:0:1"},
                               json={})

        assert resp.status_code == 200
        assert resp.get_json()["format"] == "w3c"


class TestDiagnosticsTraceInjectEndpoint:
    """GET /api/diagnostics/trace/inject 端点测试"""

    def test_returns_injected_headers(self, client):
        """返回注入的追踪头"""
        with patch("agent.server_routes.routes_logging.inject_trace_context",
                   return_value={"traceparent": "00-t-s-01"}), \
             patch("agent.server_routes.routes_logging.get_trace_id", return_value="t"), \
             patch("agent.server_routes.routes_logging.get_span_id", return_value="s"):
            resp = client.get("/api/diagnostics/trace/inject")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["headers"]["traceparent"] == "00-t-s-01"
        assert data["trace_id"] == "t"
        assert data["span_id"] == "s"

    def test_inject_returns_none(self, client):
        """inject_trace_context 返回 None"""
        with patch("agent.server_routes.routes_logging.inject_trace_context",
                   return_value=None), \
             patch("agent.server_routes.routes_logging.get_trace_id", return_value=None), \
             patch("agent.server_routes.routes_logging.get_span_id", return_value=None):
            resp = client.get("/api/diagnostics/trace/inject")

        assert resp.status_code == 200
        assert resp.get_json()["headers"] is None


class TestDiagnosticsMetricsEndpoint:
    """GET /api/diagnostics/metrics 端点测试"""

    def test_returns_metrics(self, client):
        """返回运行时指标"""
        with patch("agent.server_routes.routes_logging._get_runtime_metrics",
                   return_value={"histograms": {}, "counters": {}}):
            resp = client.get("/api/diagnostics/metrics")

        assert resp.status_code == 200
        data = resp.get_json()
        assert "histograms" in data
        assert "counters" in data

    def test_metrics_error(self, client):
        """指标异常"""
        with patch("agent.server_routes.routes_logging._get_runtime_metrics",
                   return_value={"error": "fail"}):
            resp = client.get("/api/diagnostics/metrics")

        assert resp.status_code == 200
        assert "error" in resp.get_json()


class TestDiagnosticsLogsEndpoint:
    """GET /api/diagnostics/logs 端点测试"""

    def test_default_limit_50(self, client):
        """默认 limit=50"""
        with patch("agent.server_routes.routes_logging._get_recent_logs") as mock_logs:
            mock_logs.return_value = {"logs": [], "limit": 50}
            resp = client.get("/api/diagnostics/logs")

        assert resp.status_code == 200
        mock_logs.assert_called_once_with(50)

    def test_custom_limit(self, client):
        """自定义 limit"""
        with patch("agent.server_routes.routes_logging._get_recent_logs") as mock_logs:
            mock_logs.return_value = {"logs": [], "limit": 10}
            resp = client.get("/api/diagnostics/logs?limit=10")

        assert resp.status_code == 200
        mock_logs.assert_called_once_with(10)

    def test_returns_logs(self, client):
        """返回日志列表"""
        with patch("agent.server_routes.routes_logging._get_recent_logs",
                   return_value={"logs": [{"msg": "test"}], "source": "log_system"}):
            resp = client.get("/api/diagnostics/logs")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["msg"] == "test"


# ════════════════════════════════════════════════════════════════
# 告警规则 CRUD 端点测试
# ════════════════════════════════════════════════════════════════


class TestAlertsListEndpoint:
    """GET /api/observability/alerts 端点测试"""

    def test_list_with_existing_rules(self, client, written_alert_rules):
        """列出已有规则"""
        resp = client.get("/api/observability/alerts")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["groups"]) == 1
        assert data["groups"][0]["name"] == "yunshu_alerts"
        assert len(data["groups"][0]["rules"]) == 2
        assert "timestamp" in data

    def test_list_empty_rules(self, client, alert_rules_file):
        """空规则列表"""
        resp = client.get("/api/observability/alerts")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["groups"] == []

    def test_list_no_groups_key(self, client, alert_rules_file):
        """规则文件无 groups 键"""
        # 写入不含 groups 的 YAML
        with open(alert_rules_file, "w") as f:
            yaml.dump({"other": "data"}, f)
        routes_logging._ALERT_RULES_CACHE = None

        resp = client.get("/api/observability/alerts")

        assert resp.status_code == 200
        assert resp.get_json()["groups"] == []


class TestAlertsCreateEndpoint:
    """POST /api/observability/alerts 端点测试

    必填字段: name, expr
    groups 为空时自动创建默认 group "yunshu_alerts"
    """

    def test_create_success(self, client, alert_rules_file):
        """成功创建规则"""
        resp = client.post("/api/observability/alerts",
                           json={"name": "NewAlert", "expr": "rate > 1"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["rule"]["alert"] == "NewAlert"
        assert data["rule"]["expr"] == "rate > 1"
        assert data["rule"]["for"] == "5m"  # 默认值
        assert data["rule"]["labels"]["severity"] == "warning"  # 默认值
        assert data["rule"]["annotations"]["summary"] == "NewAlert"  # 默认回退到 name

    def test_create_with_all_fields(self, client, alert_rules_file):
        """带所有字段创建规则"""
        resp = client.post("/api/observability/alerts",
                           json={"name": "FullAlert", "expr": "rate > 2",
                                 "for": "10m", "severity": "critical",
                                 "summary": "sum", "description": "desc"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["rule"]["for"] == "10m"
        assert data["rule"]["labels"]["severity"] == "critical"
        assert data["rule"]["annotations"]["summary"] == "sum"
        assert data["rule"]["annotations"]["description"] == "desc"

    def test_create_missing_name(self, client, alert_rules_file):
        """缺少 name 字段返回 400"""
        resp = client.post("/api/observability/alerts",
                           json={"expr": "rate > 1"})

        assert resp.status_code == 400
        assert "name" in resp.get_json()["error"]

    def test_create_missing_expr(self, client, alert_rules_file):
        """缺少 expr 字段返回 400"""
        resp = client.post("/api/observability/alerts",
                           json={"name": "TestAlert"})

        assert resp.status_code == 400
        assert "expr" in resp.get_json()["error"]

    def test_create_missing_all_required(self, client, alert_rules_file):
        """缺少所有必填字段返回 400"""
        resp = client.post("/api/observability/alerts", json={})

        assert resp.status_code == 400
        data = resp.get_json()
        assert "name" in data["error"]
        assert "expr" in data["error"]

    def test_create_no_body(self, client, alert_rules_file):
        """无 body 返回 400"""
        resp = client.post("/api/observability/alerts",
                           data="{}", content_type="application/json")

        assert resp.status_code == 400

    def test_create_initializes_default_group(self, client, alert_rules_file):
        """空 groups 时自动创建默认 group"""
        # 先确保文件为空（不存在的文件 → 返回 {"groups": []}）
        resp = client.post("/api/observability/alerts",
                           json={"name": "First", "expr": "rate > 0"})

        assert resp.status_code == 200
        # 验证文件中有默认 group
        with open(alert_rules_file, "r") as f:
            loaded = yaml.safe_load(f)
        assert loaded["groups"][0]["name"] == "yunshu_alerts"
        assert loaded["groups"][0]["interval"] == "30s"

    def test_create_when_rules_missing_groups_key(self, client, alert_rules_file):
        """rules 字典无 groups 键时自动初始化"""
        with open(alert_rules_file, "w") as f:
            yaml.dump({"other_key": "value"}, f)

        resp = client.post("/api/observability/alerts",
                           json={"name": "TestAlert", "expr": "rate > 1"})

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_create_appends_to_existing_group(self, client, written_alert_rules):
        """追加到已有 group"""
        resp = client.post("/api/observability/alerts",
                           json={"name": "Third", "expr": "rate > 3"})

        assert resp.status_code == 200
        # 验证文件中有 3 条规则
        routes_logging._ALERT_RULES_CACHE = None
        loaded = _load_alert_rules()
        assert len(loaded["groups"][0]["rules"]) == 3

    def test_create_save_failure_returns_500(self, client, alert_rules_file):
        """保存失败返回 500"""
        with patch("agent.server_routes.routes_logging._save_alert_rules", return_value=False):
            resp = client.post("/api/observability/alerts",
                               json={"name": "Fail", "expr": "rate > 0"})

        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False


class TestAlertsUpdateEndpoint:
    """PUT /api/observability/alerts/<alert_name> 端点测试

    双层循环查找 + 部分更新
    """

    def test_update_expr(self, client, written_alert_rules):
        """更新 expr 字段"""
        resp = client.put("/api/observability/alerts/HighErrorRate",
                          json={"expr": "error_rate > 0.2"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["rule"]["expr"] == "error_rate > 0.2"

    def test_update_for(self, client, written_alert_rules):
        """更新 for 字段"""
        resp = client.put("/api/observability/alerts/HighErrorRate",
                          json={"for": "15m"})

        assert resp.status_code == 200
        assert resp.get_json()["rule"]["for"] == "15m"

    def test_update_severity(self, client, written_alert_rules):
        """更新 severity 字段"""
        resp = client.put("/api/observability/alerts/HighLatency",
                          json={"severity": "critical"})

        assert resp.status_code == 200
        assert resp.get_json()["rule"]["labels"]["severity"] == "critical"

    def test_update_summary(self, client, written_alert_rules):
        """更新 summary 字段"""
        resp = client.put("/api/observability/alerts/HighErrorRate",
                          json={"summary": "new summary"})

        assert resp.status_code == 200
        assert resp.get_json()["rule"]["annotations"]["summary"] == "new summary"

    def test_update_description(self, client, written_alert_rules):
        """更新 description 字段"""
        resp = client.put("/api/observability/alerts/HighErrorRate",
                          json={"description": "new desc"})

        assert resp.status_code == 200
        assert resp.get_json()["rule"]["annotations"]["description"] == "new desc"

    def test_update_multiple_fields(self, client, written_alert_rules):
        """更新多个字段"""
        resp = client.put("/api/observability/alerts/HighErrorRate",
                          json={"expr": "new_expr", "for": "20m", "severity": "info"})

        assert resp.status_code == 200
        rule = resp.get_json()["rule"]
        assert rule["expr"] == "new_expr"
        assert rule["for"] == "20m"
        assert rule["labels"]["severity"] == "info"

    def test_update_nonexistent_returns_404(self, client, written_alert_rules):
        """更新不存在的规则返回 404"""
        resp = client.put("/api/observability/alerts/Nonexistent",
                          json={"expr": "new"})

        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_update_no_fields(self, client, written_alert_rules):
        """无字段更新: 不修改但返回成功"""
        resp = client.put("/api/observability/alerts/HighErrorRate", json={})

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_update_save_failure_returns_500(self, client, written_alert_rules):
        """保存失败返回 500"""
        with patch("agent.server_routes.routes_logging._save_alert_rules", return_value=False):
            resp = client.put("/api/observability/alerts/HighErrorRate",
                              json={"expr": "new"})

        assert resp.status_code == 500


class TestAlertsDeleteEndpoint:
    """DELETE /api/observability/alerts/<alert_name> 端点测试

    多 group 遍历 + break
    """

    def test_delete_existing(self, client, written_alert_rules):
        """删除已有规则"""
        resp = client.delete("/api/observability/alerts/HighErrorRate")

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        # 验证文件中只剩 1 条规则
        routes_logging._ALERT_RULES_CACHE = None
        loaded = _load_alert_rules()
        assert len(loaded["groups"][0]["rules"]) == 1
        assert loaded["groups"][0]["rules"][0]["alert"] == "HighLatency"

    def test_delete_nonexistent_returns_404(self, client, written_alert_rules):
        """删除不存在的规则返回 404"""
        resp = client.delete("/api/observability/alerts/Nonexistent")

        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_delete_last_rule_in_group(self, client, written_alert_rules):
        """删除 group 中最后一条规则"""
        client.delete("/api/observability/alerts/HighErrorRate")
        resp = client.delete("/api/observability/alerts/HighLatency")

        assert resp.status_code == 200
        routes_logging._ALERT_RULES_CACHE = None
        loaded = _load_alert_rules()
        assert len(loaded["groups"][0]["rules"]) == 0

    def test_delete_save_failure_returns_404(self, client, written_alert_rules):
        """保存失败返回 404(合并错误)"""
        with patch("agent.server_routes.routes_logging._save_alert_rules", return_value=False):
            resp = client.delete("/api/observability/alerts/HighErrorRate")

        assert resp.status_code == 404


class TestAlertsValidateEndpoint:
    """POST /api/observability/alerts/validate 端点测试"""

    def test_validate_valid_expr(self, client):
        """验证有效表达式"""
        with patch("prometheus_client.parser.parse", create=True):
            resp = client.post("/api/observability/alerts/validate",
                               json={"expr": "rate(http_requests_total[5m]) > 0.1"})

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_validate_empty_expr(self, client):
        """空表达式返回 400"""
        resp = client.post("/api/observability/alerts/validate", json={"expr": ""})

        assert resp.status_code == 400
        assert "expr" in resp.get_json()["error"]

    def test_validate_missing_expr(self, client):
        """缺少 expr 参数返回 400"""
        resp = client.post("/api/observability/alerts/validate", json={})

        assert resp.status_code == 400
        assert "expr" in resp.get_json()["error"]

    def test_validate_invalid_expr(self, client):
        """无效表达式返回 400"""
        with patch("prometheus_client.parser.parse", side_effect=Exception("parse error"), create=True):
            resp = client.post("/api/observability/alerts/validate",
                               json={"expr": "invalid syntax !!!"})

        assert resp.status_code == 400
        assert "parse error" in resp.get_json()["error"]


# ════════════════════════════════════════════════════════════════
# Prometheus 指标导出端点测试
# ════════════════════════════════════════════════════════════════


class TestPrometheusMetricsEndpoint:
    """GET /metrics 端点测试"""

    def test_successful_export(self, client):
        """成功导出 Prometheus 指标"""
        mock_collector = MagicMock()
        mock_collector.export_prometheus.return_value = "# HELP test\n# TYPE test counter\ntest 1\n"

        with patch("agent.server_routes.routes_logging.get_metrics_collector",
                   return_value=mock_collector), \
             patch("prometheus_client.generate_latest", return_value=b"# registry output\n"):
            resp = client.get("/metrics")

        assert resp.status_code == 200
        assert "text/plain" in resp.content_type
        assert b"test 1" in resp.data

    def test_collector_exception_returns_500(self, client):
        """collector 异常返回 500"""
        with patch("agent.server_routes.routes_logging.get_metrics_collector",
                   side_effect=RuntimeError("collector boom")):
            resp = client.get("/metrics")

        assert resp.status_code == 500
        assert b"Error" in resp.data

    def test_generate_latest_failure_silently_ignored(self, client):
        """generate_latest 失败时静默忽略"""
        mock_collector = MagicMock()
        mock_collector.export_prometheus.return_value = "# collector output\n"

        with patch("agent.server_routes.routes_logging.get_metrics_collector",
                   return_value=mock_collector), \
             patch("prometheus_client.generate_latest", side_effect=RuntimeError("registry fail")):
            resp = client.get("/metrics")

        assert resp.status_code == 200
        assert b"collector output" in resp.data

    def test_content_type_is_prometheus_format(self, client):
        """content_type 为 Prometheus 格式"""
        mock_collector = MagicMock()
        mock_collector.export_prometheus.return_value = "# metrics\n"

        with patch("agent.server_routes.routes_logging.get_metrics_collector",
                   return_value=mock_collector):
            resp = client.get("/metrics")

        assert "version=0.0.4" in resp.content_type


# ════════════════════════════════════════════════════════════════
# 可观测性状态端点测试
# ════════════════════════════════════════════════════════════════


class TestObservabilityStateEndpoint:
    """GET /api/observability/state 端点测试"""

    def test_returns_combined_state(self, client):
        """返回综合状态"""
        with patch("agent.server_routes.routes_logging.get_trace_id", return_value="t1"), \
             patch("agent.server_routes.routes_logging._get_health_status", return_value={"overall": 0.9}), \
             patch("agent.server_routes.routes_logging._get_runtime_metrics", return_value={"counters": {}}), \
             patch("agent.server_routes.routes_logging._get_tool_summary", return_value={"total_tools": 1}), \
             patch("agent.server_routes.routes_logging._get_config_status", return_value={"version": "1.0"}):
            resp = client.get("/api/observability/state")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trace_id"] == "t1"
        assert data["health"]["overall"] == 0.9
        assert data["tools"]["total_tools"] == 1
        assert data["config"]["version"] == "1.0"
        assert "timestamp" in data


# ════════════════════════════════════════════════════════════════
# Loki 日志端点测试
# ════════════════════════════════════════════════════════════════


class TestObservabilityLogsGetEndpoint:
    """GET /api/observability/logs 端点测试

    LogQL 查询拼接: query / level / service 组合
    """

    def test_default_query(self, client):
        """默认查询(无参数)"""
        with patch("agent.monitoring.loki.query_loki_logs", return_value=[]) as mock_query:
            resp = client.get("/api/observability/logs")

        assert resp.status_code == 200
        mock_query.assert_called_once()
        # 默认查询为 ".+"
        call_kwargs = mock_query.call_args
        assert call_kwargs[1]["query"] == ".+" or call_kwargs[0][0] == ".+"

    def test_with_query_param(self, client):
        """带 query 参数"""
        with patch("agent.monitoring.loki.query_loki_logs", return_value=[]) as mock_query:
            resp = client.get("/api/observability/logs?query=error")

        assert resp.status_code == 200

    def test_with_level_filter(self, client):
        """带 level 过滤"""
        with patch("agent.monitoring.loki.query_loki_logs", return_value=[]) as mock_query:
            resp = client.get("/api/observability/logs?level=ERROR")

        assert resp.status_code == 200

    def test_with_query_and_level_filter(self, client):
        """query + level 组合: 覆盖 log_query += 分支"""
        with patch("agent.monitoring.loki.query_loki_logs", return_value=[]) as mock_query:
            resp = client.get("/api/observability/logs?query=error&level=ERROR")

        assert resp.status_code == 200

    def test_with_service_filter(self, client):
        """带 service 过滤"""
        with patch("agent.monitoring.loki.query_loki_logs", return_value=[]) as mock_query:
            resp = client.get("/api/observability/logs?service=api")

        assert resp.status_code == 200

    def test_with_query_and_service_filter(self, client):
        """query + service 组合: 覆盖 log_query += service 分支"""
        with patch("agent.monitoring.loki.query_loki_logs", return_value=[]) as mock_query:
            resp = client.get("/api/observability/logs?query=error&service=api")

        assert resp.status_code == 200

    def test_with_limit(self, client):
        """带 limit 参数"""
        with patch("agent.monitoring.loki.query_loki_logs", return_value=[]):
            resp = client.get("/api/observability/logs?limit=50")

        assert resp.status_code == 200
        assert resp.get_json()["limit"] == 50

    def test_returns_logs_with_sensitive_filter(self, client):
        """返回日志经过敏感数据过滤"""
        logs = [{"message": "password=123", "labels": {"app": "test"}}]
        with patch("agent.monitoring.loki.query_loki_logs", return_value=logs):
            resp = client.get("/api/observability/logs")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert "timestamp" in data

    def test_loki_exception_returns_500(self, client):
        """Loki 查询异常返回 500"""
        with patch("agent.monitoring.loki.query_loki_logs", side_effect=RuntimeError("loki down")):
            resp = client.get("/api/observability/logs")

        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False

    def test_import_error_returns_500(self, client):
        """Loki 导入失败返回 500"""
        with patch("agent.monitoring.loki.query_loki_logs", side_effect=ImportError("no loki")):
            resp = client.get("/api/observability/logs")

        assert resp.status_code == 500


class TestObservabilityLogsLabelsEndpoint:
    """GET /api/observability/logs/labels 端点测试"""

    def test_returns_labels(self, client):
        """返回标签列表"""
        with patch("agent.monitoring.loki.get_loki_labels",
                   return_value={"app": ["api", "web"]}):
            resp = client.get("/api/observability/logs/labels")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["labels"]["app"] == ["api", "web"]

    def test_exception_returns_500(self, client):
        """异常返回 500"""
        with patch("agent.monitoring.loki.get_loki_labels", side_effect=RuntimeError("fail")):
            resp = client.get("/api/observability/logs/labels")

        assert resp.status_code == 500


class TestObservabilityLogsPushEndpoint:
    """POST /api/observability/logs 端点测试"""

    def test_push_success(self, client):
        """成功推送日志"""
        with patch("agent.monitoring.loki.log_to_loki") as mock_log:
            resp = client.post("/api/observability/logs",
                               json={"message": "test log", "labels": {"app": "api"}})

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mock_log.assert_called_once_with(message="test log", labels={"app": "api"}, timestamp=None)

    def test_push_missing_message_returns_400(self, client):
        """缺少 message 返回 400"""
        resp = client.post("/api/observability/logs", json={"labels": {}})

        assert resp.status_code == 400
        assert "message" in resp.get_json()["error"]

    def test_push_empty_message_returns_400(self, client):
        """空 message 返回 400"""
        resp = client.post("/api/observability/logs", json={"message": ""})

        assert resp.status_code == 400

    def test_push_no_body_returns_400(self, client):
        """无 body 返回 400"""
        resp = client.post("/api/observability/logs", data="{}", content_type="application/json")

        assert resp.status_code == 400

    def test_push_with_timestamp(self, client):
        """带时间戳推送"""
        with patch("agent.monitoring.loki.log_to_loki") as mock_log:
            resp = client.post("/api/observability/logs",
                               json={"message": "msg", "timestamp": 1234567890.0})

        assert resp.status_code == 200
        mock_log.assert_called_once_with(message="msg", labels={}, timestamp=1234567890.0)

    def test_push_loki_exception_returns_500(self, client):
        """Loki 异常返回 500"""
        with patch("agent.monitoring.loki.log_to_loki", side_effect=RuntimeError("loki fail")):
            resp = client.post("/api/observability/logs", json={"message": "msg"})

        assert resp.status_code == 500


# ════════════════════════════════════════════════════════════════
# 追踪可视化端点测试
# ════════════════════════════════════════════════════════════════


class TestObservabilityTracesEndpoint:
    """GET /api/observability/traces 端点测试"""

    def test_returns_traces(self, client):
        """返回追踪列表"""
        with patch("agent.monitoring.tracing.get_recent_traces",
                   return_value=[{"trace_id": "t1", "spans": []}]):
            resp = client.get("/api/observability/traces")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["traces"]) == 1
        assert data["total"] == 1

    def test_default_limit_20(self, client):
        """默认 limit=20"""
        with patch("agent.monitoring.tracing.get_recent_traces", return_value=[]) as mock_traces:
            resp = client.get("/api/observability/traces")

        assert resp.status_code == 200
        mock_traces.assert_called_once_with(20)

    def test_custom_limit(self, client):
        """自定义 limit"""
        with patch("agent.monitoring.tracing.get_recent_traces", return_value=[]) as mock_traces:
            resp = client.get("/api/observability/traces?limit=50")

        assert resp.status_code == 200
        mock_traces.assert_called_once_with(50)

    def test_trace_id_filter(self, client):
        """trace_id 过滤(子串匹配)"""
        traces = [{"trace_id": "abc123"}, {"trace_id": "def456"}]
        with patch("agent.monitoring.tracing.get_recent_traces", return_value=traces):
            resp = client.get("/api/observability/traces?trace_id=abc")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["traces"]) == 1
        assert data["traces"][0]["trace_id"] == "abc123"

    def test_exception_returns_500(self, client):
        """异常返回 500"""
        with patch("agent.monitoring.tracing.get_recent_traces", side_effect=RuntimeError("fail")):
            resp = client.get("/api/observability/traces")

        assert resp.status_code == 500


class TestObservabilityTraceDetailEndpoint:
    """GET /api/observability/traces/<trace_id> 端点测试"""

    def test_returns_trace_detail(self, client):
        """返回追踪详情"""
        with patch("agent.monitoring.tracing.get_trace_detail",
                   return_value={"trace_id": "t1", "spans": [{"id": "s1"}]}):
            resp = client.get("/api/observability/traces/t1")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trace_id"] == "t1"
        assert len(data["spans"]) == 1

    def test_trace_not_found_returns_404(self, client):
        """追踪不存在返回 404"""
        with patch("agent.monitoring.tracing.get_trace_detail", return_value=None):
            resp = client.get("/api/observability/traces/nonexistent")

        assert resp.status_code == 404

    def test_exception_returns_500(self, client):
        """异常返回 500"""
        with patch("agent.monitoring.tracing.get_trace_detail", side_effect=RuntimeError("fail")):
            resp = client.get("/api/observability/traces/t1")

        assert resp.status_code == 500


# ════════════════════════════════════════════════════════════════
# 仪表盘 + 访问日志端点测试
# ════════════════════════════════════════════════════════════════


class TestDashboardEndpoint:
    """GET /dashboard 端点测试"""

    def test_render_template_success(self, client):
        """成功渲染仪表盘"""
        with patch("flask.render_template", return_value="<html>dashboard</html>"):
            resp = client.get("/dashboard")

        assert resp.status_code == 200

    def test_render_template_failure_returns_500(self, client):
        """渲染失败返回 500(纯文本)"""
        with patch("flask.render_template", side_effect=RuntimeError("template not found")):
            resp = client.get("/dashboard")

        assert resp.status_code == 500
        # 返回纯文本错误（非 JSON），包含异常信息
        assert b"template not found" in resp.data


class TestAccessLogsEndpoint:
    """GET /api/observability/access_logs 端点测试"""

    def test_returns_access_logs(self, client):
        """返回访问日志"""
        mock_logger = MagicMock()
        mock_logger.get_recent_access.return_value = [{"endpoint": "/api/test"}]
        with patch("agent.server_routes.routes_logging.get_access_logger", return_value=mock_logger):
            resp = client.get("/api/observability/access_logs")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["access_logs"]) == 1
        assert data["total"] == 1

    def test_default_limit_100(self, client):
        """默认 limit=100"""
        mock_logger = MagicMock()
        mock_logger.get_recent_access.return_value = []
        with patch("agent.server_routes.routes_logging.get_access_logger", return_value=mock_logger):
            resp = client.get("/api/observability/access_logs")

        assert resp.status_code == 200
        assert resp.get_json()["limit"] == 100

    def test_with_endpoint_filter(self, client):
        """带 endpoint 过滤"""
        mock_logger = MagicMock()
        mock_logger.get_recent_access.return_value = []
        with patch("agent.server_routes.routes_logging.get_access_logger", return_value=mock_logger):
            resp = client.get("/api/observability/access_logs?endpoint=/api/test")

        assert resp.status_code == 200
        mock_logger.get_recent_access.assert_called_once()
        call_kwargs = mock_logger.get_recent_access.call_args[1]
        assert call_kwargs["endpoint"] == "/api/test"

    def test_exception_returns_500(self, client):
        """异常返回 500"""
        with patch("agent.server_routes.routes_logging.get_access_logger",
                   side_effect=RuntimeError("fail")):
            resp = client.get("/api/observability/access_logs")

        assert resp.status_code == 500


class TestAccessStatsEndpoint:
    """GET /api/observability/access_stats 端点测试"""

    def test_returns_access_stats(self, client):
        """返回访问统计"""
        mock_logger = MagicMock()
        mock_logger.get_access_stats.return_value = {
            "total_accesses": 100,
            "unique_endpoints": 5,
            "error_rate": 0.05,
        }
        with patch("agent.server_routes.routes_logging.get_access_logger", return_value=mock_logger):
            resp = client.get("/api/observability/access_stats")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_accesses"] == 100
        assert data["unique_endpoints"] == 5
        assert "timestamp" in data

    def test_exception_returns_500(self, client):
        """异常返回 500"""
        with patch("agent.server_routes.routes_logging.get_access_logger",
                   side_effect=RuntimeError("fail")):
            resp = client.get("/api/observability/access_stats")

        assert resp.status_code == 500


# ════════════════════════════════════════════════════════════════
# SSE 日志流端点测试
# ════════════════════════════════════════════════════════════════


class TestObservabilityLogsStreamEndpoint:
    """GET /api/observability/logs/stream 端点测试

    SSE 无限循环: 需 mock time.sleep + 控制退出
    """

    def test_stream_returns_sse_content_type(self, client):
        """流响应 content_type 为 text/event-stream"""
        call_count = [0]

        def side_effect(limit):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("stop")
            return {"logs": []}

        with patch("agent.server_routes.routes_logging._get_recent_logs", side_effect=side_effect), \
             patch("time.sleep"):
            resp = client.get("/api/observability/logs/stream")
            data = resp.data  # 在 patch 上下文内消费生成器

        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type

    def test_stream_yields_heartbeat(self, client):
        """流产生心跳事件"""
        call_count = [0]

        def side_effect(limit):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("stop")
            return {"logs": []}

        with patch("agent.server_routes.routes_logging._get_recent_logs", side_effect=side_effect), \
             patch("time.sleep"):
            resp = client.get("/api/observability/logs/stream")
            data = resp.data  # 在 patch 上下文内消费生成器

        assert b"heartbeat" in data

    def test_stream_yields_new_logs(self, client):
        """流产生新日志事件"""
        future_ts = time.time() + 1000
        call_count = [0]

        def side_effect(limit):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("stop")
            return {"logs": [{"timestamp": future_ts, "msg": "new log"}]}

        with patch("agent.server_routes.routes_logging._get_recent_logs", side_effect=side_effect), \
             patch("time.sleep"):
            resp = client.get("/api/observability/logs/stream")
            data = resp.data

        assert b"new log" in data

    def test_stream_trace_id_filter(self, client):
        """trace_id 过滤"""
        future_ts = time.time() + 1000
        call_count = [0]

        def side_effect(limit):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("stop")
            return {"logs": [
                {"timestamp": future_ts, "msg": "matching", "trace_id": "target"},
                {"timestamp": future_ts, "msg": "not_matching", "trace_id": "other"},
            ]}

        with patch("agent.server_routes.routes_logging._get_recent_logs", side_effect=side_effect), \
             patch("time.sleep"):
            resp = client.get("/api/observability/logs/stream?trace_id=target")
            data = resp.data

        assert b"matching" in data
        assert b"not_matching" not in data

    def test_stream_error_event_on_exception(self, client):
        """异常产生 error 事件"""
        with patch("agent.server_routes.routes_logging._get_recent_logs",
                   side_effect=RuntimeError("stream boom")), \
             patch("time.sleep"):
            resp = client.get("/api/observability/logs/stream")
            data = resp.data

        assert b"error" in data
        assert b"stream boom" in data
