"""RuleMatcher — 规则匹配器（关键词/正则/自定义函数三种匹配模式）

提供标准化的匹配函数工厂，供 Rule.match_fn 使用。
"""

import re
from typing import Callable, List, Pattern


def keyword_match(keywords: List[str], case_sensitive: bool = False) -> Callable[[str], bool]:
    """关键词匹配——输入包含任一关键词即匹配"""
    if case_sensitive:
        def _match(text: str) -> bool:
            return any(kw in text for kw in keywords)
    else:
        keys_lower = [k.lower() for k in keywords]
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
                if p.lower() in text.lower():
                    return True
        return False
