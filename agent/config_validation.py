"""声明式配置校验基础设施"""
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


@dataclass
class ValidationRule:
    path: str
    validator: Callable[[Any], bool]
    default: Any = None
    error_message: str = ""
    description: str = ""
    required: bool = True


def _range_validator(min_val, max_val):
    def validate(value):
        try:
            num = float(value)
        except (TypeError, ValueError):
            return False
        return min_val <= num <= max_val
    return validate


def _non_empty_string_validator():
    def validate(value):
        return isinstance(value, str) and value.strip() != ""
    return validate


def _choice_validator(choices):
    choices_set = set(choices)
    def validate(value):
        return value in choices_set
    return validate


def _bool_validator():
    def validate(value):
        return isinstance(value, bool)
    return validate


def _url_validator():
    def validate(value):
        if not isinstance(value, str):
            return False
        return value.startswith(('http://', 'https://'))
    return validate


def _path_validator(must_exist=False):
    def validate(value):
        if not isinstance(value, str) or not value:
            return False
        return os.path.exists(value) if must_exist else True
    return validate


def validate_dict_against_rules(data, rules):
    errors = []
    for rule in rules:
        value = data.get(rule.path, None)
        if not rule.required and value is None:
            logger.debug("校验跳过（可选字段缺失）: path=%s", rule.path)
            continue
        if rule.required and (value is None or value == ""):
            logger.debug("校验失败（必填字段缺失）: path=%s, error=%s", rule.path, rule.error_message)
            errors.append(rule.error_message)
            continue
        if not rule.validator(value):
            logger.debug("校验失败（验证器拒绝）: path=%s, value=%r, error=%s", rule.path, value, rule.error_message)
            errors.append(rule.error_message)
        else:
            logger.debug("校验通过: path=%s, value=%r", rule.path, value)
    return errors


SEARCH_INSTANCE_VALIDATION_RULES = [
    ValidationRule(
        path="name",
        validator=_non_empty_string_validator(),
        default="",
        error_message="名称不能为空",
        description="搜索实例显示名称",
        required=True,
    ),
    ValidationRule(
        path="timeout",
        validator=_range_validator(1, 300),
        default=30,
        error_message="超时必须在 1-300 秒之间",
        description="搜索实例请求超时时间（秒）",
        required=False,
    ),
]


#: 已知的内置搜索引擎类型（原 agent/server_routes/routes_config.py，模块退役后迁入）
BUILTIN_ENGINES = {'tavily', 'firecrawl', 'bing', 'google', 'brave',
                   'duckduckgo', 'baidu', 'sogou', 'so360'}


def validate_search_instance(instance: dict) -> List[str]:
    """验证搜索实例配置

    使用 SEARCH_INSTANCE_VALIDATION_RULES 声明式规则集校验 name 和 timeout，
    条件逻辑（engine_type 枚举/自定义、api_endpoint 条件必填）在包装函数中处理。

    【2026-10-15 迁入】原实现位于 agent/server_routes/routes_config.py；该模块
    未接线（端点由 plugins/admin.py 提供）已退役，但校验函数是活体依赖，故迁到
    本基础设施模块。
    """
    t0 = time.perf_counter()
    errors = validate_dict_against_rules(instance, SEARCH_INSTANCE_VALIDATION_RULES)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    engine_type = instance.get('engine_type', '')
    if not engine_type:
        errors.append('引擎类型不能为空')
    elif engine_type != 'custom' and engine_type not in BUILTIN_ENGINES:
        errors.append(f'未知的内置引擎类型: {engine_type}')

    if engine_type == 'custom' and not instance.get('api_endpoint'):
        errors.append('自定义引擎必须提供 API 端点 URL')

    logger.debug("搜索实例校验完成: 声明式校验耗时=%.3fms, 错误数=%d, 错误详情=%s",
                 elapsed_ms, len(errors), errors if errors else "无")
    return errors


# 三级熔断器配置校验规则（SESSION/USER/GLOBAL 各 4 项，共 12 项）
# 对应 agent/circuit_breaker.py 的 ThreeLevelBreakerConfig 默认值
CIRCUIT_BREAKER_VALIDATION_RULES = [
    # ── SESSION 级（单会话单工具冷却 60s）──────────────────────
    ValidationRule(
        path="session_failure_threshold",
        validator=_range_validator(0, 1),
        default=1.0,
        error_message="session failure_threshold 必须在 [0,1]",
        description="SESSION 级失败率阈值",
        required=True,
    ),
    ValidationRule(
        path="session_min_requests",
        validator=_range_validator(1, 10000),
        default=5,
        error_message="session min_requests 必须在 [1,10000]",
        description="SESSION 级最小请求数",
        required=True,
    ),
    ValidationRule(
        path="session_recovery_timeout",
        validator=_range_validator(0, 86400),
        default=60.0,
        error_message="session recovery_timeout 必须在 [0,86400]",
        description="SESSION 级冷却恢复时间（秒）",
        required=True,
    ),
    ValidationRule(
        path="session_half_open_max_calls",
        validator=_range_validator(1, 100),
        default=1,
        error_message="session half_open_max_calls 必须在 [1,100]",
        description="SESSION 级半开最大探测数",
        required=True,
    ),
    # ── USER 级（单用户高危工具冷却 300s）──────────────────────
    ValidationRule(
        path="user_failure_threshold",
        validator=_range_validator(0, 1),
        default=1.0,
        error_message="user failure_threshold 必须在 [0,1]",
        description="USER 级失败率阈值",
        required=True,
    ),
    ValidationRule(
        path="user_min_requests",
        validator=_range_validator(1, 10000),
        default=20,
        error_message="user min_requests 必须在 [1,10000]",
        description="USER 级最小请求数",
        required=True,
    ),
    ValidationRule(
        path="user_recovery_timeout",
        validator=_range_validator(0, 86400),
        default=300.0,
        error_message="user recovery_timeout 必须在 [0,86400]",
        description="USER 级冷却恢复时间（秒）",
        required=True,
    ),
    ValidationRule(
        path="user_half_open_max_calls",
        validator=_range_validator(1, 100),
        default=2,
        error_message="user half_open_max_calls 必须在 [1,100]",
        description="USER 级半开最大探测数",
        required=True,
    ),
    # ── GLOBAL 级（全局单工具冷却 600s）────────────────────────
    ValidationRule(
        path="global_failure_threshold",
        validator=_range_validator(0, 1),
        default=1.0,
        error_message="global failure_threshold 必须在 [0,1]",
        description="GLOBAL 级失败率阈值",
        required=True,
    ),
    ValidationRule(
        path="global_min_requests",
        validator=_range_validator(1, 10000),
        default=100,
        error_message="global min_requests 必须在 [1,10000]",
        description="GLOBAL 级最小请求数",
        required=True,
    ),
    ValidationRule(
        path="global_recovery_timeout",
        validator=_range_validator(0, 86400),
        default=600.0,
        error_message="global recovery_timeout 必须在 [0,86400]",
        description="GLOBAL 级冷却恢复时间（秒）",
        required=True,
    ),
    ValidationRule(
        path="global_half_open_max_calls",
        validator=_range_validator(1, 100),
        default=3,
        error_message="global half_open_max_calls 必须在 [1,100]",
        description="GLOBAL 级半开最大探测数",
        required=True,
    ),
]
