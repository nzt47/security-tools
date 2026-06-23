"""WorkflowEngine — 工作流引擎

匹配→执行→返回 WorkflowResult。0 Token 消耗的本地规则处理层。
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Any
from .registry import RuleRegistry

logger = logging.getLogger(__name__)


@dataclass
class WorkflowResult:
    """工作流执行结果"""
    matched: bool = False
    rule_name: str = ""
    output: str = ""
    data: Any = None


class WorkflowEngine:
    """工作流引擎——匹配→执行"""

    def __init__(self):
        self.registry = RuleRegistry()

    def try_match(self, text: str) -> Optional[WorkflowResult]:
        """尝试匹配并执行规则

        Args:
            text: 用户输入文本

        Returns:
            匹配成功返回 WorkflowResult，匹配失败返回 None
        """
        for rule in self.registry.get_enabled():
            try:
                if rule.match_fn(text):
                    output = rule.execute_fn(text)
                    try:
                        logger.info("[WorkflowEngine] 规则匹配: %s → %s", rule.name, output[:60])
                    except Exception:
                        pass  # 日志异常不应影响匹配结果（Windows GBK 编码问题）
                    return WorkflowResult(
                        matched=True,
                        rule_name=rule.name,
                        output=output,
                    )
            except Exception as e:
                logger.warning("[WorkflowEngine] 规则执行异常 %s: %s", rule.name, e)
                continue
        return None

    def match(self, text: str) -> Optional[WorkflowResult]:
        """try_match 的别名"""
        return self.try_match(text)
