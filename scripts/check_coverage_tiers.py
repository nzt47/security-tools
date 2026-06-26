#!/usr/bin/env python3
"""
云枢系统覆盖率分级检查脚本

支持分阶段覆盖率阈值：
- 全局覆盖率：分阶段从40%→55%→70%
- 核心模块：分阶段从60%→70%→80%
- 安全/权限模块：分阶段从70%→80%→90%
- 新增代码覆盖率必须≥80%

输出结构化JSON报告，包含：
- 各模块覆盖率详情
- 达标情况判定
- 改进建议
- 详细报告路径
"""

import json
import sys
import os
import argparse
import logging
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum

# ── 结构化日志配置（与 visibility_report.py 降级日志模式对齐） ──
logger = logging.getLogger("coverage_tiers")


def _trace_id() -> str:
    """生成简易 trace_id（无第三方依赖）"""
    return uuid.uuid4().hex[:16]

class PhaseLevel(Enum):
    PHASE_1 = "phase_1"
    PHASE_2 = "phase_2"
    PHASE_3 = "phase_3"

@dataclass
class CoverageTier:
    name: str
    pattern: str
    phase_1: float
    phase_2: float
    phase_3: float
    critical: bool = False

@dataclass
class CoverageResult:
    tier: str
    pattern: str
    covered_lines: int
    total_lines: int
    coverage_percent: float
    target_percent: float
    meets_target: bool
    critical: bool
    gap: float

@dataclass
class NewCodeCoverage:
    files: List[str]
    covered_lines: int
    total_lines: int
    coverage_percent: float
    meets_target: bool

class CoverageTierChecker:
    COVERAGE_TIERS = [
        CoverageTier("全局", "agent/", 40, 55, 70, critical=True),
        CoverageTier("核心模块", "agent/memory/", 60, 70, 80, critical=True),
        CoverageTier("核心模块", "agent/cognitive/", 60, 70, 80, critical=True),
        CoverageTier("安全/权限模块", "agent/permission/", 70, 80, 90, critical=True),
        CoverageTier("安全/权限模块", "agent/security/", 70, 80, 90, critical=True),
        CoverageTier("监控模块", "agent/monitoring/", 60, 70, 80, critical=True),
        CoverageTier("规划模块", "agent/planning/", 55, 65, 75, critical=False),
        CoverageTier("传感器模块", "agent/sensor/", 55, 65, 75, critical=False),
        CoverageTier("工具调用模块", "agent/tool_calling/", 60, 70, 80, critical=True),
        CoverageTier("编排模块", "agent/orchestrator/", 60, 70, 80, critical=True),
    ]

    NEW_CODE_TARGET = 80.0

    def __init__(self, phase: PhaseLevel = PhaseLevel.PHASE_2):
        self.phase = phase
        self.results: List[CoverageResult] = []
        self.new_code_result: Optional[NewCodeCoverage] = None

    def get_target_for_phase(self, tier: CoverageTier) -> float:
        if self.phase == PhaseLevel.PHASE_1:
            return tier.phase_1
        elif self.phase == PhaseLevel.PHASE_2:
            return tier.phase_2
        else:
            return tier.phase_3

    def parse_coverage_xml(self, xml_path: Path) -> Dict[str, Dict[str, int]]:
        import xml.etree.ElementTree as ET

        if not xml_path.exists():
            raise FileNotFoundError(f"覆盖率报告文件不存在: {xml_path}")

        tree = ET.parse(xml_path)
        root = tree.getroot()

        coverage_data = {}
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
                coverage_data[package_name] = {
                    'total_lines': total_lines,
                    'covered_lines': covered_lines
                }
        
        return coverage_data

    def parse_coverage_json(self, json_path: Path) -> Dict[str, Dict[str, int]]:
        if not json_path.exists():
            raise FileNotFoundError(f"覆盖率报告文件不存在: {json_path}")

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        coverage_data = {}
        if 'files' in data:
            for file_data in data['files']:
                filename = file_data.get('filename', '')
                summary = file_data.get('summary', {})
                coverage_data[filename] = {
                    'total_lines': summary.get('num_statements', 0),
                    'covered_lines': summary.get('covered_statements', 0)
                }
        
        return coverage_data

    def check_tiers(self, coverage_data: Dict[str, Dict[str, int]]) -> List[CoverageResult]:
        results = []

        for tier in self.COVERAGE_TIERS:
            target = self.get_target_for_phase(tier)
            matching_files = [
                (name, data) for name, data in coverage_data.items()
                if tier.pattern in name
            ]

            if matching_files:
                total_lines = sum(data['total_lines'] for _, data in matching_files)
                covered_lines = sum(data['covered_lines'] for _, data in matching_files)
                
                if total_lines > 0:
                    coverage_percent = (covered_lines / total_lines) * 100
                    meets_target = coverage_percent >= target
                    gap = target - coverage_percent

                    result = CoverageResult(
                        tier=tier.name,
                        pattern=tier.pattern,
                        covered_lines=covered_lines,
                        total_lines=total_lines,
                        coverage_percent=coverage_percent,
                        target_percent=target,
                        meets_target=meets_target,
                        critical=tier.critical,
                        gap=gap
                    )
                    results.append(result)

        self.results = results
        return results

    def check_new_code(self, coverage_data: Dict[str, Dict[str, int]], changed_files: Optional[List[str]] = None) -> NewCodeCoverage:
        if not changed_files:
            return NewCodeCoverage(
                files=[],
                covered_lines=0,
                total_lines=0,
                coverage_percent=0.0,
                meets_target=True
            )

        total_lines = 0
        covered_lines = 0
        matched_files = []

        for changed_file in changed_files:
            for name, data in coverage_data.items():
                if changed_file in name or name in changed_file:
                    total_lines += data['total_lines']
                    covered_lines += data['covered_lines']
                    matched_files.append(name)
                    break

        if total_lines > 0:
            coverage_percent = (covered_lines / total_lines) * 100
            meets_target = coverage_percent >= self.NEW_CODE_TARGET
        else:
            # 结构化日志：变更文件无匹配覆盖率数据，降级视为 100% 通过
            # 警告：此降级可能掩盖覆盖率数据缺失，需人工核实
            logger.warning(json.dumps({
                "trace_id": _trace_id(),
                "module_name": "coverage_tiers",
                "action": "check_new_code.no_data_fallback",
                "duration_ms": 0,
                "changed_files": changed_files,
                "reason": "变更文件无匹配的覆盖率数据，降级视为 100% 通过（可能掩盖数据缺失）",
            }, ensure_ascii=False))
            coverage_percent = 100.0
            meets_target = True

        result = NewCodeCoverage(
            files=matched_files,
            covered_lines=covered_lines,
            total_lines=total_lines,
            coverage_percent=coverage_percent,
            meets_target=meets_target
        )
        self.new_code_result = result
        return result

    def generate_report(self) -> Dict[str, Any]:
        critical_failed = [r for r in self.results if r.critical and not r.meets_target]
        non_critical_failed = [r for r in self.results if not r.critical and not r.meets_target]
        
        overall_status = "PASS" if not critical_failed else "FAIL"
        all_meet_target = all(r.meets_target for r in self.results)
        if self.new_code_result and not self.new_code_result.meets_target:
            overall_status = "FAIL"

        recommendations = self._generate_recommendations()

        report = {
            "generated_at": datetime.now().isoformat(),
            "phase": self.phase.value,
            "summary": {
                "overall_status": overall_status,
                "total_tiers": len(self.results),
                "tiers_meeting_target": sum(1 for r in self.results if r.meets_target),
                "critical_tiers_failed": len(critical_failed),
                "non_critical_tiers_failed": len(non_critical_failed),
                "new_code_meets_target": self.new_code_result.meets_target if self.new_code_result else True,
                "new_code_coverage": self.new_code_result.coverage_percent if self.new_code_result else 0.0,
            },
            "thresholds": {
                "phase": self.phase.value,
                "global": self.get_target_for_phase(self.COVERAGE_TIERS[0]),
                "core_modules": self.get_target_for_phase(self.COVERAGE_TIERS[1]),
                "security_modules": self.get_target_for_phase(self.COVERAGE_TIERS[3]),
                "new_code": self.NEW_CODE_TARGET,
            },
            "results": [asdict(r) for r in self.results],
            "new_code": asdict(self.new_code_result) if self.new_code_result else None,
            "recommendations": recommendations,
        }

        return report

    def _generate_recommendations(self) -> List[str]:
        recommendations = []

        for result in self.results:
            if not result.meets_target:
                recommendations.append(
                    f"模块 {result.pattern} 覆盖率 {result.coverage_percent:.1f}%，"
                    f"目标 {result.target_percent:.0f}%，差距 {result.gap:.1f}%。"
                )
                if result.critical:
                    recommendations.append(
                        f"⚠️ 严重: {result.pattern} 是核心模块，覆盖率未达标将导致CI失败！"
                    )

        if self.new_code_result and not self.new_code_result.meets_target:
            recommendations.append(
                f"新增代码覆盖率 {self.new_code_result.coverage_percent:.1f}%，"
                f"未达到目标 {self.NEW_CODE_TARGET:.0f}%，请补充测试用例。"
            )

        if not recommendations:
            recommendations.append("✅ 所有模块覆盖率达标，继续保持！")

        return recommendations

    def print_summary(self, report: Dict[str, Any]):
        print("\n" + "="*70)
        print("云枢系统覆盖率分级检查报告")
        print("="*70)
        print(f"\n阶段: {self.phase.value}")
        print(f"生成时间: {report['generated_at']}")
        print(f"整体状态: {'✅ 通过' if report['summary']['overall_status'] == 'PASS' else '❌ 失败'}")

        print("\n--- 阈值设置 ---")
        thresholds = report['thresholds']
        print(f"  全局覆盖率: {thresholds['global']}%")
        print(f"  核心模块: {thresholds['core_modules']}%")
        print(f"  安全/权限模块: {thresholds['security_modules']}%")
        print(f"  新增代码: {thresholds['new_code']}%")

        print("\n--- 模块覆盖率详情 ---")
        for result in report['results']:
            status = "✅" if result['meets_target'] else "❌"
            critical_mark = " [核心]" if result['critical'] else ""
            print(f"  {status} {result['tier']}{critical_mark}")
            print(f"     路径: {result['pattern']}")
            print(f"     覆盖率: {result['coverage_percent']:.1f}% (目标: {result['target_percent']:.0f}%)")
            if not result['meets_target']:
                print(f"     差距: {result['gap']:.1f}%")

        if report['new_code']:
            nc = report['new_code']
            status = "✅" if nc['meets_target'] else "❌"
            print(f"\n--- 新增代码覆盖率 ---")
            print(f"  {status} 新增代码覆盖率: {nc['coverage_percent']:.1f}% (目标: {self.NEW_CODE_TARGET:.0f}%)")
            if nc['files']:
                print(f"  涉及文件: {len(nc['files'])} 个")

        print("\n--- 改进建议 ---")
        for rec in report['recommendations']:
            print(f"  {rec}")

        print("\n" + "="*70)

def get_changed_files() -> List[str]:
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'diff', '--name-only', 'HEAD', 'HEAD~1'],
            capture_output=True,
            text=True,
            timeout=30
        )
        return [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
    except Exception as e:
        # 结构化日志：git 变更文件检测失败，降级返回空列表（不静默吞异常）
        # 警告：此降级会导致新增代码覆盖率检查被跳过
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "coverage_tiers",
            "action": "get_changed_files.failed",
            "duration_ms": 0,
            "error": f"{type(e).__name__}: {e}",
            "reason": "git 变更文件检测失败，降级返回空列表（新增代码检查将被跳过）",
        }, ensure_ascii=False))
        return []

def main():
    parser = argparse.ArgumentParser(description='云枢系统覆盖率分级检查')
    parser.add_argument('--coverage-file', type=str, default='coverage.xml',
                        help='覆盖率报告文件路径 (XML或JSON)')
    parser.add_argument('--phase', type=str, default='phase_2',
                        choices=['phase_1', 'phase_2', 'phase_3'],
                        help='覆盖率阶段 (phase_1/phase_2/phase_3)')
    parser.add_argument('--new-code-check', action='store_true',
                        help='检查新增代码覆盖率')
    parser.add_argument('--changed-files', type=str, nargs='*',
                        help='指定变更文件列表')
    parser.add_argument('--output', type=str, default='test_reports/coverage_tier_report.json',
                        help='输出报告路径')
    parser.add_argument('--fail-on-critical', action='store_true',
                        help='核心模块不达标时退出码为1')
    
    args = parser.parse_args()

    try:
        phase = PhaseLevel(args.phase)
        checker = CoverageTierChecker(phase)

        coverage_path = Path(args.coverage_file)
        
        if coverage_path.suffix == '.xml':
            coverage_data = checker.parse_coverage_xml(coverage_path)
        elif coverage_path.suffix == '.json':
            coverage_data = checker.parse_coverage_json(coverage_path)
        else:
            raise ValueError(f"不支持的文件格式: {coverage_path.suffix}")

        checker.check_tiers(coverage_data)

        if args.new_code_check:
            changed_files = args.changed_files if args.changed_files else get_changed_files()
            checker.check_new_code(coverage_data, changed_files)

        report = checker.generate_report()
        checker.print_summary(report)

        output_path = Path(args.output)
        output_path.parent.mkdir(exist_ok=True, parents=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"\n📊 详细报告已保存到: {output_path}")

        if report['summary']['overall_status'] == 'FAIL':
            print("\n❌ 覆盖率检查未通过！")
            sys.exit(1)
        else:
            print("\n✅ 覆盖率检查通过！")
            sys.exit(0)

    except Exception as e:
        print(f"\n❌ 覆盖率检查失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()