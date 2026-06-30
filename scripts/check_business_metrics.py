import argparse
import json
import os
import re
import sys
from typing import Dict, List, Any, Tuple

CORE_METRICS = [
    "yunshu_interaction_total",
    "yunshu_task_completion_rate",
    "yunshu_memory_search_hit_rate",
    "yunshu_tool_call_total",
    "yunshu_circuit_breaker_trigger_total",
    "yunshu_model_call_total",
    "yunshu_extension_install_total",
    "yunshu_rate_limit_trigger_total",
]

METRIC_NAMING_PATTERN = r"^yunshu_[a-z_]+_(total|duration_seconds|rate|count|success|state|hit_rate|distribution)$"

METRIC_TYPE_PATTERNS = {
    "counter": [r"_total$", r"_success$", r"_distribution$"],
    "gauge": [r"_rate$", r"_count$", r"_state$", r"_hit_rate$"],
    "histogram": [r"_duration_seconds$"],
}

class BusinessMetricsChecker:
    def __init__(self, source_dir: str = "agent"):
        self.source_dir = source_dir
        self.results = {
            "core_metrics_coverage": {},
            "naming_convention": {},
            "new_code_check": {},
            "summary": {},
        }
        self.all_files = []
        self._collect_files()

    def _collect_files(self):
        for root, _, files in os.walk(self.source_dir):
            for file in files:
                if file.endswith(".py") and not file.startswith("_"):
                    self.all_files.append(os.path.join(root, file))

    def check_core_metrics_coverage(self) -> Dict[str, Any]:
        print("=== 检查核心业务埋点覆盖率 ===")
        metrics_file = os.path.join(self.source_dir, "monitoring", "business_metrics.py")
        if not os.path.exists(metrics_file):
            print(f"❌ 业务指标文件不存在: {metrics_file}")
            return {"status": "error", "message": "业务指标文件不存在"}

        with open(metrics_file, "r", encoding="utf-8") as f:
            content = f.read()

        coverage_results = {}
        total_core = len(CORE_METRICS)
        covered = 0
        missing = []

        for metric in CORE_METRICS:
            if metric in content:
                coverage_results[metric] = {"covered": True, "status": "pass"}
                covered += 1
                print(f"  ✅ {metric}")
            else:
                coverage_results[metric] = {"covered": False, "status": "fail"}
                missing.append(metric)
                print(f"  ❌ {metric}")

        coverage_rate = (covered / total_core) * 100
        status = "pass" if coverage_rate == 100 else "fail"

        print(f"\n  核心埋点覆盖率: {coverage_rate:.1f}% ({covered}/{total_core})")

        self.results["core_metrics_coverage"] = {
            "status": status,
            "coverage_rate": coverage_rate,
            "total_core_metrics": total_core,
            "covered_metrics": covered,
            "missing_metrics": missing,
            "details": coverage_results,
        }

        return self.results["core_metrics_coverage"]

    def check_naming_convention(self) -> Dict[str, Any]:
        print("\n=== 检查埋点命名规范 ===")
        metrics_file = os.path.join(self.source_dir, "monitoring", "business_metrics.py")
        if not os.path.exists(metrics_file):
            return {"status": "error", "message": "业务指标文件不存在"}

        with open(metrics_file, "r", encoding="utf-8") as f:
            content = f.read()

        metric_pattern = r'"yunshu_[^"]+"'
        all_metrics = re.findall(metric_pattern, content)
        metrics = [m.strip('"') for m in all_metrics]

        naming_results = {}
        valid_count = 0
        invalid_metrics = []

        for metric in metrics:
            if re.match(METRIC_NAMING_PATTERN, metric):
                naming_results[metric] = {"valid": True, "status": "pass"}
                valid_count += 1
                print(f"  ✅ {metric}")
            else:
                naming_results[metric] = {"valid": False, "status": "fail", "reason": "命名不符合规范"}
                invalid_metrics.append(metric)
                print(f"  ❌ {metric}")

        total_metrics = len(metrics)
        valid_rate = (valid_count / total_metrics) * 100 if total_metrics > 0 else 0
        status = "pass" if valid_rate == 100 else "fail"

        print(f"\n  命名规范符合率: {valid_rate:.1f}% ({valid_count}/{total_metrics})")

        self.results["naming_convention"] = {
            "status": status,
            "valid_rate": valid_rate,
            "total_metrics": total_metrics,
            "valid_metrics": valid_count,
            "invalid_metrics": invalid_metrics,
            "pattern": METRIC_NAMING_PATTERN,
            "details": naming_results,
        }

        return self.results["naming_convention"]

    def check_new_code_metrics(self, new_files: List[str] = None) -> Dict[str, Any]:
        print("\n=== 检查新增代码埋点 ===")
        if not new_files:
            new_files = self.all_files

        metric_import_pattern = r"(from|import).*business_metrics"
        metric_call_pattern = r"(record_|update_|yunshu_)"

        new_code_results = {}
        files_with_metrics = []
        files_without_metrics = []
        files_with_incorrect_metrics = []

        for filepath in new_files:
            if not os.path.exists(filepath):
                continue

            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            has_metric_import = bool(re.search(metric_import_pattern, content))
            has_metric_call = bool(re.search(metric_call_pattern, content))
            filename = os.path.relpath(filepath, self.source_dir)

            if has_metric_import or has_metric_call:
                new_code_results[filename] = {
                    "has_metrics": True,
                    "has_import": has_metric_import,
                    "has_call": has_metric_call,
                    "status": "pass",
                }
                files_with_metrics.append(filename)
                print(f"  ✅ {filename}")
            else:
                if self._is_business_logic_file(filepath):
                    new_code_results[filename] = {
                        "has_metrics": False,
                        "has_import": has_metric_import,
                        "has_call": has_metric_call,
                        "status": "fail",
                        "reason": "业务逻辑文件缺少埋点",
                    }
                    files_without_metrics.append(filename)
                    print(f"  ❌ {filename} - 业务逻辑文件缺少埋点")
                else:
                    new_code_results[filename] = {
                        "has_metrics": False,
                        "has_import": has_metric_import,
                        "has_call": has_metric_call,
                        "status": "skip",
                        "reason": "非业务逻辑文件",
                    }
                    print(f"  ⚠️ {filename} - 非业务逻辑文件，跳过")

        total_business_files = len(files_with_metrics) + len(files_without_metrics)
        coverage_rate = (len(files_with_metrics) / total_business_files) * 100 if total_business_files > 0 else 0
        status = "pass" if coverage_rate == 100 else "fail"

        print(f"\n  新增代码埋点覆盖率: {coverage_rate:.1f}% ({len(files_with_metrics)}/{total_business_files})")

        self.results["new_code_check"] = {
            "status": status,
            "coverage_rate": coverage_rate,
            "total_business_files": total_business_files,
            "files_with_metrics": len(files_with_metrics),
            "files_without_metrics": len(files_without_metrics),
            "missing_files": files_without_metrics,
            "details": new_code_results,
        }

        return self.results["new_code_check"]

    def _is_business_logic_file(self, filepath: str) -> bool:
        business_dirs = [
            "agent/",
            "agent/core/",
            "agent/skills/",
            "agent/extensions/",
            "agent/memory/",
            "agent/planning/",
            "agent/tools/",
            "agent/monitoring/",
        ]
        relative_path = os.path.relpath(filepath)
        for business_dir in business_dirs:
            if relative_path.startswith(business_dir):
                exclude_patterns = [
                    r".*tests?/",
                    r".*__pycache__/",
                    r".*\.pyc$",
                    r".*config\.py$",
                    r".*settings\.py$",
                    r".*constants\.py$",
                    r".*utils\.py$",
                    r".*__init__\.py$",
                ]
                if not any(re.match(pattern, relative_path) for pattern in exclude_patterns):
                    return True
        return False

    def check_metric_type_consistency(self) -> Dict[str, Any]:
        print("\n=== 检查指标类型一致性 ===")
        metrics_file = os.path.join(self.source_dir, "monitoring", "business_metrics.py")
        if not os.path.exists(metrics_file):
            return {"status": "error", "message": "业务指标文件不存在"}

        with open(metrics_file, "r", encoding="utf-8") as f:
            content = f.read()

        metric_def_pattern = r'"(yunshu_[^"]+)":\s*BusinessMetricDefinition\(\s*name="[^"]+",\s*description="[^"]+",\s*metric_type="([^"]+)"'
        matches = re.findall(metric_def_pattern, content, re.MULTILINE | re.DOTALL)

        consistency_results = {}
        consistent_count = 0
        inconsistent_metrics = []

        for metric_name, metric_type in matches:
            patterns = METRIC_TYPE_PATTERNS.get(metric_type, [])
            matched = any(re.search(p, metric_name) for p in patterns)

            if matched:
                consistency_results[metric_name] = {
                    "metric_type": metric_type,
                    "consistent": True,
                    "status": "pass",
                }
                consistent_count += 1
                print(f"  ✅ {metric_name} - {metric_type}")
            else:
                consistency_results[metric_name] = {
                    "metric_type": metric_type,
                    "consistent": False,
                    "status": "fail",
                    "reason": f"指标名称后缀与类型 {metric_type} 不匹配",
                }
                inconsistent_metrics.append(metric_name)
                print(f"  ❌ {metric_name} - {metric_type}")

        total_metrics = len(matches)
        consistency_rate = (consistent_count / total_metrics) * 100 if total_metrics > 0 else 0
        status = "pass" if consistency_rate == 100 else "fail"

        print(f"\n  指标类型一致率: {consistency_rate:.1f}% ({consistent_count}/{total_metrics})")

        self.results["metric_type_consistency"] = {
            "status": status,
            "consistency_rate": consistency_rate,
            "total_metrics": total_metrics,
            "consistent_count": consistent_count,
            "inconsistent_metrics": inconsistent_metrics,
            "details": consistency_results,
        }

        return self.results["metric_type_consistency"]

    def generate_summary(self) -> Dict[str, Any]:
        print("\n=== 生成检查总结 ===")
        checks = [
            ("core_metrics_coverage", "核心埋点覆盖率"),
            ("naming_convention", "命名规范"),
            ("new_code_check", "新增代码埋点"),
            ("metric_type_consistency", "指标类型一致性"),
        ]

        passed_checks = 0
        failed_checks = 0

        for check_key, check_name in checks:
            if check_key in self.results:
                status = self.results[check_key].get("status", "unknown")
                if status == "pass":
                    passed_checks += 1
                elif status == "fail":
                    failed_checks += 1

        overall_status = "pass" if failed_checks == 0 else "fail"

        self.results["summary"] = {
            "overall_status": overall_status,
            "passed_checks": passed_checks,
            "failed_checks": failed_checks,
            "total_checks": len(checks),
            "checks": checks,
        }

        print(f"  总检查项: {len(checks)}")
        print(f"  通过: {passed_checks}")
        print(f"  失败: {failed_checks}")
        print(f"  总体状态: {'✅ 通过' if overall_status == 'pass' else '❌ 失败'}")

        return self.results["summary"]

    def run_all_checks(self) -> Dict[str, Any]:
        print("=" * 60)
        print("  业务埋点检查脚本")
        print("=" * 60)

        self.check_core_metrics_coverage()
        self.check_naming_convention()
        self.check_new_code_metrics()
        self.check_metric_type_consistency()
        self.generate_summary()

        print("\n" + "=" * 60)

        return self.results

    def save_results(self, output_file: str):
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        print(f"\n检查结果已保存到: {output_file}")

def main():
    parser = argparse.ArgumentParser(description="业务埋点检查脚本")
    parser.add_argument(
        "--source-dir",
        default="agent",
        help="源代码目录",
    )
    parser.add_argument(
        "--output",
        default="test_reports/business_metrics_report.json",
        help="输出报告文件路径",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="检查失败时退出码为1",
    )

    args = parser.parse_args()

    checker = BusinessMetricsChecker(source_dir=args.source_dir)
    results = checker.run_all_checks()

    checker.save_results(args.output)

    if args.fail_on_error and results["summary"]["overall_status"] == "fail":
        sys.exit(1)

if __name__ == "__main__":
    main()