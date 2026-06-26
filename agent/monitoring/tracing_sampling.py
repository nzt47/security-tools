#!/usr/bin/env python3
"""
追踪采样机制模块

实现多种采样策略：
- 概率采样（基于trace_id哈希）
- 基于请求类型采样
- 基于延迟采样（慢请求优先）
- 基于错误采样（错误请求优先）
- 动态采样规则
"""

import time
import hashlib
import threading
from typing import Dict, Any, Callable, Optional, Set, List
from collections import defaultdict

# 采样决策结果
class SamplingDecision:
    """采样决策结果"""
    
    def __init__(self, sampled: bool, reason: str = "", attributes: Dict = None):
        self.sampled = sampled
        self.reason = reason
        self.attributes = attributes or {}
    
    def __bool__(self):
        return self.sampled
    
    def __repr__(self):
        return f"SamplingDecision(sampled={self.sampled}, reason={self.reason!r})"


class BaseSampler:
    """采样器基类"""
    
    def should_sample(self, trace_id: str, **kwargs) -> SamplingDecision:
        """
        判断是否采样
        
        Args:
            trace_id: 追踪ID
            **kwargs: 额外参数（service_name, operation, request_type等）
        
        Returns:
            SamplingDecision: 采样决策
        """
        raise NotImplementedError


class AlwaysOnSampler(BaseSampler):
    """始终采样"""
    
    def should_sample(self, trace_id: str, **kwargs) -> SamplingDecision:
        return SamplingDecision(True, "always_on")


class AlwaysOffSampler(BaseSampler):
    """从不采样"""
    
    def should_sample(self, trace_id: str, **kwargs) -> SamplingDecision:
        return SamplingDecision(False, "always_off")


class ProbabilitySampler(BaseSampler):
    """概率采样器
    
    基于trace_id的哈希值进行概率采样，确保相同trace_id始终得到相同的采样结果
    """
    
    def __init__(self, ratio: float = 0.1):
        """
        Args:
            ratio: 采样比例 (0.0-1.0)
        """
        self.ratio = max(0.0, min(1.0, ratio))
    
    def should_sample(self, trace_id: str, **kwargs) -> SamplingDecision:
        if self.ratio >= 1.0:
            return SamplingDecision(True, "probability_100%")
        if self.ratio <= 0.0:
            return SamplingDecision(False, "probability_0%")
        
        # 使用trace_id的哈希值计算采样概率
        hash_val = int(hashlib.md5(trace_id.encode()).hexdigest()[:8], 16)
        threshold = int(self.ratio * 0xFFFFFFFF)
        
        sampled = hash_val <= threshold
        reason = f"probability_{self.ratio*100:.1f}%"
        
        return SamplingDecision(sampled, reason, {"ratio": self.ratio})


class RequestTypeSampler(BaseSampler):
    """基于请求类型的采样器
    
    根据请求类型设置不同的采样比例
    """
    
    def __init__(self, type_ratios: Dict[str, float], default_ratio: float = 0.1):
        """
        Args:
            type_ratios: 请求类型到采样比例的映射
            default_ratio: 默认采样比例（当请求类型不在映射中时）
        """
        self.type_ratios = type_ratios
        self.default_ratio = default_ratio
        self._prob_samplers = {}
        
        # 预创建概率采样器缓存
        for req_type, ratio in type_ratios.items():
            self._prob_samplers[req_type] = ProbabilitySampler(ratio)
        self._default_sampler = ProbabilitySampler(default_ratio)
    
    def should_sample(self, trace_id: str, request_type: str = None, **kwargs) -> SamplingDecision:
        sampler = self._prob_samplers.get(request_type) or self._default_sampler
        decision = sampler.should_sample(trace_id)
        decision.reason = f"request_type={request_type or 'unknown'}, {decision.reason}"
        decision.attributes["request_type"] = request_type
        return decision


class LatencyBasedSampler(BaseSampler):
    """基于延迟的采样器
    
    慢请求优先采样，用于捕获性能问题
    """
    
    def __init__(self, 
                 fast_ratio: float = 0.01,
                 medium_ratio: float = 0.1,
                 slow_ratio: float = 0.5,
                 fast_threshold_ms: int = 100,
                 slow_threshold_ms: int = 1000):
        """
        Args:
            fast_ratio: 快速请求采样比例
            medium_ratio: 中等请求采样比例
            slow_ratio: 慢请求采样比例
            fast_threshold_ms: 快速请求阈值（毫秒）
            slow_threshold_ms: 慢请求阈值（毫秒）
        """
        self.fast_ratio = fast_ratio
        self.medium_ratio = medium_ratio
        self.slow_ratio = slow_ratio
        self.fast_threshold_ms = fast_threshold_ms
        self.slow_threshold_ms = slow_threshold_ms
        
        self._fast_sampler = ProbabilitySampler(fast_ratio)
        self._medium_sampler = ProbabilitySampler(medium_ratio)
        self._slow_sampler = ProbabilitySampler(slow_ratio)
    
    def should_sample(self, trace_id: str, duration_ms: float = None, **kwargs) -> SamplingDecision:
        if duration_ms is None:
            # 无法确定延迟，使用默认中等采样
            decision = self._medium_sampler.should_sample(trace_id)
            decision.reason = f"latency_unknown, {decision.reason}"
            return decision
        
        if duration_ms < self.fast_threshold_ms:
            sampler = self._fast_sampler
            latency_type = "fast"
        elif duration_ms < self.slow_threshold_ms:
            sampler = self._medium_sampler
            latency_type = "medium"
        else:
            sampler = self._slow_sampler
            latency_type = "slow"
        
        decision = sampler.should_sample(trace_id)
        decision.reason = f"latency_{latency_type}({duration_ms:.1f}ms), {decision.reason}"
        decision.attributes["latency_type"] = latency_type
        decision.attributes["duration_ms"] = duration_ms
        
        return decision


class ErrorBasedSampler(BaseSampler):
    """基于错误的采样器
    
    错误请求优先采样
    """
    
    def __init__(self, error_ratio: float = 1.0, success_ratio: float = 0.1):
        """
        Args:
            error_ratio: 错误请求采样比例
            success_ratio: 成功请求采样比例
        """
        self.error_sampler = ProbabilitySampler(error_ratio)
        self.success_sampler = ProbabilitySampler(success_ratio)
    
    def should_sample(self, trace_id: str, has_error: bool = False, **kwargs) -> SamplingDecision:
        sampler = self.error_sampler if has_error else self.success_sampler
        decision = sampler.should_sample(trace_id)
        decision.reason = f"error={has_error}, {decision.reason}"
        decision.attributes["has_error"] = has_error
        return decision


class RateLimitedSampler(BaseSampler):
    """速率限制采样器
    
    限制每秒最大采样数量，防止采样过多
    """
    
    def __init__(self, max_samples_per_second: int = 100, delegate: BaseSampler = None):
        """
        Args:
            max_samples_per_second: 每秒最大采样数
            delegate: 委托采样器（用于实际采样决策）
        """
        self.max_samples_per_second = max_samples_per_second
        self.delegate = delegate or ProbabilitySampler(1.0)
        
        # 速率限制状态
        self._lock = threading.Lock()
        self._current_second = int(time.time())
        self._sample_count = 0
    
    def _acquire_slot(self) -> bool:
        """尝试获取采样槽位"""
        now = int(time.time())
        
        with self._lock:
            if now != self._current_second:
                # 新的一秒，重置计数器
                self._current_second = now
                self._sample_count = 0
            
            if self._sample_count < self.max_samples_per_second:
                self._sample_count += 1
                return True
            return False
    
    def should_sample(self, trace_id: str, **kwargs) -> SamplingDecision:
        # 先检查速率限制
        if not self._acquire_slot():
            return SamplingDecision(False, "rate_limited")
        
        # 再调用委托采样器
        decision = self.delegate.should_sample(trace_id, **kwargs)
        return decision


class CompositeSampler(BaseSampler):
    """组合采样器
    
    支持多个采样器的组合，可配置采样策略（AND/OR）
    """
    
    def __init__(self, samplers: List[BaseSampler], strategy: str = "AND"):
        """
        Args:
            samplers: 采样器列表
            strategy: 组合策略 "AND" 或 "OR"
        """
        self.samplers = samplers
        self.strategy = strategy.upper()
        
        if self.strategy not in ("AND", "OR"):
            raise ValueError(f"Invalid strategy: {strategy}, must be 'AND' or 'OR'")
    
    def should_sample(self, trace_id: str, **kwargs) -> SamplingDecision:
        decisions = []
        reasons = []
        
        for sampler in self.samplers:
            decision = sampler.should_sample(trace_id, **kwargs)
            decisions.append(decision)
            reasons.append(decision.reason)
        
        if self.strategy == "AND":
            sampled = all(decisions)
        else:  # OR
            sampled = any(decisions)
        
        reason = f"composite_{self.strategy}({', '.join(reasons)})"
        attributes = {"samplers": len(self.samplers), "strategy": self.strategy}
        
        return SamplingDecision(sampled, reason, attributes)


class DynamicSampler(BaseSampler):
    """动态采样器
    
    根据运行时统计信息动态调整采样比例
    """
    
    def __init__(self, 
                 target_samples_per_second: int = 100,
                 min_ratio: float = 0.01,
                 max_ratio: float = 1.0,
                 adjustment_interval: int = 10):
        """
        Args:
            target_samples_per_second: 目标每秒采样数
            min_ratio: 最小采样比例
            max_ratio: 最大采样比例
            adjustment_interval: 调整间隔（秒）
        """
        self.target_samples_per_second = target_samples_per_second
        self.min_ratio = min_ratio
        self.max_ratio = max_ratio
        self.adjustment_interval = adjustment_interval
        
        self._current_ratio = 0.5
        self._prob_sampler = ProbabilitySampler(self._current_ratio)
        
        # 统计信息
        self._lock = threading.Lock()
        self._request_count = 0
        self._sample_count = 0
        self._last_adjustment = time.time()
    
    def _adjust_ratio(self):
        """根据统计信息调整采样比例"""
        now = time.time()
        elapsed = now - self._last_adjustment
        
        if elapsed < self.adjustment_interval:
            return
        
        with self._lock:
            if self._request_count > 0:
                actual_rate = self._sample_count / elapsed
                ratio_adjustment = self.target_samples_per_second / max(actual_rate, 1)
                
                new_ratio = self._current_ratio * ratio_adjustment
                self._current_ratio = max(self.min_ratio, min(self.max_ratio, new_ratio))
                
                self._prob_sampler = ProbabilitySampler(self._current_ratio)
            
            # 重置计数器
            self._request_count = 0
            self._sample_count = 0
            self._last_adjustment = now
    
    def should_sample(self, trace_id: str, **kwargs) -> SamplingDecision:
        # 记录请求数
        with self._lock:
            self._request_count += 1
        
        # 尝试采样
        decision = self._prob_sampler.should_sample(trace_id)
        
        if decision.sampled:
            with self._lock:
                self._sample_count += 1
        
        # 异步调整比例（非阻塞）
        self._adjust_ratio()
        
        decision.reason = f"dynamic(ratio={self._current_ratio:.4f}), {decision.reason}"
        decision.attributes["current_ratio"] = self._current_ratio
        
        return decision


class CustomRuleSampler(BaseSampler):
    """自定义规则采样器
    
    支持基于任意条件的采样规则
    """
    
    def __init__(self, rules: List[Dict]):
        """
        Args:
            rules: 规则列表，每个规则包含:
                - condition: 条件函数或判断逻辑
                - ratio: 采样比例
                - description: 规则描述
        """
        self.rules = rules
        self._rule_samplers = []
        
        for rule in rules:
            ratio = rule.get("ratio", 0.1)
            self._rule_samplers.append((rule, ProbabilitySampler(ratio)))
    
    def should_sample(self, trace_id: str, **kwargs) -> SamplingDecision:
        for rule, sampler in self._rule_samplers:
            condition = rule.get("condition")
            
            # 检查条件是否满足
            if callable(condition):
                try:
                    matches = condition(**kwargs)
                except Exception:
                    matches = False
            else:
                # 简单的属性匹配
                matches = True
                for key, expected in rule.get("match", {}).items():
                    if kwargs.get(key) != expected:
                        matches = False
                        break
            
            if matches:
                decision = sampler.should_sample(trace_id)
                decision.reason = f"rule='{rule.get('description', 'unnamed')}', {decision.reason}"
                decision.attributes["rule"] = rule.get("description", "unnamed")
                return decision
        
        # 没有匹配的规则，默认不采样
        return SamplingDecision(False, "no_matching_rule")


class SamplingManager:
    """采样管理器
    
    管理多个采样器，根据请求属性选择合适的采样策略
    """
    
    def __init__(self):
        self._samplers: Dict[str, BaseSampler] = {}
        self._default_sampler = ProbabilitySampler(0.1)
    
    def register_sampler(self, name: str, sampler: BaseSampler):
        """注册采样器"""
        self._samplers[name] = sampler
    
    def get_sampler(self, name: str) -> Optional[BaseSampler]:
        """获取采样器"""
        return self._samplers.get(name)
    
    def sample(self, 
               trace_id: str, 
               sampler_name: str = None,
               **kwargs) -> SamplingDecision:
        """
        执行采样决策
        
        Args:
            trace_id: 追踪ID
            sampler_name: 采样器名称（可选）
            **kwargs: 额外参数
        
        Returns:
            SamplingDecision: 采样决策
        """
        sampler = self._samplers.get(sampler_name) if sampler_name else self._default_sampler
        
        if sampler is None:
            return SamplingDecision(False, "sampler_not_found")
        
        try:
            return sampler.should_sample(trace_id, **kwargs)
        except Exception as e:
            # 采样器出错时，使用默认策略
            return SamplingDecision(False, f"sampler_error: {str(e)}")


# 全局采样管理器实例
_global_sampling_manager = None

def get_sampling_manager() -> SamplingManager:
    """获取全局采样管理器"""
    global _global_sampling_manager
    if _global_sampling_manager is None:
        _global_sampling_manager = SamplingManager()
    return _global_sampling_manager


def setup_default_samplers():
    """设置默认采样器"""
    manager = get_sampling_manager()
    
    # 注册常用采样器
    manager.register_sampler("always_on", AlwaysOnSampler())
    manager.register_sampler("always_off", AlwaysOffSampler())
    manager.register_sampler("probability_10", ProbabilitySampler(0.1))
    manager.register_sampler("probability_50", ProbabilitySampler(0.5))
    
    # 请求类型采样器
    request_type_sampler = RequestTypeSampler({
        "health": 0.01,        # 健康检查请求低采样
        "api": 0.1,            # API请求正常采样
        "websocket": 0.05,     # WebSocket请求低采样
        "background": 0.01,    # 后台任务低采样
        "critical": 1.0        # 关键请求全采样
    })
    manager.register_sampler("request_type", request_type_sampler)
    
    # 延迟采样器
    latency_sampler = LatencyBasedSampler(
        fast_ratio=0.01,
        medium_ratio=0.1,
        slow_ratio=0.8
    )
    manager.register_sampler("latency", latency_sampler)
    
    # 错误采样器
    error_sampler = ErrorBasedSampler(
        error_ratio=1.0,
        success_ratio=0.05
    )
    manager.register_sampler("error", error_sampler)
    
    # 速率限制采样器（包装默认概率采样器）
    rate_limited_sampler = RateLimitedSampler(
        max_samples_per_second=100,
        delegate=ProbabilitySampler(0.1)
    )
    manager.register_sampler("rate_limited", rate_limited_sampler)
    
    # 动态采样器
    dynamic_sampler = DynamicSampler(
        target_samples_per_second=50,
        min_ratio=0.01,
        max_ratio=1.0
    )
    manager.register_sampler("dynamic", dynamic_sampler)


# 采样装饰器
def sampled(sampler_name: str = None, **sampling_kwargs):
    """
    采样装饰器
    
    用于控制函数是否被追踪
    
    Args:
        sampler_name: 采样器名称
        **sampling_kwargs: 额外的采样参数
    
    Usage:
        @sampled("request_type", request_type="api")
        def handle_request():
            pass
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            from .tracing import get_trace_id
            
            trace_id = get_trace_id()
            if trace_id:
                manager = get_sampling_manager()
                decision = manager.sample(trace_id, sampler_name, **sampling_kwargs)
                
                if not decision.sampled:
                    # 不采样，直接执行函数
                    return func(*args, **kwargs)
            
            # 采样或无trace_id，正常执行
            return func(*args, **kwargs)
        
        return wrapper
    return decorator


# OpenTelemetry 采样器适配器
try:
    from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision
    from opentelemetry.trace import Link, SpanKind
    
    class OTelSamplerAdapter(Sampler):
        """OpenTelemetry采样器适配器
        
        将自定义采样器适配为OpenTelemetry采样器
        """
        
        def __init__(self, sampler: BaseSampler):
            self._sampler = sampler
        
        def should_sample(self, parent_context, trace_id, name, kind, attributes, links):
            trace_id_str = format(trace_id, '032x')
            
            # 从属性中提取额外参数
            kwargs = {}
            if attributes:
                for key, value in attributes.items():
                    kwargs[key] = value
            
            decision = self._sampler.should_sample(trace_id_str, **kwargs)
            
            if decision.sampled:
                return SamplingResult(Decision.RECORD_AND_SAMPLE)
            else:
                return SamplingResult(Decision.DROP)
        
        @property
        def description(self):
            return f"OTelSamplerAdapter({type(self._sampler).__name__})"

except ImportError:
    # OpenTelemetry不可用时的降级实现
    OTelSamplerAdapter = None


__all__ = [
    'SamplingDecision',
    'BaseSampler',
    'AlwaysOnSampler',
    'AlwaysOffSampler',
    'ProbabilitySampler',
    'RequestTypeSampler',
    'LatencyBasedSampler',
    'ErrorBasedSampler',
    'RateLimitedSampler',
    'CompositeSampler',
    'DynamicSampler',
    'CustomRuleSampler',
    'SamplingManager',
    'get_sampling_manager',
    'setup_default_samplers',
    'sampled',
    'OTelSamplerAdapter'
]