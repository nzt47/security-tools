"""kwarg_scanner — Python 关键字参数冲突风险静态扫描器

检测 `func(explicit_kwarg=x, **dict)` 模式中 dict 含同名键的冲突风险。

快速使用:
    from kwarg_scanner import scan_directory

    findings = scan_directory("src/")
    for f in findings:
        print(f"{f.file}:{f.lineno} {f.risk_level} {f.reason}")

高级用法:
    from kwarg_scanner import KwargScanner, ScanConfig

    config = ScanConfig(min_risk="HIGH")
    scanner = KwargScanner(config)
    findings = scanner.scan("src/")
    report = scanner.format_report(findings, format="text")

CLI:
    $ kwarg-scan --path src/ --min-risk HIGH
    $ kwarg-scan --path src/ --format json --output report.json
"""

from .types import ConflictFinding, FuncSignature, ScanConfig, RiskLevel
from .scanner import KwargScanner, scan_file, scan_directory
from .reporter import format_text_report, format_json_report

__version__ = "1.0.0"
__all__ = [
    "KwargScanner",
    "ScanConfig",
    "ConflictFinding",
    "FuncSignature",
    "RiskLevel",
    "scan_file",
    "scan_directory",
    "format_text_report",
    "format_json_report",
]
