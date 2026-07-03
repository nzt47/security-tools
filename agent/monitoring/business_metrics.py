"""业务指标定义模块 — BusinessMetrics

定义云枢智能代理的核心业务指标，用于衡量业务价值。

指标分类：
1. 用户交互指标 - 对话次数、工具调用次数、消息类型分布
2. 任务完成指标 - 规划任务完成率、异步任务成功率、任务耗时分布
3. 知识库指标 - 记忆搜索命中率、向量查询命中率、记忆访问频率
4. 扩展使用指标 - 技能安装次数、MCP连接次数、扩展启用率

设计文档：P3 可观测性建设 — Business Metrics Layer

重构说明:
- 使用 utils.calculate_percentiles 消除重复的百分位计算代码
- 使用 utils.make_label_key / parse_label_key 统一标签键处理
"""

import logging
import json
import uuid
import time
import threading
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone

from agent.monitoring.utils import calculate_percentiles, make_label_key, parse_label_key
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



@dataclass
class BusinessMetricDefinition:
    """业务指标定义
    
    Attributes:
        name: 指标名称（如 yunshu_interaction_total）
        description: 指标描述
        metric_type: 指标类型（counter/gauge/histogram）
        labels: 标签列表（如 ['interaction_type', 'model']）
        unit: 单位（如 '次', '秒', '%'）
        category: 指标分类（interaction/task/knowledge/extension）
        business_value: 业务价值说明
        aggregation: 聚合方式（sum/avg/max/min）
        retention_days: 数据保留天数
    """
    name: str
    description: str
    metric_type: str  # counter, gauge, histogram
    labels: List[str] = field(default_factory=list)
    unit: str = "次"
    category: str = "business"
    business_value: str = ""
    aggregation: str = "sum"
    retention_days: int = 30


# ============================================================================
# 业务指标定义表
# ============================================================================

BUSINESS_METRICS_DEFINITIONS = {
    # ── 1. 用户交互指标 ──
    "yunshu_interaction_total": BusinessMetricDefinition(
        name="yunshu_interaction_total",
        description="用户交互总次数（对话、工具调用等）",
        metric_type="counter",
        labels=["interaction_type", "model", "success"],
        unit="次",
        category="interaction",
        business_value="衡量用户活跃度和系统使用频率",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_interaction_duration_seconds": BusinessMetricDefinition(
        name="yunshu_interaction_duration_seconds",
        description="交互处理耗时分布",
        metric_type="histogram",
        labels=["interaction_type", "model"],
        unit="秒",
        category="interaction",
        business_value="衡量响应速度和用户体验",
        aggregation="avg",
        retention_days=7,
    ),
    "yunshu_message_type_total": BusinessMetricDefinition(
        name="yunshu_message_type_total",
        description="消息类型分布统计（简单问候、复杂任务、追问等）",
        metric_type="counter",
        labels=["message_type", "intent"],
        unit="次",
        category="interaction",
        business_value="了解用户意图分布，优化对话策略",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_tool_call_total": BusinessMetricDefinition(
        name="yunshu_tool_call_total",
        description="工具调用总次数",
        metric_type="counter",
        labels=["tool_name", "tool_category", "success"],
        unit="次",
        category="interaction",
        business_value="衡量工具使用频率，识别高频工具",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_tool_call_duration_seconds": BusinessMetricDefinition(
        name="yunshu_tool_call_duration_seconds",
        description="工具调用耗时分布",
        metric_type="histogram",
        labels=["tool_name", "tool_category"],
        unit="秒",
        category="interaction",
        business_value="识别慢工具，优化工具性能",
        aggregation="avg",
        retention_days=7,
    ),

    # ── 2. 任务完成指标 ──
    "yunshu_task_completion_rate": BusinessMetricDefinition(
        name="yunshu_task_completion_rate",
        description="任务完成率（规划任务、异步任务等）",
        metric_type="gauge",
        labels=["task_type", "complexity"],
        unit="%",
        category="task",
        business_value="衡量任务执行成功率，识别失败模式",
        aggregation="avg",
        retention_days=30,
    ),
    "yunshu_task_total": BusinessMetricDefinition(
        name="yunshu_task_total",
        description="任务执行总次数",
        metric_type="counter",
        labels=["task_type", "complexity", "status"],
        unit="次",
        category="task",
        business_value="统计任务执行量，分析任务分布",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_task_duration_seconds": BusinessMetricDefinition(
        name="yunshu_task_duration_seconds",
        description="任务执行耗时分布",
        metric_type="histogram",
        labels=["task_type", "complexity"],
        unit="秒",
        category="task",
        business_value="识别耗时任务，优化任务调度",
        aggregation="avg",
        retention_days=7,
    ),
    "yunshu_planning_task_success": BusinessMetricDefinition(
        name="yunshu_planning_task_success",
        description="规划任务成功次数",
        metric_type="counter",
        labels=["planner_type", "steps_count"],
        unit="次",
        category="task",
        business_value="衡量规划引擎成功率",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_async_task_success": BusinessMetricDefinition(
        name="yunshu_async_task_success",
        description="异步任务成功次数",
        metric_type="counter",
        labels=["async_type", "queue_name"],
        unit="次",
        category="task",
        business_value="衡量异步任务执行成功率",
        aggregation="sum",
        retention_days=30,
    ),

    # ── 3. 知识库指标 ──
    "yunshu_memory_search_hit_rate": BusinessMetricDefinition(
        name="yunshu_memory_search_hit_rate",
        description="记忆搜索命中率",
        metric_type="gauge",
        labels=["memory_type", "search_method"],
        unit="%",
        category="knowledge",
        business_value="衡量记忆检索效率，优化记忆策略",
        aggregation="avg",
        retention_days=30,
    ),
    "yunshu_memory_search_total": BusinessMetricDefinition(
        name="yunshu_memory_search_total",
        description="记忆搜索总次数",
        metric_type="counter",
        labels=["memory_type", "search_method", "hit"],
        unit="次",
        category="knowledge",
        business_value="统计记忆检索频率",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_memory_access_total": BusinessMetricDefinition(
        name="yunshu_memory_access_total",
        description="记忆访问次数统计",
        metric_type="counter",
        labels=["memory_key", "importance"],
        unit="次",
        category="knowledge",
        business_value="识别高频访问记忆，优化记忆缓存",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_memory_storage_total": BusinessMetricDefinition(
        name="yunshu_memory_storage_total",
        description="记忆存储总次数",
        metric_type="counter",
        labels=["memory_type", "importance", "success"],
        unit="次",
        category="knowledge",
        business_value="统计记忆写入频率与成功率",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_vector_query_hit_rate": BusinessMetricDefinition(
        name="yunshu_vector_query_hit_rate",
        description="向量查询命中率",
        metric_type="gauge",
        labels=["vector_store", "query_type"],
        unit="%",
        category="knowledge",
        business_value="衡量向量检索效率",
        aggregation="avg",
        retention_days=30,
    ),
    "yunshu_memory_compression_total": BusinessMetricDefinition(
        name="yunshu_memory_compression_total",
        description="记忆压缩总次数",
        metric_type="counter",
        labels=["compression_type", "success"],
        unit="次",
        category="knowledge",
        business_value="统计记忆压缩频率，优化压缩策略",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_memory_deletion_total": BusinessMetricDefinition(
        name="yunshu_memory_deletion_total",
        description="记忆删除总次数",
        metric_type="counter",
        labels=["memory_type", "success"],
        unit="次",
        category="knowledge",
        business_value="统计记忆删除频率，评估记忆清理策略",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_memory_operation_duration_seconds": BusinessMetricDefinition(
        name="yunshu_memory_operation_duration_seconds",
        description="记忆操作耗时分布",
        metric_type="histogram",
        labels=["operation_type", "memory_type"],
        unit="秒",
        category="knowledge",
        business_value="识别慢记忆操作，优化记忆性能",
        aggregation="avg",
        retention_days=7,
    ),

    # ── 4. 扩展使用指标 ──
    "yunshu_extension_install_total": BusinessMetricDefinition(
        name="yunshu_extension_install_total",
        description="扩展安装总次数",
        metric_type="counter",
        labels=["extension_type", "source", "success"],
        unit="次",
        category="extension",
        business_value="衡量扩展获取频率，识别热门扩展",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_extension_uninstall_total": BusinessMetricDefinition(
        name="yunshu_extension_uninstall_total",
        description="扩展卸载总次数",
        metric_type="counter",
        labels=["extension_type", "extension_id"],
        unit="次",
        category="extension",
        business_value="统计扩展移除频率，识别不常用扩展",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_extension_enabled_count": BusinessMetricDefinition(
        name="yunshu_extension_enabled_count",
        description="已启用扩展数量",
        metric_type="gauge",
        labels=["extension_type"],
        unit="个",
        category="extension",
        business_value="衡量扩展活跃度",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_mcp_connection_total": BusinessMetricDefinition(
        name="yunshu_mcp_connection_total",
        description="MCP 连接总次数",
        metric_type="counter",
        labels=["transport_type", "service_id", "success"],
        unit="次",
        category="extension",
        business_value="衡量 MCP 服务使用频率",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_mcp_active_connection_count": BusinessMetricDefinition(
        name="yunshu_mcp_active_connection_count",
        description="活跃 MCP 连接数",
        metric_type="gauge",
        labels=["transport_type"],
        unit="个",
        category="extension",
        business_value="衡量 MCP 服务活跃度",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_skill_usage_total": BusinessMetricDefinition(
        name="yunshu_skill_usage_total",
        description="技能使用总次数",
        metric_type="counter",
        labels=["skill_id", "skill_category", "success"],
        unit="次",
        category="extension",
        business_value="衡量技能使用频率，识别热门技能",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_market_search_total": BusinessMetricDefinition(
        name="yunshu_market_search_total",
        description="扩展市场搜索总次数",
        metric_type="counter",
        labels=["query_category", "result_count"],
        unit="次",
        category="extension",
        business_value="衡量市场使用频率，识别热门搜索",
        aggregation="sum",
        retention_days=30,
    ),

    # ── 4.5 模型路由指标 ──
    "yunshu_model_call_total": BusinessMetricDefinition(
        name="yunshu_model_call_total",
        description="模型调用总次数",
        metric_type="counter",
        labels=["model_name", "provider", "success"],
        unit="次",
        category="model_router",
        business_value="统计模型使用频率，评估模型选择策略",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_model_call_duration_seconds": BusinessMetricDefinition(
        name="yunshu_model_call_duration_seconds",
        description="模型调用耗时分布",
        metric_type="histogram",
        labels=["model_name", "provider"],
        unit="秒",
        category="model_router",
        business_value="识别慢模型，优化模型选择和超时设置",
        aggregation="avg",
        retention_days=7,
    ),
    "yunshu_model_switch_total": BusinessMetricDefinition(
        name="yunshu_model_switch_total",
        description="模型切换总次数",
        metric_type="counter",
        labels=["from_model", "to_model", "reason"],
        unit="次",
        category="model_router",
        business_value="统计模型切换频率，评估模型容灾策略",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_model_success_rate": BusinessMetricDefinition(
        name="yunshu_model_success_rate",
        description="模型调用成功率",
        metric_type="gauge",
        labels=["model_name", "provider"],
        unit="%",
        category="model_router",
        business_value="实时监控各模型的成功率",
        aggregation="avg",
        retention_days=7,
    ),

    # ── 5. 稳定性指标 ──
    "yunshu_circuit_breaker_trigger_total": BusinessMetricDefinition(
        name="yunshu_circuit_breaker_trigger_total",
        description="熔断器触发总次数",
        metric_type="counter",
        labels=["breaker_name", "from_state", "to_state", "reason"],
        unit="次",
        category="stability",
        business_value="衡量系统故障隔离能力，识别频繁熔断的组件",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_circuit_breaker_state": BusinessMetricDefinition(
        name="yunshu_circuit_breaker_state",
        description="熔断器当前状态",
        metric_type="gauge",
        labels=["breaker_name", "state"],
        unit="",
        category="stability",
        business_value="实时监控熔断器状态",
        aggregation="last",
        retention_days=7,
    ),
    "yunshu_rate_limit_trigger_total": BusinessMetricDefinition(
        name="yunshu_rate_limit_trigger_total",
        description="限流触发总次数",
        metric_type="counter",
        labels=["level", "endpoint", "user_id", "reason"],
        unit="次",
        category="stability",
        business_value="衡量系统流量控制效果，识别高频限流点",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_degrade_trigger_total": BusinessMetricDefinition(
        name="yunshu_degrade_trigger_total",
        description="降级触发总次数",
        metric_type="counter",
        labels=["module", "level", "reason"],
        unit="次",
        category="stability",
        business_value="衡量系统容错能力，识别频繁降级的模块",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_disaster_recovery_total": BusinessMetricDefinition(
        name="yunshu_disaster_recovery_total",
        description="容灾恢复总次数",
        metric_type="counter",
        labels=["recovery_type", "status", "backup_id"],
        unit="次",
        category="stability",
        business_value="衡量系统故障恢复能力，评估容灾策略有效性",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_backup_total": BusinessMetricDefinition(
        name="yunshu_backup_total",
        description="备份总次数",
        metric_type="counter",
        labels=["backup_type", "success"],
        unit="次",
        category="stability",
        business_value="衡量数据备份频率，评估备份策略有效性",
        aggregation="sum",
        retention_days=30,
    ),
}

# ============================================================================
# 业务指标收集器
# ============================================================================

class BusinessMetricsCollector:
    """业务指标收集器
    
    负责收集、存储和查询业务指标数据。
    支持：
    - 指标计数（Counter）
    - 指标观测（Histogram）
    - 指标设置（Gauge）
    - 时间范围查询
    - 维度分组统计
    
    使用示例：
        collector = BusinessMetricsCollector()
        
        # 记录交互
        collector.record_interaction("chat", "gpt-4", success=True, duration=1.5)
        
        # 记录工具调用
        collector.record_tool_call("read_file", "file", success=True, duration=0.3)
        
        # 记录任务完成
        collector.record_task("planning", "complex", status="success", duration=10.0)
        
        # 记录记忆搜索
        collector.record_memory_search("long_term", "keyword", hit=True)
        
        # 记录扩展安装
        collector.record_extension_install("skill", "github", success=True)
        
        # 记录熔断器触发
        collector.record_circuit_breaker_trigger("tool_calling", "closed", "open", "high_error_rate")
        
        # 记录限流触发
        collector.record_rate_limit_trigger("global", "/api/chat", "user123", "rate_limit_exceeded")
        
        # 记录降级触发
        collector.record_degrade_trigger("schema", "text_only", "validation_failed")
        
        # 记录容灾恢复
        collector.record_disaster_recovery("auto", "completed", "backup_20240101_120000")
        
        # 获取仪表盘数据
        dashboard = collector.get_dashboard_data()
    """
    
    def __init__(self, storage_path: Optional[str] = None):
        """初始化业务指标收集器
        
        Args:
            storage_path: 数据存储路径（可选，默认内存存储）
        """
        self._storage_path = storage_path
        self._lock = threading.Lock()
        
        # 内存存储
        self._counters: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._gauges: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._histograms: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        
        # 时间戳记录（用于时间范围查询）- 按标签键存储
        self._timestamps: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        
        logger.info(log_dict({'module_name': 'business_metrics', 'action': 'log', 'msg': '[BusinessMetrics] 业务指标收集器已初始化'}))
    
    def record_interaction(
        self,
        interaction_type: str,
        model: str,
        success: bool = True,
        duration: Optional[float] = None,
    ):
        """记录用户交互（埋点失败隔离：内部异常不影响主流程）
        
        Args:
            interaction_type: 交互类型（chat/tool_call/planning等）
            model: 使用的模型名称
            success: 是否成功
            duration: 耗时（秒）
        """
        labels = {
            "interaction_type": interaction_type,
            "model": model,
            "success": str(success),
        }
        
        # 增加计数器（埋点失败隔离）
        try:
            self._increment_counter("yunshu_interaction_total", labels)
        except Exception as e:
            logger.warning(log_dict({'module_name': 'business_metrics', 'action': 'log', 'msg': f'[BusinessMetrics] 记录交互计数器失败: {e}'}))
        
        # 记录耗时（埋点失败隔离）
        if duration is not None:
            try:
                self._observe_histogram("yunshu_interaction_duration_seconds", labels, duration)
            except Exception as e:
                logger.warning(log_dict({'module_name': 'business_metrics', 'action': 'log', 'msg': f'[BusinessMetrics] 记录交互耗时失败: {e}'}))
        
        logger.debug(log_dict({'module_name': 'business_metrics', 'action': 'type.interaction_type.model', 'msg': f'[BusinessMetrics] 交互记录: type={interaction_type}, model={model}, success={success}, duration={duration}'}))
    
    def record_message_type(self, message_type: str, intent: str):
        """记录消息类型分布
        
        Args:
            message_type: 消息类型（simple_query/complex_task/follow_up等）
            intent: 意图（greeting/request/question等）
        """
        labels = {
            "message_type": message_type,
            "intent": intent,
        }
        self._increment_counter("yunshu_message_type_total", labels)
    
    def record_tool_call(
        self,
        tool_name: str,
        tool_category: str,
        success: bool = True,
        duration: Optional[float] = None,
    ):
        """记录工具调用
        
        Args:
            tool_name: 工具名称
            tool_category: 工具分类（file/web/system等）
            success: 是否成功
            duration: 耗时（秒）
        """
        labels = {
            "tool_name": tool_name,
            "tool_category": tool_category,
            "success": str(success),
        }
        
        # 增加计数器
        self._increment_counter("yunshu_tool_call_total", labels)
        
        # 记录耗时
        if duration is not None:
            self._observe_histogram("yunshu_tool_call_duration_seconds", labels, duration)
        
        logger.debug(log_dict({'module_name': 'business_metrics', 'action': 'tool.tool_name.category', 'msg': f'[BusinessMetrics] 工具调用记录: tool={tool_name}, category={tool_category}, success={success}, duration={duration}'}))
    
    # ── 任务完成指标 ──
    
    def record_task(
        self,
        task_type: str,
        complexity: str,
        status: str,
        duration: Optional[float] = None,
    ):
        """记录任务执行
        
        Args:
            task_type: 任务类型（planning/async/direct等）
            complexity: 复杂度（simple/medium/complex）
            status: 状态（success/failed/pending）
            duration: 耗时（秒）
        """
        labels = {
            "task_type": task_type,
            "complexity": complexity,
            "status": status,
        }
        
        # 增加计数器
        self._increment_counter("yunshu_task_total", labels)
        
        # 记录耗时
        if duration is not None:
            self._observe_histogram("yunshu_task_duration_seconds", labels, duration)
        
        logger.debug(log_dict({'module_name': 'business_metrics', 'action': 'type.task_type.complexity', 'msg': f'[BusinessMetrics] 任务记录: type={task_type}, complexity={complexity}, status={status}, duration={duration}'}))
    
    def update_task_completion_rate(self, task_type: str, complexity: str, rate: float):
        """更新任务完成率
        
        Args:
            task_type: 任务类型
            complexity: 复杂度
            rate: 完成率（0-100）
        """
        labels = {
            "task_type": task_type,
            "complexity": complexity,
        }
        self._set_gauge("yunshu_task_completion_rate", labels, rate)
    
    def record_planning_task(self, planner_type: str, steps_count: int, success: bool):
        """记录规划任务
        
        Args:
            planner_type: 规划器类型
            steps_count: 步骤数量
            success: 是否成功
        """
        labels = {
            "planner_type": planner_type,
            "steps_count": str(steps_count),
        }
        self._increment_counter("yunshu_planning_task_success", labels)
    
    def record_async_task(self, async_type: str, queue_name: str, success: bool):
        """记录异步任务
        
        Args:
            async_type: 异步任务类型
            queue_name: 队列名称
            success: 是否成功
        """
        labels = {
            "async_type": async_type,
            "queue_name": queue_name,
        }
        self._increment_counter("yunshu_async_task_success", labels)
    
    # ── 知识库指标 ──
    
    def record_memory_search(
        self,
        memory_type: str,
        search_method: str,
        hit: bool,
        duration: Optional[float] = None,
    ):
        """记录记忆搜索
        
        Args:
            memory_type: 记忆类型（long_term/short_term等）
            search_method: 搜索方法（keyword/vector等）
            hit: 是否命中
            duration: 耗时（秒）
        """
        labels = {
            "memory_type": memory_type,
            "search_method": search_method,
            "hit": str(hit),
        }
        
        # 增加计数器
        self._increment_counter("yunshu_memory_search_total", labels)
        
        logger.debug(log_dict({'module_name': 'business_metrics', 'action': 'type.memory_type.method', 'msg': f'[BusinessMetrics] 记忆搜索记录: type={memory_type}, method={search_method}, hit={hit}'}))
    
    def update_memory_hit_rate(self, memory_type: str, search_method: str, rate: float):
        """更新记忆搜索命中率
        
        Args:
            memory_type: 记忆类型
            search_method: 搜索方法
            rate: 命中率（0-100）
        """
        labels = {
            "memory_type": memory_type,
            "search_method": search_method,
        }
        self._set_gauge("yunshu_memory_search_hit_rate", labels, rate)
    
    def record_memory_access(self, memory_key: str, importance: int):
        """记录记忆访问
        
        Args:
            memory_key: 记忆键
            importance: 重要性评分
        """
        labels = {
            "memory_key": memory_key,
            "importance": str(importance),
        }
        self._increment_counter("yunshu_memory_access_total", labels)
    
    def record_memory_storage(self, memory_type: str, importance: int, success: bool = True):
        """记录记忆存储
        
        Args:
            memory_type: 记忆类型
            importance: 重要性评分
            success: 是否成功（默认为True）
        """
        labels = {
            "memory_type": memory_type,
            "importance": str(importance),
            "success": str(success),
        }
        self._increment_counter("yunshu_memory_storage_total", labels)
    
    def update_vector_hit_rate(self, vector_store: str, query_type: str, rate: float):
        """更新向量查询命中率
        
        Args:
            vector_store: 向量存储类型
            query_type: 查询类型
            rate: 命中率（0-100）
        """
        labels = {
            "vector_store": vector_store,
            "query_type": query_type,
        }
        self._set_gauge("yunshu_vector_query_hit_rate", labels, rate)
    
    def record_memory_compression(self, compression_type: str, success: bool):
        """记录记忆压缩
        
        Args:
            compression_type: 压缩类型
            success: 是否成功
        """
        labels = {
            "compression_type": compression_type,
            "success": str(success),
        }
        self._increment_counter("yunshu_memory_compression_total", labels)
    
    def record_memory_deletion(self, memory_type: str, success: bool):
        """记录记忆删除
        
        Args:
            memory_type: 记忆类型
            success: 是否成功
        """
        labels = {
            "memory_type": memory_type,
            "success": str(success),
        }
        self._increment_counter("yunshu_memory_deletion_total", labels)
    
    def record_memory_operation(self, operation_type: str, memory_type: str, duration: float):
        """记录记忆操作耗时
        
        Args:
            operation_type: 操作类型（search/save/delete/update）
            memory_type: 记忆类型
            duration: 耗时（秒）
        """
        labels = {
            "operation_type": operation_type,
            "memory_type": memory_type,
        }
        self._observe_histogram("yunshu_memory_operation_duration_seconds", labels, duration)
    
    # ── 扩展使用指标 ──
    
    def record_extension_install(
        self,
        extension_type: str,
        source: str,
        success: bool,
    ):
        """记录扩展安装
        
        Args:
            extension_type: 扩展类型（skill/mcp/channel/plugin）
            source: 来源（builtin/github/npm/pip等）
            success: 是否成功
        """
        labels = {
            "extension_type": extension_type,
            "source": source,
            "success": str(success),
        }
        self._increment_counter("yunshu_extension_install_total", labels)
        
        logger.debug(log_dict({'module_name': 'business_metrics', 'action': 'type.extension_type.source', 'msg': f'[BusinessMetrics] 扩展安装记录: type={extension_type}, source={source}, success={success}'}))
    
    def record_extension_uninstall(self, extension_type: str, extension_id: str):
        """记录扩展卸载
        
        Args:
            extension_type: 扩展类型
            extension_id: 扩展ID
        """
        labels = {
            "extension_type": extension_type,
            "extension_id": extension_id,
        }
        self._increment_counter("yunshu_extension_uninstall_total", labels)
    
    def update_extension_enabled_count(self, extension_type: str, count: int):
        """更新已启用扩展数量
        
        Args:
            extension_type: 扩展类型
            count: 数量
        """
        labels = {
            "extension_type": extension_type,
        }
        self._set_gauge("yunshu_extension_enabled_count", labels, count)
    
    def record_mcp_connection(
        self,
        transport_type: str,
        service_id: str,
        success: bool,
    ):
        """记录 MCP 连接
        
        Args:
            transport_type: 传输类型（stdio/http）
            service_id: 服务ID
            success: 是否成功
        """
        labels = {
            "transport_type": transport_type,
            "service_id": service_id,
            "success": str(success),
        }
        self._increment_counter("yunshu_mcp_connection_total", labels)
    
    def update_mcp_active_connections(self, transport_type: str, count: int):
        """更新活跃 MCP 连接数
        
        Args:
            transport_type: 传输类型
            count: 数量
        """
        labels = {
            "transport_type": transport_type,
        }
        self._set_gauge("yunshu_mcp_active_connection_count", labels, count)
    
    def record_skill_usage(self, skill_id: str, skill_category: str, success: bool):
        """记录技能使用
        
        Args:
            skill_id: 技能ID
            skill_category: 技能分类
            success: 是否成功
        """
        labels = {
            "skill_id": skill_id,
            "skill_category": skill_category,
            "success": str(success),
        }
        self._increment_counter("yunshu_skill_usage_total", labels)
    
    def record_market_search(self, query_category: str, result_count: int):
        """记录扩展市场搜索
        
        Args:
            query_category: 查询分类
            result_count: 结果数量
        """
        labels = {
            "query_category": query_category,
            "result_count": str(result_count),
        }
        self._increment_counter("yunshu_market_search_total", labels)
    
    # ── 模型路由指标 ──
    
    def record_model_call(self, model_name: str, provider: str, success: bool, duration: Optional[float] = None):
        """记录模型调用
        
        Args:
            model_name: 模型名称
            provider: 模型提供商
            success: 是否成功
            duration: 耗时（秒）
        """
        labels = {
            "model_name": model_name,
            "provider": provider,
            "success": str(success),
        }
        self._increment_counter("yunshu_model_call_total", labels)
        
        if duration is not None:
            duration_labels = {
                "model_name": model_name,
                "provider": provider,
            }
            self._observe_histogram("yunshu_model_call_duration_seconds", duration_labels, duration)
    
    def update_model_success_rate(self, model_name: str, provider: str, rate: float):
        """更新模型调用成功率
        
        Args:
            model_name: 模型名称
            provider: 模型提供商
            rate: 成功率（0-100）
        """
        labels = {
            "model_name": model_name,
            "provider": provider,
        }
        self._set_gauge("yunshu_model_success_rate", labels, rate)
    
    def record_model_switch(self, from_model: str, to_model: str, reason: str):
        """记录模型切换
        
        Args:
            from_model: 切换前模型
            to_model: 切换后模型
            reason: 切换原因
        """
        labels = {
            "from_model": from_model,
            "to_model": to_model,
            "reason": reason,
        }
        self._increment_counter("yunshu_model_switch_total", labels)
    
    # ── 稳定性指标 ──
    
    def record_circuit_breaker_trigger(self, breaker_name: str, from_state: str, to_state: str, reason: str):
        """记录熔断器触发
        
        Args:
            breaker_name: 熔断器名称
            from_state: 触发前状态
            to_state: 触发后状态
            reason: 触发原因
        """
        labels = {
            "breaker_name": breaker_name,
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
        }
        self._increment_counter("yunshu_circuit_breaker_trigger_total", labels)
    
    def update_circuit_breaker_state(self, breaker_name: str, state: str, value: float = 1.0):
        """更新熔断器状态
        
        Args:
            breaker_name: 熔断器名称
            state: 当前状态（closed/open/half_open）
            value: 状态值（1表示该状态，0表示非该状态）
        """
        labels = {
            "breaker_name": breaker_name,
            "state": state,
        }
        self._set_gauge("yunshu_circuit_breaker_state", labels, value)
    
    def record_rate_limit_trigger(self, level: str, endpoint: str, user_id: str = "", reason: str = ""):
        """记录限流触发
        
        Args:
            level: 限流级别（global/endpoint/user）
            endpoint: 接口端点
            user_id: 用户ID
            reason: 限流原因
        """
        labels = {
            "level": level,
            "endpoint": endpoint,
            "user_id": user_id,
            "reason": reason,
        }
        self._increment_counter("yunshu_rate_limit_trigger_total", labels)
    
    def record_degrade_trigger(self, module: str, level: str, reason: str):
        """记录降级触发
        
        Args:
            module: 降级模块（schema/critic/memory/dashboard/tool_call）
            level: 降级级别（retry/lenient/text_only/cache_only/skip/emergency）
            reason: 降级原因
        """
        labels = {
            "module": module,
            "level": level,
            "reason": reason,
        }
        self._increment_counter("yunshu_degrade_trigger_total", labels)
    
    def record_disaster_recovery(self, recovery_type: str, status: str, backup_id: str = ""):
        """记录容灾恢复
        
        Args:
            recovery_type: 恢复类型（auto/manual/snapshot）
            status: 恢复状态（completed/failed/in_progress）
            backup_id: 备份ID
        """
        labels = {
            "recovery_type": recovery_type,
            "status": status,
            "backup_id": backup_id,
        }
        self._increment_counter("yunshu_disaster_recovery_total", labels)
    
    def record_backup(self, backup_type: str, success: bool):
        """记录备份
        
        Args:
            backup_type: 备份类型（full/incremental/snapshot）
            success: 是否成功
        """
        labels = {
            "backup_type": backup_type,
            "success": str(success),
        }
        self._increment_counter("yunshu_backup_total", labels)
    
    # ── 内部方法 ──
    
    def _increment_counter(self, metric_name: str, labels: Dict[str, str]) -> None:
        """增加计数器（埋点失败隔离：内部异常不向上传播）"""
        try:
            label_key = make_label_key(labels)
            with self._lock:
                self._counters[metric_name][label_key] += 1
                self._timestamps[metric_name][label_key].append(time.time())
        except Exception as e:
            # 埋点失败隔离：记录日志但不向上传播异常
            logger.warning(log_dict({'module_name': 'business_metrics', 'action': 'metric.metric_name.error', 'msg': f'[BusinessMetrics] 计数器增加失败: metric={metric_name}, error={e}'}))

    def _set_gauge(self, metric_name: str, labels: Dict[str, str], value: float) -> None:
        """设置 Gauge 值（埋点失败隔离：内部异常不向上传播）"""
        try:
            label_key = make_label_key(labels)
            with self._lock:
                self._gauges[metric_name][label_key] = value
        except Exception as e:
            logger.warning(log_dict({'module_name': 'business_metrics', 'action': 'gauge.metric.metric_name', 'msg': f'[BusinessMetrics] Gauge设置失败: metric={metric_name}, error={e}'}))

    def _observe_histogram(self, metric_name: str, labels: Dict[str, str], value: float) -> None:
        """观测 Histogram 值（埋点失败隔离：内部异常不向上传播）"""
        try:
            label_key = make_label_key(labels)
            with self._lock:
                self._histograms[metric_name][label_key].append(value)
        except Exception as e:
            logger.warning(log_dict({'module_name': 'business_metrics', 'action': 'histogram.metric.metric_name', 'msg': f'[BusinessMetrics] Histogram观测失败: metric={metric_name}, error={e}'}))
    
    # ── 数据查询 ──
    
    def get_dashboard_data(self, time_range: Optional[float] = None) -> Dict:
        """获取仪表盘数据
        
        Args:
            time_range: 时间范围（秒），None 表示全部数据
        
        Returns:
            仪表盘数据字典
        """
        with self._lock:
            # 计算时间范围
            now = time.time()
            start_time = now - time_range if time_range else 0
            
            # 收集各分类指标
            dashboard = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "time_range_seconds": time_range,
                "interaction": self._get_category_metrics("interaction", start_time),
                "task": self._get_category_metrics("task", start_time),
                "knowledge": self._get_category_metrics("knowledge", start_time),
                "extension": self._get_category_metrics("extension", start_time),
                "model_router": self._get_category_metrics("model_router", start_time),
                "stability": self._get_category_metrics("stability", start_time),
            }
            
            # 计算汇总统计
            dashboard["summary"] = self._calculate_summary(dashboard)
            
            return dashboard
    
    def _get_category_metrics(self, category: str, start_time: float) -> Dict:
        """获取指定分类的指标数据"""
        metrics = {}
        
        # 查找该分类的所有指标定义
        for name, definition in BUSINESS_METRICS_DEFINITIONS.items():
            if definition.category == category:
                metrics[name] = {
                    "description": definition.description,
                    "unit": definition.unit,
                    "business_value": definition.business_value,
                    "data": self._get_metric_data(name, definition.metric_type, start_time),
                }
        
        return metrics
    
    def _get_metric_data(self, metric_name: str, metric_type: str, start_time: float) -> Dict:
        """获取单个指标的数据（支持时间范围过滤）"""
        data = {}
        
        if metric_type == "counter":
            if metric_name in self._counters:
                for label_key, value in self._counters[metric_name].items():
                    if start_time > 0 and metric_name in self._timestamps:
                        timestamps = self._timestamps[metric_name].get(label_key, [])
                        filtered_count = sum(1 for t in timestamps if t >= start_time)
                        if filtered_count > 0:
                            data[label_key] = filtered_count
                    else:
                        data[label_key] = value
        
        elif metric_type == "gauge":
            if metric_name in self._gauges:
                for label_key, value in self._gauges[metric_name].items():
                    data[label_key] = value
        
        elif metric_type == "histogram":
            if metric_name in self._histograms:
                for label_key, values in self._histograms[metric_name].items():
                    if values:
                        data[label_key] = calculate_percentiles(values)
        
        return data
    
    def _calculate_summary(self, dashboard: Dict) -> Dict:
        """计算汇总统计"""
        summary = {
            "total_interactions": 0,
            "total_tool_calls": 0,
            "task_success_rate": 0.0,
            "memory_hit_rate": 0.0,
            "active_extensions": 0,
        }
        
        # 计算总交互次数
        if "yunshu_interaction_total" in dashboard.get("interaction", {}):
            data = dashboard["interaction"]["yunshu_interaction_total"]["data"]
            summary["total_interactions"] = sum(data.values())
        
        # 计算总工具调用次数
        if "yunshu_tool_call_total" in dashboard.get("interaction", {}):
            data = dashboard["interaction"]["yunshu_tool_call_total"]["data"]
            summary["total_tool_calls"] = sum(data.values())
        
        # 计算任务成功率
        if "yunshu_task_completion_rate" in dashboard.get("task", {}):
            data = dashboard["task"]["yunshu_task_completion_rate"]["data"]
            if data:
                summary["task_success_rate"] = sum(data.values()) / len(data)
        
        # 计算记忆命中率
        if "yunshu_memory_search_hit_rate" in dashboard.get("knowledge", {}):
            data = dashboard["knowledge"]["yunshu_memory_search_hit_rate"]["data"]
            if data:
                summary["memory_hit_rate"] = sum(data.values()) / len(data)
        
        # 计算活跃扩展数
        if "yunshu_extension_enabled_count" in dashboard.get("extension", {}):
            data = dashboard["extension"]["yunshu_extension_enabled_count"]["data"]
            summary["active_extensions"] = sum(data.values())
        
        return summary
    
    def get_metric_by_name(self, metric_name: str) -> Optional[Dict]:
        """获取单个指标的详细信息
        
        Args:
            metric_name: 指标名称
        
        Returns:
            指标详情字典
        """
        definition = BUSINESS_METRICS_DEFINITIONS.get(metric_name)
        if not definition:
            return None
        
        return {
            "definition": {
                "name": definition.name,
                "description": definition.description,
                "metric_type": definition.metric_type,
                "labels": definition.labels,
                "unit": definition.unit,
                "category": definition.category,
                "business_value": definition.business_value,
            },
            "data": self._get_metric_data(metric_name, definition.metric_type, 0),
        }
    
    def reset(self):
        """重置所有指标"""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._timestamps.clear()
        logger.info(log_dict({'module_name': 'business_metrics', 'action': 'log', 'msg': '[BusinessMetrics] 业务指标已重置'}))
    
    def export_prometheus(self) -> str:
        """导出 Prometheus 格式的指标
        
        Returns:
            Prometheus 格式的文本
        """
        lines = []
        
        for name, definition in BUSINESS_METRICS_DEFINITIONS.items():
            metric_name = name.replace('.', '_')
            lines.append(f"# HELP {metric_name} {definition.description}")
            lines.append(f"# TYPE {metric_name} {definition.metric_type}")
            
            if definition.metric_type == "counter":
                if name in self._counters:
                    for label_key, value in self._counters[name].items():
                        labels_dict = parse_label_key(label_key)
                        labels_str = ",".join(f'{k}="{v}"' for k, v in labels_dict.items())
                        lines.append(f'{metric_name}{{{labels_str}}} {value}')
            
            elif definition.metric_type == "gauge":
                if name in self._gauges:
                    for label_key, value in self._gauges[name].items():
                        labels_dict = parse_label_key(label_key)
                        labels_str = ",".join(f'{k}="{v}"' for k, v in labels_dict.items())
                        lines.append(f'{metric_name}{{{labels_str}}} {value}')
            
            elif definition.metric_type == "histogram":
                if name in self._histograms:
                    for label_key, values in self._histograms[name].items():
                        if values:
                            labels_dict = parse_label_key(label_key)
                            labels_str = ",".join(f'{k}="{v}"' for k, v in labels_dict.items())
                            stats = calculate_percentiles(values)
                            lines.append(f'{metric_name}_sum{{{labels_str}}} {stats["sum"]}')
                            lines.append(f'{metric_name}_count{{{labels_str}}} {stats["count"]}')
                            lines.append(f'{metric_name}{{{labels_str},quantile="0.5"}} {stats["p50"]}')
                            lines.append(f'{metric_name}{{{labels_str},quantile="0.95"}} {stats["p95"]}')
                            lines.append(f'{metric_name}{{{labels_str},quantile="0.99"}} {stats["p99"]}')
        
        return '\n'.join(lines)


# ============================================================================
# 全局单例
# ============================================================================

_global_business_collector = BusinessMetricsCollector()


def get_business_metrics_collector() -> BusinessMetricsCollector:
    """获取全局业务指标收集器
    
    Returns:
        全局 BusinessMetricsCollector 实例
    """
    return _global_business_collector


# ============================================================================
# 快捷函数
# ============================================================================

def record_interaction(interaction_type: str, model: str, success: bool = True, duration: Optional[float] = None):
    """快捷函数：记录用户交互"""
    get_business_metrics_collector().record_interaction(interaction_type, model, success, duration)


def record_tool_call(tool_name: str, tool_category: str, success: bool = True, duration: Optional[float] = None):
    """快捷函数：记录工具调用"""
    get_business_metrics_collector().record_tool_call(tool_name, tool_category, success, duration)


def record_task(task_type: str, complexity: str, status: str, duration: Optional[float] = None):
    """快捷函数：记录任务执行"""
    get_business_metrics_collector().record_task(task_type, complexity, status, duration)


def record_memory_search(memory_type: str, search_method: str, hit: bool):
    """快捷函数：记录记忆搜索"""
    get_business_metrics_collector().record_memory_search(memory_type, search_method, hit)


def record_extension_install(extension_type: str, source: str, success: bool):
    """快捷函数：记录扩展安装"""
    get_business_metrics_collector().record_extension_install(extension_type, source, success)


def get_dashboard_data(time_range: Optional[float] = None) -> Dict:
    """快捷函数：获取仪表盘数据"""
    return get_business_metrics_collector().get_dashboard_data(time_range)