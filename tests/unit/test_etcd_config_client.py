"""[TLM] etcd 配置中心 P0 优化（watch 推送 + 内存缓存）单元测试

测试边界:
- 用 unittest.mock 替换 etcd3 客户端 / Orchestrator / MetricsCollector
- 不依赖真实 etcd 服务，不依赖网络
- 聚焦 P0 优化点: watch 推送 + 内存缓存预热 + 指标埋点 + 可观测性接口

覆盖场景:
1. watch 事件推送: 配置变更实时写入 _SEM_API_OVERRIDE
2. watch 删除事件: 从内存缓存移除键
3. watch 异常: 推送失败计数 + 不抛异常
4. 启动预热: apply_etcd_config_to_orchestrator 一次性加载全量配置
5. 预热失败: etcd 不可用时返回 False + 记录延迟
6. 可观测性接口: get_etcd_cache_state 返回正确状态
7. 指标埋点防御式: MetricsCollector 异常时不影响主链路
8. 重试耗尽指标: retry_policy.should_retry 返回 False 时记录 RETRY_EXHAUSTED
"""
import asyncio
import json
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

import pytest

# 确保项目根目录在 sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.config import etcd_config_client
from agent.config.etcd_config_client import (
    _watch_callback_async,
    apply_etcd_config_to_orchestrator,
    get_etcd_cache_state,
    _record_metric,
    ETCD_CONFIG_PREFIX,
    _METRIC_EVENT_TOTAL,
    _METRIC_PUSH_TOTAL,
    _METRIC_PUSH_FAILURE,
    _METRIC_PUSH_LATENCY,
    _METRIC_PREHEAT_LATENCY,
    _METRIC_RECONNECT_TOTAL,
    _METRIC_RETRY_EXHAUSTED,
)


# ═══════════════════════════════════════════════════════════════
# Mock 工厂
# ═══════════════════════════════════════════════════════════════

def _make_mock_event(key: str, value=None):
    """构造 etcd3 watch 事件替身

    Args:
        key: 配置键（不含前缀）
        value: 配置值；None 表示删除事件
    """
    event = MagicMock()
    full_key = (ETCD_CONFIG_PREFIX + key).encode('utf-8')
    event.key = full_key
    if value is None:
        # 删除事件: 无 value 属性或 value 为空
        event.value = None
        del_attr = hasattr(event, 'value')
        # 让 hasattr(event, 'value') 返回 True 但 event.value 为 None
    else:
        event.value = json.dumps(value).encode('utf-8')
    return event


def _make_mock_orchestrator_class(override=None):
    """构造 Orchestrator 类替身（含 _SEM_API_OVERRIDE 类属性）"""
    mock_cls = MagicMock()
    mock_cls._SEM_API_OVERRIDE = dict(override) if override else None
    mock_cls._clear_semantic_config_cache = MagicMock()
    return mock_cls


@pytest.fixture(autouse=True)
def _reset_module_state():
    """每个测试前后重置 etcd_config_client 模块级状态

    避免跨测试污染（watch 线程 / etcd 客户端单例）
    """
    etcd_config_client._etcd_client = None
    etcd_config_client._watch_thread = None
    etcd_config_client._watch_loop = None
    etcd_config_client._watch_task = None
    etcd_config_client._watch_stop.clear()
    yield
    etcd_config_client._etcd_client = None
    etcd_config_client._watch_thread = None
    etcd_config_client._watch_loop = None
    etcd_config_client._watch_task = None


# ═══════════════════════════════════════════════════════════════
# 1. watch 推送 + 内存缓存
# ═══════════════════════════════════════════════════════════════

class TestWatchPushMemoryCache:
    """验证 watch 事件实时推送到 _SEM_API_OVERRIDE 内存缓存"""

    @pytest.mark.asyncio
    async def test_配置变更_推送到内存缓存(self):
        """场景1: watch 收到 PUT 事件 → 写入 _SEM_API_OVERRIDE + 清缓存 + 记录指标"""
        mock_orch = _make_mock_orchestrator_class()
        metric_calls = []

        with patch("agent.config.etcd_config_client._record_metric",
                   side_effect=lambda name, value=1.0, is_counter=True: metric_calls.append((name, value, is_counter))):
            with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": MagicMock(Orchestrator=mock_orch)}):
                event = _make_mock_event("min_score", value=0.7)
                await _watch_callback_async(event)

        # 内存缓存已更新
        assert mock_orch._SEM_API_OVERRIDE == {"min_score": 0.7}
        # 缓存清除被调用
        mock_orch._clear_semantic_config_cache.assert_called_once()
        # 指标埋点: 事件计数 + 推送计数 + 推送延迟
        metric_names = [c[0] for c in metric_calls]
        assert _METRIC_EVENT_TOTAL in metric_names
        assert _METRIC_PUSH_TOTAL in metric_names
        assert _METRIC_PUSH_LATENCY in metric_names
        # 推送延迟是 histogram（is_counter=False）
        push_lat_call = next(c for c in metric_calls if c[0] == _METRIC_PUSH_LATENCY)
        assert push_lat_call[2] is False
        assert push_lat_call[1] > 0  # 延迟 > 0

    @pytest.mark.asyncio
    async def test_配置删除_从内存缓存移除(self):
        """场景2: watch 收到 DELETE 事件 → 从 _SEM_API_OVERRIDE 移除键"""
        mock_orch = _make_mock_orchestrator_class(override={"min_score": 0.7, "top_k": 5})

        with patch("agent.config.etcd_config_client._record_metric"):
            with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": MagicMock(Orchestrator=mock_orch)}):
                # value=None 触发删除分支
                event = _make_mock_event("min_score", value=None)
                await _watch_callback_async(event)

        # min_score 已移除，top_k 保留
        assert "min_score" not in mock_orch._SEM_API_OVERRIDE
        assert mock_orch._SEM_API_OVERRIDE == {"top_k": 5}
        mock_orch._clear_semantic_config_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_删除不存在的键_静默跳过(self):
        """场景3: 删除事件但键不在缓存中 → 不抛异常，缓存不变"""
        mock_orch = _make_mock_orchestrator_class(override={"top_k": 5})

        with patch("agent.config.etcd_config_client._record_metric"):
            with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": MagicMock(Orchestrator=mock_orch)}):
                event = _make_mock_event("not_exist_key", value=None)
                await _watch_callback_async(event)

        # 缓存不变
        assert mock_orch._SEM_API_OVERRIDE == {"top_k": 5}

    @pytest.mark.asyncio
    async def test_watch异常_记录失败指标不抛错(self):
        """场景4: 回调中抛异常 → 记录 PUSH_FAILURE + 不向上传播"""
        mock_orch = _make_mock_orchestrator_class()
        # 让 clear_semantic_config_cache 抛异常
        mock_orch._clear_semantic_config_cache.side_effect = RuntimeError("simulated crash")
        metric_calls = []

        with patch("agent.config.etcd_config_client._record_metric",
                   side_effect=lambda name, value=1.0, is_counter=True: metric_calls.append(name)):
            with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": MagicMock(Orchestrator=mock_orch)}):
                event = _make_mock_event("min_score", value=0.7)
                # 不应抛异常
                await _watch_callback_async(event)

        # 失败指标已记录
        assert _METRIC_PUSH_FAILURE in metric_calls
        # 事件计数已记录（在异常前）
        assert _METRIC_EVENT_TOTAL in metric_calls


# ═══════════════════════════════════════════════════════════════
# 2. 启动预热
# ═══════════════════════════════════════════════════════════════

class TestStartupPreheat:
    """验证启动时一次性预热内存缓存"""

    def test_预热成功_加载全量配置到内存缓存(self):
        """场景5: etcd 有配置 → 一次性加载到 _SEM_API_OVERRIDE + 记录延迟"""
        mock_orch = _make_mock_orchestrator_class()
        mock_overrides = {"min_score": 0.7, "top_k": 10, "enabled": True}
        metric_calls = []

        with patch("agent.config.etcd_config_client._record_metric",
                   side_effect=lambda name, value=1.0, is_counter=True: metric_calls.append((name, value))):
            with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": MagicMock(Orchestrator=mock_orch)}):
                with patch("agent.config.etcd_config_client.load_config_from_etcd",
                           return_value=mock_overrides):
                    result = apply_etcd_config_to_orchestrator()

        assert result is True
        assert mock_orch._SEM_API_OVERRIDE == mock_overrides
        mock_orch._clear_semantic_config_cache.assert_called_once()
        # 指标: 预热延迟 + 推送计数（N 个键）
        metric_names = [c[0] for c in metric_calls]
        assert _METRIC_PREHEAT_LATENCY in metric_names
        assert _METRIC_PUSH_TOTAL in metric_names
        # 推送计数 = 键数量
        push_call = next(c for c in metric_calls if c[0] == _METRIC_PUSH_TOTAL)
        assert push_call[1] == 3  # min_score + top_k + enabled

    def test_预热失败_etcd不可用返回False(self):
        """场景6: etcd 不可用（load 返回 None）→ 返回 False + 记录延迟"""
        mock_orch = _make_mock_orchestrator_class()
        metric_calls = []

        with patch("agent.config.etcd_config_client._record_metric",
                   side_effect=lambda name, value=1.0, is_counter=True: metric_calls.append((name, value))):
            with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": MagicMock(Orchestrator=mock_orch)}):
                with patch("agent.config.etcd_config_client.load_config_from_etcd",
                           return_value=None):
                    result = apply_etcd_config_to_orchestrator()

        assert result is False
        # 预热失败也记录延迟（便于诊断 etcd 连接耗时）
        assert _METRIC_PREHEAT_LATENCY in [c[0] for c in metric_calls]
        # 不应记录 PUSH_TOTAL（未推送任何配置）
        assert _METRIC_PUSH_TOTAL not in [c[0] for c in metric_calls]
        # _SEM_API_OVERRIDE 不被修改
        assert mock_orch._SEM_API_OVERRIDE is None


# ═══════════════════════════════════════════════════════════════
# 3. 可观测性接口
# ═══════════════════════════════════════════════════════════════

class TestObservabilityInterface:
    """验证 get_etcd_cache_state 返回正确状态"""

    def test_返回缓存状态_启用且有线程(self, monkeypatch):
        """场景7: etcd 启用 + watch 线程存活 → 返回完整状态"""
        monkeypatch.setenv("ETCD_ENABLED", "true")
        mock_orch = _make_mock_orchestrator_class(override={"min_score": 0.7})
        # 模拟 watch 线程存活
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        etcd_config_client._watch_thread = mock_thread
        etcd_config_client._etcd_client = MagicMock()  # 已连接

        with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": MagicMock(Orchestrator=mock_orch)}):
            state = get_etcd_cache_state()

        assert state["enabled"] is True
        assert state["cache_size"] == 1
        assert state["cache_keys"] == ["min_score"]
        assert state["watch_alive"] is True
        assert state["etcd_connected"] is True

    def test_返回缓存状态_未启用且无线程(self, monkeypatch):
        """场景8: etcd 未启用 + 无 watch 线程 → enabled=False, watch_alive=False"""
        monkeypatch.setenv("ETCD_ENABLED", "false")
        mock_orch = _make_mock_orchestrator_class(override={})

        with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": MagicMock(Orchestrator=mock_orch)}):
            state = get_etcd_cache_state()

        assert state["enabled"] is False
        assert state["watch_alive"] is False
        assert state["etcd_connected"] is False
        assert state["cache_size"] == 0

    def test_Orchestrator导入失败_静默降级返回空状态(self):
        """场景9: Orchestrator 导入异常 → 返回空缓存状态，不抛异常"""
        with patch.dict("sys.modules", {"agent.orchestrator.orchestrator": None}):
            # 应不抛异常
            state = get_etcd_cache_state()

        assert state["cache_size"] == 0
        assert state["cache_keys"] == []


# ═══════════════════════════════════════════════════════════════
# 4. 指标埋点防御式
# ═══════════════════════════════════════════════════════════════

class TestMetricDefensive:
    """验证 _record_metric 防御式（采集失败不影响主链路）"""

    def test_MetricsCollector异常_不抛错(self):
        """场景10: get_metrics_collector 抛异常 → _record_metric 静默吞掉"""
        with patch("agent.monitoring.metrics.get_metrics_collector",
                   side_effect=RuntimeError("collector unavailable")):
            # 不应抛异常
            _record_metric(_METRIC_EVENT_TOTAL)
            _record_metric(_METRIC_PUSH_LATENCY, 1.5, is_counter=False)

    def test_模块导入失败_不抛错(self):
        """场景11: agent.monitoring.metrics 模块不存在 → _record_metric 静默吞掉"""
        with patch.dict("sys.modules", {"agent.monitoring.metrics": None}):
            _record_metric(_METRIC_PUSH_TOTAL, 5)
            # 不抛异常即通过


# ═══════════════════════════════════════════════════════════════
# 5. watch 循环重试指标
# ═══════════════════════════════════════════════════════════════

class TestWatchLoopRetryMetric:
    """验证 watch 循环在重试/重试耗尽时记录指标"""

    @pytest.mark.asyncio
    async def test_重连时记录RECONNECT指标(self):
        """场景12: watch 断连 + should_retry=True → 记录 RECONNECT_TOTAL"""
        from agent.config.etcd_config_client import _watch_loop_async

        mock_client = MagicMock()
        # watch_prefix 第一次抛异常触发重试
        mock_client.watch_prefix.side_effect = ConnectionError("etcd disconnected")

        mock_retry = MagicMock()
        mock_retry.should_retry.return_value = False  # 不重试，直接耗尽

        metric_calls = []
        call_count = [0]

        async def _fast_sleep(seconds):
            call_count[0] += 1  # 避免无限循环

        with patch("agent.config.etcd_config_client._get_etcd_client", return_value=mock_client):
            with patch("agent.config.etcd_config_client._get_retry_policy", return_value=mock_retry):
                with patch("agent.config.etcd_config_client._record_metric",
                           side_effect=lambda name, value=1.0, is_counter=True: metric_calls.append(name)):
                    with patch("asyncio.sleep", new=_fast_sleep):
                        await _watch_loop_async()

        # 重试耗尽指标已记录
        assert _METRIC_RETRY_EXHAUSTED in metric_calls
        # should_retry 被调用一次
        mock_retry.should_retry.assert_called_once()
