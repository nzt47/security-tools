"""
云枢智能体 - 发布检查清单自动化验证工具
确保发布前所有检查项都已完成
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class CheckStatus(str, Enum):
    """检查状态"""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    PENDING = "pending"
    WARNING = "warning"


@dataclass
class CheckItem:
    """检查项"""
    id: str
    name: str
    category: str
    description: str
    severity: str = "medium"  # critical, high, medium, low
    status: CheckStatus = CheckStatus.PENDING
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


class ReleaseChecklist:
    """发布检查清单"""

    def __init__(self, base_dir: str = "."):
        self.base_dir = base_dir
        self.checks: List[CheckItem] = []
        self._init_checks()

    def _init_checks(self):
        """初始化检查项"""
        self.checks = [
            # 代码质量
            CheckItem(
                id="code_quality_lint",
                name="代码风格检查",
                category="code_quality",
                description="运行 linter 检查代码风格",
                severity="medium",
            ),
            CheckItem(
                id="code_quality_type",
                name="类型检查",
                category="code_quality",
                description="运行类型检查确保类型安全",
                severity="medium",
            ),
            CheckItem(
                id="code_security",
                name="安全扫描",
                category="security",
                description="运行安全扫描检查漏洞",
                severity="critical",
            ),
            # 测试
            CheckItem(
                id="test_unit",
                name="单元测试",
                category="testing",
                description="单元测试全部通过",
                severity="critical",
            ),
            CheckItem(
                id="test_integration",
                name="集成测试",
                category="testing",
                description="集成测试全部通过",
                severity="high",
            ),
            CheckItem(
                id="test_coverage",
                name="测试覆盖率",
                category="testing",
                description="测试覆盖率达到阈值",
                severity="medium",
            ),
            CheckItem(
                id="test_performance",
                name="性能测试",
                category="testing",
                description="性能基准测试无明显退化",
                severity="medium",
            ),
            # 构建
            CheckItem(
                id="build_docker",
                name="Docker 镜像构建",
                category="build",
                description="Docker 镜像构建成功",
                severity="critical",
            ),
            CheckItem(
                id="build_version",
                name="版本号验证",
                category="build",
                description="版本号符合 SemVer 规范",
                severity="high",
            ),
            # 配置
            CheckItem(
                id="config_env",
                name="环境配置",
                category="configuration",
                description="生产环境配置已更新",
                severity="high",
            ),
            CheckItem(
                id="config_secrets",
                name="敏感配置",
                category="configuration",
                description="敏感配置未硬编码",
                severity="critical",
            ),
            # 文档
            CheckItem(
                id="doc_changelog",
                name="CHANGELOG 更新",
                category="documentation",
                description="CHANGELOG 已更新",
                severity="medium",
            ),
            CheckItem(
                id="doc_upgrade",
                name="升级指南",
                category="documentation",
                description="升级指南已准备",
                severity="low",
            ),
            # 发布准备
            CheckItem(
                id="release_rollback",
                name="回滚计划",
                category="release",
                description="回滚计划已准备",
                severity="high",
            ),
            CheckItem(
                id="release_monitoring",
                name="监控告警",
                category="release",
                description="监控告警已配置",
                severity="high",
            ),
            CheckItem(
                id="release_announcement",
                name="发布公告",
                category="release",
                description="发布公告已准备",
                severity="low",
            ),
        ]

    def run_all_checks(self) -> Tuple[bool, List[CheckItem]]:
        """运行所有检查"""
        all_passed = True

        for check in self.checks:
            try:
                self._run_check(check)
                if check.status == CheckStatus.FAILED and check.severity in ["critical", "high"]:
                    all_passed = False
            except Exception as e:
                check.status = CheckStatus.FAILED
                check.message = f"检查执行异常: {str(e)}"
                if check.severity in ["critical", "high"]:
                    all_passed = False

        return all_passed, self.checks

    def _run_check(self, check: CheckItem):
        """运行单个检查"""
        check_funcs = {
            "code_quality_lint": self._check_lint,
            "code_quality_type": self._check_type,
            "code_security": self._check_security,
            "test_unit": self._check_unit_tests,
            "test_integration": self._check_integration_tests,
            "test_coverage": self._check_coverage,
            "test_performance": self._check_performance,
            "build_docker": self._check_docker_build,
            "build_version": self._check_version,
            "config_env": self._check_env_config,
            "config_secrets": self._check_secrets,
            "doc_changelog": self._check_changelog,
            "doc_upgrade": self._check_upgrade_guide,
            "release_rollback": self._check_rollback_plan,
            "release_monitoring": self._check_monitoring,
            "release_announcement": self._check_announcement,
        }

        func = check_funcs.get(check.id)
        if func:
            func(check)
        else:
            check.status = CheckStatus.SKIPPED
            check.message = "暂无自动检查"

    def _check_lint(self, check: CheckItem):
        """代码风格检查"""
        # 检查是否有 lint 配置
        if os.path.exists(os.path.join(self.base_dir, "pyproject.toml")):
            check.status = CheckStatus.WARNING
            check.message = "需手动运行: ruff check ."
            check.details = {"tool": "ruff"}
        else:
            check.status = CheckStatus.SKIPPED
            check.message = "未配置 lint 工具"

    def _check_type(self, check: CheckItem):
        """类型检查"""
        if os.path.exists(os.path.join(self.base_dir, "pyproject.toml")):
            check.status = CheckStatus.WARNING
            check.message = "需手动运行: mypy agent/"
            check.details = {"tool": "mypy"}
        else:
            check.status = CheckStatus.SKIPPED

    def _check_security(self, check: CheckItem):
        """安全扫描"""
        # 检查是否有安全工具配置
        check.status = CheckStatus.WARNING
        check.message = "需运行安全扫描: bandit -r agent/"
        check.details = {"tools": ["bandit", "safety"]}

    def _check_unit_tests(self, check: CheckItem):
        """单元测试检查"""
        test_dir = os.path.join(self.base_dir, "tests", "unit")
        if os.path.exists(test_dir):
            test_files = [f for f in os.listdir(test_dir) if f.startswith("test_")]
            check.status = CheckStatus.WARNING
            check.message = f"发现 {len(test_files)} 个测试文件，需运行: pytest tests/unit/"
            check.details = {"test_files": len(test_files)}
        else:
            check.status = CheckStatus.FAILED
            check.message = "未找到单元测试目录"

    def _check_integration_tests(self, check: CheckItem):
        """集成测试检查"""
        test_dir = os.path.join(self.base_dir, "tests")
        if os.path.exists(test_dir):
            check.status = CheckStatus.WARNING
            check.message = "需运行集成测试: pytest tests/ -m integration"
        else:
            check.status = CheckStatus.SKIPPED
            check.message = "未配置集成测试"

    def _check_coverage(self, check: CheckItem):
        """测试覆盖率检查"""
        # 检查覆盖率配置
        if os.path.exists(os.path.join(self.base_dir, ".coveragerc")) or \
           os.path.exists(os.path.join(self.base_dir, "pyproject.toml")):
            check.status = CheckStatus.WARNING
            check.message = "需检查测试覆盖率是否达标"
            check.details = {"threshold": "40%"}
        else:
            check.status = CheckStatus.SKIPPED

    def _check_performance(self, check: CheckItem):
        """性能测试检查"""
        perf_dir = os.path.join(self.base_dir, "tests", "performance")
        if os.path.exists(perf_dir):
            check.status = CheckStatus.WARNING
            check.message = "需运行性能基准测试"
        else:
            check.status = CheckStatus.SKIPPED
            check.message = "未配置性能测试"

    def _check_docker_build(self, check: CheckItem):
        """Docker 构建检查"""
        dockerfile = os.path.join(self.base_dir, "Dockerfile")
        if os.path.exists(dockerfile):
            check.status = CheckStatus.WARNING
            check.message = "Dockerfile 存在，需验证构建成功"
            check.details = {"dockerfile": "Dockerfile"}
        else:
            check.status = CheckStatus.FAILED
            check.message = "未找到 Dockerfile"

    def _check_version(self, check: CheckItem):
        """版本号检查"""
        version_file = os.path.join(self.base_dir, "VERSION")
        pyproject_file = os.path.join(self.base_dir, "pyproject.toml")

        version_str = None
        if os.path.exists(version_file):
            with open(version_file, "r") as f:
                version_str = f.read().strip()
        elif os.path.exists(pyproject_file):
            import re
            with open(pyproject_file, "r") as f:
                content = f.read()
                match = re.search(r'version\s*=\s*"([^"]+)"', content)
                if match:
                    version_str = match.group(1)

        if version_str:
            import re
            semver_regex = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
            if re.match(semver_regex, version_str):
                check.status = CheckStatus.PASSED
                check.message = f"版本号有效: {version_str}"
                check.details = {"version": version_str}
            else:
                check.status = CheckStatus.FAILED
                check.message = f"版本号格式无效: {version_str}"
        else:
            check.status = CheckStatus.WARNING
            check.message = "未找到版本号配置"

    def _check_env_config(self, check: CheckItem):
        """环境配置检查"""
        env_example = os.path.join(self.base_dir, ".env.example")
        if os.path.exists(env_example):
            check.status = CheckStatus.WARNING
            check.message = ".env.example 存在，请确认生产环境配置已更新"
        else:
            check.status = CheckStatus.WARNING
            check.message = "未找到 .env.example"

    def _check_secrets(self, check: CheckItem):
        """敏感配置检查"""
        # 简单检查：确保 .env 在 .gitignore 中
        gitignore = os.path.join(self.base_dir, ".gitignore")
        if os.path.exists(gitignore):
            with open(gitignore, "r") as f:
                content = f.read()
                if ".env" in content:
                    check.status = CheckStatus.PASSED
                    check.message = ".env 已在 .gitignore 中"
                    return

        check.status = CheckStatus.WARNING
        check.message = "请确认敏感配置未提交到代码库"

    def _check_changelog(self, check: CheckItem):
        """CHANGELOG 检查"""
        changelog = os.path.join(self.base_dir, "CHANGELOG.md")
        if os.path.exists(changelog):
            check.status = CheckStatus.WARNING
            check.message = "CHANGELOG 存在，请确认已更新"
            check.details = {"file": "CHANGELOG.md"}
        else:
            check.status = CheckStatus.FAILED
            check.message = "未找到 CHANGELOG.md"

    def _check_upgrade_guide(self, check: CheckItem):
        """升级指南检查"""
        docs_dir = os.path.join(self.base_dir, "docs")
        if os.path.exists(docs_dir):
            check.status = CheckStatus.WARNING
            check.message = "请确认升级指南已准备"
        else:
            check.status = CheckStatus.SKIPPED

    def _check_rollback_plan(self, check: CheckItem):
        """回滚计划检查"""
        rollback_script = os.path.join(self.base_dir, "scripts", "rollback.ps1")
        if os.path.exists(rollback_script):
            check.status = CheckStatus.PASSED
            check.message = "回滚脚本存在"
            check.details = {"script": "scripts/rollback.ps1"}
        else:
            check.status = CheckStatus.WARNING
            check.message = "请确认回滚计划已准备"

    def _check_monitoring(self, check: CheckItem):
        """监控告警检查"""
        prometheus_config = os.path.join(self.base_dir, "monitoring", "prometheus.yml")
        alerts_config = os.path.join(self.base_dir, "monitoring", "alerts.yml")

        if os.path.exists(prometheus_config) and os.path.exists(alerts_config):
            check.status = CheckStatus.PASSED
            check.message = "监控告警配置已就绪"
            check.details = {
                "prometheus": "monitoring/prometheus.yml",
                "alerts": "monitoring/alerts.yml",
            }
        else:
            check.status = CheckStatus.WARNING
            check.message = "请确认监控告警已配置"

    def _check_announcement(self, check: CheckItem):
        """发布公告检查"""
        check.status = CheckStatus.SKIPPED
        check.message = "发布公告需手动准备"

    def generate_report(self, output_file: Optional[str] = None) -> str:
        """生成检查报告"""
        all_passed, checks = self.run_all_checks()

        # 统计
        stats = {
            "passed": sum(1 for c in checks if c.status == CheckStatus.PASSED),
            "failed": sum(1 for c in checks if c.status == CheckStatus.FAILED),
            "warning": sum(1 for c in checks if c.status == CheckStatus.WARNING),
            "skipped": sum(1 for c in checks if c.status == CheckStatus.SKIPPED),
            "pending": sum(1 for c in checks if c.status == CheckStatus.PENDING),
        }

        lines = []
        lines.append("# 云枢发布检查清单报告")
        lines.append("")
        lines.append(f"**检查时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**总体结果**: {'✅ 通过' if all_passed else '❌ 未通过'}")
        lines.append("")

        # 统计摘要
        lines.append("## 检查统计")
        lines.append("")
        lines.append("| 状态 | 数量 |")
        lines.append("|------|------|")
        lines.append(f"| ✅ 通过 | {stats['passed']} |")
        lines.append(f"| ❌ 失败 | {stats['failed']} |")
        lines.append(f"| ⚠️  警告 | {stats['warning']} |")
        lines.append(f"| ⏭️  跳过 | {stats['skipped']} |")
        lines.append("")

        # 按分类分组
        categories = {}
        for check in checks:
            if check.category not in categories:
                categories[check.category] = []
            categories[check.category].append(check)

        category_names = {
            "code_quality": "代码质量",
            "security": "安全",
            "testing": "测试",
            "build": "构建",
            "configuration": "配置",
            "documentation": "文档",
            "release": "发布准备",
        }

        for cat_key, cat_name in category_names.items():
            if cat_key not in categories:
                continue
            lines.append(f"## {cat_name}")
            lines.append("")

            for check in categories[cat_key]:
                status_icon = {
                    CheckStatus.PASSED: "✅",
                    CheckStatus.FAILED: "❌",
                    CheckStatus.WARNING: "⚠️",
                    CheckStatus.SKIPPED: "⏭️",
                    CheckStatus.PENDING: "⏳",
                }.get(check.status, "❓")

                severity_label = {
                    "critical": "【严重】",
                    "high": "【高】",
                    "medium": "【中】",
                    "low": "【低】",
                }.get(check.severity, "")

                lines.append(f"### {status_icon} {check.name} {severity_label}")
                lines.append("")
                lines.append(f"- **描述**: {check.description}")
                lines.append(f"- **状态**: {check.status.value}")
                if check.message:
                    lines.append(f"- **说明**: {check.message}")
                lines.append("")

        # 严重级别失败项汇总
        critical_failures = [c for c in checks if c.status == CheckStatus.FAILED and c.severity in ["critical", "high"]]
        if critical_failures:
            lines.append("## ❗ 严重问题汇总")
            lines.append("")
            for check in critical_failures:
                lines.append(f"- **{check.name}**: {check.message}")
            lines.append("")

        report = "\n".join(lines)

        if output_file:
            os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(report)

        return report


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="云枢发布检查清单工具")
    parser.add_argument("--output", "-o", help="输出报告文件")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    parser.add_argument("--strict", action="store_true", help="严格模式，警告也算失败")

    args = parser.parse_args()

    checklist = ReleaseChecklist()
    all_passed, checks = checklist.run_all_checks()

    if args.json:
        result = {
            "timestamp": datetime.now().isoformat(),
            "passed": all_passed,
            "checks": [
                {
                    "id": c.id,
                    "name": c.name,
                    "category": c.category,
                    "severity": c.severity,
                    "status": c.status.value,
                    "message": c.message,
                }
                for c in checks
            ],
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        report = checklist.generate_report(args.output)
        print(report)

    if args.strict:
        has_warnings = any(c.status == CheckStatus.WARNING for c in checks)
        if not all_passed or has_warnings:
            sys.exit(1)
    elif not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
