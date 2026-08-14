"""结构化失败诊断 — 任务4 步骤2（诊断引擎）

将工具/LLM 失败输出转化为结构化诊断报告（错误类型/分类/上下文/修复提示），
而非把原始报错丢给 LLM（采纳设计文档"诊断引擎"思想）。

错误分类来源：
- agent.error_handler.ErrorCategory（15 类枚举：网络/资源/外部服务/数据/权限/配置/未知）
- 诊断层扩展 TOOL_NOT_FOUND：error_handler 无"工具缺失"类别，任务文档修复提示表明确要求

对外接口：
- build_diagnosis(action_result, attempts, history, tool_name, project_context) -> FailureDiagnosis
- classify_error(error, tool_name) -> str（ErrorCategory.value / TOOL_NOT_FOUND / unknown）
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.error_handler import ErrorCategory, YunshuError

# 诊断层扩展类别：工具缺失（error_handler.ErrorCategory 无此值）
TOOL_NOT_FOUND = "tool_not_found"

MAX_ERROR_MESSAGE = 300  # error_message 截断长度
MAX_HISTORY_ITEMS = 3    # history 保留最近失败轮数
MAX_CONTEXT_KEYS = 8     # project_context 摘要保留键数


@dataclass
class FailureDiagnosis:
    """结构化失败诊断报告

    基础字段由 build_diagnosis 生成；root_cause/confidence/repair_actions/avoid
    为失败反思增强字段（reflector.failure_reflect 产出，未反思时保持默认）。
    """
    error_type: str                       # ErrorCategory.value / TOOL_NOT_FOUND / unknown
    error_message: str                    # 截断至 MAX_ERROR_MESSAGE 字符
    tool_name: Optional[str]              # 失败工具名
    attempt: int                          # 第几次尝试（1-based）
    history: List[Dict[str, Any]]         # 前几轮失败摘要（action + error + 根因猜测）
    project_context: Dict[str, Any]       # 项目上下文摘要（工具/配置，裁剪至 token 预算）
    repair_hints: List[str]               # 按 error_type 映射的修复约束（表格驱动）
    # 失败反思增强字段（failure_reflect 产出）
    root_cause: Optional[str] = None
    confidence: float = 0.0
    repair_actions: List[str] = field(default_factory=list)
    avoid: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "tool_name": self.tool_name,
            "attempt": self.attempt,
            "history": self.history,
            "project_context": self.project_context,
            "repair_hints": self.repair_hints,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "repair_actions": self.repair_actions,
            "avoid": self.avoid,
        }


# 错误类型 → 注入的修复约束（任务文档步骤2 表格 + ErrorCategory 全类别覆盖）
REPAIR_HINTS: Dict[str, List[str]] = {
    ErrorCategory.NETWORK_TIMEOUT.value: ["建议重试或换备用路径，禁止无限重试"],
    ErrorCategory.NETWORK_TEMPORARY.value: ["短暂网络波动，可退避后重试，禁止无限重试"],
    ErrorCategory.NETWORK_CONNECTION.value: ["检查网络连接与端点可达性，勿原样重发"],
    ErrorCategory.EXTERNAL_API.value: ["校验请求参数与凭证，勿原样重发"],
    ErrorCategory.EXTERNAL_SERVICE.value: ["确认上游服务可用性，勿原样重发"],
    ErrorCategory.PERMISSION_DENIED.value: ["检查权限声明与工具白名单，勿重复尝试"],
    TOOL_NOT_FOUND: ["校验工具名与注册表，勿虚构工具"],
    ErrorCategory.DATA_INVALID.value: ["校验输入数据格式与类型，勿直接使用"],
    ErrorCategory.DATA_MISSING.value: ["补齐缺失数据或改用替代来源"],
    ErrorCategory.DATA_CORRUPT.value: ["重建或忽略损坏数据，勿继续使用"],
    ErrorCategory.RESOURCE_MEMORY.value: ["降低并发或优化内存占用，勿以相同参数重试"],
    ErrorCategory.RESOURCE_DISK.value: ["清理磁盘空间或更换存储路径"],
    ErrorCategory.RESOURCE_CPU.value: ["降低负载或增加资源，勿重复重试"],
    ErrorCategory.CONFIG_ERROR.value: ["检查配置项与默认值，修正后重试"],
    ErrorCategory.SECURITY_ALERT.value: ["停止尝试并上报安全风险"],
    ErrorCategory.UNKNOWN.value: ["分析错误上下文，更换策略或终止"],
}

# 兜底：任何未映射类型走 unknown 的通用约束
_FALLBACK_HINTS = REPAIR_HINTS[ErrorCategory.UNKNOWN.value]


# 字符串特征 → 错误类别（YunshuError 缺失时的兜底匹配；有序，先命中优先）
_TEXT_FEATURE_RULES: List[tuple] = [
    ("timeout", ErrorCategory.NETWORK_TIMEOUT),
    ("timed out", ErrorCategory.NETWORK_TIMEOUT),  # 常见超时文案（如 Connection timed out）
    ("超时", ErrorCategory.NETWORK_TIMEOUT),
    ("permission", ErrorCategory.PERMISSION_DENIED),
    ("denied", ErrorCategory.PERMISSION_DENIED),
    ("权限", ErrorCategory.PERMISSION_DENIED),
    ("not found", TOOL_NOT_FOUND),
    ("不存在", TOOL_NOT_FOUND),
    ("connection", ErrorCategory.NETWORK_CONNECTION),
    ("连接", ErrorCategory.NETWORK_CONNECTION),
    ("network", ErrorCategory.NETWORK_TEMPORARY),
    ("网络", ErrorCategory.NETWORK_TEMPORARY),
    ("api", ErrorCategory.EXTERNAL_API),
    ("external", ErrorCategory.EXTERNAL_SERVICE),
    ("外部服务", ErrorCategory.EXTERNAL_SERVICE),
    ("memory", ErrorCategory.RESOURCE_MEMORY),
    ("内存", ErrorCategory.RESOURCE_MEMORY),
    ("disk", ErrorCategory.RESOURCE_DISK),
    ("磁盘", ErrorCategory.RESOURCE_DISK),
    ("config", ErrorCategory.CONFIG_ERROR),
    ("配置", ErrorCategory.CONFIG_ERROR),
    ("数据", ErrorCategory.DATA_INVALID),
    ("security", ErrorCategory.SECURITY_ALERT),
    ("安全", ErrorCategory.SECURITY_ALERT),
]


def classify_error(error: Any, tool_name: Optional[str] = None) -> str:
    """解析错误类型，返回 ErrorCategory.value / TOOL_NOT_FOUND / unknown。

    优先级：YunshuError.category > 工具缺失语义 > 文本特征 > unknown。
    文本匹配不区分大小写（message.lower()）。
    """
    if isinstance(error, YunshuError):
        return (
            error.category.value
            if error.category is not None
            else ErrorCategory.UNKNOWN.value
        )
    message = str(error)
    lowered = message.lower()
    if tool_name and ("not found" in lowered or "不存在" in message):
        return TOOL_NOT_FOUND
    for keyword, category in _TEXT_FEATURE_RULES:
        if keyword in lowered:
            return category.value if isinstance(category, ErrorCategory) else category
    return ErrorCategory.UNKNOWN.value


def build_diagnosis(
    action_result: Any,
    attempts: int,
    history: Optional[List[Dict[str, Any]]] = None,
    tool_name: Optional[str] = None,
    project_context: Optional[Dict[str, Any]] = None,
) -> FailureDiagnosis:
    """从 ActionResult 构建结构化诊断报告。

    Args:
        action_result: 动作执行结果（取 .error 文本；兼容带 error 字段的对象）
        attempts: 当前第几次尝试（1-based）
        history: 前几轮失败摘要 [{"attempt": n, "action": str, "error": str, "guess": str}]
        tool_name: 失败工具名（可选）
        project_context: 项目上下文摘要（可用工具/配置，可选）
    """
    error = str(getattr(action_result, "error", None) or "未知错误")
    error_type = classify_error(error, tool_name)
    hints = REPAIR_HINTS.get(error_type, _FALLBACK_HINTS)
    return FailureDiagnosis(
        error_type=error_type,
        error_message=error[:MAX_ERROR_MESSAGE],
        tool_name=tool_name,
        attempt=attempts,
        history=[dict(h) for h in (history or [])][-MAX_HISTORY_ITEMS:],
        project_context={
            k: str(v)[:100]
            for k, v in list((project_context or {}).items())[:MAX_CONTEXT_KEYS]
        },
        repair_hints=list(hints),
    )
