"""伦理规则引擎——不可突破的硬约束"""
import logging

logger = logging.getLogger(__name__)

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
