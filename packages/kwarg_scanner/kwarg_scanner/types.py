"""kwarg_scanner 类型定义 — 数据类与枚举"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class RiskLevel(IntEnum):
    """风险等级枚举（值越大风险越高）"""

    LOW = 0
    MEDIUM = 1
    HIGH = 2

    @classmethod
    def from_str(cls, s: str) -> "RiskLevel":
        return {"LOW": cls.LOW, "MEDIUM": cls.MEDIUM, "HIGH": cls.HIGH}[s.upper()]

    def __str__(self) -> str:
        return self.name


@dataclass
class ScanConfig:
    """扫描器配置

    Attributes:
        min_risk: 最低报告风险等级
        exclude_dirs: 排除的目录名集合
        filtered_name_prefixes: 已过滤变量名前缀（识别为安全）
        filtered_name_suffixes: 已过滤变量名后缀
        enable_logging: 是否输出结构化日志
    """

    min_risk: RiskLevel = RiskLevel.LOW
    exclude_dirs: Set[str] = field(default_factory=lambda: {
        "__pycache__", ".git", "node_modules", "venv", ".venv",
        "env", ".env", ".pytest_cache", "dist", "build", "egg-info",
    })
    filtered_name_prefixes: Tuple[str, ...] = ("safe_", "filtered_", "clean_")
    filtered_name_suffixes: Tuple[str, ...] = ("_safe", "_filtered", "_clean")
    enable_logging: bool = False


@dataclass
class FuncSignature:
    """函数签名摘要"""

    name: str
    params: Set[str]
    kwonly_params: Set[str]
    has_var_kw: bool
    lineno: int


@dataclass
class ConflictFinding:
    """冲突发现记录"""

    file: str
    lineno: int
    col: int
    func_name: str
    explicit_kwargs: List[str]
    spread_expr: str
    risk_level: str  # "HIGH" / "MEDIUM" / "LOW"
    reason: str
    conflicting_params: List[str] = field(default_factory=list)
    suggested_fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转为字典（用于 JSON 序列化）"""
        return {
            "file": self.file,
            "lineno": self.lineno,
            "col": self.col,
            "func_name": self.func_name,
            "explicit_kwargs": self.explicit_kwargs,
            "spread_expr": self.spread_expr,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "conflicting_params": self.conflicting_params,
            "suggested_fix": self.suggested_fix,
        }
