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


# ════════════════════════════════════════════════════════════
#  工具层错误恢复与结果压缩
#  （V2.0 §3.6 七分类 / §4.5 结构化压缩；INV-08 不静默 / INV-09 写重试必带幂等键）
# ════════════════════════════════════════════════════════════
#
# 【本节的来历 —— 先读这一段再改】
#   `agent/tool_calling.py::_execute_safe` 从很早就写着：
#       from agent.response_workflows import ErrorRecovery, ToolResultProcessor
#       except ImportError: has_workflow = False
#   但这两个符号**在本仓从来不存在**（审计实测：全仓 class 定义数 = 0）。
#   于是该 ImportError 被静默吞掉，后果有二且都很隐蔽：
#     ① 工具失败**永不重试**（不是"按策略不重试"，而是代码路径根本到不了）；
#     ② `ToolResultProcessor.compress_verbose` **永不执行** ⇒ 工具输出压缩形同虚设。
#   这违反 INV-08（禁止静默降级）。本节把这两个符号补成**真实现**，
#   让"工具层重试"与"结果压缩"从"名义存在"变成"真实生效"。
#
# 【但补实现不等于放开重试 —— INV-09 是硬闸门】
#   本仓**没有**工具写幂等键（审计实测：`agent/tools/` / `tool_gate` / `tool_approval`
#   中 idempotency_key 命中 = 0），所以"重试写操作"会真实产生重复副作用。
#   故 `ErrorRecovery` 的重试闸门是 **fail-closed**：
#       只有 `data/tool_definitions/*.yaml` 声明 `effect: read` 的工具才允许重试；
#       未知 / 缺失 / write / execute / extend ⇒ **一律不重试**。
#   这不是"少做了一点"，而是"在没有幂等键之前唯一正确的做法"。

#: 错误分类常量（V2.0 §3.6 七分类在**工具层**的落地口径）
#: 【为什么与 agent/capregistry/errors.py 的 14 码并存而不合并】
#:   两者粒度不同且都已接线：14 码是**能力出口**的对外契约（CI 可凭 status/code 阻断），
#:   七分类是**重试决策**的输入。硬合并会让某一侧的既定消费者被迫改口径（D2 违规）。
#:   二者的映射关系由 tests/unit/test_tool_error_recovery.py 对拍锁死，防止漂移。
ERR_TRANSIENT = "TRANSIENT"
ERR_TIMEOUT = "TIMEOUT"
ERR_RATE_LIMIT = "RATE_LIMIT"
ERR_AUTH = "AUTH"
ERR_PERMANENT = "PERMANENT"
ERR_UNAVAILABLE = "UNAVAILABLE"
ERR_UNKNOWN = "UNKNOWN"

#: 可重试集合（V2.0 §3.6 `RETRYABLE = {TRANSIENT, TIMEOUT, RATE_LIMIT}`）
RETRYABLE_CLASSES = frozenset({ERR_TRANSIENT, ERR_TIMEOUT, ERR_RATE_LIMIT})

#: 允许重试的工具后果等级（唯一判据，见本节来历第 2 段）
RETRY_SAFE_EFFECTS = frozenset({"read"})

#: 关键词 → 分类。顺序有意义：先判**不可重试**的强特征（权限/契约），后判可重试的弱特征，
#: 否则 "timeout 后鉴权失败" 这类复合消息会被误判成可重试。
_KEYWORD_TO_CLASS = (
    # ── 不可重试（放最前，优先命中）──
    ("permission denied", ERR_AUTH),
    ("permission_denied", ERR_AUTH),
    ("unauthorized", ERR_AUTH),
    ("forbidden", ERR_AUTH),
    ("401", ERR_AUTH),
    ("403", ERR_AUTH),
    ("鉴权", ERR_AUTH),
    ("未授权", ERR_AUTH),
    ("无权限", ERR_AUTH),
    ("审批", ERR_AUTH),
    ("schema", ERR_PERMANENT),
    ("validation", ERR_PERMANENT),
    ("参数", ERR_PERMANENT),
    ("不存在", ERR_PERMANENT),
    ("未知工具", ERR_PERMANENT),
    ("not found", ERR_PERMANENT),
    ("unknown tool", ERR_PERMANENT),
    ("400", ERR_PERMANENT),
    ("404", ERR_PERMANENT),
    ("422", ERR_PERMANENT),
    # ── 可重试 ──
    ("rate limit", ERR_RATE_LIMIT),
    ("ratelimit", ERR_RATE_LIMIT),
    ("429", ERR_RATE_LIMIT),
    ("限流", ERR_RATE_LIMIT),
    ("频率过高", ERR_RATE_LIMIT),
    ("quota", ERR_RATE_LIMIT),
    ("timeout", ERR_TIMEOUT),
    ("timed out", ERR_TIMEOUT),
    ("超时", ERR_TIMEOUT),
    ("408", ERR_TIMEOUT),
    ("504", ERR_TIMEOUT),
    ("connection", ERR_TRANSIENT),
    ("refused", ERR_TRANSIENT),
    ("reset by peer", ERR_TRANSIENT),
    ("broken pipe", ERR_TRANSIENT),
    ("500", ERR_TRANSIENT),
    ("502", ERR_TRANSIENT),
    ("503", ERR_TRANSIENT),
    ("服务不可用", ERR_TRANSIENT),
    ("temporarily", ERR_TRANSIENT),
    # ── 连接类不可达（走 fallback 而非重试）──
    ("unreachable", ERR_UNAVAILABLE),
    ("dns", ERR_UNAVAILABLE),
    ("no route", ERR_UNAVAILABLE),
    ("拒绝连接", ERR_UNAVAILABLE),
)

#: 工具 effect 缓存（进程内；load_tool_meta 自身已带缓存，此处只避免重复字典查找）
_TOOL_EFFECT_CACHE: dict = {}


def resolve_tool_effect(tool_name: str) -> str:
    """查工具的后果等级（`read`/`write`/`execute`/`extend`）；取不到返回空串。

    **取不到时返回空串是 fail-closed 的关键**：`ErrorRecovery` 把空串视为
    "后果未知" ⇒ 不重试。绝不返回 "read" 之类的乐观默认值。
    """
    if not tool_name:
        return ""
    cached = _TOOL_EFFECT_CACHE.get(tool_name)
    if cached is not None:
        return cached
    effect = ""
    try:
        from agent.lines.models import load_tool_meta
        meta = load_tool_meta().get(tool_name)
        if meta is not None:
            effect = str(getattr(meta, "effect", "") or "")
    except Exception:  # noqa: BLE001  台账不可用 ⇒ 视为"后果未知"（fail-closed）
        effect = ""
    _TOOL_EFFECT_CACHE[tool_name] = effect
    return effect


class ErrorRecovery:
    """工具层错误恢复决策（纯函数，零 IO、零副作用）

    【不易】决策**只看**三件事：错误消息分类、已尝试次数、工具后果等级。
            不看时间、不看随机数（除非调用方显式要求抖动）。
    【变易】关键词表 `_KEYWORD_TO_CLASS` 可扩；重试上限 `MAX_ATTEMPTS` 可调。
    【简易】不重试未知分类 —— 宁可少重试，不可重复副作用。
    """

    #: 工具层最多重试次数（与 tool_calling.`range(3)` 的既有外层循环对齐：
    #: attempt 0/1 可重试，attempt 2 是最后一次 ⇒ 与"最多 3 次尝试"语义一致）
    MAX_ATTEMPTS = 3

    @staticmethod
    def classify(error_msg: str) -> str:
        """把错误消息归入七分类之一（大小写不敏感）。"""
        haystack = str(error_msg or "").lower()
        if not haystack:
            return ERR_UNKNOWN
        for needle, cls in _KEYWORD_TO_CLASS:
            if needle in haystack:
                return cls
        return ERR_UNKNOWN

    @staticmethod
    def is_retryable_class(error_class: str) -> bool:
        """分类是否属于可重试集合。"""
        return error_class in RETRYABLE_CLASSES

    @staticmethod
    def _retry_safe(effect: str) -> bool:
        """工具后果是否允许重试（fail-closed：未知 ⇒ 否）。"""
        return str(effect or "").strip().lower() in RETRY_SAFE_EFFECTS

    @staticmethod
    def get_recovery_plan(error_msg: str,
                          attempt: int,
                          *,
                          tool_name: str = "",
                          effect: Optional[str] = None,
                          delay_base: float = 1.0) -> dict:
        """给出一次工具失败的处置方案。

        Args:
            error_msg: 原始错误消息（**只用于分类**，不外泄给模型）
            attempt: 已尝试次数（0-based）
            tool_name: 工具名；提供且未显式给 `effect` 时自动查台账
            effect: 显式指定后果等级（`read`/`write`/...）。None ⇒ 按工具名查
            delay_base: 退避基数（秒）

        Returns:
            `{"should_retry": bool, "delay": float, "message": str,
               "error_class": str, "effect": str, "reason": str}`

        `reason` 是**给人看的判定理由**，会被写进日志 —— 这样"为什么不重试"
        在事后可归因，而不是一个沉默的 False（INV-08）。
        """
        error_class = ErrorRecovery.classify(error_msg)
        resolved_effect = effect if effect is not None else resolve_tool_effect(tool_name)
        retryable = ErrorRecovery.is_retryable_class(error_class)
        safe = ErrorRecovery._retry_safe(resolved_effect)
        within_attempts = attempt < ErrorRecovery.MAX_ATTEMPTS - 1

        should_retry = bool(retryable and safe and within_attempts)
        reason = "ok"
        if not retryable:
            reason = f"分类 {error_class} 不在可重试集合 {sorted(RETRYABLE_CLASSES)}"
        elif not safe:
            reason = (f"工具 {tool_name or '?'} 的 effect={resolved_effect or '(未知)'} "
                      f"不在可重试集合 {sorted(RETRY_SAFE_EFFECTS)}"
                      f"（无幂等键时不重试写操作，INV-09）")
        elif not within_attempts:
            reason = f"已达上限（attempt={attempt}, max={ErrorRecovery.MAX_ATTEMPTS}）"

        # 退避：指数 + 抖动（去相关，避免多路失败同相位重试）
        delay = 0.0
        if should_retry:
            base = float(delay_base) * (2 ** attempt)
            delay = ErrorRecovery._jittered(base)

        # 【run 级预算闸门（INV-09「重试预算属于 run，不属于 call」）】
        # 预算耗尽 ⇒ 放弃本层重试，且**显式说明**，不静默降级。
        if should_retry:
            allowed, budget_note = ErrorRecovery._consume_budget()
            if not allowed:
                should_retry = False
                delay = 0.0
                reason = budget_note

        message = (f"[{error_class}] {ErrorRecovery._safe_brief(error_msg)}"
                   if should_retry else f"[{error_class}] 不重试（{reason}）")
        return {
            "should_retry": should_retry,
            "delay": delay,
            "message": message,
            "error_class": error_class,
            "effect": resolved_effect,
            "reason": reason,
        }

    @staticmethod
    def _jittered(base: float) -> float:
        """指数退避加抖动；复用 run 级预算模块的抖动系数（保持全仓一致）。"""
        try:
            from agent.timeout_budget import jittered_delay
            return float(jittered_delay(base))
        except Exception:  # noqa: BLE001  抖动不可用 ⇒ 确定性退避（不阻断）
            return float(base)

    @staticmethod
    def _consume_budget():
        """从当前 run 的重试预算里扣一次；返回 `(allowed, note)`。

        预算模块不可用 ⇒ `(True, "预算不可用")`，即**退回旧行为**而不是把工具层闷死
        （与 `tool_calling` 里 LLM 重试层的降级口径一致）。
        """
        try:
            from agent.timeout_budget import consume_retry
            if bool(consume_retry("tool")):
                return True, "预算扣减成功"
            return False, "run 级重试预算已耗尽（INV-09：预算属于 run）"
        except Exception as _e:  # noqa: BLE001
            return True, f"预算不可用（按旧行为继续）: {_e}"

    @staticmethod
    def _safe_brief(error_msg: str, limit: int = 160) -> str:
        """把错误消息压成一行短摘要（不含堆栈、不含路径）。

        【为什么在这里做而不是交给模型】V2.0 §5.3「错误聚合摘要：不给模型拼堆栈」。
        这里是**日志**用途，故只压空白与长度；进模型的形态仍走
        `agent.capregistry.errors.to_llm_safe()`（那是唯一允许进上下文的形态）。
        """
        try:
            text = " ".join(str(error_msg or "").split())
        except Exception:  # noqa: BLE001
            return ""
        return text[:limit]


class ToolResultProcessor:
    """工具结果后处理：结构化压缩（V2.0 §4.5「token 预算一等公民」）

    【为什么需要】`_execute_safe` 一直在调用它，但它此前不存在 ⇒ 工具返回的
    大段文本（read_file 全文、shell 输出、HTTP 正文）原样进入模型上下文。
    这是上下文膨胀最直接的一处来源。

    【不易（三条纪律）】
      1. **不静默截断**（INV-08）：被压缩的字段会追加显式标注
         `[…已压缩: 原 N 字符 → M 字符，中段省略…]`，且 `ok`/`error` 语义不动。
      2. **不改结构**：只压字符串叶子，键名/类型/层级一律保持，调用方的
         `result["ok"]` 等既有读取点 100% 不变。
      3. **绝不抛异常**：压缩失败 ⇒ 原样返回（压缩是优化，不是正确性前提）。
    """

    #: 单字段保留上限（字符）。**保守取值**：只压明显过大的输出，
    #: 避免影响正常结果与其既有断言。
    MAX_FIELD_CHARS = 8000
    #: 压缩后头部 / 尾部保留比例（U 型注意力：首尾信息量最高，砍中段）
    HEAD_RATIO = 0.7

    @staticmethod
    def compress_verbose(result: dict,
                         *,
                         max_chars: Optional[int] = None) -> dict:
        """就地压缩结果中的超长字符串字段。

        Args:
            result: 工具返回结果（原地修改）
            max_chars: 覆盖默认上限

        Returns:
            `{"compressed": bool, "fields": [键路径], "saved_chars": int}`
            —— 返回报告而不是 None，便于调用方埋点（若调用方忽略返回值也不受影响）。
        """
        report = {"compressed": False, "fields": [], "saved_chars": 0}
        if not isinstance(result, dict):
            return report
        cap = int(max_chars or ToolResultProcessor.MAX_FIELD_CHARS)
        if cap <= 0:
            return report
        try:
            ToolResultProcessor._walk(result, "", cap, report)
        except Exception:  # noqa: BLE001  压缩失败不得影响工具结果可用性
            return report
        return report

    @staticmethod
    def _walk(node, path: str, cap: int, report: dict) -> None:
        if isinstance(node, dict):
            for key in list(node.keys()):
                value = node[key]
                if isinstance(value, str):
                    new_value = ToolResultProcessor._shrink(value, cap)
                    if new_value is not None:
                        node[key] = new_value
                        report["compressed"] = True
                        report["fields"].append(f"{path}.{key}" if path else str(key))
                        report["saved_chars"] += len(value) - len(new_value)
                elif isinstance(value, (dict, list)):
                    ToolResultProcessor._walk(
                        value, f"{path}.{key}" if path else str(key), cap, report)
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                child_path = f"{path}[{idx}]"
                if isinstance(value, str):
                    new_value = ToolResultProcessor._shrink(value, cap)
                    if new_value is not None:
                        node[idx] = new_value
                        report["compressed"] = True
                        report["fields"].append(child_path)
                        report["saved_chars"] += len(value) - len(new_value)
                elif isinstance(value, (dict, list)):
                    ToolResultProcessor._walk(value, child_path, cap, report)

    @staticmethod
    def _shrink(text: str, cap: int) -> Optional[str]:
        """超限则返回压缩后的文本；未超限返回 None（表示"无需改动"）。"""
        if len(text) <= cap:
            return None
        head_len = max(1, int(cap * ToolResultProcessor.HEAD_RATIO))
        tail_len = max(1, cap - head_len)
        head = text[:head_len]
        tail = text[-tail_len:]
        # 【为什么用 % 而不是 f-string】压缩标注里需要换行，而多行 f-string
        #   在源码里极易被编辑工具改坏（本次就踩过）；% 形式无此风险。
        marker = ("[\u2026已压缩: 原 %d 字符 \u2192 %d 字符，中段 %d 字符已省略\u2026]"
                  % (len(text), cap, len(text) - head_len - tail_len))
        return head + marker + tail
