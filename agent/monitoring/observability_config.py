"""可观测性配置集中化模块

将散落于多处的可观测性配置收拢到统一入口：
- 追踪配置（原 agent/monitoring/tracing_config.py）
- 采样率
- 日志级别与输出路径
- 指标采集开关
- 健康检查频率
- 资源监控配置

核心特性：
1. 声明式 ValidationRule 验证架构（path/validator/default/error_message）
2. 热加载：配置变更无需重启，复用 disaster_recovery.ConfigHotReloader
3. 向后兼容：保留 TracingConfig 原入口，内部委托到本模块
4. 原子性变更：变更失败自动回滚到上一个有效配置
5. 启动时自动验证并修复无效配置项

使用示例：
    from agent.monitoring.observability_config import get_observability_config

    config = get_observability_config()
    # 读取配置
    interval = config.get("resource_monitor.sample_interval_sec")
    # 修改配置（运行时热生效）
    config.set("resource_monitor.sample_interval_sec", 30)
"""

import json
import logging
import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.monitoring.tracing import get_trace_id

logger = logging.getLogger(__name__)


# ============================================================================
# ValidationRule 声明式验证架构
# ============================================================================

@dataclass
class ValidationRule:
    """声明式配置验证规则

    Attributes:
        path: 配置路径（点分式，如 "resource_monitor.sample_interval_sec"）
        validator: 验证函数，返回 (is_valid: bool, repaired_value: Any)
        default: 默认值（验证失败且无法修复时使用）
        error_message: 验证失败时的错误描述
        description: 配置项说明（用于文档生成）
    """

    path: str
    validator: Callable[[Any], Tuple[bool, Any]]
    default: Any
    error_message: str
    description: str = ""


def _range_validator(min_val: float, max_val: float) -> Callable[[Any], Tuple[bool, Any]]:
    """构造范围验证器（数值类型）"""
    def _validate(value: Any) -> Tuple[bool, Any]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False, None
        if min_val <= v <= max_val:
            return True, v
        # 超出范围则修复到默认区间的中点
        repaired = (min_val + max_val) / 2
        return False, repaired
    return _validate


def _choice_validator(choices: List[str]) -> Callable[[Any], Tuple[bool, Any]]:
    """构造枚举验证器"""
    def _validate(value: Any) -> Tuple[bool, Any]:
        if value in choices:
            return True, value
        return False, choices[0]
    return _validate


def _bool_validator() -> Callable[[Any], Tuple[bool, Any]]:
    """构造布尔验证器"""
    def _validate(value: Any) -> Tuple[bool, Any]:
        if isinstance(value, bool):
            return True, value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes"), True
        if isinstance(value, (int, float)):
            return value != 0, bool(value)
        return False, True
    return _validate


def _path_validator() -> Callable[[Any], Tuple[bool, Any]]:
    """构造路径验证器（允许空字符串表示不输出到文件）"""
    def _validate(value: Any) -> Tuple[bool, Any]:
        if not isinstance(value, str):
            return False, ""
        # 空字符串表示输出到 stdout，合法
        if value == "":
            return True, ""
        # 非空时验证父目录可写（不强制创建，仅校验合法性）
        parent = os.path.dirname(value) or "."
        if os.path.isdir(parent):
            return True, value
        # 父目录不存在时修复为空（stdout）
        return False, ""
    return _validate


# ============================================================================
# 配置验证规则定义表（覆盖 10 个核心配置项）
# ============================================================================

OBSERVABILITY_VALIDATION_RULES: List[ValidationRule] = [
    # ── 1. 追踪配置（tracing） ──
    ValidationRule(
        path="tracing.env",
        validator=_choice_validator(["development", "staging", "production"]),
        default="development",
        error_message="tracing.env 必须是 development/staging/production 之一",
        description="追踪环境类型",
    ),
    ValidationRule(
        path="tracing.log_level",
        validator=_choice_validator(["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]),
        default="INFO",
        error_message="tracing.log_level 必须是 DEBUG/INFO/WARN/ERROR/CRITICAL 之一",
        description="追踪日志级别",
    ),
    ValidationRule(
        path="tracing.sampler_ratio",
        validator=_range_validator(0.0, 1.0),
        default=0.1,
        error_message="tracing.sampler_ratio 必须在 0.0-1.0 之间",
        description="追踪采样比例",
    ),

    # ── 2. 日志配置（logging） ──
    ValidationRule(
        path="logging.level",
        validator=_choice_validator(["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]),
        default="INFO",
        error_message="logging.level 必须是合法日志级别",
        description="全局日志级别",
    ),
    ValidationRule(
        path="logging.output_path",
        validator=_path_validator(),
        default="",
        error_message="logging.output_path 父目录必须存在或为空（stdout）",
        description="日志输出路径（空表示 stdout）",
    ),

    # ── 3. 指标采集（metrics） ──
    ValidationRule(
        path="metrics.enabled",
        validator=_bool_validator(),
        default=True,
        error_message="metrics.enabled 必须是布尔值",
        description="是否启用指标采集",
    ),

    # ── 4. 健康检查（health_check） ──
    ValidationRule(
        path="health_check.interval_sec",
        validator=_range_validator(5, 3600),
        default=60,
        error_message="health_check.interval_sec 必须在 5-3600 秒之间",
        description="健康检查频率（秒）",
    ),

    # ── 5. 资源监控（resource_monitor） ──
    ValidationRule(
        path="resource_monitor.enabled",
        validator=_bool_validator(),
        default=True,
        error_message="resource_monitor.enabled 必须是布尔值",
        description="是否启用资源监控",
    ),
    ValidationRule(
        path="resource_monitor.sample_interval_sec",
        validator=_range_validator(1, 3600),
        default=60,
        error_message="resource_monitor.sample_interval_sec 必须在 1-3600 秒之间",
        description="资源采样间隔（秒）",
    ),
    ValidationRule(
        path="resource_monitor.stress_test_interval_sec",
        validator=_range_validator(0.5, 10),
        default=1.0,
        error_message="resource_monitor.stress_test_interval_sec 必须在 0.5-10 秒之间",
        description="压测模式采样间隔（秒）",
    ),
    ValidationRule(
        path="resource_monitor.leak_slope_threshold",
        validator=_range_validator(0.0, 1000.0),
        default=1.0,
        error_message="resource_monitor.leak_slope_threshold 必须非负",
        description="资源增长斜率告警阈值（字节/采样）",
    ),
    ValidationRule(
        path="resource_monitor.history_size",
        validator=_range_validator(10, 10000),
        default=1440,
        error_message="resource_monitor.history_size 必须在 10-10000 之间",
        description="历史采样保留数量",
    ),

    # ── 6. 资源监控持久化（resource_monitor.persist） ──
    ValidationRule(
        path="resource_monitor.persist_enabled",
        validator=_bool_validator(),
        default=True,
        error_message="resource_monitor.persist_enabled 必须是布尔值",
        description="是否启用历史采样落盘（跨重启趋势分析）",
    ),
    ValidationRule(
        path="resource_monitor.persist_path",
        validator=_path_validator(),
        default="",
        error_message="resource_monitor.persist_path 父目录必须存在或为空（使用默认路径）",
        description="持久化文件路径（空表示使用默认 ./data/resource_monitor_history.jsonl）",
    ),
    ValidationRule(
        path="resource_monitor.persist_max_age_hours",
        validator=_range_validator(1, 720),
        default=168,
        error_message="resource_monitor.persist_max_age_hours 必须在 1-720 小时之间（1 小时-30 天）",
        description="持久化数据最大保留时长（小时），默认 7 天",
    ),
    ValidationRule(
        path="resource_monitor.persist_batch_size",
        validator=_range_validator(1, 1000),
        default=100,
        error_message="resource_monitor.persist_batch_size 必须在 1-1000 之间",
        description="批量落盘的缓冲条数（达到此数量触发写入）",
    ),

    # ── 7. 时间窗口上限（time_window） ──
    # 统一管理所有 timedelta(days=N) 调用的上限，防止 OverflowError
    ValidationRule(
        path="time_window.max_analyze_days",
        validator=_range_validator(1, 36500),
        default=36500,
        error_message="time_window.max_analyze_days 必须在 1-36500 之间（100 年上限）",
        description="时间窗口分析上限天数，用于 data_analytics/replay_storage/defect_tracker 等模块的 timedelta(days=) 参数校验",
    ),

    # ── 8. 重试策略（retry） ──
    # 统一管理 RetryPolicy/with_retry/async_with_retry 的默认最大重试次数
    ValidationRule(
        path="retry.default_max_retries",
        validator=_range_validator(0, 20),
        default=3,
        error_message="retry.default_max_retries 必须在 0-20 之间（0 表示不重试）",
        description="默认最大重试次数，用于 error_handler.py 的 RetryPolicy/with_retry/async_with_retry 装饰器默认值",
    ),

    # ── 9. 认知反思（cognitive） ──
    # 反思引擎的重试硬限制，防止死循环
    ValidationRule(
        path="cognitive.reflection_max_retries",
        validator=_range_validator(1, 10),
        default=3,
        error_message="cognitive.reflection_max_retries 必须在 1-10 之间",
        description="认知反思引擎最大重试次数，用于 reflection.py 的 MAX_RETRIES 硬限制",
    ),

    # ── 10. HTTP 客户端（http） ──
    # HTTP 请求的默认重试次数
    ValidationRule(
        path="http.max_retries",
        validator=_range_validator(0, 10),
        default=3,
        error_message="http.max_retries 必须在 0-10 之间（0 表示不重试）",
        description="HTTP 客户端默认重试次数，用于 http_client.py 的 DEFAULT_MAX_RETRIES",
    ),
    # HTTP 请求的默认超时秒数
    ValidationRule(
        path="http.timeout_sec",
        validator=_range_validator(1, 300),
        default=30,
        error_message="http.timeout_sec 必须在 1-300 秒之间",
        description="HTTP 请求默认超时秒数，用于 http_client.py 的 DEFAULT_TIMEOUT",
    ),
    # HTTP 连接超时秒数
    ValidationRule(
        path="http.connect_timeout_sec",
        validator=_range_validator(1, 60),
        default=10,
        error_message="http.connect_timeout_sec 必须在 1-60 秒之间",
        description="HTTP 连接建立超时秒数，用于 http_client.py 的 DEFAULT_CONNECT_TIMEOUT",
    ),
    # HTTP 连接池大小
    ValidationRule(
        path="http.pool_size",
        validator=_range_validator(1, 100),
        default=20,
        error_message="http.pool_size 必须在 1-100 之间",
        description="HTTP 连接池大小，用于 http_client.py 的 DEFAULT_POOL_SIZE",
    ),

    # ── 11. 缓存容量（cache） ──
    # L1 内存缓存最大条目数
    ValidationRule(
        path="cache.l1_max_size",
        validator=_range_validator(100, 10000),
        default=1000,
        error_message="cache.l1_max_size 必须在 100-10000 之间",
        description="L1 内存缓存最大条目数，用于 multi_level_cache.py 的 MultiLevelCache 默认参数",
    ),

    # ── 12. 追踪缓存容量（tracing_cache） ──
    # 追踪上下文缓存容量
    ValidationRule(
        path="tracing_cache.context_max_size",
        validator=_range_validator(256, 16384),
        default=4096,
        error_message="tracing_cache.context_max_size 必须在 256-16384 之间",
        description="追踪上下文缓存容量，用于 tracing_cache.py 的 TraceContextCache._context_cache",
    ),
    # Span 数据缓存容量
    ValidationRule(
        path="tracing_cache.span_max_size",
        validator=_range_validator(128, 8192),
        default=2048,
        error_message="tracing_cache.span_max_size 必须在 128-8192 之间",
        description="Span 数据缓存容量，用于 tracing_cache.py 的 TraceContextCache._span_cache",
    ),
    # Span 对象池大小
    ValidationRule(
        path="tracing_cache.span_pool_size",
        validator=_range_validator(50, 2000),
        default=500,
        error_message="tracing_cache.span_pool_size 必须在 50-2000 之间",
        description="Span 对象池大小，用于 tracing_cache.py 的 TraceContextCache._span_pool",
    ),

    # ── 13. 调度器常量（scheduler） ──
    # tick 检查间隔（秒）
    ValidationRule(
        path="scheduler.check_interval_sec",
        validator=_range_validator(1, 300),
        default=10,
        error_message="scheduler.check_interval_sec 必须在 1-300 秒之间",
        description="调度器 tick 检查间隔，用于 task_scheduler.py 的 DEFAULT_CHECK_INTERVAL",
    ),
    # 系统命令执行超时（秒）
    ValidationRule(
        path="scheduler.command_timeout_sec",
        validator=_range_validator(10, 3600),
        default=300,
        error_message="scheduler.command_timeout_sec 必须在 10-3600 秒之间",
        description="系统命令执行超时，用于 task_scheduler.py 的 COMMAND_TIMEOUT",
    ),
    # 执行历史最大行数
    ValidationRule(
        path="scheduler.max_history_lines",
        validator=_range_validator(100, 10000),
        default=1000,
        error_message="scheduler.max_history_lines 必须在 100-10000 之间",
        description="执行历史最大行数，用于 task_scheduler.py 的 MAX_HISTORY_LINES",
    ),
    # 心跳间隔（秒）
    ValidationRule(
        path="scheduler.heartbeat_interval_sec",
        validator=_range_validator(10, 600),
        default=60,
        error_message="scheduler.heartbeat_interval_sec 必须在 10-600 秒之间",
        description="心跳检测间隔，用于 task_scheduler.py 的 HEARTBEAT_INTERVAL",
    ),
    # 心跳历史保留条数
    ValidationRule(
        path="scheduler.max_heartbeat_history",
        validator=_range_validator(144, 14400),
        default=1440,
        error_message="scheduler.max_heartbeat_history 必须在 144-14400 之间",
        description="心跳历史保留条数，用于 task_scheduler.py 的 MAX_HEARTBEAT_HISTORY",
    ),

    # ── 14. LLM 监控（llm_monitor） ──
    # 环形缓冲区最大记录数
    ValidationRule(
        path="llm_monitor.max_records",
        validator=_range_validator(100, 5000),
        default=500,
        error_message="llm_monitor.max_records 必须在 100-5000 之间",
        description="LLM 交互记录环形缓冲区大小，用于 llm_monitor.py 的 MAX_RECORDS",
    ),

    # ── 15. Loki 日志推送（loki） ──
    # 推送日志到 Loki 的超时（秒）
    ValidationRule(
        path="loki.push_timeout_sec",
        validator=_range_validator(1, 60),
        default=10,
        error_message="loki.push_timeout_sec 必须在 1-60 秒之间",
        description="Loki push API 超时，用于 monitoring/loki.py 的 _session.post",
    ),
    # 查询 Loki 的超时（秒）
    ValidationRule(
        path="loki.query_timeout_sec",
        validator=_range_validator(1, 120),
        default=30,
        error_message="loki.query_timeout_sec 必须在 1-120 秒之间",
        description="Loki query_range/labels API 超时，用于 monitoring/loki.py 的 _session.get",
    ),

    # ── 16. 告警通知（alert） ──
    # 告警通知超时（秒）
    ValidationRule(
        path="alert.timeout_sec",
        validator=_range_validator(1, 120),
        default=30,
        error_message="alert.timeout_sec 必须在 1-120 秒之间",
        description="告警通知超时，用于 monitoring/alert_notifier.py 的 SMTP/Webhook 请求",
    ),

    # ── 17. Prometheus 指标导出（prometheus） ──
    # Phase 4 Task 2: Prometheus exporter 重试次数配置化
    ValidationRule(
        path="prometheus.max_retries",
        validator=_range_validator(0, 10),
        default=3,
        error_message="prometheus.max_retries 必须在 0-10 之间（0 表示不重试）",
        description="Prometheus 指标导出重试次数，用于 monitoring/prometheus.py 的 RetryPolicy(max_retries=)",
    ),

    # ── 18. 混沌注入器（chaos） ──
    # Phase 4 Task 2: 故障注入器线程清理超时配置化
    ValidationRule(
        path="chaos.thread_join_timeout_sec",
        validator=_range_validator(1, 60),
        default=5,
        error_message="chaos.thread_join_timeout_sec 必须在 1-60 秒之间",
        description="混沌注入器内存压力线程清理超时，用于 monitoring/chaos_injector.py 的 thread.join(timeout=)",
    ),

    # ── 19. 资源监控器线程清理（resource_monitor.thread_join） ──
    # Phase 4 Task 2: 资源监控采样线程清理超时配置化
    ValidationRule(
        path="resource_monitor.thread_join_timeout_sec",
        validator=_range_validator(1, 60),
        default=5,
        error_message="resource_monitor.thread_join_timeout_sec 必须在 1-60 秒之间",
        description="资源监控采样线程清理超时，用于 monitoring/resource_monitor.py 的 _sample_thread.join(timeout=)",
    ),

    # ── 20. 搜索性能监控（search） ──
    # Phase 4 Task 2: 搜索监控线程清理与请求超时配置化
    ValidationRule(
        path="search.thread_join_timeout_sec",
        validator=_range_validator(1, 60),
        default=5,
        error_message="search.thread_join_timeout_sec 必须在 1-60 秒之间",
        description="搜索性能监控线程清理超时，用于 monitoring/search.py 的 _thread.join(timeout=)",
    ),
    ValidationRule(
        path="search.config_apply_timeout_sec",
        validator=_range_validator(1, 60),
        default=10,
        error_message="search.config_apply_timeout_sec 必须在 1-60 秒之间",
        description="搜索配置应用请求超时，用于 monitoring/search.py 的 requests.post(/api/apply-network-config)",
    ),
    ValidationRule(
        path="search.web_search_timeout_sec",
        validator=_range_validator(1, 120),
        default=30,
        error_message="search.web_search_timeout_sec 必须在 1-120 秒之间",
        description="搜索性能检测请求超时，用于 monitoring/search.py 的 requests.get(/api/web/search)",
    ),
    ValidationRule(
        path="search.status_check_timeout_sec",
        validator=_range_validator(1, 60),
        default=10,
        error_message="search.status_check_timeout_sec 必须在 1-60 秒之间",
        description="搜索状态查询请求超时，用于 monitoring/search.py 的 requests.get(/api/web/search/status)",
    ),

    # ── 21. 自愈管理器（self_healer） ──
    # Phase 4 Task 2: 自愈操作各类超时配置化
    ValidationRule(
        path="self_healer.restart_timeout_sec",
        validator=_range_validator(10, 300),
        default=60,
        error_message="self_healer.restart_timeout_sec 必须在 10-300 秒之间",
        description="服务重启命令超时，用于 monitoring/self_healer.py 的 subprocess.run(restart)",
    ),
    ValidationRule(
        path="self_healer.sync_timeout_sec",
        validator=_range_validator(1, 30),
        default=5,
        error_message="self_healer.sync_timeout_sec 必须在 1-30 秒之间",
        description="系统同步命令超时，用于 monitoring/self_healer.py 的 subprocess.run(sync)",
    ),
    ValidationRule(
        path="self_healer.verify_timeout_sec",
        validator=_range_validator(10, 300),
        default=60,
        error_message="self_healer.verify_timeout_sec 必须在 10-300 秒之间",
        description="自愈效果验证超时，用于 monitoring/self_healer.py 的 verify_heal(timeout=)",
    ),
    ValidationRule(
        path="self_healer.thread_join_timeout_sec",
        validator=_range_validator(1, 60),
        default=5,
        error_message="self_healer.thread_join_timeout_sec 必须在 1-60 秒之间",
        description="自愈健康检查线程清理超时，用于 monitoring/self_healer.py 的 _health_check_thread.join(timeout=)",
    ),
]


def _default_config() -> Dict[str, Any]:
    """根据验证规则生成默认配置树"""
    tree: Dict[str, Any] = {}
    for rule in OBSERVABILITY_VALIDATION_RULES:
        parts = rule.path.split(".")
        node = tree
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = rule.default
    return tree


# ============================================================================
# 可观测性配置管理器
# ============================================================================

class ObservabilityConfig:
    """可观测性统一配置管理器

    职责：
    1. 集中管理追踪/日志/指标/健康检查/资源监控配置
    2. 启动时自动验证并修复无效配置项
    3. 支持运行时热修改（get/set），变更原子性（失败回滚）
    4. 提供配置变更回调注册（用于联动其他模块）
    5. 可选监听配置文件变化触发热加载

    线程安全：所有读写均受 self._lock 保护。
    """

    def __init__(self, initial_config: Optional[Dict[str, Any]] = None):
        """初始化可观测性配置管理器

        Args:
            initial_config: 初始配置（None 则使用默认配置）
        """
        self._lock = threading.RLock()
        self._rules: Dict[str, ValidationRule] = {r.path: r for r in OBSERVABILITY_VALIDATION_RULES}
        # 当前生效配置（深拷贝默认配置，避免外部篡改）
        self._config: Dict[str, Any] = _default_config()
        # 配置变更回调列表：[(key_pattern, callback), ...]
        self._callbacks: List[Tuple[str, Callable[[str, Any], None]]] = []
        # 配置变更历史（用于审计与回滚）
        self._change_log: List[Dict[str, Any]] = []
        self._max_change_log = 100

        # 如果提供了初始配置，合并后执行验证修复
        if initial_config:
            self._merge_config(self._config, initial_config)

        # 启动时自动验证并修复所有配置项
        self._validate_and_repair_all()

        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "observability_config",
            "action": "init",
            "duration_ms": 0,
            "config_items": len(self._rules),
            "config": self._safe_log_config(),
        }, ensure_ascii=False))

    # ── 公开 API ──

    def get(self, key: str, default: Any = None) -> Any:
        """读取配置项

        Args:
            key: 点分式配置路径（如 "resource_monitor.sample_interval_sec"）
            default: 键不存在时的返回值

        Returns:
            配置值；不存在则返回 default
        """
        with self._lock:
            parts = key.split(".")
            node = self._config
            for p in parts:
                if not isinstance(node, dict) or p not in node:
                    return default
                node = node[p]
            return node

    def set(self, key: str, value: Any) -> bool:
        """修改配置项（原子性：验证失败则不变更，回滚到原值）

        Args:
            key: 点分式配置路径
            value: 新值

        Returns:
            True 表示修改成功；False 表示验证失败未变更
        """
        start = time.time()
        with self._lock:
            # 1. 验证：若该 key 有对应规则则执行验证
            rule = self._rules.get(key)
            if rule:
                is_valid, repaired = rule.validator(value)
                if not is_valid:
                    # 尝试使用修复后的值
                    if repaired is not None:
                        value = repaired
                    else:
                        # 修复失败，回退到默认值
                        value = rule.default
                # 二次校验修复后的值
                is_valid2, repaired2 = rule.validator(value)
                if not is_valid2:
                    value = rule.default

            # 2. 记录旧值（用于回滚）
            old_value = self._raw_get(key)

            # 3. 写入新值
            self._raw_set(key, value)

            # 4. 二次验证：读取回来再校验一次，确保写入正确
            written = self._raw_get(key)
            if rule and written != value:
                # 写入异常，回滚
                self._raw_set(key, old_value)
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "observability_config",
                    "action": "set_rollback",
                    "duration_ms": int((time.time() - start) * 1000),
                    "key": key,
                    "attempted_value": value,
                    "written_value": written,
                    "error": "写入值与预期不一致，已回滚",
                }, ensure_ascii=False))
                return False

            # 5. 记录变更日志
            change_record = {
                "timestamp": time.time(),
                "key": key,
                "old_value": old_value,
                "new_value": value,
                "duration_ms": int((time.time() - start) * 1000),
            }
            self._change_log.append(change_record)
            if len(self._change_log) > self._max_change_log:
                self._change_log = self._change_log[-self._max_change_log:]

            # 6. 触发回调
            self._fire_callbacks(key, value)

            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "observability_config",
                "action": "set",
                "duration_ms": int((time.time() - start) * 1000),
                "key": key,
                "old_value": old_value,
                "new_value": value,
            }, ensure_ascii=False))
            return True

    def get_all(self) -> Dict[str, Any]:
        """获取全部配置的深拷贝"""
        import copy
        with self._lock:
            return copy.deepcopy(self._config)

    def register_callback(self, key_pattern: str, callback: Callable[[str, Any], None]) -> None:
        """注册配置变更回调

        Args:
            key_pattern: 键前缀匹配（如 "resource_monitor" 匹配该段所有变更）
            callback: 回调函数 (key, new_value) -> None
        """
        with self._lock:
            self._callbacks.append((key_pattern, callback))

    def watch_config_file(self, config_path: str) -> bool:
        """监听配置文件变化触发热加载（复用 ConfigHotReloader）

        Args:
            config_path: 配置文件路径（JSON 或 YAML）

        Returns:
            True 表示监听已注册
        """
        try:
            from agent.disaster_recovery import get_config_reloader
            reloader = get_config_reloader()

            def _on_change(path: str):
                try:
                    new_config = self._load_config_file(path)
                    if new_config:
                        self.reload_from_dict(new_config)
                except Exception as e:
                    logger.error(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "observability_config",
                        "action": "watch_config_file_failed",
                        "duration_ms": 0,
                        "path": path,
                        "error": str(e),
                        "stack_trace": traceback.format_exc(),
                    }, ensure_ascii=False))

            reloader.watch_config(config_path, _on_change)
            reloader.start()
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "observability_config",
                "action": "watch_config_file",
                "duration_ms": 0,
                "path": config_path,
            }, ensure_ascii=False))
            return True
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "observability_config",
                "action": "watch_config_file_register_failed",
                "duration_ms": 0,
                "path": config_path,
                "error": str(e),
            }, ensure_ascii=False))
            return False

    def reload_from_dict(self, new_config: Dict[str, Any]) -> bool:
        """从字典批量重载配置（原子性：验证失败回滚到旧配置）

        Args:
            new_config: 新配置字典

        Returns:
            True 表示重载成功；False 表示验证失败已回滚
        """
        start = time.time()
        with self._lock:
            # 备份当前配置用于回滚
            import copy
            old_config = copy.deepcopy(self._config)

            # 合并新配置到当前配置
            merged = copy.deepcopy(old_config)
            self._merge_config(merged, new_config)

            # 验证合并后的配置
            is_valid, failed_keys = self._validate_all(merged)
            if not is_valid:
                # 验证失败：尝试逐项修复
                repaired = self._repair_all(merged)
                if repaired != len(failed_keys):
                    # 仍有无法修复的项，回滚
                    logger.warning(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "observability_config",
                        "action": "reload_rollback",
                        "duration_ms": int((time.time() - start) * 1000),
                        "failed_keys": failed_keys,
                        "error": "配置重载验证失败，已回滚",
                    }, ensure_ascii=False))
                    self._config = old_config
                    return False

            # 提交新配置
            self._config = merged

            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "observability_config",
                "action": "reload_from_dict",
                "duration_ms": int((time.time() - start) * 1000),
                "repaired_keys": failed_keys,
            }, ensure_ascii=False))
            return True

    def get_change_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取配置变更历史"""
        with self._lock:
            return list(self._change_log[-limit:])

    def get_validation_rules(self) -> List[Dict[str, Any]]:
        """获取所有验证规则（用于文档生成与自检）"""
        with self._lock:
            return [
                {
                    "path": r.path,
                    "default": r.default,
                    "error_message": r.error_message,
                    "description": r.description,
                }
                for r in self._rules.values()
            ]

    # ── 内部实现 ──

    def _raw_get(self, key: str) -> Any:
        """直接读取（不加验证，调用方需持锁）"""
        parts = key.split(".")
        node = self._config
        for p in parts:
            if not isinstance(node, dict) or p not in node:
                return None
            node = node[p]
        return node

    def _raw_set(self, key: str, value: Any) -> None:
        """直接写入（调用方需持锁）"""
        parts = key.split(".")
        node = self._config
        for p in parts[:-1]:
            if p not in node or not isinstance(node[p], dict):
                node[p] = {}
            node = node[p]
        node[parts[-1]] = value

    def _merge_config(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """递归合并 source 到 target"""
        for k, v in source.items():
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                self._merge_config(target[k], v)
            else:
                target[k] = v

    def _validate_all(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """验证整个配置树，返回 (是否全部合法, 失败键列表)"""
        failed = []
        for path, rule in self._rules.items():
            value = self._get_from_tree(config, path)
            is_valid, _ = rule.validator(value)
            if not is_valid:
                failed.append(path)
        return len(failed) == 0, failed

    def _repair_all(self, config: Dict[str, Any]) -> int:
        """修复配置树中所有无效项，返回修复数量"""
        repaired_count = 0
        for path, rule in self._rules.items():
            value = self._get_from_tree(config, path)
            is_valid, repaired = rule.validator(value)
            if not is_valid:
                final = repaired if repaired is not None else rule.default
                self._set_to_tree(config, path, final)
                repaired_count += 1
        return repaired_count

    def _validate_and_repair_all(self) -> None:
        """启动时验证并修复所有配置项"""
        repaired = self._repair_all(self._config)
        if repaired > 0:
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "observability_config",
                "action": "startup_repair",
                "duration_ms": 0,
                "repaired_count": repaired,
            }, ensure_ascii=False))

    @staticmethod
    def _get_from_tree(tree: Dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        node = tree
        for p in parts:
            if not isinstance(node, dict) or p not in node:
                return None
            node = node[p]
        return node

    @staticmethod
    def _set_to_tree(tree: Dict[str, Any], path: str, value: Any) -> None:
        parts = path.split(".")
        node = tree
        for p in parts[:-1]:
            if p not in node or not isinstance(node[p], dict):
                node[p] = {}
            node = node[p]
        node[parts[-1]] = value

    def _fire_callbacks(self, key: str, value: Any) -> None:
        """触发匹配的回调（异常隔离，不影响配置提交）"""
        for pattern, callback in list(self._callbacks):
            if key == pattern or key.startswith(pattern + "."):
                try:
                    callback(key, value)
                except Exception as e:
                    logger.warning(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "observability_config",
                        "action": "callback_failed",
                        "duration_ms": 0,
                        "key": key,
                        "pattern": pattern,
                        "error": str(e),
                    }, ensure_ascii=False))

    @staticmethod
    def _load_config_file(path: str) -> Optional[Dict[str, Any]]:
        """加载 JSON/YAML 配置文件"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if path.endswith((".json",)):
                return json.loads(content)
            if path.endswith((".yaml", ".yml")):
                try:
                    import yaml
                    return yaml.safe_load(content)
                except ImportError:
                    logger.warning("yaml 模块未安装，无法解析 YAML 配置文件")
                    return None
            # 默认尝试 JSON
            return json.loads(content)
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "observability_config",
                "action": "load_config_file_failed",
                "duration_ms": 0,
                "path": path,
                "error": str(e),
            }, ensure_ascii=False))
            return None

    def _safe_log_config(self) -> Dict[str, Any]:
        """返回用于日志的安全配置（过滤敏感信息，本模块无敏感项）"""
        import copy
        return copy.deepcopy(self._config)


# ============================================================================
# 向后兼容：TracingConfig 委托层
# ============================================================================

class _TracingConfigCompat:
    """向后兼容层：保留原 TracingConfig 接口，内部委托到 ObservabilityConfig

    现有代码 `from agent.monitoring.tracing_config import tracing_config` 无需修改即可工作。

    配置优先级（与原 TracingConfig 行为一致）：
        TRACING_* 环境变量 > ObservabilityConfig 热配置 > 内置默认值

    说明：原 TracingConfig 直接读取环境变量，此处保留该行为以确保向后兼容；
    同时将基础值委托到 ObservabilityConfig，使运行时热修改（config.set）能够生效。
    环境变量始终具备最高优先级，符合 12-Factor App 的配置原则。
    """

    def __init__(self, obs_config: ObservabilityConfig):
        self._obs = obs_config

    @staticmethod
    def _env_str(key: str, upper: bool = False) -> Optional[str]:
        """读取字符串型环境变量，未设置返回 None"""
        val = os.getenv(key)
        if val is None or val == "":
            return None
        return val.upper() if upper else val

    @staticmethod
    def _env_float(key: str) -> Optional[float]:
        """读取浮点型环境变量，未设置或非法返回 None"""
        val = os.getenv(key)
        if not val:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _env_int(key: str) -> Optional[int]:
        """读取整型环境变量，未设置或非法返回 None"""
        val = os.getenv(key)
        if not val:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    @property
    def env(self) -> str:
        # 环境变量优先（保持原 TracingConfig 行为）
        env_val = self._env_str("TRACING_ENV")
        if env_val:
            return env_val
        return self._obs.get("tracing.env", "development")

    @property
    def log_level(self) -> str:
        env_val = self._env_str("TRACING_LOG_LEVEL", upper=True)
        if env_val:
            return env_val
        return self._obs.get("tracing.log_level", "INFO")

    @property
    def sampler_type(self) -> str:
        # 环境变量优先
        env_val = self._env_str("TRACING_SAMPLER", upper=True)
        if env_val:
            return env_val
        # 兼容原值（原 TracingConfig 中根据 env 推导）
        env = self.env
        if env == "development":
            return "ALWAYS_ON"
        if env == "staging":
            return "ALWAYS_ON"
        if env == "production":
            return "PARENT_BASED_RATIO"
        return "ALWAYS_ON"

    @property
    def sampler_ratio(self) -> float:
        env_val = self._env_float("TRACING_SAMPLER_RATIO")
        if env_val is not None:
            return env_val
        return self._obs.get("tracing.sampler_ratio", 0.1)

    @property
    def sampler_rate_limit(self) -> int:
        # 原 TracingConfig 支持 TRACING_SAMPLER_RATE_LIMIT，默认 100
        env_val = self._env_int("TRACING_SAMPLER_RATE_LIMIT")
        if env_val is not None:
            return env_val
        return 100

    @property
    def exporter_type(self) -> str:
        env_val = self._env_str("TRACING_EXPORTER", upper=True)
        if env_val:
            return env_val
        env = self.env
        if env == "development":
            return "CONSOLE"
        return "OTLP"

    @property
    def exporter_endpoint(self) -> str:
        env_val = self._env_str("TRACING_EXPORTER_ENDPOINT")
        if env_val is not None:
            return env_val
        env = self.env
        if env in ("staging", "production"):
            return "localhost:4317"
        return ""

    @property
    def exporter_protocol(self) -> str:
        env_val = self._env_str("TRACING_EXPORTER_PROTOCOL", upper=True)
        if env_val:
            return env_val
        return "GRPC"

    @property
    def data_retention_days(self) -> int:
        env_val = self._env_int("TRACING_DATA_RETENTION_DAYS")
        if env_val is not None:
            return env_val
        env = self.env
        if env == "development":
            return 7
        if env == "staging":
            return 14
        if env == "production":
            return 30
        return 7

    @property
    def debug_mode(self) -> bool:
        return self.env == "development"

    def get_logging_level(self) -> int:
        import logging
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARN": logging.WARN,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return level_map.get(self.log_level, logging.INFO)

    def is_debug_enabled(self) -> bool:
        return self.debug_mode or self.log_level == "DEBUG"

    def get_config_dict(self) -> Dict[str, Any]:
        return {
            "env": self.env,
            "log_level": self.log_level,
            "sampler_type": self.sampler_type,
            "sampler_ratio": self.sampler_ratio,
            "sampler_rate_limit": self.sampler_rate_limit,
            "exporter_type": self.exporter_type,
            "exporter_endpoint": self.exporter_endpoint,
            "exporter_protocol": self.exporter_protocol,
            "data_retention_days": self.data_retention_days,
            "debug_mode": self.debug_mode,
        }

    def __repr__(self):
        return (
            f"TracingConfig(env={self.env!r}, log_level={self.log_level!r}, "
            f"sampler_type={self.sampler_type!r}, sampler_ratio={self.sampler_ratio!r}, "
            f"exporter_type={self.exporter_type!r}, exporter_endpoint={self.exporter_endpoint!r})"
        )


# ============================================================================
# 全局实例与访问函数
# ============================================================================

_global_observability_config: Optional[ObservabilityConfig] = None
_global_config_lock = threading.Lock()


def get_observability_config() -> ObservabilityConfig:
    """获取全局可观测性配置实例（惰性初始化，线程安全）"""
    global _global_observability_config
    if _global_observability_config is None:
        with _global_config_lock:
            if _global_observability_config is None:
                _global_observability_config = ObservabilityConfig()
    return _global_observability_config


def get_max_analyze_days() -> int:
    """读取时间窗口分析上限天数（便捷函数，支持热加载）

    Returns:
        最大分析天数，默认 36500（100 年）
    """
    try:
        return int(get_observability_config().get("time_window.max_analyze_days", default=36500))
    except Exception:
        return 36500


def get_default_max_retries() -> int:
    """读取默认最大重试次数（便捷函数，支持热加载）

    Returns:
        最大重试次数，默认 3
    """
    try:
        return int(get_observability_config().get("retry.default_max_retries", default=3))
    except Exception:
        return 3


def get_reflection_max_retries() -> int:
    """读取认知反思引擎最大重试次数（便捷函数，支持热加载）

    Returns:
        最大重试次数，默认 3
    """
    try:
        return int(get_observability_config().get("cognitive.reflection_max_retries", default=3))
    except Exception:
        return 3


def get_http_max_retries() -> int:
    """读取 HTTP 客户端默认重试次数（便捷函数，支持热加载）

    Returns:
        最大重试次数，默认 3
    """
    try:
        return int(get_observability_config().get("http.max_retries", default=3))
    except Exception:
        return 3


def get_http_timeout() -> int:
    """读取 HTTP 请求默认超时秒数（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 30
    """
    try:
        return int(get_observability_config().get("http.timeout_sec", default=30))
    except Exception:
        return 30


def get_http_connect_timeout() -> int:
    """读取 HTTP 连接建立超时秒数（便捷函数，支持热加载）

    Returns:
        连接超时秒数，默认 10
    """
    try:
        return int(get_observability_config().get("http.connect_timeout_sec", default=10))
    except Exception:
        return 10


def get_http_pool_size() -> int:
    """读取 HTTP 连接池大小（便捷函数，支持热加载）

    Returns:
        连接池大小，默认 20
    """
    try:
        return int(get_observability_config().get("http.pool_size", default=20))
    except Exception:
        return 20


# ── 缓存容量便捷函数 ──

def get_cache_l1_max_size() -> int:
    """读取 L1 内存缓存最大条目数（便捷函数，支持热加载）

    Returns:
        最大条目数，默认 1000
    """
    try:
        return int(get_observability_config().get("cache.l1_max_size", default=1000))
    except Exception:
        return 1000


def get_tracing_cache_context_max_size() -> int:
    """读取追踪上下文缓存容量（便捷函数，支持热加载）

    Returns:
        缓存容量，默认 4096
    """
    try:
        return int(get_observability_config().get("tracing_cache.context_max_size", default=4096))
    except Exception:
        return 4096


def get_tracing_cache_span_max_size() -> int:
    """读取 Span 数据缓存容量（便捷函数，支持热加载）

    Returns:
        缓存容量，默认 2048
    """
    try:
        return int(get_observability_config().get("tracing_cache.span_max_size", default=2048))
    except Exception:
        return 2048


def get_tracing_cache_span_pool_size() -> int:
    """读取 Span 对象池大小（便捷函数，支持热加载）

    Returns:
        对象池大小，默认 500
    """
    try:
        return int(get_observability_config().get("tracing_cache.span_pool_size", default=500))
    except Exception:
        return 500


# ── 调度器常量便捷函数 ──

def get_scheduler_check_interval() -> int:
    """读取调度器 tick 检查间隔（便捷函数，支持热加载）

    Returns:
        检查间隔秒数，默认 10
    """
    try:
        return int(get_observability_config().get("scheduler.check_interval_sec", default=10))
    except Exception:
        return 10


def get_scheduler_command_timeout() -> int:
    """读取系统命令执行超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 300
    """
    try:
        return int(get_observability_config().get("scheduler.command_timeout_sec", default=300))
    except Exception:
        return 300


def get_scheduler_max_history_lines() -> int:
    """读取执行历史最大行数（便捷函数，支持热加载）

    Returns:
        最大行数，默认 1000
    """
    try:
        return int(get_observability_config().get("scheduler.max_history_lines", default=1000))
    except Exception:
        return 1000


def get_scheduler_heartbeat_interval() -> int:
    """读取心跳检测间隔（便捷函数，支持热加载）

    Returns:
        间隔秒数，默认 60
    """
    try:
        return int(get_observability_config().get("scheduler.heartbeat_interval_sec", default=60))
    except Exception:
        return 60


def get_scheduler_max_heartbeat_history() -> int:
    """读取心跳历史保留条数（便捷函数，支持热加载）

    Returns:
        保留条数，默认 1440
    """
    try:
        return int(get_observability_config().get("scheduler.max_heartbeat_history", default=1440))
    except Exception:
        return 1440


# ── LLM 监控便捷函数 ──

def get_llm_monitor_max_records() -> int:
    """读取 LLM 交互记录环形缓冲区大小（便捷函数，支持热加载）

    Returns:
        最大记录数，默认 500
    """
    try:
        return int(get_observability_config().get("llm_monitor.max_records", default=500))
    except Exception:
        return 500


# ── Loki 日志推送便捷函数 ──

def get_loki_push_timeout() -> int:
    """读取 Loki push API 超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 10
    """
    try:
        return int(get_observability_config().get("loki.push_timeout_sec", default=10))
    except Exception:
        return 10


def get_loki_query_timeout() -> int:
    """读取 Loki query API 超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 30
    """
    try:
        return int(get_observability_config().get("loki.query_timeout_sec", default=30))
    except Exception:
        return 30


# ── 告警通知便捷函数 ──

def get_alert_timeout() -> int:
    """读取告警通知超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 30
    """
    try:
        return int(get_observability_config().get("alert.timeout_sec", default=30))
    except Exception:
        return 30


# ── Prometheus 指标导出便捷函数 ──

def get_prometheus_max_retries() -> int:
    """读取 Prometheus 指标导出重试次数（便捷函数，支持热加载）

    Returns:
        最大重试次数，默认 3
    """
    try:
        return int(get_observability_config().get("prometheus.max_retries", default=3))
    except Exception:
        return 3


# ── 混沌注入器便捷函数 ──

def get_chaos_thread_join_timeout() -> int:
    """读取混沌注入器线程清理超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 5
    """
    try:
        return int(get_observability_config().get("chaos.thread_join_timeout_sec", default=5))
    except Exception:
        return 5


# ── 资源监控器线程清理便捷函数 ──

def get_resource_monitor_thread_join_timeout() -> int:
    """读取资源监控采样线程清理超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 5
    """
    try:
        return int(get_observability_config().get("resource_monitor.thread_join_timeout_sec", default=5))
    except Exception:
        return 5


# ── 搜索性能监控便捷函数 ──

def get_search_thread_join_timeout() -> int:
    """读取搜索监控线程清理超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 5
    """
    try:
        return int(get_observability_config().get("search.thread_join_timeout_sec", default=5))
    except Exception:
        return 5


def get_search_config_apply_timeout() -> int:
    """读取搜索配置应用请求超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 10
    """
    try:
        return int(get_observability_config().get("search.config_apply_timeout_sec", default=10))
    except Exception:
        return 10


def get_search_web_search_timeout() -> int:
    """读取搜索性能检测请求超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 30
    """
    try:
        return int(get_observability_config().get("search.web_search_timeout_sec", default=30))
    except Exception:
        return 30


def get_search_status_check_timeout() -> int:
    """读取搜索状态查询请求超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 10
    """
    try:
        return int(get_observability_config().get("search.status_check_timeout_sec", default=10))
    except Exception:
        return 10


# ── 自愈管理器便捷函数 ──

def get_self_healer_restart_timeout() -> int:
    """读取服务重启命令超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 60
    """
    try:
        return int(get_observability_config().get("self_healer.restart_timeout_sec", default=60))
    except Exception:
        return 60


def get_self_healer_sync_timeout() -> int:
    """读取系统同步命令超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 5
    """
    try:
        return int(get_observability_config().get("self_healer.sync_timeout_sec", default=5))
    except Exception:
        return 5


def get_self_healer_verify_timeout() -> int:
    """读取自愈效果验证超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 60
    """
    try:
        return int(get_observability_config().get("self_healer.verify_timeout_sec", default=60))
    except Exception:
        return 60


def get_self_healer_thread_join_timeout() -> int:
    """读取自愈健康检查线程清理超时（便捷函数，支持热加载）

    Returns:
        超时秒数，默认 5
    """
    try:
        return int(get_observability_config().get("self_healer.thread_join_timeout_sec", default=5))
    except Exception:
        return 5


def reset_observability_config() -> None:
    """重置全局实例（仅用于测试）"""
    global _global_observability_config
    with _global_config_lock:
        _global_observability_config = None


def get_tracing_config_compat() -> _TracingConfigCompat:
    """获取向后兼容的 TracingConfig 实例（委托到 ObservabilityConfig）"""
    return _TracingConfigCompat(get_observability_config())
