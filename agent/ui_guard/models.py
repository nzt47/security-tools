"""ui_guard 的数据模型（与前端/工具共享的稳定形状）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Finding:
    """一条违规（file:line 可定位；fixable 表示本模块能自动修）。"""

    rule: str
    severity: str          # error | warning | info
    file: str              # 仓库相对路径
    line: int
    message: str
    fix_hint: str = ""
    snippet: str = ""
    fixable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "fix_hint": self.fix_hint,
            "snippet": self.snippet,
            "fixable": self.fixable,
        }


@dataclass
class Report:
    """一次检查的完整结果（schema_version 用于前端/工具判断形状）。"""

    schema_version: int = 1
    mode: str = "fast"                 # fast | full
    generated_at: str = ""
    commit: str = ""
    findings: List[Finding] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    by_rule: Dict[str, int] = field(default_factory=dict)
    scanned_files: int = 0
    baseline: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_dict(self, *, limit: int = 500) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "generated_at": self.generated_at,
            "commit": self.commit,
            "counts": dict(self.counts),
            "by_rule": dict(self.by_rule),
            "scanned_files": self.scanned_files,
            "baseline": dict(self.baseline),
            "errors": list(self.errors),
            "duration_ms": self.duration_ms,
            "findings": [f.to_dict() for f in self.findings[: max(0, int(limit))]],
            "findings_total": len(self.findings),
        }
