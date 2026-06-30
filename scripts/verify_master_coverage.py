#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
master 分支覆盖率稳定性验证脚本。

功能：
1. 预检查所有依赖文件完整性（config.yaml / 脚本 / 契约文件 / 边界配置）
2. 验证 CI 配置触发分支包含 master
3. 验证 UTF-8 编码修复已生效
4. 运行 visibility_report.py 验证 6 项指标全部达标
5. 运行 check_boundary_coverage.py 验证边界覆盖率
6. 对比历史基线，检测覆盖率回归

使用方式：
    # 标准验证（使用 config.yaml 中的阈值）
    python scripts/verify_master_coverage.py

    # 严格模式（使用阶段1目标值作为阈值）
    python scripts/verify_master_coverage.py --strict

    # JSON 输出模式（供 CI 消费）
    python scripts/verify_master_coverage.py --json

    # 指定配置文件
    python scripts/verify_master_coverage.py --config config.yaml

退出码：
    0 = 全部通过
    1 = 覆盖率不达标
    2 = 依赖文件缺失或脚本错误
    3 = 覆盖率回归（比历史基线下降超过 5pp）

可观测性约束：
    - 结构化日志：包含 trace_id/module_name/action/duration_ms
    - 边界显性化：失败时抛出带明确错误码的异常
    - 埋点预留：关键检查点预留 trackEvent 占位符
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ==============================================================================
# 可观测性：结构化日志
# ==============================================================================
TRACE_ID = f"verify_{int(time.time())}"


def log(action: str, **kwargs) -> None:
    """输出 JSON 格式的结构化日志。"""
    entry = {
        "trace_id": TRACE_ID,
        "module_name": "master_coverage_verifier",
        "action": action,
        "timestamp": datetime.now().isoformat(),
        "duration_ms": kwargs.pop("duration_ms", 0),
        **kwargs,
    }
    print(json.dumps(entry, ensure_ascii=False), flush=True)


def trackEvent(event_name: str, payload: Dict[str, Any]) -> None:
    """埋点预留：关键验证节点的追踪调用占位符。"""
    pass


# ==============================================================================
# 错误码定义
# ==============================================================================
class VerifyError(Exception):
    """验证错误基类，携带明确错误码。"""

    def __init__(self, code: str, message: str, details: Optional[Dict] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code}] {message}")


# 错误码常量
ERR_FILE_MISSING = "FILE_MISSING"
ERR_CI_CONFIG = "CI_CONFIG_INVALID"
ERR_ENCODING_FIX = "ENCODING_FIX_MISSING"
ERR_THRESHOLD_FAIL = "THRESHOLD_NOT_MET"
ERR_BOUNDARY_FAIL = "BOUNDARY_COVERAGE_FAIL"
ERR_CONTRACT_FAIL = "CONTRACT_INVALID"
ERR_REGRESSION = "COVERAGE_REGRESSION"
ERR_SUBPROCESS = "SUBPROCESS_ERROR"


# ==============================================================================
# 预检查器
# ==============================================================================
class PreflightChecker:
    """预检查所有依赖文件和配置完整性。"""

    # 必须存在的文件列表
    REQUIRED_FILES = [
        "config.yaml",
        "scripts/visibility_report.py",
        "scripts/check_boundary_coverage.py",
        "tests/boundary_config.yaml",
        ".github/workflows/observability-ci.yml",
    ]

    # 必须存在的契约文件
    REQUIRED_CONTRACTS = [
        "tests/contract/contracts/chat_api_contract.json",
        "tests/contract/contracts/dashboard_api_contract.json",
        "tests/contract/contracts/health_api_contract.json",
    ]

    def __init__(self, project_root: Path):
        self.root = project_root

    def check_all(self) -> List[Dict[str, Any]]:
        """执行所有预检查，返回检查结果列表。"""
        results = []
        results.append(self._check_files())
        results.append(self._check_contracts())
        results.append(self._check_ci_branches())
        results.append(self._check_encoding_fix())
        return results

    def _check_files(self) -> Dict[str, Any]:
        """检查必需文件是否存在。"""
        start = time.time()
        missing = []
        for f in self.REQUIRED_FILES:
            path = self.root / f
            if not path.exists():
                missing.append(f)

        result = {
            "check": "required_files",
            "passed": len(missing) == 0,
            "missing": missing,
            "checked_count": len(self.REQUIRED_FILES),
            "duration_ms": int((time.time() - start) * 1000),
        }
        log("preflight.files", **result)

        if missing:
            raise VerifyError(
                ERR_FILE_MISSING,
                f"必需文件缺失: {', '.join(missing)}",
                {"missing_files": missing},
            )
        return result

    def _check_contracts(self) -> Dict[str, Any]:
        """检查契约文件是否存在且格式正确。"""
        start = time.time()
        issues = []
        for contract_path in self.REQUIRED_CONTRACTS:
            full_path = self.root / contract_path
            if not full_path.exists():
                issues.append({"file": contract_path, "error": "文件不存在"})
                continue
            try:
                data = json.loads(full_path.read_text(encoding="utf-8"))
                # 验证契约结构
                required_keys = ["name", "consumer", "provider"]
                missing_keys = [k for k in required_keys if k not in data]
                if missing_keys:
                    issues.append({"file": contract_path, "error": f"缺少必需字段: {missing_keys}"})
            except json.JSONDecodeError as e:
                issues.append({"file": contract_path, "error": f"JSON 解析失败: {e}"})

        result = {
            "check": "contract_files",
            "passed": len(issues) == 0,
            "issues": issues,
            "checked_count": len(self.REQUIRED_CONTRACTS),
            "duration_ms": int((time.time() - start) * 1000),
        }
        log("preflight.contracts", **result)

        if issues:
            raise VerifyError(
                ERR_CONTRACT_FAIL,
                f"契约文件验证失败: {len(issues)} 个问题",
                {"issues": issues},
            )
        return result

    def _check_ci_branches(self) -> Dict[str, Any]:
        """检查 CI 配置是否包含 master 分支触发。"""
        start = time.time()
        ci_path = self.root / ".github" / "workflows" / "observability-ci.yml"
        content = ci_path.read_text(encoding="utf-8")

        # 检查 push.branches 是否包含 master
        has_master = re.search(r"push:\s*\n\s*branches:\s*\n(\s*-\s*\w+\s*\n)+", content)
        branches = re.findall(r"-\s*(\w+)", content[:content.index("paths:")]) if "paths:" in content else []
        master_included = "master" in branches

        result = {
            "check": "ci_branches",
            "passed": master_included,
            "branches_found": branches,
            "master_included": master_included,
            "duration_ms": int((time.time() - start) * 1000),
        }
        log("preflight.ci_branches", **result)

        if not master_included:
            raise VerifyError(
                ERR_CI_CONFIG,
                "observability-ci.yml 的 push.branches 未包含 master 分支",
                {"branches_found": branches},
            )
        return result

    def _check_encoding_fix(self) -> Dict[str, Any]:
        """检查 UTF-8 编码修复是否已应用。"""
        start = time.time()
        issues = []

        # 检查 check_boundary_coverage.py
        boundary_script = (self.root / "scripts" / "check_boundary_coverage.py").read_text(encoding="utf-8")
        if "sys.stdout.reconfigure" not in boundary_script or "utf-8" not in boundary_script.lower():
            issues.append("check_boundary_coverage.py 缺少 sys.stdout.reconfigure UTF-8 修复")

        # 检查 visibility_report.py
        report_script = (self.root / "scripts" / "visibility_report.py").read_text(encoding="utf-8")
        if 'encoding="utf-8"' not in report_script and "encoding='utf-8'" not in report_script:
            issues.append("visibility_report.py 缺少 subprocess encoding='utf-8' 修复")

        result = {
            "check": "encoding_fix",
            "passed": len(issues) == 0,
            "issues": issues,
            "duration_ms": int((time.time() - start) * 1000),
        }
        log("preflight.encoding_fix", **result)

        if issues:
            raise VerifyError(
                ERR_ENCODING_FIX,
                f"UTF-8 编码修复缺失: {len(issues)} 个问题",
                {"issues": issues},
            )
        return result


# ==============================================================================
# 覆盖率验证器
# ==============================================================================
class CoverageVerifier:
    """运行覆盖率脚本并验证结果。"""

    # 阶段1目标值（严格模式使用）
    STRICT_THRESHOLDS = {
        "structured_log_coverage": 50,
        "trace_coverage": 50,
        "test_coverage": 55,
        "boundary_test_coverage": 70,
        "exception_coverage": 70,
        "contract_test_count": 3,
        "track_event_coverage": 30,
    }

    def __init__(self, project_root: Path, config_path: str = "config.yaml", strict: bool = False):
        self.root = project_root
        self.config_path = config_path
        self.strict = strict
        self.thresholds = self._load_thresholds()

    def _load_thresholds(self) -> Dict[str, float]:
        """从 config.yaml 加载阈值，或使用严格模式阈值。"""
        if self.strict:
            log("thresholds.strict_mode", thresholds=self.STRICT_THRESHOLDS)
            return self.STRICT_THRESHOLDS

        config_file = self.root / self.config_path
        if not config_file.exists():
            raise VerifyError(ERR_FILE_MISSING, f"配置文件不存在: {self.config_path}")

        content = config_file.read_text(encoding="utf-8")
        thresholds = {}

        # 解析 visibility_thresholds 部分
        threshold_patterns = {
            "structured_log_coverage": r"structured_log_coverage:\s*(\d+)",
            "trace_coverage": r"trace_coverage:\s*(\d+)",
            "test_coverage": r"test_coverage:\s*(\d+)",
            "boundary_test_coverage": r"boundary_test_coverage:\s*(\d+)",
            "contract_test_count": r"contract_test_count:\s*(\d+)",
            "track_event_coverage": r"track_event_coverage:\s*(\d+)",
            "exception_coverage": r"exception_coverage:\s*(\d+)",
        }

        for key, pattern in threshold_patterns.items():
            match = re.search(pattern, content)
            if match:
                thresholds[key] = float(match.group(1))
            else:
                # 未找到阈值，使用默认值 0（不检查）
                thresholds[key] = 0.0

        log("thresholds.loaded", thresholds=thresholds, source=self.config_path)
        return thresholds

    def run_visibility_report(self) -> Dict[str, Any]:
        """运行 visibility_report.py 并返回结果。"""
        start = time.time()
        cmd = [
            sys.executable,
            "scripts/visibility_report.py",
            "--config", self.config_path,
            "--output", "docs/observability/visibility_report.md",
            "--verbose",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self.root),
                timeout=120,
            )

            duration_ms = int((time.time() - start) * 1000)
            log(
                "visibility_report.executed",
                returncode=result.returncode,
                stdout_len=len(result.stdout),
                stderr_len=len(result.stderr),
                duration_ms=duration_ms,
            )

            if result.returncode not in (0, 1):
                raise VerifyError(
                    ERR_SUBPROCESS,
                    f"visibility_report.py 异常退出 (code={result.returncode})",
                    {"stderr": result.stderr[:500]},
                )

            # 解析 JSON 结果
            # 优先使用 visibility_report.json，回退到最新的 visibility_report_*.json
            json_path = self.root / "docs" / "observability" / "visibility_report.json"
            if not json_path.exists():
                # 查找带日期的 JSON 文件，取最新修改的
                candidates = sorted(
                    (self.root / "docs" / "observability").glob("visibility_report_*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    json_path = candidates[0]

            if json_path.exists():
                report_data = json.loads(json_path.read_text(encoding="utf-8"))
                # 扁平化 layers[].metrics[] 为 metrics 字典
                # visibility_report.json 结构是分层嵌套的，需要提取所有指标到顶层
                flat_metrics = {}
                for layer in report_data.get("layers", []):
                    for metric in layer.get("metrics", []):
                        name = metric.get("name")
                        value = metric.get("value", 0)
                        if name:
                            flat_metrics[name] = value
                return {
                    "report": report_data,
                    "metrics": flat_metrics,
                    "json_path": str(json_path),
                    "stdout_excerpt": result.stdout[-500:] if result.stdout else "",
                    "duration_ms": duration_ms,
                }
            else:
                # 从 stdout 解析
                return self._parse_stdout(result.stdout, duration_ms)

        except subprocess.TimeoutExpired:
            raise VerifyError(ERR_SUBPROCESS, "visibility_report.py 执行超时（120s）")
        except FileNotFoundError:
            raise VerifyError(ERR_FILE_MISSING, "visibility_report.py 不存在")

    def _parse_stdout(self, stdout: str, duration_ms: int) -> Dict[str, Any]:
        """从 stdout 中解析覆盖率结果。"""
        metrics = {}
        for line in stdout.split("\n"):
            for key in ["structured_log_coverage", "trace_coverage", "test_coverage",
                        "boundary_test_coverage", "contract_test_count",
                        "track_event_coverage", "exception_coverage"]:
                pattern = rf'"{key}"\s*:\s*([\d.]+)'
                match = re.search(pattern, line)
                if match:
                    metrics[key] = float(match.group(1))

        return {"report": {"metrics": metrics}, "stdout_excerpt": stdout[-500:], "duration_ms": duration_ms}

    def verify_thresholds(self, report_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """验证所有指标是否达标。"""
        results = []
        # 优先使用扁平化后的 metrics（run_visibility_report 已提取）
        # 回退到 report.metrics（兼容旧格式）
        metrics = report_data.get("metrics")
        if metrics is None:
            metrics = report_data.get("report", {}).get("metrics", {})
        log("threshold.metrics_loaded", metric_count=len(metrics), metric_keys=list(metrics.keys()))

        for key, threshold in self.thresholds.items():
            actual = metrics.get(key, 0)
            passed = actual >= threshold
            result = {
                "metric": key,
                "actual": actual,
                "threshold": threshold,
                "passed": passed,
                "margin": round(actual - threshold, 1),
            }
            results.append(result)
            log("threshold.check", **result)
            trackEvent("threshold_check", result)

        return results

    def run_boundary_check(self) -> Dict[str, Any]:
        """独立运行边界覆盖率检查。"""
        start = time.time()
        cmd = [
            sys.executable,
            "scripts/check_boundary_coverage.py",
            "--json-only",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self.root),
                timeout=60,
            )

            duration_ms = int((time.time() - start) * 1000)
            log(
                "boundary_check.executed",
                returncode=result.returncode,
                stdout_len=len(result.stdout),
                duration_ms=duration_ms,
            )

            if result.returncode not in (0, 1):
                raise VerifyError(
                    ERR_SUBPROCESS,
                    f"check_boundary_coverage.py 异常退出 (code={result.returncode})",
                    {"stderr": result.stderr[:500]},
                )

            # 解析 JSON 输出
            if result.stdout.strip():
                try:
                    data = json.loads(result.stdout)
                    coverage = 0
                    if data.get("total_tests", 0) > 0:
                        coverage = round(
                            data["total_boundary_tests"] / data["total_tests"] * 100, 1
                        )
                    return {
                        "total_tests": data.get("total_tests", 0),
                        "total_boundary_tests": data.get("total_boundary_tests", 0),
                        "coverage_percent": coverage,
                        "overall_status": data.get("overall_status", "unknown"),
                        "duration_ms": duration_ms,
                    }
                except json.JSONDecodeError as e:
                    raise VerifyError(
                        ERR_SUBPROCESS,
                        f"边界覆盖率 JSON 解析失败: {e}",
                        {"stdout": result.stdout[:200]},
                    )
            else:
                raise VerifyError(
                    ERR_SUBPROCESS,
                    "check_boundary_coverage.py 未输出 JSON",
                    {"stderr": result.stderr[:200]},
                )

        except subprocess.TimeoutExpired:
            raise VerifyError(ERR_SUBPROCESS, "check_boundary_coverage.py 执行超时（60s）")


# ==============================================================================
# 回归检测器
# ==============================================================================
class RegressionDetector:
    """检测覆盖率是否比历史基线大幅下降。"""

    # 历史基线（基于最近一次成功运行）
    BASELINE = {
        "structured_log_coverage": 26.5,
        "trace_coverage": 16.7,
        "test_coverage": 0.6,
        "boundary_test_coverage": 12.2,
        "exception_coverage": 71.6,
        "track_event_coverage": 7.4,
    }

    REGRESSION_THRESHOLD_PP = 5.0  # 下降超过 5pp 视为回归

    @classmethod
    def check(cls, metrics: Dict[str, float]) -> List[Dict[str, Any]]:
        """检查指标是否回归。"""
        results = []
        for key, baseline in cls.BASELINE.items():
            actual = metrics.get(key, 0)
            diff = actual - baseline
            is_regression = diff < -cls.REGRESSION_THRESHOLD_PP
            result = {
                "metric": key,
                "baseline": baseline,
                "actual": actual,
                "diff_pp": round(diff, 1),
                "is_regression": is_regression,
            }
            results.append(result)
            if is_regression:
                log("regression.detected", **result)
            else:
                log("regression.ok", **result)
        return results


# ==============================================================================
# 主验证流程
# ==============================================================================
class MasterCoverageVerifier:
    """主验证器，协调整个验证流程。"""

    def __init__(self, project_root: Path, config_path: str = "config.yaml", strict: bool = False):
        self.root = project_root
        self.preflight = PreflightChecker(project_root)
        self.coverage = CoverageVerifier(project_root, config_path, strict)

    def run(self) -> Dict[str, Any]:
        """执行完整验证流程。"""
        start = time.time()
        trackEvent("verification_started", {"strict": self.coverage.strict})

        result = {
            "trace_id": TRACE_ID,
            "timestamp": datetime.now().isoformat(),
            "strict_mode": self.coverage.strict,
            "preflight": [],
            "threshold_checks": [],
            "boundary_check": None,
            "regression": [],
            "overall_passed": False,
            "errors": [],
        }

        # 阶段 1: 预检查
        log("phase.start", phase="preflight")
        try:
            result["preflight"] = self.preflight.check_all()
            log("phase.complete", phase="preflight", passed=True)
        except VerifyError as e:
            result["errors"].append({"code": e.code, "message": e.message, "details": e.details})
            log("phase.failed", phase="preflight", error=e.message)
            result["overall_passed"] = False
            result["duration_ms"] = int((time.time() - start) * 1000)
            return result

        # 阶段 2: 运行 visibility_report.py
        log("phase.start", phase="visibility_report")
        try:
            report_data = self.coverage.run_visibility_report()
            result["report_data"] = report_data
            log("phase.complete", phase="visibility_report", passed=True)
        except VerifyError as e:
            result["errors"].append({"code": e.code, "message": e.message, "details": e.details})
            log("phase.failed", phase="visibility_report", error=e.message)
            result["overall_passed"] = False
            result["duration_ms"] = int((time.time() - start) * 1000)
            return result

        # 阶段 3: 阈值验证
        log("phase.start", phase="threshold_check")
        result["threshold_checks"] = self.coverage.verify_thresholds(report_data)
        threshold_passed = all(c["passed"] for c in result["threshold_checks"])
        log("phase.complete", phase="threshold_check", passed=threshold_passed)

        # 阶段 4: 边界覆盖率独立验证
        log("phase.start", phase="boundary_check")
        try:
            result["boundary_check"] = self.coverage.run_boundary_check()
            boundary_passed = result["boundary_check"]["coverage_percent"] >= self.coverage.thresholds.get(
                "boundary_test_coverage", 5
            )
            log("phase.complete", phase="boundary_check", passed=boundary_passed)
        except VerifyError as e:
            result["errors"].append({"code": e.code, "message": e.message, "details": e.details})
            log("phase.failed", phase="boundary_check", error=e.message)
            boundary_passed = False

        # 阶段 5: 回归检测
        log("phase.start", phase="regression")
        # 使用扁平化后的 metrics（run_visibility_report 已提取）
        metrics = report_data.get("metrics")
        if metrics is None:
            metrics = report_data.get("report", {}).get("metrics", {})
        result["regression"] = RegressionDetector.check(metrics)
        regression_passed = not any(r["is_regression"] for r in result["regression"])
        log("phase.complete", phase="regression", passed=regression_passed)

        # 最终结果
        result["overall_passed"] = threshold_passed and boundary_passed and regression_passed and len(result["errors"]) == 0
        result["duration_ms"] = int((time.time() - start) * 1000)

        trackEvent("verification_completed", {
            "overall_passed": result["overall_passed"],
            "duration_ms": result["duration_ms"],
        })

        return result


# ==============================================================================
# 报告输出
# ==============================================================================
def print_report(result: Dict[str, Any]) -> None:
    """输出人类可读的验证报告。"""
    print("\n" + "=" * 70)
    print("🔍 master 分支覆盖率稳定性验证报告")
    print("=" * 70)
    print(f"Trace ID: {result['trace_id']}")
    print(f"时间戳: {result['timestamp']}")
    print(f"严格模式: {'是' if result['strict_mode'] else '否'}")
    print(f"耗时: {result.get('duration_ms', 0)}ms")

    # 预检查结果
    print(f"\n{'─' * 40}")
    print("📋 预检查")
    print(f"{'─' * 40}")
    for p in result.get("preflight", []):
        status = "✅" if p["passed"] else "❌"
        print(f"  {status} {p['check']}")

    # 阈值检查结果
    print(f"\n{'─' * 40}")
    print("📊 覆盖率阈值检查")
    print(f"{'─' * 40}")
    for c in result.get("threshold_checks", []):
        status = "✅" if c["passed"] else "❌"
        margin = f"+{c['margin']}pp" if c["margin"] >= 0 else f"{c['margin']}pp"
        print(f"  {status} {c['metric']}: {c['actual']}% (阈值≥{c['threshold']}%, 差值{margin})")

    # 边界检查结果
    if result.get("boundary_check"):
        bc = result["boundary_check"]
        print(f"\n{'─' * 40}")
        print("🛡️ 边界覆盖率独立验证")
        print(f"{'─' * 40}")
        print(f"  覆盖率: {bc['coverage_percent']}%")
        print(f"  边界测试数: {bc['total_boundary_tests']}")
        print(f"  总测试数: {bc['total_tests']}")
        print(f"  状态: {bc['overall_status']}")

    # 回归检测
    if result.get("regression"):
        print(f"\n{'─' * 40}")
        print("📉 回归检测")
        print(f"{'─' * 40}")
        for r in result["regression"]:
            status = "✅" if not r["is_regression"] else "⚠️"
            print(f"  {status} {r['metric']}: 基线{r['baseline']}% → 实际{r['actual']}% (差值{r['diff_pp']}pp)")

    # 错误
    if result.get("errors"):
        print(f"\n{'─' * 40}")
        print("❌ 错误列表")
        print(f"{'─' * 40}")
        for e in result["errors"]:
            print(f"  [{e['code']}] {e['message']}")

    # 最终结论
    print(f"\n{'=' * 70}")
    if result["overall_passed"]:
        print("✅ 验证通过：所有指标达标，可安全推送到 master 分支")
    else:
        print("❌ 验证失败：存在不达标指标或回归，请修复后再推送")
    print(f"{'=' * 70}\n")


# ==============================================================================
# 主入口
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="master 分支覆盖率稳定性验证")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--strict", action="store_true", help="严格模式（使用阶段1目标值）")
    parser.add_argument("--json", action="store_true", help="JSON 输出模式")
    parser.add_argument("--project-root", default=".", help="项目根目录")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()

    try:
        verifier = MasterCoverageVerifier(project_root, args.config, args.strict)
        result = verifier.run()

        if args.json:
            # JSON 输出模式（供 CI 消费）
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_report(result)

        # 保存结果
        output_path = project_root / "docs" / "observability" / "master_coverage_verify.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # 退出码
        if result["overall_passed"]:
            exit(0)
        elif any(e["code"] == ERR_REGRESSION for e in result.get("errors", [])):
            exit(3)
        elif any(e["code"] in (ERR_THRESHOLD_FAIL, ERR_BOUNDARY_FAIL) for e in result.get("errors", [])):
            exit(1)
        else:
            exit(2)

    except VerifyError as e:
        log("verify.failed", code=e.code, message=e.message)
        if args.json:
            print(json.dumps({"error": {"code": e.code, "message": e.message}}, ensure_ascii=False))
        else:
            print(f"\n❌ 验证失败: [{e.code}] {e.message}", file=sys.stderr)
        exit(2)
    except Exception as e:
        log("verify.error", error=str(e))
        print(f"\n💥 未预期错误: {e}", file=sys.stderr)
        exit(2)


if __name__ == "__main__":
    main()
