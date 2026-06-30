"""InputGuard — 输入护栏，检测提示词注入攻击

8 种注入模式检测（指令忽略、System Prompt 泄露、角色扮演越狱、编码绕过等）
命中时返回 BLOCK 动作及相关信息。
"""

import re
import logging
import json
import uuid
from enum import Enum
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class GuardAction(Enum):
    """护栏动作"""
    ALLOW = "allow"
    BLOCK = "block"


@dataclass
class GuardResult:
    """输入检查结果"""
    action: GuardAction
    reason: str = ""
    matched_pattern: str = ""
    confidence: float = 0.0


# ── 注入模式 ──────────────────────────────────────────────────────────
INJECTION_PATTERNS = [
    # 指令忽略
    (re.compile(r"(?i)\bignore\s+(all\s+)?(previous|above|prior)\s+(instructions|directives|commands)"), "指令忽略"),
    (re.compile(r"(?i)\bdisregard\s+(all\s+)?(previous|above|prior)\s+(instructions|directives|commands)"), "指令忽略"),
    # System Prompt 泄露
    (re.compile(r"(?i)(what('s| is) your (system )?prompt|how (are you )?(instructed|programmed)\b)"), "System Prompt 泄露尝试"),
    (re.compile(r"(?i)(reveal|show|print|output|display)\s+(your\s+)?(system\s+)?(prompt|instructions|directives)"), "System Prompt 泄露尝试"),
    # 角色扮演越狱
    (re.compile(r"(?i)(act\s+as\s+(if\s+you\s+(are|were)\s+)?an?|you\s+are\s+now\s+|pretend\s+to\s+be\s+)"
               r"(dan|jailbreak|unfiltered|unrestricted|nofilter|uncensored)"), "角色扮演越狱"),
    # 编码绕过
    (re.compile(r"(?i)(base64|rot13|hex|unicode\s+escape)\s*(encode|decode|convert|bypass)"), "编码绕过"),
    # XML/JSON 注入
    (re.compile(r"(?i)(<\s*(system|instruction|prompt)\s*>|```(system|instruction|prompt))"), "XML/JSON 注入"),
    # Do Anything Now
    (re.compile(r"(?i)\bDAN\b"), "DAN 越狱"),
    # 分隔符绕过
    (re.compile(r"(?i)(new\s+instructions|override\s+(mode|protocol|setting|constraint))"), "分隔符绕过"),
    # 多语言混淆
    (re.compile(r"[Ѐ-ӿ؀-ۿ฀-๿]{10,}"), "多语言混淆（疑似绕过）"),
]


class InputGuard:
    """输入护栏——检测提示词注入"""

    def __init__(self, max_input_length: int = 100_000):
        self.max_input_length = max_input_length

    def check(self, text: str) -> GuardResult:
        """检查输入文本是否包含注入攻击"""
        if not text or not text.strip():
            return GuardResult(GuardAction.BLOCK, "空输入", "empty_input")

        if len(text) > self.max_input_length:
            return GuardResult(GuardAction.BLOCK, f"输入超长 ({len(text)} > {self.max_input_length})", "input_too_long")

        for pattern, category in INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                logger.warning("[InputGuard] 检测到 %s: %r", category, match.group()[:60])
                return GuardResult(
                    GuardAction.BLOCK,
                    f"检测到 {category}",
                    matched_pattern=match.group()[:80],
                )

        return GuardResult(GuardAction.ALLOW)


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
            "module_name": "input_guard",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
