"""声明式配置校验基础设施"""
import logging
import os
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
