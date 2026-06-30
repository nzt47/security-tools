"""WorkflowEngine — 工作流引擎

匹配→执行→返回 WorkflowResult。0 Token 消耗的本地规则处理层。
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Any
from .registry import RuleRegistry

logger = logging.getLogger(__name__)


@dataclass
class WorkflowResult:
    """工作流执行结果"""
    matched: bool = False
    rule_name: str = ""
    intent: str = ""
    output: str = ""
    data: Any = None
    confidence: float = 1.0
    execution_time_ms: float = 0.0


class WorkflowEngine:
    """工作流引擎——匹配→执行"""

    def __init__(self):
        self.registry = RuleRegistry()

    def try_match(self, text: str) -> WorkflowResult:
        """尝试匹配并执行规则

        Args:
            text: 用户输入文本

        Returns:
            WorkflowResult — matched=True 表示命中规则，matched=False 表示无匹配
        """
        t0 = time.time()
        for rule in self.registry.get_enabled():
            try:
                if rule.match_fn(text):
                    output = rule.execute_fn(text)
                    elapsed = (time.time() - t0) * 1000
                    try:
                        logger.info("[WorkflowEngine] 规则匹配: %s → %s", rule.name, output[:60])
                    except Exception:
                        pass  # 日志异常不应影响匹配结果（Windows GBK 编码问题）
                    return WorkflowResult(
                        matched=True,
                        rule_name=rule.name,
                        intent=rule.name,
                        output=output,
                        confidence=1.0,
                        execution_time_ms=round(elapsed, 2),
                    )
            except Exception as e:
                logger.warning("[WorkflowEngine] 规则执行异常 %s: %s", rule.name, e)
                continue
        return WorkflowResult(matched=False)

    def match(self, text: str) -> WorkflowResult:
        """try_match 的别名"""
        return self.try_match(text)
