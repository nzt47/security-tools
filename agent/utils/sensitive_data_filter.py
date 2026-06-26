#!/usr/bin/env python3
"""
统一敏感数据过滤模块。

整合各模块的敏感数据过滤能力，提供统一的 API 接口，
支持字典、列表、字符串等多种数据类型的递归过滤和脱敏。

主要功能：
- filter(data): 通用数据过滤（字典、列表、字符串）
- detect(data): 检测敏感信息，返回检测结果和违规项
- mask(text): 文本内容脱敏

支持的敏感数据类型：
- 密码、API Key、Token、Secret 等密钥类
- 手机号（中国大陆、香港）
- 身份证号（18位、15位）
- 邮箱地址
- 银行卡号
- IP 地址（IPv4）
- JWT Token
- AWS / GitHub / OpenAI 等第三方密钥
- SQL 注入模式
- XSS 脚本注入

同时兼容 logging.Filter 接口，可直接用于日志系统的自动脱敏。

用法示例：
    >>> from agent.utils.sensitive_data_filter import SensitiveDataFilter
    >>> filter = SensitiveDataFilter()
    >>> result = filter.filter({"password": "secret123"})
    >>> print(result)
    {'password': '********'}
"""

import re
import json
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern, Tuple, Union


logger = logging.getLogger(__name__)


class SensitiveLevel(Enum):
    """敏感等级枚举

    Attributes:
        SAFE: 安全
        LOW: 低敏感（可脱敏处理）
        MEDIUM: 中敏感（需要确认）
        HIGH: 高敏感（阻止写入）
        CRITICAL: 极高敏感（立即阻止+告警）
    """
    SAFE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class SensitiveMatch:
    """敏感信息匹配结果

    Attributes:
        pattern_name: 匹配的模式名称
        matched_text: 匹配的原始文本（脱敏后）
        start_pos: 匹配起始位置
        end_pos: 匹配结束位置
        level: 敏感等级
        suggestion: 处理建议
    """
    pattern_name: str
    matched_text: str
    start_pos: int
    end_pos: int
    level: SensitiveLevel
    suggestion: str = ""


@dataclass
class FilterResult:
    """过滤结果

    Attributes:
        allowed: 是否允许通过
        violations: 违规列表
        sanitized_content: 脱敏后的内容（如果有）
        action_taken: 采取的动作
    """
    allowed: bool
    violations: List[SensitiveMatch] = field(default_factory=list)
    sanitized_content: Optional[Any] = None
    action_taken: str = "pass"


REDACTED_VALUE = "********"
REDACTED_PARTIAL = "****"


class SensitiveDataFilter(logging.Filter):
    """统一敏感数据过滤器

    整合多模块过滤能力，支持：
    1. 基于字段名的字典/列表递归过滤
    2. 基于正则的文本内容检测与脱敏
    3. 作为 logging.Filter 用于日志系统
    4. 敏感等级评估与阻止策略

    Usage:
        >>> filter = SensitiveDataFilter()
        >>> result = filter.filter({"password": "secret123"})
        >>> print(result)
        {'password': '********'}
        >>> detected = filter.detect("手机号：13800138000")
        >>> print(detected.allowed)
        False
        >>> masked = filter.mask("邮箱：test@example.com")
        >>> print(masked)
        邮箱：te***@example.com
    """

    SENSITIVE_KEY_PATTERNS: List[str] = [
        r'password', r'passwd', r'pwd', r'secret',
        r'api_?key', r'token', r'auth', r'credential',
        r'private_?key', r'privatekey', r'rsa_?key', r'ssh_?key',
        r'db_?pass', r'database_?password', r'mongo_?uri', r'redis_?password',
        r'jwt_?token', r'bearer_?token', r'access_?token', r'refresh_?token',
        r'authorization', r'x_api_key', r'x_auth',
        r'signature', r'sign', r'encrypt',
        r'session_?id', r'session_?token',
        r'certificate', r'cert_?key',
        r'client_?secret', r'app_?secret',
    ]

    REDACT_KEY_PATTERNS: List[str] = [
        r'^secret$', r'^token$', r'^password$', r'^pwd$',
        r'^api_?key$', r'^private_?key$', r'^access_?token$',
        r'^db_?pass(word)?$', r'^redis_?pass(word)?$',
        r'^mongo_?uri$', r'^client_?secret$',
    ]

    CONTENT_PATTERNS: Dict[str, Dict] = {
        "aws_access_key": {
            "pattern": r'\bAKIA[0-9A-Z]{16}\b',
            "level": SensitiveLevel.CRITICAL,
            "description": "AWS Access Key",
        },
        "github_token": {
            "pattern": r'\bgh[pousr]_[a-zA-Z0-9]{36,}\b',
            "level": SensitiveLevel.CRITICAL,
            "description": "GitHub Token",
        },
        "openai_key": {
            "pattern": r'\bsk-[a-zA-Z0-9_-]{48}\b',
            "level": SensitiveLevel.CRITICAL,
            "description": "OpenAI API Key (48字符)",
        },
        "sk_key_general": {
            "pattern": r'\bsk-[a-zA-Z0-9_-]{20,}\b',
            "level": SensitiveLevel.CRITICAL,
            "description": "通用 sk- API Key",
        },
        "jwt_token": {
            "pattern": r'\beyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*\b',
            "level": SensitiveLevel.CRITICAL,
            "description": "JWT Token",
        },
        "bearer_token": {
            "pattern": r'(?i)(Bearer\s+)([a-zA-Z0-9\-_.~+/]{20,})',
            "level": SensitiveLevel.CRITICAL,
            "description": "Bearer Token",
        },
        "api_key_field": {
            "pattern": r'(?i)(api[_-]?key["\']?\s*[:=]\s*["\']?)([a-zA-Z0-9_\-]{16,})',
            "level": SensitiveLevel.CRITICAL,
            "description": "API Key 字段值",
        },
        "password_field": {
            "pattern": r'(?i)(password|passwd|pwd|secret)["\']?\s*[:=]\s*["\']?([^\s"\'<>]{6,})',
            "level": SensitiveLevel.CRITICAL,
            "description": "密码字段值",
        },
        "url_password": {
            "pattern": r'(:[^:@]+:)([^:@]+)(@)',
            "level": SensitiveLevel.CRITICAL,
            "description": "URL 中的密码",
        },
        "private_key": {
            "pattern": r'-----BEGIN\s+(RSA|DSA|EC|OPENSSH|PGP)\s+PRIVATE KEY-----',
            "level": SensitiveLevel.CRITICAL,
            "description": "私钥文件头",
        },
        "china_id": {
            "pattern": r'\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b',
            "level": SensitiveLevel.CRITICAL,
            "description": "中国身份证号（18位）",
        },
        "china_id_old": {
            "pattern": r'(?<!\d)\d{15}(?!\d)',
            "level": SensitiveLevel.HIGH,
            "description": "中国身份证号（15位旧版）",
        },
        "phone_cn": {
            "pattern": r'(?<!\d)1[3-9]\d{9}(?!\d)',
            "level": SensitiveLevel.HIGH,
            "description": "中国大陆手机号",
        },
        "phone_cn_area": {
            "pattern": r'(?<!\d)(\+?86)1[3-9]\d{9}(?!\d)',
            "level": SensitiveLevel.HIGH,
            "description": "带区号的中国大陆手机号",
        },
        "phone_hk": {
            "pattern": r'(?<!\d)(\+?852)?[569]\d{7}(?!\d)',
            "level": SensitiveLevel.HIGH,
            "description": "香港手机号",
        },
        "email": {
            "pattern": r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b',
            "level": SensitiveLevel.LOW,
            "description": "电子邮件地址",
        },
        "bank_card": {
            "pattern": r'\b\d{16,19}\b',
            "level": SensitiveLevel.CRITICAL,
            "description": "银行卡号",
        },
        "ip_v4": {
            "pattern": r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b',
            "level": SensitiveLevel.LOW,
            "description": "IPv4 地址",
        },
        "sql_injection": {
            "pattern": r'(?i)(select\s+.*\s+from|insert\s+into|delete\s+from|drop\s+table|union\s+select)',
            "level": SensitiveLevel.CRITICAL,
            "description": "SQL 注入模式",
        },
        "xss_script": {
            "pattern": r'(?i)(<script|javascript:|onerror=|onclick=|<iframe)',
            "level": SensitiveLevel.CRITICAL,
            "description": "XSS 脚本注入",
        },
    }

    def __init__(
        self,
        additional_key_patterns: Optional[List[str]] = None,
        custom_replacements: Optional[Dict[str, str]] = None,
        custom_content_patterns: Optional[Dict[str, Dict]] = None,
        block_critical: bool = True,
        block_high: bool = False,
    ):
        """初始化敏感数据过滤器

        Args:
            additional_key_patterns: 额外的敏感字段名模式（正则）
            custom_replacements: 自定义替换规则 {字段名: 替换值}
            custom_content_patterns: 自定义内容检测模式
            block_critical: 是否阻止 CRITICAL 级别的内容（detect 模式）
            block_high: 是否阻止 HIGH 级别的内容（detect 模式）
        """
        super().__init__()

        self._key_patterns: List[Pattern] = [
            re.compile(p, re.IGNORECASE) for p in self.SENSITIVE_KEY_PATTERNS
        ]
        if additional_key_patterns:
            for p in additional_key_patterns:
                try:
                    self._key_patterns.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    logger.warning(f"[SensitiveFilter] 无效的正则表达式: {p}")

        self._redact_patterns: List[Pattern] = [
            re.compile(p, re.IGNORECASE) for p in self.REDACT_KEY_PATTERNS
        ]

        self.custom_replacements = custom_replacements or {}

        self._content_patterns: Dict[str, Dict] = dict(self.CONTENT_PATTERNS)
        if custom_content_patterns:
            for name, config in custom_content_patterns.items():
                self._content_patterns[name] = config

        self._compiled_content: Dict[str, Pattern] = {}
        for name, config in self._content_patterns.items():
            try:
                self._compiled_content[name] = re.compile(config["pattern"])
            except re.error as e:
                logger.error("[SensitiveFilter] 模式编译失败: %s, error=%s", name, e)

        self._block_critical = block_critical
        self._block_high = block_high

    def is_sensitive_key(self, key: str) -> bool:
        """检查字段名是否为敏感字段。

        按以下优先级判断：
        1. 精确匹配完全屏蔽的字段名模式（REDACT_KEY_PATTERNS）
        2. 模糊匹配敏感字段名模式（SENSITIVE_KEY_PATTERNS）
        3. 检查自定义替换规则中的字段名

        Args:
            key: 待检查的字段名

        Returns:
            True 如果是敏感字段，False 否则
        """
        if not key:
            return False

        key_lower = key.lower()

        for pattern in self._redact_patterns:
            if pattern.match(key_lower):
                return True

        for pattern in self._key_patterns:
            if pattern.search(key_lower):
                return True

        if key_lower in self.custom_replacements:
            return True

        return False

    def is_redact_key(self, key: str) -> bool:
        """检查字段名是否需要完全屏蔽（替换为固定掩码）。

        仅匹配 REDACT_KEY_PATTERNS 中的精确模式，
        这些字段的值需要完全隐藏，不保留任何原文。

        Args:
            key: 待检查的字段名

        Returns:
            True 如果需要完全屏蔽，False 否则
        """
        if not key:
            return False

        key_lower = key.lower()

        for pattern in self._redact_patterns:
            if pattern.match(key_lower):
                return True

        return False

    def _get_key_replacement(self, key: str, value: Any) -> Any:
        """获取字段的替换值

        Args:
            key: 字段名
            value: 原值

        Returns:
            替换后的值
        """
        key_lower = key.lower()

        if key_lower in self.custom_replacements:
            replacement = self.custom_replacements[key_lower]
            if callable(replacement):
                return replacement(value)
            return replacement

        if self.is_redact_key(key):
            return REDACTED_VALUE

        if isinstance(value, str):
            if len(value) <= 4:
                return REDACTED_VALUE
            return value[:2] + REDACTED_PARTIAL + value[-2:]
        elif isinstance(value, (int, float, bool)):
            return REDACTED_VALUE
        else:
            return REDACTED_VALUE

    def filter_dict(self, data: Dict[str, Any], parent_key: str = "") -> Dict[str, Any]:
        """递归过滤字典中的敏感字段。

        遍历字典的所有键值对，对每个字段：
        1. 如果值是字典，递归过滤
        2. 如果值是列表，递归过滤列表元素
        3. 如果键名是敏感字段，替换值为脱敏内容
        4. 如果值是字符串，对内容进行脱敏

        Args:
            data: 待过滤的字典数据
            parent_key: 父级字段路径，用于嵌套场景下的日志定位

        Returns:
            过滤后的新字典（原字典不被修改）
        """
        if not isinstance(data, dict):
            return data

        result = {}
        for key, value in data.items():
            full_key = f"{parent_key}.{key}" if parent_key else key

            if isinstance(value, dict):
                result[key] = self.filter_dict(value, full_key)
            elif isinstance(value, list):
                result[key] = self.filter_list(value, full_key)
            elif self.is_sensitive_key(key):
                result[key] = self._get_key_replacement(key, value)
                logger.debug(f"[SensitiveFilter] 过滤敏感字段: {full_key}")
            else:
                if isinstance(value, str):
                    result[key] = self.mask(value)
                else:
                    result[key] = value

        return result

    def filter_list(self, data: List[Any], parent_key: str = "") -> List[Any]:
        """递归过滤列表中的敏感数据。

        遍历列表的所有元素，对每个元素：
        1. 如果是字典，递归过滤字典
        2. 如果是列表，递归过滤子列表
        3. 如果是字符串，进行内容脱敏
        4. 其他类型直接返回

        Args:
            data: 待过滤的列表数据
            parent_key: 父级字段路径，用于日志定位

        Returns:
            过滤后的新列表（原列表不被修改）
        """
        if not isinstance(data, list):
            return data

        result = []
        for i, item in enumerate(data):
            if isinstance(item, dict):
                result.append(self.filter_dict(item, f"{parent_key}[{i}]"))
            elif isinstance(item, list):
                result.append(self.filter_list(item, f"{parent_key}[{i}]"))
            elif isinstance(item, str):
                result.append(self.mask(item))
            else:
                result.append(item)

        return result

    def mask(self, text: str) -> str:
        """脱敏文本中的敏感信息。

        使用正则表达式检测文本中的各类敏感数据，并替换为脱敏格式。
        支持的脱敏类型包括：密码、密钥、JWT、身份证、手机号、邮箱、银行卡、IP等。

        Args:
            text: 待脱敏的文本字符串

        Returns:
            脱敏后的文本字符串
        """
        if not isinstance(text, str):
            return text

        result = text

        result = re.sub(
            r'(?i)(password|passwd|pwd|secret)["\']?\s*[:=]\s*["\']?([^"\']*)["\']?',
            r'\1="' + REDACTED_VALUE + '"',
            result,
        )
        result = re.sub(
            r'(?i)(api[_-]?key|secret_key|access_token|refresh_token)["\']?\s*[:=]\s*["\']?([^"\']*)["\']?',
            r'\1="' + REDACTED_VALUE + '"',
            result,
        )
        result = re.sub(
            r'([?&])(api_key|key|secret|token)\s*=\s*[^&]*',
            r'\1\2=' + REDACTED_VALUE,
            result,
            flags=re.IGNORECASE,
        )

        result = re.sub(
            r'\bAKIA[0-9A-Z]{16}\b',
            REDACTED_VALUE,
            result,
        )
        result = re.sub(
            r'\bgh[pousr]_[a-zA-Z0-9]{36,}\b',
            REDACTED_VALUE,
            result,
        )
        result = re.sub(
            r'\bsk-[a-zA-Z0-9_-]{48}\b',
            REDACTED_VALUE,
            result,
        )
        # 通用 sk- 模式（20字符以上）
        result = re.sub(
            r'\bsk-[a-zA-Z0-9_-]{20,}\b',
            REDACTED_VALUE,
            result,
        )
        result = re.sub(
            r'\beyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*\b',
            REDACTED_VALUE,
            result,
        )
        result = re.sub(
            r'(Bearer\s+)([a-zA-Z0-9\-_.~+/]{20,})',
            r'\1' + REDACTED_VALUE,
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r'(:[^:@]+:)([^:@]+)(@)',
            r'\1' + REDACTED_VALUE + r'\3',
            result,
        )
        result = re.sub(
            r'-----BEGIN\s+(RSA|DSA|EC|OPENSSH|PGP)\s+PRIVATE KEY-----',
            REDACTED_VALUE,
            result,
            flags=re.IGNORECASE,
        )

        result = re.sub(
            r'(\d{6})\d{8}(\d{3}[Xx])',
            r'\1********\2',
            result,
        )
        result = re.sub(
            r'(\d{6})\d{8}(\d{4})',
            r'\1********\2',
            result,
        )
        result = re.sub(
            r'(\d{6})\d{6}(\d{3})',
            r'\1******\2',
            result,
        )

        result = re.sub(
            r'(?<!\d)(\+?86)?(1[3-9]\d)\d{4}(\d{4})(?!\d)',
            lambda m: f"{m.group(1) or ''}{m.group(2)}****{m.group(3)}",
            result,
        )

        result = re.sub(
            r'(?<!\d)(\+?852)?([569]\d{3})\d{4}(?!\d)',
            lambda m: f"{m.group(1) or ''}{m.group(2)}****",
            result,
        )

        result = re.sub(
            r'([a-zA-Z0-9._%+-]{2})[a-zA-Z0-9._%+-]*(@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
            r'\1***\2',
            result,
            flags=re.IGNORECASE,
        )

        result = re.sub(
            r'\b(\d{4})\d{8,11}(\d{4})\b',
            r'\1' + REDACTED_PARTIAL + r'\2',
            result,
        )

        result = re.sub(
            r'\b(?:(\d{1,3}\.\d{1,3})\.\d{1,3}\.\d{1,3})\b',
            r'\1.xxx.xxx',
            result,
        )

        return result

    def detect(self, content: Any, path: str = "") -> FilterResult:
        """检测内容中是否包含敏感信息。

        将内容转换为文本后，使用所有内容检测模式进行匹配，
        收集所有违规项，并根据配置的阻止策略决定是否允许通过。

        Args:
            content: 待检测的内容（支持 str、dict、list 等类型）
            path: 当前检测路径，用于嵌套结构定位（预留参数）

        Returns:
            FilterResult 检测结果对象，包含：
            - allowed: 是否允许通过
            - violations: 违规项列表（SensitiveMatch）
            - sanitized_content: 脱敏后的内容（如允许通过）
            - action_taken: 采取的动作（pass/blocked_critical/blocked_high）
        """
        if content is None:
            return FilterResult(allowed=True, action_taken="pass_empty")

        if isinstance(content, (dict, list)):
            text_content = json.dumps(content, ensure_ascii=False)
        elif isinstance(content, str):
            text_content = content
        else:
            text_content = str(content)

        violations: List[SensitiveMatch] = []

        for name, pattern in self._compiled_content.items():
            config = self._content_patterns[name]
            level = config.get("level", SensitiveLevel.MEDIUM)

            for match in pattern.finditer(text_content):
                matched_text = match.group(0)
                sanitized = self._sanitize_match(name, matched_text)

                violation = SensitiveMatch(
                    pattern_name=name,
                    matched_text=sanitized,
                    start_pos=match.start(),
                    end_pos=match.end(),
                    level=level,
                    suggestion=config.get("description", ""),
                )
                violations.append(violation)

        max_level_value = max(
            [v.level.value for v in violations],
            default=SensitiveLevel.SAFE.value,
        )
        max_level = SensitiveLevel(max_level_value)

        allowed = True
        action_taken = "pass"

        if max_level == SensitiveLevel.CRITICAL and self._block_critical:
            allowed = False
            action_taken = "blocked_critical"
        elif max_level == SensitiveLevel.HIGH and self._block_high:
            allowed = False
            action_taken = "blocked_high"

        sanitized_content = None
        if allowed and violations:
            sanitized_content = self.mask(text_content)

        return FilterResult(
            allowed=allowed,
            violations=violations,
            sanitized_content=sanitized_content,
            action_taken=action_taken,
        )

    def detect_and_sanitize(self, content: Any) -> Tuple[bool, Optional[Any]]:
        """检测内容并返回脱敏结果。

        便捷方法，内部调用 detect()，直接返回是否允许和脱敏内容。

        Args:
            content: 待检测的内容

        Returns:
            元组 (是否允许通过, 脱敏后的内容或 None)
        """
        result = self.detect(content)

        if result.allowed:
            return True, result.sanitized_content
        return False, None

    def _sanitize_match(self, pattern_name: str, matched_text: str) -> str:
        """脱敏单个匹配项

        Args:
            pattern_name: 模式名称
            matched_text: 匹配的原始文本

        Returns:
            脱敏后的文本
        """
        if pattern_name in ("password_field", "password", "sk_key_general"):
            if len(matched_text) > 6:
                return matched_text[:3] + "***"
        elif pattern_name == "jwt_token":
            parts = matched_text.split(".")
            if len(parts) == 3:
                return f"{parts[0]}.{parts[1][:10]}..."
        elif pattern_name in ("phone_cn", "phone_cn_area"):
            digits = re.sub(r'\D', '', matched_text)
            if len(digits) >= 11:
                return f"{digits[:3]}****{digits[-4:]}"
        elif pattern_name == "phone_hk":
            digits = re.sub(r'\D', '', matched_text)
            if len(digits) >= 8:
                return f"{digits[:4]}****"
        elif pattern_name == "email":
            parts = matched_text.split("@")
            if len(parts) == 2:
                return f"{parts[0][:2]}***@{parts[1]}"
        elif pattern_name == "china_id":
            if len(matched_text) == 18:
                return f"{matched_text[:6]}********{matched_text[14:]}"
        elif pattern_name == "api_key_field":
            return f"{matched_text[:8]}..."
        elif pattern_name == "bank_card":
            if len(matched_text) >= 8:
                return f"{matched_text[:4]}****{matched_text[-4:]}"
        elif pattern_name == "ip_v4":
            parts = matched_text.split(".")
            if len(parts) == 4:
                return f"{parts[0]}.{parts[1]}.xxx.xxx"

        if len(matched_text) > 6:
            return f"{matched_text[:2]}...{matched_text[-2:]}"
        return "***"

    def filter_data(self, data: Any) -> Any:
        """通用数据过滤（主要 API）。

        根据输入数据的类型自动选择对应的过滤方式：
        - dict: 调用 filter_dict 递归过滤字段名和字段值
        - list: 调用 filter_list 递归过滤每个元素
        - str: 调用 mask 进行内容脱敏
        - 其他类型: 直接返回原值

        Args:
            data: 待过滤的数据，支持任意类型

        Returns:
            过滤后的数据，类型与输入一致
        """
        if isinstance(data, dict):
            return self.filter_dict(data)
        elif isinstance(data, list):
            return self.filter_list(data)
        elif isinstance(data, str):
            return self.mask(data)
        else:
            return data

    def filter_json(self, data: Any) -> Any:
        """通用 JSON 数据过滤（filter_data 的别名）。

        与 filter_data 功能相同，提供语义化的别名便于 JSON 场景使用。

        Args:
            data: 任意 JSON 数据

        Returns:
            过滤后的数据
        """
        return self.filter_data(data)

    def filter_string(self, text: str) -> str:
        """过滤字符串中的敏感信息（mask 的别名）。

        与 mask 功能相同，提供语义化的别名便于字符串场景使用。

        Args:
            text: 待过滤的字符串

        Returns:
            过滤后的字符串
        """
        return self.mask(text)

    def add_pattern(
        self,
        name: str,
        pattern: str,
        level: SensitiveLevel = SensitiveLevel.MEDIUM,
        description: str = "",
    ) -> None:
        """添加自定义内容检测模式。

        运行时动态添加新的敏感数据检测规则，无需修改源码。

        Args:
            name: 模式名称，唯一标识
            pattern: 正则表达式字符串
            level: 敏感等级，默认 MEDIUM
            description: 模式描述，用于提示和文档
        """
        self._content_patterns[name] = {
            "pattern": pattern,
            "level": level,
            "description": description,
        }
        try:
            self._compiled_content[name] = re.compile(pattern)
        except re.error as e:
            logger.error("[SensitiveFilter] 模式编译失败: %s, error=%s", name, e)

    def get_stats(self) -> Dict[str, Any]:
        """获取过滤器的统计信息。

        用于监控和调试，返回当前加载的模式数量和配置状态。

        Returns:
            包含统计信息的字典：
            - total_key_patterns: 敏感字段名模式总数
            - total_redact_patterns: 完全屏蔽字段模式总数
            - total_content_patterns: 内容检测模式总数
            - content_patterns_by_level: 按敏感等级分组的模式计数
            - block_critical: 是否阻止 CRITICAL 级别
            - block_high: 是否阻止 HIGH 级别
        """
        level_counts = {level.name: 0 for level in SensitiveLevel}
        for config in self._content_patterns.values():
            level = config.get("level", SensitiveLevel.MEDIUM)
            level_counts[level.name] += 1

        return {
            "total_key_patterns": len(self._key_patterns),
            "total_redact_patterns": len(self._redact_patterns),
            "total_content_patterns": len(self._content_patterns),
            "content_patterns_by_level": level_counts,
            "block_critical": self._block_critical,
            "block_high": self._block_high,
        }

    def filter(self, data: Any) -> Any:
        """统一过滤入口（智能分发）。

        根据输入类型自动选择处理方式：
        - logging.LogRecord: 作为日志过滤器使用，脱敏后返回 True
        - dict/list/str: 作为数据过滤器使用，返回过滤后的数据
        - 其他类型: 直接返回原值

        Args:
            data: 待过滤的数据或日志记录

        Returns:
            过滤后的数据（数据过滤模式）或 True（日志过滤模式）
        """
        if isinstance(data, logging.LogRecord):
            return self._filter_log_record(data)
        else:
            return self.filter_data(data)

    def _filter_log_record(self, record: logging.LogRecord) -> bool:
        """日志过滤器接口（logging.Filter 兼容）

        Args:
            record: 日志记录对象

        Returns:
            True（始终允许记录，只是脱敏内容）
        """
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = self.mask(record.msg)

        if hasattr(record, 'args') and isinstance(record.args, tuple):
            sanitized_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    sanitized_args.append(self.mask(arg))
                elif isinstance(arg, dict):
                    sanitized_args.append(self.filter_dict(arg))
                else:
                    sanitized_args.append(arg)
            record.args = tuple(sanitized_args)

        return True


_default_filter: Optional[SensitiveDataFilter] = None


def get_default_filter() -> SensitiveDataFilter:
    """获取全局默认过滤器实例（单例模式）。

    第一次调用时创建实例，后续调用返回同一实例。
    用于全局统一的敏感数据过滤场景。

    Returns:
        默认 SensitiveDataFilter 实例
    """
    global _default_filter
    if _default_filter is None:
        _default_filter = SensitiveDataFilter()
    return _default_filter


def filter_sensitive_data(data: Any) -> Any:
    """使用默认过滤器过滤敏感数据。

    便捷函数，内部调用 get_default_filter().filter(data)。

    Args:
        data: 待过滤的数据，支持 dict/list/str 等

    Returns:
        过滤后的数据
    """
    return get_default_filter().filter(data)


def filter_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """使用默认过滤器过滤字典中的敏感字段。

    便捷函数，内部调用 get_default_filter().filter_dict(data)。

    Args:
        data: 待过滤的字典

    Returns:
        过滤后的字典
    """
    return get_default_filter().filter_dict(data)


def filter_string(text: str) -> str:
    """使用默认过滤器过滤字符串中的敏感信息。

    便捷函数，内部调用 get_default_filter().mask(text)。

    Args:
        text: 待过滤的字符串

    Returns:
        过滤后的字符串
    """
    return get_default_filter().mask(text)


def sensitive_filter(key: str) -> bool:
    """快捷函数：检查字段名是否为敏感字段。

    便捷函数，内部调用 get_default_filter().is_sensitive_key(key)。

    Args:
        key: 字段名

    Returns:
        True 如果是敏感字段，False 否则
    """
    return get_default_filter().is_sensitive_key(key)


def create_filter(
    additional_key_patterns: Optional[List[str]] = None,
    **kwargs: Any,
) -> SensitiveDataFilter:
    """创建自定义配置的过滤器实例。

    工厂函数，用于创建带有自定义配置的过滤器。

    Args:
        additional_key_patterns: 额外的敏感字段名正则模式列表
        **kwargs: 其他参数传递给 SensitiveDataFilter 构造函数

    Returns:
        新的 SensitiveDataFilter 实例
    """
    return SensitiveDataFilter(
        additional_key_patterns=additional_key_patterns,
        **kwargs,
    )


def mask_ip(ip: str) -> str:
    """脱敏 IP 地址（保留前两段）。

    将 IP 地址的前两段保留，后两段替换为 xxx。

    Args:
        ip: IP 地址字符串

    Returns:
        脱敏后的 IP 地址
    """
    if not ip:
        return ip
    parts = ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.xxx.xxx"
    return ip


__all__ = [
    'SensitiveDataFilter',
    'SensitiveLevel',
    'SensitiveMatch',
    'FilterResult',
    'filter_sensitive_data',
    'filter_dict',
    'filter_string',
    'sensitive_filter',
    'create_filter',
    'get_default_filter',
    'mask_ip',
    'REDACTED_VALUE',
    'REDACTED_PARTIAL',
]
