
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]

"""RuleMatcher — 规则匹配器（关键词/正则/自定义函数三种匹配模式）

提供标准化的匹配函数工厂，供 Rule.match_fn 使用。
"""

import re
from typing import Callable, List, Pattern


def keyword_match(keywords: List[str], case_sensitive: bool = False) -> Callable[[str], bool]:
    """关键词匹配——输入包含任一关键词即匹配

    防御性修复：过滤空字符串关键词，避免 "" in text 始终为 True 导致匹配所有文本。
    与 impact_analysis.py 的 _find_tests_for_module 空字符串匹配 Bug 同源。
    """
    # 过滤空字符串与空白关键词，防止 "" in text 始终返回 True
    safe_keywords = [kw for kw in keywords if kw and kw.strip()]
    if case_sensitive:
        def _match(text: str) -> bool:
            return any(kw in text for kw in safe_keywords)
    else:
        keys_lower = [k.lower() for k in safe_keywords]
        def _match(text: str) -> bool:
            t = text.lower()
            return any(kw in t for kw in keys_lower)
    return _match


def regex_match(pattern: str, flags: int = re.IGNORECASE) -> Callable[[str], bool]:
    """正则匹配"""
    compiled = re.compile(pattern, flags)
    def _match(text: str) -> bool:
        return bool(compiled.search(text))
    return _match


def function_match(fn: Callable[[str], bool]) -> Callable[[str], bool]:
    """自定义函数匹配"""
    return fn


class RuleMatcher:
    """规则匹配器入口"""

    @staticmethod
    def match_text(text: str, patterns) -> bool:
        """通用匹配——支持多种模式"""
        for p in patterns:
            if isinstance(p, Pattern):
                if p.search(text):
                    return True
            elif callable(p):
                if p(text):
                    return True
            elif isinstance(p, str):
                # 防御性修复：跳过空字符串，避免 "" in text 始终为 True 导致匹配所有文本
                if p and p.lower() in text.lower():
                    return True
        return False


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
            "module_name": "matcher",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
