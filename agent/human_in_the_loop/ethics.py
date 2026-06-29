"""伦理规则引擎——不可突破的硬约束"""
import logging
import json
import uuid

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


class EthicsEngine:
    RULES = [
        {"id": "E001", "desc": "禁止删除系统文件", "check": lambda a, p: "rm -rf /" in str(p) or "del /f" in str(p)},
        {"id": "E002", "desc": "禁止格式化磁盘", "check": lambda a, p: "format" in str(p)},
        {"id": "E003", "desc": "禁止关闭系统", "check": lambda a, p: "shutdown" in str(p)},
        {"id": "E004", "desc": "禁止读取敏感文件", "check": lambda a, p: a == "read_file" and any(x in str(p) for x in ["/etc/passwd", "/etc/shadow"])},
        {"id": "E005", "desc": "禁止自我修改", "check": lambda a, p: a == "write_file" and "orchestrator" in str(p).lower()},
        {"id": "E006", "desc": "禁止生成违法内容", "check": lambda a, p: any(x in str(p) for x in ["破解", "入侵", "病毒", "木马"])},
    ]

    def check(self, action: str, params: dict) -> list[dict]:
        violations = []
        for rule in self.RULES:
            if rule["check"](action, params):
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
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "ethics",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
