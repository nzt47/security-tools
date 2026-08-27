#!/usr/bin/env python3
"""etcd 配置中心集成 — 语义层配置热更（方案2）

依赖: pip install etcd3
适用: K8s 多副本部署，配置全局共享

架构:
  etcd 配置中心 → watch 回调 → _SEM_API_OVERRIDE 更新 → 即时生效

设计约束:
  【不易】etcd 不可用时降级到 SQLite/内存模式，不影响主链路
  【变易】watch 线程监听配置变更，秒级同步到所有副本
  【简易】复用 _SEM_API_OVERRIDE 覆盖层，零侵入 orchestrator.py

P0 优化（watch 推送 + 内存缓存）:
  - watch 事件实时推送到 _SEM_API_OVERRIDE 内存缓存（毫秒级生效）
  - 启动时一次性预热内存缓存（避免首请求 cold read）
  - Prometheus 指标埋点: 事件数 / 重连次数 / 推送延迟 / 缓存大小
  - 可观测性接口: get_etcd_cache_state() 供运维诊断

环境变量:
  ETCD_ENABLED=true          # 启用 etcd 集成
  ETCD_HOST=localhost        # etcd 地址
  ETCD_PORT=2379             # etcd 端口
"""
import json
import logging
import asyncio
import threading
import time
from typing import Optional, Dict, Any

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# etcd3 延迟导入（仅在使用时加载，避免未安装时模块导入失败）
_etcd_client = None
_watch_task: Optional[asyncio.Task] = None          # 异步 watch 任务
_watch_thread: Optional[threading.Thread] = None     # 兼容: 同步守护线程（运行事件循环）
_watch_loop: Optional[asyncio.AbstractEventLoop] = None  # 事件循环引用
_watch_stop = threading.Event()

ETCD_CONFIG_PREFIX = "/orchestrator/semantic_layer/"

# 【变易】重试策略 — 复用项目统一 RetryPolicy（agent.error_handler）
# etcd watch 断连时自动重试，避免配置变更丢失
_ETCD_RETRY_POLICY = None  # 延迟初始化（避免模块导入时依赖）


# ═══════════════════════════════════════════════════════════════
# P0 优化: Prometheus 指标埋点（防御式，采集失败不影响主链路）
# 复用 agent.monitoring.metrics.MetricsCollector 接口
# 指标命名遵循项目惯例: count.{module}.{action}.total / {module}.{action}.latency_ms
# ═══════════════════════════════════════════════════════════════
_METRIC_EVENT_TOTAL = "count.etcd.watch.event.total"            # watch 事件总数
_METRIC_RECONNECT_TOTAL = "count.etcd.watch.reconnect.total"    # 重连次数
_METRIC_RETRY_EXHAUSTED = "count.etcd.watch.retry_exhausted.total"  # 重试耗尽
_METRIC_PUSH_TOTAL = "count.etcd.config.push.total"             # 推送到内存缓存次数
_METRIC_PUSH_FAILURE = "count.etcd.config.push.failure"         # 推送失败次数
_METRIC_PUSH_LATENCY = "etcd.config.push.latency_ms"            # 推送延迟（ms）
_METRIC_PREHEAT_LATENCY = "etcd.config.preheat.latency_ms"      # 启动预热延迟（ms）


def _record_metric(metric_name: str, value: float = 1.0, is_counter: bool = True) -> None:
    """记录 Prometheus 指标（防御式，失败静默）

    【不易】指标采集失败绝不影响配置热更主链路
    【简易】统一 try/except，调用方无需关心异常
    """
    try:
        from agent.monitoring.metrics import get_metrics_collector
        collector = get_metrics_collector()
        if is_counter:
            collector.increment_counter(metric_name, int(value))
        else:
            collector.record_latency(metric_name, float(value))
    except Exception:
        pass  # 指标采集失败静默降级


def _get_retry_policy():
    """获取 etcd watch 重试策略（延迟初始化）"""
    global _ETCD_RETRY_POLICY
    if _ETCD_RETRY_POLICY is not None:
        return _ETCD_RETRY_POLICY
    try:
        from agent.error_handler import RetryPolicy
        _ETCD_RETRY_POLICY = RetryPolicy(
            max_retries=5,              # 最多重试 5 次
            initial_delay=1.0,          # 首次重试 1s
            max_delay=30.0,             # 最大延迟 30s
            backoff_factor=2.0,         # 指数退避
            jitter_factor=0.1,          # 10% 抖动
            strategy="exponential",
        )
    except ImportError:
        # 降级: 简单重试策略（不依赖 error_handler）
        class _SimpleRetry:
            def should_retry(self, exc, attempt):
                return attempt < 5
            def calculate_delay(self, attempt):
                import random
                return min(1.0 * (2 ** attempt), 30.0) * random.uniform(0.9, 1.1)
        _ETCD_RETRY_POLICY = _SimpleRetry()
    return _ETCD_RETRY_POLICY


def _get_etcd_client():
    """获取 etcd 客户端（延迟初始化，单例）"""
    global _etcd_client
    if _etcd_client is not None:
        return _etcd_client

    try:
        import etcd3
        import os
        host = os.environ.get("ETCD_HOST", "localhost")
        port = int(os.environ.get("ETCD_PORT", "2379"))
        _etcd_client = etcd3.client(host=host, port=port)
        logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd.success', 'msg': "[etcd] 连接成功: %s:%d" % (host, port)}))
        return _etcd_client
    except ImportError:
        logger.warning(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] etcd3 未安装，请执行: pip install etcd3"}))
        return None
    except Exception as e:
        logger.error(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd.failed', 'msg': "[etcd] 连接失败: %s" % e}))
        return None


def load_config_from_etcd() -> Optional[Dict[str, Any]]:
    """从 etcd 加载语义层配置（一次性读取）

    Returns:
        配置字典 {key: value}，或 None（etcd 不可用/无配置）
    """
    client = _get_etcd_client()
    if client is None:
        return None

    try:
        overrides = {}
        for value, meta in client.get_prefix(ETCD_CONFIG_PREFIX):
            key = meta.key.decode('utf-8').replace(ETCD_CONFIG_PREFIX, '')
            try:
                overrides[key] = json.loads(value.decode('utf-8'))
            except (ValueError, TypeError):
                pass

        if overrides:
            logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] 加载配置: keys=%s" % list(overrides.keys())}))
        return overrides if overrides else None
    except Exception as e:
        logger.error(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd.failed', 'msg': "[etcd] 加载配置失败: %s" % e}))
        return None


def apply_etcd_config_to_orchestrator() -> bool:
    """将 etcd 配置应用到 Orchestrator._SEM_API_OVERRIDE（启动预热）

    P0 优化: 启动时一次性拉取全量配置预热内存缓存，
    避免首个请求触发 cold read（etcd 网络往返）。

    Returns:
        True 表示成功加载并应用，False 表示 etcd 不可用或无配置
    """
    from agent.orchestrator.orchestrator import Orchestrator

    _t0 = time.perf_counter()
    overrides = load_config_from_etcd()
    if overrides is None:
        # 预热失败也记录延迟（便于诊断 etcd 连接耗时）
        _record_metric(_METRIC_PREHEAT_LATENCY, (time.perf_counter() - _t0) * 1000, is_counter=False)
        return False

    if Orchestrator._SEM_API_OVERRIDE is None:
        Orchestrator._SEM_API_OVERRIDE = {}
    Orchestrator._SEM_API_OVERRIDE.update(overrides)
    Orchestrator._clear_semantic_config_cache()

    _elapsed_ms = (time.perf_counter() - _t0) * 1000
    _record_metric(_METRIC_PREHEAT_LATENCY, _elapsed_ms, is_counter=False)
    _record_metric(_METRIC_PUSH_TOTAL, len(overrides))  # 预热=N 次推送
    logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] 配置已应用到 Orchestrator (预热): keys=%s latency=%.2fms" % (list(overrides.keys()), _elapsed_ms)}))
    return True


async def _watch_callback_async(event):
    """etcd watch 异步回调 — 配置变更时推送到 _SEM_API_OVERRIDE 内存缓存

    P0 优化: watch 事件实时推送，毫秒级生效；带指标埋点便于线上观测

    【不易】回调中不持锁，_SEM_API_OVERRIDE 的 dict 操作在 GIL 下原子
    【变易】异步执行，不阻塞事件循环；异常不传播（避免 watch 任务退出）
    【简易】用 asyncio.to_thread 包装重 I/O（如 Orchestrator 操作），避免阻塞事件循环
    """
    from agent.orchestrator.orchestrator import Orchestrator

    _t0 = time.perf_counter()
    _record_metric(_METRIC_EVENT_TOTAL)  # 事件计数

    try:
        key = event.key.decode('utf-8').replace(ETCD_CONFIG_PREFIX, '')
        if hasattr(event, 'value') and event.value:
            value = json.loads(event.value.decode('utf-8'))
            # dict 操作在 GIL 下原子，无需锁
            if Orchestrator._SEM_API_OVERRIDE is None:
                Orchestrator._SEM_API_OVERRIDE = {}
            Orchestrator._SEM_API_OVERRIDE[key] = value
            logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] 配置变更: %s=%s" % (key, value)}))
        else:
            # 键被删除 → 移除覆盖
            if Orchestrator._SEM_API_OVERRIDE and key in Orchestrator._SEM_API_OVERRIDE:
                del Orchestrator._SEM_API_OVERRIDE[key]
                logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] 配置删除: %s" % key}))

        # 清除缓存（轻量操作，同步即可）
        Orchestrator._clear_semantic_config_cache()

        # 推送成功指标: 计数 + 延迟
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        _record_metric(_METRIC_PUSH_TOTAL)
        _record_metric(_METRIC_PUSH_LATENCY, _elapsed_ms, is_counter=False)
    except Exception as e:
        # 推送失败指标（不抛异常，避免 watch 任务退出）
        _record_metric(_METRIC_PUSH_FAILURE)
        logger.error(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd.failed', 'msg': "[etcd] watch 异步回调处理失败: %s" % e}))


async def _watch_loop_async():
    """异步 watch 循环 — 监听 etcd 配置变更 + 断连重试

    【变易】etcd3 的 watch_prefix 是同步阻塞迭代器，用 asyncio.to_thread 包装
           避免阻塞事件循环；断连时用 RetryPolicy 指数退避重试
    【不易】重试耗尽后记录 ERROR，不抛异常（避免事件循环崩溃）
    """
    client = _get_etcd_client()
    if client is None:
        return

    retry_policy = _get_retry_policy()
    attempt = 0

    logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] 异步 watch 已启动: prefix=%s" % ETCD_CONFIG_PREFIX}))

    while not _watch_stop.is_set():
        try:
            # etcd3 watch_prefix 返回 (events_iterator, cancel_fn)
            # 用 asyncio.to_thread 包装同步迭代器的获取，避免阻塞
            events, cancel = await asyncio.to_thread(client.watch_prefix, ETCD_CONFIG_PREFIX)

            # 逐个处理事件（迭代器是阻塞的，用 to_thread 包装 next 调用）
            while not _watch_stop.is_set():
                try:
                    # 用 to_thread 获取下一个事件（避免阻塞事件循环）
                    event = await asyncio.to_thread(next, events)
                    # 异步处理事件
                    await _watch_callback_async(event)
                except StopIteration:
                    break  # 迭代器耗尽，重新建立 watch

            attempt = 0  # 成功执行后重置重试计数

        except Exception as e:
            if _watch_stop.is_set():
                break  # 主动停止，不重试

            attempt += 1
            if retry_policy.should_retry(e, attempt):
                delay = retry_policy.calculate_delay(attempt)
                _record_metric(_METRIC_RECONNECT_TOTAL)  # 重连计数
                logger.warning(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] watch 断连，%d 秒后重试 (attempt=%d): %s" % (round(delay, 2), attempt, e)}))
                await asyncio.sleep(delay)
            else:
                _record_metric(_METRIC_RETRY_EXHAUSTED)  # 重试耗尽
                logger.error(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] watch 重试耗尽 (%d 次)，停止监听: %s" % (attempt, e)}))
                break  # 重试耗尽，退出循环


def start_etcd_watch() -> None:
    """启动 etcd 异步 watch（在守护线程的事件循环中运行）

    【变易】asyncio 事件循环运行在守护线程中，不阻塞主线程
    【简易】兼容同步调用接口（start_etcd_watch() 仍为同步函数）
    """
    global _watch_thread, _watch_loop, _watch_task

    client = _get_etcd_client()
    if client is None:
        return

    if _watch_thread is not None and _watch_thread.is_alive():
        return  # 已启动

    _watch_stop.clear()

    def _run_event_loop():
        """在守护线程中运行 asyncio 事件循环"""
        global _watch_loop, _watch_task
        _watch_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_watch_loop)
        _watch_task = _watch_loop.create_task(_watch_loop_async())
        try:
            _watch_loop.run_until_complete(_watch_task)
        except Exception as e:
            logger.error(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd.failed', 'msg': "[etcd] 事件循环异常: %s" % e}))
        finally:
            _watch_loop.close()

    _watch_thread = threading.Thread(target=_run_event_loop, daemon=True, name="etcd-async-watch")
    _watch_thread.start()


def stop_etcd_watch() -> None:
    """停止 etcd 异步 watch（优雅关闭）"""
    global _watch_thread, _watch_loop, _watch_task

    _watch_stop.set()

    # 取消异步任务
    if _watch_task is not None and not _watch_task.done():
        if _watch_loop is not None and _watch_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                _watch_task.cancel(), _watch_loop
            )

    # 等待线程退出
    if _watch_thread is not None:
        _watch_thread.join(timeout=5)
        _watch_thread = None

    _watch_loop = None
    _watch_task = None
    logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] 异步 watch 已停止"}))


# ═══════════════════════════════════════════════════════════════
# 可观测性接口（供运维诊断 / 监控大盘调用）
# ═══════════════════════════════════════════════════════════════

def get_etcd_cache_state() -> Dict[str, Any]:
    """获取 etcd 内存缓存当前状态（供运维诊断 / 监控大盘）

    Returns:
        {
            "enabled": bool,           # etcd 集成是否启用
            "cache_size": int,         # 内存缓存键数量
            "cache_keys": list,        # 缓存键列表
            "watch_alive": bool,       # watch 线程是否存活
            "etcd_connected": bool,    # etcd 客户端是否已连接
        }

    【简易】纯只读，不触发任何 I/O，可高频调用
    """
    import os
    try:
        from agent.orchestrator.orchestrator import Orchestrator
        override = Orchestrator._SEM_API_OVERRIDE or {}
        cache_keys = list(override.keys())
    except Exception:
        override = {}
        cache_keys = []

    return {
        "enabled": os.environ.get("ETCD_ENABLED", "false").lower() in ("true", "1", "yes"),
        "cache_size": len(override),
        "cache_keys": cache_keys,
        "watch_alive": _watch_thread is not None and _watch_thread.is_alive(),
        "etcd_connected": _etcd_client is not None,
    }


# ═══════════════════════════════════════════════════════════════
# 集成入口（在 app_server.py 启动时调用）
# ═══════════════════════════════════════════════════════════════

def init_etcd_config_integration() -> None:
    """初始化 etcd 配置中心集成

    在 app_server.py 启动时调用:
        from agent.config.etcd_config_client import init_etcd_config_integration
        init_etcd_config_integration()

    流程:
      1. 检查 ETCD_ENABLED 环境变量
      2. 启动时从 etcd 加载配置（一次性预热内存缓存）
      3. 启动 watch 线程监听变更（持续推送）

    【不易】etcd 不可用时降级到 SQLite/内存模式，不抛异常
    """
    import os
    if os.environ.get("ETCD_ENABLED", "false").lower() not in ("true", "1", "yes"):
        logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] 未启用 (设置 ETCD_ENABLED=true 以启用)"}))
        return

    logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd', 'msg': "[etcd] 初始化配置中心集成..."}))

    # 1. 启动时加载配置（预热内存缓存，避免首请求 cold read）
    if apply_etcd_config_to_orchestrator():
        try:
            from agent.orchestrator.orchestrator import Orchestrator
            cache_size = len(Orchestrator._SEM_API_OVERRIDE or {})
            logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd.success', 'msg': "[etcd] 启动配置加载成功 (预热缓存: %d 个键)" % cache_size}))
        except Exception:
            logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd.success', 'msg': "[etcd] 启动配置加载成功"}))
    else:
        logger.info(log_dict({'module_name': 'etcd_config_client', 'action': 'etcd.failed', 'msg': "[etcd] 无配置或加载失败，使用现有配置"}))

    # 2. 启动 watch 监听变更（持续推送）
    start_etcd_watch()
