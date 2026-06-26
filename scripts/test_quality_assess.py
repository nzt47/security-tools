#!/usr/bin/env python3
"""
云枢系统测试质量评估脚本

评估维度：
- 测试通过率（单元/集成/P0测试）
- 边界条件覆盖
- 异常处理覆盖
- 测试执行时间
- 测试重复度检测
- AI生成代码质量评分

输出结构化JSON报告，包含：
- 各维度评分
- 测试质量等级
- 改进建议
- 详细报告路径
"""

import json
import sys
import os
import argparse
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum

class QualityLevel(Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    NEEDS_IMPROVEMENT = "needs_improvement"
    POOR = "poor"

@dataclass
class TestResult:
    name: str
    status: str
    duration: float
    error: Optional[str] = None
    markers: Optional[List[str]] = None

@dataclass
class QualityDimension:
    name: str
    score: float
    max_score: float
    weight: float
    level: QualityLevel
    details: List[str]

@dataclass
class AIQualityScore:
    test_pass_rate: float
    coverage_rate: float
    boundary_coverage: float
    metrics_coverage: float
    exception_handling: float
    overall_score: float
    level: QualityLevel

class TestQualityAssessor:
    UNIT_TEST_PASS_TARGET = 95.0
    INTEGRATION_TEST_PASS_TARGET = 90.0
    P0_TEST_PASS_TARGET = 100.0
    MAX_EXECUTION_TIME_MINUTES = 30.0

    BOUNDARY_PATTERNS = [
        r'\b(None|null|empty|zero|min|max|negative|positive|boundary|edge)\b',
        r'\b(large|huge|small|tiny|max_size|min_size)\b',
        r'\b(invalid|valid|corrupt|malformed|abnormal)\b',
        r'\b(overflow|underflow|timeout|limit)\b',
    ]

    EXCEPTION_PATTERNS = [
        r'try\s*:',
        r'except\s+\w+:',
        r'assertRaises',
        r'expect.*Exception',
        r'mock.*side_effect',
    ]

    def __init__(self):
        self.test_results: List[TestResult] = []
        self.quality_dimensions: List[QualityDimension] = []
        self.ai_quality_score: Optional[AIQualityScore] = None

    def parse_pytest_json(self, json_path: Path) -> List[TestResult]:
        if not json_path.exists():
            raise FileNotFoundError(f"测试结果文件不存在: {json_path}")

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        results = []
        for test in data.get('tests', []):
            markers = test.get('markers', [])
            result = TestResult(
                name=test.get('nodeid', ''),
                status=test.get('outcome', 'unknown'),
                duration=test.get('duration', 0.0),
                error=test.get('error', {}).get('message') if test.get('error') else None,
                markers=markers
            )
            results.append(result)

        self.test_results = results
        return results

    def parse_junit_xml(self, xml_path: Path) -> List[TestResult]:
        import xml.etree.ElementTree as ET

        if not xml_path.exists():
            raise FileNotFoundError(f"测试结果文件不存在: {xml_path}")

        tree = ET.parse(xml_path)
        root = tree.getroot()

        results = []
        for testcase in root.findall(".//testcase"):
            name = testcase.get('name', '')
            classname = testcase.get('classname', '')
            duration = float(testcase.get('time', '0'))
            
            status = 'passed'
            error = None
            markers = []

            if testcase.find('failure') is not None:
                status = 'failed'
                failure = testcase.find('failure')
                error = failure.get('message', '')
            elif testcase.find('error') is not None:
                status = 'error'
                err = testcase.find('error')
                error = err.get('message', '')

            result = TestResult(
                name=f"{classname}.{name}",
                status=status,
                duration=duration,
                error=error,
                markers=markers
            )
            results.append(result)

        self.test_results = results
        return results

    def analyze_test_files(self, test_dir: Path) -> Dict[str, Any]:
        boundary_count = 0
        exception_count = 0
        total_tests = 0
        test_file_count = 0

        for test_file in test_dir.rglob('test_*.py'):
            try:
                with open(test_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 修复：test_file_count 移入 try 块内，仅计成功读取的文件
                    # 原代码在 try 外递增，文件读取失败时 test_file_count 包含失败文件，
                    # 但 boundary_count/exception_count 不包含，导致覆盖率被人为压低
                    test_file_count += 1
                    total_tests += len(re.findall(r'\bdef test_', content))
                    
                    for pattern in self.BOUNDARY_PATTERNS:
                        if re.search(pattern, content, re.IGNORECASE):
                            boundary_count += 1
                            break
                    
                    for pattern in self.EXCEPTION_PATTERNS:
                        if re.search(pattern, content, re.IGNORECASE):
                            exception_count += 1
                            break
            except Exception:
                continue

        return {
            'test_file_count': test_file_count,
            'total_tests': total_tests,
            'boundary_coverage_files': boundary_count,
            'exception_coverage_files': exception_count,
            'boundary_coverage_rate': boundary_count / test_file_count if test_file_count > 0 else 0,
            'exception_coverage_rate': exception_count / test_file_count if test_file_count > 0 else 0,
        }

    def assess_pass_rate(self) -> QualityDimension:
        if not self.test_results:
            return QualityDimension(
                name="测试通过率",
                score=0.0,
                max_score=100.0,
                weight=0.30,
                level=QualityLevel.POOR,
                details=["没有测试结果数据"]
            )

        total = len(self.test_results)
        passed = sum(1 for r in self.test_results if r.status == 'passed')
        pass_rate = (passed / total) * 100

        unit_tests = [r for r in self.test_results if 'unit' in (r.markers or [])]
        integration_tests = [r for r in self.test_results if 'integration' in (r.markers or [])]
        p0_tests = [r for r in self.test_results if 'p0' in (r.markers or [])]

        unit_pass_rate = (sum(1 for r in unit_tests if r.status == 'passed') / len(unit_tests) * 100) if unit_tests else 0
        integration_pass_rate = (sum(1 for r in integration_tests if r.status == 'passed') / len(integration_tests) * 100) if integration_tests else 0
        p0_pass_rate = (sum(1 for r in p0_tests if r.status == 'passed') / len(p0_tests) * 100) if p0_tests else 0

        details = [
            f"总测试数: {total}, 通过: {passed}, 通过率: {pass_rate:.1f}%",
            f"单元测试通过率: {unit_pass_rate:.1f}% (目标: {self.UNIT_TEST_PASS_TARGET}%)",
            f"集成测试通过率: {integration_pass_rate:.1f}% (目标: {self.INTEGRATION_TEST_PASS_TARGET}%)",
            f"P0测试通过率: {p0_pass_rate:.1f}% (目标: {self.P0_TEST_PASS_TARGET}%)",
        ]

        meets_unit = unit_pass_rate >= self.UNIT_TEST_PASS_TARGET or not unit_tests
        meets_integration = integration_pass_rate >= self.INTEGRATION_TEST_PASS_TARGET or not integration_tests
        meets_p0 = p0_pass_rate >= self.P0_TEST_PASS_TARGET or not p0_tests

        score_components = []
        if unit_tests:
            score_components.append(min(unit_pass_rate / self.UNIT_TEST_PASS_TARGET, 1.0))
        if integration_tests:
            score_components.append(min(integration_pass_rate / self.INTEGRATION_TEST_PASS_TARGET, 1.0))
        if p0_tests:
            score_components.append(min(p0_pass_rate / self.P0_TEST_PASS_TARGET, 1.0))

        if score_components:
            score = sum(score_components) / len(score_components) * 100
        else:
            score = pass_rate

        level = self._determine_level(score)

        if not meets_p0 and p0_tests:
            level = QualityLevel.POOR
            score = min(score, 50)

        return QualityDimension(
            name="测试通过率",
            score=score,
            max_score=100.0,
            weight=0.30,
            level=level,
            details=details
        )

    def assess_execution_time(self) -> QualityDimension:
        if not self.test_results:
            return QualityDimension(
                name="测试执行时间",
                score=0.0,
                max_score=100.0,
                weight=0.15,
                level=QualityLevel.POOR,
                details=["没有测试结果数据"]
            )

        total_duration = sum(r.duration for r in self.test_results)
        total_duration_minutes = total_duration / 60.0

        if total_duration_minutes <= self.MAX_EXECUTION_TIME_MINUTES:
            score = 100.0
            level = QualityLevel.EXCELLENT
        else:
            score = max(0, 100 - ((total_duration_minutes - self.MAX_EXECUTION_TIME_MINUTES) / self.MAX_EXECUTION_TIME_MINUTES * 100))
            level = self._determine_level(score)

        details = [
            f"总执行时间: {total_duration_minutes:.1f} 分钟",
            f"测试数量: {len(self.test_results)}",
            f"平均测试时间: {total_duration / len(self.test_results):.2f} 秒",
            f"超时阈值: {self.MAX_EXECUTION_TIME_MINUTES} 分钟",
        ]

        return QualityDimension(
            name="测试执行时间",
            score=score,
            max_score=100.0,
            weight=0.15,
            level=level,
            details=details
        )

    def assess_boundary_coverage(self, test_dir: Path, analysis: Optional[Dict[str, Any]] = None) -> QualityDimension:
        # 优化：复用传入的 analysis 结果，避免与 assess_exception_handling 重复扫描+读取测试文件
        if analysis is None:
            analysis = self.analyze_test_files(test_dir)
        coverage_rate = analysis['boundary_coverage_rate'] * 100

        score = coverage_rate
        level = self._determine_level(score)

        details = [
            f"边界条件覆盖文件数: {analysis['boundary_coverage_files']}/{analysis['test_file_count']}",
            f"边界条件覆盖率: {coverage_rate:.1f}%",
            f"总测试文件数: {analysis['test_file_count']}",
        ]

        return QualityDimension(
            name="边界条件覆盖",
            score=score,
            max_score=100.0,
            weight=0.15,
            level=level,
            details=details
        )

    def assess_exception_handling(self, test_dir: Path, analysis: Optional[Dict[str, Any]] = None) -> QualityDimension:
        # 优化：复用传入的 analysis 结果，避免与 assess_boundary_coverage 重复扫描+读取测试文件
        if analysis is None:
            analysis = self.analyze_test_files(test_dir)
        coverage_rate = analysis['exception_coverage_rate'] * 100

        score = coverage_rate
        level = self._determine_level(score)

        details = [
            f"异常处理覆盖文件数: {analysis['exception_coverage_files']}/{analysis['test_file_count']}",
            f"异常处理覆盖率: {coverage_rate:.1f}%",
            f"总测试文件数: {analysis['test_file_count']}",
        ]

        return QualityDimension(
            name="异常处理覆盖",
            score=score,
            max_score=100.0,
            weight=0.15,
            level=level,
            details=details
        )

    def assess_test_redundancy(self) -> QualityDimension:
        if not self.test_results:
            return QualityDimension(
                name="测试重复度",
                score=0.0,
                max_score=100.0,
                weight=0.10,
                level=QualityLevel.POOR,
                details=["没有测试结果数据"]
            )

        test_names = [r.name for r in self.test_results]
        unique_names = set(test_names)
        redundancy = 1 - (len(unique_names) / len(test_names)) if test_names else 0
        
        score = max(0, 100 - redundancy * 200)
        level = self._determine_level(score)

        details = [
            f"总测试数: {len(test_names)}",
            f"唯一测试数: {len(unique_names)}",
            f"重复度: {redundancy * 100:.1f}%",
        ]

        return QualityDimension(
            name="测试重复度",
            score=score,
            max_score=100.0,
            weight=0.10,
            level=level,
            details=details
        )

    def assess_ai_code_quality(self, coverage_rate: float = 0.0) -> AIQualityScore:
        pass_rate_dim = next((d for d in self.quality_dimensions if d.name == "测试通过率"), None)
        boundary_dim = next((d for d in self.quality_dimensions if d.name == "边界条件覆盖"), None)
        exception_dim = next((d for d in self.quality_dimensions if d.name == "异常处理覆盖"), None)

        test_pass_rate = pass_rate_dim.score if pass_rate_dim else 0.0
        coverage_rate_actual = coverage_rate
        boundary_coverage = boundary_dim.score if boundary_dim else 0.0
        exception_handling = exception_dim.score if exception_dim else 0.0

        metrics_coverage = self._check_metrics_coverage()

        weights = {
            'test_pass_rate': 0.30,
            'coverage_rate': 0.25,
            'boundary_coverage': 0.15,
            'metrics_coverage': 0.15,
            'exception_handling': 0.15,
        }

        overall_score = (
            test_pass_rate * weights['test_pass_rate'] +
            coverage_rate_actual * weights['coverage_rate'] +
            boundary_coverage * weights['boundary_coverage'] +
            metrics_coverage * weights['metrics_coverage'] +
            exception_handling * weights['exception_handling']
        )

        level = self._determine_level(overall_score)

        return AIQualityScore(
            test_pass_rate=test_pass_rate,
            coverage_rate=coverage_rate_actual,
            boundary_coverage=boundary_coverage,
            metrics_coverage=metrics_coverage,
            exception_handling=exception_handling,
            overall_score=overall_score,
            level=level
        )

    def _check_metrics_coverage(self) -> float:
        business_metrics_path = Path('agent/monitoring/business_metrics.py')
        if not business_metrics_path.exists():
            return 0.0

        try:
            with open(business_metrics_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            core_metrics = [
                'yunshu_interaction_total',
                'yunshu_task_completion_rate',
                'yunshu_memory_search_hit_rate',
                'yunshu_tool_call_total',
                'yunshu_circuit_breaker_trigger_total',
            ]
            
            found_count = sum(1 for metric in core_metrics if metric in content)
            return (found_count / len(core_metrics)) * 100
        except Exception:
            return 0.0

    def _determine_level(self, score: float) -> QualityLevel:
        if score >= 90:
            return QualityLevel.EXCELLENT
        elif score >= 75:
            return QualityLevel.GOOD
        elif score >= 60:
            return QualityLevel.NEEDS_IMPROVEMENT
        else:
            return QualityLevel.POOR

    def generate_report(self, coverage_rate: float = 0.0) -> Dict[str, Any]:
        # 优化：预先调用一次 analyze_test_files，缓存分析结果，
        # 供 assess_boundary_coverage 和 assess_exception_handling 共享，
        # 避免同一批测试文件被扫描+读取两次。
        tests_analysis = self.analyze_test_files(Path('tests'))

        dimensions = [
            self.assess_pass_rate(),
            self.assess_execution_time(),
            self.assess_boundary_coverage(Path('tests'), tests_analysis),
            self.assess_exception_handling(Path('tests'), tests_analysis),
            self.assess_test_redundancy(),
        ]
        self.quality_dimensions = dimensions

        weighted_score = sum(d.score * d.weight for d in dimensions)
        overall_level = self._determine_level(weighted_score)

        self.ai_quality_score = self.assess_ai_code_quality(coverage_rate)

        recommendations = self._generate_recommendations()

        report = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "overall_score": weighted_score,
                "overall_level": overall_level.value,
                "ai_quality_score": self.ai_quality_score.overall_score,
                "ai_quality_level": self.ai_quality_score.level.value,
                "total_tests": len(self.test_results),
                "passed_tests": sum(1 for r in self.test_results if r.status == 'passed'),
                "failed_tests": sum(1 for r in self.test_results if r.status == 'failed'),
                "error_tests": sum(1 for r in self.test_results if r.status == 'error'),
            },
            "dimensions": [asdict(d) for d in dimensions],
            "ai_quality_score": asdict(self.ai_quality_score),
            "recommendations": recommendations,
        }

        return report

    def _generate_recommendations(self) -> List[str]:
        recommendations = []

        for dim in self.quality_dimensions:
            if dim.level in [QualityLevel.POOR, QualityLevel.NEEDS_IMPROVEMENT]:
                if dim.name == "测试通过率":
                    recommendations.append("⚠️ 测试通过率较低，请检查失败的测试用例并修复。")
                elif dim.name == "测试执行时间":
                    recommendations.append("⚠️ 测试执行时间过长，请优化慢测试或考虑并行执行。")
                elif dim.name == "边界条件覆盖":
                    recommendations.append("⚠️ 边界条件覆盖不足，请为核心模块补充边界测试用例。")
                elif dim.name == "异常处理覆盖":
                    recommendations.append("⚠️ 异常处理覆盖不足，请补充异常场景测试用例。")
                elif dim.name == "测试重复度":
                    recommendations.append("⚠️ 存在测试重复，请清理重复的测试用例。")

        if self.ai_quality_score and self.ai_quality_score.level in [QualityLevel.POOR, QualityLevel.NEEDS_IMPROVEMENT]:
            recommendations.append(
                f"⚠️ AI生成代码质量评分 {self.ai_quality_score.overall_score:.1f} 较低，"
                f"建议从测试覆盖率、边界条件、异常处理等方面进行改进。"
            )

        if not recommendations:
            recommendations.append("✅ 测试质量优秀，继续保持！")

        return recommendations

    def print_summary(self, report: Dict[str, Any]):
        print("\n" + "="*70)
        print("云枢系统测试质量评估报告")
        print("="*70)
        print(f"\n生成时间: {report['generated_at']}")
        
        level_text = {
            'excellent': '🌟 优秀',
            'good': '👍 良好',
            'needs_improvement': '⚠️ 需要改进',
            'poor': '❌ 较差',
        }
        
        print(f"整体质量等级: {level_text.get(report['summary']['overall_level'], '未知')}")
        print(f"整体质量评分: {report['summary']['overall_score']:.1f}")
        print(f"AI代码质量评分: {report['summary']['ai_quality_score']:.1f}")

        print("\n--- 测试执行概况 ---")
        print(f"  总测试数: {report['summary']['total_tests']}")
        print(f"  通过: {report['summary']['passed_tests']}")
        print(f"  失败: {report['summary']['failed_tests']}")
        print(f"  错误: {report['summary']['error_tests']}")

        print("\n--- 各维度评分 ---")
        for dim in report['dimensions']:
            print(f"\n  {dim['name']}:")
            print(f"    评分: {dim['score']:.1f}/{dim['max_score']}")
            print(f"    权重: {dim['weight']*100:.0f}%")
            print(f"    等级: {level_text.get(dim['level'], '未知')}")
            for detail in dim['details']:
                print(f"    - {detail}")

        print("\n--- AI代码质量评分详情 ---")
        ai_score = report['ai_quality_score']
        print(f"  测试通过率: {ai_score['test_pass_rate']:.1f}%")
        print(f"  代码覆盖率: {ai_score['coverage_rate']:.1f}%")
        print(f"  边界条件覆盖: {ai_score['boundary_coverage']:.1f}%")
        print(f"  埋点覆盖率: {ai_score['metrics_coverage']:.1f}%")
        print(f"  异常处理覆盖: {ai_score['exception_handling']:.1f}%")
        print(f"  综合评分: {ai_score['overall_score']:.1f}")

        print("\n--- 改进建议 ---")
        for rec in report['recommendations']:
            print(f"  {rec}")

        print("\n" + "="*70)

def main():
    parser = argparse.ArgumentParser(description='云枢系统测试质量评估')
    parser.add_argument('--test-results', type=str, default='test_results.json',
                        help='测试结果文件路径 (JSON或XML)')
    parser.add_argument('--coverage-rate', type=float, default=0.0,
                        help='代码覆盖率百分比')
    parser.add_argument('--output', type=str, default='test_reports/test_quality_report.json',
                        help='输出报告路径')
    
    args = parser.parse_args()

    try:
        assessor = TestQualityAssessor()

        results_path = Path(args.test_results)
        if results_path.suffix == '.json':
            assessor.parse_pytest_json(results_path)
        elif results_path.suffix == '.xml':
            assessor.parse_junit_xml(results_path)
        else:
            raise ValueError(f"不支持的文件格式: {results_path.suffix}")

        report = assessor.generate_report(args.coverage_rate)
        assessor.print_summary(report)

        output_path = Path(args.output)
        output_path.parent.mkdir(exist_ok=True, parents=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"\n📊 详细报告已保存到: {output_path}")

        level = report['summary']['overall_level']
        if level in ['poor']:
            print("\n❌ 测试质量评估未通过！")
            sys.exit(1)
        else:
            print("\n✅ 测试质量评估通过！")
            sys.exit(0)

    except Exception as e:
        print(f"\n❌ 测试质量评估失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()