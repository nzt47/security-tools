#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云枢系统质量报告生成脚本

支持生成多种报告类型：
- 每日测试质量简报 (daily)
- 每周缺陷分析报告 (weekly)
- 每月质量趋势报告 (monthly)
- 发布质量评估报告 (release)

输出格式：
- JSON 结构化报告
- Markdown 可读报告
"""

import json
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum

if sys.platform == 'win32':
    os.system('chcp 65001 >nul 2>&1')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

class ReportType(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    RELEASE = "release"

@dataclass
class ReportSummary:
    report_type: str
    generated_at: str
    period_start: str
    period_end: str
    overall_status: str
    quality_score: float

@dataclass
class DefectRecord:
    id: str
    title: str
    type: str
    severity: str
    status: str
    detected_at: str
    root_cause: Optional[str]
    test_missing: bool
    fixed_at: Optional[str]

@dataclass
class CoverageTrend:
    date: str
    global_coverage: float
    core_modules_coverage: float
    security_modules_coverage: float

@dataclass
class TestPerformance:
    total_tests: int
    passed_tests: int
    failed_tests: int
    pass_rate: float
    execution_time_minutes: float

class QualityReportGenerator:
    REPORTS_DIR = Path("test_reports")
    HISTORY_DIR = Path("test_reports/history")

    def __init__(self, report_type: ReportType):
        self.report_type = report_type
        self.period_start, self.period_end = self._calculate_period()
        self.report: Dict[str, Any] = {}

    def _calculate_period(self):
        now = datetime.now()
        
        if self.report_type == ReportType.DAILY:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif self.report_type == ReportType.WEEKLY:
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
        elif self.report_type == ReportType.MONTHLY:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = (start + timedelta(days=32)).replace(day=1)
        else:
            start = now - timedelta(days=7)
            end = now
        
        return start.isoformat(), end.isoformat()

    def load_coverage_report(self) -> Optional[Dict[str, Any]]:
        coverage_file = self.REPORTS_DIR / "coverage_tier_report.json"
        if coverage_file.exists():
            with open(coverage_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def load_quality_report(self) -> Optional[Dict[str, Any]]:
        quality_file = self.REPORTS_DIR / "test_quality_report.json"
        if quality_file.exists():
            with open(quality_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def load_test_results(self) -> Optional[Dict[str, Any]]:
        results_file = self.REPORTS_DIR / "test_results.json"
        if results_file.exists():
            with open(results_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def load_security_report(self) -> Optional[Dict[str, Any]]:
        security_file = self.REPORTS_DIR / "bandit_report.json"
        if security_file.exists():
            with open(security_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def load_business_metrics_report(self) -> Optional[Dict[str, Any]]:
        metrics_file = self.REPORTS_DIR / "business_metrics_report.json"
        if metrics_file.exists():
            with open(metrics_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def get_coverage_trend(self) -> List[CoverageTrend]:
        trends = []
        
        if not self.HISTORY_DIR.exists():
            return trends

        history_files = sorted(
            self.HISTORY_DIR.glob("coverage_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )[:30]

        for filepath in history_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                date_str = filepath.stem.replace("coverage_", "")
                trends.append(CoverageTrend(
                    date=date_str,
                    global_coverage=data.get('summary', {}).get('overall_coverage', 0),
                    core_modules_coverage=data.get('results', [{}])[0].get('coverage_percent', 0) if data.get('results') else 0,
                    security_modules_coverage=0.0
                ))
            except Exception:
                continue

        return list(reversed(trends))

    def get_defect_records(self) -> List[DefectRecord]:
        defects_file = self.REPORTS_DIR / "defects.json"
        defects = []
        
        if defects_file.exists():
            try:
                with open(defects_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for defect in data.get('defects', []):
                        defects.append(DefectRecord(
                            id=defect.get('id', ''),
                            title=defect.get('title', ''),
                            type=defect.get('type', ''),
                            severity=defect.get('severity', ''),
                            status=defect.get('status', ''),
                            detected_at=defect.get('detected_at', ''),
                            root_cause=defect.get('root_cause'),
                            test_missing=defect.get('test_missing', False),
                            fixed_at=defect.get('fixed_at'),
                        ))
            except Exception:
                pass

        return defects

    def calculate_test_performance(self) -> TestPerformance:
        quality_report = self.load_quality_report()
        
        if quality_report:
            summary = quality_report.get('summary', {})
            return TestPerformance(
                total_tests=summary.get('total_tests', 0),
                passed_tests=summary.get('passed_tests', 0),
                failed_tests=summary.get('failed_tests', 0),
                pass_rate=summary.get('ai_quality_score', {}).get('test_pass_rate', 0),
                execution_time_minutes=0.0
            )
        
        test_results = self.load_test_results()
        if test_results:
            tests = test_results.get('tests', [])
            passed = sum(1 for t in tests if t.get('outcome') == 'passed')
            failed = sum(1 for t in tests if t.get('outcome') == 'failed')
            total = len(tests)
            duration = sum(t.get('duration', 0) for t in tests)
            
            return TestPerformance(
                total_tests=total,
                passed_tests=passed,
                failed_tests=failed,
                pass_rate=(passed / total * 100) if total > 0 else 0,
                execution_time_minutes=duration / 60.0
            )
        
        return TestPerformance(0, 0, 0, 0.0, 0.0)

    def calculate_defect_escape_rate(self, defects: List[DefectRecord]) -> float:
        total_defects = len(defects)
        escaped_defects = sum(1 for d in defects if d.test_missing)
        
        if total_defects == 0:
            return 0.0
        
        return (escaped_defects / total_defects) * 100

    def generate_daily_report(self) -> Dict[str, Any]:
        coverage = self.load_coverage_report()
        quality = self.load_quality_report()
        security = self.load_security_report()
        business_metrics = self.load_business_metrics_report()
        performance = self.calculate_test_performance()
        defects = self.get_defect_records()
        
        coverage_status = coverage.get('summary', {}).get('overall_status', 'UNKNOWN') if coverage else 'UNKNOWN'
        quality_score = quality.get('summary', {}).get('overall_score', 0) if quality else 0
        metrics_status = business_metrics.get('summary', {}).get('overall_status', 'UNKNOWN') if business_metrics else 'UNKNOWN'
        
        all_pass = (
            coverage_status == "PASS" and 
            quality_score >= 75 and 
            metrics_status == "pass"
        )
        overall_status = "PASS" if all_pass else "FAIL"

        report = {
            "report_type": "daily",
            "generated_at": datetime.now().isoformat(),
            "period": {
                "start": self.period_start,
                "end": self.period_end,
            },
            "summary": {
                "overall_status": overall_status,
                "quality_score": quality_score,
                "coverage_status": coverage_status,
                "metrics_status": metrics_status,
            },
            "test_performance": asdict(performance),
            "coverage": coverage.get('summary', {}) if coverage else {},
            "security": {
                "high_vulnerabilities": len(security.get('results', [])) if security else 0,
            },
            "business_metrics": business_metrics.get('summary', {}) if business_metrics else {},
            "defects": {
                "total_open": sum(1 for d in defects if d.status == 'open'),
                "total_fixed": sum(1 for d in defects if d.status == 'fixed'),
                "escape_rate": self.calculate_defect_escape_rate(defects),
            },
            "recommendations": self._generate_daily_recommendations(coverage, quality, defects, business_metrics),
        }

        return report

    def generate_weekly_report(self) -> Dict[str, Any]:
        coverage_trend = self.get_coverage_trend()
        defects = self.get_defect_records()
        quality = self.load_quality_report()
        
        escaped_defects = [d for d in defects if d.test_missing]
        defect_types = {}
        for d in defects:
            defect_types[d.type] = defect_types.get(d.type, 0) + 1

        report = {
            "report_type": "weekly",
            "generated_at": datetime.now().isoformat(),
            "period": {
                "start": self.period_start,
                "end": self.period_end,
            },
            "summary": {
                "total_defects": len(defects),
                "escaped_defects": len(escaped_defects),
                "escape_rate": self.calculate_defect_escape_rate(defects),
                "defect_types": defect_types,
            },
            "coverage_trend": [asdict(t) for t in coverage_trend],
            "defect_analysis": {
                "by_severity": self._analyze_defects_by_severity(defects),
                "by_type": self._analyze_defects_by_type(defects),
                "root_cause_summary": self._summarize_root_causes(defects),
            },
            "test_gap_analysis": {
                "missing_tests_count": len(escaped_defects),
                "suggested_test_cases": self._generate_test_suggestions(escaped_defects),
            },
            "recommendations": self._generate_weekly_recommendations(defects, coverage_trend),
        }

        return report

    def generate_monthly_report(self) -> Dict[str, Any]:
        coverage_trend = self.get_coverage_trend()
        defects = self.get_defect_records()
        quality = self.load_quality_report()

        coverage_improvement = 0.0
        if len(coverage_trend) >= 2:
            first = coverage_trend[0].global_coverage
            last = coverage_trend[-1].global_coverage
            coverage_improvement = last - first

        monthly_defects = [d for d in defects if self.period_start <= d.detected_at <= self.period_end]
        monthly_escaped = [d for d in monthly_defects if d.test_missing]

        report = {
            "report_type": "monthly",
            "generated_at": datetime.now().isoformat(),
            "period": {
                "start": self.period_start,
                "end": self.period_end,
            },
            "summary": {
                "coverage_improvement": coverage_improvement,
                "total_defects_month": len(monthly_defects),
                "escaped_defects_month": len(monthly_escaped),
                "escape_rate_month": self.calculate_defect_escape_rate(monthly_defects),
                "quality_score_trend": self._get_quality_score_trend(),
            },
            "coverage_trend": [asdict(t) for t in coverage_trend],
            "quality_metrics": {
                "test_pass_rate": quality.get('summary', {}).get('ai_quality_score', {}).get('test_pass_rate', 0) if quality else 0,
                "boundary_coverage": quality.get('summary', {}).get('ai_quality_score', {}).get('boundary_coverage', 0) if quality else 0,
                "exception_coverage": quality.get('summary', {}).get('ai_quality_score', {}).get('exception_handling', 0) if quality else 0,
                "metrics_coverage": quality.get('summary', {}).get('ai_quality_score', {}).get('metrics_coverage', 0) if quality else 0,
            },
            "goals": {
                "coverage_goal": 70.0,
                "current_coverage": coverage_trend[-1].global_coverage if coverage_trend else 0,
                "gap_to_goal": 70.0 - (coverage_trend[-1].global_coverage if coverage_trend else 0),
            },
            "recommendations": self._generate_monthly_recommendations(coverage_trend, coverage_improvement),
        }

        return report

    def generate_release_report(self, version: str) -> Dict[str, Any]:
        coverage = self.load_coverage_report()
        quality = self.load_quality_report()
        security = self.load_security_report()
        performance = self.calculate_test_performance()
        defects = self.get_defect_records()
        
        security_issues = security.get('results', []) if security else []
        high_severity = [r for r in security_issues if r.get('issue_severity') == 'HIGH']
        
        critical_defects = [d for d in defects if d.severity == 'critical' and d.status == 'open']
        
        quality_score = quality.get('summary', {}).get('overall_score', 0) if quality else 0
        coverage_rate = coverage.get('summary', {}).get('overall_coverage', 0) if coverage else 0
        
        release_ready = (
            len(high_severity) == 0 and
            len(critical_defects) == 0 and
            quality_score >= 75 and
            coverage_rate >= 55
        )

        report = {
            "report_type": "release",
            "version": version,
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "release_ready": release_ready,
                "quality_score": quality_score,
                "coverage_rate": coverage_rate,
            },
            "quality_gates": {
                "unit_test_pass_rate": {
                    "value": performance.pass_rate,
                    "threshold": 95.0,
                    "passed": performance.pass_rate >= 95.0,
                },
                "integration_test_pass_rate": {
                    "value": performance.pass_rate,
                    "threshold": 90.0,
                    "passed": performance.pass_rate >= 90.0,
                },
                "security_high_vulnerabilities": {
                    "value": len(high_severity),
                    "threshold": 0,
                    "passed": len(high_severity) == 0,
                },
                "critical_defects_open": {
                    "value": len(critical_defects),
                    "threshold": 0,
                    "passed": len(critical_defects) == 0,
                },
                "coverage_rate": {
                    "value": coverage_rate,
                    "threshold": 55.0,
                    "passed": coverage_rate >= 55.0,
                },
            },
            "test_performance": asdict(performance),
            "security_summary": {
                "total_issues": len(security_issues),
                "high_severity": len(high_severity),
            },
            "defect_summary": {
                "total_open": sum(1 for d in defects if d.status == 'open'),
                "critical_open": len(critical_defects),
            },
            "recommendations": self._generate_release_recommendations(release_ready, coverage, quality, security),
        }

        return report

    def _generate_daily_recommendations(self, coverage, quality, defects, business_metrics=None) -> List[str]:
        recommendations = []
        
        if coverage and coverage.get('summary', {}).get('overall_status') == 'FAIL':
            recommendations.append("⚠️ 覆盖率检查未通过，请补充测试用例。")
        
        if quality and quality.get('summary', {}).get('overall_level') in ['poor', 'needs_improvement']:
            recommendations.append("⚠️ 测试质量需要改进，请参考详细报告。")
        
        if business_metrics and business_metrics.get('summary', {}).get('overall_status') == 'fail':
            recommendations.append("⚠️ 业务埋点检查未通过，请检查埋点命名规范和覆盖率。")
            metrics_summary = business_metrics.get('summary', {})
            failed = metrics_summary.get('failed_checks', 0)
            if failed > 0:
                recommendations.append(f"   - {failed} 项检查未通过")
        
        open_critical = [d for d in defects if d.severity == 'critical' and d.status == 'open']
        if open_critical:
            recommendations.append(f"⚠️ 存在 {len(open_critical)} 个严重缺陷未修复。")
        
        if not recommendations:
            recommendations.append("✅ 今日测试质量良好，继续保持！")
        
        return recommendations

    def _generate_weekly_recommendations(self, defects, coverage_trend) -> List[str]:
        recommendations = []
        
        escaped = [d for d in defects if d.test_missing]
        if escaped:
            recommendations.append(f"⚠️ 本周有 {len(escaped)} 个缺陷是由于测试遗漏导致的，建议补充测试用例。")
        
        if len(coverage_trend) >= 2 and coverage_trend[-1].global_coverage < coverage_trend[0].global_coverage:
            recommendations.append("⚠️ 覆盖率出现下降趋势，需要关注。")
        
        return recommendations

    def _generate_monthly_recommendations(self, coverage_trend, improvement) -> List[str]:
        recommendations = []
        
        if improvement < 0:
            recommendations.append("⚠️ 本月覆盖率下降，需要分析原因并采取改进措施。")
        elif improvement > 0:
            recommendations.append(f"✅ 本月覆盖率提升了 {improvement:.1f}%，继续保持！")
        
        if coverage_trend and coverage_trend[-1].global_coverage < 70:
            remaining = 70 - coverage_trend[-1].global_coverage
            recommendations.append(f"⚠️ 距离覆盖率目标还有 {remaining:.1f}% 的差距，需要加速测试补充。")
        
        return recommendations

    def _generate_release_recommendations(self, ready, coverage, quality, security) -> List[str]:
        recommendations = []
        
        if not ready:
            recommendations.append("❌ 当前版本不满足发布条件，请先修复以下问题：")
            
            if security:
                high = [r for r in security.get('results', []) if r.get('issue_severity') == 'HIGH']
                if high:
                    recommendations.append(f"  - 修复 {len(high)} 个高危安全漏洞")
            
            if coverage and coverage.get('summary', {}).get('overall_status') == 'FAIL':
                recommendations.append("  - 提升代码覆盖率至达标水平")
            
            if quality and quality.get('summary', {}).get('overall_level') in ['poor']:
                recommendations.append("  - 提升测试质量")
        else:
            recommendations.append("✅ 当前版本满足发布条件！")
        
        return recommendations

    def _analyze_defects_by_severity(self, defects) -> Dict[str, int]:
        result = {}
        for d in defects:
            result[d.severity] = result.get(d.severity, 0) + 1
        return result

    def _analyze_defects_by_type(self, defects) -> Dict[str, int]:
        result = {}
        for d in defects:
            result[d.type] = result.get(d.type, 0) + 1
        return result

    def _summarize_root_causes(self, defects) -> List[str]:
        root_causes = {}
        for d in defects:
            if d.root_cause:
                root_causes[d.root_cause] = root_causes.get(d.root_cause, 0) + 1
        
        return [f"{cause} ({count}次)" for cause, count in sorted(root_causes.items(), key=lambda x: -x[1])]

    def _generate_test_suggestions(self, escaped_defects) -> List[str]:
        suggestions = []
        for d in escaped_defects:
            suggestions.append(f"为 '{d.title}' 添加测试用例，覆盖场景: {d.root_cause or '未知'}")
        return suggestions

    def _get_quality_score_trend(self) -> List[float]:
        return []

    def generate(self, version: str = "") -> Dict[str, Any]:
        if self.report_type == ReportType.DAILY:
            self.report = self.generate_daily_report()
        elif self.report_type == ReportType.WEEKLY:
            self.report = self.generate_weekly_report()
        elif self.report_type == ReportType.MONTHLY:
            self.report = self.generate_monthly_report()
        elif self.report_type == ReportType.RELEASE:
            self.report = self.generate_release_report(version)
        
        return self.report

    def save_report(self, output_path: Path):
        output_path.parent.mkdir(exist_ok=True, parents=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.report, f, indent=2, ensure_ascii=False)
        
        md_path = output_path.with_suffix('.md')
        self._generate_markdown(md_path)

    def _generate_markdown(self, md_path: Path):
        report = self.report
        content = []
        
        content.append(f"# 云枢系统质量报告")
        content.append(f"**报告类型**: {report.get('report_type', 'unknown')}")
        content.append(f"**生成时间**: {report.get('generated_at', '')}")
        
        period = report.get('period', {})
        content.append(f"**时间范围**: {period.get('start', '')} ~ {period.get('end', '')}")
        
        content.append("")
        
        if report.get('version'):
            content.append(f"**版本**: {report['version']}")
            content.append("")
        
        summary = report.get('summary', {})
        content.append("## 摘要")
        content.append(f"- 整体状态: {'✅ 通过' if summary.get('overall_status') == 'PASS' else '❌ 失败'}")
        if 'quality_score' in summary:
            content.append(f"- 质量评分: {summary['quality_score']:.1f}")
        if 'escape_rate' in summary:
            content.append(f"- 缺陷逃逸率: {summary['escape_rate']:.1f}%")
        
        content.append("")
        
        if 'test_performance' in report:
            tp = report['test_performance']
            content.append("## 测试性能")
            content.append(f"- 总测试数: {tp.get('total_tests', 0)}")
            content.append(f"- 通过: {tp.get('passed_tests', 0)}")
            content.append(f"- 失败: {tp.get('failed_tests', 0)}")
            content.append(f"- 通过率: {tp.get('pass_rate', 0):.1f}%")
        
        content.append("")
        
        if 'business_metrics' in report and report['business_metrics']:
            bm = report['business_metrics']
            content.append("## 业务埋点检查")
            content.append(f"- 总体状态: {'✅ 通过' if bm.get('overall_status') == 'pass' else '❌ 失败'}")
            content.append(f"- 通过检查项: {bm.get('passed_checks', 0)}/{bm.get('total_checks', 0)}")
            content.append(f"- 失败检查项: {bm.get('failed_checks', 0)}")
        
        content.append("")
        
        if 'defects' in report and report['defects']:
            df = report['defects']
            content.append("## 缺陷统计")
            content.append(f"- 未修复缺陷: {df.get('total_open', 0)}")
            content.append(f"- 已修复缺陷: {df.get('total_fixed', 0)}")
            content.append(f"- 缺陷逃逸率: {df.get('escape_rate', 0):.1f}%")
        
        content.append("")
        
        if 'recommendations' in report:
            content.append("## 改进建议")
            for rec in report['recommendations']:
                content.append(f"- {rec}")
        
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content))

    def print_summary(self):
        report = self.report
        
        print("\n" + "="*70)
        report_type_text = {
            'daily': '每日测试质量简报',
            'weekly': '每周缺陷分析报告',
            'monthly': '每月质量趋势报告',
            'release': '发布质量评估报告',
        }
        print(f"云枢系统{report_type_text.get(report.get('report_type'), '质量报告')}")
        print("="*70)
        
        print(f"\n生成时间: {report.get('generated_at')}")
        
        period = report.get('period', {})
        print(f"时间范围: {period.get('start', '')} ~ {period.get('end', '')}")
        
        if report.get('version'):
            print(f"版本: {report['version']}")
        
        summary = report.get('summary', {})
        print(f"\n整体状态: {'✅ 通过' if summary.get('overall_status') == 'PASS' else '❌ 失败'}")
        if 'quality_score' in summary:
            print(f"质量评分: {summary['quality_score']:.1f}")
        
        if 'recommendations' in report:
            print("\n改进建议:")
            for rec in report['recommendations']:
                print(f"  {rec}")
        
        print("\n" + "="*70)

def main():
    parser = argparse.ArgumentParser(description='云枢系统质量报告生成')
    parser.add_argument('--type', type=str, default='daily',
                        choices=['daily', 'weekly', 'monthly', 'release'],
                        help='报告类型')
    parser.add_argument('--version', type=str, default='',
                        help='发布版本号 (release类型必需)')
    parser.add_argument('--output', type=str, default='test_reports/quality_report.json',
                        help='输出报告路径')
    
    args = parser.parse_args()

    try:
        report_type = ReportType(args.type)
        generator = QualityReportGenerator(report_type)
        
        report = generator.generate(args.version)
        generator.print_summary()
        
        output_path = Path(args.output)
        generator.save_report(output_path)
        
        print(f"\n📊 报告已保存到: {output_path}")
        print(f"📝 Markdown报告已保存到: {output_path.with_suffix('.md')}")
        
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ 生成质量报告失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()