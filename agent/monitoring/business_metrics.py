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
    # 【不易】技能质量与幻觉指标（与 observability.emit_eval_score_metric 对齐）
    "yunshu_skill_eval_score": BusinessMetricDefinition(
        name="yunshu_skill_eval_score",
        description="技能端到端评估分数分布（0-1）",
        metric_type="histogram",
        labels=["skill_id", "task_success"],
        unit="分",
        category="extension",
        business_value="衡量技能输出质量，识别低分技能",
        aggregation="avg",
        retention_days=30,
    ),
    "yunshu_skill_hallucination_total": BusinessMetricDefinition(
        name="yunshu_skill_hallucination_total",
        description="技能幻觉检测总次数",
        metric_type="counter",
        labels=["skill_id"],
        unit="次",
        category="extension",
        business_value="衡量技能幻觉率，识别不可靠技能",
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

    # ── 6. 技能检索指标（RRF / TF-IDF / 缓存 / 倒排索引 / Reranker）──
    # 【为什么必须在这里"登记"而不是只在埋点处 inc/observe】
    #   BusinessMetricsCollector.export_prometheus() 的遍历源是
    #   BUSINESS_METRICS_DEFINITIONS（只导出"已登记"的名字）。没有登记 = 埋点写进了
    #   内存字典、/api/business/prometheus 里连指标名都看不到 —— 这正是
    #   reranker.py:662 的 yunshu_rerank_duration_ms 埋点了却告警恒不触发的原因。
    # 【为什么是静态登记而不是 lock_watchdog 那样的运行时注册】
    #   这些指标属于技能检索主链路（loader/vector_adapter/negative_intent_detector
    #   都在同一个包内），登记点就在业务指标模块本身最不容易漏；lock_watchdog 用
    #   运行时注册是因为它默认关闭、零开销，不适用于本场景。
    # 【命名口径】名字逐字对齐 monitoring/ 下的规则文件（含 tfidf_scan_candidate_total_total
    #   这种双 _total），改名等于规则继续恒不触发，故不做"美化"。
    "tfidf_scan_candidate_limit_applied_total": BusinessMetricDefinition(
        name="tfidf_scan_candidate_limit_applied_total",
        description="TF-IDF 扫描 candidate_limit 截断实际生效次数（降级方案触发次数）",
        metric_type="counter",
        labels=["layer"],
        unit="次",
        category="skill_quality",
        business_value="candidate_limit 降级是否在压测/生产中真的生效",
        aggregation="sum",
        retention_days=30,
    ),
    "tfidf_scan_candidate_truncated_total": BusinessMetricDefinition(
        name="tfidf_scan_candidate_truncated_total",
        description="TF-IDF 扫描被 candidate_limit 丢弃的候选技能数（按候选计，非按次计）",
        metric_type="counter",
        labels=["layer"],
        unit="个",
        category="skill_quality",
        business_value="量化降级造成的候选集精度损失（截断比例的分母/分子之一）",
        aggregation="sum",
        retention_days=30,
    ),
    "tfidf_scan_candidate_total_total": BusinessMetricDefinition(
        name="tfidf_scan_candidate_total_total",
        description="TF-IDF 扫描命中 query token 的候选技能总数（截断前）",
        metric_type="counter",
        labels=["layer"],
        unit="个",
        category="skill_quality",
        business_value="技能规模与 query 泛化程度的直接度量；与截断数相除得精度损失比",
        aggregation="sum",
        retention_days=30,
    ),
    "query_cache_hit_rate": BusinessMetricDefinition(
        name="query_cache_hit_rate",
        description="query embedding LRU 缓存命中率（0-100）",
        metric_type="gauge",
        labels=["cache"],
        unit="%",
        category="skill_quality",
        business_value="衡量 BGE-m3 推理开销被缓存抵消的程度",
        aggregation="last",
        retention_days=30,
    ),
    "query_cache_misses_total": BusinessMetricDefinition(
        name="query_cache_misses_total",
        description="query embedding LRU 缓存未命中总次数",
        metric_type="counter",
        labels=["cache"],
        unit="次",
        category="skill_quality",
        business_value="未命中激增 = 冷启动或 query 模式突变，是推理延迟的先行指标",
        aggregation="sum",
        retention_days=30,
    ),
    "inverted_index_built_total": BusinessMetricDefinition(
        name="inverted_index_built_total",
        description="TF-IDF 倒排索引重建次数",
        metric_type="counter",
        labels=["include_zh"],
        unit="次",
        category="skill_quality",
        business_value="正常仅进程启动构建 1 次；频繁重建说明 _meta_index 被异常刷新",
        aggregation="sum",
        retention_days=30,
    ),
    "skill_use_inverted_index": BusinessMetricDefinition(
        name="skill_use_inverted_index",
        description="当前技能检索是否走倒排索引（1=启用，0=全量遍历）",
        metric_type="gauge",
        labels=["layer"],
        unit="",
        category="skill_quality",
        business_value="与 skill_total_count 联合判断'大规模下却未启用加速'",
        aggregation="last",
        retention_days=7,
    ),
    "skill_total_count": BusinessMetricDefinition(
        name="skill_total_count",
        description="当前元数据索引中的技能总数",
        metric_type="gauge",
        labels=["layer"],
        unit="个",
        category="skill_quality",
        business_value="技能规模，是容量规划与降级阈值的基准量",
        aggregation="last",
        retention_days=30,
    ),
    "skill_candidate_limit_current": BusinessMetricDefinition(
        name="skill_candidate_limit_current",
        description="当前生效的 TF-IDF 候选集上限（0=未降级）",
        metric_type="gauge",
        labels=["layer"],
        unit="个",
        category="skill_quality",
        business_value="区分'P99 高是因为没开降级'还是'开了降级仍高'",
        aggregation="last",
        retention_days=30,
    ),
    "yunshu_skill_match_fallback_total": BusinessMetricDefinition(
        name="yunshu_skill_match_fallback_total",
        description="技能检索降级到下一层检索路的次数（按 reason 区分）",
        metric_type="counter",
        labels=["reason", "success"],
        unit="次",
        category="skill_quality",
        business_value="检索层级失效（向量不可用/RRF 空/精排未生效）的直接信号",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_negative_intent_detector_failed_total": BusinessMetricDefinition(
        name="yunshu_negative_intent_detector_failed_total",
        description="v6.2 负样本意图检测器降级/失败次数（prototype 加载失败、编码不可用、异常）",
        metric_type="counter",
        labels=["reason", "success"],
        unit="次",
        category="skill_quality",
        business_value="检测器持续降级会让负样本回退到 RRF+Reranker（延迟 600ms+）",
        aggregation="sum",
        retention_days=30,
    ),
    "yunshu_negative_intent_duration_ms": BusinessMetricDefinition(
        name="yunshu_negative_intent_duration_ms",
        description="v6.2 负样本意图检测耗时分布（毫秒）",
        metric_type="histogram",
        labels=["result"],
        unit="毫秒",
        category="skill_quality",
        business_value="验证'负样本延迟 ≤ 200ms'目标是否被 BGE-m3 编码拖垮",
        aggregation="avg",
        retention_days=7,
    ),
    "yunshu_rerank_duration_ms": BusinessMetricDefinition(
        name="yunshu_rerank_duration_ms",
        description="Cross-Encoder 精排耗时分布（毫秒，reranker.py 既有埋点）",
        metric_type="histogram",
        labels=["backend", "success"],
        unit="毫秒",
        category="skill_quality",
        business_value="ONNX/PyTorch 后端的延迟 SLO（500ms）唯一数据源",
        aggregation="avg",
        retention_days=7,
    ),
    # ======================================================================
    #  7. 全仓 emit_metric 指标名补登记（2026-10-02）
    #
    #  【为什么必须逐条登记】BusinessMetricsCollector.export_prometheus() 的遍历源
    #    是本表：名字不在这里 => 埋点写进了内存字典，但 /api/business/prometheus
    #    里连指标名都没有 => 依赖它的告警**恒不触发**（即本次修复的缺陷）。
    #  【kind/labels 的来源】不是猜的：对 agent/ 下所有 emit_metric(...) 调用做 AST
    #    扫描，取字面量 kind（缺省 = counter）与 labels 键；labels 里的 success 是
    #    emit_metric 在既无 success 也无 failure 时**自动补**的标签（observability.py）。
    #  【type 写错的后果】导出器按 metric_type 去对应字典取值 —— histogram 写成
    #    gauge 会让样本行永远为空，所以这里必须与调用点逐字一致。
    #  【回归防线】tests/unit/test_business_metrics_registration_completeness.py
    #    用同样的 AST 扫描断言『每个 emit_metric 名字都已登记』，防止再次漏登记。
    #  【清单来源】agent/skills_mgmt（57）+ agent/workflow_learning（10）+ agent/cognitive（5）
    #    —— 这三处的 emit_metric 都指向同一个全局业务收集器。
    'yunshu_reranker_load_total': BusinessMetricDefinition(
        name='yunshu_reranker_load_total',
        description='Reranker 后端加载次数（backend=onnx/pytorch，status=success/failed/skipped，skipped 带 reason）',
        metric_type='counter',
        labels=['backend', 'status', 'reason', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_reranker_load_time_seconds': BusinessMetricDefinition(
        name='yunshu_reranker_load_time_seconds',
        description='Reranker 模型加载耗时（秒，RerankerOnnxLoadSlow 数据源）',
        metric_type='gauge',
        labels=['backend', 'success'],
        unit='秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_reranker_fallback_total': BusinessMetricDefinition(
        name='yunshu_reranker_fallback_total',
        description='Reranker 降级次数（onnx→pytorch 或 reranker→original_order）',
        metric_type='counter',
        labels=['from', 'to', 'reason', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_reranker_completed_total': BusinessMetricDefinition(
        name='yunshu_reranker_completed_total',
        description='Reranker 精排成功次数（降级率分母之一，reranker-alerts.yml 引用）',
        metric_type='counter',
        labels=['backend', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_reranker_predict_failed_total': BusinessMetricDefinition(
        name='yunshu_reranker_predict_failed_total',
        description='Reranker 推理失败次数（推理失败率分子，reranker-alerts.yml 引用）',
        metric_type='counter',
        labels=['backend', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_reranker_timeout_total': BusinessMetricDefinition(
        name='yunshu_reranker_timeout_total',
        description='Reranker 推理超时次数（rerank_timeout 触发）',
        metric_type='counter',
        labels=['backend', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_reranker_hot_reload_total': BusinessMetricDefinition(
        name='yunshu_reranker_hot_reload_total',
        description='Reranker ONNX variant 热重载次数（status=success/failed/exception）',
        metric_type='counter',
        labels=['status', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_reranker_model_size_gb': BusinessMetricDefinition(
        name='yunshu_reranker_model_size_gb',
        description='Reranker 模型体积（GB，超过 1GB 建议换轻量模型）',
        metric_type='gauge',
        labels=['model', 'success'],
        unit='GB',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_skill_match_latency_ms': BusinessMetricDefinition(
        name='yunshu_skill_match_latency_ms',
        description='技能匹配耗时分布（毫秒，业务层；原生 skill_match_latency_ms 另有 _bucket 通道）',
        metric_type='histogram',
        labels=['layer', 'method', 'success'],
        unit='毫秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_match_count': BusinessMetricDefinition(
        name='yunshu_skill_match_count',
        description='单次技能匹配返回的技能数（gauge，v6 告警用它算命中占比）',
        metric_type='gauge',
        labels=['layer', 'method', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_skill_rrf_quality_gate_rejected': BusinessMetricDefinition(
        name='yunshu_skill_rrf_quality_gate_rejected',
        description='RRF 质量闸门拒绝次数（判为误召回的候选被拦）',
        metric_type='counter',
        labels=['layer', 'method', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_retrieval_precision_at_k': BusinessMetricDefinition(
        name='yunshu_skill_retrieval_precision_at_k',
        description='检索 Precision@K 分布（k 为标签；P@3 告警数据源）',
        metric_type='histogram',
        labels=['k', 'success'],
        unit='%',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_bm25_index_count': BusinessMetricDefinition(
        name='yunshu_skill_bm25_index_count',
        description='BM25 索引条目数',
        metric_type='gauge',
        labels=['layer', 'method', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_skill_bm25_match_count': BusinessMetricDefinition(
        name='yunshu_skill_bm25_match_count',
        description='BM25 单次命中的技能数',
        metric_type='gauge',
        labels=['layer', 'method', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_skill_search_latency_ms': BusinessMetricDefinition(
        name='yunshu_skill_search_latency_ms',
        description='技能检索（searcher）耗时分布（毫秒）',
        metric_type='histogram',
        labels=['success'],
        unit='毫秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_vector_index_count': BusinessMetricDefinition(
        name='yunshu_skill_vector_index_count',
        description='向量索引条目数',
        metric_type='gauge',
        labels=['success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_skill_vector_degraded_total': BusinessMetricDefinition(
        name='yunshu_skill_vector_degraded_total',
        description='向量检索降级次数（按降级原因）',
        metric_type='counter',
        labels=['reason', 'failure'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_vector_fallback_store_engaged': BusinessMetricDefinition(
        name='yunshu_skill_vector_fallback_store_engaged',
        description='向量落盘降级库启用次数',
        metric_type='counter',
        labels=['failure'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_vector_fallback_store_low_coverage': BusinessMetricDefinition(
        name='yunshu_skill_vector_fallback_store_low_coverage',
        description='向量库覆盖率不足触发降级次数',
        metric_type='counter',
        labels=['failure', 'threshold'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_metadata_index_count': BusinessMetricDefinition(
        name='yunshu_skill_metadata_index_count',
        description='技能元数据索引条目数',
        metric_type='gauge',
        labels=['success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_skill_exec_total': BusinessMetricDefinition(
        name='yunshu_skill_exec_total',
        description='技能执行次数（成功/失败，失败带 reason）',
        metric_type='counter',
        labels=['skill_id', 'success', 'reason'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_exec_latency_ms': BusinessMetricDefinition(
        name='yunshu_skill_exec_latency_ms',
        description='技能执行耗时分布（毫秒，executor 侧）',
        metric_type='histogram',
        labels=['skill_id', 'success'],
        unit='毫秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_execution_latency_ms': BusinessMetricDefinition(
        name='yunshu_skill_execution_latency_ms',
        description='技能执行耗时分布（毫秒，enhancer 侧）',
        metric_type='histogram',
        labels=['skill_id', 'success'],
        unit='毫秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_mgr_exec_total': BusinessMetricDefinition(
        name='yunshu_skill_mgr_exec_total',
        description='技能执行器（skill_manager）调用次数',
        metric_type='counter',
        labels=['skill_id', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_mgr_exec_latency_ms': BusinessMetricDefinition(
        name='yunshu_skill_mgr_exec_latency_ms',
        description='技能执行器耗时分布（毫秒）',
        metric_type='histogram',
        labels=['skill_id', 'success'],
        unit='毫秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_script_exec_total': BusinessMetricDefinition(
        name='yunshu_skill_script_exec_total',
        description='技能脚本执行次数',
        metric_type='counter',
        labels=['skill_id', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_script_latency_ms': BusinessMetricDefinition(
        name='yunshu_skill_script_latency_ms',
        description='技能脚本执行耗时分布（毫秒）',
        metric_type='histogram',
        labels=['skill_id', 'success'],
        unit='毫秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_validation_total': BusinessMetricDefinition(
        name='yunshu_skill_validation_total',
        description='技能输出校验次数（按 status）',
        metric_type='counter',
        labels=['skill_id', 'status', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_inject_tokens': BusinessMetricDefinition(
        name='yunshu_skill_inject_tokens',
        description='技能上下文注入 token 数分布（上下文成本）',
        metric_type='histogram',
        labels=['layer', 'skill_id', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_sensitive_inject_tokens': BusinessMetricDefinition(
        name='yunshu_skill_sensitive_inject_tokens',
        description='敏感技能注入 token 数分布（按隔离策略）',
        metric_type='histogram',
        labels=['skill_id', 'isolation_strategy', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_fewshot_inject_tokens': BusinessMetricDefinition(
        name='yunshu_skill_fewshot_inject_tokens',
        description='few-shot 示例注入 token 数分布',
        metric_type='histogram',
        labels=['skill_id', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_instruction_tokens': BusinessMetricDefinition(
        name='yunshu_skill_instruction_tokens',
        description='技能指令注入 token 数分布',
        metric_type='histogram',
        labels=['layer', 'skill_id', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_output_guard_total': BusinessMetricDefinition(
        name='yunshu_skill_output_guard_total',
        description='技能输出护栏命中次数（按严重级别）',
        metric_type='counter',
        labels=['skill_id', 'severity', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_llm_guard_total': BusinessMetricDefinition(
        name='yunshu_skill_llm_guard_total',
        description='技能 LLM 输出护栏命中次数',
        metric_type='counter',
        labels=['severity', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_value_guard_hit': BusinessMetricDefinition(
        name='yunshu_skill_value_guard_hit',
        description='价值护栏命中次数（blocked 表示是否拦截）',
        metric_type='counter',
        labels=['blocked', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_feedback_rating': BusinessMetricDefinition(
        name='yunshu_skill_feedback_rating',
        description='技能反馈评分分布',
        metric_type='histogram',
        labels=['skill_id', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_review_total': BusinessMetricDefinition(
        name='yunshu_skill_review_total',
        description='技能评审次数（成功/失败）',
        metric_type='counter',
        labels=['success', 'failure', 'reason'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_auto_rollback': BusinessMetricDefinition(
        name='yunshu_skill_auto_rollback',
        description='技能自动回滚次数（restored 表示是否已还原）',
        metric_type='counter',
        labels=['restored', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_create_total': BusinessMetricDefinition(
        name='yunshu_skill_create_total',
        description='技能创建次数（按创建模式）',
        metric_type='counter',
        labels=['mode', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_install_total': BusinessMetricDefinition(
        name='yunshu_skill_install_total',
        description='技能安装次数（按安装方式）',
        metric_type='counter',
        labels=['scheme', 'success'],
        unit='次',
        category='extension',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_publish_total': BusinessMetricDefinition(
        name='yunshu_skill_publish_total',
        description='技能发布次数',
        metric_type='counter',
        labels=['success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_toggle_total': BusinessMetricDefinition(
        name='yunshu_skill_toggle_total',
        description='技能启停次数（enabled 表示操作后状态）',
        metric_type='counter',
        labels=['enabled', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_version_bump_total': BusinessMetricDefinition(
        name='yunshu_skill_version_bump_total',
        description='技能版本递增次数（按递增类型）',
        metric_type='counter',
        labels=['kind', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_meta_edit_total': BusinessMetricDefinition(
        name='yunshu_skill_meta_edit_total',
        description='技能元数据编辑各阶段次数（stage 区分阶段）',
        metric_type='counter',
        labels=['stage', 'skill_id', 'status', 'actor', 'reason', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_evolution_total': BusinessMetricDefinition(
        name='yunshu_skill_evolution_total',
        description='技能演化决策次数（decision=采纳/拒绝）',
        metric_type='counter',
        labels=['skill_id', 'committed', 'decision', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_evolve_latency_ms': BusinessMetricDefinition(
        name='yunshu_skill_evolve_latency_ms',
        description='单技能演化耗时分布（毫秒）',
        metric_type='histogram',
        labels=['skill_id', 'success'],
        unit='毫秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_evolution_committed_total': BusinessMetricDefinition(
        name='yunshu_skill_evolution_committed_total',
        description='演化结果提交次数',
        metric_type='counter',
        labels=['skill_id', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_evolution_budget_break_total': BusinessMetricDefinition(
        name='yunshu_skill_evolution_budget_break_total',
        description='演化预算耗尽提前中断次数',
        metric_type='counter',
        labels=['skill_id', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_pareto_variants_count': BusinessMetricDefinition(
        name='yunshu_skill_pareto_variants_count',
        description='单技能候选变体数量',
        metric_type='gauge',
        labels=['skill_id', 'success'],
        unit='个',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_skill_evolution_batch_total': BusinessMetricDefinition(
        name='yunshu_skill_evolution_batch_total',
        description='离线演化批处理轮次（含本轮演化/跳过/失败数）',
        metric_type='counter',
        labels=['trigger', 'evolved', 'skipped', 'failed', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_evolution_batch_tokens': BusinessMetricDefinition(
        name='yunshu_skill_evolution_batch_tokens',
        description='离线演化批处理消耗 token 数',
        metric_type='counter',
        labels=['trigger', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_evolution_scheduled_run_total': BusinessMetricDefinition(
        name='yunshu_skill_evolution_scheduled_run_total',
        description='演化定时任务运行次数（按状态）',
        metric_type='counter',
        labels=['status', 'success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_pareto_filter_latency_ms': BusinessMetricDefinition(
        name='yunshu_skill_pareto_filter_latency_ms',
        description='Pareto 过滤耗时分布（毫秒）',
        metric_type='histogram',
        labels=['success'],
        unit='毫秒',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_skill_pareto_domination_checks': BusinessMetricDefinition(
        name='yunshu_skill_pareto_domination_checks',
        description='Pareto 支配关系检查次数（算法复杂度观测）',
        metric_type='counter',
        labels=['success'],
        unit='次',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_skill_pareto_front_ratio': BusinessMetricDefinition(
        name='yunshu_skill_pareto_front_ratio',
        description='Pareto 前沿占比（非支配解比例）',
        metric_type='gauge',
        labels=['success'],
        unit='%',
        category='skill_quality',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='last',
        retention_days=30,
    ),
    'yunshu_memory_abstract_total': BusinessMetricDefinition(
        name='yunshu_memory_abstract_total',
        description='记忆抽象（摘要）次数（auto_register 表示是否自动登记）',
        metric_type='counter',
        labels=['auto_register', 'success'],
        unit='次',
        category='knowledge',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_mcp_discover_total': BusinessMetricDefinition(
        name='yunshu_mcp_discover_total',
        description='MCP 服务发现次数（按 server）',
        metric_type='counter',
        labels=['server', 'success'],
        unit='次',
        category='extension',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_wf_execution_total': BusinessMetricDefinition(
        name='yunshu_wf_execution_total',
        description='工作流执行次数',
        metric_type='counter',
        labels=['success', 'workflow_id'],
        unit='次',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_wf_execution_latency_ms': BusinessMetricDefinition(
        name='yunshu_wf_execution_latency_ms',
        description='工作流执行耗时分布（毫秒）',
        metric_type='histogram',
        labels=['success'],
        unit='毫秒',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_wf_agent_execution_total': BusinessMetricDefinition(
        name='yunshu_wf_agent_execution_total',
        description='工作流内子代理执行次数',
        metric_type='counter',
        labels=['success', 'workflow_id'],
        unit='次',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_wf_generated_total': BusinessMetricDefinition(
        name='yunshu_wf_generated_total',
        description='学习生成的工作流数',
        metric_type='counter',
        labels=['success'],
        unit='次',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_wf_learned_total': BusinessMetricDefinition(
        name='yunshu_wf_learned_total',
        description='学习并落库的工作流数',
        metric_type='counter',
        labels=['success'],
        unit='次',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_wf_admission_draft_total': BusinessMetricDefinition(
        name='yunshu_wf_admission_draft_total',
        description='工作流准入草稿次数（code 为判定码）',
        metric_type='counter',
        labels=['code', 'success'],
        unit='次',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_wf_admission_rejected_total': BusinessMetricDefinition(
        name='yunshu_wf_admission_rejected_total',
        description='工作流准入拒绝次数（code 为拒绝码）',
        metric_type='counter',
        labels=['code', 'success'],
        unit='次',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_wf_match_latency_ms': BusinessMetricDefinition(
        name='yunshu_wf_match_latency_ms',
        description='工作流匹配耗时分布（毫秒）',
        metric_type='histogram',
        labels=['success'],
        unit='毫秒',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_workflow_convert_total': BusinessMetricDefinition(
        name='yunshu_workflow_convert_total',
        description='工作流转换为技能次数',
        metric_type='counter',
        labels=['success'],
        unit='次',
        category='task',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_external_skill_convert_total': BusinessMetricDefinition(
        name='yunshu_external_skill_convert_total',
        description='外部技能格式转换次数（按源格式）',
        metric_type='counter',
        labels=['source_format', 'success'],
        unit='次',
        category='extension',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_prompt_optimization_total': BusinessMetricDefinition(
        name='yunshu_prompt_optimization_total',
        description='Prompt 优化提案总次数（按来源与结果）',
        metric_type='counter',
        labels=['outcome', 'source', 'success'],
        unit='次',
        category='business',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_prompt_optimization_adopted_total': BusinessMetricDefinition(
        name='yunshu_prompt_optimization_adopted_total',
        description='Prompt 优化提案被采纳次数',
        metric_type='counter',
        labels=['proposal_id', 'success'],
        unit='次',
        category='business',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_prompt_optimization_failed_prompt_total': BusinessMetricDefinition(
        name='yunshu_prompt_optimization_failed_prompt_total',
        description='被判定为失败样本的 prompt 次数',
        metric_type='counter',
        labels=['outcome', 'prompt_id', 'success'],
        unit='次',
        category='business',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='sum',
        retention_days=30,
    ),
    'yunshu_prompt_optimization_improvement': BusinessMetricDefinition(
        name='yunshu_prompt_optimization_improvement',
        description='Prompt 优化提升幅度分布',
        metric_type='histogram',
        labels=['source', 'success'],
        unit='个',
        category='business',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
    ),
    'yunshu_prompt_optimization_score_delta': BusinessMetricDefinition(
        name='yunshu_prompt_optimization_score_delta',
        description='Prompt 优化前后得分变化分布',
        metric_type='histogram',
        labels=['success'],
        unit='个',
        category='business',
        business_value='补登记以恢复对应告警的可触发性与可观测性',
        aggregation='avg',
        retention_days=7,
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

    # ── 通用对外 API（供 observability.emit_metric 等通用埋点入口调用）──
    # [不易] 这 3 个公共方法是 emit_metric 的 hasattr 探测目标，缺失会导致
    # 所有通用埋点（含 yunshu_skill_hallucination_total）静默丢失。
    # 曾由 commit 1a7009a3 添加，后被覆盖丢失，此处恢复。

    def inc_counter(self, metric_name: str,
                    labels: Optional[Dict[str, str]] = None,
                    value: float = 1.0) -> None:
        """[TLM-L1] 通用计数器埋点 — 内部循环 _increment_counter 以支持 value>1"""
        if value <= 0:
            return
        labels = labels or {}
        n = int(value)
        for _ in range(n):
            self._increment_counter(metric_name, labels)

    def add_counter(self, metric_name: str,
                    value: float,
                    labels: Optional[Dict[str, str]] = None) -> None:
        """[技能检索埋点] 批量增量计数器 — 一次加锁加 value（value 可为任意正数）

        【为什么必须有它，而不能复用 inc_counter】
            inc_counter 的实现是「for _ in range(int(value))」逐次 +1。对
            tfidf_scan_candidate_total_total 这类"一次扫描数百上千个候选"的指标，
            逐次 +1 = 在检索**热路径**上做 O(value) 次"建标签键 + 加锁 + 写两个字典 +
            追加时间戳"，P99（约 40ms 的预算）会被埋点本身吃掉；同时 _timestamps
            会按 value 长度膨胀，内存无界。
        【语义】与 inc_counter 一致，仅把"次数"换成"总量"。
        【失败隔离】异常只记日志，绝不向上传播（与本类其余埋点同纪律）。
        """
        try:
            if value <= 0:
                return
            label_key = make_label_key(labels or {})
            with self._lock:
                self._counters[metric_name][label_key] += value
                # 时间戳按"调用次数"记（不按 value 展开），供时间窗查询近似定位；
                # 本组指标不在 get_dashboard_data 的时间窗分类里，不做逐事件还原。
                self._timestamps[metric_name][label_key].append(time.time())
        except Exception as e:
            logger.warning(log_dict({'module_name': 'business_metrics', 'action': 'metric.metric_name.error', 'msg': f'[BusinessMetrics] 计数器批量增加失败: metric={metric_name}, error={e}'}))

    def observe_histogram(self, metric_name: str,
                          value: float,
                          labels: Optional[Dict[str, str]] = None) -> None:
        """[TLM-L1] 通用直方图埋点 — 委托 _observe_histogram"""
        self._observe_histogram(metric_name, labels or {}, float(value))

    def set_gauge(self, metric_name: str,
                  value: float,
                  labels: Optional[Dict[str, str]] = None) -> None:
        """[TLM-L1] 通用仪表盘埋点 — 委托 _set_gauge"""
        self._set_gauge(metric_name, labels or {}, float(value))

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

        # [2026-08-13 并发审计] 无锁入口需持锁读取：防遍历时其他线程
        # 并发增删 defaultdict key 抛 RuntimeError
        with self._lock:
            data = self._get_metric_data(metric_name, definition.metric_type, 0)
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
            "data": data,
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
        
        # [2026-08-13 并发审计] 全量导出持锁遍历：防并发增删 key 抛 RuntimeError
        # （仅内存只读构建 lines，无 I/O）
        with self._lock:
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


# ============================================================================
# 熔断器观测钩子（**由本模块注入**，方向：monitoring → circuit_breaker，合法）
# ============================================================================

def _on_circuit_breaker_state(breaker_name: str, old_state, new_state) -> None:
    """接收熔断器状态事件并写成业务指标。

    `old_state is None` ⇒ 仅"发布当前状态"（访问点触发的，不是一次转换）。
    """
    _c = get_business_metrics_collector()
    if old_state is not None:
        _c.record_circuit_breaker_trigger(
            breaker_name, str(old_state), str(new_state), "state_machine")
    _c.update_circuit_breaker_state(breaker_name, str(new_state))


def _install_circuit_breaker_observer() -> None:
    """把上面的回调注入 `agent.circuit_breaker`。

    【为什么用注入而不是让 circuit_breaker 直接 import 本模块】
    `.importlinter` 有分层契约：`error_handler`（底层）不得导入 `monitoring`（上层）。
    而 `agent/error_handler.py` **会导入 `agent.circuit_breaker`**，
    所以 circuit_breaker 反向导入 monitoring 会让底层**间接**依赖上层，
    CI 的「循环依赖校验」实测报 BROKEN（链路：error_handler → circuit_breaker → business_metrics）。
    ⇒ 改为**上层注入**：方向变成 monitoring → circuit_breaker，合法且无环。
    这也是本仓既有的认可模式（该契约注释里写明 error_handler 的 7 处延迟导入属"有意 DI 设计"）。
    【失败隔离】钩子装不上不得影响本模块可用（业务指标照常工作，只是熔断器指标暂缺）。
    """
    try:
        from agent.circuit_breaker import set_state_observer
        set_state_observer(_on_circuit_breaker_state)
    except Exception:  # noqa: BLE001
        pass


_install_circuit_breaker_observer()


# ============================================================================
# 启动期熔断器状态发布（**本部署"熔断器状态序列一定存在"的唯一保证**）
# ============================================================================
#
# 【要治的病】告警 `CircuitBreakerMetricsMissing`（monitoring/circuit_breaker_alerts.yml §4.1）
#   的表达式是 `absent(yunshu_circuit_breaker_state) and absent(yunshu_circuit_breaker_trigger_total)`。
#   而 `export_prometheus()` 对"已登记但一条样本都没有"的族**只输出 # HELP/# TYPE**
#   （见本文件 2306-2323 行的遍历：counter/gauge 都先判 `name in self._counters/_gauges`）。
#   这是**有意为之的既有契约**——不在这里伪造样本，否则"没有数据"与"数据是 0"又被混为一谈。
#   ⇒ 于是"序列不存在"这件事只能靠**真的写一次埋点**来消除。
#
# 【为什么必须由启动路径显式调用，而不是在导入期自动发布】
#   本模块被大量单测/脚本导入；在导入期写全局采集器会让每个"只读导出"的调用方
#   凭空多出熔断器样本、污染断言。发布是一次**部署动作**（组合根 app_server.py 负责），
#   不该是库导入的副作用。
#
# 【名字从哪来：只发布**真实存在**的熔断器】
#   下表每一行都取自本仓**生产代码**里经全局注册表 `get_circuit_breaker(<字面名>)` 取用的调用点。
#   发布方式就是调用同一个访问点：`get_circuit_breaker(name)` 本身会
#   `_notify_state_observer(name, None, state)`（agent/circuit_breaker.py:686），经本模块的
#   `_on_circuit_breaker_state` 落到 `update_circuit_breaker_state()` ⇒ 写进
#   `yunshu_circuit_breaker_state`（set_gauge 幂等）。
#   【为什么走注册表名字而不是直接构造实例】只有"经注册表取得的名字"，发布出去的状态
#     才与真实业务路径**拿到的是同一个对象**；直接 `CircuitBreaker(...)` 构造的实例
#     （agent/capregistry/loader.py:181 的 `capregistry.{kind}`、agent/monitoring/prometheus.py:75
#     的 `prometheus-exporter`）**不在全局注册表里**，用同名去注册表取会拿到**另一个对象**
#     ⇒ 那条序列会是假的，故**刻意不发布**。
#   【本部署没有"无条件存在"的熔断器】实测 `python -c "import app_server"` 之后
#     `get_breaker_registry()` 为空、下表四个模块一个都没被加载 ⇒ 没有任何访问点会被无条件执行，
#     这正是"跑 45 分钟真实流量后仍是 0 条样本"的根因
#     （docs/closeout/监控清理_evidence_20261002/c2_longrun_report.md §3.3.1）。
#     所以"遍历已注册熔断器发布"在本部署等于发布空集，必须显式声明本部署的熔断器拓扑。
DEPLOYED_CIRCUIT_BREAKER_NAMES: tuple = (
    # 出域链路熔断（命中"读密钥→外发"链路时 force_open）
    "guardrails.egress_chain",   # agent/guardrails/egress_chain.py:381（常量定义于 :57）
    # 学习预算护栏（日预算耗尽时熔断）
    "learning_budget",           # agent/learning_budget.py:182（默认名定义于 :153）
    # Critic 质量评审熔断
    "critic",                    # agent/cognitive/critic.py:114
    # 输出 Schema 校验熔断
    "schema_validation",         # agent/guardrails/output_schema.py:202
)


def publish_deployed_circuit_breaker_states(names=None) -> list:
    """发布本部署真实存在的熔断器**当前状态**，返回实际发布成功的名字列表。

    [契约] 本函数只做"取一次访问点"，不改变任何熔断判定；幂等（同名重复调用仍写同一
    label_key，gauge 覆盖语义）。**失败隔离**：任何一步异常都被吞掉并继续，
    埋点缺一条序列远比启动/调用方被打断轻。
    [分层] 本模块在 monitoring（上层），import agent.circuit_breaker（下层）方向合法；
    调用方是组合根 app_server.py。反方向（circuit_breaker → 本模块）被
    tests/unit/test_circuit_breaker_layering.py 与 .importlinter 明确禁止。
    [回滚] 删除本函数与常量、并删掉 app_server.py 里的 `_publish_circuit_breaker_states` 调用即可。
    """
    published: list = []
    try:
        import agent.circuit_breaker as _cb
    except Exception:  # noqa: BLE001 熔断器模块不可用 ⇒ 无可发布
        return published

    # 观察钩子必须已在位，否则 get_circuit_breaker() 的发布会被静默丢弃。
    # 正常路径下 _install_circuit_breaker_observer() 已在模块导入时执行；
    # 这里只在被显式卸载过时补装，**不覆盖**调用方自定义的观察者。
    try:
        if getattr(_cb, "_state_observer", None) is None:
            _install_circuit_breaker_observer()
    except Exception:  # noqa: BLE001
        pass

    for name in (names if names is not None else DEPLOYED_CIRCUIT_BREAKER_NAMES):
        try:
            _cb.get_circuit_breaker(name)   # 访问点内即完成状态发布
            published.append(name)
        except Exception as exc:  # noqa: BLE001 单个熔断器失败不影响其余
            logger.warning(log_dict({'module_name': 'business_metrics',
                                     'action': 'circuit_breaker.publish_initial.failed',
                                     'breaker_name': name, 'error': str(exc)}))
    return published