"""自愈恢复动作映射表（补 M4）

【不易】契约：
- restore_map 为声明式「故障域 → 恢复动作」映射，不包含可执行逻辑；
- 每个动作的状态一目了然：executed = execute_action 已实现；unimplemented = 未实现 → SKIPPED + 原因；
- alert_manager._check_heal_action 的默认动作读取本表，不再写死 ["restart_service"]。

【变易】故障域/动作可增量扩展：新增故障域只需加映射项，新增动作只需更新 ACTION_STATUS。
【简易】纯数据模块，无第三方依赖，测试可直接加载校验。
"""
from typing import Dict, List, Optional

# 故障域 → 恢复动作映射（detect 为声明式匹配条件描述，信号源由后续任务接入）
RESTORE_MAP: Dict[str, Dict[str, str]] = {
    "llm_timeout": {            # LLM 超时故障域
        "detect": "error_handler.category == NETWORK_TIMEOUT",
        "actions": ["retry_limited", "degrade_llm_router"],
    },
    "tool_failure": {           # 工具调用失败故障域
        "detect": "error_handler.category == EXTERNAL_API",
        "actions": ["recover_circuit_breaker", "clear_cache"],
    },
    "memory_failure": {         # 记忆模块故障域
        "detect": "module == memory && error_rate >= 0.4",
        "actions": ["rebuild_index"],   # 预留，未实现则 SKIPPED
    },
    "decision_loop": {          # 决策循环故障域（任务 5 提供检测信号）
        "detect": "state_hash_repeat >= 3",
        "actions": ["terminate_loop"],
    },
}

# 动作实现状态：executed = execute_action 已实现；unimplemented = 未实现 → SKIPPED
# 覆盖 HealAction 全部 9 值 + restore_map 预留动作，保证枚举动作均有明确状态
ACTION_STATUS: Dict[str, str] = {
    "restart_service": "executed",
    "restart_component": "executed",
    "clear_cache": "executed",
    "recover_circuit_breaker": "executed",
    "clear_memory": "executed",
    "gc_collect": "executed",
    # HealAction 中未实现的动作（返回 SKIPPED + 原因，而非 FAILED）
    "scale_up": "unimplemented",
    "scale_down": "unimplemented",
    "restart_pod": "unimplemented",
    # restore_map 预留动作（调用时返回 SKIPPED + 原因）
    "retry_limited": "unimplemented",
    "degrade_llm_router": "unimplemented",
    "rebuild_index": "unimplemented",
    "terminate_loop": "unimplemented",
}

# 告警名 → 故障域 提示子串（顺序匹配，靠前优先；小写比较）
DOMAIN_HINTS: List[tuple] = [
    ("loop", "decision_loop"),
    ("memory", "memory_failure"),
    ("tool", "tool_failure"),
    ("llm", "llm_timeout"),
    ("timeout", "llm_timeout"),
]

# 兜底动作（无规则显式 heal_actions 且无法识别故障域时）
DEFAULT_ACTIONS: List[str] = ["restart_service"]

# 未实现动作的明确跳过原因
UNIMPLEMENTED_REASON = "未实现"


def get_domain_for_alert(alert_name: str) -> Optional[str]:
    """根据告警名识别故障域（无匹配返回 None）"""
    name = (alert_name or "").lower()
    for hint, domain in DOMAIN_HINTS:
        if hint in name:
            return domain
    return None


def get_actions_for_alert(alert_name: str) -> List[str]:
    """根据告警名获取恢复动作列表（无匹配返回 DEFAULT_ACTIONS）"""
    domain = get_domain_for_alert(alert_name)
    if domain and domain in RESTORE_MAP:
        return list(RESTORE_MAP[domain]["actions"])
    return list(DEFAULT_ACTIONS)


def get_action_status(action: str) -> str:
    """查询动作实现状态（unknown = 未注册动作）"""
    return ACTION_STATUS.get(action, "unknown")


def get_domain_actions(domain: str) -> List[str]:
    """按故障域名直接获取动作列表（未知域返回空列表）"""
    entry = RESTORE_MAP.get(domain)
    if not entry:
        return []
    return list(entry["actions"])
