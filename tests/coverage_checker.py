"""
云枢系统测试覆盖率统计模块

提供：
- 测试覆盖率目标定义
- 覆盖率检查工具
- 覆盖率报告生成
- 覆盖率趋势分析
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum

class CoverageLevel(Enum):
    """覆盖率等级"""
    EXCELLENT = "excellent"
    GOOD = "good"
    NEEDS_IMPROVEMENT = "needs_improvement"
    CRITICAL = "critical"

@dataclass
class CoverageTarget:
    """覆盖率目标"""
    module: str
    target_percent: float
    critical: bool = False

@dataclass
class CoverageResult:
    """覆盖率结果"""
    module: str
    covered_lines: int
    total_lines: int
    coverage_percent: float
    level: CoverageLevel
    meets_target: bool
    timestamp: str

class CoverageChecker:
    """覆盖率检查器"""

    # 核心模块覆盖率目标
    COVERAGE_TARGETS = {
        "agent/": CoverageTarget("agent/", 80.0, critical=True),
        "agent/memory/": CoverageTarget("agent/memory/", 85.0, critical=True),
        "agent/permission/": CoverageTarget("agent/permission/", 90.0, critical=True),
        "agent/monitoring/": CoverageTarget("agent/monitoring/", 75.0, critical=True),
        "agent/planning/": CoverageTarget("agent/planning/", 75.0, critical=False),
        "agent/cognitive/": CoverageTarget("agent/cognitive/", 70.0, critical=False),
        "agent/sensor/": CoverageTarget("agent/sensor/", 70.0, critical=False),
    }

    # 全局覆盖率目标
    GLOBAL_TARGET = 70.0

    def __init__(self, coverage_report_path: Optional[Path] = None):
        self.coverage_report_path = coverage_report_path or Path("coverage.json")
        self.results: List[CoverageResult] = []

    def parse_coverage_report(self, report_path: Path) -> Dict[str, Any]:
        """解析覆盖率报告"""
        if not report_path.exists():
            raise FileNotFoundError(f"覆盖率报告文件不存在: {report_path}")

        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 解析XML格式的coverage报告
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)

        results = {}
        for package in root.findall(".//package"):
            package_name = package.get('name', 'unknown')
            classes = package.findall(".//class")

            total_lines = 0
            covered_lines = 0

            for cls in classes:
                lines = cls.find(".//lines")
                if lines is not None:
                    total_lines += int(lines.get('covered', 0)) + int(lines.get('missed', 0))
                    covered_lines += int(lines.get('covered', 0))

            if total_lines > 0:
                coverage_percent = (covered_lines / total_lines) * 100
                results[package_name] = {
                    'total_lines': total_lines,
                    'covered_lines': covered_lines,
                    'coverage_percent': coverage_percent
                }

        return results

    def check_coverage(self, coverage_data: Dict[str, Any]) -> List[CoverageResult]:
        """检查覆盖率是否达标"""
        results = []

        for module_pattern, target in self.COVERAGE_TARGETS.items():
            # 查找匹配的模块
            matching_modules = [
                (name, data) for name, data in coverage_data.items()
                if module_pattern in name
            ]

            if matching_modules:
                # 计算所有匹配模块的综合覆盖率
                total_lines = sum(data['total_lines'] for _, data in matching_modules)
                covered_lines = sum(data['covered_lines'] for _, data in matching_modules)

                if total_lines > 0:
                    coverage_percent = (covered_lines / total_lines) * 100
                    meets_target = coverage_percent >= target.target_percent

                    level = self._determine_level(coverage_percent)

                    result = CoverageResult(
                        module=module_pattern,
                        covered_lines=covered_lines,
                        total_lines=total_lines,
                        coverage_percent=coverage_percent,
                        level=level,
                        meets_target=meets_target,
                        timestamp=datetime.now().isoformat()
                    )
                    results.append(result)

        # 计算全局覆盖率
        total_lines = sum(r.total_lines for r in results)
        covered_lines = sum(r.covered_lines for r in results)

        if total_lines > 0:
            global_percent = (covered_lines / total_lines) * 100
            global_result = CoverageResult(
                module="GLOBAL",
                covered_lines=covered_lines,
                total_lines=total_lines,
                coverage_percent=global_percent,
                level=self._determine_level(global_percent),
                meets_target=global_percent >= self.GLOBAL_TARGET,
                timestamp=datetime.now().isoformat()
            )
            results.insert(0, global_result)

        self.results = results
        return results

    def _determine_level(self, percent: float) -> CoverageLevel:
        """确定覆盖率等级"""
        if percent >= 90:
            return CoverageLevel.EXCELLENT
        elif percent >= 70:
            return CoverageLevel.GOOD
        elif percent >= 50:
            return CoverageLevel.NEEDS_IMPROVEMENT
        else:
            return CoverageLevel.CRITICAL

    def generate_report(self) -> Dict[str, Any]:
        """生成覆盖率报告"""
        if not self.results:
            return {"error": "没有覆盖率数据，请先运行覆盖率检查"}

        report = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_modules": len(self.results),
                "modules_meeting_target": sum(1 for r in self.results if r.meets_target),
                "overall_coverage": self.results[0].coverage_percent if self.results else 0,
                "status": "PASS" if all(r.meets_target for r in self.results) else "FAIL"
            },
            "targets": [
                {
                    "module": target.module,
                    "target": target.target_percent,
                    "critical": target.critical
                }
                for target in self.COVERAGE_TARGETS.values()
            ],
            "results": [asdict(r) for r in self.results],
            "recommendations": self._generate_recommendations()
        }

        return report

    def _generate_recommendations(self) -> List[str]:
        """生成改进建议"""
        recommendations = []

        for result in self.results:
            if not result.meets_target:
                gap = result.coverage_percent - self.COVERAGE_TARGETS.get(
                    result.module, CoverageTarget(result.module, self.GLOBAL_TARGET)
                ).target_percent

                recommendations.append(
                    f"模块 {result.module} 覆盖率 {result.coverage_percent:.1f}%，"
                    f"需要提升 {abs(gap):.1f}%。"
                )

                if result.level == CoverageLevel.CRITICAL:
                    recommendations.append(
                        f"⚠️ 严重: {result.module} 覆盖率过低，需要优先处理！"
                    )

        if not recommendations:
            recommendations.append("✅ 所有模块覆盖率达标，继续保持！")

        return recommendations

    def save_report(self, report_path: Path):
        """保存覆盖率报告"""
        report = self.generate_report()

        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        return report

class CoverageTrendAnalyzer:
    """覆盖率趋势分析器"""

    def __init__(self, history_dir: Path):
        self.history_dir = history_dir
        self.history_dir.mkdir(exist_ok=True, parents=True)

    def save_snapshot(self, coverage_data: Dict[str, Any]):
        """保存覆盖率快照"""
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "data": coverage_data
        }

        filename = f"coverage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.history_dir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)

    def get_trend(self, days: int = 30) -> List[Dict[str, Any]]:
        """获取覆盖率趋势"""
        snapshots = sorted(
            self.history_dir.glob("coverage_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )[:days]

        trends = []
        for snapshot_path in snapshots:
            with open(snapshot_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                trends.append(data)

        return list(reversed(trends))

def run_coverage_check():
    """运行覆盖率检查"""
    import sys

    # 查找覆盖率报告
    possible_paths = [
        Path("coverage.xml"),
        Path("tests/coverage.xml"),
        Path(".coverage"),
    ]

    report_path = None
    for path in possible_paths:
        if path.exists():
            report_path = path
            break

    if not report_path:
        print("❌ 未找到覆盖率报告文件")
        print("请先运行: pytest --cov=agent --cov-report=xml")
        sys.exit(1)

    # 执行检查
    checker = CoverageChecker(report_path)

    try:
        coverage_data = checker.parse_coverage_report(report_path)
        results = checker.check_coverage(coverage_data)

        # 输出结果
        print("\n" + "="*60)
        print("测试覆盖率检查报告")
        print("="*60)

        for result in results:
            status = "✅" if result.meets_target else "❌"
            print(f"{status} {result.module:30s} {result.coverage_percent:6.1f}% "
                  f"(目标: {CoverageChecker.COVERAGE_TARGETS.get(result.module, CoverageTarget(result.module, 70)).target_percent:.0f}%)")

        print("="*60)

        # 保存报告
        output_path = Path("test_reports/coverage_report.json")
        output_path.parent.mkdir(exist_ok=True, parents=True)
        report = checker.save_report(output_path)

        print(f"\n📊 详细报告已保存到: {output_path}")

        if report["summary"]["status"] == "FAIL":
            print("\n⚠️  覆盖率未达标，请参考报告中建议进行改进")
            sys.exit(1)
        else:
            print("\n✅ 所有模块覆盖率达标！")
            sys.exit(0)

    except Exception as e:
        print(f"❌ 覆盖率检查失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_coverage_check()
