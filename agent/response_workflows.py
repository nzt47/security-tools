"""响应工作流层 — 意图路由 + 模板匹配（0 Token 消耗）

架构定位（三层漏斗第 2 层 — 模板语义层）:
    orchestrator.process()
        ├── 第一步 WorkflowEngine.try_match  (规则层，8 条高频规则)
        ├── 第三步 IntentRouter.classify      (本模块，模板语义层)
        │     └── ResponseTemplates.for_intent → 命中则跳过 LLM
        └── 第四步 _call_llm                  (大模型层)

【不易】
  - IntentRouter.classify 为纯函数：零 LLM 调用、零外部 IO、零副作用
  - 与 WorkflowEngine 8 条规则"互补为主 + 高频意图防御性冗余"：
    WorkflowEngine 优先处理时间/日期/问候/告别/感谢/确认/计算/健康；
    本模块额外覆盖身份/能力/天气/闲聊等对话意图，并对 time_query/greeting
    做防御性冗余分类（WorkflowEngine 失效时仍可识别）。time_query 模板返回
    None 交 LLM 兜底——纯函数无法读取系统时钟，违零 IO 约束。
  - Confidence 枚举与 orchestrator.py L293 `confidence.name` 契约对齐
【变易】
  - 意图规则外部化（_INTENT_RULES 列表），支持运行时 register_intent 扩展
  - 模板支持时段感知（hour 参数驱动问候语分时）
【简易】
  - 复用 message_handler 的正则模式风格，单一文件，无新依赖
  - 无模板匹配返回 None，调用方继续 LLM（降级链清晰）

修复背景: 原 orchestrator.py L251 `from agent.response_workflows import ...`
触发 ImportError 被 L297 `except ImportError: pass` 静默吞掉，导致模板语义层
从未执行。本模块补齐该断裂点。
"""

from __future__ import annotations

import re
import logging
from datetime import datetime
from enum import Enum
from typing import Optional, List, Callable, Tuple

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Confidence — 置信度枚举
# ════════════════════════════════════════════════════════════

class Confidence(Enum):
    """意图分类置信度分级

    与 orchestrator.py L293 `confidence.name` 契约对齐（Enum.name 返回 "HIGH" 等）。
    """
    HIGH = 0.9       # 正则强匹配，可直接走模板
    MEDIUM = 0.6     # 弱匹配，模板回复但可被 follow_up 覆盖
    LOW = 0.3        # 模糊匹配，建议降级 LLM


# ════════════════════════════════════════════════════════════
#  意图规则定义
# ════════════════════════════════════════════════════════════

# 意图名常量（与 ResponseTemplates 模板键对齐）
INTENT_TIME_QUERY = "time_query"       # 时间查询（防御性冗余：模板返回 None 交 LLM）
INTENT_IDENTITY = "identity"           # 你是谁/你叫什么
INTENT_CAPABILITY = "capability"       # 你能做什么/有什么功能
INTENT_WEATHER = "weather"             # 天气查询
INTENT_GREETING = "greeting"           # 问候（防御性冗余 + 模板可分时问候）
INTENT_SIMPLE_CHAT = "simple_chat"     # 简单闲聊
INTENT_DISSATISFACTION = "dissatisfaction"  # 不满/纠正（降级 LLM）
INTENT_FOLLOW_UP = "follow_up"         # 追问（降级 LLM）
INTENT_UNKNOWN = "unknown"             # 未知（继续 LLM）


class _IntentRule:
    """意图规则（内部数据结构）

    Args:
        name: 意图名（与 ResponseTemplates 模板键对齐）
        patterns: 正则列表，任一匹配即命中
        confidence: 命中置信度
        priority: 优先级（数字大优先），用于多规则同时命中时排序
    """
    __slots__ = ("name", "patterns", "confidence", "priority")

    def __init__(self, name: str, patterns: List[re.Pattern],
                 confidence: Confidence, priority: int = 0):
        self.name = name
        self.patterns = patterns
        self.confidence = confidence
        self.priority = priority


# 默认意图规则集（与 WorkflowEngine 8 条规则互补为主 + 高频意图防御性冗余）
# 【变易】可通过 IntentRouter.register_intent 运行时扩展
_DEFAULT_RULES: List[_IntentRule] = [
    # 防御性冗余：WorkflowEngine 的 check_time 规则失效时兜底分类
    # 模板不生成回复（纯函数无法读时钟），交 LLM 兜底
    _IntentRule(
        name=INTENT_TIME_QUERY,
        patterns=[
            re.compile(r"(?i)(现在|当前).*(几点|时间)"),
            re.compile(r"(?i)^几点了"),
            re.compile(r"(?i)什么时间"),
            re.compile(r"(?i)几点钟"),
        ],
        confidence=Confidence.HIGH,
        priority=95,
    ),
    _IntentRule(
        name=INTENT_IDENTITY,
        patterns=[
            re.compile(r"(?i)你(是|叫|名字是)(什么|谁|啥)"),
            re.compile(r"(?i)你(是|叫)啥"),
            re.compile(r"(?i)(who are you|what.*your name)"),
        ],
        confidence=Confidence.HIGH,
        priority=90,
    ),
    _IntentRule(
        name=INTENT_CAPABILITY,
        patterns=[
            re.compile(r"(?i)你(能|可以|会)(做|帮|干)(什么|啥)"),
            re.compile(r"(?i)(有什么|有哪些)(功能|能力|本事)"),
            re.compile(r"(?i)(帮|协助).*(什么|啥)"),
            re.compile(r"(?i)what can you do"),
        ],
        confidence=Confidence.HIGH,
        priority=85,
    ),
    _IntentRule(
        name=INTENT_WEATHER,
        patterns=[
            re.compile(r"(?i)(今天|明天|后天|这|那).*(天气|温度|下雨|下雪)"),
            re.compile(r"(?i)天气(怎么样|如何|预报)"),
            re.compile(r"(?i)(weather|temperature)"),
        ],
        confidence=Confidence.HIGH,
        priority=80,
    ),
    # 防御性冗余：WorkflowEngine 的 greeting 规则失效时兜底，模板可分时问候
    _IntentRule(
        name=INTENT_GREETING,
        patterns=[
            re.compile(r"(?i)^(早上好|下午好|晚上好|中午好|凌晨好|你好|您好|大家好)"),
            re.compile(r"(?i)^(hi|hello|hey)\b"),
        ],
        confidence=Confidence.HIGH,
        priority=75,
    ),
    _IntentRule(
        name=INTENT_DISSATISFACTION,
        patterns=[
            re.compile(r"(?i)(你(是不是)?(不|没|无法|不能)|怎么(还)?(不|没))"),
            re.compile(r"(?i)(回答|回复|答案)(错误|不对|错的|不准确)"),
            re.compile(r"(?i)(无语|算了|懒得|说了你也不懂)"),
            re.compile(r"(?i)(重新|再(次)?).{0,4}(回答|说|解释|讲)"),
        ],
        confidence=Confidence.HIGH,
        priority=70,
    ),
    _IntentRule(
        name=INTENT_FOLLOW_UP,
        patterns=[
            re.compile(r"(?i)^(那|然后|所以|接着|还有|另外|不过|但是|可是|然而)"),
            re.compile(r"(?i)^(为什么|怎么|如何|什么|哪里|谁|什么时候|哪个)"),
            re.compile(r"(?i)(具[体]?[一]?点|详细|解释|说说|继续|接着说)"),
        ],
        confidence=Confidence.MEDIUM,
        priority=60,
    ),
    _IntentRule(
        name=INTENT_SIMPLE_CHAT,
        patterns=[
            re.compile(r"(?i)^(无聊|好无聊|发呆|陪我聊天|聊聊天|说说话)"),
            re.compile(r"(?i)^(你(忙吗|在干嘛|干什么呢))"),
        ],
        confidence=Confidence.MEDIUM,
        priority=40,
    ),
]


# ════════════════════════════════════════════════════════════
#  IntentRouter — 意图路由器
# ════════════════════════════════════════════════════════════

class IntentRouter:
    """意图路由器 — 纯函数式意图分类

    【不易】classify 为静态方法，零 LLM、零 IO、零副作用
    【变易】支持 register_intent 运行时扩展规则
    【简易】按 priority 降序遍历，首个匹配即返回
    """

    # 类级规则表（register_intent 修改此列表）
    _rules: List[_IntentRule] = list(_DEFAULT_RULES)

    @staticmethod
    def classify(user_input: str) -> Tuple[str, Confidence]:
        """对用户输入进行意图分类

        Args:
            user_input: 用户原始输入文本

        Returns:
            (intent_name, confidence) 元组。未匹配任何规则时返回
            (INTENT_UNKNOWN, Confidence.LOW)。

        日志分支（便于排查分类错误，均 debug 级避免刷屏）:
            - classify.empty_input  : 空输入短路
            - classify.hit          : 命中规则（含意图/置信度/预览/规则数）
            - classify.miss         : 全部规则未命中（含预览/规则数，便于排查误分类）
        """
        # 分支1: 空输入短路
        if not user_input or not user_input.strip():
            logger.debug(log_dict_safe({
                "module_name": "response_workflows",
                "action": "intent_router.classify.empty_input",
                "input_preview": "" if not user_input else user_input[:50],
            }))
            return (INTENT_UNKNOWN, Confidence.LOW)

        text = user_input.strip()
        rules_sorted = sorted(IntentRouter._rules, key=lambda r: r.priority, reverse=True)

        # 分支2: 按 priority 降序遍历，首个匹配即返回（高优先级规则先判）
        for rule in rules_sorted:
            for pattern in rule.patterns:
                if pattern.search(text):
                    logger.debug(log_dict_safe({
                        "module_name": "response_workflows",
                        "action": "intent_router.classify.hit",
                        "intent": rule.name,
                        "confidence": rule.confidence.name,
                        "input_preview": text[:50],
                        "priority": rule.priority,
                        "rules_total": len(rules_sorted),
                    }))
                    return (rule.name, rule.confidence)

        # 分支3: 全部规则未命中，降级 LLM（记录预览便于排查误分类）
        logger.debug(log_dict_safe({
            "module_name": "response_workflows",
            "action": "intent_router.classify.miss",
            "input_preview": text[:50],
            "rules_total": len(rules_sorted),
        }))
        return (INTENT_UNKNOWN, Confidence.LOW)

    @staticmethod
    def register_intent(name: str, patterns: List[str],
                        confidence: Confidence = Confidence.MEDIUM,
                        priority: int = 50) -> None:
        """运行时注册新意图规则

        Args:
            name: 意图名
            patterns: 正则表达式字符串列表
            confidence: 置信度
            priority: 优先级
        """
        compiled = [re.compile(p) for p in patterns]
        IntentRouter._rules.append(_IntentRule(name, compiled, confidence, priority))
        logger.info(log_dict_safe({
            "module_name": "response_workflows",
            "action": "intent_router.register",
            "intent": name,
            "priority": priority,
        }))

    @staticmethod
    def _reset_rules() -> None:
        """重置为默认规则（仅供测试使用）"""
        IntentRouter._rules = list(_DEFAULT_RULES)


# ════════════════════════════════════════════════════════════
#  ResponseTemplates — 模板回复
# ════════════════════════════════════════════════════════════

class ResponseTemplates:
    """意图模板回复库

    【不易】for_intent 为纯函数，无副作用
    【变易】模板支持时段感知（hour 驱动问候分时）
    【简易】无匹配返回 None，调用方继续 LLM
    """

    @staticmethod
    def for_intent(intent: str,
                   confidence: Optional[Confidence] = None,
                   hour: Optional[int] = None) -> Optional[str]:
        """根据意图返回模板回复

        Args:
            intent: IntentRouter.classify 返回的意图名
            confidence: 置信度（LOW 时倾向于不返回模板，降级 LLM）
            hour: 当前小时（0-23），用于时段感知问候

        Returns:
            模板回复字符串；无匹配或低置信度追问类意图返回 None（继续 LLM）
        """
        # 追问/不满类意图不返回模板，交由 LLM 处理（需上下文理解）
        if intent in (INTENT_FOLLOW_UP, INTENT_DISSATISFACTION, INTENT_UNKNOWN):
            return None

        # time_query 不返回模板：纯函数无法读取系统时钟（违零 IO 约束），
        # 交由 WorkflowEngine（已优先处理）或 LLM 兜底
        if intent == INTENT_TIME_QUERY:
            return None

        # LOW 置信度不返回模板（避免误模板化）
        if confidence == Confidence.LOW:
            return None

        h = hour if hour is not None else datetime.now().hour
        greeting = ResponseTemplates._time_greeting(h)

        if intent == INTENT_IDENTITY:
            return ("我是云枢，你的本地智能助手。"
                    "我可以帮你查资料、处理文件、运行脚本、管理任务等。"
                    "有什么我能帮你的吗？")

        if intent == INTENT_CAPABILITY:
            return ("我能帮你做这些事：\n"
                    "• 网页搜索与信息抓取\n"
                    "• 文件读写与目录管理\n"
                    "• 代码执行与脚本运行\n"
                    "• 定时任务与异步任务\n"
                    "• 技能与扩展管理\n"
                    "• 记忆与上下文管理\n"
                    "告诉我你想做什么，我来帮你。")

        if intent == INTENT_WEATHER:
            return ('天气查询需要明确城市，请告诉我你想查哪个城市的天气，'
                    '例如「北京今天天气怎么样」。')

        if intent == INTENT_GREETING:
            return f"{greeting}！有什么我可以帮你的吗？"

        if intent == INTENT_SIMPLE_CHAT:
            return f"{greeting}！我在呢，想聊点什么？"

        return None

    @staticmethod
    def _time_greeting(hour: int) -> str:
        """时段问候语"""
        if hour < 6:
            return "凌晨好"
        elif hour < 12:
            return "早上好"
        elif hour < 14:
            return "中午好"
        elif hour < 18:
            return "下午好"
        else:
            return "晚上好"


# ════════════════════════════════════════════════════════════
#  日志 helper（避免循环依赖 logging_utils）
# ════════════════════════════════════════════════════════════

def log_dict_safe(payload: dict) -> dict:
    """轻量日志规范化（不依赖 logging_utils，避免循环导入）

    与 logging_utils.log_dict 字段对齐：trace_id/module_name/action/message
    """
    import uuid
    data = dict(payload)
    if "trace_id" not in data:
        data["trace_id"] = uuid.uuid4().hex[:16]
    if "module_name" not in data:
        data["module_name"] = "response_workflows"
    if "action" not in data:
        data["action"] = "unknown"
    return data
