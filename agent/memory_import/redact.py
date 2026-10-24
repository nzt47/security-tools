"""敏感信息扫描与脱敏（复用云枢既有密钥闸门）。

【为什么复用而不新造】云枢已有一道 manifest 密钥闸门
（agent.subagent.credentials.find_manifest_secrets）：scoped 记忆写入、
分身 bundle 打包都用它。外部记忆导入若另立一套正则，等于制造
第二个"什么算密钥"的事实源 —— 这里只补"可掩码的定位"能力：

- 闸门回答"这段内容里有没有密钥"（form 级，不给位置）；
- 本模块的 PATTERNS 回答"在哪、是什么形态"（用于**就地掩码**）；
- 掩码之后**再过一次闸门**：仍命中 => 该条记录拒绝导入（fail-closed），
  因为"说不清是什么的密钥"不该被写进云枢。

【不易】命中明细只报形态名与次数，**任何日志/预览/审计都不写明文**；
闸门不可用时按"有密钥"处理（与 scoped_store 的保守口径一致）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Pattern, Tuple

#: 掩码占位（不含任何原文片段）
MASK = "***"
#: 闸门不可用时的占位 form 名
GATE_UNAVAILABLE = "credentials_gate_unavailable"

#: (形态名, 正则, 替换函数)；替换函数只保留"键名 + 掩码"，丢正文
_Pattern = Tuple[str, Pattern[str], Callable[[re.Match], str]]


def _whole(name: str) -> Callable[[re.Match], str]:
    return lambda _m: f"<redacted:{name}>"


def _keep_key(name: str) -> Callable[[re.Match], str]:
    def repl(match: re.Match) -> str:
        key = match.group("key") if "key" in match.groupdict() else ""
        return f"{key}={MASK}" if key else f"<redacted:{name}>"
    return repl


PATTERNS: List[_Pattern] = [
    ("openai_sk", re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), _whole("openai_sk")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), _whole("github_token")),
    ("aws_akia", re.compile(r"AKIA[0-9A-Z]{16}"), _whole("aws_akia")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     _whole("private_key")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\."
                       r"[A-Za-z0-9_\-]{8,}"), _whole("jwt")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}"), _whole("bearer")),
    ("password_assign", re.compile(
        r"(?i)\b(?P<key>password|passwd|pwd)\b\s*[:=]\s*"
        r"['\"]?[^\s'\"，。;]{6,}"), _keep_key("password_assign")),
    ("api_key_assign", re.compile(
        r"(?i)\b(?P<key>api[_-]?key|secret[_-]?key|access[_-]?token|"
        r"auth[_-]?token)\b\s*[:=]\s*['\"]?[^\s'\"，。;]{8,}"),
     _keep_key("api_key_assign")),
]


@dataclass
class RedactionOutcome:
    """一条文本的脱敏结果（hits 只有形态->次数，绝无明文）。"""

    text: str
    hits: Dict[str, int] = field(default_factory=dict)
    gate_forms: List[str] = field(default_factory=list)
    blocked: bool = False
    reason: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.hits)

    def to_dict(self) -> Dict[str, object]:
        return {"hits": dict(self.hits), "gate_forms": list(self.gate_forms),
                "blocked": self.blocked, "reason": self.reason,
                "changed": self.changed}


def gate_check(text: str) -> Tuple[List[str], bool]:
    """调既有密钥闸门 -> (form 列表, 闸门是否可用)。

    入参形状固定为 {"content": text}：闸门按键名判定的规则不会误触发，
    只有"值形态像密钥"才会命中（这正是我们要的第二道保险）。
    """
    try:
        from agent.subagent.credentials import find_manifest_secrets
    except Exception:  # noqa: BLE001 闸门不可用 => fail-closed
        return [GATE_UNAVAILABLE], False
    try:
        forms = find_manifest_secrets({"content": str(text or "")})
    except Exception:  # noqa: BLE001 闸门自身异常同样按不可用处理
        return [GATE_UNAVAILABLE], False
    return [str(f) for f in (forms or [])], True


def scan(text: str) -> Dict[str, int]:
    """只扫描不修改（用于统计命中次数）。"""
    counts: Dict[str, int] = {}
    for name, pattern, _repl in PATTERNS:
        found = len(pattern.findall(str(text or "")))
        if found:
            counts[name] = counts.get(name, 0) + found
    return counts


def redact_text(text: str) -> RedactionOutcome:
    """逐形态就地掩码；掩码后仍过不了闸门 => blocked。"""
    source = str(text or "")
    hits: Dict[str, int] = {}
    masked = source
    for name, pattern, repl in PATTERNS:
        masked, count = pattern.subn(repl, masked)
        if count:
            hits[name] = hits.get(name, 0) + count
    forms, available = gate_check(masked)
    blocked = bool(forms)
    reason = ""
    if blocked:
        reason = ("密钥闸门不可用，按有密钥处理" if not available
                  else "掩码后仍命中密钥形态：" + ",".join(sorted(set(forms))))
    return RedactionOutcome(text=masked, hits=hits, gate_forms=forms,
                            blocked=blocked, reason=reason)


__all__ = [
    "GATE_UNAVAILABLE", "MASK", "PATTERNS", "RedactionOutcome",
    "gate_check", "redact_text", "scan",
]
