"""OutputGuard — 输出护栏，PII 遮盖

"遮盖不阻塞"策略：检测到敏感信息时遮盖而非拒绝输出。
"""

import re
import logging
import json
import uuid
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



@dataclass
class OutputResult:
    """输出检查结果"""
    modified: bool = False
    filtered: str = ""
    redacted_fields: List[str] = field(default_factory=list)


# ── PII 模式 ──────────────────────────────────────────────────────────

def _pii_patterns():
    """返回 PII 匹配模式列表 (pattern, field_name, replacement)"""
    return [
        # 中国大陆手机号: 1xx-xxxx-xxxx
        (re.compile(r'1[3-9]\d{9}'), "手机号",
         lambda m: m.group()[:3] + "****" + m.group()[-4:]),

        # 身份证号: 18 位
        (re.compile(r'[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]'),
         "身份证号",
         lambda m: m.group()[:6] + "********" + m.group()[-4:]),

        # 电子邮箱
        (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
         "邮箱",
         lambda m: m.group()[0] + "***@" + m.group().split('@')[1]),

        # API Key（32 位以上疑似 Key）
        (re.compile(r'\b(sk-|pk-|api[_-]?key[_-]?)[A-Za-z0-9_-]{16,}\b', re.I),
         "API Key",
         lambda m: m.group()[:8] + "****"),

        # 密码泄露（password=xxx 模式）
        (re.compile(r'(?i)(password|passwd|pwd|secret)\s*[=:]\s*\S{6,}'),
         "密码/密钥",
         lambda m: m.group().split('=')[0] + "=****" if '=' in m.group()
                    else m.group().split(':')[0] + ":****"),
    ]


class OutputGuard:
    """输出护栏——PII 遮盖"""

    def __init__(self):
        self._patterns = _pii_patterns()

    def check(self, text: str) -> OutputResult:
        """检查输出文本并遮盖 PII"""
        if not text:
            return OutputResult(filtered=text or "")

        result = OutputResult(filtered=text)
        for pattern, field_name, replacer in self._patterns:
            matches = pattern.findall(text)
            if matches:
                result.modified = True
                result.redacted_fields.append(field_name)
                text = pattern.sub(replacer, text)

        result.filtered = text
        if result.modified:
            logger.info("[OutputGuard] 已遮盖 %d 个字段", len(result.redacted_fields))

        return result


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "output_guard",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
