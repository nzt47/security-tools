"""伦理规则引擎——不可突破的硬约束

【匹配口径（2026-09-22 修复 · 待办台账 #2）】规则只对**参数里出现的值**判定，
且 format / shutdown 这类**命令动词**必须落在「命令位」上。

修复前的实现是 "format" in str(params) —— 把整个参数字典（**含键名**）转成
字符串做裸子串匹配。两个后果都实测到了：

① **键名参与判定**：delegate 的必填参数 artifact_format 让**每一次**委派都命中
   E002「禁止格式化磁盘」，理由与任务内容毫无关系。键名由工具 schema 固定，
   不是调用方表达的内容 ⇒ 命中与否只取决于 schema，与"这次要干什么"无关。
   影响不止"理由难看"：在 CP_TOOL_CONFIRM_LEVEL_ENFORCE=0（回滚态）与
   CP_TOOL_CONFIRM_LEVEL_SHADOW=1（影子态）下，分级层本应放行，这条误报却
   **独自**把调用重新拦下 —— 与两个开关的书面承诺相反。
② **裸子串**：git log --format=%H、"'{}'".format(x)、grep shutdown 全部命中
   ⇒ 误报面大到"理由不可信"，把排查引向"闸门坏了"。

故判据拆成两条：**只看值**（键名永不参与）+ **命令动词看位置**（串首，或紧跟
shell 分隔符/引号/括号/换行之后，容许 sudo / doas / cmd /c / powershell -c 前缀）。
两条都只**收窄误报**，不放宽真命中：format C: / shutdown -h now /
bash -c "shutdown -h now" 仍然命中，由 tests/unit/test_ethics_engine.py 逐条钉住。
"""
import logging
import re
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# ─────────────────────────────────────────────────────────────
# 匹配基元（「命令位」判定）
# ─────────────────────────────────────────────────────────────

#: 命令位前缀：串首，或紧跟 shell 分隔符 / 引号 / 括号 / 换行之后；
#: 其后再容许常见的提权与解释器前缀。
#: 为什么需要它：--format=%H、"{}".format(x)、grep shutdown 里的同名子串都
#: 不是"要执行这条命令"，裸子串匹配会把它们全部误判成"格式化磁盘""关机"。
_CMD_PREFIX = (r'(?:^|[;&|`(){}\[\]"\n]\s*)'
               r"(?:sudo\s+|doas\s+|cmd(?:\.exe)?\s+/c\s+|"
               r"powershell(?:\.exe)?\s+-c(?:ommand)?\s+)?")


def _command_pattern(verb: str) -> "re.Pattern":
    """命令动词的正则：**处在命令位**，且其后是空白或串尾

    尾巴（其后必须是空白或串尾）把 formatting / shutdowns 这类词形挡在外面。
    """
    return re.compile(_CMD_PREFIX + re.escape(verb) + r"(?:\.exe|\.com)?(?:\s|$)",
                      re.IGNORECASE)


_COMMAND_RE = {
    "format": _command_pattern("format"),
    "shutdown": _command_pattern("shutdown"),
}


# ─────────────────────────────────────────────────────────────
# 参数字典 → 待匹配文本（**只看值**）
# ─────────────────────────────────────────────────────────────


def _iter_values(value: Any) -> Iterator[str]:
    """递归摊平参数里的**值**；映射的**键名永不产出**"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_values(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _iter_values(item)
    elif value is None or isinstance(value, bool):
        return
    else:
        yield str(value)


def _values_text(params: Any) -> str:
    """参数 → 用于匹配的文本（**不含键名**；多值以换行分隔）

    非映射（裸串）原样返回，保持"调用方直接传命令串"的既有用法可用。
    """
    if params is None:
        return ""
    if isinstance(params, str):
        return params
    return "\n".join(_iter_values(params))


class EthicsEngine:
    RULES = [
        {"id": "E001", "desc": "禁止删除系统文件", "check": lambda a, p: "rm -rf /" in str(p) or "del /f" in str(p)},
        {"id": "E002", "desc": "禁止格式化磁盘", "check": lambda a, p: bool(_COMMAND_RE["format"].search(str(p)))},
        {"id": "E003", "desc": "禁止关闭系统", "check": lambda a, p: bool(_COMMAND_RE["shutdown"].search(str(p)))},
        {"id": "E004", "desc": "禁止读取敏感文件", "check": lambda a, p: a == "read_file" and any(x in str(p) for x in ["/etc/passwd", "/etc/shadow"])},
        {"id": "E005", "desc": "禁止自我修改", "check": lambda a, p: a == "write_file" and "orchestrator" in str(p).lower()},
        {"id": "E006", "desc": "禁止生成违法内容", "check": lambda a, p: any(x in str(p) for x in ["破解", "入侵", "病毒", "木马"])},
    ]

    def check(self, action: str, params: dict) -> list[dict]:
        """命中的规则列表（空列表 = 无违规）

        params 先经 _values_text 摊平成"只有值"的文本再交给各规则 ——
        规则实现只见文本，不见键名（口径见模块 docstring）。
        """
        text = _values_text(params)
        violations = []
        for rule in self.RULES:
            if rule["check"](action, text):
                violations.append(rule)
                logger.warning(f"[Ethics] ⛔ {rule['id']}: {rule['desc']}")
        return violations


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(log_dict({'module_name': 'ethics', 'action': action + '.failed', 'error': f'{type(e).__name__}: {e}'}))
        raise
