"""kwarg_scanner 报告生成器 — 文本 / JSON 格式报告"""

from __future__ import annotations

import json
import time
from typing import List, Dict, Any

from .types import ConflictFinding


def format_text_report(findings: List[ConflictFinding]) -> str:
    """生成文本格式报告

    Args:
        findings: 冲突发现列表

    Returns:
        str — Markdown 格式报告文本

    Example:
        >>> from kwarg_scanner import KwargScanner, format_text_report
        >>> scanner = KwargScanner()
        >>> findings = scanner.scan("src/")
        >>> print(format_text_report(findings))
    """
    lines = []
    lines.append("=" * 80)
    lines.append("关键字参数冲突风险扫描报告")
    lines.append("=" * 80)
    lines.append(f"扫描时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"总发现数: {len(findings)}")
    lines.append("")

    by_risk: Dict[str, List[ConflictFinding]] = {"HIGH": [], "MEDIUM": [], "LOW": []}
    for f in findings:
        by_risk.setdefault(f.risk_level, []).append(f)

    for risk in ["HIGH", "MEDIUM", "LOW"]:
        items = by_risk.get(risk, [])
        if not items:
            continue
        icon = {"HIGH": "[HIGH]", "MEDIUM": "[MEDIUM]", "LOW": "[LOW]"}[risk]
        lines.append(f"\n{'─' * 80}")
        lines.append(f"{icon} {risk} ({len(items)} 处)")
        lines.append(f"{'─' * 80}")

        for f in items:
            lines.append(f"\n  [{f.file}:{f.lineno}:{f.col}]")
            lines.append(f"     函数: {f.func_name}")
            lines.append(f"     显式 kwargs: {f.explicit_kwargs}")
            lines.append(f"     **展开: **{f.spread_expr}")
            if f.conflicting_params:
                lines.append(f"     冲突参数: {f.conflicting_params}")
            lines.append(f"     原因: {f.reason}")
            if f.suggested_fix:
                lines.append(f"     建议: {f.suggested_fix}")

    lines.append(f"\n{'=' * 80}")
    lines.append("汇总统计")
    lines.append(f"{'=' * 80}")
    lines.append(f"  HIGH:   {len(by_risk.get('HIGH', []))} 处")
    lines.append(f"  MEDIUM: {len(by_risk.get('MEDIUM', []))} 处")
    lines.append(f"  LOW:    {len(by_risk.get('LOW', []))} 处")
    lines.append(f"  总计:   {len(findings)} 处")
    lines.append("")

    return "\n".join(lines)


def format_json_report(findings: List[ConflictFinding]) -> str:
    """生成 JSON 格式报告

    Args:
        findings: 冲突发现列表

    Returns:
        str — JSON 格式报告字符串

    Example:
        >>> from kwarg_scanner import scan_directory, format_json_report
        >>> findings = scan_directory("src/")
        >>> print(format_json_report(findings))
    """
    return json.dumps({
        "scan_time": time.strftime('%Y-%m-%d %H:%M:%S'),
        "total": len(findings),
        "summary": {
            "HIGH": sum(1 for f in findings if f.risk_level == "HIGH"),
            "MEDIUM": sum(1 for f in findings if f.risk_level == "MEDIUM"),
            "LOW": sum(1 for f in findings if f.risk_level == "LOW"),
        },
        "findings": [f.to_dict() for f in findings],
    }, ensure_ascii=False, indent=2)


# ── SonarQube Generic Issue Import Format (GIIF) ──────────────────────────────
# 参考: https://docs.sonarqube.org/latest/analyzing-source-code/importing-external-issues/

# 风险等级 → SonarQube 严重度 / 类型 映射
_SEVERITY_MAP = {
    "HIGH": "MAJOR",
    "MEDIUM": "MINOR",
    "LOW": "INFO",
}
_TYPE_MAP = {
    "HIGH": "BUG",
    "MEDIUM": "BUG",
    "LOW": "CODE_SMELL",
}


def format_sonarqube_report(findings: List[ConflictFinding]) -> str:
    """生成 SonarQube Generic Issue Import Format (GIIF) 报告

    生成的外部问题报告可直接通过 sonar-scanner 上传到 SonarQube，
    与项目原有分析结果统一展示。

    Args:
        findings: 冲突发现列表

    Returns:
        str — GIIF JSON 字符串

    Example:
        >>> from kwarg_scanner import scan_directory, format_sonarqube_report
        >>> findings = scan_directory("src/")
        >>> print(format_sonarqube_report(findings))
    """
    issues = []
    for f in findings:
        # 组装修复建议消息
        message = f.reason
        if f.suggested_fix:
            message = f"{message} 修复: {f.suggested_fix}"

        # 路径规范化: 去除 ./ 前缀，统一正斜杠
        file_path = f.file.replace("\\", "/")
        if file_path.startswith("./"):
            file_path = file_path[2:]

        issue = {
            "engineId": "kwarg-scanner",
            "ruleId": "python:kwarg-conflict",
            "severity": _SEVERITY_MAP.get(f.risk_level, "INFO"),
            "type": _TYPE_MAP.get(f.risk_level, "CODE_SMELL"),
            "primaryLocation": {
                "message": message,
                "filePath": file_path,
                "textRange": {
                    "startLine": f.lineno,
                    "endLine": f.lineno,
                    "startColumn": max(f.col, 0),
                    "endColumn": max(f.col, 0) + 1,
                },
            },
        }
        issues.append(issue)

    return json.dumps({"issues": issues}, ensure_ascii=False, indent=2)
