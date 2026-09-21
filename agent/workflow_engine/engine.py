"""WorkflowEngine — 工作流引擎

匹配→执行→返回 WorkflowResult。0 Token 消耗的本地规则处理层。
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from .registry import RuleRegistry

logger = logging.getLogger(__name__)

# ── 规则层置信度出口（L26）────────────────────────────────────────────
# 【缺陷事实】try_match 原先把 WorkflowResult.confidence 硬编码为 1.0
#   ⇒ 规则层读数恒为常数、不携带信息，无法参与校准/拒识
#   （TASK-10 基线 scripts/run_routing_reliability.py:209-211 把该层记为
#    confidence_kind="hardcoded_1.0"）。
# 【加固方式 · 向后兼容】保留默认 1.0，但允许**规则自己声明** confidence；
#   另给一个"按命中强度（rule.priority）保守推导"的可选 fallback。
#   ⚠️ 推导**不默认启用**：tests/unit/test_workflow_engine_comprehensive.py:462
#   断言"默认 priority=50 的自定义规则命中后 confidence == 1.0"，
#   自动推导会让该断言变红 ⇒ 校准必须由维护者显式选择（见 resolve_rule_confidence 文档）。
# 【不易】未声明 confidence 的规则读数与加固前**逐位相同**（仍是 1.0），
#   既有判定结果（matched / rule_name / intent / output）一字未动。

#: 规则未声明置信度时的默认值（保持既有语义：1.0）
DEFAULT_RULE_CONFIDENCE = 1.0

#: 按命中强度推导时的下界（priority 极低也不给出 0 置信度，避免"未命中"歧义）
MIN_DERIVED_CONFIDENCE = 0.05


def _valid_confidence(value: Any) -> Optional[float]:
    """把候选值规整为 [0.0, 1.0] 的 float；非法/越界/布尔返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v < 0.0 or v > 1.0:   # NaN 或越界
        return None
    return v


def confidence_from_priority(rule: Any) -> float:
    """按"命中强度"（rule.priority）**保守**推导规则层置信度。

    映射：clamp(priority / 100.0, MIN_DERIVED_CONFIDENCE, 1.0)
      - priority=100（最特异的 8 条内置规则里的时间/日期查询）⇒ 1.0
      - priority=50（confirmation 等宽泛口头语规则）⇒ 0.5
      - priority 缺失/非法 ⇒ DEFAULT_RULE_CONFIDENCE

    保守性：结果**不会高于**规则自报的特异性（priority 上限 100 ⇒ 上限 1.0），
    也不会给出 0（0 留给"未命中"，避免与 matched=False 混淆）。
    本函数**不被 try_match 自动调用**（见文件头 ⚠️）；需要校准时在 try_match 的
    返回处改为：
        confidence=resolve_rule_confidence(rule, fallback=confidence_from_priority)
    """
    raw = getattr(rule, "priority", None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_RULE_CONFIDENCE
    try:
        p = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_RULE_CONFIDENCE
    if p != p:
        return DEFAULT_RULE_CONFIDENCE
    return max(MIN_DERIVED_CONFIDENCE, min(1.0, p / 100.0))


def resolve_rule_confidence(
    rule: Any, *, fallback: Optional[Callable[[Any], float]] = None,
) -> float:
    """解析"规则层命中"的置信度（L26 最小出口，默认行为与加固前逐位一致）。

    优先级：
      ① 规则对象上显式声明的 confidence（0.0~1.0 的数值）→ 原样使用；
         （Rule 是普通 dataclass，既可子类化声明，也可运行时赋值
           rule.confidence = 0.8；registry.py 无需改动）
      ② 未声明/非法 + 给了 fallback 回调 → fallback(rule)（例如
         confidence_from_priority，做"按命中强度"校准）；
      ③ 其余 → DEFAULT_RULE_CONFIDENCE（1.0，既有读数不变）。

    fallback 的返回值同样会被规整；非法则退回默认值，绝不抛异常。
    """
    declared = _valid_confidence(getattr(rule, "confidence", None))
    if declared is not None:
        return declared
    if fallback is not None:
        try:
            derived = _valid_confidence(fallback(rule))
        except Exception as e:  # noqa: BLE001 校准钩子故障不得影响匹配
            logger.debug("[WorkflowEngine] 置信度推导失败（退回默认）: %s", e)
            derived = None
        if derived is not None:
            return derived
    return DEFAULT_RULE_CONFIDENCE


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
                        # 【L26】置信度出口：默认仍为 1.0（未声明的规则读数逐位不变）；
                        # 规则声明了 confidence 即生效；需要按命中强度校准时给
                        # fallback=confidence_from_priority（见 resolve_rule_confidence）
                        confidence=resolve_rule_confidence(rule),
                        execution_time_ms=round(elapsed, 2),
                    )
            except Exception as e:
                logger.warning("[WorkflowEngine] 规则执行异常 %s: %s", rule.name, e)
                continue
        return WorkflowResult(matched=False)

    def match(self, text: str) -> WorkflowResult:
        """try_match 的别名"""
        return self.try_match(text)
