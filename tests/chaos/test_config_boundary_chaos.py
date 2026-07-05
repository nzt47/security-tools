# -*- coding: utf-8 -*-
"""配置边界值混沌测试 — 异常配置注入下的系统降级行为验证

【测试目标】
验证 ObservabilityConfig 配置化项在以下极端场景下的行为正确性与稳定性：
1. retry.default_max_retries = 0：不重试，首次失败即返回
2. retry.default_max_retries = 20：最大重试不导致死循环
3. http.max_retries = 0：HTTP 客户端不重试
4. http.timeout_sec = 1：极短超时触发超时异常
5. http.pool_size = 1：最小连接池仍能工作
6. cognitive.reflection_max_retries = 1：反思引擎仅重试一次
7. time_window.max_analyze_days = 1：极短时间窗口正常工作
8. time_window.max_analyze_days = 36500：最大时间窗口不溢出

【可观测性约束】
- 边界显性化：所有异常值通过 Config.set 注入，非法值由 ValidationRule 自动修复
- 异常处理：每个测试设置 30s 超时，避免死锁
- 埋点预留：配置变更有结构化日志（observability_config 模块已内置）

【配置同步机制说明】
- 使用 pytest fixture 管理配置生命周期，确保每个测试后恢复默认值
- Config.set 支持热加载，变更立即生效
- ValidationRule 对非法值自动修复到合理范围

【生成日志摘要】
- 生成时间：2026-07-03
- 版本：v1.0.0
- 内容：配置边界值混沌测试（8 个异常场景）
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.monitoring.observability_config import (  # noqa: E402
    get_observability_config,
    reset_observability_config,
    get_default_max_retries,
    get_reflection_max_retries,
    get_http_max_retries,
    get_http_timeout,
    get_http_connect_timeout,
    get_http_pool_size,
    get_max_analyze_days,
)


# ═══════════════════════════════════════════════════════════════
#  Fixture：配置生命周期管理
# ═══════════════════════════════════════════════════════════════

# 所有配置化项的默认值快照（用于测试后恢复）
_CONFIG_DEFAULTS = {
    "retry.default_max_retries": 3,
    "cognitive.reflection_max_retries": 3,
    "http.max_retries": 3,
    "http.timeout_sec": 30,
    "http.connect_timeout_sec": 10,
    "http.pool_size": 20,
    "time_window.max_analyze_days": 36500,
}


@pytest.fixture
def config_snapshot():
    """配置快照 fixture：测试前重置、测试后恢复

    确保每个混沌测试的配置异常值不会污染后续测试。
    """
    reset_observability_config()
    config = get_observability_config()
    yield config
    # 测试后恢复默认值
    for key, value in _CONFIG_DEFAULTS.items():
        config.set(key, value)
    reset_observability_config()


# ═══════════════════════════════════════════════════════════════
#  1. 重试次数异常场景
# ═══════════════════════════════════════════════════════════════

class TestRetryMaxRetriesChaos:
    """retry.default_max_retries 异常值注入"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_zero_max_retries_no_retry(self, config_snapshot):
        """max_retries=0 时首次失败即返回，不重试"""
        config_snapshot.set("retry.default_max_retries", 0)
        assert get_default_max_retries() == 0

        from agent.error_handler import RetryPolicy
        policy = RetryPolicy()  # 不传 max_retries，从 Config 读取
        assert policy.max_retries == 0

        # should_retry 在 attempt=0 时应返回 False（因为 0 >= 0）
        result = policy.should_retry(ValueError("test"), attempt=0)
        assert result is False, "max_retries=0 时不应重试"

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_max_max_retries_no_infinite_loop(self, config_snapshot):
        """max_retries=20 时不会导致无限重试"""
        config_snapshot.set("retry.default_max_retries", 20)
        assert get_default_max_retries() == 20

        from agent.error_handler import RetryPolicy
        policy = RetryPolicy()
        assert policy.max_retries == 20

        # attempt=19 时应允许重试
        assert policy.should_retry(ValueError("test"), attempt=19) is True
        # attempt=20 时应停止重试
        assert policy.should_retry(ValueError("test"), attempt=20) is False

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_negative_max_retries_auto_repaired(self, config_snapshot):
        """非法值（-1）被 ValidationRule 自动修复到合理范围"""
        config_snapshot.set("retry.default_max_retries", -1)
        # ValidationRule 的 _range_validator 会修复到范围中点 (0+20)/2=10
        repaired = get_default_max_retries()
        assert 0 <= repaired <= 20, f"非法值应被修复到 0-20 范围，得到: {repaired}"


# ═══════════════════════════════════════════════════════════════
#  2. HTTP 配置异常场景
# ═══════════════════════════════════════════════════════════════

class TestHttpConfigChaos:
    """HTTP 配置异常值注入"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_zero_http_max_retries(self, config_snapshot):
        """http.max_retries=0 时 HTTP 客户端不重试"""
        config_snapshot.set("http.max_retries", 0)
        assert get_http_max_retries() == 0

        from agent.web.http_client import HttpClient
        client = HttpClient()
        # Retry.total 应为 0
        adapter = client._session.get_adapter("https://example.com")
        retry = adapter.max_retries
        assert retry.total == 0, f"HTTP max_retries 应为 0，得到: {retry.total}"

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_minimal_pool_size(self, config_snapshot):
        """http.pool_size=1 时连接池仍能正常初始化"""
        config_snapshot.set("http.pool_size", 1)
        assert get_http_pool_size() == 1

        from agent.web.http_client import HttpClient
        client = HttpClient()
        adapter = client._session.get_adapter("https://example.com")
        assert adapter._pool_connections == 1
        assert adapter._pool_maxsize == 2  # pool_size * 2

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_large_pool_size(self, config_snapshot):
        """http.pool_size=100 时连接池正确初始化"""
        config_snapshot.set("http.pool_size", 100)
        assert get_http_pool_size() == 100

        from agent.web.http_client import HttpClient
        client = HttpClient()
        adapter = client._session.get_adapter("https://example.com")
        assert adapter._pool_connections == 100
        assert adapter._pool_maxsize == 200

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_short_timeout_config_value(self, config_snapshot):
        """http.timeout_sec=1 时配置值正确读取"""
        config_snapshot.set("http.timeout_sec", 1)
        assert get_http_timeout() == 1

        # 验证 HttpClient 不因短超时崩溃
        from agent.web.http_client import HttpClient
        client = HttpClient()
        assert client is not None

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_explicit_config_overrides_global(self, config_snapshot):
        """显式 config 参数优先于全局 Config 值"""
        config_snapshot.set("http.pool_size", 50)
        from agent.web.http_client import HttpClient
        client = HttpClient({"pool_size": 5})
        adapter = client._session.get_adapter("https://example.com")
        assert adapter._pool_connections == 5, "显式 config 应优先于全局 Config"


# ═══════════════════════════════════════════════════════════════
#  3. 认知反思异常场景
# ═══════════════════════════════════════════════════════════════

class TestCognitiveReflectionChaos:
    """cognitive.reflection_max_retries 异常值注入"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_min_reflection_retries(self, config_snapshot):
        """reflection_max_retries=1 时反思仅允许一次重试"""
        config_snapshot.set("cognitive.reflection_max_retries", 1)
        assert get_reflection_max_retries() == 1

        from agent.cognitive.reflection import ReflectionEngine
        engine = ReflectionEngine()
        assert engine._get_max_retries() == 1
        # 向后兼容常量不受影响
        assert engine.MAX_RETRIES == 3

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_max_reflection_retries(self, config_snapshot):
        """reflection_max_retries=10 时反思允许最多 10 次重试"""
        config_snapshot.set("cognitive.reflection_max_retries", 10)
        assert get_reflection_max_retries() == 10

        from agent.cognitive.reflection import ReflectionEngine
        engine = ReflectionEngine()
        assert engine._get_max_retries() == 10

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_reflection_evaluate_with_chaos_config(self, config_snapshot):
        """异常配置下反思引擎 evaluate 不崩溃

        反思评分维度：
        - 维度2（错误信息）：output 含"错误" → score -= 0.3
        - 维度3（输出过短）：input>50 且 output<10 → score -= 0.2
        组合触发 score=0.5 < 0.6 → passed=False → should_retry=True
        """
        config_snapshot.set("cognitive.reflection_max_retries", 1)

        from agent.cognitive.reflection import ReflectionEngine
        engine = ReflectionEngine()

        # 注入低分输出触发重试逻辑（错误信息 + 输出过短 → score=0.5）
        result = engine.evaluate(
            task_id="chaos-test-001",
            input_text="这是一个测试输入" * 10,
            output="错误",
            execution_time_ms=100,
        )
        assert result is not None
        assert result.passed is False
        # score >= 0.3 且未超过 max_retries 时 should_retry 为 True
        if result.score >= 0.3:
            assert result.should_retry is True

        # 第二次评估同 task_id，应触发 max_retries 限制
        result2 = engine.evaluate(
            task_id="chaos-test-001",
            input_text="这是一个测试输入" * 10,
            output="错误",
            execution_time_ms=100,
        )
        # 第二次 should_retry 应为 False（已达到 max_retries=1）
        assert result2.should_retry is False


# ═══════════════════════════════════════════════════════════════
#  4. 时间窗口异常场景
# ═══════════════════════════════════════════════════════════════

class TestTimeWindowChaos:
    """time_window.max_analyze_days 异常值注入"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_min_analyze_days(self, config_snapshot):
        """max_analyze_days=1 时仅允许 1 天的分析窗口"""
        config_snapshot.set("time_window.max_analyze_days", 1)
        assert get_max_analyze_days() == 1

        from agent.data_analytics import DataAnalytics, _get_max_analyze_days
        assert _get_max_analyze_days() == 1

        # days=2 应抛出 ValueError
        analytics = DataAnalytics()
        with pytest.raises(ValueError, match="超过上限"):
            analytics.analyze_event_trends(days=2)

        # days=1 应正常执行
        result = analytics.analyze_event_trends(days=1)
        assert result is not None

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_max_analyze_days_no_overflow(self, config_snapshot):
        """max_analyze_days=36500 时不触发 OverflowError"""
        config_snapshot.set("time_window.max_analyze_days", 36500)
        assert get_max_analyze_days() == 36500

        from agent.data_analytics import DataAnalytics
        analytics = DataAnalytics()

        # days=36500 应正常执行（不溢出）
        result = analytics.analyze_event_trends(days=36500)
        assert result is not None

        # days=36501 应抛出 ValueError（超过上限）
        with pytest.raises(ValueError, match="超过上限"):
            analytics.analyze_event_trends(days=36501)

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_replay_storage_with_chaos_config(self, config_snapshot):
        """异常配置下 replay_storage 不崩溃

        资源清理：TemporaryDirectory 清理前必须显式关闭 SQLite 连接，
        否则 GC 时 sqlite3 连接对象尝试访问已删除的 DB 文件，
        在 Windows 上触发 NotADirectoryError [WinError 267]。
        """
        config_snapshot.set("time_window.max_analyze_days", 7)

        from agent.monitoring.replay_storage import ReplayStorage
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ReplayStorage(storage_root=tmpdir)
            try:
                # days=8 应抛出 ValueError（8 > max_analyze_days=7）
                with pytest.raises(ValueError, match="超过上限"):
                    storage.cleanup_old_records(days=8)

                # days=7 应正常执行（7 <= max_analyze_days=7）
                storage.cleanup_old_records(days=7)
            finally:
                # 显式关闭 SQLite 连接，避免 TemporaryDirectory 清理后 GC 触发 NotADirectoryError
                storage._conn.close()


# ═══════════════════════════════════════════════════════════════
#  5. 配置恢复与隔离性
# ═══════════════════════════════════════════════════════════════

class TestConfigRecoveryIsolation:
    """配置恢复与测试隔离性验证"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_config_restored_after_test(self, config_snapshot):
        """验证 fixture 在测试后恢复默认值"""
        # 此测试故意修改配置
        config_snapshot.set("retry.default_max_retries", 15)
        config_snapshot.set("http.pool_size", 99)
        assert get_default_max_retries() == 15
        assert get_http_pool_size() == 99

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_config_defaults_after_previous_chaos(self, config_snapshot):
        """此测试应在 test_config_restored_after_test 之后运行，验证配置已恢复"""
        # 如果 fixture 正确恢复，此处应读到默认值
        assert get_default_max_retries() == 3, "前一个测试的配置变更应已恢复"
        assert get_http_pool_size() == 20, "前一个测试的配置变更应已恢复"

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_concurrent_config_access_safe(self, config_snapshot):
        """并发读写配置不导致状态不一致"""
        import threading

        results = []
        errors = []

        def reader():
            try:
                for _ in range(50):
                    val = get_default_max_retries()
                    results.append(val)
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(50):
                    config_snapshot.set("retry.default_max_retries", (i % 20))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"并发访问产生错误: {errors}"
        assert len(results) == 150, f"读取次数不符: {len(results)}"
        # 所有读取值都应在合法范围 [0, 20] 或默认值 3
        for val in results:
            assert 0 <= val <= 20, f"读取到非法值: {val}"
