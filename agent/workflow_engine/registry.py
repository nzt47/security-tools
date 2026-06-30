"""Rule Registry — 规则注册中心

Rule 数据结构 + RuleRegistry（注册/查询/排序）
"""

from dataclasses import dataclass, field
from typing import Callable, Optional, List
import logging
import json
import uuid

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



@dataclass
class Rule:
    """工作流规则"""
    name: str
    description: str
    match_fn: Callable[..., bool]
    execute_fn: Callable[..., str]
    priority: int = 50
    category: str = "general"
    enabled: bool = True


class RuleRegistry:
    """规则注册中心"""

    def __init__(self):
        self._rules: List[Rule] = []

    def register(self, rule: Rule):
        """注册一条规则"""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)
        logger.debug("[RuleRegistry] 已注册规则: %s (priority=%d)", rule.name, rule.priority)

    def unregister(self, name: str):
        """注销规则"""
        self._rules = [r for r in self._rules if r.name != name]

    def get_enabled(self) -> List[Rule]:
        """获取所有启用规则（按优先级降序）"""
        return [r for r in self._rules if r.enabled]

    def get_by_category(self, category: str) -> List[Rule]:
        return [r for r in self._rules if r.category == category and r.enabled]

    def count(self) -> int:
        return len(self._rules)

    def decorator(self, name: str = "", description: str = "",
                  priority: int = 50, category: str = "general"):
        """装饰器模式注册"""
        def _wrapper(fn):
            rule = Rule(
                name=name or fn.__name__,
                description=description or fn.__doc__ or "",
                match_fn=fn,
                execute_fn=fn,
                priority=priority,
                category=category,
            )
            self.register(rule)
            return fn
        return _wrapper

    def clear(self):
        self._rules.clear()


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "registry",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
